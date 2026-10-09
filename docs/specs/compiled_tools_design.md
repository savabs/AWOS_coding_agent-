# Spec: compiled verified tools and Routines (Trick T8)

Status: **design, 2026-10-09. Steps 1–2 built as a skeleton on 2026-10-10 (see §12.1);
not wired into the orchestrator. E7 is not yet run.**
Sources: `docs/research/trick_book_2026-10.md` §1 #8, §2.1, §3 combination 1, §5 E7;
`docs/research/local_first_architecture_2026-10.md` §2.2, §2.5, §2.6.
VISION fit: Stage 1 (one excellent worker). It advances memory, capability reuse
(the capability cache) and verification, and it targets the objective directly:
a replayed Tool costs $0, takes seconds and makes zero LLM calls.
Not deferred: there is no training, RL or multi-agent work. Compilation is offline
prompting of an existing cloud model.

> **Naming.** "T0" means two things in this repo. The Trick-T0 spec
> (`local_provider_spec.md`) is the local model endpoint. The *T0 tier* in the
> architecture doc is zero-LLM replay. This spec uses **R0** for the replay tier,
> **R1** for Routine-guided local execution, and **L0** for the normal local
> attempt.

---

## 0. Summary

1. A strong model (DeepSeek V4 Flash, offline and batched) reads clusters of runs
   that already carry **L2 evidence**. From them it compiles one of two things:
   - a **Tool**: a parameterised program that can replay with zero LLM calls;
   - a **Routine**: a numbered procedure in which every step names a kernel tool.
     The local model fills in the arguments and the kernel runs each step.
2. A candidate is **admitted** only after the harness re-runs it on a fresh golden
   clone with **held-out parameters**, and a deterministic **post-state probe** passes.
   The probe must also fail on the starting state. The precondition guard must
   refuse every generated **near-miss**. The previously-solved regression set must
   not drop.
3. An admitted record is promoted to **R0** (zero-LLM replay) only when the 95%
   lower bound of Beta(1+s, 1+f) is at least 0.9. With no failures that needs
   **s ≥ 28**; with one failure it needs **s ≥ 44**.
4. **One failed probe demotes the record.** A precondition mismatch suspends it
   and is not counted as a failure.
5. At runtime the Gatekeeper retrieves **the top-1 record or none**. The goal is
   always passed verbatim. Routines carry procedures, never code fixes.
6. A replay is a **fixed, trusted program** under the sink policy. Injected data
   can fill typed arguments but can never add steps.

---

## 1. What the evidence says

The paper extraction is in §1.1. The design rules it implies:

| Rule | Evidence |
|---|---|
| Verification is the gain; program form adds about 1pp, and only when the program is actually callable | ASI Shopping ablation: unverified text 32.6, verified text 39.0 (+6.4), verified program as action 40.1; a verified program shown only as text 36.4 |
| Expect few admissions | ASI accepts about 15.6% of induced skills |
| A strong model makes the tools and a weak model uses them | LATM: weak users succeed when arguments are near the surface; GPT-3.5 made 0/5 working tools |
| Self-generated skills without a hard check do nothing | SkillsBench: curated +16pp overall, +4.5pp for SWE; self-generated ≈0 |
| Skills can break tasks that used to pass | Red team: about 59% of gross gain cancelled in one analysis. AWOS's own experience store collapsed job 12 from 2/2 to 0/2 (`ablation_experience.md`) |
| Retrieve top-1 or none | Routine: distractor Routines cost about 20pp. Small models choose skills poorly (Agent Skill Framework) |
| Distilled templates beat raw traces, but only on recurring work | APC: 85.5 vs 72; hit rate collapses on GAIA. Its admission used an oracle, which AWOS must not copy |
| A numbered, tool-named plan lifts small models | Routine: Qwen3-8B 35%→81% per step (teacher-forced, one private domain). Blueprint-First: deterministic engine, about 2x on TravelPlanner. COPE: guideline from big model, executed by small |
| LLM-judged admission is a trap | Trick Book §4; this is what sank the experience store |

### 1.1 Paper procedures (admission, representation, failure modes)

Read 2026-10-09 from the arXiv HTML pages. Quotes are close to the source but
were passed through a summarising fetcher. Items marked [unverified] were not
confirmed.

