#!/usr/bin/env python3
"""
Standalone script to perform interactive Monarch Money login.

Supports three auth paths in order of recommendation:

1. Session cookies pasted from a logged-in browser. Long-lived, works
   for all account types including SSO, sidesteps Cloudflare CAPTCHA.
2. Email and password (with optional email OTP and MFA prompts). Now
   requests a long-lived session token from Monarch.
3. Legacy session token paste. Kept for users with a working token
   captured under the old auth model.
"""

import asyncio
import getpass
import os
import sys
from pathlib import Path

src_path = Path(__file__).parent / "src"
sys.path.insert(0, str(src_path))

# Load a local .env (e.g. MONARCH_COOKIE=...) if python-dotenv is available.
# It is a declared dependency, but guard the import so the script still runs
# under a bare interpreter without it.
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

from monarchmoney import CaptchaRequiredException, RequireMFAException

from monarch_mcp_server.monarch_auth import (
    EmailOtpRequiredException,
    create_monarch_client,
    login_with_browser_cookies,
    login_with_current_auth,
)
from monarch_mcp_server.secure_session import secure_session


def _read_cookie_string():
    """Read the cookie header, avoiding the terminal's line-length limit.

    A full Monarch ``cookie:`` header is often 1.5-3 KB, but macOS caps
    canonical-mode terminal input at MAX_CANON (1024 bytes) per line, so
    pasting the cookie into a ``getpass``/``input`` prompt silently
    truncates it and swallows the Enter keypress. Reading from an env var
    or a file sidesteps that entirely. Direct paste is kept only as a
    last resort for short inputs.
    """
    env_cookie = os.environ.get("MONARCH_COOKIE", "").strip()
    if env_cookie:
        print("📎 Using cookie from MONARCH_COOKIE (.env or environment).")
        return env_cookie

    print("\n📋 To copy the right cookie string:")
    print("  1. Log in to https://app.monarch.com in Chrome or Firefox")
    print("  2. Open DevTools (F12) → Network tab")
    print("  3. Click any request whose Name starts with 'graphql'")
    print("     (or any request to api.monarch.com)")
    print("  4. Scroll to 'Request Headers' and find the 'cookie:' header")
    print("  5. Copy the full value (a long string of key=value; pairs)")
    print()
    print(
        "⚠️  On macOS the terminal truncates pasted lines longer than 1024 "
        "bytes, and the cookie is usually longer than that. So save the "
        "cookie to a file and enter its path here instead of pasting it."
    )
    print("   e.g.  pbpaste > ~/monarch-cookie.txt   (after copying the value)")
    print()
    path = input("Path to file containing the cookie: ").strip()
    if path:
        path = os.path.expanduser(path)
        try:
            cookie_string = Path(path).read_text().strip()
        except OSError as e:
            print(f"❌ Could not read {path}: {e}")
            return None
        if not cookie_string:
            print(f"❌ File {path} is empty. Exiting.")
            return None
        return cookie_string

    # Fallback: direct paste (only reliable for cookies under ~1024 bytes).
    cookie_string = getpass.getpass(
        "No path given; paste the Cookie header value instead: "
    ).strip()
    if not cookie_string:
        print("❌ No cookie provided. Exiting.")
        return None
    return cookie_string


async def _login_with_cookies():
    cookie_string = _read_cookie_string()
    if not cookie_string:
        return None
    try:
        mm = await login_with_browser_cookies(cookie_string)
        print("✅ Cookie login successful")
        return mm
    except Exception as e:
        print(f"❌ Cookie login failed: {e}")
        return None


