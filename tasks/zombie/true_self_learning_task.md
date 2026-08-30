# True Self-Learning for AWOS — Spec & Implementation Plan

**Status:** Spec (ready to implement)
**Date:** 2026-05-26
**Research basis:** Live-SWE-agent (arXiv:2511.13646), DGM (arXiv:2505.22954),
  OPRO / PromptBreeder (arXiv:2309.03409 / 2309.16797), RLTF (arXiv:2307.04349)

---

## Why Everything So Far Is Not Self-Learning

| Module | What we call it | What it really is | Why it doesn't count |
|---|---|---|---|
| `ErrorPatternStore` → inject | "Reflexion memory" | RAG | LLM reasons; AWOS code unchanged |
| `VectorMemory` → inject | "Semantic retrieval" | RAG | LLM reasons; AWOS code unchanged |
| `SkillLibrary` → inject | "Skill KB" | RAG | LLM reasons; AWOS code unchanged |
| `SelfCorrectionEngine` | "Self-correction" | Hardcoded rules | Static `if/elif`, never changes |
| `MCTSSearchEngine` | "MCTS search" | LLM × N calls | Search, not learning |
| `MLRouter` (LinUCB) | "Bandit routing" | **Actual learning** ✓ | Policy updates from outcomes |

**The gap:** AWOS currently uses the LLM as an oracle and manages its context.
Real self-learning means AWOS's own code, prompts, and tools **change from experience**.

**Three levels of real self-learning, zero GPU required:**

```
Level 1A: Worker prompt evolves from empirical session outcomes  (OPRO-inspired)
Level 1B: New Python tools synthesized on-the-fly during failures  (Live-SWE-agent)
Level 1C: Scaffold source code self-patches from failure analysis  (DGM mini-scale)
```

---

## Feature 1A — PromptEvolver

### What it does
After every `AWOS_PROMPT_EVOLVE_EVERY=10` completed sessions, the agent:
1. Reads `.awos/error_patterns.jsonl` (failure critiques) and `.awos/skills/index.json` (success patterns)
2. Calls a cheap LLM with the current worker prompt + evidence summary
3. Receives a JSON diff of prompt improvements
4. Saves the improved prompt to `.awos/evolved_prompt.json`
5. `Worker` loads this file at construction and merges it into its base prompt

**Why this is real learning:** The worker's actual instruction text changes based on empirical outcomes. Not the LLM's weights — AWOS's own persisted config evolves.

### New file
`scaffold/agent/prompt_evolver.py`

### Interface
```python
class PromptEvolver:
    def __init__(self, store_path: str = ".awos", cheap_call: Callable = None): ...

    def should_evolve(self, session_count: int) -> bool:
        """Return True every AWOS_PROMPT_EVOLVE_EVERY sessions."""

    def evolve(
        self,
        current_prompt_template: str,
        error_store: ErrorPatternStore,
        skill_library: SkillLibrary,
    ) -> str:
        """Return an improved prompt template or original if LLM fails/regresses."""

    def load_evolved_prompt(self, base_prompt: str) -> str:
        """Load .awos/evolved_prompt.json if present, else return base_prompt."""
```

### Algorithm
```
1. Read last 50 records from error_patterns.jsonl
   Group by error_type → find top 3 recurring failure types
   
2. Read skill_library — top 5 entries by win_rate
   
3. Build meta-prompt:
   """You are improving a code-editing agent's worker instruction prompt.
   
   CURRENT PROMPT TEMPLATE:
   {current_prompt}
   
   EVIDENCE FROM LAST {n} SESSIONS:
   Top 3 failure patterns: {failure_summary}
   Top 5 success patterns: {success_summary}
   
   Propose EXACTLY 3 targeted improvements. Output ONLY valid JSON:
   {
     "changes": [
       {"section": "...", "old_text": "...", "new_text": "...", "reason": "..."}
     ],
     "confidence": 0.0-1.0
   }
   Only propose changes with confidence >= 0.7. Output {} if no confident changes."""

4. Call cheap_call(meta_prompt) → parse JSON
   - Validate: each change must have old_text present in current prompt
   - Apply changes if valid
   - If LLM output is invalid JSON → keep original prompt

5. Save to .awos/evolved_prompt.json:
   {
     "prompt": "<evolved text>",
     "evolved_at": "2026-05-26T...",
     "session_count": 42,
     "changes_applied": 3
   }

6. Worker.execute_task loads this at call time (not startup — supports hot-reload)
```

### Wiring into existing code
- `orchestrator.py` `__init__`: instantiate `PromptEvolver`
- `orchestrator.execute_feature()`: after goal completes, increment session counter, call `evolver.should_evolve()` → `evolver.evolve()`
- `worker.py` `_build_prompt()`: call `evolver.load_evolved_prompt(base)` before returning

