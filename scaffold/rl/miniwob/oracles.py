"""
oracles.py — scripted solutions for a few simple MiniWoB++ types, derived only from the
seeded utterance (MiniWoB ships no oracles). Steps use the contract's semantic form
(scaffold/rl/contract.py TaskSource.oracle) and are resolved against the live Page by the
harness. Types not listed here return [] (not admitted via oracle; eval-only).

Accessible names assumed (Chromium a11y tree, with the shim's ARIA patch on):
    click-button      <button>TEXT</button>                    -> button "TEXT"
    click-link        <span class="alink">WORD</span> (patched) -> link "WORD"
    enter-text        <input type=text id=tt> (no label)        -> textbox ""   (only textbox)
    click-checkboxes  <label><input type=checkbox>NAME</label> -> checkbox "NAME"
"""
from __future__ import annotations

import re
from typing import Callable, Dict, List, Optional

_Q = r'"(.+)"'


def _click_button(u: str) -> Optional[List[dict]]:
    m = re.fullmatch(r'Click on the ' + _Q + r' button\.', u)
    if not m:
        return None
    return [{"op": "click", "role": "button", "name": m.group(1)},
            {"op": "done", "text": f'Clicked the "{m.group(1)}" button.'}]


def _click_link(u: str) -> Optional[List[dict]]:
    m = re.fullmatch(r'Click on the link ' + _Q + r'\.', u)
    if not m:
        return None
    return [{"op": "click", "role": "link", "name": m.group(1)},
            {"op": "done", "text": f'Clicked the link "{m.group(1)}".'}]


def _enter_text(u: str) -> Optional[List[dict]]:
    m = re.fullmatch(r'Enter ' + _Q + r' into the text field and press Submit\.', u)
    if not m:
        return None
    return [{"op": "fill", "role": "textbox", "name": "", "value": m.group(1)},
            {"op": "click", "role": "button", "name": "Submit"},
            {"op": "done", "text": f'Entered "{m.group(1)}" and submitted.'}]


def _click_checkboxes(u: str) -> Optional[List[dict]]:
    m = re.fullmatch(r'Select (.*) and click Submit\.', u)
    if not m:
        return None
    body = m.group(1).strip()
    names = [] if body == "nothing" else [s.strip() for s in body.split(",") if s.strip()]
    steps = [{"op": "click", "role": "checkbox", "name": n} for n in names]
    steps.append({"op": "click", "role": "button", "name": "Submit"})
    steps.append({"op": "done", "text": "Selected " + (", ".join(names) or "nothing") + " and submitted."})
    return steps


ORACLES: Dict[str, Callable[[str], Optional[List[dict]]]] = {
    "click-button": _click_button,
    "click-link": _click_link,
    "enter-text": _enter_text,
    "click-checkboxes": _click_checkboxes,
}


def oracle_for(task_type: str, utterance: str) -> List[dict]:
    """Oracle steps for ``task_type`` given its exact utterance, or [] if none/unparsed."""
    fn = ORACLES.get(task_type)
    if fn is None or not utterance:
        return []
    return fn(" ".join(utterance.split())) or []
