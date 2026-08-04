"""Tests for pkg.core.trip — Trip lifecycle driven through TripManager.

These exercise enrich_vehicles() over synthetic poll sequences on a
hand-built straight-line route, so direction flips, retirement, dormancy,
and route disambiguation are all observable without touching the network.
"""

import pytest

from pkg import geo
from pkg.core import trip as trip_mod
from pkg.core.shapes import RouteShape
from pkg.core.trip import TripManager


# A due-east route ~4.4 km long, standing in for a trolley line.
ORIGIN = (39.9500, -75.2000)
DEST = (39.9500, -75.1500)


def build_shape(route_id="T1", origin=ORIGIN, dest=DEST, n_stops=10):
    """A straight polyline from origin to dest with evenly spaced stops.

    Stops span only the inner 92% of the shape. Real stops come from
    projecting route_stops.json onto the GTFS shape, which nearly always
    runs past the outermost stop, and Trip's direction logic depends on
    that overhang existing.
    """
    steps = 50
    pts = [
        (origin[0] + (dest[0] - origin[0]) * i / steps,
         origin[1] + (dest[1] - origin[1]) * i / steps)
        for i in range(steps + 1)
    ]
    cum = [0.0]
    for i in range(1, len(pts)):
        cum.append(cum[-1] + geo.distance(*pts[i - 1], *pts[i]))
    total = cum[-1]
    stops = [(f"Stop {i}", round(total * 0.92 * i / (n_stops - 1), 1))
             for i in range(n_stops)]
    return RouteShape(
        route_id=route_id,
        pts=pts,
        cum_dist=cum,
        total_len=total,
        terminus=("Origin Terminus", origin[0], origin[1],
                  "Dest Terminus", dest[0], dest[1]),
        stops=stops,
        origin_bearing=geo.bearing(*origin, *dest),
    )


class StubRegistry:
    """Minimal stand-in for RouteShapeRegistry."""

    def __init__(self, shapes):
        self._shapes = {s.route_id: s for s in shapes}

    def get(self, route_id):
        return self._shapes.get(route_id)


def point_at(shape, fraction):
    """A lat/lng a given fraction along the shape."""
    return geo.interpolate(shape.pts, shape.cum_dist, shape.total_len,
                           shape.total_len * fraction)


def vehicle(vid, route, lat, lng, label=None):
    return {"vehicle_id": vid, "route_id": route, "lat": lat, "lng": lng,
            "label": label or vid, "meta": {}}


@pytest.fixture(autouse=True)
def no_disk_writes(monkeypatch):
    """Trip records start/finish through stats; keep that off disk."""
    def noop(*_args, **_kwargs):
        return None

    for name in ("record_start", "record_finish", "record_travel_start"):
        monkeypatch.setattr(trip_mod, name, noop)


@pytest.fixture
def shape():
    return build_shape()


@pytest.fixture
def manager(shape):
    return TripManager(
        shape_registry=StubRegistry([shape]),
        route_config={"T1": {
            "origin": "Origin Terminus",
            "destination": "Dest Terminus",
            "origin_to_dest_bearing": 90.0,
            "mode": "TROLLEY",
        }},
    )


def poll(manager, shape, fraction, route="T1", vid="9001"):
    """Run one enrichment cycle with the vehicle at `fraction` along the shape."""
    lat, lng = point_at(shape, fraction)
    routes = {route: [vehicle(vid, route, lat, lng)]}
    manager.enrich_vehicles(routes)
    return routes[route]


def run(manager, shape, fractions, route="T1", vid="9001"):
    """Poll a sequence of positions; return the last cycle's output.

    Direction correction is built around small per-poll increments (SEPTA is
    polled every 5s), so journeys must be simulated as a sequence rather than
    a single jump between distant points.
    """
    out = []
    for f in fractions:
        out = poll(manager, shape, f, route=route, vid=vid)
    return out


