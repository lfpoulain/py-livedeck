"""Panneau de gauche : inputs vMix en direct et bibliothèque de boutons, à glisser sur le deck."""
from __future__ import annotations

import json

from PySide6.QtCore import QMimeData, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFrame, QLineEdit, QListWidget, QListWidgetItem,
                               QMenu, QSizePolicy, QStackedWidget, QStyle, QStyledItemDelegate, QVBoxLayout, QWidget)

from .. import presets
from . import theme as T
from .deck_view import MIME_INPUTS, MIME_PRESET, preset_thumb
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
            self.mode.addItem(f"Crée : {text}", v)
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
        lay.addWidget(label("Double-clic : crée la touche et passe à la suivante. Glisser : un ou plusieurs "
                            "inputs sur le deck. Clic droit : remplir une rangée.", "Hint", True))

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


HEAD_H = {"head": 30, "tool": 46, "sub": 24}  # hauteur des lignes de titre ; un bouton : 46


class _PresetDelegate(QStyledItemDelegate):
    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine

    def sizeHint(self, option, index):
        return QSize(200, HEAD_H.get(index.data(ROLE + 2), 46))

    def paint(self, p: QPainter, option, index):
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(option.rect).adjusted(2, 2, -2, -2)
        color = QColor(index.data(ROLE + 3) or T.MUTED)
        kind = index.data(ROLE + 2)
        if kind == "tool":
            # Un outil : pastille d'état (vert relié, rouge en défaut, gris inactif), son nom, puis son état
            ok = index.data(ROLE + 6)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(T.OK if ok else T.FAINT if ok is None else T.DANGER))
            p.drawEllipse(QRectF(r.x() + 7, r.y() + 13, 8, 8))
            f = QFont(option.font)
            f.setPointSizeF(10)
            f.setBold(True)
            p.setFont(f)
            p.setPen(color)
            w = r.width() - 30
            p.drawText(QRectF(r.x() + 24, r.y() + 6, w, 20), Qt.AlignLeft | Qt.AlignVCenter,
                       QFontMetricsF(f).elidedText(index.data(ROLE + 4), Qt.ElideRight, w))
            f2 = QFont(option.font)
            f2.setPointSizeF(7.5)
            p.setFont(f2)
            p.setPen(QColor(T.FAINT))
            p.drawText(QRectF(r.x() + 24, r.y() + 24, w, 16), Qt.AlignLeft | Qt.AlignVCenter,
                       QFontMetricsF(f2).elidedText(index.data(ROLE + 5) or "", Qt.ElideRight, w))
            p.setPen(QColor(T.BORDER))
            p.drawLine(r.topLeft(), r.topRight())
        elif kind in ("head", "sub"):
            f = QFont(option.font)
            f.setPointSizeF(7.6 if kind == "head" else 7.2)
            f.setBold(True)
            f.setLetterSpacing(QFont.AbsoluteSpacing, 1.2)
            p.setFont(f)
            p.setPen(color if kind == "head" else QColor(T.MUTED))
            p.drawText(r.adjusted(6 if kind == "head" else 24, 6 if kind == "head" else 2, 0, 0),
                       Qt.AlignLeft | Qt.AlignVCenter, index.data(ROLE + 4).upper())
        else:
            _row_bg(p, option, r)
            sw = QRectF(r.x() + 6, r.center().y() - 16, 32, 32)
            dpr = option.widget.devicePixelRatioF() if option.widget else 1.0
            thumb = preset_thumb(self.engine, index.data(ROLE), index.data(ROLE + 4).lstrip("→ "), 32, dpr)
            if thumb is not None:  # la touche telle qu'elle sera posée
                p.drawPixmap(sw.topLeft(), thumb)
            else:
                p.setPen(Qt.NoPen)
                c = QColor(color)
                c.setAlphaF(0.18)
                p.setBrush(c)
                p.drawRoundedRect(sw, 6, 6)
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
    """Liste de boutons à glisser sur le deck. tools=False : les familles d'actions (Vidéo, Audio, Windows…).
    tools=True : les outils pilotés, un par section avec son état en direct, ses boutons rangés par rubrique
    et un filtre pour n'en voir qu'un."""
    activated = Signal(str)

    def __init__(self, engine, tools: bool = False):
        super().__init__()
        self.engine, self.tools = engine, tools
        self._sig = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self.pick = None
        if tools:
            self.pick = tame_combo(QComboBox(), 12)
            self.pick.addItem("Tous les outils", "")
            for _, name, _ in presets.TOOLS:
                self.pick.addItem(name, name)
            self.pick.setToolTip("N'afficher qu'un seul outil")
            self.pick.currentIndexChanged.connect(lambda _: self._filter(self.search.text()))
            lay.addWidget(self.pick)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Rechercher : scène, flux, talkback…" if tools else
                                       "Rechercher : take, http, page…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        lay.addWidget(self.search)
        self.list = _PresetList()
        self.list.setItemDelegate(_PresetDelegate(engine, self.list))
        self.list.setDragEnabled(True)
        self.list.setDragDropMode(QAbstractItemView.DragOnly)
        self.list.setMouseTracking(True)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.list.itemDoubleClicked.connect(lambda it: it.data(ROLE) and self.activated.emit(it.data(ROLE)))
        lay.addWidget(self.list, 1)
        lay.addWidget(label("Chaque outil décrit ses propres touches : texte, couleur et état suivent l'outil en "
                            "direct. Glisser sur le deck, ou double-clic. Adresses : Réglages → Intégrations."
                            if tools else
                            "Glisser sur le deck, ou double-clic : pose le bouton sur la touche sélectionnée.",
                            "Hint", True))
        self.refresh()

    def rows(self) -> list[dict]:
        """Lignes à afficher : titres (famille, outil, rubrique) et boutons, chacun avec sa section."""
        out = []
        if not self.tools:
            for cat, items in presets.library(self.engine):
                if cat in presets.TOOL_NAMES:
                    continue  # les outils ont leur onglet
                color = presets.color(cat, T.MUTED)
                out.append({"kind": "head", "name": cat, "color": color, "cat": cat})
                out += [{"kind": "", "pid": pid, "name": name, "desc": desc, "color": color, "cat": cat}
                        for pid, name, desc in items]
            return out
        for tool in presets.tools(self.engine):
            cat, color = tool["name"], tool["color"]
            out.append({"kind": "tool", "name": cat, "color": color, "cat": cat, "desc": tool["status"],
                        "ok": tool["ok"]})
            for rubric, items in tool["groups"]:
                if rubric:
                    out.append({"kind": "sub", "name": rubric, "color": color, "cat": cat})
                out += [{"kind": "", "pid": pid, "name": name, "desc": desc, "color": color, "cat": cat,
                         "sub": rubric} for pid, name, desc in items]
        return out

    @staticmethod
    def _apply(it: QListWidgetItem, row: dict) -> None:
        for role, k in ((ROLE, "pid"), (ROLE + 2, "kind"), (ROLE + 3, "color"), (ROLE + 4, "name"),
                        (ROLE + 5, "desc"), (ROLE + 6, "ok"), (ROLE + 7, "cat"), (ROLE + 8, "sub")):
            it.setData(role, row.get(k))
        tip = row.get("desc") or ""
        it.setToolTip(f"{row['name']} : {tip}" if row["kind"] == "tool" and tip else tip)

    def refresh(self) -> None:
        rows = self.rows()
        sig = json.dumps(rows)
        if sig == self._sig:
            self.list.viewport().update()  # vignettes des touches d'app : leur état change en direct
            return
        shape = [(r["kind"], r.get("pid"), r["name"]) for r in rows]
        same = shape == getattr(self, "_shape", None)
        self._sig, self._shape = sig, shape
        if same:
            # Seuls un état ou une description ont changé : mise à jour sur place, sans perdre la position
            # dans la liste (un outil en direct change souvent : « 3 à remercier », « En cours »…).
            for i, row in enumerate(rows):
                self._apply(self.list.item(i), row)
            self.list.viewport().update()
            return
        pos = self.list.verticalScrollBar().value()
        self.list.clear()
        for row in rows:
            it = QListWidgetItem()
            if row["kind"]:
                it.setFlags(Qt.NoItemFlags)
            self._apply(it, row)
            self.list.addItem(it)
        self._filter(self.search.text())
        self.list.verticalScrollBar().setValue(pos)

    def _filter(self, text: str) -> None:
        """Filtre sur nom, description, section et rubrique ; un titre disparaît si plus rien ne le suit."""
        words = [w for w in text.lower().split() if w]
        only = self.pick.currentData() if self.pick is not None else ""
        heads = {}  # titre en cours par niveau -> [ligne, nombre de boutons visibles]
        for row in range(self.list.count()):
            it = self.list.item(row)
            kind = it.data(ROLE + 2)
            if kind:
                level = 1 if kind == "sub" else 0
                for lv in [lv for lv in heads if lv >= level]:
                    head, n = heads.pop(lv)
                    head.setHidden(n == 0)
                heads[level] = [it, 0]
                continue
            hay = (f"{it.data(ROLE + 7) or ''} {it.data(ROLE + 8) or ''} {it.data(ROLE)} {it.data(ROLE + 4)} "
                   f"{it.data(ROLE + 5) or ''}").lower()
            ok = all(w in hay for w in words) and (not only or it.data(ROLE + 7) == only)
            it.setHidden(not ok)
            for h in heads.values():
                h[1] += ok
        for head, n in heads.values():
            head.setHidden(n == 0)

    def show_tool(self, name: str) -> None:
        """N'afficher que cet outil ("" : tous)."""
        if self.pick is not None:
            self.pick.setCurrentIndex(max(0, self.pick.findData(name)))


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
        self.tabs = Seg([("inputs", "Inputs"), ("presets", "Boutons"), ("tools", "Outils")], "inputs")
        for b in self.tabs.buttons.values():  # trois onglets dans la colonne la plus étroite : ils se partagent la place
            b.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.tabs.buttons["inputs"].setToolTip("Les inputs de vMix, en direct")
        self.tabs.buttons["presets"].setToolTip("Boutons prêts à l'emploi : vidéo, audio, Windows, clavier, web…")
        self.tabs.buttons["tools"].setToolTip("py-intercom, orchestream, py-multiview, MixPanel, DMAIX, caméras "
                                              "ONVIF, Tally32 : chaque outil, son état et ses touches")
        lay.addWidget(self.tabs)
        self.stack = QStackedWidget()
        self.inputs = InputsPage(engine)
        self.presets = PresetsPage(engine)
        self.tools = PresetsPage(engine, tools=True)
        for page in (self.inputs, self.presets, self.tools):
            self.stack.addWidget(page)
        lay.addWidget(self.stack, 1)
        self.tabs.changed.connect(lambda v: self.stack.setCurrentIndex(["inputs", "presets", "tools"].index(v)))
        self.inputs.activated.connect(self.input_activated)
        self.presets.activated.connect(self.preset_activated)
        self.tools.activated.connect(self.preset_activated)

    def refresh_buttons(self) -> None:
        """Boutons et outils : bus, touches des apps et presets des caméras découverts en direct."""
        self.presets.refresh()
        self.tools.refresh()

    def show_tools(self, name: str = "") -> None:
        """Ouvre l'onglet « Outils », sur un seul outil si son nom est donné."""
        self.tabs.set("tools")
        self.stack.setCurrentWidget(self.tools)
        self.tools.show_tool(name)
