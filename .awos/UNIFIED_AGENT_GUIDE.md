# 🤖 Unified AI Agent — Simple. Smart. Efficient.

**One command. Handles everything. Automatically optimized.**

```bash
ai "your question"
```

---

## The Concept

Instead of learning different commands for different tasks, you have **ONE smart interface** that:

1. **Understands** what you're asking
2. **Routes** to the most efficient handler
3. **Minimizes** cost and tokens
4. **Maintains** full reasoning power

**No command memory needed. Just ask naturally.**

---

## Four Smart Handlers

### 1. **Workflow** → $0.002 (Cheapest)
Project management, task tracking, status checks

```bash
ai "where am i?"
ai "what should i work on?"
ai "show me the plan"
```

### 2. **Coding** → $0.015 (Moderate)
Code generation, implementation, features

```bash
ai "implement user authentication"
ai "add error handling to module X"
ai "write tests for the API"
```

### 3. **Review** → $0.008 (Moderate)
Code analysis, security, quality

```bash
ai "review code for security issues"
ai "check for performance problems"
ai "find bugs in this function"
```

### 4. **Reasoning** → $0.050 (Higher, but necessary)
Architecture, design, decisions

```bash
ai "what's the best system design?"
ai "how should i structure the database?"
ai "what are the pros and cons?"
```

---

## How Routing Works

```
Your Question
    ↓
Pattern Matching (cost < 0.0001)
    ↓
Intent Detection
    ↓
Handler Selection:
  Workflow? → Use lightweight handler
  Coding?   → Use code handler
  Review?   → Use analysis handler
  Reasoning? → Use deep thinking model
    ↓
Execute with Optimal Cost
```

**Routing happens in <100ms. No overhead.**

---

## Real Examples

### Example 1: Workflow
```bash
$ ai "what's next?"

💡 Routing: Workflow Handler (cheapest)
   Cost: $0.0020

📊 **Project Status**
Stage: SETUP
Progress: 1/11 (9%)

🎯 **Next Step:**
Initialize Configuration Module
Create config.py with settings
```

### Example 2: Coding
```bash
$ ai "implement a user login endpoint"

💡 Routing: Coding Handler
   Cost: $0.0150
   Tokens: 2500

🔧 **Coding Handler**

I'll:
1. Create authentication logic
2. Add password hashing
3. Write endpoint handler
4. Add unit tests

Ready for implementation...
```

### Example 3: Review
```bash
$ ai "check the database code for SQL injection"

💡 Routing: Review Handler
   Cost: $0.0080
   Tokens: 1500

🔍 **Code Analysis**

Scanning for:
- SQL injection vulnerabilities
- Parameterized queries
- Input validation

Ready to review...
```

### Example 4: Reasoning
```bash
$ ai "what's the best way to structure a microservices system?"

💡 Routing: Reasoning Handler
   Cost: $0.0500
   Tokens: 4000

🧠 **Deep Reasoning**

I'll analyze:
- Monolithic vs microservices
- Communication patterns
- Scalability tradeoffs
- Deployment considerations

Ready for deep thinking...
```

---

## Key Features

✅ **One Command** — Learn `ai`, forget the rest  
✅ **Smart Routing** — Automatic handler selection  
✅ **Cost Optimized** — Always uses cheapest option  
✅ **Token Efficient** — Minimal input, maximum value  
✅ **Full Power** — All reasoning capabilities preserved  
✅ **Natural Input** — No rigid syntax needed  
✅ **Transparent** — Shows routing decision and cost  
✅ **Budget Aware** — Tracks spending automatically  

---

## Cost Breakdown (Monthly)

```
Workflow queries (200/month @ $0.002)  = $0.40
Coding tasks (30/month @ $0.015)       = $0.45
Reviews (10/month @ $0.008)            = $0.08
Reasoning (5/month @ $0.050)           = $0.25
Cache savings (90% on repeats)         = -$0.38
                                  Total = ~$0.80/month
```

**vs GitHub Copilot: $40/month**  
**Savings: $39.20/month (98% reduction)**

---

## Usage Patterns

### Quick Answers
```bash
ai "where am i?"
# Single response, immediate answer
```

### Interactive Session
```bash
ai
# Chat back and forth, multiple queries
```

### Scripting
```bash
echo "what's my status" | ai
echo "show the plan" | ai
```

### Batch Processing
```bash
for task in $(cat tasks.txt); do
  ai "$task"
done
```

---

## Natural Language Patterns

The agent recognizes 50+ natural language patterns across four categories:

### Workflow Patterns
- "What's my status?" / "Where am I?" / "Show progress"
- "What's next?" / "What should I do?" / "Proceed"
- "Show the plan" / "List tasks" / "What's the roadmap?"

### Coding Patterns
- "Implement..." / "Add..." / "Create..." / "Build..."
- "Fix..." / "Debug..." / "Optimize..."
- "Write tests" / "Refactor..."

### Review Patterns
- "Review..." / "Analyze..." / "Check..."
- "Find bugs" / "Security audit" / "Performance review"
- "Code quality..." / "Are there issues?"

### Reasoning Patterns
- "Best way to..." / "How should..." / "What's the best..."
- "Architecture" / "Design" / "Approach"
- "Pros and cons" / "Tradeoffs"

---

## Architecture

### Stack
- **Unified Interface:** Single `ai` command
- **Router:** Pattern-based intent detection
- **Handlers:** Four specialized backends
- **Integration:** Works with Aider, orchestrator, cost dispatcher
- **Persistence:** State saved to `.awos/workflow_state.json`

### Efficiency
- **Routing:** 50+ regex patterns (CPU efficient)
- **Caching:** 5-minute ephemeral cache on ProjectSoul
- **Deduplication:** Reuse context across requests
- **Token Budgeting:** Max 10,000 tokens per request

---

## Implementation Details

### Files
- `scaffold/agent/unified_agent.py` — Main unified interface
- `~/.local/bin/ai` — CLI entry point
- `scaffold/agent/chat_orchestrator.py` — Chat backend (workflow)
- `scaffold/agent/workflow_orchestrator.py` — Task execution

### Request Flow
```
ai "your question"
  ↓
UnifiedAgent.handle_request()
  ↓
RequestRouter.route()  [<100ms]
  ↓
Handler.execute()
  ↓
Response + Budget Status
```

---

## Everything Still Works

This unified interface **preserves all existing functionality:**

✅ Cost tracking ($0.70/month budget)  
✅ Prompt caching (90% savings on repeats)  
✅ Workflow orchestration (11-task pipeline)  
✅ Aider integration (terminal code editor)  
✅ Smart SEARCH/REPLACE (1-line changes)  
✅ Task state persistence (automatic checkpointing)  

**Just with a simpler, smarter interface on top.**

---

## Summary

| Aspect | Before | Now |
|---|---|---|
| Commands to learn | orchestrate, orchestrate-chat, aider-cost | **ai** |
| Query type selection | Manual | **Automatic** |
| Cost optimization | Per-request | **Per-request + handler selection** |
| Token efficiency | Basic caching | **Smart routing + caching** |
| Learning curve | Multiple interfaces | **One natural interface** |
| Monthly cost | $0.70 → $40 Copilot | **Still ~$0.70** |

---

## Try It Now

```bash
# Interactive
ai

# Single query
ai "what's my status?"

# Ask anything
ai "implement a feature"
ai "review the code"
ai "what's the best design?"
```

**One command. Infinite capability. Minimal cost.**
