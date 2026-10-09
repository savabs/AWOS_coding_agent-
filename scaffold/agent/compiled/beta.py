"""
beta.py — promotion and demotion math for compiled records (spec §7).

Posterior Beta(1+s, 1+f) under a uniform prior. The 95% one-sided lower bound is
LB = BetaInv(0.05; 1+s, 1+f). With integer parameters the Beta CDF is exact as a
binomial tail, I_x(a, b) = P(Binomial(a+b-1, x) >= a), so no scipy is needed;
the inverse is found by bisection. For f = 0 the closed form is 0.05^(1/(s+1)).

State machine (apply_event):

  candidate --admit--> admitted (R1 only)
  admitted/demoted --ok--> ... promoted when LB>=0.9 and s_live>=3 and s_indep>=5
  promoted --fail--> demoted (one failure; R0 off; R1 again after 10 consecutive passes)
  admitted/demoted --second fail within 30 days--> retired
  any --precondition mismatch / lockfile change--> suspended (no f increment)
  promoted --idle 60 days--> admitted
"""

from __future__ import annotations

import math
import time
from typing import Optional

CONF = 0.95
PROMOTE_LB = 0.9
PROMOTE_S_LIVE = 3
PROMOTE_S_INDEP = 5
DEMOTED_REUSE_PASSES = 10
RETIRE_WINDOW_S = 30 * 86400
IDLE_DEMOTE_S = 60 * 86400


def beta_cdf(x: float, a: int, b: int) -> float:
    """Regularised incomplete beta I_x(a, b) for integer a, b >= 1 (exact)."""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    n = a + b - 1
    lx, l1x = math.log(x), math.log1p(-x)
    tot = 0.0
    for j in range(a, n + 1):
        tot += math.exp(math.lgamma(n + 1) - math.lgamma(j + 1) - math.lgamma(n - j + 1)
                        + j * lx + (n - j) * l1x)
    return min(1.0, tot)


def lower_bound(s: int, f: int, conf: float = CONF) -> float:
    """One-sided `conf` lower bound of Beta(1+s, 1+f): the (1-conf) quantile."""
    a, b, q = 1 + int(s), 1 + int(f), 1.0 - conf
    if f == 0:
        return q ** (1.0 / a)
    lo, hi = 0.0, 1.0
    for _ in range(100):
        mid = (lo + hi) / 2
        if beta_cdf(mid, a, b) < q:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def successes_needed(f: int, target: float = PROMOTE_LB, conf: float = CONF) -> int:
    """Smallest s with lower_bound(s, f) >= target."""
    s = 0
    while lower_bound(s, f, conf) < target:
        s += 1
    return s


def promotion_check(ev: dict) -> dict:
    """Why a record can or cannot be promoted to R0. `ev` is the evidence dict."""
    s, f = int(ev.get("s", 0)), int(ev.get("f", 0))
    lb = lower_bound(s, f)
    reasons = []
    if lb < PROMOTE_LB:
        reasons.append(f"LB {lb:.3f} < {PROMOTE_LB} (need s >= {successes_needed(f)} at f={f})")
    if int(ev.get("s_live", 0)) < PROMOTE_S_LIVE:
        reasons.append(f"s_live {ev.get('s_live', 0)} < {PROMOTE_S_LIVE}")
    if int(ev.get("s_indep", 0)) < PROMOTE_S_INDEP:
        reasons.append(f"s_indep {ev.get('s_indep', 0)} < {PROMOTE_S_INDEP}")
    return {"lb95": lb, "promotable": not reasons, "blocked_by": reasons}


def apply_event(rec: dict, event: str, *, live: bool = False, indep: bool = False,
                ts: Optional[float] = None, detail: str = "") -> dict:
    """
    Apply one event to a record dict (mutates and returns it). Events:
      "admit"         A1-A6 passed (s/f already set by the harness)
      "reject"        admission failed
      "ok"            a probe-verified success (live / independent synthetic)
      "fail"          a probe failure
      "mismatch"      precondition fingerprint / lockfile / toolchain drift
      "idle"          the record was unused; demotes promoted -> admitted after 60 days
    """
    ts = time.time() if ts is None else ts
    ev = rec.setdefault("evidence", {})
    for k in ("s", "f", "s_live", "f_live", "s_indep", "consecutive_ok"):
        ev.setdefault(k, 0)
    ev.setdefault("fail_ts", [])
    state = rec.get("state", "candidate")
    new = state

    if event == "admit":
        new = "admitted" if state == "candidate" else state
    elif event == "reject":
        new = "rejected" if state == "candidate" else state
    elif event == "mismatch":
        new = "suspended"
    elif event == "idle":
        last = ev.get("last_used_ts") or 0
        if state == "promoted" and ts - last >= IDLE_DEMOTE_S:
            new = "admitted"
    elif event == "ok":
        ev["s"] += 1
        ev["s_live"] += int(live)
        ev["s_indep"] += int(indep)
        ev["consecutive_ok"] += 1
        ev["last_used_ts"] = ts
        if state == "demoted" and ev["consecutive_ok"] >= DEMOTED_REUSE_PASSES:
            new = "admitted"
        if new == "admitted" and promotion_check(ev)["promotable"]:
            new = "promoted"
    elif event == "fail":
        ev["f"] += 1
        ev["f_live"] += int(live)
        ev["consecutive_ok"] = 0
        recent = [t for t in ev["fail_ts"] if ts - t <= RETIRE_WINDOW_S]
        ev["fail_ts"] = recent + [ts]
        ev["last_fail"] = {"ts": ts, "detail": detail}
        ev["last_used_ts"] = ts
        if state == "promoted":
            new = "demoted"
        elif state in ("admitted", "demoted") and recent:
            new = "retired"
        elif state in ("admitted", "demoted"):
            new = "demoted"
    else:
        raise ValueError(f"unknown event {event!r}")

    ev["lb95"] = round(lower_bound(ev["s"], ev["f"]), 4)
    rec["state"] = new
    rec.setdefault("history", []).append(
        {"ts": ts, "event": event, "from": state, "to": new, "detail": detail})
    return rec


def fold_executions(entries: list[dict], record_id: str) -> dict:
    """Recompute evidence counts from the executions log (spec §4: counts are a fold)."""
    ev = {"s": 0, "f": 0, "s_live": 0, "f_live": 0, "s_indep": 0}
    for e in entries:
        if e.get("record_id") != record_id:
            continue
        ok = (e.get("probe_result") or {}).get("ok")
        if e.get("outcome") == "precondition_refused" or ok is None:
            continue
        live = e.get("source") == "live"
        if ok:
            ev["s"] += 1
            ev["s_live"] += int(live)
            ev["s_indep"] += int(e.get("source") == "indep")
        else:
            ev["f"] += 1
            ev["f_live"] += int(live)
    ev["lb95"] = round(lower_bound(ev["s"], ev["f"]), 4)
    return ev


def table(fs=(0, 1, 2, 3), ss=(3, 5, 10, 12, 20, 28)) -> str:
    """The §7 table as text."""
    lines = ["failures f | successes needed for LB >= 0.9"]
    for f in fs:
        s = successes_needed(f)
        lines.append(f"{f:>10} | {s} (LB {lower_bound(s, f):.3f})")
    lines.append("f=0 reference: " + ", ".join(f"s={s} -> {lower_bound(s, 0):.2f}" for s in ss))
    return "\n".join(lines)