# Outbound leg that stops short of the terminus radius, the full outbound
# leg including the terminus crossing, and the return leg.
OUTBOUND = [0.3, 0.5, 0.7, 0.9]
TO_TERMINUS = OUTBOUND + [0.96, 0.99]
RETURN = [0.9, 0.8, 0.7, 0.6, 0.5]


class TestCreation:
    def test_trip_created_mid_route(self, manager, shape):
        out = poll(manager, shape, 0.5)
        assert len(out) == 1
        assert out[0]["trip_id"].startswith("9001_")
        assert out[0]["origin"] == "Origin Terminus"
        assert out[0]["destination"] == "Dest Terminus"
        assert out[0]["toward_destination"] is True

    def test_api_fields_are_populated(self, manager, shape):
        out = poll(manager, shape, 0.5)[0]
        assert set(out["position"]) == {
            "lat", "lng", "heading", "speed_mps", "dist_along", "shape_total_len"}
        assert set(out["progress"]) >= {
            "current_stop", "next_stop", "previous_stops", "stops_passed",
            "stops_remaining", "stops_total", "delay_minutes", "elapsed_seconds"}
        assert out["vehicle_type"] == "TROLLEY"

    def test_tunnel_seconds_exposed_only_on_t_routes(self, manager, shape):
        out = poll(manager, shape, 0.5)[0]
        assert "tunnel_seconds" in out["progress"]

    def test_vehicle_without_id_is_skipped(self, manager, shape):
        lat, lng = point_at(shape, 0.5)
        routes = {"T1": [{"vehicle_id": "", "route_id": "T1", "lat": lat, "lng": lng}]}
        manager.enrich_vehicles(routes)
        assert routes["T1"] == []

    def test_null_island_coordinates_are_skipped(self, manager, shape):
        routes = {"T1": [vehicle("9001", "T1", 0, 0)]}
        manager.enrich_vehicles(routes)
        assert routes["T1"] == []

    def test_unknown_route_is_left_alone(self, manager):
        routes = {"ZZ": [vehicle("9001", "ZZ", 39.95, -75.18)]}
        manager.enrich_vehicles(routes)
        assert "trip_id" not in routes["ZZ"][0]


class TestStopProgress:
    def test_counts_are_absolute_along_the_route(self, manager, shape):
        out = poll(manager, shape, 0.5)[0]
        p = out["progress"]
        assert p["stops_passed"] + p["stops_remaining"] == p["stops_total"]
        assert p["stops_total"] == 10

    def test_counts_advance_with_travel(self, manager, shape):
        first = poll(manager, shape, 0.2)[0]["progress"]["stops_passed"]
        poll(manager, shape, 0.5)
        later = poll(manager, shape, 0.8)[0]["progress"]["stops_passed"]
        assert later > first

    def test_counts_still_sum_after_a_direction_flip(self, manager, shape):
        run(manager, shape, TO_TERMINUS)
        p = run(manager, shape, RETURN)[0]["progress"]
        assert p["stops_passed"] + p["stops_remaining"] == p["stops_total"]


class TestDirectionFlip:
    def test_starts_toward_the_destination(self, manager, shape):
        assert run(manager, shape, OUTBOUND)[0]["toward_destination"] is True

    def test_turns_around_on_the_return_leg(self, manager, shape):
        run(manager, shape, TO_TERMINUS)
        assert run(manager, shape, RETURN)[0]["toward_destination"] is False

    def test_turnaround_swaps_origin_and_destination(self, manager, shape):
        run(manager, shape, TO_TERMINUS)
        out = run(manager, shape, RETURN)[0]
        assert out["origin"] == "Dest Terminus"
        assert out["destination"] == "Origin Terminus"

    def test_turnaround_reverses_bearing(self, manager, shape):
        before = run(manager, shape, OUTBOUND)[0]["bearing"]
        after = run(manager, shape, TO_TERMINUS + RETURN)[0]["bearing"]
        assert after == (before + 180) % 360

    def test_sustained_reverse_movement_corrects_direction(self, manager, shape):
        """Movement-based correction catches a wrong initial direction even
        without a terminus crossing — needed on sparse-stop routes."""
        out = run(manager, shape, [0.9, 0.8, 0.7, 0.6, 0.5])
        assert out[0]["toward_destination"] is False


