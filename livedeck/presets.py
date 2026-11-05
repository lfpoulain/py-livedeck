"""Boutons prêts à l'emploi : tuiles des touches vides et bibliothèque à glisser."""
from __future__ import annotations

from . import feedback
from .actions import ACTIONS, new_action
from .integrations import bus_label, overlay_label
from .model import new_button


def input_button(title: str, kind: str) -> dict:
    """Bouton lié à un input vMix. kind : program | preview | take | overlay1..8 | audio."""
    if kind == "program":
        return new_button(label=title, bg="#330000", actions=[new_action("vmix.program", input=title)])
    if kind == "preview":
        return new_button(label=title, bg="#003300", actions=[new_action("vmix.preview", input=title)])
    if kind == "take":
        return new_button(label=title, bg=ACTIONS["vmix.take"]["color"],
                          actions=[new_action("vmix.take", input=title)])
    if kind == "audio":
        return new_button(label=title, bg=ACTIONS["vmix.audio"]["color"],
                          actions=[new_action("vmix.audio", input=title)])
    n = int(kind[-1])
    return new_button(label=f"PIP {title}" if n == 2 else title, bg="#333300",
                      actions=[new_action("vmix.overlay", input=title, overlay=n)])


def _toggle_http() -> dict:
    return new_button(label="Overlay", bg="#333300", mode="toggle",
                      actions=[new_action("http", method="POST", body="{}")],
                      actions_off=[new_action("http", method="POST", body="{}")])


def _health() -> dict:
    targets = "Équipement 1 = 192.168.0.10"
    return new_button(label="Réseau", bg="#000000", feedbacks=[
        feedback.fb("health", "#237423", "#ffffff", targets=targets, state="ok"),
        feedback.fb("health", "#cc0000", "#ffffff", targets=targets, state="ko")])


def _talk(bus: str, name: str = "") -> dict:
    return new_button(label=f"Intercom\n{name or bus_label(bus)}", bg="#0b1d3a",
                      actions=[new_action("intercom.ptt", bus=bus)])


def _orch(overlay: str, name: str = "") -> dict:
    return new_button(label=name or overlay_label(overlay), bg="#333300",
                      actions=[new_action("orchestream.overlay", overlay=overlay)])


def _mv(target: str, label: str, ref: str = "", bg: str = "#0f2a24") -> dict:
    return new_button(label=label, bg=bg,
                      actions=[new_action("multiview.control", target=target, ref=ref)])


def _json_value() -> dict:
    return new_button(label="API", bg="#172554", feedbacks=[
        feedback.fb("http_json", url="", path="", op="always", value="", interval=5, label_fmt="Valeur\\n{value}"),
        feedback.fb("http_json", "#cc0000", "#ffffff", url="", op="error", interval=5)])


def _prod(label: str, what: str) -> dict:
    return new_button(label=label, bg="#330000", actions=[new_action("vmix.production", what=what)])


def _knob(label: str, turn: dict, press: dict | None = None, bg: str = "#0b2e2a") -> dict:
    """Réglage pour molette : la rotation (une seule liste, valable dans les deux sens) et un appui.
    Posé sur une touche ordinaire, c'est l'appui qui sert, et la jauge s'affiche quand même."""
    return new_button(label=label, bg=bg, kind="rotary", actions_cw=[turn], actions_ccw=[], step=1,
                      actions=[press] if press else [])


def _vol(target: str, label: str, mute: str, **kw) -> dict:
    return _knob(label, new_action("vmix.adjust", target=target, amount=2, **kw),
                 new_action("vmix.function", function=mute) if mute else None)


def _win_vol(target: str, label: str, **kw) -> dict:
    return _knob(label, new_action("win.audio", target=target, mode="adjust", amount=2, **kw),
                 new_action("win.audio", target=target, mode="toggle", **kw), bg="#1e1b4b")


