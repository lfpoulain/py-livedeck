"""Retours d'état : conditions évaluées en direct et retours automatiques déduits des actions."""
from __future__ import annotations

import re
import urllib.parse

from . import adjust
from .actions import F
from .adjust import WIN_TARGETS
from .health import JSON_OPS, json_compare, json_path, json_text, parse_targets
from .integrations import (MULTIVIEW_NEEDS_REF, MULTIVIEW_TARGET_LABEL, MULTIVIEW_TARGETS, bus_label,
                           overlay_label)

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
    "orchdeck": {"label": "orchestream : régie",
                 "fields": [F("command", "Commande", "orch_command", "game.advance"),
                            F("state", "Quand", "choice", "unavailable",
                              [("unavailable", "La commande est impossible maintenant"),
                               ("game", "Une partie est en cours"),
                               ("live", "Le live est en cours"),
                               ("unthanked", "Il reste des viewers à remercier"),
                               ("offline", "orchestream injoignable ou jeton refusé")])]},
    "multiview": {"label": "py-multiview",
                  "fields": [F("target", "Cible", "choice", "sender", MULTIVIEW_TARGETS),
                             F("ref", "Lequel", "mv_item", "", placeholder="nom ou numéro (1, 2…)",
                               when={"target": MULTIVIEW_NEEDS_REF}),
                             F("state", "Quand", "choice", "on",
                               [("on", "En marche"), ("off", "À l'arrêt"),
                                ("error", "En défaut (attente, erreur)"),
                                ("unknown", "État inconnu (py-multiview injoignable)")])]},
    "flag": {"label": "État vMix (rec, stream…)", "fields": [F("flag", "État", "choice", "recording", FLAGS)]},
    "toggle": {"label": "Interrupteur activé", "fields": []},
    "pressed": {"label": "Touche enfoncée", "fields": []},
    "health": {"label": "Équipements réseau",
               "fields": [F("targets", "Cibles (Nom = IP ou URL, une par ligne)", "multiline", "",
                            placeholder="Caméra 1 = 192.168.1.50\nRégie = http://192.168.1.20/status"),
                          F("state", "Quand", "choice", "ko", [("ok", "Tous joignables"), ("ko", "Au moins un hors ligne")])]},
    "win_audio": {"label": "Son Windows (coupé / actif)",
                  "fields": [F("target", "Quoi", "choice", "mic", WIN_TARGETS),
                             F("app", "Application", "winapp", "", placeholder="spotify.exe",
                               when={"target": ("app",)}),
                             F("state", "Quand", "choice", "muted", [("muted", "Coupé"), ("live", "Actif")])]},
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


def _short(text: str, n: int = 22) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[:n - 1] + "…"


def _eval_orchdeck(engine, b: dict, cond: dict) -> tuple[bool, dict]:
    """Retours d'état de l'API Stream Deck d'orchestream-v2 (GET /api/deck/state)."""
    od = engine.orchdeck
    cid, state = cond.get("command", "game.advance"), cond.get("state", "unavailable")
    base = (b.get("label") or "").strip()
    if state == "offline":
        why = od.unavailable()
        return bool(why), ({"label": f"{base or 'orchestream'}\n{why}"} if why else {})
    if od.unavailable():
        return False, {}
    info = od.command_state(cid)
    if state == "unavailable":
        # L'état ne liste que les commandes permises au jeton : absente d'un état bien reçu = refusée.
        # Sans ce cas, une touche « Fin du live » sans la permission s'affichait en rouge « 42 viewers »,
        # prête à servir, alors qu'orchestream la refuserait.
        if info is None:
            return isinstance(od.get("commands"), list), {"label": f"{base or cid}\nNON AUTORISÉ"}
        if info.get("available") is False:
            return True, {"label": f"{base or cid}\n{_short(info.get('reason') or 'indisponible')}"}
        return False, {}
    if state == "game":
        g = od.get("game") or {}
        nxt, n = g.get("nextAction"), g.get("questionNumber")
        if cid == "game.advance" and nxt in ("close", "open", "finish"):
            # Ce que fera la prochaine pression, écrit sur la touche (« Fermer Q3 »)
            return True, {"label": ("Fermer Q{n}", "Ouvrir Q{n}", "Terminer")[("close", "open", "finish").index(nxt)]
                          .format(n=n or "?")}
        return g.get("phase") in ("open", "closed"), {}
    if state == "widget":
        widget = cond.get("widget") or ""
        return bool((od.get("overlays") or {}).get(widget)), {}
    if state == "unthanked":
        n = od.get("unthanked") or 0
        return n > 0, ({"label": f"{base or 'Remercier'}\n{n} à remercier"} if n else {})
    if state == "live":
        # Allumée quand le live est en cours : rouge « à l'antenne », comme l'enregistrement de vMix.
        active = bool(od.get("live.active"))
        if not active:
            return False, {}
        viewers = od.get("live.viewers")
        if cid == "live.end" and viewers is not None:
            return True, {"label": f"{base or 'Fin du live'}\n{viewers} viewers"}
        return True, {}
    return False, {}


