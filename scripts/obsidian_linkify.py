#!/usr/bin/env python3
"""
obsidian_linkify.py — Auto-maintain Obsidian frontmatter and wiki links.

Checks:
  1. Every .md file has required frontmatter fields (title, tags)
  2. Bare file path references like docs/research/foo.md → [[foo]]
  3. Adds ## Related section stubs where missing

Usage:
    python scripts/obsidian_linkify.py               # check all .md files
    python scripts/obsidian_linkify.py --fix         # apply safe auto-fixes
    python scripts/obsidian_linkify.py --path docs/  # scope to subtree
    python scripts/obsidian_linkify.py --fix --path tasks/active/
"""

import argparse
import re
import sys
from pathlib import Path


def get_project_root() -> Path:
    here = Path.cwd()
    for parent in [here, *here.parents]:
        if (parent / ".git").exists() or (parent / "AWOS.md").exists():
            return parent
    return here


def collect_files(root: Path, subtree: Path | None = None) -> list[Path]:
    search_root = subtree or root
    return [
        f
        for f in search_root.rglob("*.md")
        if ".git" not in f.parts and "node_modules" not in f.parts
    ]


def build_name_index(all_files: list[Path], root: Path) -> dict[str, str]:
    """Map filename stem → relative path for wiki link resolution."""
    index = {}
    for f in all_files:
        stem = f.stem
        if stem in index:
            # Ambiguous — skip (Obsidian resolves by most relevant match)
            index[stem] = None  # type: ignore
        else:
            index[stem] = str(f.relative_to(root))
    return {k: v for k, v in index.items() if v is not None}


def has_frontmatter(content: str) -> bool:
    return content.startswith("---")


def extract_frontmatter(content: str) -> tuple[str, str]:
    """Returns (frontmatter_block, body). frontmatter_block includes the --- delimiters."""
    if not content.startswith("---"):
        return "", content
    end = content.find("---", 3)
    if end == -1:
        return "", content
    fm = content[: end + 3]
    body = content[end + 3 :]
    return fm, body


def infer_title_from_content(content: str, file_path: Path) -> str:
    """Try to extract H1 title, fall back to filename."""
    for line in content.split("\n"):
        if line.startswith("# "):
            return line[2:].strip()
    return file_path.stem.replace("_", " ").replace("-", " ").title()


def infer_tags_from_path(file_path: Path, root: Path) -> list[str]:
    """Infer reasonable tags from file location."""
    parts = file_path.relative_to(root).parts
    tags = []
    if "research" in parts:
        tags.append("doc/research")
    elif "specs" in parts:
        tags.append("doc/spec")
    elif "tasks" in parts:
        tags.append("doc/task")
        if "active" in parts:
            tags.append("status/active")
        elif "done" in parts:
            tags.append("status/done")
    elif "memory" in parts:
        if "checkpoint_" in file_path.name:
            tags.append("doc/checkpoint")
        else:
            tags.append("doc/memory")
    elif "adr" in parts:
        tags.append("doc/adr")
    elif "wiki" in parts:
        tags.append("doc/wiki")
    elif "protocols" in parts:
        tags.append("doc/wiki")
    else:
        tags.append("doc/wiki")
    return tags or ["doc/wiki"]


def add_frontmatter_stub(content: str, file_path: Path, root: Path) -> str:
    """Prepend minimal frontmatter to a file missing it."""
    title = infer_title_from_content(content, file_path)
    tags = infer_tags_from_path(file_path, root)
    tag_lines = "\n".join(f"  - {t}" for t in tags)
    fm = f'---\ntitle: "{title}"\ntags:\n{tag_lines}\n---\n\n'
    return fm + content


def check_frontmatter_fields(fm: str, required: list[str]) -> list[str]:
    """Return list of missing required fields."""
    missing = []
    for field in required:
        if not re.search(rf"^{field}:", fm, re.MULTILINE):
            missing.append(field)
    return missing


# Patterns that look like bare file path references
BARE_PATH_PATTERN = re.compile(
    r"""
    (?<!\[\[)              # not already a wiki link
    (?<!\()                # not in a markdown link target
    (?<!\`)                # not in code
    (
        (?:docs|tasks|wiki|memories|scripts|protocols|examples|agent)/
        [a-zA-Z0-9_\-/]+
        \.md
    )
    (?!\]\])               # not already closing a wiki link
    """,
    re.VERBOSE,
)


