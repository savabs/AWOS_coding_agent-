"""
pack/policy.py — what the Month-end Document Pack may touch on the owner's real portals (v0.1).

    src = SourceConfig("Stripe", domains=("stripe.com",), sso_domains=("accounts.google.com",))
    allowed("https://dashboard.stripe.com/invoices", "navigate", src)   # Decision(ok=True, ...)
    allowed("https://evil.example/", "redirect", src)                   # ok=False, needs_you=True
    control_verdict({"role": "AXButton", "title": "Pay now"})           # ok=False (no approval path)
    field_verdict({"role": "AXTextField", "title": "From date"})        # ok=True
    url_hook(src)                                                       # -> actions.Actor(url_policy=...)

Domains: https only (port 443), exact registrable domains from the source config plus the
explicitly listed SSO / IdP domains; a host matches a listed domain when it IS that domain or
a subdomain of it ("dashboard.stripe.com" for "stripe.com", never "stripe.com.evil.net" or
"evilstripe.com"). URLs with credentials, IP literals (other than loopback), odd ports,
whitespace or backslashes are refused. Loopback (http/https on 127.0.0.1 / localhost / ::1)
stays allowed so the fake-portal tests keep working. A page that LANDS on a domain outside
the list (``action="redirect"`` / ``"landed"``) is a stop: ``needs_you=True``.

Read-only: v0.1 has NO approval path for anything that moves money, changes a plan, deletes,
or acts on the owner's behalf. A control is refused when its name (title / description /
label, and a link's URL path) hits the finance deny list below, or ax_compress.button_role
calls it destructive -- except the minimal cookie-banner choices ("Reject all", "Necessary
only", ...), which are allowed. Navigation, menus, period / date-range pickers, download /
export / view-invoice / statement links stay allowed. Text entry is limited to date /
period / search / filter fields; secure, OTP and captcha fields are never typed into (the
human checkpoint, live/checkpoint.py, hands those to the owner).
"""
from __future__ import annotations

import ipaddress
import re
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, Optional, Tuple

from ..ax_compress import button_role, is_secure_node, norm_role

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
#: suffixes too broad to be a source's "registrable domain" (a tiny built-in list, not the PSL)
PUBLIC_SUFFIXES = frozenset({
    "com", "net", "org", "io", "co", "in", "ai", "app", "dev", "biz", "info", "me", "us", "uk",
    "co.in", "net.in", "org.in", "firm.in", "gen.in", "ind.in", "gov.in", "nic.in", "ac.in", "edu.in",
    "res.in", "co.uk", "org.uk", "gov.uk", "ac.uk", "com.au", "gov", "edu", "mil", "int",
    "github.io", "vercel.app", "netlify.app", "herokuapp.com", "pages.dev", "workers.dev",
    "appspot.com", "web.app", "firebaseapp.com", "azurewebsites.net", "cloudfront.net",
    "amazonaws.com", "blogspot.com",
})
ACTIONS = frozenset({"navigate", "open", "observe", "redirect", "landed", "press", "select", "scroll",
                     "download", "type", "menu"})
STOP_ACTIONS = frozenset({"redirect", "landed"})

# -------------------------------------------------------------- deny / allow lists --
#: controls that are always refused in v0.1, whatever button_role says and whatever approve() would say
FINANCE_DENY_RE = re.compile(
    r"\b("
    r"pay|pay now|pay bill|make (a )?payment|proceed to pay(ment)?|payment now|checkout|check out|buy|"
    r"purchase|place order|order now|upgrade|downgrade|subscribe|resubscribe|renew|auto[- ]?renew|"
    r"cancel (subscription|plan|membership|account|order|payment|mandate|auto[- ]?pay)|"
    r"change plan|switch plan|choose plan|select plan|start (free )?trial|"
    r"delete|remove( (card|payment method|account|payee|beneficiary|user|member))?|"
    r"transfer|send money|send|wire|remit|pay(ee|out)s? now|"
    r"add (payee|beneficiary|card|payment method|bank account|account|user|member|funds|money)|"
    r"approve|authori[sz]e|confirm( (payment|transfer|purchase|order))?|submit|"
    r"close account|deactivate|terminate|"
    r"sign out of all (devices|sessions)|log ?out of all (devices|sessions)|sign out|log ?out|log off|"
    r"withdraw|refund|issue refund|dispute|raise dispute|chargeback|"
    r"request (refund|payout|loan|card|cheque ?book|check ?book|statement by (e-?mail|post))|"
    r"block card|freeze|unfreeze|unblock|lock card|unlock|"
    r"enable|disable|turn on|turn off|activate|"
    r"save|save changes|update|apply for|invite|share|"
    r"create|new (invoice|payment|payment link|customer|subscription|product|coupon|payout|transfer)|"
    r"file (return|gstr[\w-]*)|e-?verify|generate (challan|invoice|payment link|api key|token|key)|"
    r"yes|ok|okay|continue|proceed|accept( all)?( cookies)?|allow all( cookies)?|agree"
    r")\b", re.I)

