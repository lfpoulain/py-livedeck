"""Intégrations maison : py-intercom (talkback), orchestream (overlays web), Tally32 (tallys ESP32),
py-multiview (multiviewer SRT/OMT).

Chaque client tourne dans son propre thread et ne travaille que si une touche l'utilise (ou pendant que la
fenêtre Réglages est ouverte). Les appels vers ces services ne passent jamais par un proxy quand l'adresse
est locale (127.0.0.1, réseau privé).
"""
from __future__ import annotations

import copy
import json
import logging
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor

from .webreq import DIRECT, NET_ERRORS, error_kind, open_url

# Valeurs de repli tant que le service n'a pas répondu : la liste réelle vient de /status (py-intercom),
# des réglages et du flux en direct (orchestream).
INTERCOM_BUSES = [("regie", "Régie"), ("plateau", "Plateau"), ("vmix", "vMix")]
ORCH_OVERLAYS = [("twizz", "Twizz"), ("poll", "Sondage"), ("speedizz", "Speedizz")]
_BUS_IDS = {"0": "regie", "1": "plateau", "2": "vmix"}  # l'API accepte aussi les numéros de bus
BUS_MODES = {"ptt": "maintenu", "toggle": "bascule", "always_on": "toujours actif"}
INTERCOM_MIN = "version 2.0.x du 29/10/2026 ou plus récente"
# py-multiview : port par défaut de son API, et ce que chaque cible pilote
MULTIVIEW_PORT = 5090
MULTIVIEW_TARGETS = [("reception", "Réception (tous les flux)"), ("stream", "Un flux"),
                     ("mute", "Son d'un flux"), ("sender", "Émission OMT"),
                     ("monitor", "Retour audio"), ("floating", "Fenêtre de retour"),
                     ("route", "Route multicast")]
MULTIVIEW_TARGET_LABEL = dict(MULTIVIEW_TARGETS)
MULTIVIEW_SECTION = {"reception": "reception", "sender": "sender", "monitor": "monitor",
                     "floating": "floating"}
# cibles qui demandent de préciser lequel
MULTIVIEW_NEEDS_REF = ("stream", "mute", "route")
# py-intercom n'a pas été atteint (ou ce n'est pas lui qui répond) : le raccourci de secours a un sens
UNREACHABLE = {"refused", "timeout", "dns", "reset", "network", "notapi", "proxy", "forbidden"}


def bus_label(key) -> str:
    key = _BUS_IDS.get(str(key), str(key or ""))
    return dict(INTERCOM_BUSES).get(key.lower(), key.capitalize() if key else "?")


def overlay_label(name, labels: dict | None = None) -> str:
    return (labels or {}).get(name) or dict(ORCH_OVERLAYS).get(name) or str(name or "?").capitalize()


def overlay_list(orch_cfg: dict | None) -> list[tuple[str, str]]:
    """Overlays réglés (Réglages → Intégrations), sinon ceux d'orchestream par défaut."""
    items = (orch_cfg or {}).get("overlays")
    if not isinstance(items, list):
        return list(ORCH_OVERLAYS)
    out: list[tuple[str, str]] = []
    for it in items:
        name, label = (it.get("name"), it.get("label")) if isinstance(it, dict) else (it, "")
        name = str(name or "").strip()
        if name and name not in {n for n, _ in out}:
            out.append((name, str(label or "").strip() or overlay_label(name)))
    return out


def add_overlay(integ: dict, name: str) -> None:
    """Ajoute un overlay à la liste réglée (import, migration) s'il n'y est pas déjà."""
    orch = integ.setdefault("orchestream", {})
    items = overlay_list(orch)
    if name not in {n for n, _ in items}:
        items.append((name, overlay_label(name)))
    orch["overlays"] = [{"name": n, "label": label} for n, label in items]


def _http(method: str, url: str, timeout: float = 1.5, direct: bool | None = None) -> tuple[int, dict | None]:
    """Requête sans en-tête Origin (py-intercom refuse les navigateurs). Lève OSError si injoignable.
    direct : toujours sans proxy (py-intercom, tallys) ; sinon sans proxy seulement pour une adresse locale."""
    req = urllib.request.Request(url, data=b"" if method == "POST" else None, method=method)
    try:
        with (DIRECT.open(req, timeout=timeout) if direct else open_url(req, timeout)) as resp:
            status, raw = resp.status, resp.read(65536)
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read(65536)
    try:
        return status, json.loads(raw.decode("utf-8", "replace") or "null")
    except ValueError:
        return status, None


def _hostport(url: str) -> tuple[str, str]:
    """(hôte, « hôte:port ») d'une URL, pour les messages."""
    rest = url.split("://", 1)[-1].split("/", 1)[0]
    host = rest.rsplit(":", 1)[0] if rest.count(":") == 1 else rest
    return host.strip("[]"), rest


def _is_proxy_reply(status: int, data) -> bool:
    """Réponse 407 / 502 / 504 qui n'est pas du JSON : elle vient d'un proxy, pas de l'appareil."""
    return status in (407, 502, 504) and not isinstance(data, dict)


def _net_text(kind: str, who: str, where: str) -> str:
    return {"timeout": f"{who} ne répond pas à temps sur {where} : vérifie l'adresse IP, le pare-feu et que "
                       "l'appareil est allumé",
            "dns": f"{who} : adresse « {where} » introuvable (nom d'hôte inconnu)",
            "reset": f"{who} : connexion coupée par {where}",
            "proxy": f"{who} : la requête vers {where} est interceptée par un proxy (vérifie HTTP_PROXY ou "
                     "le proxy de Windows)"}.get(kind, f"{who} injoignable sur {where}")


def explain_intercom(host: str, port, status: int | None = None, data=None, exc: BaseException | None = None,
                     bus: str | None = None, buses: dict | None = None) -> tuple[str, str]:
    """(nature, message en français) d'une réponse ou d'une erreur de py-intercom.
    Natures : ok, refused, timeout, dns, reset, network, proxy, forbidden, notapi, offline, absent,
    always_on, busy, http."""
    where = f"{host}:{port}"
    if exc is not None:
        kind = error_kind(exc, host)
        if kind == "refused":
            r = getattr(exc, "reason", exc)
            if isinstance(r, (TimeoutError, socket.timeout)) or "timed out" in str(r):  # en local : fermé ou bloqué
                return kind, (f"py-intercom ne répond pas à temps sur {where} : client fermé, ou occupé ? Vérifie "
                              f"qu'il est lancé ({INTERCOM_MIN}) avec « Contrôle HTTP » coché dans la zone Parler (et "
                              "« Depuis le réseau » si LiveDeck tourne sur un autre PC)")
            return kind, (f"py-intercom ne répond pas sur {where} : lance le client ({INTERCOM_MIN}) et coche "
                          "« Contrôle HTTP » dans la zone Parler ; si LiveDeck tourne sur un autre PC, coche "
                          "aussi « Depuis le réseau »")
        if kind == "timeout":
            return kind, (f"py-intercom ne répond pas à temps sur {where} : vérifie l'adresse IP, le pare-feu "
                          "du PC intercom et que « Depuis le réseau » est coché dans le client")
        return kind, _net_text(kind, "py-intercom", where)
    if _is_proxy_reply(status or 0, data):
        return "proxy", _net_text("proxy", "py-intercom", where) + f" (HTTP {status})"
    data = data if isinstance(data, dict) else {}
    err = str(data.get("error") or "")
    if status == 403:
        return "forbidden", (f"py-intercom a refusé la requête (403, en-tête de navigateur) : un proxy ou un "
                             f"antivirus modifie-t-il les requêtes vers {where} ?")
    if status == 409:
        if "online" in data and not data.get("online"):
            state = f" (état : {data['state']})" if data.get("state") else ""
            return "offline", (f"py-intercom désarmé : le client n'est pas en ligne{state}. Connecte-le au "
                               "serveur intercom")
        return "always_on", f"py-intercom : bus {bus_label(bus)} en mode « Toujours actif » dans le client"
    if status == 404:
        if "proposé" in err:
            avail = ", ".join(bus_label(k) for k in buses or {}) or "aucun"
            return "absent", (f"py-intercom : le bus {bus_label(bus)} n'est pas proposé par le serveur intercom "
                              f"(disponibles : {avail})")
        if bus is not None and "bus inconnu" in err:
            return "absent", f"py-intercom : bus « {bus} » inconnu ({err})"
        return "notapi", (f"{where} répond, mais ce n'est pas l'API de py-intercom (autre programme sur ce port, "
                          f"ou client trop ancien : il faut la {INTERCOM_MIN})")
    if status == 503:
        return "busy", "py-intercom occupé (503) : réessaie"
    if status and 200 <= status < 300:
        if "buses" in data:  # py-intercom renvoie toujours son état
            return "ok", ""
        return "notapi", (f"{where} répond, mais ce n'est pas l'API de py-intercom (autre programme sur ce "
                          "port ?)")
    return "http", f"py-intercom {status} : {err or 'réponse inattendue'}"


