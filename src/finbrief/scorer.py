"""FinBERT sentiment scorer.

Wraps ProsusAI/finbert. Lazy-loads the model on first call so the module is cheap to import.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Sequence

log = logging.getLogger(__name__)

MODEL_ID = "ProsusAI/finbert"
LABELS = ("positive", "neutral", "negative")


@lru_cache(maxsize=1)
def _load_pipeline():
    from transformers import AutoTokenizer, AutoModelForSequenceClassification, pipeline
    import torch

    log.info("loading %s ...", MODEL_ID)
    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
    model = AutoModelForSequenceClassification.from_pretrained(MODEL_ID)
    device = 0 if torch.cuda.is_available() else -1
    return pipeline(
        "text-classification",
        model=model,
        tokenizer=tokenizer,
        device=device,
        top_k=None,
        truncation=True,
        max_length=256,
    )


def score_texts(texts: Sequence[str]) -> list[dict]:
    """Score a list of texts. Returns list of {label, confidence, scores: {label: prob}} dicts."""
    if not texts:
        return []

    pipe = _load_pipeline()
    raw = pipe(list(texts))

    out: list[dict] = []
    for entry in raw:
        scores = {item["label"].lower(): float(item["score"]) for item in entry}
        label, conf = max(scores.items(), key=lambda kv: kv[1])
        out.append({"label": label, "confidence": conf, "scores": scores})
    return out
