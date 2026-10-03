"""
bench_cases.py — Loading, staging and scoring the benchmark cases.

One implementation of "lay a case out on disk and run its tests", shared by
scripts/validate_bug_cases.py and scripts/bench_executors.py. The repository
already carries four model routers and two orchestrators from experiments added
alongside rather than replacing; case handling does not need a second copy.

A case is a directory:

    tests/bug_cases/<name>/
        task.json        {"action": "...", "file": "buggy.py"}
        buggy.py         the file the agent must fix
        test_case.py     pytest that fails before the fix and passes after
        fix.py           ground truth, for validation and fault localisation
        context/         optional — other project files, copied in alongside

`context/` is what makes a case discriminating. A single-shot worker is handed
only the contents of buggy.py, so a fix that depends on a constant, a field
name or a signature defined in context/ is unreachable for it — while an agent
that can open files finds it by looking. Cases without context/ are solvable
from the file alone and serve as the baseline.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

DEFAULT_CASES_DIR = "tests/bug_cases"
TEST_TIMEOUT_SEC = 60


@dataclass
class BugCase:
    """One benchmark case, loaded from disk."""

    path: Path
    task: dict[str, Any]
    buggy_source: str
    fix_source: str
    test_source: str
    context: dict[str, str] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def target_file(self) -> str:
        return self.task.get("file", "buggy.py")

    @property
    def action(self) -> str:
        return self.task.get("action", "")

    @property
    def is_multi_file(self) -> bool:
        return bool(self.context)

    @property
    def tier(self) -> str:
        """Leading letter of the case name groups cases by kind."""
        return self.name[0].upper() if self.name else "?"


def load_case(case_dir: str | Path) -> BugCase:
    """Read a case directory into memory."""
    path = Path(case_dir)
    context: dict[str, str] = {}
    context_dir = path / "context"
    if context_dir.is_dir():
        for extra in sorted(context_dir.iterdir()):
            if extra.is_file():
                context[extra.name] = extra.read_text(encoding="utf-8")
    return BugCase(
        path=path,
        task=json.loads((path / "task.json").read_text(encoding="utf-8")),
        buggy_source=(path / "buggy.py").read_text(encoding="utf-8"),
        fix_source=(path / "fix.py").read_text(encoding="utf-8")
        if (path / "fix.py").exists()
        else "",
        test_source=(path / "test_case.py").read_text(encoding="utf-8")
        if (path / "test_case.py").exists()
        else "",
        context=context,
    )


def discover_cases(cases_dir: str | Path = DEFAULT_CASES_DIR) -> list[Path]:
    """Every runnable case directory, sorted by name."""
    root = Path(cases_dir)
    if not root.exists():
        return []
    return sorted(
        d
        for d in root.iterdir()
        if d.is_dir()
        and not d.name.startswith(".")
        and (d / "buggy.py").exists()
        and (d / "task.json").exists()
    )


def stage_case(case: BugCase, dest: str | Path, source: Optional[str] = None) -> Path:
    """
    Write a runnable copy of `case` into `dest`.

    `source` overrides the contents of the file under test, which is how a
    candidate patch — or the ground-truth fix — gets scored.
    """
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)

    (dest / "buggy.py").write_text(
        case.buggy_source if source is None else source, encoding="utf-8"
    )
    if case.test_source:
        (dest / "test_case.py").write_text(case.test_source, encoding="utf-8")
    for name, content in case.context.items():
        (dest / name).write_text(content, encoding="utf-8")

    # A local pytest.ini keeps the repository's own config — its -m filter and
    # --timeout, which need plugins a bare case cannot assume — from applying.
    (dest / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    return dest


def run_case_tests(
    case: BugCase, source: Optional[str] = None, timeout: int = TEST_TIMEOUT_SEC
) -> tuple[bool, str]:
    """
    Run a case's tests against `source`. Returns (all_passed, combined_output).

    Each run happens in a throwaway directory, so cases cannot contaminate each
    other or the repository.
    """
    with tempfile.TemporaryDirectory() as tmp:
        staged = stage_case(case, tmp, source=source)
        try:
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "test_case.py",
                    "-q",
                    "--no-header",
                    "-p",
                    "no:cacheprovider",
                ],
                cwd=staged,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return False, f"timed out after {timeout}s"
        return proc.returncode == 0, proc.stdout + proc.stderr
