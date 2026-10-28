"""
mind2web_data.py — Mind2Web train trajectories -> SFT rows in the dataset.py output schema
(docs/specs/web_data.md).

    python -m scaffold.rl.train.mind2web_data \\
        --input /data/m2w/train_0.json [--input ...] \\
        --exclude /data/om2w/Online_Mind2Web.json [--exclude ...] \\
        --out runs/rl/m2w_rows.jsonl [--max-tasks 50] [--max-steps 12] [--after-skip stop|continue] \\
        [--exclude-overlap-websites] [--threshold 0.75]

The rows file is an ``--extra-rows`` input of dataset.py (messages system/user/assistant,
prompt/completion from chat.py, meta.task_id / meta.split = "train"). A stats JSON lands next to it
(``<out>.stats.json``): step mapping rate, rows per task, prompt sizes, skip reasons, contamination.

Per step (one Mind2Web action):
  * the stored ``raw_html`` is loaded into headless Chromium with JavaScript off; every request is
    answered by a route (the page itself is fulfilled from memory, everything else aborted), and the
    browser is the harness's (``harness.chromium.Chromium``: host resolver maps every host to
    NOTFOUND). The HTML is untrusted data: it never runs, it is only laid out and read.
  * elements Mind2Web recorded as not rendered (bounding_box_rect "-1,-1,-1,-1" with no rendered
    descendant) are hidden (display:none), because the dump has no CSS; values the trajectory
    typed / selected earlier on the same page are put back in their fields;
  * the observation is the product's: harness.chromium.read_cdp (CDP Accessibility.getFullAXTree +
    DOMSnapshot.captureSnapshot) -> axmap.snapshot_from_cdp -> live.driver.to_micro_page ->
    web_tools.Page (the exact path of ChromiumBackend._snap / _load), and the prompt is MicroAgent._prompt with the product wiring
    (BrokerSecretStore masking, WebAXGrounder), SYSTEM, Step i of max_steps, history of previous
    actions in the product's wording, "Pages so far", the injection warning;
  * the ground-truth element (``data_pw_testid_buckeye`` = action_uid in raw_html, then the
    pos_candidates) is located among OUR numbered controls through the DOM: the element itself, its
    nearest control ancestor (span inside a link), a radio option of a radiogroup, or the one
    compatible control inside it;
  * the action is written in the GBNF grammar: click(n) / fill(n, "text") / select(n, "option");
    when the control is past the 40-control window, scroll(down) rows come first (as the product
    must). Mind2Web ENTER (original_op, mapped to CLICK on a text box) becomes click(submit button of
    the same form) when the form has exactly one button, else the step is unmapped: the product has no
    Enter verb yet (docs/specs/web_data.md);
  * HOVER, a click on a text box (focus) and a click on a <select> before SELECT are no-ops for our
    agent: no row, no history line;
  * the product's code checks are applied before a row is written: guard_page (CAPTCHA / MFA /
    passkey / HTTP walls stop the episode), WebAXGrounder must resolve the control (no ambiguous
    controls), no destructive control, fill values that MicroAgent would refuse (credential-looking,
    > 200 chars, a password / code field) are unmapped.
  * an unmapped step stops the task's rows by default (--after-skip stop): the later prompts would
    show a history the agent never produced. Every step is still mapped for the mapping-rate metric.
  * final row done("...") only when the page after the last action is known: the last action was a
    fill / select (the same snapshot with the value applied). After a final click Mind2Web has no
    snapshot, so no done row (counted as no_final_page).

Contamination (hard rule): Online-Mind2Web is our test set. ``--exclude`` (Online_Mind2Web.json or
any copy: JSON list or JSONL with confirmed_task / task_description / task and website) is REQUIRED;
a Mind2Web task whose text fuzzy-matches any benchmark task (normalised SequenceMatcher ratio or
token Jaccard over the threshold) is excluded, and every written row is re-checked (check_row raises
ContaminationError). Website overlap is reported separately (and dropped with
--exclude-overlap-websites).
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
import time
import urllib.parse
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

from scaffold.agent.desktop.web_tools import CLICKABLE_ROLES
from scaffold.rl.harness.axmap import flat_document  # noqa: F401  (moved to the shared reader; re-exported)
from scaffold.rl.train.dataset import EvalSplitError, Example, _len_stats

#: the URL each snapshot is served at; never fetched (a route answers it from memory)
SNAPSHOT_URL = "http://127.0.0.1:9/"
TASK_PREFIX = "m2w:"
DEFAULT_THRESHOLD = 0.75
DEFAULT_JACCARD = 0.7
HIDDEN_RECT = "-1,-1,-1,-1"
CLICKABLE = CLICKABLE_ROLES          # what click(n) acts on (the AX reader's extra click targets included)
MAX_SCROLLS = 6                       # a target past 7 windows (280 controls) is unmapped (too_far)


class ContaminationError(EvalSplitError):
    """A training row's task matches an Online-Mind2Web (test) task."""


# ------------------------------------------------------------------ reading --

_TASK_START = re.compile(rb'\{"website"\s*:')


def iter_tasks(path: Path, chunk: int = 1 << 24) -> Iterator[dict]:
    """Tasks of a Mind2Web train JSON file (a JSON list of task objects), streamed: a shard is
    ~600 MB. Inside JSON strings every quote is escaped, so an unescaped ``{"website":`` only
    occurs where a task object starts; each slice between two starts is parsed on its own. A
    truncated file (a ranged download) yields every complete task and drops the torn last one."""
    dec = json.JSONDecoder()
    buf = b""
    with open(path, "rb") as fh:
        eof = False
        while not eof:
            data = fh.read(chunk)
            eof = not data
            buf += data
            starts = [m.start() for m in _TASK_START.finditer(buf)]
            ends = starts[1:] if not eof else starts[1:] + [len(buf)]
            done_upto = 0
            for s, e in zip(starts, ends):
                text = buf[s:e].decode("utf-8", errors="replace").rstrip().rstrip(",]").rstrip()
                try:
                    obj, _ = dec.raw_decode(text)
                except ValueError:
                    done_upto = e
                    continue                                  # torn (truncated download)
                if isinstance(obj, dict) and isinstance(obj.get("actions"), list):
                    yield obj
                done_upto = e
            buf = buf[done_upto:] if starts else buf[-64:]


