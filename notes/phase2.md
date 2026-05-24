# Phase 2 — Persistence & Aggregation: Notes

## Current status

Phase 2 implementation has started. The one-shot pipeline can now optionally persist a run into SQLite while still preserving the Phase 1 JSON output.

## Implemented

- `src/finbrief/db.py`
  - SQLite connection helper.
  - Schema initialization.
  - `tickers`, `pipeline_runs`, `headlines`, `scores`, and `daily_aggregates` tables.
  - Upsert logic for tickers, headlines, and scores.
  - Daily aggregate recomputation for ticker/date pairs touched by a pipeline run.
  - Initial negative-spike query helper using rolling aggregate history.
- `src/finbrief/pipeline.py`
  - New optional `--db PATH` flag.
  - When set, pipeline persists fetched headlines, scores, run metadata, and recomputed aggregates.
- `scripts/benchmark_phrasebank.py`
  - Local CSV benchmark harness for Phase 1 validation.
  - Expected columns: `sentence,label`.
- `scripts/portfolio.py`
  - Stores the active portfolio in SQLite.
  - Supports `set`, `add`, `remove`, and `list`.
- `scripts/inspect_db.py`
  - Prints table counts, latest aggregate rows, negative-spike results, and responsible high-confidence negative headlines.
- `scripts/backfill_finnhub.py`
  - Backfills historical date windows from Finnhub into SQLite.
  - Requires `FINNHUB_API_KEY` in local `.env`.
- `src/finbrief/queries.py`
  - Shared read-side data contract for the future dashboard/API.
  - Provides `get_summary(...)` and `get_ticker_detail(...)`.
- `scripts/daily_run.py`
  - Runs one daily fetch/score/persist cycle using the active SQLite portfolio.
  - Writes structured JSONL logs to `logs/daily_runs.jsonl`.
- `scripts/schedule_daily.py`
  - Lightweight local scheduler that calls `daily_run.py` at a configured wall-clock time.
  - Useful for development; Windows Task Scheduler remains the better unattended production option.
- `src/finbrief/app.py`
  - FastAPI app using the shared query helpers.
  - Provides `/portfolio`, `/summary`, `/ticker/{symbol}`, `/ticker/{symbol}/view`, `/health`, `/docs`, and dashboard pages at `/` plus ticker drill-down URLs.
- `templates/`
  - Jinja templates for the dashboard shell, home view, and ticker drill-down view.
- `static/styles.css`
  - Shared dashboard styling.

## SQLite schema

| Table | Purpose |
| ----- | ------- |
| `tickers` | Active portfolio symbols, one row per symbol. |
| `pipeline_runs` | Run-level observability: timestamps, status, tickers, counts, fetch/score timings. |
| `headlines` | Deduplicated fetched headlines keyed by a stable SHA-256 fingerprint. |
| `scores` | One FinBERT score row per headline. |
| `daily_aggregates` | One ticker/date aggregate for dashboard summary and urgency logic. |

## Aggregate logic

Sentiment labels map to numeric values:

- `positive` = `1.0`
- `neutral` = `0.0`
- `negative` = `-1.0`

Ticker-day score:

```text
sum(sentiment_value * confidence) / sum(confidence)
```

The aggregate also stores headline count, positive/neutral/negative counts, high-confidence negative count (`negative` with confidence `>= 0.7`), and average confidence.

## Urgency-spike helper

Implemented in `find_negative_spikes(...)`:

- Requires today's aggregate row.
- Requires at least 2 high-confidence negative headlines by default.
- Compares today's weighted score against prior rolling aggregate history.
- Flags when today's score is lower than `rolling_mean - (1.5 * rolling_std)`.

This cannot produce meaningful spikes until enough historical aggregates exist.

## How to run

Seed or inspect the active portfolio:

```powershell
$env:PYTHONPATH = "src"
.venv\Scripts\python.exe scripts\portfolio.py --db data\finbrief.db set AAPL,MSFT,NVDA,JPM,TSLA
.venv\Scripts\python.exe scripts\portfolio.py --db data\finbrief.db list
```

