# Adapted from OpenAdapt Flow openadapt_flow/runtime/effects/file_arrival.py
# (record identity rel@size@mtime, size_ok/fresh/content_match -> arrived,
# count_new_only duplicate guard, unreadable -> INDETERMINATE), Copyright (c)
# 2026 OpenAdapt.AI (MLDSAI Inc.), MIT License -- see LICENSE-OpenAdapt here.
# Local directories only (the SFTP transport was dropped); no pydantic.
"""File-arrival effect: "exactly N new conforming files appeared because of this action".

    eff = FileArrival(downloads, pattern="*.csv", expected_count=1)
    before = eff.capture()
    ... export / download ...
    eff.verify(before)

A candidate conforms when it is at least ``min_size`` bytes, (optionally)
modified within ``mtime_window_s`` and (optionally) matches ``content_probe``
in its first MiB. A record's identity includes size and mtime, so an in-place
rewrite counts as a new arrival. Only records absent from the pre-state are
counted: a double export (``report (1).csv``) is REFUTED as a duplicate.
A missing root or an unreadable candidate -> INDETERMINATE.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from .outcome import CheckLevel, EffectVerdict, confirmed, indeterminate, refuted

CONTENT_PROBE_LIMIT = 1 << 20


@dataclass(frozen=True)
class ArrivalState:
    reachable: bool
    ids: frozenset = frozenset()


@dataclass
class FileArrival:
    root: Path
    pattern: str = "*"
    expected_count: int = 1
    min_size: int = 1
    mtime_window_s: Optional[float] = None
    content_probe: Optional[str] = None
    timeout_s: float = 0.0
    substrate: str = "file_arrival"

    def _scan(self) -> Optional[List[Dict]]:
        root = Path(self.root)
        if not root.is_dir():
            return None
        probe = re.compile(self.content_probe) if self.content_probe else None
        now = time.time()
        out = []
        try:
            paths = sorted(p for p in root.glob(self.pattern) if p.is_file())
        except OSError:
            return None
        for p in paths:
            try:
                st = p.stat()
                content_ok = True
                if probe is not None:
                    with p.open("rb") as fh:
                        content_ok = probe.search(fh.read(CONTENT_PROBE_LIMIT).decode("utf-8", "replace")) is not None
            except OSError:
                return None
            fresh = True if self.mtime_window_s is None else abs(now - st.st_mtime) <= self.mtime_window_s
            out.append({"id": f"{p.relative_to(root)}@{st.st_size}@{st.st_mtime:.3f}",
                        "arrived": st.st_size >= self.min_size and fresh and content_ok})
        return out

    def capture(self) -> ArrivalState:
        recs = self._scan()
        return ArrivalState(recs is not None, frozenset(r["id"] for r in recs or []))

    def verify(self, before: ArrivalState) -> EffectVerdict:
        deadline = time.monotonic() + max(0.0, self.timeout_s)
        while True:
            v = self._judge(before)
            if v.confirmed or time.monotonic() >= deadline:
                return v
            time.sleep(0.2)

    def _judge(self, before: ArrivalState) -> EffectVerdict:
        lvl = CheckLevel.SYSTEM_OF_RECORD
        if not before.reachable:
            return indeterminate(self.substrate, "no readable pre-state baseline; newness cannot be attributed", lvl)
        recs = self._scan()
        if recs is None:
            return indeterminate(self.substrate, "watched folder missing or unreadable", lvl)
        new = [r for r in recs if r["id"] not in before.ids]
        good = [r for r in new if r["arrived"]]
        ev = {"new_files": len(new), "conforming_new_files": len(good)}
        if len(good) == self.expected_count and len(new) == len(good):
            return confirmed(self.substrate, f"{len(good)} new conforming file(s) arrived", lvl,
                             observed_count=len(good), expected_count=self.expected_count, evidence=ev)
        if not new:
            return refuted(self.substrate, "no new file arrived", lvl, observed_count=0,
                           expected_count=self.expected_count, absent=True, evidence=ev)
        return refuted(self.substrate, f"{len(good)} conforming of {len(new)} new file(s); expected "
                       f"exactly {self.expected_count}", lvl, observed_count=len(good),
                       expected_count=self.expected_count, evidence=ev)
