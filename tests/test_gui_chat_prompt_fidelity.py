"""Regression tests for chat input fidelity and provider request tracing."""

from __future__ import annotations

from pathlib import Path

from scaffold.agent.gui_chat import ChatMessage, ChatSession, GuiChatService, GuiChatStore

MOAT_PROMPT = "lets not tell me whats the most important moat of this project"


def test_raw_user_input_is_preserved_exactly(tmp_path: Path) -> None:
    store = GuiChatStore(tmp_path / ".awos" / "gui_chat")
    session = store.create(title=MOAT_PROMPT)
    session.messages.append(ChatMessage(role="user", content=MOAT_PROMPT, mode="auto"))
    store.save(session)

    loaded = store.load(session.chat_id)

    assert loaded is not None
    assert loaded.messages[0].content == MOAT_PROMPT


def test_provider_messages_include_prior_turns(tmp_path: Path) -> None:
    service = GuiChatService(codebase_root=str(tmp_path), store=GuiChatStore(tmp_path / "chat"))
    session = ChatSession(chat_id="chat_test")
    session.messages.extend(
        [
            ChatMessage(role="user", content="What is AWOS?"),
            ChatMessage(role="assistant", content="AWOS is App #1 coding-agent infrastructure."),
            ChatMessage(role="user", content=MOAT_PROMPT),
        ]
    )

    messages = service._provider_messages("system", "Question: " + MOAT_PROMPT, session)

    assert [message["role"] for message in messages] == [
        "system",
        "user",
        "assistant",
        "user",
    ]
    assert messages[-1]["content"] == "Question: " + MOAT_PROMPT


def test_qa_prompt_requires_intent_fidelity(tmp_path: Path) -> None:
    service = GuiChatService(codebase_root=str(tmp_path), store=GuiChatStore(tmp_path / "chat"))
    session = ChatSession(chat_id="chat_test")

    system, _user, _cache_key, _prior_turns = service._build_qa_prompt(session, MOAT_PROMPT)

    assert "never silently rewrite or correct intent" in system
    assert "ask for clarification" in system
    assert "label the interpretation as an assumption" in system


def test_prompt_trace_is_bounded_and_secret_safe(tmp_path: Path) -> None:
    service = GuiChatService(codebase_root=str(tmp_path), store=GuiChatStore(tmp_path / "chat"))
    session = ChatSession(chat_id="chat_test")
    session.messages.append(ChatMessage(role="user", content=MOAT_PROMPT))

    trace = service._prompt_trace(
        message=MOAT_PROMPT,
        mode="qa",
        provider="opencode_go",
        model="qwen3.7-plus",
        session=session,
        system="AWOS system instructions",
        user="Question: " + MOAT_PROMPT,
    )

    assert trace["raw_user_input"] == MOAT_PROMPT
    assert trace["provider"] == "opencode_go"
    assert trace["model"] == "qwen3.7-plus"
    assert trace["prior_turns"] == 0
    assert trace["provider_message_roles"] == ["system", "user"]
    assert "API_KEY" not in str(trace)
    assert "instructions" not in trace


def test_chat_ui_has_distinct_collapsed_request_trace() -> None:
    source = Path("gui/static/chat.js").read_text(encoding="utf-8")

    assert "function renderPromptTrace(meta)" in source
    assert "<details class=\"prompt-trace\">" in source
    assert "JSON.stringify(trace, null, 2)" in source
    assert "extra += renderPromptTrace(meta);" in source


def _many_turn_session(n: int) -> ChatSession:
    session = ChatSession(chat_id="chat_slide")
    for i in range(n):
        session.messages.append(ChatMessage(role="user", content=f"question {i}"))
        session.messages.append(ChatMessage(role="assistant", content=f"answer {i}"))
    return session


def test_conversation_bounds_recent_and_compacts_older(tmp_path: Path) -> None:
    service = GuiChatService(codebase_root=str(tmp_path), store=GuiChatStore(tmp_path / "chat"))
    session = _many_turn_session(20)  # 20 user + 20 assistant (last excluded)

    messages = service._conversation_messages(session, max_turns=6)

    # 1 compact summary + 6 recent turns * 2 = 13 messages
    assert len(messages) == 1 + 6 * 2
    assert messages[0]["content"].startswith("Earlier conversation (compact):")
    # last prior message is user "question 19" (assistant 19 was just appended)
    assert messages[-1]["content"] == "question 19"


def test_conversation_under_recent_window_has_no_summary(tmp_path: Path) -> None:
    service = GuiChatService(codebase_root=str(tmp_path), store=GuiChatStore(tmp_path / "chat"))
    session = _many_turn_session(3)

    messages = service._conversation_messages(session, max_turns=6)
    assert not any(m["content"].startswith("Earlier conversation") for m in messages)
    assert len(messages) == 5  # 6 msgs - last excluded


def test_conversation_message_cap_applies(tmp_path: Path) -> None:
    service = GuiChatService(codebase_root=str(tmp_path), store=GuiChatStore(tmp_path / "chat"))
    session = ChatSession(chat_id="chat_cap")
    session.messages.append(ChatMessage(role="user", content="x" * 9000))
    session.messages.append(ChatMessage(role="assistant", content="reply"))

    messages = service._conversation_messages(session, max_turns=6)
    assert len(messages) == 1
    assert messages[0]["content"] == "x" * 4000
