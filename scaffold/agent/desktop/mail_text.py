"""mail_text.py: email text hygiene for prompts (WP3). Stdlib only, no model.

  html_to_text(html)            visible text of an HTML body (scripts, styles,
                                head and hidden preheaders dropped; block
                                elements become line breaks)
  strip_tracking_pixels(html)   remove 0x0 / 1x1 / hidden / known-beacon <img>
                                tags; ``block_remote=True`` also drops every
                                non-cid: image (nothing is fetched on render)
  strip_link_trackers(text)     unwrap redirect wrappers (google /url?q=,
                                Outlook safelinks, urldefense v2) and drop
                                utm_* / click-id query parameters in every URL
  trim_quoted(text)             cut the quoted history of a reply ("On .. wrote:",
                                "> " blocks, Outlook "-----Original Message-----"
                                and "From: / Sent:" headers)
  escape_xml(text)              & < > " ' escaped, XML-illegal control chars removed
  message_to_xml(m) / thread_to_xml(msgs) / messages_to_xml(msgs)
                                normalised message dicts (mail_adapter shape) ->
                                escaped XML-ish data blocks for prompts. Every
                                piece of mail-controlled text goes through
                                escape_xml, so a body containing "</body>" or
                                "<system>..." stays character data inside its
                                element and cannot close or open a block.

Portions derived from Zero (Mail-0/Zero, commit 64c5480), MIT licence:
  apps/server/src/thread-workflow-utils/workflow-utils.ts
      htmlToText, escapeXml, messageToXML, getParticipants, threadToXML
  apps/server/src/lib/email-processor.ts
      tracking-pixel removal (img width/height 0 or 1), remote-image blocking
      except cid:, preheader removal, quoted-text selectors (blockquote,
      .gmail_quote)
Ported to Python and changed (stdlib html.parser instead of cheerio, line
structure kept, more pixel heuristics, link-tracker stripping, quote trimming
on plain text, control-character removal). Original notice:

    MIT License

    Copyright (c) 2025 Zero Email

    Permission is hereby granted, free of charge, to any person obtaining a copy
    of this software and associated documentation files (the "Software"), to deal
    in the Software without restriction, including without limitation the rights
    to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
    copies of the Software, and to permit persons to whom the Software is
    furnished to do so, subject to the following conditions:

    The above copyright notice and this permission notice shall be included in all
    copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
    IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
    FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
    AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
    LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
    OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
    SOFTWARE.

None of the 7 Zero files with the restricted header was used.
"""
from __future__ import annotations

import html as _html
import re
from html.parser import HTMLParser
from typing import Dict, Iterable, List, Optional, Sequence
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

__all__ = [
    "html_to_text", "strip_tracking_pixels", "strip_link_trackers", "clean_url",
    "trim_quoted", "escape_xml", "message_to_xml", "thread_to_xml", "messages_to_xml",
    "get_participants", "looks_like_html",
]

# --------------------------------------------------------------------------- html -> text

_BLOCK = {"p", "div", "br", "tr", "li", "ul", "ol", "table", "h1", "h2", "h3", "h4", "h5",
          "h6", "blockquote", "pre", "hr", "section", "article", "header", "footer", "dd",
          "dt", "dl", "address", "center", "form", "fieldset"}
_DROP = {"script", "style", "head", "title", "noscript", "template", "svg", "object",
         "iframe"}
_VOID = {"br", "img", "hr", "meta", "link", "input", "area", "base", "col", "embed",
         "param", "source", "track", "wbr"}
_HIDDEN_STYLE = re.compile(
    r"display\s*:\s*none|visibility\s*:\s*hidden|mso-hide\s*:\s*all|"
    r"(?:max-height|line-height|font-size)\s*:\s*0(?:px|pt|em|%)?\s*(?:;|$|!)|"
    r"opacity\s*:\s*0(?:\.0+)?\s*(?:;|$|!)", re.I)


def looks_like_html(text: str) -> bool:
    return bool(re.search(r"<(?:html|body|div|p|br|table|span|a|img|td|font)\b", text or "", re.I))


