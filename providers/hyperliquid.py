"""Hyperliquid public API -- the only source for HYPE.

HYPE is not listed on Binance spot, so its live price and candles come from
Hyperliquid's own info endpoint. Everything here is a POST to a single URL
with a `type` discriminator in the body.
"""

from __future__ import annotations

import logging
from typing import Any

import config
from providers.base import post

log = logging.getLogger("providers.hyperliquid")

API = "https://api.hyperliquid.xyz/info"


def _coins() -> list[str]:
    return [a.hyperliquid_coin for a in config.ASSETS if a.hyperliquid_coin]


async def mids() -> dict[str, dict[str, Any]]:
    """Live mid prices for every Hyperliquid-sourced asset.

    allMids returns ~900 coins in one call; we pick out the ones we track.
    Note this endpoint gives price only -- no 24h change or volume, which the
    caller is expected to backfill from candles or CoinGecko.
    """
    data = await post(API, json={"type": "allMids"})
    out: dict[str, dict[str, Any]] = {}

    for asset in config.ASSETS:
        if not asset.hyperliquid_coin:
            continue
        raw = data.get(asset.hyperliquid_coin)
        if raw is None:
            log.warning("hyperliquid: no mid for %s", asset.hyperliquid_coin)
            continue
        out[asset.symbol] = {"price": float(raw)}
    return out


_INTERVAL_MS = {
    "1m": 60_000, "5m": 300_000, "15m": 900_000,
    "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000,
}


async def candles(symbol: str, interval: str = config.CANDLE_INTERVAL,
                  limit: int = config.CANDLE_LIMIT) -> list[tuple]:
    """OHLCV candles as (open_time, o, h, l, c, v), oldest first.

    Unlike Binance there is no `limit` parameter -- the window is expressed as
    a time range, so we derive start from the interval size and requested count.
    """
    import time

    asset = config.BY_SYMBOL[symbol]
    if not asset.hyperliquid_coin:
        raise ValueError(f"{symbol} is not a Hyperliquid asset")

    step = _INTERVAL_MS.get(interval)
    if step is None:
        raise ValueError(f"unsupported interval {interval}")

    end = int(time.time() * 1000)
    start = end - step * limit

    data = await post(API, json={
        "type": "candleSnapshot",
        "req": {
            "coin": asset.hyperliquid_coin,
            "interval": interval,
            "startTime": start,
            "endTime": end,
        },
    })

    return [
        (int(c["t"]), float(c["o"]), float(c["h"]),
         float(c["l"]), float(c["c"]), float(c["v"]))
        for c in data
    ]
