---
name: awos-debug-safety
title: "AWOS debugging and safety"
tags:
  - doc/skill
  - topic/safety
description: Use when debugging repeated failures or changing shell execution, sandboxing, permissions, filesystem boundaries, secrets, network access, or recovery behavior in AWOS.
---

# AWOS debugging and safety

After two unsuccessful fixes for the same problem, stop patching and switch to explicit debug mode using `protocols/DEBUG_PROTOCOL.md`.

Before each edit, write a falsifiable hypothesis:

- “I think the bug is at `<location>` because `<evidence>`.”
- “This check would disconfirm it: `<specific reproduction or assertion>`.”

Then reproduce, instrument only the relevant path, verify the hypothesis, apply one focused fix, and run a regression test.

For execution or sandbox changes:

1. Read `protocols/SECURITY_PROTOCOL.md` and the governing sandbox spec/task.
2. Check workspace containment, symlink and traversal handling, approval binding, process-group cleanup, timeout behavior, output limits, secret exposure, and network policy.
3. Use `shell=False` and structured argv for acceptance/evaluation commands.
4. Preserve evidence of commands, outcomes, changed paths, and failures without logging credentials or sensitive data.
5. Never treat truncation, timeout, missing output, or a provider self-report as success.
6. Verify failed tests are terminal for the current execution and do not trigger misleading verifier replanning.
7. Do not claim container isolation, network isolation, or full sandbox security unless a test proves that exact property.

Report known limitations explicitly. A partial safety control is better than a misleading “secure” label.
