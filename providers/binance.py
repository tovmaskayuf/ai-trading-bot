"""Binance public REST -- primary source for 14 of the 15 assets.

No API key required. HYPE is intentionally absent here: it is not listed on
Binance spot (HYPEUSDT -> -1121 Invalid symbol) and routes to Hyperliquid.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import config
from providers.base import get

log = logging.getLogger("providers.binance")

API = "https://api.binance.com/api/v3"


def _symbols() -> list[str]:
    return [a.binance_symbol for a in config.ASSETS if a.binance_symbol]


def _encode(pairs: list[str]) -> str:
    """Binance's `symbols` param rejects whitespace, so the default json.dumps
    separator (", ") triggers -1100 Illegal characters. Force compact output."""
    return json.dumps(pairs, separators=(",", ":"))


async def tickers_24h() -> dict[str, dict[str, Any]]:
    """One batched call covering every Binance-listed asset.

    Returns {SYMBOL: {price, chg_24h, volume_24h, quote_volume, high, low}}
    keyed by our internal symbol (BTC), not the pair (BTCUSDT).
    """
    pairs = _symbols()
    data = await get(f"{API}/ticker/24hr", params={"symbols": _encode(pairs)})

    by_pair = {row["symbol"]: row for row in data}
    out: dict[str, dict[str, Any]] = {}

    for asset in config.ASSETS:
        row = by_pair.get(asset.binance_symbol) if asset.binance_symbol else None
        if not row:
            continue
        out[asset.symbol] = {
            "price": float(row["lastPrice"]),
            "chg_24h": float(row["priceChangePercent"]),
            "volume_24h": float(row["volume"]),
            "quote_volume": float(row["quoteVolume"]),
            "high": float(row["highPrice"]),
            "low": float(row["lowPrice"]),
        }
    return out


async def book_tickers() -> dict[str, dict[str, float]]:
    """Best bid/ask for spread and top-of-book depth scoring."""
    pairs = _symbols()
    data = await get(f"{API}/ticker/bookTicker", params={"symbols": _encode(pairs)})

    by_pair = {row["symbol"]: row for row in data}
    out: dict[str, dict[str, float]] = {}

    for asset in config.ASSETS:
        row = by_pair.get(asset.binance_symbol) if asset.binance_symbol else None
        if not row:
            continue
        bid, ask = float(row["bidPrice"]), float(row["askPrice"])
        mid = (bid + ask) / 2
        out[asset.symbol] = {
            "bid": bid,
            "ask": ask,
            "spread_bps": ((ask - bid) / mid * 10_000) if mid > 0 else 0.0,
            "depth_usd": bid * float(row["bidQty"]) + ask * float(row["askQty"]),
        }
    return out


async def klines(symbol: str, interval: str = config.CANDLE_INTERVAL,
                 limit: int = config.CANDLE_LIMIT) -> list[tuple]:
    """OHLCV candles as (open_time, o, h, l, c, v) tuples, oldest first."""
    asset = config.BY_SYMBOL[symbol]
    if not asset.binance_symbol:
        raise ValueError(f"{symbol} is not listed on Binance")

    data = await get(f"{API}/klines", params={
        "symbol": asset.binance_symbol, "interval": interval, "limit": limit,
    })
    return [
        (int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]))
        for r in data
    ]