# id : (catégorie, nom, description, fabrique)
PRESETS: dict[str, tuple] = {
    "take": ("Vidéo", "Take", "1er appui preview, 2e antenne", lambda: input_button("", "take")),
    "program": ("Vidéo", "Program", "Direct à l'antenne", lambda: input_button("", "program")),
    "preview": ("Vidéo", "Preview", "Input en preview", lambda: input_button("", "preview")),
    "cut": ("Vidéo", "CUT", "Preview → antenne", lambda: new_button(
        label="CUT", bg="#ff8000", actions=[new_action("vmix.transition", effect="Cut")])),
    "fade": ("Vidéo", "Fade", "Fondu preview → antenne", lambda: new_button(
        label="FADE", bg="#ff8000", actions=[new_action("vmix.transition", effect="Fade")])),
    "stinger1": ("Vidéo", "Stinger 1", "Transition stinger", lambda: new_button(
        label="Stinger", bg="#ff8000", actions=[new_action("vmix.transition", effect="Stinger1")])),
    "stinger2": ("Vidéo", "Stinger 2", "Transition stinger", lambda: new_button(
        label="Stinger 2", bg="#ff8000", actions=[new_action("vmix.transition", effect="Stinger2")])),
    "overlay": ("Vidéo", "Overlay / PIP", "Afficher / masquer", lambda: input_button("", "overlay2")),
    "overlays_off": ("Vidéo", "Couper les overlays", "Tous les overlays", lambda: new_button(
        label="Overlays OFF", bg="#333300", actions=[new_action("vmix.function", function="OverlayInputAllOff")])),
    "ftb": ("Production", "FTB", "Fondu au noir", lambda: _prod("FTB", "fadetoblack")),
    "rec": ("Production", "REC", "Enregistrement vMix", lambda: _prod("REC", "recording")),
    "stream": ("Production", "STREAM", "Stream vMix", lambda: _prod("STREAM", "streaming")),
    "external": ("Production", "EXTERNAL", "Sortie externe", lambda: _prod("EXT", "external")),
    "multicorder": ("Production", "MULTICORDER", "Enregistrement multi", lambda: _prod("MULTI\nCORDER", "multicorder")),
    "snapshot": ("Production", "Snapshot", "Capture de l'antenne", lambda: new_button(
        label="SNAP", bg="#1f2937", actions=[new_action("vmix.production", what="snapshot")])),
    "audio": ("Audio", "Couper un son", "Micro, musique…", lambda: input_button("", "audio")),
    "talkback": ("Clavier", "Raccourci maintenu", "PTT clavier d'un autre logiciel", lambda: new_button(
        label="PTT", bg="#000033", actions=[new_action("hotkey", hold=True)])),
    "hotkey": ("Clavier", "Raccourci clavier", "Une combinaison de touches", lambda: new_button(
        bg="#000033", actions=[new_action("hotkey")])),
    "http": ("Web", "HTTP / curl", "API, webhook", lambda: new_button(
        bg="#172554", actions=[new_action("http")])),
    "toggle_http": ("Web", "Interrupteur HTTP", "Show / hide mémorisé", _toggle_http),
    "http_json": ("Web", "Valeur d'une API", "Affiche / colore selon du JSON", _json_value),
    "net_tcp": ("Réseau", "Commande TCP / UDP", "Trame vers un appareil", lambda: new_button(
        label="TCP", bg=ACTIONS["net.send"]["color"], actions=[new_action("net.send")])),
    "osc": ("Réseau", "Message OSC", "QLab, Reaper, X32…", lambda: new_button(
        label="OSC", bg=ACTIONS["osc"]["color"], actions=[new_action("osc")])),
    "page_next": ("Deck", "Page suivante", "Navigation du deck", lambda: new_button(
        label="Page ▶", bg="#262626", actions=[new_action("page", page="next")])),
    "page_prev": ("Deck", "Page précédente", "Navigation du deck", lambda: new_button(
        label="◀ Page", bg="#262626", actions=[new_action("page", page="prev")])),
    "health": ("Deck", "Surveillance réseau", "Vert si tout répond", _health),
    "label": ("Deck", "Texte seul", "Étiquette, séparateur", lambda: new_button(label="Texte", bg="#1f2937")),
    "function": ("vMix avancé", "Fonction vMix", "Toute l'API vMix", lambda: new_button(
        bg="#1e293b", actions=[new_action("vmix.function")])),
    "talk": ("Intégrations", "Talkback", "py-intercom, maintenu", lambda: _talk("regie")),
    "orch": ("Intégrations", "Overlay web", "orchestream, état en direct", lambda: _orch("twizz")),
    "talk_regie": ("Intégrations", "Talkback Régie", "py-intercom, maintenu", lambda: _talk("regie", "Régie")),
    "talk_plateau": ("Intégrations", "Talkback Plateau", "py-intercom, maintenu", lambda: _talk("plateau", "Plateau")),
    "talk_vmix": ("Intégrations", "Talkback vMix", "py-intercom, maintenu", lambda: _talk("vmix", "vMix")),
    "talk_alloff": ("Intégrations", "Couper l'intercom", "Relâche tous les bus", lambda: new_button(
        label="Intercom\nOFF", bg="#1a1d24", actions=[new_action("intercom.ptt", mode="alloff")])),
    "ov_twizz": ("Intégrations", "Overlay Twizz", "orchestream, état en direct", lambda: _orch("twizz", "Twizz")),
    "ov_poll": ("Intégrations", "Overlay Sondage", "orchestream, état en direct", lambda: _orch("poll", "Poll")),
    "ov_speedizz": ("Intégrations", "Overlay Speedizz", "orchestream, état en direct", lambda: _orch("speedizz", "Speedizz")),
    "tallies": ("Intégrations", "Tallys", "Vert si tous connectés, reconnecte", lambda: new_button(
        label="Tallys", bg="#000000", actions=[new_action("tally.reconnect")])),
    "mv_sender": ("Intégrations", "Émission OMT", "py-multiview, état en direct",
                  lambda: _mv("sender", "Émission\nOMT")),
    "mv_reception": ("Intégrations", "Réception multiview", "py-multiview, tous les flux",
                     lambda: _mv("reception", "Réception")),
    "mv_monitor": ("Intégrations", "Retour audio", "py-multiview, écoute du master",
                   lambda: _mv("monitor", "Retour\naudio")),
    "mv_floating": ("Intégrations", "Fenêtre de retour", "py-multiview, fenêtre flottante",
                    lambda: _mv("floating", "Fenêtre\nretour")),
    "mv_stream": ("Intégrations", "Un flux multiview", "py-multiview, à préciser",
                  lambda: _mv("stream", "Flux 1", "1")),
    # -- molettes : la rotation règle, l'appui coupe ----------------------------------
    "knob_master": ("Molette", "Volume Master", "Tourner : volume · appuyer : couper",
                    lambda: _vol("master", "Master", "MasterAudio")),
    "knob_input": ("Molette", "Volume d'un input", "Tourner : volume · appuyer : couper",
                   lambda: _knob("Volume", new_action("vmix.adjust", target="input_volume", amount=2),
                                 new_action("vmix.audio", mode="toggle"))),
    "knob_headphones": ("Molette", "Volume du casque", "Retour casque de vMix",
                        lambda: _vol("headphones", "Casque", "")),
    "knob_bus": ("Molette", "Volume d'un bus", "Tourner : volume · appuyer : couper le bus",
                 lambda: _knob("Bus A", new_action("vmix.adjust", target="bus", bus="A", amount=2),
                               new_action("vmix.function", function="BusXAudio", params="Value=A"))),
    "knob_preview": ("Molette", "Parcourir la preview", "Tourner : input suivant · appuyer : CUT",
                     lambda: _knob("Preview", new_action("vmix.adjust", target="preview", amount=1),
                                   new_action("vmix.transition", effect="Cut"), bg="#003300")),
    "knob_scrub": ("Molette", "Avance / retour vidéo", "1 s par cran · appuyer : lecture / pause",
                   lambda: _knob("Lecture", new_action("vmix.adjust", target="position", amount=1),
                                 new_action("vmix.function", function="PlayPause"), bg="#1e293b")),
    "knob_list": ("Molette", "Photos / liste / PowerPoint", "Élément suivant ou précédent",
                  lambda: _knob("Liste", new_action("vmix.adjust", target="list", amount=1), bg="#1e293b")),
    "knob_countdown": ("Molette", "Régler un compte à rebours", "±10 s par cran · appuyer : départ",
                       lambda: _knob("Compte à\nrebours", new_action("vmix.adjust", target="countdown", amount=10),
                                     new_action("vmix.function", function="StartCountdown"), bg="#1e293b")),
    "knob_win": ("Molette", "Volume de Windows", "Tourner : volume · appuyer : couper",
                 lambda: _win_vol("output", "Windows")),
    "knob_app": ("Molette", "Volume d'une application", "Spotify, navigateur… indépendamment",
                 lambda: _win_vol("app", "Appli")),
    "knob_scroll": ("Molette", "Molette de souris", "Défiler la fenêtre sous la souris",
                    lambda: _knob("Défiler", new_action("win.scroll", amount=1), bg="#1e1b4b")),
    # -- Windows -----------------------------------------------------------------------
    "win_mic": ("Windows", "Couper le micro", "Micro par défaut, rouge quand coupé", lambda: new_button(
        label="Micro", bg="#1e1b4b", actions=[new_action("win.audio", target="mic", mode="toggle")])),
    "win_mute": ("Windows", "Couper le son", "Son de Windows, rouge quand coupé", lambda: new_button(
        label="Son", bg="#1e1b4b", actions=[new_action("win.audio", target="output", mode="toggle")])),
    "win_vol_up": ("Windows", "Volume +", "Son de Windows", lambda: new_button(
        label="Vol +", bg="#1e1b4b", actions=[new_action("win.audio", target="output", mode="adjust", amount=5)])),
    "win_vol_down": ("Windows", "Volume −", "Son de Windows", lambda: new_button(
        label="Vol −", bg="#1e1b4b", actions=[new_action("win.audio", target="output", mode="adjust", amount=-5)])),
    "win_play": ("Windows", "Lecture / pause", "Lecteur actif (Spotify…)", lambda: new_button(
        label="▶ ❚❚", bg="#1e1b4b", actions=[new_action("win.media", what="playpause")])),
    "win_next": ("Windows", "Piste suivante", "Lecteur actif", lambda: new_button(
        label="▶▶", bg="#1e1b4b", actions=[new_action("win.media", what="next")])),
    "win_launch": ("Windows", "Ouvrir un programme", "Logiciel, fichier, dossier ou site", lambda: new_button(
        label="Ouvrir", bg="#1e1b4b", actions=[new_action("win.launch")])),
    "win_focus": ("Windows", "Fenêtre au premier plan", "Puis un raccourci dans ce logiciel", lambda: new_button(
        label="vMix", bg="#1e1b4b", actions=[new_action("win.focus", title="vMix")])),
    "win_run": ("Windows", "Commande", "Script, .bat, commande", lambda: new_button(
        label="CMD", bg="#1e1b4b", actions=[new_action("win.run")])),
    "win_type": ("Windows", "Taper un texte", "Signature, nom d'invité…", lambda: new_button(
        label="Texte", bg="#1e1b4b", actions=[new_action("win.type")])),
    "win_lock": ("Windows", "Verrouiller Windows", "Maintenir ½ s, sécurisé", lambda: new_button(
        label="Verrou", bg="#1e1b4b", confirm=True, actions=[new_action("win.lock")])),
    "ftb_safe": ("Production", "FTB sécurisé", "Maintenir ½ s pour noircir", lambda: new_button(
        label="FTB", bg="#330000", confirm=True, actions=[new_action("vmix.production", what="fadetoblack")])),
    "stream_stop": ("Production", "Arrêt du stream", "Maintenir ½ s, sécurisé", lambda: new_button(
        label="STOP\nSTREAM", bg="#330000", confirm=True,
        actions=[new_action("vmix.production", what="streaming", mode="stop")])),
}

