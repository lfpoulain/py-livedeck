"""Intégrations maison : py-intercom (talkback), Tally32 (tallys ESP32), py-multiview (multiviewer SRT/OMT),
et les apps qui exposent l'API Stream Deck commune /api/deck (orchestream-v2, py-multiview…).

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
from concurrent.futures import ThreadPoolExecutor

from .webreq import DIRECT, NET_ERRORS, error_kind, open_url

# Valeurs de repli tant que le service n'a pas répondu : la liste réelle vient de /status (py-intercom),
# (les touches d'orchestream viennent de son flux en direct).
INTERCOM_BUSES = [("regie", "Régie"), ("plateau", "Plateau"), ("vmix", "vMix")]
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
            if (self.reachable is not None or not ok) and self.wanted:
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


# --- API Stream Deck commune (/api/deck) : touches décrites par le serveur -------------------------
#
# Contrat défini par orchestream-v2 (docs/STREAM_DECK.md) et repris par les autres apps (py-multiview…) :
# chaque app décrit elle-même les touches qu'elle propose (identifiant stable, rubrique, commande et
# arguments, texte, couleur, allumée ou non, disponible ou non) et pousse les changements en direct.
# LiveDeck n'a donc aucune liste de commandes en dur : une touche enregistre l'app et l'« id » de la touche.


def normalize_url(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    if url and not url.startswith(("http://", "https://")):
        url = "http://" + url
    return url


# Apps qui exposent /api/deck. « cfg » : bloc de Réglages → Intégrations (l'adresse de py-multiview sert aussi
# à son ancienne API) ; « default » : adresse quand rien n'est saisi ; « token_required » : sans jeton, rien
# n'est envoyé (orchestream refuse tout) ; sinon le jeton est facultatif, et seul un 401 dit qu'il en faut un.
DECK_APPS: dict[str, dict] = {
    "orchestream": {"name": "orchestream", "label": "Orchestream", "cfg": "orchestream", "default": "",
                    "example": "http://serveur:5174", "token_required": True,
                    "token_where": "dans orchestream, Réglages › Sécurité › Jetons API",
                    "public_404": True, "port": None},
    "multiview": {"name": "py-multiview", "label": "py-multiview", "cfg": "multiview",
                  "default": "http://127.0.0.1:5090", "example": "192.168.1.42:5090", "token_required": False,
                  "token_where": "dans py-multiview, Préférences → Pilotage à distance",
                  "public_404": False, "port": 5090},
}
DECK_DEFAULT_APP = "orchestream"  # touches enregistrées avant l'arrivée des autres apps


def deck_spec(app: str) -> dict:
    return DECK_APPS.get(app) or {"name": app or "?", "label": app or "?", "cfg": app, "default": "",
                                  "example": "http://poste:port", "token_required": False,
                                  "token_where": "dans l'app", "public_404": False, "port": None}


def deck_name(app: str) -> str:
    return deck_spec(app)["name"]


def de(name: str) -> str:
    """« de py-multiview », « d'orchestream »."""
    return f"d'{name}" if name[:1].lower() in "aeéèêiîoôuyh" else f"de {name}"


def deck_url(app: str, url: str) -> str:
    """Adresse normalisée d'une app : celle par défaut si rien n'est saisi, port ajouté s'il manque."""
    spec = deck_spec(app)
    url = normalize_url(url or spec["default"])
    if url and spec["port"]:
        scheme, _, rest = url.partition("://")
        host, _, path = rest.partition("/")
        if ":" not in host.strip("[]") or host.endswith("]"):
            url = f"{scheme}://{host}:{spec['port']}" + (f"/{path}" if path else "")
    return url


def deck_config(cfg: dict, app: str) -> tuple[str, str]:
    """(adresse, jeton) réglés pour une app, adresse par défaut comprise."""
    block = (cfg.get("integrations") or {}).get(deck_spec(app)["cfg"]) or {}
    return deck_url(app, str(block.get("url") or "")), str(block.get("token") or "").strip()


def deck_configured(cfg: dict, app: str) -> bool:
    url, token = deck_config(cfg, app)
    return bool(url) and (bool(token) or not deck_spec(app)["token_required"])


def _orch_message(data) -> str:
    """Message d'erreur de l'API : {"error": {"code", "message"}} (ou {"message": "…"})."""
    if not isinstance(data, dict):
        return ""
    err = data.get("error")
    if isinstance(err, dict):
        return str(err.get("message") or err.get("code") or "")
    return str(err or data.get("message") or "")


