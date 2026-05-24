"""Lightweight local smoke tests for FinBrief.

This intentionally avoids live news fetches and FinBERT inference. It verifies the
current local DB, query layer, relevance guardrails, cleanup dry-run, and optionally
an already-running FastAPI server.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from finbrief.db import connect
from finbrief.fetcher import Headline, is_relevant_to_ticker
from finbrief.queries import get_summary, get_ticker_detail


def main() -> int:
    parser = argparse.ArgumentParser(description="Run FinBrief smoke tests")
    parser.add_argument("--db", type=Path, default=Path("data/finbrief.db"), help="SQLite database path")
    parser.add_argument("--base-url", default="http://127.0.0.1:8783", help="Optional running app base URL")
    parser.add_argument("--skip-server", action="store_true", help="Skip HTTP endpoint checks")
    args = parser.parse_args()

    checks: list[tuple[str, bool, str]] = []
    checks.extend(check_db(args.db))
    checks.extend(check_relevance())
    checks.extend(check_cleanup_dry_run(args.db))
    if not args.skip_server:
        checks.extend(check_server(args.base_url))

    failed = False
    for name, ok, detail in checks:
        marker = "PASS" if ok else "FAIL"
        print(f"{marker} {name}: {detail}")
        failed = failed or not ok
    return 1 if failed else 0


def check_db(db_path: Path) -> list[tuple[str, bool, str]]:
    checks = []
    with connect(db_path) as conn:
        counts = {
            table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("tickers", "pipeline_runs", "headlines", "scores", "daily_aggregates")
        }
        checks.append(("db tables populated", all(value > 0 for value in counts.values()), json.dumps(counts)))

        summary = get_summary(conn)
        checks.append((
            "summary shape",
            bool(summary["active_tickers"]) and summary["aggregate_date"] is not None,
            f"{len(summary['active_tickers'])} active, date={summary['aggregate_date']}",
        ))

        ticker = summary["active_tickers"][0]
        detail = get_ticker_detail(conn, ticker)
        checks.append((
            "ticker detail shape",
            detail["ticker"] == ticker and bool(detail["aggregates"]),
            f"{ticker}: {len(detail['aggregates'])} aggregates, {len(detail['headlines'])} latest headlines",
        ))
    return checks


def check_relevance() -> list[tuple[str, bool, str]]:
    unrelated = Headline(
        ticker="NVDA",
        title="Boise Cascade Stock Is Down 23%",
        summary="",
        url="https://example.com/boise",
        source="test",
        published_at="2026-05-24T00:00:00+00:00",
    )
    related = Headline(
        ticker="NVDA",
        title="Nvidia CEO Jensen Huang asks for more GPUs",
        summary="",
        url="https://example.com/nvidia",
        source="test",
        published_at="2026-05-24T00:00:00+00:00",
    )
    return [
        ("relevance rejects unrelated", not is_relevant_to_ticker(unrelated), unrelated.title),
        ("relevance keeps related", is_relevant_to_ticker(related), related.title),
    ]


def check_cleanup_dry_run(db_path: Path) -> list[tuple[str, bool, str]]:
    command = [
        sys.executable,
        str(ROOT / "scripts" / "clean_duplicate_headlines.py"),
        "--db",
        str(db_path),
        "--drop-irrelevant",
        "--dry-run",
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    ok = completed.returncode == 0 and "rows_to_delete=0" in completed.stdout
    detail = completed.stdout.strip().replace("\n", "; ") or completed.stderr.strip()
    return [("cleanup dry-run clean", ok, detail)]


def check_server(base_url: str) -> list[tuple[str, bool, str]]:
    endpoints = ("/", "/summary", "/refresh/status", "/static/app.js")
    checks = []
    for endpoint in endpoints:
        url = base_url.rstrip("/") + endpoint
        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                checks.append((f"server {endpoint}", response.status == 200, f"HTTP {response.status}"))
        except urllib.error.URLError as exc:
            checks.append((f"server {endpoint}", False, str(exc)))
    return checks


if __name__ == "__main__":
    raise SystemExit(main())
