# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Environment

Python 3.14 with a local venv. There is no `requirements.txt`, no test runner, and no linter installed — dependencies were installed ad hoc into `.venv` (`fastapi`, `uvicorn`, `httpx`, `pydantic`).

Version control: git, with `origin` on GitHub. Commit each logical change as you go and push, so there is always a restore point.

```bash
.venv/bin/python -c "import config, db; from providers import binance"   # smoke-check imports
```

Always run from the project root. `providers/` has no `__init__.py` and resolves as a namespace package, so `from providers.base import get` only works with the root on `sys.path`.

**The directory name contains `}{`** (`ai_trading}{bot`). Brace characters break unquoted shell paths — always quote the path in `cd`, `find`, and redirects.

## Current state

Only the foundation layer exists: `config.py`, `db.py`, and `providers/`. Modules referenced by existing docstrings and config but **not yet written**: the CoinGecko provider (the documented fallback for every Binance asset), the rating engine, the signal/paper-trading strategy, and the FastAPI server. `config.STATIC_DIR` and `config.DB_PATH`'s `data/` directory do not exist yet; `db.connect()` creates `data/` on first call.

## Architecture

**`config.py` is the single source of truth for asset routing.** The `ASSETS` registry maps each internal symbol (`BTC`) onto its per-provider identifiers (`binance_symbol="BTCUSDT"`, `coingecko_id="bitcoin"`, `hyperliquid_coin`). Provider selection is derived from which identifiers are populated — via `Asset.price_source`. No module should branch on a symbol name directly; to add an asset, add a row to `ASSETS` and nothing else changes.

HYPE is the one asset that exercises this indirection: it is not listed on Binance spot (`HYPEUSDT` → error `-1121`), so it carries `hyperliquid_coin` instead of `binance_symbol` and routes to Hyperliquid. Any code that assumes "all assets are on Binance" will break on HYPE specifically.

**Providers are stateless async modules over one shared `httpx.AsyncClient`** (`providers/base.py`). Each returns dicts keyed by *internal* symbol, never by exchange pair — the pair→symbol translation belongs in the provider, not the caller.

Retry policy in `base.request` is deliberate: transport errors, 429, and 5xx retry with exponential backoff; any other 4xx raises `ProviderError(permanent=True)` and fails immediately, because retrying a bad symbol or bad params only burns rate-limit budget.

The two providers differ in shape, which is why they aren't a single abstraction:
- Binance batches all pairs into one call (`tickers_24h`, `book_tickers`) and takes an explicit `limit` on klines.
- Hyperliquid is a single POST URL discriminated by a `type` field. `allMids` returns ~900 coins and gives **price only** — no 24h change or volume, so callers must backfill those from candles or CoinGecko. Its candle endpoint has no `limit`; the window is a time range derived from interval size × requested count.

**`db.py` — SQLite in WAL mode**, so the polling engine can write while the HTTP server reads concurrently. One process-wide connection created lazily with `check_same_thread=False`; schema is applied idempotently on first `connect()`. All timestamps are **epoch milliseconds UTC** to match the upstream APIs — never seconds.

Writers are all upsert-shaped: `upsert_candles` rewrites the in-progress candle on every refresh, so refetching an overlapping window is safe and idempotent.

`prune()` drops only `snapshots` and `ratings` past `RETENTION_DAYS`; candles, trades, and equity are kept indefinitely. `reset_portfolio()` wipes simulated trading state but preserves market data.

## Tuning constraints

These values encode reasoning that isn't obvious from the numbers:

- **Cadence** — `CYCLE_SECONDS = 120` is the base tick. Candles (`KLINE_EVERY_N_CYCLES`) and market cap (`MARKET_EVERY_N_CYCLES`) refresh on multiples of it specifically to stay inside free-tier rate limits. Lowering these risks 429s.
- **`DEFAULT_WEIGHTS` must sum to 1.0** across momentum/risk/structure/relative. The dashboard is intended to override them per-request; these are the defaults the engine persists with.
- **The gap between `BUY_THRESHOLD` (70) and `EXIT_THRESHOLD` (45) is a hysteresis dead band**, not two independent knobs. Ratings recompute every 2 minutes; narrowing the gap makes a composite hovering near one threshold churn positions almost every cycle. `MIN_HOLD_MINUTES` backstops the same problem in the time dimension.
- `CANDLE_LIMIT = 300` is sized for EMA200 plus 30-day statistics — shrinking it silently degrades those indicators.
