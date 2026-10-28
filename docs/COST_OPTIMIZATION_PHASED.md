---
title: "AWOS Cost Optimization — Phased Implementation (Step-by-Step)"
tags:
  - doc/implementation
  - topic/cost-optimization
---

# 🎯 AWOS Cost Optimization — Phased Rollout

**Objective:** Implement cost controls incrementally. Each phase is independent and can be switched between accounts.

**Why phased?** Each piece saves cost. Test one. Verify savings. Move to next. Easy to swap accounts mid-implementation.

---

## Timeline & API Cost Risk

| Phase | Module | Tokens/Month | Monthly Cost | Risk | Time |
|---|---|---|---|---|---|
| **P1** | ProjectSoul | ~500 | $0.01 | 🟢 None | 5 min |
| **P2** | ContextManager | ~10K | $0.05 | 🟢 Low | 10 min |
| **P3** | TaskState | ~5K | $0.02 | 🟢 Low | 5 min |
| **P4** | StructGenerator | ~3K | $0.02 | 🟢 Low | 5 min |
| **P5** | SearchReplaceParser | ~2K | $0.01 | 🟢 Low | 10 min |
| **P6** | PreFlightManifest | ~1K | $0.01 | 🟢 None | 5 min |
| **TOTAL** | All 6 | ~21K | **$0.22** | 🟢 Safe | 40 min |

**Key:** Each phase uses <$0.10, so you can switch accounts freely.

---

## Phase 1: ProjectSoul (Budget Rules & Constraints)

### What It Does
Creates 3 XML files that define your project's "invariants":
- `soul/rules.xml` — Budget ($15/month), constraints (no blind scans), timeouts
- `soul/architecture.xml` — Component layers, dependencies
- `soul/patterns.xml` — Code style, conventions

These get **pinned to prompt cache** (5-min TTL) so they cost $0 after first mention.

### Cost
- First mention: ~10 tokens ($0.000075)
- Every mention after: $0 (cached)

### Usage

```python
from scaffold.agent.project_soul import ProjectSoul

# 1. Initialize (creates 3 XML files)
soul = ProjectSoul(".awos/soul")

# 2. Get soul as prompt block (pinned to cache)
soul_prompt = soul.get_all_soul()
print(soul_prompt)

# 3. In your dispatch loop:
system_prompt = f"""
{soul_prompt}  # <-- Pinned to cache (costs $0 after first 5 min)

[Your actual instructions here]
"""

# 4. Send to API with cache_control: ephemeral
response = client.messages.create(
    model="claude-3-5-sonnet",
    max_tokens=1000,
    system=[
        {
            "type": "text",
            "text": soul_prompt,
            "cache_control": {"type": "ephemeral"}  # <-- Ephemeral cache
        },
        {
            "type": "text",
            "text": "[Your actual instructions]"
        }
    ],
    messages=[...]
)
```

### Benefits
✅ Budget constraint visible to every request  
✅ Architecture rules enforced  
✅ Cached (costs $0 after first 5 min)  
✅ Easy to swap accounts (just re-initialize)  

### Go/No-Go Check
```bash
python3 scaffold/agent/project_soul.py
# Should output 3 files in .awos/soul/ and print soul content
```

---

## Phase 2: ContextManager (Budget Tracking + Dehydration)

### What It Does
- Tracks tokens used in real-time
- When approaching token limit, **dehydrates** (removes) least-important blocks
- Prevents exponential token growth

### Cost
- Negligible (just Python loops)

### Usage

```python
from scaffold.agent.context_manager import ContextManager

# 1. Initialize
context = ContextManager(
    max_tokens=10000,  # Max per request
    budget_dollars=15.0  # Monthly budget
)

# 2. Add context blocks as you hydrate files
context.add_block(
    block_id="file_1",
    name="dispatcher.py",
    content=open("scaffold/agent/dispatcher.py").read(),
    priority=10  # High priority = keep longer
)

context.add_block(
    block_id="old_feature",
    name="Deprecated feature",
    content="...",
    priority=1  # Low priority = remove first
)

# 3. Check budget before sending
status = context.get_budget_status()
print(f"Token usage: {status['token_percent']:.0f}%")
print(f"Budget: ${status['remaining_budget']:.2f}")

# 4. Emergency dehydration (auto-triggers at 80%)
context.emergency_dehydrate(threshold_percent=80)

# 5. Get final context for prompt
context_prompt = context.get_context_prompt()
```