def probe_intercom(host: str, port, timeout: float = 3.0) -> tuple[bool | None, str]:
    """Test ponctuel (bouton « Tester ») : (True en ligne / None joignable mais désarmé / False, message)."""
    host = (host or "127.0.0.1").strip()
    try:
        status, data = _http("GET", f"http://{host}:{int(port)}/status", timeout=timeout, direct=True)
    except NET_ERRORS as e:
        return False, explain_intercom(host, port, exc=e)[1]
    if status == 200 and isinstance(data, dict) and "buses" in data:
        buses = "  ·  ".join(f"{bus_label(k)} ({BUS_MODES.get(v.get('mode'), v.get('mode'))}"
                             f"{', ouvert' if v.get('active') else ''})"
                             for k, v in (data.get("buses") or {}).items() if isinstance(v, dict))
        if data.get("online"):
            return True, f"py-intercom en ligne ({data.get('state') or 'OK'})  ·  bus : {buses or 'aucun'}"
        return None, (f"py-intercom répond, mais le client n'est pas en ligne (état : {data.get('state') or '?'}) : "
                      "connecte-le au serveur intercom")
    return False, explain_intercom(host, port, status, data)[1]


class _Service:
    def __init__(self, on_change, log):
        self._on_change, self._log = on_change, log
        self.wanted = False  # une touche utilise ce service
        self.forced = False  # surveillance demandée sans touche (fenêtre Réglages ouverte)
        self._stop = threading.Event()
        self._wake = threading.Event()

    @property
    def active(self) -> bool:
        return self.wanted or self.forced

    def start(self) -> None:
        threading.Thread(target=self._guarded, name=type(self).__name__, daemon=True).start()

    def _guarded(self) -> None:
        """Relance la boucle après une erreur imprévue au lieu de laisser mourir le thread."""
        while not self._stop.is_set():
            try:
                self._run()
            except Exception:
                logging.getLogger("livedeck").exception(type(self).__name__)
                self._stop.wait(2)

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def set_wanted(self, wanted: bool) -> None:
        if wanted != self.wanted:
            self.wanted = wanted
            self._wake.set()

    def set_forced(self, forced: bool) -> None:
        if forced != self.forced:
            self.forced = forced
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
        self.error = ""  # pourquoi py-intercom est injoignable (message lisible)
        self.last: tuple[str, str, str] = ("", "", "")  # dernière commande : (nature, niveau, message)
        self.buses: dict[str, dict] = {}
        self.fallback_held: set[tuple[str, str]] = set()  # (bus, touches) tenus par le raccourci de secours
        self.fallback_tried: set[tuple[str, str]] = set()  # … dont le « on » HTTP a été tenté (client lent ?)
        self._lock = threading.Lock()
        self._off_due: dict[str, tuple] = {}  # bus refermé par LiveDeck -> (envoi, échéance, renvois)

    @property
    def base(self) -> str:
        return f"http://{self.host}:{self.port}"

    def configure(self, host: str, port: int) -> None:
        host, port = (host or "127.0.0.1").strip(), int(port or 5080)
        if (host, port) != (self.host, self.port):
            self.host, self.port = host, port
            self.reachable, self.error, self.buses = None, "", {}
        self._wake.set()

    def _apply(self, data: dict | None) -> None:
        if not isinstance(data, dict) or "buses" not in data:
            return
        sig = (self.reachable, self.online, self.state_text, json.dumps(self.buses, sort_keys=True))
        self.online = bool(data.get("online"))
        self.state_text = data.get("state", "")
        self.buses = {k.lower(): v for k, v in (data.get("buses") or {}).items() if isinstance(v, dict)}
        if sig != (True, self.online, self.state_text, json.dumps(self.buses, sort_keys=True)):
            self._on_change()

    def _set_reachable(self, ok: bool, why: str = "") -> None:
        self.error = "" if ok else why
        if ok != self.reachable:
            if self.reachable is not None or not ok:
                self._log("info" if ok else "warn", f"py-intercom joignable ({self.base})" if ok
                          else (why or f"py-intercom injoignable ({self.base})"))
            self.reachable = ok
            self._on_change()

    def _run(self) -> None:
        while not self._stop.is_set():
            if not self.active and not self._off_due:
                self._sleep(5)
                continue
            t0 = time.monotonic()
            try:
                status, data = _http("GET", f"{self.base}/status", timeout=1, direct=True)
            except NET_ERRORS as e:
                self._set_reachable(False, explain_intercom(self.host, self.port, exc=e)[1])
            else:
                if status == 200 and isinstance(data, dict) and "buses" in data:
                    self._set_reachable(True)
                    self._apply(data)
                    self._reconcile(t0)
                else:
                    self._set_reachable(False, explain_intercom(self.host, self.port, status, data)[1])
            self._sleep(0.35 if self.reachable else 2)

    def _reconcile(self, t0: float) -> None:
        """État voulu : un bus que LiveDeck a refermé ne doit pas rester ouvert (« off » perdu, client
        redémarré, « on » appliqué en retard…). On regarde la 1re lecture de /status après notre « off » :
        bus encore actif -> « off » renvoyé (2 fois au plus, une seule alerte). Bus en mode bascule dans le
        client, ou vu fermé : on arrête de surveiller (l'opérateur a pu le rouvrir lui-même depuis le client)."""
        now = time.monotonic()
        for bus, (sent, deadline, tries) in list(self._off_due.items()):
            if now > deadline:
                self._off_due.pop(bus, None)
                continue
            if sent >= t0:  # cet état a été lu avant notre « off » : on attend le suivant
                continue
            buses = list(self.buses.values()) if bus == "all" else [self.bus(bus)]
            if tries >= 2 or not any(b.get("active") and b.get("mode") not in ("always_on", "toggle") for b in buses):
                self._off_due.pop(bus, None)
                continue
            if not tries:
                self._log("warn", f"py-intercom : bus {bus_label(bus)} encore ouvert, LiveDeck renvoie « off »")
            with self._lock:
                try:
                    _http("POST", f"{self.base}/ptt/{bus}/off", timeout=1, direct=True)
                except NET_ERRORS:
                    pass
                if bus in self._off_due:
                    self._off_due[bus] = (time.monotonic(), deadline, tries + 1)

    def command(self, bus: str, cmd: str, quick: bool = False, report: bool = True) -> bool:
        """Envoie une commande PTT. Un « off » est réessayé (le micro ne doit pas rester ouvert), puis
        surveillé par _reconcile. quick : une seule tentative courte (arrêt, raccourci de secours prêt).
        report : journalise l'échec (sinon il reste lisible dans self.last). Renvoie True si c'est fait."""
        bus = "all" if str(bus).lower() == "all" else _BUS_IDS.get(str(bus), str(bus or "regie").lower())
        url = f"{self.base}/ptt/{bus}/{cmd}"
        off = cmd == "off"
        was = self.reachable
        status, data = 0, None
        with self._lock:  # un « off » ne doit jamais doubler un « on »
            if not off:
                self._off_due.pop(bus, None)
                self._off_due.pop("all", None)
            for attempt in range(4):
                try:
                    status, data = _http("POST", url, timeout=0.5 if quick else (1 if off else 1.5), direct=True)
                except NET_ERRORS as e:
                    if off and not quick and attempt < 3:
                        time.sleep((0.1, 0.3, 0.6)[attempt])
                        continue
                    kind, msg = explain_intercom(self.host, self.port, exc=e)
                    self._set_reachable(False, msg)
                    if off:  # à renvoyer dès que le client répond de nouveau
                        self._off_due[bus] = (time.monotonic(), time.monotonic() + 60, 0)
                        self._wake.set()
                    # déjà signalé injoignable à l'appui : le relâché ne fait pas une 2e alerte
                    self._finish(kind, "warn" if off and was is False else "error", msg, report)
                    return False
                if status == 503 and attempt < 2 and not quick:
                    time.sleep(0.05)
                    continue
                break
            if off and status in (200, 503):
                self._off_due[bus] = (time.monotonic(), time.monotonic() + (3 if status == 200 else 10), 0)
        kind, msg = explain_intercom(self.host, self.port, status, data, bus=bus, buses=self.buses)
        if kind in UNREACHABLE:
            self._set_reachable(False, msg)
        else:
            self._set_reachable(True)
            self._apply(data)
        self._wake.set()
        if kind == "ok" or (kind == "always_on" and off):  # bus toujours ouvert : rien à refermer
            self.last = (kind, "", "")
            return True
        if kind in ("offline", "absent") and off:  # désarmé ou bus absent : aucun micro ouvert, rien à signaler
            self.last = (kind, "", msg)
            return False
        self._finish(kind, "info" if kind == "always_on" else "error", msg, report)
        return False

    def _finish(self, kind: str, level: str, msg: str, report: bool) -> None:
        self.last = (kind, level, msg)
        if report and msg:
            self._log(level, msg)

    def bus(self, name: str) -> dict:
        return self.buses.get(_BUS_IDS.get(str(name), str(name).lower()), {})

    def bus_choices(self) -> list[tuple[str, str]]:
        """Bus proposés par ce serveur (lus sur /status), sinon la liste habituelle."""
        if self.buses:
            return [(k, bus_label(k)) for k in self.buses]
        return list(INTERCOM_BUSES)

    def unavailable(self, bus: str) -> str:
        """Pourquoi on ne peut pas parler sur ce bus (texte court affiché sur la touche), sinon ""."""
        if self.reachable is False:
            return "HORS LIGNE"
        if self.reachable and not self.online:
            return "DÉSARMÉ"
        if self.reachable and self.buses and not self.bus(bus):
            return "BUS ABSENT"
        return ""


