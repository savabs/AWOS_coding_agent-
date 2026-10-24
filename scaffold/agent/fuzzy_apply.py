"""
fuzzy_apply.py — T4 fuzzy-apply ladder for SEARCH/REPLACE edits.

Spec: docs/specs/edit_robustness.md (trick T4, docs/research/trick_book_2026-10.md).
Off unless AWOS_FUZZY_APPLY=1; when off, Verifier keeps its legacy matcher
byte-for-byte.

A small model's SEARCH text is often *almost* a copy of the file: whitespace
drift, the block re-indented, a token mistyped. The ladder tries, in order,
and stops at the first tier that finds exactly one location:

  1 exact        the SEARCH text occurs once in the file
  2 whitespace   lines equal after rstrip + collapsing in-line [ \\t]+ runs;
                 leading indentation must still match exactly
  3 reindent     lines equal after stripping, and the indentation *relative
                 to the block's own minimum* is identical; REPLACE is shifted
                 by the same offset (Aider: replace_part_with_missing_leading_whitespace)
  4 difflib      SequenceMatcher ratio >= 0.9 on the stripped, blank-free text,
                 anchored: some SEARCH line must occur exactly once in the file,
                 every such anchor must fall inside the chosen window, and no
                 other non-overlapping window may also reach the threshold

Safety rule: never apply on ambiguity. More than one location at any tier
is a refusal (with a reason the model can act on), not a fall-through to a
fuzzier tier — a fuzzier tier can only be more ambiguous.
"""

from __future__ import annotations

import difflib
import os
import re
from dataclasses import dataclass
from typing import Optional

ENV_VAR = "AWOS_FUZZY_APPLY"
THRESHOLD = 0.9
#: an anchor line must be at least this long (stripped) and contain a letter/digit
ANCHOR_MIN_CHARS = 6
TIER_NAMES = {1: "exact", 2: "whitespace", 3: "reindent", 4: "difflib"}

_WS_RUN = re.compile(r"[ \t]+")


def enabled() -> bool:
    return os.getenv(ENV_VAR, "").strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Match:
    matched: bool
    content: str
    tier: int = 0               # 1..4 when matched
    reason: str = ""            # why nothing was applied (refusal / no match)
    start: int = -1             # 0-based first replaced line (tiers 2-4)
    length: int = 0             # number of file lines replaced (tiers 2-4)
    ratio: float = 1.0

    @property
    def tier_name(self) -> str:
        return TIER_NAMES.get(self.tier, "none")

    @property
    def label(self) -> str:
        """The tier string Verifier reports ("exact" stays "exact")."""
        if not self.matched:
            return "refused" if self.reason.startswith("ambiguous") else "none"
        if self.tier == 1:
            return "exact"
        if self.tier == 4:
            return f"ladder{self.tier}:{self.tier_name}({self.ratio:.0%})"
        return f"ladder{self.tier}:{self.tier_name}"


def _indent(line: str) -> str:
    return line[:len(line) - len(line.lstrip(" \t"))]


def _ws_key(line: str) -> str:
    body = line.lstrip(" \t")
    return (_indent(line) + _WS_RUN.sub(" ", body)).rstrip()


def _body_key(line: str) -> str:
    return _WS_RUN.sub(" ", line.strip())


def _strip_outer_blank(lines: list[str]) -> tuple[list[str], int, int]:
    lo, hi = 0, len(lines)
    while lo < hi and not lines[lo].strip():
        lo += 1
    while hi > lo and not lines[hi - 1].strip():
        hi -= 1
    return lines[lo:hi], lo, len(lines) - hi


def _common_indent(lines: list[str]) -> str:
    indents = [_indent(l) for l in lines if l.strip()]
    if not indents:
        return ""
    prefix = os.path.commonprefix(indents)
    return prefix


def _find_windows(o_keys: list[str], s_keys: list[str]) -> list[int]:
    n = len(s_keys)
    if n == 0 or n > len(o_keys):
        return []
    first = s_keys[0]
    return [i for i in range(len(o_keys) - n + 1)
            if o_keys[i] == first and o_keys[i:i + n] == s_keys]


