"""FastAPI app: JSON API + SSE stream + the dashboard.

Runs the polling engine as a background task inside the same process, so a
single `uvicorn server:app` gives you both the always-on bot and the UI.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import config
import db
import engine
import providers
from analytics import rating
from trading import manual, portfolio

log = logging.getLogger("server")

_engine_task: asyncio.Task | None = None


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    global _engine_task
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)-12s %(message)s",
        datefmt="%H:%M:%S",
    )
    # Quiet httpx's per-request logging; the engine already logs each cycle.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    db.connect()
    _engine_task = asyncio.create_task(engine.loop())
    log.info("engine started (cycle=%ds)", config.CYCLE_SECONDS)
    try:
        yield
    finally:
        if _engine_task:
            _engine_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await _engine_task
        await providers.aclose()


app = FastAPI(title="AI Crypto Trading Bot", lifespan=lifespan)


def _current_prices() -> dict[str, float]:
    out: dict[str, float] = {}
    for symbol in config.SYMBOLS:
        asset = engine.STATE["assets"].get(symbol)
        if asset and asset.get("price"):
            out[symbol] = asset["price"]
        else:
            snap = db.latest_snapshot(symbol)
            if snap and snap.get("price"):
                out[symbol] = snap["price"]
    return out


# --- API -------------------------------------------------------------------


@app.get("/api/overview")
async def overview() -> dict[str, Any]:
    """Everything the main grid needs in one call."""
    assets = engine.STATE.get("assets") or {}

    # Before the first cycle completes, serve the last persisted state so the
    # dashboard renders immediately on a restart instead of sitting empty.
    if not assets:
        assets = {}
        for symbol in config.SYMBOLS:
            snap = db.latest_snapshot(symbol) or {}
            rt = db.latest_rating(symbol) or {}
            asset = config.BY_SYMBOL[symbol]
            assets[symbol] = {
                "symbol": symbol, "name": asset.name, "thesis": asset.thesis,
                "source": asset.price_source,
                "price": snap.get("price"), "chg_24h": snap.get("chg_24h"),
                "mcap": snap.get("mcap"), "rank": snap.get("rank"),
                "stale": bool(snap.get("stale")),
                "momentum": rt.get("momentum"), "risk": rt.get("risk"),
                "structure": rt.get("structure"), "relative": rt.get("relative"),
                "composite": rt.get("composite"), "grade": rt.get("grade"),
                "signal": rt.get("signal"), "detail": {},
            }

    return {
        "assets": list(assets.values()),
        "cycle": engine.STATE.get("cycle", 0),
        "updated_at": engine.STATE.get("updated_at"),
        "actions": engine.STATE.get("actions", []),
        "errors": engine.STATE.get("errors", []),
        "weights": config.DEFAULT_WEIGHTS,
        "thresholds": {
            "buy": config.BUY_THRESHOLD,
            "strong_buy": config.STRONG_BUY_THRESHOLD,
            "exit": config.EXIT_THRESHOLD,
            "strong_sell": config.STRONG_SELL_THRESHOLD,
        },
        "cycle_seconds": config.CYCLE_SECONDS,
    }


@app.get("/api/asset/{symbol}")
async def asset_detail(symbol: str) -> dict[str, Any]:
    symbol = symbol.upper()
    if symbol not in config.BY_SYMBOL:
        raise HTTPException(404, f"unknown symbol {symbol}")

    asset = config.BY_SYMBOL[symbol]
    live = (engine.STATE.get("assets") or {}).get(symbol, {})
    candles = db.get_candles(symbol, limit=300)

    return {
        "symbol": symbol,
        "name": asset.name,
        "thesis": asset.thesis,
        "source": asset.price_source,
        "live": live,
        "detail": live.get("detail", {}),
        "candles": [
            {"t": c["open_time"], "o": c["o"], "h": c["h"],
             "l": c["l"], "c": c["c"], "v": c["v"]}
            for c in candles
        ],
        "rating_history": db.rating_history(symbol, limit=300),
        "position": portfolio.position_for(symbol),
    }


@app.get("/api/portfolio")
async def portfolio_view() -> dict[str, Any]:
    prices = _current_prices()
    positions = []
    for p in portfolio.open_positions():
        price = prices.get(p["symbol"], p["entry_price"])
        cost = p["qty"] * p["entry_price"]
        value = p["qty"] * price
        positions.append({
            **p,
            "current_price": price,
            "value": value,
            "unrealized_pnl": value - cost,
            "unrealized_pct": ((value / cost - 1) * 100) if cost else 0.0,
        })

    return {
        "stats": portfolio.stats(prices),
        "positions": positions,
        "trades": portfolio.all_trades(limit=200),
        "equity_curve": db.query(
            "SELECT ts, total, cash, positions_value, drawdown_pct "
            "FROM equity ORDER BY ts DESC LIMIT 500"
        )[::-1],
        "config": {
            "starting_capital": config.STARTING_CAPITAL,
            "risk_per_trade": config.RISK_PER_TRADE,
            "max_position_pct": config.MAX_POSITION_PCT,
            "max_open_positions": config.MAX_OPEN_POSITIONS,
            "stop_atr_mult": config.STOP_ATR_MULT,
            "take_profit_atr_mult": config.TAKE_PROFIT_ATR_MULT,
            "fee_rate": config.FEE_RATE,
            "min_hold_minutes": config.MIN_HOLD_MINUTES,
        },
    }


@app.post("/api/weights")
async def rescore(payload: dict[str, float]) -> dict[str, Any]:
    """Recompute composites under caller-supplied axis weights.

    The dashboard does this client-side for instant feedback; this endpoint
    exists for API consumers and to keep the two implementations honest.
    """
    weights = {k: float(payload.get(k, 0)) for k in config.DEFAULT_WEIGHTS}
    total = sum(weights.values())
    if total <= 0:
        raise HTTPException(400, "weights must sum to more than zero")
    weights = {k: v / total for k, v in weights.items()}

    out = []
    for symbol, a in (engine.STATE.get("assets") or {}).items():
        sub = {k: a.get(k) for k in config.DEFAULT_WEIGHTS}
        composite = rating.composite_score(sub, weights)
        out.append({
            "symbol": symbol,
            "composite": round(composite, 2) if composite is not None else None,
            "grade": rating.grade_for(composite) if composite is not None else "-",
        })
    out.sort(key=lambda r: r["composite"] or 0, reverse=True)
    return {"weights": weights, "assets": out}


@app.post("/api/portfolio/reset")
async def reset_portfolio() -> dict[str, str]:
    portfolio.reset()
    return {"status": "reset", "capital": f"{config.STARTING_CAPITAL:.2f}"}


# --- Manual (user-driven) paper trading ------------------------------------


@app.get("/api/manual")
async def manual_view() -> dict[str, Any]:
    """The user's own portfolio, marked to the latest prices."""
    return manual.snapshot(_current_prices())


