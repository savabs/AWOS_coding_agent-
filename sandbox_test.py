#!/usr/bin/env python3
"""
AWOS Self-Improvement Sandbox
==============================
The agent works on the AWOS project itself — dogfooding.

Pipeline:
  1. API health checks (tiny ping, reports what's live)
  2. Gemini 2.5 Flash plans 2-3 real tasks for AWOS
  3. DeepSeek Worker executes each task (Haiku fallback)
  4. Verifier checks Python syntax — rollback on failure
  5. Diff shown for every change
  6. Full cost report

Budget hard-cap: $0.50. Aborts before any call if < $0.05 remains.
All changes made to real AWOS files — GitManager backs each up first.
"""

import difflib
import json
import os
import sys
import time
from pathlib import Path

# ── load .env ────────────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

# ── paths ─────────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent
AGENT_DIR    = PROJECT_ROOT / "scaffold" / "agent"
sys.path.insert(0, str(AGENT_DIR))

# ── budget ────────────────────────────────────────────────────────────────────
BUDGET_CAP       = 0.50   # hard stop ($)
ABORT_THRESHOLD  = 0.05   # abort if less than this remains


# ═══════════════════════════════════════════════════════════════════════════════
#  1. API HEALTH CHECKS
# ═══════════════════════════════════════════════════════════════════════════════

def check_apis() -> dict:
    results = {}

    # Anthropic
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        results["anthropic"] = {"status": "MISSING_KEY", "cost": 0.0,
                                 "note": "Set ANTHROPIC_API_KEY → https://console.anthropic.com/"}
    else:
        try:
            from anthropic import Anthropic
            r = Anthropic(api_key=key).messages.create(
                model="claude-haiku-4-5", max_tokens=8,
                messages=[{"role": "user", "content": "reply: ok"}])
            cost = (r.usage.input_tokens / 1e6) * 1.0 + (r.usage.output_tokens / 1e6) * 5.0
            results["anthropic"] = {"status": "OK", "cost": cost,
                                     "note": f"claude-haiku-4-5 OK ({r.usage.input_tokens}in/{r.usage.output_tokens}out)"}
        except Exception as e:
            results["anthropic"] = {"status": "ERROR", "cost": 0.0, "note": str(e)[:120]}

    # DeepSeek
    key = os.getenv("DEEPSEEK_API_KEY")
    if not key:
        results["deepseek"] = {"status": "MISSING_KEY", "cost": 0.0,
                                "note": "Set DEEPSEEK_API_KEY → https://platform.deepseek.com/api_keys"}
    else:
        try:
            from openai import OpenAI
            r = OpenAI(api_key=key, base_url="https://api.deepseek.com").chat.completions.create(
                model="deepseek-chat", max_tokens=8,
                messages=[{"role": "user", "content": "reply: ok"}])
            cost = (r.usage.prompt_tokens / 1e6) * 0.14 + (r.usage.completion_tokens / 1e6) * 0.28
            results["deepseek"] = {"status": "OK", "cost": cost,
                                    "note": f"deepseek-chat OK ({r.usage.prompt_tokens}in/{r.usage.completion_tokens}out)"}
        except Exception as e:
            results["deepseek"] = {"status": "ERROR", "cost": 0.0, "note": str(e)[:120]}

    # Gemini — use new google.genai SDK
    key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not key:
        results["gemini"] = {"status": "MISSING_KEY", "cost": 0.0,
                              "note": "Set GEMINI_API_KEY → https://aistudio.google.com/apikey"}
    else:
        try:
            from google import genai as gai
            _shadow = os.environ.pop("GOOGLE_API_KEY", None)
            _gclient = gai.Client(api_key=key)
            if _shadow:
                os.environ["GOOGLE_API_KEY"] = _shadow
            r = _gclient.models.generate_content(model="gemini-2.5-flash", contents="reply: ok")
            meta = r.usage_metadata
            inp = meta.prompt_token_count if meta else 4
            out = meta.candidates_token_count if meta else 1
            cost = (inp / 1e6) * 0.15 + (out / 1e6) * 0.60
            results["gemini"] = {"status": "OK", "cost": cost,
                                  "note": f"gemini-2.5-flash OK ({inp}in/{out}out)"}
        except Exception as e:
            results["gemini"] = {"status": "ERROR", "cost": 0.0, "note": str(e)[:120]}

    # Tavily
    key = os.getenv("TAVILY_API_KEY")
    if not key:
        results["tavily"] = {"status": "MISSING_KEY", "cost": 0.0,
                              "note": "Set TAVILY_API_KEY → https://app.tavily.com/"}
    else:
        try:
            from tavily import TavilyClient
            r = TavilyClient(api_key=key).search("AWOS test", max_results=1)
            results["tavily"] = {"status": "OK", "cost": 0.001,
                                  "note": f"Tavily OK — {len(r.get('results', []))} result(s)"}
        except Exception as e:
            results["tavily"] = {"status": "ERROR", "cost": 0.0, "note": str(e)[:120]}

    return results


