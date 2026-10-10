"""Tests for scaffold/agent/host/journal.py — step journal + checkpoint/resume."""

import json
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scaffold"))

from scaffold.agent.agent_loop import ModelReply
from scaffold.agent.host.journal import (
    CHECKPOINT,
    DONE,
    LLM_CALL,
    STARTED,
    TOOL_CALL,
    Journal,
    JournalingClient,
    workspace_fingerprint,
)


class _Tmp(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.job = self.tmp / "jobs" / "j1"

    def tearDown(self):
        self._td.cleanup()


class TestAppend(_Tmp):
    def test_append_persists_and_reloads(self):
        j = Journal(self.job, fsync=True)
        j.append({"key": "a", "kind": "note", "payload": {"x": 1}})
        j.append({"key": "b", "kind": TOOL_CALL, "status": STARTED})
        lines = (self.job / "journal.jsonl").read_text().splitlines()
        self.assertEqual(len(lines), 2)
        j2 = Journal(self.job)
        self.assertEqual([s["key"] for s in j2.steps()], ["a", "b"])
        self.assertEqual(j2.get("a")["seq"], 1)

    def test_idempotent_append(self):
        j = Journal(self.job)
        r1 = j.append({"key": "a", "kind": "note"})
        r2 = j.append({"key": "a", "kind": "note"})
        self.assertEqual(r1["seq"], r2["seq"])
        self.assertEqual(len(Journal(self.job).records()), 1)

    def test_derived_key_is_stable(self):
        j = Journal(self.job)
        a = j.append({"kind": "note", "payload": {"z": 1, "y": 2}})
        b = j.append({"kind": "note", "payload": {"y": 2, "z": 1}})
        self.assertEqual(a["key"], b["key"])
        self.assertEqual(len(j.records()), 1)

    def test_kind_required(self):
        with self.assertRaises(ValueError):
            Journal(self.job).append({"key": "x"})

    def test_steps_collapse_latest_per_key(self):
        j = Journal(self.job)
        j.append({"key": "t", "kind": TOOL_CALL, "status": STARTED})
        j.append({"key": "t", "kind": TOOL_CALL, "status": DONE, "result": 7})
        self.assertEqual(len(j.steps()), 1)
        self.assertEqual(j.steps()[0]["result"], 7)


class TestCorruption(_Tmp):
    def test_truncated_last_line_repaired(self):
        j = Journal(self.job)
        j.append({"key": "a", "kind": "note"})
        with open(j.path, "ab") as f:
            f.write(b'{"key":"b","kind":"no')
        j2 = Journal(self.job)
        self.assertEqual(j2.repaired_bytes, len(b'{"key":"b","kind":"no'))
        j2.append({"key": "c", "kind": "note"})
        keys = [s["key"] for s in Journal(self.job).steps()]
        self.assertEqual(keys, ["a", "c"])

    def test_complete_record_missing_newline_kept(self):
        self.job.mkdir(parents=True)
        (self.job / "journal.jsonl").write_text('{"key":"a","kind":"note"}')
        j = Journal(self.job)
        j.append({"key": "b", "kind": "note"})
        self.assertEqual([s["key"] for s in Journal(self.job).steps()], ["a", "b"])

    def test_garbage_mid_file_skipped(self):
        self.job.mkdir(parents=True)
        (self.job / "journal.jsonl").write_text(
            '{"key":"a","kind":"note"}\n\x00garbage\n[1,2]\n{"key":"b","kind":"note"}\n'
        )
        j = Journal(self.job)
        self.assertEqual(j.corrupt_lines, 2)
        self.assertEqual([s["key"] for s in j.steps()], ["a", "b"])


class TestRun(_Tmp):
    def test_executes_once_then_skips(self):
        calls = []
        j = Journal(self.job)
        r = j.run("s", "note", lambda: calls.append(1) or "x")
        self.assertEqual((r.outcome, r.result), ("executed", "x"))
        r = Journal(self.job).run("s", "note", lambda: calls.append(1))
        self.assertEqual((r.outcome, r.result), ("skipped", "x"))
        self.assertEqual(len(calls), 1)

    def test_side_effect_reverified_and_rerun_when_missing(self):
        f = self.tmp / "out.txt"
        calls = []

        def effect():
            calls.append(1)
            f.write_text("hi")
            return {"path": str(f)}

        verify = lambda rec: f.exists()
        Journal(self.job).run("w", TOOL_CALL, effect, verify=verify)
        self.assertEqual(Journal(self.job).run("w", TOOL_CALL, effect, verify=verify).outcome,
                         "verified")
        f.unlink()  # the world changed: effect gone
        r = Journal(self.job).run("w", TOOL_CALL, effect, verify=verify)
        self.assertEqual(r.outcome, "executed")
        self.assertEqual(r.record["attempt"], 2)
        self.assertEqual(len(calls), 2)

    def test_in_doubt_step_verified_not_rerun(self):
        f = self.tmp / "out.txt"
        j = Journal(self.job)
        j.append({"key": "w", "kind": TOOL_CALL, "status": STARTED})
        f.write_text("hi")  # crash happened after the effect, before "done"
        r = Journal(self.job).run("w", TOOL_CALL, lambda: 1 / 0, verify=lambda r: f.exists())
        self.assertEqual(r.outcome, "verified")
        self.assertEqual(Journal(self.job).get("w")["status"], DONE)

    def test_failure_journaled_and_raised(self):
        j = Journal(self.job)
        with self.assertRaises(ZeroDivisionError):
            j.run("bad", "note", lambda: 1 / 0)
        self.assertEqual(Journal(self.job).get("bad")["status"], "failed")
        r = Journal(self.job).run("bad", "note", lambda: "ok")
        self.assertEqual((r.outcome, r.record["attempt"]), ("executed", 2))


class TestReplayPlan(_Tmp):
    def _build(self):
        j = Journal(self.job)
        j.append({"key": "llm1", "kind": LLM_CALL, "result": {"text": "a"}})
        j.append({"key": "t1", "kind": TOOL_CALL, "payload": {"ok": True}})
        j.append({"key": "t2", "kind": TOOL_CALL, "payload": {"ok": False}})
        j.append({"key": "llm2", "kind": LLM_CALL, "status": STARTED})
        return j

    def test_without_verifier(self):
        p = self._build().replay_plan()
        self.assertEqual(p.done, ["llm1"])
        self.assertEqual(p.to_verify, ["t1", "t2"])
        self.assertEqual(p.incomplete, ["llm2"])
        self.assertEqual(p.resume_after, "t2")

    def test_with_verifier(self):
        p = self._build().replay_plan(verifier=lambda r: r["payload"]["ok"])
        self.assertEqual(p.verified, ["t1"])
        self.assertEqual(p.rerun, ["t2"])
        self.assertEqual(p.skip, ["llm1", "t1"])
        self.assertEqual(p.resume_after, "t1")

    def test_raising_verifier_means_rerun(self):
        j = Journal(self.job)
        j.append({"key": "t", "kind": TOOL_CALL})
        p = j.replay_plan(verifier=lambda r: 1 / 0)
        self.assertEqual(p.rerun, ["t"])

    def test_checkpoint_and_drift(self):
        ws = self.tmp / "ws"
        ws.mkdir()
        (ws / "a.py").write_text("x = 1\n")
        j = Journal(self.job)
        j.checkpoint("cp1", ws)
        self.assertEqual(j.last_checkpoint()["kind"], CHECKPOINT)
        self.assertFalse(j.replay_plan(workspace=ws).workspace_drift)
        (ws / "a.py").write_text("x = 2\n")
        self.assertTrue(Journal(self.job).replay_plan(workspace=ws).workspace_drift)

    def test_fingerprint_ignores_git_dir_contents(self):
        ws = self.tmp / "ws"
        (ws / ".git").mkdir(parents=True)
        (ws / "f").write_text("1")
        a = workspace_fingerprint(ws)
        (ws / ".git" / "index").write_text("churn")
        self.assertEqual(a, workspace_fingerprint(ws))
        (ws / "g").write_text("2")
        self.assertNotEqual(a, workspace_fingerprint(ws))


class _Fake:
    model = "fake"

    def __init__(self):
        self.calls = 0

    def complete(self, system, messages, registry):
        self.calls += 1
        return ModelReply(text=f"reply {self.calls}", input_tokens=3, output_tokens=2)

    def format_assistant_turn(self, reply):
        return {"role": "assistant", "content": reply.text}

    def format_tool_results(self, calls, results):
        return []


class TestJournalingClient(_Tmp):
    def test_resumed_run_served_from_journal(self):
        msgs = [{"role": "user", "content": "hi"}]
        fake = _Fake()
        c = JournalingClient(fake, Journal(self.job))
        r1 = c.complete("sys", msgs, None)
        r1b = c.complete("sys", msgs, None)  # same prompt twice = two turns
        self.assertEqual(fake.calls, 2)
        fake2 = _Fake()
        c2 = JournalingClient(fake2, Journal(self.job))
        self.assertEqual(c2.complete("sys", msgs, None).text, r1.text)
        self.assertEqual(c2.complete("sys", msgs, None).text, r1b.text)
        self.assertEqual(fake2.calls, 0)
        self.assertEqual(c2.cache_hits, 2)
        c2.complete("sys", msgs + [{"role": "user", "content": "more"}], None)
        self.assertEqual(fake2.calls, 1)


class TestLiveProof(_Tmp):
    def test_sigkill_after_step_4_then_resume(self):
        out = subprocess.run(
            [sys.executable, "-m", "scaffold.agent.host.journal", "demo", str(self.tmp)],
            cwd=ROOT, capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("run 1 exit code %d" % -signal.SIGKILL, out.stdout)
        run2 = out.stdout.split("--- run 2")[1]
        for marker in ("step 1 1-plan        skip(cached reply)",
                       "step 2 2-write-a     verified",
                       "step 3 3-checkpoint  skipped",
                       "step 4 4-write-b     verified",
                       "step 5 5-review      executed",
                       "step 6 6-write-c     executed",
                       "EXACTLY-ONCE: PASS"):
            self.assertIn(marker, run2)
        self.assertEqual((self.tmp / "effects.log").read_text().split(),
                         ["a.txt", "b.txt", "c.txt"])


if __name__ == "__main__":
    unittest.main()
