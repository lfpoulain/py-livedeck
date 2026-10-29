"""Retours d'état : conditions évaluées en direct et retours automatiques déduits des actions."""
from __future__ import annotations

import re
import urllib.parse

from .actions import F
from .health import JSON_OPS, json_compare, json_path, json_text, parse_targets
from .integrations import bus_label, overlay_label

RED, GREEN, YELLOW, BLUE = "#e5262b", "#22c55e", "#facc15", "#4040ff"
OFF_BG, OFF_FG = "#1a1d24", "#5a6273"  # intégration indisponible : touche grisée
BLACK, WHITE = "#000000", "#ffffff"

FLAGS = [("recording", "Enregistrement en cours"), ("streaming", "Stream en cours"),
         ("external", "Sortie externe active"), ("multicorder", "MultiCorder actif"),
         ("fadetoblack", "Fondu au noir actif"), ("playlist", "Playlist en cours")]

CONDITIONS: dict[str, dict] = {
    "program": {"label": "Input à l'antenne (Program)",
                "fields": [F("input", "Input", "input", ""), F("mix", "Mix", "mix", 1)]},
    "preview": {"label": "Input en Preview",
                "fields": [F("input", "Input", "input", ""), F("mix", "Mix", "mix", 1)]},
    "overlay": {"label": "Overlay actif",
                "fields": [F("input", "Input (vide = n'importe lequel)", "input", ""),
                           F("overlay", "Overlay", "overlay", 1),
                           F("where", "Où", "choice", "program", [("program", "À l'antenne"), ("preview", "En preview")])]},
    "audio": {"label": "Son d'un input",
              "fields": [F("input", "Input", "input", ""),
                         F("state", "Quand", "choice", "muted", [("muted", "Coupé"), ("live", "Actif")])]},
    "intercom": {"label": "Intercom (py-intercom)",
                 "fields": [F("bus", "Bus", "intercom_bus", "regie"),
                            F("state", "Quand", "choice", "active",
                              [("active", "Micro ouvert"), ("offline", "Indisponible (hors ligne, désarmé, bus absent)"),
                               ("always_on", "Bus toujours ouvert (mode du client)")])]},
    "orchestream": {"label": "Overlay orchestream",
                    "fields": [F("overlay", "Overlay", "orch_overlay", "twizz"),
                               F("state", "Quand", "choice", "visible", [("visible", "Affiché"), ("hidden", "Masqué"),
                                                                         ("unknown", "État inconnu")])]},
    "tally": {"label": "Tallys (Tally32)",
              "fields": [F("target", "Tally", "tally", "all"),
                         F("state", "Quand", "choice", "ko", [("ok", "Tous connectés à vMix"),
                                                             ("ko", "Au moins un hors ligne")])]},
    "flag": {"label": "État vMix (rec, stream…)", "fields": [F("flag", "État", "choice", "recording", FLAGS)]},
    "toggle": {"label": "Interrupteur activé", "fields": []},
    "pressed": {"label": "Touche enfoncée", "fields": []},
    "health": {"label": "Équipements réseau",
               "fields": [F("targets", "Cibles (Nom = IP ou URL, une par ligne)", "multiline", "",
                            placeholder="Caméra 1 = 192.168.1.50\nRégie = http://192.168.1.20/status"),
                          F("state", "Quand", "choice", "ko", [("ok", "Tous joignables"), ("ko", "Au moins un hors ligne")])]},
    "http_json": {"label": "Valeur d'une API JSON",
                  "fields": [F("url", "URL (GET)", "text", "", placeholder="http://192.168.1.20/api/status"),
                             F("path", "Chemin dans la réponse", "text", "", placeholder="data.viewers  ou  items[0].state"),
                             F("op", "Quand la valeur est", "choice", "==", JSON_OPS),
                             F("value", "Valeur comparée", "text", "", placeholder="on, 100…"),
                             F("interval", "Lire toutes les", "seconds", 5),
                             F("label_fmt", "Texte affiché ({value} = valeur lue, \\n = retour à la ligne)", "text", "",
                               placeholder="Spectateurs\\n{value}")]},
}

_FLAG_FUNCS = {
    "recording": ("startstoprecording", "startrecording", "stoprecording"),
    "streaming": ("startstopstreaming", "startstreaming", "stopstreaming"),
    "multicorder": ("startstopmulticorder", "startmulticorder", "stopmulticorder"),
    "external": ("startstopexternal", "startexternal", "stopexternal"),
    "fadetoblack": ("fadetoblack",),
}


def fb(cond_type: str, bg="", fg="", bar="", label="", **cond) -> dict:
    return {"cond": {"type": cond_type, **cond}, "bg": bg, "fg": fg, "bar": bar, "label": label}


def _program(inp, mix=1):
    return [fb("program", RED, WHITE, input=inp, mix=mix), fb("preview", bar=GREEN, input=inp, mix=mix)]


