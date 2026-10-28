"""mail_imap.py: ImapGmail, the PRIMARY real-Gmail MailAdapter (WP3).

Stdlib imaplib over TLS to imap.gmail.com:993, logged in with a Gmail *app
password* fetched from the macOS Keychain at connect time. Mail stays on the
Mac, no Google OAuth verification is needed, and IMAP keeps working while the
screen is locked (UI driving does not), so this is the path for overnight
triage. See mail_adapter.py for the contract and the normalised message.

Safety defaults
  * read-only: the mailbox is opened with EXAMINE and every write
    (modify_labels, move, create_draft) raises ReadOnlyError. Pass
    ``read_only=False`` to allow label changes, moves and drafts.
  * never deletes: there is no delete method; the client never sends EXPUNGE,
    never sets \\Deleted, never adds the Trash/Spam labels and never moves to
    them. Reads use BODY.PEEK[] so they do not mark mail as read.
  * never sends: drafts are APPENDed to [Gmail]/Drafts; sending goes through
    sends.py (prepare -> owner approve -> commit).
  * the app password is never stored on the object, never logged, never put
    in an exception message (imaplib debug output is forced off).
  * TLS with certificate verification (ssl.create_default_context()).
    Plaintext is allowed only to a loopback host and only with
    ``allow_insecure_loopback=True`` (the local fake server in tests).

Gmail extensions used: X-GM-EXT-1 (X-GM-MSGID, X-GM-THRID, X-GM-LABELS,
X-GM-RAW search), UIDPLUS (APPENDUID for draft ids), CONDSTORE (MODSEQ /
CHANGEDSINCE for list_history). Message ids are the hex X-GM-MSGID, the same
id Gmail's web UI and API use; thread ids are the hex X-GM-THRID. The adapter
works on "[Gmail]/All Mail", so a message keeps its UID when labels change.

----------------------------------------------------------------------------
SETUP: connect your real Gmail (one time, about 3 minutes)
----------------------------------------------------------------------------
1. Turn on 2-Step Verification for the Google account (Google Account ->
   Security). App passwords only exist on accounts with 2SV.
2. Create an app password: open https://myaccount.google.com/apppasswords,
   type a name such as "AWOS", press Create. Google shows a 16-letter code
   once (shown in 4 groups; spaces do not matter). Do not paste it into any
   chat, file or terminal history.
   Workspace (company) accounts: the admin may have disabled app passwords or
   IMAP; then this path is unavailable.
3. IMAP: Gmail has IMAP on for personal accounts. If your Gmail settings
   still show "Forwarding and POP/IMAP", make sure IMAP is enabled there.
4. Store the code in the macOS login Keychain under service
   "awos-gmail-imap" and your address as the account. Either command prompts
   for the secret without echoing it:
       security add-generic-password -s awos-gmail-imap -a you@gmail.com -w
   or, with the keyring package installed (pip install keyring):
       python -m keyring set awos-gmail-imap you@gmail.com
5. AWOS reads it with the `keyring` library (pip install keyring). macOS may
   ask once whether Python may read the item; choose "Always Allow".
6. Try it read-only:
       from scaffold.agent.desktop.mail_imap import ImapGmail
       with ImapGmail("you@gmail.com") as mb:
           for m in mb.list_messages("in:inbox", limit=5):
               print(m["date"], m["from"], m["subject"])
   Label changes need ImapGmail("you@gmail.com", read_only=False).
To revoke: delete the app password at the same Google page (AWOS loses access
at once) and remove the Keychain item:
       security delete-generic-password -s awos-gmail-imap -a you@gmail.com
----------------------------------------------------------------------------
"""
from __future__ import annotations

import base64
import email
import email.policy
import imaplib
import re
import ssl
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .mail_adapter import (
    FORBIDDEN_LABELS,
    ForbiddenOperation,
    MailAdapterError,
    Message,
    ReadOnlyError,
    _check_labels,
    list_headers_of,
    normalise_label,
    parse_since,
)
from .mail_text import html_to_text, strip_tracking_pixels

