"""Tests for financial-analysis MCP tools."""

import json
from datetime import date
from pathlib import Path

import pytest

from monarch_mcp_server.tools.financial import (
    get_cashflow,
    get_cashflow_summary,
    get_net_worth_by_account_type,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def cashflow_page():
    return json.loads((FIXTURES / "cashflow.json").read_text())


class TestGetNetWorthByAccountType:
    async def test_invalid_timeframe_returns_error_shape(self):
        """Invalid input must report through json_error, not a success payload."""
        result = json.loads(
            await get_net_worth_by_account_type(
                start_date="2024-01-01", timeframe="decade"
            )
        )
        assert result["error"] is True
        assert result["tool"] == "get_net_worth_by_account_type"
        assert "month" in result["message"]


class TestGetCashflow:
    async def test_returns_cashflow_data(self):
        result = json.loads(await get_cashflow())
        assert result["cashflow"]["income"] == 5000.00
        assert result["cashflow"]["expenses"] == -3200.00

    async def test_passes_date_params(self, mock_monarch_client):
        await get_cashflow(start_date="2026-01-01", end_date="2026-01-31")
        mock_monarch_client.get_cashflow.assert_called_once_with(
            start_date="2026-01-01", end_date="2026-01-31"
        )

    async def test_handles_api_error(self, mock_monarch_client):
        mock_monarch_client.get_cashflow.side_effect = Exception("Cashflow error")
        result = await get_cashflow()
        assert "get_cashflow" in result


class TestGetCashflowSummary:
    async def test_summary_block(self, mock_monarch_client, cashflow_page):
        mock_monarch_client.get_cashflow.return_value = cashflow_page
        result = json.loads(
            await get_cashflow_summary("2026-08-01", end_date="2026-09-19")
        )
        assert result["start_date"] == "2026-08-01"
        assert result["end_date"] == "2026-09-19"
        assert result["income"] == 15833.53
        assert result["expenses"] == 11368.56
        assert result["savings"] == 4464.97
        assert result["savings_rate_pct"] == 28.2

    async def test_income_by_category_sorted_desc_and_positive(
        self, mock_monarch_client, cashflow_page
    ):
        mock_monarch_client.get_cashflow.return_value = cashflow_page
        result = json.loads(await get_cashflow_summary("2026-09-01", "2026-09-19"))
        assert result["income_by_category"] == [
            {"category": "Paychecks", "amount": 9180.29},
            {"category": "Other Income", "amount": 5849.49},
            {"category": "Dividends & Capital Gains", "amount": 803.75},
        ]

    async def test_expenses_by_category_joins_group_name(
        self, mock_monarch_client, cashflow_page
    ):
        mock_monarch_client.get_cashflow.return_value = cashflow_page
        result = json.loads(await get_cashflow_summary("2026-09-01", "2026-09-19"))
        assert result["expenses_by_category"] == [
            {"category": "Mortgage", "group": "Housing", "amount": 3160.65},
            {"category": "Groceries", "group": "Food & Dining", "amount": 1112.89},
            {"category": "Utilities", "group": "Housing", "amount": 990.84},
            {"category": "Restaurants", "group": "Food & Dining", "amount": 604.18},
        ]

    async def test_expenses_by_group(self, mock_monarch_client, cashflow_page):
        mock_monarch_client.get_cashflow.return_value = cashflow_page
        result = json.loads(await get_cashflow_summary("2026-09-01", "2026-09-19"))
        assert result["expenses_by_group"] == [
            {"group": "Housing", "amount": 4151.49},
            {"group": "Food & Dining", "amount": 1717.07},
        ]

    async def test_transfer_categories_and_groups_excluded(
        self, mock_monarch_client, cashflow_page
    ):
        """Transfer, Credit Card Payment, Buy (transfer group) and Sell (which
        Monarch files under income) must not appear anywhere."""
        mock_monarch_client.get_cashflow.return_value = cashflow_page
        raw = await get_cashflow_summary("2026-09-01", "2026-09-19")
        for noise in ("Transfer", "Credit Card Payment", "Buy", "Sell"):
            assert f'"{noise}"' not in raw
        result = json.loads(raw)
        assert all(row["group"] != "Transfers" for row in result["expenses_by_group"])

    async def test_zero_amount_rows_dropped(self, mock_monarch_client, cashflow_page):
        mock_monarch_client.get_cashflow.return_value = cashflow_page
        result = json.loads(await get_cashflow_summary("2026-09-01", "2026-09-19"))
        assert "Refund" not in [r["category"] for r in result["income_by_category"]]

    async def test_top_n_caps_each_list(self, mock_monarch_client, cashflow_page):
        mock_monarch_client.get_cashflow.return_value = cashflow_page
        result = json.loads(
            await get_cashflow_summary("2026-09-01", "2026-09-19", top_n=1)
        )
        assert [r["category"] for r in result["income_by_category"]] == ["Paychecks"]
        assert [r["category"] for r in result["expenses_by_category"]] == ["Mortgage"]
        assert [r["group"] for r in result["expenses_by_group"]] == ["Housing"]

    async def test_end_date_defaults_to_today(self, mock_monarch_client, cashflow_page):
        mock_monarch_client.get_cashflow.return_value = cashflow_page
        result = json.loads(await get_cashflow_summary("2026-09-01"))
        today = date.today().isoformat()
        assert result["end_date"] == today
        mock_monarch_client.get_cashflow.assert_called_once_with(
            start_date="2026-09-01", end_date=today
        )

    @pytest.mark.parametrize("bad", ["2026/09/01", "09-01-2026", "2026-13-01", ""])
    async def test_invalid_start_date_is_error(self, mock_monarch_client, bad):
        result = json.loads(await get_cashflow_summary(bad))
        assert result["error"] is True
        assert result["tool"] == "get_cashflow_summary"
        assert "YYYY-MM-DD" in result["message"]
        mock_monarch_client.get_cashflow.assert_not_called()

    async def test_invalid_end_date_is_error(self, mock_monarch_client):
        result = json.loads(await get_cashflow_summary("2026-09-01", "next week"))
        assert result["error"] is True
        assert "YYYY-MM-DD" in result["message"]

    async def test_no_merchant_or_typename_leaks(
        self, mock_monarch_client, cashflow_page
    ):
        mock_monarch_client.get_cashflow.return_value = cashflow_page
        raw = await get_cashflow_summary("2026-09-01", "2026-09-19")
        assert "__typename" not in raw
        assert "Whole Foods" not in raw
        assert "logoUrl" not in raw
        assert "\n" not in raw
        assert len(raw) < 5000

    async def test_missing_summary_block_is_error(self, mock_monarch_client):
        mock_monarch_client.get_cashflow.return_value = {"byCategory": []}
        result = json.loads(await get_cashflow_summary("2026-09-01", "2026-09-19"))
        assert result["error"] is True

    async def test_upstream_error_is_reported(self, mock_monarch_client):
        mock_monarch_client.get_cashflow.side_effect = Exception("boom")
        result = json.loads(await get_cashflow_summary("2026-09-01", "2026-09-19"))
        assert result["error"] is True
        assert result["tool"] == "get_cashflow_summary"
