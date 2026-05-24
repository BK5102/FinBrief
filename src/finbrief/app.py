"""FastAPI app for the FinBrief dashboard/API."""

from __future__ import annotations

import html
import os
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

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


@app.post("/portfolio")
async def update_portfolio(request: Request):
    body = (await request.body()).decode("utf-8")
    values = parse_qs(body)
    raw = values.get("tickers", [""])[0]
    tickers = _split_tickers(raw)
    with connect(DEFAULT_DB_PATH) as conn:
        active = set_active_tickers(conn, tickers)

    accept = request.headers.get("accept", "")
    if "text/html" in accept:
        return RedirectResponse("/", status_code=303)
    return {"tickers": active}


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


@app.get("/ticker/{symbol}/view", response_class=HTMLResponse)
def ticker_page(symbol: str, date: str | None = None) -> str:
    with connect(DEFAULT_DB_PATH) as conn:
        detail = get_ticker_detail(conn, symbol, aggregate_date=date)
    if not detail["aggregates"] and not detail["headlines"]:
        raise HTTPException(status_code=404, detail=f"No data found for {symbol.upper()}")
    return render_ticker_detail(detail)


def render_home(data: dict) -> str:
    portfolio_value = ",".join(data["active_tickers"])
    latest_date = data["aggregate_date"] or "No aggregate date"
    cards = []
    for holding in data["holdings"]:
        cards.append(render_holding_card(holding, latest_date))

    banner = render_urgency_banner(data)
    spike_sections = "".join(render_spike_section(spike) for spike in data["negative_spikes"])
    last_run = render_last_run(data.get("last_run"))
    editor = f"""
    <section class="toolbar">
      <form method="post" action="/portfolio">
        <label for="tickers">Portfolio</label>
        <input id="tickers" name="tickers" value="{escape(portfolio_value)}" autocomplete="off">
        <button type="submit">Update</button>
      </form>
      <a class="button ghost" href="/docs">API Docs</a>
    </section>
    """

    content = f"""
    {banner}
    {editor}
    {last_run}
    <section class="grid" aria-label="Ticker summary cards">{''.join(cards)}</section>
    {spike_sections}
    """
    return layout("FinBrief", content, subtitle=f"Morning brief - {escape(latest_date)}")


def render_holding_card(holding: dict, latest_date: str) -> str:
    ticker = holding["ticker"]
    aggregate = holding["aggregate"]
    if not aggregate:
        return (
            f"<a class='card muted' href='/ticker/{escape(ticker)}/view'>"
            f"<h2>{escape(ticker)}</h2><p>No headlines for {escape(latest_date)}</p></a>"
        )

    tone = "negative" if holding["is_negative_spike"] else score_tone(float(aggregate["weighted_score"]))
    flag = "<span class='flag'>Spike</span>" if holding["is_negative_spike"] else ""
    return (
        f"<a class='card {tone}' href='/ticker/{escape(ticker)}/view'>"
        f"<div class='card-head'><h2>{escape(ticker)}</h2>{flag}</div>"
        f"<p class='score'>{float(aggregate['weighted_score']):+.3f}</p>"
        f"<p>{aggregate['headline_count']} headlines</p>"
        f"<p>{aggregate['positive_count']}/{aggregate['neutral_count']}/{aggregate['negative_count']} pos/neu/neg</p>"
        f"<p>{aggregate['high_conf_negative_count']} high-confidence negative</p>"
        f"</a>"
    )


def render_urgency_banner(data: dict) -> str:
    count = data["negative_spike_count"]
    total = len(data["active_tickers"])
    date = escape(data["aggregate_date"] or "latest date")
    if count:
        links = " ".join(
            f"<a href='/ticker/{escape(spike['ticker'])}/view'>{escape(spike['ticker'])}</a>"
            for spike in data["negative_spikes"]
        )
        return (
            f"<section class='banner danger'><div><strong>{count} of {total} holdings</strong> "
            f"show negative sentiment spikes for {date}.</div><div class='banner-links'>{links}</div></section>"
        )
    return (
        f"<section class='banner calm'><strong>No negative sentiment spikes</strong> detected for {date}.</section>"
    )


def render_spike_section(spike: dict) -> str:
    headlines = spike["responsible_headlines"][:5]
    if not headlines:
        return ""
    items = "".join(
        f"<li><span>{float(headline['confidence']):.3f}</span> "
        f"<a href='{escape(headline['url'])}'>{escape(headline['title'])}</a></li>"
        for headline in headlines
    )
    return f"<section class='panel'><h3>{escape(spike['ticker'])}: why flagged?</h3><ul>{items}</ul></section>"


