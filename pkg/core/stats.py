"""Start-time persistence.

Storage schema:
    daily_cdfs.json — {route: {"YYYY-MM-DD": [sorted minutes-since-midnight]}}
    today.json      — {route: {"YYYY-MM-DD": [sorted entries]}}, where each
                      entry is {"start": minutes-since-midnight (local time),
                                "elapsed_seconds": int|null,
                                "stops_passed": int|null,
                                "tunnel_seconds": int|null  # T routes only
                               }

``record_start`` is called when a Trip is created; ``record_finish`` is
called when the same Trip retires (filling in the elapsed/stops/tunnel
fields by matching on start time).  Rail trips, which aren't Trip-managed,
only get a start record.

``rollover`` runs once at startup and then nightly; it drains finished
days into daily_cdfs.json, flattening rich entries to minute lists.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta

from ..helpers import (
    TODAY, DAILY_CDFS,
    load, dump, date_str, minutes_since_midnight,
)


CUTOFF_DATE = "2026-03-17"

# Completed trips that traversed less than this fraction of their effective
# route are treated as anomalies: they're left in today.json for same-day
# debugging but excluded from CDF stats and from the daily_cdfs harvest.
MIN_VALID_STOP_FRACTION = 0.95

_file_lock = threading.Lock()


def _entry_minute(entry):
    """Return the start-minute for an entry, regardless of schema version."""
    if isinstance(entry, dict):
        if 'start' in entry and isinstance(entry['start'], (int, float)):
            val = entry['start']
            # Heuristic: legacy ms timestamps are huge; new schema is <1440 min.
            if val > 10000:
                return round(minutes_since_midnight(val), 2)
            return round(float(val), 2)
        if 'end' in entry:
            return round(minutes_since_midnight(entry['end']), 2)
        return None
    return round(float(entry), 2)


def _is_valid_for_stats(entry):
    """True if the entry should count toward CDFs.

    In-flight entries (no elapsed_seconds yet) and entries without a
    fraction (e.g. rail trips, which aren't Trip-managed) are kept.
    Only completed entries with an explicit low fraction are excluded.
    """
    if not isinstance(entry, dict):
        return True
    if entry.get('elapsed_seconds') is None:
        return True
    f = entry.get('fraction_stops_passed')
    if f is None:
        return True
    return f >= MIN_VALID_STOP_FRACTION


def _as_mins(val, *, valid_only=False):
    """Flatten any bucket form to a sorted, deduped list of minute floats.

    If valid_only is True, completed-but-anomalous trips are dropped.
    """
    out = set()
    for x in val:
        if valid_only and not _is_valid_for_stats(x):
            continue
        m = _entry_minute(x)
        if m is not None:
            out.add(m)
    return sorted(out)


def _insort_entry(entries, entry):
    """Insert a rich entry into a list sorted by start-minute."""
    start = entry['start']
    lo, hi = 0, len(entries)
    while lo < hi:
        mid = (lo + hi) // 2
        m = _entry_minute(entries[mid])
        if m is not None and m < start:
            lo = mid + 1
        else:
            hi = mid
    entries.insert(lo, entry)


def record_start(route, start_ms):
    """Append a new trip-start entry to today.json (sorted by minute)."""
    if not route:
        return
    day = date_str(start_ms)
    entry = {
        'start': round(minutes_since_midnight(start_ms), 2),
        'elapsed_seconds': None,
        'stops_passed': None,
    }
    with _file_lock:
        data = load(TODAY)
        bucket = data.setdefault(route, {}).setdefault(day, [])
        _insort_entry(bucket, entry)
        dump(TODAY, data)


def record_starts(entries):
    """Batch-insert (route, start_ms) pairs with one file write."""
    if not entries:
        return
    with _file_lock:
        data = load(TODAY)
        for route, start_ms in entries:
            if not route:
                continue
            day = date_str(start_ms)
            entry = {
                'start': round(minutes_since_midnight(start_ms), 2),
                'elapsed_seconds': None,
                'stops_passed': None,
            }
            bucket = data.setdefault(route, {}).setdefault(day, [])
            _insort_entry(bucket, entry)
        dump(TODAY, data)


def record_travel_start(route, nominal_start_ms, travel_start_ms, idle_seconds):
    """Promote a trip's nominal start to its travel-start in today.json.

    Finds the entry inserted by record_start (matched on nominal-minute),
    rewrites its ``start`` field to the travel-start minute, records the
    nominal start under ``nominal_start``, and stores ``idle_seconds``.
    The bucket is re-sorted so CDF readers stay in order.
    """
    if not route or nominal_start_ms == travel_start_ms:
        return
    day = date_str(nominal_start_ms)
    if date_str(travel_start_ms) != day:
        # Idle period crossed midnight — don't bother shifting buckets.
        return
    nominal_min = round(minutes_since_midnight(nominal_start_ms), 2)
    travel_min = round(minutes_since_midnight(travel_start_ms), 2)
    with _file_lock:
        data = load(TODAY)
        bucket = data.get(route, {}).get(day)
        if not bucket:
            return
        for i, e in enumerate(bucket):
            m = _entry_minute(e)
            if m is None or abs(m - nominal_min) > 0.02:
                continue
            if not isinstance(e, dict):
                e = {'start': m}
            e['start'] = travel_min
            e['nominal_start'] = nominal_min
            e['idle_seconds'] = round(idle_seconds, 1)
            bucket.pop(i)
            _insort_entry(bucket, e)
            dump(TODAY, data)
            return


def record_finish(route, start_ms, elapsed_seconds=None,
                  stops_passed=None, fraction_stops_passed=None,
                  was_on_detour=False, tunnel_seconds=None):
    """Update the matching today.json entry with retirement stats.

    Matches the entry by route + day + start-minute (within 0.02 min).
    If no match exists (rare — e.g. the day rolled over mid-trip), the
    finish stats are silently dropped.
    """
    if not route:
        return
    day = date_str(start_ms)
    target = round(minutes_since_midnight(start_ms), 2)
    with _file_lock:
        data = load(TODAY)
        bucket = data.get(route, {}).get(day)
        if not bucket:
            return
        for i, e in enumerate(bucket):
            m = _entry_minute(e)
            if m is None or abs(m - target) > 0.02:
                continue
            if not isinstance(e, dict):
                e = {'start': m}
            e['elapsed_seconds'] = elapsed_seconds
            e['stops_passed'] = stops_passed
            e['fraction_stops_passed'] = fraction_stops_passed
            if was_on_detour:
                e['was_on_detour'] = True
            if tunnel_seconds is not None:
                e['tunnel_seconds'] = tunnel_seconds
            bucket[i] = e
            dump(TODAY, data)
            return


def rollover():
    """Drain finished days from today.json into daily_cdfs.json.

    today.json keeps rich entries (including anomalous ones); daily_cdfs.json
    stores flat minute lists with anomalous trips filtered out.
    """
    today = date_str()
    with _file_lock:
        trips = load(TODAY)
        cdfs = load(DAILY_CDFS)
        dirty_t = dirty_c = False

        for route, days in list(trips.items()):
            for day in list(days.keys()):
                if day == today:
                    continue
                if day not in cdfs.get(route, {}):
                    cdfs.setdefault(route, {})[day] = _as_mins(
                        days[day], valid_only=True)
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


def today_minutes(data=None, *, valid_only=True):
    """Return today.json reshaped as {route: {day: [minutes]}} for CDFs.

    Anomalous completed trips are filtered out by default; pass
    valid_only=False to include them (e.g. for raw export).
    """
    if data is None:
        data = load(TODAY)
    return {r: {d: _as_mins(v, valid_only=valid_only) for d, v in days.items()}
            for r, days in data.items()}


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