#: URL path segments that mark a money-moving / account-changing page
DENY_PATH_SEGMENTS = frozenset({
    "pay", "payment-new", "checkout", "upgrade", "downgrade", "subscribe", "purchase", "buy",
    "transfer", "transfers-new", "send-money", "delete", "cancel", "close-account", "logout",
    "log-out", "signout", "sign-out", "add-payee", "add-beneficiary", "approve",
})

#: minimal cookie-banner choices: allowed although button_role may call "accept ..." destructive
COOKIE_MINIMAL_RE = re.compile(
    r"^(reject( all)?( cookies)?|decline( all)?( cookies)?|deny( all)?|refuse( all)?|"
    r"(accept|allow|use|only|use only)? ?(strictly )?(necessary|essential|required)( cookies)?( only)?|"
    r"(accept|allow) (only )?(strictly )?(necessary|essential|required)( cookies)?( only)?|"
    r"reject non-?essential( cookies)?|only (strictly )?(necessary|essential)( cookies)?|"
    r"continue without accepting)$", re.I)

#: read-only things the pack exists to do (informational: they are allowed anyway when not denied)
READ_ALLOW_RE = re.compile(
    r"\b(download|export|view|open|show|statement|invoice|receipt|report|history|transactions?|"
    r"settlements?|payouts?|billing|period|month|quarter|year|date|range|from|to|filter|apply|"
    r"search|go|next|previous|prev|more|page|menu|csv|pdf|xlsx?|excel|print|details?)\b", re.I)

#: text fields the pack may type into (date ranges, periods, search / filter boxes)
FIELD_ALLOW_RE = re.compile(
    r"\b(date|from|to|start|end|period|month|year|quarter|range|search|filter|find|query|"
    r"statement period|financial year|fy|ay|assessment year|dd/mm/yyyy|mm/yyyy|yyyy-mm-dd)\b", re.I)
#: fields that belong to the owner (OTP, captcha, credentials) -- never typed into
HUMAN_ONLY_FIELD_RE = re.compile(
    r"(otp|one[- ]?time|verification code|security code|auth(entication|enticator)? code|2fa|"
    r"two[- ]factor|passcode|pass ?word|\bpin\b|mpin|tpin|captcha|recaptcha|hcaptcha|"
    r"user ?(name|id)|login id|customer id|cust ?id|e-?mail|card number|cvv|cvc)", re.I)
MENU_ROLES = frozenset({"menubutton", "popupbutton", "menubaritem", "disclosuretriangle", "tab", "combobox"})
DENY_ROLES = frozenset({"switch"})                  # toggles apply immediately (auto-pay, alerts)


@dataclass(frozen=True)
class Decision:
    ok: bool
    reason: str = ""
    needs_you: bool = False
    category: str = ""

    def __bool__(self) -> bool:
        return self.ok

    def to_dict(self) -> dict:
        return {"ok": self.ok, "reason": self.reason, "needs_you": self.needs_you, "category": self.category}


def norm_host(host: Optional[str]) -> str:
    h = (host or "").strip().lower().rstrip(".")
    if not h:
        return ""
    try:
        h.encode("ascii")
    except UnicodeEncodeError:
        try:
            h = h.encode("idna").decode("ascii")
        except UnicodeError:
            return ""
    return h


def _is_ip(h: str) -> bool:
    try:
        ipaddress.ip_address(h.strip("[]"))
        return True
    except ValueError:
        return False


