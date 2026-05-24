# Phase 1 — Data & Model Spike: Notes

## Current status (paused)

Pipeline and multi-source fetcher complete; **validation step paused** while builder ramps on financial-sentiment labeling conventions. Phase 2 will not start until validation closes via one of the paths below.

**Recommended next move:** choose option B first. A public Financial PhraseBank benchmark gives an objective accuracy number without requiring the builder to become a financial-news labeler before the product can move forward.

## Exit criteria status

- [x] Pipeline runs end-to-end for 5+ tickers in under 2 minutes (10 tickers, ~110s steady-state).
- [ ] Sentiment labels validated. **Options:**
  - **A.** Defer hand-labeling; rely on FinBERT's published validation, revisit with real Phase-2 data.
  - **B.** Run pipeline against the Financial PhraseBank validation set and compute accuracy vs gold labels (objective, no manual work).
  - **C.** Hand-label 30 headlines after the builder reviews FinBERT/PhraseBank labeling conventions (see "Topics to learn" below).

## Topics to learn before option C

1. Financial sentiment ≠ general sentiment — label from an investor's perspective ("does this signal good/bad for company value?").
2. Positive / neutral / negative buckets across categories: earnings, analyst actions, corporate actions, operations, regulatory, macro.
3. Specific terms whose financial meaning isn't obvious: beat/miss, guidance, YoY, EPS, buyback, dilution, insider buying/selling.
4. Counterintuitive cases: layoffs often = positive (cost-cutting), reaction headlines ("stock falls 3%") usually = neutral.
5. References:
   - Financial PhraseBank paper (Malo et al., 2014) — https://arxiv.org/abs/1307.5336 — labeling conventions in section 3.
   - FinBERT paper (Araci, 2019) — https://arxiv.org/abs/1908.10063.
   - Investopedia on: earnings call, analyst upgrade, guidance, buyback, dilution.

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

## Next execution checklist

1. Implement a small benchmark script for Financial PhraseBank. **Done:** `scripts/benchmark_phrasebank.py` accepts a local `sentence,label` CSV.
2. Run benchmark scoring through the existing `scorer.py` wrapper and record accuracy/confusion counts.
3. If benchmark quality is acceptable, mark Phase 1 closed with validation caveats.
4. Start Phase 2: SQLite schema, ingest persistence, daily aggregate calculation, and an initial seeded portfolio. **Started:** schema, persistence, aggregate recomputation, and spike-query helper are implemented in `src/finbrief/db.py`.
