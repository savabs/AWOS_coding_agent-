# Edit robustness: fuzzy-apply ladder + copy-constrained SEARCH (trick T4)

Stage 1, one excellent worker. This work moves effort into the apply step and
the decoder, so the cheap model's edits land without an extra LLM call.
Sources: `docs/research/trick_book_2026-10.md` §1 #2, §2.2, and §3 combination 2.

Both features are off by default:

| Flag | Where it applies | Effect |
|------|------------------|--------|
| `AWOS_FUZZY_APPLY=1` | `Verifier._apply_fuzzy`. This covers `EditFileTool` (the agent's `edit_file`) and `one_shot.apply_blocks` | The unique-match ladder replaces the legacy 4-tier matcher |
| `AWOS_EDIT_GRAMMAR=1` | `one_shot.run_one_shot`, first call only, local client only | Sends a GBNF `grammar` to llama-server |
| `AWOS_EDIT_GRAMMAR_MAX_BYTES` | (default 200000) | The grammar is not sent above this size, and the skip is logged |

## Research

- **Aider editblock** (`aider/coders/editblock_coder.py`, `replace_most_similar_chunk`):
  1. Exact line-tuple match.
  2. `replace_part_with_missing_leading_whitespace`: removes the shared minimum indent, matches on `lstrip()`, requires one uniform prefix, and adds that prefix to REPLACE.
  3. Retry without a leading blank line.
  4. `...` elision.

  Its difflib tier (`replace_closest_edit_distance`, ratio 0.8, window ±10%) is
  **dead code** (unreachable after a bare `return`). `search_replace.py` (strategies
  `search_and_replace` → git cherry-pick → diff-match-patch, crossed with
  strip-blank / `RelativeIndenter` preprocs) is used only by the udiff coder.
  <https://github.com/Aider-AI/aider/blob/main/aider/coders/editblock_coder.py>
- **Diff-XYZ** (JetBrains 2025, arXiv:2510.12487) covers apply, anti-apply and diff
  generation across udiff and search-replace formats. Search-replace is the best
  format for large models. At 7B (Qwen2.5-Coder-7B), search-replace *generation* is
  weak: EM 0.28. The trick book puts the apply rate near 0.44. Small models gain
  little from changing the format, which argues for fixing the applier and the
  decoder instead.
- **CRANE** (arXiv:2502.09061): constraining a whole reply reduces reasoning ability
  (TC⁰ argument). The fix is to keep reasoning free and switch the grammar on only
  between delimiters. That gives up to about +10 pp at 1.5–32B.
- **llama-server** (`tools/server/README.md`, `server-common.cpp`):
  - `/v1/chat/completions` accepts a per-request `grammar` (GBNF), `json_schema` and `response_format`.
  - `grammar` combined with `tools` is an error.
  - `grammar_lazy` / `grammar_triggers` in the request body are **ignored on the chat endpoint**, because the template sets them. They only work on `/completion`.
  - A raw grammar also constrains a `<think>` block. Our server runs `--reasoning off`.
  - An invalid grammar returns HTTP 400.
  - Repetition bound: 2000.
  - There is no documented size limit.
- **Cloud (OpenRouter Flash)** has no grammar parameter. For cloud models the fuzzy
  ladder is the only lever.

## Design

### Fuzzy ladder (`scaffold/agent/fuzzy_apply.py`)

The ladder stops at the first tier that finds exactly **one** location:

1. **exact.** The SEARCH text occurs once in the file.
2. **whitespace.** Lines match after `rstrip` and after collapsing in-line `[ \t]+` runs. Leading indentation must still match exactly.
3. **reindent.** Lines match after `strip`, and indentation *relative to the block's own common indent* is identical. REPLACE is shifted from the SEARCH base indent to the file's base indent. The ladder refuses if a REPLACE line sits left of the SEARCH base.
4. **difflib.** `SequenceMatcher` ratio ≥ 0.9 on stripped, blank-free text. The window is anchored: at least one SEARCH line (≥ 6 chars, with an alphanumeric character) must occur **exactly once** in the file. The window is that anchor's aligned position, ±1 for start and length. Every unique anchor must fall inside the window. No other non-overlapping window may also reach 0.9. REPLACE is re-indented from the first anchor.

**Never apply on ambiguity.** More than one match at any tier is a refusal. It does
not fall through to a fuzzier tier, because a fuzzier tier can only be more
ambiguous. The refusal reason is added to the model-facing `error_context`, for
example "SEARCH matches 2 places up to indentation (lines 2, 7)".

Logs:
- `[FUZZY-APPLY] tier=<n> (<label>) file=<path>` on a fuzzy apply.
- `[FUZZY-APPLY] refused file=<path>: <reason>` on a refusal.

The legacy matcher had no uniqueness rule at any tier and used difflib ≥ 0.85. Its
whitespace tier ignored indentation and pasted REPLACE without re-indenting it.

The static gate (T3) still runs on the ladder's output, as do Verifier's syntax and
contract checks, so a misplaced or mis-indented fuzzy edit is still rejected before
it is written.

### Path robustness (same flag, `one_shot.py`)

The live runs showed that the path line, not the SEARCH text, was the first thing to
fail. With `AWOS_FUZZY_APPLY=1`, two fixes apply:

- `_trailing_filename` accepts a path glued onto the end of a prose line.
- `_fuzzy_path` maps a missing path to an existing file. It tries trailing components first (for example `path/to/pkg/mod.py` → `pkg/mod.py`), then a **unique** basename. It never maps under `.git` or `.awos`, and never maps when the basename is ambiguous.

The pre-apply snapshot uses the same mapping, so the net-change check stays correct.

### Copy-constrained SEARCH (`scaffold/agent/edit_grammar.py`)

- `root ::= ( fl "\n" | blk "\n" )* ( fl | blk )?`
  - `fl` is any line not starting with `<<<<<<<`. Free text, the path line and fences go here.
  - `blk ::= "<<<<<<< SEARCH\n" sl* "=======\n" rl* ">>>>>>> REPLACE"`.
  - `rl` is any line not starting with `>>>>>>>`.
- `sl` is a **radix trie of the distinct lines** of the target files: the context files and sectioned files, minus read-only tests unless the task allows test edits. Each SEARCH line is therefore a verbatim file line. An empty SEARCH (create or append) stays legal.
- **Any order rather than in order.**
  - The trie is deterministic, so the grammar engine keeps one stack.
  - An in-order chain (each line may be followed only by its successor) needs one alternative per *position*. Every repeated line (blank lines, `)`, `return`) then keeps one live stack per occurrence, which is GBNF's worst case.
  - Contiguity is left to the applier: an exact match, then the ladder.
- **Not lazy.** The chat endpoint ignores `grammar_lazy`, so the grammar covers the whole reply. Free lines are unconstrained, which keeps reasoning text free in the CRANE sense.
- **Fallback.** If the call fails with the grammar attached (for example an HTTP 400), one call is made without it, and `[EDIT-GRAMMAR] call failed with grammar …; retrying without it` is logged. `[EDIT-GRAMMAR] skipped: …` is logged for a cloud client, missing targets, or a grammar over the size cap.
- No `providers.py` change was needed: `utility_chat(**extra)` already passes `extra_body` through, and the local client does not send `reasoning`.

## Measurement (offline, $0)

### Recorded failures

Replaying recorded failures was not used for these numbers. The one-shot reply is
stored truncated to 600 chars, and failed blocks keep only 300 chars of SEARCH, so a
full failed block plus its exact base file could not be rebuilt reliably. The
synthetic corpus below replaces it. The live runs further down add two real failure
modes the corpus does not cover:

- the prompt's `path/to/` example prefix copied into the path line (local 9B);
- the path glued onto the end of a prose line (Flash).

### Synthetic corpus

The corpus script is `scripts/fuzzy_apply_eval.py`, run with seed 7 on 1500 cases from `scaffold/**/*.py`. Each case is:
- a unique 3–8 line chunk;
- noise of one of these kinds: trailing whitespace, in-line whitespace, dedent, shift, indent unit 4→2, one-character token edit, dropped blank line, or a combination;
- REPLACE = the noisy chunk + a sentinel line.

Ground truth is where the sentinel must land.

**Metrics:**
- **apply**: the matcher changed the file.
- **right**: the sentinel landed after the true chunk.
- **wrong**: the edit was applied somewhere else.
- **indent_ok**: the sentinel has the file's indentation.
- **right+compiles**: the result still compiles. This is what survives Verifier's syntax gate. It is measured over the cases whose ideal edit compiles.

| corpus | matcher | apply | right | wrong | indent_ok | right+compiles |
|--------|---------|------:|------:|------:|----------:|---------------:|
| scaffold (1500) | exact only | 0.003 | 0.003 | 0 | 0.003 | 0.003 |
| | legacy (flag off) | 0.989 | 0.987 | **0.003** | 0.561 | 0.600 |
| | **ladder** | 0.977 | 0.977 | **0.000** | 0.911 | **0.915** |
| job_series (1000) | exact only | 0.002 | 0.002 | 0 | 0.002 | 0.000 |
| | legacy | 0.991 | 0.991 | 0.000 | 0.622 | 0.634 |
| | **ladder** | 0.984 | 0.984 | **0.000** | 0.930 | **0.930** |

Ladder tiers on the scaffold corpus:

| Outcome | Cases |
|---------|------:|
| whitespace | 291 |
| reindent | 370 |
| difflib | 799 |
| exact | 5 |
| refused (ambiguous) | 9 |
| no match | 26 |

The legacy matcher "applies" almost everything, but about 40% of its fuzzy applies
are mis-indented. Its whitespace tier pastes REPLACE without re-indenting it, and the
syntax gate then rejects those edits. It also put 0.3% of edits in the wrong place.
Counting edits that survive the gate, the ladder raises the apply rate from 0.60 to
0.92, and from 0.63 to 0.93, with **0 wrong-location edits**.

Per noise type, the ladder places 0.94–1.00 correctly. Its lowest rows are
dedent+token (0.94), and token and ws+token (0.96).

Runtime: at most about 0.55 s per block on a 4000-line file (tier 4 full-file uniqueness scan).

## Live proof

The runs used `scripts/one_shot_live.py` on job `ordertool/08_excel_bom_bug`: one
`run_one_shot` call, then the hidden tests.

**(a) Local (Qwen3.5-9B Q4_K_M, llama-server b11146, `AWOS_PROVIDER=local
AWOS_EDIT_GRAMMAR=1 AWOS_FUZZY_APPLY=1`)**

- **The grammar was accepted.** The log shows `[EDIT-GRAMMAR] sending grammar: 34675 bytes, 12 files, 688 distinct lines`. A probe confirmed the server enforces a request grammar.
- **Decode speed is unchanged:** about 8.0 tok/s with the grammar and 7.8 without. Grammar compile time was not measurable.
- **Timeout.** The first attempt hit the client's 120 s `AWOS_MODEL_TIMEOUT_S`: about 60 s of prompt eval for 12k tokens, plus generation. The fallback logged `[EDIT-GRAMMAR] call failed … retrying without it` and worked as designed. Local runs need `AWOS_MODEL_TIMEOUT_S` of about 900.
- **What the grammar did.** Both SEARCH blocks were verbatim copies, with or without the grammar; at temperature 0 this model already copied exactly on this job. The grammar did keep the repair reply well formed: without it, the repair reply had no `>>>>>>> REPLACE`.
- **Paths.** Before the path fallback, 0/2 blocks applied: the model wrote `path/to/ordertool/storage.py`. With the fallback (`[FUZZY-APPLY] path path/to/ordertool/storage.py -> ordertool/storage.py`), block 1 applied, block 2 was refused as a no-op, and the repair applied. The result is `applied=['ordertool/storage.py']`, `failed=0`.
- **Tests.** The hidden tests still fail. That comes from the model's fix logic, not from applying it.

**(b) DeepSeek V4 Flash via OpenRouter, `AWOS_FUZZY_APPLY=1`, $0.0014 total for two calls**

- **Without the path fix:** the reply glued the path onto prose (``…block for `ordertool/storage.py`.ordertool/storage.py``). The result was `block has no file path` and 0 applied, which is also what flag-off does.
- **With the trailing-path parse + unique-basename fallback:** the log shows `[FUZZY-APPLY] path block.ordertool/storage.py -> ordertool/storage.py`, 1/1 applied, and **33/33 tests passed**. Cost was $0.00029 and the run took 6.9 s.

## Risks

- **Wrong place.** Every tier requires uniqueness, and difflib also needs a unique anchor and no rival window. On the synthetic corpus, 0 edits landed in the wrong place, against 0.4% for the legacy matcher. The static gate stays on.
- **Indent-unit changes** (the model writes 2-space indentation in a 4-space file) are not a uniform shift. Tier 3 refuses them, and tier 4 places them correctly but keeps the model's indentation. The syntax gate catches cases where that breaks structure.
- **Grammar size.** It grows with the distinct text of the target files: about 23 KB for a 473-line file. The cap skips very large contexts. No measurable decode slowdown at 23 KB (8.16 vs 8.08 tok/s).
- **A grammar can force a wrong copy.** When the model wants a line that is not in the file (for example a file outside the context), the grammar forces the closest legal path. That block then fails to apply and goes to repair, as before.
