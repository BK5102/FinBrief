"""Backfill FinBrief headlines from Finnhub date windows."""

from __future__ import annotations

import argparse
import os
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

from finbrief.db import connect, list_active_tickers, persist_pipeline_result
from finbrief.fetcher import fetch_finnhub_range, headline_dedupe_key
from finbrief.pipeline import _score_text
from finbrief.scorer import score_texts


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill FinBrief SQLite data from Finnhub")
    parser.add_argument("--db", type=Path, default=Path("data/finbrief.db"), help="SQLite database path")
    parser.add_argument("--tickers", help="Comma-separated ticker symbols. Defaults to active DB portfolio.")
    parser.add_argument("--days", type=int, default=7, help="Number of calendar days to backfill, including today")
    parser.add_argument("--from-date", help="Start date YYYY-MM-DD. Overrides --days when paired with --to-date.")
    parser.add_argument("--to-date", help="End date YYYY-MM-DD. Defaults to today UTC with --from-date.")
    args = parser.parse_args()

    load_dotenv()
    api_key = os.getenv("FINNHUB_API_KEY")
    if not api_key:
        raise SystemExit("FINNHUB_API_KEY is required in .env for Finnhub backfill")

    with connect(args.db) as conn:
        tickers = _parse_tickers(args.tickers) if args.tickers else list_active_tickers(conn)

    if not tickers:
        raise SystemExit("No tickers provided and no active tickers found in the DB")

    start, end = _date_window(args)

    fetch_start = time.perf_counter()
    headlines = []
    for ticker in tickers:
        headlines.extend(fetch_finnhub_range(ticker, api_key, start=start, end=end))
    headlines = _dedupe_headlines(headlines)
    fetch_seconds = time.perf_counter() - fetch_start

    score_start = time.perf_counter()
    scores = score_texts([_score_text(headline) for headline in headlines]) if headlines else []
    score_seconds = time.perf_counter() - score_start

    with connect(args.db) as conn:
        run_id = persist_pipeline_result(
            conn,
            tickers,
            headlines,
            scores,
            {"fetch": round(fetch_seconds, 2), "score": round(score_seconds, 2)},
        )

    counts = Counter(headline.ticker for headline in headlines)
    print(f"run_id={run_id}")
    print(f"window={start.date().isoformat()}..{end.date().isoformat()}")
    print(f"headlines={len(headlines)}")
    print(f"counts={dict(sorted(counts.items()))}")
    print(f"fetch_seconds={fetch_seconds:.2f}")
    print(f"score_seconds={score_seconds:.2f}")
    return 0


def _date_window(args) -> tuple[datetime, datetime]:
    if args.from_date:
        start = _parse_date(args.from_date)
        end = _parse_date(args.to_date) if args.to_date else datetime.now(timezone.utc)
    else:
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=max(args.days - 1, 0))

    start = start.replace(hour=0, minute=0, second=0, microsecond=0)
    end = end.replace(hour=23, minute=59, second=59, microsecond=999999)
    if start > end:
        raise SystemExit("--from-date must be earlier than or equal to --to-date")
    return start, end


def _parse_date(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def _parse_tickers(raw: str) -> list[str]:
    return [ticker.strip().upper() for ticker in raw.split(",") if ticker.strip()]


def _dedupe_headlines(headlines):
    seen: set[str] = set()
    deduped = []
    for headline in headlines:
        key = headline_dedupe_key(headline)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(headline)
    return deduped


if __name__ == "__main__":
    raise SystemExit(main())
