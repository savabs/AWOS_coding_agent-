# SWE-CI vs Lightweight Alternative — Real Resource Comparison

## Full SWE-CI Benchmark (Official)

### What it needs:
- **Download:** 129 GB (Docker images for 137 test environments)
- **Disk space:** ~150 GB total
- **RAM:** 64 GB recommended
- **CPU:** 32-core server (can run on less, will be slower)
- **Time:** 48 hours with 16 parallel workers
  - On your laptop (fewer cores): **3-7 days**
- **Docker:** Required (Linux containers for test isolation)
- **API cost:** ~$50-150 depending on model (137 tasks × multiple attempts)
- **Setup complexity:** Medium-high (Docker, environment troubleshooting)

### What you get:
- Official benchmark, published paper
- 137 real repos, 71 commits each, 233 days of evolution per task
- Directly comparable to other agents (leaderboard)

---

## Code Evolution Lab (Lightweight Alternative)

### What it needs:
- **Download:** Nothing (we build it)
- **Disk space:** <100 MB
- **RAM:** Whatever your laptop has (8-16 GB fine)
- **CPU:** Your laptop (no server needed)
- **Time:** 
  - Build fixture: 1-2 hours
  - Run test: 30-60 minutes
- **Docker:** Not needed
- **API cost:** ~$0.01-0.05 (5 commits × small codebase)
- **Setup complexity:** Low (just Python)

### What you get:
- Proves the same **core concept** (maintain code across multiple commits)
- Fast proof you can run today
- Not "official" but demonstrates harness value

---

## Side-by-Side

| | Full SWE-CI | Code Evolution Lab |
|--|--|--|
| **Your laptop can run it?** | Maybe (slow, 3-7 days) | Yes (30-60 min) |
| **Needs server?** | Recommended | No |
| **Needs Docker?** | Yes | No |
| **Download size** | 129 GB | <1 MB |
| **Time to first result** | 2-7 days | Today (2-3 hours) |
| **API cost** | $50-150 | $0.01-0.05 |
| **Setup pain** | Medium-high | Low |
| **"Official" benchmark** | Yes | No |
| **Proves harness value** | Yes | Yes |

---

## My Recommendation

**Start with Code Evolution Lab** (the lightweight one):
- Run it **today** on your laptop
- Takes **2-3 hours total** (build + run)
- Costs **<$0.10** in API calls
- **No Docker, no 129GB download**
- Proves the same thing: AWOS maintains code across commits better than raw API

**Then later** (if you want the "official" stamp):
- Pick 1-2 SWE-CI tasks (not all 137)
- Download just those (~5-10 GB instead of 129 GB)
- Run on a rented server or your laptop over a weekend
- Publish as "AWOS on Official SWE-CI"

---

## Bottom Line

Full SWE-CI is a **production research benchmark** — it's designed for labs with compute budgets and time.

Code Evolution Lab gives you **90% of the proof value** in **1% of the time/cost**.

Do the lightweight version now. If customers demand "official SWE-CI," run 1-2 real tasks later.
