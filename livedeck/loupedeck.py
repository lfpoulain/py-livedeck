"""Loupedeck (CT, CT V2, Live, Live S, Razer Stream Controller) : pilote série.

L'appareil se présente comme un port série USB (CDC) sur lequel on ouvre une
pseudo-connexion WebSocket : poignée de main HTTP, puis des trames binaires
préfixées de l'octet magique 0x82 suivi de leur longueur.

Chaque message utile vaut [longueur, commande, n° de transaction, données…].

Ce module ne dépend que de pyserial et Pillow : ni Qt, ni le moteur LiveDeck.
"""
from __future__ import annotations

import logging
import struct
import threading
import time

from PIL import Image

log = logging.getLogger("livedeck")

# -- protocole ---------------------------------------------------------------
MAGIC = 0x82
BAUD = 256000
WS_UPGRADE = (b"GET /index.html\nHTTP/1.1\nConnection: Upgrade\n"
              b"Upgrade: websocket\nSec-WebSocket-Key: 123abc\n\n")
WS_RESPONSE = b"HTTP/1.1"
WS_CLOSE = bytes([0x88, 0x80, 0x00, 0x00, 0x00, 0x00])

CMD_BUTTON = 0x00
CMD_ROTATE = 0x01
CMD_COLOR = 0x02
CMD_SERIAL = 0x03
CMD_RESET = 0x06
CMD_VERSION = 0x07
CMD_BRIGHTNESS = 0x09
CMD_FRAMEBUFF = 0x10
CMD_DRAW = 0x0F
CMD_VIBRATE = 0x1B
CMD_TOUCH = 0x4D
CMD_TOUCH_CT = 0x52
CMD_TOUCH_END = 0x6D
CMD_TOUCH_END_CT = 0x72

MAX_BRIGHTNESS = 10

# Identifiant d'appareil -> nom de contrôle. Vérifié sur un CT V2 (firmware 0.1.2) :
# les 26 boutons remontent, les 7 encodeurs tournent, le tactile couvre x 0..479.
BUTTON_IDS = {
    0x00: "wheel", 0x01: "knobTL", 0x02: "knobCL", 0x03: "knobBL",
    0x04: "knobTR", 0x05: "knobCR", 0x06: "knobBR",
    0x07: "key0", 0x08: "key1", 0x09: "key2", 0x0A: "key3",
    0x0B: "key4", 0x0C: "key5", 0x0D: "key6", 0x0E: "key7",
    0x0F: "home", 0x10: "undo", 0x11: "keyboard", 0x12: "enter",
    0x13: "save", 0x14: "fnL", 0x15: "a", 0x16: "c", 0x17: "fnR",
    0x18: "b", 0x19: "d", 0x1A: "e",
}
BUTTON_CODES = {v: k for k, v in BUTTON_IDS.items()}

# Motifs haptiques utiles (le firmware en accepte beaucoup d'autres)
HAPTIC = {
    "court": 0x01, "moyen": 0x0A, "long": 0x0F, "grave": 0x31,
    "montant": 0x53, "descendant": 0x47, "vibration": 0x70,
}

VENDOR_IDS = (0x2EC2, 0x1532)  # Loupedeck, Razer


class Profile:
    """Description d'un modèle : écrans, grille de touches, contrôles physiques."""

    def __init__(self, name: str, displays: dict, cols: int, rows: int, key_size: int,
                 keys_x: int, buttons: tuple, knobs: tuple, strips: tuple = (), wheel: bool = False):
        self.name, self.displays = name, displays
        self.cols, self.rows, self.key_size = cols, rows, key_size
        self.keys_x = keys_x  # abscisse de la première colonne sur l'écran central
        self.buttons, self.knobs, self.strips, self.wheel = buttons, knobs, strips, wheel

    @property
    def keys(self) -> int:
        return self.cols * self.rows


