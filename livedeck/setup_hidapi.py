"""Télécharge hidapi.dll (bibliothèque USB nécessaire aux Stream Decks sous Windows)."""
import io
import sys
import urllib.request
import zipfile

from .deck import LIB_DIR

URL = "https://github.com/libusb/hidapi/releases/download/hidapi-0.15.0/hidapi-win.zip"


def main() -> None:
    target = LIB_DIR / "hidapi.dll"
    if target.exists():
        print(f"hidapi déjà installée : {target}")
        return
    print(f"Téléchargement de {URL}…")
    data = urllib.request.urlopen(URL, timeout=60).read()
    arch = "x64" if sys.maxsize > 2**32 else "x86"
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        name = next(n for n in z.namelist() if n.endswith(f"{arch}/hidapi.dll"))
        LIB_DIR.mkdir(parents=True, exist_ok=True)
        target.write_bytes(z.read(name))
    print(f"hidapi installée : {target}")


if __name__ == "__main__":
    main()
