"""Tests for account-related MCP tools."""

import json

from monarch_mcp_server.tools.accounts import (
    get_accounts,
    get_account_holdings,
    refresh_accounts,
    get_account_balance_history,
    upload_account_balance_history,
)


class TestGetAccounts:
    async def test_returns_formatted_account_list(self):
        result = json.loads(await get_accounts())
        assert len(result) == 3
        assert result[0]["id"] == "acc-1"
        assert result[0]["name"] == "Checking Account"
        assert result[0]["type"] == "checking"
        assert result[0]["balance"] == 1500.00
        assert result[0]["current_balance"] == 1500.00
        assert result[0]["display_balance"] == 500.00
        assert result[0]["is_asset"] is True
        assert result[0]["institution"] == "Test Bank"
        assert result[0]["is_active"] is True
        assert result[0]["is_hidden"] is False

    async def test_hidden_account_flagged(self):
        result = json.loads(await get_accounts())
        assert result[1]["is_hidden"] is True

    async def test_balance_alias_tracks_current_balance(self):
        """`balance` is a documented alias of `current_balance`, not of display."""
        for account in json.loads(await get_accounts()):
            assert account["balance"] == account["current_balance"]

    async def test_liability_signs_passed_through_unchanged(self):
        """Liabilities carry a negative current_balance and positive display_balance.

        Both are surfaced verbatim -- callers pick the convention they need,
        and is_asset is what tells them apart.
        """
        card = json.loads(await get_accounts())[2]
        assert card["id"] == "acc-3"
        assert card["is_asset"] is False
        assert card["current_balance"] == -250.00
        assert card["display_balance"] == 250.00

    async def test_signed_balance_for_asset_is_current_balance(self):
        result = json.loads(await get_accounts())
        assert result[0]["is_asset"] is True
        assert result[0]["signed_balance"] == result[0]["current_balance"]

    async def test_signed_balance_negates_amount_owed_for_liability(self):
        card = json.loads(await get_accounts())[2]
        assert card["is_asset"] is False
        assert card["display_balance"] == 250.00
        assert card["signed_balance"] == -250.00

    async def test_signed_balance_corrects_wrong_current_balance_sign(
        self, mock_monarch_client
    ):
        """The Kohl's case: Monarch returns a positive current_balance for a debt.

        Some MX-sourced cards that Monarch typed ``other`` rather than
        ``credit_card`` come back with currentBalance positive while the stored
        balance history -- what net worth is built from -- holds the negative.
        display_balance is correct, so signed_balance must follow it, not
        current_balance.
        """
        mock_monarch_client.get_accounts.return_value = {
            "accounts": [
                {
                    "id": "acc-6",
                    "displayName": "Store Card",
                    "type": {"name": "credit"},
                    "subtype": {"name": "other"},
                    "currentBalance": 36.05,  # wrong sign, upstream
                    "displayBalance": 36.05,  # owed, correct
                    "isAsset": False,
                    "institution": None,
                    "deactivatedAt": None,
                    "isHidden": False,
                }
            ]
        }
        card = json.loads(await get_accounts())[0]
        assert card["current_balance"] == 36.05
        assert card["signed_balance"] == -36.05

    async def test_signed_balance_falls_back_when_undeterminable(
        self, mock_monarch_client
    ):
        """No isAsset, or no displayBalance -> fall back, never guess a sign."""
        mock_monarch_client.get_accounts.return_value = {
            "accounts": [
                {  # isAsset missing: nothing to key the negation off
                    "id": "acc-7",
                    "displayName": "No Flag",
                    "type": {"name": "credit"},
                    "currentBalance": -10.0,
                    "displayBalance": 10.0,
                    "institution": None,
                    "deactivatedAt": None,
                    "isHidden": False,
                },
                {  # liability with no displayBalance to negate
                    "id": "acc-8",
                    "displayName": "No Display",
                    "type": {"name": "credit"},
                    "currentBalance": -20.0,
                    "displayBalance": None,
                    "isAsset": False,
                    "institution": None,
                    "deactivatedAt": None,
                    "isHidden": False,
                },
            ]
        }
        result = json.loads(await get_accounts())
        assert result[0]["signed_balance"] == -10.0
        assert result[1]["signed_balance"] == -20.0

    async def test_overpaid_liability_keeps_positive_signed_balance(
        self, mock_monarch_client
    ):
        """A liability in credit is legitimately positive -- do not "correct" it.

        An overpaid card owes the user money, so its signed balance adds to net
        worth and display_balance (amount owed) goes negative. is_asset stays
        False: the account is still a liability, it just happens to be in credit.
        """
        mock_monarch_client.get_accounts.return_value = {
            "accounts": [
                {
                    "id": "acc-5",
                    "displayName": "Overpaid Card",
                    "type": {"name": "credit"},
                    "currentBalance": 17.51,
                    "displayBalance": -17.51,
                    "isAsset": False,
                    "institution": None,
                    "deactivatedAt": None,
                    "isHidden": False,
                }
            ]
        }
        card = json.loads(await get_accounts())[0]
        assert card["is_asset"] is False
        assert card["current_balance"] == 17.51
        assert card["display_balance"] == -17.51
        assert card["signed_balance"] == 17.51

    async def test_is_asset_absent_yields_none(self, mock_monarch_client):
        """Older/narrower responses without isAsset must not raise."""
        mock_monarch_client.get_accounts.return_value = {
            "accounts": [
                {
                    "id": "acc-4",
                    "displayName": "No Asset Flag",
                    "type": {"name": "credit"},
                    "currentBalance": -10.0,
                    "displayBalance": 10.0,
                    "institution": None,
                    "deactivatedAt": None,
                    "isHidden": False,
                }
            ]
        }
        result = json.loads(await get_accounts())
        assert result[0]["is_asset"] is None

    async def test_handles_null_type(self, mock_monarch_client):
        mock_monarch_client.get_accounts.return_value = {
            "accounts": [
                {
                    "id": "acc-3",
                    "displayName": "Unknown",
                    "type": None,
                    "currentBalance": 0,
                    "displayBalance": 0,
                    "institution": None,
                    "deactivatedAt": None,
                    "isHidden": False,
                }
            ]
        }
        result = json.loads(await get_accounts())
        assert result[0]["type"] is None
        assert result[0]["institution"] is None
        assert result[0]["balance"] == 0
        assert result[0]["current_balance"] == 0
        assert result[0]["display_balance"] == 0

    async def test_handles_empty_accounts(self, mock_monarch_client):
        mock_monarch_client.get_accounts.return_value = {"accounts": []}
        result = json.loads(await get_accounts())
        assert result == []

    async def test_handles_api_error(self, mock_monarch_client):
        mock_monarch_client.get_accounts.side_effect = Exception("API timeout")
        result = await get_accounts()
        assert "get_accounts" in result
        assert "API timeout" in result