_CT_BUTTONS = ("key0", "key1", "key2", "key3", "key4", "key5", "key6", "key7",
               "home", "undo", "keyboard", "enter", "save",
               "fnL", "a", "b", "c", "d", "fnR", "e")
_CT_KNOBS = ("knobTL", "knobCL", "knobBL", "knobTR", "knobCR", "knobBR")

# Nom lisible de chaque contrôle, pour l'inspecteur et les journaux.
CONTROL_LABELS = {
    "knobTL": "Molette haut gauche", "knobCL": "Molette milieu gauche", "knobBL": "Molette bas gauche",
    "knobTR": "Molette haut droite", "knobCR": "Molette milieu droite", "knobBR": "Molette bas droite",
    "wheel": "Molette centrale", "home": "Home", "undo": "Annuler", "keyboard": "Clavier",
    "enter": "Entrée", "save": "Enregistrer", "fnL": "Fn gauche", "fnR": "Fn droite",
    "a": "A", "b": "B", "c": "C", "d": "D", "e": "E",
    **{f"key{i}": f"Bouton rond {i + 1}" for i in range(8)},
    **{f"strip{s}{z}": f"Bandeau {'gauche' if s == 'L' else 'droit'} {z}"
       for s in "LR" for z in (1, 2, 3)},
}
STRIP_ZONES = 3  # chaque bandeau latéral est découpé en 3 zones tactiles affichables


# Nom court pour l'étiquette d'un contrôle libre, quand la place manque. Les molettes, les
# bandeaux et les boutons ronds se reconnaissent à leur forme et à leur place : on n'y écrit rien
# plutôt que d'afficher un numéro qu'on prendrait pour un libellé.
CONTROL_SHORT = {"home": "Home", "undo": "Annul.", "keyboard": "Clav.", "enter": "Entrée",
                 "save": "Save", "fnL": "Fn G", "fnR": "Fn D",
                 **{c: c.upper() for c in "abcde"}}


def short_label(cid: str) -> str:
    return CONTROL_SHORT.get(cid, "")
ROTARIES = frozenset(_CT_KNOBS) | {"wheel"}


def control_kind(cid: str) -> str:
    """« rotary » (rotation + appui) ou « button » (appui seul)."""
    return "rotary" if cid in ROTARIES else "button"


def is_round(cid: str) -> bool:
    """Un des 8 boutons ronds sous le pavé tactile (key0..key7) — et non « keyboard »."""
    return cid.startswith("key") and cid[3:].isdigit()


def has_led(cid: str) -> bool:
    """Bouton à diode RGB. Loupedeck annonce « 20 boutons RGB » sur le CT : les 8 ronds sous le
    pavé et les 12 du bas. Les molettes et la molette centrale n'en ont pas."""
    return cid in BUTTON_CODES and cid not in ROTARIES


