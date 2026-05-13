# RL / LLM Training Protocol

> **Context:** Patterns for fine-tuning LLMs with reinforcement learning (GRPO, PPO, DPO).
> Distilled from the entity_ai / research_SLAI project (Qwen3-1.7B + GRPO on Kaggle, May 2026).
> Apply to any project that trains a language model with a reward signal.

---

## 1. Staged Reward Design (Variance Insurance)

### The Problem

GRPO and other relative-ranking RL algorithms compute advantages from reward *variance* across a group of completions. If all completions receive identical rewards:

```
variance = 0 → advantages = 0 → gradient = 0 → no learning
```

This happens silently. Training appears to run but the model never improves.

### The Pattern

Decompose the reward into levels ordered by difficulty:

```
Level 1 — Format   (+0.2 / -0.1): did the model output a code fence?
Level 2 — Syntax   (+0.3):         does the extracted code compile?
Level 3 — Execution (-0.5 to +1.5): do the tests actually pass?
```

Levels 1–2 guarantee variance even when all completions fail Level 3.
Early in training the model learns format; later it learns correctness.
Each level contributes separable gradient signal.

### The Rule

Always have at least one reward level the model can *almost certainly* win or lose in early training. Never start with only a hard end-signal reward (e.g., test pass/fail alone).

### Implementation

```python
def reward_fn(prompt, completion):
    # Level 1: Format
    has_fence = bool(re.search(r'```', completion))
    format_r  = 0.2 if has_fence else -0.1

    # Level 2: Syntax
    fm   = re.search(r'```(?:python)?\s*\n(.*?)```', completion, re.DOTALL)
    code = fm.group(1).strip() if fm else completion.strip()
    try:
        compile(code, '<string>', 'exec')
        syntax_r = 0.3
    except SyntaxError:
        syntax_r = 0.0

    # Level 3: Execution
    exec_r = exec_reward(prompt, completion, test_source=test_src).total

    return format_r + syntax_r + exec_r
```

---

## 2. SFT Warm-Up Before RL (Format Seeding)

### The Problem

If the model has never seen the expected output format, every RL rollout gets the same failure penalty → variance = 0 → no gradient. DeepSeek-R1 solved this with an SFT cold start before GRPO.

### The Pattern

Before running RL, run supervised fine-tuning on reference solutions for 2–3 epochs:
- **Prompt:** same chat-template format the RL loop uses
- **Completion:** reference solution in the exact output format (e.g., ` ```python ... ``` `)
- Push the SFT checkpoint; RL starts from it
- Gate with a flag (`RUN_SFT = False`) to skip on reruns

### The Rule

SFT warm-up is mandatory for any model starting from a base checkpoint on a structured-output RL task. Skip only when loading a checkpoint that already knows the output format.

### Implementation

```python
RUN_SFT    = True   # set False after first successful run
SFT_EPOCHS = 3
SFT_LR     = 2e-4

for data in raw_tasks:
    prompt     = _apply_chat(format_task_prompt(data['description'], test_src, stage))
    completion = f"```python\n{data['reference_solution'].strip()}\n```"
    sft_records.append({"prompt": prompt, "completion": completion})

# Train for SFT_EPOCHS then push checkpoint to HF Hub before starting RL
```

---

## 3. Chat Template Wrapping in RL Rollouts

### The Problem

Chat-instruction models (Qwen3, Llama-3-Instruct, etc.) generate free text when called without a chat template. RL rollouts without the template produce prose, not code → all completions fail execution → no variance.

### The Rule

Always wrap prompts with `tokenizer.apply_chat_template()` before rollout.
For Qwen3, set `enable_thinking=False` — otherwise the model burns its response budget on `<think>...</think>` before writing any code.

### Implementation

```python
def _apply_chat(prompt: str) -> str:
    messages = [{"role": "user", "content": prompt}]
    return tokenizer.apply_chat_template(
        messages,
        tokenize=False,
        add_generation_prompt=True,
        enable_thinking=False,   # Qwen3 specific
    )
