from __future__ import annotations

"""Route shape data, terminus definitions, and stop positions.

Shapes are loaded from static/shapes.json and oriented so that index 0
corresponds to the start (outer/western) terminus.  Stop positions are
projected onto each shape at load time so that every stop has a known
distance-along value, enabling the Trip model to compute current_stop,
next_stop, stops_passed, and stops_remaining.
"""

import json
from dataclasses import dataclass, field

from .helpers import BASE
from . import geo

# ── Terminus definitions ──────────────────────────────────────────────
# (start_name, start_lat, start_lng, end_name, end_lat, end_lng)
# "start" = outer/western terminus (shape index 0)
# "end"   = inner/eastern terminus

TERMINI = {
    'T1':  ('63rd-Malvern',     39.9838, -75.2460, '13th St',                    39.9525, -75.1626),
    'T2':  ('61st-Baltimore',   39.9440, -75.2463, '13th St',                    39.9525, -75.1626),
    'T3':  ('Darby TC',         39.9191, -75.2624, '13th St',                    39.9525, -75.1626),
    'T4':  ('Island Av',        39.9171, -75.2464, '13th St',                    39.9525, -75.1626),
    'T5':  ('Elmwood Loop',     39.9140, -75.2426, '13th St',                    39.9525, -75.1626),
    'G1':  ('63rd & Girard',    39.9702, -75.2446, 'Richmond & Westmoreland',    39.9843, -75.0996),
    'MFL': ('69th St TC',       39.9623, -75.2586, 'Frankford TC',               40.0229, -75.0779),
    'BSL': ('NRG Station',      39.9054, -75.1739, 'Fern Rock TC',               40.0419, -75.1368),
}


# ── Per-route stop lists ──────────────────────────────────────────────
# (name, lat, lng) — order does not matter; they are sorted by shape
# projection at load time.  Mirrors HARDCODED_STATIONS from the frontend.

_TUNNEL_36TH = [
    ('36th St Portal',    39.9553,   -75.1942),
    ('33rd St',           39.9548,   -75.1895),
    ('30th St',           39.9548,   -75.1835),
    ('22nd St',           39.9540,   -75.1767),
    ('19th St',           39.9533,   -75.1716),
    ('15th St/City Hall', 39.9525,   -75.1653),
    ('13th St',           39.9525,   -75.1626),
]

_TUNNEL_40TH = [
    ('40th St Portal',    39.9502,   -75.2010),
    ('37th & Spruce',     39.9510,   -75.1969),
    ('36th & Sansom',     39.9539,   -75.1947),
    ('33rd St',           39.9548,   -75.1895),
    ('30th St',           39.9548,   -75.1835),
    ('22nd St',           39.9540,   -75.1767),
    ('19th St',           39.9533,   -75.1716),
    ('15th St/City Hall', 39.9525,   -75.1653),
    ('13th St',           39.9525,   -75.1626),
]

