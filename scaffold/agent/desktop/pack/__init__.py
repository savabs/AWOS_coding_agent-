"""
pack — the month-end document pack (v0.1).

Each month: for every due source (SaaS/vendor invoices, bank/card statements,
payment-gateway reports, tax documents) replay the owner's taught skill in their
own browser session inside the sandbox VM, pick up the new downloads, file them
under ``Pack/<YYYY-MM>/<source-id>/``, check them, and hand the accountant a
completeness report + zip. Spec: docs/specs/doc_pack_v0.md.

    config.py    sources.toml (~/.awos/pack/sources.toml) -> PackConfig / Source
    files.py     downloads watcher, sha256 dedupe, filing, JSONL ledger
    run.py       month orchestration, per-(month, source) state, resume
    report.py    completeness report (HTML / text / JSON) + accountant zip
    __main__.py  CLI: python -m scaffold.agent.desktop.pack ...

This module holds ONLY the plug-in contract shared with the other pack pieces
(it imports nothing heavy, so ``pack.verify`` / ``pack.policy`` / the live
checkpoint detector can import it without cycles):

    Verdict     what ``verify.check(doc_path, source, period) -> Verdict`` returns
    Period      the month (or quarter) being collected
    Checkpoint  a login / OTP / captcha / other wall only the owner can pass
    NeedsYou    raise it (with a Checkpoint) from a runner to stop at a checkpoint
    RunnerResult / RunContext   what a skill runner gets and gives back
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# ── verdicts (verify.check) ─────────────────────────────────────────────────
VERIFIED = "VERIFIED"          # period / document type / amounts checked and consistent
UNVERIFIED = "UNVERIFIED"      # nothing could be checked (no text, unknown layout) -- not an error
MISMATCH = "MISMATCH"          # readable, but wrong period / wrong account / not the expected doc
SUSPICIOUS = "SUSPICIOUS"      # looks unsafe or not a real document (HTML error page, empty, script)
VERDICTS = (VERIFIED, UNVERIFIED, MISMATCH, SUSPICIOUS)


@dataclass
class Verdict:
    status: str = UNVERIFIED
    reasons: List[str] = field(default_factory=list)
    facts: Dict[str, Any] = field(default_factory=dict)   # e.g. {"period": "2026-10", "total": "1234.00"}

    def __post_init__(self) -> None:
        if self.status not in VERDICTS:
            raise ValueError(f"unknown verdict status {self.status!r}; use one of {VERDICTS}")

    def to_dict(self) -> Dict[str, Any]:
        return {"status": self.status, "reasons": list(self.reasons), "facts": dict(self.facts)}

    @classmethod
    def coerce(cls, v: Any) -> "Verdict":
        """Accept a Verdict, a dict, or any object with .status/.reasons/.facts."""
        if isinstance(v, Verdict):
            return v
        if v is None:
            return cls(UNVERIFIED, ["verifier returned nothing"])
        if isinstance(v, dict):
            return cls(str(v.get("status", UNVERIFIED)), list(v.get("reasons") or []), dict(v.get("facts") or {}))
        return cls(str(getattr(v, "status", UNVERIFIED)), list(getattr(v, "reasons", []) or []),
                   dict(getattr(v, "facts", {}) or {}))


def stub_check(doc_path: Path, source: Any, period: "Period") -> Verdict:
    """Default verifier until pack.verify exists: nothing is claimed."""
    return Verdict(UNVERIFIED, ["no verifier installed"])


# ── period ──────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Period:
    label: str                 # "2026-10" -- the pack month (folder name)
    start: date                # first day covered (quarter start for quarterly sources)
    end: date                  # last day covered (inclusive)
    cadence: str = "monthly"   # "monthly" | "quarterly"

    def __str__(self) -> str:
        return self.label

    @property
    def year(self) -> int:
        return int(self.label[:4])

    @property
    def month(self) -> int:
        return int(self.label[5:7])


# ── checkpoints (live/checkpoint.py plugs in here) ──────────────────────────
CHECKPOINT_KINDS = ("login", "otp", "captcha", "2fa_app", "consent", "unknown")


@dataclass
class Checkpoint:
    kind: str                  # one of CHECKPOINT_KINDS
    message: str = ""          # what the screen said, already redacted by the detector
    url: str = ""              # where it happened (no query string with tokens, please)

    def __post_init__(self) -> None:
        if self.kind not in CHECKPOINT_KINDS:
            self.kind = "unknown"

    def to_dict(self) -> Dict[str, str]:
        return {"kind": self.kind, "message": self.message, "url": self.url}


class NeedsYou(Exception):
    """Raise from a runner (or a checkpoint callback) to stop at an owner-only wall."""

    def __init__(self, checkpoint: Checkpoint):
        super().__init__(f"{checkpoint.kind}: {checkpoint.message}")
        self.checkpoint = checkpoint


# ── runner contract ─────────────────────────────────────────────────────────
RUN_OK = "ok"                  # the skill finished; downloads (if any) are in ctx.downloads
RUN_NEEDS_YOU = "needs_you"    # stopped at a checkpoint (set .checkpoint)
RUN_FAILED = "failed"          # halted / crashed (set .reason)
RUN_STATUSES = (RUN_OK, RUN_NEEDS_YOU, RUN_FAILED)


@dataclass
class RunnerResult:
    status: str = RUN_OK
    reason: str = ""
    checkpoint: Optional[Checkpoint] = None
    detail: Dict[str, Any] = field(default_factory=dict)   # e.g. SkillRunner outcome, model_calls


@dataclass
class RunContext:
    """What a runner gets besides the Source and the Period."""
    downloads: Path                                  # where the browser saves files (VM ~/Downloads)
    allowed: Callable[[str, str], bool]              # policy: allowed(url, action) -> bool
    checkpoint: Callable[[Checkpoint], None]         # call on a login/OTP/captcha wall; it raises NeedsYou
    password_item: Optional[str] = None              # keychain item NAME (never the value)
    log: Callable[[str], None] = lambda s: None


Runner = Callable[[Any, Period, RunContext], Optional[RunnerResult]]


def raise_needs_you(cp: Checkpoint) -> None:
    """Default RunContext.checkpoint: stop the run."""
    raise NeedsYou(cp)


__all__ = ["Verdict", "VERIFIED", "UNVERIFIED", "MISMATCH", "SUSPICIOUS", "VERDICTS", "stub_check",
           "Period", "Checkpoint", "CHECKPOINT_KINDS", "NeedsYou", "RunnerResult", "RunContext", "Runner",
           "RUN_OK", "RUN_NEEDS_YOU", "RUN_FAILED", "RUN_STATUSES", "raise_needs_you"]
