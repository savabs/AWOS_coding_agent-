"""Rendering of monthly summaries."""

import json

from .money import format_money


def render_text(summary, show_daily=False):
    cur = summary.currency
    money = lambda v: format_money(v, cur)  # noqa: E731
    lines = [
        f"Monthly summary {summary.account_id} {summary.year:04d}-{summary.month:02d} ({cur})",
        "=" * 60,
        f"Opening balance   {money(summary.opening_balance):>20}",
        f"Income            {money(summary.total_income):>20}",
        f"Spending          {money(summary.total_spending):>20}",
        f"Net movement      {money(summary.net_movement):>20}",
        f"Closing balance   {money(summary.closing_balance):>20}",
        f"FX fees           {money(summary.fx_fees):>20}",
        f"Transactions      {summary.txn_count:>20}",
        f"Average spend     {money(summary.average_spend):>20}",
    ]
    if summary.largest_expense is not None:
        txn_id, amount = summary.largest_expense
        lines.append(f"Largest expense   {money(amount):>20}  ({txn_id})")
    lines.append("")
    lines.append("Categories (month / year to date)")
    names = sorted(set(summary.categories) | set(summary.ytd_categories))
    for name in names:
        month_value = summary.categories.get(name)
        month_text = money(month_value) if month_value is not None else "-"
        lines.append(f"  {name:<28}{month_text:>14}{money(summary.ytd_categories.get(name, 0)):>16}")
    lines.append("")
    lines.append("Top merchants")
    for rank, (name, spend) in enumerate(summary.top_merchants, start=1):
        lines.append(f"  {rank}. {name:<30}{money(spend):>14}")
    if show_daily:
        lines.append("")
        lines.append("Daily balances")
        for day, balance in summary.daily_balances:
            lines.append(f"  {day.isoformat()}  {money(balance):>16}")
    return "\n".join(lines) + "\n"


def render_json(summary):
    return json.dumps(summary.to_dict(), indent=2, sort_keys=True) + "\n"
