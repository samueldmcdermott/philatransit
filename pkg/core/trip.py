"""Trip model and lifecycle manager.

A Trip represents one observed journey of a vehicle along a route — from
an origin terminus toward a destination terminus, and optionally back.

The Trip is the primary data object in the system.  The provider API
gives us only vehicle_id, route_id, and raw GPS position; everything
else is computed internally:

  - trip_id: assigned by TripManager
  - origin / destination / bearing: from RouteInfo (looked up by route_id)
  - toward_destination: computed from stop-transition tracking
  - progress fields: current_stop, next_stop, previous_stops, etc.
  - position fields beyond lat/lng: heading, speed, dist_along

Key lifecycle rules:
  - toward_destination starts True (heading from origin toward destination).
  - Direction is corrected by tracking stop transitions along the shape.
  - After passing the destination terminus, toward_destination flips to
    False and bearing flips 180°.
  - The trip is retired when the vehicle returns near its origin terminus
    (or disappears for too long).
  - For loop routes (no termini), toward_destination stays True and the
    trip retires on stale timeout only.
"""

from __future__ import annotations

import bisect
import threading
import time
from dataclasses import dataclass, field

from .. import geo
from .stats import record_finish, record_start


# ── Configuration ─────────────────────────────────────────────────────

_MIN_MOVE = 20         # meters — ignore jitter below this
_STALE_S = 600         # seconds — drop trip after this long without update
_TERMINUS_RADIUS = 200 # meters — "at terminus" threshold
_DA_HISTORY_LEN = 4    # polls of dist_along history for movement-based direction
_DA_FLIP_THRESH = 100  # meters — minimum net movement to trigger a direction flip


# ── Trip dataclass ────────────────────────────────────────────────────

