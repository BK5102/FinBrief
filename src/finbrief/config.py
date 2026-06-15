"""Runtime configuration from environment variables.

Loaded once at import time. Import this module wherever a config value is
needed — load_dotenv() here is idempotent and safe to call from multiple
entry points.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

FINBRIEF_DB: Path = Path(os.getenv("FINBRIEF_DB", "data/finbrief.db"))
SECRET_KEY: str = os.getenv("SECRET_KEY", "change-me-in-production")
FINNHUB_API_KEY: str | None = os.getenv("FINNHUB_API_KEY") or None
REFRESH_TIME: str = os.getenv("REFRESH_TIME", "07:00")

# Urgency-spike thresholds (passed to db.find_negative_spikes)
SPIKE_SIGMA: float = float(os.getenv("SPIKE_SIGMA", "1.5"))
MIN_NEG_HEADLINES: int = int(os.getenv("MIN_NEG_HEADLINES", "2"))

# Per-source fetch retry settings
FETCH_RETRIES: int = int(os.getenv("FETCH_RETRIES", "3"))
FETCH_RETRY_DELAY: float = float(os.getenv("FETCH_RETRY_DELAY", "2.0"))
