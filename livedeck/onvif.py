"""Caméras ONVIF (PTZ) : client SOAP minimal, sans dépendance.

Ce qui est piloté : pan / tilt / zoom en continu (tant que la touche est enfoncée), arrêt, position d'origine,
rappel et enregistrement d'un preset. L'identification part des deux façons qu'une caméra peut exiger :
jeton WS-Security (mot de passe haché, à l'heure de la caméra) et Digest HTTP.

Une caméra n'est surveillée que si une touche l'utilise (ou pendant que la fenêtre Réglages est ouverte) ;
elle n'est jamais jointe à travers un proxy.
"""
from __future__ import annotations

import base64
import calendar
import hashlib
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from xml.sax.saxutils import escape

from .integrations import _Service, _net_text
from .webreq import NET_ERRORS, error_kind

DEVICE_PATH = "/onvif/device_service"
NS_SOAP = "http://www.w3.org/2003/05/soap-envelope"
NS_DEVICE = "http://www.onvif.org/ver10/device/wsdl"
NS_MEDIA = "http://www.onvif.org/ver10/media/wsdl"
NS_PTZ = "http://www.onvif.org/ver20/ptz/wsdl"
NS_TT = "http://www.onvif.org/ver10/schema"
NS_WSSE = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
NS_WSU = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"
_DIGEST = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest"
_B64 = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary"

# Mouvements continus : commande -> (pan, tilt, zoom), chacun à -1, 0 ou 1 (multiplié par la vitesse)
MOVES = {"left": (-1, 0, 0), "right": (1, 0, 0), "up": (0, 1, 0), "down": (0, -1, 0),
         "upleft": (-1, 1, 0), "upright": (1, 1, 0), "downleft": (-1, -1, 0), "downright": (1, -1, 0),
         "zoomin": (0, 0, 1), "zoomout": (0, 0, -1)}
# Flèches : seulement des signes que la police des touches sait dessiner (◄ ► ▲ ▼, pas ◀ ▶ ni les diagonales)
MOVE_CHOICES = [("left", "◄ Gauche"), ("right", "► Droite"), ("up", "▲ Haut"), ("down", "▼ Bas"),
                ("upleft", "▲◄ Haut gauche"), ("upright", "▲► Haut droite"),
                ("downleft", "▼◄ Bas gauche"), ("downright", "▼► Bas droite"),
                ("zoomin", "Zoom +"), ("zoomout", "Zoom −")]
COMMANDS = MOVE_CHOICES + [("stop", "Arrêter le mouvement"), ("preset", "Aller à un preset"),
                           ("save", "Enregistrer la position dans un preset"), ("home", "Position d'origine")]
ARROWS = {"left": "◄", "right": "►", "up": "▲", "down": "▼", "upleft": "▲◄", "upright": "▲►",
          "downleft": "▼◄", "downright": "▼►", "zoomin": "Zoom +", "zoomout": "Zoom −"}


class OnvifError(Exception):
    """Refus ou réponse inattendue d'une caméra. kind : auth | fault | noptz | notonvif."""

    def __init__(self, kind: str, text: str):
        super().__init__(text)
        self.kind = kind


def split_host(addr: str) -> tuple[str, str, str]:
    """« [utilisateur:mot de passe@]hôte[:port][/chemin] » -> (adresse du service, utilisateur, mot de passe)."""
    addr = (addr or "").strip()
    if "://" not in addr:
        addr = "http://" + addr
    u = urllib.parse.urlsplit(addr)
    host = u.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    try:
        port = u.port
    except ValueError:
        port = None
    url = f"{u.scheme}://{host}" + (f":{port}" if port else "") + (u.path if u.path.strip("/") else DEVICE_PATH)
    return (url if host else ""), urllib.parse.unquote(u.username or ""), urllib.parse.unquote(u.password or "")


def parse_cameras(text: str) -> list[dict]:
    """« Caméra 1 = 192.168.1.60 » ou « Caméra 2 = admin:secret@192.168.1.61:8080 », une caméra par ligne."""
    out, seen = [], set()
    for line in (text or "").splitlines():
        name, sep, addr = line.partition("=")
        addr = (addr if sep else name).strip()
        name = name.strip() if sep else addr.rpartition("@")[2]
        if not addr or not split_host(addr)[0] or name in seen:
            continue
        seen.add(name)
        out.append({"name": name, "host": addr})
    return out


