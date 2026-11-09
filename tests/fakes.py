"""Faux serveurs vMix, py-intercom, orchestream et Tally32 pour les tests."""
import json
import queue
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
    """Comme l'API HTTP du client py-intercom (client/remote.py + gui._handle_remote_command) :
    route avec numéros de bus et paramètres d'URL, 403 navigateur, 409 hors ligne (avec l'état), 404 bus
    inconnu / non proposé par le serveur, 400 si /ptt/all/<autre que off>, Always On (« on » accepté sans effet,
    off / toggle refusés en 409), 503 « interface occupée » à la demande. Seuls les bus de self.buses sont
    proposés ; « active » est faux pour tous quand le client n'est pas en ligne."""
    KEYS = {"regie": 0, "plateau": 1, "vmix": 2}

    def __init__(self, port):
        self.online = True
        self.buses = {"regie": {"mode": "ptt", "active": False}, "plateau": {"mode": "toggle", "active": False},
                      "vmix": {"mode": "always_on", "active": True}}
        self.log = []
        self.ignore_off = 0  # nombre de « off » acceptés (200) mais sans effet : « off » perdu côté client
        self.busy = 0  # nombre de réponses 503 « interface occupée » à renvoyer
        self.slow: list[float] = []  # délais (s) des prochaines commandes : client lent mais vivant
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

            def _handle(self, method):
                if self.headers.get("Origin") or self.headers.get("Referer"):
                    return self._send(403, {"error": "requêtes de navigateur refusées"})
                parts = [p for p in self.path.split("?", 1)[0].split("/") if p]
                if parts == ["status"]:
                    if method != "GET":
                        return self._send(405, {"error": "utilisez GET /status"})
                    return self._send(200, fake.payload())
                if len(parts) != 3 or parts[0] != "ptt":
                    return self._send(404, {"error": "routes : GET /status, POST /ptt/<bus>/<on|off|toggle>"})
                if method != "POST":
                    return self._send(405, {"error": "les commandes PTT exigent POST"})
                _, token, cmd = parts
                cmd = cmd.lower()
                fake.log.append((token.lower(), cmd))
                with fake._gui:  # comme le QtDispatcher du client : une commande à la fois, dans l'ordre
                    if fake.slow:  # interface occupée : la commande est appliquée (et répondue) en retard
                        time.sleep(fake.slow.pop(0))
                    if token.lower() == "all":
                        if cmd != "off":
                            return self._send(400, {"error": "seul POST /ptt/all/off est accepté"})
                        bus = None
                    else:
                        bus = next((k for k, i in fake.KEYS.items() if token.lower() in (k, str(i))), None)
                        if bus is None:
                            return self._send(404, {"error": f"bus inconnu « {token} » ; attendus : regie, plateau, vmix"})
                        if cmd not in ("on", "off", "toggle"):
                            return self._send(404, {"error": "action attendue : on, off ou toggle"})
                    if fake.busy > 0:
                        fake.busy -= 1
                        return self._send(503, {"error": "interface occupée, réessayez"})
                    if not fake.online:
                        return self._send(409, {"error": "PTT désarmé : le client n'est pas en ligne", **fake.payload()})
                    if bus is not None and bus not in fake.buses:
                        return self._send(404, {"error": "bus non proposé par ce serveur"})
                    for b in ([bus] if bus else list(fake.buses)):
                        st = fake.buses[b]
                        if st["mode"] == "always_on":
                            if bus is not None and cmd != "on":
                                return self._send(409, {"error": "bus en mode Always On : changez son mode dans le client"})
                            continue
                        if cmd == "off" and fake.ignore_off > 0:
                            fake.ignore_off -= 1
                            continue
                        st["active"] = (not st["active"]) if cmd == "toggle" else cmd == "on"
                    self._send(200, fake.payload())

            def do_GET(self):
                self._handle("GET")

            def do_POST(self):
                self._handle("POST")

            def log_message(self, *a):
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", port), H)
        self.srv.daemon_threads = True
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def payload(self) -> dict:
        return {"state": "En ligne" if self.online else "Hors ligne", "online": self.online,
                "buses": {k: {"mode": v["mode"], "active": bool(self.online and (v["mode"] == "always_on" or v["active"]))}
                          for k, v in self.buses.items()}}

    def stop(self):
        self.srv.shutdown()
        self.srv.server_close()


