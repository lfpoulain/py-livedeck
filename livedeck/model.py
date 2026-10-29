"""Modèle de configuration LiveDeck : pages, boutons et decks, stockés en JSON.

Les données vivent dans %APPDATA%\\LiveDeck, indépendamment de l'exe : on peut remplacer
l'exe par une nouvelle version (ou lancer depuis les sources) et retrouver la même
configuration. LIVEDECK_HOME permet de choisir un autre dossier (clé USB, tests…).
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import sys
import uuid
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
HOME = Path(os.environ.get("LIVEDECK_HOME") or Path(os.environ.get("APPDATA") or Path.home()) / "LiveDeck")
CONFIG_PATH = HOME / "config.json"
# ancien emplacement (v1.0) : dossier data/ à côté des sources ou de l'exe
LEGACY_DIR = (Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else APP_DIR) / "data"


def migrate_legacy() -> None:
    old = LEGACY_DIR / "config.json"
    if old.exists() and not CONFIG_PATH.exists() and not os.environ.get("LIVEDECK_HOME"):
        HOME.mkdir(parents=True, exist_ok=True)
        shutil.copy2(old, CONFIG_PATH)

# Taille des grilles (colonnes, rangées) par modèle de Stream Deck
DECK_SIZES = {
    "Stream Deck XL (8×4)": (8, 4),
    "Stream Deck MK.2 / Original (5×3)": (5, 3),
    "Stream Deck + / Neo (4×2)": (4, 2),
    "Stream Deck Mini (3×2)": (3, 2),
}


def new_id() -> str:
    return uuid.uuid4().hex[:10]


def key(r: int, c: int) -> str:
    return f"{r},{c}"


def parse_key(k: str) -> tuple[int, int]:
    r, c = k.split(",")
    return int(r), int(c)


BUTTON_DEFAULTS = {
    "label": "",
    "bg": "#1f2937",
    "fg": "#ffffff",
    "size": 0,  # 0 = taille de texte automatique
    "icon": "",
    "mode": "press",  # press | toggle
    "actions": [],
    "actions_off": [],
    "long_actions": [],  # appui long (mode press)
    "confirm": False,  # sécurité : il faut maintenir pour déclencher
    "auto_feedback": True,
    "feedbacks": [],
}


def new_button(**kw) -> dict:
    b = {"id": new_id(), **copy.deepcopy(BUTTON_DEFAULTS)}
    b.update(kw)
    return b


def normalize_button(b: dict) -> dict:
    for k, v in BUTTON_DEFAULTS.items():
        b.setdefault(k, copy.deepcopy(v))
    b.setdefault("id", new_id())
    return b


def new_page(name: str, cols: int = 8, rows: int = 4) -> dict:
    return {"id": new_id(), "name": name, "cols": cols, "rows": rows, "buttons": {}}


def default_config() -> dict:
    return {
        "version": 2,
        "vmix": {"host": "127.0.0.1", "port": 8099},
        "pages": [new_page("Régie")],
        "decks": {},  # serial -> {"page": page_id, "brightness": 80}
        "integrations": {"intercom": {"host": "127.0.0.1", "port": 5080}, "orchestream": {"url": ""},
                         "tallies": []},
        "options": {"health_interval": 15, "start_live": False, "close_to_tray": True},
    }


def normalize_config(cfg: dict) -> dict:
    base = default_config()
    for k, v in base.items():
        cfg.setdefault(k, v)
    for k, v in base["options"].items():
        cfg["options"].setdefault(k, v)
    for k, v in base["integrations"].items():
        cfg["integrations"].setdefault(k, v)
    if not cfg["pages"]:
        cfg["pages"] = base["pages"]
    for p in cfg["pages"]:
        p.setdefault("id", new_id())
        p.setdefault("cols", 8)
        p.setdefault("rows", 4)
        p.setdefault("buttons", {})
        for b in p["buttons"].values():
            normalize_button(b)
    return cfg


def load_config(path: Path = CONFIG_PATH) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
    except FileNotFoundError:
        return default_config()
    old = cfg.get("version", 1)
    cfg = normalize_config(cfg)
    if old < 2:  # v1 : bricolages hérités de Companion -> intégrations natives
        from .integrations import migrate
        migrate(cfg)
        cfg["version"] = 2
    return cfg


def save_config(cfg: dict, path: Path = CONFIG_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