class TestTerminusFlip:
    """Reaching the destination terminus is a positional fact and must win
    over the heuristic correctors, which would otherwise read the vehicle's
    remaining forward motion as evidence it is still outbound and undo it."""

    def test_flips_on_entering_the_terminus_radius(self, manager, shape):
        run(manager, shape, OUTBOUND)
        assert manager._trips["9001"].toward_destination is True
        out = poll(manager, shape, 0.96)      # inside _TERMINUS_RADIUS
        assert out[0]["toward_destination"] is False

    def test_sets_passed_destination_immediately(self, manager, shape):
        """Retirement at the origin is gated on passed_destination, so a
        delayed flag delays retirement and skews the recorded trip stats."""
        run(manager, shape, OUTBOUND)
        poll(manager, shape, 0.96)
        assert manager._trips["9001"].passed_destination is True

    def test_survives_continued_forward_crawl(self, manager, shape):
        """Vehicles keep edging forward for several polls after arriving.
        Each such poll previously re-triggered the forward correctors."""
        run(manager, shape, OUTBOUND)
        out = run(manager, shape, [0.96, 0.97, 0.98, 0.99, 0.995])
        assert out[0]["toward_destination"] is False
        assert manager._trips["9001"].passed_destination is True

    def test_bearing_reverses_at_the_terminus(self, manager, shape):
        before = run(manager, shape, OUTBOUND)[0]["bearing"]
        after = poll(manager, shape, 0.96)[0]["bearing"]
        assert after == (before + 180) % 360

    def test_stop_counts_stay_consistent_across_the_flip(self, manager, shape):
        run(manager, shape, OUTBOUND)
        p = poll(manager, shape, 0.96)[0]["progress"]
        assert p["stops_passed"] + p["stops_remaining"] == p["stops_total"]

    def test_forward_correction_still_works_mid_route(self, manager, shape):
        """The terminus gate must not disable the corrector elsewhere: a trip
        wrongly flagged as returning while mid-route still gets corrected."""
        run(manager, shape, TO_TERMINUS)
        assert manager._trips["9001"].toward_destination is False
        # Now drive it forward again from well inside the route.
        out = run(manager, shape, [0.3, 0.4, 0.5, 0.6, 0.7])
        assert out[0]["toward_destination"] is True

    def test_recovers_from_a_spurious_terminus_reading(self, manager, shape):
        """A single bad GPS fix at the shape end flips the trip. Once real
        positions resume, the movement corrector must still undo it —
        otherwise the terminus flip would be a one-way trap."""
        run(manager, shape, [0.3, 0.4, 0.5])
        poll(manager, shape, 0.99)               # bogus fix at the terminus
        assert manager._trips["9001"].passed_destination is True

        out = run(manager, shape, [0.5, 0.55, 0.6, 0.65, 0.7])
        assert out[0]["toward_destination"] is True
        assert manager._trips["9001"].passed_destination is False


