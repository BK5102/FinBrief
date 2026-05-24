"""SQLite persistence and aggregation for FinBrief."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

from finbrief.fetcher import Headline
from finbrief.scorer import MODEL_ID

SENTIMENT_VALUES = {"positive": 1.0, "neutral": 0.0, "negative": -1.0}


def connect(path: str | Path) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS tickers (
            symbol TEXT PRIMARY KEY,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS pipeline_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            status TEXT NOT NULL,
            tickers_json TEXT NOT NULL,
            articles_fetched INTEGER NOT NULL DEFAULT 0,
            articles_scored INTEGER NOT NULL DEFAULT 0,
            fetch_seconds REAL,
            score_seconds REAL,
            error TEXT
        );

        CREATE TABLE IF NOT EXISTS headlines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL REFERENCES tickers(symbol),
            title TEXT NOT NULL,
            summary TEXT NOT NULL DEFAULT '',
            url TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL,
            published_at TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            fingerprint TEXT NOT NULL UNIQUE
        );

        CREATE INDEX IF NOT EXISTS idx_headlines_ticker_published
            ON headlines(ticker, published_at);

        CREATE TABLE IF NOT EXISTS scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            headline_id INTEGER NOT NULL UNIQUE REFERENCES headlines(id) ON DELETE CASCADE,
            model_id TEXT NOT NULL,
            label TEXT NOT NULL CHECK (label IN ('positive', 'neutral', 'negative')),
            confidence REAL NOT NULL,
            positive_score REAL NOT NULL,
            neutral_score REAL NOT NULL,
            negative_score REAL NOT NULL,
            scored_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS daily_aggregates (
            ticker TEXT NOT NULL REFERENCES tickers(symbol),
            aggregate_date TEXT NOT NULL,
            headline_count INTEGER NOT NULL,
            weighted_score REAL NOT NULL,
            positive_count INTEGER NOT NULL,
            neutral_count INTEGER NOT NULL,
            negative_count INTEGER NOT NULL,
            high_conf_negative_count INTEGER NOT NULL,
            avg_confidence REAL NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (ticker, aggregate_date)
        );
        """
    )
    conn.commit()


def upsert_tickers(conn: sqlite3.Connection, tickers: Iterable[str]) -> None:
    now = _now_iso()
    rows = [(ticker.strip().upper(), now, now) for ticker in tickers if ticker.strip()]
    conn.executemany(
        """
        INSERT INTO tickers(symbol, active, created_at, updated_at)
        VALUES (?, 1, ?, ?)
        ON CONFLICT(symbol) DO UPDATE SET active = 1, updated_at = excluded.updated_at
        """,
        rows,
    )


def set_active_tickers(conn: sqlite3.Connection, tickers: Iterable[str]) -> list[str]:
    """Replace the active portfolio with the provided ticker list."""
    normalized = _normalize_tickers(tickers)
    now = _now_iso()
    conn.execute("UPDATE tickers SET active = 0, updated_at = ?", (now,))
    upsert_tickers(conn, normalized)
    conn.commit()
    return normalized


def deactivate_tickers(conn: sqlite3.Connection, tickers: Iterable[str]) -> list[str]:
    normalized = _normalize_tickers(tickers)
    now = _now_iso()
    conn.executemany(
        "UPDATE tickers SET active = 0, updated_at = ? WHERE symbol = ?",
        [(now, ticker) for ticker in normalized],
    )
    conn.commit()
    return normalized


def list_active_tickers(conn: sqlite3.Connection) -> list[str]:
    init_db(conn)
    rows = conn.execute("SELECT symbol FROM tickers WHERE active = 1 ORDER BY symbol").fetchall()
    return [str(row["symbol"]) for row in rows]


def persist_pipeline_result(
    conn: sqlite3.Connection,
    tickers: Sequence[str],
    headlines: Sequence[Headline],
    scores: Sequence[dict],
    timings_seconds: dict[str, float],
) -> int:
    """Persist a completed pipeline run and recompute aggregates for touched ticker-days."""
    if len(headlines) != len(scores):
        raise ValueError("headlines and scores must have the same length")

    init_db(conn)
    upsert_tickers(conn, tickers)
    now = _now_iso()

    cur = conn.execute(
        """
        INSERT INTO pipeline_runs(
            started_at, completed_at, status, tickers_json,
            articles_fetched, articles_scored, fetch_seconds, score_seconds
        )
        VALUES (?, ?, 'success', ?, ?, ?, ?, ?)
        """,
        (
            now,
            now,
            json.dumps(list(tickers)),
            len(headlines),
            len(scores),
            timings_seconds.get("fetch"),
            timings_seconds.get("score"),
        ),
    )
    run_id = int(cur.lastrowid)

    touched: set[tuple[str, str]] = set()
    for headline, score in zip(headlines, scores):
        headline_id = upsert_headline(conn, headline, fetched_at=now)
        upsert_score(conn, headline_id, score, scored_at=now)
        touched.add((headline.ticker.upper(), _date_part(headline.published_at)))

    for ticker, aggregate_date in touched:
        recompute_daily_aggregate(conn, ticker, aggregate_date)

    conn.commit()
    return run_id