# --- orchestream ----------------------------------------------------------------------------


def normalize_url(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    if url and not url.startswith(("http://", "https://")):
        url = "http://" + url
    return url


def _orch_message(data) -> str:
    """Message d'erreur d'orchestream : v2 {"error": {"code", "message"}}, v1 {"error": "…"} ou {"message": "…"}."""
    if not isinstance(data, dict):
        return ""
    err = data.get("error")
    if isinstance(err, dict):
        return str(err.get("message") or err.get("code") or "")
    return str(err or data.get("message") or "")


def _orch_code(data) -> str:
    err = data.get("error") if isinstance(data, dict) else None
    return str(err.get("code") or "") if isinstance(err, dict) else ""


def explain_orchestream(url: str, status: int | None = None, data=None, exc: BaseException | None = None) -> str:
    host, where = _hostport(url)
    if exc is not None:
        kind = error_kind(exc, host)
        if kind == "refused":
            return f"orchestream ne répond pas sur {where} : le serveur est-il lancé, l'adresse et le port sont-ils bons ?"
        return _net_text(kind, "orchestream", where)
    if _is_proxy_reply(status or 0, data):
        return _net_text("proxy", "orchestream", where) + f" (HTTP {status})"
    if status == 404:
        if _orch_code(data) == "NOT_FOUND":  # orchestream-v2 joint par son adresse publique (tunnel)
            return (f"{where} est bien orchestream-v2, mais il refuse la régie par cette adresse (adresse publique "
                    "ou tunnel) : utilise son adresse locale, ex. http://192.168.1.20:5174")
        return f"{where} répond, mais sans l'API d'orchestream (/api/overlay/…) : vérifie l'adresse"
    return f"orchestream : {_orch_message(data) or f'erreur {status}'}"


def _read_json(resp) -> object:
    try:
        return json.loads(resp.read(262144).decode("utf-8", "replace") or "null")
    except ValueError:
        return None


def _orch_state(url: str, timeout: float) -> tuple[int, object]:
    """GET /api/overlay/state (orchestream-v2) : (statut HTTP, JSON). Lève OSError si injoignable."""
    try:
        with open_url(urllib.request.Request(f"{url}/api/overlay/state"), timeout) as resp:
            return resp.status, _read_json(resp)
    except urllib.error.HTTPError as e:
        return e.code, _read_json(e)


def _is_v2_state(status: int, data) -> bool:
    return status == 200 and isinstance(data, dict) and isinstance(data.get("widgets"), dict)


def _v2_refused(status: int, data) -> bool:
    """404 « NOT_FOUND » en JSON sur /api/overlay/state : orchestream-v2 refuse la régie par cette adresse."""
    return status == 404 and _orch_code(data) == "NOT_FOUND"


def probe_orchestream(url: str, timeout: float = 4.0) -> tuple[bool | None, str]:
    """Test ponctuel : état des overlays (orchestream-v2), sinon flux en direct de la v1."""
    url = normalize_url(url)
    if not url:
        return None, "Adresse à renseigner (ex. http://serveur:5174)."
    try:
        status, data = _orch_state(url, timeout)
        if _is_v2_state(status, data):
            shown = [overlay_label(k) for k, w in data["widgets"].items() if isinstance(w, dict) and w.get("visible")]
            return True, (f"orchestream-v2 joignable ({url}) · "
                          + (f"à l'antenne : {', '.join(shown)}" if shown else "aucun overlay affiché"))
        if _v2_refused(status, data):
            return False, explain_orchestream(url, status, data)
        try:
            with open_url(urllib.request.Request(f"{url}/api/overlay/events"), timeout) as resp:
                ctype = resp.headers.get("Content-Type", "")
                if resp.status == 200 and "event-stream" in ctype:
                    return True, f"orchestream (v1) joignable : flux en direct ouvert ({url})"
                return False, f"{url} répond ({resp.status}, {ctype or 'sans type'}), mais ce n'est pas orchestream"
        except urllib.error.HTTPError as e:
            return False, explain_orchestream(url, e.code, _read_json(e))
    except NET_ERRORS as e:
        return False, explain_orchestream(url, exc=e)


class Orchestream(_Service):
    """Overlays web d'orchestream : commandes show/hide et état suivi en direct.
    orchestream-v2 : état lu sur /api/overlay/state, puis canal « overlay » de Transmit (/__transmit/events).
    orchestream v1 (utilisé si la v2 ne répond pas) : flux SSE /api/overlay/events ; l'état de Twizz et du
    Sondage y reste inconnu jusqu'au premier changement."""

    def __init__(self, on_change, log):
        super().__init__(on_change, log)
        self.url = ""
        self.api = ""  # "v2", "v1" ou "" (pas encore connu)
        self.connected = False
        self.error = ""
        self.labels: dict[str, str] = dict(ORCH_OVERLAYS)
        self.learned: list[str] = []  # overlays vus en direct mais absents des réglages
        self.overlays: dict[str, bool | None] = {k: None for k, _ in ORCH_OVERLAYS}

    def configure(self, url: str, overlays: list[tuple[str, str]] | None = None) -> None:
        url = normalize_url(url)
        if overlays is not None:
            self.labels = dict(overlays)
            self.overlays = {k: v for k, v in self.overlays.items() if k in self.labels or k in self.learned}
            for name in self.labels:
                self.overlays.setdefault(name, None)
        if url != self.url:
            self.url = url
            self.api = ""
            self.error = ""
            self.learned = []
            self.overlays = {k: None for k in self.labels}
            self._wake.set()

    def names(self) -> list[tuple[str, str]]:
        """Overlays réglés, puis ceux appris en direct."""
        out = list(self.labels.items())
        out += [(k, overlay_label(k)) for k in list(self.learned) if k not in self.labels]
        return out

    def _learn(self, name: str) -> None:
        if name not in self.labels and name not in self.learned:
            self.learned.append(name)

    def _set(self, name: str, visible) -> None:
        visible = None if visible is None else bool(visible)
        if name not in self.overlays or self.overlays.get(name) != visible:
            self.overlays[name] = visible
            self._on_change()

    def _set_connected(self, ok: bool, why: str = "") -> None:
        if ok:
            self.error = ""
        elif why and why != self.error:
            self.error = why
            self._log("warn", why)
        if ok != self.connected:
            self.connected = ok
            if ok or not why:
                what = "orchestream-v2" if self.api == "v2" else "orchestream"
                self._log("info" if ok else "warn", f"{what} {'connecté' if ok else 'déconnecté'} ({self.url})")
            self._on_change()

    def _handle(self, event: str, data) -> None:
        """Événement de la v1 (« <nom>:overlay:toggle », instantanés Speedizz)."""
        m = re.fullmatch(r"([\w-]+):overlay:toggle", event)
        if m and isinstance(data, dict):
            self._learn(m.group(1))
            self._set(m.group(1), data.get("isVisible"))
        elif event == "speedizz:snapshot":
            self._set("speedizz", bool(isinstance(data, dict) and data.get("isVisible")))
        elif event == "speedizz:session:finished":
            self._set("speedizz", False)

    def _widget(self, w) -> None:
        """État d'un widget de la v2 : {"widget", "visible", "data"}."""
        if isinstance(w, dict) and isinstance(w.get("widget"), str):
            self._learn(w["widget"])
            self._set(w["widget"], w.get("visible"))

    def _alive(self, url: str) -> bool:
        return not self._stop.is_set() and url == self.url and self.active

    def _run(self) -> None:
        delay = 2
        while not self._stop.is_set():
            if not (self.active and self.url):
                self._set_connected(False)
                self._sleep(5)
                continue
            url, why = self.url, ""
            try:
                status, data = _orch_state(url, 6)
                if _is_v2_state(status, data):
                    self.api = "v2"
                    self._run_v2(url, data)
                elif _v2_refused(status, data):
                    why = explain_orchestream(url, status, data)
                else:
                    self.api = "v1"
                    self._run_v1(url)
            except urllib.error.HTTPError as e:
                why = explain_orchestream(url, e.code, _read_json(e))
            except NET_ERRORS as e:
                why = explain_orchestream(url, exc=e)
            if self.connected:
                delay = 2  # la connexion avait abouti : on retente vite
            self._set_connected(False, why)
            self._sleep(delay)
            delay = min(delay * 2, 15)

    def _sse(self, resp, url: str):
        """Messages SSE (événement, données JSON) d'une réponse ouverte, tant que le service est actif."""
        event, data = "", []
        for raw in resp:
            if not self._alive(url):
                return
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].strip())
            elif not line and (event or data):
                try:
                    yield event, json.loads("\n".join(data) or "null")
                except ValueError:
                    pass
                event, data = "", []

    def _run_v1(self, url: str) -> None:
        with open_url(urllib.request.Request(f"{url}/api/overlay/events"), 45) as resp:
            self._set_connected(True)
            for event, data in self._sse(resp, url):
                if event:
                    self._handle(event, data)

    def _run_v2(self, url: str, snapshot: dict) -> None:
        """Transmit : ouvre le flux avec un identifiant, s'abonne au canal « overlay », relit l'état, écoute."""
        uid = uuid.uuid4().hex
        # orchestream envoie un ping toutes les 25 s : 45 s sans rien = connexion perdue
        with open_url(urllib.request.Request(f"{url}/__transmit/events?uid={uid}"), 45) as resp:
            body = json.dumps({"uid": uid, "channel": "overlay"}).encode()
            sub = urllib.request.Request(f"{url}/__transmit/subscribe", data=body, method="POST",
                                         headers={"Content-Type": "application/json"})
            with open_url(sub, 6):
                pass
            status, fresh = _orch_state(url, 6)  # relu après l'abonnement : aucun changement perdu entre les deux
            for w in (fresh if _is_v2_state(status, fresh) else snapshot)["widgets"].values():
                self._widget(w)
            self._set_connected(True)
            for _, msg in self._sse(resp, url):
                if isinstance(msg, dict) and msg.get("channel") == "overlay":
                    payload = msg.get("payload")
                    if isinstance(payload, dict) and payload.get("type") == "widget.update":
                        self._widget(payload.get("data"))

    def command(self, name: str, mode: str) -> bool:
        if not self.url:
            self._log("error", "orchestream : adresse non réglée (Réglages → Intégrations)")
            return False
        show = (not self.overlays.get(name)) if mode == "toggle" else mode == "show"
        url = f"{self.url}/api/overlay/{name}/{'show' if show else 'hide'}"
        try:
            status, data = _http("POST", url, timeout=4)
        except NET_ERRORS as e:
            self._log("error", explain_orchestream(self.url, exc=e))
            return False
        if 200 <= status < 300:
            if isinstance(data, dict):  # v2 : état du widget ; v1 : {"isVisible"}
                show = data.get("visible", data.get("isVisible", show))
            self._set(name, show)
            return True
        code = _orch_code(data)
        if status in (404, 409) and _orch_message(data) and code not in ("NOT_FOUND", "E_ROUTE_NOT_FOUND"):
            msg = _orch_message(data)  # refus métier (ex. « Aucun Speedizz à afficher »)
        elif status == 422 or (status == 404 and code != "NOT_FOUND"):
            msg = f"overlay « {name} » inconnu d'orchestream (pas de route /api/overlay/{name})"
        else:
            msg = explain_orchestream(self.url, status, data).removeprefix("orchestream : ")
        self._log("error", f"orchestream · {overlay_label(name, self.labels)} : {msg}")
        return False


