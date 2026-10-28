"""
core.py — the repairer (rule tier -> model tier -> safety gate) and the taught-skill hook.

    rp = Repairer(picker=None)                       # rule-only; picker=LocalPicker() adds the model tier
    res = rp.refind(fp, candidates)                  # RepairResult(status=repaired | not_found | ambiguous
                                                     #              | needs_approval, candidate, tier, reason)

    hook = SkillRepair(rp)                           # pack/runner.py: LiveRunner(repair=hook)
    ground = hook.wrap(base_ground)                  # same signature as skill_runner grounders
    ...run...; hook.finish(program, ok=True, root=skills_root)   # write repairs into the skill (new version)

Order of tiers (repair_verify.md 4a):
  1. rule: similo.rank + similo.decide (score >= ACCEPT, margin >= MARGIN, an identity anchor), no model;
  2. model: only when the rule tier is AMBIGUOUS; the picker sees <= 5 candidates and may answer
     NOT_FOUND. A picker reply out of range / unparseable / a dead server is an abstain;
  3. safety gate, after either tier: a candidate whose name or context reads destructive (the
     product's own ``micro_agent._DESTRUCTIVE_RE``: delete / pay / purchase / transfer / sign out ...)
     or send-like (``SEND_RE``: send / submit / confirm / place order / checkout) is NEVER repaired
     onto without ``approve(request) -> True``. No approver = ``needs_approval`` (a halt).

The hook (``SkillRepair``) grounds a skill step as before; only when the strict grounder says
missing / ambiguous does it re-find. It records:
  * ``enroll``: the first successful strict grounding of a target that has no fingerprint stores the
    control's full fingerprint (old skills carry role + name only; see docs/specs/mw_repair.md);
  * ``repair``: the new name is added to ``target.aliases`` (the strict grounder then matches it
    with zero model calls next time), a role drift is kept in ``target.repairs`` and tried first
    (rung ``repair_cache``), the fingerprint is moved to the new control, and every repair keeps
    ``{from, to, tier, score, margin, reason, at}``.
Nothing is written unless the run finished OK (``finish(ok=True)``); a failed run drops its repairs.
Steps whose target is a period ``name_template`` (pack documents for one month) are never repaired:
picking "the other month's PDF" is exactly the wrong repair the template exists to prevent.
Absence-tolerant targets (``begin(program)`` collects them: an ``optional`` op's target, a handler's
guard control) may legitimately be missing, so a "missing" there is information, not breakage: they
get the rule tier only (no model call), and anything short of a confident repair returns the strict
grounder's own result -- the runner then skips the optional step / does not fire the handler, as it
would with no hook.
Labels never carry typed text: the ``value`` of an editable control (textbox / combobox / ...) is not
read when building a fingerprint's nearby label (it would store usernames, account numbers in the
skill JSON), and the label is passed through ``web_tools.mask_secrets``.
"""
from __future__ import annotations

import copy
import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from . import similo as S
from .model import Pick, Picker
from .similo import Fingerprint

SEND_RE = re.compile(r"\b(send|submit|confirm|place (the )?order|checkout|check out|sign|authori[sz]e|approve|"
                     r"publish|post|book now|order now)\b", re.I)


def _destructive_re():
    try:
        from ..micro_agent import _DESTRUCTIVE_RE
        return _DESTRUCTIVE_RE
    except Exception:  # pragma: no cover - product package unavailable: refuse broadly
        return re.compile(r"\b(delete|remove|pay|purchase|buy|transfer|cancel|reset|sign out|log ?out)\b", re.I)


def risk_of(c: Fingerprint) -> str:
    """'' when safe to repair onto; else why the control needs the owner's approval."""
    rx = _destructive_re()
    for text in (c.name, c.context):
        if text and rx.search(text):
            return f"destructive ({rx.search(text).group(0)!r})"
        if text and SEND_RE.search(text):
            return f"send-like ({SEND_RE.search(text).group(0)!r})"
    return ""