class FakeOrchestream:
    """orchestream-v2 (par défaut) ou v1 (v1=True).
    v2 : GET /api/overlay/state, /api/overlay/<w>/show|hide (GET ou POST, 409 OVERLAY_EMPTY sans contenu),
    temps réel Transmit (GET /__transmit/events?uid=…, POST /__transmit/subscribe {uid, channel}).
    external=True imite une requête arrivée par l'adresse publique : 404 NOT_FOUND partout."""

    def __init__(self, port, v1=False):
        self.v1 = v1
        self.external = False
        self.visible = {"twizz": False, "poll": False, "speedizz": False}
        self.content = {"twizz": True, "poll": True, "speedizz": False}  # v2 : un widget sans contenu refuse « show »
        self.speedizz_run = False
        self.clients: list[queue.Queue] = []
        self.streams: dict[str, queue.Queue] = {}  # v2 : uid → file du flux
        self.subscribed: dict[str, set] = {}
        fake = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def _send(self, code, data):
                body = json.dumps(data).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _stream(self, q, first: bytes, fmt):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                try:  # le client peut refermer tout de suite (bouton « Tester »)
                    self.wfile.write(first)
                    self.wfile.flush()
                    while True:
                        self.wfile.write(fmt(q.get()))
                        self.wfile.flush()
                except OSError:
                    pass

            def do_GET(self):
                path = self.path.split("?", 1)[0]
                if fake.external:
                    return self._send(404, {"error": {"code": "NOT_FOUND", "message": "Ressource introuvable."}})
                if fake.v1:
                    if path == "/api/overlay/events":
                        q = queue.Queue()
                        fake.clients.append(q)
                        snap = {"isVisible": fake.visible["speedizz"]} if fake.speedizz_run else None
                        # comme orchestream v1 : instantanés au branchement, puis battements de cœur
                        self._stream(q, b"event: polls:snapshot\ndata: []\n\n:heartbeat\n\n"
                                     + f"event: speedizz:snapshot\ndata: {json.dumps(snap)}\n\n".encode(),
                                     lambda m: f"event: {m[0]}\ndata: {json.dumps(m[1])}\n\n".encode())
                        fake.clients.remove(q)
                        return
                    return self.do_POST()
                if path == "/api/overlay/state":
                    return self._send(200, {"widgets": {w: fake.widget(w) for w in fake.visible}})
                if path == "/__transmit/events":
                    uid = self.path.split("uid=", 1)[-1] if "uid=" in self.path else ""
                    if not uid:
                        return self._send(500, {"error": {"code": "E_RUNTIME", "message": "Missing uid"}})
                    q = queue.Queue()
                    fake.streams[uid], fake.subscribed[uid] = q, set()
                    self._stream(q, b":ok\n\n", lambda m: f"data: {json.dumps(m)}\n\n".encode())
                    fake.streams.pop(uid, None)
                    fake.subscribed.pop(uid, None)
                    return
                self.do_POST()

            def do_POST(self):
                path = self.path.split("?", 1)[0]
                if fake.external:
                    return self._send(404, {"error": {"code": "NOT_FOUND", "message": "Ressource introuvable."}})
                if path == "/__transmit/subscribe" and not fake.v1:
                    n = int(self.headers.get("Content-Length") or 0)
                    body = json.loads(self.rfile.read(n) or b"{}")
                    if body.get("uid") not in fake.subscribed or body.get("channel") != "overlay":
                        return self._send(400, {"error": {"code": "BAD_REQUEST", "message": "Requête invalide."}})
                    fake.subscribed[body["uid"]].add("overlay")
                    self.send_response(204)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                parts = path.strip("/").split("/")  # api overlay <name> <show|hide>
                if len(parts) == 4 and parts[:2] == ["api", "overlay"]:
                    name, act = parts[2], parts[3]
                    if fake.v1:
                        if name == "speedizz" and act == "show" and not fake.speedizz_run:
                            return self._send(404, {"success": False, "message": "Aucun Speedizz à afficher"})
                        fake.set(name, act == "show")
                        return self._send(200, {"success": True, "isVisible": act == "show"})
                    if name not in fake.visible:
                        return self._send(422, {"error": {"code": "E_VALIDATION_ERROR", "message": "Requête invalide."}})
                    if act == "show" and not fake.content[name]:
                        return self._send(409, {"error": {"code": "OVERLAY_EMPTY",
                                                          "message": "Aucun Speedizz à afficher : lancez une partie d’abord."}})
                    fake.set(name, act == "show")
                    return self._send(200, fake.widget(name))
                if fake.v1:  # route inconnue d'AdonisJS 6 (orchestream v1)
                    return self._send(404, {"errors": [{"message": f"Cannot {self.command}:{path}",
                                                        "code": "E_ROUTE_NOT_FOUND"}]})
                self._send(404, {"error": {"code": "NOT_FOUND", "message": "Ressource introuvable."}})

            def log_message(self, *a):
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", port), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def widget(self, name):
        return {"widget": name, "visible": self.visible.get(name, False),
                "data": {"name": name} if self.content.get(name, True) else None}

    def set(self, name, visible):
        """L'overlay change (depuis LiveDeck ou la régie d'orchestream) : diffusé aux clients abonnés."""
        self.visible[name] = visible
        self.content.setdefault(name, True)
        if self.v1:
            self.emit(f"{name}:overlay:toggle", {"isVisible": visible})
        else:
            self.broadcast("widget.update", self.widget(name))

    def emit(self, ev, data):
        """v1 : événement SSE nommé."""
        for q in list(self.clients):
            q.put((ev, data))

    def broadcast(self, type_, data):
        """v2 : message Transmit du canal « overlay »."""
        msg = {"channel": "overlay", "payload": {"type": type_, "data": data, "at": "2026-11-04T12:00:00Z"}}
        for uid, q in list(self.streams.items()):
            if "overlay" in self.subscribed.get(uid, ()):
                q.put(msg)

    def ping(self):
        for q in list(self.streams.values()):
            q.put({"channel": "$$transmit/ping", "payload": {}})


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


