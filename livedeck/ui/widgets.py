"""Petits composants réutilisables."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap, QStandardItem
from PySide6.QtWidgets import (QAbstractScrollArea, QAbstractSpinBox, QApplication, QButtonGroup, QCheckBox, QColorDialog, QComboBox, QCompleter, QFormLayout,
                               QFrame, QGridLayout, QHBoxLayout, QLabel, QLayout, QLineEdit, QPlainTextEdit,
                               QPushButton, QSizePolicy, QSlider, QSpinBox, QVBoxLayout, QWidget)

from ..actions import ACTIONS, CATEGORIES, VMIX_FUNCTIONS
from . import theme as T


def typo(text: str) -> str:
    """Espaces insécables du français : jamais de retour à la ligne dans « … » ni avant « : »."""
    return (text or "").replace(" :", " :").replace("« ", "« ").replace(" »", " »")


def label(text: str, name: str = "", wrap: bool = False) -> QLabel:
    lb = QLabel(typo(text))
    if name:
        lb.setObjectName(name)
    lb.setWordWrap(wrap)
    return lb


def tame_combo(c: QComboBox, min_chars: int = 8) -> QComboBox:
    """Une option longue (titre vMix, nom de tally…) ne doit jamais élargir la colonne : la liste fermée est
    raccourcie, la liste ouverte reste assez large pour tout lire (widen_popup)."""
    c.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
    c.setMinimumContentsLength(min_chars)
    c.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    return c


class _WheelGuard(QObject):
    """Dans une zone qui défile, la molette fait défiler la zone : elle ne change jamais la liste, le nombre
    ou le curseur qui passe sous la souris. Ces champs se règlent au clic ou au clavier."""

    def eventFilter(self, obj, e):
        if e.type() == QEvent.Wheel and isinstance(obj, (QComboBox, QAbstractSpinBox, QSlider)):
            area = obj.parentWidget()
            while area is not None and not isinstance(area, QAbstractScrollArea):
                area = area.parentWidget()
            if area is not None:
                QApplication.sendEvent(area.viewport(), e)
                return True
        return False


def install_wheel_guard() -> None:
    """Une seule fois pour toute l'application (fenêtre principale, réglages, petites fenêtres)."""
    app = QApplication.instance()
    if app is not None and not hasattr(app, "_wheel_guard"):
        app._wheel_guard = _WheelGuard(app)
        app.installEventFilter(app._wheel_guard)


def widen_popup(c: QComboBox) -> None:
    """À appeler une fois la liste remplie : le menu déroulant affiche les textes en entier."""
    c.view().setMinimumWidth(min(640, c.view().sizeHintForColumn(0) + 32))


class FlowLayout(QLayout):
    """Disposition qui passe à la ligne quand la largeur manque (puces de catégories…)."""

    def __init__(self, parent=None, spacing: int = 6):
        super().__init__(parent)
        self._items = []
        self._sp = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return self._place(QRect(0, 0, w, 0), False)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._place(rect, True)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        s = QSize()
        for it in self._items:
            s = s.expandedTo(it.minimumSize())
        return s

    def _place(self, rect, apply: bool) -> int:
        x, y, line = rect.x(), rect.y(), 0
        for it in self._items:
            hint = it.sizeHint()
            if x + hint.width() > rect.right() + 1 and line:
                x, y, line = rect.x(), y + line + self._sp, 0
            if apply:
                it.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._sp
            line = max(line, hint.height())
        return y + line - rect.y()


def section(title: str, trailing: QWidget | None = None) -> QHBoxLayout:
    """Titre de section en petites capitales, avec un contrôle optionnel à droite."""
    row = QHBoxLayout()
    row.setContentsMargins(0, 6, 0, 0)
    row.addWidget(label(title.upper(), "H2"))
    row.addStretch()
    if trailing:
        row.addWidget(trailing)
    return row


def form() -> QFormLayout:
    f = QFormLayout()
    f.setContentsMargins(0, 0, 0, 0)
    f.setHorizontalSpacing(10)
    f.setVerticalSpacing(8)
    f.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    f.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
    f.setRowWrapPolicy(QFormLayout.WrapLongRows)  # colonne étroite : le libellé passe au-dessus du champ
    return f


