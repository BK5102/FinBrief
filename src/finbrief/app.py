"""FastAPI app for the FinBrief dashboard."""

from __future__ import annotations

import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv

from finbrief.auth import hash_password, make_session, read_session, verify_password
from finbrief.db import (
    connect,
    create_user,
    deactivate_tickers,
    get_user_by_email,
    get_user_by_id,
    list_active_tickers,
    set_active_tickers,
)
from finbrief.queries import get_recent_runs, get_summary, get_ticker_detail
from finbrief.runner import run_pipeline_cycle

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")
DEFAULT_DB_PATH = Path(os.getenv("FINBRIEF_DB", "data/finbrief.db"))
TEMPLATES = Jinja2Templates(directory=str(ROOT / "templates"))

_REFRESH_LOCK = threading.Lock()
_REFRESH_STATES: dict[int, dict] = {}

app = FastAPI(title="FinBrief", version="0.2.0", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _get_user(request: Request) -> dict | None:
    token = request.cookies.get("session")
    if not token:
        return None
    user_id = read_session(token)
    if not user_id:
        return None
    with connect(DEFAULT_DB_PATH) as conn:
        return get_user_by_id(conn, user_id)


def _render(request: Request, template: str, ctx: dict, status_code: int = 200) -> Response:
    ctx.setdefault("current_user", _get_user(request))
    return TEMPLATES.TemplateResponse(request, template, ctx, status_code=status_code)


def _set_session(response: Response, user_id: int) -> None:
    response.set_cookie(
        "session", make_session(user_id),
        httponly=True, samesite="lax", max_age=86400 * 30,
    )


# ---------------------------------------------------------------------------
# System
# ---------------------------------------------------------------------------

@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@app.get("/signup")
def signup_page(request: Request):
    if _get_user(request):
        return RedirectResponse("/", status_code=303)
    return _render(request, "signup.html", {})


@app.post("/signup")
async def signup(request: Request):
    data = await _form_data(request, "email", "password", "password2")
    email, password, password2 = data["email"], data["password"], data["password2"]

    if not email or not password:
        return _render(request, "signup.html", {"error": "Email and password are required."}, 400)
    if len(password) < 8:
        return _render(request, "signup.html", {"error": "Password must be at least 8 characters.", "email": email}, 400)
    if password != password2:
        return _render(request, "signup.html", {"error": "Passwords do not match.", "email": email}, 400)

    with connect(DEFAULT_DB_PATH) as conn:
        if get_user_by_email(conn, email):
            return _render(request, "signup.html", {"error": "An account with that email already exists.", "email": email}, 400)
        user_id = create_user(conn, email, hash_password(password))

    response = RedirectResponse("/", status_code=303)
    _set_session(response, user_id)
    return response


@app.get("/login")
def login_page(request: Request):
    if _get_user(request):
        return RedirectResponse("/", status_code=303)
    return _render(request, "login.html", {})


@app.post("/login")
async def login(request: Request):
    data = await _form_data(request, "email", "password")
    email, password = data["email"], data["password"]

    with connect(DEFAULT_DB_PATH) as conn:
        user = get_user_by_email(conn, email)

    if not user or not verify_password(password, user["password_hash"]):
        return _render(request, "login.html", {"error": "Incorrect email or password.", "email": email}, 401)

    response = RedirectResponse("/", status_code=303)
    _set_session(response, user["id"])
    return response


@app.post("/logout")
def logout():
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie("session")
    return response


# ---------------------------------------------------------------------------
# Landing / dashboard
# ---------------------------------------------------------------------------

@app.get("/")
def home(request: Request):
    user = _get_user(request)
    if not user:
        return _render(request, "landing.html", {})

    user_id = user["id"]
    with connect(DEFAULT_DB_PATH) as conn:
        data = get_summary(conn, user_id)
        runs = get_recent_runs(conn, user_id)

    return _render(request, "home.html", {
        "summary": data,
        "recent_runs": runs,
        "refresh": _get_refresh_state(user_id),
        "portfolio_value": ",".join(data["active_tickers"]),
        "holding_cards": [_prepare_holding(h) for h in data["holdings"]],
    })


# ---------------------------------------------------------------------------
# Portfolio
# ---------------------------------------------------------------------------

@app.get("/portfolio")
def portfolio(request: Request) -> dict:
    user = _get_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    with connect(DEFAULT_DB_PATH) as conn:
        return {"tickers": list_active_tickers(conn, user["id"])}


@app.post("/portfolio")
async def update_portfolio(request: Request):
    user = _get_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    raw = await _form_value(request, "tickers")
    tickers = _split_tickers(raw)
    with connect(DEFAULT_DB_PATH) as conn:
        active = set_active_tickers(conn, tickers, user["id"])
    if _wants_html(request):
        return RedirectResponse("/", status_code=303)
    return {"tickers": active}


@app.post("/portfolio/add")
async def add_portfolio_ticker(request: Request):
    user = _get_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    raw = await _form_value(request, "ticker")
    ticker = _normalize_ticker(raw)
    if not ticker:
        if _wants_html(request):
            return RedirectResponse("/", status_code=303)
        raise HTTPException(status_code=400, detail="Ticker is required")

    with connect(DEFAULT_DB_PATH) as conn:
        active = list_active_tickers(conn, user["id"])
        if ticker not in active:
            active.append(ticker)
        active = set_active_tickers(conn, active, user["id"])

    if _wants_html(request):
        return RedirectResponse("/", status_code=303)
    return {"tickers": active}


@app.post("/portfolio/remove/{symbol}")
def remove_portfolio_ticker(request: Request, symbol: str):
    user = _get_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    ticker = _normalize_ticker(symbol)
    if not ticker:
        if _wants_html(request):
            return RedirectResponse("/", status_code=303)
        raise HTTPException(status_code=400, detail="Ticker is required")

    with connect(DEFAULT_DB_PATH) as conn:
        deactivate_tickers(conn, [ticker], user["id"])
        active = list_active_tickers(conn, user["id"])

    if _wants_html(request):
        return RedirectResponse("/", status_code=303)
    return {"removed": ticker, "tickers": active}


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------

@app.post("/refresh")
def start_refresh(request: Request):
    user = _get_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    _start_background_refresh(user["id"])
    if _wants_html(request):
        return RedirectResponse("/", status_code=303)
    return {"started": True, "refresh": _get_refresh_state(user["id"])}


@app.get("/refresh")
def refresh_landing(request: Request):
    user = _get_user(request)
    if _wants_html(request):
        return RedirectResponse("/", status_code=303)
    return {
        "detail": "Use POST /refresh to start a manual refresh. Use GET /refresh/status to check status.",
        "refresh": _get_refresh_state(user["id"]) if user else {},
    }


@app.get("/refresh/status")
def refresh_status(request: Request):
    user = _get_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return _get_refresh_state(user["id"])


# ---------------------------------------------------------------------------
# Ticker detail
# ---------------------------------------------------------------------------

@app.get("/ticker/{symbol}")
def ticker_detail_json(request: Request, symbol: str, date: str | None = None) -> dict:
    user = _get_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return _ticker_detail(symbol, user["id"], date)


@app.get("/ticker/{symbol}/view")
def ticker_page(request: Request, symbol: str, date: str | None = None):
    user = _get_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)
    try:
        detail = _ticker_detail(symbol, user["id"], date)
    except HTTPException:
        detail = {"ticker": symbol.upper(), "aggregate_date": None, "aggregates": [], "headlines": []}
        return _render(request, "ticker.html", {
            "detail": detail, "chart": _chart_points([]), "aggregate_rows": [],
        }, status_code=404)
    return _render(request, "ticker.html", {
        "detail": detail,
        "chart": _chart_points(detail["aggregates"]),
        "aggregate_rows": detail["aggregates"],
    })


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _ticker_detail(symbol: str, user_id: int, date: str | None = None) -> dict:
    with connect(DEFAULT_DB_PATH) as conn:
        detail = get_ticker_detail(conn, symbol, user_id, aggregate_date=date)
    if not detail["aggregates"] and not detail["headlines"]:
        raise HTTPException(status_code=404, detail=f"No data found for {symbol.upper()}")
    return detail


