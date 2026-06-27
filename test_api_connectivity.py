#!/usr/bin/env python3
"""
Pseudo connectivity test for AWOS LLM providers.
Makes minimal cheap calls (~$0.0001 each) to verify API keys work.
Does NOT modify any files.
"""
import os
import sys
from pathlib import Path

# Load .env
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

# Add agent to path
sys.path.insert(0, str(Path(__file__).parent / "scaffold" / "agent"))


def test_openai():
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        return False, "OPENAI_API_KEY not set"
    try:
        from openai import OpenAI
        client = OpenAI(api_key=key)
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "Say 'ok'"}],
            max_tokens=5,
            temperature=0.0,
        )
        text = resp.choices[0].message.content.strip()
        return True, f"gpt-4o-mini responded: '{text}'"
    except Exception as e:
        return False, f"OpenAI error: {e}"


def test_deepseek():
    key = os.getenv("DEEPSEEK_API_KEY")
    if not key:
        return False, "DEEPSEEK_API_KEY not set"
    try:
        from openai import OpenAI
        client = OpenAI(api_key=key, base_url="https://api.deepseek.com")
        resp = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": "Say 'ok'"}],
            max_tokens=5,
            temperature=0.0,
        )
        text = resp.choices[0].message.content.strip()
        return True, f"deepseek-chat responded: '{text}'"
    except Exception as e:
        return False, f"DeepSeek error: {e}"


def test_anthropic():
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return False, "ANTHROPIC_API_KEY not set"
    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=key)
        resp = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=5,
            messages=[{"role": "user", "content": "Say 'ok'"}],
        )
        text = resp.content[0].text.strip()
        return True, f"claude-haiku responded: '{text}'"
    except Exception as e:
        return False, f"Anthropic error: {e}"


if __name__ == "__main__":
    print("=" * 50)
    print("AWOS API Connectivity Pseudo-Test")
    print("Each call uses ~1-5 tokens — total cost < $0.001")
    print("=" * 50)

    tests = [
        ("DeepSeek", test_deepseek),
        ("OpenAI", test_openai),
        ("Anthropic", test_anthropic),
    ]

    all_ok = True
    for name, fn in tests:
        print(f"\n{name} ...", end=" ")
        ok, msg = fn()
        if ok:
            print("OK")
            print(f"  → {msg}")
        else:
            print("FAIL")
            print(f"  → {msg}")
            all_ok = False

    print("\n" + "=" * 50)
    if all_ok:
        print("All configured providers are reachable.")
    else:
        print("Some providers failed. Check keys and network.")
    print("=" * 50)
