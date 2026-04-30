#!/usr/bin/env python3
"""
obsidian_lint.py — Check all markdown files for Obsidian health issues.

Checks:
  FM01  Missing required frontmatter field (title or tags)
  FM02  Frontmatter present but malformed (not valid YAML)
  LK01  Broken wiki link — [[filename]] target does not exist in vault
  LK02  Orphan file — no other file links to it (advisory)
  ST03  Stale file — last modified > 90 days ago with status/active tag (advisory)

Exit codes:
  0 — no FM/LK01 errors
  1 — FM or LK01 errors found

Usage:
    python scripts/obsidian_lint.py
    python scripts/obsidian_lint.py --strict       # also enforce LK02
    python scripts/obsidian_lint.py --path docs/   # scan specific subtree
    python scripts/obsidian_lint.py --fix          # auto-add missing frontmatter stubs
"""

import argparse
import datetime
import re
import sys
from pathlib import Path

try:
    import yaml

    HAS_YAML = True
except ImportError:
    HAS_YAML = False


def get_vault_root(start: Path) -> Path:
    for parent in [start, *start.parents]:
        if (parent / ".obsidian").exists() or (parent / "AWOS.md").exists():
            return parent
    return start


def collect_markdown_files(root: Path, subtree: Path | None = None) -> list[Path]:
    search_root = subtree if subtree else root
    return [
        f
        for f in search_root.rglob("*.md")
        if ".git" not in f.parts and "node_modules" not in f.parts
    ]


def parse_frontmatter(content: str) -> tuple[dict | None, bool]:
    """Return (frontmatter_dict, is_present). Returns (None, False) if no FM."""
    if not content.startswith("---"):
        return None, False

    end = content.find("\n---", 3)
    if end == -1:
        return None, True  # FM present but not closed

    fm_text = content[3:end].strip()
    if not HAS_YAML:
        # Basic check without PyYAML
        has_title = "title:" in fm_text
        has_tags = "tags:" in fm_text
        return {"title": has_title, "tags": has_tags}, True

    try:
        parsed = yaml.safe_load(fm_text)
        return parsed if isinstance(parsed, dict) else {}, True
    except yaml.YAMLError:
        return None, True  # FM present but invalid YAML


def extract_wiki_links(content: str) -> list[str]:
    """Extract all [[link]] targets from content."""
    return re.findall(r"\[\[([^\]|]+?)(?:\|[^\]]+)?\]\]", content)


def build_filename_index(files: list[Path], vault_root: Path) -> dict[str, Path]:
    """Map stem (lowercase) → path for quick lookup."""
    index = {}
    for f in files:
        key = f.stem.lower()
        if key not in index:
            index[key] = f
    return index


def check_file(
    path: Path,
    vault_root: Path,
    filename_index: dict[str, Path],
    all_links: dict[str, list[str]],
    strict: bool,
) -> list[dict]:
    """Run all lint checks on a single file. Returns list of findings."""
    findings = []
    content = path.read_text(encoding="utf-8", errors="replace")
    rel = str(path.relative_to(vault_root))

    # FM01 / FM02 — frontmatter
    fm, fm_present = parse_frontmatter(content)
    if not fm_present:
        findings.append({"code": "FM01", "file": rel, "msg": "No frontmatter found"})
    elif fm is None:
        findings.append(
            {
                "code": "FM02",
                "file": rel,
                "msg": "Frontmatter present but could not be parsed",
            }
        )
    else:
        if not fm.get("title"):
            findings.append(
                {"code": "FM01", "file": rel, "msg": "Missing required field: title"}
            )
        if not fm.get("tags"):
            findings.append(
                {"code": "FM01", "file": rel, "msg": "Missing required field: tags"}
            )

    # LK01 — broken wiki links
    links = extract_wiki_links(content)
    for link in links:
        target = link.strip().lower()
        # Strip anchors (#heading)
        target = target.split("#")[0].strip()
        if not target:
            continue
        if target not in filename_index:
            findings.append(
                {
                    "code": "LK01",
                    "file": rel,
                    "msg": f"Broken link: [[{link}]] — target not found in vault",
                }
            )

    # LK02 — orphan (advisory)
    if strict:
        stem = path.stem.lower()
        linked_to = all_links.get(stem, [])
        if not linked_to and "TEMPLATE" not in path.name:
            findings.append(
                {
                    "code": "LK02",
                    "file": rel,
                    "msg": "Orphan: no other file links to this file (advisory)",
                }
            )

    # ST03 — stale active file (advisory)
    if fm and isinstance(fm.get("tags"), list):
        tags = fm.get("tags", [])
        if "status/active" in tags:
            mtime = datetime.datetime.fromtimestamp(path.stat().st_mtime)
            age = (datetime.datetime.now() - mtime).days
            if age > 90:
                findings.append(
                    {
                        "code": "ST03",
                        "file": rel,
                        "msg": f"Stale: tagged status/active but not modified in {age} days",
                    }
                )

    return findings


