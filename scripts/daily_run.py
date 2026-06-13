"""Run one daily FinBrief ingest/scoring/persistence cycle."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from finbrief.runner import run_pipeline_cycle


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
        tickers = _split_tickers(args.tickers) if args.tickers else []
        output = run_pipeline_cycle(tickers=tickers, db_path=args.db, finnhub_key=finnhub_key)

        event = {
            "status": "success",
            "run_id": output.get("db", {}).get("pipeline_run_id"),
            "started_at": started_at,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "tickers": output["tickers"],
            "articles_fetched": sum(output["counts"].values()),
            "articles_scored": sum(output["counts"].values()),
            "timings_seconds": output["timings_seconds"],
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