def _reindent(r_lines: list[str], s_base: str, f_base: str) -> Optional[list[str]]:
    """Shift REPLACE from the SEARCH's base indent to the file's; None if a
    REPLACE line sits left of the SEARCH base (no consistent mapping)."""
    if s_base == f_base:
        return list(r_lines)
    out = []
    for line in r_lines:
        if not line.strip():
            out.append(line)
        elif line.startswith(s_base):
            out.append(f_base + line[len(s_base):])
        else:
            return None
    return out


def _file_base(o_line: str, s_line: str, s_base: str) -> Optional[str]:
    """The file's base indent implied by one aligned (file, search) line pair."""
    s_ind, o_ind = _indent(s_line), _indent(o_line)
    if not s_ind.startswith(s_base):
        return None
    rel = s_ind[len(s_base):]
    if rel and not o_ind.endswith(rel):
        return None
    return o_ind[:len(o_ind) - len(rel)] if rel else o_ind


def _splice(o_lines: list[str], start: int, length: int, r_lines: list[str]) -> str:
    return "\n".join(o_lines[:start] + r_lines + o_lines[start + length:])


def _is_anchor(stripped: str) -> bool:
    return len(stripped) >= ANCHOR_MIN_CHARS and any(c.isalnum() for c in stripped)


def _joined(lines: list[str]) -> str:
    return "\n".join(_body_key(l) for l in lines if l.strip())


def apply(original: str, search: str, replace: str) -> Match:
    """Run the ladder. Never raises; Match.matched False leaves `original`."""
    # Tier 1: exact, unique.
    count = original.count(search) if search else 0
    if count == 1:
        return Match(True, original.replace(search, replace, 1), tier=1)
    if count > 1:
        return Match(False, original, reason=(
            f"ambiguous: SEARCH occurs {count} times; include more surrounding lines"))

    o_lines = original.split("\n")
    s_all = search.split("\n")
    s_lines, s_lead, s_trail = _strip_outer_blank(s_all)
    if not s_lines:
        return Match(False, original, reason="empty SEARCH")
    r_lines = replace.split("\n")
    # Drop the same outer blank lines from REPLACE that were dropped from SEARCH.
    r_lead = 0
    while r_lead < min(s_lead, len(r_lines)) and not r_lines[r_lead].strip():
        r_lead += 1
    r_lines = r_lines[r_lead:]
    r_trail = 0
    while r_trail < min(s_trail, len(r_lines)) and not r_lines[len(r_lines) - 1 - r_trail].strip():
        r_trail += 1
    if r_trail:
        r_lines = r_lines[:len(r_lines) - r_trail]
    n = len(s_lines)

    # Tier 2: whitespace-normalised (indentation kept).
    hits = _find_windows([_ws_key(l) for l in o_lines], [_ws_key(l) for l in s_lines])
    if len(hits) == 1:
        return Match(True, _splice(o_lines, hits[0], n, r_lines), tier=2,
                     start=hits[0], length=n)
    if len(hits) > 1:
        return Match(False, original, reason=(
            f"ambiguous: SEARCH matches {len(hits)} places up to whitespace "
            f"(lines {', '.join(str(h + 1) for h in hits[:5])})"))

    # Tier 3: relative indentation.
    s_base = _common_indent(s_lines)
    hits3: list[tuple[int, str]] = []
    for i in _find_windows([_body_key(l) for l in o_lines], [_body_key(l) for l in s_lines]):
        window = o_lines[i:i + n]
        f_base = None
        consistent = True
        for o_l, s_l in zip(window, s_lines):
            if not s_l.strip():
                continue
            fb = _file_base(o_l, s_l, s_base)
            if fb is None or (f_base is not None and fb != f_base):
                consistent = False
                break
            f_base = fb
        if consistent and f_base is not None:
            hits3.append((i, f_base))
    if len(hits3) > 1:
        return Match(False, original, reason=(
            f"ambiguous: SEARCH matches {len(hits3)} places up to indentation "
            f"(lines {', '.join(str(h + 1) for h, _ in hits3[:5])})"))
    if len(hits3) == 1:
        i, f_base = hits3[0]
        shifted = _reindent(r_lines, s_base, f_base)
        if shifted is None:
            return Match(False, original, reason=(
                "REPLACE is indented left of SEARCH; cannot re-indent it consistently"))
        return Match(True, _splice(o_lines, i, n, shifted), tier=3, start=i, length=n)

    # Tier 4: difflib, anchored and unique.
    return _difflib_tier(original, o_lines, s_lines, r_lines, s_base)


