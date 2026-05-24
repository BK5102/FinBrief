"""Run one daily FinBrief ingest/scoring/persistence cycle."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from finbrief.db import connect, list_active_tickers, persist_pipeline_result
from finbrief.fetcher import fetch_all_today
from finbrief.pipeline import _score_text
from finbrief.scorer import score_texts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one FinBrief daily pipeline cycle")
    parser.add_argument("--db", type=Path, default=Path("data/finbrief.db"), help="SQLite database path")
    parser.add_argument("--tickers", help="Comma-separated ticker symbols. Defaults to active DB portfolio.")
    parser.add_argument("--log", type=Path, default=Path("logs/daily_runs.jsonl"), help="JSONL run log path")
    args = parser.parse_args(argv)

    load_dotenv()
    started_at = datetime.now(timezone.utc).isoformat()
    finnhub_key = os.getenv("FINNHUB_API_KEY") or None

    try:
        with connect(args.db) as conn:
            tickers = _split_tickers(args.tickers) if args.tickers else list_active_tickers(conn)
        if not tickers:
            raise RuntimeError("No tickers provided and no active tickers found in DB")

        t0 = time.perf_counter()
        headlines = fetch_all_today(tickers, finnhub_key=finnhub_key)
        fetch_seconds = time.perf_counter() - t0

        t1 = time.perf_counter()
        scores = score_texts([_score_text(headline) for headline in headlines]) if headlines else []
        score_seconds = time.perf_counter() - t1

        timings = {"fetch": round(fetch_seconds, 2), "score": round(score_seconds, 2)}
        with connect(args.db) as conn:
            run_id = persist_pipeline_result(conn, tickers, headlines, scores, timings)

        event = {
            "status": "success",
            "run_id": run_id,
            "started_at": started_at,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "tickers": tickers,
            "articles_fetched": len(headlines),
            "articles_scored": len(scores),
            "timings_seconds": timings,
        }
        _write_log(args.log, event)
        print(json.dumps(event, indent=2))
        return 0
    except Exception as exc:
        event = {
            "status": "failure",
            "started_at": started_at,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "error": str(exc),
        }
        _write_log(args.log, event)
        print(json.dumps(event, indent=2), file=sys.stderr)
        return 1


def _split_tickers(raw: str) -> list[str]:
    return [ticker.strip().upper() for ticker in raw.split(",") if ticker.strip()]


def _write_log(path: Path, event: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
