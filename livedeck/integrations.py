"""Intégrations maison : py-intercom (talkback), orchestream (overlays web), Tally32 (tallys ESP32).

Chaque client tourne dans son propre thread et ne travaille que si une touche l'utilise.
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

INTERCOM_BUSES = [("regie", "Régie"), ("plateau", "Plateau"), ("vmix", "vMix")]
ORCH_OVERLAYS = [("twizz", "Twizz"), ("poll", "Sondage"), ("speedizz", "Speedizz")]


def _http(method: str, url: str, timeout: float = 1.5) -> tuple[int, dict | None]:
    """Requête sans en-tête Origin (py-intercom refuse les navigateurs). Lève OSError si injoignable."""
    req = urllib.request.Request(url, data=b"" if method == "POST" else None, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status, raw = resp.status, resp.read(65536)
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read(65536)
    try:
        return status, json.loads(raw.decode("utf-8", "replace") or "null")
    except ValueError:
        return status, None


class _Service:
    def __init__(self, on_change, log):
        self._on_change, self._log = on_change, log
        self.wanted = False  # une touche utilise ce service
        self._stop = threading.Event()
        self._wake = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run, name=type(self).__name__, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def set_wanted(self, wanted: bool) -> None:
        if wanted != self.wanted:
            self.wanted = wanted
            self._wake.set()

    def _sleep(self, s: float) -> None:
        self._wake.wait(s)
        self._wake.clear()

    def _run(self) -> None:
        raise NotImplementedError


# --- py-intercom ----------------------------------------------------------------------------


class Intercom(_Service):
    """Pilotage du client py-intercom par son API HTTP locale (POST /ptt/<bus>/on|off|toggle)."""

    def __init__(self, on_change, log):
        super().__init__(on_change, log)
        self.host, self.port = "127.0.0.1", 5080
        self.reachable: bool | None = None
        self.online = False
        self.state_text = ""
        self.buses: dict[str, dict] = {}
        self._lock = threading.Lock()

    @property
    def base(self) -> str:
        return f"http://{self.host}:{self.port}"

    def configure(self, host: str, port: int) -> None:
        self.host, self.port = host or "127.0.0.1", int(port or 5080)
        self.reachable = None
        self._wake.set()

    def _apply(self, data: dict | None) -> None:
        if not isinstance(data, dict) or "buses" not in data:
            return
        sig = (self.reachable, self.online, self.state_text, json.dumps(self.buses, sort_keys=True))
        self.online = bool(data.get("online"))
        self.state_text = data.get("state", "")
        self.buses = {k.lower(): v for k, v in (data.get("buses") or {}).items()}
        if sig != (True, self.online, self.state_text, json.dumps(self.buses, sort_keys=True)):
            self._on_change()

    def _set_reachable(self, ok: bool) -> None:
        if ok != self.reachable:
            if self.reachable is not None or not ok:
                self._log("info" if ok else "warn", f"py-intercom {'joignable' if ok else 'injoignable'} ({self.base})")
            self.reachable = ok
            self._on_change()

    def _run(self) -> None:
        while not self._stop.is_set():
            if not self.wanted:
                self._sleep(5)
                continue
            try:
                status, data = _http("GET", f"{self.base}/status", timeout=1)
                self._set_reachable(True)
                self._apply(data)
            except OSError:
                self._set_reachable(False)
            self._sleep(0.35 if self.reachable else 2)

    def command(self, bus: str, cmd: str) -> None:
        url = f"{self.base}/ptt/{bus}/{cmd}"
        with self._lock:  # un « off » ne doit jamais doubler un « on »
            for attempt in range(3):
                try:
                    status, data = _http("POST", url, timeout=1.5)
                except OSError:
                    self._set_reachable(False)
                    self._log("error", f"py-intercom injoignable : vérifie que le client tourne et que "
                                       f"son API est active ({self.base})")
                    return
                self._set_reachable(True)
                if status == 503 and attempt < 2:
                    time.sleep(0.05)
                    continue
                break
        self._apply(data)
        if status == 409:
            self._log("warn", f"py-intercom : {(data or {}).get('error', 'commande refusée')}")
        elif status >= 400:
            self._log("error", f"py-intercom {status} : {(data or {}).get('error', url)}")
        self._wake.set()

    def bus(self, name: str) -> dict:
        key = {"0": "regie", "1": "plateau", "2": "vmix"}.get(str(name), str(name).lower())
        return self.buses.get(key, {})


# --- orchestream ----------------------------------------------------------------------------


class Orchestream(_Service):
    """Overlays web d'orchestream : commandes show/hide et état suivi en direct par le flux SSE."""

    def __init__(self, on_change, log):
        super().__init__(on_change, log)
        self.url = ""
        self.connected = False
        self.overlays: dict[str, bool | None] = {k: None for k, _ in ORCH_OVERLAYS}

    def configure(self, url: str) -> None:
        url = (url or "").strip().rstrip("/")
        if url and not url.startswith(("http://", "https://")):
            url = "http://" + url
        if url != self.url:
            self.url = url
            self.overlays = {k: None for k, _ in ORCH_OVERLAYS}
            self._wake.set()

    def _set(self, name: str, visible) -> None:
        visible = None if visible is None else bool(visible)
        if self.overlays.get(name) != visible:
            self.overlays[name] = visible
            self._on_change()

    def _set_connected(self, ok: bool) -> None:
        if ok != self.connected:
            self.connected = ok
            self._log("info" if ok else "warn", f"orchestream {'connecté' if ok else 'déconnecté'} ({self.url})")
            self._on_change()

    def _handle(self, event: str, data) -> None:
        m = re.fullmatch(r"(twizz|poll|speedizz):overlay:toggle", event)
        if m and isinstance(data, dict):
            self._set(m.group(1), data.get("isVisible"))
        elif event == "speedizz:snapshot":
            self._set("speedizz", bool(data and data.get("isVisible")))
        elif event == "speedizz:session:finished":
            self._set("speedizz", False)

    def _run(self) -> None:
        delay = 2
        while not self._stop.is_set():
            if not (self.wanted and self.url):
                self._set_connected(False)
                self._sleep(5)
                continue
            url = self.url
            try:
                with urllib.request.urlopen(f"{url}/api/overlay/events", timeout=45) as resp:
                    self._set_connected(True)
                    delay = 2
                    event, data = "", []
                    for raw in resp:
                        if self._stop.is_set() or url != self.url or not self.wanted:
                            break
                        line = raw.decode("utf-8", "replace").rstrip("\r\n")
                        if line.startswith("event:"):
                            event = line[6:].strip()
                        elif line.startswith("data:"):
                            data.append(line[5:].strip())
                        elif not line and event:
                            try:
                                self._handle(event, json.loads("\n".join(data) or "null"))
                            except ValueError:
                                pass
                            event, data = "", []
            except OSError:
                pass
            self._set_connected(False)
            self._sleep(delay)
            delay = min(delay * 2, 15)

    def command(self, name: str, mode: str) -> None:
        if not self.url:
            self._log("error", "orchestream : adresse non réglée (Réglages → Intégrations)")
            return
        show = (not self.overlays.get(name)) if mode == "toggle" else mode == "show"
        try:
            status, data = _http("POST", f"{self.url}/api/overlay/{name}/{'show' if show else 'hide'}", timeout=4)
        except OSError as e:
            self._log("error", f"orchestream injoignable : {e}")
            return
        if 200 <= status < 300:
            self._set(name, (data or {}).get("isVisible", show))
        else:
            msg = (data or {}).get("error") or (data or {}).get("message") or f"erreur {status}"
            self._log("error", f"orchestream · {dict(ORCH_OVERLAYS).get(name, name)} : {msg}")


