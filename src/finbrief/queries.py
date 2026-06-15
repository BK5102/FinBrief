"""Read-side query helpers for dashboard and API views."""

from __future__ import annotations

import sqlite3

from finbrief.config import MIN_NEG_HEADLINES, SPIKE_SIGMA
from finbrief.db import find_negative_spikes, get_negative_headlines, list_active_tickers


def latest_aggregate_date(conn: sqlite3.Connection, user_id: int) -> str | None:
    row = conn.execute(
        "SELECT max(aggregate_date) FROM daily_aggregates WHERE user_id = ?", (user_id,)
    ).fetchone()
    return row[0] if row else None


def get_summary(conn: sqlite3.Connection, user_id: int, aggregate_date: str | None = None) -> dict:
    active = list_active_tickers(conn, user_id)
    target_date = aggregate_date or latest_aggregate_date(conn, user_id)
    aggregate_by_ticker = _aggregate_by_ticker(conn, user_id, target_date) if target_date else {}
    spikes = (
        find_negative_spikes(
            conn, target_date, user_id,
            min_std_drop=SPIKE_SIGMA, min_high_conf_negatives=MIN_NEG_HEADLINES,
        )
        if target_date
        else []
    )
    spike_tickers = {spike["ticker"] for spike in spikes}

    holdings = []
    for ticker in active:
        aggregate = aggregate_by_ticker.get(ticker)
        holdings.append(
            {
                "ticker": ticker,
                "has_data": aggregate is not None,
                "is_negative_spike": ticker in spike_tickers,
                "aggregate": aggregate,
            }
        )

    last_run = conn.execute(
        """
        SELECT id, completed_at, articles_fetched, articles_scored, fetch_seconds, score_seconds
        FROM pipeline_runs
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (user_id,),
    ).fetchone()

    return {
        "aggregate_date": target_date,
        "active_tickers": active,
        "holdings": holdings,
        "negative_spike_count": len(spikes),
        "negative_spikes": [
            {
                **spike,
                "responsible_headlines": get_negative_headlines(
                    conn, spike["ticker"], spike["aggregate_date"], user_id
                ),
            }
            for spike in spikes
        ],
        "last_run": dict(last_run) if last_run else None,
    }


def get_ticker_detail(
    conn: sqlite3.Connection,
    ticker: str,
    user_id: int,
    aggregate_date: str | None = None,
) -> dict:
    symbol = ticker.upper()
    target_date = aggregate_date or latest_aggregate_date(conn, user_id)
    aggregate_rows = conn.execute(
        """
        SELECT *
        FROM daily_aggregates
        WHERE user_id = ? AND ticker = ?
        ORDER BY aggregate_date DESC
        LIMIT 14
        """,
        (user_id, symbol),
    ).fetchall()

    headlines = []
    if target_date:
        headlines = conn.execute(
            """
            SELECT h.title, h.summary, h.url, h.source, h.published_at,
                   s.label, s.confidence, s.positive_score, s.neutral_score, s.negative_score
            FROM headlines h
            JOIN scores s ON s.headline_id = h.id
            WHERE h.user_id = ?
              AND h.ticker = ?
              AND substr(h.published_at, 1, 10) = ?
            ORDER BY s.label = 'negative' DESC, s.confidence DESC, h.published_at DESC
            """,
            (user_id, symbol, target_date),
        ).fetchall()

    return {
        "ticker": symbol,
        "aggregate_date": target_date,
        "aggregates": [dict(row) for row in aggregate_rows],
        "headlines": [dict(row) for row in headlines],
    }


def get_recent_runs(conn: sqlite3.Connection, user_id: int, limit: int = 5) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, completed_at, status, articles_fetched, articles_scored, fetch_seconds, score_seconds
        FROM pipeline_runs
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (user_id, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def _aggregate_by_ticker(
    conn: sqlite3.Connection, user_id: int, aggregate_date: str | None
) -> dict[str, dict]:
    if not aggregate_date:
        return {}
    rows = conn.execute(
        "SELECT * FROM daily_aggregates WHERE user_id = ? AND aggregate_date = ? ORDER BY ticker",
        (user_id, aggregate_date),
    ).fetchall()
    return {row["ticker"]: dict(row) for row in rows}
