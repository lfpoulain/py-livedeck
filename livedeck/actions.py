"""Catalogue des actions : champs à éditer, exécution et résumé lisible."""
from __future__ import annotations

import copy
import time

from . import adjust, hotkeys, netsend, webreq, winsys
from .adjust import BUSES, VMIX_STEP, VMIX_TARGETS, WIN_MODES, WIN_TARGETS
from .integrations import (MULTIVIEW_NEEDS_REF, MULTIVIEW_TARGET_LABEL, MULTIVIEW_TARGETS, ORCH_COMMAND_ARG,
                           ORCH_COMMAND_LABEL, UNREACHABLE, bus_label, overlay_label)
from .webreq import error_text

# Fonctions de transition de l'API vMix (FadeToBlack n'en est pas une : c'est un interrupteur, voir PRODUCTION)
TRANSITIONS = ["Cut", "Fade", "Zoom", "Wipe", "Slide", "Fly", "CrossZoom", "FlyRotate", "Cube", "CubeZoom",
               "VerticalWipe", "VerticalSlide", "Merge", "WipeReverse", "SlideReverse", "VerticalWipeReverse",
               "VerticalSlideReverse", "Stinger1", "Stinger2", "Stinger3", "Stinger4",
               "Transition1", "Transition2", "Transition3", "Transition4"]
TRANSITION_CHOICES = [(t, f"Bouton transition {t[-1]} (réglé dans vMix)" if t.startswith("Transition")
                       else f"Stinger {t[-1]}" if t.startswith("Stinger")
                       else t.replace("Reverse", " inversé")) for t in TRANSITIONS]
THEN_CHOICES = [("none", "Rien (il reste en preview)")] + TRANSITION_CHOICES

CATEGORIES = {"Vidéo": "#fbbf24", "Audio": "#2dd4bf", "Production": "#fb7185", "Intégrations": "#f472b6",
              "vMix avancé": "#60a5fa", "Molette": "#5eead4", "Clavier": "#a78bfa", "Windows": "#818cf8",
              "Web": "#38bdf8",
              "Réseau": "#34d399", "Deck": "#a3e635", "Avancé": "#94a3b8"}
MEDIA = [("playpause", "Lecture / pause"), ("next", "Piste suivante"), ("previous", "Piste précédente"),
         ("mute", "Couper le son (touche média)")]
SCROLL_DIRS = [("vertical", "Verticale (haut / bas)"), ("horizontal", "Horizontale (gauche / droite)")]
# action ou condition -> intégration (clé de Réglages → Intégrations et de integration_status)
INTEGRATION_OF = {"intercom.ptt": "intercom", "orchestream.overlay": "orchestream", "tally.reconnect": "tallies",
                  "orchestream.command": "orchdeck", "orchdeck": "orchdeck",
                  "multiview.control": "multiview", "multiview.volume": "multiview",
                  "intercom": "intercom", "orchestream": "orchestream", "tally": "tallies",
                  "multiview": "multiview"}

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

PTT_MODES = [("hold", "Maintenu (push-to-talk)"), ("toggle", "Basculer"),
             ("on", "Ouvrir le micro"), ("off", "Fermer le micro"), ("alloff", "Couper tous les bus")]
SHOW_MODES = [("toggle", "Basculer (afficher / masquer)"), ("show", "Afficher"), ("hide", "Masquer")]
ON_OFF_MODES = [("toggle", "Basculer (marche / arrêt)"), ("on", "Démarrer"), ("off", "Arrêter")]
NET_PROTOS = [("tcp", "TCP"), ("udp", "UDP")]

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


def F(key, label, kind, default, options=None, placeholder="", when=None):
    """Champ d'une action. when : {champ: (valeurs…)} — le champ n'est montré que si l'action a l'une
    de ces valeurs (ex. l'input n'apparaît que pour une cible qui en demande un)."""
    return {"key": key, "label": label, "kind": kind, "default": default, "options": options,
            "placeholder": placeholder, "when": when}


def shown(spec: dict, a: dict) -> bool:
    """Le champ `spec` a-t-il un sens pour l'action `a` dans son état actuel ?"""
    for k, vals in (spec.get("when") or {}).items():
        if a.get(k) not in vals:
            return False
    return True


