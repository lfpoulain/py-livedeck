"""Cœur de LiveDeck : configuration, exécution des boutons, état en direct."""
from __future__ import annotations

import copy
import logging
import queue
import threading
import time

from . import actions, adjust, feedback, winsys
from . import vars as textvars
from .health import HealthMonitor, JsonPoller, parse_targets
from .integrations import Intercom, Orchestream, Tallies, overlay_list
from .model import (CONFIG_PATH, backup_config, config_text, control_key, has_backup_today, new_page,
                    normalize_button, normalize_control, open_config, parse_control, parse_key,
                    save_config_text)
from .vmix import VmixClient

log = logging.getLogger("livedeck")
LONG_PRESS = 0.5  # secondes
LIVE_CONDS = ("level", "win_audio")  # retours d'état qui changent sans événement : relus périodiquement


def _with_delta(acts: list[dict], ticks: int) -> list[dict]:
    """Remplace les jetons de rotation dans les actions d'une molette.

    $(delta) garde le signe (−2, +3…), $(delta_abs) non : une commande vMix de volume veut
    « SetVolume…Value=-2 » tandis qu'un « AudioPlus » répété veut un nombre de crans positif."""
    out = copy.deepcopy(acts)
    for a in out:
        for key_, val in a.items():
            if isinstance(val, str) and "$(delta" in val:
                a[key_] = val.replace("$(delta_abs)", str(abs(ticks))).replace("$(delta)", str(ticks))
    return out