# --- Tally32 --------------------------------------------------------------------------------


class Tallies(_Service):
    """Tallys ESP32 VTally-32 : état (GET /status) et reconnexion à vMix (POST /vmix/reconnect)."""

    def __init__(self, on_change, log):
        super().__init__(on_change, log)
        self.devices: list[dict] = []
        self.status: dict[str, dict] = {}  # hôte -> {"reachable", "connected", "live", "preview", …}
        self._pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="tally")

    def configure(self, devices: list[dict]) -> None:
        self.devices = [d for d in devices if d.get("host")]
        self.status = {h: s for h, s in self.status.items() if h in {d["host"] for d in self.devices}}
        self._wake.set()

    def _url(self, host: str, path: str) -> str:
        return f"{host if host.startswith('http') else 'http://' + host}{path}"

    def _poll(self, host: str) -> None:
        try:
            status, data = _http("GET", self._url(host, "/status"), timeout=1.5)
            new = {"reachable": True, **(data or {})} if status == 200 else {"reachable": False}
        except OSError:
            new = {"reachable": False}
        if new != self.status.get(host):
            self.status[host] = new
            self._on_change()

    def _run(self) -> None:
        while not self._stop.is_set():
            if self.wanted:
                for d in list(self.devices):
                    self._pool.submit(self._poll, d["host"])
            self._sleep(3 if self.wanted else 5)

    def targets(self, target: str) -> list[dict]:
        return [d for d in self.devices if target in ("", "all") or d.get("name") == target]

    def reconnect(self, target: str) -> None:
        for d in self.targets(target):
            try:
                _http("POST", self._url(d["host"], "/vmix/reconnect"), timeout=2)
            except OSError:
                self._log("warn", f"Tally {d.get('name') or d['host']} injoignable")
        threading.Timer(1.5, self._wake.set).start()  # relire l'état après la reconnexion

    def summary(self, target: str) -> tuple[bool | None, list[str]]:
        """(tous connectés à vMix ?, noms des tallys en défaut). None tant que rien n'est connu."""
        devs = self.targets(target)
        states = [(d.get("name") or d["host"], self.status.get(d["host"])) for d in devs]
        if not states or any(s is None for _, s in states):
            return None, []
        down = [name for name, s in states if not (s.get("reachable") and s.get("connected"))]
        return not down, down


