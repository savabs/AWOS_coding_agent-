"""
axmap.py — Chromium accessibility tree (CDP) -> the macOS AX dict tree Safari/WebKit gives
observe.py, so live/driver.read_web and to_micro_page run UNCHANGED on headless Chromium.

Pure functions, no browser: input is the JSON of CDP ``Accessibility.getFullAXTree`` plus
``DOM.getDocument(depth=-1)``; output is an observe.Snapshot whose tree has the keys
observe.make_node produces ("role" AX*, "subrole", "title" = accessible name, "value",
"placeholder", "identifier" = DOM id, "url", "secure", "selected", "focused", "children")
and whose ``handles`` map every tree path to the node's backendDOMNodeId (what the
ChromiumBackend acts on).

Role mapping (Chromium role -> WebKit/Safari AX role [+ subrole]):

    RootWebArea         AXWebArea (url, title)       StaticText      AXStaticText (value = text)
    link                AXLink (+ url from href)     button          AXButton
    textbox             AXTextField | AXTextArea (multiline) | AXTextField+AXSecureTextField
                        (DOM input type=password: secure, value never copied)
    searchbox           AXTextField / AXSearchField  spinbutton (input type=number)  AXTextField
    combobox <select>   AXPopUpButton (value = selected text) > AXMenu > AXMenuItem per option
    combobox (ARIA)     AXComboBox                   listbox         AXList (options AXStaticText)
    checkbox            AXCheckBox (value 0/1)       switch          AXCheckBox / AXSwitch
    radiogroup          AXRadioGroup                 radio           AXRadioButton (value 0/1)
    tablist / tab       AXTabGroup / AXRadioButton+AXTabButton (WebKit: tabs are radio buttons)
    AXButton, AXCheckBox, AXRadioButton, AXImage, AXSlider, ... are leaves (as in WebKit)
    heading             AXHeading (value = level)    table/grid      AXTable > AXRow > AXCell
                        (rowgroups flattened, caption dropped: its text is the table's name)
    dialog              AXGroup / AXApplicationDialog            alertdialog  AXApplicationAlertDialog
    alert / status      AXGroup / AXApplicationAlert | AXApplicationStatus
    log / timer / marquee  AXGroup / AXApplicationLog | AXApplicationTimer | AXApplicationMarquee
    form / region / navigation / main / banner / contentinfo / complementary / search
                        AXGroup / AXLandmark{Form,Region,Navigation,Main,Banner,ContentInfo,
                        Complementary,Search}
    menu/menubar/menuitem*  AXMenu / AXMenuBar / AXMenuItem       image  AXImage
    list / listitem     AXList / AXGroup             ListMarker      AXListMarker
    slider / progressbar   AXSlider / AXProgressIndicator         Date / DateTime / InputTime  AXDateTimeArea
    everything else (generic, paragraph, LabelText, Legend, emphasis, code, ...)  AXGroup

Ignored Chromium nodes are dropped and their unignored descendants spliced into the parent
(WebKit likewise flattens presentational wrappers); InlineTextBox nodes are dropped; text
inputs keep no children (their inner editor is not an AX child in WebKit either).

Clickable non-controls (the extra keys driver.read_web turns into controls; absent elsewhere):
    "press"   the control kind a node acts as when clicked: ARIA option (outside <select>) ->
              "option", menuitem / menuitemradio -> "menuitem", menuitemcheckbox -> "checkbox",
              treeitem -> "treeitem", gridcell that is focusable / selectable / clickable ->
              "gridcell", and a generic / listitem / cell / image ... element that Chromium marks
              clickable (DOMSnapshot isClickable: a click / mouse listener) or that is the topmost
              element of a cursor:pointer run -> "clickable" (``clickable_ids``).
    "hint"    for a control with no accessible name: a name derived from the DOM (title, alt,
              aria-description, tooltip data attributes, icon class names such as fa-search /
              icon-cart, an <svg><use href="#icon-x">, an image file name), read_web's first
              choice for the "(unnamed)" name.
Very deep pages: below depth DEEP_PATH, plain wrapper groups (no name, id, focus or click) are
spliced into their parent, so the tree (and read_web's recursion) stays shallow; ``flat_document``
turns a DOMSnapshot into the DOM.getDocument shape dom_index reads (DOM.getDocument itself fails once
CDP's CBOR encoder hits its nesting limit).
"""
from __future__ import annotations