def _get_refresh_state(user_id: int) -> dict:
    with _REFRESH_LOCK:
        return dict(_REFRESH_STATES.get(user_id, {
            "running": False, "status": "idle",
            "started_at": None, "completed_at": None, "result": None, "error": None,
        }))


def _start_background_refresh(user_id: int) -> bool:
    with _REFRESH_LOCK:
        state = _REFRESH_STATES.get(user_id, {})
        if state.get("running"):
            return False
        _REFRESH_STATES[user_id] = {
            "running": True, "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "completed_at": None, "result": None, "error": None,
        }
    thread = threading.Thread(target=_run_refresh_job, args=(user_id,), daemon=True)
    thread.start()
    return True


def _run_refresh_job(user_id: int) -> None:
    try:
        output = run_pipeline_cycle(
            db_path=DEFAULT_DB_PATH,
            finnhub_key=os.getenv("FINNHUB_API_KEY") or None,
            user_id=user_id,
        )
        result = {
            "tickers": output["tickers"],
            "counts": output["counts"],
            "timings_seconds": output["timings_seconds"],
            "pipeline_run_id": output.get("db", {}).get("pipeline_run_id"),
        }
        with _REFRESH_LOCK:
            _REFRESH_STATES[user_id] = {
                "running": False, "status": "success",
                "started_at": _REFRESH_STATES[user_id]["started_at"],
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "result": result, "error": None,
            }
    except Exception as exc:
        with _REFRESH_LOCK:
            _REFRESH_STATES[user_id] = {
                "running": False, "status": "failure",
                "started_at": _REFRESH_STATES.get(user_id, {}).get("started_at"),
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "result": None, "error": str(exc),
            }


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
        "width": width, "height": height, "pad": pad,
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


async def _form_data(request: Request, *fields: str) -> dict[str, str]:
    body = (await request.body()).decode("utf-8")
    parsed = parse_qs(body)
    return {field: parsed.get(field, [""])[0].strip() for field in fields}


async def _form_value(request: Request, field: str) -> str:
    data = await _form_data(request, field)
    return data[field]


def _wants_html(request: Request) -> bool:
    return "text/html" in request.headers.get("accept", "")


def _split_tickers(raw: str) -> list[str]:
    return [ticker for ticker in (_normalize_ticker(v) for v in raw.split(",")) if ticker]


def _normalize_ticker(raw: str) -> str:
    ticker = raw.strip().upper()
    return ticker if ticker and all(c.isalnum() or c in ".-" for c in ticker) else ""