# --- migration d'une config Companion -------------------------------------------------------------

_ORCH = re.compile(r"^(https?://[^/]+)/api/overlay/(twizz|poll|speedizz)/(show|hide)/?$")
_TALLY = re.compile(r"^https?://([^/]+)/vmix/reconnect/?$")


def migrate(cfg: dict) -> list[str]:
    """Remplace les bricolages hérités de Companion par les intégrations natives."""
    from .actions import new_action
    from .health import parse_targets
    from .vmix import norm

    integ = cfg.setdefault("integrations", {})
    notes: list[str] = []
    counts = {"orch": 0, "tally": 0, "intercom": 0}
    for page in cfg.get("pages", []):
        for b in page["buttons"].values():
            acts, off = b.get("actions") or [], b.get("actions_off") or []
            # 1. interrupteur show / hide vers orchestream -> overlay orchestream suivi en direct
            on_m = [_ORCH.match(a.get("url", "")) for a in acts if a.get("type") == "http"]
            off_m = [_ORCH.match(a.get("url", "")) for a in off if a.get("type") == "http"]
            if (b.get("mode") == "toggle" and len(acts) == len(off) == 1 and on_m[:1] and off_m[:1]
                    and on_m[0] and off_m[0] and on_m[0].group(2) == off_m[0].group(2)):
                orch = integ.setdefault("orchestream", {})
                if not orch.get("url"):
                    orch["url"] = on_m[0].group(1)
                b["mode"], b["actions_off"] = "press", []
                b["actions"] = [new_action("orchestream.overlay", overlay=on_m[0].group(2), mode="toggle")]
                b["feedbacks"] = [f for f in b.get("feedbacks", []) if f.get("cond", {}).get("type") != "toggle"]
                counts["orch"] += 1
                continue
            # 2. reconnexions de tallys + pings -> intégration Tally32
            hosts = [m.group(1) for m in (_TALLY.match(a.get("url", "")) for a in acts if a.get("type") == "http") if m]
            if hosts:
                names = {addr: name for f in b.get("feedbacks", []) if f.get("cond", {}).get("type") == "health"
                         for name, addr in parse_targets(f["cond"].get("targets", ""))}
                devices = integ.setdefault("tallies", [])
                for h in hosts:
                    if h not in {d["host"] for d in devices}:
                        devices.append({"name": names.get(h, h), "host": h})
                b["actions"] = [a for a in acts if not (a.get("type") == "http" and _TALLY.match(a.get("url", "")))]
                b["actions"].insert(0, new_action("tally.reconnect", target="all"))
                # les pings deviennent inutiles : le retour automatique des tallys les remplace
                b["feedbacks"] = [f for f in b.get("feedbacks", []) if f.get("cond", {}).get("type") != "health"]
                counts["tally"] += 1
                continue
            # 3. raccourci maintenu « Intercom <bus> » -> API py-intercom
            label = norm(b.get("label", ""))
            bus = next((k for k, _ in INTERCOM_BUSES if k in label), None)
            if ("intercom" in label and bus and len(acts) == 1 and acts[0].get("type") == "hotkey"
                    and acts[0].get("hold")):
                b["actions"] = [new_action("intercom.ptt", bus=bus, mode="hold")]
                b["feedbacks"] = [f for f in b.get("feedbacks", []) if f.get("cond", {}).get("type") != "pressed"]
                counts["intercom"] += 1
    if counts["orch"]:
        notes.append(f"{counts['orch']} interrupteur(s) d'overlay orchestream reliés au flux en direct "
                     "(l'état suit les changements faits ailleurs).")
    if counts["tally"]:
        notes.append(f"Bouton de vérification des tallys converti : {len(integ.get('tallies', []))} Tally32 "
                     "surveillé(s) directement (GET /status), reconnexion native.")
    if counts["intercom"]:
        notes.append(f"{counts['intercom']} touche(s) Intercom passée(s) de raccourcis clavier à l'API de "
                     "py-intercom (talkback maintenu, voyant d'état).")
    return notes
