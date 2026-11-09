"""Réglages : général (vMix, démarrage), Stream Decks, intégrations (py-intercom, orchestream, Tally32)."""
from __future__ import annotations

import copy
import html
import threading

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPlainTextEdit, QPushButton, QScrollArea, QSlider, QSpinBox, QStackedWidget, QVBoxLayout,
                               QWidget)

from .. import __version__, autostart
from ..health import parse_targets
from ..model import surface_of
from ..integrations import (integration_status, overlay_label, overlay_list, probe_deck, probe_intercom,
                            probe_multiview, probe_orchestream, probe_tally)
from . import theme as T
from .widgets import Seg, label


def _dot(ok: bool | None) -> str:
    return f"<span style='color:{T.OK if ok else T.FAINT if ok is None else T.DANGER}'>●</span>&nbsp; "


class _Probe(QObject):
    """Résultat d'un test lancé dans un thread, renvoyé au thread de l'interface."""
    done = Signal(str, object, str)  # intégration, état (True / None / False), message


def parse_overlays(text: str) -> list[dict]:
    """« twizz = Twizz » (ou juste « twizz »), un overlay par ligne ou séparés par des virgules."""
    out = []
    for name, lab in parse_targets(text.replace(",", "\n")):
        name = name.strip()
        if name and name not in {o["name"] for o in out}:
            out.append({"name": name, "label": lab.strip() if lab.strip() != name else overlay_label(name)})
    return out


def _page() -> tuple[QWidget, QVBoxLayout]:
    w = QWidget()
    v = QVBoxLayout(w)
    v.setContentsMargins(0, 10, 0, 0)
    v.setSpacing(10)
    return w, v


def _scroll(page: QWidget) -> QScrollArea:
    """Onglet dans une zone qui défile en hauteur : rien ne se chevauche dans une petite fenêtre."""
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    area.setWidget(page)
    return area


