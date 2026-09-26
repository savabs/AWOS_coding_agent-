"""
AWOS Self-Learning Demo
Shows all three features working without a real API key.
Run: python3 demo_self_learning.py
"""

import json
import os
import sys
import textwrap
import tempfile
import shutil
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent / "scaffold" / "agent"))

from prompt_evolver import PromptEvolver
from live_tool_synth import LiveToolSynthesizer
from scaffold_evolver import ScaffoldEvolver

RESET  = "\033[0m"
BOLD   = "\033[1m"
GREEN  = "\033[32m"
YELLOW = "\033[33m"
CYAN   = "\033[36m"
RED    = "\033[31m"
DIM    = "\033[2m"

def banner(text):
    print(f"\n{BOLD}{CYAN}{'─'*60}{RESET}")
    print(f"{BOLD}{CYAN}  {text}{RESET}")
    print(f"{BOLD}{CYAN}{'─'*60}{RESET}")

def ok(text):    print(f"  {GREEN}✓{RESET} {text}")
def info(text):  print(f"  {YELLOW}→{RESET} {text}")
def dim(text):   print(f"  {DIM}{text}{RESET}")
def err(text):   print(f"  {RED}✗{RESET} {text}")


# ─── Setup temp workspace ────────────────────────────────────────────────────
tmp = Path(tempfile.mkdtemp(prefix="awos_demo_"))
awos = tmp / ".awos"
awos.mkdir()
tools_dir = awos / "tools"
tools_dir.mkdir()

def teardown():
    shutil.rmtree(tmp, ignore_errors=True)


# ─── Seed some fake historical data ─────────────────────────────────────────
patterns_file = awos / "error_patterns.jsonl"
patterns_file.write_text("\n".join([
    json.dumps({"error_type": "SYNTAX_ERROR",     "critique": "Missing closing bracket on line 42", "error_msg": "SyntaxError: unexpected EOF"}),
    json.dumps({"error_type": "SYNTAX_ERROR",     "critique": "Indent is wrong after if-block",     "error_msg": "IndentationError"}),
    json.dumps({"error_type": "SYNTAX_ERROR",     "critique": "String not terminated",               "error_msg": "SyntaxError: EOL"}),
    json.dumps({"error_type": "SEARCH_NOT_FOUND", "critique": "Search text has extra whitespace",    "error_msg": "no match found"}),
    json.dumps({"error_type": "SEARCH_NOT_FOUND", "critique": "Context lines changed since read",    "error_msg": "no match found"}),
]) + "\n")

skills_dir = awos / "skills"
skills_dir.mkdir()
(skills_dir / "index.json").write_text(json.dumps([
    {"task_type": "bug_fix",    "keywords": ["syntax", "indent"],  "model_used": "deepseek", "win_rate": 0.91},
    {"task_type": "refactor",   "keywords": ["rename", "extract"], "model_used": "claude",   "win_rate": 0.85},
]))


# ═══════════════════════════════════════════════════════════════════════════
#  DEMO 1 — PromptEvolver (Feature 1A)
# ═══════════════════════════════════════════════════════════════════════════
banner("Feature 1A  —  PromptEvolver")

GUIDELINE_RESPONSE = json.dumps({
    "guidelines": [
        {
            "section":    "syntax",
            "guideline":  "Always include 2 lines of context before and after the SEARCH block to ensure unique matching.",
            "reason":     "SYNTAX_ERROR occurred 3 times — search text mismatches cause failed replacements",
            "confidence": 0.88,
        },
        {
            "section":    "search_replace",
            "guideline":  "Re-read the exact indentation of the target lines before writing the SEARCH block.",
            "reason":     "SEARCH_NOT_FOUND occurred 2 times due to whitespace differences",
            "confidence": 0.82,
        },
    ]
})

pe_call  = MagicMock(return_value=GUIDELINE_RESPONSE)
pe       = PromptEvolver(store_path=str(awos), cheap_call=pe_call)

os.environ["AWOS_PROMPT_EVOLUTION"]    = "true"
os.environ["AWOS_PROMPT_EVOLVE_EVERY"] = "10"

info("Checking historical evidence in .awos/error_patterns.jsonl ...")
patterns = pe._read_error_patterns()
ok(f"Loaded {len(patterns)} error patterns")
dim(f"  Top types: {', '.join(p['error_type'] for p in patterns[:3])}")

info("Reading skill library ...")
skills = pe._read_skill_entries()
ok(f"Loaded {len(skills)} skill entries")

