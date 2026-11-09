"""Petites fenêtres : options d'import Companion, remplissage d'une rangée, raccourcis et légende."""
from __future__ import annotations

import threading

from PySide6.QtCore import QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel,
                               QListWidget, QListWidgetItem, QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout)

from .. import __version__, updater
from ..actions import THEN_CHOICES
from . import theme as T
from .widgets import label, tame_combo, widen_popup

# styles de rangée : mêmes valeurs que le choix « Créer » de la bibliothèque d'inputs
ROW_STYLES = [("take", "Take (1er appui preview, 2e antenne)"), ("pair", "Preview + Program (2 rangées)"),
              ("preview", "Preview"), ("program", "Program (cut direct)"), ("overlay1", "Overlay 1"),
              ("overlay2", "Overlay 2 (PIP)"), ("overlay3", "Overlay 3"), ("overlay4", "Overlay 4"),
              ("audio", "Couper le son")]

SHORTCUTS = [
    ("F5", "Passer en Live / revenir à l'édition"),
    ("Ctrl+L", "Verrouiller / déverrouiller : plus d'édition ni de clic sur les touches (les Stream Decks restent actifs)"),
    ("Entrée", "Tester la touche sélectionnée (le deck à l'écran a le focus)"),
    ("← → ↑ ↓", "Changer de touche sélectionnée"),
    ("F2", "Modifier le texte de la touche"),
    ("Ctrl+F", "Rechercher une touche sur toutes les pages"),
    ("Ctrl+Pg↑ / Ctrl+Pg↓", "Page précédente / suivante"),
    ("Ctrl+C / X / V", "Copier, couper, coller une touche"),
    ("Ctrl+D", "Dupliquer la touche"),
    ("Suppr", "Effacer la touche"),
    ("Ctrl+Z / Ctrl+Y", "Annuler / rétablir"),
    ("Échap", "Désélectionner"),
    ("F1", "Cette aide"),
]

LEGEND = (
    "<b>Sur les touches</b><br>"
    f"<span style='color:{T.PGM}'>■</span> Fond rouge : l'input est à l'antenne<br>"
    f"<span style='color:{T.PVW}'>■</span> Fond vert : en preview, prêt à prendre (Take : le prochain appui "
    "l'envoie à l'antenne)<br>"
    f"<span style='color:{T.PVW}'>▁</span> Liseré vert sous une touche rouge : aussi en preview<br>"
    f"<span style='color:{T.WARN}'>●</span> Point orange : un input de la touche est introuvable dans vMix<br>"
    f"<span style='color:{T.FAINT}'>■</span> Grisée + HORS LIGNE / DÉSARMÉ / BUS ABSENT : py-intercom "
    "indisponible<br>"
    "▮ Jauge qui se remplit : maintenir la touche (sécurité ½ s ou appui long)<br><br>"
    "Survole une touche pour voir ce qu'elle fait."
)


