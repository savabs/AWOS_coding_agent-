"""
live_renderer.py — Real-time agent activity renderer for AWOS.

Produces a Claude Code-style live terminal UI showing exactly what the
agent is doing as it does it: tool selection, model routing, patch
generation, verification, cost tracking.

Usage (wired automatically by orchestrator):
    renderer = LiveRenderer()
    renderer.session_start(goal="add login method", n_files=12)
    renderer.planning_done(tasks=[...], model="haiku", reason="simple goal")
    renderer.task_start(idx=1, total=3, task={...}, model="DeepSeek T2")
    renderer.tool_event("FaultLocalizer", "auth.py:45-89 · confidence 0.87")
    renderer.tool_event("VectorMemory", "3 chunks retrieved")
    renderer.worker_start(attempt=1, model="deepseek-chat", temperature=0.2)
    renderer.worker_done(success=True, tokens=812, cost=0.003, n_edits=2)
    renderer.verify_result(passed=True, issues=[])
    renderer.task_done(success=True, elapsed=3.1)
    renderer.session_done(completed=3, failed=0, total_cost=0.009, elapsed=9.2)
"""

from __future__ import annotations

import os
import sys
import threading
import time
from typing import List, Optional


# ── ANSI ──────────────────────────────────────────────────────────────────────

def _c(*codes: int) -> str:
    return f"\033[{';'.join(str(c) for c in codes)}m"

RESET  = _c(0)
BOLD   = _c(1)
DIM    = _c(2)
GREEN  = _c(32)
YELLOW = _c(33)
BLUE   = _c(34)
CYAN   = _c(36)
WHITE  = _c(97)
RED    = _c(31)
MAGENTA = _c(35)
BG_DARK = _c(40)

def _g(t): return f"{GREEN}{t}{RESET}"
def _r(t): return f"{RED}{t}{RESET}"
def _y(t): return f"{YELLOW}{t}{RESET}"
def _b(t): return f"{BLUE}{t}{RESET}"
def _c_(t): return f"{CYAN}{t}{RESET}"
def _m(t): return f"{MAGENTA}{t}{RESET}"
def _dim(t): return f"{DIM}{t}{RESET}"
def _bold(t): return f"{BOLD}{t}{RESET}"
def _w(t): return f"{WHITE}{t}{RESET}"


# ── Spinner ────────────────────────────────────────────────────────────────────

class _Spinner:
    FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def __init__(self, label: str):
        self._label = label
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._spin, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *_):
        self._stop.set()
        self._thread.join()
        sys.stdout.write("\r\033[2K")
        sys.stdout.flush()

    def _spin(self):
        i = 0
        while not self._stop.is_set():
            frame = self.FRAMES[i % len(self.FRAMES)]
            sys.stdout.write(f"\r  {CYAN}{frame}{RESET}  {self._label}")
            sys.stdout.flush()
            time.sleep(0.08)
            i += 1


# ── LiveRenderer ───────────────────────────────────────────────────────────────