def _orch_code(data) -> str:
    err = data.get("error") if isinstance(data, dict) else None
    return str(err.get("code") or "") if isinstance(err, dict) else ""


def token_help(app: str = DECK_DEFAULT_APP) -> str:
    return f"crée-en un {deck_spec(app)['token_where']} et colle-le dans Réglages → Intégrations"


TOKEN_HELP = token_help("orchestream")


def explain_deck(app: str, url: str, status: int | None = None, data=None, exc: BaseException | None = None,
                 token: str | None = None) -> str:
    """Message en français d'un échec de l'API Stream Deck d'une app. token="" : aucun jeton envoyé."""
    spec = deck_spec(app)
    name = spec["name"]
    host, where = _hostport(url)
    if exc is not None:
        kind = error_kind(exc, host)
        if kind == "refused":
            return f"{name} ne répond pas sur {where} : le serveur est-il lancé, l'adresse et le port sont-ils bons ?"
        return _net_text(kind, name, where)
    if _is_proxy_reply(status or 0, data):
        return _net_text("proxy", name, where) + f" (HTTP {status})"
    code, msg = _orch_code(data), _orch_message(data)
    if status == 401:
        if token == "" and not spec["token_required"]:
            return f"{name} demande un jeton API : {token_help(app)}"
        return f"{name} refuse le jeton API (absent, inconnu ou révoqué) : {token_help(app)}"
    if status == 403:
        return f"{name} : {msg or 'le jeton n’a pas la permission de cette commande'}"
    if status == 404 and code == "NOT_FOUND" and spec["public_404"]:  # adresse publique : pas de régie
        return (f"{where} est bien orchestream-v2, mais il refuse la régie par cette adresse (adresse publique "
                "ou tunnel) : utilise son adresse locale, ex. http://192.168.1.20:5174")
    if status == 404 and code != "COMMAND_NOT_FOUND":
        if app == "orchestream":
            return f"{where} répond, mais sans l'API Stream Deck d'orchestream-v2 (/api/deck) : vérifie l'adresse"
        return (f"{where} répond, mais sans l'API Stream Deck (/api/deck) : vérifie l'adresse, ou mets "
                f"{name} à jour")
    if status == 429:
        return f"{name} : {msg or 'trop de requêtes, réessaie dans un instant'}"
    return f"{name} : {msg or f'erreur {status}'}"


def explain_orchestream(url: str, status: int | None = None, data=None, exc: BaseException | None = None) -> str:
    """Message en français d'un échec de l'API Stream Deck d'orchestream-v2."""
    return explain_deck("orchestream", url, status, data, exc)


def _read_json(resp) -> object:
    try:
        return json.loads(resp.read(1 << 22).decode("utf-8", "replace") or "null")
    except ValueError:
        return None


def _auth_headers(token: str, accept: str = "application/json") -> dict:
    """Jeton dans l'en-tête Authorization, seulement s'il est rempli (jamais dans l'URL)."""
    headers = {"Accept": accept}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _deck_request(method: str, url: str, token: str, body: dict | None = None,
                  timeout: float = 3.0) -> tuple[int, object, dict]:
    """Requête vers /api/deck : (statut, JSON, en-têtes). Lève NET_ERRORS si injoignable."""
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = _auth_headers(token)
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with open_url(req, timeout) as resp:
            return resp.status, _read_json(resp), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, _read_json(e), dict(e.headers or {})


def _is_state(status: int, data) -> bool:
    return status == 200 and isinstance(data, dict) and isinstance(data.get("commands"), list)


def probe_deck(app: str, url: str, token: str, timeout: float = 4.0) -> tuple[bool | None, str]:
    """Test ponctuel (bouton « Tester ») : API joignable, jeton accepté, et les touches proposées."""
    spec = deck_spec(app)
    name = spec["name"]
    url, token = deck_url(app, url), (token or "").strip()
    if not url:
        return None, f"Adresse de {name} à renseigner (ex. {spec['example']})."
    if not token and spec["token_required"]:
        return None, f"Jeton API à renseigner ({spec['token_where']})."
    try:
        status, data, _ = _deck_request("GET", f"{url}/api/deck/state", token, timeout=timeout)
    except NET_ERRORS as e:
        return False, explain_deck(app, url, exc=e)
    if _is_state(status, data):
        tok = data.get("token") or {}
        keys = data.get("keys")
        who = (f"jeton « {tok.get('name') or '?'} » accepté" if token else
               f"API Stream Deck ({tok.get('name') or 'accès libre'})")
        if not isinstance(keys, list):
            return False, (f"{who}, mais cet {name} ne décrit pas les touches (version trop ancienne) : "
                           f"mets {name} à jour")
        extra = []
        if app == "orchestream":
            extra.append("live en cours" if (data.get("live") or {}).get("active") else "aucun live en cours")
        return True, " · ".join([who, f"{len(keys)} touche(s)"] + extra)
    return False, explain_deck(app, url, status, data, token=token)


