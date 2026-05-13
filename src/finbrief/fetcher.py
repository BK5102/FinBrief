"""News fetchers for FinBrief.

Each fetcher returns a list of Headline dicts with the schema:
    {
        "ticker": str,
        "title": str,
        "summary": str,
        "url": str,
        "source": str,           # e.g. "yahoo_rss", "finnhub"
        "published_at": str,     # ISO-8601 UTC
    }
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from typing import Iterable

import feedparser
import requests

log = logging.getLogger(__name__)


@dataclass
class Headline:
    ticker: str
    title: str
    summary: str
    url: str
    source: str
    published_at: str

    def to_dict(self) -> dict:
        return asdict(self)


def _today_utc_bounds() -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, now


def fetch_yahoo_rss(ticker: str, since: datetime | None = None) -> list[Headline]:
    """Yahoo Finance per-ticker RSS feed. No API key required."""
    url = f"https://finance.yahoo.com/rss/headline?s={ticker}"
    try:
        feed = feedparser.parse(url)
    except Exception as e:
        log.warning("yahoo_rss parse failed for %s: %s", ticker, e)
        return []

    out: list[Headline] = []
    for entry in feed.entries:
        published_dt = _parse_feed_time(entry)
        if since is not None and published_dt is not None and published_dt < since:
            continue
        out.append(
            Headline(
                ticker=ticker,
                title=getattr(entry, "title", "").strip(),
                summary=_clean_summary(getattr(entry, "summary", "")),
                url=getattr(entry, "link", ""),
                source="yahoo_rss",
                published_at=(published_dt or datetime.now(timezone.utc)).isoformat(),
            )
        )
    return out


def fetch_finnhub(ticker: str, api_key: str, since: datetime | None = None) -> list[Headline]:
    """Finnhub company-news endpoint. Free tier requires an API key."""
    start, end = _today_utc_bounds()
    if since is not None:
        start = since
    params = {
        "symbol": ticker,
        "from": start.date().isoformat(),
        "to": end.date().isoformat(),
        "token": api_key,
    }
    try:
        r = requests.get("https://finnhub.io/api/v1/company-news", params=params, timeout=15)
        r.raise_for_status()
        items = r.json()
    except Exception as e:
        log.warning("finnhub fetch failed for %s: %s", ticker, e)
        return []

    out: list[Headline] = []
    for item in items:
        ts = item.get("datetime")
        published_dt = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None
        if since is not None and published_dt is not None and published_dt < since:
            continue
        out.append(
            Headline(
                ticker=ticker,
                title=(item.get("headline") or "").strip(),
                summary=(item.get("summary") or "").strip(),
                url=item.get("url", ""),
                source="finnhub",
                published_at=(published_dt or datetime.now(timezone.utc)).isoformat(),
            )
        )
    return out


def fetch_all_today(tickers: Iterable[str], finnhub_key: str | None = None) -> list[Headline]:
    """Fetch today's headlines for each ticker from all configured sources, deduped by URL."""
    since, _ = _today_utc_bounds()
    seen_urls: set[str] = set()
    results: list[Headline] = []

    for ticker in tickers:
        ticker = ticker.strip().upper()
        if not ticker:
            continue

        batch: list[Headline] = []
        batch.extend(fetch_yahoo_rss(ticker, since=since))
        if finnhub_key:
            batch.extend(fetch_finnhub(ticker, finnhub_key, since=since))

        for h in batch:
            key = h.url or f"{h.source}:{h.title}"
            if key in seen_urls or not h.title:
                continue
            seen_urls.add(key)
            results.append(h)

    return results


def _parse_feed_time(entry) -> datetime | None:
    parsed = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if not parsed:
        return None
    try:
        return datetime(*parsed[:6], tzinfo=timezone.utc)
    except Exception:
        return None


def _clean_summary(html_or_text: str) -> str:
    import re
    text = re.sub(r"<[^>]+>", " ", html_or_text or "")
    return re.sub(r"\s+", " ", text).strip()