class FakeMultiview:
    """API locale de py-multiview : GET /status, commandes POST à corps vide."""

    def __init__(self, port):
        self.streams = [{"index": 1, "id": "s-1", "name": "Caméra 1", "source": "srt", "running": False,
                         "status": "stopped", "detail": "", "muted": False, "display": "Écran 2"},
                        {"index": 2, "id": "s-2", "name": "Plateau", "source": "omt", "running": True,
                         "status": "active", "detail": "", "muted": False, "display": "Écran 3"}]
        self.sender = False
        self.monitor = False
        self.floating = False
        self.volume = 100
        self.calls = []
        self.refuse = ""  # message d'un refus métier (409)
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

        class MV(BaseHTTPRequestHandler):
            def _send(self, code, data):
                body = json.dumps(data, ensure_ascii=False).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _error(self, code, msg):
                self._send(code, {"error": {"code": "REFUSED", "message": msg}})

            def _find(self, ref):
                return next((s for s in fake.streams if ref in (str(s["index"]), s["id"], s["name"])), None)

            def do_GET(self):
                if self.path == "/status":
                    return self._send(200, payload())
                self._send(404, {"error": {"code": "NOT_FOUND", "message": "routes : GET /status"}})

            def do_POST(self):
                parts = [unquote(p) for p in self.path.strip("/").split("/") if p]
                fake.calls.append("/".join(parts))
                if fake.refuse:
                    return self._error(409, fake.refuse)
                if len(parts) == 2 and parts[0] in ("sender", "monitor", "floating", "reception"):
                    want = parts[1] == "on"
                    if parts[0] == "reception":
                        for s in fake.streams:
                            s["running"] = want
                            s["status"] = "active" if want else "stopped"
                    else:
                        setattr(fake, parts[0], want)
                    return self._send(200, payload())
                if len(parts) == 3 and parts[:2] == ["monitor", "volume"]:
                    fake.volume = int(parts[2])
                    return self._send(200, payload())
                if len(parts) == 3 and parts[0] == "stream":
                    s = self._find(parts[1])
                    if s is None:
                        return self._error(404, f"flux « {parts[1]} » inconnu")
                    s["running"] = parts[2] == "on"
                    s["status"] = "active" if s["running"] else "stopped"
                    return self._send(200, payload())
                if len(parts) == 4 and parts[0] == "stream" and parts[2] == "mute":
                    s = self._find(parts[1])
                    if s is None:
                        return self._error(404, f"flux « {parts[1]} » inconnu")
                    s["muted"] = parts[3] == "on"
                    return self._send(200, payload())
                self._error(404, "route inconnue")

            def log_message(self, *a):
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", port), MV)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()


