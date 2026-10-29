"""Panneau de droite : l'inspecteur de la touche sélectionnée."""
from __future__ import annotations

import copy
import json

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout,
                               QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMenu, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout,
                               QWidget)

from .. import feedback, presets
from ..actions import ACTIONS, CATEGORIES, change_type, new_action, summary
from ..feedback import CONDITIONS
from ..model import normalize_button, parse_key
from ..render import render
from ..webreq import parse_curl
from . import theme as T
from .deck_view import pil_to_pixmap
from .widgets import ColorRow, Seg, dot_icon, fill_types, form, label, make_field, section, short_label


def _card() -> tuple[QFrame, QVBoxLayout]:
    f = QFrame()
    f.setObjectName("Card")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(12, 12, 12, 12)
    lay.setSpacing(8)
    return f, lay


class Inspector(QFrame):
    def __init__(self, engine, main):
        super().__init__()
        self.setObjectName("Side")
        self.setFixedWidth(384)
        self.engine, self.main = engine, main
        self.page_id: str | None = None
        self.key: str | None = None
        self.b: dict | None = None
        self._last = None
        self.side = "actions"  # liste affichée : actions / actions_off (interrupteur)
        self.step = 0
        self.preview: QLabel | None = None
        self.auto_desc: QLabel | None = None
        self.steps: QListWidget | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.inner = QWidget()
        self.inner.setObjectName("Transparent")
        self.v = QVBoxLayout(self.inner)
        self.v.setContentsMargins(20, 20, 20, 10)
        self.v.setSpacing(10)
        self.scroll.setWidget(self.inner)
        self.lock_banner = label("🔒  Mode Live : édition verrouillée  ·  F5 pour modifier", "Toast")
        self.lock_banner.setContentsMargins(20, 14, 20, 0)
        self.lock_banner.hide()
        outer.addWidget(self.lock_banner)
        outer.addWidget(self.scroll, 1)

        self.bottom = QWidget()
        bl = QHBoxLayout(self.bottom)
        bl.setContentsMargins(20, 10, 20, 20)
        self.test_btn = QPushButton("▶  Tester")
        self.test_btn.setObjectName("Primary")
        self.test_btn.setToolTip("Déclenche la touche comme sur le Stream Deck (maintenir = maintenu)")
        self.test_btn.pressed.connect(lambda: self.engine.press(self.page_id, self.key, True))
        self.test_btn.released.connect(lambda: self.engine.press(self.page_id, self.key, False))
        self.clear_btn = QPushButton("Effacer")
        self.clear_btn.setObjectName("Danger")
        self.clear_btn.clicked.connect(self.main.delete_selected)
        bl.addWidget(self.test_btn, 1)
        bl.addWidget(self.clear_btn)
        outer.addWidget(self.bottom)
        self.rebuild()

    def set_locked(self, locked: bool) -> None:
        """En Live, on ne peut plus rien modifier par erreur ; « Tester » reste disponible."""
        self.lock_banner.setVisible(locked)
        self.inner.setEnabled(not locked)
        self.clear_btn.setEnabled(not locked)

    # -- chargement -----------------------------------------------------------
    @staticmethod
    def _sig(b) -> str:
        return json.dumps(b, sort_keys=True, ensure_ascii=False)

    def load(self, page_id: str | None, k: str | None, force: bool = False) -> None:
        b = self.engine.button(page_id, k) if page_id and k else None
        if not force and (page_id, k) == (self.page_id, self.key) and self._sig(b) == self._last:
            return
        if (page_id, k) != (self.page_id, self.key):
            self.side, self.step = "actions", 0
        self.page_id, self.key = page_id, k
        self.b = normalize_button(copy.deepcopy(b)) if b else None
        self._last = self._sig(b)
        self.rebuild()

    def on_config(self) -> None:
        if self.page_id and not self.engine.page(self.page_id):
            self.load(None, None)
        else:
            self.load(self.page_id, self.key)

    def commit(self, coalesce: str | None = None, rebuild: bool = False) -> None:
        self._last = self._sig(self.b)
        self.engine.set_button(self.page_id, self.key, copy.deepcopy(self.b) if self.b else None, coalesce)
        if rebuild:
            self.rebuild()
        else:
            if self.auto_desc is not None and self.b:
                self.auto_desc.setText(self._auto_text())
            self._refresh_step_texts()
        self.refresh_preview()

    def refresh_preview(self) -> None:
        if self.preview is None:
            return
        real = self.engine.button(self.page_id, self.key) if self.page_id and self.key else None
        dpr = self.devicePixelRatioF()
        self.preview.setPixmap(pil_to_pixmap(render(self.engine.visual(real), int(64 * dpr)), dpr))

    # -- construction ---------------------------------------------------------
    def rebuild(self) -> None:
        scroll = self.scroll.verticalScrollBar().value()
        while self.v.count():
            it = self.v.takeAt(0)
            if it.widget():
                it.widget().hide()  # masqué tout de suite : deleteLater n'agit qu'au retour dans la boucle
                it.widget().deleteLater()
            elif it.layout():
                _drop_layout(it.layout())
                it.layout().deleteLater()
        self.preview = self.auto_desc = self.steps = None
        if not self.page_id or not self.key:
            self.bottom.hide()
            hint = label("Clique une touche du deck,\nou appuie-la directement sur le Stream Deck.", "Hint", True)
            hint.setAlignment(Qt.AlignCenter)
            self.v.addStretch()
            self.v.addWidget(hint)
            self.v.addStretch()
            return
        self.bottom.setVisible(self.b is not None)
        self._header()
        if self.b is None:
            self._tiles()
        else:
            self._appearance()
            self._actions()
            self._feedbacks()
        self.v.addStretch()
        QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(scroll))

    def _header(self):
        self.v.addWidget(label("TOUCHE", "H2"))
        row = QHBoxLayout()
        row.setSpacing(12)
        if self.b is not None:
            self.preview = QLabel()
            self.preview.setFixedSize(64, 64)
            row.addWidget(self.preview)
        col = QVBoxLayout()
        col.setSpacing(6)
        r, c = parse_key(self.key)
        title = (self.b or {}).get("label", "").split("\n")[0].strip()
        if not title and self.b and self.b["actions"]:
            title = summary(self.b["actions"][0])
        h1 = label(title or ("Touche vide" if self.b is None else "Sans texte"), "H1")
        h1.setMaximumWidth(270)
        col.addWidget(h1)
        chips = QHBoxLayout()
        chips.setSpacing(6)
        page = self.engine.page(self.page_id)
        chips.addWidget(label(f"Rangée {r + 1} · Col. {c + 1}", "Chip"))
        if page:
            chips.addWidget(label(page["name"], "Chip"))
        chips.addStretch()
        col.addLayout(chips)
        row.addLayout(col, 1)
        self.v.addLayout(row)
        self.refresh_preview()

    def _tiles(self):
        self.v.addLayout(section("Que doit faire cette touche ?"))
        grid = QGridLayout()
        grid.setSpacing(6)
        for i, pid in enumerate(presets.TILES):
            cat, name, desc, _ = presets.PRESETS[pid]
            b = QPushButton(f"{name}\n{desc}")
            b.setObjectName("Tile")
            b.setStyleSheet(f"QPushButton#Tile{{border-left:3px solid {CATEGORIES.get(cat, T.MUTED)};}}")
            b.setCursor(Qt.PointingHandCursor)
            b.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            b.setMinimumHeight(50)
            b.setToolTip(desc)
            b.clicked.connect(lambda _=False, p=pid: self._create(p))
            grid.addWidget(b, i // 2, i % 2)
        self.v.addLayout(grid)
        self.v.addWidget(label("Ou glisse un input vMix ou un bouton de la bibliothèque sur la touche.", "Hint", True))
        if self.main.clipboard:
            paste = QPushButton("Coller le bouton copié")
            paste.clicked.connect(self.main.paste)
            self.v.addWidget(paste)

    def _create(self, pid: str):
        self.b = presets.make(pid, self.engine)
        self.side, self.step = "actions", 0
        self.commit(rebuild=True)

    # -- apparence ------------------------------------------------------------
    def _appearance(self):
        b = self.b
        self.v.addLayout(section("Apparence"))
        text = QPlainTextEdit(b["label"])
        text.setObjectName("Field")
        text.setPlaceholderText("Texte de la touche (Entrée = retour à la ligne)")
        text.setFixedHeight(52)
        text.textChanged.connect(lambda: self._set("label", text.toPlainText(), "label"))
        self.label_edit = text
        self.v.addWidget(text)
        bg = ColorRow(b["bg"])
        bg.changed.connect(lambda v: self._set("bg", v))
        self.v.addWidget(bg)
        f = form()
        fg = ColorRow(b["fg"], swatches=T.FG_SWATCHES)
        fg.changed.connect(lambda v: self._set("fg", v))
        f.addRow(label("Texte", "Muted"), fg)
        row = QHBoxLayout()
        size = QComboBox()
        for v in [0, 10, 12, 14, 16, 18, 20, 24, 28, 32, 40]:
            size.addItem("Taille auto" if v == 0 else f"{v} px", v)
        size.setCurrentIndex(max(0, size.findData(int(b.get("size") or 0))))
        size.currentIndexChanged.connect(lambda i: self._set("size", size.itemData(i)))
        row.addWidget(size, 1)
        icon = QPushButton("Icône…" if not b.get("icon") else "Icône ✓")
        icon.setObjectName("Small")
        icon.setToolTip(b.get("icon") or "Ajouter une image PNG au-dessus du texte")
        icon.clicked.connect(self._pick_icon)
        row.addWidget(icon)
        if b.get("icon"):
            rm = QPushButton("✕")
            rm.setObjectName("Ghost")
            rm.setToolTip("Retirer l'icône")
            rm.clicked.connect(lambda: (self._set("icon", ""), self.rebuild()))
            row.addWidget(rm)
        f.addRow(label("Police", "Muted"), row)
        self.v.addLayout(f)

    def _pick_icon(self):
        path, _ = QFileDialog.getOpenFileName(self, "Icône", "", "Images (*.png *.jpg *.jpeg *.bmp *.gif)")
        if path:
            self._set("icon", path)
            self.rebuild()

    def _set(self, k: str, v, coalesce: str | None = None):
        if self.b.get(k) == v:
            return
        self.b[k] = v
        self.commit(coalesce)

    # -- actions --------------------------------------------------------------
    def _actions(self):
        mode = Seg([("press", "Appui"), ("toggle", "Interrupteur")], self.b["mode"])
        mode.setToolTip("Interrupteur : alterne entre deux séries d'actions et reste allumé tant qu'il est actif")
        mode.changed.connect(self._set_mode)
        self.v.addLayout(section("Actions", mode))
        toggle = self.b["mode"] == "toggle"
        if toggle and self.side == "long_actions" or not toggle and self.side == "actions_off":
            self.side = "actions"
        sides = ([("actions", "À l'activation"), ("actions_off", "À la désactivation")] if toggle
                 else [("actions", "Appui court"), ("long_actions", "Appui long")])
        side = Seg(sides, self.side)
        side.changed.connect(self._set_side)
        if not toggle:
            side.buttons["long_actions"].setText("Appui long" + (f"  ·  {len(self.b['long_actions'])}"
                                                                if self.b["long_actions"] else ""))
        self.v.addWidget(side)
        confirm = QCheckBox("Sécurité : maintenir ½ s pour déclencher")
        confirm.setToolTip("Évite un appui accidentel (FTB, arrêt du stream…). Une jauge se remplit sur la touche.")
        confirm.setChecked(bool(self.b.get("confirm")))
        confirm.toggled.connect(lambda v: (self._set("confirm", v), self.rebuild()))
        self.v.addWidget(confirm)
        if self.side == "long_actions" and self.b.get("confirm"):
            self.v.addWidget(label("Avec la sécurité, l'appui long déclenche déjà les actions de l'appui court : "
                                   "cette liste est ignorée.", "Hint", True))
        elif not toggle and self.b["long_actions"] and not self.b.get("confirm"):
            self.v.addWidget(label("Un appui long est défini : l'appui court part au relâchement de la touche.",
                                   "Hint", True))
        lst = self.b[self.side]
        self.step = min(self.step, max(0, len(lst) - 1))

        if lst:
            self.steps = QListWidget()
            self.steps.setObjectName("Steps")
            self.steps.setDragDropMode(QAbstractItemView.InternalMove)
            self.steps.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self.steps.setWordWrap(False)
            self.steps.setTextElideMode(Qt.ElideMiddle)
            self.steps.setUniformItemSizes(True)
            self.steps.setFixedHeight(min(6, len(lst)) * 38 + 12)
            for i, a in enumerate(lst):
                it = QListWidgetItem(dot_icon(CATEGORIES.get(ACTIONS.get(a["type"], {}).get("cat"), T.MUTED)), "")
                it.setData(Qt.UserRole, i)
                it.setSizeHint(QSize(0, 36))
                self.steps.addItem(it)
            self._refresh_step_texts()
            self.steps.setCurrentRow(self.step)
            self.steps.currentRowChanged.connect(self._select_step)
            self.steps.model().rowsMoved.connect(lambda *_: QTimer.singleShot(0, self._reordered))
            self.steps.setIconSize(QSize(10, 10))
            self.v.addWidget(self.steps)
        else:
            self.v.addWidget(label("Aucune action pour l'instant.", "Hint"))

        row = QHBoxLayout()
        row.setSpacing(6)
        add = QPushButton("＋ Action")
        add.setObjectName("Small")
        menu = QMenu(add)
        for cat, color in CATEGORIES.items():
            items = [(t, s) for t, s in ACTIONS.items() if s["cat"] == cat]
            if items:
                menu.addSection(cat)
                for t, s in items:
                    menu.addAction(dot_icon(color), s["label"], lambda t=t: self._add_action(t))
        add.setMenu(menu)
        row.addWidget(add)
        pause = QPushButton("＋ Pause")
        pause.setObjectName("Small")
        pause.clicked.connect(lambda: self._add_action("delay"))
        row.addWidget(pause)
        row.addStretch()
        for text, tip, fn in (("⧉", "Dupliquer l'étape", self._dup_step), ("✕", "Supprimer l'étape", self._del_step)):
            b = QPushButton(text)
            b.setObjectName("Ghost")
            b.setToolTip(tip)
            b.setEnabled(bool(lst))
            b.clicked.connect(fn)
            row.addWidget(b)
        self.v.addLayout(row)
        if lst:
            self.v.addWidget(self._step_editor(lst[self.step]))

    def _refresh_step_texts(self):
        if self.steps is None or not self.b:
            return
        lst = self.b[self.side]
        names = self.engine.page_names()
        for i in range(self.steps.count()):
            it = self.steps.item(i)
            idx = it.data(Qt.UserRole)
            if idx is not None and idx < len(lst):
                it.setText(f"{i + 1}.  {summary(lst[idx], names)}")

    def _step_editor(self, a: dict) -> QWidget:
        frame, lay = _card()
        types = QComboBox()
        types.setMaxVisibleItems(20)
        fill_types(types, a["type"])
        types.currentIndexChanged.connect(lambda i: self._change_type(types.itemData(i)))
        lay.addWidget(types)
        spec = ACTIONS.get(a["type"], {"fields": [], "help": ""})
        if spec.get("help"):
            lay.addWidget(label(spec["help"], "Hint", True))
        f = form()
        for fs in spec["fields"]:
            w = make_field(self.engine, fs, a.get(fs["key"], fs["default"]),
                           lambda v, k=fs["key"]: self._set_field(k, v), self.engine.page_names())
            if fs["kind"] in ("bool", "input", "multiline", "hotkey"):
                if fs["kind"] != "bool":
                    f.addRow(label(short_label(fs), "Muted"))
                f.addRow(w)
            else:
                f.addRow(label(short_label(fs), "Muted"), w)
        lay.addLayout(f)
        if a["type"] == "http":
            curl = QPushButton("Coller une commande curl…")
            curl.setObjectName("Small")
            curl.clicked.connect(self._paste_curl)
            lay.addWidget(curl)
        return frame

    def _set_mode(self, mode: str):
        if self.b["mode"] != mode:
            self.b["mode"] = mode
            self.side, self.step = "actions", 0
            self.commit(rebuild=True)

    def _set_side(self, side: str):
        self.side, self.step = side, 0
        self.rebuild()

    def _select_step(self, row: int):
        if row >= 0 and row != self.step:
            self.step = row
            self.rebuild()

    def _reordered(self):
        if self.steps is None:
            return
        lst = self.b[self.side]
        order = [self.steps.item(i).data(Qt.UserRole) for i in range(self.steps.count())]
        if order == list(range(len(lst))):
            return
        self.b[self.side] = [lst[i] for i in order]
        self.step = self.steps.currentRow()
        self.commit(rebuild=True)

    def _add_action(self, t: str):
        lst = self.b[self.side]
        prev = next((a for a in reversed(lst) if a.get("input")), None)
        a = new_action(t)
        if prev and "input" in a and t != "vmix.transition":
            a["input"] = prev["input"]  # même input que l'étape précédente : souvent ce qu'on veut
        lst.append(a)
        self.step = len(lst) - 1
        self.commit(rebuild=True)

    def _dup_step(self):
        lst = self.b[self.side]
        if lst:
            lst.insert(self.step + 1, copy.deepcopy(lst[self.step]))
            self.step += 1
            self.commit(rebuild=True)

    def _del_step(self):
        lst = self.b[self.side]
        if lst:
            lst.pop(self.step)
            self.step = max(0, self.step - 1)
            self.commit(rebuild=True)

    def _change_type(self, t: str | None):
        lst = self.b[self.side]
        if not t or not lst or lst[self.step]["type"] == t:
            return
        old = lst[self.step]["type"]
        lst[self.step] = change_type(lst[self.step], t)
        a = lst[self.step]
        if "input" in a and not a["input"]:  # reprendre l'input déjà utilisé par la touche
            a["input"] = next((x["input"] for x in lst if x.get("input")), "")
        if self.step == 0 and self.b["bg"] == ACTIONS.get(old, {}).get("color"):
            self.b["bg"] = ACTIONS[t]["color"]  # la couleur suit le type tant qu'on ne l'a pas choisie
        self.commit(rebuild=True)

    def _set_field(self, k: str, v):
        a = self.b[self.side][self.step]
        old = a.get(k)
        if old == v:
            return
        a[k] = v
        # le texte de la touche suit l'input tant qu'on ne l'a pas personnalisé
        if k == "input" and self.side == "actions" and self.step == 0:
            new_label = None
            if self.b["label"] in ("", str(old or "")):
                new_label = v
            elif self.b["label"] == f"PIP {old}":
                new_label = f"PIP {v}"
            if new_label is not None:
                self.b["label"] = new_label
                self.label_edit.blockSignals(True)
                self.label_edit.setPlainText(new_label)
                self.label_edit.blockSignals(False)
        self.commit(coalesce=f"{self.side}.{self.step}.{k}")

    def _paste_curl(self):
        text, ok = QInputDialog.getMultiLineText(self, "Commande curl", "Collez la commande curl :")
        if not ok or not text.strip():
            return
        try:
            parsed = parse_curl(text)
        except ValueError as e:
            self.engine.log("error", f"curl illisible : {e}")
            return
        self.b[self.side][self.step].update(parsed)
        self.commit(rebuild=True)

    # -- retours d'état -------------------------------------------------------
    def _auto_text(self) -> str:
        lines = self.engine.describe_auto(self.b)
        if not lines:
            return "Rien à afficher automatiquement pour ces actions."
        return "\n".join(f"•  {line}" for line in lines)

    def _feedbacks(self):
        add = QPushButton("＋ Personnaliser")
        add.setObjectName("Ghost")
        menu = QMenu(add)
        for t, spec in CONDITIONS.items():
            menu.addAction(spec["label"], lambda t=t: self._add_fb(t))
        add.setMenu(menu)
        self.v.addLayout(section("Retour d'état", add))
        auto = QCheckBox("Automatique, déduit des actions")
        auto.setChecked(self.b.get("auto_feedback", True))
        auto.toggled.connect(lambda v: self._set("auto_feedback", v))
        self.v.addWidget(auto)
        self.auto_desc = label(self._auto_text(), "Hint", True)
        self.v.addWidget(self.auto_desc)
        for i, f in enumerate(self.b["feedbacks"]):
            self.v.addWidget(self._fb_card(i, f))

    def _add_fb(self, t: str):
        cond = {"type": t, **{f["key"]: f["default"] for f in CONDITIONS[t]["fields"]}}
        first = next((a for a in self.b["actions"] if a.get("input")), None)
        if first and "input" in cond:
            cond["input"] = first["input"]
        self.b["feedbacks"].append({"cond": cond, "bg": feedback.RED, "fg": "#ffffff", "bar": "", "label": ""})
        self.commit(rebuild=True)

    def _fb_card(self, i: int, f: dict) -> QWidget:
        frame, lay = _card()
        cond = f["cond"]
        spec = CONDITIONS.get(cond.get("type"), {"label": cond.get("type"), "fields": []})
        head = QHBoxLayout()
        head.addWidget(label("SI", "Chip"))
        head.addWidget(label(spec["label"]))
        head.addStretch()
        rm = QPushButton("✕")
        rm.setObjectName("Ghost")
        rm.clicked.connect(lambda: self._remove_fb(i))
        head.addWidget(rm)
        lay.addLayout(head)
        fl = form()
        for fs in spec["fields"]:
            w = make_field(self.engine, fs, cond.get(fs["key"], fs["default"]),
                           lambda v, k=fs["key"]: self._set_cond(i, k, v))
            if fs["kind"] in ("input", "multiline"):
                fl.addRow(label(short_label(fs), "Muted"))
                fl.addRow(w)
            else:
                fl.addRow(label(short_label(fs), "Muted"), w)
        for k, name, sw in (("bg", "Fond", T.FB_SWATCHES), ("fg", "Texte", ["#ffffff", "#000000", "#facc15"]),
                            ("bar", "Liseré", [feedback.RED, feedback.GREEN, feedback.YELLOW, feedback.BLUE])):
            row = ColorRow(f.get(k, ""), allow_none=True, swatches=sw)
            row.changed.connect(lambda v, k=k: self._set_fb(i, k, v))
            fl.addRow(label(name, "Muted"), row)
        txt = QLineEdit(f.get("label", ""))
        txt.setPlaceholderText("Remplacer le texte (optionnel)")
        txt.textEdited.connect(lambda v: self._set_fb(i, "label", v, f"fb.{i}.label"))
        fl.addRow(txt)
        lay.addLayout(fl)
        return frame

    def _set_cond(self, i: int, k: str, v):
        self.b["feedbacks"][i]["cond"][k] = v
        self.commit(coalesce=f"fb.{i}.{k}")

    def _set_fb(self, i: int, k: str, v, coalesce: str | None = None):
        self.b["feedbacks"][i][k] = v
        self.commit(coalesce)

    def _remove_fb(self, i: int):
        self.b["feedbacks"].pop(i)
        self.commit(rebuild=True)


def _drop_layout(lay):
    while lay.count():
        it = lay.takeAt(0)
        if it.widget():
            it.widget().hide()
            it.widget().deleteLater()
        elif it.layout():
            _drop_layout(it.layout())
            it.layout().deleteLater()
