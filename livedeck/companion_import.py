"""Import d'un export Bitfocus Companion (.companionconfig) vers LiveDeck."""
from __future__ import annotations

import copy
import json
import re

from . import feedback
from .actions import new_action
from .model import deck_size, default_config, key, new_button, new_page, normalize_config
from .vmix import norm


def _color(v, default="#000000") -> str:
    try:
        return "#%06x" % (int(v) & 0xFFFFFF)
    except (TypeError, ValueError):
        return default


def _input(v) -> str:
    """$(vmix:input_cameraregie_number) → « cameraregie » (retrouvé ensuite par nom dans vMix)."""
    v = str(v or "").strip()
    m = re.fullmatch(r"\$\(vmix:input_(.+?)_(number|name|guid|key)\)", v)
    return m.group(1) if m else v


def _size(model: str) -> tuple[int, int]:
    return deck_size(model)  # une seule table, dans model.py


def _mix_of(o: dict) -> int:
    """Mix Companion (0 = Master, -1 = « mix sélectionné ») -> mix LiveDeck (1 = Master)."""
    try:
        return max(1, int(o.get("mix") or 0) + 1)
    except (TypeError, ValueError):
        return 1


# Variables Companion (module vMix / interne) -> jetons LiveDeck (voir livedeck/vars.py)
_INPUT_PROPS = {"name": "input_title", "title": "input_title", "short_title": "input_title",
                "shorttitle": "input_title", "number": "input_number", "state": "input_state",
                "remaining": "remaining", "duration": "duration", "position": "position", "volume": "volume"}


def _var(body: str) -> str | None:
    ns, _, var = body.partition(":")
    var = var.strip()
    if ns == "vmix":
        m = re.fullmatch(r"input_(.+?)_(%s)(?:_[a-z0-9_.]+)?" % "|".join(_INPUT_PROPS), var)
        if m:
            return f"$(vmix:{_INPUT_PROPS[m.group(2)]}:{m.group(1)})"
        m = re.fullmatch(r"(?:mix_(\d+)_)?(program|preview)(?:_(name|title|short_title|number))?", var)
        if m:
            what = "number" if (m.group(3) or "number") == "number" else "title"
            mix = f":{m.group(1)}" if m.group(1) and m.group(1) != "1" else ""
            return f"$(vmix:{m.group(2)}_{what}{mix})"
        m = re.fullmatch(r"(recording|streaming|external|multicorder)_(?:duration|hhmmss|hh_mm_ss)", var)
        if m:
            return f"$(vmix:{m.group(1)}_duration)"
    if ns == "internal":
        if var in ("time_hm", "time_h_m"):
            return "$(time:hm)"
        if var.startswith("time"):
            return "$(time)"
        if var.startswith("date"):
            return "$(date)"
    return None


