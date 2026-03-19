"""Pure geometry: distance, bearing, and shape projection.

All functions are stateless — no side effects, no module-level state.
"""

import math


def distance(lat1, lng1, lat2, lng2):
    """Approximate distance in meters between two lat/lng points."""
    dlat = (lat1 - lat2) * 111320
    dlng = (lng1 - lng2) * 111320 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.sqrt(dlat * dlat + dlng * dlng)


def bearing(lat1, lng1, lat2, lng2):
    """Compass bearing (0–360°) from point 1 to point 2."""
    dlng = math.radians(lng2 - lng1)
    lat1r, lat2r = math.radians(lat1), math.radians(lat2)
    x = math.sin(dlng) * math.cos(lat2r)
    y = (math.cos(lat1r) * math.sin(lat2r) -
         math.sin(lat1r) * math.cos(lat2r) * math.cos(dlng))
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def project(pts, cum_dist, lat, lng):
    """Project a point onto a polyline.  Returns distance-along in meters."""
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
        perp = distance(lat, lng, proj_lat, proj_lng)
        if perp < best_perp:
            best_perp = perp
            best_da = cum_dist[i - 1] + t * (cum_dist[i] - cum_dist[i - 1])

    return best_da


def interpolate(pts, cum_dist, total_len, dist_along):
    """Interpolate a lat/lng at a given distance along a polyline."""
    dist_along = max(0, min(total_len, dist_along))

    seg = 0
    for i in range(1, len(cum_dist)):
        if cum_dist[i] >= dist_along:
            seg = i - 1
            break
    else:
        seg = len(cum_dist) - 2

    span = cum_dist[seg + 1] - cum_dist[seg]
    t = (dist_along - cum_dist[seg]) / span if span > 0 else 0
    lat = pts[seg][0] + t * (pts[seg + 1][0] - pts[seg][0])
    lng = pts[seg][1] + t * (pts[seg + 1][1] - pts[seg][1])
    return lat, lng


def shape_heading(pts, cum_dist, total_len, dist_along, forward, look_ahead=200):
    """Bearing along a polyline at dist_along.  forward=True → toward end."""
    cur_lat, cur_lng = interpolate(pts, cum_dist, total_len, dist_along)

    offset = look_ahead if forward else -look_ahead
    tgt_da = max(0, min(total_len, dist_along + offset))
    tgt_lat, tgt_lng = interpolate(pts, cum_dist, total_len, tgt_da)

    if cur_lat == tgt_lat and cur_lng == tgt_lng:
        fb_da = max(0, min(total_len, dist_along + (50 if forward else -50)))
        tgt_lat, tgt_lng = interpolate(pts, cum_dist, total_len, fb_da)

    if cur_lat == tgt_lat and cur_lng == tgt_lng:
        return 0

    return bearing(cur_lat, cur_lng, tgt_lat, tgt_lng)
