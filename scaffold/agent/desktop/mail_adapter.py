"""mail_adapter.py: the MailAdapter contract (WP3) and its non-network backends.

One interface for reading and organising a mailbox, whatever the transport:

  FakeMailbox    reads a testbeds/email_triage mailbox dir (inbox.json); state
                 changes live in memory. Used by tests and testbeds.
  ImapGmail      mail_imap.py. The PRIMARY real-Gmail path: IMAP over TLS with
                 a Gmail app password from the macOS Keychain. Works while the
                 screen is locked (overnight runs), unlike UI driving.
  BrowserGmail   documented stub for the secondary path (Gmail in Chrome via
                 the macOS Accessibility tree). Not implemented.

Contract (every backend):

  list_messages(query="", since=None, limit=50) -> List[Message]   newest first
  get_message(id) -> Message                                       KeyError if unknown
  modify_labels(id, add=(), remove=()) -> Message                  write
  move(id, folder) -> Message                                      write
  create_draft(reply_to_id, body) -> Dict                          write, DRAFT ONLY
  list_history(since_token=None) -> {"token", "changes", "reset"}  incremental sync
      no / unknown token -> reset=True (do a full list_messages, keep the token);
      changes: [{"type": "added" | "changed" | "draft", "id", "history_id",
                 "labels", "flags", ...}] oldest first
  snapshot() -> {"token", "messages": {id: {"labels", "flags"}}, "drafts": [...]}

Rules every backend keeps:
  * read-only by default: writes raise ReadOnlyError until the adapter is built
    with ``read_only=False``;
  * there is no delete / expunge / trash / send method, and move() and
    modify_labels() refuse Trash and Spam (both delete in Gmail);
  * sending is NOT part of the adapter: a draft is prepared here and the send
    goes through sends.py (prepare -> owner approve -> commit once).

Normalised message (the testbed's inbox.json message shape, plus fields):

  id, thread_id, date (ISO 8601 with offset), from, from_name, from_addr, to,
  subject, body (plain text), headers ({name: value}; the testbed keeps only
  extra headers such as List-Unsubscribe / List-Id)
  + labels        Gmail-API-style names: INBOX, IMPORTANT, STARRED, SENT, DRAFT,
                  CATEGORY_*, or user label names
  + flags         subset of {"seen", "flagged", "answered", "draft"}
  + list_headers  {"List-Id": ..., "List-Unsubscribe": ...} when present

Fields a consumer of inbox.json reads (id, from, subject, body, ...) are byte
identical through FakeMailbox, so the email-triage agent scores the same
through the adapter as on the direct path.

Correction capture: take ``snapshot()`` before the agent acts and again later;
``diff_snapshots(before, after)`` lists what changed per message. Changes that
match the agent's own action log are the agent's; the rest are the user's
corrections (WP2 consumes this).
"""
from __future__ import annotations

import copy
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Protocol, Sequence, runtime_checkable

Message = Dict[str, Any]

__all__ = [
    "Message", "MailAdapter", "MailAdapterError", "ReadOnlyError", "ForbiddenOperation",
    "FakeMailbox", "BrowserGmail", "diff_snapshots", "normalise_label", "INBOX_KEYS",
    "FORBIDDEN_LABELS", "parse_since",
]

# The keys of a testbed inbox.json message, in its order.
INBOX_KEYS = ("id", "thread_id", "date", "from", "from_name", "from_addr", "to", "subject",
              "body", "headers")
LIST_HEADER_NAMES = ("List-Id", "List-Unsubscribe", "List-Unsubscribe-Post", "List-Post")
FLAG_NAMES = ("seen", "flagged", "answered", "draft")
# Labels/folders that would delete or hide mail as spam: never set by the adapter.
FORBIDDEN_LABELS = frozenset({"TRASH", "SPAM", "BIN", "DELETED"})
SYSTEM_LABELS = ("INBOX", "IMPORTANT", "STARRED", "SENT", "DRAFT", "UNREAD")


class MailAdapterError(RuntimeError):
    """Base error for mail adapters."""


class ReadOnlyError(MailAdapterError, PermissionError):
    """A write was attempted on an adapter opened read-only (the default)."""


class ForbiddenOperation(MailAdapterError, PermissionError):
    """An operation the adapter never performs (delete, trash, spam, send)."""