class _Runner:
    """File d'un bouton : appui puis relâché toujours dans l'ordre. Thread démon : ne bloque pas la sortie."""

    def __init__(self):
        self._q: queue.SimpleQueue = queue.SimpleQueue()
        threading.Thread(target=self._loop, name="btn", daemon=True).start()

    def submit(self, fn) -> threading.Event:
        done = threading.Event()
        self._q.put((fn, done))
        return done

    def shutdown(self) -> None:
        self._q.put(None)  # les travaux déjà en file passent avant

    def _loop(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                return
            fn, done = item
            try:
                fn()
            except Exception:
                log.exception("bouton")
            finally:
                done.set()


class Engine:
    def __init__(self, config_path=CONFIG_PATH, enable_decks: bool = True):
        self.config_path = config_path
        self.cfg, self.load_warning = open_config(config_path)
        self.save_blocked = bool(self.cfg.pop("_readonly", False))  # config.json illisible : ne jamais l'écraser
        self.listeners: list = []  # fn(kind, payload) ; kind ∈ state | config | log | decks | page | activity
        self.toggles: dict[str, bool] = {}
        self.pressed: set[str] = set()
        self._lock = threading.RLock()  # protège cfg (modifs, annuler, sauvegarde) entre threads
        self._plock = threading.RLock()  # protège l'état des appuis en cours
        self._save_lock = threading.Lock()
        self._runners: dict[str, _Runner] = {}
        self._active: dict[tuple, dict] = {}  # touche physique (deck|"ui", case) -> bouton figé à l'appui
        self._holds: dict[tuple, list] = {}  # touche -> [début, jeton, déclenché, id bouton] (appui long)
        self._latched: dict[str, tuple] = {}  # interrupteur : actions maintenues en attente du 2e appui
        self._rot: dict[tuple, float] = {}  # molettes : crans accumulés en attente du pas
        self._ids: set[str] = set()
        self.diagnostic = False
        self._undo: list = []
        self._redo: list = []
        self._last_coalesce = (None, 0.0)
        self._save_timer: threading.Timer | None = None
        self._backup_day = ""
        self.save_error = ""
        self._applied_decks: dict = {}
        self._halt = threading.Event()  # arrêt des tâches de fond du moteur (textes dynamiques)
        self.vmix = VmixClient(on_change=self._vmix_changed, on_log=self.log)
        self._inputs_sig = None
        self.health = HealthMonitor(on_change=lambda: self.emit("state"))
        self.jsonpoll = JsonPoller(on_change=lambda: self.emit("state"))
        self.levels = adjust.Levels()  # réglages au cran : dernière valeur envoyée, sans attendre vMix
        self.winaudio = winsys.CachedAudio(winsys.WinAudio())  # remplaçable dans les tests
        self.intercom = Intercom(lambda: self.emit("state"), self.log)
        self.orchestream = Orchestream(lambda: self.emit("state"), self.log)
        self.tallies = Tallies(lambda: self.emit("state"), self.log)
        self.decks = None
        if enable_decks:
            from .deck import DeckHub
            self.decks = DeckHub(self)  # Stream Deck et Loupedeck derrière la même API

    # -- cycle de vie ---------------------------------------------------------
    def start(self) -> None:
        if self.load_warning:
            self.log("error", self.load_warning)
        v = self.cfg["vmix"]
        self.vmix.start(v["host"], v["port"])
        self.health.interval = self.cfg["options"].get("health_interval", 15)
        self._update_health_targets()
        self.health.start()
        self.jsonpoll.start()
        self.apply_integrations()
        self._ids = {b["id"] for _, _, b in self.all_buttons()}
        self._applied_decks = copy.deepcopy(self.cfg["decks"])
        for s in (self.intercom, self.orchestream, self.tallies):
            s.start()
        if self.decks:
            self.decks.start()
        threading.Thread(target=self._tick_labels, name="vars", daemon=True).start()

    def stop(self) -> None:
        """Arrêt sûr : on relâche d'abord ce qui est maintenu, chaque étape est isolée, la sauvegarde en dernier."""
        self._halt.set()
        steps = [lambda: self.release_all(wait=True)]
        if self.decks:
            steps.append(self.decks.stop)
        steps += [self.vmix.stop, self.health.stop, self.jsonpoll.stop, self.intercom.stop, self.orchestream.stop, self.tallies.stop,
                  self._stop_runners, self.flush_save]
        for step in steps:
            try:
                step()
            except Exception:
                log.exception("arrêt")

    def _stop_runners(self) -> None:
        with self._plock:
            runners, self._runners = list(self._runners.values()), {}
        for r in runners:
            r.shutdown()

    # -- événements -----------------------------------------------------------
    def emit(self, kind: str, payload=None) -> None:
        if kind == "state" and self.decks:
            self.decks.request_refresh()
        for fn in list(self.listeners):
            try:
                fn(kind, payload)
            except Exception:  # un écouteur défaillant ne doit pas bloquer le moteur
                log.exception("listener")

    def log(self, level: str, msg: str) -> None:
        getattr(log, {"warn": "warning", "action": "info"}.get(level, level), log.info)(msg)
        self.emit("log", (level, msg))

    def _vmix_changed(self) -> None:
        st = self.vmix.state
        sig = tuple(i["title"] for i in st.inputs) if st.connected else None
        if sig and sig != self._inputs_sig:
            self._inputs_sig = sig
            try:
                self._heal_inputs()
            except Exception:
                log.exception("correction des noms d'input")
        self.emit("state")

    def _heal_inputs(self) -> None:
        """Remplace les noms approchés (ex. « cameraregie » importé de Companion) par le vrai titre vMix.

        Correction automatique (thread vMix) : appliquée sous verrou, sans créer d'étape « annuler »,
        et aussi dans l'historique pour qu'un « annuler » ne ramène pas l'ancien nom."""
        st = self.vmix.state

        def items(b):
            return ((b.get("actions") or []) + (b.get("actions_off") or []) + (b.get("long_actions") or [])
                    + [(f.get("cond") or {}) for f in (b.get("feedbacks") or [])])
        fixes = {}
        for _, _, b in self.all_buttons():
            for ref in (a.get("input") for a in items(b)):
                i = st.resolve(ref) if ref and not str(ref).isdigit() else None
                if i and ref not in (i["title"], i["key"]) and str(ref).lower() != i["title"].lower():
                    fixes[ref] = i["title"]
        if not fixes:
            return
        with self._lock:
            for cfg in [self.cfg, *self._undo, *self._redo]:
                for p in cfg["pages"]:
                    for b in p["buttons"].values():
                        for a in items(b):
                            if a.get("input") in fixes:
                                a["input"] = fixes[a["input"]]
        self.changed()
        self.log("info", f"{len(fixes)} nom(s) d'input remplacé(s) par leur titre vMix exact")

    # -- accès configuration --------------------------------------------------
    @property
    def pages(self) -> list[dict]:
        return self.cfg["pages"]

    def page(self, page_id: str) -> dict | None:
        return next((p for p in self.pages if p["id"] == page_id), None)

    def button(self, page_id: str, k: str) -> dict | None:
        p = self.page(page_id)
        return self.resolve(p, k) if p else None

    def resolve(self, page: dict, k: str) -> dict | None:
        """Élément commandé par une case : touche de la grille, ou contrôle hors grille
        (« c:knobTL ») pour les molettes, bandeaux et boutons physiques des Loupedeck."""
        cid = parse_control(k)
        if cid is not None:
            return (page.get("controls") or {}).get(cid)
        return page["buttons"].get(k) if self.in_grid(page, k) else None

    def control(self, page_id: str, cid: str) -> dict | None:
        p = self.page(page_id)
        return (p.get("controls") or {}).get(cid) if p else None

    def page_names(self) -> dict[str, str]:
        return {p["id"]: p["name"] for p in self.pages}

    # -- modifications (avec annuler / rétablir) ------------------------------
    def mutate(self, fn, coalesce: str | None = None) -> None:
        """Applique fn(cfg). Les frappes successives sur un même champ forment un seul « annuler »."""
        with self._lock:
            now = time.monotonic()
            last_key, last_t = self._last_coalesce
            if not (coalesce and coalesce == last_key and now - last_t < 1.5):
                self._undo.append(copy.deepcopy(self.cfg))
                del self._undo[:-100]
                self._redo.clear()
            self._last_coalesce = (coalesce, now)
            fn(self.cfg)
        self.changed()

    def undo(self) -> bool:
        with self._lock:
            if not self._undo:
                return False
            self._redo.append(copy.deepcopy(self.cfg))
            self.cfg = self._undo.pop()
            self._last_coalesce = (None, 0.0)
        self.changed()
        return True

    def redo(self) -> bool:
        with self._lock:
            if not self._redo:
                return False
            self._undo.append(copy.deepcopy(self.cfg))
            self.cfg = self._redo.pop()
            self._last_coalesce = (None, 0.0)
        self.changed()
        return True

    def apply_integrations(self) -> None:
        """Réglages des intégrations + n'activer que celles qu'une touche utilise."""
        integ = self.cfg.get("integrations", {})
        ic = integ.get("intercom", {})
        self.intercom.configure(ic.get("host", "127.0.0.1"), ic.get("port", 5080))
        orch = integ.get("orchestream") or {}
        self.orchestream.configure(orch.get("url", ""), overlay_list(orch))
        self.tallies.configure(integ.get("tallies", []))
        used = set()
        for _, _, b in self.all_buttons():
            for a in (b.get("actions") or []) + (b.get("actions_off") or []) + (b.get("long_actions") or []):
                used.add(a.get("type", "").split(".")[0])
            for f in b.get("feedbacks") or []:
                used.add((f.get("cond") or {}).get("type"))
        self.intercom.set_wanted("intercom" in used)
        self.orchestream.set_wanted("orchestream" in used)
        self.tallies.set_wanted("tally" in used)

    def _sync_settings(self) -> None:
        """Après une modif, un annuler ou un rétablir : le vMix, la santé et la luminosité suivent la config."""
        v = self.cfg["vmix"]
        if (v["host"], int(v["port"])) != (self.vmix.host, self.vmix.port):
            self.vmix.reconfigure(v["host"], int(v["port"]))
        self.health.interval = self.cfg["options"].get("health_interval", 15)
        decks = self.cfg["decks"]
        if decks != self._applied_decks:
            old, self._applied_decks = self._applied_decks, copy.deepcopy(decks)
            if self.decks:
                for serial in set(old) | set(decks):
                    if (old.get(serial) or {}).get("brightness") != (decks.get(serial) or {}).get("brightness"):
                        self.decks.apply_settings(serial)

    def changed(self) -> None:
        self._sync_settings()
        self._update_health_targets()
        self.apply_integrations()
        self._ids = {b["id"] for _, _, b in self.all_buttons()}
        self._unlatch_stale()
        self._gc_runners()
        with self._lock:
            if self._save_timer:
                self._save_timer.cancel()
            self._save_timer = threading.Timer(0.6, self.flush_save)
            self._save_timer.daemon = True
            self._save_timer.start()
        self.emit("config")
        self.emit("state")

    def flush_save(self) -> None:
        """Écrit une copie figée sous verrou (jamais pendant une modif). Une erreur est journalisée, jamais levée."""
        with self._save_lock:
            with self._lock:
                t, self._save_timer = self._save_timer, None
                text = config_text(self.cfg)
            if t:
                t.cancel()
            if self.save_blocked:
                if not self.save_error:
                    self.save_error = "enregistrement désactivé (configuration illisible au démarrage)"
                    self.log("error", "Modifications NON enregistrées : config.json était illisible au démarrage. "
                                      "Relance LiveDeck une fois le fichier libéré.")
                return
            try:
                today = time.strftime("%Y%m%d")
                if self._backup_day != today:  # une sauvegarde de secours par jour
                    self._backup_day = today
                    if not has_backup_today(self.config_path):
                        backup_config(self.config_path, "jour")
                save_config_text(text, self.config_path)
                self.save_error = ""
            except Exception as e:
                self.save_error = str(e)
                log.exception("sauvegarde")
                self.log("error", f"Sauvegarde de la configuration impossible : {e}")

    def backup(self, tag: str = "") -> None:
        """Sauvegarde de secours avant une opération lourde (import, remplacement de la configuration)."""
        self.flush_save()
        backup_config(self.config_path, tag)

    def set_button(self, page_id: str, k: str, b: dict | None, coalesce: str | None = None) -> None:
        cid = parse_control(k)

        def apply(cfg):
            p = next(p for p in cfg["pages"] if p["id"] == page_id)
            store = p.setdefault("controls", {}) if cid is not None else p["buttons"]
            target = cid if cid is not None else k
            if b is None:
                store.pop(target, None)
            elif cid is not None:
                store[target] = normalize_control(b)
            else:
                store[target] = normalize_button(b)
        # regroupement « annuler » propre à chaque touche : taper sur A puis sur B = deux étapes
        self.mutate(apply, f"{page_id}/{k}/{coalesce}" if coalesce else None)

    def move_button(self, src: tuple[str, str], dst: tuple[str, str], copy_: bool = False) -> None:
        """Déplace (échange si la case est occupée) ou copie un bouton."""
        from .model import new_id

        def apply(cfg):
            pages = {p["id"]: p for p in cfg["pages"]}
            sp, dp = pages[src[0]]["buttons"], pages[dst[0]]["buttons"]
            a, b = sp.get(src[1]), dp.get(dst[1])
            if a is None:
                return
            if copy_:
                dp[dst[1]] = {**copy.deepcopy(a), "id": new_id()}
                return
            dp[dst[1]] = a
            if b is not None:
                sp[src[1]] = b
            else:
                sp.pop(src[1], None)
        self.mutate(apply)

    def add_page(self, name: str, cols: int, rows: int, surface: str = "") -> str:
        p = new_page(name, cols, rows, surface)
        self.mutate(lambda cfg: cfg["pages"].append(p))
        return p["id"]

    def update_page(self, page_id: str, **fields) -> None:
        def apply(cfg):
            p = next(p for p in cfg["pages"] if p["id"] == page_id)
            p.update(fields)
            # les boutons hors de la nouvelle grille sont conservés mais masqués
        self.mutate(apply)

    def remove_page(self, page_id: str) -> None:
        if len(self.pages) <= 1:
            return
        self.mutate(lambda cfg: cfg.__setitem__("pages", [p for p in cfg["pages"] if p["id"] != page_id]))

    def move_page(self, page_id: str, delta: int) -> None:
        def apply(cfg):
            ps = cfg["pages"]
            i = next(i for i, p in enumerate(ps) if p["id"] == page_id)
            j = max(0, min(len(ps) - 1, i + delta))
            ps.insert(j, ps.pop(i))
        self.mutate(apply)

    def set_vmix(self, host: str, port: int) -> None:
        v = {"host": host, "port": int(port)}
        if self.cfg["vmix"] != v:
            self.mutate(lambda cfg: cfg.__setitem__("vmix", v))  # _sync_settings reconnecte le client

    def set_deck_settings(self, serial: str, **fields) -> None:
        old = dict(self.cfg["decks"].get(serial) or {})
        new = {**old, **fields}
        if new == old:
            return
        self.mutate(lambda cfg: cfg["decks"].__setitem__(serial, new))
        if self.decks and "page" in fields and fields["page"] != old.get("page"):
            self.decks.apply_settings(serial, page_changed=True)  # sinon le deck reste sur sa page actuelle

    def replace_config(self, cfg: dict) -> None:
        self.backup("avant-remplacement")
        self.mutate(lambda c: (c.clear(), c.update(cfg)))
        if self.decks:
            self.decks.reassign_pages()

    # -- exécution ------------------------------------------------------------
    # Chaque appui fige une copie du bouton, rangée par touche physique (numéro de série du deck, ou
    # « ui » pour l'écran) : le relâché s'applique à cette copie même si la page a changé, ou si le
    # bouton a été modifié ou supprimé entre-temps. Les actions « maintenues » (raccourci maintenu,
    # intercom en mode maintenu) qui ont reçu leur appui attendent leur relâché dans snap["held"].
    #
    # Règle des actions maintenues sur un bouton spécial :
    #  - appui long : elles partent dès l'appui et s'arrêtent au relâché (le court / long ne concerne
    #    que les autres actions) ;
    #  - sécurité (maintenir pour déclencher) : elles partent une fois le délai atteint, jusqu'au relâché ;
    #  - interrupteur : 1er appui = elles commencent, 2e appui = elles s'arrêtent (verrouillage).
    def press(self, page_id: str, k: str, down: bool, deck: str | None = None, test: bool = False) -> None:
        """Appui / relâché d'une touche. test : bouton « Tester » (la sécurité ne s'applique pas)."""
        tok = (deck or ("test" if test else "ui"), k)
        if down:
            with self._lock:
                p = self.page(page_id)
                b = self.resolve(p, k) if p else None
                b = copy.deepcopy(b) if b else None
            if deck and p:
                # Même sans action : appuyer une touche vide sur l'appareil doit la sélectionner
                # dans l'éditeur. C'est ainsi qu'on câble une surface, touche après touche.
                self.emit("activity", (page_id, k, deck))
            if not b:
                return
            with self._plock:
                if tok in self._active:  # relâché perdu : on libère l'appui précédent d'abord
                    self._release(self._active.pop(tok), tok)
                snap = {"page": page_id, "k": k, "b": b, "deck": deck, "held": [], "test": test}
                self._active[tok] = snap
                self.pressed.add(b["id"])
                self._down(snap, tok)
            if not deck or not p:  # déjà signalé plus haut pour un appui sur l'appareil
                self.emit("activity", (page_id, k, deck))
            self._recheck_health(b)
        else:
            with self._plock:
                snap = self._active.pop(tok, None)
                if snap is None:
                    return
                self._release(snap, tok)
        self.emit("state")

    def rotate(self, page_id: str, cid: str, delta: int, deck: str | None = None) -> None:
        """Cran de molette. Les crans sont accumulés jusqu'au « pas » du contrôle : un pas de 4
        sur une molette rapide évite d'inonder vMix de commandes. Le reste est gardé, donc une
        rotation lente finit toujours par déclencher au lieu d'être perdue."""
        with self._lock:
            p = self.page(page_id)
            c = copy.deepcopy((p.get("controls") or {}).get(cid)) if p else None
        if deck and p:
            # Même libre : tourner une molette la sélectionne dans l'éditeur (« suivre le deck »).
            self.emit("activity", (page_id, control_key(cid), deck))
        if not c or c.get("kind") != "rotary":
            return
        step = max(1, int(c.get("step") or 1))
        tok = (deck or "ui", cid)
        with self._plock:
            acc = self._rot.get(tok, 0) + delta
            if acc and (acc > 0) != (delta > 0):  # changement de sens : on repart de zéro
                acc = delta
            ticks = int(acc / step)
            self._rot[tok] = acc - ticks * step
        if not ticks:
            return
        acts = c.get("actions_cw" if ticks > 0 else "actions_ccw") or []
        if not acts:
            # Une action « au cran » (volume, défilement…) placée d'un seul côté vaut pour les deux sens :
            # c'est la rotation qui décide de monter ou de baisser. Inutile de la configurer deux fois.
            other = c.get("actions_ccw" if ticks > 0 else "actions_cw") or []
            if other and all(actions.is_relative(a) for a in other):
                acts = other
        if not acts:
            return
        if not deck or not p:
            self.emit("activity", (page_id, control_key(cid), deck))
        self._run(c, _with_delta(acts, ticks), ("down", "up"), deck, {"delta": ticks})
        self.emit("state")

    def trigger(self, page_id: str, k: str, deck: str | None = None) -> None:
        """« Tester » depuis un menu : exécute l'action principale tout de suite (sans sécurité ni appui long)."""
        with self._lock:
            b = copy.deepcopy(self.button(page_id, k))
        if not b:
            return
        self.emit("activity", (page_id, k, deck))
        with self._plock:
            self._fire_main(b, deck)
        self._recheck_health(b)
        self.emit("state")

    def _down(self, snap: dict, tok: tuple) -> None:
        b, deck = snap["b"], snap["deck"]
        confirm = b.get("confirm") and not snap["test"]
        if b.get("mode") == "toggle":
            if confirm:  # interrupteur sécurisé : il ne bascule qu'après le maintien
                self._press_deferred(snap, tok, [], True)
            else:
                self._fire_main(b, deck)
            return
        long_acts = b.get("long_actions") or []
        if confirm or long_acts:
            if not confirm:  # appui long : les actions maintenues partent tout de suite
                self._fire_held(snap, actions.momentary(b.get("actions")))
            self._press_deferred(snap, tok, long_acts, confirm)
        else:
            self._fire_held(snap, b.get("actions") or [])

    def _fire_held(self, snap: dict, acts: list) -> None:
        """Phase « appui » ; les actions maintenues attendent le relâché de la touche."""
        self._run(snap["b"], acts, ("down",), snap["deck"])
        snap["held"] += actions.momentary(acts)

    def _release(self, snap: dict, tok: tuple, short: bool = True) -> threading.Event | None:
        """Relâché (appelé sous _plock) : appui court éventuel, puis fin des actions maintenues."""
        b, deck = snap["b"], snap["deck"]
        hold = self._holds.pop(tok, None)
        if short and hold is not None and not hold[2] and not (b.get("confirm") and not snap["test"]):
            acts = [a for a in b.get("actions") or [] if not actions.is_momentary(a)]
            self._run(b, acts, ("down", "up"), deck)  # appui court
        held, snap["held"] = snap["held"], []
        done = self._run(b, held, ("up",), deck)
        if not any(s["b"]["id"] == b["id"] for s in self._active.values()):
            self.pressed.discard(b["id"])
        if b["id"] not in self._ids:
            self._gc_runners()
        return done

    def release_all(self, deck: str | None = None, wait: bool = False, latched: bool | None = None) -> None:
        """Relâche tout ce qui est maintenu : touches enfoncées (d'un deck, ou toutes) et interrupteurs
        verrouillés (latched, par défaut si deck est None). Appelé à l'arrêt (wait=True), au débranchement
        d'un deck, à l'entrée du diagnostic. Sans deck précis, coupe aussi tous les bus intercom si l'un d'eux
        était maintenu."""
        latched = deck is None if latched is None else latched
        events, intercom = [], False
        with self._plock:
            for tok in [t for t in self._active if deck is None or t[0] == deck]:
                snap = self._active.pop(tok)
                intercom |= any(a.get("type") == "intercom.ptt" for a in snap["held"])
                events.append(self._release(snap, tok, short=False))
            if latched:
                for bid, (b, acts, dk) in list(self._latched.items()):
                    intercom |= any(a.get("type") == "intercom.ptt" for a in acts)
                    events.append(self._run(b, acts, ("up",), dk))
                    self.toggles[bid] = False
                self._latched.clear()
        events = [e for e in events if e is not None]
        if not events and not intercom:
            return

        def finish():
            end = time.monotonic() + 2.5
            for ev in events:
                ev.wait(max(0.0, end - time.monotonic()))
            if intercom and deck is None:  # arrêt : aucun micro ne doit rester ouvert (un seul deck débranché :
                # le « off » de chaque bus relâché suffit, sans couper les autres decks ni les bascules du client)
                self.intercom.command("all", "off", quick=wait)
            self.emit("state")
        if wait:
            finish()
        else:
            threading.Thread(target=finish, name="release", daemon=True).start()

    def _fire_main(self, b: dict, deck) -> None:
        """Déclenche l'action principale en entier (appui + relâché ; interrupteur : bascule)."""
        if b.get("mode") == "toggle":
            bid = b["id"]
            on = not self.toggles.get(bid, False)
            self.toggles[bid] = on
            if on:
                self._run(b, b.get("actions") or [], ("down",), deck)
                mom = actions.momentary(b.get("actions"))
                if mom:  # maintenu dans un interrupteur : verrouillé jusqu'au prochain appui
                    self._latched[bid] = (b, mom, deck)
            else:
                lat = self._latched.pop(bid, None)
                if lat:
                    self._run(lat[0], lat[1], ("up",), lat[2])
                self._run(b, b.get("actions_off") or [], ("down", "up"), deck)
        else:
            self._run(b, b.get("actions") or [], ("down", "up"), deck)

    def _press_deferred(self, snap: dict, tok: tuple, long_acts: list, confirm: bool) -> None:
        """Appui court au relâché, appui long après LONG_PRESS (ou seulement l'appui long si sécurité)."""
        token = object()
        self._holds[tok] = [time.monotonic(), token, False, snap["b"]["id"]]

        def watch():
            step = 0
            while True:
                time.sleep(0.04)
                with self._plock:  # décision atomique : jamais l'appui court ET l'appui long
                    hold = self._holds.get(tok)
                    if not hold or hold[1] is not token:
                        break
                    elapsed = time.monotonic() - hold[0]
                    if elapsed >= LONG_PRESS:
                        hold[2] = True
                        if confirm and snap["b"].get("mode") == "toggle":
                            self._fire_main(snap["b"], snap["deck"])
                        else:
                            self._fire_held(snap, snap["b"].get("actions") if confirm else long_acts)
                        break
                s = int(elapsed / LONG_PRESS * 12)
                if s != step:  # jauge : au plus 12 rafraîchissements
                    step = s
                    self.emit("state")
            self.emit("state")
        threading.Thread(target=watch, name="long-press", daemon=True).start()

    def _run(self, b: dict, acts: list[dict], phases: tuple, deck, extra: dict | None = None) -> threading.Event | None:
        if not acts:
            return None
        with self._plock:
            runner = self._runners.get(b["id"])
            if runner is None:  # un exécuteur par bouton : appui puis relâché toujours dans l'ordre
                runner = self._runners[b["id"]] = _Runner()
        acts = copy.deepcopy(acts)
        label = (b.get("label") or "").replace("\n", " ")
        names = self.page_names()

        def job():
            for phase in phases:
                for a in acts:
                    try:
                        if phase == "down":
                            self.log("action", f"{label or 'Bouton'} — {actions.summary(a, names)}")
                        actions.execute(self, a, phase, {"deck": deck, **(extra or {})})
                    except Exception as e:
                        self.log("error", f"{label or 'Bouton'} : {actions.summary(a, names)} → {e}")
        return runner.submit(job)

    def _unlatch_stale(self) -> None:
        """Un interrupteur verrouillé (talkback, raccourci maintenu) dont le bouton a disparu, n'est plus un
        interrupteur ou n'a plus les mêmes actions maintenues (suppression, annuler, import…) est relâché :
        sinon le micro ou la touche resteraient tenus jusqu'à la fermeture de LiveDeck."""
        if not self._latched:
            return
        with self._lock:
            cur = {b["id"]: b for _, _, b in self.all_buttons()}
        with self._plock:
            for bid, (b, acts, dk) in list(self._latched.items()):
                nb = cur.get(bid)
                if nb and nb.get("mode") == "toggle" and actions.momentary(nb.get("actions")) == acts:
                    continue
                self._latched.pop(bid)
                self.toggles[bid] = False
                self._run(b, acts, ("up",), dk)  # « up » d'un intercom maintenu = « off » de ce bus

    def _gc_runners(self) -> None:
        """Libère l'exécuteur des boutons disparus (sauf s'ils ont encore un relâché à faire)."""
        with self._plock:
            busy = {s["b"]["id"] for s in self._active.values()} | set(self._latched)
            for bid in [bid for bid in self._runners if bid not in self._ids and bid not in busy]:
                self._runners.pop(bid).shutdown()

    def goto_page(self, deck: str | None, target: str) -> None:
        if deck and self.decks:
            self.decks.goto(deck, target)
        else:
            self.emit("page", target)

    # -- santé réseau ---------------------------------------------------------
    def _health_conds(self, b: dict, kind: str = "health"):
        return [f["cond"] for f in b.get("feedbacks") or [] if (f.get("cond") or {}).get("type") == kind]

    def _update_health_targets(self) -> None:
        """Équipements à surveiller (ping / HTTP) et API JSON à lire, d'après les retours d'état."""
        addrs, urls = set(), {}
        for _, _, b in self.all_buttons():
            for c in self._health_conds(b):
                addrs.update(a for _, a in parse_targets(c.get("targets", "")))
            for c in self._health_conds(b, "http_json"):
                url = (c.get("url") or "").strip()
                if url.lower().startswith(("http://", "https://")):
                    every = float(c.get("interval") or 5)
                    urls[url] = min(urls.get(url, every), every)
        self.health.set_targets(addrs)
        self.jsonpoll.set_targets(urls)

    def _recheck_health(self, b: dict) -> None:
        for c in self._health_conds(b, "http_json"):  # l'action vient peut-être de changer la valeur lue
            if (c.get("url") or "").strip() in self.jsonpoll.targets:
                t = threading.Timer(0.4, self.jsonpoll.check_now, args=((c.get("url") or "").strip(),))
                t.daemon = True
                t.start()
        addrs = {a for c in self._health_conds(b) for _, a in parse_targets(c.get("targets", ""))}
        if addrs:  # après l'action (ex. « reconnect »), on revérifie un peu plus tard
            t = threading.Timer(1.5, self.health.check_now, args=(addrs,))
            t.daemon = True
            t.start()

    # -- apparence en direct --------------------------------------------------
    def _tick_labels(self) -> None:
        """Textes dynamiques qui changent sans événement vMix (temps restant, durée d'enregistrement, heure) :
        on recalcule les libellés à jetons 2 fois par seconde et on ne redessine que s'ils ont changé."""
        last: dict = {}
        while not self._halt.wait(0.5):
            try:
                cur = {}
                for _, _, b in self.all_buttons():
                    texts = [b.get("label") or ""] + [f.get("label") or "" for f in b.get("feedbacks") or []]
                    if any(textvars.has_tokens(t) for t in texts):
                        cur[b["id"]] = tuple(textvars.expand(t, self) for t in texts)
                    # Niveaux (volume vMix, son et micro Windows) : ils changent aussi hors de LiveDeck,
                    # sans qu'aucun événement ne le signale. On compare l'apparence obtenue.
                    fbs = (feedback.auto(b) if b.get("auto_feedback", True) else []) + list(b.get("feedbacks") or [])
                    if any((f.get("cond") or {}).get("type") in LIVE_CONDS for f in fbs):
                        v = self.visual(b)
                        cur[(b["id"], "v")] = tuple(sorted((v or {}).items()))
                if cur != last:
                    last = cur
                    self.emit("state")
            except Exception:
                log.exception("textes dynamiques")

    def visual(self, b: dict | None) -> dict | None:
        if not b:
            return None
        v = {"bg": b.get("bg"), "fg": b.get("fg"), "label": b.get("label", ""), "size": b.get("size", 0),
             "icon": b.get("icon", ""), "bar": "", "pressed": b["id"] in self.pressed, "warn": False, "lit": ""}
        fbs = (feedback.auto(b) if b.get("auto_feedback", True) else []) + list(b.get("feedbacks") or [])
        for f in fbs:
            ok, extra = feedback.evaluate(self, b, f.get("cond", {}))
            if ok:
                v["lit"] = f.get("bg") or v["lit"]
                for k in ("bg", "fg", "bar", "label"):
                    if f.get(k):
                        v[k] = f[k]
                if extra.get("label") and not f.get("label"):
                    v["label"] = extra["label"]
                if "progress" in extra:  # jauge d'un niveau (volume réglé par une molette)
                    v["progress"] = extra["progress"]
        if textvars.has_tokens(v["label"]):
            v["label"] = textvars.expand(v["label"], self)
        v["warn"] = bool(self.unresolved_inputs(b))
        hold = next((h for h in list(self._holds.values()) if h[3] == b["id"]), None)
        if hold and not hold[2]:
            v["progress"] = round(min(1.0, (time.monotonic() - hold[0]) / LONG_PRESS) * 12) / 12
        return v

    def unresolved_inputs(self, b: dict) -> list[str]:
        """Inputs référencés par le bouton mais introuvables dans vMix (si connecté)."""
        st = self.vmix.state
        if not st.connected or not st.inputs:
            return []
        refs = [a.get("input") for a in (b.get("actions") or []) + (b.get("actions_off") or [])
                + (b.get("long_actions") or [])]
        return [r for r in refs if r and st.resolve(r) is None]

    def describe_auto(self, b: dict) -> list[str]:
        return [feedback.describe(f) for f in feedback.auto(b)]

    def assign_input(self, page_id: str, k: str, title: str, kind: str) -> None:
        """Lie un input à une touche : change l'input d'un bouton vMix existant, sinon crée le bouton."""
        from .presets import input_button
        b = self.button(page_id, k)
        first = next((a for a in (b or {}).get("actions", []) if "input" in a), None)
        if b and first is not None:
            b = copy.deepcopy(b)
            a = next(a for a in b["actions"] if "input" in a)
            old = a.get("input") or ""
            a["input"] = title
            if b["label"] in ("", old):
                b["label"] = title
            elif b["label"] == f"PIP {old}":
                b["label"] = f"PIP {title}"
            self.set_button(page_id, k, b)
        else:
            self.set_button(page_id, k, input_button(title, kind))

    def send_to_page(self, page_id: str, k: str, dst_page: str) -> str | None:
        """Déplace une touche vers la première case libre d'une autre page."""
        free = self.first_free(dst_page)
        b = self.button(page_id, k)
        if not free or not b:
            return None

        def apply(cfg):
            pages = {p["id"]: p for p in cfg["pages"]}
            pages[dst_page]["buttons"][free] = pages[page_id]["buttons"].pop(k)
        self.mutate(apply)
        return free

    def all_buttons(self) -> list:
        """(page, case, bouton) de toute la config, lus sous verrou (sûr depuis n'importe quel thread).

        Inclut les contrôles hors grille (molettes, bandeaux, boutons des Loupedeck), repérés par
        une case « c:<id> » : ils ont les mêmes retours d'état, textes dynamiques et surveillance
        réseau que les touches."""
        with self._lock:
            out = [(p, k, b) for p in self.cfg["pages"] for k, b in p["buttons"].items()]
            out += [(p, control_key(cid), c) for p in self.cfg["pages"]
                    for cid, c in (p.get("controls") or {}).items()]
            return out

    def first_free(self, page_id: str) -> str | None:
        p = self.page(page_id)
        for r in range(p["rows"]):
            for c in range(p["cols"]):
                if f"{r},{c}" not in p["buttons"]:
                    return f"{r},{c}"
        return None

    def in_grid(self, page: dict, k: str) -> bool:
        try:
            r, c = parse_key(k)
        except ValueError:
            return False
        return r < page["rows"] and c < page["cols"]
