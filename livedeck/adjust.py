"""Réglages au cran : ce qu'une molette tourne (volumes, lecture, preview…).

vMix n'a pas de commande relative pour le volume : « SetVolume » attend une valeur absolue 0-100.
On lit donc la valeur actuelle, on ajoute les crans, et on envoie le résultat.

Piège : vMix ne renvoie son état que plusieurs fois par seconde. Sur une rotation rapide, chaque
cran relirait la même valeur périmée et la plupart des crans seraient perdus. On garde donc la
dernière valeur envoyée pendant un court instant et on repart de celle-ci (« valeur optimiste »),
exactement comme pour l'affichage immédiat du program après une transition.
"""
from __future__ import annotations

import threading
import time

FRESH = 1.5  # s : durée pendant laquelle la dernière valeur envoyée prime sur celle lue

VMIX_TARGETS = [("input_volume", "Volume d'un input"), ("master", "Volume Master"),
                ("headphones", "Volume du casque"), ("bus", "Volume d'un bus"),
                ("position", "Avance / retour dans une vidéo ou un son"),
                ("preview", "Parcourir les inputs en preview"),
                ("list", "Élément suivant / précédent (liste, photos, PowerPoint)"),
                ("countdown", "Compte à rebours (ajouter / retirer)")]
BUSES = [(b, f"Bus {b}") for b in "ABCDEFG"]
# Valeur d'un cran par défaut, et son unité, pour chaque cible
VMIX_STEP = {"input_volume": (2, "%"), "master": (2, "%"), "headphones": (2, "%"), "bus": (2, "%"),
             "position": (1, "s"), "preview": (1, "input"), "list": (1, "élément"), "countdown": (10, "s")}
NEEDS_INPUT = ("input_volume", "position", "list", "countdown")

WIN_TARGETS = [("output", "Volume du son de Windows"), ("mic", "Micro par défaut"),
               ("app", "Une application (Spotify, navigateur…)")]
WIN_MODES = [("adjust", "Monter / baisser"), ("set", "Fixer à une valeur"), ("toggle", "Couper / rétablir"),
             ("mute", "Couper"), ("unmute", "Rétablir")]


