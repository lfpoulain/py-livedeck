"""Catalogue des actions : champs à éditer, exécution et résumé lisible."""
from __future__ import annotations

import time

from . import hotkeys, webreq
from .integrations import INTERCOM_BUSES, ORCH_OVERLAYS

TRANSITIONS = ["Cut", "Fade", "Merge", "Wipe", "Zoom", "Slide", "Fly", "CrossZoom", "FlyRotate", "Cube",
               "CubeZoom", "VerticalWipe", "VerticalSlide", "Stinger1", "Stinger2", "Stinger3", "Stinger4",
               "Transition1", "Transition2", "Transition3", "Transition4", "FadeToBlack"]

CATEGORIES = {"Vidéo": "#fbbf24", "Audio": "#2dd4bf", "Production": "#fb7185", "Intégrations": "#f472b6",
              "vMix avancé": "#60a5fa", "Clavier": "#a78bfa", "Web": "#38bdf8", "Deck": "#a3e635"}

PRODUCTION = [("recording", "Enregistrement"), ("streaming", "Stream"), ("external", "Sortie externe"),
              ("multicorder", "MultiCorder"), ("fadetoblack", "Fondu au noir (FTB)"), ("snapshot", "Capture d'image")]
PRODUCTION_FUNCS = {
    "recording": ("StartStopRecording", "StartRecording", "StopRecording"),
    "streaming": ("StartStopStreaming", "StartStreaming", "StopStreaming"),
    "external": ("StartStopExternal", "StartExternal", "StopExternal"),
    "multicorder": ("StartStopMultiCorder", "StartMultiCorder", "StopMultiCorder"),
    "fadetoblack": ("FadeToBlack",) * 3,
    "snapshot": ("Snapshot",) * 3,
}
TOGGLE_MODES = [("toggle", "Démarrer / arrêter"), ("start", "Démarrer"), ("stop", "Arrêter")]
AUDIO_MODES = [("toggle", "Couper / rétablir"), ("off", "Couper"), ("on", "Rétablir")]

PTT_MODES = [("hold", "Maintenu (parler tant que la touche est enfoncée)"), ("toggle", "Basculer"),
             ("on", "Ouvrir le micro"), ("off", "Fermer le micro"), ("alloff", "Couper tous les bus")]
SHOW_MODES = [("toggle", "Basculer (afficher / masquer)"), ("show", "Afficher"), ("hide", "Masquer")]

OVERLAY_MODES = [("toggle", "Basculer (afficher / masquer)"), ("in", "Afficher"), ("out", "Masquer"),
                 ("off", "Couper sans transition"), ("preview", "Envoyer en preview")]

VMIX_FUNCTIONS = [
    "PreviewInput", "CutDirect", "ActiveInput", "Cut", "Fade", "Merge", "Stinger1", "Stinger2",
    "Transition1", "Transition2", "Transition3", "Transition4", "QuickPlay", "FadeToBlack",
    "OverlayInput1", "OverlayInput2", "OverlayInput3", "OverlayInput4", "OverlayInput1Off",
    "OverlayInputAllOff", "PreviewOverlayInput1",
    "StartStopRecording", "StartRecording", "StopRecording", "StartStopStreaming", "StartStreaming",
    "StopStreaming", "StartStopExternal", "StartStopMultiCorder", "Snapshot", "SnapshotInput",
    "Play", "Pause", "PlayPause", "Restart", "Loop", "NextItem", "PreviousItem", "SelectIndex",
    "SetText", "SetImage", "SetVolume", "SetVolumeFade", "AudioOn", "AudioOff", "Audio", "AudioBus",
    "MasterAudioON", "MasterAudioOFF", "MasterAudio", "BusXAudio", "SetMultiViewOverlay",
    "MultiViewOverlay", "MultiViewOverlayOn", "MultiViewOverlayOff", "KeyPress", "ScriptStart",
    "ScriptStop", "ScriptStopAll", "DataSourceAutoNextOn", "DataSourceNextRow", "ReplayMarkIn",
    "ReplayMarkOut", "ReplayPlay", "ReplayStopEvents", "PTZMoveToVirtualInputPosition",
    "PTZHome", "SetDynamicInput1", "SetDynamicValue1", "SelectTitlePreset", "TitleBeginAnimation",
    "SetCountdown", "StartCountdown", "StopCountdown", "ResetCountdown",
]