Run with explicit tickers:

```powershell
$env:PYTHONPATH = "src"
.venv\Scripts\python.exe -m finbrief.pipeline --tickers AAPL,MSFT,NVDA --db data\finbrief.db --pretty --out today.json
```

Run using active tickers from SQLite:

```powershell
$env:PYTHONPATH = "src"
.venv\Scripts\python.exe -m finbrief.pipeline --db data\finbrief.db --pretty --out data\latest_run.json
```

Inspect persisted data:

```powershell
$env:PYTHONPATH = "src"
.venv\Scripts\python.exe scripts\inspect_db.py --db data\finbrief.db
```

Run one daily cycle:

```powershell
.venv\Scripts\python.exe scripts\daily_run.py --db data\finbrief.db
```

Run a local scheduler:

```powershell
.venv\Scripts\python.exe scripts\schedule_daily.py --db data\finbrief.db --time 07:00
```

Run FastAPI:

```powershell
.venv\Scripts\python.exe -m uvicorn finbrief.app:app --app-dir src --host 127.0.0.1 --port 8780
```

Backfill from Finnhub:

```powershell
$env:PYTHONPATH = "src"
.venv\Scripts\python.exe scripts\backfill_finnhub.py --db data\finbrief.db --days 7
```

## First real persisted run

Run date: 2026-05-23 local / 2026-05-24 UTC.

- Tickers: `AAPL,MSFT,NVDA,JPM,TSLA`
- Headlines persisted: 26
- Scores persisted: 26
- Aggregates created: 2 (`NVDA`, `TSLA`)
- Zero-headline tickers for that UTC window: `AAPL`, `MSFT`, `JPM`
- Negative spikes: 0 (expected with only one aggregate day; rolling history is not populated yet)

## First 7-day Finnhub backfill

Run date: 2026-05-23 local.

- Window: 2026-05-18 through 2026-05-24 UTC
- Tickers: active DB portfolio (`AAPL,JPM,MSFT,NVDA,TSLA`)
- Fetched/scored rows: 1,137
- Unique persisted headlines/scores after dedupe: 1,030
- Aggregate dates: 2026-05-18 through 2026-05-24
- Daily aggregate rows: 25
- Latest-date negative spikes: 1 (`NVDA`)
- CPU scoring time: ~296 seconds

Follow-up finding: the first inspection showed the same syndicated NVDA headline counted through yfinance, Yahoo RSS, and Finnhub. Dedupe now uses ticker/date/normalized-title rather than URL, because finance syndication often produces different source URLs for the same story.

## Next execution checklist

1. Run the PhraseBank benchmark once a labeled CSV is available.
2. Run a real persisted pipeline pass with `--db data\finbrief.db`. **Done once.**
3. Inspect `pipeline_runs`, `headlines`, `scores`, and `daily_aggregates`. **Done via `scripts/inspect_db.py`.**
4. Add portfolio management commands or a tiny seed script for active tickers. **Done via `scripts/portfolio.py`.**
5. Add a backfill path. **Implemented:** `scripts/backfill_finnhub.py` supports Finnhub historical windows once `FINNHUB_API_KEY` is present.
6. Add scheduler entrypoint after persistence is verified. **Implemented:** `scripts/daily_run.py` and `scripts/schedule_daily.py`.
7. Add summary/query helpers for dashboard endpoints (`/summary`, `/ticker/{symbol}`) using the persisted aggregates and responsible headlines. **Implemented:** `src/finbrief/queries.py`.
8. Build the FastAPI service around the query helpers. **Started:** `src/finbrief/app.py`.
9. Replace the lightweight dashboard page with a richer Jinja/HTMX view or decide on React. **In progress:** current FastAPI + Jinja UI now includes portfolio editor, urgency banner, ticker grid, ticker drill-down, 14-day SVG chart, headline list, and run-status panel.