@runtime_checkable
class MailAdapter(Protocol):
    read_only: bool

    def list_messages(self, query: str = "", since: Optional[str] = None,
                      limit: int = 50) -> List[Message]: ...

    def get_message(self, id: str) -> Message: ...

    def modify_labels(self, id: str, add: Sequence[str] = (),
                      remove: Sequence[str] = ()) -> Message: ...

    def move(self, id: str, folder: str) -> Message: ...

    def create_draft(self, reply_to_id: str, body: str) -> Dict[str, Any]: ...

    def list_history(self, since_token: Optional[str] = None) -> Dict[str, Any]: ...

    def snapshot(self) -> Dict[str, Any]: ...


# --------------------------------------------------------------------------- helpers

def normalise_label(name: str) -> str:
    """Map IMAP / Gmail spellings to the API-style names used in Message.labels."""
    n = str(name).strip()
    bare = n.lstrip("\\")
    up = bare.upper()
    if up in ("INBOX", "IMPORTANT", "STARRED", "SENT", "DRAFT", "DRAFTS", "TRASH", "SPAM",
              "JUNK", "BIN", "UNREAD", "DELETED"):
        return {"DRAFTS": "DRAFT", "JUNK": "SPAM"}.get(up, up)
    if up.startswith("[GMAIL]/"):
        return normalise_label(bare[8:])
    if up.startswith("CATEGORY_"):
        return up
    return n


def _check_labels(labels: Iterable[str]) -> List[str]:
    out = []
    for lb in labels:
        n = normalise_label(lb)
        if n.upper() in FORBIDDEN_LABELS:
            raise ForbiddenOperation(f"label {lb!r} would delete or spam mail; refused")
        out.append(n)
    return out


def parse_since(since: Optional[str]) -> Optional[datetime]:
    """ISO date or datetime -> aware datetime (UTC assumed when naive)."""
    if not since:
        return None
    s = str(since).strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def list_headers_of(headers: Dict[str, str]) -> Dict[str, str]:
    low = {k.lower(): (k, v) for k, v in (headers or {}).items()}
    out = {}
    for name in LIST_HEADER_NAMES:
        if name.lower() in low:
            out[name] = low[name.lower()][1]
    return out


