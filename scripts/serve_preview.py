"""Serve a lightweight local HTML preview of the FinBrief SQLite data."""

from __future__ import annotations

import argparse
import html
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from finbrief.db import connect, find_negative_spikes, get_negative_headlines, list_active_tickers


class PreviewHandler(BaseHTTPRequestHandler):
    db_path: Path

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        try:
            if path == "/":
                body = render_home(self.db_path)
            elif path.startswith("/ticker/"):
                ticker = unquote(path.split("/", 2)[2]).upper()
                body = render_ticker(self.db_path, ticker)
            else:
                self.send_error(404, "Not found")
                return
            payload = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except Exception as exc:
            self.send_error(500, str(exc))

    def log_message(self, format: str, *args) -> None:
        print(f"{self.address_string()} - {format % args}")


def render_home(db_path: Path) -> str:
    with connect(db_path) as conn:
        active = list_active_tickers(conn)
        latest_date = latest_aggregate_date(conn)
        aggregates = aggregate_rows(conn, latest_date) if latest_date else {}
        spikes = find_negative_spikes(conn, latest_date) if latest_date else []
        spike_tickers = {spike["ticker"] for spike in spikes}
        responsible = {
            spike["ticker"]: get_negative_headlines(conn, spike["ticker"], latest_date) for spike in spikes
        }
        last_run = conn.execute(
            """
            SELECT id, completed_at, articles_fetched, articles_scored, fetch_seconds, score_seconds
            FROM pipeline_runs
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()

    if not latest_date:
        content = "<section class='empty'>No aggregate data yet. Run the pipeline with <code>--db data\\finbrief.db</code>.</section>"
        return layout("FinBrief Preview", content)

    banner_class = "banner danger" if spikes else "banner calm"
    if spikes:
        banner = (
            f"<section class='{banner_class}'><strong>{len(spikes)} of {len(active)} holdings</strong> "
            f"show negative sentiment spikes for {escape(latest_date)}.</section>"
        )
    else:
        banner = (
            f"<section class='{banner_class}'><strong>No negative sentiment spikes</strong> "
            f"detected for {escape(latest_date)}.</section>"
        )

    cards = []
    for ticker in active:
        row = aggregates.get(ticker)
        if not row:
            cards.append(
                f"<a class='card muted' href='/ticker/{escape(ticker)}'>"
                f"<h2>{escape(ticker)}</h2><p>No headlines for {escape(latest_date)}</p></a>"
            )
            continue
        tone = "negative" if ticker in spike_tickers else score_tone(row["weighted_score"])
        cards.append(
            f"<a class='card {tone}' href='/ticker/{escape(ticker)}'>"
            f"<h2>{escape(ticker)}</h2>"
            f"<p class='score'>{float(row['weighted_score']):+.3f}</p>"
            f"<p>{row['headline_count']} headlines · {row['positive_count']}/{row['neutral_count']}/{row['negative_count']} pos/neu/neg</p>"
            f"<p>{row['high_conf_negative_count']} high-confidence negative</p>"
            f"</a>"
        )

    why = []
    for ticker, headlines in responsible.items():
        items = "".join(
            f"<li><span>{float(h['confidence']):.3f}</span> {escape(h['title'])}</li>" for h in headlines[:5]
        )
        why.append(f"<section><h3>{escape(ticker)}: why flagged?</h3><ul>{items}</ul></section>")

    run_text = ""
    if last_run:
        run_text = (
            f"<p class='meta'>Last run #{last_run['id']} · {escape(last_run['completed_at'])} · "
            f"{last_run['articles_scored']} scored · fetch {last_run['fetch_seconds']}s · score {last_run['score_seconds']}s</p>"
        )

    content = f"""
    {banner}
    {run_text}
    <section class="grid">{''.join(cards)}</section>
    {''.join(why)}
    """
    return layout("FinBrief Preview", content)


def render_ticker(db_path: Path, ticker: str) -> str:
    with connect(db_path) as conn:
        latest_date = latest_aggregate_date(conn)
        aggregates = conn.execute(
            """
            SELECT *
            FROM daily_aggregates
            WHERE ticker = ?
            ORDER BY aggregate_date DESC
            LIMIT 14
            """,
            (ticker,),
        ).fetchall()
        headlines = conn.execute(
            """
            SELECT h.title, h.summary, h.url, h.source, h.published_at, s.label, s.confidence
            FROM headlines h
            JOIN scores s ON s.headline_id = h.id
            WHERE h.ticker = ?
              AND substr(h.published_at, 1, 10) = ?
            ORDER BY s.label = 'negative' DESC, s.confidence DESC, h.published_at DESC
            """,
            (ticker, latest_date),
        ).fetchall() if latest_date else []

    spark = "".join(
        f"<tr><td>{escape(row['aggregate_date'])}</td><td>{float(row['weighted_score']):+.3f}</td>"
        f"<td>{row['headline_count']}</td><td>{row['positive_count']}/{row['neutral_count']}/{row['negative_count']}</td></tr>"
        for row in aggregates
    )
    articles = "".join(
        f"<article class='headline {escape(row['label'])}'>"
        f"<div><span class='badge'>{escape(row['label'])} {float(row['confidence']):.3f}</span>"
        f"<span class='meta'>{escape(row['source'])} · {escape(row['published_at'])}</span></div>"
        f"<h3><a href='{escape(row['url'])}'>{escape(row['title'])}</a></h3>"
        f"<p>{escape(row['summary'])}</p></article>"
        for row in headlines
    )
    content = f"""
    <p><a href="/">← Back to portfolio</a></p>
    <section class="ticker-head"><h2>{escape(ticker)}</h2><p>{escape(latest_date or 'No aggregate date')}</p></section>
    <table><thead><tr><th>Date</th><th>Score</th><th>Headlines</th><th>Pos/Neu/Neg</th></tr></thead><tbody>{spark}</tbody></table>
    <section>{articles or '<p>No headlines for latest aggregate date.</p>'}</section>
    """
    return layout(f"{ticker} · FinBrief", content)


def latest_aggregate_date(conn) -> str | None:
    row = conn.execute("SELECT max(aggregate_date) FROM daily_aggregates").fetchone()
    return row[0] if row else None


def aggregate_rows(conn, aggregate_date: str) -> dict:
    rows = conn.execute(
        "SELECT * FROM daily_aggregates WHERE aggregate_date = ? ORDER BY ticker",
        (aggregate_date,),
    ).fetchall()
    return {row["ticker"]: row for row in rows}


def layout(title: str, content: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>
  <style>
    :root {{ color-scheme: light; font-family: Inter, Segoe UI, Arial, sans-serif; background: #f7f8f4; color: #1d2420; }}
    body {{ margin: 0; }}
    main {{ max-width: 1120px; margin: 0 auto; padding: 32px 20px 56px; }}
    header {{ display: flex; justify-content: space-between; gap: 16px; align-items: baseline; margin-bottom: 24px; }}
    h1 {{ font-size: 32px; margin: 0; letter-spacing: 0; }}
    h2, h3 {{ letter-spacing: 0; }}
    a {{ color: inherit; }}
    .meta {{ color: #66736b; font-size: 14px; }}
    .banner {{ border-left: 6px solid; padding: 18px 20px; margin: 0 0 18px; background: white; box-shadow: 0 1px 8px #0000000d; }}
    .banner.danger {{ border-color: #b42318; }}
    .banner.calm {{ border-color: #16805d; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 14px; margin: 22px 0; }}
    .card {{ display: block; text-decoration: none; background: white; border: 1px solid #dfe4dd; border-radius: 8px; padding: 16px; min-height: 148px; }}
    .card:hover {{ border-color: #859080; box-shadow: 0 6px 20px #00000012; }}
    .card h2 {{ margin: 0 0 8px; font-size: 22px; }}
    .score {{ font-size: 28px; font-weight: 700; margin: 0 0 10px; }}
    .positive .score {{ color: #137a4b; }}
    .negative .score, .danger strong {{ color: #b42318; }}
    .neutral .score {{ color: #5d685f; }}
    .muted {{ color: #6f786f; }}
    section {{ margin: 24px 0; }}
    ul {{ padding-left: 20px; }}
    li {{ margin: 8px 0; }}
    li span {{ font-family: Consolas, monospace; color: #9a3412; }}
    table {{ width: 100%; border-collapse: collapse; background: white; border: 1px solid #dfe4dd; border-radius: 8px; overflow: hidden; }}
    th, td {{ text-align: left; padding: 10px 12px; border-bottom: 1px solid #eef1ec; }}
    th {{ background: #eef1ec; }}
    .headline {{ background: white; border: 1px solid #dfe4dd; border-radius: 8px; padding: 16px; margin: 12px 0; }}
    .headline h3 {{ margin: 8px 0; font-size: 18px; }}
    .headline p {{ margin-bottom: 0; color: #46524a; }}
    .badge {{ display: inline-block; padding: 4px 8px; border-radius: 999px; background: #eef1ec; font-size: 13px; margin-right: 10px; }}
    .headline.negative .badge {{ background: #fee4df; color: #9f1f15; }}
    .headline.positive .badge {{ background: #dcf4e8; color: #137a4b; }}
    .ticker-head {{ display: flex; justify-content: space-between; align-items: baseline; }}
  </style>
</head>
<body>
  <main>
    <header><h1>FinBrief</h1><span class="meta">Local SQLite preview</span></header>
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


def escape(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve a local FinBrief SQLite preview")
    parser.add_argument("--db", type=Path, default=Path("data/finbrief.db"), help="SQLite database path")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind")
    args = parser.parse_args()

    PreviewHandler.db_path = args.db
    server = ThreadingHTTPServer((args.host, args.port), PreviewHandler)
    print(f"Serving FinBrief preview at http://{args.host}:{args.port}/ using {args.db}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
