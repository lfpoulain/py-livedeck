"""Modèle de configuration LiveDeck : pages, boutons et decks, stockés en JSON.

Les données vivent dans %APPDATA%\\LiveDeck, indépendamment de l'exe : on peut remplacer
l'exe par une nouvelle version (ou lancer depuis les sources) et retrouver la même
configuration. LIVEDECK_HOME permet de choisir un autre dossier (clé USB, tests…).
"""
from __future__ import annotations

import copy
import json
import logging
import os
import shutil
import sys
import time
import uuid
from pathlib import Path

log = logging.getLogger("livedeck")

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
    "Stream Deck + XL (9×4)": (9, 4),
    "Stream Deck Studio (16×2)": (16, 2),
    "Stream Deck Pedal (3 pédales)": (3, 1),
    "Loupedeck CT / Live (4×3)": (4, 3),
    "Loupedeck Live S (5×3)": (5, 3),
}


def deck_size(model: str) -> tuple[int, int]:
    """Grille (colonnes, rangées) d'après le nom d'un modèle (Companion, python-elgato-streamdeck…)."""
    low = (model or "").lower().replace("plus", "+")
    # Loupedeck / Razer : pavé tactile 4×3, sauf la Live S et la Stream Controller X en 5×3.
    if "loupedeck" in low or "stream controller" in low:
        return (5, 3) if "live s" in low or low.rstrip().endswith(" x") else (4, 3)
    for test, size in ((lambda m: "+ xl" in m or "+xl" in m, (9, 4)), (lambda m: "studio" in m, (16, 2)),
                       (lambda m: "pedal" in m, (3, 1)), (lambda m: "xl" in m, (8, 4)),
                       (lambda m: "mini" in m, (3, 2)), (lambda m: "neo" in m or "+" in m, (4, 2))):
        if test(low):
            return size
    return (5, 3)


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


def control_kind(cid: str) -> str:
    from .loupedeck import control_kind as kind  # import tardif : loupedeck ne dépend pas du modèle
    return kind(cid)


def new_page(name: str, cols: int = 8, rows: int = 4, surface: str = "") -> dict:
    """surface : « loupedeck » pour dessiner molettes, bandeaux et molette centrale ;
    vide = déduit de l'appareil qui affiche la page (Stream Deck par défaut)."""
    return {"id": new_id(), "name": name, "cols": cols, "rows": rows, "surface": surface,
            "buttons": {}, "controls": {}}


def surface_of(model: str) -> str:
    """Nature de la surface d'après un nom de modèle (entrée de DECK_SIZES ou modèle détecté)."""
    low = (model or "").lower()
    return "loupedeck" if "loupedeck" in low or "stream controller" in low else "streamdeck"


# Contrôles hors grille (molettes, bandeaux, boutons physiques du Loupedeck). Ils se comportent
# comme des touches : mêmes actions, mêmes retours d'état, mêmes modes. Une molette ajoute deux
# listes d'actions (un sens de rotation chacune) et un pas d'accélération.
CONTROL_DEFAULTS = {
    **{k: v for k, v in BUTTON_DEFAULTS.items()},
    "kind": "button",      # button | rotary
    "actions_cw": [],      # rotation horaire
    "actions_ccw": [],     # rotation antihoraire
    "step": 1,             # nombre de crans à accumuler avant de déclencher
    "haptic": "court",     # retour vibratoire à l'appui ("" pour aucun)
}
CONTROL_PREFIX = "c:"     # une case « c:knobTL » désigne un contrôle, pas une case de la grille


def control_key(cid: str) -> str:
    return f"{CONTROL_PREFIX}{cid}"


def parse_control(k: str) -> str | None:
    """Identifiant du contrôle désigné par une case, ou None si c'est une case de grille."""
    return k[len(CONTROL_PREFIX):] if k.startswith(CONTROL_PREFIX) else None


def new_control(kind: str = "button", **kw) -> dict:
    c = {"id": new_id(), **copy.deepcopy(CONTROL_DEFAULTS), "kind": kind}
    c.update(kw)
    return c


def normalize_control(c: dict) -> dict:
    for k, v in CONTROL_DEFAULTS.items():
        c.setdefault(k, copy.deepcopy(v))
    c.setdefault("id", new_id())
    return c


CONFIG_VERSION = 4  # 2 : intégrations natives ; 3 : FadeToBlack n'est plus une transition ;
#                     4 : contrôles hors grille (molettes, bandeaux et boutons des Loupedeck)


def _migrate_v3(cfg: dict) -> None:
    """FadeToBlack (interrupteur vMix) n'est plus proposé comme transition : une touche « Transition
    FadeToBlack » devient « Rec / Stream / FTB » (fondu au noir, basculer) ; un Take passe en Cut."""
    for p in cfg["pages"]:
        for b in p["buttons"].values():
            for lst in ("actions", "actions_off", "long_actions"):
                for i, a in enumerate(b.get(lst) or []):
                    if a.get("effect") != "FadeToBlack":
                        continue
                    if a.get("type") == "vmix.transition":
                        b[lst][i] = {"type": "vmix.production", "what": "fadetoblack", "mode": "toggle"}
                    else:
                        a["effect"] = "Cut"


