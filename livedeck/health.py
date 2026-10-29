"""Surveillance d'équipements réseau (ping ou HTTP) pour colorer un bouton."""
from __future__ import annotations

import json
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

from . import webreq


def parse_targets(text: str) -> list[tuple[str, str]]:
    """« Reporter = 10.0.0.163 » ou « Général = http://10.0.0.72/status », une cible par ligne."""
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
