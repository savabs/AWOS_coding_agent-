"""edit_grammar_probe.py — send one edit request to the local llama-server with
and without the T4 copy-constraint grammar; print latency and the reply.

  python scripts/edit_grammar_probe.py <file> <function-name> [port]
"""

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scaffold" / "agent"))
import edit_grammar as eg  # noqa: E402


def main() -> None:
    src, fn = sys.argv[1], sys.argv[2]
    port = sys.argv[3] if len(sys.argv) > 3 else "8080"
    g, note = eg.grammar_for_files(".", [src])
    print("grammar:", note)
    content = Path(src).read_text()
    msgs = [
        {"role": "system", "content": "Reply with SEARCH/REPLACE blocks: a path line, "
         "<<<<<<< SEARCH, exact lines from the file, =======, new lines, >>>>>>> REPLACE."},
        {"role": "user", "content": f"{src}\n```\n{content}\n```\nTask: in function `{fn}`, "
         "add a one-line comment at the top of its body. Explain in one sentence, then give one block."},
    ]
    for use in (False, True):
        body = {"model": "local", "messages": msgs, "max_tokens": 400, "temperature": 0}
        if use:
            body["grammar"] = g
        t = time.time()
        req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions",
                                     data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            r = json.load(urllib.request.urlopen(req, timeout=600))
        except urllib.error.HTTPError as e:
            print("HTTP", e.code, e.read()[:500])
            continue
        tm = r.get("timings", {})
        print("=== grammar" if use else "=== free", f"{time.time() - t:.1f}s",
              r["usage"], "tok/s=", tm.get("predicted_per_second"))
        print(r["choices"][0]["message"]["content"][:1500])


if __name__ == "__main__":
    main()
