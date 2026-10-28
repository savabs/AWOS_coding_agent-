"""Month-end Document Pack v0.1 policy (scaffold/agent/desktop/pack/policy.py).

Per-source https domain allowlist (+ listed SSO / IdP domains, loopback kept for tests), a
landing off the list is a needs_you stop, and the read-only control policy: the finance deny
list is always refused (no approval path), reads / downloads / period pickers / the minimal
cookie choice are allowed, typing only into date / period / search fields. Pure, no UI.
"""
import pytest

from scaffold.agent.desktop.live import actions as A
from scaffold.agent.desktop.pack import policy as P

STRIPE = P.SourceConfig("Stripe", domains=("stripe.com",), sso_domains=("accounts.google.com",))
HDFC = P.SourceConfig("HDFC NetBanking", domains=("hdfcbank.com",))
GST = P.SourceConfig("GST portal", domains=("gst.gov.in",))


# ------------------------------------------------------------------- domains --
@pytest.mark.parametrize("url,src", [
    ("https://dashboard.stripe.com/invoices", STRIPE),
    ("https://stripe.com/", STRIPE),
    ("https://DASHBOARD.Stripe.COM./payments?x=1", STRIPE),
    ("https://accounts.google.com/signin/v2", STRIPE),          # listed IdP
    ("https://netbanking.hdfcbank.com/netbanking/", HDFC),
    ("https://services.gst.gov.in/services/login", GST),
    ("https://dashboard.stripe.com:443/x", STRIPE),
    ("http://127.0.0.1:8123/portal", STRIPE),                  # loopback stays (tests)
    ("https://localhost/x", HDFC),
])
def test_allowed_urls(url, src):
    d = P.allowed(url, "navigate", src)
    assert d.ok and not d.needs_you, d.reason


@pytest.mark.parametrize("url,why", [
    ("http://dashboard.stripe.com/", "https"),
    ("https://stripe.com.evil.net/", "allowlist"),
    ("https://evilstripe.com/", "allowlist"),
    ("https://stripe.co/", "allowlist"),
    ("https://google.com/", "allowlist"),                      # only accounts.google.com is listed
    ("https://user:pw@dashboard.stripe.com/", "credentials"),
    ("https://dashboard.stripe.com@evil.net/", "credentials"),
    ("https://dashboard.stripe.com:8443/", "port"),
    ("https://52.1.2.3/", "IP"),
    ("https://dashboard.stripe.com\\@evil.net/", "malformed"),
    ("https://dash board.stripe.com/", "malformed"),
    ("javascript:alert(1)", "host"),
    ("file:///etc/passwd", "host"),
    ("", "malformed"),
    ("https://gov.in/", "allowlist"),
])
def test_refused_urls(url, why):
    d = P.allowed(url, "navigate", STRIPE if "gov" not in url else GST)
    assert not d.ok and why.lower() in d.reason.lower(), d.reason
    assert not d.needs_you                         # refusing to GO somewhere is not a stop


def test_loopback_can_be_switched_off():
    src = P.SourceConfig("x", domains=("example.com",), allow_loopback=False)
    assert not P.allowed("http://127.0.0.1/x", "navigate", src).ok


def test_redirect_to_foreign_domain_is_a_needs_you_stop():
    d = P.allowed("https://phish.example/login", "redirect", STRIPE)
    assert not d.ok and d.needs_you and d.category == "foreign_domain"
    assert P.allowed("https://phish.example/", "landed", STRIPE).needs_you
    assert P.allowed("https://accounts.google.com/o/oauth2", "redirect", STRIPE).ok   # SSO hop is fine
    assert not P.allowed("https://dashboard.stripe.com/", "teleport", STRIPE).ok      # unknown action


@pytest.mark.parametrize("bad", ["com", "co.in", "gov.in", "https://stripe.com", "stripe.com/x", "*.stripe.com",
                                 "1.2.3.4", "", "stripe.com:443", "localhost"])
def test_source_config_rejects_broad_or_malformed_domains(bad):
    with pytest.raises(ValueError):
        P.SourceConfig("x", domains=(bad,))


def test_source_from_dict_and_idn():
    s = P.SourceConfig.from_dict({"name": "Razorpay", "domains": ["Razorpay.com"], "sso_domains": "login.microsoftonline.com"})
    assert s.domains == ("razorpay.com",) and s.sso_domains == ("login.microsoftonline.com",)
    # a homograph host never matches an ascii domain
    assert not P.allowed("https://dashboard.rаzorpay.com/", "navigate", s).ok       # Cyrillic 'а'


# ------------------------------------------------- actions.url_allowed hook --
def test_actions_url_allowed_hook_only_adds():
    hook = P.url_hook(STRIPE)
    url = "https://dashboard.stripe.com/invoices"
    assert not A.url_allowed(url)[0]                                    # unchanged default: refused
    ok, why = A.url_allowed(url, (), hook)
    assert ok and "url policy" in why
    assert not A.url_allowed("https://evil.example/", (), hook)[0]
    # existing allowances are untouched by the hook
    assert A.url_allowed("http://127.0.0.1:9/x", (), hook)[0]
    assert A.url_allowed("file:///tmp/x.txt", (), hook)[0]
    # a broken hook refuses instead of raising
    assert not A.url_allowed("https://x.example/", (), lambda u: 1 / 0)[0]


