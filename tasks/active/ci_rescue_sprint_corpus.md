# Task: CI Rescue Sprint Corpus v1

**Status:** active  
**Research:** [`docs/research/ci_rescue_sprint_corpus.md`](../docs/research/ci_rescue_sprint_corpus.md)  
**Spec:** [`docs/specs/ci_rescue_sprint_corpus_spec.md`](../docs/specs/ci_rescue_sprint_corpus_spec.md)  
**Started:** 2026-06-25

---

## Context

First **Cursor-class** corpus: 28 reds, 5 modules, 18 mission tasks, pytest oracle. Synthetic `shopkit` fixture with `golden/` reference. Proves assertion + scale bar before live API sprint.

---

## Steps

### Phase 1 — Corpus

- [x] 1.1 Research + spec
- [x] 1.2 Fixture `tests/fixtures/wedge_v1/ci_rescue_sprint/` (28 red / 7 green)
- [x] 1.3 `golden/` reference modules
- [x] 1.4 Mission `docs/missions/ci_rescue_sprint.json` + 18-task plan
- [x] 1.5 `wedge_goal.json`

### Phase 2 — Proof infra

- [x] 2.1 `scripts/demo_ci_rescue_sprint.sh`
- [x] 2.2 `docs/ci_rescue_sprint_proof.md`
- [x] 2.3 `tests/test_ci_rescue_sprint_corpus.py`
- [x] 2.4 Live proof: run demo — watch `CI_RESCUE: red_count=28` + `SEMANTIC_PASS`

### Phase 3 — Live API

- [x] 3.1 `awos mission start --ci-rescue` (18 tasks, worktree)
- [x] 3.2 Assert SEMANTIC_PASS on worktree (`rs_d39c498dd4ca`, 49.7s, $0.0056)
- [x] 3.3 PEI pair vs raw API on same plan — see `docs/product/ci_rescue_head_to_head.md`

### Phase 4 — Real repo

- [ ] 4.1 Repeat sprint shape on external repo with organic CI reds

---

## Live proof checklist

- [x] Live proof: `./scripts/demo_ci_rescue_sprint.sh` — `CI_RESCUE: red_count=28`, `WEDGE_ASSERT: SEMANTIC_PASS`

---

## Session log

| Date | Done | Next |
|------|------|------|
| 2026-06-25 | Corpus + golden + mission + demo script | Run live demo; API mission |
