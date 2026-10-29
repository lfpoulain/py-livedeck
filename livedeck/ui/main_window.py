"""Fenêtre principale."""
from __future__ import annotations

import copy
import html
import json
import os
import time

from PySide6.QtCore import QObject, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (QApplication, QCheckBox, QFileDialog, QFrame, QHBoxLayout, QInputDialog, QLabel,
                               QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QSystemTrayIcon,
                               QTabBar, QVBoxLayout, QWidget)

from .. import __version__, presets
from ..companion_import import import_companion
from ..model import DECK_SIZES, HOME, key, new_id, normalize_config, parse_key
from . import theme as T
from .deck_view import DeckGrid
from .diagnostic import DiagnosticDialog
from .inspector import Inspector
from .library import LibraryPanel
from .search import SearchDialog
from .settings import SettingsDialog
from .widgets import Seg, label

HINT = ("Clic : sélectionner  ·  Glisser : déplacer (Ctrl = copier)  ·  Clic droit : options  ·  "
        "Appuie une touche du Stream Deck pour la retrouver ici")
HINT_LIVE = "Mode Live : un clic déclenche la touche comme sur le Stream Deck  ·  F5 pour revenir à l'édition"


class Bridge(QObject):
    event = Signal(str, object)


def icon_pixmap(size: int = 64) -> QPixmap:
    """Icône de l'app : un mini Stream Deck dont une touche est à l'antenne."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(size / 64, size / 64)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#14171d"))
    p.drawRoundedRect(QRectF(2, 2, 60, 60), 14, 14)
    colors = [T.PGM, "#2a303b", "#2a303b", "#2a303b", T.ACCENT, "#2a303b", "#2a303b", "#2a303b", T.PVW]
    for i, c in enumerate(colors):
        p.setBrush(QColor(c))
        p.drawRoundedRect(QRectF(12 + (i % 3) * 14, 12 + (i // 3) * 14, 11, 11), 3, 3)
    p.end()
    return pm


def app_icon() -> QIcon:
    icon = QIcon()
    for s in (16, 32, 64, 256):
        icon.addPixmap(icon_pixmap(s))
    return icon


class StatusPill(QLabel):
    def __init__(self, name: str):
        super().__init__()
        self.setObjectName("Pill")
        self.name = name
        self.set(None, "")

    def set(self, ok: bool | None, detail: str):
        dot = T.OK if ok else T.FAINT if ok is None else T.DANGER
        self.setText(f"<span style='color:{dot}'>●</span>&nbsp; <b>{self.name}</b>"
                     f"&nbsp;<span style='color:{T.MUTED}'>{html.escape(detail)}</span>")


class Tray(QSystemTrayIcon):
    """Icône près de l'horloge : LiveDeck continue de piloter les decks fenêtre fermée."""

    def __init__(self, win: "MainWindow"):
        super().__init__(app_icon(), win)
        self.win = win
        menu = QMenu()
        menu.addAction("Afficher LiveDeck", win.bring_to_front)
        self.live_act = menu.addAction("Mode Live", lambda: win.set_live(self.live_act.isChecked()))
        self.live_act.setCheckable(True)
        menu.addAction("Diagnostic Stream Deck…", lambda: (win.bring_to_front(), win.open_diagnostic()))
        menu.addSeparator()
        menu.addAction("Quitter LiveDeck", win.quit_app)
        self.setContextMenu(menu)
        self._menu = menu
        self.activated.connect(lambda reason: reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick)
                               and win.bring_to_front())
        if self.isSystemTrayAvailable():
            self.show()

    def sync(self):
        self.live_act.setChecked(self.win.grid.live)
        e = self.win.engine
        decks = len(e.decks.info()) if e.decks else 0
        self.setToolTip(f"LiveDeck — vMix {'connecté' if e.vmix.state.connected else 'hors ligne'}, "
                        f"{decks} Stream Deck{'s' if decks > 1 else ''}" + ("  ·  LIVE" if self.win.grid.live else ""))


