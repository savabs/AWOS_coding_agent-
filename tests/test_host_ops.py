"""M1 host ops: watchdog, morning report, launchd generator (docs/specs/host_ops.md).

Hermetic: tmp host dir, fake clock/pmset/resolver, no launchctl, no network.
"""
import json
import os
import plistlib
import socket
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from scaffold.agent.host import watchdog as wd  # noqa: E402
from scaffold.agent.host import morning_report as mr  # noqa: E402

sys.path.insert(0, str(REPO / "scripts" / "host"))
import install_launchd as il  # noqa: E402

BATT_LOW = ("Now drawing from 'Battery Power'\n"
            " -InternalBattery-0 (id=1)\t12%; discharging; 0:40 remaining present: true\n")
BATT_MID = ("Now drawing from 'Battery Power'\n"
            " -InternalBattery-0 (id=1)\t54%; discharging; 3:12 remaining present: true\n")
AC = ("Now drawing from 'AC Power'\n"
      " -InternalBattery-0 (id=1)\t100%; charged; 0:00 remaining present: true\n")


@pytest.fixture
def root(tmp_path, monkeypatch):
    r = tmp_path / "host"
    monkeypatch.setenv("AWOS_HOST_DIR", str(r))
    monkeypatch.delenv("AWOS_HOST_NOTIFY", raising=False)
    return r


# ----------------------------------------------------------------- watchdog

def test_host_root_env_and_default(root, monkeypatch):
    assert wd.host_root() == root
    monkeypatch.delenv("AWOS_HOST_DIR")
    assert wd.host_root() == Path.home() / ".awos" / "host"
    assert wd.host_root("/x/y") == Path("/x/y")


def test_heartbeat_missing_fresh_stale(root):
    assert wd.check_heartbeat(root).status == "missing"
    wd.write_heartbeat(root, note="job abc step 3", now=1000.0)
    assert wd.check_heartbeat(root, max_age_s=300, now=1100.0).ok
    f = wd.check_heartbeat(root, max_age_s=300, now=1000.0 + 3600)
    assert f.status == "stale"
    assert "job abc step 3" in f.detail
    assert "launchctl kickstart" in f.action
    assert f.data["age_s"] == 3600


def test_heartbeat_corrupt_is_stale(root):
    root.mkdir(parents=True)
    (root / "heartbeat").write_text("{not json")
    assert wd.check_heartbeat(root).status == "stale"


@pytest.mark.parametrize("err,status,cls", [
    ("Error code: 401 - {'error': {'message': 'User not found.'}}", None, "auth"),
    ("Incorrect API key provided", None, "auth"),
    ("whatever", 401, "auth"),
    ("whatever", 403, "auth"),
    ("Error code: 402 - insufficient credits", None, "billing"),
    ("Error code: 429 rate limit", None, "rate_limit"),
    ("[Errno 8] nodename nor servname provided, or not known", None, "network"),
    ("APIConnectionError: Connection error.", None, "network"),
    ("SyntaxError at line 401 of foo.py", None, "other"),
])
def test_classify_backend_error(err, status, cls):
    assert wd.classify_backend_error(err, status) == cls


def test_classify_exceptions():
    assert wd.classify_backend_error(socket.gaierror(8, "nodename")) == "network"
    assert wd.classify_backend_error(ConnectionResetError()) == "network"
    assert wd.classify_backend_error(ValueError("bad")) == "other"


def test_401_pauses_queue_and_notifies_without_leaking_key(root):
    f = wd.handle_backend_error(
        "Error code: 401 invalid key sk-or-v1-abcdef1234567890SECRET", root=root,
        job_id="j1", now=5.0)
    assert f.status == "paused" and f.data["class"] == "auth"
    p = wd.is_paused(root)
    assert p["kind"] == "auth" and p["job_id"] == "j1"
    notes = (root / "notifications.jsonl").read_text()
    assert "paused" in notes
    assert "SECRET" not in notes and "SECRET" not in (root / "PAUSED").read_text()
    assert wd.resume_queue(root) is True
    assert wd.is_paused(root) is None
    assert wd.resume_queue(root) is False


def test_network_backoff_grows_caps_and_resets(root):
    assert wd.network_ready(root, now=0)
    delays = [wd.handle_backend_error("Connection error.", root=root, now=0).data["delay_s"]
              for _ in range(8)]
    assert delays[:3] == [30, 60, 120]
    assert max(delays) == wd.BACKOFF_CAP_S
    assert not wd.network_ready(root, now=10)
    assert wd.network_ready(root, now=10_000)
    assert wd.is_paused(root) is None          # network never pauses the queue
    wd.record_network_ok(root)
    assert wd.network_ready(root, now=0)


