"""Publie une nouvelle version de LiveDeck.

    python tools/release.py 1.3.0            # prépare, commite et pousse sur main
    python tools/release.py 1.3.0 --dry-run  # vérifie seulement

Étapes :
  1. vérifie qu'on est sur main, que le dépôt est propre et que CHANGELOG.md contient une section « ## 1.3.0 » ;
  2. met à jour livedeck/__init__.py ;
  3. lance le lint et les tests ;
  4. crée le commit « Version 1.3.0 » et le pousse.

Le push déclenche le workflow GitHub « CI » : il voit une version sans release, compile l'installateur,
crée le tag v1.3.0 et publie la release avec les notes du CHANGELOG.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INIT = ROOT / "livedeck" / "__init__.py"
CHANGELOG = ROOT / "CHANGELOG.md"


def git(*args: str, capture=True) -> str:
    r = subprocess.run(["git", *args], cwd=ROOT, text=True, capture_output=capture)
    if r.returncode:
        sys.exit(f"git {' '.join(args)} a échoué :\n{r.stderr}")
    return (r.stdout or "").strip()


def main():
    ap = argparse.ArgumentParser(description="Publie une nouvelle version de LiveDeck.")
    ap.add_argument("version", help="ex. 1.3.0")
    ap.add_argument("--dry-run", action="store_true", help="vérifier sans rien modifier")
    args = ap.parse_args()
    v = args.version.lstrip("v")
    if not re.fullmatch(r"\d+\.\d+\.\d+", v):
        sys.exit("La version doit être de la forme 1.2.3")

    if git("rev-parse", "--abbrev-ref", "HEAD") != "main":
        sys.exit("Passe d'abord sur la branche main.")
    if git("status", "--porcelain"):
        sys.exit("Le dépôt contient des modifications non commitées : commite-les d'abord.")
    git("fetch", "--tags", "--quiet")
    if git("tag", "--list", f"v{v}"):
        sys.exit(f"La version {v} est déjà publiée (tag v{v}).")
    if not re.search(rf"^## {re.escape(v)}\b", CHANGELOG.read_text(encoding="utf-8"), re.M):
        sys.exit(f"Ajoute d'abord une section « ## {v} — AAAA-MM-JJ » dans CHANGELOG.md.")

    print("Lint et tests…")
    checks = [[sys.executable, "-m", "pyflakes", "livedeck", "tests", "tools", "main.py"],
              [sys.executable, "tests/test_core.py"]]
    for cmd in checks:
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        if r.returncode:
            sys.exit(f"{' '.join(cmd[1:])} échoue : release annulée.\n{r.stdout[-2000:]}{r.stderr[-2000:]}")

    if args.dry_run:
        print(f"Tout est prêt pour la version {v} (dry-run : rien n'a été modifié).")
        return

    text = INIT.read_text(encoding="utf-8")
    new = re.sub(r'__version__ = "[^"]+"', f'__version__ = "{v}"', text)
    if new != text:
        INIT.write_text(new, encoding="utf-8")
        git("commit", "-am", f"Version {v}")
    git("push", "origin", "main", capture=False)
    print(f"\nVersion {v} poussée. Le workflow « CI » compile l'installateur et publie la release sur GitHub.")


if __name__ == "__main__":
    main()