class MainWindow(QMainWindow):
    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        self.clipboard: dict | None = None
        self.page_id = engine.pages[0]["id"]
        self.setWindowTitle(f"LiveDeck {__version__}")
        self.setWindowIcon(app_icon())
        self.resize(1560, 900)
        self.setMinimumSize(1200, 640)

        self.bridge = Bridge()
        self.bridge.event.connect(self._on_event)
        self._listener = lambda kind, payload: self.bridge.event.emit(kind, payload)
        engine.listeners.append(self._listener)
        self._live_timer = QTimer(self, singleShot=True, interval=30, timeout=self._refresh_live)
        self._quitting = False
        self._tray_hint_shown = False
        self._toast_timer = QTimer(self, singleShot=True, interval=4000, timeout=self._end_toast)

        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        v = QVBoxLayout(root)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(self._header())
        body = QHBoxLayout()
        body.setContentsMargins(14, 14, 14, 14)
        body.setSpacing(14)
        self.library = LibraryPanel(engine)
        self.inspector = Inspector(engine, self)
        body.addWidget(self.library)
        body.addLayout(self._center(), 1)
        body.addWidget(self.inspector)
        v.addLayout(body, 1)

        self.library.input_activated.connect(self._input_activated)
        self.library.preset_activated.connect(self._preset_activated)
        self.tray = Tray(self)
        self._shortcuts()
        self._rebuild_tabs()
        self.grid.set_page(self.page_id)
        self._refresh_live()
        QTimer(self, interval=1000, timeout=self._update_pills).start()

    # -- en-tête --------------------------------------------------------------
    def _header(self) -> QFrame:
        h = QFrame()
        h.setObjectName("Header")
        h.setFixedHeight(62)
        lay = QHBoxLayout(h)
        lay.setContentsMargins(20, 0, 16, 0)
        lay.setSpacing(10)
        logo = QLabel()
        logo.setPixmap(app_icon().pixmap(24, 24))
        lay.addWidget(logo)
        brand = QHBoxLayout()
        brand.setSpacing(0)
        for text, name in (("Live", "Brand"), ("Deck", "BrandAccent")):
            brand.addWidget(label(text, name))
        lay.addLayout(brand)
        lay.addStretch(1)
        self.deck_pill = StatusPill("Stream Deck")
        self.vmix_pill = StatusPill("vMix")
        lay.addWidget(self.deck_pill, 0, Qt.AlignVCenter)
        lay.addWidget(self.vmix_pill, 0, Qt.AlignVCenter)
        lay.addSpacing(10)
        self.mode = Seg([("edit", "Édition"), ("live", "Live")], "edit", live_last=True)
        self.mode.buttons["edit"].setToolTip("Clic sur une touche = la sélectionner pour l'éditer")
        self.mode.buttons["live"].setToolTip("Clic sur une touche = la déclencher, comme sur le Stream Deck (F5)")
        self.mode.changed.connect(lambda m: self.set_live(m == "live"))
        lay.addWidget(self.mode, 0, Qt.AlignVCenter)
        self.follow = QCheckBox("Suivre le Stream Deck")
        self.follow.setChecked(True)
        self.follow.setToolTip("Appuyer sur une touche physique la sélectionne dans l'éditeur")
        lay.addWidget(self.follow)
        lay.addSpacing(6)
        imp = QPushButton("Importer")
        menu = QMenu(imp)
        menu.addAction("Config Bitfocus Companion…", self.import_companion)
        menu.addAction("Configuration LiveDeck (.json)…", self.load_config_file)
        menu.addSeparator()
        menu.addAction("Exporter la configuration…", self.export_config)
        menu.addAction("Ouvrir le dossier des données", lambda: hasattr(os, "startfile") and os.startfile(HOME))
        imp.setMenu(menu)
        lay.addWidget(imp)
        diag = QPushButton("Diagnostic")
        diag.setObjectName("Ghost")
        diag.setToolTip("Vérifier les Stream Decks : écrans, chaque touche, performances")
        diag.clicked.connect(self.open_diagnostic)
        lay.addWidget(diag)
        self.mon_btn = QPushButton("Moniteur")
        self.mon_btn.setObjectName("Ghost")
        self.mon_btn.setCheckable(True)
        self.mon_btn.setToolTip("Journal des actions envoyées et des erreurs")
        self.mon_btn.toggled.connect(lambda on: self.monitor.setVisible(on))
        lay.addWidget(self.mon_btn)
        gear = QPushButton("Réglages")
        gear.setObjectName("Ghost")
        gear.clicked.connect(self.open_settings)
        lay.addWidget(gear)
        return h

    def _center(self) -> QVBoxLayout:
        center = QVBoxLayout()
        center.setSpacing(10)
        row = QHBoxLayout()
        row.setSpacing(4)
        self.tabs = QTabBar()
        self.tabs.setExpanding(False)
        self.tabs.setDrawBase(False)
        self.tabs.setUsesScrollButtons(True)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.tabs.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tabs.customContextMenuRequested.connect(lambda pos: self._page_menu().exec(self.tabs.mapToGlobal(pos)))
        row.addWidget(self.tabs, 1)
        find = QPushButton("Rechercher")
        find.setObjectName("Ghost")
        find.setToolTip("Trouver une touche sur toutes les pages (Ctrl+F)")
        find.clicked.connect(self.open_search)
        row.addWidget(find)
        add = QPushButton("＋ Page")
        add.setObjectName("Ghost")
        add.clicked.connect(self.add_page)
        row.addWidget(add)
        more = QPushButton("⋯")
        more.setObjectName("Ghost")
        more.setToolTip("Options de la page")
        more.clicked.connect(lambda: self._page_menu().exec(more.mapToGlobal(more.rect().bottomLeft())))
        row.addWidget(more)
        center.addLayout(row)

        self.grid = DeckGrid(self.engine)
        self.grid.selected.connect(lambda k: self.inspector.load(self.page_id, k or None))
        self.grid.pressed.connect(lambda k, down: self.engine.press(self.page_id, k, down))
        self.grid.moved.connect(lambda s, d, c: self.engine.move_button((self.page_id, s), (self.page_id, d), c))
        self.grid.inputs_dropped.connect(self._drop_inputs)
        self.grid.preset_dropped.connect(self._drop_preset)
        self.grid.context.connect(self._cell_menu)
        center.addWidget(self.grid, 1)
        self.hint = label(HINT, "Hint")
        self.hint.setAlignment(Qt.AlignCenter)
        center.addWidget(self.hint)
        self.monitor = QPlainTextEdit()
        self.monitor.setObjectName("Monitor")
        self.monitor.setReadOnly(True)
        self.monitor.setMaximumBlockCount(400)
        self.monitor.setFixedHeight(150)
        self.monitor.hide()
        center.addWidget(self.monitor)
        return center

    def _shortcuts(self):
        for seq, fn in ((QKeySequence.Undo, self.undo), (QKeySequence.Redo, self.redo), ("Ctrl+Y", self.redo),
                        (QKeySequence.Copy, self.copy), (QKeySequence.Paste, self.paste),
                        (QKeySequence.Cut, self.cut), ("Ctrl+D", self.duplicate_selected),
                        (QKeySequence.Delete, self.delete_selected), ("F5", lambda: self.set_live(not self.grid.live)),
                        (Qt.Key_Escape, lambda: self.grid.select(None)), (QKeySequence.Find, self.open_search)):
            QShortcut(QKeySequence(seq), self, activated=fn)

    # -- événements moteur ----------------------------------------------------
    def _on_event(self, kind: str, payload):
        if kind == "state":
            if not self._live_timer.isActive():
                self._live_timer.start()
        elif kind == "config":
            if not self.engine.page(self.page_id):
                self.page_id = self.engine.pages[0]["id"]
                self.grid.set_page(self.page_id)
            self._rebuild_tabs()
            self.grid.update()
            self.inspector.on_config()
            self.library.presets.refresh()
        elif kind == "log":
            self._log(*payload)
        elif kind == "decks":
            self._update_pills()
            self._rebuild_tabs()
            self.grid.update()
        elif kind == "page":
            self._goto_page(payload)
        elif kind == "activity":
            self._activity(*payload)

    def _activity(self, page_id: str, k: str, deck: str | None):
        if deck and self.follow.isChecked() and not self.grid.live:
            if page_id != self.page_id:
                self._goto_page(page_id)
            if k != self.grid.sel:
                self.grid.select(k)
        if page_id == self.page_id:
            self.grid.flash(k)

    def _refresh_live(self):
        self.grid.update()
        self.library.inputs.refresh()
        self.inspector.refresh_preview()
        self._update_pills()

    def _log(self, level: str, msg: str):
        mark = {"error": "✕", "warn": "!", "action": "▶"}.get(level, "·")
        self.monitor.appendPlainText(f"{time.strftime('%H:%M:%S')}  {mark} {msg}")
        if level == "error":
            self.toast(msg, error=True)

    def toast(self, msg: str, error: bool = False):
        self.hint.setObjectName("Toast")
        self.hint.setStyleSheet(f"color:{T.DANGER}" if error else "")
        self.hint.setText(msg)
        self.hint.style().unpolish(self.hint)
        self.hint.style().polish(self.hint)
        self._toast_timer.start()

    def _end_toast(self):
        self.hint.setObjectName("Hint")
        self.hint.setStyleSheet("")
        self.hint.setText(HINT_LIVE if self.grid.live else HINT)
        self.hint.style().unpolish(self.hint)
        self.hint.style().polish(self.hint)

    def _update_pills(self):
        if hasattr(self, "tray"):
            self.tray.sync()
        st, vm = self.engine.vmix.state, self.engine.vmix
        if st.connected:
            lat = vm.latency_ms
            detail = f"v{st.version}" + ("" if lat is None else f"  ·  {lat:.0f} ms" if lat >= 1 else "  ·  < 1 ms")
            self.vmix_pill.set(True, detail)
            self.vmix_pill.setToolTip(f"{st.inputs and len(st.inputs)} inputs · temps aller-retour moyen d'une commande")
        else:
            self.vmix_pill.set(False, f"déconnecté  ·  {self.engine.cfg['vmix']['host']}")
            self.vmix_pill.setToolTip("LiveDeck réessaie toutes les 2 secondes")
        dm = self.engine.decks
        info = dm.info() if dm else []
        if dm is None:
            self.deck_pill.set(None, "désactivé")
        elif dm.error:
            self.deck_pill.set(False, "indisponible")
            self.deck_pill.setToolTip(dm.error)
        else:
            self.deck_pill.set(bool(info), f"{len(info)} connecté{'s' if len(info) > 1 else ''}" if info
                               else "aucun détecté")
            self.deck_pill.setToolTip("\n".join(f"{d['model']} · {d['serial']}" for d in info)
                                      or "Ferme Companion et le logiciel Elgato pour libérer les decks")

    # -- pages ----------------------------------------------------------------
    def _rebuild_tabs(self):
        on_deck = {d["page"] for d in (self.engine.decks.info() if self.engine.decks else [])}
        self.tabs.blockSignals(True)
        while self.tabs.count():
            self.tabs.removeTab(0)
        for p in self.engine.pages:
            i = self.tabs.addTab(("●  " if p["id"] in on_deck else "") + p["name"])
            self.tabs.setTabData(i, p["id"])
            self.tabs.setTabToolTip(i, f"{p['name']} — grille {p['cols']}×{p['rows']}"
                                    + (" — affichée sur un Stream Deck" if p["id"] in on_deck else "")
                                    + "\nClic droit : renommer, taille, dupliquer, supprimer")
            if p["id"] == self.page_id:
                self.tabs.setCurrentIndex(i)
        self.tabs.blockSignals(False)

    def _tab_changed(self, i: int):
        pid = self.tabs.tabData(i)
        if pid and pid != self.page_id:
            self.page_id = pid
            self.grid.set_page(pid)
            self.inspector.load(None, None)

    def _goto_page(self, target: str):
        ids = [p["id"] for p in self.engine.pages]
        idx = ids.index(self.page_id) if self.page_id in ids else 0
        pid = {"next": ids[(idx + 1) % len(ids)], "prev": ids[(idx - 1) % len(ids)]}.get(target, target)
        if pid in ids:
            self.tabs.setCurrentIndex(ids.index(pid))

    def _page_menu(self) -> QMenu:
        m = QMenu(self)
        m.addAction("Renommer…", self.rename_page)
        size = m.addMenu("Taille de la grille")
        for name, (c, r) in DECK_SIZES.items():
            size.addAction(name, lambda c=c, r=r: self.engine.update_page(self.page_id, cols=c, rows=r))
        m.addAction("Dupliquer la page", self.duplicate_page)
        m.addAction("Déplacer à gauche", lambda: self.engine.move_page(self.page_id, -1))
        m.addAction("Déplacer à droite", lambda: self.engine.move_page(self.page_id, 1))
        m.addSeparator()
        rm = m.addAction("Supprimer la page", self.delete_page)
        rm.setEnabled(len(self.engine.pages) > 1)
        return m

    def add_page(self):
        names = list(DECK_SIZES)
        info = self.engine.decks.info() if self.engine.decks else []
        default = next((i for i, n in enumerate(names) if info and DECK_SIZES[n] == (info[0]["cols"], info[0]["rows"])), 0)
        size, ok = QInputDialog.getItem(self, "Nouvelle page", "Modèle de Stream Deck :", names, default, False)
        if not ok:
            return
        name, ok = QInputDialog.getText(self, "Nouvelle page", "Nom de la page :", text=f"Page {len(self.engine.pages) + 1}")
        if ok and name.strip():
            self.page_id = self.engine.add_page(name.strip(), *DECK_SIZES[size])
            self._rebuild_tabs()
            self.grid.set_page(self.page_id)

    def rename_page(self):
        p = self.engine.page(self.page_id)
        name, ok = QInputDialog.getText(self, "Renommer la page", "Nom :", text=p["name"])
        if ok and name.strip():
            self.engine.update_page(self.page_id, name=name.strip())

    def duplicate_page(self):
        p = copy.deepcopy(self.engine.page(self.page_id))
        p["id"], p["name"] = new_id(), p["name"] + " (copie)"
        for b in p["buttons"].values():
            b["id"] = new_id()
        self.engine.mutate(lambda cfg: cfg["pages"].append(p))
        self._goto_page(p["id"])

    def delete_page(self):
        p = self.engine.page(self.page_id)
        if QMessageBox.question(self, "Supprimer la page", f"Supprimer « {p['name']} » et ses "
                                f"{len(p['buttons'])} touches ? (Ctrl+Z pour annuler)") == QMessageBox.Yes:
            self.engine.remove_page(self.page_id)

    # -- touches --------------------------------------------------------------
    def set_live(self, live: bool):
        self.grid.set_live(live)
        self.mode.set("live" if live else "edit")
        self.inspector.set_locked(live)
        if hasattr(self, "tray"):
            self.tray.sync()
        if not self._toast_timer.isActive():
            self._end_toast()

    def _editable(self) -> bool:
        if self.grid.live:
            self.toast("Mode Live : édition verrouillée  ·  F5 pour modifier", error=True)
            return False
        return True

    def _target(self) -> str | None:
        """Touche visée par un double-clic : la sélection, sinon la première case libre."""
        k = self.grid.sel or self.engine.first_free(self.page_id)
        if not k:
            self.toast("La page est pleine : sélectionne une touche à remplacer.", error=True)
        return k

    def _input_activated(self, title: str):
        if not self._editable():
            return
        k = self._target()
        if not k:
            return
        mode = self.library.inputs.drop_mode()
        if mode == "pair":
            self.engine.assign_input(self.page_id, k, title, "preview")
            r, c = parse_key(k)
            if r + 1 < self.engine.page(self.page_id)["rows"]:
                self.engine.assign_input(self.page_id, key(r + 1, c), title, "program")
        else:
            self.engine.assign_input(self.page_id, k, title, mode)
        nxt = self.grid.next_key(k)
        self.grid.select(nxt or k)
        self.toast(f"« {title} » assigné  ·  touche suivante sélectionnée  ·  Ctrl+Z pour annuler")

    def _preset_activated(self, pid: str):
        if not self._editable():
            return
        k = self._target()
        if k:
            self._drop_preset(k, pid)
            self.grid.select(k)

    def _drop_preset(self, k: str, pid: str):
        b = presets.make(pid, self.engine)
        if b:
            self.engine.set_button(self.page_id, k, b)
            self.inspector.load(self.page_id, k, force=True)

    def _drop_inputs(self, k: str, titles: list[str]):
        mode = self.library.inputs.drop_mode()
        page = self.engine.page(self.page_id)
        r0, c0 = parse_key(k)
        placed = []

        def apply(cfg):
            p = next(p for p in cfg["pages"] if p["id"] == self.page_id)
            r, c = r0, c0
            step = 2 if mode == "pair" else 1
            for t in titles:
                if c >= p["cols"]:
                    r, c = r + step, 0
                if r >= p["rows"]:
                    break
                if mode == "pair":
                    p["buttons"][key(r, c)] = presets.input_button(t, "preview")
                    if r + 1 < p["rows"]:
                        p["buttons"][key(r + 1, c)] = presets.input_button(t, "program")
                else:
                    p["buttons"][key(r, c)] = presets.input_button(t, mode)
                placed.append(t)
                c += 1

        if len(titles) == 1 and mode != "pair":
            self.engine.assign_input(self.page_id, k, titles[0], mode)
            placed = titles
        else:
            self.engine.mutate(apply)
        skipped = len(titles) - len(placed)
        self.toast(f"{len(placed)} touche(s) créée(s) sur « {page['name']} »"
                   + (f", {skipped} hors de la grille" if skipped else "") + "  ·  Ctrl+Z pour annuler")
        self.inspector.load(self.page_id, k, force=True)

    def _cell_menu(self, k: str, pos):
        b = self.engine.button(self.page_id, k)
        m = QMenu(self)
        if self.grid.live:
            if b:
                m.addAction("▶  Tester", lambda: (self.engine.press(self.page_id, k, True),
                                                 self.engine.press(self.page_id, k, False)))
                m.exec(pos)
            return
        if b:
            m.addAction("▶  Tester", lambda: (self.engine.press(self.page_id, k, True),
                                             self.engine.press(self.page_id, k, False)))
            m.addSeparator()
            m.addAction("Copier", self.copy)
            m.addAction("Couper", self.cut)
        paste = m.addAction("Coller", self.paste)
        paste.setEnabled(self.clipboard is not None)
        if b:
            m.addAction("Dupliquer", self.duplicate_selected)
            send = m.addMenu("Envoyer vers la page")
            for p in self.engine.pages:
                if p["id"] != self.page_id:
                    act = send.addAction(p["name"], lambda pid=p["id"]: self._send_to(k, pid))
                    act.setEnabled(self.engine.first_free(p["id"]) is not None)
            m.addSeparator()
            m.addAction("Effacer", self.delete_selected)
        m.exec(pos)

    def _send_to(self, k: str, pid: str):
        dst = self.engine.send_to_page(self.page_id, k, pid)
        if dst:
            r, c = parse_key(dst)
            self.toast(f"Touche envoyée sur « {self.engine.page(pid)['name']} », rangée {r + 1} colonne {c + 1}")

    def _selected(self):
        return self.engine.button(self.page_id, self.grid.sel) if self.grid.sel else None

    def copy(self):
        b = self._selected()
        if b:
            self.clipboard = copy.deepcopy(b)
            self.toast("Touche copiée  ·  Ctrl+V pour la coller")

    def cut(self):
        if not self._editable():
            return
        if self._selected():
            self.copy()
            self.delete_selected()

    def paste(self):
        if not self._editable():
            return
        if self.clipboard and self.grid.sel:
            self.engine.set_button(self.page_id, self.grid.sel, {**copy.deepcopy(self.clipboard), "id": new_id()})
            self.inspector.load(self.page_id, self.grid.sel, force=True)

    def delete_selected(self):
        if not self._editable():
            return
        if self._selected():
            self.engine.set_button(self.page_id, self.grid.sel, None)
            self.toast("Touche effacée  ·  Ctrl+Z pour annuler")

    def duplicate_selected(self):
        if not self._editable():
            return
        b = self._selected()
        free = self.engine.first_free(self.page_id)
        if b and free:
            r, c = parse_key(self.grid.sel)
            page = self.engine.page(self.page_id)
            right = key(r, c + 1)
            target = right if c + 1 < page["cols"] and right not in page["buttons"] else free
            self.engine.set_button(self.page_id, target, {**copy.deepcopy(b), "id": new_id()})
            self.grid.select(target)

    def undo(self):
        if not self._editable():
            return
        if self.engine.undo():
            self.inspector.load(self.page_id, self.grid.sel, force=True)
            self.toast("Annulé")

    def redo(self):
        if not self._editable():
            return
        if self.engine.redo():
            self.inspector.load(self.page_id, self.grid.sel, force=True)
            self.toast("Rétabli")

    # -- fichiers -------------------------------------------------------------
    def import_companion(self):
        path, _ = QFileDialog.getOpenFileName(self, "Config Companion", "",
                                              "Companion (*.companionconfig *.json);;Tous (*.*)")
        if not path:
            return
        try:
            cfg, report = import_companion(path)
        except Exception as e:
            QMessageBox.warning(self, "Import impossible", f"Ce fichier n'a pas pu être lu :\n{e}")
            return
        box = QMessageBox(self)
        box.setWindowTitle("Import Companion")
        box.setText(f"<b>{report[0]}</b>")
        box.setInformativeText("Remplacer la configuration actuelle, ou ajouter ces pages à la suite ?")
        box.setDetailedText("\n".join(report[1:]) or "Tout a été repris.")
        replace = box.addButton("Remplacer", QMessageBox.AcceptRole)
        add = box.addButton("Ajouter les pages", QMessageBox.ActionRole)
        box.addButton("Annuler", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() == replace:
            self.engine.replace_config(cfg)
            self.engine.set_vmix(cfg["vmix"]["host"], cfg["vmix"]["port"])
        elif box.clickedButton() == add:
            def apply(c):
                c["pages"].extend(cfg["pages"])
                c["decks"].update(cfg["decks"])
            self.engine.mutate(apply)
            if self.engine.decks:
                self.engine.decks.reassign_pages()
        else:
            return
        self.page_id = self.engine.pages[0]["id"]
        self._rebuild_tabs()
        self.grid.set_page(self.page_id)
        self.inspector.load(None, None)
        for line in report:
            self.engine.log("info", line)
        self.toast(report[0])

    def export_config(self):
        path, _ = QFileDialog.getSaveFileName(self, "Exporter", "livedeck.json", "JSON (*.json)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.engine.cfg, f, ensure_ascii=False, indent=2)
            self.toast(f"Configuration exportée : {path}")

    def load_config_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Charger", "", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                cfg = normalize_config(json.load(f))
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, "Chargement impossible", str(e))
            return
        self.engine.replace_config(cfg)
        self.engine.set_vmix(cfg["vmix"]["host"], cfg["vmix"]["port"])
        self.page_id = self.engine.pages[0]["id"]
        self._rebuild_tabs()
        self.grid.set_page(self.page_id)

    def open_settings(self):
        SettingsDialog(self.engine, self).exec()
        self._update_pills()

    def open_diagnostic(self):
        DiagnosticDialog(self.engine, self).exec()

    def open_search(self):
        dlg = SearchDialog(self.engine, self)
        if dlg.exec() and dlg.chosen:
            page_id, k = dlg.chosen
            self._goto_page(page_id)
            self.grid.select(k)
            self.grid.flash(k)

    # -- fenêtre / barre des tâches -------------------------------------------
    def bring_to_front(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def quit_app(self):
        self._quitting = True
        self.close()

    def closeEvent(self, e):
        keep = (not self._quitting and self.engine.cfg["options"].get("close_to_tray", True)
                and self.tray.isVisible())
        if keep:
            e.ignore()
            self.hide()
            if not self._tray_hint_shown:
                self._tray_hint_shown = True
                self.tray.showMessage("LiveDeck reste actif", "Les Stream Decks continuent de fonctionner. "
                                      "Clic droit sur l'icône → Quitter pour fermer LiveDeck.", self.tray.icon(), 4000)
            return
        self.engine.listeners.remove(self._listener)
        self.engine.stop()
        self.tray.hide()
        super().closeEvent(e)
        QApplication.quit()