def F(key, label, kind, default, options=None, placeholder=""):
    return {"key": key, "label": label, "kind": kind, "default": default, "options": options,
            "placeholder": placeholder}


ACTIONS: dict[str, dict] = {
    "vmix.preview": {"label": "Preview", "cat": "Vidéo", "color": "#003300",
                     "help": "Envoie l'input en preview.",
                     "fields": [F("input", "Input", "input", ""), F("mix", "Mix", "mix", 1)]},
    "vmix.program": {"label": "Program (cut direct)", "cat": "Vidéo", "color": "#330000",
                     "help": "Envoie l'input directement à l'antenne.",
                     "fields": [F("input", "Input", "input", ""), F("mix", "Mix", "mix", 1)]},
    "vmix.transition": {"label": "Transition", "cat": "Vidéo", "color": "#ff8000",
                        "help": "Passe la preview à l'antenne avec l'effet choisi (ou l'input indiqué).",
                        "fields": [F("effect", "Effet", "choice", "Cut", [(t, t) for t in TRANSITIONS]),
                                   F("duration", "Durée (ms, 0 = réglage vMix)", "int", 0),
                                   F("input", "Input (optionnel)", "input", ""), F("mix", "Mix", "mix", 1)]},
    "vmix.take": {"label": "Take (preview puis antenne)", "cat": "Vidéo", "color": "#2b1d00",
                  "help": "1er appui : l'input passe en preview. 2e appui : la transition l'envoie à l'antenne.",
                  "fields": [F("input", "Input", "input", ""),
                             F("effect", "Transition", "choice", "Cut", [(t, t) for t in TRANSITIONS]),
                             F("duration", "Durée (ms, 0 = réglage vMix)", "int", 0), F("mix", "Mix", "mix", 1)]},
    "vmix.overlay": {"label": "Overlay / PIP", "cat": "Vidéo", "color": "#333300",
                     "help": "Affiche ou masque un input sur un canal d'overlay.",
                     "fields": [F("input", "Input", "input", ""), F("overlay", "Overlay", "overlay", 1),
                                F("mode", "Mode", "choice", "toggle", OVERLAY_MODES)]},
    "vmix.audio": {"label": "Couper un son", "cat": "Audio", "color": "#0b2e2a",
                   "help": "Coupe ou rétablit l'audio d'un input (micro, musique…).",
                   "fields": [F("input", "Input", "input", ""), F("mode", "Mode", "choice", "toggle", AUDIO_MODES)]},
    "vmix.production": {"label": "Rec / Stream / FTB", "cat": "Production", "color": "#330000",
                        "help": "Enregistrement, stream, sortie externe, MultiCorder, fondu au noir.",
                        "fields": [F("what", "Quoi", "choice", "recording", PRODUCTION),
                                   F("mode", "Mode", "choice", "toggle", TOGGLE_MODES)]},
    "vmix.function": {"label": "Fonction vMix", "cat": "vMix avancé", "color": "#1e293b",
                      "help": "N'importe quelle fonction de l'API vMix.",
                      "fields": [F("function", "Fonction", "function", ""),
                                 F("params", "Paramètres", "text", "", placeholder="Input=Titre&Value=Bonjour")]},
    "intercom.ptt": {"label": "Intercom (py-intercom)", "cat": "Intégrations", "color": "#0b1d3a",
                     "help": "Parle sur un bus de py-intercom. La touche s'allume quand le micro est ouvert, "
                             "se grise quand le client est désarmé.",
                     "fields": [F("bus", "Bus", "choice", "regie", INTERCOM_BUSES),
                                F("mode", "Mode", "choice", "hold", PTT_MODES)]},
    "orchestream.overlay": {"label": "Overlay orchestream", "cat": "Intégrations", "color": "#333300",
                            "help": "Affiche ou masque un overlay web d'orchestream. L'état suit en direct les "
                                    "changements faits ailleurs (régie web, autre opérateur).",
                            "fields": [F("overlay", "Overlay", "choice", "twizz", ORCH_OVERLAYS),
                                       F("mode", "Mode", "choice", "toggle", SHOW_MODES)]},
    "tally.reconnect": {"label": "Tallys : reconnecter", "cat": "Intégrations", "color": "#000000",
                        "help": "Demande aux Tally32 de se reconnecter à vMix. Le retour d'état montre en "
                                "direct les tallys hors ligne.",
                        "fields": [F("target", "Tally", "tally", "all")]},
    "hotkey": {"label": "Raccourci clavier", "cat": "Clavier", "color": "#000033",
               "help": "Simule une combinaison de touches sur ce PC.",
               "fields": [F("keys", "Touches", "hotkey", ""),
                          F("hold", "Maintenir tant que le bouton est enfoncé", "bool", False)]},
    "http": {"label": "Requête HTTP / curl", "cat": "Web", "color": "#172554",
             "help": "Appelle une URL (API, webhook, overlay web…).",
             "fields": [F("method", "Méthode", "choice", "POST",
                          [(m, m) for m in ("GET", "POST", "PUT", "PATCH", "DELETE")]),
                        F("url", "URL", "text", "", placeholder="http://…"),
                        F("body", "Corps", "multiline", ""),
                        F("content_type", "Content-Type", "text", "application/json"),
                        F("headers", "En-têtes (un par ligne)", "multiline", "", placeholder="Authorization: Bearer …")]},
    "page": {"label": "Changer de page", "cat": "Deck", "color": "#262626",
             "help": "Affiche une autre page sur ce Stream Deck.",
             "fields": [F("page", "Page", "page", "next")]},
    "delay": {"label": "Pause", "cat": "Avancé", "color": "#262626",
              "help": "Attend avant l'action suivante.",
              "fields": [F("ms", "Durée (ms)", "int", 250)]},
}


