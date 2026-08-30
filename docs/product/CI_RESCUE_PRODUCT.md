# CI Repair Benchmark — Capability Spec

> **Path note:** this file historically framed CI Rescue as the product wedge. It is now a capability/benchmark spec under the coding-agent-first vision.

---

## Purpose

`awos fix-ci` is a **benchmark workflow** for the coding agent.

It is useful because it stresses:
- failure discovery
- deterministic planning
- bounded repair work
- verification
- retry logic
- model/tool routing

It is **not** the identity of AWOS.

---

## Workflow

```bash
awos fix-ci
```

What happens:
1. discover failing tests
2. group failures into actionable work
3. generate a plan
4. execute repairs
5. verify the result
6. record outcomes for future learning

---

## Why this benchmark still matters

CI repair is a good benchmark because it is:
- concrete
- verifiable
- bounded
- hard enough to expose real weaknesses

It helps validate the coding agent before broader self-learning claims are made.

---

## What this benchmark can prove

- the coding agent can navigate a medium-scale bugfix task
- verification catches false greens
- routing and tools reduce wasted cost
- the system can observe failures for later improvement

---

## What this benchmark cannot prove alone

- that AWOS is only a CI system
- that the app identity should be CI-first
- that self-learning already works broadly
- that the OS vision is complete

---

## Example output

```text
AWOS Coding Agent — CI Repair Complete

Repo: my-broken-repo
Time: 4m 32s
Cost: $0.0056
Result: verified repair run complete
```

---

## Success markers

- tests discovered correctly
- repair tasks generated clearly
- verification is honest
- output diff is reviewable
- run is logged for later analysis

---

## Relationship to the larger vision

CI repair is one proving workflow inside:
1. proper coding agent
2. self-learning coding agent
3. AI-operated OS

Use it as a benchmark, not as the top-level framing.

---

## Related

- [[VISION]] — canonical identity
- [[pivot_web_app_product]] — interface pathway
- [[wedge_v1_test_rescue]] — internal benchmark task
- [[TRANSPARENT_ROUTING]] — routing UX
