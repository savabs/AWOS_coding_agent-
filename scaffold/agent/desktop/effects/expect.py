"""expect.py — expected-effects verification: an Expectation induced from a taught demo,
checked deterministically after every replay. Code decides PASS; a model may only veto.

    exp = induce(demo_before, demo_after, kind="act")         # once, when the owner teaches
    ...replay...
    res = check(exp, run_before, run_after, judge=None)       # every run
    res.verdict in ("verified", "failed", "unverified")

What the checker sees is an ``Observation`` built by an adapter (web readback, pack files):

    url / text / statuses / alerts   the final page (address, visible text, role=status
                                     banners, role=alert banners)
    reads   {key: [atom, ...]}       a READBACK of the system of record: for each key (a view
                                     re-opened after the run, a list page, a folder) the
                                     multiset of atoms on it (row text, field=value, ...).
                                     A key mapped to None was unreadable.
    files   [{name, size, sha256}]   files that arrived because of this run
    answer / ended                   the final done("...") text; how the run ended
                                     ("done" | "needs_user" | ...)

Expectation kinds (each one gets its own verdict; ``required`` ones decide the run):

    atom_delta     read[key] count of one atom changed by exactly N (+1 added, -1 removed,
                   0 is never induced). Covers "value present", "row gone", duplicates (+2).
    read_unchanged every OTHER atom of read[key] kept its count ("nothing else changed /
                   deleted"); the demo's own changes are excluded
    row_count      number of row atoms ("row:" prefix) on read[key] changed by N
    no_error       the final page shows no role=alert banner
    files_arrived  exactly N new files (at least N if value.at_least, as pack_hook sets),
                   extension in the taught set, size within bounds
    files_distinct no two arrived files share a sha256 (a double download)
    answer         the final answer contains the taught value (case/space-insensitive)
    ended          the run ended the taught way (needs_user at a checkpoint)
    banner         a success banner like the demo's is on the final page    (advisory)
    url            the final URL matches the demo's, normalised             (advisory)
    final_text     a value the demo added is visible on the final page      (advisory)

Run verdict (closed set):
    VERIFIED    every required expectation CONFIRMED (and at least one exists; an act demo
                whose effect was invisible to every readback has none -> never VERIFIED)
    FAILED      any required expectation REFUTED -- loud: the reason names each one
    UNVERIFIED  otherwise (missing / unreadable evidence, nothing to check, judge veto)
Advisory expectations are reported but never change the run verdict: a success banner
alone proves nothing, and a missing one on a correct run is not a failure.

Judge hook: ``judge(expectation, after, result) -> None | True | False | str``. It is
called ONLY when the deterministic verdict is VERIFIED, so it cannot grant a pass;
False or a non-empty string vetoes (VERIFIED -> UNVERIFIED, reason kept); a judge that
raises also downgrades (no second opinion -> no stamp). Anything else = agree.

Reasons never carry observed values: an expectation is named by its ``label`` (key, kind,
index), and evidence holds counts and one-way digests only (see outcome.py).
"""
from __future__ import annotations

import fnmatch
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional

from .outcome import Outcome, Verdict, digest

RUN_VERIFIED = "verified"
RUN_FAILED = "failed"
RUN_UNVERIFIED = "unverified"
RUN_VERDICTS = (RUN_VERIFIED, RUN_FAILED, RUN_UNVERIFIED)

KINDS = ("atom_delta", "read_unchanged", "row_count", "no_error", "files_arrived", "files_distinct",
         "answer", "ended", "banner", "url", "final_text")
ROW_PREFIX = "row:"
Judge = Callable[["Expectation", "Observation", "RunCheck"], Any]


# ── evidence ────────────────────────────────────────────────────────────────
@dataclass
class Observation:
    url: Optional[str] = None
    text: Optional[str] = None
    statuses: Optional[List[str]] = None
    alerts: Optional[List[str]] = None
    reads: Dict[str, Optional[List[str]]] = field(default_factory=dict)
    files: Optional[List[Dict[str, Any]]] = None
    answer: Optional[str] = None
    ended: Optional[str] = None


