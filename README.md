# AI Crypto Trading Bot

A continuously-running bot that tracks 15 cryptocurrencies, rates each one across
four analytical axes every two minutes, and trades a **simulated** portfolio off
those ratings. Ships with an interactive browser dashboard.

**Paper trading only.** No exchange account, no API keys, no real money. The
portfolio is virtual capital marked against live prices.

## Quick start

```bash
.venv/bin/python -m uvicorn server:app --port 8000
```

Then open <http://localhost:8000>. One process runs both the polling engine and
the dashboard.

### Run it always-on in the background

```bash
nohup .venv/bin/python -m uvicorn server:app --port 8000 > bot.log 2>&1 &
```

The engine keeps polling and building rating history whether or not the
dashboard is open. State lives in `data/bot.db`, so restarts resume rather
than reset.

## Tracked assets

BTC · ETH · SOL · BNB · XRP · LINK · SUI · AVAX · TRX · ADA · ARB · ONDO · TAO ·
HYPE · DOGE

Routing is driven entirely by the registry in `config.py` — to add an asset, add
a row, and nothing else changes.

| Source | Role |
|---|---|
| Binance public REST | Price, 24h stats, OHLC candles, order book — 14 of 15 |
| Hyperliquid public API | **HYPE only** — it is not listed on Binance spot (`HYPEUSDT` → `-1121`) |
| CoinGecko free | Market cap, rank, global volume; also the price fallback |

No API keys are required for any of them.

## The rating system

Each asset gets four sub-scores from 0–100, blended into a composite, a letter
grade (A+ → F) and a signal.

| Axis | Default weight | What it measures |
|---|---|---|
| **Momentum** | 30% | RSI (1h + 4h), MACD histogram and slope, EMA 20/50/200 stacking, range position |
| **Risk** | 25% | ATR%, realized volatility, max drawdown, Sharpe — *inverted*, so lower risk scores higher |
| **Structure** | 25% | Basket rank, turnover, volume trend, bid/ask spread |
| **Relative** | 20% | Returns vs BTC and percentile within the basket, across 24h/7d/30d |

Several axes are scored **relative to the basket** rather than against fixed
thresholds. That is deliberate: absolute bands are regime-dependent, and in a
quiet market every asset's raw turnover sits at the bottom of any fixed range,
collapsing the axis and dragging every composite down with it.

Sub-scores are stored raw, so the dashboard's weight sliders recompute the
composite in the browser with no server round-trip. That client-side formula is
cross-checked against `analytics/rating.py` — both must agree exactly.

### Signals and the dead band

`BUY` requires the composite to cross **above 70**; exit requires it to fall to
**45**. The gap between them is a hysteresis dead band, not two independent
knobs — ratings recompute every two minutes, and a single threshold would make
any asset hovering near it churn positions on nearly every cycle.
`MIN_HOLD_MINUTES` backstops the same problem in the time dimension.

The regression test for this feeds a composite oscillating around the buy
threshold for 60 cycles and asserts it produces **1 trade, not 60**.

## Two portfolios

There are two independent $100,000 virtual portfolios, both marked against the
same live prices:

- **My Portfolio** — you trade it. Click any coin, buy with a dollar amount or
  sell part of a holding, and watch P&L move.
- **Bot** — the algorithm trades it automatically off its own signals.

They are deliberately separate so you can see whether the bot actually beats you
over the same period. Resetting one does not touch the other.

Manual trading uses an **average cost basis**: buying more of something you
already hold averages into one line rather than opening a second lot, and
selling books realised P&L against that average. Fees (0.1% per side) apply to
both portfolios, so a flat round-trip loses exactly the fees rather than
breaking even.

## Bot paper trading

- $100,000 virtual starting capital
- **Sizing:** risk 2% of equity per trade, quantity derived from the ATR stop
  distance, so a volatile asset gets a smaller position for the same dollar risk
- **Exits:** 2×ATR stop-loss, 3×ATR take-profit, a trailing stop that only ever
  ratchets up, or a rating-driven exit below 45
- **Costs:** 0.1% fee per side plus 5bp slippage, so P&L isn't fantasy
- Every trade records *why* the bot took it

> `MAX_POSITION_PCT × MAX_OPEN_POSITIONS` must stay ≤ 1.0. Otherwise cash runs
> out before the position limit is reached and the book silently caps below
> `MAX_OPEN_POSITIONS`. There is a test asserting this invariant.

## Tests

```bash
.venv/bin/python tests/test_indicators.py   # indicator correctness
.venv/bin/python tests/test_strategy.py     # trading safety rules
```

Both are plain scripts, not pytest — they print PASS/FAIL per assertion and exit
non-zero on failure. Run from the project root.

## API

| Endpoint | Returns |
|---|---|
| `GET /api/overview` | All 15 with prices, sub-scores, composites, signals |
| `GET /api/asset/{symbol}` | Candles, rating history, indicator drill-down, position |
| `GET /api/portfolio` | Bot stats, open positions, trade log, equity curve |
| `GET /api/manual` | Your holdings, cash, P&L and trade history |
| `POST /api/manual/trade` | Buy (`usd` or `qty`) / sell (`qty` or `fraction`) |
| `POST /api/weights` | Re-score under custom axis weights |
| `GET /api/stream` | SSE — one message per completed cycle |
| `POST /api/portfolio/reset` | Wipe the bot's trading state (market data preserved) |
| `POST /api/manual/reset` | Wipe your portfolio back to $100k |
| `GET /api/health` | Engine liveness and cycle count |

Trade prices are always taken server-side from the latest cycle, so a stale
browser tab cannot fill at an old quote.

## Layout

```
config.py               asset registry, weights, thresholds, capital
db.py                   SQLite (WAL mode) schema and helpers
providers/              binance · hyperliquid · coingecko, behind one interface
analytics/indicators.py EMA, RSI, MACD, ATR, vol, drawdown, Sharpe — no numpy
analytics/rating.py     four axes → composite → grade → signal
trading/                portfolio accounting and the strategy rules
engine.py               the 120s polling loop
server.py               FastAPI: REST + SSE + dashboard
static/dashboard.html   the whole UI, one self-contained file
```

## Notes

- Indicators are **deliberately dependency-free** — plain Python lists, no
  numpy/pandas. 15 assets × a few hundred candles is trivial compute, and this
  keeps installs working on new Python releases where compiled wheels lag.
- Indicator functions return `None` on insufficient data rather than raising, so
  a cold start degrades gracefully. `score_momentum` needs ≥60 closes and
  `risk_metrics` ≥30, so some axes read `—` until enough candles accumulate.
  That is expected, not a bug.
- The directory name contains `}{`, which breaks unquoted shell paths — always
  quote it. (The GitHub repo is `ai-trading-bot`; GitHub rejects braces.)
