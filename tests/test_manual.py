"""Portfolio accounting checks.

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
import settings  # noqa: E402
from trading import manual  # noqa: E402

failures: list[str] = []
TS = 1_000_000_000_000


def check(name: str, ok: bool, detail: str = ""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}{f': {detail}' if detail else ''}")
    if not ok:
        failures.append(name)


# --- Settings-driven starting capital --------------------------------------
settings.save({"starting_capital": 50_000, "followed": ["BTC", "ETH"], "language": "hy"})
manual.reset()
check("starting capital from settings", manual.cash() == 50_000, f"cash={manual.cash()}")
check("settings round-trip", settings.get()["followed"] == ["BTC", "ETH"])
check("language persisted", settings.get()["language"] == "hy")

for bad, why in [({"starting_capital": 5}, "below minimum"),
                 ({"starting_capital": 99_999_999}, "above maximum"),
                 ({"followed": []}, "empty followed"),
                 ({"followed": ["FAKE"]}, "unknown symbol"),
                 ({"language": "xx"}, "unsupported language")]:
    try:
        settings.save(bad)
        check(f"settings reject {why}", False, "no error raised")
    except ValueError:
        check(f"settings reject {why}", True)

settings.save({"starting_capital": 100_000, "followed": list(config.SYMBOLS)})
manual.reset()

# --- Average cost basis -----------------------------------------------------
manual.buy("BTC", 60_000.0, TS, usd=30_000)
manual.buy("BTC", 80_000.0, TS, usd=20_000)
h = manual.holding_for("BTC")
# 30k at 60k plus 20k at 80k, fees included in basis.
expected_qty = (30_000 / 1.001) / 60_000 + (20_000 / 1.001) / 80_000
check("avg cost merges lots", abs(h["qty"] - expected_qty) < 1e-9, f"qty={h['qty']}")
check("basis includes fees", abs(h["qty"] * h["avg_cost"] - 50_000) < 0.01,
      f"basis={h['qty'] * h['avg_cost']:.2f}")

# --- All-in dollar amounts --------------------------------------------------
check("buy is all-in", abs(manual.cash() - 50_000) < 0.01, f"cash={manual.cash():.2f}")

# --- Partial sell books proportional P&L ------------------------------------
before = manual.cash()
r = manual.sell("BTC", 70_000.0, TS, fraction=0.5)
check("partial sell leaves half", abs(manual.holding_for("BTC")["qty"] - expected_qty / 2) < 1e-9)
check("sell proceeds to cash", manual.cash() > before)

# --- Flat round-trip loses exactly the fees ---------------------------------
manual.reset()
manual.buy("ETH", 2_000.0, TS, usd=10_000)
manual.sell("ETH", 2_000.0, TS, fraction=1.0)
s = manual.snapshot({"ETH": 2_000.0})
check("round-trip loses only fees",
      abs((100_000 - s["total"]) - s["fees_paid"]) < 0.01,
      f"loss={100_000 - s['total']:.4f} fees={s['fees_paid']:.4f}")
check("full sell clears holding", manual.holding_for("ETH") is None)

# --- Guards -----------------------------------------------------------------
for label, fn in [
    ("sell without holding", lambda: manual.sell("ADA", 1.0, TS, fraction=1.0)),
    ("buy beyond cash", lambda: manual.buy("ETH", 2_000.0, TS, usd=9_999_999)),
    ("negative amount", lambda: manual.buy("ETH", 2_000.0, TS, usd=-5)),
    ("unknown symbol", lambda: manual.buy("FAKE", 1.0, TS, usd=10)),
    ("zero price", lambda: manual.buy("ETH", 0.0, TS, usd=10)),
    ("oversell", lambda: (manual.buy("ETH", 2_000.0, TS, usd=1_000),
                          manual.sell("ETH", 2_000.0, TS, qty=999.0))),
]:
    try:
        fn()
        check(f"guard: {label}", False, "no error raised")
    except manual.TradeError:
        check(f"guard: {label}", True)

# --- Equity history ---------------------------------------------------------
manual.reset()
check("reset seeds equity history",
      len(db.manual_equity_series(None)) == 1)
manual.record_equity(TS + 60_000, {})
manual.record_equity(TS + 120_000, {})
series = db.manual_equity_series(None)
check("equity accumulates", len(series) >= 2, f"points={len(series)}")
check("equity total tracks cash", abs(series[-1]["total"] - manual.cash()) < 0.01)

print()
if failures:
    print(f"{len(failures)} FAILURE(S): {failures}")
    sys.exit(1)
print("all portfolio checks passed")
