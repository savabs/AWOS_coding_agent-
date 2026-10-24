# Discovery posts (drafts, not posted)

Three short, honest posts. Each one asks a question, because the goal is to find out who needs a local agent, not to
sell one. Before posting, re-read the evidence rules below; the owner posts by hand.

**Evidence rules (all three posts):**
- Only cite `docs/memory/runs/ladder_h1d_loop_2026-10-15.md` (40/40 vs 39/40 on Haiku 5.5, 0.62x cost,
  statistically a tie) and `docs/memory/runs/local_check_qwen_2026-10-16.md` (one task, Qwen3.5-9B, 21 steps,
  5 min, $0, 0 format errors; the first attempt failed on an AWOS bug that was then fixed).
- Never say AWOS beats anything on a local model. The local comparison (H2) has not run yet. Say so.
- Each link carries `?src=` so the waitlist can count sign-ups per post (first-party, no cookies or trackers).

Install: `curl -fsSL https://awos.tirramind.com/install.sh | sh`

---

## 1. r/LocalLLaMA

**Title:** I'm building a coding agent tuned for small local models. Here's an early honest result, and a question

**Body:**

I'm building AWOS, a coding agent that runs entirely on your own machine. You give it an issue, it works in a separate
git worktree, runs your tests, and hands back a branch. Nothing is pushed. The point is making a *small* local model
reliable enough to be useful: constrained reply format (grammar), prefix-cache reuse, stuck detection, and test-gated
handoff.

What I can show so far:

- **Qwen3.5-9B (Q4_K_M) through llama-server, on a laptop on battery:** it fixed a real failing test in a small scratch
  repo in 21 steps and about 5 minutes, at $0. There were 0 malformed replies out of 81, and about 91% of the prompt
  was reused from cache. The first attempt ran out of steps because of a bug in my harness (the model put the submit
  marker inside a bash block), which I fixed. This is one task, so it's a functional check, not a benchmark.
- On a cloud model (Haiku), the same agent loop solved 40/40 fresh Python issues vs mini-swe-agent's 39/40, at 0.62x
  the cost. That's a statistical tie, and I'm not claiming more.

What I have **not** shown yet: that AWOS makes a local model better than a minimal agent loop. That's a pre-registered
15-issue test I'm running next, and I'll post the result either way.

Install (macOS/Linux): `curl -fsSL https://awos.tirramind.com/install.sh | sh`, then set `AWOS_PROVIDER=local` and
point it at your llama-server.

My question for this sub: **if you run models locally for coding, what's the actual reason?** Privacy, cost, offline
work, or just because you can? And what stops you using a local model for agent work today? There's a
two-question waitlist here if you want to hear when the local results are out:
https://awos.tirramind.com/?src=localllama#waitlist

---

## 2. Show HN

**Title:** Show HN: AWOS – a coding agent that runs on your own computer with a local model

**Body:**

AWOS is a coding agent that runs on your own machine. Give it an issue and it works in its own git worktree, runs
your tests, and leaves a branch for you to review. It never pushes. With a local model (llama-server and Qwen3.5-9B in
my tests) your code stays on your machine and a task costs $0.

Where the evidence actually stands:

- Cloud (Claude Haiku), pre-registered: the AWOS loop solved 40/40 fresh Python issues and mini-swe-agent solved
  39/40, at 0.62x the cost per run. That's a tie within noise. A strong cloud model does most of the work; the
  harness adds little there.
- Local: one real run on a laptop. Qwen3.5-9B fixed a failing test in 21 steps and 5 minutes, with 0 malformed
  replies. The first attempt failed on a harness bug I've since fixed. That's one task, not a benchmark.
- Not shown yet: whether AWOS beats a minimal loop on a local model. That test is pre-registered and runs next.

I think the useful version of this is private, offline, $0-per-task agents on hardware you already own, eventually
for general computer use and not just code. I don't yet know who needs that most, so I'd really like to hear from
people who can't, or won't, send code to cloud AI. What would it need to do for you?

Install: `curl -fsSL https://awos.tirramind.com/install.sh | sh`
Waitlist (two questions): https://awos.tirramind.com/?src=hn#waitlist

---

## 3. r/developersIndia

**Why this community:** it is the largest active Indian developer community online, and it regularly discusses
tool costs, so it is the cheapest way to test the "cost-sensitive individual developer" segment from
`docs/STRATEGY.md`. Cloud agent subscriptions are priced in dollars, which makes them a much bigger share of an
Indian developer's salary. Before posting, check the subreddit's current self-promotion rules. If promotion is
limited to a weekly showcase thread or a flair, post there.

**Title:** Built a coding agent that runs on your own laptop with a free local model ($0 per task). Is cost what stops you using AI agents?

**Body:**

I'm building AWOS from India. It's a coding agent that runs fully on your own computer. You give it a bug
or a small task, it works in a separate copy of your repo, runs your tests, and gives you a branch to review. With a
local model there is no API bill and no subscription, and your code never leaves your machine.

Honest status:
- On my laptop, Qwen3.5-9B (free, runs through llama-server) fixed a real failing test in 21 steps and about 5
  minutes, for $0. It's one small task, not a benchmark, and the first attempt failed on a bug in my code that I
  then fixed.
- On a paid cloud model it matched an established open-source agent (40/40 vs 39/40) at about 60% of the cost.
- I have **not** yet shown that it beats other agents on local models. That test is next.

Install (macOS/Linux): `curl -fsSL https://awos.tirramind.com/install.sh | sh`

Questions for you:
1. Do you use AI coding tools today? Who pays for them, you or your company?
2. Is cost the blocker, or is it company policy about sending code to the cloud?
3. What laptop do you code on? (RAM matters a lot for local models.)

Two-question waitlist: https://awos.tirramind.com/?src=devindia#waitlist
