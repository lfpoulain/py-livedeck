"""Le Stream Deck à l'écran : miroir en direct, sélection, glisser-déposer."""
from __future__ import annotations

import html
import json
import time

from PySide6.QtCore import QEvent, QMimeData, QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QDrag, QFont, QFontMetricsF, QImage, QLinearGradient, QPainter,
                           QPainterPath, QPen, QPixmap)
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QToolTip, QVBoxLayout, QWidget

from .. import loupedeck as ld
from ..actions import INTEGRATION_OF, next_press, summary
from ..model import control_key, key, parse_control, parse_key, surface_of
from ..render import render
from . import theme as T

MIME_CELL = "application/x-livedeck-cell"
MIME_INPUTS = "application/x-livedeck-inputs"
MIME_PRESET = "application/x-livedeck-preset"

MODEL_NAMES = {(8, 4): "STREAM DECK XL", (5, 3): "STREAM DECK MK.2", (4, 2): "STREAM DECK +",
               (3, 2): "STREAM DECK MINI", (9, 4): "STREAM DECK + XL", (16, 2): "STREAM DECK STUDIO",
               (3, 1): "STREAM DECK PEDAL"}
GAP = 0.16  # écart entre touches, en fraction de touche

# -- proportions réelles du Loupedeck CT, en largeur de touche -----------------------------------
# Déduites de la géométrie des écrans de l'appareil : la dalle avant fait 480×270 px pour
# 4×3 touches de 90 px, et l'écran de la molette 240×240 px. Une touche valant 1, la molette
# fait donc 240/90 = 2,67 — elle est énorme, et c'est ce qui donne sa silhouette au CT.
# Boîtier 150 × 160 mm (specs Loupedeck) : presque carré, un peu plus haut que large.
L_KEY_GAP = 0.07      # la dalle est continue : un simple filet sépare les touches
L_STRIP_W = 0.667     # bandeaux latéraux : 60 px
L_KNOB_D = 0.95       # molettes ~20 mm, comme une touche
L_KNOB_GAP, L_EDGE = 0.06, 0.12
L_ROUND_D = 0.58      # les 8 boutons ronds et les 12 du bas
L_WHEEL_D = 2.667     # écran de la molette : 240 px
L_ROW_GAP, L_WHEEL_GAP = 0.45, 0.42
L_TOP, L_BOTTOM = 0.35, 0.65
BTN_GAP, BLOCK_GAP = 0.12, 0.22
SIDE = 1.35           # colonne d'état de LiveDeck : à CÔTÉ de l'appareil, pas dedans
# Bas de l'appareil : molette centrale au milieu, encadrée de deux blocs de 6 boutons ronds
# (2 colonnes × 3 rangées chacun). L'ordre des identifiants du firmware se scinde justement en
# deux groupes de six, colonne par colonne : home…fnL à gauche, a…e à droite.
BOTTOM_L = ("home", "enter", "undo", "save", "keyboard", "fnL")   # lu par rangées
BOTTOM_R = ("a", "b", "c", "d", "fnR", "e")


