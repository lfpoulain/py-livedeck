"""Commandes réseau génériques : trame TCP / UDP brute et message OSC (encodeur maison, sans dépendance).

Données d'une trame : texte avec échappements \\r \\n \\t \\0 \\\\ \\xNN (ex. « PWR ON\\r »), ou octets en
hexadécimal (« 81 01 04 00 02 FF », espaces, « : » et « 0x » tolérés).

Arguments OSC, séparés par des espaces : 1 (entier), 0.5 (réel), true / false, "texte avec espaces",
ou forcés par un préfixe i: f: s: (ex. s:12 pour envoyer le texte « 12 »).
"""
from __future__ import annotations

import re
import shlex
import socket
import struct

_ESC = {"r": "\r", "n": "\n", "t": "\t", "0": "\0", "\\": "\\", "'": "'", '"': '"'}


def decode_payload(text: str, hex_mode: bool = False) -> bytes:
    """Texte saisi -> octets envoyés. Lève ValueError (message en français) si la saisie est invalide."""
    text = text or ""
    if hex_mode:
        clean = re.sub(r"0x|[\s:,\-]", "", text, flags=re.I)
        if len(clean) % 2 or not re.fullmatch(r"[0-9a-fA-F]*", clean):
            raise ValueError("hexadécimal invalide (attendu : paires de chiffres 0-9 A-F, ex. « 81 01 FF »)")
        return bytes.fromhex(clean)
    out = bytearray()
    i = 0
    while i < len(text):
        c = text[i]
        if c == "\\" and i + 1 < len(text):
            n = text[i + 1]
            if n in ("x", "X") and re.fullmatch(r"[0-9a-fA-F]{2}", text[i + 2:i + 4]):
                out.append(int(text[i + 2:i + 4], 16))
                i += 4
                continue
            if n in _ESC:
                out += _ESC[n].encode()
                i += 2
                continue
        out += c.encode("utf-8")
        i += 1
    return bytes(out)


def send(proto: str, host: str, port: int, data: bytes, timeout: float = 2.0) -> None:
    """Envoie une trame. Lève OSError si l'appareil est injoignable (TCP) ou l'adresse invalide."""
    host = (host or "").strip()
    if not host:
        raise ValueError("adresse non renseignée")
    port = int(port or 0)
    if not 0 < port < 65536:
        raise ValueError("port invalide")
    if (proto or "tcp").lower() == "udp":
        try:  # IPv4 d'abord : « localhost » donne souvent ::1, que les récepteurs OSC n'écoutent pas
            info = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_DGRAM)[0]
        except socket.gaierror:
            info = socket.getaddrinfo(host, port, type=socket.SOCK_DGRAM)[0]
        with socket.socket(info[0], socket.SOCK_DGRAM) as s:
            if host.endswith(".255") or host == "255.255.255.255":
                s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.sendto(data, info[4])
        return
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.sendall(data)


# --- OSC 1.0 --------------------------------------------------------------------------------------

def _osc_str(s: str) -> bytes:
    b = s.encode("utf-8") + b"\0"
    return b + b"\0" * (-len(b) % 4)


def parse_osc_args(text: str) -> list:
    """« 1 0.5 true "mon texte" s:12 » -> [1, 0.5, True, "mon texte", "12"]."""
    try:
        tokens = shlex.split(text or "", posix=False)
    except ValueError as e:
        raise ValueError(f"arguments OSC invalides ({e})") from None
    out: list = []
    for t in tokens:
        if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'":
            out.append(t[1:-1])
        elif t[:2] in ("i:", "f:", "s:"):
            kind, val = t[0], t[2:]
            try:
                out.append(int(val) if kind == "i" else float(val) if kind == "f" else val)
            except ValueError:
                raise ValueError(f"argument OSC « {t} » : valeur invalide") from None
        elif t.lower() in ("true", "false"):
            out.append(t.lower() == "true")
        elif re.fullmatch(r"[-+]?\d+", t):
            out.append(int(t))
        elif re.fullmatch(r"[-+]?(\d+\.\d*|\.\d+|\d+)([eE][-+]?\d+)?", t):
            out.append(float(t))
        else:
            out.append(t)
    return out


def osc_message(address: str, args: list) -> bytes:
    """Encode un message OSC (types i, f, s, T, F)."""
    address = (address or "").strip()
    if not address.startswith("/"):
        raise ValueError("l'adresse OSC doit commencer par « / » (ex. /go)")
    tags, body = ",", b""
    for a in args:
        if isinstance(a, bool):
            tags += "T" if a else "F"
        elif isinstance(a, int):
            if not -2 ** 31 <= a < 2 ** 31:
                raise ValueError(f"entier OSC hors limites : {a}")
            tags += "i"
            body += struct.pack(">i", a)
        elif isinstance(a, float):
            tags += "f"
            body += struct.pack(">f", a)
        else:
            tags += "s"
            body += _osc_str(str(a))
    return _osc_str(address) + _osc_str(tags) + body