# --- orchestream-v2 : API Stream Deck (commandes régie, jeton) -------------------------------

# Commandes régie d'orchestream-v2 (docs/COMMANDES.md) : (id, libellé, argument éventuel).
# La liste réelle, filtrée par les permissions du jeton, vient de GET /api/deck/commands ;
# celle-ci sert tant que le serveur n'a pas répondu, et pour écrire les résumés.
ORCH_COMMANDS = [
    ("game.advance", "Jeu : étape suivante", ""), ("game.next", "Question suivante", ""),
    ("game.close", "Fermer la question", ""), ("game.finish", "Terminer la partie", ""),
    ("twizz.start", "Lancer un Twizz", "quizId"), ("speedizz.start", "Lancer un Speedizz", "gameId"),
    ("draw.run", "Tirer au sort", ""), ("draw.reroll", "Retirer au sort", ""),
    ("draw.fromLastTwizz", "Tirage du dernier Twizz", ""), ("poll.end", "Terminer le sondage", ""),
    ("prediction.lock", "Verrouiller la prédiction", ""),
    ("overlay.show", "Afficher un widget", "widget"), ("overlay.hide", "Masquer un widget", "widget"),
    ("popups.reset", "Effacer les popups", ""), ("activity.ackAll", "Tout remercier", ""),
    ("popup.replayLast", "Rejouer la dernière popup", ""), ("lanes.clear", "Vider les files", ""),
    ("dmaix.baseScene", "Scène de base DMAIX", ""), ("vmix.function", "Fonction vMix (autorisée)", "fn"),
    ("live.start", "Démarrer la session", ""), ("live.end", "Terminer le live", ""),
]
ORCH_COMMAND_LABEL = {c: label for c, label, _ in ORCH_COMMANDS}
ORCH_COMMAND_ARG = {c: arg for c, _, arg in ORCH_COMMANDS if arg}
ORCH_WIDGETS = [("twizz", "Twizz"), ("poll", "Sondage"), ("speedizz", "Speedizz"), ("hypeTrain", "Hype Train"),
                ("prediction", "Prédiction")]