class TestRetirement:
    def test_retires_on_return_to_origin(self, manager, shape):
        run(manager, shape, TO_TERMINUS)
        run(manager, shape, RETURN)
        run(manager, shape, [0.3, 0.1, 0.0])     # home → retire
        assert manager._trips == {}

    def test_next_sighting_starts_a_fresh_trip(self, manager, shape):
        run(manager, shape, TO_TERMINUS)
        run(manager, shape, RETURN)
        first_trip = manager._trips["9001"]
        run(manager, shape, [0.3, 0.1, 0.0])     # retire
        poll(manager, shape, 0.3)
        new_trip = manager._trips["9001"]
        assert new_trip is not first_trip
        assert new_trip.toward_destination is True
        assert new_trip.passed_destination is False

    def test_does_not_retire_before_reaching_the_destination(self, manager, shape):
        first = poll(manager, shape, 0.3)[0]["trip_id"]
        again = run(manager, shape, [0.4, 0.5, 0.6])[0]["trip_id"]
        assert again == first

    def test_records_finish_stats_on_retirement(self, manager, shape, monkeypatch):
        recorded = []
        monkeypatch.setattr(trip_mod, "record_finish",
                            lambda *a, **k: recorded.append((a, k)))
        run(manager, shape, TO_TERMINUS)
        run(manager, shape, RETURN)
        run(manager, shape, [0.3, 0.1, 0.0])
        assert len(recorded) == 1
        assert recorded[0][1]["fraction_stops_passed"] is not None

    def test_stale_trips_are_pruned(self, manager, shape, monkeypatch):
        poll(manager, shape, 0.5)
        assert len(manager._trips) == 1
        # Advance the clock past the stale threshold with no sighting.
        real_time = trip_mod.time.time
        monkeypatch.setattr(trip_mod.time, "time",
                            lambda: real_time() + trip_mod._STALE_S + 60)
        manager.enrich_vehicles({"T1": []})
        assert manager._trips == {}

    def test_underground_trips_survive_stale_pruning(self, manager, shape, monkeypatch):
        """Ghost vehicles have no GPS by definition — the tunnel layer owns
        them, so the stale sweep must not delete their Trip."""
        poll(manager, shape, 0.5)
        manager.set_ghost_labels({"9001"})
        real_time = trip_mod.time.time
        monkeypatch.setattr(trip_mod.time, "time",
                            lambda: real_time() + trip_mod._STALE_S + 60)
        manager.enrich_vehicles({"T1": []})
        assert "9001" in manager._trips


class TestBornDormant:
    def test_trip_starting_at_origin_is_hidden(self, manager, shape):
        """SEPTA flags trolleys live before they leave the yard."""
        out = poll(manager, shape, 0.0)
        assert out[0].get("dormant") is True
        assert manager._trips["9001"]._born_dormant is True

    def test_stays_dormant_while_jittering(self, manager, shape):
        poll(manager, shape, 0.0)
        for _ in range(6):
            poll(manager, shape, 0.0)       # identical position → no movement
        assert manager._trips["9001"]._born_dormant is True

    def test_wakes_after_consecutive_movement(self, manager, shape):
        poll(manager, shape, 0.0)
        for i in range(1, trip_mod._BORN_DORMANT_STEPS + 2):
            poll(manager, shape, 0.001 * i)
        t = manager._trips["9001"]
        assert t._born_dormant is False
        assert t.dormant is False

    def test_wake_resets_the_start_time(self, manager, shape):
        """The wake moment is the real departure, so it becomes start_time
        and the waiting period is recorded as idle_seconds."""
        poll(manager, shape, 0.0)
        nominal = manager._trips["9001"].nominal_start
        for i in range(1, trip_mod._BORN_DORMANT_STEPS + 2):
            poll(manager, shape, 0.001 * i)
        t = manager._trips["9001"]
        assert t.start_time >= nominal
        assert t.idle_seconds >= 0

    def test_trip_starting_mid_route_is_not_born_dormant(self, manager, shape):
        poll(manager, shape, 0.5)
        assert manager._trips["9001"]._born_dormant is False


