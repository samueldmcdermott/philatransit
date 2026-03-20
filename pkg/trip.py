from __future__ import annotations

"""Trip model and lifecycle manager.

A Trip represents one observed journey of a vehicle along a route — from
an origin terminus toward a destination terminus, and optionally back.

Key lifecycle rules:
  - Direction of travel and destination are LOCKED until the vehicle
    passes its initial destination terminus.
  - After passing the destination, direction can change and the
    destination swaps to the opposite terminus.
  - The trip is retired when the vehicle returns to its origin terminus
    (or disappears for too long).

The TripManager replaces the old direction._vstate dict and provides the
same vehicle enrichment API that the poller and frontend expect.
"""

import bisect
import threading
import time
from dataclasses import dataclass, field

from . import geo
from .shapes import routes as shape_routes, TERMINI


# ── Configuration ─────────────────────────────────────────────────────

_MIN_MOVE = 20         # meters — ignore jitter below this
_STALE_S = 600         # seconds — drop trip after this long without update
_TERMINUS_RADIUS = 200 # meters — "at terminus" threshold

# ── Trip dataclass ────────────────────────────────────────────────────

@dataclass
class Trip:
    id: str
    vehicle_id: str
    route: str
    origin: str                         # current origin terminus name
    destination: str                    # current destination terminus name
    bearing: float                      # fixed compass bearing origin→destination
    forward: bool                       # heading toward end terminus (shape direction)
    original_forward: bool              # initial direction (for retirement check)
    dist_along: float                   # meters along shape
    first_dist_along: float             # distance at first sighting
    start_time: float                   # epoch seconds
    last_update: float                  # epoch seconds

    current_stop: str | None = None
    next_stop: str | None = None
    stops_passed: int = 0
    stops_remaining: int = 0
    speed_mps: float | None = None

    total_travel: float = 0.0              # cumulative distance (never shrinks)

    passed_destination: bool = False
    retired: bool = False

    last_stop_name: str | None = None   # most recent stop observed
    last_stop_da: float | None = None   # its dist_along
    prev_stop_da: float | None = None   # dist_along of the stop before that

    meta: dict = field(default_factory=dict)

    @property
    def elapsed(self) -> float:
        """Seconds since the trip started."""
        return self.last_update - self.start_time


# ── Stop computation ──────────────────────────────────────────────────

def _stop_da(stop_dists, name):
    """Return the dist_along for a stop by name, or None."""
    for n, da in stop_dists:
        if n == name:
            return da
    return None


def _update_stop_info(trip, stop_dists):
    """Compute current_stop, next_stop, stops_passed, stops_remaining.

    stop_dists: [(name, dist_along), ...] sorted by dist_along.
    """
    if not stop_dists:
        return

    dists = [s[1] for s in stop_dists]
    names = [s[0] for s in stop_dists]
    n = len(dists)

    idx = bisect.bisect_right(dists, trip.dist_along)

    if trip.forward:
        trip.current_stop = names[idx - 1] if idx > 0 else None
        trip.next_stop = names[idx] if idx < n else None
        first_idx = bisect.bisect_right(dists, trip.first_dist_along)
        trip.stops_passed = max(0, idx - first_idx)
        trip.stops_remaining = n - idx
    else:
        trip.current_stop = names[idx] if idx < n else None
        trip.next_stop = names[idx - 1] if idx > 0 else None
        first_idx = bisect.bisect_left(dists, trip.first_dist_along)
        trip.stops_passed = max(0, first_idx - idx)
        trip.stops_remaining = idx


# ── TripManager ───────────────────────────────────────────────────────

