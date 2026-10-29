"""Client vMix via l'API TCP (port 8099).

- SUBSCRIBE TALLY / ACTS : retours d'état poussés instantanément par vMix
- XML interrogé en continu : liste des inputs, mixes, overlays, enregistrement…
- FUNCTION : envoi des commandes sans attendre la réponse (latence minimale)

État « optimiste » : juste après l'envoi d'un PreviewInput / CutDirect / d'une transition, LiveDeck
note ce qu'il attend (expect) et l'affiche tout de suite. ACTS ou le XML suivant le confirment ; une
attente non confirmée expire d'elle-même (1 s, plus la durée de la transition). Un 2e appui rapide sur
une touche Take voit donc déjà l'input en preview, même si vMix n'a pas encore répondu.

vMix figé : si le XML ne revient plus pendant STALE_AFTER secondes alors que la connexion TCP reste
ouverte, la connexion est déclarée perdue et refaite (les touches ne montrent plus un état périmé).
"""
from __future__ import annotations

import collections
import logging
import re
import socket
import threading
import time
import unicodedata
import urllib.parse
import xml.etree.ElementTree as ET

log = logging.getLogger("livedeck")

POLL_INTERVAL = 0.3
STALE_AFTER = 3.5  # s sans XML complet : vMix considéré comme figé
FLAG_TAGS = ("recording", "streaming", "external", "multiCorder", "fadeToBlack", "playList")


def norm(s: str) -> str:
    """Forme comparable d'un nom d'input : sans accents, minuscules, alphanumérique."""
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", s.lower())


class VmixState:
    def __init__(self):
        self.connected = False
        self.version = ""
        self.inputs: list[dict] = []
        self.mixes: dict[int, dict] = {1: {"active": None, "preview": None}}
        self.overlays: dict[int, dict] = {i: {"program": None, "preview": None} for i in range(1, 9)}
        self.flags: dict[str, bool] = {t.lower(): False for t in FLAG_TAGS}
        self.durations: dict[str, int] = {}  # recording / streaming… : secondes écoulées (si vMix les donne)
        self.transitions: list[dict] = []  # boutons de transition 1-4 : {"effect", "duration"}
        self._by_number: dict[int, dict] = {}
        self._by_key: dict[str, dict] = {}
        self._by_title: dict[str, dict] = {}
        self._by_norm: dict[str, dict] = {}

    def set_inputs(self, inputs: list[dict]) -> None:
        self.inputs = inputs
        self._by_number = {i["number"]: i for i in inputs}
        self._by_key = {i["key"].lower(): i for i in inputs}
        by_title, by_norm = {}, {}
        for i in inputs:
            for t in (i["title"], i["short"]):
                by_title.setdefault(t.lower(), i)
                by_norm.setdefault(norm(t), i)
        self._by_title, self._by_norm = by_title, by_norm

    def resolve(self, ref) -> dict | None:
        """Retrouve un input par numéro, GUID, titre exact ou titre approché."""
        ref = str(ref or "").strip()
        if not ref:
            return None
        if ref.isdigit():
            return self._by_number.get(int(ref))
        low = ref.lower()
        return self._by_key.get(low) or self._by_title.get(low) or self._by_norm.get(norm(ref))

    def number(self, ref) -> int | None:
        i = self.resolve(ref)
        return i["number"] if i else None

    def program(self, mix: int = 1) -> int | None:
        return self.mixes.get(mix, {}).get("active")

    def preview(self, mix: int = 1) -> int | None:
        return self.mixes.get(mix, {}).get("preview")

    def title(self, n) -> str:
        i = self._by_number.get(n) if n is not None else None
        return i["title"] if i else ""


# séparateur de paramètres : « & », ou un espace suivi de « Clé= » (les valeurs gardent leurs espaces)
PARAM_SPLIT = r"&|\s+(?=[A-Za-z][A-Za-z0-9_]*=)"


def _encode_query(query: str) -> str:
    parts = []
    for part in re.split(PARAM_SPLIT, query.strip()):
        if not part:
            continue
        k, sep, v = part.partition("=")
        parts.append(f"{k.strip()}={urllib.parse.quote(urllib.parse.unquote(v.strip()), safe='')}" if sep else k.strip())
    return "&".join(parts)


