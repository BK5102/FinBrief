"""Benchmark FinBrief's FinBERT wrapper against a labeled PhraseBank-style CSV.

Expected input CSV columns:
    sentence,label

Labels must be positive, neutral, or negative. This script intentionally accepts a
local CSV so the validation step can run without adding a dataset dependency.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

from finbrief.scorer import score_texts


LABELS = ("positive", "neutral", "negative")


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark FinBERT on a labeled sentence CSV")
    parser.add_argument("--input", required=True, type=Path, help="CSV with sentence,label columns")
    parser.add_argument("--out", type=Path, help="Optional JSON report path")
    parser.add_argument("--limit", type=int, help="Optional row limit for quick smoke tests")
    args = parser.parse_args()

    rows = _load_rows(args.input, limit=args.limit)
    predictions = score_texts([row["sentence"] for row in rows])

    total = len(rows)
    correct = 0
    confusion: dict[str, Counter[str]] = defaultdict(Counter)
    examples: list[dict] = []

    for row, pred in zip(rows, predictions):
        actual = row["label"]
        predicted = pred["label"]
        if actual == predicted:
            correct += 1
        confusion[actual][predicted] += 1
        examples.append(
            {
                "sentence": row["sentence"],
                "actual": actual,
                "predicted": predicted,
                "confidence": round(float(pred["confidence"]), 4),
            }
        )

    report = {
        "input": str(args.input),
        "total": total,
        "correct": correct,
        "accuracy": round(correct / total, 4) if total else 0.0,
        "confusion": {label: {pred: confusion[label][pred] for pred in LABELS} for label in LABELS},
        "examples": examples,
    }

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


def _load_rows(path: Path, limit: int | None = None) -> list[dict]:
    rows: list[dict] = []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        required = {"sentence", "label"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise SystemExit(f"{path} missing required columns: {', '.join(sorted(missing))}")

        for row in reader:
            sentence = (row.get("sentence") or "").strip()
            label = (row.get("label") or "").strip().lower()
            if not sentence:
                continue
            if label not in LABELS:
                raise SystemExit(f"unsupported label {label!r}; expected one of {', '.join(LABELS)}")
            rows.append({"sentence": sentence, "label": label})
            if limit and len(rows) >= limit:
                break
    return rows


if __name__ == "__main__":
    raise SystemExit(main())
