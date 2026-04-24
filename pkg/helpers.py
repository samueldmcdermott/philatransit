"""Shared file I/O utilities, path constants, and small time helpers."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).parent.parent
DATA = BASE / "data"
TODAY = DATA / "today.json"
SCHED = DATA / "scheduled.json"
DAILY_CDFS = DATA / "daily_cdfs.json"

DATA.mkdir(exist_ok=True)

DATE_FORMAT = "%Y-%m-%d"


def load(path, default=None):
    default = {} if default is None else default
    try:
        return json.loads(path.read_text()) if path.exists() else default
    except Exception:
        return default


def dump(path, obj):
    path.write_text(json.dumps(obj, indent=2))


def date_str(ts_ms: int | None = None) -> str:
    """Return YYYY-MM-DD for a millisecond timestamp, or today if None."""
    if ts_ms is None:
        return datetime.now().strftime(DATE_FORMAT)
    return datetime.fromtimestamp(ts_ms / 1000).strftime(DATE_FORMAT)


def minutes_since_midnight(ts_ms: int) -> float:
    """Return minutes-since-midnight (local time) for a ms timestamp."""
    dt = datetime.fromtimestamp(ts_ms / 1000)
    return dt.hour * 60 + dt.minute + dt.second / 60