def read_tasks(paths: Sequence[Path]) -> Iterator[dict]:
    for p in paths:
        yield from iter_tasks(Path(p))


# ------------------------------------------------------------ contamination --

@dataclass
class BenchTask:
    task_id: str
    text: str
    website: str = ""


def _records(path: Path) -> List[dict]:
    raw = Path(path).read_text(encoding="utf-8")
    try:
        d = json.loads(raw)
    except ValueError:
        d = [json.loads(ln) for ln in raw.splitlines() if ln.strip()]
    if isinstance(d, dict):                       # a one-line JSONL, or {"data": [...]}
        d = d.get("data") or d.get("tasks") or [d]
    return [r for r in d if isinstance(r, dict)]


def load_benchmark(paths: Sequence[Path]) -> List[BenchTask]:
    """Online-Mind2Web tasks from one or more copies (versions differ: tasks are replaced over
    time, so pass every copy you have; the union is excluded)."""
    out: List[BenchTask] = []
    seen = set()
    for p in paths:
        for r in _records(p):
            text = r.get("confirmed_task") or r.get("task_description") or r.get("task") or ""
            if not isinstance(text, str) or not text.strip():
                continue
            key = (str(r.get("task_id", "")), norm_text(text))
            if key in seen:
                continue
            seen.add(key)
            out.append(BenchTask(str(r.get("task_id", "")), text, str(r.get("website") or "")))
    if not out:
        raise ValueError(f"no benchmark tasks in {', '.join(map(str, paths))}")
    return out


def norm_text(s: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).split())


def site_key(website: str) -> str:
    """'https://www.united.com/en' -> 'united'; 'sports.yahoo' -> 'sports.yahoo'; 'bbc.co.uk' -> 'bbc'."""
    w = (website or "").strip().lower()
    if "//" in w:
        w = urllib.parse.urlsplit(w).hostname or ""
    w = w.split("/")[0]
    if w.startswith("www."):
        w = w[4:]
    parts = [x for x in w.split(".") if x]
    if len(parts) >= 3 and parts[-2] in ("co", "com", "org", "net", "ac", "gov") and len(parts[-1]) == 2:
        parts = parts[:-2]
    elif len(parts) >= 2 and parts[-1] in _TLDS:
        parts = parts[:-1]
    return ".".join(parts)


_TLDS = {"com", "org", "net", "gov", "edu", "io", "co", "us", "uk", "ca", "de", "fr", "info", "tv", "me", "ai"}


def brand(website: str) -> str:
    """The last label of site_key ('sports.yahoo' -> 'yahoo')."""
    k = site_key(website)
    return k.split(".")[-1] if k else ""


class Exclusion:
    """Refuses Mind2Web tasks that fuzzy-match an Online-Mind2Web task; reports website overlap."""

    def __init__(self, bench: List[BenchTask], threshold: float = DEFAULT_THRESHOLD,
                 jaccard: float = DEFAULT_JACCARD):
        if not bench:
            raise ValueError("an empty benchmark list would exclude nothing; refusing")
        self.bench = bench
        self.threshold, self.jaccard = threshold, jaccard
        self._norm = [(b, norm_text(b.text), set(norm_text(b.text).split())) for b in bench]
        self.sites = {site_key(b.website): b.website for b in bench if b.website}
        self.brands = {brand(b.website): b.website for b in bench if b.website}

    def nearest(self, text: str) -> Tuple[Optional[BenchTask], float, float]:
        """(closest benchmark task, SequenceMatcher ratio, token Jaccard) by max of the two."""
        t = norm_text(text)
        toks = set(t.split())
        best: Tuple[Optional[BenchTask], float, float] = (None, 0.0, 0.0)
        sm = difflib.SequenceMatcher(autojunk=False)
        sm.set_seq2(t)
        for b, bt, btoks in self._norm:
            jac = len(toks & btoks) / max(1, len(toks | btoks))
            sm.set_seq1(bt)
            if sm.real_quick_ratio() < min(best[1], self.threshold) and jac < best[2]:
                continue
            r = sm.ratio()
            if max(r, jac) > max(best[1], best[2]):
                best = (b, r, jac)
        return best

    def match(self, text: str) -> Optional[Tuple[BenchTask, float, float]]:
        b, r, jac = self.nearest(text)
        if b is not None and (r >= self.threshold or jac >= self.jaccard):
            return b, r, jac
        return None

    def website_overlap(self, website: str) -> Optional[str]:
        """The benchmark website this Mind2Web website is ("united" -> "https://www.united.com/"), else None."""
        return self.sites.get(site_key(website))

    def brand_overlap(self, website: str) -> Optional[str]:
        """A benchmark website of the same brand but another site ("sports.yahoo" -> finance.yahoo.com)."""
        if self.website_overlap(website):
            return None
        b = brand(website)
        return self.brands.get(b) if b else None


def check_row(row: dict, excl: Exclusion) -> None:
    """The last gate before a row is written: its task text must not match a benchmark task."""
    meta = row.get("meta") or {}
    text = meta.get("task_text") or ""
    hit = excl.match(text)
    if hit is not None:
        raise ContaminationError(f"row of {meta.get('task_id')!r} matches Online-Mind2Web task "
                                 f"{hit[0].task_id!r} (ratio {hit[1]:.2f}, jaccard {hit[2]:.2f}): "
                                 f"{text[:80]!r}")
    if meta.get("split") != "train":
        raise EvalSplitError(f"row of {meta.get('task_id')!r} has split={meta.get('split')!r}")


# ------------------------------------------------------------------ browser --