# presets qui attendent un input vMix : l'inspecteur ouvre tout de suite le choix de l'input
NEEDS_INPUT = ("program", "preview", "take", "overlay", "audio")
ROTARY_FIRST = ["Molette"]  # sur une molette, ces réglages passent en tête du choix


def slots(b: dict) -> tuple[list[tuple[str, str]], str]:
    """Où poser ce bouton sur une molette : ([(emplacement, libellé)], emplacement recommandé).

    La liste est TOUJOURS la même, quel que soit le contrôle et quelle que soit l'action : seule
    la recommandation change. Une liste qui varie d'une molette à l'autre donne l'impression que
    les contrôles n'ont pas les mêmes possibilités, alors qu'ils en ont exactement les mêmes.

    Liste vide = rien à répartir (texte seul, surveillance réseau…) : pas de question à poser."""
    from .actions import is_relative
    turn, press = b.get("actions_cw") or [], b.get("actions") or []
    if not turn and not press:
        return [], "press"
    both = "Tourner pour régler, appuyer pour l'autre action"
    choices = [("press", "À l'appui"), ("cw", "À la rotation ⟳ (sens horaire)"),
               ("ccw", "À la rotation ⟲ (sens antihoraire)")]
    if turn and press:  # réglage pensé pour une molette : il a déjà les deux
        return [("both", both)] + choices, "both"
    if turn:            # réglage sans appui (molette de souris) : la rotation d'abord
        return choices, "cw"
    # Un réglage au cran suit le sens de rotation : le poser d'un seul côté suffit pour les deux.
    return choices, ("cw" if all(is_relative(a) for a in press) else "press")


