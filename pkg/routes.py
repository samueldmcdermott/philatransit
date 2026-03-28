"""Flask blueprint with provider-agnostic API routes."""

import time
from datetime import datetime

from flask import Blueprint, Response, current_app, jsonify, request

from .helpers import TRIPS, SCHED, DAILY_CDFS, load, dump
from .poller import transit_lock, transit_cache, rail_lock, rail_cache
from .version import get_version

api = Blueprint("api", __name__)


# ── Helpers ──────────────────────────────────────────────────

def _provider():
    return current_app.config['provider']

def _tracker():
    return current_app.config['tracker']


# ── Version ──────────────────────────────────────────────────

@api.route("/api/version")
def version():
    return jsonify(version=get_version())


# ── Config ───────────────────────────────────────────────────

@api.route("/api/config")
def config():
    """Return route configuration for the frontend."""
    return jsonify(_provider().get_route_config())


# ── Vehicles (Trip-centric) ──────────────────────────────────

@api.route("/api/vehicles")
def vehicles():
    """Return trips for a route from the transit cache."""
    route = request.args.get("route", "")
    with transit_lock:
        trips = list(transit_cache["routes"].get(route, []))
        ts = transit_cache["ts"]
    return jsonify({"trips": trips, "timestamp": ts})


@api.route("/api/vehicles/rail")
def vehicles_rail():
    """Return rail trips from the rail cache."""
    route = request.args.get("route", "")
    with rail_lock:
        all_trains = list(rail_cache["data"])
        ts = rail_cache["ts"]
    if route:
        all_trains = [t for t in all_trains if t.get("route_id") == route]
    return jsonify({"trips": all_trains, "timestamp": ts})


# ── Stops ────────────────────────────────────────────────────

@api.route("/api/stops")
def stops():
    route = request.args.get("route", "")
    data = _provider().fetch_stops(route)
    return jsonify(data)


# ── Stop predictions ─────────────────────────────────────────

@api.route("/api/stop-predictions")
def stop_predictions():
    stop_ids = set(s.strip() for s in request.args.get("stops", "").split(",") if s.strip())
    route_ids = [s.strip() for s in request.args.get("routes", "").split(",") if s.strip()]

    if not stop_ids or not route_ids:
        return jsonify(error="missing stops or routes param"), 400

    result = _provider().fetch_stop_predictions(stop_ids, route_ids)
    return jsonify(result)


# ── Alerts ───────────────────────────────────────────────────

@api.route("/api/alerts")
def alerts():
    return jsonify(_provider().fetch_alerts())


# ── Tunnel ghosts ────────────────────────────────────────────

@api.route("/api/ghosts")
def get_ghosts():
    detector = _provider().get_tunnel_detector()
    if detector:
        return jsonify({
            'ghosts': detector.get_ghosts(),
            'lingering': detector.get_lingering(),
        })
    return jsonify({'ghosts': [], 'lingering': {}})


# ── Stats ────────────────────────────────────────────────────

@api.route("/api/stats")
def get_stats():
    return jsonify(load(TRIPS))


@api.route("/api/stats/cdfs")
def get_cdfs():
    """Return CDF data: historical summaries + today's live trips."""
    from .core.tracker import _CUTOFF_DATE, _CUTOFF_MS

    cdfs = load(DAILY_CDFS)
    trips = load(TRIPS)

    # Remove historical CDF entries before cutoff
    for route in list(cdfs.keys()):
        for day in list(cdfs[route].keys()):
            if day < _CUTOFF_DATE:
                del cdfs[route][day]

    # Compute today's minutes from live trip data
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
            headers={"Content-Disposition": "attachment; filename=trips.csv"},
        )

    return jsonify(trips)


# ── Scheduled config ─────────────────────────────────────────

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


# ── Tracker control ──────────────────────────────────────────

@api.route("/api/tracker/status")
def tracker_status():
    t = _tracker()
    return jsonify(running=t.running, tracked=t.registry_size)


@api.route("/api/tracker/start", methods=["POST"])
def tracker_start():
    t = _tracker()
    t.start()
    return jsonify(ok=True, running=True)


@api.route("/api/tracker/stop", methods=["POST"])
def tracker_stop():
    t = _tracker()
    t.stop()
    return jsonify(ok=True, running=False)
