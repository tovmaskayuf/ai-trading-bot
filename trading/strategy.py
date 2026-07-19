"""Signal -> position translation.

Entries come from the rating engine; sizing comes from volatility. The rules
here are deliberately conservative because the loop runs every two minutes and
an over-eager strategy would churn itself to death on fees.
"""

from __future__ import annotations

import logging
from typing import Any

import config
import db
from trading import portfolio

log = logging.getLogger("strategy")

BULLISH = ("BUY", "STRONG BUY")


def position_size(equity: float, price: float, atr: float) -> float:
    """Risk-parity sizing.

    Risk a fixed fraction of equity per trade, and derive quantity from the
    distance to the stop rather than from a flat dollar amount -- a volatile
    asset therefore gets a smaller position for the same dollar risk.
    """
    if price <= 0:
        return 0.0

    stop_distance = atr * config.STOP_ATR_MULT
    if stop_distance <= 0:
        return 0.0

    risk_dollars = equity * config.RISK_PER_TRADE
    qty = risk_dollars / stop_distance

    # Cap so a single low-volatility name cannot dominate the book.
    max_qty = (equity * config.MAX_POSITION_PCT) / price
    return min(qty, max_qty)


def _held_minutes(position: dict[str, Any], now_ms: int) -> float:
    return (now_ms - position["entry_ts"]) / 60_000


def _summarize(rating: dict[str, Any]) -> str:
    """Human-readable reason string stored alongside the trade."""
    parts = [f"{k[:3].upper()} {rating[k]:.0f}"
             for k in ("momentum", "risk", "structure", "relative")
             if rating.get(k) is not None]
    return f"{rating.get('grade','-')} {rating.get('composite',0):.1f} | " + " ".join(parts)


def evaluate(symbol: str, rating: dict[str, Any], price: float,
             atr: float | None, now_ms: int) -> str | None:
    """Apply entry and exit rules for one asset. Returns an action label if
    something happened, else None."""
    position = portfolio.position_for(symbol)
    composite = rating.get("composite")
    signal = rating.get("signal")

    # --- Exit logic on an open position ---
    if position:
        held = _held_minutes(position, now_ms)
        entry = position["entry_price"]
        high_water = max(position.get("high_water") or entry, price)

        # Hard stop and take-profit fire regardless of hold time: they are risk
        # controls, not opinions.
        if position["stop"] and price <= position["stop"]:
            portfolio.sell(position, price, now_ms, "stop-loss")
            return "STOP"

        if position["take_profit"] and price >= position["take_profit"]:
            portfolio.sell(position, price, now_ms, "take-profit")
            return "TAKE_PROFIT"

        # Trail the stop upward once the trade is in profit. The stop only ever
        # ratchets up -- never loosen it on a pullback. high_water is persisted
        # independently of whether the stop actually moved, otherwise a new high
        # that fails to advance the stop would be forgotten on the next cycle.
        new_stop = position["stop"] or 0.0
        if atr and high_water > entry:
            trail = high_water - atr * config.TRAILING_ATR_MULT
            new_stop = max(new_stop, trail)
        if new_stop != (position["stop"] or 0.0) or high_water != (position.get("high_water") or 0.0):
            portfolio.update_stop(position["id"], new_stop, high_water)

        # Rating-driven exit, gated by the minimum hold so a composite
        # oscillating around a threshold cannot churn the position.
        if composite is not None and composite <= config.EXIT_THRESHOLD:
            if held >= config.MIN_HOLD_MINUTES:
                portfolio.sell(position, price, now_ms, f"rating {composite:.1f}")
                return "EXIT"
            log.debug("%s exit suppressed: held %.1f < %d min",
                      symbol, held, config.MIN_HOLD_MINUTES)
        return None

    # --- Entry logic when flat ---
    if signal not in BULLISH:
        return None

    if len(portfolio.open_positions()) >= config.MAX_OPEN_POSITIONS:
        return None

    if not atr or atr <= 0:
        log.debug("%s entry skipped: no ATR", symbol)
        return None

    prices = {symbol: price}
    equity = portfolio.cash() + portfolio.positions_value(prices)
    qty = position_size(equity, price, atr)
    if qty <= 0:
        return None

    stop = price - atr * config.STOP_ATR_MULT
    take_profit = price + atr * config.TAKE_PROFIT_ATR_MULT

    pos_id = portfolio.buy(symbol, qty, price, now_ms, stop, take_profit,
                           _summarize(rating))
    return "ENTRY" if pos_id else None


def run_cycle(ratings: dict[str, dict[str, Any]], prices: dict[str, float],
              atrs: dict[str, float | None], now_ms: int) -> list[str]:
    """Evaluate every asset once. Returns a list of action descriptions."""
    actions: list[str] = []
    for symbol, rating in ratings.items():
        price = prices.get(symbol)
        if price is None:
            continue
        try:
            action = evaluate(symbol, rating, price, atrs.get(symbol), now_ms)
            if action:
                actions.append(f"{action} {symbol}")
        except Exception:
            log.exception("strategy failed for %s", symbol)
    return actions
