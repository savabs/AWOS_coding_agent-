#!/usr/bin/env python3
# python3 is the correct interpreter on this system
"""
prewarm_cache.py — Pre-warm Claude prompt cache before an Aider session.
Sends a max_tokens=0 request to write CONVENTIONS.md to cache.
Zero output tokens billed. Run this at the start of each work session.

Usage: python scripts/prewarm_cache.py
"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv


def main():
    load_dotenv()

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not found in .env")
        sys.exit(1)

    # Import here so script fails gracefully if not installed
    try:
        import anthropic
    except ImportError:
        print("ERROR: anthropic not installed. Run: pip install anthropic")
        sys.exit(1)

    conventions_path = Path("CONVENTIONS.md")
    if not conventions_path.exists():
        print("ERROR: CONVENTIONS.md not found. Run from project root.")
        sys.exit(1)

    conventions_text = conventions_path.read_text(encoding="utf-8")
    model = "claude-sonnet-4-5"

    print(f"Pre-warming cache for {model}...")
    print(f"CONVENTIONS.md: {len(conventions_text.split())} words")

    client = anthropic.Anthropic(api_key=api_key)

    response = client.messages.create(
        model=model,
        max_tokens=0,
        system=[
            {
                "type": "text",
                "text": conventions_text,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": "warmup"}],
    )

    usage = response.usage
    cache_written = getattr(usage, "cache_creation_input_tokens", 0)
    cache_read = getattr(usage, "cache_read_input_tokens", 0)
    input_tokens = getattr(usage, "input_tokens", 0)

    print(f"\nCache pre-warm complete:")
    print(f"  cache_creation_input_tokens: {cache_written:,}")
    print(f"  cache_read_input_tokens:     {cache_read:,}")
    print(f"  input_tokens (uncached):     {input_tokens:,}")
    print(f"  output_tokens:               0 (max_tokens=0)")
    print(f"  stop_reason:                 {response.stop_reason}")

    if cache_written == 0 and cache_read == 0:
        total_tokens = input_tokens + cache_written + cache_read
        print(f"\nWARNING: Nothing was cached. Total tokens sent: {total_tokens}")
        print(f"Minimum for caching on {model}: 1024 tokens")
        print(f"CONVENTIONS.md may be too short. Check token count.")
    elif cache_written > 0:
        print(f"\nCache WRITTEN. Next request reads at 10% cost for 5 minutes.")
    elif cache_read > 0:
        print(f"\nCache HIT — already warm. No write cost.")

    print()


if __name__ == "__main__":
    main()
