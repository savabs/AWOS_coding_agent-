"""The optional reason line in MicroAgent's action language: the GBNF grammar and the parser accept
replies with and without a '# reason' line (old replies keep parsing), the reason is capped at 160
characters and at most one, and the cloud reply normaliser maps 'reason:' / '#' forms onto it.

    PYTHONPATH=.:tests python3 -m pytest tests/test_micro_reason.py -q
"""
from __future__ import annotations

import pytest

import gbnf_check
from scaffold.agent.desktop import micro_agent as ma
from scaffold.rl.harness import models as M

OLD = ['click(3)', 'fill(2, "Ada")', 'select(4, "Read-only")', 'scroll(down)', 'done("saved")',
       'needs_user("login required")']


@pytest.mark.parametrize("action", OLD)
def test_old_and_new_replies_both_match_grammar_and_parse(action):
    g = ma.build_grammar(5)
    for reply in (action, "# open the lead the goal names\n" + action, "# " + "r" * 160 + "\n" + action):
        assert gbnf_check.matches(g, reply), reply
        assert ma.parse_text_action(reply).show() == ma.parse_text_action(action).show()


def test_grammar_bounds_the_reason_line():
    g = ma.build_grammar(5)
    assert not gbnf_check.matches(g, "# " + "r" * 161 + "\nclick(1)")      # too long
    assert not gbnf_check.matches(g, "# \nclick(1)")                        # empty
    assert not gbnf_check.matches(g, "# a\n# b\nclick(1)")                  # two reasons
    assert not gbnf_check.matches(g, "click(1)\n# after")                   # reason after the action
    assert not gbnf_check.matches(g, "reason: x\nclick(1)")                 # only the '# ' form
    assert "reason ::=" in g and "root ::= reason? action" in g


def test_parser_still_rejects_two_actions_or_prose():
    for bad in ["# why\nclick(1)\nclick(2)", "# only a reason", "I will click(1)"]:
        with pytest.raises(ma.ParseError):
            ma.parse_text_action(bad)


@pytest.mark.parametrize("raw, want", [
    ("click(4)", "click(4)"),
    ("# open the keys page\nclick(4)", "# open the keys page\nclick(4)"),
    ("Reason: open the keys page\nclick(4)", "# open the keys page\nclick(4)"),
    ("reason:   \nclick(4)", "click(4)"),                    # empty reason dropped
    ("#\nclick(4)", "click(4)"),
    ("```\n# go\nfill(2, \"Ada\")\n```", '# go\nfill(2, "Ada")'),
])
def test_normalize_text_reply_reason_forms(raw, want):
    got = M.normalize_text_reply(raw)
    assert got == want
    assert gbnf_check.matches(ma.build_grammar(5), got)
