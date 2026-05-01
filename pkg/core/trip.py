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
from .stats import record_finish, record_start, record_travel_start


# ── Configuration ─────────────────────────────────────────────────────

_STALE_S = 600         # seconds — drop trip after this long without update
_TERMINUS_RADIUS = 200 # meters — "at terminus" threshold
_DA_HISTORY_LEN = 4    # polls of dist_along history for movement-based direction
_DA_FLIP_THRESH = 100  # meters — minimum net movement to trigger a direction flip
_TRAVEL_MIN_MOVE = 20  # meters — distance from origin that counts as "started moving"
_TRAVEL_IDLE_POLLS = 4 # min polls stationary near origin before idle override applies
_DORMANT_AFTER_S = 1800  # seconds — auto-dormant after this long without movement
_DORMANT_MOVE_M = 20     # meters — movement threshold to wake a dormant trip

# A trip first observed near its origin is "born dormant": SEPTA marks
# trolleys as active before they actually leave the yard, so we hide
# them until we have evidence of real motion.  Wake when EITHER three
# consecutive polls each show >_BORN_DORMANT_STEP_M of movement OR the
# vehicle is >_BORN_DORMANT_TOTAL_M (straight-line) from its first
# sighting.  Both thresholds are deliberately tight to filter GPS jitter.
_BORN_DORMANT_STEP_M = 3
_BORN_DORMANT_TOTAL_M = 10
_BORN_DORMANT_STEPS = 3

# Trolley tunnel routes — the only routes that accumulate tunnel_seconds
# and surface that field on the API.
_TUNNEL_ROUTES = frozenset({'T1', 'T2', 'T3', 'T4', 'T5'})

# Route-misassignment correction (SEPTA frequently reports a trolley under
# the wrong T-route; the wrong assignment shows up clearly as a large
# off-shape projection distance).  Override only when:
#   * the reported route's projection is far from the vehicle GPS, AND
#   * a sibling route's projection is close, AND
#   * the gap between them is large enough that shared-track ambiguity
#     can't explain it.
# All thresholds in meters.
_MISASSIGN_REPORTED_FAR_M = 200
_MISASSIGN_BEST_CLOSE_M = 50
_MISASSIGN_GAP_M = 200

# Window after a tunnel exit during which a route change for the same
# vehicle is interpreted as a cross-route reassignment (e.g. T2 emerged at
# 13th and is now running back as T3).  Within this window, the new trip
# starts mid-route — we still track it, but skip record_start so it doesn't
# pollute the start-time CDF for the new route.
_TUNNEL_REASSIGN_WINDOW_S = 60


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
    start_time: float = 0.0              # effective start (= travel_start once override fires)
    nominal_start: float = 0.0           # when SEPTA first marked the trip live
    idle_seconds: float = 0.0            # travel_start - nominal_start (0 if no idle period)
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
    was_on_detour: bool = False          # ever on detour during trip lifetime
    stops_at_detour_start: int = 0       # stops_passed snapshot at first detour entry

    # Tunnel timing (T routes only — accumulated across emergences)
    tunnel_seconds: float = 0.0
    last_tunnel_exit: float = 0.0  # epoch seconds; used to detect cross-route reassignment

    # Lifecycle
    passed_destination: bool = False
    retired: bool = False
    # Dormant trips are kept alive on the backend (so the next time the
    # vehicle reappears we can decide between continuation and a fresh
    # trip) but excluded from API output entirely — the client never sees
    # them.  Causes: 30+ min stationary, or tunnel-FIFO violation.
    dormant: bool = False

    # Internal direction-correction state (not exposed in API)
    _last_stop_name: str | None = field(default=None, repr=False)
    _last_stop_da: float | None = field(default=None, repr=False)
    _prev_stop_da: float | None = field(default=None, repr=False)
    _da_history: list = field(default_factory=list, repr=False)  # recent dist_along samples
    _stationary_polls: int = field(default=1, repr=False)        # consecutive polls within _TRAVEL_MIN_MOVE
    _travel_detected: bool = field(default=False, repr=False)
    _last_move_ts: float = field(default=0.0, repr=False)        # last poll where dist_along moved >_DORMANT_MOVE_M
    # Born-dormant state: trip created near origin terminus and never
    # observed moving.  Wakes only after _BORN_DORMANT_STEPS consecutive
    # polls each >_BORN_DORMANT_STEP_M, or >_BORN_DORMANT_TOTAL_M total
    # straight-line displacement from the first sighting.
    _born_dormant: bool = field(default=False, repr=False)
    _first_lat: float = field(default=0.0, repr=False)
    _first_lng: float = field(default=0.0, repr=False)
    _consecutive_moves: int = field(default=0, repr=False)

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


