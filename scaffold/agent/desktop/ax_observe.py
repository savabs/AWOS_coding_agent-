"""
ax_observe.py — step-to-step AX observations: stable IDs, diffs, no_change.

D16 in docs/research/rea_synthesis_2026-10-16.md, after ChatGPT "Work with
Apps": perception is accessibility text, one line per element, each with an
ID that survives between steps; the model gets a diff only when the diff is
shorter than the full tree, and an explicit "no_change" when nothing moved.

    obs = Observer(viewport=(0, 0, 1440, 900))
    m1 = obs.observe(tree)          # m1.kind == "full"
    m2 = obs.observe(tree2)         # "diff" | "full" | "no_change"
    print(m2.text)

Stable IDs (``e1``, ``e2``, ...; never reused within one Observer):
  * an element with an AX identifier / DOM id is keyed by it;
  * otherwise by (role, value-free label, chain of labelled ancestors), plus
    its ordinal among elements with that same key in reading order.
  The value of an editable control is not part of the key, so typing into a
  field keeps its ID; unlabelled wrapper groups are not part of the chain, so
  a wrapper appearing or disappearing does not renumber everything.

Message texts:
  full       ``obs 3: full, 12 elements, app "Finder"`` + notes + one line each
  diff       ``obs 4: diff vs obs 3 (+1 -0 ~1)`` + ``+ line`` / ``- [e7] role "x"`` /
             ``~ line`` (+ changed notes)
  no_change  ``obs 5: no_change since obs 4 (12 elements)``
Range notes: ``note: showing 1–300 of 412 elements`` when the element cap
cuts the list, and ``note: list "Inbox": showing 5–24 of 340`` from
ax_compress.collect() when a list hides rows.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .ax_compress import EDITABLE, Element, collect, norm_role


@dataclass
class Observation:
    seq: int
    kind: str                         # full | diff | no_change
    text: str                         # what the model is shown
    elements: List[Element]
    notes: List[str]
    app: str = ""
    full_text: str = ""               # always the full rendering (logs, fallbacks)
    added: List[str] = field(default_factory=list)      # sids
    removed: List[str] = field(default_factory=list)
    changed: List[str] = field(default_factory=list)

    @property
    def by_sid(self) -> Dict[str, Element]:
        return {e.sid: e for e in self.elements}

    def element(self, sid: str) -> Optional[Element]:
        return self.by_sid.get(sid)

    @property
    def focused(self) -> Optional[Element]:
        return next((e for e in self.elements if e.focused), None)

    @property
    def default_button(self) -> Optional[Element]:
        """The button Return activates: a modal's default first."""
        cands = [e for e in self.elements if e.is_default and e.enabled]
        return next((e for e in cands if e.modal), cands[0] if cands else None)


def _app_name(tree: Any) -> str:
    roots = tree if isinstance(tree, list) else [tree]
    for r in roots:
        if isinstance(r, dict) and norm_role(r.get("role") or r.get("tag")) == "application":
            return str(r.get("title") or r.get("name") or "")[:80]
    return ""


class Observer:
    """Turns successive AX trees into full / diff / no_change messages."""

    def __init__(self, viewport: Optional[Tuple[float, float, float, float]] = None,
                 max_elements: int = 300):
        self.viewport = viewport
        self.max_elements = max_elements
        self.seq = 0
        self.latest: Optional[Observation] = None
        self._ids: Dict[tuple, str] = {}
        self._next = 1

    # -- identity -------------------------------------------------------------
    def _assign(self, elems: List[Element]) -> None:
        seen: Dict[tuple, int] = {}
        for e in elems:
            label = e.label if e.role in EDITABLE else (e.label or e.text)
            base = ("id", e.ident) if e.ident else ("k", e.role, label, e.anc)
            n = seen.get(base, 0)
            seen[base] = n + 1
            key = base + (n,)
            sid = self._ids.get(key)
            if sid is None:
                sid = f"e{self._next}"
                self._next += 1
                self._ids[key] = sid
            e.sid = sid

    # -- observation ----------------------------------------------------------
    def observe(self, tree: Any) -> Observation:
        self.seq += 1
        elems, notes = collect(tree, self.viewport)
        self._assign(elems)
        total = len(elems)
        elems = elems[:self.max_elements]
        for i, e in enumerate(elems):
            e.index = i
        if total > len(elems):
            notes = [f"showing 1–{len(elems)} of {total} elements"] + notes
        app = _app_name(tree)
        body = [f"note: {n}" for n in notes] + [e.render() for e in elems]
        head = f"obs {self.seq}: full, {len(elems)} elements" + (f', app "{app}"' if app else "")
        full = "\n".join([head] + body)
        obs = Observation(self.seq, "full", full, elems, notes, app, full)
        prev, self.latest = self.latest, obs
        if prev is None or prev.app != app:
            return obs
        prev_body = [f"note: {n}" for n in prev.notes] + [e.render() for e in prev.elements]
        if body == prev_body:
            obs.kind = "no_change"
            obs.text = f"obs {self.seq}: no_change since obs {prev.seq} ({len(elems)} elements)"
            return obs
        p = {e.sid: e.render() for e in prev.elements}
        c = {e.sid: e.render() for e in elems}
        obs.added = [e.sid for e in elems if e.sid not in p]
        obs.removed = [e.sid for e in prev.elements if e.sid not in c]
        obs.changed = [e.sid for e in elems if e.sid in p and p[e.sid] != c[e.sid]]
        if not (obs.added or obs.removed or obs.changed) and notes == prev.notes:
            return obs                      # only the order moved: the diff can't say that
        lines = [f"obs {self.seq}: diff vs obs {prev.seq} "
                 f"(+{len(obs.added)} -{len(obs.removed)} ~{len(obs.changed)})"]
        if notes != prev.notes:
            lines += [f"note: {n}" for n in notes] or ["note: (no range notes)"]
        lines += [f"+ {c[s]}" for s in obs.added]
        old = {e.sid: e for e in prev.elements}
        lines += [f'- [{s}] {old[s].role}' + (f' "{old[s].text}"' if old[s].text else "")
                  for s in obs.removed]
        lines += [f"~ {c[s]}" for s in obs.changed]
        text = "\n".join(lines)
        if len(text) < len(full):
            obs.kind, obs.text = "diff", text
        return obs
