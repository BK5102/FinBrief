"""Sample N random headlines from a pipeline JSON output and emit a CSV for human labeling.

Usage:
    python scripts/make_sanity_csv.py today_10_v2.json notes/sanity_check_headlines.csv --n 30
"""

from __future__ import annotations

import argparse
import csv
import json
import random
from pathlib import Path


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("input_json", type=Path)
    p.add_argument("output_csv", type=Path)
    p.add_argument("--n", type=int, default=30)
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args()

    data = json.loads(args.input_json.read_text(encoding="utf-8"))
    all_headlines = [h for hs in data["headlines_by_ticker"].values() for h in hs]

    random.seed(args.seed)
    sample = random.sample(all_headlines, min(args.n, len(all_headlines)))

    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.output_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["ticker", "title", "source", "finbert_label", "finbert_confidence", "human_label", "notes"]
        )
        for h in sample:
            writer.writerow(
                [
                    h["ticker"],
                    h["title"],
                    h["source"],
                    h["sentiment"]["label"],
                    f"{h['sentiment']['confidence']:.3f}",
                    "",
                    "",
                ]
            )

    print(f"wrote {len(sample)} rows to {args.output_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
