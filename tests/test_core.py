"""Tests de bout en bout : faux vMix, faux Stream Deck, faux py-intercom / orchestream / Tally32.

    python -m pytest tests      ou      python tests/test_core.py

Rien ne sort du PC : tous les serveurs sont simulés sur 127.0.0.1.
"""
import os
import sys
import threading
import time
from pathlib import Path

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
from livedeck.deck import DeckHandle, DeckManager  # noqa: E402
from livedeck.engine import Engine  # noqa: E402
from livedeck import vmix as vmix_mod  # noqa: E402
from livedeck.feedback import GREEN, RED, auto  # noqa: E402
from livedeck.model import (backup_config, config_text, deck_size, default_config, load_config,  # noqa: E402
                           new_button, new_page)
from livedeck.ui import theme  # noqa: E402
from livedeck.ui.diagnostic import DiagnosticDialog  # noqa: E402
from livedeck.ui.main_window import MainWindow  # noqa: E402
from livedeck.ui.search import SearchDialog  # noqa: E402
from livedeck.ui.settings import SettingsDialog  # noqa: E402
from livedeck.ui.widgets import InputCombo  # noqa: E402
from livedeck.vars import expand  # noqa: E402
from livedeck.vmix import VmixClient, _encode_query  # noqa: E402
from livedeck.webreq import parse_curl  # noqa: E402
from livedeck import integrations as integ, netsend, webreq  # noqa: E402
from livedeck.feedback import OFF_BG, fb  # noqa: E402
from livedeck.health import json_compare, json_path  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
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
    assert [n for n, _ in e.orchestream.names()] == ["twizz", "quiz"]
    orch.emit("poll:overlay:toggle", {"isVisible": True})    # overlay appris en direct
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
    assert wait(lambda: "flux en direct ouvert" in dlg._tests["orchestream"][1].text(), 6)
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
