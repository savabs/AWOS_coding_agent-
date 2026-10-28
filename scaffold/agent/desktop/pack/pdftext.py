"""
pdftext.py: text extraction for the Month-end Document Pack verifier.

    res = extract("statement.pdf", passwords=[("keychain:hdfc", pw)],
                  templates=["<pan_lower><dob_ddmmyyyy>"],
                  template_vars=derive_vars(pan="ABCDE1234F", dob="1990-01-01"))
    res.text, res.locked, res.unlocked_by, res.backend

Backends, tried in order:
  pdfkit   macOS PDFKit through PyObjC (``import Quartz``; Quartz.PDFDocument)
  pypdf    pure-Python fallback, used only if ``pypdf`` is importable
  none     no backend available: the result says so (``error="no_backend"``)
           and the caller degrades to "unverified". Encryption is still
           detected from the raw bytes (``/Encrypt`` in the trailer).

Passwords: the caller resolves them (keychain via the secrets broker) and passes
``(label, value)`` pairs. Only the LABEL is ever recorded (``unlocked_by``);
values never appear in results, reprs, exceptions or logs. Templates such as
``"<pan_lower><dob_ddmmyyyy>"`` are expanded from caller-supplied variables;
this module ships the mechanism only, not any bank's actual rule. A template
whose placeholders are not all available is skipped.

No OCR: a PDF whose pages carry no text layer (scanned images) comes back
with ``text == ""`` and ``pages > 0``; the verifier reports it as unverified.
"""
from __future__ import annotations

import datetime as _dt
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

__all__ = [
    "PdfText",
    "extract",
    "expand_templates",
    "derive_vars",
    "looks_encrypted",
    "normalize_text",
    "available_backends",
]

_PLACEHOLDER = re.compile(r"<([a-z0-9_]+)>")


@dataclass
class PdfText:
    text: str = ""
    pages: int = 0
    encrypted: bool = False
    locked: bool = False
    unlocked_by: Optional[str] = None  # label of the password/template, never the value
    backend: str = "none"
    error: Optional[str] = None  # open_failed | no_backend | <backend error class>
    page_chars: List[int] = field(default_factory=list)
    attempts: int = 0  # number of candidate passwords tried

    @property
    def ok(self) -> bool:
        return self.error is None and not self.locked

    @property
    def has_text(self) -> bool:
        return bool(self.text.strip())


# ── text helpers ─────────────────────────────────────────────────────────────

