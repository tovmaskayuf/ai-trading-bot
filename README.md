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
- **A portfolio per visitor**, with optional accounts — play as a guest
  immediately, and sign up later without losing what you built
- **Global leaderboard** ranking every registered player by total return
- **Five languages** — English, Հայերեն, Українська, Español, Ελληνικά
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

1. Open **<https://render.com/deploy?repo=https://github.com/tovmaskayuf/ai-trading-training-bot>**
2. Sign in (free), confirm, and Render builds and starts the service
3. Your instance is live at `https://<your-service>.onrender.com`

### Add a CoinGecko key when deploying

Running locally needs no keys at all. On Render it is worth one extra minute:
CoinGecko rate-limits keyless requests **per client IP**, and Render's free
tier egresses through shared addresses that are throttled on reputation, so the
call fails there however slowly you poll. Prices are unaffected — Binance and
Hyperliquid carry those — but market cap and rank come back empty and the
Structure axis then scores on volume trend and spread alone.

1. Get a free **Demo** key at <https://www.coingecko.com/en/developers/dashboard>
2. In Render: **Environment → Add Environment Variable**
3. Key `COINGECKO_API_KEY`, value your key. Leave `COINGECKO_PLAN` as `demo`.

A **wrong** key is obvious — CoinGecko rejects it with `401` / `error_code
10002`, which surfaces in the dashboard's error banner. A **missing** key is
the quiet failure: the app falls back to keyless, which works most of the time,
so nothing looks broken until a throttle hits.

To check which mode is actually live, hit `/api/health`:

```bash
curl -s https://<your-service>.onrender.com/api/health | grep coingecko_auth
# "coingecko_auth": "demo"      <- key loaded
# "coingecko_auth": "keyless"   <- key did not reach the process
```

If you put the variable in a Render **Environment Group**, creating the group
is not enough — the group has to be linked to the service (service →
Environment → *Link Environment Group*) or its variables never reach the app.

### Accounts need a database

`render.yaml` also provisions a free Postgres (`tt-trading-db`) and injects
`DATABASE_URL`. Without it the app still runs, but the account store falls back
to SQLite on the instance's ephemeral disk, so **every account, portfolio and
leaderboard standing is wiped on restart** — including the 15-minute idle
spin-down. Market data is unaffected either way; it is regenerable and lives on
the ephemeral disk deliberately.

Check which store is live:

```bash
curl -s https://<your-service>.onrender.com/api/health | grep -E "store_backend|accounts_durable"
```

> **Free Postgres expires 30 days after creation**, with a 14-day grace period
> before deletion. Upgrade or export before then.

### Optional: an administrator account

Set `MASTER_PASSWORD` (and optionally `MASTER_USERNAME`, default `master`) on
the **service** to create an admin account at startup. With it unset, no admin
account exists at all — rather than one with a guessable password. Never commit
it: this repository is public.

Verify with `curl … /api/health | grep admin_configured`.

Free-tier honesty notes:

- The instance **sleeps after ~15 minutes idle** and wakes on the next visit
  (the first load takes ~30 seconds while it spins up).
- Market data re-backfills on every cold start — about 3.5 seconds for all 15
  assets — so ratings fill in shortly after a wake.

## Data sources — no API keys required to run locally

| Source | Role |
|---|---|
| Binance public REST | Prices, 24h stats, hourly + daily candles, order book — 14 of 15 assets |
| Hyperliquid public API | **HYPE only** — it is not listed on Binance spot (`HYPEUSDT` → `-1121`) |
| CoinGecko free | Market capitalization, basket rank, volume; price fallback |

All three work keyless from a home connection. The one optional key is
`COINGECKO_API_KEY`, which only matters on shared cloud hosting — see
[Add a CoinGecko key when deploying](#add-a-coingecko-key-when-deploying).

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
.venv/bin/python tests/test_manual.py       # legacy single-portfolio accounting
.venv/bin/python tests/test_portfolio.py    # per-user portfolios, isolation, leaderboard
.venv/bin/python tests/test_admin.py        # block / delete / reset, and no password leak
.venv/bin/python tests/test_ratelimit.py    # 429/418 back-off (no network needed)
.venv/bin/python tests/test_frontend.py     # JS parses, i18n parity, DOM sanity
```

Plain scripts, not pytest — they print PASS/FAIL per assertion and exit
non-zero on failure. Run from the project root.

`test_frontend.py` needs **node** on PATH (`brew install node`); it runs each
script block through `node --check` and evaluates the i18n table in real
JavaScript. A release once shipped with markup that did not parse — an
unclosed `<noscript>` swallowed the whole body — while every API check passed
and the page rendered blank. Braces balancing is not the same as parsing.

`test_portfolio.py` and `test_admin.py` run against whichever backend is
configured, so they double as the Postgres check:

```bash
DATABASE_URL=postgresql://… .venv/bin/python tests/test_portfolio.py
```

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/overview` | All assets with prices, sub-scores, composites, signals |
| `GET /api/settings` · `POST /api/settings` | Start-screen preferences (language, assets, capital) |
| `GET /api/asset/{symbol}` | Live detail, rating breakdown, holding |
| `GET /api/asset/{symbol}/prices?range=1h\|24h\|7d\|30d\|1y\|all` | Price series at range-appropriate resolution |
| `GET /api/asset/{symbol}/ratings?range=…` | Composite-score history |
| `GET /api/manual` | Your portfolio: holdings, cash, P&L, trades |
| `POST /api/manual/trade` | Buy (`usd` or `qty`) / sell (`qty` or `fraction`) |
| `GET /api/manual/history?range=…` | Portfolio value/cash/invested/realized/fees over time |
| `POST /api/manual/reset` | Reset **your own** portfolio to starting capital |
| `POST /api/weights` | Re-score under custom axis weights |
| `GET /api/stream` | Server-sent events — one message per completed cycle |
| `GET /api/health` | Engine liveness, data-source auth, store backend, rate-limit state |

Identity is a `HttpOnly` session cookie; a first visit mints a guest so you can
trade before signing up.

| Endpoint | Purpose |
|---|---|
| `GET /api/me` | Who you are (guest, registered, admin, blocked) |
| `POST /api/auth/signup` | Create an account — a guest keeps its portfolio |
| `POST /api/auth/login` · `POST /api/auth/logout` | Session in / out |
| `GET /api/leaderboard` | Global standings, marked to live prices |

Administrator routes require an admin session and return **404** to everyone
else, so the surface is not discoverable by probing. **No route can reveal a
password** — they are one-way hashes; reset is the recovery path.

| Endpoint | Purpose |
|---|---|
| `GET /api/admin/players` | Every account with live standings |
| `GET /api/admin/player/{id}` | One player in full: trades, equity, sessions |
| `POST /api/admin/player/{id}/block` | Block or unblock |
| `POST /api/admin/player/{id}/delete` | Delete, releasing the username |
| `POST /api/admin/player/{id}/password` | Set a new password |

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
  quote it. (The GitHub repo is `ai-trading-training-bot`; GitHub rejects braces.)