class ControlBar(QWidget):
    """Contrôles hors grille d'un Loupedeck (molettes, bandeaux, boutons physiques).

    Ils ne tiennent pas dans la grille du deck : on les présente en pastilles, groupées, sous
    la vue. Une pastille se sélectionne comme une touche et s'édite dans le même inspecteur."""

    selected = Signal(str)

    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.page_id: str | None = None
        self.sel: str | None = None
        self._chips: dict[str, QPushButton] = {}
        self.v = QVBoxLayout(self)
        self.v.setContentsMargins(0, 2, 0, 0)
        self.v.setSpacing(4)
        self.hide()

    def profile(self):
        """Modèle à présenter : celui d'un Loupedeck branché, sinon le plus complet (CT) dès
        qu'une page utilise des contrôles."""
        for d in (self.engine.decks.info() if self.engine.decks else []):
            for pid, prof in ld.PROFILES.items():
                if prof.name == d["model"]:
                    return prof
        p = self.engine.page(self.page_id)
        return ld.PROFILES[0x0007] if p and p.get("controls") else None

    def on_surface(self) -> bool:
        """La vue du deck dessine-t-elle déjà la surface Loupedeck de cette page ?"""
        p = self.engine.page(self.page_id)
        if not p:
            return False
        if p.get("surface"):
            return p["surface"] == "loupedeck"
        return any(d["page"] == self.page_id and surface_of(d["model"]) == "loupedeck"
                   for d in (self.engine.decks.info() if self.engine.decks else []))

    def set_page(self, page_id: str | None) -> None:
        self.page_id = page_id
        self.refresh()

    def select(self, k: str | None) -> None:
        self.sel = k
        for cid, chip in self._chips.items():
            chip.setChecked(k == control_key(cid))

    def refresh(self) -> None:
        prof = self.profile()
        if prof is None or self.on_surface():
            # Quand la vue dessine la surface Loupedeck, les contrôles y sont déjà : une seconde
            # liste de pastilles en dessous ne ferait que dupliquer et brouiller.
            self.hide()
            return
        wanted = [c for c in ld.controls(prof)]
        sig = (prof.name, tuple(c["id"] for c in wanted))
        if sig != getattr(self, "_sig", None):
            self._sig = sig
            self._rebuild(prof, wanted)
        page = self.engine.page(self.page_id)
        controls = (page or {}).get("controls") or {}
        for cid, chip in self._chips.items():
            used = cid in controls
            chip.setProperty("used", "1" if used else "0")
            lab = ld.CONTROL_LABELS.get(cid, cid)
            el = controls.get(cid)
            text = (el.get("label") or "").split("\n")[0].strip() if el else ""
            chip.setText(text or lab)
            chip.setToolTip(f"{lab}{' · ' + text if text else ''}"
                            + ("\nMolette : rotation dans les deux sens et appui"
                               if ld.control_kind(cid) == "rotary" else "")
                            + ("" if used else "\nLibre : clique pour lui donner une action"))
            chip.style().unpolish(chip)
            chip.style().polish(chip)
        self.select(self.sel)
        self.show()

    def _rebuild(self, prof, wanted) -> None:
        while self.v.count():
            it = self.v.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
            elif it.layout():
                while it.layout().count():
                    sub = it.layout().takeAt(0)
                    if sub.widget():
                        sub.widget().deleteLater()
                it.layout().deleteLater()
        self._chips = {}
        groups: dict[str, list] = {}
        for c in wanted:
            groups.setdefault(c["group"], []).append(c)
        for name, items in groups.items():
            row = QHBoxLayout()
            row.setSpacing(4)
            head = QLabel(name.upper())
            head.setObjectName("H2")
            head.setFixedWidth(96)
            row.addWidget(head)
            for c in items:
                chip = QPushButton("")
                chip.setObjectName("Chip")
                chip.setCheckable(True)
                chip.setCursor(Qt.PointingHandCursor)
                chip.clicked.connect(lambda _=False, cid=c["id"]: self.selected.emit(control_key(cid)))
                row.addWidget(chip)
                self._chips[c["id"]] = chip
            row.addStretch()
            self.v.addLayout(row)


def pil_to_pixmap(img, dpr: float = 1.0) -> QPixmap:
    qimg = QImage(img.tobytes(), img.width, img.height, img.width * 3, QImage.Format_RGB888).copy()
    pm = QPixmap.fromImage(qimg)
    pm.setDevicePixelRatio(dpr)
    return pm


