"""Panneau de gauche : inputs vMix en direct et bibliothèque de boutons, à glisser sur le deck."""
from __future__ import annotations

import json

from PySide6.QtCore import QMimeData, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFrame, QLineEdit, QListWidget, QListWidgetItem,
                               QMenu, QStackedWidget, QStyle, QStyledItemDelegate, QVBoxLayout, QWidget)

from .. import presets
from ..actions import CATEGORIES
from . import theme as T
from .deck_view import MIME_INPUTS, MIME_PRESET
from .dialogs import ROW_STYLES
from .widgets import Seg, label, tame_combo, widen_popup

ROLE = Qt.UserRole
CREATE_MODES = ROW_STYLES


def _row_bg(p: QPainter, option, r: QRectF):
    if option.state & (QStyle.State_MouseOver | QStyle.State_Selected):
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(T.RAISED if option.state & QStyle.State_Selected else T.PANEL2))
        p.drawRoundedRect(r, 9, 9)


def _two_lines(p: QPainter, option, x: float, r: QRectF, title: str, sub: str, right: float = 8):
    w = r.right() - x - right
    f = QFont(option.font)
    f.setPointSizeF(9.5)
    f.setWeight(QFont.DemiBold)
    p.setFont(f)
    p.setPen(QColor(T.TEXT))
    p.drawText(QRectF(x, r.y() + 4, w, 20), Qt.AlignLeft | Qt.AlignVCenter,
               QFontMetricsF(f).elidedText(title, Qt.ElideRight, w))
    f2 = QFont(option.font)
    f2.setPointSizeF(7.5)
    p.setFont(f2)
    p.setPen(QColor(T.FAINT))
    p.drawText(QRectF(x, r.y() + 22, w, 16), Qt.AlignLeft | Qt.AlignVCenter,
               QFontMetricsF(f2).elidedText(sub, Qt.ElideRight, w))


# --- inputs ---------------------------------------------------------------------------


class _InputDelegate(QStyledItemDelegate):
    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine

    def sizeHint(self, option, index):
        return QSize(200, 46)

    def paint(self, p: QPainter, option, index):
        st = self.engine.vmix.state
        inp = st.resolve(str(index.data(ROLE + 1)))
        if inp is None:
            return
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(option.rect).adjusted(2, 2, -2, -2)
        _row_bg(p, option, r)
        n = inp["number"]
        tally = T.PGM if n == st.program() else T.PVW if n == st.preview() else None
        chip = QRectF(r.x() + 6, r.center().y() - 11, 28, 22)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(tally) if tally else QColor(T.RAISED))
        p.drawRoundedRect(chip, 6, 6)
        f = QFont(option.font)
        f.setPointSizeF(8.5)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor("#0b0d10") if tally else QColor(T.MUTED))
        p.drawText(chip, Qt.AlignCenter, str(n))
        overlays = [o for o, s in st.overlays.items() if s.get("program") == n]
        sub = inp["type"] + ("  ·  muet" if inp.get("muted") else "")
        right = 8
        if overlays:  # badge « OV2 » : l'input est affiché en overlay
            badge = QRectF(r.right() - 40, r.center().y() - 9, 34, 18)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(T.WARN))
            p.drawRoundedRect(badge, 5, 5)
            f.setPointSizeF(7)
            p.setFont(f)
            p.setPen(QColor("#0b0d10"))
            p.drawText(badge, Qt.AlignCenter, f"OV{overlays[0]}")
            right = 46
        _two_lines(p, option, chip.right() + 10, r, inp["title"], sub, right)
        p.restore()


class _InputList(QListWidget):
    def mimeData(self, items):
        md = QMimeData()
        md.setData(MIME_INPUTS, json.dumps([i.data(ROLE) for i in items]).encode())
        return md

    def mimeTypes(self):
        return [MIME_INPUTS]