def cameras_text(block: dict | None) -> str:
    return "\n".join(f"{c.get('name')} = {c.get('host')}" for c in (block or {}).get("cameras") or []
                     if isinstance(c, dict) and c.get("host"))


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find(root, *names):
    """Premier élément dont le nom local est names[0], puis son descendant names[1]… (espaces de noms ignorés)."""
    cur = [root]
    for name in names:
        cur = [el for parent in cur for el in parent.iter() if _local(el.tag) == name and el is not parent]
        if not cur:
            return None
    return cur[0]


def _text(root, *names) -> str:
    el = _find(root, *names)
    return (el.text or "").strip() if el is not None else ""


def _security(user: str, password: str, offset: float) -> str:
    """En-tête WS-Security UsernameToken : Base64(SHA-1(nonce + date + mot de passe)), à l'heure de la caméra."""
    nonce = os.urandom(16)
    created = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() + offset))
    digest = base64.b64encode(hashlib.sha1(nonce + created.encode() + password.encode("utf-8")).digest()).decode()
    return (f'<wsse:Security xmlns:wsse="{NS_WSSE}" xmlns:wsu="{NS_WSU}" s:mustUnderstand="1"><wsse:UsernameToken>'
            f"<wsse:Username>{escape(user)}</wsse:Username>"
            f'<wsse:Password Type="{_DIGEST}">{digest}</wsse:Password>'
            f'<wsse:Nonce EncodingType="{_B64}">{base64.b64encode(nonce).decode()}</wsse:Nonce>'
            f"<wsu:Created>{created}</wsu:Created></wsse:UsernameToken></wsse:Security>")


def _fault(raw: bytes) -> tuple[str, str] | None:
    """(nature, texte) d'une erreur SOAP, ou None si la réponse n'en est pas une."""
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return None
    fault = _find(root, "Fault")
    if fault is None:
        return None
    codes = " ".join((el.text or "") for el in fault.iter() if _local(el.tag) == "Value").lower()
    text = _text(fault, "Reason", "Text") or _text(fault, "faultstring") or "erreur SOAP"
    auth = any(w in codes + " " + text.lower() for w in ("notauthorized", "not authorized", "unauthorized",
                                                          "failedauthentication", "authority"))
    return ("auth" if auth else "fault"), text