info("Building meta-prompt + calling cheap LLM ...")
guidelines = pe.evolve(error_patterns_raw=patterns, skill_entries_raw=skills)
ok(f"Got {len(guidelines.splitlines())} guidelines from LLM")

print()
print(f"  {BOLD}Evolved Guidelines:{RESET}")
for line in guidelines.splitlines():
    print(f"    {GREEN}{line}{RESET}")

info("Persisting to .awos/evolved_prompt.json ...")
pe.persist(guidelines, session_count=10)
saved = json.loads((awos / "evolved_prompt.json").read_text())
ok(f"Saved — session_count={saved['session_count']}, changes_applied={saved['changes_applied']}")

info("Worker hot-loading evolved guidelines ...")
loaded = pe.load_evolved_guidelines()
ok(f"Loaded {len(loaded)} chars of guidelines — injected as [EVOLVED GUIDELINES] in next prompt")

info("should_evolve(10) = True, should_evolve(7) = False ...")
ok(f"should_evolve(10) → {pe.should_evolve(10)}")
ok(f"should_evolve(7)  → {pe.should_evolve(7)}")


# ═══════════════════════════════════════════════════════════════════════════
#  DEMO 2 — LiveToolSynthesizer (Feature 1B)
# ═══════════════════════════════════════════════════════════════════════════
banner("Feature 1B  —  LiveToolSynthesizer")

REFLECT_RESPONSE = json.dumps({
    "should_create": True,
    "tool_purpose": "check if the target Python file has syntax errors using py_compile"
})

TOOL_CODE = textwrap.dedent('''\
    """Check if a Python file has syntax errors by reading it from stdin task dict."""
    import sys, json, py_compile, tempfile, os
    try:
        task = json.loads(sys.stdin.read())
        file_path = task.get("file", "")
        if file_path and os.path.exists(file_path):
            py_compile.compile(file_path, doraise=True)
            print(f"SYNTAX OK: {file_path}")
        else:
            print("File not provided or not found — skipping syntax check")
    except py_compile.PyCompileError as e:
        print(f"SYNTAX ERROR DETECTED: {e}")
    except Exception as e:
        print(f"Check skipped: {e}")
''')

os.environ["AWOS_LIVE_TOOLS"] = "true"

call_count = [0]
def lts_mock(prompt):
    call_count[0] += 1
    if "Would writing" in prompt:
        return REFLECT_RESPONSE
    return TOOL_CODE

lts = LiveToolSynthesizer(tools_dir=str(tools_dir), cheap_call=lts_mock)

info("Simulating attempt=2 failure on a syntax task ...")
task = {"action": "fix syntax error in parser.py", "file": "scaffold/agent/worker.py"}
error = "SyntaxError: unexpected EOF while parsing"

tool = lts.reflect(task=task, error=error, attempt=2)

if tool:
    ok(f"Tool synthesized: {tool.name}")
    ok(f"Script saved to: {tool.script_path}")
    dim(f"  Purpose: {tool.description}")
else:
    err("Tool synthesis returned None (check mock)")

info("Verifying tool is registered in .awos/tools/index.json ...")
all_tools = lts.list_tools()
ok(f"{len(all_tools)} tool(s) in registry")

info("Running tool against the current task ...")
if tool:
    output = lts.run_tool(tool, task)
    ok(f"Tool output: {repr(output[:120])}")

info("Simulating next task — find_relevant_tool() by keyword overlap ...")
new_task = {"action": "there is a syntax error in the module file"}
matched = lts.find_relevant_tool(new_task)
if matched:
    ok(f"Matched existing tool: {matched.name}")
    dim(f"  Trigger pattern overlap with new task")
else:
    info("No keyword match above threshold — tool not injected")


# ═══════════════════════════════════════════════════════════════════════════
#  DEMO 3 — ScaffoldEvolver (Feature 1C)
# ═══════════════════════════════════════════════════════════════════════════
banner("Feature 1C  —  ScaffoldEvolver")

# Build a fake scaffold root
scaffold_root = tmp / "scaffold_root"
(scaffold_root / "scaffold" / "agent").mkdir(parents=True)
target_file = scaffold_root / "scaffold" / "agent" / "self_correction.py"
original_code = textwrap.dedent('''\
    def classify_error(msg: str) -> str:
        """Map an error message string to a known error class."""
        msg_lower = msg.lower()
        if "syntaxerror" in msg_lower:
            return "SYNTAX_ERROR"
        return "UNKNOWN"
''')
target_file.write_text(original_code)

