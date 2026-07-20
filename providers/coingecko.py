"""CoinGecko -- market-structure data for all 15 assets, plus the price
fallback when Binance is unavailable.

One /simple/price call covers every asset at once.

Works keyless, but keyless requests are rate-limited by client IP, and shared
cloud egress (Render's free tier especially) is throttled hard enough on
reputation alone that the call fails regardless of how slowly we poll. Setting
COINGECKO_API_KEY moves the quota onto the key instead of the IP, which is the
only thing that actually fixes it there. Demo and Pro keys use different hosts
and different header names, so the tier is selected explicitly.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import config
from providers.base import get

log = logging.getLogger("providers.coingecko")

PUBLIC_API = "https://api.coingecko.com/api/v3"
PRO_API = "https://pro-api.coingecko.com/api/v3"

API_KEY = os.getenv("COINGECKO_API_KEY", "").strip()
# Demo keys are the free tier and must go to the public host; only a paid key
# is valid against pro-api, where it 401s if sent to the wrong one.
IS_PRO = os.getenv("COINGECKO_PLAN", "demo").strip().lower() == "pro"

API = PRO_API if (API_KEY and IS_PRO) else PUBLIC_API


def auth_headers() -> dict[str, str]:
    if not API_KEY:
        return {}
    return {("x-cg-pro-api-key" if IS_PRO else "x-cg-demo-api-key"): API_KEY}


def auth_mode() -> str:
    """'demo' | 'pro' | 'keyless'. Safe to expose over HTTP -- carries no key
    material, only which mode is active."""
    if not API_KEY:
        return "keyless"
    return "pro" if IS_PRO else "demo"


def key_status() -> str:
    """Human-readable auth mode for local logs, with a short key suffix to tell
    two keys apart. Log-only -- use auth_mode() for anything user-facing."""
    if not API_KEY:
        return "keyless (IP-rate-limited)"
    return f"{'pro' if IS_PRO else 'demo'} key ...{API_KEY[-4:]}"


async def market_data() -> dict[str, dict[str, Any]]:
    """Price, 24h change, market cap and volume for all 15 in a single call."""
    ids = ",".join(a.coingecko_id for a in config.ASSETS)
    data = await get(f"{API}/simple/price", params={
        "ids": ids,
        "vs_currencies": "usd",
        "include_24hr_change": "true",
        "include_24hr_vol": "true",
        "include_market_cap": "true",
    }, headers=auth_headers())

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