# --------------------------------------------------------- read-only controls --
DENY = ["Pay", "Pay now", "Checkout", "Buy", "Upgrade", "Subscribe", "Cancel subscription", "Delete", "Remove card",
        "Transfer", "Send money", "Add payee", "Approve", "Confirm payment", "Change plan", "Close account",
        "Sign out of all devices", "Upgrade to Pro", "Buy credits", "Place order", "Purchase", "Renew now",
        "Add beneficiary", "Authorize", "Submit", "Save changes", "Accept all cookies", "Proceed to pay",
        "File GSTR-3B", "Log out", "Request refund", "Enable auto-pay", "Yes", "OK", "Continue"]


@pytest.mark.parametrize("name", DENY)
@pytest.mark.parametrize("role", ["AXButton", "AXLink", "AXMenuItem"])
def test_deny_list_always_refused(name, role):
    v = P.control_verdict({"role": role, "title": name}, STRIPE)
    assert not v.ok and v.category == "deny", (name, v)


def test_deny_list_ignores_a_page_claiming_normal_role_and_description_names():
    assert not P.control_verdict({"role": "AXButton", "title": "Pay now", "button_role": "normal"}).ok
    assert not P.control_verdict({"role": "AXButton", "description": "Transfer funds"}).ok
    assert not P.control_verdict({"role": "AXButton", "title": "→", "description": "Pay invoice"}).ok


ALLOW = ["Download", "Download statement", "Export", "Export CSV", "View invoice", "Invoices", "Payments",
         "Transfers", "Subscriptions", "Billing", "Statements", "Last month", "Apply", "Next", "Previous page",
         "Search", "Show more", "Reject all", "Necessary only", "Accept necessary cookies", "Only essential cookies",
         "Use necessary cookies only", "Decline", "Close", "Back", "September 2026", "Account statement", "PDF"]


@pytest.mark.parametrize("name", ALLOW)
def test_reads_and_minimal_cookie_choice_allowed(name):
    v = P.control_verdict({"role": "AXButton", "title": name}, STRIPE)
    assert v.ok, (name, v)


def test_menus_pickers_disabled_switches_links():
    assert P.control_verdict({"role": "AXPopUpButton", "title": "Date range"}).category == "menu"
    assert P.control_verdict({"role": "AXMenuButton", "title": "More"}).ok
    assert not P.control_verdict({"role": "AXButton", "title": "Download", "enabled": False}).ok
    assert not P.control_verdict({"role": "AXSwitch", "title": "Email alerts"}).ok
    # links: off-list target refused, deny path segment refused, statement link allowed
    assert not P.control_verdict({"role": "AXLink", "title": "Help", "url": "https://evil.example/"}, STRIPE).ok
    assert not P.control_verdict({"role": "AXLink", "title": "Plans",
                                  "url": "https://dashboard.stripe.com/settings/billing/upgrade"}, STRIPE).ok
    assert P.control_verdict({"role": "AXLink", "title": "Invoice INV-001",
                              "url": "https://dashboard.stripe.com/invoices/in_1"}, STRIPE).ok


def test_option_verdicts():
    assert P.option_verdict("Last 30 days").ok and P.option_verdict("September 2026").ok
    assert P.option_verdict("CSV").ok and P.option_verdict("Reject all").ok
    for bad in ("Delete", "Pay now", "Upgrade plan", "Accept all", "Sign out"):
        assert not P.option_verdict(bad).ok, bad


@pytest.mark.parametrize("node,ok", [
    ({"role": "AXTextField", "title": "From date"}, True),
    ({"role": "AXTextField", "placeholder": "DD/MM/YYYY"}, True),
    ({"role": "AXSearchField", "title": "Search invoices"}, True),
    ({"role": "AXTextField", "title": "Statement period"}, True),
    ({"role": "AXTextField", "subrole": "AXSecureTextField", "title": "Password"}, False),
    ({"role": "AXTextField", "secure": True}, False),
    ({"role": "AXTextField", "title": "Enter OTP"}, False),
    ({"role": "AXTextField", "title": "One-time code"}, False),
    ({"role": "AXTextField", "title": "Verification code"}, False),
    ({"role": "AXTextField", "title": "Enter captcha"}, False),
    ({"role": "AXTextField", "title": "Customer ID"}, False),
    ({"role": "AXTextField", "title": "Email"}, False),
    ({"role": "AXTextField", "title": "Amount"}, False),
    ({"role": "AXTextField", "title": "Beneficiary account number"}, False),
    ({"role": "AXTextField", "title": "Card number"}, False),
])
def test_field_verdicts(node, ok):
    assert P.field_verdict(node).ok is ok, P.field_verdict(node)
