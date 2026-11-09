"""Assistant « Diagnostic Stream Deck » : vérifie en une minute que les decks et l'app
se comprennent, dans les deux sens.

  1. Écrans : l'app numérote chaque touche, affiche des couleurs pleines et fait varier
     la luminosité. Tu regardes si tout s'affiche au bon endroit.
  2. Touches : tu appuies sur chaque touche ; elle se coche ici et s'allume en vert.
  3. Performances : latence vMix, lecture de l'état, temps d'affichage d'une touche.

Pendant le diagnostic, les touches ne déclenchent rien.
"""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QDialog, QGridLayout, QHBoxLayout, QLabel, QPushButton, QStackedWidget,
                               QVBoxLayout, QWidget)

from . import theme as T
from .widgets import Seg, label

CHIP = ("QLabel {{ background: {bg}; color: {fg}; border: 1px solid {bd}; border-radius: 7px;"
        " padding: 4px 0; font-weight: 600; min-width: 34px; }}")
NO_DECK = ("Aucun Stream Deck connecté. Ferme Companion et le logiciel Elgato Stream Deck, "
           "rebranche le deck, puis rouvre ce diagnostic.")


class DiagnosticDialog(QDialog):
    key_event = Signal(str, int, bool)  # appels venus du thread des decks → thread de l'interface

    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.dm = engine.decks
        self.decks = self.dm.info() if self.dm else []
        self.setWindowTitle("Diagnostic Stream Deck")
        self.resize(780, 620)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(12)

        self.tabs = Seg([("out", "1 · Écrans"), ("in", "2 · Touches"), ("perf", "3 · Performances")], "out")
        self.tabs.changed.connect(lambda v: self.stack.setCurrentIndex(["out", "in", "perf"].index(v)))
        lay.addWidget(self.tabs)
        self.stack = QStackedWidget()
        self.stack.addWidget(self._outputs_page())
        self.stack.addWidget(self._inputs_page())
        self.stack.addWidget(self._perf_page())
        lay.addWidget(self.stack, 1)

        bottom = QHBoxLayout()
        bottom.addWidget(label("Pendant le diagnostic, les touches ne déclenchent rien.", "Hint"))
        bottom.addStretch(1)
        close = QPushButton("Terminer")
        close.setObjectName("Primary")
        close.clicked.connect(self.accept)
        bottom.addWidget(close)
        lay.addLayout(bottom)

        self._seq: list = []
        self._timer = QTimer(self, interval=90, timeout=self._step)
        self.key_event.connect(self._on_key)
        if self.dm:
            # les relâchés n'iront plus au moteur pendant le diagnostic : on relâche tout avant
            self.engine.release_all(latched=False)
            self.dm.diag_handler = lambda serial, idx, state: self.key_event.emit(serial, idx, state)
            for d in self.decks:
                for i in range(d["rows"] * d["cols"]):
                    self.dm.paint(d["serial"], i, None)

    # -- pages ----------------------------------------------------------------
    def _outputs_page(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 8, 0, 0)
        v.setSpacing(12)
        v.addWidget(label("L'app va numéroter chaque touche, afficher du rouge, du vert, du bleu et du blanc, "
                          "puis faire varier la luminosité. Regarde les decks : chaque étape doit s'afficher "
                          "partout, sans touche morte ni décalage.", "Hint", True))
        start = QPushButton("▶  Lancer la démonstration")
        start.setObjectName("Primary")
        start.setEnabled(bool(self.decks))
        start.clicked.connect(self._run_demo)
        v.addWidget(start, 0, Qt.AlignLeft)
        self.phase = QLabel("" if self.decks else NO_DECK)
        self.phase.setWordWrap(True)
        self.phase.setStyleSheet("font-size: 22px; font-weight: 700; padding: 16px 0;" if self.decks
                                 else f"color: {T.DANGER}; padding: 16px 0;")
        v.addWidget(self.phase)
        v.addWidget(label("RIEN NE S'ALLUME ?", "H2"))
        v.addWidget(label("• Le deck n'apparaît pas : Companion ou le logiciel Elgato le tient encore ; "
                          "ferme-les complètement (icône près de l'horloge).\n"
                          "• « Stream Deck indisponible » dans l'en-tête : relance setup.bat pour réinstaller "
                          "hidapi.dll.\n"
                          "• Des touches restent noires ou clignotent sur un hub USB : branche le deck "
                          "directement sur le PC ou sur un hub alimenté.\n"
                          "• Les numéros sont décalés : vérifie la taille de grille de la page "
                          "(clic droit sur l'onglet → Taille de la grille).", "Hint", True))
        v.addStretch(1)
        return w

    def _inputs_page(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 8, 0, 0)
        v.setSpacing(10)
        v.addWidget(label("Appuie sur chaque touche de chaque Stream Deck. Chaque touche reconnue se coche ici "
                          "et s'allume en vert sur le deck.", "Hint", True))
        self.count = label("", "H1")
        v.addWidget(self.count)
        self.chips: dict[tuple[str, int], QLabel] = {}
        for d in self.decks:
            v.addWidget(label(f"{d['model'].upper()}  ·  {d['serial']}", "H2"))
            grid = QGridLayout()
            grid.setHorizontalSpacing(6)
            grid.setVerticalSpacing(6)
            for i in range(d["rows"] * d["cols"]):
                chip = QLabel(str(i + 1))
                chip.setAlignment(Qt.AlignCenter)
                self.chips[(d["serial"], i)] = chip
                grid.addWidget(chip, i // d["cols"], i % d["cols"])
            row = QHBoxLayout()
            row.addLayout(grid)
            row.addStretch(1)
            v.addLayout(row)
        if not self.decks:
            v.addWidget(label(NO_DECK, "Hint", True))
        self.last = label("", "Hint")
        v.addWidget(self.last)
        v.addStretch(1)
        self._seen: set = set()
        self._refresh_chips()
        return w

    def _perf_page(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 8, 0, 0)
        v.setSpacing(10)
        v.addWidget(label("Mesures en direct, actualisées chaque seconde. Appuie sur quelques touches pour voir "
                          "l'activité.", "Hint", True))
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(10)
        self.perf: dict[str, QLabel] = {}
        rows = [("cmd", "vMix · commandes"), ("xml", "vMix · lecture de l'état"),
                ("img", "Stream Deck · affichage"), ("decks", "Stream Decks connectés")]
        for r, (k, title) in enumerate(rows):
            grid.addWidget(label(title, "Muted"), r, 0)
            val = QLabel("—")
            val.setStyleSheet("font-weight: 600;")
            self.perf[k] = val
            grid.addWidget(val, r, 1)
        grid.setColumnStretch(1, 1)
        v.addLayout(grid)
        self.perf_hint = label("", "Hint", True)
        v.addWidget(self.perf_hint)
        v.addStretch(1)
        self._prev = None
        self._perf_timer = QTimer(self, interval=1000, timeout=self._update_perf)
        self._perf_timer.start()
        self._update_perf()
        return w

    # -- performances ---------------------------------------------------------
    def _update_perf(self):
        vm, dm = self.engine.vmix, self.dm
        now = time.monotonic()
        cur = (now, vm.stats["cmd"], vm.stats["xml"], dm.stats["images"] if dm else 0)
        if self._prev is None:
            self._prev = cur
            return
        dt = max(0.001, now - self._prev[0])
        cmd_s, xml_s, img_s = ((c - o) / dt for c, o in zip(cur[1:], self._prev[1:]))
        self._prev = cur
        lat = f"{vm.latency_ms:.1f} ms aller-retour" if vm.latency_ms is not None else "—"
        self.perf["cmd"].setText(f"{lat}  ·  {cmd_s:.0f} commandes/s")
        xml = f"{vm.xml_ms:.0f} ms par lecture" if vm.xml_ms is not None else "—"
        self.perf["xml"].setText(f"{xml}  ·  {xml_s:.1f} lectures/s (toutes les 300 ms)")
        img = f"{dm.image_ms:.1f} ms par touche" if dm and dm.image_ms is not None else "—"
        self.perf["img"].setText(f"{img}  ·  {img_s:.0f} touches redessinées/s")
        info = dm.info() if dm else []
        self.perf["decks"].setText("  ·  ".join(f"{d['model']} ({d['cols']}×{d['rows']})" for d in info) or "aucun")
        hints = []
        if not vm.state.connected:
            hints.append("vMix n'est pas connecté : vérifie l'adresse dans les Réglages.")
        if vm.xml_ms is not None and vm.xml_ms > 300:
            hints.append(f"vMix met {vm.xml_ms:.0f} ms à produire son état : les retours sur les touches "
                         "arrivent en retard. vMix est probablement très chargé.")
        if vm.latency_ms is not None and vm.latency_ms > 40:
            hints.append(f"vMix répond lentement aux commandes ({vm.latency_ms:.0f} ms) : processeur ou "
                         "nombre de sources à surveiller.")
        if dm and dm.image_ms is not None and dm.image_ms > 25:
            hints.append(f"Une touche met {dm.image_ms:.0f} ms à s'afficher : un hub USB non alimenté ou "
                         "un port USB lent peut ralentir le deck.")
        self.perf_hint.setText("\n".join(hints) or "Tout est dans les temps.")
        self.perf_hint.setStyleSheet(f"color: {T.WARN};" if hints else f"color: {T.OK};")

    # -- touches --------------------------------------------------------------
    def _refresh_chips(self):
        for k, chip in self.chips.items():
            ok = k in self._seen
            chip.setStyleSheet(CHIP.format(bg="#14361f" if ok else T.PANEL2, fg=T.OK if ok else T.FAINT,
                                           bd=T.OK if ok else T.BORDER))
        total, done = len(self.chips), len(self._seen & set(self.chips))
        self.count.setText(f"{done} / {total} touches reconnues" + ("  ✓" if total and done == total else ""))

    def _on_key(self, serial: str, idx: int, state: bool):
        if not state:
            return
        self._seen.add((serial, idx))
        self._refresh_chips()
        model = next((d["model"] for d in self.decks if d["serial"] == serial), serial)
        cols = next((d["cols"] for d in self.decks if d["serial"] == serial), 1)
        self.last.setText(f"Dernier appui : {model} · touche {idx + 1} (rangée {idx // cols + 1}, "
                          f"colonne {idx % cols + 1})")
        self.dm.paint(serial, idx, {"bg": "#14361f", "fg": T.OK, "label": f"✓\n{idx + 1}"})

    # -- écrans ---------------------------------------------------------------
    def _run_demo(self):
        seq: list = []

        def all_keys(visual):
            return [("paint", (d["serial"], i, visual)) for d in self.decks for i in range(d["rows"] * d["cols"])]

        seq.append(("text", "Toutes les touches éteintes"))
        seq += all_keys(None)
        seq.append(("text", "Numérotation des touches"))
        longest = max(d["rows"] * d["cols"] for d in self.decks)
        for i in range(longest):
            seq.append(("batch", [("paint", (d["serial"], i, {"bg": T.ACCENT, "fg": "#04211d", "label": str(i + 1)}))
                                  for d in self.decks if i < d["rows"] * d["cols"]]))
        seq += [("wait", None)] * 6
        for name, color in (("Rouge", "#ff0000"), ("Vert", "#00ff00"), ("Bleu", "#0000ff"), ("Blanc", "#ffffff")):
            seq.append(("text", f"Couleur pleine · {name}"))
            seq.append(("batch", all_keys({"bg": color, "label": ""})))
            seq += [("wait", None)] * 8
        seq.append(("text", "Luminosité · de 10 à 100 %"))
        for b in list(range(10, 101, 10)) + [100]:
            seq.append(("batch", [("bright", (d["serial"], b)) for d in self.decks]))
            seq += [("wait", None)] * 2
        seq.append(("text", "Terminé : tout s'est affiché au bon endroit ?"))
        seq.append(("restore_bright", None))
        self._seq = seq
        self._timer.start()

    def _do(self, kind, data):
        if kind == "paint":
            self.dm.paint(*data)
        elif kind == "bright":
            self.dm.set_brightness(*data)

    def _step(self):
        if not self._seq:
            self._timer.stop()
            return
        kind, data = self._seq.pop(0)
        if kind == "text":
            self.phase.setText(data)
        elif kind == "batch":
            for k, d in data:
                self._do(k, d)
        elif kind == "restore_bright":
            for d in self.decks:
                self.dm.set_brightness(d["serial"], self.engine.cfg["decks"].get(d["serial"], {}).get("brightness", 80))
        else:
            self._do(kind, data)
            # enchaîner les touches éteintes d'un coup plutôt qu'une par tick
            while self._seq and self._seq[0][0] == "paint":
                self._do(*self._seq.pop(0))

    def done(self, r):
        self._timer.stop()
        self._perf_timer.stop()
        if self.dm:
            self.dm.diag_handler = None
            self.dm.restore()
        super().done(r)
