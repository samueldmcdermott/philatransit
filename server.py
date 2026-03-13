#!/usr/bin/env python3
"""
SEPTA Live – local proxy server + statistics store.

Usage:
    pip install flask requests
    python3 server.py          # → http://localhost:5000
    python3 server.py --port 8080
"""

import json
import sys
import argparse
import threading
import time
from datetime import datetime
from pathlib import Path

try:
    from flask import Flask, Response, jsonify, request, send_file, send_from_directory
    import requests as req
except ImportError:
    print("\n  Missing dependencies. Run:\n\n    pip install flask requests\n")
    sys.exit(1)


# ── paths ────────────────────────────────────────────────────
BASE   = Path(__file__).parent
DATA   = BASE / "data"
TRIPS  = DATA / "trips.json"
SCHED  = DATA / "scheduled.json"

DATA.mkdir(exist_ok=True)

SEPTA   = "https://www3.septa.org/api"
HEADERS = {"User-Agent": "SEPTA-Live/1.0"}

app = Flask(__name__)

# ── json helpers ─────────────────────────────────────────────
def load(path, default=None):
    default = {} if default is None else default
    try:
        return json.loads(path.read_text()) if path.exists() else default
    except Exception:
        return default

def dump(path, obj):
    path.write_text(json.dumps(obj, indent=2))

# ── frontend ─────────────────────────────────────────────────
@app.route("/")
def index():
    return send_file(BASE / "public" / "index.html")

@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(BASE / "static", filename)

@app.route("/src/<path:filename>")
def src_files(filename):
    return send_from_directory(BASE / "src", filename)

# ── SEPTA proxy ──────────────────────────────────────────────
@app.route("/api/septa/trainview")
def trainview():
    try:
        r = req.get(f"{SEPTA}/TrainView/index.php", headers=HEADERS, timeout=12)
        return Response(r.content, mimetype="application/json")
    except Exception as e:
        return jsonify(error=str(e)), 502

@app.route("/api/septa/transitview")
def transitview():
    route = request.args.get("route", "")
    try:
        r = req.get(
            f"{SEPTA}/TransitView/index.php",
            params={"route": route},
            headers=HEADERS,
            timeout=12,
        )
        return Response(r.content, mimetype="application/json")
    except Exception as e:
        return jsonify(error=str(e)), 502

@app.route("/api/septa/stops")
def stops():
    route = request.args.get("route", "")
    try:
        r = req.get(
            f"{SEPTA}/Stops/index.php",
            params={"req1": route},
            headers=HEADERS,
            timeout=12,
        )
        return Response(r.content, mimetype="application/json")
    except Exception as e:
        return jsonify(error=str(e)), 502

# ── SEPTA v2 API: real-time stop predictions ─────────────────

SEPTA_V2 = "https://www3.septa.org/api/v2"
_trips_cache = {"data": {}, "ts": 0, "routes": set()}  # route → [trip objects]
_trips_lock = threading.Lock()
_trip_detail_cache = {}   # trip_id → {stop_times, ts}

def _is_gps_tracked(trip):
    """Return True if the trip has real GPS tracking (not a scheduled ghost)."""
    return (trip.get("vehicle_id") not in (None, "None", "")
            and trip.get("delay") != 998
            and trip.get("status") != "NO GPS")


def _fetch_route_trips(route_ids):
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


def _fetch_trip_detail(trip_id):
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


@app.route("/api/septa/stop-predictions")
def stop_predictions():
    """Return per-stop arrival predictions from SEPTA v2 API.

    Fetches live GPS-tracked trips, then uses trip-update to get
    per-stop scheduled times and real-time ETAs. Only returns
    predictions for stops in the requested set.

    Query params:
      stops  – comma-separated stop IDs (e.g. "20645,20646")
      routes – comma-separated route IDs (e.g. "T2,T3")
    """
    stop_ids = set(s.strip() for s in request.args.get("stops", "").split(",") if s.strip())
    route_ids = [s.strip() for s in request.args.get("routes", "").split(",") if s.strip()]

    if not stop_ids or not route_ids:
        return jsonify(error="missing stops or routes param"), 400

    now = int(time.time())
    trips_by_route = _fetch_route_trips(route_ids)

    # Collect GPS-tracked trips
    real_trips = []
    for rid, trips in trips_by_route.items():
        for t in trips:
            if _is_gps_tracked(t):
                real_trips.append({
                    "trip_id": str(t["trip_id"]),
                    "vehicle": str(t.get("vehicle_id", "")),
                    "route": rid,
                    "delay": t.get("delay", 0),
                    "status": t.get("status", ""),
                })

    result = {sid: [] for sid in stop_ids}

    for trip_info in real_trips:
        detail = _fetch_trip_detail(trip_info["trip_id"])
        stop_times = detail.get("stop_times", [])
        for st in stop_times:
            sid = str(st.get("stop_id", ""))
            if sid not in stop_ids:
                continue
            if st.get("departed"):
                continue

            # Use ETA from trip-update (real-time adjusted)
            eta = st.get("eta", 0)
            if not eta or eta < now - 120:
                continue

            # Parse scheduled_time for the scheduled arrival
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

    # Sort by arrival time
    for sid in result:
        result[sid].sort(key=lambda x: x["arrival"])

    return jsonify(result)