class TestGetAccountHoldings:
    async def test_returns_holdings(self):
        result = json.loads(await get_account_holdings("acc-1"))
        assert result["holdings"][0]["name"] == "VTI"
        assert result["holdings"][0]["value"] == 25000.00

    async def test_passes_account_id(self, mock_monarch_client):
        await get_account_holdings("acc-99")
        mock_monarch_client.get_account_holdings.assert_called_once_with("acc-99")

    async def test_handles_api_error(self, mock_monarch_client):
        mock_monarch_client.get_account_holdings.side_effect = Exception("Not found")
        result = await get_account_holdings("bad-id")
        assert "get_account_holdings" in result


class TestRefreshAccounts:
    async def test_auto_discovers_active_visible_accounts(self, mock_monarch_client):
        """No args → fetch accounts, refresh only active+non-hidden ones."""
        result = json.loads(await refresh_accounts())
        assert result["requestAccountsRefresh"]["success"] is True
        # Default fixture: acc-1 and acc-3 active+visible, acc-2 hidden.
        mock_monarch_client.request_accounts_refresh.assert_awaited_once_with(
            ["acc-1", "acc-3"]
        )

    async def test_passes_explicit_account_ids(self, mock_monarch_client):
        """Explicit account_ids must be passed through unchanged."""
        result = json.loads(await refresh_accounts(account_ids=["acc-9", "acc-42"]))
        assert result["requestAccountsRefresh"]["success"] is True
        mock_monarch_client.request_accounts_refresh.assert_awaited_once_with(
            ["acc-9", "acc-42"]
        )
        # Must not have looked up accounts when caller specified the list.
        mock_monarch_client.get_accounts.assert_not_called()

    async def test_empty_list_falls_back_to_auto_discover(self, mock_monarch_client):
        """An empty list is treated as 'refresh all visible', matching no-arg."""
        await refresh_accounts(account_ids=[])
        mock_monarch_client.request_accounts_refresh.assert_awaited_once_with(
            ["acc-1", "acc-3"]
        )

    async def test_no_visible_accounts_returns_graceful_message(
        self, mock_monarch_client
    ):
        """If every account is hidden or inactive, do not call the upstream API."""
        mock_monarch_client.get_accounts.return_value = {
            "accounts": [
                {"id": "acc-h", "isHidden": True, "deactivatedAt": None},
                {"id": "acc-d", "isHidden": False, "deactivatedAt": "2025-01-01"},
            ]
        }
        result = json.loads(await refresh_accounts())
        assert result["refreshed"] == []
        assert "No active" in result["message"]
        mock_monarch_client.request_accounts_refresh.assert_not_called()

    async def test_handles_api_error(self, mock_monarch_client):
        mock_monarch_client.request_accounts_refresh.side_effect = Exception("Timeout")
        result = await refresh_accounts(account_ids=["acc-1"])
        assert "refresh_accounts" in result


