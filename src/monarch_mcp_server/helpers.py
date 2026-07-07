"""Shared helpers for Monarch MCP Server tools."""

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def format_exception(exc: Exception) -> str:
    """Best-effort string representation for tool error responses.

    Some exceptions (e.g. certain async/transport errors) stringify to ``""``;
    fall back to ``repr`` and finally the class name so the message is never
    blank.
    """
    message = str(exc).strip()
    if message:
        return message
    rep = repr(exc).strip()
    if rep:
        return rep
    return type(exc).__name__


def first_present(*values: Any) -> Any:
    """Return the first value that is not None and not an empty string."""
    for value in values:
        if value is not None and value != "":
            return value
    return None


def tool_response_envelope(
    tool: str,
    args: Dict[str, Any],
    rows: List[Dict[str, Any]],
    *,
    total_count: Optional[int] = None,
    search_info: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Wrap a list of rows in a self-describing envelope.

    Lets agents see how much was returned, whether more is available, and which
    search strategy ran without re-asking. ``truncated`` is True when the server
    reports more rows than were returned, or when the page filled exactly to the
    limit and total_count is unknown.
    """
    count = len(rows)

    def _as_int(value: Any) -> Optional[int]:
        """Coerce a possibly string-typed pagination arg to int, or None."""
        if isinstance(value, bool) or value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    limit = _as_int(args.get("limit"))
    offset = _as_int(args.get("offset")) or 0
    truncated = (
        offset + count < total_count
        if isinstance(total_count, int) and not isinstance(total_count, bool)
        else isinstance(limit, int) and count == limit
    )

    return {
        "tool": tool,
        "args": args,
        "count": count,
        "total_count": total_count,
        "truncated": truncated,
        "search": search_info,
        "data": rows,
    }


def format_transaction(txn: Dict[str, Any], extended: bool = False) -> Dict[str, Any]:
    """Format a raw Monarch transaction dict into a consistent output format.

    Args:
        txn: Raw transaction dict from the Monarch API.
        extended: If True, include extra fields like is_split, is_recurring,
                  has_attachments.
    """
    # Nested fields are guarded with isinstance rather than truthiness: if the
    # API ever returns a truthy non-dict (or non-list tags), a plain .get()
    # would raise AttributeError and mask the whole transaction behind an
    # opaque error.
    merchant = txn.get("merchant")
    merchant = merchant if isinstance(merchant, dict) else {}
    category = txn.get("category")
    category = category if isinstance(category, dict) else {}
    account = txn.get("account")
    account = account if isinstance(account, dict) else {}
    tags = txn.get("tags")
    tags = tags if isinstance(tags, list) else []

    info: Dict[str, Any] = {
        "id": txn.get("id"),
        "date": txn.get("date"),
        "amount": txn.get("amount"),
        "merchant": merchant.get("name") or None,
        "original_name": txn.get("plaidName") or txn.get("originalName"),
        "category": category.get("name") or None,
        "category_id": category.get("id") or None,
        "account": account.get("displayName") or None,
        "account_id": account.get("id") or None,
        "notes": txn.get("notes"),
        "needs_review": txn.get("needsReview", False),
        "is_pending": txn.get("pending", False),
        "hide_from_reports": txn.get("hideFromReports", False),
        "tags": [
            {"id": tag.get("id"), "name": tag.get("name")}
            for tag in tags
            if isinstance(tag, dict)
        ],
    }

    if extended:
        info["is_split"] = txn.get("isSplitTransaction", False)
        info["is_recurring"] = txn.get("isRecurring", False)
        info["has_attachments"] = bool(txn.get("attachments"))

    return info


def json_success(data: Any) -> str:
    """Serialize *data* to a JSON string for tool responses."""
    return json.dumps(data, indent=2, default=str)


def json_error(tool_name: str, exc: Exception) -> str:
    """Return a consistent JSON error string and log the failure."""
    logger.error(f"Failed in {tool_name}: {exc}")
    return json.dumps(
        {"error": True, "tool": tool_name, "message": format_exception(exc)},
        indent=2,
        default=str,
    )
