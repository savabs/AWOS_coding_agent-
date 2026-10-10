# SWE-CI Adaptation Strategy

**Goal:** Prove AWOS harness handles multi-commit code evolution better than raw API, using SWE-CI principles.

## SWE-CI Reality

| Metric | Value |
|--------|-------|
| Full dataset | 137 tasks (v2.0), 100 in default split |
| Download size | **129 GB** (Docker images + source archives) |
| Runtime | **~48 hours** with 16 workers |
| Infrastructure | Docker required, Linux, 32-core CPU recommended |
| Per-task | Average 71 commits, 233 days evolution, 500+ lines changed |

**Verdict:** Full SWE-CI run is a **production benchmark**, not a quick proof.

---

## Three Realistic Paths

### Option A: **Mini SWE-CI** (1-2 tasks, Docker required)
- Download 1-2 smallest SWE-CI tasks (~2-5 GB each)
- Run full SWE-CI protocol (architect + programmer dual-agent loop)
- Compare AWOS vs raw API on same task
- **Pros:** Official benchmark, real repos
- **Cons:** Docker setup, ~4-8 hours per task, infrastructure heavy

### Option B: **Synthetic Multi-Commit Evolution** (lightweight, no Docker)
- Build a fixture like `ci_rescue_sprint` but with **3-5 sequential commits**
- Each commit adds 2-3 bugs, agent must fix and maintain green as code evolves
- Simulates SWE-CI's "maintainability over time" without Docker
- **Pros:** Fast (<1 hour), reproducible, no Docker
- **Cons:** Synthetic, not "official" benchmark

### Option C: **Aider Polyglot Benchmark** (225 exercises, public, established)
- Paul Gauthier's benchmark: 225 hard exercises, 6 languages, hidden tests
- Measures raw coding ability (not evolution, but honest and well-maintained)
- Compare AWOS vs raw on 20-30 Python exercises
- **Pros:** Public, trusted, no Docker, ~2-4 hours
- **Cons:** Single-shot fixes (not multi-commit evolution like SWE-CI)

---

## Recommendation: **Option B** (Synthetic Multi-Commit) + **Future: Option A** (1-2 Real SWE-CI Tasks)

### Why Option B Now
1. **Fast proof** (~1-2 hours to build + run)
2. **Captures SWE-CI core insight:** maintainability over multiple commits
3. **No infrastructure barrier** (no Docker, runs on laptop)
4. **Demonstrates harness value:** raw API regresses or breaks green; AWOS maintains

### Implementation: "Code Evolution Lab"
```
tests/fixtures/wedge_v1/code_evolution/
  ├── commit_1/  # 5 modules, 15 tests pass
  ├── commit_2/  # adds 3 features → 3 new tests fail
  ├── commit_3/  # refactor 2 modules → 2 old tests break
  ├── commit_4/  # new edge case → 1 test fails
  └── commit_5/  # final state → all 21 tests should pass
```

**Scenario:**
- Agent starts at commit_1 (baseline, all green)
- Sequentially applies commit_2 → commit_3 → commit_4 → commit_5
- At each commit, run tests → identify failures → fix → maintain green
- **Raw API:** likely breaks earlier green tests or ships brittle fixes
- **AWOS:** verify loop catches regressions, maintains green across evolution

**Proof metric:**
- **Completion:** Did agent reach commit_5 with all 21 tests green?
- **Regression count:** How many times did agent break previously-green tests?
- **Cost:** $ spent
- **Time:** wall-clock seconds

### Future: 1-2 Real SWE-CI Tasks
Once we've proven the concept with synthetic evolution:
- Download 1-2 smallest SWE-CI tasks (~2-5 GB)
- Set up Docker locally
- Run full SWE-CI protocol (4-8 hours)
- Publish result as "AWOS on Official SWE-CI"

---

## Decision

**Immediate:** Build **Code Evolution Lab** (Option B) — 3-5 commit sequence, simulates SWE-CI principles, <2 hours total.

**After proof:** Consider 1-2 real SWE-CI tasks if Docker + time available.
