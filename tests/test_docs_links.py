"""v0.2.1: user-facing docs links point at the docs site through ONE constant."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from scaffold.agent import doctor, paths

ROOT = Path(__file__).resolve().parents[1]
QUICKSTART = "https://awos-6v8.pages.dev/docs/quickstart/"


def test_docs_url_comes_from_one_constant(monkeypatch):
    monkeypatch.delenv("AWOS_DOCS_URL", raising=False)
    assert paths.docs_url() == QUICKSTART
    assert paths.install_url() == "https://awos-6v8.pages.dev/install.sh"
    monkeypatch.setattr(paths, "DOCS_BASE_URL", "https://awos.tirramind.com")
    assert paths.docs_url() == "https://awos.tirramind.com/docs/quickstart/"
    assert paths.docs_url("faq") == "https://awos.tirramind.com/docs/faq/"
    monkeypatch.setenv("AWOS_DOCS_URL", "https://example.test/")
    assert paths.docs_url() == "https://example.test/docs/quickstart/"


def test_awos_help_links_to_the_site_quickstart():
    out = subprocess.run([sys.executable, str(ROOT / "awos.py"), "--help"], capture_output=True,
                         text=True, cwd=str(ROOT), timeout=60, env={"PATH": "/usr/bin:/bin",
                                                                   "HOME": str(ROOT)}).stdout
    assert f"Docs: {QUICKSTART}" in out
    assert "INSTALL.md" not in out


def test_no_provider_and_doctor_hints_carry_the_docs_link(monkeypatch):
    monkeypatch.delenv("AWOS_DOCS_URL", raising=False)
    assert QUICKSTART in paths.no_provider_message()
    bad = doctor.Check("Provider", False, "no key", fix="set one")
    assert f"Help: {QUICKSTART}" in doctor.format_checks([bad])
    good = doctor.Check("Provider", True, "ok")
    assert "Help:" not in doctor.format_checks([good])


def test_onboarding_menu_shows_the_setup_guide(monkeypatch):
    from scaffold.agent import onboarding
    monkeypatch.delenv("AWOS_DOCS_URL", raising=False)
    said = []
    assert onboarding.setup_provider(ask=lambda _: "s", say=said.append) is None
    assert any(QUICKSTART in line for line in said)


def test_no_dead_install_md_links_in_shipped_code():
    for path in [ROOT / "awos.py", *(ROOT / "scaffold").rglob("*.py")]:
        text = path.read_text(encoding="utf-8", errors="replace")
        assert "blob/main/docs/INSTALL.md" not in text, path
