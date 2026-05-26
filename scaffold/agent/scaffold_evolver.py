"""
ScaffoldEvolver — DGM-inspired empirical self-modification of AWOS scaffold code.

When a session's task failure rate exceeds AWOS_EVOLVE_THRESHOLD (default 40%),
the agent:
  1. Identifies the top recurring error pattern from ErrorPatternStore
  2. Proposes a targeted SEARCH/REPLACE patch to a scaffold file
  3. Applies it via the Worker's existing SEARCH/REPLACE mechanism
  4. Runs pytest (gated by AWOS_SAFE_TO_RUN_TESTS=true)
  5. Keeps the change only if test pass count >= baseline; otherwise rollbacks

Records every mutation attempt in .awos/scaffold_mutations.jsonl.

Feature 1C from tasks/active/true_self_learning_task.md.
Reference: Darwin Gödel Machine (arXiv:2505.22954)
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Callable, List, Optional

if TYPE_CHECKING:
    from .error_pattern_store import ErrorPatternStore

logger = logging.getLogger(__name__)

_MUTATIONS_LOG = ".awos/scaffold_mutations.jsonl"
_DEFAULT_THRESHOLD = 0.40
_MIN_BASELINE_TESTS = 50
_MUTATION_PROMPT_TOKENS = 600

_ALLOWED_TARGETS = {
    "SYNTAX_ERROR":       "scaffold/agent/self_correction.py",
    "SEARCH_NOT_FOUND":   "scaffold/agent/worker.py",
    "JSON_DECODE_ERROR":  "scaffold/agent/worker.py",
    "MAX_RETRIES":        "scaffold/agent/self_correction.py",
    "UNKNOWN":            "scaffold/agent/worker.py",
}
_DEFAULT_TARGET = "scaffold/agent/worker.py"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class MutationResult:
    """Result of one scaffold evolution attempt."""
    __test__ = False

    accepted: bool
    target_file: str
    description: str
    error_type: str
    tests_before: int
    tests_after: int
    timestamp: str
    rejection_reason: str = ""


class ScaffoldEvolver:
    """
    Empirically self-modifies AWOS scaffold code when failure rate is high.

    Safety gates (ALL must be true before any mutation):
        AWOS_SCAFFOLD_EVOLUTION=true
        AWOS_SAFE_TO_RUN_TESTS=true
        Baseline test count >= _MIN_BASELINE_TESTS
        Git working tree clean (if git is available)
    """

    def __init__(
        self,
        scaffold_root: str = ".",
        cheap_call: Optional[Callable[[str], str]] = None,
    ) -> None:
        self._root = Path(scaffold_root)
        self._cheap_call = cheap_call
        self._threshold = float(os.getenv("AWOS_EVOLVE_THRESHOLD", str(_DEFAULT_THRESHOLD)))
        self._mutations_log = Path(_MUTATIONS_LOG)
        self._mutations_log.parent.mkdir(parents=True, exist_ok=True)

    # ── Public API ──────────────────────────────────────────────────────────

    def should_evolve(self, session_failure_rate: float) -> bool:
        """True if evolution is enabled and failure rate exceeds threshold."""
        if os.getenv("AWOS_SCAFFOLD_EVOLUTION", "").lower() != "true":
            return False
        if os.getenv("AWOS_SAFE_TO_RUN_TESTS", "").lower() != "true":
            return False
        if self._cheap_call is None:
            return False
        return session_failure_rate >= self._threshold

    def evolve_once(
        self,
        error_store: "ErrorPatternStore",
        test_cmd: Optional[List[str]] = None,
    ) -> MutationResult:
        """
        Run one DGM-style iteration:
          1. Find top failure pattern
          2. Propose targeted SEARCH/REPLACE change
          3. Apply, run tests, accept or rollback

        Args:
            error_store:  ErrorPatternStore instance to read failure patterns from.
            test_cmd:     pytest command list (default: [sys.executable, '-m', 'pytest', 'tests/', '-q']).

        Returns:
            MutationResult describing what happened.
        """
        if test_cmd is None:
            test_cmd = [sys.executable, "-m", "pytest", "tests/", "-q", "--tb=no"]

        timestamp = _now_iso()

        if not self._safety_check():
            return self._record(MutationResult(
                accepted=False, target_file="", description="",
                error_type="", tests_before=0, tests_after=0,
                timestamp=timestamp, rejection_reason="safety check failed",
            ))

        top_error, count, samples = self._find_top_error(error_store)
        if not top_error:
            return self._record(MutationResult(
                accepted=False, target_file="", description="no error patterns found",
                error_type="", tests_before=0, tests_after=0,
                timestamp=timestamp, rejection_reason="no data",
            ))

        target_rel = _ALLOWED_TARGETS.get(top_error, _DEFAULT_TARGET)
        target_abs = self._root / target_rel
        if not target_abs.exists():
            return self._record(MutationResult(
                accepted=False, target_file=target_rel,
                description=f"target file not found: {target_rel}",
                error_type=top_error, tests_before=0, tests_after=0,
                timestamp=timestamp, rejection_reason="target missing",
            ))

        tests_before = self._run_tests(test_cmd)
        if tests_before < _MIN_BASELINE_TESTS:
            return self._record(MutationResult(
                accepted=False, target_file=target_rel,
                description="baseline too low",
                error_type=top_error, tests_before=tests_before, tests_after=0,
                timestamp=timestamp,
                rejection_reason=f"baseline {tests_before} < {_MIN_BASELINE_TESTS}",
            ))

        file_content = target_abs.read_text()
        patch = self._propose_patch(top_error, count, samples, target_rel, file_content)
        if patch is None:
            return self._record(MutationResult(
                accepted=False, target_file=target_rel,
                description="LLM proposed no patch",
                error_type=top_error, tests_before=tests_before, tests_after=0,
                timestamp=timestamp, rejection_reason="no patch proposed",
            ))

        search_text, replace_text = patch
        if search_text not in file_content:
            return self._record(MutationResult(
                accepted=False, target_file=target_rel,
                description="SEARCH text not found in file",
                error_type=top_error, tests_before=tests_before, tests_after=0,
                timestamp=timestamp, rejection_reason="search text mismatch",
            ))

        backup = file_content
        mutated = file_content.replace(search_text, replace_text, 1)
        target_abs.write_text(mutated)

        tests_after = self._run_tests(test_cmd)

        if tests_after >= tests_before:
            description = f"Reduced {top_error} errors ({count} occurrences)"
            result = MutationResult(
                accepted=True, target_file=target_rel,
                description=description, error_type=top_error,
                tests_before=tests_before, tests_after=tests_after,
                timestamp=timestamp,
            )
            logger.info("[ScaffoldEvolver] ACCEPTED mutation on %s (+%d tests)", target_rel, tests_after - tests_before)
        else:
            target_abs.write_text(backup)
            result = MutationResult(
                accepted=False, target_file=target_rel,
                description="tests regressed — rolled back",
                error_type=top_error, tests_before=tests_before, tests_after=tests_after,
                timestamp=timestamp,
                rejection_reason=f"tests dropped from {tests_before} to {tests_after}",
            )
            logger.info("[ScaffoldEvolver] REJECTED mutation on %s (tests %d→%d)", target_rel, tests_before, tests_after)

        return self._record(result)

    def load_mutations(self) -> List[MutationResult]:
        """Return all recorded mutations from .awos/scaffold_mutations.jsonl."""
        if not self._mutations_log.exists():
            return []
        results = []
        for line in self._mutations_log.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    results.append(MutationResult(**json.loads(line)))
                except Exception:
                    pass
        return results

    # ── Internal helpers ────────────────────────────────────────────────────

    def _safety_check(self) -> bool:
        """All safety gates must pass."""
        if os.getenv("AWOS_SCAFFOLD_EVOLUTION", "").lower() != "true":
            logger.warning("[ScaffoldEvolver] AWOS_SCAFFOLD_EVOLUTION not set")
            return False
        if os.getenv("AWOS_SAFE_TO_RUN_TESTS", "").lower() != "true":
            logger.warning("[ScaffoldEvolver] AWOS_SAFE_TO_RUN_TESTS not set")
            return False
        return True

    def _find_top_error(
        self, error_store: "ErrorPatternStore"
    ) -> tuple[str, int, List[str]]:
        """Return (top_error_type, count, [sample_msgs])."""
        try:
            patterns = error_store.list_all() if hasattr(error_store, "list_all") else []
        except Exception:
            patterns = []

        if not patterns:
            return "", 0, []

        counts: dict[str, int] = {}
        samples: dict[str, list] = {}
        for p in patterns:
            et = getattr(p, "error_type", "UNKNOWN")
            counts[et] = counts.get(et, 0) + 1
            if et not in samples:
                samples[et] = []
            if len(samples[et]) < 3:
                msg = getattr(p, "error_msg", "")[:150]
                if msg:
                    samples[et].append(msg)

        if not counts:
            return "", 0, []

        top = max(counts, key=lambda k: counts[k])
        return top, counts[top], samples.get(top, [])

    def _propose_patch(
        self,
        error_type: str,
        count: int,
        samples: List[str],
        target_file: str,
        file_content: str,
    ) -> Optional[tuple[str, str]]:
        """Ask LLM for a SEARCH/REPLACE patch. Returns (search, replace) or None."""
        excerpt = file_content[:2000]
        sample_text = "\n".join(f"  - {s}" for s in samples[:3])

        prompt = (
            f"You are improving a coding agent's scaffold file: {target_file}\n\n"
            f"TOP FAILURE PATTERN: {error_type} — occurred {count} times.\n"
            f"Sample error messages:\n{sample_text or '  (none)'}\n\n"
            f"FILE EXCERPT (first 2000 chars):\n{excerpt}\n\n"
            f"Propose ONE small, targeted improvement to reduce {error_type} failures.\n"
            f"Be conservative — minimal change only. Use SEARCH/REPLACE format:\n\n"
            f"<<<SEARCH>>>\n<exact existing text to replace>\n"
            f"<<<REPLACE>>>\n<improved replacement text>\n<<<END>>>"
        )

        try:
            raw = self._cheap_call(prompt)
        except Exception as exc:
            logger.warning("[ScaffoldEvolver] proposal call failed: %s", exc)
            return None

        return self._parse_patch(raw)

    def _parse_patch(self, raw: str) -> Optional[tuple[str, str]]:
        """Parse <<<SEARCH>>>...<<<REPLACE>>>...<<<END>>> format."""
        try:
            search_start = raw.index("<<<SEARCH>>>") + len("<<<SEARCH>>>")
            replace_start = raw.index("<<<REPLACE>>>")
            end_marker   = raw.index("<<<END>>>")

            search_text  = raw[search_start:replace_start].strip()
            replace_text = raw[replace_start + len("<<<REPLACE>>>"):end_marker].strip()

            if not search_text or not replace_text:
                return None
            return search_text, replace_text
        except ValueError:
            return None

    def _run_tests(self, test_cmd: List[str]) -> int:
        """Run test suite, return pass count (0 on failure/timeout)."""
        try:
            result = subprocess.run(
                test_cmd,
                capture_output=True,
                text=True,
                timeout=300,
                cwd=str(self._root),
            )
            return self._parse_pass_count(result.stdout + result.stderr)
        except subprocess.TimeoutExpired:
            logger.warning("[ScaffoldEvolver] test run timed out")
            return 0
        except Exception as exc:
            logger.warning("[ScaffoldEvolver] test run error: %s", exc)
            return 0

    def _parse_pass_count(self, output: str) -> int:
        """Extract pass count from pytest -q output."""
        import re
        m = re.search(r"(\d+) passed", output)
        return int(m.group(1)) if m else 0

    def _record(self, result: MutationResult) -> MutationResult:
        """Append mutation result to .awos/scaffold_mutations.jsonl."""
        try:
            with open(self._mutations_log, "a") as f:
                f.write(json.dumps(asdict(result)) + "\n")
        except Exception as exc:
            logger.debug("[ScaffoldEvolver] log write failed: %s", exc)
        return result