@dataclass(frozen=True)
class Expect:
    kind: str
    key: str = ""
    value: Any = None
    required: bool = True
    label: str = ""

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"unknown expectation kind {self.kind!r}")


@dataclass
class Expectation:
    """What a correct replay must change (and leave alone), induced from one demo."""
    kind: str = "act"                          # act | answer | pause | files
    items: List[Expect] = field(default_factory=list)
    observable: bool = True                    # False: the demo's effect was invisible to readback
    note: str = ""

    @property
    def required(self) -> List[Expect]:
        return [e for e in self.items if e.required]

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "observable": self.observable, "note": self.note,
                "items": [asdict(e) for e in self.items]}

    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> "Expectation":
        return Expectation(str(d.get("kind", "act")), [Expect(**e) for e in d.get("items", [])],
                           bool(d.get("observable", True)), str(d.get("note", "")))


@dataclass(frozen=True)
class ExpectVerdict:
    expect: Expect
    verdict: Verdict
    reason: str
    evidence: Dict[str, Any] = field(default_factory=dict)

    def to_record(self) -> Dict[str, Any]:
        return {"kind": self.expect.kind, "label": self.expect.label, "required": self.expect.required,
                "verdict": self.verdict.value, "reason": self.reason, "evidence": dict(self.evidence)}


@dataclass
class RunCheck:
    verdict: str
    reason: str
    results: List[ExpectVerdict] = field(default_factory=list)
    judge: Optional[Dict[str, Any]] = None

    @property
    def verified(self) -> bool:
        return self.verdict == RUN_VERIFIED

    @property
    def outcome(self) -> Outcome:
        return {RUN_VERIFIED: Outcome.VERIFIED, RUN_FAILED: Outcome.RECONCILIATION_REQUIRED}.get(
            self.verdict, Outcome.COMPLETED_UNVERIFIED)

    def refuted(self, required_only: bool = True) -> List[ExpectVerdict]:
        return [r for r in self.results if r.verdict is Verdict.REFUTED
                and (r.expect.required or not required_only)]

    def to_record(self) -> Dict[str, Any]:
        return {"verdict": self.verdict, "reason": self.reason, "judge": self.judge,
                "results": [r.to_record() for r in self.results]}


# ── induction (from one taught demo) ────────────────────────────────────────
def _norm(s: Any) -> str:
    return " ".join(str(s or "").split())


def _ext(name: str) -> str:
    return Path(str(name)).suffix.lower().lstrip(".")


