"""fuzzy_apply_eval.py — offline ($0) apply-rate measurement for T4.

Builds a synthetic corpus of SEARCH blocks with known ground truth: a unique
3-8 line chunk of a real source file, perturbed with the kinds of noise small
models produce, and a REPLACE that is the noisy chunk plus a sentinel line.
Each case runs through
  exact   — exact unique match only (what a strict applier does)
  legacy  — Verifier._apply_fuzzy with AWOS_FUZZY_APPLY off (pre-T4 default)
  ladder  — fuzzy_apply.apply (AWOS_FUZZY_APPLY=1)
and is scored by where the sentinel lands:
  applied    the matcher changed the file
  right+compiles  right, and the result still compiles (Verifier's syntax gate)
  right      sentinel directly after the true chunk (±1 line for a dropped blank)
  wrong      applied, but the sentinel is somewhere else (a misplaced edit)
  indent_ok  right, and the sentinel has the file's indentation

  python scripts/fuzzy_apply_eval.py [--n 2000] [--seed 7] [root ...]
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scaffold" / "agent"))
import fuzzy_apply  # noqa: E402
from verifier import Verifier  # noqa: E402

SENT = "SENTINEL_T4_MARK = 1"


def _indent(l):
    return l[:len(l) - len(l.lstrip(" \t"))]


def noise_trailing(lines, rng):
    return [l + " " * rng.randint(1, 3) if l.strip() and rng.random() < 0.5 else l for l in lines]


def noise_inline_ws(lines, rng):
    out = []
    for l in lines:
        ind, body = _indent(l), l.lstrip(" \t")
        if " " in body and rng.random() < 0.6:
            body = re.sub(r" ", "  ", body, count=1) if rng.random() < 0.5 else re.sub(r" = ", "=", body, count=1)
        out.append(ind + body)
    return out


def noise_dedent(lines, rng):
    base = os.path.commonprefix([_indent(l) for l in lines if l.strip()])
    return [l[len(base):] if l.strip() else l for l in lines]


def noise_shift(lines, rng):
    pad = " " * rng.choice((2, 4, 8))
    return [pad + l if l.strip() else l for l in lines]


def noise_indent_unit(lines, rng):
    return [(" " * (len(_indent(l)) // 2) + l.lstrip(" ")) if l.strip() else l for l in lines]


def noise_token(lines, rng):
    cand = [i for i, l in enumerate(lines) if len(l.strip()) > 10]
    if not cand:
        return lines
    i = rng.choice(cand)
    l = lines[i]
    j = rng.randrange(len(_indent(l)), len(l))
    op = rng.random()
    if op < 0.4:
        l = l[:j] + l[j + 1:]                      # drop a char
    elif op < 0.7:
        l = l[:j] + rng.choice("abcxyz_") + l[j:]  # insert
    else:
        l = l[:j] + rng.choice("abcxyz_") + l[j + 1:]
    out = list(lines)
    out[i] = l
    return out


def noise_drop_blank(lines, rng):
    blanks = [i for i, l in enumerate(lines) if not l.strip() and 0 < i < len(lines) - 1]
    if not blanks:
        return lines
    i = rng.choice(blanks)
    return lines[:i] + lines[i + 1:]


INDENT_NOISES = (noise_dedent, noise_shift, noise_indent_unit)

NOISES = {
    "trailing": [noise_trailing],
    "inline_ws": [noise_inline_ws],
    "dedent": [noise_dedent],
    "shift": [noise_shift],
    "indent_unit": [noise_indent_unit],
    "token": [noise_token],
    "drop_blank": [noise_drop_blank],
    "dedent+token": [noise_dedent, noise_token],
    "ws+token": [noise_inline_ws, noise_trailing, noise_token],
}


def files(roots):
    for r in roots:
        for p in sorted(Path(r).rglob("*.py")):
            if any(part in (".git", ".venv", "node_modules", "__pycache__", ".awos")
                   for part in p.parts):
                continue
            try:
                t = p.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if 40 <= t.count("\n") <= 4000:
                yield p, t


def make_cases(roots, n, rng):
    pool = list(files(roots))
    cases = []
    tries = 0
    while len(cases) < n and tries < n * 50:
        tries += 1
        p, text = rng.choice(pool)
        lines = text.split("\n")
        k = rng.randint(3, 8)
        s = rng.randrange(0, max(1, len(lines) - k))
        chunk = lines[s:s + k]
        if not chunk[0].strip() or not chunk[-1].strip():
            continue
        if sum(1 for l in chunk if l.strip()) < 2 or text.count("\n".join(chunk)) != 1:
            continue
        if not _compiles(text):
            continue
        name = rng.choice(list(NOISES))
        noisy, framed = chunk, chunk
        for fn in NOISES[name]:
            state = rng.getstate()
            noisy = fn(noisy, rng)
            if fn in INDENT_NOISES:
                # REPLACE = the clean chunk in the model's (re-indented) frame,
                # so a correct apply leaves valid code.
                rng2 = random.Random()
                rng2.setstate(state)
                framed = fn(framed, rng2)
        if noisy == chunk:
            continue
        last_ind = _indent([l for l in framed if l.strip()][-1])
        replace = framed + [last_ind + SENT]
        cases.append(dict(file=str(p), text=text, start=s, k=k, noise=name,
                          search="\n".join(noisy), replace="\n".join(replace),
                          want_indent=_indent([l for l in chunk if l.strip()][-1])))
    return cases


def score(case, matched, out):
    if not matched or out == case["text"]:
        return "miss", False
    lines = out.split("\n")
    idx = [i for i, l in enumerate(lines) if SENT in l]
    if len(idx) != 1:
        return "wrong", False
    i = idx[0]
    want = case["start"] + case["k"]
    if abs(i - want) <= 1:
        return "right", _indent(lines[i]) == case["want_indent"]
    return "wrong", False


def _compiles(text):
    try:
        compile(text, "<eval>", "exec")
        return True
    except (SyntaxError, ValueError):
        return False


def run(cases):
    v = Verifier()
    res = defaultdict(Counter)
    by_noise = defaultdict(lambda: defaultdict(Counter))
    tiers = Counter()
    for c in cases:
        exact_ok = c["text"].count(c["search"]) == 1
        outs = {
            "exact": (exact_ok, c["text"].replace(c["search"], c["replace"], 1) if exact_ok else c["text"]),
        }
        os.environ.pop("AWOS_FUZZY_APPLY", None)
        ok, out, _ = v._apply_fuzzy(c["text"], c["search"], c["replace"])
        outs["legacy"] = (ok, out)
        m = fuzzy_apply.apply(c["text"], c["search"], c["replace"])
        outs["ladder"] = (m.matched, m.content)
        tiers[m.tier_name if m.matched else ("refused" if m.reason.startswith("ambiguous") else "none")] += 1
        # The ideal edit (sentinel after the true chunk, file indentation) must
        # itself compile for the compile metric to say anything about a matcher.
        o = c["text"].split("\n")
        at = c["start"] + c["k"]
        oracle_ok = _compiles("\n".join(o[:at] + [c["want_indent"] + SENT] + o[at:]))
        for name, (ok, out) in outs.items():
            verdict, ind = score(c, ok, out)
            res[name]["n"] += 1
            res[name][verdict] += 1
            res[name]["indent_ok"] += int(ind)
            res[name]["oracle_n"] += int(oracle_ok)
            res[name]["syntax_ok"] += int(oracle_ok and verdict == "right" and _compiles(out))
            by_noise[c["noise"]][name][verdict] += 1
            by_noise[c["noise"]][name]["n"] += 1
    return res, by_noise, tiers


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--json", default="")
    ap.add_argument("roots", nargs="*", default=["scaffold"])
    a = ap.parse_args()
    rng = random.Random(a.seed)
    cases = make_cases(a.roots, a.n, rng)
    res, by_noise, tiers = run(cases)
    print(f"cases={len(cases)} roots={a.roots}")
    print(f"{'matcher':8} {'apply':>7} {'right':>7} {'wrong':>7} {'indent_ok':>9} {'right+compiles':>14}")
    for name in ("exact", "legacy", "ladder"):
        r = res[name]
        n = r["n"] or 1
        print(f"{name:8} {(r['right'] + r['wrong']) / n:7.3f} {r['right'] / n:7.3f} "
              f"{r['wrong'] / n:7.3f} {r['indent_ok'] / n:9.3f} "
              f"{r['syntax_ok'] / (r['oracle_n'] or 1):14.3f}")
    print(f"(right+compiles is over the {res['ladder']['oracle_n']} cases whose ideal edit compiles)")
    print("ladder tiers:", dict(tiers))
    print("per noise (right rate exact/legacy/ladder, wrong legacy/ladder):")
    for noise, d in sorted(by_noise.items()):
        n = d["ladder"]["n"]
        print(f"  {noise:13} n={n:4} " + " ".join(
            f"{d[m]['right'] / n:.2f}" for m in ("exact", "legacy", "ladder"))
            + f"  wrong {d['legacy']['wrong'] / n:.2f}/{d['ladder']['wrong'] / n:.2f}")
    if a.json:
        Path(a.json).write_text(json.dumps({k: dict(v) for k, v in res.items()}, indent=1))


if __name__ == "__main__":
    main()