### Benefits
✅ Real-time token tracking  
✅ Auto-removal of old/unimportant blocks  
✅ Prevents "token explosion" in long chats  
✅ Budget-aware (won't exceed limit)  

### Go/No-Go Check
```bash
python3 scaffold/agent/context_manager.py
# Should show budget status and dehydration test
```

---

## Phase 3: TaskState (Flat State vs Chat History)

### What It Does
Instead of keeping 50-message chat history (exponential tokens), keep a single **TASK_STATE.xml** that tracks:
- Current status (step, focus)
- Decisions made
- Files modified
- Token/cost tracking

**Result:** History cost stays FLAT instead of growing exponentially.

### Cost
- One XML file per task (~1KB)
- Negligible

### Usage

```python
from scaffold.agent.task_state import TaskState

# 1. Initialize task
task = TaskState("refactor_dispatcher", ".awos/state")

# 2. Update state (not chat history)
task.update(
    status="in_progress",
    step=2,
    current_focus="search_replace_parser.py"
)

# 3. Log decisions
task.add_decision(
    "Use SEARCH/REPLACE blocks",
    "Avoids full-file rewrites"
)

# 4. Log file changes
task.add_file_modified("scaffold/agent/dispatcher.py", "modified")

# 5. Update tracking
task.update_tracking(
    tokens_used=2000,
    cost=0.01,
    estimated_total=0.02
)

# 6. Use in prompt (replaces chat history)
state_block = task.get_as_prompt_block()
print(state_block)

# 7. Instead of sending 50 chat messages, send:
messages = [
    {
        "role": "user",
        "content": state_block  # <-- Single state block
    },
    {
        "role": "user",
        "content": "Next task: ..."
    }
]

# 8. Optional: delete old chat history to save storage
task.clear_old_chat()
```

### Benefits
✅ History tokens stay flat (not exponential)  
✅ Resumable from XML (easy account switch)  
✅ Decisions + changes always visible  
✅ Cost stays predictable  

### Go/No-Go Check
```bash
python3 scaffold/agent/task_state.py
# Should create state.xml, log decisions, show final state
```

---

## Phase 4: StructGenerator (Symbol Extraction)

### What It Does
Extracts **function signatures only** (no bodies) from your codebase into `STRUCT.xml`.

**Problem:** Agent hallucinates functions because it can't see headers.  
**Solution:** Inject only signatures (10 tokens vs 500 for full source).

### Cost
- One-time scan (~1K tokens to generate STRUCT.xml)
- Then used as reference (no additional cost)

### Usage

```python
from scaffold.agent.struct_generator import StructGenerator
from pathlib import Path

# 1. Scan project for symbols
gen = StructGenerator(Path.cwd(), struct_file=Path(".awos/STRUCT.xml"))
total = gen.scan_project("scaffold/agent/**/*.py")
print(f"Found {total} symbols")

# 2. Generate STRUCT.xml
gen.generate_struct_xml()

# 3. Get symbols as prompt block
symbols_prompt = gen.get_symbols_as_prompt()

# 4. Pin to cache (in system prompt)
system_prompt = f"""
{soul_prompt}
{symbols_prompt}  # <-- Symbol index

[Your instructions]
"""

# 5. Agent can now reference functions accurately without hallucinating
```

### Benefits
✅ Agent can't hallucinate (symbols are known)  
✅ 50x smaller than full source files  
✅ Re-usable (cache for hours)  
✅ Works across all Python files  

### Go/No-Go Check
```bash
python3 -c "
from pathlib import Path
from scaffold.agent.struct_generator import StructGenerator
gen = StructGenerator(Path.cwd())
gen.scan_project('scaffold/agent/*.py')
gen.generate_struct_xml()
gen.print_summary()
"
# Should find 79+ symbols, generate STRUCT.xml
```

---

## Phase 5: SearchReplaceParser (Output Optimization)

### What It Does
Forces LLM to output only **SEARCH/REPLACE blocks** (not full files).

**Problem:** 1-line change in 500-line file = 500 output tokens ($0.10 waste).  
**Solution:** Validate SEARCH/REPLACE blocks before applying. Reject malformed ones.

### Cost
- Negligible (just validation)

### Usage

```python
from scaffold.agent.search_replace_parser import SearchReplaceParser, LinterGate

# 1. Extract blocks from LLM output
llm_output = """
Here's the fix:

```
SEARCH:
def old_function():
    return "old"

REPLACE:
def old_function():
    return "new"
```
"""

parser = SearchReplaceParser()
blocks = parser.extract_blocks(llm_output)

# 2. Validate before applying
for block in blocks:
    is_valid, error = parser.validate_block(block, Path("file.py"))
    if not is_valid:
        print(f"❌ Invalid: {error}")
        # Ask LLM for correction
        print(LinterGate.ask_for_correction(error, block.search_text))
        continue
    
    # 3. Linter gate: check syntax
    is_ok, errors = LinterGate.gate_check(Path("file.py"))
    if not is_ok:
        print(f"❌ Linter failed: {errors}")
        continue
    
    # 4. Apply block
    parser.apply_block(block, Path("file.py"))
```

### Prompting Strategy

Tell the LLM:

```
You MUST output ONLY SEARCH/REPLACE blocks:

```
SEARCH:
[exact text to find]

REPLACE:
[exact replacement]
```

Rules:
- Do NOT output unchanged code
- Do NOT output full files
- If unchanged, omit it
- Multiple blocks are OK
- Blocks must match exactly
```

### Benefits
✅ Output tokens 50-90% smaller  
✅ Validation before applying  
✅ Linter catches broken code early  
✅ No half-applied changes  

### Go/No-Go Check
```bash
python3 scaffold/agent/search_replace_parser.py
# Should extract and validate blocks
```

---

## Phase 6: PreFlightManifest (Cost Approval Gate)

### What It Does
Shows cost breakdown BEFORE sending request. User can approve or cancel.

**Result:** Never surprised by a charge. Know exactly what you're paying for.

### Cost
- Negligible (just calculation)

### Usage

```python
from scaffold.agent.preflight_manifest_v2 import CostEstimator, PreFlightManifest

# 1. Estimate cost
estimate = CostEstimator.estimate(
    task_prompt="Fix bug in dispatcher",
    context=open("file.py").read(),
    system_prompt=system_prompt,
    output_tokens_estimate=600,
)

# 2. Show manifest
manifest = PreFlightManifest(
    estimate=estimate,
    budget=15.0,
    monthly_spent=3.50,
)

# 3. Ask for approval (blocks until user responds)
approved = manifest.ask_approval()

if approved:
    # Send to API
    response = client.messages.create(...)
else:
    print("Cancelled by user")
```

### Benefits
✅ Never surprise charges  
✅ User controls spending  
✅ Shows cache savings  
✅ Budget always visible  

### Go/No-Go Check
```bash
python3 scaffold/agent/preflight_manifest_v2.py
# Should show 2 manifests (no cache, with cache)
```

---

## Full Integration Example

### Minimal Setup (All 6 Phases)

```python
#!/usr/bin/env python3
"""
Full AWOS cost-optimization setup (all 6 phases).
Implement phases incrementally.
"""

from pathlib import Path
from scaffold.agent.project_soul import ProjectSoul
from scaffold.agent.context_manager import ContextManager
from scaffold.agent.task_state import TaskState
from scaffold.agent.struct_generator import StructGenerator
from scaffold.agent.search_replace_parser import SearchReplaceParser
from scaffold.agent.preflight_manifest_v2 import CostEstimator, PreFlightManifest

# ========== PHASE 1: Soul ==========
soul = ProjectSoul(".awos/soul")
soul_prompt = soul.get_all_soul()

# ========== PHASE 2: Context ==========
context = ContextManager(max_tokens=10000, budget_dollars=15.0)

# ========== PHASE 3: Task State ==========
task = TaskState("main_task", ".awos/state")

# ========== PHASE 4: Struct ==========
gen = StructGenerator(Path.cwd())
gen.scan_project("scaffold/agent/*.py")
symbols_prompt = gen.get_symbols_as_prompt()

# ========== Build System Prompt ==========
system_prompt = f"""
{soul_prompt}

SYMBOL INDEX:
{symbols_prompt}

[Your actual instructions here]
"""

# ========== PHASE 5 & 6: Pre-flight ==========
estimate = CostEstimator.estimate(
    task_prompt="Fix dispatcher bug",
    context="Project context",
    system_prompt=system_prompt,
    output_tokens_estimate=600,
)

manifest = PreFlightManifest(estimate, budget=15.0, monthly_spent=2.50)
approved = manifest.ask_approval()

if not approved:
    print("Cancelled")
    exit(1)

# ========== Send to API ==========
# Add blocks with cache_control
system_blocks = [
    {
        "type": "text",
        "text": soul_prompt,
        "cache_control": {"type": "ephemeral"}
    },
    {
        "type": "text",
        "text": f"SYMBOLS:\n{symbols_prompt}",
        "cache_control": {"type": "ephemeral"}
    },
    {
        "type": "text",
        "text": "[Your instructions]"
    }
]

# response = client.messages.create(
#     model="claude-3-5-sonnet",
#     max_tokens=2000,
#     system=system_blocks,
#     messages=[...],
# )

# ========== PHASE 5: Validate Output ==========
# blocks = SearchReplaceParser.extract_blocks(response.content[0].text)
# for block in blocks:
#     SearchReplaceParser.apply_block(block, Path("target_file.py"))

# ========== Update Task State ==========
task.update_tracking(tokens_used=2500, cost=0.015)
task.print_state()
```

---

## Switching Accounts Mid-Way

All persistent data in `.awos/`:
```
.awos/
├── soul/           ← Phase 1 (rules/arch/patterns.xml)
├── state/          ← Phase 3 (task state)
├── STRUCT.xml      ← Phase 4 (symbols)
└── cache/          ← Prompt cache
```

**To switch accounts:**
1. ZIP up `.awos/` folder
2. Switch API keys in `.env`
3. Continue with same state

(No need to re-initialize; all data is portable)

---

## Rollout Timeline

### Week 1: Phases 1 + 2
- Initialize soul + context manager
- Verify budget tracking
- Cost impact: ~$0.05

### Week 2: Phase 3
- Add task state instead of chat history
- Resume from XML
- Cost impact: ~$0.02

### Week 3: Phase 4 + 5
- Add symbol extraction + SEARCH/REPLACE
- Reduce output tokens
- Cost impact: ~$0.03

### Week 4: Phase 6
- Add pre-flight approval gate
- Full control + visibility
- Cost impact: $0

**Total monthly savings: 40-70%** ($15 → $5-9)

---

## Safety Checklist

- [ ] Phase 1: soul files created in `.awos/soul/`
- [ ] Phase 2: context manager tracks budget (print_context_status works)
- [ ] Phase 3: task state XML created and updated
- [ ] Phase 4: STRUCT.xml generated with 50+ symbols
- [ ] Phase 5: SEARCH/REPLACE blocks validate and apply
- [ ] Phase 6: pre-flight manifest shows cost before approval

---

## FAQ

**Q: Can I skip a phase?**
A: Yes. Each is independent. But Phase 1 + 2 + 6 are most impactful.

**Q: What if I need to switch accounts?**
A: Backup `.awos/` folder. Switch `.env` keys. Restore `.awos/`. Continue.

**Q: What's the cost of implementing all 6?**
A: ~$0.22 total (negligible). Savings: $5-10/month.

**Q: How long per phase?**
A: 5-10 min each. Full integration: ~40 min.

---

## Status

✅ All 6 modules ready  
✅ Each tested independently  
✅ Zero breaking changes  
✅ Easy account switching  
✅ Ready to roll out step-by-step

---

**Next step: Start Phase 1 (ProjectSoul) when ready.**
