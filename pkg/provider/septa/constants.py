"""SEPTA-specific constants, URLs, route definitions, and tunnel geometry."""

# ── API endpoints ────────────────────────────────────────────────────

SEPTA_API = "https://www3.septa.org/api"
SEPTA_V2 = "https://www3.septa.org/api/v2"
HEADERS = {"User-Agent": "SEPTA-Live/1.0"}

# ── Rail line aliases (for mapping TrainView fields to route keys) ───

RAIL_ALIASES = {
    "Airport":            ["airport", "phl"],
    "Chestnut Hill East": ["chestnut hill east", "che"],
    "Chestnut Hill West": ["chestnut hill west", "chw"],
    "Cynwyd":             ["cynwyd"],
    "Fox Chase":          ["fox chase"],
    "Lansdale":           ["lansdale", "doylestown"],
    "Media":              ["media", "wawa"],
    "Manayunk":           ["manayunk", "norristown"],
    "Paoli":              ["paoli", "thorndale", "malvern"],
    "Trenton":            ["trenton"],
    "Warminster":         ["warminster"],
    "West Trenton":       ["west trenton"],
    "Wilmington":         ["wilmington", "newark"],
}


def rail_line_key(line, dest, src):
    """Map TrainView fields to a stable route key."""
    for field_val in [line, dest, src]:
        low = (field_val or "").lower()
        for route_id, keys in RAIL_ALIASES.items():
            if any(k in low for k in keys):
                return route_id
    return line or "unknown"


# ── Shape trimming (non-revenue spur removal) ────────────────────────

SHAPE_TRIM = {
    # T2 GTFS shape: Elmwood Loop spur (0-103) + backtrack to 61st (103-174).
    'T2': 174,
}

# ── Tunnel geometry ──────────────────────────────────────────────────

TUNNEL_ROUTES = {'T1', 'T2', 'T3', 'T4', 'T5'}

PORTALS = {
    'T1': (39.9553, -75.1942),
    'T2': (39.94939, -75.20333),
    'T3': (39.94939, -75.20333),
    'T4': (39.94939, -75.20333),
    'T5': (39.94939, -75.20333),
}

TUNNEL_EAST = (39.9525, -75.1626)

LINGER_RADIUS = 0.002

# Tight bounding box around the 40th St tunnel mouth (T2-T5 portal).
MOUTH_40TH_BOX = {
    'minLat': 39.949499, 'maxLat': 39.949647,
    'minLng': -75.203387, 'maxLng': -75.202749,
}
MOUTH_40TH_ROUTES = {'T2', 'T3', 'T4', 'T5'}

LINGER_TIME_S = 60
STATIONARY_THRESH = 0.0005
GHOST_MAX_AGE_S = 25 * 60

EASTBOUND_KW = ['13th', 'market']

# ── Detour detection ────────────────────────────────────────────────
# When the tunnel is closed, trolleys divert to surface streets north of
# Baltimore Ave.  Vehicles in this zone are never there during normal ops.
DETOUR_ZONE = {
    'minLat': 39.952, 'maxLat': 39.970,
    'minLng': -75.210, 'maxLng': -75.195,
}
DETOUR_ROUTES = {'T1', 'T2', 'T3', 'T4', 'T5'}

# ── Route definitions ────────────────────────────────────────────────