async def _login_with_password():
    email = input("Email: ").strip()
    password = getpass.getpass("Password: ")
    try:
        mm = await login_with_current_auth(email, password)
        print("✅ Login successful")
        return mm
    except CaptchaRequiredException as e:
        print(f"❌ {e}")
        print("Re-run this script and choose option 1 (session cookies).")
        return None
    except EmailOtpRequiredException:
        print("📧 Monarch sent a verification code to your email.")
        code = input("Email verification code: ").strip()
        if not code:
            print("❌ No code provided. Exiting.")
            return None
        try:
            mm = await login_with_current_auth(email, password, email_otp=code)
        except RequireMFAException:
            mfa_code = input("Two Factor Code: ").strip()
            mm = await login_with_current_auth(
                email, password, email_otp=code, mfa_code=mfa_code
            )
        print("✅ Email verification successful")
        return mm
    except RequireMFAException:
        mfa_code = input("Two Factor Code: ").strip()
        if not mfa_code:
            print("❌ No MFA code provided. Exiting.")
            return None
        mm = await login_with_current_auth(email, password, mfa_code=mfa_code)
        print("✅ MFA authentication successful")
        return mm


def _login_with_legacy_token():
    print("\n📋 To get a legacy session token:")
    print("  1. Log in to https://app.monarch.com in Chrome or Firefox")
    print("  2. DevTools (F12) → Application tab → Local Storage")
    print("     → https://app.monarch.com → key 'token'")
    print("  3. Copy the value")
    print()
    print(
        "⚠️  Monarch may no longer accept Authorization: Token auth on the "
        "GraphQL endpoint. If the test call below fails with 401, re-run "
        "this script and choose option 1 (cookies) instead."
    )
    token = getpass.getpass("Paste your session token: ").strip()
    if not token:
        print("❌ No token provided. Exiting.")
        return None
    mm = create_monarch_client(token=token)
    print("✅ Token configured")
    return mm


async def main():
    print("\n🏦 Monarch Money - Claude Desktop Setup")
    print("=" * 45)
    print("This will authenticate you once and save a session")
    print("for seamless access through Claude Desktop.\n")

    try:
        import monarchmoney

        print(
            f"📦 MonarchMoney version: "
            f"{getattr(monarchmoney, '__version__', 'unknown')}"
        )
    except Exception as e:
        print(f"⚠️  Could not check version: {e}")

    try:
        secure_session.delete_token()
        print("🗑️ Cleared existing secure sessions")

        print("\nHow do you sign in to Monarch Money?")
        print(
            "  1) Session cookies from browser   "
            "(recommended: long-lived, supports SSO)"
        )
        print("  2) Email and password")
        print("  3) Legacy session token paste")
        choice = input("Choice [1]: ").strip() or "1"

        mm = None
        if choice == "1":
            mm = await _login_with_cookies()
        elif choice == "2":
            mm = await _login_with_password()
        elif choice == "3":
            mm = _login_with_legacy_token()
        else:
            print(f"❌ Unrecognized choice: {choice!r}. Exiting.")
            return

        if mm is None:
            return

        print("\nTesting connection...")
        try:
            accounts = await mm.get_accounts()
            if accounts and isinstance(accounts, dict):
                account_count = len(accounts.get("accounts", []))
                print(f"✅ Found {account_count} accounts")
            else:
                print(f"❌ Unexpected accounts response: {type(accounts)}")
                return
        except Exception as test_error:
            print(f"❌ Connection test failed: {test_error}")
            print(f"Error type: {type(test_error).__name__}")
            print(
                "\nIf this looks like 401 Unauthorized, the cookie or token "
                "is invalid. Re-run this script."
            )
            return

        try:
            print("\n🔐 Saving session securely to system keyring...")
            secure_session.save_authenticated_session(mm)
            print("✅ Session saved")
        except Exception as save_error:
            print(f"❌ Could not save session: {save_error}")
            return

        print("\n🎉 Setup complete. Restart Claude Desktop to pick up the session.")
        print("\n💡 Useful tools in Claude:")
        print("   • get_accounts - View all your accounts")
        print("   • get_transactions - Recent transactions")
        print("   • get_budgets - Budget information")
        print("   • get_cashflow - Income/expense analysis")

    except Exception as e:
        print(f"\n❌ Login failed: {e}")
        print(f"Error type: {type(e).__name__}")


if __name__ == "__main__":
    asyncio.run(main())
