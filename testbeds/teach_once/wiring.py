"""
wiring.py — plug the product #2 runtime pieces into the teach-once skill runner.

    SkillRunner(program, env, ground="wp6")          # WP6 AX grounding ladder
    SkillRunner(program, BrokerEnv(env), ...)         # WP7 secrets broker + redacted reports
    res["outcome"]                                    # WP7 closed outcome (set by the runner)

Grounding (WP6, scaffold/agent/desktop/grounding.py)
    ``page_to_ax_tree`` turns the testbed's stdlib accessibility page (browser.Page) into an
    AX-style tree: window(title) > webarea(app) > [form(aria-label) >] link / button /
    textfield / securetextfield, each with a frame laid out in document order, so the WP6
    gates (visible, enabled, modal, risk class) have what they need. Secure field values are
    never copied into the tree.

    ``wp6_ground`` has the runner's grounding signature
    (``ground(page, target, exclude=..., fuzzy=...) -> skill_runner.Grounding``):
      1. the recorded name, then each taught alias, through ``grounding.ground`` (role+name,
         label, text tiers; normalised names). The first tier with candidates decides.
      2. WP6 refusals map to the runner's statuses:
             ambiguous / window_ambiguous           -> "ambiguous"  (the runner HALTs)
             risk_escalation                        -> "ambiguous" with refusal="risk_escalation"
                                                       (the runner HALTs with reason risk_escalation,
                                                       even for an optional step)
             not_found / low_confidence / disabled /
             not_visible / blocked_by_modal / ...   -> "missing"    (handlers, then HALT)
         A refusal is never turned into a pick.
      3. (``wp6`` only, when ``fuzzy``) the rename rung: the runner's exclusion-aware unique
         token-overlap pick among same-role controls (never another step's target, never a
         destructive control) proposes a control, and that proposal must itself pass WP6
         grounding with the ORIGINAL step's risk class (so a rename can never escalate to a
         destructive control, and a now-ambiguous name still refuses). WP6's own ``relaxed``
         tier needs a recorded container / position, which the teach-once traces do not
         carry; this rung is how renamed controls heal. ``wp6_strict`` has no rename rung:
         a renamed control halts and is healed by teaching (an alias).

Secrets (WP7, scaffold/agent/desktop/secrets.py)
    ``BrokerEnv`` wraps a run env: ``env.secret(name)`` returns a ``{{secret:name}}`` handle
    (never the value), the value is substituted by ``SecretBroker.substitute`` only inside
    ``type`` (the sink) and the runner redacts its halt report and step log with
    ``broker.redact_obj`` before they leave it (SkillRunner picks up ``env.redact_obj``).

Effects (WP7, scaffold/agent/desktop/effects)
    Implemented in skill_runner.SkillRunner (``res["effects"]``, ``res["outcome"]``);
    ``run_outcome(res)`` returns the ``Outcome`` enum.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from scaffold.agent.desktop.effects.outcome import Outcome
from scaffold.agent.desktop.grounding import Target, ground as wp6
from scaffold.agent.desktop.secrets import NAME_RE, SecretBroker, handles_in

from .browser import Page
from .induction import ROLE, fuzzy_pick
from .skill_runner import Grounding, ground_role_name

AX_ROLE = {"link": "link", "button": "button", "textbox": "textfield"}
ROW_H, GAP, LEFT, WIDTH = 30, 10, 40, 360
HALT_REFUSALS = ("ambiguous", "window_ambiguous", "risk_escalation")


# ============================================================ AX tree ==

def page_to_ax_tree(page: Page, app: str = "") -> Tuple[dict, Dict[Tuple[int, ...], dict]]:
    """(AX-style tree, path -> page element). Paths are build_index paths."""
    by_path: Dict[Tuple[int, ...], dict] = {}
    top: List[dict] = []
    forms: Dict[int, dict] = {}
    for i, el in enumerate(page.elements):
        role = AX_ROLE.get(el["role"])
        if role is None:
            continue
        if role == "textfield" and el.get("secure"):
            role = "securetextfield"
        node: Dict[str, Any] = {"role": role, "title": el.get("name", ""),
                                "frame": [LEFT, 60 + i * (ROW_H + GAP), WIDTH, ROW_H], "_el": el}
        if el["role"] == "textbox":
            node["value"] = "" if el.get("secure") else el.get("value", "")
        fi = el.get("form")
        if fi is None:
            top.append(node)
            continue
        if fi not in forms:
            f = page.forms[fi] if 0 <= fi < len(page.forms) else {}
            forms[fi] = {"role": "form", "title": f.get("label", ""), "children": []}
            top.append(forms[fi])
        forms[fi]["children"].append(node)
    for f in forms.values():
        ys = [c["frame"][1] for c in f["children"]]
        f["frame"] = [LEFT - 10, min(ys) - 5, WIDTH + 20, max(ys) - min(ys) + ROW_H + 10]
    height = 120 + len(page.elements) * (ROW_H + GAP)
    tree = {"role": "window", "title": page.title or app or "page", "frame": [0, 0, WIDTH + 2 * LEFT, height],
            "children": [{"role": "webarea", "title": app or page.title or "web", "children": top}]}

    def walk(n: dict, path: Tuple[int, ...]) -> None:
        el = n.pop("_el", None)
        if el is not None:
            by_path[path] = el
        for k, c in enumerate(n.get("children") or []):
            walk(c, path + (k,))
    walk(tree, (0,))
    return tree, by_path


# ========================================================== grounding ==

def _risk(target: dict) -> Optional[str]:
    return Target(role=target.get("role"), name=target.get("name") or None).risk()


def _cands(g) -> List[str]:
    return [c.get("name", "") for c in (g.refusal.candidates if g.refusal else [])]


def _map(g, by_path, rung: str) -> Optional[Grounding]:
    """WP6 Grounding -> runner Grounding; None = not found here (try the next name)."""
    if g.ok:
        el = by_path.get(tuple(g.match.path))
        if el is None:                       # matched a container / non-element: not a control we can act on
            return None
        return Grounding("ok", el, rung, [el.get("name", "")])
    r = g.refusal.reason
    if r in HALT_REFUSALS:
        return Grounding("ambiguous", None, f"wp6:{g.refusal.tier or r}", _cands(g), refusal=r)
    return None


def wp6_ground(page: Optional[Page], target: dict, *, exclude=(), fuzzy: bool = True,
               rename: bool = True) -> Grounding:
    """The runner's grounding signature over the WP6 ladder (see the module docstring)."""
    if page is None:
        return Grounding("missing", rung="no screen")
    tree, by_path = page_to_ax_tree(page)
    role, name = target.get("role"), target.get("name") or ""
    risk = _risk(target)
    last = "not_found"
    for k, nm in enumerate([name] + [a for a in target.get("aliases", []) if a]):
        if not nm:
            continue
        g = wp6(tree, Target(role=role, name=nm, button_role=risk), allow_relaxed=fuzzy)
        if g.ok:
            exact = g.match.tier == "role_name" and g.match.name == name
            rung = "exact" if exact else ("alias" if k else
                                          ("normalized" if g.match.tier == "role_name" else f"wp6:{g.match.tier}"))
            m = _map(g, by_path, rung)
        else:
            last = g.refusal.reason
            m = _map(g, by_path, "")
        if m is not None:
            return m
    same = [e for e in page.elements if e["role"] == ROLE.get(role, role)]
    if fuzzy and rename and name:
        st, idx = fuzzy_pick(name, [e.get("name", "") for e in same], exclude=exclude)
        if st == "ambiguous":
            return Grounding("ambiguous", None, "wp6:rename", [same[i].get("name", "") for i in idx],
                             refusal="ambiguous")
        if st == "ok":
            picked = same[idx[0]].get("name", "")
            g = wp6(tree, Target(role=role, name=picked, button_role=risk), allow_relaxed=False)
            m = _map(g, by_path, "wp6:rename")
            if m is not None:
                return m
            last = g.refusal.reason if g.refusal else last
    return Grounding("missing", None, "", [e.get("name", "") for e in same][:40], refusal=last)


