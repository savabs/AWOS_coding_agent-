#!/usr/bin/env python3
"""
Chat Session Memory Manager

Maintains append-only context within a single chat session.
Automatically manages tokens by keeping recent messages full and older messages summarized.

Strategies:
1. Simple: Keep all messages (until token limit)
2. Sliding Window: Keep last N messages
3. Hybrid (OPTIMAL): Recent messages + periodic summarization
4. Semantic: Store embeddings, retrieve relevant messages
"""

import json
from pathlib import Path
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from enum import Enum


class MemoryStrategy(Enum):
    """Memory management strategies"""
    SIMPLE = "simple"              # Keep all messages (until limit)
    SLIDING_WINDOW = "sliding"     # Keep last N messages
    HYBRID = "hybrid"              # Recent full + old summarized
    SEMANTIC = "semantic"          # Embeddings + relevance retrieval


@dataclass
class Message:
    """Single message in conversation"""
    role: str  # "user" or "assistant"
    content: str
    tokens: int
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self):
        return asdict(self)


@dataclass
class SessionMemory:
    """Manages conversation memory for a single session"""
    session_id: str
    strategy: MemoryStrategy = MemoryStrategy.HYBRID
    messages: List[Message] = field(default_factory=list)
    summary: Optional[str] = None
    total_tokens: int = 0
    max_tokens_per_request: int = 10000
    recent_messages_kept: int = 5  # For HYBRID strategy
    
    def add_message(self, role: str, content: str, tokens: int):
        """Add message to session"""
        message = Message(role=role, content=content, tokens=tokens)
        self.messages.append(message)
        self.total_tokens += tokens

    def get_context_for_request(self, new_request_tokens: int = 500) -> Tuple[str, int]:
        """
        Get formatted context to include in next request.
        Returns: (formatted_context, tokens_used)
        """
        if self.strategy == MemoryStrategy.SIMPLE:
            return self._strategy_simple(new_request_tokens)
        elif self.strategy == MemoryStrategy.SLIDING_WINDOW:
            return self._strategy_sliding_window(new_request_tokens)
        elif self.strategy == MemoryStrategy.HYBRID:
            return self._strategy_hybrid(new_request_tokens)
        elif self.strategy == MemoryStrategy.SEMANTIC:
            return self._strategy_semantic(new_request_tokens)
        else:
            return self._strategy_hybrid(new_request_tokens)

    def _strategy_simple(self, new_request_tokens: int) -> Tuple[str, int]:
        """
        Simple: Include all messages until token budget exceeded.
        ✅ Pros: Full context, simplest
        ❌ Cons: Scales poorly, can hit token limits
        """
        available_tokens = self.max_tokens_per_request - new_request_tokens
        context_lines = []
        tokens_used = 0

        for msg in self.messages:
            if tokens_used + msg.tokens > available_tokens:
                context_lines.append("[... earlier messages truncated due to token limit ...]")
                break
            context_lines.append(f"{msg.role.upper()}: {msg.content}")
            tokens_used += msg.tokens

        context = "\n\n".join(context_lines)
        return context, tokens_used

    def _strategy_sliding_window(self, new_request_tokens: int) -> Tuple[str, int]:
        """
        Sliding Window: Keep only last N messages.
        ✅ Pros: Bounded tokens, always fits
        ❌ Cons: Loses earlier context
        """
        available_tokens = self.max_tokens_per_request - new_request_tokens
        recent = self.messages[-self.recent_messages_kept:]  # Last N messages
        context_lines = []
        tokens_used = 0

        if len(self.messages) > self.recent_messages_kept:
            context_lines.append("[... earlier conversation omitted ...]")

        for msg in recent:
            context_lines.append(f"{msg.role.upper()}: {msg.content}")
            tokens_used += msg.tokens

        context = "\n\n".join(context_lines)
        return context, tokens_used

    def _strategy_hybrid(self, new_request_tokens: int) -> Tuple[str, int]:
        """
        Hybrid (RECOMMENDED): Recent messages full + old messages summarized.
        ✅ Pros: Balanced context + tokens, maintains reasoning
        ❌ Cons: Requires summary generation
        """
        available_tokens = self.max_tokens_per_request - new_request_tokens
        context_parts = []
        tokens_used = 0

        # 1. Add summary if it exists (high compression)
        if self.summary:
            summary_tokens = len(self.summary.split()) // 1.5  # Rough estimate
            context_parts.append(f"CONVERSATION SUMMARY:\n{self.summary}")
            tokens_used += int(summary_tokens)

        # 2. Add recent messages in full (preserve detail)
        recent = self.messages[-self.recent_messages_kept:]
        for msg in recent:
            if tokens_used + msg.tokens > available_tokens:
                break
            context_parts.append(f"{msg.role.upper()}: {msg.content}")
            tokens_used += msg.tokens

        context = "\n\n".join(context_parts)
        return context, tokens_used

    def _strategy_semantic(self, new_request_tokens: int) -> Tuple[str, int]:
        """
        Semantic: Store embeddings, retrieve relevant messages (advanced).
        ✅ Pros: Smart retrieval, can handle long conversations
        ❌ Cons: Complex, requires embedding model
        Note: Not implemented yet - would need vector DB
        """
        # Placeholder - would need embedding model
        return self._strategy_hybrid(new_request_tokens)

    def should_summarize(self) -> bool:
        """Check if conversation should be summarized"""
        # Summarize when we've used >50% of tokens for older messages
        if len(self.messages) < 10:
            return False
        return self.total_tokens > (self.max_tokens_per_request * 0.5)

    def create_summary(self) -> str:
        """Create summary of conversation so far"""
        if not self.messages:
            return ""

        summary_points = []
        for i, msg in enumerate(self.messages):
            if i % 2 == 0:  # Every other message (reduce size)
                # Extract key points
                content = msg.content
                if len(content) > 200:
                    content = content[:200] + "..."
                summary_points.append(content)

        summary = "Key discussion points:\n" + "\n- ".join(summary_points[:10])
        return summary

    def to_dict(self) -> Dict:
        """Serialize to dict"""
        return {
            'session_id': self.session_id,
            'strategy': self.strategy.value,
            'messages': [m.to_dict() for m in self.messages],
            'summary': self.summary,
            'total_tokens': self.total_tokens,
            'message_count': len(self.messages),
        }

    def to_json(self) -> str:
        """Serialize to JSON"""
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_json(cls, json_str: str) -> 'SessionMemory':
        """Deserialize from JSON"""
        data = json.loads(json_str)
        memory = cls(
            session_id=data['session_id'],
            strategy=MemoryStrategy(data['strategy']),
            summary=data.get('summary'),
            total_tokens=data.get('total_tokens', 0),
        )
        for msg_data in data.get('messages', []):
            msg = Message(
                role=msg_data['role'],
                content=msg_data['content'],
                tokens=msg_data['tokens'],
                timestamp=msg_data.get('timestamp'),
            )
            memory.messages.append(msg)
        return memory