__all__ = ["ImapGmail", "MailCredentialError", "MailAuthError", "keychain_password",
           "KEYCHAIN_SERVICE", "parse_imap", "parse_fetch", "imap_quote", "mutf7_encode",
           "mutf7_decode"]

KEYCHAIN_SERVICE = "awos-gmail-imap"
GMAIL_HOST = "imap.gmail.com"
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
KEEP_HEADERS = ("List-Unsubscribe", "List-Unsubscribe-Post", "List-Id", "List-Post",
                "Reply-To", "Precedence", "Auto-Submitted", "X-Auto-Response-Suppress",
                "Authentication-Results", "Sender")
FETCH_FULL = "(UID X-GM-MSGID X-GM-THRID X-GM-LABELS FLAGS INTERNALDATE BODY.PEEK[])"
FETCH_STATE = "(UID X-GM-MSGID X-GM-LABELS FLAGS)"
_FLAG_MAP = {"\\seen": "seen", "\\flagged": "flagged", "\\answered": "answered",
             "\\draft": "draft"}
_SYS_TO_IMAP = {"INBOX": "\\Inbox", "IMPORTANT": "\\Important", "SENT": "\\Sent",
                "DRAFT": "\\Draft"}


class MailCredentialError(MailAdapterError):
    """No app password available (keyring missing or no Keychain item)."""


class MailAuthError(MailAdapterError):
    """The IMAP server refused the login (message never contains the secret)."""


# --------------------------------------------------------------------------- credentials

def keychain_password(user: str, service: str = KEYCHAIN_SERVICE) -> str:
    """The app password for ``user`` from the macOS Keychain via ``keyring``."""
    try:
        import keyring  # type: ignore
    except ImportError:
        raise MailCredentialError(
            "the 'keyring' package is not installed, so AWOS cannot read the Gmail app "
            "password from the macOS Keychain. Install it with `pip install keyring`, then "
            f"store the password: `security add-generic-password -s {service} -a {user} -w` "
            "(see the setup notes in scaffold/agent/desktop/mail_imap.py).") from None
    try:
        pw = keyring.get_password(service, user)
    except Exception as e:  # backend errors: report the type only
        raise MailCredentialError(f"Keychain lookup failed ({type(e).__name__}) for "
                                  f"service {service!r}, account {user!r}") from None
    if not pw:
        raise MailCredentialError(
            f"no Keychain item for service {service!r}, account {user!r}. Create a Gmail app "
            f"password and run `security add-generic-password -s {service} -a {user} -w` "
            "(setup notes: scaffold/agent/desktop/mail_imap.py).")
    return pw


# --------------------------------------------------------------------------- IMAP parsing

_LP, _RP = object(), object()


def _tokenise(b: bytes) -> List[Any]:
    out: List[Any] = []
    i, n = 0, len(b)
    while i < n:
        c = b[i]
        if c in b" \r\n\t":
            i += 1
        elif c == 0x28:
            out.append(_LP)
            i += 1
        elif c == 0x29:
            out.append(_RP)
            i += 1
        elif c == 0x22:
            j, buf = i + 1, bytearray()
            while j < n and b[j] != 0x22:
                if b[j] == 0x5C and j + 1 < n:
                    buf.append(b[j + 1])
                    j += 2
                    continue
                buf.append(b[j])
                j += 1
            out.append(("s", buf.decode("utf-8", "replace")))
            i = j + 1
        elif c == 0x7B and re.match(rb"\{\d+\+?\}\s*$", b[i:]):
            j = b.index(b"}", i)
            out.append(("litlen", int(b[i + 1:j].rstrip(b"+"))))
            i = j + 1
        else:
            j, depth = i, 0
            while j < n:
                ch = b[j]
                if ch == 0x5B:
                    depth += 1
                elif ch == 0x5D:
                    depth -= 1
                elif depth <= 0 and ch in b" ()\r\n":
                    break
                j += 1
            atom = b[i:j].decode("utf-8", "replace")
            out.append(None if atom.upper() == "NIL" else ("a", atom))
            i = j
    return out


