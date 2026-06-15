"""Shared pipeline runner used by CLI, scheduler, and web refresh."""

from __future__ import annotations

import time
from collections import defaultdict
from pathlib import Path
from typing import Sequence

from finbrief.db import connect, list_active_tickers, persist_pipeline_result
from finbrief.fetcher import Headline, fetch_all_today
from finbrief.scorer import score_texts


def run_pipeline_cycle(
    tickers: Sequence[str] | None = None,
    db_path: str | Path | None = None,
    finnhub_key: str | None = None,
    user_id: int | None = None,
) -> dict:
    """Fetch, score, optionally persist, and return the standard pipeline output."""
    resolved_tickers = _resolve_tickers(tickers, db_path, user_id)

    t0 = time.perf_counter()
    headlines = fetch_all_today(resolved_tickers, finnhub_key=finnhub_key)
    fetch_secs = time.perf_counter() - t0

    texts = [score_text(headline) for headline in headlines]
    t1 = time.perf_counter()
    scores = score_texts(texts) if texts else []
    score_secs = time.perf_counter() - t1

    by_ticker: dict[str, list[dict]] = defaultdict(list)
    for headline, score in zip(headlines, scores):
        by_ticker[headline.ticker].append({**headline.to_dict(), "sentiment": score})

    output = {
        "tickers": resolved_tickers,
        "counts": {ticker: len(by_ticker.get(ticker, [])) for ticker in resolved_tickers},
        "timings_seconds": {"fetch": round(fetch_secs, 2), "score": round(score_secs, 2)},
        "headlines_by_ticker": by_ticker,
    }

    if db_path and user_id is not None:
        with connect(db_path) as conn:
            run_id = persist_pipeline_result(
                conn, resolved_tickers, headlines, scores, output["timings_seconds"], user_id
            )
        output["db"] = {"path": str(db_path), "pipeline_run_id": run_id}

    return output


def score_text(headline: Headline) -> str:
    if headline.summary and headline.summary.lower() not in headline.title.lower():
        return f"{headline.title}. {headline.summary}"
    return headline.title


def _resolve_tickers(
    tickers: Sequence[str] | None,
    db_path: str | Path | None,
    user_id: int | None,
) -> list[str]:
    normalized = _normalize_tickers(tickers or [])
    if not normalized and db_path and user_id is not None:
        with connect(db_path) as conn:
            normalized = list_active_tickers(conn, user_id)
    if not normalized:
        raise ValueError("tickers must contain at least one symbol, unless db_path contains active tickers")
    return normalized


def _normalize_tickers(tickers: Sequence[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for ticker in tickers:
        symbol = ticker.strip().upper()
        if not symbol or symbol in seen:
            continue
        out.append(symbol)
        seen.add(symbol)
    return out