# Ce que fera le prochain « Jeu : étape suivante » (état game.nextAction), à écrire sur la touche
GAME_NEXT = {"close": "Fermer Q{n}", "open": "Ouvrir Q{n}", "finish": "Terminer"}


def _deck_request(method: str, url: str, token: str, body: dict | None = None,
                  timeout: float = 3.0) -> tuple[int, object, dict]:
    """Requête vers /api/deck : jeton dans l'en-tête Authorization (jamais dans l'URL, qu'orchestream
    refuse de lire). (statut, JSON, en-têtes). Lève NET_ERRORS si injoignable."""
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with open_url(req, timeout) as resp:
            return resp.status, _read_json(resp), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, _read_json(e), dict(e.headers or {})


def explain_deck(url: str, status: int | None = None, data=None, exc: BaseException | None = None) -> str:
    """Message en français d'un échec de l'API Stream Deck d'orchestream-v2."""
    host, where = _hostport(url)
    if exc is not None:
        return explain_orchestream(url, exc=exc)
    code, msg = _orch_code(data), _orch_message(data)
    if status == 401:
        return ("orchestream refuse le jeton (absent, inconnu ou révoqué) : crée-en un dans orchestream, "
                "Réglages › Sécurité › Jetons API, et colle-le dans Réglages → Intégrations")
    if status == 403:
        return f"orchestream : {msg or 'le jeton n’a pas la permission de cette commande'}"
    if status == 404 and code == "NOT_FOUND":
        return explain_orchestream(url, status, data)  # adresse publique / tunnel : refusé hors du réseau local
    if status == 404 and code not in ("COMMAND_NOT_FOUND",):
        return f"{where} répond, mais sans l'API Stream Deck d'orchestream-v2 (/api/deck) : vérifie l'adresse"
    if status == 429:
        return f"orchestream : {msg or 'trop de requêtes, réessaie dans un instant'}"
    if _is_proxy_reply(status or 0, data):
        return _net_text("proxy", "orchestream", where) + f" (HTTP {status})"
    return f"orchestream : {msg or f'erreur {status}'}"


def probe_deck(url: str, token: str, timeout: float = 4.0) -> tuple[bool | None, str]:
    """Test ponctuel de l'API Stream Deck : jeton accepté, et ce qu'il permet."""
    url = normalize_url(url)
    if not url:
        return None, "Adresse d'orchestream à renseigner."
    if not (token or "").strip():
        return None, "Jeton API à renseigner (orchestream : Réglages › Sécurité › Jetons API)."
    try:
        status, data, _ = _deck_request("GET", f"{url}/api/deck/state", token.strip(), timeout=timeout)
    except NET_ERRORS as e:
        return False, explain_deck(url, exc=e)
    if status == 200 and isinstance(data, dict) and isinstance(data.get("commands"), list):
        tok = data.get("token") or {}
        n = len(data["commands"])
        live = "live en cours" if (data.get("live") or {}).get("active") else "aucun live en cours"
        return True, f"jeton « {tok.get('name') or '?'} » accepté · {n} commande(s) autorisée(s) · {live}"
    return False, explain_deck(url, status, data)


class OrchestreamDeck(_Service):
    """API Stream Deck d'orchestream-v2 : commandes régie (POST /api/deck/commands/<id>) et état prêt à
    afficher (GET /api/deck/state), authentifiés par un jeton API.

    L'état est relu toutes les 1,5 s (la documentation recommande 1 à 2 s ; la limite est de 120 lectures
    par 10 s), et aussitôt après une commande. La liste des commandes, avec les valeurs de leurs arguments
    (quiz, Speedizz, fonctions vMix autorisées), est relue au démarrage puis toutes les 30 s."""

    POLL = 1.5
    LIST_EVERY = 30.0

    def __init__(self, on_change, log):
        super().__init__(on_change, log)
        self.url = ""
        self.token = ""
        self.connected = False
        self.error = ""
        self.state: dict = {}
        self.commands: dict[str, dict] = {}  # id -> RegieCommandInfo (avec les valeurs d'arguments)
        self._listed_at = 0.0
        self._retry_at = 0.0  # 429 : pas de nouvelle requête avant (Retry-After)
        self._lock = threading.Lock()

    def configure(self, url: str, token: str) -> None:
        url, token = normalize_url(url), (token or "").strip()
        if (url, token) != (self.url, self.token):
            self.url, self.token = url, token
            with self._lock:
                self.state, self.commands, self._listed_at = {}, {}, 0.0
            self.error = ""
            self._wake.set()

    # -- état ----------------------------------------------------------------------------------
    def _set_connected(self, ok: bool, why: str = "") -> None:
        if ok:
            self.error = ""
        elif why and why != self.error:
            self.error = why
            self._log("warn", why)
        if ok != self.connected:
            self.connected = ok
            if ok:
                self._log("info", f"orchestream (Stream Deck) connecté ({self.url})")
            self._on_change()

    def unavailable(self) -> str:
        """Pourquoi les touches ne peuvent pas servir (texte court pour la touche), ou "" si tout va bien."""
        if not self.url:
            return "ADRESSE ?"
        if not self.token:
            return "JETON ?"
        if not self.connected:
            return "HORS LIGNE"
        return ""

    def command_state(self, cid: str) -> dict | None:
        """État d'une commande pour ce jeton : {"available", "reason", …}, None si inconnue ou refusée."""
        with self._lock:
            for c in self.state.get("commands") or []:
                if isinstance(c, dict) and c.get("id") == cid:
                    return c
        return None

    def get(self, path: str):
        """Valeur de l'état par chemin pointé (« game.nextAction », « unthanked »…), None si absente."""
        with self._lock:
            cur = self.state
        for part in path.split("."):
            if not isinstance(cur, dict):
                return None
            cur = cur.get(part)
        return cur

    def arg_values(self, cid: str) -> list[tuple[str, str]]:
        """Valeurs proposées pour l'argument d'une commande (quiz, Speedizz, widgets, fonctions vMix)."""
        with self._lock:
            info = self.commands.get(cid) or {}
        for arg in info.get("args") or []:
            vals = [(str(v.get("value")), str(v.get("label") or v.get("value"))) for v in arg.get("values") or []
                    if isinstance(v, dict) and v.get("value") is not None]
            if vals:
                return vals
        return list(ORCH_WIDGETS) if ORCH_COMMAND_ARG.get(cid) == "widget" else []

    def command_list(self) -> list[tuple[str, str]]:
        """Commandes du jeton (une fois lues), sinon toutes celles d'orchestream."""
        with self._lock:
            known = [(cid, str(c.get("label") or ORCH_COMMAND_LABEL.get(cid, cid))) for cid, c in self.commands.items()]
        return known or [(c, label) for c, label, _ in ORCH_COMMANDS]

    # -- boucle --------------------------------------------------------------------------------
    def _run(self) -> None:
        while not self._stop.is_set():
            if not (self.active and self.url and self.token):
                self._set_connected(False)
                self._sleep(5)
                continue
            wait = self._retry_at - time.monotonic()
            if wait > 0:  # 429 : on respecte Retry-After au lieu d'aggraver le blocage
                self._sleep(wait)
                continue
            self._poll_once()
            self._sleep(self.POLL if self.connected else 5)

    def _poll_once(self) -> None:
        url, token = self.url, self.token
        try:
            status, data, headers = _deck_request("GET", f"{url}/api/deck/state", token)
            if status == 200 and isinstance(data, dict):
                changed = False
                with self._lock:
                    if data != self.state:
                        self.state, changed = data, True
                if time.monotonic() - self._listed_at > self.LIST_EVERY:
                    self._list(url, token)
                self._set_connected(True)
                if changed:
                    self._on_change()
                return
            self._throttle(status, headers)
            self._set_connected(False, explain_deck(url, status, data))
        except NET_ERRORS as e:
            self._set_connected(False, explain_deck(url, exc=e))

    def _list(self, url: str, token: str) -> None:
        try:
            status, data, headers = _deck_request("GET", f"{url}/api/deck/commands", token)
        except NET_ERRORS:
            return
        if status == 200 and isinstance(data, list):
            with self._lock:
                self.commands = {c["id"]: c for c in data if isinstance(c, dict) and isinstance(c.get("id"), str)}
            self._listed_at = time.monotonic()
        else:
            self._throttle(status, headers)

    def _throttle(self, status: int, headers: dict) -> None:
        if status != 429:
            return
        try:
            secs = float({k.lower(): v for k, v in headers.items()}.get("retry-after") or 2)
        except (TypeError, ValueError):
            secs = 2.0
        self._retry_at = time.monotonic() + min(max(secs, 1.0), 60.0)

    # -- commande ------------------------------------------------------------------------------
    def run(self, cid: str, args: dict | None = None) -> bool:
        """Lance une commande régie ; journalise la réponse d'orchestream (déjà en français)."""
        label = ORCH_COMMAND_LABEL.get(cid, cid)
        if not self.url:
            self._log("error", "orchestream : adresse non réglée (Réglages → Intégrations)")
            return False
        if not self.token:
            self._log("error", "orchestream : jeton API non réglé (Réglages → Intégrations)")
            return False
        body = {"args": {k: str(v) for k, v in (args or {}).items() if str(v or "").strip()}}
        if not body["args"]:
            body = {}
        path = urllib.parse.quote(cid, safe=".")
        try:
            status, data, headers = _deck_request("POST", f"{self.url}/api/deck/commands/{path}", self.token, body)
        except NET_ERRORS as e:
            self._log("error", f"orchestream · {label} : {explain_deck(self.url, exc=e)}")
            return False
        self._wake.set()  # relit l'état tout de suite : la touche montre l'effet de la commande
        if 200 <= status < 300:
            msg = data.get("message") if isinstance(data, dict) else ""
            self._log("info", f"orchestream · {msg or label}")
            return True
        self._throttle(status, headers)
        self._log("error", f"orchestream · {label} : {explain_deck(self.url, status, data).removeprefix('orchestream : ')}")
        return False


