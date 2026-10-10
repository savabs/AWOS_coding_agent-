"""Smoke test: verify cache telemetry captures stats from a real API call."""
import os
import sys
from pathlib import Path

# Add agent dir to path
agent_path = Path(__file__).parent.parent / "scaffold" / "agent"
sys.path.insert(0, str(agent_path))

from cache_telemetry import CacheTelemetryStore, extract_cache_stats_anthropic
from anthropic import Anthropic

def main():
    """Simple test: make one Anthropic call, check cache stats recorded."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("ANTHROPIC_API_KEY not set, skipping test")
        return
    
    client = Anthropic(api_key=api_key)
    
    # Clean slate
    store = CacheTelemetryStore()
    if store.file.exists():
        store.file.unlink()
    
    # Make a simple API call with prompt caching
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=50,
        system=[
            {
                "type": "text",
                "text": "You are a helpful assistant.",
                "cache_control": {"type": "ephemeral"}
            }
        ],
        messages=[
            {"role": "user", "content": "Say hello in one word."}
        ]
    )
    
    print(f"Response: {response.content[0].text}")
    print(f"\nUsage: {response.usage}")
    
    # Extract and record cache stats
    event = extract_cache_stats_anthropic(
        response=response.model_dump(),
        component="test",
        model="claude-sonnet-4-6",
    )
    
    store.record(event)
    
    print(f"\nCache event recorded:")
    print(f"  Input tokens: {event.input_tokens}")
    print(f"  Cache read: {event.cache_read_tokens}")
    print(f"  Cache create: {event.cache_creation_tokens}")
    print(f"  Hit rate: {event.cache_hit_rate:.1%}")
    print(f"  Cost saved: ${event.cost_saved_usd:.4f}")
    
    # Verify file created
    assert store.file.exists(), f"Cache stats file not created at {store.file}"
    
    # Read back
    stats = store.stats()
    print(f"\nAggregated stats:")
    print(f"  Event count: {stats['event_count']}")
    print(f"  Overall hit rate: {stats['hit_rate']:.1%}")
    
    print("\n✅ Smoke test passed!")

if __name__ == "__main__":
    main()