@app.post("/api/manual/trade")
async def manual_trade(payload: dict[str, Any]) -> dict[str, Any]:
    """Execute a simulated buy or sell at the current live price.

    Price is taken server-side rather than from the client, so a stale page
    cannot fill at an old quote.
    """
    symbol = str(payload.get("symbol", "")).upper()
    side = str(payload.get("side", "")).upper()

    if symbol not in config.BY_SYMBOL:
        raise HTTPException(400, f"unknown symbol {symbol}")
    if side not in ("BUY", "SELL"):
        raise HTTPException(400, "side must be BUY or SELL")

    price = _current_prices().get(symbol)
    if not price:
        raise HTTPException(409, f"no live price for {symbol} yet — try again shortly")

    ts = db.now_ms()
    try:
        if side == "BUY":
            result = manual.buy(
                symbol, price, ts,
                usd=_opt_float(payload, "usd"),
                qty=_opt_float(payload, "qty"),
            )
        else:
            result = manual.sell(
                symbol, price, ts,
                qty=_opt_float(payload, "qty"),
                fraction=_opt_float(payload, "fraction"),
            )
    except manual.TradeError as e:
        raise HTTPException(400, str(e)) from None

    return {"ok": True, "trade": result, "portfolio": manual.snapshot(_current_prices())}


def _opt_float(payload: dict[str, Any], key: str) -> float | None:
    v = payload.get(key)
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        raise HTTPException(400, f"{key} must be a number") from None


@app.post("/api/manual/reset")
async def manual_reset() -> dict[str, str]:
    manual.reset()
    return {"status": "reset", "capital": f"{config.STARTING_CAPITAL:.2f}"}


@app.get("/api/stream")
async def stream(request: Request) -> StreamingResponse:
    """Server-sent events: one message per completed engine cycle."""
    async def gen():
        q = engine.subscribe()
        try:
            # Tell a reconnecting client where things stand immediately.
            yield f"data: {json.dumps({'cycle': engine.STATE.get('cycle', 0)})}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    cycle = await asyncio.wait_for(q.get(), timeout=30.0)
                    payload = {
                        "cycle": cycle,
                        "updated_at": engine.STATE.get("updated_at"),
                        "actions": engine.STATE.get("actions", []),
                    }
                    yield f"data: {json.dumps(payload)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            engine.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    })


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {
        "running": engine.STATE.get("running", False),
        "cycle": engine.STATE.get("cycle", 0),
        "updated_at": engine.STATE.get("updated_at"),
        "errors": engine.STATE.get("errors", []),
        "assets_tracked": len(config.SYMBOLS),
    }


# --- Dashboard -------------------------------------------------------------

if config.STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")


@app.get("/")
async def index() -> FileResponse:
    path = config.STATIC_DIR / "dashboard.html"
    if not path.exists():
        raise HTTPException(404, "dashboard.html not found")
    return FileResponse(path)
