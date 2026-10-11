"""
work_miner.py — derive how the owner actually works, from local signals only.

Instead of guessing which work to automate, AWOS reads what the owner really
does and ranks recurring task families by share x automatability x recurrence:

  * AI prompts the owner typed (Claude Code session transcripts, top-level
    sessions only — not subagents, tool results, task notifications, peer
    messages or system text), labelled into a task-family taxonomy by the
    LOCAL model (work_miner_label), with keyword rules as the fallback;
  * session patterns: how much babysitting the agents need (supervision
    share, agent run length before the owner steps in, what preceded each
    supervision prompt, weekly series) — work_miner_sessions;
  * shell commands (~/.zsh_history), rejoined into whole commands and
    normalised into families — work_miner_shell.

Everything stays on the machine: prompts are only ever sent to a localhost
llama-server, secrets are redacted before anything is kept, and the profile is
written to .awos/jobs/ (git-ignored).

    python -m scaffold.agent.work_miner                    # model if up, else rules
    python -m scaffold.agent.work_miner --start-server     # start/stop llama-server (4 slots)
    python -m scaffold.agent.work_miner --label rules      # deterministic only
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from scaffold.agent import work_miner_label as wl
from scaffold.agent import work_miner_sessions as ws
from scaffold.agent import work_miner_shell as wsh

CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
ZSH_HISTORY = Path.home() / ".zsh_history"

_SECRETS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"\b(?:ghp|gho|ghs|github_pat)_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]+"),
    re.compile(r"(?i)\b(api[_-]?key|token|secret|password|passwd|bearer)\b\s*[=:]?\s*['\"]?[A-Za-z0-9_\-./+]{8,}"),
    re.compile(r"\b[0-9a-f]{32,}\b"),
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),
]


def redact(text: str) -> str:
    for pat in _SECRETS:
        text = pat.sub("[redacted]", text)
    return text


TAXONOMY = wl.TAXONOMY
SUPERVISION = wl.SUPERVISION

# Deterministic fallback, first match wins (order matters). Families are the
# taxonomy in work_miner_label; automatability comes from there too.
PROMPT_RULES: list[tuple[str, re.Pattern]] = [
    ("continue / approve", re.compile(r"^\s*(continue|resume|go ahead|go on|proceed|carry on|keep going|yes\b|yep\b|yeah\b|ok\b|okay\b|sure\b|do it\b|go\b|try again|retry|option \d)", re.I)),
    ("status / progress check", re.compile(r"\b(status|progress|what'?s going on|how far|whats happening|what is happening|are you done|is it done|any update|still running|finished\?)\b", re.I)),
    ("keys / credentials / quota", re.compile(r"\b(api[ _-]?keys?|keys?|credentials?|tokens?|credits?|quota|rate[ -]?limit|login|log in|auth\w*|openrouter|billing)\b", re.I)),
    ("steer / redirect agent", re.compile(r"^\s*(no\b|nope|don'?t|stop\b|instead|actually|wait\b|not that|but\b)", re.I)),
    ("git / commit / PR / merge", re.compile(r"\b(commit|push|merge|pull request|PR|rebase|branch|release)\b", re.I)),
    ("run benchmark / experiment", re.compile(r"\b(run|benchmark|experiment|ablation|eval|series|baseline|swe-?bench)\b", re.I)),
    ("debug / fix", re.compile(r"\b(fix|bug|error|fail\w*|broken|crash\w*|debug|traceback|exception|not working)\b", re.I)),
    ("environment setup / install", re.compile(r"\b(install|setup|set up|configure|config|env|dependenc\w+|brew|venv)\b", re.I)),
    ("rules / memory / preferences", re.compile(r"\b(remember|from now on|always|never|rules?|memory|preferences?)\b", re.I)),
    ("agent orchestration / delegation", re.compile(r"\b(parallel|subagents?|spawn|delegate|multiple agents|split (the )?work)\b", re.I)),
    ("machine / system admin", re.compile(r"\b(disk|storage|free space|laptop|battery|processes|mac ?os|finder|backup)\b", re.I)),
    ("research / explore", re.compile(r"\b(research|explore|paper|survey|investigate|learn|read about|find out|compare)\b", re.I)),
    ("docs / writing", re.compile(r"\b(docs?|readme|write[- ]?up|report|blog|post|email|document)\b", re.I)),
    ("plan / strategy / discuss", re.compile(r"\b(plan|strategy|discuss|think|idea|vision|business|monetar|roadmap|priorit\w+)\b", re.I)),
    ("build feature / implement", re.compile(r"\b(build|implement|add|create|develop|make|write|feature)\b", re.I)),
    ("explain / understand", re.compile(r"\b(explain|understand|what is|what are|how does|why)\b", re.I)),
    ("review / verify / test", re.compile(r"\b(review|verify|check|test|validate|confirm)\b", re.I)),
]


def automatability(family: str) -> float:
    return TAXONOMY.get(family, (0.3, ""))[0]


def classify_prompt(text: str) -> tuple[str, float]:
    for family, pat in PROMPT_RULES:
        if pat.search(text):
            return family, automatability(family)
    return "other", automatability("other")


@dataclass
class Event:
    source: str          # "ai_prompt" | "shell"
    family: str
    automatability: float
    text: str            # redacted, truncated
    project: str = ""
    day: Optional[str] = None
    chars: int = 0


def read_prompts(root: Path = CLAUDE_PROJECTS) -> Iterable[Event]:
    """Owner-typed prompts from top-level Claude Code sessions (rules-labelled)."""
    for s in ws.read_sessions(root):
        for p in s.prompts:
            family, auto = classify_prompt(p.text)
            day = p.ts.date().isoformat() if p.ts else None
            yield Event("ai_prompt", family, auto, redact(" ".join(p.text.split()))[:80],
                        p.project, day, len(p.text))


def read_shell(path: Path = ZSH_HISTORY) -> Iterable[Event]:
    for c in wsh.read_commands(path):
        fam = wsh.command_family(c.text)
        day = datetime.fromtimestamp(c.ts).date().isoformat() if c.ts else None
        first = c.text.strip().splitlines()[0] if c.text.strip() else ""
        yield Event("shell", fam, wsh.shell_automatability(fam), redact(first)[:80],
                    day=day, chars=len(c.text))


UNDATED_RECURRENCE = 0.5


@dataclass
class Family:
    name: str
    source: str
    count: int = 0
    days: set = field(default_factory=set)
    projects: Counter = field(default_factory=Counter)
    automatability: float = 0.0
    chars: int = 0
    examples: list = field(default_factory=list)

    def recurrence(self, observed_days: int) -> float:
        """Fraction of observed days the family shows up on. A source with no
        dates (plain zsh history) gets a neutral UNDATED_RECURRENCE so it does
        not outrank dated prompt families by default."""
        if observed_days <= 0:
            return UNDATED_RECURRENCE
        return len(self.days) / observed_days

    def score(self, total: int, observed_days: int) -> float:
        share = self.count / max(total, 1)
        return round(share * self.automatability * self.recurrence(observed_days) * 100, 2)


def build_profile(events: Iterable[Event]) -> dict:
    fams: dict[tuple[str, str], Family] = {}
    totals: Counter = Counter()
    days: dict[str, set] = {}
    for e in events:
        totals[e.source] += 1
        f = fams.setdefault((e.source, e.family), Family(e.family, e.source, automatability=e.automatability))
        f.count += 1
        f.chars += e.chars
        if e.day:
            f.days.add(e.day)
            days.setdefault(e.source, set()).add(e.day)
        if e.project:
            f.projects[e.project] += 1
        if e.text and len(f.examples) < 2 and e.text not in f.examples:
            f.examples.append(e.text)
    rows = []
    for (source, _), f in fams.items():
        nd = len(days.get(source, ()))
        rows.append({
            "source": source, "family": f.name, "count": f.count,
            "share": round(f.count / max(totals[source], 1), 3),
            "active_days": len(f.days), "recurrence": round(f.recurrence(nd), 2),
            "automatability": f.automatability,
            "avg_chars": round(f.chars / max(f.count, 1)),
            "top_projects": [p for p, _ in f.projects.most_common(3)],
            "score": f.score(totals[source], nd), "examples": f.examples,
        })
    rows.sort(key=lambda r: (r["source"], -r["score"]))
    return {"generated": datetime.now().isoformat(timespec="seconds"),
            "totals": dict(totals),
            "observed_days": {k: len(v) for k, v in days.items()},
            "families": rows}


# ------------------------------------------------------- automation candidates

# family -> (candidate, how AWOS takes it over). Families mapping to the same
# candidate pool their scores (e.g. git prompts + git shell commands).
PLAYBOOK: dict[str, tuple[str, str]] = {
    "continue / approve": ("Supervision autopilot", "when an agent stops at a routine checkpoint, auto-reply continue/approve under standing policy + budget caps; page the owner only on risk or real choices"),
    "status / progress check": ("Push status digests", "post each running agent's progress, ETA and blockers on a timer and on finish, so the owner never has to ask"),
    "keys / credentials / quota": ("Key & quota health", "preflight every provider key/credit before a run, fail over to a healthy provider, and notify only when a human must act"),
    "run benchmark / experiment": ("Experiment runner", "queue benchmark/ablation runs as resumable jobs (job_series) with auto-written result summaries"),
    "debug / fix": ("Red-to-green fixer", "on failing tests or pasted errors, reproduce, patch and verify in a worktree before reporting"),
    "review / verify / test": ("Auto-verify gate", "run tests and a diff review on every agent change before it is reported done"),
    "git / commit / PR / merge": ("Git steward", "commit, branch, merge and open PRs automatically after verified changes"),
    "git: inspect": ("Git steward", ""), "git: commit/push": ("Git steward", ""),
    "git: sync/branch": ("Git steward", ""), "git: other": ("Git steward", ""), "github cli": ("Git steward", ""),
    "run tests": ("Auto-verify gate", ""),
    "steer / redirect agent": ("Preference capture", ""),
    "build feature / implement": ("Spec-to-PR worker", "take well-specified features end to end (plan, implement, verify, PR)"),
    "environment setup / install": ("Setup recipes", "record setup steps once as a replayable skill; re-run them on new machines/projects"),
    "python packages": ("Setup recipes", ""), "system packages": ("Setup recipes", ""), "node / js": ("Setup recipes", ""),
    "machine / system admin": ("Machine janitor", "scheduled disk/process/OS health checks with one-line fixes proposed"),
    "disk / system info": ("Machine janitor", ""), "processes": ("Machine janitor", ""),
    "AI coding agent": ("Agent launcher", "AWOS starts and resumes agent sessions itself (right project, model and task) instead of the owner typing them"),
    "agent orchestration / delegation": ("Planner-worker fan-out", "split owner goals into parallel non-colliding agents automatically and merge results"),
    "docs / writing": ("Doc updater", "regenerate docs/reports from results and diffs after each milestone"),
    "rules / memory / preferences": ("Preference capture", "turn corrections and stated rules into persistent memory/specs so the same steer is never needed twice"),
    "research / explore": ("Background research digests", "run research questions as background jobs that return a cited digest"),
    "explain / understand": ("Codebase Q&A", "answer how-does-X-work questions from a local index without a full agent session"),
    "local models": ("Setup recipes", ""),
}
_NOT_CANDIDATES = {"other", "plan / strategy / discuss", "terminal housekeeping", "navigate files",
                   "read files", "edit files", "open apps/files", "print / inspect env"}


def automation_candidates(profile: dict, sessions: Optional[dict] = None, top: int = 5) -> list[dict]:
    pooled: dict[str, dict] = {}
    for r in profile["families"]:
        fam = r["family"]
        if fam in _NOT_CANDIDATES or fam.startswith("other") or fam not in PLAYBOOK:
            continue
        name, how = PLAYBOOK[fam]
        c = pooled.setdefault(name, {"candidate": name, "how": "", "score": 0.0, "families": [], "events": 0})
        c["score"] = round(c["score"] + r["score"], 2)
        c["events"] += r["count"]
        c["families"].append(f"{r['source']}:{fam}")
        if how and not c["how"]:
            c["how"] = how
    for c in pooled.values():
        if not c["how"]:
            c["how"] = next((h for f, (n, h) in PLAYBOOK.items() if n == c["candidate"] and h), "")
    ranked = sorted(pooled.values(), key=lambda c: -c["score"])[:top]
    if sessions:
        for c in ranked:
            if c["candidate"] == "Supervision autopilot":
                c["evidence"] = (f"supervision share {sessions['supervision_share']:.0%}, "
                                 f"{sessions.get('supervision_per_agent_hour')} supervision prompts per agent-hour")
    return ranked


# ------------------------------------------------------------------ labelling

def label_prompts(texts: list[str], mode: str = "auto", cache_path: Optional[Path] = None,
                  workers: int = 4, labeller: Optional[wl.LocalLabeller] = None) -> tuple[dict, dict]:
    """Return ({text_hash: family}, stats). mode: auto | model | rules."""
    rules = {wl.prompt_hash(t): classify_prompt(t)[0] for t in texts}
    stats = {"mode": mode, "prompts": len(texts), "model_labelled": 0, "cache_hits": 0,
             "rules_fallback": 0, "server": False, "proposals": {}}
    labels = dict(rules)
    model: dict = {}
    if mode != "rules":
        lab = labeller or wl.LocalLabeller(cache_path=cache_path, workers=workers, redact=redact)
        stats["server"] = lab.available()
        if stats["server"] or (lab.cache and lab.cache.data):
            if not stats["server"]:           # offline: cache only
                model = {h: v for h in rules if (v := lab.cache.get(h))}
            else:
                model = lab.label_many(texts)
        elif mode == "model":
            raise RuntimeError("local model server is not reachable (scripts/local_model.sh start)")
        stats["cache_hits"] = sum(1 for v in model.values() if v.source == "cache")
        stats["model_labelled"] = len(model)
        for h, v in model.items():
            labels[h] = v.label
        props = Counter(v.new_family for v in model.values() if v.new_family)
        stats["proposals"] = dict(props.most_common(15))
    stats["rules_fallback"] = sum(1 for h in rules if h not in model)
    uniq = set(rules)
    stats["unclassified_rules"] = round(sum(1 for h in uniq if rules[h] == "other") / max(len(uniq), 1), 3)
    stats["unclassified_final"] = round(sum(1 for h in uniq if labels[h] == "other") / max(len(uniq), 1), 3)
    both = [h for h in uniq if h in model]
    if both:
        stats["model_rules_agreement"] = round(sum(rules[h] == labels[h] for h in both) / len(both), 3)
    return labels, stats


# -------------------------------------------------------------------- report

def _fmt(v, unit=""):
    return "n/a" if v is None else f"{v}{unit}"


def render_markdown(profile: dict) -> str:
    out = [f"# Work profile ({profile['generated']})", "",
           "Derived locally from your AI prompts, Claude Code sessions and shell history. "
           "Prompts were labelled only by the local model (or keyword rules); secrets redacted; "
           "nothing left this machine.", "",
           f"Events: {profile['totals']} · observed days: {profile.get('observed_days', {})}", ""]
    lab = profile.get("labelling")
    if lab:
        out += ["## Labelling", "",
                f"- mode `{lab['mode']}`, server up: {lab['server']}, prompts {lab['prompts']}, "
                f"model-labelled {lab['model_labelled']} (cache {lab['cache_hits']}), rules fallback {lab['rules_fallback']}",
                f"- unclassified ('other'): rules {lab['unclassified_rules']:.0%} -> final {lab['unclassified_final']:.0%}"
                + (f"; model/rules agreement {lab['model_rules_agreement']:.0%}" if "model_rules_agreement" in lab else ""),
                f"- taxonomy {wl.TAXONOMY_VERSION}; model new-family proposals: {lab['proposals'] or 'none'}", ""]
    out += ["Score = share x automatability x recurrence (fraction of observed days the family appears) x 100.", ""]
    for source, title in (("ai_prompt", "What you ask AI to do"), ("shell", "What you do in the shell")):
        rows = [r for r in profile["families"] if r["source"] == source]
        if not rows:
            continue
        out += [f"## {title}", "",
                "| # | Family | Count | Share | Days | Recur. | Automatable | Score |",
                "|---|---|---|---|---|---|---|---|"]
        for i, r in enumerate(rows[:18], 1):
            out.append(f"| {i} | {r['family']} | {r['count']} | {r['share']:.0%} | {r['active_days']} "
                       f"| {r['recurrence']:.2f} | {r['automatability']:.1f} | {r['score']} |")
        out.append("")
    s = profile.get("sessions")
    if s:
        out += ["## Session patterns (how much babysitting agents need)", "",
                f"- sessions with owner prompts: {s['sessions']}; owner prompts: {s['owner_prompts']} "
                f"(per session median {s['prompts_per_session']['median']}, mean {s['prompts_per_session']['mean']}, "
                f"p90 {s['prompts_per_session']['p90']}; single-prompt sessions {s['single_prompt_sessions']})",
                f"- time between owner prompts: median {_fmt(s['gap_between_prompts_min']['median'], ' min')}, "
                f"p90 {_fmt(s['gap_between_prompts_min']['p90'], ' min')}",
                f"- agent run before the owner intervenes: median {_fmt(s['agent_run_before_intervention_min']['median'], ' min')}, "
                f"p90 {_fmt(s['agent_run_before_intervention_min']['p90'], ' min')}; "
                f"before a supervision prompt: median {_fmt(s['agent_run_before_supervision_min']['median'], ' min')}",
                f"- agent idle before the owner's next prompt: median {_fmt(s['idle_before_prompt_min']['median'], ' min')}, "
                f"p90 {_fmt(s['idle_before_prompt_min']['p90'], ' min')}",
                f"- **supervision share: {s['supervision_share']:.0%}** ({s['supervision_prompts']} prompts; "
                f"{_fmt(s['supervision_per_agent_hour'])} per agent-hour over {s['agent_hours']} agent-hours)",
                f"- supervision by family: {s['supervision_by_family']}",
                f"- prompt sources: {s['prompt_sources']}", "",
                "What preceded each owner prompt (first matching cause):", "",
                "| Preceded by | Supervision prompts | Other prompts |", "|---|---|---|"]
        for k in ws.PRECEDERS:
            a = s["preceded_by_supervision"].get(k, {"count": 0, "share": 0})
            b = s["preceded_by_other"].get(k, {"count": 0, "share": 0})
            if a["count"] or b["count"]:
                out.append(f"| {k} | {a['count']} ({a['share']:.0%}) | {b['count']} ({b['share']:.0%}) |")
        out += ["", "### Weekly supervision series", "",
                "| Week | Sessions | Owner prompts | Supervision | Share | Agent-hours | Sup./agent-hour |",
                "|---|---|---|---|---|---|---|"]
        for w in s["weekly"]:
            out.append(f"| {w['week']} | {w['sessions']} | {w['prompts']} | {w['supervision']} | "
                       f"{w['supervision_share']:.0%} | {w['agent_hours']} | {_fmt(w['supervision_per_agent_hour'])} |")
        out.append("")
    cands = profile.get("candidates")
    if cands:
        out += ["## Top automation candidates", ""]
        for i, c in enumerate(cands, 1):
            ev = f" ({c['evidence']})" if c.get("evidence") else ""
            out.append(f"{i}. **{c['candidate']}** (score {c['score']}, {c['events']} events{ev}) — {c['how']}.")
        out.append("")
    return "\n".join(out)


def run(projects: Path = CLAUDE_PROJECTS, history: Path = ZSH_HISTORY, out_dir: Optional[Path] = None,
        mode: str = "auto", workers: int = 4, labeller: Optional[wl.LocalLabeller] = None) -> dict:
    out_dir = out_dir or Path.cwd() / ".awos" / "jobs"
    out_dir.mkdir(parents=True, exist_ok=True)
    sessions = ws.read_sessions(projects)
    texts = [p.text for s in sessions for p in s.prompts]
    labels, lstats = label_prompts(texts, mode, out_dir / "work_miner_labels.json", workers, labeller)
    ws.apply_labels(sessions, lambda t: labels.get(wl.prompt_hash(t), classify_prompt(t)[0]), SUPERVISION)
    events: list[Event] = []
    for s in sessions:
        for p in s.prompts:
            # no prompt text in the stored profile: prompts are private
            events.append(Event("ai_prompt", p.family, automatability(p.family), "", p.project,
                                p.ts.date().isoformat() if p.ts else None, len(p.text)))
    events.extend(read_shell(history))
    profile = build_profile(events)
    profile["labelling"] = lstats
    profile["sessions"] = ws.session_metrics(sessions)
    profile["candidates"] = automation_candidates(profile, profile["sessions"])
    (out_dir / "work_profile.json").write_text(json.dumps(profile, indent=1), encoding="utf-8")
    (out_dir / "work_profile.md").write_text(render_markdown(profile), encoding="utf-8")
    return profile


def main(argv: Optional[list] = None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--label", choices=["auto", "model", "rules"], default="auto")
    ap.add_argument("--start-server", action="store_true", help="start llama-server for the run, stop it after")
    ap.add_argument("--slots", type=int, default=4)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    with wl.local_server(a.slots, enabled=a.start_server and a.label != "rules"):
        prof = run(out_dir=a.out, mode=a.label, workers=a.slots)
    if not a.quiet:
        print(render_markdown(prof))
    return prof


if __name__ == "__main__":
    main()
