"""Compute direction-of-travel and shape-aligned heading for transit vehicles.

Each poll cycle, vehicles are projected onto their route's GTFS shape.
Direction is determined by comparing consecutive projections.  The heading
is the bearing along the shape toward the destination terminus.

This replaces the old approach of using SEPTA's reported destination text
to infer direction, and SEPTA's GPS bearing for the arrow heading.
"""

import json
import math
import threading
import time

from .helpers import BASE

# ── Terminus definitions ────────────────────────────────────────────
# (start_name, start_lat, start_lng, end_name, end_lat, end_lng)
# "start" = outer/western terminus (index 0 after orientation)
# "end"   = inner/eastern terminus (13th St for trolleys)
TERMINI = {
    'T1': ('63rd-Malvern',  39.9838, -75.2460, '13th St', 39.9525, -75.1626),
    'T2': ('61st-Baltimore', 39.9440, -75.2463, '13th St', 39.9525, -75.1626),
    'T3': ('Darby TC',      39.9191, -75.2624, '13th St', 39.9525, -75.1626),
    'T4': ('Island Av',     39.9171, -75.2464, '13th St', 39.9525, -75.1626),
    'T5': ('Elmwood Loop',  39.9140, -75.2426, '13th St', 39.9525, -75.1626),
    'G1': ('63rd & Girard', 39.9702, -75.2446, 'Richmond & Westmoreland', 39.9843, -75.0996),
    'MFL': ('69th St TC',   39.9623, -75.2586, 'Frankford TC', 40.0229, -75.0779),
    'BSL': ('NRG Station',  39.9054, -75.1739, 'Fern Rock TC', 40.0419, -75.1368),
}

# ── Shape data (loaded once) ───────────────────────────────────────
_shapes = {}       # route_id → [(lat, lng), ...]
_cum_dist = {}     # route_id → [cumulative distance in meters]
_total_len = {}    # route_id → total shape length in meters

# ── Per-vehicle state ──────────────────────────────────────────────
# vid → {dist_along, forward, route, ts, first_ts, first_da}
_vstate = {}
_lock = threading.Lock()

_MIN_MOVE = 20     # meters — minimum movement to update direction
_STALE_S = 600     # seconds — drop vehicle state after this long
_LOOK_AHEAD_M = 200  # meters to look ahead for bearing


def _dist(lat1, lng1, lat2, lng2):
    dlat = (lat1 - lat2) * 111320
    dlng = (lng1 - lng2) * 111320 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.sqrt(dlat * dlat + dlng * dlng)


def _bearing(lat1, lng1, lat2, lng2):
    dlng = math.radians(lng2 - lng1)
    lat1r, lat2r = math.radians(lat1), math.radians(lat2)
    x = math.sin(dlng) * math.cos(lat2r)
    y = (math.cos(lat1r) * math.sin(lat2r) -
         math.sin(lat1r) * math.cos(lat2r) * math.cos(dlng))
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def load_shapes():
    """Load GTFS shapes and orient them so index 0 = start terminus."""
    shapes_path = BASE / "static" / "shapes.json"
    if not shapes_path.exists():
        print("  [direction] shapes.json not found — direction enrichment disabled")
        return

    with open(shapes_path) as f:
        raw = json.load(f)

    for route_id, coords in raw.items():
        if not coords or len(coords) < 2:
            continue

        pts = [(c[0], c[1]) for c in coords]

        # Orient shape so index 0 is near the start terminus
        term = TERMINI.get(route_id)
        if term:
            s_lat, s_lng = term[1], term[2]
            d0 = _dist(pts[0][0], pts[0][1], s_lat, s_lng)
            dn = _dist(pts[-1][0], pts[-1][1], s_lat, s_lng)
            if dn < d0:
                pts = list(reversed(pts))

        _shapes[route_id] = pts

        # Build cumulative distance
        cum = [0.0]
        for i in range(1, len(pts)):
            cum.append(cum[-1] + _dist(pts[i - 1][0], pts[i - 1][1],
                                       pts[i][0], pts[i][1]))
        _cum_dist[route_id] = cum
        _total_len[route_id] = cum[-1]

    print(f"  [direction] loaded {len(_shapes)} route shapes")


def _project(route_id, lat, lng):
    """Project a point onto the route shape.  Returns distance-along in meters."""
    pts = _shapes.get(route_id)
    cum = _cum_dist.get(route_id)
    if not pts or not cum:
        return None

    best_da = 0.0
    best_perp = float('inf')
    cos_lat = math.cos(math.radians(lat))

    for i in range(1, len(pts)):
        p0, p1 = pts[i - 1], pts[i]
        dx = (p1[0] - p0[0]) * 111320
        dy = (p1[1] - p0[1]) * 111320 * cos_lat
        px = (lat - p0[0]) * 111320
        py = (lng - p0[1]) * 111320 * cos_lat
        seg2 = dx * dx + dy * dy
        t = max(0, min(1, (px * dx + py * dy) / seg2)) if seg2 > 0 else 0
        proj_lat = p0[0] + t * (p1[0] - p0[0])
        proj_lng = p0[1] + t * (p1[1] - p0[1])
        perp = _dist(lat, lng, proj_lat, proj_lng)
        if perp < best_perp:
            best_perp = perp
            best_da = cum[i - 1] + t * (cum[i] - cum[i - 1])

    return best_da


