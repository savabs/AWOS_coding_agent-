# GUI Chat Sliding-Window Context Proof

## Command

```bash
python3 - <<'PY'
import tempfile
from scaffold.agent.gui_chat import ChatMessage, ChatSession, GuiChatService, GuiChatStore
svc = GuiChatService(codebase_root=tempfile.mkdtemp(), store=GuiChatStore(tempfile.mkdtemp()))
s = ChatSession(chat_id="x")
for i in range(20):
    s.messages.append(ChatMessage(role="user", content=f"q{i}"))
    s.messages.append(ChatMessage(role="assistant", content=f"a{i}"))
msgs = svc._conversation_messages(s, max_turns=6)
print("num messages:", len(msgs))
print("first (compact):", msgs[0]["content"][:90], "...")
print("total chars:", sum(len(m["content"]) for m in msgs))
PY
```

## Success Markers

- `num messages: 13` — 1 compact summary + 6 recent turns × 2 (bounded, not 39).
- `first (compact): Earlier conversation (compact): ...` — older turns collapsed into a summary.
- `total chars:` small (a few hundred) — prompt stays bounded regardless of session length.

## What It Demonstrates

A long 20-turn chat is reduced to a bounded prompt: recent turns kept verbatim, older turns collapsed into a single compact summary. Context is preserved without unbounded growth.

## Failure Signals

- `num messages` grows with session length (bounded window broken).
- No `Earlier conversation (compact)` summary appears when the session exceeds the window.

## Automated Coverage

`tests/test_gui_chat_prompt_fidelity.py` — recent/compact split, no-summary-under-window, message char cap.
