"""Manual (user-driven) paper trading.

Separate from the bot's own portfolio so the two track records can be compared
on identical live data: does the algorithm actually beat you?

Modelled as holdings with an average cost basis rather than discrete positions.
Buying more of something you already own averages into one line; selling books
realised P&L against that average. This is how a brokerage account behaves, and
it is what makes partial sells meaningful.
"""

from __future__ import annotations

import logging

import config
import db

log = logging.getLogger("manual")

CASH_KEY = "manual_cash"


class TradeError(ValueError):
    """A trade the user cannot make -- insufficient cash, size, or holdings."""


# --- Cash ------------------------------------------------------------------

def cash() -> float:
    raw = db.get_meta(CASH_KEY)
    if raw is None:
        db.set_meta(CASH_KEY, str(config.STARTING_CAPITAL))
        return config.STARTING_CAPITAL
    return float(raw)


def set_cash(value: float) -> None:
    db.set_meta(CASH_KEY, str(value))


# --- Holdings --------------------------------------------------------------

def holdings() -> list[dict]:
    return db.query("SELECT * FROM manual_holdings WHERE qty > 0 ORDER BY symbol")


def holding_for(symbol: str) -> dict | None:
    return db.query_one(
        "SELECT * FROM manual_holdings WHERE symbol=? AND qty > 0", (symbol,)
    )


def trades(limit: int = 200) -> list[dict]:
    return db.query("SELECT * FROM manual_trades ORDER BY ts DESC LIMIT ?", (limit,))


# --- Trading ---------------------------------------------------------------

def buy(symbol: str, price: float, ts: int, *,
        usd: float | None = None, qty: float | None = None) -> dict:
    """Buy by dollar amount or quantity. Fees come out of cash on top of cost."""
    if symbol not in config.BY_SYMBOL:
        raise TradeError(f"unknown symbol {symbol}")
    if price <= 0:
        raise TradeError("no live price available for this asset")

    if usd is not None:
        # Interpret the dollar amount as all-in (cost + fee), so "spend $1000"
        # actually removes $1000 from cash rather than $1000 plus fees.
        if usd <= 0:
            raise TradeError("amount must be greater than zero")
        gross = usd / (1 + config.FEE_RATE)
        qty = gross / price
    elif qty is not None:
        if qty <= 0:
            raise TradeError("quantity must be greater than zero")
        gross = qty * price
    else:
        raise TradeError("specify either usd or qty")

    fee = gross * config.FEE_RATE
    total = gross + fee

    available = cash()
    # Absorb float dust so a "Max" button doesn't fail by a fraction of a cent.
    if total > available + 1e-6:
        raise TradeError(
            f"insufficient cash: need ${total:,.2f}, have ${available:,.2f}"
        )
    total = min(total, available)

    existing = holding_for(symbol)
    if existing:
        new_qty = existing["qty"] + qty
        # Average cost includes fees, so realised P&L reflects what you paid.
        new_cost = (existing["qty"] * existing["avg_cost"] + total) / new_qty
    else:
        new_qty, new_cost = qty, total / qty

    with db.tx() as conn:
        conn.execute(
            "INSERT INTO manual_holdings (symbol, qty, avg_cost, updated_ts) "
            "VALUES (?,?,?,?) ON CONFLICT(symbol) DO UPDATE SET "
            "qty=excluded.qty, avg_cost=excluded.avg_cost, updated_ts=excluded.updated_ts",
            (symbol, new_qty, new_cost, ts),
        )
        conn.execute(
            "INSERT INTO manual_trades (symbol, side, qty, price, value, fee, ts) "
            "VALUES (?,'BUY',?,?,?,?,?)",
            (symbol, qty, price, gross, fee, ts),
        )

    set_cash(available - total)
    log.info("MANUAL BUY  %-5s qty=%.6f @ %.6f  total=%.2f", symbol, qty, price, total)
    return {"symbol": symbol, "side": "BUY", "qty": qty, "price": price,
            "value": gross, "fee": fee, "cash": cash()}


