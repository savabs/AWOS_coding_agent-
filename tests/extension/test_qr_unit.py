"""lib/qr.js (the phone pairing QR code) without the extension, in a plain page.

Golden data below was generated during development from two independent reference encoders in a
throwaway venv outside the repo (Nayuki's `qrcodegen` and `qrcode`/python-qrcode, byte mode, UTF-8):
  - auto cases: Nayuki qrcodegen picked the same version and mask, and the matrix hashes are its output
  - forced cases (fixed version + mask): qrcodegen and python-qrcode agreed bit for bit
During development the encoder also matched python-qrcode on all 160 (version 1..40 x L/M/Q/H, random
forced mask) combinations and qrcodegen's auto version/mask/matrix on 200 random texts up to 700 bytes.
Hash = sha256 of the module rows as '1'/'0' strings joined with '\\n' (no quiet zone).
Run:  PYTHONPATH=. ~/.awos/rl-venv/bin/python -m pytest -q tests/extension/test_qr_unit.py
"""
import hashlib
import pathlib

import pytest

EXT = pathlib.Path(__file__).resolve().parents[2] / "extension"
pytestmark = pytest.mark.timeout(60)

# Same shape as the real pairing link (153 chars); the values are dummies.
LINK = ("https://awos.tirramind.com/phone/#c=AAECAwQFBgcICQoLDA0ODw&k=EBESExQVFhcYGRobHB0eHyAhIiMkJSYnKCkqKywtLi8"
        "&t=MDEyMzQ1Njc4OTo7PD0-P0&n=awos-0123456789abcdef")

# (text, ecc, version, mask, sha256 of rows) - auto version + auto mask
AUTO = [
    ("HELLO WORLD", "M", 1, 4, "2d21897bf5a7ac606d02da07bdd5e7f02a8b90097f12404b1f7d764f18ef618c"),
    (LINK, "M", 9, 2, "d153e7d951af6579d2c2a119ba202364e54052a8038e8e7229f6c9aa3c657c9d"),
    ("awos pairing é test 123", "L", 2, 4, "e21917cf604c80204bd0cf9f3decd28035a04ed901afb62a79789429cf0fb81c"),
    (LINK, "H", 12, 2, "0139c54edc1211d9d228cee4d5f6b403a404522302acdfeb8d3c4c07a6624d9a"),
    (LINK, "L", 7, 6, "647b44496c7b223ef9a3dbaffc8baacfea6dddf57ac44da9cfe74d4b3d12fffe"),
    (LINK, "Q", 11, 2, "23fb93388c78ff84209b34de8f235397cc76aa63f7da334e561f76ca6a07bf9a"),
]
# (text, ecc, forced version, forced mask, sha256 of rows) - pins RS/interleave/placement/version info exactly
FORCED = [
    (LINK, "M", 10, 3, "3a5cc91b2d3c4ed799090e9c8068d3f0676d4d5b1d0a91ba68c039d0dc0680b4"),
    ("x", "Q", 7, 5, "c58652f628abccca9ac999cc41a7ff4d1f7ce2f0ca27cce628024bb6dafcd433"),
    ("forced", "H", 22, 6, "f267f60e5ce92e100dcc8e2d1e7cc075a3c66585089b9b1501d0d168a6a43c9d"),
]

ENC = """([t, e, o]) => { const q = AWOSQR.encode(t, e, o);
  return {size: q.size, version: q.version, ecc: q.ecc, mask: q.mask,
          rows: q.modules.map(r => r.map(x => x ? '1' : '0').join(''))}; }"""


@pytest.fixture(scope="module")
def page(pw):
    try:
        b = pw.chromium.launch(headless=True)
    except Exception as e:
        pytest.skip(f"no Chromium: {e}")
    p = b.new_page()
    p.set_content("<html><body></body></html>")
    p.add_script_tag(path=str(EXT / "lib/qr.js"))
    yield p
    b.close()