### Success criterion
- `pytest tests/test_prompt_evolver.py` passes
- After injecting synthetic failure data, `evolve()` returns a different prompt
- `Worker` integration: evolved prompt appears in constructed prompt string

### Risk mitigation
- Changes validated (old_text must exist in prompt) — no hallucinated insertions
- Original prompt kept if confidence < 0.7 or JSON parse fails
- Entire feature gated by `AWOS_PROMPT_EVOLUTION=true` env var

---

## Feature 1B — LiveToolSynthesizer

### What it does
When the worker fails on the **same task pattern twice**, a step-reflection prompt fires:
"Would writing a reusable Python helper script help solve this class of problem?"

If yes, the agent writes `.awos/tools/custom_<hash>.py`, runs it against the current task, and on success registers it. Future tasks with semantically similar descriptions receive the tool's output as context.

**Why this is real learning:** The agent's **executable toolset grows from encountered problems**. No human programmer added these tools. Each session, the toolkit may be larger than the last.

### New file
`scaffold/agent/live_tool_synth.py`

### Interface
```python
class LiveToolSynthesizer:
    def __init__(self, tools_dir: str = ".awos/tools", cheap_call: Callable = None): ...

    def reflect(
        self,
        task: dict,
        error: str,
        attempt: int,
    ) -> Optional["SynthesizedTool"]:
        """On attempt >= 2 failure: ask LLM if a tool would help.
        Returns SynthesizedTool if synthesized, None otherwise."""

    def run_tool(self, tool: "SynthesizedTool", task: dict) -> str:
        """Execute the tool script and return stdout (capped 1000 chars)."""

    def find_relevant_tool(self, task: dict) -> Optional["SynthesizedTool"]:
        """Find a persisted tool whose description matches the task (keyword overlap)."""

    def list_tools(self) -> List["SynthesizedTool"]:
        """Return all persisted tools."""

@dataclass
class SynthesizedTool:
    name: str           # e.g. "ast_validator"
    description: str    # one-line docstring
    script_path: str    # .awos/tools/custom_<hash>.py
    trigger_pattern: str  # task keyword pattern that triggered creation
    created_at: str
    use_count: int = 0
```

### Algorithm
```
Trigger: orchestrator._execute_single_task() — attempt >= 2 AND same error_type as attempt 1

Step 1 — Reflection prompt (cheap, max_tokens=80):
  "You are helping a coding agent that failed twice on: {task_action}.
   Error: {error}
   Would writing a Python helper script (that can be called as a subprocess) 
   help avoid this error? Answer JSON: {"should_create": bool, "tool_purpose": "..."}"

Step 2 — If should_create=True, synthesis prompt (max_tokens=400):
  "Write a Python script called {tool_name}.py that {tool_purpose}.
   The script reads from stdin (JSON task dict) and prints its output to stdout.
   Include a clear docstring. Output ONLY the Python code, no explanation."

Step 3 — Validate synthesized code:
  - ast.parse() check — reject if SyntaxError
  - Run with timeout=5s on synthetic input
  - Reject if raises uncaught exception

Step 4 — Persist to .awos/tools/custom_<hash>.py
  Register in .awos/tools/index.json:
  {name, description, script_path, trigger_pattern, created_at, use_count}

Step 5 — In subsequent attempts:
  find_relevant_tool(task) → keyword overlap score ≥ 0.5
  If found: run tool, prepend output to worker prompt as [TOOL OUTPUT]
```

### Wiring into existing code
- `orchestrator.py` `__init__`: instantiate `LiveToolSynthesizer`
- `orchestrator._execute_single_task()` in the attempt loop: on attempt 2 failure, call `synth.reflect()`; before each attempt, call `synth.find_relevant_tool()` and inject output

### Success criterion
- `pytest tests/test_live_tool_synth.py` passes
- Synthesized tool file appears in `.awos/tools/`
- `find_relevant_tool` retrieves correct tool on keyword match
- End-to-end: a task that fails with "file not found" pattern synthesizes a path-validator tool

### Risk mitigation
- `ast.parse()` guard — no syntactically broken tools saved
- Subprocess timeout=5s — no infinite loops
- Tool output capped at 1000 chars before prompt injection
- Gated by `AWOS_LIVE_TOOLS=true` env var

---

## Feature 1C — ScaffoldEvolver

