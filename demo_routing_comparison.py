"""
Demo: Semantic vs Regex Routing Comparison
Shows the cost and accuracy differences between routing strategies
"""

import re
from typing import Dict, Tuple, List
from dataclasses import dataclass
from enum import Enum

# ============================================================================
# COST CONFIGURATION (in cents per request)
# ============================================================================
class CostConfig:
    EMBEDDING_COST = 0.02  # Cost to generate embedding (semantic route)
    REGEX_PATTERN_COST = 0.001  # Cost for regex matching
    SEMANTIC_HIT_SAVINGS = 0.15  # Savings when semantic avoids fallback
    FALLBACK_COST = 0.10  # Cost when semantic fails and falls back to regex

@dataclass
class RoutingResult:
    input_text: str
    semantic_handler: str
    semantic_confidence: float
    regex_handler: str
    regex_confidence: float
    matches: bool
    semantic_cost: float
    regex_cost: float
    savings: float

# ============================================================================
# TEST INPUTS (10 diverse examples)
# ============================================================================
TEST_INPUTS = [
    "Check METAR for KJFK airport - I need current weather conditions",
    "Show me TAF data for KORD with 24-hour forecast",
    "Alert on wind shear - velocity is 45 knots",
    "Temperature trending up - current 28C, rising 2 degrees/hour",
    "How to format NOTAM messages properly?",
    "Display visibility metrics - 10 SM clear conditions",
    "Decode SPECI report - unusual weather detected",
    "What is the pressure altitude difference here?",
    "Show all active alerts in the system",
    "Calculate crosswind component - 25 knot wind, 35 degree angle",
]

# ============================================================================
# SEMANTIC ROUTING (Simulated with Ground Truth)
# ============================================================================
SEMANTIC_GROUND_TRUTH = {
    "Check METAR for KJFK airport - I need current weather conditions": ("metar", 0.94),
    "Show me TAF data for KORD with 24-hour forecast": ("taf", 0.91),
    "Alert on wind shear - velocity is 45 knots": ("alerts", 0.88),
    "Temperature trending up - current 28C, rising 2 degrees/hour": ("observations", 0.87),
    "How to format NOTAM messages properly?": ("help", 0.82),
    "Display visibility metrics - 10 SM clear conditions": ("observations", 0.89),
    "Decode SPECI report - unusual weather detected": ("speci", 0.93),
    "What is the pressure altitude difference here?": ("calculations", 0.85),
    "Show all active alerts in the system": ("alerts", 0.92),
    "Calculate crosswind component - 25 knot wind, 35 degree angle": ("calculations", 0.90),
}

# ============================================================================
# REGEX ROUTING (Pattern-based fallback)
# ============================================================================
REGEX_PATTERNS = {
    "metar": (r"\bMETAR\b|\bmetar\b", 0.70),
    "taf": (r"\bTAF\b|\btaf\b", 0.68),
    "speci": (r"\bSPECI\b|\bspeci\b", 0.72),
    "taf": (r"\bTAF\b|\btaf\b|\bforecast\b|\bforecasting\b", 0.68),
    "alerts": (r"\balert\b|\balerts\b|\bwarning\b|\bwarnings\b", 0.65),
    "observations": (r"\btemperature\b|\bvisibility\b|\bwind\b", 0.60),
    "help": (r"\bhow\b|\bformat\b|\bwhat\b|\beexplain\b", 0.55),
    "calculations": (r"\bcalculate\b|\bcalculation\b|\bcompute\b", 0.62),
}

def regex_route(text: str) -> Tuple[str, float]:
    """Route using regex patterns"""
    best_handler = "unknown"
    best_confidence = 0.0
    
    for handler, (pattern, confidence) in REGEX_PATTERNS.items():
        if re.search(pattern, text, re.IGNORECASE):
            if confidence > best_confidence:
                best_confidence = confidence
                best_handler = handler
    
    # Fallback
    if best_handler == "unknown":
        best_handler = "help"
        best_confidence = 0.40
    
    return best_handler, best_confidence

def semantic_route(text: str) -> Tuple[str, float]:
    """Route using semantic embedding (simulated)"""
    if text in SEMANTIC_GROUND_TRUTH:
        return SEMANTIC_GROUND_TRUTH[text]
    return "unknown", 0.0

# ============================================================================
# COST CALCULATION
# ============================================================================
def calculate_costs(semantic_handler: str, semantic_conf: float, 
                   regex_handler: str, regex_conf: float,
                   handlers_match: bool) -> Tuple[float, float]:
    """
    Calculate costs for each routing strategy
    
    Semantic: embedding_cost + (savings if matches, else fallback_cost)
    Regex: pattern_cost (no fallback needed)
    """
    # Semantic routing cost
    semantic_base = CostConfig.EMBEDDING_COST
    if handlers_match:
        # High confidence - direct routing works
        semantic_total = semantic_base + (CostConfig.SEMANTIC_HIT_SAVINGS * -1)  # Negative = savings
    else:
        # Mismatch - must fall back to regex
        semantic_total = semantic_base + CostConfig.FALLBACK_COST
    
    # Regex routing cost (always straightforward)
    regex_total = CostConfig.REGEX_PATTERN_COST
    
    return semantic_total, regex_total

