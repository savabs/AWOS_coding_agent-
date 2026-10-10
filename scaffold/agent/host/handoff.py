"""M1 host hand-off: turn a finished job's workspace into a local review branch.

Spec: docs/specs/host_handoff.md

Contract:
- ``finish(job, workspace, outcome)`` creates ``awos/<id8>`` in the target repo
  from the job's base commit, containing exactly the workspace diff.
- Pure git plumbing with a temporary index: the user's HEAD, index and
  working tree are never read-modified-written.
- Never pushes; no network git operation exists in this module.
- Credential guard: a secret in the diff blocks the branch; a secret in the
  report is redacted. Findings never contain the secret value.
"""
from __future__ import annotations

import datetime as _dt
import fnmatch
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

BRANCH_PREFIX = "awos/"

# Paths never handed off, whatever the workspace contains.
EXCLUDE_GLOBS: Tuple[str, ...] = (
    ".env", ".env.*", "*/.env", "*/.env.*",
    ".awos/*", "*/.awos/*",
    "*.pem", "*.key", "id_rsa*", "*/id_rsa*",
)

SECRET_PATTERNS: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("openai_like_sk", re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}")),
    ("github_token", re.compile(r"\b(?:ghp|gho|ghs|ghu|ghr)_[A-Za-z0-9]{30,}")),
    ("github_pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,}")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("slack_token", re.compile(r"\bxox[abpr]-[A-Za-z0-9\-]{10,}")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}")),
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
)
SECRET_ENV_NAME = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL", re.I)
MIN_ENV_SECRET_LEN = 8
JOB_ID_RE = re.compile(r"[A-Za-z0-9_-]+")
MAX_JOB_ID_LEN = 128
# Larger than any file a workspace can hold; keeps staged blobs loose.
STAGING_BIG_FILE_THRESHOLD = "1024g"
_NON_ASCII_TEXT = re.compile(r"[^\t\n\r\x20-\x7e]")
# Encodings an env secret value is matched in, as raw bytes.
_ENV_VALUE_ENCODINGS = ("utf-8", "utf-16-le", "utf-16-be", "utf-32-le", "utf-32-be")
# Config forced on every git call: no attribute-driven filters or diff
# drivers, no hooks, no fsmonitor command, nothing extra written into the
# user's GIT_DIR (split/untracked index caches).
_HARDENED_CONFIG: Tuple[Tuple[str, str], ...] = (
    ("core.bigFileThreshold", STAGING_BIG_FILE_THRESHOLD),
    ("core.attributesFile", os.devnull),
    ("core.hooksPath", os.devnull),
    ("core.fsmonitor", "false"),
    ("core.splitIndex", "false"),
    ("core.untrackedCache", "false"),
)

_GIT_IDENTITY = {
    "GIT_AUTHOR_NAME": "AWOS host",
    "GIT_AUTHOR_EMAIL": "awos-host@localhost",
    "GIT_COMMITTER_NAME": "AWOS host",
    "GIT_COMMITTER_EMAIL": "awos-host@localhost",
}


class HandoffError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# Credential guard
# --------------------------------------------------------------------------- #
def _env_secret_values(env: Optional[Dict[str, str]] = None) -> List[Tuple[str, str]]:
    env = dict(os.environ if env is None else env)
    out = []
    for name, val in env.items():
        if SECRET_ENV_NAME.search(name) and val and len(val) >= MIN_ENV_SECRET_LEN:
            out.append((name, val))
    return out


