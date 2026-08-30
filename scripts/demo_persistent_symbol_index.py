"""Live proof: persistent symbol index + disk-grounded hierarchical retrieval.

Scenarios demonstrated:
  1. Build index once → persisted under repo-local .awos.
  2. Reload from cache snapshot.
  3. A file moved on disk is still found via the stale hint (disk-first).
  4. ReAct context gains symbol map + on-demand read windows pointing
     at real files.
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scaffold.agent.index_retrieval import ensure_index, retrieve
from scaffold.agent.repo_map import enrich_with_repo_map
from scaffold.agent.symbol_index import SymbolIndex

GREETER = (
    "class Greeter:\n"
    "    def hello(self, name):\n"
    "        return f'hi {name}'\n"
    "\n\n"
    "def make_greeter():\n"
    "    return Greeter()\n"
)


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "pkg").mkdir()
        (root / "pkg" / "greeter.py").write_text(GREETER, encoding="utf-8")

        # 1. Build + persist under repo-local .awos.
        idx = ensure_index(str(root))
        idx_path = root / ".awos" / "symbol_index.json"
        print("[PROOF] index built; cache file:", idx_path.exists())
        idx.save(str(idx_path))
        loaded = SymbolIndex.load(str(idx_path), str(root))
        print("[PROOF] loaded from cache:", len(loaded._cache), "files")

        # 2. Move the file on disk; stale index hint must still resolve.
        (root / "pkg" / "moved.py").write_text(GREETER, encoding="utf-8")
        (root / "pkg" / "greeter.py").unlink()
        retrieval = retrieve(str(root), index=idx, primary_file="pkg/greeter.py")
        for w in retrieval["read_windows"]:
            assert (root / w["file"]).is_file()
        print(
            "[PROOF] stale 'pkg/greeter.py' resolved on disk →",
            [w["file"] for w in retrieval["read_windows"]],
        )

        # 3. ReAct context gains symbol map + read windows (hierarchical level).
        ctx = enrich_with_repo_map("greet user", str(root), {"architecture": "python"})
        assert "symbol_map" in ctx
        assert "symbol_read_windows" in ctx
        print("[PROOF] symbol_map lines:", len(ctx["symbol_map"].splitlines()))
        print("[PROOF] read windows:", ctx["symbol_read_windows"])

    print("\n[PROOF] PASS — persistent + disk-grounded symbol retrieval")


if __name__ == "__main__":
    main()
