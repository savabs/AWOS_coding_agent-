"""
acceptance.py — acceptance tests from the issue gate "done" (ablation C).

See docs/specs/ablation_acceptance.md. Behind AWOS_ACCEPTANCE (default 0).

  generate_acceptance_tests(goal_text, project_root, exploration, client=, model=)
      ONE utility_chat call (reasoning off) -> a small pytest file written
      from the issue's stated behaviour and repro examples only
  build_suite(...)
      generate, then run on the START state and keep only the tests that FAIL
      there (must-fail-first). Gate inactive when none survive.
  run_acceptance(project_root, suite, sandbox)
      (all_passed, summary, failing_output) for the kept tests

Mode 2 (AWOS_ACCEPTANCE=2, ablation C2, docs/specs/ablation_acceptance_v2.md),
the fail-safe gate:
  arbitrate(...)            one utility call: each failing kept test is
                            WRONG_TEST (dropped) or CODE_INCOMPLETE (kept);
                            an unparseable reply is all WRONG_TEST
  snapshot_workspace / restore_workspace
                            the visible-green state before the one bounded
                            repair, put back when the repair does not end
                            green and acceptance-passing

Where the tests live: only in memory between checks. Each run writes the file
into a hidden dir under the project (.awos_acceptance_<hex>/), runs pytest on
it through the same TestRunner/sandbox path the visible tests use (the
seatbelt sandbox denies reads outside the workspace, so a temp dir elsewhere
would not be readable), and deletes the dir at once. The agent never runs at
the same time, so the file never appears to its tools, in files_changed, in
git status or in the patch.

Filter policy (start state):
  FAILED (assertion, ImportError/AttributeError/TypeError for a name or
  argument the issue says should exist)  -> keep: the failure is informative
  FAILED with NameError / SyntaxError (a bug in the test itself)  -> drop
  PASSED, ERROR (fixture/setup), SKIPPED, XFAIL  -> drop
  collection error of the whole file (SyntaxError, module-level import)
  or a crashed/timed-out run  -> drop all (gate inactive)
The prompt asks for project imports INSIDE each test so a missing name fails
only that test instead of erroring the whole module at collection.
"""

from __future__ import annotations

import ast
import json
import logging
import os
import re
import shutil
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

ACCEPTANCE_ENV = "AWOS_ACCEPTANCE"
HIDDEN_DIR_PREFIX = ".awos_acceptance_"
TEST_FILENAME = "test_awos_acceptance.py"
CONTEXT_BUDGET_TOKENS = 6000
MAX_REPLY_TOKENS = 3000
RUN_TIMEOUT_SEC = 120
FEEDBACK_CHARS = 3000
REPAIR_TURNS_ENV = "AWOS_ACCEPTANCE_REPAIR_TURNS"
DEFAULT_REPAIR_TURNS = 10
ARBITRATION_MAX_TOKENS = 1024
ARBITRATION_DIFF_CHARS = 6000
ARBITRATION_TEST_CHARS = 2500
WRONG_TEST = "WRONG_TEST"
CODE_INCOMPLETE = "CODE_INCOMPLETE"

#: A failure whose reason names one of these is a bug in the test, not a
#: missing behaviour.
TEST_BUG_MARKERS = ("NameError", "SyntaxError", "IndentationError", "fixture '")

SYSTEM_PROMPT = """You write acceptance tests for a software issue BEFORE it is fixed.

Rules:
- Reply with ONE ```python fenced block holding a complete pytest file, nothing else.
- Test ONLY behaviour the issue states or shows (its reproduction snippets and
  expected outputs). Do not test anything the issue does not mention.
- Write 2 to 6 small test functions with plain `assert`s.
- Import the project's names INSIDE each test function (not at module top), using
  the same public import paths the existing tests use.
- No network, no files outside pytest's tmp_path, no sleeps; each test runs in
  well under a second.
- Do not assert exact exception messages or exact reprs unless the issue states them.
- Do not use fixtures other than tmp_path, and no conftest."""


def acceptance_mode() -> str:
    """
    "0" off, "1" ablation C (v1 gate), "2" ablation C2 (fail-safe gate).

    Unset means "2": the fail-safe gate was adopted by ablation C2
    (docs/specs/ablation_acceptance_v2.md). Under pytest, unset means "0" so
    unit tests never make a model call unless they set the variable.
    """
    default = "0" if "PYTEST_CURRENT_TEST" in os.environ else "2"
    raw = os.getenv(ACCEPTANCE_ENV, default).strip().lower()
    if raw == "2":
        return "2"
    return "1" if raw in ("1", "on", "true", "yes") else "0"