def scan_secrets(text: str, env: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    """Return findings ``{"kind", "name", "line"}`` — never the secret value.

    Env values are matched against the whole text, so a value that contains a
    newline is found too."""
    findings: List[Dict[str, Any]] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for name, pat in SECRET_PATTERNS:
            if pat.search(line):
                findings.append({"kind": "pattern", "name": name, "line": lineno})
    for var, val in _env_secret_values(env):
        start = text.find(val)
        while start != -1:
            findings.append({"kind": "env_value", "name": var,
                             "line": text.count("\n", 0, start) + 1})
            start = text.find(val, start + 1)
    findings.sort(key=lambda f: f["line"])
    return findings


def redact_secrets(text: str, env: Optional[Dict[str, str]] = None) -> str:
    # Longest value first: a value that is a prefix of another must not leave
    # the longer value's suffix behind.
    for var, val in sorted(_env_secret_values(env), key=lambda kv: -len(kv[1])):
        text = text.replace(val, f"[REDACTED:{var}]")
    for name, pat in SECRET_PATTERNS:
        text = pat.sub(f"[REDACTED:{name}]", text)
    return text


def _redact_obj(obj: Any, env: Optional[Dict[str, str]] = None) -> Any:
    """Redact every string in a JSON-able structure *before* serialising, so
    secret values containing quotes or backslashes match in their raw form."""
    if isinstance(obj, str):
        return redact_secrets(obj, env)
    if isinstance(obj, dict):
        return {k: _redact_obj(v, env) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_redact_obj(v, env) for v in obj]
    return obj


# --------------------------------------------------------------------------- #
# git helpers
# --------------------------------------------------------------------------- #
_SCRUBBED_GIT_ENV = ("GIT_INDEX_FILE", "GIT_DIR", "GIT_WORK_TREE", "GIT_OBJECT_DIRECTORY",
                     "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_CONFIG_PARAMETERS",
                     "GIT_CONFIG_COUNT", "GIT_EXTERNAL_DIFF", "GIT_ATTR_SOURCE")


def _git_env(env: Optional[Dict[str, str]]) -> Dict[str, str]:
    full_env = {k: v for k, v in os.environ.items()
                if k not in _SCRUBBED_GIT_ENV
                and not k.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"))}
    full_env["GIT_TERMINAL_PROMPT"] = "0"
    full_env["GIT_ATTR_NOSYSTEM"] = "1"
    full_env["GIT_CONFIG_COUNT"] = str(len(_HARDENED_CONFIG))
    for i, (key, val) in enumerate(_HARDENED_CONFIG):
        full_env[f"GIT_CONFIG_KEY_{i}"] = key
        full_env[f"GIT_CONFIG_VALUE_{i}"] = val
    if env:
        full_env.update(env)
    return full_env


def _git(args: List[str], *, cwd: Optional[str] = None, env: Optional[Dict[str, str]] = None,
         check: bool = True, input_text: Optional[str] = None) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, env=_git_env(env), input=input_text,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace")
    if check and proc.returncode != 0:
        raise HandoffError(f"git {args[0]} failed: {proc.stderr.strip()[:400]}")
    return proc.stdout


def _git_bytes(args: List[str], *, cwd: Optional[str] = None,
               env: Optional[Dict[str, str]] = None,
               input_bytes: Optional[bytes] = None) -> bytes:
    proc = subprocess.run(["git", *args], cwd=cwd, env=_git_env(env), capture_output=True,
                          input=input_bytes)
    if proc.returncode != 0:
        raise HandoffError(f"git {args[0]} failed: "
                           f"{proc.stderr.decode('utf-8', 'replace').strip()[:400]}")
    return proc.stdout


def _is_excluded(path: str) -> bool:
    return any(fnmatch.fnmatch(path, g) for g in EXCLUDE_GLOBS)


def host_root() -> Path:
    return Path(os.environ.get("AWOS_HOST_DIR") or Path.home() / ".awos" / "host").expanduser()


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _minutes(job: Dict[str, Any], outcome: Dict[str, Any]) -> Optional[float]:
    if outcome.get("minutes") is not None:
        return round(float(outcome["minutes"]), 1)
    ca = job.get("created_at")
    if not ca:
        return None
    try:
        start = _dt.datetime.fromisoformat(str(ca).replace("Z", "+00:00"))
        if start.tzinfo is None:
            start = start.replace(tzinfo=_dt.timezone.utc)
        return round((_dt.datetime.now(_dt.timezone.utc) - start).total_seconds() / 60, 1)
    except ValueError:
        return None


def _staging_env(repo_objects: str, staging_objects: str) -> Dict[str, str]:
    """Env that writes new objects to a private staging dir and reads the user's
    repo objects as an alternate. Nothing lands in the user's repo until
    ``_promote_objects`` copies the objects a created commit actually needs, so
    excluded files and blocked (secret-bearing) content are never stored there."""
    # core.bigFileThreshold (in _HARDENED_CONFIG, applied by _git_env) is forced
    # very high so hashing never streams a large blob into a packfile
    # (``_promote_objects`` copies loose objects only).
    return {"GIT_OBJECT_DIRECTORY": staging_objects,
            "GIT_ALTERNATE_OBJECT_DIRECTORIES": repo_objects}


def _promote_objects(repo: str, senv: Dict[str, str], commit: str, base: str) -> None:
    """Copy the loose objects reachable from ``commit`` but not ``base`` out of
    the staging dir into the user's repo object store."""
    staging = Path(senv["GIT_OBJECT_DIRECTORY"])
    target = Path(senv["GIT_ALTERNATE_OBJECT_DIRECTORIES"])
    out = _git(["rev-list", "--objects", commit, "--not", base], cwd=repo, env=senv)
    for line in out.splitlines():
        sha = line.split(" ", 1)[0].strip()
        if not sha:
            continue
        src = staging / sha[:2] / sha[2:]
        if not src.exists():
            continue  # already in the user's repo (read via the alternate)
        dst = target / sha[:2] / sha[2:]
        if dst.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + f".awos-tmp-{os.getpid()}")
        shutil.copyfile(src, tmp)
        os.replace(tmp, dst)
    _verify_objects_in_repo(repo, out)


def _verify_objects_in_repo(repo: str, rev_list_out: str) -> None:
    """Fail closed unless every object the new commit needs is readable from the
    user's repo *without* the staging alternate (e.g. a blob that ended up in a
    staging packfile instead of a loose object). Called before ``update-ref``."""
    shas = [l.split(" ", 1)[0].strip() for l in rev_list_out.splitlines()]
    shas = [x for x in shas if x]
    if not shas:
        return
    out = _git(["cat-file", "--batch-check=%(objectname)"], cwd=repo,
               input_text="\n".join(shas) + "\n")
    missing = [l.split()[0] for l in out.splitlines() if l.endswith(" missing")]
    if missing:
        raise HandoffError(f"{len(missing)} object(s) could not be promoted into the repo "
                           f"(first: {missing[0][:12]}); branch NOT created")


def _binary_text_views(data: bytes) -> List[str]:
    """Text views of raw bytes for the key-pattern scan: UTF-8, UTF-16 at both
    byte alignments and UTF-32 at all four (LE views at every offset also cover
    big-endian ASCII-range text, which is all the key patterns match)."""
    views = [data.decode("utf-8", "replace")]
    for width, codec in ((2, "utf-16-le"), (4, "utf-32-le")):
        for off in range(width):
            chunk = data[off:]
            chunk = chunk[: len(chunk) - (len(chunk) % width)]
            # Non-ASCII code units (misaligned junk, BOMs) become spaces so they
            # cannot glue onto a key and defeat the patterns' \b anchors.
            views.append(_NON_ASCII_TEXT.sub(" ", chunk.decode(codec, "replace")))
    return views


def _scan_bytes(data: bytes,
                env: Optional[Dict[str, str]]) -> Dict[Tuple[str, str], Tuple[int, int]]:
    """Scan raw blob bytes; return ``{(kind, name): (count, first_line)}``.

    Key patterns run on every text view. Env secret values are matched as their
    encoded byte sequences (UTF-8/16/32), so non-ASCII and multi-line values
    match in every encoding."""
    out: Dict[Tuple[str, str], Tuple[int, int]] = {}

    def note(key: Tuple[str, str], count: int, line: int) -> None:
        if count > out.get(key, (0, 0))[0]:
            out[key] = (count, line)

    for text in _binary_text_views(data):
        for name, pat in SECRET_PATTERNS:
            hits = list(pat.finditer(text))
            if hits:
                note(("pattern", name), len(hits), text.count("\n", 0, hits[0].start()) + 1)
    for var, val in _env_secret_values(env):
        for enc in _ENV_VALUE_ENCODINGS:
            needle = val.encode(enc)
            n = data.count(needle)
            if n:
                line = data.count("\n".encode(enc), 0, data.find(needle)) + 1
                note(("env_value", var), n, line)
    return out


def _read_blobs(repo: str, senv: Dict[str, str], shas: Iterable[str]) -> Dict[str, bytes]:
    wanted = sorted(set(shas))
    if not wanted:
        return {}
    raw = _git_bytes(["cat-file", "--batch"], cwd=repo, env=senv,
                     input_bytes=("\n".join(wanted) + "\n").encode())
    blobs: Dict[str, bytes] = {}
    pos = 0
    while pos < len(raw):
        nl = raw.index(b"\n", pos)
        header = raw[pos:nl].decode("ascii", "replace").split()
        pos = nl + 1
        if len(header) == 3:
            size = int(header[2])
            blobs[header[0]] = raw[pos:pos + size]
            pos += size + 1
    missing = [s for s in wanted if s not in blobs]
    if missing:
        raise HandoffError(f"could not read {len(missing)} blob(s) for the secret scan")
    return blobs


_BLOB_MODES = ("100644", "100755", "120000")


def _scan_changes(repo: str, senv: Dict[str, str], base_entries: Dict[str, Tuple[str, str]],
                  new_entries: Dict[str, Tuple[str, str]], files: List[str],
                  env: Optional[Dict[str, str]]) -> List[Dict[str, Any]]:
    """Scan every changed path: its name (when new) and its full new content in
    every encoding view, whatever git would call the file. A content finding
    counts only when the new blob has more of it than the base blob at the same
    path, so text that was already committed never blocks."""
    findings: List[Dict[str, Any]] = []
    for path in files:
        if path in base_entries:
            continue
        seen = set()
        for f in scan_secrets(path, env):
            if (f["kind"], f["name"]) not in seen:
                seen.add((f["kind"], f["name"]))
                findings.append({"kind": f["kind"], "name": f["name"], "line": 0,
                                 "where": "path", "path": path})
    want: List[str] = []
    for path in files:
        new, old = new_entries.get(path), base_entries.get(path)
        if new and new[0] in _BLOB_MODES:
            want.append(new[1])
            if old and old[0] in _BLOB_MODES:
                want.append(old[1])
    blobs = _read_blobs(repo, senv, want)
    for path in files:
        new, old = new_entries.get(path), base_entries.get(path)
        if not new or new[0] not in _BLOB_MODES:
            continue
        hits = _scan_bytes(blobs[new[1]], env)
        before = _scan_bytes(blobs[old[1]], env) if old and old[0] in _BLOB_MODES else {}
        for (kind, name), (count, line) in sorted(hits.items()):
            if count > before.get((kind, name), (0, 0))[0]:
                findings.append({"kind": kind, "name": name, "line": line,
                                 "where": "content", "path": path})
    return findings


def _tree_entries(repo: str, senv: Dict[str, str], tree: str) -> Dict[str, Tuple[str, str]]:
    out = _git_bytes(["ls-tree", "-r", "-z", "--full-tree", tree], cwd=repo, env=senv)
    entries: Dict[str, Tuple[str, str]] = {}
    for rec in out.split(b"\0"):
        if not rec:
            continue
        meta, path = rec.split(b"\t", 1)
        mode, _typ, sha = meta.decode("ascii").split()
        entries[os.fsdecode(path)] = (mode, sha)
    return entries


def _hash_workspace(git_dir: str, workspace: str, paths: List[str],
                    senv: Dict[str, str]) -> Dict[str, Tuple[str, str]]:
    """Hash workspace files into the staging object dir with ``--no-filters``:
    no clean filter, attribute or hook ever sees workspace content, and nothing
    is written outside the staging dir. Symlinks are stored as their target
    (never followed)."""
    entries: Dict[str, Tuple[str, str]] = {}
    batch: List[Tuple[str, str, str]] = []  # (rel, abs, mode)
    gd = [f"--git-dir={git_dir}"]
    for rel in paths:
        ab = os.path.join(workspace, rel)
        try:
            st = os.lstat(ab)
        except OSError:
            continue  # deleted
        if stat.S_ISLNK(st.st_mode):
            sha = _git_bytes([*gd, "hash-object", "-w", "--no-filters", "--stdin"], env=senv,
                             input_bytes=os.fsencode(os.readlink(ab))).decode().strip()
            entries[rel] = ("120000", sha)
        elif stat.S_ISREG(st.st_mode):
            mode = "100755" if st.st_mode & 0o100 else "100644"
            if "\n" in ab or "\r" in ab:  # --stdin-paths is line based
                with open(ab, "rb") as fh:
                    data = fh.read()
                sha = _git_bytes([*gd, "hash-object", "-w", "--no-filters", "--stdin"],
                                 env=senv, input_bytes=data).decode().strip()
                entries[rel] = (mode, sha)
            else:
                batch.append((rel, ab, mode))
    if batch:
        out = _git_bytes([*gd, "hash-object", "-w", "--no-filters", "--stdin-paths"], env=senv,
                         input_bytes=b"".join(os.fsencode(ab) + b"\n" for _, ab, _ in batch))
        shas = out.decode().split()
        if len(shas) != len(batch):
            raise HandoffError("hash-object returned an unexpected number of objects")
        for (rel, _, mode), sha in zip(batch, shas):
            entries[rel] = (mode, sha)
    return entries


def _build_tree(git_dir: str, workspace: str, base: str, repo: str,
                senv: Dict[str, str]) -> Dict[str, Any]:
    """Build the hand-off tree in a temp index without ``git add`` (so no clean
    filter or attribute runs). Returns tree, files, excluded, warnings and the
    base/new path->(mode, sha) maps used by the secret scan."""
    tmpd = tempfile.mkdtemp(prefix="awos-handoff-index-")
    env = dict(senv, GIT_INDEX_FILE=os.path.join(tmpd, "index"))
    wt = [f"--git-dir={git_dir}", f"--work-tree={workspace}"]
    warnings: List[str] = []
    try:
        base_entries = _tree_entries(repo, senv, base)
        # The base index is only used to list tracked + untracked, non-ignored paths.
        _git([*wt, "read-tree", base], env=env)
        listed = _git_bytes([*wt, "ls-files", "-z", "--cached", "--others",
                             "--exclude-standard"], cwd=workspace, env=env)
        paths = sorted({os.fsdecode(p) for p in listed.split(b"\0") if p})
        nested = [p for p in paths if p.endswith("/")]
        if nested:
            warnings.append("nested repositories not handed off: " + ", ".join(nested))
        paths = [p for p in paths if not p.endswith("/")]
        new_entries = _hash_workspace(git_dir, workspace, paths, senv)
        for p, (mode, sha) in base_entries.items():  # base gitlinks stay while present
            if mode == "160000" and os.path.isdir(os.path.join(workspace, p)):
                new_entries[p] = (mode, sha)
        changed = sorted(p for p in set(base_entries) | set(new_entries)
                         if base_entries.get(p) != new_entries.get(p))
        excluded = [p for p in changed if _is_excluded(p)]
        for p in excluded:
            if p in base_entries:
                new_entries[p] = base_entries[p]
            else:
                new_entries.pop(p, None)
        files = [p for p in changed if p not in excluded]
        os.unlink(env["GIT_INDEX_FILE"])
        index_info = b"".join(f"{m} {sha}\t".encode() + os.fsencode(p) + b"\0"
                              for p, (m, sha) in sorted(new_entries.items()))
        _git_bytes([*wt, "update-index", "-z", "--add", "--index-info"], env=env,
                   input_bytes=index_info)
        tree = _git([*wt, "write-tree"], env=env).strip()
        return {"tree": tree, "files": files, "excluded": excluded, "warnings": warnings,
                "base_entries": base_entries, "new_entries": new_entries}
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)