def _preview(inp, mix=1):
    """Touche Preview / Take : verte = en preview (prête à prendre), rouge = à l'antenne (le rouge l'emporte
    toujours, la dernière condition vraie gagne), liseré vert si l'input est aussi en preview."""
    return [fb("preview", GREEN, BLACK, input=inp, mix=mix), fb("program", RED, WHITE, input=inp, mix=mix),
            fb("preview", bar=GREEN, input=inp, mix=mix)]


def auto(b: dict) -> list[dict]:
    """Ce que le bouton doit afficher, déduit de ce qu'il fait."""
    if b.get("mode") == "toggle":
        return [fb("toggle", YELLOW, BLACK)]
    acts = b.get("actions") or b.get("long_actions") or []
    for i, a in enumerate(acts):
        t = a.get("type")
        if t == "vmix.preview" and a.get("input"):
            # « preview puis transition » = le bouton envoie l'input à l'antenne
            if any(x.get("type") == "vmix.transition" and not x.get("input") for x in acts[i + 1:]):
                return _program(a["input"], a.get("mix", 1))
            return _preview(a["input"], a.get("mix", 1))
        if t == "vmix.take" and a.get("input"):
            return _preview(a["input"], a.get("mix", 1))
        if t == "vmix.program" and a.get("input"):
            return _program(a["input"], a.get("mix", 1))
        if t == "vmix.transition" and a.get("input"):
            return _program(a["input"], a.get("mix", 1))
        if t == "vmix.overlay" and a.get("input"):
            n = a.get("overlay", 1)
            return [fb("overlay", YELLOW, BLACK, input=a["input"], overlay=n, where="program"),
                    fb("overlay", bar=GREEN, input=a["input"], overlay=n, where="preview")]
        if t == "vmix.audio" and a.get("input"):
            return [fb("audio", RED, WHITE, input=a["input"], state="muted"),
                    fb("audio", bar=GREEN, input=a["input"], state="live")]
        if t == "vmix.production" and a.get("what") != "snapshot":
            return [fb("flag", RED, WHITE, flag=a.get("what", "recording"))]
        if t == "intercom.ptt" and a.get("mode") != "alloff":
            bus = a.get("bus", "regie")
            return [fb("intercom", "#2563eb", WHITE, bus=bus, state="active"),
                    fb("intercom", "#1e3a8a", WHITE, bus=bus, state="always_on"),
                    fb("intercom", OFF_BG, OFF_FG, bus=bus, state="offline")]
        if t == "orchestream.overlay":
            ov = a.get("overlay", "twizz")
            return [fb("orchestream", YELLOW, BLACK, overlay=ov, state="visible"),
                    fb("orchestream", bar="#5a6273", overlay=ov, state="unknown")]
        if t == "tally.reconnect":
            target = a.get("target", "all")
            return [fb("tally", "#237423", WHITE, target=target, state="ok"),
                    fb("tally", "#cc0000", WHITE, target=target, state="ko")]
        if t == "vmix.function":
            name = (a.get("function") or "").strip().lower()
            for flag, names in _FLAG_FUNCS.items():
                if name in names:
                    return [fb("flag", RED, WHITE, flag=flag)]
            params = a.get("params") or ""
            m = re.search(r"(?:^|&|\s)input=(.+?)(?=&|\s+[A-Za-z][A-Za-z0-9_]*=|$)", params, re.I)
            inp = urllib.parse.unquote(m.group(1)).strip() if m else ""
            mm = re.search(r"(?:^|&|\s)mix=(\d+)", params, re.I)
            mix = int(mm.group(1)) + 1 if mm else 1  # Mix= de l'API vMix : 0 = Master
            if inp and name in ("cutdirect", "activeinput"):
                return _program(inp, mix)
            if inp and name == "previewinput":
                return _preview(inp, mix)
            om = re.fullmatch(r"overlayinput(\d)(in|out)?", name)
            if inp and om:
                return [fb("overlay", YELLOW, BLACK, input=inp, overlay=int(om.group(1)), where="program")]
        if t == "hotkey" and a.get("hold"):
            return [fb("pressed", BLUE, WHITE)]
    return []


