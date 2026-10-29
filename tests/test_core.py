"""Tests de bout en bout : faux vMix, faux Stream Deck, faux py-intercom / orchestream / Tally32.

    python -m pytest tests      ou      python tests/test_core.py

Rien ne sort du PC : tous les serveurs sont simulés sur 127.0.0.1.
"""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

import fakes  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from livedeck.actions import new_action  # noqa: E402
from livedeck.companion_import import import_companion  # noqa: E402
from livedeck.deck import DeckHandle, DeckManager  # noqa: E402
from livedeck.engine import Engine  # noqa: E402
from livedeck.feedback import RED  # noqa: E402
from livedeck.model import default_config, load_config, new_button  # noqa: E402
from livedeck.ui import theme  # noqa: E402
from livedeck.ui.diagnostic import DiagnosticDialog  # noqa: E402
from livedeck.ui.main_window import MainWindow  # noqa: E402
from livedeck.ui.search import SearchDialog  # noqa: E402
from livedeck.ui.settings import SettingsDialog  # noqa: E402
from livedeck.ui.widgets import InputCombo  # noqa: E402
from livedeck.webreq import parse_curl  # noqa: E402

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
    if cfg is None:
        cfg, _ = import_companion(FIXTURE)
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


# -- import Companion -----------------------------------------------------------
def test_import_companion():
    cfg, report = import_companion(FIXTURE)
    names = [p["name"] for p in cfg["pages"]]
    assert {"StreamDeck XL", "StreamDeck 1", "StreamDeck 2"} <= set(names), names
    assert set(cfg["decks"]) == {"DEMOXL0000001", "DEMOSD0000001", "DEMOSD0000002"}
    assert report
    integ = cfg["integrations"]
    assert "orchestream.local" in integ["orchestream"]["url"]
    assert {t["host"] for t in integ["tallies"]} >= {"10.0.0.51", "10.0.0.52", "10.0.0.53"}
    types = {a["type"] for p in cfg["pages"] for b in p["buttons"].values() for a in b["actions"]}
    assert {"vmix.preview", "vmix.program", "orchestream.overlay", "tally.reconnect", "intercom.ptt"} <= types, types


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
    assert not w.inspector.inner.isEnabled() and w.inspector.test_btn.isEnabled()
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


# -- intégrations maison ---------------------------------------------------------
def integrations_engine(tmp_path):
    ic, orch = fakes.FakeIntercom(18192), fakes.FakeOrchestream(18193)
    t1, t2 = fakes.FakeTally(18194, True), fakes.FakeTally(18195, False)
    cfg, _ = import_companion(FIXTURE)
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
    sys.exit(1 if failed else 0)