class TripManager:
    """Maintains active Trip objects for every observed vehicle."""

    def __init__(self):
        self._trips: dict[str, Trip] = {}
        self._lock = threading.Lock()

    def is_heading_to_end(self, vid) -> bool | None:
        """Return True if vehicle is heading toward the end terminus.

        For trolleys, the end terminus is 13th St (eastbound).
        Returns None if direction is unknown.
        """
        with self._lock:
            trip = self._trips.get(vid)
            if trip:
                return trip.forward
        return None

    def get_trip(self, vid) -> Trip | None:
        with self._lock:
            return self._trips.get(vid)

    def enrich_vehicles(self, transit_routes):
        """Add computed Trip fields to vehicle dicts in-place.

        Called each poll cycle with the full transit cache snapshot.
        Maintains Trip lifecycle and writes enrichment fields back
        onto each vehicle dict for the frontend.
        """
        now = time.time()

        with self._lock:
            seen = set()

            for route_id, vehicles in transit_routes.items():
                shape = shape_routes.get(route_id)
                if not shape or shape.total_len == 0:
                    continue

                term = TERMINI.get(route_id)

                for v in vehicles:
                    vid = _make_vid(v)
                    if not vid:
                        continue

                    try:
                        lat = float(v.get('lat', 0))
                        lng = float(v.get('lng', 0))
                    except (TypeError, ValueError):
                        continue
                    if lat == 0 or lng == 0:
                        continue

                    seen.add(vid)
                    da = geo.project(shape.pts, shape.cum_dist, lat, lng)

                    trip = self._trips.get(vid)

                    if trip and trip.route == route_id:
                        self._update_trip(trip, da, shape, now)
                    else:
                        trip = self._create_trip(vid, route_id, lat, lng, da, shape, term, now)
                        self._trips[vid] = trip

                    if trip.retired:
                        del self._trips[vid]
                        continue

                    # Write enrichment fields onto vehicle dict
                    self._write_vehicle_fields(v, trip, shape, da, now)

            # Prune stale trips
            for vid in list(self._trips):
                if now - self._trips[vid].last_update > _STALE_S:
                    del self._trips[vid]

    def _create_trip(self, vid, route_id, lat, lng, da, shape, term, now):
        """Create a new Trip for a first-seen vehicle.

        Always starts forward=True (toward end terminus).  Direction is
        corrected on the first stop transition — see _update_trip.
        """
        if term:
            origin, destination = term[0], term[3]
            trip_bearing = geo.bearing(term[1], term[2], term[4], term[5])
        else:
            origin, destination = '', ''
            trip_bearing = 0.0

        trip = Trip(
            id=f"{vid}_{int(now)}",
            vehicle_id=vid,
            route=route_id,
            origin=origin,
            destination=destination,
            bearing=round(trip_bearing, 1),
            forward=True,
            original_forward=True,
            dist_along=da,
            first_dist_along=da,
            start_time=now,
            last_update=now,
        )

        # Populate stop info and seed stop history so the first stop
        # transition can trigger a direction correction.
        _update_stop_info(trip, shape.stops)
        if trip.current_stop:
            trip.last_stop_name = trip.current_stop
            trip.last_stop_da = _stop_da(shape.stops, trip.current_stop)
            trip.prev_stop_da = da

        return trip

    def _update_trip(self, trip, new_da, shape, now):
        """Advance an existing trip: direction, lifecycle, stops."""
        prev_da = trip.dist_along
        trip.total_travel += abs(new_da - prev_da)
        trip.dist_along = new_da
        trip.last_update = now

        # forward→False flip at destination terminus (permanent, one-way)
        if trip.forward and new_da >= shape.total_len - _TERMINUS_RADIUS:
            self._flip_reverse(trip)

        # Retirement: back at origin after having flipped
        if trip.passed_destination and new_da <= _TERMINUS_RADIUS:
            trip.retired = True

        # Speed from cumulative travel (survives direction changes)
        elapsed = now - trip.start_time
        trip.speed_mps = round(trip.total_travel / elapsed, 2) if elapsed >= 30 and trip.total_travel >= 50 else None

        # Stop info
        _update_stop_info(trip, shape.stops)

        # Track stop history; correct direction if stops contradict it.
        if trip.current_stop and trip.current_stop != trip.last_stop_name:
            cur_da = _stop_da(shape.stops, trip.current_stop)
            if cur_da is not None:
                trip.prev_stop_da = trip.last_stop_da
                trip.last_stop_name = trip.current_stop
                trip.last_stop_da = cur_da

                flipped = False
                if trip.prev_stop_da is not None:
                    if trip.forward and cur_da < trip.prev_stop_da:
                        self._flip_reverse(trip)
                        flipped = True
                    elif not trip.forward and cur_da > trip.prev_stop_da:
                        self._flip_forward(trip)
                        flipped = True

                if flipped:
                    # Resync: the flip changes how current_stop is computed,
                    # so update history to prevent an immediate re-flip.
                    _update_stop_info(trip, shape.stops)
                    trip.prev_stop_da = None
                    if trip.current_stop:
                        trip.last_stop_name = trip.current_stop
                        trip.last_stop_da = _stop_da(shape.stops, trip.current_stop)

    def _flip_reverse(self, trip):
        """Flip from forward=True to forward=False."""
        trip.forward = False
        trip.original_forward = False
        trip.passed_destination = True
        trip.origin, trip.destination = trip.destination, trip.origin
        trip.bearing = (trip.bearing + 180) % 360

    def _flip_forward(self, trip):
        """Correct a wrong reverse back to forward."""
        trip.forward = True
        trip.original_forward = True
        trip.passed_destination = False
        trip.origin, trip.destination = trip.destination, trip.origin
        trip.bearing = (trip.bearing + 180) % 360

    def _write_vehicle_fields(self, v, trip, shape, da, now):
        """Write backward-compatible + new Trip fields onto a vehicle dict."""
        heading = geo.shape_heading(
            shape.pts, shape.cum_dist, shape.total_len, da, trip.forward
        )

        # Backward-compatible fields (existing frontend contract)
        v['computed_heading'] = round(heading, 1)
        v['computed_direction'] = 'forward' if trip.forward else 'reverse'
        if trip.destination:
            v['toward_terminus'] = trip.destination
        v['dist_along'] = round(da, 1)
        v['first_seen_ts'] = round(trip.start_time, 3)
        v['first_dist_along'] = round(trip.first_dist_along, 1)
        v['speed_mps'] = trip.speed_mps
        v['shape_total_len'] = round(shape.total_len, 1)

        # New Trip fields
        v['trip_id'] = trip.id
        v['trip_bearing'] = trip.bearing
        v['origin_terminus'] = trip.origin
        v['destination_terminus'] = trip.destination
        v['current_stop'] = trip.current_stop
        v['next_stop'] = trip.next_stop
        v['trip_start_time'] = round(trip.start_time, 3)
        v['trip_elapsed'] = round(trip.elapsed, 1)
        v['stops_passed'] = trip.stops_passed
        v['stops_remaining'] = trip.stops_remaining


# ── Vehicle ID helper ─────────────────────────────────────────────────

def _make_vid(v):
    """Build a stable vehicle identifier from a SEPTA vehicle dict."""
    trip = v.get('trip')
    if trip and str(trip) not in ('0', 'None', ''):
        return str(trip)
    vid = v.get('VehicleID')
    if vid and str(vid) not in ('0', 'None', '') and 'schedBased' not in str(vid):
        return str(vid)
    label = v.get('label', '')
    if label and str(label) not in ('None', '0'):
        return f"{label}_{v.get('lat')}_{v.get('lng')}"
    return None


# ── Module-level singleton ────────────────────────────────────────────

trip_manager = TripManager()
