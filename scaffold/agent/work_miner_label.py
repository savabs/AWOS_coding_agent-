"""
work_miner_label.py — label owner prompts into task families with the LOCAL
model only.

Privacy is the point: the owner's prompts never leave the machine. The
labeller only talks to a llama-server bound to localhost (scripts/local_model.sh)
and refuses any other host. Output is constrained to a fixed label set via a
JSON schema, plus an optional short "new family" proposal the taxonomy can be
revised from. Labels are cached by prompt hash (no prompt text) in
.awos/jobs/, so reruns only label new prompts. When the server is down the
deterministic keyword rules in work_miner are the fallback.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional
from urllib.parse import urlparse

TAXONOMY_VERSION = "v1.2"
# On a taxonomy bump these cached labels are re-asked (the new families were
# carved out of them); every other concrete label carries over.
# v1.2 (2026-10-11): v1.1 put 30% of prompts in plan/strategy and the model's
# "other" proposals were constraint/clarification/budget items -> added
# "steer / redirect agent", widened keys to billing/budget.
RELABEL_ON_BUMP = {"other", "plan / strategy / discuss", "continue / approve"}

# family -> (automatability prior 0-1, one-line definition for the model).
# Supervision families are the babysitting the autopilot should absorb.
TAXONOMY: dict[str, tuple[float, str]] = {
    "continue / approve": (0.9, "nudges an agent to keep going or approves its plan: continue, go ahead, yes, proceed, do it, option 2, resume, try again"),
    "status / progress check": (0.9, "asks what the agent is doing, how far it got, whether it finished, what happened, any results yet"),
    "keys / credentials / quota": (0.7, "API keys, tokens, logins, credits, billing, budget or spend limits, quota or rate-limit problems, which provider or model account to use"),
    "steer / redirect agent": (0.4, "corrects or redirects the agent's current work: answers its question with specifics, adds constraints or requirements, says 'no, do X instead', changes scope"),
    "run benchmark / experiment": (0.8, "start, rerun or compare benchmarks, evals, ablations, experiments, measure performance"),
    "debug / fix": (0.7, "something is broken, failing, erroring or wrong and should be fixed; pasted error output"),
    "build feature / implement": (0.5, "write new code, add a feature, implement a spec, create a script, tool or app"),
    "review / verify / test": (0.7, "review code or results, check correctness, run or write tests, validate a claim"),
    "git / commit / PR / merge": (0.9, "commit, push, branch, merge, open or update a pull request, release"),
    "research / explore": (0.4, "investigate options, survey papers or tools, compare approaches, find information"),
    "plan / strategy / discuss": (0.2, "open-ended roadmap, priorities, business or product strategy, vision or brainstorming, not instructions about the task in progress"),
    "explain / understand": (0.5, "explain how something works, summarise, answer a factual question about the code or a concept"),
    "docs / writing": (0.6, "write or edit documents, READMEs, reports, posts, specs, emails"),
    "environment setup / install": (0.7, "install, configure, set up tools, dependencies, machines, local models, settings"),
    "machine / system admin": (0.6, "disk space, processes, OS settings, files on the computer, hardware, backups"),
    "agent orchestration / delegation": (0.6, "split work across agents, spawn or coordinate subagents, parallelise, assign tasks"),
    "rules / memory / preferences": (0.5, "tell the agent how to behave in future, update rules, memory, instructions, preferences"),
    "other": (0.3, "none of the above"),
}
SUPERVISION = {"continue / approve", "status / progress check", "keys / credentials / quota"}
LABELS = list(TAXONOMY)

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
MAX_PROMPT_CHARS = 1500

SYSTEM_PROMPT = (
    "You classify one message that a software developer typed to an AI coding agent. "
    "Pick the single best task family from this list:\n"
    + "\n".join(f"- {k}: {v[1]}" for k, v in TAXONOMY.items())
    + "\nBare short replies such as 'continue', 'yes', 'go' are 'continue / approve'; "
    "a reply that also changes or adds instructions is 'steer / redirect agent'. "
    "Questions about progress are 'status / progress check'. "
    "If no family fits well, answer 'other' and propose a short new family name (2-4 words) in new_family; "
    "otherwise leave new_family empty. Reply with JSON only."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "label": {"type": "string", "enum": LABELS},
        "new_family": {"type": "string", "maxLength": 40},
    },
    "required": ["label", "new_family"],
}


def prompt_hash(text: str) -> str:
    return hashlib.sha256(" ".join(text.split()).encode("utf-8")).hexdigest()[:20]


def assert_local(url: str) -> None:
    host = urlparse(url).hostname or ""
    if host not in _LOCAL_HOSTS:
        raise ValueError(f"work_miner labeller only talks to a local model; refusing host {host!r}")


@dataclass
class Label:
    label: str
    new_family: str = ""
    source: str = "model"      # model | rules | cache


class LabelCache:
    """{taxonomy_version:hash -> {label, new_family, model}}; holds no prompt text."""

    def __init__(self, path: Path):
        self.path = path
        self.data: dict = {}
        if path.exists():
            try:
                self.data = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                self.data = {}

    def key(self, h: str) -> str:
        return f"{TAXONOMY_VERSION}:{h}"

    def get(self, h: str) -> Optional[Label]:
        v = self.data.get(self.key(h))
        if v and v.get("label") in TAXONOMY:
            return Label(v["label"], v.get("new_family", ""), "cache")
        # Taxonomy bumped: a concrete label from an older version that still
        # exists is carried over; "other" (or a dropped family) is relabelled,
        # so adding families only costs the prompts that had no home.
        for k, old in self.data.items():
            if k.endswith(":" + h) and old.get("label") in TAXONOMY and old.get("label") not in RELABEL_ON_BUMP:
                self.data[self.key(h)] = dict(old)
                return Label(old["label"], old.get("new_family", ""), "cache")
        return None

    def put(self, h: str, lab: Label, model: str) -> None:
        self.data[self.key(h)] = {"label": lab.label, "new_family": lab.new_family, "model": model}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=0, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)


def _sanitize_family(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9 /&+-]", "", str(s or "")).strip().lower()
    return " ".join(s.split()[:5])[:40]


class LocalLabeller:
    def __init__(self, base_url: Optional[str] = None, model: Optional[str] = None,
                 cache_path: Optional[Path] = None, workers: int = 4, timeout: float = 120.0,
                 redact: Callable[[str], str] = lambda t: t):
        port = os.environ.get("AWOS_LOCAL_PORT", "8080")
        self.base_url = (base_url or f"http://127.0.0.1:{port}/v1").rstrip("/")
        assert_local(self.base_url)
        self.model = model or os.environ.get("AWOS_LOCAL_MODEL", "qwen3.5-9b")
        self.cache = LabelCache(cache_path) if cache_path else None
        self.workers = max(1, workers)
        self.timeout = timeout
        self.redact = redact
        self.stats = {"cache": 0, "model": 0, "failed": 0}

    # -- server
    def available(self) -> bool:
        root = self.base_url[:-3] if self.base_url.endswith("/v1") else self.base_url
        try:
            with urllib.request.urlopen(root + "/health", timeout=3) as r:
                return r.status == 200
        except (urllib.error.URLError, OSError, ValueError):
            return False

    def _post(self, payload: dict) -> dict:
        req = urllib.request.Request(self.base_url + "/chat/completions",
                                     data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def label_one(self, text: str) -> Optional[Label]:
        body = self.redact(text.strip())[:MAX_PROMPT_CHARS]
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                         {"role": "user", "content": "Message:\n<<<\n" + body + "\n>>>"}],
            "temperature": 0, "max_tokens": 60,
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": "label", "schema": SCHEMA}},
            "chat_template_kwargs": {"enable_thinking": False},
        }
        try:
            resp = self._post(payload)
            content = resp["choices"][0]["message"]["content"]
            m = re.search(r"\{.*\}", content, re.S)
            obj = json.loads(m.group(0) if m else content)
        except (urllib.error.URLError, OSError, ValueError, KeyError, IndexError, TypeError):
            return None
        lab = obj.get("label") if isinstance(obj, dict) else None
        if lab not in TAXONOMY:
            return None
        return Label(lab, _sanitize_family(obj.get("new_family", "")), "model")

    def label_many(self, texts: Iterable[str], save_every: int = 50) -> dict[str, Label]:
        """hash -> Label for every text the model (or cache) could label."""
        out: dict[str, Label] = {}
        todo: dict[str, str] = {}
        for t in texts:
            h = prompt_hash(t)
            if h in out or h in todo:
                continue
            hit = self.cache.get(h) if self.cache else None
            if hit:
                out[h] = hit
                self.stats["cache"] += 1
            else:
                todo[h] = t
        if not todo:
            return out
        done = 0
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            items = list(todo.items())
            for (h, _), lab in zip(items, pool.map(lambda kv: self.label_one(kv[1]), items)):
                if lab is None:
                    self.stats["failed"] += 1
                    continue
                out[h] = lab
                self.stats["model"] += 1
                if self.cache:
                    self.cache.put(h, lab, self.model)
                    done += 1
                    if done % save_every == 0:
                        self.cache.save()
        if self.cache:
            self.cache.save()
        return out


@contextmanager
def local_server(slots: int = 4, script: Optional[Path] = None, enabled: bool = True):
    """Start llama-server via scripts/local_model.sh if it is not already up;
    stop it afterwards only if we started it. Yields True when a server is up."""
    script = script or Path(__file__).resolve().parents[2] / "scripts" / "local_model.sh"
    probe = LocalLabeller()
    if not enabled or probe.available():
        yield probe.available()
        return
    env = dict(os.environ, AWOS_LOCAL_SLOTS=str(slots),
               AWOS_LOCAL_CONTEXT=os.environ.get("AWOS_LOCAL_CONTEXT", str(4096 * slots)))
    started = subprocess.run(["bash", str(script), "start"], env=env,
                             capture_output=True, text=True).returncode == 0
    try:
        yield started and probe.available()
    finally:
        if started:
            subprocess.run(["bash", str(script), "stop"], env=env, capture_output=True, text=True)