SLOT_SIDE = {"press": "actions", "both": "actions_cw", "turn": "actions_cw", "cw": "actions_cw",
             "ccw": "actions_ccw"}


def assign(b: dict, slot: str) -> dict:
    """Range les actions du bouton dans l'emplacement choisi (appui, rotation, sens)."""
    import copy as _copy
    b = _copy.deepcopy(b)
    turn = b.pop("actions_cw", None) or []
    b.pop("actions_ccw", None)
    press = b.get("actions") or []
    cw, ccw = [], []
    if turn:
        if slot == "press":
            b["actions"] = press
        elif slot == "turn":
            b["actions"], cw = [], turn
        else:
            cw = turn
    elif slot in ("turn", "cw", "ccw"):
        cw, ccw = (press, []) if slot != "ccw" else ([], press)
        b["actions"] = []
        # Une rotation n'a ni « relâché » ni second appui : interrupteur et appui long n'ont plus de sens.
        b["mode"], b["actions_off"], b["long_actions"] = "press", [], []
    b["actions_cw"], b["actions_ccw"] = cw, ccw
    return b


def place(b: dict, k: str) -> dict:
    """Adapte un bouton à l'endroit où on le pose.

    Une molette garde ses listes de rotation. Une touche de l'écran ou un bouton physique ne tourne
    pas : ses listes de rotation sont RETIRÉES, sinon il garderait une action qui ne partira jamais
    et l'éditeur comme l'info-bulle annonceraient une rotation impossible."""
    from .loupedeck import control_kind
    from .model import normalize_control, parse_control
    cid = parse_control(k)
    rotary = cid is not None and control_kind(cid) == "rotary"
    if not rotary:
        turn = (b.get("actions_cw") or []) + (b.get("actions_ccw") or [])
        b = {**b, "actions_cw": [], "actions_ccw": []}
        if turn and not (b.get("actions") or []):
            # Réglage sans action d'appui (molette de souris, volume…) : sa rotation devient
            # l'appui — un cran par appui — plutôt que de disparaître sans rien dire.
            b["actions"] = turn
    if cid is not None:
        return normalize_control({**b, "kind": control_kind(cid)})
    b = dict(b)
    for key_ in ("kind", "actions_cw", "actions_ccw", "step", "haptic"):
        b.pop(key_, None)  # une touche de la grille n'a pas ces réglages
    return b