ACTIONS: dict[str, dict] = {
    "vmix.preview": {"label": "Preview", "cat": "Vidéo", "color": "#003300",
                     "help": "Envoie l'input en preview (touche verte ; rouge s'il est à l'antenne). Option : "
                             "un 2e appui alors qu'il est déjà en preview l'envoie à l'antenne avec la "
                             "transition choisie.",
                     "fields": [F("input", "Input", "input", ""), F("mix", "Mix", "mix", 1),
                                F("then", "2e appui (déjà en preview)", "choice", "none", THEN_CHOICES),
                                F("duration", "Durée (ms, 0 = réglage vMix)", "int", 0)]},
    "vmix.program": {"label": "Program (cut direct)", "cat": "Vidéo", "color": "#330000",
                     "help": "Envoie l'input directement à l'antenne.",
                     "fields": [F("input", "Input", "input", ""), F("mix", "Mix", "mix", 1)]},
    "vmix.transition": {"label": "Transition", "cat": "Vidéo", "color": "#ff8000",
                        "help": "Passe la preview à l'antenne avec l'effet choisi (ou l'input indiqué).",
                        "fields": [F("effect", "Effet", "choice", "Cut", TRANSITION_CHOICES),
                                   F("duration", "Durée (ms, 0 = réglage vMix)", "int", 0),
                                   F("input", "Input (optionnel)", "input", ""), F("mix", "Mix", "mix", 1)]},
    "vmix.take": {"label": "Take (preview puis antenne)", "cat": "Vidéo", "color": "#2b1d00",
                  "help": "1er appui : l'input passe en preview (touche verte = prêt à prendre). 2e appui : "
                          "il passe à l'antenne avec la transition choisie (touche rouge = à l'antenne). "
                          "S'il est déjà à l'antenne, rien ne se passe.",
                  "fields": [F("input", "Input", "input", ""),
                             F("effect", "Transition", "choice", "Cut", TRANSITION_CHOICES),
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
    "vmix.adjust": {"label": "Régler au cran (volume, lecture…)", "cat": "Audio", "color": "#0b2e2a",
                    "help": "Fait pour les molettes : chaque cran monte ou baisse un volume, avance dans une vidéo, "
                            "parcourt les inputs en preview… Le sens de rotation donne le sens du réglage ; sur "
                            "une touche, une valeur négative fait baisser. La jauge et la valeur s'affichent.",
                    "fields": [F("target", "Régler", "choice", "master", VMIX_TARGETS),
                               F("input", "Input", "input", "",
                                 when={"target": adjust.NEEDS_INPUT}),
                               F("bus", "Bus", "choice", "A", BUSES, when={"target": ("bus",)}),
                               F("mix", "Mix", "mix", 1, when={"target": ("preview",)}),
                               F("amount", "Par cran", "step", 2)]},
    "vmix.function": {"label": "Fonction vMix", "cat": "vMix avancé", "color": "#1e293b",
                      "help": "N'importe quelle fonction de l'API vMix.",
                      "fields": [F("function", "Fonction", "function", ""),
                                 F("params", "Paramètres", "text", "", placeholder="Input=Titre&Value=Bonjour")]},
    "intercom.ptt": {"label": "Intercom (py-intercom)", "cat": "Intégrations", "color": "#0b1d3a",
                     "help": "Parle sur un bus de py-intercom (Maintenu : tant que la touche est enfoncée). "
                             "La touche s'allume quand le micro est ouvert et affiche HORS LIGNE, DÉSARMÉ ou "
                             "BUS ABSENT sinon. Raccourci de secours : touches simulées si py-intercom ne "
                             "répond pas (modes Maintenu et Basculer).",
                     "fields": [F("bus", "Bus", "intercom_bus", "regie"),
                                F("mode", "Mode", "choice", "hold", PTT_MODES),
                                F("fallback_keys", "Raccourci de secours (si py-intercom ne répond pas)",
                                  "hotkey", "")]},
    "orchestream.overlay": {"label": "Overlay orchestream", "cat": "Intégrations", "color": "#333300",
                            "help": "Affiche ou masque un overlay web d'orchestream. L'état suit en direct les "
                                    "changements faits ailleurs (régie web, autre opérateur).",
                            "fields": [F("overlay", "Overlay", "orch_overlay", "twizz"),
                                       F("mode", "Mode", "choice", "toggle", SHOW_MODES)]},
    "tally.reconnect": {"label": "Tallys : reconnecter", "cat": "Intégrations", "color": "#000000",
                        "help": "Demande aux Tally32 de se reconnecter à vMix. Le retour d'état montre en "
                                "direct les tallys hors ligne.",
                        "fields": [F("target", "Tally", "tally", "all")]},
    "orchestream.command": {"label": "orchestream : commande régie", "cat": "Intégrations", "color": "#2a1f05",
                            "help": "Lance une commande de la régie orchestream-v2 (jeu suivant, tirage, Twizz, "
                                    "remercier, widget, fin du live…) par son API Stream Deck. Demande un jeton API "
                                    "(Réglages → Intégrations). La touche se grise quand la commande n'est pas "
                                    "possible, avec la raison donnée par orchestream.",
                            "fields": [F("command", "Commande", "orch_command", "game.advance"),
                                       F("arg", "Paramètre", "orch_arg", "",
                                         when={"command": tuple(ORCH_COMMAND_ARG)})]},
    "multiview.control": {"label": "py-multiview", "cat": "Intégrations", "color": "#0f2a24",
                          "help": "Démarre ou arrête une partie de py-multiview : un flux, toute la "
                                  "réception, l'émission OMT, le retour audio ou la fenêtre de retour. "
                                  "L'état de la touche suit ce que fait py-multiview.",
                          "fields": [F("target", "Cible", "choice", "sender", MULTIVIEW_TARGETS),
                                     F("ref", "Lequel", "mv_item", "",
                                       placeholder="nom ou numéro (1, 2…)",
                                       when={"target": MULTIVIEW_NEEDS_REF}),
                                     F("mode", "Mode", "choice", "toggle", ON_OFF_MODES)]},
    "multiview.volume": {"label": "py-multiview : volume du retour", "cat": "Intégrations", "color": "#0f2a24",
                         "help": "Règle le volume du retour audio de py-multiview.",
                         "fields": [F("value", "Volume", "int", 50, (0, 100))]},
    "hotkey": {"label": "Raccourci clavier", "cat": "Clavier", "color": "#000033",
               "help": "Simule une combinaison de touches sur ce PC.",
               "fields": [F("keys", "Touches", "hotkey", ""),
                          F("hold", "Maintenir tant que la touche est enfoncée", "bool", False)]},
    "win.audio": {"label": "Son Windows (volume, micro, appli)", "cat": "Windows", "color": "#1e1b4b",
                  "help": "Volume ou coupure du son de Windows, du micro par défaut ou d'une seule application "
                          "(Spotify, navigateur, Teams…). Sur une molette, « Monter / baisser » suit le sens de "
                          "rotation. Une application n'est visible que pendant qu'elle produit du son.",
                  "fields": [F("target", "Quoi", "choice", "output", WIN_TARGETS),
                             F("app", "Application", "winapp", "", placeholder="spotify.exe",
                               when={"target": ("app",)}),
                             F("mode", "Action", "choice", "adjust", WIN_MODES),
                             F("amount", "Par cran", "step", 2, when={"mode": ("adjust",)}),
                             F("level", "Volume", "percent", 50, when={"mode": ("set",)})]},
    "win.media": {"label": "Lecture multimédia", "cat": "Windows", "color": "#1e1b4b",
                  "help": "Touches multimédia de Windows : pilotent le lecteur actif (Spotify, navigateur…).",
                  "fields": [F("what", "Touche", "choice", "playpause", MEDIA)]},
    "win.launch": {"label": "Ouvrir un programme / fichier / site", "cat": "Windows", "color": "#1e1b4b",
                   "help": "Comme un double-clic : un programme (.exe), un fichier, un dossier ou une adresse web. "
                           "Les variables d'environnement sont acceptées (%USERPROFILE%\\Desktop…).",
                   "fields": [F("target", "Programme, fichier ou adresse", "text", "",
                                placeholder="C:\\Program Files\\OBS\\obs64.exe  ou  https://…"),
                              F("args", "Arguments", "text", "", placeholder="--startrecording")]},
    "win.focus": {"label": "Mettre une fenêtre au premier plan", "cat": "Windows", "color": "#1e1b4b",
                  "help": "Active la première fenêtre dont le titre contient ce texte. Utile avant un raccourci "
                          "clavier, pour qu'il arrive dans le bon logiciel.",
                  "fields": [F("title", "Titre de la fenêtre (une partie suffit)", "text", "", placeholder="vMix")]},
    "win.run": {"label": "Commande Windows", "cat": "Windows", "color": "#1e1b4b",
                "help": "Lance une commande (comme dans l'invite de commandes), sans attendre qu'elle se termine.",
                "fields": [F("command", "Commande", "text", "", placeholder="shutdown /a"),
                           F("hidden", "Sans fenêtre", "bool", True)]},
    "win.type": {"label": "Taper un texte", "cat": "Windows", "color": "#1e1b4b",
                 "help": "Saisit le texte dans la fenêtre active, comme au clavier.",
                 "fields": [F("text", "Texte", "multiline", "")]},
    "win.scroll": {"label": "Molette de souris", "cat": "Windows", "color": "#1e1b4b",
                   "help": "Fait défiler la fenêtre sous la souris : timeline, liste, page web. Sur une molette, "
                           "le sens de rotation donne le sens du défilement.",
                   "fields": [F("direction", "Sens", "choice", "vertical", SCROLL_DIRS),
                              F("amount", "Lignes par cran", "step", 1)]},
    "win.lock": {"label": "Verrouiller la session", "cat": "Windows", "color": "#1e1b4b",
                 "help": "Verrouille Windows (comme Win+L). Les surfaces de contrôle restent actives.",
                 "fields": []},
    "http": {"label": "Requête HTTP / curl", "cat": "Web", "color": "#172554",
             "help": "Appelle une URL (API, webhook, overlay web…).",
             "fields": [F("method", "Méthode", "choice", "POST",
                          [(m, m) for m in ("GET", "POST", "PUT", "PATCH", "DELETE")]),
                        F("url", "URL", "text", "", placeholder="http://…"),
                        F("body", "Corps", "multiline", ""),
                        F("content_type", "Content-Type", "text", "application/json"),
                        F("headers", "En-têtes (un par ligne)", "multiline", "", placeholder="Authorization: Bearer …")]},
    "net.send": {"label": "Commande TCP / UDP", "cat": "Réseau", "color": "#052e2b",
                 "help": "Envoie une trame à un appareil (mélangeur, caméra, automate…). Échappements : "
                         "\\r \\n \\t \\xNN (ex. « PWR ON\\r »), ou coche Hexadécimal (« 81 01 04 00 02 FF »).",
                 "fields": [F("proto", "Protocole", "choice", "tcp", NET_PROTOS),
                            F("host", "Machine (IP ou nom)", "text", "", placeholder="192.168.1.50"),
                            F("port", "Port", "port", 9000),
                            F("payload", "Données", "text", "", placeholder="PWR ON\\r"),
                            F("hex", "Données en hexadécimal", "bool", False)]},
    "osc": {"label": "Message OSC", "cat": "Réseau", "color": "#052e2b",
            "help": "Envoie un message OSC en UDP (QLab, Reaper, consoles X32, TouchDesigner…). Arguments "
                    "séparés par des espaces : 1, 0.5, true, \"texte\" ; préfixes i: f: s: pour forcer le type.",
            "fields": [F("host", "Machine (IP ou nom)", "text", "127.0.0.1", placeholder="127.0.0.1"),
                       F("port", "Port", "port", 8000),
                       F("address", "Adresse OSC", "text", "", placeholder="/go"),
                       F("args", "Arguments", "text", "", placeholder="1 0.5 \"texte\"")]},
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


def is_momentary(a: dict) -> bool:
    """Action « maintenue » : elle commence à l'appui et doit être relâchée (phase « up »)."""
    t = a.get("type")
    return ((t == "intercom.ptt" and a.get("mode", "hold") == "hold")
            or (t == "hotkey" and bool(a.get("hold")) and bool(a.get("keys"))))


def momentary(acts) -> list[dict]:
    return [a for a in acts or [] if is_momentary(a)]


def is_relative(a: dict) -> bool:
    """Action « au cran » : le sens de rotation d'une molette décide du sens du réglage. Une telle
    action, placée d'un seul côté d'une molette, s'applique donc dans les deux sens."""
    t = a.get("type")
    return t in ("vmix.adjust", "win.scroll") or (t == "win.audio" and a.get("mode", "adjust") == "adjust")


def _mix(a) -> int | None:
    m = int(a.get("mix") or 1)
    return m - 1 if m > 1 else None


def _input(engine, a: dict):
    """(réf, numéro) de l'input d'une action vMix, ou None (avec un message clair) s'il manque ou si vMix,
    lu et à jour, ne le connaît pas : on n'envoie jamais une commande que vMix refuserait."""
    ref = str(a.get("input") or "").strip()
    label = ACTIONS.get(a.get("type"), {}).get("label", "vMix")
    if not ref:
        engine.log("warn", f"{label} : aucun input choisi")
        return None
    st = engine.vmix.state
    n = st.number(ref)
    if n is None and st.connected and st.inputs:
        engine.log("error", f"{label} : input « {ref} » introuvable dans vMix")
        return None
    return ref, n


def _transition(vm, effect: str, ref: str | None, n: int | None, a: dict, mix: int) -> None:
    """Transition vers l'input indiqué (sinon la preview), puis état « à l'antenne » affiché sans attendre."""
    effect = effect or "Cut"
    dur = int(a.get("duration") or 0)
    if vm.function(effect, Input=vm.input_param(ref) if ref else None, Duration=dur or None, Mix=_mix(a)):
        target = n if ref else vm.state.preview(mix)
        # seul le program est anticipé : l'échange preview / program dépend des réglages de vMix
        vm.expect(mix, ttl=1.0 + vm.transition_ms(effect, dur) / 1000, active=target)


def execute(engine, a: dict, phase: str, ctx: dict) -> None:
    t = a.get("type")
    if phase == "up":
        if t == "hotkey" and a.get("hold") and a.get("keys"):
            hotkeys.release(a["keys"])
        elif t == "intercom.ptt" and a.get("mode", "hold") == "hold":
            _ptt_release(engine, a)
        return
    vm = engine.vmix
    st = vm.state
    mix = int(a.get("mix") or 1)
    if t in ("vmix.preview", "vmix.program", "vmix.take", "vmix.audio") or (
            t == "vmix.overlay" and a.get("mode", "toggle") != "off"):
        got = _input(engine, a)
        if not got:
            return
        ref, n = got
    if t == "vmix.preview":
        then = a.get("then") or "none"
        if then != "none" and n is not None and st.preview(mix) == n and st.program(mix) != n:
            _transition(vm, then, ref, n, a, mix)  # option « 2e appui : à l'antenne »
        elif vm.function("PreviewInput", Input=vm.input_param(ref), Mix=_mix(a)):
            vm.expect(mix, preview=n)
    elif t == "vmix.program":
        if vm.function("CutDirect", Input=vm.input_param(ref), Mix=_mix(a)):
            vm.expect(mix, active=n)
    elif t == "vmix.transition":
        ref = str(a.get("input") or "").strip() or None
        n = st.number(ref) if ref else None
        if ref and n is None and st.connected and st.inputs:
            engine.log("error", f"Transition : input « {ref} » introuvable dans vMix")
            return
        _transition(vm, a.get("effect"), ref, n, a, mix)
    elif t == "vmix.take":
        # Take : 1er appui = preview, 2e appui = antenne. La décision lit l'état « optimiste » (vmix.expect) :
        # un 2e appui très rapide, avant la réponse de vMix, prend bien l'input.
        if n is None:  # inputs vMix pas encore lus : on ne peut que le mettre en preview
            if st.connected:
                engine.log("warn", f"Take : état de « {ref} » inconnu, envoyé en preview seulement")
            vm.function("PreviewInput", Input=vm.input_param(ref), Mix=_mix(a))
        elif st.program(mix) == n:
            engine.log("info", f"Take : « {ref} » est déjà à l'antenne")
        elif st.preview(mix) == n:
            _transition(vm, a.get("effect"), ref, n, a, mix)
        elif vm.function("PreviewInput", Input=vm.input_param(ref), Mix=_mix(a)):
            vm.expect(mix, preview=n)
    elif t == "vmix.overlay":
        o, mode = int(a.get("overlay") or 1), a.get("mode", "toggle")
        name = {"toggle": f"OverlayInput{o}", "in": f"OverlayInput{o}In", "out": f"OverlayInput{o}Out",
                "off": f"OverlayInput{o}Off", "preview": f"PreviewOverlayInput{o}"}.get(mode, f"OverlayInput{o}")
        vm.function(name, Input=None if mode == "off" else vm.input_param(ref))
    elif t == "vmix.audio":
        name = {"toggle": "Audio", "off": "AudioOff", "on": "AudioOn"}.get(a.get("mode", "toggle"), "Audio")
        vm.function(name, Input=vm.input_param(ref))
    elif t == "vmix.production":
        what, mode = a.get("what"), a.get("mode", "toggle")
        if what == "fadetoblack" and mode in ("start", "stop"):
            # FadeToBlack est un interrupteur dans vMix : Démarrer / Arrêter n'envoient que si l'état change
            if not st.connected:
                engine.log("warn", "FTB : état de vMix inconnu, rien n'est envoyé")
                return
            if bool(st.flags.get("fadetoblack")) == (mode == "start"):
                engine.log("info", "FTB : déjà " + ("au noir" if mode == "start" else "à l'image"))
                return
        funcs = PRODUCTION_FUNCS.get(what, PRODUCTION_FUNCS["recording"])
        vm.function(funcs[{"toggle": 0, "start": 1, "stop": 2}.get(mode, 0)])
    elif t == "vmix.function":
        params = (a.get("params") or "").strip().lstrip("&?")
        vm.raw(f"{a.get('function', '').strip()}&{params}" if params else a.get("function", ""))
    elif t == "intercom.ptt":
        _ptt_press(engine, a)
    elif t == "net.send":
        proto, host = (a.get("proto") or "tcp").upper(), a.get("host") or ""
        data = netsend.decode_payload(a.get("payload", ""), bool(a.get("hex")))
        try:
            netsend.send(proto, host, a.get("port", 0), data)
        except OSError as e:
            engine.log("error", f"{proto} {host or '?'}:{a.get('port')} : {error_text(e, host)}")
        else:
            engine.log("info", f"{proto} {host}:{a.get('port')} ← {len(data)} octet(s)")
    elif t == "osc":
        host = a.get("host") or ""
        msg = netsend.osc_message(a.get("address", ""), netsend.parse_osc_args(a.get("args", "")))
        try:
            netsend.send("udp", host, a.get("port", 0), msg)
        except OSError as e:
            engine.log("error", f"OSC {host or '?'}:{a.get('port')} : {error_text(e, host)}")
    elif t == "orchestream.overlay":
        engine.orchestream.command(a.get("overlay", "twizz"), a.get("mode", "toggle"))
    elif t == "orchestream.command":
        cid = a.get("command") or "game.advance"
        arg = ORCH_COMMAND_ARG.get(cid)
        engine.orchdeck.run(cid, {arg: a.get("arg", "")} if arg else None)
    elif t == "tally.reconnect":
        engine.tallies.reconnect(a.get("target", "all"))
    elif t == "multiview.control":
        engine.multiview.command(a.get("target", "sender"), a.get("ref", ""), a.get("mode", "toggle"))
    elif t == "multiview.volume":
        engine.multiview.set_volume(a.get("value", 50))
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
    elif t == "vmix.adjust":
        adjust.vmix_adjust(engine, a, int(ctx.get("delta") or 1))
    elif t == "win.audio":
        adjust.win_audio(engine, a, int(ctx.get("delta") or 1))
    elif t == "win.media":
        hotkeys.tap(a.get("what") or "playpause")
    elif t == "win.launch":
        try:
            winsys.launch(a.get("target", ""), a.get("args", ""))
        except (OSError, ValueError) as e:
            engine.log("error", f"Ouvrir « {a.get('target') or '?'} » : {e}")
    elif t == "win.focus":
        if not winsys.focus_window(a.get("title", "")):
            engine.log("warn", f"Aucune fenêtre ouverte ne contient « {a.get('title') or '?'} »")
    elif t == "win.run":
        try:
            winsys.run(a.get("command", ""), bool(a.get("hidden", True)))
        except (OSError, ValueError) as e:
            engine.log("error", f"Commande : {e}")
    elif t == "win.type":
        winsys.type_text(a.get("text", ""))
    elif t == "win.scroll":
        n = int(float(a.get("amount") or 1) * int(ctx.get("delta") or 1))
        # vers le haut / la droite pour une rotation horaire, comme une molette de souris
        winsys.scroll(n, 0) if a.get("direction") == "horizontal" else winsys.scroll(0, n)
    elif t == "win.lock":
        winsys.lock()


def _ptt_press(engine, a: dict) -> None:
    """Intercom : commande HTTP ; si py-intercom ne répond pas et qu'un raccourci de secours est réglé, on
    simule ce raccourci (maintenu jusqu'au relâché en mode Maintenu, appui bref en mode Basculer)."""
    ic = engine.intercom
    mode, bus = a.get("mode", "hold"), a.get("bus") or "regie"
    if mode == "alloff":
        ic.command("all", "off")
        return
    keys = (a.get("fallback_keys") or "").strip() if mode in ("hold", "toggle") else ""
    cmd = {"hold": "on"}.get(mode, mode)
    if not keys:
        ic.command(bus, cmd)
        return
    # déjà connu injoignable : le raccourci part tout de suite, sans attendre un délai réseau
    tried = ic.reachable is not False
    if tried:
        if ic.command(bus, cmd, quick=True, report=False):
            return
        kind, level, msg = ic.last
        if kind not in UNREACHABLE:  # client désarmé, bus absent… : le raccourci n'y changerait rien
            if msg:
                engine.log(level or "error", msg)
            return
    if mode == "hold":
        hotkeys.press(keys)
        ic.fallback_held.add((bus, keys))
        if tried:  # un client lent a pu recevoir le « on » quand même : le relâché enverra aussi « off »
            ic.fallback_tried.add((bus, keys))
    else:
        hotkeys.tap(keys)
    engine.log("warn", f"py-intercom ne répond pas : raccourci de secours « {keys} » utilisé ({bus_label(bus)})")


def _ptt_release(engine, a: dict) -> None:
    ic = engine.intercom
    bus, keys = a.get("bus") or "regie", (a.get("fallback_keys") or "").strip()
    if keys and (bus, keys) in ic.fallback_held:
        ic.fallback_held.discard((bus, keys))
        hotkeys.release(keys)
        if (bus, keys) in ic.fallback_tried:  # « on » peut-être appliqué en retard : on le referme aussi
            ic.fallback_tried.discard((bus, keys))
            ic.command(bus, "off", quick=True, report=False)  # en cas d'échec, surveillé par _reconcile
        return
    if ic.bus(bus).get("mode") == "always_on":  # bus toujours ouvert : rien à refermer
        return
    ic.command(bus, "off")


def next_press(engine, b: dict) -> str:
    """Ce que fera le prochain appui, d'après l'état en direct (inspecteur, mode Live). "" si rien à dire."""
    if not b:
        return ""
    if b.get("mode") == "toggle":
        return ("désactiver (actions « À la désactivation »)" if engine.toggles.get(b.get("id"))
                else "activer (actions « À l'activation »)")
    acts = b.get("actions") or []
    a = acts[0] if acts else {}
    t = a.get("type")
    if t not in ("vmix.take", "vmix.preview"):
        return ""
    ref, mix = str(a.get("input") or "").strip(), int(a.get("mix") or 1)
    st = engine.vmix.state
    if not ref:
        return "aucun input choisi : rien ne part"
    if not st.connected:
        return "vMix hors ligne"
    n = st.number(ref)
    if n is None:
        return "input introuvable dans vMix : rien ne part" if st.inputs else "état de vMix en cours de lecture…"
    pgm, pvw = st.program(mix), st.preview(mix)
    if t == "vmix.take":
        if pgm == n:
            return "rien : l'input est déjà à l'antenne"
        if pvw == n:
            return f"À L'ANTENNE ({dict(TRANSITION_CHOICES).get(a.get('effect'), a.get('effect') or 'Cut')})"
        return "mettre en preview (l'appui suivant l'enverra à l'antenne)"
    then = a.get("then") or "none"
    if then != "none" and pvw == n and pgm != n:
        return f"À L'ANTENNE ({dict(TRANSITION_CHOICES).get(then, then)})"
    if pvw == n:
        return "rien : déjà en preview" + ("" if then != "none" else " (« 2e appui » non réglé)")
    return "mettre en preview"


def to_take(b: dict) -> dict | None:
    """Touche Preview / Program convertie en Take (même input, même mix), sinon None."""
    acts = (b or {}).get("actions") or []
    if b.get("mode") == "toggle" or not acts or acts[0].get("type") not in ("vmix.preview", "vmix.program"):
        return None
    old = acts[0]
    then = old.get("then") or "none"
    nb = copy.deepcopy(b)
    nb["actions"][0] = new_action("vmix.take", input=old.get("input", ""), mix=old.get("mix", 1),
                                  effect=then if then != "none" else "Cut", duration=old.get("duration", 0))
    if (b.get("bg") or "").lower() in (ACTIONS[old["type"]]["color"], "#003300", "#330000"):
        nb["bg"] = ACTIONS["vmix.take"]["color"]
    return nb


def summary(a: dict, page_names: dict | None = None) -> str:
    t = a.get("type")
    mix = f" (Mix {a['mix']})" if int(a.get("mix") or 1) > 1 else ""
    if t == "vmix.preview":
        then = a.get("then") or "none"
        return f"Preview · {a.get('input') or '?'}{mix}" + (f" (2e appui : {then})" if then != "none" else "")
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
        return f"Intercom · {bus_label(a.get('bus'))} ({mode})"
    if t == "orchestream.overlay":
        mode = dict(SHOW_MODES).get(a.get("mode"), "").split(" ")[0].lower()
        return f"Orchestream · {overlay_label(a.get('overlay'))} ({mode})"
    if t == "net.send":
        data = a.get("payload") or ""
        return (f"{(a.get('proto') or 'tcp').upper()} {a.get('host') or '?'}:{a.get('port')} · "
                f"{data[:24]}{'…' if len(data) > 24 else ''}").rstrip(" ·")
    if t == "osc":
        return f"OSC {a.get('host') or '?'}:{a.get('port')} {a.get('address') or '?'} {a.get('args') or ''}".strip()
    if t == "tally.reconnect":
        return "Tallys · reconnecter à vMix" + ("" if a.get("target", "all") == "all" else f" ({a['target']})")
    if t == "orchestream.command":
        cid = a.get("command") or "game.advance"
        what = ORCH_COMMAND_LABEL.get(cid, cid)
        if ORCH_COMMAND_ARG.get(cid) and a.get("arg"):
            what = f"{what} « {a['arg']} »"
        return f"orchestream · {what}"
    if t == "multiview.control":
        target = a.get("target", "sender")
        what = MULTIVIEW_TARGET_LABEL.get(target, target)
        if target in MULTIVIEW_NEEDS_REF and a.get("ref"):
            what = f"{what} « {a['ref']} »"
        mode = dict(ON_OFF_MODES).get(a.get("mode"), "").split(" ")[0].lower()
        return f"py-multiview · {what} ({mode})"
    if t == "multiview.volume":
        return f"py-multiview · retour audio à {a.get('value', 50)} %"
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
    if t == "vmix.adjust":
        tgt = a.get("target", "master")
        unit = VMIX_STEP.get(tgt, (1, ""))[1]
        what = {"input_volume": f"Volume {a.get('input') or '?'}", "master": "Volume Master",
                "headphones": "Volume casque", "bus": f"Volume bus {a.get('bus', 'A')}",
                "position": f"Lecture {a.get('input') or '?'}", "preview": f"Preview{mix}",
                "list": f"Liste {a.get('input') or '?'}",
                "countdown": f"Compte à rebours {a.get('input') or '?'}"}.get(tgt, tgt)
        return f"{what} · {_signed(a.get('amount'))} {unit} par cran".strip()
    if t == "win.audio":
        who = {"output": "Son Windows", "mic": "Micro"}.get(a.get("target"), a.get("app") or "Application")
        mode = a.get("mode", "adjust")
        if mode == "adjust":
            return f"{who} · {_signed(a.get('amount'))} % par cran"
        if mode == "set":
            return f"{who} · {a.get('level')} %"
        return f"{who} · {dict(WIN_MODES).get(mode, mode).lower()}"
    if t == "win.media":
        return f"Média · {dict(MEDIA).get(a.get('what'), '?').lower()}"
    if t == "win.launch":
        return f"Ouvrir · {a.get('target') or '?'}"
    if t == "win.focus":
        return f"Fenêtre · {a.get('title') or '?'}"
    if t == "win.run":
        return f"Commande · {a.get('command') or '?'}"
    if t == "win.type":
        text = (a.get("text") or "").replace("\n", " ")
        return f"Taper · {text[:24]}{'…' if len(text) > 24 else ''}"
    if t == "win.scroll":
        return f"Défilement {'horizontal' if a.get('direction') == 'horizontal' else 'vertical'} · " \
               f"{_signed(a.get('amount'))} par cran"
    if t == "win.lock":
        return "Verrouiller Windows"
    return t or "?"


def _signed(v) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "?"
    return f"{'+' if f > 0 else ''}{f:g}"
