#!/usr/bin/env python3
"""Stress test for server stability fix.
Sends N sequential chat queries to the running AWOS server.
Verifies the server survives the workload that previously crashed it at 2-3 queries.
"""

import argparse
import json
import sys
import time
import urllib.request
import urllib.error


def send_query(server_url: str, message: str, timeout: int = 60) -> dict:
    """Send a chat query via SSE endpoint. Returns {'success': bool, 'chunks': int, 'error': str}."""
    chunks = 0
    try:
        req = urllib.request.Request(
            f"{server_url}/api/chat/stream",
            data=json.dumps({"message": message, "chat_id": f"stress-test-{time.time_ns()}"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        # Read SSE stream
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            buffer = b""
            while True:
                chunk = resp.read(8192)
                if not chunk:
                    break
                buffer += chunk
                # Count SSE events
                text = buffer.decode("utf-8", errors="replace")
                chunks += text.count("event: ")
                buffer = chunk[-256:]  # keep tail for potential partial
        return {"success": True, "chunks": chunks, "error": None}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:200]
        return {"success": False, "chunks": 0, "error": f"HTTP {e.code}: {body}"}
    except Exception as e:
        return {"success": False, "chunks": 0, "error": str(e)[:200]}


def check_health(server_url: str) -> bool:
    """Check if server is still alive."""
    try:
        req = urllib.request.Request(f"{server_url}/api/health")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser(description="Stress test AWOS server stability")
    parser.add_argument("--count", type=int, default=20, help="Number of queries to send (default: 20)")
    parser.add_argument("--port", type=int, default=8765, help="Server port (default: 8765)")
    parser.add_argument("--delay", type=float, default=1.0, help="Delay between queries in seconds (default: 1.0)")
    args = parser.parse_args()

    server_url = f"http://127.0.0.1:{args.port}"
    queries = [
        "What is Python?",
        "Explain a for loop",
        "What is a function?",
        "What is a list?",
        "What is a dictionary?",
        "Explain if/else",
        "What is a class?",
        "What is an import?",
        "What is a module?",
        "Explain try/except",
        "What is a tuple?",
        "What is a set?",
        "Explain list comprehension",
        "What is a lambda?",
        "Explain map/filter",
        "What is a decorator?",
        "What is a generator?",
        "Explain context managers",
        "What are type hints?",
        "What is async/await?",
    ][: args.count]

    print("🔬 Server Stability Stress Test")
    print(f"   Target: {server_url}")
    print(f"   Queries: {args.count}")
    print(f"   Delay: {args.delay}s between queries")
    print()

    # Pre-check
    if not check_health(server_url):
        print("❌ SERVER NOT REACHABLE. Ensure it's running:")
        print(f"   python3 gui/server.py --port {args.port}")
        sys.exit(1)
    print("✅ Server is alive (pre-test)")
    print()

    results = []
    success_markers = []

    for i, query in enumerate(queries, 1):
        print(f"  [{i:2d}/{args.count}] Sending: '{query}' ... ", end="", flush=True)
        start = time.monotonic()
        result = send_query(server_url, query)
        elapsed = time.monotonic() - start

        results.append(result)
        if result["success"]:
            print(f"OK ({result['chunks']} chunks, {elapsed:.1f}s)")
            success_markers.append(f"✅ Query {i}: OK")
        else:
            print(f"FAILED: {result['error']}")
            success_markers.append(f"❌ Query {i}: FAILED - {result['error']}")

        if i < len(queries):
            time.sleep(args.delay)

    # Post-check
    print()
    alive = check_health(server_url)
    if alive:
        print("✅ Server is alive (post-test)")
    else:
        print("❌ SERVER CRASHED during test")
    print()

    # Summary
    passed = sum(1 for r in results if r["success"])
    failed = args.count - passed
    avg_time = sum(time.monotonic() - 0 for _ in results) or 0  # placeholder
    print("📊 Results:")
    print(f"   Passed:  {passed}/{args.count}")
    print(f"   Failed:  {failed}/{args.count}")
    print(f"   Alive:   {'YES ✅' if alive else 'NO ❌'}")
    print()
    print("🔍 Success markers:")
    for m in success_markers:
        print(f"   {m}")

    # Exit code based on all passing and server alive
    if passed == args.count and alive:
        print()
        print("🎉 ALL CLEAR — Server stable across all queries!")
        sys.exit(0)
    else:
        print()
        print("⚠️  ISSUES DETECTED — See failures above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