# --- Tally32 --------------------------------------------------------------------------------


def _tally_url(host: str, path: str) -> str:
    return f"{host if host.startswith('http') else 'http://' + host}{path}"


def probe_tally(host: str, timeout: float = 3.0) -> tuple[bool | None, str]:
    """Test ponctuel d'un Tally32 (GET /status) : (connecté à vMix / None sans vMix / False injoignable)."""
    url = _tally_url(host, "/status")
    h, where = _hostport(url)
    try:
        status, data = _http("GET", url, timeout=timeout, direct=True)
    except NET_ERRORS as e:
        kind = error_kind(e, h)
        return False, ("connexion refusée : ce n'est pas un Tally32 ?" if kind == "refused"
                       else _net_text(kind, "tally", where))
    if status != 200 or not isinstance(data, dict):
        if _is_proxy_reply(status, data):
            return False, _net_text("proxy", "tally", where)
        return False, f"{where} répond ({status}) mais pas comme un Tally32 (GET /status)"
    if data.get("connected"):
        return True, f"connecté à vMix ({data.get('vmix_host') or '?'}, input {data.get('vmix_input') or '?'})"
    return None, f"joignable, mais pas connecté à vMix ({data.get('vmix_host') or 'vMix non réglé'})"


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
        return _tally_url(host, path)

    def _poll(self, host: str) -> None:
        try:
            status, data = _http("GET", self._url(host, "/status"), timeout=1.5, direct=True)
            ok = status == 200 and isinstance(data, dict)
            new = {"reachable": True, **data} if ok else {"reachable": False, "error": f"HTTP {status}"}
        except NET_ERRORS as e:
            new = {"reachable": False, "error": error_kind(e, _hostport(host)[0])}
        if new != self.status.get(host):
            self.status[host] = new
            self._on_change()

    def _run(self) -> None:
        while not self._stop.is_set():
            if self.active:
                for d in list(self.devices):
                    try:
                        self._pool.submit(self._poll, d["host"])
                    except RuntimeError:  # arrêt en cours : le pool est déjà fermé
                        return
            self._sleep(3 if self.active else 5)

    def targets(self, target: str) -> list[dict]:
        return [d for d in self.devices if target in ("", "all") or d.get("name") == target]

    def reconnect(self, target: str) -> None:
        devs = self.targets(target)
        if not devs:
            self._log("error", "Tallys : aucun tally renseigné (Réglages → Intégrations)" if not self.devices
                      else f"Tallys : « {target} » n'est plus dans la liste (Réglages → Intégrations)")
        for d in devs:
            name = d.get("name") or d["host"]
            try:
                status, _ = _http("POST", self._url(d["host"], "/vmix/reconnect"), timeout=2, direct=True)
            except NET_ERRORS as e:
                self._log("error", f"Tally {name} injoignable : {_net_text(error_kind(e, _hostport(d['host'])[0]), 'tally', d['host'])}")
                continue
            if status == 404:
                self._log("error", f"Tally {name} : firmware sans /vmix/reconnect (mets le Tally32 à jour)")
            elif not 200 <= status < 300:
                self._log("error", f"Tally {name} : reconnexion refusée (HTTP {status})")
        t = threading.Timer(1.5, self._wake.set)  # relire l'état après la reconnexion
        t.daemon = True
        t.start()

    def summary(self, target: str) -> tuple[bool | None, list[str]]:
        """(tous connectés à vMix ?, noms des tallys en défaut). None tant que rien n'est connu."""
        devs = self.targets(target)
        states = [(d.get("name") or d["host"], self.status.get(d["host"])) for d in devs]
        if not states or any(s is None for _, s in states):
            return None, []
        down = [name for name, s in states if not (s.get("reachable") and s.get("connected"))]
        return not down, down


# --- py-multiview ---------------------------------------------------------------------------


def multiview_url(url: str) -> str:
    """Adresse normalisée : « 192.168.1.42 » suffit, le port 5090 est ajouté."""
    url = normalize_url(url)
    if not url:
        return ""
    scheme, _, rest = url.partition("://")
    host, _, path = rest.partition("/")
    if ":" not in host.strip("[]") or host.endswith("]"):
        host = f"{host}:{MULTIVIEW_PORT}"
    return f"{scheme}://{host}" + (f"/{path}" if path else "")


def _mv_state(url: str, timeout: float) -> tuple[int, dict | None]:
    return _http("GET", f"{url}/status", timeout=timeout)


def _is_mv_state(status: int, data) -> bool:
    """py-multiview se reconnaît à « streams » et à son nom : une autre API ne passe pas."""
    return status == 200 and isinstance(data, dict) and isinstance(data.get("streams"), list) \
        and data.get("app") == "py-multiview"


def explain_multiview(url: str, status: int | None = None, data=None, exc: BaseException | None = None) -> str:
    where = _hostport(url)[1]
    if exc is not None:
        kind = error_kind(exc, _hostport(url)[0])
        if kind == "refused":
            return f"py-multiview : {where} refuse la connexion (API fermée ? Préférences → Pilotage à distance)"
        return _net_text(kind, "py-multiview", where)
    if _is_proxy_reply(status or 0, data):
        return _net_text("proxy", "py-multiview", where)
    if status == 404:
        return f"{where} répond, mais ce n'est pas l'API de py-multiview (GET /status)"
    if isinstance(data, dict) and isinstance(data.get("error"), dict):
        return f"py-multiview : {data['error'].get('message') or data['error'].get('code')}"
    return f"py-multiview : {where} a répondu HTTP {status}"


