import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """A temp dir with a source tree, used as the working directory."""
    src = tmp_path / "src"
    (src / "docs").mkdir(parents=True)
    (src / "docs" / "a.txt").write_text("alpha")
    (src / "docs" / "b.tmp").write_text("scratch")
    (src / "node_modules").mkdir()
    (src / "node_modules" / "x.js").write_text("x")
    (src / "top.txt").write_text("top")
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def write_ini(workspace):
    def _write(extra_backup="", notify="", logging_section="", name="settings.ini"):
        body = "\n".join([
            "[backup]",
            f"source_dir = {workspace / 'src'}",
            f"backup_dir = {workspace / 'out'}",
            extra_backup,
            "",
            "[notify]",
            notify,
            "",
            "[logging]",
            logging_section,
            "",
        ])
        path = workspace / name
        path.write_text(body)
        return path

    return _write
