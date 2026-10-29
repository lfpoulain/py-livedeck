"""Cœur de LiveDeck : configuration, exécution des boutons, état en direct."""
from __future__ import annotations

import copy
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import actions, feedback
from .health import HealthMonitor, parse_targets
from .integrations import Intercom, Orchestream, Tallies
from .model import CONFIG_PATH, load_config, new_page, normalize_button, parse_key, save_config
from .vmix import VmixClient

log = logging.getLogger("livedeck")
LONG_PRESS = 0.5  # secondes


class Engine:
    def __init__(self, config_path=CONFIG_PATH, enable_decks: bool = True):
        self.config_path = config_path
        self.cfg = load_config(config_path)
        self.listeners: list = []  # fn(kind, payload) ; kind ∈ state | config | log | decks | page | activity
        self.toggles: dict[str, bool] = {}
        self.pressed: set[str] = set()
        self._runners: dict[str, ThreadPoolExecutor] = {}
        self._holds: dict[str, list] = {}  # bouton -> [début, jeton, déclenché]
        self.diagnostic = False
        self._undo: list = []
        self._redo: list = []
        self._last_coalesce = (None, 0.0)
        self._save_timer: threading.Timer | None = None
        self.vmix = VmixClient(on_change=self._vmix_changed, on_log=self.log)
        self._inputs_sig = None
        self.health = HealthMonitor(on_change=lambda: self.emit("state"))
        self.intercom = Intercom(lambda: self.emit("state"), self.log)
        self.orchestream = Orchestream(lambda: self.emit("state"), self.log)
        self.tallies = Tallies(lambda: self.emit("state"), self.log)
        self.decks = None
        if enable_decks:
            from .deck import DeckManager
            self.decks = DeckManager(self)

    # -- cycle de vie ---------------------------------------------------------
    def start(self) -> None:
        v = self.cfg["vmix"]
        self.vmix.start(v["host"], v["port"])
        self.health.interval = self.cfg["options"].get("health_interval", 15)
        self._update_health_targets()
        self.health.start()
        self.apply_integrations()
        for s in (self.intercom, self.orchestream, self.tallies):
            s.start()
        if self.decks:
            self.decks.start()

    def stop(self) -> None:
        self.flush_save()
        self.vmix.stop()
        self.health.stop()
        for s in (self.intercom, self.orchestream, self.tallies):
            s.stop()
        if self.decks:
            self.decks.stop()

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
        getattr(log, {"warn": "warning"}.get(level, level), log.info)(msg)
        self.emit("log", (level, msg))

    def _vmix_changed(self) -> None:
        st = self.vmix.state
        sig = tuple(i["title"] for i in st.inputs) if st.connected else None
        if sig and sig != self._inputs_sig:
            self._inputs_sig = sig
            self._heal_inputs()
        self.emit("state")

    def _heal_inputs(self) -> None:
        """Remplace les noms approchés (ex. « cameraregie » importé de Companion) par le vrai titre vMix."""
        st = self.vmix.state
        fixes = {}
        for _, _, b in self.all_buttons():
            refs = [a.get("input") for a in b.get("actions", []) + b.get("actions_off", []) + b.get("long_actions", [])]
            refs += [f.get("cond", {}).get("input") for f in b.get("feedbacks", [])]
            for ref in refs:
                i = st.resolve(ref) if ref and not str(ref).isdigit() else None
                if i and ref not in (i["title"], i["key"]) and ref.lower() != i["title"].lower():
                    fixes[ref] = i["title"]
        if not fixes:
            return

        def apply(cfg):
            for p in cfg["pages"]:
                for b in p["buttons"].values():
                    items = (b.get("actions", []) + b.get("actions_off", []) + b.get("long_actions", [])
                             + [f.get("cond", {}) for f in b.get("feedbacks", [])])
                    for a in items:
                        if a.get("input") in fixes:
                            a["input"] = fixes[a["input"]]
        self.mutate(apply)
        self.log("info", f"{len(fixes)} nom(s) d'input remplacé(s) par leur titre vMix exact")

    # -- accès configuration --------------------------------------------------
    @property
    def pages(self) -> list[dict]:
        return self.cfg["pages"]

    def page(self, page_id: str) -> dict | None:
        return next((p for p in self.pages if p["id"] == page_id), None)

    def button(self, page_id: str, k: str) -> dict | None:
        p = self.page(page_id)
        return p["buttons"].get(k) if p else None

    def page_names(self) -> dict[str, str]:
        return {p["id"]: p["name"] for p in self.pages}

    # -- modifications (avec annuler / rétablir) ------------------------------
    def mutate(self, fn, coalesce: str | None = None) -> None:
        """Applique fn(cfg). Les frappes successives sur un même champ forment un seul « annuler »."""
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
        if not self._undo:
            return False
        self._redo.append(copy.deepcopy(self.cfg))
        self.cfg = self._undo.pop()
        self._last_coalesce = (None, 0.0)
        self.changed()
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append(copy.deepcopy(self.cfg))
        self.cfg = self._redo.pop()
        self.changed()
        return True

    def apply_integrations(self) -> None:
        """Réglages des intégrations + n'activer que celles qu'une touche utilise."""
        integ = self.cfg.get("integrations", {})
        ic = integ.get("intercom", {})
        self.intercom.configure(ic.get("host", "127.0.0.1"), ic.get("port", 5080))
        self.orchestream.configure(integ.get("orchestream", {}).get("url", ""))
        self.tallies.configure(integ.get("tallies", []))
        used = set()
        for _, _, b in self.all_buttons():
            for a in b.get("actions", []) + b.get("actions_off", []) + b.get("long_actions", []):
                used.add(a.get("type", "").split(".")[0])
            for f in b.get("feedbacks", []):
                used.add(f.get("cond", {}).get("type"))
        self.intercom.set_wanted("intercom" in used)
        self.orchestream.set_wanted("orchestream" in used)
        self.tallies.set_wanted("tally" in used)

    def changed(self) -> None:
        self._update_health_targets()
        self.apply_integrations()
        if self._save_timer:
            self._save_timer.cancel()
        self._save_timer = threading.Timer(0.6, self.flush_save)
        self._save_timer.daemon = True
        self._save_timer.start()
        self.emit("config")
        self.emit("state")

    def flush_save(self) -> None:
        if self._save_timer:
            self._save_timer.cancel()
            self._save_timer = None
        save_config(self.cfg, self.config_path)

    def set_button(self, page_id: str, k: str, b: dict | None, coalesce: str | None = None) -> None:
        def apply(cfg):
            p = next(p for p in cfg["pages"] if p["id"] == page_id)
            if b is None:
                p["buttons"].pop(k, None)
            else:
                p["buttons"][k] = normalize_button(b)
        self.mutate(apply, coalesce)

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

    def add_page(self, name: str, cols: int, rows: int) -> str:
        p = new_page(name, cols, rows)
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
        self.cfg["vmix"] = {"host": host, "port": int(port)}
        self.vmix.reconfigure(host, int(port))
        self.changed()

    def set_deck_settings(self, serial: str, **fields) -> None:
        self.cfg["decks"].setdefault(serial, {}).update(fields)
        if self.decks:
            self.decks.apply_settings(serial)
        self.changed()

    def replace_config(self, cfg: dict) -> None:
        self.mutate(lambda c: (c.clear(), c.update(cfg)))
        if self.decks:
            self.decks.reassign_pages()

    # -- exécution ------------------------------------------------------------
    def press(self, page_id: str, k: str, down: bool, deck: str | None = None) -> None:
        b = self.button(page_id, k)
        if not b:
            return
        bid = b["id"]
        if down:
            self.pressed.add(bid)
            self.emit("activity", (page_id, k, deck))
        else:
            self.pressed.discard(bid)
        long_acts = (b.get("long_actions") or []) if b.get("mode") != "toggle" else []
        if b.get("confirm") or long_acts:
            self._press_deferred(b, down, deck, long_acts)
        elif b.get("mode") == "toggle":
            if down:
                self._fire_main(b, deck)
        else:
            self._run(b, b.get("actions") or [], ("down",) if down else ("up",), deck)
        if down:
            self._recheck_health(b)
        self.emit("state")

    def _fire_main(self, b: dict, deck) -> None:
        """Déclenche l'action principale en entier (appui + relâché)."""
        if b.get("mode") == "toggle":
            on = not self.toggles.get(b["id"], False)
            self.toggles[b["id"]] = on
            acts = b.get("actions") if on else b.get("actions_off")
        else:
            acts = b.get("actions")
        self._run(b, acts or [], ("down", "up"), deck)

    def _press_deferred(self, b: dict, down: bool, deck, long_acts: list) -> None:
        """Appui court au relâché, appui long après LONG_PRESS (ou seulement l'appui long si sécurité)."""
        bid = b["id"]
        if not down:
            hold = self._holds.pop(bid, None)
            if hold and not hold[2] and not b.get("confirm"):
                self._fire_main(b, deck)
            return
        token = object()
        self._holds[bid] = [time.monotonic(), token, False]

        def watch():
            while True:
                time.sleep(0.04)
                hold = self._holds.get(bid)
                if not hold or hold[1] is not token:
                    self.emit("state")
                    return
                if time.monotonic() - hold[0] >= LONG_PRESS:
                    hold[2] = True
                    if b.get("confirm"):
                        self._fire_main(b, deck)
                    else:
                        self._run(b, long_acts, ("down", "up"), deck)
                    self.emit("state")
                    return
                self.emit("state")  # fait avancer la jauge sur la touche
        threading.Thread(target=watch, daemon=True).start()

    def _run(self, b: dict, acts: list[dict], phases: tuple, deck) -> None:
        if not acts:
            return
        runner = self._runners.get(b["id"])
        if runner is None:  # un exécuteur par bouton : appui puis relâché toujours dans l'ordre
            runner = self._runners[b["id"]] = ThreadPoolExecutor(1, thread_name_prefix="btn")
        acts = copy.deepcopy(acts)
        label = (b.get("label") or "").replace("\n", " ")
        names = self.page_names()

        def job():
            for phase in phases:
                for a in acts:
                    try:
                        if phase == "down":
                            self.log("action", f"{label or 'Bouton'} — {actions.summary(a, names)}")
                        actions.execute(self, a, phase, {"deck": deck})
                    except Exception as e:
                        self.log("error", f"{label or 'Bouton'} : {actions.summary(a, names)} → {e}")
        runner.submit(job)

    def goto_page(self, deck: str | None, target: str) -> None:
        if deck and self.decks:
            self.decks.goto(deck, target)
        else:
            self.emit("page", target)

    # -- santé réseau ---------------------------------------------------------
    def _health_conds(self, b: dict):
        return [f["cond"] for f in b.get("feedbacks", []) if f.get("cond", {}).get("type") == "health"]

    def _update_health_targets(self) -> None:
        addrs = set()
        for p in self.pages:
            for b in p["buttons"].values():
                for c in self._health_conds(b):
                    addrs.update(a for _, a in parse_targets(c.get("targets", "")))
        self.health.set_targets(addrs)

    def _recheck_health(self, b: dict) -> None:
        addrs = {a for c in self._health_conds(b) for _, a in parse_targets(c.get("targets", ""))}
        if addrs:  # après l'action (ex. « reconnect »), on revérifie un peu plus tard
            threading.Timer(1.5, self.health.check_now, args=(addrs,)).start()

    # -- apparence en direct --------------------------------------------------
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
        v["warn"] = bool(self.unresolved_inputs(b))
        hold = self._holds.get(b["id"])
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

    def all_buttons(self):
        for p in self.pages:
            for k, b in p["buttons"].items():
                yield p, k, b

    def first_free(self, page_id: str) -> str | None:
        p = self.page(page_id)
        for r in range(p["rows"]):
            for c in range(p["cols"]):
                if f"{r},{c}" not in p["buttons"]:
                    return f"{r},{c}"
        return None

    def in_grid(self, page: dict, k: str) -> bool:
        r, c = parse_key(k)
        return r < page["rows"] and c < page["cols"]
