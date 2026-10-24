"""
source.py — MiniWoB++ as a contract.TaskSource (``MiniWobSource``).

One TaskSpec per (task type, seed). The goal is the EXACT MiniWoB utterance for that seed,
precomputed by rendering each page once in Chromium (``python -m scaffold.rl.miniwob
utterances``) and frozen in data/utterances.json (plus an optional cache file for extra
training seeds). A (type, seed) with no precomputed utterance falls back to the goal
"Follow the instruction shown at the top of the page." and is tagged ``goal_on_page``.
At run time the shim reports the live utterance; a mismatch is written into Score.notes.

Password tasks (enter-password, login-user*): MicroAgent only fills password fields with
secret handles, so the goal shows ``{{secret:password}}`` in place of the literal and the
value is in ``meta["secrets"]["password"]`` (the harness must load it into its secret
store). ``secrets_in = ["password"]``.

Scoring: success = MiniWoB raw reward > 0 (BrowserGym's convention); the raw reward,
time-adjusted reward and reason go into notes; progress = max(0, raw reward). MiniWoB has
no safety semantics, so ``unsafe`` is always empty.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

from scaffold.rl.contract import Score, TaskSpec, Trajectory

from . import catalog
from .oracles import oracle_for
from .server import MiniWobServer

PAGE_GOAL = "Follow the instruction shown at the top of the page."
REPO_ROOT = Path(__file__).resolve().parents[3]
FETCH_SCRIPT = REPO_ROOT / "scripts" / "rl" / "fetch_miniwob.sh"
_PW_RE = re.compile(r'(password\s+")([^"]+)(")', re.IGNORECASE)


def default_html_root() -> Path:
    env = os.environ.get("AWOS_MINIWOB_HTML")
    if env:
        return Path(env).expanduser()
    cache = Path(os.environ.get("AWOS_RL_CACHE", "~/.cache/awos-rl")).expanduser()
    return cache / "miniwob" / catalog.PINNED_SHA / "html"


def ensure_html(root: Optional[Path] = None, fetch: bool = True) -> Path:
    """Return the html root, running scripts/rl/fetch_miniwob.sh if it is missing."""
    root = Path(root) if root else default_html_root()
    if (root / "core" / "core.js").is_file() and (root / "miniwob").is_dir():
        return root
    if not fetch:
        raise FileNotFoundError(f"MiniWoB html not found at {root}; run {FETCH_SCRIPT}")
    env = dict(os.environ, MINIWOB_SHA=catalog.PINNED_SHA)
    out = subprocess.run(["bash", str(FETCH_SCRIPT)], env=env, check=True, capture_output=True, text=True)
    got = Path(out.stdout.strip().splitlines()[-1])
    if root != got and not (root / "core" / "core.js").is_file():
        root = got
    return root


def load_utterances(extra: Optional[Path] = None) -> Dict[str, str]:
    """{"<type>/<seed>": utterance} from data/utterances.json (+ an optional extra file)."""
    out: Dict[str, str] = {}
    paths = [catalog.DATA_DIR / "utterances.json"]
    if extra is None:
        extra = Path(os.environ.get("AWOS_RL_CACHE", "~/.cache/awos-rl")).expanduser() / \
            "miniwob" / catalog.PINNED_SHA / "utterances.json"
    paths.append(Path(extra))
    for p in paths:
        if p.is_file():
            d = json.loads(p.read_text())
            out.update(d.get("utterances", d))
    return out


def secret_goal(utterance: str) -> tuple:
    """(goal with the password replaced by a handle, password or None)."""
    m = _PW_RE.search(utterance)
    if not m:
        return utterance, None
    return _PW_RE.sub(lambda mm: mm.group(1) + "{{secret:password}}" + mm.group(3), utterance), m.group(2)


class MiniWobSource:
    """contract.TaskSource over the pinned MiniWoB++ HTML tasks."""

    name = "miniwob"

    def __init__(self, html_root: Optional[Path] = None, seeds: Sequence[int] = catalog.EVAL_SEEDS,
                 types: Optional[Iterable[str]] = None, aria_patch: bool = True, port: int = 0,
                 outcome_wait: float = 1.5, fetch: bool = True,
                 utterances: Optional[Dict[str, str]] = None):
        self._html_root = Path(html_root) if html_root else None
        self.seeds = list(seeds)
        self.types = sorted(types) if types is not None else sorted(catalog.INCLUDED)
        unknown = [t for t in self.types if t not in catalog.INCLUDED]
        if unknown:
            raise ValueError(f"not an included MiniWoB type: {unknown}")
        self.aria_patch = aria_patch
        self.port = port
        self.outcome_wait = outcome_wait
        self.fetch = fetch
        self.utterances = utterances if utterances is not None else load_utterances()
        self._holdout = set(catalog.frozen_holdout())
        self._server: Optional[MiniWobServer] = None
        self._current: Dict[str, str] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------ tasks --
    def task(self, type_name: str, seed: int) -> TaskSpec:
        t = catalog.INCLUDED[type_name]
        utt = self.utterances.get(f"{type_name}/{seed}", "")
        tags = list(t.tags)
        meta = {"type": type_name, "seed": seed, "utterance": utt, "miniwob_sha": catalog.PINNED_SHA,
                "goal_mode": "utterance" if utt else "page", "aria_patch": self.aria_patch}
        goal = utt or PAGE_GOAL
        if not utt:
            tags.append("goal_on_page")
        secrets_in: List[str] = []
        if t.secret:
            secrets_in = ["password"]
            if utt:
                goal, pw = secret_goal(utt)
                if pw is not None:
                    meta["secrets"] = {"password": pw}
        return TaskSpec(
            id=f"miniwob/{type_name}/s{seed}",
            source=self.name,
            family=t.family,
            split="holdout" if type_name in self._holdout else "train",
            goal=goal,
            start=f"/miniwob/{type_name}.html?awos_seed={seed}",
            kind="act",
            tags=tags,
            secrets_in=secrets_in,
            allow_writes=True,
            max_steps=t.max_steps,
            meta=meta,
        )

    def tasks(self, split: Optional[str] = None) -> Iterable[TaskSpec]:
        for name in self.types:
            for seed in self.seeds:
                spec = self.task(name, seed)
                if split is None or spec.split == split:
                    yield spec

    # ----------------------------------------------------------- server --
    @property
    def html_root(self) -> Path:
        if self._html_root is None:
            self._html_root = ensure_html(None, fetch=self.fetch)
        return self._html_root

    def start(self) -> str:
        if self._server is None:
            self._server = MiniWobServer(self.html_root, port=self.port)
        return self._server.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.stop()
            self._server = None

    @property
    def base_url(self) -> str:
        if self._server is None:
            raise RuntimeError("MiniWobSource not started")
        return self._server.base_url

    # ---------------------------------------------------------- episode --
    def reset(self, task: TaskSpec) -> str:
        if self._server is None:
            self.start()
        assert self._server is not None
        ep = self._server.store.new(task.meta["type"], int(task.meta["seed"]), task.id)
        with self._lock:
            old = self._current.get(task.id)
            self._current[task.id] = ep
        if old:
            self._server.store.drop(old)
        return f"{self.base_url}{task.start}&awos_ep={ep}&awos_aria={1 if self.aria_patch else 0}"

    def episode(self, task: TaskSpec) -> Optional[dict]:
        ep = self._current.get(task.id)
        return self._server.store.get(ep) if (ep and self._server) else None

    def outcome(self, task: TaskSpec, traj: Optional[Trajectory] = None) -> Score:
        ep = self._current.get(task.id)
        if not ep or self._server is None:
            return Score(success=False, notes="no episode (reset() not called)")
        rec = self._server.store.wait(ep, "end", timeout=self.outcome_wait)
        if rec is None:
            return Score(success=False, notes="episode record missing")
        notes = []
        start = rec.get("start")
        if start is None:
            return Score(success=False, notes="page never reported start (shim not loaded?)")
        want = task.meta.get("utterance") or ""
        live = (start.get("utterance") or "").strip()
        if want and live != want:
            notes.append(f"utterance_mismatch live={json.dumps(live)}")
        end = rec.get("end")
        if end is None:
            notes.insert(0, "no_reward: episode not finished")
            return Score(success=False, progress=0.0, notes="; ".join(notes))
        try:
            raw = float(end.get("raw_reward"))
        except (TypeError, ValueError):
            raw = 0.0
        try:
            rew = float(end.get("reward"))
        except (TypeError, ValueError):
            rew = raw
        notes.insert(0, f"raw_reward={raw:g} reward={rew:g} reason={end.get('reason')}")
        return Score(success=raw > 0, unsafe=[], progress=max(0.0, min(1.0, raw)), notes="; ".join(notes))

    def oracle(self, task: TaskSpec) -> List[dict]:
        return oracle_for(task.meta.get("type", ""), task.meta.get("utterance", ""))
