# AI Crypto Trading and Training by TT

A crypto paper-trading trainer with live market data and AI-powered ratings.
Pick your assets and starting capital on an animated start screen, then trade a
virtual portfolio against real prices — zero risk, real skills.

- **15 assets** — BTC, ETH, SOL, BNB, XRP, LINK, SUI, AVAX, TRX, ADA, ARB,
  ONDO, TAO, HYPE, DOGE — with live prices refreshed **every 60 seconds**
- **Four-axis AI rating** per asset (0–100): Momentum, Risk, Structure,
  Relative Strength — combined into a composite score, letter grade, and signal
- **Paper trading** with an average-cost-basis portfolio, realistic 0.10% fees,
  P&L tracking, and full trade history
- **Interactive charts everywhere** — portfolio value, every stat, every asset —
  each switchable between 1H / 24H / 1W / 1M / 1Y / All
- **Four languages** — English, Հայերեն, Русский, Español
- Light and dark themes, single self-contained web UI, no build step

## Quick start

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m uvicorn server:app --port 8000
```

Open <http://localhost:8000>. The start screen walks you through language,
asset selection, and starting capital; the engine begins collecting live data
immediately. One process runs both the data engine and the web interface.

To keep it running in the background:

```bash
nohup .venv/bin/python -m uvicorn server:app --port 8000 > bot.log 2>&1 &
```

## Put it on the public internet

The repository is public — anyone can run their own instance with the three
commands above. To host a public URL, the included [`render.yaml`](render.yaml)
deploys it to Render's free tier in a few clicks:

1. Open **<https://render.com/deploy?repo=https://github.com/tovmaskayuf/ai_trading_training_bot>**
2. Sign in (free), confirm, and Render builds and starts the service
3. Your instance is live at `https://<your-service>.onrender.com`

Free-tier honesty notes:

- The instance **sleeps after ~15 minutes idle** and wakes on the next visit
  (the first load takes ~30 seconds while it spins up).
- Free instances have **no persistent disk** — the portfolio and collected
  history reset whenever the instance restarts. Fine for practice; add a paid
  disk if you want history to survive.
- The app currently keeps **one portfolio per instance** (no accounts), so a
  shared public URL shares one portfolio among its visitors. Per-visitor
  portfolios are a planned next step.

## Data sources — no API keys required

| Source | Role |
|---|---|
| Binance public REST | Prices, 24h stats, hourly + daily candles, order book — 14 of 15 assets |
| Hyperliquid public API | **HYPE only** — it is not listed on Binance spot (`HYPEUSDT` → `-1121`) |
| CoinGecko free | Market capitalization, basket rank, volume; price fallback |

## The rating system

Each asset receives four sub-scores from 0–100, blended into a weighted
composite, a letter grade (A+ → F), and a signal.

| Axis | Default weight | What it measures |
|---|---|---|
| **Momentum** | 30% | RSI (1h + 4h), MACD histogram and slope, EMA 20/50/200 stacking, range position |
| **Risk** | 25% | ATR%, realized volatility, max drawdown, Sharpe — *inverted*: lower risk scores higher |
| **Structure** | 25% | Basket rank, turnover, volume trend, bid/ask spread |
| **Relative Strength** | 20% | Returns versus BTC and percentile within the basket across 24h/7d/30d |

Several axes are scored **relative to the 15-asset basket** rather than against
fixed thresholds — absolute bands are regime-dependent, and in a quiet market
they collapse every score toward zero.

Sub-scores are stored raw, so the **Rating Weights** sliders recompute the
composite instantly in your browser. The client-side formula is cross-checked
against `analytics/rating.py` and matches exactly.

Signals use hysteresis: **Buy** requires the composite to cross above 70, and
the signal does not flip to **Sell** until it falls to 45. The dead band
between prevents a score hovering near one threshold from flapping every
minute. *Holding* marks an asset you own; *Neutral* means genuinely flat.

## Trading

- Starting capital is whatever you chose on the start screen ($100 – $10M);
  changing it later resets the portfolio.
- **Average cost basis**, like a real brokerage: buying more of a holding
  averages into one line; partial sells book realized P&L against that average.
- **0.10% fee per side** — a flat round-trip loses exactly the fees, so results
  do not flatter you.
- Trade prices are always taken server-side from the latest cycle; a stale
  browser tab cannot fill at an old quote.

## Tests

```bash
.venv/bin/python tests/test_indicators.py   # indicator correctness (RSI, MACD, EMA, ATR…)
.venv/bin/python tests/test_manual.py       # portfolio accounting and settings guards
```

Plain scripts, not pytest — they print PASS/FAIL per assertion and exit
non-zero on failure. Run from the project root.

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/overview` | All assets with prices, sub-scores, composites, signals |
| `GET /api/settings` · `POST /api/settings` | Start-screen preferences (language, assets, capital) |
| `GET /api/asset/{symbol}` | Live detail, rating breakdown, holding |
| `GET /api/asset/{symbol}/prices?range=1h\|24h\|7d\|30d\|1y\|all` | Price series at range-appropriate resolution |
| `GET /api/asset/{symbol}/ratings?range=…` | Composite-score history |
| `GET /api/manual` | Portfolio: holdings, cash, P&L, trades |
| `POST /api/manual/trade` | Buy (`usd` or `qty`) / sell (`qty` or `fraction`) |
| `GET /api/manual/history?range=…` | Portfolio value/cash/invested/realized/fees over time |
| `POST /api/manual/reset` | Reset the portfolio to starting capital |
| `POST /api/weights` | Re-score under custom axis weights |
| `GET /api/stream` | Server-sent events — one message per completed cycle |
| `GET /api/health` | Engine liveness |

## Layout

```
config.py               asset registry, cadence, thresholds, capital bounds, languages
settings.py             user preferences (assets, capital, language), stored in SQLite
db.py                   SQLite (WAL) schema, migrations, history queries
providers/              binance · hyperliquid · coingecko behind one interface
analytics/indicators.py EMA, RSI, MACD, ATR, vol, drawdown, Sharpe — dependency-free
analytics/rating.py     four axes → composite → grade → signal
trading/manual.py       the portfolio: holdings, trades, equity history
engine.py               60-second polling loop (no automated trading)
server.py               FastAPI: REST + SSE + static dashboard
static/dashboard.html   the entire UI — start screen, markets, portfolio — one file
render.yaml             one-click Render deployment blueprint
```

## Notes

- Indicators are **deliberately dependency-free** — plain Python lists, no
  numpy/pandas — so installs keep working on brand-new Python releases.
- Indicator functions return `None` on insufficient data; on a cold start some
  axes read "—" until enough candles accumulate. Expected, not a bug.
- The engine only collects data and rates assets. **It never trades** — every
  trade in the portfolio is one you made.
- The directory name contains `}{`, which breaks unquoted shell paths — always
  quote it. (The GitHub repo is `ai_trading_training_bot`; GitHub rejects braces.)
