"""Compact, review-oriented projections of investment holdings.

These tools exist because the raw ``get_account_holdings`` payload carries the
full GraphQL security object, a duplicate ``holdings[]`` sub-object and
``__typename`` on every node -- far over the tool-result size limit for a
40-holding account. Both tools here run the same upstream query and keep only
the handful of fields a portfolio review needs.
"""

import asyncio
import logging
import re
from datetime import date
from typing import Any, Dict, List, Optional, Sequence

from pydantic import BaseModel

from monarch_mcp_server.app import mcp
from monarch_mcp_server.client import get_monarch_client
from monarch_mcp_server.helpers import first_present, json_error, json_success
from monarch_mcp_server.tools.accounts import _signed_balance

logger = logging.getLogger(__name__)

_CASH_LIKE_TYPES = frozenset({"cash", "other"})
_CASH_LIKE_NAME = re.compile(r"sweep|liquidity|money market|fed fund", re.IGNORECASE)


class Holding(BaseModel):
    """One aggregate holding, projected to the fields a review needs."""

    account_id: str
    ticker: Optional[str] = None
    name: Optional[str] = None
    type: Optional[str] = None
    quantity: Optional[float] = None
    price: Optional[float] = None
    value: float
    basis: float
    gain_loss: float
    gain_loss_pct: Optional[float] = None
    price_as_of: Optional[str] = None


class AccountHoldingsSummary(BaseModel):
    """Per-account roll-up inside ``get_all_holdings``."""

    account_id: str
    account_name: Optional[str] = None
    balance: Optional[float] = None
    holdings_value: float
    holdings_count: int
    has_holdings: bool
    error: Optional[str] = None


class HoldingWithAccount(Holding):
    """A ``Holding`` tagged with the account it belongs to."""

    account_name: Optional[str] = None


class HoldingsTotals(BaseModel):
    investment_accounts_balance: float
    holdings_value: float
    cash_like_value: float


class AllHoldings(BaseModel):
    """Whole-portfolio snapshot returned by ``get_all_holdings``."""

    as_of: str
    accounts: List[AccountHoldingsSummary]
    holdings: List[HoldingWithAccount]
    totals: HoldingsTotals


def _round2(value: Any) -> float:
    return round(float(value or 0), 2)


def _date_part(timestamp: Any) -> Optional[str]:
    if not isinstance(timestamp, str) or len(timestamp) < 10:
        return None
    return timestamp[:10]


def _project_holding(node: Dict[str, Any], account_id: str) -> Holding:
    """Apply the field rules from the compact-holdings spec to one node.

    ``holdings[0]`` (the account-level lot) is preferred over ``security`` for
    ticker, name, type and price: several ETFs have ``security.ticker = null``
    while the lot carries it, and ``security.closingPrice`` is stale (2023
    dates) on many rows.
    """
    lots = node.get("holdings")
    lot: Dict[str, Any] = lots[0] if isinstance(lots, list) and lots else {}
    if not isinstance(lot, dict):
        lot = {}
    security = node.get("security")
    if not isinstance(security, dict):
        security = {}

    value = _round2(node.get("totalValue"))
    basis = _round2(node.get("basis"))
    gain_loss = round(value - basis, 2)
    gain_loss_pct = round(gain_loss / basis * 100, 2) if basis else None

    quantity = node.get("quantity")
    price = lot.get("closingPrice")

    return Holding(
        account_id=account_id,
        ticker=first_present(lot.get("ticker"), security.get("ticker")),
        name=first_present(lot.get("name"), security.get("name")),
        type=first_present(lot.get("type"), security.get("type")),
        quantity=float(quantity) if quantity is not None else None,
        price=float(price) if price is not None else None,
        value=value,
        basis=basis,
        gain_loss=gain_loss,
        gain_loss_pct=gain_loss_pct,
        price_as_of=_date_part(lot.get("closingPriceUpdatedAt")),
    )


def _project_holdings(raw: Dict[str, Any], account_id: str) -> List[Holding]:
    """Project a raw ``Web_GetHoldings`` response, sorted by value descending."""
    edges = ((raw or {}).get("portfolio") or {}).get("aggregateHoldings") or {}
    rows = [
        _project_holding(edge["node"], account_id)
        for edge in edges.get("edges") or []
        if isinstance(edge, dict) and isinstance(edge.get("node"), dict)
    ]
    rows.sort(key=lambda h: h.value, reverse=True)
    return rows


def _is_cash_like(holding: Holding) -> bool:
    """The review's "idle sweep cash": cash/other rows, or par-priced sweeps."""
    if holding.type in _CASH_LIKE_TYPES:
        return True
    return (
        holding.price == 1.0
        and holding.name is not None
        and _CASH_LIKE_NAME.search(holding.name) is not None
    )


def _is_active(account: Dict[str, Any]) -> bool:
    if "isActive" in account:
        return bool(account.get("isActive"))
    return not account.get("deactivatedAt")


