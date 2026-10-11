"""Build single-job fresh series (PRs merged on/after 2026-08-01) in the real_series format.

usage: build.py [series-name ...]          build (all specs if none given)

Same construction as real_series/_tools/build3a.py, but snapshots come from the GitHub
API (gha.py: tarball at the commit) instead of a local VCS, and PR metadata from
prinfo/<repo>_<num>.json (prinfo.py). fix = the PR's merge commit, parent = its first
parent. Per-repo accommodations live in PROFILES, per-issue tweaks in SPECS; list/dict
keys present in both are merged (profile first):
  source [files]                 -> reference/ (post-fix copies)
  tests {src: dst-under-tests/}  -> hidden_tests/ (post-fix copies)
  hidden_extra {src: dst}        non-test support files shipped with the hidden tests
  replace {file: [(old, new)]}   visible-suite edits on base/ (asserted)
  strip {file: [test names]}     remove top-level test functions / methods (Class.name) from base/
  hidden_strip [names]           remove these tests from the hidden copies (where present)
  hidden_replace [(old, new)]    edits on the hidden copies (spec-level: asserted)
  drop [paths]                   extra paths removed from base/
  dup {src: dst}                 copy a base/ file (pre-fix) to dst, e.g. in-package tests into tests/
  write {file: text} / copy {file: files/<src>} / append {file: text}
  dist_info True                 minimal <name>.dist-info (entry points) at the base root
"""
import ast
import shutil
import sys
from pathlib import Path

import gha
from specs import PROFILES, SPECS

ROOT = Path("/Users/becmachlean/.awos-harness/fresh_series")
FILES = Path(__file__).with_name("files")


def strip_tests(path: Path, names, name, strict=True):
    lines = path.read_text().splitlines(keepends=True)
    tree = ast.parse("".join(lines))
    cut, found = set(), set()

    def cut_node(node):
        start = min([node.lineno] + [d.lineno for d in node.decorator_list])
        cut.update(range(start - 1, node.end_lineno))

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            cut_node(node)
            found.add(node.name)
        if isinstance(node, ast.ClassDef):
            for sub in node.body:
                q = f"{node.name}.{sub.name}" if hasattr(sub, "name") else None
                if q in names and isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    cut_node(sub)
                    found.add(q)
    if strict:
        assert found == set(names), (name, path, set(names) - found)
    text = "".join(l for i, l in enumerate(lines) if i not in cut)
    compile(text, str(path), "exec")
    path.write_text(text)
    return found


def strip_text(text, names):
    """strip_tests on a string (non-strict); returns the new text."""
    import tempfile
    with tempfile.NamedTemporaryFile("w+", suffix=".py", delete=False) as fh:
        fh.write(text)
    p = Path(fh.name)
    strip_tests(p, names, "hidden", strict=False)
    out = p.read_text()
    p.unlink()
    return out


def write_dist_info(base):
    """Minimal installed-metadata stand-in so entry points resolve without pip."""
    import tomllib
    proj = tomllib.loads((base / "pyproject.toml").read_text())["project"]
    d = base / f"{proj['name']}.dist-info"
    d.mkdir()
    (d / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {proj['name']}\nVersion: 0+awos\n")
    lines = []
    for group, eps in proj.get("entry-points", {}).items():
        lines.append(f"[{group}]")
        lines += [f"{k} = {v}" for k, v in eps.items()]
        lines.append("")
    if "scripts" in proj:
        lines += ["[console_scripts]"] + [f"{k} = {v}" for k, v in proj["scripts"].items()] + [""]
    (d / "entry_points.txt").write_text("\n".join(lines))


def merged(prof, s, key, kind):
    a, b = prof.get(key, kind()), s.get(key, kind())
    if kind is list:
        return list(a) + list(b)
    out = {}
    for src in (a, b):
        for k, v in src.items():
            if isinstance(v, list):
                out.setdefault(k, []).extend(v)
            else:
                out[k] = v
    return out


def build(name):
    s = SPECS[name]
    repo = s["repo"]
    short = repo.split("/")[1]
    prof = PROFILES[s.get("profile", short)]
    info = gha.prinfo(f"{short}_{s['pr']}")
    fix, parent = info["mergeCommit"]["oid"], info["parents"][0]
    pr_files = {f["path"] for f in info["files"]}
    assert set(s["source"]) <= pr_files, (name, set(s["source"]) - pr_files)
    assert set(s["tests"]) <= pr_files, (name, set(s["tests"]) - pr_files)
    unshipped = sorted(p for p in pr_files - set(s["source"]) - set(s["tests"])
                       if p.endswith((".py", ".pyi")) and "test" not in p.rsplit("/", 1)[-1])
    if unshipped:
        print(f"  note {name}: PR .py files not in reference/: {unshipped}", flush=True)
    sdir = ROOT / name
    base = sdir / "base"
    job = sdir / "jobs" / f"01_{s['slug']}"
    if (sdir / "jobs").exists():
        for old in (sdir / "jobs").iterdir():
            if old != job:
                shutil.rmtree(old)
    for d in (base, job / "hidden_tests", job / "reference"):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
    gha.extract(repo, parent, base)
    for p in sorted(base.rglob("*"), reverse=True):
        if p.is_symlink():
            target = p.resolve()
            p.unlink()
            if target.is_file():
                shutil.copy2(target, p)
    for d in merged(prof, s, "drop", list):
        p = base / d
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()
    for src, dst in s.get("dup", {}).items():
        (base / dst).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(base / src, base / dst)
    for f, pairs in merged(prof, s, "replace", dict).items():
        if not (base / f).exists() and f in prof.get("replace_optional", ()):
            continue
        text = (base / f).read_text()
        for old, new in pairs:
            assert old in text, (name, f, old)
            text = text.replace(old, new, 1)
        (base / f).write_text(text)
    for f, names in merged(prof, s, "strip", dict).items():
        strip_tests(base / f, names, name)
    for f, text in merged(prof, s, "write", dict).items():
        (base / f).parent.mkdir(parents=True, exist_ok=True)
        (base / f).write_text(text)
    for f, src in merged(prof, s, "copy", dict).items():
        (base / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(FILES / src, base / f)
    for f, extra in merged(prof, s, "append", dict).items():
        with open(base / f, "a") as fh:
            fh.write(extra)
    if prof.get("dist_info") or s.get("dist_info"):
        write_dist_info(base)
    for f in s["source"]:
        out = job / "reference" / f
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(gha.show(repo, fix, f))
    hstrip = merged(prof, s, "hidden_strip", list)
    for src, dst in {**s.get("tests", {}), **s.get("hidden_extra", {})}.items():
        rel = Path(dst).relative_to("tests")
        out = job / "hidden_tests" / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        text = gha.show(repo, fix, src)
        if src.endswith(".py"):
            for old, new in prof.get("hidden_replace", []):
                text = text.replace(old, new)   # profile-level: where present
            for old, new in s.get("hidden_replace", []):
                assert old in text, (name, src, old)
                text = text.replace(old, new)
            if hstrip:
                text = strip_text(text, hstrip)
        out.write_text(text)
    print("built", sdir, "parent", parent[:10], flush=True)


if __name__ == "__main__":
    for n in sys.argv[1:] or SPECS:
        build(n)