| Paper | Admission / verification | Representation and argument filling | Selection | Key numbers | Failure modes and maintenance |
|---|---|---|---|---|---|
| **LATM** 2305.17126 | GPT-4 writes a function from **3 demonstrations** (≤3 retries). It then writes unit tests from **3 held-out validation samples** and runs them. On failure it fixes the *test calls*, not the function. A tool that still fails is not admitted | A Python function plus usage demos. GPT-3.5 at temperature 0 fills the call | A "dispatcher" LLM decides whether a cached tool fits; this is the paper's "functional cache" | GPT-3.5 user: 66.4 → 79.7 (Logical Deduction); LATM 86.6 vs GPT-4 CoT 88.8 | A GPT-3.5 maker succeeded **0/5** and its tools overfit. No demotion |
| **ASI** 2504.06821 | Induced only from episodes an LLM evaluator judged correct. The trajectory is then **rewritten to call the new skill**, its tail truncated "to avoid spurious successes", and **re-executed**. Admitted only if (1) the task is solved, (2) at least one new skill is called, (3) "all skill-calling actions cause environment changes". **15.6%** of turns pass (AWM: 31.4%) | Typed Python functions, 2–5 primitive actions; some hard-coded site constants remain | None: verified skills are added to the action space | WebArena 32.7 → 40.4 (AWM 36.3), 15.3% fewer steps. Shopping ablation: unverified text 32.6, verified program in memory 36.4, verified text 39.0, verified program as action 40.1 | Skills broke on a new site (`sort_by_listings`). Stale skills are simply left unused; no removal. A program shown as text but not callable cost 2.6pp |
| **SkillWeaver** 2504.07079 | An LLM proposes tasks, then practises them; an **LLM reward model** judges success; the successes become Playwright APIs, with static analysis. "Honing": APIs with no parameters run as a unit test; for parameterised APIs the LLM generates parameter values as test cases. [unverified: what happens on a failure; survival counts] | Python Playwright functions; docstrings carry prerequisite states and a usage log | An API-selection module filters for relevance and **removes APIs whose preconditions fail** | WebArena +31.8% rel; real sites +39.8% rel; GPT-4o-mini gains up to +54.3% | Wrong API recognised; wrong argument values; worse than human-made APIs on GitLab and Maps. No pruning |
| **APC** 2506.14852 | Templates come only from successful runs. A rule-based filter, then an LLM filter, strips entity names and numbers. **No verification beyond the run having succeeded** | A plan template; LLaMA-3.1-8B adapts it | A small LM extracts **one intent keyword**, matched exactly (16 µs, vs 148 ms fuzzy). Embedding similarity was rejected because it "overemphasizes context-specific details" | Cost −50.3%, latency −27.3%, 96.6% of accuracy retained. Template 85.5 vs full history 72.0; fuzzy 80% match 83 | **Hit rate collapses on GAIA** (non-recurring work); cold start. LRU eviction only, no invalidation |
| **Routine** 2507.14447 | **Experts draft, GPT-4o refines.** No automatic verification. Qwen3-14B: expert draft 70.9, AI-refined 76.7, annotated 83.3 | Numbered steps with an **explicit tool name**, optional I/O descriptions, and branches. The executor fills the parameters | Similarity between the procedure description and the task | GPT-4o 41.1 → 96.3; Qwen3-14B 32.6 → 83.3; Qwen3-8B 81.3 with one Routine. Removing tool names: 83.3 → 71.9. Adding I/O descriptions: Qwen2.5-7B 49.7 → 60.6 | **Distractors (Table 5):** one correct Routine plus 1 unrelated one: GPT-4o 96.3 → 76.6, Qwen3-14B 83.3 → 63.2, Qwen3-8B 81.3 → 66.0. The damage is **non-monotonic**: 2 Routines are worst, 5 partly recover (the model tries to *merge* steps). No maintenance procedure |
| **Blueprint First** 2508.02721 | Human and LLM co-author a deterministic Python blueprint (87–332 LOC); >90% of 53 workflows converge in ≤5 rounds. Code-level `@precondition` / postconditions | The code calls `run_llm` only for bounded subtasks | One blueprint per task class | TravelPlanner 35.6 vs 18.0; with the same rules in the baseline's prompt, still 37.2 vs 24.5 (the gain comes from structure) | Unsuited to open-ended work. A broken assumption halts at a named node, which makes failures easy to locate |
| **COPE** 2506.11578 | Plans are not cached. Small model first (accepted at consensus ≥0.75, or **tests pass** for code). Then a large-model guideline executed by the small model (≥0.5). Then the large model alone | A goal plus a guideline in text | — | MBPP 66.4% at $1,279 vs 64.0% at $4,889; plans 74.4 vs no plan 71.2 | 28.7% of queries reach stage 3, at 1.25× cost; latency 2.35× |
| **SkillsBench** 2602.12670 | Curated vs self-generated skills over 86 tasks with deterministic verifiers | SKILL.md text | — | Curated +16.2pp (SWE +4.5); **16/84 tasks got worse**. Self-generated −1.3pp. Skills given: 1 → +17.8, 2–3 → +18.6, ≥4 → +5.9. A "comprehensive" skill −2.9 | Conflicting guidance |
| **Agent Skill Framework** 2602.16653 | — | SKILL.md, loaded on demand | The model selects | ≥30B select at >0.99. <4B fail with 4–6 distractors | All skills in context can be worse than none for small models |

What AWOS takes from each:
- **ASI's rewrite-and-re-execute** with its three checks (solved, skill called,
  skill caused a state change). This becomes A2 and A3.
- **LATM's held-out inputs.** This becomes A2, which uses 12 held-out cases, not 3,
  because R0 has no LLM to catch errors.
- **APC's exact intent key**, not embeddings. This becomes §8 MATCH.
- **Routine's tool-named steps with I/O descriptions**, and **one Routine only**.
- **SkillWeaver's and Blueprint's precondition filter.**
- **SkillsBench's 19% of tasks hurt.** This is why every record gets its own
  paired regression test (A5) and why a demotion path exists. None of the papers
  implements demotion; it is a gap in the literature that AWOS has to fill
  itself.
- **What AWOS rejects:** every LLM-judged admission (ASI's evaluator,
  SkillWeaver's reward model, APC's success-only filter). AWOS uses L2 evidence
  plus a deterministic probe instead.

---

## 2. What AWOS already has (read-only audit)