def _stream(segments: Sequence[Any]) -> List[Any]:
    """imaplib response data (bytes, or (prefix, literal) tuples) -> one token stream."""
    toks: List[Any] = []
    for seg in segments:
        if isinstance(seg, tuple):
            prefix, lit = seg[0], seg[1]
            t = _tokenise(prefix)
            if t and isinstance(t[-1], tuple) and t[-1][0] == "litlen":
                t = t[:-1]
            toks.extend(t)
            toks.append(("lit", lit))
        elif isinstance(seg, (bytes, bytearray)):
            toks.extend(_tokenise(bytes(seg)))
    return toks


def _tree(toks: List[Any]) -> List[Any]:
    stack: List[List[Any]] = [[]]
    for t in toks:
        if t is _LP:
            stack.append([])
        elif t is _RP:
            if len(stack) > 1:
                done = stack.pop()
                stack[-1].append(done)
        elif t is None:
            stack[-1].append(None)
        else:
            stack[-1].append(t[1])
    while len(stack) > 1:
        done = stack.pop()
        stack[-1].append(done)
    return stack[0]


def parse_imap(segments: Sequence[Any]) -> List[Any]:
    """Parse IMAP data into nested lists: atoms/quoted -> str, NIL -> None,
    literals -> bytes, parenthesised lists -> list."""
    return _tree(_stream(segments))


def parse_fetch(data: Sequence[Any]) -> List[Dict[str, Any]]:
    """imaplib FETCH data -> [{ITEM: value}] (item names upper-cased)."""
    tree = parse_imap([d for d in data if d is not None])
    out = []
    for node in tree:
        if isinstance(node, list):
            d: Dict[str, Any] = {}
            for k, v in zip(node[0::2], node[1::2]):
                if isinstance(k, str):
                    d[k.upper()] = v
            out.append(d)
    return out


def imap_quote(s: str) -> str:
    """An IMAP quoted string (CR, LF and NUL are refused)."""
    s = str(s)
    if any(c in s for c in "\r\n\x00"):
        raise ValueError("CR/LF/NUL not allowed in an IMAP string")
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def mutf7_encode(s: str) -> str:
    """RFC 3501 modified UTF-7 (mailbox names; Gmail uses it for X-GM-LABELS)."""
    out, buf = [], []

    def flush():
        if buf:
            b = base64.b64encode("".join(buf).encode("utf-16-be")).decode().rstrip("=")
            out.append("&" + b.replace("/", ",") + "-")
            buf.clear()
    for ch in s:
        if 0x20 <= ord(ch) <= 0x7E:
            flush()
            out.append("&-" if ch == "&" else ch)
        else:
            buf.append(ch)
    flush()
    return "".join(out)


def mutf7_decode(s: str) -> str:
    def dec(mo):
        body = mo.group(1)
        if not body:
            return "&"
        b = body.replace(",", "/")
        b += "=" * (-len(b) % 4)
        try:
            return base64.b64decode(b).decode("utf-16-be")
        except Exception:
            return mo.group(0)
    return re.sub(r"&([A-Za-z0-9+,]*)-", dec, s) if "&" in s else s