def print_health(results: dict) -> float:
    print("\n" + "═" * 68)
    print("  API HEALTH CHECK")
    print("═" * 68)
    total = 0.0
    for p, r in results.items():
        icon = "✅" if r["status"] == "OK" else ("❌" if r["status"] == "ERROR" else "⚠️ ")
        print(f"  {icon}  {p:<12}  {r['status']:<14}  {r['note']}")
        total += r.get("cost", 0.0)
    print(f"\n  Health check cost: ${total:.6f}")
    print("═" * 68)
    return total


# ═══════════════════════════════════════════════════════════════════════════════
#  2. SELF-IMPROVEMENT TASKS (agent works on AWOS itself)
# ═══════════════════════════════════════════════════════════════════════════════

# Real tasks on AWOS source files — each adds GENUINELY MISSING functionality.
# Actions are ANCHOR-BASED: SEARCH targets existing code, REPLACE extends it.
SELF_TASKS = [
    {
        "task_id": 1,
        "file": "scaffold/agent/example_store.py",
        "action": (
            "After the `size(self) -> int` method (its exact body is `return len(self._examples)`), "
            "add a new `recent(self, n: int = 5) -> list` method that returns the last `n` examples "
            "from `self._examples` as plain dicts using `asdict(e)`. "
            "SEARCH must be exactly:\n"
            "    def size(self) -> int:\n"
            "        return len(self._examples)\n"
            "REPLACE must be that same `size` method followed immediately by the new `recent` method."
        ),
        "complexity": "low",
    },
    {
        "task_id": 2,
        "file": "scaffold/agent/escalation_engine.py",
        "action": (
            "After the `failure_count(self, task_id: str) -> int` method "
            "(which counts consecutive failures by walking `self._task_history[task_id]` in reverse), "
            "add a new `reset_task(self, task_id: str)` method that deletes the key from "
            "`self._task_history` if it exists (use `self._task_history.pop(task_id, None)`). "
            "SEARCH must match the last 3 lines of the existing `failure_count` method exactly, "
            "REPLACE must contain those same 3 lines followed by the new `reset_task` method."
        ),
        "complexity": "low",
    },
    {
        "task_id": 3,
        "file": "scaffold/agent/response_cache.py",
        "action": (
            "After the `hit_rate` is computed inside the `stats(self)` method "
            "(which returns a dict with keys entries/hits/misses/hit_rate/estimated_savings_requests), "
            "add a new `hit_rate_float(self) -> float` method that returns the raw hit-rate as a float "
            "(hits / (hits + misses) if any requests were made, else 0.0). "
            "SEARCH must match the exact body of the existing `summary(self) -> str` method, "
            "REPLACE must contain that same `summary` method followed by the new `hit_rate_float` method."
        ),
        "complexity": "low",
    },
]


def budget_guard(spent: float, label: str) -> bool:
    remaining = BUDGET_CAP - spent
    if remaining < ABORT_THRESHOLD:
        print(f"\n  🛑 BUDGET GUARD: ${remaining:.4f} left < ${ABORT_THRESHOLD} — aborting '{label}'")
        return False
    return True


def show_diff(original: str, modified: str, filename: str):
    diff = list(difflib.unified_diff(
        original.splitlines(keepends=True),
        modified.splitlines(keepends=True),
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
        n=2,
    ))
    if diff:
        print(f"\n     {'─'*60}")
        print(f"     DIFF ({filename}):")
        for line in diff[:30]:
            sym = "  +"  if line.startswith("+") else ("  -" if line.startswith("-") else "   ")
            print(f"  {sym}  {line.rstrip()}")
        if len(diff) > 30:
            print(f"     ... ({len(diff)-30} more lines)")
        print(f"     {'─'*60}")