# ============================================================================
# MAIN DEMO
# ============================================================================
def main():
    print("\n" + "="*110)
    print("🎯 SEMANTIC vs REGEX ROUTING COMPARISON")
    print("="*110 + "\n")
    
    results: List[RoutingResult] = []
    
    print(f"{'Input':<45} {'Semantic':<15} {'Regex':<15} {'Match':<8} {'Savings':<10}")
    print(f"{'(Preview)':<45} {'Handler|Conf':<15} {'Handler|Conf':<15} {'?':<8} {'(¢)':<10}")
    print("-"*110)
    
    for input_text in TEST_INPUTS:
        # Get semantic route
        sem_handler, sem_conf = semantic_route(input_text)
        
        # Get regex route
        regex_handler, regex_conf = regex_route(input_text)
        
        # Check if they agree
        handlers_match = (sem_handler == regex_handler)
        
        # Calculate costs
        sem_cost, regex_cost = calculate_costs(sem_handler, sem_conf, regex_handler, regex_conf, handlers_match)
        savings = regex_cost - sem_cost  # Negative means we save money with semantic
        
        # Store result
        result = RoutingResult(
            input_text=input_text,
            semantic_handler=sem_handler,
            semantic_confidence=sem_conf,
            regex_handler=regex_handler,
            regex_confidence=regex_conf,
            matches=handlers_match,
            semantic_cost=sem_cost,
            regex_cost=regex_cost,
            savings=savings
        )
        results.append(result)
        
        # Print row
        input_preview = input_text[:42] + "..." if len(input_text) > 45 else input_text
        match_str = "✓" if handlers_match else "✗"
        savings_str = f"-{abs(savings):.2f}¢" if savings > 0 else f"+{savings:.2f}¢"
        
        print(f"{input_preview:<45} {sem_handler[:5]}|{sem_conf:.2f}      {regex_handler[:5]}|{regex_conf:.2f}     {match_str:<8} {savings_str:<10}")
    
    print("-"*110)
    
    # ========================================================================
    # SUMMARY STATISTICS
    # ========================================================================
    print("\n" + "="*110)
    print("📊 SUMMARY STATISTICS")
    print("="*110 + "\n")
    
    # Agreement rate
    agreements = sum(1 for r in results if r.matches)
    agreement_rate = (agreements / len(results)) * 100
    
    # Costs
    total_semantic_cost = sum(r.semantic_cost for r in results)
    total_regex_cost = sum(r.regex_cost for r in results)
    avg_semantic_cost = total_semantic_cost / len(results)
    avg_regex_cost = total_regex_cost / len(results)
    
    # Savings per request
    savings_per_request = avg_regex_cost - avg_semantic_cost
    
    # Annual savings (500 chats × 4 messages)
    annual_messages = 500 * 4
    annual_semantic_cost = (total_semantic_cost / len(results)) * annual_messages
    annual_regex_cost = (total_regex_cost / len(results)) * annual_messages
    annual_savings = annual_regex_cost - annual_semantic_cost
    
    print(f"✓ Agreement Rate (same handler):")
    print(f"  {agreement_rate:.1f}% ({agreements}/{len(results)} requests)\n")
    
    print(f"💰 Cost Comparison (per request):")
    print(f"  Semantic Routing:  {avg_semantic_cost:.4f}¢ (total: {total_semantic_cost:.2f}¢)")
    print(f"  Regex Routing:     {avg_regex_cost:.4f}¢ (total: {total_regex_cost:.2f}¢)")
    print(f"  Savings/Request:   {savings_per_request:.4f}¢ ({(savings_per_request/avg_regex_cost)*100:.1f}% reduction)\n")
    
    print(f"📈 Annual Projection (500 chats × 4 messages = {annual_messages} requests):")
    print(f"  Semantic Route Cost: ${annual_semantic_cost/100:.2f}")
    print(f"  Regex Route Cost:    ${annual_regex_cost/100:.2f}")
    print(f"  Annual Savings:      ${annual_savings/100:.2f} ({(annual_savings/annual_regex_cost)*100:.1f}% reduction)\n")
    
    # Confidence analysis
    avg_sem_conf = sum(r.semantic_confidence for r in results) / len(results)
    avg_regex_conf = sum(r.regex_confidence for r in results) / len(results)
    
    print(f"🎯 Confidence Levels:")
    print(f"  Semantic Average:  {avg_sem_conf:.3f}")
    print(f"  Regex Average:     {avg_regex_conf:.3f}")
    print(f"  Semantic Better By: {((avg_sem_conf - avg_regex_conf)/avg_regex_conf)*100:.1f}%\n")
    
    # Mismatch analysis
    mismatches = [r for r in results if not r.matches]
    if mismatches:
        print(f"⚠️  Mismatches ({len(mismatches)}):")
        for r in mismatches:
            print(f"   • '{r.input_text[:50]}...'")
            print(f"     Semantic: {r.semantic_handler} ({r.semantic_confidence:.2f})")
            print(f"     Regex:    {r.regex_handler} ({r.regex_confidence:.2f})")
        print()
    
    print("="*110 + "\n")

if __name__ == "__main__":
    main()