class InputsPage(QWidget):
    activated = Signal(str)
    fill_requested = Signal(list)  # « Remplir la rangée » avec les inputs sélectionnés

    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        self._sig = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self.mode = tame_combo(QComboBox(), 12)
        for v, text in CREATE_MODES:
            self.mode.addItem(f"Créer : {text}", v)
            self.mode.setItemData(self.mode.count() - 1, text, Qt.ToolTipRole)
        widen_popup(self.mode)
        self.mode.setToolTip("Type de touche créée en glissant ou en double-cliquant un input")
        lay.addWidget(self.mode)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Rechercher…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        lay.addWidget(self.search)
        self.list = _InputList()
        self.list.setItemDelegate(_InputDelegate(engine, self.list))
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.setDragEnabled(True)
        self.list.setDragDropMode(QAbstractItemView.DragOnly)
        self.list.setMouseTracking(True)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.list.itemDoubleClicked.connect(lambda it: self.activated.emit(it.data(ROLE)))
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        lay.addWidget(self.list, 1)
        self.placeholder = label("vMix n'est pas connecté.\nVérifie l'adresse dans les réglages\n"
                                 "(API TCP, port 8099).", "Hint", True)
        self.placeholder.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.placeholder, 1)
        lay.addWidget(label("Glisse un ou plusieurs inputs sur le deck. Double-clic : l'assigner à la touche "
                            "sélectionnée, puis passer à la suivante. Clic droit : remplir une rangée.", "Hint", True))

    def selected_titles(self) -> list[str]:
        """Inputs sélectionnés, dans l'ordre de vMix."""
        return [self.list.item(r).data(ROLE) for r in range(self.list.count()) if self.list.item(r).isSelected()]

    def _menu(self, pos):
        titles = self.selected_titles()
        m = QMenu(self)
        act = m.addAction(f"Remplir la rangée avec {len(titles)} input{'s' if len(titles) > 1 else ''}…"
                          if titles else "Remplir la rangée…", lambda: self.fill_requested.emit(titles))
        act.setToolTip("À partir de la touche sélectionnée sur le deck")
        m.exec(self.list.viewport().mapToGlobal(pos))

    def drop_mode(self) -> str:
        return self.mode.currentData()

    def refresh(self) -> None:
        st = self.engine.vmix.state
        online = st.connected and bool(st.inputs)
        self.list.setVisible(online)
        self.placeholder.setVisible(not online)
        self.placeholder.setText("vMix n'est pas connecté.\nVérifie l'adresse dans les réglages\n(API TCP, port 8099)."
                                 if not st.connected else "Aucun input dans vMix.")
        sig = tuple((i["number"], i["title"]) for i in st.inputs)
        if sig == self._sig:
            self.list.viewport().update()
            return
        self._sig = sig
        self.list.clear()
        for i in st.inputs:
            it = QListWidgetItem()
            it.setData(ROLE, i["title"])
            it.setData(ROLE + 1, i["number"])
            it.setToolTip(f"{i['number']} · {i['title']}\n{i['type']}")
            self.list.addItem(it)
        self._filter(self.search.text())

    def _filter(self, text: str) -> None:
        t = text.strip().lower()
        for row in range(self.list.count()):
            it = self.list.item(row)
            it.setHidden(bool(t) and t not in str(it.data(ROLE)).lower())


# --- bibliothèque de boutons ------------------------------------------------------------


class _PresetDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return QSize(200, 30 if index.data(ROLE + 2) == "head" else 44)

    def paint(self, p: QPainter, option, index):
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(option.rect).adjusted(2, 2, -2, -2)
        color = QColor(index.data(ROLE + 3) or T.MUTED)
        if index.data(ROLE + 2) == "head":
            f = QFont(option.font)
            f.setPointSizeF(7.6)
            f.setBold(True)
            f.setLetterSpacing(QFont.AbsoluteSpacing, 1.2)
            p.setFont(f)
            p.setPen(color)
            p.drawText(r.adjusted(6, 6, 0, 0), Qt.AlignLeft | Qt.AlignVCenter, index.data(ROLE + 4).upper())
        else:
            _row_bg(p, option, r)
            sw = QRectF(r.x() + 8, r.center().y() - 11, 22, 22)
            p.setPen(Qt.NoPen)
            c = QColor(color)
            c.setAlphaF(0.18)
            p.setBrush(c)
            p.drawRoundedRect(sw, 6, 6)
            p.setBrush(color)
            p.drawEllipse(sw.center(), 4, 4)
            _two_lines(p, option, sw.right() + 10, r, index.data(ROLE + 4), index.data(ROLE + 5))
        p.restore()


