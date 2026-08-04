"""Tests for pkg.geo — the pure geometry used for every position estimate."""

import math

import pytest

from pkg import geo


# Real Philadelphia landmarks used as fixtures.
CITY_HALL = (39.9526, -75.1652)
THIRTEENTH_ST = (39.9525, -75.1626)
FORTIETH_PORTAL = (39.94939, -75.20333)


class TestDistance:
    def test_zero_for_identical_points(self):
        assert geo.distance(*CITY_HALL, *CITY_HALL) == 0

    def test_city_hall_to_13th_st(self):
        # ~220 m east along Market St.
        d = geo.distance(*CITY_HALL, *THIRTEENTH_ST)
        assert 200 < d < 250

    def test_city_hall_to_40th_portal(self):
        # ~3.3 km west.
        d = geo.distance(*CITY_HALL, *FORTIETH_PORTAL)
        assert 3000 < d < 3600

    def test_symmetric(self):
        a = geo.distance(*CITY_HALL, *FORTIETH_PORTAL)
        b = geo.distance(*FORTIETH_PORTAL, *CITY_HALL)
        assert a == pytest.approx(b, rel=1e-6)

    def test_one_degree_latitude(self):
        assert geo.distance(39.0, -75.0, 40.0, -75.0) == pytest.approx(111320, rel=1e-9)


class TestBearing:
    @pytest.mark.parametrize("dlat,dlng,expected", [
        (1, 0, 0),      # north
        (0, 1, 90),     # east
        (-1, 0, 180),   # south
        (0, -1, 270),   # west
    ])
    def test_cardinal_directions(self, dlat, dlng, expected):
        lat, lng = CITY_HALL
        b = geo.bearing(lat, lng, lat + dlat * 0.01, lng + dlng * 0.01)
        assert b == pytest.approx(expected, abs=1.0)

    def test_always_in_range(self):
        lat, lng = CITY_HALL
        for angle in range(0, 360, 15):
            r = math.radians(angle)
            b = geo.bearing(lat, lng, lat + 0.01 * math.cos(r), lng + 0.01 * math.sin(r))
            assert 0 <= b < 360


@pytest.fixture
def straight_line():
    """A due-north polyline: 4 points, ~1113 m apart, with cumulative distances."""
    pts = [(39.95, -75.16), (39.96, -75.16), (39.97, -75.16), (39.98, -75.16)]
    cum = [0.0]
    for i in range(1, len(pts)):
        cum.append(cum[-1] + geo.distance(*pts[i - 1], *pts[i]))
    return pts, cum, cum[-1]


class TestProject:
    def test_point_on_the_line(self, straight_line):
        pts, cum, total = straight_line
        da, perp = geo.project_with_perp(pts, cum, 39.96, -75.16)
        assert da == pytest.approx(cum[1], abs=1.0)
        assert perp == pytest.approx(0, abs=1.0)

    def test_point_beside_the_line(self, straight_line):
        pts, cum, total = straight_line
        # Offset ~85 m east of the midpoint of the first segment.
        da, perp = geo.project_with_perp(pts, cum, 39.955, -75.159)
        assert da == pytest.approx(cum[1] / 2, rel=0.05)
        assert 50 < perp < 120

    def test_point_past_the_end_clamps(self, straight_line):
        pts, cum, total = straight_line
        da, perp = geo.project_with_perp(pts, cum, 40.10, -75.16)
        # Projection clamps to the final vertex; perp carries the overshoot.
        assert da == pytest.approx(total, abs=1.0)
        assert perp > 10000

    def test_point_before_the_start_clamps(self, straight_line):
        pts, cum, _ = straight_line
        da, _perp = geo.project_with_perp(pts, cum, 39.90, -75.16)
        assert da == pytest.approx(0.0, abs=1.0)

    def test_project_matches_project_with_perp(self, straight_line):
        pts, cum, _ = straight_line
        da_only = geo.project(pts, cum, 39.965, -75.161)
        da_pair, _ = geo.project_with_perp(pts, cum, 39.965, -75.161)
        assert da_only == da_pair


class TestInterpolate:
    def test_endpoints(self, straight_line):
        pts, cum, total = straight_line
        assert geo.interpolate(pts, cum, total, 0) == pytest.approx(pts[0], abs=1e-9)
        assert geo.interpolate(pts, cum, total, total) == pytest.approx(pts[-1], abs=1e-6)

    def test_clamps_out_of_range(self, straight_line):
        pts, cum, total = straight_line
        assert geo.interpolate(pts, cum, total, -500) == pytest.approx(pts[0], abs=1e-9)
        assert geo.interpolate(pts, cum, total, total * 5) == pytest.approx(pts[-1], abs=1e-6)

    def test_round_trips_through_project(self, straight_line):
        """interpolate(project(p)) should land back on p for a point on the line."""
        pts, cum, total = straight_line
        target = (39.965, -75.16)
        da = geo.project(pts, cum, *target)
        back = geo.interpolate(pts, cum, total, da)
        assert geo.distance(*back, *target) < 1.0

    def test_midpoint_of_first_segment(self, straight_line):
        pts, cum, total = straight_line
        lat, lng = geo.interpolate(pts, cum, total, cum[1] / 2)
        assert lat == pytest.approx(39.955, abs=1e-4)
        assert lng == pytest.approx(-75.16, abs=1e-9)


class TestShapeHeading:
    def test_forward_is_north(self, straight_line):
        pts, cum, total = straight_line
        h = geo.shape_heading(pts, cum, total, total / 2, forward=True)
        assert h == pytest.approx(0, abs=1.0)

    def test_backward_is_south(self, straight_line):
        pts, cum, total = straight_line
        h = geo.shape_heading(pts, cum, total, total / 2, forward=False)
        assert h == pytest.approx(180, abs=1.0)

    def test_forward_at_the_end_still_resolves(self, straight_line):
        """At the far end, look-ahead clamps onto the current point; the
        fallback offset must keep the heading meaningful rather than 0."""
        pts, cum, total = straight_line
        h = geo.shape_heading(pts, cum, total, total, forward=False)
        assert h == pytest.approx(180, abs=1.0)

    def test_degenerate_shape_returns_zero(self):
        """A zero-length shape has no direction — must not raise."""
        pts = [(39.95, -75.16), (39.95, -75.16)]
        cum = [0.0, 0.0]
        assert geo.shape_heading(pts, cum, 0.0, 0.0, forward=True) == 0
