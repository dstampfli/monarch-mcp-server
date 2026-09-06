"""Account management tools."""

import json
import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from pydantic import RootModel, ValidationError

from monarch_mcp_server.app import mcp
from monarch_mcp_server.client import get_monarch_client
from monarch_mcp_server.helpers import json_success, json_error

logger = logging.getLogger(__name__)


class BalanceCorrections(RootModel[Dict[date, Decimal]]):
    """Validates the corrections payload for upload_account_balance_history.

    Keys must be ISO dates (YYYY-MM-DD) and values must parse as decimals.
    Pydantic raises on bad input rather than letting typos silently no-op.
    """


def _signed_balance(account: Dict[str, Any]) -> Optional[float]:
    """Recover the balance that actually contributes to net worth.

    ``currentBalance`` is Monarch's signed balance for most accounts, but it is
    not reliable: some liabilities (observed on MX-sourced cards that Monarch
    failed to classify as ``credit_card`` and typed ``other``) come back
    positive while the stored balance history -- the thing net worth is built
    from -- holds the correct negative.

    ``displayBalance`` does not have that problem. For a liability it is
    consistently the negation of the signed balance (the amount owed, positive),
    so negating it back recovers the signed value even for the broken accounts.
    Verified against ``recentBalances`` across a live account: 0/38 disagreed,
    and the sum reproduced Monarch's own net-worth snapshot exactly, where
    summing ``currentBalance`` did not.

    Falls back to ``currentBalance`` when the account is an asset, when
    ``isAsset`` is missing (nothing to key the negation off), or when
    ``displayBalance`` is absent -- never guessing a sign it cannot derive.
    """
    is_asset = account.get("isAsset")
    display: Optional[float] = account.get("displayBalance")
    if is_asset is False and display is not None:
        return -display
    current: Optional[float] = account.get("currentBalance")
    return current


@mcp.tool()
async def get_accounts() -> str:
    """Get all financial accounts from Monarch Money.

    Balance sign conventions (they differ, so pick deliberately):

    - ``signed_balance`` is the value that contributes to net worth: positive
      adds, negative subtracts. **Use this for any summing or net-worth math.**
      It is computed, not raw -- see ``_signed_balance`` for why the raw field
      cannot be trusted for that purpose.
    - ``display_balance`` is the amount as Monarch shows it in the UI. For a
      liability it is the amount owed as a positive number ($428,133.39 of
      mortgage, not -$428,133.39) -- and correspondingly negative when the card
      is overpaid and owes you. For an asset it equals ``current_balance``. Use
      it when echoing a single balance back to a human.
    - ``current_balance`` is Monarch's raw ``currentBalance``, passed through
      unchanged. It is *usually* the signed balance, but Monarch returns it with
      the wrong sign on some liabilities, so do not sum it -- that is what
      ``signed_balance`` is for. Kept raw so the upstream value stays visible.
    - ``balance`` is a backward-compatible alias of ``current_balance`` and
      inherits the same caveat.
    - ``is_asset`` distinguishes assets from liabilities; without it the sign
      conventions above are ambiguous.

    Assets are positive and liabilities are normally negative, but do not treat
    "liability" as implying a negative -- an overpaid credit card sits in credit
    and is legitimately positive on both ``signed_balance`` and
    ``current_balance``.
    """
    try:
        client = await get_monarch_client()
        accounts = await client.get_accounts()

        account_list = []
        for account in accounts.get("accounts", []):
            account_info = {
                "id": account.get("id"),
                "name": account.get("displayName") or account.get("name"),
                "type": (account.get("type") or {}).get("name"),
                # Alias of current_balance, kept so existing callers/prompts
                # that read "balance" keep working. See the docstring for which
                # of the two balance conventions each field follows.
                "balance": account.get("currentBalance"),
                "current_balance": account.get("currentBalance"),
                "display_balance": account.get("displayBalance"),
                "signed_balance": _signed_balance(account),
                "is_asset": account.get("isAsset"),
                "institution": (account.get("institution") or {}).get("name"),
                "is_active": account.get("isActive")
                if "isActive" in account
                else not account.get("deactivatedAt"),
                "is_hidden": account.get("isHidden", False),
            }
            account_list.append(account_info)

        return json_success(account_list)
    except Exception as e:
        return json_error("get_accounts", e)


@mcp.tool()
async def refresh_accounts(account_ids: Optional[List[str]] = None) -> str:
    """Request account data refresh from financial institutions.

    Args:
        account_ids: Specific account IDs to refresh. If omitted or empty,
            refreshes all active, non-hidden accounts.
    """
    try:
        client = await get_monarch_client()
        if not account_ids:
            accounts = await client.get_accounts()
            account_ids = [
                a["id"]
                for a in accounts.get("accounts", [])
                if (
                    a.get("isActive", not a.get("deactivatedAt"))
                    and not a.get("isHidden")
                )
            ]
        if not account_ids:
            return json_success(
                {"refreshed": [], "message": "No active, visible accounts to refresh"}
            )
        result = await client.request_accounts_refresh(account_ids)
        return json_success(result)
    except Exception as e:
        return json_error("refresh_accounts", e)


