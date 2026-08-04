"""Flask blueprint with provider-agnostic API routes."""

import time

from flask import Blueprint, Response, current_app, jsonify, request

from .helpers import DAILY_CDFS, load, date_str
from .poller import transit_lock, transit_cache, rail_lock, rail_cache
from .core.stats import CUTOFF_DATE, today_minutes, today_snapshot
from .version import get_version

api = Blueprint("api", __name__)


# ── Helpers ──────────────────────────────────────────────────

def _provider():
    return current_app.config['provider']

def _tunnel_monitor():
    return current_app.config['tunnel_monitor']


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
    stop_ids = {s.strip() for s in request.args.get("stops", "").split(",") if s.strip()}
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


# ── Tunnel monitoring ────────────────────────────────────────

@api.route("/api/monitoring")
def monitoring():
    """Return tunnel monitoring data (rolling averages)."""
    route = request.args.get("route")
    m = _tunnel_monitor()
    if route:
        return jsonify({'tunnel': m.get_tunnel_avg(route),
                        'timestamp': time.time()})
    return jsonify(m.get_snapshot())


# ── Stats ────────────────────────────────────────────────────

@api.route("/api/stats")
def get_stats():
    return jsonify(today_snapshot())


@api.route("/api/stats/cdfs")
def get_cdfs():
    """Return {route: {day: [sorted start-minutes]}} — history + today merged.

    Anomalous completed trips are excluded by default; pass ?include_invalid=1
    to include them (today's bucket only — daily_cdfs.json never contains them).
    """
    include_invalid = request.args.get("include_invalid") in ("1", "true", "yes")
    cdfs = load(DAILY_CDFS)
    for route in list(cdfs.keys()):
        for day in list(cdfs[route].keys()):
            if day < CUTOFF_DATE:
                del cdfs[route][day]

    today_str = date_str()
    for route, days in today_minutes(valid_only=not include_invalid).items():
        if today_str in days:
            cdfs.setdefault(route, {})[today_str] = days[today_str]

    return jsonify(cdfs)


@api.route("/api/stats/export")
def export_stats():
    fmt = request.args.get("format", "json").lower()
    trips = today_snapshot()

    if fmt == "csv":
        route_filters = {r for r in request.args.getlist("route") if r.strip()} or None
        flat = today_minutes(trips)
        rows = ["route,date,time_of_day,minutes"]
        for route in sorted(flat):
            if route_filters and route not in route_filters:
                continue
            safe = route.replace('"', '""')
            for day in sorted(flat[route]):
                for mins in flat[route][day]:
                    h, m = int(mins // 60), int(mins % 60)
                    s = int(round((mins - int(mins)) * 60))
                    rows.append(f'"{safe}",{day},{h:02d}:{m:02d}:{s:02d},{mins}')
        return Response(
            "\n".join(rows),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=trips.csv"},
        )

    return jsonify(trips)