#: (target m2w ids, values {m2w id: [kind, value]}, fallback title, action_uid)
#: -> [hidden, revealed, dialogs hidden because the target is outside them]. Runs in the page's
#: main world through CDP while the page's own scripts are disabled; our code, not the page's.
PREP_JS = r"""
([keep, values, title, uid]) => {
  let hidden = 0, revealed = 0;
  const rect = el => el.getAttribute('bounding_box_rect');
  const rendered = el => rect(el) !== null && rect(el) !== '-1,-1,-1,-1';
  // the target's region: everything under the target's nearest rendered ancestor stays as dumped,
  // so a menu the annotator opened (dumped before it rendered) is shown whole, not just the item
  const targets = [];
  if (uid) targets.push(...document.querySelectorAll('[data_pw_testid_buckeye="' + CSS.escape(uid) + '"]'));
  for (const k of keep) {
    const e = document.querySelector('[backend_node_id="' + CSS.escape(String(k)) + '"]');
    if (e) targets.push(e);
  }
  const protect = new Set();
  for (const t of targets) {
    if (rendered(t)) { protect.add(t); continue; }
    let x = t;
    while (x && x.nodeType === 1 && !rendered(x)) x = x.parentElement;
    protect.add(x && x !== document.documentElement ? x : document.body);
    revealed++;
  }
  // dialogs that do not hold the target are hidden: one dumped as not rendered / zero-size is closed,
  // whatever its children report; a rendered one did not block the annotator, who acted outside it
  // (a cookie banner, a header panel with role=dialog). WebAX grounding refuses every control
  // "behind" an open dialog, so a dialog the human ignored must not look open.
  const area = el => {
    const p = String(rect(el) || '').split(',').map(Number);
    return p.length === 4 && p[2] > 0 && p[3] > 0;
  };
  let behind = 0;
  const dialogs = document.querySelectorAll('[role="dialog"], [role="alertdialog"], [aria_modal="true"], dialog');
  for (const d of Array.from(dialogs)) {
    if (targets.some(t => d.contains(t))) continue;
    if (area(d)) { if (!targets.length) continue; behind++; }
    d.style.setProperty('display', 'none', 'important');
    hidden++;
  }
  const walk = el => {
    const tag = el.tagName.toLowerCase();
    if (tag === 'select' || tag === 'head' || protect.has(el)) return true;
    let live = rendered(el);
    for (const c of Array.from(el.children)) if (walk(c)) live = true;
    if (!live && rect(el) === '-1,-1,-1,-1') {
      el.style.setProperty('display', 'none', 'important');
      hidden++;
    }
    return live;
  };
  if (document.body) walk(document.body);
  // undo Mind2Web's attribute rewriting: aria_label -> aria-label, input_value / input_checked ->
  // the live value / checked state, and <a> lost its href (an <a> without href is not a link)
  for (const el of Array.from(document.querySelectorAll('*'))) {
    for (const at of Array.from(el.attributes)) {
      if (at.name.startsWith('aria_')) el.setAttribute('aria-' + at.name.slice(5).replace(/_/g, '-'), at.value);
    }
    if (el.hasAttribute('input_value') && 'value' in el && el.tagName.toLowerCase() !== 'select') {
      try { el.value = el.getAttribute('input_value'); } catch (e) {}
    }
    if (el.hasAttribute('input_checked') && 'checked' in el) {
      el.checked = String(el.getAttribute('input_checked')).toLowerCase() === 'true';
    }
    if (el.tagName.toLowerCase() === 'a' && !el.hasAttribute('href')
        && !el.querySelector('a, button, input, select, textarea')) el.setAttribute('href', '#');
  }
  if (!document.title && title) document.title = title;
  for (const [id, kv] of Object.entries(values)) {
    const el = document.querySelector('[backend_node_id="' + id + '"]');
    if (!el) continue;
    const [kind, v] = kv;
    if (kind === 'select' && el.tagName.toLowerCase() === 'select') {
      const want = String(v).trim().toLowerCase();
      for (const o of el.options) {
        if ((o.text || '').trim().toLowerCase() === want) { el.value = o.value; o.selected = true; }
      }
    } else if ('value' in el) {
      el.value = v;
    }
  }
  return [hidden, revealed, behind];
}
"""


VOID_TAGS = frozenset("area base br col embed hr img input link meta param source track wbr".split())
_SELF_CLOSING = re.compile(r"<([A-Za-z][A-Za-z0-9-]*)(\s[^<>]*?)?/>")


def repair_html(html: str) -> str:
    """Mind2Web serialised empty elements self-closing (``<span class=x/>``, ``<div .../>``). An HTML
    parser ignores the slash on a non-void element, so every later sibling would nest inside it (pages
    thousands of levels deep, controls swallowed by an AXImage leaf). Write them as open + close tags,
    which restores the DOM that was dumped."""
    def fix(m: "re.Match[str]") -> str:
        tag = m.group(1)
        if tag.lower() in VOID_TAGS:
            return m.group(0)
        return f"<{tag}{(m.group(2) or '').rstrip()}></{tag}>"
    return _SELF_CLOSING.sub(fix, html)


@dataclass
class Rendered:
    page: Any                                   # web_tools.Page (with _live)
    m2w_of: Dict[int, str]                      # chromium backendNodeId -> Mind2Web backend_node_id
    by_m2w: Dict[str, int]                      # Mind2Web backend_node_id -> chromium backendNodeId
    parent: Dict[int, Optional[int]]
    tag: Dict[int, str]
    buckeye: Optional[int]                      # the element marked with the action_uid
    hidden: int = 0                             # elements hidden (not rendered when dumped)
    revealed: int = 0                           # targets whose unrendered region was kept visible
    ax_ids: set = field(default_factory=set)    # backendNodeIds with an unignored AX node
    dialogs_behind: int = 0                     # rendered dialogs hidden because the target is outside
    labels: Dict[int, int] = field(default_factory=dict)   # <label for=id> -> the labelled element