class LiveRenderer:
    """
    Drop-in renderer for real-time AWOS agent activity display.
    All methods are no-ops if stdout is not a TTY (CI/redirect safe).
    """

    TOOL_ICONS = {
        "FaultLocalizer":    "◆",
        "VectorMemory":      "◈",
        "LintPipeline":      "◉",
        "SkillLibrary":      "◇",
        "MCTS":              "◎",
        "ParallelSampling":  "◐",
        "Planner":           "◑",
        "Worktree":          "◳",
    }

    MODEL_COLOURS = {
        "haiku":    CYAN,
        "sonnet":   BLUE,
        "deepseek": MAGENTA,
        "gemini":   YELLOW,
        "opus":     RED,
    }

    def __init__(self, tty: bool | None = None):
        self._tty = tty if tty is not None else sys.stdout.isatty()
        self._task_start_time: float = 0.0
        self._session_start_time: float = time.time()
        self._current_task_idx = 0
        self._total_tasks = 0
        self._lock = threading.Lock()

    # ── Public API ─────────────────────────────────────────────────────────────

    def session_start(self, goal: str, n_files: int = 0) -> None:
        width = min(os.get_terminal_size().columns if self._tty else 68, 68)
        border = "─" * (width - 2)
        goal_display = goal if len(goal) <= width - 12 else goal[:width - 15] + "..."
        self._print()
        self._print(f"{CYAN}╭{border}╮{RESET}")
        self._print(f"{CYAN}│{RESET} {BOLD}AWOS{RESET}  {_dim('coding agent')}  {_dim('─')}  {_w(goal_display)}")
        if n_files:
            self._print(f"{CYAN}│{RESET} {_dim(f'{n_files} files indexed')}")
        self._print(f"{CYAN}╰{border}╯{RESET}")
        self._print()

    def planning_done(self, tasks: list, model: str = "", reason: str = "") -> None:
        model_col = self._model_colour(model)
        model_str = f"  {_dim('·')}  {model_col}{model}{RESET}" if model else ""
        reason_str = f"  {_dim('('+ reason +')')}" if reason else ""
        self._print(f"  {_b('▶')} {_bold('Planner')}  →  {_g(str(len(tasks)) + ' tasks')}{model_str}{reason_str}")
        for i, t in enumerate(tasks, 1):
            action = t.get("action", str(t))[:70]
            complexity = t.get("complexity", "")
            c_col = GREEN if str(complexity).lower() == "low" else (YELLOW if str(complexity).lower() == "medium" else RED)
            cx = f"  {_dim('['+ str(complexity) +']')}" if complexity else ""
            self._print(f"    {_dim(str(i)+'.')}  {action}{cx}")
        self._print()

    def task_start(self, idx: int, total: int, task: dict, model: str = "") -> None:
        self._task_start_time = time.time()
        self._current_task_idx = idx
        self._total_tasks = total
        action = task.get("action", "")
        file = task.get("file", "")
        complexity = task.get("complexity", "")
        model_col = self._model_colour(model)
        model_str = f"  {model_col}{model}{RESET}" if model else ""
        progress = f"{CYAN}{idx}{RESET}{_dim('/'+str(total))}"
        bar = "━" * 48
        self._print(f"  {_dim(bar)}")
        self._print(f"  {_b('Task')} {progress}  {_bold(action[:60])}")
        if file:
            self._print(f"  {_dim('  File')}     {_c_(file)}")
        if complexity:
            cx_col = GREEN if str(complexity).lower() == "low" else (YELLOW if str(complexity).lower() == "medium" else RED)
            self._print(f"  {_dim('  Complexity')} {cx_col}{complexity}{RESET}{model_str}")
        self._print()

    def tool_event(self, tool: str, detail: str, success: bool = True) -> None:
        icon = self.TOOL_ICONS.get(tool, "◆")
        col = CYAN if success else YELLOW
        status = _g("✓") if success else _y("!")
        self._print(f"  {col}{icon}{RESET}  {_dim(tool):<20}  {detail}")

    def worker_start(self, attempt: int, model: str, temperature: float = 0.2) -> None:
        model_col = self._model_colour(model)
        temp_str = _dim(f"  t={temperature:.1f}") if temperature else ""
        attempt_str = f"  {_y('retry '+str(attempt))}" if attempt > 1 else ""
        self._print(f"  {_dim('⟳')}  {model_col}{model}{RESET}  generating patch{attempt_str}{temp_str}")

    def worker_done(
        self,
        success: bool,
        tokens: int = 0,
        cached_tokens: int = 0,
        cost: float = 0.0,
        n_edits: int = 0,
        error: str = "",
    ) -> None:
        if success:
            tok_str = f"{tokens:,} tok" if tokens else ""
            cache_str = f"  {_g(str(cached_tokens)+' cached')}" if cached_tokens > 0 else ""
            cost_str = f"  ${cost:.4f}" if cost > 0 else ""
            edits_str = f"  {_g(str(n_edits)+' edit'+('s' if n_edits != 1 else ''))}" if n_edits else ""
            self._print(f"  {_g('✓')}  patch generated  {_dim(tok_str)}{cache_str}{cost_str}{edits_str}")
        else:
            short_err = (error[:60] + "…") if len(error) > 60 else error
            self._print(f"  {_r('✗')}  worker failed  {_dim(short_err)}")

    def verify_result(self, passed: bool, issues: List[str] | None = None) -> None:
        issues = issues or []
        if passed:
            self._print(f"  {_g('✓')}  verification passed")
        else:
            self._print(f"  {_r('✗')}  verification failed")
            for issue in issues[:3]:
                self._print(f"       {_dim('·')}  {_y(issue[:70])}")

    def lint_result(self, n_issues: int, blocking: bool = False) -> None:
        if n_issues == 0:
            self._print(f"  {_g('✓')}  {_dim('Ruff')}  clean")
        elif blocking:
            self._print(f"  {_r('!')}  {_dim('Ruff')}  {_r(str(n_issues)+' blocking issue'+('s' if n_issues!=1 else ''))}")
        else:
            self._print(f"  {_y('!')}  {_dim('Ruff')}  {_y(str(n_issues)+' warning'+('s' if n_issues!=1 else ''))}")

    def test_result(self, passed: int, total: int, elapsed: float = 0.0) -> None:
        rate = passed / total if total > 0 else 0
        col = GREEN if rate >= 0.9 else (YELLOW if rate >= 0.5 else RED)
        bar = self._mini_bar(rate, 12, col)
        elapsed_str = f"  {_dim(f'{elapsed:.1f}s')}" if elapsed > 0 else ""
        self._print(f"  {col}{'✓' if rate >= 1.0 else '!'}{RESET}  {_dim('Tests')}  {bar}  {col}{passed}/{total}{RESET}{elapsed_str}")

    def task_done(self, success: bool, elapsed: float = 0.0) -> None:
        elapsed_str = f"  {_dim(f'{elapsed:.1f}s')}" if elapsed > 0 else ""
        if success:
            self._print(f"  {_g('●')}  {_bold(_g('task complete'))}{elapsed_str}")
        else:
            self._print(f"  {_r('●')}  {_bold(_r('task failed'))}{elapsed_str}")
        self._print()

    def retry_notice(self, attempt: int, reason: str, correction: str = "") -> None:
        self._print(f"  {_y('↻')}  {_y('retry ' + str(attempt))}  {_dim(reason[:60])}")
        if correction:
            self._print(f"       {_dim('approach:')}  {correction[:60]}")

    def session_done(
        self,
        completed: int,
        failed: int,
        total_cost: float,
        elapsed: float,
        grade: str = "",
        skills_learned: int = 0,
    ) -> None:
        total = completed + failed
        all_ok = failed == 0
        col = GREEN if all_ok else (YELLOW if completed > 0 else RED)
        width = min(os.get_terminal_size().columns if self._tty else 68, 68)
        border = "─" * (width - 2)
        self._print()
        self._print(f"{col}╭{border}╮{RESET}")
        self._print(
            f"{col}│{RESET}  {'✓' if all_ok else '!'} "
            f"{col}{_bold(str(completed)+'/'+str(total)+' tasks')}{RESET}  "
            f"{_dim('·')}  ${total_cost:.4f}  "
            f"{_dim('·')}  {elapsed:.1f}s"
            + (f"  {_dim('·')}  {_g('Grade ' + grade)}" if grade else "")
        )
        if skills_learned > 0:
            self._print(f"{col}│{RESET}  {_g('◇')} {skills_learned} skill{'s' if skills_learned != 1 else ''} learned")
        self._print(f"{col}╰{border}╯{RESET}")
        self._print()

    def spinner(self, label: str) -> _Spinner:
        """Context manager for spinner: `with renderer.spinner('label'):`"""
        return _Spinner(label) if self._tty else _NoOpSpinner()

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _print(self, msg: str = "") -> None:
        with self._lock:
            print(msg)

    def _model_colour(self, model: str) -> str:
        model_lower = model.lower()
        for key, col in self.MODEL_COLOURS.items():
            if key in model_lower:
                return col
        return WHITE

    @staticmethod
    def _mini_bar(value: float, width: int, colour: str) -> str:
        filled = int(round(value * width))
        return f"{colour}{'█' * filled}{'░' * (width - filled)}{RESET}"


class _NoOpSpinner:
    def __enter__(self): return self
    def __exit__(self, *_): pass


# ── Module-level singleton (shared across orchestrator calls) ─────────────────

_renderer: LiveRenderer | None = None

def get_renderer() -> LiveRenderer:
    global _renderer
    if _renderer is None:
        _renderer = LiveRenderer()
    return _renderer
