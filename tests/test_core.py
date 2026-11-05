"""Tests de bout en bout : faux vMix, faux Stream Deck, faux py-intercom / orchestream / Tally32.

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
from livedeck import integrations as integ, netsend, webreq, winsys  # noqa: E402
from livedeck import presets  # noqa: E402
from livedeck.actions import ACTIONS, execute, is_relative, shown, summary  # noqa: E402
from livedeck.feedback import OFF_BG, fb  # noqa: E402
from livedeck.health import json_compare, json_path  # noqa: E402
from PySide6.QtCore import QMimeData, QPointF, Qt  # noqa: E402
from PySide6.QtWidgets import QLabel, QPlainTextEdit  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "demo.companionconfig"
VMIX_PORT = 18191
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
    assert not types & {"orchestream.overlay", "tally.reconnect", "intercom.ptt"}, types
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
    assert {"vmix.preview", "vmix.program", "orchestream.overlay", "tally.reconnect", "intercom.ptt"} <= types, types
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
    assert btns["0,0"]["actions"][0]["type"] == "orchestream.overlay"
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


# -- intégrations maison ---------------------------------------------------------
def integrations_engine(tmp_path):
    ic, orch = fakes.FakeIntercom(18192), fakes.FakeOrchestream(18193)
    t1, t2 = fakes.FakeTally(18194, True), fakes.FakeTally(18195, False)
    cfg, _ = import_companion(FIXTURE, integrations=True)
    cfg["integrations"] = {"intercom": {"host": "127.0.0.1", "port": 18192}, "orchestream": {"url": "127.0.0.1:18193"},
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
    # orchestream : la touche suit l'overlay, même basculé ailleurs
    assert wait(lambda: e.orchestream.connected)
    tap(e, sd2, "2,4")
    assert wait(lambda: orch.visible["twizz"] and e.orchestream.overlays["twizz"])
    orch.set("twizz", False)
    assert wait(lambda: e.orchestream.overlays["twizz"] is False)
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


def multiview_engine(tmp_path, port=18196):
    """Faux py-multiview + une page de touches qui le pilotent."""
    mv = fakes.FakeMultiview(port)
    cfg = default_config()
    page = cfg["pages"][0]
    page.update(name="MV", cols=5, rows=3)
    # auto_feedback : la couleur de la touche est déduite de l'action par le moteur
    page["buttons"]["0,0"] = new_button(label="Émission",
                                        actions=[new_action("multiview.control", target="sender")])
    page["buttons"]["0,1"] = new_button(label="Flux",
                                        actions=[new_action("multiview.control", target="stream", ref="Caméra 1")])
    page["buttons"]["0,2"] = new_button(label="Volume",
                                        actions=[new_action("multiview.volume", value=35)])
    page["buttons"]["0,3"] = new_button(label="Retour",
                                        actions=[new_action("multiview.control", target="monitor", mode="on")])
    cfg["integrations"]["multiview"] = {"url": f"127.0.0.1:{port}"}
    e = engine(tmp_path, cfg=cfg)
    assert wait(lambda: e.multiview.reachable)
    return e, mv, page["id"]


def test_multiview_drives_py_multiview(tmp_path):
    e, mv, pg = multiview_engine(tmp_path)

    # l'état lu alimente la touche : vert quand l'émission tourne
    assert e.multiview.running("sender") is False
    tap(e, pg, "0,0")
    assert wait(lambda: mv.sender)
    assert wait(lambda: e.visual(e.button(pg, "0,0"))["bg"].lower() == "#15803d")
    # « bascule » est résolu depuis l'état connu : la 2e pression envoie « off », pas « toggle »
    tap(e, pg, "0,0")
    assert wait(lambda: not mv.sender)
    assert mv.calls[:2] == ["sender/on", "sender/off"], mv.calls

    # un flux s'adresse par son nom, accents compris
    tap(e, pg, "0,1")
    assert wait(lambda: mv.streams[0]["running"])
    assert "stream/Caméra 1/on" in mv.calls

    # mode absolu : « Démarrer » reste « on » même répété
    tap(e, pg, "0,3")
    tap(e, pg, "0,3")
    assert wait(lambda: mv.monitor)
    assert [c for c in mv.calls if c.startswith("monitor/")] == ["monitor/on", "monitor/on"]

    # volume
    tap(e, pg, "0,2")
    assert wait(lambda: mv.volume == 35)


def test_multiview_reports_refusals_and_absence(tmp_path):
    e, mv, pg = multiview_engine(tmp_path, 18197)
    logs = []
    e.listeners.append(lambda k, pl: logs.append(pl) if k == "log" else None)

    mv.refuse = "aucun écran assigné"
    tap(e, pg, "0,0")
    assert wait(lambda: any("aucun écran assigné" in m for _, m in logs)), logs

    # py-multiview éteint : la touche le dit au lieu de rester verte
    mv.refuse = ""
    mv.srv.shutdown()
    assert wait(lambda: e.multiview.reachable is False, 8)
    assert e.multiview.unavailable() == "HORS LIGNE"
    assert e.multiview.running("sender") is None


def test_multiview_auto_feedback_colours():
    b = new_button(label="Émission", actions=[new_action("multiview.control", target="sender")])
    states = {f["cond"]["state"]: f.get("bg") or f.get("bar") for f in auto(b)}
    assert states == {"on": "#15803d", "error": "#7c2d12", "unknown": "#5a6273"}
    # le son coupé est un défaut, pas une marche : rouge
    muted = new_button(actions=[new_action("multiview.control", target="mute", ref="1")])
    assert next(f["bg"] for f in auto(muted) if f["cond"]["state"] == "on") == "#cc0000"


def test_multiview_address_and_probe():
    assert integrations.multiview_url("192.168.1.42") == "http://192.168.1.42:5090"
    assert integrations.multiview_url("192.168.1.42:5099") == "http://192.168.1.42:5099"
    assert integrations.multiview_url("http://poste/") == "http://poste:5090"
    assert integrations.multiview_url("") == ""
    mv = fakes.FakeMultiview(18198)
    ok, msg = integrations.probe_multiview("127.0.0.1:18198")
    assert ok is True and "1/2 flux en lecture" in msg, msg
    mv.srv.shutdown()
    ok, msg = integrations.probe_multiview("127.0.0.1:18198")
    assert ok is False and "py-multiview" in msg


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
    assert ok is False and "Contrôle HTTP" in msg and "Depuis le réseau" in msg and "2.0" in msg, msg
    ic = fakes.FakeIntercom(18221)
    ok, msg = integ.probe_intercom("127.0.0.1", 18221)
    assert ok is True and "Régie" in msg, msg
    ic.online = False
    ok, msg = integ.probe_intercom("127.0.0.1", 18221)
    assert ok is None and "connecte-le au serveur" in msg, msg
    ic.online = True
    fakes.FakeTally(18222, True)                                       # autre programme sur ce port
    assert "pas l'API de py-intercom" in integ.probe_intercom("127.0.0.1", 18222)[1]
    j = fakes.FakeJson(18223, {})
    j.status = 404
    assert "trop ancien" in integ.probe_intercom("127.0.0.1", 18223)[1]
    # API fidèle à py-intercom : numéros de bus, paramètres d'URL, /ptt/all/on refusé
    assert integ._http("POST", "http://127.0.0.1:18221/ptt/0/on?x=1", direct=True)[0] == 200
    assert ic.buses["regie"]["active"]
    assert integ._http("POST", "http://127.0.0.1:18221/ptt/all/on", direct=True)[0] == 400
    integ._http("POST", "http://127.0.0.1:18221/ptt/all/off", direct=True)

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
    assert wait(lambda: e.intercom.reachable is False and "Contrôle HTTP" in e.intercom.error, 5), e.intercom.error
    assert "HORS LIGNE" in vis("0,0")["label"]
    logs.clear()
    tap(e, pid, "0,0")
    assert wait(lambda: any(lv == "error" and "Contrôle HTTP" in m for lv, m in logs), 5), logs
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
        status, data = integ._http("GET", "http://127.0.0.1:18225/status", timeout=2, direct=True)
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
    orch = fakes.FakeOrchestream(18232)
    tally = fakes.FakeTally(18233, False)
    tally.old_firmware = True
    e, pid, logs = one_page(tmp_path, {"0,0": new_button(label="Tallys", actions=[new_action("tally.reconnect")])}, {
        "intercom": {"host": "127.0.0.1", "port": 18231},
        "orchestream": {"url": "127.0.0.1:18232", "overlays": [{"name": "twizz", "label": "Twizz"},
                                                                {"name": "quiz", "label": "Quiz"}]},
        "tallies": [{"name": "Cam 1", "host": "127.0.0.1:18233"}]})
    assert not e.intercom.wanted and not e.orchestream.wanted
    w = MainWindow(e)
    dlg = SettingsDialog(e, w)                      # fenêtre ouverte : tout est surveillé, même sans touche
    dlg.show()
    assert wait(lambda: e.intercom.online and e.orchestream.connected, 6)
    dlg._refresh_status()
    assert "En ligne" in dlg.ic_status.text() and "Quiz" in dlg.orch_status.text(), dlg.orch_status.text()
    assert [n for n, _ in e.orchestream.names()][:2] == ["twizz", "quiz"]
    orch.set("poll", True)                          # overlay de la v2 absent des réglages : appris en direct
    assert wait(lambda: e.orchestream.overlays.get("poll") is True)
    assert "poll" in [n for n, _ in e.orchestream.names()]
    # « Tester » essaie les valeurs saisies et classe le résultat
    result = dlg._tests["intercom"][1]
    dlg.ic_port.setValue(18234)
    dlg._test("intercom")
    assert wait(lambda: "Contrôle HTTP" in result.text(), 6), result.text()
    dlg.ic_port.setValue(18231)
    dlg._test("intercom")
    assert wait(lambda: "en ligne" in result.text(), 6), result.text()
    dlg._test("orchestream")
    assert wait(lambda: "orchestream-v2 joignable" in dlg._tests["orchestream"][1].text(), 6)
    dlg._test("tallies")
    assert wait(lambda: "pas connecté à vMix" in dlg._tests["tallies"][1].text(), 6)
    dlg.orch_overlays.setText("twizz, quiz = Le Quiz, poll")
    dlg._save()
    assert e.cfg["integrations"]["orchestream"]["overlays"][1] == {"name": "quiz", "label": "Le Quiz"}
    assert not e.intercom.forced and not e.orchestream.forced
    # bibliothèque : intégrations d'après les services réglés
    from livedeck import presets
    lib = dict(presets.library(e))["Intégrations"]
    assert {"talk:regie", "orch:quiz", "tallies"} <= {pid_ for pid_, _, _ in lib}, lib
    assert presets.make("orch:quiz", e)["label"] == "Le Quiz"
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
    assert cats[:2] == ["Vidéo", "Intégrations"] and {"Production", "Clavier", "Web", "Réseau", "Deck"} <= set(cats)
    ids = {pid_ for _, items in ins.picker_items() for pid_, *_ in items}
    assert {"take", "tallies", "talk_alloff", "http", "osc", "page_next"} <= ids
    assert any(i.startswith("talk:") for i in ids) and ("orch" in ids)
    ins._pick_filter(text="intercom")
    assert {c for c, _ in ins.picker_items()} == {"Intégrations"}
    names = [n for _, items in ins.picker_items() for _, n, _, _ in items]
    assert "Raccourci maintenu" not in names        # le PTT clavier n'est pas confondu avec py-intercom
    ins._pick_filter(text="", cat="Clavier")
    assert [c for c, _ in ins.picker_items()] == ["Clavier"]
    assert dict((p, s) for _, items in presets.catalog(e) for p, _, _, s in items)["orch"] == "orchestream"
    ins._pick_filter(cat="")
    # une intégration à régler : la touche est créée et l'inspecteur montre son état + « Réglages… »
    ins._create("orch")
    assert e.button(pid, "0,0")["actions"][0]["type"] == "orchestream.overlay"
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
    lib.search.setText("intercom")
    shown = [lib.list.item(r) for r in range(lib.list.count()) if not lib.list.item(r).isHidden()]
    heads = [it.data(Qt.UserRole + 4) for it in shown if it.data(Qt.UserRole + 2) == "head"]
    assert heads == ["Intégrations"] and len(shown) > 1, heads
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
    assert tiles["talk:plateau"] == ("py-intercom, bascule", "") and tiles["talk:regie"][1] == ""
    # import « Ajouter les pages » : les réglages trouvés par la conversion sont repris
    imp = {"integrations": {"orchestream": {"url": "http://orch.local", "overlays": [
        {"name": "twizz", "label": "Twizz"}, {"name": "quiz", "label": "Le Quiz"}]},
        "tallies": [{"name": "Cam 1", "host": "10.0.0.11"}, {"name": "Cam 2", "host": "10.0.0.12"}]}}
    cur = {"integrations": {"orchestream": {"url": ""}, "tallies": [{"name": "A", "host": "10.0.0.11"}]}}
    notes = integ.merge_integrations(cur, imp)
    assert cur["integrations"]["orchestream"]["url"] == "http://orch.local" and len(notes) == 3, notes
    assert "quiz" in {o["name"] for o in cur["integrations"]["orchestream"]["overlays"]}
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
    tile.resize(170, tile.height())
    tile.show()
    app.processEvents()
    assert "…" not in tile.text() and len(tile.text().splitlines()) == 3, tile.text()
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


def test_orchestream_v2(tmp_path):
    from livedeck.integrations import probe_orchestream
    orch = fakes.FakeOrchestream(18251)
    orch.visible["poll"] = True                     # déjà à l'antenne avant le lancement de LiveDeck
    e, pid, logs = one_page(tmp_path, {
        "0,0": new_button(label="Twizz", actions=[new_action("orchestream.overlay", overlay="twizz")]),
        "0,1": new_button(label="Sondage", actions=[new_action("orchestream.overlay", overlay="poll")]),
        "0,2": new_button(label="Speedizz", actions=[new_action("orchestream.overlay", overlay="speedizz",
                                                                mode="show")])},
        {"orchestream": {"url": "127.0.0.1:18251"}})
    o = e.orchestream
    # la v2 garde l'état : connu dès la connexion, sans attendre un changement
    assert wait(lambda: o.connected and o.api == "v2", 6)
    assert o.overlays == {"twizz": False, "poll": True, "speedizz": False}, o.overlays
    assert wait(lambda: any(orch.subscribed.values()))
    # basculer : show puis hide, la réponse de la v2 met l'état à jour
    tap(e, pid, "0,0")
    assert wait(lambda: orch.visible["twizz"] and o.overlays["twizz"] is True)
    tap(e, pid, "0,1")
    assert wait(lambda: orch.visible["poll"] is False and o.overlays["poll"] is False)
    # changé depuis la régie d'orchestream : suivi par Transmit (le ping est ignoré)
    orch.ping()
    orch.set("twizz", False)
    assert wait(lambda: o.overlays["twizz"] is False)
    orch.set("poll", True)
    assert wait(lambda: o.overlays["poll"] is True)
    # rien à afficher : le 409 OVERLAY_EMPTY est expliqué tel quel
    tap(e, pid, "0,2")
    assert wait(lambda: any("lancez une partie" in m for _, m in logs)), logs
    assert not orch.visible["speedizz"]
    assert probe_orchestream("127.0.0.1:18251")[0] is True
    assert "à l'antenne : Sondage" in probe_orchestream("127.0.0.1:18251")[1]
    # adresse publique (tunnel) : la v2 refuse la régie, le message dit quoi faire
    orch.external = True
    ok, msg = probe_orchestream("127.0.0.1:18251")
    assert ok is False and "adresse locale" in msg, msg
    e.stop()


def test_orchestream_v1_still_supported(tmp_path):
    orch = fakes.FakeOrchestream(18252, v1=True)
    e, pid, logs = one_page(tmp_path, {
        "0,0": new_button(label="Twizz", actions=[new_action("orchestream.overlay", overlay="twizz")])},
        {"orchestream": {"url": "127.0.0.1:18252"}})
    o = e.orchestream
    assert wait(lambda: o.connected and o.api == "v1", 6)
    tap(e, pid, "0,0")
    assert wait(lambda: orch.visible["twizz"] and o.overlays["twizz"] is True)
    orch.set("twizz", False)
    assert wait(lambda: o.overlays["twizz"] is False)
    orch.emit("quiz:overlay:toggle", {"isVisible": True})    # overlay appris en direct
    assert wait(lambda: o.overlays.get("quiz") is True)
    from livedeck.integrations import probe_orchestream
    assert "v1" in probe_orchestream("127.0.0.1:18252")[1]
    e.stop()


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
