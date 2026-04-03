#!/usr/bin/env python3
"""
Extract tunnel transit times for SEPTA trolley routes from GTFS data.

For each trolley route (T1-T5), finds the time between the portal stop
and 13th St stop using stop_times.txt, then outputs tunnel_times.json.

Usage:
    pip install requests
    python3 scripts/tunnel_timing.py
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


def read_csv(zf, filename):
    names = zf.namelist()
    match = next((n for n in names if n.endswith(filename)), None)
    if match is None:
        raise FileNotFoundError(f"{filename} not found in ZIP")
    with zf.open(match) as f:
        text = f.read().decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


def open_gtfs(content):
    outer = zipfile.ZipFile(io.BytesIO(content))
    inner_names = [n for n in outer.namelist() if n.endswith(".zip")]
    if inner_names:
        merged = {}
        for inner_name in inner_names:
            prefix = inner_name.replace(".zip", "") + "/"
            with outer.open(inner_name) as f:
                inner = zipfile.ZipFile(io.BytesIO(f.read()))
                for name in inner.namelist():
                    merged[prefix + name] = inner.read(name)
                inner.close()
        outer.close()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zout:
            for name, data in merged.items():
                zout.writestr(name, data)
        buf.seek(0)
        return zipfile.ZipFile(buf)
    return outer


def parse_time_seconds(s):
    """Parse HH:MM:SS → seconds since midnight."""
    parts = s.strip().split(":")
    if len(parts) != 3:
        return None
    try:
        h, m, sec = int(parts[0]), int(parts[1]), int(parts[2])
        return h * 3600 + m * 60 + sec
    except ValueError:
        return None


# Portal and 13th St stop names/coordinates for matching
# We'll match by proximity to known coordinates
PORTAL_COORDS = {
    "T1": (39.9553, -75.1942),   # 36th St Portal
    "T2": (39.94939, -75.20333),   # 40th St Portal (east tip of yard)
    "T3": (39.94939, -75.20333),
    "T4": (39.94939, -75.20333),
    "T5": (39.94939, -75.20333),
}
THIRTEENTH_ST = (39.9525, -75.1626)  # 13th St station


def dist_sq(lat1, lng1, lat2, lng2):
    return (lat1 - lat2) ** 2 + (lng1 - lng2) ** 2


def main():
    OUT_DIR.mkdir(exist_ok=True)

    print(f"Downloading GTFS from {GTFS_URL} …")
    r = requests.get(GTFS_URL, timeout=120)
    r.raise_for_status()
    print(f"  Downloaded {len(r.content) / 1024:.0f} KB")

    with open_gtfs(r.content) as zf:
        # Load routes
        routes_rows = read_csv(zf, "routes.txt")
        route_short = {}
        for row in routes_rows:
            rid = row.get("route_id", "").strip()
            short = row.get("route_short_name", "").strip()
            if rid:
                route_short[rid] = short or rid

        # Load trips → route mapping
        trips_rows = read_csv(zf, "trips.txt")
        trip_route = {}  # trip_id → route_short_name
        for row in trips_rows:
            tid = row.get("trip_id", "").strip()
            rid = row.get("route_id", "").strip()
            short = route_short.get(rid, rid)
            if tid and short in ("T1", "T2", "T3", "T4", "T5"):
                trip_route[tid] = short

        print(f"  {len(trip_route)} trolley trips found")

        # Load stops → find portal and 13th St stops per route
        stops_rows = read_csv(zf, "stops.txt")
        all_stops = {}
        for row in stops_rows:
            sid = row.get("stop_id", "").strip()
            lat = row.get("stop_lat", "").strip()
            lng = row.get("stop_lon", "").strip()
            name = row.get("stop_name", "").strip()
            if sid and lat and lng:
                try:
                    all_stops[sid] = {"name": name, "lat": float(lat), "lng": float(lng)}
                except ValueError:
                    pass

        # Load stop_times for trolley trips
        print("Loading stop_times.txt (this takes a moment) …")
        st_rows = read_csv(zf, "stop_times.txt")

        # For each trolley trip, collect all stop times
        trip_stops = defaultdict(list)  # trip_id → [(stop_seq, stop_id, arrival_sec, departure_sec)]
        for row in st_rows:
            tid = row.get("trip_id", "").strip()
            if tid not in trip_route:
                continue
            sid = row.get("stop_id", "").strip()
            seq = row.get("stop_sequence", "0").strip()
            arr = row.get("arrival_time", "").strip()
            dep = row.get("departure_time", "").strip()
            arr_s = parse_time_seconds(arr)
            dep_s = parse_time_seconds(dep)
            if arr_s is not None:
                try:
                    trip_stops[tid].append((int(seq), sid, arr_s, dep_s or arr_s))
                except ValueError:
                    pass

        print(f"  {len(trip_stops)} trips with stop times")

        # For each route, find tunnel transit times
        results = {}
        for route_id in ("T1", "T2", "T3", "T4", "T5"):
            portal_lat, portal_lng = PORTAL_COORDS[route_id]
            durations = []

            for tid, stops_list in trip_stops.items():
                if trip_route.get(tid) != route_id:
                    continue

                stops_list.sort(key=lambda x: x[0])

                # Find the stop closest to the portal and closest to 13th St
                portal_stop = None
                thirteenth_stop = None

                for seq, sid, arr, dep in stops_list:
                    s = all_stops.get(sid)
                    if not s:
                        continue

                    d_portal = dist_sq(s["lat"], s["lng"], portal_lat, portal_lng)
                    d_13th = dist_sq(s["lat"], s["lng"], THIRTEENTH_ST[0], THIRTEENTH_ST[1])

                    if d_portal < 0.0001:  # ~30m threshold
                        if portal_stop is None or d_portal < portal_stop[1]:
                            portal_stop = (arr, d_portal, s["name"], seq)

                    if d_13th < 0.0001:
                        if thirteenth_stop is None or d_13th < thirteenth_stop[1]:
                            thirteenth_stop = (arr, d_13th, s["name"], seq)

                if portal_stop and thirteenth_stop:
                    dt = abs(thirteenth_stop[0] - portal_stop[0])
                    if 120 < dt < 1800:  # between 2 and 30 minutes
                        durations.append(dt)

            if durations:
                avg = sum(durations) / len(durations)
                print(f"  {route_id}: {len(durations)} trips, avg one-way = {avg:.0f}s ({avg/60:.1f} min)")
                results[route_id] = {
                    "one_way_seconds": round(avg),
                    "round_trip_seconds": round(avg * 2),
                    "sample_count": len(durations),
                }
            else:
                print(f"  {route_id}: no tunnel timing data found")
                # Fallback estimates
                fallback = 420 if route_id != "T1" else 330
                results[route_id] = {
                    "one_way_seconds": fallback,
                    "round_trip_seconds": fallback * 2,
                    "sample_count": 0,
                }

    out_path = OUT_DIR / "tunnel_times.json"
    out_path.write_text(json.dumps(results, indent=2))
    print(f"\nWrote {out_path}")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
