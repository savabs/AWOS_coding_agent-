"""Network off still allows loopback (unit tests use localhost), never the internet."""
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scaffold.agent import sandbox as sbmod  # noqa: E402

PROBE = """
import socket
def t(h, p):
    try:
        s = socket.create_connection((h, p), timeout=5); s.close(); return "CONNECTED"
    except Exception as e:
        return type(e).__name__
srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1)
print("loopback", t("127.0.0.1", srv.getsockname()[1]))
print("internet", t("1.1.1.1", 443))
"""


def test_profile_allows_only_loopback_when_network_off():
    src = open(sbmod.__file__).read()
    assert '(allow network-outbound (remote ip "localhost:*"))' in src
    # A combined network* allow for localhost also let the internet through.
    assert '(allow network* (local ip "localhost:*")' not in src


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("sandbox-exec"),
                    reason="Seatbelt is macOS-only")
def test_seatbelt_loopback_on_internet_off(tmp_path):
    (tmp_path / "probe.py").write_text(PROBE)
    sb = sbmod.make_sandbox(str(tmp_path), backend="seatbelt")
    if sb is None or getattr(sb, "allow_network", True):
        pytest.skip("seatbelt sandbox unavailable or network allowed by config")
    venv = os.path.dirname(os.path.dirname(sys.executable))
    sb.allow_read([venv, sys.base_prefix])
    res = sb.run(f"{sys.executable} probe.py", timeout_sec=60)
    out = (getattr(res, "stdout", "") or "") + (getattr(res, "stderr", "") or "")
    assert "loopback CONNECTED" in out, out
    assert "internet CONNECTED" not in out, out
