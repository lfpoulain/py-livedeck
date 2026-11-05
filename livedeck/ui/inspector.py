"""Panneau de droite : l'inspecteur de la touche sélectionnée."""
from __future__ import annotations

import copy
import html
import json
import threading

from PySide6.QtCore import QObject, QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout,
                               QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMenu, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QSpinBox,
                               QVBoxLayout, QWidget)

from .. import feedback, presets
from ..actions import (ACTIONS, CATEGORIES, INTEGRATION_OF, change_type, new_action, next_press, shown, summary,
                       to_take)
from ..adjust import VMIX_STEP
from ..feedback import CONDITIONS
from ..integrations import integration_status, probe_intercom, probe_orchestream, probe_tally
from ..loupedeck import CONTROL_LABELS, HAPTIC, control_kind
from ..model import normalize_button, normalize_control, parse_control, parse_key
from ..render import render
from ..webreq import parse_curl
from . import theme as T
from . import widgets as W
from .deck_view import pil_to_pixmap
from .widgets import (ColorRow, FlowLayout, InputCombo, Seg, dot_icon, fill_types, form, label, make_field,
                      section, short_label, tame_combo)

INTEG_NAMES = {"intercom": "py-intercom", "orchestream": "orchestream", "tallies": "Tally32",
               "multiview": "py-multiview"}


def prepare_preset(parent, b: dict, k: str | None) -> tuple[dict, str] | None:
    """Prépare un bouton de la bibliothèque pour la case `k` : (bouton, liste à afficher), ou None
    si l'utilisateur annule.

    Sur une molette, on DEMANDE où ranger l'action (appui, rotation, sens) au lieu de la poser
    d'office sur l'appui : la même action n'a pas du tout le même effet selon l'emplacement."""
    cid = parse_control(k) if k else None
    if cid is None or control_kind(cid) != "rotary":
        return (presets.place(b, k) if k else b), "actions"
    choices, best = presets.slots(b)
    if len(choices) > 1:
        slot = W.ask_slot(parent, choices, f"{CONTROL_LABELS.get(cid, cid)} : quand déclencher ?", best)
        if slot is None:
            return None
    else:
        slot = choices[0][0] if choices else "press"
    return presets.place(presets.assign(b, slot), k), presets.SLOT_SIDE.get(slot, "actions")


def _card() -> tuple[QFrame, QVBoxLayout]:
    f = QFrame()
    f.setObjectName("Card")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(12, 12, 12, 12)
    lay.setSpacing(8)
    return f, lay


def _dot(ok) -> str:
    return f"<span style='color:{T.OK if ok else T.FAINT if ok is None else T.DANGER}'>●</span>&nbsp; "


def wrap(lb: QLabel) -> QLabel:
    """Texte qui passe à la ligne et n'impose jamais sa largeur à la colonne."""
    lb.setWordWrap(True)
    lb.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    return lb


class _Scroll(QScrollArea):
    """Défilement vertical seulement : une molette inclinée ou un pavé tactile ne décale jamais la colonne."""

    def wheelEvent(self, e):
        if e.angleDelta().y() == 0 and e.angleDelta().x() != 0:
            e.ignore()
            return
        super().wheelEvent(e)


class Tile(QPushButton):
    """Tuile « nom + description » : la description passe sur 2 lignes, au-delà elle est raccourcie (…)
    au lieu de déborder."""

    def __init__(self, name: str, sub: str):
        super().__init__(f"{name}\n{sub}")
        self.setObjectName("Tile")
        self._lines = (name, sub)
        self.setFixedHeight(self.fontMetrics().lineSpacing() * 3 + 24)

    def resizeEvent(self, e):
        fm, w = self.fontMetrics(), max(20, self.width() - 30)
        lines, cur = [], ""
        for word in self._lines[1].split():
            t = f"{cur} {word}".strip()
            if cur and not lines and fm.horizontalAdvance(t) > w:
                lines.append(cur)
                cur = word
            else:
                cur = t
        lines.append(cur)
        self.setText("\n".join(fm.elidedText(t, Qt.ElideRight, w) for t in (self._lines[0], *lines)))
        super().resizeEvent(e)


class _Probe(QObject):
    done = Signal(str, object, str)  # intégration, état, message