def make(preset_id: str, engine=None) -> dict | None:
    """Crée le bouton d'un preset. « page:<id> » = aller directement à cette page."""
    if preset_id.startswith("page:") and engine:
        page = engine.page(preset_id[5:])
        if page:
            return new_button(label=page["name"], bg="#262626",
                              actions=[new_action("page", page=page["id"])])
        return None
    if preset_id.startswith("talk:"):
        return _talk(preset_id[5:])
    if preset_id.startswith("orch:"):
        return _orch(preset_id[5:], engine.orchestream.labels.get(preset_id[5:], "") if engine else "")
    if preset_id.startswith("mv:"):
        return _mv("stream", preset_id[3:], preset_id[3:])
    entry = PRESETS.get(preset_id)
    return entry[3]() if entry else None


def _integrations(engine) -> list[tuple[str, str, str]]:
    """Intégrations d'après ce qui est réglé et découvert : bus proposés par py-intercom, overlays réglés ou
    vus en direct ; un service non réglé n'apparaît qu'une fois, marqué « à régler »."""
    integ = engine.cfg.get("integrations", {})
    modes = {"toggle": "py-intercom, bascule", "always_on": "py-intercom, toujours ouvert"}
    out = [(f"talk:{k}", f"Talkback {name}", modes.get(engine.intercom.bus(k).get("mode"), "py-intercom, maintenu"))
           for k, name in engine.intercom.bus_choices()]
    out.append(("talk_alloff", "Couper l'intercom", "Relâche tous les bus"))
    if (integ.get("orchestream") or {}).get("url"):
        out += [(f"orch:{k}", f"Overlay {name}", "orchestream, état en direct") for k, name in engine.orchestream.names()]
    else:
        out.append(("orch", "Overlay orchestream", "à régler : Réglages → Intégrations"))
    out.append(("tallies", "Tallys", "Vert si tous connectés, reconnecte" if integ.get("tallies")
                else "Tally32 à régler : Réglages → Intégrations"))
    if (integ.get("multiview") or {}).get("url"):
        out += [("mv_sender", "Émission OMT", "py-multiview, état en direct"),
                ("mv_reception", "Réception multiview", "py-multiview, tous les flux"),
                ("mv_monitor", "Retour audio", "py-multiview, écoute du master"),
                ("mv_floating", "Fenêtre de retour", "py-multiview, fenêtre flottante")]
        out += [(f"mv:{name}", f"Flux {name}", "py-multiview, état en direct")
                for name, _ in engine.multiview.choices("stream")]
    else:
        out.append(("mv_sender", "py-multiview", "à régler : Réglages → Intégrations"))
    return out


