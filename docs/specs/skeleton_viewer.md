# Spec: skeleton / bounded file viewer (trick T5)

**Status:** implemented behind `AWOS_SKELETON_VIEW` (default `0`). Stage 1 (one excellent worker): execution efficiency, meaning fewer turns and tokens per solved task.
**Source:** `docs/research/trick_book_2026-10.md` §1 #5, §2.3.
**Code:** `scaffold/agent/skeleton.py`, `scaffold/agent/tools/filesystem.py`. **Tests:** `tests/test_skeleton_viewer.py`.

## Problem

The agent loop reads a large file whole, scrolls through it, and runs out of turns before it edits anything. Example: 26 reading turns with no edit on `parse/__init__.py` (1,091 lines) and on pyparsing `core.py` (6,981 lines). A whole-file read also fills the context with code that has nothing to do with the bug.

## Prior work (the design follows these)

| Source | What it does | Evidence |
|---|---|---|
| SWE-agent ACI (Yang et al., 2024, arXiv 2405.15793) | The file viewer shows a window of 100 lines with line numbers, plus a header ("N more lines above/below"). Commands are `open path [line]`, `goto n`, `scroll_up`/`scroll_down`, `search_file`, `search_dir` and `find_file`. Searches return a short summary, not every match. | Window-size ablation: 100 lines **18.0%**, full file **12.7%**, 30 lines **14.3%** resolved. |
| Agentless (Xia et al., 2024, arXiv 2407.01489) | Localization runs on a "skeleton" (compressed) format. It keeps class and function headers (signatures) and module-level statements, and elides bodies. The LLM then asks for specific classes or functions, and only those bodies are expanded. | Localization with the skeleton beats full-file context (58.3% vs 53.7% in the trick book's reading) and uses about 85% fewer tokens. |
| aider repo map (aider.chat/docs/repomap.html) | Uses tree-sitter "tags" of definitions and references. A graph ranking (PageRank) chooses which symbols fit a token budget. Output is the signature lines of definitions, with `⋮` marking elided code. | Design reference only: whole-repo maps within a budget. AWOS already has `repo_map.py` for this. |

T5 takes the 100-line window from SWE-agent and the per-file skeleton plus expand-by-name from Agentless. aider's global ranking is out of scope, because this spec covers a single file.

## Behaviour (`AWOS_SKELETON_VIEW=1`)

`read_file(path, start_line?, end_line?, symbol?)`:

1. **`symbol=` given** (Python only): returns the full source of that class or function, with line numbers and decorators. Names can be `Class.method` or a unique suffix such as `method`. An ambiguous or unknown name fails and lists the candidates. Bodies longer than 3× the cap (300 lines) are cut to the first 100 lines, with a continuation hint.
2. **No range, file ≥ `AWOS_SKELETON_MIN_LINES` (300)**:
   - `.py` files return the skeleton plus the hint "read_file with symbol=... or start_line/end_line". The skeleton contains:
     - the first line of the module docstring
     - module-level assigned names (folded runs)
     - every class and def, nested by indentation, with decorators, the full signature (`ast.unparse`), `# L<first>-<last>`, the first docstring line and class attribute names.
   - A `.py` file that fails to parse, or any non-Python file, returns lines 1–100 with the total count and a continuation hint.
3. **Explicit range**: works as before (same tolerant parsing of `"[20, 55]"` and similar), but returns at most `AWOS_SKELETON_MAX_LINES` (100) lines and says where to continue.
4. **Small file with no range**: returned whole, as before.
5. Output is line-numbered in the `cat -n` style (`%6d\t`) when the view is on. Without numbers, the skeleton's line numbers could not be used.
6. Every bounded answer prints `[SKELETON] <file> N lines -> skeleton|window a-b|symbol X M chars`.

`ShowSymbolTool` (`show_symbol(path, symbol)`) does the same as point 1. It is **not registered** yet: `agent_loop.default_registry` is owned by another work stream. Until it is registered, `read_file(symbol=...)` provides the same capability with no change to the registry.

With the flag off (the default), the parameters, description and output are byte-identical to before. A test checks this.

## Offline measurement ($0)

Series bases are read-only. "Fix def" is the innermost def that contains each changed base line in the diff between `jobs/01_*/reference` and `base`.

| Series | File | Lines | Full chars | Skeleton chars | Ratio | Fix def(s) listed with correct range |
|---|---|---|---|---|---|---|
| parse_137 | parse/__init__.py | 1091 | 35,991 | 4,866 | 13.5% | `Parser._handle_field` L650-865 ✓ (+ `ALLOWED_TYPES` named) |
| parse_159 | parse/__init__.py | 1090 | 35,806 | 4,866 | 13.6% | `Parser._handle_field` ✓ |
| parse_249 | parse/__init__.py | 1091 | 36,018 | 4,866 | 13.5% | `Parser._handle_field` ✓ (+ `__version__`) |
| pyparsing_332 | pyparsing/core.py | 6981 | 253,190 | 39,429 | 15.6% | `ParserElement.__mul__`, `_MultipleMatch.__init__/parseImpl`, `ZeroOrMore.__init__` ✓ |
| pyparsing_560 | pyparsing/helpers.py | 1220 | 41,798 | 4,500 | 10.8% | `match_previous_expr` + 2 nested defs ✓ |
| pyparsing_647 | pyparsing/common.py | 576 | 17,025 | 1,733 | 10.2% | `pyparsing_common.as_datetime` ✓ (+ an import line) |
| more-itertools_1250 | more_itertools/more.py | 5590 | 173,289 | 21,258 | 12.3% | `value_chain` ✓ |
| more-itertools_1284 | more_itertools/more.py | 5633 | 174,846 | 21,598 | 12.4% | `bucket.__init__/_get_values/__iter__` ✓ |
| more-itertools_1304 | more_itertools/more.py | 5636 | 175,085 | 21,598 | 12.3% | `ichunked` ✓ |

The skeleton is 10–16% of the full file, which matches Agentless's ~85% saving. In 9 of 9 cases, every changed line inside a def falls in a def the skeleton lists with its exact line range. The changes outside a def are a version string, a module constant (named in the skeleton), an import, and one insertion just past a def's end.

One caveat: parse's fix def `_handle_field` has 216 lines. That is more than the 100-line range cap, but within the 300-line cap for `symbol=`. This is why the symbol cap is set larger than the range cap.

## Risks

- **Reread guard in `agent_loop`**: `_read_span` keys reads on `(path, start, end)` and ignores `symbol`. It records a call with no range as `1–EOF`. As a result, a skeleton read followed by `symbol=` reads all look like the same whole-file span, and after `MAX_RANGE_READS` (3) of them the loop answers "already read" instead of the body. A capped range is also recorded at the size requested, not the size served. Fix (in agent_loop, not owned here): include `symbol` in the span key, or skip span tracking when `symbol` is set.
- **One-shot whole-source context**: the adopted one-shot path puts the whole source into the prompt, not through `read_file`. T5 does not affect it. It only changes agent-loop or fallback reads. A run with both on can show the whole file in the prompt and a skeleton from the tools. That is harmless, but measure T5 on agent-loop jobs.
- **Line-number prefixes**: the model may copy the `   123\t` prefixes into an `edit_file` `old_string`. Watch for edit-mismatch failures when the flag is on.
- **Untested on the solve rate**: there is no A/B yet. Decide with the T1 forward rule (paired Bayesian plus McNemar) before making it the default.