def sell(symbol: str, price: float, ts: int, *,
         qty: float | None = None, fraction: float | None = None) -> dict:
    """Sell all or part of a holding. Realised P&L is booked against avg cost."""
    held = holding_for(symbol)
    if not held:
        raise TradeError(f"you do not hold any {symbol}")
    if price <= 0:
        raise TradeError("no live price available for this asset")

    if fraction is not None:
        if not 0 < fraction <= 1:
            raise TradeError("fraction must be between 0 and 1")
        qty = held["qty"] * fraction
    if qty is None:
        raise TradeError("specify either qty or fraction")
    if qty <= 0:
        raise TradeError("quantity must be greater than zero")

    # Tolerate float dust on a full sell rather than rejecting it.
    if qty > held["qty"] + 1e-9:
        raise TradeError(f"you only hold {held['qty']:.8f} {symbol}")
    qty = min(qty, held["qty"])

    gross = qty * price
    fee = gross * config.FEE_RATE
    proceeds = gross - fee

    cost_basis = qty * held["avg_cost"]
    pnl = proceeds - cost_basis
    pnl_pct = (pnl / cost_basis * 100) if cost_basis else 0.0

    remaining = held["qty"] - qty
    with db.tx() as conn:
        if remaining <= 1e-12:
            conn.execute("DELETE FROM manual_holdings WHERE symbol=?", (symbol,))
        else:
            conn.execute(
                "UPDATE manual_holdings SET qty=?, updated_ts=? WHERE symbol=?",
                (remaining, ts, symbol),
            )
        conn.execute(
            "INSERT INTO manual_trades (symbol, side, qty, price, value, fee, ts, pnl, pnl_pct) "
            "VALUES (?,'SELL',?,?,?,?,?,?,?)",
            (symbol, qty, price, gross, fee, ts, pnl, pnl_pct),
        )

    set_cash(cash() + proceeds)
    log.info("MANUAL SELL %-5s qty=%.6f @ %.6f  pnl=%.2f (%.2f%%)",
             symbol, qty, price, pnl, pnl_pct)
    return {"symbol": symbol, "side": "SELL", "qty": qty, "price": price,
            "value": gross, "fee": fee, "pnl": pnl, "pnl_pct": pnl_pct,
            "cash": cash()}


# --- Reporting -------------------------------------------------------------

def snapshot(prices: dict[str, float]) -> dict:
    """Full portfolio view: holdings marked to market, plus headline stats."""
    c = cash()
    rows = []
    invested = 0.0
    market_value = 0.0

    for h in holdings():
        price = prices.get(h["symbol"])
        cost = h["qty"] * h["avg_cost"]
        value = h["qty"] * price if price else cost
        invested += cost
        market_value += value
        asset = config.BY_SYMBOL.get(h["symbol"])
        rows.append({
            "symbol": h["symbol"],
            "name": asset.name if asset else h["symbol"],
            "qty": h["qty"],
            "avg_cost": h["avg_cost"],
            "price": price,
            "cost_basis": cost,
            "value": value,
            "unrealized_pnl": value - cost,
            "unrealized_pct": ((value / cost - 1) * 100) if cost else 0.0,
            "updated_ts": h["updated_ts"],
        })

    rows.sort(key=lambda r: r["value"], reverse=True)

    all_trades = trades(limit=10_000)
    sells = [t for t in all_trades if t["side"] == "SELL"]
    wins = [t for t in sells if (t["pnl"] or 0) > 0]
    realized = sum(t["pnl"] or 0 for t in sells)
    fees_paid = sum(t["fee"] or 0 for t in all_trades)

    total = c + market_value
    for r in rows:
        r["weight_pct"] = (r["value"] / total * 100) if total else 0.0

    return {
        "cash": c,
        "invested": invested,
        "market_value": market_value,
        "total": total,
        "starting_capital": config.STARTING_CAPITAL,
        "total_return_pct": (total / config.STARTING_CAPITAL - 1) * 100,
        "total_pnl": total - config.STARTING_CAPITAL,
        "unrealized_pnl": market_value - invested,
        "realized_pnl": realized,
        "fees_paid": fees_paid,
        "holdings": rows,
        "trades": all_trades[:200],
        "trade_count": len(all_trades),
        "closed_count": len(sells),
        "win_rate": (len(wins) / len(sells) * 100) if sells else 0.0,
        "fee_rate": config.FEE_RATE,
    }


def reset() -> None:
    db.reset_manual()
    set_cash(config.STARTING_CAPITAL)
    log.info("manual portfolio reset to %.2f", config.STARTING_CAPITAL)
