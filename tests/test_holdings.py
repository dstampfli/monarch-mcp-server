"""Tests for the compact holdings tools (get_holdings_summary, get_all_holdings)."""

import json
from pathlib import Path

import pytest

from monarch_mcp_server.tools.holdings import get_all_holdings, get_holdings_summary

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name):
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def roth_holdings():
    return _load("holdings_roth.json")


@pytest.fixture
def empty_holdings():
    return _load("holdings_empty.json")


@pytest.fixture
def brokerage_accounts():
    """Accounts list shaped like GetAccounts, with three brokerage accounts."""
    return {
        "accounts": [
            {
                "id": "acc-roth",
                "displayName": "Chase Mgd Roth IRA (...1234)",
                "type": {"name": "brokerage", "display": "Investments"},
                "subtype": {"name": "roth", "display": "Roth IRA"},
                "currentBalance": 398559.78,
                "displayBalance": 398559.78,
                "isAsset": True,
                "deactivatedAt": None,
                "isHidden": False,
                "holdingsCount": 5,
            },
            {
                "id": "acc-403b",
                "displayName": "Lincoln Investment 403b (...5513)",
                "type": {"name": "brokerage", "display": "Investments"},
                "subtype": {"name": "403b", "display": "403(b)"},
                "currentBalance": 96073.76,
                "displayBalance": 96073.76,
                "isAsset": True,
                "deactivatedAt": None,
                "isHidden": False,
                "holdingsCount": 0,
            },
            {
                "id": "acc-old",
                "displayName": "Closed Brokerage",
                "type": {"name": "brokerage", "display": "Investments"},
                "subtype": {"name": "brokerage", "display": "Brokerage"},
                "currentBalance": 0.0,
                "displayBalance": 0.0,
                "isAsset": True,
                "deactivatedAt": "2025-01-01T00:00:00+00:00",
                "isHidden": False,
                "holdingsCount": 0,
            },
            {
                "id": "acc-chk",
                "displayName": "Checking",
                "type": {"name": "depository", "display": "Cash"},
                "subtype": {"name": "checking", "display": "Checking"},
                "currentBalance": 1500.0,
                "displayBalance": 1500.0,
                "isAsset": True,
                "deactivatedAt": None,
                "isHidden": False,
                "holdingsCount": 0,
            },
        ]
    }