### What it does
After any session where the task failure rate exceeds `AWOS_EVOLVE_THRESHOLD=40%`,
the agent:
1. Identifies the top 1 recurring error pattern from `ErrorPatternStore`
2. Proposes a targeted code change to a scaffold file (`self_correction.py` or `worker.py`)
3. Applies it via the existing `Worker.execute_task` SEARCH/REPLACE mechanism — **the agent edits its own code**
4. Runs `pytest tests/ -q` (gated by `AWOS_SAFE_TO_RUN_TESTS`)
5. Keeps the change only if test pass count is ≥ pre-change count
6. Records mutation in `.awos/scaffold_mutations.jsonl`

**Why this is real learning:** This is DGM at small scale. The agent's own `.py` source files change based on empirical evidence. AWOS literally rewrites itself.

**This builds on `self_improver.py`** (already exists) but adds the empirical gating loop.

### New file
`scaffold/agent/scaffold_evolver.py`

### Interface
```python
class ScaffoldEvolver:
    def __init__(
        self,
        scaffold_root: str = "scaffold/agent",
        worker: "Worker" = None,
        cheap_call: Callable = None,
    ): ...

    def should_evolve(self, session_failure_rate: float) -> bool:
        """True if failure_rate >= AWOS_EVOLVE_THRESHOLD (default 0.40)."""

    def evolve_once(
        self,
        error_store: ErrorPatternStore,
        test_runner: "TestRunner",
    ) -> "MutationResult":
        """
        Run one DGM-style iteration:
        1. Find top failure pattern
        2. Propose + apply code change
        3. Run tests
        4. Accept or rollback
        Returns MutationResult with accepted/rejected + details.
        """

    def rollback_last(self) -> bool:
        """Revert the last accepted mutation (via git checkout or stored backup)."""

@dataclass
class MutationResult:
    __test__ = False
    accepted: bool
    target_file: str
    description: str
    tests_before: int
    tests_after: int
    timestamp: str
```

### Algorithm
```
Pre-check:
  - AWOS_SAFE_TO_RUN_TESTS must be "true" (never mutate without test validation)
  - AWOS_SCAFFOLD_EVOLUTION must be "true"
  - git must be clean (no uncommitted changes) — abort if dirty

Step 1 — Baseline test run:
  TestRunner.run(changed_files=[]) → tests_before (pass count)
  Store result. If baseline < N_MIN_TESTS (=100) → abort (too risky)

Step 2 — Find mutation target:
  Read last 100 error_patterns.jsonl records
  Group by error_type → pick type with highest count
  Map error_type → target scaffold file:
    SYNTAX_ERROR        → self_correction.py (_Recipe hints)
    SEARCH_NOT_FOUND    → worker.py (context truncation logic)
    JSON_DECODE_ERROR   → worker.py (response parsing)
    MAX_RETRIES         → self_correction.py (retry escalation)
    default             → worker.py

Step 3 — Mutation proposal prompt (max_tokens=600):
  "You are improving a coding agent's {target_file}.
   
   TOP FAILURE PATTERN: {error_type} occurred {count} times.
   Recent error messages:
   {sample_errors}
   
   CURRENT FILE (relevant section only):
   {file_excerpt}
   
   Propose ONE small, targeted improvement that reduces {error_type} failures.
   Use SEARCH/REPLACE format. Be conservative — minimal change only.
   
   <<<SEARCH>>>
   <exact existing text>
   <<<REPLACE>>>
   <improved text>
   <<<END>>>"

Step 4 — Apply via Worker.execute_task on scaffold file
  task = {action: "Self-improve: reduce {error_type} errors", file: target_file, ...}
  search_replace = parse_search_replace(llm_output)

Step 5 — Validate with backup:
  backup = read(target_file)
  apply patch
  result = TestRunner.run([target_file])
  
  if result.pass_count >= tests_before:
    record_mutation(ACCEPTED)
    append to .awos/scaffold_mutations.jsonl
  else:
    write(target_file, backup)   ← rollback
    record_mutation(REJECTED)

Step 6 — One mutation per session max (conservative)
```

### Wiring into existing code
- `orchestrator.py` `execute_feature()`: at the end, compute `failure_rate`; if `evolver.should_evolve(failure_rate)` → call `evolver.evolve_once()`
- `orchestrator.py` `__init__`: instantiate `ScaffoldEvolver`

### Success criterion
- `pytest tests/test_scaffold_evolver.py` passes
- With synthetic error data + mock `TestRunner` that returns constant pass count, a mutation gets accepted
- With mock `TestRunner` that returns lower count, mutation is rejected and file is restored
- `.awos/scaffold_mutations.jsonl` records both outcomes correctly

