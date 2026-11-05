"""Fenêtre principale."""
from __future__ import annotations

import copy
import html
import json
import os
import time

from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QRectF, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout,
                               QInputDialog, QLabel, QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QPushButton,
                               QSizePolicy, QSplitter, QSystemTrayIcon, QTabBar, QVBoxLayout, QWidget)

from .. import __version__, presets
from ..actions import to_take
from ..companion_import import import_companion
from ..integrations import integration_status, merge_integrations
from ..loupedeck import control_kind
from ..model import (CONFIG_VERSION, DECK_SIZES, HOME, key, new_id, normalize_button, normalize_config,
                     parse_control, parse_key, surface_of)
from . import theme as T
from .deck_view import ControlBar, DeckGrid
from .diagnostic import DiagnosticDialog
from .dialogs import LEGEND, FillRowDialog, ImportOptionsDialog, ShortcutsDialog
from .inspector import Inspector
from .library import LibraryPanel
from .search import SearchDialog
from .settings import SettingsDialog
from .widgets import Seg, install_wheel_guard, label, tame_combo, typo

HINT = ("Clic : sélectionner  ·  Glisser : déplacer (Ctrl = copier)  ·  Clic droit : options  ·  "
        "Entrée : tester  ·  F1 : aide")
TOAST_MS = {"error": 9000, "warn": 6000}
MONITOR_FILTERS = [("all", "Tout"), ("problems", "Erreurs et alertes"), ("action", "Actions envoyées")]
INTEG_PILLS = [("intercom", "Intercom"), ("orchestream", "Overlays"), ("tallies", "Tallys")]
HINT_LOCK = "Verrouillé : plus d'édition ni de clic sur les touches (les Stream Decks restent actifs)  ·  Ctrl+L pour déverrouiller"
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
    clicked = Signal()

    def __init__(self, name: str):
        super().__init__()
        self.setObjectName("Pill")
        self.name = name
        self.ok = None
        self.setCursor(Qt.PointingHandCursor)
        self.set(None, "")

    def set(self, ok: bool | None, detail: str):
        self.ok = ok
        dot = T.OK if ok else T.FAINT if ok is None else T.DANGER
        self.setText(f"<span style='color:{dot}'>●</span>&nbsp; <b>{self.name}</b>"
                     + (f"&nbsp;<span style='color:{T.MUTED}'>{html.escape(detail)}</span>" if detail else ""))

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(e)


class Tray(QSystemTrayIcon):
    """Icône près de l'horloge : LiveDeck continue de piloter les decks fenêtre fermée."""

    def __init__(self, win: "MainWindow"):
        super().__init__(app_icon(), win)
        self.win = win
        menu = QMenu()
        menu.addAction("Afficher LiveDeck", win.bring_to_front)
        self.live_act = menu.addAction("Mode Live", lambda: win.set_live(self.live_act.isChecked()))
        self.live_act.setCheckable(True)
        self.lock_act = menu.addAction("Verrouiller (pas de fausse manip)", lambda: (win.bring_to_front(), win.toggle_lock()))
        self.lock_act.setCheckable(True)
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
        self.lock_act.setChecked(self.win.locked)
        self.live_act.setEnabled(not self.win.locked)
        e = self.win.engine
        decks = len(e.decks.info()) if e.decks else 0
        self.setToolTip(f"LiveDeck — vMix {'connecté' if e.vmix.state.connected else 'hors ligne'}, "
                        f"{decks} Stream Deck{'s' if decks > 1 else ''}" + ("  ·  LIVE" if self.win.grid.live else "") + ("  ·  VERROUILLÉ" if self.win.locked else ""))


