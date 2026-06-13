"""Simple local scheduler for FinBrief daily runs.

For unattended use, Windows Task Scheduler or cron can call `scripts/daily_run.py`
directly. This helper is convenient while developing because it keeps one local
process alive and triggers the run at the configured wall-clock time.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run FinBrief daily at a local wall-clock time")
    parser.add_argument(
        "--time",
        default=os.getenv("REFRESH_TIME", "07:00"),
        help="Local run time in HH:MM format (default: REFRESH_TIME env var or 07:00)",
    )
    parser.add_argument("--db", type=Path, default=Path("data/finbrief.db"), help="SQLite database path")
    parser.add_argument("--tickers", help="Optional comma-separated ticker override")
    args = parser.parse_args()

    hour, minute = _parse_time(args.time)
    while True:
        run_at = _next_run_at(hour, minute)
        wait_seconds = max((run_at - datetime.now()).total_seconds(), 0)
        print(f"Next FinBrief run at {run_at.isoformat(timespec='seconds')}")
        time.sleep(wait_seconds)
        command = [sys.executable, str(Path(__file__).with_name("daily_run.py")), "--db", str(args.db)]
        if args.tickers:
            command.extend(["--tickers", args.tickers])
        subprocess.run(command, check=False)


def _parse_time(value: str) -> tuple[int, int]:
    parts = value.split(":")
    if len(parts) != 2:
        raise SystemExit("--time must be HH:MM")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise SystemExit("--time must be HH:MM")
    return hour, minute


def _next_run_at(hour: int, minute: int) -> datetime:
    now = datetime.now()
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


if __name__ == "__main__":
    raise SystemExit(main())
