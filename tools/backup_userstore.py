"""Dump the durable user store out of Render Postgres into a JSON file.

Free Render Postgres is deleted 30 days after creation (plus a 14-day grace
period), and it takes every account, portfolio and leaderboard standing with
it. This exists so that deadline stops being load-bearing.

Needs no pg_dump -- it goes through psycopg, which is already in the venv
because the app itself depends on it.

    DATABASE_URL='postgresql://...' .venv/bin/python backup_userstore.py

`sessions` is skipped on purpose: the rows are live session tokens, they expire
on their own, and writing them to a file on disk is a liability with no upside.
"""

import datetime
import decimal
import json
import os
import sys

import psycopg

TABLES = ["users", "portfolios", "holdings", "user_trades", "user_equity"]


def _plain(value):
    """Make psycopg's richer column types survive json.dumps."""
    if isinstance(value, decimal.Decimal):
        # str, not float -- money must not pick up binary-float error.
        return str(value)
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    if isinstance(value, (bytes, memoryview)):
        return bytes(value).hex()
    return value


def main():
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL is not set. Use the External Database URL from Render.")

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = f"userstore-backup-{stamp}.json"
    dump = {"taken_at": datetime.datetime.now().isoformat(), "tables": {}}

    with psycopg.connect(url, connect_timeout=30) as conn:
        for table in TABLES:
            with conn.cursor() as cur:
                try:
                    cur.execute(f"SELECT * FROM {table}")
                except psycopg.errors.UndefinedTable:
                    # An older deployment may predate a table; that is not fatal.
                    print(f"  {table:<12} absent, skipped")
                    conn.rollback()
                    continue
                cols = [c.name for c in cur.description]
                rows = [dict(zip(cols, (_plain(v) for v in r))) for r in cur.fetchall()]
            dump["tables"][table] = rows
            print(f"  {table:<12} {len(rows):>6} rows")

    with open(out, "w") as fh:
        json.dump(dump, fh, indent=2)

    total = sum(len(r) for r in dump["tables"].values())
    print(f"\nWrote {out} -- {total} rows across {len(dump['tables'])} tables.")


if __name__ == "__main__":
    main()