def _interp_point(route_id, dist_along):
    """Interpolate a lat/lng along the shape at given distance."""
    pts = _shapes[route_id]
    cum = _cum_dist[route_id]

    # Clamp
    dist_along = max(0, min(_total_len[route_id], dist_along))

    seg = 0
    for i in range(1, len(cum)):
        if cum[i] >= dist_along:
            seg = i - 1
            break
    else:
        seg = len(cum) - 2

    span = cum[seg + 1] - cum[seg]
    t = (dist_along - cum[seg]) / span if span > 0 else 0
    lat = pts[seg][0] + t * (pts[seg + 1][0] - pts[seg][0])
    lng = pts[seg][1] + t * (pts[seg + 1][1] - pts[seg][1])
    return lat, lng


def _shape_heading(route_id, dist_along, forward):
    """Bearing along shape at dist_along.  forward=True → toward end."""
    if route_id not in _shapes:
        return 0

    cur_lat, cur_lng = _interp_point(route_id, dist_along)

    offset = _LOOK_AHEAD_M if forward else -_LOOK_AHEAD_M
    tgt_da = dist_along + offset
    tgt_da = max(0, min(_total_len[route_id], tgt_da))
    tgt_lat, tgt_lng = _interp_point(route_id, tgt_da)

    if cur_lat == tgt_lat and cur_lng == tgt_lng:
        # Fallback: use a small offset in the other direction
        fb_da = dist_along + (50 if forward else -50)
        fb_da = max(0, min(_total_len[route_id], fb_da))
        tgt_lat, tgt_lng = _interp_point(route_id, fb_da)

    if cur_lat == tgt_lat and cur_lng == tgt_lng:
        return 0

    return _bearing(cur_lat, cur_lng, tgt_lat, tgt_lng)


def _make_vid(v):
    """Build vehicle ID matching ghosts.py logic."""
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


def enrich_vehicles(transit_routes):
    """Add computed direction and heading to vehicle dicts in-place.

    Adds to each vehicle dict:
      computed_heading:   bearing (0-360°) along shape in direction of travel
      computed_direction: 'forward' (toward end terminus) or 'reverse'
      toward_terminus:    name of the terminus the vehicle is heading toward
      dist_along:         distance along shape in meters (from start terminus)
      first_seen_ts:      epoch timestamp when vehicle was first tracked
      first_dist_along:   distance along shape at first sighting
      speed_mps:          speed in meters per second (null if < 30s tracking)
      shape_total_len:    total shape length in meters
    """
    now = time.time()

    with _lock:
        seen = set()

        for route_id, vehicles in transit_routes.items():
            if route_id not in _shapes:
                continue

            tlen = _total_len.get(route_id, 0)
            if tlen == 0:
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
                da = _project(route_id, lat, lng)
                if da is None:
                    continue

                # Determine direction from consecutive positions
                prev = _vstate.get(vid)
                if prev and prev['route'] == route_id:
                    delta = da - prev['dist_along']
                    if abs(delta) > _MIN_MOVE:
                        forward = delta > 0
                    else:
                        forward = prev['forward']
                else:
                    # First sighting: closer to start terminus → heading toward end
                    if term:
                        d_to_start = _dist(lat, lng, term[1], term[2])
                        d_to_end = _dist(lat, lng, term[4], term[5])
                        forward = d_to_start < d_to_end
                    else:
                        forward = True

                heading = _shape_heading(route_id, da, forward)

                if term:
                    toward = term[3] if forward else term[0]
                else:
                    toward = ''

                v['computed_heading'] = round(heading, 1)
                v['computed_direction'] = 'forward' if forward else 'reverse'
                if toward:
                    v['toward_terminus'] = toward

                # First-seen tracking for NTA speed computation
                if prev and prev['route'] == route_id:
                    first_ts = prev['first_ts']
                    first_da = prev['first_da']
                else:
                    first_ts = now
                    first_da = da

                # Compute speed (meters/second) from first sighting
                elapsed = now - first_ts
                travel = abs(da - first_da)
                if elapsed >= 30 and travel >= 50:
                    speed = travel / elapsed
                else:
                    speed = None

                v['dist_along'] = round(da, 1)
                v['first_seen_ts'] = round(first_ts, 3)
                v['first_dist_along'] = round(first_da, 1)
                v['speed_mps'] = round(speed, 2) if speed is not None else None
                v['shape_total_len'] = round(tlen, 1)

                _vstate[vid] = {
                    'dist_along': da,
                    'forward': forward,
                    'route': route_id,
                    'ts': now,
                    'first_ts': first_ts,
                    'first_da': first_da,
                }

        # Prune stale state
        for vid in list(_vstate):
            if now - _vstate[vid]['ts'] > _STALE_S:
                del _vstate[vid]


def is_heading_to_end(vid):
    """Return True if vehicle is heading toward the end terminus.

    For trolley routes, the end terminus is 13th St (eastbound).
    Returns None if direction is unknown.
    """
    with _lock:
        state = _vstate.get(vid)
        if state:
            return state['forward']
    return None