class DeckGrid(QWidget):
    selected = Signal(str)
    pressed = Signal(str, bool)
    moved = Signal(str, str, bool)
    inputs_dropped = Signal(str, list)
    preset_dropped = Signal(str, str)
    context = Signal(str, QPoint)
    test_key = Signal(str)  # Entrée sur la touche sélectionnée
    blocked = Signal()  # un clic a été refusé : le verrou est actif

    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.page_id: str | None = None
        self.sel: str | None = None
        self.live = False
        self.locked = False  # verrou : ni sélection, ni appui, ni glisser-déposer à la souris
        self._hover: str | None = None
        self._drop: str | None = None
        self._press_pos = None
        self._live_down: str | None = None
        self._pix: dict = {}
        self._flash: dict[str, float] = {}
        self._anim = QTimer(self, interval=30, timeout=self._tick)
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumSize(480, 300)

    # -- géométrie ------------------------------------------------------------
    @property
    def page(self):
        return self.engine.page(self.page_id)

    @property
    def surface(self) -> str:
        """« loupedeck » ou « streamdeck ». La page peut l'imposer ; sinon on le déduit de
        l'appareil qui l'affiche, pour que brancher un CT suffise à changer la vue."""
        p = self.page
        if not p:
            return "streamdeck"
        if p.get("surface"):
            return p["surface"]
        for d in (self.engine.decks.info() if self.engine.decks else []):
            if d["page"] == self.page_id:
                return surface_of(d["model"])
        return "streamdeck"

    def device_grid(self) -> tuple[int, int]:
        """Grille réellement affichée par l'appareil. Un Loupedeck a un pavé fixe (4×3 sur un CT) :
        si la page est plus grande, l'appareil n'en montre que ce qui tient, et la vue doit montrer
        la même chose — sinon on dessine une colonne qui n'existe pas."""
        p = self.page
        cols, rows = (p["cols"], p["rows"]) if p else (8, 4)
        if self.surface != "loupedeck":
            return cols, rows
        prof = ld.PROFILES[0x0007]
        for d in (self.engine.decks.info() if self.engine.decks else []):
            if d["page"] == self.page_id and surface_of(d["model"]) == "loupedeck":
                prof = next((pr for pr in ld.PROFILES.values() if pr.name == d["model"]), prof)
                break
        return min(cols, prof.cols), min(rows, prof.rows)

    def hidden_keys(self) -> int:
        """Touches de la page que l'appareil ne peut pas afficher (page plus grande que son pavé)."""
        p = self.page
        if not p or self.surface != "loupedeck":
            return 0
        cols, rows = self.device_grid()
        return sum(1 for k in p["buttons"] if not (lambda rc: rc[0] < rows and rc[1] < cols)(parse_key(k)))

    def _geom(self):
        """(colonnes, rangées, côté d'une touche, écart, x0, y0, boîtier).

        x0/y0 = coin du pavé de touches ; `body` = l'appareil dessiné. La colonne d'état de
        LiveDeck est réservée en plus, à droite : elle ne fait pas partie de l'appareil et ne
        doit donc pas en déformer les proportions."""
        cols, rows = self.device_grid()
        if self.surface == "loupedeck":
            return self._geom_loupedeck(cols, rows)
        pad, foot = 0.42, 0.5  # marges du boîtier, pied (en touches)
        wu = cols + (cols - 1) * GAP + 2 * pad + SIDE
        hu = rows + (rows - 1) * GAP + pad + foot
        cell = min((self.width() - 8) / wu, (self.height() - 8) / hu, 138)
        body = QRectF((self.width() - wu * cell) / 2, (self.height() - hu * cell) / 2, wu * cell, hu * cell)
        return cols, rows, cell, cell * GAP, body.x() + pad * cell, body.y() + pad * cell, body

    def _geom_loupedeck(self, cols: int, rows: int):
        gap = L_KEY_GAP
        panel_w = 2 * L_STRIP_W + cols + (cols - 1) * gap          # dalle avant : bandeaux + touches
        side_w = L_EDGE + L_KNOB_D + L_KNOB_GAP                    # colonne de molettes, chaque côté
        wu_dev = 2 * side_w + panel_w
        hu_dev = (L_TOP + rows + (rows - 1) * gap + L_ROW_GAP + L_ROUND_D
                  + L_WHEEL_GAP + L_WHEEL_D + L_BOTTOM)
        cell = min((self.width() - 8) / (wu_dev + SIDE), (self.height() - 8) / hu_dev, 138)
        total_w = (wu_dev + SIDE) * cell
        body = QRectF((self.width() - total_w) / 2, (self.height() - hu_dev * cell) / 2,
                      wu_dev * cell, hu_dev * cell)
        x0 = body.x() + (side_w + L_STRIP_W) * cell
        y0 = body.y() + L_TOP * cell
        return cols, rows, cell, cell * gap, x0, y0, body

    def _cell_rect(self, r, c, g=None) -> QRectF:
        cols, rows, cell, gap, x0, y0, _ = g or self._geom()
        return QRectF(x0 + c * (cell + gap), y0 + r * (cell + gap), cell, cell)

    def _layout(self, g=None) -> dict:
        """Toutes les zones cliquables : {case: (rectangle, nature)}.

        Une seule source de vérité pour le dessin, le survol, le pointage et le glisser-déposer :
        si la géométrie change, les deux suivent."""
        g = g or self._geom()
        cols, rows, cell, gap, x0, y0, body = g
        out = {key(r, c): (self._cell_rect(r, c, g), "key") for r in range(rows) for c in range(cols)}
        if self.surface != "loupedeck":
            return out
        keys_w = cols * cell + (cols - 1) * gap
        keys_h = rows * cell + (rows - 1) * gap

        # Bandeaux latéraux : trois zones chacun, collés au pavé (même dalle que les touches).
        for side, sx in (("L", x0 - L_STRIP_W * cell), ("R", x0 + keys_w)):
            zh = keys_h / ld.STRIP_ZONES
            for z in range(ld.STRIP_ZONES):
                out[control_key(f"strip{side}{z + 1}")] = (
                    QRectF(sx, y0 + z * zh + 1, L_STRIP_W * cell - 1, zh - 2), "strip")

        # Molettes : une par rangée de touches, de chaque côté, au bord du boîtier.
        d = L_KNOB_D * cell
        for side, kx in (("L", x0 - (L_STRIP_W + L_KNOB_GAP + L_KNOB_D) * cell),
                         ("R", x0 + keys_w + (L_STRIP_W + L_KNOB_GAP) * cell)):
            for i, pos in enumerate(("T", "C", "B")):
                cy = y0 + i * (cell + gap) + cell / 2
                out[control_key(f"knob{pos}{side}")] = (QRectF(kx, cy - d / 2, d, d), "knob")

        # Les 8 boutons ronds : alignés sous toute la dalle, bandeaux compris (ils sont plus larges
        # que le seul pavé de touches, sinon ils ne tiendraient pas côte à côte).
        ry = y0 + keys_h + L_ROW_GAP * cell
        rd = L_ROUND_D * cell
        row_x, row_w = x0 - L_STRIP_W * cell, keys_w + 2 * L_STRIP_W * cell
        step = row_w / 8
        for i in range(8):
            out[control_key(f"key{i}")] = (QRectF(row_x + i * step + (step - rd) / 2, ry, rd, rd), "round")

        # Dernière bande : la grande molette au milieu, un bloc de 6 boutons de chaque côté.
        by = ry + rd + L_WHEEL_GAP * cell
        wd = L_WHEEL_D * cell
        cx = x0 + keys_w / 2
        out[control_key("wheel")] = (QRectF(cx - wd / 2, by, wd, wd), "wheel")
        bd, bg = L_ROUND_D * cell, BTN_GAP * cell
        block_w = 2 * bd + bg
        block_h = 3 * bd + 2 * bg
        for names, left in ((BOTTOM_L, True), (BOTTOM_R, False)):
            bx = (cx - wd / 2 - BLOCK_GAP * cell - block_w) if left else (cx + wd / 2 + BLOCK_GAP * cell)
            for i, cid in enumerate(names):
                out[control_key(cid)] = (QRectF(bx + (i % 2) * (bd + bg),
                                                by + (wd - block_h) / 2 + (i // 2) * (bd + bg),
                                                bd, bd), "round")
        return out

    def draws(self, k: str) -> bool:
        """La vue dessine-t-elle cette case ? (les contrôles n'apparaissent qu'en surface Loupedeck)"""
        return bool(k) and k in self._layout()

    def key_at(self, pos) -> str | None:
        for k, (rect, kind) in self._layout().items():
            if kind in ("knob", "round", "wheel"):
                c = rect.center()
                if (pos.x() - c.x()) ** 2 + (pos.y() - c.y()) ** 2 <= (rect.width() / 2) ** 2:
                    return k
            elif rect.contains(pos):
                return k
        return None

    # -- état -----------------------------------------------------------------
    def set_page(self, page_id: str) -> None:
        if page_id != self.page_id:
            self.page_id = page_id
            self.sel = None
        self.update()

    def set_live(self, live: bool) -> None:
        self.live = live
        self.update()

    def set_locked(self, locked: bool) -> None:
        self.locked = locked
        self.setCursor(Qt.ArrowCursor)
        self.update()

    def select(self, k: str | None, emit: bool = True) -> None:
        self.sel = k
        self.update()
        if emit:
            self.selected.emit(k or "")

    def flash(self, k: str) -> None:
        self._flash[k] = time.monotonic()
        if not self._anim.isActive():
            self._anim.start()

    def _tick(self):
        now = time.monotonic()
        self._flash = {k: t for k, t in self._flash.items() if now - t < 0.6}
        if not self._flash:
            self._anim.stop()
        self.update()

    # -- dessin ---------------------------------------------------------------
    def _font(self, size: float, bold=False, spacing=0.0) -> QFont:
        f = QFont(self.font())
        f.setPointSizeF(max(5.0, size))
        f.setBold(bold)
        if spacing:
            f.setLetterSpacing(QFont.AbsoluteSpacing, spacing)
        return f

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        page = self.page
        if not page:
            return
        g = self._geom()
        cols, rows, cell, gap, x0, y0, body = g
        self._paint_body(p, body, cell, page)

        dpr = self.devicePixelRatioF()
        px = max(int(cell * dpr), 72)  # rendu à la taille du vrai deck, puis réduit : même mise en page
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        radius = cell * 0.13
        controls = page.get("controls") or {}
        for k, (rect, kind) in self._layout(g).items():
            if kind == "key":
                b = page["buttons"].get(k)
                if b:
                    self._paint_key(p, k, rect, radius, px, dpr, b)
                else:
                    self._paint_empty(p, k, rect, radius, cell)
            else:
                self._paint_control(p, k, rect, kind, cell, controls.get(parse_control(k)))
        sx = (body.right() + 0.14 * cell if self.surface == "loupedeck"
              else x0 + cols * (cell + gap))
        self._paint_status(p, body, cell, sx, y0, rows * cell + (rows - 1) * gap)

    def _paint_control(self, p: QPainter, k: str, rect: QRectF, kind: str, cell: float, el: dict | None):
        """Molette, zone de bandeau, molette centrale ou bouton physique.

        Un contrôle configuré prend ses couleurs et son libellé ; sinon il reste en creux, pour
        qu'on voie d'un coup d'œil ce qui est libre sur l'appareil."""
        v = self.engine.visual(el) if el else None
        round_ = kind in ("knob", "round", "wheel")
        bg = QColor(v["bg"]) if v and v.get("bg") else QColor("#171b22")
        if v and v.get("lit"):
            self._glow(p, rect, QColor(v["lit"]), rect.width() / 2 if round_ else cell * 0.08)
        hover = k == self._hover and not self.live and not self.locked
        p.setBrush(bg)
        p.setPen(QPen(QColor("#394252" if el else "#262c37"), 1.2))
        if round_:
            p.drawEllipse(rect)
            if kind == "knob":  # repère de rotation, pour distinguer une molette d'un bouton
                p.setPen(QPen(QColor(T.MUTED if el else "#2f3542"), max(1.2, cell * 0.02)))
                p.drawLine(rect.center().x(), rect.top() + cell * 0.06,
                           rect.center().x(), rect.top() + cell * 0.17)
        else:
            p.drawRoundedRect(rect, cell * 0.07, cell * 0.07)
        text = ""
        if el:
            text = (el.get("label") or "").replace("\n", " ").strip()
            if v and v.get("label"):
                text = v["label"].replace("\n", " ").strip()
        if not text:
            text = "+" if hover else ld.short_label(parse_control(k) or "")
        p.setPen(QColor(v["fg"]) if v and v.get("fg") else QColor(T.FAINT if hover else "#59616f"))
        size = cell * (0.13 if kind == "wheel" else 0.095)
        p.setFont(self._font(size))
        p.drawText(rect.adjusted(2, 1, -2, -1), Qt.AlignCenter | Qt.TextWordWrap,
                   QFontMetricsF(self._font(size)).elidedText(text, Qt.ElideRight, rect.width() * 1.9))
        pen = self._outline(k)
        if pen:
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            if round_:
                p.drawEllipse(rect.adjusted(-2.5, -2.5, 2.5, 2.5))
            else:
                p.drawRoundedRect(rect.adjusted(-2.5, -2.5, 2.5, 2.5), cell * 0.09, cell * 0.09)

    def _paint_body(self, p: QPainter, body: QRectF, cell: float, page: dict):
        grad = QLinearGradient(body.topLeft(), body.bottomLeft())
        grad.setColorAt(0, QColor("#1c2029"))
        grad.setColorAt(1, QColor("#121419"))
        red = self.live or self.locked
        p.setPen(QPen(QColor("#7f1d1d") if red else QColor("#2b313d"), 1.6 if red else 1.2))
        p.setBrush(grad)
        p.drawRoundedRect(body, cell * 0.24, cell * 0.24)
        # pied : modèle et deck(s) qui affichent la page
        foot_y = body.bottom() - cell * 0.44
        p.setFont(self._font(cell * 0.1, True, cell * 0.035))
        p.setPen(QColor("#2f3542"))
        name = ("LOUPEDECK CT" if self.surface == "loupedeck"
                else MODEL_NAMES.get((page["cols"], page["rows"]), "STREAM DECK"))
        p.drawText(QRectF(body.x() + cell * 0.42, foot_y, body.width() / 2, cell * 0.3), Qt.AlignLeft | Qt.AlignVCenter, name)
        decks = [d for d in (self.engine.decks.info() if self.engine.decks else []) if d["page"] == self.page_id]
        text = ("SUR  " + "  ·  ".join(d["model"].replace("Stream Deck ", "").upper() for d in decks)) if decks \
            else ("LIVE  ·  CLIC = APPUI" if self.live else "AFFICHÉE SUR AUCUN DECK")
        if self.locked:
            text = "VERROUILLÉ  ·  " + text
        hidden = self.hidden_keys()
        if hidden:  # page plus grande que le pavé de l'appareil : il n'en affiche qu'une partie
            cols, rows = self.device_grid()
            text = f"{hidden} TOUCHE{'S' if hidden > 1 else ''} HORS DU PAVÉ {cols}×{rows}  ·  " + text
        p.setPen(QColor("#b91c1c" if hidden else
                        (T.ACCENT if decks and not self.locked else ("#b91c1c" if red else "#3c4352"))))
        p.setFont(self._font(cell * 0.09, True, cell * 0.02))
        p.drawText(QRectF(body.center().x(), foot_y, body.width() / 2 - cell * 0.42, cell * 0.3),
                   Qt.AlignRight | Qt.AlignVCenter, text)

    def _paint_status(self, p: QPainter, body: QRectF, cell: float, x: float, y: float, h: float):
        """Colonne d'état : voyants + PGM / PVW, comme la colonne MAIN de MixPanel."""
        st = self.engine.vmix.state
        w = cell * 1.05
        panel = QRectF(x + cell * 0.12, y, w, h)
        p.setPen(QPen(QColor("#262b35"), 1))
        p.setBrush(QColor("#0f1115"))
        p.drawRoundedRect(panel, cell * 0.09, cell * 0.09)
        decks = self.engine.decks.info() if self.engine.decks else []
        leds = [("DECK", T.ACCENT, bool(decks)), ("VMIX", T.OK, st.connected),
                ("REC", T.PGM, st.connected and st.flags.get("recording")),
                ("STREAM", T.PGM, st.connected and st.flags.get("streaming"))]
        ly = panel.y() + cell * 0.14
        step = min(cell * 0.26, h * 0.1)
        p.setFont(self._font(cell * 0.095, True, cell * 0.008))
        for name, col, on in leds:
            c = QColor(col) if on else QColor("#2a303b")
            if on:
                halo = QColor(col)
                halo.setAlphaF(0.25)
                p.setPen(Qt.NoPen)
                p.setBrush(halo)
                p.drawEllipse(QPointF(panel.x() + cell * 0.16, ly), cell * 0.075, cell * 0.075)
            p.setPen(Qt.NoPen)
            p.setBrush(c)
            p.drawEllipse(QPointF(panel.x() + cell * 0.16, ly), cell * 0.042, cell * 0.042)
            p.setPen(QColor(T.TEXT) if on else QColor("#4a5263"))
            p.drawText(QRectF(panel.x() + cell * 0.28, ly - cell * 0.1, w - cell * 0.3, cell * 0.2),
                       Qt.AlignLeft | Qt.AlignVCenter, name)
            ly += step
        # PGM / PVW
        box_h = min(cell * 0.78, (panel.bottom() - ly - cell * 0.2) / 2)
        by = panel.bottom() - 2 * box_h - cell * 0.18
        for tag, col, num in (("PGM", T.PGM, st.program()), ("PVW", T.PVW, st.preview())):
            r = QRectF(panel.x() + cell * 0.1, by, w - cell * 0.2, box_h)
            online = st.connected and num
            p.setPen(QPen(QColor(col), 1.2) if online else QPen(QColor("#2a303b"), 1))
            p.setBrush(QColor(col).darker(320) if online else Qt.NoBrush)
            p.drawRoundedRect(r, cell * 0.06, cell * 0.06)
            p.setPen(QColor(col) if online else QColor("#4a5263"))
            p.setFont(self._font(cell * 0.085, True, cell * 0.012))
            p.drawText(QRectF(r.x(), r.y() + box_h * 0.05, r.width(), box_h * 0.28), Qt.AlignCenter, tag)
            p.setFont(self._font(cell * 0.17, True))
            p.setPen(QColor(T.TEXT) if online else QColor("#4a5263"))
            p.drawText(QRectF(r.x(), r.y() + box_h * 0.28, r.width(), box_h * 0.4), Qt.AlignCenter,
                       str(num) if online else "–")
            if online:
                title = (st._by_number.get(num) or {}).get("title", "")
                f = self._font(cell * 0.075)
                p.setFont(f)
                p.setPen(QColor(T.MUTED))
                tw = r.width() - cell * 0.08
                p.drawText(QRectF(r.x() + cell * 0.04, r.y() + box_h * 0.66, tw, box_h * 0.3), Qt.AlignCenter,
                           QFontMetricsF(f).elidedText(title, Qt.ElideRight, tw))
            by += box_h + cell * 0.08

    def _outline(self, k: str) -> QPen | None:
        if k == self.sel and not self.live:
            return QPen(QColor("#ffffff"), 2)
        if k in self._flash:
            a = max(0.0, 1 - (time.monotonic() - self._flash[k]) / 0.6)
            c = QColor(T.ACCENT)
            c.setAlphaF(0.3 + 0.7 * a)
            return QPen(c, 2.2)
        if k == self._hover:
            return QPen(QColor("#4b5568"), 1.4)
        return None

    def _glow(self, p: QPainter, rect: QRectF, color: QColor, radius: float):
        for k, a in ((7, 0.05), (4.5, 0.09), (2.2, 0.16)):
            c = QColor(color)
            c.setAlphaF(a)
            p.setPen(Qt.NoPen)
            p.setBrush(c)
            p.drawRoundedRect(rect.adjusted(-k, -k, k, k), radius + k, radius + k)

    def _paint_key(self, p, k, rect, radius, px, dpr, b):
        v = self.engine.visual(b)
        if v.get("lit"):
            self._glow(p, rect, QColor(v["lit"]), radius)
        sig = (tuple(sorted(v.items())), px)
        pm = self._pix.get(sig)
        if pm is None:
            if len(self._pix) > 800:
                self._pix.clear()
            pm = self._pix[sig] = pil_to_pixmap(render(v, px), dpr)
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        p.save()
        p.setClipPath(path)
        p.drawPixmap(rect.toRect(), pm)
        p.restore()
        pen = self._outline(k)
        if k == self._drop:
            self._paint_drop(p, rect, radius)
        elif pen:
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(rect.adjusted(-2.5, -2.5, 2.5, 2.5), radius + 2, radius + 2)

    def _paint_empty(self, p, k, rect, radius, cell):
        p.setPen(QPen(QColor("#2a303b"), 1))
        p.setBrush(QColor("#181b22"))
        p.drawRoundedRect(rect, radius, radius)
        hover = k == self._hover and not self.live and not self.locked
        p.setPen(QColor(T.FAINT) if hover else QColor("#3a4150"))
        p.setFont(self._font(cell * (0.2 if hover else 0.12)))
        p.drawText(rect, Qt.AlignCenter, "+" if hover else "·")
        if k == self._drop:
            self._paint_drop(p, rect, radius)
        else:
            pen = self._outline(k)
            if pen:
                p.setPen(pen)
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(rect.adjusted(-2.5, -2.5, 2.5, 2.5), radius + 2, radius + 2)

    def _paint_drop(self, p, rect, radius):
        p.setPen(QPen(QColor(T.ACCENT), 1.6, Qt.DashLine))
        p.setBrush(QColor(45, 212, 191, 30))
        p.drawRoundedRect(rect.adjusted(-3, -3, 3, 3), radius + 3, radius + 3)

    # -- info-bulles ---------------------------------------------------------
    def tooltip_text(self, k: str) -> str:
        """Ce que fait la touche, ce que fera le prochain appui et ce qui cloche (input introuvable…)."""
        page = self.page
        cid = parse_control(k)
        b = self.engine.resolve(page, k) if page else None
        where = (ld.CONTROL_LABELS.get(cid, cid) if cid is not None
                 else "Rangée {}, colonne {}".format(*(n + 1 for n in parse_key(k))))
        if not b:
            hint = "" if self.live else "  ·  clic pour choisir une action"
            if cid is not None and ld.control_kind(cid) == "rotary":
                hint += "  ·  rotation dans les deux sens et appui"
            return f"{where} : vide{hint}"
        e = self.engine
        names = e.page_names()
        title = (b.get("label") or "").replace("\n", " ").strip() or "Sans texte"
        lines = [f"<b>{html.escape(title)}</b>"]
        acts = b.get("actions") or []
        if cid is not None:
            lines.append(f"<span style='color:{T.MUTED}'>{html.escape(where)}</span>")
        if b.get("mode") == "toggle":
            lines.append(f"<span style='color:{T.MUTED}'>Interrupteur</span>")
        for lst, how in (("actions_cw", "Rotation ⟳"), ("actions_ccw", "Rotation ⟲")):
            if b.get(lst):
                lines.append(f"{how} : {html.escape(summary(b[lst][0], names))}"
                             + (f"  (tous les {b['step']} crans)" if int(b.get("step") or 1) > 1 else ""))
        lines += [f"▸ {html.escape(summary(a, names))}" for a in acts[:5]]
        if len(acts) > 5:
            lines.append(f"▸ … {len(acts) - 5} autres étapes")
        if b.get("long_actions"):
            lines.append(f"Appui long : {html.escape(summary(b['long_actions'][0], names))}")
        if b.get("confirm"):
            lines.append("Sécurité : maintenir ½ s")
        nxt = next_press(e, b)
        if nxt:
            lines.append(f"<span style='color:{T.ACCENT}'>Prochain appui : {html.escape(nxt)}</span>")
        for m in e.unresolved_inputs(b):
            lines.append(f"<span style='color:{T.WARN}'>● Input « {html.escape(m)} » introuvable dans vMix</span>")
        for a in acts:
            if INTEGRATION_OF.get(a.get("type")) == "intercom" and a.get("mode") != "alloff":
                why = e.intercom.unavailable(a.get("bus") or "regie")
                if why:
                    lines.append(f"<span style='color:{T.DANGER}'>py-intercom : {why}"
                                 + (f" ({html.escape(e.intercom.error)})" if e.intercom.error else "") + "</span>")
                break
        return "<br>".join(lines)

    def event(self, e):
        if e.type() == QEvent.ToolTip:
            k = self.key_at(QPointF(e.pos()))
            if k and self.page:
                QToolTip.showText(e.globalPos(), self.tooltip_text(k), self)
            else:
                QToolTip.hideText()
                e.ignore()
            return True
        return super().event(e)

    # -- souris ---------------------------------------------------------------
    def mousePressEvent(self, e):
        if self.locked:
            self.blocked.emit()
            return
        k = self.key_at(e.position())
        if e.button() == Qt.RightButton:
            if k:
                self.select(k)
                self.context.emit(k, e.globalPosition().toPoint())
            return
        if e.button() != Qt.LeftButton:
            return
        if not k:
            if not self.live:
                self.select(None)
            return
        if self.live:
            self._live_down = k
            self.pressed.emit(k, True)
            return
        self.select(k)
        self._press_pos = e.position()

    def mouseReleaseEvent(self, e):
        if self._live_down:
            self.pressed.emit(self._live_down, False)
            self._live_down = None
        self._press_pos = None

    def mouseMoveEvent(self, e):
        k = self.key_at(e.position())
        if k != self._hover:
            self._hover = k
            self.setCursor(Qt.PointingHandCursor if k and not self.locked else Qt.ArrowCursor)
            self.update()
        page = self.page
        if (self._press_pos is not None and not self.live and self.sel and page
                and self.sel in page["buttons"]
                and (e.position() - self._press_pos).manhattanLength() > 8):
            src = self.sel
            self._press_pos = None
            drag = QDrag(self)
            md = QMimeData()
            md.setData(MIME_CELL, src.encode())
            drag.setMimeData(md)
            v = self.engine.visual(page["buttons"][src])
            drag.setPixmap(pil_to_pixmap(render(v, 64)))
            drag.setHotSpot(QPoint(32, 32))
            drag.exec(Qt.MoveAction | Qt.CopyAction, Qt.MoveAction)

    def leaveEvent(self, _):
        self._hover = None
        self.update()

    # -- glisser-déposer ------------------------------------------------------
    def dragEnterEvent(self, e):
        if not self.live and not self.locked and any(e.mimeData().hasFormat(m) for m in (MIME_CELL, MIME_INPUTS, MIME_PRESET)):
            e.acceptProposedAction()

    @staticmethod
    def _accepts(k: str | None, md) -> bool:
        """Un contrôle est lié à un organe physique : on n'y déplace pas une touche et on n'y
        dépose pas une rangée d'inputs (qui s'étale sur la grille). Un preset, si."""
        if not k:
            return False
        return parse_control(k) is None or md.hasFormat(MIME_PRESET)

    def dragMoveEvent(self, e):
        k = self.key_at(e.position())
        k = k if self._accepts(k, e.mimeData()) else None
        if k != self._drop:
            self._drop = k
            self.update()
        if not k:
            e.ignore()
        elif e.mimeData().hasFormat(MIME_CELL) and e.modifiers() & Qt.ControlModifier:
            e.setDropAction(Qt.CopyAction)
            e.accept()
        else:
            e.acceptProposedAction()

    def dragLeaveEvent(self, _):
        self._drop = None
        self.update()

    def dropEvent(self, e):
        k = self.key_at(e.position())
        self._drop = None
        self.update()
        md = e.mimeData()
        if not self._accepts(k, md):
            return
        if md.hasFormat(MIME_CELL):
            src = bytes(md.data(MIME_CELL)).decode()
            if src != k:
                self.moved.emit(src, k, bool(e.modifiers() & Qt.ControlModifier))
        elif md.hasFormat(MIME_INPUTS):
            self.inputs_dropped.emit(k, json.loads(bytes(md.data(MIME_INPUTS)).decode()))
        elif md.hasFormat(MIME_PRESET):
            self.preset_dropped.emit(k, bytes(md.data(MIME_PRESET)).decode())
        self.select(k)
        e.acceptProposedAction()

    # -- clavier --------------------------------------------------------------
    def keyPressEvent(self, e):
        page = self.page
        if not page or self.live or self.locked:
            return super().keyPressEvent(e)
        moves = {Qt.Key_Left: (0, -1), Qt.Key_Right: (0, 1), Qt.Key_Up: (-1, 0), Qt.Key_Down: (1, 0)}
        if e.key() in moves:
            # Les flèches parcourent la grille ; depuis un contrôle hors grille on y revient.
            r, c = parse_key(self.sel) if self.sel and parse_control(self.sel) is None else (0, -1)
            dr, dc = moves[e.key()]
            self.select(key(max(0, min(page["rows"] - 1, r + dr)), max(0, min(page["cols"] - 1, c + dc))))
            return
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and self.sel and not e.isAutoRepeat():
            self.test_key.emit(self.sel)
            return
        super().keyPressEvent(e)

    def next_key(self, k: str) -> str | None:
        """Touche suivante (à droite, puis rangée suivante) pour le remplissage rapide."""
        page = self.page
        if parse_control(k) is not None:
            return None  # un contrôle n'a pas de « suivant » dans la grille
        r, c = parse_key(k)
        c += 1
        if c >= page["cols"]:
            r, c = r + 1, 0
        return key(r, c) if r < page["rows"] else None