def needs_setup(preset_id: str, engine) -> str:
    """Intégration à régler (ou injoignable) avant que ce preset serve : « intercom », « orchestream »,
    « tallies », sinon "". py-intercom n'a rien à régler par défaut (même PC, port 5080) : signalé seulement
    s'il s'est déjà montré injoignable."""
    if engine is None:
        return ""
    integ = engine.cfg.get("integrations", {})
    if preset_id.startswith("talk") and preset_id != "talkback":
        if engine.intercom.reachable is False:
            return "intercom"
        bus = preset_id[5:] if preset_id.startswith(("talk:", "talk_")) else ""
        return "always_on" if bus and engine.intercom.bus(bus).get("mode") == "always_on" else ""
    if preset_id == "orch" or preset_id.startswith(("orch:", "ov_")):
        return "" if (integ.get("orchestream") or {}).get("url") else "orchestream"
    if preset_id == "tallies":
        return "" if integ.get("tallies") else "tallies"
    if preset_id.startswith("mv"):
        return "" if (integ.get("multiview") or {}).get("url") else "multiview"
    return ""


PICKER_ORDER = ["Vidéo", "Intégrations", "Production", "Audio", "Molette", "Windows", "Clavier", "Web", "Réseau",
                "Deck", "vMix avancé"]


def catalog(engine, rotary: bool = False) -> list[tuple[str, list[tuple[str, str, str, str]]]]:
    """Tout ce qu'une touche vide peut devenir, par catégorie, intégrations comprises :
    [(catégorie, [(id, nom, description, intégration à régler ou "")])].
    rotary : on choisit pour une molette — les réglages au cran passent en tête."""
    groups = {cat: list(items) for cat, items in library(engine, with_inputs=True)}
    groups["Deck"] = [it for it in groups.get("Deck", []) if not it[0].startswith("page:")]  # pages : bibliothèque
    order = ROTARY_FIRST + [c for c in PICKER_ORDER if c not in ROTARY_FIRST] if rotary else PICKER_ORDER
    return [(cat, [(pid, name, desc, needs_setup(pid, engine)) for pid, name, desc in groups[cat]])
            for cat in order if groups.get(cat)]


def library(engine, with_inputs: bool = False) -> list[tuple[str, list[tuple[str, str, str]]]]:
    """Bibliothèque regroupée par catégorie : [(catégorie, [(id, nom, description)])]."""
    groups: dict[str, list] = {}
    for pid, (cat, name, desc, _) in PRESETS.items():
        if pid in NEEDS_INPUT and not with_inputs:
            continue  # ceux-là se créent en glissant un input
        if cat == "Intégrations":
            continue  # liste construite d'après les services réglés
        groups.setdefault(cat, []).append((pid, name, desc))
    groups["Intégrations"] = _integrations(engine)
    groups["Deck"] = [(f"page:{p['id']}", f"→ {p['name']}", f"Afficher cette page ({p['cols']}×{p['rows']})")
                      for p in engine.pages] + groups.get("Deck", [])
    order = ["Vidéo", "Production", "Audio", "Molette", "Windows", "Intégrations", "Clavier", "Web", "Réseau", "Deck",
             "vMix avancé"]
    return [(cat, groups[cat]) for cat in order if cat in groups]
