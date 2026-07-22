"""Concurrent access to the durable store.

The DB-touching routes are plain `def`, so FastAPI runs them in a threadpool
and real overlap is possible. This suite pins the two properties that must
hold however the connection layer is built:

  1. Concurrent trades do not lose updates.
  2. One thread's rollback never discards another thread's committed work.

Both held under the original single-connection-plus-global-lock design by
serialising everything. They must still hold once connections are pooled, and
that is the point of writing them down: the guarantee is the invariant, not
the lock that used to provide it.

    .venv/bin/python tests/test_concurrency.py
    DATABASE_URL=postgresql://… .venv/bin/python tests/test_concurrency.py

**Run the Postgres form against a scratch database, never the live one.** It
creates accounts it does not clean up and inserts ~160k equity rows to make the
throughput comparison meaningful, which on the free plan's 1 GB ceiling is not
something to spend on a test run.

Running it there matters, though: DIALECT.for_update is an empty string on
SQLite, so the local run exercises the BEGIN IMMEDIATE path and leaves the row
locking that production depends on completely untested.
"""

import os
import sys
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config

if not os.getenv("DATABASE_URL"):
    config.BASE_DIR = Path(tempfile.mkdtemp())

import accounts  # noqa: E402
import portfolio as pf  # noqa: E402
import userstore  # noqa: E402

failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("PASS  " if cond else "FAIL  ") + name +
          (f"\n      {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def test_no_lost_updates() -> None:
    """24 concurrent buys must remove exactly 24 buys' worth of cash.

    The historical failure took $110 out of cash instead of $240: each thread
    read the opening balance, computed a closing one from it, and wrote that
    absolute value back, so all but one payment vanished. Trades read through
    the open transaction's cursor now, which is what makes this hold.
    """
    tag = uuid.uuid4().hex[:8]
    u = accounts.create_user(f"conc_{tag}", "password12345", 10_000)["id"]
    n, spend = 24, 10.0

    barrier = threading.Barrier(n)

    def one(_i: int) -> str | None:
        barrier.wait()          # release together, to maximise overlap
        try:
            pf.buy(u, "BTC", 100.0, int(time.time() * 1000), usd=spend)
            return None
        except Exception as exc:                      # noqa: BLE001
            return f"{type(exc).__name__}: {exc}"

    with ThreadPoolExecutor(max_workers=n) as ex:
        errors = [e for e in ex.map(one, range(n)) if e]

    check("every concurrent buy succeeded", not errors,
          f"{len(errors)} failed, first: {errors[0] if errors else ''}")

    cash = pf.cash(u)
    expected = 10_000 - n * spend
    check(f"{n} concurrent buys removed exactly {n} x ${spend:g}",
          abs(cash - expected) < 1e-6,
          f"cash={cash:.4f} expected={expected:.4f} "
          f"(short by {cash - expected:+.2f})")

    held = pf.holding_for(u, "BTC")
    check("holding quantity matches the money spent",
          held is not None and abs(held["qty"] * held["avg_cost"]
                                   - n * spend) < 1e-6)


def test_rollback_is_thread_local() -> None:
    """A failing transaction must not roll back a concurrent one.

    This is the property the single shared connection was protecting by
    serialising: commit and rollback act on a connection, so two threads
    inside tx() on the *same* connection would let one thread's rollback
    discard the other's uncommitted statements.
    """
    tag = uuid.uuid4().hex[:8]
    keeper = accounts.create_user(f"keep_{tag}", "password12345", 10_000)["id"]
    loser = accounts.create_user(f"lose_{tag}", "password12345", 10_000)["id"]

    started = threading.Event()
    may_finish = threading.Event()

    def doomed() -> None:
        """Hold a transaction open, write, then fail so it rolls back."""
        try:
            with userstore.tx() as cur:
                cur.execute(userstore.DIALECT.convert(
                    "UPDATE portfolios SET cash = ? WHERE user_id = ?"),
                    (1.0, loser))
                started.set()
                may_finish.wait(5)
                raise RuntimeError("forced rollback")
        except RuntimeError:
            pass

    def committer() -> None:
        started.wait(5)
        with userstore.tx() as cur:
            cur.execute(userstore.DIALECT.convert(
                "UPDATE portfolios SET cash = ? WHERE user_id = ?"),
                (4_242.0, keeper))
        may_finish.set()

    a = threading.Thread(target=doomed)
    b = threading.Thread(target=committer)
    a.start(); b.start()
    a.join(10); b.join(10)

    check("the committed write survived the other thread's rollback",
          abs(pf.cash(keeper) - 4_242.0) < 1e-6,
          f"keeper cash={pf.cash(keeper):.2f}, expected 4242.00")
    check("the rolled-back write did not persist",
          abs(pf.cash(loser) - 10_000.0) < 1e-6,
          f"loser cash={pf.cash(loser):.2f}, expected 10000.00")


def test_concurrent_reads() -> None:
    """Parallel reads return complete, uncorrupted rows."""
    tag = uuid.uuid4().hex[:8]
    ids = [accounts.create_user(f"r{i}_{tag}", "password12345", 10_000)["id"]
           for i in range(6)]

    def read(uid: int) -> bool:
        for _ in range(25):
            snap = pf.snapshot(uid, {"BTC": 100.0})
            if snap is None or snap.get("cash") != 10_000:
                return False
        return True

    with ThreadPoolExecutor(max_workers=len(ids)) as ex:
        ok = list(ex.map(read, ids))
    check("concurrent readers all saw consistent state", all(ok))


def report_throughput() -> None:
    """Informational only -- timings are not asserted, they are too flaky.

    Two workloads, because one number here is actively misleading. A trivial
    indexed lookup against a local SQLite file costs microseconds: there is no
    I/O to overlap, and the GIL serialises the Python half, so pooling only
    adds queue and context-switch overhead and comes out *slower*. A query that
    spends real time inside the driver -- which is every query in production,
    where the store is Postgres across a network -- releases the GIL while it
    waits, and that is the time pooling recovers.

    Reporting only the first would say the pool made things worse; only the
    second would oversell it. Both are true of different workloads.
    """
    tag = uuid.uuid4().hex[:8]
    ids = [accounts.create_user(f"t{i}_{tag}", "password12345", 10_000)["id"]
           for i in range(8)]

    # Enough rows that the aggregate below costs milliseconds, standing in for
    # a network round trip.
    with userstore.tx() as cur:
        c = userstore.DIALECT.convert
        cur.executemany(c(
            "INSERT INTO user_equity (user_id, ts, cash, invested, "
            "market_value, total, realized, fees) VALUES (?,?,?,?,?,?,?,?)"),
            [(u, 1_700_000_000_000 + i, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0)
             for u in ids for i in range(20_000)])

    def trivial(uid: int) -> None:
        for _ in range(40):
            userstore.query_one(
                "SELECT cash FROM portfolios WHERE user_id = ?", (uid,))

    def substantial(uid: int) -> None:
        for _ in range(6):
            userstore.query_one(
                "SELECT COUNT(*) AS n, SUM(total) AS s FROM user_equity "
                "WHERE user_id = ?", (uid,))

    print()
    for label, fn in (("trivial reads (microseconds)", trivial),
                      ("real work (driver releases GIL)", substantial)):
        t = time.perf_counter()
        with ThreadPoolExecutor(max_workers=8) as ex:
            list(ex.map(fn, ids))
        par = time.perf_counter() - t
        t = time.perf_counter()
        for uid in ids:
            fn(uid)
        seq = time.perf_counter() - t
        verdict = ("overhead dominates" if seq / par < 1.0
                   else "serialised" if seq / par < 1.3 else "concurrent")
        print(f"      {label:34} {par * 1000:7.1f} ms parallel | "
              f"{seq * 1000:7.1f} ms sequential | {seq / par:5.2f}x  {verdict}")


def main() -> None:
    userstore.connect()
    print(f"backend: {userstore.backend()}\n")
    test_no_lost_updates()
    test_rollback_is_thread_local()
    test_concurrent_reads()
    report_throughput()

    print()
    if failures:
        print(f"{len(failures)} FAILED: " + ", ".join(failures))
        sys.exit(1)
    print("all concurrency checks passed")


if __name__ == "__main__":
    main()