class ImportOptionsDialog(QDialog):
    """Options de l'import Companion : 2e appui des touches Preview, conversion vers les intégrations."""

    def __init__(self, parent=None, path: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Import Companion")
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(12)
        if path:
            lay.addWidget(label(path, "Hint", True))
        lay.addWidget(label("2E APPUI SUR UNE TOUCHE PREVIEW", "H2"))
        self.then = tame_combo(QComboBox(), 16)
        for v, text in THEN_CHOICES:
            self.then.addItem("Rien, comme dans Companion" if v == "none" else f"À l'antenne : {text}", v)
        widen_popup(self.then)
        lay.addWidget(self.then)
        lay.addWidget(label("Dans Companion, appuyer deux fois sur une touche Preview ne fait rien. Choisis une "
                            "transition pour qu'un 2e appui sur l'input déjà en preview l'envoie à l'antenne "
                            "(comme une touche Take).", "Hint", True))
        lay.addSpacing(6)
        lay.addWidget(label("INTÉGRATIONS", "H2"))
        self.integrations = QCheckBox("Convertir vers les intégrations natives (py-intercom, orchestream, Tally32)")
        lay.addWidget(self.integrations)
        lay.addWidget(label("Les raccourcis clavier « Intercom » deviennent des touches py-intercom (le raccourci est "
                            "gardé en secours), les requêtes show / hide d'overlays deviennent des touches orchestream "
                            "et les reconnexions de tallys des touches Tally32. Laisse décoché si tu n'utilises pas "
                            "ces logiciels.", "Hint", True))
        btns = QHBoxLayout()
        btns.addStretch()
        ok = QPushButton("Importer…")
        ok.setObjectName("Primary")
        ok.clicked.connect(self.accept)
        cancel = QPushButton("Annuler")
        cancel.clicked.connect(self.reject)
        btns.addWidget(ok)
        btns.addWidget(cancel)
        lay.addLayout(btns)

    def options(self) -> dict:
        return {"preview_then": self.then.currentData(), "integrations": self.integrations.isChecked()}


class FillRowDialog(QDialog):
    """« Remplir la rangée » : plusieurs inputs vMix d'un coup, dans le style choisi."""

    def __init__(self, engine, parent=None, selected: list[str] | None = None, style: str = "take",
                 where: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Remplir la rangée")
        self.resize(460, 560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(10)
        if where:
            lay.addWidget(label(where, "Hint", True))
        lay.addWidget(label("STYLE", "H2"))
        self.style = tame_combo(QComboBox(), 16)
        for v, text in ROW_STYLES:
            self.style.addItem(text, v)
        self.style.setCurrentIndex(max(0, self.style.findData(style)))
        lay.addWidget(self.style)
        lay.addWidget(label("INPUTS VMIX (DANS L'ORDRE)", "H2"))
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.NoSelection)
        selected = selected or []
        st = engine.vmix.state
        for i in st.inputs:
            it = QListWidgetItem(f"{i['number']:>2}   {i['title']}")
            it.setData(Qt.UserRole, i["title"])
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if i["title"] in selected else Qt.Unchecked)
            self.list.addItem(it)
        lay.addWidget(self.list, 1)
        if not st.inputs:
            lay.addWidget(label("vMix n'est pas connecté : aucun input à proposer.", "Hint", True))
        lay.addWidget(label("Les touches sont posées de gauche à droite à partir de la touche choisie ; au bout de "
                            "la rangée, on passe à la suivante. Ctrl+Z annule tout d'un coup.", "Hint", True))
        btns = QHBoxLayout()
        btns.addStretch()
        ok = QPushButton("Remplir")
        ok.setObjectName("Primary")
        ok.clicked.connect(self.accept)
        cancel = QPushButton("Annuler")
        cancel.clicked.connect(self.reject)
        btns.addWidget(ok)
        btns.addWidget(cancel)
        lay.addLayout(btns)

    def titles(self) -> list[str]:
        return [self.list.item(r).data(Qt.UserRole) for r in range(self.list.count())
                if self.list.item(r).checkState() == Qt.Checked]


class ShortcutsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Raccourcis et légende")
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(10)
        lay.addWidget(label("RACCOURCIS CLAVIER", "H2"))
        rows = "".join(f"<tr><td style='padding:3px 16px 3px 0'><b>{k}</b></td>"
                       f"<td style='color:{T.MUTED}'>{v}</td></tr>" for k, v in SHORTCUTS)
        lay.addWidget(QLabel(f"<table>{rows}</table>"))
        lay.addSpacing(6)
        lay.addWidget(label("LÉGENDE", "H2"))
        lay.addWidget(label(LEGEND, "", True))
        ok = QPushButton("Fermer")
        ok.clicked.connect(self.accept)
        lay.addWidget(ok, 0, Qt.AlignRight)


class UpdateDialog(QDialog):
    """Nouvelle version : notes, téléchargement avec progression, puis installation silencieuse."""
    progress = Signal(int, int)
    downloaded = Signal(str)  # message d'erreur, vide si le fichier est prêt

    def __init__(self, parent, rel: updater.Release, busy: bool):
        super().__init__(parent)
        self.rel, self.installing, self._path = rel, False, None
        self.setWindowTitle("Mise à jour de LiveDeck")
        self.setMinimumWidth(520)
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(10)
        v.addWidget(label(f"LiveDeck {rel.version} est disponible", "H1"))
        v.addWidget(label(f"Version installée : {__version__}", "Muted"))
        notes = QPlainTextEdit(rel.notes or "Pas de notes pour cette version.")
        notes.setReadOnly(True)
        notes.setMinimumHeight(180)
        v.addWidget(notes)
        self.info = label("", "Hint", True)
        v.addWidget(self.info)
        if updater.can_install():
            self.info.setText(("⚠  Mode Live ou verrouillé : l'installation ferme LiveDeck, les Stream Decks s'arrêtent "
                               "quelques secondes. " if busy else "")
                              + "L'installation ferme LiveDeck puis le relance. Ta configuration est conservée.")
        else:
            self.info.setText("Cette copie tourne depuis les sources : mets-la à jour avec « git pull », ou télécharge "
                              "l'installateur depuis la page de la release.")
        self.bar = QProgressBar()
        self.bar.hide()
        v.addWidget(self.bar)
        row = QHBoxLayout()
        row.addStretch()
        self.go = QPushButton("Installer et redémarrer" if updater.can_install() else "Ouvrir la page de la release")
        self.go.setObjectName("Primary")
        self.go.clicked.connect(self._start)
        later = QPushButton("Plus tard")
        later.clicked.connect(self.reject)
        row.addWidget(self.go)
        row.addWidget(later)
        v.addLayout(row)
        self.progress.connect(self._progress)
        self.downloaded.connect(self._downloaded)

    def _start(self) -> None:
        if not updater.can_install():
            QDesktopServices.openUrl(QUrl(updater.PAGE))
            return
        self.go.setEnabled(False)
        self.bar.setRange(0, 0)
        self.bar.show()
        self.info.setText("Téléchargement…")

        def work():
            try:
                self._path = updater.download(self.rel, lambda d, t: self.progress.emit(d, t))
                self.downloaded.emit("")
            except updater.UpdateError as e:
                self.downloaded.emit(str(e))
            except Exception as e:
                self.downloaded.emit(f"Téléchargement impossible : {e}")
        threading.Thread(target=work, name="livedeck-download", daemon=True).start()

    def _progress(self, done: int, total: int) -> None:
        if total:
            self.bar.setRange(0, total)
            self.bar.setValue(done)

    def _downloaded(self, error: str) -> None:
        if error:
            self.bar.hide()
            self.go.setEnabled(True)
            self.info.setText(error)
            return
        try:
            updater.launch_installer(self._path)
        except OSError as e:
            self.bar.hide()
            self.go.setEnabled(True)
            self.info.setText(f"Impossible de lancer l'installateur : {e}")
            return
        self.installing = True
        self.accept()
