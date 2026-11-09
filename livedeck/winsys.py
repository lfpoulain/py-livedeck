"""Utilitaires Windows : son (volume, micro, applications), fenêtres, programmes, souris.

Le son passe par Core Audio, appelé directement avec ctypes : aucune dépendance ajoutée, et rien
de fragile dans l'exécutable (pycaw dépend de comtypes, qui génère du code au premier lancement).

Core Audio est une API COM : chaque fonction se trouve à un index fixe dans la table virtuelle de
l'objet. Les index ci-dessous suivent l'ordre de déclaration des interfaces dans le SDK Windows
(mmdeviceapi.h, endpointvolume.h, audiopolicy.h) ; un index faux fait planter le processus, d'où
les tests sur une vraie machine.

Hors Windows, ou sans périphérique audio (machines de CI), `available()` renvoie False et les
actions le disent au lieu d'échouer.
"""
from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import sys
import threading
import time

log = logging.getLogger("livedeck")

WINDOWS = sys.platform == "win32"
TARGETS = ("output", "mic", "app")


class AudioError(OSError):
    pass


# =============================================================================
# Core Audio par ctypes
# =============================================================================
if WINDOWS:
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                    ("Data4", ctypes.c_ubyte * 8)]

        def __init__(self, text: str):
            super().__init__()
            h = text.strip("{}").replace("-", "")
            self.Data1, self.Data2, self.Data3 = int(h[0:8], 16), int(h[8:12], 16), int(h[12:16], 16)
            for i in range(8):
                self.Data4[i] = int(h[16 + 2 * i:18 + 2 * i], 16)

    CLSID_MMDeviceEnumerator = GUID("BCDE0395-E52F-467C-8E3D-C4579291692E")
    IID_IMMDeviceEnumerator = GUID("A95664D2-9614-4F35-A746-DE8DB63617E6")
    IID_IAudioEndpointVolume = GUID("5CDF2C82-841E-4546-9722-0CF74078229A")
    IID_IAudioSessionManager2 = GUID("77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F")
    IID_IAudioSessionControl2 = GUID("BFB7FF88-7239-4FC9-8FA2-07C950BE9C6D")
    IID_ISimpleAudioVolume = GUID("87CE5498-68D6-44E5-9215-6DA47EF883D8")

    CLSCTX_ALL = 0x17
    E_RENDER, E_CAPTURE = 0, 1
    E_CONSOLE = 0
    COINIT_MULTITHREADED = 0
    RPC_E_CHANGED_MODE = -2147417850  # le fil est déjà initialisé dans l'autre mode : COM reste utilisable
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    _ole32 = ctypes.WinDLL("ole32")
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _user32 = ctypes.WinDLL("user32", use_last_error=True)

    # Index dans la table virtuelle (0-2 : IUnknown QueryInterface, AddRef, Release)
    QI, RELEASE = 0, 2
    ENUM_GET_DEFAULT = 4                       # IMMDeviceEnumerator::GetDefaultAudioEndpoint
    DEVICE_ACTIVATE = 3                        # IMMDevice::Activate
    EPV_SET_SCALAR, EPV_GET_SCALAR = 7, 9      # IAudioEndpointVolume::Set/GetMasterVolumeLevelScalar
    EPV_SET_MUTE, EPV_GET_MUTE = 14, 15        # IAudioEndpointVolume::SetMute / GetMute
    ASM2_GET_ENUM = 5                          # IAudioSessionManager2::GetSessionEnumerator
    ASE_COUNT, ASE_GET = 3, 4                  # IAudioSessionEnumerator::GetCount / GetSession
    ASC2_GET_PID = 14                          # IAudioSessionControl2::GetProcessId
    SAV_SET, SAV_GET, SAV_SET_MUTE, SAV_GET_MUTE = 3, 4, 5, 6  # ISimpleAudioVolume

    HRESULT = ctypes.c_long

    def _call(obj, index: int, *argtypes_and_values):
        """Appelle la méthode `index` de l'objet COM `obj` ; lève AudioError sur HRESULT d'échec."""
        types = argtypes_and_values[0::2]
        values = argtypes_and_values[1::2]
        vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        fn = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, *types)(vtbl[index])
        hr = fn(obj, *values)
        if hr < 0:
            raise AudioError(f"Core Audio : échec 0x{hr & 0xFFFFFFFF:08X} (méthode {index})")
        return hr

    def _release(obj) -> None:
        if obj:
            vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
            ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtbl[RELEASE])(obj)

    _com = threading.local()

    def _co_init() -> None:
        """COM doit être initialisé dans chaque fil qui l'utilise (exécution des actions, rafraîchissement)."""
        if getattr(_com, "ready", False):
            return
        hr = _ole32.CoInitializeEx(None, COINIT_MULTITHREADED)
        if hr < 0 and hr != RPC_E_CHANGED_MODE:
            raise AudioError(f"COM indisponible (0x{hr & 0xFFFFFFFF:08X})")
        _com.ready = True

    def _enumerator() -> ctypes.c_void_p:
        _co_init()
        obj = ctypes.c_void_p()
        hr = _ole32.CoCreateInstance(ctypes.byref(CLSID_MMDeviceEnumerator), None, CLSCTX_ALL,
                                     ctypes.byref(IID_IMMDeviceEnumerator), ctypes.byref(obj))
        if hr < 0:
            raise AudioError(f"Service audio Windows indisponible (0x{hr & 0xFFFFFFFF:08X})")
        return obj

    def _activate(flow: int, iid) -> ctypes.c_void_p:
        """Interface `iid` du périphérique par défaut (sortie ou micro)."""
        enum = _enumerator()
        device = ctypes.c_void_p()
        try:
            try:
                _call(enum, ENUM_GET_DEFAULT, ctypes.c_int, flow, ctypes.c_int, E_CONSOLE,
                      ctypes.POINTER(ctypes.c_void_p), ctypes.byref(device))
            except AudioError:
                raise AudioError("aucun " + ("micro" if flow == E_CAPTURE else "périphérique de sortie")
                                 + " par défaut") from None
            out = ctypes.c_void_p()
            _call(device, DEVICE_ACTIVATE, ctypes.POINTER(GUID), ctypes.byref(iid), wintypes.DWORD, CLSCTX_ALL,
                  ctypes.c_void_p, None, ctypes.POINTER(ctypes.c_void_p), ctypes.byref(out))
            return out
        finally:
            _release(device)
            _release(enum)

    def _process_name(pid: int) -> str:
        h = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(len(buf))
            if _kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return os.path.basename(buf.value)
            return ""
        finally:
            _kernel32.CloseHandle(h)


