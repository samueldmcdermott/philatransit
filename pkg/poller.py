"""Background SEPTA data poller and in-memory caches.

All SEPTA transitview/trainview data is fetched here on a 15 s cycle.
Client requests are served from these caches — SEPTA sees a fixed call
rate regardless of how many users are active.
"""

import threading
import time

import requests as req

from .helpers import SEPTA, SEPTA_V2, HEADERS
from .shapes import load_shapes
from .trip import trip_manager
from .tunnel import process_tunnel_ghosts

POLL_INTERVAL = 5  # seconds (matches SEPTA website default)

# transit cache: route_id -> list of vehicle dicts (from TransitViewAll)
transit_cache = {"routes": {}, "ts": 0.0}
transit_lock = threading.Lock()

# trainview cache: list of train dicts
trainview_cache = {"data": [], "ts": 0.0}
trainview_lock = threading.Lock()

# per-trip predictions cache (used by stop-predictions endpoint)
_trips_cache = {"data": {}, "ts": 0, "routes": set()}
_trips_lock = threading.Lock()
_trip_detail_cache = {}

# alerts cache
alerts_cache = {"data": [], "ts": 0}


def _poll_loop():
    """Background thread: refresh SEPTA transit + train data every POLL_INTERVAL seconds."""
    while True:
        # -- TransitViewAll (buses, trolleys, subway) --
        try:
            r = req.get(f"{SEPTA}/TransitViewAll/index.php", headers=HEADERS, timeout=15)
            by_route = {}
            for group in r.json().get("routes", []):
                for route_id, vehicles in group.items():
                    by_route[route_id] = vehicles

            # Enrich vehicles with Trip-based direction, heading, and stop info
            try:
                trip_manager.enrich_vehicles(by_route)
            except Exception as e:
                print(f"  [trip] enrichment error: {e}")

            with transit_lock:
                transit_cache["routes"] = by_route
                transit_cache["ts"] = time.time()
        except Exception as e:
            print(f"  [poller] TransitViewAll error: {e}")

        # -- Tunnel ghost tracking --
        try:
            with transit_lock:
                routes_snap = dict(transit_cache["routes"])
            process_tunnel_ghosts(routes_snap)
        except Exception as e:
            print(f"  [tunnel] error: {e}")

        # -- TrainView (regional rail) --
        try:
            r = req.get(f"{SEPTA}/TrainView/index.php", headers=HEADERS, timeout=12)
            with trainview_lock:
                trainview_cache["data"] = r.json()
                trainview_cache["ts"] = time.time()
        except Exception as e:
            print(f"  [poller] TrainView error: {e}")

        time.sleep(POLL_INTERVAL)


def start_poller():
    """Launch the background SEPTA data poller thread."""
    load_shapes()
    t = threading.Thread(target=_poll_loop, daemon=True, name="SeptaPoller")
    t.start()
    print("  [poller] SEPTA poller started")


# ── Prediction helpers (used by stop-predictions route) ───────────────

def is_gps_tracked(trip):
    """Return True if the trip has real GPS tracking (not a scheduled ghost)."""
    return (trip.get("vehicle_id") not in (None, "None", "")
            and trip.get("delay") != 998
            and trip.get("status") != "NO GPS")


def fetch_route_trips(route_ids):
    """Fetch live trips for given routes, cached 15s."""
    now = time.time()
    route_set = set(route_ids)
    with _trips_lock:
        if (now - _trips_cache["ts"]) < 15 and route_set <= _trips_cache["routes"]:
            return _trips_cache["data"]

    result = {}
    for rid in route_ids:
        try:
            r = req.get(f"{SEPTA_V2}/trips/", params={"route_id": rid},
                        headers=HEADERS, timeout=10)
            result[rid] = r.json() if r.status_code == 200 else []
        except Exception as e:
            print(f"  [v2] trips error for {rid}: {e}")
            result[rid] = []

    with _trips_lock:
        _trips_cache["data"] = result
        _trips_cache["routes"] = route_set
        _trips_cache["ts"] = time.time()
    return result


def fetch_trip_detail(trip_id):
    """Fetch per-stop scheduled + real-time data for a trip, cached 15s."""
    now = time.time()
    cached = _trip_detail_cache.get(trip_id)
    if cached and (now - cached["ts"]) < 15:
        return cached["data"]

    try:
        r = req.get(f"{SEPTA_V2}/trip-update/", params={"trip_id": trip_id},
                    headers=HEADERS, timeout=10)
        if r.status_code != 200:
            return {}
        data = r.json()
        _trip_detail_cache[trip_id] = {"data": data, "ts": now}
        return data
    except Exception as e:
        print(f"  [v2] trip-update error for {trip_id}: {e}")
        return {}


def fetch_alerts():
    """Fetch SEPTA alerts, cached 60s."""
    now = time.time()
    if (now - alerts_cache["ts"]) < 60:
        return alerts_cache["data"]
    try:
        r = req.get(f"{SEPTA_V2}/alerts/", headers=HEADERS, timeout=12)
        data = r.json() if r.status_code == 200 else []
    except Exception as e:
        print(f"  [v2] alerts error: {e}")
        data = alerts_cache["data"]  # fallback to stale cache
    alerts_cache["data"] = data
    alerts_cache["ts"] = time.time()
    return data
