"""Extrait la section d'une version de CHANGELOG.md (notes de la release GitHub).

    python tools/release_notes.py 1.2.0 > notes.md
"""
import re
import sys
from pathlib import Path

CHANGELOG = Path(__file__).resolve().parent.parent / "CHANGELOG.md"

FOOTER = r"""
---

### Installation

1. Télécharge **`{exe}`** ci-dessous.
2. Range-le dans le dossier de ton choix (par ex. `Documents\LiveDeck`) et double-clique dessus.
3. Ferme Bitfocus Companion et le logiciel Elgato Stream Deck avant le premier lancement (un seul logiciel à la fois peut piloter un Stream Deck).

Mise à jour : quitte LiveDeck (clic droit sur l'icône près de l'horloge → Quitter) puis remplace simplement l'ancien exe.
Ta configuration est dans `%APPDATA%\LiveDeck` et n'est jamais touchée. Si le démarrage automatique est activé, il suit le nouvel exe dès son premier lancement.
Windows SmartScreen peut afficher un avertissement au premier lancement (exe non signé) : « Informations complémentaires » → « Exécuter quand même ».
"""


def notes(version: str) -> str:
    text = CHANGELOG.read_text(encoding="utf-8")
    m = re.search(rf"^## {re.escape(version)}\b.*?$(.*?)(?=^## |\Z)", text, re.M | re.S)
    if not m:
        sys.exit(f"Aucune section « ## {version} » dans CHANGELOG.md")
    return m.group(1).strip() + "\n" + FOOTER.format(exe=f"LiveDeck-{version}-win64.exe")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(notes(sys.argv[1].lstrip("v")))
