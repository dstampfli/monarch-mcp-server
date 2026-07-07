"""Transaction splitting tools."""

import logging
from typing import Any, Dict, List

from monarch_mcp_server.app import mcp
from monarch_mcp_server.client import get_monarch_client
from monarch_mcp_server.helpers import json_success, json_error

logger = logging.getLogger(__name__)


@mcp.tool()
async def get_transaction_splits(transaction_id: str) -> str:
    """
    Get the splits for a transaction.

    Returns the split details if the transaction has been split into multiple parts.

    Args:
        transaction_id: The ID of the transaction to get splits for

    Returns:
        Split information for the transaction, or empty if not split.
    """
    try:
        client = await get_monarch_client()
        result = await client.get_transaction_splits(transaction_id=transaction_id)
        return json_success(result)
    except Exception as e:
        return json_error("get_transaction_splits", e)


@mcp.tool()
async def split_transaction(
    transaction_id: str,
    splits: List[Dict[str, Any]],
    dry_run: bool = False,
) -> str:
    """
    Split a transaction into multiple parts with different categories/merchants.

    The sum of all split amounts must equal the original transaction amount.
    Pass an empty list to remove all splits and restore the original transaction.

    Args:
        transaction_id: The ID of the transaction to split
        splits: List of split objects. Each split should have:
            - amount: The amount for this split (negative for expenses, positive for income)
            - categoryId: (optional) The category ID for this split
            - merchantName: (optional) The merchant name for this split
        dry_run: If True, validate and echo the planned splits (including their
            computed total) without mutating the transaction.

    Returns:
        The updated split information for the transaction.
    """
    try:
        # Validate the split shape and compute the total up front so a
        # malformed list is rejected before any mutation, and the caller can
        # confirm the total matches the original transaction amount.
        split_total = 0.0
        for i, split in enumerate(splits):
            if not isinstance(split, dict) or "amount" not in split:
                raise ValueError(f"split #{i} must be an object with an 'amount'")
            amount = split["amount"]
            if isinstance(amount, bool) or not isinstance(amount, (int, float)):
                raise ValueError(f"split #{i} 'amount' must be a number")
            split_total += amount
        split_total = round(split_total, 2)

        if dry_run:
            return json_success({
                "dry_run": True,
                "transaction_id": transaction_id,
                "planned_splits": splits,
                "split_count": len(splits),
                "split_total": split_total,
                "message": (
                    "Dry run — no changes made. Ensure split_total equals the "
                    "original transaction amount before running for real."
                    if splits
                    else "Dry run — would remove all splits."
                ),
            })

        client = await get_monarch_client()
        result = await client.update_transaction_splits(
            transaction_id=transaction_id,
            split_data=splits,
        )

        return json_success({
            "success": True,
            "message": f"Transaction split into {len(splits)} parts" if splits else "Splits removed from transaction",
            "split_total": split_total,
            "splits": result,
        })
    except Exception as e:
        return json_error("split_transaction", e)