class _Converter:
    def __init__(self, data: dict, preview_then: str = "none"):
        self.types = {cid: c.get("instance_type", "") for cid, c in (data.get("instances") or {}).items()}
        self.types["internal"] = "internal"
        self.notes: list[str] = []
        self.where = ""
        self.preview_then = preview_then or "none"
        self.counts = {"preview": 0, "selected_mix": 0, "vars": 0}

    def label(self, text: str) -> str:
        """Libellé : les variables Companion connues deviennent des textes dynamiques LiveDeck."""
        def sub(m):
            new = _var(m.group(1))
            if new is None:
                self.note(f"variable « {m.group(0)} » non reprise (affichée telle quelle)")
                return m.group(0)
            self.counts["vars"] += 1
            return new
        return re.sub(r"\$\(([A-Za-z0-9_-]+:[^)]*)\)", sub, text)

    def note(self, msg: str) -> None:
        self.notes.append(f"{self.where} : {msg}")

    # -- actions --------------------------------------------------------------
    def actions(self, lst: list, up: list | None = None) -> tuple[list[dict], list[tuple[str, str]]]:
        out, targets, held = [], [], []
        released = {self._key_of(a) for a in (up or []) if a.get("definitionId") == "release"}
        for a in lst or []:
            mod = self.types.get(a.get("connectionId"), "")
            d, o = a.get("definitionId", ""), a.get("options") or {}
            if "vmix" in mod:
                conv = self._vmix(d, o)
                if conv:
                    out.append(conv)
                else:
                    self.note(f"action vMix « {d} » non reprise")
            elif "vicreo" in mod or "hotkey" in mod:
                if d == "press":
                    held.append(self._key_of(a))
                elif d != "release":
                    keys = "+".join([*(o.get("modifiers") or []), str(o.get("key") or o.get("keyPress") or "")])
                    out.append(new_action("hotkey", keys=keys.lower().strip("+")))
            elif "http" in mod:
                if d in ("get", "post", "put", "patch", "delete"):
                    if d == "get" and o.get("jsonResultDataVariable"):
                        continue  # lecture d'état : remplacée par la surveillance réseau
                    out.append(new_action("http", method=d.upper(), url=o.get("url", ""), body=o.get("body", ""),
                                          headers=o.get("header", ""),
                                          content_type=o.get("contenttype", "application/json")))
            elif "ping" in mod:
                if d == "sendCustomPing" and o.get("host"):
                    name = re.sub(r"^(PingTally|Ping)", "", o.get("customVariable") or "") or o["host"]
                    targets.append((name, o["host"]))
            elif mod == "internal":
                if d == "custom_variable_set_value":
                    continue
                if d in ("set_page", "button_pressrelease"):
                    self.note(f"action interne « {d} » à refaire avec « Changer de page »")
                else:
                    self.note(f"action interne « {d} » non reprise")
            else:
                self.note(f"action « {d} » ({mod or 'inconnu'}) non reprise")
        if held:
            out.insert(0, new_action("hotkey", keys="+".join(k.lower() for k in held),
                                     hold=bool(released & set(held))))
        return out, targets

    @staticmethod
    def _key_of(a: dict) -> str:
        o = a.get("options") or {}
        return str(o.get("keyPress") or o.get("keyRelease") or o.get("key") or "")

    def _vmix(self, d: str, o: dict) -> dict | None:
        mix = _mix_of(o) if not isinstance(o.get("mix"), list) else 1
        if str(o.get("mix")) == "-1":
            self.counts["selected_mix"] += 1
        if d == "programCut":
            return new_action("vmix.program", input=_input(o.get("input")), mix=mix)
        if d == "previewInput":
            self.counts["preview"] += 1
            return new_action("vmix.preview", input=_input(o.get("input")), mix=mix, then=self.preview_then)
        if d == "transition":
            effect = o.get("functionID") or "Cut"
            if effect == "FadeToBlack":  # interrupteur vMix, pas une transition
                return new_action("vmix.production", what="fadetoblack")
            return new_action("vmix.transition", effect=effect, mix=mix)
        if d == "overlayFunctions":
            mode = {"OverlayInput": "toggle", "OverlayInputIn": "in", "OverlayInputOut": "out",
                    "OverlayInputOff": "off", "PreviewOverlayInput": "preview"}.get(o.get("type"), "toggle")
            return new_action("vmix.overlay", input=_input(o.get("input")), overlay=int(o.get("overlay") or 1), mode=mode)
        if d == "keyPress":
            return new_action("vmix.function", function="KeyPress", params=f"Value={o.get('value', '')}")
        if d == "command":
            cmd = str(o.get("command", "")).strip()
            m = re.match(r"^([A-Za-z0-9_]+)[\s&?]*(.*)$", cmd)
            if not m:
                return None
            name, params = m.group(1), m.group(2)
            if name.lower() == "cut" and not params:
                return new_action("vmix.transition", effect="Cut")
            # « SetText Input=T Value=Bonjour le monde » : seul l'espace devant « Clé= » sépare deux paramètres
            params = re.sub(r"\s+(?=[A-Za-z][A-Za-z0-9_]*=)", "&", params.strip())
            return new_action("vmix.function", function=name, params=params)
        what = {"startStopRecording": "recording", "startStopStreaming": "streaming",
                "startStopExternal": "external", "startStopMultiCorder": "multicorder"}.get(d)
        if what:
            return new_action("vmix.production", what=what)
        if d in ("audio", "audioOn", "audioOff"):
            return new_action("vmix.audio", input=_input(o.get("input")),
                              mode={"audio": "toggle", "audioOn": "on", "audioOff": "off"}[d])
        return None

    # -- retours --------------------------------------------------------------
    def feedbacks(self, lst: list, has_health: bool, targets_text: str) -> list[dict]:
        out = []
        for f in lst or []:
            mod = self.types.get(f.get("connectionId"), "")
            d, o, style = f.get("definitionId", ""), f.get("options") or {}, f.get("style") or {}
            if "vmix" in mod and d in ("inputLive", "inputPreview"):
                out.append(feedback.fb("program" if d == "inputLive" else "preview", _color(o.get("bg")),
                                       _color(o.get("fg")), input=_input(o.get("input")), mix=_mix_of(o)))
            elif "vmix" in mod and d == "overlayStatus":
                inp, n = _input(o.get("input")), int(o.get("overlay") or 1)
                out.append(feedback.fb("overlay", _color(o.get("bgProgram")), _color(o.get("fg")),
                                       input=inp, overlay=n, where="program"))
                out.append(feedback.fb("overlay", bar=_color(o.get("bgPreview")), input=inp, overlay=n, where="preview"))
            elif d == "bank_pushed":
                out.append(feedback.fb("pressed", _color(style.get("bgcolor")), _color(style.get("color"))))
            elif d == "variable_value" and str(o.get("variable", "")).startswith("custom:"):
                out.append(feedback.fb("toggle", _color(style.get("bgcolor")), _color(style.get("color"))))
            elif d == "variable_value" and has_health:
                state = "ok" if o.get("op") == "eq" else "ko"
                out.append(feedback.fb("health", _color(style.get("bgcolor")), _color(style.get("color")),
                                       targets=targets_text, state=state))
            else:
                self.note(f"retour « {d} » non repris")
        return out

    # -- bouton ---------------------------------------------------------------
    def button(self, ctrl: dict) -> dict | None:
        if ctrl.get("type") != "button":
            return None
        style = ctrl.get("style") or {}
        step = next(iter((ctrl.get("steps") or {}).values()), {}) or {}
        sets = step.get("action_sets") or {}
        down, up = sets.get("down") or [], sets.get("up") or []
        text = re.sub(r"\s+\n", "\n", str(style.get("text") or "").replace("\r\n", "\n")).strip()
        text = self.label(text)
        size = str(style.get("size") or "auto")
        b = new_button(label=text, bg=_color(style.get("bgcolor")), fg=_color(style.get("color"), "#ffffff"),
                       size=int(size) if size.isdigit() and size not in ("14",) else 0)

        cond_if = down[0] if len(down) == 1 and down[0].get("definitionId") == "logic_if" else None
        targets = []
        if cond_if:
            ch = cond_if.get("children") or {}
            cond = (ch.get("condition") or [{}])[0].get("options") or {}
            then, _ = self.actions(ch.get("actions"))
            other, _ = self.actions(ch.get("else_actions"))
            is_on_when_true = str(cond.get("value")) not in ("0", "false", "")
            b["mode"] = "toggle"
            b["actions"], b["actions_off"] = (other, then) if is_on_when_true else (then, other)
        else:
            b["actions"], targets = self.actions(down, up)

        targets_text = "\n".join(f"{n} = {h}" for n, h in targets)
        converted = self.feedbacks(ctrl.get("feedbacks"), bool(targets), targets_text)
        auto_conds = [self._cond_sig(f["cond"]) for f in feedback.auto(b)]
        b["feedbacks"] = [f for f in converted if self._cond_sig(f["cond"]) not in auto_conds]
        if not b["label"] and not b["actions"] and not b["actions_off"]:
            return None
        return b

    @staticmethod
    def _cond_sig(c: dict) -> tuple:
        return (c.get("type"), norm(str(c.get("input", ""))), int(c.get("mix") or 1),
                int(c.get("overlay") or 0), c.get("where", ""))


