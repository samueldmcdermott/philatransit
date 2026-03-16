"""Background trip tracker — detects vehicle trip completions across all routes."""

import threading
import time
from datetime import datetime

from .helpers import TRIPS, DAILY_CDFS, load, dump, rail_line_key
from .cache import transit_lock, transit_cache, trainview_lock, trainview_cache


# ── Startup cleanup: discard data before 2026-03-15 15:00 ────────────────────

_CUTOFF_DATE = "2026-03-15"
_CUTOFF_MS = int(datetime(2026, 3, 15, 15, 0, 0).timestamp() * 1000)


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
            dt = datetime.fromtimestamp(ts / 1000)
            m = dt.hour * 60 + dt.minute + dt.second / 60
            mins.append(round(m, 2))
        mins.sort()
        if mins:
            cdfs.setdefault(route, {})[date_str] = mins

    dump(DAILY_CDFS, cdfs)


class TripTracker:
    """
    Polls SEPTA every 30 s, tracking every vehicle across all routes.
    When a vehicle disappears after being seen in >= MIN_DWELL polls it
    is counted as a completed trip and appended to trips.json.
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
        self._last_summary_date = datetime.now().strftime("%Y-%m-%d")
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
            # Sleep in 0.5 s ticks so stop() takes effect quickly
            for _ in range(self.POLL_INTERVAL * 2):
                if not self.running:
                    break
                time.sleep(0.5)

    def _check_day_rollover(self):
        """At midnight, summarize the previous day's data into daily_cdfs.json."""
        today = datetime.now().strftime("%Y-%m-%d")
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

        # -- regional rail --
        try:
            with trainview_lock:
                trains = list(trainview_cache["data"])
            for t in trains:
                vid   = str(t.get("trainno", ""))
                route = rail_line_key(
                    t.get("line", ""), t.get("dest", ""), t.get("SOURCE", "")
                )
                if vid:
                    vehicles[vid] = route
        except Exception as e:
            print(f"  [tracker] rail error: {e}")

        # -- bus / trolley / subway (from shared transit cache) --
        try:
            with transit_lock:
                routes_snapshot = dict(transit_cache["routes"])
            for route_id, vs in routes_snapshot.items():
                for v in vs:
                    if v.get("late") == 998:
                        continue
                    label = v.get("label", "")
                    if label in ("None", None, ""):
                        continue
                    vehicle_id = str(v.get("VehicleID") or "")
                    if "schedBased" in vehicle_id:
                        continue
                    vid = str(v.get("trip") or vehicle_id or "")
                    if vid and vid not in ("None", ""):
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
                    day      = datetime.fromtimestamp(start_ms / 1000).strftime("%Y-%m-%d")
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


tracker = TripTracker()
