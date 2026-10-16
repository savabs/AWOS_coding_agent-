"""Tests for scaffold/agent/loop_checkpoint.py — per-turn checkpoint/resume of
the loop executor's conversation (rea_synthesis_2026-10-16 item 9)."""

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scaffold.agent import loop_checkpoint as lc
from scaffold.agent.loop_checkpoint import LoopCheckpoint, checkpoint_enabled, task_key


def _turn(i, big=0):
    """One loop turn in Anthropic shape: assistant tool_use + user tool_result."""
    return [
        {"role": "assistant", "content": [
            {"type": "text", "text": f"step {i}: look around"},
            {"type": "tool_use", "id": f"toolu_{i}", "name": "bash",
             "input": {"command": f"ls -la dir{i}"}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": f"toolu_{i}",
             "content": f"<returncode>0</returncode>\n<output>\n{'x' * big}file{i}\n</output>"}]},
    ]


def _counters(steps, cost=0.0):
    return {"steps": steps, "tool_calls": steps, "cost_usd": cost, "input_tokens": 100 * steps}


class _Tmp(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.dir = Path(self._td.name)
        self.path = self.dir / "job" / "loop_checkpoint.jsonl"
        self.key = task_key("fix the bug", "claude-haiku-5-5", False)

    def tearDown(self):
        self._td.cleanup()

    def ck(self, key=None, path=None):
        return LoopCheckpoint(path or self.path, key or self.key, fsync=False)

    def lines(self):
        return self.path.read_bytes().splitlines()

    def run_turns(self, ck, n, big=0, first=None):
        msgs = [first or {"role": "user", "content": "Please solve this task"}]
        for i in range(1, n + 1):
            msgs.extend(_turn(i, big))
            ck.save(msgs, _counters(i, 0.01 * i))
        return msgs


class TestEnvAndKey(unittest.TestCase):
    def test_enabled_default_and_off_values(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AWOS_LOOP_CHECKPOINT", None)
            self.assertTrue(checkpoint_enabled())
        for v in ("0", "false", "OFF", " no ", "False"):
            with mock.patch.dict(os.environ, {"AWOS_LOOP_CHECKPOINT": v}):
                self.assertFalse(checkpoint_enabled(), v)
        for v in ("1", "true", "on", ""):
            with mock.patch.dict(os.environ, {"AWOS_LOOP_CHECKPOINT": v}):
                self.assertTrue(checkpoint_enabled(), v)

    def test_task_key_stable_and_distinct(self):
        k = task_key("goal", "m", False)
        self.assertEqual(k, task_key("goal", "m", False))
        self.assertTrue(k.startswith("lc1-"))
        others = {task_key("goal ", "m", False), task_key("goal", "m2", False),
                  task_key("goal", "m", True), task_key("goal", "m", False, extra="t2")}
        self.assertNotIn(k, others)
        self.assertEqual(len(others), 4)

    def test_task_key_stable_across_processes(self):
        code = ("import sys; sys.path.insert(0, %r); "
                "from scaffold.agent.loop_checkpoint import task_key; "
                "print(task_key('gé', 'm', True))" % str(ROOT))
        out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                             text=True, env={**os.environ, "PYTHONHASHSEED": "123"})
        self.assertEqual(out.stdout.strip(), task_key("gé", "m", True))


class TestRoundTrip(_Tmp):
    def test_missing_file_loads_none(self):
        self.assertIsNone(self.ck().load())

    def test_round_trip_latest_state(self):
        msgs = self.run_turns(self.ck(), 5)
        got = self.ck().load()
        self.assertIsNotNone(got)
        m, c = got
        self.assertEqual(m, msgs)
        self.assertEqual(c, _counters(5, 0.05))

    def test_same_instance_load_after_save(self):
        ck = self.ck()
        msgs = self.run_turns(ck, 2)
        self.assertEqual(ck.load()[0], msgs)

    def test_openai_shape_and_nested(self):
        ck = self.ck()
        msgs = [{"role": "user", "content": "go"},
                {"role": "assistant", "content": None, "tool_calls": [
                    {"id": "c1", "type": "function",
                     "function": {"name": "bash", "arguments": "{\"command\": \"ls\"}"}}]},
                {"role": "tool", "tool_call_id": "c1", "content": "a\nb",
                 "meta": {"nested": [1, 2.5, {"deep": [True, None, False]}]}}]
        ck.save(msgs, {"steps": 1, "prefix_cache": {"status": "hit"}, "files": ["a.py"]})
        m, c = self.ck().load()
        self.assertEqual(m, msgs)
        self.assertEqual(c["prefix_cache"], {"status": "hit"})

    def test_resume_then_continue_appending(self):
        msgs = self.run_turns(self.ck(), 3)
        # "Kill": a new process/instance resumes and keeps going.
        ck2 = self.ck()
        m, c = ck2.load()
        size_before = self.path.stat().st_size
        m = list(m)
        m.extend(_turn(4))
        ck2.save(m, _counters(c["steps"] + 1))
        self.assertGreater(self.path.stat().st_size, size_before)
        self.assertEqual(len(self.lines()), 4)  # appended, not rewritten
        rec = json.loads(self.lines()[-1])
        self.assertEqual(rec["base"], len(msgs))
        self.assertEqual(len(rec["msgs"]), 2)
        self.assertEqual(self.ck().load()[0], msgs + _turn(4))


class TestAppendOnly(_Tmp):
    def test_each_record_holds_only_the_suffix(self):
        self.run_turns(self.ck(), 10)
        lines = self.lines()
        self.assertEqual(len(lines), 10)
        recs = [json.loads(x) for x in lines]
        self.assertEqual(len(recs[0]["msgs"]), 3)  # instance prompt + first turn
        for r in recs[1:]:
            self.assertEqual(len(r["msgs"]), 2)
        self.assertEqual([r["base"] for r in recs], [0] + [1 + 2 * i for i in range(1, 10)])

    def test_growth_is_linear(self):
        self.run_turns(self.ck(), 60, big=2000)
        size = self.path.stat().st_size
        one_turn = len(json.dumps(_turn(1, 2000)))
        # O(n): about 60 turns' worth of bytes, far from the ~60*61/2 a rewrite costs.
        self.assertLess(size, 60 * one_turn * 1.5)
        self.assertEqual(len(self.ck().load()[0]), 121)

    def test_counters_only_change_appends_empty_suffix(self):
        ck = self.ck()
        msgs = self.run_turns(ck, 1)
        ck.save(msgs, _counters(1, 9.0))
        recs = [json.loads(x) for x in self.lines()]
        self.assertEqual(len(recs), 2)
        self.assertEqual(recs[1]["msgs"], [])
        self.assertEqual(self.ck().load()[1]["cost_usd"], 9.0)

    def test_diverged_history_rewrites_file(self):
        ck = self.ck()
        msgs = self.run_turns(ck, 4)
        edited = msgs[:3] + [{"role": "user", "content": "summary of earlier turns"}]
        ck.save(edited, _counters(5))
        self.assertEqual(len(self.lines()), 1)
        self.assertEqual(self.ck().load()[0], edited)

    def test_new_run_does_not_extend_old_runs_file(self):
        self.run_turns(self.ck(), 5)
        # A fresh run (caller did not resume) with a different first reply.
        ck = self.ck()
        msgs = [{"role": "user", "content": "Please solve this task"},
                {"role": "assistant", "content": "something else"},
                {"role": "user", "content": "nudge"}]
        ck.save(msgs, _counters(1))
        self.assertEqual(len(self.lines()), 1)
        self.assertEqual(self.ck().load()[0], msgs)


class TestCorruption(_Tmp):
    def test_truncated_last_line_ignored_then_repaired(self):
        msgs = self.run_turns(self.ck(), 3)
        data = self.path.read_bytes()
        last_start = data.rstrip(b"\n").rfind(b"\n") + 1
        self.path.write_bytes(data[:last_start + 40])  # torn mid-record
        ck = self.ck()
        m, c = ck.load()
        self.assertEqual(m, msgs[:5])  # state after turn 2
        self.assertEqual(c["steps"], 2)
        self.assertEqual(ck.skipped_lines, 1)
        # Resume and append: the torn tail is cut, the new record is clean.
        m = list(m) + _turn(3) + _turn(4)
        ck.save(m, _counters(4))
        self.assertTrue(self.path.read_bytes().endswith(b"\n"))
        ck3 = self.ck()
        self.assertEqual(ck3.load()[0], m)
        self.assertEqual(ck3.skipped_lines, 0)

    def test_torn_tail_without_any_newline(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(b'{"v":1,"t":"turn","key":"lc1-')
        self.assertIsNone(self.ck().load())
        msgs = self.run_turns(self.ck(), 1)
        self.assertEqual(self.ck().load()[0], msgs)

    def test_garbage_and_bit_flip_skipped(self):
        msgs = self.run_turns(self.ck(), 3)
        lines = self.lines()
        # Valid JSON whose payload no longer hashes to its "h": a flipped byte.
        flipped = lines[2].replace(b"file3", b"fileX")
        self.assertNotEqual(flipped, lines[2])
        self.path.write_bytes(b"\n".join(lines[:2] + [b"not json at all", flipped]) + b"\n")
        ck = self.ck()
        m, _ = ck.load()
        self.assertEqual(m, msgs[:5])
        self.assertEqual(ck.skipped_lines, 2)

    def test_gap_in_chain_stops_at_last_consistent(self):
        msgs = self.run_turns(self.ck(), 4)
        lines = self.lines()
        del lines[1]  # turn 2 lost: turns 3 and 4 extend a prefix never seen
        self.path.write_bytes(b"\n".join(lines) + b"\n")
        m, c = self.ck().load()
        self.assertEqual(m, msgs[:3])
        self.assertEqual(c["steps"], 1)

    def test_unreadable_path_returns_none(self):
        self.path.mkdir(parents=True)  # a directory where the file should be
        ck = self.ck()
        self.assertIsNone(ck.load())
        ck.save([{"role": "user", "content": "x"}], {})  # must not raise


class TestKeys(_Tmp):
    def test_wrong_key_returns_none(self):
        self.run_turns(self.ck(), 2)
        other = task_key("fix the bug", "another-model", False)
        self.assertIsNone(self.ck(key=other).load())
        self.assertIsNotNone(self.ck().load())

    def test_other_key_save_replaces_file(self):
        self.run_turns(self.ck(), 2)
        other = task_key("other goal", "m", False)
        msgs = self.run_turns(self.ck(key=other), 1)
        self.assertIsNone(self.ck().load())
        self.assertEqual(self.ck(key=other).load()[0], msgs)

    def test_interleaved_other_key_records_ignored(self):
        msgs = self.run_turns(self.ck(), 2)
        with open(self.path, "ab") as f:
            f.write(b'{"v":1,"t":"turn","key":"lc1-someone-else","base":0}\n')
        ck = self.ck()
        self.assertEqual(ck.load()[0], msgs)
        self.assertEqual(ck.skipped_lines, 0)


class TestFinishClear(_Tmp):
    def test_finish_then_load_none(self):
        ck = self.ck()
        self.run_turns(ck, 3)
        ck.finish("submitted")
        self.assertIsNone(ck.load())
        self.assertIsNone(self.ck().load())
        rec = json.loads(self.lines()[-1])
        self.assertEqual((rec["t"], rec["stop_reason"]), ("finish", "submitted"))

    def test_finish_without_file_is_noop(self):
        self.ck().finish("max_steps")
        self.assertFalse(self.path.exists())

    def test_new_run_after_finish_starts_fresh(self):
        ck = self.ck()
        self.run_turns(ck, 3)
        ck.finish("gate_red")
        msgs = self.run_turns(ck, 1)
        self.assertEqual(len(self.lines()), 1)
        self.assertEqual(self.ck().load()[0], msgs)

    def test_stale_writer_after_finish_does_not_resurrect(self):
        a = self.ck()
        msgs = self.run_turns(a, 2)
        b = self.ck()
        b.finish("submitted")
        a.save(msgs + _turn(3), _counters(3))  # appends base>0 after the finish
        self.assertIsNone(self.ck().load())

    def test_clear(self):
        ck = self.ck()
        self.run_turns(ck, 2)
        ck.clear()
        self.assertFalse(self.path.exists())
        self.assertIsNone(ck.load())
        ck.clear()  # idempotent
        msgs = self.run_turns(ck, 1)
        self.assertEqual(self.ck().load()[0], msgs)
        self.assertEqual(len(self.lines()), 1)


class TestContent(_Tmp):
    def test_large_messages(self):
        ck = self.ck()
        big = "y" * (3 * 1024 * 1024)
        msgs = [{"role": "user", "content": "go"},
                {"role": "assistant", "content": "reading"},
                {"role": "user", "content": big}]
        ck.save(msgs, _counters(1))
        msgs2 = msgs + [{"role": "assistant", "content": "ok"}]
        ck.save(msgs2, _counters(2))
        self.assertEqual(self.ck().load()[0], msgs2)
        # The second record holds only the small suffix.
        self.assertLess(len(self.lines()[1]), 1000)

    def test_non_ascii_and_surrogates(self):
        ck = self.ck()
        msgs = [{"role": "user", "content": "réparer le bogue — 修复错误 🐛"},
                {"role": "assistant", "content": [{"type": "text", "text": "Ünïcödé ✓"}]},
                {"role": "user", "content": "bad bytes: \udcff\ud800 end"}]
        ck.save(msgs, {"note": "naïve"})
        raw = self.path.read_bytes()
        raw.decode("ascii")  # every line is pure ASCII on disk
        m, c = self.ck().load()
        self.assertEqual(m, msgs)
        self.assertEqual(c, {"note": "naïve"})

    def test_unserialisable_message_does_not_raise(self):
        ck = self.ck()
        msgs = self.run_turns(ck, 1)
        ck.save(msgs + [{"role": "user", "content": object()}], _counters(2))
        self.assertEqual(self.ck().load()[0], msgs)  # last good state survives
        msgs2 = msgs + _turn(2)
        ck.save(msgs2, _counters(2))
        self.assertEqual(self.ck().load()[0], msgs2)

    def test_save_does_not_mutate_or_alias_input(self):
        ck = self.ck()
        msgs = [{"role": "user", "content": "go"}]
        snapshot = json.dumps(msgs)
        ck.save(msgs, {"steps": 0})
        self.assertEqual(json.dumps(msgs), snapshot)
        loaded = self.ck().load()[0]
        loaded[0]["content"] = "changed"
        self.assertEqual(msgs[0]["content"], "go")

    def test_oserror_on_write_is_swallowed(self):
        ck = self.ck()
        msgs = self.run_turns(ck, 1)
        with mock.patch("builtins.open", side_effect=OSError("disk full")):
            ck.save(msgs + _turn(2), _counters(2))  # must not raise
            ck.finish("submitted")
        # Next save re-syncs with the disk and writes the missing suffix.
        msgs3 = msgs + _turn(2) + _turn(3)
        ck.save(msgs3, _counters(3))
        self.assertEqual(self.ck().load()[0], msgs3)


class TestConcurrent(_Tmp):
    def test_double_save_same_state_is_noop(self):
        ck = self.ck()
        msgs = self.run_turns(ck, 2)
        n = len(self.lines())
        ck.save(msgs, _counters(2, 0.02))
        ck.save(list(msgs), dict(_counters(2, 0.02)))
        self.assertEqual(len(self.lines()), n)
        self.assertEqual(self.ck().load()[0], msgs)

    def test_two_instances_same_state(self):
        msgs = self.run_turns(self.ck(), 2)
        a, b = self.ck(), self.ck()
        nxt = msgs + _turn(3)
        a.save(nxt, _counters(3))
        b.save(nxt, _counters(3))
        m, c = self.ck().load()
        self.assertEqual(m, nxt)
        self.assertEqual(c["steps"], 3)

    def test_stale_writer_forks_at_its_base(self):
        base = self.run_turns(self.ck(), 2)
        a, b = self.ck(), self.ck()
        a.save(base, _counters(2))   # both adopt the same file state
        b.save(base, _counters(2))
        b_msgs = base + _turn(40)
        b.save(b_msgs, _counters(3))
        self.assertEqual(self.ck().load()[0], b_msgs)
        a_msgs = base + _turn(30) + _turn(31)
        a.save(a_msgs, _counters(4))  # stale writer: same base, other suffix
        m, c = self.ck().load()
        self.assertEqual(m, a_msgs)   # last writer wins, histories never mix
        self.assertEqual(c["steps"], 4)

    def test_writer_that_replaced_file_wins_over_stale_one(self):
        base = self.run_turns(self.ck(), 2)
        a, b = self.ck(), self.ck()
        a_msgs = base + _turn(30)
        a.save(a_msgs, _counters(3))
        b_msgs = base + _turn(40) + _turn(41)
        b.save(b_msgs, _counters(4))  # does not extend the file: rewrites it
        self.assertEqual(len(self.lines()), 1)
        a.save(a_msgs + _turn(31), _counters(4))  # extends a prefix no longer on disk
        ck = self.ck()
        self.assertEqual(ck.load()[0], b_msgs)
        self.assertEqual(ck.skipped_lines, 1)

    def test_parallel_processes_never_corrupt(self):
        script = textwrap.dedent(f"""
            import sys
            sys.path.insert(0, {str(ROOT)!r})
            from scaffold.agent.loop_checkpoint import LoopCheckpoint
            tag = sys.argv[1]
            ck = LoopCheckpoint({str(self.path)!r}, {self.key!r})
            msgs = [{{"role": "user", "content": "Please solve this task"}}]
            for i in range(30):
                msgs.append({{"role": "assistant", "content": tag + str(i) + "z" * 5000}})
                msgs.append({{"role": "user", "content": "ok"}})
                ck.save(msgs, {{"steps": i + 1, "tag": tag}})
        """)
        procs = [subprocess.Popen([sys.executable, "-c", script, t]) for t in ("A", "B", "C")]
        for p in procs:
            self.assertEqual(p.wait(timeout=60), 0)
        ck = self.ck()
        got = ck.load()
        self.assertIsNotNone(got)
        m, c = got
        # Every line parses (no torn or glued records); records from writers
        # whose prefix another writer replaced are skipped, never mixed in.
        for line in self.lines():
            json.loads(line)
        tag = c["tag"]
        self.assertEqual(len(m), 1 + 2 * c["steps"])
        # Whatever writer won, the state is one writer's consistent history.
        for i in range(c["steps"]):
            self.assertTrue(m[1 + 2 * i]["content"].startswith(tag + str(i) + "z"))

    def test_sigkill_mid_run_resumes(self):
        script = textwrap.dedent(f"""
            import os, signal, sys
            sys.path.insert(0, {str(ROOT)!r})
            from scaffold.agent.loop_checkpoint import LoopCheckpoint
            ck = LoopCheckpoint({str(self.path)!r}, {self.key!r})
            msgs = [{{"role": "user", "content": "task"}}]
            for i in range(1, 8):
                msgs += [{{"role": "assistant", "content": "a%d" % i}},
                         {{"role": "user", "content": "r%d" % i}}]
                ck.save(msgs, {{"steps": i, "cost_usd": 0.5 * i}})
                if i == 5:
                    os.kill(os.getpid(), signal.SIGKILL)
        """)
        rc = subprocess.run([sys.executable, "-c", script]).returncode
        self.assertNotEqual(rc, 0)
        m, c = self.ck().load()
        self.assertEqual(c, {"steps": 5, "cost_usd": 2.5})
        self.assertEqual(len(m), 11)
        self.assertEqual(m[-1], {"role": "user", "content": "r5"})


class TestFsyncPath(_Tmp):
    def test_default_fsync_path_works(self):
        ck = LoopCheckpoint(self.path, self.key)  # fsync=True
        msgs = self.run_turns(ck, 2)
        ck.save(msgs[:3] + [{"role": "user", "content": "rewrite"}], {})  # atomic rewrite
        self.assertEqual(len(self.lines()), 1)
        self.assertFalse(any(p.name.endswith(".tmp") for p in self.path.parent.iterdir()))
        ck.finish("submitted")
        self.assertIsNone(LoopCheckpoint(self.path, self.key).load())

    def test_repair_tail_scans_back_past_chunk(self):
        msgs = self.run_turns(self.ck(), 1)
        with open(self.path, "ab") as f:
            f.write(b"{" + b"q" * (lc._CHUNK * 2 + 17))  # long torn line
        ck = self.ck()
        self.assertEqual(ck.load()[0], msgs)
        msgs2 = msgs + _turn(2)
        ck.save(msgs2, _counters(2))
        self.assertEqual(len(self.lines()), 2)
        self.assertEqual(self.ck().load()[0], msgs2)


if __name__ == "__main__":
    unittest.main()