def probe(engine, key: str) -> tuple[bool | None, str]:
    """Test immédiat d'une intégration avec les réglages enregistrés (bouton « Tester la connexion »)."""
    integ = engine.cfg.get("integrations", {})
    if key == "intercom":
        ic = integ.get("intercom") or {}
        return probe_intercom(ic.get("host", "127.0.0.1"), ic.get("port", 5080))
    if key == "orchestream":
        return probe_orchestream((integ.get("orchestream") or {}).get("url", ""))
    devices = integ.get("tallies") or []
    if not devices:
        return None, "Aucun tally renseigné : Réglages → Intégrations."
    res = [(d.get("name") or d.get("host"), *probe_tally(d.get("host", ""))) for d in devices]
    oks = [ok for _, ok, _ in res]
    return (False if False in oks else None if None in oks else True), "  ·  ".join(f"{n} : {m}" for n, _, m in res)


def integration_line(engine, key: str, a: dict | None = None) -> tuple[bool | None, str]:
    """État en direct d'une intégration, précisé pour l'action (bus, overlay) quand c'est utile."""
    st = next(((ok, text) for k, _, ok, text in integration_status(engine) if k == key), (None, ""))
    ok, text = st
    a = a or {}
    if key == "intercom" and a.get("bus") and a.get("mode") != "alloff":
        why = engine.intercom.unavailable(a["bus"])
        if why:
            ok, text = False, f"{why} : {text}"
    return ok, text


