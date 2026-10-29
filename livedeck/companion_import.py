"""Import d'un export Bitfocus Companion (.companionconfig) vers LiveDeck."""
from __future__ import annotations

import json
import re

from . import feedback
from .actions import new_action
from .model import default_config, key, new_button, new_page, normalize_config
from .vmix import norm

SURFACE_SIZES = {"xl": (8, 4), "mini": (3, 2), "plus": (4, 2), "neo": (4, 2), "pedal": (3, 1)}


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
    low = (model or "").lower()
    for k, s in SURFACE_SIZES.items():
        if k in low:
            return s
    return (5, 3)


class _Converter:
    def __init__(self, data: dict):
        self.types = {cid: c.get("instance_type", "") for cid, c in (data.get("instances") or {}).items()}
        self.types["internal"] = "internal"
        self.notes: list[str] = []
        self.where = ""

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
        mix = int(o.get("mix") or 0) + 1 if not isinstance(o.get("mix"), list) else 1
        if d == "programCut":
            return new_action("vmix.program", input=_input(o.get("input")), mix=mix)
        if d == "previewInput":
            return new_action("vmix.preview", input=_input(o.get("input")), mix=mix)
        if d == "transition":
            return new_action("vmix.transition", effect=o.get("functionID", "Cut"), mix=mix)
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
            return new_action("vmix.function", function=name, params=params.replace(" ", "&"))
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
                                       _color(o.get("fg")), input=_input(o.get("input")),
                                       mix=int(o.get("mix") or 0) + 1))
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


def import_companion(path) -> tuple[dict, list[str]]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    conv = _Converter(data)
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
            extra = new_page(f"{p['name']} (suite)", cols, rows)
            for i, b in enumerate(overflow):
                extra["buttons"][key(*divmod(i, cols))] = b
            cfg["pages"].append(extra)
            report.append(f"« {p['name']} » : {len(overflow)} bouton(s) hors de la grille {cols}×{rows} "
                          f"du deck déplacés dans « {extra['name']} ».")
    vmix_conf = next((c.get("config") for c in (data.get("instances") or {}).values()
                      if "vmix" in c.get("instance_type", "")), None)
    if vmix_conf:
        cfg["vmix"] = {"host": vmix_conf.get("host", "127.0.0.1"), "port": int(vmix_conf.get("tcpPort", 8099))}
    from .integrations import migrate
    report[0:0] = migrate(cfg)
    report.insert(0, f"{total} boutons importés sur {len(cfg['pages'])} pages, {len(cfg['decks'])} Stream Deck(s) associés.")
    report.extend(conv.notes)
    return normalize_config(cfg), report
