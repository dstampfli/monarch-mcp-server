"""Tests for elicitation-based auth tools."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from monarchmoney import CaptchaRequiredException, RequireMFAException

from monarch_mcp_server import auth
from monarch_mcp_server.monarch_auth import EmailOtpRequiredException


def make_ctx(*elicit_results):
    """Build a mock Context whose elicit() returns the given results in order."""
    ctx = MagicMock()
    ctx.elicit = AsyncMock(side_effect=list(elicit_results))
    return ctx


def accept(**fields):
    return SimpleNamespace(action="accept", data=SimpleNamespace(**fields))


def cancel():
    return SimpleNamespace(action="cancel", data=None)


@pytest.fixture(autouse=True)
def no_session_save():
    """Prevent real keyring writes during auth tests."""
    with patch("monarch_mcp_server.auth.secure_session") as mock:
        yield mock


class TestLoginInteractive:
    def test_happy_path_no_mfa(self, no_session_save):
        mm = AsyncMock()
        with patch(
            "monarch_mcp_server.auth.login_with_current_auth",
            AsyncMock(return_value=mm),
        ) as login:
            ctx = make_ctx(accept(email="a@b.com", password="pw"))
            result = asyncio.run(auth.login_interactive(ctx))
        assert "Logged in" in result
        # Routed through the compat layer (sets trusted_device, headers, etc.).
        login.assert_awaited_once_with("a@b.com", "pw")
        no_session_save.save_authenticated_session.assert_called_once_with(mm)

    def test_mfa_required(self, no_session_save):
        mm = AsyncMock()
        login = AsyncMock(side_effect=[RequireMFAException("mfa"), mm])
        with patch("monarch_mcp_server.auth.login_with_current_auth", login):
            ctx = make_ctx(
                accept(email="a@b.com", password="pw"),
                accept(mfa_code="123456"),
            )
            result = asyncio.run(auth.login_interactive(ctx))
        assert "Logged in" in result
        # Retried with the MFA code (and no email OTP).
        assert login.await_args_list[-1].args == ("a@b.com", "pw")
        assert login.await_args_list[-1].kwargs == {
            "email_otp": None,
            "mfa_code": "123456",
        }
        no_session_save.save_authenticated_session.assert_called_once_with(mm)

    def test_email_otp_required(self, no_session_save):
        mm = AsyncMock()
        login = AsyncMock(side_effect=[EmailOtpRequiredException(), mm])
        with patch("monarch_mcp_server.auth.login_with_current_auth", login):
            ctx = make_ctx(
                accept(email="a@b.com", password="pw"),
                accept(email_otp="000111"),
            )
            result = asyncio.run(auth.login_interactive(ctx))
        assert "Logged in" in result
        assert login.await_args_list[-1].kwargs == {"email_otp": "000111"}
        no_session_save.save_authenticated_session.assert_called_once_with(mm)

    def test_email_otp_then_mfa(self, no_session_save):
        mm = AsyncMock()
        login = AsyncMock(
            side_effect=[EmailOtpRequiredException(), RequireMFAException("mfa"), mm]
        )
        with patch("monarch_mcp_server.auth.login_with_current_auth", login):
            ctx = make_ctx(
                accept(email="a@b.com", password="pw"),
                accept(email_otp="000111"),
                accept(mfa_code="123456"),
            )
            result = asyncio.run(auth.login_interactive(ctx))
        assert "Logged in" in result
        assert login.await_args_list[-1].kwargs == {
            "email_otp": "000111",
            "mfa_code": "123456",
        }
        no_session_save.save_authenticated_session.assert_called_once_with(mm)

    def test_captcha_returns_cookie_hint(self, no_session_save):
        login = AsyncMock(side_effect=CaptchaRequiredException("blocked"))
        with patch("monarch_mcp_server.auth.login_with_current_auth", login):
            ctx = make_ctx(accept(email="a@b.com", password="pw"))
            result = asyncio.run(auth.login_interactive(ctx))
        assert "CAPTCHA" in result
        assert "login_setup.py" in result
        no_session_save.save_authenticated_session.assert_not_called()

    def test_login_failure_reported(self, no_session_save):
        login = AsyncMock(side_effect=Exception("bad credentials"))
        with patch("monarch_mcp_server.auth.login_with_current_auth", login):
            ctx = make_ctx(accept(email="a@b.com", password="pw"))
            result = asyncio.run(auth.login_interactive(ctx))
        assert "Login failed" in result
        assert "bad credentials" in result
        no_session_save.save_authenticated_session.assert_not_called()

    def test_user_cancels_initial_form(self, no_session_save):
        ctx = make_ctx(cancel())
        result = asyncio.run(auth.login_interactive(ctx))
        assert result == "Login cancelled."
        no_session_save.save_authenticated_session.assert_not_called()

    def test_user_cancels_mfa(self, no_session_save):
        login = AsyncMock(side_effect=RequireMFAException("mfa"))
        with patch("monarch_mcp_server.auth.login_with_current_auth", login):
            ctx = make_ctx(accept(email="a@b.com", password="pw"), cancel())
            result = asyncio.run(auth.login_interactive(ctx))
        assert result == "Login cancelled."
        no_session_save.save_authenticated_session.assert_not_called()


class TestLoginWithTokenInteractive:
    def test_happy_path(self, no_session_save):
        mm = AsyncMock()
        with patch("monarch_mcp_server.auth.MonarchMoney", return_value=mm):
            ctx = make_ctx(accept(token="raw-token"))
            result = asyncio.run(auth.login_with_token_interactive(ctx))
        assert "saved" in result.lower()
        mm.get_subscription_details.assert_awaited_once()
        no_session_save.save_token.assert_called_once_with("raw-token")

    def test_strips_whitespace(self, no_session_save):
        mm = AsyncMock()
        with patch("monarch_mcp_server.auth.MonarchMoney", return_value=mm):
            ctx = make_ctx(accept(token="  token-with-spaces  "))
            asyncio.run(auth.login_with_token_interactive(ctx))
        no_session_save.save_token.assert_called_once_with("token-with-spaces")

    def test_empty_token_rejected(self, no_session_save):
        ctx = make_ctx(accept(token="   "))
        result = asyncio.run(auth.login_with_token_interactive(ctx))
        assert "Empty" in result
        no_session_save.save_token.assert_not_called()

    def test_user_cancels(self, no_session_save):
        ctx = make_ctx(cancel())
        result = asyncio.run(auth.login_with_token_interactive(ctx))
        assert result == "Login cancelled."
        no_session_save.save_token.assert_not_called()


class TestLogout:
    def test_clears_session(self, no_session_save):
        result = asyncio.run(auth.logout())
        assert "Cleared" in result
        no_session_save.delete_token.assert_called_once()


class TestDebugSessionLoading:
    def test_no_token_message(self):
        from monarch_mcp_server.tools import auth as tools_auth

        with patch(
            "monarch_mcp_server.tools.auth.secure_session.load_token",
            return_value=None,
        ):
            result = asyncio.run(tools_auth.debug_session_loading())
        assert "No token" in result

    def test_token_present_does_not_leak_length(self):
        from monarch_mcp_server.tools import auth as tools_auth

        with patch(
            "monarch_mcp_server.tools.auth.secure_session.load_token",
            return_value="a-secret-token-value",
        ):
            result = asyncio.run(tools_auth.debug_session_loading())
        assert "Token found" in result
        assert "length" not in result.lower()
        assert "a-secret-token-value" not in result

    def test_keyring_failure_omits_traceback(self):
        from monarch_mcp_server.tools import auth as tools_auth

        with patch(
            "monarch_mcp_server.tools.auth.secure_session.load_token",
            side_effect=RuntimeError("keyring backend unavailable"),
        ):
            result = asyncio.run(tools_auth.debug_session_loading())
        assert "Keyring access failed" in result
        assert "RuntimeError" in result
        assert "keyring backend unavailable" in result
        assert "Traceback" not in result
        assert 'File "' not in result


class TestElicitNotSupported:
    """Older MCP SDKs (<1.10) do not expose Context.elicit."""

    def test_login_interactive_returns_upgrade_hint(self, no_session_save):
        ctx = SimpleNamespace()  # no elicit attribute
        result = asyncio.run(auth.login_interactive(ctx))
        assert "1.10" in result
        assert "login_setup.py" in result
        no_session_save.save_authenticated_session.assert_not_called()

    def test_login_with_token_returns_upgrade_hint(self, no_session_save):
        ctx = SimpleNamespace()
        result = asyncio.run(auth.login_with_token_interactive(ctx))
        assert "1.10" in result
        no_session_save.save_token.assert_not_called()
