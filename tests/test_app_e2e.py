"""
End-to-end: drive the real `awos` app through a pseudo-terminal.

A scratch git repo with a bug and failing tests; the background worker is
auto-started by the app and runs the model-free demo runner
(scaffold.agent.app.demo_runner), so the whole path is real except the model:
queue, detached worker, private worktree, host test run, hand-off branch,
progress view, diff, merge.

Live mode (real model, costs money):

    AWOS_APP_LIVE=1 python tests/test_app_e2e.py [transcript.txt]

runs the same flow against the default runner and prints a transcript.
"""
from __future__ import annotations

import fcntl
import os
import re
import select
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.timeout(180)   # real subprocesses, real worker

ROOT = Path(__file__).resolve().parents[1]
AWOS = ROOT / "awos.py"
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|[\x01\x02]")

BUGGY = "def average(xs):\n    return sum(xs) / (len(xs) + 1)\n"
TESTS = ("from calc import average\n\n\ndef test_avg():\n    assert average([2, 4, 6]) == 4\n\n\n"
         "def test_one():\n    assert average([5]) == 5\n")


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def make_repo(base: Path) -> Path:
    repo = base / "shop"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "dev@example.com")
    _git(repo, "config", "user.name", "Dev")
    (repo / "calc.py").write_text(BUGGY)
    (repo / "test_calc.py").write_text(TESTS)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "calc with a bug")
    return repo


_TOKEN = re.compile(r"\x1b\[2K|\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|\r\n|[\r\n\x08\x01\x02]|[^\x1b\r\n\x08\x01\x02]+")


def render(raw: str) -> str:
    """What a terminal shows: honours \\r, erase-line and backspace (enough for this app)."""
    lines: list[str] = []
    cur: list[str] = []
    col = 0
    for tok in _TOKEN.findall(raw):
        if tok in ("\n", "\r\n"):
            lines.append("".join(cur).rstrip())
            cur, col = [], 0
        elif tok == "\r":
            col = 0
        elif tok == "\x1b[2K":
            cur, col = [], 0
        elif tok == "\x08":
            col = max(0, col - 1)
        elif tok.startswith("\x1b") or tok in ("\x01", "\x02"):
            continue
        else:
            for ch in tok:
                if col < len(cur):
                    cur[col] = ch
                else:
                    cur.extend(" " * (col - len(cur)))
                    cur.append(ch)
                col += 1
    lines.append("".join(cur).rstrip())
    return "\n".join(lines)


class Pty:
    """A tiny pexpect: run a command on a pty, send keys, wait for text."""

    def __init__(self, argv: list[str], cwd: Path, env: dict, cols: int = 120, rows: int = 200):
        self.master, slave = os.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        def ctty():   # own session with the pty as controlling terminal: Ctrl-C -> SIGINT
            os.setsid()
            fcntl.ioctl(0, termios.TIOCSCTTY, 0)

        self.proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=slave, stdout=slave, stderr=slave,
                                     preexec_fn=ctty, close_fds=True)
        os.close(slave)
        self.raw = ""
        self._seen = 0

    def _read(self, timeout: float) -> bool:
        ready, _, _ = select.select([self.master], [], [], timeout)
        if not ready:
            return False
        try:
            data = os.read(self.master, 65536)
        except OSError:
            return False
        if not data:
            return False
        self.raw += data.decode("utf-8", errors="replace")
        return True

    @property
    def text(self) -> str:
        return render(self.raw)

    def expect(self, pattern: str, timeout: float = 30.0) -> re.Match:
        rx = re.compile(pattern)
        end = time.monotonic() + timeout
        while True:
            text = self.text
            m = rx.search(text, self._seen)
            if m:
                # The last, unfinished line can still be redrawn (prompt/status):
                # never skip past its start.
                self._seen = min(m.end(), text.rfind("\n") + 1)
                return m
            left = end - time.monotonic()
            if left <= 0:
                raise AssertionError(f"timed out waiting for {pattern!r}; output so far:\n{self.text[-3000:]}")
            if not self._read(min(left, 0.2)) and self.proc.poll() is not None:
                while self._read(0.05):
                    pass
                m = rx.search(self.text, self._seen)
                if m:
                    self._seen = m.end()
                    return m
                raise AssertionError(f"process exited ({self.proc.returncode}) before {pattern!r}:\n"
                                     f"{self.text[-3000:]}")

    def send(self, text: str) -> None:
        os.write(self.master, text.encode())

    def line(self, text: str) -> None:
        self.send(text + "\r")

    def wait(self, timeout: float = 15.0) -> int:
        end = time.monotonic() + timeout
        while self.proc.poll() is None and time.monotonic() < end:
            self._read(0.1)
        while self._read(0.05):
            pass
        if self.proc.poll() is None:
            self.proc.kill()
        return self.proc.wait()

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()
        os.close(self.master)