_RAW_STOPS = {
    'T1': [
        ('63rd-Malvern',               39.9838, -75.2460),
        ('63rd St & Lebanon Av',       39.9809, -75.2467),
        ('63rd St & Columbia Av',      39.9806, -75.2465),
        ('63rd St & Jefferson St',     39.9784, -75.2462),
        ('Lansdowne Av & 63rd',        39.9753, -75.2450),
        ('Lansdowne Av & 61st St',     39.9757, -75.2419),
        ('Lansdowne Av & 59th St',     39.9761, -75.2387),
        ('Lansdowne Av & 57th St',     39.9764, -75.2354),
        ('Lansdowne Av & 55th St',     39.9768, -75.2319),
        ('Lansdowne Av & Lancaster Av',39.9773, -75.2275),
        ('Lancaster Av & 50th St',     39.9752, -75.2226),
        ('Lancaster Av & Girard Av',   39.9731, -75.2186),
        ('Lancaster Av & 47th St',     39.9716, -75.2161),
        ('Lancaster Av & 45th St',     39.9697, -75.2130),
        ('Lancaster Av & 44th St',     39.9688, -75.2115),
        ('Lancaster Av & 42nd St',     39.9664, -75.2077),
        ('Lancaster Av & 41st St',     39.9651, -75.2056),
        ('Lancaster Av & 40th St',     39.9632, -75.2024),
        ('Lancaster Av & 38th St',     39.9603, -75.1970),
        ('Lancaster Av & Powelton Av', 39.9598, -75.1961),
        ('36th & Market',              39.9561, -75.1941),
        *_TUNNEL_36TH,
    ],
    'T2': [
        ('61st-Baltimore',             39.9440, -75.2463),
        ('Baltimore Av & 60th St',     39.9443, -75.2444),
        ('Baltimore Av & 59th St',     39.9452, -75.2419),
        ('Baltimore Av & 58th St',     39.9458, -75.2403),
        ('Baltimore Av & 57th St',     39.9464, -75.2373),
        ('Baltimore Av & 56th St',     39.9468, -75.2356),
        ('Baltimore Av & 55th St',     39.9472, -75.2337),
        ('Baltimore Av & 54th St',     39.9477, -75.2315),
        ('Baltimore Av & 53rd St',     39.9478, -75.2296),
        ('Baltimore Av & 51st St',     39.9477, -75.2255),
        ('Baltimore Av & 50th St',     39.9479, -75.2234),
        ('Baltimore Av & 49th St',     39.9481, -75.2214),
        ('Baltimore Av & 47th St',     39.9485, -75.2172),
        ('Baltimore Av & 46th St',     39.9488, -75.2151),
        ('Baltimore Av & 45th St',     39.9490, -75.2130),
        ('Baltimore Av & 44th St',     39.9492, -75.2113),
        ('Baltimore Av & 43rd St',     39.9495, -75.2093),
        ('Baltimore Av & 42nd St',     39.9497, -75.2073),
        *_TUNNEL_40TH,
    ],
    'T3': [
        ('Darby Transit Center',       39.9191, -75.2624),
        ('9th St & Summit St',         39.9211, -75.2589),
        ('Yeadon',                     39.9245, -75.2525),
        ('Chester Av & Yeadon Av',     39.9261, -75.2475),
        ('65th St & Chester Av',       39.9286, -75.2389),
        ('Mt. Moriah',                 39.9289, -75.2355),
        ('Kingsessing Av & 61st St',   39.9310, -75.2324),
        ('60th St & Chester Av',       39.9336, -75.2314),
        ('Chester Av & 59th St',       39.9348, -75.2299),
        ('Chester Av & 58th St',       39.9357, -75.2286),
        ('Chester Av & 56th St',       39.9377, -75.2257),
        ('Chester Av & 55th St',       39.9387, -75.2243),
        ('Chester Av & 54th St',       39.9396, -75.2230),
        ('Chester Av & 52nd St',       39.9415, -75.2202),
        ('Chester Av & 51st St',       39.9423, -75.2190),
        ('Chester Av & 49th St',       39.9440, -75.2166),
        ('Chester Av & 48th St',       39.9450, -75.2153),
        ('Chester Av & 47th St',       39.9459, -75.2140),
        ('Chester Av & 46th St',       39.9468, -75.2127),
        ('Chester Av & 45th St',       39.9477, -75.2114),
        ('Chester Av & 42nd St',       39.9484, -75.2072),
        *_TUNNEL_40TH,
    ],
    'T4': [
        ('Woodland Av & Island Av',    39.9171, -75.2464),
        ('Woodland Av & 72nd St',      39.9184, -75.2444),
        ('Woodland Av & 70th St',      39.9205, -75.2415),
        ('Woodland Av & 68th St',      39.9226, -75.2384),
        ('Woodland Av & 65th St',      39.9255, -75.2344),
        ('Woodland Av & 63rd St',      39.9273, -75.2318),
        ('Woodland Av & 62nd St',      39.9280, -75.2308),
        ('Woodland Av & 60th St',      39.9302, -75.2276),
        ('Woodland Av & 58th St',      39.9323, -75.2247),
        ('Woodland Av & 56th St',      39.9343, -75.2218),
        ('Woodland Av & 54th St',      39.9363, -75.2191),
        ('Woodland Av & 52nd St',      39.9382, -75.2163),
        ('Woodland Av & 50th St',      39.9399, -75.2139),
        ('Woodland Av & 48th St',      39.9420, -75.2117),
        ('Woodland Av & 46th St',      39.9443, -75.2098),
        ('Woodland Av & 45th St',      39.9454, -75.2086),
        ('Woodland Av & 42nd St',      39.9467, -75.2071),
        *_TUNNEL_40TH,
    ],
    'T5': [
        ('Elmwood Loop',               39.9140, -75.2426),
        ('Elmwood Av & 73rd St',       39.9141, -75.2418),
        ('Elmwood Av & 71st St',       39.9161, -75.2390),
        ('Elmwood Av & 69th St',       39.9181, -75.2361),
        ('Elmwood Av & 67th St',       39.9203, -75.2329),
        ('Elmwood Av & 65th St',       39.9221, -75.2304),
        ('Elmwood Av & 63rd St',       39.9239, -75.2278),
        ('Elmwood Av & 61st St',       39.9260, -75.2249),
        ('Elmwood Av & 58th St',       39.9290, -75.2206),
        ('Elmwood Av & 56th St',       39.9310, -75.2177),
        ('Lindbergh Blvd & 53rd St',   39.9351, -75.2144),
        ('Grays Av & 51st St',         39.9370, -75.2123),
        ('49th St & Paschall Av',      39.9398, -75.2113),
        ('Woodland Av & 48th St',      39.9420, -75.2117),
        ('Woodland Av & 46th St',      39.9443, -75.2098),
        ('Woodland Av & 45th St',      39.9454, -75.2086),
        ('Woodland Av & 42nd St',      39.9467, -75.2071),
        *_TUNNEL_40TH,
    ],
    'G1': [
        ('63rd & Girard',              39.9702, -75.2446),
        ('Girard Av & 59th St',        39.9702, -75.2373),
        ('Girard Av & 56th St',        39.9708, -75.2325),
        ('Girard Av & 52nd St',        39.9715, -75.2258),
        ('Girard Av & 49th St',        39.9729, -75.2191),
        ('Girard Av & Belmont Av',     39.9733, -75.2121),
        ('Girard Av & 42nd St',        39.9737, -75.2088),
        ('Girard Av & 39th St',        39.9745, -75.2017),
        ('Girard Av & 33rd St',        39.9752, -75.1880),
        ('Girard Av & 29th St',        39.9746, -75.1834),
        ('Girard Av & 27th St',        39.9742, -75.1803),
        ('Girard Av & 24th St',        39.9725, -75.1758),
        ('Girard Av & 20th St',        39.9727, -75.1689),
        ('Girard Av & Ridge Av',       39.9724, -75.1662),
        ('Girard Av & Broad St',       39.9715, -75.1593),
        ('Girard Av & 11th St',        39.9708, -75.1540),
        ('Girard Av & 8th St',         39.9706, -75.1499),
        ('Girard Av & 5th St',         39.9702, -75.1449),
        ('Girard Av & Front St',       39.9689, -75.1361),
        ('Girard Av & Frankford',      39.9689, -75.1344),
        ('Richmond & Girard',          39.9731, -75.1196),
        ('Richmond & Lehigh',          39.9769, -75.1131),
        ('Richmond & Allegheny',       39.9831, -75.1014),
        ('Richmond & Westmoreland',    39.9843, -75.0996),
    ],
    'MFL': [
        ('69th St Transit Center',     39.9623, -75.2586),
        ('Millbourne',                 39.9643, -75.2522),
        ('63rd St',                    39.9627, -75.2468),
        ('60th St',                    39.9620, -75.2408),
        ('56th St',                    39.9610, -75.2329),
        ('52nd St',                    39.9600, -75.2249),
        ('46th St',                    39.9586, -75.2140),
        ('40th St',                    39.9571, -75.2020),
        ('34th St',                    39.9558, -75.1915),
        ('30th St',                    39.9548, -75.1833),
        ('15th St/City Hall',          39.9526, -75.1653),
        ('13th St',                    39.9521, -75.1615),
        ('11th St',                    39.9517, -75.1583),
        ('8th-Market',                 39.9511, -75.1536),
        ('5th St/Independence Hall',   39.9505, -75.1489),
        ('2nd St',                     39.9498, -75.1438),
        ('Spring Garden',              39.9605, -75.1403),
        ('Front-Girard',               39.9689, -75.1361),
        ('Berks',                      39.9786, -75.1334),
        ('York-Dauphin',               39.9855, -75.1319),
        ('Huntingdon',                 39.9888, -75.1273),
        ('Somerset',                   39.9914, -75.1225),
        ('Kensington-Allegheny',       39.9965, -75.1134),
        ('Tioga',                      40.0003, -75.1064),
        ('Erie-Torresdale',            40.0058, -75.0964),
        ('Church',                     40.0109, -75.0887),
        ('Arrott Transit Center',      40.0166, -75.0838),
        ('Frankford Transit Center',   40.0229, -75.0779),
    ],
    'BSL': [
        ('NRG Station',                39.9054, -75.1739),
        ('Oregon',                     39.9168, -75.1714),
        ('Snyder',                     39.9244, -75.1698),
        ('Tasker-Morris',              39.9298, -75.1686),
        ('Ellsworth-Federal',          39.9362, -75.1672),
        ('Lombard-South',              39.9441, -75.1655),
        ('Walnut-Locust',              39.9487, -75.1645),
        ('15th St/City Hall',          39.9525, -75.1641),
        ('Race-Vine',                  39.9570, -75.1626),
        ('Broad-Spring Garden',        39.9624, -75.1615),
        ('Fairmount',                  39.9670, -75.1605),
        ('Broad-Girard',               39.9715, -75.1595),
        ('Cecil B. Moore',             39.9787, -75.1579),
        ('Susquehanna-Dauphin',        39.9870, -75.1561),
        ('North Philadelphia',         39.9939, -75.1546),
        ('Broad-Allegheny',            40.0016, -75.1529),
        ('Erie',                       40.0092, -75.1513),
        ('Hunting Park',               40.0169, -75.1496),
        ('Wyoming',                    40.0246, -75.1479),
        ('Logan',                      40.0306, -75.1466),
        ('Olney Transit Center',       40.0391, -75.1448),
        ('Fern Rock Transit Center',   40.0419, -75.1368),
    ],
}


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


# Non-revenue shape prefixes to strip before projection.
# The T2 GTFS shape includes the Elmwood Loop supply spur (indices 0-103)
# AND a backtrack from the junction to 61st-Baltimore (indices 103-174).
# Stripping everything before the western terminus (index 174) gives a
# clean one-directional shape (61st → 13th) with no doubled-back segments
# that would cause ambiguous projections.
_SHAPE_TRIM = {
    'T2': 174,   # strip spur + backtrack; start at 61st-Baltimore terminus
}

# Module-level registry — populated by load_shapes()
routes: dict[str, RouteShape] = {}


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