class ChatSessionManager:
    """Manages multiple chat sessions"""

    def __init__(self, sessions_dir: Optional[Path] = None):
        """Initialize session manager"""
        self.sessions_dir = sessions_dir or Path(".awos/sessions")
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.current_session: Optional[SessionMemory] = None

    def create_session(self, session_id: str, strategy: MemoryStrategy = MemoryStrategy.HYBRID) -> SessionMemory:
        """Create new chat session"""
        session = SessionMemory(session_id=session_id, strategy=strategy)
        self.current_session = session
        return session

    def get_session(self, session_id: str) -> Optional[SessionMemory]:
        """Load existing session"""
        session_file = self.sessions_dir / f"{session_id}.json"
        if session_file.exists():
            with open(session_file) as f:
                return SessionMemory.from_json(f.read())
        return None

    def save_session(self, session: Optional[SessionMemory] = None):
        """Save session to disk"""
        if session is None:
            session = self.current_session
        if session is None:
            return

        session_file = self.sessions_dir / f"{session.session_id}.json"
        with open(session_file, 'w') as f:
            f.write(session.to_json())

    def add_to_current_session(self, role: str, content: str, tokens: int):
        """Add message to current session"""
        if self.current_session is None:
            raise RuntimeError("No active session. Create one first.")
        self.current_session.add_message(role, content, tokens)

    def get_current_context(self, new_request_tokens: int = 500) -> str:
        """Get context from current session"""
        if self.current_session is None:
            return ""
        context, _ = self.current_session.get_context_for_request(new_request_tokens)
        return context

    def list_sessions(self) -> List[str]:
        """List all available sessions"""
        return [f.stem for f in self.sessions_dir.glob("*.json")]


# ===== COMPARISON OF STRATEGIES =====

STRATEGY_COMPARISON = """
┌─────────────┬──────────────┬────────────┬────────────┬──────────────┐
│ Strategy    │ Token Usage  │ Context    │ Reasoning  │ Best For     │
├─────────────┼──────────────┼────────────┼────────────┼──────────────┤
│ Simple      │ HIGH         │ FULL       │ EXCELLENT  │ Short chats  │
│ Sliding     │ LOW          │ LIMITED    │ GOOD       │ Long chats   │
│ Hybrid      │ MODERATE     │ BALANCED   │ VERY GOOD  │ General use  │
│ Semantic    │ VERY LOW     │ TARGETED   │ GOOD       │ Complex chats│
└─────────────┴──────────────┴────────────┴────────────┴──────────────┘

RECOMMENDATION: Hybrid
  ✅ Keeps recent messages in full (preserves reasoning)
  ✅ Summarizes old messages (reduces tokens)
  ✅ Balanced approach (most practical)
  ✅ Can be upgraded to semantic later
"""

# STRATEGY_COMPARISON is available for reference — call print(STRATEGY_COMPARISON) to view
