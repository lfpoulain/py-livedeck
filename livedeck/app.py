"""Point d'entrée de l'application."""
from __future__ import annotations

import getpass
import logging
import logging.handlers
import os
import sys
import threading
import time

from PySide6.QtCore import QLockFile, QTimer
from PySide6.QtGui import QColor, QPalette
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

from . import __version__, autostart
from .engine import Engine
from .model import HOME, migrate_legacy
from .ui import theme
from .ui.main_window import MainWindow


def _wake_first(name: str, wait_s: float = 5.0) -> None:
    """Demande à la première instance de se montrer. Elle peut être encore en train de démarrer (le socket
    n'écoute pas encore) : on réessaie quelques secondes."""
    deadline = time.monotonic() + wait_s
    while True:
        sock = QLocalSocket()
        sock.connectToServer(name)
        if sock.waitForConnected(300):
            sock.write(b"show")
            sock.flush()
            sock.waitForBytesWritten(300)
            sock.disconnectFromServer()
            return
        if time.monotonic() >= deadline:
            return
        time.sleep(0.1)


def _single_instance(name: str):
    """Une seule instance : deux LiveDeck se disputeraient les Stream Decks.

    Un verrou fichier (atomique, libéré par le système si le processus meurt) est pris dès le départ, avant le
    moteur : deux lancements rapprochés ne passent plus tous les deux. Renvoie (verrou, serveur) pour la première
    instance, None pour les suivantes (qui réveillent la première)."""
    HOME.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(HOME / "livedeck.lock"))
    if not lock.tryLock(0):
        _wake_first(name)
        return None
    server = QLocalServer()
    QLocalServer.removeServer(name)  # reste d'un arrêt brutal ; le verrou prouve qu'aucune autre instance ne vit
    if not server.listen(name):
        logging.getLogger("livedeck").warning("Socket d'instance unique indisponible : %s", server.errorString())
    return lock, server


def _install_logging() -> None:
    """Journal tournant (2 Mo × 3) et erreurs des threads journalisées : avec pythonw ou l'exe,
    il n'y a pas de console, une exception non rattrapée serait sinon invisible."""
    HOME.mkdir(parents=True, exist_ok=True)
    handlers = [logging.handlers.RotatingFileHandler(HOME / "livedeck.log", maxBytes=2_000_000, backupCount=3,
                                                     encoding="utf-8")]
    if sys.stderr:  # absent avec pythonw et l'exe
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=handlers)
    log = logging.getLogger("livedeck")

    def on_error(exc_type, exc, tb):
        if not issubclass(exc_type, KeyboardInterrupt):
            log.critical("Erreur non rattrapée", exc_info=(exc_type, exc, tb))

    def on_thread_error(a):
        if a.exc_type is not SystemExit:
            log.critical("Erreur non rattrapée dans le thread %s", getattr(a.thread, "name", "?"),
                         exc_info=(a.exc_type, a.exc_value, a.exc_traceback))

    sys.excepthook = on_error
    threading.excepthook = on_thread_error


def main() -> None:
    args = sys.argv[1:]
    _install_logging()
    if sys.platform == "win32":  # icône propre dans la barre des tâches
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("LiveDeck")
    app = QApplication(sys.argv)
    app.setApplicationName("LiveDeck")
    app.setApplicationVersion(__version__)
    app.setQuitOnLastWindowClosed(False)  # la fenêtre peut se réduire près de l'horloge
    app.setStyle("Fusion")
    pal = QPalette()
    for role, color in ((QPalette.Window, theme.BG), (QPalette.Base, theme.PANEL2), (QPalette.Text, theme.TEXT),
                        (QPalette.WindowText, theme.TEXT), (QPalette.Button, theme.RAISED),
                        (QPalette.ButtonText, theme.TEXT), (QPalette.Highlight, theme.ACCENT),
                        (QPalette.ToolTipBase, theme.RAISED), (QPalette.ToolTipText, theme.TEXT)):
        pal.setColor(role, QColor(color))
    app.setPalette(pal)
    app.setStyleSheet(theme.QSS)

    instance = None
    if "--selftest" not in args:
        instance = _single_instance(f"LiveDeck-{getpass.getuser()}")
        if instance is None:
            return
    migrate_legacy()
    engine = Engine(enable_decks=not os.environ.get("LIVEDECK_NO_DECK"))
    win = MainWindow(engine)
    if instance:
        server = instance[1]

        def on_connection():
            while server.hasPendingConnections():
                server.nextPendingConnection()
            win.bring_to_front()

        server.newConnection.connect(on_connection)
        if server.hasPendingConnections():  # un second lancement est arrivé pendant notre démarrage
            on_connection()
    engine.start()
    if engine.cfg["options"].get("start_live"):
        win.set_live(True)
    if "--tray" in args and win.tray.isVisible():
        win.tray.showMessage("LiveDeck", "LiveDeck est lancé : les Stream Decks sont actifs.", win.tray.icon(), 3000)
    else:
        win.show()
    try:
        autostart.resync()
    except OSError:
        pass
    if "--selftest" in args:
        QTimer.singleShot(2500, lambda: _selftest(win, engine))
    sys.exit(app.exec())


def _selftest(win, engine) -> None:
    st = engine.vmix.state
    try:  # crée le contrôleur clavier sans envoyer de touche : vérifie que pynput est bien embarqué
        from . import hotkeys
        hotkeys._controller()
        keyboard = "ok"
    except Exception as ex:
        keyboard = f"erreur {ex}"
    try:  # pyserial bien embarqué : sans lui, aucun Loupedeck ne serait détecté dans l'exe installé
        from serial.tools import list_ports
        list_ports.comports()
        loupedeck = "ok"
    except Exception as ex:
        loupedeck = f"erreur {ex}"
    decks = (engine.decks.error or f"{len(engine.decks.info())} deck(s)") if engine.decks else "désactivés"
    (HOME / "selftest.txt").write_text(
        f"OK LiveDeck {__version__}\npages={len(engine.pages)}\nvmix={'connecté' if st.connected else 'hors ligne'}\n"
        f"decks={decks}\nclavier={keyboard}\nloupedeck={loupedeck}\n"
        f"tray={win.tray.isSystemTrayAvailable()}\n", encoding="utf-8")
    win.quit_app()