def diff_snapshots(before: Dict[str, Any], after: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Per-message label / flag changes between two snapshot() results.

    Returns [{"id", "labels_added", "labels_removed", "flags_added",
    "flags_removed", "new": bool, "gone": bool}] for messages that changed.
    """
    b, a = before.get("messages", {}), after.get("messages", {})
    out = []
    for mid in sorted(set(b) | set(a)):
        x, y = b.get(mid), a.get(mid)
        xl, yl = set((x or {}).get("labels", [])), set((y or {}).get("labels", []))
        xf, yf = set((x or {}).get("flags", [])), set((y or {}).get("flags", []))
        if x is not None and y is not None and xl == yl and xf == yf:
            continue
        out.append({"id": mid, "labels_added": sorted(yl - xl), "labels_removed": sorted(xl - yl),
                    "flags_added": sorted(yf - xf), "flags_removed": sorted(xf - yf),
                    "new": x is None, "gone": y is None})
    return out


# --------------------------------------------------------------------------- query matching

_TOKEN = re.compile(r'(-?)(\w+:)?("([^"]*)"|\S+)')


def _match_query(m: Message, query: str) -> bool:
    """A small Gmail-search subset: words, "phrases", from:, to:, subject:,
    label:, in:inbox|anywhere|archive, is:unread|read|starred|important,
    has:list. A leading '-' negates a term. All terms must match."""
    if not query or not query.strip():
        return True
    labels = {x.lower() for x in m.get("labels", [])}
    flags = set(m.get("flags", []))
    hay = (m.get("from", "") + "\n" + m.get("subject", "") + "\n" + m.get("body", "")).lower()
    for neg, op, raw, phrase in _TOKEN.findall(query):
        val = (phrase if phrase else raw).lower()
        op = (op or "").lower()
        if op == "from:":
            ok = val in (m.get("from", "") or "").lower()
        elif op == "to:":
            ok = val in str(m.get("to", "")).lower()
        elif op == "subject:":
            ok = val in (m.get("subject", "") or "").lower()
        elif op == "label:":
            ok = val in labels or val.replace("-", " ") in labels
        elif op == "in:":
            ok = (val == "anywhere" or (val == "inbox" and "inbox" in labels)
                  or (val == "archive" and "inbox" not in labels) or val in labels)
        elif op == "is:":
            ok = {"unread": "seen" not in flags, "read": "seen" in flags,
                  "starred": "starred" in labels or "flagged" in flags,
                  "important": "important" in labels}.get(val, val in labels)
        elif op == "has:":
            ok = val == "list" and bool(m.get("list_headers"))
        else:
            ok = ((op or "") + val) in hay
        if bool(neg) == ok:
            return False
    return True


# --------------------------------------------------------------------------- FakeMailbox

class FakeMailbox:
    """MailAdapter over a testbeds/email_triage ``mailbox`` dir (inbox.json).

    The files are never written. Labels, flags, moves and drafts are kept in
    memory, with a history log so list_history works like Gmail's. Every
    message starts with labels ["INBOX"] and no flags (unread).

    ``deliver(msg)`` adds a new message (for incremental-sync tests).
    """

    def __init__(self, mailbox_dir, *, read_only: bool = True):
        self.mailbox_dir = Path(mailbox_dir)
        data = json.loads((self.mailbox_dir / "inbox.json").read_text())
        self.account = data.get("account", {})
        self.read_only = read_only
        self._msgs: Dict[str, Message] = {}
        self._order: List[str] = []
        self._state: Dict[str, Dict[str, List[str]]] = {}
        self._drafts: List[Dict[str, Any]] = []
        self._history: List[Dict[str, Any]] = []
        self._hid = 0
        # history ids are assigned oldest first, as a real mailbox would
        for m in sorted(data["messages"], key=lambda x: x.get("date", "")):
            self._add(m)

    # -- internals
    def _add(self, m: Message) -> None:
        mid = str(m["id"])
        self._msgs[mid] = copy.deepcopy(m)
        self._order.append(mid)
        self._state[mid] = {"labels": ["INBOX"], "flags": []}
        self._log("added", mid)

    def _log(self, kind: str, mid: str, **extra) -> None:
        self._hid += 1
        st = self._state[mid]
        self._history.append({"history_id": str(self._hid), "type": kind, "id": mid,
                              "labels": list(st["labels"]), "flags": list(st["flags"]), **extra})

    def _require_write(self, what: str) -> None:
        if self.read_only:
            raise ReadOnlyError(f"{what}: adapter is read-only (construct with read_only=False)")

    def _view(self, mid: str) -> Message:
        m = copy.deepcopy(self._msgs[mid])
        st = self._state[mid]
        m["labels"] = list(st["labels"])
        m["flags"] = list(st["flags"])
        m["list_headers"] = list_headers_of(m.get("headers") or {})
        return m

    # -- reads
    def list_messages(self, query: str = "", since: Optional[str] = None,
                      limit: int = 50) -> List[Message]:
        since_dt = parse_since(since)
        ids = sorted(self._order, key=lambda i: self._msgs[i].get("date", ""), reverse=True)
        out = []
        for mid in ids:
            m = self._view(mid)
            if since_dt is not None and parse_since(m.get("date")) < since_dt:
                continue
            if not _match_query(m, query):
                continue
            out.append(m)
            if limit and len(out) >= limit:
                break
        return out

    def get_message(self, id: str) -> Message:
        if id not in self._msgs:
            raise KeyError(id)
        return self._view(id)

    # -- writes
    def modify_labels(self, id: str, add: Sequence[str] = (),
                      remove: Sequence[str] = ()) -> Message:
        self._require_write("modify_labels")
        if id not in self._msgs:
            raise KeyError(id)
        add_n, rem_n = _check_labels(add), [normalise_label(x) for x in remove]
        st = self._state[id]
        for lb in add_n:
            if lb == "UNREAD":
                if "seen" in st["flags"]:
                    st["flags"].remove("seen")
            elif lb not in st["labels"]:
                st["labels"].append(lb)
        for lb in rem_n:
            if lb == "UNREAD":
                if "seen" not in st["flags"]:
                    st["flags"].append("seen")
            elif lb in st["labels"]:
                st["labels"].remove(lb)
        self._log("changed", id, added=add_n, removed=rem_n)
        return self._view(id)

    def move(self, id: str, folder: str) -> Message:
        self._require_write("move")
        if id not in self._msgs:
            raise KeyError(id)
        f = normalise_label(folder)
        if f.upper() in FORBIDDEN_LABELS:
            raise ForbiddenOperation(f"move to {folder!r} would delete or spam mail; refused")
        st = self._state[id]
        if f.upper() == "INBOX":
            if "INBOX" not in st["labels"]:
                st["labels"].append("INBOX")
        else:
            if "INBOX" in st["labels"]:
                st["labels"].remove("INBOX")
            if f.upper() not in ("ARCHIVE", "ALL MAIL") and f not in st["labels"]:
                st["labels"].append(f)
        self._log("changed", id, folder=f)
        return self._view(id)

    def create_draft(self, reply_to_id: str, body: str) -> Dict[str, Any]:
        self._require_write("create_draft")
        if reply_to_id not in self._msgs:
            raise KeyError(reply_to_id)
        orig = self._msgs[reply_to_id]
        subj = orig.get("subject", "")
        d = {"draft_id": "d%04d" % (len(self._drafts) + 1), "reply_to_id": reply_to_id,
             "thread_id": orig.get("thread_id"), "to": orig.get("from_addr", ""),
             "subject": subj if subj.lower().startswith("re:") else "Re: " + subj,
             "body": str(body), "status": "draft"}
        self._drafts.append(d)
        self._log("draft", reply_to_id, draft_id=d["draft_id"])
        return dict(d)

    # -- sync
    def list_history(self, since_token: Optional[str] = None) -> Dict[str, Any]:
        try:
            start = int(since_token) if since_token else -1
        except ValueError:
            start = -1
        if start < 0 or start > self._hid:
            return {"token": str(self._hid), "changes": [], "reset": True}
        changes = [dict(h) for h in self._history if int(h["history_id"]) > start]
        return {"token": str(self._hid), "changes": changes, "reset": False}

    def snapshot(self) -> Dict[str, Any]:
        return {"token": str(self._hid),
                "messages": {mid: {"labels": sorted(st["labels"]), "flags": sorted(st["flags"])}
                             for mid, st in self._state.items()},
                "drafts": [dict(d) for d in self._drafts]}

    # -- test helper
    def deliver(self, m: Message) -> None:
        """Add a new incoming message (not a write by the agent; allowed read-only)."""
        if str(m["id"]) in self._msgs:
            raise ValueError(f"duplicate id {m['id']}")
        self._add(m)

    def __repr__(self) -> str:
        return f"FakeMailbox({str(self.mailbox_dir)!r}, read_only={self.read_only})"


# --------------------------------------------------------------------------- BrowserGmail

class BrowserGmail:
    """SECONDARY path, not implemented: Gmail in Chrome driven via macOS AX.

    Why secondary: synthetic CGEvents and focus changes do not reach apps while
    the screen is locked or the display sleeps, and Chrome throttles background
    tabs, so UI driving cannot do overnight triage. ImapGmail is primary; this
    path is for visible, user-present tasks and teach-once demos.

    How it would work (for whoever implements it, on ui_session.py):
      * Needs the Accessibility permission for the AWOS process, and Chrome's
        AX tree switched on (set AXEnhancedUserInterface / AXManualAccessibility
        on the Chrome app element; see ax_observe.py).
      * Observe with ax_observe.Observer (stable element ids, diffs), act only
        through ui_session.UISession leases and guarded actions; app_policy
        must list Chrome at tier "click" or higher for mail.google.com.
      * list_messages: type the Gmail operator query into the search box
        (role=combobox "Search mail"), read rows of the grid (role=row) ->
        sender, subject, snippet, date; ids from the row's data attributes are
        not exposed via AX, so key rows by (thread subject, date) and open the
        thread for its URL (#inbox/<thread id>).
      * get_message: open the row, read the message body region (role=listitem
        within role=main), expand trimmed content ("Show trimmed content").
      * modify_labels / move: toolbar buttons "Labels" / "Move to" -> menu items
        by name; refuse Delete, Report spam and "Move to Bin" (never click them).
      * create_draft: "Reply" button, type into the editable body, close the
        compose (Gmail autosaves the draft). Never press Send: sends go
        through sends.py's two-phase flow and the owner.
      * list_history / snapshot: no AX equivalent; diff successive
        list_messages() results.
    """

    read_only = True

    def __init__(self, *_, **__):
        raise NotImplementedError(
            "BrowserGmail is a documented stub (secondary path). Use ImapGmail "
            "(scaffold/agent/desktop/mail_imap.py) for real Gmail, or FakeMailbox for tests.")

    def list_messages(self, query: str = "", since: Optional[str] = None,
                      limit: int = 50) -> List[Message]:
        raise NotImplementedError

    def get_message(self, id: str) -> Message:
        raise NotImplementedError

    def modify_labels(self, id: str, add: Sequence[str] = (),
                      remove: Sequence[str] = ()) -> Message:
        raise NotImplementedError

    def move(self, id: str, folder: str) -> Message:
        raise NotImplementedError

    def create_draft(self, reply_to_id: str, body: str) -> Dict[str, Any]:
        raise NotImplementedError

    def list_history(self, since_token: Optional[str] = None) -> Dict[str, Any]:
        raise NotImplementedError

    def snapshot(self) -> Dict[str, Any]:
        raise NotImplementedError