@dataclass(frozen=True)
class SourceConfig:
    """One portal of the pack: its exact registrable domains and the SSO / IdP domains its
    login may pass through."""
    name: str
    domains: Tuple[str, ...]
    sso_domains: Tuple[str, ...] = ()
    allow_loopback: bool = True
    notes: Dict[str, Any] = field(default_factory=dict, compare=False, hash=False)

    def __post_init__(self):
        if not self.name:
            raise ValueError("a source needs a name")
        doms = tuple(_check_domain(d) for d in _as_tuple(self.domains))
        if not doms:
            raise ValueError(f"source {self.name!r} lists no domains")
        object.__setattr__(self, "domains", doms)
        object.__setattr__(self, "sso_domains", tuple(_check_domain(d) for d in _as_tuple(self.sso_domains)))

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SourceConfig":
        return cls(name=str(d.get("name") or ""), domains=_as_tuple(d.get("domains") or ()),
                   sso_domains=_as_tuple(d.get("sso_domains") or ()),
                   allow_loopback=bool(d.get("allow_loopback", True)))

    def all_domains(self) -> Tuple[str, ...]:
        return self.domains + self.sso_domains


def _as_tuple(v: Iterable[str]) -> Tuple[str, ...]:
    return (v,) if isinstance(v, str) else tuple(v or ())


def _check_domain(d: str) -> str:
    raw = str(d or "")
    if "://" in raw or "/" in raw or "@" in raw or ":" in raw or "*" in raw:
        raise ValueError(f"domain {raw!r}: give a bare registrable domain (no scheme, path, port or wildcard)")
    h = norm_host(raw)
    if not h or "." not in h or h in PUBLIC_SUFFIXES or _is_ip(h):
        raise ValueError(f"domain {raw!r} is not a registrable domain")
    return h


def host_matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def allowed(url: str, action: str, source: SourceConfig) -> Decision:
    """May the pack ``action`` this ``url`` for ``source``? A refused landing / redirect
    (``action`` "redirect" or "landed") is a stop that needs the owner."""
    stop = action in STOP_ACTIONS

    def no(reason: str) -> Decision:
        return Decision(False, reason, needs_you=stop, category="foreign_domain" if stop else "url")
    if action not in ACTIONS:
        return Decision(False, f"unknown action {action!r}", category="action")
    u = str(url or "")
    if not u or any(c in u for c in "\\ \t\r\n"):
        return no("malformed URL")
    try:
        p = urllib.parse.urlsplit(u)
        port = p.port
    except ValueError:
        return no("malformed URL")
    scheme = p.scheme.lower()
    host = norm_host(p.hostname)
    if not host:
        return no("URL has no host")
    if source.allow_loopback and host in LOOPBACK_HOSTS and scheme in ("http", "https"):
        return Decision(True, "loopback", category="loopback")
    if scheme != "https":
        return no(f"only https is allowed (got {scheme or 'no scheme'}://{host})")
    if p.username is not None or p.password is not None:
        return no("URLs with credentials are refused")
    if port not in (None, 443):
        return no(f"port {port} is not allowed")
    if _is_ip(host):
        return no(f"IP-literal host {host} is not allowed")
    for d in source.domains:
        if host_matches(host, d):
            return Decision(True, f"{host} is {source.name} ({d})", category="source")
    for d in source.sso_domains:
        if host_matches(host, d):
            return Decision(True, f"{host} is {source.name}'s sign-in provider ({d})", category="sso")
    return no(f"{host} is not on {source.name}'s allowlist")


def url_host(url: Optional[str]) -> str:
    try:
        return norm_host(urllib.parse.urlsplit(url or "").hostname)
    except ValueError:
        return ""


def url_hook(source: SourceConfig, action: str = "navigate") -> Callable[[str], Tuple[bool, str]]:
    """The callable live/actions.Actor(url_policy=...) consults for non-local URLs."""
    def hook(url: str) -> Tuple[bool, str]:
        d = allowed(url, action, source)
        return d.ok, d.reason
    hook.source = source          # type: ignore[attr-defined]
    return hook


# ---------------------------------------------------------------------- controls --
def control_name(n: dict) -> str:
    parts = [str(n.get(k)) for k in ("title", "name", "label", "description", "help")
             if n.get(k) not in (None, "") and not isinstance(n.get(k), (dict, list))]
    return " ".join(" ".join(p.replace("’", "'").replace("…", " ").split()) for p in parts).strip()


def _deny_path(url: str) -> str:
    try:
        segs = [s.lower() for s in urllib.parse.urlsplit(url).path.split("/") if s]
    except ValueError:
        return "malformed"
    for s in segs:
        if s in DENY_PATH_SEGMENTS:
            return s
    return ""


