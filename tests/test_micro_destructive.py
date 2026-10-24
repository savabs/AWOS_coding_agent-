"""Irreversible controls the RL baseline found the destructive filter missing."""
import pytest

from scaffold.agent.desktop.micro_agent import is_destructive
from scaffold.agent.desktop.web_tools import Control


@pytest.mark.parametrize("name", ["Empty trash", "Empty bin", "Delete permanently", "Discard draft", "Clear all"])
def test_irreversible_controls_are_destructive(name):
    assert is_destructive(Control(1, "button", name))


@pytest.mark.parametrize("name", ["Move to trash", "Open Personal", "Save", "Next", "Restore"])
def test_ordinary_controls_are_not(name):
    assert not is_destructive(Control(1, "button", name))
