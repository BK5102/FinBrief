# PortfolioPulse

**A one-glance morning brief for your stock portfolio.**

PortfolioPulse is a personal-use dashboard that ingests a user-defined stock portfolio, pulls daily financial news per ticker, scores each headline with [FinBERT](https://huggingface.co/ProsusAI/finbert), and surfaces a single urgency signal that tells you where to look first. It replaces 30+ minutes of manual scanning across financial news sites with a one-glance morning brief.

> Example signal: *"3 of your 8 holdings have negative sentiment spikes today — here's why."*

---

## Target User

A single retail investor (the builder) holding **5 to 15 equities**, checking the dashboard once per day.

---

## Goals

### Product Goals

- **Portfolio input** — add, edit, and remove tickers via a simple UI; holdings persist locally across sessions.
- **Daily news collection** — for each held ticker, pull the day's relevant English-language financial headlines from at least **two free news sources** for redundancy.
- **Per-ticker sentiment scoring** — every collected headline scored by FinBERT into `{positive, neutral, negative}` with a confidence weight; ticker-level daily score aggregates these.
- **Urgency signal** — front-page banner naming how many holdings show a negative sentiment spike today and linking to the headlines responsible.
- **Drill-down view** — click any ticker to see today's headlines, their FinBERT labels, source, and timestamp.

### Success Criteria

- Dashboard correctly displays sentiment for at least **10 user-supplied tickers across 7 consecutive days** without manual intervention.
- Urgency banner has actionable precision: a manual spot-check of **20 flagged "negative spike" days** shows that at least **70%** correspond to genuinely negative news (not false positives from neutral-but-volatile language).

---

## Non-Goals

- **Financial advice** — no buy/sell/hold recommendations. Sentiment is presented as raw signal, not guidance.
- **Price prediction** — no forecasting models. Sentiment is not used to predict future returns.
- **Real-time streaming** — headlines update daily, not by minute or second. Day traders are not the target.

---

## Build Plan

| Phase | Theme                       | Output                                                                                                                | Exit Criteria                                                                                                                       |
| ----- | --------------------------- | --------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| **1** | Data & Model Spike          | CLI script that, given a ticker list, returns a JSON blob of today's headlines and FinBERT scores.                    | Pipeline runs end-to-end for 5 tickers in under 2 minutes; sentiment labels manually sanity-checked on 30 sample headlines.         |
| **2** | Persistence & Aggregation   | SQLite database storing tickers, headlines, scores, and daily aggregates. Scheduled job runs the pipeline once daily. | 7 consecutive days of historical data successfully captured and queryable; urgency-spike logic defined and validated.               |
| **3** | Dashboard UI                | Web dashboard (FastAPI + lightweight frontend) with portfolio input, urgency banner, ticker grid, and headline drill-down. | All four views render correctly with live data; portfolio edits persist; page loads in under 1 second from cached data.             |
| **4** | Hardening & Polish          | Dockerized deployment, error handling for API/network failures, basic observability (logs + run history), README.     | App runs unattended for 7 days with no manual recovery; documented setup gets a fresh machine to first run in under 15 minutes.     |

---

## Phase 1 — Data & Model Spike (Week 1)

**Goal:** prove the riskiest pieces work before building anything else.

### Scope

- Select news source(s): evaluate **Yahoo Finance RSS**, **Finnhub free tier**, and **NewsAPI free tier**. Choose 2 for redundancy.
- Build a fetcher that takes a ticker and returns the day's headlines (`title`, `summary`, `url`, `source`, `published_at`).
- Load FinBERT (`ProsusAI/finbert`) via Hugging Face `transformers`; build a scorer that takes a list of headlines and returns labels + confidence scores.
- Compose into a CLI: `python pipeline.py --tickers AAPL,MSFT,NVDA` → prints JSON.
- Sanity-check: hand-label 30 random headlines and compare to FinBERT output; document failure modes.

### Risks Addressed

- News API rate limits or coverage gaps for less-popular tickers.
- FinBERT inference latency on CPU; decide here whether GPU/quantization is needed.
- FinBERT label drift on non-headline text (e.g., press release boilerplate).

---

## Phase 2 — Persistence & Aggregation (Week 2)

**Goal:** turn the one-shot script into a system that accumulates history.

### Scope

- Define schema: `tickers`, `headlines`, `scores`, `daily_aggregates` (one row per ticker per day).
- Migrate the Phase 1 pipeline to write into SQLite instead of stdout.
- **Aggregation logic:** ticker-day score = weighted average of headline scores (weight = FinBERT confidence).
- **Urgency-spike definition:** a ticker has a "negative spike" today if its daily score drops more than **1.5 standard deviations** below its 14-day rolling mean **AND** at least **2 headlines** are labeled negative with confidence **≥ 0.7**. Tune thresholds against captured history.
- Scheduler: cron (or APScheduler) running the pipeline at **07:00 local**.
- Backfill: ingest 7 days of history to seed the rolling baseline.

---

## Phase 3 — Dashboard UI (Week 3)

**Goal:** make the data usable in 10 seconds per morning.

### Scope

- **Backend:** FastAPI service exposing `/portfolio`, `/summary`, `/ticker/{symbol}`.
- **Frontend:** Jinja templates + HTMX (or a minimal React app — decide by Tuesday). Tailwind for styling.
- **Views:**
  1. Portfolio editor
  2. Home with urgency banner + ticker grid
  3. Ticker detail with today's headlines and 14-day score chart
- **Color/label coding:** green / neutral / red badges per ticker; banner styling escalates with spike count.
- **"Why?" link** on each negative-spike ticker jumps to the headlines responsible for the drop.

---

## Phase 4 — Hardening & Polish (Week 4)

**Goal:** take it from "works on my machine in dev mode" to "runs unattended for a week."

### Scope

- **Dockerize:** one `Dockerfile`, one `docker-compose` for app + scheduler + volume-mounted SQLite.
- **Error handling:** retries with backoff on news API failures; graceful degradation if one source is down; alerting (log file flag) on full pipeline failure.
- **Observability:** structured logs per pipeline run (tickers attempted, articles fetched, articles scored, duration).
- **Configuration:** `.env` file for API keys, refresh time, urgency thresholds.
- **README:** setup, running, troubleshooting, and a short "how the urgency signal works" explainer.
- **Manual 7-day run:** deploy, walk away, return after a week, confirm nothing crashed.

---

## Tech Stack

| Layer       | Choice                                    |
| ----------- | ----------------------------------------- |
| Language    | Python 3.11+                              |
| ML Model    | FinBERT (`ProsusAI/finbert`) via Hugging Face `transformers` |
| News Sources | Yahoo Finance RSS, Finnhub, NewsAPI (pick 2) |
| Storage     | SQLite                                    |
| Scheduling  | cron / APScheduler                        |
| Backend     | FastAPI                                   |
| Frontend    | Jinja + HTMX + Tailwind (or minimal React) |
| Deployment  | Docker + docker-compose                   |

---

## Status

Project initialized. Phase 1 work begins next.