class Levels:
    """Dernières valeurs envoyées, par cible, pour enchaîner les crans sans attendre vMix."""

    def __init__(self):
        self._v: dict[tuple, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def current(self, key: tuple, read):
        with self._lock:
            hit = self._v.get(key)
        if hit and time.monotonic() - hit[1] < FRESH:
            return hit[0]
        return read()

    def remember(self, key: tuple, value: float) -> None:
        with self._lock:
            self._v[key] = (value, time.monotonic())


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _fmt(v: float) -> str:
    return f"{round(v, 1):g}"


def vmix_value(engine, a: dict):
    """Valeur actuelle de la cible (pour l'affichage et la jauge), ou None si inconnue."""
    st = engine.vmix.state
    if not st.connected:
        return None
    t = a.get("target", "master")
    if t == "input_volume":
        i = st.resolve(a.get("input"))
        return i.get("volume") if i else None
    if t == "master":
        return (st.audio.get("master") or {}).get("volume")
    if t == "headphones":
        return (st.audio.get("master") or {}).get("headphones")
    if t == "bus":
        return (st.audio.get(a.get("bus", "A")) or {}).get("volume")
    return None


def vmix_adjust(engine, a: dict, delta: int) -> None:
    """Applique `delta` crans à la cible vMix de l'action."""
    from .actions import _input  # import tardif : actions importe ce module
    vm, st = engine.vmix, engine.vmix.state
    t = a.get("target", "master")
    amount = float(a.get("amount") if a.get("amount") not in (None, "") else VMIX_STEP.get(t, (1,))[0])
    change = amount * delta
    if not change:
        return

    if t in ("input_volume", "master", "headphones", "bus"):
        ref = None
        if t == "input_volume":
            got = _input(engine, a)
            if not got:
                return
            ref = got[0]
        key = ("vmix", t, a.get("bus", "") if t == "bus" else "", str(ref or ""))
        cur = engine.levels.current(key, lambda: vmix_value(engine, a))
        if cur is None:
            engine.log("warn", f"Réglage : volume actuel inconnu ({'vMix non connecté' if not st.connected else t})")
            return
        new = _clamp(cur + change)
        if new == cur:
            return
        name = {"input_volume": "SetVolume", "master": "SetMasterVolume",
                "headphones": "SetHeadphonesVolume"}.get(t) or f"SetBus{a.get('bus', 'A')}Volume"
        if vm.function(name, Input=vm.input_param(ref) if ref else None, Value=_fmt(new)):
            engine.levels.remember(key, new)
        return

    if t == "position":
        got = _input(engine, a)
        if not got:
            return
        ref = got[0]
        i = st.resolve(ref) if st.connected else None
        if not i or not i.get("duration"):
            engine.log("warn", f"Réglage : « {ref} » n'a pas de durée (ni vidéo ni son)")
            return
        key = ("vmix", "position", ref)
        cur = engine.levels.current(key, lambda: i.get("position") or 0)
        new = _clamp(cur + change * 1000, 0, float(i["duration"]))
        if vm.function("SetPosition", Input=vm.input_param(ref), Value=int(new)):
            engine.levels.remember(key, new)
        return

    if t == "preview":
        mix = int(a.get("mix") or 1)
        numbers = sorted(i["number"] for i in st.inputs) if st.connected else []
        if not numbers:
            engine.log("warn", "Réglage : liste des inputs vMix inconnue")
            return
        key = ("vmix", "preview", mix)
        cur = engine.levels.current(key, lambda: st.preview(mix))
        idx = numbers.index(cur) if cur in numbers else 0
        n = numbers[max(0, min(len(numbers) - 1, idx + int(change)))]  # bloqué aux extrémités, sans boucler
        if n == cur:
            return
        if vm.function("PreviewInput", Input=n, Mix=mix - 1 if mix > 1 else None):
            vm.expect(mix, preview=n)
            engine.levels.remember(key, n)
        return

    if t == "list":
        got = _input(engine, a)
        if not got:
            return
        name = "NextItem" if change > 0 else "PreviousItem"
        for _ in range(min(20, abs(int(change)))):
            vm.function(name, Input=vm.input_param(got[0]))
        return

    if t == "countdown":
        got = _input(engine, a)
        if got:
            vm.function("AdjustCountdown", Input=vm.input_param(got[0]), Value=int(change))


def win_audio(engine, a: dict, delta: int) -> None:
    """Son de Windows : sortie, micro ou application."""
    audio = engine.winaudio
    target, app = a.get("target", "output"), (a.get("app") or "").strip()
    who = {"output": "Son Windows", "mic": "Micro"}.get(target, app or "Application")
    if target == "app" and not app:
        engine.log("warn", "Son Windows : aucune application choisie")
        return
    mode = a.get("mode", "adjust")
    try:
        if mode in ("toggle", "mute", "unmute"):
            muted = {"mute": True, "unmute": False}.get(mode)
            if muted is None:
                muted = not audio.get(target, app)[1]
            audio.set_mute(target, muted, app)
            engine.log("info", f"{who} : {'coupé' if muted else 'rétabli'}")
            return
        amount = float(a.get("amount") if a.get("amount") not in (None, "") else 2)
        key = ("win", target, app.lower())
        if mode == "set":
            new = audio.set_level(target, _clamp(float(a.get("level") if a.get("level") not in (None, "") else 50)), app)
        else:
            cur = engine.levels.current(key, lambda: audio.get(target, app)[0])
            new = audio.set_level(target, _clamp(cur + amount * delta), app)
        engine.levels.remember(key, new)
    except Exception as e:
        engine.log("error", f"{who} : {e}")


def win_value(engine, a: dict):
    """(volume, coupé) actuels pour l'affichage, ou None."""
    audio = getattr(engine, "winaudio", None)
    if audio is None:
        return None
    return audio.peek(a.get("target", "output"), (a.get("app") or "").strip())
