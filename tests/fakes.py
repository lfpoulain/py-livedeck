"""Faux serveurs vMix, py-intercom, orchestream et Tally32 pour les tests."""
import json
import queue
import socket
import sys
import threading
import time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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
    def __init__(self, port):
        self.visible = {"twizz": False, "poll": False, "speedizz": False}
        self.speedizz_run = False
        self.clients: list[queue.Queue] = []
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

            def do_GET(self):
                if self.path == "/api/overlay/events":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    q = queue.Queue()
                    fake.clients.append(q)
                    snap = {"isVisible": fake.visible["speedizz"]} if fake.speedizz_run else None
                    # comme orchestream : instantanés au branchement, puis battements de cœur (commentaires SSE)
                    try:  # le client peut refermer tout de suite (bouton « Tester »)
                        self.wfile.write(b"event: polls:snapshot\ndata: []\n\n:heartbeat\n\n")
                        self.wfile.write(f"event: speedizz:snapshot\ndata: {json.dumps(snap)}\n\n".encode())
                        self.wfile.flush()
                        while True:
                            ev, data = q.get()
                            self.wfile.write(f"event: {ev}\ndata: {json.dumps(data)}\n\n".encode())
                            self.wfile.flush()
                    except OSError:
                        fake.clients.remove(q)
                    return
                self.do_POST()

            def do_POST(self):
                parts = self.path.strip("/").split("/")  # api overlay <name> <show|hide>
                if len(parts) == 4 and parts[:2] == ["api", "overlay"]:
                    name, act = parts[2], parts[3]
                    if name == "speedizz" and act == "show" and not fake.speedizz_run:
                        return self._send(404, {"success": False, "message": "Aucun Speedizz à afficher"})
                    fake.set(name, act == "show")
                    return self._send(200, {"success": True, "isVisible": act == "show"})
                self._send(404, {"error": "?"})

            def log_message(self, *a):
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", port), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def set(self, name, visible):
        self.visible[name] = visible
        self.emit(f"{name}:overlay:toggle", {"isVisible": visible})

    def emit(self, ev, data):
        for q in list(self.clients):
            q.put((ev, data))


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
