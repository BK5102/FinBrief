"""Manage the active FinBrief portfolio stored in SQLite."""

from __future__ import annotations

import argparse
from pathlib import Path

from finbrief.db import connect, deactivate_tickers, init_db, list_active_tickers, set_active_tickers, upsert_tickers


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage FinBrief portfolio tickers")
    parser.add_argument("--db", type=Path, default=Path("data/finbrief.db"), help="SQLite database path")

    subparsers = parser.add_subparsers(dest="command", required=True)

    set_parser = subparsers.add_parser("set", help="Replace active portfolio")
    set_parser.add_argument("tickers", help="Comma-separated ticker symbols")

    add_parser = subparsers.add_parser("add", help="Add or reactivate tickers")
    add_parser.add_argument("tickers", help="Comma-separated ticker symbols")

    remove_parser = subparsers.add_parser("remove", help="Deactivate tickers")
    remove_parser.add_argument("tickers", help="Comma-separated ticker symbols")

    subparsers.add_parser("list", help="List active tickers")

    args = parser.parse_args()
    with connect(args.db) as conn:
        init_db(conn)
        if args.command == "set":
            active = set_active_tickers(conn, _split_tickers(args.tickers))
        elif args.command == "add":
            upsert_tickers(conn, _split_tickers(args.tickers))
            conn.commit()
            active = list_active_tickers(conn)
        elif args.command == "remove":
            deactivate_tickers(conn, _split_tickers(args.tickers))
            active = list_active_tickers(conn)
        else:
            active = list_active_tickers(conn)

    print(",".join(active))
    return 0


def _split_tickers(raw: str) -> list[str]:
    return [ticker.strip().upper() for ticker in raw.split(",") if ticker.strip()]


if __name__ == "__main__":
    raise SystemExit(main())
