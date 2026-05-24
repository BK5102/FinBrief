"""FinBrief Phase 1 CLI.

Usage:
    python -m finbrief.pipeline --tickers AAPL,MSFT,NVDA
    python -m finbrief.pipeline --tickers AAPL,MSFT --pretty --out today.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv

from finbrief.db import connect, persist_pipeline_result
from finbrief.fetcher import fetch_all_today
from finbrief.scorer import score_texts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FinBrief Phase 1 pipeline")
    parser.add_argument("--tickers", required=True, help="Comma-separated ticker symbols, e.g. AAPL,MSFT,NVDA")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    parser.add_argument("--out", type=Path, help="Write JSON to file instead of stdout")
    parser.add_argument("--db", type=Path, help="Persist headlines, scores, and daily aggregates to SQLite")
    parser.add_argument("--verbose", action="store_true", help="Verbose logging")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    load_dotenv()
    finnhub_key = os.getenv("FINNHUB_API_KEY") or None

    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    if not tickers:
        parser.error("--tickers must contain at least one symbol")

    t0 = time.perf_counter()
    headlines = fetch_all_today(tickers, finnhub_key=finnhub_key)
    fetch_secs = time.perf_counter() - t0

    texts = [_score_text(h) for h in headlines]
    t1 = time.perf_counter()
    scores = score_texts(texts) if texts else []
    score_secs = time.perf_counter() - t1

    by_ticker: dict[str, list[dict]] = defaultdict(list)
    for h, s in zip(headlines, scores):
        by_ticker[h.ticker].append({**h.to_dict(), "sentiment": s})

    output = {
        "tickers": tickers,
        "counts": {t: len(by_ticker.get(t, [])) for t in tickers},
        "timings_seconds": {"fetch": round(fetch_secs, 2), "score": round(score_secs, 2)},
        "headlines_by_ticker": by_ticker,
    }

    if args.db:
        with connect(args.db) as conn:
            run_id = persist_pipeline_result(conn, tickers, headlines, scores, output["timings_seconds"])
        output["db"] = {"path": str(args.db), "pipeline_run_id": run_id}

    payload = json.dumps(output, indent=2 if args.pretty else None, ensure_ascii=False)
    if args.out:
        args.out.write_text(payload, encoding="utf-8")
        print(f"wrote {args.out} ({len(headlines)} headlines)", file=sys.stderr)
    else:
        print(payload)
    return 0


def _score_text(headline) -> str:
    """FinBERT sees title; summary appended if present (short enough that truncation handles overflow)."""
    if headline.summary and headline.summary.lower() not in headline.title.lower():
        return f"{headline.title}. {headline.summary}"
    return headline.title


if __name__ == "__main__":
    raise SystemExit(main())
