"""
catalog.py — which MiniWoB++ task types AWOS uses, why the others are excluded, and the
deterministic train / holdout split.

Pinned upstream: Farama-Foundation/miniwob-plusplus @ PINNED_SHA (tag v1.1, MIT). The
HTML is fetched by scripts/rl/fetch_miniwob.sh into ~/.cache/awos-rl/miniwob/<sha>/html;
nothing from upstream is vendored into this repo.

Inclusion rule: the task is solvable with AWOS's MicroAgent verbs only — click / fill text
/ select option / checkbox / radio / scroll / done (/ needs_user) — acting through the
accessibility tree. Anything that needs coordinates, drag, hover, canvas/SVG geometry,
colour or shape perception, text selection, modifier keys or real-time reaction is out.

Split: 20% of the included task TYPES are held out (split="holdout"), never trained on.
Near-duplicate variants (…-nodelay, …-2, …-easy, email-inbox-*) are grouped into a
FAMILY and a family is held out whole, so a held-out type never has a trained twin.
Families are ordered by sha256(HOLDOUT_SALT + family) and added greedily while the
held-out type count stays <= round(0.2 * n_types). The result is frozen in
data/holdout.json; tests assert the code still reproduces it.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple

PINNED_SHA = "a7658beb68e1d3572329e06734c5822bd284ddc8"   # tag v1.1, 2026-08-13
PINNED_TAG = "v1.1"
HOLDOUT_SALT = "awos-miniwob-holdout-v1:"
HOLDOUT_FRACTION = 0.2

# Seeds: 10 fixed eval seeds per type (pass@1 / pass^k reporting). Training rollouts
# must use seeds >= TRAIN_SEED_BASE so eval instances are never trained on.
EVAL_SEEDS: Tuple[int, ...] = tuple(range(10))
TRAIN_SEED_BASE = 1000

DATA_DIR = Path(__file__).with_name("data")


@dataclass(frozen=True)
class TaskType:
    name: str
    family: str
    max_steps: int = 10
    tags: Tuple[str, ...] = ()
    secret: bool = False          # instruction carries a password -> secrets_in=["password"]


def _t(name: str, family: str = "", max_steps: int = 10, tags: Tuple[str, ...] = (), secret: bool = False):
    return TaskType(name, family or name, max_steps, tags, secret)


# --------------------------------------------------------------- included --
# tags: aria_patched = needs the shim's ARIA patch (icon-only or div/span "buttons");
#       aria_poor    = controls exist but have no accessible names (positional only);
#       partial_credit = MiniWoB raw reward can be > 0 without the task fully solved;
#       secret = password typed via a {{secret:password}} handle (MicroAgent never types
#                a literal into a password field).
INCLUDED: Dict[str, TaskType] = {t.name: t for t in [
    _t("click-button"),
    _t("click-button-sequence"),
    _t("click-checkboxes", "click-checkboxes", 12, ("partial_credit",)),
    _t("click-checkboxes-large", "click-checkboxes", 20, ("partial_credit",)),
    _t("click-checkboxes-soft", "click-checkboxes", 12, ("partial_credit",)),
    _t("click-checkboxes-transfer", "click-checkboxes", 12, ("partial_credit",)),
    _t("click-collapsible", "click-collapsible"),
    _t("click-collapsible-nodelay", "click-collapsible"),
    _t("click-collapsible-2", "click-collapsible", 12),
    _t("click-collapsible-2-nodelay", "click-collapsible", 12),
    _t("click-dialog", "click-dialog"),
    _t("click-dialog-2", "click-dialog"),
    _t("click-link", tags=("aria_patched",)),
    _t("click-option"),
    _t("click-tab", "click-tab"),
    _t("click-tab-2", "click-tab", 12),
    _t("click-tab-2-easy", "click-tab", 12),
    _t("click-tab-2-medium", "click-tab", 12),
    _t("click-tab-2-hard", "click-tab", 12),
    _t("click-test", "click-test"),
    _t("click-test-2", "click-test"),
    _t("click-test-transfer", "click-test"),
    _t("unicode-test"),
    _t("focus-text", "focus-text", tags=("aria_poor",)),
    _t("focus-text-2", "focus-text", tags=("aria_poor",)),
    _t("enter-text", "enter-text", tags=("aria_poor",)),
    _t("enter-text-2", "enter-text", tags=("aria_poor",)),
    _t("enter-text-dynamic", "enter-text", tags=("aria_poor",)),
    _t("enter-password", tags=("secret", "aria_poor"), secret=True),
    _t("login-user", "login-user", tags=("secret",), secret=True),
    _t("login-user-popup", "login-user", 12, ("secret",), secret=True),
    _t("choose-list"),
    _t("choose-date", "choose-date", 15),
    _t("choose-date-easy", "choose-date", 12),
    _t("choose-date-medium", "choose-date", 15),
    _t("choose-date-nodelay", "choose-date", 15),
    _t("use-autocomplete", "use-autocomplete", tags=("aria_patched",)),
    _t("use-autocomplete-nodelay", "use-autocomplete", tags=("aria_patched",)),
    _t("email-inbox", "email-inbox", 15, ("aria_patched",)),
    _t("email-inbox-delete", "email-inbox", 12, ("aria_patched",)),
    _t("email-inbox-forward", "email-inbox", 15, ("aria_patched",)),
    _t("email-inbox-forward-nl", "email-inbox", 15, ("aria_patched",)),
    _t("email-inbox-forward-nl-turk", "email-inbox", 15, ("aria_patched",)),
    _t("email-inbox-important", "email-inbox", 12, ("aria_patched",)),
    _t("email-inbox-nl-turk", "email-inbox", 15, ("aria_patched",)),
    _t("email-inbox-noscroll", "email-inbox", 15, ("aria_patched",)),
    _t("email-inbox-reply", "email-inbox", 15, ("aria_patched",)),
    _t("email-inbox-star-reply", "email-inbox", 15, ("aria_patched",)),
    _t("social-media", "social-media", 12, ("aria_patched",)),
    _t("social-media-all", "social-media", 20, ("aria_patched",)),
    _t("social-media-some", "social-media", 20, ("aria_patched",)),
    _t("search-engine", max_steps=15, tags=("aria_patched",)),
    _t("book-flight", "book-flight", 20, ("aria_patched",)),
    _t("book-flight-nodelay", "book-flight", 20, ("aria_patched",)),
    _t("navigate-tree", max_steps=15, tags=("aria_patched",)),
    _t("multi-layouts", "multi-form"),
    _t("multi-orderings", "multi-form"),
    _t("read-table", "read-table"),
    _t("read-table-2", "read-table"),
    _t("find-word"),
    _t("copy-paste", "copy-paste"),
    _t("copy-paste-2", "copy-paste"),
    _t("scroll-text"),
    _t("guess-number", max_steps=12, tags=("spinbutton",)),
    _t("generate-number", max_steps=12),
    _t("odd-or-even", max_steps=12, tags=("aria_patched",)),
    _t("simple-arithmetic", "simple-math"),
    _t("simple-algebra", "simple-math"),
    _t("button-delay"),
    _t("phone-book", max_steps=15, tags=("aria_patched",)),
    _t("buy-ticket", tags=("aria_patched",)),
    _t("order-food", max_steps=15, tags=("aria_patched",)),
    _t("find-greatest", max_steps=12, tags=("aria_patched",)),
    _t("form-sequence-2", "form-sequence", tags=("aria_poor",)),
    _t("form-sequence-3", "form-sequence", tags=("aria_patched",)),
]}

# --------------------------------------------------------------- excluded --
EXCLUDED: Dict[str, str] = {
    "ascending-numbers": "numbers are SVG text nodes clicked by position (no a11y controls)",
    "bisect-angle": "click a coordinate that bisects a drawn angle (canvas geometry)",
    "chase-circle": "keep the mouse over a moving circle (real-time pointer)",
    "circle-center": "click the centre of a drawn circle (geometry)",
    "click-color": "pick by colour swatch, no text (visual perception)",
    "click-menu": "nested jQuery menu opens on hover (no hover verb)",
    "click-menu-2": "icon menu opens on hover (no hover verb)",
    "click-pie": "SVG pie-menu slices chosen by angle (geometry)",
    "click-pie-nodelay": "SVG pie-menu slices chosen by angle (geometry)",
    "click-scroll-list": "multi-select listbox needs ctrl/shift-click (modifier keys)",
    "click-shades": "pick by colour shade (visual perception)",
    "click-shape": "pick an SVG shape by form/colour (visual perception)",
    "count-shape": "count drawn SVG shapes (visual perception)",
    "count-sides": "count polygon sides in SVG (visual perception)",
    "click-widget": "target is a widget TYPE (text input vs textarea vs ...) that the accessible role does not distinguish",
    "daily-calendar": "create an event by mouse drag on a time grid (drag)",
    "drag-box": "drag and drop",
    "drag-circle": "drag and drop",
    "drag-cube": "drag to rotate a 3D cube (drag)",
    "drag-items": "drag to reorder a list (drag)",
    "drag-items-grid": "drag to reorder a grid (drag)",
    "drag-shapes": "drag and drop",
    "drag-shapes-2": "drag and drop",
    "drag-single-shape": "drag and drop",
    "drag-sort-numbers": "drag to sort (drag)",
    "draw-circle": "freehand drawing on canvas (pointer path)",
    "draw-line": "freehand drawing on canvas (pointer path)",
    "enter-date": "native <input type=date> picker; value entry is browser-specific, not a text fill",
    "enter-time": "native <input type=time> picker; value entry is browser-specific, not a text fill",
    "find-midpoint": "click the midpoint of two drawn points (geometry)",
    "form-sequence": "first step needs the jQuery slider (drag/arrow keys)",
    "grid-coordinate": "click a cell by its (x, y) grid coordinate on an unlabeled grid (geometry)",
    "highlight-text": "select text with the mouse (text selection)",
    "highlight-text-2": "select text with the mouse (text selection)",
    "hot-cold": "hover to read hot/cold feedback, click a hidden point (pointer position)",
    "hover-shape": "hover a shape (no hover verb)",
    "identify-shape": "name an SVG shape (visual perception)",
    "moving-items": "click items that move (real-time pointer)",
    "number-checkboxes": "checkbox grid drawn as images of digits (visual perception)",
    "resize-textarea": "drag the textarea resize handle (drag)",
    "right-angle": "place a point forming a right angle (geometry)",
    "scroll-text-2": "reward is the scroll position of one textarea (element scroll, not page scroll)",
    "sign-agreement": "most seeds require scrolling inside a textarea to enable the form (element scroll)",
    "simon-says": "memorise a timed flashing sequence (real-time visual)",
    "stock-market": "buy when a live-updating price drops below a threshold (real-time)",
    "terminal": "keystroke-driven terminal emulator (raw key events, not a text field)",
    "text-editor": "rich-text editor: select text and apply styles (text selection)",
    "text-transform": "type distorted CAPTCHA-style text (perception; product policy is to pause on captchas)",
    "tic-tac-toe": "unlabeled 3x3 cells chosen by board position (spatial reasoning over unnamed cells)",
    "use-colorwheel": "pick a colour on a wheel (pointer position)",
    "use-colorwheel-2": "pick a colour on a wheel (pointer position)",
    "use-slider": "jQuery slider handle (drag/arrow keys)",
    "use-slider-2": "jQuery slider handles (drag/arrow keys)",
    "use-spinner": "icon-only spinner arrows and typing is blocked (keyboard handler); no named control",
    "visual-addition": "add counts of drawn blocks (visual perception)",
}

ALL_TYPES = sorted(set(INCLUDED) | set(EXCLUDED))


def families() -> Dict[str, List[str]]:
    fam: Dict[str, List[str]] = {}
    for t in INCLUDED.values():
        fam.setdefault(t.family, []).append(t.name)
    return {k: sorted(v) for k, v in sorted(fam.items())}


def compute_holdout() -> List[str]:
    """Deterministic holdout task types (see module docstring)."""
    fams = families()
    target = round(HOLDOUT_FRACTION * len(INCLUDED))
    order = sorted(fams, key=lambda f: hashlib.sha256((HOLDOUT_SALT + f).encode()).hexdigest())
    held: List[str] = []
    for f in order:
        if len(held) + len(fams[f]) <= target:
            held.extend(fams[f])
        if len(held) >= target:
            break
    return sorted(held)


def frozen_holdout() -> List[str]:
    """The holdout recorded in data/holdout.json (the source of truth at run time)."""
    p = DATA_DIR / "holdout.json"
    return sorted(json.loads(p.read_text())["holdout_types"])


def split_of(name: str) -> str:
    return "holdout" if name in set(frozen_holdout()) else "train"


def holdout_record() -> dict:
    held = compute_holdout()
    fams = families()
    return {
        "miniwob_sha": PINNED_SHA,
        "salt": HOLDOUT_SALT,
        "fraction": HOLDOUT_FRACTION,
        "n_included_types": len(INCLUDED),
        "n_holdout_types": len(held),
        "holdout_families": sorted({INCLUDED[n].family for n in held}),
        "holdout_types": held,
        "train_types": sorted(n for n in INCLUDED if n not in held),
        "n_families": len(fams),
        "eval_seeds": list(EVAL_SEEDS),
        "train_seed_base": TRAIN_SEED_BASE,
    }
