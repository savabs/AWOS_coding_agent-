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
"""
from __future__ import annotations

import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

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


def _v(field: Any) -> Any:
    return field.get("value") if isinstance(field, dict) else None


def _props(n: dict) -> Dict[str, Any]:
    return {p.get("name"): _v(p.get("value")) for p in n.get("properties") or []}


def dom_index(doc: dict) -> Dict[int, dict]:
    """backendNodeId -> {"tag", "attrs"} for every element of DOM.getDocument(depth=-1)."""
    out: Dict[int, dict] = {}

    def walk(n: dict) -> None:
        if n.get("nodeType") == 1:
            a = n.get("attributes") or []
            out[n["backendNodeId"]] = {"tag": str(n.get("localName") or n.get("nodeName") or "").lower(),
                                       "attrs": {a[i]: a[i + 1] for i in range(0, len(a) - 1, 2)}}
        for key in ("children", "shadowRoots"):
            for c in n.get(key) or []:
                walk(c)
        if n.get("contentDocument"):
            pass                                    # iframes: not followed (one document per page)
    walk(doc.get("root") or doc)
    return out


def _clip(s: Any, limit: int = 512) -> Optional[str]:
    if not isinstance(s, str):
        return None
    s = s.strip()
    return s[:limit] if s else None


class _Builder:
    def __init__(self, ax_nodes: List[dict], dom: Dict[int, dict], page_url: str):
        self.by_id = {n["nodeId"]: n for n in ax_nodes}
        self.dom = dom
        self.url = page_url
        self.handles: Dict[Path, Any] = {}
        self.count = 0
        self.focused: Optional[Path] = None

    def el(self, n: dict) -> dict:
        return self.dom.get(n.get("backendDOMNodeId"), {"tag": "", "attrs": {}})

    def kids(self, n: dict) -> List[dict]:
        """Unignored children in order, splicing through ignored nodes."""
        out: List[dict] = []
        for cid in n.get("childIds") or []:
            c = self.by_id.get(cid)
            if c is None:
                continue
            role = _v(c.get("role"))
            if role == "InlineTextBox":
                continue
            if c.get("ignored"):
                out.extend(self.kids(c))
            else:
                out.append(c)
        return out

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

        self.handles[path] = n.get("backendDOMNodeId")
        self.count += 1
        if descend:
            kids_out: List[dict] = []
            for c in self._flat_children(n, role):
                k = self.node(c, path + (len(kids_out),))
                if k is not None:
                    kids_out.append(k)
            if kids_out:
                d["children"] = kids_out
        elif "children" in d:
            for i, c in enumerate(d["children"]):          # the select's synthetic menu
                self._index(c, path + (i,))
        return d

    def _flat_children(self, n: dict, role: str) -> List[dict]:
        kids = self.kids(n)
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


def build_tree(ax_nodes: List[dict], dom_doc: Optional[dict], page_url: str, title: str = ""
               ) -> Tuple[Optional[dict], Dict[Path, Any], int, Optional[Path]]:
    """-> (window tree, {path: backendDOMNodeId}, node count, focused path)."""
    dom = dom_index(dom_doc) if dom_doc else {}
    b = _Builder(ax_nodes, dom, page_url)
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
                      app: Optional[dict] = None):
    """An observe.Snapshot over the Chromium page, ready for driver.read_web / to_micro_page."""
    from scaffold.agent.desktop.live.observe import Snapshot
    nodes = ax.get("nodes") if isinstance(ax, dict) else ax
    tree, handles, count, focused = build_tree(nodes or [], dom_doc, page_url, title)
    if tree is None:
        return Snapshot(None, False, dict(app or {}), reason="no_window")
    return Snapshot(tree, False, dict(app or {"name": "Chromium", "bundle_id": "headless"}),
                    tree.get("title") or "", page_url, handles, count, 0.0, "", focused, [WEB_PATH])
