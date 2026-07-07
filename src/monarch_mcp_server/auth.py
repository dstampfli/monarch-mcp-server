"""Interactive authentication for the Monarch Money MCP server.

Uses MCP elicitation so credentials flow client-UI → server directly over
the protocol — they never appear in tool arguments or the model's context.
"""

from __future__ import annotations

from typing import Optional

from mcp.server.fastmcp import Context
from monarchmoney import CaptchaRequiredException, MonarchMoney, RequireMFAException
from pydantic import BaseModel, Field

from monarch_mcp_server.monarch_auth import (
    EmailOtpRequiredException,
    login_with_current_auth,
)
from monarch_mcp_server.secure_session import secure_session


_UPGRADE_HINT = (
    "Elicitation requires the MCP Python SDK >= 1.10.0 (added in June 2025). "
    "Your MCP server install appears to be running an older version that does "
    "not expose Context.elicit. Upgrade the `mcp` package, then restart your "
    "MCP client. If you launch via `uv run --with mcp[cli]`, run `uv cache "
    "clean mcp` first so a fresh version is resolved. As a fallback, run "
    "`python login_setup.py` from the repo to authenticate via terminal."
)


def _elicit_supported(ctx: Context) -> bool:
    return hasattr(ctx, "elicit")


class LoginForm(BaseModel):
    email: str = Field(description="Monarch Money email address")
    password: str = Field(description="Monarch Money password")


class MFAForm(BaseModel):
    mfa_code: str = Field(description="Monarch Money MFA code")


class EmailOtpForm(BaseModel):
    email_otp: str = Field(
        description="The verification code Monarch emailed to your account"
    )


class TokenForm(BaseModel):
    token: str = Field(
        description=(
            "Monarch Money session token. Grab it from browser DevTools → "
            "Application → Local Storage for app.monarchmoney.com, key 'token'."
        ),
    )


async def _login_with_mfa(
    ctx: Context,
    email: str,
    password: str,
    *,
    email_otp: Optional[str] = None,
) -> Optional[MonarchMoney]:
    """Elicit an MFA code and complete login, or None if the user cancels."""
    mfa_result = await ctx.elicit(
        message="Enter your Monarch Money MFA code.", schema=MFAForm
    )
    if mfa_result.action != "accept":
        return None
    return await login_with_current_auth(
        email, password, email_otp=email_otp, mfa_code=mfa_result.data.mfa_code
    )


async def login_interactive(ctx: Context) -> str:
    if not _elicit_supported(ctx):
        return _UPGRADE_HINT
    form_result = await ctx.elicit(message="Sign in to Monarch Money.", schema=LoginForm)
    if form_result.action != "accept":
        return "Login cancelled."
    email = form_result.data.email
    password = form_result.data.password

    # Route through login_with_current_auth so the login payload sets
    # trusted_device=True (long-lived token), injects the current web headers,
    # captures the device-uuid needed to reload the session, and rejects
    # JWT/short-lived tokens — none of which a raw MonarchMoney().login() does.
    mm: Optional[MonarchMoney] = None
    try:
        try:
            mm = await login_with_current_auth(email, password)
        except EmailOtpRequiredException:
            otp_result = await ctx.elicit(
                message="Monarch emailed you a verification code. Enter it "
                "(this can happen for a new session even with MFA off).",
                schema=EmailOtpForm,
            )
            if otp_result.action != "accept":
                return "Login cancelled."
            email_otp = otp_result.data.email_otp
            try:
                mm = await login_with_current_auth(
                    email, password, email_otp=email_otp
                )
            except RequireMFAException:
                mm = await _login_with_mfa(
                    ctx, email, password, email_otp=email_otp
                )
        except RequireMFAException:
            mm = await _login_with_mfa(ctx, email, password)
    except CaptchaRequiredException:
        return (
            "Programmatic login is blocked by Cloudflare CAPTCHA. Run "
            "`python login_setup.py` from the repo and choose the browser-cookie "
            "option instead."
        )
    except Exception as e:  # LoginFailedException, network errors, etc.
        return f"Login failed: {e}"

    if mm is None:
        return "Login cancelled."

    secure_session.save_authenticated_session(mm)
    return "Logged in. Session saved to system keyring."


async def login_with_token_interactive(ctx: Context) -> str:
    if not _elicit_supported(ctx):
        return _UPGRADE_HINT
    form_result = await ctx.elicit(
        message="Paste your Monarch Money session token.", schema=TokenForm
    )
    if form_result.action != "accept":
        return "Login cancelled."

    token = form_result.data.token.strip()
    if not token:
        return "Empty token — aborting."

    mm = MonarchMoney(token=token)
    await mm.get_subscription_details()
    secure_session.save_token(token)
    return "Session token saved to system keyring."


async def logout() -> str:
    secure_session.delete_token()
    return "Cleared stored Monarch session."
