"""
mutate.py — page mutations with exact ground truth, over the app factory's HTML (scripts/repair_bench.py).

    view = htmldriver.parse(html)                        # scaffold/rl/factory (import only)
    fp = ctl_fingerprint(view, i)                        # what the replayed step remembers
    m = mutate(html, i, "rename", rng)                   # Mutated(view, gt_index | None, note) or None (n/a)

The target control is tagged with ``data-awos-gt`` in the parsed tree before the mutation, the tree
is serialised back to HTML and parsed again, so the new page goes through exactly the accessible-
name / context rules the factory's driver (and the product's AX reader) use. The tag is invisible
to the fingerprint (no data-* attribute is a feature). Mutations (``MUTATIONS``):

  rename        the accessible name changes (word swap, qualifier, dropped word); ids in the name kept
  move          the control's block moves to another position in its container (or one level up)
  wrap          the block is wrapped in a plain <div> or in a named region (context changes)
  role          button <-> link (a slight role drift; fields / checkboxes: not applicable)
  delete        the block is removed: the right answer is NOT_FOUND
  decoy         a near-duplicate is added (same name or a close variant) next to it or in a new region
  delete_decoy  delete + a close-variant decoy where it was (the literature's worst case)

The rename word table here is deliberately NOT similo.SYNONYMS (partial overlap only), so the
benchmark does not just test the matcher's own synonym list.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from scaffold.rl.factory import htmldriver as H
from scaffold.rl.factory.htmldriver import VOID, El, PageView

from .similo import ID_RE, STOP, Fingerprint

GT = "data-awos-gt"
MUTATIONS = ("rename", "move", "wrap", "role", "delete", "decoy", "delete_decoy")
CONTROL_ROLES = {"button", "radiogroup"}
STOP_AT = {"body", "html", "form", "main", "nav", "header", "footer", "section", "table", "tbody", "thead", "ul",
           "ol", "#root"}
RENAME_WORDS: Dict[str, List[str]] = {
    "open": ["View", "Details", "Show"], "save": ["Save changes", "Apply", "Keep"], "search": ["Find", "Look up"],
    "next": ["Continue", "Go on"], "back": ["Previous", "Return"], "add": ["Create", "New", "Insert"],
    "edit": ["Change", "Modify", "Update"], "download": ["Export", "Get"], "email": ["E-mail", "Email address"],
    "phone": ["Phone number", "Telephone"], "settings": ["Preferences", "Configuration"], "help": ["Support"],
    "apply": ["Use", "Run"], "filter": ["Refine", "Narrow"], "sort": ["Order"], "mark": ["Set", "Flag"],
    "archive": ["Move to archive", "Store"], "select": ["Choose", "Pick"], "name": ["Full name"],
    "notes": ["Comments", "Remarks"], "status": ["State", "Stage"], "assign": ["Allocate", "Give"],
    "reply": ["Respond", "Answer"], "subject": ["Topic", "Title"], "message": ["Body", "Text"],
    "close": ["Dismiss", "Hide"], "reject": ["Decline", "Refuse"], "accept": ["Agree", "Allow"],
    "home": ["Dashboard", "Start"], "page": ["Screen", "View"], "show": ["Display", "Reveal"],
}
QUALIFIERS_BTN = ("{n} now", "{n} ›", "Go: {n}", "{n} (new)", "Quick {lo}")
QUALIFIERS_FIELD = ("{n} (required)", "Your {lo}", "{n} *", "Enter {lo}")


def control_els(root: El) -> List[El]:
    out = []
    for e in root.iter():
        a = e.attrs
        if (e.tag == "a" and "href" in a) or e.tag in ("button", "select", "textarea") or \
                a.get("role") in CONTROL_ROLES or (e.tag == "input" and a.get("type", "text") != "hidden"):
            out.append(e)
    return out


def _root_of(e: El) -> El:
    while e.parent is not None:
        e = e.parent
    return e


def serialize(e) -> str:
    if isinstance(e, str):
        return (e.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    if e.tag == "#root":
        return "".join(serialize(c) for c in e.children)
    attrs = "".join(f' {k}' if v == "" and k in ("checked", "selected", "hidden", "disabled", "multiple")
                    else f' {k}="{_attr(v)}"' for k, v in e.attrs.items())
    if e.tag in VOID:
        return f"<{e.tag}{attrs}>"
    if e.tag in ("script", "style"):
        return f"<{e.tag}{attrs}>" + "".join(c for c in e.children if isinstance(c, str)) + f"</{e.tag}>"
    return f"<{e.tag}{attrs}>" + "".join(serialize(c) for c in e.children) + f"</{e.tag}>"


def _attr(v: str) -> str:
    return str(v).replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")


def clone(e, parent: Optional[El] = None):
    if isinstance(e, str):
        return e
    n = El(e.tag, dict(e.attrs), parent)
    n.children = [clone(c, n) for c in e.children]
    return n


def _detach(e: El) -> int:
    i = e.parent.children.index(e)
    e.parent.children.pop(i)
    return i


def _insert(parent: El, i: int, e: El) -> None:
    parent.children.insert(i, e)
    e.parent = parent


def _subtree_controls(e: El) -> int:
    return sum(1 for _ in control_els(e)) if e.tag != "#root" else 1 << 30


def unit(el: El) -> El:
    """The largest block around ``el`` that holds no other control (its label, its cell, its row ...)."""
    u = el
    while u.parent is not None and u.parent.tag not in STOP_AT and u.parent.attrs.get("role") not in H.CONTEXT_ROLES \
            and _subtree_controls(u.parent) <= 1 and u.parent.attrs.get("id") != "appform":
        u = u.parent
    return u


# ------------------------------------------------------------------- names --

def _cap(word: str, like: str) -> str:
    return word[:1].upper() + word[1:] if like[:1].isupper() else word.lower()


def renamed(name: str, field_like: bool, rng: random.Random) -> str:
    words = name.split()
    swaps = [i for i, w in enumerate(words) if w.lower().strip(".,:()") in RENAME_WORDS
             and not ID_RE.match(w.lower())]
    r = rng.random()
    if swaps and r < 0.6:
        i = rng.choice(swaps)
        key = words[i].lower().strip(".,:()")
        words[i] = _cap(rng.choice(RENAME_WORDS[key]), words[i])
        out = " ".join(words)
    elif len(words) >= 3 and r < 0.75:
        drop = [i for i, w in enumerate(words) if not ID_RE.match(w.lower()) and w.lower() not in ("as",)]
        i = rng.choice(drop[1:] or drop)
        out = " ".join(w for j, w in enumerate(words) if j != i)
    else:
        q = rng.choice(QUALIFIERS_FIELD if field_like else QUALIFIERS_BTN)
        out = q.format(n=name, lo=name[:1].lower() + name[1:])
    return out if out.strip() and out != name else name + " now"


CONTRAST = ("Share", "Print", "Copy", "Export", "Hide", "Pin", "Follow", "Preview")


def decoy_name(name: str, rng: random.Random, others: Sequence[str] = ()) -> str:
    """A similar-looking control for ANOTHER thing: another id ("D-305" -> "D-395"), another
    entity's word from the page ("Victor Kaya" -> "Victor Lee"), or another action ("Share
    record"). Never a qualifier on the same words: that is a rename, not a decoy."""
    toks = name.split()
    ids = [i for i, w in enumerate(toks) if ID_RE.match(w.lower().strip(".,:()"))]
    if ids:
        i = rng.choice(ids)
        w = toks[i]
        digits = [j for j, ch in enumerate(w) if ch.isdigit()]
        j = rng.choice(digits)
        toks[i] = w[:j] + str((int(w[j]) + rng.randint(1, 8)) % 10) + w[j + 1:]
        return " ".join(toks)
    mine = {t.lower() for t in toks}
    pool = [o.split() for o in others if o and len(o.split()) == len(toks) and o != name
            and not ({t.lower() for t in o.split()} >= mine)]
    if pool and len(toks) >= 2 and rng.random() < 0.7:
        o = rng.choice(pool)
        i = rng.choice([k for k in range(len(toks)) if toks[k].lower() not in STOP] or [0])
        if o[i].lower() not in mine and o[i].lower() not in STOP:
            toks[i] = o[i]
            return " ".join(toks)
    if len(toks) >= 2:
        toks[0] = rng.choice([c for c in CONTRAST if c.lower() not in mine])
        return " ".join(toks)
    return f"{rng.choice(CONTRAST)} {name.lower()}"


