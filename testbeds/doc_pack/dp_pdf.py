"""
dp_pdf.py — write the testbed's PDFs (invoices, bills, statements).

Two backends, picked once per process:

  quartz    macOS CoreGraphics + CoreText through PyObjC (pyobjc-framework-Quartz).
            Real fonts (the ₹ sign renders through CoreText font fallback), compressed
            streams, and real password protection: CGPDFContext's user password gives an
            AES-128 (/V 4 /R 4) encrypted file that Preview / PDFKit / pdftotext open
            only with the password.
  plain     pure-Python fallback (no third-party packages). Uncompressed, Helvetica,
            WinAnsi text; "₹" is drawn as "Rs." and the file is NOT encrypted (we do not
            ship our own cipher). seed.py records ``pdf_backend`` and
            ``statement_encrypted`` in truth.json so verify.py knows what to expect.

Force the fallback with DOC_PACK_PDF=plain (used by a test so both paths stay alive).

    d = Doc(title="Tax invoice", author="PayFlow")
    d.text(40, 800, "TAX INVOICE", size=16, bold=True)
    d.rule(40, 790, 555); d.new_page() ...
    data = d.render(password=None)      # -> bytes
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

PAGE_W, PAGE_H = 595.0, 842.0          # A4 in points


def _quartz_ok() -> bool:
    if os.environ.get("DOC_PACK_PDF", "").lower() == "plain":
        return False
    try:
        import CoreText  # noqa: F401
        import Quartz  # noqa: F401
        return True
    except Exception:
        return False


def backend() -> str:
    return "quartz" if _quartz_ok() else "plain"


Item = tuple[str, float, float, str, float, bool, tuple[float, float, float]]


class Doc:
    def __init__(self, title: str = "", author: str = ""):
        self.title, self.author = title, author
        self.pages: list[list[Item]] = [[]]

    # -- layout -------------------------------------------------------------
    def text(self, x: float, y: float, s: str, size: float = 10, bold: bool = False,
             color: tuple[float, float, float] = (0, 0, 0)) -> None:
        self.pages[-1].append(("t", x, y, str(s), size, bold, color))

    def right(self, xr: float, y: float, s: str, size: float = 10, bold: bool = False) -> None:
        """Right-aligned text (approximate width: 0.5 em per char is enough for layout)."""
        w = len(str(s)) * size * 0.52
        self.text(xr - w, y, s, size, bold)

    def rule(self, x0: float, y: float, x1: float, w: float = 0.6) -> None:
        self.pages[-1].append(("r", x0, y, str(x1), w, False, (0.6, 0.6, 0.6)))

    def box(self, x: float, y: float, w: float, h: float, color=(0.93, 0.95, 0.98)) -> None:
        self.pages[-1].append(("b", x, y, f"{w},{h}", 0, False, color))

    def new_page(self) -> None:
        self.pages.append([])

    # -- output -------------------------------------------------------------
    def render(self, password: str | None = None) -> bytes:
        if _quartz_ok():
            return _render_quartz(self, password)
        return _render_plain(self)


# ------------------------------------------------------------------ quartz --

def _render_quartz(doc: Doc, password: str | None) -> bytes:
    import CoreText
    import Quartz as Q
    from Foundation import NSURL
    fd, tmp = tempfile.mkstemp(suffix=".pdf", prefix="dp_")
    os.close(fd)
    try:
        aux = {Q.kCGPDFContextTitle: doc.title or "Document", Q.kCGPDFContextCreator: doc.author or "doc_pack",
               Q.kCGPDFContextAuthor: doc.author or "doc_pack"}
        if password:
            aux[Q.kCGPDFContextUserPassword] = password
            aux[Q.kCGPDFContextOwnerPassword] = password + "#owner"
            aux[Q.kCGPDFContextEncryptionKeyLength] = 128
        box = Q.CGRectMake(0, 0, PAGE_W, PAGE_H)
        ctx = Q.CGPDFContextCreateWithURL(NSURL.fileURLWithPath_(tmp), box, aux)
        fonts = {}
        for items in doc.pages:
            Q.CGContextBeginPage(ctx, box)
            for kind, x, y, s, size, bold, color in items:
                if kind == "b":
                    w, h = (float(v) for v in s.split(","))
                    Q.CGContextSetRGBFillColor(ctx, color[0], color[1], color[2], 1.0)
                    Q.CGContextFillRect(ctx, Q.CGRectMake(x, y, w, h))
                    continue
                if kind == "r":
                    Q.CGContextSetRGBStrokeColor(ctx, color[0], color[1], color[2], 1.0)
                    Q.CGContextSetLineWidth(ctx, size)
                    Q.CGContextMoveToPoint(ctx, x, y)
                    Q.CGContextAddLineToPoint(ctx, float(s), y)
                    Q.CGContextStrokePath(ctx)
                    continue
                key = (bold, size)
                if key not in fonts:
                    fonts[key] = CoreText.CTFontCreateWithName("Helvetica-Bold" if bold else "Helvetica", size, None)
                fg = Q.CGColorCreateGenericRGB(color[0], color[1], color[2], 1.0)
                attr = CoreText.CFAttributedStringCreate(None, s, {CoreText.kCTFontAttributeName: fonts[key],
                                                                   CoreText.kCTForegroundColorAttributeName: fg})
                line = CoreText.CTLineCreateWithAttributedString(attr)
                Q.CGContextSetTextPosition(ctx, x, y)
                CoreText.CTLineDraw(line, ctx)
            Q.CGContextEndPage(ctx)
        Q.CGPDFContextClose(ctx)
        del ctx
        return Path(tmp).read_bytes()
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


# ------------------------------------------------------------------- plain --

def _esc(s: str) -> str:
    s = s.replace("₹", "Rs.").replace("–", "-").replace("—", "-").replace("•", "*")
    s = s.encode("latin-1", "replace").decode("latin-1")
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _render_plain(doc: Doc) -> bytes:
    objs: list[bytes] = []

    def add(b: bytes) -> int:
        objs.append(b)
        return len(objs)

    add(b"")                                                    # 1: catalog (filled later)
    add(b"")                                                    # 2: pages
    f1 = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    f2 = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
    kids = []
    for items in doc.pages:
        ops = []
        for kind, x, y, s, size, bold, color in items:
            if kind == "b":
                w, h = (float(v) for v in s.split(","))
                ops.append(f"{color[0]:.3f} {color[1]:.3f} {color[2]:.3f} rg {x:.1f} {y:.1f} {w:.1f} {h:.1f} re f")
            elif kind == "r":
                ops.append(f"{color[0]:.3f} {color[1]:.3f} {color[2]:.3f} RG {size:.2f} w "
                           f"{x:.1f} {y:.1f} m {float(s):.1f} {y:.1f} l S")
            else:
                ops.append(f"{color[0]:.3f} {color[1]:.3f} {color[2]:.3f} rg BT /{'F2' if bold else 'F1'} "
                           f"{size:.1f} Tf 1 0 0 1 {x:.1f} {y:.1f} Tm ({_esc(s)}) Tj ET")
        stream = "\n".join(ops).encode("latin-1")
        c = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        kids.append(add(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] /Contents %d 0 R "
                        b"/Resources << /Font << /F1 %d 0 R /F2 %d 0 R >> >> >>"
                        % (int(PAGE_W), int(PAGE_H), c, f1, f2)))
    objs[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objs[1] = (b"<< /Type /Pages /Kids [" + b" ".join(b"%d 0 R" % k for k in kids)
               + b"] /Count %d >>" % len(kids))
    info = add(b"<< /Title (" + _esc(doc.title).encode("latin-1") + b") /Author ("
               + _esc(doc.author).encode("latin-1") + b") /Producer (doc_pack plain) >>")
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offs = []
    for i, b in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + b + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for o in offs:
        out += b"%010d 00000 n \n" % o
    out += (b"trailer\n<< /Size %d /Root 1 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (len(objs) + 1, info, xref))
    return bytes(out)