import re
import urllib.parse
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

Path = Tuple[int, ...]

WEB_PATH: Path = (0, 0, 0)          # window > scroll area > web area (as in Safari)

_GROUP_SUBROLE = {
    "dialog": "AXApplicationDialog", "alertdialog": "AXApplicationAlertDialog",
    "alert": "AXApplicationAlert", "status": "AXApplicationStatus", "log": "AXApplicationLog",
    "timer": "AXApplicationTimer", "marquee": "AXApplicationMarquee",
    "form": "AXLandmarkForm", "region": "AXLandmarkRegion", "navigation": "AXLandmarkNavigation",
    "main": "AXLandmarkMain", "banner": "AXLandmarkBanner", "contentinfo": "AXLandmarkContentInfo",
    "complementary": "AXLandmarkComplementary", "search": "AXLandmarkSearch",
    "tabpanel": "AXTabPanel",
}
_SIMPLE = {
    "button": "AXButton", "heading": "AXHeading", "radiogroup": "AXRadioGroup", "radio": "AXRadioButton",
    "checkbox": "AXCheckBox", "list": "AXList", "listbox": "AXList", "ListMarker": "AXListMarker",
    "image": "AXImage", "img": "AXImage", "slider": "AXSlider", "progressbar": "AXProgressIndicator",
    "menu": "AXMenu", "menubar": "AXMenuBar", "menuitem": "AXMenuItem", "menuitemcheckbox": "AXMenuItem",
    "menuitemradio": "AXMenuItem", "tablist": "AXTabGroup", "row": "AXRow", "cell": "AXCell",
    "gridcell": "AXCell", "columnheader": "AXCell", "rowheader": "AXCell", "table": "AXTable",
    "grid": "AXTable", "treegrid": "AXTable", "Date": "AXDateTimeArea", "DateTime": "AXDateTimeArea",
    "InputTime": "AXDateTimeArea", "separator": "AXSplitter", "scrollbar": "AXScrollBar",
    "tree": "AXOutline", "treeitem": "AXRow", "toolbar": "AXToolbar",
}
_TEXT_ENTRY = {"textbox", "searchbox", "spinbutton"}
#: WebKit roles that cannot have AX children (WebKit canHaveChildren() is false for them): their
#: text is their title only. Live evidence: Safari flattens a dialog's text WITHOUT its button
#: labels (commit e0fa180), i.e. AXButton exposes no AXStaticText children.
LEAF_ROLES = {"AXButton", "AXCheckBox", "AXRadioButton", "AXImage", "AXProgressIndicator", "AXSlider",
              "AXSplitter", "AXMenuButton", "AXStaticText"}
_TRUE = ("true", True, "mixed")
#: ARIA roles that act as click targets -> the control kind read_web gives them
_PRESS_ROLE = {"option": "option", "menuitem": "menuitem", "menuitemradio": "menuitem",
               "menuitemcheckbox": "checkbox", "treeitem": "treeitem"}
#: Chromium roles a JS-clickable element may have and still not be a control of its own
_PRESS_GENERIC = {"generic", "none", "presentation", "listitem", "paragraph", "image", "img", "cell", "row",
                  "article", "figure", "group", "Section", "term", "definition", "DescriptionListTerm",
                  "DescriptionListDetail", "time", "emphasis", "strong", "mark", "code", "blockquote", "Abbr",
                  "ListMarker", "SvgRoot", "graphics-symbol", "graphics-document", "Canvas", "Pre", "Ruby"}
