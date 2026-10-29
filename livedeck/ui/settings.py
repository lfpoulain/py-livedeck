"""Réglages : général (vMix, démarrage), Stream Decks, intégrations (py-intercom, orchestream, Tally32)."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QLineEdit,
                               QPlainTextEdit, QPushButton, QSlider, QSpinBox, QStackedWidget, QVBoxLayout, QWidget)

from .. import autostart
from ..health import parse_targets
from ..integrations import ORCH_OVERLAYS
from . import theme as T
from .widgets import Seg, label


def _dot(ok: bool | None) -> str:
    return f"<span style='color:{T.OK if ok else T.FAINT if ok is None else T.DANGER}'>●</span>&nbsp; "


def _page() -> tuple[QWidget, QVBoxLayout]:
    w = QWidget()
    v = QVBoxLayout(w)
    v.setContentsMargins(0, 10, 0, 0)
    v.setSpacing(10)
    return w, v


class SettingsDialog(QDialog):
    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.setWindowTitle("Réglages")
        self.setMinimumSize(680, 560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(12)
        tabs = Seg([("general", "Général"), ("decks", "Stream Decks"), ("integ", "Intégrations")], "general")
        lay.addWidget(tabs)
        stack = QStackedWidget()
        stack.addWidget(self._general())
        stack.addWidget(self._decks())
        stack.addWidget(self._integrations())
        tabs.changed.connect(lambda v: stack.setCurrentIndex(["general", "decks", "integ"].index(v)))
        lay.addWidget(stack, 1)

        btns = QHBoxLayout()
        btns.addStretch()
        ok_btn = QPushButton("Enregistrer")
        ok_btn.setObjectName("Primary")
        ok_btn.clicked.connect(self._save)
        cancel = QPushButton("Annuler")
        cancel.clicked.connect(self.reject)
        btns.addWidget(ok_btn)
        btns.addWidget(cancel)
        lay.addLayout(btns)
        self._timer = QTimer(self, interval=500, timeout=self._refresh_status)
        self._timer.start()
        self._refresh_status()

    # -- général ---------------------------------------------------------------
    def _general(self) -> QWidget:
        e = self.engine
        w, v = _page()
        v.addWidget(label("VMIX", "H2"))
        row = QHBoxLayout()
        self.host = QLineEdit(e.cfg["vmix"]["host"])
        self.host.setPlaceholderText("127.0.0.1")
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(int(e.cfg["vmix"]["port"]))
        row.addWidget(label("Adresse", "Muted"))
        row.addWidget(self.host, 1)
        row.addWidget(label("Port TCP", "Muted"))
        row.addWidget(self.port)
        v.addLayout(row)
        self.vmix_status = label("", "Hint", True)
        v.addWidget(self.vmix_status)

        v.addSpacing(8)
        v.addWidget(label("DÉMARRAGE", "H2"))
        opts = e.cfg["options"]
        self.autostart = QCheckBox("Lancer LiveDeck à l'ouverture de session Windows (réduit près de l'horloge)")
        self.autostart.setChecked(autostart.is_enabled())
        self.start_live = QCheckBox("Démarrer en mode Live (édition verrouillée)")
        self.start_live.setChecked(bool(opts.get("start_live")))
        self.close_tray = QCheckBox("Fermer la fenêtre la réduit près de l'horloge : les decks continuent de fonctionner")
        self.close_tray.setChecked(bool(opts.get("close_to_tray", True)))
        for cb in (self.autostart, self.start_live, self.close_tray):
            v.addWidget(cb)

        v.addSpacing(8)
        v.addWidget(label("SURVEILLANCE RÉSEAU (PING / HTTP)", "H2"))
        row = QHBoxLayout()
        row.addWidget(label("Vérifier les équipements toutes les", "Muted"))
        self.interval = QSpinBox()
        self.interval.setRange(3, 600)
        self.interval.setSuffix(" s")
        self.interval.setValue(int(opts.get("health_interval", 15)))
        row.addWidget(self.interval)
        row.addStretch()
        v.addLayout(row)
        v.addStretch()
        return w

    # -- decks -----------------------------------------------------------------
    def _decks(self) -> QWidget:
        e = self.engine
        w, v = _page()
        box = QFrame()
        box.setObjectName("Card")
        grid = QGridLayout(box)
        grid.setContentsMargins(14, 12, 14, 12)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)
        self.deck_rows = []
        connected = {d["serial"]: d for d in (e.decks.info() if e.decks else [])}
        serials = list(connected) + [s for s in e.cfg["decks"] if s not in connected]
        if e.decks and e.decks.error:
            v.addWidget(label(e.decks.error, "Hint", True))
        if not serials:
            grid.addWidget(label("Aucun Stream Deck détecté. Ferme Companion et le logiciel Elgato : "
                                 "un deck ne peut être piloté que par une seule application.", "Hint", True), 0, 0)
        for i, s in enumerate(serials):
            conf = e.cfg["decks"].get(s, {})
            d = connected.get(s)
            grid.addWidget(label(_dot(bool(d)) + (f"<b>{d['model']}</b><br><span style='color:{T.FAINT}'>{s}</span>"
                                                   if d else f"<b>{s}</b><br><span style='color:{T.FAINT}'>débranché</span>")),
                           i, 0)
            page = QComboBox()
            for p in e.pages:
                page.addItem(f"{p['name']}  ({p['cols']}×{p['rows']})", p["id"])
            page.setCurrentIndex(max(0, page.findData(conf.get("page") or (d or {}).get("page"))))
            grid.addWidget(page, i, 1)
            bright = QSlider(Qt.Horizontal)
            bright.setRange(5, 100)
            bright.setValue(int(conf.get("brightness", 80)))
            bright.setFixedWidth(100)
            bright.setToolTip("Luminosité")
            grid.addWidget(bright, i, 2)
            ident = QPushButton("Identifier")
            ident.setObjectName("Small")
            ident.setToolTip("Allume ce deck en couleur pendant 2 secondes")
            ident.setEnabled(bool(d))
            ident.clicked.connect(lambda _=False, s=s: e.decks and e.decks.identify(s))
            grid.addWidget(ident, i, 3)
            self.deck_rows.append((s, page, bright))
        grid.setColumnStretch(1, 1)
        v.addWidget(box)
        v.addWidget(label("Chaque deck est reconnu par son numéro de série et affiche sa page au démarrage.", "Hint", True))
        v.addStretch()
        return w

    # -- intégrations ----------------------------------------------------------
    def _integrations(self) -> QWidget:
        integ = self.engine.cfg["integrations"]
        w, v = _page()
        v.addWidget(label("PY-INTERCOM  ·  TALKBACK", "H2"))
        row = QHBoxLayout()
        self.ic_host = QLineEdit(integ["intercom"].get("host", "127.0.0.1"))
        self.ic_port = QSpinBox()
        self.ic_port.setRange(1, 65535)
        self.ic_port.setValue(int(integ["intercom"].get("port", 5080)))
        row.addWidget(label("Client", "Muted"))
        row.addWidget(self.ic_host, 1)
        row.addWidget(label("Port", "Muted"))
        row.addWidget(self.ic_port)
        v.addLayout(row)
        self.ic_status = label("", "Hint", True)
        v.addWidget(self.ic_status)

        v.addSpacing(6)
        v.addWidget(label("ORCHESTREAM  ·  OVERLAYS WEB", "H2"))
        self.orch_url = QLineEdit(integ["orchestream"].get("url", ""))
        self.orch_url.setPlaceholderText("http://orchestream.diy.yt")
        v.addWidget(self.orch_url)
        self.orch_status = label("", "Hint", True)
        v.addWidget(self.orch_status)

        v.addSpacing(6)
        v.addWidget(label("TALLY32  ·  TALLYS ESP32", "H2"))
        self.tallies = QPlainTextEdit("\n".join(f"{d.get('name')} = {d.get('host')}" for d in integ.get("tallies", [])))
        self.tallies.setObjectName("Field")
        self.tallies.setPlaceholderText("Reporter = 10.0.0.163\nAnimateur = 10.0.0.197")
        self.tallies.setFixedHeight(80)
        v.addWidget(self.tallies)
        self.tally_status = label("", "Hint", True)
        v.addWidget(self.tally_status)
        v.addWidget(label("Chaque intégration ne travaille que si une touche l'utilise (bibliothèque « Boutons » → "
                          "Intégrations).", "Hint", True))
        v.addStretch()
        return w

    def _refresh_status(self):
        e = self.engine
        st, vm = e.vmix.state, e.vmix
        lat = f" · {vm.latency_ms:.1f} ms" if vm.latency_ms is not None else ""
        self.vmix_status.setText(_dot(True) + f"Connecté à vMix {st.version}{lat}" if st.connected else
                                 _dot(False) + "Non connecté. L'API TCP de vMix (port 8099) est active par défaut ; "
                                 "si vMix tourne sur un autre PC, indique son IP.")
        ic = e.intercom
        if not ic.wanted:
            text = _dot(None) + "Aucune touche ne l'utilise."
        elif ic.reachable is None:
            text = _dot(None) + "Connexion…"
        elif not ic.reachable:
            text = _dot(False) + (f"Injoignable sur {ic.base}. Lance le client py-intercom et active son API "
                                  "(réglage du port dans le client).")
        else:
            buses = "  ·  ".join(f"{k} {'🎙 ouvert' if b.get('active') else 'fermé'} ({b.get('mode')})"
                                 for k, b in ic.buses.items())
            text = _dot(ic.online) + f"{ic.state_text or ('En ligne' if ic.online else 'Désarmé')}  ·  {buses}"
        self.ic_status.setText(text)
        orch = e.orchestream
        if not orch.wanted:
            text = _dot(None) + "Aucune touche ne l'utilise."
        elif not orch.url:
            text = _dot(False) + "Adresse à renseigner."
        else:
            names = dict(ORCH_OVERLAYS)
            states = "  ·  ".join(f"{names[k]} {'affiché' if v else 'masqué' if v is False else 'inconnu'}"
                                  for k, v in orch.overlays.items())
            text = _dot(orch.connected) + (f"Flux en direct connecté  ·  {states}" if orch.connected
                                           else "Flux en direct injoignable, nouvel essai en cours…")
        self.orch_status.setText(text)
        ta = e.tallies
        if not ta.devices:
            text = _dot(None) + "Aucun tally renseigné."
        elif not ta.wanted:
            text = _dot(None) + "Aucune touche ne les utilise."
        else:
            parts = []
            for d in ta.devices:
                s = ta.status.get(d["host"])
                if s is None:
                    parts.append(_dot(None) + d["name"])
                elif not s.get("reachable"):
                    parts.append(_dot(False) + f"{d['name']} injoignable")
                else:
                    tally = "PGM" if s.get("live") else "PVW" if s.get("preview") else ""
                    parts.append(_dot(bool(s.get("connected"))) + f"{d['name']} "
                                 + ("connecté à vMix" if s.get("connected") else "sans vMix")
                                 + (f" · {tally}" if tally else ""))
            text = "&nbsp;&nbsp;&nbsp;".join(parts)
        self.tally_status.setText(text)

    def _save(self):
        e = self.engine
        host, port = self.host.text().strip() or "127.0.0.1", self.port.value()
        if (host, port) != (e.cfg["vmix"]["host"], e.cfg["vmix"]["port"]):
            e.set_vmix(host, port)
        opts = e.cfg["options"]
        opts["health_interval"] = self.interval.value()
        opts["start_live"] = self.start_live.isChecked()
        opts["close_to_tray"] = self.close_tray.isChecked()
        e.health.interval = self.interval.value()
        if self.autostart.isChecked() != autostart.is_enabled():
            try:
                autostart.set_enabled(self.autostart.isChecked())
            except OSError as ex:
                e.log("error", f"Démarrage automatique impossible à régler : {ex}")
        for serial, page, bright in self.deck_rows:
            e.set_deck_settings(serial, page=page.currentData(), brightness=bright.value())
        integ = e.cfg["integrations"]
        integ["intercom"] = {"host": self.ic_host.text().strip() or "127.0.0.1", "port": self.ic_port.value()}
        integ["orchestream"] = {"url": self.orch_url.text().strip()}
        integ["tallies"] = [{"name": n, "host": h} for n, h in parse_targets(self.tallies.toPlainText())]
        e.changed()
        self.accept()