@mcp.tool()
async def get_holdings_summary(account_id: str) -> str:
    """Compact per-account investment holdings (one row per aggregate holding).

    Same upstream query as ``get_account_holdings`` but projected to ~10
    fields, sorted by value descending. About 200 bytes per holding.

    Returns a JSON array. Example row::

        {"account_id": "2512...", "ticker": "FXAIX", "name": "Fidelity 500
        Index Fund", "type": "mutual_fund", "quantity": 1422.904, "price":
        266.43, "value": 379104.31, "basis": 382045.04, "gain_loss": -2940.73,
        "gain_loss_pct": -0.77, "price_as_of": "2026-09-18"}

    Field notes: ``ticker`` may be null for cash/sweep rows (``name`` is always
    set); ``gain_loss_pct`` is null when ``basis`` is 0; ``price`` is the
    account-level closing price, not the stale security price; ``type`` is one
    of mutual_fund, etf, equity, cash, other. Money is rounded to 2 dp.

    Returns ``[]`` for a known account with no synced holdings and an error for
    an unknown account id.

    Args:
        account_id: Monarch account ID (from ``get_accounts``).
    """
    try:
        client = await get_monarch_client()
        raw = await client.get_account_holdings(account_id)
        rows = _project_holdings(raw, account_id)

        if not rows:
            # Empty edges is also what Monarch returns for an id it does not
            # know, so confirm the account exists before reporting "none".
            accounts = await client.get_accounts()
            known = {a.get("id") for a in accounts.get("accounts", [])}
            if account_id not in known:
                raise ValueError(
                    f"Unknown account id {account_id!r}; use get_accounts to "
                    "look up valid ids"
                )

        return json_success([h.model_dump() for h in rows], compact=True)
    except Exception as e:
        return json_error("get_holdings_summary", e)


async def _fetch_account_holdings(
    client: Any, account: Dict[str, Any]
) -> tuple[AccountHoldingsSummary, List[HoldingWithAccount]]:
    """Fetch and project one account; errors become a row, not an exception."""
    account_id = str(account.get("id"))
    account_name = account.get("displayName") or account.get("name")
    balance = _signed_balance(account)
    try:
        raw = await client.get_account_holdings(account_id)
        rows = _project_holdings(raw, account_id)
    except Exception as exc:  # one bad account must not sink the whole call
        logger.warning("get_all_holdings: %s failed: %s", account_id, exc)
        summary = AccountHoldingsSummary(
            account_id=account_id,
            account_name=account_name,
            balance=balance,
            holdings_value=0.0,
            holdings_count=0,
            has_holdings=False,
            error=str(exc) or type(exc).__name__,
        )
        return summary, []

    tagged = [
        HoldingWithAccount(**h.model_dump(), account_name=account_name) for h in rows
    ]
    summary = AccountHoldingsSummary(
        account_id=account_id,
        account_name=account_name,
        balance=balance,
        holdings_value=round(sum(h.value for h in rows), 2),
        holdings_count=len(rows),
        has_holdings=bool(rows),
    )
    return summary, tagged


@mcp.tool()
async def get_all_holdings(include_inactive: bool = False) -> str:
    """Whole-portfolio holdings snapshot in one call.

    Lists accounts, keeps those of type ``brokerage`` (active and with a
    non-zero balance unless ``include_inactive``), fetches every account's
    holdings concurrently and returns the ``get_holdings_summary`` rows for all
    of them plus per-account and portfolio totals.

    Returns a JSON object::

        {"as_of": "2026-09-19",
         "accounts": [{"account_id": "...", "account_name": "Chase Mgd IRA",
                       "balance": 911910.30, "holdings_value": 911912.02,
                       "holdings_count": 22, "has_holdings": true,
                       "error": null}, ...],
         "holdings": [<get_holdings_summary rows, each with account_id and
                       account_name>],
         "totals": {"investment_accounts_balance": 1440626.34,
                    "holdings_value": 1171612.51,
                    "cash_like_value": 112248.10}}

    ``has_holdings`` is false both for accounts whose provider syncs no
    holdings and for accounts whose fetch failed; the latter also carry an
    ``error`` string. ``cash_like_value`` sums holdings of type cash/other, or
    priced at 1.0 with a sweep / liquidity / money market / fed fund name.

    Args:
        include_inactive: Also include deactivated and zero-balance brokerage
            accounts. Default False.
    """
    try:
        client = await get_monarch_client()
        accounts_raw = await client.get_accounts()

        selected: List[Dict[str, Any]] = []
        for account in accounts_raw.get("accounts", []):
            if (account.get("type") or {}).get("name") != "brokerage":
                continue
            if not include_inactive:
                if not _is_active(account):
                    continue
                if not _signed_balance(account):
                    continue
            selected.append(account)

        results: Sequence[tuple[AccountHoldingsSummary, List[HoldingWithAccount]]] = (
            await asyncio.gather(
                *(_fetch_account_holdings(client, a) for a in selected)
            )
        )

        summaries = [summary for summary, _ in results]
        holdings = [h for _, rows in results for h in rows]

        totals = HoldingsTotals(
            investment_accounts_balance=round(
                sum(s.balance or 0 for s in summaries), 2
            ),
            holdings_value=round(sum(h.value for h in holdings), 2),
            cash_like_value=round(
                sum(h.value for h in holdings if _is_cash_like(h)), 2
            ),
        )

        result = AllHoldings(
            as_of=date.today().isoformat(),
            accounts=summaries,
            holdings=holdings,
            totals=totals,
        )
        return json_success(result.model_dump(), compact=True)
    except Exception as e:
        return json_error("get_all_holdings", e)
