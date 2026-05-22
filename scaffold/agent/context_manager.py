"""
ContextManager — Budget-Aware Context Dehydration

Problem: A 50-turn chat means you send 100,000 system prompt tokens.
Solution: Budget tracker + automatic dehydration when context grows too large.

Workflow:
  1. Track token usage per request
  2. If approaching limit, "dehydrate" (remove) oldest/least-referenced blocks
  3. Keep a "delta" state (TASK_STATE.xml) instead of full chat history
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional
from datetime import datetime
import json
from pathlib import Path


@dataclass
class ContextBlock:
    """A block of context (code file, reference, etc.)."""
    id: str
    name: str
    content: str
    tokens: int
    priority: int = 0  # Higher = keep longer
    last_referenced: datetime = field(default_factory=datetime.now)
    
    def age_minutes(self) -> int:
        """Minutes since last referenced."""
        return int((datetime.now() - self.last_referenced).total_seconds() / 60)


class ContextManager:
    """Manage context efficiently within token budget."""
    
    def __init__(self, max_tokens: int = 10000, budget_dollars: float = 15.0):
        self.max_tokens = max_tokens
        self.budget_dollars = budget_dollars
        self.blocks: Dict[str, ContextBlock] = {}
        self.token_counter = 0
        self.cost_counter = 0.0
        self.token_price_mtok = 5.0  # Claude Sonnet input
    
    def add_block(self, block_id: str, name: str, content: str, priority: int = 0) -> int:
        """
        Add a context block. Returns estimated tokens.
        
        Args:
            block_id: Unique identifier
            name: Human-readable name
            content: The actual content
            priority: 0-10 (higher = keep longer when dehydrating)
        """
        # Rough token estimate: 1 token ≈ 4 characters
        estimated_tokens = len(content) // 4
        
        self.blocks[block_id] = ContextBlock(
            id=block_id,
            name=name,
            content=content,
            tokens=estimated_tokens,
            priority=priority,
        )
        
        self.token_counter += estimated_tokens
        return estimated_tokens
    
    def remove_block(self, block_id: str) -> int:
        """Remove a block. Returns tokens freed."""
        if block_id in self.blocks:
            tokens = self.blocks[block_id].tokens
            del self.blocks[block_id]
            self.token_counter -= tokens
            return tokens
        return 0
    
    def dehydrate(self, target_tokens: int) -> Dict[str, int]:
        """
        Remove least-important blocks until under target.
        
        Returns:
            {"block_id": tokens_freed, ...}
        """
        if self.token_counter <= target_tokens:
            return {}
        
        removed = {}
        
        # Sort by: priority (ascending), then age (descending)
        sorted_blocks = sorted(
            self.blocks.values(),
            key=lambda b: (b.priority, -b.age_minutes())
        )
        
        for block in sorted_blocks:
            if self.token_counter <= target_tokens:
                break
            
            tokens = self.remove_block(block.id)
            removed[block.id] = tokens
            print(f"🔄 Dehydrated '{block.name}' ({tokens} tokens)")
        
        return removed
    
    def get_budget_status(self) -> Dict:
        """Current budget and token status."""
        cost_so_far = (self.token_counter / 1_000_000) * self.token_price_mtok
        remaining_budget = self.budget_dollars - cost_so_far
        
        return {
            "tokens_used": self.token_counter,
            "max_tokens": self.max_tokens,
            "token_percent": (self.token_counter / self.max_tokens) * 100,
            "cost_so_far": cost_so_far,
            "budget_dollars": self.budget_dollars,
            "remaining_budget": remaining_budget,
            "blocks_loaded": len(self.blocks),
        }
    
    def check_budget(self) -> bool:
        """Returns True if we're still under budget."""
        status = self.get_budget_status()
        return status["remaining_budget"] > 0
    
    def emergency_dehydrate(self, threshold_percent: float = 80) -> Dict[str, int]:
        """
        If token usage exceeds threshold, emergency dehydrate.
        
        Args:
            threshold_percent: Trigger dehydration if > 80% of max_tokens
        
        Returns:
            {"block_id": tokens_freed, ...}
        """
        status = self.get_budget_status()
        
        if status["token_percent"] > threshold_percent:
            target = int(self.max_tokens * 0.6)  # Drop to 60% of max
            print(f"⚠️  Emergency dehydration: {status['token_percent']:.0f}% usage")
            return self.dehydrate(target)
        
        return {}
    
    def print_context_status(self):
        """Display current context status."""
        status = self.get_budget_status()
        
        print("\n" + "=" * 70)
        print("CONTEXT MANAGER STATUS")
        print("=" * 70)
        print(f"Tokens: {status['tokens_used']}/{status['max_tokens']} ({status['token_percent']:.1f}%)")
        print(f"Cost: ${status['cost_so_far']:.4f} / ${status['budget_dollars']:.2f}")
        print(f"Remaining Budget: ${status['remaining_budget']:.4f}")
        print(f"Blocks Loaded: {status['blocks_loaded']}")
        print("\nBlocks:")
        for block in sorted(self.blocks.values(), key=lambda b: -b.tokens)[:5]:
            print(f"  - {block.name:30} {block.tokens:6} tokens (priority {block.priority})")
        print("=" * 70 + "\n")
    
    def get_context_prompt(self) -> str:
        """Get all loaded context as a prompt block."""
        lines = []
        for block in sorted(self.blocks.values(), key=lambda b: -b.priority):
            lines.append(f"\n--- {block.name} ---\n{block.content}")
        return "\n".join(lines)


# Quick test
if __name__ == "__main__":
    mgr = ContextManager(max_tokens=5000, budget_dollars=15.0)
    
    # Add some blocks
    mgr.add_block("rules", "Project Rules", "No blind scans...", priority=10)
    mgr.add_block("arch", "Architecture", "Layer: dispatcher...", priority=8)
    mgr.add_block("code1", "Main Handler", "def dispatch():\n  ...", priority=5)
    mgr.add_block("code2", "Old Feature", "def old_feature():\n  ...", priority=1)
    
    mgr.print_context_status()
    
    # Simulate token overflow
    mgr.token_counter = 4500  # Manually set high
    removed = mgr.emergency_dehydrate(threshold_percent=80)
    print(f"Removed: {removed}")
    
    mgr.print_context_status()