class VmixClient:
    def __init__(self, on_change, on_log):
        self.host = "127.0.0.1"
        self.port = 8099
        self.state = VmixState()
        self._on_change = on_change
        self._log = on_log
        self._sock: socket.socket | None = None
        self._send_lock = threading.Lock()
        self._stop = threading.Event()
        self._xml_due = 0.0
        self._sig = None
        self._thread: threading.Thread | None = None
        self._pending: collections.deque = collections.deque(maxlen=64)  # (t0, nom, paramètres)
        self._expect: dict[tuple, tuple] = {}  # (mix, "active"|"preview") -> (valeur, échéance)
        self._exp_lock = threading.Lock()
        self.latency_ms: float | None = None  # aller-retour moyen d'une commande
        self.xml_ms: float | None = None  # temps moyen pour obtenir l'état complet
        self.stats = {"cmd": 0, "xml": 0}
        self._xml_sent = 0.0

    # -- cycle de vie ---------------------------------------------------------
    def start(self, host: str, port: int) -> None:
        self.host, self.port = host, int(port)
        self._thread = threading.Thread(target=self._run, name="vmix", daemon=True)
        self._thread.start()

    def reconfigure(self, host: str, port: int) -> None:
        self.host, self.port = host, int(port)
        self._drop()

    def stop(self) -> None:
        self._stop.set()
        self._drop()

    def _drop(self) -> None:
        s = self._sock
        if s:
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def _notify(self) -> None:
        """Une erreur chez l'abonné ne doit jamais tuer le thread vMix (plus de reconnexion sinon)."""
        try:
            self._on_change()
        except Exception:
            log.exception("vMix : traitement d'un changement d'état")

    def _set_connected(self, value: bool) -> None:
        if self.state.connected != value:
            self.state.connected = value
            self._log("info" if value else "warn",
                      f"vMix {'connecté' if value else 'déconnecté'} ({self.host}:{self.port})")
            self._notify()

    def _run(self) -> None:
        while not self._stop.is_set():
            host, port = self.host, self.port
            try:
                sock = socket.create_connection((host, port), timeout=2)
            except OSError:
                self._set_connected(False)
                self._stop.wait(2)
                continue
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            try:  # pair mort (câble, PC vMix planté) : détecté aussi par le système
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                if hasattr(socket, "SIO_KEEPALIVE_VALS"):
                    sock.ioctl(socket.SIO_KEEPALIVE_VALS, (1, 3000, 1000))
            except (OSError, AttributeError, ValueError):
                pass
            self._sock = sock
            self._pending.clear()
            try:
                self._session(sock)
            except (OSError, ConnectionError, ValueError):
                pass
            except Exception:  # imprévu : on journalise et on se reconnecte
                log.exception("vMix : session interrompue")
            finally:
                self._sock = None
                try:
                    sock.close()
                except OSError:
                    pass
                self._set_connected(False)
            self._stop.wait(1)

    def _session(self, sock: socket.socket) -> None:
        sock.settimeout(0.05)
        self._raw_send(b"SUBSCRIBE TALLY\r\nSUBSCRIBE ACTS\r\nXML\r\n")
        xml_pending, last_req = True, time.monotonic()
        last_xml = last_req
        buf = b""
        while not self._stop.is_set():
            now = time.monotonic()
            due = (self._xml_due and now >= self._xml_due) or now - last_req >= POLL_INTERVAL
            if now - last_xml > STALE_AFTER:  # le socket reste ouvert mais vMix ne répond plus
                if self.state.connected:
                    self._log("warn", f"vMix ne répond plus depuis {STALE_AFTER:.0f} s — reconnexion")
                raise ConnectionError("vMix muet")
            if xml_pending and now - last_req > 1.5:
                xml_pending = False  # réponse perdue : on relance
            if due and not xml_pending:
                self._xml_sent = time.perf_counter()
                self._raw_send(b"XML\r\n")
                xml_pending, last_req, self._xml_due = True, now, 0.0
            try:
                data = sock.recv(262144)
            except socket.timeout:
                continue
            if not data:
                raise ConnectionError("fermé par vMix")
            buf += data
            while True:
                idx = buf.find(b"\r\n")
                if idx < 0:
                    break
                line = buf[:idx].decode("utf-8", "replace")
                if line.startswith("XML "):
                    n = int(line[4:].strip() or 0)
                    if len(buf) < idx + 2 + n:
                        break  # attendre la suite du document
                    payload, buf = buf[idx + 2: idx + 2 + n], buf[idx + 2 + n:]
                    xml_pending, last_xml = False, time.monotonic()
                    if self._xml_sent:
                        ms = (time.perf_counter() - self._xml_sent) * 1000
                        self.xml_ms = ms if self.xml_ms is None else self.xml_ms * 0.9 + ms * 0.1
                        self._xml_sent = 0.0
                    self.stats["xml"] += 1
                    self._parse_xml(payload)
                    continue
                buf = buf[idx + 2:]
                self._handle_line(line)

    # -- réception ------------------------------------------------------------
    def _handle_line(self, line: str) -> None:
        if not line:
            return
        parts = line.split(" ")
        head = parts[0]
        # « connecté » n'est déclaré qu'à réception d'un XML complet (_parse_xml) : un vMix figé qui
        # accepte encore la connexion TCP ne passe jamais pour connecté.
        if head == "VERSION" and len(parts) >= 3:
            self.state.version = parts[2]
        elif head == "ACTS" and len(parts) >= 4 and parts[1] == "OK":
            self._handle_act(parts[2:])
        elif head == "FUNCTION":
            sent = self._pending.popleft() if self._pending else None
            if sent:
                rtt = (time.perf_counter() - sent[0]) * 1000
                self.latency_ms = rtt if self.latency_ms is None else self.latency_ms * 0.8 + rtt * 0.2
            if len(parts) >= 2 and parts[1] == "ER":
                what = (f"« {sent[1]}{' ' + urllib.parse.unquote(sent[2]) if sent[2] else ''} »"
                        if sent else "la commande")
                self._log("error", f"vMix a refusé {what} : " + " ".join(parts[2:]))
            # vMix répond après avoir exécuté la fonction : on relit l'état complet tout de suite
            # (Mix 2+, audio, overlays en preview… n'ont pas d'ACTS)
            self._xml_due = time.monotonic() + 0.01

    def _handle_act(self, args: list[str]) -> None:
        name = args[0]
        try:
            num, on = int(args[1]), args[-1] == "1"
        except (ValueError, IndexError):
            num, on = None, False
        st = self.state
        changed = False
        if name in ("Input", "InputPreview") and on:
            field = "active" if name == "Input" else "preview"
            with self._exp_lock:
                exp = self._expect.get((1, field))
                if exp and exp[0] == num:
                    del self._expect[(1, field)]  # confirmé
                if not exp or exp[0] == num or time.monotonic() > exp[1]:
                    # (sinon : ACTS d'une commande précédente, l'attente en cours reste prioritaire)
                    st.mixes.setdefault(1, {"active": None, "preview": None})[field], changed = num, True
        elif name.startswith("Overlay") and name[7:].isdigit():
            ov = st.overlays.setdefault(int(name[7:]), {"program": None, "preview": None})
            if on:
                ov["program"], changed = num, True
            elif ov["program"] == num:
                ov["program"], changed = None, True
        if changed:
            self._notify()
        self._xml_due = time.monotonic() + 0.02

    def _parse_xml(self, payload: bytes) -> None:
        try:
            root = ET.fromstring(payload.decode("utf-8", "replace").strip())
        except ET.ParseError:
            return
        st = self.state
        def num(el):
            try:
                return int(el.text) if el is not None and el.text else None
            except ValueError:
                return None

        def integer(v):
            try:
                return int(float(v)) if v not in (None, "") else None
            except ValueError:
                return None

        inputs = []
        for el in root.iter("input"):
            if el.get("number") is None:
                continue
            texts = {}
            for t in el.findall("text"):  # champs d'un titre (GT) : <text index="0" name="Headline.Text">
                texts[t.get("name") or t.get("index") or str(len(texts))] = t.text or ""
                if t.get("index") is not None:
                    texts.setdefault("#" + t.get("index"), t.text or "")
            try:
                volume = float(el.get("volume")) if el.get("volume") not in (None, "") else None
            except ValueError:
                volume = None
            inputs.append({
                "number": int(el.get("number")),
                "key": el.get("key", ""),
                "title": el.get("title", ""),
                "short": el.get("shortTitle", el.get("title", "")),
                "type": el.get("type", ""),
                "state": el.get("state", ""),
                "muted": None if el.get("muted") is None else el.get("muted", "").lower() == "true",
                "duration": integer(el.get("duration")),  # ms
                "position": integer(el.get("position")),  # ms
                "loop": (el.get("loop") or "").lower() == "true",
                "volume": volume,
                "texts": texts,
            })

        mixes = {1: {"active": num(root.find("active")), "preview": num(root.find("preview"))}}
        for m in root.findall("mix"):
            try:
                mixes[int(m.get("number"))] = {"active": num(m.find("active")), "preview": num(m.find("preview"))}
            except (TypeError, ValueError):
                pass
        overlays = {i: {"program": None, "preview": None} for i in range(1, 9)}
        ov_root = root.find("overlays")
        for o in (ov_root if ov_root is not None else []):
            try:
                n = int(o.get("number"))
            except (TypeError, ValueError):
                continue
            slot = overlays.setdefault(n, {"program": None, "preview": None})
            slot["preview" if o.get("preview", "").lower() == "true" else "program"] = num(o)
        flags, durations = {}, {}
        for tag in FLAG_TAGS:
            el = root.find(tag)
            flags[tag.lower()] = el is not None and (el.text or "").strip().lower() == "true"
            if el is not None and el.get("duration") is not None:
                durations[tag.lower()] = integer(el.get("duration")) or 0
        transitions = [{"effect": t.get("effect", ""), "duration": integer(t.get("duration")) or 0}
                       for t in root.findall("transitions/transition")]
        version = (root.findtext("version") or st.version).strip()
        mixes = self._apply_expect(mixes)

        sig = (tuple((i["number"], i["key"], i["title"], i["state"], i["muted"], tuple(sorted(i["texts"].items())))
                     for i in inputs),
               tuple(sorted((k, v["active"], v["preview"]) for k, v in mixes.items())),
               tuple(sorted((k, v["program"], v["preview"]) for k, v in overlays.items())),
               tuple(sorted(flags.items())))
        st.version = version
        # position, volume, durées : relues à chaque XML (textes dynamiques) mais sans tout redessiner
        st.durations, st.transitions = durations, transitions
        st.set_inputs(inputs)
        if sig != self._sig:
            self._sig = sig
            st.mixes, st.overlays, st.flags = mixes, overlays, flags
            self._notify()
        self._set_connected(True)

    # -- état optimiste -------------------------------------------------------
    def expect(self, mix: int, ttl: float = 1.0, **fields) -> None:
        """Affiche tout de suite l'état attendu après une commande (active= / preview=, pour ce mix).

        ACTS ou le XML le confirment ; sans confirmation il expire après ttl secondes."""
        changed = False
        with self._exp_lock:
            end = time.monotonic() + ttl
            m = self.state.mixes.setdefault(mix, {"active": None, "preview": None})
            for k, v in fields.items():
                if v is None or k not in ("active", "preview"):
                    continue
                self._expect[(mix, k)] = (v, end)
                if m.get(k) != v:
                    m[k], changed = v, True
        if changed:
            self._notify()

    def _apply_expect(self, mixes: dict) -> dict:
        """Recouvre l'état lu dans le XML par les attentes encore valables ; oublie celles confirmées."""
        now = time.monotonic()
        with self._exp_lock:
            for (mix, k), (v, end) in list(self._expect.items()):
                got = mixes.get(mix, {}).get(k)
                if got == v or now > end:
                    del self._expect[(mix, k)]
                else:  # XML plus ancien que la commande : on garde ce qu'on attend
                    mixes.setdefault(mix, {"active": None, "preview": None})[k] = v
        return mixes

    def transition_ms(self, effect: str, duration: int = 0) -> int:
        """Durée probable d'une transition (pour l'attente optimiste) : celle demandée, sinon celle de vMix."""
        if duration:
            return duration
        effect = effect or "Cut"
        tr = self.state.transitions
        m = re.fullmatch(r"Transition(\d)", effect)
        if m and 1 <= int(m.group(1)) <= len(tr):
            return tr[int(m.group(1)) - 1]["duration"]
        if effect.lower() == "cut":
            return 0
        same = [t["duration"] for t in tr if t["effect"].lower() == effect.lower()]
        return same[0] if same else 1000

    # -- envoi ----------------------------------------------------------------
    def _raw_send(self, data: bytes) -> bool:
        sock = self._sock
        if not sock:
            return False
        with self._send_lock:
            try:
                sock.sendall(data)
                return True
            except OSError:
                return False

    def input_param(self, ref) -> str:
        """Valeur à passer à vMix pour un input : son GUID s'il est connu."""
        i = self.state.resolve(ref)
        return i["key"] if i and i["key"] else str(ref or "").strip()

    def function(self, name: str, **params) -> bool:
        query = "&".join(f"{k}={urllib.parse.quote(str(v), safe='')}"
                         for k, v in params.items() if v is not None and v != "")
        return self._send_function(name, query)

    def raw(self, command: str) -> bool:
        """Commande libre façon Companion : « Cut Mix=3 », « SetText&Input=Titre&Value=Bonjour »…"""
        command = command.strip()
        m = re.match(r"^([A-Za-z0-9_]+)[\s&?]*(.*)$", command)
        if not m:
            return False
        return self._send_function(m.group(1), _encode_query(m.group(2)))

    def _send_function(self, name: str, query: str) -> bool:
        line = f"FUNCTION {name}" + (f" {query}" if query else "")
        self.stats["cmd"] += 1
        self._pending.append((time.perf_counter(), name, query))  # monotonic() est trop grossier sous Windows
        if not self._raw_send((line + "\r\n").encode("utf-8")):
            self._pending.pop()
            self._log("warn", f"vMix non connecté — « {name} » non envoyé")
            return False
        return True
