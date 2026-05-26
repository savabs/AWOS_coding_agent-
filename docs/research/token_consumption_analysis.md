---
title: "Token Consumption Research — Real Data"
tags:
  - doc/research
  - topic/cost-analysis
---

# Token Consumption Research — What's the Real Math?

**Research Goal:** Understand ACTUAL token consumption patterns in large code projects

**User Data Point:**
- 1000 requests/month
- "easily things went 1 million token" (1M tokens total, not per request)
- Budget constraint: $15/month
- Project: "very very huge level" (like Claude Sonnet 4.6 era usage)

---

## Key Questions to Research

### Q1: What's the input vs output token ratio?

For code analysis tasks:
- Input: Task description + code context (typically large)
- Output: Analysis/suggestion (typically smaller)

**Estimate range to research:**
- Simple tasks: input 70%, output 30%
- Complex tasks: input 60%, output 40%
- What's realistic for YOUR project?

### Q2: How many tokens per request type?

Different request types consume different token amounts:

| Request Type | Input Tokens | Output Tokens | Total | Notes |
|---|---|---|---|---|
| "List functions in file" | ? | ? | ? | Structure query |
| "Fix this bug" | ? | ? | ? | Code fix |
| "Refactor module" | ? | ? | ? | Architecture |
| "Add tests" | ? | ? | ? | Test generation |

---

## Analysis: Your 1M Token Budget

Given:
- 1000 requests/month
- 1 million tokens total
- **Average: 1000 tokens per request**

But this is the TOTAL of input + output.

### Breakdown Example (if 60/40 split)

Per request average:
- Input: 600 tokens
- Output: 400 tokens
- Total: 1000 tokens

At 1000 requests:
- Total input: 600,000 tokens
- Total output: 400,000 tokens
- Total: 1,000,000 tokens ✓

---

## Cost Analysis by Model (at 1M tokens/month, 60/40 split)

### DeepSeek ($0.14 input / $0.02 output)
```
Input: 600k × $0.14/1M = $0.084
Output: 400k × $0.02/1M = $0.008
Total: $0.092/month ✓ FITS $15 BUDGET
```

### Claude-Haiku ($0.80 input / $0.40 output)
```
Input: 600k × $0.80/1M = $0.48
Output: 400k × $0.40/1M = $0.16
Total: $0.64/month ✓ FITS $15 BUDGET
```

### Claude-Sonnet-5 ($5.0 input / $15.0 output)
```
Input: 600k × $5.0/1M = $3.00
Output: 400k × $15.0/1M = $6.00
Total: $9.00/month ✓ FITS $15 BUDGET
```

### OLD Claude-Sonnet-4.6 ($3.0 input / $15.0 output)
```
Input: 600k × $3.0/1M = $1.80
Output: 400k × $15.0/1M = $6.00
Total: $7.80/month ✓ FITS $15 BUDGET
```

---

## Critical Insight: Input vs Output Token Costs

Most code analysis tasks are INPUT-HEAVY because:

1. You send a lot of CODE context
2. Output is typically shorter (suggestions, fixes, explanations)

Example request:
```
INPUT:
- System prompt: 200 tokens
- Code context: 2000 tokens  ← LARGE
- Your question: 100 tokens
Total input: 2300 tokens

OUTPUT:
- Answer/suggestion: 400 tokens ← Smaller
Total output: 400 tokens

Total per request: 2700 tokens
```

But YOUR budget says 1M tokens for 1000 requests = **1000 tokens average**

That means either:
1. Most requests DON'T include 2000 tokens of code context
2. Or local analysis + caching MUST reduce input tokens
3. Or output tokens are kept very short

---

## What We Need to Know

### For YOUR actual project, research:

1. **What's your typical code context size?**
   - Small task (bug fix): 500 input + 200 output?
   - Medium task (refactor): 2000 input + 500 output?
   - Large task (design): 5000 input + 1000 output?

2. **Input/Output ratio?**
   - Code fixes: mostly input (show code, small output)?
   - Design tasks: mixed?
   - Bug analysis: mostly input?

3. **Cache reuse?**
   - How many requests use SAME code context?
   - If 2nd request uses cached context: only 200 new input tokens?

4. **Local analysis potential?**
   - How many requests need NO code context at all?
   - Example: "list all database models" = structure query, minimal tokens

---

## Model Pricing Comparison (Actual Rates)

### Current Models (2026)

**DeepSeek**
- Input: $0.14/1M
- Output: $0.14/1M (same rate!)
- Result: CHEAPEST option

**Claude-3.5-Haiku**
- Input: $0.80/1M
- Output: $0.40/1M
- Result: CHEAP but less capable

**Claude-3.5-Sonnet**
- Input: $5.0/1M
- Output: $15.0/1M
- Result: EXPENSIVE output kills you

**DeepSeek-Reasoner**
- Input: $0.55/1M
- Output: $0.55/1M
- Result: Good middle ground for reasoning

---

## The Real Calculation We Need

```
1000 requests/month
? tokens per request (UNKNOWN - need your data)
? input/output split (UNKNOWN)
? % cached requests (UNKNOWN)
? % local-solved (UNKNOWN)

Budget: $15/month

Constraint equation:
(Total Input Tokens × Input Rate) + (Total Output Tokens × Output Rate) ≤ $15
```

Until we know the unknowns, we can't solve this.

---

## Next Steps

**What I need from you:**

1. **Pick a representative request from your Sonnet era usage**
   - Full prompt sent to Claude
   - Full response received
   - Count tokens (rough estimate or actual from Claude)

2. **Or provide:**
   - How many tokens per request on average?
   - What % input vs output?
   - What % are structure queries (low token)?
   - What % are deep code analysis (high token)?

3. **Or:**
   - Show me your actual token usage breakdown from ChatGPT/Claude usage stats
   - Month view: tokens used by request type

**Then I can calculate:**
- Real cost projections by model
- Where to apply caching (which request types)
- Where to apply local analysis (which request types)
- True budget allocation

---

**Status:** Research incomplete - need actual token consumption data to proceed
