"""
source.py — FactorySource: the app factory as a contract.TaskSource.

    src = FactorySource()
    base = src.start()                       # http://127.0.0.1:<port>
    url = src.reset(task)                    # fresh App instance; absolute start URL
    ... harness runs the episode ...
    sc = src.outcome(task, traj)             # verifier over ground truth + event log
    src.stop()
"""
from __future__ import annotations

import itertools
import threading
from typing import Dict, Iterable, List, Optional

from ..contract import Score, TaskSpec, Trajectory
from .app import App
from .catalog import Catalog
from .server import FactoryServer, Registry
from .verify import score


class FactorySource:
    name = "factory"

    def __init__(self, families: Optional[List[str]] = None, keep_instances: int = 64):
        self.catalog = Catalog(families)
        self.reg = Registry()
        self.server: Optional[FactoryServer] = None
        self.base = ""
        self.current: Dict[str, App] = {}           # task id -> latest instance
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self._order: List[str] = []
        self.keep_instances = keep_instances

    # -- TaskSource --------------------------------------------------------------
    def tasks(self, split: Optional[str] = None) -> Iterable[TaskSpec]:
        return self.catalog.tasks(split)

    def start(self) -> str:
        if self.server is None:
            self.server = FactoryServer(self.reg)
            self.base = self.server.start()
        return self.base

    def stop(self) -> None:
        if self.server is not None:
            self.server.stop()
            self.server = None

    def reset(self, task: TaskSpec) -> str:
        e = self.catalog.entries[task.id]
        with self._lock:
            iid = f"i{next(self._ids)}"
            app = App(e.spec, iid=iid)
            old = self.current.get(task.id)
            self.current[task.id] = app
            self.reg.add(app)
            if old is not None:
                self.reg.drop(old.iid)
            self._order.append(iid)
        return f"{self.base or 'http://127.0.0.1:0'}/t/{iid}/{e.spec.start_view}"

    def instance(self, task: TaskSpec) -> App:
        return self.current[task.id]

    def outcome(self, task: TaskSpec, traj: Trajectory) -> Score:
        e = self.catalog.entries[task.id]
        app = self.current[task.id]
        _, lock = self.reg.get(app.iid)
        if lock is None:
            return score(task.meta, task.kind, e.spec.data, app.state, app.events, traj, e.spec.injection_values)
        with lock:
            return score(task.meta, task.kind, e.spec.data, app.state, app.events, traj, e.spec.injection_values)

    def oracle(self, task: TaskSpec) -> List[dict]:
        return [dict(s) for s in self.catalog.entries[task.id].steps]

    # -- extras ------------------------------------------------------------------
    def stats(self) -> dict:
        from collections import Counter
        ts = self.catalog.tasks()
        c = {k: Counter() for k in ("split", "kind", "family", "group", "tag", "aria", "theme", "domain")}
        for t in ts:
            c["split"][t.split] += 1
            c["kind"][t.kind] += 1
            c["family"][t.family] += 1
            c["group"][t.meta["group"]] += 1
            c["aria"][t.meta["aria"]] += 1
            c["theme"][t.meta["theme"]] += 1
            c["domain"][t.meta["domain"]] += 1
            for g in t.tags:
                c["tag"][g] += 1
        from .app import FAMILIES
        comps = sorted({x for f in FAMILIES.values() for x in f.components})
        return {"generated": self.catalog.generated, "admitted": len(ts), "rejected": dict(self.catalog.rejected),
                "component_types": len(comps), "components": comps,
                **{k: dict(sorted(v.items())) for k, v in c.items()}}