@dataclass
class Trip:
    id: str                              # internally assigned: "{vehicle_id}_{epoch}"
    vehicle_id: str                      # from NormalizedVehicle
    route: str                           # route_id
    origin: str                          # from RouteInfo ("" for loop routes)
    destination: str                     # from RouteInfo ("" for loop routes)
    current_location: tuple[float, float]  # (lat, lng), updated each poll
    previous_stops: list[str]            # last 2 stops passed, most recent first
    toward_destination: bool             # True = origin→destination; flips after reaching destination
    bearing: float                       # from RouteInfo.origin_to_dest_bearing; flips 180° with toward_destination

    # Position tracking
    dist_along: float = 0.0
    first_dist_along: float = 0.0
    start_time: float = 0.0
    last_update: float = 0.0
    total_travel: float = 0.0
    speed_mps: float | None = None

    # Stop progress
    current_stop: str | None = None
    next_stop: str | None = None
    stops_passed: int = 0
    stops_remaining: int = 0
    stops_total: int = 0
    total_stops_crossed: int = 0         # cumulative stop-transitions over trip lifetime

    # Classification
    vehicle_type: str = ''               # from RouteInfo mode (e.g. "TROLLEY", "BUS")
    label: str = ''                      # fleet number (stable across trip ID changes)
    on_detour: bool = False              # set by DetourDetector

    # Tunnel timing (T routes only — accumulated across emergences)
    tunnel_seconds: float = 0.0

    # Lifecycle
    passed_destination: bool = False
    retired: bool = False

    # Internal direction-correction state (not exposed in API)
    _last_stop_name: str | None = field(default=None, repr=False)
    _last_stop_da: float | None = field(default=None, repr=False)
    _prev_stop_da: float | None = field(default=None, repr=False)
    _da_history: list = field(default_factory=list, repr=False)  # recent dist_along samples

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

    stops_passed / stops_remaining are absolute counts along the route in the
    direction of travel — not relative to where the trip was first observed —
    so they always sum to stops_total (e.g. 14/27 stops passed) and remain
    consistent across mid-route pickups, tunnel transits, and direction flips.

    stop_dists: [(name, dist_along), ...] sorted by dist_along.
    """
    if not stop_dists:
        return

    dists = [s[1] for s in stop_dists]
    names = [s[0] for s in stop_dists]
    n = len(dists)
    trip.stops_total = n

    idx = bisect.bisect_right(dists, trip.dist_along)

    if trip.toward_destination:
        trip.current_stop = names[idx - 1] if idx > 0 else None
        trip.next_stop = names[idx] if idx < n else None
        trip.stops_passed = idx
        trip.stops_remaining = n - idx
    else:
        trip.current_stop = names[idx] if idx < n else None
        trip.next_stop = names[idx - 1] if idx > 0 else None
        trip.stops_passed = n - idx
        trip.stops_remaining = idx


def _update_previous_stops(trip):
    """Update previous_stops list and bump cumulative stop-crossings on change."""
    if trip.current_stop and (not trip.previous_stops or
                              trip.previous_stops[0] != trip.current_stop):
        trip.previous_stops = [trip.current_stop] + trip.previous_stops[:1]
        trip.total_stops_crossed += 1


# ── TripManager ───────────────────────────────────────────────────────

class TripManager:
    """Maintains active Trip objects for every observed vehicle.

    Not a module-level singleton — instantiated by the app factory.
    """

    def __init__(self, shape_registry=None, route_config=None):
        """
        shape_registry: RouteShapeRegistry (from core.shapes)
        route_config: dict of route_id -> RouteInfo (from provider)
        """
        self._trips: dict[str, Trip] = {}
        self._lock = threading.Lock()
        self._shapes = shape_registry
        self._route_config = route_config or {}
        self._detour_detector = None
        self._route_avg_speed: dict[str, float] = {}
        self._ghost_labels: set[str] = set()  # labels currently underground

    def set_shapes(self, shape_registry):
        self._shapes = shape_registry

    def set_route_config(self, route_config):
        self._route_config = route_config

    def set_detour_detector(self, detector):
        self._detour_detector = detector

    def set_ghost_labels(self, labels: set[str]):
        """Update the set of labels currently underground.

        Trips for these vehicles are protected from stale-pruning.
        """
        with self._lock:
            self._ghost_labels = labels

    def get_direction(self, vid) -> bool | None:
        """Return True if vehicle is heading toward destination.

        Used as a callback by tunnel detectors.
        Returns None if direction is unknown.
        """
        with self._lock:
            trip = self._trips.get(vid)
            if trip:
                return trip.toward_destination
        return None

    def get_trip(self, vid) -> Trip | None:
        with self._lock:
            return self._trips.get(vid)

    def enrich_vehicles(self, transit_routes: dict[str, list[dict]]):
        """Add computed Trip fields to vehicle dicts in-place.

        Called each poll cycle with {route_id: [NormalizedVehicle, ...]}.
        Maintains Trip lifecycle and writes enrichment fields back
        onto each vehicle dict for the API response.
        """
        if not self._shapes:
            return

        now = time.time()

        with self._lock:
            seen = set()

            for route_id, vehicles in transit_routes.items():
                shape = self._shapes.get(route_id)
                if not shape or shape.total_len == 0:
                    continue

                for v in vehicles:
                    vid = v.get('vehicle_id')
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
                        self._update_trip(trip, da, lat, lng, shape, now)
                    else:
                        trip = self._create_trip(
                            vid, route_id, lat, lng, da, shape, now,
                            label=str(v.get('label', '')))
                        self._trips[vid] = trip

                    if trip.retired:
                        self._record_retirement(trip)
                        del self._trips[vid]
                        continue

                    # Detour detection (provider-specific)
                    if self._detour_detector:
                        trip.on_detour = self._detour_detector.check_detour(
                            vid, route_id, lat, lng)
                        if trip.on_detour and self._detour_detector.detect_turnaround(
                                vid, route_id, lat, lng, trip.toward_destination):
                            self._flip_to_return(trip)

                    # Write Trip fields onto the vehicle dict
                    self._write_vehicle_fields(v, trip, shape, da, now)

            # Compute route average speeds
            route_speeds: dict[str, list[float]] = {}
            for trip in self._trips.values():
                if trip.speed_mps is not None:
                    route_speeds.setdefault(trip.route, []).append(trip.speed_mps)
            for rid, speeds in route_speeds.items():
                self._route_avg_speed[rid] = round(sum(speeds) / len(speeds), 2)

            # Prune stale trips (but not vehicles currently underground)
            for vid in list(self._trips):
                trip = self._trips[vid]
                if trip.label and trip.label in self._ghost_labels:
                    continue
                if now - trip.last_update > _STALE_S:
                    self._record_retirement(trip)
                    del self._trips[vid]

    def _create_trip(self, vid, route_id, lat, lng, da, shape, now,
                     label=''):
        """Create a new Trip for a first-seen vehicle."""
        # Look up route info for origin/destination/bearing
        route_info = self._route_config.get(route_id, {})
        origin = route_info.get('origin', '')
        destination = route_info.get('destination', '')
        route_bearing = route_info.get('origin_to_dest_bearing', 0.0)

        # If no bearing from route config, compute from shape termini
        if not route_bearing and shape.terminus:
            term = shape.terminus
            if term[1] and term[4]:  # have lat/lng for both termini
                route_bearing = geo.bearing(term[1], term[2], term[4], term[5])

        # If no origin/destination from route config, use shape terminus names
        if not origin and shape.terminus:
            origin = shape.terminus[0] if isinstance(shape.terminus[0], str) else ''
        if not destination and shape.terminus:
            destination = shape.terminus[3] if len(shape.terminus) > 3 and isinstance(shape.terminus[3], str) else ''

        trip = Trip(
            id=f"{vid}_{int(now)}",
            vehicle_id=vid,
            route=route_id,
            origin=origin,
            destination=destination,
            current_location=(lat, lng),
            previous_stops=[],
            toward_destination=True,
            bearing=round(route_bearing, 1),
            dist_along=da,
            first_dist_along=da,
            start_time=now,
            last_update=now,
            vehicle_type=route_info.get('mode', ''),
            label=label,
        )

        # Populate stop info and seed stop history
        _update_stop_info(trip, shape.stops)
        _update_previous_stops(trip)
        if trip.current_stop:
            trip._last_stop_name = trip.current_stop
            trip._last_stop_da = _stop_da(shape.stops, trip.current_stop)
            trip._prev_stop_da = da

        record_start(trip.route, int(trip.start_time * 1000))
        return trip

    def _update_trip(self, trip, new_da, lat, lng, shape, now):
        """Advance an existing trip: direction, lifecycle, stops."""
        prev_da = trip.dist_along
        trip.total_travel += abs(new_da - prev_da)
        trip.dist_along = new_da
        trip.current_location = (lat, lng)
        trip.last_update = now

        # Speed from cumulative travel
        elapsed = now - trip.start_time
        trip.speed_mps = round(trip.total_travel / elapsed, 2) if elapsed >= 30 and trip.total_travel >= 50 else None

        # On-detour vehicles are off their normal shape — skip shape-based
        # direction/terminus/stop logic (projection gives meaningless results).
        if trip.on_detour:
            return

        # toward_destination flips to False at destination terminus
        if trip.toward_destination and new_da >= shape.total_len - _TERMINUS_RADIUS:
            self._flip_to_return(trip)

        # Retirement: back at origin after having flipped
        if trip.passed_destination and new_da <= _TERMINUS_RADIUS:
            trip.retired = True

        # Stop info
        _update_stop_info(trip, shape.stops)
        _update_previous_stops(trip)

        # Track stop history; correct direction if stops contradict it.
        stop_flipped = False
        if trip.current_stop and trip.current_stop != trip._last_stop_name:
            cur_da = _stop_da(shape.stops, trip.current_stop)
            if cur_da is not None:
                trip._prev_stop_da = trip._last_stop_da
                trip._last_stop_name = trip.current_stop
                trip._last_stop_da = cur_da

                if trip._prev_stop_da is not None:
                    if trip.toward_destination and cur_da < trip._prev_stop_da:
                        self._flip_to_return(trip)
                        stop_flipped = True
                    elif not trip.toward_destination and cur_da > trip._prev_stop_da:
                        self._flip_to_forward(trip)
                        stop_flipped = True

                if stop_flipped:
                    _update_stop_info(trip, shape.stops)
                    trip._prev_stop_da = None
                    if trip.current_stop:
                        trip._last_stop_name = trip.current_stop
                        trip._last_stop_da = _stop_da(shape.stops, trip.current_stop)

        # Movement-based direction correction: if the last N samples
        # consistently move in one direction by enough distance, flip.
        # This catches direction errors between stops or on routes with
        # sparse stops, without being sensitive to GPS jitter.
        if not stop_flipped:
            trip._da_history.append(new_da)
            if len(trip._da_history) > _DA_HISTORY_LEN:
                trip._da_history = trip._da_history[-_DA_HISTORY_LEN:]
            if len(trip._da_history) >= _DA_HISTORY_LEN:
                deltas = [trip._da_history[i] - trip._da_history[i - 1]
                          for i in range(1, len(trip._da_history))]
                all_fwd = all(d > 0 for d in deltas)
                all_rev = all(d < 0 for d in deltas)
                net = trip._da_history[-1] - trip._da_history[0]
                if all_fwd and net > _DA_FLIP_THRESH and not trip.toward_destination:
                    self._flip_to_forward(trip)
                    _update_stop_info(trip, shape.stops)
                    trip._da_history.clear()
                elif all_rev and net < -_DA_FLIP_THRESH and trip.toward_destination:
                    self._flip_to_return(trip)
                    _update_stop_info(trip, shape.stops)
                    trip._da_history.clear()
        else:
            trip._da_history.clear()

    def _flip_to_return(self, trip):
        """Flip from toward_destination=True to False (reached destination)."""
        trip.toward_destination = False
        trip.passed_destination = True
        trip.origin, trip.destination = trip.destination, trip.origin
        trip.bearing = (trip.bearing + 180) % 360

    def apply_tunnel_emergence(self, emerged: dict[str, dict]):
        """Flip direction on trips whose vehicles just exited the tunnel.

        emerged: {label: {route, direction, entry_time, exit_time, ...}}
        from the tunnel detector.  Eastbound emergence flips the trip to
        toward_destination=False (the vehicle has reached 13th St and is
        heading back).  In both directions we accumulate tunnel_seconds
        for the trip from the entry/exit timestamps.
        """
        if not emerged or not self._shapes:
            return
        with self._lock:
            for trip in self._trips.values():
                if not trip.label or trip.label not in emerged:
                    continue
                info = emerged[trip.label]
                if trip.route != info['route']:
                    continue
                # Accumulate tunnel time for this trip
                entry = info.get('entry_time')
                exit_ = info.get('exit_time')
                if entry and exit_ and exit_ > entry:
                    trip.tunnel_seconds = round(trip.tunnel_seconds + (exit_ - entry), 1)
                # Flip to return if still heading toward destination
                if trip.toward_destination:
                    self._flip_to_return(trip)
                shape = self._shapes.get(trip.route)
                if shape:
                    _update_stop_info(trip, shape.stops)

    def _record_retirement(self, trip):
        """Persist final trip stats to today.json on retirement."""
        is_tunnel_route = trip.route in {'T1', 'T2', 'T3', 'T4', 'T5'}
        record_finish(
            trip.route,
            int(trip.start_time * 1000),
            elapsed_seconds=round(trip.last_update - trip.start_time, 1),
            stops_passed=trip.total_stops_crossed,
            tunnel_seconds=round(trip.tunnel_seconds, 1) if is_tunnel_route else None,
        )

    def _flip_to_forward(self, trip):
        """Correct a wrong return flip back to forward."""
        trip.toward_destination = True
        trip.passed_destination = False
        trip.origin, trip.destination = trip.destination, trip.origin
        trip.bearing = (trip.bearing + 180) % 360

    def _write_vehicle_fields(self, v, trip, shape, da, now):
        """Write Trip fields onto a vehicle dict for the API response."""
        heading = geo.shape_heading(
            shape.pts, shape.cum_dist, shape.total_len, da, trip.toward_destination
        )

        delay = v.get('meta', {}).get('delay', 0)

        v['trip_id'] = trip.id
        v['origin'] = trip.origin
        v['destination'] = trip.destination
        v['toward_destination'] = trip.toward_destination
        v['bearing'] = trip.bearing
        v['start_time'] = round(trip.start_time, 3)
        v['vehicle_type'] = trip.vehicle_type
        v['on_detour'] = trip.on_detour

        v['position'] = {
            'lat': trip.current_location[0],
            'lng': trip.current_location[1],
            'heading': round(heading, 1),
            'speed_mps': trip.speed_mps or self._route_avg_speed.get(trip.route),
            'dist_along': round(da, 1),
            'shape_total_len': round(shape.total_len, 1),
        }

        is_tunnel_route = trip.route in {'T1', 'T2', 'T3', 'T4', 'T5'}
        progress = {
            'current_stop': trip.current_stop,
            'next_stop': trip.next_stop,
            'previous_stops': list(trip.previous_stops),
            'stops_passed': trip.stops_passed,
            'stops_remaining': trip.stops_remaining,
            'stops_total': trip.stops_total,
            'delay_minutes': delay,
            'elapsed_seconds': round(trip.elapsed, 1),
        }
        if is_tunnel_route:
            progress['tunnel_seconds'] = round(trip.tunnel_seconds, 1)
        v['progress'] = progress
