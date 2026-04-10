"""Route shape data, terminus definitions, and stop positions.

Decoupled from any specific provider.  Shapes are loaded from static
JSON files; provider-specific shape trimming is passed as a parameter.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .. import geo


# ── RouteShape dataclass ─────────────────────────────────────────────

@dataclass
class RouteShape:
    route_id: str
    pts: list                   # [(lat, lng), ...]
    cum_dist: list              # [float, ...]
    total_len: float
    terminus: tuple             # (start_name, start_lat, start_lng, end_name, end_lat, end_lng)
    stops: list = field(default_factory=list)   # [(name, dist_along), ...] sorted
    origin_bearing: float = 0.0  # bearing from start to end terminus


# ── RouteShapeRegistry ───────────────────────────────────────────────

class RouteShapeRegistry:
    """Registry of loaded route shapes.  Not a module-level global."""

    def __init__(self):
        self._routes: dict[str, RouteShape] = {}
        self._termini: dict[str, tuple] = {}

    def get(self, route_id: str) -> RouteShape | None:
        return self._routes.get(route_id)

    @property
    def termini(self) -> dict[str, tuple]:
        return self._termini

    def __len__(self):
        return len(self._routes)

    def __contains__(self, route_id):
        return route_id in self._routes


# ── Synthetic stop generation ────────────────────────────────────────

_SAMPLE_SPACING_M = 350


def _sample_stops(pts, cum_dist, spacing=_SAMPLE_SPACING_M):
    """Generate evenly-spaced synthetic stops along a shape polyline."""
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


# ── Loader ───────────────────────────────────────────────────────────

def _load_termini(path: Path) -> dict:
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


def _load_raw_stops(path: Path) -> dict:
    if not path.exists():
        print("  [shapes] route_stops.json not found")
        return {}
    with open(path) as f:
        raw = json.load(f)
    return {k: [(s[0], s[1], s[2]) for s in v] for k, v in raw.items()}


def load_shapes(base_dir: Path, shape_trims: dict | None = None,
                termini: dict | None = None) -> RouteShapeRegistry:
    """Load GTFS shapes, orient them, project stops, and return a registry.

    Parameters:
        base_dir: project root (contains static/ directory)
        shape_trims: optional {route_id: trim_index} for provider-specific
                     non-revenue spur removal
        termini: optional {route_id: (start_name, start_lat, start_lng,
                                      end_name, end_lat, end_lng)}.
                 If None or empty, the static termini.json is used.
    """
    registry = RouteShapeRegistry()

    shapes_path = base_dir / "static" / "shapes.json"
    if not shapes_path.exists():
        print("  [shapes] shapes.json not found — shape enrichment disabled")
        return registry

    if not termini:
        termini = _load_termini(base_dir / "static" / "termini.json")
    raw_stops = _load_raw_stops(base_dir / "static" / "route_stops.json")
    registry._termini = termini

    with open(shapes_path) as f:
        raw = json.load(f)

    shape_trims = shape_trims or {}

    for route_id, coords in raw.items():
        if not coords or len(coords) < 2:
            continue

        pts = [(c[0], c[1]) for c in coords]

        # Orient so index 0 is near the start terminus
        term = termini.get(route_id)
        if term:
            s_lat, s_lng = term[1], term[2]
            d0 = geo.distance(pts[0][0], pts[0][1], s_lat, s_lng)
            dn = geo.distance(pts[-1][0], pts[-1][1], s_lat, s_lng)
            if dn < d0:
                pts = list(reversed(pts))

        # Strip non-revenue prefix
        trim = shape_trims.get(route_id)
        if trim is not None:
            pts = pts[trim:]

        # Build cumulative distance
        cum = [0.0]
        for i in range(1, len(pts)):
            cum.append(cum[-1] + geo.distance(
                pts[i - 1][0], pts[i - 1][1], pts[i][0], pts[i][1]
            ))
        total = cum[-1]

        # For routes without explicit termini, derive from shape endpoints.
        # This gives buses (and any other routes missing from termini.json)
        # a valid origin/destination/bearing.
        if not term and len(pts) >= 2:
            term = (
                f'{route_id} Start', pts[0][0], pts[0][1],
                f'{route_id} End', pts[-1][0], pts[-1][1],
            )
            termini[route_id] = term

        # Bearing from start terminus to end terminus
        origin_bearing = 0.0
        if term:
            origin_bearing = geo.bearing(term[1], term[2], term[4], term[5])

        # Project stops onto shape
        stop_dists = []
        for name, slat, slng in raw_stops.get(route_id, []):
            da = geo.project(pts, cum, slat, slng)
            stop_dists.append((name, round(da, 1)))
        stop_dists.sort(key=lambda s: s[1])

        # Auto-generate synthetic stops if none defined
        if not stop_dists:
            stop_dists = _sample_stops(pts, cum)

        registry._routes[route_id] = RouteShape(
            route_id=route_id,
            pts=pts,
            cum_dist=cum,
            total_len=total,
            terminus=term or ('', 0, 0, '', 0, 0),
            stops=stop_dists,
            origin_bearing=round(origin_bearing, 1),
        )

    print(f"  [shapes] loaded {len(registry)} route shapes")
    return registry