```

Apply this to every prompt before passing it to the RL algorithm or rollout sampler.

---

## 4. ZPD Curriculum — Train at the Learning Frontier

### The Problem

Training on tasks that are too easy (solve rate > 70%) or too hard (solve rate < 30%) produces near-zero gradient in both cases — either all succeed or all fail.

### The Pattern

Zone of Proximal Development (ZPD) — prefer tasks where the current solve rate is 30–70%:

| Solve rate | Signal | Action |
|---|---|---|
| < 30% | Too hard — all fail | Deprioritise; return later |
| 30–70% | Frontier — max variance | **Prioritise these** |
| > 70% | Too easy — all pass | Mark mastered; use for spaced repetition |

### The Rule

At each iteration, select frontier tasks first, fill remainder randomly.
Combine with a **forgetting detector** (spaced repetition) to recheck mastered tasks — models regress.

### Implementation

```python
def select_tasks(n):
    frontier_ids = set(zpd.frontier_tasks(low=0.3, high=0.7))
    forgotten_ids = set(forgetting.forgotten_tasks())
    priority = [t for t in all_tasks if t.task_id in frontier_ids | forgotten_ids]
    rest     = [t for t in all_tasks if t.task_id not in frontier_ids | forgotten_ids]
    random.shuffle(rest)
    return (priority + rest)[:n]
```

---

## 5. Hub-as-Durable-Storage (Ephemeral Compute)

### The Problem

Kaggle and many cloud GPU environments wipe the working directory on session restart. Hours of training are lost if a kernel crashes and state was not persisted externally.

### The Pattern

Use HuggingFace Hub dataset repos as durable key-value storage for training state:
- After each iteration: push state JSON, concept graphs, ZPD history, rollout caches
- On session start: pull all state files before resuming
- Keep a private `<model-repo>-state` dataset repo for this purpose

### The Rule

**Push state after every iteration, not just at the end.** The last successful push is the only durable recovery point.

### Implementation

```python
def hub_push(local_path, hub_name):
    upload_file(
        path_or_fileobj=str(local_path), path_in_repo=hub_name,
        repo_id=HF_STATE_REPO, repo_type="dataset", token=os.environ["HF_TOKEN"],
    )

def hub_pull(hub_name, local_path):
    try:
        src = hf_hub_download(
            repo_id=HF_STATE_REPO, filename=hub_name,
            repo_type="dataset", token=os.environ["HF_TOKEN"],
        )
        shutil.copy(src, local_path)
        return True
    except Exception:
        return False

# At end of every iteration:
hub_push("/kaggle/working/training_state.json", "training_state.json")
hub_push("/kaggle/working/concept_graph.json",  "concept_graph.json")
```

Also cache RL rollouts to disk and push them — rollout generation is the most expensive part; paying it twice is wasteful.

---

## 6. Reward-Collapse Auto-Stop

### The Pattern

Monitor `reward_std` (standard deviation across the rollout group) at each iteration.
If it stays below a threshold for N consecutive iterations, stop automatically:

```python
COLLAPSE_STD_THRESHOLD = 0.002
COLLAPSE_PATIENCE      = 5

if reward_std < COLLAPSE_STD_THRESHOLD:
    consecutive_collapse += 1
    if consecutive_collapse >= COLLAPSE_PATIENCE:
        save_session(...)
        raise RuntimeError("Reward collapsed — all completions identical. Check for NaN/Inf or lower temperature.")
else:
    consecutive_collapse = 0
```

### The Rule

Continuing to train past reward collapse corrupts the model — you are gradient-descending on noise.

Tighten the threshold when using a staged reward (format/syntax levels already provide variance floor) — the threshold should reflect the minimum acceptable signal, not just any nonzero value.

**Diagnostics on collapse:**
- Check for NaN/Inf in model parameters
- Lower sampling temperature (model may have become deterministic)
- Reload from last good checkpoint and reduce learning rate

---

## 7. Debug Checklist for Zero-Gradient Runs

If loss is 0 or reward_std is 0 from the start:

> **Research first.** Before checking any of the boxes below, copy the exact error/crash message and search GitHub Issues + PyTorch/HF forums for it. Write findings to `docs/debug_<name>.md`. See `DEBUG_PROTOCOL.md §Step 0`.

- [ ] Are prompts wrapped with chat template?
- [ ] Did SFT warm-up run successfully and checkpoint was pushed?
- [ ] Is the reward function being called at all? (add a print)
- [ ] Are all 8 completions actually different? (log first 8 in debug mode)
- [ ] Is the model in `train()` mode with `requires_grad=True` on LoRA params?
- [ ] Is gradient checkpointing enabled with `enable_input_require_grads()`?
- [ ] Are prompt tokens correctly masked in the loss (labels = -100)?

---

## Related

- [[AWOS]] — master workflow OS
- [[IMPLEMENTATION_PROTOCOL]] — general implementation discipline
- [[DEBUG_PROTOCOL]] — when training runs fail and the fix is not obvious
- [[MEMORY_PROTOCOL]] — persisting training state across sessions
