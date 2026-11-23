"""Faux serveurs vMix, py-intercom, orchestream-v2, Tally32, caméra ONVIF, py-multiview, MixPanel et DMAIX
pour les tests."""
import json
import socket
import sys
import threading
import time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import fake_vmix  # noqa: E402


def serve_vmix(port: int) -> "fake_vmix.FakeVmix":
    """Démarre un faux vMix (API TCP) sur 127.0.0.1:port."""
    vm = fake_vmix.FakeVmix()
    srv = socket.create_server(("127.0.0.1", port))

    def accept():
        while True:
            c, _ = srv.accept()
            threading.Thread(target=vm.serve, args=(c,), daemon=True).start()

    threading.Thread(target=accept, daemon=True).start()
    return vm


class FakeIntercom:
    """Comme l'API Stream Deck du client py-intercom (client/deck.py + remote.py), seule API servie : /api/deck
    (state, events SSE, commands, POST commands/<id>), jeton facultatif (token=None : accès libre), 403 si
    Origin / Referer, 409 COMMAND_REFUSED hors ligne ou bus Permanent, 422 bus manquant / inconnu, 404
    COMMAND_NOT_FOUND, 503 « interface occupée » à la demande ; toute autre route : 404 ROUTE_NOT_FOUND.
    Seuls les bus de self.buses sont proposés ; « active » est faux pour tous quand le client n'est pas en ligne."""
    KEYS = {"regie": 0, "plateau": 1, "vmix": 2}
    NAMES = {"regie": "Régie", "plateau": "Plateau", "vmix": "VMix"}
    TONES = {"regie": "blue", "plateau": "amber", "vmix": "violet"}

    def __init__(self, port, token=None):
        self.online = True
        self.token = token
        self.buses = {"regie": {"mode": "ptt", "active": False}, "plateau": {"mode": "toggle", "active": False},
                      "vmix": {"mode": "always_on", "active": True}}
        self.log = []
        self.seen_origin = []  # Origin / Referer reçus (LiveDeck n'en envoie jamais)
        self.auth = []  # en-têtes Authorization reçus
        self.requests = []  # (méthode, chemin)
        self.ignore_off = 0  # nombre de « off » acceptés (200) mais sans effet : « off » perdu côté client
        self.busy = 0  # nombre de réponses 503 « interface occupée » à renvoyer
        self.slow: list[float] = []  # délais (s) des prochaines commandes : client lent mais vivant
        self.no_stream = False  # flux SSE indisponible : le client lit l'état en secours
        self._gui = threading.Lock()
        fake = self

        class H(BaseHTTPRequestHandler):
            def _send(self, code, data):
                body = json.dumps(data, ensure_ascii=False).encode()
                try:
                    self.send_response(code)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except OSError:  # LiveDeck a abandonné (délai dépassé) : la commande est appliquée quand même
                    pass

            def _err(self, code, name, message, details=None):
                err = {"code": name, "message": message}
                if details:
                    err["details"] = details
                self._send(code, {"error": err})

            def _stream(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                old = fake.state()
                try:
                    self.wfile.write(f"event: state\ndata: {json.dumps(old, ensure_ascii=False)}\n\n".encode())
                    self.wfile.flush()
                    idle = 0
                    while True:
                        time.sleep(0.05)
                        new = fake.state()
                        a = {k: v for k, v in old.items() if k != "updatedAt"}
                        b = {k: v for k, v in new.items() if k != "updatedAt"}
                        if a != b:
                            keys = {k["id"]: k for k in old["keys"]}
                            patch = {"changed": {k: v for k, v in b.items() if k != "keys" and a.get(k) != v},
                                     "keys": [k for k in new["keys"] if keys.get(k["id"]) != k],
                                     "removedKeys": [], "updatedAt": new["updatedAt"]}
                            self.wfile.write(f"event: patch\ndata: {json.dumps(patch, ensure_ascii=False)}\n\n".encode())
                            self.wfile.flush()
                            old, idle = new, 0
                        else:
                            idle += 1
                            if idle % 100 == 0:
                                self.wfile.write(b": keepalive\n\n")
                                self.wfile.flush()
                except OSError:
                    pass

            def _handle(self, method):
                fake.requests.append((method, self.path))
                if self.headers.get("Origin") or self.headers.get("Referer"):
                    fake.seen_origin.append(self.headers.get("Origin") or self.headers.get("Referer"))
                    return self._err(403, "FORBIDDEN", "requêtes de navigateur refusées")
                fake.auth.append(self.headers.get("Authorization"))
                if fake.token is not None and self.headers.get("Authorization") != f"Bearer {fake.token}":
                    return self._err(401, "INVALID_TOKEN", "jeton API absent ou incorrect")
                parts = [p for p in self.path.split("?", 1)[0].split("/") if p]
                if parts[:2] != ["api", "deck"]:
                    return self._err(404, "ROUTE_NOT_FOUND", "route inconnue")
                rest = parts[2:]
                if rest == ["state"] and method == "GET":
                    return self._send(200, fake.state())
                if rest == ["events"] and method == "GET":
                    if fake.no_stream:
                        return self._err(404, "ROUTE_NOT_FOUND", "route inconnue")
                    return self._stream()
                if rest == ["commands"] and method == "GET":
                    return self._send(200, {"commands": fake.state()["commands"]})
                if len(rest) != 2 or rest[0] != "commands" or method != "POST":
                    if method == "GET":
                        return self._err(404, "ROUTE_NOT_FOUND", "route inconnue")
                    return self._err(405, "METHOD_NOT_ALLOWED", "méthode non permise")
                cid = rest[1]
                if cid not in ("ptt.toggle", "ptt.on", "ptt.off", "ptt.allOff"):
                    return self._err(404, "COMMAND_NOT_FOUND", f"commande « {cid} » inconnue")
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                    body = json.loads(self.rfile.read(n) or b"{}") if n else {}
                except ValueError:
                    return self._err(422, "VALIDATION_FAILED", "JSON illisible")
                args = body.get("args") if isinstance(body, dict) else None
                bus, cmd = None, cid.split(".")[1]
                if cid == "ptt.allOff":
                    cmd = "off"
                else:
                    value = str((args or {}).get("bus") or "").strip().lower()
                    if not value:
                        return self._err(422, "VALIDATION_FAILED", "argument « bus » manquant",
                                         {"args.bus": "argument « bus » manquant"})
                    bus = next((k for k, i in fake.KEYS.items() if value in (k, str(i))), None)
                    if bus is None:
                        return self._err(422, "VALIDATION_FAILED", f"bus « {value} » inconnu",
                                         {"args.bus": f"bus « {value} » inconnu"})
                fake.log.append((bus or "all", cmd))
                with fake._gui:  # comme le QtDispatcher du client : une commande à la fois, dans l'ordre
                    if fake.slow:  # interface occupée : la commande est appliquée (et répondue) en retard
                        time.sleep(fake.slow.pop(0))
                    if fake.busy > 0:
                        fake.busy -= 1
                        return self._err(503, "BUSY", "Interface occupée, réessayez.")
                    if not fake.online and cmd != "off":
                        return self._err(409, "COMMAND_REFUSED", "PTT désarmé : le client n'est pas en ligne")
                    if bus is not None and bus not in fake.buses:
                        return self._err(422, "VALIDATION_FAILED", "bus non proposé par ce serveur",
                                         {"args.bus": "bus non proposé"})
                    for b in ([bus] if bus else list(fake.buses)):
                        st = fake.buses[b]
                        if st["mode"] == "always_on":
                            if bus is not None and cmd != "on":
                                return self._err(409, "COMMAND_REFUSED",
                                                 "Bus en mode Permanent : changez son mode dans le poste.")
                            continue
                        if cmd == "off" and fake.ignore_off > 0:
                            fake.ignore_off -= 1
                            continue
                        st["active"] = (not st["active"]) if cmd == "toggle" else cmd == "on"
                    self._send(200, {"ok": True, "message": "fait", "data": fake.payload()})

            def do_GET(self):
                self._handle("GET")

            def do_POST(self):
                self._handle("POST")

            def log_message(self, *a):
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", port), H)
        self.srv.daemon_threads = True
        self.port = self.srv.server_address[1]  # port 0 : celui que le système a donné
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def buses_list(self) -> list[dict]:
        on = self.online
        return [{"id": k, "name": self.NAMES[k], "mode": v["mode"],
                 "active": bool(on and (v["mode"] == "always_on" or v["active"]))} for k, v in self.buses.items()]

    def payload(self) -> dict:
        """Ce que renvoie une commande : data = état de la fenêtre (buses : dictionnaire)."""
        return {"state": "En ligne" if self.online else "Hors ligne", "online": self.online,
                "operational": "online" if self.online else "disconnected", "detail": None,
                "buses": {b["id"]: {"mode": b["mode"], "active": b["active"]} for b in self.buses_list()}}

    def state(self) -> dict:
        """GET /api/deck/state : token, client, buses, commands, keys, updatedAt (comme client/deck.py)."""
        online = self.online
        buses = self.buses_list()
        keys = [{"id": "client.status", "group": "État", "command": None, "args": {}, "title": "Intercom",
                 "subtitle": "En ligne" if online else "Hors ligne", "active": online,
                 "tone": "success" if online else "neutral", "available": True, "reason": None}]
        for b in buses:
            reason = None
            if not online:
                reason = "Le poste n'est pas en ligne."
            elif b["mode"] == "always_on":
                reason = "Bus en mode Permanent : changez son mode dans le poste."
            keys.append({"id": f"ptt.toggle:{b['id']}", "group": "Parole", "command": "ptt.toggle",
                         "args": {"bus": b["id"]}, "title": b["name"],
                         "subtitle": "Hors ligne" if not online else ("Tu parles" if b["active"] else "Prêt"),
                         "active": b["active"], "tone": "live" if b["active"] else self.TONES[b["id"]],
                         "available": reason is None, "reason": reason})
        keys.append({"id": "ptt.allOff", "group": "Parole", "command": "ptt.allOff", "args": {},
                     "title": "Tout couper", "subtitle": None, "active": False, "tone": "danger",
                     "available": True, "reason": None})
        cmds = [{"id": c, "label": c, "argNames": [] if c == "ptt.allOff" else ["bus"], "available": True,
                 "reason": None} for c in ("ptt.toggle", "ptt.on", "ptt.off", "ptt.allOff")]
        return {"token": {"name": "jeton API" if self.token else "accès libre", "permissions": []},
                "client": {"state": "online" if online else "disconnected",
                           "label": "En ligne" if online else "Hors ligne", "online": online, "detail": None},
                "buses": buses, "commands": cmds, "keys": keys,
                "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())}

    def stop(self):
        self.srv.shutdown()
        self.srv.server_close()


class FakeKeys:
    """Remplace la simulation clavier : aucun test ne doit appuyer sur une vraie touche."""

    def __init__(self):
        self.log = []

    def install(self) -> "FakeKeys":
        from livedeck import hotkeys
        hotkeys.press = lambda combo: self.log.append(("press", combo))
        hotkeys.release = lambda combo: self.log.append(("release", combo))
        hotkeys.tap = lambda combo: self.log.append(("tap", combo))
        return self


class FakeAudio:
    """Remplace Core Audio : aucun test ne doit toucher au vrai son de la machine (et la CI n'a pas de
    carte son). Même interface que winsys.WinAudio."""

    def __init__(self):
        self.levels = {"output": 50.0, "mic": 100.0}
        self.muted = {"output": False, "mic": False}
        self.app_levels = {"spotify.exe": 60.0, "chrome.exe": 100.0}
        self.app_muted = {"spotify.exe": False, "chrome.exe": False}
        self.calls = []

    def available(self) -> bool:
        return True

    def _app(self, app):
        from livedeck.winsys import AudioError, WinAudio
        for name in self.app_levels:
            if WinAudio._match(name, app):
                return name
        raise AudioError(f"« {app} » ne produit pas de son en ce moment (aucune session audio)")

    def get(self, target, app=""):
        if target == "app":
            n = self._app(app)
            return self.app_levels[n], self.app_muted[n]
        return self.levels[target], self.muted[target]

    def set_level(self, target, value, app=""):
        value = max(0.0, min(100.0, float(value)))
        self.calls.append(("level", target, app, value))
        if target == "app":
            self.app_levels[self._app(app)] = value
        else:
            self.levels[target] = value
        return value

    def set_mute(self, target, muted, app=""):
        self.calls.append(("mute", target, app, muted))
        if target == "app":
            self.app_muted[self._app(app)] = bool(muted)
        else:
            self.muted[target] = bool(muted)

    def apps(self):
        return sorted((n, v, self.app_muted[n]) for n, v in self.app_levels.items())


class FakeJson:
    """API JSON quelconque (retour d'état « Valeur d'une API JSON »)."""

    def __init__(self, port, data=None):
        self.data, self.status, self.hits = data, 200, 0
        fake = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                fake.hits += 1
                body = json.dumps(fake.data).encode() if fake.status == 200 else b"erreur"
                self.send_response(fake.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", port), H)
        self.srv.daemon_threads = True
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()


class FakeTally:
    def __init__(self, port, connected):
        self.connected = connected
        self.reconnects = 0
        self.old_firmware = False  # sans route /vmix/reconnect (404)
        fake = self

        class H(BaseHTTPRequestHandler):
            def _send(self, code, data):
                body = json.dumps(data).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/status":
                    return self._send(200, {"connected": fake.connected, "live": False, "preview": True,
                                            "vmix_host": "10.0.0.10", "vmix_input": 3, "wifi_ssid": "regie",
                                            "wifi_ip": "127.0.0.1"})
                self._send(404, {})

            def do_POST(self):
                if self.path == "/vmix/reconnect" and not fake.old_firmware:
                    fake.reconnects += 1
                    fake.connected = True
                    return self._send(200, {"status": "ok", "message": "Reconnexion VMix demandee"})
                self._send(404, {})

            def log_message(self, *a):
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", port), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()


class FakeOnvif:
    """Caméra ONVIF PTZ : device_service, media et ptz en SOAP 1.2. Le jeton WS-Security est vérifié (mot de
    passe haché, daté à l'heure de la caméra, qui avance de `skew` secondes sur ce PC). La caméra annonce ses
    services sur une IP qu'on ne peut pas joindre, comme une caméra restée sur une ancienne adresse."""
    USER, PASSWORD = "admin", "secret"

    def __init__(self, port=0, skew=3600, ptz=True):
        self.skew, self.has_ptz = skew, ptz
        self.calls = []                      # (opération, {paramètres})
        self.presets = {"1": "Plateau", "2": "Large"}
        self.numbered = False                # caméra à emplacements numérotés : refuse SetPreset par nom
        fake = self

        class H(BaseHTTPRequestHandler):
            def _send(self, code, body):
                raw = ('<?xml version="1.0" encoding="UTF-8"?><s:Envelope '
                       'xmlns:s="http://www.w3.org/2003/05/soap-envelope" '
                       'xmlns:tt="http://www.onvif.org/ver10/schema" xmlns:tds="http://www.onvif.org/ver10/device/wsdl" '
                       'xmlns:trt="http://www.onvif.org/ver10/media/wsdl" xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl" '
                       'xmlns:ter="http://www.onvif.org/ver10/error">'
                       f"<s:Body>{body}</s:Body></s:Envelope>").encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/soap+xml; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _fault(self, sub, text):
                self._send(400, "<s:Fault><s:Code><s:Value>s:Sender</s:Value><s:Subcode>"
                                f"<s:Value>{sub}</s:Value></s:Subcode></s:Code><s:Reason>"
                                f'<s:Text xml:lang="en">{text}</s:Text></s:Reason></s:Fault>')

            def do_POST(self):
                import base64
                import hashlib
                import re
                xml = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode("utf-8", "replace")
                body = xml.split("Body>", 1)[1]
                op = re.search(r"<\w+:(\w+)", body).group(1)
                if op == "GetSystemDateAndTime":
                    t = time.gmtime(time.time() + fake.skew)
                    return self._send(200, "<tds:GetSystemDateAndTimeResponse><tds:SystemDateAndTime><tt:UTCDateTime>"
                                      f"<tt:Time><tt:Hour>{t.tm_hour}</tt:Hour><tt:Minute>{t.tm_min}</tt:Minute>"
                                      f"<tt:Second>{t.tm_sec}</tt:Second></tt:Time><tt:Date><tt:Year>{t.tm_year}</tt:Year>"
                                      f"<tt:Month>{t.tm_mon}</tt:Month><tt:Day>{t.tm_mday}</tt:Day></tt:Date>"
                                      "</tt:UTCDateTime></tds:SystemDateAndTime></tds:GetSystemDateAndTimeResponse>")
                m = re.search(r"Username>([^<]*)<.*?Password[^>]*>([^<]*)<.*?Nonce[^>]*>([^<]*)<.*?Created>([^<]*)<",
                              xml, re.S)
                ok = False
                if m:
                    user, digest, nonce, created = m.groups()
                    want = base64.b64encode(hashlib.sha1(base64.b64decode(nonce) + created.encode()
                                                         + fake.PASSWORD.encode()).digest()).decode()
                    import calendar
                    age = abs(calendar.timegm(time.strptime(created[:19], "%Y-%m-%dT%H:%M:%S"))
                              - (time.time() + fake.skew))
                    ok = user == fake.USER and digest == want and age < 30
                if not ok:
                    return self._fault("ter:NotAuthorized", "Sender not Authorized")
                args = dict(re.findall(r"<\w+:(ProfileToken|PresetToken|PresetName|PanTilt|Zoom)>([^<]*)<", body))
                for tag, attrs in re.findall(r"<tt:(PanTilt|Zoom) ([^/]*)/>", body):
                    args[tag] = tuple(float(v) for v in re.findall(r'="([-\d.]+)"', attrs))
                fake.calls.append((op, args))
                port = self.server.server_address[1]
                if op == "GetCapabilities":
                    ptz = (f"<tt:PTZ><tt:XAddr>http://10.9.9.9:{port}/onvif/ptz</tt:XAddr></tt:PTZ>"
                           if fake.has_ptz else "")
                    return self._send(200, "<tds:GetCapabilitiesResponse><tds:Capabilities><tt:Media><tt:XAddr>"
                                      f"http://10.9.9.9:{port}/onvif/media</tt:XAddr></tt:Media>{ptz}"
                                      "</tds:Capabilities></tds:GetCapabilitiesResponse>")
                if op == "GetProfiles" and self.path == "/onvif/media":
                    return self._send(200, '<trt:GetProfilesResponse><trt:Profiles token="sub"><tt:Name>sub</tt:Name>'
                                      '</trt:Profiles><trt:Profiles token="main"><tt:Name>main</tt:Name>'
                                      '<tt:PTZConfiguration token="ptz0"/></trt:Profiles></trt:GetProfilesResponse>')
                if self.path != "/onvif/ptz" or args.get("ProfileToken") != "main":
                    return self._fault("ter:InvalidArgVal", "No such profile")
                if op == "GetPresets":
                    return self._send(200, "<tptz:GetPresetsResponse>" + "".join(
                        f'<tptz:Preset token="{t}"><tt:Name>{n}</tt:Name></tptz:Preset>'
                        for t, n in fake.presets.items()) + "</tptz:GetPresetsResponse>")
                if op == "GotoPreset" and args.get("PresetToken") not in fake.presets:
                    return self._fault("ter:NoToken", "The requested preset token does not exist")
                if op == "SetPreset":
                    tok = args.get("PresetToken")
                    if not tok:
                        if fake.numbered:
                            return self._fault("ter:InvalidArgVal", "Preset name not supported")
                        tok = str(len(fake.presets) + 1)
                    fake.presets[tok] = args.get("PresetName") or fake.presets.get(tok, f"Preset {tok}")
                    return self._send(200, f"<tptz:SetPresetResponse><tptz:PresetToken>{tok}</tptz:PresetToken>"
                                           "</tptz:SetPresetResponse>")
                self._send(200, f"<tptz:{op}Response/>")

            def log_message(self, *a):
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", port), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def ops(self, since=0):
        return [op for op, _ in self.calls[since:] if op not in ("GetCapabilities", "GetProfiles", "GetPresets")]


class FakeMultiview:
    """API Stream Deck commune de py-multiview (/api/deck), d'après py_multiview/deck.py et api.py : touches décrites, flux « state » puis « patch »,
    jeton facultatif (vide : accès libre), cible inconnue -> 422 VALIDATION_FAILED, jamais 404 NOT_FOUND.
    deck=False : version d'avant /api/deck."""

    def __init__(self, port, token="", deck=True):
        self.streams = [{"index": 1, "id": "s-1", "name": "Caméra 1", "source": "srt", "running": False,
                         "status": "stopped", "detail": "", "muted": False, "display": "Écran 2"},
                        {"index": 2, "id": "s-2", "name": "Plateau", "source": "omt", "running": True,
                         "status": "active", "detail": "", "muted": False, "display": "Écran 3"}]
        self.sender = False
        self.monitor = False
        self.floating = False
        self.volume = 100
        self.refuse = ""  # message d'un refus métier (409)
        self.token, self.deck = token, deck
        self.deck_calls = []    # (commande, args, en-tête Authorization)
        self.deck_headers = []  # en-têtes reçus sur /api/deck
        self.deck_streams = 0
        self.deck_reads = 0
        fake = self

        def payload():
            return {"app": "py-multiview", "version": "0.3.7", "connected": True,
                    "reception": {"running": any(s["running"] for s in fake.streams),
                                  "active": sum(s["running"] for s in fake.streams), "total": len(fake.streams)},
                    "streams": fake.streams,
                    "sender": {"running": fake.sender, "status": "active" if fake.sender else "stopped",
                               "detail": "", "name": "Régie", "audio": "loopback", "fps": 25},
                    "monitor": {"running": fake.monitor, "status": "active" if fake.monitor else "stopped",
                                "detail": "", "source": "RÉGIE (Master)", "volume": fake.volume},
                    "floating": {"running": fake.floating, "status": "active" if fake.floating else "stopped",
                                 "detail": "", "source": "RÉGIE (Master)"},
                    "routes": [{"index": 1, "id": "r-1", "name": "Multicast", "running": False,
                                "status": "stopped"}]}

        self.payload = payload

        tones = {"active": "success", "waiting": "warning", "retrying": "warning", "error": "danger"}

        def dkey(kid, group, command, args, title, subtitle, active, tone, reason=None):
            return {"id": kid, "group": group, "command": command, "args": args, "title": title,
                    "subtitle": subtitle or None, "active": bool(active), "tone": tone,
                    "available": reason is None, "reason": reason}

        def keys():
            st = payload()
            rec, out = st["reception"], []
            out.append(dkey("reception.toggle", "Réception", "reception.toggle", {}, "Réception",
                            f"{rec['active']}/{rec['total']} actifs" if rec["running"] else "Arrêtée",
                            rec["running"], "success" if rec["running"] else "neutral"))
            for x in st["streams"]:
                out.append(dkey(f"stream.toggle:{x['id']}", "Flux", "stream.toggle", {"stream": x["id"]}, x["name"],
                                "Actif" if x["running"] else "Arrêté", x["running"],
                                tones.get(x["status"], "neutral") if x["running"] else "neutral",
                                None if x["running"] or x.get("display") else "Aucun écran assigné."))
            for x in st["streams"]:
                out.append(dkey(f"stream.muteToggle:{x['id']}", "Son des flux", "stream.muteToggle",
                                {"stream": x["id"]}, x["name"], "Son coupé" if x["muted"] else "Son actif",
                                x["muted"], "danger" if x["muted"] else "neutral"))
            out.append(dkey("sender.toggle", "Émission", "sender.toggle", {}, "Émission OMT",
                            "Actif" if fake.sender else "Régie", fake.sender, "live" if fake.sender else "neutral"))
            out.append(dkey("monitor.toggle", "Retour audio", "monitor.toggle", {}, "Retour audio",
                            "RÉGIE (Master)", fake.monitor, "success" if fake.monitor else "neutral"))
            out.append(dkey("monitor.volumeUp", "Retour audio", "monitor.volumeUp", {}, "Volume +",
                            f"{fake.volume} %", False, "neutral",
                            "Volume déjà au maximum." if fake.volume >= 100 else None))
            for r in st["routes"]:
                out.append(dkey(f"route.toggle:{r['id']}", "Routes", "route.toggle", {"route": r["id"]}, r["name"],
                                "Arrêtée", r["running"], "neutral"))
            return out

        def deck_state():
            st = payload()
            st.pop("app"), st.pop("version"), st.pop("connected")
            return {"token": {"name": "jeton API" if fake.token else "accès libre", "permissions": []}, **st,
                    "commands": [{"id": c, "label": c, "argNames": []} for c in
                                 ("reception.toggle", "stream.toggle", "sender.toggle", "monitor.toggle")],
                    "keys": keys(), "updatedAt": "2026-11-23T12:00:00Z"}

        def deck_patch(old, new):
            changed = {k: v for k, v in new.items() if k not in ("keys", "updatedAt") and old.get(k) != v}
            before, now = {k["id"]: k for k in old["keys"]}, {k["id"]: k for k in new["keys"]}
            ks = [k for kid, k in now.items() if before.get(kid) != k]
            removed = [kid for kid in before if kid not in now]
            if not (changed or ks or removed):
                return None
            return {"changed": changed, "keys": ks, "removedKeys": removed, "updatedAt": new["updatedAt"]}

        def deck_run(cid, args):
            """(statut, réponse) d'une commande, comme deck.run_command."""
            obj, _, verb = cid.partition(".")

            def find(name, items):
                ref = args.get(name)
                if not ref:
                    raise ValueError(name, f"argument « {name} » manquant")
                item = next((i for i in items if i["id"] == ref), None)
                if item is None:
                    raise ValueError(name, f"{name} « {ref} » inconnu")
                return item
            try:
                if obj in ("reception", "sender", "monitor", "floating") and verb in ("start", "stop", "toggle"):
                    cur = any(x["running"] for x in fake.streams) if obj == "reception" else getattr(fake, obj)
                    want = {"start": True, "stop": False}.get(verb, not cur)
                    if obj == "reception":
                        for x in fake.streams:
                            x["running"], x["status"] = want, "active" if want else "stopped"
                    else:
                        setattr(fake, obj, want)
                elif cid in ("stream.start", "stream.stop", "stream.toggle"):
                    x = find("stream", fake.streams)
                    x["running"] = {"start": True, "stop": False}.get(verb, not x["running"])
                    x["status"] = "active" if x["running"] else "stopped"
                elif cid == "stream.muteToggle":
                    x = find("stream", fake.streams)
                    x["muted"] = not x["muted"]
                elif cid == "monitor.volumeUp":
                    fake.volume = min(100, fake.volume + 5)
                else:
                    return 404, {"error": {"code": "COMMAND_NOT_FOUND", "message": f"commande « {cid} » inconnue"}}
            except ValueError as e:
                return 422, {"error": {"code": "VALIDATION_FAILED", "message": e.args[1],
                                       "details": {f"args.{e.args[0]}": e.args[1]}}}
            if fake.refuse:
                return 409, {"error": {"code": "REFUSED", "message": fake.refuse}}
            return 200, {"ok": True, "message": f"{cid} : fait", "data": None}

        self.deck_state = deck_state

        class MV(BaseHTTPRequestHandler):
            def _send(self, code, data):
                body = json.dumps(data, ensure_ascii=False).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _deck_auth(self):
                fake.deck_headers.append(dict(self.headers))
                if not fake.token:
                    return True
                if (self.headers.get("Authorization") or "") != f"Bearer {fake.token}":
                    self._send(401, {"error": {"code": "INVALID_TOKEN", "message": "jeton API absent ou invalide"}})
                    return False
                return True

            def _events(self):
                fake.deck_streams += 1
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                last = deck_state()
                self.wfile.write(f"event: state\ndata: {json.dumps(last, ensure_ascii=False)}\n\n".encode())
                self.wfile.flush()
                beat = time.monotonic()
                while True:
                    time.sleep(0.05)
                    new = deck_state()
                    pt = deck_patch(last, new)
                    if pt:
                        last = new
                        self.wfile.write(f"event: patch\ndata: {json.dumps(pt, ensure_ascii=False)}\n\n".encode())
                        self.wfile.flush()
                    elif time.monotonic() - beat > 1:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                        beat = time.monotonic()

            def do_GET(self):
                if fake.deck and self.path.startswith("/api/deck"):
                    if not self._deck_auth():
                        return
                    if self.path == "/api/deck/events":
                        try:
                            return self._events()
                        except OSError:
                            return
                    if self.path == "/api/deck/state":
                        fake.deck_reads += 1
                        return self._send(200, deck_state())
                    return self._send(404, {"error": {"code": "ROUTE_NOT_FOUND", "message": "route inconnue"}})
                self._send(404, {"error": {"code": "NOT_FOUND", "message": "routes : GET /status"}})

            def do_POST(self):
                if fake.deck and self.path.startswith("/api/deck/commands/"):
                    if not self._deck_auth():
                        return
                    n = int(self.headers.get("Content-Length") or 0)
                    body = json.loads(self.rfile.read(n) or b"{}") if n else {}
                    cid = unquote(self.path.split("/api/deck/commands/", 1)[1])
                    args = (body or {}).get("args") or {}
                    fake.deck_calls.append((cid, args, self.headers.get("Authorization")))
                    return self._send(*deck_run(cid, args))
                self._send(404, {"error": {"code": "NOT_FOUND", "message": "route inconnue"}})

            def log_message(self, *a):
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", port), MV)
        self.srv.daemon_threads = True
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()


class FakeOrchDeck:
    """API Stream Deck d'orchestream-v2, d'après packages/back (deck_controller, deck_keys, deck_stream_service,
    api_token_middleware) : jeton dans « Authorization: Bearer » ou « X-Orchestream-Token » (jamais dans
    l'URL), 401 INVALID_TOKEN, 403 FORBIDDEN_ROLE selon les permissions du jeton, erreurs métier 409 avec
    message en français, 429 TOO_MANY_REQUESTS + Retry-After, réponse { ok, message } des commandes.
    Touches décrites par le serveur (« keys ») et flux /api/deck/events : « state », puis « patch »."""
    TOKEN = "osk_test_123"
    PERMS = {"game.advance": "games.run", "game.next": "games.run", "draw.run": "games.run",
             "twizz.start": "games.run", "activity.ackAll": "thanks", "overlay.show": "overlay.control",
             "overlay.hide": "overlay.control", "overlay.toggle": "overlay.control",
             "studio.shortcut": "studio.control", "live.end": "live.control"}
    WIDGETS = {"twizz": "Twizz", "poll": "Sondage", "speedizz": "Speedizz"}

    def __init__(self, port, stream=True):
        self.permissions = {"read", "games.run", "thanks", "overlay.control", "studio.control"}  # pas live.control
        self.stream = stream  # False : orchestream sans /api/deck/events (le client lit l'état en secours)
        self.live = True
        self.game = {"kind": "twizz", "runName": "Culture G", "phase": "open", "questionIndex": 2,
                     "questionNumber": 3, "totalQuestions": 10, "nextAction": "close"}
        self.unthanked = 5
        self.overlays = {"twizz": True, "poll": False, "speedizz": False, "hypeTrain": False, "prediction": False}
        self.shortcuts = {"s1": {"name": "Lumière plateau", "on": False, "tone": "amber"}}
        self.calls = []       # (commande, args, en-tête utilisé)
        self.reads = 0
        self.streams = 0      # flux ouverts depuis le début
        self.stream_headers = []  # en-têtes reçus à l'ouverture du flux
        self.throttle = 0     # nombre de 429 à renvoyer
        self.revoked = False  # jeton révoqué : flux fermés, 401 ensuite
        self.quizzes = [{"value": "q-1", "label": "Culture G"}, {"value": "q-2", "label": "Cinéma"}]
        fake = self

        def allowed(cid):
            return self.PERMS[cid] in fake.permissions

        def info(cid):
            return {"id": cid, "label": cid, "permission": self.PERMS[cid], "description": "",
                    "available": not (cid == "draw.run" and not fake.live),
                    "reason": None if fake.live or cid != "draw.run" else "Aucune session live en cours.",
                    "argNames": {"twizz.start": ["quizId"], "studio.shortcut": ["shortcut"]}.get(
                        cid, ["widget"] if cid.startswith("overlay.") else [])}

        def key(kid, group, command, title, args=None, subtitle=None, active=False, tone="neutral",
                available=True, reason=None):
            return {"id": kid, "group": group, "command": command, "args": args or {}, "title": title,
                    "subtitle": subtitle, "active": active, "tone": tone, "available": available,
                    "reason": None if available else reason}

        def keys():
            g, out = fake.game, []
            if allowed("game.advance"):
                nxt = g["nextAction"]
                title = {"close": "Fermer Q{n}", "open": "Ouvrir Q{n}", "finish": "Terminer"}.get(nxt, "Suivant")
                out.append(key("game.advance", "Jeux", "game.advance", title.format(n=g["questionNumber"]),
                               subtitle=g["runName"], tone="accent", available=g["phase"] != "idle",
                               reason="Aucune partie en cours."))
            if allowed("twizz.start"):
                out += [key(f"twizz.start:{q['value']}", "Jeux", "twizz.start", f"Twizz {q['label']}",
                            {"quizId": q["value"]}, tone="accent") for q in fake.quizzes]
            if allowed("draw.run"):
                out.append(key("draw.run", "Tirages", "draw.run", "Tirer au sort", available=fake.live,
                               reason="Aucune session live en cours."))
            if allowed("activity.ackAll"):
                n = fake.unthanked
                out.append(key("activity.ackAll", "Chat et remerciements", "activity.ackAll", "Tout remercier",
                               subtitle=f"{n} à remercier" if n else None, active=n > 0,
                               tone="warning" if n else "neutral"))
            for verb in ("toggle", "show", "hide"):
                if allowed(f"overlay.{verb}"):
                    out += [key(f"overlay.{verb}:{w}", "Overlay", f"overlay.{verb}", label, {"widget": w},
                                subtitle="Affiché" if fake.overlays.get(w) else None,
                                active=bool(fake.overlays.get(w)), tone="violet")
                            for w, label in fake.WIDGETS.items()]
            if allowed("studio.shortcut"):
                out += [key(f"studio.shortcut:{sid}", "Studio", "studio.shortcut", s["name"], {"shortcut": sid},
                            subtitle="Allumé" if s["on"] else "Éteint", active=s["on"], tone=s["tone"])
                        for sid, s in fake.shortcuts.items()]
            if allowed("live.end"):
                out.append(key("live.end", "Live", "live.end", "Fin du live", tone="danger", available=fake.live,
                               reason="Aucun live en cours."))
            out.append(key("status:live", "État", None, "Live", subtitle="42 viewers" if fake.live else None,
                           active=fake.live, tone="live"))
            return out

        def state():
            return {"token": {"name": "Stream Deck plateau", "permissions": sorted(fake.permissions)},
                    "live": {"active": fake.live, "simulated": False, "startedAt": None, "viewers": 42,
                             "twitchOnline": True},
                    "game": dict(fake.game), "draw": {"pendingId": None, "pendingName": None, "lastWinner": None,
                                                     "defaultSource": "viewers"},
                    "poll": {"active": False, "title": None, "endsAt": None},
                    "prediction": {"active": False, "status": None, "title": None, "locksAt": None},
                    "unthanked": fake.unthanked, "overlays": dict(fake.overlays),
                    "lanes": {"pending": 0, "running": 0},
                    "commands": [info(c) for c in fake.PERMS if allowed(c)], "keys": keys(),
                    "updatedAt": "2026-11-06T12:00:00Z"}

        def patch(old, new):
            """Comme deckStatePatch : sections changées (entières), touches changées ou nouvelles, disparues."""
            changed = {k: v for k, v in new.items() if k not in ("keys", "updatedAt") and old.get(k) != v}
            before = {k["id"]: k for k in old["keys"]}
            now = {k["id"]: k for k in new["keys"]}
            keys_ = [k for kid, k in now.items() if before.get(kid) != k]
            removed = [kid for kid in before if kid not in now]
            if not (changed or keys_ or removed):
                return None
            return {"changed": changed, "keys": keys_, "removedKeys": removed, "updatedAt": new["updatedAt"]}

        self.state, self.patch = state, patch

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def _send(self, code, data, headers=None):
                body = json.dumps(data, ensure_ascii=False).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _err(self, code, ecode, msg, headers=None):
                self._send(code, {"error": {"code": ecode, "message": msg}}, headers)

            def _auth(self):
                """Comme readApiToken : Bearer, sinon X-Orchestream-Token. Le jeton dans l'URL n'est jamais lu."""
                auth = self.headers.get("Authorization") or ""
                raw = auth[7:].strip() if auth.lower().startswith("bearer ") else (
                    self.headers.get("X-Orchestream-Token") or "").strip()
                if raw != fake.TOKEN or fake.revoked:
                    self._err(401, "INVALID_TOKEN", "Jeton API manquant, invalide ou révoqué.")
                    return None
                if fake.throttle:
                    fake.throttle -= 1
                    self._err(429, "TOO_MANY_REQUESTS", "Trop de requêtes. Réessayez dans 1 seconde.",
                              {"Retry-After": "1"})
                    return None
                return "bearer" if auth else "custom"

            def _events(self):
                fake.streams += 1
                fake.stream_headers.append(dict(self.headers))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                last = state()
                self.wfile.write(f"event: state\ndata: {json.dumps(last, ensure_ascii=False)}\n\n".encode())
                self.wfile.flush()
                beat = time.monotonic()
                while not fake.revoked:  # le serveur regarde régulièrement (chaque seconde ; ici plus vite)
                    time.sleep(0.05)
                    new = state()
                    p = patch(last, new)
                    if p:
                        last = new
                        self.wfile.write(f"event: patch\ndata: {json.dumps(p, ensure_ascii=False)}\n\n".encode())
                        self.wfile.flush()
                    elif time.monotonic() - beat > 1:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                        beat = time.monotonic()

            def do_GET(self):
                if self._auth() is None:
                    return
                fake.reads += 1
                path = self.path.split("?")[0]
                if path == "/api/deck/events":
                    if not fake.stream:  # orchestream sans le flux : route inconnue d'AdonisJS
                        return self._err(404, "E_ROUTE_NOT_FOUND", "Cannot GET:/api/deck/events")
                    try:
                        return self._events()
                    except OSError:
                        return
                if path == "/api/deck/state":
                    return self._send(200, state())
                if path == "/api/deck/commands":
                    out = []
                    for c in fake.PERMS:
                        if allowed(c):
                            i = info(c)
                            i.pop("argNames")
                            if c == "twizz.start":
                                i["args"] = [{"name": "quizId", "label": "Quiz", "values": fake.quizzes}]
                            out.append(i)
                    return self._send(200, out)
                self._err(404, "NOT_FOUND", "Ressource introuvable.")

            def do_POST(self):
                how = self._auth()
                if how is None:
                    return
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}") if n else {}
                cid = unquote(self.path.split("/api/deck/commands/", 1)[-1].split("?")[0])
                args = (body or {}).get("args") or {}
                fake.calls.append((cid, args, how))
                if cid not in fake.PERMS:
                    return self._err(404, "COMMAND_NOT_FOUND", f"Commande inconnue : {cid}")
                if not allowed(cid):
                    return self._err(403, "FORBIDDEN_ROLE",
                                     "Action non autorisée pour le jeton « Stream Deck plateau ».")
                if cid == "twizz.start":
                    if args.get("quizId") not in {q["value"] for q in fake.quizzes}:
                        return self._err(422, "VALIDATION_FAILED", "Quiz inconnu.")
                    return self._send(200, {"ok": True, "message": "Twizz lancé"})
                if cid.startswith("overlay."):
                    w = args.get("widget")
                    if w not in fake.overlays:
                        return self._err(422, "VALIDATION_FAILED", "Widget inconnu.")
                    if w == "speedizz" and cid != "overlay.hide":
                        return self._err(409, "OVERLAY_EMPTY", "Aucun Speedizz à afficher.")
                    fake.overlays[w] = {"overlay.show": True, "overlay.hide": False}.get(cid, not fake.overlays[w])
                    return self._send(200, {"ok": True, "message": f"{fake.WIDGETS.get(w, w)} "
                                                                   f"{'affiché' if fake.overlays[w] else 'masqué'}"})
                if cid == "studio.shortcut":
                    s = fake.shortcuts.get(args.get("shortcut"))
                    if not s:
                        return self._err(422, "VALIDATION_FAILED", "Raccourci inconnu.")
                    s["on"] = not s["on"]
                    return self._send(200, {"ok": True, "message": f"{s['name']} {'allumé' if s['on'] else 'éteint'}"})
                if cid == "game.advance":
                    if fake.game["phase"] == "idle":
                        return self._err(409, "NO_ACTIVE_GAME", "Aucune partie en cours.")
                    # Comme deck_state.ts : questionNumber désigne la question ouverte, sinon celle que
                    # la prochaine pression ouvrira — après fermeture de Q3, c'est donc Q4.
                    if fake.game["nextAction"] == "close":
                        n = fake.game["questionNumber"]
                        fake.game.update(phase="closed", nextAction="open", questionIndex=n, questionNumber=n + 1)
                        return self._send(200, {"ok": True, "message": f"Question {n} fermée"})
                    fake.game.update(phase="open", nextAction="close")
                    return self._send(200, {"ok": True, "message": f"Question {fake.game['questionNumber']} ouverte"})
                if cid == "activity.ackAll":
                    fake.unthanked = 0
                    return self._send(200, {"ok": True, "message": "Tout est remercié"})
                if cid == "draw.run" and not fake.live:
                    return self._err(409, "LIVE_NOT_ACTIVE", "Aucune session live en cours.")
                self._send(200, {"ok": True, "message": f"{cid} : fait"})

            def log_message(self, *a):
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", port), H)
        self.srv.daemon_threads = True
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()


class FakeMixPanel:
    """API Stream Deck de MixPanel (console X-Touch pour vMix), d'après pymixpanel/deck.py et api.py : état
    avec « app » {name, version}, touches décrites (état, scènes, macros, ducking, page, verrouillage, mode
    de la X-Touch), flux « state » puis « patch », jeton facultatif (vide : accès libre), 401 INVALID_TOKEN,
    cible inconnue -> 422 VALIDATION_FAILED, refus 409 REFUSED. name : ce que l'état dit être l'app."""

    def __init__(self, port, token="", name="MixPanel"):
        self.token, self.name = token, name
        self.vmix = True
        self.xtouch, self.mode = True, "standard"
        self.page, self.locked = "A", False
        self.ducking = {"enabled": False, "active": False, "configured": True}
        self.scenes = [{"id": "s1", "name": "Jeu", "active": True}, {"id": "s2", "name": "Plateau", "active": False}]
        self.macros = [{"id": "m1", "name": "Fin", "steps": 2, "running": False},
                       {"id": "m2", "name": "Vide", "steps": 0, "running": False}]
        self.deck_calls = []    # (commande, args, en-tête Authorization)
        self.deck_headers = []
        self.deck_streams = 0
        self.deck_reads = 0
        fake = self

        def key(kid, group, command, args, title, subtitle, active, tone, reason=None):
            return {"id": kid, "group": group, "command": command, "args": args, "title": title,
                    "subtitle": subtitle or None, "active": bool(active), "tone": tone,
                    "available": reason is None, "reason": reason}

        def keys():
            mc, no_vmix = fake.mode == "mc", None if fake.vmix else "vMix n'est pas connecté."
            out = [key("status.vmix", "État", None, {}, "vMix", "v27" if fake.vmix else "Déconnecté", fake.vmix,
                       "success" if fake.vmix else "danger"),
                   key("status.xtouch", "État", None, {}, "X-Touch",
                       ("Mode Mackie (en pause)" if mc else "Connectée") if fake.xtouch else "Déconnectée",
                       fake.xtouch, ("warning" if mc else "success") if fake.xtouch else "danger")]
            for s in fake.scenes:
                out.append(key(f"scene.recall:{s['id']}", "Scènes", "scene.recall", {"scene": s["id"]}, s["name"],
                               "En place" if s["active"] else "Rappeler", s["active"],
                               "success" if s["active"] else "neutral", no_vmix))
            for m in fake.macros:
                steps = f"{m['steps']} étape{'s' if m['steps'] > 1 else ''}"
                out.append(key(f"macro.run:{m['id']}", "Macros", "macro.run", {"macro": m["id"]}, m["name"],
                               "En cours" if m["running"] else steps, m["running"],
                               "info" if m["running"] else "neutral", None if m["steps"] else "Macro vide."))
            d = fake.ducking
            sub, tone = (("Musique baissée", "warning") if d["active"] else ("Activé", "success") if d["enabled"]
                         else ("Désactivé", "neutral"))
            out.append(key("ducking.toggle", "Automatisation", "ducking.toggle", {}, "Ducking", sub, d["enabled"],
                           tone, None if d["configured"] or d["enabled"] else "Ducking non configuré."))
            out += [key("page.toggle", "Console", "page.toggle", {}, "Page", f"Page {fake.page}", fake.page == "B",
                        "accent" if fake.page == "B" else "neutral"),
                    key("lock.toggle", "Console", "lock.toggle", {}, "Verrouillage",
                        "Verrouillé" if fake.locked else "Déverrouillé", fake.locked,
                        "danger" if fake.locked else "neutral"),
                    key("console.modeToggle", "Console", "console.modeToggle", {}, "Mode X-Touch",
                        "Mackie (MC)" if mc else "Standard", mc, "warning" if mc else "neutral",
                        None if fake.xtouch else "X-Touch non connectée.")]
            return out

        def deck_state():
            return {"token": {"name": "jeton API" if fake.token else "accès libre", "permissions": []},
                    "app": {"name": fake.name, "version": "1.2.1"}, "profile": {"name": "Show"},
                    "vmix": {"connected": fake.vmix, "version": "27"},
                    "xtouch": {"connected": fake.xtouch, "port": "X-TOUCH", "mode": fake.mode},
                    "page": fake.page, "locked": fake.locked, "ducking": dict(fake.ducking),
                    "scenes": [dict(s) for s in fake.scenes], "macros": [dict(m) for m in fake.macros],
                    "commands": [{"id": c, "label": c, "argNames": []} for c in
                                 ("scene.recall", "macro.run", "ducking.toggle", "page.toggle", "lock.toggle",
                                  "console.modeToggle")],
                    "keys": keys(), "updatedAt": "2026-11-23T12:00:00Z"}

        def deck_patch(old, new):
            changed = {k: v for k, v in new.items() if k not in ("keys", "updatedAt") and old.get(k) != v}
            before, now = {k["id"]: k for k in old["keys"]}, {k["id"]: k for k in new["keys"]}
            ks = [k for kid, k in now.items() if before.get(kid) != k]
            removed = [kid for kid in before if kid not in now]
            if not (changed or ks or removed):
                return None
            return {"changed": changed, "keys": ks, "removedKeys": removed, "updatedAt": new["updatedAt"]}

        def invalid(name, msg):
            return 422, {"error": {"code": "VALIDATION_FAILED", "message": msg, "details": {f"args.{name}": msg}}}

        def refused(msg):
            return 409, {"error": {"code": "REFUSED", "message": msg}}

        def deck_run(cid, args):
            if cid in ("scene.recall", "macro.run"):
                name = "scene" if cid == "scene.recall" else "macro"
                items = fake.scenes if name == "scene" else fake.macros
                ref = args.get(name)
                if not ref:
                    return invalid(name, f"argument « {name} » manquant")
                item = next((i for i in items if i["id"] == ref), None)
                if item is None:
                    return invalid(name, f"{'scène' if name == 'scene' else 'macro'} « {ref} » introuvable")
                if name == "scene":
                    if not fake.vmix:
                        return refused("vMix n'est pas connecté.")
                    for x in fake.scenes:
                        x["active"] = x is item
                    done = f"Scène « {item['name']} » rappelée"
                else:
                    if not item["steps"]:
                        return refused(f"La macro « {item['name']} » est vide.")
                    item["running"] = True
                    done = f"Macro « {item['name']} » lancée"
            elif cid == "ducking.toggle":
                fake.ducking["enabled"] = not fake.ducking["enabled"]
                done = "Ducking " + ("activé" if fake.ducking["enabled"] else "désactivé")
            elif cid == "page.toggle":
                fake.page = "B" if fake.page == "A" else "A"
                done = f"Page {fake.page}"
            elif cid == "lock.toggle":
                fake.locked = not fake.locked
                done = "Verrouillé" if fake.locked else "Déverrouillé"
            elif cid == "console.modeToggle":
                if not fake.xtouch:
                    return refused("X-Touch non connectée.")
                fake.mode = "standard" if fake.mode == "mc" else "mc"
                done = "X-Touch en mode " + ("Mackie (MC)" if fake.mode == "mc" else "standard")
            else:
                return 404, {"error": {"code": "COMMAND_NOT_FOUND", "message": f"commande « {cid} » inconnue"}}
            return 200, {"ok": True, "message": done, "data": None}

        self.deck_state = deck_state

        class H(BaseHTTPRequestHandler):
            def _send(self, code, data):
                body = json.dumps(data, ensure_ascii=False).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _auth(self):
                fake.deck_headers.append(dict(self.headers))
                if fake.token and (self.headers.get("Authorization") or "") != f"Bearer {fake.token}":
                    self._send(401, {"error": {"code": "INVALID_TOKEN", "message": "jeton API absent ou invalide"}})
                    return False
                return True

            def _events(self):
                fake.deck_streams += 1
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                last = deck_state()
                self.wfile.write(f"event: state\ndata: {json.dumps(last, ensure_ascii=False)}\n\n".encode())
                self.wfile.flush()
                beat = time.monotonic()
                while True:
                    time.sleep(0.05)
                    new = deck_state()
                    pt = deck_patch(last, new)
                    if pt:
                        last = new
                        self.wfile.write(f"event: patch\ndata: {json.dumps(pt, ensure_ascii=False)}\n\n".encode())
                        self.wfile.flush()
                    elif time.monotonic() - beat > 1:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                        beat = time.monotonic()

            def do_GET(self):
                if not self._auth():
                    return
                if self.path == "/api/deck/events":
                    try:
                        return self._events()
                    except OSError:
                        return
                if self.path == "/api/deck/state":
                    fake.deck_reads += 1
                    return self._send(200, deck_state())
                self._send(404, {"error": {"code": "ROUTE_NOT_FOUND", "message": "route inconnue"}})

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b""
                if not self._auth():
                    return
                if not self.path.startswith("/api/deck/commands/"):
                    return self._send(404, {"error": {"code": "ROUTE_NOT_FOUND", "message": "route inconnue"}})
                cid = unquote(self.path.split("/api/deck/commands/", 1)[1])
                args = (json.loads(raw) if raw else {}).get("args") or {}
                fake.deck_calls.append((cid, args, self.headers.get("Authorization")))
                return self._send(*deck_run(cid, args))

            def log_message(self, *a):
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", port), H)
        self.srv.daemon_threads = True
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


class FakeDmaix:
    """API Stream Deck de DMAIX (dmaix-v2 : apps/server/src/services/deck.ts et http/routes/deck.ts) :
    jeton facultatif (vide = API ouverte, sinon « Authorization: Bearer », 401 INVALID_TOKEN), touches
    construites comme buildKeys (État, Scènes, Effets, Commandes), flux /api/deck/events (« state » puis
    « patch »), erreurs { error: { code, message } } : 404 COMMAND_NOT_FOUND / ROUTE_NOT_FOUND, 409
    COMMAND_REFUSED, 422 VALIDATION_FAILED. Jamais de 404 NOT_FOUND."""
    TO_FIX = "Élément à corriger dans la bibliothèque."
    COMMANDS = {"scene.play": "scene", "effect.toggle": "effect", "effect.start": "effect",
                "effect.stop": "effect", "effects.stopAll": None, "dmx.blackout": None}

    def __init__(self, port, token=""):
        self.token = token
        self.output = {"enabled": True, "driver": "enttec", "connected": True}
        self.scenes = [{"id": "sc-1", "name": "Plein feu", "playable": True},
                       {"id": "sc-2", "name": "Ambiance bleue", "playable": True},
                       {"id": "sc-3", "name": "Cassée", "playable": False}]
        self.effects = [{"id": "fx-1", "name": "Strobe", "durationMs": 4000, "playable": True},
                        {"id": "fx-2", "name": "Chase", "durationMs": None, "playable": True}]
        self.scene = None         # {"libraryItemId", "name", "modified"}
        self.running = []         # libraryItemId des effets en cours
        self.blackouts = 0
        self.calls = []           # (commande, corps, en-tête Authorization)
        self.streams = 0
        self.reads = 0
        fake = self

        def key(kid, group, command, args, title, subtitle, active, tone, reason=None):
            return {"id": kid, "group": group, "command": command, "args": args, "title": title,
                    "subtitle": subtitle or None, "active": active, "tone": tone,
                    "available": reason is None, "reason": reason}

        def keys():
            o, sc, n = fake.output, fake.scene, len(fake.running)
            out = [key("output.status", "État", None, {}, "Sortie DMX",
                       "Désactivée" if not o["enabled"] else "Connectée" if o["connected"] else "Déconnectée",
                       o["connected"], "neutral" if not o["enabled"] else "success" if o["connected"] else "danger"),
                   key("scene.current", "État", None, {}, "Scène",
                       f"{sc['name']}{' (modifiée)' if sc['modified'] else ''}" if sc else "Aucune",
                       sc is not None, "info" if sc else "neutral")]
            for it in fake.scenes:
                cur = bool(sc) and sc["libraryItemId"] == it["id"]
                out.append(key(f"scene.play:{it['id']}", "Scènes", "scene.play", {"scene": it["id"]}, it["name"],
                               ("Modifiée" if sc["modified"] else "En cours") if cur else None, cur,
                               "success" if cur else "neutral", None if it["playable"] else fake.TO_FIX))
            for it in fake.effects:
                on = it["id"] in fake.running
                dur = f"{it['durationMs'] // 1000} s" if it["durationMs"] else "Boucle"
                out.append(key(f"effect.toggle:{it['id']}", "Effets", "effect.toggle", {"effect": it["id"]},
                               it["name"], "En cours" if on else dur, on, "live" if on else "neutral",
                               None if it["playable"] or on else fake.TO_FIX))
            out += [key("effects.stopAll", "Commandes", "effects.stopAll", {}, "Stop effets",
                        f"{n} en cours" if n else "Aucun", n > 0, "warning" if n else "neutral",
                        None if n else "Aucun effet en cours."),
                    key("dmx.blackout", "Commandes", "dmx.blackout", {}, "Blackout", None, False, "danger")]
            return out

        def state():
            names = {e["id"]: e["name"] for e in fake.effects}
            return {"token": {"name": "jeton API" if fake.token else "accès libre", "permissions": []},
                    "output": dict(fake.output), "scene": dict(fake.scene) if fake.scene else None,
                    "effects": [{"executionId": f"x-{i}", "libraryItemId": r, "name": names.get(r)}
                                for i, r in enumerate(fake.running)],
                    "library": {"scenes": [dict(s) for s in fake.scenes], "effects": [dict(e) for e in fake.effects]},
                    "commands": [{"id": c, "label": c, "argNames": [a] if a else [], "available": True,
                                  "reason": None} for c, a in fake.COMMANDS.items()],
                    "keys": keys(), "updatedAt": "2026-11-23T12:00:00Z"}

        def patch(old, new):
            changed = {k: v for k, v in new.items() if k not in ("keys", "updatedAt") and old.get(k) != v}
            before, now = {k["id"]: k for k in old["keys"]}, {k["id"]: k for k in new["keys"]}
            keys_ = [k for kid, k in now.items() if before.get(kid) != k]
            removed = [kid for kid in before if kid not in now]
            if not (changed or keys_ or removed):
                return None
            return {"changed": changed, "keys": keys_, "removedKeys": removed, "updatedAt": new["updatedAt"]}

        self.state = state

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def _send(self, code, data):
                body = json.dumps(data, ensure_ascii=False).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _err(self, code, ecode, msg):
                self._send(code, {"error": {"code": ecode, "message": msg}})

            def _auth(self):
                auth = self.headers.get("Authorization") or ""
                if fake.token and auth != f"Bearer {fake.token}":
                    self._err(401, "INVALID_TOKEN", "Jeton API absent ou invalide.")
                    return False
                return True

            def _events(self):
                fake.streams += 1
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                last = state()
                self.wfile.write(f"event: state\ndata: {json.dumps(last, ensure_ascii=False)}\n\n".encode())
                self.wfile.flush()
                beat = time.monotonic()
                while True:
                    time.sleep(0.05)
                    new = state()
                    p = patch(last, new)
                    if p:
                        last = new
                        self.wfile.write(f"event: patch\ndata: {json.dumps(p, ensure_ascii=False)}\n\n".encode())
                        self.wfile.flush()
                    elif time.monotonic() - beat > 1:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        beat = time.monotonic()

            def do_GET(self):
                if not self._auth():
                    return
                fake.reads += 1
                path = self.path.split("?")[0]
                if path == "/api/deck/events":
                    try:
                        return self._events()
                    except OSError:
                        return
                if path == "/api/deck/state":
                    return self._send(200, state())
                self._err(404, "ROUTE_NOT_FOUND", "Route inconnue.")

            def do_POST(self):
                if not self._auth():
                    return
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}") if n else {}
                cid = unquote(self.path.split("/api/deck/commands/", 1)[-1].split("?")[0])
                fake.calls.append((cid, body, self.headers.get("Authorization")))
                if cid not in fake.COMMANDS:
                    return self._err(404, "COMMAND_NOT_FOUND", f"Commande inconnue : {cid}.")
                arg = fake.COMMANDS[cid]
                val = ((body or {}).get("args") or {}).get(arg) if arg else None
                items = {i["id"]: i for i in (fake.scenes if arg == "scene" else fake.effects)} if arg else {}
                if arg and val not in items:
                    return self._send(422, {"error": {"code": "VALIDATION_FAILED", "message": "Argument invalide.",
                                                      "details": {f"args.{arg}": "Valeur inconnue."}}})
                it = items.get(val)
                if it is not None and not it["playable"]:
                    return self._err(409, "COMMAND_REFUSED", f"« {it['name']} » est à corriger.")
                if cid == "scene.play":
                    fake.scene = {"libraryItemId": val, "name": it["name"], "modified": False}
                    return self._send(200, {"ok": True, "message": f"Scène « {it['name']} » jouée", "data": {}})
                if cid == "effect.toggle":
                    if val in fake.running:
                        fake.running.remove(val)
                        return self._send(200, {"ok": True, "message": f"Effet « {it['name']} » arrêté"})
                    fake.running.append(val)
                    return self._send(200, {"ok": True, "message": f"Effet « {it['name']} » lancé"})
                if cid == "effects.stopAll":
                    fake.running.clear()
                    return self._send(200, {"ok": True, "message": "Effets arrêtés"})
                if cid == "dmx.blackout":
                    fake.blackouts += 1
                    return self._send(200, {"ok": True, "message": "Blackout"})
                self._send(200, {"ok": True, "message": f"{cid} : fait"})

            def log_message(self, *a):
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", port), H)
        self.srv.daemon_threads = True
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
