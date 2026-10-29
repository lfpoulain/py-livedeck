"""Boutons prêts à l'emploi : tuiles des touches vides et bibliothèque à glisser."""
from __future__ import annotations

from . import feedback
from .actions import ACTIONS, new_action
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


def _talk(bus: str, name: str) -> dict:
    return new_button(label=f"Intercom\n{name}", bg="#0b1d3a", actions=[new_action("intercom.ptt", bus=bus)])


def _orch(overlay: str, name: str) -> dict:
    return new_button(label=name, bg="#333300", actions=[new_action("orchestream.overlay", overlay=overlay)])


def _prod(label: str, what: str) -> dict:
    return new_button(label=label, bg="#330000", actions=[new_action("vmix.production", what=what)])


# id : (catégorie, nom, description, fabrique)
PRESETS: dict[str, tuple] = {
    "program": ("Vidéo", "Program", "Direct à l'antenne", lambda: input_button("", "program")),
    "preview": ("Vidéo", "Preview", "Input en preview", lambda: input_button("", "preview")),
    "take": ("Vidéo", "Take", "Preview puis antenne", lambda: input_button("", "take")),
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
    "talkback": ("Clavier", "Talkback", "Raccourci maintenu", lambda: new_button(
        label="Talkback", bg="#000033", actions=[new_action("hotkey", hold=True)])),
    "hotkey": ("Clavier", "Raccourci clavier", "Une combinaison de touches", lambda: new_button(
        bg="#000033", actions=[new_action("hotkey")])),
    "http": ("Web", "HTTP / curl", "API, webhook", lambda: new_button(
        bg="#172554", actions=[new_action("http")])),
    "toggle_http": ("Web", "Interrupteur HTTP", "Show / hide mémorisé", _toggle_http),
    "page_next": ("Deck", "Page suivante", "Navigation du deck", lambda: new_button(
        label="Page ▶", bg="#262626", actions=[new_action("page", page="next")])),
    "page_prev": ("Deck", "Page précédente", "Navigation du deck", lambda: new_button(
        label="◀ Page", bg="#262626", actions=[new_action("page", page="prev")])),
    "health": ("Deck", "Surveillance réseau", "Vert si tout répond", _health),
    "label": ("Deck", "Texte seul", "Étiquette, séparateur", lambda: new_button(label="Texte", bg="#1f2937")),
    "function": ("vMix avancé", "Fonction vMix", "Toute l'API vMix", lambda: new_button(
        bg="#1e293b", actions=[new_action("vmix.function")])),
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
    "ftb_safe": ("Production", "FTB sécurisé", "Maintenir ½ s pour noircir", lambda: new_button(
        label="FTB", bg="#330000", confirm=True, actions=[new_action("vmix.production", what="fadetoblack")])),
    "stream_stop": ("Production", "Arrêt du stream", "Maintenir ½ s, sécurisé", lambda: new_button(
        label="STOP\nSTREAM", bg="#330000", confirm=True,
        actions=[new_action("vmix.production", what="streaming", mode="stop")])),
}

# tuiles proposées sur une touche vide, dans l'ordre
TILES = ["take", "program", "preview", "cut", "overlay", "audio", "rec", "talkback", "http", "toggle_http",
         "page_next", "health", "function"]


def make(preset_id: str, engine=None) -> dict | None:
    """Crée le bouton d'un preset. « page:<id> » = aller directement à cette page."""
    if preset_id.startswith("page:") and engine:
        page = engine.page(preset_id[5:])
        if page:
            return new_button(label=page["name"], bg="#262626",
                              actions=[new_action("page", page=page["id"])])
        return None
    entry = PRESETS.get(preset_id)
    return entry[3]() if entry else None


def library(engine) -> list[tuple[str, list[tuple[str, str, str]]]]:
    """Bibliothèque regroupée par catégorie : [(catégorie, [(id, nom, description)])]."""
    groups: dict[str, list] = {}
    for pid, (cat, name, desc, _) in PRESETS.items():
        if pid in ("program", "preview", "take", "overlay", "audio"):
            continue  # ceux-là se créent en glissant un input
        groups.setdefault(cat, []).append((pid, name, desc))
    groups["Deck"] = [(f"page:{p['id']}", f"→ {p['name']}", f"Afficher cette page ({p['cols']}×{p['rows']})")
                      for p in engine.pages] + groups.get("Deck", [])
    order = ["Vidéo", "Production", "Audio", "Intégrations", "Clavier", "Web", "Deck", "vMix avancé"]
    return [(cat, groups[cat]) for cat in order if cat in groups]
