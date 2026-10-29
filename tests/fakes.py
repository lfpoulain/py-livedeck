"""Faux serveurs vMix, py-intercom, orchestream et Tally32 pour les tests."""
import json
import queue
import socket
import sys
import threading
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
    def __init__(self, port):
        self.online = True
        self.buses = {"regie": {"mode": "ptt", "active": False}, "plateau": {"mode": "toggle", "active": False},
                      "vmix": {"mode": "always_on", "active": True}}
        self.log = []
        fake = self

        class H(BaseHTTPRequestHandler):
            def _send(self, code, data):
                body = json.dumps(data).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def state(self):
                return {"state": "En ligne" if fake.online else "Hors ligne", "online": fake.online, "buses": fake.buses}

            def do_GET(self):
                if self.headers.get("Origin") or self.headers.get("Referer"):
                    return self._send(403, {"error": "navigateur refusé"})
                if self.path == "/status":
                    return self._send(200, self.state())
                if self.path.startswith("/ptt/"):
                    return self._send(405, {"error": "POST attendu"})
                self._send(404, {"error": "route inconnue"})

            def do_POST(self):
                if self.headers.get("Origin") or self.headers.get("Referer"):
                    return self._send(403, {"error": "navigateur refusé"})
                parts = self.path.strip("/").split("/")
                if len(parts) != 3 or parts[0] != "ptt":
                    return self._send(404, {"error": "route inconnue"})
                _, bus, cmd = parts
                fake.log.append((bus, cmd))
                if not fake.online:
                    return self._send(409, {"error": "client pas en ligne", **self.state()})
                targets = list(fake.buses) if bus == "all" else [bus.lower()]
                for b in targets:
                    if b not in fake.buses:
                        return self._send(404, {"error": "bus inconnu"})
                    if fake.buses[b]["mode"] == "always_on" and cmd == "off" and bus != "all":
                        return self._send(409, {"error": "bus en mode Always On"})
                    if fake.buses[b]["mode"] == "always_on":
                        continue
                    fake.buses[b]["active"] = {"on": True, "off": False}.get(cmd, not fake.buses[b]["active"])
                self._send(200, self.state())

            def log_message(self, *a):
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", port), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()


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
                    self.wfile.write(f"event: speedizz:snapshot\ndata: {json.dumps(snap)}\n\n".encode())
                    self.wfile.flush()
                    try:
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
        for q in list(self.clients):
            q.put((f"{name}:overlay:toggle", {"isVisible": visible}))


class FakeTally:
    def __init__(self, port, connected):
        self.connected = connected
        self.reconnects = 0
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
                if self.path == "/vmix/reconnect":
                    fake.reconnects += 1
                    fake.connected = True
                    return self._send(200, {"status": "ok", "message": "Reconnexion VMix demandee"})
                self._send(404, {})

            def log_message(self, *a):
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", port), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