| File | What it does | Verdict |
|---|---|---|
| `scaffold/agent/live_tool_synth.py` (318 LOC) | Behind `AWOS_LIVE_TOOLS=true`. After 2 failures a cheap model decides whether a helper script would help, writes it (≤40 lines, stdin JSON → stdout), checks it with `ast.parse` and one run on `{}` with exit 0, then stores it in `.awos/tools/` (not present today). Retrieval is word-set Jaccard > 0.4. The tool's output is injected as `[TOOL OUTPUT]`. Called from `orchestrator.py:3524`. | **Harden into the new store, and keep the file.** It already has the persist / index / subprocess-run skeleton. It breaks every rule in §1: the *cheap* model is the maker (the Live-SWE-agent trap: GPT-5-Nano fell from 44% to 14%); it synthesises during a failure, not from L2 successes; there are no held-out parameters, no probe, no preconditions and no evidence counts; retrieval is fuzzy keyword matching; it runs unsandboxed through `sys.executable` with the host environment. Plan: move the maker to offline Flash, swap Jaccard for the deterministic matcher (§6.1), run through `make_sandbox`, and keep the on-failure synthesis path disabled. |
| `scaffold/agent/skill_library.py` (303) | Groups successes by (task_type, keywords, model, strategy) and injects a "SKILL CONTEXT" block. `index.json` shows "hello"/"fn_b" toy entries from May. | **Retire from the prompt path.** It is an L1-evidence (or weaker) text injection. Leave it off whenever R1 is on, so two memory channels never compete. |
| `scaffold/agent/skill_extractor.py` (215) | Has the LLM write Markdown skills from tasks scored ≥0.95 and injects the top 2. | **Retire** for the same reason. top_k=2 also breaks the top-1 rule. |
| `scaffold/agent/experience.py` (383) | Experience store (REJECTED 2026-10-08: +50% cost, one collapse). Trajectory log, **always on**: goal, model, verdict, files, diff, call slice, cost, turns → `.awos/trajectories/<date>.jsonl`. | **Reuse the trajectory log as the compile input.** It lacks the evidence level and the action sequence; add both (step 1). The directory does not exist yet in this worktree, so no trajectories exist. |
| `scaffold/agent/cassette.py` (300) | Records and replays model replies, keyed on a root-normalised fingerprint of the conversation; a miss is explicit in strict mode. | **Reuse the pattern, not the module.** Root normalisation (`<PROJECT_ROOT>`) and "say so on a miss, never return the wrong turn" carry over to the action log and to the step journal. Cassettes replay *model replies*; R0 replays *actions*, so it needs no model at all. |
| `scaffold/agent/job_host.py` (512) | Durable queue; job = goal + ability + verifier; `ABILITIES = {"coding": _run_coding}`; child process per job; atomic JSON writes. | **The integration point.** The Gatekeeper's R0 → R1 → L0 ladder runs inside the ability call before `Orchestrator().execute_feature`. Overnight compile and admission runs become their own abilities (`compile`, `admit`), so they inherit crash safety. |
| `scaffold/agent/sandbox.py` (596) | Seatbelt or Docker; no network; no secrets; no unsandboxed fallback. | **Replay always runs here.** Add the sink policy (§8) on top of it. |
| `scaffold/agent/worktree.py` (151) | Git worktree create, commit and clean up. | **The golden-clone primitive** for admission (worktree at the fingerprint commit). An APFS `clonefile` fast path can come later. |
| `scaffold/agent/acceptance.py` | Keeps generated tests only if they fail at the start (`filter_start_failing`). | **Same rule for probes:** a probe must fail on the start state and pass on the verified end state. |
| `scripts/eval_report.py` `decision()` / `scripts/stats_audit.py` | The T1 paired decision rule (P(Δ>0) ≥ 0.95 plus exact McNemar). | **E7 uses it unchanged.** |

**Summary: harden 1 file, retire 2 from the prompt path, reuse 5, and build the
store, compiler, harness and matcher (§10).**

---

## 3. Concepts and tiers

| Kind | Form | Who decides arguments | LLM calls at run time | Allowed tier |
|---|---|---|---|---|
| **Tool** | A Python module with `run(params, ctx) -> Result`, plus `probe` and `preconditions`. Shell only through argv lists. | Local model fills a JSON schema from the verbatim goal (constrained decoding). **R0 needs no model:** arguments are extracted by deterministic slot rules, or the call comes from a scheduled job with explicit parameters | 0 (R0) or 1 small call (argument fill) | R0 after promotion, R1 before |
| **Routine** | A numbered list of steps. Each step has a `tool` name from the kernel registry, argument templates and an optional per-step probe | The local model, one step at a time | 1 per step, local | R1 only, never R0 |
| **None** | — | — | — | L0 normal attempt |

A Routine compiles to a Tool when every step's arguments can be derived from the
parameters without reading the content of the workspace. If a step's arguments
depend on what an earlier step read (the "data requires action" case), it stays a
Routine. A coding change that needs a real edit ("add a CLI flag") is always a
Routine: the edit step is `edit_file` with model-written content, followed by V0–V2.

---

## 4. Data model

One JSON file per record under `.awos/compiled/<repo_key>/<record_id>.json`, plus
the code at `.awos/compiled/<repo_key>/<record_id>.py`. Writes are atomic
(`write_json_atomic` from `job_host.py`). Records are immutable by version: a
recompile creates `v2` and never edits `v1`.

```jsonc
{
  "id": "rt_9c1e…",                 // sha256(kind + family + code/steps)[:12]
  "version": 1,
  "kind": "tool" | "routine",
  "family": "bump_version",         // intent family (also the R40 family)
  "intent": {
    "summary": "Bump the package version and update CHANGELOG",
    "triggers": ["bump version", "release <semver>"],   // exact phrase rules, used by the matcher
    "anti_triggers": ["downgrade", "yank"],
    "embedding_key": null                                // reserved; fuzzy matching is a trap (APC)
  },
  "params_schema": {                // JSON Schema draft 2020-12; drives constrained decoding
    "type": "object",
    "required": ["new_version"],
    "properties": {
      "new_version": {"type": "string", "pattern": "^\\d+\\.\\d+\\.\\d+$"},
      "changelog_note": {"type": "string", "maxLength": 200, "x-taint": "data"}  // may not reach a sink
    },
    "additionalProperties": false
  },
  "preconditions": {                // all checked by code, no LLM
    "repo_key": "awos-3f2a",
    "fingerprint": {
      "files": {"pyproject.toml": "sha256:…", "awos.py": "ast-sig:…"},  // only files the record touches or reads
      "lockfile": "sha256:…",       // requirements.txt / uv.lock / package-lock.json
      "toolchain": {"python": "3.12", "pytest": ">=8"}
    },
    "state_predicates": ["git.clean", "git.branch != main"],
    "drift_policy": "suspend"      // fingerprint mismatch -> suspended, not failed
  },
  "body": {
    "tool":    {"entry": "rt_9c1e.py:run", "sinks": ["fs.write:worktree"]},
    "routine": [                    // present only when kind=routine
      // each step names its tool and carries I/O descriptions (Routine paper: -11pp without tool names, +11pp with I/O on a 7B)
      {"n": 1, "tool": "read_file", "args": {"path": "pyproject.toml"}, "io": "in: none; out: current version string", "probe": null},
      {"n": 2, "tool": "edit_file", "args": {"path": "pyproject.toml", "content": "<model>"},
       "probe": "toml.get('project.version') == params.new_version"}
    ]
  },
  "probe": {                        // the post-state check; deterministic
    "entry": "rt_9c1e.py:probe",   // probe(params, ctx) -> {"ok": bool, "evidence": {...}}
    "kinds": ["file_hash", "toml_value", "exit_code", "git_ref"],
    "fails_on_start": true          // verified at admission
  },
  "near_miss": {                    // what the guard must refuse
    "generator": "rt_9c1e.py:near_misses",
    "examples": ["bump to 2.0 (not semver)", "bump version in a repo without pyproject"]
  },
  "evidence": {
    "source_traces": ["traj/2026-10-12.jsonl#41", "…"],   // L2 only
    "admission": {"held_out_n": 12, "pass": 12, "near_miss_n": 6, "refused": 6,
                  "regression_set": "prev_solved@2026-10-12", "regressions": 0},
    "s": 12, "f": 0,                // probe-verified executions after admission (synthetic + live)
    "s_live": 0, "f_live": 0,       // the subset from real goals
    "last_fail": null,
    "lb95": 0.79                    // cached; recomputed on every update
  },
  "state": "candidate" | "admitted" | "promoted" | "suspended" | "demoted" | "retired",
  "history": [{"ts": "…", "event": "admitted", "by": "harness", "detail": "…"}],
  "maker": {"model": "deepseek/deepseek-v4-flash", "prompt_sha": "…", "cost_usd": 0.004},
  "owner_approved_sinks": false     // required before any external sink (push, PR, send)
}
```

