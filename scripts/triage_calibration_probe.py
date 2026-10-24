#!/usr/bin/env python3
"""triage_calibration_probe.py — does the local model's label confidence mean anything?

Runs the L3 triage labeller against a LIVE llama-server on the email-triage
testbed, records the confidence the cascade would see for every message, and
prints a reliability table plus suggested abstain thresholds for
scaffold/agent/desktop/triage_cascade.py. Inbox Zero's 0.3 / 0.1 / 0.75 came from
cloud models and do not transfer (BLUEPRINT critic 9), so measure first.

    # llama-server must already be running (e.g. Qwen3.5-9B on :8080)
    python scripts/triage_calibration_probe.py --seeds 7,8,9,10,11 --split calibration \
        --mode both --out /tmp/triage_probe.json

Modes:
  batch   exactly the cascade's L3 call (5 messages, JSON schema); confidence is
          read from the token probabilities at the first token of each
          "category" value.
  single  one message per call, the answer constrained by a GBNF grammar to one
          letter (A-G, H = none with --none), so each label is exactly one token
          and its probability is read directly.
  both    run both.

Probability source: --probs post (default) sends post_sampling_probs=true, i.e.
probabilities after the grammar and the sampler chain (temperature sharpens
them); --probs raw sends the model's softmax before sampling, renormalised over
the label tokens. Run both if unsure; thresholds only need to be monotone.

Uncertainty mode (--uncertainty raw|consistency|second_opinion; overrides --mode):
measures, per signal from scaffold/agent/desktop/triage_uncertainty.py, the AUROC
for "label wrong" (category != truth; also review bucket wrong) and a
coverage / accuracy table over thresholds. Every run also scores the free
deterministic-disagreement signal (d) on the same labels.
  raw             the L3 batch call with post_sampling_probs=false; temperature
                  fitted by NLL; score = normalised entropy (also 1 - max p)
  consistency     + (k-1) resamples per batch at --sample-temperature, each in
                  a different message order; score = share of samples whose
                  review bucket / category differs from the first label
  second_opinion  + one batched yes/no "must act within 48h?" call per batch;
                  score = p(contradicting the first label on urgency)

    python scripts/triage_calibration_probe.py --seeds 7,8,9,10,11 --split calibration \
        --uncertainty consistency --k 3 --out /tmp/unc_consistency.json

The probe reads the testbed's ground truth (it is a measurement tool, not an
agent arm). It talks only to --base-url, which must be loopback unless
--allow-remote. It never prints keys. No files are written except --out.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scaffold.agent.desktop import email_triage_agent as eta
from scaffold.agent.desktop import triage_cascade as tc
from scaffold.agent.desktop import triage_uncertainty as tu
from testbeds.email_triage import seed as seed_mod

LETTERS = "ABCDEFGH"
BINS = [0.0, 0.3, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 1.0001]


def _bucket_of(cat: str) -> str:
    return "bulk" if cat in tc.BULK else cat


# ── data ──────────────────────────────────────────────────────────────────────

def load_items(seeds: list[int], split: str, limit: int = 0) -> list[dict]:
    rows = []
    for s in seeds:
        d = seed_mod.generate(s)
        by_id = {m["id"]: m for m in d["messages"]}
        truth = {t["id"]: t for t in d["truth"]["messages"]}
        ids = (d["splits"]["calibration"] + d["splits"]["evaluation"] if split == "all"
               else d["splits"][split])
        for i in ids:
            rows.append({"seed": s, "msg": by_id[i], "truth": truth[i], "profile": d["account"],
                         "all_messages": d["messages"]})
    return rows[:limit] if limit else rows


# ── model calls ───────────────────────────────────────────────────────────────

def run_batch(items: list[dict], prefs: dict, allow_none: bool) -> list[dict]:
    out = []
    for i in range(0, len(items), eta.BATCH):
        chunk = items[i:i + eta.BATCH]
        ctx = eta._triage_context(chunk[0]["profile"], prefs)
        preds, dists = eta.model_batch([r["msg"] for r in chunk], ctx, allow_none)
        for r in chunk:
            mid = r["msg"]["id"]
            out.append(_record(r, "batch", (preds.get(mid) or {}).get("category"), dists.get(mid)))
        print(f"[probe] batch {min(i + eta.BATCH, len(items))}/{len(items)}", file=sys.stderr, flush=True)
    return out


def single_prompt(r: dict, prefs: dict, allow_none: bool) -> list:
    labels = tc.CATEGORIES + ([tc.NONE_LABEL] if allow_none else [])
    legend = "\n".join(f"{LETTERS[k]} = {lab}" for k, lab in enumerate(labels))
    user = (eta._triage_context(r["profile"], prefs) + "\n\nMessage:\n\n" + eta._render(r["msg"])
            + "\nWhich category is this message? Answer with ONE letter:\n" + legend)
    return [{"role": "system", "content": eta.SYSTEM}, {"role": "user", "content": user}]


def run_single(items: list[dict], prefs: dict, allow_none: bool, probs: str, temperature: float,
               n_probs: int, post: Callable[[str, dict], dict]) -> list[dict]:
    labels = tc.CATEGORIES + ([tc.NONE_LABEL] if allow_none else [])
    letters = [c.lower() for c in LETTERS[:len(labels)]]
    base = os.environ.get("AWOS_LOCAL_BASE_URL", "http://127.0.0.1:8080/v1").rstrip("/")
    out = []
    for k, r in enumerate(items):
        body = {"model": os.environ.get("AWOS_LOCAL_MODEL", "qwen3.5-9b"),
                "messages": single_prompt(r, prefs, allow_none), "temperature": temperature,
                "max_tokens": 2, "grammar": "root ::= [" + LETTERS[:len(labels)] + "]",
                "chat_template_kwargs": {"enable_thinking": False},
                "logprobs": True, "top_logprobs": n_probs, "n_probs": n_probs,
                "post_sampling_probs": probs == "post"}
        data = post(base + "/chat/completions", body)
        eta._log_usage(data)
        toks = tc.normalize_token_probs(data) or []
        dist = None
        if toks:
            d = tc.dist_at(toks, 0, "", letters)
            if d:
                dist = {labels[letters.index(L)]: v for L, v in d.items()}
        text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "").strip()
        label = labels[LETTERS.index(text[0].upper())] if text and text[0].upper() in LETTERS[:len(labels)] else None
        out.append(_record(r, "single", label, dist))
        if (k + 1) % 10 == 0:
            print(f"[probe] single {k + 1}/{len(items)}", file=sys.stderr, flush=True)
    return out


def _record(r: dict, mode: str, label: str | None, dist: dict[str, float] | None) -> dict:
    t = r["truth"]
    rec = {"seed": r["seed"], "id": r["msg"]["id"], "mode": mode, "truth": t["category"],
           "truth_importance": t["importance"], "truth_bucket": _bucket_of(t["category"]),
           "label": label, "has_probs": bool(dist)}
    if dist:
        top = max(dist, key=dist.get)
        b = sorted(tc.buckets(dist).items(), key=lambda kv: -kv[1])
        rec.update({"pred": top, "conf": dist[top], "bucket": b[0][0], "bucket_conf": b[0][1],
                    "margin": b[0][1] - (b[1][1] if len(b) > 1 else 0.0),
                    "p_bulk": tc.buckets(dist)["bulk"], "dist": dist})
    return rec


# ── analysis ──────────────────────────────────────────────────────────────────

def reliability(recs: list[dict], conf_key: str, pred_key: str, truth_key: str) -> dict:
    rows, ece, n_all = [], 0.0, len(recs)
    for lo, hi in zip(BINS[:-1], BINS[1:]):
        b = [r for r in recs if lo <= r[conf_key] < hi]
        if not b:
            rows.append({"bin": f"{lo:.2f}-{min(hi, 1.0):.2f}", "n": 0})
            continue
        acc = sum(r[pred_key] == r[truth_key] for r in b) / len(b)
        mc = sum(r[conf_key] for r in b) / len(b)
        ece += len(b) / max(n_all, 1) * abs(acc - mc)
        rows.append({"bin": f"{lo:.2f}-{min(hi, 1.0):.2f}", "n": len(b), "mean_conf": round(mc, 3),
                     "accuracy": round(acc, 3)})
    acc_all = sum(r[pred_key] == r[truth_key] for r in recs) / max(n_all, 1)
    return {"rows": rows, "ece": round(ece, 4), "accuracy": round(acc_all, 3), "n": n_all}


def _grid(lo: float, hi: float, step: float) -> list[float]:
    n = int(round((hi - lo) / step))
    return [round(lo + k * step, 4) for k in range(n + 1)]


def suggest(recs: list[dict], target: float) -> dict:
    """Thresholds for the cascade from the measured records."""
    n = len(recs)
    out = {}
    # min_conf: smallest t whose accepted set reaches the target bucket accuracy
    for t in _grid(0.3, 0.99, 0.01):
        acc_set = [r for r in recs if r["bucket_conf"] >= t]
        if acc_set and sum(r["bucket"] == r["truth_bucket"] for r in acc_set) / len(acc_set) >= target:
            out["min_conf"] = {"value": t, "coverage": round(len(acc_set) / n, 3)}
            break
    else:
        out["min_conf"] = {"value": None, "coverage": 0.0, "note": "target accuracy never reached"}
    for m in _grid(0.0, 0.95, 0.01):
        acc_set = [r for r in recs if r["margin"] >= m]
        if acc_set and sum(r["bucket"] == r["truth_bucket"] for r in acc_set) / len(acc_set) >= target:
            out["margin"] = {"value": m, "coverage": round(len(acc_set) / n, 3)}
            break
    else:
        out["margin"] = {"value": None, "coverage": 0.0, "note": "target accuracy never reached"}
    # archive_conf: smallest p_bulk threshold with ZERO demotions of non-bulk mail
    n_bulk = sum(r["truth_bucket"] == "bulk" for r in recs) or 1
    chosen = None
    for t in _grid(0.5, 1.0, 0.01):
        dem = [r for r in recs if r["p_bulk"] >= t and r["bucket"] == "bulk"]
        bad = [r for r in dem if r["truth_bucket"] != "bulk"]
        if not bad:
            chosen = {"value": t, "bulk_recall": round(sum(r["truth_bucket"] == "bulk" for r in dem) / n_bulk, 3),
                      "false_demotions": 0}
            break
    out["archive_conf"] = chosen or {"value": None, "note": "every threshold demotes some non-bulk mail"}
    return out


def simulate(recs: list[dict], th: tc.Thresholds) -> dict:
    ab = dem = bad = 0
    for r in recs:
        d = tc.abstain_decision(r["dist"], th)
        ab += d["abstain"]
        if d["demote_ok"]:
            dem += 1
            bad += r["truth_bucket"] != "bulk"
    return {"abstain_rate": round(ab / max(len(recs), 1), 3), "demoted": dem, "false_demotions": bad}


def report(recs: list[dict], target: float, emit=print) -> dict:
    res = {}
    for mode in sorted({r["mode"] for r in recs}):
        rs = [r for r in recs if r["mode"] == mode]
        with_p = [r for r in rs if r["has_probs"]]
        emit(f"\n=== mode: {mode}  ({len(rs)} items, {len(with_p)} with token probabilities)")
        if not with_p:
            emit("  NO token probabilities came back. Check that this llama-server build supports "
                 "n_probs / logprobs on /v1/chat/completions; the cascade then runs without abstain.")
            res[mode] = {"n": len(rs), "with_probs": 0}
            continue
        lab = reliability(with_p, "conf", "pred", "truth")
        bkt = reliability(with_p, "bucket_conf", "bucket", "truth_bucket")
        for title, rel in (("label (7 classes)", lab), ("bucket (bulk summed)", bkt)):
            emit(f"  reliability, {title}: accuracy {rel['accuracy']}, ECE {rel['ece']}")
            emit(f"    {'bin':<11} {'n':>4} {'mean_conf':>10} {'accuracy':>9}")
            for row in rel["rows"]:
                if row["n"]:
                    emit(f"    {row['bin']:<11} {row['n']:>4} {row['mean_conf']:>10.3f} {row['accuracy']:>9.3f}")
                else:
                    emit(f"    {row['bin']:<11} {0:>4}")
        sug = suggest(with_p, target)
        default = simulate(with_p, tc.Thresholds())
        emit(f"  suggested (target bucket accuracy {target}): {json.dumps(sug)}")
        th = tc.Thresholds(min_conf=sug["min_conf"]["value"] or 1.0, margin=sug["margin"]["value"] or 1.0,
                           archive_conf=sug["archive_conf"]["value"] or 1.01)
        tuned = simulate(with_p, th)
        emit(f"  Inbox Zero defaults 0.3/0.1/0.75 -> {json.dumps(default)}")
        emit(f"  suggested thresholds          -> {json.dumps(tuned)}")
        emit(f"  env: AWOS_TRIAGE_MIN_CONF={th.min_conf} AWOS_TRIAGE_MARGIN={th.margin} "
             f"AWOS_TRIAGE_ARCHIVE_CONF={th.archive_conf}")
        res[mode] = {"n": len(rs), "with_probs": len(with_p), "label": lab, "bucket": bkt,
                     "suggested": sug, "sim_default": default, "sim_suggested": tuned}
    return res


# ── uncertainty signals (triage_uncertainty.py) ───────────────────────────────

def run_uncertainty(items: list[dict], prefs: dict, kind: str, k: int = 3,
                    sample_temperature: float = 0.7,
                    post: Callable | None = None) -> tuple[list[dict], dict]:
    """First-pass L3 labels exactly as the cascade would get them (model_view'd,
    batched, same context), plus the extra calls ``kind`` needs. Returns
    (records, call counts)."""
    xml = eta.use_adapter()
    recs, calls = [], {"base": 0, "extra": 0}
    for i in range(0, len(items), eta.BATCH):
        chunk = items[i:i + eta.BATCH]
        profile = chunk[0]["profile"]
        context = eta._triage_context_xml(profile, prefs) if xml else eta._triage_context(profile, prefs)
        view = [tc.model_view(r["msg"]) for r in chunk]
        preds, dists = eta.model_batch(view, context, False, prefs=prefs, xml=xml)
        calls["base"] += 1
        samples: dict[str, list] = {r["msg"]["id"]: [] for r in chunk}
        p_yes: dict = {}
        if kind == "consistency":
            rs = tu.make_resampler(context, prefs=prefs, xml=xml, temperature=sample_temperature, post=post)
            for j in range(1, k):
                got = rs(tu.permuted(view, j), j)
                calls["extra"] += 1
                for mid, got_list in samples.items():
                    if got.get(mid):
                        got_list.append(got[mid])
        elif kind == "second_opinion":
            p_yes = tu.make_second_opinion(context, prefs=prefs, xml=xml, post=post)(view)
            calls["extra"] += 1
        ctx = tc.build_context(profile, prefs, chunk[0]["all_messages"])
        for r in chunk:
            mid, t = r["msg"]["id"], r["truth"]
            pred = preds.get(mid)
            tb = tu.truth_bucket(t["category"], int(t["importance"]))
            det = tu.deterministic_conflicts(r["msg"], pred, tc.guards(r["msg"], ctx), prefs)
            sev = max([tu.REASONS[x][1] for x in det if tu.REASONS[x][0] != "keep"] or [0])
            rec = {"seed": r["seed"], "id": mid, "truth": t["category"],
                   "truth_importance": t["importance"], "truth_bucket": tb,
                   "label": (pred or {}).get("category"), "importance": (pred or {}).get("importance"),
                   "bucket": tu.review_bucket(pred), "wrong": (pred or {}).get("category") != t["category"],
                   "wrong_bucket": tu.review_bucket(pred) != tb, "dist": dists.get(mid),
                   "deterministic": det, "score_deterministic": sev / 3}
            if kind == "consistency":
                c = tu.consistency_score(pred, samples[mid])
                c2 = tu.consistency_score(pred, samples[mid][:1])  # what k=2 would have seen
                rec.update({"samples": samples[mid], "score_bucket_disagree": c["bucket_disagree"],
                            "score_category_disagree": c["category_disagree"],
                            "score_bucket_disagree_k2": c2["bucket_disagree"]})
            elif kind == "second_opinion":
                rec.update({"p_yes": p_yes.get(mid),
                            "score_second_opinion": tu.second_opinion_score(pred, p_yes.get(mid))})
            recs.append(rec)
        print(f"[probe] uncertainty/{kind} {min(i + eta.BATCH, len(items))}/{len(items)}",
              file=sys.stderr, flush=True)
    if kind == "raw":
        with_d = [r for r in recs if r["dist"]]
        T = tu.fit_temperature([r["dist"] for r in with_d], [r["truth"] for r in with_d])
        calls["temperature"] = T
        for r in recs:
            r["score_entropy_T1"] = tu.entropy_score(r["dist"], 1.0)
            r["score_entropy"] = tu.entropy_score(r["dist"], T)
            r["score_one_minus_max"] = (1 - max(r["dist"].values())) if r["dist"] else None
    return recs, calls


UNC_KEYS = {"raw": ["score_entropy", "score_entropy_T1", "score_one_minus_max"],
            "consistency": ["score_bucket_disagree", "score_category_disagree",
                            "score_bucket_disagree_k2"],
            "second_opinion": ["score_second_opinion"]}


def uncertainty_report(recs: list[dict], kind: str, calls: dict, target: float, emit=print) -> dict:
    keys = UNC_KEYS[kind] + ["score_deterministic"]
    wrong = [r["wrong"] for r in recs]
    wrong_b = [r["wrong_bucket"] for r in recs]
    n = len(recs)
    base, extra = max(calls.get("base", 0), 1), calls.get("extra", 0)
    emit(f"\n=== uncertainty: {kind}  ({n} items, label accuracy {round(1 - sum(wrong) / max(n, 1), 3)}, "
         f"bucket accuracy {round(1 - sum(wrong_b) / max(n, 1), 3)})")
    share = round(min(1.0, 0.3 * base / extra), 2) if extra else 1.0
    emit(f"  model calls: base {calls.get('base', 0)}, extra {extra} (+{round(100 * extra / base)}% if "
         f"every item is checked; at the cascade's +30% budget the paid check covers ~{share} of L3 items)")
    if "temperature" in calls:
        emit(f"  fitted temperature (NLL, in-sample): {calls['temperature']}")
    res = {"kind": kind, "n": n, "calls": calls, "signals": {}}
    for key in keys:
        sc = [r.get(key) for r in recs]
        have = sum(s is not None for s in sc)
        a_lab, a_bkt = tu.auroc(sc, wrong), tu.auroc(sc, wrong_b)
        table = tu.coverage_table(sc, wrong)
        ok = [row for row in table if row["accuracy_kept"] is not None and row["accuracy_kept"] >= target]
        sug = max(ok, key=lambda row: (row["coverage"], -row["threshold"])) if ok else None
        fmt = (lambda v: None if v is None else round(v, 3))
        emit(f"  {key}: scored {have}/{n}, AUROC label-wrong {fmt(a_lab)}, bucket-wrong {fmt(a_bkt)}")
        emit(f"    {'thresh':>7} {'coverage':>9} {'acc_kept':>9} {'review':>7} {'caught':>7} {'recall':>7}")
        for row in table:
            emit(f"    {row['threshold']:>7} {row['coverage']:>9} {row['accuracy_kept']!s:>9} "
                 f"{row['review']:>7} {row['errors_caught']:>7} {row['error_recall']!s:>7}")
        if sug:
            emit(f"    -> keep score <= {sug['threshold']}: accuracy {sug['accuracy_kept']} >= {target} "
                 f"at coverage {sug['coverage']}")
        res["signals"][key] = {"scored": have, "auroc_label": a_lab, "auroc_bucket": a_bkt,
                               "table": table, "suggested": sug}
    ent = res["signals"].get("score_entropy") or {}
    if kind == "raw" and ent.get("suggested"):
        emit(f"  env: AWOS_TRIAGE_POST_SAMPLING=0 AWOS_TRIAGE_UNC_T={calls['temperature']} "
             f"AWOS_TRIAGE_UNC_ENTROPY={ent['suggested']['threshold']}")
    return res


def _check_url(url: str, allow_remote: bool) -> None:
    host = (urlparse(url).hostname or "").lower()
    if not allow_remote and host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit(f"refusing non-loopback base URL {host!r} (use --allow-remote)")


def main(argv=None, post: Callable[[str, dict], dict] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base-url", default=os.environ.get("AWOS_LOCAL_BASE_URL", "http://127.0.0.1:8080/v1"))
    ap.add_argument("--seeds", default="7", help="comma-separated testbed seeds (30 calibration items each)")
    ap.add_argument("--split", choices=["calibration", "evaluation", "all"], default="calibration")
    ap.add_argument("--mode", choices=["batch", "single", "both"], default="both")
    ap.add_argument("--probs", choices=["post", "raw"], default="post")
    ap.add_argument("--temperature", type=float, default=0.2, help="single mode (batch uses the agent's 0.2)")
    ap.add_argument("--n-probs", type=int, default=10)
    ap.add_argument("--none", action="store_true", help="offer a NONE label")
    ap.add_argument("--prefs", default="", help="optional prefs.json to put in the prompt")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--target", type=float, default=0.9, help="bucket accuracy the thresholds must reach")
    ap.add_argument("--out", default="", help="write records + report JSON here")
    ap.add_argument("--allow-remote", action="store_true")
    ap.add_argument("--uncertainty", choices=["raw", "consistency", "second_opinion"], default=None,
                    help="measure an uncertainty signal instead of --mode (see the docstring)")
    ap.add_argument("--k", type=int, default=3, help="consistency: samples incl. the first label")
    ap.add_argument("--sample-temperature", type=float, default=0.7,
                    help="consistency: resample temperature")
    a = ap.parse_args(argv)
    _check_url(a.base_url, a.allow_remote)
    os.environ["AWOS_PROVIDER"] = "local"
    os.environ["AWOS_LOCAL_BASE_URL"] = a.base_url
    os.environ["AWOS_TRIAGE_LOGPROBS"] = "1"
    raw = a.uncertainty == "raw" or a.probs == "raw"
    os.environ["AWOS_TRIAGE_POST_SAMPLING"] = "0" if raw else "1"
    os.environ["AWOS_TRIAGE_NPROBS"] = str(a.n_probs)
    os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
    prefs = json.loads(Path(a.prefs).read_text()) if a.prefs else {"version": 1, "notes": [], "rules": []}
    items = load_items([int(s) for s in a.seeds.split(",") if s.strip()], a.split, a.limit)
    if a.uncertainty:
        saved = eta._http_post
        if post is not None:
            eta._http_post = post  # model_batch posts through eta._http_post
        try:
            recs, calls = run_uncertainty(items, prefs, a.uncertainty, a.k, a.sample_temperature, post)
        finally:
            eta._http_post = saved
        print(f"[probe] {len(items)} item(s), seeds {a.seeds}, split {a.split}, uncertainty "
              f"{a.uncertainty}; model calls {eta.USAGE['calls']}, parse failures {eta.USAGE['parse_failures']}")
        res = uncertainty_report(recs, a.uncertainty, calls, a.target)
        if a.out:
            Path(a.out).write_text(json.dumps({"args": vars(a), "report": res, "records": recs}, indent=1))
            print(f"[probe] wrote {a.out}")
        return 0
    post = post or eta._http_post
    recs: list[dict] = []
    if a.mode in ("batch", "both"):
        recs += run_batch(items, prefs, a.none)
    if a.mode in ("single", "both"):
        recs += run_single(items, prefs, a.none, a.probs, a.temperature, a.n_probs, post)
    print(f"[probe] {len(items)} item(s), seeds {a.seeds}, split {a.split}, probs {a.probs}; "
          f"model calls {eta.USAGE['calls']}, parse failures {eta.USAGE['parse_failures']}")
    res = report(recs, a.target)
    if a.out:
        Path(a.out).write_text(json.dumps({"args": vars(a), "report": res, "records": recs}, indent=1))
        print(f"[probe] wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