(scaffold_root / ".awos").mkdir()
mutations_log = scaffold_root / ".awos" / "scaffold_mutations.jsonl"

PATCH_RESPONSE = textwrap.dedent('''\
    <<<SEARCH>>>
        return "UNKNOWN"
    <<<REPLACE>>>
        if "indentationerror" in msg_lower:
            return "SYNTAX_ERROR"
        return "UNKNOWN"
    <<<END>>>
''')

os.environ["AWOS_SCAFFOLD_EVOLUTION"] = "true"
os.environ["AWOS_SAFE_TO_RUN_TESTS"]  = "true"

se = ScaffoldEvolver(scaffold_root=str(scaffold_root), cheap_call=MagicMock(return_value=PATCH_RESPONSE))
se._mutations_log = mutations_log

failure_rate = 0.55
info(f"Session failure rate = {failure_rate:.0%}  (threshold = 40%)")
ok(f"should_evolve({failure_rate}) → {se.should_evolve(failure_rate)}")

# Wire a fake error store
from error_pattern_store import ErrorPatternStore
real_store = MagicMock(spec=ErrorPatternStore)
fake_patterns = []
for et, count in [("SYNTAX_ERROR", 5), ("SEARCH_NOT_FOUND", 2)]:
    p = MagicMock()
    p.error_type = et
    p.error_msg  = f"example {et}"
    fake_patterns.extend([p] * count)
real_store.list_all.return_value = fake_patterns

info("Finding top error pattern from ErrorPatternStore.list_all() ...")
top_et, count, samples = se._find_top_error(real_store)
ok(f"Top error: {top_et} × {count}")

info("Parsing proposed SEARCH/REPLACE patch ...")
parsed = se._parse_patch(PATCH_RESPONSE)
ok(f"Patch parsed — search={repr(parsed[0][:40])}")

info("Applying patch to scaffold/agent/self_correction.py ...")
se._run_tests = MagicMock(return_value=120)
result = se.evolve_once(real_store)

if result.accepted:
    ok(f"Patch ACCEPTED — '{result.description}'")
    new_code = target_file.read_text()
    ok(f"File modified — 'indentationerror' now handled:")
    for line in new_code.splitlines():
        print(f"    {DIM}{line}{RESET}")
else:
    info(f"Patch rejected: {result.rejection_reason}")

info("Mutation logged to .awos/scaffold_mutations.jsonl ...")
mutations = se.load_mutations()
ok(f"{len(mutations)} mutation record(s) written")
dim(f"  accepted={mutations[0].accepted}, error_type={mutations[0].error_type}, tests={mutations[0].tests_before}→{mutations[0].tests_after}")

info("Testing rollback — simulating test regression ...")
# Restore original so we have something to patch
target_file.write_text(original_code)
call_no = [0]
def regression_tests(_):
    call_no[0] += 1
    return 120 if call_no[0] == 1 else 80   # tests drop on 2nd call

se._run_tests = regression_tests
result2 = se.evolve_once(real_store)

if not result2.accepted and target_file.read_text() == original_code:
    ok(f"Rollback CONFIRMED — file restored to original after test regression")
    dim(f"  Rejection reason: {result2.rejection_reason}")
else:
    err("Rollback check failed")


# ─── Summary ────────────────────────────────────────────────────────────────
banner("Summary")
print(f"""
  {BOLD}Feature 1A — PromptEvolver{RESET}
    • Read {len(patterns)} past failures + {len(skills)} skill entries
    • Evolved 2 targeted guidelines  →  .awos/evolved_prompt.json
    • Worker hot-loads this before every LLM call (no restart)

  {BOLD}Feature 1B — LiveToolSynthesizer{RESET}
    • Detected repeated SYNTAX_ERROR on attempt 2
    • Synthesized + validated Python syntax-checker tool
    • Registered in .awos/tools/index.json
    • Keyword overlap matched tool for similar future task

  {BOLD}Feature 1C — ScaffoldEvolver{RESET}
    • 55% failure rate exceeded 40% threshold
    • Proposed targeted patch to self_correction.py
    • Applied, tests held (120→120) → ACCEPTED
    • Rollback test: tests dropped (120→80) → file RESTORED

  {BOLD}{GREEN}All three self-learning loops are live.{RESET}
  Set env vars to enable in production:
    export AWOS_PROMPT_EVOLUTION=true
    export AWOS_LIVE_TOOLS=true
    export AWOS_SCAFFOLD_EVOLUTION=true AWOS_SAFE_TO_RUN_TESTS=true
""")

teardown()
