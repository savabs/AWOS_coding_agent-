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

import logging
import os
import re
import shutil
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


def acceptance_enabled() -> bool:
    return os.getenv(ACCEPTANCE_ENV, "0").strip().lower() in ("1", "on", "true", "yes")


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
    if suite is None or not suite.active:
        return True, "inactive", ""
    suite.checks += 1
    k = len(suite.kept)
    try:
        run = run_tests_once(project_root, suite.source, names=suite.kept, sandbox=sandbox)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[ACCEPTANCE] check could not run: %s", exc)
        return True, f"{k} kept, check could not run", ""
    result = run["result"]
    if run["skipped"] or (result is not None and getattr(result, "infra_error", False)):
        print(f"[ACCEPTANCE] check: {k} kept, could not run (no evidence; not blocking)")
        return True, f"{k} kept, check could not run", ""
    passed = sum(1 for n in suite.kept if run["outcomes"].get(n, ("",))[0] == "PASSED")
    print(f"[ACCEPTANCE] check: {k} kept, {passed} passed")
    raw = getattr(result, "raw_output", "") or ""
    if passed == k:
        return True, f"{k} kept, {k} passed", ""
    if result is not None and result.timed_out:
        raw = f"acceptance tests timed out after {RUN_TIMEOUT_SEC}s"
    failing = raw if len(raw) <= FEEDBACK_CHARS else "...\n" + raw[-FEEDBACK_CHARS:]
    feedback = (f"Acceptance tests written from the issue still fail ({k - passed} of {k}). "
                "They are not in the project and run automatically after you finish; fix "
                "the code, not the tests (they may be imperfect: trust the issue text).\n"
                f"Kept tests: {', '.join(suite.kept)}\n```python\n"
                f"{suite.source[:FEEDBACK_CHARS]}```\n\nOutput:\n{failing}")
    return False, f"{k - passed} failing of {k}", feedback