def enc(page, text, ecc=None, opts=None):
    return page.evaluate(ENC, [text, ecc, opts or {}])


def digest(rows):
    return hashlib.sha256("\n".join(rows).encode()).hexdigest()


@pytest.mark.parametrize("text,ecc,ver,mask,sha", AUTO)
def test_auto_matches_reference(page, text, ecc, ver, mask, sha):
    q = enc(page, text, ecc)
    assert (q["version"], q["mask"], q["ecc"], q["size"]) == (ver, mask, ecc, ver * 4 + 17)
    assert all(len(r) == q["size"] for r in q["rows"])
    assert digest(q["rows"]) == sha


@pytest.mark.parametrize("text,ecc,ver,mask,sha", FORCED)
def test_forced_version_and_mask_match_reference(page, text, ecc, ver, mask, sha):
    q = enc(page, text, ecc, {"version": ver, "mask": mask})
    assert (q["version"], q["mask"]) == (ver, mask)
    assert digest(q["rows"]) == sha


def test_default_ecc_is_m_and_pair_link_fits_small(page):
    q = enc(page, LINK)
    assert q["ecc"] == "M" and q["version"] == 9 and q["size"] == 53


def test_finder_patterns_at_three_corners(page):
    q = enc(page, LINK, "M")
    rows, n = q["rows"], q["size"]
    finder = ["1111111", "1000001", "1011101", "1011101", "1011101", "1000001", "1111111"]
    for ox, oy in ((0, 0), (n - 7, 0), (0, n - 7)):
        assert [rows[oy + i][ox:ox + 7] for i in range(7)] == finder, (ox, oy)
    assert rows[n - 7][n - 7:] != "1111111"  # no fourth finder
    assert rows[6][8:n - 8] == ("10" * n)[:n - 16]  # timing pattern
    assert rows[n - 8][8] == "1"  # dark module


def test_too_long_throws(page):
    assert enc(page, "a" * 2953, "L")["version"] == 40  # byte-mode capacity of 40-L
    assert enc(page, "a" * 1273, "H")["version"] == 40
    for text, ecc in (("a" * 2954, "L"), ("a" * 1274, "H"), ("é" * 1500, "L")):
        err = page.evaluate("([t, e]) => { try { AWOSQR.encode(t, e); return null } catch (x) { return String(x.message) } }", [text, ecc])
        assert err and "too long" in err
    err = page.evaluate("() => { try { AWOSQR.encode('x', 'Z'); return null } catch (x) { return String(x.message) } }")
    assert err


def test_to_svg_is_plain_markup(page):
    svg = page.evaluate("t => AWOSQR.toSvg(t, {ecc: 'M', scale: 3, margin: 2, dark: '#123456', light: '#fff'})", LINK)
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    low = svg.lower()
    assert "<script" not in low and "onload" not in low and "javascript:" not in low
    assert svg.count("<path") == 1 and 'shape-rendering="crispEdges"' in svg and "#123456" in svg
    assert 'width="171"' in svg and 'viewBox="0 0 57 57"' in svg  # (53 + 2*2) * 3
    hostile = page.evaluate("t => AWOSQR.toSvg(t, {dark: '\"/><script>alert(1)</script>'})", "x")
    assert "<script" not in hostile.lower()
    # defaults: 4 px/module, 4-module quiet zone, black on white
    d = page.evaluate("t => AWOSQR.toSvg(t)", "HELLO WORLD")
    assert 'viewBox="0 0 29 29"' in d and 'width="116"' in d and 'fill="#000"' in d and 'fill="#fff"' in d
    # renders as an element when used as innerHTML
    n = page.evaluate("t => { const d = document.createElement('div'); d.innerHTML = AWOSQR.toSvg(t);"
                      " document.body.appendChild(d); return d.querySelectorAll('svg path').length }", LINK)
    assert n == 1