#: below this tree depth, plain wrapper groups are spliced into their parent (very deep pages)
DEEP_PATH = 96
_ICON_CLASS = [re.compile(r"(?:^|\s)(?:fa[srbld]?|bi|mdi|ti|ion|icons?|glyphicon|la|lni|feather|ico|gg|ri|uil|"
                          r"octicon|material-icons?|ms|svg-icon)[-_]([a-z][a-z0-9-]{1,30})(?=\s|$)", re.I),
               re.compile(r"(?:^|\s)([a-z][a-z0-9]{1,20})[-_]icon(?=\s|$)", re.I),
               re.compile(r"(?:^|\s)btn-(close)(?=\s|$)", re.I)]
_ICON_NOISE = {"fw", "lg", "xs", "sm", "md", "xl", "2x", "3x", "solid", "regular", "light", "brands", "inverse",
               "spin", "pulse", "border", "stack", "ul", "li", "fixed", "wrapper", "container", "icon", "svg",
               "outline", "outlined", "filled", "round", "rounded", "sharp", "small", "large", "white", "black",
               "primary", "secondary", "default", "image", "img", "inline", "block", "button", "btn", "base"}
_HINT_ATTRS = ("title", "alt", "aria-description", "data-tooltip", "data-title", "data-original-title",
               "data-tip", "data-label", "data-name", "tooltip", "label")


def _v(field: Any) -> Any:
    return field.get("value") if isinstance(field, dict) else None


def _props(n: dict) -> Dict[str, Any]:
    return {p.get("name"): _v(p.get("value")) for p in n.get("properties") or []}


def dom_index(doc: dict) -> Dict[int, dict]:
    """backendNodeId -> {"tag", "attrs"} for every element of DOM.getDocument(depth=-1)."""
    out: Dict[int, dict] = {}
    stack = [doc.get("root") or doc]                # iterative: real pages nest past the recursion limit
    up: Dict[int, Optional[int]] = {}
    while stack:
        n = stack.pop()
        bid = n.get("backendNodeId")
        if n.get("nodeType") == 1:
            a = n.get("attributes") or []
            out[bid] = {"tag": str(n.get("localName") or n.get("nodeName") or "").lower(),
                        "attrs": {a[i]: a[i + 1] for i in range(0, len(a) - 1, 2)}, "kids": []}
            par = n.get("_parent", up.get(bid))          # flat_document carries the parent itself
            if par in out:
                out[par]["kids"].append(bid)
        for key in ("shadowRoots", "children"):     # iframes (contentDocument): not followed
            kids = n.get(key) or []
            for c in kids:
                up[c.get("backendNodeId")] = bid if n.get("nodeType") == 1 else up.get(bid)
            stack.extend(reversed(kids))
    return out


def flat_document(snapshot: dict) -> dict:
    """DOMSnapshot.captureSnapshot -> a DOM.getDocument-shaped dict whose root lists every element as a
    direct child (with ``_parent``), plus ``maxDepth`` (element nesting). dom_index only reads
    backendNodeId -> tag + attributes, so the observation is the same as from DOM.getDocument(depth=-1),
    which fails on deeply nested pages (CDP's CBOR encoder has a nesting limit: "CBOR: stack limit
    exceeded")."""
    strings = snapshot.get("strings") or []
    s = lambda i: strings[i] if isinstance(i, int) and 0 <= i < len(strings) else ""  # noqa: E731
    d = (snapshot.get("documents") or [{}])[0]
    nodes = d.get("nodes") or {}
    par = nodes.get("parentIndex") or []
    types = nodes.get("nodeType") or []
    names = nodes.get("nodeName") or []
    bids = nodes.get("backendNodeId") or []
    attrs = nodes.get("attributes") or []
    elem_bid: Dict[int, int] = {}
    depth: Dict[int, int] = {}
    out: List[dict] = []
    for i, t in enumerate(types):
        if t != 1 or s(names[i]).startswith("::"):   # pseudo elements (::marker, ::before) are not DOM
            continue
        elem_bid[i] = bids[i]
        j = par[i] if i < len(par) else -1
        while j >= 0 and j not in elem_bid:          # parents come before children in the snapshot
            j = par[j] if j < len(par) else -1
        depth[i] = depth[j] + 1 if j >= 0 else 0
        out.append({"nodeType": 1, "backendNodeId": bids[i], "localName": s(names[i]).lower(),
                    "attributes": [s(x) for x in (attrs[i] if i < len(attrs) else [])],
                    "_parent": elem_bid.get(j)})
    return {"root": {"nodeType": 9, "children": out}, "maxDepth": max(depth.values(), default=0)}


