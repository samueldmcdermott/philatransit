#!/usr/bin/env python3
"""
Build static GTFS data files for SEPTA Live.

Downloads the latest SEPTA GTFS ZIP and extracts:
  - static/stops.json      → {stop_id: {name, lat, lng}}
  - static/schedule.json   → {route_short_name: {weekday:[min,...], saturday:[...], sunday:[...]}}

Scheduled minutes are the first-stop departure times for each trip (minutes since midnight),
representing when each scheduled trip begins service.

Usage:
    pip install requests
    python3 scripts/build_gtfs.py
"""

import csv
import io
import json
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

try:
    import requests
except ImportError:
    print("Missing: pip install requests")
    sys.exit(1)

GTFS_URL = "https://github.com/septadev/GTFS/releases/latest/download/gtfs_public.zip"
OUT_DIR  = Path(__file__).parent.parent / "static"


def parse_time(s):
    """Parse HH:MM:SS → minutes since midnight (handles >24h wrap for overnight trips)."""
    parts = s.strip().split(":")
    if len(parts) != 3:
        return None
    try:
        h, m, sec = int(parts[0]), int(parts[1]), int(parts[2])
        return h * 60 + m + sec / 60
    except ValueError:
        return None


def read_csv(zf, filename):
    """Read a CSV file from a ZIP archive into a list of dicts.
    Merges rows from all matching files (e.g. google_bus/routes.txt + google_rail/routes.txt)."""
    names = zf.namelist()
    matches = [n for n in names if n.endswith(filename)]
    if not matches:
        raise FileNotFoundError(f"{filename} not found in ZIP. Available: {names[:20]}")
    rows = []
    for match in matches:
        with zf.open(match) as f:
            text = f.read().decode("utf-8-sig")
        rows.extend(csv.DictReader(io.StringIO(text)))
    return rows


def open_gtfs(content):
    """Open GTFS ZIP, handling SEPTA's nested structure (google_bus.zip + google_rail.zip)."""
    outer = zipfile.ZipFile(io.BytesIO(content))
    inner_names = [n for n in outer.namelist() if n.endswith(".zip")]
    if inner_names:
        # Nested ZIPs — merge all inner ZIPs into a single virtual ZIP
        merged = {}
        for inner_name in inner_names:
            prefix = inner_name.replace(".zip", "") + "/"
            with outer.open(inner_name) as f:
                inner = zipfile.ZipFile(io.BytesIO(f.read()))
                for name in inner.namelist():
                    merged[prefix + name] = inner.read(name)
                inner.close()
        outer.close()
        # Create a new in-memory ZIP with merged contents
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zout:
            for name, data in merged.items():
                zout.writestr(name, data)
        buf.seek(0)
        return zipfile.ZipFile(buf)
    return outer


