"""Refait les captures d'écran de la documentation (docs/images), sans rien de réel.

    python tools/screenshots.py            # écrit dans docs/images
    python tools/screenshots.py dossier    # écrit ailleurs (pour comparer avant de remplacer)

Tout est simulé sur 127.0.0.1, comme dans les tests : faux vMix, faux Stream Deck XL, faux py-intercom,
orchestream, py-multiview, MixPanel, DMAIX, Tally32 et caméra ONVIF. La configuration est celle de démonstration
(tests/fixtures/demo.companionconfig), écrite dans un dossier temporaire : ni ta configuration, ni le registre,
ni le clavier ne sont touchés. L'interface est dessinée hors écran, à taille fixe.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
_fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
if _fonts.is_dir():
    os.environ.setdefault("QT_QPA_FONTDIR", str(_fonts))
ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

import fakes  # noqa: E402
from livedeck import autostart  # noqa: E402

autostart.is_enabled = lambda: False  # jamais le registre Windows
autostart.set_enabled = lambda on: None
KEYS = fakes.FakeKeys().install()  # jamais une vraie touche du clavier

from livedeck import winsys  # noqa: E402
from livedeck.actions import new_action  # noqa: E402
from livedeck.companion_import import import_companion  # noqa: E402
from livedeck.deck import DeckHandle, DeckManager  # noqa: E402
from livedeck.engine import Engine  # noqa: E402
from livedeck.model import new_button  # noqa: E402
from livedeck.ui import theme  # noqa: E402
from livedeck.ui.diagnostic import DiagnosticDialog  # noqa: E402
from livedeck.ui.main_window import MainWindow  # noqa: E402
from livedeck.ui.search import SearchDialog  # noqa: E402
from livedeck.ui.settings import SettingsDialog  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "demo.companionconfig"
PORT = 18700  # faux serveurs : PORT (vMix), puis un port chacun
SIZE = (1560, 900)


class MockDeck:
    """Ce que LiveDeck demande à un Stream Deck (bibliothèque python-elgato-streamdeck)."""

    def __init__(self):
        self.images = {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def key_image_format(self):
        return {"size": (96, 96), "format": "JPEG", "flip": (True, True), "rotation": 0}

    def set_key_image(self, i, data):
        self.images[i] = data

    def set_brightness(self, b):
        pass

    def id(self):
        return "mock-xl"

    def connected(self):
        return True


def pump(seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def wait(cond, timeout: float = 6.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


def build(tmp: Path):
    """Moteur de démonstration branché sur les faux serveurs, avec un faux Stream Deck XL."""
    fakes.serve_vmix(PORT)
    fakes.FakeIntercom(PORT + 1)
    fakes.FakeOrchDeck(PORT + 2)
    fakes.FakeTally(PORT + 3, True)
    fakes.FakeTally(PORT + 4, True)
    fakes.FakeMultiview(PORT + 5)
    fakes.FakeMixPanel(PORT + 6)
    fakes.FakeDmaix(PORT + 7)
    cam = fakes.FakeOnvif(PORT + 8)
    cfg, _ = import_companion(FIXTURE, integrations=True)
    cfg["vmix"] = {"host": "127.0.0.1", "port": PORT}
    cfg["integrations"] = {
        "intercom": {"host": "127.0.0.1", "port": PORT + 1},
        "orchestream": {"url": f"127.0.0.1:{PORT + 2}", "token": fakes.FakeOrchDeck.TOKEN},
        "tallies": [{"name": "Caméra 1", "host": f"127.0.0.1:{PORT + 3}"},
                    {"name": "Caméra 2", "host": f"127.0.0.1:{PORT + 4}"}],
        "multiview": {"url": f"127.0.0.1:{PORT + 5}"}, "mixpanel": {"url": f"127.0.0.1:{PORT + 6}"},
        "dmaix": {"url": f"127.0.0.1:{PORT + 7}"},
        "onvif": {"user": cam.USER, "password": cam.PASSWORD,
                  "cameras": [{"name": "Plateau", "host": f"127.0.0.1:{PORT + 8}"}]}}
    xl = next(p for p in cfg["pages"] if (p["cols"], p["rows"]) == (8, 4))
    # Une touche décrite par un outil (DMAIX) à la place de la dernière de la 3e rangée…
    free = [f"{r},{c}" for r in range(4) for c in range(8) if f"{r},{c}" not in xl["buttons"]]
    xl["buttons"]["2,7"] = new_button(label="", bg="#1f2937", actions=[
        new_action("deck.key", app="dmaix", key="scene.play:sc-2", title="Ambiance bleue")])
    # … et une touche à deux gestes : appui court (preview puis stinger), appui long (fondu au noir)
    xl["buttons"][free[0]] = new_button(label="Pause", bg="#7c2d12", actions=[
        new_action("vmix.preview", input="Waiting Screen"), new_action("vmix.transition", effect="Stinger1")],
        long_actions=[new_action("vmix.production", what="fadetoblack")])
    e = Engine(tmp / "config.json", enable_decks=False)
    e.winaudio = winsys.CachedAudio(fakes.FakeAudio())  # jamais le vrai son de la machine
    e.cfg = cfg
    dm = DeckManager(e)
    e.decks = dm
    deck = MockDeck()
    h = DeckHandle(deck, "DEMOXL0000001", "Stream Deck XL", 4, 8, 96)
    dm.handles[deck.id()] = h
    h.page_id = xl["id"]
    return e, xl, free[1:]


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "images"
    out.mkdir(parents=True, exist_ok=True)
    app.setStyleSheet(theme.QSS)
    with tempfile.TemporaryDirectory() as d:
        e, xl, free = build(Path(d))
        w = MainWindow(e)
        e.start()
        w.resize(*SIZE)
        w.show()
        ok = wait(lambda: e.vmix.state.connected and e.vmix.state.inputs and e.intercom.reachable
                  and e.deck("dmaix").has_keys and e.orchestream.has_keys)
        if not ok:
            sys.exit("Les faux serveurs n'ont pas répondu : captures annulées.")
        pump(1.5)

        def shot(name: str, widget=None) -> None:
            pump(0.6)
            (widget or w).grab().save(str(out / name))
            print("écrit", out / name)

        def first(pred):
            return next(k for k, b in sorted(xl["buttons"].items()) if pred(b))

        # 1. la surface : une touche Program sélectionnée, les inputs vMix à gauche
        w.select(first(lambda b: (b.get("actions") or [{}])[0].get("type") == "vmix.program"
                       and "Regie" in b.get("label", "")))
        shot("surface.png")
        # 2. les étapes d'une touche : appui court + appui long
        w.select(first(lambda b: b.get("long_actions")))
        shot("etapes.png")
        # 3. une touche vide : tout ce qu'elle peut devenir, une pastille par outil
        w.select(free[0])
        shot("touche-vide.png")
        # 4. l'onglet Outils : chaque outil, son état, ses touches par rubrique
        w.library.show_tools()
        w.select(first(lambda b: (b.get("actions") or [{}])[0].get("type") == "deck.key"))
        shot("outils.png")
        w.library.tabs.set("inputs")
        w.library.stack.setCurrentWidget(w.library.inputs)

        # 5. Réglages → Intégrations : un outil par page
        dlg = SettingsDialog(e, w, tab="integ", tool="orchestream")
        dlg.show()
        wait(lambda: e.deck("mixpanel").has_keys and e.deck("multiview").has_keys
             and all(c.reachable for c in e.onvif.cameras) and e.tallies.summary("all")[0], 8)
        pump(1.2)
        shot("integrations.png", dlg)
        dlg.reject()

        # 6. la recherche (Ctrl+F)
        dlg = SearchDialog(e, w)
        dlg.show()
        dlg.edit.setText("plateau")
        shot("recherche.png", dlg)
        dlg.reject()

        # 7. le diagnostic : quelques touches reconnues
        dlg = DiagnosticDialog(e, w)
        dlg.show()
        dlg.tabs.set("in")
        dlg.stack.setCurrentIndex(1)
        for idx in (0, 1, 2, 8, 10, 11, 19, 20, 27):
            dlg.key_event.emit("DEMOXL0000001", idx, True)
            dlg.key_event.emit("DEMOXL0000001", idx, False)
        shot("diagnostic.png", dlg)
        dlg.reject()
        e.stop()
    sys.stdout.flush()
    os._exit(0)  # fin nette : décharger Qt avec des fils natifs encore actifs plante parfois sous Windows


if __name__ == "__main__":
    main()