def acceptance_enabled() -> bool:
    return acceptance_mode() != "0"


def repair_turns() -> int:
    """Turn cap of the one acceptance-triggered repair attempt (mode 2)."""
    try:
        return max(1, int(os.getenv(REPAIR_TURNS_ENV, str(DEFAULT_REPAIR_TURNS))))
    except ValueError:
        return DEFAULT_REPAIR_TURNS


@dataclass
class AcceptanceSuite:
    source: str = ""
    generated: int = 0              # test functions found on the start run
    kept: list = field(default_factory=list)  # test names (node id after "::")
    dropped: dict = field(default_factory=dict)  # reason -> count
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    error: str = ""
    checks: int = 0

    @property
    def active(self) -> bool:
        return bool(self.kept) and bool(self.source)


# ── generation ───────────────────────────────────────────────────────────────

def _extract_code(reply: str) -> str:
    blocks = re.findall(r"```(?:python|py)?[ \t]*\n(.*?)```", reply or "", re.DOTALL)
    if blocks:
        return max(blocks, key=len).strip() + "\n"
    text = (reply or "").strip()
    return text + "\n" if "def test_" in text else ""


def build_messages(goal_text: str, context_text: str) -> list:
    user = (
        f"# Issue\n\n{goal_text.strip()}\n\n# Repository (current, unfixed)\n\n{context_text}\n\n"
        "Write the pytest acceptance tests for this issue. They must fail on the "
        "current code and pass once the issue is fixed."
    )
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