The index `.awos/compiled/<repo_key>/INDEX.json` maps family → current record id.
There is **at most one live record per family**, which enforces top-1 by
construction.

Every execution is appended to `.awos/compiled/executions.jsonl`:
`{record_id, version, tier (R0|R1), goal_sha, params, precondition_result,
probe_result, outcome, wall_s, llm_calls, cost_usd, sandbox, ts}`. The evidence
counts are a fold over this log, so they can always be recomputed.

---

## 5. Compile pipeline (offline)

Compilation runs as a `compile` job on the host: overnight, batched, and off-peak
for DeepSeek pricing. It never runs on the request path.

### 5.1 Which traces qualify

- **Evidence level L2 only.** That means one of: hidden tests passed, a merged PR,
  green CI on the PR, no revert in 7 days, or (for chores) a deterministic
  probe the owner confirmed once. L1 traces (our gate green) stay quarantined, as
  the architecture doc requires (§2.5).
- The trace must carry the **action log**: an ordered list of
  `{tool, args, result_digest}` with `<PROJECT_ROOT>` normalisation. Today's
  trajectory record has the diff and call slice but no action list. Step 1 adds it.
- **Cluster** traces by family and by *structure hash*: the sha of the action
  sequence with argument values masked by type. Compile only when a cluster has
  **≥2 L2 traces with the same structure and different parameter values**. Two
  traces with identical arguments are a single example.
- Exclude a trace when it touched test files, used network, needed a human
  confirmation, or included a step whose arguments came from untrusted content
  (provenance tag).

### 5.2 Flash compile call

The input is the 2–5 cluster traces (actions and diffs, never raw secrets), the
kernel tool registry, and the target schema. Flash must return:

1. `kind` (tool or routine) with a reason, using the rule in §3;
2. `params_schema`, with `x-taint` on any free-text field;
3. `run()`, or the numbered `steps`;
4. `probe()`, written as a getter plus comparator, with one or two alternatives
   (OSWorld style);
5. `preconditions`: the files read or written, plus the lockfile;
6. `gen_params(seed) -> params`: a held-out parameter generator;
7. `near_misses(seed) -> [(goal_text, state_mutation)]`: inputs the guard must
   refuse. Examples: wrong shape, a missing file, a different toolchain, or a
   goal that sounds the same but asks something else ("bump the *dependency*
   version").

Static checks, all without an LLM:
- `ast.parse` passes.
- Allowed imports only.
- No `subprocess(..., shell=True)` and no string-formatted commands.
- Every sink the code calls is declared in `body.tool.sinks`.
- The code has no literal values copied from a source trace's parameters (this
  catches overfitting to the example).
- Size ≤ 200 LOC.

A failure gets one retry with the error message; after that the cluster is marked
`compile_failed` and retried only when a new trace arrives.

### 5.3 The generator is held out from the maker

`gen_params` comes from the maker, so held-out parameters are not fully
independent of it. Two mitigations:
- (a) A second, independent Flash call (different prompt, no access to `run()`)
  writes 5 extra parameter sets from the schema and the intent alone.
- (b) For R40 families, the *test author* (the owner or the R40 builder) supplies
  the hidden held-out parameters, and the maker never sees them.

---

## 6. Admission harness

The harness runs as an `admit` job. Every run happens inside `make_sandbox()` on a
**golden clone**: a git worktree at the commit matching the precondition
fingerprint, with dependencies installed from the lockfile, and reset per case.

For each candidate:

| Check | Pass condition |
|---|---|
| **A1 Start-state probe** | `probe` returns `ok=false` on the untouched clone for every held-out case (same rule as `acceptance.filter_start_failing`). A probe that already passes proves nothing |
| **A2 Held-out replay** | N_h = 8 cases from `gen_params` plus 4 from the independent generator. Each case: fresh clone → preconditions true → run (Tool directly; Routine with the local model filling arguments, temperature 0, plus 1 repeat at 0.7) → `probe.ok`. **All 12 must pass** for a Tool; ≥11/12 for a Routine, with the miss logged. A failure-free run of 12 gives a Beta lower bound of 0.79, which is enough to *admit* but not to *promote* |
| **A2b Trace rewrite (ASI)** | Each source L2 trace is rewritten to call the candidate in place of the steps it replaces, with the tail truncated, and re-executed on a clone at the trace's start commit. The hidden or L2 check must still pass, the candidate must be called, and each call must change state |
| **A3 Idempotence / no collateral** | After the run, `git status` shows only files in the declared write set. Running the Tool twice gives the same post-state, or the second run refuses cleanly |
| **A4 Near-miss guard** | All generated near-misses (≥5) are refused, either by a precondition or by the matcher's anti-triggers, *before* any write happens. One leak → reject |
| **A5 Regression set** | The previously-solved set (the last 20 tasks of this repo that passed hidden checks, re-run once with the record live versus once without, on the same day) shows **0 tasks flipping from pass to fail**. This is the direct guard against "skills break previously solved tasks", and against the job-12 collapse |
| **A6 Probe sensitivity** | Mutate the end state with one plausible wrong result per case (wrong version string, a test file deleted). The probe must say `ok=false`. This catches probes that are vacuous |