class WinAudio:
    """Volume (0-100) et coupure de la sortie, du micro par défaut, ou d'une application."""

    def available(self) -> bool:
        if not WINDOWS:
            return False
        try:
            self.get("output")
            return True
        except Exception:
            return False

    # -- périphériques par défaut ------------------------------------------------
    def _endpoint(self, target: str):
        return _activate(E_CAPTURE if target == "mic" else E_RENDER, IID_IAudioEndpointVolume)

    def _endpoint_get(self, target: str) -> tuple[float, bool]:
        epv = self._endpoint(target)
        try:
            level, muted = ctypes.c_float(), wintypes.BOOL()
            _call(epv, EPV_GET_SCALAR, ctypes.POINTER(ctypes.c_float), ctypes.byref(level))
            _call(epv, EPV_GET_MUTE, ctypes.POINTER(wintypes.BOOL), ctypes.byref(muted))
            return round(level.value * 100, 1), bool(muted.value)
        finally:
            _release(epv)

    # -- sessions d'application ----------------------------------------------------
    def _sessions(self):
        """(nom de l'exécutable, ISimpleAudioVolume) de chaque session audio. À libérer par l'appelant."""
        mgr = _activate(E_RENDER, IID_IAudioSessionManager2)
        enum = ctypes.c_void_p()
        out = []
        try:
            _call(mgr, ASM2_GET_ENUM, ctypes.POINTER(ctypes.c_void_p), ctypes.byref(enum))
            count = ctypes.c_int()
            _call(enum, ASE_COUNT, ctypes.POINTER(ctypes.c_int), ctypes.byref(count))
            for i in range(count.value):
                ctl, ctl2, vol = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
                try:
                    _call(enum, ASE_GET, ctypes.c_int, i, ctypes.POINTER(ctypes.c_void_p), ctypes.byref(ctl))
                    _call(ctl, QI, ctypes.POINTER(GUID), ctypes.byref(IID_IAudioSessionControl2),
                          ctypes.POINTER(ctypes.c_void_p), ctypes.byref(ctl2))
                    pid = wintypes.DWORD()
                    _call(ctl2, ASC2_GET_PID, ctypes.POINTER(wintypes.DWORD), ctypes.byref(pid))
                    name = _process_name(pid.value) if pid.value else ""
                    if not name:
                        continue  # sons système : pas d'application à viser
                    _call(ctl, QI, ctypes.POINTER(GUID), ctypes.byref(IID_ISimpleAudioVolume),
                          ctypes.POINTER(ctypes.c_void_p), ctypes.byref(vol))
                    out.append((name, vol))
                    vol = ctypes.c_void_p()  # transmis à l'appelant
                except AudioError:
                    continue
                finally:
                    _release(vol)
                    _release(ctl2)
                    _release(ctl)
        finally:
            _release(enum)
            _release(mgr)
        return out

    @staticmethod
    def _match(name: str, app: str) -> bool:
        a = (app or "").strip().lower()
        n = name.lower()
        return bool(a) and (n == a or n == a + ".exe" or n.removesuffix(".exe") == a.removesuffix(".exe"))

    def apps(self) -> list[tuple[str, float, bool]]:
        """Applications qui ont une session audio : (exécutable, volume, coupée)."""
        found: dict[str, tuple[float, bool]] = {}
        for name, vol in self._sessions():
            try:
                level, muted = ctypes.c_float(), wintypes.BOOL()
                _call(vol, SAV_GET, ctypes.POINTER(ctypes.c_float), ctypes.byref(level))
                _call(vol, SAV_GET_MUTE, ctypes.POINTER(wintypes.BOOL), ctypes.byref(muted))
                found.setdefault(name, (round(level.value * 100, 1), bool(muted.value)))
            except AudioError:
                pass
            finally:
                _release(vol)
        return sorted((n, lv, m) for n, (lv, m) in found.items())

    def _app_apply(self, app: str, fn) -> list:
        """Applique fn(ISimpleAudioVolume) à toutes les sessions de l'application (Chrome en a plusieurs)."""
        out, err = [], None
        sessions = self._sessions()
        try:
            for name, vol in sessions:
                if not self._match(name, app):
                    continue
                try:
                    out.append(fn(vol))
                except AudioError as e:  # une session périmée ne doit pas empêcher les autres
                    err = e
        finally:
            # Toutes les sessions sont libérées, même après une erreur : avant, une seule session en
            # échec laissait les références COM des suivantes à l'abandon.
            for _, vol in sessions:
                _release(vol)
        if not out:
            raise err or AudioError(f"« {app} » ne produit pas de son en ce moment (aucune session audio)")
        return out

    # -- API publique ---------------------------------------------------------------
    def get(self, target: str, app: str = "") -> tuple[float, bool]:
        """(volume 0-100, coupé)."""
        if target in ("output", "mic"):
            return self._endpoint_get(target)

        def read(vol):
            level, muted = ctypes.c_float(), wintypes.BOOL()
            _call(vol, SAV_GET, ctypes.POINTER(ctypes.c_float), ctypes.byref(level))
            _call(vol, SAV_GET_MUTE, ctypes.POINTER(wintypes.BOOL), ctypes.byref(muted))
            return round(level.value * 100, 1), bool(muted.value)
        return self._app_apply(app, read)[0]

    def set_level(self, target: str, value: float, app: str = "") -> float:
        value = max(0.0, min(100.0, float(value)))
        if target in ("output", "mic"):
            epv = self._endpoint(target)
            try:
                _call(epv, EPV_SET_SCALAR, ctypes.c_float, value / 100, ctypes.c_void_p, None)
            finally:
                _release(epv)
        else:
            self._app_apply(app, lambda vol: _call(vol, SAV_SET, ctypes.c_float, value / 100, ctypes.c_void_p, None))
        return value

    def set_mute(self, target: str, muted: bool, app: str = "") -> None:
        if target in ("output", "mic"):
            epv = self._endpoint(target)
            try:
                _call(epv, EPV_SET_MUTE, wintypes.BOOL, bool(muted), ctypes.c_void_p, None)
            finally:
                _release(epv)
        else:
            self._app_apply(app, lambda vol: _call(vol, SAV_SET_MUTE, wintypes.BOOL, bool(muted),
                                                   ctypes.c_void_p, None))


