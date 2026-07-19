"""Provider aggregation.

Callers use `fetch_prices()` and `fetch_candles()` rather than reaching into
individual provider modules, so routing stays driven by the asset registry.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import config
from providers import binance, coingecko, hyperliquid
from providers.base import ProviderError, aclose

log = logging.getLogger("providers")

__all__ = ["fetch_prices", "fetch_candles", "fetch_market", "aclose", "ProviderError"]


async def fetch_prices() -> dict[str, dict[str, Any]]:
    """Live price for all 15, routed per the asset registry.

    Each entry carries `stale: bool` -- true when the primary provider failed
    and the value came from the CoinGecko fallback (or is missing entirely),
    so the UI can flag it rather than silently showing an old number.
    """
    bn_task = asyncio.create_task(binance.tickers_24h())
    hl_task = asyncio.create_task(hyperliquid.mids())
    results = await asyncio.gather(bn_task, hl_task, return_exceptions=True)

    merged: dict[str, dict[str, Any]] = {}
    failed_primary: list[str] = []

    for result in results:
        if isinstance(result, Exception):
            log.warning("price provider failed: %s", result)
            continue
        for sym, row in result.items():
            merged[sym] = {**row, "stale": False}

    # Anything the primary route did not supply falls back to CoinGecko.
    missing = [s for s in config.SYMBOLS if s not in merged]
    if missing:
        failed_primary = missing
        try:
            cg = await coingecko.market_data()
            for sym in missing:
                if sym in cg and cg[sym].get("price") is not None:
                    merged[sym] = {
                        "price": cg[sym]["price"],
                        "chg_24h": cg[sym].get("chg_24h"),
                        "volume_24h": None,
                        "quote_volume": cg[sym].get("volume_24h_usd"),
                        "stale": True,
                    }
        except ProviderError as e:
            log.error("coingecko fallback failed for %s: %s", missing, e)

    if failed_primary:
        log.warning("fell back to coingecko for: %s", failed_primary)

    return merged


async def fetch_candles(symbol: str, interval: str = config.CANDLE_INTERVAL,
                        limit: int = config.CANDLE_LIMIT) -> list[tuple]:
    """OHLCV for one symbol from whichever provider owns it."""
    asset = config.BY_SYMBOL[symbol]
    if asset.price_source == "hyperliquid":
        return await hyperliquid.candles(symbol, interval, limit)
    return await binance.klines(symbol, interval, limit)


async def fetch_market() -> dict[str, dict[str, Any]]:
    """Market cap / rank / volume for the structure score."""
    return await coingecko.market_data()


async def fetch_book() -> dict[str, dict[str, float]]:
    """Spread and depth. Binance-only; HYPE simply has no entry."""
    try:
        return await binance.book_tickers()
    except ProviderError as e:
        log.warning("bookTicker failed: %s", e)
        return {}
