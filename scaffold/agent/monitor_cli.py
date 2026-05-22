#!/usr/bin/env python3
"""
Inference Engine Monitor — CLI Dashboard

Command-line tool to view monitoring statistics without writing code.

Usage:
    python monitor_cli.py                    # Show dashboard
    python monitor_cli.py --db stats.db      # Use custom database
    python monitor_cli.py --export output.csv  # Export today's data
    python monitor_cli.py --today            # Show today's report
    python monitor_cli.py --month            # Show month's report
    python monitor_cli.py --models           # Compare models
    python monitor_cli.py --help             # Show help
"""

import sys
import argparse
from datetime import date, timedelta
from pathlib import Path

# Add scaffold to path so we can import the monitor
sys.path.insert(0, str(Path(__file__).parent.parent))

from scaffold.agent.inference_engine_monitor import InferenceEngineMonitor


def format_currency(amount):
    """Format amount as currency."""
    return f"${amount:.4f}"


def format_percentage(num, denom):
    """Format as percentage."""
    if denom == 0:
        return "0.0%"
    return f"{(num / denom * 100):.1f}%"


def format_number(n):
    """Format large numbers with commas."""
    return f"{n:,}"


def print_section(title):
    """Print a section header."""
    print(f"\n{'═' * 60}")
    print(f"  {title}")
    print(f"{'═' * 60}")


def show_dashboard(monitor):
    """Show the full dashboard."""
    monitor.print_dashboard()


def show_today(monitor):
    """Show today's report."""
    print_section("Today's Report")
    
    daily = monitor.daily_report()
    
    print(f"\nOverall Statistics:")
    print(f"  Requests:      {daily.total_requests}")
    print(f"  Tokens:        {format_number(daily.total_tokens)}")
    print(f"  Cost:          {format_currency(daily.total_cost)}")
    print(f"  Avg Response:  {daily.avg_response_time_ms:.0f}ms")
    print(f"  Error Rate:    {daily.error_rate:.1f}% ({daily.error_count} errors)")
    
    if daily.by_model:
        print(f"\nBy Model:")
        for model, stats in daily.by_model.items():
            print(f"  {model}:")
            print(f"    Requests: {stats['count']}")
            print(f"    Tokens:   {format_number(stats['tokens'])}")
            print(f"    Cost:     {format_currency(stats['cost'])}")
    
    if daily.by_task_type:
        print(f"\nBy Task Type:")
        for task_type, stats in daily.by_task_type.items():
            print(f"  {task_type}:")
            print(f"    Requests: {stats['count']}")
            print(f"    Cost:     {format_currency(stats['cost'])}")
            print(f"    Avg Cost: {format_currency(stats['cost'] / stats['count'] if stats['count'] > 0 else 0)}")


def show_month(monitor):
    """Show month's report."""
    print_section("Monthly Report")
    
    monthly = monitor.monthly_report()
    
    print(f"\nBudget Status:")
    print(f"  Budget:        {format_currency(monthly.budget)}")
    print(f"  Spent:         {format_currency(monthly.total_cost)}")
    print(f"  Remaining:     {format_currency(monthly.budget - monthly.total_cost)}")
    print(f"  Used:          {monthly.budget_used_percent:.1f}%")
    print(f"  Projected:     {format_currency(monthly.projected_cost)}")
    
    if monthly.projected_cost > monthly.budget:
        print(f"\n  ⚠️  WARNING: Projected to exceed budget!")
        print(f"     Overage: {format_currency(monthly.projected_cost - monthly.budget)}")
    
    print(f"\nActivity:")
    print(f"  Total Requests:    {monthly.total_requests}")
    print(f"  Daily Average:     {monthly.daily_avg_requests:.1f} requests/day")
    print(f"  Daily Cost Average: {format_currency(monthly.daily_cost_avg)}")
    print(f"  Days Remaining:    {monthly.days_remaining}")


def show_models(monitor):
    """Show model comparison."""
    print_section("Model Comparison")
    
    models = monitor.model_comparison()
    
    if not models:
        print("\nNo model data available yet.")
        return
    
    print(f"\n{'Model':<20} {'Requests':<10} {'Cost':<10} {'Tokens/$':<12} {'Errors':<10}")
    print("─" * 62)
    
    # Sort by cost efficiency (tokens per dollar)
    sorted_models = sorted(
        models.items(),
        key=lambda x: x[1]['tokens_per_dollar'] if x[1]['tokens_per_dollar'] > 0 else 0,
        reverse=True
    )
    
    for model, metrics in sorted_models:
        requests = metrics['total_requests']
        cost = metrics['total_cost']
        tokens_per_dollar = metrics['tokens_per_dollar']
        error_rate = metrics['error_rate']
        
        print(
            f"{model:<20} "
            f"{requests:<10} "
            f"{format_currency(cost):<10} "
            f"{tokens_per_dollar:>10.0f}  "
            f"{error_rate:>7.1f}%"
        )


def export_data(monitor, output_file):
    """Export data to CSV."""
    today = date.today()
    start = date(today.year, today.month, 1)
    end = today
    
    monitor.export_csv(start, end, output_file)
    print(f"\n✅ Exported to {output_file}")
    print(f"   Data: {start} to {end}")


def main():
    parser = argparse.ArgumentParser(
        description="Inference Engine Monitor — CLI Dashboard",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python monitor_cli.py                    # Show full dashboard
  python monitor_cli.py --today            # Show today's stats
  python monitor_cli.py --month            # Show month's stats
  python monitor_cli.py --models           # Compare models
  python monitor_cli.py --db custom.db     # Use custom database
  python monitor_cli.py --export data.csv  # Export data
        """
    )
    
    parser.add_argument(
        "--db",
        default="stats.db",
        help="Database file (default: stats.db)"
    )
    parser.add_argument(
        "--today",
        action="store_true",
        help="Show today's report"
    )
    parser.add_argument(
        "--month",
        action="store_true",
        help="Show month's report"
    )
    parser.add_argument(
        "--models",
        action="store_true",
        help="Compare models"
    )
    parser.add_argument(
        "--export",
        metavar="FILE",
        help="Export to CSV"
    )
    
    args = parser.parse_args()
    
    # Initialize monitor
    try:
        monitor = InferenceEngineMonitor(args.db, monthly_budget=15.0)
    except Exception as e:
        print(f"Error: Could not open database {args.db}")
        print(f"Details: {e}")
        sys.exit(1)
    
    # Handle commands
    if args.export:
        export_data(monitor, args.export)
    elif args.today:
        show_today(monitor)
    elif args.month:
        show_month(monitor)
    elif args.models:
        show_models(monitor)
    else:
        # Default: show full dashboard
        show_dashboard(monitor)


if __name__ == "__main__":
    main()