def probe_orchestream(url: str, token: str, timeout: float = 4.0) -> tuple[bool | None, str]:
    return probe_deck("orchestream", url, token, timeout)


class _StreamUnavailable(Exception):
    """Le flux /api/deck/events ne s'ouvre pas, mais l'app répond : lecture de l'état en secours."""


class DeckApp(_Service):
    """Client de l'API Stream Deck commune d'une app (orchestream-v2, py-multiview…), une instance par app.
    Jeton facultatif (obligatoire pour orchestream), envoyé seulement dans l'en-tête Authorization.

    L'état et les touches arrivent par le flux en direct GET /api/deck/events : « state » (tout) à
    l'ouverture, puis « patch » (sections changées, touches modifiées, touches disparues). Le serveur
    regarde chaque seconde : la touche change dans la seconde, sans interroger. Flux coupé : reconnexion
    avec un délai croissant, et un nouvel état complet. Si le flux ne s'ouvre pas alors que l'app répond,
    l'état est relu chaque seconde sur GET /api/deck/state, et le flux retenté régulièrement.
    Appuyer : POST /api/deck/commands/<commande> avec les arguments que donne la touche."""

    STALL = 45.0       # le serveur envoie un commentaire toutes les 25 s : 45 s sans rien = flux perdu
    POLL = 1.0         # secours : lecture de l'état (orchestream : 120 lectures par 10 s et par jeton)
    RETRY_STREAM = 30.0  # secours : le flux est retenté au bout de ce délai

    def __init__(self, app: str, on_change, log):
        super().__init__(on_change, log)
        self.app = app
        self.spec = deck_spec(app)
        self.name = self.spec["name"]
        self.url = ""
        self.token = ""
        self.connected = False
        self.mode = ""     # "stream" (flux en direct) ou "poll" (secours), une fois connecté
        self.error = ""
        self.auth_failed = False         # dernier refus : 401 (jeton absent, inconnu ou révoqué)
        self.state: dict = {}            # DeckState sans les touches
        self.keys: dict[str, dict] = {}  # id -> DeckKey, dans l'ordre de l'app
        self.has_keys = False            # une liste de touches a été reçue (sinon : rien à juger)
        self._retry_at = 0.0             # 429 : pas de nouvelle requête avant (Retry-After)
        self._lock = threading.Lock()
        self._resp = None                # flux ouvert, fermé de force quand il faut s'arrêter

    def start(self) -> None:
        threading.Thread(target=self._guarded, name=f"deck-{self.app}", daemon=True).start()

    def configure(self, url: str, token: str) -> None:
        url, token = deck_url(self.app, url), (token or "").strip()
        if (url, token) != (self.url, self.token):
            self.url, self.token = url, token
            with self._lock:
                self.state, self.keys, self.has_keys = {}, {}, False
            self.error, self.auth_failed = "", False
            self._abort()
            self._wake.set()

    @property
    def configured(self) -> bool:
        return bool(self.url) and (bool(self.token) or not self.spec["token_required"])

    def set_wanted(self, wanted: bool) -> None:
        super().set_wanted(wanted)
        if not self.active:
            self._abort()

    def set_forced(self, forced: bool) -> None:
        super().set_forced(forced)
        if not self.active:
            self._abort()

    def stop(self) -> None:
        super().stop()
        self._abort()

    def _abort(self) -> None:
        """Coupe le flux ouvert : la lecture bloquée se termine aussitôt (sinon jusqu'au prochain message)."""
        resp = self._resp
        if resp is None:
            return
        try:
            resp.fp.raw._sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        try:
            resp.close()
        except Exception:
            pass

    # -- lecture -------------------------------------------------------------------------------
    def unavailable(self) -> str:
        """Pourquoi les touches ne peuvent pas servir (texte court pour la touche), ou "" si tout va bien.
        « JETON ? » : il en faut un et il manque ; « JETON REFUSÉ » : celui qui est réglé est refusé."""
        if not self.url:
            return "ADRESSE ?"
        if not self.token and self.spec["token_required"]:
            return "JETON ?"
        if not self.connected:
            if self.auth_failed:
                return "JETON REFUSÉ" if self.token else "JETON ?"
            return "HORS LIGNE"
        return ""

    def key(self, key_id: str) -> dict | None:
        with self._lock:
            return self.keys.get(key_id)

    def key_list(self) -> list[dict]:
        """Touches proposées, dans l'ordre de l'app (rubriques « Live », « Jeux »…)."""
        with self._lock:
            return list(self.keys.values())

    def get(self, path: str):
        """Valeur de l'état par chemin pointé (« token.name », « live.active »…), None si absente."""
        with self._lock:
            cur = self.state
        for part in path.split("."):
            if not isinstance(cur, dict):
                return None
            cur = cur.get(part)
        return cur

    def _explain(self, status: int | None = None, data=None, exc: BaseException | None = None,
                 url: str | None = None) -> str:
        return explain_deck(self.app, url or self.url, status, data, exc, token=self.token)

    def _set_connected(self, ok: bool, why: str = "", mode: str = "") -> None:
        if ok:
            self.error, self.auth_failed = "", False
        elif why and why != self.error:
            self.error = why
            if self.wanted:  # seulement surveillée (Réglages ouverts) : l'état s'affiche, sans journal
                self._log("warn", why)
        if ok and mode != self.mode:
            self.mode = mode
            if self.connected:
                self._log("info", f"{self.name} : flux en direct rétabli" if mode == "stream" else
                          f"{self.name} : flux en direct indisponible, lecture de l'état chaque seconde")
                self._on_change()
        if ok != self.connected:
            self.connected = ok
            if ok:
                self.mode = mode
                how = "flux en direct" if mode == "stream" else "lecture de l'état chaque seconde (secours)"
                self._log("info", f"{self.name} connecté ({self.url}, {how})")
            self._on_change()

    @staticmethod
    def _valid_key(k) -> bool:
        return isinstance(k, dict) and isinstance(k.get("id"), str)

    def _apply_state(self, data) -> None:
        if not isinstance(data, dict):
            return
        keys = data.get("keys")
        with self._lock:
            self.state = {k: v for k, v in data.items() if k != "keys"}
            self.keys = {k["id"]: k for k in keys if self._valid_key(k)} if isinstance(keys, list) else {}
            self.has_keys = isinstance(keys, list)
        self._on_change()

    def _apply_patch(self, data) -> None:
        if not isinstance(data, dict):
            return
        with self._lock:
            changed = data.get("changed")
            if isinstance(changed, dict):
                self.state.update(changed)
            if data.get("updatedAt"):
                self.state["updatedAt"] = data["updatedAt"]
            for k in data.get("keys") or []:
                if self._valid_key(k):
                    self.keys[k["id"]] = k
                    self.has_keys = True
            for kid in data.get("removedKeys") or []:
                self.keys.pop(kid, None)
        self._on_change()

    # -- boucle --------------------------------------------------------------------------------
    def _alive(self, url: str, token: str) -> bool:
        return not self._stop.is_set() and (url, token) == (self.url, self.token) and self.active

    def _run(self) -> None:
        delay = 1.0
        poll_until = 0.0  # secours en cours jusqu'à cette heure, puis on retente le flux
        while not self._stop.is_set():
            if not (self.active and self.configured):
                self._set_connected(False)
                self._sleep(5)
                continue
            wait = self._retry_at - time.monotonic()
            if wait > 0:  # 429 : on respecte Retry-After au lieu d'aggraver le blocage
                self._sleep(wait)
                continue
            url, token = self.url, self.token
            if time.monotonic() < poll_until:
                if self._poll_once(url, token):
                    self._sleep(self.POLL)
                else:
                    poll_until = 0.0
                    self._sleep(delay)
                    delay = min(delay * 2, 15)
                continue
            why = ""
            try:
                self._stream(url, token)
                delay = 1.0  # le flux avait abouti puis s'est fermé (jeton révoqué, serveur relancé) : vite
            except _StreamUnavailable as e:
                if self._poll_once(url, token):
                    self._log("warn", f"{self.name} : flux en direct indisponible ({e}), lecture de l'état "
                                      "chaque seconde en attendant")
                    poll_until = time.monotonic() + self.RETRY_STREAM
                    delay = 1.0
                    continue
                why = self.error
            except urllib.error.HTTPError as e:
                data = _peek_json(e)
                self._throttle(e.code, dict(e.headers or {}))
                why = self._explain(e.code, data, url=url)
                self.auth_failed = e.code == 401
                if e.code == 401:  # jeton refusé : orchestream bloque un jeton présenté 30 fois par minute
                    delay = max(delay, 10.0)
            except NET_ERRORS as e:
                why = "" if not self._alive(url, token) else self._explain(exc=e, url=url)
            if not self._alive(url, token):
                continue
            self._set_connected(False, why)
            self._sleep(delay)
            delay = min(delay * 2, 30)

    def _stream(self, url: str, token: str) -> None:
        """Ouvre GET /api/deck/events et applique les messages tant que le service en a besoin."""
        headers = _auth_headers(token, "text/event-stream")
        headers["Cache-Control"] = "no-cache"
        req = urllib.request.Request(f"{url}/api/deck/events", headers=headers)
        try:
            resp = open_url(req, self.STALL)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403, 429) or (e.code == 404 and _orch_code(_peek_json(e)) == "NOT_FOUND"):
                raise
            raise _StreamUnavailable(f"HTTP {e.code}") from None
        with resp:
            ctype = resp.headers.get("Content-Type", "")
            if "event-stream" not in ctype:
                raise _StreamUnavailable(ctype or "réponse sans type")
            self._resp = resp
            try:
                for event, data in self._sse(resp, url, token):
                    if event == "state":
                        self._apply_state(data)
                        self._set_connected(True, mode="stream")
                    elif event == "patch":
                        self._apply_patch(data)
            finally:
                self._resp = None

    def _sse(self, resp, url: str, token: str):
        """Messages SSE (événement, données JSON) d'une réponse ouverte, tant que le service est actif."""
        event, data = "", []
        for raw in resp:
            if not self._alive(url, token):
                return
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if line.startswith(":"):  # commentaire « keepalive »
                continue
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].lstrip(" "))
            elif not line and (event or data):
                try:
                    yield event, json.loads("\n".join(data) or "null")
                except ValueError:
                    pass
                event, data = "", []

    def _poll_once(self, url: str, token: str) -> bool:
        """Secours : GET /api/deck/state. True si l'état a été lu."""
        try:
            status, data, headers = _deck_request("GET", f"{url}/api/deck/state", token)
        except NET_ERRORS as e:
            if self._alive(url, token):
                self._set_connected(False, self._explain(exc=e, url=url))
            return False
        if not self._alive(url, token):
            return False
        if _is_state(status, data):
            with self._lock:
                same = {k: v for k, v in data.items() if k not in ("keys", "updatedAt")} == \
                    {k: v for k, v in self.state.items() if k != "updatedAt"} and \
                    isinstance(data.get("keys"), list) and \
                    data["keys"] == list(self.keys.values())
            if not same:
                self._apply_state(data)
            self._set_connected(True, mode="poll")
            return True
        self._throttle(status, headers)
        self.auth_failed = status == 401
        self._set_connected(False, self._explain(status, data, url=url))
        return False

    def _throttle(self, status: int, headers: dict) -> None:
        if status != 429:
            return
        try:
            secs = float({k.lower(): v for k, v in headers.items()}.get("retry-after") or 2)
        except (TypeError, ValueError):
            secs = 2.0
        self._retry_at = time.monotonic() + min(max(secs, 1.0), 60.0)

    # -- appui ---------------------------------------------------------------------------------
    def press(self, key_id: str, title: str = "") -> bool:
        """Lance la commande de la touche avec ses arguments ; journalise la réponse de l'app (déjà en
        français). Une touche d'état (sans commande) ne fait rien."""
        n = self.name
        if not self.url:
            self._log("error", f"{n} : adresse non réglée (Réglages → Intégrations)")
            return False
        if not self.token and self.spec["token_required"]:
            self._log("error", f"{n} : jeton API non réglé (Réglages → Intégrations)")
            return False
        k = self.key(key_id)
        name = (k or {}).get("title") or title or key_id or "?"
        if k is None:
            if not key_id:
                self._log("error", f"{n} : aucune touche choisie (inspecteur → action {self.spec['label']})")
            elif self.has_keys:
                self._log("error", f"{n} · {name} : touche « {key_id} » inconnue {de(n)} "
                                   "(supprimée, ou non permise à ce jeton)")
            else:
                self._log("error", f"{n} · {name} : liste des touches pas encore reçue "
                                   f"({self.error or 'connexion en cours'})")
            return False
        command = k.get("command")
        if not command:
            return False  # touche d'état : elle ne fait qu'afficher
        args = {str(a): str(v) for a, v in (k.get("args") or {}).items()} if isinstance(k.get("args"), dict) else {}
        body = {"args": args} if args else {}
        path = urllib.parse.quote(str(command), safe=".")
        try:
            status, data, headers = _deck_request("POST", f"{self.url}/api/deck/commands/{path}", self.token, body)
        except NET_ERRORS as e:
            self._log("error", f"{n} · {name} : {self._explain(exc=e)}")
            return False
        if 200 <= status < 300:
            msg = data.get("message") if isinstance(data, dict) else ""
            self._log("info", f"{n} · {msg or name}")
            if self.mode == "poll":
                self._wake.set()  # secours : relit l'état tout de suite (le flux, lui, l'envoie de lui-même)
            return True
        self._throttle(status, headers)
        self.auth_failed = status == 401
        self._log("error", f"{n} · {name} : {self._explain(status, data).removeprefix(f'{n} : ')}")
        return False