def change_type(a: dict, type_: str) -> dict:
    """Nouvelle action du type choisi, en gardant les réglages compatibles (input, mix…)."""
    new = new_action(type_)
    for k in new:
        if k != "type" and k in a:
            new[k] = a[k]
    return new


def new_action(type_: str, **values) -> dict:
    a = {"type": type_}
    for f in ACTIONS[type_]["fields"]:
        a[f["key"]] = f["default"]
    a.update(values)
    return a


def _mix(a) -> int | None:
    m = int(a.get("mix") or 1)
    return m - 1 if m > 1 else None


def execute(engine, a: dict, phase: str, ctx: dict) -> None:
    t = a.get("type")
    if phase == "up":
        if t == "hotkey" and a.get("hold") and a.get("keys"):
            hotkeys.release(a["keys"])
        elif t == "intercom.ptt" and a.get("mode", "hold") == "hold":
            engine.intercom.command(a.get("bus", "regie"), "off")
        return
    vm = engine.vmix
    if t == "vmix.preview":
        vm.function("PreviewInput", Input=vm.input_param(a.get("input")), Mix=_mix(a))
    elif t == "vmix.program":
        vm.function("CutDirect", Input=vm.input_param(a.get("input")), Mix=_mix(a))
    elif t == "vmix.transition":
        dur = int(a.get("duration") or 0)
        vm.function(a.get("effect") or "Cut", Input=vm.input_param(a["input"]) if a.get("input") else None,
                    Duration=dur or None, Mix=_mix(a))
    elif t == "vmix.take":
        mix = int(a.get("mix") or 1)
        n = vm.state.number(a.get("input"))
        if n is not None and vm.state.preview(mix) == n:
            dur = int(a.get("duration") or 0)
            vm.function(a.get("effect") or "Cut", Duration=dur or None, Mix=_mix(a))
        else:
            vm.function("PreviewInput", Input=vm.input_param(a.get("input")), Mix=_mix(a))
    elif t == "vmix.overlay":
        n, mode = int(a.get("overlay") or 1), a.get("mode", "toggle")
        name = {"toggle": f"OverlayInput{n}", "in": f"OverlayInput{n}In", "out": f"OverlayInput{n}Out",
                "off": f"OverlayInput{n}Off", "preview": f"PreviewOverlayInput{n}"}[mode]
        vm.function(name, Input=None if mode == "off" else vm.input_param(a.get("input")))
    elif t == "vmix.audio":
        name = {"toggle": "Audio", "off": "AudioOff", "on": "AudioOn"}[a.get("mode", "toggle")]
        vm.function(name, Input=vm.input_param(a.get("input")))
    elif t == "vmix.production":
        funcs = PRODUCTION_FUNCS.get(a.get("what"), PRODUCTION_FUNCS["recording"])
        vm.function(funcs[{"toggle": 0, "start": 1, "stop": 2}.get(a.get("mode"), 0)])
    elif t == "vmix.function":
        params = (a.get("params") or "").strip().lstrip("&?")
        vm.raw(f"{a.get('function', '').strip()}&{params}" if params else a.get("function", ""))
    elif t == "intercom.ptt":
        mode = a.get("mode", "hold")
        if mode == "alloff":
            engine.intercom.command("all", "off")
        else:
            engine.intercom.command(a.get("bus", "regie"), {"hold": "on"}.get(mode, mode))
    elif t == "orchestream.overlay":
        engine.orchestream.command(a.get("overlay", "twizz"), a.get("mode", "toggle"))
    elif t == "tally.reconnect":
        engine.tallies.reconnect(a.get("target", "all"))
    elif t == "hotkey":
        if a.get("keys"):
            (hotkeys.press if a.get("hold") else hotkeys.tap)(a["keys"])
    elif t == "http":
        status, text = webreq.request(a.get("method", "GET"), a.get("url", ""), a.get("body", ""),
                                      a.get("headers", ""), a.get("content_type", ""))
        level = "info" if 200 <= status < 300 else "error"
        engine.log(level, f"HTTP {status} ← {a.get('url')}" + (f" : {text[:120]}" if level == "error" else ""))
    elif t == "page":
        engine.goto_page(ctx.get("deck"), a.get("page", "next"))
    elif t == "delay":
        time.sleep(max(0, int(a.get("ms") or 0)) / 1000)