class Camera:
    """Une caméra : adresse, identifiants, et ce qui a été découvert (service PTZ, profil, presets)."""

    def __init__(self, name: str, host: str, user: str = "", password: str = ""):
        self.name = name
        self.host = host
        url, u, p = split_host(host)
        self.url, self.user, self.password = url, u or user, p if u else password
        self.ptz_url = self.profile = ""
        self.offset = 0.0  # heure de la caméra - heure de ce PC (le jeton WS-Security est daté)
        self.reachable: bool | None = None
        self.error = ""
        self.info = ""
        self.preset_list: list[tuple[str, str]] = []  # (jeton, nom)
        self._lock = threading.Lock()
        handlers = [urllib.request.ProxyHandler({})]
        if self.user:
            mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
            mgr.add_password(None, self.url, self.user, self.password)
            handlers.append(urllib.request.HTTPDigestAuthHandler(mgr))
        self._opener = urllib.request.build_opener(*handlers)

    @property
    def key(self) -> tuple:
        return self.name, self.host, self.user, self.password

    @property
    def where(self) -> str:
        return urllib.parse.urlsplit(self.url).netloc or self.host

    def _same_host(self, xaddr: str) -> str:
        """Adresse d'un service annoncée par la caméra. Une caméra derrière une redirection, ou qui a gardé
        une ancienne IP, annonce une adresse injoignable : on garde alors l'hôte réglé, avec son chemin."""
        if not xaddr:
            return ""
        mine, theirs = urllib.parse.urlsplit(self.url), urllib.parse.urlsplit(xaddr)
        if theirs.hostname == mine.hostname:
            return xaddr
        return urllib.parse.urlunsplit((mine.scheme, mine.netloc, theirs.path, theirs.query, ""))

    def call(self, url: str, ns: str, body: str, timeout: float = 3.0, auth: bool = True) -> ET.Element:
        """Envoie une requête SOAP ; renvoie le corps de la réponse. Lève OnvifError ou une erreur réseau."""
        action = body.split(">", 1)[0].split(":", 1)[-1].split()[0].strip("</>")
        header = _security(self.user, self.password, self.offset) if auth and self.user else ""
        env = (f'<?xml version="1.0" encoding="UTF-8"?><s:Envelope xmlns:s="{NS_SOAP}" xmlns:tt="{NS_TT}">'
               f"<s:Header>{header}</s:Header><s:Body>{body}</s:Body></s:Envelope>").encode("utf-8")
        req = urllib.request.Request(url, data=env, method="POST", headers={
            "Content-Type": f'application/soap+xml; charset=utf-8; action="{ns}/{action}"'})
        try:
            with self._opener.open(req, timeout=timeout) as resp:
                raw = resp.read(1 << 20)
        except urllib.error.HTTPError as e:
            raw = e.read(1 << 20)
            fault = _fault(raw)
            if e.code == 401 or (fault and fault[0] == "auth"):
                raise OnvifError("auth", "identifiants refusés" if self.user else "identifiants demandés") from None
            if fault:
                raise OnvifError("fault", fault[1]) from None
            raise OnvifError("notonvif", f"HTTP {e.code}") from None
        fault = _fault(raw)
        if fault:
            raise OnvifError(*fault)
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            raise OnvifError("notonvif", "réponse illisible") from None
        body_el = _find(root, "Body")
        if body_el is None:
            raise OnvifError("notonvif", "réponse sans corps SOAP")
        return body_el

    def connect(self, timeout: float = 3.0) -> None:
        """Découvre le service PTZ et le profil à piloter (sans effet si c'est déjà fait)."""
        with self._lock:
            if self.ptz_url and self.profile:
                return
            if not self.url:
                raise OnvifError("notonvif", "adresse à renseigner")
            try:  # heure de la caméra : sans elle, un jeton daté de ce PC est refusé si les horloges diffèrent
                t = self.call(self.url, NS_DEVICE, f'<tds:GetSystemDateAndTime xmlns:tds="{NS_DEVICE}"/>',
                              timeout, auth=False)
                utc = _find(t, "UTCDateTime")
                if utc is not None:
                    parts = [int(_text(utc, n) or 0) for n in ("Year", "Month", "Day", "Hour", "Minute", "Second")]
                    if parts[0] >= 2000:  # horloge jamais réglée (1970) : un décalage n'aurait aucun sens
                        self.offset = calendar.timegm((*parts, 0, 0, 0)) - time.time()
            except (OnvifError, ValueError, OverflowError):
                self.offset = 0.0
            caps = self.call(self.url, NS_DEVICE, f'<tds:GetCapabilities xmlns:tds="{NS_DEVICE}">'
                             "<tds:Category>All</tds:Category></tds:GetCapabilities>", timeout)
            ptz, media = _text(caps, "PTZ", "XAddr"), _text(caps, "Media", "XAddr")
            if not ptz:
                raise OnvifError("noptz", "cette caméra n'annonce pas de service PTZ")
            profiles = self.call(self._same_host(media) or self.url, NS_MEDIA,
                                 f'<trt:GetProfiles xmlns:trt="{NS_MEDIA}"/>', timeout)
            found = [(el.get("token") or "", _find(el, "PTZConfiguration") is not None)
                     for el in profiles.iter() if _local(el.tag) == "Profiles"]
            tokens = [tok for tok, has in found if tok and has] or [tok for tok, _ in found if tok]
            if not tokens:
                raise OnvifError("noptz", "aucun profil vidéo sur cette caméra")
            self.ptz_url, self.profile = self._same_host(ptz), tokens[0]

    def _ptz(self, body: str, timeout: float = 3.0) -> ET.Element:
        self.connect(timeout)
        return self.call(self.ptz_url, NS_PTZ, body, timeout)

    def _op(self, name: str, inner: str = "") -> str:
        return (f'<tptz:{name} xmlns:tptz="{NS_PTZ}"><tptz:ProfileToken>{escape(self.profile)}</tptz:ProfileToken>'
                f"{inner}</tptz:{name}>")

    def move(self, command: str, speed: float) -> None:
        """Mouvement continu : il dure jusqu'à `stop`. Seul l'axe demandé est envoyé, pour qu'une touche de
        zoom n'arrête pas un panoramique en cours (et inversement)."""
        self.connect()
        pan, tilt, zoom = MOVES[command]
        s = max(0.01, min(1.0, speed))
        vel = (f'<tt:Zoom x="{zoom * s:.2f}"/>' if zoom else f'<tt:PanTilt x="{pan * s:.2f}" y="{tilt * s:.2f}"/>')
        self._ptz(self._op("ContinuousMove", f"<tptz:Velocity>{vel}</tptz:Velocity>"))

    def stop(self, pantilt: bool = True, zoom: bool = True) -> None:
        self.connect()
        self._ptz(self._op("Stop", f"<tptz:PanTilt>{str(pantilt).lower()}</tptz:PanTilt>"
                                   f"<tptz:Zoom>{str(zoom).lower()}</tptz:Zoom>"))

    def home(self) -> None:
        self.connect()
        self._ptz(self._op("GotoHomePosition"))

    def presets(self) -> list[tuple[str, str]]:
        self.connect()
        got = self._ptz(self._op("GetPresets"))
        self.preset_list = [(el.get("token") or "", _text(el, "Name")) for el in got.iter()
                            if _local(el.tag) == "Preset" and el.get("token")]
        return self.preset_list

    def resolve(self, preset: str, refresh: bool = True) -> str:
        """Jeton d'un preset désigné par son nom ou son numéro ("" s'il n'existe pas)."""
        want = str(preset or "").strip()
        for attempt in range(2):
            for tok, name in self.preset_list:
                if name and name.casefold() == want.casefold():
                    return tok
            for tok, _ in self.preset_list:
                if tok == want:
                    return tok
            if attempt or not refresh:
                break
            self.presets()
        return ""

    def goto(self, preset: str) -> None:
        self.connect()
        tok = self.resolve(preset) or str(preset).strip()  # inconnu : tenté tel quel, la caméra tranchera
        self._ptz(self._op("GotoPreset", f"<tptz:PresetToken>{escape(tok)}</tptz:PresetToken>"))

    def save(self, preset: str) -> None:
        """Enregistre la position actuelle : remplace le preset s'il existe, sinon en crée un à ce nom."""
        self.connect()
        tok, want = self.resolve(preset), str(preset).strip()
        inner = (f"<tptz:PresetToken>{escape(tok)}</tptz:PresetToken>" if tok
                 else f"<tptz:PresetName>{escape(want)}</tptz:PresetName>")
        try:
            self._ptz(self._op("SetPreset", inner))
        except OnvifError as e:
            if tok or e.kind != "fault" or not want.isdigit():
                raise
            # caméra à emplacements numérotés : elle refuse un nom libre mais accepte le numéro
            self._ptz(self._op("SetPreset", f"<tptz:PresetToken>{escape(want)}</tptz:PresetToken>"))
        try:
            self.presets()
        except (OnvifError, *NET_ERRORS):
            pass

    def forget(self) -> None:
        """Après une erreur réseau : la prochaine commande redécouvre la caméra (elle a pu redémarrer)."""
        self.ptz_url = self.profile = ""