def render_last_run(last_run: dict | None) -> str:
    if not last_run:
        return "<p class='meta'>No pipeline runs recorded.</p>"
    return (
        f"<p class='meta'>Last run #{last_run['id']} - {escape(last_run['completed_at'])} - "
        f"{last_run['articles_scored']} scored - fetch {last_run['fetch_seconds']}s - "
        f"score {last_run['score_seconds']}s</p>"
    )


def render_ticker_detail(detail: dict) -> str:
    ticker = detail["ticker"]
    latest_date = detail["aggregate_date"] or "No aggregate date"
    chart = render_score_chart(detail["aggregates"])
    aggregate_table = render_aggregate_table(detail["aggregates"])
    headlines = "".join(render_headline(headline) for headline in detail["headlines"])
    content = f"""
    <p><a class="button ghost" href="/">Back to portfolio</a></p>
    <section class="ticker-head">
      <div><h2>{escape(ticker)}</h2><p class="meta">Latest article date: {escape(latest_date)}</p></div>
      <a class="button ghost" href="/ticker/{escape(ticker)}">JSON</a>
    </section>
    <section class="panel">
      <h3>14-day sentiment path</h3>
      {chart}
      {aggregate_table}
    </section>
    <section class="headline-list">
      <h3>Headlines driving today's signal</h3>
      {headlines or '<p>No headlines for the latest aggregate date.</p>'}
    </section>
    """
    return layout(f"{ticker} - FinBrief", content, subtitle="Ticker drill-down")


def render_score_chart(aggregates: list[dict]) -> str:
    if not aggregates:
        return "<p class='meta'>No aggregate history yet.</p>"

    rows = list(reversed(aggregates))
    width, height, pad = 720, 180, 24
    if len(rows) == 1:
        points = [(width / 2, height / 2)]
    else:
        points = []
        for index, row in enumerate(rows):
            x = pad + (index * (width - 2 * pad) / (len(rows) - 1))
            score = max(min(float(row["weighted_score"]), 1.0), -1.0)
            y = pad + ((1.0 - score) / 2.0) * (height - 2 * pad)
            points.append((x, y))

    path = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    dots = "".join(f"<circle cx='{x:.1f}' cy='{y:.1f}' r='4'></circle>" for x, y in points)
    zero_y = pad + 0.5 * (height - 2 * pad)
    labels = "".join(
        f"<span>{escape(row['aggregate_date'][5:])}</span>" for row in rows
    )
    return f"""
    <div class="chart-wrap">
      <svg viewBox="0 0 {width} {height}" role="img" aria-label="Weighted sentiment chart">
        <line class="zero" x1="{pad}" y1="{zero_y:.1f}" x2="{width - pad}" y2="{zero_y:.1f}"></line>
        <polyline points="{path}"></polyline>
        {dots}
      </svg>
      <div class="chart-labels">{labels}</div>
    </div>
    """