def stop_worker(host: Path) -> None:
    try:
        pid = int((host / "worker.lock").read_text().strip())
    except (OSError, ValueError):
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def app_env(host: Path, live: bool = False) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST")}
    env.update({"AWOS_HOST_DIR": str(host), "TERM": "xterm-256color", "AWOS_NOTIFY_MACOS": "0",
                "AWOS_APP_POLL": "0.2", "AWOS_APP_WORKER_ARGS": "--poll 0.5", "PYTHONUNBUFFERED": "1"})
    env.pop("NO_COLOR", None)
    if not live:
        env.update({"AWOS_HOST_RUNNER": "scaffold.agent.app.demo_runner:run",
                    "AWOS_APP_DEMO_FIX": '{"file": "calc.py", "old": " + 1", "new": ""}',
                    "AWOS_APP_DEMO_STEP_SEC": "0.15", "AWOS_HOST_KEY_CHECK": "0",
                    "ANTHROPIC_API_KEY": "", "AWOS_PROVIDER": "local"})
    return env


@pytest.fixture
def scratch():
    base = Path(tempfile.mkdtemp(prefix="awos-app-e2e-"))
    host = base / "host"
    yield base, host
    stop_worker(host)
    shutil.rmtree(base, ignore_errors=True)


def test_session_task_progress_diff_merge(scratch):
    base, host = scratch
    repo = make_repo(base)
    app = Pty([sys.executable, str(AWOS)], repo, app_env(host))
    try:
        app.expect(r"AWOS .*shop \(main\).*tests: python -m pytest")
        app.expect(r"queue: 0 running, 0 queued .*worker (started|starting|on)")
        app.expect(r"awos ›")
        app.line("fix average() so the tests in test_calc.py pass")
        app.expect(r"\+ queued ([0-9a-f]{8})")
        app.expect(r"reading calc\.py")
        app.expect(r"editing calc\.py")
        app.expect(r"running tests… 2 passed")
        m = app.expect(r"✓ done\s+([0-9a-f]{8})")
        jid = m.group(1)
        app.expect(rf"tests 2 passed · \$0\.0000 · \d+s · branch awos/{jid}")
        app.expect(r"\[d\]iff\s+\[m\]erge\s+\[r\]eject\s+\[f\]ollow-up\s+\[o\]pen report")
        app.line("d")
        app.expect(r"\nawos › d\n")          # the "[1 working]" prefix is gone once the job is done
        app.expect(r"-    return sum\(xs\) / \(len\(xs\) \+ 1\)")
        app.expect(r"\+    return sum\(xs\) / \(len\(xs\)\)")
        assert "+ 1" in (repo / "calc.py").read_text()          # nothing merged without asking
        app.line("m")
        app.expect(rf"merged awos/{jid} into main")
        app.line("/status")
        app.expect(r"\[merged\]")
        app.line("/quit")
        assert app.wait() == 0
    finally:
        app.close()
    assert (repo / "calc.py").read_text() == "def average(xs):\n    return sum(xs) / (len(xs))\n"
    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert _git(repo, "log", "-1", "--format=%s").startswith(f"Merge awos/{jid}")
    out = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=repo,
                         capture_output=True, text=True)
    assert "2 passed" in out.stdout


