#!/usr/bin/env python3
"""
Fetch a small sample of SWE-CI tasks for AWOS testing.

SWE-CI is 100 tasks × ~233 days × 71 commits each — full run is ~48 hours.
This script fetches metadata and identifies 3-5 Python tasks suitable for a proof run.
"""

import json
from pathlib import Path

try:
    from datasets import load_dataset
    HAS_DATASETS = True
except ImportError:
    HAS_DATASETS = False

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "research"
OUT.mkdir(parents=True, exist_ok=True)

def fetch_sweci_metadata():
    """Fetch SWE-CI dataset metadata from HuggingFace."""
    if not HAS_DATASETS:
        print("ERROR: pip install datasets")
        return None
    
    print("Fetching SWE-CI dataset from HuggingFace (metadata only)...")
    print("Dataset: skylenage-ai/SWE-CI")
    
    try:
        # Load just the first few samples to understand structure
        ds = load_dataset("skylenage-ai/SWE-CI", split="test", streaming=True)
        samples = []
        for i, item in enumerate(ds):
            if i >= 10:
                break
            samples.append({
                "task_id": item.get("task_id"),
                "repo": item.get("repo"),
                "base_commit": item.get("base_commit"),
                "target_commit": item.get("target_commit"),
                "language": item.get("language"),
                "num_commits": item.get("num_commits"),
                "duration_days": item.get("duration_days"),
                "lines_changed": item.get("lines_changed"),
            })
            print(f"  [{i+1}] {item.get('task_id')} — {item.get('repo')} — {item.get('num_commits')} commits, {item.get('duration_days')} days")
        
        return samples
    
    except Exception as e:
        print(f"ERROR fetching dataset: {e}")
        return None

def select_python_tasks(samples):
    """Filter for smaller Python tasks suitable for AWOS proof."""
    if not samples:
        return []
    
    # Prefer tasks with:
    # - Python language
    # - Fewer commits (easier to debug)
    # - Moderate duration (not too long)
    python = [s for s in samples if s.get("language") == "python"]
    if not python:
        python = samples  # fallback
    
    # Sort by num_commits ascending
    python.sort(key=lambda x: x.get("num_commits") or 999)
    
    return python[:5]

def main():
    samples = fetch_sweci_metadata()
    if not samples:
        print("\nFailed to fetch SWE-CI samples.")
        print("The dataset requires ~50GB download and Docker runtime.")
        print("For AWOS proof, we'll synthesize a similar multi-commit evolution test instead.")
        return
    
    selected = select_python_tasks(samples)
    
    out_path = OUT / "sweci_sample_tasks.json"
    out_path.write_text(json.dumps(selected, indent=2), encoding="utf-8")
    
    print(f"\n✓ Wrote {len(selected)} candidate tasks to {out_path}")
    print("\nNext steps:")
    print("1. Full SWE-CI requires 48h + Docker + 50GB dataset")
    print("2. For AWOS proof, we can:")
    print("   a) Run 1-2 SWE-CI tasks (Docker required)")
    print("   b) Build a lighter multi-commit evolution test on real repo")
    print("   c) Use Aider polyglot benchmark (225 exercises, no Docker)")

if __name__ == "__main__":
    main()
