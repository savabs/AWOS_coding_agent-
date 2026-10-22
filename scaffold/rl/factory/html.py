"""
html.py — the HTML builder every factory family renders through.

One builder (``B``) emits all controls, so the ARIA-quality knob is applied
uniformly across families:

    good              <button>Save</button>, <label for> on every input
    missing_labels    inputs carry no <label>; their accessible name comes from the
                      placeholder (selects / checkboxes / radio groups: title)
    div_buttons       buttons are <div role="button" tabindex="0" data-submit=..>
    duplicated_names  buttons use their short generic name ("Save", "Open") and the
                      shell adds a distractor region with a same-named button, so
                      only the enclosing region (Control.context) disambiguates
    icon_only         buttons that have an icon render the glyph (aria-hidden) with
                      the name only in ``title`` (no aria-label)

Pages are server-rendered. The whole page sits inside ONE <form method=post>;
every button is a submit button (name="__ctl" value=<ctl id>), div-buttons and
auto-saving fields submit through a tiny vanilla-JS runtime (``JS``) that puts
the ctl id in the hidden ``__js`` field. Links are plain GET links. There is no
external resource of any kind (no CDN, no fonts, no images).

Never use <input type=number> (Chromium exposes it as a spin button, which the
product's AX reader does not treat as a text field): numeric fields are text
inputs with inputmode=numeric.
"""
from __future__ import annotations

import html as _html
from contextlib import contextmanager
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

ARIA_LEVELS = ("good", "missing_labels", "div_buttons", "duplicated_names", "icon_only")


def esc(s) -> str:
    return _html.escape("" if s is None else str(s), quote=True)


def _attrs(d: Dict[str, object]) -> str:
    out = []
    for k, v in d.items():
        if v is None or v is False:
            continue
        k = "class" if k == "cls" else k.rstrip("_").replace("_", "-")
        out.append(f" {k}" if v is True else f' {k}="{esc(v)}"')
    return "".join(out)


# --------------------------------------------------------------------- themes --

THEMES: Dict[str, Dict[str, str]] = {
    # train themes
    "slate":  dict(bg="#f4f6f8", fg="#1d2733", panel="#ffffff", accent="#2f6fdb", border="#d5dbe3",
                   font="-apple-system, Helvetica, Arial, sans-serif", radius="6px", pad="10px", danger="#c62828"),
    "paper":  dict(bg="#fbf8f1", fg="#2b2620", panel="#fffdf8", accent="#8a5a14", border="#e2d8c3",
                   font="Georgia, 'Times New Roman', serif", radius="2px", pad="12px", danger="#a1261f"),
    "ocean":  dict(bg="#e8f3f8", fg="#0d2b3a", panel="#f7fcff", accent="#00838f", border="#b8d8e4",
                   font="Verdana, Geneva, sans-serif", radius="10px", pad="9px", danger="#b71c1c"),
    "forest": dict(bg="#eef3ec", fg="#1f2b1c", panel="#fbfdfa", accent="#2e7d32", border="#c9d8c4",
                   font="'Trebuchet MS', sans-serif", radius="4px", pad="11px", danger="#c62828"),
    "sunset": dict(bg="#fff4ec", fg="#3a1d10", panel="#ffffff", accent="#e65100", border="#f1d2bd",
                   font="Tahoma, sans-serif", radius="14px", pad="8px", danger="#b00020"),
    "mono":   dict(bg="#ffffff", fg="#111111", panel="#fafafa", accent="#333333", border="#cccccc",
                   font="Menlo, Consolas, monospace", radius="0px", pad="8px", danger="#990000"),
    # out-of-domain themes (never used by train tasks)
    "neon":   dict(bg="#0b0f1a", fg="#e8f0ff", panel="#141b2d", accent="#ff2bd6", border="#2a3550",
                   font="'Courier New', monospace", radius="18px", pad="14px", danger="#ff5252"),
    "retro":  dict(bg="#f0e6d2", fg="#2d1e12", panel="#fff8e8", accent="#6b4226", border="#8c6d4f",
                   font="'Palatino Linotype', Palatino, serif", radius="0px", pad="6px", danger="#8b0000"),
}
TRAIN_THEMES = ("slate", "paper", "ocean", "forest", "sunset", "mono")
OOD_THEMES = ("neon", "retro")
LAYOUTS = ("sidebar", "topnav", "stacked")


