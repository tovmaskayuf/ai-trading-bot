# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Git workflow — commit and push as you go

**Commit each logical unit of work as soon as it is complete, and push to `origin` in the same pass.** Do not batch a session's work into one commit at the end, and do not leave finished work sitting uncommitted — the point is that there is always a restore point and the session's state survives even if the conversation is lost.

Repository: `tovmaskayuf/ai-trading-bot` on GitHub, branch `main`, HTTPS remote (`gh` is authenticated).

Practical rules:
- Commit when a module, a fix, or a coherent slice of a feature works — not per file, not per session.
- Run the indicator tests before committing anything under `analytics/`.
- Subject line: imperative mood, under ~72 chars, describing what changed. Body: explain **why** — the constraint, tradeoff, or upstream API quirk that motivated it. Existing commits set the standard; match them.
- Push after committing. `git status -sb` showing `## main...origin/main` with no ahead marker means synced.
- Never commit `.venv/`, `data/`, or `.claude/settings.local.json` — `.gitignore` covers these.

Be aware that files may appear or change between your tool calls: this project has been developed with parallel edits happening outside your own writes. Re-check `git status` before assuming your view of the tree is current, and never force-push or discard work you did not write.

## Environment

Python 3.14 in a local venv. Dependencies were installed ad hoc — there is **no `requirements.txt`**, and no pytest/ruff/black/mypy. Installed: `fastapi`, `uvicorn`, `httpx`, `pydantic`.

```bash
.venv/bin/python tests/test_indicators.py    # test suite — exits 1 on failure
.venv/bin/python -c "import config, db; from providers import binance"   # import smoke check
```

The test suite is a **plain script, not pytest** — it prints PASS/FAIL per assertion and exits non-zero if any fail. Add cases by calling `check(name, got, want, tol)` at module level. There is no way to run a single test in isolation; the file is fast enough that this doesn't matter.

Always run from the project root — `tests/test_indicators.py` inserts the parent directory onto `sys.path` itself, but the provider and analytics imports assume root-relative resolution.

**The directory name contains `}{`** (`ai_trading}{bot`). Braces break unquoted shell paths — always quote it in `cd`, `find`, and redirects. This is why the GitHub repo is named `ai-trading-bot` instead; GitHub rejects brace characters.

## Current state

Built and committed:

| Layer | Files | Status |
|---|---|---|
| Config / asset registry | `config.py` | Done |
| Persistence | `db.py` | Done |
| Providers | `providers/{base,binance,hyperliquid,coingecko}.py` | Done |
| Indicators | `analytics/indicators.py` + tests | Done, tests passing |
| Rating engine | `analytics/rating.py` | Done, **no tests yet** |

Not yet written:
- **Polling engine** — the cycle loop that drives `CYCLE_SECONDS`, calls providers on their staggered cadences, writes snapshots/candles, invokes `rate_asset`, and persists ratings.
- **Paper-trading strategy** — consumes signals and the `RISK_PER_TRADE` / `MAX_POSITION_PCT` / ATR-stop parameters in `config.py`; writes `positions`, `trades`, `equity`. The DB schema and every tuning constant already exist for this; only the logic is missing.
- **FastAPI server + dashboard** — `fastapi`/`uvicorn` are installed and `config.STATIC_DIR` is referenced, but neither the app nor `static/` exists.

`data/` does not exist in the tree; `db.connect()` creates it on first call.

## Architecture

### Asset registry drives everything

**`config.py` is the single source of truth for asset routing.** The `ASSETS` registry maps each internal symbol (`BTC`) onto per-provider identifiers (`binance_symbol="BTCUSDT"`, `coingecko_id="bitcoin"`, `hyperliquid_coin`). Provider selection derives from which identifiers are populated, via `Asset.price_source`. No module should branch on a symbol name directly — to add an asset, add a row and nothing else changes.

HYPE is the asset that exercises this indirection: not listed on Binance spot (`HYPEUSDT` → error `-1121`), so it carries `hyperliquid_coin` instead and routes to Hyperliquid. Any code assuming "all assets are on Binance" breaks on HYPE specifically.

### Providers

Stateless async modules over one shared `httpx.AsyncClient` (`providers/base.py`). Each returns dicts keyed by **internal symbol**, never by exchange pair — pair→symbol translation belongs in the provider, not the caller.

Retry policy in `base.request` is deliberate: transport errors, 429 and 5xx retry with exponential backoff; any other 4xx raises `ProviderError(permanent=True)` and fails immediately, because retrying a bad symbol only burns rate-limit budget.

