"""Loupedeck : détection à chaud, affichage des pages, appuis.

Même contrat que DeckManager (Stream Deck) : le moteur et l'interface ne font pas la
différence. Les 12 touches tactiles du pavé central sont des touches de page ordinaires,
donc toutes les actions, retours d'état et intégrations existantes fonctionnent telles quelles.
"""
from __future__ import annotations

import logging
import threading
import time

from . import loupedeck as ld
from .model import control_key, key
from .render import hex_rgb, render, render_strip, render_wheel

log = logging.getLogger("livedeck")

# Aucun bouton n'a de rôle par défaut : un contrôle sans action affectée ne fait RIEN. Un
# comportement caché (changer de page, par exemple) se découvre en appuyant par accident, en
# plein direct. Pour naviguer, on pose « Page suivante » sur le bouton de son choix.


class LoupedeckHandle:
    def __init__(self, dev: ld.Loupedeck):
        self.dev = dev
        self.serial, self.model = dev.serial, dev.model
        self.rows, self.cols = dev.profile.rows, dev.profile.cols
        self.size = dev.profile.key_size
        self.visual = True
        self.page_id: str | None = None
        self.cache: dict[int, tuple] = {}
        self.zones: dict[str, tuple] = {}  # bandeaux et wheel : dernière signature affichée
        self.leds: dict[str, tuple] = {}   # diodes des boutons ronds : dernière couleur envoyée
        self.fingers: dict[int, tuple[str, str]] = {}  # doigt -> (page, case) appuyée au poser
        self.controls = {c["id"]: c for c in ld.controls(dev.profile)}
        self.reset_zones()

    def reset_zones(self) -> None:
        """État connu juste après un effacement : tout est noir, inutile de renvoyer du noir.
        Les signatures des bandeaux, de la molette et des diodes traversent les changements de page ;
        seules les vignettes sont réaffichées en bloc, comme sur un Stream Deck."""
        empty = tuple([None] * ld.STRIP_ZONES)
        self.zones = {s: empty for s in ("left", "right") if s in self.dev.profile.displays}
        if self.dev.profile.wheel:
            self.zones["wheel"] = None
        self.leds = {cid: (0, 0, 0) for cid, m in self.controls.items() if m.get("led")}

    def invalidate(self, everything: bool = False) -> None:
        self.cache.clear()
        if everything:
            self.reset_zones()


