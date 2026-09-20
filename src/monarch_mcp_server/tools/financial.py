"""Financial analytics tools (cashflow, net worth)."""

import logging
from datetime import date
from datetime import datetime as dt
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from monarch_mcp_server.app import mcp
from monarch_mcp_server.client import get_monarch_client
from monarch_mcp_server.helpers import json_success, json_error

logger = logging.getLogger(__name__)

# Categories that net to zero across accounts and are noise in a review, even
# when a user has moved them out of Monarch's transfer group (Sell, for one,
# ships under income).
_TRANSFER_CATEGORY_NAMES = frozenset(
    {"transfer", "credit card payment", "buy", "sell"}
)


class CategoryAmount(BaseModel):
    category: str
    amount: float


class CategoryGroupAmount(BaseModel):
    category: str
    group: Optional[str] = None
    amount: float


class GroupAmount(BaseModel):
    group: str
    amount: float


class CashflowSummary(BaseModel):
    """Aggregates-only cashflow for a date range (``get_cashflow_summary``)."""

    start_date: str
    end_date: str
    income: float
    expenses: float
    savings: float
    savings_rate_pct: Optional[float] = None
    income_by_category: List[CategoryAmount]
    expenses_by_category: List[CategoryGroupAmount]
    expenses_by_group: List[GroupAmount]


def _parse_iso_date(value: Optional[str], label: str) -> str:
    """Return *value* if it is a real YYYY-MM-DD date, else raise ValueError.

    The length check matters: ``date.fromisoformat`` also accepts ``20260901``
    and, on 3.11+, ISO week and ordinal forms, which Monarch would reject.
    """
    if isinstance(value, str) and len(value) == 10:
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError:
            pass
    raise ValueError(f"{label} must be a date in YYYY-MM-DD format, got {value!r}")


def _is_transfer(name: Optional[str], group_type: Optional[str]) -> bool:
    if group_type == "transfer":
        return True
    return (name or "").strip().lower() in _TRANSFER_CATEGORY_NAMES


@mcp.tool()
async def get_cashflow(
    start_date: Optional[str] = None, end_date: Optional[str] = None
) -> str:
    """
    Get cashflow analysis from Monarch Money.

    Args:
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format
    """
    try:
        client = await get_monarch_client()

        filters: Dict[str, Any] = {}
        if start_date:
            filters["start_date"] = start_date
        if end_date:
            filters["end_date"] = end_date

        cashflow = await client.get_cashflow(**filters)
        return json_success(cashflow)
    except Exception as e:
        return json_error("get_cashflow", e)


@mcp.tool()
async def get_cashflow_summary(
    start_date: str,
    end_date: Optional[str] = None,
    top_n: int = 15,
) -> str:
    """Compact cashflow for a date range: totals plus per-category/group lists.

    Same upstream query as ``get_cashflow`` but returns only the summary block
    and category / category-group totals -- no merchant list, no
    ``__typename``. Under 5 KB for any range. Transfer-type categories
    (Transfer, Credit Card Payment, Buy, Sell and anything in Monarch's
    transfer group) are excluded because they net to zero.

    Returns a JSON object::

        {"start_date": "2026-09-01", "end_date": "2026-09-19",
         "income": 15833.53, "expenses": 11368.56, "savings": 4464.97,
         "savings_rate_pct": 28.2,
         "income_by_category": [{"category": "Paychecks", "amount": 9180.29}],
         "expenses_by_category": [{"category": "Mortgage", "group": "Housing",
                                   "amount": 3160.65}],
         "expenses_by_group": [{"group": "Housing", "amount": 4151.49}]}

    All amounts are positive, rounded to 2 dp and sorted descending; each list
    is capped at ``top_n``. ``savings_rate_pct`` is a percentage (28.2, not
    0.282) and null when Monarch reports none.

    Args:
        start_date: Start date, YYYY-MM-DD (required).
        end_date: End date, YYYY-MM-DD. Defaults to today.
        top_n: Maximum rows per list. Default 15.
    """
    try:
        start = _parse_iso_date(start_date, "start_date")
        end = (
            _parse_iso_date(end_date, "end_date")
            if end_date is not None
            else date.today().isoformat()
        )

        client = await get_monarch_client()
        page = await client.get_cashflow(start_date=start, end_date=end)

        summary_rows = page.get("summary") or []
        summary = (summary_rows[0].get("summary") if summary_rows else None) or {}
        if not summary:
            raise ValueError("Monarch returned no cashflow summary for this range")

        group_names: Dict[str, str] = {}
        expenses_by_group: List[GroupAmount] = []
        for row in page.get("byCategoryGroup") or []:
            group = (row.get("groupBy") or {}).get("categoryGroup") or {}
            gid, gname, gtype = group.get("id"), group.get("name"), group.get("type")
            if not gname:
                continue
            if gid is not None:
                group_names[str(gid)] = gname
            amount = round(float((row.get("summary") or {}).get("sum") or 0), 2)
            if gtype == "expense" and not _is_transfer(gname, gtype) and amount < 0:
                expenses_by_group.append(GroupAmount(group=gname, amount=-amount))

        income_by_category: List[CategoryAmount] = []
        expenses_by_category: List[CategoryGroupAmount] = []
        for row in page.get("byCategory") or []:
            category = (row.get("groupBy") or {}).get("category") or {}
            cname = category.get("name")
            group = category.get("group") or {}
            gtype = group.get("type")
            if not cname or _is_transfer(cname, gtype):
                continue
            amount = round(float((row.get("summary") or {}).get("sum") or 0), 2)
            if amount == 0:
                continue
            if gtype == "income" and amount > 0:
                income_by_category.append(CategoryAmount(category=cname, amount=amount))
            elif gtype == "expense" and amount < 0:
                expenses_by_category.append(
                    CategoryGroupAmount(
                        category=cname,
                        group=group_names.get(str(group.get("id"))),
                        amount=-amount,
                    )
                )

        def _top(rows: List[Any]) -> List[Any]:
            return sorted(rows, key=lambda r: r.amount, reverse=True)[: max(top_n, 0)]

        rate = summary.get("savingsRate")
        result = CashflowSummary(
            start_date=start,
            end_date=end,
            income=round(float(summary.get("sumIncome") or 0), 2),
            expenses=round(abs(float(summary.get("sumExpense") or 0)), 2),
            savings=round(float(summary.get("savings") or 0), 2),
            savings_rate_pct=round(float(rate) * 100, 1) if rate is not None else None,
            income_by_category=_top(income_by_category),
            expenses_by_category=_top(expenses_by_category),
            expenses_by_group=_top(expenses_by_group),
        )
        return json_success(result.model_dump(), compact=True)
    except Exception as e:
        return json_error("get_cashflow_summary", e)


