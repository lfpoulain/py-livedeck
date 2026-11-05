"""Faux vMix (API TCP) pour essayer LiveDeck sans vMix : python tools/fake_vmix.py [port] [délai_s]

Assez fidèle pour tester les cas délicats :
- Mix 1 à 4 (paramètre Mix= de l'API : 0 = Master, 1 = Mix 2…), éléments <mix number="n"> du XML ;
  ACTS seulement pour le Master, comme vMix ;
- overlays 1-4 : bascule, In / Out / Off, PreviewOverlayInputN (élément preview="True") ;
- fondu au noir (interrupteur <fadeToBlack>), enregistrement / stream avec durée ;
- un titre (GT) avec champs <text name="…"> modifiables par SetText ; une vidéo avec durée et position
  (Play / Pause / PlayPause / Restart) ;
- « FUNCTION ER » pour un input inconnu ; « FUNCTION OK » envoyé APRÈS les ACTS, comme vMix ;
- delay : temps de traitement de chaque commande (vMix chargé, Wi-Fi…) ;
- hang : vMix figé (connexion ouverte, plus aucune réponse, ni XML ni FUNCTION).
"""
import socket
import sys
import threading
import time
import urllib.parse
import uuid
from xml.sax.saxutils import escape, quoteattr

TITLES = ["Animateur", "General", "Reporter", "Camera Regie", "Ecran Jeu", "Iphone", "PTZ Plateau", "PTZ Atelier",
          "PTZ - Couloir", "PTZ - Showroom", "Invité", "Waiting Screen", "Intro", "Magneto", "ImageIA",
          "VideoIA", "Commentaire", "Bandeau"]
VIDEOS = {"Magneto": 95_000, "Intro": 12_000}  # durées en ms
TITLES_GT = {"Bandeau": {"Headline.Text": "Bonjour", "Description.Text": "LiveDeck"}}
TRANSITIONS = {t.lower() for t in [
    "Cut", "Fade", "Zoom", "Wipe", "Slide", "Fly", "CrossZoom", "FlyRotate", "Cube", "CubeZoom", "VerticalWipe",
    "VerticalSlide", "Merge", "WipeReverse", "SlideReverse", "VerticalWipeReverse", "VerticalSlideReverse",
    "Stinger1", "Stinger2", "Stinger3", "Stinger4", "Transition1", "Transition2", "Transition3", "Transition4"]}
OUTPUTS = ("recording", "streaming", "external", "multicorder")