def dot_icon(color: str, size: int = 12, filled: bool = True) -> QIcon:
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QColor(color))
    p.setBrush(QColor(color) if filled else Qt.NoBrush)
    p.drawEllipse(2, 2, size - 4, size - 4)
    p.end()
    return QIcon(pm)


class Seg(QFrame):
    """Contrôle segmenté (comme Édition / Live)."""
    changed = Signal(str)

    def __init__(self, items: list[tuple[str, str]], current: str, live_last: bool = False):
        super().__init__()
        self.setObjectName("SegBox")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)
        self.group = QButtonGroup(self)
        self.buttons = {}
        for i, (value, text) in enumerate(items):
            b = QPushButton(text)
            b.setObjectName("SegLive" if live_last and i == len(items) - 1 else "Seg")
            b.setCheckable(True)
            b.setChecked(value == current)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, v=value: self.changed.emit(v))
            self.group.addButton(b)
            self.buttons[value] = b
            lay.addWidget(b, 1)

    def set(self, value: str):
        if value in self.buttons:
            self.buttons[value].setChecked(True)


class ColorRow(QWidget):
    """Pastilles de couleurs + sélecteur libre. Valeur vide autorisée si allow_none."""
    changed = Signal(str)
    COLS = 8

    def __init__(self, value: str, allow_none: bool = False, swatches=None):
        super().__init__()
        self.value = value or ""
        lay = QGridLayout(self)  # 8 pastilles par ligne : tient dans la colonne la plus étroite
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)
        self.btns = []
        items = ([""] if allow_none else []) + list(swatches or T.SWATCHES)
        for i, c in enumerate(items):
            b = QPushButton("∅" if not c else "")
            b.setFixedSize(18, 18)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(c or "Aucune")
            b.clicked.connect(lambda _=False, c=c: self.set(c, True))
            b.setProperty("color", c)
            lay.addWidget(b, i // self.COLS, i % self.COLS)
            self.btns.append(b)
        self.custom = QPushButton("…")
        self.custom.setFixedSize(24, 18)
        self.custom.setToolTip("Couleur personnalisée")
        self.custom.clicked.connect(self._pick)
        n = len(items)
        lay.addWidget(self.custom, n // self.COLS, n % self.COLS)
        lay.setColumnStretch(self.COLS, 1)
        self._paint()

    def _paint(self):
        for b in self.btns:
            c = b.property("color")
            sel = c.lower() == self.value.lower()
            border = f"2px solid {T.ACCENT}" if sel else f"1px solid {T.BORDER}"
            b.setStyleSheet(f"QPushButton{{background:{c or T.PANEL2};border:{border};border-radius:9px;"
                            f"padding:0;color:{T.MUTED};font-size:9px}}")
        custom = self.value and self.value.lower() not in [b.property("color").lower() for b in self.btns]
        self.custom.setStyleSheet(f"QPushButton{{background:{self.value if custom else T.PANEL2};"
                                  f"border:{'2px solid ' + T.ACCENT if custom else '1px solid ' + T.BORDER};"
                                  f"border-radius:6px;padding:0}}")

    def _pick(self):
        c = QColorDialog.getColor(QColor(self.value or "#000000"), self, "Couleur")
        if c.isValid():
            self.set(c.name(), True)

    def set(self, c: str, emit: bool = False):
        self.value = c
        self._paint()
        if emit:
            self.changed.emit(c)


QT_KEY_NAMES = {
    Qt.Key_Space: "space", Qt.Key_Return: "enter", Qt.Key_Enter: "enter", Qt.Key_Tab: "tab",
    Qt.Key_Backspace: "backspace", Qt.Key_Delete: "delete", Qt.Key_Insert: "insert", Qt.Key_Home: "home",
    Qt.Key_End: "end", Qt.Key_PageUp: "pageup", Qt.Key_PageDown: "pagedown", Qt.Key_Up: "up",
    Qt.Key_Down: "down", Qt.Key_Left: "left", Qt.Key_Right: "right", Qt.Key_Escape: "esc",
    Qt.Key_Print: "printscreen", Qt.Key_Pause: "pause", Qt.Key_Menu: "menu",
    Qt.Key_VolumeUp: "volumeup", Qt.Key_VolumeDown: "volumedown", Qt.Key_VolumeMute: "mute",
    Qt.Key_MediaTogglePlayPause: "playpause", Qt.Key_MediaNext: "next", Qt.Key_MediaPrevious: "previous",
}
MODIFIER_KEYS = {Qt.Key_Shift, Qt.Key_Control, Qt.Key_Alt, Qt.Key_Meta, Qt.Key_AltGr}


class HotkeyEdit(QWidget):
    """Champ texte + bouton « Enregistrer » qui capture la combinaison pressée."""
    changed = Signal(str)

    def __init__(self, value: str):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.edit = QLineEdit(value)
        self.edit.setPlaceholderText("shift+a, ctrl+f5…")
        self.edit.textEdited.connect(self.changed.emit)
        self.edit.setMinimumWidth(60)
        self.rec = QPushButton("● Enregistrer")
        self.rec.setObjectName("Small")
        self.rec.setCheckable(True)
        self.rec.toggled.connect(self._toggle)
        lay.addWidget(self.edit, 1)
        lay.addWidget(self.rec)

    def _toggle(self, on: bool):
        self.rec.setText("Appuie…" if on else "● Enregistrer")
        if on:
            self.grabKeyboard()
        else:
            self.releaseKeyboard()

    def keyPressEvent(self, e):
        if not self.rec.isChecked():
            return super().keyPressEvent(e)
        k = e.key()
        if k in MODIFIER_KEYS:
            return
        mods = e.modifiers()
        parts = [n for m, n in ((Qt.ControlModifier, "ctrl"), (Qt.AltModifier, "alt"),
                                (Qt.ShiftModifier, "shift"), (Qt.MetaModifier, "win")) if mods & m]
        if Qt.Key_F1 <= k <= Qt.Key_F24:
            name = f"f{k - Qt.Key_F1 + 1}"
        elif k in QT_KEY_NAMES:
            name = QT_KEY_NAMES[k]
        elif Qt.Key_A <= k <= Qt.Key_Z or Qt.Key_0 <= k <= Qt.Key_9:
            name = chr(k).lower()
        else:
            name = (e.text() or "").lower()
        if not name:
            return
        combo = "+".join(parts + [name])
        self.edit.setText(combo)
        self.rec.setChecked(False)
        self.changed.emit(combo)


class InputCombo(QWidget):
    """Choix d'un input vMix (liste en direct) avec indication de résolution."""
    changed = Signal(str)

    def __init__(self, engine, value: str):
        super().__init__()
        self.engine = engine
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)
        self.combo = tame_combo(QComboBox(), 10)
        self.combo.setEditable(True)
        self.combo.setInsertPolicy(QComboBox.NoInsert)
        self.combo.setMaxVisibleItems(18)
        for i in engine.vmix.state.inputs:
            self.combo.addItem(f"{i['number']:>2}   {i['title']}", i["title"])
        widen_popup(self.combo)
        self.combo.setCurrentIndex(-1)
        self.combo.setEditText(value)
        self.combo.completer().setCompletionMode(QCompleter.PopupCompletion)
        self.combo.completer().setFilterMode(Qt.MatchContains)
        self.combo.lineEdit().setPlaceholderText("Choisir un input…")
        self.combo.activated.connect(self._picked)
        self.combo.lineEdit().textEdited.connect(self._typed)
        self.status = label("", "Hint", True)
        self.status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        lay.addWidget(self.combo)
        lay.addWidget(self.status)
        self._update_status(value)

    def _picked(self, idx: int):
        title = self.combo.itemData(idx)
        if title is not None:
            self.combo.setEditText(title)
            self._update_status(title)
            self.changed.emit(title)

    def _typed(self, text: str):
        self._update_status(text)
        self.changed.emit(text)

    def _update_status(self, ref: str):
        st = self.engine.vmix.state
        self.status.setVisible(bool(ref))
        if not ref:
            return
        if not st.connected:
            self.status.setText("vMix hors ligne : vérification impossible")
            self.status.setStyleSheet(f"color:{T.FAINT}")
            return
        i = st.resolve(ref)
        self.status.setText(f"✓  Input {i['number']} · {i['title']}" if i else "⚠  Introuvable dans vMix")
        self.status.setStyleSheet(f"color:{T.OK if i else T.DANGER}")


def fill_types(combo: QComboBox, current: str) -> None:
    """Liste des types d'action, regroupés par catégorie (en-têtes colorés non sélectionnables)."""
    combo.blockSignals(True)
    combo.clear()
    model = combo.model()
    for cat, color in CATEGORIES.items():
        items = [(t, s) for t, s in ACTIONS.items() if s["cat"] == cat]
        if not items:
            continue
        head = QStandardItem(cat.upper())
        head.setEnabled(False)
        head.setForeground(QColor(color))
        model.appendRow(head)
        for t, s in items:
            combo.addItem(f"   {s['label']}", t)
    combo.setCurrentIndex(max(0, combo.findData(current)))
    tame_combo(combo, 10)
    widen_popup(combo)
    combo.blockSignals(False)


def make_field(engine, spec: dict, value, on_change, page_names=None) -> QWidget:
    """Construit le widget d'édition d'un champ d'action ou de condition."""
    kind = spec["kind"]
    if kind == "input":
        w = InputCombo(engine, value or "")
        w.changed.connect(on_change)
        return w
    if kind in ("mix", "overlay", "choice", "page", "tally", "intercom_bus", "orch_overlay"):
        w = tame_combo(QComboBox())  # option longue raccourcie ; texte complet en info-bulle et dans la liste
        if kind == "mix":
            items = [(1, "Master (Program)")] + [(i, f"Mix {i}") for i in range(2, 17)]
        elif kind == "overlay":
            items = [(i, f"Overlay {i}") for i in range(1, 9)]
        elif kind == "tally":
            items = [("all", "Tous les tallys")] + [(d.get("name"), f"{d.get('name')}  ·  {d.get('host')}")
                                                     for d in engine.cfg.get("integrations", {}).get("tallies", [])]
        elif kind == "page":
            items = [("next", "Page suivante"), ("prev", "Page précédente")] + list((page_names or {}).items())
        elif kind == "intercom_bus":  # bus proposés par le client py-intercom (lus sur /status)
            items = engine.intercom.bus_choices()
            if value and value not in dict(items):
                items = items + [(value, f"{value} (absent)" if engine.intercom.buses else str(value))]
        elif kind == "orch_overlay":  # overlays réglés + ceux vus en direct sur orchestream
            items = engine.orchestream.names()
            if value and value not in dict(items):
                items = items + [(value, str(value))]
        else:
            items = spec["options"]
        for v, text in items:
            w.addItem(text, v)
            w.setItemData(w.count() - 1, text, Qt.ToolTipRole)
        widen_popup(w)
        idx = w.findData(value)
        w.setCurrentIndex(idx if idx >= 0 else 0)
        w.currentIndexChanged.connect(lambda i: on_change(w.itemData(i)))
        return w
    if kind == "mv_item":  # flux et routes lus sur /status de py-multiview, saisie libre acceptée
        w = tame_combo(QComboBox(), 10)
        w.setEditable(True)
        for name, text in engine.multiview.choices("stream") + engine.multiview.choices("route"):
            w.addItem(name)
            w.setItemData(w.count() - 1, text, Qt.ToolTipRole)
        widen_popup(w)
        w.setEditText(str(value or ""))
        w.lineEdit().setPlaceholderText("nom du flux, ou 1, 2…")
        w.currentTextChanged.connect(on_change)
        return w
    if kind == "function":
        w = tame_combo(QComboBox(), 10)
        w.setEditable(True)
        w.addItems(VMIX_FUNCTIONS)
        widen_popup(w)
        w.setEditText(value or "")
        w.completer().setCompletionMode(QCompleter.PopupCompletion)
        w.completer().setFilterMode(Qt.MatchContains)
        w.lineEdit().setPlaceholderText("Cut, SetText, Snapshot…")
        w.currentTextChanged.connect(on_change)
        return w
    if kind == "winapp":
        # Applications qui produisent du son en ce moment ; on peut aussi taper un nom d'exécutable.
        w = tame_combo(QComboBox(), 10)
        w.setEditable(True)
        try:
            names = [n for n, _, _ in engine.winaudio.apps()]
        except Exception:
            names = []
        w.addItems(names)
        w.setEditText(value or "")
        w.lineEdit().setPlaceholderText(spec.get("placeholder") or "spotify.exe")
        w.setToolTip("Applications qui produisent du son en ce moment. Une application absente de la liste "
                     "peut être tapée : elle sera reconnue dès qu'elle jouera du son.")
        w.currentTextChanged.connect(on_change)
        return w
    if kind in ("step", "percent"):
        w = QSpinBox()
        if kind == "percent":
            w.setRange(0, 100)
            w.setSuffix(" %")
        else:  # pas d'une molette : le signe ne compte que sur une touche (négatif = baisser)
            w.setRange(-1000, 1000)
            w.setToolTip("Valeur d'un cran. Sur une molette, le sens de rotation donne le sens ; "
                         "sur une touche, une valeur négative fait baisser.")
        w.setValue(int(float(value if value not in (None, "") else (spec.get("default") or 0))))
        w.valueChanged.connect(on_change)
        return w
    if kind in ("int", "port", "seconds"):
        w = QSpinBox()
        if kind == "port":
            w.setRange(1, 65535)
        elif kind == "seconds":
            w.setRange(1, 3600)
            w.setSuffix(" s")
        else:
            w.setRange(0, 600000)
            w.setSingleStep(50)
            w.setSuffix(" ms")
        w.setValue(int(value if value not in (None, "") else (spec.get("default") or 0)))
        w.valueChanged.connect(on_change)
        return w
    if kind == "bool":
        w = QCheckBox(spec["label"])
        w.setChecked(bool(value))
        w.toggled.connect(on_change)
        return w
    if kind == "hotkey":
        w = HotkeyEdit(value or "")
        w.changed.connect(on_change)
        return w
    if kind == "multiline":
        w = QPlainTextEdit(value or "")
        w.setObjectName("Field")
        w.setPlaceholderText(spec.get("placeholder", ""))
        w.setFixedHeight(64)
        w.textChanged.connect(lambda: on_change(w.toPlainText()))
        return w
    w = QLineEdit(str(value or ""))
    w.setPlaceholderText(spec.get("placeholder", ""))
    w.textEdited.connect(on_change)
    return w


def ask_slot(parent, choices: list[tuple[str, str]], title: str = "",
             recommended: str = "") -> str | None:
    """Demande où poser une action sur une molette (appui, rotation…). None = annulé.

    Menu sous le pointeur plutôt que boîte de dialogue : on vient de cliquer ou de déposer, la
    réponse se donne d'un second clic au même endroit. Les choix sont les mêmes pour toutes les
    molettes ; seul celui qui convient le mieux à cette action est mis en avant."""
    from PySide6.QtGui import QCursor
    from PySide6.QtWidgets import QMenu
    menu = QMenu(parent)
    if title:
        head = menu.addAction(title)
        head.setEnabled(False)
        menu.addSeparator()
    actions = {}
    for slot, text in choices:
        a = menu.addAction(text + ("   (conseillé)" if slot == recommended else ""))
        if slot == recommended:
            f = a.font()
            f.setBold(True)
            a.setFont(f)
            menu.setActiveAction(a)
        actions[a] = slot
    chosen = menu.exec(QCursor.pos())
    return actions.get(chosen)


def short_label(spec: dict) -> str:
    """Libellé de formulaire court (« Durée (ms, 0 = réglage vMix) » → « Durée »)."""
    return spec["label"].split(" (")[0]