Pass → `admitted` with `s = number of held-out passes`, `f = 0`. Any fail →
`rejected`, with the reason logged, and the cluster returns to the pool. Expect a
low admission rate; ASI's figure is about 15.6%. A high rate is suspicious and is
audited by hand.

**Cost per candidate.** One Flash compile call (about $0.002–0.01) plus about 12
sandboxed replays (seconds each for Tools; for Routines, local-model time of about
12 × steps × 2–5 s). A5 costs the most and is run in batches: once per night for
all candidates of a repo, as a single "library on vs off" pass.

---

## 7. Promotion and demotion math

Each probe-verified execution after admission updates (s, f). The posterior is
Beta(1+s, 1+f), with a uniform prior. The **95% one-sided lower bound** is
`LB = BetaInv(0.05; 1+s, 1+f)`.

| Failures f | Successes needed for LB ≥ 0.9 |
|---|---|
| 0 | 28 (LB 0.902) |
| 1 | 44 |
| 2 | 58 |
| 3 | 72 |

For reference, with f = 0: s=3 → 0.47, s=5 → 0.61, s=10 → 0.76, s=20 → 0.87.
For f = 0 the closed form is `LB = 0.05^(1/(s+1))`.

**Where 28 successes come from.** Live use alone would take months, so most comes
from **synthetic held-out replays**, which are nearly free for a Tool (zero LLM, a
few seconds each in a sandbox). Synthetic evidence is cheap but correlated with
the maker's generator, so:
- **Promotion to R0** needs LB ≥ 0.9 over all evidence, **and** `s_live ≥ 3`
  (real goals that ran at R1 with the probe passing) **and** at least 5 of the
  synthetic successes from the independent generator.
- The nightly `admit` job tops up synthetic evidence for admitted Tools, up to
  40 replays per record.

**State machine.**

```
candidate ──A1–A6 pass──► admitted (usable at R1 only)
admitted ──LB≥0.9 ∧ s_live≥3 ∧ indep≥5──► promoted (R0 allowed)
promoted ──one probe fail──► demoted  (f+=1; R0 off; usable at R1 again only after
                                       10 consecutive synthetic passes)
admitted/demoted ──second probe fail within 30 days──► retired (recompile needed)
any ──precondition fingerprint mismatch──► suspended  (no f increment; re-admit on the
                                                       new fingerprint with A1–A4)
any ──lockfile/toolchain change──► suspended
promoted ──unused for 60 days──► admitted (re-verify A2 before the next R0 use)
```

- A probe failure at R0 is a **false replay**, the headline safety metric. The
  sandbox worktree is discarded, so there is no persisted side effect. The task
  then falls through to R1 and L0 with the goal unchanged.
- The **one-failure demotion** is deliberately harsher than the Beta posterior
  needs to be. At R0 there is no LLM in the loop to notice drift, so one observed
  failure is treated as evidence that the preconditions are incomplete. Every
  demotion writes a `precondition_gap` note: Flash is shown the failing case at
  the next compile and must add the missing fingerprint file.

---

## 8. Runtime integration (Gatekeeper)

This slots into the control flow of `local_first_architecture_2026-10.md` §2.2, at the
"T0 REPLAY" line (R0 here), with R1 inserted before L0.

```
goal (verbatim) ─► privacy flag
  ─► MATCH (deterministic, ≤50 ms, no LLM)
       candidates = families whose triggers match AND no anti_trigger matches
       filter by preconditions (fingerprint, lockfile, state predicates)  → suspended skipped
       if >1 candidate remains → NONE (ambiguity means abstain; never hand over two)
  ─► R0  if record.state == promoted AND params can be extracted deterministically
         (slot regex from triggers, or explicit params from a scheduled job)
         run in sandbox worktree → probe → ok: hand off (L1 evidence) | fail: demote, discard, ↓
  ─► R1  if record.state ∈ {admitted, promoted}
         local model fills params_schema (constrained JSON) from the VERBATIM goal
         Tool: run → probe.  Routine: kernel executes steps in order; the model only
         fills each step's args; the kernel rejects a step whose tool ≠ the named tool;
         per-step probe; whole-routine probe at the end. Then GATE V0–V2 for code edits
         ok: hand off | fail: restore worktree, record f, ↓ (goal unchanged)
  ─► L0  normal local attempt (unchanged), then cascade to C1 as today
```

