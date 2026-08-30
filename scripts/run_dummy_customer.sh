#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────
# Dummy Customer Simulation — AWOS Dogfood
# ──────────────────────────────────────────────────────────────────────────
# Simulates a paying customer ("DevMint Labs") using AWOS over ~2 weeks on
# their Python codebase. Runs 10 realistic coding jobs, tracks cost per job,
# and proves compounding: Job 1 expensive → Job 10 cheaper.
#
# Usage:  ./scripts/run_dummy_customer.sh
# ──────────────────────────────────────────────────────────────────────────
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
JOBS_FILE="$ROOT/.awos/dummy_customer_jobs.txt"
RESULTS_FILE="$ROOT/.awos/dummy_customer_results.csv"
REPORT_FILE="$ROOT/.awos/dummy_customer_report.txt"

# ── Customer persona ─────────────────────────────────────────────────────
echo "╔══════════════════════════════════════════════════════════════════════╗"
echo "║     AWOS DUMMY CUSTOMER SIMULATION — DevMint Labs                   ║"
echo "╠══════════════════════════════════════════════════════════════════════╣"
echo "║  Customer:  DevMint Labs (3-devs, Python, microservices)           ║"
echo "║  Current:   Cursor Pro ~₹2,000/mo, frustrated with babysitting     ║"
echo "║  Pilot:     First month free, 10 jobs on their codebase            ║"
echo "║  Metric:    Cost per job ↓ over time = compounding advantage       ║"
echo "╚══════════════════════════════════════════════════════════════════════╝"
echo ""

# ── Job definitions (10 realistic coding tasks) ──────────────────────────
# Format: one job per line with job_number|difficulty|goal
cat > "$JOBS_FILE" << 'JOBS_EOF'
1|EASY|Add a brief module-level docstring to scaffold/agent/verifier.py right after the imports that describes the verifier's role: applies code changes and validates them
2|EASY|Add a dedicated __repr__ or __str__ method to the DiffHunk dataclass in scaffold/agent/diff_builder.py that clearly shows hunk position and line count
3|EASY|Add module-level color name constants (ANSI_GREEN, ANSI_YELLOW, ANSI_RED, ANSI_RESET) at the top of scaffold/agent/budget_ledger.py and replace the inline escape codes with them in the show_status method
4|MEDIUM|Define a class-level constant TOKENS_PER_WORD = 1.3 inside the Worker class in scaffold/agent/worker.py and replace all three inline uses of the magic number 1.3 with this constant
5|MEDIUM|Extract the hardcoded fallback model string claude-haiku-4-5 in Worker.__init__ into an environment-variable-controlled default with a class-level fallback constant in scaffold/agent/worker.py
6|MEDIUM|Add _check_cpp_syntax and _check_ts_syntax docstrings in scaffold/agent/verifier.py that clearly describe the inputs, validation logic, and failure modes
7|MEDIUM|Replace all placeholder docstrings in scaffold/agent/ package __init__.py files (core, memory, tools, config) with meaningful 1-2 line descriptions of each package's responsibility
8|MEDIUM|Extract the duplicated VectorMemory setup code in awos.py (used by cmd_memory_search and cmd_memory_stats) into a shared _get_vector_memory() helper function
9|HARD|Create tests/test_budget_ledger.py with unit tests for BudgetLedger.get_status, record, check_budget, and record_cache_hit methods
10|HARD|Replace the dynamic __import__ call in cmd_performance in awos.py with a proper import statement, using absolute or explicit relative import with error handling
JOBS_EOF

echo "Job plan: 10 jobs (3 easy → 4 medium → 3 hard)"
echo "Each job runs via: awos worker start --auto-approve"
echo ""

