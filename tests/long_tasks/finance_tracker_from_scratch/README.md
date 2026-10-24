# finance_tracker_from_scratch (kind: greenfield)

The long-horizon task: build a whole stdlib-only personal-finance CLI (`fintrack`) from a spec in an
almost empty repo. `project/README.md` is the agent's only guide; it pins down 9 modules, the
dataclasses, signatures, text/JSON output formats and CLI exit codes, so the hidden tests can target them.
A strong model needs roughly 15-40 turns across ~10 files and has to make real design decisions
(SQLite schema, exact money storage, the occurrence-counting duplicate rule, all-or-nothing import).

Hidden tests: 58 pytest cases (44 test functions, two of them parametrized) in one file per feature
(`hidden_tests/test_f1_import.py` ... `test_f8_cli.py`). fintrack modules are imported lazily inside tests,
so a partial build earns partial credit per feature. Scoring: `docs/specs/long_horizon_finance_tracker.md`.