def import_companion(path, preview_then: str = "none", integrations: bool = False) -> tuple[dict, list[str]]:
    """Convertit un export Companion. Renvoie (configuration, rapport).

    preview_then : « 2e appui » donné aux touches previewInput importées (« none » = preview simple,
    comme dans Companion ; « Cut », « Fade »… = un 2e appui sur l'input déjà en preview l'envoie à l'antenne).
    integrations : convertit aussi les bricolages Intercom (raccourcis maintenus), overlays orchestream (requêtes
    show / hide) et tallys (reconnect + pings) vers py-intercom, orchestream et Tally32. Désactivé par défaut :
    sans ces logiciels, les touches restent telles qu'elles étaient dans Companion."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    conv = _Converter(data, preview_then)
    cfg = default_config()
    cfg["pages"] = []

    surfaces = {}  # id page Companion -> (serial, (cols, rows), luminosité)
    for sid, s in (data.get("surfaces") or {}).items():
        gc = s.get("groupConfig") or {}
        pid = gc.get("startup_page_id") or gc.get("last_page_id")
        serial = sid.split(":", 1)[-1]
        surfaces.setdefault(pid, []).append((serial, _size(s.get("type", "")), (s.get("config") or {}).get("brightness", 80)))

    report, total = [], 0
    for num, page in sorted((data.get("pages") or {}).items(), key=lambda kv: int(kv[0])):
        bound = surfaces.get(page.get("id"), [])
        cols, rows = bound[0][1] if bound else (8, 4)
        p = new_page(page.get("name") or f"Page {num}", cols, rows)
        overflow = []
        for r, row in sorted((page.get("controls") or {}).items(), key=lambda kv: int(kv[0])):
            for c, ctrl in sorted(row.items(), key=lambda kv: int(kv[0])):
                conv.where = f"{p['name']} [{int(r) + 1},{int(c) + 1}]"
                b = conv.button(ctrl)
                if not b:
                    continue
                total += 1
                if int(r) < rows and int(c) < cols:
                    p["buttons"][key(int(r), int(c))] = b
                else:
                    overflow.append(b)
        cfg["pages"].append(p)
        for serial, _, brightness in bound:
            cfg["decks"][serial] = {"page": p["id"], "brightness": brightness}
        if overflow:
            per = max(1, cols * rows)  # une page « (suite) » par grille pleine : rien ne reste hors grille
            for n in range(0, len(overflow), per):
                extra = new_page(f"{p['name']} (suite{' ' + str(n // per + 1) if n else ''})", cols, rows)
                for i, b in enumerate(overflow[n:n + per]):
                    extra["buttons"][key(*divmod(i, cols))] = b
                cfg["pages"].append(extra)
            extra = {"name": f"{p['name']} (suite)" + (" et suivantes" if len(overflow) > per else "")}
            report.append(f"« {p['name']} » : {len(overflow)} bouton(s) hors de la grille {cols}×{rows} "
                          f"du deck déplacés dans « {extra['name']} ».")
    vmix_conf = next((c.get("config") for c in (data.get("instances") or {}).values()
                      if "vmix" in c.get("instance_type", "")), None)
    if vmix_conf:
        cfg["vmix"] = {"host": vmix_conf.get("host", "127.0.0.1"), "port": int(vmix_conf.get("tcpPort", 8099))}
    from .integrations import migrate
    if integrations:
        report[0:0] = migrate(cfg)
    elif migrate(copy.deepcopy(cfg)):  # simple essai : on signale ce qui pourrait être converti
        report.append("Des touches ressemblent à des intégrations py-intercom, orchestream ou Tally32 (raccourcis "
                      "Intercom, overlays web, tallys) : elles sont gardées telles quelles. Réimporte en cochant "
                      "« Convertir vers les intégrations natives » pour les relier directement.")
    report.insert(0, f"{total} boutons importés sur {len(cfg['pages'])} pages, {len(cfg['decks'])} Stream Deck(s) associés.")
    n = conv.counts
    if n["preview"] and conv.preview_then != "none":
        report.append(f"{n['preview']} action(s) Preview : un 2e appui sur l'input déjà en preview l'envoie à "
                      f"l'antenne ({conv.preview_then}).")
    elif n["preview"]:
        report.append(f"{n['preview']} action(s) Preview importées telles quelles : un 2e appui ne fait que "
                      "renvoyer la preview. Pour qu'il passe à l'antenne, règle « 2e appui » dans l'inspecteur "
                      "(ou utilise une touche Take).")
    if n["selected_mix"]:
        report.append(f"{n['selected_mix']} action(s) sur le « mix sélectionné » de Companion reprises sur le Master.")
    if n["vars"]:
        report.append(f"{n['vars']} variable(s) Companion converties en textes dynamiques.")
    report.extend(conv.notes)
    return normalize_config(cfg), report