def auto_fix_missing_frontmatter(path: Path) -> bool:
    """Add a stub frontmatter to files that have none."""
    content = path.read_text(encoding="utf-8", errors="replace")
    if content.startswith("---"):
        return False  # Already has FM

    title = path.stem.replace("_", " ").replace("-", " ").title()
    stub = f'---\ntitle: "{title}"\ntags:\n  - doc/wiki\n---\n\n'
    path.write_text(stub + content, encoding="utf-8")
    return True


def main():
    parser = argparse.ArgumentParser(description="Obsidian vault lint checker.")
    parser.add_argument(
        "--path", default=None, help="Subtree to scan (default: entire vault)"
    )
    parser.add_argument(
        "--strict", action="store_true", help="Enforce LK02 (orphan files)"
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Auto-add missing frontmatter stubs (FM01 only)",
    )
    args = parser.parse_args()

    vault_root = get_vault_root(Path.cwd())
    subtree = Path(args.path) if args.path else None
    if subtree and not subtree.is_absolute():
        subtree = vault_root / subtree

    all_files = collect_markdown_files(vault_root)
    filename_index = build_filename_index(all_files, vault_root)

    # Build reverse link map: target_stem → [files that link to it]
    all_links: dict[str, list[str]] = {}
    for f in all_files:
        content = f.read_text(encoding="utf-8", errors="replace")
        for link in extract_wiki_links(content):
            target = link.strip().lower().split("#")[0].strip()
            if target:
                all_links.setdefault(target, []).append(str(f.relative_to(vault_root)))

    # Determine scan target
    scan_files = collect_markdown_files(vault_root, subtree) if subtree else all_files

    all_findings = []
    fixed = 0

    for f in sorted(scan_files):
        if "TEMPLATE" in f.name and not args.fix:
            continue  # Templates are allowed to have incomplete FM

        findings = check_file(f, vault_root, filename_index, all_links, args.strict)
        all_findings.extend(findings)

        if args.fix:
            for finding in findings:
                if finding["code"] == "FM01" and "No frontmatter" in finding["msg"]:
                    if auto_fix_missing_frontmatter(f):
                        fixed += 1
                        print(f"  FIXED FM01: {finding['file']}")

    # Report
    errors = [f for f in all_findings if f["code"] in ("FM01", "FM02", "LK01")]
    warnings = [f for f in all_findings if f["code"] in ("LK02", "ST03")]

    if errors:
        print(f"\n=== ERRORS ({len(errors)}) — must fix before committing ===")
        for f in errors:
            print(f"  [{f['code']}] {f['file']}: {f['msg']}")

    if warnings:
        print(f"\n=== WARNINGS ({len(warnings)}) — advisory ===")
        for f in warnings:
            print(f"  [{f['code']}] {f['file']}: {f['msg']}")

    if not errors and not warnings:
        print("Obsidian lint: clean")

    if args.fix and fixed:
        print(
            f"\nAuto-fixed {fixed} files (added frontmatter stubs). Review and fill in details."
        )

    scanned = len(scan_files)
    print(f"\nScanned {scanned} files in {subtree or vault_root}")

    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
