"""Extract a candidate's parent snapshot into _tools/probe/<name> and print its layout
and pytest config (for writing a repo profile).  usage: probe.py <name> [...]"""
import shutil
import sys

import gha

for name in sys.argv[1:]:
    p = gha.prinfo(name)
    dest = gha.DATA / "probe" / name
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    gha.extract(p["repo"], p["parents"][0], dest)
    print(f"### {name} -> {dest}")
    print("  top:", " ".join(sorted(x.name for x in dest.iterdir())))
    for t in ("tests", "test"):
        if (dest / t).is_dir():
            print(f"  {t}/:", " ".join(sorted(x.name for x in (dest / t).iterdir()))[:600])
    for cfg in ("pyproject.toml", "setup.cfg", "pytest.ini", ".pytest.ini", "tox.ini"):
        f = dest / cfg
        if f.exists():
            txt = f.read_text()
            for key in ("[tool.pytest", "[pytest]", "[tool:pytest]"):
                i = txt.find(key)
                if i >= 0:
                    print(f"  {cfg}:\n    " + txt[i:i + 700].replace("\n", "\n    "))