def induce(before: Observation, after: Observation, *, kind: str = "act",
           url_normalize: Optional[Callable[[str], str]] = None,
           require_url: bool = False, require_final_text: bool = False,
           answer: Optional[str] = None) -> Expectation:
    """Turn a demo (readback before / after, final page, files) into an Expectation.
    ``answer``: the value inside the demo's final answer that a replay must report (default:
    the whole demo answer)."""
    items: List[Expect] = []
    changed_keys = 0
    for key in sorted(after.reads):
        a, b = after.reads.get(key), before.reads.get(key)
        if a is None or b is None:                  # unreadable during the demo: nothing learnt
            continue
        ca, cb = Counter(a), Counter(b)
        delta = {x: ca[x] - cb[x] for x in set(ca) | set(cb) if ca[x] != cb[x]}
        for i, x in enumerate(sorted(delta)):
            items.append(Expect("atom_delta", key, {"atom": x, "delta": delta[x]},
                                label=f"{key}: item #{i} {delta[x]:+d}"))
            if delta[x] > 0 and after.text is not None:
                val = _norm(x.split(":", 1)[-1].split("=", 1)[-1])
                if val and val in _norm(after.text):
                    items.append(Expect("final_text", key, val, required=require_final_text,
                                        label=f"{key}: item #{i} visible on final page"))
        items.append(Expect("read_unchanged", key, {"except": sorted(delta)},
                            label=f"{key}: nothing else changed"))
        rows = sum(v for x, v in delta.items() if x.startswith(ROW_PREFIX))
        if rows:
            items.append(Expect("row_count", key, rows, label=f"{key}: row count {rows:+d}"))
        changed_keys += bool(delta)
    if kind == "answer" and _norm(answer or after.answer):
        items.append(Expect("answer", "", _norm(answer or after.answer), label="final answer"))
    if kind == "pause" or (after.ended and after.ended != "done"):
        items.append(Expect("ended", "", after.ended or "needs_user", label="run ended the taught way"))
    if after.files is not None and (after.files or kind == "files"):
        sizes = [int(f.get("size") or 0) for f in after.files]
        items.append(Expect("files_arrived", "", {
            "count": len(after.files), "types": sorted({_ext(f.get("name", "")) for f in after.files}),
            "min_size": max(1, min(sizes) // 4) if sizes else 1,
            "max_size": max(sizes) * 20 if sizes else None}, label="files arrived"))
        items.append(Expect("files_distinct", "", None, label="no duplicate file"))
    if after.alerts is not None and not after.alerts:
        items.append(Expect("no_error", "", None, label="no error banner"))
    if after.statuses:
        items.append(Expect("banner", "", [_norm(s) for s in after.statuses], required=False,
                            label="success banner"))
    if after.url:
        u = url_normalize(after.url) if url_normalize else after.url
        items.append(Expect("url", "", u, required=require_url, label="final URL"))
    observable = True
    note = ""
    if kind == "act" and not changed_keys:     # (answer / pause: read_unchanged = "wrote nothing")
        observable, note = False, "the demo changed nothing any readback could see"
    if kind == "files" and not after.files:
        observable, note = False, "the demo produced no file"
    return Expectation(kind, items, observable, note)


def induce_files(files: Iterable[Mapping[str, Any]]) -> Expectation:
    """Expectation for a download-only skill (pack): N files of the taught types/sizes."""
    return induce(Observation(files=[]), Observation(files=[dict(f) for f in files]), kind="files")


# ── checking (every replay) ─────────────────────────────────────────────────
def _ok(e: Expect, reason: str, **ev) -> ExpectVerdict:
    return ExpectVerdict(e, Verdict.CONFIRMED, reason, ev)


def _bad(e: Expect, reason: str, **ev) -> ExpectVerdict:
    return ExpectVerdict(e, Verdict.REFUTED, reason, ev)


def _unk(e: Expect, reason: str, **ev) -> ExpectVerdict:
    return ExpectVerdict(e, Verdict.INDETERMINATE, reason, ev)


def _reads(e: Expect, before: Observation, after: Observation):
    a, b = after.reads.get(e.key), before.reads.get(e.key)
    if a is None or b is None:
        return None, None
    return Counter(a), Counter(b)


def check_one(e: Expect, before: Observation, after: Observation) -> ExpectVerdict:
    k = e.kind
    if k in ("atom_delta", "read_unchanged", "row_count"):
        ca, cb = _reads(e, before, after)
        if ca is None:
            return _unk(e, f"readback {e.key!r} missing or unreadable")
        if k == "atom_delta":
            atom, want = e.value["atom"], int(e.value["delta"])
            got = ca[atom] - cb[atom]
            ev = {"atom": digest(atom)[:23], "delta": got, "expected_delta": want}
            if got == want:
                return _ok(e, f"changed by {got:+d} as taught", **ev)
            what = ("missing" if want > 0 and got <= 0 else "duplicated" if got > want > 0
                    else "still present" if want < 0 and got >= 0 else "wrong count")
            return _bad(e, f"{what}: changed by {got:+d}, expected {want:+d}", **ev)
        if k == "read_unchanged":
            skip = set(e.value.get("except", []))
            moved = {x: ca[x] - cb[x] for x in set(ca) | set(cb) if x not in skip and ca[x] != cb[x]}
            lost = sum(-d for d in moved.values() if d < 0)
            extra = sum(d for d in moved.values() if d > 0)
            if not moved:
                return _ok(e, "nothing else changed", atoms=sum(cb.values()))
            return _bad(e, f"collateral change: {lost} item(s) gone, {extra} unexpected item(s)",
                        lost=lost, extra=extra)
        rows = lambda c: sum(v for x, v in c.items() if x.startswith(ROW_PREFIX))  # noqa: E731
        got = rows(ca) - rows(cb)
        return (_ok if got == int(e.value) else _bad)(e, f"row count changed by {got:+d}, expected "
                                                        f"{int(e.value):+d}", delta=got)
    if k == "no_error":
        if after.alerts is None:
            return _unk(e, "final page alerts not observed")
        return _ok(e, "no error banner") if not after.alerts else \
            _bad(e, f"{len(after.alerts)} error banner(s) on the final page", alerts=len(after.alerts))
    if k == "answer":
        if after.answer is None:
            return _unk(e, "no final answer recorded")
        return _ok(e, "answer contains the taught value") if _norm(e.value).lower() in _norm(after.answer).lower() \
            else _bad(e, "answer does not contain the taught value", answer=digest(_norm(after.answer))[:23])
    if k == "ended":
        if after.ended is None:
            return _unk(e, "how the run ended was not recorded")
        return (_ok if after.ended == e.value else _bad)(e, f"run ended with {after.ended!r} "
                                                          f"(taught {e.value!r})")
    if k in ("files_arrived", "files_distinct"):
        if after.files is None:
            return _unk(e, "arrived files not observed")
        if k == "files_distinct":
            shas = [f.get("sha256") for f in after.files]
            if any(not s for s in shas):
                return _unk(e, "a file has no content hash")
            dup = len(shas) - len(set(shas))
            return _ok(e, "all files distinct") if not dup else _bad(e, f"{dup} duplicate file(s)", duplicates=dup)
        v = e.value or {}
        n, types = len(after.files), set(v.get("types") or [])
        bad_type = [f for f in after.files if types and _ext(f.get("name", "")) not in types]
        lo, hi = int(v.get("min_size") or 1), v.get("max_size")
        bad_size = [f for f in after.files if int(f.get("size") or 0) < lo or (hi and int(f.get("size") or 0) > hi)]
        ev = {"count": n, "expected_count": v.get("count"), "bad_type": len(bad_type), "bad_size": len(bad_size)}
        want, floor = int(v.get("count") or 0), bool(v.get("at_least"))
        if n < want or (n > want and not floor):
            return _bad(e, f"{n} file(s) arrived, expected {'at least ' if floor else ''}{want}", **ev)
        if bad_type or bad_size:
            return _bad(e, f"{len(bad_type)} wrong type, {len(bad_size)} out-of-range size", **ev)
        return _ok(e, f"{n} file(s) of the taught type and size", **ev)
    if k == "banner":
        if after.statuses is None:
            return _unk(e, "final page banners not observed")
        got = {_norm(s) for s in after.statuses}
        return _ok(e, "success banner shown") if any(s in got for s in e.value or []) else \
            _bad(e, "the demo's success banner is not on the final page")
    if k == "url":
        if after.url is None:
            return _unk(e, "final URL not observed")
        return (_ok if fnmatch.fnmatchcase(after.url, e.value) or after.url == e.value else _bad)(
            e, "final URL " + ("matches" if fnmatch.fnmatchcase(after.url, e.value) else "differs from") + " the demo's")
    if k == "final_text":
        if after.text is None:
            return _unk(e, "final page text not observed")
        return (_ok if _norm(e.value) in _norm(after.text) else _bad)(e, "taught value " + (
            "visible" if _norm(e.value) in _norm(after.text) else "not visible") + " on the final page")
    return _unk(e, f"no checker for {k!r}")                      # pragma: no cover


def check(exp: Expectation, before: Observation, after: Observation, *,
          judge: Optional[Judge] = None) -> RunCheck:
    results = [check_one(e, before, after) for e in exp.items]
    req = [r for r in results if r.expect.required]
    bad = [r for r in req if r.verdict is Verdict.REFUTED]
    unk = [r for r in req if r.verdict is Verdict.INDETERMINATE]
    if bad:
        return RunCheck(RUN_FAILED, "FAILED: " + "; ".join(f"{r.expect.label}: {r.reason}" for r in bad[:6])
                        + (f" (+{len(bad) - 6} more)" if len(bad) > 6 else ""), results)
    if not exp.observable or not req:
        return RunCheck(RUN_UNVERIFIED, exp.note or "no deterministic expectation to check", results)
    if unk:
        return RunCheck(RUN_UNVERIFIED, "missing evidence: " + "; ".join(
            f"{r.expect.label}: {r.reason}" for r in unk[:6]), results)
    res = RunCheck(RUN_VERIFIED, f"all {len(req)} required expectation(s) confirmed", results)
    return apply_judge(res, exp, after, judge)


def apply_judge(res: RunCheck, exp: Expectation, after: Observation, judge: Optional[Judge]) -> RunCheck:
    """Second opinion. Only ever consulted on VERIFIED; can only turn it into UNVERIFIED."""
    if judge is None or res.verdict != RUN_VERIFIED:
        return res
    try:
        said = judge(exp, after, res)
    except Exception as e:                                   # no second opinion -> no stamp
        res.verdict, res.reason = RUN_UNVERIFIED, f"judge error ({type(e).__name__}); not stamped verified"
        res.judge = {"veto": True, "error": type(e).__name__}
        return res
    veto = said is False or (isinstance(said, str) and bool(said.strip()))
    res.judge = {"veto": veto, "reason": _norm(said)[:200] if isinstance(said, str) else None}
    if veto:
        res.verdict = RUN_UNVERIFIED
        res.reason = "judge vetoed the pass" + (f": {_norm(said)[:200]}" if isinstance(said, str) else "")
    return res


# ── pack adapter (scaffold/agent/desktop/pack/run.py ``expect=`` hook) ────────
def pack_observation(entry: Mapping[str, Any]) -> Observation:
    """Files a pack source holds this month, as an Observation (name, size, sha256).
    Same rule as pack/run.py ``_good``: type_ok and not flagged (wrong period / failed document
    checks), so a stray download the pack already set aside cannot refute a correct month."""
    files = [{"name": Path(str(f.get("path", ""))).name, "size": f.get("size"), "sha256": f.get("sha256")}
             for f in entry.get("files") or [] if f.get("type_ok") and not f.get("flagged")]
    return Observation(files=files)


def _at_least(exp: Expectation) -> Expectation:
    """The pack accepts >= expected.count good files; match it, so a month with more good
    documents than the demo is not FAILED (a double download is still caught by files_distinct)."""
    items = [Expect(i.kind, i.key, dict(i.value or {}, at_least=True), i.required, i.label)
             if i.kind == "files_arrived" else i for i in exp.items]
    return Expectation(exp.kind, items, exp.observable, exp.note)


def pack_hook(expectations: Mapping[str, Expectation], judge: Optional[Judge] = None):
    """``run_month(..., expect=pack_hook({source_id: induce_files(taught_files)}))``.
    Sources without an expectation get None (no opinion; their status is left alone)."""
    def hook(source: Any, period: Any, entry: Mapping[str, Any]) -> Optional[RunCheck]:
        exp = expectations.get(getattr(source, "id", source))
        if exp is None:
            return None
        return check(_at_least(exp), Observation(files=[]), pack_observation(entry), judge=judge)
    return hook
