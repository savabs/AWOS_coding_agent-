#!/usr/bin/env python3
"""
REALISTIC MULTI-CHAT WORKFLOWS

Showing how developers would actually use this in their daily work.
"""

WORKFLOW_BASIC = """
╔════════════════════════════════════════════════════════════════════╗
║              MULTI-CHAT WORKFLOW 1: Multiple Terminals             ║
║                    (Most Common in Practice)                       ║
╚════════════════════════════════════════════════════════════════════╝

MORNING WORK (9:00 AM):

┌─ TERMINAL 1: Project A (Auth Bug) ─────────────────────────────┐
│                                                                │
│  $ ai                                                          │
│  Session: auth-bug-2026-05-16                                 │
│  🤖 UNIFIED AI AGENT                                           │
│                                                                │
│  You: Help debug the JWT token expiry issue                   │
│  💾 Session activated: full context (800 tokens)              │
│  Cost: $0.016                                                 │
│  Assistant: I see the issue. The expiry is set to...          │
│                                                                │
│  You: What's the correct implementation?                      │
│  💾 Reusing context (NOT resent): 280 tokens                  │
│  Cost: $0.006 (60% cheaper!)                                  │
│  Assistant: Use this: ...                                     │
│                                                                │
│  You: exit                                                    │
│  [Saved: .awos/sessions/auth-bug-2026-05-16.json]           │
└─────────────────────────────────────────────────────────────────┘

┌─ TERMINAL 2: Project B (Schema Design) ────────────────────────┐
│                                                                │
│  $ ai                                                          │
│  Session: schema-design-2026-05-16                            │
│  🤖 UNIFIED AI AGENT                                           │
│                                                                │
│  You: Help design the user permissions schema                 │
│  💾 Session activated: full context (800 tokens)              │
│  Cost: $0.016                                                 │
│  Assistant: 3 tables needed: users, roles, permissions...     │
│                                                                │
│  You: What about role inheritance?                            │
│  💾 Reusing context (NOT resent): 300 tokens                  │
│  Cost: $0.006 (60% cheaper!)                                  │
│  Assistant: Use this approach: ...                            │
│                                                                │
│  You: exit                                                    │
│  [Saved: .awos/sessions/schema-design-2026-05-16.json]      │
└─────────────────────────────────────────────────────────────────┘

AFTERNOON WORK (2:00 PM):

┌─ TERMINAL 1: Different task (Docs) ────────────────────────────┐
│                                                                │
│  $ ai                                                          │
│  Session: api-docs-2026-05-16                                 │
│  🤖 UNIFIED AI AGENT                                           │
│                                                                │
│  You: Help write API documentation                            │
│  💾 Session activated: full context (800 tokens)              │
│  Cost: $0.016                                                 │
│  Assistant: Here's a structure for your docs...               │
│                                                                │
│  You: exit                                                    │
│  [Saved: .awos/sessions/api-docs-2026-05-16.json]           │
└─────────────────────────────────────────────────────────────────┘

COST SUMMARY:
  Morning, Terminal 1 (2 turns): $0.022
  Morning, Terminal 2 (2 turns): $0.022
  Afternoon, Terminal 1 (1 turn): $0.016
  ─────────────────────────────
  Total: $0.060 (vs $0.128 without session memory = 53% cheaper!)

FILES SAVED:
  .awos/sessions/auth-bug-2026-05-16.json
  .awos/sessions/schema-design-2026-05-16.json
  .awos/sessions/api-docs-2026-05-16.json
"""

WORKFLOW_ADVANCED = """
╔════════════════════════════════════════════════════════════════════╗
║            MULTI-CHAT WORKFLOW 2: Switching Between Tasks         ║
║                  (Resuming Previous Conversations)                 ║
╚════════════════════════════════════════════════════════════════════╝

DAY 1 (Tuesday, 9:00 AM):

  $ ai
  Session: auth-bug-2026-05-15
  You: What's wrong with the JWT token?
  Assistant: The expiry logic is...
  [work on it for 1 hour, then stop]
  You: exit

DAY 2 (Wednesday, 10:00 AM):

  $ ai --list
  
  📋 SAVED SESSIONS
  ════════════════════════════════════════════════════════════
  1. Session: auth-bug-2026-05-15
     Messages: 5
     Tokens: 2500
     Strategy: hybrid
     Summary: JWT expiry issue, found root cause in...
  
  2. Session: schema-design-2026-05-15
     Messages: 3
     Tokens: 1800
     Strategy: hybrid
  
  3. Session: api-docs-2026-05-16
     Messages: 2
     Tokens: 900
     Strategy: hybrid
  ════════════════════════════════════════════════════════════

  $ ai --resume auth-bug-2026-05-15
  
  ✅ RESUMED: auth-bug-2026-05-15
  Messages: 5
  Tokens: 2500
  Strategy: hybrid
  
  Continue your conversation. Type 'exit' to end.
  
  You: Did we test in production?
  💾 Context restored from YESTERDAY
     [SUMMARY] JWT expiry issue, found root cause...
     [RECENT] Previous discussion restored
  Cost: $0.006 (full context NOT resent!)
  Assistant: We haven't yet. Here's the test plan...
  
  You: Let's deploy the fix
  💾 Reusing context (same session)
  Assistant: Steps to deploy: ...

COST SUMMARY:
  Day 1, Morning (5 messages): $0.040
  Day 2, Morning (2 messages): $0.022 (resumed)
  ─────────────────────────
  Total: $0.062

  vs without session memory:
  7 messages × $0.016 = $0.112
  ────────────────────────
  Savings: $0.050 (45% cheaper!)

KEY INSIGHT:
  Session persists across DAYS
  You can resume ANY previous conversation
  Context is never resent - automatic reuse!
"""