@dataclass
class RepairResult:
    status: str                                    # repaired | not_found | ambiguous | needs_approval
    candidate: Optional[Fingerprint] = None
    tier: str = ""                                 # rule | model
    score: float = 0.0
    margin: float = 0.0
    reason: str = ""
    model_calls: int = 0
    shortlist: List[Tuple[float, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "repaired"

    def to_dict(self) -> dict:
        return {"status": self.status, "tier": self.tier, "score": round(self.score, 3),
                "margin": round(self.margin, 3), "reason": self.reason, "model_calls": self.model_calls,
                "to": self.candidate.show() if self.candidate is not None else None,
                "shortlist": [(round(s, 3), n) for s, n in self.shortlist]}


class Repairer:
    def __init__(self, picker: Optional[Picker] = None, *, approve: Optional[Callable[[dict], bool]] = None,
                 accept: float = S.ACCEPT, margin: float = S.MARGIN, floor: float = S.FLOOR,
                 weights: Optional[Dict[str, float]] = None, risk: Callable[[Fingerprint], str] = risk_of):
        self.picker, self.approve = picker, approve
        self.accept, self.margin, self.floor, self.weights, self.risk = accept, margin, floor, weights, risk
        self.model_calls = 0

    def _gate(self, fp: Fingerprint, res: RepairResult) -> RepairResult:
        why = self.risk(res.candidate)
        if not why:
            return res
        req = {"action": "repair_onto_risky_control", "from": fp.show(), "to": res.candidate.show(),
               "risk": why, "tier": res.tier, "score": round(res.score, 3)}
        try:
            ok = bool(self.approve and self.approve(req))
        except Exception:  # noqa: BLE001 -- a broken approver is a no
            ok = False
        if ok:
            res.reason += f"; approved by the owner ({why})"
            return res
        return RepairResult("needs_approval", res.candidate, res.tier, res.score, res.margin,
                            f"would repair onto {res.candidate.show()}, which is {why}: needs the owner's approval",
                            res.model_calls, res.shortlist)

    def refind(self, fp: Fingerprint, cands: Sequence[Fingerprint], *, use_model: bool = True) -> RepairResult:
        ranked = S.rank(fp, cands, self.weights)
        d = S.decide(ranked, fp, accept=self.accept, margin=self.margin, floor=self.floor)
        short = [(s, c.show()) for s, c in (d.shortlist or ranked[:S.SHORTLIST])]
        if d.kind == "accept":
            return self._gate(fp, RepairResult("repaired", d.best, "rule", d.score, d.margin, d.reason,
                                               shortlist=short))
        if d.kind == "abstain":
            return RepairResult("not_found", None, "rule", d.score, d.margin, d.reason, shortlist=short)
        if self.picker is None or not use_model:
            return RepairResult("ambiguous", d.best, "rule", d.score, d.margin,
                                f"rule tier unsure ({d.reason}); no model tier", shortlist=short)
        items = [c for _s, c in d.shortlist]
        try:
            p: Pick = self.picker(fp, items)
        except Exception as e:  # noqa: BLE001 -- a broken picker abstains
            p = Pick(None, f"picker error {type(e).__name__}", calls=1)
        calls = max(int(p.calls or 0), 1)
        self.model_calls += calls
        if p.choice is None or not 0 <= p.choice < len(items):
            return RepairResult("not_found", None, "model", d.score, d.margin,
                                f"model: NOT_FOUND ({p.reason})", calls, short)
        s, c = d.shortlist[p.choice]
        if S.role_ok(fp.role, c.role) <= 0:                        # never reached (rank gates roles); kept as a check
            return RepairResult("not_found", None, "model", s, d.margin, "model picked an incompatible role",
                                calls, short)
        return self._gate(fp, RepairResult("repaired", c, "model", s, d.margin,
                                           f"model picked {p.choice + 1}/{len(items)}: {p.reason}", calls, short))


# ========================================================== page adapters ==

ROLE_NORM = {"AXLink": "link", "AXButton": "button", "AXTextField": "textbox", "AXSecureTextField": "password",
             "AXTextArea": "textbox", "AXPopUpButton": "combobox", "AXComboBox": "combobox",
             "AXCheckBox": "checkbox", "AXRadioGroup": "radiogroup"}
TEXT_KEYS = ("value", "title", "description")
# controls whose AX ``value`` is what the user / runner typed or picked (usernames, account numbers):
# never part of a stored label -- only their title / description (the field's own label) is read
EDITABLE_ROLES = frozenset({"AXTextField", "AXSecureTextField", "AXTextArea", "AXSearchField", "AXComboBox",
                            "AXPopUpButton", "AXSlider", "AXIncrementor", "AXDateField", "AXCheckBox",
                            "AXRadioButton"})


def _node_text(n: dict, skip: Optional[dict] = None, limit: int = 40) -> List[str]:
    out: List[str] = []

    def walk(x):
        if x is skip or len(out) >= limit:
            return
        if x.get("role") in ("AXStaticText", "AXHeading", "AXCell") or not x.get("children"):
            editable = x.get("role") in EDITABLE_ROLES or x.get("subrole") == "AXSecureTextField"
            for k in TEXT_KEYS:
                if k == "value" and editable:
                    continue
                v = x.get(k)
                if isinstance(v, str) and v.strip():
                    out.append(" ".join(v.split()))
                    break
        for c in x.get("children") or []:
            walk(c)
    walk(n)
    return out


def page_candidates(page: Any) -> List[Fingerprint]:
    """Fingerprints of every control of a teach Page (pack/runner.to_pack_page, live/driver.to_teach_page;
    with the AX snapshot when the page carries one, else role / name / order only)."""
    els = list(getattr(page, "elements", None) or [])
    snap = getattr(page, "_snap", None)
    ctx: Dict[Tuple[int, ...], str] = {}
    if snap is not None:
        try:
            from ..live.driver import read_web
            ctx = {tuple(wc.path): wc.context for wc in read_web(snap).controls}
        except Exception:  # noqa: BLE001
            ctx = {}
    counts: Dict[str, int] = {}
    for e in els:
        counts[e.get("role", "")] = counts.get(e.get("role", ""), 0) + 1
    seen: Dict[str, int] = {}
    out: List[Fingerprint] = []
    names = [str(e.get("name") or "") for e in els]
    for i, e in enumerate(els):
        role = e.get("role", "")
        path = tuple(e.get("ax_path") or ())
        anc: Tuple[str, ...] = ()
        label = ""
        if snap is not None and path:
            nodes = [snap.node(path[:k]) for k in range(1, len(path))]
            anc = tuple(str(n.get("role") or "") + (f"[{n.get('subrole')}]" if n.get("subrole") else "")
                        for n in nodes if n)[-8:]
            me = snap.node(path)
            for up in (1, 2):                          # parent, then grandparent (a table row, a block)
                if len(path) > up + 1:
                    par = snap.node(path[:-up])
                    if par is not None:
                        label = _mask(" ".join(t for t in _node_text(par, skip=me) if t != e.get("name")))[:200]
                        if label:
                            break
        idx = seen.get(role, 0)
        seen[role] = idx + 1
        sib = tuple(n for n in names[max(0, i - 2):i] + names[i + 1:i + 3])
        out.append(Fingerprint(role=role, name=str(e.get("name") or ""), label=label, context=ctx.get(path, ""),
                               ancestors=anc, index=idx, count=counts[role], siblings=sib,
                               href=str(e.get("href") or ""), ref=e))
    return out


def _mask(text: str) -> str:
    try:
        from ..web_tools import mask_secrets
        return mask_secrets(text)
    except Exception:  # pragma: no cover - product package unavailable
        return text


def target_role(target: dict) -> str:
    r = target.get("role") or ""
    return ROLE_NORM.get(r, r)


def target_fingerprint(target: dict) -> Fingerprint:
    fp = target.get("fingerprint")
    if isinstance(fp, dict) and fp.get("role"):
        return Fingerprint.from_dict(fp)
    return Fingerprint(role=target_role(target), name=str(target.get("name") or ""))


# ============================================================ skill hook ==

class SkillRepair:
    """Opt-in grounding hook for pack/runner.py (and any skill_runner grounder)."""

    def __init__(self, repairer: Optional[Repairer] = None, *, picker: Optional[Picker] = None,
                 approve: Optional[Callable[[dict], bool]] = None, enroll: bool = True,
                 today: Optional[Callable[[], str]] = None,
                 candidates: Callable[[Any], List[Fingerprint]] = page_candidates):
        self.repairer = repairer or Repairer(picker, approve=approve)
        self.enroll = enroll
        self.today = today or (lambda: dt.date.today().isoformat())
        self.candidates = candidates
        self.pending: Dict[int, dict] = {}         # id(target) -> pending record
        self.targets: Dict[int, dict] = {}
        self.events: List[dict] = []
        self.tolerant: Dict[int, dict] = {}        # id(target) -> target that may legitimately be absent

    def begin(self, program: Optional[dict] = None) -> None:
        self.pending.clear()
        self.targets.clear()
        self.events.clear()
        self.tolerant = absence_tolerant(program) if program else {}
        self.repairer.model_calls = 0

    # -- grounding ----------------------------------------------------------
    def wrap(self, base: Callable) -> Callable:
        from testbeds.teach_once.skill_runner import Grounding

        def ground(page, target: dict, *, exclude=(), fuzzy: bool = False):
            g = base(page, target, exclude=exclude, fuzzy=fuzzy)
            if page is None or target.get("name_template"):
                return g
            if g.status == "ok":
                if self.enroll and not target.get("fingerprint") and g.rung == "exact":
                    self._enroll(page, target, g.element)
                return g
            for r in reversed(target.get("repairs") or []):          # a role drift recorded earlier
                to = r.get("to") or {}
                if to.get("role") and to.get("role") != target_role(target):
                    g2 = base(page, {"role": to["role"], "name": to.get("name", "")}, exclude=exclude, fuzzy=False)
                    if g2.status == "ok":
                        return Grounding("ok", g2.element, "repair_cache", [g2.element.get("name", "")])
            fp = target_fingerprint(target)
            ex = {" ".join(str(x).split()).lower() for x in exclude or ()}
            cands = [c for c in self.candidates(page) if " ".join(c.name.split()).lower() not in ex]
            tolerant = self.tolerant.get(id(target)) is target
            res = self.repairer.refind(fp, cands, use_model=not tolerant)
            ev = dict(res.to_dict(), target=fp.show(), strict=g.status, **({"tolerant": True} if tolerant else {}))
            self.events.append(ev)
            if tolerant and res.status != "repaired":
                return g                        # optional step / handler guard: absent is a valid answer
            if res.status == "repaired":
                self.pending[id(target)] = {"kind": "repair", "from": {"role": fp.role, "name": fp.name},
                                            "to": {"role": res.candidate.role, "name": res.candidate.name,
                                                   "context": res.candidate.context},
                                            "fingerprint": res.candidate.to_dict(), "tier": res.tier,
                                            "score": round(res.score, 3), "margin": round(res.margin, 3),
                                            "reason": res.reason, "at": self.today()}
                self.targets[id(target)] = target
                return Grounding("ok", res.candidate.ref, f"repair_{res.tier}", [res.candidate.name])
            if res.status == "needs_approval":
                return Grounding("missing", None, "repair", [res.candidate.name], refusal="risk_escalation")
            if res.status == "ambiguous":
                return Grounding("ambiguous", None, "repair", [n for _s, n in res.shortlist])
            return Grounding(g.status, None, g.rung or "repair_not_found", g.candidates, g.refusal)
        return ground

    def _enroll(self, page, target: dict, el: dict) -> None:
        if id(target) in self.pending:
            return
        try:
            fp = next((c for c in self.candidates(page) if c.ref is el), None)
        except Exception:  # noqa: BLE001 -- enrolment is best effort
            fp = None
        if fp is None:
            return
        fp.twins = sum(1 for c in self.candidates(page) if c.role == fp.role and c.name == fp.name) or 1
        self.pending[id(target)] = {"kind": "enroll", "fingerprint": fp.to_dict(), "at": self.today()}
        self.targets[id(target)] = target

    @property
    def model_calls(self) -> int:
        return self.repairer.model_calls

    # -- recording ------------------------------------------------------------
    def apply(self, program: dict) -> Tuple[dict, List[dict]]:
        """A copy of ``program`` with the pending enrolments / repairs written into its targets."""
        new = copy.deepcopy(program)
        done: List[dict] = []
        for sect, ops in _sections(program):
            new_ops = _get_sect(new, sect)
            for i, op in enumerate(ops):
                tgt = op.get("target")
                rec = self.pending.get(id(tgt)) if isinstance(tgt, dict) else None
                if rec is None or self.targets.get(id(tgt)) is not tgt:
                    continue
                t = new_ops[i]["target"]
                t["fingerprint"] = rec["fingerprint"]
                if rec["kind"] == "repair":
                    to = rec["to"]
                    if to["role"] == target_role(t) and to["name"] != t.get("name"):
                        t["aliases"] = list(dict.fromkeys(list(t.get("aliases") or []) + [to["name"]]))
                    t.setdefault("repairs", []).append({k: v for k, v in rec.items() if k not in ("kind",
                                                                                                "fingerprint")})
                done.append({"op": op.get("id"), "kind": rec["kind"], **({k: rec[k] for k in ("from", "to", "tier",
                                                                                             "reason")
                                                                          if k in rec})})
        return new, done

    def finish(self, program: dict, *, ok: bool, root=None, save: Optional[Callable] = None) -> dict:
        """End of a run: on ok write the records into a new skill version; otherwise drop them."""
        out = {"events": list(self.events), "model_calls": self.model_calls, "written": [], "dropped": 0}
        if not self.pending:
            return out
        if not ok:
            out["dropped"] = len(self.pending)
            self.pending.clear()
            return out
        new, done = self.apply(program)
        if done:
            if save is None:
                from ..pack.teach import save_skill as save
            path = save(new, root)
            out.update(written=done, version=new.get("version"), path=str(path))
        self.pending.clear()
        return out


def absence_tolerant(program: dict) -> Dict[int, dict]:
    """Targets the runner treats as possibly absent: ``optional`` ops (skipped when missing) and handler
    guard controls (the handler just does not fire). Keyed by id() -- the runner grounds these dicts."""
    out: Dict[int, dict] = {}
    for _sect, ops in _sections(program):
        for op in ops:
            t = op.get("target")
            if op.get("optional") and isinstance(t, dict):
                out[id(t)] = t
    for h in program.get("handlers") or []:
        t = (h.get("guard") or {}).get("control")
        if isinstance(t, dict):
            out[id(t)] = t
    return out


def _sections(program: dict):
    yield "setup", list(program.get("setup") or [])
    yield "loop", list((program.get("loop") or {}).get("body") or [])
    yield "finally", list(program.get("finally") or [])


def _get_sect(program: dict, sect: str) -> List[dict]:
    if sect == "loop":
        return (program.get("loop") or {}).get("body") or []
    return program.get(sect) or []


__all__ = ["RepairResult", "absence_tolerant", "Repairer", "SEND_RE", "SkillRepair", "page_candidates", "risk_of", "target_fingerprint"]