### Risk mitigation
- **Never runs without `AWOS_SAFE_TO_RUN_TESTS=true`** — hard abort
- **Baseline test count** must be ≥ 100 before any mutation
- **File backup** written before apply, restored on test regression
- **Git clean check** — will not mutate if working tree is dirty
- **One mutation per session** — no runaway self-modification
- **Allowlist of target files** — only `self_correction.py` and `worker.py` allowed
- Gated by `AWOS_SCAFFOLD_EVOLUTION=true` env var

---

## Implementation Sequence (Atomic Steps)

### Step 1 — PromptEvolver core (1A)
**Files:** `scaffold/agent/prompt_evolver.py` (new), `tests/test_prompt_evolver.py` (new)
**Touches:** nothing yet (standalone)
**Done when:** `pytest tests/test_prompt_evolver.py` passes

### Step 2 — Wire PromptEvolver into Worker + Orchestrator (1A)
**Files:** `scaffold/agent/worker.py`, `scaffold/agent/orchestrator.py`
**Changes:**
- `Worker`: load evolved prompt in `execute_task`
- `Orchestrator.__init__`: instantiate `PromptEvolver`
- `Orchestrator.execute_feature`: trigger evolution check after goal completes
**Done when:** `pytest tests/test_prompt_evolver.py tests/test_phase1_integration.py` passes

### Step 3 — LiveToolSynthesizer core (1B)
**Files:** `scaffold/agent/live_tool_synth.py` (new), `tests/test_live_tool_synth.py` (new)
**Touches:** nothing yet (standalone)
**Done when:** `pytest tests/test_live_tool_synth.py` passes

### Step 4 — Wire LiveToolSynthesizer into Orchestrator (1B)
**Files:** `scaffold/agent/orchestrator.py`
**Changes:** attempt-loop failure reflection + pre-attempt tool injection
**Done when:** full test suite 0 regressions

### Step 5 — ScaffoldEvolver core (1C)
**Files:** `scaffold/agent/scaffold_evolver.py` (new), `tests/test_scaffold_evolver.py` (new)
**Touches:** nothing yet (standalone, uses mocked TestRunner + Worker)
**Done when:** `pytest tests/test_scaffold_evolver.py` passes

### Step 6 — Wire ScaffoldEvolver into Orchestrator (1C)
**Files:** `scaffold/agent/orchestrator.py`
**Changes:** post-goal failure rate check → `evolver.evolve_once()`
**Done when:** full test suite 0 regressions

### Step 7 — Full test suite verification
**Command:** `pytest tests/ -q`
**Done when:** ≥ 674 pass, 0 new failures

---

## Data Flow Diagram

```
Session N completes
        │
        ├──► ErrorPatternStore.save(critique)     [always]
        ├──► SkillLibrary.record(success)          [on success]
        ├──► VectorMemory.store_outcome(...)       [always]
        │
        ├──► PromptEvolver.should_evolve()?        [every 10 sessions]
        │         └──► evolve() → .awos/evolved_prompt.json
        │                  └──► Worker picks up on next execute_task call
        │
        ├──► ScaffoldEvolver.should_evolve(failure_rate)?  [if rate > 40%]
        │         └──► evolve_once() → mutate .py → run pytest → keep/rollback
        │
Session N+1 starts
        │
        ├──► Worker loads .awos/evolved_prompt.json       [1A active]
        ├──► Attempt 2 failure? → LiveToolSynth.reflect() [1B active]
        │         └──► .awos/tools/custom_X.py created
        ├──► find_relevant_tool() → inject tool output    [1B active]
```

---

## Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `AWOS_PROMPT_EVOLUTION` | `false` | Enable Feature 1A |
| `AWOS_PROMPT_EVOLVE_EVERY` | `10` | Sessions between prompt evolutions |
| `AWOS_LIVE_TOOLS` | `false` | Enable Feature 1B |
| `AWOS_SCAFFOLD_EVOLUTION` | `false` | Enable Feature 1C |
| `AWOS_EVOLVE_THRESHOLD` | `0.40` | Failure rate that triggers scaffold evolution |
| `AWOS_SAFE_TO_RUN_TESTS` | `false` | Required for Feature 1C (hard gate) |

**All features are opt-in. Default AWOS behavior is unchanged.**

---

## Honest Limitations

- **1A and 1B** are gradient-free. The LLM's weights never change. What changes is AWOS's
  own persisted config / toolset. This is real behavioral learning, not parameter learning.
- **1C** modifies Python source. The scaffold's logic changes. This is the closest to
  true algorithmic self-improvement without gradient descent.
- **Tier 2 (LoRA fine-tuning)** is specced separately when we have ≥ 500 task outcomes
  in SkillLibrary. Currently ~0. Come back to this when data exists.
- None of these replace the LLM. They make AWOS smarter about **how** it uses the LLM.