WORKFLOW_PARALLEL = """
╔════════════════════════════════════════════════════════════════════╗
║           MULTI-CHAT WORKFLOW 3: Parallel Deep Work               ║
║        (Multiple Complex Projects Running Simultaneously)          ║
╚════════════════════════════════════════════════════════════════════╝

SCENARIO: Simultaneous work on 3 features

┌─ FEATURE 1: Auth System ──────────────────────────────────────┐
│  $ ai                                                         │
│  Session: feature-auth-2026-05-16                            │
│  ├─ Turn 1: Full context (800 tok) = $0.016                 │
│  ├─ Turn 2: Reused (280 tok) = $0.006                       │
│  ├─ Turn 3: Reused (295 tok) = $0.006                       │
│  ├─ Turn 4: Reused (310 tok) = $0.006                       │
│  └─ Turn 5: Reused (270 tok) = $0.006                       │
│     Total: $0.040 (5 turns)                                  │
└────────────────────────────────────────────────────────────────┘

┌─ FEATURE 2: Payment Integration ──────────────────────────────┐
│  $ ai                                                         │
│  Session: feature-payment-2026-05-16                         │
│  ├─ Turn 1: Full context (800 tok) = $0.016                 │
│  ├─ Turn 2: Reused (290 tok) = $0.006                       │
│  ├─ Turn 3: Reused (310 tok) = $0.006                       │
│  └─ Turn 4: Reused (280 tok) = $0.006                       │
│     Total: $0.034 (4 turns)                                  │
└────────────────────────────────────────────────────────────────┘

┌─ FEATURE 3: Analytics Dashboard ──────────────────────────────┐
│  $ ai                                                         │
│  Session: feature-analytics-2026-05-16                       │
│  ├─ Turn 1: Full context (800 tok) = $0.016                 │
│  ├─ Turn 2: Reused (305 tok) = $0.006                       │
│  └─ Turn 3: Reused (315 tok) = $0.006                       │
│     Total: $0.028 (3 turns)                                  │
└────────────────────────────────────────────────────────────────┘

SWITCHING BETWEEN THEM:

  # Work on auth for 30 min
  Terminal A: $ ai --resume feature-auth-2026-05-16
  
  # Switch to payment
  Terminal B: $ ai --resume feature-payment-2026-05-16
  
  # Check analytics
  Terminal C: $ ai --resume feature-analytics-2026-05-16
  
  # Back to auth
  Terminal A: [continue where you left off]

COST SUMMARY:
  Auth:      $0.040 (5 turns)
  Payment:   $0.034 (4 turns)
  Analytics: $0.028 (3 turns)
  ─────────────────────────
  Total:     $0.102 (12 turns)

  vs without session memory:
  12 turns × $0.016 = $0.192
  ───────────────────────
  Savings: $0.090 (47% cheaper!)

KEY INSIGHT:
  Can work on MULTIPLE projects simultaneously
  Each has its own context memory
  Switch anytime - no context loss
  Session memory works per-project!
"""

COMMAND_REFERENCE = """
╔════════════════════════════════════════════════════════════════════╗
║                      COMMAND REFERENCE                            ║
╚════════════════════════════════════════════════════════════════════╝

START NEW CHAT:
  $ ai
  └─ Creates new session with unique ID
  └─ Full context sent first time
  └─ Reused on follow-ups (40-60% cheaper)

SINGLE QUERY:
  $ ai "your question"
  └─ Quick question, auto-routes to best handler
  └─ Still creates session (can resume later)

LIST ALL SESSIONS:
  $ ai --list
  └─ Shows all saved sessions
  └─ Displays: ID, message count, tokens, strategy

SESSION INFO:
  $ ai --info auth-bug-2026-05-16
  └─ Details about specific session
  └─ Shows: messages, tokens, summary, strategy

RESUME SESSION:
  $ ai --resume auth-bug-2026-05-16
  └─ Continue previous conversation
  └─ Context automatically restored
  └─ Reuses memory (no context resent!)

HELP:
  $ ai --help
  └─ Show this reference

PRACTICAL TIPS:

✓ Use multiple terminal windows for parallel work
✓ Each terminal = one independent session
✓ Sessions saved automatically in .awos/sessions/
✓ Resume any session anytime (across days!)
✓ No commands needed for basic workflow
✓ Session memory is automatic (no config)

WORKFLOW PATTERNS:

Pattern 1: Multiple Windows (Easiest)
  Terminal 1: $ ai  [Feature A]
  Terminal 2: $ ai  [Feature B]
  Terminal 3: $ ai  [Feature C]
  → Each has independent memory

Pattern 2: Sequential Work (Same Window)
  $ ai [work on feature A]
  $ ai [work on feature B]
  $ ai [work on feature C]
  → Each is separate, can resume later

Pattern 3: Context Switching (Resume)
  $ ai --list
  $ ai --resume feature-a
  [work a bit]
  $ ai --resume feature-b
  [work a bit]
  $ ai --resume feature-a
  [continue where you left off]

COST IMPACT:
  • Session memory: 40-60% cheaper on multi-turn chats
  • Smart routing: Cheapest handler per request
  • Parallel work: No interaction between sessions
  • Daily resumption: Context persists across days

════════════════════════════════════════════════════════════════════
"""

# Print all workflows
print(WORKFLOW_BASIC)
print("\n")
print(WORKFLOW_ADVANCED)
print("\n")
print(WORKFLOW_PARALLEL)
print("\n")
print(COMMAND_REFERENCE)