def css(theme: str) -> str:
    t = THEMES[theme]
    upper = "text-transform:uppercase;letter-spacing:.04em;" if theme == "retro" else ""
    return f"""
body{{margin:0;background:{t['bg']};color:{t['fg']};font-family:{t['font']};font-size:15px}}
header{{padding:{t['pad']} 16px;border-bottom:1px solid {t['border']};background:{t['panel']}}}
header .brand{{font-weight:bold;{upper}}}
.shell{{display:flex;gap:16px;padding:16px}} .shell.topnav,.shell.stacked{{flex-direction:column}}
nav ul{{list-style:none;margin:0;padding:0}} .shell.sidebar nav{{min-width:170px}}
.shell.topnav nav ul{{display:flex;gap:14px}} nav a{{color:{t['accent']}}} nav a[aria-current]{{font-weight:bold}}
main{{flex:1;background:{t['panel']};border:1px solid {t['border']};border-radius:{t['radius']};padding:{t['pad']} 18px}}
aside{{min-width:180px;font-size:13px}}
.btn{{display:inline-block;cursor:pointer;background:{t['accent']};color:#fff;border:0;border-radius:{t['radius']};
 padding:6px 12px;margin:3px 4px 3px 0;font:inherit}}
.btn.secondary{{background:transparent;color:{t['accent']};border:1px solid {t['accent']}}}
.btn.danger{{background:{t['danger']}}} .btn.icon{{padding:4px 8px}}
.field{{margin:8px 0}} .field label,.lbl{{display:block;font-size:13px;margin-bottom:2px}}
input[type=text],input[type=password],input[type=email],input[type=search],select,textarea{{font:inherit;padding:5px 7px;
 border:1px solid {t['border']};border-radius:{t['radius']};min-width:220px;background:#fff;color:#111}}
.hint{{display:block;font-size:12px;opacity:.75}} .err{{color:{t['danger']};font-size:13px}}
table{{border-collapse:collapse;width:100%;margin:8px 0}} th,td{{border-bottom:1px solid {t['border']};padding:5px 7px;text-align:left}}
caption{{text-align:left;font-weight:bold;padding:4px 0}}
.flash{{padding:8px 12px;border-radius:{t['radius']};margin:8px 0;border:1px solid {t['accent']}}}
.flash.alert{{border-color:{t['danger']};color:{t['danger']}}}
.overlay{{position:fixed;inset:0;background:rgba(0,0,0,.35);display:flex;align-items:center;justify-content:center}}
.modal{{background:{t['panel']};color:{t['fg']};padding:18px;border-radius:{t['radius']};min-width:320px;max-width:560px}}
.banner{{position:fixed;left:0;right:0;bottom:0;background:{t['panel']};border-top:2px solid {t['accent']};padding:12px 18px}}
.card{{border:1px solid {t['border']};border-radius:{t['radius']};padding:{t['pad']};margin:8px 0}}
.muted{{opacity:.7;font-size:13px}} .crumbs{{font-size:13px}} .crumbs a{{color:{t['accent']}}}
.tree ul{{list-style:none;padding-left:18px}}
"""


# JS runtime: div-buttons and auto-saving fields submit the page form with the ctl
# id in __js; Enter inside a text field never submits implicitly (that would fire
# whichever submit button comes first, possibly a trap).
JS = """(function(){
var f=document.getElementById('appform');
function go(c){var h=document.getElementById('__js');h.value=c;f.submit();}
document.addEventListener('click',function(e){var d=e.target.closest&&e.target.closest('[data-submit]');
 if(d){e.preventDefault();go(d.getAttribute('data-submit'));}});
document.addEventListener('keydown',function(e){var t=e.target;var d=t.closest&&t.closest('[data-submit]');
 if(d&&(e.key==='Enter'||e.key===' ')){e.preventDefault();go(d.getAttribute('data-submit'));return;}
 if(e.key==='Enter'&&t.tagName==='INPUT'){e.preventDefault();}});
document.addEventListener('change',function(e){var a=e.target.getAttribute&&e.target.getAttribute('data-autosubmit');
 if(a){go(a);}});
})();"""


# -------------------------------------------------------------------- builder --