def test_check_network_uses_injected_resolver(root):
    def down(host, port):
        raise socket.gaierror(8, "nodename nor servname provided")
    f = wd.check_network(root, resolver=down, now=0)
    assert f.status == "backoff"
    f = wd.check_network(root, resolver=lambda h, p: [("ok",)], now=0)
    assert f.ok and wd.network_ready(root, now=0)


def test_parse_pmset():
    assert wd.parse_pmset_batt(AC) == {"source": "AC", "percent": 100, "state": "charged"}
    assert wd.parse_pmset_batt(BATT_MID)["source"] == "Battery"
    assert wd.parse_pmset_batt("garbage")["source"] == "unknown"


def test_power_checks(root):
    assert wd.check_power(AC, root=root).ok
    assert wd.check_power(BATT_MID, root=root).status == "warn"
    assert wd.is_paused(root) is None
    f = wd.check_power(BATT_LOW, root=root)
    assert f.status == "paused" and wd.is_paused(root)["kind"] == "battery"


def test_power_runner_failure_is_ok(root):
    def boom():
        raise OSError("no pmset")
    assert wd.check_power(None, root=root, runner=boom).ok


def test_cli_check_exit_codes(root, capsys):
    wd.write_heartbeat(root)
    assert wd.main(["check", "--skip", "network", "--skip", "power"]) == 0
    wd.write_heartbeat(root, now=0)
    assert wd.main(["check", "--skip", "network", "--skip", "power", "--notify"]) == 1
    assert "STALE" in capsys.readouterr().out
    assert (root / "notifications.jsonl").exists()


def test_cli_classify_apply(root, capsys):
    assert wd.main(["classify", "Error code: 401", "--apply"]) == 1
    assert wd.is_paused(root)
    assert wd.main(["resume"]) == 0
    assert wd.is_paused(root) is None


# ----------------------------------------------------------------- morning report

