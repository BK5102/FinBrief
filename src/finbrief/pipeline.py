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
from pathlib import Path

from dotenv import load_dotenv

from finbrief.runner import run_pipeline_cycle


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FinBrief Phase 1 pipeline")
    parser.add_argument("--tickers", help="Comma-separated ticker symbols, e.g. AAPL,MSFT,NVDA")
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

    tickers = [ticker.strip().upper() for ticker in args.tickers.split(",") if ticker.strip()] if args.tickers else []
    try:
        output = run_pipeline_cycle(tickers=tickers, db_path=args.db, finnhub_key=finnhub_key)
    except ValueError as exc:
        parser.error(str(exc))

    payload = json.dumps(output, indent=2 if args.pretty else None, ensure_ascii=False)
    if args.out:
        args.out.write_text(payload, encoding="utf-8")
        print(f"wrote {args.out} ({len(headlines)} headlines)", file=sys.stderr)
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