def _imap_date(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%d-%b-%Y")


# --------------------------------------------------------------------------- adapter

class ImapGmail:
    """MailAdapter over Gmail IMAP. Read-only unless ``read_only=False``."""

    def __init__(self, user: str, *, host: str = GMAIL_HOST, port: int = 993,
                 read_only: bool = True,
                 password_provider: Optional[Callable[[], str]] = None,
                 keychain_service: str = KEYCHAIN_SERVICE,
                 mailbox: str = "[Gmail]/All Mail", drafts_mailbox: str = "[Gmail]/Drafts",
                 ssl_context: Optional[ssl.SSLContext] = None,
                 allow_insecure_loopback: bool = False, timeout: float = 30.0,
                 snapshot_limit: int = 500, require_gmail_ext: bool = True):
        if allow_insecure_loopback and host not in LOOPBACK:
            raise ValueError("plaintext IMAP is only allowed to a loopback host")
        self.user = user
        self.host, self.port = host, port
        self.read_only = read_only
        self._password_provider = password_provider or (
            lambda: keychain_password(user, keychain_service))
        self.mailbox, self.drafts_mailbox = mailbox, drafts_mailbox
        self._ssl_context = ssl_context
        self._insecure = allow_insecure_loopback
        self.timeout = timeout
        self.snapshot_limit = snapshot_limit
        self.require_gmail_ext = require_gmail_ext
        self._imap: Optional[imaplib.IMAP4] = None
        self._caps: Tuple[str, ...] = ()
        self._sel: Dict[str, int] = {}
        self._drafts: List[Dict[str, Any]] = []

    def __repr__(self) -> str:
        return (f"ImapGmail(user={self.user!r}, host={self.host!r}, port={self.port}, "
                f"read_only={self.read_only})")

    # -- connection
    def __enter__(self) -> "ImapGmail":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def connect(self) -> "ImapGmail":
        if self._imap is not None:
            return self
        if self._insecure:
            imap = imaplib.IMAP4(self.host, self.port, timeout=self.timeout)
        else:
            ctx = self._ssl_context or ssl.create_default_context()
            imap = imaplib.IMAP4_SSL(self.host, self.port, ssl_context=ctx, timeout=self.timeout)
        imap.debug = 0
        pw = None
        try:
            pw = self._password_provider()
            if not pw:
                raise MailCredentialError("password provider returned nothing")
            secret = str(pw).replace(" ", "")
            try:
                imap.login(self.user, secret)
            except imaplib.IMAP4.error as e:
                msg = str(e).replace(secret, "***")
                if pw:
                    msg = msg.replace(str(pw), "***")
                raise MailAuthError(f"IMAP login refused for {self.user}: {msg[:200]}") from None
            finally:
                secret = None  # noqa: F841  drop the reference
        except BaseException:
            try:
                imap.shutdown()
            except Exception:
                pass
            raise
        finally:
            pw = None
        caps = imap.capabilities
        self._caps = tuple(str(c).upper() for c in caps)
        if self.require_gmail_ext and "X-GM-EXT-1" not in self._caps:
            imap.logout()
            raise MailAdapterError("server lacks the Gmail X-GM-EXT-1 extension")
        self._imap = imap
        self._select()
        return self

    def close(self) -> None:
        if self._imap is not None:
            try:
                self._imap.logout()
            except Exception:
                pass
            self._imap = None

    def _conn(self) -> imaplib.IMAP4:
        if self._imap is None:
            self.connect()
        assert self._imap is not None
        return self._imap

    def _select(self) -> Dict[str, int]:
        imap = self._imap
        assert imap is not None
        typ, data = imap.select(imap_quote(self.mailbox), readonly=self.read_only)
        if typ != "OK":
            raise MailAdapterError(f"cannot open {self.mailbox!r}: {data!r}")
        sel = {"exists": int(data[0] or 0) if data and data[0] else 0}
        for code in ("UIDVALIDITY", "UIDNEXT", "HIGHESTMODSEQ"):
            v = imap.untagged_responses.get(code)
            if v:
                try:
                    sel[code.lower()] = int(v[-1])
                except (TypeError, ValueError):
                    pass
        self._sel = sel
        return sel

    def _check(self, typ: str, data: Any, what: str) -> Any:
        if typ != "OK":
            raise MailAdapterError(f"{what} failed: {data!r}"[:300])
        return data

    # -- low-level helpers
    def _search(self, *criteria: str, literal: Optional[bytes] = None) -> List[int]:
        imap = self._conn()
        if literal is not None:
            imap.literal = literal
        typ, data = imap.uid("SEARCH", *criteria)
        self._check(typ, data, "SEARCH")
        out: List[int] = []
        for d in data or []:
            if d:
                out.extend(int(x) for x in d.split())
        return sorted(set(out))

    def _fetch(self, uids: Sequence[int], items: str, modifier: str = "") -> List[Dict[str, Any]]:
        if not uids:
            return []
        imap = self._conn()
        uidset = ",".join(str(u) for u in sorted(uids))
        args = [uidset, items] + ([modifier] if modifier else [])
        typ, data = imap.uid("FETCH", *args)
        self._check(typ, data, "FETCH")
        return [r for r in parse_fetch(data) if "UID" in r]

    def _store(self, uid: int, item: str, values: Sequence[str]) -> None:
        # The one place that writes message state. \Deleted can never pass.
        joined = " ".join(values)
        if "\\deleted" in joined.lower() or "deleted" in item.lower():
            raise ForbiddenOperation("setting \\Deleted is never allowed")
        if self.read_only:
            raise ReadOnlyError("adapter is read-only")
        typ, data = self._conn().uid("STORE", str(uid), item, "(" + joined + ")")
        self._check(typ, data, "STORE")

    def _uid_for(self, id: str) -> int:
        try:
            msgid = int(str(id), 16)
        except ValueError:
            raise KeyError(id) from None
        uids = self._search("X-GM-MSGID", str(msgid))
        if not uids:
            raise KeyError(id)
        return uids[-1]

    def _require_write(self, what: str) -> None:
        if self.read_only:
            raise ReadOnlyError(f"{what}: adapter is read-only (construct with read_only=False)")

    # -- normalisation
    @staticmethod
    def _labels_flags(rec: Dict[str, Any]) -> Tuple[List[str], List[str]]:
        raw_labels = rec.get("X-GM-LABELS") or []
        raw_flags = rec.get("FLAGS") or []
        if not isinstance(raw_labels, list):
            raw_labels = [raw_labels]
        if not isinstance(raw_flags, list):
            raw_flags = [raw_flags]
        flags = sorted({_FLAG_MAP[f.lower()] for f in raw_flags
                        if isinstance(f, str) and f.lower() in _FLAG_MAP})
        labels = []
        for lb in raw_labels:
            if isinstance(lb, (bytes, bytearray)):
                lb = lb.decode("utf-8", "replace")
            if isinstance(lb, str):
                n = normalise_label(mutf7_decode(lb))
                if n not in labels:
                    labels.append(n)
        if "flagged" in flags and "STARRED" not in labels:
            labels.append("STARRED")
        return labels, flags

    def _normalise(self, rec: Dict[str, Any]) -> Message:
        raw = rec.get("BODY[]") or rec.get("RFC822") or b""
        if isinstance(raw, str):
            raw = raw.encode("utf-8", "replace")
        em = email.message_from_bytes(raw, policy=email.policy.default)
        name, addr = parseaddr(str(em.get("From", "")))
        try:
            dt = parsedate_to_datetime(str(em.get("Date")))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            date = dt.astimezone(timezone.utc).isoformat()
        except Exception:
            idate = rec.get("INTERNALDATE")
            try:
                date = datetime.strptime(str(idate), "%d-%b-%Y %H:%M:%S %z").astimezone(
                    timezone.utc).isoformat()
            except Exception:
                date = ""
        body, html = "", None
        part = em.get_body(preferencelist=("plain", "html"))
        if part is not None:
            try:
                content = part.get_content()
            except Exception:
                payload = part.get_payload(decode=True) or b""
                content = payload.decode("utf-8", "replace")
            content = content.replace("\r\n", "\n")
            if part.get_content_subtype() == "html":
                html = content
                body = html_to_text(strip_tracking_pixels(content, block_remote=True))
            else:
                body = content[:-1] if content.endswith("\n") else content
        headers = {}
        for h in KEEP_HEADERS:
            if em.get(h) is not None:
                headers[h] = str(em.get(h))
        labels, flags = self._labels_flags(rec)
        msgid = int(rec.get("X-GM-MSGID") or 0)
        thrid = int(rec.get("X-GM-THRID") or 0)
        m: Message = {
            "id": format(msgid, "x"), "thread_id": format(thrid, "x"), "date": date,
            "from": f"{name} <{addr}>" if name else addr, "from_name": name, "from_addr": addr,
            "to": str(em.get("To", "")), "subject": str(em.get("Subject", "")), "body": body,
            "headers": headers, "labels": labels, "flags": flags,
            "list_headers": list_headers_of(headers),
            "message_id": str(em.get("Message-ID", "")), "uid": int(rec["UID"]),
        }
        cc = em.get("Cc")
        if cc:
            m["cc"] = [f"{n} <{a}>" if n else a for n, a in getaddresses([str(cc)])]
        if html is not None:
            m["html"] = html
        return m

    # -- MailAdapter: reads
    def list_messages(self, query: str = "", since: Optional[str] = None,
                      limit: int = 50) -> List[Message]:
        since_dt = parse_since(since)
        crit: List[str] = []
        literal = None
        if since_dt is not None:
            crit += ["SINCE", _imap_date(since_dt)]
        q = (query or "").replace("\r", " ").replace("\n", " ").strip()
        if q:
            if q.isascii():
                crit += ["X-GM-RAW", imap_quote(q)]
            else:
                crit = ["CHARSET", "UTF-8"] + crit + ["X-GM-RAW"]
                literal = q.encode("utf-8")
        uids = self._search(*(crit or ["ALL"]), literal=literal)
        if limit:
            uids = uids[-int(limit):]
        msgs = [self._normalise(r) for r in self._fetch(uids, FETCH_FULL)]
        if since_dt is not None:
            msgs = [m for m in msgs if m["date"] and parse_since(m["date"]) >= since_dt]
        msgs.sort(key=lambda m: (m["date"], m["uid"]), reverse=True)
        return msgs

    def get_message(self, id: str) -> Message:
        recs = self._fetch([self._uid_for(id)], FETCH_FULL)
        if not recs:
            raise KeyError(id)
        return self._normalise(recs[0])

    # -- MailAdapter: writes (all gated by read_only)
    def modify_labels(self, id: str, add: Sequence[str] = (),
                      remove: Sequence[str] = ()) -> Message:
        self._require_write("modify_labels")
        add_n = _check_labels(add)
        rem_n = [normalise_label(x) for x in remove]
        uid = self._uid_for(id)
        for sign, labels in (("+", add_n), ("-", rem_n)):
            gm: List[str] = []
            for lb in labels:
                if lb == "STARRED":
                    self._store(uid, sign + "FLAGS.SILENT", ["\\Flagged"])
                elif lb == "UNREAD":
                    self._store(uid, ("-" if sign == "+" else "+") + "FLAGS.SILENT", ["\\Seen"])
                else:
                    gm.append(imap_quote(_SYS_TO_IMAP.get(lb) or mutf7_encode(lb)))
            if gm:
                self._store(uid, sign + "X-GM-LABELS.SILENT", gm)
        return self.get_message(id)

    def move(self, id: str, folder: str) -> Message:
        self._require_write("move")
        f = normalise_label(folder)
        if f.upper() in FORBIDDEN_LABELS:
            raise ForbiddenOperation(f"move to {folder!r} would delete or spam mail; refused")
        uid = self._uid_for(id)
        inbox = imap_quote("\\Inbox")
        if f.upper() == "INBOX":
            self._store(uid, "+X-GM-LABELS.SILENT", [inbox])
        else:
            if f.upper() not in ("ARCHIVE", "ALL MAIL"):
                self._store(uid, "+X-GM-LABELS.SILENT",
                            [imap_quote(_SYS_TO_IMAP.get(f) or mutf7_encode(f))])
            self._store(uid, "-X-GM-LABELS.SILENT", [inbox])
        return self.get_message(id)

    def create_draft(self, reply_to_id: str, body: str) -> Dict[str, Any]:
        self._require_write("create_draft")
        orig = self.get_message(reply_to_id)
        reply_to = orig["headers"].get("Reply-To") or orig["from_addr"]
        subj = orig["subject"]
        em = EmailMessage()
        em["From"] = self.user
        em["To"] = reply_to
        em["Subject"] = subj if subj.lower().startswith("re:") else "Re: " + subj
        if orig.get("message_id"):
            em["In-Reply-To"] = orig["message_id"]
            em["References"] = orig["message_id"]
        em.set_content(str(body))
        imap = self._conn()
        typ, data = imap.append(imap_quote(self.drafts_mailbox), "(\\Draft)",
                                imaplib.Time2Internaldate(time.time()), em.as_bytes())
        self._check(typ, data, "APPEND")
        did = None
        for d in data or []:
            mo = re.search(rb"APPENDUID\s+(\d+)\s+(\d+)", d or b"")
            if mo:
                did = f"{int(mo.group(1))}:{int(mo.group(2))}"
        d = {"draft_id": did, "reply_to_id": reply_to_id, "thread_id": orig["thread_id"],
             "to": reply_to, "subject": str(em["Subject"]), "body": str(body), "status": "draft"}
        self._drafts.append(dict(d))
        return d

    # -- MailAdapter: sync
    def _token(self) -> str:
        s = self._sel
        return "v1:%d:%d:%d" % (s.get("uidvalidity", 0), max(s.get("uidnext", 1) - 1, 0),
                                s.get("highestmodseq", 0))

    def list_history(self, since_token: Optional[str] = None) -> Dict[str, Any]:
        """Changes since ``since_token``: new messages ("added") and label/flag
        changes ("changed", needs CONDSTORE). No token or an invalid one (e.g.
        UIDVALIDITY changed) -> ``reset: True``: do a full list_messages().
        Messages expunged by the user elsewhere are not reported."""
        self._conn()
        self._select()
        token = self._token()
        if not since_token:
            return {"token": token, "changes": [], "reset": True}
        try:
            ver, validity, maxuid, modseq = since_token.split(":")
            validity, maxuid, modseq = int(validity), int(maxuid), int(modseq)
            assert ver == "v1"
        except Exception:
            return {"token": token, "changes": [], "reset": True}
        if validity != self._sel.get("uidvalidity", 0):
            return {"token": token, "changes": [], "reset": True}
        changes: List[Dict[str, Any]] = []
        new = [u for u in self._search("UID", f"{maxuid + 1}:*") if u > maxuid]
        for r in self._fetch(new, "(UID X-GM-MSGID X-GM-LABELS FLAGS MODSEQ)"):
            labels, flags = self._labels_flags(r)
            changes.append({"type": "added", "id": format(int(r["X-GM-MSGID"]), "x"),
                            "history_id": str(self._modseq(r)), "labels": labels, "flags": flags})
        partial = "CONDSTORE" not in self._caps
        if not partial and maxuid > 0:
            for r in self._fetch_range(f"1:{maxuid}", "(UID X-GM-MSGID X-GM-LABELS FLAGS MODSEQ)",
                                       f"(CHANGEDSINCE {modseq})"):
                labels, flags = self._labels_flags(r)
                changes.append({"type": "changed", "id": format(int(r["X-GM-MSGID"]), "x"),
                                "history_id": str(self._modseq(r)), "labels": labels,
                                "flags": flags})
        changes.sort(key=lambda c: int(c["history_id"] or 0))
        out = {"token": token, "changes": changes, "reset": False}
        if partial:
            out["partial"] = True
        return out

    @staticmethod
    def _modseq(r: Dict[str, Any]) -> int:
        v = r.get("MODSEQ")
        if isinstance(v, list):
            v = v[0] if v else 0
        try:
            return int(v or 0)
        except (TypeError, ValueError):
            return 0

    def _fetch_range(self, uidset: str, items: str, modifier: str = "") -> List[Dict[str, Any]]:
        args = [uidset, items] + ([modifier] if modifier else [])
        typ, data = self._conn().uid("FETCH", *args)
        self._check(typ, data, "FETCH")
        return [r for r in parse_fetch(data) if "UID" in r]

    def snapshot(self) -> Dict[str, Any]:
        """Labels and flags of the newest ``snapshot_limit`` messages."""
        self._conn()
        self._select()
        uids = self._search("ALL")[-self.snapshot_limit:]
        msgs = {}
        for r in self._fetch(uids, FETCH_STATE):
            labels, flags = self._labels_flags(r)
            msgs[format(int(r["X-GM-MSGID"]), "x")] = {"labels": sorted(labels),
                                                       "flags": sorted(flags)}
        return {"token": self._token(), "messages": msgs, "drafts": [dict(d) for d in self._drafts]}
