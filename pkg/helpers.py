"""Shared file I/O utilities, path constants, and small time helpers."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).parent.parent
DATA = BASE / "data"
TODAY = DATA / "today.json"
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
    """Write JSON atomically.

    Serialize to a sibling temp file, fsync, then os.replace() onto the
    target.  os.replace is atomic within a filesystem, so a crash or
    container restart mid-write leaves the previous file intact rather
    than a truncated one.  The temp file is a sibling (not /tmp) so it
    lands on the same filesystem as the target, including under the
    Docker ./data volume mount.
    """
    tmp = path.with_name(path.name + ".tmp")
    try:
        with open(tmp, "w") as f:
            json.dump(obj, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def date_str(ts_ms: int | None = None) -> str:
    """Return YYYY-MM-DD for a millisecond timestamp, or today if None."""
    if ts_ms is None:
        return datetime.now().strftime(DATE_FORMAT)
    return datetime.fromtimestamp(ts_ms / 1000).strftime(DATE_FORMAT)


def minutes_since_midnight(ts_ms: int) -> float:
    """Return minutes-since-midnight (local time) for a ms timestamp."""
    dt = datetime.fromtimestamp(ts_ms / 1000)
    return dt.hour * 60 + dt.minute + dt.second / 60