def _near_origin(shape, lat, lng) -> bool:
    """True if (lat, lng) is within _TERMINUS_RADIUS straight-line meters
    of the start-terminus coord.  Used in preference to a `dist_along`
    check because some GTFS shapes don't actually reach the terminus
    point (T4 is ~1.4 km off), making projection-based distance unreliable
    near the origin."""
    t = shape.terminus
    if not t or not t[1] or not t[2]:
        return False
    return geo.distance(lat, lng, t[1], t[2]) <= _TERMINUS_RADIUS


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

    def mark_dormant_by_labels(self, labels):
        """Mark trips dormant by fleet label (used when the tunnel detector
        observes a FIFO-queue violation — the vehicle whose place was
        skipped is no longer reliably in the active queue, so we hide it
        from the API but keep its Trip alive)."""
        if not labels:
            return
        labels = set(labels)
        with self._lock:
            for trip in self._trips.values():
                if trip.label in labels:
                    trip.dormant = True

    def retire_dormant_trips(self):
        """Force-retire every dormant trip.  Called at the daily rollover
        so that long-lived dormant trips don't accumulate forever."""
        with self._lock:
            for vid in list(self._trips):
                trip = self._trips[vid]
                if not trip.dormant:
                    continue
                self._record_retirement(trip)
                del self._trips[vid]

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

    def _correct_route_misassignment(self, transit_routes):
        """Move a vehicle to a sibling route when SEPTA's assignment is
        clearly wrong by GPS — e.g. a trolley reported as T3 whose GPS is
        squarely on T4's track.

        Only operates within the trolley-tunnel route family (T1–T5),
        which all share track segments and where SEPTA's mode-of-service
        confusion shows up most often.  Other routes (buses, G1) are
        left untouched: their shapes are sparser and a "wrong" projection
        is more likely to be GPS noise than a real misassignment.

        Override only fires when the gap between the reported route and
        the best-fit sibling is decisive (see _MISASSIGN_* constants).

        Modifies transit_routes in place: removes the vehicle from the
        reported route and appends it under the corrected route.
        """
        if not self._shapes:
            return

        candidates = [r for r in _TUNNEL_ROUTES if r in transit_routes
                      and self._shapes.get(r) is not None]
        if len(candidates) < 2:
            return

        # Build a worklist before mutating, so reassignments don't
        # affect the iteration order or get re-evaluated.
        moves = []  # (vid, src_route, dst_route, vehicle_dict)
        for src_route in candidates:
            for v in transit_routes[src_route]:
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

                # Score the vehicle against every candidate route.
                best_route = None
                best_off = float('inf')
                src_off = None
                for cand in candidates:
                    shape = self._shapes.get(cand)
                    _, perp = geo.project_with_perp(
                        shape.pts, shape.cum_dist, lat, lng)
                    if cand == src_route:
                        src_off = perp
                    if perp < best_off:
                        best_off = perp
                        best_route = cand
                if best_route is None or src_off is None:
                    continue
                if best_route == src_route:
                    continue
                if src_off < _MISASSIGN_REPORTED_FAR_M:
                    continue
                if best_off > _MISASSIGN_BEST_CLOSE_M:
                    continue
                if (src_off - best_off) < _MISASSIGN_GAP_M:
                    continue
                moves.append((vid, src_route, best_route, v))

        for vid, src_route, dst_route, v in moves:
            transit_routes[src_route] = [
                x for x in transit_routes[src_route] if x is not v]
            v['route_id'] = dst_route
            transit_routes.setdefault(dst_route, []).append(v)

    def _resolve_cross_route_duplicates(self, transit_routes):
        """Drop a vehicle from every route except the one its GPS best fits.

        SEPTA occasionally lists the same fleet number under more than one
        route in a single poll (e.g. trolley 9000 appearing under both T3
        and T4 during reassignment).  Without intervention, the per-route
        loop in enrich_vehicles would treat each appearance as a separate
        trip on a different route, thrashing the underlying Trip every
        poll.  Resolve the conflict here by picking, for each duplicated
        vid, the route whose shape the vehicle is physically closest to
        and removing the vehicle from the other routes' lists.

        Modifies transit_routes in place.  Vehicles unique to one route
        are left untouched.
        """
        if not self._shapes:
            return

        # vid -> [(route_id, vehicle_dict), ...]
        appearances: dict[str, list[tuple[str, dict]]] = {}
        for route_id, vehicles in transit_routes.items():
            for v in vehicles:
                vid = v.get('vehicle_id')
                if vid:
                    appearances.setdefault(vid, []).append((route_id, v))

        for vid, occs in appearances.items():
            if len(occs) <= 1:
                continue
            # Score each occurrence by straight-line distance from the
            # reported GPS to its projected point on the route's shape.
            # Lower = better fit; the route the vehicle is actually on.
            best_route = None
            best_off = float('inf')
            scored = []
            for route_id, v in occs:
                shape = self._shapes.get(route_id)
                if not shape or shape.total_len == 0:
                    scored.append((route_id, v, float('inf')))
                    continue
                try:
                    lat = float(v.get('lat', 0))
                    lng = float(v.get('lng', 0))
                except (TypeError, ValueError):
                    scored.append((route_id, v, float('inf')))
                    continue
                _, off = geo.project_with_perp(shape.pts, shape.cum_dist, lat, lng)
                scored.append((route_id, v, off))
                if off < best_off:
                    best_off = off
                    best_route = route_id
            # Remove this vid from every route except best_route.
            for route_id, v, _ in scored:
                if route_id == best_route:
                    continue
                bucket = transit_routes.get(route_id)
                if not bucket:
                    continue
                # Identity comparison — there can be multiple dicts for
                # the same vid in one route only if the provider failed
                # to dedupe; here we drop the specific occurrence we saw.
                transit_routes[route_id] = [x for x in bucket if x is not v]

    def enrich_vehicles(self, transit_routes: dict[str, list[dict]]):
        """Add computed Trip fields to vehicle dicts in-place.

        Called each poll cycle with {route_id: [NormalizedVehicle, ...]}.
        Maintains Trip lifecycle and writes enrichment fields back
        onto each vehicle dict for the API response.
        """
        if not self._shapes:
            return

        # Resolve same-vehicle-on-multiple-routes by GPS-vs-shape distance
        # before the per-route loop runs.  Otherwise the loop would create
        # and immediately retire a Trip on each conflicting route, every
        # poll, thrashing stats.
        self._resolve_cross_route_duplicates(transit_routes)

        # Correct SEPTA route misassignments within the trolley family
        # (T1–T5): a vehicle reported as T3 whose GPS is squarely on T4
        # gets moved to T4.  Conservative thresholds keep shared-track
        # ambiguity from triggering false moves.
        self._correct_route_misassignment(transit_routes)

        now = time.time()

        with self._lock:
            for route_id, vehicles in transit_routes.items():
                shape = self._shapes.get(route_id)
                if not shape or shape.total_len == 0:
                    continue

                visible = []  # vehicles to expose to the API this cycle
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

                    da = geo.project(shape.pts, shape.cum_dist, lat, lng)

                    trip = self._trips.get(vid)

                    if trip and trip.route == route_id:
                        self._update_trip(trip, da, lat, lng, shape, now)
                    else:
                        # vid collision (same vehicle, different route, or a
                        # leftover trip overwritten without retirement).
                        # Retire the old trip first so its stats are
                        # recorded — _record_retirement applies the ghost
                        # filter at read time, so anything frac<0.1 just
                        # won't surface on the CDF.
                        skip_record_start = False
                        if trip is not None:
                            # If the previous trip recently emerged from the
                            # tunnel, this is a cross-route reassignment.
                            # The new trip starts mid-route, so suppress
                            # record_start to keep the new route's CDF clean.
                            if (trip.route != route_id
                                    and trip.last_tunnel_exit > 0
                                    and now - trip.last_tunnel_exit
                                        < _TUNNEL_REASSIGN_WINDOW_S):
                                skip_record_start = True
                            self._record_retirement(trip)
                        trip = self._create_trip(
                            vid, route_id, lat, lng, da, shape, now,
                            label=str(v.get('label', '')),
                            skip_record_start=skip_record_start)
                        self._trips[vid] = trip

                    if trip.retired:
                        self._record_retirement(trip)
                        del self._trips[vid]
                        continue

                    # Dormant trips:
                    #   * Born-dormant (SEPTA-marked-live, sitting at origin):
                    #     surface them so the map can render a dashed/pulsing
                    #     marker at the origin terminus.  They carry
                    #     dormant=True so the client can opt them out of
                    #     downstream-stop arrival predictions.
                    #   * Other dormant (30-min stationary, tunnel FIFO):
                    #     hidden from API output entirely.
                    if trip.dormant and not trip._born_dormant:
                        continue

                    # Detour detection (provider-specific) — skip for
                    # born-dormant trips since they haven't moved yet.
                    if self._detour_detector and not trip._born_dormant:
                        trip.on_detour = self._detour_detector.check_detour(
                            vid, route_id, lat, lng)
                        # Snapshot stops on first transition into detour:
                        # the trolley skips the underground stops, so only
                        # the stops it had passed up to this point count
                        # toward its effective denominator.
                        if trip.on_detour and not trip.was_on_detour:
                            trip.was_on_detour = True
                            trip.stops_at_detour_start = trip.stops_passed
                        if trip.on_detour and self._detour_detector.detect_turnaround(
                                vid, route_id, lat, lng, trip.toward_destination):
                            self._flip(trip, to_return=True)

                    # Write Trip fields onto the vehicle dict
                    self._write_vehicle_fields(v, trip, shape, da, now)
                    visible.append(v)
                transit_routes[route_id] = visible

            # Compute route average speeds
            route_speeds: dict[str, list[float]] = {}
            for trip in self._trips.values():
                if trip.speed_mps is not None:
                    route_speeds.setdefault(trip.route, []).append(trip.speed_mps)
            for rid, speeds in route_speeds.items():
                self._route_avg_speed[rid] = round(sum(speeds) / len(speeds), 2)

            # Prune stale trips.  Skip vehicles currently underground (the
            # tunnel ghost layer is the source of truth for them) and
            # dormant trips (kept alive until end-of-day or until the
            # vehicle reappears at the origin — see _update_trip).
            for vid in list(self._trips):
                trip = self._trips[vid]
                if trip.dormant:
                    continue
                if trip.label and trip.label in self._ghost_labels:
                    continue
                if now - trip.last_update > _STALE_S:
                    self._record_retirement(trip)
                    del self._trips[vid]

    def _create_trip(self, vid, route_id, lat, lng, da, shape, now,
                     label='', skip_record_start=False):
        """Create a new Trip for a first-seen vehicle.

        skip_record_start: don't append a start entry to today.json — used
        when the vehicle just emerged from the tunnel onto a different
        route, so the trip already started mid-route and shouldn't show up
        in the new route's start-time CDF.
        """
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
            nominal_start=now,
            last_update=now,
            vehicle_type=route_info.get('mode', ''),
            label=label,
        )
        trip._last_move_ts = now
        trip._first_lat = lat
        trip._first_lng = lng
        # Born-dormant: a trip first observed near its origin terminus
        # is hidden from the API until we have evidence of real motion
        # (SEPTA flags trolleys active before they leave the yard).
        if _near_origin(shape, lat, lng):
            trip.dormant = True
            trip._born_dormant = True

        # Populate stop info and seed stop history
        _update_stop_info(trip, shape.stops)
        _update_previous_stops(trip)
        if trip.current_stop:
            trip._last_stop_name = trip.current_stop
            trip._last_stop_da = _stop_da(shape.stops, trip.current_stop)
            trip._prev_stop_da = da

        if not skip_record_start:
            record_start(trip.route, int(trip.nominal_start * 1000))
        return trip

    def _update_trip(self, trip, new_da, lat, lng, shape, now):
        """Advance an existing trip: direction, lifecycle, stops.

        Dormancy:
          * Born-dormant (created near origin, never seen moving): wake
            only after _BORN_DORMANT_STEPS consecutive polls each moving
            >_BORN_DORMANT_STEP_M, or >_BORN_DORMANT_TOTAL_M total
            straight-line displacement from the first sighting.
          * 30-min stationary (was live, then idle): wake on any
            >_DORMANT_MOVE_M move along the shape.  If the wake position
            is near the origin terminus, the dormant trip is retired and
            a fresh one will be created on the next poll.
        """
        prev_lat, prev_lng = trip.current_location
        step_m = geo.distance(prev_lat, prev_lng, lat, lng)
        prev_da = trip.dist_along
        trip.total_travel += abs(new_da - prev_da)
        trip.dist_along = new_da
        trip.current_location = (lat, lng)
        trip.last_update = now

        if trip._born_dormant:
            # Track consecutive small movements + cumulative displacement
            # using raw lat/lng (not shape projections) so GPS jitter at
            # the terminus doesn't accidentally wake the trip.
            if step_m > _BORN_DORMANT_STEP_M:
                trip._consecutive_moves += 1
            else:
                trip._consecutive_moves = 0
            displacement = geo.distance(
                trip._first_lat, trip._first_lng, lat, lng)
            if (trip._consecutive_moves >= _BORN_DORMANT_STEPS
                    or displacement > _BORN_DORMANT_TOTAL_M):
                trip.dormant = False
                trip._born_dormant = False
                trip._last_move_ts = now
                # The wake moment IS the travel-start: SEPTA marked the
                # trip live while the vehicle sat at the terminus, and
                # the wake condition is what proved it actually moved.
                # Reassign start_time and persist the idle period so the
                # popup, /api/vehicles, and the start-time CDF all use
                # the real departure time instead of the nominal one.
                trip.start_time = now
                trip.idle_seconds = round(now - trip.nominal_start, 1)
                trip._travel_detected = True
                if trip.idle_seconds > 0:
                    record_travel_start(
                        trip.route,
                        int(trip.nominal_start * 1000),
                        int(now * 1000),
                        trip.idle_seconds,
                    )
            return

        moved = abs(new_da - prev_da) > _DORMANT_MOVE_M
        if moved:
            trip._last_move_ts = now
            if trip.dormant:
                if _near_origin(shape, lat, lng):
                    # Dormant trip woke up at the origin — treat as a fresh
                    # trip.  Retire here; the next poll will create the new
                    # trip via the usual _create_trip path.
                    trip.retired = True
                    return
                trip.dormant = False  # continuation
        elif (not trip.dormant
              and now - trip._last_move_ts > _DORMANT_AFTER_S):
            trip.dormant = True

        # Travel-start detection: SEPTA can mark a trip live while the vehicle
        # sits at the terminus.  If the trip stays within _TRAVEL_MIN_MOVE of
        # its first observed position for _TRAVEL_IDLE_POLLS or more polls,
        # the first subsequent movement is treated as the real start time.
        if not trip._travel_detected:
            if abs(new_da - trip.first_dist_along) > _TRAVEL_MIN_MOVE:
                if trip._stationary_polls >= _TRAVEL_IDLE_POLLS:
                    trip.start_time = now
                    trip.idle_seconds = round(now - trip.nominal_start, 1)
                    record_travel_start(
                        trip.route,
                        int(trip.nominal_start * 1000),
                        int(now * 1000),
                        trip.idle_seconds,
                    )
                trip._travel_detected = True
            else:
                trip._stationary_polls += 1

        # Speed from cumulative travel
        elapsed = now - trip.start_time
        trip.speed_mps = round(trip.total_travel / elapsed, 2) if elapsed >= 30 and trip.total_travel >= 50 else None

        # On-detour vehicles are off their normal shape — skip shape-based
        # direction/terminus/stop logic (projection gives meaningless results).
        if trip.on_detour:
            return

        # toward_destination flips to False at destination terminus
        if trip.toward_destination and new_da >= shape.total_len - _TERMINUS_RADIUS:
            self._flip(trip, to_return=True)

        # Retirement: back at origin after having flipped.  Use straight-line
        # distance to the origin coord (not dist_along) so trips retire even
        # when projection is unreliable near the terminus (e.g. trimmed
        # shapes, vehicles ducking into tunnel just before origin).
        if trip.passed_destination and (
                new_da <= _TERMINUS_RADIUS or _near_origin(shape, lat, lng)):
            trip.retired = True
            return

        # Defensive cap: a clean out-and-back crosses up to ~2× stops_total.
        # Anything beyond ~2.5× means the trip looped a second time without
        # being retired — usually because the vehicle re-entered the tunnel
        # before reaching origin.  Retire so the next observation creates a
        # fresh trip rather than letting total_stops_crossed run away.
        if (trip.stops_total > 0
                and trip.total_stops_crossed > trip.stops_total * 2 + trip.stops_total // 2):
            trip.retired = True
            return

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
                        self._flip(trip, to_return=True)
                        stop_flipped = True
                    elif not trip.toward_destination and cur_da > trip._prev_stop_da:
                        self._flip(trip, to_return=False)
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
                    self._flip(trip, to_return=False)
                    _update_stop_info(trip, shape.stops)
                    trip._da_history.clear()
                elif all_rev and net < -_DA_FLIP_THRESH and trip.toward_destination:
                    self._flip(trip, to_return=True)
                    _update_stop_info(trip, shape.stops)
                    trip._da_history.clear()
        else:
            trip._da_history.clear()

    def _flip(self, trip, *, to_return: bool):
        """Flip a trip's direction.  to_return=True means the vehicle just
        reached its destination terminus and is now heading back; False
        means an earlier flip was wrong and is being corrected forward."""
        trip.toward_destination = not to_return
        trip.passed_destination = to_return
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
                if exit_:
                    trip.last_tunnel_exit = exit_
                # Flip to return if still heading toward destination
                if trip.toward_destination:
                    self._flip(trip, to_return=True)
                shape = self._shapes.get(trip.route)
                if shape:
                    _update_stop_info(trip, shape.stops)

    def _record_retirement(self, trip):
        """Persist final trip stats to today.json on retirement.

        fraction_stops_passed is total_stops_crossed / effective_total,
        capped at 1.0.  For detoured trolleys the denominator is the stops
        passed before the detour began (the route the vehicle could
        actually traverse), not the full route.
        """
        is_tunnel_route = trip.route in _TUNNEL_ROUTES
        if trip.was_on_detour and trip.stops_at_detour_start > 0:
            denom = trip.stops_at_detour_start
        else:
            denom = trip.stops_total
        if denom > 0:
            fraction = round(min(trip.total_stops_crossed, denom) / denom, 3)
        else:
            fraction = None
        record_finish(
            trip.route,
            int(trip.start_time * 1000),
            elapsed_seconds=round(trip.last_update - trip.start_time, 1),
            stops_passed=trip.total_stops_crossed,
            fraction_stops_passed=fraction,
            was_on_detour=trip.was_on_detour,
            tunnel_seconds=round(trip.tunnel_seconds, 1) if is_tunnel_route else None,
        )

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
        v['nominal_start_time'] = round(trip.nominal_start, 3)
        v['idle_seconds'] = round(trip.idle_seconds, 1)
        v['vehicle_type'] = trip.vehicle_type
        v['on_detour'] = trip.on_detour
        # Born-dormant: at origin, SEPTA-marked-live, hasn't moved.  Exposed
        # so the client can render a dashed/pulsing marker and exclude the
        # vehicle from downstream-stop arrival predictions.
        if trip._born_dormant:
            v['dormant'] = True

        v['position'] = {
            'lat': trip.current_location[0],
            'lng': trip.current_location[1],
            'heading': round(heading, 1),
            'speed_mps': trip.speed_mps or self._route_avg_speed.get(trip.route),
            'dist_along': round(da, 1),
            'shape_total_len': round(shape.total_len, 1),
        }

        is_tunnel_route = trip.route in _TUNNEL_ROUTES
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