class TestGetHoldingsSummary:
    async def test_projects_each_aggregate_holding_to_compact_row(
        self, mock_monarch_client, roth_holdings
    ):
        mock_monarch_client.get_account_holdings.return_value = roth_holdings

        rows = json.loads(await get_holdings_summary("acc-roth"))

        fxaix = next(r for r in rows if r["ticker"] == "FXAIX")
        assert fxaix == {
            "account_id": "acc-roth",
            "ticker": "FXAIX",
            "name": "Fidelity 500 Index Fund",
            "type": "mutual_fund",
            "quantity": 1422.904,
            "price": 266.43,
            "value": 379104.31,
            "basis": 382045.04,
            "gain_loss": -2940.73,
            "gain_loss_pct": -0.77,
            "price_as_of": "2026-09-18",
        }

    async def test_uses_holding_price_not_stale_security_price(
        self, mock_monarch_client, roth_holdings
    ):
        mock_monarch_client.get_account_holdings.return_value = roth_holdings
        rows = json.loads(await get_holdings_summary("acc-roth"))
        fxaix = next(r for r in rows if r["ticker"] == "FXAIX")
        # security.closingPrice is 150.12 (2023); holdings[0].closingPrice wins.
        assert fxaix["price"] == 266.43

    async def test_ticker_falls_back_from_holding_when_security_ticker_null(
        self, mock_monarch_client, roth_holdings
    ):
        mock_monarch_client.get_account_holdings.return_value = roth_holdings
        rows = json.loads(await get_holdings_summary("acc-roth"))
        bbax = next(r for r in rows if r["name"].startswith("JPMorgan BetaBuilders"))
        assert bbax["ticker"] == "BBAX"

    async def test_falls_back_to_security_when_holdings_list_empty(
        self, mock_monarch_client, roth_holdings
    ):
        mock_monarch_client.get_account_holdings.return_value = roth_holdings
        rows = json.loads(await get_holdings_summary("acc-roth"))
        aapl = next(r for r in rows if r["ticker"] == "AAPL")
        assert aapl["name"] == "Apple Inc"
        assert aapl["type"] == "equity"
        assert aapl["price"] is None
        assert aapl["price_as_of"] is None

    async def test_cash_sweep_with_zero_basis_has_null_gain_pct(
        self, mock_monarch_client, roth_holdings
    ):
        mock_monarch_client.get_account_holdings.return_value = roth_holdings
        rows = json.loads(await get_holdings_summary("acc-roth"))
        sweep = next(r for r in rows if r["name"] == "JPMorgan Liquidity Sweep")
        assert sweep["ticker"] is None
        assert sweep["type"] == "cash"
        assert sweep["basis"] == 0
        assert sweep["gain_loss"] == 4210.77
        assert sweep["gain_loss_pct"] is None

    async def test_every_row_has_ticker_or_name(
        self, mock_monarch_client, roth_holdings
    ):
        mock_monarch_client.get_account_holdings.return_value = roth_holdings
        rows = json.loads(await get_holdings_summary("acc-roth"))
        assert len(rows) == 5
        assert all(r["ticker"] or r["name"] for r in rows)

    async def test_sorted_by_value_descending(self, mock_monarch_client, roth_holdings):
        mock_monarch_client.get_account_holdings.return_value = roth_holdings
        rows = json.loads(await get_holdings_summary("acc-roth"))
        values = [r["value"] for r in rows]
        assert values == sorted(values, reverse=True)

    async def test_drops_null_change_fields_and_typename(
        self, mock_monarch_client, roth_holdings
    ):
        mock_monarch_client.get_account_holdings.return_value = roth_holdings
        raw = await get_holdings_summary("acc-roth")
        assert "__typename" not in raw
        assert "securityPriceChange" not in raw
        assert "oneDayChange" not in raw

    async def test_is_compact(self, mock_monarch_client, roth_holdings):
        mock_monarch_client.get_account_holdings.return_value = roth_holdings
        raw = await get_holdings_summary("acc-roth")
        rows = json.loads(raw)
        # Spec target: <= 250 bytes per holding on the wire, so the tool must
        # serialize compactly (no indentation) unlike the raw pass-throughs.
        assert "\n" not in raw
        assert len(raw) / len(rows) <= 250

    async def test_all_holdings_is_compact(
        self, mock_monarch_client, roth_holdings, brokerage_accounts
    ):
        mock_monarch_client.get_accounts.return_value = brokerage_accounts
        mock_monarch_client.get_account_holdings.return_value = roth_holdings
        raw = await get_all_holdings()
        assert "\n" not in raw

    async def test_empty_edges_for_known_account_returns_empty_list(
        self, mock_monarch_client, empty_holdings, brokerage_accounts
    ):
        mock_monarch_client.get_account_holdings.return_value = empty_holdings
        mock_monarch_client.get_accounts.return_value = brokerage_accounts
        rows = json.loads(await get_holdings_summary("acc-403b"))
        assert rows == []

    async def test_empty_edges_for_unknown_account_is_error(
        self, mock_monarch_client, empty_holdings, brokerage_accounts
    ):
        mock_monarch_client.get_account_holdings.return_value = empty_holdings
        mock_monarch_client.get_accounts.return_value = brokerage_accounts
        result = json.loads(await get_holdings_summary("acc-nope"))
        assert result["error"] is True
        assert result["tool"] == "get_holdings_summary"
        assert "acc-nope" in result["message"]

    async def test_does_not_call_get_accounts_when_holdings_present(
        self, mock_monarch_client, roth_holdings
    ):
        mock_monarch_client.get_account_holdings.return_value = roth_holdings
        await get_holdings_summary("acc-roth")
        mock_monarch_client.get_accounts.assert_not_called()

    async def test_upstream_error_is_reported(self, mock_monarch_client):
        mock_monarch_client.get_account_holdings.side_effect = Exception("boom")
        result = json.loads(await get_holdings_summary("acc-roth"))
        assert result["error"] is True
        assert result["tool"] == "get_holdings_summary"


