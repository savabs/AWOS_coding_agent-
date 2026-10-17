"""notify: inbox always, macOS via osascript (mocked), dedupe, no secrets, no hang."""
from __future__ import annotations

import json
import subprocess

from scaffold.agent import notify as nt


class FakeRun:
    def __init__(self, exc=None, rc=0):
        self.calls, self.exc, self.rc = [], exc, rc

    def __call__(self, cmd, **kw):
        self.calls.append((cmd, kw))
        if self.exc:
            raise self.exc
        return subprocess.CompletedProcess(cmd, self.rc, "", "")


def _inbox(root):
    return [json.loads(l) for l in (root / nt.INBOX).read_text().splitlines()]


def test_inbox_line_always_written(tmp_path, monkeypatch):
    monkeypatch.setenv("AWOS_NOTIFY_MACOS", "0")
    run = FakeRun()
    rec = nt.notify("done", "abc12345", "done: fix bug | $0.01", "/r/report.md", root=tmp_path, run=run)
    assert rec and run.calls == []
    (line,) = _inbox(tmp_path)
    assert line["event"] == "done" and line["report"] == "/r/report.md" and line["job_id"] == "abc12345"


def test_macos_command_escaped_with_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(nt.sys, "platform", "darwin")
    monkeypatch.setenv("AWOS_NOTIFY_MACOS", "1")
    run = FakeRun()
    nt.notify("failed", "j1", 'quote " back\\slash\nnewline; do shell script "rm -rf ~"',
              "", root=tmp_path, run=run)
    (cmd, kw), = run.calls
    assert cmd[:2] == ["osascript", "-e"] and kw["timeout"] == nt.OSASCRIPT_TIMEOUT_SEC
    script = cmd[2]
    assert "\n" not in script
    # every quote inside the message is escaped: the literal cannot be closed early
    body = script[len("display notification "):script.index(" with title ")]
    assert body.startswith('"') and body.endswith('"')
    inner = body[1:-1]
    assert all(inner[i - 1] == "\\" for i, ch in enumerate(inner) if ch == '"')
    assert "osascript -e display notification" in (tmp_path / nt.LOG).read_text()


def test_macos_timeout_or_missing_binary_never_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(nt.sys, "platform", "darwin")
    monkeypatch.setenv("AWOS_NOTIFY_MACOS", "1")
    rec = nt.notify("done", "j2", "x", "", root=tmp_path,
                    run=FakeRun(exc=subprocess.TimeoutExpired("osascript", 5)))
    assert rec["macos"] is False
    rec = nt.notify("done", "j3", "x", "", root=tmp_path, run=FakeRun(exc=FileNotFoundError()))
    assert rec["macos"] is False and len(_inbox(tmp_path)) == 2


def test_non_darwin_skips_macos(tmp_path, monkeypatch):
    monkeypatch.setattr(nt.sys, "platform", "linux")
    monkeypatch.setenv("AWOS_NOTIFY_MACOS", "1")
    run = FakeRun()
    nt.notify("done", "j", "x", "", root=tmp_path, run=run)
    assert run.calls == []


def test_identical_events_deduped(tmp_path, monkeypatch):
    monkeypatch.setattr(nt.sys, "platform", "darwin")
    monkeypatch.setenv("AWOS_NOTIFY_MACOS", "1")
    run = FakeRun()
    assert nt.notify("blocked", "a", "creds", "", root=tmp_path, run=run, dedupe_key="k")
    assert nt.notify("blocked", "b", "creds", "", root=tmp_path, run=run, dedupe_key="k") is None
    assert nt.notify("done", "a", "x", "", root=tmp_path, run=run)
    assert nt.notify("done", "a", "x", "", root=tmp_path, run=run) is None
    assert len(_inbox(tmp_path)) == 2 and len(run.calls) == 2
    monkeypatch.setenv("AWOS_NOTIFY_DEDUPE_SEC", "0")  # window over -> delivered again
    assert nt.notify("done", "a", "x", "", root=tmp_path, run=run)


def test_secrets_scrubbed(tmp_path, monkeypatch):
    monkeypatch.setattr(nt.sys, "platform", "darwin")
    monkeypatch.setenv("AWOS_NOTIFY_MACOS", "1")
    monkeypatch.setenv("SOME_API_KEY", "plainvalue12345")
    run = FakeRun()
    nt.notify("failed", "j", "401 for sk-ant-api03-abcdefghijklmnop and plainvalue12345",
              "", root=tmp_path, run=run)
    text = (tmp_path / nt.INBOX).read_text() + run.calls[0][0][2] + (tmp_path / nt.LOG).read_text()
    assert "sk-ant-api03" not in text and "plainvalue12345" not in text and "[redacted]" in text
