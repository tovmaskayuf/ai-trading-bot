"""Central configuration: asset registry, rating weights, strategy parameters.

The asset registry is the single source of truth for how each symbol maps onto
each upstream provider. Provider selection is driven entirely off this table --
no module should ever branch on a symbol name directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "bot.db"
STATIC_DIR = BASE_DIR / "static"

# --- Polling cadence -------------------------------------------------------

CYCLE_SECONDS = 120

# Candles and market-cap data move far slower than price, so they refresh on a
# multiple of the base cycle to stay comfortably inside free-tier rate limits.
KLINE_EVERY_N_CYCLES = 5      # ~10 min
MARKET_EVERY_N_CYCLES = 4     # ~8 min

CANDLE_INTERVAL = "1h"
CANDLE_LIMIT = 300            # enough history for EMA200 + 30d stats

# --- Assets ----------------------------------------------------------------


@dataclass(frozen=True)
class Asset:
    symbol: str
    name: str
    thesis: str
    coingecko_id: str
    binance_symbol: str | None = None
    hyperliquid_coin: str | None = None

    @property
    def price_source(self) -> str:
        """Primary provider for live price and candles."""
        return "binance" if self.binance_symbol else "hyperliquid"


# HYPE is deliberately the odd one out: it is not listed on Binance spot
# (HYPEUSDT returns -1121 Invalid symbol), so it sources from Hyperliquid's
# own public API instead. Every other asset uses Binance as primary with
# CoinGecko as the fallback.
ASSETS: list[Asset] = [
    Asset("BTC", "Bitcoin", "The premier store of value.",
          "bitcoin", binance_symbol="BTCUSDT"),
    Asset("ETH", "Ethereum", "The leading foundation for smart contracts and DeFi.",
          "ethereum", binance_symbol="ETHUSDT"),
    Asset("SOL", "Solana", "Low fees, high speeds, and massive dApp revenue.",
          "solana", binance_symbol="SOLUSDT"),
    Asset("BNB", "BNB", "Powers the Binance ecosystem with ongoing coin burns.",
          "binancecoin", binance_symbol="BNBUSDT"),
    Asset("XRP", "XRP", "High-efficiency institutional cross-border payments.",
          "ripple", binance_symbol="XRPUSDT"),
    Asset("LINK", "Chainlink", "The oracle layer bridging chains and real-world data.",
          "chainlink", binance_symbol="LINKUSDT"),
    Asset("SUI", "Sui", "Rising Layer-1 with explosive user and dev growth.",
          "sui", binance_symbol="SUIUSDT"),
    Asset("AVAX", "Avalanche", "Scalable platform favored for subnets and institutional DeFi.",
          "avalanche-2", binance_symbol="AVAXUSDT"),
    Asset("TRX", "TRON", "Major network for stablecoin transfers and content hosting.",
          "tron", binance_symbol="TRXUSDT"),
    Asset("ADA", "Cardano", "Peer-reviewed chain focused on security and sustainability.",
          "cardano", binance_symbol="ADAUSDT"),
    Asset("ARB", "Arbitrum", "Leading Layer-2 making Ethereum faster and cheaper.",
          "arbitrum", binance_symbol="ARBUSDT"),
    Asset("ONDO", "Ondo Finance", "Major player in Real-World Asset tokenization.",
          "ondo-finance", binance_symbol="ONDOUSDT"),
    Asset("TAO", "Bittensor", "Decentralized network incentivizing machine learning.",
          "bittensor", binance_symbol="TAOUSDT"),
    Asset("HYPE", "Hyperliquid", "Dominates decentralized perpetuals and trading infra.",
          "hyperliquid", hyperliquid_coin="HYPE"),
    Asset("DOGE", "Dogecoin", "The most popular and resilient meme coin.",
          "dogecoin", binance_symbol="DOGEUSDT"),
]

BY_SYMBOL: dict[str, Asset] = {a.symbol: a for a in ASSETS}
SYMBOLS: list[str] = [a.symbol for a in ASSETS]
BENCHMARK = "BTC"

# --- Rating ----------------------------------------------------------------

# Weights must sum to 1.0. The dashboard can override these per-request; these
# are the defaults used by the engine when it persists ratings.
DEFAULT_WEIGHTS: dict[str, float] = {
    "momentum": 0.30,
    "risk": 0.25,
    "structure": 0.25,
    "relative": 0.20,
}

# Letter grade cutoffs, checked high to low.
GRADE_BANDS: list[tuple[float, str]] = [
    (90, "A+"), (85, "A"), (80, "A-"),
    (75, "B+"), (70, "B"), (65, "B-"),
    (60, "C+"), (55, "C"), (50, "C-"),
    (45, "D+"), (40, "D"), (35, "D-"),
    (0, "F"),
]

# --- Signals ---------------------------------------------------------------

# The gap between BUY_THRESHOLD and EXIT_THRESHOLD is the hysteresis dead band.
# Ratings recompute every 2 minutes; without this gap a composite hovering near
# a single threshold would open and close positions on nearly every cycle.
BUY_THRESHOLD = 70.0
STRONG_BUY_THRESHOLD = 82.0
EXIT_THRESHOLD = 45.0
STRONG_SELL_THRESHOLD = 32.0
MIN_HOLD_MINUTES = 30

# --- Paper trading ---------------------------------------------------------

STARTING_CAPITAL = 100_000.0
RISK_PER_TRADE = 0.02          # fraction of equity risked per position

# These two are coupled: MAX_POSITION_PCT * MAX_OPEN_POSITIONS must stay <= 1.0
# or the position limit is unreachable, because cash runs out first and the
# book silently caps below MAX_OPEN_POSITIONS. 0.12 * 8 = 0.96 leaves a small
# cash buffer for fees.
MAX_POSITION_PCT = 0.12        # hard cap on any single position
MAX_OPEN_POSITIONS = 8
STOP_ATR_MULT = 2.0
TAKE_PROFIT_ATR_MULT = 3.0
TRAILING_ATR_MULT = 2.0        # trailing stop distance once in profit
FEE_RATE = 0.001               # 0.1% per side
SLIPPAGE_RATE = 0.0005         # 5 bps assumed slippage per fill

# --- Retention -------------------------------------------------------------

RETENTION_DAYS = 90