# ── SEPTA alerts ──────────────────────────────────────────────
_alerts_cache = {"data": [], "ts": 0}

@app.route("/api/septa/alerts")
def alerts():
    now = time.time()
    if (now - _alerts_cache["ts"]) < 60:
        return jsonify(_alerts_cache["data"])
    try:
        r = req.get(f"{SEPTA_V2}/alerts/", headers=HEADERS, timeout=12)
        data = r.json() if r.status_code == 200 else []
    except Exception as e:
        print(f"  [v2] alerts error: {e}")
        data = _alerts_cache["data"]  # fallback to stale cache
    _alerts_cache["data"] = data
    _alerts_cache["ts"] = time.time()
    return jsonify(data)

# ── stats: read ───────────────────────────────────────────────
@app.route("/api/stats")
def get_stats():
    return jsonify(load(TRIPS))

# ── stats: record a single trip completion ────────────────────
@app.route("/api/stats/record", methods=["POST"])
def record_trip():
    body  = request.get_json(force=True, silent=True) or {}
    route = str(body.get("route", "")).strip()
    start = int(body.get("start", body.get("timestamp", datetime.now().timestamp() * 1000)))
    end   = int(body.get("end", start))

    if not route:
        return jsonify(error="missing route"), 400

    trips = load(TRIPS)
    day   = datetime.fromtimestamp(start / 1000).strftime("%Y-%m-%d")
    trips.setdefault(route, {}).setdefault(day, []).append(
        {"start": start, "end": end, "dur": end - start}
    )
    dump(TRIPS, trips)

    return jsonify(ok=True, route=route, day=day, count=len(trips[route][day]))

# ── stats: clear all ──────────────────────────────────────────
@app.route("/api/stats/clear", methods=["POST"])
def clear_stats():
    dump(TRIPS, {})
    return jsonify(ok=True)

# ── stats: export ─────────────────────────────────────────────
@app.route("/api/stats/export")
def export_stats():
    fmt   = request.args.get("format", "json").lower()
    trips = load(TRIPS)

    if fmt == "csv":
        rows = ["route,date,timestamp_ms,time_of_day"]
        for route in sorted(trips):
            for day in sorted(trips[route]):
                for ts in sorted(trips[route][day]):
                    t = datetime.fromtimestamp(ts / 1000).strftime("%H:%M:%S")
                    safe = route.replace('"', '""')
                    rows.append(f'"{safe}",{day},{ts},{t}')
        return Response(
            "\n".join(rows),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=septa_trips.csv"},
        )

    # default: JSON
    return jsonify(trips)

# ── background trip tracker ──────────────────────────────────