def convert_bare_paths_to_wiki_links(
    content: str, name_index: dict[str, str]
) -> tuple[str, int]:
    """Convert bare doc paths to wiki links. Returns (new_content, change_count)."""
    changes = 0

    def replace_match(m: re.Match) -> str:
        nonlocal changes
        path = m.group(1)
        stem = Path(path).stem
        if stem in name_index:
            changes += 1
            return f"[[{stem}]]"
        return m.group(0)  # leave as-is if not resolvable

    # Don't replace inside code blocks or existing wiki links
    # Simple approach: process line by line, skip code blocks
    in_code_block = False
    new_lines = []
    for line in content.split("\n"):
        if line.startswith("```"):
            in_code_block = not in_code_block
        if in_code_block or line.startswith("    ") or "`" in line:
            new_lines.append(line)
        else:
            new_lines.append(BARE_PATH_PATTERN.sub(replace_match, line))

    return "\n".join(new_lines), changes


def has_related_section(content: str) -> bool:
    return bool(re.search(r"^##\s+Related", content, re.MULTILINE))


def add_related_section_stub(content: str) -> str:
    """Add a ## Related section stub at the end."""
    stub = "\n## Related\n\n- (add [[wiki links]] to related documents here)\n"
    return content.rstrip() + "\n" + stub + "\n"


def should_have_related_section(file_path: Path, root: Path) -> bool:
    """Only add Related sections to research, spec, task, and ADR files."""
    parts = file_path.relative_to(root).parts
    return any(d in parts for d in ["research", "specs", "active", "done", "adr"])


def main():
    parser = argparse.ArgumentParser(
        description="Maintain Obsidian frontmatter and wiki links."
    )
    parser.add_argument("--fix", action="store_true", help="Apply safe auto-fixes")
    parser.add_argument(
        "--path", default=None, help="Scope to subtree (relative to project root)"
    )
    args = parser.parse_args()

    root = get_project_root()
    subtree = (root / args.path) if args.path else None
    all_files = collect_files(root)
    scan_files = collect_files(root, subtree)
    name_index = build_name_index(all_files, root)

    required_fm_fields = ["title", "tags"]

    findings: list[dict] = []

    for file_path in sorted(scan_files):
        content = file_path.read_text(encoding="utf-8", errors="replace")
        rel = str(file_path.relative_to(root))
        new_content = content

        # FM01: Missing frontmatter entirely
        if not has_frontmatter(content):
            if args.fix:
                new_content = add_frontmatter_stub(new_content, file_path, root)
                findings.append(
                    {"code": "FM01-FIXED", "file": rel, "msg": "Added frontmatter stub"}
                )
            else:
                findings.append(
                    {"code": "FM01", "file": rel, "msg": "Missing frontmatter"}
                )
        else:
            fm, body = extract_frontmatter(content)

            # FM02: Missing required fields
            missing = check_frontmatter_fields(fm, required_fm_fields)
            if missing:
                findings.append(
                    {
                        "code": "FM02",
                        "file": rel,
                        "msg": f"Missing fields: {', '.join(missing)}",
                    }
                )

        # LK01: Bare path references
        body_content = new_content
        converted, count = convert_bare_paths_to_wiki_links(body_content, name_index)
        if count > 0:
            if args.fix:
                new_content = converted
                findings.append(
                    {
                        "code": "LK01-FIXED",
                        "file": rel,
                        "msg": f"Converted {count} bare path(s) to wiki links",
                    }
                )
            else:
                findings.append(
                    {
                        "code": "LK01",
                        "file": rel,
                        "msg": f"{count} bare path reference(s) should be [[wiki links]]",
                    }
                )

        # Related section
        if should_have_related_section(file_path, root) and not has_related_section(
            new_content
        ):
            if args.fix:
                new_content = add_related_section_stub(new_content)
                findings.append(
                    {
                        "code": "RL01-FIXED",
                        "file": rel,
                        "msg": "Added ## Related section stub",
                    }
                )
            else:
                findings.append(
                    {
                        "code": "RL01",
                        "file": rel,
                        "msg": "Missing ## Related section (should have for research/spec/task/adr files)",
                    }
                )

        if args.fix and new_content != content:
            file_path.write_text(new_content, encoding="utf-8")

    # Report
    errors = [f for f in findings if f["code"] in ("FM01", "FM02", "LK01")]
    fixed = [f for f in findings if "FIXED" in f["code"]]
    advisory = [f for f in findings if f["code"] in ("RL01",)]

    if fixed:
        print(f"Auto-fixed {len(fixed)} issue(s):")
        for f in fixed:
            print(f"  [{f['code']}] {f['file']}: {f['msg']}")

    if errors:
        print(f"\nErrors ({len(errors)}) — fix required:")
        for f in errors:
            print(f"  [{f['code']}] {f['file']}: {f['msg']}")

    if advisory:
        print(f"\nAdvisory ({len(advisory)}) — recommended:")
        for f in advisory:
            print(f"  [{f['code']}] {f['file']}: {f['msg']}")

    if not findings:
        print(f"obsidian_linkify: clean — {len(scan_files)} files checked")

    total_errors = len(errors)
    sys.exit(1 if total_errors > 0 else 0)


if __name__ == "__main__":
    main()