def evaluate(engine, b: dict, cond: dict) -> tuple[bool, dict]:
    t = cond.get("type")
    st = engine.vmix.state
    if t in ("program", "preview"):
        if not st.connected:
            return False, {}
        n = st.number(cond.get("input"))
        mix = int(cond.get("mix") or 1)
        return n is not None and n == (st.program(mix) if t == "program" else st.preview(mix)), {}
    if t == "overlay":
        if not st.connected:
            return False, {}
        slot = st.overlays.get(int(cond.get("overlay") or 1), {})
        val = slot.get(cond.get("where", "program"))
        if cond.get("input"):
            return val is not None and val == st.number(cond["input"]), {}
        return val is not None, {}
    if t == "audio":
        i = st.resolve(cond.get("input")) if st.connected else None
        if not i or i.get("muted") is None:
            return False, {}
        return i["muted"] == (cond.get("state", "muted") == "muted"), {}
    if t == "intercom":
        ic = engine.intercom
        bus, state = cond.get("bus", "regie"), cond.get("state", "active")
        base = (b.get("label") or "Intercom").strip()
        if state == "offline":  # la touche dit pourquoi on ne peut pas parler
            why = ic.unavailable(bus)
            return bool(why), {"label": f"{base}\n{why}"} if why else {}
        info = ic.bus(bus) if ic.reachable and ic.online else {}
        if state == "always_on":
            on = info.get("mode") == "always_on"
            return on, {"label": f"{base}\nPERMANENT"} if on else {}
        return bool(info.get("active")), {}
    if t == "orchestream":
        visible = engine.orchestream.overlays.get(cond.get("overlay", "twizz"))
        state = cond.get("state", "visible")
        if state == "unknown":
            return visible is None, {}
        if state == "hidden":
            return visible is False, {}
        return bool(visible), {}
    if t == "http_json":
        poller = getattr(engine, "jsonpoll", None)
        r = poller.results.get((cond.get("url") or "").strip()) if poller else None
        if cond.get("op") == "error":
            return bool(r and not r.get("ok")), {}
        if not r or not r.get("ok"):
            return False, {}
        val = json_path(r.get("data"), cond.get("path", ""))
        ok = json_compare(val, cond.get("op", "=="), cond.get("value", ""))
        fmt = cond.get("label_fmt") or ""
        return ok, {"label": fmt.replace("\\n", "\n").replace("{value}", json_text(val))} if ok and fmt else {}
    if t == "tally":
        ok, down = engine.tallies.summary(cond.get("target", "all"))
        if ok is None:
            return False, {}
        if cond.get("state", "ko") == "ok":
            return ok, {}
        return not ok, {"label": "Hors ligne\n" + " ".join(down)} if down else {}
    if t == "flag":
        return st.connected and st.flags.get(cond.get("flag", "")), {}
    if t == "toggle":
        return bool(engine.toggles.get(b["id"])), {}
    if t == "pressed":
        return b["id"] in engine.pressed, {}
    if t == "health":
        targets = parse_targets(cond.get("targets", ""))
        res = [(name, engine.health.results.get(addr)) for name, addr in targets]
        if not res or any(ok is None for _, ok in res):
            return False, {}
        down = [name for name, ok in res if not ok]
        if cond.get("state", "ko") == "ok":
            return not down, {}
        return bool(down), {"label": "Hors ligne\n" + " ".join(down)} if down else {}
    return False, {}


def describe(f: dict) -> str:
    c = f["cond"]
    t = c.get("type")
    inp = f"« {c.get('input')} »" if c.get("input") else "un input"
    mix = f" (Mix {c['mix']})" if int(c.get("mix") or 1) > 1 else ""
    what = {
        "program": f"{inp} à l'antenne{mix}",
        "preview": f"{inp} en preview{mix}",
        "overlay": f"{inp} sur l'overlay {c.get('overlay')} " + ("à l'antenne" if c.get("where") == "program" else "en preview"),
        "audio": f"{inp} " + ("coupé" if c.get("state", "muted") == "muted" else "actif"),
        "intercom": {"offline": f"le bus {bus_label(c.get('bus'))} est indisponible (texte : HORS LIGNE, "
                                "DÉSARMÉ ou BUS ABSENT)",
                     "always_on": f"le bus {bus_label(c.get('bus'))} est toujours ouvert (texte : PERMANENT)"}.get(
            c.get("state"), f"le micro {bus_label(c.get('bus'))} est ouvert"),
        "orchestream": {"unknown": "l'état de l'overlay est inconnu (pas encore changé, ou orchestream injoignable)",
                        "hidden": f"l'overlay {overlay_label(c.get('overlay'))} est masqué"}.get(
            c.get("state"), f"l'overlay {overlay_label(c.get('overlay'))} est affiché"),
        "http_json": ("l'API JSON est injoignable" if c.get("op") == "error" else
                      f"« {c.get('path') or 'la réponse'} » " + (
                          "est lue" if c.get("op") == "always" else
                          f"{dict(JSON_OPS).get(c.get('op'), c.get('op'))}"
                          + (f" « {c.get('value')} »" if c.get("op") not in ("truthy",) else ""))),
        "tally": "tous les tallys sont connectés" if c.get("state") == "ok" else "un tally est hors ligne",
        "flag": dict(FLAGS).get(c.get("flag"), "").lower(),
        "toggle": "interrupteur activé",
        "pressed": "bouton enfoncé",
        "health": "tous les équipements joignables" if c.get("state") == "ok" else "un équipement hors ligne",
    }.get(t, t)
    effect = "liseré" if f.get("bar") and not f.get("bg") else "fond"
    color = f.get("bar") if effect == "liseré" else f.get("bg")
    name = {RED: "rouge", GREEN: "vert", YELLOW: "jaune", BLUE: "bleu", "#2563eb": "bleu", "#237423": "vert",
            "#cc0000": "rouge", OFF_BG: "gris", OFF_FG: "gris", "#1e3a8a": "bleu foncé"}.get((color or "").lower(), color or "")
    return f"{effect.capitalize()} {name} quand {what}"