class FakeVmix:
    def __init__(self):
        self.inputs = []
        for i, t in enumerate(TITLES):
            kind = "Video" if t in VIDEOS else "GT" if t in TITLES_GT else "Capture"
            self.inputs.append({"n": i + 1, "key": str(uuid.uuid4()), "title": t, "type": kind,
                                "duration": VIDEOS.get(t, 0), "pos": 0, "play": None,
                                "texts": dict(TITLES_GT.get(t, {}))})
        self.mixes = {m: {"active": 1, "preview": 2} for m in range(1, 5)}
        self.overlays = {i: {"program": None, "preview": None} for i in range(1, 5)}
        self.outputs = {o: None for o in OUTPUTS}  # sortie -> heure de début (None = arrêtée)
        self.ftb = False
        self.muted = {i["n"]: False for i in self.inputs}
        self.volume = {i["n"]: 100.0 for i in self.inputs}
        # mixage : master (et casque), bus A-G
        self.audio = {"master": {"volume": 80.0, "muted": False, "headphones": 70.0},
                      **{b: {"volume": 100.0, "muted": False} for b in "ABCDEFG"}}
        self.delay = 0.0
        self.hang = False
        self.functions: list[tuple[str, dict]] = []  # journal des commandes reçues
        self.clients: list[tuple[socket.socket, set, threading.Lock]] = []
        self.lock = threading.Lock()  # liste des clients
        self.state_lock = threading.RLock()

    # compatibilité : Master
    @property
    def active(self):
        return self.mixes[1]["active"]

    @property
    def preview(self):
        return self.mixes[1]["preview"]

    @property
    def recording(self):
        return self.outputs["recording"] is not None

    def find(self, ref):
        ref = urllib.parse.unquote(ref or "")
        for i in self.inputs:
            if ref in (str(i["n"]), i["key"]) or ref.lower() == i["title"].lower():
                return i
        return None

    def _pos(self, i) -> int:
        if i["play"] is None:
            return i["pos"]
        pos = i["pos"] + int((time.monotonic() - i["play"]) * 1000)
        if pos >= i["duration"]:  # fin du clip : il s'arrête
            i["pos"], i["play"] = i["duration"], None
            return i["duration"]
        return pos

    def xml(self) -> bytes:
        with self.state_lock:
            ins = []
            for i in self.inputs:
                attrs = (f'key="{i["key"]}" number="{i["n"]}" type="{i["type"]}" title={quoteattr(i["title"])} '
                         f'shortTitle={quoteattr(i["title"])} muted="{self.muted[i["n"]]}" '
                         f'volume="{self.volume[i["n"]]:g}" loop="False"')
                if i["type"] == "Video":
                    pos = self._pos(i)
                    attrs += (f' state="{"Running" if i["play"] is not None else "Paused"}" position="{pos}" '
                              f'duration="{i["duration"]}"')
                else:
                    attrs += ' state="Running" position="0" duration="0"'
                texts = "".join(f'<text index="{k}" name={quoteattr(name)}>{escape(v)}</text>'
                                for k, (name, v) in enumerate(i["texts"].items()))
                ins.append(f'<input {attrs}>{escape(i["title"])}{texts}</input>')
            ovs = ""
            for n, o in self.overlays.items():
                ovs += f'<overlay number="{n}">{o["program"]}</overlay>' if o["program"] else f'<overlay number="{n}"/>'
                if o["preview"]:
                    ovs += f'<overlay number="{n}" preview="True">{o["preview"]}</overlay>'
            outs = ""
            for o, tag in zip(OUTPUTS, ("recording", "streaming", "external", "multiCorder")):
                t0 = self.outputs[o]
                attr = (f' duration="{int(time.monotonic() - t0) if t0 else 0}"'
                        if o in ("recording", "streaming") else "")
                outs += f"<{tag}{attr}>{t0 is not None}</{tag}>"
            mixes = "".join(f'<mix number="{m}"><active>{v["active"]}</active><preview>{v["preview"]}</preview></mix>'
                            for m, v in self.mixes.items() if m > 1)
            trans = ('<transitions><transition number="1" effect="Fade" duration="500"/>'
                     '<transition number="2" effect="Merge" duration="1000"/>'
                     '<transition number="3" effect="Wipe" duration="1000"/>'
                     '<transition number="4" effect="CubeZoom" duration="1000"/></transitions>')
            m1 = self.mixes[1]
            ma = self.audio["master"]
            audio = (f'<audio><master volume="{ma["volume"]:g}" muted="{ma["muted"]}" meterF1="0" meterF2="0" '
                     f'headphonesVolume="{ma["headphones"]:g}"/>'
                     + "".join(f'<bus{b} volume="{self.audio[b]["volume"]:g}" muted="{self.audio[b]["muted"]}" '
                               f'meterF1="0" meterF2="0" solo="False" sendToMaster="False"/>' for b in "ABCDEFG")
                     + "</audio>")
            return (f"<vmix><version>27.0.0.49</version><edition>4K</edition><inputs>{''.join(ins)}</inputs>"
                    f"{audio}"
                    f"<overlays>{ovs}</overlays><preview>{m1['preview']}</preview><active>{m1['active']}</active>"
                    f"<fadeToBlack>{self.ftb}</fadeToBlack>{trans}{outs}<playList>False</playList>{mixes}"
                    f"</vmix>").encode("utf-8")

    def tally(self) -> str:
        m1 = self.mixes[1]
        return "TALLY OK " + "".join("1" if i["n"] == m1["active"] else "2" if i["n"] == m1["preview"] else "0"
                                     for i in self.inputs)

    @staticmethod
    def _send(client, data: bytes) -> None:
        s, _, lock = client
        with lock:  # jamais deux envois mêlés sur une même connexion (XML coupé par un ACTS)
            try:
                s.sendall(data)
            except OSError:
                pass

    def broadcast(self, lines, topic):
        if not lines:
            return
        with self.lock:
            clients = list(self.clients)
        for c in clients:
            if topic in c[1]:
                self._send(c, "".join(line + "\r\n" for line in lines).encode())

    def function(self, name: str, query: str) -> str:
        """Applique une fonction ; renvoie le texte de la réponse (« OK Completed » ou « ER … »)."""
        q = dict(urllib.parse.parse_qsl(query, keep_blank_values=True))
        self.functions.append((name, q))
        print(f"FUNCTION {name} {query}")
        low = name.lower()
        acts = []
        with self.state_lock:
            inp = self.find(q.get("Input")) if q.get("Input") else None
            if q.get("Input") and inp is None:
                return "ER Input not found"
            n = inp["n"] if inp else None
            try:
                mix = int(q.get("Mix") or 0) + 1
            except ValueError:
                return "ER Invalid Mix"
            m = self.mixes.setdefault(mix, {"active": 1, "preview": 2})
            master = mix == 1
            if low in ("previewinput", "cutdirect", "activeinput") and n is None:
                return "ER Input parameter missing"
            if low == "previewinput":
                if master:
                    acts += [f"ACTS OK InputPreview {m['preview']} 0", f"ACTS OK InputPreview {n} 1"]
                m["preview"] = n
            elif low in ("cutdirect", "activeinput"):
                if master:
                    acts += [f"ACTS OK Input {m['active']} 0", f"ACTS OK Input {n} 1"]
                m["active"] = n
            elif low in TRANSITIONS:
                target, old = n or m["preview"], m["active"]
                if target == m["preview"]:  # échange preview / program (réglage par défaut de vMix)
                    m["active"], m["preview"] = target, old
                else:
                    m["active"] = target
                if master:
                    acts += [f"ACTS OK Input {old} 0", f"ACTS OK Input {m['active']} 1",
                             f"ACTS OK InputPreview {target} 0", f"ACTS OK InputPreview {m['preview']} 1"]
            elif low == "fadetoblack":
                self.ftb = not self.ftb
            elif low.startswith("previewoverlayinput") and low[19:].isdigit():
                o = self.overlays.setdefault(int(low[19:]), {"program": None, "preview": None})
                o["preview"] = None if o["preview"] == n else n
            elif low == "overlayinputalloff":
                for o_n, o in self.overlays.items():
                    if o["program"]:
                        acts.append(f"ACTS OK Overlay{o_n} {o['program']} 0")
                        o["program"] = None
            elif low.startswith("overlayinput") and low[12:13].isdigit():
                o_n, mode = int(low[12]), low[13:]
                o = self.overlays.setdefault(o_n, {"program": None, "preview": None})
                old = o["program"]
                if mode == "":
                    o["program"] = None if old == n else n
                elif mode == "in":
                    o["program"] = n
                elif mode in ("out", "off"):
                    o["program"] = None
                if old and old != o["program"]:
                    acts.append(f"ACTS OK Overlay{o_n} {old} 0")
                if o["program"] and o["program"] != old:
                    acts.append(f"ACTS OK Overlay{o_n} {o['program']} 1")
            elif low in ("audio", "audioon", "audiooff") and n:
                self.muted[n] = {"audio": not self.muted[n], "audioon": False, "audiooff": True}[low]
            elif low in ("setvolume", "setmastervolume", "setheadphonesvolume") or (
                    low.startswith("setbus") and low.endswith("volume") and len(low) == 13):
                try:
                    v = max(0.0, min(100.0, float(q.get("Value", ""))))
                except ValueError:
                    return "ER Invalid Value"
                if low == "setvolume":
                    if n is None:
                        return "ER Input parameter missing"
                    self.volume[n] = v
                elif low == "setmastervolume":
                    self.audio["master"]["volume"] = v
                elif low == "setheadphonesvolume":
                    self.audio["master"]["headphones"] = v
                else:
                    self.audio[low[6].upper()]["volume"] = v
            elif low == "setposition" and inp and inp["type"] == "Video":
                inp["pos"] = max(0, min(inp["duration"], int(float(q.get("Value") or 0))))
                if inp["play"] is not None:
                    inp["play"] = time.monotonic()
            elif low == "settext" and inp:
                keys = list(inp["texts"])
                field = q.get("SelectedName")
                if field is None and str(q.get("SelectedIndex", "")).isdigit() and int(q["SelectedIndex"]) < len(keys):
                    field = keys[int(q["SelectedIndex"])]
                if field not in inp["texts"]:
                    return "ER Field not found"
                inp["texts"][field] = q.get("Value", "")
            elif low in ("play", "pause", "playpause", "restart") and inp and inp["type"] == "Video":
                inp["pos"] = self._pos(inp)
                playing = inp["play"] is not None
                if low == "restart":
                    inp["pos"], inp["play"] = 0, time.monotonic() if playing else None
                elif low == "play" or (low == "playpause" and not playing):
                    inp["play"] = time.monotonic()
                else:
                    inp["play"] = None
            else:
                for out in OUTPUTS:
                    if low in (f"startstop{out}", f"start{out}", f"stop{out}"):
                        on = {f"start{out}": True, f"stop{out}": False}.get(low, self.outputs[out] is None)
                        if on != (self.outputs[out] is not None):
                            self.outputs[out] = time.monotonic() if on else None
        self.broadcast(acts, "ACTS")
        self.broadcast([self.tally()], "TALLY")
        return "OK Completed"

    def serve(self, conn: socket.socket):
        subs = set()
        client = (conn, subs, threading.Lock())
        with self.lock:
            self.clients.append(client)
        self._send(client, b"VERSION OK 27.0.0.49\r\n")
        buf = b""
        try:
            while data := conn.recv(4096):
                buf += data
                while b"\r\n" in buf:
                    line, buf = buf.split(b"\r\n", 1)
                    if self.hang:
                        continue  # vMix figé : plus rien ne répond
                    cmd, _, rest = line.decode("utf-8", "replace").partition(" ")
                    if cmd == "SUBSCRIBE":
                        subs.add(rest.strip())
                        self._send(client, f"SUBSCRIBE OK {rest.strip()}\r\n".encode())
                        if rest.strip() == "TALLY":
                            self._send(client, (self.tally() + "\r\n").encode())
                    elif cmd == "XML":
                        x = self.xml()
                        self._send(client, f"XML {len(x)}\r\n".encode() + x)
                    elif cmd == "FUNCTION":
                        name, _, query = rest.partition(" ")
                        if self.delay:
                            time.sleep(self.delay)  # vMix occupé : la commande est traitée en retard
                        reply = self.function(name, query)
                        self._send(client, f"FUNCTION {reply}\r\n".encode())
        except OSError:
            pass
        finally:
            with self.lock:
                self.clients = [c for c in self.clients if c[0] is not conn]


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8099
    vm = FakeVmix()
    vm.delay = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0
    srv = socket.create_server(("127.0.0.1", port))
    print(f"Faux vMix à l'écoute sur 127.0.0.1:{port}" + (f" (délai {vm.delay * 1000:.0f} ms)" if vm.delay else ""))
    while True:
        conn, _ = srv.accept()
        threading.Thread(target=vm.serve, args=(conn,), daemon=True).start()


if __name__ == "__main__":
    main()
