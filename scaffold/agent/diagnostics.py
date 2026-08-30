#!/usr/bin/env python3
"""
AWOS Diagnostics — sensors throughout the pipeline.

Usage:
    from scaffold.agent.diagnostics import trace, TimingReport
    # ... code runs ...
    TimingReport.print_all()
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

TRACES: list["TraceSpan"] = []

@dataclass
class TraceSpan:
    name: str
    phase: str       # 'http', 'routing', 'llm_call', 'ui', 'sse'
    started_at: float = field(default_factory=time.time)
    finished_at: float = 0.0
    success: bool = True
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_ms(self) -> float:
        return (self.finished_at - self.started_at) * 1000 if self.finished_at else 0

    def done(self, success: bool = True, error: str = "", **meta):
        self.finished_at = time.time()
        self.success = success
        self.error = error
        self.metadata.update(meta)
        TRACES.append(self)
        return self


class TimingReport:
    """Aggregate and print timing reports."""

    @staticmethod
    def by_phase() -> dict[str, dict]:
        phases = defaultdict(lambda: {"count": 0, "total_ms": 0, "errors": 0, "slowest": 0})
        for t in TRACES:
            p = phases[t.phase]
            p["count"] += 1
            p["total_ms"] += t.duration_ms
            if not t.success:
                p["errors"] += 1
            if t.duration_ms > p["slowest"]:
                p["slowest"] = t.duration_ms
        return dict(phases)

    @staticmethod
    def by_name() -> dict[str, dict]:
        names = defaultdict(lambda: {"count": 0, "total_ms": 0, "avg_ms": 0, "errors": 0})
        for t in TRACES:
            n = names[t.name]
            n["count"] += 1
            n["total_ms"] += t.duration_ms
            if not t.success:
                n["errors"] += 1
        for v in names.values():
            v["avg_ms"] = round(v["total_ms"] / v["count"], 1) if v["count"] > 0 else 0
        return dict(names)

    @staticmethod
    def print_all():
        if not TRACES:
            print("[DIAG] No traces collected.")
            return
        print("\n══════════════ DIAGNOSTICS ═══════════════")
        print(f"Traces: {len(TRACES)}")
        print("\nBy phase:")
        for phase, data in sorted(TimingReport.by_phase().items()):
            err = f" · {data['errors']} errors" if data["errors"] else ""
            print(f"  {phase:12s} {data['count']:3d} calls · {data['total_ms']:8.0f}ms total · slowest: {data['slowest']:5.0f}ms{err}")
        print("\nBy name:")
        for name, data in sorted(TimingReport.by_name().items(), key=lambda x: x[1]["total_ms"], reverse=True)[:15]:
            err = f" · {data['errors']} errors" if data["errors"] else ""
            print(f"  {name:40s} {data['count']:3d}× · avg {data['avg_ms']:6.0f}ms · total {data['total_ms']:8.0f}ms{err}")
        print("═══════════════════════════════════════════\n")

    @staticmethod
    def save(path: str = ".awos/diagnostics.jsonl"):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            for t in TRACES:
                f.write(json.dumps({
                    "name": t.name, "phase": t.phase,
                    "duration_ms": t.duration_ms, "success": t.success,
                    "error": t.error, "metadata": t.metadata,
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t.started_at)),
                }) + "\n")
        TRACES.clear()

    @staticmethod
    def reset():
        TRACES.clear()