def clickable_ids(snapshot: Optional[dict]) -> Set[int]:
    """backendNodeIds of elements a click acts on although their role is not a control, from
    DOMSnapshot.captureSnapshot(computedStyles=["cursor"]): Chromium's isClickable (a click / mouse
    listener or an inline handler; also true for links and form controls, which already are controls)
    and the topmost element of each cursor:pointer run (cursor is inherited, so only the element that
    sets it counts; frameworks that delegate events to the root leave cursor:pointer as the only
    trace). <html> / <body> never count."""
    if not snapshot:
        return set()
    strings = snapshot.get("strings") or []
    d = (snapshot.get("documents") or [{}])[0]
    nodes = d.get("nodes") or {}
    par = nodes.get("parentIndex") or []
    types = nodes.get("nodeType") or []
    names = nodes.get("nodeName") or []
    bids = nodes.get("backendNodeId") or []
    out: Set[int] = set()
    skip = {"HTML", "BODY", "#document"}

    def tag(i: int) -> str:
        k = names[i] if i < len(names) else -1
        return strings[k] if isinstance(k, int) and 0 <= k < len(strings) else ""
    for i in (nodes.get("isClickable") or {}).get("index") or []:
        if 0 <= i < len(types) and types[i] == 1 and tag(i).upper() not in skip:
            out.add(bids[i])
    lay = d.get("layout") or {}
    pointer: Dict[int, bool] = {}
    for k, ni in enumerate(lay.get("nodeIndex") or []):
        st = (lay.get("styles") or [])[k] if k < len(lay.get("styles") or []) else []
        if 0 <= ni < len(types) and types[ni] == 1:
            v = strings[st[0]] if st and isinstance(st[0], int) and 0 <= st[0] < len(strings) else ""
            pointer[ni] = v == "pointer"
    for ni, on in pointer.items():
        if not on or tag(ni).upper() in skip:
            continue
        j = par[ni] if ni < len(par) else -1
        while j >= 0 and j not in pointer:          # the nearest ancestor with a layout box
            j = par[j] if j < len(par) else -1
        if j < 0 or not pointer.get(j):
            out.add(bids[ni])
    return out


def _clip(s: Any, limit: int = 512) -> Optional[str]:
    if not isinstance(s, str):
        return None
    s = s.strip()
    return s[:limit] if s else None


def _words(s: str) -> str:
    return " ".join(re.sub(r"[-_]+", " ", s).split())


def icon_words(cls: str) -> str:
    """'fa fa-shopping-cart' -> 'shopping cart'; '' when the class names no icon."""
    for rx in _ICON_CLASS:
        for m in rx.finditer(cls or ""):
            w = _words(m.group(1).lower())
            if w and w.split()[0] not in _ICON_NOISE and not re.fullmatch(r"[\d ]+x?", w):
                return w
    return ""


def _file_word(url: str) -> str:
    """'/img/icons/cart.svg?v=3' -> 'cart'; '' for data: URLs and hash-like names."""
    if not url or url.startswith("data:"):
        return ""
    base = urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1]
    base = re.sub(r"\.(svg|png|jpe?g|gif|webp|ico|avif)$", "", base, flags=re.I)
    base = re.sub(r"[@.]\d+x$", "", base)
    w = _words(urllib.parse.unquote(base))
    if not w or len(w) > 30 or sum(ch.isdigit() for ch in w) >= 4 or not re.search(r"[a-z]{3}", w, re.I):
        return ""
    return w