def set_name(el: El, new: str) -> bool:
    """Change where ``el``'s accessible name comes from (same order as htmldriver.parse)."""
    root = _root_of(el)
    a = el.attrs
    by_id = {e.attrs["id"]: e for e in root.iter() if "id" in e.attrs}
    if a.get("aria-labelledby"):
        ref = by_id.get(a["aria-labelledby"].split()[0])
        if ref is None:
            return False
        ref.children = [new]
        return True
    if a.get("aria-label"):
        a["aria-label"] = new
        return True
    field_like = el.tag in ("input", "select", "textarea")
    if field_like:
        lab = next((e for e in root.iter() if e.tag == "label" and e.attrs.get("for") == a.get("id") and a.get("id")),
                   None)
        if lab is None:
            anc = el.parent
            while anc is not None and anc.tag != "label":
                anc = anc.parent
            lab = anc
        if lab is not None and lab.text():
            lab.children = [c for c in lab.children if isinstance(c, El) and c.tag in ("input", "select",
                                                                                        "textarea")]
            lab.children.append(" " + new)
            return True
    elif el.text():
        el.children = [c for c in el.children if isinstance(c, El) and c.attrs.get("aria-hidden") == "true"] + [new]
        return True
    if a.get("title"):
        a["title"] = new
        return True
    if field_like and a.get("placeholder"):
        a["placeholder"] = new
        return True
    return False