def _is_preheader(attrs: Dict[str, str]) -> bool:
    # Zero email-processor.ts: hidden .preheader / [class*=preheader] blocks.
    return "preheader" in (attrs.get("class") or "").lower() and bool(
        _HIDDEN_STYLE.search(attrs.get("style") or ""))


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: List[str] = []
        self._skip: List[str] = []          # stack of tags whose content is dropped

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if self._skip:
            if tag not in _VOID:
                self._skip.append(tag)
            return
        if tag in _DROP or _is_preheader(a):
            if tag not in _VOID:
                self._skip.append(tag)
            return
        if tag in _BLOCK:
            self.out.append("\n")
        if tag == "img" and a.get("alt", "").strip():
            self.out.append(f"[{a['alt'].strip()}]")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID and self._skip and self._skip[-1] == tag:
            self._skip.pop()

    def handle_endtag(self, tag):
        if self._skip:
            if tag in self._skip:
                while self._skip and self._skip.pop() != tag:
                    pass
            return
        if tag in _BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.out.append(data)


def html_to_text(body: str) -> str:
    """Visible text of an HTML body. Never raises; returns '' for non-strings."""
    if not body or not isinstance(body, str):
        return ""
    p = _TextExtractor()
    try:
        p.feed(body)
        p.close()
    except Exception:  # malformed markup: fall back to tag stripping
        return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]*>", " ", body))).strip()
    text = "".join(p.out).replace("\xa0", " ").replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t\f\v]+", " ", ln).strip() for ln in text.split("\n")]
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# --------------------------------------------------------------------------- tracking pixels

_IMG_TAG = re.compile(r"<img\b[^>]*>", re.I | re.S)
_ATTR = re.compile(r"""([a-zA-Z_:][-\w:.]*)\s*(?:=\s*("[^"]*"|'[^']*'|[^\s"'>]+))?""")
_BEACON_URL = re.compile(
    r"(?:/(?:open|opened|track|tracking|pixel|beacon|wf/open|e/o|o\.gif|t\.gif|"
    r"trk|imp|impression|ss/o)(?:[/?.]|$))|(?:[?&](?:open|track|pixel)=)|"
    r"(?:\b(?:pixel|beacon|tracking|track|open)\.[a-z0-9-]+\.[a-z]{2,})|"
    r"(?:mailtrack|list-manage\.com/track|sendgrid\.net/wf/open|mandrillapp\.com/track|"
    r"hubspotlinks|mcusercontent|ct\.sendgrid|links\.\S+/o/)", re.I)


