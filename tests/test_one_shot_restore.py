"""
v0.2.1: a failed one-shot attempt's edits are undone before the agent loop,
in the host job's own workspace only (scaffold/agent/workspace_restore.py).

No network: same fakes as test_one_shot.py; the project is a real git repo.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(__file__))

from test_one_shot import FIX, GREEN, RED, _orchestrator, _Scripted, _SeqClient  # noqa: E402

from scaffold.agent import workspace_restore as wr  # noqa: E402
from scaffold.agent.agent_loop import ModelReply  # noqa: E402

NEW_FILE = """
pkg/extra.py
```python
<<<<<<< SEARCH
=======
EXTRA = 1
>>>>>>> REPLACE
```
"""


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True,
                          text=True).stdout


def _repo() -> Path:
    root = Path(tempfile.mkdtemp())
    (root / "pkg").mkdir()
    (root / "pkg" / "__init__.py").write_text("from .calc import add\n", encoding="utf-8")
    (root / "pkg" / "calc.py").write_text(
        "def add(a, b):\n    return a - b\n\n\ndef mul(a, b):\n    return a * b\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_calc.py").write_text(
        "from pkg import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base")
    # uncommitted work from an earlier task of the same job: must survive
    calc = root / "pkg" / "calc.py"
    calc.write_text("# earlier task\n" + calc.read_text(), encoding="utf-8")
    (root / "pkg" / "notes.py").write_text("NOTE = 'untracked earlier work'\n", encoding="utf-8")
    return root


def _run(client, test_results, *, job_workspace=True, restore="1"):
    root = _repo()
    orch = _orchestrator()
    orch._run_files_changed = set()
    ctx = {"codebase_root": str(root), "git": MagicMock(), "session": MagicMock(),
           "codebase_context": {}, "exploration": {}}
    spec = SimpleNamespace(model_id="claude-haiku-4-5", name="Haiku", cost_per_req=0.01,
                           level=SimpleNamespace(value=3))
    runner = MagicMock()
    runner.return_value.run.side_effect = list(test_results)
    scripted = _Scripted([ModelReply(text="Done.", input_tokens=100, output_tokens=20)])
    usage = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
    env = {"AWOS_ONE_SHOT": "1", "AWOS_ONE_SHOT_RESTORE": restore,
           "AWOS_JOB_WORKSPACE": str(root) if job_workspace else ""}
    with patch.dict(os.environ, env), \
         patch("scaffold.agent.agent_loop.build_client_from_env", return_value=scripted), \
         patch("scaffold.agent.one_shot.one_shot_client", return_value=client), \
         patch("scaffold.agent.orchestrator.TestRunner", runner):
        out = orch._execute_task_via_agent_loop(
            {"task_id": 1, "action": "fix add in pkg/calc.py", "file": "pkg/calc.py"}, ctx,
            task_id=1, esc_decision=SimpleNamespace(spec=spec), _span=MagicMock(),
            _strategy=SimpleNamespace(name="default"), _task_ts=0.0, _live_t=None,
            _task_usage=usage,
        )
    return SimpleNamespace(out=out, root=root, scripted=scripted)


def test_red_one_shot_is_reverted_before_the_agent_loop(capsys):
    r = _run(_SeqClient(FIX + NEW_FILE), [RED, GREEN])
    calc = (r.root / "pkg" / "calc.py").read_text()
    assert "return a - b" in calc and "return a + b" not in calc     # attempt undone
    assert calc.startswith("# earlier task\n")                       # earlier work kept
    assert not (r.root / "pkg" / "extra.py").exists()                # created file removed
    assert (r.root / "pkg" / "notes.py").exists()                    # untracked work kept
    assert _git(r.root, "status", "--porcelain").count("\n") == 2    # calc.py (M) + notes.py (??)
    assert r.scripted.prompts, "AgentLoop must run"
    prompt = r.scripted.prompts[0]
    assert "edits were reverted" in prompt and "assert 4 == 3" in prompt
    out = capsys.readouterr().out
    assert "[ONE-SHOT] fell back to the agent loop: tests: 1 passed, 1 failed" in out
    assert "restored the workspace to its pre-attempt state" in out
    assert "1 file(s) reverted, 1 removed" in out


def test_green_but_incomplete_edits_are_kept_by_design(capsys):
    # cut-off reply with green tests: partially correct by design, loop continues from it
    r = _run(_SeqClient((FIX + "\npkg/util.py\n<<<<<<< SEARCH\ndef hel", "length")), [GREEN, GREEN])
    assert "return a + b" in (r.root / "pkg" / "calc.py").read_text()
    out = capsys.readouterr().out
    assert "restored the workspace" not in out
    assert "keeping the attempt's edits for the agent loop (project tests are green" in out


def test_not_a_job_workspace_is_never_touched(capsys):
    r = _run(_SeqClient(FIX), [RED, GREEN], job_workspace=False)
    assert "return a + b" in (r.root / "pkg" / "calc.py").read_text()
    assert "restored the workspace" not in capsys.readouterr().out


def test_switch_off_keeps_the_old_behaviour(capsys):
    r = _run(_SeqClient(FIX), [RED, GREEN], restore="0")
    assert "return a + b" in (r.root / "pkg" / "calc.py").read_text()
    assert "(restore off)" in capsys.readouterr().out


def test_is_job_workspace_requires_the_exact_workspace(monkeypatch, tmp_path):
    root = _repo()
    monkeypatch.setenv("AWOS_JOB_WORKSPACE", str(root))
    assert wr.is_job_workspace(str(root))
    assert not wr.is_job_workspace(str(root / "pkg"))      # a subdir is not the workspace
    monkeypatch.setenv("AWOS_JOB_WORKSPACE", str(tmp_path))
    assert not wr.is_job_workspace(str(root))
    monkeypatch.delenv("AWOS_JOB_WORKSPACE")
    assert not wr.is_job_workspace(str(root))


def test_snapshot_leaves_head_and_index_alone_and_restore_stays_inside():
    root = _repo()
    head, status = _git(root, "rev-parse", "HEAD"), _git(root, "status", "--porcelain")
    tree = wr.snapshot(str(root))
    assert tree
    assert _git(root, "rev-parse", "HEAD") == head and _git(root, "status", "--porcelain") == status
    res = wr.restore(str(root), tree, ["../outside.py"])
    assert res["skipped"] == ["../outside.py"] and not res["restored"] and not res["removed"]
