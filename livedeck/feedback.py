"""Retours d'état : conditions évaluées en direct et retours automatiques déduits des actions."""
from __future__ import annotations

import re

from .actions import F
from .health import parse_targets
from .integrations import INTERCOM_BUSES, ORCH_OVERLAYS

RED, GREEN, YELLOW, BLUE = "#e5262b", "#22c55e", "#facc15", "#4040ff"
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
                 "fields": [F("bus", "Bus", "choice", "regie", INTERCOM_BUSES),
                            F("state", "Quand", "choice", "active", [("active", "Micro ouvert"),
                                                                     ("offline", "Client désarmé / injoignable")])]},
    "orchestream": {"label": "Overlay orchestream",
                    "fields": [F("overlay", "Overlay", "choice", "twizz", ORCH_OVERLAYS),
                               F("state", "Quand", "choice", "visible", [("visible", "Affiché"),
                                                                         ("unknown", "État inconnu")])]},
    "tally": {"label": "Tallys (Tally32)",
              "fields": [F("target", "Tally", "tally", "all"),
                         F("state", "Quand", "choice", "ko", [("ok", "Tous connectés à vMix"),
                                                             ("ko", "Au moins un hors ligne")])]},
    "flag": {"label": "État vMix (rec, stream…)", "fields": [F("flag", "État", "choice", "recording", FLAGS)]},
    "toggle": {"label": "Interrupteur activé", "fields": []},
    "pressed": {"label": "Bouton enfoncé", "fields": []},
    "health": {"label": "Équipements réseau",
               "fields": [F("targets", "Cibles (Nom = IP ou URL, une par ligne)", "multiline", "",
                            placeholder="Reporter = 10.0.0.163\nGénéral = http://10.0.0.72/status"),
                          F("state", "Quand", "choice", "ko", [("ok", "Tous joignables"), ("ko", "Au moins un hors ligne")])]},
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
    return [fb("preview", GREEN, BLACK, input=inp, mix=mix), fb("program", bar=RED, input=inp, mix=mix)]


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
            mix = a.get("mix", 1)
            return [fb("program", RED, WHITE, input=a["input"], mix=mix),
                    fb("preview", GREEN, BLACK, input=a["input"], mix=mix)]
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
                    fb("intercom", "#1a1d24", "#5a6273", bus=bus, state="offline")]
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
            m = re.search(r"(?:^|&)input=([^&]+)", a.get("params") or "", re.I)
            if m and name in ("cutdirect", "activeinput"):
                return _program(m.group(1))
            if m and name == "previewinput":
                return _preview(m.group(1))
            om = re.fullmatch(r"overlayinput(\d)(in|out)?", name)
            if m and om:
                return [fb("overlay", YELLOW, BLACK, input=m.group(1), overlay=int(om.group(1)), where="program")]
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
        if cond.get("state") == "offline":
            return ic.reachable is False or (ic.reachable and not ic.online), {}
        return bool(ic.reachable and ic.online and ic.bus(cond.get("bus", "regie")).get("active")), {}
    if t == "orchestream":
        visible = engine.orchestream.overlays.get(cond.get("overlay", "twizz"))
        if cond.get("state") == "unknown":
            return visible is None, {}
        return bool(visible), {}
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
        "intercom": (f"le micro {dict(INTERCOM_BUSES).get(c.get('bus'), c.get('bus'))} est ouvert"
                     if c.get("state") != "offline" else "py-intercom est désarmé ou injoignable"),
        "orchestream": (f"l'overlay {dict(ORCH_OVERLAYS).get(c.get('overlay'), c.get('overlay'))} est affiché"
                        if c.get("state") != "unknown" else "l'état de l'overlay est inconnu (orchestream injoignable)"),
        "tally": "tous les tallys sont connectés" if c.get("state") == "ok" else "un tally est hors ligne",
        "flag": dict(FLAGS).get(c.get("flag"), "").lower(),
        "toggle": "interrupteur activé",
        "pressed": "bouton enfoncé",
        "health": "tous les équipements joignables" if c.get("state") == "ok" else "un équipement hors ligne",
    }.get(t, t)
    effect = "liseré" if f.get("bar") and not f.get("bg") else "fond"
    color = f.get("bar") if effect == "liseré" else f.get("bg")
    name = {RED: "rouge", GREEN: "vert", YELLOW: "jaune", BLUE: "bleu", "#2563eb": "bleu", "#237423": "vert",
            "#cc0000": "rouge", "#1a1d24": "gris", "#5a6273": "gris"}.get((color or "").lower(), color or "")
    return f"{effect.capitalize()} {name} quand {what}"