def dom_maps(doc: dict, uid: str = "") -> Tuple[Dict[int, str], Dict[int, Optional[int]], Dict[int, str],
                                                Optional[int], Dict[int, int]]:
    """(chromium backendNodeId -> Mind2Web backend_node_id, -> parent element, -> tag, the element marked
    with ``uid``, <label> -> its labelled element) for a DOM.getDocument(depth=-1) result or a
    flat_document() (iterative: real pages nest deeper than Python's recursion limit)."""
    m2w: Dict[int, str] = {}
    parent: Dict[int, Optional[int]] = {}
    tag: Dict[int, str] = {}
    marked: List[int] = []
    ids: Dict[str, int] = {}
    label_for: Dict[int, str] = {}
    stack: List[Tuple[dict, Optional[int]]] = [(doc.get("root") or doc, None)]
    while stack:
        n, up = stack.pop()
        bid = n.get("backendNodeId")
        if n.get("nodeType") == 1 and bid is not None:
            a = n.get("attributes") or []
            attrs = {a[i]: a[i + 1] for i in range(0, len(a) - 1, 2)}
            parent[bid] = n.get("_parent", up)
            tag[bid] = str(n.get("localName") or n.get("nodeName") or "").lower()
            if "backend_node_id" in attrs:
                m2w[bid] = attrs["backend_node_id"]
            if attrs.get("id"):
                ids.setdefault(attrs["id"], bid)
            if tag[bid] == "label" and attrs.get("for"):
                label_for[bid] = attrs["for"]
            if uid and attrs.get("data_pw_testid_buckeye") == uid:
                marked.append(bid)
            up = bid
        for c in reversed(n.get("children") or []):
            stack.append((c, up))
    labels = {lb: ids[f] for lb, f in label_for.items() if f in ids}
    return m2w, parent, tag, (marked[0] if marked else None), labels


class SnapshotBrowser:
    """Renders stored HTML in headless Chromium (harness Chromium, JS off, no network) and reads it
    through the product's observation path."""

    def __init__(self, chromium: Any = None, viewport=(1280, 1080)):
        from scaffold.rl.harness.chromium import Chromium
        self._own = chromium is None
        self.chromium = chromium or Chromium()
        self.ctx = self.chromium.browser.new_context(viewport={"width": viewport[0], "height": viewport[1]},
                                                     java_script_enabled=False, accept_downloads=False,
                                                     service_workers="block")
        self._html = ""
        self.blocked = 0
        self.flat_fallbacks = 0                  # pages nested past DOM.getDocument's limit (harness.chromium.DEEP_DOM)
        self.ctx.route("**/*", self._route)
        self.pg = self.ctx.new_page()
        self.cdp = self.ctx.new_cdp_session(self.pg)
        self.cdp.send("DOM.enable")
        self.cdp.send("Accessibility.enable")

    def _route(self, route) -> None:
        req = route.request
        if req.url.split("#")[0] == SNAPSHOT_URL and req.is_navigation_request():
            route.fulfill(status=200, content_type="text/html; charset=utf-8", body=self._html)
        else:
            self.blocked += 1
            route.abort()

    def render(self, html: str, *, uid: str = "", keep: Sequence[str] = (),
               values: Optional[Dict[str, Tuple[str, str]]] = None, title: str = "") -> Rendered:
        from scaffold.agent.desktop.live.driver import to_micro_page
        from scaffold.agent.desktop.web_tools import WebError
        from scaffold.rl.harness.chromium import read_cdp
        self._html = repair_html(html)
        self.pg.goto(SNAPSHOT_URL, wait_until="load", timeout=30000)
        vals = {k: list(v) for k, v in (values or {}).items()}
        hidden, revealed, behind = self.pg.evaluate(PREP_JS, [list(keep), vals, title, uid])
        url = self.pg.url
        try:
            title = self.pg.title()
        except Exception:
            title = ""
        # == ChromiumBackend._snap + _load (one shared reader, harness.chromium.read_cdp)
        snap, ax, dom, deep = read_cdp(self.cdp, url, title)
        self.flat_fallbacks += int(deep)
        if not snap.ok or not snap.web_paths:
            raise WebError("the snapshot has no web area")
        page = to_micro_page(snap)
        m2w, parent, tag, marked, labels = dom_maps(dom, uid)
        return Rendered(page, m2w, {v: k for k, v in m2w.items()}, parent, tag, marked, int(hidden), int(revealed),
                        {n.get("backendDOMNodeId") for n in ax.get("nodes") or [] if not n.get("ignored")}, int(behind),
                        labels)

    def close(self) -> None:
        try:
            self.ctx.close()
        finally:
            if self._own:
                self.chromium.close()

    def __enter__(self) -> "SnapshotBrowser":
        return self

    def __exit__(self, *a) -> None:
        self.close()


# ------------------------------------------------------------------ mapping --

@dataclass
class Located:
    reply: Optional[str] = None                 # the action line, None when not mapped
    n: Optional[int] = None
    how: str = ""                               # self | ancestor | descendant | option | submit
    skip: str = ""                              # why unmapped ("" when mapped)
    noop: bool = False                          # harmless for our agent: no row, keep going


