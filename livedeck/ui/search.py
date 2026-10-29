"""Recherche de touche (Ctrl+F) sur toutes les pages : texte, input, action, page."""
from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter
from PySide6.QtWidgets import (QDialog, QFrame, QLineEdit, QListWidget, QListWidgetItem, QStyle,
                               QStyledItemDelegate, QVBoxLayout)

from ..actions import summary
from ..model import parse_key
from ..render import render
from ..vmix import norm
from . import theme as T
from .deck_view import pil_to_pixmap
from .widgets import label

ROLE = Qt.UserRole


def haystack(engine, page: dict, b: dict) -> str:
    names = engine.page_names()
    acts = b.get("actions", []) + b.get("actions_off", []) + b.get("long_actions", [])
    parts = [b.get("label", ""), page["name"]] + [summary(a, names) for a in acts]
    parts += [a.get("input", "") for a in acts] + [f.get("cond", {}).get("input", "") for f in b.get("feedbacks", [])]
    if b.get("mode") == "toggle":
        parts.append("interrupteur")
    if b.get("long_actions"):
        parts.append("appui long")
    if b.get("confirm"):
        parts.append("sécurité securite")
    return " ".join(norm(p) + " " + str(p).lower() for p in parts if p)


class _Delegate(QStyledItemDelegate):
    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine

    def sizeHint(self, option, index):
        return QSize(400, 52)

    def paint(self, p: QPainter, option, index):
        page_id, k = index.data(ROLE)
        b = self.engine.button(page_id, k)
        if b is None:
            return
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(option.rect).adjusted(2, 2, -2, -2)
        if option.state & (QStyle.State_Selected | QStyle.State_MouseOver):
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(T.RAISED if option.state & QStyle.State_Selected else T.PANEL2))
            p.drawRoundedRect(r, 9, 9)
        dpr = p.device().devicePixelRatioF()
        pm = pil_to_pixmap(render(self.engine.visual(b), int(38 * dpr)), dpr)
        p.drawPixmap(int(r.x() + 8), int(r.center().y() - 19), pm)
        x = r.x() + 58
        w = r.right() - x - 8
        f = QFont(option.font)
        f.setPointSizeF(9.5)
        f.setWeight(QFont.DemiBold)
        p.setFont(f)
        p.setPen(QColor(T.TEXT))
        title = (b.get("label") or "").replace("\n", " ") or "Sans texte"
        p.drawText(QRectF(x, r.y() + 6, w, 20), Qt.AlignLeft | Qt.AlignVCenter,
                   QFontMetricsF(f).elidedText(title, Qt.ElideRight, w))
        f2 = QFont(option.font)
        f2.setPointSizeF(7.8)
        p.setFont(f2)
        p.setPen(QColor(T.MUTED))
        page = self.engine.page(page_id)
        row, col = parse_key(k)
        first = summary(b["actions"][0], self.engine.page_names()) if b.get("actions") else "Aucune action"
        sub = f"{page['name']}  ·  rangée {row + 1}, colonne {col + 1}  ·  {first}"
        p.drawText(QRectF(x, r.y() + 26, w, 18), Qt.AlignLeft | Qt.AlignVCenter,
                   QFontMetricsF(f2).elidedText(sub, Qt.ElideRight, w))
        p.restore()


class SearchDialog(QDialog):
    def __init__(self, engine, parent=None):
        super().__init__(parent, Qt.Dialog | Qt.FramelessWindowHint)
        self.engine = engine
        self.chosen: tuple[str, str] | None = None
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.resize(620, 480)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        box = QFrame()
        box.setObjectName("Side")
        outer.addWidget(box)
        lay = QVBoxLayout(box)
        lay.setContentsMargins(16, 16, 16, 12)
        lay.setSpacing(10)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("Rechercher une touche : texte, input, action, page…")
        self.edit.setClearButtonEnabled(True)
        self.edit.textChanged.connect(self._filter)
        self.edit.installEventFilter(self)
        lay.addWidget(self.edit)
        self.list = QListWidget()
        self.list.setItemDelegate(_Delegate(engine, self.list))
        self.list.setMouseTracking(True)
        self.list.itemActivated.connect(self._choose)
        self.list.itemClicked.connect(self._choose)
        lay.addWidget(self.list, 1)
        self.count = label("", "Hint")
        lay.addWidget(self.count)
        self._items = []
        for page in engine.pages:
            for k, b in sorted(page["buttons"].items(), key=lambda kv: parse_key(kv[0])):
                it = QListWidgetItem()
                it.setData(ROLE, (page["id"], k))
                self._items.append((it, haystack(engine, page, b), (b.get("label") or "").lower()))
                self.list.addItem(it)
        self._filter("")

    def _filter(self, text: str):
        words = [w for w in text.lower().split() if w]
        shown = 0
        for it, hay, _ in self._items:
            ok = all(w in hay or norm(w) in hay for w in words)
            it.setHidden(not ok)
            shown += ok
        first = next((it for it, *_ in self._items if not it.isHidden()), None)
        self.list.setCurrentItem(first)
        total = len(self._items)
        self.count.setText(f"{shown} touche{'s' if shown > 1 else ''} sur {total}  ·  Entrée : y aller  ·  Échap : fermer"
                           if words else f"{total} touches  ·  ↑ ↓ pour choisir, Entrée pour y aller")

    def eventFilter(self, obj, e):
        if obj is self.edit and e.type() == e.Type.KeyPress:
            if e.key() in (Qt.Key_Down, Qt.Key_Up):
                visible = [it for it, *_ in self._items if not it.isHidden()]
                if visible:
                    cur = self.list.currentItem()
                    i = visible.index(cur) if cur in visible else -1
                    i = max(0, min(len(visible) - 1, i + (1 if e.key() == Qt.Key_Down else -1)))
                    self.list.setCurrentItem(visible[i])
                return True
            if e.key() in (Qt.Key_Return, Qt.Key_Enter):
                if self.list.currentItem() and not self.list.currentItem().isHidden():
                    self._choose(self.list.currentItem())
                return True
        return super().eventFilter(obj, e)

    def _choose(self, it):
        self.chosen = it.data(ROLE)
        self.accept()

    def showEvent(self, e):
        super().showEvent(e)
        parent = self.parentWidget()
        if parent:
            geo = parent.geometry()
            self.move(geo.x() + (geo.width() - self.width()) // 2, geo.y() + 90)
        self.edit.setFocus()
