"""Construit LiveDeck (dist/LiveDeck/LiveDeck.exe, sans console) puis son installateur
dist/LiveDeck-<version>-win64-setup.exe (Inno Setup 6 : winget install JRSoftware.InnoSetup).

    pip install -r requirements-build.txt
    python tools/build_exe.py [--no-installer]

La configuration vit dans %APPDATA%\\LiveDeck : installer, mettre à jour ou désinstaller n'y touche pas.
hidapi.dll (USB des Stream Decks) est embarquée : lancer setup.bat au moins une fois avant.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
DIST = ROOT / "dist"
sys.path.insert(0, str(ROOT))

from livedeck import __version__  # noqa: E402

VERSION_INFO = """VSVersionInfo(
  ffi=FixedFileInfo(filevers={t}, prodvers={t}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040C04B0', [
      StringStruct('CompanyName', 'LiveDeck'),
      StringStruct('FileDescription', 'LiveDeck - pilotage vMix et Stream Deck'),
      StringStruct('FileVersion', '{v}'),
      StringStruct('InternalName', 'LiveDeck'),
      StringStruct('OriginalFilename', 'LiveDeck.exe'),
      StringStruct('ProductName', 'LiveDeck'),
      StringStruct('ProductVersion', '{v}')])]),
    VarFileInfo([VarStruct('Translation', [1036, 1200])])
  ]
)
"""


def make_icon(path: Path):
    """Icône multi-tailles dessinée par l'app elle-même."""
    from PIL import Image
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtWidgets import QApplication

    from livedeck.ui.main_window import icon_pixmap

    make_icon.app = QApplication.instance() or QApplication(["build"])  # requis par QPixmap
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    icon_pixmap(256).save(buf, "PNG")
    png = BUILD / "icon-256.png"
    png.write_bytes(bytes(buf.data()))
    Image.open(png).save(path, sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])


def find_iscc() -> Path | None:
    found = shutil.which("ISCC")
    if found:
        return Path(found)
    roots = [os.environ.get(v) for v in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")]
    for root in filter(None, roots):
        for sub in ("Inno Setup 6", r"Programs\Inno Setup 6"):
            iscc = Path(root) / sub / "ISCC.exe"
            if iscc.exists():
                return iscc
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--version", default=__version__,
                    help="version attendue (la compilation échoue si elle diffère de livedeck.__version__)")
    ap.add_argument("--no-installer", action="store_true", help="ne produit que le dossier dist/LiveDeck")
    args = ap.parse_args()
    if args.version.lstrip("v") != __version__:
        sys.exit(f"Version demandée {args.version} != livedeck.__version__ {__version__}.")
    hidapi = ROOT / "livedeck" / "lib" / "hidapi.dll"
    if not hidapi.exists():
        sys.exit("hidapi.dll absente : lance d'abord setup.bat (ou python -m livedeck.setup_hidapi).")

    import PyInstaller.__main__

    BUILD.mkdir(exist_ok=True)
    ico = BUILD / "livedeck.ico"
    make_icon(ico)
    parts = (__version__.split(".") + ["0", "0", "0"])[:4]
    vfile = BUILD / "version_info.txt"
    vfile.write_text(VERSION_INFO.format(t=tuple(int(x) for x in parts), v=__version__), encoding="utf-8")

    shutil.rmtree(DIST / "LiveDeck", ignore_errors=True)
    PyInstaller.__main__.run([
        str(ROOT / "main.py"),
        "--name", "LiveDeck",
        "--onedir", "--windowed", "--noconfirm", "--clean",
        "--icon", str(ico),
        "--version-file", str(vfile),
        "--add-data", f"{ROOT / 'livedeck' / 'ui' / 'assets'};livedeck/ui/assets",
        "--add-binary", f"{hidapi};livedeck/lib",
        "--hidden-import", "pynput.keyboard._win32",
        "--hidden-import", "pynput.mouse._win32",
        "--exclude-module", "tkinter",
        "--distpath", str(DIST),
        "--workpath", str(BUILD / "work"),
        "--specpath", str(BUILD),
    ])
    shutil.rmtree(BUILD / "work", ignore_errors=True)
    exe = DIST / "LiveDeck" / "LiveDeck.exe"
    print(f"\nOK : {exe.relative_to(ROOT)}")
    if args.no_installer:
        return

    iscc = find_iscc()
    if not iscc:
        sys.exit("Inno Setup 6 introuvable (winget install JRSoftware.InnoSetup), ou relance avec --no-installer.")
    subprocess.run([str(iscc), f"/DAppVersion={__version__}", f"/DSourceDir={DIST / 'LiveDeck'}",
                    f"/DOutputDir={DIST}", f"/DIconFile={ico}", str(ROOT / "tools" / "installer.iss")], check=True)
    setup = DIST / f"LiveDeck-{__version__}-win64-setup.exe"
    print(f"\nOK : {setup.relative_to(ROOT)}  ({setup.stat().st_size / 1e6:.1f} Mo)")


if __name__ == "__main__":
    main()
