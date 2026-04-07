"""Background trip tracker — detects vehicle trip completions across all routes.

Provider-agnostic: reads from the shared poller caches which contain
NormalizedVehicle dicts regardless of provider.
"""

import threading
import time
from datetime import datetime

from ..helpers import (
    TRIPS, DAILY_CDFS,
    load, dump, date_str, minutes_since_midnight,
)
from ..poller import transit_lock, transit_cache, rail_lock, rail_cache


# ── Startup cleanup: discard data before cutoff ──────────────────────────

_CUTOFF_DATE = "2026-03-17"
_CUTOFF_MS = int(datetime(2026, 3, 17, 0, 0, 0).timestamp() * 1000)


def _cleanup_old_data():
    """Remove all trip data before the cutoff date/time."""
    trips = load(TRIPS)
    cleaned = {}
    for route, days in trips.items():
        for day, trip_list in days.items():
            if day < _CUTOFF_DATE:
                continue
            if day == _CUTOFF_DATE:
                trip_list = [t for t in trip_list
                             if (t.get("start") or t.get("end") or 0) >= _CUTOFF_MS]
            if trip_list:
                cleaned.setdefault(route, {})[day] = trip_list
    dump(TRIPS, cleaned)
    print("  [tracker] startup cleanup complete")


def _summarize_day(date_str):
    """Summarize a day's trips into daily_cdfs.json (minutes-since-midnight)."""
    trips = load(TRIPS)
    cdfs = load(DAILY_CDFS)

    for route, days in trips.items():
        day_trips = days.get(date_str, [])
        if not day_trips:
            continue
        mins = []
        for t in day_trips:
            ts = t.get("start") or t.get("end")
            if not ts:
                continue
            mins.append(round(minutes_since_midnight(ts), 2))
        mins.sort()
        if mins:
            cdfs.setdefault(route, {})[date_str] = mins

    dump(DAILY_CDFS, cdfs)


class TripTracker:
    """
    Polls every 30s, tracking every vehicle across all routes.
    When a vehicle disappears after being seen in >= MIN_DWELL polls it
    is counted as a completed trip and appended to trips.json.

    Not a module-level singleton — instantiated by the app factory.
    """
    POLL_INTERVAL = 30
    MIN_DWELL     = 2

    def __init__(self):
        self._registry = {}   # vid -> {"route": str, "seen": int, "first_ms": int}
        self._lock     = threading.Lock()
        self._thread   = None
        self.running   = False
        self._last_summary_date = None

    def start(self):
        if self.running:
            return
        _cleanup_old_data()
        self.running = True
        self._last_summary_date = date_str()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="TripTracker")
        self._thread.start()
        print("  [tracker] started")

    def stop(self):
        self.running = False
        print("  [tracker] stopped")

    @property
    def registry_size(self):
        with self._lock:
            return len(self._registry)

    def _loop(self):
        while self.running:
            try:
                self._poll()
                self._check_day_rollover()
            except Exception as e:
                print(f"  [tracker] poll error: {e}")
            for _ in range(self.POLL_INTERVAL * 2):
                if not self.running:
                    break
                time.sleep(0.5)

    def _check_day_rollover(self):
        """At midnight, summarize the previous day's data."""
        today = date_str()
        if self._last_summary_date and today != self._last_summary_date:
            prev = self._last_summary_date
            print(f"  [tracker] summarizing day: {prev}")
            try:
                _summarize_day(prev)
            except Exception as e:
                print(f"  [tracker] summary error: {e}")
            self._last_summary_date = today

    def _poll(self):
        vehicles = {}   # vid -> route_key

        # -- regional rail (from normalized rail cache) --
        try:
            with rail_lock:
                trains = list(rail_cache["data"])
            for t in trains:
                vid = t.get("vehicle_id", "")
                route = t.get("route_id", "")
                if vid:
                    vehicles[vid] = route
        except Exception as e:
            print(f"  [tracker] rail error: {e}")

        # -- bus / trolley / subway (from normalized transit cache) --
        try:
            with transit_lock:
                routes_snapshot = dict(transit_cache["routes"])
            for route_id, vs in routes_snapshot.items():
                for v in vs:
                    vid = v.get("vehicle_id", "")
                    if vid:
                        vehicles[vid] = route_id
        except Exception as e:
            print(f"  [tracker] transit error: {e}")

        now_ms = int(time.time() * 1000)

        with self._lock:
            cur_ids = set(vehicles.keys())
            trips   = load(TRIPS)
            changed = False

            # detect completions
            for vid, entry in self._registry.items():
                if vid not in cur_ids and entry["seen"] >= self.MIN_DWELL:
                    route    = entry["route"]
                    start_ms = entry["first_ms"]
                    day      = date_str(start_ms)
                    trips.setdefault(route, {}).setdefault(day, []).append(
                        {"start": start_ms, "end": now_ms, "dur": now_ms - start_ms}
                    )
                    changed = True

            if changed:
                dump(TRIPS, trips)

            # update registry
            new_reg = {}
            for vid, route in vehicles.items():
                ex = self._registry.get(vid)
                new_reg[vid] = {
                    "route":    route,
                    "seen":     (ex["seen"] + 1) if ex else 1,
                    "first_ms": ex["first_ms"] if ex else now_ms,
                }
            self._registry = new_reg
