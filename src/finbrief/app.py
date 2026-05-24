"""FastAPI app for the FinBrief dashboard/API."""

from __future__ import annotations

import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv

from finbrief.db import connect, list_active_tickers, set_active_tickers
from finbrief.queries import get_recent_runs, get_summary, get_ticker_detail
from finbrief.runner import run_pipeline_cycle

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")
DEFAULT_DB_PATH = Path(os.getenv("FINBRIEF_DB", "data/finbrief.db"))
TEMPLATES = Jinja2Templates(directory=str(ROOT / "templates"))
REFRESH_LOCK = threading.Lock()
REFRESH_STATE = {
    "running": False,
    "status": "idle",
    "started_at": None,
    "completed_at": None,
    "result": None,
    "error": None,
}

app = FastAPI(title="FinBrief", version="0.1.0", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")


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
    raw = parse_qs(body).get("tickers", [""])[0]
    tickers = _split_tickers(raw)
    with connect(DEFAULT_DB_PATH) as conn:
        active = set_active_tickers(conn, tickers)

    if "text/html" in request.headers.get("accept", ""):
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


@app.post("/refresh")
def start_refresh(request: Request):
    started = _start_background_refresh()
    if "text/html" in request.headers.get("accept", ""):
        return RedirectResponse("/", status_code=303)
    return {"started": started, "refresh": refresh_status()}


@app.get("/refresh")
def refresh_landing(request: Request):
    if "text/html" in request.headers.get("accept", ""):
        return RedirectResponse("/", status_code=303)
    return {
        "detail": "Use POST /refresh to start a manual refresh. Use GET /refresh/status to inspect status.",
        "refresh": refresh_status(),
    }


@app.get("/refresh/status")
def refresh_status() -> dict:
    with REFRESH_LOCK:
        return dict(REFRESH_STATE)


@app.get("/ticker/{symbol}")
def ticker_detail(symbol: str, date: str | None = None) -> dict:
    detail = _ticker_detail(symbol, date)
    return detail


@app.get("/")
def home(request: Request):
    with connect(DEFAULT_DB_PATH) as conn:
        data = get_summary(conn)
        runs = get_recent_runs(conn)

    return TEMPLATES.TemplateResponse(
        request,
        "home.html",
        {
            "summary": data,
            "recent_runs": runs,
            "refresh": refresh_status(),
            "portfolio_value": ",".join(data["active_tickers"]),
            "holding_cards": [_prepare_holding(holding) for holding in data["holdings"]],
        },
    )


@app.get("/ticker/{symbol}/view")
def ticker_page(request: Request, symbol: str, date: str | None = None):
    detail = _ticker_detail(symbol, date)
    return TEMPLATES.TemplateResponse(
        request,
        "ticker.html",
        {
            "detail": detail,
            "chart": _chart_points(detail["aggregates"]),
            "aggregate_rows": detail["aggregates"],
        },
    )


def _ticker_detail(symbol: str, date: str | None = None) -> dict:
    with connect(DEFAULT_DB_PATH) as conn:
        detail = get_ticker_detail(conn, symbol, aggregate_date=date)
    if not detail["aggregates"] and not detail["headlines"]:
        raise HTTPException(status_code=404, detail=f"No data found for {symbol.upper()}")
    return detail


def _start_background_refresh() -> bool:
    with REFRESH_LOCK:
        if REFRESH_STATE["running"]:
            return False
        REFRESH_STATE.update(
            {
                "running": True,
                "status": "running",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "completed_at": None,
                "result": None,
                "error": None,
            }
        )
    thread = threading.Thread(target=_run_refresh_job, daemon=True)
    thread.start()
    return True


def _run_refresh_job() -> None:
    try:
        output = run_pipeline_cycle(db_path=DEFAULT_DB_PATH, finnhub_key=os.getenv("FINNHUB_API_KEY") or None)
        result = {
            "tickers": output["tickers"],
            "counts": output["counts"],
            "timings_seconds": output["timings_seconds"],
            "pipeline_run_id": output.get("db", {}).get("pipeline_run_id"),
        }
        with REFRESH_LOCK:
            REFRESH_STATE.update(
                {
                    "running": False,
                    "status": "success",
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                    "result": result,
                    "error": None,
                }
            )
    except Exception as exc:
        with REFRESH_LOCK:
            REFRESH_STATE.update(
                {
                    "running": False,
                    "status": "failure",
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                    "result": None,
                    "error": str(exc),
                }
            )


def _prepare_holding(holding: dict) -> dict:
    aggregate = holding["aggregate"]
    if not aggregate:
        return {**holding, "tone": "muted", "score_text": "No data"}
    score = float(aggregate["weighted_score"])
    return {
        **holding,
        "tone": "negative" if holding["is_negative_spike"] else _score_tone(score),
        "score_text": f"{score:+.3f}",
    }


def _chart_points(aggregates: list[dict]) -> dict:
    if not aggregates:
        return {"points": "", "dots": [], "labels": []}

    rows = list(reversed(aggregates))
    width, height, pad = 720, 180, 24
    points = []
    if len(rows) == 1:
        points = [(width / 2, height / 2)]
    else:
        for index, row in enumerate(rows):
            x = pad + (index * (width - 2 * pad) / (len(rows) - 1))
            score = max(min(float(row["weighted_score"]), 1.0), -1.0)
            y = pad + ((1.0 - score) / 2.0) * (height - 2 * pad)
            points.append((x, y))

    return {
        "width": width,
        "height": height,
        "pad": pad,
        "zero_y": pad + 0.5 * (height - 2 * pad),
        "points": " ".join(f"{x:.1f},{y:.1f}" for x, y in points),
        "dots": [{"x": f"{x:.1f}", "y": f"{y:.1f}"} for x, y in points],
        "labels": [row["aggregate_date"][5:] for row in rows],
    }


def _score_tone(score: float) -> str:
    if score > 0.15:
        return "positive"
    if score < -0.15:
        return "negative"
    return "neutral"


def _split_tickers(raw: str) -> list[str]:
    return [ticker.strip().upper() for ticker in raw.split(",") if ticker.strip()]
