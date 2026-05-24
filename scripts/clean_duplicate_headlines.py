"""Remove existing duplicate/noisy headline rows and recompute affected aggregates."""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from finbrief.db import connect, recompute_daily_aggregate
from finbrief.fetcher import Headline, is_relevant_to_ticker, normalize_title


def main() -> int:
    parser = argparse.ArgumentParser(description="Clean duplicate headlines from FinBrief SQLite")
    parser.add_argument("--db", type=Path, default=Path("data/finbrief.db"), help="SQLite database path")
    parser.add_argument("--drop-irrelevant", action="store_true", help="Also remove rows that fail ticker relevance checks")
    parser.add_argument("--dry-run", action="store_true", help="Report duplicates without deleting")
    args = parser.parse_args()

    with connect(args.db) as conn:
        groups = defaultdict(list)
        rows = conn.execute(
            """
            SELECT id, ticker, title, summary, url, published_at, source
            FROM headlines
            ORDER BY ticker, published_at, id
            """
        ).fetchall()
        row_dicts = [dict(row) for row in rows]
        for row in row_dicts:
            key = group_key(row)
            groups[key].append(dict(row))

        duplicate_groups = {key: values for key, values in groups.items() if len(values) > 1}
        delete_ids = []
        affected = set()
        for key, values in duplicate_groups.items():
            keep = choose_keeper(values)
            for row in values:
                if row["id"] == keep["id"]:
                    continue
                delete_ids.append(row["id"])
                affected.add((row["ticker"], row["published_at"][:10]))

        irrelevant_rows = []
        if args.drop_irrelevant:
            duplicate_delete_ids = set(delete_ids)
            for row in row_dicts:
                if row["id"] in duplicate_delete_ids:
                    continue
                headline = Headline(
                    ticker=row["ticker"],
                    title=row["title"],
                    summary=row["summary"],
                    url=row["url"],
                    source=row["source"],
                    published_at=row["published_at"],
                )
                if not is_relevant_to_ticker(headline):
                    irrelevant_rows.append(row)
                    delete_ids.append(row["id"])
                    affected.add((row["ticker"], row["published_at"][:10]))

        print(f"duplicate_groups={len(duplicate_groups)}")
        print(f"irrelevant_rows={len(irrelevant_rows)}")
        print(f"rows_to_delete={len(set(delete_ids))}")

        if args.dry_run:
            return 0

        conn.executemany("DELETE FROM headlines WHERE id = ?", [(row_id,) for row_id in sorted(set(delete_ids))])
        for ticker, aggregate_date in sorted(affected):
            recompute_daily_aggregate(conn, ticker, aggregate_date)
        conn.commit()
    return 0


def choose_keeper(rows: list[dict]) -> dict:
    source_rank = {"finnhub": 0}
    return sorted(rows, key=lambda row: (source_rank.get(row["source"], 1), row["id"]))[0]


def group_key(row: dict) -> tuple[str, str, str]:
    return (row["ticker"], row["published_at"][:10], normalize_title(row["title"]))


if __name__ == "__main__":
    raise SystemExit(main())