class CachedAudio:
    """Lectures mises en cache brièvement : le rafraîchissement des touches lit le son plusieurs fois
    par seconde, pour chaque touche concernée, et l'énumération des applications n'est pas gratuite."""

    # Plus long que le rafraîchissement des niveaux (0,5 s), sinon chaque passage manquait le cache.
    # Les écritures de LiveDeck l'invalident aussitôt : seul un changement fait ailleurs (clavier,
    # barre des tâches) peut mettre jusqu'à une seconde à apparaître.
    TTL = 1.0

    def __init__(self, backend):
        self.backend = backend
        self._cache: dict[tuple, tuple[float, object, BaseException | None]] = {}
        self._lock = threading.Lock()

    def available(self) -> bool:
        return self.backend.available()

    def get(self, target: str, app: str = ""):
        k = (target, (app or "").lower())
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(k)
        if hit and now - hit[0] < self.TTL:
            if hit[2] is not None:
                raise hit[2]
            return hit[1]
        try:
            val = self.backend.get(target, app)
        except Exception as e:
            # L'échec est mis en cache lui aussi : une application qui ne joue pas de son relançait
            # sinon une énumération complète de l'audio Windows à chaque dessin de chaque touche.
            with self._lock:
                self._cache[k] = (now, None, e)
            raise
        with self._lock:
            self._cache[k] = (now, val, None)
        return val

    def peek(self, target: str, app: str = ""):
        """Valeur en cache ou lue, sans jamais lever : pour l'affichage."""
        try:
            return self.get(target, app)
        except Exception:
            return None

    def _forget(self, target: str, app: str) -> None:
        with self._lock:
            self._cache.pop((target, (app or "").lower()), None)

    def set_level(self, target: str, value: float, app: str = "") -> float:
        out = self.backend.set_level(target, value, app)
        self._forget(target, app)
        return out

    def set_mute(self, target: str, muted: bool, app: str = "") -> None:
        self.backend.set_mute(target, muted, app)
        self._forget(target, app)

    def apps(self):
        return self.backend.apps()