def _dirty_files(repo: str) -> List[str]:
    out = _git(["--no-optional-locks", "status", "--porcelain", "-z", "--untracked-files=all"],
               cwd=repo, check=False)
    files = []
    entries = out.split("\0")
    i = 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if len(entry) > 3:
            files.append(entry[3:])
            # rename/copy: the original path follows as its own unprefixed entry
            if entry[0] in "RC" and i < len(entries):
                if entries[i]:
                    files.append(entries[i])
                i += 1
    return files


# --------------------------------------------------------------------------- #
# report
# --------------------------------------------------------------------------- #
def _render_report(job: Dict[str, Any], outcome: Dict[str, Any], info: Dict[str, Any],
                   diffstat: str, review_base: str) -> str:
    goal = str(job.get("goal", ""))
    fence = "````" if "```" in goal else "```"
    tests = outcome.get("tests") or {}
    if isinstance(tests, dict) and tests:
        t_line = f"{tests.get('status', 'unknown')}"
        if "passed" in tests or "failed" in tests:
            t_line += f" — passed {tests.get('passed', '?')}, failed {tests.get('failed', '?')}"
        if tests.get("command"):
            t_line += f" (`{tests['command']}`)"
    else:
        t_line = str(tests) if tests else "not reported"
    cost = outcome.get("cost_usd")
    budget = job.get("budget_usd")
    cost_line = (f"${float(cost):.4f}" if cost is not None else "not reported")
    if budget is not None:
        cost_line += f" of ${float(budget):.2f} budget"
    minutes = info.get("minutes")
    branch = info.get("branch")
    lines = [
        f"# AWOS job {job.get('id', '?')}",
        "",
        "## Goal (verbatim)",
        "",
        fence,
        goal,
        fence,
        "",
        f"- **Status:** {info['status']}",
        f"- **Branch:** `{branch}`" if branch else "- **Branch:** none created",
        f"- **Base commit:** `{info.get('base_commit') or '?'}`",
        f"- **Tests:** {t_line}",
        f"- **Cost:** {cost_line}",
        f"- **Minutes:** {minutes if minutes is not None else 'not reported'}",
        "",
    ]
    if outcome.get("summary"):
        lines += ["## Summary", "", str(outcome["summary"]), ""]
    lines += ["## What changed", ""]
    if info.get("files"):
        lines += ["```", diffstat.rstrip() or "(no stat)", "```", ""]
    else:
        lines += ["No files changed.", ""]
    if info.get("excluded"):
        lines += ["Excluded from hand-off (never committed): "
                  + ", ".join(f"`{p}`" for p in info["excluded"]), ""]
    if info.get("warnings"):
        lines += ["## Warnings", ""] + [f"- {w}" for w in info["warnings"]] + [""]
    if branch:
        lines += [
            "## How to review",
            "",
            "```sh",
            f"git diff {review_base}...{branch}",
            f"git log --stat {review_base}..{branch}",
            f"git merge --no-ff {branch}      # accept",
            f"git branch -D {branch}          # discard",
            "```",
            "",
            "Nothing was pushed. The branch exists only in your local repo.",
            "",
        ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #
def finish(job: Dict[str, Any], workspace: str, outcome: Dict[str, Any], *,
           job_dir: Optional[str] = None, env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Hand a finished job off as a local branch + report. Never pushes.

    ``env`` overrides the environment used for secret scanning (tests).
    """
    outcome = dict(outcome or {})
    job_id = str(job.get("id") or "")
    if not job_id:
        raise HandoffError("job has no id")
    if not JOB_ID_RE.fullmatch(job_id) or len(job_id) > MAX_JOB_ID_LEN:
        raise HandoffError(f"job id must match [A-Za-z0-9_-]+ (max {MAX_JOB_ID_LEN} chars)")
    id8 = job_id.replace("-", "")[:8]
    if not id8:
        raise HandoffError("job id needs at least one character other than '-'")
    branch = f"{BRANCH_PREFIX}{id8}"
    jdir = Path(job_dir) if job_dir else host_root() / "jobs" / job_id
    jdir.mkdir(parents=True, exist_ok=True)
    report_path = jdir / "report.md"
    handoff_path = jdir / "handoff.json"

    repo = str(Path(job.get("repo_path") or "").expanduser().resolve())
    workspace = str(Path(workspace).expanduser().resolve())
    info: Dict[str, Any] = {
        "job_id": job_id, "status": "error", "branch": None, "base_commit": None,
        "commit": None, "files": [], "excluded": [], "warnings": [],
        "report_path": str(report_path), "handoff_path": str(handoff_path),
        "secret_scan": {"diff": [], "report": []}, "created_at": _now_iso(),
        "minutes": _minutes(job, outcome),
    }
    diffstat = ""
    review_base = "main"
    staging_dir: Optional[str] = None
    try:
        git_dir = _git(["rev-parse", "--absolute-git-dir"], cwd=repo).strip()
        base_ref = job.get("base_commit") or outcome.get("base_commit")
        if not base_ref:
            base_ref = "HEAD"
            info["warnings"].append("no base_commit recorded; used the repo's current HEAD")
        base = _git(["rev-parse", "--verify", f"{base_ref}^{{commit}}"], cwd=repo).strip()
        info["base_commit"] = base
        cur = _git(["symbolic-ref", "--short", "-q", "HEAD"], cwd=repo, check=False).strip()
        review_base = cur or base[:12]
        base_tree = _git(["rev-parse", f"{base}^{{tree}}"], cwd=repo).strip()

        repo_objects = _git(["rev-parse", "--git-path", "objects"], cwd=repo).strip()
        repo_objects = str((Path(repo) / repo_objects).resolve())
        staging_dir = tempfile.mkdtemp(prefix="awos-handoff-objects-")
        senv = _staging_env(repo_objects, staging_dir)

        built = _build_tree(git_dir, workspace, base, repo, senv)
        tree, files = built["tree"], built["files"]
        info["files"], info["excluded"] = files, built["excluded"]
        info["warnings"].extend(built["warnings"])

        if tree == base_tree:
            info["status"] = "no_changes"
        else:
            hits = _scan_changes(repo, senv, built["base_entries"], built["new_entries"],
                                 files, env)
            info["secret_scan"]["diff"] = hits
            if hits:
                info["status"] = "blocked_secret"
                names = sorted({h["name"] for h in hits})
                info["warnings"].append(
                    "credential guard: diff contains secret-like content ("
                    + ", ".join(names) + "); branch NOT created")
            else:
                diffstat = _git(["diff-tree", "--stat", "--no-color", "--no-ext-diff",
                                 "--no-textconv", base_tree, tree],
                                cwd=repo, env=senv)
                msg = (f"awos: {str(job.get('goal', '')).strip().splitlines()[0][:72] if str(job.get('goal', '')).strip() else 'job'}"
                       f"\n\nAWOS-Job: {job_id}\n")
                msg = redact_secrets(msg, env)
                ref = f"refs/heads/{branch}"
                existing = _git(["rev-parse", "-q", "--verify", ref], cwd=repo, check=False).strip()
                if existing:
                    ex_tree = _git(["rev-parse", f"{existing}^{{tree}}"], cwd=repo).strip()
                    ex_parent = _git(["rev-parse", f"{existing}^"], cwd=repo, check=False).strip()
                    if ex_tree == tree and ex_parent == base:
                        commit = existing
                        info["warnings"].append("branch already existed with identical content; reused")
                    else:
                        raise HandoffError(f"branch {branch} already exists with different content; "
                                           "refusing to overwrite")
                else:
                    commit = _git(["commit-tree", tree, "-p", base], cwd=repo,
                                  env=dict(senv, **_GIT_IDENTITY), input_text=msg).strip()
                    _promote_objects(repo, senv, commit, base)
                    _git(["update-ref", "-m", "awos handoff", ref, commit, ""], cwd=repo)
                info.update(status="handed_off", branch=branch, commit=commit)
                overlap = sorted(set(_dirty_files(repo)) & set(files))
                if overlap:
                    info["warnings"].append(
                        "your checkout has uncommitted changes in handed-off files ("
                        + ", ".join(overlap) + "); expect merge conflicts")
    except HandoffError as exc:
        info["status"] = "error"
        info["warnings"].append(str(exc))
    except Exception as exc:  # anything unexpected still yields a report
        info["status"] = "error"
        info["warnings"].append(f"internal error: {type(exc).__name__}: {exc}"[:400])
    finally:
        if staging_dir:
            shutil.rmtree(staging_dir, ignore_errors=True)

    report = _render_report(job, outcome, info, diffstat, review_base)
    rep_hits = scan_secrets(report, env)
    if rep_hits:
        info["secret_scan"]["report"] = rep_hits
        info["warnings"].append("credential guard: report contained secret-like text; redacted")
    report = redact_secrets(report, env)  # always, whatever the scan found
    report_path.write_text(report, encoding="utf-8")

    if info["status"] == "handed_off":
        info["summary"] = f"{len(info['files'])} file(s) on {branch}; review: git diff {review_base}...{branch}"
    elif info["status"] == "no_changes":
        info["summary"] = "no changes to hand off"
    elif info["status"] == "blocked_secret":
        info["summary"] = "hand-off blocked by credential guard"
    else:
        info["summary"] = "hand-off failed: " + (info["warnings"][-1] if info["warnings"] else "unknown")
    payload = json.dumps(_redact_obj(info, env), indent=2, sort_keys=True)
    payload = redact_secrets(payload, env)  # belt and braces
    handoff_path.write_text(payload + "\n", encoding="utf-8")
    return info


# --------------------------------------------------------------------------- #
# live-proof demo
# --------------------------------------------------------------------------- #
def _demo() -> int:
    import uuid

    tmp = Path(tempfile.mkdtemp(prefix="awos-handoff-demo-"))
    repo, ws, host = tmp / "repo", tmp / "workspace", tmp / "host"
    repo.mkdir()
    _git(["init", "-q", "-b", "main"], cwd=str(repo))
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    (repo / "README.md").write_text("demo\n")
    _git(["add", "-A"], cwd=str(repo))
    _git(["commit", "-q", "-m", "init"], cwd=str(repo), env=_GIT_IDENTITY)
    base = _git(["rev-parse", "HEAD"], cwd=str(repo)).strip()
    _git(["remote", "add", "origin", "https://example.invalid/never.git"], cwd=str(repo))

    shutil.copytree(repo, ws, ignore=shutil.ignore_patterns(".git"))
    (ws / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (ws / "test_calc.py").write_text("from calc import add\n\ndef test_add():\n    assert add(2, 2) == 4\n")
    (ws / ".env").write_text("OPENROUTER_API_KEY=dummy-not-real\n")

    status_before = _git(["status", "--porcelain"], cwd=str(repo))
    head_before = _git(["rev-parse", "HEAD"], cwd=str(repo)).strip()
    job = {"id": uuid.uuid4().hex, "goal": "Fix add() in calc.py: it subtracts instead of adding.",
           "repo_path": str(repo), "kind": "coding", "budget_usd": 0.5, "privacy": "local_only",
           "created_at": _now_iso(), "state": "running", "attempts": 1, "result": None,
           "base_commit": base}
    os.environ["AWOS_HOST_DIR"] = str(host)
    res = finish(job, str(ws), {"tests": {"status": "passed", "passed": 1, "failed": 0,
                                          "command": "pytest -q"},
                                "cost_usd": 0.0123, "minutes": 3.2,
                                "summary": "Changed `-` to `+` and added a test."})
    print("[handoff] status:", res["status"], "| branch:", res["branch"], "| commit:", res["commit"][:12])
    print("[handoff] excluded:", res["excluded"])
    print("[handoff] branches:", _git(["branch", "--list"], cwd=str(repo)).strip().replace("\n", " "))
    print("[handoff] remotes untouched:",
          _git(["for-each-ref", "refs/remotes"], cwd=str(repo)).strip() == "")
    print("[handoff] checkout unchanged:",
          _git(["status", "--porcelain"], cwd=str(repo)) == status_before
          and _git(["rev-parse", "HEAD"], cwd=str(repo)).strip() == head_before)
    print("----- git diff main...%s -----" % res["branch"])
    print(_git(["diff", f"main...{res['branch']}"], cwd=str(repo)))
    print("----- report.md -----")
    print(Path(res["report_path"]).read_text())
    print("[handoff] demo dir:", tmp)
    return 0


if __name__ == "__main__":
    if "--demo" in sys.argv:
        sys.exit(_demo())
    print("usage: python -m scaffold.agent.host.handoff --demo")
    sys.exit(2)
