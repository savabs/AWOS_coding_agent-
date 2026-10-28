"""
teach_once — a deterministic, measurable "I do it once, then it learns it" testbed.

Spec (draft pre-registration): docs/specs/testbed_teach_once.md

    seed.py         the local work environment: 3 recurring tasks x 9 instances
                    (i01 = the demonstration instance, i02..i09 held out);
                    agent-visible files under <run>/visible, ground truth and
                    server state/logs under <run>/hidden
    webapps.py      three fake 127.0.0.1 web apps (orders portal, invoices
                    inbox, accounting) with accessible HTML and server-side
                    action logs written outside the agent sandbox
    browser.py      a tiny stdlib "accessibility browser": HTML -> role/name
                    elements, click / type / select / download by role+name
    trace.py        the semantic DEMONSTRATION TRACE format + Recorder API
                    (a real macOS AX-observer + FSEvents recorder plugs in here)
    demonstrate.py  scripted "human demonstrations" (one per task, on i01)
    compiler.py     trace -> reusable skill: parameters, invariants, check
    baselines.py    naive exact replay of the demo + a scripted oracle
    verify.py       stdlib-only verifier (no AWOS imports)
    run.py          runner skeleton (pluggable agent command; --dry-run)
"""

TASKS = ("t1_statements", "t2_orders", "t3_invoices")
DEMO_INSTANCE = "i01"
HELD_OUT = tuple(f"i{n:02d}" for n in range(2, 10))
INSTANCES = (DEMO_INSTANCE,) + HELD_OUT