class FakeOrchDeck:
    """API Stream Deck d'orchestream-v2, d'après packages/back (deck_controller, api_token_middleware) :
    jeton dans « Authorization: Bearer » ou « X-Orchestream-Token » (jamais dans l'URL), 401
    INVALID_TOKEN, 403 FORBIDDEN_ROLE selon les permissions du jeton, erreurs métier 409 avec message en
    français, 429 TOO_MANY_REQUESTS + Retry-After, réponse { ok, message } des commandes."""
    TOKEN = "osk_test_123"
    PERMS = {"game.advance": "games.run", "game.next": "games.run", "draw.run": "games.run",
             "twizz.start": "games.run", "activity.ackAll": "thanks", "overlay.show": "overlay.control",
             "live.end": "live.control"}

    def __init__(self, port):
        self.permissions = {"read", "games.run", "thanks", "overlay.control"}  # pas live.control
        self.live = True
        self.game = {"kind": "twizz", "runName": "Culture G", "phase": "open", "questionIndex": 2,
                     "questionNumber": 3, "totalQuestions": 10, "nextAction": "close"}
        self.unthanked = 5
        self.overlays = {"twizz": True, "poll": False, "speedizz": False, "hypeTrain": False, "prediction": False}
        self.calls = []       # (commande, args, en-tête utilisé)
        self.reads = 0
        self.throttle = 0     # nombre de 429 à renvoyer
        self.quizzes = [{"value": "q-1", "label": "Culture G"}, {"value": "q-2", "label": "Cinéma"}]
        fake = self

        def info(cid):
            return {"id": cid, "label": cid, "permission": self.PERMS[cid], "description": "",
                    "available": not (cid == "draw.run" and not fake.live),
                    "reason": None if fake.live or cid != "draw.run" else "Aucune session live en cours.",
                    "argNames": ["quizId"] if cid == "twizz.start" else (["widget"] if cid == "overlay.show" else [])}

        def allowed(cid):
            return self.PERMS[cid] in fake.permissions

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
                    "commands": [info(c) for c in fake.PERMS if allowed(c)], "updatedAt": "2026-11-06T12:00:00Z"}

        class H(BaseHTTPRequestHandler):
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
                if raw != fake.TOKEN:
                    self._err(401, "INVALID_TOKEN", "Jeton API manquant, invalide ou révoqué.")
                    return None
                if fake.throttle:
                    fake.throttle -= 1
                    self._err(429, "TOO_MANY_REQUESTS", "Trop de requêtes. Réessayez dans 1 seconde.",
                              {"Retry-After": "1"})
                    return None
                return "bearer" if auth else "custom"

            def do_GET(self):
                if self._auth() is None:
                    return
                fake.reads += 1
                path = self.path.split("?")[0]
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
                fake.calls.append((cid, (body or {}).get("args") or {}, how))
                if cid not in fake.PERMS:
                    return self._err(404, "COMMAND_NOT_FOUND", f"Commande inconnue : {cid}")
                if not allowed(cid):
                    return self._err(403, "FORBIDDEN_ROLE",
                                     "Action non autorisée pour le jeton « Stream Deck plateau ».")
                if cid == "twizz.start":
                    quiz = ((body or {}).get("args") or {}).get("quizId")
                    if quiz not in {q["value"] for q in fake.quizzes}:
                        return self._err(422, "VALIDATION_FAILED", "Quiz inconnu.")
                    return self._send(200, {"ok": True, "message": "Twizz lancé"})
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
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
