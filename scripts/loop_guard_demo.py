#!/usr/bin/env python3
"""
loop_guard_demo.py — live proof that the loop breaker trips on a real stream.

Asks the pinned model (DeepSeek V4 Flash via OpenRouter) for a deliberately
repetitive reply through the same path one-shot uses (_UsageTap +
utility_chat), with AWOS_LOOP_BREAKER=1 and a low max_tokens. Expected:
"[LOOP-GUARD] tripped: repeated_lines ..." on stderr, the stream aborted after
a few lines, one resample, and a trimmed reply. Costs well under $0.01.

    python scripts/loop_guard_demo.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scaffold" / "agent"))
os.environ["AWOS_LOOP_BREAKER"] = "1"

from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO / ".env")

from one_shot import _UsageTap, one_shot_client  # noqa: E402
from providers import REASONING_OFF, utility_chat, TruncatedReplyError  # noqa: E402

MODEL = os.environ.get("AWOS_LOOP_DEMO_MODEL", "deepseek/deepseek-v4-flash")
PROMPT = ("Write the sentence 'The parser must return the last dotted name.' exactly "
          "150 times, one per line, with nothing else before or after.")


def main() -> int:
    client = one_shot_client()
    if client is None:
        print("no client configured")
        return 1
    tap = _UsageTap(client)
    t0 = time.time()
    try:
        text, info = utility_chat(tap, "loop_guard_demo", MODEL,
                                  [{"role": "user", "content": PROMPT}],
                                  max_tokens=1200, reasoning=dict(REASONING_OFF), retry=False)
        finish = info.get("finish_reason")
        cost = info.get("cost_usd")
    except TruncatedReplyError as exc:
        text, finish, cost = exc.text, "length", exc.cost_usd
    lines = [l for l in text.splitlines() if l.strip()]
    print(f"elapsed {time.time() - t0:.1f}s, finish={finish}, kept {len(text)} chars / "
          f"{len(lines)} line(s), tokens in/out {tap.input_tokens}/{tap.output_tokens}, "
          f"cost ${float(cost or 0):.5f}")
    print("kept text:", repr(text[:200]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
