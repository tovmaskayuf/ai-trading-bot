"""Paper-trading portfolio accounting.

Holds cash, open positions, the realised trade log and the equity curve. All
state lives in SQLite so it survives restarts -- the engine is expected to run
for weeks and the portfolio must not reset when the process bounces.
"""

from __future__ import annotations

import logging
from typing import Any

import config
import db

log = logging.getLogger("portfolio")

CASH_KEY = "cash"


def cash() -> float:
    raw = db.get_meta(CASH_KEY)
    if raw is None:
        db.set_meta(CASH_KEY, str(config.STARTING_CAPITAL))
        return config.STARTING_CAPITAL
    return float(raw)


def set_cash(value: float) -> None:
    db.set_meta(CASH_KEY, str(value))


def open_positions() -> list[dict[str, Any]]:
    return db.query("SELECT * FROM positions WHERE status='open' ORDER BY entry_ts")


def position_for(symbol: str) -> dict[str, Any] | None:
    return db.query_one(
        "SELECT * FROM positions WHERE symbol=? AND status='open' LIMIT 1", (symbol,)
    )


def closed_trades(limit: int = 200) -> list[dict[str, Any]]:
    return db.query(
        "SELECT * FROM trades WHERE side='SELL' ORDER BY ts DESC LIMIT ?", (limit,)
    )


def all_trades(limit: int = 400) -> list[dict[str, Any]]:
    return db.query("SELECT * FROM trades ORDER BY ts DESC LIMIT ?", (limit,))


def positions_value(prices: dict[str, float]) -> float:
    return sum(
        p["qty"] * prices.get(p["symbol"], p["entry_price"]) for p in open_positions()
    )


def equity(prices: dict[str, float]) -> float:
    return cash() + positions_value(prices)


# --- Mutations -------------------------------------------------------------


def buy(symbol: str, qty: float, price: float, ts: int, stop: float | None,
        take_profit: float | None, rationale: str) -> int | None:
    """Open a position. Applies fees and slippage; refuses if cash is short."""
    fill = price * (1 + config.SLIPPAGE_RATE)
    gross = qty * fill
    fee = gross * config.FEE_RATE
    total = gross + fee

    available = cash()
    if total > available:
        log.warning("insufficient cash for %s: need %.2f have %.2f", symbol, total, available)
        return None

    with db.tx() as conn:
        cur = conn.execute(
            "INSERT INTO positions (symbol, qty, entry_price, entry_ts, stop, "
            "take_profit, high_water, status, rationale) "
            "VALUES (?,?,?,?,?,?,?,'open',?)",
            (symbol, qty, fill, ts, stop, take_profit, fill, rationale),
        )
        pos_id = cur.lastrowid
        conn.execute(
            "INSERT INTO trades (position_id, symbol, side, qty, price, ts, fee, rationale) "
            "VALUES (?,?,'BUY',?,?,?,?,?)",
            (pos_id, symbol, qty, fill, ts, fee, rationale),
        )

    set_cash(available - total)
    log.info("BUY  %-5s qty=%.6f @ %.6f  (%s)", symbol, qty, fill, rationale)
    return pos_id


def sell(position: dict[str, Any], price: float, ts: int, reason: str) -> float:
    """Close a position and book realised P&L. Returns the P&L in dollars."""
    fill = price * (1 - config.SLIPPAGE_RATE)
    qty = position["qty"]
    gross = qty * fill
    fee = gross * config.FEE_RATE
    proceeds = gross - fee

    entry_cost = qty * position["entry_price"]
    entry_fee = entry_cost * config.FEE_RATE
    pnl = proceeds - entry_cost - entry_fee
    pnl_pct = (pnl / (entry_cost + entry_fee) * 100) if entry_cost else 0.0

    with db.tx() as conn:
        conn.execute("UPDATE positions SET status='closed' WHERE id=?", (position["id"],))
        conn.execute(
            "INSERT INTO trades (position_id, symbol, side, qty, price, ts, fee, "
            "pnl, pnl_pct, exit_reason, rationale) "
            "VALUES (?,?,'SELL',?,?,?,?,?,?,?,?)",
            (position["id"], position["symbol"], qty, fill, ts, fee,
             pnl, pnl_pct, reason, position.get("rationale")),
        )

    set_cash(cash() + proceeds)
    log.info("SELL %-5s qty=%.6f @ %.6f  pnl=%.2f (%.2f%%)  [%s]",
             position["symbol"], qty, fill, pnl, pnl_pct, reason)
    return pnl


def update_stop(position_id: int, stop: float, high_water: float) -> None:
    with db.tx() as conn:
        conn.execute("UPDATE positions SET stop=?, high_water=? WHERE id=?",
                     (stop, high_water, position_id))


# --- Reporting -------------------------------------------------------------


def record_equity(ts: int, prices: dict[str, float]) -> dict[str, float]:
    c = cash()
    pv = positions_value(prices)
    total = c + pv

    peak_row = db.query_one("SELECT MAX(total) AS peak FROM equity")
    peak = max((peak_row or {}).get("peak") or 0.0, total, config.STARTING_CAPITAL)
    drawdown = ((peak - total) / peak * 100) if peak > 0 else 0.0

    db.insert_equity(ts, c, pv, total, drawdown)
    return {"cash": c, "positions_value": pv, "total": total, "drawdown_pct": drawdown}


def stats(prices: dict[str, float]) -> dict[str, Any]:
    """Headline performance numbers for the dashboard."""
    trades = closed_trades(limit=10_000)
    wins = [t for t in trades if (t["pnl"] or 0) > 0]
    losses = [t for t in trades if (t["pnl"] or 0) <= 0]

    gross_win = sum(t["pnl"] for t in wins) or 0.0
    gross_loss = abs(sum(t["pnl"] for t in losses)) or 0.0

    total = equity(prices)
    curve = db.query("SELECT MAX(total) AS peak FROM equity")
    peak = max((curve[0]["peak"] if curve and curve[0]["peak"] else 0.0),
               total, config.STARTING_CAPITAL)

    return {
        "equity": total,
        "cash": cash(),
        "positions_value": positions_value(prices),
        "starting_capital": config.STARTING_CAPITAL,
        "total_return_pct": (total / config.STARTING_CAPITAL - 1) * 100,
        "realized_pnl": sum(t["pnl"] or 0 for t in trades),
        "open_positions": len(open_positions()),
        "closed_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": (len(wins) / len(trades) * 100) if trades else 0.0,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else None,
        "avg_win": (gross_win / len(wins)) if wins else 0.0,
        "avg_loss": (gross_loss / len(losses)) if losses else 0.0,
        "max_drawdown_pct": ((peak - total) / peak * 100) if peak > 0 else 0.0,
    }


def reset() -> None:
    db.reset_portfolio()
    set_cash(config.STARTING_CAPITAL)
    log.info("portfolio reset to %.2f", config.STARTING_CAPITAL)
