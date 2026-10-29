"""Point d'entrée de l'application."""
from __future__ import annotations

import getpass
import logging
import os
import sys

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor, QPalette
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

from . import __version__, autostart
from .engine import Engine
from .model import HOME, migrate_legacy
from .ui import theme
from .ui.main_window import MainWindow


def _already_running(name: str) -> bool:
    """Une seule instance : deux LiveDeck se disputeraient les Stream Decks. On réveille la première."""
    sock = QLocalSocket()
    sock.connectToServer(name)
    if not sock.waitForConnected(300):
        return False
    sock.write(b"show")
    sock.flush()
    sock.waitForBytesWritten(300)
    sock.disconnectFromServer()
    return True


def main() -> None:
    args = sys.argv[1:]
    HOME.mkdir(parents=True, exist_ok=True)
    handlers = [logging.FileHandler(HOME / "livedeck.log", encoding="utf-8")]
    if sys.stderr:  # absent avec pythonw et l'exe
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=handlers)
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

    server_name = f"LiveDeck-{getpass.getuser()}"
    if "--selftest" not in args and _already_running(server_name):
        return
    migrate_legacy()
    engine = Engine(enable_decks=not os.environ.get("LIVEDECK_NO_DECK"))
    win = MainWindow(engine)
    server = QLocalServer()
    QLocalServer.removeServer(server_name)
    server.listen(server_name)
    server.newConnection.connect(lambda: (server.nextPendingConnection(), win.bring_to_front()))
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
    decks = (engine.decks.error or f"{len(engine.decks.info())} deck(s)") if engine.decks else "désactivés"
    (HOME / "selftest.txt").write_text(
        f"OK LiveDeck {__version__}\npages={len(engine.pages)}\nvmix={'connecté' if st.connected else 'hors ligne'}\n"
        f"decks={decks}\nclavier={keyboard}\ntray={win.tray.isSystemTrayAvailable()}\n", encoding="utf-8")
    win.quit_app()
