"""
compiled — compiled verified Tools and Routines (Trick T8).

Spec: docs/specs/compiled_tools_design.md. Built so far (steps 1-2):

  repetition.py  the repetition meter: an always-on, cheap intent fingerprint per
                 finished task, appended to .awos/repetition/log.jsonl
  record.py      the Tool/Routine record format (dataclasses + JSON schema), the
                 params-schema validator with taint marks, and the
                 preconditions fingerprint
  beta.py        the Beta lower-bound promotion/demotion math and state machine
  admit.py       the admission-harness skeleton (A1, A2, A3, A4, A6)
  examples/      hand-written example Tools (bump_version)

Nothing here is wired into the orchestrator: the hook is described in the spec's
implementation notes (§12.1). Nothing calls an LLM.
"""
