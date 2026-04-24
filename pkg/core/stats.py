"""Start-time persistence.

Storage schema (shared by today.json and daily_cdfs.json):
    {route: {"YYYY-MM-DD": [sorted minutes-since-midnight, ...]}}

``record_start`` / ``record_starts`` are called by Trip-creation sites
(transit via TripManager, rail via the poller).  ``rollover`` is fired
once at startup and then nightly by ``start_midnight_scheduler``.
"""

from __future__ import annotations

import bisect
import threading
import time
from datetime import datetime, timedelta

from ..helpers import (
    TODAY, DAILY_CDFS,
    load, dump, date_str, minutes_since_midnight,
)


CUTOFF_DATE = "2026-03-17"

_file_lock = threading.Lock()


def _as_mins(val):
    """Normalize a day bucket to a sorted, deduped list of minute floats.

    Accepts either the new form (list of floats) or the legacy form
    (list of {start, end, dur} dicts).
    """
    out = set()
    for x in val:
        if isinstance(x, dict):
            ts = x.get("start") or x.get("end")
            if ts:
                out.add(round(minutes_since_midnight(ts), 2))
        else:
            out.add(round(float(x), 2))
    return sorted(out)


def record_start(route, start_ms):
    """Insert one start time (ms) into today.json, keeping each day sorted."""
    if not route:
        return
    day = date_str(start_ms)
    mins = round(minutes_since_midnight(start_ms), 2)
    with _file_lock:
        data = load(TODAY)
        bisect.insort(data.setdefault(route, {}).setdefault(day, []), mins)
        dump(TODAY, data)


def record_starts(entries):
    """Batch-insert (route, start_ms) pairs with a single file write."""
    if not entries:
        return
    with _file_lock:
        data = load(TODAY)
        for route, start_ms in entries:
            if not route:
                continue
            day = date_str(start_ms)
            mins = round(minutes_since_midnight(start_ms), 2)
            bisect.insort(data.setdefault(route, {}).setdefault(day, []), mins)
        dump(TODAY, data)


def rollover():
    """Normalize all buckets in place; move finished days into daily_cdfs."""
    today = date_str()
    with _file_lock:
        trips = load(TODAY)
        cdfs = load(DAILY_CDFS)
        dirty_t = dirty_c = False

        for route, days in list(trips.items()):
            for day in list(days.keys()):
                mins = _as_mins(days[day])
                if mins != days[day]:
                    days[day] = mins
                    dirty_t = True
                if day == today:
                    continue
                if day not in cdfs.get(route, {}):
                    cdfs.setdefault(route, {})[day] = mins
                    dirty_c = True
                days.pop(day)
                dirty_t = True
            if not days:
                del trips[route]
                dirty_t = True

        for route, days in cdfs.items():
            for day, val in list(days.items()):
                mins = _as_mins(val)
                if mins != val:
                    days[day] = mins
                    dirty_c = True

        if dirty_t:
            dump(TODAY, trips)
        if dirty_c:
            dump(DAILY_CDFS, cdfs)


def _seconds_until_midnight() -> float:
    now = datetime.now()
    nxt = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return (nxt - now).total_seconds()


def start_midnight_scheduler():
    """Launch a daemon thread that fires rollover() shortly after each midnight."""
    def _loop():
        while True:
            time.sleep(_seconds_until_midnight() + 5)
            try:
                rollover()
            except Exception as e:
                print(f"  [stats] rollover error: {e}")
    t = threading.Thread(target=_loop, daemon=True, name="RolloverScheduler")
    t.start()
    print("  [stats] midnight scheduler started")
