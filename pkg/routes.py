"""Flask blueprint with all API routes."""

import time
from datetime import datetime

from flask import Blueprint, Response, jsonify, request
import requests as req

from .helpers import SEPTA, HEADERS, TRIPS, SCHED, DAILY_CDFS, load, dump
from .poller import (
    transit_lock, transit_cache, trainview_lock, trainview_cache,
    is_gps_tracked, fetch_route_trips, fetch_trip_detail, fetch_alerts,
)
from .tunnel import get_ghost_list, get_lingering_vids
from .tracker import tracker
from .version import get_version

api = Blueprint("api", __name__)

# ── Version ──────────────────────────────────────────────────

@api.route("/api/version")
def version():
    return jsonify(version=get_version())


# ── SEPTA proxy endpoints (served from cache) ────────────────

@api.route("/api/septa/trainview")
def trainview():
    with trainview_lock:
        data = list(trainview_cache["data"])
    if not data:
        # Cache cold — fallback to live fetch once
        try:
            r = req.get(f"{SEPTA}/TrainView/index.php", headers=HEADERS, timeout=12)
            data = r.json()
        except Exception as e:
            return jsonify(error=str(e)), 502
    return jsonify(data)


@api.route("/api/septa/transitview")
def transitview():
    route = request.args.get("route", "")
    with transit_lock:
        vehicles = list(transit_cache["routes"].get(route, []))
        ts = transit_cache["ts"]
    if not vehicles and ts == 0.0:
        # Cache cold — fallback to live fetch once
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
    return jsonify({"bus": vehicles})


@api.route("/api/septa/stops")
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


# ── Stop predictions (SEPTA v2 API) ──────────────────────────

@api.route("/api/septa/stop-predictions")
def stop_predictions():
    """Return per-stop arrival predictions from SEPTA v2 API."""
    stop_ids = set(s.strip() for s in request.args.get("stops", "").split(",") if s.strip())
    route_ids = [s.strip() for s in request.args.get("routes", "").split(",") if s.strip()]

    if not stop_ids or not route_ids:
        return jsonify(error="missing stops or routes param"), 400

    now = int(time.time())
    trips_by_route = fetch_route_trips(route_ids)

    # Collect GPS-tracked trips
    real_trips = []
    for rid, trips in trips_by_route.items():
        for t in trips:
            if is_gps_tracked(t):
                real_trips.append({
                    "trip_id": str(t["trip_id"]),
                    "vehicle": str(t.get("vehicle_id", "")),
                    "route": rid,
                    "delay": t.get("delay", 0),
                    "status": t.get("status", ""),
                })

    result = {sid: [] for sid in stop_ids}

    for trip_info in real_trips:
        detail = fetch_trip_detail(trip_info["trip_id"])
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

    # Sort by arrival time
    for sid in result:
        result[sid].sort(key=lambda x: x["arrival"])

    return jsonify(result)


# ── Alerts ────────────────────────────────────────────────────

@api.route("/api/septa/alerts")
def alerts():
    return jsonify(fetch_alerts())


# ── Tunnel ghosts ─────────────────────────────────────────────

@api.route("/api/ghosts")
def get_ghosts():
    return jsonify({
        'ghosts': get_ghost_list(),
        'lingering': get_lingering_vids(),
    })


# ── Stats ─────────────────────────────────────────────────────

@api.route("/api/stats")
def get_stats():
    return jsonify(load(TRIPS))


@api.route("/api/stats/cdfs")
def get_cdfs():
    """Return CDF data: historical summaries + today's live trips.

    Response: {route: {date: [sorted minutes-since-midnight], ...}, ...}
    Historical days come from daily_cdfs.json; today is computed live from trips.json.
    """
    from .tracker import _CUTOFF_DATE, _CUTOFF_MS

    cdfs = load(DAILY_CDFS)
    trips = load(TRIPS)
    today = datetime.now().strftime("%Y-%m-%d")

    # Remove any historical CDF entries before cutoff
    for route in list(cdfs.keys()):
        for day in list(cdfs[route].keys()):
            if day < _CUTOFF_DATE:
                del cdfs[route][day]

    # Compute today's minutes from live trip data (applying cutoff)
    for route, days in trips.items():
        for day_str, day_trips in days.items():
            if day_str < _CUTOFF_DATE:
                continue
            mins = []
            for t in day_trips:
                ts = t.get("start") or t.get("end")
                if not ts:
                    continue
                if day_str == _CUTOFF_DATE and ts < _CUTOFF_MS:
                    continue
                dt = datetime.fromtimestamp(ts / 1000)
                m = dt.hour * 60 + dt.minute + dt.second / 60
                mins.append(round(m, 2))
            mins.sort()
            if mins:
                cdfs.setdefault(route, {})[day_str] = mins

    return jsonify(cdfs)


@api.route("/api/stats/record", methods=["POST"])
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


@api.route("/api/stats/clear", methods=["POST"])
def clear_stats():
    dump(TRIPS, {})
    return jsonify(ok=True)


@api.route("/api/stats/export")
def export_stats():
    fmt   = request.args.get("format", "json").lower()
    trips = load(TRIPS)

    if fmt == "csv":
        route_filters = set(r for r in request.args.getlist("route") if r.strip()) or None
        rows = ["route,date,timestamp_ms,time_of_day"]
        for route in sorted(trips):
            if route_filters and route not in route_filters:
                continue
            for day in sorted(trips[route]):
                for trip in sorted(trips[route][day], key=lambda x: x.get("start", 0)):
                    ts = trip.get("start") or trip.get("end") or 0
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


# ── Scheduled config ──────────────────────────────────────────

@api.route("/api/scheduled")
def get_scheduled():
    return jsonify(load(SCHED))


@api.route("/api/scheduled", methods=["POST"])
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


# ── Tracker control ───────────────────────────────────────────

@api.route("/api/tracker/status")
def tracker_status():
    return jsonify(running=tracker.running, tracked=tracker.registry_size)


@api.route("/api/tracker/start", methods=["POST"])
def tracker_start():
    tracker.start()
    return jsonify(ok=True, running=True)


@api.route("/api/tracker/stop", methods=["POST"])
def tracker_stop():
    tracker.stop()
    return jsonify(ok=True, running=False)
