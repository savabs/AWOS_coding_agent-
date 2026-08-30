#!/usr/bin/env python3
"""Profile the AWOS chat pipeline to identify speed bottlenecks."""

import sys
import time

sys.path.insert(0, ".")

from scaffold.agent.gui_chat import GuiChatService, ChatSession


def profile_phase(name, fn, *args, **kwargs):
    t0 = time.perf_counter()
    try:
        result = fn(*args, **kwargs)
    except Exception as e:
        t1 = time.perf_counter()
        print(f"  {name}: FAILED after {(t1-t0)*1000:.0f}ms — {e}")
        return None, (t1-t0)
    t1 = time.perf_counter()
    ms = (t1 - t0) * 1000
    return result, ms


def main():
    print("=" * 60)
    print("AWOS Chat Pipeline — Speed Profile")
    print("=" * 60)

    svc = GuiChatService(".")
    query = "What is AWOS?"
    session = ChatSession(chat_id="perf-probe")

    results = []

    # ── Phase 1: read_project_context ──
    ctx, ms = profile_phase("_read_project_context", svc._read_project_context, query)
    results.append(("read_project_context", ms, f"{len(ctx) if ctx else 0} chars"))

    # ── Phase 2: _read_soul_context ──
    soul, ms = profile_phase("_read_soul_context", svc._read_soul_context)
    results.append(("read_soul_context", ms, f"{len(soul) if soul else 0} chars"))

    # ── Phase 3: _build_qa_prompt ──
    prompt_data, ms = profile_phase("_build_qa_prompt", svc._build_qa_prompt, session, query)
    if prompt_data:
        system, user, cache_key, prior_turns = prompt_data
        results.append(("build_qa_prompt", ms, f"system={len(system)}c, user={len(user)}c, prior_turns={prior_turns}"))
    else:
        results.append(("build_qa_prompt", ms, "FAILED"))

    # ── Phase 4: _qa_context_blob ──
    blob, ms = profile_phase("_qa_context_blob", svc._qa_context_blob, session, query)
    results.append(("qa_context_blob", ms, f"{len(blob) if blob else 0} chars"))

    # ── Phase 5: response_cache.get ──
    if prompt_data:
        _, _, cache_key, _ = prompt_data
        cached, ms = profile_phase("response_cache.get", svc.response_cache.get, cache_key)
        results.append(("cache_lookup", ms, "HIT" if cached else "MISS"))
    else:
        results.append(("cache_lookup", 0, "SKIPPED"))

    # ── Phase 6: _llm_messages ──
    if prompt_data:
        system, user, _, _ = prompt_data
        msgs, ms = profile_phase("_llm_messages", svc._llm_messages, system, user, session)
        results.append(("llm_messages", ms, f"{len(msgs) if msgs else 0} msgs"))
    else:
        results.append(("llm_messages", 0, "SKIPPED"))

    # ── Summary ──
    total_ms = sum(r[1] for r in results)
    print()
    print("-" * 60)
    print(f"{'Phase':<30} {'Time':>8}   {'Details'}")
    print("-" * 60)
    for name, ms, detail in results:
        print(f"{name:<30} {ms:>6.0f}ms   {detail}")
    print("-" * 60)
    print(f"{'TOTAL (no LLM call)':<30} {total_ms:>6.0f}ms")
    print()

    # ── Phase 7: Actual LLM streaming (optional — costs API credits) ──
    print("─" * 60)
    print("LLM STREAMING CALL (if OPENCODE_GO_API_KEY is set)")
    print("─" * 60)
    if prompt_data:
        system, user, _, _ = prompt_data
        import os
        if os.getenv("OPENCODE_GO_API_KEY"):
            print("Running streaming call (this costs API credits)...")
            t0 = time.perf_counter()
            chunks = []
            first_chunk_at = None
            try:
                gen = svc._stream_opencode_go(system, user, session, max_tokens=512)
                for chunk in gen:
                    if first_chunk_at is None:
                        first_chunk_at = time.perf_counter()
                    chunks.append(chunk)
            except Exception as e:
                t1 = time.perf_counter()
                print(f"  STREAM FAILED: {e} ({(t1-t0)*1000:.0f}ms)")
                return

            t1 = time.perf_counter()
            ttfb = (first_chunk_at - t0) * 1000 if first_chunk_at else 0
            total_ms = (t1 - t0) * 1000
            text = "".join(chunks)
            print(f"  Model:  {svc._last_model}")
            print(f"  TTFB:   {ttfb:.0f}ms")
            print(f"  Total:  {total_ms:.0f}ms")
            print(f"  Chunks: {len(chunks)}")
            print(f"  Output: {len(text)} chars, ~{len(text)//4} tokens")
            print(f"  Tokens: in={svc._last_prompt_tokens} out={svc._last_completion_tokens}")
            print(f"  Cost:   ${svc._last_cost:.5f}")
            print()
            print(f"{'TOTAL (with LLM call)':<30} {(total_ms + sum(r[1] for r in results)):>6.0f}ms")
        else:
            print("  SKIPPED — OPENCODE_GO_API_KEY not set")


if __name__ == "__main__":
    main()