class Orchestream(DeckApp):
    """API Stream Deck d'orchestream-v2 (jeton obligatoire)."""

    def __init__(self, on_change, log):
        super().__init__("orchestream", on_change, log)


def _peek_json(e: urllib.error.HTTPError) -> object:
    """Corps JSON d'une erreur HTTP, lu une seule fois (relu ensuite depuis le cache)."""
    if not hasattr(e, "_livedeck_json"):
        e._livedeck_json = _read_json(e)
    return e._livedeck_json


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
        self._busy: set[str] = set()  # sondages en cours : jamais deux à la fois pour le même tally

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
        finally:
            self._busy.discard(host)
        if new != self.status.get(host):
            self.status[host] = new
            self._on_change()

    def _run(self) -> None:
        while not self._stop.is_set():
            if self.active:
                for d in list(self.devices):
                    # Chaque modification de la config réveille ce fil : sans cette garde, des tallys
                    # injoignables empilaient des sondages et l'état affiché prenait du retard.
                    if d["host"] in self._busy:
                        continue
                    self._busy.add(d["host"])
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


def probe_multiview(url: str, token: str = "", timeout: float = 4.0) -> tuple[bool | None, str]:
    """Test ponctuel (bouton « Tester ») : l'API d'origine (GET /status), puis l'API Stream Deck (/api/deck)
    qui décrit ses touches. (True les deux / None joignable, touches indisponibles / False, message)."""
    url = multiview_url(url) or deck_url("multiview", "")
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
    text = "py-multiview : " + ", ".join(bits)
    ok, deck = probe_deck("multiview", url, token, timeout)
    return (True if ok else None), f"{text}  ·  touches : {deck.removeprefix('py-multiview : ')}"


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
        url = multiview_url(url) or deck_url("multiview", "")  # même adresse par défaut que ses touches
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
            if (self.reachable is not None or not ok) and self.wanted:
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


