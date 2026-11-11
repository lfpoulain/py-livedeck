"""Mise à jour dans l'app : lit la dernière release GitHub, télécharge l'installateur et le lance.

Seule l'app installée (exe) se met à jour : l'installateur est silencieux, ferme LiveDeck, remplace les
fichiers et relance l'app. La configuration (%APPDATA%\\LiveDeck) n'est jamais touchée.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import __version__, webreq

REPO = "lfpoulain/py-livedeck"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
PAGE = f"https://github.com/{REPO}/releases/latest"
ASSET_SUFFIX = "-win64-setup.exe"


@dataclass
class Release:
    version: str
    notes: str
    url: str          # téléchargement de l'installateur
    name: str
    size: int
    sha256: str       # vide si GitHub ne le fournit pas


class UpdateError(Exception):
    """Message prêt à afficher."""


def parse_version(text: str) -> tuple[int, ...]:
    m = re.match(r"v?(\d+(?:\.\d+)*)", (text or "").strip())
    return tuple(int(x) for x in m.group(1).split(".")) if m else ()


def is_newer(candidate: str, current: str = __version__) -> bool:
    c = parse_version(candidate)
    return bool(c) and c > parse_version(current)


def can_install() -> bool:
    """Seul l'exe installé sait se remplacer ; depuis les sources, on passe par git."""
    return bool(getattr(sys, "frozen", False)) and sys.platform == "win32"


def release_notes(body: str) -> str:
    """Les notes de la release sans le pied « Installation » (inutile une fois dans l'app)."""
    return re.split(r"^---\s*$", body or "", maxsplit=1, flags=re.M)[0].strip()


def parse_release(data: dict) -> Release | None:
    """Release GitHub → Release, ou None s'il n'y a pas d'installateur dans ses fichiers."""
    for a in data.get("assets") or []:
        name = a.get("name") or ""
        if name.endswith(ASSET_SUFFIX) and a.get("browser_download_url"):
            digest = a.get("digest") or ""
            return Release(version=(data.get("tag_name") or "").lstrip("v"), notes=release_notes(data.get("body") or ""),
                           url=a["browser_download_url"], name=name, size=int(a.get("size") or 0),
                           sha256=digest.split(":", 1)[1].lower() if digest.startswith("sha256:") else "")
    return None


def latest() -> Release | None:
    """Dernière release publiée (None : aucune avec installateur). Lève UpdateError si GitHub est injoignable."""
    api = os.environ.get("LIVEDECK_UPDATE_API") or API
    try:
        status, text = webreq.request("GET", api, timeout=8, max_bytes=2_000_000, headers=(
            f"Accept: application/vnd.github+json\nUser-Agent: LiveDeck/{__version__}"))
    except webreq.NET_ERRORS as e:
        raise UpdateError(f"GitHub injoignable : {webreq.error_text(e)}") from e
    if status == 404:
        raise UpdateError("Aucune release publiée (ou dépôt privé : la mise à jour intégrée demande un dépôt public).")
    if status == 403 or status == 429:
        raise UpdateError("GitHub limite les requêtes : réessaie dans quelques minutes.")
    if status != 200:
        raise UpdateError(f"GitHub a répondu {status}.")
    try:
        return parse_release(json.loads(text))
    except (ValueError, AttributeError) as e:
        raise UpdateError("Réponse de GitHub illisible.") from e


def update_dir() -> Path:
    d = Path(tempfile.gettempdir()) / "LiveDeck-update"
    d.mkdir(parents=True, exist_ok=True)
    return d


def download(rel: Release, progress: Callable[[int, int], None] | None = None) -> Path:
    """Télécharge l'installateur dans le dossier temporaire et le vérifie (taille, SHA-256 si fourni)."""
    dest = update_dir() / (Path(rel.name).name or "LiveDeck-Setup.exe")
    part = dest.with_suffix(".part")
    req = urllib.request.Request(rel.url, headers={"User-Agent": f"LiveDeck/{__version__}"})
    h, done = hashlib.sha256(), 0
    try:
        with webreq.open_url(req, 30) as resp, open(part, "wb") as f:
            total = int(resp.headers.get("Content-Length") or rel.size or 0)
            while chunk := resp.read(256 * 1024):
                f.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
    except webreq.NET_ERRORS as e:
        part.unlink(missing_ok=True)
        raise UpdateError(f"Téléchargement interrompu : {webreq.error_text(e)}") from e
    if rel.size and done != rel.size:
        part.unlink(missing_ok=True)
        raise UpdateError(f"Téléchargement incomplet ({done} octets sur {rel.size}).")
    if rel.sha256 and h.hexdigest() != rel.sha256:
        part.unlink(missing_ok=True)
        raise UpdateError("Le fichier téléchargé ne correspond pas à l'empreinte SHA-256 de la release : annulé.")
    os.replace(part, dest)
    return dest


def launch_installer(path: Path) -> None:
    """Lance l'installateur en silencieux, détaché : il ferme LiveDeck, remplace les fichiers et le relance."""
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([str(path), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/RELAUNCH=1"],
                     creationflags=flags, close_fds=True)