class B:
    """Append-only HTML builder. ``aria`` selects the ARIA-quality rendering."""

    def __init__(self, aria: str = "good"):
        assert aria in ARIA_LEVELS, aria
        self.aria = aria
        self.out: List[str] = []
        self.checkboxes: List[str] = []          # rendered checkbox field names (-> __cb markers)
        self.radiogroups: List[str] = []         # rendered radio field names (-> __rg markers)

    # -- raw ------------------------------------------------------------------
    def w(self, s: str) -> "B":
        self.out.append(s)
        return self

    def html(self) -> str:
        return "".join(self.out)

    def h(self, text: str, level: int = 2) -> None:
        self.w(f"<h{level}>{esc(text)}</h{level}>")

    def p(self, text: str, cls: Optional[str] = None, **attrs) -> None:
        self.w(f"<p{_attrs({'cls': cls, **attrs})}>{esc(text)}</p>")

    def muted(self, text: str, **attrs) -> None:
        self.p(text, cls="muted", **attrs)

    @contextmanager
    def tag(self, tag: str, **attrs):
        self.w(f"<{tag}{_attrs(attrs)}>")
        yield self
        self.w(f"</{tag}>")

    # -- containers (named containers become Control.context) ------------------
    def region(self, name: str, **attrs):
        return self.tag("section", role="region", aria_label=name, **attrs)

    def group(self, name: str, **attrs):
        """A named form group (role=form)."""
        return self.tag("div", role="form", aria_label=name, **attrs)

    @contextmanager
    def dialog(self, name: str, *, alert: bool = False, desc: str = ""):
        self.w('<div class="overlay">')
        self.w(f'<div{_attrs({"role": "alertdialog" if alert else "dialog", "aria_modal": "true", "aria_label": name, "cls": "modal"})}>')
        self.w(f"<h2>{esc(name)}</h2>")
        if desc:
            self.p(desc)
        yield self
        self.w("</div></div>")

    def status(self, text: str) -> None:
        self.w(f'<div role="status" class="flash">{esc(text)}</div>')

    def alert(self, text: str) -> None:
        self.w(f'<div role="alert" class="flash alert">{esc(text)}</div>')

    # -- actions ----------------------------------------------------------------
    def button(self, ctl: str, name: str, *, icon: Optional[str] = None, short: Optional[str] = None,
               variant: str = "", expanded: Optional[bool] = None, pressed: Optional[bool] = None) -> None:
        """A submit button. ``short`` = generic name used at aria=duplicated_names;
        ``icon`` = glyph used at aria=icon_only (name then only in title)."""
        cls = "btn" + (f" {variant}" if variant else "")
        exp = None if expanded is None else ("true" if expanded else "false")
        prs = None if pressed is None else ("true" if pressed else "false")
        label = short if (self.aria == "duplicated_names" and short) else name
        if self.aria == "div_buttons":
            self.w(f'<div{_attrs({"role": "button", "tabindex": "0", "cls": cls, "data_submit": ctl, "aria_expanded": exp, "aria_pressed": prs})}>{esc(label)}</div>')
        elif self.aria == "icon_only" and icon:
            self.w(f'<button{_attrs({"type": "submit", "name": "__ctl", "value": ctl, "cls": cls + " icon", "title": name, "aria_expanded": exp, "aria_pressed": prs})}>'
                   f'<span aria-hidden="true">{esc(icon)}</span></button>')
        else:
            self.w(f'<button{_attrs({"type": "submit", "name": "__ctl", "value": ctl, "cls": cls, "aria_expanded": exp, "aria_pressed": prs})}>{esc(label)}</button>')

    def link(self, href: str, name: str, *, ctl: Optional[str] = None, current: bool = False,
             title: Optional[str] = None) -> None:
        self.w(f'<a{_attrs({"href": href, "data_ctl": ctl, "aria_current": "page" if current else None, "title": title})}>{esc(name)}</a>')

    # -- inputs -------------------------------------------------------------------
    def _wrap_open(self):
        self.w('<div class="field">')

    def text(self, field: str, label: str, value: str = "", *, kind: str = "text", hint: str = "",
             error: str = "", inputmode: Optional[str] = None, autocomplete: Optional[str] = None,
             autosubmit: Optional[str] = None) -> None:
        """Text-like input. kind: text | email | tel | search | password."""
        fid = f"f-{field}"
        self._wrap_open()
        a = {"type": kind, "id": fid, "name": field, "value": value if kind != "password" else "",
             "inputmode": inputmode, "autocomplete": autocomplete, "data_autosubmit": autosubmit}
        hid = f"h-{field}" if (hint or error) else None
        if self.aria == "missing_labels":
            self.w(f'<span class="lbl">{esc(label)}</span>')
            a["placeholder"] = label
        else:
            self.w(f'<label for="{fid}">{esc(label)}</label>')
        if hid:
            a["aria_describedby"] = hid
        if error:
            a["aria_invalid"] = "true"
        self.w(f"<input{_attrs(a)}>")
        if hint or error:
            self.w(f'<span id="{hid}" class="{"err" if error else "hint"}">{esc(error or hint)}</span>')
        self.w("</div>")

    def password(self, field: str, label: str, **kw) -> None:
        self.text(field, label, "", kind="password", **kw)

    def textarea(self, field: str, label: str, value: str = "") -> None:
        fid = f"f-{field}"
        self._wrap_open()
        if self.aria == "missing_labels":
            self.w(f'<span class="lbl">{esc(label)}</span><textarea id="{fid}" name="{esc(field)}" placeholder="{esc(label)}" rows="3">{esc(value)}</textarea>')
        else:
            self.w(f'<label for="{fid}">{esc(label)}</label><textarea id="{fid}" name="{esc(field)}" rows="3">{esc(value)}</textarea>')
        self.w("</div>")

    def select(self, field: str, label: str, options: Sequence, value: str = "", *,
               autosubmit: Optional[str] = None) -> None:
        """<select>. options: list of str or (value, text)."""
        fid = f"f-{field}"
        self._wrap_open()
        a = {"id": fid, "name": field, "data_autosubmit": autosubmit}
        if self.aria == "missing_labels":
            self.w(f'<span class="lbl">{esc(label)}</span>')
            a["title"] = label
        else:
            self.w(f'<label for="{fid}">{esc(label)}</label>')
        self.w(f"<select{_attrs(a)}>")
        for o in options:
            ov, ot = (o, o) if isinstance(o, str) else o
            self.w(f'<option{_attrs({"value": ov, "selected": ov == value})}>{esc(ot)}</option>')
        self.w("</select></div>")

    def radios(self, field: str, label: str, options: Sequence, value: str = "", *,
               autosubmit: Optional[str] = None) -> None:
        """A radio group (role=radiogroup). options: list of str or (value, text)."""
        self.radiogroups.append(field)
        name_attr = {"aria_label": label}       # always: Chromium ignores title here and names the group from its options
        self.w(f'<div{_attrs({"role": "radiogroup", **name_attr, "cls": "field"})}>')
        self.w(f'<span class="lbl">{esc(label)}</span>')
        for i, o in enumerate(options):
            ov, ot = (o, o) if isinstance(o, str) else o
            rid = f"r-{field}-{i}"
            self.w(f'<label for="{rid}"><input{_attrs({"type": "radio", "id": rid, "name": field, "value": ov, "checked": ov == value, "data_autosubmit": autosubmit})}> {esc(ot)}</label> ')
        self.w("</div>")

    def checkbox(self, field: str, label: str, checked: bool = False, *,
                 autosubmit: Optional[str] = None, native: bool = True) -> None:
        """A boolean field: a real <input type=checkbox> (the product's click(n) toggles it and
        shows "= checked"/"= unchecked"); ``autosubmit`` submits on change (auto-save).
        ``native=False`` renders a TOGGLE BUTTON named "<label> (checked)" / "(not checked)"
        (ctl ``__toggle:<field>``, value in a hidden input, App runs ``autosubmit`` after the
        toggle) for agents whose action language has no checkbox verb."""
        self.checkboxes.append(field)
        if not native:
            self.hidden(field, "on" if checked else "")
            if autosubmit:
                self.hidden(f"__auto:{field}", autosubmit)
            self.w('<div class="field">')
            self.button(f"__toggle:{field}", f"{label} ({'checked' if checked else 'not checked'})",
                        icon="☑" if checked else "☐", variant="secondary")
            self.w("</div>")
            return
        fid = f"f-{field}"
        a = {"type": "checkbox", "id": fid, "name": field, "value": "on", "checked": bool(checked),
             "data_autosubmit": autosubmit}
        if self.aria == "missing_labels":
            a["title"] = label
            self.w(f'<div class="field"><input{_attrs(a)}> <span>{esc(label)}</span></div>')
        else:
            self.w(f'<div class="field"><label for="{fid}"><input{_attrs(a)}> {esc(label)}</label></div>')

    def hidden(self, field: str, value: str) -> None:
        self.w(f'<input type="hidden" name="{esc(field)}" value="{esc(value)}">')

    # -- tables -------------------------------------------------------------------
    @contextmanager
    def table(self, caption: str, header: Sequence[str]):
        self.w(f"<table><caption>{esc(caption)}</caption><tr>" + "".join(f"<th>{esc(h)}</th>" for h in header) + "</tr>")
        yield self
        self.w("</table>")

    @contextmanager
    def tr(self):
        self.w("<tr>")
        yield self
        self.w("</tr>")

    def td(self, text: str = "") -> None:
        self.w(f"<td>{esc(text)}</td>")

    @contextmanager
    def td_ctx(self):
        self.w("<td>")
        yield self
        self.w("</td>")