def deck_status(d: DeckApp) -> tuple[bool | None, str]:
    """(état, texte) d'une app pilotée par l'API Stream Deck commune."""
    spec = d.spec
    if not d.configured:
        return None, ("Adresse et jeton API à renseigner pour l'utiliser." if not d.url else
                      f"Jeton API à renseigner ({spec['token_where']}).")
    if not d.active:
        return None, "Aucune touche ne l'utilise (« Tester » pour vérifier)."
    if not d.connected:
        return False, d.error or f"Connexion à {d.url}…"
    how = "flux en direct" if d.mode == "stream" else "lecture de l'état (flux indisponible)"
    keys = f"{len(d.keys)} touche(s)" if d.has_keys else f"touches non décrites ({d.name} à mettre à jour)"
    parts = [keys, how]
    if d.token:
        parts.insert(0, f"jeton « {d.get('token.name') or '?'} »")
    if d.app == "orchestream":
        parts.insert(2, "live en cours" if d.get("live.active") else "aucun live")
    return True, "  ·  ".join(parts)


def _multiview_status(mv: "Multiview", d: DeckApp) -> tuple[bool | None, str]:
    """py-multiview : son API d'origine (anciennes touches) et ses touches décrites, en une ligne."""
    rows = []
    if mv.active:
        if mv.reachable is None:
            rows.append((None, f"Connexion à {mv.url}…"))
        elif not mv.reachable:
            rows.append((False, mv.error or f"Injoignable sur {mv.url}."))
        else:
            streams = mv.items("stream")
            live = sum(1 for s in streams if s.get("running"))
            parts = [f"{live}/{len(streams)} flux en lecture"]
            for key, text in (("sender", "émission OMT"), ("monitor", "retour audio"), ("floating", "fenêtre")):
                if mv.running(key):
                    parts.append(text)
            rows.append((True, "  ·  ".join(parts)))
    if d.active:
        ok, text = deck_status(d)
        rows.append((ok, f"touches : {text}" if mv.active else text))
    if not rows:
        return None, "Aucune touche ne l'utilise (« Tester » pour vérifier)."
    oks = [ok for ok, _ in rows]
    if len(rows) == 2 and rows[0][0] is False and rows[1][0] is False:
        rows = rows[:1]  # éteint : une seule fois
    return (False if False in oks else None if None in oks else True), "  ·  ".join(t for _, t in rows)


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
    out.append(("orchestream", "orchestream", *deck_status(engine.orchestream)))
    for app, spec in DECK_APPS.items():  # autres apps à touches décrites (py-multiview : plus bas, avec son API)
        if app not in ("orchestream", "multiview"):
            out.append((app, spec["name"], *deck_status(engine.deck(app))))
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
    out.append(("multiview", "py-multiview", *_multiview_status(engine.multiview, engine.deck("multiview"))))
    return out


