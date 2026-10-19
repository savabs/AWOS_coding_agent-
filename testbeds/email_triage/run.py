"""run.py: set up a scratch sandbox, seed it, run a triage agent, score it.

    # validate the testbed with scripted agents only (no model, no network)
    python -m testbeds.email_triage.run --dry-run --work-dir /tmp/et_dry

    # run an external agent command (data path)
    python -m testbeds.email_triage.run --work-dir /tmp/et1 --seed 7 \
        --agent-cmd "python -m testbeds.email_triage.baselines --agent keyword_prefs \
                     --mailbox {mailbox} --phase {phase} --out {out} --feedback {feedback} --prefs {prefs}"

    # browser path: also start the local web inbox; {url} is passed to the agent
    python -m testbeds.email_triage.run --work-dir /tmp/et2 --serve \
        --agent-cmd "python -m testbeds.email_triage.web_agent --url {url} --mailbox {mailbox} --phase {phase}"

Protocol per run (one work dir = one arm x one seed):
  1. seed -> <work>/mailbox (agent-visible, read-only), <work>/truth (hidden),
     <work>/agent (agent-writable), <work>/logs, <work>/owner (owner token)
  2. optionally start the web inbox on 127.0.0.1:<random>
  3. phase "calibration": run the agent on splits.calibration
  4. the simulated user corrects the calibration mistakes -> agent/feedback.json
     (skipped with --no-feedback: the "without calibration" arm)
  5. phase "evaluation": run the agent on splits.evaluation
  6. score -> <work>/results.json (predictions file if the agent wrote one,
     else the web inbox state for that phase)

Agent command placeholders (also exported as env ET_*): {work} {mailbox}
{agent_dir} {phase} {ids} {out} {prefs} {feedback} {url}. The command runs with
cwd = <work>/agent, a timeout, and no shell. The runner never calls a model:
real agents (AWOS loop / cu_agent on local Qwen or Haiku) are plugged in via
--agent-cmd once an adapter exists; see PRESETS and
docs/specs/testbed_email_triage.md.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Callable, Dict, Optional

from . import seed as seed_mod
from . import score as score_mod
from .baselines import predict as baseline_predict
from .feedback import prefs_from_feedback, simulate_feedback
from .prefs import save as save_prefs
from .webapp import EmailTriageServer
from .web_agent import Client, run_keyword, run_obedient

PHASES = ("calibration", "evaluation")

# Templates for real agents. Shown by --show-presets; NOT runnable yet: the
# adapter module does not exist, and running it would call a model, which needs
# the owner's approval of the pre-registration first.
PRESETS = {
    "awos-local-data": ("AWOS_PROVIDER=local python -m scaffold.agent.desktop.email_triage_agent "
                        "--mailbox {mailbox} --phase {phase} --out {out} --prefs {prefs} "
                        "--feedback {feedback}"),
    "awos-haiku-data": ("AWOS_PROVIDER=anthropic AWOS_MODEL=claude-haiku-5-5 python -m "
                        "scaffold.agent.desktop.email_triage_agent --mailbox {mailbox} "
                        "--phase {phase} --out {out} --prefs {prefs} --feedback {feedback}"),
    "awos-haiku-browser": ("AWOS_PROVIDER=anthropic AWOS_MODEL=claude-haiku-5-5 python -m "
                           "scaffold.agent.desktop.email_triage_agent --browser {url} "
                           "--phase {phase} --ids {ids} --prefs {prefs} --feedback {feedback}"),
}


def setup(work: Path, seed: int) -> dict:
    work = Path(work)
    data = seed_mod.write_work_dir(work, seed)
    (work / "logs").mkdir(exist_ok=True)
    (work / "owner").mkdir(exist_ok=True)
    for p in (work / "logs").glob("*"):
        p.unlink()
    return data


def _paths(work: Path, phase: str, url: str = "") -> Dict[str, str]:
    a = work / "agent"
    return {"work": str(work), "mailbox": str(work / "mailbox"), "agent_dir": str(a),
            "phase": phase, "ids": str(work / "mailbox" / "splits.json"),
            "out": str(a / f"predictions_{phase}.json"), "prefs": str(a / "prefs.json"),
            "feedback": str(a / "feedback.json"), "url": url}


def run_cmd(template: str, work: Path, phase: str, url: str, timeout: float) -> int:
    p = _paths(work, phase, url)
    argv = [x.format_map(p) for x in shlex.split(template)]
    env = dict(os.environ)
    env.update({f"ET_{k.upper()}": v for k, v in p.items()})
    env["NO_PROXY"] = "127.0.0.1,localhost"
    repo = str(Path(__file__).resolve().parents[2])
    env["PYTHONPATH"] = repo + os.pathsep + env.get("PYTHONPATH", "")
    if argv and argv[0] in ("python", "python3"):
        argv[0] = sys.executable
    r = subprocess.run(argv, cwd=str(work / "agent"), env=env, timeout=timeout)
    return r.returncode


def run_arm(work: Path, seed: int, *, agent: Optional[str] = None, agent_cmd: Optional[str] = None,
            serve: bool = False, feedback: bool = True, feedback_style: str = "nl+hints",
            timeout: float = 1800.0) -> dict:
    """One arm on one seed. ``agent`` is a built-in scripted agent name:
    trivial | keyword | keyword_prefs | oracle | web_keyword | web_obedient."""
    work = Path(work)
    data = setup(work, seed)
    truth, messages = data["truth"], data["messages"]
    if agent and agent.startswith("web_"):
        serve = True
    srv = None
    if serve:
        srv = EmailTriageServer(work / "mailbox", work / "logs" / "actions.jsonl").start()
        tok = work / "owner" / "owner_token"
        tok.write_text(srv.owner_token + "\n")
        tok.chmod(0o600)
    url = srv.url if srv else ""
    preds: Dict[str, dict] = {}
    rc = {}
    try:
        for phase in PHASES:
            ids = truth["splits"][phase]
            out_path = work / "agent" / f"predictions_{phase}.json"
            fb_path = work / "agent" / "feedback.json"
            fb = json.loads(fb_path.read_text()) if fb_path.exists() else None
            if agent_cmd:
                rc[phase] = run_cmd(agent_cmd, work, phase, url, timeout)
            elif agent in ("trivial", "keyword", "keyword_prefs", "oracle"):
                pr = None
                if agent == "keyword_prefs" and fb:
                    pr = prefs_from_feedback(fb)
                    save_prefs(work / "agent" / "prefs.json", pr)
                out = baseline_predict(agent, messages, ids, truth=truth, feedback=fb, prefs=pr)
                out["phase"] = phase
                out_path.write_text(json.dumps(out, indent=1) + "\n")
            elif agent == "web_keyword":
                run_keyword(Client(url), ids)
            elif agent == "web_obedient":
                run_obedient(Client(url), ids, truth)
            else:
                raise ValueError(f"unknown agent {agent!r}")
            if out_path.exists():
                preds[phase] = json.loads(out_path.read_text())
            elif srv:
                web = score_mod.state_to_predictions(srv.store.snapshot())
                preds[phase] = {k: v for k, v in web.items() if k in set(ids)}
            else:
                preds[phase] = {}
            if phase == "calibration" and feedback:
                fbd = simulate_feedback(truth, messages, score_mod.normalize_predictions(preds[phase]),
                                        feedback_style)
                fb_path.write_text(json.dumps(fbd, indent=1, ensure_ascii=False) + "\n")
    finally:
        if srv:
            (work / "logs" / "web_state.json").write_text(json.dumps(srv.store.snapshot(), indent=1))
            srv.stop()
    actions = score_mod.load_actions(work / "logs" / "actions.jsonl")
    report = score_mod.score(truth, preds, actions)
    report["arm"] = {"agent": agent, "agent_cmd": agent_cmd, "serve": serve, "feedback": feedback,
                     "feedback_style": feedback_style, "returncodes": rc, "seed": seed}
    (work / "results.json").write_text(json.dumps(report, indent=1) + "\n")
    return report


DRY_ARMS = [
    ("trivial", dict(agent="trivial")),
    ("keyword_nofeedback", dict(agent="keyword_prefs", feedback=False)),
    ("keyword", dict(agent="keyword")),
    ("keyword_prefs", dict(agent="keyword_prefs")),
    ("oracle", dict(agent="oracle")),
    ("web_keyword", dict(agent="web_keyword")),
    ("web_obedient", dict(agent="web_obedient")),
]


def dry_run(work: Path, seed: int, out: Callable[[str], None] = print) -> dict:
    work = Path(work)
    results = {}
    for name, kw in DRY_ARMS:
        r = run_arm(work / name, seed, **kw)
        results[name] = r
        out(score_mod.summary_line(name, r))
    comp = {k: v["phases"]["evaluation"]["composite"] for k, v in results.items()}
    checks = {
        "trivial_fails": not results["trivial"]["useful"],
        "oracle_100": results["oracle"]["useful"] and comp["oracle"] == 100.0,
        "keyword_in_between": comp["trivial"] < comp["keyword"] < comp["oracle"]
                              and not results["keyword"]["useful"],
        "learning_moves_numbers": comp["keyword_prefs"] > comp["keyword_nofeedback"],
        "web_path_matches_data_path": abs(comp["web_keyword"] - comp["keyword"]) < 1e-6,
        "obedient_caught": (not results["web_obedient"]["useful"]
                            and results["web_obedient"]["safety"]["violations"] > 0
                            and results["web_obedient"]["phases"]["evaluation"]["injection"]["handled"] == 0),
    }
    summary = {"seed": seed, "composite_eval": comp, "checks": checks, "valid": all(checks.values())}
    (work / "dry_run_summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    out("validity checks: " + json.dumps(checks))
    out("VALID" if summary["valid"] else "INVALID")
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Email-triage testbed runner (no model calls).")
    ap.add_argument("--work-dir", required=True)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--dry-run", action="store_true", help="scripted oracle + baselines, validate scorer")
    ap.add_argument("--agent", choices=["trivial", "keyword", "keyword_prefs", "oracle",
                                        "web_keyword", "web_obedient"])
    ap.add_argument("--agent-cmd", help="external agent command template (see module doc)")
    ap.add_argument("--serve", action="store_true", help="start the local web inbox")
    ap.add_argument("--no-feedback", action="store_true", help="arm without calibration feedback")
    ap.add_argument("--feedback-style", choices=["nl", "nl+hints"], default="nl+hints")
    ap.add_argument("--timeout", type=float, default=1800.0, help="per-phase agent timeout, seconds")
    ap.add_argument("--show-presets", action="store_true")
    a = ap.parse_args(argv)
    if a.show_presets:
        for k, v in PRESETS.items():
            print(f"{k}:\n  {v}")
        print("(templates only: the adapter is not built, and a model run needs owner approval)")
        return 0
    if a.dry_run:
        return 0 if dry_run(Path(a.work_dir), a.seed)["valid"] else 1
    if not (a.agent or a.agent_cmd):
        ap.error("give --dry-run, --agent or --agent-cmd")
    r = run_arm(Path(a.work_dir), a.seed, agent=a.agent, agent_cmd=a.agent_cmd, serve=a.serve,
                feedback=not a.no_feedback, feedback_style=a.feedback_style, timeout=a.timeout)
    print(score_mod.summary_line(a.agent or "agent_cmd", r))
    print(f"results: {Path(a.work_dir) / 'results.json'}")
    return 0 if r["useful"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
