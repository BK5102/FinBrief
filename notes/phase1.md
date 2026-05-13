# Phase 1 — Data & Model Spike: Notes

## Exit criteria status

- [x] Pipeline runs end-to-end for 5+ tickers in under 2 minutes (10 tickers, ~110s steady-state).
- [ ] Sentiment labels manually sanity-checked on 30 headlines. CSV ready at `notes/sanity_check_headlines.csv` — pending human labels.

## News sources evaluated

| Source | Key required | Symbol-keyed | Today's coverage (10 mega-caps) | Notes |
| ------ | -----------: | -----------: | ------------------------------: | ----- |
| Yahoo Finance RSS (`feedparser`) | no | weak | Highly variable (0–130 across runs hours apart) | Bleeds cross-promotional content (NVDA feed returned Toast / Beyond Meat). Intermittent. |
| `yfinance` `Ticker.news` | no | weak | 63 headlines, every ticker covered | Modern Yahoo endpoint. New schema (`content.{title, summary, pubDate, ...}`). No `relatedTickers` field for filtering. |
| Finnhub `/company-news` | yes (free tier) | strong | Not yet exercised | Symbol-specific at source — no relevance leakage. 60 calls/min free. |

**Decision:** primary = `yfinance`; backup = Yahoo RSS; Finnhub on when `FINNHUB_API_KEY` is set, for redundancy and ticker-pure baseline.

## Known failure modes

1. **Relevance leakage in Yahoo sources.** Per-ticker feeds include unrelated cross-promotional stories. No `relatedTickers` field on the new yfinance schema to filter on. Mitigation: Finnhub (symbol-pure) + downstream confidence weighting in aggregation.
2. **Coverage variance in Yahoo RSS.** Same ticker can return 0 or dozens within hours. Don't trust RSS alone.
3. **FinBERT 512-token truncation.** Long press-release summaries get cut. Acceptable — we score title + short summary.
4. **First-call latency.** Cold model load + HF cache resolution can take 60–90s. Steady-state inference is ~0.5s/text on CPU at batch size = headlines/run.
5. **`model.safetensors` not published for `ProsusAI/finbert`** — HF tries it, 404s, falls back to `pytorch_model.bin`. Cosmetic warning, no functional impact.
6. **Unicode in titles.** Some Yahoo titles contain typographic quotes that render as `?` in cp1252 terminals on Windows. JSON output is fine (UTF-8 with `ensure_ascii=False`).

## Risks addressed

- ✅ News API rate limits / coverage gaps → confirmed real on Yahoo RSS, mitigated by multi-source design.
- ✅ FinBERT CPU latency → acceptable at ~0.5s/text; GPU/quantization not needed for daily runs.
- ✅ FinBERT label drift on non-headline text → titles+short summaries score sensibly on spot-check; deeper validation pending the 30-headline labeling pass.