class _PresetList(QListWidget):
    def mimeData(self, items):
        md = QMimeData()
        if items and items[0].data(ROLE):
            md.setData(MIME_PRESET, items[0].data(ROLE).encode())
        return md

    def mimeTypes(self):
        return [MIME_PRESET]


class PresetsPage(QWidget):
    activated = Signal(str)

    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        self._sig = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Rechercher : intercom, http, page…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        lay.addWidget(self.search)
        self.list = _PresetList()
        self.list.setItemDelegate(_PresetDelegate(self.list))
        self.list.setDragEnabled(True)
        self.list.setDragDropMode(QAbstractItemView.DragOnly)
        self.list.setMouseTracking(True)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.list.itemDoubleClicked.connect(lambda it: it.data(ROLE) and self.activated.emit(it.data(ROLE)))
        lay.addWidget(self.list, 1)
        lay.addWidget(label("Glisse un bouton sur une touche du deck. Double-clic : le poser sur la touche "
                            "sélectionnée.", "Hint", True))
        self.refresh()

    def refresh(self) -> None:
        groups = presets.library(self.engine)
        sig = json.dumps(groups)
        if sig == self._sig:
            return
        self._sig = sig
        self.list.clear()
        for cat, items in groups:
            color = CATEGORIES.get(cat, T.MUTED)
            head = QListWidgetItem()
            head.setFlags(Qt.NoItemFlags)
            for role, v in ((ROLE + 2, "head"), (ROLE + 3, color), (ROLE + 4, cat)):
                head.setData(role, v)
            self.list.addItem(head)
            for pid, name, desc in items:
                it = QListWidgetItem()
                for role, v in ((ROLE, pid), (ROLE + 3, color), (ROLE + 4, name), (ROLE + 5, desc)):
                    it.setData(role, v)
                it.setToolTip(desc)
                self.list.addItem(it)
        self._filter(self.search.text())

    def _filter(self, text: str) -> None:
        """Filtre sur nom, description et catégorie ; un en-tête disparaît si plus rien ne le suit."""
        words = [w for w in text.lower().split() if w]
        head, shown = None, 0
        for row in range(self.list.count()):
            it = self.list.item(row)
            if it.data(ROLE + 2) == "head":
                if head is not None:
                    head.setHidden(shown == 0)
                head, shown = it, 0
                continue
            hay = f"{head.data(ROLE + 4) if head else ''} {it.data(ROLE)} {it.data(ROLE + 4)} {it.data(ROLE + 5)}".lower()
            ok = all(w in hay for w in words)
            it.setHidden(not ok)
            shown += ok
        if head is not None:
            head.setHidden(shown == 0)


class LibraryPanel(QFrame):
    input_activated = Signal(str)
    preset_activated = Signal(str)

    def __init__(self, engine):
        super().__init__()
        self.setObjectName("Side")
        self.setMinimumWidth(250)
        self.setMaximumWidth(520)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(12)
        self.tabs = Seg([("inputs", "Inputs vMix"), ("presets", "Boutons")], "inputs")
        lay.addWidget(self.tabs)
        self.stack = QStackedWidget()
        self.inputs = InputsPage(engine)
        self.presets = PresetsPage(engine)
        self.stack.addWidget(self.inputs)
        self.stack.addWidget(self.presets)
        lay.addWidget(self.stack, 1)
        self.tabs.changed.connect(lambda v: self.stack.setCurrentIndex(0 if v == "inputs" else 1))
        self.inputs.activated.connect(self.input_activated)
        self.presets.activated.connect(self.preset_activated)
