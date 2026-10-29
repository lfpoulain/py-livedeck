"""Simulation de raccourcis clavier sur cette machine (remplace VICREO Listener)."""
from __future__ import annotations

_ctrl = None


def _controller():
    global _ctrl
    if _ctrl is None:
        from pynput.keyboard import Controller
        _ctrl = Controller()
    return _ctrl


def _special():
    from pynput.keyboard import Key
    table = {
        "ctrl": Key.ctrl, "control": Key.ctrl, "shift": Key.shift, "alt": Key.alt, "altgr": Key.alt_gr,
        "win": Key.cmd, "cmd": Key.cmd, "meta": Key.cmd,
        "enter": Key.enter, "return": Key.enter, "esc": Key.esc, "escape": Key.esc, "space": Key.space,
        "tab": Key.tab, "backspace": Key.backspace, "delete": Key.delete, "del": Key.delete,
        "insert": Key.insert, "home": Key.home, "end": Key.end, "pageup": Key.page_up,
        "pagedown": Key.page_down, "up": Key.up, "down": Key.down, "left": Key.left, "right": Key.right,
        "capslock": Key.caps_lock, "printscreen": Key.print_screen, "pause": Key.pause, "menu": Key.menu,
        "volumeup": Key.media_volume_up, "volumedown": Key.media_volume_down,
        "mute": Key.media_volume_mute, "playpause": Key.media_play_pause,
        "next": Key.media_next, "previous": Key.media_previous,
    }
    for i in range(1, 21):
        table[f"f{i}"] = getattr(Key, f"f{i}")
    return table


def parse(combo: str) -> list:
    from pynput.keyboard import KeyCode
    special = _special()
    keys = []
    for part in [p.strip().lower() for p in combo.replace(" ", "").split("+")]:
        if not part:
            continue
        if part in special:
            keys.append(special[part])
        elif len(part) == 1:
            keys.append(KeyCode.from_char(part))
        else:
            raise ValueError(f"Touche inconnue : {part}")
    return keys


def press(combo: str) -> None:
    c = _controller()
    for k in parse(combo):
        c.press(k)


def release(combo: str) -> None:
    c = _controller()
    for k in reversed(parse(combo)):
        c.release(k)


def tap(combo: str) -> None:
    press(combo)
    release(combo)
