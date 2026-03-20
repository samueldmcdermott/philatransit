from __future__ import annotations

"""Route shape data, terminus definitions, and stop positions.

Shapes are loaded from static/shapes.json, terminus definitions from
static/termini.json, and stop positions from static/route_stops.json.
Shapes are oriented so that index 0 corresponds to the start
(outer/western) terminus.  Stop positions are projected onto each shape
at load time so that every stop has a known distance-along value,
enabling the Trip model to compute current_stop, next_stop,
stops_passed, and stops_remaining.
"""

import json
from dataclasses import dataclass, field

from .helpers import BASE
from . import geo


# ── Load terminus and stop data from static JSON ─────────────────────

def _load_termini():
    path = BASE / "static" / "termini.json"
    if not path.exists():
        print("  [shapes] termini.json not found")
        return {}
    with open(path) as f:
        raw = json.load(f)
    return {
        k: (v["start_name"], v["start_lat"], v["start_lng"],
            v["end_name"], v["end_lat"], v["end_lng"])
        for k, v in raw.items()
    }


def _load_raw_stops():
    path = BASE / "static" / "route_stops.json"
    if not path.exists():
        print("  [shapes] route_stops.json not found")
        return {}
    with open(path) as f:
        raw = json.load(f)
    return {k: [(s[0], s[1], s[2]) for s in v] for k, v in raw.items()}


TERMINI = _load_termini()
_RAW_STOPS = _load_raw_stops()


# ── RouteShape: loaded shape + projected stops ────────────────────────

@dataclass
class RouteShape:
    route_id: str
    pts: list                   # [(lat, lng), ...]
    cum_dist: list              # [float, ...]
    total_len: float
    terminus: tuple             # (start_name, start_lat, start_lng, end_name, end_lat, end_lng)
    stops: list = field(default_factory=list)   # [(name, dist_along), ...] sorted
    origin_bearing: float = 0.0  # bearing from start to end terminus


# Non-revenue shape prefix indices to strip before projection.
# Must stay in sync with ROUTE_SPURS.cutoffIndex in tunnel.js.
_SHAPE_TRIM = {
    # T2 GTFS shape: Elmwood Loop spur (0-103) + backtrack to 61st (103-174).
    # Trim to start at the western terminus for a clean one-directional shape.
    'T2': 174,
}

# Module-level registry — populated by load_shapes()
routes: dict[str, RouteShape] = {}


_SAMPLE_SPACING_M = 350  # auto-generate a stop roughly every 350 m


def _sample_stops(pts, cum_dist, spacing=_SAMPLE_SPACING_M):
    """Generate evenly-spaced synthetic stops along a shape polyline.

    Returns [(name, dist_along), ...] — same format as projected real stops.
    """
    if not cum_dist:
        return []
    total = cum_dist[-1]
    stops = [('Start', 0.0)]
    accum = 0.0
    for i in range(1, len(cum_dist)):
        seg = cum_dist[i] - cum_dist[i - 1]
        accum += seg
        if accum >= spacing:
            stops.append((f'Stop {len(stops)}', round(cum_dist[i], 1)))
            accum = 0.0
    stops.append(('End', round(total, 1)))
    return stops


def load_shapes():
    """Load GTFS shapes, orient them, project stops, and populate `routes`."""
    shapes_path = BASE / "static" / "shapes.json"
    if not shapes_path.exists():
        print("  [shapes] shapes.json not found — shape enrichment disabled")
        return

    with open(shapes_path) as f:
        raw = json.load(f)

    for route_id, coords in raw.items():
        if not coords or len(coords) < 2:
            continue

        pts = [(c[0], c[1]) for c in coords]

        # Orient so index 0 is near the start terminus
        term = TERMINI.get(route_id)
        if term:
            s_lat, s_lng = term[1], term[2]
            d0 = geo.distance(pts[0][0], pts[0][1], s_lat, s_lng)
            dn = geo.distance(pts[-1][0], pts[-1][1], s_lat, s_lng)
            if dn < d0:
                pts = list(reversed(pts))

        # Strip non-revenue prefix (spur + backtrack) so vehicles on the
        # revenue route don't get ambiguous projections onto overlapping
        # shape segments.
        trim = _SHAPE_TRIM.get(route_id)
        if trim is not None:
            pts = pts[trim:]

        # Build cumulative distance
        cum = [0.0]
        for i in range(1, len(pts)):
            cum.append(cum[-1] + geo.distance(
                pts[i - 1][0], pts[i - 1][1], pts[i][0], pts[i][1]
            ))
        total = cum[-1]

        # Bearing from start terminus to end terminus
        origin_bearing = 0.0
        if term:
            origin_bearing = geo.bearing(term[1], term[2], term[4], term[5])

        # Project stops onto shape and sort by distance-along
        stop_dists = []
        raw_stops = _RAW_STOPS.get(route_id, [])
        for name, slat, slng in raw_stops:
            da = geo.project(pts, cum, slat, slng)
            stop_dists.append((name, round(da, 1)))
        stop_dists.sort(key=lambda s: s[1])

        # Auto-generate synthetic stops for routes with no defined stops,
        # so the Trip direction-correction logic has stop transitions to
        # detect actual movement direction.
        if not stop_dists:
            stop_dists = _sample_stops(pts, cum)

        routes[route_id] = RouteShape(
            route_id=route_id,
            pts=pts,
            cum_dist=cum,
            total_len=total,
            terminus=term or ('', 0, 0, '', 0, 0),
            stops=stop_dists,
            origin_bearing=round(origin_bearing, 1),
        )

    print(f"  [shapes] loaded {len(routes)} route shapes")
