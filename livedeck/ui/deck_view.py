"""Le Stream Deck à l'écran : miroir en direct, sélection, glisser-déposer."""
from __future__ import annotations

import html
import json
import time

from PySide6.QtCore import QEvent, QMimeData, QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QDrag, QFont, QFontMetricsF, QImage, QLinearGradient, QPainter,
                           QPainterPath, QPen, QPixmap)
from PySide6.QtWidgets import QToolTip, QWidget

from ..actions import INTEGRATION_OF, next_press, summary
from ..model import key, parse_key
from ..render import render
from . import theme as T

MIME_CELL = "application/x-livedeck-cell"
MIME_INPUTS = "application/x-livedeck-inputs"
MIME_PRESET = "application/x-livedeck-preset"

MODEL_NAMES = {(8, 4): "STREAM DECK XL", (5, 3): "STREAM DECK MK.2", (4, 2): "STREAM DECK +",
               (3, 2): "STREAM DECK MINI", (9, 4): "STREAM DECK + XL", (16, 2): "STREAM DECK STUDIO",
               (3, 1): "STREAM DECK PEDAL"}
GAP = 0.16  # écart entre touches, en fraction de touche


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

    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.page_id: str | None = None
        self.sel: str | None = None
        self.live = False
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

    def _geom(self):
        p = self.page
        cols, rows = (p["cols"], p["rows"]) if p else (8, 4)
        pad, side, foot = 0.42, 1.35, 0.5  # marges du boîtier, colonne d'état, pied (en touches)
        wu = cols + (cols - 1) * GAP + 2 * pad + side
        hu = rows + (rows - 1) * GAP + pad + foot
        cell = min((self.width() - 8) / wu, (self.height() - 8) / hu, 138)
        body = QRectF((self.width() - wu * cell) / 2, (self.height() - hu * cell) / 2, wu * cell, hu * cell)
        x0, y0 = body.x() + pad * cell, body.y() + pad * cell
        return cols, rows, cell, cell * GAP, x0, y0, body

    def _cell_rect(self, r, c, g=None) -> QRectF:
        cols, rows, cell, gap, x0, y0, _ = g or self._geom()
        return QRectF(x0 + c * (cell + gap), y0 + r * (cell + gap), cell, cell)

    def key_at(self, pos) -> str | None:
        g = self._geom()
        for r in range(g[1]):
            for c in range(g[0]):
                if self._cell_rect(r, c, g).contains(pos):
                    return key(r, c)
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
        for r in range(rows):
            for c in range(cols):
                k = key(r, c)
                rect = self._cell_rect(r, c, g)
                b = page["buttons"].get(k)
                if b:
                    self._paint_key(p, k, rect, radius, px, dpr, b)
                else:
                    self._paint_empty(p, k, rect, radius, cell)
        self._paint_status(p, body, cell, x0 + cols * (cell + gap), y0, rows * cell + (rows - 1) * gap)

    def _paint_body(self, p: QPainter, body: QRectF, cell: float, page: dict):
        grad = QLinearGradient(body.topLeft(), body.bottomLeft())
        grad.setColorAt(0, QColor("#1c2029"))
        grad.setColorAt(1, QColor("#121419"))
        p.setPen(QPen(QColor("#7f1d1d") if self.live else QColor("#2b313d"), 1.6 if self.live else 1.2))
        p.setBrush(grad)
        p.drawRoundedRect(body, cell * 0.24, cell * 0.24)
        # pied : modèle et deck(s) qui affichent la page
        foot_y = body.bottom() - cell * 0.44
        p.setFont(self._font(cell * 0.1, True, cell * 0.035))
        p.setPen(QColor("#2f3542"))
        name = MODEL_NAMES.get((page["cols"], page["rows"]), "STREAM DECK")
        p.drawText(QRectF(body.x() + cell * 0.42, foot_y, body.width() / 2, cell * 0.3), Qt.AlignLeft | Qt.AlignVCenter, name)
        decks = [d for d in (self.engine.decks.info() if self.engine.decks else []) if d["page"] == self.page_id]
        text = ("SUR  " + "  ·  ".join(d["model"].replace("Stream Deck ", "").upper() for d in decks)) if decks \
            else ("LIVE  ·  CLIC = APPUI" if self.live else "AFFICHÉE SUR AUCUN DECK")
        p.setPen(QColor(T.ACCENT if decks else ("#b91c1c" if self.live else "#3c4352")))
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
        hover = k == self._hover and not self.live
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
        b = page["buttons"].get(k) if page else None
        r, c = parse_key(k)
        if not b:
            return f"Rangée {r + 1}, colonne {c + 1} : vide" + ("" if self.live else "  ·  clic pour choisir une action")
        e = self.engine
        names = e.page_names()
        title = (b.get("label") or "").replace("\n", " ").strip() or "Sans texte"
        lines = [f"<b>{html.escape(title)}</b>"]
        acts = b.get("actions") or []
        if b.get("mode") == "toggle":
            lines.append(f"<span style='color:{T.MUTED}'>Interrupteur</span>")
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
            self.setCursor(Qt.PointingHandCursor if k else Qt.ArrowCursor)
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
        if not self.live and any(e.mimeData().hasFormat(m) for m in (MIME_CELL, MIME_INPUTS, MIME_PRESET)):
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        k = self.key_at(e.position())
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
        if not k:
            return
        md = e.mimeData()
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
        if not page or self.live:
            return super().keyPressEvent(e)
        moves = {Qt.Key_Left: (0, -1), Qt.Key_Right: (0, 1), Qt.Key_Up: (-1, 0), Qt.Key_Down: (1, 0)}
        if e.key() in moves:
            r, c = parse_key(self.sel) if self.sel else (0, -1)
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
        r, c = parse_key(k)
        c += 1
        if c >= page["cols"]:
            r, c = r + 1, 0
        return key(r, c) if r < page["rows"] else None