def q(s: str) -> str:
    """A GBNF ``str`` literal: only \\ and \" are escaped (json.dumps would emit \\uXXXX)."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _control_index(r: Rendered) -> Tuple[Dict[int, int], Dict[int, Tuple[int, str]]]:
    page = r.page
    snap = page._live["snap"]
    ctl: Dict[int, int] = {}
    opt: Dict[int, Tuple[int, str]] = {}
    for n, path in page._live["paths"].items():
        bid = snap.element(path)
        if bid is not None:
            ctl.setdefault(bid, n)
        c = page.control(n)
        for low, opath in (page._live["options"].get(n) or {}).items():
            obid = snap.element(opath)
            if obid is not None and c is not None:
                canon = next((o for o in c.options if o.lower() == low), low)
                opt.setdefault(obid, (n, canon))
    return ctl, opt


def _ancestors(r: Rendered, bid: int) -> Iterator[int]:
    x: Optional[int] = bid
    seen = 0
    while x is not None and seen < 400:
        yield x
        x = r.parent.get(x)
        seen += 1


def _compatible(op: str, role: str) -> bool:
    if op == "TYPE":
        return role in ("textbox", "password")
    if op == "SELECT":
        return role in ("combobox", "radiogroup")
    return role in CLICKABLE or role in ("textbox", "combobox", "radiogroup")


def candidate_ids(action: dict) -> List[str]:
    pos = action.get("pos_candidates") or []
    pos = sorted(pos, key=lambda c: (not c.get("is_original_target"), not c.get("is_top_level_target")))
    return [str(c.get("backend_node_id")) for c in pos if c.get("backend_node_id") is not None]


def locate(r: Rendered, action: dict) -> Located:
    """Our control for the step's ground-truth element, and the action line in our grammar."""
    from scaffold.agent.desktop import micro_agent as MA
    op = (action.get("operation") or {}).get("op", "CLICK")
    orig = (action.get("operation") or {}).get("original_op", op)
    value = (action.get("operation") or {}).get("value", "") or ""
    page = r.page
    if orig == "HOVER":
        return Located(skip="hover", noop=True)
    targets: List[int] = []
    if r.buckeye is not None:
        targets.append(r.buckeye)
    targets += [r.by_m2w[i] for i in candidate_ids(action) if i in r.by_m2w and r.by_m2w[i] not in targets]
    if not targets:
        return Located(skip="target_not_in_dom")
    ctl, opt = _control_index(r)
    found: Optional[Tuple[int, str, Optional[str]]] = None       # (n, how, option)
    for t in targets:
        for k, x in enumerate(_ancestors(r, t)):
            if x in opt:
                found = (opt[x][0], "option", opt[x][1])
                break
            if x in ctl and _compatible(op, page.control(ctl[x]).role):
                found = (ctl[x], "self" if k == 0 else "ancestor", None)
                break
        if found:
            break
        inside = []
        for bid, n in ctl.items():
            if bid != t and t in _ancestors(r, bid) and _compatible(op, page.control(n).role):
                inside.append(n)
        if len(set(inside)) == 1:
            found = (inside[0], "descendant", None)
            break
        if len(set(inside)) > 1:
            found = (-1, "ambiguous", None)
    if not found:                                # a click on a <label>: the control it labels
        for t in targets:
            for x in list(_ancestors(r, t))[:4]:
                if r.tag.get(x) != "label":
                    continue
                inner = [bid for bid in list(ctl) + list(opt) if bid != x and x in _ancestors(r, bid)]
                to = r.labels.get(x) or (inner[0] if len(inner) == 1 else None)
                if to in opt:
                    found = (opt[to][0], "label", opt[to][1])
                elif to in ctl and _compatible(op, page.control(ctl[to]).role):
                    found = (ctl[to], "label", None)
                break
            if found:
                break
    if not found:
        # not_in_ax: the element is not in the accessibility tree at all (hidden / presentational);
        # not_semantic: it is (e.g. a clickable <li> / <div> with no role), but nothing in its chain is a
        # control our observation numbers: the product could not act on it either
        return Located(skip="target_not_semantic" if any(t in r.ax_ids for t in targets) else "target_not_in_ax")
    n, how, option = found
    if n < 0:
        return Located(skip="ambiguous_descendants")
    c = page.control(n)
    if orig == "ENTER" or (op == "CLICK" and c.role == "textbox"):
        if orig != "ENTER":
            return Located(n=n, how=how, skip="click_on_textbox", noop=True)
        btns = [b for b in page.controls if b.form is not None and b.form == c.form and b.role == "button"]
        if c.form is None or len(btns) != 1:
            return Located(n=n, how=how, skip="enter_no_submit")
        return Located(reply=f"click({btns[0].n})", n=btns[0].n, how="submit")
    if op == "CLICK":
        if option is not None:
            return Located(reply=f"select({n}, {q(option)})", n=n, how=how)
        if c.role in ("combobox", "radiogroup"):
            return Located(n=n, how=how, skip="click_on_choice", noop=True)
        if MA.is_destructive(c):
            return Located(n=n, how=how, skip="destructive")
        return Located(reply=f"click({n})", n=n, how=how)
    if op == "TYPE":
        if c.role != "textbox":
            return Located(n=n, how=how, skip="type_not_textbox")
        v = value.strip()
        if not v or "\n" in v or len(v) > 200 or "{{" in v or "secret:" in v or MA.mask_secrets(v) != v \
                or MA.is_secret_field(c):
            return Located(n=n, how=how, skip="value_refused")
        return Located(reply=f"fill({n}, {q(v)})", n=n, how=how)
    if op == "SELECT":
        want = " ".join(value.split()).lower()
        canon = next((o for o in c.options if " ".join(o.split()).lower() == want), None)
        if option is not None and canon is None and " ".join(option.split()).lower() == want:
            canon = option
        if canon is None:
            return Located(n=n, how=how, skip="select_option_missing")
        return Located(reply=f"select({n}, {q(canon)})", n=n, how=how)
    return Located(skip=f"op_{op}")


# --------------------------------------------------------------- conversion --

@dataclass
class TaskResult:
    rows: List[dict] = field(default_factory=list)
    steps: int = 0
    mapped: int = 0                             # independently mappable steps (the metric)
    noop: int = 0
    reasons: Counter = field(default_factory=Counter)
    how: Counter = field(default_factory=Counter)
    stopped: str = ""
    done_row: bool = False
    seconds: float = 0.0
    page_fixes: Counter = field(default_factory=Counter)   # dialogs_behind / revealed_targets


def scroll_window(window: int, down: bool, nctl: int) -> int:
    """MicroAgent.run's window update after an accepted scroll."""
    from scaffold.agent.desktop import micro_agent as MA
    window = max(0, window + (MA.WINDOW if down else -MA.WINDOW))
    if window >= nctl:
        window = max(0, nctl - MA.WINDOW)
    return window