def test_typing_survives_progress_lines(scratch):
    """Progress printed above the prompt must not eat what the user is typing."""
    base, host = scratch
    repo = make_repo(base)
    env = app_env(host)
    env["AWOS_APP_DEMO_STEP_SEC"] = "0.5"
    app = Pty([sys.executable, str(AWOS)], repo, env)
    try:
        app.expect(r"awos ›")
        app.line("fix average() so the tests pass")
        app.expect(r"\+ queued")
        app.send("/sta")
        app.expect(r"reading calc\.py")
        app.expect(r"editing calc\.py\n.*awos › /sta")   # prompt + typed text redrawn below
        app.send("\x7fatus\r")                       # backspace, finish the word: /status
        app.expect(r"awos › /status")
        app.expect(r"running\s+fix average")         # the /status listing
        app.line("/quit")
        assert app.wait() == 0
    finally:
        app.close()
    assert "Unknown command" not in app.text


def test_ctrl_c_leaves_jobs_running_and_reopen_shows_results(scratch):
    base, host = scratch
    repo = make_repo(base)
    env = app_env(host)
    env["AWOS_APP_DEMO_STEP_SEC"] = "0.6"
    app = Pty([sys.executable, str(AWOS)], repo, env)
    try:
        app.expect(r"awos ›")
        app.line("fix average() so the tests pass")
        m = app.expect(r"\+ queued ([0-9a-f]{8})")
        jid = m.group(1)
        app.expect(r"started")
        app.send("\x03")                                   # Ctrl-C
        app.expect(r"keep running in the background")
        assert app.wait() == 0
    finally:
        app.close()
    # the job finishes while the app is closed
    from scaffold.agent.host.queue import JobQueue
    q = JobQueue(host)
    for _ in range(100):
        if q.get(jid).state == "done":
            break
        time.sleep(0.2)
    assert q.get(jid).state == "done"
    again = Pty([sys.executable, str(AWOS)], repo, env)
    try:
        again.expect(r"worker on")
        again.expect(rf"✓ done\s+{jid}.*\[needs review\]")
        again.line("r")
        again.expect(rf"Delete branch awos/{jid}\? .*\[y/N\]")
        again.send("y")
        again.expect(rf"deleted awos/{jid}")
        again.line("/quit")
        assert again.wait() == 0
    finally:
        again.close()
    assert "awos/" + jid not in _git(repo, "branch", "--list")
    assert "+ 1" in (repo / "calc.py").read_text()


def test_one_shot_then_follow_up_and_merge(scratch):
    base, host = scratch
    repo = make_repo(base)
    env = app_env(host)
    app = Pty([sys.executable, str(AWOS), "fix average() so the tests pass"], repo, env)
    try:
        m = app.expect(r"\+ queued ([0-9a-f]{8})")
        jid = m.group(1)
        app.expect(r"running tests… 2 passed")
        app.expect(rf"✓ done\s+{jid}")
        app.expect(r"\[d\]iff\s+\[m\]erge\s+\[r\]eject\s+\[f\]ollow-up\s+\[o\]pen report\s+\[q\]uit ›")
        app.send("o")
        app.expect(r"Branch:")
        app.expect(r"\[q\]uit ›")
        app.send("m")
        app.expect(rf"merged awos/{jid} into main")
        assert app.wait() == 0
    finally:
        app.close()
    assert "len(xs))" in (repo / "calc.py").read_text()


def test_follow_up_job_builds_on_previous_branch(scratch):
    base, host = scratch
    repo = make_repo(base)
    env = app_env(host)
    app = Pty([sys.executable, str(AWOS)], repo, env)
    try:
        app.expect(r"awos ›")
        app.line("fix average() so the tests pass")
        jid = app.expect(r"✓ done\s+([0-9a-f]{8})").group(1)
        # second job: the demo fix ("+ 1" -> "") no longer applies on top of the
        # first branch, so the runner reports failure; what matters is its base.
        app.line(f"/follow {jid} also handle an empty list")
        app.expect(rf"queued follow-up ([0-9a-f]{{8}}) \(starts from awos/{jid}\)")
        app.expect(r"(✗ failed|✓ done)")
        app.line("/quit")
        app.wait()
    finally:
        app.close()
    from scaffold.agent.host.queue import JobQueue
    q = JobQueue(host)
    follow = [j for j in q.list() if j.goal.startswith("also handle")][0]
    base_commit = (q.job_dir(follow.id) / "base_commit").read_text().strip()
    assert base_commit == _git(repo, "rev-parse", f"awos/{jid}")


