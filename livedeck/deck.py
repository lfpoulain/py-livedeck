"""Stream Decks : détection à chaud, affichage des pages, appuis."""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from .model import key
from .render import render

LIB_DIR = Path(__file__).resolve().parent / "lib"


class DeckHandle:
    def __init__(self, deck, serial: str, model: str, rows: int, cols: int, size: int):
        self.deck, self.serial, self.model = deck, serial, model
        self.rows, self.cols, self.size = rows, cols, size
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

    # -- démarrage ------------------------------------------------------------
    def start(self) -> None:
        if LIB_DIR.exists() and hasattr(os, "add_dll_directory"):
            os.add_dll_directory(str(LIB_DIR))
        try:
            from StreamDeck.DeviceManager import DeviceManager
            self._manager = DeviceManager()
        except Exception as e:  # hidapi absente, etc.
            self.error = f"Stream Deck indisponible : {e}. Lancez setup.bat pour installer hidapi."
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
                self._scan()
            tick += 1
            if self._wake.wait(0.1):
                self._wake.clear()
                self.refresh()

    def _scan(self) -> None:
        try:
            found = self._manager.enumerate()
        except Exception as e:
            self.error = str(e)
            return
        changed = False
        with self._lock:
            for path, h in list(self.handles.items()):
                if not h.deck.connected():
                    self.engine.log("warn", f"Stream Deck {h.model} débranché")
                    try:
                        h.deck.close()
                    except Exception:
                        pass
                    del self.handles[path]
                    changed = True
            for d in found:
                if d.id() in self.handles or not d.is_visual():
                    continue
                try:
                    d.open()
                    d.reset()
                    rows, cols = d.key_layout()
                    h = DeckHandle(d, d.get_serial_number(), d.deck_type(), rows, cols,
                                   d.key_image_format()["size"][0])
                except Exception as e:
                    self.engine.log("error", f"Ouverture du Stream Deck impossible : {e}")
                    continue
                self.handles[d.id()] = h
                h.page_id = self._initial_page(h)
                self.apply_settings(h.serial)
                d.set_key_callback(self._on_key)
                self.engine.log("info", f"Stream Deck {h.model} connecté ({h.serial})")
                changed = True
        if changed:
            self.error = ""
            self.engine.emit("decks")
            self.refresh(force=True)

    def _initial_page(self, h: DeckHandle) -> str | None:
        pages = self.engine.pages
        wanted = self.engine.cfg["decks"].get(h.serial, {}).get("page")
        if self.engine.page(wanted):
            return wanted
        same = [p for p in pages if (p["cols"], p["rows"]) == (h.cols, h.rows)]
        return (same or pages)[0]["id"]

    def reassign_pages(self) -> None:
        with self._lock:
            for h in self.handles.values():
                h.page_id = self._initial_page(h)
                h.cache.clear()
        self.request_refresh()

    def apply_settings(self, serial: str) -> None:
        conf = self.engine.cfg["decks"].get(serial, {})
        with self._lock:
            for h in self.handles.values():
                if h.serial == serial:
                    try:
                        with h.deck:
                            h.deck.set_brightness(int(conf.get("brightness", 80)))
                    except Exception:
                        pass
                    if conf.get("page") and self.engine.page(conf["page"]):
                        h.page_id = conf["page"]
                        h.cache.clear()
        self.request_refresh()

    # -- pages ----------------------------------------------------------------
    def goto(self, serial: str, target: str) -> None:
        pages = self.engine.pages
        with self._lock:
            for h in self.handles.values():
                if h.serial != serial:
                    continue
                idx = next((i for i, p in enumerate(pages) if p["id"] == h.page_id), 0)
                if target == "next":
                    h.page_id = pages[(idx + 1) % len(pages)]["id"]
                elif target == "prev":
                    h.page_id = pages[(idx - 1) % len(pages)]["id"]
                elif self.engine.page(target):
                    h.page_id = target
                h.cache.clear()
        self.engine.emit("decks")
        self.request_refresh()

    def identify(self, serial: str) -> None:
        """Allume le deck en couleur avec son nom pendant 2 s, pour le reconnaître."""
        from StreamDeck.ImageHelpers import PILHelper
        with self._lock:
            handles = [h for h in self.handles.values() if h.serial == serial]
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
        threading.Timer(2.0, self.request_refresh).start()

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
            if index >= h.rows * h.cols:
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
        """Après le diagnostic : luminosité et pages d'origine."""
        with self._lock:
            serials = [h.serial for h in self.handles.values()]
            for h in self.handles.values():
                h.cache.clear()
        for s in serials:
            self.apply_settings(s)
        self.request_refresh()

    def info(self) -> list[dict]:
        with self._lock:
            return [{"serial": h.serial, "model": h.model, "cols": h.cols, "rows": h.rows, "page": h.page_id}
                    for h in self.handles.values()]

    # -- affichage / appuis ---------------------------------------------------
    def _on_key(self, deck, index: int, state: bool) -> None:
        h = self.handles.get(deck.id())
        if not h:
            return
        if self.diag_handler:
            self.diag_handler(h.serial, index, state)
            return
        if not h.page_id:
            return
        r, c = divmod(index, h.cols)
        self.engine.press(h.page_id, key(r, c), state, deck=h.serial)

    def refresh(self, force: bool = False) -> None:
        from StreamDeck.ImageHelpers import PILHelper
        with self._lock:
            handles = list(self.handles.values())
        for h in handles:
            page = self.engine.page(h.page_id)
            if page is None:
                h.page_id = self._initial_page(h)
                page = self.engine.page(h.page_id)
            for idx in range(h.rows * h.cols):
                r, c = divmod(idx, h.cols)
                b = page["buttons"].get(key(r, c)) if page and r < page["rows"] and c < page["cols"] else None
                v = self.engine.visual(b)
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
