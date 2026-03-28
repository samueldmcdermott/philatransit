"""Shared file I/O utilities and path constants."""

import json
from pathlib import Path

BASE = Path(__file__).parent.parent
DATA = BASE / "data"
TRIPS = DATA / "trips.json"
SCHED = DATA / "scheduled.json"
DAILY_CDFS = DATA / "daily_cdfs.json"

DATA.mkdir(exist_ok=True)


def load(path, default=None):
    default = {} if default is None else default
    try:
        return json.loads(path.read_text()) if path.exists() else default
    except Exception:
        return default


def dump(path, obj):
    path.write_text(json.dumps(obj, indent=2))