def normalize_text(s: str) -> str:
    """NFKC (ligatures, full-width digits), NBSP -> space, unify newlines.

    The rupee sign survives NFKC unchanged."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = s.replace(" ", " ").replace(" ", " ").replace(" ", " ")
    s = s.replace("\r\n", "\n").replace("\r", "\n").replace(" ", "\n")
    return s


def looks_encrypted(data: bytes) -> bool:
    """Byte-level check: an encryption dictionary is referenced by the trailer."""
    return b"/Encrypt" in data


# ── password templates ──────────────────────────────────────────────────────

def _as_date(v) -> Optional[_dt.date]:
    if isinstance(v, _dt.datetime):
        return v.date()
    if isinstance(v, _dt.date):
        return v
    if isinstance(v, str):
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d%m%Y"):
            try:
                return _dt.datetime.strptime(v.strip(), fmt).date()
            except ValueError:
                pass
    return None


def derive_vars(
    *,
    pan: Optional[str] = None,
    dob=None,
    name: Optional[str] = None,
    mobile: Optional[str] = None,
    account: Optional[str] = None,
    customer_id: Optional[str] = None,
    **extra: str,
) -> Dict[str, str]:
    """Build common template variables from raw owner facts.

    Produces e.g. pan_lower, pan_upper, dob_ddmmyyyy, dob_ddmmyy, dob_yyyymmdd,
    dob_ddmm, name_first4_lower, name_first4_upper, mobile_last4, mobile_last5,
    account_last4, account_last5, customer_id. Extra keyword args pass through.
    """
    out: Dict[str, str] = {}
    if pan:
        p = pan.strip()
        out["pan"] = p
        out["pan_lower"] = p.lower()
        out["pan_upper"] = p.upper()
    d = _as_date(dob) if dob is not None else None
    if d:
        out["dob_ddmmyyyy"] = d.strftime("%d%m%Y")
        out["dob_ddmmyy"] = d.strftime("%d%m%y")
        out["dob_yyyymmdd"] = d.strftime("%Y%m%d")
        out["dob_ddmm"] = d.strftime("%d%m")
        out["dob_yyyy"] = d.strftime("%Y")
    if name:
        n = re.sub(r"[^A-Za-z]", "", name)
        out["name_first4_lower"] = n[:4].lower()
        out["name_first4_upper"] = n[:4].upper()
        out["name_first4"] = n[:4]
    if mobile:
        m = re.sub(r"\D", "", mobile)
        out["mobile"] = m
        out["mobile_last4"] = m[-4:]
        out["mobile_last5"] = m[-5:]
    if account:
        a = re.sub(r"\D", "", account)
        out["account_last4"] = a[-4:]
        out["account_last5"] = a[-5:]
        out["account"] = a
    if customer_id:
        out["customer_id"] = customer_id.strip()
    out.update({k: str(v) for k, v in extra.items() if v is not None})
    return out


def expand_templates(
    templates: Iterable[str], template_vars: Optional[Dict[str, str]]
) -> List[Tuple[str, str]]:
    """``"<pan_lower><dob_ddmmyyyy>"`` -> ``[("template:<pan_lower><dob_ddmmyyyy>", value)]``.

    Templates with an unknown placeholder are skipped (never half-filled)."""
    vars_ = template_vars or {}
    out: List[Tuple[str, str]] = []
    seen = set()
    for tpl in templates or ():
        names = _PLACEHOLDER.findall(tpl)
        if any(n not in vars_ or vars_[n] in (None, "") for n in names):
            continue
        val = _PLACEHOLDER.sub(lambda m: str(vars_[m.group(1)]), tpl)
        if val and val not in seen:
            seen.add(val)
            out.append((f"template:{tpl}", val))
    return out


# ── backends ────────────────────────────────────────────────────────────────

def _have_pdfkit() -> bool:
    try:
        import Quartz  # noqa: F401

        return hasattr(Quartz, "PDFDocument")
    except Exception:
        return False


def _have_pypdf() -> bool:
    try:
        import pypdf  # noqa: F401

        return True
    except Exception:
        return False


def available_backends() -> List[str]:
    out = []
    if _have_pdfkit():
        out.append("pdfkit")
    if _have_pypdf():
        out.append("pypdf")
    return out


def _extract_pdfkit(path: str, candidates: Sequence[Tuple[str, str]]) -> PdfText:
    import Quartz
    from Foundation import NSURL

    res = PdfText(backend="pdfkit")
    doc = Quartz.PDFDocument.alloc().initWithURL_(NSURL.fileURLWithPath_(str(path)))
    if doc is None:
        res.error = "open_failed"
        return res
    res.encrypted = bool(doc.isEncrypted())
    if doc.isLocked():
        for label, pw in candidates:
            res.attempts += 1
            if doc.unlockWithPassword_(pw):
                res.unlocked_by = label
                break
        if doc.isLocked():
            res.locked = True
            res.pages = int(doc.pageCount())
            return res
    n = int(doc.pageCount())
    res.pages = n
    chunks = []
    for i in range(n):
        page = doc.pageAtIndex_(i)
        s = page.string() if page is not None else None
        s = normalize_text(str(s)) if s else ""
        res.page_chars.append(len(s.strip()))
        chunks.append(s)
    res.text = "\n".join(chunks)
    return res


def _extract_pypdf(path: str, candidates: Sequence[Tuple[str, str]]) -> PdfText:
    import pypdf

    res = PdfText(backend="pypdf")
    try:
        reader = pypdf.PdfReader(str(path))
    except Exception as e:  # malformed
        res.error = "open_failed"
        res.backend = f"pypdf:{type(e).__name__}"
        return res
    res.encrypted = bool(getattr(reader, "is_encrypted", False))
    if res.encrypted:
        unlocked = False
        # owner-only encryption: the empty user password opens it
        for label, pw in [("empty", "")] + list(candidates):
            if label != "empty":
                res.attempts += 1
            try:
                if reader.decrypt(pw):
                    unlocked = True
                    res.unlocked_by = None if label == "empty" else label
                    break
            except Exception:
                # missing crypto dependency or bad password: do not leak which
                continue
        if not unlocked:
            res.locked = True
            try:
                res.pages = len(reader.pages)
            except Exception:
                pass
            return res
    chunks = []
    try:
        for page in reader.pages:
            s = normalize_text(page.extract_text() or "")
            res.page_chars.append(len(s.strip()))
            chunks.append(s)
    except Exception:
        res.error = "open_failed"
    res.pages = len(chunks)
    res.text = "\n".join(chunks)
    return res


def extract(
    path,
    passwords: Sequence[Tuple[str, str]] = (),
    *,
    templates: Iterable[str] = (),
    template_vars: Optional[Dict[str, str]] = None,
    backend: Optional[str] = None,
) -> PdfText:
    """Extract text from a PDF, unlocking it if needed.

    ``passwords``: ``(label, value)`` pairs, tried first, in order.
    ``templates`` + ``template_vars``: expanded and tried after them.
    ``backend``: force "pdfkit" / "pypdf" (default: first available).
    """
    candidates: List[Tuple[str, str]] = []
    for item in passwords or ():
        if isinstance(item, str):  # bare value: give it a neutral label
            candidates.append(("password", item))
        else:
            candidates.append((str(item[0]), str(item[1])))
    candidates += expand_templates(templates, template_vars)

    order = [backend] if backend else available_backends()
    for name in order:
        try:
            if name == "pdfkit" and _have_pdfkit():
                return _extract_pdfkit(str(path), candidates)
            if name == "pypdf" and _have_pypdf():
                return _extract_pypdf(str(path), candidates)
        except Exception as e:  # never surface candidate values via exceptions
            return PdfText(backend=name, error=f"{name}_error:{type(e).__name__}")
    # no backend: still report encryption from raw bytes
    res = PdfText(backend="none", error="no_backend")
    try:
        with open(path, "rb") as fh:
            data = fh.read()
        if looks_encrypted(data):
            res.encrypted = True
            res.locked = True
    except OSError:
        res.error = "open_failed"
    return res
