"""Tests de bout en bout : faux vMix, faux Stream Deck, faux py-intercom / orchestream-v2 / Tally32.

    python -m pytest tests      ou      python tests/test_core.py

Rien ne sort du PC : tous les serveurs sont simulés sur 127.0.0.1.
"""
import json
import os
import struct
import sys
import threading
import time
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# hors écran, Qt n'a pas de polices sans ce dossier : les mesures de mise en page seraient fausses
_fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
if _fonts.is_dir():
    os.environ.setdefault("QT_QPA_FONTDIR", str(_fonts))
ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

import fakes  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

KEYS = fakes.FakeKeys().install()  # avant tout : aucun test n'appuie sur une vraie touche du clavier
from livedeck import autostart  # noqa: E402
autostart.is_enabled = lambda: False  # ni lecture ni écriture du registre Windows pendant les tests
autostart.set_enabled = lambda on: None

from livedeck.actions import new_action  # noqa: E402
from livedeck.companion_import import import_companion  # noqa: E402
from livedeck.deck import DeckHandle, DeckHub, DeckManager  # noqa: E402
from livedeck.engine import Engine  # noqa: E402
from livedeck import loupedeck as ld  # noqa: E402
from livedeck.ldeck import LoupedeckHandle, LoupedeckManager  # noqa: E402
from livedeck import vmix as vmix_mod  # noqa: E402
from livedeck.feedback import GREEN, RED, auto  # noqa: E402
from livedeck import integrations  # noqa: E402
from livedeck.model import (DECK_SIZES, backup_config, config_text, control_key, deck_size,  # noqa: E402
                           default_config, key, load_config, new_button, new_control, new_page,
                           normalize_config, parse_control, surface_of)
from livedeck.ui.inspector import prepare_preset  # noqa: E402
from livedeck.ui.deck_view import BOTTOM_L, BOTTOM_R, ControlBar, DeckGrid  # noqa: E402
from livedeck.ui import theme  # noqa: E402
from livedeck.ui.diagnostic import DiagnosticDialog  # noqa: E402
from livedeck.ui.main_window import MainWindow  # noqa: E402
from livedeck.ui.search import ROLE, SearchDialog  # noqa: E402
from livedeck.ui.settings import SettingsDialog  # noqa: E402
from livedeck.ui.widgets import InputCombo  # noqa: E402
from livedeck.ui import widgets as ui_widgets  # noqa: E402
from livedeck.vars import expand  # noqa: E402
from livedeck.vmix import VmixClient, _encode_query  # noqa: E402
from livedeck.webreq import parse_curl  # noqa: E402
from livedeck import integrations as integ, netsend, onvif, webreq, winsys  # noqa: E402
from livedeck import presets  # noqa: E402
from livedeck.actions import ACTIONS, execute, is_relative, shown, summary  # noqa: E402
from livedeck.feedback import OFF_BG, fb  # noqa: E402
from livedeck.health import json_compare, json_path  # noqa: E402
from PySide6.QtCore import QMimeData, QPointF, Qt  # noqa: E402
from PySide6.QtWidgets import QLabel, QPlainTextEdit  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "demo.companionconfig"
VMIX_PORT = int(os.environ.get("LIVEDECK_TEST_VMIX_PORT") or 18191)  # autre port : deux suites en même temps
app = QApplication.instance() or QApplication([])
app.setStyleSheet(theme.QSS)
_vmix = None


def wait(cond, timeout=4.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.005)
    return bool(cond())


def vmix_server():
    global _vmix
    if _vmix is None:
        _vmix = fakes.serve_vmix(VMIX_PORT)
    return _vmix


def engine(tmp_path, cfg=None, start=True) -> Engine:
    """Moteur sans Stream Deck réel, branché sur le faux vMix (intégrations coupées par défaut)."""
    vmix_server()
    e = Engine(Path(tmp_path) / "config.json", enable_decks=False)
    e.winaudio = winsys.CachedAudio(fakes.FakeAudio())  # jamais le vrai son de la machine
    if cfg is None:
        cfg, _ = import_companion(FIXTURE, integrations=True)
        cfg["integrations"] = {"intercom": {"host": "127.0.0.1", "port": 18199}, "orchestream": {"url": ""},
                               "tallies": []}
    cfg["vmix"] = {"host": "127.0.0.1", "port": VMIX_PORT}
    e.cfg = cfg
    if start:
        e.start()
        assert wait(lambda: e.vmix.state.connected and e.vmix.state.inputs), "pas de connexion au faux vMix"
    return e


def page_ids(e) -> dict:
    return {p["name"]: p["id"] for p in e.pages}


def tap(e, pid, k):
    e.press(pid, k, True)
    e.press(pid, k, False)


class MockDeck:
    """Imite l'API python-elgato-streamdeck d'un Stream Deck MK.2."""

    def __init__(self, i=1):
        self.images, self.brightness, self._id = {}, None, f"mock{i}"

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def key_image_format(self):
        return {"size": (72, 72), "format": "JPEG", "flip": (True, True), "rotation": 0}

    def set_key_image(self, i, data):
        self.images[i] = data

    def set_brightness(self, b):
        self.brightness = b

    def id(self):
        return self._id

    def connected(self):
        return True


def mock_deck(e, serial="DEMOSD0000001"):
    dm = DeckManager(e)
    e.decks = dm
    d = MockDeck()
    h = DeckHandle(d, serial, "Stream Deck Original", 3, 5, 72)
    dm.handles[d.id()] = h
    h.page_id = dm._initial_page(h)
    return dm, d, h


class MockLoupedeckPort:
    """Imite le port série d'un Loupedeck CT : poignée de main « WebSocket », trames 0x82,
    réponses aux commandes et injection d'événements physiques."""

    def __init__(self, serial="FAKECT0000001", firmware=(1, 2, 3), junk=b""):
        # junk : rebut laissé par une session précédente mal refermée (acquittements, trame de fermeture)
        self.is_open, self._pending = True, bytearray(junk)
        self.written, self.commands = bytearray(), []
        self.serial_no, self.firmware = serial, firmware
        self.flushed = 0

    # -- API pyserial utilisée par le pilote ---------------------------------
    def reset_input_buffer(self):
        self.flushed += 1
        self._pending.clear()

    def reset_output_buffer(self):
        self.flushed += 1

    def close(self):
        self.is_open = False

    def read(self, n=1):
        if not self._pending:
            time.sleep(0.01)  # comme le timeout de pyserial : la boucle de lecture ne tourne pas à vide
            return b""
        out, self._pending = bytes(self._pending[:n]), self._pending[n:]
        return out

    def write(self, data):
        self.written += data
        if data.startswith(b"GET /index.html"):
            self.written.clear()
            self._pending += b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n\r\n"
            return len(data)
        self._decode_written()
        return len(data)

    def _decode_written(self):
        """Extrait les commandes envoyées par LiveDeck et répond à SERIAL / VERSION."""
        buf = self.written
        while True:
            i = buf.find(b"\x82")
            if i < 0 or len(buf) < i + 2:
                return
            n = buf[i + 1]
            if n == 0xFF:  # gros message : longueur sur 4 octets
                if len(buf) < i + 14:
                    return
                size = int.from_bytes(bytes(buf[i + 6:i + 10]), "big")
                head = i + 14
            else:
                size, head = n - 0x80, i + 6
            if len(buf) < head + size:
                return
            body = bytes(buf[head:head + size])
            del buf[:head + size]
            if len(body) < 3:
                continue
            cmd, tid, data = body[1], body[2], body[3:]
            self.commands.append((cmd, data))
            if cmd == ld.CMD_SERIAL:
                self.emit(ld.CMD_SERIAL, self.serial_no.encode(), tid)
            elif cmd == ld.CMD_VERSION:
                self.emit(ld.CMD_VERSION, bytes(self.firmware), tid)

    # -- injection d'événements physiques ------------------------------------
    def emit(self, cmd, data=b"", tid=1):
        body = bytes([3 + len(data), cmd, tid]) + data
        self._pending += bytes([0x82, len(body)]) + body

    def button(self, name, down=True):
        self.emit(ld.CMD_BUTTON, bytes([ld.BUTTON_CODES[name], 0x00 if down else 0x01]))

    def rotate(self, name, delta=1):
        self.emit(ld.CMD_ROTATE, bytes([ld.BUTTON_CODES[name]]) + int(delta).to_bytes(1, "big", signed=True))

    def touch(self, x, y, finger=1, end=False, wheel=False):
        cmd = ((ld.CMD_TOUCH_END_CT if end else ld.CMD_TOUCH_CT) if wheel
               else (ld.CMD_TOUCH_END if end else ld.CMD_TOUCH))
        self.emit(cmd, b"\x00" + int(x).to_bytes(2, "big") + int(y).to_bytes(2, "big") + bytes([finger]))


def mock_loupedeck(e, serial="FAKECT0000001", junk=b""):
    """Un Loupedeck CT simulé, branché sur un LoupedeckManager comme s'il venait d'être détecté."""
    port = MockLoupedeckPort(serial, junk=junk)
    lm = LoupedeckManager(e)
    with mock.patch("serial.Serial", return_value=port):
        dev = ld.Loupedeck("COM-TEST", 0x0007, serial, on_event=lm._on_event)
        dev.connect()
    h = LoupedeckHandle(dev)
    lm.handles["COM-TEST"] = h
    h.page_id = lm._initial_page(h)
    return lm, port, h


def held_keys(since=0):
    """Raccourcis clavier encore maintenus (press sans release) depuis l'index donné du journal."""
    held = []
    for kind, combo in KEYS.log[since:]:
        if kind == "press":
            held.append(combo)
        elif kind == "release" and combo in held:
            held.remove(combo)
    return held


def key_xy(index, cols=4, size=90, keys_x=60):
    """Centre de la vignette `index` du pavé central, en coordonnées de la dalle."""
    row, col = divmod(index, cols)
    return keys_x + col * size + size // 2, row * size + size // 2


# -- import Companion -----------------------------------------------------------
def test_import_companion():
    plain, report = import_companion(FIXTURE)  # par défaut : pas de conversion vers les intégrations maison
    types = {a["type"] for p in plain["pages"] for b in p["buttons"].values() for a in b["actions"]}
    assert not types & {"deck.key", "tally.reconnect", "intercom.ptt"}, types
    assert any("Convertir vers les intégrations natives" in line for line in report)
    cfg, report = import_companion(FIXTURE, integrations=True)
    names = [p["name"] for p in cfg["pages"]]
    assert {"StreamDeck XL", "StreamDeck 1", "StreamDeck 2"} <= set(names), names
    assert set(cfg["decks"]) == {"DEMOXL0000001", "DEMOSD0000001", "DEMOSD0000002"}
    assert report
    integ = cfg["integrations"]
    assert "orchestream.local" in integ["orchestream"]["url"]
    assert {t["host"] for t in integ["tallies"]} >= {"10.0.0.51", "10.0.0.52", "10.0.0.53"}
    types = {a["type"] for p in cfg["pages"] for b in p["buttons"].values() for a in b["actions"]}
    assert {"vmix.preview", "vmix.program", "deck.key", "tally.reconnect", "intercom.ptt"} <= types, types
    keys = {a.get("key") for p in cfg["pages"] for b in p["buttons"].values() for a in b["actions"]}
    assert {"overlay.toggle:twizz", "overlay.toggle:poll", "overlay.toggle:speedizz"} <= keys, keys
    ptt = [a for p in cfg["pages"] for b in p["buttons"].values() for a in b["actions"] if a["type"] == "intercom.ptt"]
    assert ptt and all(a.get("fallback_keys") for a in ptt), ptt   # le raccourci d'origine reste en secours


def test_migration_v1(tmp_path):
    old = {"version": 1, "vmix": {"host": "127.0.0.1", "port": 8099}, "decks": {}, "options": {}, "pages": [
        {"id": "p", "name": "P", "cols": 5, "rows": 3, "buttons": {
            "0,0": {"id": "a", "label": "Poll", "mode": "toggle",
                    "actions": [{"type": "http", "method": "POST", "url": "http://orch.local/api/overlay/poll/show"}],
                    "actions_off": [{"type": "http", "method": "POST", "url": "http://orch.local/api/overlay/poll/hide"}]},
            "0,1": {"id": "b", "label": "Intercom Plateau", "actions": [{"type": "hotkey", "keys": "shift+z", "hold": True}]}}}]}
    p = Path(tmp_path) / "config.json"
    import json
    p.write_text(json.dumps(old), encoding="utf-8")
    cfg = load_config(p)
    btns = cfg["pages"][0]["buttons"]
    assert cfg["version"] >= 2
    assert btns["0,0"]["actions"][0] == {"type": "deck.key", "app": "orchestream", "key": "overlay.toggle:poll"}
    assert btns["0,0"]["mode"] == "press" and not btns["0,0"]["actions_off"]
    assert btns["0,1"]["actions"][0]["type"] == "intercom.ptt"
    assert btns["0,1"]["actions"][0]["fallback_keys"] == "shift+z"
    assert cfg["_migration_notes"] and "_migration_notes" not in config_text(cfg)
    assert "orch.local" in cfg["integrations"]["orchestream"]["url"]


def test_parse_curl():
    r = parse_curl('curl -X POST "http://orch.local/api/overlay/poll/show" -H "Content-Type: application/json" -d "{}"')
    assert r["method"] == "POST" and r["url"].endswith("/poll/show") and r["body"] == "{}"
    r = parse_curl("curl 'https://api.x.io/v1/a' -H 'Authorization: Bearer abc' --data-raw '{\"a\":1}'")
    assert r["method"] == "POST" and "Authorization: Bearer abc" in r["headers"]


# -- vMix -------------------------------------------------------------------------
def test_vmix_preview_program_cut(tmp_path):
    e = engine(tmp_path)
    st = e.vmix.state
    for ref, title in [("animateur", "Animateur"), ("cameraregie", "Camera Regie"), ("PTZ - Couloir", "PTZ - Couloir")]:
        assert st.resolve(ref)["title"] == title
    xl = page_ids(e)["StreamDeck XL"]
    tap(e, xl, "1,3")                               # preview Camera Regie
    assert wait(lambda: st.preview() == 4)
    tap(e, xl, "0,1")                               # program General
    assert wait(lambda: st.program() == 2)
    assert wait(lambda: e.visual(e.button(xl, "0,1"))["bg"] == RED)
    prog, prev = st.program(), st.preview()
    tap(e, xl, "3,3")                               # CUT
    assert wait(lambda: st.program() == prev and st.preview() == prog)
    n = len(e.page(xl)["buttons"])
    e.set_button(xl, "0,0", None)
    e.undo()
    assert len(e.page(xl)["buttons"]) == n
    e.stop()
    assert (Path(tmp_path) / "config.json").exists()


def test_take_long_press_and_confirm(tmp_path):
    e = engine(tmp_path)
    st = e.vmix.state
    pid = e.pages[0]["id"]
    e.set_button(pid, "3,2", new_button(label="Reporter", actions=[new_action("vmix.take", input="Reporter")]))
    tap(e, pid, "3,2")
    assert wait(lambda: st.preview() == 3)          # 1er appui : preview
    tap(e, pid, "3,2")
    assert wait(lambda: st.program() == 3)          # 2e appui : à l'antenne
    e.set_button(pid, "3,6", new_button(label="Court/Long", actions=[new_action("vmix.preview", input="Ecran Jeu")],
                                        long_actions=[new_action("vmix.preview", input="Iphone")]))
    e.press(pid, "3,6", True)
    time.sleep(0.1)
    e.press(pid, "3,6", False)
    assert wait(lambda: st.preview() == 5)          # appui court
    e.press(pid, "3,6", True)
    time.sleep(0.25)
    assert 0 < (e.visual(e.button(pid, "3,6")).get("progress") or 0) < 1
    time.sleep(0.5)
    e.press(pid, "3,6", False)
    assert wait(lambda: st.preview() == 6)          # appui long
    e.set_button(pid, "3,6", new_button(label="Sécurisé", confirm=True,
                                        actions=[new_action("vmix.preview", input="PTZ Plateau")]))
    tap(e, pid, "3,6")
    time.sleep(0.3)
    assert st.preview() == 6                         # appui bref ignoré
    e.press(pid, "3,6", True)
    time.sleep(0.7)
    e.press(pid, "3,6", False)
    assert wait(lambda: st.preview() == 7)          # maintenu : exécuté
    e.stop()


def test_import_heals_input_names(tmp_path):
    e = engine(tmp_path)
    xl = page_ids(e)["StreamDeck XL"]
    assert wait(lambda: e.button(xl, "0,3")["actions"][0]["input"] == "Camera Regie")
    e.stop()


# -- Stream Deck ------------------------------------------------------------------
def test_deck_render_cache_and_keys(tmp_path):
    e = engine(tmp_path, start=False)
    dm, d, h = mock_deck(e)
    assert e.page(h.page_id)["name"] == "StreamDeck 1"
    dm.apply_settings(h.serial)
    assert d.brightness is not None
    dm.refresh(force=True)
    assert len(d.images) == 15 and len(d.images[0]) > 100
    d.images.clear()
    dm.refresh()
    assert not d.images                              # rien n'a changé : rien n'est renvoyé
    e.page(h.page_id)["buttons"]["2,2"] = new_button(label="Page ▶", actions=[new_action("page", page="next")])
    dm.refresh()
    assert list(d.images) == [12]
    dm._on_key(d, 12, True)
    dm._on_key(d, 12, False)
    assert wait(lambda: e.page(h.page_id)["name"] != "StreamDeck 1")


# -- interface ------------------------------------------------------------------
def test_editing_flow(tmp_path):
    cfg = default_config()
    e = engine(tmp_path, cfg=cfg, start=False)
    w = MainWindow(e)
    e.start()
    w.show()
    assert wait(lambda: e.vmix.state.inputs)
    pid = e.pages[0]["id"]
    w.grid.select("0,0")
    w.inspector._create("program")
    combo = w.inspector.findChildren(InputCombo)[0]
    combo._picked(combo.combo.findData("Camera Regie"))
    assert e.button(pid, "0,0")["actions"][0]["input"] == "Camera Regie"
    # double-clics successifs : une rangée remplie d'un coup
    w.grid.select("1,0")
    w.library.inputs.mode.setCurrentIndex(w.library.inputs.mode.findData("preview"))
    for t in ["Animateur", "General", "Reporter"]:
        w._input_activated(t)
    assert [e.button(pid, f"1,{c}")["label"] for c in range(3)] == ["Animateur", "General", "Reporter"]
    # double-clic sur une touche existante : l'input change
    w.grid.select("0,0")
    w._input_activated("Iphone")
    assert e.button(pid, "0,0")["actions"][0]["input"] == "Iphone"
    # bibliothèque
    w.grid.select("3,0")
    w._preset_activated("cut")
    w.grid.select("3,1")
    w._preset_activated("rec")
    assert e.button(pid, "3,0") and e.button(pid, "3,1")["actions"]
    # étapes
    w.grid.select("0,0")
    w.inspector._add_action("vmix.transition")
    w.inspector._change_type("vmix.overlay")
    assert [a["type"] for a in e.button(pid, "0,0")["actions"]] == ["vmix.program", "vmix.overlay"]
    w.inspector._del_step()
    assert [a["type"] for a in e.button(pid, "0,0")["actions"]] == ["vmix.program"]
    # audio et retour d'état
    w.grid.select("2,0")
    w.library.inputs.mode.setCurrentIndex(w.library.inputs.mode.findData("audio"))
    w._input_activated("General")
    tap(e, pid, "2,0")
    assert wait(lambda: (e.vmix.state.resolve("General") or {}).get("muted"))
    tap(e, pid, "3,1")
    assert wait(lambda: e.vmix.state.flags.get("recording"))
    # envoyer vers une autre page
    p2 = e.add_page("Page 2", 5, 3)
    e.send_to_page(pid, "3,0", p2)
    assert e.page(p2)["buttons"]
    tap(e, pid, "3,1")                              # arrête l'enregistrement du faux vMix
    w.quit_app()


def test_live_lock_search_and_diagnostic(tmp_path):
    e = engine(tmp_path, start=False)
    dm, d, h = mock_deck(e)
    w = MainWindow(e)
    e.start()
    w.show()
    assert wait(lambda: e.vmix.state.inputs)
    pid = e.pages[0]["id"]
    # verrou Live : rien ne s'efface, l'inspecteur est figé, « Tester » reste actif
    w.grid.select("0,0")
    w.set_live(True)
    w.delete_selected()
    assert e.button(pid, "0,0") is not None
    from PySide6.QtWidgets import QComboBox
    ins = w.inspector                               # Live : vue d'exploitation, plus aucun champ modifiable
    assert ins.locked and ins.test_btn.isEnabled() and not ins.clear_btn.isVisibleTo(ins)
    assert not [c for t in (QComboBox, QPlainTextEdit) for c in ins.inner.findChildren(t) if c.isVisibleTo(ins.inner)]
    w.set_live(False)
    # recherche Ctrl+F
    dlg = SearchDialog(e, w)
    dlg.edit.setText("plateau")
    app.processEvents()
    assert [it for it, *_ in dlg._items if not it.isHidden()]
    dlg.close()
    # diagnostic : les touches répondent, aucune commande vMix ne part
    dd = DiagnosticDialog(e, w)
    dd.show()
    for idx in (0, 1, 7):
        dm._on_key(d, idx, True)
    assert wait(lambda: len(dd._seen) == 3)
    before = e.vmix.stats["cmd"]
    dd._run_demo()
    n = 0
    while dd._seq and n < 2000:
        dd._step()
        n += 1
    assert e.vmix.stats["cmd"] == before
    dd.accept()
    assert dm.diag_handler is None
    w.quit_app()