# --- migration d'une config Companion -------------------------------------------------------------

_ORCH = re.compile(r"^(https?://[^/]+)/api/overlay/([\w-]+)/(show|hide)/?$")
_TALLY = re.compile(r"^https?://([^/]+)/vmix/reconnect/?$")


def merge_integrations(dst: dict, src: dict) -> list[str]:
    """« Ajouter les pages » d'un import : reprend les réglages d'intégrations que la conversion a trouvés
    (adresse orchestream si aucune, tallys absents) sans toucher aux réglages actuels."""
    di, si = dst.setdefault("integrations", {}), src.get("integrations") or {}
    notes = []
    s_orch, d_orch = si.get("orchestream") or {}, di.setdefault("orchestream", {})
    if s_orch.get("url") and not d_orch.get("url"):
        d_orch["url"] = s_orch["url"]
        notes.append(f"orchestream : adresse {s_orch['url']}")
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
            # 1. interrupteur show / hide vers orchestream -> touche « Basculer le widget » d'orchestream-v2
            on_m = [_ORCH.match(str(a.get("url") or "")) for a in acts if a.get("type") == "http"]
            off_m = [_ORCH.match(str(a.get("url") or "")) for a in off if a.get("type") == "http"]
            if (b.get("mode") == "toggle" and len(acts) == len(off) == 1 and on_m[:1] and off_m[:1]
                    and on_m[0] and off_m[0] and on_m[0].group(2) == off_m[0].group(2)):
                orch = integ.setdefault("orchestream", {})
                if not orch.get("url"):
                    orch["url"] = on_m[0].group(1)
                b["mode"], b["actions_off"] = "press", []
                b["actions"] = [new_action("deck.key", app="orchestream", key=f"overlay.toggle:{on_m[0].group(2)}")]
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
        notes.append(f"{counts['orch']} interrupteur(s) d'overlay orchestream passés aux touches d'orchestream-v2 "
                     "(état en direct ; jeton API à renseigner dans Réglages → Intégrations).")
    if counts["tally"]:
        notes.append(f"Bouton de vérification des tallys converti : {len(integ.get('tallies', []))} Tally32 "
                     "surveillé(s) directement (GET /status), reconnexion native.")
    if counts["intercom"]:
        notes.append(f"{counts['intercom']} touche(s) Intercom passée(s) à l'API de py-intercom (talkback maintenu, "
                     "voyant d'état). Leur raccourci clavier reste en secours si py-intercom ne répond pas : "
                     + " ; ".join(converted[:6]) + (" …" if len(converted) > 6 else ""))
    return notes