def summary(a: dict, page_names: dict | None = None) -> str:
    t = a.get("type")
    mix = f" (Mix {a['mix']})" if int(a.get("mix") or 1) > 1 else ""
    if t == "vmix.preview":
        return f"Preview · {a.get('input') or '?'}{mix}"
    if t == "vmix.program":
        return f"Program · {a.get('input') or '?'}{mix}"
    if t == "vmix.transition":
        return f"{a.get('effect')}" + (f" · {a['input']}" if a.get("input") else "") + mix
    if t == "vmix.take":
        return f"Take · {a.get('input') or '?'} ({a.get('effect')}){mix}"
    if t == "vmix.overlay":
        mode = dict(OVERLAY_MODES).get(a.get("mode"), "").split(" ")[0].lower()
        return f"Overlay {a.get('overlay')} · {a.get('input') or '?'} ({mode})"
    if t == "vmix.audio":
        mode = dict(AUDIO_MODES).get(a.get("mode"), "").lower()
        return f"Audio · {a.get('input') or '?'} ({mode})"
    if t == "vmix.production":
        mode = dict(TOGGLE_MODES).get(a.get("mode"), "").lower()
        return f"{dict(PRODUCTION).get(a.get('what'), '?')} ({mode})"
    if t == "vmix.function":
        return f"vMix · {a.get('function')} {a.get('params') or ''}".strip()
    if t == "intercom.ptt":
        if a.get("mode") == "alloff":
            return "Intercom · couper tous les bus"
        mode = {"hold": "maintenu", "toggle": "basculer", "on": "ouvrir", "off": "fermer"}.get(a.get("mode"), "")
        return f"Intercom · {dict(INTERCOM_BUSES).get(a.get('bus'), a.get('bus'))} ({mode})"
    if t == "orchestream.overlay":
        mode = dict(SHOW_MODES).get(a.get("mode"), "").split(" ")[0].lower()
        return f"Orchestream · {dict(ORCH_OVERLAYS).get(a.get('overlay'), a.get('overlay'))} ({mode})"
    if t == "tally.reconnect":
        return "Tallys · reconnecter à vMix" + ("" if a.get("target", "all") == "all" else f" ({a['target']})")
    if t == "hotkey":
        return f"Clavier · {a.get('keys') or '?'}" + (" (maintenu)" if a.get("hold") else "")
    if t == "http":
        return f"{a.get('method')} {a.get('url')}"
    if t == "page":
        p = a.get("page")
        label = {"next": "suivante", "prev": "précédente"}.get(p) or (page_names or {}).get(p, p)
        return f"Page → {label}"
    if t == "delay":
        return f"Pause {a.get('ms')} ms"
    return t or "?"
