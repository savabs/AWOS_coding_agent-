# Phase D — clawcode Real Repo Proof

**Config:** `docs/missions/stage1_phase_d_clawcode.json`  
**Proof:** `docs/phase_d_clawcode_proof.md`  
**Demo:** `scripts/demo_phase_d_clawcode.sh`

**Status:** IN PROGRESS

---

## Steps

- [x] Add `--root` to `awos worker start` and `awos run`
- [x] Add `--clawcode` mission flag + `codebase_root` in config
- [x] Create 14-task plan for clawcode
- [x] Demo script + proof guide
- [x] Live proof: run `./scripts/demo_phase_d_clawcode.sh` — **14/14 tasks, 40.5s, $0.0070, session rs_34d1a845a6b0**
- [x] Checkpoint with observed output (tasks, cost, cache hit rate)

---

## Command

```bash
python3 awos.py mission start --clawcode
```
