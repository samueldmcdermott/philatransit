"""Background trip tracker — detects vehicle trip completions across all routes.

Trip identity comes from TripManager: each transit vehicle in the shared
cache carries a `trip_id` field of the form ``{label}_{epoch_start}`` that
remains constant for the lifetime of one round trip (origin → destination →
back to origin), even across tunnel transits and SEPTA's mid-trip route
reassignments.  We treat the disappearance of a trip_id from the cache —
combined with the vehicle not being currently underground — as the
authoritative "trip completed" signal.

Rail vehicles are not managed by TripManager, so we synthesize a trip_id
from ``{vehicle_id}`` (the train number) and detect completions the same
way the legacy tracker did: trip ends when the train number disappears.
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
    Polls every 30s and records trip completions to trips.json.

    A "trip" is keyed by the trip_id that TripManager writes onto each
    transit vehicle in the shared cache.  When a trip_id stops appearing
    in the cache and the vehicle is not currently flagged as a tunnel
    ghost, that trip is recorded as completed.  This naturally handles:

      - tunnel transits (TripManager keeps the same trip_id alive while
        the vehicle is underground, since it's protected by the ghost set)
      - SEPTA's mid-trip route reassignment (TripManager keys trips on
        the fleet label, so the trip_id is stable across reassignment)
      - the legitimate end of a round trip (when TripManager retires the
        Trip on return-to-origin, the trip_id disappears from the cache)

    Rail vehicles are not managed by TripManager; their trip_id is just
    the train number, which gives the same legacy "disappearance =
    completion" semantics.

    Not a module-level singleton — instantiated by the app factory.
    """
    POLL_INTERVAL = 30

    def __init__(self, tunnel_detector=None):
        # trip_id -> {label, route, start_ms, last_seen_ms}
        self._active = {}
        self._lock     = threading.Lock()
        self._thread   = None
        self.running   = False
        self._tunnel_detector = tunnel_detector
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
            return len(self._active)

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

    def _collect_current_trips(self):
        """Return {trip_id: {label, route, start_ms}} from current caches."""
        current = {}
        now_ms = int(time.time() * 1000)

        # -- regional rail (no TripManager — synthesize trip_id from vehicle_id) --
        try:
            with rail_lock:
                trains = list(rail_cache["data"])
            for t in trains:
                vid = t.get("vehicle_id", "")
                route = t.get("route_id", "")
                if not vid:
                    continue
                tid = f"rail:{vid}"
                current[tid] = {
                    "label": vid,
                    "route": route,
                    "start_ms": now_ms,  # rail trip start is "first seen"
                }
        except Exception as e:
            print(f"  [tracker] rail error: {e}")

        # -- transit (TripManager already wrote trip_id + start_time) --
        try:
            with transit_lock:
                routes_snapshot = dict(transit_cache["routes"])
            for route_id, vs in routes_snapshot.items():
                for v in vs:
                    tid = v.get("trip_id")
                    if not tid:
                        continue
                    start_time = v.get("start_time")
                    if start_time:
                        start_ms = int(float(start_time) * 1000)
                    else:
                        start_ms = now_ms
                    current[tid] = {
                        "label": str(v.get("label", "")) or str(v.get("vehicle_id", "")),
                        "route": route_id,
                        "start_ms": start_ms,
                    }
        except Exception as e:
            print(f"  [tracker] transit error: {e}")

        return current

    def _poll(self):
        current = self._collect_current_trips()

        # Vehicles currently underground — their trips must NOT be recorded
        # as completed even though their trip_id disappears from the cache
        # (TripManager actually keeps the Trip object alive across the
        # tunnel transit, but we double-check via the ghost label set as
        # belt-and-braces).
        ghost_labels = set()
        if self._tunnel_detector is not None:
            try:
                ghost_labels = {g['vid'] for g in self._tunnel_detector.get_ghosts()}
            except Exception as e:
                print(f"  [tracker] ghost lookup error: {e}")

        now_ms = int(time.time() * 1000)

        with self._lock:
            cur_tids = set(current.keys())

            # Update / insert active entries from this poll
            for tid, info in current.items():
                ex = self._active.get(tid)
                if ex:
                    ex["last_seen_ms"] = now_ms
                    # Route can flip mid-trip (SEPTA reassignment); keep
                    # the most recent route as the canonical one.
                    ex["route"] = info["route"]
                else:
                    self._active[tid] = {
                        "label": info["label"],
                        "route": info["route"],
                        "start_ms": info["start_ms"],
                        "last_seen_ms": now_ms,
                    }

            # Detect completions: trip_ids no longer present in the cache
            # for vehicles that aren't currently flagged as ghosts.
            trips_data = load(TRIPS)
            changed = False
            for tid in list(self._active):
                entry = self._active[tid]
                if tid in cur_tids:
                    continue
                if entry["label"] and entry["label"] in ghost_labels:
                    continue  # underground — protected
                start_ms = entry["start_ms"]
                end_ms = entry["last_seen_ms"]
                if end_ms <= start_ms:
                    del self._active[tid]
                    continue
                route = entry["route"]
                day = date_str(start_ms)
                trips_data.setdefault(route, {}).setdefault(day, []).append(
                    {"start": start_ms, "end": end_ms, "dur": end_ms - start_ms}
                )
                del self._active[tid]
                changed = True

            if changed:
                dump(TRIPS, trips_data)