def _program(inp, mix=1):
    return [fb("program", RED, WHITE, input=inp, mix=mix), fb("preview", bar=GREEN, input=inp, mix=mix)]


def _preview(inp, mix=1):
    """Touche Preview / Take : verte = en preview (prête à prendre), rouge = à l'antenne (le rouge l'emporte
    toujours, la dernière condition vraie gagne), liseré vert si l'input est aussi en preview."""
    return [fb("preview", GREEN, BLACK, input=inp, mix=mix), fb("program", RED, WHITE, input=inp, mix=mix),
            fb("preview", bar=GREEN, input=inp, mix=mix)]


def auto(b: dict) -> list[dict]:
    """Ce que le bouton doit afficher, déduit de ce qu'il fait.

    Une molette peut cumuler : son appui coupe un input (fond rouge) et sa rotation règle un
    volume (jauge et valeur). Les deux retours coexistent, la jauge ne touchant pas au fond."""
    return _auto_press(b) + _auto_level(b)


def _auto_press(b: dict) -> list[dict]:
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
        if t == "orchestream.command":
            cid = a.get("command") or "game.advance"
            # Tous les retours vrais s'appliquent dans l'ordre : le DERNIER l'emporte. L'état propre à la
            # commande d'abord, puis ce qui l'empêche de servir — qui doit primer : une touche « Fin du
            # live » refusée au jeton s'affichait sinon en rouge « à l'antenne », prête à servir.
            out = []
            if cid in ("game.advance", "game.next", "game.close", "game.finish"):
                out.append(fb("orchdeck", "#1d4ed8", WHITE, command=cid, state="game"))
            elif cid == "overlay.show":
                out.append(fb("orchdeck", YELLOW, BLACK, command=cid, state="widget", widget=a.get("arg", "")))
            elif cid == "activity.ackAll":
                out.append(fb("orchdeck", "#b45309", WHITE, command=cid, state="unthanked"))
            elif cid in ("live.start", "live.end"):
                out.append(fb("orchdeck", RED, WHITE, command=cid, state="live"))
            return out + [fb("orchdeck", OFF_BG, OFF_FG, command=cid, state="unavailable"),
                          fb("orchdeck", OFF_BG, OFF_FG, command=cid, state="offline")]
        if t == "tally.reconnect":
            target = a.get("target", "all")
            return [fb("tally", "#237423", WHITE, target=target, state="ok"),
                    fb("tally", "#cc0000", WHITE, target=target, state="ko")]
        if t == "multiview.control":
            target, ref = a.get("target", "sender"), a.get("ref", "")
            # Le son coupé est un état « en défaut » plutôt qu'une marche : rouge.
            on_colour = "#cc0000" if target == "mute" else "#15803d"
            return [fb("multiview", on_colour, WHITE, target=target, ref=ref, state="on"),
                    fb("multiview", "#7c2d12", WHITE, target=target, ref=ref, state="error"),
                    fb("multiview", bar="#5a6273", target=target, ref=ref, state="unknown")]
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


def _auto_level(b: dict) -> list[dict]:
    """Jauge et valeur de ce que règle la touche ou la molette (liste « rotation » d'abord)."""
    for a in ((b.get("actions_cw") or []) + (b.get("actions_ccw") or []) + (b.get("actions") or [])
              + (b.get("long_actions") or [])):
        t = a.get("type")
        if t == "vmix.adjust" and a.get("target") in ("input_volume", "master", "headphones", "bus"):
            return [fb("level", source="vmix", **_level_args(a))]
        if t == "win.audio":
            args = {"target": a.get("target", "output"), "app": a.get("app", "")}
            return [fb("level", source="win", **args),
                    fb("win_audio", RED, WHITE, state="muted", **args)]
    return []


