# Local-model functional check (Qwen3.5-9B, laptop, 2026-10-16)

Not a benchmark: one small real task to check that the local-reliability work (B1–B5 plus the cloud's REA items) runs
end to end on the real model. Owner-approved 5-minute check, $0, on battery (41%).

**Setup:** `scripts/local_model.sh start` (Qwen3.5-9B Q4_K_M, ctx 24576, battery policy, admission 8469/12288 MB),
then `awos "<goal>"` with `AWOS_PROVIDER=local`, default `loop` engine with grammar on. Task: `parse_duration` in a
scratch repo rejected combined durations (`1h30m`); 1 of 3 tests failing.

| | run 1 (31e41c2^) | run 2 (31e41c2) |
|---|---|---|
| outcome | ✗ `max_steps` (60), branch `awos/b9975a34-failed`; tests 3/3 passed on its tree | ✓ submitted, gate green (3/3), branch `awos/375cf035` |
| steps / time | 60 / 8m15s | **21 / 5m06s** |
| format errors / parse failures | 0 / 0 | 0 / 0 |
| cached fraction (input) | 97.5% | 91.2% |
| prefix cache | `saved` (first build) | **`restored`** |
| mean prefill per step | 152 tokens, 1.34 s | 275 tokens, 1.74 s |
| stuck detector (log mode) | repeat_command at step 18, escalate recommended at 30 | repeat_command at step 12, no escalation |
| $ | 0 | 0 |

**Bug found and fixed (31e41c2):** Qwen submitted by putting the bare marker inside a ```bash block, which ran as a
command ("command not found") and was repeated until `max_steps`. A bash call whose command is the bare marker now
submits; the marker inside a longer command still runs. Tests: `test_bare_marker_in_bash_block_submits`,
`test_marker_inside_a_longer_command_still_runs`.

**Observations:**
- The grammar holds on the real model (0 format errors over 81 replies).
- Prefix reuse works: llama-server keeps 85–99.7% of the slot each step.
- Generation is about 7 tok/s on battery.
- `prefix_drift_tokens` is non-zero every call (~70–140 tokens), which is worth a look; the cached fraction stays above 90%.
- The fix itself is correct: regex for one or more number+unit parts, summed, garbage still rejected.

**Next (pre-registered, needs AC power):** H2 (loop-local vs mini-local, 15 issues) and H2-g (grammar on/off), amendment 5.
