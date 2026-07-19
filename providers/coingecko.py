"""CoinGecko free API -- market-structure data for all 15 assets, plus the
price fallback when Binance is unavailable.

One /simple/price call covers every asset at once. No API key needed; the
engine polls this on a slower cadence than price to stay inside the free tier.
"""

from __future__ import annotations

import logging
from typing import Any

import config
from providers.base import get

log = logging.getLogger("providers.coingecko")

API = "https://api.coingecko.com/api/v3"


async def market_data() -> dict[str, dict[str, Any]]:
    """Price, 24h change, market cap and volume for all 15 in a single call."""
    ids = ",".join(a.coingecko_id for a in config.ASSETS)
    data = await get(f"{API}/simple/price", params={
        "ids": ids,
        "vs_currencies": "usd",
        "include_24hr_change": "true",
        "include_24hr_vol": "true",
        "include_market_cap": "true",
    })

    out: dict[str, dict[str, Any]] = {}
    for asset in config.ASSETS:
        row = data.get(asset.coingecko_id)
        if not row:
            log.warning("coingecko: no data for %s (%s)", asset.symbol, asset.coingecko_id)
            continue
        out[asset.symbol] = {
            "price": row.get("usd"),
            "chg_24h": row.get("usd_24h_change"),
            "mcap": row.get("usd_market_cap"),
            "volume_24h_usd": row.get("usd_24h_vol"),
        }

    # Rank by market cap within our own basket. CoinGecko's global rank needs a
    # heavier endpoint, and a basket-relative rank is what the structure score
    # actually wants anyway.
    ranked = sorted(
        (s for s, v in out.items() if v.get("mcap")),
        key=lambda s: out[s]["mcap"],
        reverse=True,
    )
    for i, sym in enumerate(ranked, start=1):
        out[sym]["rank"] = i

    return out
