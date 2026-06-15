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

from finbrief.fetcher import Headline, headline_dedupe_key
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
    if _is_old_schema(conn):
        _drop_old_schema(conn)

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS tickers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            symbol TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, symbol)
        );

        CREATE TABLE IF NOT EXISTS pipeline_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
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
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            ticker TEXT NOT NULL,
            title TEXT NOT NULL,
            summary TEXT NOT NULL DEFAULT '',
            url TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL,
            published_at TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            UNIQUE(user_id, fingerprint)
        );

        CREATE INDEX IF NOT EXISTS idx_headlines_user_ticker_published
            ON headlines(user_id, ticker, published_at);

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
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            ticker TEXT NOT NULL,
            aggregate_date TEXT NOT NULL,
            headline_count INTEGER NOT NULL,
            weighted_score REAL NOT NULL,
            positive_count INTEGER NOT NULL,
            neutral_count INTEGER NOT NULL,
            negative_count INTEGER NOT NULL,
            high_conf_negative_count INTEGER NOT NULL,
            avg_confidence REAL NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (user_id, ticker, aggregate_date)
        );
        """
    )
    conn.commit()


# --- User helpers ---

def create_user(conn: sqlite3.Connection, email: str, password_hash: str) -> int:
    init_db(conn)
    cur = conn.execute(
        "INSERT INTO users(email, password_hash, created_at) VALUES (?, ?, ?)",
        (email.lower().strip(), password_hash, _now_iso()),
    )
    conn.commit()
    return int(cur.lastrowid)


def get_user_by_email(conn: sqlite3.Connection, email: str) -> dict | None:
    init_db(conn)
    row = conn.execute(
        "SELECT id, email, password_hash FROM users WHERE email = ?",
        (email.lower().strip(),),
    ).fetchone()
    return dict(row) if row else None


def get_user_by_id(conn: sqlite3.Connection, user_id: int) -> dict | None:
    row = conn.execute(
        "SELECT id, email FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    return dict(row) if row else None


def get_all_active_user_ids(conn: sqlite3.Connection) -> list[int]:
    """Return all user IDs that have at least one active ticker."""
    rows = conn.execute(
        "SELECT DISTINCT user_id FROM tickers WHERE active = 1"
    ).fetchall()
    return [int(row["user_id"]) for row in rows]


# --- Portfolio helpers ---

def upsert_tickers(conn: sqlite3.Connection, tickers: Iterable[str], user_id: int) -> None:
    now = _now_iso()
    rows = [(user_id, ticker.strip().upper(), now, now) for ticker in tickers if ticker.strip()]
    conn.executemany(
        """
        INSERT INTO tickers(user_id, symbol, active, created_at, updated_at)
        VALUES (?, ?, 1, ?, ?)
        ON CONFLICT(user_id, symbol) DO UPDATE SET active = 1, updated_at = excluded.updated_at
        """,
        rows,
    )


def set_active_tickers(conn: sqlite3.Connection, tickers: Iterable[str], user_id: int) -> list[str]:
    normalized = _normalize_tickers(tickers)
    now = _now_iso()
    conn.execute("UPDATE tickers SET active = 0, updated_at = ? WHERE user_id = ?", (now, user_id))
    upsert_tickers(conn, normalized, user_id)
    conn.commit()
    return normalized


def deactivate_tickers(conn: sqlite3.Connection, tickers: Iterable[str], user_id: int) -> list[str]:
    normalized = _normalize_tickers(tickers)
    now = _now_iso()
    conn.executemany(
        "UPDATE tickers SET active = 0, updated_at = ? WHERE user_id = ? AND symbol = ?",
        [(now, user_id, ticker) for ticker in normalized],
    )
    conn.commit()
    return normalized


def list_active_tickers(conn: sqlite3.Connection, user_id: int) -> list[str]:
    init_db(conn)
    rows = conn.execute(
        "SELECT symbol FROM tickers WHERE user_id = ? AND active = 1 ORDER BY symbol",
        (user_id,),
    ).fetchall()
    return [str(row["symbol"]) for row in rows]


# --- Pipeline persistence ---

def persist_pipeline_result(
    conn: sqlite3.Connection,
    tickers: Sequence[str],
    headlines: Sequence[Headline],
    scores: Sequence[dict],
    timings_seconds: dict[str, float],
    user_id: int,
) -> int:
    if len(headlines) != len(scores):
        raise ValueError("headlines and scores must have the same length")

    init_db(conn)
    upsert_tickers(conn, tickers, user_id)
    now = _now_iso()

    cur = conn.execute(
        """
        INSERT INTO pipeline_runs(
            user_id, started_at, completed_at, status, tickers_json,
            articles_fetched, articles_scored, fetch_seconds, score_seconds
        )
        VALUES (?, ?, ?, 'success', ?, ?, ?, ?, ?)
        """,
        (
            user_id,
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
        headline_id = upsert_headline(conn, headline, user_id=user_id, fetched_at=now)
        upsert_score(conn, headline_id, score, scored_at=now)
        touched.add((headline.ticker.upper(), _date_part(headline.published_at)))

    for ticker, aggregate_date in touched:
        recompute_daily_aggregate(conn, ticker, aggregate_date, user_id)

    conn.commit()
    return run_id


def upsert_headline(
    conn: sqlite3.Connection,
    headline: Headline,
    user_id: int,
    fetched_at: str | None = None,
) -> int:
    fetched_at = fetched_at or _now_iso()
    fingerprint = headline_fingerprint(headline)
    conn.execute(
        """
        INSERT INTO headlines(user_id, ticker, title, summary, url, source, published_at, fetched_at, fingerprint)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, fingerprint) DO UPDATE SET
            summary = excluded.summary,
            url = excluded.url,
            source = excluded.source,
            published_at = excluded.published_at,
            fetched_at = excluded.fetched_at
        """,
        (
            user_id,
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
    row = conn.execute(
        "SELECT id FROM headlines WHERE user_id = ? AND fingerprint = ?",
        (user_id, fingerprint),
    ).fetchone()
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


def recompute_daily_aggregate(
    conn: sqlite3.Connection, ticker: str, aggregate_date: str, user_id: int
) -> None:
    rows = conn.execute(
        """
        SELECT s.label, s.confidence
        FROM headlines h
        JOIN scores s ON s.headline_id = h.id
        WHERE h.user_id = ?
          AND h.ticker = ?
          AND substr(h.published_at, 1, 10) = ?
        """,
        (user_id, ticker.upper(), aggregate_date),
    ).fetchall()

    if not rows:
        conn.execute(
            "DELETE FROM daily_aggregates WHERE user_id = ? AND ticker = ? AND aggregate_date = ?",
            (user_id, ticker.upper(), aggregate_date),
        )
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
            user_id, ticker, aggregate_date, headline_count, weighted_score,
            positive_count, neutral_count, negative_count, high_conf_negative_count,
            avg_confidence, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, ticker, aggregate_date) DO UPDATE SET
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
            user_id,
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
    user_id: int,
    min_std_drop: float = 1.5,
    min_high_conf_negatives: int = 2,
    lookback_days: int = 14,
) -> list[dict]:
    today_rows = conn.execute(
        "SELECT * FROM daily_aggregates WHERE user_id = ? AND aggregate_date = ?",
        (user_id, aggregate_date),
    ).fetchall()

    spikes: list[dict] = []
    for today in today_rows:
        if int(today["high_conf_negative_count"]) < min_high_conf_negatives:
            continue

        history = conn.execute(
            """
            SELECT weighted_score
            FROM daily_aggregates
            WHERE user_id = ? AND ticker = ? AND aggregate_date < ?
            ORDER BY aggregate_date DESC
            LIMIT ?
            """,
            (user_id, today["ticker"], aggregate_date, lookback_days),
        ).fetchall()
        values = [float(row["weighted_score"]) for row in history]
        if len(values) < 2:
            continue

        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
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


def get_negative_headlines(
    conn: sqlite3.Connection,
    ticker: str,
    aggregate_date: str,
    user_id: int,
    min_confidence: float = 0.7,
) -> list[dict]:
    rows = conn.execute(
        """
        SELECT h.title, h.url, h.source, h.published_at, s.label, s.confidence
        FROM headlines h
        JOIN scores s ON s.headline_id = h.id
        WHERE h.user_id = ?
          AND h.ticker = ?
          AND substr(h.published_at, 1, 10) = ?
          AND s.label = 'negative'
          AND s.confidence >= ?
        ORDER BY s.confidence DESC, h.published_at DESC
        """,
        (user_id, ticker.upper(), aggregate_date, min_confidence),
    ).fetchall()
    return [dict(row) for row in rows]


def headline_fingerprint(headline: Headline) -> str:
    basis = headline_dedupe_key(headline)
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def _is_old_schema(conn: sqlite3.Connection) -> bool:
    has_tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('tickers','headlines')"
    ).fetchone()
    has_users = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='users'"
    ).fetchone()
    return has_tables is not None and has_users is None


def _drop_old_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DROP TABLE IF EXISTS daily_aggregates;
        DROP TABLE IF EXISTS scores;
        DROP TABLE IF EXISTS headlines;
        DROP TABLE IF EXISTS pipeline_runs;
        DROP TABLE IF EXISTS tickers;
        """
    )
    conn.commit()


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
