# Transparent Routing — Agent Interface Spec

> **Status:** Spec — needs re-review under the coding-agent-first vision.
> **Goal:** The operator can see routing decisions, understand the tradeoffs, and override choices when needed. Those overrides become signals for better future routing.
> **See:** [[VISION]] · [[CI_RESCUE_PRODUCT]] · [[pivot_web_app_product]]

---

## Philosophy

**Transparency is a reliability feature, not a sales feature.**

The point of transparent routing is to help a user inspect and steer the coding agent:
- which model was chosen
- why it was chosen
- what alternatives existed
- when an override makes sense
- whether the result justified the cost

This supports the AWOS objective:

```text
Quality × Speed ÷ Cost
```

Routing should be legible enough to debug, not hidden behind product theater.

---

## What the operator sees (per task)

```text
┌── Task: Fix token validation ─────────────────────────────────────┐
│  Recommended model: DeepSeek V4                                  │
│  Reason: Standard logic repair, low risk, strong past results     │
│                                                                   │
│  Alternatives                                                     │
│  • Claude Sonnet — higher quality, higher cost                    │
│  • Gemini Flash  — cheaper, lower confidence                      │
│                                                                   │
│  Estimated tradeoff                                               │
│  • Quality: good                                                  │
│  • Speed: fast                                                    │
│  • Cost: low                                                      │
│                                                                   │
│  [Use recommended] [Try Sonnet] [Pin Sonnet for auth/]           │
└───────────────────────────────────────────────────────────────────┘
```

The operator should be able to tell whether the router is making a good decision without reading internal bandit math.

---

## Routing strategy panel

```text
┌── Routing Preferences ────────────────────────────────────────────┐
│                                                                   │
│  Default strategy: Balanced                                       │
│                                                                   │
│  [Minimum Cost ◀───────●──────────▶ Maximum Quality]              │
│                                                                   │
│  ─── Per-scope Overrides ───                                      │
│  Scope         │ Default   │ Override      │ Reason               │
│  ──────────────┼───────────┼───────────────┼──────────────────     │
│  auth/         │ DeepSeek  │ Sonnet ◀      │ security-sensitive   │
│  billing/      │ DeepSeek  │ —             │ —                    │
│  tests/        │ Gemini    │ —             │ low-risk iteration   │
│  docs/         │ Gemini    │ DeepSeek ◀    │ better writing       │
│                                                                   │
│  [Add override] [Reset overrides]                                │
└───────────────────────────────────────────────────────────────────┘
```

Overrides should be optional guardrails for real work, not a pricing upsell surface.

---

## Learning feedback

```text
┌── Routing Feedback ───────────────────────────────────────────────┐
│                                                                   │
│  Today                                                            │
│  • auth/ routed to Sonnet by operator override                    │
│  • billing/ stayed on DeepSeek and passed verification            │
│                                                                   │
│  Recent pattern                                                   │
│  • auth fixes succeed more often on Sonnet                        │
│  • docs tasks are cost-efficient on DeepSeek                      │
│                                                                   │
│  Result                                                           │
│  • router confidence updated for future tasks                     │
│  • fewer unnecessary escalations expected                         │
└───────────────────────────────────────────────────────────────────┘
```

The important signal is not “the customer trained the product.”
The important signal is that the coding agent gets better at choosing the right tool/model for future work.

---

## Budget visibility

```text
┌── Run Budget ─────────────────────────────────────────────────────┐
│                                                                   │
│  Session spend: $0.42                                             │
│  Session cap:   $2.00                                             │
│  Remaining:     $1.58                                             │
│                                                                   │
│  By task type                                                     │
│  • auth fixes:      $0.18                                         │
│  • billing fixes:   $0.09                                         │
│  • docs updates:    $0.15                                         │
│                                                                   │
│  Suggestion                                                       │
│  • auth tasks are escalating often                                │
│  • consider whether quality requirements justify Sonnet           │
└───────────────────────────────────────────────────────────────────┘
```

Budget display should expose real operational tradeoffs for the agent, not subscription-plan packaging.

---

## What the operator should not need to see

| Hidden or collapsed detail | Why |
|---|---|
| Raw API payloads by default | Too noisy for most runs |
| Token-count minutiae by default | Useful diagnostically, but not always first-order |
| Full routing internals | Show the decision, not every formula |
| Provider implementation trivia | Less important than task outcome |

Advanced details can still be expandable for debugging.

---

## UX principles

1. **Show reasoning, not math.** “Sonnet is recommended here because auth fixes have been fragile” is better than raw posterior scores.
2. **Keep overrides cheap to use.** A good operator can steer the agent quickly.
3. **Treat overrides as learning signals.** They should improve future routing decisions.
4. **Make cost real.** Show actual spend and caps, not packaging abstractions.
5. **Support debugging.** Transparency is there to help catch bad routing and wrong escalation.
6. **Keep the app aligned to the coding agent.** This is an interface for autonomous work, not a customer-pricing shell.

---

## Implementation notes

- LinUCB already tracks `action_id` (model choice) and `features` (task type, module)
- Add a `user_override` signal to routing/reward data where useful
- Add `module_path` or equivalent scope markers to improve per-area routing
- Store operator override patterns only when they improve future task selection
- Surface routing decisions in the app alongside plan, tool use, and verification
- Keep benchmark-specific routing views separate from top-level product identity

---

## Relationship to CI benchmark work

Transparent routing is useful for many workloads.
One important proving workload is the **CI repair benchmark** because it produces:
- repeatable failure discovery
- bounded task sequences
- verifiable outputs
- useful routing comparisons across models

That benchmark is a proving ground for routing quality.
It is not the identity of AWOS.

---

## Related

- [[VISION]] — product identity and objective
- [[CI_RESCUE_PRODUCT]] — CI repair benchmark capability spec
- [[PILOT_PLAN]] — execution path under the current vision
- [[memories/repo/project_structure]] — canonical facts
- [[pivot_web_app_product]] — active task file
- `scaffold/agent/ml_router.py` — LinUCB bandit
- `scaffold/agent/escalation_engine.py` — model ladder
- `gui/server.py` — web server