def probe_multiview(url: str, timeout: float = 4.0) -> tuple[bool | None, str]:
    """Test ponctuel (bouton « Tester ») : (True joignable / False, message)."""
    url = multiview_url(url)
    if not url:
        return None, "Adresse non renseignée (ex. 192.168.1.42:5090)."
    try:
        status, data = _mv_state(url, timeout)
    except NET_ERRORS as e:
        return False, explain_multiview(url, exc=e)
    if not _is_mv_state(status, data):
        return False, explain_multiview(url, status, data)
    streams = data.get("streams") or []
    live = sum(1 for s in streams if s.get("running"))
    bits = [f"version {data.get('version', '?')}", f"{live}/{len(streams)} flux en lecture"]
    if (data.get("sender") or {}).get("running"):
        bits.append("émission OMT en cours")
    return True, "py-multiview : " + ", ".join(bits)


class Multiview(_Service):
    """py-multiview : flux, émission OMT et retours, par son API locale.

    Un seul GET /status porte tout l'état, et chaque commande le renvoie : une
    touche est donc à jour dès la réponse, sans attendre la lecture suivante.
    Comme pour orchestream, « bascule » est résolu ici à partir de l'état connu,
    pour qu'un double appui n'envoie pas deux fois la même chose.
    """

    def __init__(self, on_change, log):
        super().__init__(on_change, log)
        self.url = ""
        self.reachable: bool | None = None
        self.error = ""
        self.state: dict = {}  # dernier /status reçu

    def configure(self, url: str) -> None:
        url = multiview_url(url)
        if url != self.url:
            self.url, self.reachable, self.error, self.state = url, None, "", {}
        self._wake.set()

    # -- lecture d'état ----------------------------------------------------

    def _apply(self, data: dict | None) -> None:
        if not isinstance(data, dict) or not isinstance(data.get("streams"), list):
            return
        if data != self.state:
            self.state = data
            self._on_change()

    def _set_reachable(self, ok: bool, why: str = "") -> None:
        self.error = "" if ok else why
        if ok != self.reachable:
            if self.reachable is not None or not ok:
                self._log("info" if ok else "warn",
                          f"py-multiview joignable ({self.url})" if ok else (why or "py-multiview injoignable"))
            self.reachable = ok
            if not ok:
                self.state = {}
            self._on_change()

    def _run(self) -> None:
        while not self._stop.is_set():
            if not self.active or not self.url:
                self._sleep(5)
                continue
            try:
                status, data = _mv_state(self.url, 1.5)
            except NET_ERRORS as e:
                self._set_reachable(False, explain_multiview(self.url, exc=e))
            else:
                if _is_mv_state(status, data):
                    self._set_reachable(True)
                    self._apply(data)
                else:
                    self._set_reachable(False, explain_multiview(self.url, status, data))
            self._sleep(1 if self.reachable else 3)

    # -- ce que l'état contient -------------------------------------------

    def items(self, target: str) -> list[dict]:
        key = "routes" if target == "route" else "streams"
        items = self.state.get(key)
        return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []

    def choices(self, target: str) -> list[tuple[str, str]]:
        """(nom, description) des flux ou des routes, lus sur /status."""
        out = []
        for item in self.items(target):
            name = str(item.get("name") or "").strip() or str(item.get("index") or "")
            if name:
                extra = " · ".join(str(x) for x in (item.get("index"), item.get("source")) if x)
                out.append((name, f"{name}  ·  {extra}" if extra else name))
        return out

    def find(self, target: str, ref: str) -> dict | None:
        """Un flux ou une route par position, nom ou identifiant — comme l'API elle-même."""
        ref = str(ref or "").strip()
        items = self.items(target)
        if not ref:
            return None
        if ref.isdigit():
            return next((i for i in items if str(i.get("index")) == ref), None)
        low = ref.casefold()
        return (next((i for i in items if i.get("id") == ref), None)
                or next((i for i in items if str(i.get("name", "")).casefold() == low), None))

    def running(self, target: str, ref: str = "") -> bool | None:
        """État d'une cible, ou None tant qu'il n'est pas connu."""
        if not self.state:
            return None
        if target in ("stream", "mute", "route"):
            item = self.find("route" if target == "route" else "stream", ref)
            if item is None:
                return None
            return bool(item.get("muted") if target == "mute" else item.get("running"))
        section = self.state.get(MULTIVIEW_SECTION.get(target, target))
        return bool(section.get("running")) if isinstance(section, dict) else None

    def status_of(self, target: str, ref: str = "") -> str:
        """« active », « waiting », « error »… tel que py-multiview le rapporte."""
        if target in ("stream", "route"):
            item = self.find(target, ref)
            return str(item.get("status", "")) if item else ""
        section = self.state.get(MULTIVIEW_SECTION.get(target, target))
        return str(section.get("status", "")) if isinstance(section, dict) else ""

    def volume(self) -> int | None:
        monitor = self.state.get("monitor")
        return int(monitor.get("volume", 0)) if isinstance(monitor, dict) else None

    def unavailable(self) -> str:
        """Texte court pour la touche quand la commande ne passera pas."""
        if not self.url:
            return "NON RÉGLÉ"
        return "HORS LIGNE" if self.reachable is False else ""

    # -- commandes ---------------------------------------------------------

    def _send(self, path: str, what: str) -> bool:
        if not self.url:
            self._log("error", "py-multiview : adresse non réglée (Réglages → Intégrations)")
            return False
        try:
            status, data = _http("POST", f"{self.url}{path}", timeout=4)
        except NET_ERRORS as e:
            self._set_reachable(False, explain_multiview(self.url, exc=e))
            self._log("error", self.error)
            return False
        if 200 <= status < 300:
            self._set_reachable(True)
            self._apply(data)
            self._wake.set()
            return True
        message = ""
        if isinstance(data, dict) and isinstance(data.get("error"), dict):
            message = str(data["error"].get("message") or "")
        self._log("error", f"py-multiview : {message}" if message
                  else f"py-multiview a refusé {what} (HTTP {status})")
        self._wake.set()
        return False

    def command(self, target: str, ref: str, mode: str) -> bool:
        """Pilote une cible. « toggle » est résolu ici quand l'état est connu."""
        if mode == "toggle":
            current = self.running(target, ref)
            if current is not None:
                mode = "off" if current else "on"
        if target in ("stream", "mute", "route"):
            if not str(ref or "").strip():
                self._log("error", f"py-multiview : aucun {'flux' if target != 'route' else 'route'} choisi")
                return False
            quoted = urllib.parse.quote(str(ref).strip(), safe="")
            path = (f"/stream/{quoted}/mute/{mode}" if target == "mute"
                    else f"/{'route' if target == 'route' else 'stream'}/{quoted}/{mode}")
        else:
            path = f"/{target}/{mode}"
        return self._send(path, MULTIVIEW_TARGET_LABEL.get(target, target).lower())

    def set_volume(self, value: int) -> bool:
        return self._send(f"/monitor/volume/{max(0, min(100, int(value)))}", "le volume du retour")


# --- état lisible (Réglages, voyants) -----------------------------------------------------------------