def strip_zone(side: str, y: int, height: int = 270) -> str:
    """Identifiant de la zone d'un bandeau touchée à l'ordonnée y."""
    z = min(STRIP_ZONES - 1, max(0, y * STRIP_ZONES // height))
    return f"strip{side}{z + 1}"


def controls(profile: "Profile") -> list[dict]:
    """Contrôles configurables d'un modèle, dans l'ordre où l'interface les présente.

    kind « rotary » : rotation (deux sens) ET appui. kind « button » : appui seul.
    screen : zone d'écran associée, quand le contrôle peut afficher quelque chose."""
    out = []
    for cid in profile.knobs:
        out.append({"id": cid, "kind": "rotary", "label": CONTROL_LABELS.get(cid, cid), "group": "Molettes"})
    if profile.wheel:
        out.append({"id": "wheel", "kind": "rotary", "label": CONTROL_LABELS["wheel"],
                    "group": "Molettes", "screen": "wheel"})
    for side in ("L", "R"):
        if ("left" if side == "L" else "right") not in profile.displays:
            continue
        for z in range(1, STRIP_ZONES + 1):
            cid = f"strip{side}{z}"
            out.append({"id": cid, "kind": "button", "label": CONTROL_LABELS[cid],
                        "group": "Bandeaux", "screen": "left" if side == "L" else "right", "zone": z})
    for cid in profile.buttons:
        # Attention : « keyboard » commence aussi par « key » sans être un bouton rond.
        out.append({"id": cid, "kind": "button", "label": CONTROL_LABELS.get(cid, cid),
                    "group": "Boutons ronds" if is_round(cid) else "Boutons",
                    "led": has_led(cid)})
    return out
# Le CT V2 adresse les trois zones de la dalle avant comme un seul écran « M » (480×270)
# et la molette centrale comme un écran « W » séparé, en gros-boutiste.
_LIVE_DISPLAYS = {
    "center": {"id": b"\x00M", "width": 360, "height": 270, "offset": (60, 0)},
    "left": {"id": b"\x00M", "width": 60, "height": 270, "offset": (0, 0)},
    "right": {"id": b"\x00M", "width": 60, "height": 270, "offset": (420, 0)},
}
_WHEEL_DISPLAY = {"id": b"\x00W", "width": 240, "height": 240, "offset": (0, 0), "big_endian": True}

PROFILES = {
    0x0007: Profile("Loupedeck CT", {**_LIVE_DISPLAYS, "wheel": _WHEEL_DISPLAY},
                    4, 3, 90, 60, _CT_BUTTONS, _CT_KNOBS, ("left", "right"), True),
    0x0003: Profile("Loupedeck CT (V1)",
                    {"center": {"id": b"\x00A", "width": 360, "height": 270, "offset": (0, 0)},
                     "left": {"id": b"\x00L", "width": 60, "height": 270, "offset": (0, 0)},
                     "right": {"id": b"\x00R", "width": 60, "height": 270, "offset": (0, 0)},
                     "wheel": _WHEEL_DISPLAY},
                    4, 3, 90, 0, _CT_BUTTONS, _CT_KNOBS, ("left", "right"), True),
    0x0004: Profile("Loupedeck Live", _LIVE_DISPLAYS, 4, 3, 90, 60,
                    ("key0", "key1", "key2", "key3", "key4", "key5", "key6", "key7"),
                    _CT_KNOBS, ("left", "right")),
    0x0006: Profile("Loupedeck Live S",
                    {"center": {"id": b"\x00M", "width": 480, "height": 270, "offset": (0, 0)}},
                    5, 3, 90, 15, ("key0", "key1", "key2", "key3"), ("knobTL", "knobCL")),
    0x0D06: Profile("Razer Stream Controller", _LIVE_DISPLAYS, 4, 3, 90, 60,
                    ("key0", "key1", "key2", "key3", "key4", "key5", "key6", "key7"),
                    _CT_KNOBS, ("left", "right")),
    0x0D09: Profile("Razer Stream Controller X",
                    {"center": {"id": b"\x00M", "width": 480, "height": 288, "offset": (0, 0)}},
                    5, 3, 96, 0, (), ()),
}


# -- conversion d'images -----------------------------------------------------
_R5 = tuple((v & 0xF8) << 8 for v in range(256))
_G6 = tuple((v & 0xFC) << 3 for v in range(256))
_B5 = tuple(v >> 3 for v in range(256))


def rgb565(img: Image.Image, big_endian: bool = False) -> bytes:
    """Image Pillow -> tampon 16 bits 5-6-5, petit-boutiste sauf pour l'écran de la molette."""
    raw = img.convert("RGB").tobytes()
    n = len(raw) // 3
    out = bytearray(n * 2)
    r5, g6, b5 = _R5, _G6, _B5
    if big_endian:
        for i in range(n):
            j = i * 3
            v = r5[raw[j]] | g6[raw[j + 1]] | b5[raw[j + 2]]
            out[i * 2] = v >> 8
            out[i * 2 + 1] = v & 0xFF
    else:
        for i in range(n):
            j = i * 3
            v = r5[raw[j]] | g6[raw[j + 1]] | b5[raw[j + 2]]
            out[i * 2] = v & 0xFF
            out[i * 2 + 1] = v >> 8
    return bytes(out)


# -- découpage du flux -------------------------------------------------------
def split_frames(buf: bytearray) -> list[bytes]:
    """Extrait les messages complets de `buf` (modifié sur place) ; garde le reste."""
    out = []
    while True:
        i = buf.find(MAGIC)
        if i < 0 or len(buf) < i + 2:
            break
        n = buf[i + 1]
        if len(buf) < i + 2 + n:
            break
        out.append(bytes(buf[i + 2:i + 2 + n]))
        del buf[:i + 2 + n]
    return out


def discover() -> list[dict]:
    """Ports série correspondant à un appareil Loupedeck ou Razer."""
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    found = []
    for p in list_ports.comports():
        vid, pid = p.vid, p.pid
        if vid is None or vid not in VENDOR_IDS or pid not in PROFILES:
            continue
        found.append({"path": p.device, "vid": vid, "pid": pid,
                      "serial": p.serial_number or p.device,
                      "model": PROFILES[pid].name})
    return found


class Loupedeck:
    """Un appareil ouvert. `on_event` reçoit des dictionnaires décrivant les actions physiques."""

    def __init__(self, path: str, pid: int, serial: str = "", on_event=None, on_close=None):
        self.path, self.pid = path, pid
        self.profile = PROFILES[pid]
        self.model = self.profile.name
        self.serial = serial or path
        self.firmware = ""
        self.on_event = on_event
        self.on_close = on_close
        self._port = None
        self._tid = 0
        self._buf = bytearray()
        self._touches: dict[int, dict] = {}
        self._stop = threading.Event()
        self._send_lock = threading.Lock()
        self._reader: threading.Thread | None = None

    # -- connexion ------------------------------------------------------------
    def connect(self, timeout: float = 2.0) -> None:
        import serial
        self._port = serial.Serial(self.path, baudrate=BAUD, timeout=0.05, write_timeout=5)
        try:
            self._handshake(timeout)
        except Exception:
            self.close()  # sinon le port reste ouvert et la tentative suivante se voit refuser l'accès
            raise
        self._stop.clear()
        self._reader = threading.Thread(target=self._read_loop, name=f"loupedeck-{self.serial}", daemon=True)
        self._reader.start()
        self._send(CMD_SERIAL)
        self._send(CMD_VERSION)

    def _handshake(self, timeout: float) -> None:
        """Poignée de main « WebSocket » sur le port série.

        Une session précédente mal refermée laisse des acquittements et parfois une trame de
        fermeture dans le tampon : la réponse HTTP n'est alors PAS en tête du flux. On vide donc
        les tampons, puis on cherche le marqueur n'importe où en jetant ce qui le précède."""
        self._port.reset_input_buffer()
        self._port.reset_output_buffer()
        self._port.write(WS_UPGRADE)
        deadline = time.monotonic() + timeout
        head = bytearray()
        while time.monotonic() < deadline:
            head += self._port.read(256)
            start = head.find(WS_RESPONSE)
            if start < 0:
                if len(head) > 4096:  # ce n'est visiblement pas un Loupedeck
                    raise OSError(f"poignée de main refusée : {bytes(head[:40])!r}")
                continue
            end = head.find(b"\r\n\r\n", start)
            if end < 0:
                continue  # en-têtes encore incomplets
            # Le binaire commence après la ligne vide ; ce qui précédait le marqueur est du rebut.
            self._buf = bytearray(head[end + 4:])
            return
        raise OSError("poignée de main sans réponse")

    @property
    def connected(self) -> bool:
        return bool(self._port and self._port.is_open and not self._stop.is_set())

    def close(self) -> None:
        self._stop.set()
        port, self._port = self._port, None
        if port:
            try:
                port.write(WS_CLOSE)
            except Exception:
                pass
            try:
                port.close()
            except Exception:
                pass

    # -- envoi ----------------------------------------------------------------
    def _send(self, cmd: int, data: bytes = b"") -> None:
        port = self._port
        if not port or not port.is_open:
            return
        with self._send_lock:
            self._tid = self._tid % 255 + 1  # le 0 est ignoré par l'appareil
            body = bytes([min(3 + len(data), 0xFF), cmd, self._tid]) + data
            if len(body) > 0xFF:
                prep = bytearray(14)
                prep[0], prep[1] = MAGIC, 0xFF
                struct.pack_into(">I", prep, 6, len(body))
            else:
                prep = bytearray(6)
                prep[0], prep[1] = MAGIC, 0x80 + len(body)
            try:
                port.write(bytes(prep) + body)
            except Exception as e:
                log.warning("Loupedeck %s : écriture impossible (%s)", self.serial, e)
                self._fail()

    def _fail(self) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        cb = self.on_close
        if cb:
            try:
                cb(self)
            except Exception:
                log.exception("Loupedeck : rappel de déconnexion")

    # -- réception ------------------------------------------------------------
    def _read_loop(self) -> None:
        while not self._stop.is_set():
            port = self._port
            if not port:
                break
            try:
                chunk = port.read(4096)
            except Exception:
                self._fail()
                break
            if not chunk:
                continue
            self._buf += chunk
            for msg in split_frames(self._buf):
                try:
                    self._dispatch(msg)
                except Exception:  # un message malformé ne doit pas tuer le thread
                    log.exception("Loupedeck : message %s", msg.hex())

    def _dispatch(self, msg: bytes) -> None:
        if len(msg) < 3:
            return
        length, cmd = msg[0], msg[1]
        data = msg[3:length] if 3 <= length <= len(msg) else msg[3:]
        if cmd == CMD_SERIAL:
            self.serial = data.decode("ascii", "replace").strip() or self.serial
        elif cmd == CMD_VERSION and len(data) >= 3:
            self.firmware = ".".join(str(b) for b in data[:3])
        elif cmd == CMD_BUTTON and len(data) >= 2:
            self._emit({"type": "button", "id": BUTTON_IDS.get(data[0], f"0x{data[0]:02x}"),
                        "down": data[1] == 0x00})
        elif cmd == CMD_ROTATE and len(data) >= 2:
            self._emit({"type": "rotate", "id": BUTTON_IDS.get(data[0], f"0x{data[0]:02x}"),
                        "delta": struct.unpack("b", data[1:2])[0]})
        elif cmd in (CMD_TOUCH, CMD_TOUCH_CT, CMD_TOUCH_END, CMD_TOUCH_END_CT) and len(data) >= 6:
            self._touch(cmd, data)

    def _touch(self, cmd: int, data: bytes) -> None:
        x, y = struct.unpack(">HH", data[1:5])
        finger = data[5]
        ending = cmd in (CMD_TOUCH_END, CMD_TOUCH_END_CT)
        wheel = cmd in (CMD_TOUCH_CT, CMD_TOUCH_END_CT)
        target = {"screen": "wheel"} if wheel else self.target(x, y)
        evt = {"type": "touch", "x": x, "y": y, "finger": finger, "target": target}
        if ending:
            self._touches.pop(finger, None)
            evt["phase"] = "end"
        else:
            evt["phase"] = "move" if finger in self._touches else "start"
            self._touches[finger] = evt
        self._emit(evt)

    def target(self, x: int, y: int) -> dict:
        """Zone de la dalle avant touchée : bandeau gauche, touche du pavé, bandeau droit."""
        p = self.profile
        left = p.displays.get("left", {}).get("width", 0)
        if "left" in p.displays and x < left:
            return {"screen": "left"}
        right_x = left + p.displays["center"]["width"]
        if "right" in p.displays and x >= right_x:
            return {"screen": "right"}
        col = (x - p.keys_x) // p.key_size
        row = y // p.key_size
        if not (0 <= col < p.cols and 0 <= row < p.rows):
            return {}
        return {"screen": "center", "key": int(row * p.cols + col), "row": int(row), "col": int(col)}

    def _emit(self, evt: dict) -> None:
        evt["serial"] = self.serial
        cb = self.on_event
        if not cb:
            return
        try:
            cb(evt)
        except Exception:
            log.exception("Loupedeck : traitement de %s", evt.get("type"))

    # -- sorties --------------------------------------------------------------
    def set_brightness(self, percent: int) -> None:
        level = max(0, min(MAX_BRIGHTNESS, round(percent / 100 * MAX_BRIGHTNESS)))
        self._send(CMD_BRIGHTNESS, bytes([level]))

    def set_button_color(self, button: str, color: tuple[int, int, int]) -> None:
        code = BUTTON_CODES.get(button)
        if code is None:
            raise ValueError(f"bouton inconnu : {button}")
        self._send(CMD_COLOR, bytes([code, color[0] & 0xFF, color[1] & 0xFF, color[2] & 0xFF]))

    def vibrate(self, pattern: str | int = "court") -> None:
        code = HAPTIC.get(pattern, pattern) if isinstance(pattern, str) else pattern
        self._send(CMD_VIBRATE, bytes([int(code) & 0xFF]))

    def draw(self, display: str, img: Image.Image, x: int = 0, y: int = 0, refresh: bool = True) -> None:
        """Dessine une image dans un écran (coordonnées relatives à cet écran)."""
        info = self.profile.displays.get(display)
        if not info:
            raise ValueError(f"écran absent de ce modèle : {display}")
        ox, oy = info["offset"]
        header = struct.pack(">HHHH", x + ox, y + oy, img.width, img.height)
        self._send(CMD_FRAMEBUFF, info["id"] + header + rgb565(img, info.get("big_endian", False)))
        if refresh:
            self._send(CMD_DRAW, info["id"])

    def draw_key(self, index: int, img: Image.Image, refresh: bool = True) -> None:
        p = self.profile
        if not 0 <= index < p.keys:
            raise ValueError(f"touche hors grille : {index}")
        row, col = divmod(index, p.cols)
        self.draw("center", img, x=p.keys_x - p.displays["center"]["offset"][0] + col * p.key_size,
                  y=row * p.key_size, refresh=refresh)

    def refresh(self, display: str) -> None:
        info = self.profile.displays.get(display)
        if info:
            self._send(CMD_DRAW, info["id"])

    def panels(self) -> dict[bytes, tuple[int, int]]:
        """Dalles physiques : identifiant -> (largeur, hauteur). Plusieurs écrans logiques
        peuvent partager la même dalle (centre et bandeaux du CT V2)."""
        out: dict[bytes, tuple[int, int]] = {}
        for info in self.profile.displays.values():
            ox, oy = info["offset"]
            w, h = out.get(info["id"], (0, 0))
            out[info["id"]] = (max(w, ox + info["width"]), max(h, oy + info["height"]))
        return out

    def clear(self, color: tuple[int, int, int] = (0, 0, 0)) -> None:
        """Éteint tous les écrans et toutes les diodes des boutons ronds."""
        for disp_id, (w, h) in self.panels().items():
            big = any(i.get("big_endian") for i in self.profile.displays.values() if i["id"] == disp_id)
            img = Image.new("RGB", (w, h), color)
            self._send(CMD_FRAMEBUFF, disp_id + struct.pack(">HHHH", 0, 0, w, h) + rgb565(img, big))
            self._send(CMD_DRAW, disp_id)
        for b in self.profile.buttons:
            try:
                self.set_button_color(b, (0, 0, 0))
            except ValueError:
                pass
