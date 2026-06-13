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
import re
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from typing import Callable, Iterable, TypeVar

import feedparser
import requests
import yfinance as yf

from finbrief.config import FETCH_RETRIES, FETCH_RETRY_DELAY

log = logging.getLogger(__name__)

_T = TypeVar("_T")


def _retry(fn: Callable[[], _T], retries: int = FETCH_RETRIES, delay: float = FETCH_RETRY_DELAY) -> _T:
    """Call fn(); on exception retry with exponential backoff. Raises on final failure."""
    for attempt in range(retries):
        try:
            return fn()
        except Exception:
            if attempt == retries - 1:
                raise
            wait = delay * (2 ** attempt)
            log.debug("retry %d/%d in %.1fs", attempt + 1, retries, wait)
            time.sleep(wait)

TICKER_ALIASES = {
    "AAPL": ("aapl", "apple", "iphone", "ipad", "mac", "app store"),
    "AMZN": ("amzn", "amazon", "aws", "prime video"),
    "GOOGL": ("googl", "google", "alphabet", "waymo", "youtube", "gemini"),
    "GOOG": ("goog", "google", "alphabet", "waymo", "youtube", "gemini"),
    "JPM": ("jpm", "jpmorgan", "jp morgan", "jpmorgan chase", "chase"),
    "KO": ("ko", "coca cola", "coca-cola"),
    "META": ("meta", "facebook", "instagram", "whatsapp", "threads"),
    "MSFT": ("msft", "microsoft", "azure", "windows", "xbox", "openai"),
    "NVDA": ("nvda", "nvidia", "jensen huang", "geforce", "cuda"),
    "TSLA": ("tsla", "tesla", "elon musk", "cybertruck", "model y", "model 3"),
    "V": ("visa", "nyse v", "v stock"),
}


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
        feed = _retry(lambda: feedparser.parse(url))
    except Exception as e:
        log.warning("yahoo_rss fetch failed for %s after retries: %s", ticker, e)
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


def fetch_yfinance(ticker: str, since: datetime | None = None) -> list[Headline]:
    """yfinance's Ticker.news endpoint. No API key required. Wraps the modern Yahoo news API."""
    try:
        items = _retry(lambda: yf.Ticker(ticker).news or [])
    except Exception as e:
        log.warning("yfinance fetch failed for %s after retries: %s", ticker, e)
        return []

    out: list[Headline] = []
    for item in items:
        content = item.get("content") or item
        title = (content.get("title") or "").strip()
        if not title:
            continue

        published_dt = _parse_iso(content.get("pubDate") or content.get("displayTime"))
        if since is not None and published_dt is not None and published_dt < since:
            continue

        url = ""
        for key in ("clickThroughUrl", "canonicalUrl"):
            block = content.get(key) or {}
            if isinstance(block, dict) and block.get("url"):
                url = block["url"]
                break
        if not url:
            url = content.get("link", "")

        provider = content.get("provider") or {}
        provider_name = provider.get("displayName") if isinstance(provider, dict) else ""

        out.append(
            Headline(
                ticker=ticker,
                title=title,
                summary=(content.get("summary") or content.get("description") or "").strip(),
                url=url,
                source=f"yfinance:{provider_name}" if provider_name else "yfinance",
                published_at=(published_dt or datetime.now(timezone.utc)).isoformat(),
            )
        )
    return out


def fetch_finnhub(ticker: str, api_key: str, since: datetime | None = None) -> list[Headline]:
    """Finnhub company-news endpoint. Free tier requires an API key."""
    start, end = _today_utc_bounds()
    if since is not None:
        start = since
    return fetch_finnhub_range(ticker, api_key, start=start, end=end)


def fetch_finnhub_range(ticker: str, api_key: str, start: datetime, end: datetime) -> list[Headline]:
    """Finnhub company-news endpoint for a specific inclusive date range."""
    params = {
        "symbol": ticker,
        "from": start.date().isoformat(),
        "to": end.date().isoformat(),
        "token": api_key,
    }
    try:
        r = _retry(lambda: requests.get("https://finnhub.io/api/v1/company-news", params=params, timeout=15))
        r.raise_for_status()
        items = r.json()
    except Exception as e:
        log.warning("finnhub fetch failed for %s after retries: %s", ticker, e)
        return []

    out: list[Headline] = []
    for item in items:
        ts = item.get("datetime")
        published_dt = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None
        if published_dt is not None and published_dt < start:
            continue
        if published_dt is not None and published_dt > end:
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
        batch.extend(fetch_yfinance(ticker, since=since))
        batch.extend(fetch_yahoo_rss(ticker, since=since))
        if finnhub_key:
            batch.extend(fetch_finnhub(ticker, finnhub_key, since=since))

        for h in batch:
            if not is_relevant_to_ticker(h):
                continue
            key = headline_dedupe_key(h)
            if key in seen_urls or not h.title:
                continue
            seen_urls.add(key)
            results.append(h)

    return results


def headline_dedupe_key(headline: Headline) -> str:
    """Prefer title/date dedupe because syndicated finance stories often have different source URLs."""
    normalized_title = normalize_title(headline.title)
    if normalized_title:
        return f"{headline.ticker.upper()}:{headline.published_at[:10]}:{normalized_title}"
    return headline.url or f"{headline.source}:{headline.title}"


def is_relevant_to_ticker(headline: Headline) -> bool:
    aliases = TICKER_ALIASES.get(headline.ticker.upper(), (headline.ticker.lower(),))
    text = normalize_title(f"{headline.title} {headline.summary} {headline.url}")
    return any(normalize_title(alias) in text for alias in aliases)


def normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _parse_feed_time(entry) -> datetime | None:
    parsed = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if not parsed:
        return None
    try:
        return datetime(*parsed[:6], tzinfo=timezone.utc)
    except Exception:
        return None


def _clean_summary(html_or_text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html_or_text or "")
    return re.sub(r"\s+", " ", text).strip()