def generate_acceptance_tests(goal_text: str, project_root: str,
                              exploration: Optional[dict] = None, *, client: Any,
                              model: str, tracker: Any = None) -> dict:
    """
    One utility call, reasoning off. Returns {"source", "cost_usd",
    "input_tokens", "output_tokens", "error"}. Never raises.
    """
    try:
        from . import one_shot
        from .providers import REASONING_OFF, utility_chat
    except ImportError:
        import one_shot
        from providers import REASONING_OFF, utility_chat
    out = {"source": "", "cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0, "error": ""}
    try:
        ctx = one_shot.build_context(project_root, goal_text, exploration,
                                     budget=CONTEXT_BUDGET_TOKENS)
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"context build failed: {exc}"
        return out
    tap = one_shot._UsageTap(client)
    try:
        text, info = utility_chat(
            tap, "acceptance", model, build_messages(goal_text, ctx.text),
            max_tokens=MAX_REPLY_TOKENS, tracker=tracker, request_type="acceptance",
            temperature=0, send_reasoning=one_shot._sends_reasoning(client),
            reasoning=dict(REASONING_OFF), retry=False,
        )
        out["cost_usd"] = float(info.get("cost_usd") or 0.0)
        out["source"] = _extract_code(text)
        if not out["source"]:
            out["error"] = "reply had no python block"
    except Exception as exc:  # noqa: BLE001 — empty/cut-off reply or transport
        out["cost_usd"] = float(getattr(exc, "cost_usd", 0.0) or 0.0)
        out["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
    out["input_tokens"], out["output_tokens"] = tap.input_tokens, tap.output_tokens
    return out


# ── running ──────────────────────────────────────────────────────────────────

_OUTCOME_LINE = re.compile(r"^(PASSED|FAILED|ERROR|XFAIL|XPASS)\s+(\S+)(?:\s+-\s+(.*))?$",
                           re.MULTILINE)


def parse_outcomes(output: str) -> tuple[dict, bool]:
    """
    pytest -rA short summary -> ({test_name: (status, reason)}, collection_error).
    A test name is the node id after the file's "::".
    """
    outcomes: dict = {}
    collection_error = False
    for status, nodeid, reason in _OUTCOME_LINE.findall(output or ""):
        if "::" not in nodeid:
            if status == "ERROR":
                collection_error = True
            continue
        name = nodeid.split("::", 1)[1]
        outcomes[name] = (status, reason or "")
    if "error during collection" in (output or "") or "errors during collection" in (output or ""):
        collection_error = True
    # The short summary's reason is cut to the terminal width: take each
    # failed test's error ("E   ..." lines) from its FAILURES section instead.
    for name, body in _failure_sections(output or "").items():
        if name in outcomes:
            errors = [ln.strip()[1:].strip() for ln in body.splitlines()
                      if ln.startswith("E ")]
            outcomes[name] = (outcomes[name][0], " | ".join(errors)[:500] or outcomes[name][1])
    return outcomes, collection_error


_SECTION_HEAD = re.compile(r"^_{3,} (\S+) _{3,}$", re.MULTILINE)


def _failure_sections(output: str) -> dict:
    heads = list(_SECTION_HEAD.finditer(output))
    out = {}
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(output)
        body = output[m.end():end]
        body = re.split(r"^={3,}", body, maxsplit=1, flags=re.MULTILINE)[0]
        out[m.group(1)] = body
    return out


def run_tests_once(project_root: str, source: str, names: Optional[list] = None,
                   sandbox: Any = None) -> dict:
    """
    Write `source` into a hidden dir under the project, run it (only `names`
    when given) with the visible tests' pytest args and sandbox, delete the
    dir. Returns {"outcomes", "collection_error", "result", "skipped"}.
    """
    try:
        from .test_runner import _NO_CONFIG_ARGS, _SAFETY_ENV_VAR, TestConfig, TestRunner
    except ImportError:
        from test_runner import _NO_CONFIG_ARGS, _SAFETY_ENV_VAR, TestConfig, TestRunner
    out = {"outcomes": {}, "collection_error": False, "result": None, "skipped": ""}
    if os.environ.get(_SAFETY_ENV_VAR) != "1":
        out["skipped"] = f"{_SAFETY_ENV_VAR} not set"
        return out
    runner = TestRunner(project_root=project_root, timeout_sec=RUN_TIMEOUT_SEC, sandbox=sandbox)
    cfg = runner.detect()
    if cfg is None or cfg.runner != "pytest":
        out["skipped"] = "not a pytest project"
        return out
    hidden = Path(project_root) / f"{HIDDEN_DIR_PREFIX}{uuid.uuid4().hex[:8]}"
    rel = f"{hidden.name}/{TEST_FILENAME}"
    try:
        hidden.mkdir()
        (hidden / TEST_FILENAME).write_text(source, encoding="utf-8")
        command = list(cfg.command) + ["-p", "no:cacheprovider", "--color=no", "-rA"]
        if not runner._has_pytest_config():
            command += _NO_CONFIG_ARGS
        command += [f"{rel}::{n}" for n in names] if names else [rel]
        result = runner._execute(TestConfig("pytest", command, RUN_TIMEOUT_SEC))
    finally:
        shutil.rmtree(hidden, ignore_errors=True)
    out["result"] = result
    out["outcomes"], out["collection_error"] = parse_outcomes(result.raw_output)
    return out


def remove_leftovers(project_root: str) -> None:
    """Delete any hidden acceptance dir left behind (a killed run)."""
    try:
        for p in Path(project_root).glob(HIDDEN_DIR_PREFIX + "*"):
            shutil.rmtree(p, ignore_errors=True)
    except OSError:
        pass


def filter_start_failing(outcomes: dict, collection_error: bool) -> tuple[list, dict]:
    """Must-fail-first: the kept test names and a reason -> count of the dropped."""
    dropped: dict = {}
    if collection_error:
        dropped["collection error"] = max(1, len(outcomes))
        return [], dropped
    kept = []
    for name, (status, reason) in outcomes.items():
        if status != "FAILED":
            key = "passed on start" if status == "PASSED" else status.lower()
        elif any(m in reason for m in TEST_BUG_MARKERS):
            key = "test bug"
        else:
            kept.append(name)
            continue
        dropped[key] = dropped.get(key, 0) + 1
    return kept, dropped


def build_suite(goal_text: str, project_root: str, exploration: Optional[dict] = None, *,
                client: Any, model: str, sandbox: Any = None,
                tracker: Any = None) -> AcceptanceSuite:
    """Generate, then keep only the tests that fail on the start state. Never raises."""
    suite = AcceptanceSuite()
    gen = generate_acceptance_tests(goal_text, project_root, exploration, client=client,
                                    model=model, tracker=tracker)
    suite.cost_usd = gen["cost_usd"]
    suite.input_tokens, suite.output_tokens = gen["input_tokens"], gen["output_tokens"]
    if gen["error"] or not gen["source"]:
        suite.error = gen["error"] or "no tests generated"
        return suite
    try:
        run = run_tests_once(project_root, gen["source"], sandbox=sandbox)
    except Exception as exc:  # noqa: BLE001
        suite.error = f"start run failed: {type(exc).__name__}: {exc}"
        return suite
    if run["skipped"]:
        suite.error = run["skipped"]
        return suite
    result = run["result"]
    if result is not None and (result.timed_out or getattr(result, "infra_error", False)):
        suite.error = "start run timed out" if result.timed_out else "start run could not run"
        return suite
    suite.generated = len(run["outcomes"])
    suite.kept, suite.dropped = filter_start_failing(run["outcomes"], run["collection_error"])
    if suite.kept:
        suite.source = gen["source"]
    return suite


def run_acceptance(project_root: str, suite: AcceptanceSuite,
                   sandbox: Any = None) -> tuple[bool, str, str]:
    """
    (all_passed, summary, failing_output) for the kept tests. An inactive
    suite, or a run that could not start (no evidence), passes.
    """
    ok, summary, feedback, _ = run_acceptance_detail(project_root, suite, sandbox)
    return ok, summary, feedback


def run_acceptance_detail(project_root: str, suite: AcceptanceSuite,
                          sandbox: Any = None) -> tuple[bool, str, str, dict]:
    """run_acceptance plus {failing test name: its failure text} (mode 2)."""
    if suite is None or not suite.active:
        return True, "inactive", "", {}
    suite.checks += 1
    k = len(suite.kept)
    try:
        run = run_tests_once(project_root, suite.source, names=suite.kept, sandbox=sandbox)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[ACCEPTANCE] check could not run: %s", exc)
        return True, f"{k} kept, check could not run", "", {}
    result = run["result"]
    if run["skipped"] or (result is not None and getattr(result, "infra_error", False)):
        print(f"[ACCEPTANCE] check: {k} kept, could not run (no evidence; not blocking)")
        return True, f"{k} kept, check could not run", "", {}
    passed = sum(1 for n in suite.kept if run["outcomes"].get(n, ("",))[0] == "PASSED")
    print(f"[ACCEPTANCE] check: {k} kept, {passed} passed")
    raw = getattr(result, "raw_output", "") or ""
    if passed == k:
        return True, f"{k} kept, {k} passed", "", {}
    if result is not None and result.timed_out:
        raw = f"acceptance tests timed out after {RUN_TIMEOUT_SEC}s"
    failing = raw if len(raw) <= FEEDBACK_CHARS else "...\n" + raw[-FEEDBACK_CHARS:]
    feedback = (f"Acceptance tests written from the issue still fail ({k - passed} of {k}). "
                "They are not in the project and run automatically after you finish; fix "
                "the code, not the tests (they may be imperfect: trust the issue text).\n"
                f"Kept tests: {', '.join(suite.kept)}\n```python\n"
                f"{suite.source[:FEEDBACK_CHARS]}```\n\nOutput:\n{failing}")
    sections = _failure_sections(raw)
    failing_detail = {}
    for n in suite.kept:
        status, reason = run["outcomes"].get(n, ("MISSING", "no result (timed out or not run)"))
        if status == "PASSED":
            continue
        text = sections.get(n) or f"{status}: {reason}"
        failing_detail[n] = text[-FEEDBACK_CHARS:]
    return False, f"{k - passed} failing of {k}", feedback, failing_detail


# ── mode 2: the fail-safe gate (ablation C2, docs/specs/ablation_acceptance_v2.md) ──

ARBITRATION_PROMPT = """You judge acceptance tests that were written from a software issue.

The project's own visible test suite PASSES on the current code. Some acceptance
tests, written from the issue text before the fix, still FAIL. For each failing
test decide which is wrong:

- WRONG_TEST: the test expects behaviour the issue does not require, guesses an
  exact output/format/message the issue does not state, or contradicts the
  visible tests or the issue's own examples.
- CODE_INCOMPLETE: the current code misses a requirement the issue clearly states.

When unsure, answer WRONG_TEST.
Reply with ONE JSON object only, mapping each failing test name to
"WRONG_TEST" or "CODE_INCOMPLETE", for example:
{"test_a": "WRONG_TEST", "test_b": "CODE_INCOMPLETE"}"""


def test_function_source(source: str, name: str) -> str:
    """The source of test function `name` in `source` (the whole file as fallback)."""
    base = name.split("[", 1)[0]
    try:
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == base:
                seg = ast.get_source_segment(source, node)
                if seg:
                    return seg
    except SyntaxError:
        pass
    return source


test_function_source.__test__ = False  # not a pytest test when imported into one


def workspace_diff(project_root: str, limit: int = ARBITRATION_DIFF_CHARS) -> str:
    """`git diff` of the workspace plus new files, trimmed; "" without git."""
    diff = _git(project_root, "diff", "--no-color", "HEAD") or ""
    new = (_git(project_root, "ls-files", "--others", "--exclude-standard") or "").split()
    new = [n for n in new if not n.startswith(HIDDEN_DIR_PREFIX)]
    if new:
        diff += "\nNew files: " + ", ".join(new[:30])
    return diff if len(diff) <= limit else diff[:limit] + "\n... (diff trimmed)"


def build_arbitration_messages(goal_text: str, suite: AcceptanceSuite, failing: dict,
                               diff: str) -> list:
    parts = [f"# Issue\n\n{goal_text.strip()}", "# Failing acceptance tests"]
    for name, output in failing.items():
        src = test_function_source(suite.source, name)[:ARBITRATION_TEST_CHARS]
        out = (output or "")[-ARBITRATION_TEST_CHARS:]
        parts.append(f"## {name}\n```python\n{src}\n```\nFailure output:\n```\n{out}\n```")
    parts.append(f"# Current diff (the visible tests pass with it)\n```diff\n"
                 f"{diff or '(empty)'}\n```")
    parts.append("Answer with the JSON object for these tests: " + ", ".join(failing))
    return [{"role": "system", "content": ARBITRATION_PROMPT},
            {"role": "user", "content": "\n\n".join(parts)}]


def parse_arbitration(reply: str, names: list) -> tuple[dict, bool]:
    """
    {name: WRONG_TEST | CODE_INCOMPLETE} and whether the reply parsed. Fail-safe:
    an unparseable reply, or a test missing from it or given another label,
    is WRONG_TEST (dropped), so the arbiter can only ever remove work.
    """
    verdicts = {n: WRONG_TEST for n in names}
    m = re.search(r"\{.*\}", reply or "", re.DOTALL)
    if not m:
        return verdicts, False
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return verdicts, False
    if not isinstance(data, dict):
        return verdicts, False
    for n in names:
        if str(data.get(n, "")).strip().upper() == CODE_INCOMPLETE:
            verdicts[n] = CODE_INCOMPLETE
    return verdicts, True


def arbitrate(goal_text: str, suite: AcceptanceSuite, failing: dict, diff: str, *,
              client: Any, model: str, tracker: Any = None) -> dict:
    """
    One utility call (reasoning off) judging each failing kept test. Returns
    {"verdicts", "parsed", "cost_usd", "input_tokens", "output_tokens", "error"}.
    Never raises; any failure is fail-safe (all WRONG_TEST).
    """
    try:
        from . import one_shot
        from .providers import REASONING_OFF, utility_chat
    except ImportError:
        import one_shot
        from providers import REASONING_OFF, utility_chat
    names = list(failing)
    out = {"verdicts": {n: WRONG_TEST for n in names}, "parsed": False, "cost_usd": 0.0,
           "input_tokens": 0, "output_tokens": 0, "error": ""}
    if not names:
        return out
    tap = one_shot._UsageTap(client)
    try:
        text, info = utility_chat(
            tap, "acceptance", model,
            build_arbitration_messages(goal_text, suite, failing, diff),
            max_tokens=ARBITRATION_MAX_TOKENS, tracker=tracker,
            request_type="acceptance_arbitration", temperature=0,
            send_reasoning=one_shot._sends_reasoning(client),
            reasoning=dict(REASONING_OFF), retry=False,
        )
        out["cost_usd"] = float(info.get("cost_usd") or 0.0)
        out["verdicts"], out["parsed"] = parse_arbitration(text, names)
        if not out["parsed"]:
            out["error"] = "reply was not JSON (fail-safe: all WRONG_TEST)"
    except Exception as exc:  # noqa: BLE001
        out["cost_usd"] = float(getattr(exc, "cost_usd", 0.0) or 0.0)
        out["error"] = f"{type(exc).__name__}: {str(exc)[:300]} (fail-safe: all WRONG_TEST)"
    out["input_tokens"], out["output_tokens"] = tap.input_tokens, tap.output_tokens
    return out


def repair_feedback(suite: AcceptanceSuite, failing: dict) -> str:
    """The bounded repair's feedback: only the CODE_INCOMPLETE tests."""
    blocks = []
    for name, output in failing.items():
        src = test_function_source(suite.source, name)[:ARBITRATION_TEST_CHARS]
        blocks.append(f"## {name}\n```python\n{src}\n```\nOutput:\n```\n"
                      f"{(output or '')[-1500:]}\n```")
    return ("The project's visible tests pass, but acceptance tests written from the issue "
            f"still fail ({len(failing)}), and a review judged that the code misses a "
            "requirement the issue states. They are not in the project and run "
            "automatically after you finish; fix the code, not the tests. Keep the "
            "visible tests passing. You have a small turn budget: make the minimal "
            "change.\n\n" + "\n\n".join(blocks))


# ── snapshot / restore of the visible-green workspace (mode 2) ─────────────────

_WALK_SKIP = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
              "node_modules", ".venv", "venv", ".tox", ".awos"}
_WALK_MAX_BYTES = 64 * 1024 * 1024


@dataclass
class WorkspaceSnapshot:
    root: str
    mode: str                                  # "git" or "walk"
    files: dict = field(default_factory=dict)  # rel path -> bytes (None = absent)
    present: set = field(default_factory=set)  # walk mode: every file present


def _git_bytes(root: str, *args: str) -> Optional[bytes]:
    try:
        r = subprocess.run(["git", *args], cwd=root, capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def _git(root: str, *args: str) -> Optional[str]:
    out = _git_bytes(root, *args)
    return None if out is None else out.decode("utf-8", "surrogateescape")


def _git_dirty(root: str) -> Optional[set]:
    """Paths that differ from HEAD (tracked changes and untracked, not ignored)."""
    if _git(root, "rev-parse", "--verify", "HEAD") is None:
        return None
    if (_git(root, "rev-parse", "--show-toplevel") or "").strip() != str(
            Path(root).resolve()):
        return None  # a sub-directory of some other repo: walk instead
    out = _git(root, "status", "--porcelain", "-z", "--untracked-files=all")
    if out is None:
        return None
    paths: set = set()
    entries = out.split("\0")
    i = 0
    while i < len(entries):
        e = entries[i]
        i += 1
        if len(e) < 4:
            continue
        code, path = e[:2], e[3:]
        if "R" in code or "C" in code:
            if i < len(entries) and entries[i]:
                paths.add(entries[i])  # the rename's source
            i += 1
        paths.add(path)
    return {p for p in paths if not p.startswith(HIDDEN_DIR_PREFIX)}


def _read(path: Path) -> Optional[bytes]:
    try:
        return path.read_bytes() if path.is_file() else None
    except OSError:
        return None


def _walk(root: str) -> list:
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _WALK_SKIP
                       and not d.startswith(HIDDEN_DIR_PREFIX)]
        for f in filenames:
            out.append(os.path.relpath(os.path.join(dirpath, f), root))
    return out


def snapshot_workspace(project_root: str) -> WorkspaceSnapshot:
    """
    The workspace as it is now (visible-green), before an acceptance repair.
    In a git repo: the contents of every path that differs from HEAD (None
    for a deleted one). Otherwise: every file's contents (skipping caches).
    """
    dirty = _git_dirty(project_root)
    root = Path(project_root)
    if dirty is not None:
        snap = WorkspaceSnapshot(root=project_root, mode="git")
        for rel in dirty:
            snap.files[rel] = _read(root / rel)
        return snap
    snap = WorkspaceSnapshot(root=project_root, mode="walk")
    total = 0
    for rel in _walk(project_root):
        data = _read(root / rel)
        if data is None:
            continue
        total += len(data)
        if total > _WALK_MAX_BYTES:
            raise RuntimeError("workspace too large to snapshot without git")
        snap.files[rel] = data
        snap.present.add(rel)
    return snap


def _write(path: Path, data: Optional[bytes]) -> None:
    if data is None:
        if path.is_file() or path.is_symlink():
            path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def restore_workspace(snap: WorkspaceSnapshot) -> list:
    """
    Put the workspace back as it was at the snapshot: changed files get their
    snapshot contents back, files created since are deleted, and (git) paths
    that were clean then are reset to HEAD. Returns the paths it touched.
    """
    root = Path(snap.root)
    touched = []
    if snap.mode == "git":
        now = _git_dirty(snap.root) or set()
        for rel in sorted(now | set(snap.files)):
            if rel in snap.files:
                want = snap.files[rel]
            else:
                want = _git_bytes(snap.root, "show", f"HEAD:{rel}")  # None: new file
            if _read(root / rel) != want:
                _write(root / rel, want)
                touched.append(rel)
        return touched
    for rel in _walk(snap.root):
        if rel not in snap.present:
            _write(root / rel, None)
            touched.append(rel)
    for rel, data in snap.files.items():
        if _read(root / rel) != data:
            _write(root / rel, data)
            touched.append(rel)
    return touched