def test_lock_blocks_clicks_and_editing(tmp_path):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    e = engine(tmp_path, start=False)
    w = MainWindow(e)
    e.start()
    w.show()
    assert wait(lambda: e.vmix.state.inputs)
    pid = e.pages[0]["id"]
    w.grid.select("0,0")
    w.set_live(True)  # même en Live, verrouillé : un clic ne déclenche rien
    sent = []
    real_press = e.press
    e.press = lambda *a, **k: sent.append(a) or real_press(*a, **k)
    w.set_locked(True)
    assert w.locked and w.lock_btn.isChecked() and "VERROUILLÉ" in w.windowTitle()
    QTest.mouseClick(w.grid, Qt.LeftButton, Qt.NoModifier, QPoint(w.grid.width() // 2, w.grid.height() // 2))
    QTest.mouseClick(w.grid, Qt.RightButton, Qt.NoModifier, QPoint(w.grid.width() // 2, w.grid.height() // 2))
    assert not sent
    # édition : rien ne bouge, ni au clavier ni par les menus
    w.delete_selected()
    w.cut()
    assert e.button(pid, "0,0") is not None
    assert not w.library.isEnabled() and not w.inspector.isEnabled() and not w.mode.isEnabled()
    assert not w._editable()
    w.set_live(False)  # F5 refusé
    assert w.grid.live
    # le Stream Deck physique reste actif
    tap(e, pid, "0,0")
    assert sent[-2:] == [(pid, "0,0", True), (pid, "0,0", False)]
    # le verrou survit à un redémarrage de l'interface
    w2 = MainWindow(e)
    assert w2.locked and not w2.library.isEnabled()
    w2.set_locked(False)
    assert w2.library.isEnabled() and w2.inspector.isEnabled() and not w2.locked
    QTest.mouseClick(w2.grid, Qt.LeftButton, Qt.NoModifier, QPoint(10, 10))
    w2.quit_app()
    w.quit_app()


def test_updater_release_download_and_dialog(tmp_path):
    import hashlib
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from livedeck import updater
    from livedeck.ui.dialogs import UpdateDialog
    payload = b"MZ-fake-installer" * 1000
    state = {"sha": "sha256:" + hashlib.sha256(payload).hexdigest(), "assets": True}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path == "/api":
                port = self.server.server_address[1]
                data = {"tag_name": "v99.0.0", "body": "### Nouveautés\n- Truc\n\n---\n\n### Installation\n1. ...",
                        "assets": [{"name": "LiveDeck-99.0.0-win64-setup.exe", "size": len(payload), "digest": state["sha"],
                                    "browser_download_url": f"http://127.0.0.1:{port}/file"}] if state["assets"] else []}
                body = json.dumps(data).encode()
            elif self.path == "/file":
                body = payload
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    api = f"http://127.0.0.1:{srv.server_address[1]}/api"
    try:
        assert updater.parse_version("v1.10.2") > updater.parse_version("1.9.9") and not updater.is_newer("1.7.0", "1.7.0")
        assert updater.is_newer("1.8.0", "1.7.9") and not updater.is_newer("", "1.0.0") and not updater.is_newer("zzz", "1.0.0")
        with mock.patch.dict(os.environ, {"LIVEDECK_UPDATE_API": api}),                 mock.patch.object(updater, "update_dir", return_value=Path(tmp_path)):
            rel = updater.latest()
            assert rel.version == "99.0.0" and rel.notes == "### Nouveautés\n- Truc" and rel.sha256 and updater.is_newer(rel.version)
            seen = []
            f = updater.download(rel, lambda d, tot: seen.append((d, tot)))
            assert f.read_bytes() == payload and seen[-1] == (len(payload), len(payload)) and not list(Path(tmp_path).glob("*.part"))
            # empreinte fausse : fichier refusé et supprimé
            bad = updater.Release(**{**rel.__dict__, "sha256": "0" * 64})
            try:
                updater.download(bad)
                raise AssertionError("empreinte non vérifiée")
            except updater.UpdateError as ex:
                assert "SHA-256" in str(ex)
            assert not list(Path(tmp_path).glob("*.part"))
            # release sans installateur : rien à proposer
            state["assets"] = False
            assert updater.latest() is None
            state["assets"] = True
            # fenêtre : téléchargement puis lancement de l'installateur
            launched = []
            with mock.patch.object(updater, "can_install", return_value=True),                     mock.patch.object(updater, "launch_installer", side_effect=launched.append):
                dlg = UpdateDialog(None, rel, busy=True)
                assert "Mode Live" in dlg.info.text()
                dlg._start()
                assert wait(lambda: dlg.installing)
                assert launched == [f]
        # dépôt introuvable ou GitHub hors ligne : message clair, jamais d'exception brute
        with mock.patch.dict(os.environ, {"LIVEDECK_UPDATE_API": api.replace("/api", "/absent")}):
            try:
                updater.latest()
                raise AssertionError("404 non signalé")
            except updater.UpdateError as ex:
                assert "Aucune release" in str(ex)
        with mock.patch.dict(os.environ, {"LIVEDECK_UPDATE_API": "http://127.0.0.1:1/api"}):
            try:
                updater.latest()
                raise AssertionError("hors ligne non signalé")
            except updater.UpdateError as ex:
                assert "injoignable" in str(ex)
    finally:
        srv.shutdown()
    # fenêtre principale : une release plus récente affiche le bouton, la même version le laisse caché
    e = engine(tmp_path, start=False)
    w = MainWindow(e)
    w._update_result(updater.Release("0.0.1", "", "u", "n", 1, ""), "")
    assert w.update_found is None and not w.update_btn.isVisibleTo(w)
    w._update_result(updater.Release("99.0.0", "x", "u", "n", 1, ""), "")
    assert w.update_found.version == "99.0.0" and "99.0.0" in w.update_btn.text() and w.update_btn.isVisibleTo(w)
    w.quit_app()


# -- intégrations maison ---------------------------------------------------------
def integrations_engine(tmp_path):
    ic, orch = fakes.FakeIntercom(18192), fakes.FakeOrchDeck(18193)
    t1, t2 = fakes.FakeTally(18194, True), fakes.FakeTally(18195, False)
    cfg, _ = import_companion(FIXTURE, integrations=True)
    cfg["integrations"] = {"intercom": {"host": "127.0.0.1", "port": 18192},
                           "orchestream": {"url": "127.0.0.1:18193", "token": fakes.FakeOrchDeck.TOKEN},
                           "tallies": [{"name": "Reporter", "host": "127.0.0.1:18194"},
                                       {"name": "Animateur", "host": "127.0.0.1:18195"}]}
    return engine(tmp_path, cfg=cfg), ic, orch, (t1, t2)


def test_integrations(tmp_path):
    e, ic, orch, (t1, t2) = integrations_engine(tmp_path)
    P = page_ids(e)
    xl, sd1, sd2 = P["StreamDeck XL"], P["StreamDeck 1"], P["StreamDeck 2"]

    def vis(pg, k):
        return e.visual(e.button(pg, k))

    # py-intercom : maintien = micro ouvert, relâché = coupé
    assert wait(lambda: e.intercom.reachable and e.intercom.online)
    e.press(xl, "3,0", True)
    assert wait(lambda: e.intercom.bus("regie").get("active"))
    assert wait(lambda: vis(xl, "3,0")["bg"].lower() == "#2563eb")
    e.press(xl, "3,0", False)
    assert wait(lambda: e.intercom.bus("regie").get("active") is False)
    assert ("regie", "on") in ic.log and ("regie", "off") in ic.log
    ic.online = False
    assert wait(lambda: not e.intercom.online)
    ic.online = True
    # orchestream : la touche (overlay.toggle:twizz) suit le widget, même basculé ailleurs
    assert wait(lambda: e.orchestream.connected)
    tap(e, sd2, "2,4")
    assert wait(lambda: orch.overlays["twizz"] is False and not e.orchestream.key("overlay.toggle:twizz")["active"])
    orch.overlays["twizz"] = True
    assert wait(lambda: e.orchestream.key("overlay.toggle:twizz")["active"])
    logs = []
    e.listeners.append(lambda k, pl: logs.append(pl) if k == "log" else None)
    tap(e, sd2, "2,1")                              # Speedizz sans partie : le refus est journalisé
    assert wait(lambda: any("Speedizz" in m for _, m in logs))
    # Tally32 : un tally hors ligne rend la touche rouge, l'appui le reconnecte
    assert wait(lambda: e.tallies.summary("all")[0] is False)
    assert "Animateur" in vis(sd1, "2,0")["label"]
    tap(e, sd1, "2,0")
    assert wait(lambda: e.tallies.summary("all")[0] is True, 8)
    assert t2.reconnects >= 1
    # réglages : l'onglet Intégrations s'ouvre avec l'état en direct
    w = MainWindow(e)
    dlg = SettingsDialog(e, w)
    dlg.show()
    dlg._refresh_status()
    dlg.reject()
    w.quit_app()


# -- sûreté du moteur ------------------------------------------------------------
def ptt_engine(tmp_path, port):
    """Faux py-intercom + faux deck 5×3 : page A (PTT régie maintenu, page suivante) et page B."""
    ic = fakes.FakeIntercom(port)
    cfg = default_config()
    a = cfg["pages"][0]
    a.update(name="A", cols=5, rows=3)
    b = new_page("B", 5, 3)
    cfg["pages"].append(b)
    a["buttons"]["0,0"] = new_button(label="Talk", actions=[new_action("intercom.ptt", bus="regie", mode="hold")])
    a["buttons"]["0,1"] = new_button(label="Page", actions=[new_action("page", page="next")])
    cfg["integrations"]["intercom"] = {"host": "127.0.0.1", "port": port}
    e = engine(tmp_path, cfg=cfg)
    dm, d, h = mock_deck(e)
    h.page_id = a["id"]
    assert wait(lambda: e.intercom.reachable and e.intercom.online)
    return e, ic, dm, d, h, a["id"], b["id"]


def test_release_follows_pressed_button(tmp_path):
    e, ic, dm, d, h, pa, pb = ptt_engine(tmp_path, 18201)
    regie = ic.buses["regie"]
    # page changée pendant l'appui : le relâché coupe quand même le micro
    dm._on_key(d, 0, True)
    assert wait(lambda: regie["active"])
    dm._on_key(d, 1, True)
    dm._on_key(d, 1, False)
    assert wait(lambda: h.page_id == pb)
    dm._on_key(d, 0, False)
    assert wait(lambda: regie["active"] is False)
    assert ic.log[:2] == [("regie", "on"), ("regie", "off")], ic.log
    assert not e.pressed
    # bouton modifié pendant l'appui : le « off » va au bus qui a été ouvert
    dm.goto(h.serial, pa)
    dm._on_key(d, 0, True)
    assert wait(lambda: regie["active"])
    e.set_button(pa, "0,0", new_button(label="Talk", actions=[new_action("intercom.ptt", bus="plateau", mode="hold")]))
    dm._on_key(d, 0, False)
    assert wait(lambda: regie["active"] is False)
    assert ("plateau", "off") not in ic.log
    # bouton supprimé pendant l'appui
    e.set_button(pa, "0,0", new_button(label="Talk", actions=[new_action("intercom.ptt", bus="regie", mode="hold")]))
    dm._on_key(d, 0, True)
    assert wait(lambda: regie["active"])
    e.set_button(pa, "0,0", None)
    dm._on_key(d, 0, False)
    assert wait(lambda: regie["active"] is False)
    e.stop()


def test_release_all_on_unplug_and_stop(tmp_path):
    e, ic, dm, d, h, pa, pb = ptt_engine(tmp_path, 18202)
    regie = ic.buses["regie"]
    dm._on_key(d, 0, True)
    assert wait(lambda: regie["active"])
    d.connected = lambda: False                     # deck débranché pendant l'appui
    dm._manager = type("M", (), {"enumerate": lambda self: []})()
    dm._scan()
    assert not dm.handles
    assert wait(lambda: regie["active"] is False)
    assert dm._initial_page(h) == pa                 # rebranché : il revient sur sa page
    e.press(pa, "0,0", True)                         # appui depuis l'écran, puis on quitte
    assert wait(lambda: regie["active"])
    e.stop()
    assert regie["active"] is False and ("all", "off") in ic.log
    assert not e._runners


def test_hold_actions_on_special_buttons(tmp_path):
    e, ic, dm, d, h, pa, pb = ptt_engine(tmp_path, 18203)
    st = e.vmix.state

    def active():
        return ic.buses["regie"]["active"]
    talk = new_action("intercom.ptt", bus="regie", mode="hold")
    # appui long : le micro s'ouvre dès l'appui et reste ouvert jusqu'au relâché
    e.set_button(pa, "1,0", new_button(actions=[talk], long_actions=[new_action("vmix.preview", input="Iphone")]))
    e.press(pa, "1,0", True)
    assert wait(active)
    time.sleep(0.7)
    assert active()
    e.press(pa, "1,0", False)
    assert wait(lambda: not active())
    assert wait(lambda: st.preview() == 6)          # l'appui long a aussi fait son travail
    # sécurité : le micro s'ouvre une fois le délai atteint, jusqu'au relâché
    e.set_button(pa, "1,1", new_button(actions=[talk], confirm=True))
    e.press(pa, "1,1", True)
    time.sleep(0.2)
    assert not active()
    assert wait(active, 2)
    time.sleep(0.2)
    assert active()
    e.press(pa, "1,1", False)
    assert wait(lambda: not active())
    # interrupteur : 1er appui = micro ouvert, 2e appui = fermé
    e.set_button(pa, "1,2", new_button(actions=[talk], mode="toggle"))
    tap(e, pa, "1,2")
    assert wait(active)
    time.sleep(0.2)
    assert active()
    tap(e, pa, "1,2")
    assert wait(lambda: not active())
    # « Tester » sur un bouton sécurisé
    e.set_button(pa, "1,3", new_button(actions=[new_action("vmix.preview", input="Reporter")], confirm=True))
    e.trigger(pa, "1,3")
    assert wait(lambda: st.preview() == 3)
    e.set_button(pa, "1,4", new_button(actions=[new_action("vmix.preview", input="Ecran Jeu")], confirm=True))
    e.press(pa, "1,4", True, test=True)
    e.press(pa, "1,4", False, test=True)
    assert wait(lambda: st.preview() == 5)
    e.stop()


def test_intercom_lost_off_is_resent(tmp_path):
    e, ic, dm, d, h, pa, pb = ptt_engine(tmp_path, 18204)
    ic.ignore_off = 1                                # le client accepte le « off » mais ne coupe pas
    dm._on_key(d, 0, True)
    assert wait(lambda: ic.buses["regie"]["active"])
    dm._on_key(d, 0, False)
    assert wait(lambda: ic.buses["regie"]["active"] is False, 5)
    assert ic.log.count(("regie", "off")) >= 2
    e.stop()


def test_corrupt_config_and_backups(tmp_path):
    p = Path(tmp_path) / "config.json"
    p.write_text("{ pas du json", encoding="utf-8")
    e = Engine(p, enable_decks=False)
    assert e.load_warning and e.pages
    assert list(Path(tmp_path).glob("config.json.corrupt-*"))
    e.flush_save()
    assert load_config(p)["pages"] and not e.save_error
    e.replace_config(default_config())              # sauvegarde de secours avant remplacement
    folder = Path(tmp_path) / "backups"
    assert list(folder.glob("config-*.json"))
    for i in range(25):
        backup_config(p, f"t{i}")
    assert len(list(folder.glob("config-*.json"))) == 20
    e.flush_save()
    p.write_text("[1, 2]", encoding="utf-8")         # JSON valide mais pas une configuration
    e2 = Engine(p, enable_decks=False)
    assert e2.load_warning and e2.pages


def test_concurrent_mutate_and_save(tmp_path):
    e = engine(tmp_path, cfg=default_config(), start=False)
    pid = e.pages[0]["id"]
    errors, stop = [], threading.Event()
    e.listeners.append(lambda k, pl: errors.append(pl) if k == "log" and pl[0] == "error" else None)

    def saver():
        while not stop.is_set():
            e.flush_save()

    def mutator():
        for i in range(200):
            e.mutate(lambda cfg: cfg["options"].__setitem__("n", i))
    threads = [threading.Thread(target=saver), threading.Thread(target=mutator)]
    for t in threads:
        t.start()
    for i in range(200):
        e.set_button(pid, f"{i % 4},{i % 8}", new_button(label=str(i)))
    threads[1].join()
    stop.set()
    threads[0].join()
    e.flush_save()
    assert not errors, errors
    saved = load_config(Path(tmp_path) / "config.json")
    assert saved["pages"][0]["buttons"]["3,7"]["label"] == "199" and saved["options"]["n"] == 199
    assert not list(Path(tmp_path).glob("*.tmp"))


def test_runners_freed(tmp_path):
    e = engine(tmp_path, cfg=default_config())
    pid = e.pages[0]["id"]
    for c in range(6):
        e.set_button(pid, f"0,{c}", new_button(actions=[new_action("delay", ms=1)]))
        tap(e, pid, f"0,{c}")
    assert len(e._runners) == 6
    for c in range(5):
        e.set_button(pid, f"0,{c}", None)
    assert list(e._runners) == [e.button(pid, "0,5")["id"]]
    e.stop()
    assert not e._runners


def test_undo_settings_and_coalesce(tmp_path):
    e = engine(tmp_path, cfg=default_config(), start=False)
    pid = e.pages[0]["id"]
    e.set_button(pid, "0,0", new_button(label="A"))
    e.set_vmix("10.1.2.3", 8099)
    assert e.vmix.host == "10.1.2.3"
    e.set_button(pid, "0,1", new_button(label="B"))
    e.undo()                                         # annule B, pas le réglage vMix
    assert e.cfg["vmix"]["host"] == "10.1.2.3" and e.vmix.host == "10.1.2.3"
    e.undo()                                         # annule le réglage vMix : le client suit
    assert e.vmix.host == e.cfg["vmix"]["host"] == "127.0.0.1"
    n = len(e._undo)
    b = e.button(pid, "0,0")
    e.set_button(pid, "0,0", {**b, "label": "A1"}, coalesce="label")
    e.set_button(pid, "0,0", {**b, "label": "A12"}, coalesce="label")
    assert len(e._undo) == n + 1                     # frappes sur la même touche : une étape
    e.set_button(pid, "0,2", new_button(label="C"), coalesce="label")
    assert len(e._undo) == n + 2                     # autre touche : nouvelle étape


def test_deck_pages_and_models(tmp_path):
    e = engine(tmp_path, start=False)
    dm, d, h = mock_deck(e)
    P = page_ids(e)
    dm.goto(h.serial, P["StreamDeck 2"])
    before = e.cfg["decks"][h.serial].get("brightness", 80)
    e.set_deck_settings(h.serial, brightness=40)    # Réglages → OK : la page ne bouge pas
    assert h.page_id == P["StreamDeck 2"] and d.brightness == 40
    dm.restore()                                     # fin du diagnostic
    assert h.page_id == P["StreamDeck 2"]
    e.undo()
    assert h.page_id == P["StreamDeck 2"] and d.brightness == before   # annuler : la luminosité suit
    seen = set()
    for _ in range(len(e.pages) + 1):                # suivante : seulement les pages 5×3
        dm.goto(h.serial, "next")
        seen.add(h.page_id)
    assert P["StreamDeck XL"] not in seen and P["StreamDeck 1"] in seen
    # Neo : 8 touches + 2 touches tactiles (index 8 et 9) = page précédente / suivante
    n1, n2 = e.add_page("Neo 1", 4, 2), e.add_page("Neo 2", 4, 2)
    neo = MockDeck(2)
    hn = DeckHandle(neo, "NEO1", "Stream Deck Neo", 2, 4, 96)
    dm.handles[neo.id()] = hn
    hn.page_id = dm._initial_page(hn)
    assert hn.page_id == n1
    dm._on_key(neo, 9, True)
    dm._on_key(neo, 9, False)
    assert hn.page_id == n2
    # Pedal : pas d'écran, mais les pédales déclenchent leur bouton
    pedal_page = e.add_page("Pédales", 3, 1)
    e.set_button(pedal_page, "0,1", new_button(label="P2", actions=[new_action("delay", ms=1)]))
    ped = MockDeck(3)
    hp = DeckHandle(ped, "PEDAL1", "Stream Deck Pedal", 1, 3, 0, visual=False)
    dm.handles[ped.id()] = hp
    hp.page_id = dm._initial_page(hp)
    dm.refresh(force=True)
    assert not ped.images
    dm._on_key(ped, 1, True)
    assert e.button(pedal_page, "0,1")["id"] in e.pressed
    dm._on_key(ped, 1, False)
    sizes = {"Elgato Stream Deck +": (4, 2), "Stream Deck + XL": (9, 4), "Stream Deck Studio": (16, 2),
             "Elgato Stream Deck XL": (8, 4), "Stream Deck Pedal": (3, 1), "Elgato Stream Deck MK.2": (5, 3),
             "Stream Deck Mini": (3, 2), "Stream Deck Neo": (4, 2), "elgato-streamdeck-plus": (4, 2)}
    for name, size in sizes.items():
        assert deck_size(name) == size, name


def test_threads_survive_callback_errors(tmp_path):
    vmix_server()
    calls = []

    def boom():
        calls.append(1)
        raise RuntimeError("test")
    vm = VmixClient(on_change=boom, on_log=lambda *a: None)
    vm.start("127.0.0.1", VMIX_PORT)
    assert wait(lambda: vm.state.connected and len(calls) >= 2)
    time.sleep(0.3)
    assert vm._thread.is_alive()
    vm.stop()
    e = engine(tmp_path, cfg=default_config(), start=False)
    dm, d, h = mock_deck(e)

    def broken(*a, **k):
        raise RuntimeError("panne")
    e.press = broken
    dm._on_key(d, 0, True)                           # ne lève pas : le thread du deck survit
    e.visual = broken
    dm.refresh(force=True)
    assert len(d.images) == 15                       # touches « ERREUR », mais affichées


# -- Take, preview, état optimiste -------------------------------------------------
def free_input(e, *avoid):
    """Un input ni à l'antenne ni en preview (Master), hors de ceux à éviter."""
    fv, st = vmix_server(), e.vmix.state
    for t in ("Iphone", "PTZ Plateau", "PTZ Atelier", "Ecran Jeu", "Invité", "Reporter", "Animateur"):
        n = st.number(t)
        if n not in (fv.mixes[1]["active"], fv.mixes[1]["preview"]) and t not in avoid:
            return t, n
    raise AssertionError("pas d'input libre")


def sent(fv, mark, name=None):
    return [f for f in fv.functions[mark:] if name is None or f[0] == name]


def test_take_fast_double_press_and_mix2(tmp_path):
    e = engine(tmp_path)
    fv, st = vmix_server(), e.vmix.state
    pid = e.pages[0]["id"]
    fv.delay = 0.08  # vMix lent : la 2e pression arrive avant la réponse à la 1re
    try:
        title, n = free_input(e)
        e.set_button(pid, "3,2", new_button(label="T", actions=[new_action("vmix.take", input=title)]))
        mark = len(fv.functions)
        tap(e, pid, "3,2")
        time.sleep(0.02)
        tap(e, pid, "3,2")
        assert wait(lambda: fv.mixes[1]["active"] == n), "2e appui rapide : l'input doit passer à l'antenne"
        assert [f[0] for f in sent(fv, mark)] == ["PreviewInput", "Cut"]
        assert sent(fv, mark, "Cut")[0][1].get("Input")  # la transition désigne l'input explicitement
        assert wait(lambda: st.program() == n)
        # Mix 2 : pas d'ACTS, l'état optimiste + le XML relu juste après la réponse suffisent
        title2, n2 = free_input(e, title)
        fv.mixes[2] = {"active": 1, "preview": 2}
        e.set_button(pid, "3,3", new_button(label="M2", actions=[new_action("vmix.take", input=title2, mix=2)]))
        mark = len(fv.functions)
        tap(e, pid, "3,3")
        time.sleep(0.05)
        tap(e, pid, "3,3")
        assert wait(lambda: fv.mixes[2]["active"] == n2)
        assert all(f[1].get("Mix") == "1" for f in sent(fv, mark))
        assert wait(lambda: st.program(2) == n2 and st.preview(2) == 1)
    finally:
        fv.delay = 0.0
    e.stop()


def test_take_on_air_and_colors(tmp_path):
    e = engine(tmp_path)
    fv, st = vmix_server(), e.vmix.state
    pid = e.pages[0]["id"]
    title, n = free_input(e)
    b = new_button(label="T", actions=[new_action("vmix.take", input=title)])
    e.set_button(pid, "3,2", b)
    e.vmix.function("CutDirect", Input=n)
    assert wait(lambda: st.program() == n and fv.mixes[1]["active"] == n)
    assert wait(lambda: e.visual(e.button(pid, "3,2"))["bg"] == RED)
    mark = len(fv.functions)
    tap(e, pid, "3,2")
    tap(e, pid, "3,2")
    time.sleep(0.3)
    assert not sent(fv, mark), "déjà à l'antenne : rien n'est envoyé"
    assert fv.mixes[1]["active"] == n
    # à l'antenne ET en preview : rouge (le rouge l'emporte) avec liseré vert
    e.vmix.function("PreviewInput", Input=n)
    assert wait(lambda: st.preview() == n)
    v = e.visual(e.button(pid, "3,2"))
    assert v["bg"] == RED and v["bar"] == GREEN, v
    # en preview seulement : vert = prêt à prendre
    other, n2 = free_input(e, title)
    e.vmix.function("CutDirect", Input=n2)
    assert wait(lambda: e.visual(e.button(pid, "3,2"))["bg"] == GREEN)
    # touche Preview : même règle
    pv = new_button(label="P", actions=[new_action("vmix.preview", input=other)])
    assert wait(lambda: e.visual(pv)["bg"] == RED)
    # retour auto d'une fonction vMix brute aux paramètres encodés
    raw = new_button(actions=[new_action("vmix.function", function="PreviewInput", params="Input=PTZ%20Plateau&Mix=1")])
    assert auto(raw)[0]["cond"] == {"type": "preview", "input": "PTZ Plateau", "mix": 2}
    e.stop()


def test_preview_second_press_and_bad_inputs(tmp_path):
    e = engine(tmp_path)
    fv, st = vmix_server(), e.vmix.state
    logs = []
    e.listeners.append(lambda k, p: logs.append(p) if k == "log" else None)
    pid = e.pages[0]["id"]
    title, n = free_input(e)
    e.set_button(pid, "3,4", new_button(label="P", actions=[new_action("vmix.preview", input=title)]))
    tap(e, pid, "3,4")
    tap(e, pid, "3,4")
    assert wait(lambda: fv.mixes[1]["preview"] == n)
    time.sleep(0.2)
    assert fv.mixes[1]["active"] != n                # par défaut : une preview reste une preview
    e.set_button(pid, "3,4", new_button(label="P", actions=[new_action("vmix.preview", input=title, then="Fade")]))
    mark = len(fv.functions)
    tap(e, pid, "3,4")
    assert wait(lambda: fv.mixes[1]["active"] == n)  # option « 2e appui » : à l'antenne
    assert [f[0] for f in sent(fv, mark)] == ["Fade"]
    # input vide ou introuvable : aucune commande, un message clair
    mark = len(fv.functions)
    for ref in ("", "Input qui n'existe pas"):
        e.set_button(pid, "3,5", new_button(label="X", actions=[new_action("vmix.take", input=ref)]))
        tap(e, pid, "3,5")
    assert wait(lambda: any("aucun input choisi" in m for _, m in logs))
    assert wait(lambda: any("introuvable dans vMix" in m for _, m in logs))
    assert not sent(fv, mark)
    # vMix refuse une commande : le message dit laquelle
    e.vmix.raw("SetText Input=Bandeau&SelectedName=Inconnu&Value=x")
    assert wait(lambda: any("SetText" in m and "refusé" in m for _, m in logs))
    # FTB : Démarrer / Arrêter n'envoient que si l'état change (FadeToBlack est un interrupteur)
    fv.ftb = False
    assert wait(lambda: not st.flags.get("fadetoblack"))
    e.set_button(pid, "3,6", new_button(label="FTB", actions=[new_action("vmix.production", what="fadetoblack",
                                                                          mode="start")]))
    tap(e, pid, "3,6")
    assert wait(lambda: fv.ftb and st.flags.get("fadetoblack"))
    tap(e, pid, "3,6")
    time.sleep(0.3)
    assert fv.ftb, "Démarrer alors que le FTB est actif ne doit pas le couper"
    e.set_button(pid, "3,6", new_button(label="FTB", actions=[new_action("vmix.production", what="fadetoblack",
                                                                          mode="stop")]))
    tap(e, pid, "3,6")
    assert wait(lambda: not fv.ftb)
    e.stop()


def test_dynamic_text(tmp_path):
    e = engine(tmp_path)
    pid = e.pages[0]["id"]
    e.vmix.function("CutDirect", Input=3)
    assert wait(lambda: expand("$(vmix:program_title)", e) == "Reporter")
    assert expand("$(vmix:input_title:3) · $(vmix:input_number:Reporter)", e) == "Reporter · 3"
    assert expand("$(vmix:foo) $(autre:x) $(vmix:input_title:Inexistant)", e) == "$(vmix:foo) $(autre:x) —"
    assert len(expand("$(time)", e)) == 8
    # champ de titre modifié par une commande à espaces (SetText … Value=Bonjour le monde)
    e.set_button(pid, "3,7", new_button(label="Titre", actions=[new_action(
        "vmix.function", function="SetText", params="Input=Bandeau&SelectedName=Headline.Text&Value=Bonjour le monde")]))
    tap(e, pid, "3,7")
    assert wait(lambda: expand("$(vmix:text:Bandeau:Headline)", e) == "Bonjour le monde")
    assert expand("$(vmix:text:Bandeau:0)", e) == "Bonjour le monde"
    # temps restant : la touche se met à jour toute seule pendant la lecture
    e.vmix.function("Restart", Input="Magneto")
    e.vmix.function("Play", Input="Magneto")
    b = new_button(label="CLIP\n$(vmix:remaining:Magneto)", actions=[new_action("vmix.preview", input="Magneto")])
    e.set_button(pid, "3,7", b)
    assert wait(lambda: e.visual(e.button(pid, "3,7"))["label"] in ("CLIP\n1:35", "CLIP\n1:34"))
    states = []
    e.listeners.append(lambda k, p: states.append(1) if k == "state" else None)
    first = e.visual(e.button(pid, "3,7"))["label"]
    assert wait(lambda: e.visual(e.button(pid, "3,7"))["label"] != first, 3)
    assert states                                    # le moteur a redemandé l'affichage
    e.vmix.function("Pause", Input="Magneto")
    rec = expand("$(vmix:recording_duration)", e)
    assert rec == "—" or ":" in rec
    e.stop()
    off = Engine(Path(tmp_path) / "c2.json", enable_decks=False)
    assert expand("$(vmix:program_title)", off) == "—"   # vMix hors ligne


def test_companion_command_and_vars_import(tmp_path):
    import json
    data = {"instances": {"v": {"instance_type": "vmix", "config": {"host": "10.0.0.9", "tcpPort": 8099}}},
            "pages": {"1": {"id": "p1", "name": "P", "controls": {"0": {
                "0": {"type": "button", "style": {"text": "$(vmix:input_reporter_remaining_mmss)\n$(internal:time_hms)"},
                      "steps": {"0": {"action_sets": {"down": [
                          {"connectionId": "v", "definitionId": "command",
                           "options": {"command": "SetText Input=Titre&SelectedName=Headline.Text&Value=Bonjour le monde"}},
                          {"connectionId": "v", "definitionId": "command", "options": {"command": "Cut Mix=1 Duration=500"}},
                          {"connectionId": "v", "definitionId": "previewInput", "options": {"input": "Reporter", "mix": -1}},
                          {"connectionId": "v", "definitionId": "transition", "options": {"functionID": "FadeToBlack"}}]}}},
                      "feedbacks": []}}}}}}
    p = Path(tmp_path) / "x.companionconfig"
    p.write_text(json.dumps(data), encoding="utf-8")
    cfg, report = import_companion(p, preview_then="Cut")
    b = cfg["pages"][0]["buttons"]["0,0"]
    a = b["actions"]
    assert a[0]["params"] == "Input=Titre&SelectedName=Headline.Text&Value=Bonjour le monde", a[0]
    assert a[1]["params"] == "Mix=1&Duration=500"
    assert a[2]["type"] == "vmix.preview" and a[2]["mix"] == 1 and a[2]["then"] == "Cut"
    assert a[3] == {"type": "vmix.production", "what": "fadetoblack", "mode": "toggle"}
    assert b["label"] == "$(vmix:remaining:reporter)\n$(time)"
    assert any("Master" in r for r in report) and any("2e appui" in r for r in report)
    assert _encode_query("Input=T&Value=Bonjour le monde") == "Input=T&Value=Bonjour%20le%20monde"
    assert _encode_query("Mix=2 Duration=500") == "Mix=2&Duration=500"
    # ancienne config : « Transition FadeToBlack » migrée (v3)
    old = default_config()
    old["version"] = 2
    old["pages"][0]["buttons"]["0,0"] = new_button(actions=[{"type": "vmix.transition", "effect": "FadeToBlack"}])
    old["pages"][0]["buttons"]["0,1"] = new_button(actions=[new_action("vmix.take", input="A", effect="FadeToBlack")])
    q = Path(tmp_path) / "old.json"
    q.write_text(json.dumps(old), encoding="utf-8")
    cfg = load_config(q)
    assert cfg["version"] >= 3
    assert cfg["pages"][0]["buttons"]["0,0"]["actions"][0]["type"] == "vmix.production"
    assert cfg["pages"][0]["buttons"]["0,1"]["actions"][0]["effect"] == "Cut"


def test_hung_vmix_is_detected(tmp_path):
    fv = fakes.serve_vmix(18211)
    logs = []
    vm = VmixClient(on_change=lambda: None, on_log=lambda lvl, m: logs.append(m))
    old = vmix_mod.STALE_AFTER
    vmix_mod.STALE_AFTER = 0.8
    try:
        vm.start("127.0.0.1", 18211)
        assert wait(lambda: vm.state.connected)
        fv.hang = True                               # socket ouvert, plus aucune réponse
        assert wait(lambda: not vm.state.connected, 4)
        assert any("ne répond plus" in m for m in logs)
        time.sleep(1.5)
        assert not vm.state.connected                # reconnexion à un vMix figé : jamais « connecté »
        fv.hang = False
        assert wait(lambda: vm.state.connected, 5)
    finally:
        vmix_mod.STALE_AFTER = old
        vm.stop()


# -- intégrations : diagnostics, secours, commandes réseau ------------------------------------------
def one_page(tmp_path, buttons: dict, integrations: dict | None = None):
    """Moteur sur une page 5×3 avec ces touches ; intégrations coupées sauf celles indiquées."""
    cfg = default_config()
    pg = cfg["pages"][0]
    pg.update(cols=5, rows=3)
    pg["buttons"].update(buttons)
    cfg["integrations"] = {"intercom": {"host": "127.0.0.1", "port": 18199}, "orchestream": {"url": ""},
                           "tallies": [], **(integrations or {})}
    e = engine(tmp_path, cfg=cfg)
    logs = []
    e.listeners.append(lambda k, pl: logs.append(pl) if k == "log" else None)
    return e, pg["id"], logs


def ptt(bus, mode="hold", **kw):
    return new_action("intercom.ptt", bus=bus, mode=mode, **kw)


def test_intercom_probe_and_errors(tmp_path):
    ok, msg = integ.probe_intercom("127.0.0.1", 18229, timeout=0.5)   # rien n'écoute
    assert ok is False and "contrôle HTTP" in msg and "réseau local" in msg and "/api/deck" in msg, msg
    ic = fakes.FakeIntercom(18221)
    ok, msg = integ.probe_intercom("127.0.0.1", 18221)
    assert ok is True and "Régie" in msg, msg
    ic.online = False
    ok, msg = integ.probe_intercom("127.0.0.1", 18221)
    assert ok is None and "connecte-le au serveur" in msg, msg
    ic.online = True
    fakes.FakeTally(18222, True)                                       # autre programme sur ce port
    assert "sans l'API Stream Deck de py-intercom" in integ.probe_intercom("127.0.0.1", 18222)[1]
    j = fakes.FakeJson(18223, {})
    j.status = 404
    assert "trop ancien" in integ.probe_intercom("127.0.0.1", 18223)[1]
    # API fidèle à py-intercom : numéros de bus acceptés, bus manquant ou inconnu refusé en 422
    base = "http://127.0.0.1:18221/api/deck/commands/"
    assert integ._deck_request("POST", base + "ptt.on", "", {"args": {"bus": "0"}}, direct=True)[0] == 200
    assert ic.buses["regie"]["active"]
    assert integ._deck_request("POST", base + "ptt.on", "", {}, direct=True)[0] == 422
    assert integ._deck_request("POST", base + "ptt.on", "", {"args": {"bus": "x"}}, direct=True)[0] == 422
    assert integ._deck_request("POST", base + "ptt.nope", "", None, direct=True)[0] == 404
    assert integ._deck_request("POST", base + "ptt.allOff", "", None, direct=True)[0] == 200
    assert not ic.buses["regie"]["active"]
    # les anciennes routes n'existent plus : 404 ROUTE_NOT_FOUND (jamais NOT_FOUND)
    for old in ("/status", "/ptt/regie/on"):
        st, data, _ = integ._deck_request("GET", "http://127.0.0.1:18221" + old, "", direct=True)
        assert st == 404 and data["error"]["code"] == "ROUTE_NOT_FOUND", (old, st, data)

    del ic.buses["plateau"]                                            # ce serveur ne propose pas Plateau
    e, pid, logs = one_page(tmp_path, {
        "0,0": new_button(label="Régie", actions=[ptt("regie")]),
        "0,1": new_button(label="Plateau", actions=[ptt("plateau")]),
        "0,2": new_button(label="vMix", actions=[ptt("vmix")])},
        {"intercom": {"host": "127.0.0.1", "port": 18221}})

    def vis(k):
        return e.visual(e.button(pid, k))
    assert wait(lambda: e.intercom.reachable and e.intercom.online)
    assert [k for k, _ in e.intercom.bus_choices()] == ["regie", "vmix"]
    # bus non proposé : erreur lisible, la touche le dit
    tap(e, pid, "0,1")
    assert wait(lambda: any(lv == "error" and "pas proposé" in m for lv, m in logs)), logs
    assert "BUS ABSENT" in vis("0,1")["label"]
    time.sleep(0.3)
    assert len([m for _, m in logs if "pas proposé" in m]) == 1, logs   # pas de 2e alerte au relâché
    # bus toujours ouvert : pas de « off » au relâché, pas d'alerte, la touche le dit
    n = len(ic.log)
    tap(e, pid, "0,2")
    assert wait(lambda: ("vmix", "on") in ic.log[n:])
    time.sleep(0.3)
    assert ("vmix", "off") not in ic.log[n:], ic.log
    assert "PERMANENT" in vis("0,2")["label"]
    assert not [m for lv, m in logs if lv in ("warn", "error") and "Toujours" in m]
    # client désarmé : 409 expliqué une seule fois, touche « DÉSARMÉ » grisée
    ic.online = False
    assert wait(lambda: not e.intercom.online)
    assert "DÉSARMÉ" in vis("0,0")["label"] and vis("0,0")["bg"] == OFF_BG
    logs.clear()
    e.press(pid, "0,0", True)
    assert wait(lambda: any(lv == "error" and "désarmé" in m for lv, m in logs)), logs
    e.press(pid, "0,0", False)
    time.sleep(0.4)
    assert len([m for lv, m in logs if lv == "error"]) == 1, logs
    ic.online = True
    # client fermé : « HORS LIGNE », et le message dit quoi faire
    ic.stop()
    assert wait(lambda: e.intercom.reachable is False and "contrôle HTTP" in e.intercom.error, 5), e.intercom.error
    assert "HORS LIGNE" in vis("0,0")["label"]
    logs.clear()
    tap(e, pid, "0,0")
    assert wait(lambda: any(lv == "error" and "contrôle HTTP" in m for lv, m in logs), 5), logs
    e.stop()


def test_intercom_fallback_hotkey(tmp_path):
    e, pid, logs = one_page(tmp_path, {
        "0,0": new_button(label="Régie", actions=[ptt("regie", fallback_keys="shift+a")]),
        "0,1": new_button(label="Plateau", actions=[ptt("plateau", "toggle", fallback_keys="shift+b")])},
        {"intercom": {"host": "127.0.0.1", "port": 18224}})
    assert wait(lambda: e.intercom.reachable is False, 5)
    KEYS.log.clear()
    e.press(pid, "0,0", True)                       # py-intercom ne répond pas : on parle par le raccourci
    assert wait(lambda: ("press", "shift+a") in KEYS.log)
    e.press(pid, "0,0", False)
    assert wait(lambda: ("release", "shift+a") in KEYS.log)
    tap(e, pid, "0,1")
    assert wait(lambda: ("tap", "shift+b") in KEYS.log)
    assert any("raccourci de secours" in m for _, m in logs)
    ic = fakes.FakeIntercom(18224)                  # le client revient : l'API reprend la main
    assert wait(lambda: e.intercom.reachable and e.intercom.online, 6)
    KEYS.log.clear()
    e.press(pid, "0,0", True)
    assert wait(lambda: ic.buses["regie"]["active"])
    e.press(pid, "0,0", False)
    assert wait(lambda: ic.buses["regie"]["active"] is False)
    assert not KEYS.log
    ic.online = False                               # désarmé : le raccourci n'y changerait rien
    assert wait(lambda: not e.intercom.online)
    tap(e, pid, "0,0")
    assert wait(lambda: any("désarmé" in m for _, m in logs))
    time.sleep(0.2)
    assert not KEYS.log
    e.stop()


def test_proxy_bypass(tmp_path):
    fakes.FakeIntercom(18225)
    fakes.FakeJson(18226, {"a": 1})
    names = ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "NO_PROXY", "no_proxy")
    old = {k: os.environ.get(k) for k in names}
    for k in names:
        os.environ.pop(k, None)
    os.environ["HTTP_PROXY"] = os.environ["http_proxy"] = "http://127.0.0.1:9"  # proxy mort
    try:
        status, data, _ = integ._deck_request("GET", "http://127.0.0.1:18225/api/deck/state", "", timeout=2,
                                              direct=True)
        assert status == 200 and "buses" in data
        assert webreq.request("GET", "http://127.0.0.1:18226/")[0] == 200
        assert integ.probe_intercom("127.0.0.1", 18225)[0] is True
    finally:
        for k, v in old.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
    assert all(webreq.is_local_host(h) for h in ("127.0.0.1", "10.0.0.5", "192.168.1.20", "localhost",
                                                 "regie-pc", "cam.local", "::1"))
    assert not any(webreq.is_local_host(h) for h in ("example.com", "8.8.8.8", ""))
    # une réponse 502 non JSON vient d'un proxy, pas de py-intercom
    assert integ.explain_intercom("10.0.0.2", 5080, 502, None)[0] == "proxy"


def test_net_send_and_osc(tmp_path):
    import socket
    import struct
    got = []
    srv = socket.create_server(("127.0.0.1", 18227))

    def accept():
        while True:
            try:
                c, _ = srv.accept()
            except OSError:  # serveur fermé en fin de test
                return
            with c:
                buf = b""
                while chunk := c.recv(4096):
                    buf += chunk
                got.append(buf)
    threading.Thread(target=accept, daemon=True).start()
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp.bind(("127.0.0.1", 18228))
    udp.settimeout(3)
    net = lambda **kw: new_action("net.send", host="127.0.0.1", **kw)  # noqa: E731
    e, pid, logs = one_page(tmp_path, {
        "0,0": new_button(actions=[net(port=18227, payload="PWR ON\\r\\x41")]),
        "0,1": new_button(actions=[net(proto="udp", port=18228, payload="81 01 ff", hex=True)]),
        "0,2": new_button(actions=[new_action("osc", host="127.0.0.1", port=18228, address="/cue/1/go",
                                              args='1 0.5 "deux mots" true s:12')]),
        "0,3": new_button(actions=[net(port=18229, payload="x")]),
        "0,4": new_button(actions=[net(port=18227, payload="zz", hex=True)])})
    tap(e, pid, "0,0")
    assert wait(lambda: got == [b"PWR ON\rA"]), got
    tap(e, pid, "0,1")
    assert udp.recvfrom(1024)[0] == b"\x81\x01\xff"
    tap(e, pid, "0,2")
    expected = (b"/cue/1/go\0\0\0" + b",ifsTs\0\0" + struct.pack(">i", 1) + struct.pack(">f", 0.5)
                + b"deux mots\0\0\0" + b"12\0\0")
    assert udp.recvfrom(1024)[0] == expected
    tap(e, pid, "0,3")                              # rien n'écoute : erreur claire
    assert wait(lambda: any(lv == "error" and "TCP 127.0.0.1:18229" in m and "refusée" in m for lv, m in logs), 5), logs
    tap(e, pid, "0,4")                              # hexadécimal invalide
    assert wait(lambda: any(lv == "error" and "hexadécimal invalide" in m for lv, m in logs)), logs
    assert netsend.decode_payload("a\\tb\\\\c\\q") == b"a\tb\\c\\q"
    assert netsend.parse_osc_args("i:3 f:2 -4 1e3 x") == [3, 2.0, -4, 1000.0, "x"]
    from livedeck.actions import summary
    assert summary(new_action("osc", port=53000, address="/go")) == "OSC 127.0.0.1:53000 /go"
    udp.close()
    srv.close()
    e.stop()


def test_http_json_feedback(tmp_path):
    j = fakes.FakeJson(18230, {"data": {"viewers": 42, "live": True}, "items": [{"state": "on"}]})
    url = "http://127.0.0.1:18230/api"
    e, pid, logs = one_page(tmp_path, {"0,0": new_button(label="Live", feedbacks=[
        fb("http_json", RED, "#ffffff", url=url, path="data.viewers", op=">", value="10", interval=1,
           label_fmt="Vues\\n{value}"),
        fb("http_json", bar=GREEN, url=url, path="items[0].state", op="==", value="ON", interval=1),
        fb("http_json", "#123456", url=url, op="error", interval=1)])})

    def vis():
        return e.visual(e.button(pid, "0,0"))
    assert wait(lambda: vis()["bg"] == RED and vis()["label"] == "Vues\n42", 5), vis()
    assert vis()["bar"] == GREEN
    j.data["data"]["viewers"] = 3
    assert wait(lambda: vis()["bg"] != RED and vis()["label"] == "Live", 5), vis()
    j.status = 500
    assert wait(lambda: vis()["bg"] == "#123456", 5), vis()
    assert json_path({"a": [{"b": 2}]}, "a[0].b") == 2 and json_path({"a": [{"b": 2}]}, "a.0.b") == 2
    assert json_compare("5", ">", "4,5") and json_compare("abc", "contains", "B")
    assert not json_compare("false", "truthy", "") and json_compare(["x", "y"], "contains", "y")
    e.stop()


def test_integration_settings_and_details(tmp_path):
    fakes.FakeIntercom(18231)
    fakes.FakeOrchDeck(18232)
    tally = fakes.FakeTally(18233, False)
    tally.old_firmware = True
    e, pid, logs = one_page(tmp_path, {"0,0": new_button(label="Tallys", actions=[new_action("tally.reconnect")])}, {
        "intercom": {"host": "127.0.0.1", "port": 18231},
        "orchestream": {"url": "127.0.0.1:18232", "token": fakes.FakeOrchDeck.TOKEN},
        "tallies": [{"name": "Cam 1", "host": "127.0.0.1:18233"}]})
    assert not e.intercom.wanted and not e.orchestream.wanted
    w = MainWindow(e)
    dlg = SettingsDialog(e, w)                      # fenêtre ouverte : tout est surveillé, même sans touche
    dlg.show()
    assert wait(lambda: e.intercom.online and e.orchestream.connected, 6)
    dlg._refresh_status()
    assert "En ligne" in dlg.ic_status.text() and "flux en direct" in dlg.orch_status.text(), dlg.orch_status.text()
    assert "Stream Deck plateau" in dlg.orch_status.text()
    # « Tester » essaie les valeurs saisies et classe le résultat
    result = dlg._tests["intercom"][1]
    dlg.ic_port.setValue(18234)
    dlg._test("intercom")
    assert wait(lambda: "contrôle HTTP" in result.text(), 6), result.text()
    dlg.ic_port.setValue(18231)
    dlg._test("intercom")
    assert wait(lambda: "en ligne" in result.text(), 6), result.text()
    dlg._test("orchestream")
    assert wait(lambda: "accepté" in dlg._tests["orchestream"][1].text(), 6), dlg._tests["orchestream"][1].text()
    dlg._test("tallies")
    assert wait(lambda: "pas connecté à vMix" in dlg._tests["tallies"][1].text(), 6)
    dlg.orch_token.setText("osk_autre")
    dlg._save()
    assert e.cfg["integrations"]["orchestream"]["token"] == "osk_autre"
    assert not e.intercom.forced and not e.orchestream.forced
    # bibliothèque : intégrations d'après les services réglés
    from livedeck import presets
    lib = [it for c, items in presets.library(e) if c in presets.TOOL_NAMES for it in items]
    assert {"talk:regie", "deck:orchestream", "tallies"} <= {pid_ for pid_, _, _ in lib}, lib  # jeton changé
    # tally à l'ancien firmware : la reconnexion explique quoi faire
    tap(e, pid, "0,0")
    assert wait(lambda: any(lv == "error" and "firmware" in m for lv, m in logs), 5), logs
    w.quit_app()


# -- interface : colonne de droite, touches vides, Take, moniteur -------------------------------------------
LONG = "Libellé de touche vraiment très long pour vérifier que l'en-tête passe à la ligne"


def ui_window(tmp_path, buttons: dict | None = None, integrations: dict | None = None):
    """Fenêtre principale sur une page 5×3 (moteur démarré, faux vMix), à la taille minimale."""
    cfg = default_config()
    pg = cfg["pages"][0]
    pg.update(cols=5, rows=3)
    pg["buttons"].update(buttons or {})
    cfg["integrations"] = {"intercom": {"host": "127.0.0.1", "port": 18199}, "orchestream": {"url": ""},
                           "tallies": [], **(integrations or {})}
    e = engine(tmp_path, cfg=cfg, start=False)
    w = MainWindow(e)
    e.start()
    w.resize(w.minimumSize())
    w.show()
    assert wait(lambda: e.vmix.state.connected and e.vmix.state.inputs)
    return e, w, pg["id"]


def overflow(ins) -> int:
    """De combien le contenu de l'inspecteur dépasse la largeur visible (0 = rien de caché)."""
    for _ in range(4):
        app.processEvents()
    return max(0, ins.inner.minimumSizeHint().width() - ins.scroll.viewport().width(),
               ins.scroll.horizontalScrollBar().maximum())


def test_inspector_fits_narrowest_column(tmp_path):
    from livedeck.actions import ACTIONS
    from livedeck.feedback import CONDITIONS
    from livedeck.model import normalize_button
    e, w, pid = ui_window(tmp_path, integrations={"tallies": [
        {"name": "Tally Plateau Caméra 3 Grand Angle Côté Jardin", "host": "127.0.0.1:18298"}]})
    ins = w.inspector
    assert w.minimumSizeHint().width() <= w.minimumWidth(), w.minimumSizeHint()
    w.split.setSizes([0, 10000, 0])                 # colonnes latérales à leur largeur minimale
    wait(lambda: False, 0.1)
    assert ins.width() == ins.minimumWidth() == 360, ins.width()
    assert w.library.minimumSizeHint().width() <= w.library.width(), w.library.minimumSizeHint()
    bad = {}

    def check(name, b=None, side="actions"):
        e.set_button(pid, "0,0", b)
        w.grid.select("0,0")
        ins.load(pid, "0,0", force=True)
        if side != "actions":
            ins._set_side(side)
        ins.scroll.verticalScrollBar().setValue(0)
        over = overflow(ins)
        if over:
            bad[name] = over

    check("touche vide")
    for t in ACTIONS:
        a = new_action(t)
        if "input" in a:
            a["input"] = "Un titre d'input vMix interminable qui n'existe pas du tout"
        a.update({k: v for k, v in {"url": "http://192.168.100.200:8080/une/route/tres/longue?avec=des&param=1",
                                    "keys": "ctrl+shift+alt+f12", "then": "Stinger1"}.items() if k in a})
        check(t, normalize_button({**new_button(label=LONG), "actions": [a]}))
    check("interrupteur", normalize_button({**new_button(mode="toggle"), "actions": [new_action("vmix.preview")],
                                            "actions_off": [new_action("vmix.program")]}), "actions_off")
    check("appui long", normalize_button({**new_button(confirm=True), "actions": [new_action("vmix.take")],
                                          "long_actions": [new_action("intercom.ptt")]}), "long_actions")
    for t, spec in CONDITIONS.items():
        cond = {"type": t, **{f["key"]: f["default"] for f in spec["fields"]}}
        check("retour " + t, normalize_button({**new_button(label=LONG), "actions": [new_action("tally.reconnect")],
                                               "feedbacks": [fb(t, RED, "#ffffff", **{k: v for k, v in cond.items()
                                                                                      if k != "type"})]}))
    assert not bad, f"contenu plus large que la colonne (px) : {bad}"
    # une molette inclinée ne décale jamais la colonne
    assert ins.scroll.horizontalScrollBar().maximum() == 0
    # la largeur choisie est gardée d'une session à l'autre (fichier ui.ini à côté de la config, pas le registre)
    w.split.setSizes([300, 700, 520])
    w._save_ui()
    assert (Path(tmp_path) / "ui.ini").exists()
    w.quit_app()


def test_empty_key_picker_and_take(tmp_path):
    from livedeck import presets
    from livedeck.actions import next_press, to_take
    e, w, pid = ui_window(tmp_path)
    ins = w.inspector
    w.grid.select("0,0")
    # touche vide : toutes les familles, intégrations comprises, par catégorie et avec recherche
    cats = [c for c, _ in ins.picker_items()]
    assert cats[:3] == ["Vidéo", "py-intercom", "orchestream"] and set(presets.TOOL_NAMES) <= set(cats) and {"Production", "Clavier", "Web", "Réseau", "Deck"} <= set(cats)
    ids = {pid_ for _, items in ins.picker_items() for pid_, *_ in items}
    assert {"take", "tallies", "talk_alloff", "http", "osc", "page_next"} <= ids
    assert any(i.startswith("talk:") for i in ids) and {"deck:orchestream", "deck:multiview"} <= ids
    ins._pick_filter(text="intercom")
    assert {c for c, _ in ins.picker_items()} == {"py-intercom"}
    names = [n for _, items in ins.picker_items() for _, n, _, _ in items]
    assert "Raccourci maintenu" not in names        # le PTT clavier n'est pas confondu avec py-intercom
    ins._pick_filter(text="", cat="Clavier")
    assert [c for c, _ in ins.picker_items()] == ["Clavier"]
    setup = dict((p, s) for _, items in presets.catalog(e) for p, _, _, s in items)
    assert setup["deck:orchestream"] == "orchestream" and setup["deck:multiview"] == "", setup
    ins._pick_filter(cat="")
    # une intégration à régler : la touche est créée et l'inspecteur montre son état + « Réglages… »
    ins._create("deck:orchestream")
    assert e.button(pid, "0,0")["actions"][0]["type"] == "deck.key"
    assert ins._integ_lbls and "orchestream" in ins._integ_lbls[0][0].text()
    # Take : créé depuis une tuile, il demande l'input ; le prochain appui est annoncé en direct
    w.grid.select("0,1")
    ins._create("take")
    wait(lambda: False, 0.1)
    combo = ins.findChildren(InputCombo)[0]
    combo.combo.hidePopup()
    assert ins.focus_input(popup=False) is combo
    st = e.vmix.state
    title, _ = free_input(e)
    combo._picked(combo.combo.findData(title))
    b = e.button(pid, "0,1")
    assert b["label"] == title and ins.h1.text() == title
    assert "preview" in next_press(e, b)
    tap(e, pid, "0,1")
    assert wait(lambda: st.preview() == st.number(title))
    assert wait(lambda: "ANTENNE" in ins.next_lbl.text()), ins.next_lbl.text()
    assert "Prochain appui" in w.grid.tooltip_text("0,1")
    # Preview : l'option « 2e appui » et sa durée ; conversion en Take (une touche, une rangée)
    e.set_button(pid, "1,0", new_button(label="A", bg="#003300", actions=[new_action("vmix.preview", input=title)]))
    e.set_button(pid, "1,1", new_button(label="B", actions=[new_action("vmix.program", input="Reporter", mix=2)]))
    w.grid.select("1,0")
    def labels():
        app.processEvents()
        return [lb.text() for lb in ins.inner.findChildren(QLabel) if lb.isVisibleTo(ins.inner)]
    assert "2e appui" in labels() and "Durée" not in labels(), labels()
    ins._set_field("then", "Fade")
    assert "Durée" in labels()
    assert to_take(new_button(mode="toggle", actions=[new_action("vmix.preview")])) is None
    assert w._row_takes("1,0") == ["1,0", "1,1"]
    w.convert_to_take(w._row_takes("1,0"))
    a0, a1 = e.button(pid, "1,0")["actions"][0], e.button(pid, "1,1")["actions"][0]
    assert (a0["type"], a0["effect"], a0["input"]) == ("vmix.take", "Fade", title)
    assert (a1["type"], a1["mix"]) == ("vmix.take", 2) and e.button(pid, "1,0")["bg"] != "#003300"
    e.undo()
    assert e.button(pid, "1,1")["actions"][0]["type"] == "vmix.program"   # une seule étape « annuler »
    # « Variables… » : jetons proposés et insérés dans le texte
    w.grid.select("0,1")
    toks = [t for _, items in ins.var_tokens() for _, t in items]
    assert "$(vmix:program_title)" in toks and f"$(vmix:remaining:{title})" in toks
    ins.label_edit.clear()
    ins.insert_token("$(vmix:program_title)")
    assert wait(lambda: e.visual(e.button(pid, "0,1"))["label"] == st.title(st.program()))
    # Live : vue d'exploitation, « Tester » disponible
    w.set_live(True)
    app.processEvents()
    assert ins.locked and ins.next_lbl is not None
    assert not [c for c in ins.inner.findChildren(QPlainTextEdit) if c.isVisibleTo(ins.inner)]
    w.set_live(False)
    w.quit_app()


def test_rows_pages_monitor_and_import_options(tmp_path):
    e, w, pid = ui_window(tmp_path, {"2,4": new_button(label="Talk", actions=[new_action("intercom.ptt")])})
    titles = [i["title"] for i in e.vmix.state.inputs[:4]]
    # remplir une rangée : 4 inputs à partir de 1,2 → la fin passe à la rangée suivante
    w.fill_row("1,2", titles, "take", keep=False)
    assert [e.button(pid, k)["actions"][0]["input"] for k in ("1,2", "1,3", "1,4", "2,0")] == titles
    assert e.button(pid, "1,2")["actions"][0]["type"] == "vmix.take"
    # pastilles d'intégration : seulement celles qu'une touche utilise
    w._update_pills()                               # fenêtre étroite : une pastille « Intégrations »
    assert w.integ_all.isVisibleTo(w) and "py-intercom" in w.integ_all.toolTip()
    w.resize(1600, 900)
    w._update_pills()
    assert w.integ_pills["intercom"].isVisibleTo(w) and not w.integ_pills["tallies"].isVisibleTo(w)
    assert "py-intercom" in w.integ_pills["intercom"].toolTip() and not w.integ_all.isVisibleTo(w)
    # bibliothèque « Boutons » : recherche (les en-têtes sans résultat disparaissent)
    lib = w.library.presets
    lib.search.setText("stinger")
    shown = [lib.list.item(r) for r in range(lib.list.count()) if not lib.list.item(r).isHidden()]
    heads = [it.data(Qt.UserRole + 4) for it in shown if it.data(Qt.UserRole + 2)]
    assert heads == ["Vidéo"] and len(shown) == 3, heads
    assert not set(presets.TOOL_NAMES) & {lib.list.item(r).data(Qt.UserRole + 4) for r in range(lib.list.count())}
    # … les outils ont leur onglet : un titre par outil (avec son état), ses rubriques, un filtre par outil
    tools = w.library.tools

    def visible(kind=None):
        its = [tools.list.item(r) for r in range(tools.list.count()) if not tools.list.item(r).isHidden()]
        return [it.data(Qt.UserRole + 4) for it in its if kind is None or it.data(Qt.UserRole + 2) == kind]

    assert visible("tool") == presets.TOOL_NAMES, visible("tool")
    tools.search.setText("talkback")
    assert visible("tool") == ["py-intercom"] and visible("sub") == ["Talkback"] and len(visible("")) >= 1
    tools.search.clear()
    w.library.show_tools("Tally32")
    assert w.library.stack.currentWidget() is tools and visible("tool") == ["Tally32"] and visible("") == ["Tallys"]
    tools.show_tool("")
    lib.search.clear()
    lib.search.clear()
    # info-bulle d'une touche du deck : ce qu'elle fait et ce qui cloche
    e.set_button(pid, "0,0", new_button(label="X", actions=[new_action("vmix.program", input="Inconnu au bataillon")]))
    assert "introuvable" in w.grid.tooltip_text("0,0") and "vide" in w.grid.tooltip_text("0,1")
    # export / import d'une page : nouveaux identifiants, nom distinct
    path = str(Path(tmp_path) / "page.json")
    w.write_page(path, pid)
    new = w.read_page(path)
    assert new and new != pid and len(e.page(new)["buttons"]) == len(e.page(pid)["buttons"])
    assert e.page(new)["name"].endswith("(importée)")
    assert not {b["id"] for b in e.page(new)["buttons"].values()} & {b["id"] for b in e.page(pid)["buttons"].values()}
    # moniteur : badge d'erreurs non lues, alerte en toast, filtre
    w.mon_btn.setChecked(False)
    e.log("error", "panne de test")
    e.log("warn", "alerte de test")
    e.log("action", "action de test")
    assert wait(lambda: w.unread == {"error": 1, "warn": 1} and "2" in w.mon_btn.text())
    assert "alerte de test" in w.hint.text()
    w.resize(w.minimumSize())                       # badge + pastille d'intégration : l'en-tête tient à 1200 px
    w._update_pills()
    assert w.minimumSizeHint().width() <= w.minimumWidth(), w.minimumSizeHint()
    w.mon_btn.setChecked(True)
    assert w.mon_btn.text() == "Moniteur"
    w.mon_filter.setCurrentIndex(w.mon_filter.findData("problems"))
    text = w.monitor.toPlainText()
    assert "panne de test" in text and "alerte de test" in text and "action de test" not in text
    # import Companion : options « 2e appui » et intégrations transmises
    cfg, _ = import_companion(FIXTURE, preview_then="Cut")
    assert any(a.get("then") == "Cut" for p in cfg["pages"] for b in p["buttons"].values() for a in b["actions"])
    from livedeck.ui.dialogs import ImportOptionsDialog
    dlg = ImportOptionsDialog(w)
    dlg.then.setCurrentIndex(dlg.then.findData("Fade"))
    dlg.integrations.setChecked(True)
    assert dlg.options() == {"preview_then": "Fade", "integrations": True}
    # notes de migration d'une vieille config : montrées une fois dans le moniteur
    e.cfg["_migration_notes"] = ["Touche convertie"]
    w._show_migration_notes()
    assert "_migration_notes" not in e.cfg and "Touche convertie" in w.monitor.toPlainText() + "".join(
        line for _, line in w._entries)
    w.quit_app()


def test_long_words_and_settings_scroll(tmp_path):
    """Un mot long (PERMANENT) rapetisse au lieu d'être coupé ; les onglets des réglages défilent au lieu de se
    chevaucher dans la plus petite fenêtre."""
    from PIL import Image, ImageDraw
    from PySide6.QtWidgets import QScrollArea
    from livedeck.render import _layout
    draw = ImageDraw.Draw(Image.new("RGB", (72, 72)))
    _, lines, _ = _layout(draw, "Intercom\nvMix\nPERMANENT", 0, 64, 56)
    assert lines == ["Intercom", "vMix", "PERMANENT"], lines
    e = engine(tmp_path, start=False)
    sd = SettingsDialog(e, tab="integ")
    sd.resize(sd.minimumSize())
    sd.show()
    app.processEvents()
    area = sd.stack.currentWidget()
    assert isinstance(area, QScrollArea) and area.widget().minimumSizeHint().height() > 0
    assert area.horizontalScrollBarPolicy() == Qt.ScrollBarAlwaysOff
    sd.reject()
    e.stop()



def test_toggle_confirm_and_unlatch(tmp_path):
    e, ic, dm, d, h, pa, pb = ptt_engine(tmp_path, 18241)
    KEYS.log.clear()
    # interrupteur sécurisé : un appui bref ne fait rien, un maintien de ½ s bascule
    e.set_button(pa, "2,0", new_button(mode="toggle", confirm=True, actions=[new_action("hotkey", keys="f13")]))
    bid = e.button(pa, "2,0")["id"]
    e.press(pa, "2,0", True)
    time.sleep(0.05)
    e.press(pa, "2,0", False)
    time.sleep(0.8)
    assert not KEYS.log and not e.toggles.get(bid), KEYS.log
    e.press(pa, "2,0", True)
    assert wait(lambda: ("tap", "f13") in KEYS.log, 2)
    e.press(pa, "2,0", False)
    assert e.toggles.get(bid)
    # interrupteur verrouillé (talkback, touche maintenue) puis supprimé ou changé : tout est relâché
    talk = new_action("intercom.ptt", bus="regie", mode="hold")
    e.set_button(pa, "2,1", new_button(mode="toggle", actions=[talk]))
    e.set_button(pa, "2,2", new_button(mode="toggle", actions=[new_action("hotkey", keys="ctrl", hold=True)]))
    e.set_button(pa, "2,3", new_button(mode="toggle", actions=[new_action("intercom.ptt", bus="plateau", mode="hold")]))
    for k in ("2,1", "2,2", "2,3"):
        tap(e, pa, k)
    assert wait(lambda: ic.buses["regie"]["active"] and ic.buses["plateau"]["active"])
    assert wait(lambda: ("press", "ctrl") in KEYS.log)
    e.set_button(pa, "2,1", None)
    e.set_button(pa, "2,2", None)
    b3 = e.button(pa, "2,3")
    b3["mode"] = "press"
    e.set_button(pa, "2,3", b3)
    assert wait(lambda: not ic.buses["regie"]["active"] and not ic.buses["plateau"]["active"])
    assert wait(lambda: ("release", "ctrl") in KEYS.log)
    assert not e._latched and not e.toggles.get(b3["id"])
    # un interrupteur verrouillé qu'on renomme reste verrouillé
    e.set_button(pa, "2,4", new_button(mode="toggle", actions=[talk]))
    tap(e, pa, "2,4")
    assert wait(lambda: ic.buses["regie"]["active"])
    b4 = e.button(pa, "2,4")
    b4["label"] = "Renommé"
    e.set_button(pa, "2,4", b4)
    time.sleep(0.4)
    assert ic.buses["regie"]["active"] and e._latched
    e.stop()
    assert not ic.buses["regie"]["active"]


def test_unplug_one_deck_keeps_other_talkback(tmp_path):
    e, ic, dm, d, h, pa, pb = ptt_engine(tmp_path, 18242)
    e.set_button(pa, "1,0", new_button(actions=[new_action("intercom.ptt", bus="plateau", mode="hold")]))
    e.press(pa, "0,0", True, deck="DECK_B")
    e.press(pa, "1,0", True, deck="DECK_A")
    assert wait(lambda: ic.buses["regie"]["active"] and ic.buses["plateau"]["active"])
    e.release_all(deck="DECK_A")                    # un seul deck débranché
    assert wait(lambda: not ic.buses["plateau"]["active"])
    time.sleep(0.4)
    assert ic.buses["regie"]["active"] and ("all", "off") not in ic.log
    e.press(pa, "0,0", False, deck="DECK_B")
    assert wait(lambda: not ic.buses["regie"]["active"])
    e.stop()


def test_slow_intercom_fallback_closes_bus(tmp_path):
    ic = fakes.FakeIntercom(18243)
    e, pid, logs = one_page(tmp_path, {"0,0": new_button(label="Régie", actions=[ptt("regie", fallback_keys="shift+a")])},
                            {"intercom": {"host": "127.0.0.1", "port": 18243}})
    assert wait(lambda: e.intercom.reachable and e.intercom.online)
    KEYS.log.clear()
    ic.slow = [0.8]                                 # client occupé : le « on » est appliqué en retard
    e.press(pid, "0,0", True)
    assert wait(lambda: ("press", "shift+a") in KEYS.log)
    time.sleep(0.1)
    e.press(pid, "0,0", False)
    assert wait(lambda: ("release", "shift+a") in KEYS.log)
    assert wait(lambda: ("regie", "off") in ic.log, 3)
    time.sleep(1.5)
    assert not ic.buses["regie"]["active"], ic.log
    assert any("à temps" in m for _, m in logs), logs
    # réconciliation : un bus en mode bascule rouvert par l'opérateur dans le client reste ouvert
    assert wait(lambda: e.intercom.reachable)
    e.intercom.command("plateau", "on")
    e.intercom.command("plateau", "off")
    ic.buses["plateau"]["active"] = True
    time.sleep(1.5)
    assert ic.buses["plateau"]["active"] and ic.log.count(("plateau", "off")) == 1, ic.log
    e.stop()


def test_unreadable_config_is_never_overwritten(tmp_path):
    from livedeck import model
    p = Path(tmp_path) / "config.json"
    cfg = default_config()
    cfg["pages"][0]["buttons"]["0,0"] = new_button(label="Garde-moi")
    p.write_text(config_text(cfg), encoding="utf-8")
    before = p.read_bytes()
    real_open, calls = open, []

    def locked(path, *a, **kw):                     # fichier verrouillé (antivirus, OneDrive…)
        if Path(path) == p and not a:
            calls.append(1)
            raise PermissionError(13, "Permission denied")
        return real_open(path, *a, **kw)
    model.open = locked
    try:
        e = Engine(p, enable_decks=False)
    finally:
        del model.open
    assert e.load_warning and e.save_blocked and len(calls) == 5
    e.set_button(e.pages[0]["id"], "1,1", new_button(label="x"))
    e.stop()
    assert p.read_bytes() == before and e.save_error
    flaky = [1]                                     # verrou bref : la lecture suivante passe

    def once(path, *a, **kw):
        if Path(path) == p and flaky and not a:
            flaky.pop()
            raise PermissionError(13, "Permission denied")
        return real_open(path, *a, **kw)
    model.open = once
    try:
        e2 = Engine(p, enable_decks=False)
    finally:
        del model.open
    assert not e2.load_warning and not e2.save_blocked
    assert e2.pages[0]["buttons"]["0,0"]["label"] == "Garde-moi"


def test_review_small_fixes(tmp_path):
    import socket
    from livedeck import presets
    from livedeck.health import JsonPoller
    from livedeck.ui.widgets import typo
    # booléens JSON : « == true », « == oui », « != false »
    assert json_compare(True, "==", "true") and json_compare(True, "==", "True") and json_compare(True, "==", "oui")
    assert json_compare(False, "==", "false") and json_compare(False, "!=", "true")
    assert not json_compare(True, "==", "0")
    # UDP / OSC vers « localhost » : reçu par un récepteur IPv4
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp.bind(("127.0.0.1", 18244))
    udp.settimeout(3)
    netsend.send("udp", "localhost", 18244, netsend.osc_message("/go", []))
    assert udp.recvfrom(1024)[0].startswith(b"/go")
    udp.close()
    # réponse JSON de plus de 64 Ko
    fakes.FakeJson(18245, {"items": [{"n": i, "name": "x" * 50} for i in range(1500)], "live": True})
    jp = JsonPoller(lambda: None)
    url = "http://127.0.0.1:18245/"
    jp.targets = {url: 5}
    jp._fetch(url)
    assert jp.results[url]["ok"] and jp.results[url]["data"]["live"] is True, jp.results[url]
    # bus « toujours ouvert » : décrit comme tel et marqué, pas proposé comme un talkback maintenu
    fakes.FakeIntercom(18246)
    e, pid, logs = one_page(tmp_path, {"0,0": new_button(actions=[ptt("regie", mode="alloff")])},
                            {"intercom": {"host": "127.0.0.1", "port": 18246}})
    assert wait(lambda: e.intercom.buses)
    tiles = {p: (d, s) for _, items in presets.catalog(e) for p, _, d, s in items}
    assert tiles["talk:vmix"][1] == "always_on" and "toujours ouvert" in tiles["talk:vmix"][0]
    assert tiles["talk:plateau"] == ("Talkback · bascule", "") and tiles["talk:regie"][1] == ""
    # import « Ajouter les pages » : les réglages trouvés par la conversion sont repris
    imp = {"integrations": {"orchestream": {"url": "http://orch.local"},
        "tallies": [{"name": "Cam 1", "host": "10.0.0.11"}, {"name": "Cam 2", "host": "10.0.0.12"}]}}
    cur = {"integrations": {"orchestream": {"url": ""}, "tallies": [{"name": "A", "host": "10.0.0.11"}]}}
    notes = integ.merge_integrations(cur, imp)
    assert cur["integrations"]["orchestream"]["url"] == "http://orch.local" and len(notes) == 2, notes
    assert [t["host"] for t in cur["integrations"]["tallies"]] == ["10.0.0.11", "10.0.0.12"]
    assert typo("F1 : aide « x »") == "F1 : aide « x »"
    e.stop()
    # « couper tous les bus » : ni bus ni raccourci de secours dans l'éditeur
    e, w, pid = ui_window(tmp_path, {"0,0": new_button(actions=[ptt("regie", mode="alloff")]),
                                     "0,1": new_button(actions=[ptt("regie")])})
    ins = w.inspector

    def texts():
        app.processEvents()
        return [lb.text() for lb in ins.findChildren(QLabel) if lb.isVisibleTo(ins)]
    ins.load(pid, "0,1", force=True)
    assert "Bus" in texts() and any(t.startswith("Raccourci de secours") for t in texts())
    ins.load(pid, "0,0", force=True)
    assert not any(t == "Bus" or t.startswith("Raccourci de secours") for t in texts()), texts()
    from livedeck.ui.inspector import Tile               # description de tuile sur 2 lignes, sans « … »
    tile = Tile("Take", "1er appui preview, 2e antenne")
    tile.resize(130, tile.height())
    tile.show()
    app.processEvents()
    lines = tile.shown_lines()
    assert not any("…" in t for t in lines) and len(lines) == 3, lines
    tile.close()
    w.quit_app()


def test_wheel_scrolls_column_not_fields(tmp_path):
    from PySide6.QtCore import QPoint, QPointF
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtWidgets import QComboBox
    acts = [new_action(t) for t in ("vmix.program", "vmix.preview", "vmix.overlay", "vmix.audio")]
    e, w, pid = ui_window(tmp_path, {"0,0": new_button(label="Molette", actions=acts)})
    w.grid.select("0,0")
    ins = w.inspector
    bar = ins.scroll.verticalScrollBar()
    assert wait(lambda: bar.maximum() > 0), "l'inspecteur doit défiler pour ce test"
    combos = [c for c in ins.inner.findChildren(QComboBox)
              if c.isVisible() and c.count() > 1 and c.currentIndex() < c.count() - 1]
    assert combos
    bar.setValue(0)
    for c in combos[:3]:
        before, pos = c.currentIndex(), bar.value()
        mid = QPointF(c.rect().center())
        ev = QWheelEvent(mid, QPointF(c.mapToGlobal(c.rect().center())), QPoint(0, 0), QPoint(0, -120),
                         Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
        app.sendEvent(c, ev)
        app.processEvents()
        assert c.currentIndex() == before, (c, before, c.currentIndex())
        assert bar.value() > pos or bar.value() == bar.maximum(), (pos, bar.value())
    e.stop()
    w.close()


# -- Loupedeck ------------------------------------------------------------------
def test_loupedeck_protocol(tmp_path):
    """Poignée de main, trames et décodage des contrôles, y compris après une session mal refermée."""
    e = engine(tmp_path, start=False)
    # Rebut typique laissé dans le tampon : acquittements puis trame de fermeture WebSocket.
    junk = b"\x82\x03\x03\x02@\x82\x03\x03\x02A\x88\x02\x03\xe8"
    lm, port, h = mock_loupedeck(e, junk=junk)
    assert port.flushed, "les tampons doivent être vidés avant la poignée de main"
    assert h.dev.connected and h.serial == "FAKECT0000001"
    assert wait(lambda: h.dev.firmware == "1.2.3", 3), h.dev.firmware
    assert (h.cols, h.rows, h.size) == (4, 3, 90)

    got = []
    h.dev.on_event = got.append
    port.button("key3")
    port.button("key3", down=False)
    port.rotate("knobBR", -3)
    port.touch(*key_xy(6))
    port.touch(120, 120, wheel=True)
    assert wait(lambda: len(got) >= 5, 3), got
    assert got[0] == {"type": "button", "id": "key3", "down": True, "serial": h.serial}
    assert got[1]["down"] is False
    assert got[2] == {"type": "rotate", "id": "knobBR", "delta": -3, "serial": h.serial}
    assert got[3]["target"] == {"screen": "center", "key": 6, "row": 1, "col": 2}, got[3]
    assert got[4]["target"] == {"screen": "wheel"}, got[4]
    # Les bandeaux latéraux encadrent le pavé central sur la même dalle physique.
    assert h.dev.target(20, 100) == {"screen": "left"}
    assert h.dev.target(450, 100) == {"screen": "right"}
    assert h.dev.panels() == {b"\x00M": (480, 270), b"\x00W": (240, 240)}
    e.stop()


def test_loupedeck_touch_release_follows_finger(tmp_path):
    """Un doigt qui glisse hors de la vignette doit quand même la relâcher : sinon un bouton
    intercom « maintenu » laisserait le micro ouvert en direct."""
    e, pid, logs = one_page(tmp_path, {
        "0,0": new_button(label="Cam 1", actions=[new_action("vmix.preview", input="Cam 1")]),
        "1,2": new_button(label="Talk", actions=[new_action("hotkey", keys="shift+z", hold=True)])})
    lm, port, h = mock_loupedeck(e)
    h.page_id = pid
    since = len(KEYS.log)

    # 1) appui franc sur une vignette : appuyé puis relâché normalement
    port.touch(*key_xy(0), finger=7)
    assert wait(lambda: e.pressed, 3), "l'appui n'est pas remonté au moteur"
    port.touch(*key_xy(0), finger=7, end=True)
    assert wait(lambda: not e.pressed, 3), "le relâché n'est pas remonté"

    # 2) le doigt part d'une vignette « maintenue » et se lève sur le bandeau droit
    port.touch(*key_xy(6), finger=8)
    assert wait(lambda: held_keys(since) == ["shift+z"], 3), "le raccourci maintenu n'a pas démarré"
    port.touch(455, 20, finger=8, end=True)          # relâché AILLEURS que sur la vignette
    assert wait(lambda: not held_keys(since), 3), "touche clavier restée enfoncée après un glissement"
    assert wait(lambda: not e.pressed, 3), f"bouton resté enfoncé : {e.pressed}"
    assert not e._active, f"appui bloqué dans le moteur : {list(e._active)}"
    assert not h.fingers, h.fingers

    # 3) un doigt qui n'a jamais touché le pavé ne déclenche rien
    port.touch(20, 50, finger=9)
    port.touch(20, 90, finger=9, end=True)
    time.sleep(0.2)
    assert not e.pressed and not e._active
    e.stop()


def test_loupedeck_pages_and_hub(tmp_path):
    """Le CT affiche une page comme un Stream Deck, navigue avec fnL/fnR, et le DeckHub
    présente les deux familles derrière une seule API."""
    e = engine(tmp_path, start=False)
    e.cfg["pages"] = [new_page("CT A", cols=4, rows=3), new_page("CT B", cols=4, rows=3),
                      new_page("XL", cols=8, rows=4)]
    e.cfg["pages"][0]["buttons"]["0,1"] = new_button(label="Cam 2")
    e.start()
    lm, port, h = mock_loupedeck(e)
    a, b = e.cfg["pages"][0]["id"], e.cfg["pages"][1]["id"]
    assert h.page_id == a, "le CT doit prendre la première page à sa taille (4×3), pas la page XL"

    lm.refresh()
    assert lm.stats["images"] == 12, lm.stats          # 12 vignettes dessinées
    drawn = [c for c in port.commands if c[0] == ld.CMD_FRAMEBUFF]
    assert len(drawn) == 12 and all(d[1][:2] == b"\x00M" for d in drawn), len(drawn)
    assert sum(1 for c in port.commands if c[0] == ld.CMD_DRAW) == 1, "une seule bascule d'écran"
    # Aucun contrôle n'est configuré : les bandeaux et la molette, déjà noirs, ne reçoivent rien.
    assert not [c for c in drawn if c[1][:2] == b"\x00W"]
    before = len(drawn)
    lm.refresh()                                       # rien n'a changé : le cache évite tout renvoi
    assert len([c for c in port.commands if c[0] == ld.CMD_FRAMEBUFF]) == before

    lm.goto(h.serial, "next")                          # navigation (via une action « page »)
    assert wait(lambda: h.page_id == b, 3), h.page_id
    lm.goto(h.serial, "prev")
    assert wait(lambda: h.page_id == a, 3), h.page_id
    assert [p["id"] for p in e.pages if (p["cols"], p["rows"]) == (8, 4)], "la page XL reste disponible"

    # Le hub agrège Stream Deck et Loupedeck sans que l'interface voie la différence.
    dm, d, sdh = mock_deck(e)
    hub = DeckHub(e, [dm, lm])
    e.decks = hub
    models = {i["model"] for i in hub.info()}
    assert models == {"Stream Deck Original", "Loupedeck CT"}, models
    assert hub.error == "", hub.error
    assert hub.stats["images"] >= 12
    hub.set_brightness(h.serial, 40)                   # diffusé : seul le propriétaire réagit
    assert wait(lambda: any(c[0] == ld.CMD_BRIGHTNESS for c in port.commands), 3)
    hub.diag_handler = lambda *a: None
    assert dm.diag_handler is not None and lm.diag_handler is not None
    hub.diag_handler = None
    e.stop()


def test_loupedeck_unplug_releases_keys(tmp_path):
    """Appareil débranché pendant un appui maintenu : la touche clavier doit être relâchée."""
    e, pid, logs = one_page(tmp_path, {
        "0,0": new_button(label="Talk", actions=[new_action("hotkey", keys="shift+z", hold=True)])})
    lm, port, h = mock_loupedeck(e)
    h.page_id = pid
    since = len(KEYS.log)
    port.touch(*key_xy(0), finger=3)
    assert wait(lambda: held_keys(since) == ["shift+z"], 3), "le raccourci maintenu n'a pas démarré"
    port.is_open = False                               # câble arraché
    h.dev._fail()
    with mock.patch.object(ld, "discover", return_value=[]):
        lm._scan()
    assert not lm.handles, "l'appareil disparu doit être retiré"
    assert wait(lambda: not held_keys(since), 3), "touche clavier restée enfoncée après débranchement"
    e.stop()


def test_loupedeck_model_sizes():
    assert deck_size("Loupedeck CT") == (4, 3)
    assert deck_size("Loupedeck Live") == (4, 3)
    assert deck_size("Loupedeck Live S") == (5, 3)
    assert deck_size("Razer Stream Controller") == (4, 3)
    assert deck_size("Razer Stream Controller X") == (5, 3)
    assert deck_size("Stream Deck XL") == (8, 4), "les modèles Elgato ne changent pas"
    assert deck_size("Stream Deck +") == (4, 2)
    assert set(ld.PROFILES) == {0x0003, 0x0004, 0x0006, 0x0007, 0x0D06, 0x0D09}
    ct = ld.PROFILES[0x0007]
    assert (ct.cols, ct.rows, ct.keys) == (4, 3, 12)
    assert ct.displays["wheel"]["big_endian"] is True, "l'écran de la molette est gros-boutiste"
    assert len(ct.buttons) == 20 and len(ct.knobs) == 6
    # Loupedeck annonce « 20 boutons RGB » : les 8 ronds + les 12 du bas, pas les molettes.
    leds = {c["id"] for c in ld.controls(ct) if c.get("led")}
    assert leds == set(ct.buttons) and len(leds) == 20, leds
    assert not any(ld.has_led(k) for k in ("knobTL", "wheel")), "une molette n'a pas de diode"
    # « keyboard » commence par « key » sans être un bouton rond.
    assert not ld.is_round("keyboard") and ld.is_round("key7")
    assert ld.control_kind("wheel") == "rotary" and ld.control_kind("key0") == "button"
    groups = {c["id"]: c["group"] for c in ld.controls(ct)}
    assert groups["keyboard"] == "Boutons" and groups["key0"] == "Boutons ronds"
    assert len([c for c in ld.controls(ct) if c["group"] == "Bandeaux"]) == 6


def test_loupedeck_controls_actions(tmp_path):
    """Molettes, bandeaux et boutons physiques déclenchent de vraies actions, avec accélération."""
    e = engine(tmp_path, start=False)
    page = new_page("CT", cols=4, rows=3)
    page["controls"] = {
        "knobTR": new_control("rotary", label="Volume", step=3,
                              actions_cw=[new_action("vmix.function", function="AudioPlus",
                                                     params="Input=Magneto&Value=$(delta_abs)")],
                              actions_ccw=[new_action("vmix.function", function="AudioMinus",
                                                      params="Input=Magneto&Value=$(delta_abs)")],
                              actions=[new_action("vmix.function", function="AudioOff", params="Input=Magneto")]),
        "stripL2": new_control("button", label="Bandeau",
                               actions=[new_action("vmix.function", function="Fade")]),
        "key2": new_control("button", label="Rond 3",
                            actions=[new_action("vmix.function", function="Cut")]),
        "wheel": new_control("rotary", label="Master",
                             actions_cw=[new_action("vmix.function", function="SetMasterVolume",
                                                    params="Value=$(delta)")],
                             actions_ccw=[new_action("vmix.function", function="SetMasterVolume",
                                                     params="Value=$(delta)")]),
    }
    e.cfg["pages"] = [page]
    e.start()
    lm, port, h = mock_loupedeck(e)
    sent = vmix_server()
    names = lambda since: [n for n, _ in sent.functions[since:]]  # noqa: E731

    # 1) Le pas de 3 : deux crans ne déclenchent rien, le troisième oui, et le reste est gardé.
    before = len(sent.functions)
    port.rotate("knobTR", 1)
    port.rotate("knobTR", 1)
    time.sleep(0.4)
    assert not names(before), "le pas de 3 doit retenir les deux premiers crans"
    port.rotate("knobTR", 1)
    assert wait(lambda: names(before), 3), "le 3e cran doit déclencher"
    assert sent.functions[before][0] == "AudioPlus", sent.functions[before]
    assert sent.functions[before][1].get("Value") == "1", sent.functions[before]

    # 2) Sens inverse : l'autre liste, et $(delta_abs) sans le signe.
    before = len(sent.functions)
    for _ in range(3):
        port.rotate("knobTR", -1)
    assert wait(lambda: names(before), 3)
    assert sent.functions[before][0] == "AudioMinus", sent.functions[before]
    assert sent.functions[before][1].get("Value") == "1", "delta_abs ne doit pas porter le signe"

    # 3) $(delta) garde le signe sur la molette centrale (pas de 1).
    before = len(sent.functions)
    port.rotate("wheel", -1)
    assert wait(lambda: names(before), 3)
    assert sent.functions[before][0] == "SetMasterVolume"
    assert sent.functions[before][1].get("Value") == "-1", sent.functions[before]

    # 4) Appui sur la molette : la liste « actions », comme une touche.
    before = len(sent.functions)
    port.button("knobTR")
    port.button("knobTR", down=False)
    assert wait(lambda: "AudioOff" in names(before), 3), names(before)

    # 5) Zone de bandeau : le y décide de la zone touchée (3 zones de 90 px).
    assert ld.strip_zone("L", 10) == "stripL1" and ld.strip_zone("L", 135) == "stripL2"
    assert ld.strip_zone("R", 260) == "stripR3"
    before = len(sent.functions)
    port.touch(20, 135, finger=2)                      # bandeau gauche, zone du milieu
    port.touch(20, 135, finger=2, end=True)
    assert wait(lambda: "Fade" in names(before), 3), names(before)

    # 6) Bouton rond physique
    before = len(sent.functions)
    port.button("key2")
    port.button("key2", down=False)
    assert wait(lambda: "Cut" in names(before), 3), names(before)

    # 7) Un bouton sans contrôle affecté ne fait rien du tout
    e.cfg["pages"].append(new_page("CT 2", cols=4, rows=3))
    before = len(sent.functions)
    port.button("fnR")
    port.button("fnR", down=False)
    time.sleep(0.3)
    assert h.page_id == page["id"] and len(sent.functions) == before, "un bouton libre doit rester inerte"
    e.stop()


def test_loupedeck_controls_render(tmp_path):
    """Bandeaux, molette centrale et diodes RGB suivent le retour d'état, sans renvoi inutile."""
    e = engine(tmp_path, start=False)
    page = new_page("CT", cols=4, rows=3)
    page["controls"] = {
        "stripR1": new_control("button", label="PGM", bg="#dc2626"),
        "wheel": new_control("rotary", label="Master", bg="#0d9488"),
        "key0": new_control("button", label="Rond", bg="#2563eb"),
    }
    e.cfg["pages"] = [page]
    e.start()
    lm, port, h = mock_loupedeck(e)
    lm.refresh()
    fb = [c for c in port.commands if c[0] == ld.CMD_FRAMEBUFF]
    assert any(c[1][:2] == b"\x00W" for c in fb), "l'écran de la molette doit être dessiné"
    right = [c for c in fb if c[1][:2] == b"\x00M" and c[1][2:10] == struct.pack(">HHHH", 420, 0, 60, 270)]
    assert right, "le bandeau droit doit être dessiné à x=420 sur 60×270"
    # La diode du bouton rond prend la couleur du contrôle.
    leds = [c for c in port.commands if c[0] == ld.CMD_COLOR]
    assert any(c[1] == bytes([ld.BUTTON_CODES["key0"], 0x25, 0x63, 0xEB]) for c in leds), leds
    n = len(port.commands)
    lm.refresh()                                        # rien n'a changé : aucun octet de plus
    assert len(port.commands) == n, "le cache des zones doit éviter tout renvoi"
    e.stop()


def test_loupedeck_control_editing(tmp_path):
    """Un contrôle s'édite comme une touche : même inspecteur, même annulation, même stockage."""
    e = engine(tmp_path, start=False)
    e.cfg["pages"] = [new_page("CT", cols=4, rows=3)]
    e.start()
    pid = e.cfg["pages"][0]["id"]
    k = control_key("knobCL")

    # Le moteur écrit dans « controls », pas dans la grille.
    e.set_button(pid, k, new_control("rotary", label="Gain"))
    assert e.cfg["pages"][0]["controls"]["knobCL"]["label"] == "Gain"
    assert k not in e.cfg["pages"][0]["buttons"], "un contrôle ne doit pas polluer la grille"
    assert e.button(pid, k)["label"] == "Gain", "le moteur doit résoudre « c:knobCL »"
    assert (pid, k) in [(p["id"], kk) for p, kk, _ in e.all_buttons()], "absent de all_buttons()"

    w = MainWindow(e)
    w.page_id = pid
    w.controls.set_page(pid)
    assert w.controls.isVisible() or w.controls.profile() is not None
    w._select_control(k)
    assert w.inspector.key == k and w.inspector.b["kind"] == "rotary"
    # Les trois listes d'une molette sont proposées, pas « appui long ».
    w.inspector.side = "actions_cw"
    w.inspector.rebuild()
    w.inspector.b["actions_cw"] = [new_action("vmix.function", function="AudioPlus")]
    w.inspector.commit()
    assert e.cfg["pages"][0]["controls"]["knobCL"]["actions_cw"][0]["function"] == "AudioPlus"
    e.undo()
    assert not e.cfg["pages"][0]["controls"]["knobCL"]["actions_cw"], "annuler doit fonctionner"
    # Supprimer le contrôle le retire du bloc controls.
    e.set_button(pid, k, None)
    assert "knobCL" not in e.cfg["pages"][0]["controls"]
    w.close()
    e.stop()


def test_loupedeck_selection_and_shortcuts(tmp_path):
    """Un contrôle sélectionné se comporte comme une touche pour les raccourcis d'édition.

    Avant correctif, la fenêtre lisait `grid.sel` partout : Ctrl+Z vidait l'inspecteur et
    copier/coller/supprimer ne faisaient rien, sans message."""
    e = engine(tmp_path, start=False)
    page = new_page("CT", cols=4, rows=3)
    page["buttons"]["0,0"] = new_button(label="Cam 1")
    page["controls"] = {"knobTL": new_control("rotary", label="Gain"),
                        "knobBR": new_control("rotary", label="Replay")}
    e.cfg["pages"] = [page]
    e.start()
    pid = page["id"]
    w = MainWindow(e)
    w.page_id = pid
    w.grid.set_page(pid)
    w.controls.set_page(pid)
    kt, kb = control_key("knobTL"), control_key("knobBR")

    # Les deux sélections s'excluent et `sel` désigne toujours la bonne.
    w.grid.select("0,0")
    assert w.sel == "0,0" and w.controls.sel is None
    w._select_control(kt)
    assert w.sel == kt and w.grid.sel is None, (w.sel, w.grid.sel)
    assert w._selected()["label"] == "Gain"

    # Ctrl+Z doit garder le contrôle dans l'inspecteur, pas le vider.
    w.inspector.load(pid, kt)
    e.set_button(pid, kt, {**e.button(pid, kt), "label": "Gain 2"})
    w.undo()
    assert w.inspector.key == kt, "annuler ne doit pas vider l'inspecteur d'un contrôle"
    assert w.inspector.b and w.inspector.b["label"] == "Gain", w.inspector.b

    # Copier un contrôle puis le coller sur un autre : la nature (molette) est conservée.
    w._select_control(kt)
    w.copy()
    assert w.clipboard and w.clipboard["label"] == "Gain"
    w._select_control(kb)
    w.paste()
    assert e.cfg["pages"][0]["controls"]["knobBR"]["label"] == "Gain"
    assert e.cfg["pages"][0]["controls"]["knobBR"]["kind"] == "rotary", "coller doit garder la molette"
    assert e.cfg["pages"][0]["controls"]["knobBR"]["id"] != e.cfg["pages"][0]["controls"]["knobTL"]["id"]

    # Supprimer agit bien sur le contrôle, et pas sur la grille.
    w._select_control(kb)
    w.delete_selected()
    assert "knobBR" not in e.cfg["pages"][0]["controls"]
    assert e.cfg["pages"][0]["buttons"]["0,0"]["label"] == "Cam 1", "la grille ne doit pas bouger"

    # Dupliquer n'a pas de sens pour un organe physique : on le dit au lieu de ne rien faire.
    w._select_control(kt)
    before = json.dumps(e.cfg, sort_keys=True)
    w.duplicate_selected()
    assert json.dumps(e.cfg, sort_keys=True) == before, "dupliquer ne doit rien changer"

    # Échap libère les deux sélections.
    w.clear_selection()
    assert w.sel is None
    w.close()
    e.stop()


def test_loupedeck_search_finds_controls(tmp_path):
    """Ctrl+F trouve les contrôles comme les touches, et les sélectionne correctement."""
    e = engine(tmp_path, start=False)
    page = new_page("CT", cols=4, rows=3)
    page["buttons"]["0,0"] = new_button(label="Cam 1")
    page["controls"] = {"knobTL": new_control("rotary", label="Gain micro")}
    e.cfg["pages"] = [page]
    e.start()
    pid = page["id"]
    dlg = SearchDialog(e, None)
    keys = [dlg.list.item(i).data(ROLE) for i in range(dlg.list.count())]
    assert (pid, control_key("knobTL")) in keys, keys
    assert (pid, "0,0") in keys, keys
    # Recherche par libellé du contrôle…
    dlg._filter("gain micro")
    shown = [dlg.list.item(i).data(ROLE) for i in range(dlg.list.count())
             if not dlg.list.item(i).isHidden()]
    assert shown == [(pid, control_key("knobTL"))], shown
    # …et par le nom de l'organe physique.
    dlg._filter("molette haut gauche")
    shown = [dlg.list.item(i).data(ROLE) for i in range(dlg.list.count())
             if not dlg.list.item(i).isHidden()]
    assert shown == [(pid, control_key("knobTL"))], shown
    dlg.close()

    # Le choix d'un contrôle depuis la recherche le sélectionne sans toucher la grille.
    w = MainWindow(e)
    w.page_id = pid
    w.grid.set_page(pid)
    w.controls.set_page(pid)
    w.select(control_key("knobTL"))
    assert w.sel == control_key("knobTL") and w.grid.sel is None
    w.close()
    e.stop()


def test_loupedeck_surface_view(tmp_path):
    """La vue dessine la surface Loupedeck (molettes, bandeaux, molette centrale) et la détecte
    toute seule quand un Loupedeck affiche la page. Le Stream Deck, lui, ne change pas."""
    e = engine(tmp_path, start=False)
    sd = new_page("XL", cols=8, rows=4)
    ct = new_page("CT", cols=4, rows=3, surface="loupedeck")
    auto = new_page("Auto", cols=4, rows=3)                 # surface déduite de l'appareil
    e.cfg["pages"] = [sd, ct, auto]
    e.start()
    g = DeckGrid(e)
    g.resize(1100, 800)

    # Stream Deck : rien n'a changé, uniquement des touches de grille.
    g.set_page(sd["id"])
    assert g.surface == "streamdeck"
    kinds = {}
    for _, (_, kind) in g._layout().items():
        kinds[kind] = kinds.get(kind, 0) + 1
    assert kinds == {"key": 32}, kinds

    # Loupedeck imposé par la page : toute la surface est cliquable.
    g.set_page(ct["id"])
    assert g.surface == "loupedeck"
    lay = g._layout()
    kinds = {}
    for _, (_, kind) in lay.items():
        kinds[kind] = kinds.get(kind, 0) + 1
    # 8 boutons ronds sous le pavé + 2 blocs de 6 de part et d'autre de la molette centrale.
    assert kinds == {"key": 12, "strip": 6, "knob": 6, "round": 20, "wheel": 1}, kinds
    for cid in ("knobTL", "knobBR", "wheel", "stripL1", "stripR3", "key0", "key7", "fnL", "e"):
        assert control_key(cid) in lay, cid

    # La molette centrale est bien centrée, avec 6 boutons à sa gauche et 6 à sa droite.
    wheel = lay[control_key("wheel")][0]
    keys_mid = (lay["0,0"][0].left() + lay["0,3"][0].right()) / 2
    assert abs(wheel.center().x() - keys_mid) < 2, (wheel.center().x(), keys_mid)
    # Proportions de l'appareil réel : la molette fait 240 px pour des touches de 90 px.
    key_w = lay["0,0"][0].width()
    assert abs(wheel.width() / key_w - 240 / 90) < 0.05, (wheel.width(), key_w)
    strip = lay[control_key("stripL2")][0]
    assert abs(strip.width() / key_w - 60 / 90) < 0.05, (strip.width(), key_w)
    # Boîtier presque carré (150 × 160 mm), colonne d'état de LiveDeck exclue.
    body = g._geom()[6]
    assert 0.88 < body.width() / body.height() < 1.0, (body.width(), body.height())
    assert body.right() < lay[control_key("knobTR")][0].right() + key_w, "les molettes sont au bord"
    left = [c for c in BOTTOM_L if lay[control_key(c)][0].center().x() < wheel.left()]
    right = [c for c in BOTTOM_R if lay[control_key(c)][0].center().x() > wheel.right()]
    assert len(left) == 6 and len(right) == 6, (left, right)
    # Chaque bloc fait bien 2 colonnes × 3 rangées.
    for names in (BOTTOM_L, BOTTOM_R):
        xs = {round(lay[control_key(c)][0].center().x()) for c in names}
        ys = {round(lay[control_key(c)][0].center().y()) for c in names}
        assert len(xs) == 2 and len(ys) == 3, (names, xs, ys)

    # Le pointage retrouve chaque nature au bon endroit (centre du rectangle).
    for cid in ("knobTL", "wheel", "stripR2", "key3", "save"):
        assert g.key_at(lay[control_key(cid)][0].center()) == control_key(cid), cid
    assert g.key_at(lay["0,0"][0].center()) == "0,0"
    # Un clic hors d'une molette ronde, mais dans son rectangle, ne la sélectionne pas.
    r = lay[control_key("knobTL")][0]
    assert g.key_at(r.topLeft() + QPointF(1, 1)) != control_key("knobTL")

    # Aucun chevauchement entre zones : un clic ne peut pas être ambigu.
    rects = [(k, v[0]) for k, v in lay.items()]
    for i, (k1, r1) in enumerate(rects):
        for k2, r2 in rects[i + 1:]:
            inter = r1.intersected(r2)
            assert inter.width() <= 1 or inter.height() <= 1, f"{k1} et {k2} se chevauchent"

    # Détection automatique : un CT branché qui affiche la page suffit.
    g.set_page(auto["id"])
    assert g.surface == "streamdeck", "sans appareil, on reste sur la vue Stream Deck"
    lm, port, h = mock_loupedeck(e)
    h.page_id = auto["id"]
    e.decks = DeckHub(e, [lm])
    assert g.surface == "loupedeck", "un Loupedeck affichant la page doit changer la vue"

    # Le dessin ne lève pas et les info-bulles des contrôles non plus.
    auto["controls"] = {"knobTL": new_control("rotary", label="Gain",
                                              actions_cw=[new_action("vmix.function", function="AudioPlus")],
                                              step=4)}
    g.grab()
    tip = g.tooltip_text(control_key("knobTL"))
    assert "Gain" in tip and "Rotation" in tip and "4 crans" in tip, tip
    assert "Molette haut droite" in g.tooltip_text(control_key("knobTR")), "libellé de l'organe libre"
    g.grab()

    # Une molette n'accepte ni un déplacement de touche ni une rangée d'inputs.
    md = QMimeData()
    md.setData("application/x-livedeck-inputs", b"[]")
    assert not g._accepts(control_key("knobTL"), md)
    assert g._accepts("0,0", md)
    md2 = QMimeData()
    md2.setData("application/x-livedeck-preset", b"take")
    assert g._accepts(control_key("knobTL"), md2), "un preset peut être déposé sur une molette"
    e.stop()


def test_selected_control_outlined_in_view(tmp_path):
    """Le contrôle suivi ou sélectionné doit être entouré de blanc dans la vue, comme une touche.

    Avant correctif, sélectionner un contrôle vidait `grid.sel` : la vue ne savait plus quoi
    entourer, et rien ne montrait ce qu'on éditait."""
    e = engine(tmp_path, start=False)
    ct = new_page("CT", cols=4, rows=3, surface="loupedeck")
    sd = new_page("XL", cols=8, rows=4)
    e.cfg["pages"] = [ct, sd]
    e.start()
    w = MainWindow(e)
    w.page_id = ct["id"]
    w.grid.set_page(ct["id"])
    w.controls.set_page(ct["id"])
    k = control_key("knobTL")
    assert w.grid.draws(k) and w.grid.draws("0,0")

    w._select_control(k, silent=True)                  # comme « suivre le deck »
    assert w.grid.sel == k, "la vue doit porter la sélection pour pouvoir l'entourer"
    assert w.grid._outline(k) is not None, "pas de liseré autour du contrôle suivi"
    assert w.grid._outline(k).color().name() == "#ffffff", w.grid._outline(k).color().name()
    assert w.sel == k and w.inspector.key == k
    # Une touche de la grille reprend la main proprement.
    w.grid.select("0,0")
    assert w.sel == "0,0" and w.grid._outline(k) is None

    # Un appui sur l'appareil éclaire aussi le contrôle (flash), puisque la vue le dessine.
    w.follow.setChecked(True)
    w._activity(ct["id"], k, "CT")
    assert w.grid.sel == k and k in w.grid._flash, w.grid._flash

    # Page Stream Deck : le contrôle n'est pas dessiné, la sélection revient aux pastilles.
    w.page_id = sd["id"]
    w.grid.set_page(sd["id"])
    w.controls.set_page(sd["id"])
    assert not w.grid.draws(k)
    w._select_control(k, silent=True)
    assert w.controls.sel == k and w.grid.sel is None and w.sel == k
    w.close()
    e.stop()


def test_free_buttons_do_nothing(tmp_path):
    """Aucun bouton n'a de rôle caché : sans action affectée, il ne doit RIEN faire — surtout pas
    changer de page, ce qui se découvrirait en appuyant par accident en plein direct."""
    e = engine(tmp_path, start=False)
    ct = new_page("CT", cols=4, rows=3, surface="loupedeck")
    other = new_page("CT 2", cols=4, rows=3, surface="loupedeck")
    e.cfg["pages"] = [ct, other]
    e.start()
    lm, port, h = mock_loupedeck(e)
    h.page_id = ct["id"]
    sent = vmix_server()
    n = len(sent.functions)

    # Un par un : « page suivante » puis « page précédente » se compenseraient et masqueraient
    # le défaut si on les enchaînait avant de vérifier.
    for cid in ("fnL", "fnR", "home", "undo", "keyboard", "enter", "save", "a", "e", "key0", "key7"):
        port.button(cid)
        port.button(cid, down=False)
        time.sleep(0.15)
        assert h.page_id == ct["id"], f"« {cid} » a changé la page tout seul : {h.page_id}"
    assert len(sent.functions) == n, sent.functions[n:]
    assert not e.pressed and not e._active

    # Pour naviguer, on pose explicitement l'action sur le bouton de son choix.
    e.decks = DeckHub(e, [lm])
    e.set_button(ct["id"], control_key("fnR"),
                 new_control("button", actions=[new_action("page", page="next")]))
    port.button("fnR")
    port.button("fnR", down=False)
    assert wait(lambda: h.page_id == other["id"], 3), h.page_id

    # Et rien dans l'éditeur n'annonce de rôle par défaut.
    g = DeckGrid(e)
    g.resize(1000, 900)
    g.set_page(ct["id"])
    for cid in ("fnL", "save", "home"):
        assert "page" not in g.tooltip_text(control_key(cid)).lower(), cid
    g.grab()
    e.stop()


def test_loupedeck_controlbar_hidden_on_surface(tmp_path):
    """Les pastilles ne doivent pas doubler la surface quand celle-ci dessine déjà les contrôles."""
    e = engine(tmp_path, start=False)
    ct = new_page("CT", cols=4, rows=3, surface="loupedeck")
    ct["controls"] = {"knobTL": new_control("rotary", label="Gain")}
    plain = new_page("XL", cols=8, rows=4)
    plain["controls"] = {"knobTL": new_control("rotary", label="Gain")}
    e.cfg["pages"] = [ct, plain]
    e.start()
    bar = ControlBar(e)
    bar.set_page(ct["id"])
    assert bar.on_surface() and bar.isHidden(), "la surface dessine déjà les molettes"
    bar.set_page(plain["id"])
    assert not bar.on_surface(), "page Stream Deck : les contrôles n'ont pas de place sur la vue"
    e.stop()


def test_page_model_sets_surface(tmp_path):
    """Choisir un modèle Loupedeck doit aussi basculer la vue, sinon le réglage n'a aucun effet
    visible et l'utilisateur croit que ça n'a pas marché."""
    e = engine(tmp_path, start=False)
    e.cfg["pages"] = [new_page("P", cols=8, rows=4)]
    e.start()
    w = MainWindow(e)
    w.page_id = e.cfg["pages"][0]["id"]
    w.grid.set_page(w.page_id)
    w._set_page_model(*DECK_SIZES["Loupedeck CT / Live (4×3)"], surface_of("Loupedeck CT / Live (4×3)"))
    p = e.page(w.page_id)
    assert (p["cols"], p["rows"], p["surface"]) == (4, 3, "loupedeck"), p
    assert w.grid.surface == "loupedeck"
    w._set_page_model(*DECK_SIZES["Stream Deck XL (8×4)"], surface_of("Stream Deck XL (8×4)"))
    assert e.page(w.page_id)["surface"] == "streamdeck"
    assert w.grid.surface == "streamdeck"
    assert surface_of("Razer Stream Controller") == "loupedeck"
    assert surface_of("Stream Deck MK.2") == "streamdeck"
    w.close()
    e.stop()


def test_follow_deck_selects_empty_keys(tmp_path):
    """« Suivre le deck » doit sélectionner même une touche ou une molette SANS action : c'est
    ainsi qu'on câble une surface, en appuyant l'organe qu'on veut configurer."""
    e = engine(tmp_path, start=False)
    page = new_page("CT", cols=4, rows=3, surface="loupedeck")
    page["buttons"]["0,0"] = new_button(label="Cam 1")        # une seule touche câblée
    e.cfg["pages"] = [page]
    e.start()
    pid = page["id"]
    seen = []
    e.listeners.append(lambda kind, p: seen.append(p) if kind == "activity" else None)

    # Touche de grille vide, appuyée sur l'appareil.
    e.press(pid, "2,3", True, deck="DECK1")
    e.press(pid, "2,3", False, deck="DECK1")
    assert (pid, "2,3", "DECK1") in seen, seen
    # Une touche câblée ne doit pas être signalée deux fois.
    seen.clear()
    e.press(pid, "0,0", True, deck="DECK1")
    e.press(pid, "0,0", False, deck="DECK1")
    assert seen.count((pid, "0,0", "DECK1")) == 1, seen
    # Un appui depuis l'écran (sans deck) ne doit rien changer au comportement existant.
    seen.clear()
    e.press(pid, "1,1", True)
    assert not seen, "une touche vide cliquée à l'écran ne déclenche pas d'activité"

    # Contrôles Loupedeck libres : bouton, molette tournée, bouton rond.
    lm, port, h = mock_loupedeck(e)
    h.page_id = pid
    seen.clear()
    port.button("key5")
    assert wait(lambda: (pid, control_key("key5"), h.serial) in seen, 3), seen
    port.rotate("knobCR", 1)
    assert wait(lambda: (pid, control_key("knobCR"), h.serial) in seen, 3), seen
    port.touch(20, 135, finger=4)                              # zone libre d'un bandeau
    assert wait(lambda: (pid, control_key("stripL2"), h.serial) in seen, 3), seen

    # fnL / fnR n'ont aucun rôle réservé : ils se sélectionnent comme les autres, et la page
    # ne change pas toute seule.
    e.cfg["pages"].append(new_page("CT 2", cols=4, rows=3, surface="loupedeck"))
    seen.clear()
    port.button("fnR")
    assert wait(lambda: (pid, control_key("fnR"), h.serial) in seen, 3), seen
    assert h.page_id == pid, "un bouton libre ne doit pas changer de page"

    # L'éditeur suit : la molette libre devient la sélection courante.
    w = MainWindow(e)
    w.page_id = pid
    w.grid.set_page(pid)
    w.controls.set_page(pid)
    w.follow.setChecked(True)
    w._activity(pid, control_key("knobTL"), h.serial)
    assert w.sel == control_key("knobTL") and w.inspector.key == control_key("knobTL")
    w._activity(pid, "2,3", h.serial)
    assert w.sel == "2,3" and w.controls.sel is None, "retour sur la grille : le contrôle est libéré"
    w.close()
    e.stop()


# -- Réglages au cran (molettes) et Windows ------------------------------------
def _reset_vmix_audio():
    s = vmix_server()
    with s.state_lock:
        s.audio["master"].update(volume=80.0, headphones=70.0)
        for b in "ABCDEFG":
            s.audio[b]["volume"] = 100.0
        for n in s.volume:
            s.volume[n] = 100.0
    return s


def _knob_page(e, controls):
    page = new_page("CT", cols=4, rows=3, surface="loupedeck")
    page["controls"] = controls
    e.cfg["pages"] = [page]
    return page["id"]


def test_vmix_audio_state_parsed(tmp_path):
    """Le master, le casque et les bus A-G sont lus dans le XML de vMix."""
    _reset_vmix_audio()
    e = engine(tmp_path)
    st = e.vmix.state
    assert wait(lambda: st.audio.get("master"), 3), st.audio
    assert st.audio["master"]["volume"] == 80.0 and st.audio["master"]["headphones"] == 70.0, st.audio
    assert set(st.audio) == {"master", *"ABCDEFG"}, st.audio
    assert st.audio["C"] == {"volume": 100.0, "muted": False}
    e.stop()


def test_adjust_rapid_ticks_are_not_lost(tmp_path):
    """Rotation rapide : chaque cran compte, même si vMix n'a pas encore renvoyé son état.

    Sans la « valeur optimiste », chaque cran relirait 80 et le volume finirait à 82 au lieu de 90."""
    s = _reset_vmix_audio()
    e = engine(tmp_path)
    assert wait(lambda: e.vmix.state.audio.get("master"), 3)
    pid = _knob_page(e, {"knobTL": new_control(
        "rotary", label="Master", actions_cw=[new_action("vmix.adjust", target="master", amount=2)])})
    for _ in range(5):
        e.rotate(pid, "knobTL", 1, deck="CT")          # 5 crans d'affilée, sans attendre
    assert wait(lambda: s.audio["master"]["volume"] == 90.0, 3), s.audio["master"]
    # Sens inverse avec la MÊME liste : l'action au cran vaut pour les deux sens.
    for _ in range(3):
        e.rotate(pid, "knobTL", -1, deck="CT")
    assert wait(lambda: s.audio["master"]["volume"] == 84.0, 3), s.audio["master"]
    # Butée : jamais au-delà de 100.
    for _ in range(20):
        e.rotate(pid, "knobTL", 1, deck="CT")
    assert wait(lambda: s.audio["master"]["volume"] == 100.0, 3), s.audio["master"]
    n = len(s.functions)
    e.rotate(pid, "knobTL", 1, deck="CT")                # déjà à 100 : rien n'est envoyé
    time.sleep(0.3)
    assert not [f for f in s.functions[n:] if f[0] == "SetMasterVolume"], s.functions[n:]
    e.stop()


def test_adjust_targets(tmp_path):
    """Chaque cible envoie la bonne commande vMix, avec la bonne valeur."""
    s = _reset_vmix_audio()
    e = engine(tmp_path)
    st = e.vmix.state
    assert wait(lambda: st.audio.get("master"), 3)
    first = st.inputs[0]["title"]
    video = next(i for i in st.inputs if i.get("duration"))
    pid = _knob_page(e, {
        "knobTL": new_control("rotary", actions_cw=[new_action("vmix.adjust", target="input_volume",
                                                               input=first, amount=5)]),
        "knobCL": new_control("rotary", actions_cw=[new_action("vmix.adjust", target="bus", bus="C", amount=3)]),
        "knobBL": new_control("rotary", actions_cw=[new_action("vmix.adjust", target="headphones", amount=4)]),
        "knobTR": new_control("rotary", actions_cw=[new_action("vmix.adjust", target="preview", amount=1)]),
        "knobCR": new_control("rotary", actions_cw=[new_action("vmix.adjust", target="list",
                                                               input=first, amount=1)]),
        "knobBR": new_control("rotary", actions_cw=[new_action("vmix.adjust", target="countdown",
                                                               input=first, amount=10)]),
        "wheel": new_control("rotary", actions_cw=[new_action("vmix.adjust", target="position",
                                                              input=video["title"], amount=2)]),
    })

    def last(name, since):
        return next((q for f, q in reversed(s.functions[since:]) if f == name), None)

    n = len(s.functions)
    e.rotate(pid, "knobTL", -2, deck="CT")              # input : 100 − 2×5
    assert wait(lambda: last("SetVolume", n), 3)
    assert last("SetVolume", n)["Value"] == "90", last("SetVolume", n)
    e.rotate(pid, "knobCL", -1, deck="CT")
    assert wait(lambda: last("SetBusCVolume", n), 3)
    assert last("SetBusCVolume", n)["Value"] == "97"
    e.rotate(pid, "knobBL", 1, deck="CT")
    assert wait(lambda: last("SetHeadphonesVolume", n), 3)
    assert last("SetHeadphonesVolume", n)["Value"] == "74"

    # Preview : input suivant, bloqué aux extrémités (jamais de saut du dernier au premier).
    with s.state_lock:
        s.mixes[1]["preview"] = 2
    assert wait(lambda: st.preview(1) == 2, 3)
    e.rotate(pid, "knobTR", 1, deck="CT")
    assert wait(lambda: last("PreviewInput", n), 3)
    assert last("PreviewInput", n)["Input"] == "3", last("PreviewInput", n)
    m = len(s.functions)
    e.rotate(pid, "knobTR", -50, deck="CT")
    assert wait(lambda: last("PreviewInput", m), 3)
    assert last("PreviewInput", m)["Input"] == str(min(i["number"] for i in st.inputs))

    # Liste : NextItem / PreviousItem répétés autant que de crans.
    m = len(s.functions)
    e.rotate(pid, "knobCR", -3, deck="CT")
    assert wait(lambda: len([f for f, _ in s.functions[m:] if f == "PreviousItem"]) == 3, 3), s.functions[m:]

    # Compte à rebours : relatif natif, signe conservé.
    m = len(s.functions)
    e.rotate(pid, "knobBR", -1, deck="CT")
    assert wait(lambda: last("AdjustCountdown", m), 3)
    assert last("AdjustCountdown", m)["Value"] == "-10"

    # Position : borne à 0, en millisecondes.
    m = len(s.functions)
    e.rotate(pid, "wheel", -100, deck="CT")
    assert wait(lambda: last("SetPosition", m), 3)
    assert last("SetPosition", m)["Value"] == "0", last("SetPosition", m)
    e.stop()


def test_relative_fallback_only_for_relative_actions(tmp_path):
    """Une action quelconque placée d'un seul côté NE doit PAS se déclencher dans l'autre sens :
    seul un réglage au cran a un sens dans les deux directions."""
    s = _reset_vmix_audio()
    e = engine(tmp_path)
    pid = _knob_page(e, {"knobTL": new_control(
        "rotary", actions_cw=[new_action("vmix.function", function="Cut")])})
    n = len(s.functions)
    e.rotate(pid, "knobTL", -1, deck="CT")
    time.sleep(0.3)
    assert not [f for f, _ in s.functions[n:] if f == "Cut"], "un Cut ne doit pas partir à l'envers"
    e.rotate(pid, "knobTL", 1, deck="CT")
    assert wait(lambda: [f for f, _ in s.functions[n:] if f == "Cut"], 3)
    assert is_relative(new_action("vmix.adjust")) and is_relative(new_action("win.scroll"))
    assert is_relative(new_action("win.audio", mode="adjust"))
    assert not is_relative(new_action("win.audio", mode="toggle")), "couper n'est pas un réglage au cran"
    e.stop()


def test_level_feedback_gauge(tmp_path):
    """La molette affiche la jauge et la valeur de ce qu'elle règle, et suit un changement fait
    dans vMix (sans action de LiveDeck)."""
    s = _reset_vmix_audio()
    e = engine(tmp_path)
    assert wait(lambda: e.vmix.state.audio.get("master"), 3)
    pid = _knob_page(e, {"knobTL": new_control(
        "rotary", label="Master", actions_cw=[new_action("vmix.adjust", target="master", amount=2)],
        actions=[new_action("vmix.function", function="MasterAudio")])})
    c = e.control(pid, "knobTL")
    v = e.visual(c)
    assert v["progress"] == 0.8 and v["label"] == "Master\n80 %", v
    with s.state_lock:
        s.audio["master"]["volume"] = 35.0                 # changé dans vMix
    states = []
    e.listeners.append(lambda kind, p: states.append(kind) if kind == "state" else None)
    assert wait(lambda: e.visual(e.control(pid, "knobTL"))["label"] == "Master\n35 %", 4)
    assert wait(lambda: "state" in states, 3), "le rafraîchissement doit signaler le nouveau niveau"
    e.stop()


def test_win_audio_actions(tmp_path):
    """Son Windows : monter / baisser au cran, fixer, couper ; application absente signalée."""
    e = engine(tmp_path)
    fake = e.winaudio.backend
    logs = []
    e.listeners.append(lambda kind, p: logs.append(p) if kind == "log" else None)
    pid = _knob_page(e, {
        "knobTL": new_control("rotary", label="Win", actions_cw=[new_action("win.audio", target="output",
                                                                            mode="adjust", amount=5)],
                              actions=[new_action("win.audio", target="output", mode="toggle")]),
        "knobTR": new_control("rotary", actions_cw=[new_action("win.audio", target="app", app="spotify",
                                                               mode="adjust", amount=10)]),
        "key0": new_control("button", actions=[new_action("win.audio", target="mic", mode="toggle")]),
        "key1": new_control("button", actions=[new_action("win.audio", target="output", mode="set", level=25)]),
        "key2": new_control("button", actions=[new_action("win.audio", target="app", app="absent.exe",
                                                          mode="toggle")]),
    })
    for _ in range(4):
        e.rotate(pid, "knobTL", 1, deck="CT")
    assert wait(lambda: fake.levels["output"] == 70.0, 3), fake.levels   # 50 + 4 × 5, aucun cran perdu
    e.rotate(pid, "knobTR", -1, deck="CT")                  # « spotify » sans .exe : reconnu
    assert wait(lambda: fake.app_levels["spotify.exe"] == 50.0, 3), fake.app_levels
    tap(e, pid, control_key("key0"))
    assert wait(lambda: fake.muted["mic"] is True, 3)
    tap(e, pid, control_key("key0"))
    assert wait(lambda: fake.muted["mic"] is False, 3)
    tap(e, pid, control_key("key1"))
    assert wait(lambda: fake.levels["output"] == 25.0, 3)
    tap(e, pid, control_key("key2"))
    assert wait(lambda: any("absent.exe" in str(p) for p in logs), 3), logs

    # Retours d'état : jauge du volume Windows, et rouge quand c'est coupé.
    v = e.visual(e.control(pid, "knobTL"))
    assert v["progress"] == 0.25 and "25 %" in v["label"], v
    fake.muted["output"] = True                             # coupé ailleurs (clavier, barre des tâches)
    e.winaudio._cache.clear()
    assert e.visual(e.control(pid, "knobTL"))["bg"] == RED
    mic = e.visual(e.control(pid, "key0"))
    assert "progress" in mic and mic["bg"] != RED, "micro actif : pas de rouge"
    e.stop()


def test_win_actions_wiring(tmp_path):
    """Les utilitaires Windows reçoivent les bons arguments (sans rien lancer réellement)."""
    e = engine(tmp_path, start=False)
    calls = []
    saved = {n: getattr(winsys, n) for n in ("launch", "run", "focus_window", "scroll", "type_text", "lock")}
    winsys.launch = lambda t, a="": calls.append(("launch", t, a))
    winsys.run = lambda c, hidden=True: calls.append(("run", c, hidden))
    winsys.focus_window = lambda t: calls.append(("focus", t)) or None   # aucune fenêtre trouvée
    winsys.scroll = lambda dx, dy: calls.append(("scroll", dx, dy))
    winsys.type_text = lambda t: calls.append(("type", t))
    winsys.lock = lambda: calls.append(("lock",))
    logs = []
    e.listeners.append(lambda kind, p: logs.append(p) if kind == "log" else None)
    try:
        execute(e, new_action("win.launch", target="obs64.exe", args="--startrecording"), "down", {})
        execute(e, new_action("win.run", command="echo ok", hidden=False), "down", {})
        execute(e, new_action("win.focus", title="vMix"), "down", {})
        execute(e, new_action("win.scroll", amount=3), "down", {"delta": -2})
        execute(e, new_action("win.scroll", direction="horizontal", amount=1), "down", {"delta": 1})
        execute(e, new_action("win.type", text="Bonjour"), "down", {})
        execute(e, new_action("win.lock"), "down", {})
        execute(e, new_action("win.media", what="next"), "down", {})
    finally:
        for n, f in saved.items():
            setattr(winsys, n, f)
    assert calls == [("launch", "obs64.exe", "--startrecording"), ("run", "echo ok", False), ("focus", "vMix"),
                     ("scroll", 0, -6), ("scroll", 1, 0), ("type", "Bonjour"), ("lock",)], calls
    assert ("tap", "next") in KEYS.log, "la touche média passe par la simulation clavier"
    assert any("vMix" in str(p) for p in logs), "fenêtre introuvable : il faut le dire"


def test_new_actions_catalog():
    """Champs, valeurs par défaut et résumés des nouvelles actions."""
    # Deux champs « Par cran » / « Volume » : des clés distinctes, sinon le second écraserait le premier.
    a = new_action("win.audio")
    assert a["amount"] == 2 and a["level"] == 50, a
    keys = [f["key"] for f in ACTIONS["win.audio"]["fields"]]
    assert len(keys) == len(set(keys)), f"clés en double : {keys}"
    # L'input n'apparaît que pour une cible qui en demande un.
    spec = {f["key"]: f for f in ACTIONS["vmix.adjust"]["fields"]}
    assert not shown(spec["input"], new_action("vmix.adjust", target="master"))
    assert shown(spec["input"], new_action("vmix.adjust", target="input_volume"))
    assert shown(spec["bus"], new_action("vmix.adjust", target="bus"))
    assert not shown(spec["bus"], new_action("vmix.adjust", target="master"))
    for t in [t for t in ACTIONS if t.startswith("win.") or t == "vmix.adjust"]:
        s = summary(new_action(t))
        assert s and s != t, (t, s)
    assert summary(new_action("vmix.adjust", target="bus", bus="B", amount=-3)) == "Volume bus B · -3 % par cran"
    assert summary(new_action("win.audio", target="app", app="spotify.exe", amount=5)) == \
        "spotify.exe · +5 % par cran"


def test_knob_presets_placement(tmp_path):
    """Un preset de molette garde sa rotation sur une molette, et devient une touche normale ailleurs."""
    e = engine(tmp_path, start=False)
    for pid, (cat, name, desc, make) in presets.PRESETS.items():
        b = make()
        assert b and b.get("id"), pid
    knob = presets.make("knob_master", e)
    on_knob = presets.place(knob, control_key("knobTL"))
    assert on_knob["kind"] == "rotary" and on_knob["actions_cw"][0]["type"] == "vmix.adjust"
    on_key = presets.place(knob, "0,0")
    assert "kind" not in on_key, "une touche ordinaire ne doit pas proposer de rotation"
    assert on_key["actions"][0]["function"] == "MasterAudio", "l'appui (couper) reste"
    on_btn = presets.place(knob, control_key("key3"))
    assert on_btn["kind"] == "button"
    # Sur une molette, les réglages au cran sont proposés en premier.
    assert presets.catalog(e, rotary=True)[0][0] == "Molette"
    assert presets.catalog(e)[0][0] != "Molette"
    assert any(c == "Windows" for c, _ in presets.catalog(e))


def test_knob_preset_in_inspector(tmp_path):
    """Choisir « Volume Master » sur une molette l'ouvre sur sa rotation, et changer la cible
    remet un pas adapté (1 input pour la preview, pas 2)."""
    e = engine(tmp_path, start=False)
    pid = _knob_page(e, {})
    e.start()
    w = MainWindow(e)
    w.page_id = pid
    w.grid.set_page(pid)
    w._select_control(control_key("knobTL"))
    with mock.patch.object(ui_widgets, "ask_slot", return_value="both"):
        w.inspector._create("knob_master")
    assert w.inspector.side == "actions_cw", w.inspector.side
    c = e.control(pid, "knobTL")
    assert c["kind"] == "rotary" and c["actions_cw"][0]["target"] == "master"
    w.inspector.step = 0
    w.inspector._set_field("target", "preview")
    assert e.control(pid, "knobTL")["actions_cw"][0]["amount"] == 1, "pas de 1 input pour la preview"
    w.inspector._set_field("target", "master")
    assert e.control(pid, "knobTL")["actions_cw"][0]["amount"] == 2
    w.close()
    e.stop()


def test_loupedeck_open_failure_logged_once(tmp_path):
    """Un échec passager à l'ouverture reste silencieux ; un échec qui dure (logiciel Loupedeck qui
    garde le port) est signalé une seule fois, et non toutes les 2 secondes."""
    e = engine(tmp_path, start=False)
    e.cfg["pages"] = [new_page("CT", cols=4, rows=3)]
    lm = LoupedeckManager(e)
    logs = []
    e.listeners.append(lambda kind, p: logs.append(p) if kind == "log" else None)
    found = [{"path": "COM-X", "vid": 0x2EC2, "pid": 0x0007, "serial": "S1", "model": "Loupedeck CT"}]
    fails = {"n": 0}

    def broken(self, timeout=2.0):
        fails["n"] += 1
        raise OSError("accès refusé")

    def errors():
        return [p for p in logs if "COM-X" in str(p)]

    with mock.patch.object(ld, "discover", return_value=found), \
            mock.patch.object(ld.Loupedeck, "connect", broken):
        lm._scan()
        assert not errors(), "un premier échec passager ne doit pas alarmer"
        for _ in range(10):                              # 20 s de tentatives
            lm._scan()
    assert fails["n"] == 11, "on continue de réessayer"
    assert len(errors()) == 1, f"signalé une seule fois, pas {len(errors())} : {errors()}"
    assert "logiciel Loupedeck" in str(errors()[0]), "le message doit dire quoi faire"
    # Débranché puis rebranché : un nouvel échec persistant est de nouveau signalé.
    with mock.patch.object(ld, "discover", return_value=[]):
        lm._scan()
    with mock.patch.object(ld, "discover", return_value=found), \
            mock.patch.object(ld.Loupedeck, "connect", broken):
        lm._scan()
        lm._scan()
    assert len(errors()) == 2, errors()
    e.stop()


def test_preset_slots_and_assign():
    """Où poser une action sur une molette : mêmes choix partout, seule la recommandation change."""
    cut = presets.make("cut")
    base = ["press", "cw", "ccw"]
    choices, best = presets.slots(cut)
    assert [s for s, _ in choices] == base and best == "press", (choices, best)
    # Un réglage au cran suit le sens de rotation : la rotation est conseillée, mais les choix
    # proposés restent les mêmes — sinon les molettes sembleraient avoir des possibilités différentes.
    choices, best = presets.slots(presets.make("win_vol_up"))
    assert [s for s, _ in choices] == base and best == "cw", (choices, best)
    choices, best = presets.slots(presets.make("knob_scroll"))
    assert [s for s, _ in choices] == base and best == "cw", "réglage sans appui : rotation conseillée"
    choices, best = presets.slots(presets.make("knob_master"))
    assert [s for s, _ in choices] == ["both"] + base and best == "both", choices
    assert presets.slots(presets.make("label"))[0] == [], "texte seul : rien à demander"

    b = presets.assign(cut, "cw")
    assert b["actions_cw"][0]["effect"] == "Cut" and not b["actions"] and not b["actions_ccw"]
    b = presets.assign(cut, "ccw")
    assert b["actions_ccw"][0]["effect"] == "Cut" and not b["actions_cw"] and not b["actions"]
    b = presets.assign(cut, "press")
    assert b["actions"][0]["effect"] == "Cut" and not b["actions_cw"]
    knob = presets.make("knob_master")
    b = presets.assign(knob, "press")
    assert b["actions"][0]["function"] == "MasterAudio" and not b["actions_cw"], "appui seulement"
    b = presets.assign(knob, "turn")
    assert b["actions_cw"][0]["type"] == "vmix.adjust" and not b["actions"], "rotation seulement"
    b = presets.assign(knob, "both")
    assert b["actions_cw"] and b["actions"]
    # Un interrupteur posé sur la rotation redevient un simple déclenchement (pas de 2e appui).
    b = presets.assign(presets.make("toggle_http"), "cw")
    assert b["mode"] == "press" and not b["actions_off"] and b["actions_cw"]
    assert presets.SLOT_SIDE["ccw"] == "actions_ccw" and presets.SLOT_SIDE["press"] == "actions"


def test_assigning_to_knob_asks_where(tmp_path):
    """Choisir une action pour une molette demande « appui, rotation… » au lieu de la poser
    d'office sur l'appui. Une touche ordinaire, elle, ne pose aucune question."""
    e = engine(tmp_path, start=False)
    pid = _knob_page(e, {})
    e.start()
    w = MainWindow(e)
    w.page_id = pid
    w.grid.set_page(pid)
    asked = []

    def answer(slot):
        def fn(parent, choices, title="", recommended=""):
            asked.append((title, [s for s, _ in choices], recommended))
            return slot
        return fn

    # Depuis l'inspecteur : rotation horaire choisie.
    w._select_control(control_key("knobTL"))
    with mock.patch.object(ui_widgets, "ask_slot", answer("cw")):
        w.inspector._create("cut")
    c = e.control(pid, "knobTL")
    assert c["actions_cw"][0]["effect"] == "Cut" and not c["actions"], c
    assert w.inspector.side == "actions_cw", "l'éditeur s'ouvre sur la liste choisie"
    assert asked and asked[0][1] == ["press", "cw", "ccw"] and "Molette haut gauche" in asked[0][0], asked

    # Les 6 encodeurs et la molette centrale proposent EXACTEMENT la même chose : c'est le même
    # type de contrôle, la liste ne doit pas changer de l'un à l'autre.
    seen = {}
    for cid in ("knobTL", "knobCL", "knobBL", "knobTR", "knobCR", "knobBR", "wheel"):
        asked.clear()
        with mock.patch.object(ui_widgets, "ask_slot", answer(None)):
            prepare_preset(w, presets.make("cut", e), control_key(cid))
        seen[cid] = (tuple(asked[0][1]), asked[0][2])
    assert len(set(seen.values())) == 1, seen

    # Annuler la question ne crée rien.
    w._select_control(control_key("knobCL"))
    with mock.patch.object(ui_widgets, "ask_slot", answer(None)):
        w.inspector._create("cut")
    assert e.control(pid, "knobCL") is None, "annulé : aucun contrôle créé"

    # Glisser-déposer depuis la bibliothèque : même question.
    with mock.patch.object(ui_widgets, "ask_slot", answer("press")):
        w._drop_preset(control_key("knobBL"), "fade")
    c = e.control(pid, "knobBL")
    assert c["actions"][0]["effect"] == "Fade" and not c["actions_cw"], c
    assert w.inspector.key == control_key("knobBL") and w.inspector.side == "actions"
    with mock.patch.object(ui_widgets, "ask_slot", answer("ccw")):
        w._drop_preset(control_key("wheel"), "cut")
    assert w.inspector.side == "actions_ccw" and e.control(pid, "wheel")["actions_ccw"]

    # Touche de la grille, bouton rond : un seul emplacement possible, aucune question.
    def never(*a, **k):
        raise AssertionError("aucune question ne doit être posée hors d'une molette")
    with mock.patch.object(ui_widgets, "ask_slot", never):
        w._drop_preset("0,0", "cut")
        w._drop_preset(control_key("key2"), "cut")
    assert e.button(pid, "0,0")["actions"][0]["effect"] == "Cut"
    assert e.control(pid, "key2")["actions"][0]["effect"] == "Cut"
    w.close()
    e.stop()


def test_no_rotation_on_a_button(tmp_path):
    """Un bouton qui ne tourne pas ne doit jamais garder d'action de rotation : elle ne partirait
    jamais, et l'éditeur comme l'info-bulle annonceraient une rotation impossible."""
    e = engine(tmp_path, start=False)
    pid = _knob_page(e, {})
    e.start()
    g = DeckGrid(e)
    g.resize(1000, 900)
    g.set_page(pid)
    for k in (control_key("key5"), control_key("save"), "0,0"):
        b = presets.place(presets.make("knob_master", e), k)
        assert not b.get("actions_cw") and not b.get("actions_ccw"), (k, b)
        assert b["actions"][0]["function"] == "MasterAudio", (k, b)   # l'appui est conservé
        if parse_control(k) is not None:
            assert b["kind"] == "button"
        else:
            assert "kind" not in b and "step" not in b, "une touche de grille n'a pas ces réglages"
    # Un réglage SANS appui ne doit pas disparaître : sa rotation devient l'appui (un cran).
    b = presets.place(presets.make("knob_scroll", e), control_key("key5"))
    assert not b.get("actions_cw") and b["actions"][0]["type"] == "win.scroll", b
    # Une configuration déjà enregistrée avec ce défaut se répare au chargement.
    cfg = default_config()
    p = new_page("CT", cols=4, rows=3)
    p["controls"] = {"key5": new_control("button", label="Master",
                                         actions_cw=[new_action("vmix.adjust", target="master")]),
                     "save": new_control("button", label="X", actions=[new_action("vmix.function",
                                                                                  function="Cut")],
                                         actions_ccw=[new_action("vmix.adjust", target="master")]),
                     "knobTL": new_control("rotary", actions_cw=[new_action("vmix.adjust")])}
    cfg["pages"] = [p]
    cfg = normalize_config(cfg)
    c = cfg["pages"][0]["controls"]
    assert not c["key5"]["actions_cw"] and c["key5"]["actions"][0]["type"] == "vmix.adjust", c["key5"]
    assert not c["save"]["actions_ccw"] and c["save"]["actions"][0]["function"] == "Cut", c["save"]
    assert c["knobTL"]["actions_cw"], "une vraie molette garde sa rotation"

    # L'info-bulle d'un bouton n'annonce aucune rotation.
    e.set_button(pid, control_key("key5"), presets.place(presets.make("knob_master", e), control_key("key5")))
    tip = g.tooltip_text(control_key("key5"))
    assert "Rotation" not in tip, tip
    e.stop()


def test_loupedeck_view_uses_device_grid(tmp_path):
    """Une page plus grande que le pavé de l'appareil ne doit pas faire dessiner des colonnes
    que l'appareil n'a pas : le CT a 4 colonnes, pas 5."""
    e = engine(tmp_path, start=False)
    big = new_page("Régie", cols=5, rows=3, surface="loupedeck")
    for r in range(3):
        for c in range(5):
            big["buttons"][key(r, c)] = new_button(label=f"{r}{c}")
    e.cfg["pages"] = [big]
    e.start()
    g = DeckGrid(e)
    g.resize(1100, 1000)
    g.set_page(big["id"])
    assert g.device_grid() == (4, 3), g.device_grid()
    keys = [k for k, (_, kind) in g._layout().items() if kind == "key"]
    assert len(keys) == 12, f"{len(keys)} touches dessinées au lieu de 12"
    assert "0,4" not in keys and "0,3" in keys, keys
    assert g.hidden_keys() == 3, g.hidden_keys()      # la 5e colonne : 3 touches invisibles
    g.grab()                                           # le pied affiche l'avertissement, sans lever
    # Une page à la taille de l'appareil n'avertit de rien.
    small = new_page("CT", cols=4, rows=3, surface="loupedeck")
    e.cfg["pages"].append(small)
    g.set_page(small["id"])
    assert g.device_grid() == (4, 3) and g.hidden_keys() == 0
    # Et une page Stream Deck garde sa grille complète.
    sd = new_page("XL", cols=8, rows=4)
    e.cfg["pages"].append(sd)
    g.set_page(sd["id"])
    assert g.device_grid() == (8, 4) and g.hidden_keys() == 0
    e.stop()


def test_follow_deck_is_off_by_default(tmp_path):
    """« Suivre le deck » doit être décoché par défaut et mémorisé : coché, le moindre appui sur
    l'appareil déplace la sélection de l'éditeur, ce qui est intenable en exploitation."""
    e = engine(tmp_path, start=False)
    pid = _knob_page(e, {})
    e.start()
    w = MainWindow(e)
    w.page_id = pid
    w.grid.set_page(pid)
    assert not w.follow.isChecked(), "coché par défaut : l'éditeur suivrait chaque appui"

    # Décoché : un appui sur l'appareil ne touche pas à la sélection.
    w.grid.select("0,0")
    w._activity(pid, control_key("knobTL"), "CT")
    assert w.sel == "0,0", "la sélection ne doit pas bouger quand on ne suit pas le deck"

    w.follow.setChecked(True)                       # le choix est enregistré
    assert e.cfg["options"]["follow_deck"] is True
    w._activity(pid, control_key("knobTL"), "CT")
    assert w.sel == control_key("knobTL"), "coché : l'éditeur suit, pour câbler l'appareil"
    w.close()

    w2 = MainWindow(e)                              # rouvert : le réglage est retrouvé
    assert w2.follow.isChecked()
    w2.follow.setChecked(False)
    assert e.cfg["options"]["follow_deck"] is False
    w2.close()
    e.stop()


def test_loupedeck_haptic_option(tmp_path):
    """La vibration est une option de l'appareil : activée par défaut, et décochée plus rien
    ne vibre, même si un contrôle demande une vibration."""
    e = engine(tmp_path, start=False)
    pid = _knob_page(e, {"key0": new_control("button", label="REC", haptic="long")})
    e.cfg["pages"][0]["buttons"]["0,0"] = new_button(label="Cam 1")
    e.start()
    lm, port, h = mock_loupedeck(e)
    h.page_id = pid

    def vibrations():
        return [c for c in port.commands if c[0] == ld.CMD_VIBRATE]

    port.touch(*key_xy(0), finger=1)
    port.touch(*key_xy(0), finger=1, end=True)
    assert wait(lambda: vibrations(), 3), "activée par défaut : un appui doit vibrer"
    port.button("key0")
    assert wait(lambda: any(c[1] == bytes([ld.HAPTIC["long"]]) for c in vibrations()), 3), \
        "le motif choisi pour le contrôle est respecté"

    e.cfg["decks"][h.serial] = {"haptic": False}
    n = len(vibrations())
    port.touch(*key_xy(0), finger=2)
    port.touch(*key_xy(0), finger=2, end=True)
    port.button("key0")
    time.sleep(0.4)
    assert len(vibrations()) == n, "décochée : plus aucune vibration"
    lm.identify(h.serial)
    time.sleep(0.2)
    assert len(vibrations()) == n, "même « Identifier » respecte le réglage"

    # Le réglage s'affiche dans Réglages → Decks pour un Loupedeck, et s'enregistre.
    e.decks = DeckHub(e, [lm])
    dlg = SettingsDialog(e, None)
    row = next(r for r in dlg.deck_rows if r[0] == h.serial)
    assert row[3] is not None and not row[3].isChecked(), "case Vibration absente ou mal initialisée"
    row[3].setChecked(True)
    dlg._save()
    assert e.cfg["decks"][h.serial]["haptic"] is True
    dlg.close()
    e.stop()


# -- Audit : chaque test reproduit un défaut constaté, et échoue sur l'ancien code -------------
def test_held_action_released_despite_error(tmp_path):
    """Une erreur pendant le relâché ne doit JAMAIS laisser une action maintenue enfoncée : pour un
    bouton intercom, c'est un micro ouvert jusqu'à la fermeture de LiveDeck."""
    e, pid, logs = one_page(tmp_path, {"0,0": new_button(
        label="PTT", actions=[new_action("hotkey", keys="shift+z", hold=True),
                              new_action("vmix.function", function="Cut")])})
    since = len(KEYS.log)
    e.press(pid, "0,0", True)
    assert wait(lambda: held_keys(since) == ["shift+z"], 3)
    orig = e.page_names
    e.page_names = lambda: (_ for _ in ()).throw(KeyError("name"))   # page sans nom, config remplacée…
    try:
        e.press(pid, "0,0", False)
    finally:
        e.page_names = orig
    assert wait(lambda: not held_keys(since), 3), f"touche restée enfoncée : {held_keys(since)}"
    assert not e.pressed, "le bouton ne doit plus être marqué enfoncé"
    e.stop()


def test_config_with_nulls_loads(tmp_path):
    """Une valeur « null » ne doit plus faire classer tout le fichier comme corrompu (LiveDeck
    repartait alors d'une configuration vide, en plein direct)."""
    import json as _json
    raw = {"version": 4, "options": None, "integrations": None, "decks": None,
           "pages": [None, {"id": "p", "cols": 4, "rows": 3, "buttons": None, "controls": {
               "knobTL": {"label": "Gain", "actions_cw": None, "actions": [None, {"type": "vmix.function",
                                                                                 "function": "Cut"}]},
               "key0": None}},
               {"id": "q", "name": "Q", "buttons": {"0,0": {"label": "X", "actions": None,
                                                           "feedbacks": [{"cond": None, "bg": "#ff0000"}]}}}]}
    path = Path(tmp_path) / "config.json"
    path.write_text(_json.dumps(raw), encoding="utf-8")
    cfg = load_config(path)
    assert [p["id"] for p in cfg["pages"]] == ["p", "q"], "la page « null » est retirée, les autres gardées"
    p = cfg["pages"][0]
    assert p["name"] and p["buttons"] == {} and "key0" not in p["controls"]
    k = p["controls"]["knobTL"]
    assert k["kind"] == "rotary", "une molette reste une molette"
    assert k["actions_cw"] == [] and [a["function"] for a in k["actions"]] == ["Cut"]
    b = cfg["pages"][1]["buttons"]["0,0"]
    assert b["actions"] == [] and b["feedbacks"][0]["cond"] == {}
    assert isinstance(cfg["options"], dict) and isinstance(cfg["integrations"], dict)
    # Un retour d'état mal formé n'empêche plus l'affichage du bouton.
    e = engine(tmp_path, start=False)
    v = e.visual(new_button(label="X", feedbacks=[{"cond": None, "bg": "#ff0000"}, None]))
    assert v["label"] == "X"


def test_page_change_forgets_control_selection(tmp_path):
    """Changer de page oublie la molette sélectionnée : sinon « Suppr » effaçait la molette du même
    nom sur la nouvelle page, jamais sélectionnée."""
    e = engine(tmp_path, start=False)
    a, b = new_page("A", cols=8, rows=4), new_page("B", cols=8, rows=4)
    a["controls"] = {"knobTL": new_control("rotary", label="A")}
    b["controls"] = {"knobTL": new_control("rotary", label="B")}
    e.cfg["pages"] = [a, b]
    e.start()
    w = MainWindow(e)
    w._goto_page(a["id"])
    w._select_control(control_key("knobTL"))
    w._goto_page(b["id"])
    assert w.sel is None, w.sel
    w.delete_selected()
    assert "knobTL" in e.page(b["id"])["controls"], "la molette de la page B a été effacée"
    w.close()
    e.stop()


def test_edit_actions_on_controls(tmp_path):
    """Clic droit, input déposé et collage sur des contrôles Loupedeck."""
    e = engine(tmp_path, start=False)
    p = new_page("CT", cols=4, rows=3, surface="loupedeck")
    xl = new_page("XL", cols=8, rows=4)
    e.cfg["pages"] = [p, xl]
    e.start()
    w = MainWindow(e)
    w.page_id = p["id"]
    w.grid.set_page(p["id"])
    k = control_key("knobTL")
    # Clic droit : le menu s'ouvre (il plantait avant même de s'afficher). On remplace le menu par
    # une sous-classe qui ne s'affiche pas : patcher QMenu.exec sur la classe ne suffit pas, l'appel
    # d'instance de PySide l'ignore et le vrai menu modal attendait un clic, bloquant la suite.
    from PySide6.QtWidgets import QMenu
    shown_menus = []

    class SilentMenu(QMenu):
        def exec(self, *a, **k):
            shown_menus.append([x.text() for x in self.actions() if x.text()])
            return None

    with mock.patch("livedeck.ui.main_window.QMenu", SilentMenu):
        w._cell_menu(k, QPointF(0, 0).toPoint())
        e.set_button(p["id"], k, new_control("rotary", label="Gain"))
        w._cell_menu(k, QPointF(0, 0).toPoint())
    assert len(shown_menus) == 2, "le menu d'une molette doit s'ouvrir"
    assert "Effacer" in shown_menus[1] and not any("rangée" in t or "Dupliquer" in t for t in shown_menus[1]), \
        shown_menus[1]
    # Un input déposé sur une molette vide ne lui retire pas sa rotation.
    e.assign_input(p["id"], control_key("knobBR"), "Cam 1", "preview")
    assert e.control(p["id"], "knobBR")["kind"] == "rotary"
    # Coller une molette sur une touche de grille : plus de rotation fantôme.
    w.clipboard = new_control("rotary", label="Vol", actions_cw=[new_action("vmix.adjust")])
    w._goto_page(xl["id"])
    w.grid.select("0,0")
    w.paste()
    pasted = e.button(xl["id"], "0,0")
    assert "kind" not in pasted and not pasted.get("actions_cw"), pasted
    assert pasted["actions"][0]["type"] == "vmix.adjust", "la rotation devient l'appui"
    w.close()
    e.stop()


def test_settings_timer_stops(tmp_path):
    """Fermer les Réglages arrête leur minuteur : chaque ouverture en laissait un de plus, qui
    interrogeait les intégrations deux fois par seconde jusqu'à la fermeture de LiveDeck."""
    from PySide6.QtCore import QTimer
    e = engine(tmp_path, start=False)
    e.start()
    w = MainWindow(e)
    for _ in range(3):
        dlg = SettingsDialog(e, w)
        dlg.reject()
    assert not [t for t in w.findChildren(QTimer) if t.isActive() and t.interval() == 500]
    w.close()
    e.stop()


def test_close_reduces_instead_of_quitting(tmp_path):
    """La croix réduit LiveDeck (zone de notification, ou barre des tâches sans icône) : les decks
    continuent. Seul « Quitter » (ou l'option décochée) ferme pour de bon."""
    e = engine(tmp_path, start=False)
    e.start()
    w = MainWindow(e)
    w.show()
    w.tray.isVisible = lambda: False  # pas de zone de notification (ex. lancement au démarrage de Windows)
    w.tray.isSystemTrayAvailable = lambda: False
    w.close()
    assert w.isMinimized() and not w._quitting and w in QApplication.topLevelWidgets()
    w.tray.isVisible = lambda: True
    w.close()
    assert not w.isVisible()  # masquée près de l'horloge
    e.cfg["options"]["close_to_tray"] = False
    assert w.close()  # option décochée : on quitte vraiment
    e.stop()


def test_single_instance_lock(tmp_path):
    """Deux lancements rapprochés : seul le premier prend le verrou, le second réveille le premier."""
    from livedeck import app as app_mod
    woken = []
    with mock.patch.object(app_mod, "HOME", Path(tmp_path)),             mock.patch.object(app_mod, "_wake_first", lambda name, wait_s=5.0: woken.append(name)):
        first = app_mod._single_instance("LiveDeck-test-lock")
        assert first is not None
        assert app_mod._single_instance("LiveDeck-test-lock") is None
        assert woken == ["LiveDeck-test-lock"]
        first[0].unlock()
        again = app_mod._single_instance("LiveDeck-test-lock")  # verrou libéré : on peut relancer
        assert again is not None
        again[0].unlock()


def test_loupedeck_stops_writing_after_failure(tmp_path):
    """Après une panne d'écriture, plus aucune écriture : chacune pouvait bloquer 5 s, pendant
    lesquelles la détection ne pouvait ni constater la perte ni reconnecter."""
    from PIL import Image
    e = engine(tmp_path, start=False)
    lm, port, h = mock_loupedeck(e)
    h.dev._fail()
    assert not port.is_open, "le port doit être libéré tout de suite (reconnexion possible)"
    n = len(port.commands)
    for call in (lambda: h.dev.draw_key(0, Image.new("RGB", (90, 90))), lambda: h.dev.set_brightness(50),
                 lambda: h.dev.vibrate("court")):
        try:
            call()
            raise AssertionError("une écriture vers un appareil perdu doit échouer tout de suite")
        except OSError:
            pass
    assert len(port.commands) == n, "rien ne doit partir vers un port en panne"
    lm.refresh(force=True)                                 # ne doit ni lever ni écrire
    assert len(port.commands) == n
    e.stop()


def test_loupedeck_events_with_announced_serial(tmp_path):
    """L'appareil annonce un numéro de série différent de celui de l'USB : les appuis doivent
    quand même être reconnus (avant, l'appareil devenait muet sans un mot)."""
    e = engine(tmp_path, start=False)
    e.cfg["pages"] = [new_page("CT", cols=4, rows=3)]
    e.start()
    lm = LoupedeckManager(e)
    port = MockLoupedeckPort("SN-ANNONCE")
    with mock.patch("serial.Serial", return_value=port):
        dev = ld.Loupedeck("COM-T", 0x0007, "COM-T", on_event=lm._on_event)   # USB sans n° de série
        dev.connect()
    assert dev.serial == "SN-ANNONCE" and dev.firmware, "connect() attend désormais les réponses"
    h = LoupedeckHandle(dev)
    lm.handles["COM-T"] = h
    h.page_id = lm._initial_page(h)
    dev.serial = "AUTRE-CHOSE"                             # annonce tardive, après la création du handle
    seen = []
    e.listeners.append(lambda kind, p: seen.append(p) if kind == "activity" else None)
    port.button("key3")
    assert wait(lambda: seen, 3), "appui perdu : le numéro annoncé n'est pas reconnu"
    e.stop()


def test_loupedeck_framing_resync():
    """Un octet 0x82 lu au milieu d'un message ne doit pas bloquer les vrais appuis derrière lui."""
    real = bytes([0x82, 5, 5, ld.CMD_BUTTON, 1, 0x0A, 0x00])
    for junk in (b"\x40\x82\xc8", b"\x82\x00", b"\x82\x01\x82\x02", b"\x82\xff", b"\x01\x02\x03"):
        buf = bytearray(junk + real)
        out = ld.split_frames(buf)
        assert any(m[1] == ld.CMD_BUTTON and m[3] == 0x0A for m in out), (junk, out, bytes(buf))
    buf = bytearray(b"\x01\x02\x03\x04")                   # que du rebut : le tampon ne grossit pas
    ld.split_frames(buf)
    assert not buf
    buf = bytearray(b"\x82\x05\x05\x00")                   # trame valide mais incomplète : on attend
    assert ld.split_frames(buf) == [] and bytes(buf) == b"\x82\x05\x05\x00"


def test_loupedeck_scan_does_not_freeze_ui(tmp_path):
    """Un appareil qui ne répond pas ne doit plus figer l'interface : la poignée de main se fait
    hors du verrou que l'écran consulte à chaque dessin."""
    e = engine(tmp_path, start=False)
    e.cfg["pages"] = [new_page("CT", cols=4, rows=3)]
    lm = LoupedeckManager(e)
    found = [{"path": "COM-S", "vid": 0x2EC2, "pid": 0x0007, "serial": "S", "model": "Loupedeck CT"}]

    def slow(self, timeout=2.0):
        time.sleep(1.0)
        raise OSError("poignée de main sans réponse")

    with mock.patch.object(ld, "discover", return_value=found), mock.patch.object(ld.Loupedeck, "connect", slow):
        th = threading.Thread(target=lm._scan)
        th.start()
        time.sleep(0.15)
        t0 = time.perf_counter()
        lm.info()
        waited = time.perf_counter() - t0
        th.join()
    assert waited < 0.2, f"info() a attendu {waited:.2f} s"
    e.stop()


def test_loupedeck_rgb565_fast_and_identical():
    """La conversion rapide (Pillow) donne exactement les mêmes octets que l'ancienne."""
    import random
    from PIL import Image
    rnd = random.Random(7)
    for size in ((1, 1), (90, 90), (60, 270), (13, 7)):
        img = Image.frombytes("RGB", size, bytes(rnd.randrange(256) for _ in range(size[0] * size[1] * 3)))
        for be in (False, True):
            assert ld.rgb565(img, be) == ld._rgb565_slow(img, be), (size, be)
    grad = Image.frombytes("RGB", (256, 1), bytes(v for v in range(256) for _ in range(3)))
    assert ld.rgb565(grad) == ld._rgb565_slow(grad)


def test_winaudio_failure_cached():
    """Une application absente n'est plus recherchée à chaque dessin."""
    fake = fakes.FakeAudio()
    calls = []
    orig = fake.get
    fake.get = lambda t, app="": calls.append(1) or orig(t, app)
    c = winsys.CachedAudio(fake)
    for _ in range(20):
        assert c.peek("app", "absent.exe") is None
    assert len(calls) == 1, f"{len(calls)} énumérations au lieu d'une"


def test_vmix_expect_not_stuck(tmp_path):
    """Une preview attendue que vMix refuse ne doit pas rester affichée indéfiniment."""
    s = vmix_server()
    e = engine(tmp_path)
    st = e.vmix.state
    with s.state_lock:
        s.mixes[1]["preview"] = 2
    assert wait(lambda: st.preview(1) == 2, 3)
    e.vmix.expect(1, ttl=0.2, preview=4)                   # commande que vMix « n'appliquera pas »
    assert st.preview(1) == 4
    assert wait(lambda: st.preview(1) == 2, 3), f"preview attendue restée affichée : {st.preview(1)}"
    e.stop()


# -- orchestream-v2 : touches décrites par le serveur (API Stream Deck, flux en direct) ---------------
def orch_engine(tmp_path, port, token=fakes.FakeOrchDeck.TOKEN, stream=True):
    od = fakes.FakeOrchDeck(port, stream=stream)
    keys = {"0,0": "game.advance", "0,1": "activity.ackAll", "0,2": "live.end", "0,3": "twizz.start:q-2",
            "0,4": "overlay.toggle:twizz", "1,0": "status:live", "1,1": "studio.shortcut:s1"}
    e, pg, logs = one_page(tmp_path, {k: new_button(actions=[new_action("deck.key", app="orchestream", key=v)])
                                      for k, v in keys.items()},
                           {"orchestream": {"url": f"127.0.0.1:{port}", "token": token}})
    return e, od, pg, logs


def vis_of(e, pg, k):
    return e.visual(e.button(pg, k))


def test_orchestream_keys_follow_the_stream(tmp_path):
    """Les touches affichent ce que décrit orchestream (texte, couleur, allumée, disponible), suivent le flux
    /api/deck/events dans la seconde, et lancent la commande de la touche avec ses arguments."""
    from livedeck.feedback import OFF_BG, ORCH_TONES
    e, od, pg, logs = orch_engine(tmp_path, 18261)
    o = e.orchestream
    assert wait(lambda: o.connected and o.mode == "stream", 5), o.error
    assert od.stream_headers[0]["Authorization"] == f"Bearer {fakes.FakeOrchDeck.TOKEN}"  # jeton : en-tête seul
    # Texte et couleur de la touche, tels qu'orchestream les décrit
    v = vis_of(e, pg, "0,0")
    assert v["label"] == "Fermer Q3\nCulture G" and v["bg"] == ORCH_TONES["accent"][1], v
    v = vis_of(e, pg, "0,1")
    assert v["label"] == "Tout remercier\n5 à remercier" and v["bg"] == ORCH_TONES["warning"][0], v
    assert v["fg"] == "#000000", "texte noir sur une couleur claire"
    assert vis_of(e, pg, "0,4")["bg"] == ORCH_TONES["violet"][0], "widget affiché : touche allumée"
    assert vis_of(e, pg, "1,1")["label"] == "Lumière plateau\nÉteint"
    # Appui : la commande de la touche, puis la touche change dans la seconde (sans interroger)
    reads = od.reads
    tap(e, pg, "0,0")
    assert wait(lambda: od.calls and od.calls[-1][0] == "game.advance", 3), od.calls
    assert od.calls[-1][2] == "bearer"
    assert wait(lambda: vis_of(e, pg, "0,0")["label"] == "Ouvrir Q4\nCulture G", 1.5), vis_of(e, pg, "0,0")
    tap(e, pg, "0,1")
    assert wait(lambda: vis_of(e, pg, "0,1")["label"] == "Tout remercier", 1.5)
    assert vis_of(e, pg, "0,1")["bg"] == ORCH_TONES["neutral"][1]
    # Une valeur d'argument par touche : les arguments partent tels quels
    tap(e, pg, "0,3")
    assert wait(lambda: any(c[0] == "twizz.start" for c in od.calls), 3)
    assert next(c for c in od.calls if c[0] == "twizz.start")[1] == {"quizId": "q-2"}
    tap(e, pg, "1,1")
    assert wait(lambda: od.shortcuts["s1"]["on"], 3)
    assert wait(lambda: vis_of(e, pg, "1,1")["bg"] == ORCH_TONES["amber"][0], 1.5)
    # Changé ailleurs (régie web) : suivi par le flux
    tap(e, pg, "0,4")
    assert wait(lambda: od.overlays["twizz"] is False, 3)
    assert wait(lambda: vis_of(e, pg, "0,4")["bg"] == ORCH_TONES["violet"][1], 1.5)
    od.overlays["twizz"] = True
    assert wait(lambda: vis_of(e, pg, "0,4")["bg"] == ORCH_TONES["violet"][0], 1.5)
    assert od.reads == reads, "le flux suffit : aucune lecture de l'état"
    # Touche d'état : elle affiche, l'appui n'envoie rien
    assert vis_of(e, pg, "1,0")["label"] == "Live\n42 viewers"
    n = len(od.calls)
    tap(e, pg, "1,0")
    time.sleep(0.3)
    assert len(od.calls) == n
    # Indisponible : grisée avec la raison d'orchestream ; l'appui part quand même (le serveur décide)
    od.game.update(phase="idle", nextAction=None)
    assert wait(lambda: "Aucune partie en cours" in vis_of(e, pg, "0,0")["label"], 1.5)
    assert vis_of(e, pg, "0,0")["bg"] == OFF_BG
    tap(e, pg, "0,0")
    assert wait(lambda: any("Aucune partie en cours" in m for _, m in logs), 3), logs
    # Touche absente de la liste (pas la permission live.control) : indisponible, rien n'est envoyé
    v = vis_of(e, pg, "0,2")
    assert "INTROUVABLE" in v["label"] and v["bg"] == OFF_BG, v
    tap(e, pg, "0,2")
    assert wait(lambda: any("inconnue d'orchestream" in m for _, m in logs), 3), logs
    assert not any(c[0] == "live.end" for c in od.calls)
    # Touche supprimée dans orchestream (patch « removedKeys ») : elle devient indisponible
    od.shortcuts.clear()
    assert wait(lambda: "INTROUVABLE" in vis_of(e, pg, "1,1")["label"], 1.5)
    # Nouvelle touche (patch « keys ») : proposée aussitôt
    od.shortcuts["s2"] = {"name": "Néons", "on": True, "tone": "pink"}
    assert wait(lambda: o.key("studio.shortcut:s2"), 1.5)
    assert od.streams == 1
    e.stop()


def test_orchestream_token_refused_and_revoked(tmp_path):
    """Jeton refusé ou révoqué : message clair, touches grisées « JETON REFUSÉ » ; reconnexion ensuite."""
    e, od, pg, logs = orch_engine(tmp_path, 18262)
    o = e.orchestream
    assert wait(lambda: o.connected, 5), o.error
    od.revoked = True                               # le serveur ferme le flux, puis répond 401
    assert wait(lambda: not o.connected and "jeton" in o.error, 5), o.error
    assert "Jetons API" in o.error
    v = vis_of(e, pg, "0,0")
    assert v["label"].endswith("JETON REFUSÉ") and v["bg"] == "#1a1d24", v
    od.revoked = False
    o._wake.set()                                   # sans attendre le délai de reconnexion
    assert wait(lambda: o.connected and o.mode == "stream", 5), o.error
    assert od.streams == 2 and wait(lambda: vis_of(e, pg, "0,0")["label"].startswith("Fermer Q3"), 2)
    # Test ponctuel des Réglages
    ok, msg = integrations.probe_orchestream("127.0.0.1:18262", "osk_mauvais")
    assert ok is False and "jeton" in msg.lower(), msg
    ok, msg = integrations.probe_orchestream("127.0.0.1:18262", fakes.FakeOrchDeck.TOKEN)
    assert ok is True and "Stream Deck plateau" in msg and "touche(s)" in msg, msg
    assert integrations.probe_orchestream("127.0.0.1:18262", "")[0] is None, "sans jeton : à renseigner"
    e.stop()
    e2, od2, pg2, _ = orch_engine(Path(tmp_path) / "bad", 18263, token="osk_mauvais")
    assert wait(lambda: "jeton" in e2.orchestream.error, 5), e2.orchestream.error
    assert not od2.calls and od2.streams == 0
    e2.stop()


def test_orchestream_falls_back_to_state(tmp_path):
    """Flux indisponible (orchestream sans /api/deck/events) : l'état est relu chaque seconde."""
    e, od, pg, logs = orch_engine(tmp_path, 18251, stream=False)
    o = e.orchestream
    assert wait(lambda: o.connected and o.mode == "poll", 5), o.error
    assert vis_of(e, pg, "0,1")["label"] == "Tout remercier\n5 à remercier"
    tap(e, pg, "0,1")
    assert wait(lambda: vis_of(e, pg, "0,1")["label"] == "Tout remercier", 2)
    row = next(r for r in integrations.integration_status(e) if r[0] == "orchestream")
    assert row[2] is True and "lecture de l'état" in row[3], row
    assert od.streams == 0 and od.reads >= 2
    e.stop()


def test_orchestream_retry_after(tmp_path):
    """429 à la reconnexion : le service attend Retry-After au lieu d'insister."""
    e, od, pg, _ = orch_engine(tmp_path, 18252)
    o = e.orchestream
    assert wait(lambda: o.connected, 5)
    od.throttle = 1
    o._abort()                                      # flux coupé : la reconnexion reçoit le 429
    assert wait(lambda: o._retry_at > time.monotonic(), 5), "Retry-After ignoré"
    assert not od.throttle and od.streams == 1
    assert wait(lambda: o.connected and od.streams == 2, 5)
    e.stop()


def test_orchestream_wanted(tmp_path):
    """Une touche Orchestream réveille le service, y compris par la rotation d'une molette."""
    e = engine(tmp_path, start=False)
    p = new_page("P")
    e.cfg["pages"] = [p]
    e.start()
    assert not e.orchestream.wanted
    p["buttons"]["0,0"] = new_button(actions=[new_action("deck.key", key="draw.run")])
    e.apply_integrations()
    assert e.orchestream.wanted
    p["buttons"].clear()
    p["controls"] = {"knobTL": new_control("rotary", actions_cw=[new_action("deck.key", key="game.advance")])}
    e.apply_integrations()
    assert e.orchestream.wanted, "la liste de rotation d'une molette doit compter"
    p["controls"] = {}
    p["buttons"]["0,0"] = new_button(feedbacks=[fb("deckkey", app="orchestream", key="status:live", state="active")])
    e.apply_integrations()
    assert e.orchestream.wanted, "un retour d'état orchestream compte aussi"
    e.stop()


def test_orchestream_companion_overlay_toggle():
    """Import Companion « intégrations natives » : un interrupteur show / hide vers un overlay d'orchestream
    devient la touche overlay.toggle:<widget> d'orchestream-v2."""
    comp = {"pages": [{"buttons": {"0,0": {
        "label": "Poll", "mode": "toggle", "feedbacks": [],
        "actions": [{"type": "http", "method": "POST", "url": "http://o.local/api/overlay/poll/show"}],
        "actions_off": [{"type": "http", "method": "POST", "url": "http://o.local/api/overlay/poll/hide"}]}}}]}
    integrations.migrate(comp)
    b = comp["pages"][0]["buttons"]["0,0"]
    assert b["actions"] == [{"type": "deck.key", "app": "orchestream", "key": "overlay.toggle:poll"}]
    assert b["mode"] == "press"
    assert comp["integrations"]["orchestream"]["url"] == "http://o.local"
    assert not hasattr(integrations, "convert_orchestream"), "plus de conversion des anciennes touches"


def test_orchestream_catalog_and_picker(tmp_path):
    """Aucune liste de touches en dur : la bibliothèque et l'inspecteur proposent celles d'orchestream."""
    from livedeck.ui.widgets import DeckKeyPicker
    assert summary(new_action("deck.key", key="twizz.start:q-1", title="Twizz Culture G")) == \
        "Orchestream · Twizz Culture G"
    assert summary(new_action("deck.key", key="draw.run")) == "Orchestream · draw.run"
    assert summary(new_action("deck.key", app="multiview", key="sender.toggle")) == "py-multiview · sender.toggle"
    assert not hasattr(integrations, "ORCH_COMMANDS") and not hasattr(presets, "REGIE_PRESETS")
    assert presets.make("deck:orchestream")["actions"][0] == {"type": "deck.key", "app": "orchestream", "key": ""}
    e, od, pg, _ = orch_engine(tmp_path, 18264)
    assert wait(lambda: e.orchestream.has_keys, 5)
    lib = [it for c, items in presets.library(e) if c in presets.TOOL_NAMES for it in items]
    ids = {pid_ for pid_, _, _ in lib}
    assert {"deck:orchestream:game.advance", "deck:orchestream:studio.shortcut:s1",
            "deck:orchestream:status:live"} <= ids, ids
    assert "deck:orchestream:live.end" not in ids, "seulement les touches permises au jeton"
    want = {"type": "deck.key", "app": "orchestream", "key": "twizz.start:q-1", "title": "Twizz Culture G"}
    assert presets.make("deck:orchestream:twizz.start:q-1", e)["actions"][0] == want
    picker = DeckKeyPicker(e, "studio.shortcut:s1", "orchestream")
    groups = [picker.tree.topLevelItem(i).text(0) for i in range(picker.tree.topLevelItemCount())]
    assert groups[:3] == ["Jeux", "Tirages", "Chat et remerciements"] and "État" in groups, groups
    assert picker.tree.currentItem().data(0, Qt.UserRole) == "studio.shortcut:s1"
    got = []
    picker.changed.connect(got.append)
    picker.search.setText("néons")
    assert picker.tree.topLevelItemCount() == 1 and "Aucune" in picker.tree.topLevelItem(0).text(0)
    picker.search.setText("cinéma")
    item = picker.tree.topLevelItem(0).child(0)
    picker._picked(item)
    assert got == ["twizz.start:q-2"]
    e.stop()


# -- API Stream Deck commune : un client par app (orchestream, py-multiview…) ----------------------------
def mv_deck_engine(tmp_path, port, token_cfg="", **fake):
    """Faux py-multiview + touches qu'il décrit (/api/deck)."""
    mv = fakes.FakeMultiview(port, **fake)
    keys = {"0,0": "sender.toggle", "0,1": "stream.toggle:s-1", "0,2": "stream.muteToggle:s-2",
            "0,3": "monitor.volumeUp", "0,4": "route.toggle:r-9"}
    buttons = {k: new_button(actions=[new_action("deck.key", app="multiview", key=v)]) for k, v in keys.items()}
    e, pg, logs = one_page(tmp_path, buttons, {"multiview": {"url": f"127.0.0.1:{port}", "token": token_cfg}})
    return e, mv, pg, logs


def test_multiview_deck_keys_follow_the_stream(tmp_path):
    """py-multiview par l'API Stream Deck commune : sans jeton (aucun en-tête Authorization), touches décrites
    par le serveur et suivies en direct."""
    from livedeck.feedback import ORCH_TONES
    e, mv, pg, logs = mv_deck_engine(tmp_path, 18271)
    d = e.deck("multiview")
    assert d is not e.orchestream and d.name == "py-multiview"
    assert wait(lambda: d.connected and d.mode == "stream", 5), d.error
    assert not e.orchestream.wanted, "orchestream n'est pas réveillé par les touches de py-multiview"
    assert mv.deck_headers and not any("Authorization" in h for h in mv.deck_headers), "jeton vide : aucun en-tête"
    v = vis_of(e, pg, "0,0")
    assert v["label"] == "Émission OMT\nRégie" and v["bg"] == ORCH_TONES["neutral"][1], v
    assert vis_of(e, pg, "0,1")["label"] == "Caméra 1\nArrêté"
    # Appui : la commande et ses arguments ; la touche suit dans la seconde, sans relire l'état
    tap(e, pg, "0,0")
    assert wait(lambda: mv.deck_calls and mv.deck_calls[-1][:2] == ("sender.toggle", {}), 3), mv.deck_calls
    assert mv.deck_calls[-1][2] is None
    assert wait(lambda: vis_of(e, pg, "0,0")["bg"] == ORCH_TONES["live"][0], 1.5), vis_of(e, pg, "0,0")
    tap(e, pg, "0,1")
    assert wait(lambda: mv.streams[0]["running"], 3)
    assert ("stream.toggle", {"stream": "s-1"}, None) in mv.deck_calls
    assert wait(lambda: vis_of(e, pg, "0,1")["label"] == "Caméra 1\nActif", 1.5)
    # Changé dans py-multiview lui-même : suivi par le flux
    mv.streams[1]["muted"] = True
    assert wait(lambda: vis_of(e, pg, "0,2")["label"] == "Plateau\nSon coupé", 1.5)
    mv.volume = 100
    assert wait(lambda: "Volume déjà au maximum" in vis_of(e, pg, "0,3")["label"], 1.5)
    assert mv.deck_reads == 0 and mv.deck_streams == 1, "le flux suffit"
    # Touche que py-multiview ne décrit pas (route supprimée) : grisée, rien n'est envoyé
    assert "INTROUVABLE" in vis_of(e, pg, "0,4")["label"]
    tap(e, pg, "0,4")
    assert wait(lambda: any("inconnue de py-multiview" in m for _, m in logs), 3), logs
    # Refus de py-multiview : son message, préfixé du nom de l'app
    mv.refuse = "aucun écran assigné"
    tap(e, pg, "0,0")
    assert wait(lambda: any(m == "py-multiview · Émission OMT : aucun écran assigné" for _, m in logs), 3), logs
    mv.refuse = ""
    row = next(r for r in integrations.integration_status(e) if r[0] == "multiview")
    assert row[2] is True and "touche(s)" in row[3] and "flux en direct" in row[3], row
    # Bibliothèque : les touches que décrit py-multiview
    lib = [it for c, items in presets.library(e) if c in presets.TOOL_NAMES for it in items]
    ids = {pid_ for pid_, _, _ in lib}
    assert {"deck:multiview:sender.toggle", "deck:multiview:stream.toggle:s-1"} <= ids, ids
    b = presets.make("deck:multiview:stream.toggle:s-2", e)
    assert b["actions"][0] == {"type": "deck.key", "app": "multiview", "key": "stream.toggle:s-2",
                               "title": "Plateau"}
    # Sélecteur de l'inspecteur : les touches de l'app choisie
    picker = ui_widgets.DeckKeyPicker(e, "sender.toggle", "multiview")
    groups = [picker.tree.topLevelItem(i).text(0) for i in range(picker.tree.topLevelItemCount())]
    assert groups[:3] == ["Réception", "Flux", "Son des flux"], groups
    # py-multiview éteint : HORS LIGNE (pas « JETON ? » : il n'en demande pas)
    mv.srv.shutdown()
    mv.srv.server_close()
    d._abort()
    assert wait(lambda: not d.connected, 8)
    assert d.unavailable() == "HORS LIGNE" and vis_of(e, pg, "0,0")["label"].endswith("HORS LIGNE")
    e.stop()


def test_multiview_deck_optional_token(tmp_path):
    """Jeton facultatif : envoyé seulement s'il est rempli ; « JETON ? » seulement si l'app en demande un,
    « JETON REFUSÉ » si celui réglé est refusé."""
    e, mv, pg, logs = mv_deck_engine(tmp_path, 18272, token="secret")
    d = e.deck("multiview")
    assert wait(lambda: d.error and "demande un jeton" in d.error, 5), d.error
    assert d.unavailable() == "JETON ?" and vis_of(e, pg, "0,0")["label"].endswith("JETON ?")
    assert "Préférences → Pilotage à distance" in d.error
    e.mutate(lambda c: c["integrations"]["multiview"].update(token="faux"))
    assert wait(lambda: d.token == "faux" and d.unavailable() == "JETON REFUSÉ" and "refuse le jeton" in d.error,
                5), d.error
    e.mutate(lambda c: c["integrations"]["multiview"].update(token="secret"))
    d._wake.set()
    assert wait(lambda: d.connected, 12), d.error
    assert mv.deck_headers[-1].get("Authorization") == "Bearer secret"
    tap(e, pg, "0,0")
    assert wait(lambda: mv.deck_calls and mv.deck_calls[-1][2] == "Bearer secret", 3), mv.deck_calls
    e.stop()
    # Test ponctuel des Réglages
    ok, msg = integrations.probe_deck("multiview", "127.0.0.1:18272", "")
    assert ok is False and "demande un jeton" in msg, msg
    ok, msg = integrations.probe_deck("multiview", "127.0.0.1:18272", "secret")
    assert ok is True and "jeton « jeton API » accepté" in msg and "touche(s)" in msg, msg
    old = fakes.FakeMultiview(18273, deck=False)  # py-multiview d'avant /api/deck
    ok, msg = integrations.probe_deck("multiview", "127.0.0.1:18273", "")
    assert ok is False and "sans l'API Stream Deck" in msg and "mets py-multiview à jour" in msg, msg
    assert "adresse publique" not in msg, "le cas « adresse publique » est propre à orchestream"
    old.srv.shutdown()
    # Adresse par défaut : ce poste
    assert integrations.deck_url("multiview", "") == "http://127.0.0.1:5090"
    assert integrations.deck_url("multiview", "10.0.0.2") == "http://10.0.0.2:5090"
    assert integrations.deck_url("orchestream", "") == ""
    # orchestream : sans jeton, rien n'est envoyé (il en exige un)
    o = integrations.DeckApp("orchestream", lambda: None, lambda *a: None)
    o.configure("127.0.0.1:1", "")
    assert not o.configured and o.unavailable() == "JETON ?"
    assert integrations.explain_deck("multiview", "http://h:5090", 404, {"error": {"code": "NOT_FOUND"}}) \
        .startswith("h:5090 répond, mais sans l'API Stream Deck")
    assert "adresse publique" in integrations.explain_orchestream("http://h:5174", 404, {"error": {"code": "NOT_FOUND"}})


def test_multiview_deck_settings(tmp_path):
    """Réglages → Intégrations : py-multiview a son jeton facultatif et son bouton Tester."""
    mv = fakes.FakeMultiview(18274)
    e, pid, logs = one_page(tmp_path, {}, {"multiview": {"url": "127.0.0.1:18274"}})
    w = MainWindow(e)
    dlg = SettingsDialog(e, w)
    dlg.show()
    assert e.deck("multiview").forced
    assert wait(lambda: e.deck("multiview").connected, 6), e.deck("multiview").error
    dlg._test("multiview")
    result = dlg._tests["multiview"][1]
    assert wait(lambda: "touche(s)" in result.text(), 6), result.text()
    dlg._refresh_status()
    assert "touche(s)" in dlg.mv_status.text(), dlg.mv_status.text()
    before = json.dumps(e.cfg["integrations"], sort_keys=True)
    dlg._save()
    assert json.dumps(e.cfg["integrations"], sort_keys=True) == before, "rien de modifié : pas de clé ajoutée"
    dlg = SettingsDialog(e, w)
    dlg.mv_token.setText("abc")
    dlg._save()
    assert e.cfg["integrations"]["multiview"] == {"url": "127.0.0.1:18274", "token": "abc"}
    assert not e.deck("multiview").forced
    assert e.deck("multiview").token == "abc"
    w.quit_app()
    mv.srv.shutdown()


# -- MixPanel (console X-Touch pour vMix) : touches décrites par /api/deck -------------------------------
def mixpanel_engine(tmp_path, port, token_cfg="", **fake):
    """Faux MixPanel + des touches qu'il décrit (état, scène, macro, page, verrouillage, ducking)."""
    mp = fakes.FakeMixPanel(port, **fake)
    keys = {"0,0": "scene.recall:s2", "0,1": "page.toggle", "0,2": "status.vmix", "0,3": "macro.run:m2",
            "0,4": "scene.recall:s9", "1,0": "lock.toggle", "1,1": "ducking.toggle", "1,2": "console.modeToggle"}
    buttons = {k: new_button(actions=[new_action("deck.key", app="mixpanel", key=v)]) for k, v in keys.items()}
    e, pg, logs = one_page(tmp_path, buttons, {"mixpanel": {"url": f"127.0.0.1:{port}", "token": token_cfg}})
    return e, mp, pg, logs


def test_mixpanel_deck_keys_follow_the_stream(tmp_path):
    """MixPanel par l'API Stream Deck commune : sans jeton (aucun en-tête Authorization), touches décrites
    par MixPanel, état puis patchs du flux, appui -> POST de la commande de la touche avec ses arguments."""
    from livedeck.feedback import ORCH_TONES
    assert "mixpanel" in integrations.DECK_APPS
    e, mp, pg, logs = mixpanel_engine(tmp_path, 18275)
    d = e.deck("mixpanel")
    assert d.name == "MixPanel" and d is not e.orchestream and d is not e.deck("multiview")
    assert wait(lambda: d.connected and d.mode == "stream", 5), d.error
    assert not e.orchestream.wanted and not e.deck("multiview").wanted
    assert mp.deck_headers and not any("Authorization" in h for h in mp.deck_headers), "jeton vide : aucun en-tête"
    assert d.get("app.name") == "MixPanel"
    # État complet à l'ouverture
    v = vis_of(e, pg, "0,0")
    assert v["label"] == "Plateau\nRappeler" and v["bg"] == ORCH_TONES["neutral"][1], v
    assert vis_of(e, pg, "0,1")["label"] == "Page\nPage A"
    assert vis_of(e, pg, "0,2")["label"] == "vMix\nv27"
    assert "Macro vide" in vis_of(e, pg, "0,3")["label"], "indisponible : la raison donnée par MixPanel"
    assert "INTROUVABLE" in vis_of(e, pg, "0,4")["label"], "scène supprimée dans MixPanel"
    # Appui : la commande et ses arguments, puis la touche suit par le flux (patch)
    tap(e, pg, "0,0")
    assert wait(lambda: mp.deck_calls and mp.deck_calls[-1] == ("scene.recall", {"scene": "s2"}, None), 3), \
        mp.deck_calls
    assert wait(lambda: vis_of(e, pg, "0,0")["label"] == "Plateau\nEn place", 1.5), vis_of(e, pg, "0,0")
    assert vis_of(e, pg, "0,0")["bg"] == ORCH_TONES["success"][0]
    tap(e, pg, "0,1")
    assert wait(lambda: ("page.toggle", {}, None) in mp.deck_calls, 3), mp.deck_calls
    assert wait(lambda: vis_of(e, pg, "0,1")["label"] == "Page\nPage B", 1.5)
    assert vis_of(e, pg, "0,1")["bg"] == ORCH_TONES["accent"][0]
    tap(e, pg, "1,0")
    assert wait(lambda: vis_of(e, pg, "1,0")["label"] == "Verrouillage\nVerrouillé", 3)
    # Changé dans MixPanel lui-même (X-Touch, vMix) : suivi en direct par le flux
    mp.vmix = False
    assert wait(lambda: vis_of(e, pg, "0,2")["label"] == "vMix\nDéconnecté", 1.5)
    assert vis_of(e, pg, "0,2")["bg"] == ORCH_TONES["danger"][1], "éteinte : teinte sombre"
    mp.ducking["active"], mp.ducking["enabled"] = True, True
    assert wait(lambda: vis_of(e, pg, "1,1")["label"] == "Ducking\nMusique baissée", 1.5)
    assert mp.deck_reads == 0 and mp.deck_streams == 1, "le flux suffit"
    # Touche d'affichage seul (command: null) : rien n'est envoyé
    n = len(mp.deck_calls)
    tap(e, pg, "0,2")
    time.sleep(0.3)
    assert len(mp.deck_calls) == n, mp.deck_calls
    # Refus motivé de MixPanel : son message, au nom de l'app
    mp.xtouch = False
    assert wait(lambda: "X-Touch non connectée" in vis_of(e, pg, "1,2")["label"], 1.5)
    tap(e, pg, "1,2")
    assert wait(lambda: any(m.startswith("MixPanel") and "X-Touch non connectée" in m for _, m in logs), 3), logs
    # Réglages et voyants
    row = next(r for r in integrations.integration_status(e) if r[0] == "mixpanel")
    assert row[1] == "MixPanel" and row[2] is True and "touche(s)" in row[3] and "flux en direct" in row[3], row
    # Bibliothèque et sélecteur de l'inspecteur : les touches que décrit MixPanel
    lib = [it for c, items in presets.library(e) if c in presets.TOOL_NAMES for it in items]
    ids = {pid_ for pid_, _, _ in lib}
    assert {"deck:mixpanel:scene.recall:s1", "deck:mixpanel:macro.run:m1", "deck:mixpanel:page.toggle",
            "deck:mixpanel:console.modeToggle", "deck:mixpanel:status.xtouch"} <= ids, ids
    b = presets.make("deck:mixpanel:scene.recall:s1", e)
    assert b["actions"][0] == {"type": "deck.key", "app": "mixpanel", "key": "scene.recall:s1", "title": "Jeu"}
    assert summary(b["actions"][0]) == "MixPanel · Jeu"
    picker = ui_widgets.DeckKeyPicker(e, "page.toggle", "mixpanel")
    groups = [picker.tree.topLevelItem(i).text(0) for i in range(picker.tree.topLevelItemCount())]
    assert groups[:5] == ["État", "Scènes", "Macros", "Automatisation", "Console"], groups
    # MixPanel fermé : HORS LIGNE (pas « JETON ? » : jeton facultatif), avec où ouvrir l'API
    mp.close()
    d._abort()
    assert wait(lambda: not d.connected, 8)
    assert d.unavailable() == "HORS LIGNE" and vis_of(e, pg, "0,1")["label"].endswith("HORS LIGNE")
    assert wait(lambda: "Pilotage à distance" in (d.error or ""), 8), d.error
    e.stop()


def test_mixpanel_deck_optional_token_and_probe(tmp_path):
    """Jeton facultatif : réglé dans MixPanel, il part dans « Authorization: Bearer ». Tester reconnaît MixPanel
    par « app.name », ajoute le port 5085 à une IP seule et dit où ouvrir l'API quand elle est fermée."""
    e, mp, pg, logs = mixpanel_engine(tmp_path, 18276, token="s3cret")
    d = e.deck("mixpanel")
    assert wait(lambda: d.error and "demande un jeton" in d.error, 5), d.error
    assert d.unavailable() == "JETON ?" and vis_of(e, pg, "0,1")["label"].endswith("JETON ?")
    assert "MixPanel, Réglages › Pilotage à distance" in d.error
    e.mutate(lambda c: c["integrations"]["mixpanel"].update(token="faux"))
    assert wait(lambda: d.token == "faux" and d.auth_failed, 5), d.error
    assert d.unavailable() == "JETON REFUSÉ"
    e.mutate(lambda c: c["integrations"]["mixpanel"].update(token="s3cret"))
    d._wake.set()
    assert wait(lambda: d.connected, 12), d.error
    assert mp.deck_headers[-1].get("Authorization") == "Bearer s3cret"
    tap(e, pg, "0,1")
    assert wait(lambda: mp.deck_calls and mp.deck_calls[-1] == ("page.toggle", {}, "Bearer s3cret"), 3), \
        mp.deck_calls
    row = next(r for r in integrations.integration_status(e) if r[0] == "mixpanel")
    assert row[2] is True and "jeton « jeton API »" in row[3], row
    e.stop()
    # Tester (Réglages) : sans jeton, avec, et un autre serveur /api/deck à cette adresse
    ok, msg = integrations.probe_deck("mixpanel", "127.0.0.1:18276", "")
    assert ok is False and "MixPanel demande un jeton" in msg, msg
    ok, msg = integrations.probe_deck("mixpanel", "127.0.0.1:18276", "s3cret")
    assert ok is True and msg.startswith("MixPanel 1.2.1 · jeton « jeton API » accepté") and "touche(s)" in msg, msg
    mp.close()
    other = fakes.FakeMixPanel(18277, name="py-multiview")
    ok, msg = integrations.probe_deck("mixpanel", "127.0.0.1:18277", "")
    assert ok is False and "c'est py-multiview, pas MixPanel" in msg, msg
    other.close()
    ok, msg = integrations.probe_deck("mixpanel", "127.0.0.1:18277", "")  # plus rien n'écoute
    assert ok is False and "API fermée ? MixPanel › Réglages › Pilotage à distance" in msg, msg
    # Adresse : ce poste par défaut, le port 5085 ajouté à une IP seule
    assert integrations.deck_url("mixpanel", "") == "http://127.0.0.1:5085"
    assert integrations.deck_url("mixpanel", "192.168.1.42") == "http://192.168.1.42:5085"
    assert integrations.deck_url("mixpanel", "192.168.1.42:6000") == "http://192.168.1.42:6000"
    assert integrations.deck_configured({"integrations": {}}, "mixpanel"), "jeton facultatif"
    # Le contrôle d'identité est propre à MixPanel : py-multiview n'envoie pas « app » dans l'état
    assert "app_name" not in integrations.DECK_APPS["multiview"]


def test_mixpanel_deck_settings(tmp_path):
    """Réglages → Intégrations : MixPanel a sa section (adresse, jeton facultatif) et un Tester qui marche ;
    enregistrer écrit son bloc, et rien n'est ajouté si rien n'a changé."""
    mp = fakes.FakeMixPanel(18278)
    e, pid, logs = one_page(tmp_path, {}, {"mixpanel": {"url": "127.0.0.1:18278"}})
    w = MainWindow(e)
    dlg = SettingsDialog(e, w)
    dlg.show()
    assert "mixpanel" in dlg._deck_edits and "mixpanel" in dlg.deck_status
    url, token = dlg._deck_edits["mixpanel"]
    assert url.text() == "127.0.0.1:18278" and "127.0.0.1:5085" in url.placeholderText()
    assert "Pilotage à distance" in url.toolTip() and "5085" in url.toolTip()
    assert token.placeholderText() == "facultatif"
    assert e.deck("mixpanel").forced
    assert wait(lambda: e.deck("mixpanel").connected, 6), e.deck("mixpanel").error
    dlg._test("mixpanel")
    result = dlg._tests["mixpanel"][1]
    assert wait(lambda: "touche(s)" in result.text(), 6), result.text()
    assert "MixPanel 1.2.1" in result.text()
    dlg._refresh_status()
    assert "touche(s)" in dlg.deck_status["mixpanel"].text(), dlg.deck_status["mixpanel"].text()
    before = json.dumps(e.cfg["integrations"], sort_keys=True)
    dlg._save()
    assert json.dumps(e.cfg["integrations"], sort_keys=True) == before, "rien de modifié : pas de clé ajoutée"
    dlg = SettingsDialog(e, w)
    dlg._deck_edits["mixpanel"][1].setText("abc")
    dlg._save()
    assert e.cfg["integrations"]["mixpanel"] == {"url": "127.0.0.1:18278", "token": "abc"}
    assert e.deck("mixpanel").token == "abc" and not e.deck("mixpanel").forced
    assert "mixpanel" in w.integ_pills, "un voyant MixPanel dans la barre d'état"
    w.quit_app()
    mp.close()


def _mixpanel_source() -> Path | None:
    """Dépôt py-mix-panel à côté de py-livedeck (ou MIXPANEL_SRC), pour le tester contre le vrai serveur."""
    env = os.environ.get("MIXPANEL_SRC")
    candidates = [Path(env)] if env else [p / "py-mix-panel" for p in Path(__file__).resolve().parents]
    return next((p for p in candidates if (p / "pymixpanel" / "api.py").is_file()), None)


def test_mixpanel_real_server(tmp_path):
    """Contre le vrai serveur HTTP de MixPanel (pymixpanel.api, routage et touches de pymixpanel.deck), ouvert
    sur 127.0.0.1 port 0, sans son moteur vMix/X-Touch. Ne fait rien si le dépôt py-mix-panel est absent."""
    src = _mixpanel_source()
    if src is None:
        print("py-mix-panel introuvable : test du vrai serveur sauté")
        return
    sys.path.insert(0, str(src))
    try:
        from pymixpanel import deck as mp_deck
        from pymixpanel.api import ApiServer, dispatch
    finally:
        sys.path.remove(str(src))
    status = {"app": {"name": "MixPanel", "version": "x"}, "profile": {"name": "Show"},
              "vmix": {"connected": True, "version": "27"},
              "xtouch": {"connected": True, "port": "X-TOUCH", "mode": "standard"},
              "page": "A", "locked": False, "ducking": {"enabled": False, "active": False, "configured": True},
              "scenes": [{"id": "s1", "name": "Jeu", "active": True}, {"id": "s2", "name": "Plateau", "active": False}],
              "macros": [{"id": "m1", "name": "Fin", "steps": 2, "running": False}]}
    server = ApiServer(None)
    calls = []

    class Control(mp_deck.DeckControl):
        def status(self):
            return json.loads(json.dumps(status))

        def recall_scene(self, sid):
            calls.append(("scene", sid))
            for s in status["scenes"]:
                s["active"] = s["id"] == sid
            return self.status()

        def set_page(self, page):
            calls.append(("page", page))
            status["page"] = page
            return self.status()

    ctl = Control(engine=None, token=lambda: server.token)
    server._call = lambda method, path, body=b"": dispatch(method, path, ctl, body)
    assert server.start("127.0.0.1", 0) == ""
    port = server.port
    try:
        buttons = {"0,0": new_button(actions=[new_action("deck.key", app="mixpanel", key="scene.recall:s2")]),
                   "0,1": new_button(actions=[new_action("deck.key", app="mixpanel", key="page.toggle")])}
        e, pg, logs = one_page(tmp_path, buttons, {"mixpanel": {"url": f"127.0.0.1:{port}"}})
        d = e.deck("mixpanel")
        assert wait(lambda: d.connected and d.mode == "stream", 6), d.error
        assert vis_of(e, pg, "0,0")["label"] == "Plateau\nRappeler"
        ok, msg = integrations.probe_deck("mixpanel", f"127.0.0.1:{port}", "")
        assert ok is True and msg.startswith("MixPanel x · API Stream Deck (accès libre)"), msg
        tap(e, pg, "0,0")
        assert wait(lambda: ("scene", "s2") in calls, 4), (calls, logs)
        assert wait(lambda: vis_of(e, pg, "0,0")["label"] == "Plateau\nEn place", 3), vis_of(e, pg, "0,0")
        tap(e, pg, "0,1")
        assert wait(lambda: vis_of(e, pg, "0,1")["label"] == "Page\nPage B", 3), vis_of(e, pg, "0,1")
        # Jeton réglé dans MixPanel : LiveDeck le demande, puis l'envoie
        server.token = "s3cret"
        assert integrations.probe_deck("mixpanel", f"127.0.0.1:{port}", "")[0] is False
        ok, msg = integrations.probe_deck("mixpanel", f"127.0.0.1:{port}", "s3cret")
        assert ok is True and "jeton « jeton API » accepté" in msg, msg
        e.stop()
    finally:
        server.stop()


# -- DMAIX (contrôleur DMX) : touches décrites par le serveur (API Stream Deck commune) -------------------
def dmaix_engine(tmp_path, port, token="", server_token=""):
    dm = fakes.FakeDmaix(port, token=server_token)
    keys = {"0,0": "output.status", "0,1": "scene.current", "0,2": "scene.play:sc-2", "0,3": "scene.play:sc-3",
            "0,4": "effect.toggle:fx-1", "1,0": "effects.stopAll", "1,1": "dmx.blackout"}
    e, pg, logs = one_page(tmp_path, {k: new_button(actions=[new_action("deck.key", app="dmaix", key=v)])
                                      for k, v in keys.items()},
                           {"dmaix": {"url": f"127.0.0.1:{port}", "token": token}})
    return e, dm, pg, logs


def test_dmaix_keys_follow_the_stream(tmp_path):
    """Scènes, effets, Stop effets, Blackout et touches d'état : tels que DMAIX les décrit, suivis en direct
    par le flux, l'appui part avec l'argument de la touche. API ouverte : aucun en-tête de jeton."""
    from livedeck.feedback import ORCH_TONES
    e, dm, pg, logs = dmaix_engine(tmp_path, 18281)
    d = e.deck("dmaix")
    assert d.wanted and wait(lambda: d.connected and d.mode == "stream", 5), d.error
    assert vis_of(e, pg, "0,0")["label"] == "Sortie DMX\nConnectée"
    assert vis_of(e, pg, "0,0")["bg"] == ORCH_TONES["success"][0]
    assert vis_of(e, pg, "0,1")["label"] == "Scène\nAucune"
    assert vis_of(e, pg, "0,2")["label"] == "Ambiance bleue" and vis_of(e, pg, "0,2")["bg"] == ORCH_TONES["neutral"][1]
    assert vis_of(e, pg, "0,4")["label"] == "Strobe\n4 s"
    v = vis_of(e, pg, "1,0")
    assert v["label"].startswith("Stop effets\nAucun effet") and v["bg"] == OFF_BG, v   # grisée sans effet
    assert vis_of(e, pg, "1,1")["bg"] == ORCH_TONES["danger"][1]
    # Scène : jouée avec son identifiant, la touche et la scène en cours s'allument dans la seconde
    tap(e, pg, "0,2")
    assert wait(lambda: dm.calls and dm.calls[-1][0] == "scene.play", 3), dm.calls
    assert dm.calls[-1][1] == {"args": {"scene": "sc-2"}} and dm.calls[-1][2] is None, dm.calls[-1]
    assert wait(lambda: vis_of(e, pg, "0,2")["label"] == "Ambiance bleue\nEn cours", 1.5)
    assert vis_of(e, pg, "0,2")["bg"] == ORCH_TONES["success"][0]
    assert wait(lambda: vis_of(e, pg, "0,1")["label"] == "Scène\nAmbiance bleue", 1.5)
    assert any("Scène « Ambiance bleue » jouée" in m for _, m in logs), logs
    # Effet : bascule, rouge « live » quand il tourne ; Stop effets se dégrise
    tap(e, pg, "0,4")
    assert wait(lambda: vis_of(e, pg, "0,4")["bg"] == ORCH_TONES["live"][0], 1.5)
    assert wait(lambda: vis_of(e, pg, "1,0")["label"] == "Stop effets\n1 en cours", 1.5)
    tap(e, pg, "1,0")
    assert wait(lambda: not dm.running and vis_of(e, pg, "0,4")["label"] == "Strobe\n4 s", 1.5)
    tap(e, pg, "1,1")
    assert wait(lambda: dm.blackouts == 1, 3)
    # Touche d'état : rien n'est envoyé
    n = len(dm.calls)
    tap(e, pg, "0,0")
    time.sleep(0.3)
    assert len(dm.calls) == n
    # Élément « à corriger » : grisé avec la raison de DMAIX ; l'appui est refusé (409) et journalisé
    assert "corriger" in vis_of(e, pg, "0,3")["label"] and vis_of(e, pg, "0,3")["bg"] == OFF_BG
    tap(e, pg, "0,3")
    assert wait(lambda: any("DMAIX · Cassée" in m and "à corriger" in m for _, m in logs), 3), logs
    # Changé dans DMAIX : sortie débranchée, scène supprimée, nouvelle scène
    dm.output["connected"] = False
    assert wait(lambda: vis_of(e, pg, "0,0")["label"] == "Sortie DMX\nDéconnectée", 1.5)
    dm.scenes.pop(1)
    dm.scene = None
    assert wait(lambda: "INTROUVABLE" in vis_of(e, pg, "0,2")["label"], 1.5)
    dm.scenes.append({"id": "sc-9", "name": "Nouvelle", "playable": True})
    assert wait(lambda: d.key("scene.play:sc-9"), 1.5)
    row = next(r for r in integrations.integration_status(e) if r[0] == "dmaix")
    assert row[2] is True and "touche(s)" in row[3], row
    assert dm.streams == 1, "le flux suffit : aucune lecture de l'état"
    e.stop()


def test_dmaix_token_and_probe(tmp_path):
    """Jeton facultatif : envoyé dans l'en-tête s'il est réglé ; manquant, JETON ? ; faux, JETON REFUSÉ."""
    assert integrations.deck_url("dmaix", "192.168.1.30") == "http://192.168.1.30:6174"
    assert integrations.deck_url("dmaix", "http://dmaix.local:7000/") == "http://dmaix.local:7000"
    assert integrations.deck_url("dmaix", "") == "", "pas d'adresse par défaut : DMAIX est souvent ailleurs"
    e, dm, pg, logs = dmaix_engine(tmp_path, 18282, server_token="dmx_secret")
    d = e.deck("dmaix")
    assert wait(lambda: d.unavailable() == "JETON ?" and "demande un jeton" in d.error, 5), d.error
    tap(e, pg, "0,2")
    assert not dm.calls
    e.mutate(lambda c: c["integrations"]["dmaix"].update(token="faux"))
    assert wait(lambda: d.unavailable() == "JETON REFUSÉ", 12), d.error
    e.mutate(lambda c: c["integrations"]["dmaix"].update(token="dmx_secret"))
    d._wake.set()
    assert wait(lambda: d.connected, 12), d.error
    tap(e, pg, "1,1")
    assert wait(lambda: dm.calls and dm.calls[-1][2] == "Bearer dmx_secret", 3), dm.calls
    ok, msg = integrations.probe_deck("dmaix", "127.0.0.1:18282", "dmx_secret")
    assert ok is True and "touche(s)" in msg, msg
    ok, msg = integrations.probe_deck("dmaix", "127.0.0.1:18282", "")
    assert ok is False and "jeton" in msg, msg
    assert integrations.probe_deck("dmaix", "", "")[0] is None
    ok, msg = integrations.probe_deck("dmaix", "127.0.0.1:18289", "", timeout=0.5)   # rien n'écoute
    assert ok is False and "DMAIX ne répond pas" in msg, msg
    e.stop()


def test_dmaix_catalog_picker_and_settings(tmp_path):
    """Aucune touche en dur : bibliothèque, inspecteur et Réglages s'appuient sur ce que décrit DMAIX."""
    from livedeck.ui.widgets import DeckKeyPicker
    assert summary(new_action("deck.key", app="dmaix", key="dmx.blackout", title="Blackout")) == "DMAIX · Blackout"
    assert presets.needs_setup("deck:dmaix", engine(tmp_path, start=False)) == "dmaix"
    fakes.FakeDmaix(18283)
    e, pid, _ = one_page(tmp_path, {}, {"dmaix": {"url": "127.0.0.1:18283"}})
    assert not e.deck("dmaix").wanted
    w = MainWindow(e)
    dlg = SettingsDialog(e, w)                      # fenêtre ouverte : DMAIX surveillé, même sans touche
    dlg.show()
    assert wait(lambda: e.deck("dmaix").connected, 6), e.deck("dmaix").error
    dlg._refresh_status()
    assert "touche(s)" in dlg.deck_status["dmaix"].text(), dlg.deck_status["dmaix"].text()
    dlg._test("dmaix")
    assert wait(lambda: "touche(s)" in dlg._tests["dmaix"][1].text(), 6), dlg._tests["dmaix"][1].text()
    lib = [it for c, items in presets.library(e) if c in presets.TOOL_NAMES for it in items]
    ids = {pid_ for pid_, _, _ in lib}
    assert {"deck:dmaix:scene.play:sc-1", "deck:dmaix:effect.toggle:fx-2", "deck:dmaix:effects.stopAll",
            "deck:dmaix:dmx.blackout", "deck:dmaix:output.status", "deck:dmaix:scene.current"} <= ids, ids
    b = presets.make("deck:dmaix:effect.toggle:fx-2", e)
    assert b["actions"][0] == {"type": "deck.key", "app": "dmaix", "key": "effect.toggle:fx-2", "title": "Chase"}
    picker = DeckKeyPicker(e, "scene.play:sc-1", "dmaix")
    groups = [picker.tree.topLevelItem(i).text(0) for i in range(picker.tree.topLevelItemCount())]
    assert groups == ["État", "Scènes", "Effets", "Commandes"], groups
    assert picker.tree.currentItem().data(0, Qt.UserRole) == "scene.play:sc-1"
    # Un retour d'état DMAIX réveille le service
    e.set_button(pid, "0,0", new_button(feedbacks=[fb("deckkey", app="dmaix", key="output.status", state="active")]))
    assert e.deck("dmaix").wanted
    url, token = dlg._deck_edits["dmaix"]
    url.setText("127.0.0.1:18284")
    token.setText("abc")
    dlg._save()
    assert e.cfg["integrations"]["dmaix"] == {"url": "127.0.0.1:18284", "token": "abc"}
    assert e.deck("dmaix").url == "http://127.0.0.1:18284" and not e.deck("dmaix").forced
    w.quit_app()


def test_winaudio_cache():
    """Le cache évite de rappeler Core Audio à chaque touche, et s'oublie après une écriture."""
    fake = fakes.FakeAudio()
    reads = []
    orig = fake.get
    fake.get = lambda t, app="": reads.append(t) or orig(t, app)
    c = winsys.CachedAudio(fake)
    for _ in range(10):
        c.get("output")
    assert len(reads) == 1, reads
    c.set_level("output", 12)
    assert c.get("output")[0] == 12.0 and len(reads) == 2, "une écriture doit invalider le cache"
    assert c.peek("app", "absent.exe") is None, "peek ne lève jamais"


def test_loupedeck_migration_v3_to_v4(tmp_path):
    """Une configuration v3 (Stream Deck seuls) se charge sans perte et gagne le bloc controls."""
    import json
    old = {"version": 3, "vmix": {"host": "127.0.0.1", "port": 8099}, "decks": {}, "options": {},
           "pages": [{"id": "p", "name": "Régie", "cols": 5, "rows": 3, "buttons": {
               "0,0": {"id": "a", "label": "Cam 1", "actions": [{"type": "vmix.preview", "input": "Cam 1"}]}}}]}
    path = Path(tmp_path) / "config.json"
    path.write_text(json.dumps(old), encoding="utf-8")
    cfg = load_config(path)
    assert cfg["version"] == 4
    assert cfg["pages"][0]["controls"] == {}, "le bloc controls doit exister et être vide"
    b = cfg["pages"][0]["buttons"]["0,0"]
    assert b["label"] == "Cam 1" and b["actions"][0]["input"] == "Cam 1", "la touche ne doit pas bouger"
    assert b["mode"] == "press" and b["auto_feedback"] is True, "les défauts doivent être complétés"


def test_loupedeck_rgb565():
    from PIL import Image
    px = ld.rgb565(Image.new("RGB", (1, 1), (255, 0, 0)))
    assert px == b"\x00\xf8", px.hex()                      # petit-boutiste
    assert ld.rgb565(Image.new("RGB", (1, 1), (255, 0, 0)), big_endian=True) == b"\xf8\x00"
    assert ld.rgb565(Image.new("RGB", (1, 1), (0, 0, 255))) == b"\x1f\x00"
    assert len(ld.rgb565(Image.new("RGB", (90, 90)))) == 90 * 90 * 2
    # découpage du flux : trame partielle gardée pour le tour suivant
    buf = bytearray(b"\x82\x03\x03\x02@\x82\x05\x05\x00")
    assert ld.split_frames(buf) == [b"\x03\x02@"]
    assert bytes(buf) == b"\x82\x05\x05\x00"


def test_audit_fixes(tmp_path):
    """Corrections de l'audit du cœur : config partielle, raccourci « + », doigts des deux écrans."""
    # config éditée à la main : version nulle, vmix sans port, option nulle -> complétée, jamais « abîmée »
    path = Path(tmp_path) / "config.json"
    path.write_text(json.dumps({"version": None, "vmix": {"host": "10.0.0.5"},
                                "options": {"health_interval": None}, "pages": []}), encoding="utf-8")
    cfg = load_config(path)
    assert cfg["vmix"] == {"host": "10.0.0.5", "port": 8099} and cfg["options"]["health_interval"] == 15
    assert cfg["pages"]
    # « ctrl++ » : le dernier « + » est une touche
    from livedeck import hotkeys
    from pynput.keyboard import Key, KeyCode
    assert hotkeys.parse("ctrl++") == [Key.ctrl, KeyCode.from_char("+")]
    assert hotkeys.parse("ctrl+plus") == hotkeys.parse("ctrl++") and hotkeys.parse("+") == [KeyCode.from_char("+")]
    assert hotkeys.parse("ctrl+a") == [Key.ctrl, KeyCode.from_char("a")]
    # Loupedeck : le doigt 0 de la molette ne termine pas l'appui du doigt 0 de la dalle
    events = []
    dev = ld.Loupedeck.__new__(ld.Loupedeck)
    dev._touches, dev._emit, dev.target = {}, events.append, lambda x, y: {"screen": "center"}
    pkt = b"\x00" + struct.pack(">HH", 100, 100) + b"\x00"
    dev._touch(ld.CMD_TOUCH, pkt)
    dev._touch(ld.CMD_TOUCH_CT, pkt)
    dev._touch(ld.CMD_TOUCH_END_CT, pkt)
    dev._touch(ld.CMD_TOUCH, pkt)
    assert [ev["phase"] for ev in events] == ["start", "start", "end", "move"], events
    # un nom de fichier venu d'Internet ne sort pas du dossier de mise à jour
    from livedeck import updater
    assert (updater.update_dir() / Path(r"..\..\evil.exe").name).parent == updater.update_dir()



def ic_deck_engine(tmp_path, fake, token=""):
    """Touches py-intercom décrites par /api/deck (action « Touche d'une app »), serveur sur un port libre."""
    keys = {"0,0": "ptt.toggle:regie", "0,1": "ptt.toggle:plateau", "0,2": "ptt.toggle:vmix",
            "0,3": "ptt.allOff", "0,4": "client.status"}
    buttons = {k: new_button(actions=[new_action("deck.key", app="intercom", key=v)]) for k, v in keys.items()}
    block = {"host": "127.0.0.1", "port": fake.port}
    if token:
        block["token"] = token
    return one_page(tmp_path, buttons, {"intercom": block})


def test_intercom_deck_keys_follow_the_stream(tmp_path):
    """py-intercom par l'API Stream Deck commune : état et touches lus sur /api/deck/state, mises à jour par le
    flux SSE (patch), commandes POST /api/deck/commands/<id>, accès libre (aucun en-tête Authorization) et
    jamais d'en-tête Origin ni Referer (py-intercom les refuse)."""
    ic = fakes.FakeIntercom(0)
    e, pg, logs = ic_deck_engine(tmp_path, ic)
    d = e.deck("intercom")
    assert d.name == "py-intercom" and d is not e.orchestream
    assert wait(lambda: d.connected and d.mode == "stream" and d.has_keys, 5), d.error
    assert e.intercom.wanted, "les touches décrites réveillent aussi le suivi des bus"
    assert not e.orchestream.wanted
    # état et touches : ce que décrit py-intercom
    assert d.get("token.name") == "accès libre" and d.get("client.online") is True
    assert [k["id"] for k in d.key_list()] == ["client.status", "ptt.toggle:regie", "ptt.toggle:plateau",
                                              "ptt.toggle:vmix", "ptt.allOff"]
    v = vis_of(e, pg, "0,0")
    assert v["label"].startswith("Régie"), v
    assert "Régie" in vis_of(e, pg, "0,0")["label"] and "Tout couper" in vis_of(e, pg, "0,3")["label"]
    # vMix est en mode Permanent : touche grisée avec la raison
    assert vis_of(e, pg, "0,2").get("disabled") or "Permanent" in str(d.key("ptt.toggle:vmix")["reason"])
    # appui : POST ptt.toggle avec le bus, sans Origin
    tap(e, pg, "0,0")
    assert wait(lambda: ("regie", "toggle") in ic.log and ic.buses["regie"]["active"], 3), ic.log
    # le flux pousse le patch : la touche passe en « live » sans interroger
    assert wait(lambda: (d.key("ptt.toggle:regie") or {}).get("tone") == "live", 3), d.key("ptt.toggle:regie")
    # changement côté client (raccourci, pad) : patch SSE
    ic.buses["plateau"]["active"] = True
    assert wait(lambda: (d.key("ptt.toggle:plateau") or {}).get("tone") == "live", 3)
    ic.buses["plateau"]["active"] = False
    assert wait(lambda: (d.key("ptt.toggle:plateau") or {}).get("tone") == "amber", 3)
    # tout couper
    tap(e, pg, "0,3")
    assert wait(lambda: ("all", "off") in ic.log and not ic.buses["regie"]["active"], 3), ic.log
    # poste hors ligne : les touches de parole se grisent avec la raison, « Tout couper » reste disponible
    ic.online = False
    assert wait(lambda: (d.key("ptt.toggle:regie") or {}).get("available") is False, 3)
    assert d.key("ptt.allOff")["available"] and not d.get("client.online")
    assert not ic.seen_origin, "LiveDeck n'envoie jamais Origin ni Referer"
    assert all(a is None for a in ic.auth), ic.auth
    assert ("GET", "/api/deck/events") in ic.requests
    assert not any(path in ("/status",) or path.startswith("/ptt") for _, path in ic.requests), ic.requests
    e.stop()


def test_intercom_deck_token_optional(tmp_path):
    """Jeton facultatif : absent = API ouverte ; si py-intercom en réclame un, « JETON ? » ; refusé, « JETON REFUSÉ » ;
    rempli, envoyé dans l'en-tête Authorization (jamais dans l'URL)."""
    ic = fakes.FakeIntercom(0, token="s3cret")
    e, pg, logs = ic_deck_engine(tmp_path, ic)
    d = e.deck("intercom")
    assert wait(lambda: d.auth_failed, 5), d.error
    assert d.unavailable() == "JETON ?" and "jeton" in d.error
    assert vis_of(e, pg, "0,0")["label"].endswith("JETON ?")
    e.mutate(lambda c: c["integrations"]["intercom"].update(token="faux"))
    assert wait(lambda: d.token == "faux" and d.auth_failed and d.unavailable() == "JETON REFUSÉ", 5), d.error
    e.mutate(lambda c: c["integrations"]["intercom"].update(token="s3cret"))
    d._wake.set()
    assert wait(lambda: d.connected and d.has_keys, 12), d.error
    assert d.get("token.name") == "jeton API" and "Bearer s3cret" in ic.auth  # le suivi des bus peut avoir envoyé l'ancien jeton juste avant
    assert e.intercom.token == "s3cret"
    assert wait(lambda: e.intercom.reachable and e.intercom.online, 5), e.intercom.error
    tap(e, pg, "0,1")
    assert wait(lambda: ("plateau", "toggle") in ic.log, 3), ic.log
    assert not any("s3cret" in path for _, path in ic.requests), "le jeton n'est jamais dans l'URL"
    assert not ic.seen_origin
    e.stop()
    # Test ponctuel : sans jeton refusé, avec jeton accepté
    ok, msg = integ.probe_intercom("127.0.0.1", ic.port)
    assert ok is False and "jeton" in msg, msg
    ok, msg = integ.probe_intercom("127.0.0.1", ic.port, token="s3cret")
    assert ok is True and "jeton API" in msg and "5 touche(s)" in msg, msg
    ic.token = None
    ok, msg = integ.probe_intercom("127.0.0.1", ic.port)
    assert ok is True and "accès libre" in msg, msg
    # 403 : un proxy qui ajoute Origin est reconnu
    msg = integ.explain_deck("intercom", f"http://127.0.0.1:{ic.port}", 403, {"error": {"code": "FORBIDDEN"}})
    assert "en-tête de navigateur" in msg


def test_intercom_deck_config_and_catalog(tmp_path):
    """py-intercom est un modèle d'app : adresse par défaut 127.0.0.1:5080, hôte + port réglés, jeton facultatif ;
    ses touches décrites apparaissent dans la bibliothèque."""
    assert integ.DECK_APPS["intercom"]["name"] == "py-intercom" and integ.DECK_APPS["intercom"]["port"] == 5080
    cfg = default_config()
    assert integ.deck_config(cfg, "intercom") == ("http://127.0.0.1:5080", "")
    cfg["integrations"]["intercom"] = {"host": "10.0.0.7", "port": 5099, "token": " abc "}
    assert integ.deck_config(cfg, "intercom") == ("http://10.0.0.7:5099", "abc")
    assert integ.deck_configured(cfg, "intercom")
    ic = fakes.FakeIntercom(0)
    e, pg, logs = ic_deck_engine(tmp_path, ic)
    assert wait(lambda: e.deck("intercom").has_keys, 5)
    from livedeck import presets
    ids = [pid for _, items in presets.library(e) for pid, _, _ in items]
    assert "deck:intercom:ptt.toggle:regie" in ids and "deck:intercom:ptt.allOff" in ids, ids
    rows = {k: ok for k, _, ok, _ in integ.integration_status(e)}
    assert rows["intercom"] is True and "py-intercom" not in [k for k in rows if k != "intercom"]
    e.stop()


# -- PTZ : vMix et caméras ONVIF -------------------------------------------------
def test_vmix_ptz_held_and_virtual(tmp_path):
    """PTZ de vMix : le mouvement part à l'appui avec sa vitesse et s'arrête au relâché (arrêt du même axe) ;
    une position de PTZ virtuel est un simple appui."""
    cam = "PTZ Plateau"
    e, pg, logs = one_page(tmp_path, {
        "0,0": new_button(label="G", actions=[new_action("vmix.ptz", input=cam, command="left", speed=30)]),
        "0,1": new_button(label="Z", actions=[new_action("vmix.ptz", input=cam, command="zoomin")]),
        "0,2": new_button(label="F", actions=[new_action("vmix.ptz", input=cam, command="focusfar")]),
        "0,3": new_button(label="V", actions=[new_action("vmix.ptz", input="PTZ - Couloir", command="virtual")]),
        "0,4": new_button(label="S", actions=[new_action("vmix.ptz", input=cam, command="stop")]),
        "1,0": new_button(label="?", actions=[new_action("vmix.ptz", input="Inconnu", command="left")])})
    fv = vmix_server()
    key_ = e.vmix.input_param(cam)
    mark = len(fv.functions)
    e.press(pg, "0,0", True)
    assert wait(lambda: sent(fv, mark, "PTZMoveLeft"))
    assert sent(fv, mark, "PTZMoveLeft")[0][1] == {"Input": key_, "Value": "0.3"}
    assert not sent(fv, mark, "PTZMoveStop") and e.visual(e.button(pg, "0,0"))["bg"].lower() == "#4040ff"
    e.press(pg, "0,0", False)
    assert wait(lambda: sent(fv, mark, "PTZMoveStop")) and sent(fv, mark, "PTZMoveStop")[0][1] == {"Input": key_}
    for k, start, stop in (("0,1", "PTZZoomIn", "PTZZoomStop"), ("0,2", "PTZFocusFar", "PTZFocusStop")):
        tap(e, pg, k)
        assert wait(lambda: sent(fv, mark, stop)), stop
        names = [f[0] for f in sent(fv, mark)]
        assert names.index(start) < names.index(stop) and sent(fv, mark, start)[0][1]["Value"] == "0.5"
    mark = len(fv.functions)
    tap(e, pg, "0,3")
    assert wait(lambda: sent(fv, mark, "PTZMoveToVirtualInputPosition"))
    assert sent(fv, mark)[0][1] == {"Input": e.vmix.input_param("PTZ - Couloir")} and len(sent(fv, mark)) == 1
    tap(e, pg, "0,4")
    assert wait(lambda: sent(fv, mark, "PTZZoomStop") and sent(fv, mark, "PTZMoveStop"))
    # input inconnu : rien ne part, ni à l'appui ni au relâché d'un mouvement jamais commencé… sauf l'arrêt, sans danger
    mark = len(fv.functions)
    tap(e, pg, "1,0")
    assert wait(lambda: any("introuvable" in m for _, m in logs))
    assert not sent(fv, mark, "PTZMoveLeft")
    # un mouvement maintenu est arrêté quand tout est relâché d'office (deck débranché, arrêt)
    mark = len(fv.functions)
    e.press(pg, "0,0", True)
    assert wait(lambda: sent(fv, mark, "PTZMoveLeft"))
    e.release_all(wait=True)
    assert wait(lambda: sent(fv, mark, "PTZMoveStop"))
    assert summary(new_action("vmix.ptz", input=cam, command="zoomout", speed=80)) == f"PTZ · {cam} · Zoom − (80 %)"
    assert not shown(next(f for f in ACTIONS["vmix.ptz"]["fields"] if f["key"] == "speed"),
                     new_action("vmix.ptz", command="home"))
    e.stop()


def onvif_engine(tmp_path, cam, buttons, password=fakes.FakeOnvif.PASSWORD, host=None):
    return one_page(tmp_path, buttons, {"onvif": {"user": fakes.FakeOnvif.USER, "password": password, "cameras": [
        {"name": "Plateau", "host": host or f"127.0.0.1:{cam.port}"}]}})


def onvif_btn(command, **kw):
    return new_button(label=command, actions=[new_action("onvif.ptz", camera="Plateau", command=command, **kw)])


def test_onvif_ptz(tmp_path):
    """Caméra ONVIF : identification datée à l'heure de la caméra, services joints sur l'adresse réglée,
    mouvement maintenu (arrêt du seul axe au relâché), presets par nom ou numéro, origine."""
    cam = fakes.FakeOnvif()
    e, pg, logs = onvif_engine(tmp_path, cam, {
        "0,0": onvif_btn("left", speed=40), "0,1": onvif_btn("zoomout"), "0,2": onvif_btn("preset", preset="large"),
        "0,3": onvif_btn("preset", preset="1"), "0,4": onvif_btn("home"), "1,0": onvif_btn("save", preset="Nouveau"),
        "1,1": onvif_btn("save", preset="Plateau"), "1,2": onvif_btn("stop"), "1,3": onvif_btn("preset", preset="Absent"),
        "1,4": onvif_btn("upright", speed=100)})
    assert e.onvif.wanted
    c = e.onvif.camera("Plateau")
    assert wait(lambda: c.reachable is True, 6), c.error
    assert c.profile == "main" and c.ptz_url == f"http://127.0.0.1:{cam.port}/onvif/ptz" and abs(c.offset - 3600) < 5
    assert c.preset_list == [("1", "Plateau"), ("2", "Large")]
    rows = {k: (ok, text) for k, _, ok, text in integ.integration_status(e)}
    assert rows["onvif"][0] is True and "2 preset(s)" in rows["onvif"][1]

    def last(op):
        return [a for o, a in cam.calls if o == op][-1]

    mark = len(cam.calls)
    e.press(pg, "0,0", True)
    assert wait(lambda: "ContinuousMove" in cam.ops(mark))
    assert last("ContinuousMove") == {"ProfileToken": "main", "PanTilt": (-0.4, 0.0)}   # pas de zoom envoyé
    assert "Stop" not in cam.ops(mark) and e.visual(e.button(pg, "0,0"))["bg"].lower() == "#4040ff"
    e.press(pg, "0,0", False)
    assert wait(lambda: "Stop" in cam.ops(mark))
    assert last("Stop") == {"ProfileToken": "main", "PanTilt": "true", "Zoom": "false"}
    tap(e, pg, "0,1")
    assert wait(lambda: cam.ops(mark)[-2:] == ["ContinuousMove", "Stop"] and len(cam.ops(mark)) == 4)
    assert [a for o, a in cam.calls if o == "ContinuousMove"][-1]["Zoom"] == (-0.5,)
    assert last("Stop") == {"ProfileToken": "main", "PanTilt": "false", "Zoom": "true"}
    tap(e, pg, "1,4")
    assert wait(lambda: len(cam.ops(mark)) == 6) and last("ContinuousMove")["PanTilt"] == (1.0, 1.0)
    tap(e, pg, "0,2")                                     # preset par son nom, sans tenir compte de la casse
    assert wait(lambda: "GotoPreset" in cam.ops(mark)) and last("GotoPreset")["PresetToken"] == "2"
    tap(e, pg, "0,3")                                     # … ou par son numéro
    assert wait(lambda: cam.ops(mark).count("GotoPreset") == 2) and last("GotoPreset")["PresetToken"] == "1"
    tap(e, pg, "0,4")
    assert wait(lambda: "GotoHomePosition" in cam.ops(mark))
    tap(e, pg, "1,0")                                     # preset inconnu : créé à ce nom
    assert wait(lambda: cam.presets.get("3") == "Nouveau")
    assert wait(lambda: ("3", "Nouveau") in c.preset_list) and any("enregistrée" in m for _, m in logs)
    tap(e, pg, "1,1")                                     # preset existant : remplacé, jamais dupliqué
    assert wait(lambda: cam.ops(mark).count("SetPreset") == 2)
    assert last("SetPreset") == {"ProfileToken": "main", "PresetToken": "1"} and len(cam.presets) == 3
    tap(e, pg, "1,2")
    assert wait(lambda: last("Stop") == {"ProfileToken": "main", "PanTilt": "true", "Zoom": "true"})
    tap(e, pg, "1,3")                                     # refus de la caméra : dit dans le Moniteur
    assert wait(lambda: any("ONVIF Plateau" in m and "preset token" in m for _, m in logs)), logs
    # relâché d'office : la caméra ne reste jamais en mouvement
    mark = len(cam.calls)
    e.press(pg, "0,0", True)
    assert wait(lambda: "ContinuousMove" in cam.ops(mark))
    e.release_all(wait=True)
    assert wait(lambda: "Stop" in cam.ops(mark))
    # bibliothèque : mouvements et presets de la caméra ; la touche créée rappelle le bon preset
    ids = [pid for _, items in presets.library(e) for pid, _, _ in items]
    assert "onvif:left:Plateau" in ids and "onvif:preset:Plateau|Large" in ids, ids
    b = presets.make("onvif:preset:Plateau|Large", e)
    assert b["actions"][0] == new_action("onvif.ptz", camera="Plateau", command="preset", preset="Large")
    assert presets.make("onvif:zoomin:Plateau", e)["actions"][0]["command"] == "zoomin"
    assert summary(b["actions"][0]) == "ONVIF · Plateau · preset « Large »"
    e.stop()


def test_onvif_errors_and_numbered_presets(tmp_path):
    """Mauvais mot de passe, caméra absente de la liste, caméra éteinte : la touche se grise et le Moniteur
    dit quoi vérifier. Caméra à emplacements numérotés : l'enregistrement retombe sur le numéro."""
    cam = fakes.FakeOnvif()
    e, pg, logs = onvif_engine(tmp_path, cam, {"0,0": onvif_btn("home")}, password="faux")
    c = e.onvif.camera("Plateau")
    assert wait(lambda: c.reachable is False, 6)
    assert "identifiants refusés" in c.error and "mot de passe" in c.error
    vis = e.visual(e.button(pg, "0,0"))
    assert vis["bg"].lower() == OFF_BG and "HORS LIGNE" in vis["label"]
    tap(e, pg, "0,0")
    assert wait(lambda: any("identifiants refusés" in m for _, m in logs)) and not cam.ops()
    # bon mot de passe : la caméra revient sans redémarrer LiveDeck
    e.mutate(lambda cfg: cfg["integrations"]["onvif"].update(password=fakes.FakeOnvif.PASSWORD))
    c = e.onvif.camera("Plateau")
    assert wait(lambda: c.reachable is True, 6)
    assert wait(lambda: e.visual(e.button(pg, "0,0"))["bg"].lower() != OFF_BG)
    cam.numbered = True
    e.set_button(pg, "0,1", onvif_btn("save", preset="7"))
    tap(e, pg, "0,1")
    assert wait(lambda: "7" in cam.presets), logs
    # caméra retirée de la liste, puis plus aucune caméra
    e.set_button(pg, "0,2", new_button(label="X", actions=[new_action("onvif.ptz", camera="Autre", command="home")]))
    assert wait(lambda: "CAMÉRA ?" in e.visual(e.button(pg, "0,2"))["label"])
    tap(e, pg, "0,2")
    assert wait(lambda: any("« Autre »" in m for _, m in logs))
    e.stop()
    # identifiants propres à une caméra dans son adresse ; caméra sans PTZ ; rien n'écoute
    assert onvif.probe(f"admin:secret@127.0.0.1:{cam.port}")[0] is True
    assert onvif.probe(f"127.0.0.1:{cam.port}")[0] is False
    ok, msg = onvif.probe(f"127.0.0.1:{fakes.FakeOnvif(ptz=False).port}", "admin", "secret")
    assert ok is False and "PTZ" in msg
    ok, msg = onvif.probe("127.0.0.1:1", "admin", "secret", timeout=1)
    assert ok is False and "caméra" in msg
    assert onvif.parse_cameras("A = 10.0.0.1\n\n10.0.0.2:8080\nA = 10.0.0.3\nB = u:p@cam.local") == [
        {"name": "A", "host": "10.0.0.1"}, {"name": "10.0.0.2:8080", "host": "10.0.0.2:8080"},
        {"name": "B", "host": "u:p@cam.local"}]
    assert onvif.split_host("u:p%40x@[fe80::1]:8000/onvif/dev") == ("http://[fe80::1]:8000/onvif/dev", "u", "p@x")


def test_onvif_settings_and_inspector(tmp_path):
    """Réglages → Intégrations : caméras et identifiants enregistrés en un bloc ; l'inspecteur propose les
    caméras réglées et les presets lus."""
    cam = fakes.FakeOnvif()
    e, w, pg = ui_window(tmp_path, {"0,0": onvif_btn("preset", preset="Large")})
    assert "onvif" not in e.cfg["integrations"]
    dlg = SettingsDialog(e, w, tab="integ")
    dlg.show()
    dlg.onvif_cams.setPlainText(f"Plateau = 127.0.0.1:{cam.port}")
    dlg.onvif_user.setText("admin")
    dlg.onvif_pass.setText("secret")
    dlg._test("onvif")
    assert wait(lambda: "PTZ prêt" in dlg._tests["onvif"][1].text(), 8), dlg._tests["onvif"][1].text()
    dlg._save()
    assert e.cfg["integrations"]["onvif"] == {"user": "admin", "password": "secret", "cameras": [
        {"name": "Plateau", "host": f"127.0.0.1:{cam.port}"}]}
    c = e.onvif.camera("Plateau")
    assert wait(lambda: c.reachable is True, 6)
    dlg = SettingsDialog(e, w, tab="integ")                # rouverts sans rien changer : pas d'étape « annuler »
    assert dlg.onvif_cams.toPlainText() == f"Plateau = 127.0.0.1:{cam.port}"
    n = len(e._undo)
    dlg._save()
    assert len(e._undo) == n
    a = e.button(pg, "0,0")["actions"][0]
    fields = {f["key"]: f for f in ACTIONS["onvif.ptz"]["fields"]}
    box = ui_widgets.make_field(e, fields["camera"], "Plateau", lambda v: None, item=a)
    assert box.count() == 1 and box.currentData() == "Plateau"
    box = ui_widgets.make_field(e, fields["preset"], "Large", lambda v: None, item=a)
    assert [box.itemText(i) for i in range(box.count())] == ["Plateau", "Large"] and box.currentText() == "Large"
    assert not shown(fields["preset"], new_action("onvif.ptz")) and shown(fields["speed"], new_action("onvif.ptz"))
    w.select("0,0")                                        # l'inspecteur affiche l'action et l'état de la caméra
    assert wait(lambda: any("ONVIF" in lb.text() for lb in w.inspector.findChildren(QLabel)))
    w.quit_app()



def test_key_labels_have_no_missing_glyph():
    """Aucun bouton prêt à l'emploi n'affiche un carré vide : chaque signe de son texte existe dans la police
    des touches, et les flèches absentes (◀ ▶ ❚) d'une touche déjà enregistrée sont remplacées au dessin."""
    from livedeck import render
    f = render.font(24)
    if not render._font_path() or "segoe" not in render._font_path().lower():
        return  # autre police (Linux) : ses signes ne sont pas ceux de l'exe Windows
    tofu = bytes(f.getmask("￿"))

    def missing(text):
        return [ch for ch in text.translate(render.GLYPH_FALLBACK) if not ch.isspace() and bytes(f.getmask(ch)) == tofu]

    for pid in presets.PRESETS:
        assert not missing(presets.make(pid)["label"]), pid
    for cmd in onvif.MOVES:
        assert not missing(presets.make(f"onvif:{cmd}:Cam")["label"]), cmd
    assert not missing("Page ▶ ◀ ▶ ❚❚ ✕")
    assert presets.make("page_next")["label"] == "Page ►" and presets.make("win_play")["label"] == "► ‖"
    old = render.render({"label": "▶", "bg": "#000000", "fg": "#ffffff"}, 72)
    new = render.render({"label": "►", "bg": "#000000", "fg": "#ffffff"}, 72)
    assert old.tobytes() == new.tobytes()



def test_deck_key_own_text_and_colors(tmp_path):
    """Touche décrite par une app : on peut garder son texte et ses couleurs. L'état de l'app reste visible
    (liseré quand elle est allumée) et une touche qui ne peut pas servir reste grisée avec sa raison."""
    from livedeck.feedback import ORCH_TONES
    e, dm, pg, logs = dmaix_engine(tmp_path, 18283)
    d = e.deck("dmaix")
    assert wait(lambda: d.connected and d.has_keys, 5), d.error

    def key_(text=None, colors=None, kid="scene.play:sc-2", label="BLEU", bg="#102030"):
        opts = {k: v for k, v in (("text", text), ("colors", colors)) if v}
        return new_button(label=label, bg=bg, fg="#ffeedd", actions=[new_action("deck.key", app="dmaix", key=kid, **opts)])

    assert "text" not in new_action("deck.key") and "colors" not in new_action("deck.key")  # rien d'ajouté par défaut
    assert "text" not in auto(key_())[0]["cond"] and auto(key_("own", "own"))[0]["cond"]["text"] == "own"
    for k, b in {"2,0": key_(), "2,1": key_("title"), "2,2": key_("own"), "2,3": key_("own_state"),
                 "2,4": key_("own", "own")}.items():
        e.set_button(pg, k, b)
    assert vis_of(e, pg, "2,0")["label"] == "Ambiance bleue" and vis_of(e, pg, "2,0")["bg"] == ORCH_TONES["neutral"][1]
    assert vis_of(e, pg, "2,2")["label"] == "BLEU" and vis_of(e, pg, "2,2")["bg"] == ORCH_TONES["neutral"][1]
    v = vis_of(e, pg, "2,4")
    assert (v["label"], v["bg"], v["fg"], v["bar"]) == ("BLEU", "#102030", "#ffeedd", ""), v
    tap(e, pg, "2,0")                                      # la scène joue : chaque touche le montre à sa façon
    assert wait(lambda: vis_of(e, pg, "2,0")["label"] == "Ambiance bleue\nEn cours", 2)
    lit = ORCH_TONES["success"][0]
    assert vis_of(e, pg, "2,1")["label"] == "Ambiance bleue" and vis_of(e, pg, "2,1")["bg"] == lit
    assert vis_of(e, pg, "2,2")["label"] == "BLEU" and vis_of(e, pg, "2,2")["bg"] == lit
    assert vis_of(e, pg, "2,3")["label"] == "BLEU\nEn cours"
    v = vis_of(e, pg, "2,4")
    assert (v["label"], v["bg"], v["fg"], v["bar"]) == ("BLEU", "#102030", "#ffeedd", lit), v
    # un retour d'état ajouté à la main passe par-dessus : sa propre couleur quand la touche est allumée
    b = key_("own", "own")
    b["feedbacks"] = [fb("deckkey", "#ff00ff", "#000000", app="dmaix", key="scene.play:sc-2", state="active")]
    e.set_button(pg, "2,4", b)
    assert vis_of(e, pg, "2,4")["bg"] == "#ff00ff" and vis_of(e, pg, "2,4")["label"] == "BLEU"
    # touche qui ne peut pas servir : grisée avec la raison, même avec ses propres couleurs et son texte
    e.set_button(pg, "2,4", key_("own", "own", kid="effects.stopAll", label="STOP"))
    v = vis_of(e, pg, "2,4")
    assert v["bg"] == OFF_BG and v["label"].startswith("STOP\nAucun effet"), v
    # sans texte à soi, « Mon texte » retombe sur le titre de l'app
    e.set_button(pg, "2,4", key_("own", kid="scene.play:sc-1", label=""))
    assert vis_of(e, pg, "2,4")["label"] == "Plein feu"
    # l'inspecteur propose les deux réglages
    keys = [f["key"] for f in ACTIONS["deck.key"]["fields"]]
    assert keys == ["app", "key", "text", "colors"]
    e.stop()


def test_tools_have_their_own_sections(tmp_path):
    """Chaque outil a sa section (état, rubriques annoncées par l'app) au lieu d'un bloc « Intégrations »,
    dans la bibliothèque comme dans le choix d'une touche vide ; la vignette montre la touche de l'app."""
    from livedeck.ui.deck_view import preset_thumb
    e, dm, pg, logs = dmaix_engine(tmp_path, 18284)
    assert wait(lambda: e.deck("dmaix").has_keys, 5)
    tools = {t["name"]: t for t in presets.tools(e)}
    assert list(tools) == presets.TOOL_NAMES == ["py-intercom", "orchestream", "py-multiview", "MixPanel", "DMAIX",
                                                  "Caméras ONVIF", "Tally32"]
    dmx = tools["DMAIX"]
    assert dmx["ok"] is True and "touche(s)" in dmx["status"]
    assert [g for g, _ in dmx["groups"]] == ["État", "Scènes", "Effets", "Commandes"]
    scenes = dict(dmx["groups"])["Scènes"]
    assert ("deck:dmaix:scene.play:sc-2", "Ambiance bleue", "") in scenes, scenes
    assert tools["py-intercom"]["groups"][0][0] == "Talkback"
    assert tools["orchestream"]["ok"] is None and tools["orchestream"]["groups"][0][1][0][0] == "deck:orchestream"
    cats = [c for c, _ in presets.library(e)]
    assert "Intégrations" not in cats and set(presets.TOOL_NAMES) <= set(cats)
    assert dict(presets.library(e))["DMAIX"][0][2].startswith("État")       # la rubrique précède la description
    assert presets.color("DMAIX") == "#ec4899" and presets.color("Vidéo") and presets.color("?", "x") == "x"
    assert [c for c, _ in presets.catalog(e)][:6] == ["Vidéo", *presets.TOOL_NAMES[:5]]
    a = preset_thumb(e, "deck:dmaix:scene.play:sc-2", "Ambiance bleue", 32).toImage()
    tap(e, pg, "0,2")
    assert wait(lambda: (e.deck("dmaix").key("scene.play:sc-2") or {}).get("active"), 2)
    b = preset_thumb(e, "deck:dmaix:scene.play:sc-2", "Ambiance bleue", 32).toImage()
    assert a != b, "la vignette suit l'état de la touche"
    e.stop()



def test_settings_one_page_per_tool_and_save_never_freezes(tmp_path):
    """Réglages → Intégrations : un outil par page, choisi dans une liste qui montre son état. Enregistrer
    ou fermer ne fige jamais l'interface : couper le flux en direct d'une app n'attend pas sa lecture."""
    dm = fakes.FakeDmaix(18285)
    fakes.FakeDmaix(18286)
    e, w, pg = ui_window(tmp_path, {"0,0": new_button(actions=[new_action("deck.key", app="dmaix", key="output.status")])},
                         {"dmaix": {"url": "127.0.0.1:18285"}, "mixpanel": {"url": "127.0.0.1:18286"}})
    d = e.deck("dmaix")
    assert wait(lambda: d.connected and d.mode == "stream", 5), d.error
    dlg = SettingsDialog(e, w, tab="integ", tool="dmaix")
    dlg.show()
    names = [dlg.tool_list.item(i).text() for i in range(dlg.tool_list.count())]
    assert names == presets.TOOL_NAMES and dlg.tool_stack.count() == len(names)
    assert dlg.tool_list.currentItem().data(Qt.UserRole) == "dmaix"
    url = dlg._deck_edits["dmaix"][0]
    assert url.isVisibleTo(dlg) and not dlg.ic_host.isVisibleTo(dlg) and not dlg.onvif_cams.isVisibleTo(dlg)
    dlg.show_tool("onvif")
    assert dlg.onvif_cams.isVisibleTo(dlg) and not url.isVisibleTo(dlg)
    # fenêtre ouverte : MixPanel (aucune touche) est surveillé lui aussi, son flux est ouvert
    mp = e.deck("mixpanel")
    assert wait(lambda: mp.connected and mp._resp is not None and d._resp is not None, 5), mp.error
    dlg._refresh_status()
    assert "touche(s)" in dlg.tool_list.item(names.index("DMAIX")).toolTip()
    # adresse changée + fermeture : deux flux coupés (DMAIX reconfiguré, MixPanel plus surveillé), sans attendre
    url.setText("127.0.0.1:18286")
    t0 = time.perf_counter()
    dlg._save()
    took = time.perf_counter() - t0
    assert took < 0.3, f"l'enregistrement a figé l'interface {took:.2f} s"
    assert wait(lambda: mp._resp is None and not mp.connected or not mp.active, 3)
    assert wait(lambda: d.connected and d.url.endswith(":18286"), 5), d.error   # … et DMAIX repart sur la nouvelle adresse
    assert dm is not None
    # la pastille d'un outil et « Réglages… » de l'inspecteur ouvrent directement sa page
    opened = []
    with mock.patch.object(MainWindow, "_run_dialog", staticmethod(lambda dlg_: opened.append(dlg_) or 0)):
        w.integ_pills["dmaix"].clicked.emit()
        w.open_settings("integ", "tallies")
    assert [o.tool_list.currentItem().data(Qt.UserRole) for o in opened] == ["dmaix", "tallies"]
    for o in opened:
        o.reject()
    w.quit_app()


if __name__ == "__main__":
    import tempfile
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            with tempfile.TemporaryDirectory() as d:
                try:
                    fn(d) if fn.__code__.co_argcount else fn()
                    print("OK   ", name)
                except Exception:
                    failed += 1
                    import traceback
                    traceback.print_exc()
                    print("ÉCHEC", name)
    # Fin du processus sans démontage, une fois les résultats écrits : sous Windows, décharger Qt (des
    # dizaines de fenêtres de test) pendant que des fils natifs tournent encore plante parfois (violation
    # d'accès) et faussait le code de retour alors que tous les tests étaient passés.
    code = 1 if failed else 0
    sys.stdout.flush()
    sys.stderr.flush()
    if sys.platform == "win32":
        import ctypes
        k32 = ctypes.windll.kernel32
        k32.GetCurrentProcess.restype = ctypes.c_void_p  # poignée 64 bits, sinon tronquée et l'appel échoue
        k32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        k32.TerminateProcess(k32.GetCurrentProcess(), code)
    os._exit(code)