Rules:
- **The goal is always verbatim.** The Routine is added as a separate block
  ("A verified procedure for this kind of task follows; the task statement above is
  authoritative"). It never rewrites the goal; that was the round-1 lesson.
- **Top-1 or none.** The matcher abstains on ties. It never retrieves by
  embedding similarity alone; triggers are explicit phrase rules written at
  compile time and reviewed by the owner on promotion.
- **Small models choose badly**, so the local model never picks between records.
  It only fills arguments for the single record the matcher chose.
- **Falling through is free.** R0 and R1 failures roll back the worktree and pass
  control down the ladder with the original goal. They are logged as
  `fallthrough_reason`.
- **Telemetry** per task: `tier` (R0|R1|L0|C1), `record_id`, `match_ms`,
  `precondition_result`, `probe_result`, `llm_calls`, `cost_usd`, `wall_s`. The
  OTel span is shaped to match the architecture doc's evidence ledger.
- **Kill switch:** `AWOS_COMPILED=off|r1|r0` (default `off` until E7 says KEEP).

---

## 9. Safety

- **A replay is a fixed, trusted program.** Its code is frozen at admission, and
  its hash is stored and checked before every run. Arguments pass JSON-schema
  validation (type, pattern, enum, maxLength) before `run()` sees them. Text from
  a web page, a file or the UI can fill a typed argument, but it cannot add a step,
  change a step's tool or reach a sink unless the schema allows it.
- **Provenance taint.** Fields marked `x-taint: data` (free text from the goal or
  from read content) may be written only inside the worktree. They can never
  appear in a shell argv, a URL, a git ref or a message body for an external
  send.
- **Sink policy**, deterministic and checked by the kernel, not the model:

  | Sink | Rule |
  |---|---|
  | File writes | Only inside the job worktree, and only within the declared write set (A3) |
  | Shell | argv lists only, a binary allowlist per record, no `shell=True` |
  | Network | Off (sandbox default). Package installs only during bootstrap Tools, through the host-side allowlist proxy once it exists |
  | git push | Only to `awos/*` branches, done host-side by the credential proxy; never `main` |
  | PR create / any send / pay / delete outside the worktree | Requires `owner_approved_sinks: true` on the record (set by the owner once at promotion), **plus** a per-run confirmation for irreversible actions. R0 never performs an irreversible action without that confirmation |
  | Credentials | Never inside the sandbox; the proxy injects them |

- **Sandbox always.** No unsandboxed fallback. This is exactly the
  `make_sandbox() is None` rule from `sandbox.py`. Hardening `live_tool_synth.run_tool`
  moves it from `sys.executable` with the host environment into the sandbox.
- **Chores where the next action depends on content just read** ("reply to the
  email that asks about X") cannot be R0. They stay R1, with the quarantined
  parser, or L0.
- **No test edits.** A record whose write set includes test files is rejected at
  the static check. This is the same V0 rule as the coding gate.

---

## 10. How coding and computer use both use it

### 10.1 Coding (App #1)

Code fixes rarely recur, so here the payoff shows up as **$, seconds and turns**,
not as more solves.

| Family | Kind | Probe |
|---|---|---|
| Env bootstrap (venv, install from lockfile, smoke import) | Tool | `python -c "import <pkg>"` exit 0; lockfile hash unchanged |
| Test recipe (the working test command for this repo/subdir) | Tool (its output feeds the repo card) | Collection count > 0; exit code matches a known-good run |
| Lint / format (ruff check --fix, ruff format on touched files) | Tool | `ruff check` exit 0 on the write set; diff limited to whitespace and format |
| Branch + commit + PR hand-off | Tool (push and PR through the proxy; approval needed) | `git rev-parse awos/<slug>` exists; PR URL returned by the host |
| Bump version + CHANGELOG | Tool | TOML value equals the parameter; a CHANGELOG heading exists |
| Regenerate a report (`scripts/eval_report.py` on a ledger) | Tool | Output file exists; sha differs from before; schema check passes |
| Add a CLI flag to `awos.py` | **Routine** (the edit is model-written) | `python awos.py <cmd> --help` lists the flag; V0–V2 green |
| Add a test for function X | **Routine** | A new test fails on a mutant and passes on HEAD |

### 10.2 Computer use (App #2, the main compounding bet)

Chores use the API-first action ladder: shell/Python → AppleScript, JXA,
Shortcuts or App Intents → AX semantic action → pixels last. Probes are
getter-plus-comparator checks on deterministic state: file hash, AX value,
sqlite/plist value, URL, or a calendar or reminders entry read back through
the API. Examples:
- "export this week's receipts PDF to ~/Finance/2026-10";
- "add a reminder";
- "rename screenshots by date";
- "file a downloaded invoice".

A C10 chore that passes A1–A6 inside a Lume VM clone becomes a Tool. A chore
whose steps depend on what is on screen becomes a Routine.

---

## 11. Measurement: E7

### 11.1 The prerequisite, and what to do without logs

The Trick Book requires 2–4 weeks of logs showing ≥20% intent repetition before E7.
**AWOS has no real user logs.** `.awos/trajectories/` does not exist in this worktree, and
`.awos/goals` holds benchmark goal trees. Bench runs repeat by design, so they say
nothing about repetition.

The proposal has two parts.

1. **Start the meter now, at zero cost.** Step 1 adds `family` and the action log
   to the trajectory record. A weekly script computes the **intent repetition
   rate**: the share of goals whose (family, structure hash) has appeared before
   in the trailing 28 days. The owner's own AWOS sessions on this repo count
   (the owner is user #1). That is the real prerequisite check.
2. **Build R40 now as a synthetic-but-realistic set from the owner's actual
   workflow on this repo.** Every family below is something the git history
   shows the owner doing by hand. E7 runs on R40 as a *capability* test. It is
   marked "synthetic workload" in the report, and its result does **not**
   license turning the feature on by default until the live repetition rate
   reaches 20%. A capability KEEP on R40 plus repetition ≥20% means ship;
   either one alone means keep the meter running.

### 11.2 Building R40

There are 10 families × 4 eval instances = 40 tasks, plus 2 *training* traces per
family that the compiler sees (80 traces in all, not part of the eval). Each eval
instance has **held-out parameters** the maker never saw.
**8 of the 40 (20%) are near-misses**: goals worded like the family but needing a
different action, or a repo state that breaks a precondition. The correct
behaviour is to **refuse the record and fall through**.

| # | Family | Kind | Parameter examples (held-out) | Near-miss example | Hidden check |
|---|---|---|---|---|---|
| 1 | Run the tests for a subpackage | Tool | `scaffold/agent`, `tests/unit`, a `-k` expression | "run the *benchmark*" (a different command) | Report has the right collected count and exit code |
| 2 | Bump version + CHANGELOG | Tool | 0.9.3, 1.0.0, a pre-release | "bump the *requests* dependency" | TOML value and CHANGELOG heading |
| 3 | Add a CLI flag to an `awos.py` subcommand | Routine | `budget --json`, `models --available`, `traces list --limit N` | flag already exists → must refuse | `--help` lists the flag; the flag changes output; hidden test |
| 4 | Regenerate the eval report for a ledger | Tool | Three ledger files (`job_series_*.json`) | ledger missing → refuse | Report file exists; summary table matches the recomputation |
| 5 | Lint/format the changed files | Tool | Three planted diffs with lint errors | a file with a *logic* error only | `ruff check` clean; diff only on the write set |
| 6 | Add a pytest for a named pure function | Routine | 3 functions in `scaffold/agent/` | function does not exist | The new test passes on HEAD and fails on a planted mutant |
| 7 | Create a branch, commit, open a PR (dry-run proxy) | Tool | 3 branch slugs and messages | target is `main` → refuse | `awos/<slug>` ref exists; proxy log shows a PR request; nothing pushed to `main` |
| 8 | Write a spec stub from `SPEC_TEMPLATE.md` | Tool | 3 feature names | template moved → precondition fail | File exists; all template headings present; title substituted |
| 9 | Checkpoint the session to `docs/memory/` | Tool | 3 dates and topics | path outside `docs/memory` → refuse | File at the expected path; required sections present |
| 10 | Bootstrap the env from a lockfile in a fresh clone | Tool | 3 clean clones (one with a changed lockfile = near-miss) | lockfile hash changed → suspend | `import scaffold.agent` succeeds in the venv; pytest collection > 0 |

The hidden checks are written by the R40 builder (the owner, or a separate Claude
session that never sees the compiled records). They live in
`tests/r40/hidden/`, which the agent and the maker cannot read.

### 11.3 Arms (pre-registered)

| Arm | Description |
|---|---|
| A | Local direct (L0, verbatim goal, no records) |
| B | Local + top-1 **Routine** at R1 (every family compiled as a Routine) |
| C | Local + **Tool** at R1 with JSON arguments (the local model fills only the schema) |
| C0 | C with R0 enabled for promoted Tools (this is the target configuration) |
| D | Tool **without held-out validation** (admitted on source traces only), to isolate verification |
| E | Flash direct (cloud control) |

Every arm runs 3 repeats per task with paired task IDs on the same day, on the M5
with the local 9B; E uses about $2 of Flash.

### 11.4 Metrics and decision rule

- **Primary:** end-to-end hidden-check success on the 32 non-near-miss tasks.
- **False replay rate:** R0 or R1 runs where the probe said ok but the hidden check
  failed, **plus** any near-miss where a record executed a write. Target ≤1%.
  An honest caveat: 32 × 3 = 96 runs with zero false replays only bounds the rate
  at ≤3.1% (95%, rule of three). Certifying ≤1% needs about 300 replays with zero
  failures. R0 replays are free, so the harness adds a **300-instance synthetic
  sweep** (30 per family from the independent generators, including 60
  near-misses). It reports the Clopper-Pearson upper bound.
- **Near-miss guard:** ≥8 of the 8 eval near-misses refused per repeat, and ≥8/10
  in the extended near-miss sweep. The Trick Book's bar is "≥8/10"; this design
  holds the eval near-misses to 8/8 because one leak is a false replay.
- **Cost and speed:** $/solve, wall seconds/solve, LLM calls/solve, and the R0 share.
- **Regression:** a separate check on the I28 subset with records live. No
  previously-solved task may flip.
- **Decision (T1 rule):** **KEEP C0** if:
  - success ≥ A + 15pp, with P(Δ>0) ≥ 0.95 and exact McNemar p ≤ 0.05;
  - success is within 5pp of E;
  - false replay ≤1%, with the sweep's upper bound ≤1.5%;
  - the near-miss guard passes;
  - there are zero I28 regressions.

  **REJECT** if P(Δ>0) ≤ 0.2 or there is any spreading false replay. Otherwise
  **INCONCLUSIVE**: extend R40 to R80; do not ship.
- **Secondary readouts:**
  - B vs C: whether Routine or Tool form is better for a 9B model;
  - C vs D: the value of verification. ASI predicts most of the gain is here.

---

## 12. Implementation plan

Each step is one atomic change with unit tests and a live proof, following
`protocols/LIVE_PROOF_PROTOCOL.md`. Sizes assume a solo owner. Steps 1–3 can run in
parallel because they own different files.

| # | Step | Owns | Tests | Live proof | Days |
|---|---|---|---|---|---|
| 1 | Add `family`, a root-normalised **action log** and `evidence_level` to the trajectory record; add a weekly repetition-rate script | `experience.py` (trajectory part only), `scripts/intent_repetition.py` | Record schema; root normalisation; structure hash is stable when arguments change | Run 3 real AWOS goals on this repo; the trajectory JSONL shows actions and family; the script prints a repetition rate | 1 |
| 2 | Add the record store + execution log + Beta math (`kernel/compiled/store.py`) | New dir | LB table in §7 reproduced exactly; fold over the execution log reproduces counts; atomic writes; at most one live record per family | CLI `awos compiled list/show` on a hand-written record | 1 |
| 3 | Build R40 (families, held-out params, near-misses, hidden checks) | `tests/r40/` | Each hidden check fails on the start state and passes on a hand-made solution (A1/A6 applied to the benchmark itself) | `pytest tests/r40/hidden` red on clean clones, green on reference solutions | 2 |
| 4 | Admission harness A1–A4, A6 (golden worktree clone + `make_sandbox`) | `kernel/compiled/admit.py` | A planted vacuous probe is rejected (A1/A6); a planted collateral write is rejected (A3); a planted near-miss leak is rejected (A4) | Hand-write the `bump_version` Tool; `awos compiled admit` admits it with 12/12 and refuses the 5 near-misses; spans visible | 2 |
| 5 | Flash compiler (offline `compile` ability in `job_host.ABILITIES`) with static checks and the independent generator | `kernel/compiled/compile.py`, `job_host.py` (one line in ABILITIES) | Static checker rejects `shell=True`, an undeclared sink, a literal copied from a trace; cassette-recorded Flash replies so tests are free | Overnight job compiles the R40 training traces; report lists admitted/rejected per family with reasons | 2 |
| 6 | Harden `live_tool_synth.py`: sandboxed `run_tool`, hash check, store-backed index, disable the on-failure synthesis path | `live_tool_synth.py`, `orchestrator.py:3524` block | Run under the sandbox; a tampered script hash refuses to run; Jaccard path removed | An existing tool runs only through Seatbelt (sandbox-exec visible in the process tree) | 1 |
| 7 | Deterministic matcher + precondition check + R1 executor (Tool with JSON-schema argument fill; Routine step runner with step-to-tool enforcement) | `kernel/compiled/match.py`, `run.py` | Ties abstain; anti-triggers refuse; a fingerprint mismatch suspends; a Routine step using the wrong tool is rejected; goal text is byte-identical in the prompt | `AWOS_COMPILED=r1 awos run "bump version to 0.9.4"` on this repo with `provider=local`: tier R1, probe ok, $0.00. **Re-run with the OpenRouter key present** | 2 |
| 8 | R0 replay + promotion/demotion state machine + nightly synthetic top-up | `run.py`, `admit.py` | State machine transitions; one failed probe demotes; a planted bad record is demoted, and the worktree discarded with no side effects | Promoted Tool replays with **0 LLM calls** (llm_calls.jsonl unchanged). Then perturb `pyproject.toml` so the fingerprint drifts → suspended; plant a wrong-output edit → probe fails → demoted, fall through to L0 | 2 |
| 9 | A5 regression pass (library on vs off over the last 20 solved tasks) | `admit.py` | Planted record that breaks a solved task is rejected | Nightly report shows the A5 table | 1 |
| 10 | Sink policy for compiled records (taint, argv allowlist, proxy-only push/PR, owner approval flag) | `kernel/compiled/sinks.py`, `sandbox.py` hook | A tainted field reaching argv is refused; a push to `main` is refused; a PR without approval is refused | Branch+PR Tool runs end to end against the dry-run proxy; an attempt to target `main` is refused, with the refusal in spans | 2 |
| 11 | Run E7 on R40 + the 300-replay sweep + the I28 regression check; write the verdict | `docs/specs/e7_compiled_tools.md` (pre-registered before running) | — | `eval_report.py` decision output, with the false-replay CP bound and the near-miss table | 2 (mostly machine time) |
| 12 | Computer-use pilot: 3 C10 chores compiled in a Lume clone with AX/plist probes | `apps/desktop/…` | Probe fails on the start snapshot | A chore replayed with 0 LLM calls; a perturbed UI trips the guard | 3 (after M3) |

Total: about **18 days** to the E7 verdict (steps 1–11). The Trick Book's "3–5 days"
covers only the core of steps 4, 5 and 7. The rest is the safety, regression and
measurement work that the experience-store failure showed is not optional.
Step 12 follows the M3 milestone.

### 12.1 Implementation notes (steps 1–2, built 2026-10-10)

What exists, all new files, nothing wired into the run path:

| Piece | File | Notes |
|---|---|---|
| Repetition meter | `scaffold/agent/compiled/repetition.py` | `log_task(goal, files=, actions=, root=, outcome=, evidence_level=)` appends `{family, goal_template, goal_sha, file_pattern, action_shape, intent_key, structure_key}` to `.awos/repetition/log.jsonl`. The raw goal is never stored. `AWOS_REPETITION_LOG` overrides the path or turns it `off`; off under pytest unless set. Never raises |
| Repeat-rate report | `scripts/repetition_report.py` | Trailing-window (28 d) repeat rate per family on the structure key (the E7 key) and the intent key (upper bound); prints `E7 prerequisite … MET/NOT MET` at 20% |
| Record format | `scaffold/agent/compiled/record.py` | Dataclasses mirroring §4, `RECORD_SCHEMA`, a dependency-free JSON-schema subset validator, `taint_fields`, closed-schema `validate_params`, `compute_fingerprint`/`check_fingerprint` (file sha256 + lockfile), atomic `save_record` with `INDEX.json` = one live record per family. Adds `s_indep`, `consecutive_ok`, `fail_ts`, `last_used_ts` to `evidence` for the §7 rules |
| Beta math + state machine | `scaffold/agent/compiled/beta.py` | Exact integer Beta CDF (binomial tail) + bisection; reproduces the §7 table (28/44/58/72). `apply_event` implements §7 (admit, ok, fail, mismatch, idle); `fold_executions` recomputes counts from the execution log |
| Admission skeleton | `scaffold/agent/compiled/admit.py` | A1, A2 (8 gen + 4 indep), A3, A4 (guard = trigger matcher → params schema → fingerprint → tool preconditions), A6, each case on a fresh clone of the golden repo. `python -m scaffold.agent.compiled.admit --demo` admits the hand-written `examples/bump_version.py` |

Deviations from the step table: step 1 lives in `compiled/repetition.py` and
`scripts/repetition_report.py` rather than `experience.py` and
`scripts/intent_repetition.py`, so the trajectory log is untouched. The code is
under `scaffold/agent/compiled/` (transitional) rather than `kernel/compiled/`.

**Not done in the skeleton:** A2b trace rewrite, A5 regression set, running inside
`make_sandbox()` (the tool runs in-process against the clone, so only hand-written,
trusted Tools may use it), the `awos compiled` CLI, the execution log on disk, the
full matcher (step 7), and the evidence-level field on trajectories.

**Orchestrator hook (not wired).** The meter belongs in
`Orchestrator._experience_goal_end` (`orchestrator.py`, the "Experience store +
trajectory log" block), right after `exp.write_trajectory(...)`, inside the same
`try`:

```python
from .compiled.repetition import log_task
log_task(goal, files=changed,
         actions=[c.get("tool") for c in exp.call_log_slice(start_ts, end_ts)],
         root=codebase_root, outcome="success" if success else "fail",
         evidence_level="L1" if green else "L0")
```

The action list should come from the agent loop's tool-call record rather than the
LLM call log once that is threaded through; until then the shape is coarse. The
call is advisory (never raises) and costs one small JSON line per finished goal.

---

## 13. Risks and open questions

- **Low repetition.** If the live repetition rate stays under 20%, R0 rarely fires.
  The design still pays off at R1 for bootstrap, test and lint chores that recur
  on every coding job, and on computer-use chores later. APC's collapse on GAIA is
  the warning.
- **Probe incompleteness.** A probe passes but the work is wrong. A6 mutation
  testing and the hidden-check comparison in E7 measure this. Every false replay
  is reviewed by hand.
- **Generator correlation.** Synthetic evidence is inflated if the generator
  shares the maker's blind spots. The `s_live ≥ 3` and independent-generator
  requirements bound this. E7 reports LB both with and without synthetic evidence.
- **Library growth and staleness.** One record per family, fingerprint suspension,
  60-day idle re-verification and nightly A5 regression keep it bounded.
- **Maker cost.** About $0.01 per candidate and about $0.10 per night at the expected
  volume. Admission burns local seconds and watts; it is scheduled on idle time.
- **Routine format vs "any plan".** The Routine paper's baseline was a bare prompt.
  B vs A in E7 measures what a plan buys over nothing; COPE (E6) is the nearby
  comparison.

## 14. Rollback

`AWOS_COMPILED=off` (the default) restores today's behaviour exactly: no match,
no R0 or R1, no prompt change. Records and logs are data under `.awos/compiled/`
and can be deleted. The `live_tool_synth` hardening (step 6) is independent and
keeps its existing `AWOS_LIVE_TOOLS` gate.