# ═══════════════════════════════════════════════════════════════════════════════
#  3. MAIN RUNNER
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    print("\n" + "█" * 68)
    print(f"  AWOS SELF-IMPROVEMENT SANDBOX  —  budget cap ${BUDGET_CAP:.2f}")
    print("█" * 68)

    # ── Step 1: API health ────────────────────────────────────────────────────
    print("\n⏳ Checking APIs...")
    health  = check_apis()
    spent   = print_health(health)
    ok_apis = {p for p, r in health.items() if r["status"] == "OK"}

    need_worker  = "deepseek" in ok_apis or "anthropic" in ok_apis
    need_planner = "gemini" in ok_apis

    if not need_worker:
        print("\n❌ No worker API available (need DEEPSEEK or ANTHROPIC key). Aborting.")
        sys.exit(1)

    # ── Step 2: Import agent components ──────────────────────────────────────
    from worker import Worker
    from verifier import Verifier
    from git_manager import GitManager
    from escalation_engine import EscalationEngine
    from example_store import ExampleStore
    from token_tracker import TokenTracker

    worker    = Worker()
    verifier  = Verifier()
    escalation = EscalationEngine(monthly_budget=BUDGET_CAP)
    examples  = ExampleStore(store_path="/tmp/awos_self_improve_examples.json")
    tracker   = TokenTracker(monthly_budget=BUDGET_CAP)
    tracker.total_cost = spent
    git       = GitManager(codebase_root=str(PROJECT_ROOT))

    # ── Step 3: Plan (Gemini) or use static tasks ─────────────────────────────
    tasks = SELF_TASKS
    if need_planner and budget_guard(spent, "Gemini planning"):
        try:
            from cheap_planner import CheapPlanner
            planner = CheapPlanner()
            ctx = {
                "modules": "worker.py, orchestrator.py, escalation_engine.py, example_store.py, response_cache.py",
                "architecture": "Planner→Worker→Verifier pipeline with EscalationEngine and ExampleStore",
                "files": ["scaffold/agent/worker.py", "scaffold/agent/example_store.py",
                          "scaffold/agent/response_cache.py", "scaffold/agent/escalation_engine.py"],
            }
            t0 = time.time()
            plan = planner.plan(
                goal=(
                    "Add small utility improvements to the AWOS agent: "
                    "add clear() method to ExampleStore, add clear() to ResponseCache, "
                    "add __repr__ to EscalationDecision dataclass"
                ),
                codebase_context=ctx,
                tracker=tracker,
            )
            elapsed = time.time() - t0
            spent = tracker.total_cost
            planned = plan.get("plan", [])
            if planned:
                tasks = planned
                print(f"\n  ✅ Gemini planned {len(tasks)} task(s) in {elapsed:.1f}s  (${spent - health.get('gemini',{}).get('cost',0):.5f})")
                for t in tasks:
                    print(f"     [{t['task_id']}] {t['file']} — {t['action'][:70]}")
            else:
                print("  ⚠️  Gemini returned empty plan, using built-in tasks")
        except Exception as e:
            print(f"  ⚠️  Gemini planning failed ({e}), using built-in tasks")
    else:
        print(f"\n  ℹ️  Using {len(tasks)} built-in tasks (Gemini not available)")

    # ── Step 4: Execute each task ─────────────────────────────────────────────
    print(f"\n{'─'*68}")
    print("  EXECUTING TASKS ON AWOS PROJECT")
    print(f"{'─'*68}")

    results = []
    for task in tasks:
        task_id   = task.get("task_id", "?")
        file_rel  = task.get("file", "")
        action    = task.get("action", "")
        file_abs  = PROJECT_ROOT / file_rel

        print(f"\n  [{task_id}] {file_rel}")
        print(f"       {action[:90]}")

        if not file_abs.exists():
            print(f"       ❌ File not found: {file_abs}")
            results.append({"task_id": task_id, "ok": False, "cost": 0.0,
                             "detail": f"File not found: {file_rel}"})
            continue

        if not budget_guard(spent, f"Task {task_id}"):
            results.append({"task_id": task_id, "ok": None, "cost": 0.0,
                             "detail": "ABORTED — budget cap"})
            continue

        original_content = file_abs.read_text()
        git.backup_file(str(file_abs))

        # Decide model tier
        budget_left  = BUDGET_CAP - spent
        esc          = escalation.decide(task, failure_count=0, budget_remaining=budget_left)
        print(f"       Model: {esc.spec.name}  (complexity={esc.complexity_score}/10)")

        # Worker generates SEARCH/REPLACE
        t0 = time.time()
        worker_result = worker.execute_task(
            task=task,
            file_content=original_content,
            codebase_context={"modules": file_rel},
            tracker=tracker,
            model_spec=esc.spec,
            example_store=examples,
        )
        elapsed = time.time() - t0
        task_cost = tracker.total_cost - spent
        spent     = tracker.total_cost

        if not worker_result.get("success"):
            print(f"       ❌ Worker failed: {worker_result.get('error','')[:100]}")
            git.rollback_file(str(file_abs))
            results.append({"task_id": task_id, "ok": False, "cost": task_cost,
                             "detail": worker_result.get("error", "worker failed")[:120]})
            continue

        # Pre-check: will the SEARCH actually match?
        new_content = original_content.replace(
            worker_result["search"], worker_result["replace"]
        )
        if new_content == original_content:
            print(f"       ⚠️  SEARCH not found in file (self-verify missed it)")
            git.rollback_file(str(file_abs))
            results.append({"task_id": task_id, "ok": False, "cost": task_cost,
                             "detail": "SEARCH string not found in file"})
            continue

        # Verify + apply atomically (file still has original content)
        verify = verifier.verify_and_apply(
            search_replace={
                "search": worker_result["search"],
                "replace": worker_result["replace"],
            },
            file_path=str(file_abs),
        )

        if verify.get("success"):
            examples.record_success(task, worker_result["search"], worker_result["replace"])
            git.finalize(success=True)
            show_diff(original_content, new_content, file_rel)
            print(f"       ✅ Verified OK  |  {elapsed:.1f}s  |  ${task_cost:.5f}")
            print(f"       Reasoning: {worker_result.get('reasoning','')[:90]}")
            results.append({"task_id": task_id, "ok": True, "cost": task_cost,
                             "detail": worker_result.get("reasoning", "")[:120]})
        else:
            errs = verify.get("errors", [])
            err_msg = errs[0] if errs else verify.get("error_context", "verification failed")[:100]
            print(f"       ❌ Syntax/apply FAILED: {err_msg[:100]}")
            print(f"       Rolling back {file_rel}")
            git.rollback_file(str(file_abs))
            results.append({"task_id": task_id, "ok": False, "cost": task_cost,
                             "detail": f"Syntax/apply error: {err_msg[:100]}"})

    # ── Step 5: Final report ──────────────────────────────────────────────────
    print(f"\n{'═'*68}")
    print("  SELF-IMPROVEMENT REPORT")
    print(f"{'═'*68}")
    passed  = sum(1 for r in results if r["ok"] is True)
    failed  = sum(1 for r in results if r["ok"] is False)
    skipped = sum(1 for r in results if r["ok"] is None)

    for r in results:
        icon = "✅" if r["ok"] is True else ("❌" if r["ok"] is False else "⏭️ ")
        print(f"  {icon}  Task {r['task_id']}  ${r['cost']:.5f}  {r['detail'][:80]}")

    print(f"\n  Tasks passed:  {passed}")
    print(f"  Tasks failed:  {failed}")
    print(f"  Tasks skipped: {skipped}")
    print(f"  Total cost:    ${spent:.5f} / ${BUDGET_CAP:.2f} cap  "
          f"(${BUDGET_CAP-spent:.5f} remaining)")

    missing = {p: r["note"] for p, r in health.items() if r["status"] in ("MISSING_KEY", "ERROR")}
    if missing:
        print(f"\n  📋 To unlock more providers:")
        for p, note in missing.items():
            icon = "⚠️ " if health[p]["status"] == "ERROR" else "📝"
            print(f"     {icon} {note}")

    print("═" * 68)
    return failed == 0


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