class LoupedeckManager:
    def __init__(self, engine):
        self.engine = engine
        self.handles: dict[str, LoupedeckHandle] = {}  # port série -> appareil ouvert
        self.error = ""
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self.diag_handler = None
        self.stats = {"images": 0}
        self.image_ms: float | None = None
        self._bad: set[tuple] = set()
        self._last_page: dict[str, str] = {}
        self._failures: dict[str, int] = {}  # port -> échecs d'ouverture consécutifs

    # -- démarrage ------------------------------------------------------------
    def start(self) -> None:
        try:
            import importlib
            importlib.import_module("serial")
        except ImportError:
            self.error = "Loupedeck indisponible : pyserial n'est pas installé (lance setup.bat)."
            self.engine.log("warn", self.error)
            return
        threading.Thread(target=self._run, name="loupedecks", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        with self._lock:
            for h in self.handles.values():
                try:
                    h.dev.clear()
                    h.dev.close()
                except Exception:
                    pass
            self.handles.clear()

    def request_refresh(self) -> None:
        self._wake.set()

    def _run(self) -> None:
        tick = 0
        while not self._stop.is_set():
            if tick % 20 == 0:
                try:
                    self._scan()
                except Exception:
                    log.exception("Loupedeck : détection")
            tick += 1
            if self._wake.wait(0.1):
                self._wake.clear()
                try:
                    self.refresh()
                except Exception:
                    log.exception("Loupedeck : affichage")

    def _scan(self) -> None:
        try:
            found = ld.discover()
        except Exception as e:
            self.error = str(e)
            return
        changed, gone = False, []
        with self._lock:
            present = {d["path"] for d in found}
            for path in list(self._failures):  # appareil débranché : on oublie ses échecs passés
                if path not in present:
                    del self._failures[path]
            for path, h in list(self.handles.items()):
                if h.dev.connected:
                    continue
                self.engine.log("warn", f"{h.model} débranché")
                del self.handles[path]
                gone.append(h.serial)
                changed = True
            for d in found:
                if d["path"] in self.handles:
                    continue
                try:
                    dev = ld.Loupedeck(d["path"], d["pid"], d["serial"], on_event=self._on_event)
                    dev.connect()
                except Exception as e:
                    # Un premier échec est souvent passager (appareil encore en train de se refermer après
                    # l'arrêt de LiveDeck) : on réessaie sans rien dire. S'il persiste — typiquement le
                    # logiciel Loupedeck officiel qui garde le port —, on le dit UNE fois, pas toutes les 2 s.
                    n = self._failures.get(d["path"], 0) + 1
                    self._failures[d["path"]] = n
                    if n == 2:
                        self.engine.log("error", f"Ouverture du {d['model']} ({d['path']}) impossible : {e}. "
                                                 "Ferme le logiciel Loupedeck s'il est ouvert : il garde l'appareil.")
                    continue
                self._failures.pop(d["path"], None)
                h = LoupedeckHandle(dev)
                self.handles[d["path"]] = h
                self._show(h, self._initial_page(h))
                self.apply_settings(h.serial)
                try:
                    dev.clear()
                except Exception:
                    pass
                self.engine.log("info", f"{h.model} connecté ({h.serial}, firmware {dev.firmware})")
                changed = True
        for serial in gone:  # touches maintenues sur un appareil disparu : on les relâche
            self.engine.release_all(deck=serial)
        if changed:
            self.error = ""
            self.engine.emit("decks")
            self.refresh(force=True)

    # -- pages ----------------------------------------------------------------
    def _initial_page(self, h: LoupedeckHandle, last: bool = True) -> str | None:
        pages = self.engine.pages
        if last and self.engine.page(self._last_page.get(h.serial)):
            return self._last_page[h.serial]
        wanted = (self.engine.cfg["decks"].get(h.serial) or {}).get("page")
        if self.engine.page(wanted):
            return wanted
        same = [p for p in pages if (p["cols"], p["rows"]) == (h.cols, h.rows)]
        return (same or pages)[0]["id"]

    def _show(self, h: LoupedeckHandle, page_id: str | None) -> None:
        h.page_id = page_id
        if page_id:
            self._last_page[h.serial] = page_id
        h.invalidate()

    def reassign_pages(self) -> None:
        with self._lock:
            for h in self.handles.values():
                self._show(h, self._initial_page(h, last=False))
        self.request_refresh()

    def apply_settings(self, serial: str, page_changed: bool = False) -> None:
        conf = self.engine.cfg["decks"].get(serial) or {}
        with self._lock:
            for h in self.handles.values():
                if h.serial != serial:
                    continue
                try:
                    h.dev.set_brightness(int(conf.get("brightness", 80)))
                except Exception:
                    pass
                if page_changed and conf.get("page") and self.engine.page(conf["page"]):
                    self._show(h, conf["page"])
        self.request_refresh()

    def goto(self, serial: str, target: str) -> None:
        pages = self.engine.pages
        with self._lock:
            for h in self.handles.values():
                if h.serial != serial:
                    continue
                if target in ("next", "prev"):
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
        with self._lock:
            handles = [h for h in self.handles.values() if h.serial == serial]
        for h in handles:
            v = {"bg": "#2dd4bf", "fg": "#04211d", "label": ""}
            try:
                for idx in range(h.rows * h.cols):
                    v["label"] = h.model.replace("Loupedeck ", "") if idx == 0 else ("●" if idx % 2 else "")
                    h.dev.draw_key(idx, render(v, h.size), refresh=False)
                h.dev.refresh("center")
                if self.haptic_enabled(h.serial):
                    h.dev.vibrate("court")
            except Exception:
                pass
            h.invalidate(everything=True)
        t = threading.Timer(2.0, self.request_refresh)
        t.daemon = True
        t.start()

    # -- diagnostic -----------------------------------------------------------
    def _count_image(self, t0: float) -> None:
        ms = (time.perf_counter() - t0) * 1000
        self.stats["images"] += 1
        self.image_ms = ms if self.image_ms is None else self.image_ms * 0.9 + ms * 0.1

    def paint(self, serial: str, index: int, visual: dict | None) -> None:
        with self._lock:
            handles = [h for h in self.handles.values() if h.serial == serial]
        for h in handles:
            if index >= h.rows * h.cols:
                continue
            try:
                t0 = time.perf_counter()
                h.dev.draw_key(index, render(visual, h.size))
                h.cache.pop(index, None)
                self._count_image(t0)
            except Exception:
                pass

    def set_brightness(self, serial: str, value: int) -> None:
        with self._lock:
            for h in self.handles.values():
                if h.serial == serial:
                    try:
                        h.dev.set_brightness(int(value))
                    except Exception:
                        pass

    def restore(self) -> None:
        with self._lock:
            serials = {h.serial for h in self.handles.values()}
            for h in self.handles.values():
                h.invalidate(everything=True)
        for s in serials:
            self.apply_settings(s)
        self.request_refresh()

    def info(self) -> list[dict]:
        with self._lock:
            return [{"serial": h.serial, "model": h.model, "cols": h.cols, "rows": h.rows,
                     "page": h.page_id, "visual": h.visual} for h in self.handles.values()]

    # -- événements -----------------------------------------------------------
    def _handle(self, serial: str) -> LoupedeckHandle | None:
        return next((h for h in self.handles.values() if h.serial == serial), None)

    def _on_event(self, evt: dict) -> None:
        """Appelé par le thread de lecture de l'appareil : il ne doit jamais lever."""
        try:
            h = self._handle(evt.get("serial", ""))
            if not h:
                return
            kind = evt["type"]
            if kind == "touch":
                self._on_touch(h, evt)
            elif kind == "button":
                self._on_button(h, evt)
            elif kind == "rotate":
                self._on_rotate(h, evt)
        except Exception as e:
            log.exception("Loupedeck : événement")
            try:
                self.engine.log("error", f"Loupedeck : {e}")
            except Exception:
                pass

    def _on_touch(self, h: LoupedeckHandle, evt: dict) -> None:
        """Un doigt posé sur une vignette la tient jusqu'à ce qu'il soit levé, même s'il a glissé
        ailleurs entre-temps : le relâché s'applique toujours à la case du poser. Sans cela, un
        glissement vers un bandeau laisserait la touche enfoncée (micro intercom ouvert en direct)."""
        phase, finger = evt["phase"], evt["finger"]
        if phase == "move":
            return
        if phase == "end":
            started = h.fingers.pop(finger, None)
            if started is None:
                return  # le doigt n'était pas parti d'une vignette
            page_id, k = started
            if self.diag_handler:
                self.diag_handler(h.serial, self._index(h, k), False)
            else:
                self.engine.press(page_id, k, False, deck=h.serial)
            return
        target = evt.get("target") or {}
        screen = target.get("screen")
        if screen == "center":
            k = key(target["row"], target["col"])
            if self.diag_handler:
                h.fingers[finger] = (h.page_id or "", k)
                self.diag_handler(h.serial, target["key"], True)
                return
        elif screen in ("left", "right"):
            # Un bandeau latéral vaut trois touches superposées, chacune avec son affichage.
            k = control_key(ld.strip_zone("L" if screen == "left" else "R", evt["y"]))
        elif screen == "wheel":
            k = control_key("wheel")  # toucher l'écran de la molette équivaut à l'enfoncer
        else:
            return
        if self.diag_handler or not h.page_id:
            return
        h.fingers[finger] = (h.page_id, k)
        self._haptic(h, k)
        self.engine.press(h.page_id, k, True, deck=h.serial)

    @staticmethod
    def _index(h: LoupedeckHandle, k: str) -> int:
        r, c = (int(n) for n in k.split(","))
        return r * h.cols + c

    def haptic_enabled(self, serial: str) -> bool:
        """Réglage de l'appareil (Réglages → Decks). Activé par défaut."""
        return bool((self.engine.cfg["decks"].get(serial) or {}).get("haptic", True))

    def _haptic(self, h: LoupedeckHandle, k: str) -> None:
        """Vibration à l'appui : sur une dalle tactile, rien n'indique sinon que l'appui est pris.
        Le réglage de l'appareil prime sur celui du contrôle : décoché, plus rien ne vibre."""
        if not self.haptic_enabled(h.serial):
            return
        el = self.engine.button(h.page_id, k) if h.page_id else None
        pattern = (el or {}).get("haptic", "court")
        if not pattern:
            return
        try:
            h.dev.vibrate(pattern)
        except Exception:
            pass

    def _on_button(self, h: LoupedeckHandle, evt: dict) -> None:
        cid = evt["id"]
        if self.diag_handler or not h.page_id:
            return
        k = control_key(cid)
        if evt["down"]:
            self._haptic(h, k)
        # Même libre, l'appui est transmis : le moteur le signale à l'éditeur, qui sélectionne
        # le contrôle. C'est ce qui permet de câbler l'appareil organe par organe.
        self.engine.press(h.page_id, k, evt["down"], deck=h.serial)

    def _on_rotate(self, h: LoupedeckHandle, evt: dict) -> None:
        if self.diag_handler or not h.page_id:
            return
        self.engine.rotate(h.page_id, evt["id"], evt["delta"], deck=h.serial)

    # -- affichage ------------------------------------------------------------
    def _control_visual(self, h: LoupedeckHandle, cid: str) -> dict | None:
        el = self.engine.control(h.page_id, cid) if h.page_id else None
        if not el:
            return None
        try:
            return self.engine.visual(el)
        except Exception:
            if (h.serial, cid) not in self._bad:
                self._bad.add((h.serial, cid))
                log.exception("Loupedeck : rendu du contrôle %s", cid)
            return {"bg": "#3a0d0d", "fg": "#ffffff", "label": "ERREUR"}

    def _refresh_zones(self, h: LoupedeckHandle, force: bool) -> None:
        """Bandeaux latéraux et écran de la molette : redessinés seulement s'ils ont changé."""
        prof = h.dev.profile
        for side, screen in (("L", "left"), ("R", "right")):
            if screen not in prof.displays:
                continue
            vis = [self._control_visual(h, f"strip{side}{z}") for z in range(1, ld.STRIP_ZONES + 1)]
            sig = tuple(tuple(sorted(v.items())) if v else None for v in vis)
            if not force and h.zones.get(screen) == sig:
                continue
            info = prof.displays[screen]
            try:
                h.dev.draw(screen, render_strip(vis, info["width"], info["height"]))
                h.zones[screen] = sig
            except Exception:
                h.zones.pop(screen, None)
        if prof.wheel and "wheel" in prof.displays:
            v = self._control_visual(h, "wheel")
            sig = tuple(sorted(v.items())) if v else None
            if force or h.zones.get("wheel") != sig:
                try:
                    h.dev.draw("wheel", render_wheel(v, prof.displays["wheel"]["width"]))
                    h.zones["wheel"] = sig
                except Exception:
                    h.zones.pop("wheel", None)

    def _refresh_leds(self, h: LoupedeckHandle, force: bool) -> None:
        """Diodes RGB des 8 boutons ronds : elles suivent le retour d'état du contrôle affecté."""
        for cid, meta in h.controls.items():
            if not meta.get("led"):
                continue
            v = self._control_visual(h, cid)
            color = hex_rgb(v.get("lit") or v.get("bg") or "#000000") if v else (0, 0, 0)
            if not force and h.leds.get(cid) == color:
                continue
            try:
                h.dev.set_button_color(cid, color)
                h.leds[cid] = color
            except Exception:
                h.leds.pop(cid, None)

    def refresh(self, force: bool = False) -> None:
        with self._lock:
            handles = list(self.handles.values())
        for h in handles:
            page = self.engine.page(h.page_id)
            if page is None:
                self._show(h, self._initial_page(h))
                page = self.engine.page(h.page_id)
            if self.diag_handler:
                continue  # le diagnostic garde la main sur l'affichage
            drawn = False
            for idx in range(h.rows * h.cols):
                r, c = divmod(idx, h.cols)
                b = page["buttons"].get(key(r, c)) if page and r < page["rows"] and c < page["cols"] else None
                try:
                    v = self.engine.visual(b)
                    self._bad.discard((h.serial, idx))
                except Exception:  # un bouton défaillant ne bloque pas les autres
                    if (h.serial, idx) not in self._bad:
                        self._bad.add((h.serial, idx))
                        log.exception("Loupedeck : rendu de la touche %s", idx)
                    v = {"bg": "#3a0d0d", "fg": "#ffffff", "label": "ERREUR"}
                sig = tuple(sorted(v.items())) if v else None
                if not force and h.cache.get(idx, ()) == sig:
                    continue
                try:
                    t0 = time.perf_counter()
                    h.dev.draw_key(idx, render(v, h.size), refresh=False)
                    h.cache[idx] = sig
                    drawn = True
                    self._count_image(t0)
                except Exception:
                    h.cache.pop(idx, None)
                    break  # appareil probablement débranché : le scan s'en chargera
            if drawn:
                try:
                    h.dev.refresh("center")  # une seule bascule d'écran pour toutes les touches modifiées
                except Exception:
                    pass
            try:
                self._refresh_zones(h, force)
                self._refresh_leds(h, force)
            except Exception:
                log.exception("Loupedeck : rendu des contrôles")