@mcp.tool()
async def get_net_worth(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    account_type: Optional[str] = None,
) -> str:
    """
    Get net worth history over time.

    Returns daily snapshots of total net worth, useful for tracking wealth trends.

    Args:
        start_date: Start date in YYYY-MM-DD format (defaults to account history start)
        end_date: End date in YYYY-MM-DD format (defaults to today)
        account_type: Filter by account type (e.g., "brokerage", "depository", "credit")

    Returns:
        Daily net worth snapshots with dates and values.

    Examples:
        Get net worth for the past year:
            get_net_worth(start_date="2024-01-01")

        Get only investment account net worth:
            get_net_worth(account_type="brokerage")
    """
    try:
        client = await get_monarch_client()

        params: Dict[str, Any] = {}
        # Pass ISO strings directly; upstream serializes via gql JSON and
        # cannot handle datetime.date objects in GraphQL variables.
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        if account_type:
            params["account_type"] = account_type

        result = await client.get_aggregate_snapshots(**params)

        snapshots = result.get("aggregateSnapshots", [])

        formatted: Dict[str, Any] = {
            "snapshot_count": len(snapshots),
            "snapshots": []
        }

        if snapshots:
            values = [s.get("balance", 0) for s in snapshots if s.get("balance") is not None]
            if values:
                formatted["current_net_worth"] = values[-1] if values else 0
                formatted["earliest_net_worth"] = values[0] if values else 0
                formatted["change"] = values[-1] - values[0] if len(values) > 1 else 0
                formatted["change_percent"] = (
                    ((values[-1] - values[0]) / values[0] * 100)
                    if values[0] != 0 and len(values) > 1 else 0
                )
                formatted["highest"] = max(values)
                formatted["lowest"] = min(values)

        for snapshot in snapshots[-365:]:
            formatted["snapshots"].append({
                "date": snapshot.get("date"),
                "net_worth": snapshot.get("balance"),
            })

        return json_success(formatted)
    except Exception as e:
        return json_error("get_net_worth", e)


@mcp.tool()
async def get_net_worth_by_account_type(
    start_date: str,
    timeframe: str = "month",
) -> str:
    """
    Get net worth breakdown by account type over time.

    Shows how net worth is distributed across different account types
    (checking, savings, investments, credit cards, etc.) with monthly or yearly granularity.

    Args:
        start_date: Start date in YYYY-MM-DD format
        timeframe: Granularity - "month" or "year" (default: "month")

    Returns:
        Net worth snapshots grouped by account type.

    Examples:
        Get monthly breakdown for the past year:
            get_net_worth_by_account_type(start_date="2024-01-01", timeframe="month")

        Get yearly breakdown:
            get_net_worth_by_account_type(start_date="2020-01-01", timeframe="year")
    """
    try:
        if timeframe not in ("month", "year"):
            return json_error(
                "get_net_worth_by_account_type",
                ValueError("timeframe must be 'month' or 'year'"),
            )

        client = await get_monarch_client()
        result = await client.get_account_snapshots_by_type(
            start_date=start_date,
            timeframe=timeframe,
        )

        # Upstream returns a flat list under key "snapshotsByAccountType"
        # with shape [{"accountType": str, "month": "YYYY-MM" or "YYYY", "balance": float}, ...]
        rows = result.get("snapshotsByAccountType", [])

        formatted: Dict[str, Any] = {
            "timeframe": timeframe,
            "start_date": start_date,
            "account_types": []
        }

        # Group flat rows by accountType, preserving order of first appearance.
        grouped: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            atype = row.get("accountType")
            if atype is None:
                continue
            entry = grouped.setdefault(atype, {"type": atype, "snapshots": []})
            entry["snapshots"].append({
                "month": row.get("month"),
                "balance": row.get("balance"),
            })

        for type_info in grouped.values():
            if type_info["snapshots"]:
                type_info["current_balance"] = type_info["snapshots"][-1].get("balance", 0)
            formatted["account_types"].append(type_info)

        total = sum(
            t.get("current_balance", 0)
            for t in formatted["account_types"]
            if t.get("current_balance") is not None
        )
        formatted["total_net_worth"] = total

        return json_success(formatted)
    except Exception as e:
        return json_error("get_net_worth_by_account_type", e)