def scrolls_to(window: int, idx: int, nctl: int, cap: int = 64) -> List[str]:
    """The scroll replies that bring control index ``idx`` (0-based) into the window."""
    from scaffold.agent.desktop import micro_agent as MA
    out: List[str] = []
    while not (window <= idx < window + MA.WINDOW) and len(out) < cap and nctl > MA.WINDOW:
        down = idx >= window + MA.WINDOW
        out.append("scroll(down)" if down else "scroll(up)")
        window = scroll_window(window, down, nctl)
    return out


class _NoBrowser:
    page = None


class _NoModel:
    output_format = "gbnf"

    def decide(self, system: str, user: str, grammar: Optional[str] = None) -> str:  # pragma: no cover
        raise RuntimeError("not used")


def make_agent():
    """MicroAgent with the product wiring (as harness.episode builds it), used only for _prompt and
    the grounder."""
    from scaffold.agent.desktop import micro_agent as MA
    from scaffold.agent.desktop import micro_wiring as mw
    return MA.MicroAgent(_NoBrowser(), _NoModel(), store=mw.make_store("memory"),
                         grounder=mw.default_grounder("real"))


def task_id(task: dict) -> str:
    return TASK_PREFIX + str(task.get("annotation_id"))


def convert_task(task: dict, br: SnapshotBrowser, *, agent: Any = None, max_steps: int = 12,
                 after_skip: str = "stop", overlap: Optional[str] = None) -> TaskResult:
    from scaffold.agent.desktop import micro_agent as MA
    t0 = time.monotonic()
    agent = agent or make_agent()
    tid = task_id(task)
    st = MA.Subtask(name=tid, goal=task["confirmed_task"], max_steps=max_steps)
    res = TaskResult()
    history: List[str] = []
    visited: List[str] = []
    values: Dict[str, Tuple[str, str]] = {}
    window = 0
    step = 0                                     # the agent's step counter (rows so far)
    alive = True
    actions = task.get("actions") or []
    pending: Optional[dict] = None               # the row whose history line needs the page after it
    last_rendered: Optional[Rendered] = None
    last_kind = ""

    def meta(a: dict, k: int, act_show: str, how: str) -> dict:
        op = a.get("operation") or {}
        return {"src": "mind2web", "task_id": tid, "task_source": "mind2web", "family": task.get("website"),
                "kind": "act", "split": "train", "step": step, "model": "human", "action": act_show,
                "task_text": task["confirmed_task"], "domain": task.get("domain"),
                "m2w_action_uid": a.get("action_uid"), "m2w_index": k, "m2w_op": op.get("op"),
                "m2w_original_op": op.get("original_op"), "map": how, "om2w_website_overlap": overlap}

    def emit(r: Rendered, reply: str, m: dict) -> None:
        nonlocal step
        step += 1
        m["step"] = step
        page = r.page
        if not visited or visited[-1] != page.title:
            visited.append(page.title)
        injected = bool(MA.detect_injection(page))
        user = agent._prompt(st, page, step, history, window, injected, "gbnf", captured={}, visited=visited,
                             complete=False)
        act = MA.parse_text_action(reply)
        assert act.n is None or 1 <= act.n <= len(page.controls), reply
        res.rows.append(Example(system=MA.SYSTEM, user=user, reply=reply, meta=m).to_json())

    def hist_line(act_reply: str, after: Any) -> str:
        act = MA.parse_text_action(act_reply)
        lt = after.live_text()
        return (f"{step}. {act.show()} -> ok; now on \"{after.title}\"" + (f" ({lt[:160]})" if lt else ""))

    for k, a in enumerate(actions):
        res.steps += 1
        uid = str(a.get("action_uid") or "")
        try:
            r = br.render(a.get("raw_html") or "", uid=uid, keep=candidate_ids(a),
                          values=values if alive else None, title=task.get("website") or "")
        except Exception as e:                   # an unrenderable snapshot
            res.reasons[f"render_error:{type(e).__name__}"] += 1
            alive = alive and after_skip == "continue"
            pending = None
            continue
        res.page_fixes["dialogs_behind"] += r.dialogs_behind
        res.page_fixes["revealed_targets"] += r.revealed
        if window >= len(r.page.controls):       # a re-dumped page after a fill can be shorter
            window = max(0, len(r.page.controls) - MA.WINDOW)
        if pending is not None:                  # the click before landed on this page
            history.append(hist_line(pending["reply"], r.page))
            pending = None
        loc = locate(r, a)
        why = loc.skip
        g = MA.guard_page(r.page, []) if not why or loc.noop else None
        if g is not None:
            why, loc = f"guard:{g.kind}", Located(skip=f"guard:{g.kind}")
        if loc.reply is not None:
            gr = agent.grounder.resolve(r.page, loc.n or 0)
            if gr.control is None:
                why, loc = "grounding_refused", Located(skip="grounding_refused")
        moves: List[str] = []
        if loc.reply is not None:
            moves = scrolls_to(window, (loc.n or 1) - 1, len(r.page.controls)) or []
            if len(moves) > MAX_SCROLLS:
                why, loc = "too_far", Located(skip="too_far")
        if loc.reply is not None:
            res.mapped += 1
            res.how[loc.how] += 1
        elif loc.noop:
            res.noop += 1
            res.reasons["noop:" + why] += 1
        else:
            res.reasons[why] += 1
        if not alive:
            continue
        if loc.reply is None:
            if loc.noop:
                continue
            if after_skip == "stop":
                alive = False
                res.stopped = why
            else:                                # the skipped action may have changed the page
                pending, values, window = None, {}, 0
            continue
        # scroll rows the product needs before the target is in the window
        for reply in moves:
            if step >= max_steps:
                break
            emit(r, reply, meta(a, k, reply, "scroll"))
            history.append(hist_line(reply, r.page))
            window = scroll_window(window, reply == "scroll(down)", len(r.page.controls))
        if step >= max_steps:
            alive, res.stopped = False, "max_steps"
            continue
        act = MA.parse_text_action(loc.reply)
        emit(r, loc.reply, meta(a, k, act.show(), loc.how))
        last_rendered, last_kind = r, act.verb.value
        if act.verb in (MA.Verb.FILL, MA.Verb.SELECT):
            m2w_id = r.m2w_of.get(r.page._live["snap"].element(r.page._live["paths"][act.n]))
            if m2w_id is not None:
                values[m2w_id] = ("select" if act.verb is MA.Verb.SELECT else "fill",
                                  act.value if act.verb is MA.Verb.FILL else act.option or "")
            history.append(hist_line(loc.reply, r.page))     # the product keeps the same page
        else:
            pending = {"reply": loc.reply}
            values = {}
            window = 0
    # final done(): only when the page after the last action is the same snapshot (fill / select)
    if alive and last_rendered is not None and last_kind in ("fill", "select") and step < max_steps and \
            pending is None and actions:
        a = actions[-1]
        try:
            r = br.render(a.get("raw_html") or "", uid=str(a.get("action_uid") or ""), keep=candidate_ids(a),
                          values=values, title=task.get("website") or "")
            summary = ("Completed: " + " ".join(task["confirmed_task"].split()))[:200]
            reply = f"done({q(summary)})"
            emit(r, reply, meta(a, len(actions) - 1, MA.parse_text_action(reply).show(), "done"))
            res.done_row = True
        except Exception as e:
            res.reasons[f"render_error:{type(e).__name__}"] += 1
    elif alive and actions and last_rendered is not None:
        res.reasons["no_final_page"] += 1
    res.seconds = round(time.monotonic() - t0, 2)
    return res


