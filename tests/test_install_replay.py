"""
scripts/install_replay.sh: the one-command Mac installer for AWOS Replay's local model.

Every test runs the installer with a fake HOME and a fake PATH whose first entries are stubs
(uname, sysctl, sw_vers, brew, launchctl, curl, llama-server) that only log their arguments,
so nothing here installs a launchd agent, a Homebrew package, or downloads a model.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "install_replay.sh"
LABEL = "ai.awos.replay-model"
ALABEL = "ai.awos.replay-agent"
G7 = "qwen3.5-2b-g7-rv-Q4_K_M.gguf"
A4B = "Qwen3.5-4B-Q4_K_M.gguf"
A9B = "Qwen3.5-9B-Q4_K_M.gguf"
FAKE_MODEL = b"fake-gguf-bytes\n"

STUBS = {
    "uname": '[ "$1" = -s ] && echo "${STUB_OS:-Darwin}" || echo "${STUB_ARCH:-arm64}"',
    "sysctl": 'case "$2" in hw.optional.arm64) echo 1 ;; hw.memsize) echo "${STUB_MEM:-17179869184}" ;; *) echo 0 ;; esac',
    "sw_vers": "echo 15.1",
    # free disk in KB (default 500 GB); the header line + one row, like `df -Pk`
    "df": 'printf "Filesystem 1024-blocks Used Available Capacity Mounted\\nfake 999 1 %s 1%% /\\n" "${STUB_FREE_KB:-524288000}"',
    # brew install llama.cpp drops a fake llama-server next to brew (like /opt/homebrew/bin)
    "brew": (
        'echo "brew $*" >> "$STUB_LOG"\n'
        'if [ "$1" = install ]; then printf "#!/bin/sh\\necho --sleep-idle-seconds\\n" > "$(dirname "$0")/llama-server";'
        ' chmod +x "$(dirname "$0")/llama-server"; fi\n'
        'if [ "$1" = uninstall ]; then rm -f "$(dirname "$0")/llama-server"; fi'
    ),
    # one state file per launchd label: bootstrap <domain> <plist>, bootout/print <domain>/<label>
    "launchctl": (
        'echo "launchctl $*" >> "$STUB_LOG"\n'
        'case "$1" in bootstrap) [ -n "${STUB_SLOW_BOOTOUT:-}" ] && [ -f "$STUB_STATE/$(basename "$3" .plist)" ]'
        ' && { echo "Bootstrap failed: 5: Input/output error" >&2; exit 5; };'
        ' touch "$STUB_STATE/$(basename "$3" .plist)" ;;'
        # real launchctl unloads asynchronously: with STUB_SLOW_BOOTOUT the label lingers ~1 s after bootout returns
        ' bootout) if [ -n "${STUB_SLOW_BOOTOUT:-}" ]; then (sleep 1; rm -f "$STUB_STATE/${2##*/}") >/dev/null 2>&1 &'
        ' else rm -f "$STUB_STATE/${2##*/}"; fi ;;'
        ' print) [ -f "$STUB_STATE/${2##*/}" ] || exit 113; printf "\\tpid = 4242\\n" ;; esac'
    ),
    # :8080 answers while ai.awos.replay-model is loaded, :8081 while ai.awos.replay-agent is (it lists
    # $STUB_AGENT_ID, default qwen3.5-4b); -o <file> writes the fake model
    "curl": (
        'echo "curl $*" >> "$STUB_LOG"\n'
        'case "$*" in *":8080/"*) lab=ai.awos.replay-model ;; *":8081/"*) lab=ai.awos.replay-agent ;; *) lab=none ;; esac\n'
        '[ -n "${STUB_FOREIGN_8081:-}" ] && [ "$lab" = ai.awos.replay-agent ] && touch "$STUB_STATE/foreign" && lab=foreign\n'
        'case "$*" in *"/health"*) [ -f "$STUB_STATE/$lab" ] && echo \'{"status":"ok"}\' && exit 0; exit 7 ;;\n'
        ' *"/v1/models"*) [ -f "$STUB_STATE/$lab" ] || exit 7; printf \'{"object":"list","data":[{"id":"%s","object":"model"}]}\' "${STUB_AGENT_ID:-qwen3.5-4b}"; exit 0 ;; esac\n'
        'out=""; while [ $# -gt 0 ]; do [ "$1" = -o ] && out="$2"; shift; done\n'
        '[ -n "$out" ] && printf "fake-gguf-bytes\\n" > "$out"; exit 0'
    ),
}


@pytest.fixture
def env(tmp_path):
    home = tmp_path / "home"
    (home / "Library" / "LaunchAgents").mkdir(parents=True)        # exists on every Mac
    (home / "Library" / "LaunchAgents" / "com.other.app.plist").write_text("not ours")
    (home / "Documents").mkdir()
    bin_dir = tmp_path / "stubbin"
    bin_dir.mkdir()
    state = tmp_path / "stubstate"
    state.mkdir()
    for name, body in STUBS.items():
        p = bin_dir / name
        p.write_text("#!/bin/sh\n" + body + "\n")
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
    e = {
        "HOME": str(home),
        "PATH": f"{bin_dir}:/usr/bin:/bin:/usr/sbin:/sbin",
        "STUB_LOG": str(tmp_path / "calls.log"),
        "STUB_STATE": str(state),
        "AWOS_NONINTERACTIVE": "1",
        "AWOS_BREW_CANDIDATES": "",        # never fall back to the real /opt/homebrew/bin/brew
        "AWOS_MODEL_URL": "https://example.invalid/model.gguf",
        "AWOS_MODEL_SHA256": hashlib.sha256(FAKE_MODEL).hexdigest(),
        "AWOS_AGENT_URL": "https://example.invalid/agent.gguf",
        "AWOS_AGENT_SHA256": hashlib.sha256(FAKE_MODEL).hexdigest(),
        "LANG": "C",
    }
    return {"env": e, "home": home, "bin": bin_dir, "log": tmp_path / "calls.log", "state": state}


def run(env, *args, extra=None, check=True):
    e = dict(env["env"], **(extra or {}))
    r = subprocess.run(["/bin/bash", str(SCRIPT), *args], env=e, capture_output=True, text=True, timeout=60)
    if check:
        assert r.returncode == 0, r.stdout + r.stderr
    return r


def calls(env):
    return env["log"].read_text().splitlines() if env["log"].exists() else []


def tree(root: Path):
    return sorted(str(p.relative_to(root)) + ("/" if p.is_dir() else "") for p in root.rglob("*"))


def test_dry_run_prints_plan_and_changes_nothing(env):
    (env["bin"] / "brew").unlink()                                   # fresh Mac: no Homebrew, no llama.cpp
    before = tree(env["home"])
    r = run(env, extra={"AWOS_DRY_RUN": "1"})
    out = r.stdout
    home = env["home"]
    assert tree(home) == before                                     # nothing written under HOME
    assert not any(c.startswith(("brew", "launchctl bootstrap")) for c in calls(env))  # nothing executed
    assert "[dry-run] would ask: Install Homebrew now?" in out      # brew missing -> asks, never silent
    assert "[dry-run] /opt/homebrew/bin/brew install llama.cpp" in out   # path it would have after consent
    assert "[dry-run] download https://example.invalid/model.gguf" in out
    assert f"[dry-run] write {home}/Library/LaunchAgents/{LABEL}.plist" in out
    assert f"[dry-run] launchctl bootstrap gui/{os.getuid()} {home}/Library/LaunchAgents/{LABEL}.plist" in out
    plist = "\n".join(l.strip() for l in out.splitlines())
    for flag in ("127.0.0.1", "--cache-ram", "-cms", "<string>256</string>", "--sleep-idle-seconds",
                 "<string>Background</string>", "<key>LowPriorityIO</key><true/>", "<key>Nice</key><integer>10</integer>"):
        assert flag in plist, flag
    assert "0.0.0.0" not in out
    assert "Load unpacked" in out and "http://127.0.0.1:8080/v1" in out   # next steps
    # 16 GB stub Mac: the 4B by default, the 9B suggested but not picked
    assert "Tell-it agent model: 4b" in out and "--agent 9b is stronger" in out
    assert "[dry-run] download https://example.invalid/agent.gguf" in out
    assert f"[dry-run] write {home}/Library/LaunchAgents/{ALABEL}.plist" in out
    assert f"[dry-run] launchctl bootstrap gui/{os.getuid()} {home}/Library/LaunchAgents/{ALABEL}.plist" in out
    assert "http://127.0.0.1:8081/v1 (qwen3.5-4b)" in out
    code = [l for l in SCRIPT.read_text().splitlines() if not l.lstrip().startswith("#")]
    assert not [l for l in code if re.search(r"\bsudo\b", l.split("#")[0]) and "say" not in l]


def test_rerun_waits_for_async_bootout(env):
    # macOS: bootout returns before the old service is gone, and an immediate bootstrap fails with
    # "Bootstrap failed: 5: Input/output error" (the owner hit this re-running with --agent 4b)
    run(env)
    r = run(env, "--agent", "4b", extra={"STUB_SLOW_BOOTOUT": "1"})
    assert "Bootstrap failed" not in r.stdout + r.stderr
    assert "model server: UP" in r.stdout and "Tell-it agent server: UP" in r.stdout


def test_install_then_uninstall_is_exact_roundtrip(env):
    home = env["home"]
    before = tree(home)
    r = run(env)
    assert "model server: UP" in r.stdout
    plist = home / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    model = home / ".awos" / "models" / G7
    aplist = home / "Library" / "LaunchAgents" / f"{ALABEL}.plist"
    assert plist.is_file() and model.read_bytes() == FAKE_MODEL
    assert aplist.is_file() and (home / ".awos" / "models" / A4B).read_bytes() == FAKE_MODEL
    assert "Tell-it agent server: UP on http://127.0.0.1:8081" in r.stdout
    assert not list(model.parent.glob("*.part"))
    text = plist.read_text()
    assert f"<string>{env['bin']}/llama-server</string>" in text     # absolute path, from brew's prefix
    if shutil.which("plutil"):
        subprocess.run(["plutil", "-lint", str(plist)], check=True, capture_output=True)
        subprocess.run(["plutil", "-lint", str(aplist)], check=True, capture_output=True)
    added = set(tree(home)) - set(before)
    assert added == {".awos/", ".awos/models/", f".awos/models/{G7}", f".awos/models/{A4B}", ".awos/replay/",
                     ".awos/replay/installed", f"Library/LaunchAgents/{LABEL}.plist",
                     f"Library/LaunchAgents/{ALABEL}.plist"}
    log = calls(env)
    assert "brew install llama.cpp" in log
    assert any(c.startswith("launchctl bootstrap gui/") for c in log)

    # idempotent: a second run adds nothing, downloads nothing, installs nothing
    n = len(log)
    r2 = run(env)
    assert "already downloaded" in r2.stdout and "already installed" in r2.stdout
    assert set(tree(home)) - set(before) == added
    assert not [c for c in calls(env)[n:] if c.startswith("brew") or " -o " in c]

    st = run(env, "--status").stdout
    assert "server: UP on http://127.0.0.1:8080" in st and "loaded: yes" in st and "brew llama.cpp" in st
    assert "server: UP on http://127.0.0.1:8081" in st and "serves: qwen3.5-4b (an agent model" in st

    run(env, "--uninstall")
    assert tree(home) == before                                      # exactly what install added is gone
    assert (home / "Library" / "LaunchAgents" / "com.other.app.plist").read_text() == "not ours"
    assert "brew uninstall llama.cpp" in calls(env)
    assert not (env["state"] / LABEL).exists() and not (env["state"] / ALABEL).exists()   # both booted out


def test_uninstall_keeps_what_was_there_before(env):
    home = env["home"]
    models = home / ".awos" / "models"
    models.mkdir(parents=True)
    model = models / G7
    model.write_bytes(FAKE_MODEL)                                    # user already ran awos_model.sh
    (models / A4B).write_bytes(FAKE_MODEL)                           # ... and fetched the 4B by hand
    (home / ".awos" / "telemetry.json").write_text("{}")
    llama = env["bin"] / "llama-server"
    llama.write_text("#!/bin/sh\necho --sleep-idle-seconds\n")
    llama.chmod(0o755)
    before = tree(home)
    run(env)
    assert not [c for c in calls(env) if c.startswith("brew") or " -o " in c]   # nothing re-installed
    run(env, "--uninstall")
    assert tree(home) == before and model.read_bytes() == FAKE_MODEL and llama.exists()
    assert (models / A4B).read_bytes() == FAKE_MODEL


def test_keep_model_and_dirs_with_user_files_survive(env):
    run(env)
    (env["home"] / ".awos" / "notes.txt").write_text("mine")
    run(env, "--uninstall", "--keep-model")
    assert (env["home"] / ".awos" / "models" / G7).exists() and (env["home"] / ".awos" / "models" / A4B).exists()
    assert not (env["home"] / "Library" / "LaunchAgents" / f"{ALABEL}.plist").exists()
    assert (env["home"] / ".awos" / "notes.txt").read_text() == "mine"
    assert not (env["home"] / "Library" / "LaunchAgents" / f"{LABEL}.plist").exists()
    assert not (env["home"] / ".awos" / "replay").exists()


def test_refuses_non_mac_and_intel_and_missing_homebrew_without_consent(env):
    r = run(env, extra={"STUB_OS": "Linux"}, check=False)
    assert r.returncode != 0 and "macOS" in r.stderr
    r = run(env, extra={"STUB_ARCH": "x86_64"}, check=False)
    assert r.returncode != 0 and "Rosetta" in r.stderr              # arm64 hardware, x86_64 shell
    # no brew, no llama-server, user not asked (non-interactive) -> stops, installs nothing
    brew = env["bin"] / "brew"
    brew.unlink()
    r = run(env, check=False)
    assert r.returncode != 0 and "brew.sh" in r.stderr
    assert not (env["home"] / "Library" / "LaunchAgents" / f"{LABEL}.plist").exists()
    assert not (env["home"] / ".awos" / "models").exists()


def test_bad_download_never_looks_finished(env):
    r = run(env, extra={"AWOS_MODEL_SHA256": "0" * 64}, check=False)
    assert r.returncode != 0 and "sha256 mismatch" in r.stderr
    models = env["home"] / ".awos" / "models"
    assert not list(models.glob("*.gguf")) and not list(models.glob("*.part"))
    assert not (env["home"] / "Library" / "LaunchAgents" / f"{LABEL}.plist").exists()


def test_bad_agent_download_never_looks_finished(env):
    r = run(env, "--agent", "9b", extra={"AWOS_AGENT_SHA256": "0" * 64}, check=False)
    assert r.returncode != 0 and "sha256 mismatch" in r.stderr and A9B in r.stderr
    models = env["home"] / ".awos" / "models"
    assert not (models / A9B).exists() and not list(models.glob("*.part"))
    assert not (env["home"] / "Library" / "LaunchAgents" / f"{ALABEL}.plist").exists()
    assert not [l for l in (env["home"] / ".awos" / "replay" / "installed").read_text().splitlines() if l.startswith("agent")]


@pytest.mark.parametrize("agent", ["4b", "9b", "none"])
def test_dry_run_agent_choice(env, agent):
    before = tree(env["home"])
    out = run(env, "--agent", agent, extra={"AWOS_DRY_RUN": "1"}).stdout
    assert tree(env["home"]) == before
    assert not [c for c in calls(env) if c.startswith(("brew", "launchctl bootstrap")) or " -o " in c]
    assert f"Tell-it agent model: {agent} (you chose it)" in out
    aplist = f"[dry-run] write {env['home']}/Library/LaunchAgents/{ALABEL}.plist"
    if agent == "none":
        assert aplist not in out and "agent.gguf" not in out and "again with --agent 4b" in out
        assert f"[dry-run] write {env['home']}/Library/LaunchAgents/{LABEL}.plist" in out   # the 2B is still set up
        return
    assert aplist in out and "[dry-run] download https://example.invalid/agent.gguf" in out
    plist = "\n".join(l.strip() for l in out.split(aplist, 1)[1].split("</plist>", 1)[0].splitlines())
    args = re.findall(r"<string>(.*?)</string>", plist.split("<key>ProgramArguments</key>", 1)[1].split("</array>", 1)[0])
    assert args[1:3] == ["-m", f"{env['home']}/.awos/models/{A4B if agent == '4b' else A9B}"]
    assert " ".join(args[3:]) == (f"--alias qwen3.5-{agent} --host 127.0.0.1 --port 8081 --jinja --reasoning off "
                                  "-c 12288 -np 1 --temp 0 --cache-ram 0 -cms 256 --sleep-idle-seconds 1800")   # agent: 30 min
    for flag in (f"<string>{ALABEL}</string>", "<string>Background</string>", "<key>LowPriorityIO</key><true/>",
                 "<key>LowPriorityBackgroundIO</key><true/>", "<key>Nice</key><integer>10</integer>"):
        assert flag in plist, flag
    assert "0.0.0.0" not in out


@pytest.mark.parametrize("mem_gb,agent,suggest9", [(8, "4b", False), (16, "4b", True), (36, "4b", True),
                                                   (4, "none", False)])
def test_ram_based_default_never_picks_9b_silently(env, mem_gb, agent, suggest9):
    out = run(env, extra={"AWOS_DRY_RUN": "1", "STUB_MEM": str(mem_gb * 2**30)}).stdout
    assert f"Tell-it agent model: {agent} (" in out
    assert ("--agent 9b is stronger" in out) == suggest9
    assert "Change it any time: install_replay.sh --agent 4b | --agent 9b | --agent none" in out
    assert A9B not in out and ("agent.gguf" in out) == (agent == "4b")


def test_9b_on_small_mac_warns(env):
    r = run(env, "--agent", "9b", extra={"AWOS_DRY_RUN": "1", "STUB_MEM": str(8 * 2**30)})
    assert "needs a 16 GB+ Mac" in r.stderr and "Tell-it agent model: 9b (you chose it)" in r.stdout


def test_bad_agent_argument(env):
    for args in (["--agent", "7b"], ["--agent"], ["--bogus"]):
        r = run(env, *args, check=False)
        assert r.returncode != 0 and r.stderr.startswith("error:")
    assert not (env["home"] / ".awos").exists()


def test_disk_check_refuses_before_downloading(env):
    # 4 GB free: the 2B (1.3 GB) + 4B (2.7 GB) + 1 GB headroom do not fit -> refuse, nothing written or fetched
    before = tree(env["home"])
    r = run(env, extra={"STUB_FREE_KB": str(4 * 2**20)}, check=False)
    assert r.returncode != 0 and "not enough free disk" in r.stderr and "--agent 4b or --agent none" in r.stderr
    assert tree(env["home"]) == before
    assert not [c for c in calls(env) if c.startswith(("brew", "launchctl")) or " -o " in c]
    r = run(env, "--agent", "9b", extra={"STUB_FREE_KB": str(7 * 2**20)}, check=False)   # 9B: 1.3 + 5.7 + 1 GB
    assert r.returncode != 0 and "not enough free disk" in r.stderr
    assert tree(env["home"]) == before
    # without the agent the 2B fits in 4 GB; models already on disk need no space
    run(env, "--agent", "none", extra={"STUB_FREE_KB": str(4 * 2**20)})
    run(env, "--agent", "4b", extra={"STUB_FREE_KB": str(4 * 2**20)})                  # 2B done: 2.7 + 1 fits
    run(env, extra={"STUB_FREE_KB": "1"})                                                # all done: 0 needed


def test_agent_none_switch_and_rerun_keep_choice(env):
    home = env["home"]
    models = home / ".awos" / "models"
    aplist = home / "Library" / "LaunchAgents" / f"{ALABEL}.plist"
    before = tree(home)
    run(env, "--agent", "9b")
    assert (models / A9B).exists() and "qwen3.5-9b" in aplist.read_text() and (env["state"] / ALABEL).exists()
    out = run(env).stdout                                            # re-run without --agent keeps the 9B
    assert "Tell-it agent model: 9b (already installed; keeping it)" in out and not (models / A4B).exists()
    run(env, "--agent", "4b")                                        # switch: the 9B we downloaded goes
    assert (models / A4B).exists() and not (models / A9B).exists() and "qwen3.5-4b" in aplist.read_text()
    rec = (home / ".awos" / "replay" / "installed").read_text().splitlines()
    assert "agent 4b" in rec and "agent 9b" not in rec and not [l for l in rec if A9B in l]
    assert len(rec) == len(set(rec))
    snapshot_2b = (models / G7).read_bytes()
    n = len(calls(env))
    out = run(env, "--agent", "none").stdout                         # only the agent server + model go
    assert "removing the Tell-it agent server and model" in out
    assert not aplist.exists() and not (models / A4B).exists() and not (env["state"] / ALABEL).exists()
    assert (models / G7).read_bytes() == snapshot_2b and (env["state"] / LABEL).exists()
    assert (home / "Library" / "LaunchAgents" / f"{LABEL}.plist").exists()
    assert f"launchctl bootout gui/{os.getuid()}/{ALABEL}" in calls(env)[n:]
    assert not [l for l in (home / ".awos" / "replay" / "installed").read_text().splitlines()
                if l.startswith("agent")]
    out = run(env).stdout                                            # 16 GB, nothing recorded -> default 4b again
    assert "Tell-it agent model: 4b (" in out and (models / A4B).exists()
    run(env, "--uninstall")
    assert tree(home) == before


def test_status_flags_2b_on_agent_port(env):
    run(env)
    st = run(env, "--status", extra={"STUB_AGENT_ID": "local"}).stdout
    assert "NOT an agent model" in st and "--agent 4b" in st
    st = run(env, "--status", extra={"STUB_AGENT_ID": "qwen3.5-2b-g7-rv"}).stdout
    assert "NOT an agent model" in st
    st = run(env, "--status", extra={"STUB_AGENT_ID": "qwen3.5-9b"}).stdout
    assert "serves: qwen3.5-9b (an agent model" in st


def test_status_before_install(env):
    st = run(env, "--status").stdout
    assert st.count("launchd agent: not installed") == 2 and "server: DOWN on 127.0.0.1:8081" in st
    assert "Tell it needs an agent model: install_replay.sh --agent 4b" in st


def test_agent_port_taken_by_someone_else_refuses(env):
    r = run(env, extra={"STUB_FOREIGN_8081": "1"}, check=False)
    assert r.returncode != 0 and "already serves 127.0.0.1:8081" in r.stderr and "AWOS_AGENT_PORT" in r.stderr
    assert not (env["home"] / "Library" / "LaunchAgents" / f"{ALABEL}.plist").exists()


def _awos_model_check(env, agent_id=None, agent_up=True):
    (env["state"] / LABEL).touch()
    if agent_up:
        (env["state"] / ALABEL).touch()
    stub = env["bin"] / "curl"
    # the repair prompt's answer, for awos_model.sh's own 2B check
    stub.write_text(stub.read_text().replace(
        'out=""',
        'case "$*" in *"/chat/completions"*) echo \'{"choices":[{"message":{"content":"NOT_FOUND: x"}}]}\'; exit 0 ;; esac\nout=""',
        1))
    e = dict(env["env"], **({"STUB_AGENT_ID": agent_id} if agent_id else {}))
    return subprocess.run(["/bin/bash", str(ROOT / "scripts" / "awos_model.sh"), "--check"], env=e,
                          capture_output=True, text=True, timeout=60)


def test_awos_model_check_reports_agent_server(env):
    r = _awos_model_check(env)
    assert r.returncode == 0 and "model server: UP" in r.stdout
    assert "agent server: UP on 127.0.0.1:8081; serves qwen3.5-4b" in r.stdout
    for wrong in ("local", "qwen3.5-2b", "qwen3.5-2b-g7-rv"):
        r = _awos_model_check(env, agent_id=wrong)
        assert r.returncode == 1 and "not an agent model" in r.stdout, wrong


def test_awos_model_check_agent_down_is_a_note(env):
    r = _awos_model_check(env, agent_up=False)
    assert r.returncode == 0 and "agent server: DOWN" in r.stdout and "--agent 4b" in r.stdout


def _rl_eval_specs():
    text = (ROOT / "ci" / "rl_eval.env").read_text()
    block = text.split('MODEL_SPECS="', 1)[1].split('"', 1)[0]
    return {f[0]: f[1:] for f in (l.split() for l in block.splitlines()) if f}


@pytest.mark.parametrize("size", ["4b", "9b"])
def test_agent_pins_equal_ci_rl_eval(size):
    m = re.search(rf"^\s*{size}\) AREPO=(\S+) AFILE=(\S+) AREV=([0-9a-f]{{40}}) ASIZE=(\d+) ASHA=([0-9a-f]{{64}}) ;;$",
                  SCRIPT.read_text(), re.M)
    assert m, size
    repo, file, rev, nbytes, sha = m.groups()
    assert _rl_eval_specs()[f"qwen3.5-{size}"][:5] == [repo, file, rev, nbytes, sha]


def test_model_table_matches_awos_model_sh():
    pairs = lambda path: set(re.findall(r"FILE=(\S+?);\s*SHA=([0-9a-f]{64})", path.read_text()))
    assert pairs(SCRIPT) == pairs(ROOT / "scripts" / "awos_model.sh") != set()


@pytest.mark.skipif(not shutil.which("shellcheck"), reason="shellcheck not installed")
def test_shellcheck_clean():
    for s in ("scripts/install_replay.sh", "packaging/build_extension_zip.sh"):
        subprocess.run(["shellcheck", str(ROOT / s)], check=True)


# ---- packaging/build_extension_zip.sh ---------------------------------------------------------------

BUILD = ROOT / "packaging" / "build_extension_zip.sh"


def build(ext, out, *args):
    e = dict(os.environ, AWOS_EXT_DIR=str(ext), AWOS_DIST_DIR=str(out))
    return subprocess.run(["/bin/bash", str(BUILD), *args], env=e, capture_output=True, text=True, timeout=60)


def fake_ext(tmp_path, **manifest):
    ext = tmp_path / "ext"
    (ext / "lib").mkdir(parents=True)
    (ext / "tests").mkdir()
    for f in ("background.js", "content.js", "popup.html", "lib/a.js", "tests/test_x.js", "notes.md", ".DS_Store",
              "content.js.map", "icon128.png"):
        (ext / f).write_text("x")
    m = {"manifest_version": 3, "name": "AWOS Replay", "version": "0.4.0", "description": "Show it once.",
         "background": {"service_worker": "background.js"}, "permissions": ["storage", "tabs", "alarms"],
         "host_permissions": ["http://127.0.0.1/*"], "icons": {"128": "icon128.png"},
         "content_scripts": [{"matches": ["https://*/*"], "js": ["lib/a.js", "content.js"]}],
         "action": {"default_popup": "popup.html"}}
    m.update(manifest)
    (ext / "manifest.json").write_text(json.dumps(m))
    return ext


def test_build_zip_excludes_dev_files_and_is_reproducible(tmp_path):
    ext = fake_ext(tmp_path)
    r = build(ext, tmp_path / "d1", "--strict")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "permission             alarms" in r.stdout
    z = tmp_path / "d1" / "awos-replay-0.4.0.zip"
    assert sorted(zipfile.ZipFile(z).namelist()) == ["background.js", "content.js", "icon128.png", "lib/a.js",
                                                     "manifest.json", "popup.html"]
    assert build(ext, tmp_path / "d2").returncode == 0
    assert z.read_bytes() == (tmp_path / "d2" / "awos-replay-0.4.0.zip").read_bytes()
    md = (tmp_path / "d1" / "awos-replay-0.4.0.permissions.md").read_text()
    assert "`alarms`" in md and "MISSING" not in md


@pytest.mark.parametrize("manifest,needle", [
    ({"permissions": ["storage", "debugger"]}, "'debugger' has no justification"),
    ({"manifest_version": 2}, "MV3"),
    ({"version": "0.04"}, "not 1-4 dot-separated"),
    ({"background": {"service_worker": "missing.js"}}, "'missing.js' but it is not"),
])
def test_build_rejects_bad_manifest(tmp_path, manifest, needle):
    r = build(fake_ext(tmp_path, **manifest), tmp_path / "d", "--check")
    assert r.returncode == 1 and needle in r.stdout


def test_store_blockers_fail_only_in_strict(tmp_path):
    ext = fake_ext(tmp_path, name="AWOS Replay (MVP)", description="x" * 140, icons={})
    assert build(ext, tmp_path / "d").returncode == 0
    r = build(ext, tmp_path / "d", "--strict")
    assert r.returncode == 1 and "icons.128" in r.stdout and "132" in r.stdout and "MVP" in r.stdout


def test_real_extension_manifest_validates(tmp_path):
    r = build(ROOT / "extension", tmp_path / "unused", "--check")
    assert r.returncode == 0, r.stdout
    assert not (tmp_path / "unused").exists()                        # --check writes nothing