class _Builder:
    def __init__(self, ax_nodes: List[dict], dom: Dict[int, dict], page_url: str,
                 clickable: Optional[Iterable[int]] = None):
        self.by_id = {n["nodeId"]: n for n in ax_nodes}
        self.dom = dom
        self.url = page_url
        self.clickable: Set[int] = set(clickable or ())
        self.handles: Dict[Path, Any] = {}
        self.count = 0
        self.focused: Optional[Path] = None

    def el(self, n: dict) -> dict:
        return self.dom.get(n.get("backendDOMNodeId"), {"tag": "", "attrs": {}})

    def kids(self, n: dict) -> List[dict]:
        """Unignored children in order, splicing through ignored nodes (iteratively)."""
        out: List[dict] = []
        stack = list(reversed(n.get("childIds") or []))
        while stack:
            c = self.by_id.get(stack.pop())
            if c is None:
                continue
            role = _v(c.get("role"))
            if role == "InlineTextBox":
                continue
            if c.get("ignored") and not self._wanted_ignored(c):
                stack.extend(reversed(c.get("childIds") or []))
            else:
                out.append(c)
        return out

    def _wanted_ignored(self, c: dict) -> bool:
        """An ignored wrapper Chromium found uninteresting, but which is a click target (a pointer
        <span> with only text inside): kept as a generic node so it can become a control."""
        if c.get("backendDOMNodeId") not in self.clickable:
            return False
        why = {str(r.get("name")) for r in c.get("ignoredReasons") or []}
        return bool(why) and why <= {"uninteresting"} and bool(c.get("childIds"))

    def _plain(self, c: dict) -> bool:
        """A wrapper that carries nothing of its own (spliced away on very deep pages)."""
        if _v(c.get("role")) not in ("generic", "none", "presentation") or _clip(_v(c.get("name"))):
            return False
        if c.get("backendDOMNodeId") in self.clickable or self.el(c)["attrs"].get("id"):
            return False
        pr = _props(c)
        return not (pr.get("focusable") or pr.get("focused"))

    def _subtree(self, n: dict, limit: int = 200) -> Iterable[dict]:
        """DOM elements below ``n``'s element, breadth-first, at most ``limit`` (icons are <i> / <svg>
        elements that are often not in the AX tree at all)."""
        q = list(self.el(n).get("kids") or [])
        seen = 0
        while q and seen < limit:
            el = self.dom.get(q.pop(0))
            if el is None:
                continue
            seen += 1
            yield el
            q.extend(el.get("kids") or [])

    def hint(self, n: dict) -> str:
        """A name for a control with no accessible name, from its DOM (see the module docstring)."""
        el = self.el(n)
        attrs = el["attrs"]
        for a in _HINT_ATTRS:
            v = " ".join(str(attrs.get(a) or "").split())
            if v:
                return v[:80]
        w = icon_words(attrs.get("class", ""))
        if w:
            return w
        if el["tag"] == "input" and attrs.get("type", "").lower() == "image":
            w = _file_word(attrs.get("src", ""))
            if w:
                return w
        for cel in self._subtree(n):
            a = cel["attrs"]
            for k in ("title", "alt", "aria-label"):
                v = " ".join(str(a.get(k) or "").split())
                if v:
                    return v[:80]
            w = icon_words(a.get("class", ""))
            if w:
                return w
            if cel["tag"] == "use":
                ref = a.get("href") or a.get("xlink:href") or ""
                frag = ref.rsplit("#", 1)[-1] if "#" in ref else ""
                w = icon_words("icon-" + re.sub(r"^(icons?|svg|ico)[-_]", "", frag)) if frag else ""
                if w:
                    return w
            if cel["tag"] == "img":
                w = _file_word(a.get("src", ""))
                if w:
                    return w
        return ""

    def press(self, n: dict, role: str, props: Dict[str, Any], d: dict) -> Optional[str]:
        """The control kind a non-control node acts as when clicked, or None."""
        if props.get("disabled") is True:
            return None
        if role in _PRESS_ROLE:
            return _PRESS_ROLE[role]
        clickable = n.get("backendDOMNodeId") in self.clickable
        if role == "gridcell":
            if clickable or props.get("focusable") or props.get("selected") is not None:
                return "gridcell"
            return None
        if clickable and role in _PRESS_GENERIC and d["role"] in ("AXGroup", "AXImage", "AXCell", "AXRow",
                                                                  "AXListMarker"):
            return "clickable"
        return None

    # -- one node -------------------------------------------------------------
    def node(self, n: dict, path: Path) -> Optional[dict]:
        role = _v(n.get("role")) or ""
        name = _clip(_v(n.get("name")))
        props = _props(n)
        el = self.el(n)
        tag, attrs = el["tag"], el["attrs"]
        d: Dict[str, Any] = {}
        descend = True

        if role == "RootWebArea":
            d["role"] = "AXWebArea"
            if name:
                d["title"] = name
            d["url"] = self.url
        elif role == "StaticText":
            d["role"] = "AXStaticText"
            if name:
                d["value"] = name
            descend = False
        elif role == "link":
            d["role"] = "AXLink"
            href = attrs.get("href")
            url = props.get("url") or (urllib.parse.urljoin(self.url, href) if href is not None else "")
            if url:
                d["url"] = url
        elif role in _TEXT_ENTRY:
            secure = tag == "input" and attrs.get("type", "").lower() == "password"
            multiline = bool(props.get("multiline")) or tag == "textarea"
            d["role"] = "AXTextArea" if multiline else "AXTextField"
            if secure:
                d["subrole"] = "AXSecureTextField"
                d["secure"] = True
            elif role == "searchbox" or attrs.get("type", "").lower() == "search":
                d["subrole"] = "AXSearchField"
            if not secure:
                v = _v(n.get("value"))
                d["value"] = v if isinstance(v, str) else ("" if v is None else str(v))
            descend = False
        elif role == "combobox":
            if tag == "select":
                d["role"] = "AXPopUpButton"
                v = _v(n.get("value"))
                if isinstance(v, str) and v:
                    d["value"] = v
                d["children"] = [self._menu(n)]
                descend = False
            else:
                d["role"] = "AXComboBox"
                v = _v(n.get("value"))
                d["value"] = v if isinstance(v, str) else ""
                descend = False
        elif role == "switch":
            d["role"], d["subrole"] = "AXCheckBox", "AXSwitch"
        elif role == "tab":
            d["role"], d["subrole"] = "AXRadioButton", "AXTabButton"
        elif role == "option":
            d["role"] = "AXStaticText"
            if name:
                d["value"] = name
            descend = False
        elif role == "caption":
            return None                             # its text is the table's name
        elif role in _GROUP_SUBROLE:
            d["role"], d["subrole"] = "AXGroup", _GROUP_SUBROLE[role]
        elif role in _SIMPLE:
            d["role"] = _SIMPLE[role]
        else:
            d["role"] = "AXGroup"

        if d["role"] in LEAF_ROLES:
            descend = False
        if name and "title" not in d and role not in ("StaticText", "option"):
            d["title"] = name
        if role == "heading" and props.get("level") is not None:
            d["value"] = props["level"]
        if role in ("checkbox", "switch", "radio", "tab", "menuitemcheckbox", "menuitemradio"):
            c = props.get("checked")
            sel = props.get("selected")
            on = c in _TRUE or (role == "tab" and sel in _TRUE)
            d["value"] = 2 if c == "mixed" else int(bool(on))
        if role in ("slider", "progressbar", "Date", "DateTime", "InputTime"):
            v = _v(n.get("value"))
            if v not in (None, ""):
                d["value"] = v
        ph = _clip(attrs.get("placeholder"))
        if ph and d["role"] in ("AXTextField", "AXTextArea", "AXComboBox"):
            d["placeholder"] = ph
        ident = _clip(attrs.get("id"))
        if ident:
            d["identifier"] = ident
        if props.get("disabled") is True:
            d["enabled"] = False
        if props.get("focused") is True and role != "RootWebArea":
            d["focused"] = True
            if self.focused is None:
                self.focused = path
        if props.get("selected") is True and role not in ("option",):
            d["selected"] = True
        kind = self.press(n, role, props, d)
        if kind:
            d["press"] = kind
            if kind == "option" and props.get("selected") is True:
                d["selected"] = True
        if not name and (kind or d["role"] in ("AXLink", "AXButton", "AXCheckBox", "AXRadioButton")):
            h = self.hint(n)
            if h:
                d["hint"] = h

        self.handles[path] = n.get("backendDOMNodeId")
        self.count += 1
        if descend:
            kids_out: List[dict] = []
            for c in self._flat_children(n, role, len(path)):
                k = self.node(c, path + (len(kids_out),))
                if k is not None:
                    kids_out.append(k)
            if kids_out:
                d["children"] = kids_out
        elif "children" in d:
            for i, c in enumerate(d["children"]):          # the select's synthetic menu
                self._index(c, path + (i,))
        return d

    def _flat_children(self, n: dict, role: str, depth: int = 0) -> List[dict]:
        kids = self.kids(n)
        if depth >= DEEP_PATH and any(self._plain(c) for c in kids):
            flat: List[dict] = []                          # very deep page: splice plain wrappers
            stack = list(reversed(kids))
            while stack:
                c = stack.pop()
                if self._plain(c):
                    stack.extend(reversed(self.kids(c)))
                else:
                    flat.append(c)
            kids = flat
        if role in ("table", "grid", "treegrid"):          # WebKit: rows are the table's children
            out: List[dict] = []
            for c in kids:
                if _v(c.get("role")) == "rowgroup":
                    out.extend(self._flat_children(c, "table"))
                else:
                    out.append(c)
            return out
        return kids

    def _menu(self, sel: dict) -> dict:
        items: List[dict] = []

        def walk(x: dict) -> None:
            for c in self.kids(x):
                if _v(c.get("role")) == "option":
                    it: Dict[str, Any] = {"role": "AXMenuItem"}
                    nm = _clip(_v(c.get("name")))
                    if nm:
                        it["title"] = nm
                    if _props(c).get("selected") is True:
                        it["selected"] = True
                    it["_backend"] = c.get("backendDOMNodeId")
                    items.append(it)
                else:
                    walk(c)
        walk(sel)
        menu: Dict[str, Any] = {"role": "AXMenu"}
        if items:
            menu["children"] = items
        return menu

    def _index(self, d: dict, path: Path) -> None:
        self.handles[path] = d.pop("_backend", None)
        self.count += 1
        for i, c in enumerate(d.get("children") or []):
            self._index(c, path + (i,))


