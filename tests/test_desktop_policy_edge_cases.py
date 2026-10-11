"""Shell-parsing edge cases for the untrusted-provenance allowlist (track F).

Defensive QA of scaffold/agent/desktop/policy.py: decide_command() with an
UNTRUSTED initiator (or an untrusted argument) must never ALLOW any of the
commands below. The cases follow the standard categories that command
allowlist checkers commonly mishandle (OWASP OS Command Injection Defense
Cheat Sheet, CWE-78 / CWE-88 argument injection, CWE-59 link following,
CWE-22 path traversal, the "dangerous options of benign tools" lists such as
GTFOBins). Payloads are harmless placeholders only: `echo hi`, `touch
/tmp/awos_probe`, a file named `probe`. Nothing here is executed; the
policy is a static checker.

Each category also lists legitimate read-only commands that must stay ALLOW,
so a fix cannot simply refuse everything.
"""
import os

import pytest

from scaffold.agent.desktop.policy import (
    Provenance as P, SinkPolicy, Verdict, classify_command, decide_command,
    untrusted_shell_violation,
)

PROBE = "touch /tmp/awos_probe"


@pytest.fixture
def sandbox(tmp_path):
    """A sandbox with the hostile-but-plausible layout an untrusted repo or an
    earlier allowed command could leave behind."""
    d = tmp_path / "sandbox"
    (d / "src").mkdir(parents=True)
    (d / "tests").mkdir()
    (d / "src" / "a.py").write_text("x = 1\n")
    (d / "notes.txt").write_text("n\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_text("s\n")
    (d / "sandbox2").mkdir()                                # name-prefix sibling test
    (tmp_path / "sandbox2").mkdir()
    (tmp_path / "sandbox2" / "x").write_text("x\n")
    # symlinks
    (d / "1").symlink_to(outside / "secret")                # a digit-named link out
    (d / "out_link").symlink_to(outside / "secret")
    (d / "out_dir").symlink_to(outside)
    (d / "dangling").symlink_to(outside / "missing_probe")
    (d / "to_code.txt").symlink_to(d / "src" / "a.py")      # innocuous name -> code
    (d / "to_conftest.txt").symlink_to(d / "conftest.py")   # dangling -> protected
    # hard link to a code file under an innocuous name
    os.link(d / "src" / "a.py", d / "hard.txt")
    # an executable script without an extension
    (d / "run_me").write_text("#!/bin/sh\necho hi\n")
    os.chmod(d / "run_me", 0o755)
    # files whose *names* are options (CWE-88 via glob expansion)
    (d / "globdir").mkdir()
    (d / "globdir" / "--pre=sh").write_text("")
    (d / "globdir" / "-pprobe").write_text("")
    (d / "globdir" / "--hostname-bin=probe").write_text("")
    (d / "globdir" / "ok.txt").write_text("")
    return d


def untrusted_verdicts(sandbox, cmd):
    yield decide_command(SinkPolicy(sandbox), cmd, P.UNTRUSTED)
    yield decide_command(SinkPolicy(sandbox), cmd, P.AGENT, [P.UNTRUSTED])


def assert_not_allowed(sandbox, cmd):
    for d in untrusted_verdicts(sandbox, cmd):
        assert d.verdict in (Verdict.ASK, Verdict.DENY), (cmd, d.rule, classify_command(cmd))


def assert_allowed(sandbox, cmd):
    why = untrusted_shell_violation(cmd, sandbox)
    assert why is None, (cmd, why)
    d = decide_command(SinkPolicy(sandbox), cmd, P.UNTRUSTED)
    assert d.verdict is Verdict.ALLOW, (cmd, d.rule, d.reason)


CATEGORIES = {
    # 1. command separators / chaining: every segment must be on the list
    "separators": [
        f"ls; {PROBE}", f"ls && {PROBE}", f"ls || {PROBE}", f"ls | {PROBE}",
        f"ls & {PROBE}", f"ls\n{PROBE}", f"ls |& {PROBE}", f"ls ;; {PROBE}",
        f"; {PROBE}", f"ls;{PROBE}", f"ls&&{PROBE}", f"ls\r{PROBE}",
        f"echo hi |{PROBE}", f"cat notes.txt | xargs {PROBE}",
    ],
    # 2. substitution and expansion of every kind
    "substitution": [
        f"echo $({PROBE})", f"echo `{PROBE}`", f'echo "$({PROBE})"',
        f'echo "`{PROBE}`"', f"cat <({PROBE})", f"echo hi > >({PROBE})",
        "echo $((1+1))", "echo $HOME", "echo ${HOME}", "cat ${HOME}/x",
        "echo $'\\x41'", 'echo $"hi"', "echo hi${IFS}there", "cat $1",
        "echo !!", "echo ^ls^cat", "echo {a,b}", "cat {notes.txt,/etc/hosts}",
        "cat =ls", "cat ~/x", "cat ~root/x",
    ],
    # 3. quoting and escaping tricks
    "quoting": [
        "'tou''ch' /tmp/awos_probe", '"touch" /tmp/awos_probe', "t'ouc'h /tmp/awos_probe",
        "\\touch /tmp/awos_probe", "ec\\ho hi", "echo hi\\\ntouch x", "ls 'unterminated",
        'ls "unterminated', "echo 'a' # comment", "echo 'a'#x", '"ls -la"',
        "ls\" \"-la", "echo \"\\$(id)\"",
    ],
    # 4. environment assignment prefixes and state-changing builtins
    "environment": [
        "X=1 ls", "PAGER=probe git log", "GIT_EXTERNAL_DIFF=probe git diff",
        "RIPGREP_CONFIG_PATH=x rg foo", "PYTEST_ADDOPTS=-pprobe pytest",
        "PYTHONPATH=. pytest", "'X'=1 ls", "X+=1 ls", "env X=1 ls", "env ls",
        "export X=1; ls", "set -x; ls", "alias ls=echo", "unalias ls", "cd /tmp; ls",
        "pushd src", "source x.txt", ". x.txt", "exec ls", "eval ls", "trap ls EXIT",
        "command ls", "builtin echo hi", "time ls", "nohup ls", "declare -x X=1",
        "typeset X=1", "readonly X=1", "local X=1", "shopt -s dotglob; ls",
        "setopt globdots; ls", "hash -p /tmp/probe ls", "enable -n echo",
        "function f { ls; }", "f() ( ls )", "coproc ls", "[[ -f x ]] && ls",
        "if true; then ls; fi", "while true; do ls; done", "! ls",
    ],
    # 5. redirections: forms, targets, fd numbers
    "redirection": [
        "echo hi > /tmp/awos_probe", "echo hi >> /tmp/awos_probe", "echo hi >| /tmp/awos_probe",
        "echo hi &> /tmp/awos_probe", "echo hi &>> /tmp/awos_probe", "echo hi >& /tmp/awos_probe",
        "echo hi 2> /tmp/awos_probe", "echo hi 3> /tmp/awos_probe", "echo hi >/tmp/awos_probe",
        "echo hi 1<> notes.txt", "cat <> notes.txt", "cat < /etc/hosts", "cat <&3",
        "cat <<< hi", "cat <<EOF\nhi\nEOF", "cat <<-EOF\nhi\nEOF", "echo hi >",
        "echo hi > &1", "echo hi >>& x.txt", "echo hi > ~/x", "echo hi > ../x",
        "echo hi > src/../../x", "echo hi > .zshrc", "echo hi > .git/config",
        "echo hi > src/.env", "echo hi > conftest.py", "echo hi > tests/conftest.py",
        "echo hi > pytest.ini", "echo hi > pyproject.toml", "echo hi > Makefile",
        "echo hi > x.py", "echo hi > X.PY", "echo hi > a.pth", "echo hi > run.sh",
        "echo hi > lib.dylib", "echo hi > *.txt", "echo hi > x?", "echo hi > [ab]",
        "echo hi > /dev/stdout", "echo hi > /dev/tcp/127.0.0.1/9",
        # chains that write a file and then run something that may read it
        "echo hi > data.txt && pytest -q", "echo hi > data.txt; python -m pytest",
    ],
    # 6. globbing and argument injection through option-like file names
    "glob_injection": [
        # a bare `*` where an option-named file lives: see
        # test_glob_expansion_is_judged_against_the_sandbox below
        "rg foo globdir/../globdir/*", "cd globdir && rg foo *", "cd globdir; pytest *",
        "cat .*", "cat src/.*", "ls /*", "cat /e*/hosts", "cat ?tc/../x",
        "cat [n]otes.txt", "cat *(e:probe:)", "ls **/(.)",
        # creating an option-named file to inject later
        "echo hi > --pre=sh", "echo hi > -pprobe", "echo hi > src/--hostname-bin=probe",
        "echo hi > -rf",
    ],
    # 7. path normalisation and traversal
    "paths": [
        "cat ../x", "cat ./../x", "cat src/../../x", "cat //etc/hosts", "cat /./etc/hosts",
        "cat /etc/hosts", "cat ~", "ls ~+", "ls ~-", "cat src/../..", "cat ..",
        "ls -la ..", "grep foo ..", "grep -r foo /", "rg foo /tmp", "head /etc/hosts",
        "tail /var/log/system.log", "wc /etc/hosts", "git diff -- ../x", "git log /etc",
        "git show HEAD -- /etc/hosts", "pytest ../tests", "pytest /tmp/t.py::test_x",
        "cat --files0-from=/etc/hosts", "wc --files0-from=/etc/hosts",
        "wc --files0-from=-", "wc --files0-from -", "cat notes.txt --x=/etc/hosts",
        "grep --file=/etc/hosts x", "grep --exclude-from /etc/hosts x",
        "rg --ignore-file=/etc/hosts x", "rg --ignore-file /etc/hosts x",
        "grep -f /etc/hosts x", "grep -f/etc/hosts x", "grep -rf/etc/hosts .",
    ],
    # 8. symlinks and hard links (fixture provides the links)
    "links": [
        "cat out_link", "cat out_dir/secret", "ls out_dir", "ls -L out_dir", "head 1",
        "cat dangling", "echo hi > out_link", "echo hi > out_dir/probe",
        "echo hi > dangling", "echo hi > to_code.txt", "echo hi >> to_code.txt",
        "echo hi > to_conftest.txt", "echo hi > hard.txt", "echo hi > run_me",
        # a digit operand must not be mistaken for an fd and dropped
        "cat 1 > out.txt", "cat 1 2>/dev/null > out.txt", "head 1 >> out.txt",
        # recursion that follows symlinks out of the sandbox
        "grep -R secret .", "grep -rR secret .", "grep --dereference-recursive secret .",
        "rg -L secret", "rg --follow secret", "rg -nL secret .", "ls -RL .",
        f"cat {'{'}sandbox{'}'}2/x",
    ],
    # 9. interpreter invocation
    "interpreters": [
        "python -c 'print(1)'", "python3 -c 'print(1)'", "python x.py", "python3 -",
        "python -m pip list", "python -m http.server", "python -I -m pytest",
        "python -mpytest", "python3.12.1 -m pytest", "pypy -m pytest", "python2 -m pytest",
        "/usr/bin/python3 -m pytest", "./pytest", "sh -c ls", "bash -c 'echo hi'",
        "zsh -c 'echo hi'", "sh x.txt", "bash < notes.txt", "node -e 1", "perl -e 1",
        "ruby -e 1", "osascript -e 1", "awk 'BEGIN{print 1}'", "sed -n 1p notes.txt",
        "xargs echo", "find . -name x", "env python3 -m pytest", "uv run pytest",
        "py.test -p probe", "pytest -p probe", "pytest -pprobe", "pytest -qp probe",
        "pytest -vvp probe", "pytest --pyargs probe", "pytest -c x.ini",
        "pytest --rootdir=/tmp", "pytest --confcutdir=/tmp", "pytest -o addopts=-pprobe",
        "pytest --override-ini=addopts=-pprobe", "pytest -W error", "pytest --pdb",
        "pytest --junitxml=out.xml", "pytest --debug", "pytest @args.txt",
        "pytest tests @args.txt", "python -m pytest @args.txt", "pytest --basetemp=x",
        "pytest --cache-clear",
    ],
    # 10. options of allowed tools that execute programs (or open a pager)
    "exec_options": [
        "rg --pre=sh foo", "rg --pre sh foo", "rg --pre-glob='*' foo", "rg -z foo",
        "rg --search-zip foo", "rg -nz foo", "rg --hostname-bin=probe foo",
        "rg --hostname-bin probe foo", "rg --hyperlink-format=default --hostname-bin=probe foo",
        "grep --filter=probe foo", "grep --pager=probe foo", "grep --view=probe foo",
        "grep --config=x foo", "grep --save-config foo", "grep --format-open=probe foo",
        "git log --output=x", "git log --out=x", "git diff --ext-diff", "git diff --ext",
        "git diff --textconv", "git show --textconv HEAD", "git diff --no-index a b",
        "git diff -Ox", "git diff -O x", "git log --paginate", "git -p log", "git -P log",
        "git -c core.pager=probe log", "git -C src log", "git --git-dir=x log",
        "git --exec-path=x log", "git --no-pager log", "git log --exec=probe",
        "git diff --open-files-in-pager", "git status --upload-pack=probe",
        "git grep foo", "git difftool", "git log --config-env=x", "git help log",
        "git config -l", "git blame x", "git branch", "git stash list",
    ],
    # 11. control characters and odd whitespace
    "control_chars": [
        f"ls\x00; {PROBE}", f"ls\x1b[2K; {PROBE}", "ls\x07", f"ls\x0b{PROBE}",
        f"ls\x0c{PROBE}", "ls\x7f", "echo hi\x08\x08",
    ],
    # 12. persistence targets: files a later agent session or tool obeys
    "persistence": [
        "echo hi > CLAUDE.md", "echo hi >> CLAUDE.md", "echo hi > claude.md",
        "echo hi > CLAUDE.local.md", "echo hi > AGENTS.md", "echo hi > src/AGENTS.md",
        "echo hi > GEMINI.md", "echo hi > .cursorrules", "echo hi > .claude/settings.json",
        "echo hi > .github/workflows/x.yml", "echo hi > .awos/memory.json",
        "echo hi > justfile", "echo hi > Dockerfile", "echo hi > rules.mk",
        "echo hi > .envrc", "echo hi > package.json", "echo hi > setup.py",
    ],
}

CASES = [(cat, cmd) for cat, cmds in CATEGORIES.items() for cmd in cmds]


@pytest.mark.parametrize("category,cmd", CASES, ids=[f"{c}-{i}" for i, (c, _) in enumerate(CASES)])
def test_edge_case_is_ask_or_deny_for_untrusted(sandbox, category, cmd):
    assert_not_allowed(sandbox, cmd.replace("{sandbox}", str(sandbox)))


# legitimate read-only work that must keep working after any fix. Known,
# deliberate false positives (not listed): a grep/rg pattern with [ ] { } or a
# leading ~ must be passed with -e; any operand with a `..` segment asks.
ALLOWED = [
    # separators between allowed commands
    "ls; cat notes.txt", "ls && wc -l notes.txt", "ls || pwd", "cat notes.txt | wc -l",
    "ls\ncat notes.txt",
    # quoting
    "cat 'notes.txt'", 'cat "notes.txt"', "grep 'a b' notes.txt", "echo 'a;b|c&d'",
    "echo \"a;b\"", "grep -rn 'foo.*bar' src", "grep -n -e '^x = [0-9]' src/a.py",
    "rg -n 'def \\w+' src", "echo 'not $HOME'",
    # redirections to ordinary data files and /dev/null
    "ls > listing.txt", "grep -rn x src >> hits.log", "cat notes.txt 2>/dev/null",
    "pytest -q 2>&1 | tail -n 20", "ls 2> errs.txt", "cat < notes.txt",
    "git status > status.txt",
    # globs that expand to ordinary names
    "cat src/*.py", "wc -l src/*", "ls tests/*", "grep -n x src/*.py", "rg x globdir/ok*",
    "pytest tests/test_*.py", "cat notes.tx?",
    # prefix-anchored globs expand to `globdir/--pre=sh`, which is not an option
    "rg foo globdir/*", "grep -r foo globdir/*", "pytest globdir/*",
    # paths
    "cat ./notes.txt", "ls src/", "cat {sandbox}/notes.txt",
    "ls sandbox2", "head -n 5 src/a.py", "tail -n +2 notes.txt", "wc -c notes.txt",
    # digit operands that are real in-sandbox files / fd redirects
    "cat notes.txt 2>&1", "git log -5 2>/dev/null",
    # tools with harmless options
    "grep -rn --include='*.py' x src", "grep -rL x src", "rg --files src", "rg -uu x src",
    "rg --hyperlink-format=default x src", "rg -n -e foo src", "ls -laR src",
    "git log --oneline -5", "git diff --stat", "git diff --no-ext-diff",
    "git diff --no-textconv", "git show --stat HEAD", "git log --format=%h -3",
    "git status --porcelain", "git diff --cached -- src",
    "pytest -x -q --tb=short tests", "python3 -m pytest -k 'a and not b' tests",
    "pytest tests/test_a.py::test_x -vv", "pytest --lf -q", "pytest -rA -q",
    "echo hi", "pwd", "true",
]


@pytest.mark.parametrize("cmd", ALLOWED)
def test_legitimate_read_only_stays_allowed(sandbox, cmd):
    assert_allowed(sandbox, cmd.replace("{sandbox}", str(sandbox)))


def test_glob_expansion_is_judged_against_the_sandbox(sandbox):
    """`rg foo *` is fine in a clean directory but refused once a file named
    like an option exists where the glob expands."""
    clean = sandbox / "clean"
    clean.mkdir()
    (clean / "a.txt").write_text("")
    assert_allowed(clean, "rg foo *")
    (clean / "--pre=sh").write_text("")
    assert_not_allowed(clean, "rg foo *")
    assert_not_allowed(clean, "cat *")
    assert_not_allowed(clean, "grep foo *")
    assert_not_allowed(clean, "pytest ?-pre*")
    assert_not_allowed(clean, "rg foo -- *")       # conservative: refused even after --
    assert_allowed(clean, "echo *")                # echo only prints the names


def test_symlinked_write_target_is_refused_but_still_readable(sandbox):
    (sandbox / "alias.txt").symlink_to(sandbox / "notes.txt")
    # even a link to a plain data file is refused as a write target: the check
    # cannot vouch for what a link will point at when the command runs
    assert_not_allowed(sandbox, "echo hi > alias.txt")
    assert_allowed(sandbox, "cat alias.txt")
