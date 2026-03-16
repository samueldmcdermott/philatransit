"""Shared constants, file I/O, and utility functions."""

import json
from pathlib import Path

BASE = Path(__file__).parent.parent
DATA = BASE / "data"
TRIPS = DATA / "trips.json"
SCHED = DATA / "scheduled.json"
DAILY_CDFS = DATA / "daily_cdfs.json"

DATA.mkdir(exist_ok=True)

SEPTA = "https://www3.septa.org/api"
SEPTA_V2 = "https://www3.septa.org/api/v2"
HEADERS = {"User-Agent": "SEPTA-Live/1.0"}


def load(path, default=None):
    default = {} if default is None else default
    try:
        return json.loads(path.read_text()) if path.exists() else default
    except Exception:
        return default


def dump(path, obj):
    path.write_text(json.dumps(obj, indent=2))


def rail_line_key(line, dest, src):
    """Map TrainView fields to a stable route key (mirrors JS railLineKey)."""
    aliases = {
        "Airport":            ["airport", "phl"],
        "Chestnut Hill East": ["chestnut hill east", "che"],
        "Chestnut Hill West": ["chestnut hill west", "chw"],
        "Cynwyd":             ["cynwyd"],
        "Fox Chase":          ["fox chase"],
        "Lansdale":           ["lansdale", "doylestown"],
        "Media":              ["media", "wawa"],
        "Manayunk":           ["manayunk", "norristown"],
        "Paoli":              ["paoli", "thorndale", "malvern"],
        "Trenton":            ["trenton"],
        "Warminster":         ["warminster"],
        "West Trenton":       ["west trenton"],
        "Wilmington":         ["wilmington", "newark"],
    }
    for field in [line, dest, src]:
        low = (field or "").lower()
        for route_id, keys in aliases.items():
            if any(k in low for k in keys):
                return route_id
    return line or "unknown"
