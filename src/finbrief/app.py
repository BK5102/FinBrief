"""FastAPI app for the FinBrief dashboard/API."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from finbrief.db import connect, list_active_tickers, set_active_tickers
from finbrief.queries import get_summary, get_ticker_detail

DEFAULT_DB_PATH = Path(os.getenv("FINBRIEF_DB", "data/finbrief.db"))

app = FastAPI(title="FinBrief", version="0.1.0")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/portfolio")
def portfolio() -> dict:
    with connect(DEFAULT_DB_PATH) as conn:
        return {"tickers": list_active_tickers(conn)}


@app.put("/portfolio")
def replace_portfolio(tickers: list[str]) -> dict:
    with connect(DEFAULT_DB_PATH) as conn:
        active = set_active_tickers(conn, tickers)
    return {"tickers": active}


@app.get("/summary")
def summary(date: str | None = None) -> dict:
    with connect(DEFAULT_DB_PATH) as conn:
        return get_summary(conn, aggregate_date=date)


@app.get("/ticker/{symbol}")
def ticker_detail(symbol: str, date: str | None = None) -> dict:
    with connect(DEFAULT_DB_PATH) as conn:
        detail = get_ticker_detail(conn, symbol, aggregate_date=date)
    if not detail["aggregates"] and not detail["headlines"]:
        raise HTTPException(status_code=404, detail=f"No data found for {symbol.upper()}")
    return detail


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    with connect(DEFAULT_DB_PATH) as conn:
        data = get_summary(conn)
    return render_home(data)


def render_home(data: dict) -> str:
    cards = []
    for holding in data["holdings"]:
        ticker = holding["ticker"]
        aggregate = holding["aggregate"]
        if not aggregate:
            cards.append(f"<a class='card muted' href='/ticker/{ticker}'><h2>{ticker}</h2><p>No latest data</p></a>")
            continue
        tone = "negative" if holding["is_negative_spike"] else _score_tone(aggregate["weighted_score"])
        cards.append(
            f"<a class='card {tone}' href='/ticker/{ticker}'>"
            f"<h2>{ticker}</h2>"
            f"<p class='score'>{aggregate['weighted_score']:+.3f}</p>"
            f"<p>{aggregate['headline_count']} headlines</p>"
            f"<p>{aggregate['positive_count']}/{aggregate['neutral_count']}/{aggregate['negative_count']} pos/neu/neg</p>"
            f"</a>"
        )

    if data["negative_spike_count"]:
        banner = (
            f"<section class='banner danger'><strong>{data['negative_spike_count']} of "
            f"{len(data['active_tickers'])} holdings</strong> show negative sentiment spikes.</section>"
        )
    else:
        banner = "<section class='banner calm'><strong>No negative sentiment spikes</strong> detected.</section>"

    spike_sections = []
    for spike in data["negative_spikes"]:
        items = "".join(
            f"<li>{headline['confidence']:.3f} · {headline['title']}</li>"
            for headline in spike["responsible_headlines"][:5]
        )
        spike_sections.append(f"<section><h3>{spike['ticker']}: why flagged?</h3><ul>{items}</ul></section>")

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>FinBrief</title>
  <style>
    body {{ margin: 0; font-family: Inter, Segoe UI, Arial, sans-serif; background: #f7f8f4; color: #1d2420; }}
    main {{ max-width: 1120px; margin: 0 auto; padding: 32px 20px 56px; }}
    header {{ display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 24px; }}
    h1 {{ margin: 0; font-size: 32px; letter-spacing: 0; }}
    a {{ color: inherit; }}
    .meta {{ color: #66736b; font-size: 14px; }}
    .banner {{ border-left: 6px solid; padding: 18px 20px; margin: 0 0 18px; background: white; box-shadow: 0 1px 8px #0000000d; }}
    .danger {{ border-color: #b42318; }}
    .calm {{ border-color: #16805d; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 14px; margin: 22px 0; }}
    .card {{ display: block; min-height: 140px; padding: 16px; text-decoration: none; background: white; border: 1px solid #dfe4dd; border-radius: 8px; }}
    .card:hover {{ border-color: #859080; box-shadow: 0 6px 20px #00000012; }}
    .card h2 {{ margin: 0 0 8px; font-size: 22px; }}
    .score {{ font-size: 28px; font-weight: 700; margin: 0 0 10px; }}
    .positive .score {{ color: #137a4b; }}
    .negative .score, .danger strong {{ color: #b42318; }}
    .neutral .score {{ color: #5d685f; }}
    .muted {{ color: #6f786f; }}
  </style>
</head>
<body>
  <main>
    <header><h1>FinBrief</h1><span class="meta">FastAPI SQLite dashboard · {data["aggregate_date"] or "no data"}</span></header>
    {banner}
    <section class="grid">{''.join(cards)}</section>
    {''.join(spike_sections)}
  </main>
</body>
</html>"""


def _score_tone(score: float) -> str:
    if score > 0.15:
        return "positive"
    if score < -0.15:
        return "negative"
    return "neutral"
