"""SEPTA transit data provider.

Implements the Provider ABC for Southeastern Pennsylvania Transportation
Authority APIs.  All SEPTA-specific API calls, response parsing, and
vehicle identification happen here.
"""

from __future__ import annotations

import time
import threading
from datetime import datetime

import requests as req

from ..base import Provider
from .constants import (
    SEPTA_API, SEPTA_V2, HEADERS, MODES,
    TUNNEL_ROUTES, rail_line_key,
)
from .vehicle_id import extract_vehicle_id
from .tunnel import SeptaTunnelDetector
from .detour import SeptaDetourDetector


class SeptaProvider(Provider):
    """SEPTA implementation of the Provider interface."""

    def __init__(self):
        self._tunnel_detector = SeptaTunnelDetector()
        self._detour_detector = SeptaDetourDetector()

        # Caches for predictions and alerts
        self._trips_cache = {"data": {}, "ts": 0, "routes": set()}
        self._trips_lock = threading.Lock()
        self._trip_detail_cache = {}
        self._alerts_cache = {"data": [], "ts": 0}

    # ── Transit polling ──────────────────────────────────────────

    def poll_transit(self) -> dict[str, list[dict]]:
        """Fetch all transit vehicles from TransitViewAll."""
        r = req.get(
            f"{SEPTA_API}/TransitViewAll/index.php",
            headers=HEADERS, timeout=15,
        )
        by_route = {}
        for group in r.json().get("routes", []):
            for route_id, vehicles in group.items():
                normalized = []
                for v in vehicles:
                    nv = self._normalize_transit(v, route_id)
                    if nv:
                        normalized.append(nv)
                if normalized:
                    by_route[route_id] = normalized
        return by_route

    def _normalize_transit(self, v: dict, route_id: str) -> dict | None:
        """Normalize a raw SEPTA transit vehicle dict."""
        # Filter out schedule-based and invalid vehicles
        late = v.get('late', 0)
        if late == 998:
            return None
        label = v.get('label', '')
        if label in ('None', None, '', '0'):
            return None
        vid_str = v.get('VehicleID')
        if vid_str and 'schedBased' in str(vid_str):
            return None

        vehicle_id = extract_vehicle_id(v)
        if not vehicle_id:
            return None

        try:
            lat = float(v.get('lat', 0))
            lng = float(v.get('lng', 0))
        except (TypeError, ValueError):
            return None
        if lat == 0 or lng == 0:
            return None

        return {
            'vehicle_id': vehicle_id,
            'route_id': route_id,
            'lat': lat,
            'lng': lng,
            'label': str(label),
            'meta': {
                'delay': int(late) if late is not None else 0,
                'headsign': v.get('destination') or v.get('dest') or '',
                'api_bearing': v.get('heading'),
                'api_trip_id': str(v.get('trip') or ''),
                'api_vehicle_id': str(v.get('VehicleID') or ''),
            },
        }

    # ── Rail polling ─────────────────────────────────────────────

    def poll_rail(self) -> list[dict]:
        """Fetch all regional rail vehicles from TrainView."""
        r = req.get(
            f"{SEPTA_API}/TrainView/index.php",
            headers=HEADERS, timeout=12,
        )
        result = []
        for t in r.json():
            nv = self._normalize_rail(t)
            if nv:
                result.append(nv)
        return result

    def _normalize_rail(self, t: dict) -> dict | None:
        """Normalize a raw SEPTA TrainView dict."""
        train_no = str(t.get('trainno', ''))
        if not train_no:
            return None

        route_id = rail_line_key(
            t.get('line', ''), t.get('dest', ''), t.get('SOURCE', ''),
        )

        try:
            lat = float(t.get('lat', 0))
            lng = float(t.get('lon', 0))
        except (TypeError, ValueError):
            return None

        late = t.get('late')
        return {
            'vehicle_id': train_no,
            'route_id': route_id,
            'lat': lat,
            'lng': lng,
            'label': train_no,
            'meta': {
                'delay': int(late) if late is not None else 0,
                'headsign': t.get('dest', ''),
                'api_bearing': t.get('heading'),
                'api_trip_id': train_no,
                'source': t.get('SOURCE', ''),
                'service': t.get('service', ''),
            },
        }

    # ── Stops ────────────────────────────────────────────────────

    def fetch_stops(self, route_id: str) -> list[dict]:
        """Fetch stops for a route from SEPTA Stops API."""
        try:
            r = req.get(
                f"{SEPTA_API}/Stops/index.php",
                params={"req1": route_id},
                headers=HEADERS, timeout=12,
            )
            data = r.json() if r.status_code == 200 else []
            return data if isinstance(data, list) else []
        except Exception:
            return []

    # ── Alerts ───────────────────────────────────────────────────

    def fetch_alerts(self) -> list[dict]:
        """Fetch SEPTA alerts, cached 60s."""
        now = time.time()
        if (now - self._alerts_cache["ts"]) < 60:
            return self._alerts_cache["data"]
        try:
            r = req.get(f"{SEPTA_V2}/alerts/", headers=HEADERS, timeout=12)
            data = r.json() if r.status_code == 200 else []
        except Exception as e:
            print(f"  [septa] alerts error: {e}")
            data = self._alerts_cache["data"]
        self._alerts_cache["data"] = data
        self._alerts_cache["ts"] = time.time()
        return data

    # ── Stop predictions ─────────────────────────────────────────

    def fetch_stop_predictions(self, stop_ids: set[str],
                               route_ids: list[str]) -> dict:
        """Fetch per-stop arrival predictions from SEPTA v2 API."""
        now = int(time.time())
        trips_by_route = self._fetch_route_trips(route_ids)

        # Collect GPS-tracked trips
        real_trips = []
        for rid, trips in trips_by_route.items():
            for t in trips:
                if self._is_gps_tracked(t):
                    real_trips.append({
                        "trip_id": str(t["trip_id"]),
                        "vehicle": str(t.get("vehicle_id", "")),
                        "route": rid,
                        "delay": t.get("delay", 0),
                        "status": t.get("status", ""),
                    })

        result = {sid: [] for sid in stop_ids}

        for trip_info in real_trips:
            detail = self._fetch_trip_detail(trip_info["trip_id"])
            stop_times = detail.get("stop_times", [])
            for st in stop_times:
                sid = str(st.get("stop_id", ""))
                if sid not in stop_ids:
                    continue
                if st.get("departed"):
                    continue

                eta = st.get("eta", 0)
                if not eta or eta < now - 120:
                    continue

                sched_ts = None
                sched_str = st.get("scheduled_time", "")
                if sched_str:
                    try:
                        sched_dt = datetime.strptime(sched_str, "%Y-%m-%d %I:%M %p")
                        sched_ts = int(sched_dt.timestamp())
                    except ValueError:
                        pass

                result[sid].append({
                    "trip": trip_info["trip_id"],
                    "vehicle": trip_info["vehicle"],
                    "route": trip_info["route"],
                    "arrival": eta,
                    "minutes": round((eta - now) / 60, 1),
                    "scheduled": sched_ts,
                    "sched_minutes": round((sched_ts - now) / 60, 1) if sched_ts else None,
                    "delay": trip_info["delay"],
                    "status": trip_info["status"],
                })

        for sid in result:
            result[sid].sort(key=lambda x: x["arrival"])

        return result

    def _is_gps_tracked(self, trip):
        return (trip.get("vehicle_id") not in (None, "None", "")
                and trip.get("delay") != 998
                and trip.get("status") != "NO GPS")

    def _fetch_route_trips(self, route_ids):
        now = time.time()
        route_set = set(route_ids)
        with self._trips_lock:
            if (now - self._trips_cache["ts"]) < 15 and route_set <= self._trips_cache["routes"]:
                return self._trips_cache["data"]

        result = {}
        for rid in route_ids:
            try:
                r = req.get(f"{SEPTA_V2}/trips/", params={"route_id": rid},
                            headers=HEADERS, timeout=10)
                result[rid] = r.json() if r.status_code == 200 else []
            except Exception as e:
                print(f"  [septa] trips error for {rid}: {e}")
                result[rid] = []

        with self._trips_lock:
            self._trips_cache["data"] = result
            self._trips_cache["routes"] = route_set
            self._trips_cache["ts"] = time.time()
        return result

    def _fetch_trip_detail(self, trip_id):
        now = time.time()
        cached = self._trip_detail_cache.get(trip_id)
        if cached and (now - cached["ts"]) < 15:
            return cached["data"]

        try:
            r = req.get(f"{SEPTA_V2}/trip-update/", params={"trip_id": trip_id},
                        headers=HEADERS, timeout=10)
            if r.status_code != 200:
                return {}
            data = r.json()
            self._trip_detail_cache[trip_id] = {"data": data, "ts": now}
            return data
        except Exception as e:
            print(f"  [septa] trip-update error for {trip_id}: {e}")
            return {}

    # ── Tunnel detector ──────────────────────────────────────────

    def get_tunnel_detector(self):
        return self._tunnel_detector

    def get_detour_detector(self):
        return self._detour_detector

    # ── Route config ─────────────────────────────────────────────

    def get_route_config(self) -> dict:
        """Return SEPTA route configuration for frontend and TripManager."""
        return {
            "modes": MODES,
            "provider": "SEPTA",
            "tunnel_routes": list(TUNNEL_ROUTES),
        }