def build_tree(ax_nodes: List[dict], dom_doc: Optional[dict], page_url: str, title: str = "",
               clickable: Optional[Iterable[int]] = None
               ) -> Tuple[Optional[dict], Dict[Path, Any], int, Optional[Path]]:
    """-> (window tree, {path: backendDOMNodeId}, node count, focused path). ``clickable``:
    backendNodeIds of click targets that are not controls by role (clickable_ids)."""
    dom = dom_index(dom_doc) if dom_doc else {}
    b = _Builder(ax_nodes, dom, page_url, clickable)
    root = next((n for n in ax_nodes if _v(n.get("role")) == "RootWebArea" and not n.get("parentId")), None)
    if root is None:
        root = next((n for n in ax_nodes if _v(n.get("role")) == "RootWebArea"), None)
    if root is None:
        return None, {}, 0, None
    web = b.node(root, WEB_PATH)
    win_title = title or (web or {}).get("title") or page_url
    tree = {"role": "AXWindow", "title": win_title,
            "children": [{"role": "AXScrollArea", "children": [web]}]}
    b.handles[(0,)] = None
    b.handles[(0, 0)] = None
    return tree, b.handles, b.count + 2, b.focused


def snapshot_from_cdp(ax: dict, dom_doc: Optional[dict], page_url: str, title: str = "",
                      app: Optional[dict] = None, clickable: Optional[Iterable[int]] = None):
    """An observe.Snapshot over the Chromium page, ready for driver.read_web / to_micro_page."""
    from scaffold.agent.desktop.live.observe import Snapshot
    nodes = ax.get("nodes") if isinstance(ax, dict) else ax
    tree, handles, count, focused = build_tree(nodes or [], dom_doc, page_url, title, clickable)
    if tree is None:
        return Snapshot(None, False, dict(app or {}), reason="no_window")
    return Snapshot(tree, False, dict(app or {"name": "Chromium", "bundle_id": "headless"}),
                    tree.get("title") or "", page_url, handles, count, 0.0, "", focused, [WEB_PATH])