class Inspector(QFrame):
    def __init__(self, engine, main):
        super().__init__()
        self.setObjectName("Side")
        self.setMinimumWidth(360)
        self.setMaximumWidth(680)
        self.engine, self.main = engine, main
        self.page_id: str | None = None
        self.key: str | None = None
        self.b: dict | None = None
        self._last = None
        self.side = "actions"  # liste affichée : actions / actions_off (interrupteur)
        self.step = 0
        self.locked = False
        self.preview: QLabel | None = None
        self.auto_desc: QLabel | None = None
        self.steps: QListWidget | None = None
        self.next_lbl: QLabel | None = None
        self._integ_lbls: list = []  # (QLabel, intégration, action) mis à jour en direct
        self._pick_text, self._pick_cat = "", ""
        self._probe = _Probe(self)
        self._probe.done.connect(self._probe_done)
        self._probe_lbls: dict = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.scroll = _Scroll()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.inner = QWidget()
        self.inner.setObjectName("Transparent")
        # filet de sécurité : le contenu prend toujours la largeur de la colonne, jamais plus (pas de
        # défilement horizontal caché qui décalerait les éléments)
        self.inner.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.v = QVBoxLayout(self.inner)
        self.v.setContentsMargins(20, 20, 20, 10)
        self.v.setSpacing(10)
        self.scroll.setWidget(self.inner)
        self.lock_banner = wrap(label("🔒  Mode Live : édition verrouillée  ·  F5 pour modifier", "Toast"))
        self.lock_banner.setContentsMargins(20, 14, 20, 0)
        self.lock_banner.hide()
        outer.addWidget(self.lock_banner)
        outer.addWidget(self.scroll, 1)

        self.bottom = QWidget()
        bl = QHBoxLayout(self.bottom)
        bl.setContentsMargins(20, 10, 20, 20)
        self.test_btn = QPushButton("▶  Tester")
        self.test_btn.setObjectName("Primary")
        self.test_btn.setToolTip("Déclenche la touche comme sur le Stream Deck (maintenir = maintenu, "
                                 "sans la sécurité « maintenir pour déclencher »)  ·  Entrée")
        self._test_key = None  # touche figée à l'appui : le relâché va toujours à la même
        self.test_btn.pressed.connect(self._test_down)
        self.test_btn.released.connect(self._test_up)
        self.clear_btn = QPushButton("Effacer")
        self.clear_btn.setObjectName("Danger")
        self.clear_btn.clicked.connect(self.main.delete_selected)
        bl.addWidget(self.test_btn, 1)
        bl.addWidget(self.clear_btn)
        outer.addWidget(self.bottom)
        self.rebuild()

    def set_locked(self, locked: bool) -> None:
        """En Live, l'éditeur laisse place à une vue d'exploitation (rien ne se modifie par erreur) ;
        « Tester » reste disponible."""
        if locked == self.locked:
            return
        self.locked = locked
        self.lock_banner.setVisible(locked)
        self.clear_btn.setVisible(not locked)
        self.rebuild()

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
        cid = parse_control(k) if k else None
        self.b = (normalize_control(copy.deepcopy(b)) if cid is not None
                  else normalize_button(copy.deepcopy(b))) if b else None
        self._last = self._sig(b)
        self.rebuild()

    def on_config(self) -> None:
        if self.page_id and not self.engine.page(self.page_id):
            self.load(None, None)
        else:
            self.load(self.page_id, self.key)

    def _test_down(self) -> None:
        if self.page_id and self.key:
            self._test_key = (self.page_id, self.key)
            self.engine.press(self.page_id, self.key, True, test=True)

    def _test_up(self) -> None:
        if self._test_key:
            page_id, k = self._test_key
            self._test_key = None
            self.engine.press(page_id, k, False, test=True)

    def commit(self, coalesce: str | None = None, rebuild: bool = False) -> None:
        self._last = self._sig(self.b)
        self.engine.set_button(self.page_id, self.key, copy.deepcopy(self.b) if self.b else None, coalesce)
        if rebuild:
            self.rebuild()
        else:
            if self.auto_desc is not None and self.b:
                self.auto_desc.setText(self._auto_text())
            self._refresh_step_texts()
            try:
                self._title()
            except (AttributeError, RuntimeError):  # en-tête pas encore construit
                pass
        self.refresh_preview()

    def refresh_preview(self) -> None:
        """Rafraîchissement en direct (événements « state ») : aperçu, prochain appui, état des intégrations."""
        real = self.engine.button(self.page_id, self.key) if self.page_id and self.key else None
        if self.preview is not None:
            dpr = self.devicePixelRatioF()
            size = self.preview.width()
            self.preview.setPixmap(pil_to_pixmap(render(self.engine.visual(real), int(size * dpr)), dpr))
        if self.next_lbl is not None:
            nxt = next_press(self.engine, real) if real else ""
            self.next_lbl.setVisible(bool(nxt))
            self.next_lbl.setText(f"Prochain appui : <b>{html.escape(nxt)}</b>")
        for lb, key, a in self._integ_lbls:
            ok, text = integration_line(self.engine, key, a)
            lb.setText(_dot(ok) + f"<b>{INTEG_NAMES[key]}</b> · " + html.escape(text))

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
        self.preview = self.auto_desc = self.steps = self.next_lbl = None
        self._integ_lbls = []
        if not self.page_id or not self.key:
            self.bottom.hide()
            hint = label("Clique une touche du deck,\nou appuie-la directement sur le Stream Deck.", "Hint", True)
            hint.setAlignment(Qt.AlignCenter)
            self.v.addStretch()
            self.v.addWidget(wrap(hint))
            self.v.addStretch()
            return
        self.bottom.setVisible(self.b is not None)
        self._header()
        if self.locked:
            if self.b is None:
                self.v.addWidget(wrap(label("Touche vide. Repasse en Édition (F5) pour lui donner une action.",
                                            "Hint")))
            else:
                self._live_view()
        elif self.b is None:
            self._picker()
        else:
            self._appearance()
            self._actions()
            self._feedbacks()
        self.v.addStretch()
        self.refresh_preview()
        QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(scroll))

    def _header(self):
        self.v.addWidget(label("TOUCHE", "H2"))
        row = QHBoxLayout()
        row.setSpacing(12)
        if self.b is not None:
            self.preview = QLabel()
            size = 96 if self.locked else 64
            self.preview.setFixedSize(size, size)
            row.addWidget(self.preview, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(6)
        cid = parse_control(self.key)
        self.h1 = wrap(label("", "H1"))
        self._title()
        col.addWidget(self.h1)
        chips = FlowLayout(spacing=6)
        page = self.engine.page(self.page_id)
        if cid is not None:
            chips.addWidget(label(CONTROL_LABELS.get(cid, cid), "Chip"))
            if control_kind(cid) == "rotary":
                chips.addWidget(label("Molette", "Chip"))
        else:
            r, c = parse_key(self.key)
            chips.addWidget(label(f"Rangée {r + 1} · Col. {c + 1}", "Chip"))
        if page:
            chips.addWidget(label(page["name"], "Chip"))
        if self.b and self.b.get("mode") == "toggle":
            chips.addWidget(label("Interrupteur", "Chip"))
        if self.b and self.b.get("confirm"):
            chips.addWidget(label("Sécurité ½ s", "Chip"))
        col.addLayout(chips)
        row.addLayout(col, 1)
        self.v.addLayout(row)
        if self.b is not None:
            self.next_lbl = wrap(label("", "Toast"))
            self.next_lbl.setTextFormat(Qt.RichText)
            self.next_lbl.setToolTip("D'après l'état actuel de vMix : vert = en preview (prêt à prendre), "
                                     "rouge = à l'antenne")
            self.next_lbl.hide()
            self.v.addWidget(self.next_lbl)

    def _title(self):
        b = self.b or {}
        full = b.get("label", "").replace("\n", " ").strip()
        title = b.get("label", "").split("\n")[0].strip()
        if not title and b.get("actions"):
            title = summary(b["actions"][0])
        self.h1.setText(title or ("Touche vide" if self.b is None else "Sans texte"))
        self.h1.setToolTip(full or title)

    # -- touche vide : tout ce qu'elle peut devenir -----------------------------------
    def _picker(self):
        self.v.addLayout(section("Que doit faire cette touche ?"))
        search = QLineEdit(self._pick_text)
        search.setPlaceholderText("Rechercher : take, intercom, overlay, http…")
        search.setClearButtonEnabled(True)
        self.v.addWidget(search)
        chips_box = QWidget()
        chips = FlowLayout(chips_box, spacing=4)
        self._chips = {}
        groups = presets.catalog(self.engine, rotary=self._is_rotary())
        for cat in [""] + [c for c, _ in groups]:
            b = QPushButton(cat or "Tout")
            b.setObjectName("Chip")
            b.setCheckable(True)
            b.setChecked(cat == self._pick_cat)
            b.setCursor(Qt.PointingHandCursor)
            color = CATEGORIES.get(cat, T.ACCENT)
            b.setStyleSheet(f"QPushButton#Chip:checked{{color:#0b0d10;background:{color};border-color:{color}}}")
            b.clicked.connect(lambda _=False, c=cat: self._pick_filter(cat=c))
            chips.addWidget(b)
            self._chips[cat] = b
        self.v.addWidget(chips_box)
        self.pick_box = QWidget()
        self.pick_lay = QVBoxLayout(self.pick_box)
        self.pick_lay.setContentsMargins(0, 0, 0, 0)
        self.pick_lay.setSpacing(8)
        self.v.addWidget(self.pick_box)
        search.textChanged.connect(lambda t: self._pick_filter(text=t))
        self.pick_search = search
        self._fill_picker()
        self.v.addWidget(wrap(label("Ou glisse un input vMix ou un bouton de la bibliothèque sur la touche.",
                                    "Hint")))
        if self.main.clipboard:
            paste = QPushButton("Coller la touche copiée")
            paste.clicked.connect(self.main.paste)
            self.v.addWidget(paste)

    def _pick_filter(self, text: str | None = None, cat: str | None = None):
        if text is not None:
            self._pick_text = text
        if cat is not None:
            self._pick_cat = cat
            for c, b in self._chips.items():
                b.setChecked(c == cat)
        self._fill_picker()

    def picker_items(self) -> list[tuple[str, list]]:
        """Groupes affichés après filtre (catégorie choisie + recherche sur nom, description, catégorie)."""
        words = [w for w in self._pick_text.lower().split() if w]
        out = []
        for cat, items in presets.catalog(self.engine, rotary=self._is_rotary()):
            if self._pick_cat and cat != self._pick_cat:
                continue
            keep = [it for it in items if all(w in f"{cat} {it[0]} {it[1]} {it[2]}".lower() for w in words)]
            if keep:
                out.append((cat, keep))
        return out

    def _fill_picker(self):
        _drop_layout(self.pick_lay)
        groups = self.picker_items()
        if not groups:
            self.pick_lay.addWidget(wrap(label("Rien ne correspond. Essaie « vmix », « web », « clavier »…",
                                               "Hint")))
        for cat, items in groups:
            color = CATEGORIES.get(cat, T.MUTED)
            head = label(cat.upper(), "H2")
            head.setStyleSheet(f"color:{color}")
            self.pick_lay.addWidget(head)
            grid = QGridLayout()
            grid.setSpacing(6)
            for i, (pid, name, desc, setup) in enumerate(items):
                sub = ({"intercom": "py-intercom injoignable",
                        "always_on": "toujours ouvert dans py-intercom : rien à piloter"}.get(setup)
                       or f"à configurer · {INTEG_NAMES.get(setup, setup)}") if setup else desc
                b = Tile(name, sub)
                b.setProperty("pid", pid)
                b.setStyleSheet(f"QPushButton#Tile{{border-left:3px solid {color};"
                                + (f"color:{T.MUTED};" if setup else "") + "}")
                b.setCursor(Qt.PointingHandCursor)
                b.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
                b.setToolTip(f"{name} : {desc}" + (
                    "\nCe bus est en mode « Toujours actif » dans py-intercom : change son mode dans le client "
                    "pour le piloter d'ici." if setup == "always_on" else
                    "\nÀ vérifier dans Réglages → Intégrations : la touche est créée quand même." if setup else ""))
                b.clicked.connect(lambda _=False, p=pid: self._create(p))
                grid.addWidget(b, i // 2, i % 2)
            self.pick_lay.addLayout(grid)

    def _is_rotary(self) -> bool:
        cid = parse_control(self.key) if self.key else None
        return cid is not None and control_kind(cid) == "rotary"

    def _create(self, pid: str):
        b = presets.make(pid, self.engine)
        if not b:
            return
        got = prepare_preset(self, b, self.key)
        if got is None:
            return  # question annulée : on ne crée rien
        self.b, side = got
        self.side, self.step = side, 0
        self.commit(rebuild=True)
        if pid in presets.NEEDS_INPUT or pid == "knob_input":  # on demande tout de suite l'input
            QTimer.singleShot(0, self.focus_input)

    def focus_input(self, popup: bool = True):
        """Place le curseur dans le choix de l'input (et ouvre la liste des inputs vMix) ; renvoie ce champ."""
        combos = [c for c in self.inner.findChildren(InputCombo) if c.isVisibleTo(self.inner)]
        if not combos:
            return None
        c = combos[0]
        self.scroll.ensureWidgetVisible(c, 0, 60)
        c.combo.setFocus()
        c.combo.lineEdit().selectAll()
        if popup and c.combo.count() and self.window().isVisible():
            c.combo.showPopup()
        return c

    # -- mode Live : vue d'exploitation ------------------------------------------------
    def _live_view(self):
        b = self.b
        self.v.addLayout(section("Actions"))
        names = self.engine.page_names()
        lists = ([("À l'activation", b["actions"]), ("À la désactivation", b["actions_off"])]
                 if b["mode"] == "toggle" else [("Appui", b["actions"]), ("Appui long", b["long_actions"])])
        for title, lst in lists:
            if not lst:
                continue
            text = "<br>".join(f"{i + 1}.&nbsp; {html.escape(summary(a, names))}" for i, a in enumerate(lst))
            lb = wrap(label(f"<span style='color:{T.MUTED}'>{title}</span><br>{text}"))
            lb.setTextFormat(Qt.RichText)
            self.v.addWidget(lb)
        if not b["actions"] and not b["actions_off"] and not b["long_actions"]:
            self.v.addWidget(label("Aucune action.", "Hint"))
        missing = self.engine.unresolved_inputs(b)
        if missing:
            w = wrap(label("⚠  Introuvable dans vMix : " + ", ".join(f"« {m} »" for m in missing), "Hint"))
            w.setStyleSheet(f"color:{T.WARN}")
            self.v.addWidget(w)
        self._integration_rows(b)
        self.v.addLayout(section("Retour d'état"))
        self.v.addWidget(wrap(label(self._auto_text(), "Hint")))

    def _integration_rows(self, b: dict, lay=None):
        used = []
        for a in b["actions"] + b["actions_off"] + b["long_actions"]:
            k = INTEGRATION_OF.get(a.get("type"))
            if k and k not in [u for u, _ in used]:
                used.append((k, a))
        for f in b.get("feedbacks") or []:
            k = INTEGRATION_OF.get((f.get("cond") or {}).get("type"))
            if k and k not in [u for u, _ in used]:
                used.append((k, None))
        for k, a in used:
            (lay or self.v).addWidget(self._integ_status(k, a))

    def _integ_status(self, key: str, a: dict | None) -> QWidget:
        """État en direct d'une intégration + « Tester la connexion » et « Réglages… »."""
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        lb = wrap(label("", "Hint"))
        lb.setTextFormat(Qt.RichText)
        v.addWidget(lb)
        self._integ_lbls.append((lb, key, a))
        result = wrap(label("", "Hint"))
        result.setTextFormat(Qt.RichText)
        result.setTextInteractionFlags(Qt.TextSelectableByMouse)
        result.hide()
        v.addWidget(result)
        row = QHBoxLayout()
        row.setSpacing(6)
        test = QPushButton("Tester la connexion")
        test.setObjectName("Small")
        test.setToolTip("Vérifie tout de suite la connexion avec les réglages enregistrés")
        test.clicked.connect(lambda: self._run_probe(key, test, result))
        row.addWidget(test)
        st = QPushButton("Réglages…")
        st.setObjectName("Ghost")
        st.setToolTip("Réglages → Intégrations")
        st.clicked.connect(lambda: self.main.open_settings("integ"))
        row.addWidget(st)
        row.addStretch()
        v.addLayout(row)
        return box

    def _run_probe(self, key: str, btn: QPushButton, result: QLabel) -> None:
        btn.setEnabled(False)
        result.show()
        result.setText(_dot(None) + "Test en cours…")
        self._probe_lbls[key] = (btn, result)

        def worker():
            try:
                ok, msg = probe(self.engine, key)
            except Exception as e:  # le test ne doit jamais faire tomber l'interface
                ok, msg = False, f"Erreur inattendue : {e}"
            self._probe.done.emit(key, ok, msg)
        threading.Thread(target=worker, name=f"test-{key}", daemon=True).start()

    def _probe_done(self, key: str, ok, msg: str) -> None:
        btn, result = self._probe_lbls.pop(key, (None, None))
        try:
            btn.setEnabled(True)
            result.setText(_dot(ok) + html.escape(msg))
        except (AttributeError, RuntimeError):  # inspecteur reconstruit entre-temps
            pass

    # -- apparence ------------------------------------------------------------
    def _appearance(self):
        b = self.b
        self.v.addLayout(section("Apparence", self._vars_button()))
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
        size = tame_combo(QComboBox())
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

    def _vars_button(self) -> QPushButton:
        """« Variables… » : insère un texte dynamique (titre à l'antenne, temps restant, champ de titre…)."""
        btn = QPushButton("Variables…")
        btn.setObjectName("Ghost")
        btn.setToolTip("Texte qui se met à jour tout seul sur la touche (titre à l'antenne, temps restant…)")
        menu = QMenu(btn)
        menu.aboutToShow.connect(lambda: self._fill_vars(menu))
        btn.setMenu(menu)
        return btn

    def var_tokens(self) -> list[tuple[str, list[tuple[str, str]]]]:
        """[(groupe, [(libellé, jeton)])] proposés par « Variables… »."""
        st = self.engine.vmix.state
        groups = [("vMix", [("Titre à l'antenne", "$(vmix:program_title)"),
                            ("Titre en preview", "$(vmix:preview_title)"),
                            ("Numéro à l'antenne", "$(vmix:program_number)"),
                            ("Numéro en preview", "$(vmix:preview_number)"),
                            ("Durée de l'enregistrement", "$(vmix:recording_duration)"),
                            ("Durée du stream", "$(vmix:streaming_duration)")])]
        refs = []
        for a in (self.b or {}).get("actions", []) + (self.b or {}).get("long_actions", []):
            if a.get("input") and a["input"] not in refs:
                refs.append(a["input"])
        for ref in refs[:3]:
            inp = st.resolve(ref) or {}
            items = [("Titre", f"$(vmix:input_title:{ref})"), ("Temps restant", f"$(vmix:remaining:{ref})"),
                     ("Position", f"$(vmix:position:{ref})"), ("Durée", f"$(vmix:duration:{ref})"),
                     ("Volume", f"$(vmix:volume:{ref})"), ("État", f"$(vmix:input_state:{ref})")]
            items += [(f"Texte « {n} »", f"$(vmix:text:{ref}:{n})") for n in (inp.get("texts") or {})
                      if not str(n).startswith("#")]
            groups.append((f"Input « {ref} »", items))
        groups.append(("Horloge", [("Heure (hh:mm:ss)", "$(time)"), ("Heure (hh:mm)", "$(time:hm)"),
                                   ("Date", "$(date)")]))
        return groups

    def _fill_vars(self, menu: QMenu):
        menu.clear()
        for title, items in self.var_tokens():
            menu.addSection(title)
            for text, tok in items:
                menu.addAction(text, lambda t=tok: self.insert_token(t)).setToolTip(tok)
        st = self.engine.vmix.state
        if st.inputs:
            other = menu.addMenu("Un autre input…")
            for i in st.inputs[:60]:
                sub = other.addMenu(f"{i['number']}  {i['title']}")
                ref = i["title"]
                for text, tok in (("Titre", f"$(vmix:input_title:{ref})"), ("Temps restant", f"$(vmix:remaining:{ref})"),
                                  ("Volume", f"$(vmix:volume:{ref})")):
                    sub.addAction(text, lambda t=tok: self.insert_token(t))
                for n in (i.get("texts") or {}):
                    if not str(n).startswith("#"):
                        sub.addAction(f"Texte « {n} »", lambda t=f"$(vmix:text:{ref}:{n})": self.insert_token(t))

    def insert_token(self, tok: str) -> None:
        self.label_edit.setFocus()
        self.label_edit.insertPlainText(tok)

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

    def _rotary_options(self):
        """Réglages propres aux molettes : pas d'accélération et retour vibratoire."""
        if self.side in ("actions_cw", "actions_ccw"):
            row = QHBoxLayout()
            row.setSpacing(6)
            row.addWidget(label("Déclencher tous les", "Hint"))
            spin = QSpinBox()
            spin.setRange(1, 20)
            spin.setValue(max(1, int(self.b.get("step") or 1)))
            spin.setToolTip("Nombre de crans à tourner avant de déclencher la liste. Monte-le pour ne pas "
                            "inonder vMix sur une rotation rapide ; les crans restants ne sont pas perdus.")
            spin.valueChanged.connect(lambda v: self._set("step", int(v)))
            row.addWidget(spin)
            row.addWidget(label("crans", "Hint"))
            row.addStretch()
            self.v.addLayout(row)
            self.v.addWidget(wrap(label("Dans une action, <b>$(delta)</b> vaut le nombre de crans avec son "
                                        "signe, <b>$(delta_abs)</b> sans le signe.", "Hint")))
        hap = QHBoxLayout()
        hap.setSpacing(6)
        hap.addWidget(label("Vibration à l'appui", "Hint"))
        combo = QComboBox()
        names = [("", "aucune")] + [(k, k) for k in HAPTIC]
        for value, text in names:
            combo.addItem(text, value)
        cur = self.b.get("haptic", "court")
        combo.setCurrentIndex(max(0, [v for v, _ in names].index(cur) if cur in dict(names) else 0))
        combo.currentIndexChanged.connect(lambda i: self._set("haptic", combo.itemData(i)))
        hap.addWidget(combo)
        hap.addStretch()
        self.v.addLayout(hap)

    # -- actions --------------------------------------------------------------
    def _actions(self):
        mode = Seg([("press", "Appui"), ("toggle", "Interrupteur")], self.b["mode"])
        mode.setToolTip("Interrupteur : alterne entre deux séries d'actions et reste allumé tant qu'il est actif")
        mode.changed.connect(self._set_mode)
        self.v.addLayout(section("Actions", mode))
        toggle = self.b["mode"] == "toggle"
        rotary = self.b.get("kind") == "rotary"
        if rotary:
            # Une molette a trois listes : son appui, et un sens de rotation chacun.
            sides = [("actions", "Appui"), ("actions_cw", "Rotation ⟳"), ("actions_ccw", "Rotation ⟲")]
            if self.side not in dict(sides):
                self.side = "actions"
        else:
            if toggle and self.side == "long_actions" or not toggle and self.side == "actions_off":
                self.side = "actions"
            if self.side in ("actions_cw", "actions_ccw"):
                self.side = "actions"
            sides = ([("actions", "À l'activation"), ("actions_off", "À la désactivation")] if toggle
                     else [("actions", "Appui court"), ("long_actions", "Appui long")])
        side = Seg(sides, self.side)
        side.changed.connect(self._set_side)
        if not toggle and not rotary:
            side.buttons["long_actions"].setText("Appui long" + (f"  ·  {len(self.b['long_actions'])}"
                                                                if self.b["long_actions"] else ""))
        self.v.addWidget(side)
        if rotary:
            self._rotary_options()
        confirm = QCheckBox("Sécurité : maintenir ½ s")
        confirm.setToolTip("Évite un appui accidentel (FTB, arrêt du stream…) : il faut maintenir la touche ½ s "
                           "pour déclencher. Une jauge se remplit sur la touche.")
        confirm.setChecked(bool(self.b.get("confirm")))
        confirm.toggled.connect(lambda v: (self._set("confirm", v), self.rebuild()))
        self.v.addWidget(confirm)
        if self.side == "long_actions" and self.b.get("confirm"):
            self.v.addWidget(wrap(label("Avec la sécurité, l'appui long déclenche déjà les actions de l'appui "
                                        "court : cette liste est ignorée.", "Hint")))
        elif not toggle and self.b["long_actions"] and not self.b.get("confirm"):
            self.v.addWidget(wrap(label("Un appui long est défini : l'appui court part au relâchement de la "
                                        "touche.", "Hint")))
        lst = self.b[self.side]
        self.step = min(self.step, max(0, len(lst) - 1))

        if lst:
            self.steps = QListWidget()
            self.steps.setObjectName("Steps")
            self.steps.setDragDropMode(QAbstractItemView.InternalMove)
            self.steps.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self.steps.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
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
                it.setToolTip(summary(lst[idx], names))

    def _step_editor(self, a: dict) -> QWidget:
        frame, lay = _card()
        types = QComboBox()
        types.setMaxVisibleItems(20)
        fill_types(types, a["type"])
        types.currentIndexChanged.connect(lambda i: self._change_type(types.itemData(i)))
        lay.addWidget(types)
        spec = ACTIONS.get(a["type"], {"fields": [], "help": ""})
        if spec.get("help"):
            lay.addWidget(wrap(label(spec["help"], "Hint")))
        integ = INTEGRATION_OF.get(a["type"])
        if integ:
            self._integration_rows({"actions": [a], "actions_off": [], "long_actions": []}, lay)
        f = form()
        for fs in spec["fields"]:
            if a["type"] == "vmix.preview" and fs["key"] == "duration" and (a.get("then") or "none") == "none":
                continue  # durée : seulement utile si le 2e appui envoie à l'antenne
            if a["type"] == "intercom.ptt" and a.get("mode") == "alloff" and fs["key"] in ("bus", "fallback_keys"):
                continue  # « couper tous les bus » : ni bus ni raccourci de secours
            if not shown(fs, a):
                continue  # champ sans objet pour la cible ou le mode choisis
            w = make_field(self.engine, fs, a.get(fs["key"], fs["default"]),
                           lambda v, k=fs["key"]: self._set_field(k, v), self.engine.page_names())
            if fs["kind"] in ("bool", "input", "multiline", "hotkey", "text", "winapp"):
                if fs["kind"] != "bool":
                    f.addRow(label(short_label(fs), "Muted"))
                f.addRow(w)
            else:
                f.addRow(label(short_label(fs), "Muted"), w)
        lay.addLayout(f)
        if a["type"] in ("vmix.preview", "vmix.program") and self.side == "actions" and self.step == 0 \
                and self.b["mode"] != "toggle":
            take = QPushButton("Convertir en Take")
            take.setObjectName("Small")
            take.setToolTip("Take : 1er appui = preview, 2e appui = antenne (même input, même mix)")
            take.clicked.connect(self.convert_to_take)
            lay.addWidget(take, 0, Qt.AlignLeft)
        if a["type"] == "http":
            curl = QPushButton("Coller une commande curl…")
            curl.setObjectName("Small")
            curl.clicked.connect(self._paste_curl)
            lay.addWidget(curl, 0, Qt.AlignLeft)
        return frame

    def convert_to_take(self):
        nb = to_take(self.b)
        if nb:
            self.b = nb
            self.commit(rebuild=True)

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
        if a["type"] == "vmix.adjust" and k == "target":
            # Le pas par défaut dépend de la cible : 2 % pour un volume, 1 input pour la preview…
            a["amount"] = VMIX_STEP.get(v, (1,))[0]
        if k in ("then", "bus", "mode") and a["type"] in ("vmix.preview", "intercom.ptt"):
            self.commit(rebuild=True)  # champs affichés / état d'intégration qui en dépendent
            return
        if any(k in (fs.get("when") or {}) for fs in ACTIONS.get(a["type"], {}).get("fields", [])):
            self.commit(rebuild=True)  # d'autres champs apparaissent ou disparaissent selon celui-ci
            return
        self.commit(coalesce=f"{self.side}.{self.step}.{k}")

    def _paste_curl(self):
        text, ok = QInputDialog.getMultiLineText(self, "Commande curl", "Colle la commande curl :")
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
        self.auto_desc = wrap(label(self._auto_text(), "Hint"))
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
        head.addWidget(wrap(label(spec["label"])), 1)
        rm = QPushButton("✕")
        rm.setObjectName("Ghost")
        rm.setToolTip("Supprimer ce retour d'état")
        rm.clicked.connect(lambda: self._remove_fb(i))
        head.addWidget(rm)
        lay.addLayout(head)
        fl = form()
        for fs in spec["fields"]:
            if not shown(fs, cond):
                continue
            w = make_field(self.engine, fs, cond.get(fs["key"], fs["default"]),
                           lambda v, k=fs["key"]: self._set_cond(i, k, v))
            if fs["kind"] in ("input", "multiline", "text"):  # texte : toute la largeur (URL, chemin JSON…)
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
        cond = self.b["feedbacks"][i]["cond"]
        cond[k] = v
        fields = CONDITIONS.get(cond.get("type"), {}).get("fields", [])
        if any(k in (fs.get("when") or {}) for fs in fields):
            self.commit(rebuild=True)  # un champ dépendant apparaît ou disparaît
            return
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