def upsert_headline(conn: sqlite3.Connection, headline: Headline, fetched_at: str | None = None) -> int:
    fetched_at = fetched_at or _now_iso()
    fingerprint = headline_fingerprint(headline)
    conn.execute(
        """
        INSERT INTO headlines(ticker, title, summary, url, source, published_at, fetched_at, fingerprint)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(fingerprint) DO UPDATE SET
            summary = excluded.summary,
            url = excluded.url,
            source = excluded.source,
            published_at = excluded.published_at,
            fetched_at = excluded.fetched_at
        """,
        (
            headline.ticker.upper(),
            headline.title,
            headline.summary,
            headline.url,
            headline.source,
            headline.published_at,
            fetched_at,
            fingerprint,
        ),
    )
    row = conn.execute("SELECT id FROM headlines WHERE fingerprint = ?", (fingerprint,)).fetchone()
    return int(row["id"])


def upsert_score(conn: sqlite3.Connection, headline_id: int, score: dict, scored_at: str | None = None) -> None:
    scored_at = scored_at or _now_iso()
    scores = score.get("scores") or {}
    conn.execute(
        """
        INSERT INTO scores(
            headline_id, model_id, label, confidence,
            positive_score, neutral_score, negative_score, scored_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(headline_id) DO UPDATE SET
            model_id = excluded.model_id,
            label = excluded.label,
            confidence = excluded.confidence,
            positive_score = excluded.positive_score,
            neutral_score = excluded.neutral_score,
            negative_score = excluded.negative_score,
            scored_at = excluded.scored_at
        """,
        (
            headline_id,
            MODEL_ID,
            score["label"],
            float(score["confidence"]),
            float(scores.get("positive", 0.0)),
            float(scores.get("neutral", 0.0)),
            float(scores.get("negative", 0.0)),
            scored_at,
        ),
    )


def recompute_daily_aggregate(conn: sqlite3.Connection, ticker: str, aggregate_date: str) -> None:
    rows = conn.execute(
        """
        SELECT s.label, s.confidence
        FROM headlines h
        JOIN scores s ON s.headline_id = h.id
        WHERE h.ticker = ?
          AND substr(h.published_at, 1, 10) = ?
        """,
        (ticker.upper(), aggregate_date),
    ).fetchall()

    if not rows:
        return

    weighted_total = 0.0
    confidence_total = 0.0
    counts: Counter[str] = Counter()
    high_conf_negative_count = 0

    for row in rows:
        label = row["label"]
        confidence = float(row["confidence"])
        weighted_total += SENTIMENT_VALUES[label] * confidence
        confidence_total += confidence
        counts[label] += 1
        if label == "negative" and confidence >= 0.7:
            high_conf_negative_count += 1

    headline_count = len(rows)
    weighted_score = weighted_total / confidence_total if confidence_total else 0.0
    avg_confidence = confidence_total / headline_count if headline_count else 0.0

    conn.execute(
        """
        INSERT INTO daily_aggregates(
            ticker, aggregate_date, headline_count, weighted_score,
            positive_count, neutral_count, negative_count, high_conf_negative_count,
            avg_confidence, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(ticker, aggregate_date) DO UPDATE SET
            headline_count = excluded.headline_count,
            weighted_score = excluded.weighted_score,
            positive_count = excluded.positive_count,
            neutral_count = excluded.neutral_count,
            negative_count = excluded.negative_count,
            high_conf_negative_count = excluded.high_conf_negative_count,
            avg_confidence = excluded.avg_confidence,
            updated_at = excluded.updated_at
        """,
        (
            ticker.upper(),
            aggregate_date,
            headline_count,
            weighted_score,
            counts["positive"],
            counts["neutral"],
            counts["negative"],
            high_conf_negative_count,
            avg_confidence,
            _now_iso(),
        ),
    )


def find_negative_spikes(
    conn: sqlite3.Connection,
    aggregate_date: str,
    min_std_drop: float = 1.5,
    min_high_conf_negatives: int = 2,
    lookback_days: int = 14,
) -> list[dict]:
    """Return tickers whose daily score is unusually negative vs prior aggregate history."""
    today_rows = conn.execute(
        """
        SELECT *
        FROM daily_aggregates
        WHERE aggregate_date = ?
        """,
        (aggregate_date,),
    ).fetchall()

    spikes: list[dict] = []
    for today in today_rows:
        if int(today["high_conf_negative_count"]) < min_high_conf_negatives:
            continue

        history = conn.execute(
            """
            SELECT weighted_score
            FROM daily_aggregates
            WHERE ticker = ?
              AND aggregate_date < ?
            ORDER BY aggregate_date DESC
            LIMIT ?
            """,
            (today["ticker"], aggregate_date, lookback_days),
        ).fetchall()
        values = [float(row["weighted_score"]) for row in history]
        if len(values) < 2:
            continue

        mean = sum(values) / len(values)
        variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
        std = math.sqrt(variance)
        threshold = mean - (min_std_drop * std)
        score = float(today["weighted_score"])
        if std > 0 and score < threshold:
            spikes.append(
                {
                    "ticker": today["ticker"],
                    "aggregate_date": aggregate_date,
                    "weighted_score": score,
                    "rolling_mean": mean,
                    "rolling_std": std,
                    "threshold": threshold,
                    "high_conf_negative_count": int(today["high_conf_negative_count"]),
                    "headline_count": int(today["headline_count"]),
                }
            )
    return spikes


def headline_fingerprint(headline: Headline) -> str:
    basis = headline.url or f"{headline.ticker}|{headline.source}|{headline.title}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def _date_part(iso_datetime: str) -> str:
    return iso_datetime[:10]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_tickers(tickers: Iterable[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for ticker in tickers:
        symbol = ticker.strip().upper()
        if not symbol or symbol in seen:
            continue
        normalized.append(symbol)
        seen.add(symbol)
    return normalized
