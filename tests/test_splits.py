"""Tests for transaction-splitting MCP tools."""

import json
from unittest.mock import AsyncMock, patch

from monarch_mcp_server.tools.splits import split_transaction


class TestSplitTransaction:
    @patch("monarch_mcp_server.tools.splits.get_monarch_client")
    async def test_dry_run_previews_without_mutating(self, mock_get_client):
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        result = await split_transaction(
            "txn_1", [{"amount": -10.0}, {"amount": -5.0}], dry_run=True
        )

        data = json.loads(result)
        assert data["dry_run"] is True
        assert data["split_total"] == -15.0
        assert data["split_count"] == 2
        mock_client.update_transaction_splits.assert_not_called()

    @patch("monarch_mcp_server.tools.splits.get_monarch_client")
    async def test_executes_and_reports_total(self, mock_get_client):
        mock_client = AsyncMock()
        mock_client.update_transaction_splits.return_value = {"ok": True}
        mock_get_client.return_value = mock_client

        result = await split_transaction(
            "txn_1", [{"amount": -10.0}, {"amount": -5.0}]
        )

        data = json.loads(result)
        assert data["success"] is True
        assert data["split_total"] == -15.0
        mock_client.update_transaction_splits.assert_awaited_once()

    @patch("monarch_mcp_server.tools.splits.get_monarch_client")
    async def test_malformed_split_rejected(self, mock_get_client):
        """A split lacking a numeric amount errors before any mutation."""
        mock_client = AsyncMock()
        mock_get_client.return_value = mock_client

        result = await split_transaction("txn_1", [{"categoryId": "c"}])

        data = json.loads(result)
        assert data["error"] is True
        mock_client.update_transaction_splits.assert_not_called()

    @patch("monarch_mcp_server.tools.splits.get_monarch_client")
    async def test_empty_splits_removes(self, mock_get_client):
        mock_client = AsyncMock()
        mock_client.update_transaction_splits.return_value = {}
        mock_get_client.return_value = mock_client

        result = await split_transaction("txn_1", [])

        data = json.loads(result)
        assert data["success"] is True
        assert "removed" in data["message"].lower()
        mock_client.update_transaction_splits.assert_awaited_once()