def explain(cam: Camera, exc: BaseException) -> str:
    """Erreur d'une caméra en français, avec quoi vérifier."""
    if isinstance(exc, OnvifError):
        if exc.kind == "auth":
            return (f"{exc} par {cam.where} : vérifie l'utilisateur et le mot de passe ONVIF (souvent un compte à "
                    "créer dans la caméra, distinct de celui de sa page web)")
        if exc.kind == "notonvif":
            return (f"{cam.where} répond, mais pas comme une caméra ONVIF ({exc}) : vérifie le port et qu'ONVIF "
                    "est activé dans la caméra")
        return str(exc)
    return _net_text(error_kind(exc, urllib.parse.urlsplit(cam.url).hostname or ""), "caméra", cam.where)


def probe(host: str, user: str = "", password: str = "", timeout: float = 4.0) -> tuple[bool | None, str]:
    """Test ponctuel d'une caméra : (True PTZ prêt / False en défaut, message)."""
    cam = Camera("", host, user, password)
    try:
        cam.connect(timeout)
        n = len(cam.presets())
    except (OnvifError, *NET_ERRORS) as e:
        return False, explain(cam, e)
    return True, f"PTZ prêt  ·  {n} preset(s)"


class Onvif(_Service):
    """Les caméras ONVIF réglées : connexion en tâche de fond, commandes PTZ, liste des presets."""

    def __init__(self, on_change, log):
        super().__init__(on_change, log)
        self.cameras: list[Camera] = []
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="onvif")
        self._busy: set[tuple] = set()

    def configure(self, block: dict | None) -> None:
        block = block if isinstance(block, dict) else {}
        user, password = str(block.get("user") or ""), str(block.get("password") or "")
        old = {c.key: c for c in self.cameras}
        cams = []
        for d in block.get("cameras") or []:
            if not (isinstance(d, dict) and d.get("host")):
                continue
            new = Camera(str(d.get("name") or d["host"]), str(d["host"]), user, password)
            cams.append(old.get(new.key) or new)  # inchangée : on garde sa connexion et ses presets
        if [c.key for c in cams] != [c.key for c in self.cameras]:
            self.cameras = cams
            self._wake.set()

    def camera(self, name: str) -> Camera | None:
        """Caméra de ce nom ; sans nom, la première (cas d'une seule caméra)."""
        cams = self.cameras
        name = str(name or "").strip()
        if not name:
            return cams[0] if cams else None
        return next((c for c in cams if c.name == name), None)

    def choices(self) -> list[tuple[str, str]]:
        return [(c.name, f"{c.name}  ·  {c.where}") for c in self.cameras]

    def _poll(self, cam: Camera) -> None:
        try:
            cam.presets()  # connecte si besoin ; sert aussi de test de présence
            new = (True, "")
        except (OnvifError, *NET_ERRORS) as e:
            if not isinstance(e, OnvifError):
                cam.forget()
            new = (False, explain(cam, e))
        finally:
            self._busy.discard(cam.key)
        if new != (cam.reachable, cam.error):
            cam.reachable, cam.error = new
            self._on_change()

    def _run(self) -> None:
        while not self._stop.is_set():
            if self.active:
                for cam in list(self.cameras):
                    if cam.key in self._busy:
                        continue
                    self._busy.add(cam.key)
                    try:
                        self._pool.submit(self._poll, cam)
                    except RuntimeError:  # arrêt en cours : le pool est déjà fermé
                        return
            self._sleep(10 if self.active else 5)

    def unavailable(self, name: str) -> str:
        """Pourquoi cette caméra ne peut pas servir, en un mot pour la touche ("" si tout va bien ou inconnu)."""
        cam = self.camera(name)
        if cam is None:
            return "À RÉGLER" if not self.cameras else "CAMÉRA ?"
        return "HORS LIGNE" if cam.reachable is False else ""

    def run(self, a: dict, phase: str) -> None:
        """Exécute une action « onvif.ptz ». phase « up » : fin d'un mouvement maintenu."""
        command = a.get("command") or "left"
        cam = self.camera(a.get("camera"))
        if cam is None:
            if phase == "down":
                self._log("error", "ONVIF : aucune caméra réglée (Réglages → Intégrations)" if not self.cameras
                          else f"ONVIF : la caméra « {a.get('camera')} » n'est plus dans la liste "
                               "(Réglages → Intégrations)")
            return
        zoom = command in MOVES and bool(MOVES[command][2])
        try:
            if phase == "up":
                if command in MOVES:
                    cam.stop(pantilt=not zoom, zoom=zoom)
            elif command in MOVES:
                cam.move(command, float(a.get("speed") or 50) / 100)
            elif command == "stop":
                cam.stop()
            elif command == "home":
                cam.home()
            elif command in ("preset", "save"):
                preset = str(a.get("preset") or "").strip()
                if not preset:
                    self._log("warn", f"ONVIF {cam.name} : aucun preset choisi")
                    return
                (cam.goto if command == "preset" else cam.save)(preset)
                if command == "save":
                    self._log("info", f"ONVIF {cam.name} : position enregistrée dans le preset « {preset} »")
        except (OnvifError, *NET_ERRORS) as e:
            if not isinstance(e, OnvifError):  # injoignable : à redécouvrir, et la touche le montre tout de suite
                cam.forget()
                if cam.reachable is not False:
                    cam.reachable, cam.error = False, explain(cam, e)
                    self._on_change()
            self._log("error", f"ONVIF {cam.name} : {explain(cam, e)}")
            if phase == "up":  # un arrêt perdu laisse la caméra tourner : on le retente une fois
                try:
                    cam.stop(pantilt=not zoom, zoom=zoom)
                except (OnvifError, *NET_ERRORS):
                    self._log("error", f"ONVIF {cam.name} : l'arrêt du mouvement n'a pas pu être envoyé")


def status(svc: Onvif) -> tuple[bool | None, str]:
    """(état, texte) pour Réglages et les voyants."""
    if not svc.cameras:
        return None, "Aucune caméra renseignée."
    if not svc.active:
        return None, "Aucune touche ne les utilise (« Tester » pour vérifier)."
    parts, oks = [], []
    for c in svc.cameras:
        oks.append(c.reachable)
        parts.append(f"{c.name} …" if c.reachable is None else
                     f"{c.name} : {len(c.preset_list)} preset(s)" if c.reachable else f"{c.name} : {c.error}")
    return (False if False in oks else None if None in oks else True), "  ·  ".join(parts)
