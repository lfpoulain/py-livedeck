"""Surveillance d'équipements réseau (ping ou HTTP) et lecture d'API JSON pour colorer un bouton."""
from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import webreq


def parse_targets(text: str) -> list[tuple[str, str]]:
    """« Caméra 1 = 192.168.1.50 » ou « Régie = http://192.168.1.20/status », une cible par ligne."""
    out = []
    for line in (text or "").splitlines():
        name, sep, addr = line.partition("=")
        addr = addr.strip() if sep else name.strip()
        if addr:
            out.append((name.strip() if sep else addr, addr))
    return out


def ping(host: str, timeout_ms: int = 1000) -> bool:
    if sys.platform == "win32":
        cmd = ["ping", "-n", "1", "-w", str(timeout_ms), host]
        flags = subprocess.CREATE_NO_WINDOW
    else:
        cmd = ["ping", "-c", "1", "-W", str(max(1, timeout_ms // 1000)), host]
        flags = 0
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout_ms / 1000 + 2, creationflags=flags)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0 and b"TTL=" in r.stdout.upper()


def check(address: str) -> bool:
    if not address.lower().startswith(("http://", "https://")):
        return ping(address)
    try:
        status, text = webreq.request("GET", address, timeout=2.5)
    except OSError:
        return False
    if not 200 <= status < 300:
        return False
    try:  # si l'équipement renvoie {"connected": false}, on le considère hors ligne
        data = json.loads(text)
        if isinstance(data, dict) and "connected" in data:
            return bool(data["connected"])
    except ValueError:
        pass
    return True


class HealthMonitor:
    def __init__(self, on_change):
        self._on_change = on_change
        self.results: dict[str, bool] = {}
        self.targets: set[str] = set()
        self.interval = 15
        self._pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="health")
        self._stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run, name="health", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def set_targets(self, targets: set[str]) -> None:
        new = targets - self.targets
        self.targets = set(targets)
        if new:
            self.check_now(new)

    def check_now(self, addresses=None) -> None:
        for a in list(addresses or self.targets):
            self._pool.submit(self._check_one, a)

    def _check_one(self, address: str) -> None:
        ok = check(address)
        if self.results.get(address) != ok:
            self.results[address] = ok
            self._on_change()

    def _run(self) -> None:
        while not self._stop.is_set():
            self.check_now()
            self._stop.wait(max(3, self.interval))


# --- valeur lue dans une API JSON ---------------------------------------------------------------

JSON_OPS = [("==", "égal à"), ("!=", "différent de"), (">", "supérieur à"), ("<", "inférieur à"),
            ("contains", "contient"), ("truthy", "vrai / non vide"), ("always", "toujours (afficher la valeur)"),
            ("error", "API injoignable / en erreur")]
_MISSING = object()


def json_path(data, path: str):
    """« data.items[0].name », « items.0.name » ou « [2] » ; chemin vide = toute la réponse."""
    cur = data
    for part in re.findall(r"[^.\[\]]+|\[\d+\]", (path or "").strip()):
        key = part.strip("[]")
        if isinstance(cur, list):
            try:
                cur = cur[int(key)]
            except (ValueError, IndexError):
                return _MISSING
        elif isinstance(cur, dict) and key in cur:
            cur = cur[key]
        else:
            return _MISSING
    return cur


def _num(v):
    if isinstance(v, bool):
        return float(v)
    try:
        return float(str(v).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None


def json_text(v) -> str:
    """Valeur lisible sur une touche."""
    if v is _MISSING or v is None:
        return "—"
    if isinstance(v, bool):
        return "oui" if v else "non"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)[:40]
    return str(v)


def json_compare(value, op: str, expected: str) -> bool:
    if value is _MISSING:
        return False
    if op == "always":
        return True
    if op == "truthy":
        return bool(value) and str(value).lower() not in ("0", "false", "off", "no", "non")
    if op == "contains":
        if isinstance(value, (list, tuple)):
            return any(json_text(x).lower() == str(expected).lower() for x in value)
        return str(expected).lower() in json_text(value).lower()
    if isinstance(value, bool) and op in ("==", "!="):  # {"live": true} : « == true », « == oui », « == 1 »…
        exp = str(expected or "").strip().lower()
        words = ("true", "1", "oui", "on", "yes", "vrai") if value else ("false", "0", "non", "off", "no", "faux")
        return (exp in words) == (op == "==")
    a, b = _num(value), _num(expected)
    if op in (">", "<"):
        return a is not None and b is not None and (a > b if op == ">" else a < b)
    if a is not None and b is not None:
        same = a == b
    else:
        same = json_text(value).strip().lower() == str(expected or "").strip().lower()
    return same if op == "==" else not same


JSON_MAX = 4 * 1024 * 1024


class JsonPoller:
    """Interroge des URL JSON à intervalle régulier (une requête à la fois par URL)."""

    def __init__(self, on_change):
        self._on_change = on_change
        self.results: dict[str, dict] = {}  # url -> {"ok": bool, "data": …, "error": str}
        self.targets: dict[str, float] = {}  # url -> intervalle (s)
        self._due: dict[str, float] = {}
        self._busy: set[str] = set()
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="json")
        self._stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run, name="json", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def set_targets(self, targets: dict[str, float]) -> None:
        self.targets = {u: max(1.0, float(i or 5)) for u, i in targets.items() if u}
        for u in list(self.results):
            if u not in self.targets:
                self.results.pop(u, None)
        for u in self.targets:
            self._due.setdefault(u, 0.0)

    def check_now(self, url: str) -> None:
        self._due[url] = 0.0

    def _fetch(self, url: str) -> None:
        try:
            status, text = webreq.request("GET", url, timeout=4, max_bytes=JSON_MAX)
            if len(text.encode("utf-8")) >= JSON_MAX:
                new = {"ok": False, "error": "réponse trop volumineuse (4 Mo max)"}
            elif not 200 <= status < 300:
                new = {"ok": False, "error": f"HTTP {status}"}
            else:
                new = {"ok": True, "data": json.loads(text or "null")}
        except ValueError:
            new = {"ok": False, "error": "réponse non JSON"}
        except OSError as e:
            new = {"ok": False, "error": webreq.error_text(e, "")}
        finally:
            self._busy.discard(url)
        if url in self.targets and new != self.results.get(url):
            self.results[url] = new
            self._on_change()

    def _run(self) -> None:
        while not self._stop.wait(0.25):
            now = time.monotonic()
            for url, interval in list(self.targets.items()):
                if url not in self._busy and now >= self._due.get(url, 0.0):
                    self._busy.add(url)
                    self._due[url] = now + interval
                    self._pool.submit(self._fetch, url)