def wp6_strict(page: Optional[Page], target: dict, *, exclude=(), fuzzy: bool = True) -> Grounding:
    """WP6 ladder only: a renamed control halts (and is healed by teaching an alias)."""
    return wp6_ground(page, target, exclude=exclude, fuzzy=fuzzy, rename=False)


GROUNDERS: Dict[str, Callable[..., Grounding]] = {"heuristic": ground_role_name, "wp6": wp6_ground,
                                                  "wp6_strict": wp6_strict}


# ============================================================ secrets ==

def _key(name: str) -> str:
    k = re.sub(r"[^A-Za-z0-9_.\-]+", "_", (name or "").strip())[:64]
    return k if NAME_RE.match(k) else "secret"


class _EnvProvider:
    """A read-only SecretProvider over the run env's own secret lookup (keychain / ~/.config)."""

    def __init__(self, lookup: Callable[[str], Optional[str]], names: Dict[str, str]):
        self._lookup, self._names = lookup, names

    def get(self, name: str) -> Optional[str]:
        return self._lookup(self._names.get(name, name))

    def set(self, name: str, value: str) -> None:
        raise PermissionError("the run env's secret store is read-only")

    def delete(self, name: str) -> None:
        raise PermissionError("the run env's secret store is read-only")

    def __repr__(self) -> str:
        return f"_EnvProvider(names={sorted(self._names)})"

    def __reduce__(self):
        raise TypeError("secret providers are not serialisable")


class BrokerEnv:
    """A run env whose secrets go through the WP7 SecretBroker: the runner sees handles only,
    the value appears only at the typing sink, and reports are redacted (``redact_obj``)."""

    def __init__(self, base, broker: Optional[SecretBroker] = None):
        self.base = base
        self._names: Dict[str, str] = {}
        self.broker = broker or SecretBroker(_EnvProvider(base.secret, self._names))

    def __getattr__(self, k):
        return getattr(self.base, k)

    def secret(self, name: str) -> Optional[str]:
        key = _key(name)
        self._names[key] = name
        if not self.broker.has(key):
            return None
        self.broker.reveal_for_check(key)        # remembered in process memory so redaction knows it
        return self.broker.handle(key)

    def type(self, app: str, el: dict, value: str):
        if handles_in(value):
            value = self.broker.substitute(value, target=f"ui:{app}/{el.get('name', '')}")
        return self.base.type(app, el, value)

    def redact_obj(self, obj: Any) -> Any:
        return self.broker.redact_obj(obj)


# ============================================================ effects ==

def run_outcome(res: dict) -> Outcome:
    """The WP7 closed outcome of a SkillRunner result."""
    return Outcome(res["outcome"])