def _attrs(tag: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for k, v in _ATTR.findall(tag[4:].rstrip("/>")):
        if v[:1] in "\"'":
            v = v[1:-1]
        out[k.lower()] = _html.unescape(v)
    return out


def _tiny(v: Optional[str]) -> bool:
    if v is None:
        return False
    m = re.match(r"\s*(\d+(?:\.\d+)?)", v)
    return bool(m) and float(m.group(1)) <= 1


def _is_pixel(a: Dict[str, str]) -> bool:
    style = a.get("style", "")
    sw = re.search(r"(?:^|;|\s)width\s*:\s*([\d.]+)", style, re.I)
    sh = re.search(r"(?:^|;|\s)height\s*:\s*([\d.]+)", style, re.I)
    w = a.get("width") if "width" in a else (sw.group(1) if sw else None)
    h = a.get("height") if "height" in a else (sh.group(1) if sh else None)
    if _tiny(w) and _tiny(h):               # Zero: img[width=1][height=1], [0][0]
        return True
    if (_tiny(w) or _tiny(h)) and _BEACON_URL.search(a.get("src", "")):
        return True
    if _HIDDEN_STYLE.search(style):
        return True
    return bool(_BEACON_URL.search(a.get("src", ""))) and not a.get("alt", "").strip()


def strip_tracking_pixels(body: str, *, block_remote: bool = False) -> str:
    """Remove tracking-pixel <img> tags from an HTML (or HTML-ish text) body.

    Text without <img> tags is returned unchanged (byte for byte). With
    ``block_remote`` every image whose src is not ``cid:`` / ``data:`` is
    removed too (Zero's default "don't load images" behaviour).
    """
    if not body or "<img" not in body.lower():
        return body

    def repl(mo: "re.Match[str]") -> str:
        a = _attrs(mo.group(0))
        src = a.get("src", "").strip().lower()
        if _is_pixel(a):
            return ""
        if block_remote and not (src.startswith("cid:") or src.startswith("data:")):
            alt = a.get("alt", "").strip()
            return f"[image: {_html.escape(alt)}]" if alt else ""
        return mo.group(0)
    return _IMG_TAG.sub(repl, body)


# --------------------------------------------------------------------------- link trackers

_TRACK_PARAMS = re.compile(
    r"^(?:utm_[a-z_]+|mc_eid|mc_cid|fbclid|gclid|dclid|msclkid|yclid|_hsenc|_hsmi|"
    r"mkt_tok|vero_id|vero_conv|oly_enc_id|oly_anon_id|ck_subscriber_id|trk|trkid|"
    r"wickedid|rb_clickid|s_cid|ml_subscriber|ml_subscriber_hash|_kx|__s|ref_src)$", re.I)
_URL = re.compile(r"""https?://[^\s<>"'()\[\]]+""", re.I)


def clean_url(url: str, _depth: int = 0) -> str:
    """Unwrap known redirect wrappers and drop tracking query parameters."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    host = (parts.hostname or "").lower()
    q = parse_qsl(parts.query, keep_blank_values=True)
    if _depth < 3:
        inner = None
        if host.endswith("google.com") and parts.path == "/url":
            inner = dict(q).get("q") or dict(q).get("url")
        elif host.endswith("safelinks.protection.outlook.com"):
            inner = dict(q).get("url")
        elif host == "urldefense.proofpoint.com" and parts.path.startswith("/v2/url"):
            u = dict(q).get("u")
            if u:
                inner = unquote(u.replace("-", "%").replace("_", "/"))
        if inner and inner.lower().startswith(("http://", "https://")):
            return clean_url(inner, _depth + 1)
    kept = [(k, v) for k, v in q if not _TRACK_PARAMS.match(k)]
    if len(kept) == len(q):
        return url
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), parts.fragment))


def strip_link_trackers(text: str) -> str:
    """Apply clean_url to every http(s) URL in ``text``; other text unchanged."""
    if not text or "http" not in text.lower():
        return text
    def repl(mo):
        u = mo.group(0)
        trail = ""
        while u and u[-1] in ".,;:!?":
            trail = u[-1] + trail
            u = u[:-1]
        return clean_url(u) + trail
    return _URL.sub(repl, text)


# --------------------------------------------------------------------------- quoted history

_ON_WROTE = re.compile(r"^\s*On\b.{0,300}\bwrote:\s*$", re.I)
_ORIG = re.compile(r"^\s*-{2,}\s*(?:Original Message|Forwarded message)\s*-{2,}\s*$", re.I)
_OUTLOOK_FROM = re.compile(r"^\s*From:\s.+$", re.I)
_OUTLOOK_NEXT = re.compile(r"^\s*(?:Sent|Date|To|Subject):\s", re.I)


def trim_quoted(text: str) -> str:
    """Return the new part of a reply: drop the quoted history that follows it.

    Only cuts at a recognised quote header or a trailing run of '>' lines, and
    never returns an empty string when the input had content (a message that
    is all quote is returned as is).
    """
    if not text:
        return text
    lines = text.replace("\r\n", "\n").split("\n")
    cut = None
    for i, ln in enumerate(lines):
        if _ON_WROTE.match(ln) or _ORIG.match(ln):
            cut = i
            break
        # "On Mon, 1 Sep 2026 at 09:00, Someone <a@b>\nwrote:" split over two lines
        if i + 1 < len(lines) and re.match(r"^\s*On\b", ln, re.I) and \
                re.match(r"^\s*.{0,120}\bwrote:\s*$", lines[i + 1], re.I) and len(ln) < 300:
            cut = i
            break
        if _OUTLOOK_FROM.match(ln) and any(_OUTLOOK_NEXT.match(x) for x in lines[i + 1:i + 4]):
            cut = i
            break
    if cut is None:
        j = len(lines)
        while j > 0 and (lines[j - 1].lstrip().startswith(">") or not lines[j - 1].strip()):
            j -= 1
        if j < len(lines) and any(x.lstrip().startswith(">") for x in lines[j:]):
            cut = j
    if cut is None:
        return text
    head = "\n".join(lines[:cut]).rstrip()
    return head if head.strip() else text


# --------------------------------------------------------------------------- XML serialisation

_XML_ILLEGAL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f￾￿]")


def escape_xml(text) -> str:
    """Zero's escapeXml plus removal of characters XML 1.0 forbids."""
    if text is None:
        return ""
    s = _XML_ILLEGAL.sub("", str(text))
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&apos;"))


def get_participants(messages: Sequence[dict]) -> List[Dict[str, str]]:
    seen: Dict[str, Dict[str, str]] = {}
    from email.utils import getaddresses
    for m in messages:
        people = [(m.get("from_name") or "", m.get("from_addr") or "")]
        for key in ("to", "cc"):
            v = m.get(key)
            if isinstance(v, str):
                people += getaddresses([v])
            elif isinstance(v, (list, tuple)):
                people += getaddresses([str(x) for x in v])
        for name, addr in people:
            addr = (addr or "").strip()
            if addr and addr.lower() not in seen:
                seen[addr.lower()] = {"name": (name or "").strip(), "email": addr}
    return list(seen.values())


def _body_text(m: dict, *, trim_quotes: bool, max_body_chars: Optional[int]) -> str:
    body = m.get("body") or ""
    if not body and m.get("html"):
        body = m["html"]
    if looks_like_html(body):
        body = html_to_text(strip_tracking_pixels(body, block_remote=True))
    body = strip_link_trackers(body)
    if trim_quotes:
        body = trim_quoted(body)
    if max_body_chars is not None and len(body) > max_body_chars:
        body = body[:max_body_chars] + " [truncated]"
    return body


def message_to_xml(m: dict, *, trim_quotes: bool = True, max_body_chars: Optional[int] = 4000,
                   include_labels: bool = True) -> str:
    """One normalised message -> ``<message>...</message>`` (all text escaped)."""
    parts = [f"<message id=\"{escape_xml(m.get('id', ''))}\">"]
    parts.append(f"<from>{escape_xml(m.get('from') or m.get('from_addr') or 'Unknown')}</from>")
    to = m.get("to")
    for t in (to if isinstance(to, (list, tuple)) else [to] if to else []):
        parts.append(f"<to>{escape_xml(t)}</to>")
    cc = m.get("cc")
    for c in (cc if isinstance(cc, (list, tuple)) else [cc] if cc else []):
        parts.append(f"<cc>{escape_xml(c)}</cc>")
    parts.append(f"<date>{escape_xml(m.get('date', ''))}</date>")
    parts.append(f"<subject>{escape_xml(m.get('subject', ''))}</subject>")
    lh = m.get("list_headers") or {k: v for k, v in (m.get("headers") or {}).items()
                                   if k.lower() in ("list-id", "list-unsubscribe")}
    if lh:
        parts.append("<list>" + "".join(
            f"<header name=\"{escape_xml(k)}\">{escape_xml(v)}</header>"
            for k, v in sorted(lh.items())) + "</list>")
    if include_labels and m.get("labels"):
        parts.append("<labels>" + "".join(f"<label>{escape_xml(x)}</label>"
                                          for x in m["labels"]) + "</labels>")
    parts.append(f"<body>{escape_xml(_body_text(m, trim_quotes=trim_quotes, max_body_chars=max_body_chars))}</body>")
    parts.append("</message>")
    return "".join(parts)


def thread_to_xml(messages: Sequence[dict], summary: Optional[str] = None, **kw) -> str:
    """A thread (oldest first) -> ``<thread>`` with subject, participants, messages."""
    msgs = list(messages)
    subject = (msgs[0].get("subject") if msgs else "") or "No Subject"
    people = "".join(
        f"<participant email=\"{escape_xml(p['email'])}\">{escape_xml(p['name'] or p['email'])}</participant>"
        for p in get_participants(msgs))
    tid = msgs[0].get("thread_id", "") if msgs else ""
    out = [f"<thread id=\"{escape_xml(tid)}\">", f"<subject>{escape_xml(subject)}</subject>",
           f"<participants>{people}</participants>"]
    if summary:
        out.append(f"<summary>{escape_xml(summary)}</summary>")
    out.append("<messages>" + "".join(message_to_xml(m, **kw) for m in msgs) + "</messages>")
    out.append("</thread>")
    return "".join(out)


def messages_to_xml(messages: Iterable[dict], **kw) -> str:
    """Several independent messages -> one ``<emails>`` data block."""
    return "<emails>" + "".join(message_to_xml(m, **kw) for m in messages) + "</emails>"
