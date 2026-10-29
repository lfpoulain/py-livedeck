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


def _json_value() -> dict:
    return new_button(label="API", bg="#172554", feedbacks=[
        feedback.fb("http_json", url="", path="", op="always", value="", interval=5, label_fmt="Valeur\\n{value}"),
        feedback.fb("http_json", "#cc0000", "#ffffff", url="", op="error", interval=5)])


def _prod(label: str, what: str) -> dict:
    return new_button(label=label, bg="#330000", actions=[new_action("vmix.production", what=what)])


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
    "ftb_safe": ("Production", "FTB sécurisé", "Maintenir ½ s pour noircir", lambda: new_button(
        label="FTB", bg="#330000", confirm=True, actions=[new_action("vmix.production", what="fadetoblack")])),
    "stream_stop": ("Production", "Arrêt du stream", "Maintenir ½ s, sécurisé", lambda: new_button(
        label="STOP\nSTREAM", bg="#330000", confirm=True,
        actions=[new_action("vmix.production", what="streaming", mode="stop")])),
}

# presets qui attendent un input vMix : l'inspecteur ouvre tout de suite le choix de l'input
NEEDS_INPUT = ("program", "preview", "take", "overlay", "audio")


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
    return ""


PICKER_ORDER = ["Vidéo", "Intégrations", "Production", "Audio", "Clavier", "Web", "Réseau", "Deck", "vMix avancé"]


def catalog(engine) -> list[tuple[str, list[tuple[str, str, str, str]]]]:
    """Tout ce qu'une touche vide peut devenir, par catégorie, intégrations comprises :
    [(catégorie, [(id, nom, description, intégration à régler ou "")])]."""
    groups = {cat: list(items) for cat, items in library(engine, with_inputs=True)}
    groups["Deck"] = [it for it in groups.get("Deck", []) if not it[0].startswith("page:")]  # pages : bibliothèque
    return [(cat, [(pid, name, desc, needs_setup(pid, engine)) for pid, name, desc in groups[cat]])
            for cat in PICKER_ORDER if groups.get(cat)]


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
    order = ["Vidéo", "Production", "Audio", "Intégrations", "Clavier", "Web", "Réseau", "Deck", "vMix avancé"]
    return [(cat, groups[cat]) for cat in order if cat in groups]