def default_config() -> dict:
    return {
        "version": CONFIG_VERSION,
        "vmix": {"host": "127.0.0.1", "port": 8099},
        "pages": [new_page("Régie")],
        "decks": {},  # serial -> {"page": page_id, "brightness": 80}
        "integrations": {"intercom": {"host": "127.0.0.1", "port": 5080}, "orchestream": {"url": ""},
                         "tallies": [], "multiview": {"url": ""}},
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
        p.setdefault("controls", {})
        p.setdefault("surface", "")
        for b in p["buttons"].values():
            normalize_button(b)
        for cid, c in p["controls"].items():
            normalize_control(c)
            # Un bouton ne tourne pas : une action de rotation posée là ne partirait jamais.
            # (Les versions précédentes la conservaient en déposant un réglage de molette dessus.)
            if control_kind(cid) != "rotary" and (c["actions_cw"] or c["actions_ccw"]):
                if not c["actions"]:
                    c["actions"] = c["actions_cw"] + c["actions_ccw"]
                c["actions_cw"], c["actions_ccw"] = [], []
    return cfg


def load_config(path: Path = CONFIG_PATH) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
    except FileNotFoundError:
        return default_config()
    if not isinstance(cfg, dict) or not isinstance(cfg.get("pages", []), list):
        raise ValueError("ce n'est pas une configuration LiveDeck")
    old = cfg.get("version", 1)
    cfg = normalize_config(cfg)
    if old < 2:  # v1 : bricolages hérités de Companion -> intégrations natives
        from .integrations import migrate
        notes = migrate(cfg)  # les raccourcis Intercom remplacés restent en secours (fallback_keys)
        if notes:  # clé « _ » : jamais écrite sur disque, à montrer une fois par l'interface
            cfg["_migration_notes"] = notes
        cfg["version"] = 2
    if old < 3:
        _migrate_v3(cfg)
        cfg["version"] = 3
    if old < 4:
        cfg["version"] = 4  # normalize_config a déjà ajouté le bloc « controls » vide de chaque page
    return cfg


def open_config(path: Path = CONFIG_PATH, retries: int = 5) -> tuple[dict, str]:
    """Comme load_config, mais un fichier abîmé n'empêche pas de démarrer : il est mis de côté
    (config.json.corrupt-<date>) et LiveDeck repart d'une configuration vierge. Renvoie (cfg, alerte).
    Un fichier momentanément verrouillé (antivirus, OneDrive, éditeur) n'est PAS abîmé : on réessaie, puis on
    démarre sans jamais écrire (cfg["_readonly"]) pour ne pas remplacer la vraie configuration."""
    for attempt in range(retries):
        try:
            return load_config(path), ""
        except OSError as e:  # verrouillé, droits… : le contenu n'est pas en cause
            if attempt < retries - 1:
                time.sleep(0.2)
                continue
            cfg = default_config()
            cfg["_readonly"] = True
            msg = (f"Configuration impossible à lire ({e}) : LiveDeck démarre avec une configuration vierge et "
                   f"n'enregistrera RIEN, pour ne pas écraser « {Path(path).name} ». Ferme ce qui bloque le fichier "
                   "(OneDrive, antivirus, éditeur) puis relance LiveDeck.")
            log.error(msg)
            return cfg, msg
        except Exception as e:  # JSON invalide, encodage, structure inattendue…
            bad = Path(path).with_name(f"{Path(path).name}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}")
            cfg = default_config()
            try:
                os.replace(path, bad)
                where = f"copie mise de côté dans « {bad.name} »"
            except OSError:  # impossible de la mettre de côté : on ne l'écrasera pas non plus
                cfg["_readonly"] = True
                where = f"« {Path(path).name} » n'a pas pu être mis de côté, LiveDeck n'enregistrera rien"
            msg = (f"Configuration illisible ({e}) : {where}, LiveDeck démarre avec une configuration vierge. "
                   f"Les sauvegardes automatiques sont dans « {BACKUP_DIR_NAME} ».")
            log.error(msg)
            return cfg, msg
    raise AssertionError("inaccessible")


BACKUP_DIR_NAME = "backups"
BACKUP_KEEP = 20


def backup_config(path: Path = CONFIG_PATH, tag: str = "") -> Path | None:
    """Copie datée de config.json dans backups/ (on garde les BACKUP_KEEP plus récentes)."""
    path = Path(path)
    if not path.exists():
        return None
    folder = path.parent / BACKUP_DIR_NAME
    try:
        folder.mkdir(parents=True, exist_ok=True)
        dst = folder / f"config-{time.strftime('%Y%m%d-%H%M%S')}{'-' + tag if tag else ''}.json"
        shutil.copy2(path, dst)
        for old in sorted(folder.glob("config-*.json"))[:-BACKUP_KEEP]:
            old.unlink(missing_ok=True)
        return dst
    except OSError as e:
        log.warning("Sauvegarde de secours impossible : %s", e)
        return None


def has_backup_today(path: Path = CONFIG_PATH) -> bool:
    return any((Path(path).parent / BACKUP_DIR_NAME).glob(f"config-{time.strftime('%Y%m%d')}-*.json"))


def save_config_text(text: str, path: Path = CONFIG_PATH) -> None:
    """Écriture atomique : fichier temporaire puis os.replace (réessayé si l'antivirus ou OneDrive bloque)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        for attempt in range(4):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == 3:
                    raise
                time.sleep(0.1)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def config_text(cfg: dict) -> str:
    return json.dumps({k: v for k, v in cfg.items() if not k.startswith("_")}, ensure_ascii=False, indent=2)


def save_config(cfg: dict, path: Path = CONFIG_PATH) -> None:
    save_config_text(config_text(cfg), path)