def _difflib_tier(original: str, o_lines: list[str], s_lines: list[str],
                  r_lines: list[str], s_base: str) -> Match:
    n = len(s_lines)
    occurrences: dict[str, list[int]] = {}
    for i, line in enumerate(o_lines):
        key = _body_key(line)
        if key:
            occurrences.setdefault(key, []).append(i)
    anchors: list[tuple[int, int]] = []          # (file index, search index)
    for j, line in enumerate(s_lines):
        key = _body_key(line)
        if _is_anchor(key) and len(occurrences.get(key, ())) == 1:
            anchors.append((occurrences[key][0], j))
    if not anchors:
        return Match(False, original, reason=(
            "no match: SEARCH is not in the file and no SEARCH line occurs exactly "
            "once in it (no unique anchor)"))
    target = _joined(s_lines)
    if not target:
        return Match(False, original, reason="no match")
    sm = difflib.SequenceMatcher(autojunk=False)
    sm.set_seq2(target)

    def ratio(start: int, length: int) -> float:
        if start < 0 or length <= 0 or start + length > len(o_lines):
            return 0.0
        sm.set_seq1(_joined(o_lines[start:start + length]))
        if sm.real_quick_ratio() < THRESHOLD or sm.quick_ratio() < THRESHOLD:
            return 0.0
        return sm.ratio()

    best: Optional[tuple[float, int, int, int]] = None   # (ratio, -drift, start, length)
    for a, j in anchors:
        base = a - j
        for ds in (0, -1, 1):
            for dl in (0, -1, 1):
                start, length = base + ds, n + dl
                if not (start <= a < start + length):
                    continue
                r = ratio(start, length)
                if r < THRESHOLD:
                    continue
                cand = (r, -(abs(ds) + abs(dl)), start, length)
                if best is None or cand[:2] > best[:2]:
                    best = cand
    if best is None:
        return Match(False, original, reason=(
            f"no match: closest anchored window is below {THRESHOLD:.0%} similar"))
    r, _, start, length = best
    end = start + length
    # Every unique anchor must sit inside the window: SEARCH lines that exist
    # elsewhere mean the block spans (or belongs to) another place.
    outside = [a for a, _ in anchors if not (start <= a < end)]
    if outside:
        return Match(False, original, reason=(
            f"ambiguous: SEARCH lines anchor both near line {start + 1} and line "
            f"{outside[0] + 1}"))
    # No other non-overlapping window may also reach the threshold.
    for i in range(0, len(o_lines) - n + 1):
        if i + n <= start or i >= end:
            if ratio(i, n) >= THRESHOLD:
                return Match(False, original, reason=(
                    f"ambiguous: SEARCH is >= {THRESHOLD:.0%} similar to lines "
                    f"{start + 1}-{end} and to lines {i + 1}-{i + n}"))
    # Re-indent REPLACE from the first anchor's indentation.
    a0, j0 = next((a, j) for a, j in anchors if start <= a < end)
    f_base = _file_base(o_lines[a0], s_lines[j0], s_base)
    shifted = _reindent(r_lines, s_base, f_base) if f_base is not None else None
    if shifted is None:
        return Match(False, original, reason=(
            "no match: SEARCH indentation does not map onto the file consistently"))
    return Match(True, _splice(o_lines, start, length, shifted), tier=4,
                 start=start, length=length, ratio=r)