RAIL_LINES = [
    {"id": "Airport",            "label": "Airport",             "color": "#a855f7", "gtfs": "Airport",            "alert_ids": ["AIR"]},
    {"id": "Chestnut Hill East", "label": "Chestnut Hill East",  "color": "#10b981", "gtfs": "Chestnut Hill East",  "alert_ids": ["CHE"]},
    {"id": "Chestnut Hill West", "label": "Chestnut Hill West",  "color": "#059669", "gtfs": "Chestnut Hill West",  "alert_ids": ["CHW"]},
    {"id": "Cynwyd",             "label": "Cynwyd",              "color": "#6366f1", "gtfs": "Cynwyd",              "alert_ids": ["CYN"]},
    {"id": "Fox Chase",          "label": "Fox Chase",           "color": "#f97316", "gtfs": "Fox Chase",           "alert_ids": ["FOX"]},
    {"id": "Lansdale",           "label": "Lansdale/Doylestown", "color": "#eab308", "gtfs": "Lansdale",            "alert_ids": ["LAN"]},
    {"id": "Media",              "label": "Media/Wawa",          "color": "#ec4899", "gtfs": "Media",               "alert_ids": ["MED"]},
    {"id": "Manayunk",           "label": "Manayunk/Norristown", "color": "#8b5cf6", "gtfs": "Manayunk",            "alert_ids": ["NOR"]},
    {"id": "Paoli",              "label": "Paoli/Thorndale",     "color": "#0ea5e9", "gtfs": "Paoli",               "alert_ids": ["PAO"]},
    {"id": "Trenton",            "label": "Trenton",             "color": "#ef4444", "gtfs": "Trenton",             "alert_ids": ["TRE"]},
    {"id": "Warminster",         "label": "Warminster",          "color": "#84cc16", "gtfs": "Warminster",          "alert_ids": ["WAR"]},
    {"id": "West Trenton",       "label": "West Trenton",        "color": "#06b6d4", "gtfs": "West Trenton",        "alert_ids": ["WTR"]},
    {"id": "Wilmington",         "label": "Wilmington/Newark",   "color": "#f43f5e", "gtfs": "Wilmington",          "alert_ids": ["WIL"]},
]

SUBWAY_LINES = [
    {"id": "MFL", "api_ids": ["L1"],    "label": "Market-Frankford Line", "color": "#0060a9", "gtfs": "L1", "alert_ids": ["L1", "L1 OWL"]},
    {"id": "BSL", "api_ids": ["B1"],    "label": "Broad Street Line",     "color": "#f97316", "gtfs": "B1", "alert_ids": ["B1", "B1 OWL", "B2"]},
]

TROLLEY_LINES = [
    {"id": "T-ALL", "api_ids": ["T1", "T2", "T3", "T4", "T5"], "label": "All T Lines",          "color": "#e0e8f0", "gtfs": "T-ALL", "multi": True, "alert_ids": ["T1", "T2", "T3", "T4", "T5", "T5 BUS"]},
    {"id": "T1",    "api_ids": ["T1"], "label": "T1 – 10 – Overbrook",  "color": "#22c55e", "gtfs": "T1", "alert_ids": ["T1"]},
    {"id": "T2",    "api_ids": ["T2"], "label": "T2 – 34 – Angora",     "color": "#3b82f6", "gtfs": "T2", "alert_ids": ["T2"]},
    {"id": "T3",    "api_ids": ["T3"], "label": "T3 – 13 – Yeadon",     "color": "#ec4899", "gtfs": "T3", "alert_ids": ["T3"]},
    {"id": "T4",    "api_ids": ["T4"], "label": "T4 – 11 – Darby",      "color": "#8b5cf6", "gtfs": "T4", "alert_ids": ["T4"]},
    {"id": "T5",    "api_ids": ["T5"], "label": "T5 – 36 – Eastwick",   "color": "#f59e0b", "gtfs": "T5", "alert_ids": ["T5", "T5 BUS"]},
    {"id": "G1",    "api_ids": ["G1"], "label": "G1 – 15 – Girard",     "color": "#14b8a6", "gtfs": "G1", "alert_ids": ["G1"]},
]

BUS_ROUTE_NUMBERS = [
    '1','2','3','4','5','6','7','9','10','12','14','16','17','18','19',
    '20','21','22','23','24','25','26','27','28','29','30','31','32','33',
    '35','37','38','39','40','42','43','44','45','46','47','48','49','50',
    '52','53','54','55','56','57','58','59','60','61','62','63','64','65',
    '66','67','68','70','73','75','77','78','79','80','84','88','89','90',
    '91','92','93','94','95','96','97','98','99','103','104','105','106',
    '107','108','109','110','111','112','113','114','115','116','117','118',
    '119','120','123','124','125','126','127','128','129','130','131','132',
    '133','150','201','204','206','310',
]

BUS_ROUTES = [
    {"id": n, "label": f"Route {n}", "color": "#78818c", "gtfs": n, "alert_ids": [n]}
    for n in BUS_ROUTE_NUMBERS
]

MODES = {
    "SUBWAY":  {"type": "transit", "routes": SUBWAY_LINES},
    "TROLLEY": {"type": "transit", "routes": TROLLEY_LINES},
    "BUS":     {"type": "transit", "routes": BUS_ROUTES},
    "RAIL":    {"type": "rail",    "routes": RAIL_LINES},
}
