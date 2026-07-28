import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scaffold.agent.tools.shell import ShellTool


def test_read_only_commands_match_completely():
    assert ShellTool._is_safe("ls -la")
    assert ShellTool._is_safe("git status --short")
    assert ShellTool._is_safe("python3 -m pytest tests/test_shell_safety.py")


def test_safe_prefix_cannot_hide_chained_commands():
    assert not ShellTool._is_safe("ls; rm important.txt")
    assert not ShellTool._is_safe("ls && touch created.txt")
    assert not ShellTool._is_safe("cat file | grep secret")


def test_redirection_and_command_substitution_are_blocked():
    assert not ShellTool._is_safe("echo secret > output.txt")
    assert not ShellTool._is_safe("echo $(cat secret.txt)")
    assert not ShellTool._is_safe("printf x > output.txt")
