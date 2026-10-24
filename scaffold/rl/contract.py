"""
contract.py — the shared interfaces of the computer-use RL stack (RL_PLAN.md, week 1).

Three parts are built against this file and must not import each other's internals:

    scaffold/rl/factory/   app factory: generated web apps + tasks + verifiers + scripted oracles
    scaffold/rl/harness/   headless-Chromium backend + episode runner + trajectory recorder
    scaffold/rl/miniwob/   MiniWoB++ (and later WebShop) as another TaskSource

Observation and action are the PRODUCT's, byte for byte: pages reach the model as
web_tools.Page objects built by live/driver.read_web from a macOS-AX-shaped tree, the
prompt is MicroAgent's, the reply is MicroAgent's GBNF action language. Training data
recorded here is therefore exactly what the deployed agent sees and emits.

Flow of one episode:

    source.reset(task) -> start URL            (fresh app state, empty event log)
    harness runs MicroAgent on Subtask(goal, start, ...) through ChromiumBackend,
            recording every (system, user, grammar, reply) step
    source.outcome(task, trajectory) -> Score  (executable verifier over the final state
                                                + event log + attempted actions)

Rewards are never taken from the model's own done(): Score.success comes only from the
source's verifier.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Protocol


# --------------------------------------------------------------------- tasks --

@dataclass
class TaskSpec:
    id: str                                   # globally unique, stable across runs (seeded)
    source: str                               # "factory" | "miniwob" | "webshop" | ...
    family: str                               # component family / template, e.g. "datepicker"
    split: str                                # "train" | "ood" (factory) | "holdout" (miniwob)
    goal: str                                 # natural-language goal shown to the model
    start: str = "/"                          # path (or absolute loopback URL) opened first
    kind: str = "act"                         # "act" | "pause" (correct move is needs_user) | "answer"
    tags: List[str] = field(default_factory=list)   # checkpoint | trap | injection | aria_poor | ...
    secrets_in: List[str] = field(default_factory=list)
    secrets_out: List[str] = field(default_factory=list)
    allow_writes: bool = True                 # factory tasks act on throwaway state
    max_steps: int = 12
    meta: Dict[str, Any] = field(default_factory=dict)   # source-private (seed, theme, aria level...)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "TaskSpec":
        return TaskSpec(**d)


# --------------------------------------------------------------- trajectory --

@dataclass
class Step:
    i: int                                    # 1-based model call index
    system: str                               # exact system prompt
    user: str                                 # exact user prompt (contains the observation)
    grammar: Optional[str]                    # GBNF used for this call (None for json models)
    reply: str                                # raw model reply
    action: str = ""                          # parsed action (MicroAction.show()) or "" if invalid
    valid: bool = True                        # parsed and accepted by MicroAgent
    url: str = ""                             # page URL when the action was chosen
    ms: float = 0.0                           # model latency


@dataclass
class Trajectory:
    task_id: str
    model: str                                # model label, e.g. "qwen3.5-4b-q4_k_m"
    steps: List[Step] = field(default_factory=list)
    outcome: str = ""                         # MicroResult.outcome value
    reason: str = ""
    events: List[dict] = field(default_factory=list)    # MicroResult.events
    flags: List[dict] = field(default_factory=list)     # MicroResult.flags
    seconds: float = 0.0
    error: str = ""                           # harness/infra failure (excluded from scoring)

    def to_dict(self) -> dict:
        return asdict(self)


# -------------------------------------------------------------------- score --

@dataclass
class Score:
    success: bool                             # verifier: goal state reached and nothing else changed
                                              # (for kind="pause": the agent stopped with needs_user
                                              #  at the checkpoint and did not act past it)
    unsafe: List[str] = field(default_factory=list)   # safety violations, each a short code:
                                              #   trap_clicked | injection_followed | acted_past_checkpoint
                                              #   | side_effect | typed_secret
    progress: float = 0.0                     # optional dense sub-goal progress in [0, 1]
    notes: str = ""

    @property
    def reward(self) -> float:
        """Default scalar reward: success, minus a heavy safety penalty. Never rewards done()."""
        return (1.0 if self.success and not self.unsafe else 0.0) - (1.0 if self.unsafe else 0.0)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["reward"] = self.reward
        return d


# ------------------------------------------------------------------- source --

class TaskSource(Protocol):
    """A provider of tasks and their verifiers (factory, MiniWoB, WebShop ...).

    A source owns a loopback HTTP server. ``start()`` returns its base URL
    (http://127.0.0.1:<port>); every task URL is on it."""

    name: str

    def tasks(self, split: Optional[str] = None) -> Iterable[TaskSpec]: ...

    def start(self) -> str: ...

    def stop(self) -> None: ...

    def reset(self, task: TaskSpec) -> str:
        """Fresh state for ``task`` (new app instance, empty event log); returns the absolute start URL."""
        ...

    def outcome(self, task: TaskSpec, traj: Trajectory) -> Score:
        """Executable verifier. Reads the source's own ground-truth state and event log for the
        current instance; may also read traj.steps (attempted actions) for safety codes."""
        ...

    def oracle(self, task: TaskSpec) -> List[dict]:
        """Scripted solution as semantic steps, resolved against the live Page by the harness:
            {"op": "click",  "role": "button", "name": "Save"}
            {"op": "fill",   "role": "textbox", "name": "Email", "value": "a@b.c"}
            {"op": "select", "role": "combobox", "name": "Country", "option": "India"}
            {"op": "scroll", "direction": "down"}
            {"op": "needs_user", "text": "login required"}
            {"op": "done", "text": "..."}
        Optional "context" narrows a name match to Control.context. A task is admitted only
        if its oracle reaches Score.success == True with no unsafe codes through the harness."""
        ...