class SettingsDialog(QDialog):
    def __init__(self, engine, parent=None, tab: str = "general"):
        super().__init__(parent)
        self.engine = engine
        self.setWindowTitle("Réglages")
        self.setMinimumSize(680, 560)
        self.resize(700, 660)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(12)
        tabs = Seg([("general", "Général"), ("decks", "Stream Decks"), ("integ", "Intégrations")], "general")
        lay.addWidget(tabs)
        stack = QStackedWidget()
        for page in (self._general(), self._decks(), self._integrations()):
            stack.addWidget(_scroll(page))
        tabs.changed.connect(lambda v: stack.setCurrentIndex(["general", "decks", "integ"].index(v)))
        if tab in ("decks", "integ"):
            tabs.set(tab)
            stack.setCurrentIndex(["general", "decks", "integ"].index(tab))
        self.tabs, self.stack = tabs, stack
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
        self._probe = _Probe(self)
        self._probe.done.connect(self._probe_done)
        # fenêtre ouverte : les intégrations sont surveillées même si aucune touche ne les utilise encore
        for svc in (engine.intercom, engine.orchestream, engine.tallies, engine.multiview, engine.orchdeck):
            svc.set_forced(True)
        self._timer = QTimer(self, interval=500, timeout=self._refresh_status)
        self._timer.start()
        self._refresh_status()

    def done(self, r):
        # La fenêtre fermée reste en mémoire (enfant de la fenêtre principale) : sans cet arrêt, son
        # minuteur continuait d'interroger les intégrations deux fois par seconde, une fois de plus
        # à chaque ouverture des Réglages.
        self._timer.stop()
        for svc in (self.engine.intercom, self.engine.orchestream, self.engine.tallies, self.engine.multiview,
                    self.engine.orchdeck):
            svc.set_forced(False)
        super().done(r)

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
        v.addWidget(label("MISES À JOUR", "H2"))
        self.check_updates = QCheckBox("Chercher une nouvelle version de LiveDeck au démarrage")
        self.check_updates.setChecked(bool(opts.get("check_updates", True)))
        v.addWidget(self.check_updates)
        row = QHBoxLayout()
        btn = QPushButton("Rechercher maintenant")
        btn.clicked.connect(self._check_updates_now)
        row.addWidget(btn)
        self.update_status = label(f"Version installée : {__version__}", "Hint", True)
        row.addWidget(self.update_status, 1)
        v.addLayout(row)

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

    def _check_updates_now(self) -> None:
        self.update_status.setText("Recherche…")
        win = self.parent()
        win.check_updates(manual=True, done=lambda msg: self.update_status.setText(msg))

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
            page.setToolTip("Page affichée au démarrage. Changer de page en direct ne modifie pas ce réglage.")
            page.addItem("(automatique : 1re page à sa taille)", None)
            for p in e.pages:
                page.addItem(f"{p['name']}  ({p['cols']}×{p['rows']})", p["id"])
            page.setCurrentIndex(max(0, page.findData(conf.get("page"))))
            grid.addWidget(page, i, 1)
            bright = QSlider(Qt.Horizontal)
            bright.setRange(5, 100)
            bright.setValue(int(conf.get("brightness", 80)))
            bright.setFixedWidth(100)
            bright.setToolTip("Luminosité")
            grid.addWidget(bright, i, 2)
            # Vibration : seulement pour un appareil qui en est capable (Loupedeck). Un appareil
            # débranché garde la case s'il l'avait déjà réglée, pour ne pas perdre le choix.
            haptic = None
            if (d and surface_of(d["model"]) == "loupedeck") or (not d and "haptic" in conf):
                haptic = QCheckBox("Vibration")
                haptic.setChecked(bool(conf.get("haptic", True)))
                haptic.setToolTip("Retour vibratoire à chaque appui sur l'appareil. Décoché, l'appareil ne "
                                  "vibre plus du tout, quels que soient les réglages des contrôles.")
                grid.addWidget(haptic, i, 3)
            ident = QPushButton("Identifier")
            ident.setObjectName("Small")
            ident.setToolTip("Allume ce deck en couleur pendant 2 secondes")
            ident.setEnabled(bool(d))
            ident.clicked.connect(lambda _=False, s=s: e.decks and e.decks.identify(s))
            grid.addWidget(ident, i, 4)
            self.deck_rows.append((s, page, bright, haptic))
        grid.setColumnStretch(1, 1)
        v.addWidget(box)
        v.addWidget(label("Chaque deck est reconnu par son numéro de série et affiche sa page au démarrage.", "Hint", True))
        v.addStretch()
        return w

    # -- intégrations ----------------------------------------------------------
    def _test_row(self, v: QVBoxLayout, key: str) -> QLabel:
        """Ligne d'état en direct + bouton « Tester » (qui essaie les valeurs saisies, pas encore enregistrées)."""
        row = QHBoxLayout()
        status = label("", "Hint", True)
        status.setTextFormat(Qt.RichText)
        row.addWidget(status, 1)
        btn = QPushButton("Tester")
        btn.setObjectName("Small")
        btn.setToolTip("Vérifie tout de suite la connexion avec les valeurs ci-dessus")
        btn.clicked.connect(lambda: self._test(key))
        row.addWidget(btn, 0, Qt.AlignTop)
        v.addLayout(row)
        result = label("", "Hint", True)
        result.setTextFormat(Qt.RichText)
        result.setTextInteractionFlags(Qt.TextSelectableByMouse)
        result.hide()
        v.addWidget(result)
        self._tests[key] = (btn, result)
        return status

    def _integrations(self) -> QWidget:
        integ = self.engine.cfg["integrations"]
        w, v = _page()
        self._tests: dict = {}
        v.addWidget(label("PY-INTERCOM  ·  TALKBACK", "H2"))
        row = QHBoxLayout()
        self.ic_host = QLineEdit(integ["intercom"].get("host", "127.0.0.1"))
        self.ic_host.setPlaceholderText("127.0.0.1 (même PC)")
        self.ic_port = QSpinBox()
        self.ic_port.setRange(1, 65535)
        self.ic_port.setValue(int(integ["intercom"].get("port", 5080)))
        row.addWidget(label("Client", "Muted"))
        row.addWidget(self.ic_host, 1)
        row.addWidget(label("Port", "Muted"))
        row.addWidget(self.ic_port)
        v.addLayout(row)
        self.ic_status = self._test_row(v, "intercom")
        v.addWidget(label("Dans py-intercom, zone Parler : coche « Contrôle HTTP » (même port) ; si LiveDeck tourne "
                          "sur un autre PC, coche aussi « Depuis le réseau ».", "Hint", True))

        v.addSpacing(6)
        v.addWidget(label("ORCHESTREAM  ·  OVERLAYS WEB", "H2"))
        self.orch_url = QLineEdit(integ["orchestream"].get("url", ""))
        self.orch_url.setPlaceholderText("http://serveur:5174")
        v.addWidget(self.orch_url)
        row = QHBoxLayout()
        row.addWidget(label("Overlays", "Muted"))
        self.orch_overlays = QLineEdit(", ".join(n if lab == overlay_label(n) else f"{n} = {lab}"
                                                 for n, lab in overlay_list(integ.get("orchestream"))))
        self.orch_overlays.setPlaceholderText("twizz, poll = Sondage, speedizz")
        self.orch_overlays.setToolTip("Noms des overlays d'orchestream (route /api/overlay/<nom>/show), séparés par "
                                      "des virgules ; « nom = Libellé » pour un nom affiché. Un overlay vu dans le "
                                      "flux en direct est aussi proposé automatiquement.")
        row.addWidget(self.orch_overlays, 1)
        v.addLayout(row)
        self.orch_status = self._test_row(v, "orchestream")
        # API Stream Deck d'orchestream-v2 : même serveur, authentifiée par un jeton dans l'en-tête.
        row = QHBoxLayout()
        row.addWidget(label("Jeton API", "Muted"))
        self.orch_token = QLineEdit(integ["orchestream"].get("token", ""))
        self.orch_token.setEchoMode(QLineEdit.PasswordEchoOnEdit)  # masqué, sauf pendant la saisie
        self.orch_token.setPlaceholderText("osk_…  (commandes régie)")
        self.orch_token.setToolTip("Jeton de l'API Stream Deck d'orchestream-v2 : dans orchestream, Réglages › "
                                   "Sécurité › Jetons API (Stream Deck). Il n'est affiché qu'une fois à sa création ; "
                                   "ses permissions décident des commandes que les touches peuvent lancer.\n"
                                   "Inutile pour les seuls overlays ci-dessus.")
        row.addWidget(self.orch_token, 1)
        v.addLayout(row)
        self.orchdeck_status = self._test_row(v, "orchdeck")

        v.addSpacing(6)
        v.addWidget(label("TALLY32  ·  TALLYS ESP32", "H2"))
        self.tallies = QPlainTextEdit("\n".join(f"{d.get('name')} = {d.get('host')}" for d in integ.get("tallies", [])))
        self.tallies.setObjectName("Field")
        self.tallies.setPlaceholderText("Caméra 1 = 192.168.1.51\nCaméra 2 = 192.168.1.52")
        self.tallies.setFixedHeight(72)
        v.addWidget(self.tallies)
        self.tally_status = self._test_row(v, "tallies")

        v.addSpacing(6)
        v.addWidget(label("PY-MULTIVIEW  ·  MULTIVIEWER SRT/OMT", "H2"))
        self.mv_url = QLineEdit((integ.get("multiview") or {}).get("url", ""))
        self.mv_url.setPlaceholderText("192.168.1.42:5090")
        self.mv_url.setToolTip("Adresse du poste py-multiview. Le port 5090 est ajouté s'il manque.\n"
                               "Dans py-multiview : Préférences → Pilotage à distance → Ouvrir l'API locale, "
                               "en décochant « Ce poste uniquement ».")
        v.addWidget(self.mv_url)
        self.mv_status = self._test_row(v, "multiview")

        v.addWidget(label("Une intégration ne travaille que si une touche l'utilise (bibliothèque « Boutons » → "
                          "Intégrations) ; pendant que cette fenêtre est ouverte, elles sont toutes surveillées.",
                          "Hint", True))
        v.addStretch()
        return w

    def _test(self, key: str) -> None:
        btn, result = self._tests[key]
        btn.setEnabled(False)
        result.show()
        result.setText(_dot(None) + "Test en cours…")
        if key == "intercom":
            host, port = self.ic_host.text().strip() or "127.0.0.1", self.ic_port.value()

            def run():
                return probe_intercom(host, port)
        elif key == "orchestream":
            url = self.orch_url.text()

            def run():
                return probe_orchestream(url)
        elif key == "multiview":
            mv_url = self.mv_url.text()

            def run():
                return probe_multiview(mv_url)
        elif key == "orchdeck":
            url, token = self.orch_url.text(), self.orch_token.text()

            def run():
                return probe_deck(url, token)
        else:
            devices = parse_targets(self.tallies.toPlainText())

            def run():
                if not devices:
                    return None, _dot(None) + "Aucun tally renseigné (une ligne « Nom = IP » par tally)."
                res = [(name, *probe_tally(host)) for name, host in devices]
                oks = [ok for _, ok, _ in res]
                ok = False if False in oks else None if None in oks else True
                return ok, "<br>".join(_dot(o) + html.escape(f"{n} : {m}") for n, o, m in res)

        def worker():
            try:
                ok, msg = run()
            except Exception as e:  # le test ne doit jamais faire tomber la fenêtre
                ok, msg = False, f"Erreur inattendue : {e}"
            self._probe.done.emit(key, ok, msg)
        threading.Thread(target=worker, name=f"test-{key}", daemon=True).start()

    def _probe_done(self, key: str, ok, msg: str) -> None:
        btn, result = self._tests[key]
        btn.setEnabled(True)
        result.setText(msg if key == "tallies" else _dot(ok) + html.escape(msg))  # tallys : déjà mis en forme

    def _refresh_status(self):
        e = self.engine
        st, vm = e.vmix.state, e.vmix
        lat = f" · {vm.latency_ms:.1f} ms" if vm.latency_ms is not None else ""
        self.vmix_status.setText(_dot(True) + f"Connecté à vMix {st.version}{lat}" if st.connected else
                                 _dot(False) + "Non connecté. L'API TCP de vMix (port 8099) est active par défaut ; "
                                 "si vMix tourne sur un autre PC, indique son IP.")
        labels = {"intercom": self.ic_status, "orchestream": self.orch_status, "tallies": self.tally_status,
                  "multiview": self.mv_status, "orchdeck": self.orchdeck_status}
        for key, _, ok, text in integration_status(e):
            if key in labels:
                labels[key].setText(_dot(ok) + html.escape(text))

    def _save(self):
        """Une seule étape « annuler » ; le moteur applique ensuite vMix, luminosité et intégrations.
        Les decks restent sur leur page actuelle, sauf si leur page de démarrage vient de changer."""
        e = self.engine
        host, port = self.host.text().strip() or "127.0.0.1", self.port.value()
        if self.autostart.isChecked() != autostart.is_enabled():
            try:
                autostart.set_enabled(self.autostart.isChecked())
            except OSError as ex:
                e.log("error", f"Démarrage automatique impossible à régler : {ex}")
        decks = {serial: (page.currentData(), bright.value(), haptic.isChecked() if haptic else None)
                 for serial, page, bright, haptic in self.deck_rows}
        moved = [s for s, (pg, _, _) in decks.items() if pg and pg != (e.cfg["decks"].get(s) or {}).get("page")]
        tallies = [{"name": n, "host": h} for n, h in parse_targets(self.tallies.toPlainText())]
        overlays = parse_overlays(self.orch_overlays.text())
        if [(o["name"], o["label"]) for o in overlays] == overlay_list(e.cfg["integrations"].get("orchestream")) \
                and "overlays" not in (e.cfg["integrations"].get("orchestream") or {}):
            overlays = None  # liste par défaut inchangée : on ne l'écrit pas dans la configuration

        def apply(cfg):
            cfg["vmix"] = {"host": host, "port": int(port)}
            cfg["options"].update(health_interval=self.interval.value(), start_live=self.start_live.isChecked(),
                                  close_to_tray=self.close_tray.isChecked(),
                                  check_updates=self.check_updates.isChecked())
            for serial, (pg, bright, haptic) in decks.items():
                conf = cfg["decks"].setdefault(serial, {})
                conf["brightness"] = bright
                if haptic is not None:
                    conf["haptic"] = haptic
                if pg:
                    conf["page"] = pg
                else:
                    conf.pop("page", None)
            integ = cfg["integrations"]
            integ["intercom"] = {**(integ.get("intercom") or {}), "host": self.ic_host.text().strip() or "127.0.0.1",
                                 "port": self.ic_port.value()}
            integ["orchestream"] = {**(integ.get("orchestream") or {}), "url": self.orch_url.text().strip(),
                                    "token": self.orch_token.text().strip()}
            if overlays is not None:
                integ["orchestream"]["overlays"] = overlays
            integ["tallies"] = tallies
            integ["multiview"] = {**(integ.get("multiview") or {}), "url": self.mv_url.text().strip()}
        trial = copy.deepcopy(e.cfg)
        apply(trial)
        if trial != e.cfg:  # rien de modifié : pas d'étape « annuler » inutile
            e.mutate(apply)
        if e.decks:
            for serial in moved:
                e.decks.apply_settings(serial, page_changed=True)
        self.accept()
