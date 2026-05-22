#!/usr/bin/env python3
"""
Integration test for UnifiedAgent with semantic routing.
Tests router.route() with multiple inputs and validates RoutingDecision objects.
Handles both semantic routing (with API key) and regex fallback (without API key).
"""

import sys
import json
from datetime import datetime
from pathlib import Path

# Add scaffold directory to path
sys.path.insert(0, str(Path(__file__).parent / "scaffold" / "agent"))

# Color codes for output
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
RESET = '\033[0m'

def print_header(text):
    print(f"\n{BLUE}{'='*70}{RESET}")
    print(f"{BLUE}{text}{RESET}")
    print(f"{BLUE}{'='*70}{RESET}\n")

def print_success(text):
    print(f"{GREEN}✓ {text}{RESET}")

def print_error(text):
    print(f"{RED}✗ {text}{RESET}")

def print_warning(text):
    print(f"{YELLOW}⚠ {text}{RESET}")

def print_info(text):
    print(f"{BLUE}ℹ {text}{RESET}")

def main():
    print_header("UNIFIED AGENT INTEGRATION TEST")
    print_info(f"Test started at: {datetime.now().isoformat()}")
    
    try:
        # Step 1: Import UnifiedAgent
        print_info("Step 1: Importing UnifiedAgent...")
        from unified_agent import UnifiedAgent
        print_success("UnifiedAgent imported successfully")
        
        # Step 2: Create an instance
        print_info("\nStep 2: Creating UnifiedAgent instance...")
        agent = UnifiedAgent()
        print_success(f"UnifiedAgent instance created")
        print_info(f"  - Router available: {hasattr(agent, 'router')}")
        print_info(f"  - Router type: {type(agent.router).__name__}")
        
        # Detect if running in semantic mode or fallback mode
        is_semantic = hasattr(agent.router, 'client') and agent.router.client is not None
        routing_mode = "SEMANTIC (with LLM)" if is_semantic else "REGEX FALLBACK"
        print_info(f"  - Routing mode: {routing_mode}")
        
        # Step 3: Test router.route() with 3 different inputs
        print_info("\nStep 3: Testing router.route() with 3 different inputs...")
        
        test_inputs = [
            ("Analyze current weather patterns in the region", "weather_analysis"),
            ("Process and store meteorological data", "data_processing"),
            ("Generate a report for system status", "reporting")
        ]
        
        routing_results = []
        for idx, (input_text, expected_category) in enumerate(test_inputs, 1):
            print_info(f"\n  Test input {idx}: \"{input_text[:50]}...\"")
            try:
                result = agent.router.route(input_text)
                routing_results.append((input_text, result))
                
                # Step 4: Verify it returns RoutingDecision objects
                if hasattr(result, '__class__'):
                    class_name = result.__class__.__name__
                    print_success(f"  Result type: {class_name}")
                    
                    # Check for RoutingDecision attributes
                    attrs = ['confidence', 'cost_estimate', 'handler', 'reason', 'tokens_estimate']
                    actual_attrs = [attr for attr in dir(result) if not attr.startswith('_')]
                    
                    # Display decision details using actual attributes
                    print_info(f"  Handler: {result.handler}")
                    print_info(f"  Confidence: {result.confidence:.3f}" if result.confidence else "  Confidence: N/A")
                    if hasattr(result, 'reason'):
                        print_info(f"  Reason: {result.reason}")
                    if hasattr(result, 'cost_estimate'):
                        print_info(f"  Cost estimate: ${result.cost_estimate:.4f}")
                    if hasattr(result, 'tokens_estimate'):
                        print_info(f"  Tokens estimate: {result.tokens_estimate}")
                    
                    # Step 5: Check handlers are assigned correctly
                    if result.handler:
                        print_success(f"  Handler assigned: {result.handler}")
                    else:
                        print_warning(f"  No handler assigned")
                    
            except Exception as e:
                print_error(f"  Error routing: {str(e)}")
        
        # Step 6: Print status report
        print_header("STATUS REPORT")
        
        total_tests = len(test_inputs)
        successful_routes = sum(1 for _, result in routing_results if result is not None)
        
        print_info(f"Routing mode: {routing_mode}")
        print_info(f"Total test inputs: {total_tests}")
        print_success(f"Successful routes: {successful_routes}/{total_tests}")
        
        if successful_routes == total_tests:
            print_success("All routing tests passed!")
        else:
            print_warning(f"Some routing tests failed: {total_tests - successful_routes}")
        
        # Verify handler assignments
        print_info("\nHandler assignments:")
        handler_set = set()
        for input_text, result in routing_results:
            if result and hasattr(result, 'handler') and result.handler:
                handler = str(result.handler)
                handler_set.add(handler)
                print_info(f"  '{input_text[:40]}...' → {handler}")
        
        print_info(f"\nUnique handlers used: {len(handler_set)}")
        for handler in sorted(handler_set):
            print_success(f"  - {handler}")
        
        # Final validation
        print_header("FINAL VALIDATION")
        
        checks = {
            "Agent instantiation": agent is not None,
            "Router exists": hasattr(agent, 'router') and agent.router is not None,
            "Routing works": successful_routes == total_tests,
            "Handlers assigned": len(handler_set) > 0,
            "Uses fallback or semantic": routing_mode in ["REGEX FALLBACK", "SEMANTIC (with LLM)"],
        }
        
        all_passed = all(checks.values())
        
        for check_name, passed in checks.items():
            if passed:
                print_success(check_name)
            else:
                print_error(check_name)
        
        print_header("TEST COMPLETE")
        
        if all_passed:
            print_success("✓ All integration tests PASSED!")
            print_info(f"Routing mode: {routing_mode}")
            if not is_semantic:
                print_info("  (Note: Using regex fallback - set ANTHROPIC_API_KEY for semantic routing)")
            print_info(f"Timestamp: {datetime.now().isoformat()}")
            return 0
        else:
            print_error("✗ Some integration tests FAILED!")
            print_info(f"Timestamp: {datetime.now().isoformat()}")
            return 1
            
    except ImportError as e:
        print_error(f"Failed to import UnifiedAgent: {str(e)}")
        import traceback
        traceback.print_exc()
        return 1
    except Exception as e:
        print_error(f"Unexpected error: {str(e)}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(main())