class TestGetAccountBalanceHistory:
    async def test_returns_formatted_snapshots(self):
        result = json.loads(await get_account_balance_history("12345"))
        assert result["account_id"] == "12345"
        assert result["snapshot_count"] == 3
        assert result["current_balance"] == 1100.0
        assert result["earliest_balance"] == 1000.0
        assert result["highest"] == 1200.0
        assert result["lowest"] == 1000.0
        assert result["snapshots"][0] == {"date": "2026-04-20", "balance": 1000.0}

    async def test_sorts_snapshots_by_date(self, mock_monarch_client):
        """current/earliest/change must not depend on the API's return order."""
        mock_monarch_client.get_account_history.return_value = [
            {"date": "2026-04-22", "signedBalance": 1100.0},
            {"date": "2026-04-20", "signedBalance": 1000.0},
            {"date": "2026-04-21", "signedBalance": 1050.0},
        ]
        result = json.loads(await get_account_balance_history("12345"))
        assert result["earliest_balance"] == 1000.0
        assert result["current_balance"] == 1100.0
        assert result["change"] == 100.0
        assert result["snapshots"][0] == {"date": "2026-04-20", "balance": 1000.0}
        assert result["snapshots"][-1] == {"date": "2026-04-22", "balance": 1100.0}

    async def test_handles_empty_history(self, mock_monarch_client):
        mock_monarch_client.get_account_history.return_value = []
        result = json.loads(await get_account_balance_history("12345"))
        assert result["snapshot_count"] == 0
        assert result["snapshots"] == []

    async def test_handles_api_error(self, mock_monarch_client):
        mock_monarch_client.get_account_history.side_effect = Exception("Not found")
        result = await get_account_balance_history("12345")
        assert "get_account_balance_history" in result


class TestUploadAccountBalanceHistory:
    async def test_applies_corrections(self, mock_monarch_client):
        corrections = json.dumps({"2026-04-21": 900.0})
        result = json.loads(await upload_account_balance_history("12345", corrections))
        assert result["updated"] is True
        assert result["dates_corrected"] == ["2026-04-21"]
        assert result["unmatched_dates"] == []
        assert result["total_snapshots"] == 3

        call_args = mock_monarch_client.upload_account_balance_history.call_args
        assert call_args.kwargs["account_id"] == "12345"
        assert len(call_args.kwargs["csv_content"]) == 3

    async def test_no_matching_dates(self, mock_monarch_client):
        corrections = json.dumps({"2026-01-01": 500.0})
        result = json.loads(await upload_account_balance_history("12345", corrections))
        assert result["updated"] is False
        assert result["unmatched_dates"] == ["2026-01-01"]
        mock_monarch_client.upload_account_balance_history.assert_not_called()

    async def test_surfaces_unmatched_alongside_applied(self, mock_monarch_client):
        corrections = json.dumps({"2026-04-21": 900.0, "2099-12-31": 1.0})
        result = json.loads(await upload_account_balance_history("12345", corrections))
        assert result["updated"] is True
        assert result["dates_corrected"] == ["2026-04-21"]
        assert result["unmatched_dates"] == ["2099-12-31"]

    async def test_dry_run_skips_upload(self, mock_monarch_client):
        corrections = json.dumps({"2026-04-21": 900.0})
        result = json.loads(
            await upload_account_balance_history("12345", corrections, dry_run=True)
        )
        assert result["dry_run"] is True
        assert result["dates_to_correct"] == ["2026-04-21"]
        assert result["total_snapshots"] == 3
        mock_monarch_client.upload_account_balance_history.assert_not_called()

    async def test_rejects_invalid_json(self, mock_monarch_client):
        result = json.loads(
            await upload_account_balance_history("12345", "not json")
        )
        assert result["error"] is True
        assert "valid JSON" in result["message"]
        mock_monarch_client.get_account_history.assert_not_called()

    async def test_rejects_non_object(self, mock_monarch_client):
        result = json.loads(
            await upload_account_balance_history("12345", "[1, 2, 3]")
        )
        assert result["error"] is True
        assert "JSON object" in result["message"]
        mock_monarch_client.get_account_history.assert_not_called()

    async def test_rejects_invalid_date_key(self, mock_monarch_client):
        result = json.loads(
            await upload_account_balance_history("12345", '{"yesterday": 100}')
        )
        assert result["error"] is True
        mock_monarch_client.get_account_history.assert_not_called()

    async def test_rejects_non_numeric_value(self, mock_monarch_client):
        result = json.loads(
            await upload_account_balance_history(
                "12345", '{"2026-04-21": "not-a-number"}'
            )
        )
        assert result["error"] is True
        mock_monarch_client.get_account_history.assert_not_called()

    async def test_handles_api_error(self, mock_monarch_client):
        mock_monarch_client.get_account_history.side_effect = Exception("Timeout")
        result = await upload_account_balance_history("12345", '{"2026-04-21": 0}')
        assert "upload_account_balance_history" in result
