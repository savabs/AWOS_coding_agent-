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
FAKE_MODEL = b"fake-gguf-bytes\n"

STUBS = {
    "uname": '[ "$1" = -s ] && echo "${STUB_OS:-Darwin}" || echo "${STUB_ARCH:-arm64}"',
    "sysctl": 'case "$2" in hw.optional.arm64) echo 1 ;; hw.memsize) echo 17179869184 ;; *) echo 0 ;; esac',
    "sw_vers": "echo 15.1",
    # brew install llama.cpp drops a fake llama-server next to brew (like /opt/homebrew/bin)
    "brew": (
        'echo "brew $*" >> "$STUB_LOG"\n'
        'if [ "$1" = install ]; then printf "#!/bin/sh\\necho --sleep-idle-seconds\\n" > "$(dirname "$0")/llama-server";'
        ' chmod +x "$(dirname "$0")/llama-server"; fi\n'
        'if [ "$1" = uninstall ]; then rm -f "$(dirname "$0")/llama-server"; fi'
    ),
    "launchctl": (
        'echo "launchctl $*" >> "$STUB_LOG"\n'
        'case "$1" in bootstrap) touch "$STUB_STATE/loaded" ;; bootout) rm -f "$STUB_STATE/loaded" ;;'
        ' print) [ -f "$STUB_STATE/loaded" ] || exit 113; printf "\\tpid = 4242\\n" ;; esac'
    ),
    # health answers only while the (fake) agent is loaded; -o <file> writes the fake model
    "curl": (
        'echo "curl $*" >> "$STUB_LOG"\n'
        'case "$*" in *"/health"*) [ -f "$STUB_STATE/loaded" ] && echo \'{"status":"ok"}\' && exit 0; exit 7 ;; esac\n'
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
    code = [l for l in SCRIPT.read_text().splitlines() if not l.lstrip().startswith("#")]
    assert not [l for l in code if re.search(r"\bsudo\b", l.split("#")[0]) and "say" not in l]


def test_install_then_uninstall_is_exact_roundtrip(env):
    home = env["home"]
    before = tree(home)
    r = run(env)
    assert "model server: UP" in r.stdout
    plist = home / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    model = home / ".awos" / "models" / "qwen3.5-2b-g7-rv-Q4_K_M.gguf"
    assert plist.is_file() and model.read_bytes() == FAKE_MODEL
    assert not list(model.parent.glob("*.part"))
    text = plist.read_text()
    assert f"<string>{env['bin']}/llama-server</string>" in text     # absolute path, from brew's prefix
    if shutil.which("plutil"):
        subprocess.run(["plutil", "-lint", str(plist)], check=True, capture_output=True)
    added = set(tree(home)) - set(before)
    assert added == {".awos/", ".awos/models/", ".awos/models/qwen3.5-2b-g7-rv-Q4_K_M.gguf", ".awos/replay/",
                     ".awos/replay/installed", f"Library/LaunchAgents/{LABEL}.plist"}
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
    assert "server: UP" in st and "loaded: yes" in st and "brew llama.cpp" in st

    run(env, "--uninstall")
    assert tree(home) == before                                      # exactly what install added is gone
    assert (home / "Library" / "LaunchAgents" / "com.other.app.plist").read_text() == "not ours"
    assert "brew uninstall llama.cpp" in calls(env)
    assert not (env["state"] / "loaded").exists()                    # launchd agent booted out


def test_uninstall_keeps_what_was_there_before(env):
    home = env["home"]
    models = home / ".awos" / "models"
    models.mkdir(parents=True)
    model = models / "qwen3.5-2b-g7-rv-Q4_K_M.gguf"
    model.write_bytes(FAKE_MODEL)                                    # user already ran awos_model.sh
    (home / ".awos" / "telemetry.json").write_text("{}")
    llama = env["bin"] / "llama-server"
    llama.write_text("#!/bin/sh\necho --sleep-idle-seconds\n")
    llama.chmod(0o755)
    before = tree(home)
    run(env)
    assert not [c for c in calls(env) if c.startswith("brew") or " -o " in c]   # nothing re-installed
    run(env, "--uninstall")
    assert tree(home) == before and model.read_bytes() == FAKE_MODEL and llama.exists()


def test_keep_model_and_dirs_with_user_files_survive(env):
    run(env)
    (env["home"] / ".awos" / "notes.txt").write_text("mine")
    run(env, "--uninstall", "--keep-model")
    assert (env["home"] / ".awos" / "models" / "qwen3.5-2b-g7-rv-Q4_K_M.gguf").exists()
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
