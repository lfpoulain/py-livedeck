"""Textes dynamiques sur les touches : jetons façon Companion, remplacés en direct.

Un libellé peut mélanger texte fixe et jetons, ex. « CLIP\\n$(vmix:remaining:Magneto) ».
L'input se désigne comme partout ailleurs : numéro, titre (approché) ou GUID.

vMix
  $(vmix:program_title)            titre de l'input à l'antenne ; $(vmix:program_title:2) pour le Mix 2
  $(vmix:preview_title)            titre de l'input en preview (idem :<mix>)
  $(vmix:program_number)           numéro de l'input à l'antenne (idem preview_number, :<mix>)
  $(vmix:input_title:<input>)      titre d'un input
  $(vmix:input_number:<input>)     numéro d'un input
  $(vmix:input_state:<input>)      état (Running, Paused…)
  $(vmix:remaining:<input>)        temps restant d'une vidéo / d'un son (m:ss, h:mm:ss au-delà d'une heure)
  $(vmix:position:<input>)         position de lecture ; $(vmix:duration:<input>) durée totale
  $(vmix:volume:<input>)           volume (0-100)
  $(vmix:text:<input>:<champ>)     texte d'un champ de titre : « Headline.Text », « Headline » ou son index « 0 »
  $(vmix:recording_duration)       durée de l'enregistrement (idem streaming_duration, external_duration…)
Horloge
  $(time) hh:mm:ss · $(time:hm) hh:mm · $(date) jj/mm/aaaa

Valeur indisponible (vMix hors ligne, input introuvable) : « — ». Jeton inconnu : laissé tel quel.
Les touches ne sont redessinées que si le texte obtenu change (Engine.visual + cache du deck).
"""
from __future__ import annotations

import math
import time

NA = "—"


def has_tokens(text: str) -> bool:
    return "$(" in (text or "")


def tokens(text: str) -> list[tuple[int, int, str]]:
    """(début, fin, contenu) de chaque jeton $(…) ; les parenthèses imbriquées (« Cam (Large) ») sont gérées."""
    out, i = [], 0
    while True:
        i = text.find("$(", i)
        if i < 0:
            return out
        depth, j = 0, i + 1
        while j < len(text):
            if text[j] == "(":
                depth += 1
            elif text[j] == ")":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        if j >= len(text):
            return out  # jeton non refermé : laissé tel quel
        out.append((i, j + 1, text[i + 2:j]))
        i = j + 1


def clock(seconds, ceil: bool = False) -> str:
    """Durée en secondes -> « m:ss » ou « h:mm:ss »."""
    if seconds is None:
        return NA
    s = max(0, math.ceil(seconds) if ceil else int(seconds))
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _mix(args: list[str]) -> int:
    try:
        return max(1, int(args[0])) if args else 1
    except ValueError:
        return 1


def _vmix(engine, name: str, args: list[str]) -> str | None:
    st = engine.vmix.state
    if name in ("program_title", "preview_title", "program_number", "preview_number"):
        if not st.connected:
            return NA
        mix = _mix(args)
        n = st.program(mix) if name.startswith("program") else st.preview(mix)
        if n is None:
            return NA
        return str(n) if name.endswith("number") else (st.title(n) or NA)
    if name.endswith("_duration") and not args:  # recording_duration, streaming_duration…
        what = name[:-len("_duration")].lower()
        if not st.connected or what not in st.durations:
            return NA
        return clock(st.durations[what])
    if name == "text":
        if len(args) < 2:
            return None
        ref, field = ":".join(args[:-1]), args[-1]
        i = st.resolve(ref) if st.connected else None
        if not i:
            return NA
        texts = i.get("texts") or {}
        for k in (field, f"{field}.Text", "#" + field):
            if k in texts:
                return texts[k]
        return NA
    ref = ":".join(args)
    if name not in ("input_title", "input_number", "input_state", "remaining", "position", "duration", "volume"):
        return None
    i = st.resolve(ref) if st.connected and ref else None
    if not i:
        return NA
    if name == "input_title":
        return i["title"]
    if name == "input_number":
        return str(i["number"])
    if name == "input_state":
        return i.get("state") or NA
    if name == "volume":
        return NA if i.get("volume") is None else f"{i['volume']:.0f}"
    dur, pos = i.get("duration"), i.get("position")
    if name == "duration":
        return clock(dur / 1000) if dur else NA
    if name == "position":
        return clock(pos / 1000) if pos is not None else NA
    if not dur or pos is None:  # remaining
        return NA
    return clock((dur - pos) / 1000, ceil=True)


def value(engine, body: str) -> str | None:
    """Valeur d'un jeton (contenu entre les parenthèses), ou None s'il est inconnu."""
    ns, _, rest = body.partition(":")
    ns = ns.strip().lower()
    parts = rest.split(":") if rest else []
    if ns == "vmix" and parts:
        return _vmix(engine, parts[0].strip().lower(), [p.strip() for p in parts[1:]])
    if ns == "time":
        return time.strftime("%H:%M" if parts[:1] == ["hm"] else "%H:%M:%S")
    if ns == "date":
        return time.strftime("%d/%m/%Y")
    return None


def expand(text: str, engine) -> str:
    """Remplace les jetons $(…) d'un libellé par leur valeur actuelle."""
    if not has_tokens(text):
        return text
    out, last = [], 0
    for start, end, body in tokens(text):
        try:
            v = value(engine, body)
        except Exception:  # un jeton bizarre ne doit jamais casser l'affichage d'une touche
            v = NA
        out += [text[last:start], text[start:end] if v is None else v]
        last = end
    out.append(text[last:])
    return "".join(out)