# ---------------------------------------------------------------------- CLI --

def contamination_report(tasks: Iterable[dict], excl: Exclusion) -> Dict[str, Any]:
    """Text matches and website overlap of a task list (Mind2Web records or {confirmed_task, website}
    rows; actions are not needed), for the whole train split without downloading its HTML."""
    rep: Dict[str, Any] = {"tasks": 0, "text_matches": [], "near_misses": [], "website_overlap": Counter(),
                           "brand_overlap": Counter(), "tasks_on_overlap_websites": 0, "m2w_websites": set(),
                           "threshold": excl.threshold,
                           "jaccard": excl.jaccard, "benchmark_tasks": len(excl.bench)}
    for t in tasks:
        rep["tasks"] += 1
        text = t.get("confirmed_task") or ""
        b, ratio, jac = excl.nearest(text)
        row = {"task_id": task_id(t), "text": text, "website": t.get("website"),
               "om2w_task_id": b.task_id if b else None, "om2w_text": b.text if b else None,
               "om2w_website": b.website if b else None, "ratio": round(ratio, 3), "jaccard": round(jac, 3)}
        if excl.match(text) is not None:
            rep["text_matches"].append(row)
        elif max(ratio, jac) >= 0.5:
            rep["near_misses"].append(row)
        rep["m2w_websites"].add(t.get("website") or "")
        ov = excl.website_overlap(t.get("website") or "")
        if ov:
            rep["website_overlap"][f"{t.get('website')} -> {ov}"] += 1
            rep["tasks_on_overlap_websites"] += 1
        bo = excl.brand_overlap(t.get("website") or "")
        if bo:
            rep["brand_overlap"][f"{t.get('website')} -> {bo}"] += 1
    rep["near_misses"].sort(key=lambda x: -max(x["ratio"], x["jaccard"]))
    rep["m2w_websites"] = len(rep["m2w_websites"])
    rep["overlap_websites"] = len(rep["website_overlap"])
    rep["om2w_websites"] = len(excl.sites)
    rep["website_overlap"] = dict(rep["website_overlap"].most_common())
    rep["brand_overlap"] = dict(rep["brand_overlap"].most_common())
    return rep