@mcp.tool()
async def get_account_holdings(account_id: str) -> str:
    """
    Get investment holdings for a specific account.

    Args:
        account_id: The ID of the investment account
    """
    try:
        client = await get_monarch_client()
        holdings = await client.get_account_holdings(account_id)
        return json_success(holdings)
    except Exception as e:
        return json_error("get_account_holdings", e)


@mcp.tool()
async def get_account_balance_history(account_id: str) -> str:
    """
    Get historical balance data for a specific account.

    Returns all historical balance snapshots for tracking account growth over time.

    Args:
        account_id: The ID of the account (use get_accounts to find IDs)

    Returns:
        Historical balance snapshots for the account.

    Examples:
        Track savings account growth:
            get_account_balance_history(account_id="acc_123")
    """
    try:
        client = await get_monarch_client()
        snapshots = await client.get_account_history(account_id=int(account_id))

        # Sort oldest-first by date rather than trusting the API's order:
        # current/earliest and the sign of `change` all depend on it, and
        # ISO date strings sort chronologically.
        ordered = sorted(snapshots, key=lambda s: s.get("date") or "")

        formatted = {
            "account_id": account_id,
            "snapshot_count": len(ordered),
            "snapshots": []
        }

        if ordered:
            balances = [s.get("signedBalance", 0) for s in ordered if s.get("signedBalance") is not None]
            if balances:
                formatted["current_balance"] = balances[-1]
                formatted["earliest_balance"] = balances[0]
                formatted["change"] = balances[-1] - balances[0] if len(balances) > 1 else 0
                formatted["highest"] = max(balances)
                formatted["lowest"] = min(balances)

        for snapshot in ordered:
            formatted["snapshots"].append({
                "date": snapshot.get("date"),
                "balance": snapshot.get("signedBalance"),
            })

        return json_success(formatted)
    except Exception as e:
        return json_error("get_account_balance_history", e)


@mcp.tool()
async def upload_account_balance_history(
    account_id: str,
    corrections: str,
    dry_run: bool = False,
) -> str:
    """
    Upload corrected balance snapshots for an account.

    Fetches the full existing balance history, applies the corrections,
    and re-uploads the complete history.

    Args:
        account_id: The ID of the account to correct
        corrections: JSON object mapping ISO dates (YYYY-MM-DD) to corrected
                     balances, e.g. '{"2026-04-23": 24846.45, "2026-04-24": 24846.45}'
        dry_run: If True, return the planned changes without uploading

    Mismatched dates (corrections that do not match any existing snapshot) are
    surfaced explicitly in the response rather than silently dropped.
    """
    try:
        try:
            raw = json.loads(corrections)
        except json.JSONDecodeError as exc:
            return json_error(
                "upload_account_balance_history",
                ValueError(f"corrections is not valid JSON: {exc.msg}"),
            )

        if not isinstance(raw, dict):
            return json_error(
                "upload_account_balance_history",
                ValueError("corrections must be a JSON object mapping dates to numbers"),
            )

        try:
            validated = BalanceCorrections.model_validate(raw)
        except ValidationError as exc:
            return json_error("upload_account_balance_history", exc)

        date_to_balance: Dict[str, Decimal] = {
            d.isoformat(): amount for d, amount in validated.root.items()
        }

        if not date_to_balance:
            return json_success({
                "updated": False,
                "message": "No corrections provided",
            })

        from monarchmoney.monarchmoney import BalanceHistoryRow

        client = await get_monarch_client()
        snapshots = await client.get_account_history(account_id=int(account_id))

        existing_dates = {s.get("date") for s in snapshots}
        unmatched = sorted(d for d in date_to_balance if d not in existing_dates)

        applied: list[str] = []
        rows: list[BalanceHistoryRow] = []
        for snapshot in snapshots:
            date_str = snapshot.get("date")
            balance = snapshot.get("signedBalance", 0)
            account_name = snapshot.get("accountName", "")

            if date_str in date_to_balance:
                balance = float(date_to_balance[date_str])
                applied.append(date_str)

            rows.append(BalanceHistoryRow(
                date=datetime.strptime(date_str, "%Y-%m-%d"),
                amount=balance,
                account_name=account_name,
            ))

        if not applied:
            return json_success({
                "updated": False,
                "message": "No matching dates found in history",
                "unmatched_dates": unmatched,
            })

        if dry_run:
            return json_success({
                "dry_run": True,
                "account_id": account_id,
                "dates_to_correct": applied,
                "unmatched_dates": unmatched,
                "total_snapshots": len(rows),
            })

        result = await client.upload_account_balance_history(
            account_id=account_id,
            csv_content=rows,
        )

        return json_success({
            "updated": result,
            "dates_corrected": applied,
            "unmatched_dates": unmatched,
            "total_snapshots": len(rows),
        })
    except Exception as e:
        return json_error("upload_account_balance_history", e)
