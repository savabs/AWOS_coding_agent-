# CI Rescue Sprint — Live Proof

> **Spec:** [`docs/specs/ci_rescue_sprint_corpus_spec.md`](specs/ci_rescue_sprint_corpus_spec.md)

---

## Scenario

Mini commerce kernel (`auth`, `billing`, `inventory`, `shipping`, `promo`) with **28 failing tests** and **7 regression greens**. Simulates an afternoon CI rescue job at Cursor-substitution scale.

---

## Command (no API)

```bash
chmod +x scripts/demo_ci_rescue_sprint.sh
./scripts/demo_ci_rescue_sprint.sh
```

---

## Success markers

| Marker | Meaning |
|--------|---------|
| `CI_RESCUE: red_count=28` | Corpus meets ≥15 red bar |
| `CI_RESCUE: golden 35/35 pass` | Reference fix is complete |
| `WEDGE_ASSERT: SEMANTIC_PASS` | Assertion gate accepts sprint |

---

## Live API proof (completed 2026-06-25)

```bash
python3 awos.py mission start --ci-rescue
```

| Marker | Observed |
|--------|----------|
| Session | `rs_d39c498dd4ca` |
| Tasks | **18/18** completed |
| Wall time | **49.7s** |
| Cost | **$0.0056** |
| `WEDGE_ASSERT: SEMANTIC_PASS` | ✅ (35/35 pytest after `__pycache__` assert fix) |

Worktree: `tests/fixtures/wedge_v1/ci_rescue_sprint/.awos/worktrees/d39c498dd4ca`

---

## Anti-cheat

- `forbidden_paths`: `tests/`, `conftest.py`, `golden/`
- Learning only from `SEMANTIC_PASS` runs