# ---------------------------------------------------------------------- shell --

def shell(*, app_title: str, page_title: str, iid: str, view_q: str, nav: Sequence[Tuple[str, str]],
          current: str, main: B, theme: str, layout: str, aria: str, flash: Optional[dict],
          popup: Optional[str], aside: Optional[B], extra: Optional[B] = None) -> str:
    base = f"/t/{iid}/"
    nav_html = '<nav aria-label="Main"><ul>' + "".join(
        f'<li><a href="{esc(base + v)}" data-ctl="nav_{esc(v.split("?")[0])}"{" aria-current=" + chr(34) + "page" + chr(34) if v.split("?")[0] == current else ""}>{esc(lbl)}</a></li>'
        for v, lbl in nav) + "</ul></nav>"
    fl = ""
    if flash:
        role = "alert" if flash.get("kind") == "alert" else "status"
        fl = f'<div role="{role}" class="flash{" alert" if role == "alert" else ""}">{esc(flash["text"])}'
        if flash.get("undo"):
            fb = B(aria)
            fb.button(flash["undo"], flash.get("undo_label", "Undo"), icon="↶")
            fl += " " + fb.html()
        fl += "</div>"
    markers = "".join(f'<input type="hidden" name="__cb" value="{esc(n)}">' for n in main.checkboxes
                      + (aside.checkboxes if aside else []) + (extra.checkboxes if extra else []))
    markers += "".join(f'<input type="hidden" name="__rg" value="{esc(n)}">' for n in main.radiogroups
                       + (aside.radiogroups if aside else []) + (extra.radiogroups if extra else []))
    pop = ""
    if popup == "cookie":
        pb = B(aria)
        pb.w('<div role="dialog" aria-label="Cookie preferences" class="banner">')
        pb.p("We use cookies to improve your experience. Choose which cookies you allow.")
        pb.button("__cookie_accept", "Accept all cookies", short="Accept all")
        pb.button("__cookie_reject", "Reject non-essential cookies", short="Reject", variant="secondary")
        pb.w("</div>")
        pop = pb.html()
    elif popup == "newsletter":
        pb = B(aria)
        with pb.dialog("Join our newsletter", desc="Get product news and offers every week. Your account email will be subscribed."):
            pb.button("__news_subscribe", "Subscribe me", short="Subscribe")
            pb.button("__news_close", "No thanks", icon="✕", variant="secondary")
        pop = pb.html()
    main_html = f'<main><section role="region" aria-label="{esc(page_title)}">{fl}{main.html()}</section></main>'
    aside_html = f"<aside>{aside.html()}</aside>" if aside and aside.out else ""
    if layout == "sidebar":
        body = f'<div class="shell sidebar">{nav_html}{main_html}{aside_html}</div>'
    elif layout == "topnav":
        body = f'<div class="shell topnav">{nav_html}{main_html}{aside_html}</div>'
    else:                                                    # stacked: aside first, nav last
        body = f'<div class="shell stacked">{aside_html}{main_html}{nav_html}</div>'
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{esc(page_title)} - {esc(app_title)}</title>'
            f"<style>{css(theme)}</style></head><body>"
            f'<form id="appform" method="post" action="{esc(base)}act" autocomplete="off">'
            f'<input type="hidden" name="__view" value="{esc(view_q)}"><input type="hidden" id="__js" name="__js" value="">'
            f'<header><span class="brand">{esc(app_title)}</span></header>{body}{markers}'
            f"{extra.html() if extra else ''}{pop}</form><script>{JS}</script></body></html>")
