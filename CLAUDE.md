# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

An MCP (Model Context Protocol) server exposing Monarch Money personal-finance data (accounts, transactions, budgets, categories, tags, rules, merchants, net worth) as tools for Claude Desktop / Claude Code. It wraps the community `monarchmoney` (`monarchmoneycommunity`) Python library; most tools are thin async pass-throughs to that client, with output normalization.

## Commands

Development uses `uv` (falls back to `pip install -r requirements.txt && pip install -e .`).

- Run all tests: `uv run pytest`
- Run one test file: `uv run pytest tests/test_transactions.py`
- Run one test: `uv run pytest tests/test_transactions.py::test_name`
- Format: `uv run black . && uv run isort .` (line length 88)
- Type check: `uv run mypy src` (strict mode is enabled)
- Run the server locally: `uv run mcp run src/monarch_mcp_server/server.py`
- Authenticate (must be done outside Claude): `uv run python login_setup.py`

Tests use `pytest-asyncio` with `asyncio_mode = "auto"`, so `async def test_*` needs no decorator. There is no network access in tests — see Testing below.

## Architecture

### Tool registration flow
`server.py` is a **backward-compatibility shim** — real code lives elsewhere. The load order that matters:

1. `app.py` creates the singleton `mcp = FastMCP(...)` and then imports `monarch_mcp_server.tools`.
2. `tools/__init__.py` imports every tool submodule, and each `@mcp.tool()` decorator registers against that singleton as a side effect of import. **A new tool module is invisible until added to `tools/__init__.py`.**
3. `server.py` re-exports every public tool name so old imports (`from monarch_mcp_server.server import get_accounts`) and the `mcp run` entry point keep working. When you add a tool, also add it to the `server.py` re-export list and the `_TOOL_MODULES` list in `tests/conftest.py`.

Tools are grouped by domain under `tools/`: `accounts`, `transactions`, `summaries`, `splits`, `tags`, `rules`, `categories`, `budgets`, `financial`, `merchants`, `auth`.

### Client access
Every tool obtains its client via `await get_monarch_client()` (`client.py`), which returns a module-cached, keyring-authenticated `MonarchMoney` instance. Tools never construct clients or trigger logins directly. If no session exists it raises `RuntimeError("Authentication needed! Run: python login_setup.py")`. Call `clear_client_cache()` after re-auth.

### Tool conventions (follow these when adding tools)
- Every tool is `async def`, returns a `str`, and is wrapped in `try/except Exception as e: return json_error("<tool_name>", e)`.
- Use helpers from `helpers.py`: `json_success(data)` / `json_error(name, exc)` for responses, `format_transaction(txn)` to normalize raw transaction dicts, and `tool_response_envelope(...)` for list endpoints. The envelope wraps rows with `tool`, `args`, `count`, `total_count`, `truncated`, `search`, and `data` so agents can tell whether results were truncated without re-asking.
- Writes take **IDs, not names**. There is no name-based matching on mutating tools — the pattern is: a read tool (`get_categories`, `get_tags`, `get_merchant(search=...)`) surfaces the ID, then the write tool consumes it.
- **Set-style mutations clobber omitted fields.** `update_transaction_rule` first reads the existing rule (`GetTransactionRules`) and merges, because `updateTransactionRuleV2` clears any criterion/action left out of the input — a naive single-field update would wipe the rest. Follow this read-merge pattern for any similarly destructive partial update.

### Auth is deliberately split
There are two distinct auth layers — keep them separate:
- `monarch_auth.py` — compatibility shim for Monarch's **May 2026 API change**. It repoints the upstream SDK to `https://api.monarch.com`, injects `device-uuid` / `monarch-client-version` headers, and handles the login-response quirks: email-OTP challenges (even with MFA off), Cloudflare CAPTCHA gating, and short-lived-token rejection. **Critical:** login payloads must set `trusted_device=True` or Monarch returns a 1-hour token that dies mid-session; the code refuses to save any token with a non-null `tokenExpiration` or a JWT shape.
- `secure_session.py` — persistence. Stores the token/cookies in the system keyring, with a file fallback (`~/.monarch-mcp-server/token`) for environments without a keyring backend. The fallback file is created **atomically with mode 0600** (`os.open`, not write-then-chmod, which would leave a world-readable window), and a successful keyring save deletes any stale fallback file. `_keyring_available()` **probes by round-tripping a sentinel value** rather than sniffing the backend class name — do not "simplify" it to a class-name check (macOS Keychain and the no-op fail backend share the class name `Keyring`).
- `tools/auth.py` + `auth.py` — in-client login via MCP elicitation (`ctx.elicit`, requires MCP SDK ≥ 1.10.0). `login_setup.py` at the repo root is the standalone terminal auth script (three paths: browser cookies, email/password, legacy token paste). **Both the elicitation login and the token-paste path go through `monarch_auth`** (`login_with_current_auth` / `create_monarch_client`) so they inherit `trusted_device=True`, the current headers, and device-uuid capture, and reject JWT/short-lived tokens. Never build a raw `MonarchMoney().login()` here — it silently produces a short-lived, device-uuid-less session that breaks on the next restart.

### Where the SDK is worked around
When the upstream `monarchmoney` SDK's query rejects fields Monarch's current API no longer returns, tools issue a narrower raw GraphQL query via `client.gql_call(...)` instead. `tools/budgets.py` (the `MCPBudgetData` query) is the canonical example. When a Monarch API call starts failing on unexpected fields, this narrowing is usually the fix, not a library upgrade.

### wide_search fallback
`get_transactions` (`tools/transactions.py`) accepts `search` plus `wide_search=True`. When Monarch's server-side search errors or returns nothing, `wide_search` pulls a page (`search_scan_limit`) and matches locally across merchant, original statement, description, notes, category, account, and tags. The `search` field in the response envelope records which strategy ran. The scan covers `offset + limit` rows so deep pages aren't missed, and the search info flags `scan_capped` when hitting the cap makes the reported match total a lower bound.

### Client-side filters must paginate
`get_transactions_needing_review` filters (`needsReview`, uncategorized) **client-side** because Monarch exposes no server-side predicate for them. It therefore pages through results until it collects `limit` matches or exhausts the source (bounded by a scan cap), then returns the standard envelope with a `scanned_count`. Applying a client-side filter *after* a single limited fetch silently under-returns — page instead.

## Testing

`tests/conftest.py` inserts a mock `monarchmoney` module into `sys.modules` **before** any `monarch_mcp_server` import, and an autouse fixture patches `get_monarch_client` in every tool module (listed in `_TOOL_MODULES`) to return `mock_monarch_client`. Consequences:
- Tests never hit the network or a keyring.
- A new tool module must be added to `_TOOL_MODULES` in `conftest.py` or its `get_monarch_client` won't be patched.
- Extend `mock_monarch_client` in `conftest.py` with the raw-shaped response your new tool expects (the fixtures mirror Monarch's actual nested GraphQL response shapes, e.g. `allTransactions.results`, `householdTransactionTags`).

## Safety note for mutating tools

Many tools mutate the user's ledger (`create/update/delete_transaction`, `bulk_categorize_transactions`, `set_budget_amount`, `set_transaction_tags`, `*_transaction_rule`, `split_transaction`, `update_merchant`, `review_recurring_stream`, `upload_account_balance_history`). Because the model reads back attacker-influenceable data (merchant names, memos), these should require manual approval in the MCP client. `bulk_categorize_transactions`, `upload_account_balance_history`, and `split_transaction` accept `dry_run=True` to preview changes without executing.