def render_aggregate_table(aggregates: list[dict]) -> str:
    rows = "".join(
        f"<tr><td>{escape(row['aggregate_date'])}</td><td>{float(row['weighted_score']):+.3f}</td>"
        f"<td>{row['headline_count']}</td><td>{row['positive_count']}/{row['neutral_count']}/{row['negative_count']}</td></tr>"
        for row in aggregates
    )
    return (
        "<table><thead><tr><th>Date</th><th>Score</th><th>Headlines</th><th>Pos/Neu/Neg</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )


def render_headline(headline: dict) -> str:
    label = headline["label"]
    return (
        f"<article id='headline-{escape(headline['published_at'])}' class='headline {escape(label)}'>"
        f"<div><span class='badge'>{escape(label)} {float(headline['confidence']):.3f}</span>"
        f"<span class='meta'>{escape(headline['source'])} - {escape(headline['published_at'])}</span></div>"
        f"<h3><a href='{escape(headline['url'])}'>{escape(headline['title'])}</a></h3>"
        f"<p>{escape(headline['summary'])}</p></article>"
    )


def layout(title: str, content: str, subtitle: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>
  <style>
    :root {{ font-family: Inter, Segoe UI, Arial, sans-serif; background: #f5f7f2; color: #1d2420; }}
    body {{ margin: 0; }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 28px 20px 56px; }}
    header {{ display: flex; justify-content: space-between; gap: 18px; align-items: baseline; margin-bottom: 22px; }}
    h1 {{ margin: 0; font-size: 34px; letter-spacing: 0; }}
    h2 {{ margin: 0; font-size: 28px; letter-spacing: 0; }}
    h3 {{ margin: 0 0 12px; letter-spacing: 0; }}
    a {{ color: inherit; }}
    .meta {{ color: #66736b; font-size: 14px; }}
    .toolbar {{ display: flex; justify-content: space-between; align-items: end; gap: 14px; margin: 18px 0; flex-wrap: wrap; }}
    form {{ display: flex; gap: 10px; align-items: end; flex-wrap: wrap; }}
    label {{ display: block; font-size: 13px; color: #46524a; margin-bottom: 4px; }}
    input {{ width: min(520px, 70vw); padding: 10px 12px; border: 1px solid #cfd8cf; border-radius: 6px; font: inherit; background: white; }}
    button, .button {{ display: inline-block; padding: 10px 12px; border: 1px solid #1d2420; border-radius: 6px; background: #1d2420; color: white; text-decoration: none; font: inherit; cursor: pointer; }}
    .button.ghost {{ background: white; color: #1d2420; border-color: #cfd8cf; }}
    .banner {{ border-left: 6px solid; padding: 18px 20px; margin: 0 0 18px; background: white; box-shadow: 0 1px 8px #0000000d; }}
    .banner.danger {{ border-color: #b42318; }}
    .banner.calm {{ border-color: #16805d; }}
    .banner-links {{ margin-top: 8px; display: flex; gap: 10px; }}
    .banner-links a {{ font-weight: 700; color: #b42318; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(205px, 1fr)); gap: 14px; margin: 22px 0; }}
    .card {{ display: block; min-height: 150px; padding: 16px; text-decoration: none; background: white; border: 1px solid #dfe4dd; border-radius: 8px; }}
    .card:hover {{ border-color: #859080; box-shadow: 0 6px 20px #00000012; }}
    .card-head {{ display: flex; justify-content: space-between; gap: 8px; align-items: center; }}
    .card h2 {{ font-size: 22px; }}
    .score {{ font-size: 30px; font-weight: 700; margin: 10px 0; }}
    .positive .score {{ color: #137a4b; }}
    .negative .score, .danger strong {{ color: #b42318; }}
    .neutral .score {{ color: #5d685f; }}
    .muted {{ color: #6f786f; }}
    .flag, .badge {{ display: inline-block; padding: 4px 8px; border-radius: 999px; font-size: 13px; background: #fee4df; color: #9f1f15; }}
    .panel, .headline {{ background: white; border: 1px solid #dfe4dd; border-radius: 8px; padding: 16px; margin: 16px 0; }}
    ul {{ margin: 0; padding-left: 20px; }}
    li {{ margin: 8px 0; }}
    li span {{ font-family: Consolas, monospace; color: #9a3412; }}
    .ticker-head {{ display: flex; justify-content: space-between; align-items: center; gap: 14px; margin: 18px 0; }}
    .chart-wrap {{ overflow-x: auto; margin: 8px 0 18px; }}
    svg {{ width: 100%; min-width: 520px; height: 190px; background: #fbfcfa; border: 1px solid #edf0ea; border-radius: 8px; }}
    polyline {{ fill: none; stroke: #315f72; stroke-width: 4; stroke-linejoin: round; }}
    circle {{ fill: #315f72; }}
    line.zero {{ stroke: #cfd8cf; stroke-dasharray: 5 5; }}
    .chart-labels {{ display: flex; justify-content: space-between; min-width: 520px; color: #66736b; font-size: 12px; padding: 0 6px; }}
    table {{ width: 100%; border-collapse: collapse; background: white; }}
    th, td {{ text-align: left; padding: 10px 12px; border-bottom: 1px solid #eef1ec; }}
    th {{ background: #eef1ec; }}
    .headline h3 {{ margin: 8px 0; font-size: 18px; }}
    .headline p {{ margin-bottom: 0; color: #46524a; }}
    .headline.negative .badge {{ background: #fee4df; color: #9f1f15; }}
    .headline.positive .badge {{ background: #dcf4e8; color: #137a4b; }}
    @media (max-width: 640px) {{
      header, .ticker-head {{ align-items: flex-start; flex-direction: column; }}
      input {{ width: calc(100vw - 64px); }}
    }}
  </style>
</head>
<body>
  <main>
    <header><h1>FinBrief</h1><span class="meta">{subtitle}</span></header>
    {content}
  </main>
</body>
</html>"""


def score_tone(score: float) -> str:
    if score > 0.15:
        return "positive"
    if score < -0.15:
        return "negative"
    return "neutral"


def _split_tickers(raw: str) -> list[str]:
    return [ticker.strip().upper() for ticker in raw.split(",") if ticker.strip()]


def escape(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)
