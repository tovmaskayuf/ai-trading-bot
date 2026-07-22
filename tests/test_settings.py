"""Start-screen settings, and the store they live in.

The point of this suite is one assertion: settings are on the **durable**
store. They used to be in `db.meta`, on the ephemeral market-data disk, which
is wiped on every restart and idle spin-down -- so `starting_capital` reverted
to the $100k default and silently reseeded every new visitor at a number nobody
chose, and `initialized` reverted to False, which made the next trip through
the start screen reset that player's portfolio.

Pinned to scratch SQLite **unconditionally**, unlike the suites that double as
the Postgres check: settings are a process-global singleton, so running this
against a shared database would overwrite the live starting_capital.

    .venv/bin/python tests/test_settings.py
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402

_tmp = tempfile.TemporaryDirectory()
config.DB_PATH = Path(_tmp.name) / "test.db"
os.environ.pop("DATABASE_URL", None)
config.BASE_DIR = Path(_tmp.name)

import db  # noqa: E402
import settings  # noqa: E402
import userstore  # noqa: E402

failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("PASS  " if cond else "FAIL  ") + name +
          (f"\n      {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def main() -> None:
    db.connect()
    userstore.connect()
    print(f"backend: {userstore.backend()}\n")

    # --- Defaults on a virgin store ---------------------------------------
    fresh = settings.get()
    check("virgin store returns the defaults",
          fresh["starting_capital"] == config.STARTING_CAPITAL)
    check("virgin store is not initialized", fresh["initialized"] is False)

    # --- Which store is it on? --------------------------------------------
    # The regression guard. Everything else here would pass just as well with
    # the blob back on the ephemeral disk.
    saved = settings.save({"starting_capital": 50_000})
    check("save round-trips the value", saved["starting_capital"] == 50_000)
    check("settings are on the durable store",
          userstore.get_meta("settings") is not None)
    check("settings are NOT on the ephemeral store",
          db.get_meta("settings") is None,
          "the blob is back on the disk that gets wiped on every restart")
    check("save stamps initialized", settings.get()["initialized"] is True)

    # --- Survives the connection pool going away --------------------------
    # The in-process stand-in for a restart: proves the value came off disk
    # rather than out of a live connection's state.
    userstore._pool = None
    userstore.connect()
    check("survives losing the connection pool",
          settings.get()["starting_capital"] == 50_000)

    # --- Survives an actual new process -----------------------------------
    # The only assertion that tests the real claim end to end. A restart is a
    # new interpreter, not a reconnect, so read it from one.
    child = subprocess.run(
        [sys.executable, "-c",
         "import sys, os, pathlib; sys.path.insert(0, sys.argv[1]);"
         "os.environ.pop('DATABASE_URL', None);"
         "import config;"
         "config.BASE_DIR = pathlib.Path(sys.argv[2]);"
         "config.DB_PATH = pathlib.Path(sys.argv[2]) / 'test.db';"
         "import settings; print(settings.get()['starting_capital'])",
         str(ROOT), _tmp.name],
        capture_output=True, text=True, timeout=60)
    check("survives a genuinely new process",
          child.stdout.strip() == "50000.0",
          f"stdout={child.stdout.strip()!r} stderr={child.stderr.strip()[:300]!r}")

    # --- Read-time normalisation ------------------------------------------
    settings.save({"followed": ["SOL", "BTC", "ETH"]})
    order = settings.get()["followed"]
    check("followed comes back in registry order",
          order == [s for s in config.SYMBOLS if s in {"SOL", "BTC", "ETH"}],
          f"got {order}")

    # A symbol that has left the registry is dropped on read rather than
    # served as a ghost the rest of the app cannot resolve.
    userstore.set_meta("settings", json.dumps(
        {**settings.get(), "followed": ["BTC", "DEFUNCT"]}))
    check("symbols outside the registry are filtered on read",
          settings.get()["followed"] == ["BTC"])

    # And an unparseable blob falls back rather than raising into a request.
    userstore.set_meta("settings", "{not json")
    check("a corrupt blob falls back to defaults",
          settings.get()["starting_capital"] == config.STARTING_CAPITAL)

    # --- The one-shot carry-over ------------------------------------------
    userstore.execute("DELETE FROM app_meta WHERE key = ?", ("settings",))
    db.set_meta("settings", json.dumps({"starting_capital": 7_777.0}))
    settings.adopt_legacy()
    check("adopt_legacy moves a legacy blob across",
          settings.get()["starting_capital"] == 7_777.0)

    db.set_meta("settings", json.dumps({"starting_capital": 1_234.0}))
    settings.adopt_legacy()
    check("adopt_legacy does not overwrite once durable",
          settings.get()["starting_capital"] == 7_777.0)

    print()
    if failures:
        print(f"{len(failures)} FAILED: " + ", ".join(failures))
        sys.exit(1)
    print("all settings checks passed")


if __name__ == "__main__":
    main()