class MainWindow(QMainWindow):
    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        self.clipboard: dict | None = None
        self.locked = False
        self._lockable: list = []  # ce que le verrou désactive
        self.page_id = engine.pages[0]["id"]
        self.setWindowTitle(f"LiveDeck {__version__}")
        self.setWindowIcon(app_icon())
        self.resize(1560, 900)
        self.setMinimumSize(1240, 640)
        install_wheel_guard()

        self.bridge = Bridge()
        self.bridge.event.connect(self._on_event)
        self._listener = lambda kind, payload: self.bridge.event.emit(kind, payload)
        engine.listeners.append(self._listener)
        self._live_timer = QTimer(self, singleShot=True, interval=30, timeout=self._refresh_live)
        self._quitting = False
        self._tray_hint_shown = False
        self._toast_timer = QTimer(self, singleShot=True, interval=4000, timeout=self._end_toast)
        self._entries: list[tuple[str, str]] = []  # journal du moniteur (niveau, ligne)
        self.unread = {"error": 0, "warn": 0}
        # disposition (largeur des colonnes) : fichier .ini à côté de la config, jamais le registre Windows
        self._ui = QSettings(str(Path(engine.config_path).parent / "ui.ini"), QSettings.IniFormat)

        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        v = QVBoxLayout(root)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(self._header())
        body = QHBoxLayout()
        body.setContentsMargins(14, 14, 14, 14)
        self.library = LibraryPanel(engine)
        self.inspector = Inspector(engine, self)
        center = QWidget()
        center.setLayout(self._center())
        # colonnes redimensionnables (poignées entre les panneaux), jamais repliées
        self.split = QSplitter(Qt.Horizontal)
        self.split.setHandleWidth(14)
        self.split.setChildrenCollapsible(False)
        for i, w in enumerate((self.library, center, self.inspector)):
            self.split.addWidget(w)
            self.split.setStretchFactor(i, 1 if i == 1 else 0)
        self.split.setSizes([290, 860, 400])
        try:
            state = self._ui.value("splitter")
            if state is not None:
                self.split.restoreState(state)
        except Exception:  # fichier abîmé : disposition par défaut
            pass
        self._save_ui_timer = QTimer(self, singleShot=True, interval=600, timeout=self._save_ui)
        self.split.splitterMoved.connect(lambda *_: self._save_ui_timer.start())
        body.addWidget(self.split, 1)
        v.addLayout(body, 1)

        self.library.input_activated.connect(self._input_activated)
        self.library.preset_activated.connect(self._preset_activated)
        self.library.inputs.fill_requested.connect(lambda titles: self.open_fill_row(self.grid.sel, titles))
        self.tray = Tray(self)
        self._shortcuts()
        self._rebuild_tabs()
        self.grid.set_page(self.page_id)
        self.controls.set_page(self.page_id)
        self._refresh_live()
        self._lockable += [self.library, self.inspector]
        if self._saved_lock():  # verrou gardé d'un lancement à l'autre (relance en plein direct)
            self.set_locked(True)
        QTimer(self, interval=1000, timeout=self._update_pills).start()
        QTimer.singleShot(0, self._show_migration_notes)

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
        self.vmix_pill.clicked.connect(lambda: self.open_settings("general"))
        self.deck_pill.clicked.connect(self.open_diagnostic)
        # une pastille par intégration utilisée par au moins une touche ; clic : Réglages → Intégrations
        self.integ_pills = {}
        for k, name in INTEG_PILLS:
            pill = StatusPill(name)
            pill.clicked.connect(lambda: self.open_settings("integ"))
            pill.hide()
            lay.addWidget(pill, 0, Qt.AlignVCenter)
            self.integ_pills[k] = pill
        self.integ_all = StatusPill("Intégrations")
        self.integ_all.clicked.connect(lambda: self.open_settings("integ"))
        self.integ_all.hide()
        lay.addWidget(self.integ_all, 0, Qt.AlignVCenter)
        lay.addSpacing(10)
        self.mode = Seg([("edit", "Édition"), ("live", "Live")], "edit", live_last=True)
        self.mode.buttons["edit"].setToolTip("Clic sur une touche = la sélectionner pour l'éditer")
        self.mode.buttons["live"].setToolTip("Clic sur une touche = la déclencher, comme sur le Stream Deck (F5)")
        self.mode.changed.connect(lambda m: self.set_live(m == "live"))
        lay.addWidget(self.mode, 0, Qt.AlignVCenter)
        self.lock_btn = QPushButton("Verrouiller")
        self.lock_btn.setObjectName("LockBtn")
        self.lock_btn.setCheckable(True)
        self.lock_btn.setToolTip("Verrouille l'écran pendant un direct : plus d'édition ni de clic sur les touches. "
                                 "Les Stream Decks restent actifs (Ctrl+L)")
        self.lock_btn.clicked.connect(self.toggle_lock)
        lay.addWidget(self.lock_btn, 0, Qt.AlignVCenter)
        self._lockable.append(self.mode)
        self.follow = QCheckBox("Suivre le deck")
        self.follow.setChecked(True)
        self.follow.setToolTip("Suivre le Stream Deck : appuyer sur une touche physique la sélectionne dans "
                               "l'éditeur")
        lay.addWidget(self.follow)
        lay.addSpacing(6)
        imp = QPushButton("Fichier")
        imp.setToolTip("Importer, exporter, diagnostic des Stream Decks, aide")
        menu = QMenu(imp)
        menu.addSection("Importer")
        self._lockable += [menu.addAction("Config Bitfocus Companion…", self.import_companion),
                           menu.addAction("Configuration LiveDeck (.json)…", self.load_config_file),
                           menu.addAction("Une page (.json)…", self.import_page)]
        menu.addSection("Exporter")
        menu.addAction("La configuration…", self.export_config)
        menu.addAction("La page affichée…", self.export_page)
        menu.addSeparator()
        menu.addAction("Diagnostic Stream Deck…", self.open_diagnostic).setToolTip(
            "Vérifier les Stream Decks : écrans, chaque touche, performances")
        menu.addAction("Raccourcis et légende   F1", self.open_help)
        menu.addAction("Ouvrir le dossier des données", lambda: hasattr(os, "startfile") and os.startfile(HOME))
        menu.setToolTipsVisible(True)
        imp.setMenu(menu)
        lay.addWidget(imp)
        self.mon_btn = QPushButton("Moniteur")
        self.mon_btn.setObjectName("Ghost")
        self.mon_btn.setCheckable(True)
        self.mon_btn.setToolTip("Journal des actions envoyées et des erreurs")
        self.mon_btn.toggled.connect(self._toggle_monitor)
        lay.addWidget(self.mon_btn)
        gear = QPushButton("Réglages")
        gear.setObjectName("Ghost")
        gear.clicked.connect(self.open_settings)
        lay.addWidget(gear)
        self._lockable.append(gear)
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
        self.tabs.customContextMenuRequested.connect(
            lambda pos: self.locked or self._page_menu().exec(self.tabs.mapToGlobal(pos)))
        row.addWidget(self.tabs, 1)
        legend = QPushButton("?")
        legend.setObjectName("Ghost")
        legend.setToolTip(LEGEND + "<br><br>Clic : raccourcis clavier et légende (F1)")
        legend.clicked.connect(self.open_help)
        row.addWidget(legend)
        find = QPushButton("Rechercher")
        find.setObjectName("Ghost")
        find.setToolTip("Trouver une touche sur toutes les pages (Ctrl+F)")
        find.clicked.connect(self.open_search)
        row.addWidget(find)
        self._lockable.append(find)
        add = QPushButton("＋ Page")
        add.setObjectName("Ghost")
        add.clicked.connect(self.add_page)
        row.addWidget(add)
        self._lockable.append(add)
        more = QPushButton("⋯")
        more.setObjectName("Ghost")
        more.setToolTip("Options de la page")
        more.clicked.connect(lambda: self._page_menu().exec(more.mapToGlobal(more.rect().bottomLeft())))
        row.addWidget(more)
        self._lockable.append(more)
        center.addLayout(row)

        self.grid = DeckGrid(self.engine)
        self.grid.selected.connect(self._select_cell)
        self.grid.pressed.connect(lambda k, down: self.engine.press(self.page_id, k, down))
        self.grid.moved.connect(lambda s, d, c: self.engine.move_button((self.page_id, s), (self.page_id, d), c))
        self.grid.inputs_dropped.connect(self._drop_inputs)
        self.grid.preset_dropped.connect(self._drop_preset)
        self.grid.context.connect(self._cell_menu)
        self.grid.test_key.connect(lambda k: self.engine.trigger(self.page_id, k))
        self.grid.blocked.connect(lambda: self.toast(HINT_LOCK, error=True))
        center.addWidget(self.grid, 1)
        # Molettes, bandeaux et boutons physiques des Loupedeck : hors grille, donc sous la vue.
        self.controls = ControlBar(self.engine)
        self.controls.selected.connect(self._select_control)
        center.addWidget(self.controls)
        self.hint = label(HINT, "Hint", True)
        self.hint.setAlignment(Qt.AlignCenter)
        self.hint.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.hint.installEventFilter(self)
        center.addWidget(self.hint)
        center.addWidget(self._monitor())
        return center

    def _monitor(self) -> QWidget:
        """Journal : filtre par niveau, copier, effacer, ouvrir le fichier journal."""
        self.monitor_box = QWidget()
        lay = QVBoxLayout(self.monitor_box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.mon_filter = tame_combo(QComboBox(), 14)
        self.mon_filter.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        for v, text in MONITOR_FILTERS:
            self.mon_filter.addItem(text, v)
        self.mon_filter.currentIndexChanged.connect(lambda _: self._render_monitor())
        row.addWidget(self.mon_filter)
        row.addStretch()
        for text, tip, fn in (("Copier", "Copier le journal affiché", self._copy_monitor),
                              ("Effacer", "Vider le journal", self._clear_monitor),
                              ("Fichier journal", "Ouvrir livedeck.log (détails techniques)", self._open_log)):
            b = QPushButton(text)
            b.setObjectName("Ghost")
            b.setToolTip(tip)
            b.clicked.connect(fn)
            row.addWidget(b)
        lay.addLayout(row)
        self.monitor = QPlainTextEdit()
        self.monitor.setObjectName("Monitor")
        self.monitor.setReadOnly(True)
        self.monitor.setMaximumBlockCount(400)
        self.monitor.setFixedHeight(150)
        lay.addWidget(self.monitor)
        self.monitor_box.hide()
        return self.monitor_box

    def _shortcuts(self):
        QShortcut(QKeySequence("Ctrl+L"), self, activated=self.toggle_lock)
        for seq, fn in ((QKeySequence.Undo, self.undo), (QKeySequence.Redo, self.redo), ("Ctrl+Y", self.redo),
                        (QKeySequence.Copy, self.copy), (QKeySequence.Paste, self.paste),
                        (QKeySequence.Cut, self.cut), ("Ctrl+D", self.duplicate_selected),
                        (QKeySequence.Delete, self.delete_selected), ("F5", lambda: self.set_live(not self.grid.live)),
                        (Qt.Key_Escape, self.clear_selection), (QKeySequence.Find, self.open_search),
                        ("Ctrl+PgDown", lambda: self._goto_page("next")), ("Ctrl+PgUp", lambda: self._goto_page("prev")),
                        ("F1", self.open_help), ("F2", self._edit_label)):
            # verrouillé : seuls l'aide et le changement de page restent actifs
            safe = seq in ("F1", "Ctrl+PgDown", "Ctrl+PgUp")
            QShortcut(QKeySequence(seq), self, activated=fn if safe else (lambda fn=fn: self.locked or fn()))

    def _edit_label(self):
        ed = getattr(self.inspector, "label_edit", None)
        try:
            if ed is not None and ed.isVisible():
                ed.setFocus()
                ed.selectAll()
        except RuntimeError:  # champ détruit par une reconstruction
            pass

    def eventFilter(self, obj, e):
        if obj is self.hint and e.type() == QEvent.MouseButtonPress and self._toast_timer.isActive():
            self.mon_btn.setChecked(True)  # clic sur un message : ouvrir le moniteur
            return True
        return super().eventFilter(obj, e)

    # -- événements moteur ----------------------------------------------------
    def _on_event(self, kind: str, payload):
        if kind == "state":
            if not self._live_timer.isActive():
                self._live_timer.start()
        elif kind == "config":
            if not self.engine.page(self.page_id):
                self.page_id = self.engine.pages[0]["id"]
                self.grid.set_page(self.page_id)
                self.controls.set_page(self.page_id)
            self._rebuild_tabs()
            self.grid.update()
            self.controls.refresh()
            self.inspector.on_config()
            self.library.presets.refresh()
        elif kind == "log":
            self._log(*payload)
        elif kind == "decks":
            self._update_pills()
            self._rebuild_tabs()
            self.grid.update()
            self.controls.refresh()
        elif kind == "page":
            self._goto_page(payload)
        elif kind == "activity":
            self._activity(*payload)

    @property
    def sel(self) -> str | None:
        """Case en cours d'édition : une touche de la grille, ou un contrôle hors grille
        (« c:knobTL »). Les deux sélections s'excluent, il n'y en a jamais qu'une."""
        return self.controls.sel or self.grid.sel

    def select(self, k: str | None) -> None:
        """Sélectionne une case quelle que soit sa nature (utilisé par la recherche)."""
        if k and parse_control(k) is not None:
            self._select_control(k)
        else:
            self.grid.select(k)

    def clear_selection(self) -> None:
        self.controls.select(None)
        self.grid.select(None)

    def _select_cell(self, k: str):
        """Sélection d'une touche de la grille : elle libère celle d'un contrôle Loupedeck."""
        self.controls.select(None)
        self.inspector.load(self.page_id, k or None)

    def _select_control(self, k: str, silent: bool = False):
        """Sélection d'un contrôle hors grille : la grille et l'inspecteur suivent.

        silent : sélection provoquée par l'appareil (« suivre le deck »), pas par un clic. Elle ne
        doit pas répéter l'avertissement du verrou à chaque appui."""
        if self.locked and not silent:
            self.toast(HINT_LOCK, error=True)
            return
        if self.grid.draws(k):
            # La vue dessine ce contrôle : la sélection vit dans la vue, qui l'entoure de blanc
            # comme une touche. Sans cela, rien ne montrerait ce qui est suivi ou édité.
            self.controls.select(None)
            self.grid.select(k, emit=False)
        else:  # page Stream Deck : le contrôle n'est qu'une pastille sous la vue
            self.grid.select(None, emit=False)
            self.controls.select(k)
        self.inspector.load(self.page_id, k)

    def _activity(self, page_id: str, k: str, deck: str | None):
        control = parse_control(k) is not None  # molette ou bouton hors grille : pas de case à éclairer
        if deck and self.follow.isChecked() and not self.grid.live:
            if page_id != self.page_id:
                self._goto_page(page_id)
            if control:
                if k != self.sel:
                    self._select_control(k, silent=True)
            elif k != self.grid.sel:
                self.controls.select(None)
                self.grid.select(k)
        if page_id == self.page_id and (not control or self.grid.draws(k)):
            self.grid.flash(k)

    def _refresh_live(self):
        self.grid.update()
        self.library.inputs.refresh()
        self.inspector.refresh_preview()
        self._update_pills()

    def _log(self, level: str, msg: str):
        mark = {"error": "✕", "warn": "!", "action": "▶"}.get(level, "·")
        line = f"{time.strftime('%H:%M:%S')}  {mark} {msg}"
        self._entries.append((level, line))
        del self._entries[:-400]
        if self._shown(level):
            self.monitor.appendPlainText(line)
        if level in ("error", "warn"):
            if not self.monitor_box.isVisible():
                self.unread[level] += 1
                self._update_badge()
            self.toast(msg, level=level)

    def _shown(self, level: str) -> bool:
        f = self.mon_filter.currentData()
        return f == "all" or (f == "problems" and level in ("error", "warn")) or (f == "action" and level == "action")

    def _render_monitor(self):
        self.monitor.setPlainText("\n".join(line for lv, line in self._entries if self._shown(lv)))
        self.monitor.verticalScrollBar().setValue(self.monitor.verticalScrollBar().maximum())

    def _clear_monitor(self):
        self._entries.clear()
        self.monitor.clear()

    def _copy_monitor(self):
        QApplication.clipboard().setText(self.monitor.toPlainText())
        self.toast("Journal copié")

    def _open_log(self):
        path = HOME / "livedeck.log"
        if hasattr(os, "startfile") and path.exists():
            os.startfile(path)

    def _toggle_monitor(self, on: bool):
        self.monitor_box.setVisible(on)
        if on:
            self.unread = {"error": 0, "warn": 0}
            self._update_badge()

    def _update_badge(self):
        """Badge « Moniteur ● 3 » : erreurs (rouge) ou alertes (orange) arrivées moniteur fermé."""
        n = self.unread["error"] + self.unread["warn"]
        self.mon_btn.setText(f"Moniteur  ● {n}" if n else "Moniteur")
        color = T.DANGER if self.unread["error"] else T.WARN
        self.mon_btn.setStyleSheet(f"QPushButton#Ghost{{color:{color}}}" if n else "")
        self.mon_btn.setToolTip(f"{self.unread['error']} erreur(s), {self.unread['warn']} alerte(s) non lue(s)  ·  "
                                "journal des actions envoyées et des erreurs" if n
                                else "Journal des actions envoyées et des erreurs")

    def toast(self, msg: str, error: bool = False, level: str = ""):
        level = level or ("error" if error else "info")
        self.hint.setObjectName("Toast")
        self.hint.setStyleSheet({"error": f"color:{T.DANGER}", "warn": f"color:{T.WARN}"}.get(level, ""))
        self.hint.setText(msg)
        self.hint.setToolTip("Clic : ouvrir le moniteur" if level in ("error", "warn") else "")
        self.hint.style().unpolish(self.hint)
        self.hint.style().polish(self.hint)
        self._toast_timer.start(TOAST_MS.get(level, 4000))

    def _end_toast(self):
        self.hint.setObjectName("Hint")
        self.hint.setStyleSheet("")
        self.hint.setToolTip("")
        self.hint.setText(typo(HINT_LOCK if self.locked else HINT_LIVE if self.grid.live else HINT))
        self.hint.style().unpolish(self.hint)
        self.hint.style().polish(self.hint)

    def _update_pills(self):
        if hasattr(self, "tray"):
            self.tray.sync()
        st, vm = self.engine.vmix.state, self.engine.vmix
        wide = self.width() >= 1500  # fenêtre étroite : pastilles réduites au nom, détail en info-bulle
        if st.connected:
            lat = vm.latency_ms
            detail = f"v{st.version}" + ("" if lat is None else f"  ·  {lat:.0f} ms" if lat >= 1 else "  ·  < 1 ms")
            self.vmix_pill.set(True, detail if wide else "")
            self.vmix_pill.setToolTip(f"vMix {detail}  ·  {st.inputs and len(st.inputs)} inputs  ·  temps aller-retour "
                                      "moyen d'une commande\nClic : Réglages")
        else:
            detail = f"déconnecté  ·  {self.engine.cfg['vmix']['host']}"
            self.vmix_pill.set(False, detail if wide else "")
            self.vmix_pill.setToolTip(f"vMix {detail}\nLiveDeck réessaie toutes les 2 secondes  ·  Clic : Réglages")
        dm = self.engine.decks
        info = dm.info() if dm else []
        if dm is None:
            detail, ok, tip = "désactivé", None, "Stream Decks désactivés"
        elif dm.error:
            detail, ok, tip = "indisponible", False, dm.error
        else:
            detail = f"{len(info)} connecté{'s' if len(info) > 1 else ''}" if info else "aucun détecté"
            ok, tip = bool(info), ("\n".join(f"{d['model']} · {d['serial']}" for d in info)
                                   or "Ferme Companion et le logiciel Elgato pour libérer les decks")
        self.deck_pill.set(ok, detail if wide else ("" if ok else detail))
        self.deck_pill.setToolTip(f"Stream Deck : {detail}\n{tip}\nClic : diagnostic des Stream Decks")
        # intégrations utilisées par une touche : une pastille chacune, ou une seule si la fenêtre est étroite
        e = self.engine
        services = {"intercom": e.intercom, "orchestream": e.orchestream, "tallies": e.tallies}
        used = [(k, name, ok, text) for k, name, ok, text in integration_status(e) if services[k].wanted]
        for k, pill in self.integ_pills.items():
            row = next((u for u in used if u[0] == k), None)
            pill.setVisible(bool(row) and wide)
            if row:
                pill.set(row[2], "")
                pill.setToolTip(f"{row[1]} : {row[3]}\nClic : Réglages → Intégrations")
        oks = [u[2] for u in used]
        self.integ_all.setVisible(bool(used) and not wide)
        self.integ_all.set(False if False in oks else None if None in oks else True, "")
        self.integ_all.setToolTip("\n".join(f"{name} : {text}" for _, name, _, text in used)
                                  + "\nClic : Réglages → Intégrations")
        self.library.presets.refresh()  # bus et overlays découverts en direct

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, "integ_all"):
            self._update_pills()
    # -- pages ----------------------------------------------------------------
    def _rebuild_tabs(self):
        """Onglets des pages. Avec plusieurs familles de surfaces, « Régie » seul ne suffit plus :
        chaque onglet dit sur quel appareil la page est affichée, et de quelle surface elle est."""
        decks = self.engine.decks.info() if self.engine.decks else []
        on_deck = {}
        for d in decks:
            on_deck.setdefault(d["page"], []).append(d["model"])
        self.tabs.blockSignals(True)
        while self.tabs.count():
            self.tabs.removeTab(0)
        for p in self.engine.pages:
            models = on_deck.get(p["id"], [])
            loupe = (p.get("surface") or (surface_of(models[0]) if models else "")) == "loupedeck"
            i = self.tabs.addTab(("●  " if models else "") + p["name"]
                                 + ("   ⟳" if loupe else ""))  # ⟳ = surface à molettes
            self.tabs.setTabData(i, p["id"])
            kind = "Loupedeck" if loupe else "Stream Deck"
            self.tabs.setTabToolTip(
                i, f"{p['name']} — {kind}, grille {p['cols']}×{p['rows']}"
                + (f"\nAffichée sur : {', '.join(sorted(set(models)))}" if models
                   else "\nAffichée sur aucun appareil")
                + "\nClic droit : renommer, taille, surface, dupliquer, supprimer")
            if p["id"] == self.page_id:
                self.tabs.setCurrentIndex(i)
        self.tabs.blockSignals(False)

    def _tab_changed(self, i: int):
        pid = self.tabs.tabData(i)
        if pid and pid != self.page_id:
            self.page_id = pid
            self.grid.set_page(pid)
            self.controls.set_page(pid)
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
        size = m.addMenu("Modèle d'appareil")
        for name, (c, r) in DECK_SIZES.items():
            size.addAction(name, lambda c=c, r=r, n=name: self._set_page_model(c, r, surface_of(n)))
        surf = m.addMenu("Surface affichée")
        cur = self.engine.page(self.page_id).get("surface") or ""
        for value, text in (("", "Automatique (selon l'appareil)"), ("streamdeck", "Stream Deck"),
                            ("loupedeck", "Loupedeck (molettes, bandeaux, molette centrale)")):
            a = surf.addAction(text, lambda v=value: self.engine.update_page(self.page_id, surface=v))
            a.setCheckable(True)
            a.setChecked(cur == value)
        m.addAction("Dupliquer la page", self.duplicate_page)
        takes = [k for k, b in self.engine.page(self.page_id)["buttons"].items() if to_take(b)]
        if takes:
            m.addAction(f"Convertir les Preview / Program en Take ({len(takes)})", lambda: self.convert_to_take(takes))
        m.addAction("Exporter la page…", self.export_page)
        m.addAction("Importer une page…", self.import_page)
        m.addAction("Déplacer à gauche", lambda: self.engine.move_page(self.page_id, -1))
        m.addAction("Déplacer à droite", lambda: self.engine.move_page(self.page_id, 1))
        m.addSeparator()
        rm = m.addAction("Supprimer la page", self.delete_page)
        rm.setEnabled(len(self.engine.pages) > 1)
        return m

    def _set_page_model(self, cols: int, rows: int, surface: str) -> None:
        """Changer de modèle change aussi la surface dessinée : passer une page en Loupedeck CT
        sans voir apparaître ses molettes n'aurait aucun sens."""
        self.engine.update_page(self.page_id, cols=cols, rows=rows, surface=surface)

    def add_page(self):
        names = list(DECK_SIZES)
        info = self.engine.decks.info() if self.engine.decks else []
        # Proposer d'emblée le modèle d'un appareil branché, Loupedeck compris.
        default = next((i for i, n in enumerate(names)
                        for d in info if DECK_SIZES[n] == (d["cols"], d["rows"])
                        and surface_of(n) == surface_of(d["model"])), 0)
        size, ok = QInputDialog.getItem(self, "Nouvelle page", "Modèle d'appareil :", names, default, False)
        if not ok:
            return
        name, ok = QInputDialog.getText(self, "Nouvelle page", "Nom de la page :", text=f"Page {len(self.engine.pages) + 1}")
        if ok and name.strip():
            self.page_id = self.engine.add_page(name.strip(), *DECK_SIZES[size], surface=surface_of(size))
            self._rebuild_tabs()
            self.grid.set_page(self.page_id)
            self.controls.set_page(self.page_id)

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
        if self.locked:
            self.mode.set("live" if self.grid.live else "edit")
            return
        self.grid.set_live(live)
        self.mode.set("live" if live else "edit")
        self.inspector.set_locked(live)
        if hasattr(self, "tray"):
            self.tray.sync()
        if not self._toast_timer.isActive():
            self._end_toast()

    # -- verrou ---------------------------------------------------------------
    def _saved_lock(self) -> bool:
        try:
            return str(self._ui.value("locked", "false")).lower() == "true"
        except Exception:
            return False

    def toggle_lock(self) -> None:
        """Verrouiller est immédiat ; déverrouiller demande confirmation (un clic perdu ne doit pas rouvrir l'édition)."""
        if self.locked and QMessageBox.question(
                self, "Déverrouiller", "Déverrouiller l'édition et les clics sur les touches ?") != QMessageBox.Yes:
            self.lock_btn.setChecked(True)
            return
        self.set_locked(not self.locked)

    def set_locked(self, locked: bool) -> None:
        """Pendant un direct : plus d'édition (bibliothèque, éditeur, pages, import, réglages) ni de clic sur les
        touches. Les Stream Decks physiques et le moteur ne sont pas touchés."""
        self.locked = locked
        self.grid.set_locked(locked)
        for w in self._lockable:
            w.setEnabled(not locked)
        self.lock_btn.setChecked(locked)
        self.lock_btn.setText("Verrouillé" if locked else "Verrouiller")
        self.setWindowTitle(f"LiveDeck {__version__}" + ("  ·  VERROUILLÉ" if locked else ""))
        try:
            self._ui.setValue("locked", "true" if locked else "false")
            self._ui.sync()
        except Exception:
            pass
        self.tray.sync()
        self.toast("Verrouillé : plus d'édition ni de clic sur les touches" if locked else "Déverrouillé")

    def _editable(self) -> bool:
        if self.locked:
            self.toast(HINT_LOCK, error=True)
            return False
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
            b = presets.place(b, k)  # molette : garde la rotation ; touche : n'en garde que l'appui
            self.engine.set_button(self.page_id, k, b)
            self.select(k)
            self.inspector.load(self.page_id, k, force=True)

    def _drop_inputs(self, k: str, titles: list[str]):
        self.fill_row(k, titles, self.library.inputs.drop_mode())

    def fill_row(self, k: str, titles: list[str], mode: str, keep: bool = True) -> None:
        """Pose les inputs de gauche à droite à partir de k, dans le style choisi (une seule étape « annuler »).
        keep : un seul input glissé sur une touche vMix existante ne change que son input."""
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

        if keep and len(titles) == 1 and mode != "pair":
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
                m.addAction("▶  Tester", lambda: self.engine.trigger(self.page_id, k))
                m.exec(pos)
            return
        if b:
            m.addAction("▶  Tester", lambda: self.engine.trigger(self.page_id, k))
            m.addSeparator()
            m.addAction("Copier", self.copy)
            m.addAction("Couper", self.cut)
        paste = m.addAction("Coller", self.paste)
        paste.setEnabled(self.clipboard is not None)
        m.addSeparator()
        if b and to_take(b):
            m.addAction("Convertir en Take", lambda: self.convert_to_take([k]))
        row = self._row_takes(k)
        if len(row) > 1:
            m.addAction(f"Convertir la rangée en Take ({len(row)} touches)", lambda: self.convert_to_take(row))
        m.addAction("Remplir la rangée à partir d'ici…", lambda: self.open_fill_row(k))
        if b:
            m.addSeparator()
            m.addAction("Dupliquer", self.duplicate_selected)
            send = m.addMenu("Envoyer vers la page")
            for p in self.engine.pages:
                if p["id"] != self.page_id:
                    act = send.addAction(p["name"], lambda pid=p["id"]: self._send_to(k, pid))
                    act.setEnabled(self.engine.first_free(p["id"]) is not None)
            m.addSeparator()
            m.addAction("Effacer", self.delete_selected)
        m.exec(pos)

    def _row_takes(self, k: str) -> list[str]:
        """Touches Preview / Program de la rangée de k, convertibles en Take."""
        r, _ = parse_key(k)
        return [kk for kk, b in self.engine.page(self.page_id)["buttons"].items()
                if parse_key(kk)[0] == r and to_take(b)]

    def convert_to_take(self, keys: list[str]) -> None:
        if not self._editable():
            return
        pid = self.page_id

        def apply(cfg):
            p = next(p for p in cfg["pages"] if p["id"] == pid)
            for kk in keys:
                nb = to_take(p["buttons"].get(kk) or {})
                if nb:
                    p["buttons"][kk] = nb
        self.engine.mutate(apply)
        self.inspector.load(self.page_id, self.grid.sel, force=True)
        self.toast(f"{len(keys)} touche(s) converties en Take : 1er appui preview, 2e appui antenne  ·  "
                   "Ctrl+Z pour annuler")

    def open_fill_row(self, k: str | None, titles: list[str] | None = None) -> None:
        if not self._editable():
            return
        k = k or self.engine.first_free(self.page_id) or "0,0"
        r, c = parse_key(k)
        dlg = FillRowDialog(self.engine, self, titles or self.library.inputs.selected_titles(),
                            self.library.inputs.drop_mode(),
                            f"À partir de la rangée {r + 1}, colonne {c + 1} de « {self.engine.page(self.page_id)['name']} ».")
        if dlg.exec() and dlg.titles():
            self.fill_row(k, dlg.titles(), dlg.style.currentData(), keep=False)

    def _send_to(self, k: str, pid: str):
        dst = self.engine.send_to_page(self.page_id, k, pid)
        if dst:
            r, c = parse_key(dst)
            self.toast(f"Touche envoyée sur « {self.engine.page(pid)['name']} », rangée {r + 1} colonne {c + 1}")

    def _selected(self):
        return self.engine.button(self.page_id, self.sel) if self.sel else None

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
        k = self.sel
        if self.clipboard and k:
            b = {**copy.deepcopy(self.clipboard), "id": new_id()}
            cid = parse_control(k)
            if cid is not None:  # coller sur une molette lui garde ses deux sens de rotation
                b["kind"] = control_kind(cid)
            self.engine.set_button(self.page_id, k, b)
            self.inspector.load(self.page_id, k, force=True)

    def delete_selected(self):
        if not self._editable():
            return
        if self._selected():
            k = self.sel
            self.engine.set_button(self.page_id, k, None)
            self.toast(("Contrôle effacé" if parse_control(k) is not None else "Touche effacée")
                       + "  ·  Ctrl+Z pour annuler")

    def duplicate_selected(self):
        if not self._editable():
            return
        b = self._selected()
        if b and parse_control(self.sel) is not None:
            # Un contrôle est lié à un organe physique : le dupliquer n'a de sens que vers un autre,
            # ce que l'utilisateur fait en collant. On le dit au lieu de ne rien faire.
            self.toast("Un contrôle ne se duplique pas : copie-le (Ctrl+C) puis colle-le sur un autre.",
                       error=True)
            return
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
            self.inspector.load(self.page_id, self.sel, force=True)
            self.toast("Annulé")

    def redo(self):
        if not self._editable():
            return
        if self.engine.redo():
            self.inspector.load(self.page_id, self.sel, force=True)
            self.toast("Rétabli")

    # -- fichiers -------------------------------------------------------------
    def import_companion(self):
        path, _ = QFileDialog.getOpenFileName(self, "Config Companion", "",
                                              "Companion (*.companionconfig *.json);;Tous (*.*)")
        if not path:
            return
        dlg = ImportOptionsDialog(self, path)
        if dlg.exec():
            self.import_companion_file(path, **dlg.options())

    def import_companion_file(self, path: str, preview_then: str = "none", integrations: bool = False):
        try:
            cfg, report = import_companion(path, preview_then=preview_then, integrations=integrations)
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
            self.engine.backup("avant-import")
            merged = []

            def apply(c):
                c["pages"].extend(cfg["pages"])
                c["decks"].update(cfg["decks"])
                merged[:] = merge_integrations(c, cfg)  # réglages trouvés par la conversion (sinon touches inertes)
            self.engine.mutate(apply)
            if merged:
                report.append("Intégrations reprises : " + " ; ".join(merged))
            if self.engine.decks:
                self.engine.decks.reassign_pages()
        else:
            return
        self.page_id = self.engine.pages[0]["id"]
        self._rebuild_tabs()
        self.grid.set_page(self.page_id)
        self.controls.set_page(self.page_id)
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

    def export_page(self):
        p = self.engine.page(self.page_id)
        path, _ = QFileDialog.getSaveFileName(self, "Exporter la page", f"{p['name']}.json", "JSON (*.json)")
        if path:
            self.write_page(path, self.page_id)
            self.toast(f"Page « {p['name']} » exportée : {path}")

    def write_page(self, path: str, page_id: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"livedeck_page": CONFIG_VERSION, "page": self.engine.page(page_id)}, f, ensure_ascii=False,
                      indent=2)

    def import_page(self):
        path, _ = QFileDialog.getOpenFileName(self, "Importer une page", "", "JSON (*.json)")
        if path:
            self.read_page(path)

    def read_page(self, path: str) -> str | None:
        """Ajoute une page exportée (nouveaux identifiants : on peut importer deux fois la même page)."""
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            p = data.get("page", data) if isinstance(data, dict) else None
            if not isinstance(p, dict) or not isinstance(p.get("buttons"), dict):
                raise ValueError("ce fichier ne contient pas de page LiveDeck")
            p = copy.deepcopy(p)
            p["id"] = new_id()
            p["name"] = str(p.get("name") or "Page importée")
            if p["name"] in {q["name"] for q in self.engine.pages}:
                p["name"] += " (importée)"
            p["cols"], p["rows"] = int(p.get("cols") or 8), int(p.get("rows") or 4)
            p["buttons"] = {k: normalize_button({**b, "id": new_id()}) for k, b in p["buttons"].items()
                            if isinstance(b, dict) and self.engine.in_grid(p, k)}
        except (OSError, ValueError, TypeError) as e:
            QMessageBox.warning(self, "Import impossible", f"Ce fichier n'a pas pu être lu :\n{e}")
            return None
        self.engine.mutate(lambda cfg: cfg["pages"].append(p))
        self._goto_page(p["id"])
        self.toast(f"Page « {p['name']} » ajoutée ({len(p['buttons'])} touches)  ·  Ctrl+Z pour annuler")
        return p["id"]

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
        self.controls.set_page(self.page_id)

    def open_settings(self, tab: str = "general"):
        if self.locked:
            self.toast(HINT_LOCK, error=True)
            return
        SettingsDialog(self.engine, self, tab if isinstance(tab, str) else "general").exec()
        self._update_pills()

    def open_help(self):
        ShortcutsDialog(self).exec()

    def _show_migration_notes(self):
        """Notes laissées par la mise à jour d'une ancienne configuration : affichées une fois (Moniteur)."""
        notes = self.engine.cfg.pop("_migration_notes", None)
        if notes:
            for line in notes:
                self.engine.log("info", f"Mise à jour de la configuration : {line}")
            self.toast(f"Configuration mise à jour ({len(notes)} changement(s)) : détails dans le Moniteur")

    def _save_ui(self):
        try:
            self._ui.setValue("splitter", self.split.saveState())
            self._ui.sync()
        except Exception:
            pass

    def open_diagnostic(self):
        DiagnosticDialog(self.engine, self).exec()

    def open_search(self):
        dlg = SearchDialog(self.engine, self)
        if dlg.exec() and dlg.chosen:
            page_id, k = dlg.chosen
            self._goto_page(page_id)
            self.select(k)
            if parse_control(k) is None:
                self.grid.flash(k)  # un contrôle hors grille n'a pas de case à faire clignoter

    # -- fenêtre / barre des tâches -------------------------------------------
    def bring_to_front(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def quit_app(self):
        self._quitting = True
        self.close()

    def closeEvent(self, e):
        self._save_ui()
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
        try:
            self.engine.listeners.remove(self._listener)
            self.engine.stop()  # relâche micros et raccourcis maintenus, puis sauvegarde
        finally:
            self.tray.hide()
            super().closeEvent(e)
            QApplication.quit()