def test_plain_mode_when_not_a_tty(scratch):
    base, host = scratch
    repo = make_repo(base)
    env = app_env(host)
    env["AWOS_APP_NO_WORKER"] = "1"
    out = subprocess.run([sys.executable, str(AWOS)], cwd=repo, env=env, input="/help\n/bogus\n/quit\n",
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0
    assert "\x1b[" not in out.stdout
    assert "awos › /help" in out.stdout and "Commands:" in out.stdout
    assert "Unknown command /bogus" in out.stdout
    assert "Traceback" not in out.stdout + out.stderr


def test_help_and_subcommands_still_work(scratch):
    base, host = scratch
    env = app_env(host)
    out = subprocess.run([sys.executable, str(AWOS), "--help"], env=env, capture_output=True, text=True)
    assert out.returncode == 0 and "submit" in out.stdout and 'awos "fix the failing test"' in out.stdout
    out = subprocess.run([sys.executable, str(AWOS), "status"], env=env, capture_output=True, text=True)
    assert out.returncode == 0 and "No jobs" in out.stdout
    out = subprocess.run([sys.executable, str(AWOS), "stauts"], env=env, capture_output=True, text=True)
    assert out.returncode != 0 and "quote it" in out.stderr   # a typo never becomes a job


# ── live mode ────────────────────────────────────────────────────────────────

def _live(transcript: Path | None) -> int:
    base = Path(tempfile.mkdtemp(prefix="awos-app-live-"))
    host = base / "host"
    repo = make_repo(base)
    env = app_env(host, live=True)
    env.setdefault("AWOS_APP_BUDGET", "0.15")
    log = []
    try:
        app = Pty([sys.executable, str(AWOS)], repo, env)
        app.expect(r"awos ›", 60)
        app.line("fix average() in calc.py so the tests in test_calc.py pass")
        jid = app.expect(r"✓ done\s+([0-9a-f]{8})", 600).group(1)
        app.expect(r"\[o\]pen report")
        app.line("d")
        app.expect(r"\+    return", 30)
        app.line("m")
        app.expect(rf"merged awos/{jid} into main", 30)
        app.line("/quit")
        app.wait()
        log.append(app.text)
        app.close()
        one = Pty([sys.executable, str(AWOS), "add a median(xs) function to calc.py with a test for it"],
                  repo, env)
        jid2 = one.expect(r"(✓ done|✗ failed)\s+([0-9a-f]{8})", 600).group(2)
        one.expect(r"\[q\]uit ›", 30)
        one.send("d")
        one.expect(r"\[q\]uit ›", 30)
        one.send("m")
        one.expect(r"(merged|Not merged|no review branch)", 30)
        one.wait()
        log.append(one.text)
        one.close()
        print(f"calc.py on main after both merges:\n{(repo / 'calc.py').read_text()}")
        print(_git(repo, "log", "--oneline", "--graph"))
        tests = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=repo,
                               capture_output=True, text=True)
        print("tests on main:", tests.stdout.strip().splitlines()[-1])
        print("job ids:", jid, jid2)
    finally:
        stop_worker(host)
        text = "\n\n$ awos \"add a median(xs) function ...\"\n".join(log)
        if transcript:
            transcript.write_text(text)
        print(text)
        print("host dir:", host)
    return 0


if __name__ == "__main__":
    if os.getenv("AWOS_APP_LIVE") != "1":
        sys.exit("set AWOS_APP_LIVE=1 to run the live (paid) flow")
    sys.exit(_live(Path(sys.argv[1]) if len(sys.argv) > 1 else None))