def main():
    OUT_DIR.mkdir(exist_ok=True)

    print(f"Downloading GTFS from {GTFS_URL} …")
    r = requests.get(GTFS_URL, timeout=120)
    r.raise_for_status()
    print(f"  Downloaded {len(r.content) / 1024:.0f} KB")

    with open_gtfs(r.content) as zf:
        # ── stops.json ──────────────────────────────────────────────────
        print("Parsing stops.txt …")
        stops_rows = read_csv(zf, "stops.txt")
        stops = {}
        for row in stops_rows:
            sid = row.get("stop_id", "").strip()
            lat = row.get("stop_lat", "").strip()
            lng = row.get("stop_lon", "").strip()
            name = row.get("stop_name", "").strip()
            if sid and lat and lng:
                try:
                    stops[sid] = {"name": name, "lat": float(lat), "lng": float(lng)}
                except ValueError:
                    pass
        print(f"  {len(stops)} stops")

        # ── routes → short name ─────────────────────────────────────────
        print("Parsing routes.txt …")
        routes_rows = read_csv(zf, "routes.txt")
        route_short = {}  # route_id → route_short_name
        for row in routes_rows:
            rid   = row.get("route_id", "").strip()
            short = row.get("route_short_name", "").strip()
            if rid:
                route_short[rid] = short or rid

        # ── calendar → service day type ─────────────────────────────────
        # SEPTA defines many services entirely via calendar_dates.txt with an
        # all-zero calendar.txt row (or no calendar.txt row at all). Previously
        # these defaulted to "weekday", which caused Saturday/Sunday overnight
        # services to leak into the weekday schedule (notably OWL trips on
        # T1/T3/T5). Classify those services by the DOW distribution of their
        # exception dates instead.
        from datetime import date as _date
        print("Parsing calendar.txt …")
        cal_rows = read_csv(zf, "calendar.txt")
        service_daytype = {}  # service_id → "weekday" | "saturday" | "sunday"
        unclassified = set()  # services needing calendar_dates fallback
        for row in cal_rows:
            sid = row.get("service_id", "").strip()
            if not sid:
                continue
            mon = row.get("monday", "0").strip()
            sat = row.get("saturday", "0").strip()
            sun = row.get("sunday", "0").strip()
            if sun == "1":
                service_daytype[sid] = "sunday"
            elif sat == "1":
                service_daytype[sid] = "saturday"
            elif mon == "1":
                service_daytype[sid] = "weekday"
            else:
                unclassified.add(sid)  # all-zero → classify via calendar_dates

        print("Parsing calendar_dates.txt …")
        try:
            cdates_rows = read_csv(zf, "calendar_dates.txt")
        except FileNotFoundError:
            cdates_rows = []
        svc_dow_counts = defaultdict(lambda: [0, 0, 0])  # sid → [weekday, sat, sun]
        for row in cdates_rows:
            sid = row.get("service_id", "").strip()
            if row.get("exception_type", "").strip() != "1":
                continue  # only consider "added" dates
            ds = row.get("date", "").strip()
            if len(ds) != 8:
                continue
            try:
                d = _date(int(ds[:4]), int(ds[4:6]), int(ds[6:8]))
            except ValueError:
                continue
            w = d.weekday()
            bucket = svc_dow_counts[sid]
            if w == 5:   bucket[1] += 1
            elif w == 6: bucket[2] += 1
            else:        bucket[0] += 1

        # Classify services missing from calendar.txt or with all-zero rows.
        # Tie-breaking favors Sunday, then Saturday, then weekday — this keeps
        # holiday-overlay services (which run on weekend schedules but include
        # occasional holiday Mondays) grouped with the correct schedule.
        for sid in set(svc_dow_counts) | unclassified:
            if sid in service_daytype:
                continue
            wd, sa, su = svc_dow_counts.get(sid, [0, 0, 0])
            if su > 0 and su >= sa and su >= wd:
                service_daytype[sid] = "sunday"
            elif sa > 0 and sa >= wd:
                service_daytype[sid] = "saturday"
            else:
                service_daytype[sid] = "weekday"

        # ── trips → (route_short_name, day_type, keep?) ────────────────
        # Schedule counting must match the backend's round-trip-based
        # completion tracking (see pkg/core/tracker.py — one completion per
        # round trip), so we keep only one GTFS trip per physical vehicle
        # cycle by filtering to direction_id == "0". For routes that only
        # have direction_id == "1" in the feed (rare edge cases), fall back
        # to that direction.
        print("Parsing trips.txt …")
        trips_rows = read_csv(zf, "trips.txt")
        route_dirs = defaultdict(set)
        for row in trips_rows:
            rid = row.get("route_id", "").strip()
            d   = row.get("direction_id", "").strip()
            if rid:
                route_dirs[rid].add(d)
        primary_dir = {rid: ("0" if "0" in dirs else ("1" if "1" in dirs else ""))
                       for rid, dirs in route_dirs.items()}

        trip_info = {}  # trip_id → {short, daytype}
        for row in trips_rows:
            tid = row.get("trip_id", "").strip()
            rid = row.get("route_id", "").strip()
            sid = row.get("service_id", "").strip()
            d   = row.get("direction_id", "").strip()
            if d != primary_dir.get(rid, "0"):
                continue  # skip reverse-direction trips — same physical run
            short   = route_short.get(rid, rid)
            daytype = service_daytype.get(sid, "weekday")
            trip_info[tid] = {"short": short, "daytype": daytype}

        # ── stop_times → first departure per trip ───────────────────────
        print("Parsing stop_times.txt (may take a moment) …")
        st_rows = read_csv(zf, "stop_times.txt")

        # Find minimum stop_sequence departure time per trip
        trip_first_dep = {}  # trip_id → (min_stop_seq, departure_minutes)
        for row in st_rows:
            tid  = row.get("trip_id", "").strip()
            seq  = row.get("stop_sequence", "0").strip()
            dep  = row.get("departure_time", "").strip()
            if not tid or not dep:
                continue
            try:
                seq_int = int(seq)
            except ValueError:
                continue
            dep_min = parse_time(dep)
            if dep_min is None:
                continue
            if tid not in trip_first_dep or seq_int < trip_first_dep[tid][0]:
                trip_first_dep[tid] = (seq_int, dep_min)

        # ── build schedule.json ─────────────────────────────────────────
        print("Building schedule …")
        # SEPTA's GTFS sometimes emits multiple trip_ids for what is physically
        # one run (shape variants, trolley/bus alternates, etc.), all sharing
        # the same first-stop departure minute. Two real vehicles on the same
        # route don't leave the same stop at the same minute, so dedupe by
        # (route, day_type, departure_minute).
        schedule = defaultdict(lambda: {"weekday": set(), "saturday": set(), "sunday": set()})
        for tid, (_, dep_min) in trip_first_dep.items():
            info = trip_info.get(tid)
            if not info:
                continue
            short   = info["short"]
            daytype = info["daytype"]
            if dep_min < 1440:  # keep only 0–23:59
                schedule[short][daytype].add(dep_min)

        # Convert sets to sorted lists
        schedule = {short: {dt: sorted(mins) for dt, mins in days.items()}
                    for short, days in schedule.items()}

        # ── shapes.json ─────────────────────────────────────────────────────────
        print("Parsing shapes.txt …")
        try:
            shape_rows = read_csv(zf, "shapes.txt")
            # shapes.txt: shape_id, shape_pt_lat, shape_pt_lon, shape_pt_sequence

            # Build shape_id → sorted list of (sequence, lat, lng)
            raw_shapes = defaultdict(list)
            for row in shape_rows:
                sid = row.get("shape_id", "").strip()
                seq = row.get("shape_pt_sequence", "0").strip()
                lat = row.get("shape_pt_lat", "").strip()
                lng = row.get("shape_pt_lon", "").strip()
                if sid and lat and lng:
                    try:
                        raw_shapes[sid].append((int(seq), float(lat), float(lng)))
                    except ValueError:
                        pass

            # Map shape_id → route_short_name (via trips.txt)
            shape_to_routes = defaultdict(set)
            for row in trips_rows:
                sid  = row.get("shape_id", "").strip()
                rid  = row.get("route_id", "").strip()
                short = route_short.get(rid, rid)
                if sid and short:
                    shape_to_routes[sid].add(short)

            # Build route_short_name → [[lat, lng], ...] using longest shape per route
            route_shapes = {}
            shape_lengths = {sid: len(pts) for sid, pts in raw_shapes.items()}
            for sid, route_set in shape_to_routes.items():
                pts = sorted(raw_shapes[sid], key=lambda x: x[0])
                coords = [[p[1], p[2]] for p in pts]
                for short in route_set:
                    existing = route_shapes.get(short)
                    if existing is None or len(coords) > len(existing):
                        route_shapes[short] = coords

            # Add aliases so the frontend can look up by its route IDs
            ALIASES = {
                # subway
                "L1": "MFL", "B1": "BSL",
                # regional rail (GTFS short code → app route ID)
                "AIR": "Airport", "CHE": "Chestnut Hill East",
                "CHW": "Chestnut Hill West", "CYN": "Cynwyd",
                "FOX": "Fox Chase", "LAN": "Lansdale",
                "MED": "Media", "NOR": "Manayunk",
                "PAO": "Paoli", "TRE": "Trenton",
                "WAR": "Warminster", "WTR": "West Trenton",
                "WIL": "Wilmington",
            }
            for gtfs_key, app_key in ALIASES.items():
                if gtfs_key in route_shapes:
                    route_shapes[app_key] = route_shapes[gtfs_key]

            shapes_path = OUT_DIR / "shapes.json"
            shapes_path.write_text(json.dumps(route_shapes))
            print(f"Wrote {shapes_path}  ({len(route_shapes)} route shapes)")
        except FileNotFoundError as e:
            print(f"  shapes.txt not found: {e} — skipping")

    # ── Add schedule aliases (same mapping as shapes) ────────────────────
    SCHED_ALIASES = {
        "L1": "MFL", "B1": "BSL",
        "AIR": "Airport", "CHE": "Chestnut Hill East",
        "CHW": "Chestnut Hill West", "CYN": "Cynwyd",
        "FOX": "Fox Chase", "LAN": "Lansdale",
        "MED": "Media", "NOR": "Manayunk",
        "PAO": "Paoli", "TRE": "Trenton",
        "WAR": "Warminster", "WTR": "West Trenton",
        "WIL": "Wilmington",
    }
    for gtfs_key, app_key in SCHED_ALIASES.items():
        if gtfs_key in schedule:
            schedule[app_key] = schedule[gtfs_key]

    # ── Write outputs ────────────────────────────────────────────────────
    stops_path = OUT_DIR / "stops.json"
    stops_path.write_text(json.dumps(stops, indent=2))
    print(f"Wrote {stops_path}  ({len(stops)} stops)")

    sched_path = OUT_DIR / "schedule.json"
    sched_path.write_text(json.dumps(dict(schedule), indent=2))
    print(f"Wrote {sched_path}  ({len(schedule)} routes)")
    print("Done.")


if __name__ == "__main__":
    main()