# --------------------------------------------------------------- mutations --

def _rename(el, rng, view, ctl):
    return set_name(el, renamed(ctl.name, el.tag in ("input", "select", "textarea"), rng))


def _move(el, rng, view, ctl):
    u = unit(el)
    par = u.parent
    sibs = [c for c in par.children if isinstance(c, El)]
    if len(sibs) > 1:
        i = sibs.index(u)
        _detach(u)
        if i == 0:
            par.children.append(u)
        else:
            _insert(par, par.children.index(sibs[0]) if sibs[0] is not u else 0, u)
        return True
    gp = par.parent
    if gp is None or gp.tag == "#root":
        return False
    _detach(u)
    _insert(gp, 0, u)
    return True


REGION_NAMES = ("More", "Quick actions", "Shortcuts", "Tools", "Panel")


def _wrap(el, rng, view, ctl):
    u = unit(el)
    par = u.parent
    i = _detach(u)
    if rng.random() < 0.5:
        w = El("div", {"class": "wrap"}, par)
        inner = El("div", {"class": "inner"}, w)
        w.children = [inner]
        inner.children = [u]
        u.parent = inner
    else:
        w = El("section", {"role": "region", "aria-label": rng.choice(REGION_NAMES)}, par)
        w.children = [u]
        u.parent = w
    _insert(par, i, w)
    return True


def _role(el, rng, view, ctl):
    if ctl.role == "button":
        keep = {k: v for k, v in el.attrs.items() if k in ("title", "class", GT) or k.startswith("aria-")}
        n = El("a", dict(keep, href="#act"), el.parent)
    elif ctl.role == "link":
        keep = {k: v for k, v in el.attrs.items() if k in ("title", "class", GT) or k.startswith("aria-")}
        n = El("button", dict(keep, type="button"), el.parent)
    else:
        return False
    n.children = el.children
    for c in n.children:
        if isinstance(c, El):
            c.parent = n
    i = el.parent.children.index(el)
    el.parent.children[i] = n
    return True


def _delete(el, rng, view, ctl):
    u = unit(el)
    if el.tag in ("input", "select", "textarea") and el.attrs.get("id"):
        root = _root_of(el)
        for lab in [e for e in root.iter() if e.tag == "label" and e.attrs.get("for") == el.attrs["id"]]:
            if lab.parent is not None and not _contains(u, lab):
                _detach(lab)
    _detach(u)
    return True


def _contains(a: El, b: El) -> bool:
    return any(x is b for x in a.iter())


def _make_decoy(el, rng, view, ctl, same_name: bool) -> El:
    u = unit(el)
    d = clone(u)
    for e in d.iter():
        e.attrs.pop(GT, None)
        for k in ("id", "for"):
            if k in e.attrs:
                e.attrs[k] = e.attrs[k] + "-dcy"
        if e.attrs.get("aria-labelledby"):
            e.attrs["aria-labelledby"] = " ".join(x + "-dcy" for x in e.attrs["aria-labelledby"].split())
    if not same_name:
        others = [c.name for c in view.controls if c.role == ctl.role]
        set_name(_follow(d, _path(u, el)), decoy_name(ctl.name, rng, others))
    return d