def _level_args(a: dict) -> dict:
    return {k: a.get(k) for k in ("target", "input", "bus") if a.get(k) not in (None, "")}


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
    if t == "orchdeck":
        return _eval_orchdeck(engine, b, cond)
    if t == "multiview":
        mv = engine.multiview
        target, ref = cond.get("target", "sender"), cond.get("ref", "")
        state = cond.get("state", "on")
        if state == "unknown":
            why = mv.unavailable()
            return bool(why), {"label": f"{(b.get('label') or 'py-multiview').strip()}\n{why}"} if why else {}
        if state == "error":  # démarré mais rien ne passe : attente, relance, erreur
            return mv.status_of(target, ref) in ("waiting", "retrying", "error"), {}
        running = mv.running(target, ref)
        if running is None:
            return False, {}
        return (running if state == "on" else not running), {}
    if t == "tally":
        ok, down = engine.tallies.summary(cond.get("target", "all"))
        if ok is None:
            return False, {}
        if cond.get("state", "ko") == "ok":
            return ok, {}
        return not ok, {"label": "Hors ligne\n" + " ".join(down)} if down else {}
    if t == "flag":
        return st.connected and st.flags.get(cond.get("flag", "")), {}
    if t == "level":
        # Jauge en haut de la touche + valeur sous le libellé. Toujours « vraie » quand la valeur
        # est connue, pour ne colorer rien d'autre que la jauge.
        if cond.get("source") == "win":
            got = adjust.win_value(engine, cond)
            val = got[0] if got else None
        else:
            val = adjust.vmix_value(engine, cond)
        if val is None:
            return False, {}
        base = (b.get("label") or "").strip()
        return True, {"progress": max(0.0, min(1.0, val / 100)),
                      "label": f"{base}\n{val:.0f} %" if base else f"{val:.0f} %"}
    if t == "win_audio":
        got = adjust.win_value(engine, cond)
        if not got:
            return False, {}
        return got[1] == (cond.get("state", "muted") == "muted"), {}
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


def _multiview_text(c: dict) -> str:
    target = c.get("target", "sender")
    what = MULTIVIEW_TARGET_LABEL.get(target, target).lower()
    if target in MULTIVIEW_NEEDS_REF and c.get("ref"):
        what = f"{what} « {c['ref']} »"
    return {"off": f"py-multiview : {what} à l'arrêt",
            "error": f"py-multiview : {what} en défaut",
            "unknown": "py-multiview est injoignable (texte : HORS LIGNE)"}.get(
        c.get("state"), f"py-multiview : {what} en marche")


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
        "multiview": _multiview_text(c),
        "orchdeck": {"offline": "orchestream est injoignable ou refuse le jeton (texte : HORS LIGNE, JETON ?)",
                     "unavailable": "la commande est impossible maintenant (texte : la raison d'orchestream)",
                     "game": "une partie est en cours (texte : ce que fera la prochaine pression)",
                     "widget": "le widget est affiché",
                     "unthanked": "il reste des viewers à remercier (texte : leur nombre)",
                     "live": "le live est en cours"}.get(c.get("state"), "état de la régie orchestream"),
        "flag": dict(FLAGS).get(c.get("flag"), "").lower(),
        "toggle": "interrupteur activé",
        "pressed": "bouton enfoncé",
        "health": "tous les équipements joignables" if c.get("state") == "ok" else "un équipement hors ligne",
        "win_audio": ({"output": "le son de Windows", "mic": "le micro"}.get(c.get("target"), c.get("app") or
                                                                          "l'application")
                      + (" est coupé" if c.get("state", "muted") == "muted" else " est actif")),
    }.get(t, t)
    if t == "level":
        return "Jauge et valeur du niveau réglé"
    effect = "liseré" if f.get("bar") and not f.get("bg") else "fond"
    color = f.get("bar") if effect == "liseré" else f.get("bg")
    name = {RED: "rouge", GREEN: "vert", YELLOW: "jaune", BLUE: "bleu", "#2563eb": "bleu", "#237423": "vert",
            "#cc0000": "rouge", OFF_BG: "gris", OFF_FG: "gris", "#1e3a8a": "bleu foncé", "#1d4ed8": "bleu",
            "#b45309": "orange"}.get((color or "").lower(), color or "")
    return f"{effect.capitalize()} {name} quand {what}"