def rail_line_key(line, dest, src):
    """Map TrainView fields to a stable route key (mirrors JS railLineKey)."""
    aliases = {
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
    # Match line field first (most reliable), then dest, then src
    for field in [line, dest, src]:
        low = (field or "").lower()
        for route_id, keys in aliases.items():
            if any(k in low for k in keys):
                return route_id
    return line or "unknown"


class TripTracker:
    """
    Polls SEPTA every 30 s, tracking every vehicle across all routes.
    When a vehicle disappears after being seen in ≥ MIN_DWELL polls it
    is counted as a completed trip and appended to trips.json.
    """
    POLL_INTERVAL = 30
    MIN_DWELL     = 2

    def __init__(self):
        self._registry = {}   # vid → {"route": str, "seen": int, "first_ms": int}
        self._lock     = threading.Lock()
        self._thread   = None
        self.running   = False

    # ── public ───────────────────────────────────────────────
    def start(self):
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="TripTracker")
        self._thread.start()
        print("  [tracker] started")

    def stop(self):
        self.running = False
        print("  [tracker] stopped")

    @property
    def registry_size(self):
        with self._lock:
            return len(self._registry)

    # ── internal ─────────────────────────────────────────────
    def _loop(self):
        while self.running:
            try:
                self._poll()
            except Exception as e:
                print(f"  [tracker] poll error: {e}")
            # Sleep in 0.5 s ticks so stop() takes effect quickly
            for _ in range(self.POLL_INTERVAL * 2):
                if not self.running:
                    break
                time.sleep(0.5)

    def _poll(self):
        vehicles = {}   # vid → route_key

        # ── regional rail ──────────────────────────────────
        try:
            r = req.get(f"{SEPTA}/TrainView/index.php", headers=HEADERS, timeout=12)
            for t in r.json():
                vid   = str(t.get("trainno", ""))
                route = rail_line_key(
                    t.get("line", ""), t.get("dest", ""), t.get("SOURCE", "")
                )
                if vid:
                    vehicles[vid] = route
        except Exception as e:
            print(f"  [tracker] rail error: {e}")

        # ── bus / trolley / subway (TransitViewAll) ────────
        try:
            r = req.get(f"{SEPTA}/TransitViewAll/index.php", headers=HEADERS, timeout=15)
            for route_group in r.json().get("routes", []):
                for route_id, vs in route_group.items():
                    for v in vs:
                        if v.get("late") == 998:
                            continue
                        label = v.get("label", "")
                        if label in ("None", None, ""):
                            continue
                        vehicle_id = str(v.get("VehicleID") or "")
                        if "schedBased" in vehicle_id:
                            continue
                        vid = str(v.get("trip") or vehicle_id or "")
                        if vid and vid not in ("None", ""):
                            vehicles[vid] = route_id
        except Exception as e:
            print(f"  [tracker] transit error: {e}")

        now_ms = int(time.time() * 1000)

        with self._lock:
            cur_ids = set(vehicles.keys())
            trips   = load(TRIPS)
            changed = False

            # detect completions
            for vid, entry in self._registry.items():
                if vid not in cur_ids and entry["seen"] >= self.MIN_DWELL:
                    route    = entry["route"]
                    start_ms = entry["first_ms"]
                    day      = datetime.fromtimestamp(now_ms / 1000).strftime("%Y-%m-%d")
                    trips.setdefault(route, {}).setdefault(day, []).append(
                        {"start": start_ms, "end": now_ms, "dur": now_ms - start_ms}
                    )
                    changed = True

            if changed:
                dump(TRIPS, trips)

            # update registry
            new_reg = {}
            for vid, route in vehicles.items():
                ex = self._registry.get(vid)
                new_reg[vid] = {
                    "route":    route,
                    "seen":     (ex["seen"] + 1) if ex else 1,
                    "first_ms": ex["first_ms"] if ex else now_ms,
                }
            self._registry = new_reg


tracker = TripTracker()


# ── tracker control endpoints ─────────────────────────────────

@app.route("/api/tracker/status")
def tracker_status():
    return jsonify(running=tracker.running, tracked=tracker.registry_size)

@app.route("/api/tracker/start", methods=["POST"])
def tracker_start():
    tracker.start()
    return jsonify(ok=True, running=True)

@app.route("/api/tracker/stop", methods=["POST"])
def tracker_stop():
    tracker.stop()
    return jsonify(ok=True, running=False)

# ── scheduled config ──────────────────────────────────────────
@app.route("/api/scheduled")
def get_scheduled():
    return jsonify(load(SCHED))

@app.route("/api/scheduled", methods=["POST"])
def set_scheduled():
    body  = request.get_json(force=True, silent=True) or {}
    route = str(body.get("route", "")).strip()
    count = int(body.get("count", 0))

    if not route:
        return jsonify(error="missing route"), 400

    sched = load(SCHED)
    sched[route] = count
    dump(SCHED, sched)
    return jsonify(ok=True)

# ── entrypoint ───────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()

    print(f"\n  SEPTA Live  →  http://localhost:{args.port}\n")
    tracker.start()   # auto-start background tracker on server launch
    app.run(host="127.0.0.1", port=args.port, debug=False)