class TestGetAllHoldings:
    def _route(self, roth_holdings, empty_holdings, failing=()):
        async def _get(account_id):
            if account_id in failing:
                raise Exception(f"sync failed for {account_id}")
            if account_id == "acc-roth":
                return roth_holdings
            return empty_holdings

        return _get

    async def test_includes_only_active_nonzero_brokerage_accounts(
        self, mock_monarch_client, roth_holdings, empty_holdings, brokerage_accounts
    ):
        mock_monarch_client.get_accounts.return_value = brokerage_accounts
        mock_monarch_client.get_account_holdings.side_effect = self._route(
            roth_holdings, empty_holdings
        )
        result = json.loads(await get_all_holdings())
        ids = [a["account_id"] for a in result["accounts"]]
        assert ids == ["acc-roth", "acc-403b"]

    async def test_include_inactive_keeps_closed_and_zero_balance_accounts(
        self, mock_monarch_client, roth_holdings, empty_holdings, brokerage_accounts
    ):
        mock_monarch_client.get_accounts.return_value = brokerage_accounts
        mock_monarch_client.get_account_holdings.side_effect = self._route(
            roth_holdings, empty_holdings
        )
        result = json.loads(await get_all_holdings(include_inactive=True))
        ids = [a["account_id"] for a in result["accounts"]]
        assert ids == ["acc-roth", "acc-403b", "acc-old"]

    async def test_account_rows_carry_balance_and_holdings_stats(
        self, mock_monarch_client, roth_holdings, empty_holdings, brokerage_accounts
    ):
        mock_monarch_client.get_accounts.return_value = brokerage_accounts
        mock_monarch_client.get_account_holdings.side_effect = self._route(
            roth_holdings, empty_holdings
        )
        result = json.loads(await get_all_holdings())
        roth, b403 = result["accounts"]
        assert roth == {
            "account_id": "acc-roth",
            "account_name": "Chase Mgd Roth IRA (...1234)",
            "balance": 398559.78,
            "holdings_value": 398559.79,
            "holdings_count": 5,
            "has_holdings": True,
            "error": None,
        }
        assert b403["has_holdings"] is False
        assert b403["holdings_count"] == 0
        assert b403["holdings_value"] == 0
        assert b403["balance"] == 96073.76

    async def test_holdings_are_flattened_with_account_name(
        self, mock_monarch_client, roth_holdings, empty_holdings, brokerage_accounts
    ):
        mock_monarch_client.get_accounts.return_value = brokerage_accounts
        mock_monarch_client.get_account_holdings.side_effect = self._route(
            roth_holdings, empty_holdings
        )
        result = json.loads(await get_all_holdings())
        assert len(result["holdings"]) == 5
        fxaix = next(h for h in result["holdings"] if h["ticker"] == "FXAIX")
        assert fxaix["account_id"] == "acc-roth"
        assert fxaix["account_name"] == "Chase Mgd Roth IRA (...1234)"
        assert fxaix["value"] == 379104.31

    async def test_totals_and_cash_like_value(
        self, mock_monarch_client, roth_holdings, empty_holdings, brokerage_accounts
    ):
        mock_monarch_client.get_accounts.return_value = brokerage_accounts
        mock_monarch_client.get_account_holdings.side_effect = self._route(
            roth_holdings, empty_holdings
        )
        result = json.loads(await get_all_holdings())
        totals = result["totals"]
        assert totals["investment_accounts_balance"] == round(398559.78 + 96073.76, 2)
        assert totals["holdings_value"] == 398559.79
        # cash sweep (type cash) + fed fund money market (type other)
        assert totals["cash_like_value"] == round(4210.77 + 1500.0, 2)

    async def test_cash_like_matches_sweep_name_at_par_regardless_of_type(
        self, mock_monarch_client, roth_holdings, empty_holdings, brokerage_accounts
    ):
        # Reclassify the sweep row as a mutual_fund: it should still count as
        # cash-like because price == 1.0 and the name matches /sweep/i.
        node = roth_holdings["portfolio"]["aggregateHoldings"]["edges"][2]["node"]
        node["holdings"][0]["type"] = "mutual_fund"
        mock_monarch_client.get_accounts.return_value = brokerage_accounts
        mock_monarch_client.get_account_holdings.side_effect = self._route(
            roth_holdings, empty_holdings
        )
        result = json.loads(await get_all_holdings())
        assert result["totals"]["cash_like_value"] == round(4210.77 + 1500.0, 2)

    async def test_one_failing_account_does_not_fail_the_call(
        self, mock_monarch_client, roth_holdings, empty_holdings, brokerage_accounts
    ):
        mock_monarch_client.get_accounts.return_value = brokerage_accounts
        mock_monarch_client.get_account_holdings.side_effect = self._route(
            roth_holdings, empty_holdings, failing={"acc-403b"}
        )
        result = json.loads(await get_all_holdings())
        assert "error" not in result or result.get("error") is not True
        failed = next(a for a in result["accounts"] if a["account_id"] == "acc-403b")
        assert failed["has_holdings"] is False
        assert "sync failed" in failed["error"]
        ok = next(a for a in result["accounts"] if a["account_id"] == "acc-roth")
        assert ok["error"] is None
        assert ok["has_holdings"] is True

    async def test_as_of_is_today(
        self, mock_monarch_client, roth_holdings, empty_holdings, brokerage_accounts
    ):
        from datetime import date

        mock_monarch_client.get_accounts.return_value = brokerage_accounts
        mock_monarch_client.get_account_holdings.side_effect = self._route(
            roth_holdings, empty_holdings
        )
        result = json.loads(await get_all_holdings())
        assert result["as_of"] == date.today().isoformat()

    async def test_fetches_accounts_concurrently(
        self, mock_monarch_client, roth_holdings, empty_holdings, brokerage_accounts
    ):
        import asyncio

        in_flight = 0
        peak = 0

        async def _slow(account_id):
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.01)
            in_flight -= 1
            return roth_holdings if account_id == "acc-roth" else empty_holdings

        mock_monarch_client.get_accounts.return_value = brokerage_accounts
        mock_monarch_client.get_account_holdings.side_effect = _slow
        await get_all_holdings()
        assert peak == 2

    async def test_accounts_fetch_error_is_reported(self, mock_monarch_client):
        mock_monarch_client.get_accounts.side_effect = Exception("boom")
        result = json.loads(await get_all_holdings())
        assert result["error"] is True
        assert result["tool"] == "get_all_holdings"
