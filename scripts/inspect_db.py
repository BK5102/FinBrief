"""Print a compact summary of a FinBrief SQLite database."""

from __future__ import annotations

import argparse
from pathlib import Path

from finbrief.db import connect, find_negative_spikes, get_negative_headlines


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect FinBrief SQLite data")
    parser.add_argument("--db", type=Path, default=Path("data/finbrief.db"), help="SQLite database path")
    parser.add_argument("--date", help="Aggregate date to inspect, YYYY-MM-DD. Defaults to latest aggregate date.")
    args = parser.parse_args()

    with connect(args.db) as conn:
        for table in ("tickers", "pipeline_runs", "headlines", "scores", "daily_aggregates"):
            count = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            print(f"{table}: {count}")

        aggregate_date = args.date or _latest_aggregate_date(conn)
        if not aggregate_date:
            print("No aggregates yet.")
            return 0

        print(f"\nAggregates for {aggregate_date}:")
        rows = conn.execute(
            """
            SELECT ticker, headline_count, weighted_score,
                   positive_count, neutral_count, negative_count, high_conf_negative_count
            FROM daily_aggregates
            WHERE aggregate_date = ?
            ORDER BY ticker
            """,
            (aggregate_date,),
        ).fetchall()
        for row in rows:
            print(
                f"{row['ticker']}: score={row['weighted_score']:.4f}, "
                f"headlines={row['headline_count']}, "
                f"pos/neu/neg={row['positive_count']}/{row['neutral_count']}/{row['negative_count']}, "
                f"high_conf_neg={row['high_conf_negative_count']}"
            )

        spikes = find_negative_spikes(conn, aggregate_date)
        print(f"\nNegative spikes: {len(spikes)}")
        for spike in spikes:
            print(
                f"{spike['ticker']}: score={spike['weighted_score']:.4f}, "
                f"threshold={spike['threshold']:.4f}, high_conf_neg={spike['high_conf_negative_count']}"
            )
            for headline in get_negative_headlines(conn, spike["ticker"], aggregate_date):
                print(
                    f"  - [{headline['confidence']:.3f}] {headline['title']} "
                    f"({headline['source']}, {headline['published_at']})"
                )
    return 0


def _latest_aggregate_date(conn) -> str | None:
    row = conn.execute("SELECT max(aggregate_date) FROM daily_aggregates").fetchone()
    return row[0] if row else None


if __name__ == "__main__":
    raise SystemExit(main())