# ── Track baseline budget before run ──────────────────────────────────────
BUDGET_BEFORE=$(python3 -c "
import json
from pathlib import Path
ledger = Path('.awos/budget.json')
if ledger.exists():
    data = json.loads(ledger.read_text())
    records = data if isinstance(data, list) else []
    print(json.dumps({'spent': sum(r.get('cost',0) for r in records), 'count': len(records)}))
else:
    print(json.dumps({'spent': 0, 'count': 0}))
" 2>/dev/null || echo '{"spent": 0, "count": 0}')

echo "Budget baseline: $(echo $BUDGET_BEFORE | python3 -c 'import json,sys; d=json.load(sys.stdin); print(f\"${d[\"spent\"]:.4f} spent across {d[\"count\"]} requests\")')"
echo ""

# ── Header for results ────────────────────────────────────────────────────
echo "job,status,tasks_completed,total_tasks,cost_usd,wall_sec,session_id" > "$RESULTS_FILE"

JOB_NUM=0

run_one_job() {
    local goal="$1"
    local difficulty="$2"
    JOB_NUM=$((JOB_NUM + 1))

    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  JOB $JOB_NUM/10 [$difficulty]"
    echo "  Goal: $goal"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    local start_ts=$(date +%s)

    local output
    output=$(cd "$ROOT" && python3 awos.py worker start --root "$ROOT" --auto-approve "$goal" 2>&1) || true

    local end_ts=$(date +%s)
    local wall_sec=$((end_ts - start_ts))

    local status="FAIL"
    local tasks_completed=0
    local total_tasks=0
    local cost_usd="0.0000"
    local session_id=""

    # Parse output for success markers
    if echo "$output" | grep -q 'Success:.*✓'; then
        status="PASS"
    fi

    tasks_completed=$(echo "$output" | grep -oP 'Tasks:\s+\K\d+' | head -1 || echo 0)
    total_tasks=$(echo "$output" | grep -oP 'Tasks:\s+\d+/\K\d+' | head -1 || echo 0)
    session_id=$(echo "$output" | grep -oP 'Session:\s+\Krs_\w+' | head -1 || echo "")

    # Get cost from budget delta
    local cost_json
    cost_json=$(cd "$ROOT" && python3 -c "
import json
from pathlib import Path
ledger = Path('.awos/budget.json')
if ledger.exists():
    data = json.loads(ledger.read_text())
    records = data if isinstance(data, list) else []
    spent = sum(r.get('cost',0) for r in records)
    print(json.dumps({'spent': spent, 'count': len(records)}))
" 2>/dev/null || echo '{"spent":0,"count":0}')

    local spent_total=$(echo "$cost_json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["spent"])')
    local spent_start=$(echo "$BUDGET_BEFORE" | python3 -c 'import json,sys; print(json.load(sys.stdin)["spent"])')
    cost_usd=$(python3 -c "print(f'{(float($spent_total) - float($spent_start)):.6f}')")

    echo ""
    echo "  Result:  $status  |  ${tasks_completed}/${total_tasks} tasks  |  \$$cost_usd  |  ${wall_sec}s  |  $session_id"

    # Write to results
    echo "$JOB_NUM,$status,$tasks_completed,$total_tasks,$cost_usd,$wall_sec,$session_id" >> "$RESULTS_FILE"

    # Brief output snippet on failure
    if [ "$status" = "FAIL" ]; then
        echo "  Last 3 lines of output:"
        echo "$output" | tail -3 | while read line; do echo "    $line"; done
    fi
    echo ""
}

# ── Run all jobs ──────────────────────────────────────────────────────────
while IFS='|' read -r jnum difficulty goal; do
    run_one_job "$goal" "$difficulty"
done < "$JOBS_FILE"

# ── Final report ──────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════════════════╗"
echo "║                    SIMULATION COMPLETE                              ║"
echo "╚══════════════════════════════════════════════════════════════════════╝"
echo ""

echo "─── Per-job cost trend ─────────────────────────────────────────────────"
echo ""
column -t -s',' "$RESULTS_FILE" | head -1
echo "─────────────────────────────────────────────────────────────────────────"
column -t -s',' "$RESULTS_FILE" | tail -n +2

echo ""
echo "─── Compounding savings report ─────────────────────────────────────────"
echo ""
cd "$ROOT" && python3 awos.py stats --savings

echo ""
echo "─── Summary statistics ─────────────────────────────────────────────────"
echo ""
python3 -c "
import csv
rows = list(csv.DictReader(open('$RESULTS_FILE')))
passed = sum(1 for r in rows if r['status'] == 'PASS')
total = len(rows)
total_cost = sum(float(r['cost_usd']) for r in rows)
avg_cost_first3 = sum(float(r['cost_usd']) for r in rows[:3]) / max(1, len(rows[:3]))
avg_cost_last3 = sum(float(r['cost_usd']) for r in rows[-3:]) / max(1, len(rows[-3:]))
cost_change_pct = ((avg_cost_last3 - avg_cost_first3) / avg_cost_first3 * 100) if avg_cost_first3 > 0 else 0

print(f'  Jobs completed:  {passed}/{total}')
print(f'  Total cost:      \${total_cost:.4f}')
print(f'  Avg cost (jobs 1-3):  \${avg_cost_first3:.4f}')
print(f'  Avg cost (jobs 8-10): \${avg_cost_last3:.4f}')
print(f'  Cost change:     {cost_change_pct:+.1f}%')
print()
if cost_change_pct < 0:
    print('  ✓ COMPOUNDING WORKS: cost decreases over time')
elif cost_change_pct > 0:
    print('  ⚠ Cost increased — compounding not yet visible (large late jobs)')
else:
    print('  → Flat — more data needed')
"

echo ""
echo "Results saved: $RESULTS_FILE"
echo "Full report:   $REPORT_FILE"
echo "Job listing:   $JOBS_FILE"