The three providers have genuinely different shapes:
- **Binance** — primary price/candles for 14 of 15. Batches all pairs into one call (`tickers_24h`, `book_tickers`); explicit `limit` on klines.
- **Hyperliquid** — single POST URL discriminated by a `type` field. `allMids` returns ~900 coins and gives **price only**, no 24h change or volume, so callers must backfill from candles or CoinGecko. Its candle endpoint has no `limit`; the window is a time range derived from interval size × count.
- **CoinGecko** — market cap, volume and 24h change for all 15 in one `/simple/price` call, plus the price fallback when Binance is down. Its `rank` is computed **within our own 15-asset basket**, not CoinGecko's global rank — a basket-relative rank is what the structure score actually wants, and the global rank needs a heavier endpoint.

### Storage

**SQLite in WAL mode**, so the polling engine can write while the HTTP server reads concurrently. One process-wide connection, created lazily with `check_same_thread=False`; schema applied idempotently on first `connect()`. All timestamps are **epoch milliseconds UTC** to match the upstream APIs — never seconds.

Writers are upsert-shaped: `upsert_candles` rewrites the in-progress candle on every refresh, so refetching an overlapping window is safe and idempotent.

`prune()` drops only `snapshots` and `ratings` past `RETENTION_DAYS`; candles, trades and equity are kept indefinitely. `reset_portfolio()` wipes simulated trading state but preserves market data.

### Indicators

`analytics/indicators.py` is **deliberately dependency-free** — plain Python lists, no numpy/pandas. 15 assets × a few hundred candles is trivial compute, and avoiding compiled wheels keeps installs working on new Python releases (relevant here: this is Python 3.14). Do not introduce numpy to this module.

Every function **returns `None` on insufficient data rather than raising**, so a cold start degrades gracefully instead of crashing the engine. Preserve this contract in new indicators — callers rely on it throughout `rating.py`.

`scale(value, lo, hi, invert=False)` is the shared primitive mapping raw values onto 0–100 with clamping; `invert=True` is for metrics where smaller is better. `pct_rank` gives percentile standing within a population.

### Rating engine

`analytics/rating.py` scores four axes, each 0–100, then composes them.

**Sub-scores are stored raw** (see the `ratings` table) so the dashboard can recombine them under user-chosen weights without a server round-trip. Composite, grade and signal are always derived, never stored inputs.

- **Momentum** — RSI on 1h and synthetic 4h (`closes[::-1][::4][::-1]`, reversed twice to keep the *most recent* bar aligned rather than the oldest), MACD histogram sign plus slope, EMA 20/50/200 stacking, and range position. RSI above ~70 is scored *down*, not up — overbought is a risk, not strength. A timeframe-agreement bonus applies when 1h and 4h RSI lean the same way.
- **Risk** — ATR%, realized vol and max drawdown are **percentile-ranked against the basket then inverted**, so scores stay meaningful regardless of market regime. Sharpe is the exception: scored absolutely, since it is already normalised. Higher score = lower risk.
- **Structure** — basket rank, turnover (volume/mcap, where both very low and extreme readings score poorly), 24h volume vs the 7d baseline, and bid/ask spread.
- **Relative** — returns vs BTC and percentile within the basket across 24h/7d/30d. The benchmark scores neutral against itself by definition. A "rotating in" bonus fires when short-horizon standing exceeds long-horizon by >20 points.

`composite_score` drops missing axes and **renormalises the remaining weights**, so an asset with partial data still rates instead of returning `None`.

Each scorer returns `(score, detail)` where `detail` is the drill-down payload the dashboard renders. Keep populating it — it is the only explanation of *why* a rating moved.

## Tuning constraints

These encode reasoning not obvious from the numbers:

- **Cadence** — `CYCLE_SECONDS = 120` is the base tick. Candles (`KLINE_EVERY_N_CYCLES`) and market cap (`MARKET_EVERY_N_CYCLES`) refresh on multiples of it specifically to stay inside free-tier rate limits. Lowering these risks 429s.
- **`DEFAULT_WEIGHTS` must sum to 1.0** across momentum/risk/structure/relative.
- **The gap between `BUY_THRESHOLD` (70) and `EXIT_THRESHOLD` (45) is a hysteresis dead band**, not two independent knobs. Ratings recompute every 2 minutes; narrowing the gap makes a composite hovering near one threshold churn positions almost every cycle. `MIN_HOLD_MINUTES` backstops the same problem in the time dimension, and `signal_for` implements the band.
- `CANDLE_LIMIT = 300` is sized for EMA200 plus 30-day statistics — shrinking it silently degrades those indicators.
- `score_momentum` needs ≥60 closes and `risk_metrics` ≥30, so a cold start produces `None` axes until enough candles accumulate. This is expected, not a bug.

## Known issues

- `rating.py:288` — in `signal_for`, the final `if holding or prev_signal in (...)` branch and the fallthrough both return `"HOLD"`, so the condition is dead code. Harmless today, but it likely intended to distinguish holding from neutral. Resolve before the strategy layer starts depending on signal semantics.
- `analytics/rating.py` has no test coverage. `test_indicators.py` covers only the indicator primitives.