class TestDormancy:
    def test_dormant_trips_are_hidden_from_the_api(self, manager, shape):
        poll(manager, shape, 0.5)
        manager.mark_dormant_by_labels({"9001"})
        assert poll(manager, shape, 0.5) == []

    def test_dormant_trip_survives_in_the_manager(self, manager, shape):
        poll(manager, shape, 0.5)
        manager.mark_dormant_by_labels({"9001"})
        poll(manager, shape, 0.5)
        assert "9001" in manager._trips

    def test_retire_dormant_trips_clears_them(self, manager, shape):
        poll(manager, shape, 0.5)
        manager.mark_dormant_by_labels({"9001"})
        manager.retire_dormant_trips()
        assert manager._trips == {}

    def test_retire_dormant_leaves_active_trips(self, manager, shape):
        poll(manager, shape, 0.5)
        manager.retire_dormant_trips()
        assert "9001" in manager._trips


class TestGetDirection:
    def test_reports_current_direction(self, manager, shape):
        poll(manager, shape, 0.5)
        assert manager.get_direction("9001") is True

    def test_unknown_vehicle_returns_none(self, manager):
        assert manager.get_direction("nope") is None


class TestCrossRouteDuplicates:
    """SEPTA sometimes lists one fleet number under two routes in a single
    poll; without resolution each appearance would thrash the Trip."""

    @pytest.fixture
    def two_route_manager(self):
        t1 = build_shape("T1", ORIGIN, DEST)
        # T3 runs well to the south, so GPS clearly distinguishes the two.
        t3 = build_shape("T3", (39.9300, -75.2000), (39.9300, -75.1500))
        return TripManager(shape_registry=StubRegistry([t1, t3]),
                           route_config={}), t1, t3

    def test_vehicle_kept_on_the_nearer_route(self, two_route_manager):
        manager, t1, t3 = two_route_manager
        lat, lng = point_at(t1, 0.5)
        routes = {"T1": [vehicle("9001", "T1", lat, lng)],
                  "T3": [vehicle("9001", "T3", lat, lng)]}
        manager.enrich_vehicles(routes)
        assert len(routes["T1"]) == 1
        assert routes["T3"] == []

    def test_only_one_trip_is_created(self, two_route_manager):
        manager, t1, _ = two_route_manager
        lat, lng = point_at(t1, 0.5)
        routes = {"T1": [vehicle("9001", "T1", lat, lng)],
                  "T3": [vehicle("9001", "T3", lat, lng)]}
        manager.enrich_vehicles(routes)
        assert len(manager._trips) == 1

    def test_distinct_vehicles_are_untouched(self, two_route_manager):
        manager, t1, t3 = two_route_manager
        lat1, lng1 = point_at(t1, 0.5)
        lat3, lng3 = point_at(t3, 0.5)
        routes = {"T1": [vehicle("9001", "T1", lat1, lng1)],
                  "T3": [vehicle("9002", "T3", lat3, lng3)]}
        manager.enrich_vehicles(routes)
        assert len(routes["T1"]) == 1
        assert len(routes["T3"]) == 1


class TestTunnelEmergence:
    def test_emergence_flips_direction_and_accrues_tunnel_time(self, manager, shape):
        poll(manager, shape, 0.5)
        t = manager._trips["9001"]
        assert t.toward_destination is True

        manager.apply_tunnel_emergence({
            "9001": {"route": "T1", "entry_time": 1000.0, "exit_time": 1400.0},
        })
        assert t.toward_destination is False
        assert t.tunnel_seconds == 400.0
        assert t.last_tunnel_exit == 1400.0

    def test_emergence_on_a_different_route_is_ignored(self, manager, shape):
        poll(manager, shape, 0.5)
        manager.apply_tunnel_emergence({
            "9001": {"route": "T3", "entry_time": 1000.0, "exit_time": 1400.0},
        })
        assert manager._trips["9001"].tunnel_seconds == 0.0

    def test_tunnel_time_accumulates_across_emergences(self, manager, shape):
        poll(manager, shape, 0.5)
        for entry, exit_ in ((1000.0, 1300.0), (2000.0, 2200.0)):
            manager.apply_tunnel_emergence({
                "9001": {"route": "T1", "entry_time": entry, "exit_time": exit_},
            })
        assert manager._trips["9001"].tunnel_seconds == 500.0