def _path(top: El, el: El) -> List[int]:
    out = []
    while el is not top:
        out.append(el.parent.children.index(el))
        el = el.parent
    return out[::-1]


def _follow(top: El, path: List[int]) -> El:
    for i in path:
        top = top.children[i]
    return top


def _decoy(el, rng, view, ctl):
    same = rng.random() < 0.5
    d = _make_decoy(el, rng, view, ctl, same)
    u = unit(el)
    if rng.random() < 0.4:                                   # right next to it (the "row 3 vs row 4" case)
        par = u.parent
        _insert(par, par.children.index(u) + 1, d)
        d.parent = par
        return True
    root = _root_of(el)
    host = next((e for e in root.iter() if e.tag == "main"), None) or \
        next((e for e in root.iter() if e.attrs.get("id") == "appform"), None)
    if host is None:
        return False
    reg = El("section", {"role": "region", "aria-label": rng.choice(("Recently viewed", "Suggested", "Pinned"))},
             host)
    reg.children = [d]
    d.parent = reg
    _insert(host, 0, reg)
    return True


def _delete_decoy(el, rng, view, ctl):
    d = _make_decoy(el, rng, view, ctl, same_name=False)
    u = unit(el)
    par = u.parent
    i = par.children.index(u)
    _delete(el, rng, view, ctl)
    _insert(par, min(i, len(par.children)), d)
    d.parent = par
    return True


FNS = {"rename": _rename, "move": _move, "wrap": _wrap, "role": _role, "delete": _delete, "decoy": _decoy,
       "delete_decoy": _delete_decoy}


@dataclass
class Mutated:
    kind: str
    html: str
    view: PageView
    gt: Optional[int]                 # index of the target in view.controls; None = deleted


def mutate(html: str, index: int, kind: str, rng: random.Random) -> Optional[Mutated]:
    """Apply one mutation to control ``index`` of the page; None when it does not apply."""
    view = H.parse(html)
    ctl = view.controls[index]
    el = ctl.el
    el.attrs[GT] = "1"
    if not FNS[kind](el, rng, view, ctl):
        return None
    out = serialize(_root_of(el))
    v2 = H.parse(out)
    gt = [i for i, c in enumerate(v2.controls) if c.el is not None and c.el.attrs.get(GT) == "1"]
    if kind in ("delete", "delete_decoy"):
        if gt:
            return None
        return Mutated(kind, out, v2, None)
    if len(gt) != 1:
        return None
    if kind == "rename" and v2.controls[gt[0]].name == ctl.name:
        return None
    return Mutated(kind, out, v2, gt[0])


# ------------------------------------------------------------ fingerprints --

def _label(el: El, own: str) -> str:
    u = el
    for _ in range(3):
        if u.parent is None or u.parent.tag in ("body", "#root"):
            break
        u = u.parent
        t = u.text()
        if own:
            t = t.replace(own, " ")
        t = " ".join(t.split())
        if t:
            return t[:200]
    return ""


def _ancestors(el: El) -> tuple:
    out = []
    e = el.parent
    while e is not None and e.tag not in ("#root", "html", "body"):
        out.append(e.tag + (f"[{e.attrs['role']}]" if e.attrs.get("role") else ""))
        e = e.parent
    return tuple(reversed(out))[-8:]


def ctl_fingerprint(view: PageView, i: int, *, ref=None) -> Fingerprint:
    c = view.controls[i]
    same = [x for x in view.controls if x.role == c.role]
    names = [x.name for x in view.controls]
    el = c.el
    text = ""
    if el is not None:
        text = el.attrs.get("placeholder", "") if el.tag in ("input", "textarea") else el.text()
    return Fingerprint(role=c.role, name=c.name, text=text, label=_label(el, c.name) if el is not None else "",
                       context=c.context, ancestors=_ancestors(el) if el is not None else (),
                       index=next(k for k, x in enumerate(same) if x is c), count=len(same),
                       siblings=tuple(names[max(0, i - 2):i] + names[i + 1:i + 3]), href=c.href,
                       twins=sum(1 for x in same if x.name == c.name), ref=c if ref is None else ref)


def view_candidates(view: PageView) -> List[Fingerprint]:
    return [ctl_fingerprint(view, i) for i in range(len(view.controls))]


__all__ = ["GT", "MUTATIONS", "Mutated", "ctl_fingerprint", "decoy_name", "mutate", "renamed", "serialize",
           "set_name", "unit", "view_candidates"]
