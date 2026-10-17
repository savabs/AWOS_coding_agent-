"""
Self-Learning Observability — SelfLearningMetrics.

Reads .awos/ data written by the three self-learning features and
produces a structured snapshot + human-readable ASCII report.

Data sources (all disk-only, no LLM calls):
  .awos/evolved_prompt.json        — PromptEvolver (1A)
  .awos/tools/index.json           — LiveToolSynthesizer (1B)
  .awos/scaffold_mutations.jsonl   — ScaffoldEvolver (1C)
  .awos/error_patterns.jsonl       — ErrorPatternStore (shared)
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)

_W = 60  # box width


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Snapshot dataclass ────────────────────────────────────────────────────────

@dataclass
class SelfLearningSnapshot:
    """Point-in-time snapshot of all self-learning feature metrics."""
    captured_at: str = field(default_factory=_now_iso)

    # ── PromptEvolver (1A) ──────────────────────────────────────────────────
    guidelines_version: int = 0       # increments each time persist() is called
    guidelines_evolved_at: str = ""   # ISO timestamp of last evolution
    guidelines_lines: int = 0         # count of active guideline bullet lines
    guidelines_preview: str = ""      # first 120 chars of current guidelines

    # ── LiveToolSynthesizer (1B) ────────────────────────────────────────────
    tools_synthesized: int = 0        # total tools in index
    tools_total_reuses: int = 0       # sum of all use_counts
    top_tools: List[dict] = field(default_factory=list)  # [{name, use_count}]

    # ── ScaffoldEvolver (1C) ────────────────────────────────────────────────
    mutations_attempted: int = 0
    mutations_accepted: int = 0
    acceptance_rate: float = 0.0
    files_mutated: List[str] = field(default_factory=list)
    last_mutation_at: str = ""

    # ── ErrorPatternStore (shared) ──────────────────────────────────────────
    error_patterns_recorded: int = 0
    top_error_types: List[dict] = field(default_factory=list)  # [{type, count}]
    
    # ── Cache Performance (new) ─────────────────────────────────────────────
    cache_hit_rate: float = 0.0       # overall cache hit %
    cache_tokens_read: int = 0        # total tokens from cache
    cache_tokens_fresh: int = 0       # total fresh input tokens
    cache_cost_saved: float = 0.0     # total $ saved via cache

    def to_dict(self) -> dict:
        return {
            "captured_at": self.captured_at,
            "prompt_evolver": {
                "version": self.guidelines_version,
                "evolved_at": self.guidelines_evolved_at,
                "active_guideline_lines": self.guidelines_lines,
                "preview": self.guidelines_preview,
            },
            "live_tools": {
                "synthesized": self.tools_synthesized,
                "total_reuses": self.tools_total_reuses,
                "top_tools": self.top_tools,
            },
            "scaffold_evolver": {
                "attempted": self.mutations_attempted,
                "accepted": self.mutations_accepted,
                "acceptance_rate": round(self.acceptance_rate, 3),
                "files_mutated": self.files_mutated,
                "last_mutation_at": self.last_mutation_at,
            },
            "error_patterns": {
                "total": self.error_patterns_recorded,
                "top_types": self.top_error_types,
            },
            "cache_performance": {
                "hit_rate": round(self.cache_hit_rate, 3),
                "tokens_cached": self.cache_tokens_read,
                "tokens_fresh": self.cache_tokens_fresh,
                "cost_saved_usd": round(self.cache_cost_saved, 2),
            },
        }


# ── Main class ────────────────────────────────────────────────────────────────

class SelfLearningMetrics:
    """
    Reads .awos/ artifacts and produces an observability report.

    Usage:
        metrics = SelfLearningMetrics()
        snap = metrics.snapshot()      # structured dict
        metrics.print_report()         # ASCII box to stdout
    """

    def __init__(self, store_path: str = ".awos") -> None:
        self._store = Path(store_path)
        self._evolved_path = self._store / "evolved_prompt.json"
        self._tools_index = self._store / "tools" / "index.json"
        self._mutations_log = self._store / "scaffold_mutations.jsonl"
        self._patterns_log = self._store / "error_patterns.jsonl"
        self._cache_stats = self._store / "cache_stats.jsonl"

    # ── Public API ────────────────────────────────────────────────────────────

    def snapshot(self) -> SelfLearningSnapshot:
        """Collect all metrics and return a SelfLearningSnapshot."""
        snap = SelfLearningSnapshot()
        self._collect_prompt_evolver(snap)
        self._collect_live_tools(snap)
        self._collect_scaffold_evolver(snap)
        self._collect_error_patterns(snap)
        self._collect_cache_performance(snap)
        return snap

    def print_report(self) -> None:
        """Print a human-readable ASCII summary box to stdout."""
        snap = self.snapshot()
        _print_box(snap)

    # ── Collectors ───────────────────────────────────────────────────────────

    def _collect_prompt_evolver(self, snap: SelfLearningSnapshot) -> None:
        if not self._evolved_path.exists():
            return
        try:
            data = json.loads(self._evolved_path.read_text())
            gl = data.get("evolved_guidelines", "").strip()
            snap.guidelines_version = data.get("session_count", 1)
            snap.guidelines_evolved_at = data.get("evolved_at", "")
            snap.guidelines_lines = len([l for l in gl.splitlines() if l.strip()])
            snap.guidelines_preview = gl[:120].replace("\n", " ")
        except Exception as exc:
            logger.debug("[SelfLearningMetrics] prompt_evolver read error: %s", exc)

    def _collect_live_tools(self, snap: SelfLearningSnapshot) -> None:
        if not self._tools_index.exists():
            return
        try:
            tools = json.loads(self._tools_index.read_text())
            snap.tools_synthesized = len(tools)
            snap.tools_total_reuses = sum(t.get("use_count", 0) for t in tools)
            sorted_tools = sorted(tools, key=lambda t: t.get("use_count", 0), reverse=True)
            snap.top_tools = [
                {"name": t["name"], "use_count": t.get("use_count", 0)}
                for t in sorted_tools[:3]
            ]
        except Exception as exc:
            logger.debug("[SelfLearningMetrics] live_tools read error: %s", exc)

    def _collect_scaffold_evolver(self, snap: SelfLearningSnapshot) -> None:
        if not self._mutations_log.exists():
            return
        try:
            mutations = []
            for line in self._mutations_log.read_text().splitlines():
                line = line.strip()
                if line:
                    try:
                        mutations.append(json.loads(line))
                    except Exception:
                        pass
            snap.mutations_attempted = len(mutations)
            accepted = [m for m in mutations if m.get("accepted")]
            snap.mutations_accepted = len(accepted)
            snap.acceptance_rate = (
                snap.mutations_accepted / snap.mutations_attempted
                if snap.mutations_attempted else 0.0
            )
            snap.files_mutated = sorted({
                m["target_file"] for m in accepted if m.get("target_file")
            })
            if mutations:
                snap.last_mutation_at = mutations[-1].get("timestamp", "")
        except Exception as exc:
            logger.debug("[SelfLearningMetrics] scaffold_evolver read error: %s", exc)

    def _collect_error_patterns(self, snap: SelfLearningSnapshot) -> None:
        if not self._patterns_log.exists():
            return
        try:
            counts: dict = {}
            total = 0
            for line in self._patterns_log.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    et = rec.get("error_type", "UNKNOWN")
                    counts[et] = counts.get(et, 0) + 1
                    total += 1
                except Exception:
                    pass
            snap.error_patterns_recorded = total
            snap.top_error_types = [
                {"type": et, "count": cnt}
                for et, cnt in sorted(counts.items(), key=lambda x: -x[1])[:5]
            ]
        except Exception as exc:
            logger.debug("[SelfLearningMetrics] error_patterns read error: %s", exc)
    
    def _collect_cache_performance(self, snap: SelfLearningSnapshot) -> None:
        """Collect cache hit rate stats from cache_stats.jsonl."""
        if not self._cache_stats.exists():
            return
        try:
            from datetime import timedelta
            try:
                from .cache_telemetry import CacheTelemetryStore
            except ImportError:
                from cache_telemetry import CacheTelemetryStore
            
            cache_store = CacheTelemetryStore(store_path=str(self._store))
            # Get stats for last 7 days
            now = datetime.now(timezone.utc)
            stats = cache_store.stats(since=now - timedelta(days=7))
            
            snap.cache_hit_rate = stats["hit_rate"]
            snap.cache_tokens_read = stats["tokens_cached"]
            snap.cache_tokens_fresh = stats["tokens_fresh"]
            snap.cache_cost_saved = stats["cost_saved"]
        except Exception as exc:
            logger.debug("[SelfLearningMetrics] cache_performance read error: %s", exc)


# ── ASCII report renderer ─────────────────────────────────────────────────────

def _row(label: str, value: str) -> str:
    """Render one label: value row inside the box."""
    content = f"  {label:<22} {value}"
    pad = _W - 2 - len(content)
    return f"│{content}{' ' * max(0, pad)}│"


def _header(title: str) -> str:
    content = f"  {title}"
    pad = _W - 2 - len(content)
    return f"│{content}{' ' * max(0, pad)}│"


def _divider() -> str:
    return f"│{'─' * (_W - 2)}│"


def _print_box(snap: SelfLearningSnapshot) -> None:
    lines = []
    lines.append(f"╭{'─' * (_W - 2)}╮")
    lines.append(_header("Self-Learning Observability"))
    lines.append(_header(f"Captured: {snap.captured_at}"))

    # ── PromptEvolver ──────────────────────────────────────────────────────
    lines.append(_divider())
    lines.append(_header("PromptEvolver (1A) — Evolved Guidelines"))
    if snap.guidelines_lines == 0:
        lines.append(_row("Status", "No guidelines evolved yet"))
    else:
        lines.append(_row("Last evolved", snap.guidelines_evolved_at or "—"))
        lines.append(_row("Active guidelines", f"{snap.guidelines_lines} line(s)"))
        if snap.guidelines_preview:
            preview = snap.guidelines_preview[:55] + ("…" if len(snap.guidelines_preview) > 55 else "")
            lines.append(_row("Preview", preview))

    # ── LiveToolSynthesizer ────────────────────────────────────────────────
    lines.append(_divider())
    lines.append(_header("LiveToolSynthesizer (1B) — On-the-fly Tools"))
    lines.append(_row("Tools synthesized", str(snap.tools_synthesized)))
    lines.append(_row("Total reuses", str(snap.tools_total_reuses)))
    if snap.top_tools:
        for t in snap.top_tools[:3]:
            name = t["name"][:35]
            lines.append(_row(f"  {name}", f"{t['use_count']}×"))
    else:
        lines.append(_row("  (none synthesized yet)", ""))

    # ── ScaffoldEvolver ────────────────────────────────────────────────────
    lines.append(_divider())
    lines.append(_header("ScaffoldEvolver (1C) — Code Self-Mutation"))
    if snap.mutations_attempted == 0:
        lines.append(_row("Status", "No mutations attempted yet"))
    else:
        pct = f"{snap.acceptance_rate:.0%}"
        lines.append(_row(
            "Mutations",
            f"{snap.mutations_accepted}/{snap.mutations_attempted} accepted ({pct})",
        ))
        if snap.files_mutated:
            lines.append(_row("Files mutated", ", ".join(snap.files_mutated)[:40]))
        lines.append(_row("Last mutation", snap.last_mutation_at or "—"))

    # ── Error Patterns ─────────────────────────────────────────────────────
    lines.append(_divider())
    lines.append(_header("Error Patterns (shared signal)"))
    lines.append(_row("Total recorded", str(snap.error_patterns_recorded)))
    if snap.top_error_types:
        for et in snap.top_error_types[:3]:
            lines.append(_row(f"  {et['type'][:28]}", f"{et['count']}×"))
    else:
        lines.append(_row("  (none recorded yet)", ""))
    
    # ── Cache Performance ──────────────────────────────────────────────────
    lines.append(_divider())
    lines.append(_header("Cache Performance (last 7 days)"))
    total_input = snap.cache_tokens_read + snap.cache_tokens_fresh
    if total_input == 0:
        lines.append(_row("Status", "No API calls recorded yet"))
    else:
        lines.append(_row("Hit rate", f"{snap.cache_hit_rate:.1%}"))
        lines.append(_row("Cached tokens", f"{snap.cache_tokens_read:,}"))
        lines.append(_row("Fresh tokens", f"{snap.cache_tokens_fresh:,}"))
        lines.append(_row("Cost saved", f"${snap.cache_cost_saved:.2f}"))

    lines.append(f"╰{'─' * (_W - 2)}╯")
    print("\n".join(lines))