def build(paths: Sequence[Path], excl: Exclusion, out: Path, *, max_tasks: int = 0, max_steps: int = 12,
          after_skip: str = "stop", drop_overlap: bool = False, chromium: Any = None,
          log=print) -> Dict[str, Any]:
    stats: Dict[str, Any] = {"inputs": [str(p) for p in paths], "tasks_in": 0, "tasks_excluded_text": 0,
                             "tasks_excluded_website": 0, "tasks_converted": 0, "tasks_with_rows": 0,
                             "steps": 0, "steps_mapped": 0, "steps_noop": 0, "rows": 0, "done_rows": 0,
                             "skip_reasons": Counter(), "map_how": Counter(), "page_fixes": Counter(),
                             "stopped_by": Counter(),
                             "excluded": [], "website_overlap": Counter(), "near_misses": [],
                             "threshold": excl.threshold, "jaccard": excl.jaccard, "max_steps": max_steps,
                             "after_skip": after_skip, "benchmark_tasks": len(excl.bench)}
    rows_per_task: List[int] = []
    prompt_chars: List[int] = []
    verbs: Counter = Counter()
    agent = make_agent()
    out.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    with SnapshotBrowser(chromium) as br, open(out, "w") as fh:
        for task in read_tasks(paths):
            if max_tasks and stats["tasks_converted"] >= max_tasks:
                break
            stats["tasks_in"] += 1
            text = task.get("confirmed_task") or ""
            b, ratio, jac = excl.nearest(text)
            hit = excl.match(text)
            if hit is not None:
                stats["tasks_excluded_text"] += 1
                stats["excluded"].append({"task_id": task_id(task), "text": text, "om2w_task_id": hit[0].task_id,
                                          "om2w_text": hit[0].text, "ratio": round(ratio, 3), "jaccard": round(jac, 3)})
                continue
            if b is not None and max(ratio, jac) >= 0.5:
                stats["near_misses"].append({"text": text, "om2w_text": b.text, "ratio": round(ratio, 3),
                                             "jaccard": round(jac, 3)})
            overlap = excl.website_overlap(task.get("website") or "")
            if overlap:
                stats["website_overlap"][f"{task.get('website')} -> {overlap}"] += 1
                if drop_overlap:
                    stats["tasks_excluded_website"] += 1
                    continue
            res = convert_task(task, br, agent=agent, max_steps=max_steps, after_skip=after_skip, overlap=overlap)
            stats["tasks_converted"] += 1
            stats["steps"] += res.steps
            stats["steps_mapped"] += res.mapped
            stats["steps_noop"] += res.noop
            stats["skip_reasons"].update(res.reasons)
            stats["map_how"].update(res.how)
            stats["page_fixes"].update(res.page_fixes)
            stats["stopped_by"][res.stopped or "end"] += 1
            stats["done_rows"] += int(res.done_row)
            for row in res.rows:
                check_row(row, excl)
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                prompt_chars.append(len(row["prompt"]))
                verbs[row["meta"]["action"].split("(")[0]] += 1
            stats["rows"] += len(res.rows)
            stats["tasks_with_rows"] += int(bool(res.rows))
            rows_per_task.append(len(res.rows))
            log(f"[{stats['tasks_converted']}] {task.get('website')}: {res.mapped}/{res.steps} mapped, "
                f"{res.noop} noop, {len(res.rows)} rows, stop={res.stopped or 'end'} ({res.seconds}s)")
        stats["blocked_requests"] = br.blocked
        stats["deep_dom_pages"] = br.flat_fallbacks
    real = stats["steps"] - stats["steps_noop"]
    stats["mapping_rate"] = round(stats["steps_mapped"] / real, 4) if real else None
    stats["rows_per_task"] = _len_stats(rows_per_task)
    stats["rows_per_task_mean"] = round(sum(rows_per_task) / len(rows_per_task), 2) if rows_per_task else 0
    stats["prompt_chars"] = _len_stats(prompt_chars)
    stats["prompt_tokens_est"] = {k: v // 4 for k, v in stats["prompt_chars"].items()}
    stats["verbs"] = dict(verbs)
    stats["near_misses"] = sorted(stats["near_misses"], key=lambda x: -max(x["ratio"], x["jaccard"]))[:20]
    stats["seconds"] = round(time.monotonic() - t0, 1)
    for k in ("skip_reasons", "map_how", "stopped_by", "website_overlap", "page_fixes"):
        stats[k] = dict(stats[k].most_common())
    return stats


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scaffold.rl.train.mind2web_data", description=__doc__.split("\n\n")[0])
    ap.add_argument("--input", action="append", type=Path, required=True,
                    help="Mind2Web train JSON (data/train/train_N.json; a truncated download is fine)")
    ap.add_argument("--exclude", action="append", type=Path, required=True,
                    help="Online-Mind2Web task file(s) (JSON list / JSONL); REQUIRED: the test set")
    ap.add_argument("--out", type=Path, required=True,
                    help="rows JSONL (dataset.py --extra-rows input); with --report-only: the report JSON")
    ap.add_argument("--report-only", action="store_true",
                    help="only the contamination report of --input (Mind2Web JSON, or JSON/JSONL rows with "
                         "confirmed_task + website: no HTML needed)")
    ap.add_argument("--max-tasks", type=int, default=0, help="stop after this many converted tasks (0 = all)")
    ap.add_argument("--max-steps", type=int, default=12, help="the agent's step cap shown in the prompt (default 12)")
    ap.add_argument("--after-skip", choices=("stop", "continue"), default="stop",
                    help="after an unmapped step: stop the task's rows (default) or continue")
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD, help="SequenceMatcher ratio to exclude")
    ap.add_argument("--jaccard", type=float, default=DEFAULT_JACCARD, help="token Jaccard to exclude")
    ap.add_argument("--exclude-overlap-websites", action="store_true",
                    help="also drop tasks on websites that are in Online-Mind2Web")
    a = ap.parse_args(argv)
    try:
        excl = Exclusion(load_benchmark(a.exclude), a.threshold, a.jaccard)
    except (OSError, ValueError) as e:
        print(f"REFUSING: cannot load the exclusion list: {e}", file=sys.stderr)
        return 2
    if a.report_only:
        tasks: List[dict] = []
        for p in a.input:
            try:
                tasks += _records(p)
            except ValueError:
                tasks += list(iter_tasks(p))
        rep = contamination_report(tasks, excl)
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(rep, indent=2) + "\n")
        keys = ("tasks", "m2w_websites", "om2w_websites", "overlap_websites", "tasks_on_overlap_websites",
                "brand_overlap")
        print(json.dumps({k: rep[k] for k in keys} | {"text_matches": len(rep["text_matches"])}, indent=1))
        for m in rep["text_matches"]:
            print(f"  EXCLUDED {m['ratio']:.2f}/{m['jaccard']:.2f}  {m['text'][:70]!r}\n"
                  f"                  ~ {m['om2w_text'][:70]!r}")
        return 0
    try:
        stats = build(a.input, excl, a.out, max_tasks=a.max_tasks, max_steps=a.max_steps, after_skip=a.after_skip,
                      drop_overlap=a.exclude_overlap_websites)
    except EvalSplitError as e:
        print(f"REFUSING: {e}", file=sys.stderr)
        a.out.unlink(missing_ok=True)
        return 2
    Path(str(a.out) + ".stats.json").write_text(json.dumps(stats, indent=2) + "\n")
    show = {k: stats[k] for k in ("tasks_in", "tasks_excluded_text", "tasks_excluded_website", "tasks_converted",
                                  "steps", "steps_noop", "steps_mapped", "mapping_rate", "rows", "done_rows",
                                  "rows_per_task_mean", "prompt_chars", "skip_reasons", "stopped_by", "map_how",
                                  "page_fixes", "deep_dom_pages",
                                  "verbs")}
    print(json.dumps(show, indent=1))
    print(f"-> {a.out} ({stats['rows']} rows), {a.out}.stats.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
