"""Paper-trading safety checks.

Runs against a scratch database so it never touches real portfolio state.
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config

# Point the DB at a scratch file *before* anything calls db.connect().
_tmp = tempfile.TemporaryDirectory()
config.DB_PATH = Path(_tmp.name) / "test.db"

import db  # noqa: E402
from trading import portfolio, strategy  # noqa: E402

failures: list[str] = []
MINUTE = 60_000


def check(name: str, ok: bool, detail: str = ""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}{f': {detail}' if detail else ''}")
    if not ok:
        failures.append(name)


def fresh():
    portfolio.reset()


def rating(composite: float, signal: str, momentum: float = 70.0) -> dict:
    return {"composite": composite, "signal": signal, "momentum": momentum,
            "risk": 60.0, "structure": 60.0, "relative": 60.0, "grade": "B"}


# --- Sizing ----------------------------------------------------------------
fresh()
equity = 100_000.0

# Risk 2% of 100k = $2000; stop distance = 2 x ATR = 2.0 -> qty = 1000.
# Priced at $10 the notional cap (100k * 0.12 / 10 = 1200) does not bind, so
# this isolates the risk-parity path.
qty = strategy.position_size(equity, price=10.0, atr=1.0)
check("sizing risk-parity", abs(qty - 1000.0) < 1e-6, f"qty={qty}")

# A tiny ATR would imply an enormous position, so the notional cap must bind:
# max = 100k * 0.12 / 100 = 120 units.
cap_qty = equity * config.MAX_POSITION_PCT / 100.0
qty = strategy.position_size(equity, price=100.0, atr=0.001)
check("sizing respects notional cap", abs(qty - cap_qty) < 1e-6, f"qty={qty} cap={cap_qty}")

check("sizing rejects zero ATR", strategy.position_size(equity, 100.0, 0.0) == 0.0)
check("sizing rejects zero price", strategy.position_size(equity, 0.0, 1.0) == 0.0)

# --- Entry -----------------------------------------------------------------
fresh()
t0 = 1_000_000_000_000
action = strategy.evaluate("BTC", rating(75, "BUY"), price=100.0, atr=1.0, now_ms=t0)
check("entry on BUY", action == "ENTRY", str(action))

pos = portfolio.position_for("BTC")
check("position opened", pos is not None)
check("stop set below entry", pos and pos["stop"] < pos["entry_price"],
      f"stop={pos['stop'] if pos else None} entry={pos['entry_price'] if pos else None}")
check("take-profit above entry", pos and pos["take_profit"] > pos["entry_price"])
check("cash decreased", portfolio.cash() < config.STARTING_CAPITAL,
      f"cash={portfolio.cash():.2f}")

# No double-entry while already holding.
action = strategy.evaluate("BTC", rating(75, "BUY"), price=100.0, atr=1.0, now_ms=t0 + MINUTE)
check("no duplicate entry", action is None, str(action))

# HOLD must never open a position.
fresh()
action = strategy.evaluate("ETH", rating(60, "HOLD"), price=100.0, atr=1.0, now_ms=t0)
check("no entry on HOLD", action is None and portfolio.position_for("ETH") is None)

# --- Max open positions ----------------------------------------------------
# Every asset gets a BUY, so the position limit -- not cash -- must be what
# stops the book growing. This only holds because MAX_POSITION_PCT *
# MAX_OPEN_POSITIONS <= 1.0; if that invariant breaks, cash binds first and
# this assertion is what catches it.
fresh()
for i, asset in enumerate(config.ASSETS):
    strategy.evaluate(asset.symbol, rating(75, "BUY"), price=100.0, atr=5.0,
                      now_ms=t0 + i)
n_open = len(portfolio.open_positions())
check("max open positions enforced", n_open == config.MAX_OPEN_POSITIONS,
      f"open={n_open} limit={config.MAX_OPEN_POSITIONS}")
check("position sizing invariant holds",
      config.MAX_POSITION_PCT * config.MAX_OPEN_POSITIONS <= 1.0,
      f"{config.MAX_POSITION_PCT} * {config.MAX_OPEN_POSITIONS}")

# --- Stop-loss -------------------------------------------------------------
fresh()
strategy.evaluate("BTC", rating(75, "BUY"), price=100.0, atr=1.0, now_ms=t0)
pos = portfolio.position_for("BTC")
stop_price = pos["stop"]

# Price at the stop must close the position immediately, even inside min-hold.
action = strategy.evaluate("BTC", rating(75, "BUY"), price=stop_price - 0.01,
                           atr=1.0, now_ms=t0 + MINUTE)
check("stop-loss fires", action == "STOP", str(action))
check("position closed by stop", portfolio.position_for("BTC") is None)
trades = portfolio.closed_trades()
check("stop trade booked a loss", trades and trades[0]["pnl"] < 0,
      f"pnl={trades[0]['pnl'] if trades else None}")
check("stop exit_reason recorded", trades and trades[0]["exit_reason"] == "stop-loss")

# --- Take-profit -----------------------------------------------------------
fresh()
strategy.evaluate("BTC", rating(75, "BUY"), price=100.0, atr=1.0, now_ms=t0)
pos = portfolio.position_for("BTC")
action = strategy.evaluate("BTC", rating(75, "BUY"), price=pos["take_profit"] + 0.01,
                           atr=1.0, now_ms=t0 + MINUTE)
check("take-profit fires", action == "TAKE_PROFIT", str(action))
trades = portfolio.closed_trades()
check("take-profit booked a gain", trades and trades[0]["pnl"] > 0,
      f"pnl={trades[0]['pnl'] if trades else None}")

# --- Hysteresis / min-hold -------------------------------------------------
fresh()
strategy.evaluate("BTC", rating(75, "BUY"), price=100.0, atr=1.0, now_ms=t0)

# A weak rating inside the min-hold window must NOT close the position.
action = strategy.evaluate("BTC", rating(40, "SELL"), price=100.0, atr=1.0,
                           now_ms=t0 + 5 * MINUTE)
check("min-hold suppresses early exit", action is None and portfolio.position_for("BTC"),
      f"action={action}")

# Past the min-hold window the same rating should exit.
action = strategy.evaluate("BTC", rating(40, "SELL"), price=100.0, atr=1.0,
                           now_ms=t0 + (config.MIN_HOLD_MINUTES + 1) * MINUTE)
check("exit allowed after min-hold", action == "EXIT", str(action))

# A composite in the dead band (between EXIT and BUY thresholds) must not exit.
fresh()
strategy.evaluate("BTC", rating(75, "BUY"), price=100.0, atr=1.0, now_ms=t0)
dead_band = (config.EXIT_THRESHOLD + config.BUY_THRESHOLD) / 2
action = strategy.evaluate("BTC", rating(dead_band, "HOLD"), price=100.0, atr=1.0,
                           now_ms=t0 + 120 * MINUTE)
check("dead band holds position", action is None and portfolio.position_for("BTC"),
      f"composite={dead_band} action={action}")

# --- Churn resistance ------------------------------------------------------
# Oscillate the composite around the BUY threshold for many cycles. With
# hysteresis this must not produce a stream of open/close pairs.
fresh()
ts = t0
for i in range(60):
    comp = 72.0 if i % 2 == 0 else 68.0
    sig = "BUY" if comp >= config.BUY_THRESHOLD else "HOLD"
    strategy.evaluate("BTC", rating(comp, sig), price=100.0, atr=1.0, now_ms=ts)
    ts += 2 * MINUTE
n_trades = len(portfolio.all_trades())
check("oscillation does not churn", n_trades <= 2,
      f"{n_trades} trades from 60 oscillating cycles")

# --- Trailing stop ---------------------------------------------------------
# ATR of 10 puts take-profit at ~130, so moving to 120 exercises the trailing
# logic without tripping the take-profit exit first.
fresh()
strategy.evaluate("BTC", rating(75, "BUY"), price=100.0, atr=10.0, now_ms=t0)
initial_stop = portfolio.position_for("BTC")["stop"]
strategy.evaluate("BTC", rating(75, "BUY"), price=120.0, atr=10.0, now_ms=t0 + MINUTE)
pos = portfolio.position_for("BTC")
check("position survives to trail", pos is not None)
raised = pos["stop"] if pos else 0
check("trailing stop ratchets up", raised > initial_stop, f"{initial_stop:.2f} -> {raised:.2f}")
check("high-water persisted", pos and abs(pos["high_water"] - 120.0) < 1e-6,
      f"high_water={pos['high_water'] if pos else None}")

# It must never loosen when price falls back.
strategy.evaluate("BTC", rating(75, "BUY"), price=110.0, atr=10.0, now_ms=t0 + 2 * MINUTE)
after = portfolio.position_for("BTC")["stop"]
check("trailing stop never loosens", after >= raised, f"{raised:.2f} -> {after:.2f}")
check("high-water retained on pullback",
      abs(portfolio.position_for("BTC")["high_water"] - 120.0) < 1e-6)

# --- Cash integrity --------------------------------------------------------
fresh()
ts = t0
for i, s in enumerate([a.symbol for a in config.ASSETS]):
    strategy.evaluate(s, rating(75, "BUY"), price=100.0, atr=0.5, now_ms=ts + i)
check("cash never negative", portfolio.cash() >= 0, f"cash={portfolio.cash():.2f}")

prices = {a.symbol: 100.0 for a in config.ASSETS}
eq = portfolio.equity(prices)
# Equity should be starting capital minus fees/slippage paid, not wildly off.
check("equity conserved within costs",
      config.STARTING_CAPITAL * 0.97 <= eq <= config.STARTING_CAPITAL,
      f"equity={eq:.2f}")

print()
if failures:
    print(f"{len(failures)} FAILURE(S): {failures}")
    sys.exit(1)
print("all strategy checks passed")
