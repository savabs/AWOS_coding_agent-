#!/usr/bin/env python3
"""Helper for demo_multi_resolution_context.sh — import validation."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scaffold.agent.semantic_retriever import SemanticRetriever
from scaffold.agent.multi_resolution_context import (
    MultiResolutionContextPipeline,
)

# Smoke tests
sr = SemanticRetriever()
assert sr.retrieve("") == [], "empty retrieve failed"

pipeline = MultiResolutionContextPipeline()
a = pipeline.build({}, level=0)
assert a.level == 0

print("  [PASS] semantic_retriever imports OK")
print("  [PASS] multi_resolution_context imports OK")
print("  [PASS] SemanticRetriever() create OK")
print("  [PASS] MultiResolutionContextPipeline() create OK")