def text_denied(text: str) -> str:
    """The deny-list phrase in ``text`` ("" when none)."""
    m = FINANCE_DENY_RE.search(text or "")
    return m.group(0) if m else ""


def control_verdict(node: dict, source: Optional[SourceConfig] = None, action: str = "press") -> Decision:
    """Read-only verdict for clicking / picking a control. No approval path in v0.1."""
    if not isinstance(node, dict):
        return Decision(False, "no element", category="missing")
    role = norm_role(node.get("role") or node.get("tag"))
    name = control_name(node)
    if node.get("enabled") is False:
        return Decision(False, "element is disabled", category="disabled")
    if role in DENY_ROLES:
        return Decision(False, f"toggle {name!r}: switches apply immediately (read-only pack)", category="deny")
    if role in ("textfield", "securetextfield", "textarea", "searchfield") and action == "press":
        return Decision(True, "focus a field", category="field")
    url = node.get("url")
    if isinstance(url, str) and url and source is not None and not url.startswith(("javascript:", "#")):
        d = allowed(url, "navigate", source)
        if not d.ok and not url.lower().startswith(("blob:", "data:")):
            return Decision(False, f"link leaves the allowlist: {d.reason}", category="url")
    if isinstance(url, str) and url:
        seg = _deny_path(url)
        if seg:
            return Decision(False, f"link goes to a /{seg} page (read-only pack)", category="deny")
    if name and COOKIE_MINIMAL_RE.match(name.strip(" .!")):
        return Decision(True, "minimal cookie choice", category="cookie_minimal")
    hit = text_denied(name)
    if hit:
        return Decision(False, f"{name!r} matches the read-only deny list ({hit.lower()}); "
                               f"no approval path in v0.1", category="deny")
    br = button_role(node, role)
    if br == "destructive":
        return Decision(False, f"{name!r} is destructive; no approval path in v0.1", category="deny")
    if role in MENU_ROLES:
        return Decision(True, "open a menu / picker", category="menu")
    if READ_ALLOW_RE.search(name):
        return Decision(True, "read-only control", category="read")
    return Decision(True, "navigation / neutral control", category="neutral")


def option_verdict(text: str) -> Decision:
    """A menu item / popup option by its visible text."""
    t = " ".join(str(text or "").split())
    if COOKIE_MINIMAL_RE.match(t.strip(" .!")):
        return Decision(True, "minimal cookie choice", category="cookie_minimal")
    hit = text_denied(t)
    if hit:
        return Decision(False, f"option {t!r} matches the read-only deny list ({hit.lower()})", category="deny")
    if button_role({"role": "menuitem", "title": t}, "menuitem") == "destructive":
        return Decision(False, f"option {t!r} is destructive; no approval path in v0.1", category="deny")
    return Decision(True, "option", category="option")


def human_only_field(node: dict) -> bool:
    """Password / OTP / captcha / credential fields: only the owner types there."""
    role = norm_role(node.get("role"))
    if is_secure_node(node, role):
        return True
    hints = " ".join(str(node.get(k) or "") for k in
                     ("title", "name", "label", "placeholder", "description", "identifier", "help"))
    return bool(HUMAN_ONLY_FIELD_RE.search(hints.replace("_", " ")))


def field_verdict(node: dict) -> Decision:
    """May the pack type into this text field? Only date / period / search / filter fields."""
    if not isinstance(node, dict):
        return Decision(False, "no element", category="missing")
    if human_only_field(node):
        return Decision(False, "secure / OTP / captcha / credential field: only the owner types here",
                        category="human_only")
    hints = " ".join(str(node.get(k) or "") for k in
                     ("title", "name", "label", "placeholder", "description", "identifier"))
    if text_denied(hints) and not FIELD_ALLOW_RE.search(hints):
        return Decision(False, f"field {hints.strip()!r} is not a read-only filter", category="deny")
    if FIELD_ALLOW_RE.search(hints.replace("_", " ")):
        return Decision(True, "date / period / search field", category="filter")
    return Decision(False, f"field {hints.strip() or '?'!r} is not a date / period / search field "
                           f"(read-only pack)", category="field")


__all__ = ["ACTIONS", "Decision", "SourceConfig", "allowed", "control_name", "control_verdict",
           "field_verdict", "host_matches", "human_only_field", "norm_host", "option_verdict",
           "text_denied", "url_host", "url_hook"]