def integration_status(engine) -> list[tuple[str, str, bool | None, str]]:
    """[(clé, nom, état, texte)] des trois intégrations. état : True OK, False en défaut, None inactif ou
    pas encore connu. Une intégration n'est surveillée que si une touche l'utilise (ou si forcée)."""
    out = []
    ic = engine.intercom
    if not ic.active:
        out.append(("intercom", "py-intercom", None, "Aucune touche ne l'utilise (« Tester » pour vérifier)."))
    elif ic.reachable is None:
        out.append(("intercom", "py-intercom", None, f"Connexion à {ic.base}…"))
    elif not ic.reachable:
        out.append(("intercom", "py-intercom", False, ic.error or f"Injoignable sur {ic.base}."))
    else:
        buses = "  ·  ".join(f"{bus_label(k)} {'ouvert' if b.get('active') else 'fermé'}"
                             f" ({BUS_MODES.get(b.get('mode'), b.get('mode'))})" for k, b in ic.buses.items())
        state = ic.state_text or ("En ligne" if ic.online else "Désarmé")
        out.append(("intercom", "py-intercom", ic.online,
                    f"{state}  ·  {buses or 'aucun bus proposé'}" if ic.online else
                    f"{state} : le client n'est pas en ligne, connecte-le au serveur intercom."))
    orch = engine.orchestream
    if not orch.url:
        out.append(("orchestream", "orchestream", None, "Adresse à renseigner pour l'utiliser."))
    elif not orch.active:
        out.append(("orchestream", "orchestream", None, "Aucune touche ne l'utilise (« Tester » pour vérifier)."))
    elif orch.connected:
        states = "  ·  ".join(f"{label} {'affiché' if orch.overlays.get(k) else 'masqué' if orch.overlays.get(k) is False else 'inconnu'}"
                              for k, label in orch.names())
        out.append(("orchestream", "orchestream", True, f"Flux en direct connecté  ·  {states}"))
    else:
        out.append(("orchestream", "orchestream", False, orch.error or "Flux en direct injoignable, nouvel essai en cours…"))
    ta = engine.tallies
    if not ta.devices:
        out.append(("tallies", "Tally32", None, "Aucun tally renseigné."))
    elif not ta.active:
        out.append(("tallies", "Tally32", None, "Aucune touche ne les utilise (« Tester » pour vérifier)."))
    else:
        parts, oks = [], []
        for d in ta.devices:
            s = ta.status.get(d["host"])
            name = d.get("name") or d["host"]
            if s is None:
                parts.append(f"{name} …")
                oks.append(None)
            elif not s.get("reachable"):
                parts.append(f"{name} injoignable")
                oks.append(False)
            else:
                tally = "PGM" if s.get("live") else "PVW" if s.get("preview") else ""
                parts.append(f"{name} " + ("connecté à vMix" if s.get("connected") else "sans vMix")
                             + (f" ({tally})" if tally else ""))
                oks.append(bool(s.get("connected")))
        ok = False if False in oks else None if None in oks else True
        out.append(("tallies", "Tally32", ok, "  ·  ".join(parts)))
    mv = engine.multiview
    if not mv.url:
        out.append(("multiview", "py-multiview", None, "Adresse à renseigner pour l'utiliser."))
    elif not mv.active:
        out.append(("multiview", "py-multiview", None, "Aucune touche ne l'utilise (« Tester » pour vérifier)."))
    elif mv.reachable is None:
        out.append(("multiview", "py-multiview", None, f"Connexion à {mv.url}…"))
    elif not mv.reachable:
        out.append(("multiview", "py-multiview", False, mv.error or f"Injoignable sur {mv.url}."))
    else:
        streams = mv.items("stream")
        live = sum(1 for s in streams if s.get("running"))
        parts = [f"{live}/{len(streams)} flux en lecture"]
        for key, text in (("sender", "émission OMT"), ("monitor", "retour audio"), ("floating", "fenêtre")):
            if mv.running(key):
                parts.append(text)
        out.append(("multiview", "py-multiview", True, "  ·  ".join(parts)))
    od = engine.orchdeck
    if not (od.url and od.token):
        out.append(("orchdeck", "orchestream (commandes)", None,
                    "Adresse et jeton API à renseigner pour l'utiliser." if not od.token
                    else "Adresse à renseigner pour l'utiliser."))
    elif not od.active:
        out.append(("orchdeck", "orchestream (commandes)", None, "Aucune touche ne l'utilise (« Tester » pour vérifier)."))
    elif od.connected:
        tok = od.get("token.name") or "?"
        live = "live en cours" if od.get("live.active") else "aucun live"
        out.append(("orchdeck", "orchestream (commandes)", True, f"jeton « {tok} »  ·  {live}"))
    else:
        out.append(("orchdeck", "orchestream (commandes)", False, od.error or f"Connexion à {od.url}…"))
    return out


# --- migration d'une config Companion -------------------------------------------------------------

_ORCH = re.compile(r"^(https?://[^/]+)/api/overlay/([\w-]+)/(show|hide)/?$")
_TALLY = re.compile(r"^https?://([^/]+)/vmix/reconnect/?$")


def merge_integrations(dst: dict, src: dict) -> list[str]:
    """« Ajouter les pages » d'un import : reprend les réglages d'intégrations que la conversion a trouvés
    (adresse orchestream si aucune, overlays manquants, tallys absents) sans toucher aux réglages actuels."""
    di, si = dst.setdefault("integrations", {}), src.get("integrations") or {}
    notes = []
    s_orch, d_orch = si.get("orchestream") or {}, di.setdefault("orchestream", {})
    if s_orch.get("url") and not d_orch.get("url"):
        d_orch["url"] = s_orch["url"]
        notes.append(f"orchestream : adresse {s_orch['url']}")
    if isinstance(s_orch.get("overlays"), list):
        have = {n for n, _ in overlay_list(d_orch)}
        new = [(n, lb) for n, lb in overlay_list(s_orch) if n not in have]
        if new:
            d_orch["overlays"] = [{"name": n, "label": lb} for n, lb in overlay_list(d_orch) + new]
            notes.append("overlays : " + ", ".join(lb for _, lb in new))
    devices = di.setdefault("tallies", [])
    hosts = {d.get("host") for d in devices}
    added = [d for d in si.get("tallies") or [] if d.get("host") and d["host"] not in hosts]
    devices.extend(copy.deepcopy(added))
    if added:
        notes.append(f"{len(added)} Tally32 ajouté(s)")
    return notes


def migrate(cfg: dict) -> list[str]:
    """Remplace les bricolages hérités de Companion par les intégrations natives (sur demande à l'import).
    Une touche Intercom en raccourci clavier garde ce raccourci « de secours » : il sert si py-intercom ne
    répond pas (API HTTP désactivée, ancienne version), on peut donc toujours parler."""
    from .actions import new_action
    from .health import parse_targets
    from .vmix import norm

    integ = cfg.setdefault("integrations", {})
    notes: list[str] = []
    counts = {"orch": 0, "tally": 0, "intercom": 0}
    converted: list[str] = []
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
                add_overlay(integ, on_m[0].group(2))
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
            # 3. raccourci maintenu « Intercom <bus> » -> API py-intercom (raccourci gardé en secours)
            label = norm(b.get("label", ""))
            bus = next((k for k, _ in INTERCOM_BUSES if k in label), None)
            if ("intercom" in label and bus and len(acts) == 1 and acts[0].get("type") == "hotkey"
                    and acts[0].get("hold")):
                keys = acts[0].get("keys", "")
                b["actions"] = [new_action("intercom.ptt", bus=bus, mode="hold", fallback_keys=keys)]
                b["feedbacks"] = [f for f in b.get("feedbacks", []) if f.get("cond", {}).get("type") != "pressed"]
                counts["intercom"] += 1
                converted.append(f"« {(b.get('label') or '').replace(chr(10), ' ')} » ({page.get('name')}) : "
                                 f"{keys or '?'} → bus {bus_label(bus)}")
    if counts["orch"]:
        notes.append(f"{counts['orch']} interrupteur(s) d'overlay orchestream reliés au flux en direct "
                     "(l'état suit les changements faits ailleurs).")
    if counts["tally"]:
        notes.append(f"Bouton de vérification des tallys converti : {len(integ.get('tallies', []))} Tally32 "
                     "surveillé(s) directement (GET /status), reconnexion native.")
    if counts["intercom"]:
        notes.append(f"{counts['intercom']} touche(s) Intercom passée(s) à l'API de py-intercom (talkback maintenu, "
                     "voyant d'état). Leur raccourci clavier reste en secours si py-intercom ne répond pas : "
                     + " ; ".join(converted[:6]) + (" …" if len(converted) > 6 else ""))
    return notes
