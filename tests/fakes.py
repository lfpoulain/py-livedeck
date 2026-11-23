"""Faux serveurs vMix, py-intercom, orchestream-v2, Tally32 et py-multiview pour les tests."""
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
