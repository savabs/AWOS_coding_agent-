"""
work_miner.py — derive how the owner actually works, from local signals only.

Instead of guessing which work to automate, AWOS reads what the owner really
does and ranks recurring task families by frequency x effort x automatability:

  * AI prompts the owner typed (Claude Code session transcripts, top-level
    sessions only — not subagents, tool results or system text);
  * shell commands (~/.zsh_history).

Everything stays on the machine: secrets are redacted before anything is kept,
and the profile is written to an ignored directory. Classification is
deterministic keyword rules in v0 (no model call); a local-model labeller can
replace `classify_prompt` later without changing the report.

    python -m scaffold.agent.work_miner            # writes .awos/jobs/work_profile.{md,json}
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
ZSH_HISTORY = Path.home() / ".zsh_history"

_SECRETS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"\b(?:ghp|gho|ghs|github_pat)_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    re.compile(r"(?i)\b(api[_-]?key|token|secret|password|passwd)\b\s*[=:]\s*\S+"),
    re.compile(r"\b[0-9a-f]{32,}\b"),
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),
]


def redact(text: str) -> str:
    for pat in _SECRETS:
        text = pat.sub("[redacted]", text)
    return text


# Task families for AI prompts: first matching rule wins (order matters).
# Each: (family, automatability 0-1, keyword regex). Automatability is how
# routine and checkable the work usually is — a prior the owner can tune.
PROMPT_RULES: list[tuple[str, float, re.Pattern]] = [
    ("status / progress check", 0.9, re.compile(r"\b(status|progress|update|what'?s going on|how far|whats happening|what is happening)\b", re.I)),
    ("continue / resume work", 0.7, re.compile(r"^\s*(continue|resume|go ahead|proceed|carry on|keep going|yes\b|ok\b|okay\b)", re.I)),
    ("keys / credentials / setup", 0.6, re.compile(r"\b(key|api key|credential|token|env|install|setup|configure|config)\b", re.I)),
    ("run benchmark / experiment", 0.8, re.compile(r"\b(run|benchmark|experiment|ablation|eval|issues|series|baseline)\b", re.I)),
    ("debug / fix", 0.7, re.compile(r"\b(fix|bug|error|fail|broken|crash|debug|why .* not|issue)\b", re.I)),
    ("research / explore", 0.4, re.compile(r"\b(research|explore|paper|survey|investigate|learn|read about|find out)\b", re.I)),
    ("plan / strategy / discuss", 0.2, re.compile(r"\b(plan|strategy|discuss|think|idea|vision|business|monetar|level of|what is awos)\b", re.I)),
    ("build feature / implement", 0.5, re.compile(r"\b(build|implement|add|create|develop|make|write|feature)\b", re.I)),
    ("explain / understand", 0.5, re.compile(r"\b(explain|understand|what is|what are|how does|why)\b", re.I)),
    ("review / verify", 0.7, re.compile(r"\b(review|verify|check|test|validate|confirm)\b", re.I)),
]

SHELL_FAMILIES = {
    "git": "version control", "gh": "version control",
    "python": "run python", "python3": "run python", "pip": "python packages", "pip3": "python packages",
    "pytest": "run tests", "npm": "node / js", "node": "node / js", "npx": "node / js", "yarn": "node / js",
    "brew": "system packages", "apt": "system packages", "sudo": "admin / system",
    "cd": "navigate files", "ls": "navigate files", "cat": "read files", "less": "read files",
    "vim": "edit files", "nano": "edit files", "code": "edit files", "open": "open apps/files",
    "ssh": "remote machines", "scp": "remote machines", "rsync": "remote machines",
    "docker": "containers", "kill": "processes", "ps": "processes", "top": "processes",
    "claude": "AI assistant", "ollama": "local models", "llama-server": "local models",
}


def classify_prompt(text: str) -> tuple[str, float]:
    for family, auto, pat in PROMPT_RULES:
        if pat.search(text):
            return family, auto
    return "other", 0.3


@dataclass
class Event:
    source: str          # "ai_prompt" | "shell"
    family: str
    automatability: float
    text: str            # redacted, truncated
    project: str = ""
    day: Optional[str] = None
    chars: int = 0


def _prompt_text(message: dict) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content
                 if isinstance(b, dict) and b.get("type") == "text"]
        return "\n".join(parts)
    return ""


_NOISE = re.compile(r"^\s*(<command-|<local-command|<system-reminder|Caveat:|\[Request interrupted|<task-notification|This session is being continued)", re.I)


def read_prompts(root: Path = CLAUDE_PROJECTS) -> Iterable[Event]:
    """Owner-typed prompts from top-level Claude Code sessions."""
    if not root.is_dir():
        return
    for path in sorted(root.glob("*/*.jsonl")):   # top level only: subagents live deeper
        project = path.parent.name.replace("-Users-becmachlean-", "").replace("-", "/")[:60]
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("type") != "user" or rec.get("isSidechain"):
                continue
            text = _prompt_text(rec.get("message") or {}).strip()
            if not text or _NOISE.match(text) or len(text) > 4000:
                continue
            family, auto = classify_prompt(text)
            day = str(rec.get("timestamp", ""))[:10] or None
            clean = redact(" ".join(text.split()))
            yield Event("ai_prompt", family, auto, clean[:140], project, day, len(text))


def read_shell(path: Path = ZSH_HISTORY) -> Iterable[Event]:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        cmd = raw.split(";", 1)[1] if raw.startswith(": ") and ";" in raw else raw
        cmd = cmd.strip()
        if not cmd:
            continue
        tool = cmd.split()[0].split("/")[-1]
        if tool == "sudo" and len(cmd.split()) > 1:
            tool = cmd.split()[1]
        family = SHELL_FAMILIES.get(tool, "other: " + tool[:20])
        yield Event("shell", family, 0.6 if family != "navigate files" else 0.2,
                    redact(cmd)[:100], chars=len(cmd))


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

    def score(self, total: int) -> float:
        """Share of all events x automatability, boosted by recurring on many days."""
        share = self.count / max(total, 1)
        recurrence = 1.0 + min(len(self.days), 30) / 30.0
        return round(share * self.automatability * recurrence * 100, 2)


def build_profile(events: Iterable[Event]) -> dict:
    fams: dict[tuple[str, str], Family] = {}
    totals: Counter = Counter()
    for e in events:
        totals[e.source] += 1
        f = fams.setdefault((e.source, e.family), Family(e.family, e.source, automatability=e.automatability))
        f.count += 1
        f.chars += e.chars
        if e.day:
            f.days.add(e.day)
        if e.project:
            f.projects[e.project] += 1
        if len(f.examples) < 3 and e.text not in f.examples:
            f.examples.append(e.text)
    rows = []
    for (source, _), f in fams.items():
        rows.append({
            "source": source, "family": f.name, "count": f.count,
            "share": round(f.count / max(totals[source], 1), 3),
            "active_days": len(f.days), "automatability": f.automatability,
            "avg_chars": round(f.chars / max(f.count, 1)),
            "top_projects": [p for p, _ in f.projects.most_common(3)],
            "score": f.score(totals[source]), "examples": f.examples,
        })
    rows.sort(key=lambda r: (r["source"], -r["score"]))
    return {"generated": datetime.now().isoformat(timespec="seconds"),
            "totals": dict(totals), "families": rows}


def render_markdown(profile: dict) -> str:
    out = [f"# Work profile ({profile['generated']})", "",
           "Derived locally from your AI prompts and shell history. Secrets redacted; "
           "nothing leaves this machine.", "",
           f"Events: {profile['totals']}", ""]
    for source, title in (("ai_prompt", "What you ask AI to do"), ("shell", "What you do in the shell")):
        rows = [r for r in profile["families"] if r["source"] == source]
        if not rows:
            continue
        out += [f"## {title}", "",
                "| Family | Count | Share | Days | Automatable | Score | Example |",
                "|---|---|---|---|---|---|---|"]
        for r in rows[:15]:
            ex = (r["examples"][0] if r["examples"] else "").replace("|", "/")[:70]
            out.append(f"| {r['family']} | {r['count']} | {r['share']:.0%} | {r['active_days']} "
                       f"| {r['automatability']:.1f} | {r['score']} | {ex} |")
        out.append("")
    return "\n".join(out)


def main(out_dir: Optional[Path] = None) -> dict:
    out_dir = out_dir or Path.cwd() / ".awos" / "jobs"
    out_dir.mkdir(parents=True, exist_ok=True)
    profile = build_profile([*read_prompts(), *read_shell()])
    (out_dir / "work_profile.json").write_text(json.dumps(profile, indent=1), encoding="utf-8")
    (out_dir / "work_profile.md").write_text(render_markdown(profile), encoding="utf-8")
    return profile


if __name__ == "__main__":
    p = main()
    print(render_markdown(p))
