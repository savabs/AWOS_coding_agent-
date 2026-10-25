#!/usr/bin/env python3
"""
shape.py — offline prompt-shape analysis of the EngineBench agent workload: how much of each step's prompt a
prefix cache could reuse today, how much it could with a cache-friendly layout, and where the tokens go.

    python scripts/engine/shape.py [--workload bench/engine/workload-v1.jsonl.gz] [--chars-per-token 3.0]

Character counts, converted to tokens with --chars-per-token (bench.py measures the real ratio: prompt
tokens / prompt chars). Layouts compared, per consecutive step pair of one episode:
  current    system + user exactly as the product sends it (Goal, Step k of N, Current page, history, observation)
  history-first  system, Goal, the action history (append-only), THEN Step k / Current page / observation
  static-first   history-first plus the observation's sections that did not change put before the ones that did
The reuse number is the longest common prefix with the previous step's prompt (what llama.cpp can restore).
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parents[2]
for p in (str(ROOT), str(ROOT / "scripts" / "engine")):
    if p not in sys.path:
        sys.path.insert(0, p)

import capture  # noqa: E402

_HIST = re.compile(r"Your previous actions and results:\n(.*?)(?=\n<observation|\Z)", re.S)
_OBS = re.compile(r"<observation[^>]*>\n?(.*?)\n?</observation>", re.S)
_SECTIONS = ("URL path:", "Title:", "Dialog:", "Status:", "Regions:", "Page text:", "Controls (")


def lcp(a: str, b: str) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


def parts(user: str) -> Dict[str, str]:
    """The user message cut into goal / step / page / history / observation / tail."""
    obs_m = _OBS.search(user)
    hist_m = _HIST.search(user)
    goal = user.split("\n", 1)[0]
    head = user[len(goal) + 1:obs_m.start()] if obs_m else ""
    hist = hist_m.group(1) if hist_m else ""
    step_page = head.replace("Your previous actions and results:\n" + hist, "") if hist else head
    tail = user[obs_m.end():] if obs_m else ""
    return {"goal": goal, "step_page": step_page, "history": hist, "obs": obs_m.group(1) if obs_m else "", "tail": tail}


def obs_sections(obs: str) -> Dict[str, str]:
    out, cur = {}, "other"
    for line in obs.split("\n"):
        hit = next((s for s in _SECTIONS if line.startswith(s)), None)
        if hit:
            cur = hit.rstrip(":( ")
        out[cur] = out.get(cur, "") + line + "\n"
    return out


def layouts(system: str, user: str, prev_obs_secs: Dict[str, str]) -> Dict[str, str]:
    p = parts(user)
    hist_first = "\n".join([system, p["goal"], p["history"], p["step_page"], p["obs"], p["tail"]])
    secs = obs_sections(p["obs"])
    same = [k for k in secs if prev_obs_secs.get(k) == secs[k]]
    changed = [k for k in secs if k not in same]
    static_first = "\n".join([system, p["goal"], p["history"]] + [secs[k] for k in same] + [p["step_page"]] +
                             [secs[k] for k in changed] + [p["tail"]])
    return {"current": system + "\n" + user, "history-first": hist_first, "static-first": static_first}


def analyze(episodes: List[dict], cpt: float) -> Dict[str, object]:
    reuse = {k: [0, 0] for k in ("current", "history-first", "static-first")}
    sec_chars: Dict[str, int] = {}
    total_chars = 0
    hist_append_only = [0, 0]
    obs_same_lines = []
    for ep in episodes:
        if ep["kind"] != "agent":
            continue
        prev: Dict[str, str] = {}
        prev_secs: Dict[str, str] = {}
        prev_hist, prev_ctl = None, None
        for c in ep["calls"]:
            system, user = c["messages"][0]["content"], c["messages"][1]["content"]
            p = parts(user)
            secs = obs_sections(p["obs"])
            total_chars += len(system) + len(user)
            for k, v in (("system", system), ("goal+step+page+tail", p["goal"] + p["step_page"] + p["tail"]),
                         ("history", p["history"])):
                sec_chars[k] = sec_chars.get(k, 0) + len(v)
            for k, v in secs.items():
                sec_chars["obs:" + k] = sec_chars.get("obs:" + k, 0) + len(v)
            lays = layouts(system, user, prev_secs)
            for k, txt in lays.items():
                reuse[k][1] += len(txt)
                if prev:
                    reuse[k][0] += lcp(txt, prev[k])
            if prev_hist is not None:
                hist_append_only[1] += 1
                hist_append_only[0] += int(p["history"].startswith(prev_hist))
            ctl = set(l for l in secs.get("Controls", "").split("\n")[1:] if l)
            if prev_ctl:
                obs_same_lines.append(len(ctl & prev_ctl) / max(len(ctl), 1))
            prev, prev_secs, prev_hist, prev_ctl = lays, secs, p["history"], ctl
    return {"agent_chars": total_chars, "est_tokens_per_call": None,
            "reuse_share": {k: round(a / b, 4) if b else None for k, (a, b) in reuse.items()},
            "section_share": {k: round(v / total_chars, 4) for k, v in sorted(sec_chars.items(), key=lambda x: -x[1])},
            "history_append_only": round(hist_append_only[0] / hist_append_only[1], 4) if hist_append_only[1] else None,
            "controls_unchanged_vs_prev_step": round(statistics.mean(obs_same_lines), 4) if obs_same_lines else None,
            "chars_per_token": cpt}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--workload", type=Path, action="append")
    ap.add_argument("--chars-per-token", type=float, default=3.0)
    a = ap.parse_args(argv)
    _h, eps = capture.load(a.workload or [ROOT / "bench" / "engine" / "workload-v1.jsonl.gz"])
    print(json.dumps(analyze(eps, a.chars_per_token), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
