"""Faux vMix (API TCP) pour essayer LiveDeck sans vMix : python tools/fake_vmix.py [port]"""
import socket
import sys
import threading
import urllib.parse
import uuid

TITLES = ["Animateur", "General", "Reporter", "Camera Regie", "Ecran Jeu", "Iphone", "PTZ Plateau", "PTZ Atelier",
          "PTZ - Couloir", "PTZ - Showroom", "Invité", "Waiting Screen", "Intro", "Magneto", "ImageIA",
          "VideoIA", "Commentaire"]


class FakeVmix:
    def __init__(self):
        self.inputs = [{"n": i + 1, "key": str(uuid.uuid4()), "title": t} for i, t in enumerate(TITLES)]
        self.active, self.preview = 1, 2
        self.overlays = {i: None for i in range(1, 5)}
        self.recording = False
        self.muted = {i["n"]: False for i in self.inputs}
        self.clients: list[tuple[socket.socket, set]] = []
        self.lock = threading.Lock()

    def find(self, ref):
        ref = urllib.parse.unquote(ref or "")
        for i in self.inputs:
            if ref in (str(i["n"]), i["key"]) or ref.lower() == i["title"].lower():
                return i["n"]
        return None

    def xml(self) -> bytes:
        ins = "".join(f'<input key="{i["key"]}" number="{i["n"]}" type="Capture" title="{i["title"]}" '
                      f'shortTitle="{i["title"]}" state="Running" muted="{self.muted[i["n"]]}">{i["title"]}</input>' for i in self.inputs)
        ovs = "".join(f'<overlay number="{n}">{v}</overlay>' if v else f'<overlay number="{n}"/>'
                      for n, v in self.overlays.items())
        return (f"<vmix><version>27.0.0.49</version><inputs>{ins}</inputs><overlays>{ovs}</overlays>"
                f"<preview>{self.preview}</preview><active>{self.active}</active>"
                f"<recording>{self.recording}</recording><streaming>False</streaming></vmix>").encode()

    def tally(self) -> str:
        return "TALLY OK " + "".join("1" if i["n"] == self.active else "2" if i["n"] == self.preview else "0"
                                     for i in self.inputs)

    def broadcast(self, lines, topic):
        for s, subs in list(self.clients):
            if topic in subs:
                try:
                    s.sendall("".join(line + "\r\n" for line in lines).encode())
                except OSError:
                    pass

    def function(self, name: str, query: str):
        q = dict(urllib.parse.parse_qsl(query))
        n = self.find(q.get("Input"))
        acts = []
        low = name.lower()
        if low == "previewinput" and n:
            acts += [f"ACTS OK InputPreview {self.preview} 0", f"ACTS OK InputPreview {n} 1"]
            self.preview = n
        elif low in ("cutdirect", "activeinput") and n:
            acts += [f"ACTS OK Input {self.active} 0", f"ACTS OK Input {n} 1"]
            self.active = n
        elif low in ("cut", "fade", "merge", "stinger1", "stinger2", "wipe", "zoom"):
            self.active, self.preview = (n or self.preview), self.active
            acts += [f"ACTS OK Input {self.active} 1", f"ACTS OK InputPreview {self.preview} 1"]
        elif low.startswith("overlayinput") and low[12:13].isdigit():
            o = int(low[12])
            self.overlays[o] = None if self.overlays.get(o) == n else n
            acts.append(f"ACTS OK Overlay{o} {n} {1 if self.overlays[o] else 0}")
        elif low in ("audio", "audioon", "audiooff") and n:
            self.muted[n] = {"audio": not self.muted[n], "audioon": False, "audiooff": True}[low]
        elif low == "startstoprecording":
            self.recording = not self.recording
        print(f"FUNCTION {name} {query}")
        self.broadcast(acts, "ACTS")
        self.broadcast([self.tally()], "TALLY")

    def serve(self, conn: socket.socket):
        subs = set()
        with self.lock:
            self.clients.append((conn, subs))
        conn.sendall(b"VERSION OK 27.0.0.49\r\n")
        buf = b""
        try:
            while data := conn.recv(4096):
                buf += data
                while b"\r\n" in buf:
                    line, buf = buf.split(b"\r\n", 1)
                    cmd, _, rest = line.decode().partition(" ")
                    if cmd == "SUBSCRIBE":
                        subs.add(rest.strip())
                        conn.sendall(f"SUBSCRIBE OK {rest.strip()}\r\n".encode())
                        if rest.strip() == "TALLY":
                            conn.sendall((self.tally() + "\r\n").encode())
                    elif cmd == "XML":
                        x = self.xml()
                        conn.sendall(f"XML {len(x)}\r\n".encode() + x)
                    elif cmd == "FUNCTION":
                        name, _, query = rest.partition(" ")
                        conn.sendall(b"FUNCTION OK Completed\r\n")
                        self.function(name, query)
        except OSError:
            pass
        finally:
            with self.lock:
                self.clients = [c for c in self.clients if c[0] is not conn]


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8099
    vm = FakeVmix()
    srv = socket.create_server(("127.0.0.1", port))
    print(f"Faux vMix à l'écoute sur 127.0.0.1:{port}")
    while True:
        conn, _ = srv.accept()
        threading.Thread(target=vm.serve, args=(conn,), daemon=True).start()


if __name__ == "__main__":
    main()
