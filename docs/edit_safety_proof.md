# Edit-Safety Proof (Validate-Before-Write + Ambiguity Rejection)

## Command

```bash
python3 - <<'PY'
import tempfile
from pathlib import Path
from scaffold.agent.coding_tools import EditFileTool
d = Path(tempfile.mkdtemp()); (d/"mod.py").write_text("def ok():\n    return 1\n")
tool = EditFileTool(d)
r = tool.execute({"path":"mod.py","old_string":"def ok():","new_string":"def broken(:"})
print("[PROOF] invalid edit result:", r.success, r.error)
print("[PROOF] file unchanged:", d.joinpath("mod.py").read_text() == "def ok():\n    return 1\n")
assert not r.success and "invalid Python" in r.error
assert d.joinpath("mod.py").read_text() == "def ok():\n    return 1\n"
print("[PROOF] PASS — validate-before-write: no invalid Python left on disk")
PY
```

## Success Markers

- `[PROOF] invalid edit result: False Edit would produce invalid Python: ...`
- `[PROOF] file unchanged: True`
- `[PROOF] PASS — validate-before-write: no invalid Python left on disk`

## What It Demonstrates

1. **Validate-before-write**: `EditFileTool` compiles the candidate content first and refuses to persist invalid Python — the file is never mutated on failure.
2. **Ambiguity rejection** (verifier): fuzzy matches with near-tie candidate windows are rejected with an explicit "ambiguous" error instead of guessing the wrong location.

## Failure Signals

- An invalid edit reports success.
- The file content changes despite a failed/invalid edit.

## Automated Coverage

- `tests/test_edit_safety.py` — invalid edit no-write, new-file no-create-on-invalid, valid edit applies, blank-overwrite guarded, new-file-requires-content.
- `tests/test_verifier_ambiguity.py` — ambiguous fuzzy rejected, unambiguous fuzzy applies.
- `tests/test_verifier_fidelity.py` — fidelity checks still pass.