# =============================================================================
# Fenêtres, programmes, souris, session
# =============================================================================
def focus_window(title: str) -> str | None:
    """Met au premier plan la première fenêtre visible dont le titre contient `title`.
    Renvoie le titre complet trouvé, ou None."""
    if not WINDOWS or not (title or "").strip():
        return None
    want = title.strip().lower()
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        if not _user32.IsWindowVisible(hwnd):
            return True
        n = _user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        _user32.GetWindowTextW(hwnd, buf, n + 1)
        if want in buf.value.lower():
            found.append((hwnd, buf.value))
            return False
        return True

    _user32.EnumWindows(each, 0)
    if not found:
        return None
    hwnd, text = found[0]
    if _user32.IsIconic(hwnd):
        _user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    # Windows refuse le premier plan à un processus qui n'a pas reçu d'entrée récente ; une frappe
    # d'Alt simulée lève ce verrou (méthode habituelle des lanceurs et des surfaces de contrôle).
    _user32.keybd_event(0x12, 0, 0, 0)
    _user32.keybd_event(0x12, 0, 2, 0)
    _user32.SetForegroundWindow(hwnd)
    return text


def launch(target: str, args: str = "") -> None:
    """Ouvre un programme, un fichier, un dossier ou une URL, comme un double-clic."""
    target = os.path.expandvars((target or "").strip().strip('"'))
    if not target:
        raise ValueError("rien à ouvrir")
    if WINDOWS:
        os.startfile(target, "open", args or "")
    else:
        subprocess.Popen(["xdg-open", target])


def run(command: str, hidden: bool = True) -> None:
    """Lance une commande (cmd) sans attendre sa fin."""
    if not (command or "").strip():
        raise ValueError("commande vide")
    flags = 0x08000000 if WINDOWS and hidden else 0  # CREATE_NO_WINDOW
    subprocess.Popen(command, shell=True, creationflags=flags, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def lock() -> None:
    if WINDOWS:
        _user32.LockWorkStation()


_mouse = None


def scroll(dx: int, dy: int) -> None:
    global _mouse
    if _mouse is None:
        from pynput.mouse import Controller
        _mouse = Controller()
    _mouse.scroll(dx, dy)


def type_text(text: str) -> None:
    from pynput.keyboard import Controller
    Controller().type(text or "")
