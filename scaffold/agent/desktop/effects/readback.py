# The read-back posture (same-screen is weak, a re-navigated / different-path
# read is stronger, unreadable -> INDETERMINATE, readable-but-different ->
# REFUTED, never guess CONFIRMED) is adapted from OpenAdapt Flow
# openadapt_flow/runtime/effects/onscreen.py and ui_readback.py, Copyright (c)
# 2026 OpenAdapt.AI (MLDSAI Inc.), MIT License -- see LICENSE-OpenAdapt here.
"""UI read-back effect: "the screen shows the value (or acknowledgement) we expect".

    eff = UIReadback(read=lambda: observe_raw(),           # raw AX tree / text
                     expected=lambda: broker.reveal_for_check("k"),
                     kind="code", source="structure", exact=False)
    eff.verify(eff.capture())

``read`` returns the RAW observation (an AX tree dict/list, or text) -- the
enforcer side reads it before masking, the model only ever sees the masked
copy. ``expected`` is a zero-arg callable so the contract object never holds
the value. A verdict carries a match flag, never the value.

Strength: ``different_path=False`` (the acting screen still shows it) is
CheckLevel.SAME_SCREEN; ``different_path=True`` (the caller re-navigated to
the record, e.g. reloaded the key list) is CheckLevel.REOPENED. Neither is a
system-of-record read; ``outcome_for(min_level=...)`` decides whether it is
enough for VERIFIED.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional

from .outcome import CheckLevel, EffectVerdict, confirmed, indeterminate, refuted
from .readback_values import ValueKind, compare_value

TEXT_KEYS = ("title", "name", "label", "value", "description", "placeholder", "help", "text")


def observation_text(obs: Any, keys: Iterable[str] = TEXT_KEYS) -> str:
    """Flatten an AX-like tree (dict with children / list / str) to text lines."""
    out: list = []

    def walk(n):
        if n is None:
            return
        if isinstance(n, str):
            out.append(n)
        elif isinstance(n, dict):
            for k in keys:
                v = n.get(k)
                if isinstance(v, str) and v.strip():
                    out.append(v)
            for c in n.get("children") or []:
                walk(c)
        elif isinstance(n, (list, tuple)):
            for c in n:
                walk(c)

    walk(obs)
    return "\n".join(out)


@dataclass
class UIReadback:
    read: Callable[[], Any]
    expected: Callable[[], str]
    kind: ValueKind = "text"
    source: str = "structure"            # structure (AX/DOM) | ocr
    exact: bool = False                  # False: expected may sit inside a longer read
    different_path: bool = False
    substrate: str = "ui_readback"

    def capture(self) -> None:           # a read-back has no meaningful pre-state
        return None

    def verify(self, before: Any = None) -> EffectVerdict:
        lvl = CheckLevel.REOPENED if self.different_path else CheckLevel.SAME_SCREEN
        try:
            raw = self.read()
        except Exception as ex:          # the screen could not be read
            return indeterminate(self.substrate, f"read failed ({type(ex).__name__})", lvl)
        if raw is None:
            return indeterminate(self.substrate, "nothing readable on screen", lvl)
        text = raw if isinstance(raw, str) else observation_text(raw)
        if not text.strip():
            return indeterminate(self.substrate, "screen text empty", lvl)
        try:
            want = self.expected()
        except Exception as ex:
            return indeterminate(self.substrate, f"expected value unavailable ({type(ex).__name__})", lvl)
        res = compare_value(text, want, kind=self.kind, source="ocr" if self.source == "ocr" else "structure",
                            exact=self.exact)
        ev = {"comparison": res, "source": self.source}
        if res == "match":
            return confirmed(self.substrate, "screen shows the expected value", lvl,
                             observed_count=1, expected_count=1, evidence=ev)
        if res == "ambiguous":
            return indeterminate(self.substrate, "read differs only in OCR-confusable glyphs", lvl, evidence=ev)
        return refuted(self.substrate, "screen is readable and does not show the expected value", lvl,
                       observed_count=0, expected_count=1, evidence=ev)