def make_queue(root: Path, schema: str = "contract") -> None:
    """Fixture queue DB following the shared JobSpec contract."""
    root.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(root / "queue.sqlite")
    if schema == "contract":
        con.execute("""CREATE TABLE jobs (id TEXT PRIMARY KEY, goal TEXT, repo_path TEXT,
            kind TEXT, budget_usd REAL, privacy TEXT, created_at TEXT, state TEXT,
            attempts INTEGER, result TEXT)""")
        rows = [
            ("a1" * 16, "Fix the flaky retry in backupd", "/r", "coding", 2.0, "cloud_ok",
             "2026-10-10T01:00:00+00:00", "done", 1,
             json.dumps({"cost_usd": 0.42, "duration_s": 780, "branch": "awos/a1a1a1a1",
                         "tests": {"passed": 12, "failed": 0}})),
            ("b2" * 16, "Add CSV export | with pipes", "/r", "coding", 1.0, "local_only",
             "2026-10-10T02:00:00+00:00", "failed", 2,
             json.dumps({"cost_usd": 0.10, "duration_s": 120,
                         "error": "backend 401: key revoked"})),
            ("c3" * 16, "Refactor config loader", "/r", "coding", 1.0, "cloud_ok",
             "2026-10-10T03:00:00+00:00", "queued", 0, None),
            ("d4" * 16, "Old job from last week", "/r", "coding", 1.0, "cloud_ok",
             "2026-10-01T03:00:00+00:00", "done", 1, json.dumps({"cost_usd": 9.0})),
        ]
        con.executemany("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    else:  # an unexpected schema: other table name, 'status', separate columns
        con.execute("CREATE TABLE meta (k TEXT, v TEXT)")
        con.execute("""CREATE TABLE work_items (job_id TEXT, goal TEXT, status TEXT,
            started_at TEXT, finished_at TEXT, cost_usd REAL)""")
        con.execute("INSERT INTO work_items VALUES ('zz9', 'odd schema job', 'done', "
                    "'2026-10-10T01:00:00Z', '2026-10-10T01:30:00Z', 0.5)")
    con.commit()
    con.close()
    jd = root / "jobs" / ("a1" * 16)
    jd.mkdir(parents=True)
    (jd / "report.md").write_text("# Fixed retry jitter\n\nDetails…\n")
    (jd / "handoff.json").write_text(json.dumps({"branch": "awos/a1a1a1a1",
                                                 "summary": "Fixed retry jitter"}))


NOW = datetime(2026, 10, 10, 8, 0, tzinfo=timezone.utc)


def test_morning_report_from_fixture(root):
    make_queue(root)
    wd.write_heartbeat(root, now=NOW.timestamp() - 30)
    text, path = mr.build(root, since_hours=24, now=NOW)
    assert path == root / "morning_report.md" and path.read_text() == text
    assert "Fix the flaky retry" in text and "Old job from last week" not in text
    assert "**3 jobs**" in text and "$0.52" in text
    assert "`awos/a1a1a1a1`" in text and "12 passed / 0 failed" in text
    assert "| 13.0 |" in text                      # 780 s -> 13.0 min
    assert "0.42 (2.00)" in text                  # spent (budget)
    assert "Add CSV export \\| with pipes" in text  # table-safe
    assert "## Needs attention" in text and "key revoked" in text
    assert "report.md" in text and "nothing was pushed" in text


def test_morning_report_all_includes_old(root):
    make_queue(root)
    jobs, _ = mr.load_jobs(root, since_hours=None)
    assert len(jobs) == 4


def test_morning_report_odd_schema(root):
    make_queue(root, schema="odd")
    jobs, warnings = mr.load_jobs(root)
    assert not warnings and len(jobs) == 1
    j = jobs[0]
    assert (j.id, j.state, j.cost_usd, round(j.minutes)) == ("zz9", "done", 0.5, 30)


def test_morning_report_missing_db(root):
    text, _ = mr.build(root, now=NOW, with_health=False)
    assert "no queue DB" in text and "**0 jobs**" in text


def test_morning_report_shows_pause(root):
    make_queue(root)
    wd.handle_backend_error("Error code: 401", root=root)
    text, _ = mr.build(root, now=NOW)
    assert "paused" in text and "auth" in text


def test_morning_report_db_opened_read_only(root):
    make_queue(root)
    before = (root / "queue.sqlite").read_bytes()
    mr.build(root, now=NOW)
    assert (root / "queue.sqlite").read_bytes() == before


# ----------------------------------------------------------------- launchd

def test_worker_plist_shape(root, tmp_path):
    p = il.worker_plist(root, REPO, "/py", root / "host.env")
    assert p["Label"] == "ai.awos.host" and p["RunAtLoad"] is True
    assert p["KeepAlive"] == {"SuccessfulExit": False}
    assert p["StandardOutPath"].startswith(str(root / "logs"))
    assert p["EnvironmentVariables"]["AWOS_HOST_ENV_FILE"] == str(root / "host.env")
    blob = plistlib.dumps(p).decode()
    assert "API_KEY" not in blob                 # secrets never in the plist
    assert plistlib.loads(blob.encode()) == p


def test_install_dry_run_never_touches_library(root, tmp_path, capsys, monkeypatch):
    agents = tmp_path / "LaunchAgents"
    calls = []
    monkeypatch.setattr(il.subprocess, "run", lambda *a, **k: calls.append(a))
    assert il.main(["--root", str(root), "--agents-dir", str(agents)]) == 0
    out = capsys.readouterr().out
    assert not agents.exists() and calls == []
    assert (root / "launchd" / "ai.awos.host.plist").exists()
    assert (root / "launchd" / "ai.awos.host.watchdog.plist").exists()
    assert "launchctl bootstrap gui/" in out and "dry run" in out


def test_uninstall_dry_run(root, tmp_path, capsys, monkeypatch):
    calls = []
    monkeypatch.setattr(il.subprocess, "run", lambda *a, **k: calls.append(a))
    il.main(["--root", str(root), "--uninstall"])
    out = capsys.readouterr().out
    assert "launchctl bootout" in out and calls == []


def test_run_host_refuses_world_readable_env(root, tmp_path):
    env = tmp_path / "host.env"
    env.write_text("X=1\n")
    env.chmod(0o644)
    r = subprocess.run(["/bin/bash", str(REPO / "scripts/host/run_host.sh")],
                       env={**os.environ, "AWOS_HOST_ENV_FILE": str(env),
                            "AWOS_HOST_CMD": "echo ran"},
                       capture_output=True, text=True, timeout=5)
    assert r.returncode == 78 and "ran" not in r.stdout
    env.chmod(0o600)
    r = subprocess.run(["/bin/bash", str(REPO / "scripts/host/run_host.sh")],
                       env={**os.environ, "AWOS_HOST_ENV_FILE": str(env),
                            "AWOS_HOST_CMD": "echo ran X=$X"},
                       capture_output=True, text=True, timeout=5)
    assert r.returncode == 0 and "ran X=1" in r.stdout


def test_pmset_advice_prints_without_applying():
    src = (REPO / "scripts/host/pmset_advice.sh").read_text()
    # every sudo line is inside the quoted heredoc, i.e. printed not executed
    body_before_heredoc = src.split("<<'EOF'")[0]
    assert "sudo" not in body_before_heredoc
    r = subprocess.run(["/bin/bash", str(REPO / "scripts/host/pmset_advice.sh")],
                       capture_output=True, text=True, timeout=10)
    assert r.returncode == 0 and "sudo pmset -c sleep 0" in r.stdout


# ----------------------------------------------------------------- review fixes (regressions)

def test_battery_pause_does_not_erase_auth_pause(root):
    wd.handle_backend_error("Error code: 401 key revoked", root=root, job_id="j1", now=1.0)
    wd.check_power(BATT_LOW, root=root)
    p = wd.is_paused(root)
    assert p["kind"] == "auth" and p["job_id"] == "j1"          # primary kept
    assert [r["kind"] for r in p["reasons"]] == ["auth", "battery"]
    assert "battery" in wd.describe_pause(p) and "auth" in wd.describe_pause(p)


def test_auth_pause_after_battery_becomes_primary(root):
    wd.check_power(BATT_LOW, root=root)
    wd.handle_backend_error("Error code: 402 insufficient credits", root=root)
    p = wd.is_paused(root)
    assert p["kind"] == "billing"
    assert {r["kind"] for r in p["reasons"]} == {"battery", "billing"}


def test_repeated_battery_check_pauses_and_notifies_once(root):
    for _ in range(3):
        wd.check_power(BATT_LOW, root=root)
    assert len(wd.is_paused(root)["reasons"]) == 1
    assert len((root / "notifications.jsonl").read_text().splitlines()) == 1


MODERATION_403 = ("Error code: 403 - {'error': {'message': 'Your chosen model requires "
                  "moderation and your input was flagged for \"harassment\"', 'code': 403, "
                  "'metadata': {'reasons': ['harassment'], 'flagged_input': '...'}}}")


@pytest.mark.parametrize("status", [None, 403])
def test_moderation_403_is_job_level_not_auth(root, status):
    assert wd.classify_backend_error(MODERATION_403, status) == "content"
    f = wd.handle_backend_error(MODERATION_403, root=root, status=status, job_id="j9")
    assert f.status == "error" and f.data["class"] == "content"
    assert wd.is_paused(root) is None
    assert not (root / "notifications.jsonl").exists()


def test_numeric_created_at_is_filtered_by_since_hours(root):
    root.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(root / "queue.sqlite")
    con.execute("CREATE TABLE jobs (id TEXT, goal TEXT, state TEXT, created_at REAL, "
                "result TEXT)")
    old = NOW.timestamp() - 30 * 86400
    con.executemany("INSERT INTO jobs VALUES (?,?,?,?,?)", [
        ("old", "thirty days old", "done", old, json.dumps({"cost_usd": 9.0})),
        ("new", "two hours old", "done", NOW.timestamp() - 7200, None),
        ("iso", "iso one hour old", "done", None, None),
        ("ms", "epoch ms half hour old", "done", int((NOW.timestamp() - 1800) * 1000), None),
    ])
    con.execute("UPDATE jobs SET created_at='2026-10-10T07:00:00+00:00' WHERE id='iso'")
    con.commit()
    con.close()
    jobs, _ = mr.load_jobs(root, since_hours=24, now=NOW)
    assert [j.id for j in jobs] == ["new", "iso", "ms"]           # chronological, old gone
    jobs, _ = mr.load_jobs(root, since_hours=None)
    assert [j.id for j in jobs] == ["old", "new", "iso", "ms"]


def test_dns_success_does_not_clear_api_backoff(root):
    wd.handle_backend_error("Connection error.", root=root, now=0)   # worker: real API fail
    f = wd.check_network(root, resolver=lambda h, p: [("ok",)], now=1)
    assert f.status == "backoff"
    assert not wd.network_ready(root, now=1)
    assert (root / "network_backoff.json").exists()

    def down(h, p):
        raise OSError("dns down")
    wd.record_network_ok(root)                 # API success clears it
    wd.check_network(root, resolver=down, now=0)   # DNS-only backoff ...
    assert wd.check_network(root, resolver=lambda h, p: [("ok",)], now=0).ok  # ... cleared


def test_record_network_failure_is_locked_across_processes(root):
    code = ("import sys; sys.path.insert(0, %r)\n"
            "from scaffold.agent.host import watchdog as wd\n"
            "for _ in range(40): wd.record_network_failure(%r, now=0)\n") % (str(REPO), str(root))
    procs = [subprocess.Popen([sys.executable, "-c", code]) for _ in range(4)]
    assert all(p.wait(timeout=60) == 0 for p in procs)
    st = json.loads((root / "network_backoff.json").read_text())
    assert st["failures"] == 160


def test_check_notify_is_deduped(root):
    args = ["check", "--skip", "network", "--skip", "power", "--notify"]
    for _ in range(5):                       # 5 watchdog runs, heartbeat missing
        assert wd.main(args) == 1
    assert len((root / "notifications.jsonl").read_text().splitlines()) == 1
    wd.pause_queue(root, reason="manual stop", kind="manual")   # new condition -> notify
    wd.main(args)
    assert len((root / "notifications.jsonl").read_text().splitlines()) == 2
    st = json.loads((root / "notify_state.json").read_text())   # rate limit expires
    st["ts"] -= wd.NOTIFY_REPEAT_S + 1
    (root / "notify_state.json").write_text(json.dumps(st))
    wd.main(args)
    assert len((root / "notifications.jsonl").read_text().splitlines()) == 3


@pytest.mark.parametrize("secret,leak", [
    ("Authorization: Bearer abcDEF1234567890ghijKLMN", "abcDEF1234567890ghijKLMN"),
    ("jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.SflKxwRJSMeKKF2QT4fwpM",
     "SflKxwRJSMeKKF2QT4fwpM"),
    ("key AIzaSyA1234567890abcdefghijklmnopqrs", "1234567890abcdefghijklmnopqrs"),
    ("token hf_AbCdEfGhIjKlMnOpQrStUvWx", "AbCdEfGhIjKlMnOpQrStUvWx"),
    ("api_key: 'zz9Secret0123456789'", "zz9Secret0123456789"),
    ("sk-or-v1-abcdef1234567890SECRET", "SECRET"),
])
def test_redact_covers_common_token_shapes(root, secret, leak):
    assert leak not in wd._redact(secret)
    wd.handle_backend_error(f"Error code: 401 {secret}", root=root)
    assert leak not in (root / "PAUSED").read_text()
    assert leak not in (root / "notifications.jsonl").read_text()


def test_install_apply_refuses_when_worker_module_missing(root, tmp_path, monkeypatch, capsys):
    fake_repo = tmp_path / "repo"
    fake_repo.mkdir()
    agents = tmp_path / "LaunchAgents"
    calls = []
    monkeypatch.setattr(il.subprocess, "run", lambda *a, **k: calls.append(a))
    assert not il.worker_available(fake_repo)
    rc = il.main(["--root", str(root), "--repo", str(fake_repo),
                  "--agents-dir", str(agents), "--apply"])
    assert rc == 2 and calls == []
    assert "does not exist" in capsys.readouterr().out
    rc = il.main(["--root", str(root), "--repo", str(fake_repo),   # explicit override
                  "--agents-dir", str(agents), "--apply", "--allow-missing-worker"])
    assert rc == 0 and calls
    (fake_repo / "scaffold/agent/host").mkdir(parents=True)
    (fake_repo / "scaffold/agent/host/worker.py").write_text("")
    assert il.worker_available(fake_repo)


def test_pmset_advice_text_is_correct():
    r = subprocess.run(["/bin/bash", str(REPO / "scripts/host/pmset_advice.sh")],
                       capture_output=True, text=True, timeout=10)
    out = r.stdout
    assert "pmset -c restoredefaults" not in out
    assert "restoredefaults" in out and "GLOBAL" in out
    assert 'caffeinate -dimsu -w "$(pgrep' not in out
    assert 'if [ -n "$pid" ]' in out


def test_run_host_mode_check_with_gnu_stat(root, tmp_path):
    """GNU stat: `-f` is filesystem status and exits 0; only `-c` gives the mode."""
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    (fakebin / "stat").write_text(
        "#!/bin/bash\n"
        "if [ \"$1\" = -f ]; then echo '  File: \"x\" Namelen: 255 Type: ext2/ext3'; exit 0; fi\n"
        "if [ \"$1\" = -c ]; then echo 600; exit 0; fi\n"
        "exit 1\n")
    (fakebin / "stat").chmod(0o755)
    env = tmp_path / "host.env"
    env.write_text("X=1\n")
    env.chmod(0o600)
    r = subprocess.run(["/bin/bash", str(REPO / "scripts/host/run_host.sh")],
                       env={**os.environ, "PATH": f"{fakebin}:{os.environ['PATH']}",
                            "AWOS_HOST_ENV_FILE": str(env), "AWOS_HOST_DIR": str(root),
                            "AWOS_HOST_CMD": "echo ran X=$X"},
                       capture_output=True, text=True, timeout=5)
    assert r.returncode == 0 and "ran X=1" in r.stdout, r.stderr
