"""Stream Decks : détection à chaud, affichage des pages, appuis."""
from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

from .model import key
from .render import render

LIB_DIR = Path(__file__).resolve().parent / "lib"
log = logging.getLogger("livedeck")


class DeckHandle:
    def __init__(self, deck, serial: str, model: str, rows: int, cols: int, size: int, visual: bool = True):
        self.deck, self.serial, self.model = deck, serial, model
        self.rows, self.cols, self.size = rows, cols, size
        self.visual = visual  # False : Stream Deck Pedal (touches sans écran)
        self.page_id: str | None = None
        self.cache: dict[int, tuple] = {}


class DeckManager:
    def __init__(self, engine):
        self.engine = engine
        self.handles: dict[str, DeckHandle] = {}  # chemin HID -> deck ouvert
        self.error = ""
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._manager = None
        self.diag_handler = None  # pendant le diagnostic : fn(serial, index, appui) au lieu des actions
        self.stats = {"images": 0}
        self.image_ms: float | None = None  # temps moyen pour dessiner et envoyer une touche
        self._bad: set[tuple] = set()  # touches dont le rendu a échoué (journal sans répétition)
        self._last_page: dict[str, str] = {}  # page affichée par deck : retrouvée après un faux contact USB

    # -- démarrage ------------------------------------------------------------
    def start(self) -> None:
        if LIB_DIR.exists() and hasattr(os, "add_dll_directory"):
            os.add_dll_directory(str(LIB_DIR))
        try:
            from StreamDeck.DeviceManager import DeviceManager
            self._manager = DeviceManager()
        except Exception as e:  # hidapi absente, etc.
            self.error = f"Stream Deck indisponible : {e}. Lance setup.bat pour installer hidapi."
            self.engine.log("error", self.error)
            self.engine.emit("decks")
            return
        threading.Thread(target=self._run, name="decks", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        with self._lock:
            for h in self.handles.values():
                try:
                    with h.deck:
                        h.deck.reset()
                        h.deck.close()
                except Exception:
                    pass
            self.handles.clear()

    def request_refresh(self) -> None:
        self._wake.set()

    def _run(self) -> None:
        tick = 0
        while not self._stop.is_set():
            if tick % 20 == 0:  # recherche de nouveaux decks toutes les 2 s
                try:
                    self._scan()
                except Exception:  # le thread ne doit jamais mourir : plus de branchement à chaud sinon
                    log.exception("Stream Deck : détection")
            tick += 1
            if self._wake.wait(0.1):
                self._wake.clear()
                try:
                    self.refresh()
                except Exception:
                    log.exception("Stream Deck : affichage")

    def _scan(self) -> None:
        try:
            found = self._manager.enumerate()
        except Exception as e:
            self.error = str(e)
            return
        changed, gone = False, []
        with self._lock:
            for path, h in list(self.handles.items()):
                try:
                    alive = h.deck.connected()
                except Exception:
                    alive = False
                if not alive:
                    self.engine.log("warn", f"Stream Deck {h.model} débranché")
                    try:
                        h.deck.close()
                    except Exception:
                        pass
                    del self.handles[path]
                    gone.append(h.serial)
                    changed = True
            for d in found:
                if d.id() in self.handles:
                    continue
                try:
                    d.open()
                    d.reset()
                    rows, cols = d.key_layout()
                    visual = d.is_visual()
                    h = DeckHandle(d, d.get_serial_number(), d.deck_type(), rows, cols,
                                   d.key_image_format()["size"][0] if visual else 0, visual)
                except Exception as e:
                    self.engine.log("error", f"Ouverture du Stream Deck impossible : {e}")
                    continue
                self.handles[d.id()] = h
                self._show(h, self._initial_page(h))
                self.apply_settings(h.serial)
                d.set_key_callback(self._on_key)
                self.engine.log("info", f"Stream Deck {h.model} connecté ({h.serial})")
                changed = True
        for serial in gone:  # touches maintenues sur un deck disparu : on les relâche (micro, raccourcis)
            self.engine.release_all(deck=serial)
        if changed:
            self.error = ""
            self.engine.emit("decks")
            self.refresh(force=True)

    def _initial_page(self, h: DeckHandle, last: bool = True) -> str | None:
        """Page d'un deck qui (re)apparaît : celle où il était (faux contact USB), sinon sa page de
        démarrage, sinon la première page à sa taille."""
        pages = self.engine.pages
        if last and self.engine.page(self._last_page.get(h.serial)):
            return self._last_page[h.serial]
        wanted = (self.engine.cfg["decks"].get(h.serial) or {}).get("page")
        if self.engine.page(wanted):
            return wanted
        same = [p for p in pages if (p["cols"], p["rows"]) == (h.cols, h.rows)]
        return (same or pages)[0]["id"]

    def _show(self, h: DeckHandle, page_id: str | None) -> None:
        h.page_id = page_id
        if page_id:
            self._last_page[h.serial] = page_id
        h.cache.clear()

    def reassign_pages(self) -> None:
        """Après un import ou un remplacement de configuration : chaque deck reprend sa page de démarrage."""
        with self._lock:
            for h in self.handles.values():
                self._show(h, self._initial_page(h, last=False))
        self.request_refresh()

    def apply_settings(self, serial: str, page_changed: bool = False) -> None:
        """Luminosité ; la page ne change que si la page de démarrage vient d'être modifiée."""
        conf = self.engine.cfg["decks"].get(serial) or {}
        with self._lock:
            for h in self.handles.values():
                if h.serial == serial:
                    try:
                        with h.deck:
                            h.deck.set_brightness(int(conf.get("brightness", 80)))
                    except Exception:
                        pass
                    if page_changed and conf.get("page") and self.engine.page(conf["page"]):
                        self._show(h, conf["page"])
        self.request_refresh()

    # -- pages ----------------------------------------------------------------
    def goto(self, serial: str, target: str) -> None:
        pages = self.engine.pages
        with self._lock:
            for h in self.handles.values():
                if h.serial != serial:
                    continue
                if target in ("next", "prev"):
                    # on ne parcourt que les pages à la taille de ce deck (toutes si aucune ne l'est)
                    ring = [p for p in pages if (p["cols"], p["rows"]) == (h.cols, h.rows)] or pages
                    idx = next((i for i, p in enumerate(ring) if p["id"] == h.page_id), None)
                    if idx is None:
                        idx = -1 if target == "next" else 0
                    self._show(h, ring[(idx + (1 if target == "next" else -1)) % len(ring)]["id"])
                elif self.engine.page(target):
                    self._show(h, target)
        self.engine.emit("decks")
        self.request_refresh()

    def identify(self, serial: str) -> None:
        """Allume le deck en couleur avec son nom pendant 2 s, pour le reconnaître."""
        from StreamDeck.ImageHelpers import PILHelper
        with self._lock:
            handles = [h for h in self.handles.values() if h.serial == serial and h.visual]
        for h in handles:
            v = {"bg": "#2dd4bf", "fg": "#04211d", "label": ""}
            try:
                for idx in range(h.rows * h.cols):
                    v["label"] = h.model.replace("Stream Deck ", "") if idx == 0 else ("●" if idx % 2 else "")
                    native = PILHelper.to_native_key_format(h.deck, render(v, h.size))
                    with h.deck:
                        h.deck.set_key_image(idx, native)
            except Exception:
                pass
            h.cache.clear()
        t = threading.Timer(2.0, self.request_refresh)
        t.daemon = True
        t.start()

    # -- diagnostic -----------------------------------------------------------
    def _count_image(self, t0: float) -> None:
        ms = (time.perf_counter() - t0) * 1000
        self.stats["images"] += 1
        self.image_ms = ms if self.image_ms is None else self.image_ms * 0.9 + ms * 0.1

    def paint(self, serial: str, index: int, visual: dict | None) -> None:
        """Dessine directement une touche (diagnostic), sans passer par les pages."""
        from StreamDeck.ImageHelpers import PILHelper
        with self._lock:
            handles = [h for h in self.handles.values() if h.serial == serial]
        for h in handles:
            if index >= h.rows * h.cols or not h.visual:
                continue
            try:
                t0 = time.perf_counter()
                native = PILHelper.to_native_key_format(h.deck, render(visual, h.size))
                with h.deck:
                    h.deck.set_key_image(index, native)
                h.cache.pop(index, None)
                self._count_image(t0)
            except Exception:
                pass

    def set_brightness(self, serial: str, value: int) -> None:
        with self._lock:
            for h in self.handles.values():
                if h.serial == serial:
                    try:
                        with h.deck:
                            h.deck.set_brightness(int(value))
                    except Exception:
                        pass

    def restore(self) -> None:
        """Après le diagnostic : luminosité et affichage ; chaque deck reste sur la page où il était."""
        with self._lock:
            serials = {h.serial for h in self.handles.values()}
            for h in self.handles.values():
                h.cache.clear()
        for s in serials:
            self.apply_settings(s)
        self.request_refresh()

    def info(self) -> list[dict]:
        with self._lock:
            return [{"serial": h.serial, "model": h.model, "cols": h.cols, "rows": h.rows, "page": h.page_id,
                     "visual": h.visual} for h in self.handles.values()]

    # -- affichage / appuis ---------------------------------------------------
    def _on_key(self, deck, index: int, state: bool) -> None:
        """Appelé par le thread de lecture du deck : une exception ici le tuerait (deck muet)."""
        try:
            h = self.handles.get(deck.id())
            if not h:
                return
            if index >= h.rows * h.cols:  # hors grille : touches tactiles du Neo = page précédente / suivante
                if state and not self.diag_handler:
                    self.goto(h.serial, "prev" if index == h.rows * h.cols else "next")
                return
            if self.diag_handler:
                self.diag_handler(h.serial, index, state)
                return
            if not h.page_id:
                return
            r, c = divmod(index, h.cols)
            self.engine.press(h.page_id, key(r, c), state, deck=h.serial)
        except Exception as e:
            log.exception("Stream Deck : touche")
            try:
                self.engine.log("error", f"Touche {index} : {e}")
            except Exception:
                pass

    def refresh(self, force: bool = False) -> None:
        from StreamDeck.ImageHelpers import PILHelper
        with self._lock:
            handles = list(self.handles.values())
        for h in handles:
            page = self.engine.page(h.page_id)
            if page is None:
                self._show(h, self._initial_page(h))
                page = self.engine.page(h.page_id)
            if not h.visual:
                continue
            for idx in range(h.rows * h.cols):
                r, c = divmod(idx, h.cols)
                b = page["buttons"].get(key(r, c)) if page and r < page["rows"] and c < page["cols"] else None
                try:
                    v = self.engine.visual(b)
                    self._bad.discard((h.serial, idx))
                except Exception:  # un bouton défaillant ne bloque pas les autres
                    if (h.serial, idx) not in self._bad:  # journalisé une fois, pas à chaque rafraîchissement
                        self._bad.add((h.serial, idx))
                        log.exception("Stream Deck : rendu de la touche %s", idx)
                    v = {"bg": "#3a0d0d", "fg": "#ffffff", "label": "ERREUR"}
                sig = tuple(sorted(v.items())) if v else None
                if not force and h.cache.get(idx, ()) == sig:
                    continue
                if self.diag_handler:
                    continue  # le diagnostic garde la main sur l'affichage
                try:
                    t0 = time.perf_counter()
                    native = PILHelper.to_native_key_format(h.deck, render(v, h.size))
                    with h.deck:
                        h.deck.set_key_image(idx, native)
                    h.cache[idx] = sig
                    self._count_image(t0)
                except Exception:
                    h.cache.pop(idx, None)
                    break  # deck probablement débranché : le scan s'en chargera


class DeckHub:
    """Fait cohabiter plusieurs familles de surfaces (Stream Deck, Loupedeck) derrière l'API
    d'un seul gestionnaire : l'interface, le diagnostic et le moteur n'en voient qu'un.

    Les appels nommant un numéro de série sont diffusés à tous : seul le gestionnaire qui possède
    cet appareil réagit, les autres n'ont simplement rien à faire."""

    def __init__(self, engine, managers=None):
        if managers is None:
            managers = [DeckManager(engine)]
            from .ldeck import LoupedeckManager
            managers.append(LoupedeckManager(engine))
        self.managers = list(managers)
        self._diag_handler = None

    # -- état agrégé ----------------------------------------------------------
    @property
    def error(self) -> str:
        # Une famille absente n'est pas une panne tant qu'une autre fonctionne.
        errs = [m.error for m in self.managers if m.error]
        return "" if len(errs) < len(self.managers) else " ".join(errs)

    @property
    def stats(self) -> dict:
        return {"images": sum(m.stats.get("images", 0) for m in self.managers)}

    @property
    def image_ms(self) -> float | None:
        vals = [m.image_ms for m in self.managers if m.image_ms is not None]
        return max(vals) if vals else None

    @property
    def diag_handler(self):
        return self._diag_handler

    @diag_handler.setter
    def diag_handler(self, fn) -> None:
        self._diag_handler = fn
        for m in self.managers:
            m.diag_handler = fn

    def info(self) -> list[dict]:
        return [d for m in self.managers for d in m.info()]

    # -- diffusion ------------------------------------------------------------
    def _all(self, name: str, *a, **kw) -> None:
        for m in self.managers:
            try:
                getattr(m, name)(*a, **kw)
            except Exception:  # une famille en panne n'empêche pas les autres de fonctionner
                log.exception("decks : %s", name)

    def start(self) -> None:
        self._all("start")

    def stop(self) -> None:
        self._all("stop")

    def request_refresh(self) -> None:
        self._all("request_refresh")

    def refresh(self, force: bool = False) -> None:
        self._all("refresh", force)

    def reassign_pages(self) -> None:
        self._all("reassign_pages")

    def restore(self) -> None:
        self._all("restore")

    def apply_settings(self, serial: str, page_changed: bool = False) -> None:
        self._all("apply_settings", serial, page_changed)

    def goto(self, serial: str, target: str) -> None:
        self._all("goto", serial, target)

    def identify(self, serial: str) -> None:
        self._all("identify", serial)

    def paint(self, serial: str, index: int, visual: dict | None) -> None:
        self._all("paint", serial, index, visual)

    def set_brightness(self, serial: str, value: int) -> None:
        self._all("set_brightness", serial, value)
