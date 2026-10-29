"""Lancement de LiveDeck à l'ouverture de session Windows (clé Run de l'utilisateur)."""
from __future__ import annotations

import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
NAME = "LiveDeck"


def command() -> str:
    """Commande lancée au démarrage : l'exe, ou pythonw + main.py depuis les sources."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --tray'
    pyw = Path(sys.executable).with_name("pythonw.exe")
    main = Path(__file__).resolve().parent.parent / "main.py"
    return f'"{pyw if pyw.exists() else sys.executable}" "{main}" --tray'


def _current() -> str | None:
    if sys.platform != "win32":
        return None
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            return winreg.QueryValueEx(k, NAME)[0]
    except OSError:
        return None


def is_enabled() -> bool:
    return _current() is not None


def set_enabled(on: bool) -> None:
    if sys.platform != "win32":
        return
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, NAME, 0, winreg.REG_SZ, command())
        else:
            try:
                winreg.DeleteValue(k, NAME)
            except FileNotFoundError:
                pass


def resync() -> None:
    """Si l'exe a été déplacé ou mis à jour, la clé pointe encore vers l'ancien chemin : on la corrige."""
    cur = _current()
    if cur is not None and cur != command():
        set_enabled(True)
