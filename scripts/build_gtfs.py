#!/usr/bin/env python3
"""
Build static GTFS data files for SEPTA Live.

Downloads the SEPTA GTFS ZIP that is in effect today and extracts:
  - static/stops.json          → {stop_id: {name, lat, lng}}
  - static/schedule.json       → {route_short_name: {weekday:[min,...], saturday:[...], sunday:[...]}}
  - static/shapes.json         → {route_key: [[lat, lng], ...]}
  - static/rail_lines.json     → per rail line, its ordered station list
  - static/rail_schedule.json  → per train number, its scheduled run (see build_rail)

Scheduled minutes are the first-stop departure times for each trip (minutes since midnight),
representing when each scheduled trip begins service.

Usage:
    pip install requests
    python3 scripts/build_gtfs.py            # the feed in effect today
    python3 scripts/build_gtfs.py --latest   # newest feed, even if not yet effective
"""

import contextlib
import csv
import io
import json
import sys
import zipfile
from collections import defaultdict
from datetime import date
from pathlib import Path

try:
    import requests
except ImportError:
    print("Missing: pip install requests")
    sys.exit(1)

# The GTFS-code → route-ID map is shared with the running app rather than
# duplicated here; a copy that drifts is what let the old rail aliasing
# bug hide for months.
sys.path.insert(0, str(Path(__file__).parent.parent))
from pkg.provider.septa.constants import RAIL_ROUTE_CODES  # noqa: E402

GTFS_URL      = "https://github.com/septadev/GTFS/releases/latest/download/gtfs_public.zip"
GTFS_RELEASES = "https://api.github.com/repos/septadev/GTFS/releases"
OUT_DIR       = Path(__file__).parent.parent / "static"

# How far back to walk the release list looking for the feed in effect today.
# SEPTA publishes a new release a few days to a week ahead of its effective
# date, so the current feed is normally the first or second entry.
MAX_RELEASES_SCANNED = 6


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


def read_csv(zf, filename, prefix=None):
    """Read a CSV file from a ZIP archive into a list of dicts.

    Merges rows from all matching files (e.g. google_bus/routes.txt +
    google_rail/routes.txt).  Pass `prefix` ("google_rail") to read from
    one feed only — several files exist in both and mean different
    things, notably route_stops.txt and directions.txt.
    """
    names = zf.namelist()
    matches = [n for n in names if n.endswith(filename)]
    if prefix:
        matches = [n for n in matches if n.startswith(prefix)]
    if not matches:
        raise FileNotFoundError(f"{filename} not found in ZIP. Available: {names[:20]}")
    rows = []
    for match in matches:
        with zf.open(match) as f:
            text = f.read().decode("utf-8-sig")
        rows.extend(csv.DictReader(io.StringIO(text)))
    return rows


def feed_window(zf):
    """Return (feed_start_date, feed_end_date) as YYYYMMDD strings."""
    try:
        rows = read_csv(zf, "feed_info.txt", prefix="google_rail")
    except FileNotFoundError:
        rows = read_csv(zf, "feed_info.txt")
    if not rows:
        return "", ""
    return (rows[0].get("feed_start_date", "").strip(),
            rows[0].get("feed_end_date", "").strip())


def fetch_gtfs(prefer_latest=False):
    """Download the GTFS release in effect today.

    GitHub's "latest" release is often published several days before it
    takes effect, so blindly taking it yields a feed whose train numbers
    and times do not describe the trains currently running.  Walk back
    through the release list until one covers today.
    """
    if prefer_latest:
        print(f"Downloading GTFS from {GTFS_URL} …")
        r = requests.get(GTFS_URL, timeout=180)
        r.raise_for_status()
        return r.content

    today = date.today().strftime("%Y%m%d")
    try:
        rels = requests.get(GTFS_RELEASES, params={"per_page": MAX_RELEASES_SCANNED},
                            timeout=30).json()
    except Exception as e:
        print(f"  ! release list unavailable ({e}) — falling back to latest")
        rels = []

    newest = None
    for rel in rels if isinstance(rels, list) else []:
        asset = next((a for a in rel.get("assets", [])
                      if a.get("name") == "gtfs_public.zip"), None)
        if not asset:
            continue
        print(f"Downloading {rel.get('tag_name')} …")
        r = requests.get(asset["browser_download_url"], timeout=180)
        r.raise_for_status()
        with open_gtfs(r.content) as zf:
            start, end = feed_window(zf)
        if newest is None:
            newest = (rel.get("tag_name"), r.content, start, end)
        if start <= today <= end:
            print(f"  in effect today ({start}–{end})")
            return r.content
        print(f"  covers {start}–{end}, not today ({today}) — trying the previous release")

    if newest:
        tag, content, start, end = newest
        print(f"  ! no release covers {today}; using {tag} ({start}–{end}). "
              f"Rail train numbers may not match the live feed.")
        return content

    print(f"Downloading GTFS from {GTFS_URL} …")
    r = requests.get(GTFS_URL, timeout=180)
    r.raise_for_status()
    return r.content


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


def build_rail(zf):
    """Build the two rail files from google_rail.

    rail_lines.json — {route_id: {gtfs, stations: [{id, name, lat, lng}, ...]}}
        Stations run inbound-end → outbound-end.  Every SEPTA rail line is
        a single linear sequence; what look like branches (Paoli vs
        Thorndale, Media vs Wawa) are short-turns along one line, so one
        ordered list per line is sufficient.

    rail_schedule.json — the scheduled run for each train number:
        {feed_start, feed_end, stations: [...], services: {...},
         runs: {train_no: [{route, service, outbound, headsign,
                            stops: [[station_idx, minutes], ...]}, ...]}}

        A run has one leg per line it traverses.  Through-running trains
        have two: train 2591 is Norristown TC→Suburban on NOR, then
        Suburban→Malvern on PAO.  TrainView reports the line of the leg
        the train is on now and the destination of the whole run, which
        is why neither field alone can describe a train.

        Legs are stored, not flattened, because the runtime needs to know
        which line a train is on at a given moment.  Times are minutes
        since local midnight, matching the rest of the app.
    """
    stops_rows = read_csv(zf, "stops.txt", prefix="google_rail")
    stations, station_idx = [], {}
    for row in stops_rows:
        sid = row.get("stop_id", "").strip()
        try:
            lat = float(row.get("stop_lat", ""))
            lng = float(row.get("stop_lon", ""))
        except ValueError:
            continue
        station_idx[sid] = len(stations)
        stations.append({"id": sid, "name": row.get("stop_name", "").strip(),
                         "lat": lat, "lng": lng})

    # directions.txt names each direction_id per route.  The mapping is
    # NOT consistent across routes — AIR 0 is Inbound, CHE 0 is Outbound —
    # so it must be read rather than assumed.
    outbound_dir = {}
    for row in read_csv(zf, "directions.txt", prefix="google_rail"):
        if row.get("direction", "").strip().lower() == "outbound":
            outbound_dir[row.get("route_id", "").strip()] = row.get("direction_id", "").strip()

    # rail_lines.json — the outbound station order is the canonical one.
    by_route_dir = defaultdict(list)
    for row in read_csv(zf, "route_stops.txt", prefix="google_rail"):
        key = (row.get("route_id", "").strip(), row.get("direction_id", "").strip())
        by_route_dir[key].append(row)

    rail_lines = {}
    for code, route_id in RAIL_ROUTE_CODES.items():
        rows = by_route_dir.get((code, outbound_dir.get(code, "1")))
        if not rows:
            print(f"  ! no route_stops for {code} — skipping")
            continue
        rows.sort(key=lambda r: int(r.get("route_stop_sort_order", "0") or 0))
        seq = [stations[station_idx[r["stop_id"].strip()]]
               for r in rows if r.get("stop_id", "").strip() in station_idx]
        rail_lines[route_id] = {"gtfs": code, "stations": seq}

    # services — calendar + calendar_dates, so the runtime can resolve the
    # active service by date.  Service windows overlap (a track-work
    # service can shadow the base one for a few days), which makes
    # (train, route, weekday) ambiguous without the actual date.
    services = {}
    for row in read_csv(zf, "calendar.txt", prefix="google_rail"):
        sid = row.get("service_id", "").strip()
        if not sid:
            continue
        services[sid] = {
            "dow": [int(row.get(d, "0") or 0) for d in
                    ("monday", "tuesday", "wednesday", "thursday",
                     "friday", "saturday", "sunday")],
            "start": row.get("start_date", "").strip(),
            "end": row.get("end_date", "").strip(),
            "added": [], "removed": [],
        }
    try:
        cdates = read_csv(zf, "calendar_dates.txt", prefix="google_rail")
    except FileNotFoundError:
        cdates = []
    for row in cdates:
        sid = row.get("service_id", "").strip()
        day = row.get("date", "").strip()
        if not sid or not day:
            continue
        svc = services.setdefault(sid, {"dow": [0] * 7, "start": "", "end": "",
                                        "added": [], "removed": []})
        key = "added" if row.get("exception_type", "").strip() == "1" else "removed"
        svc[key].append(day)

    # runs — keyed on the train number carried in trip_short_name.
    st_by_trip = defaultdict(list)
    for row in read_csv(zf, "stop_times.txt", prefix="google_rail"):
        st_by_trip[row.get("trip_id", "").strip()].append(row)

    runs = defaultdict(list)
    for row in read_csv(zf, "trips.txt", prefix="google_rail"):
        code = row.get("route_id", "").strip()
        short = row.get("trip_short_name", "").strip()
        # trip_short_name is the route code followed by the train number,
        # e.g. "PAO2591" — and that number is TrainView's `trainno`.
        train_no = short[len(code):] if short.startswith(code) else short
        train_no = "".join(ch for ch in train_no if ch.isdigit())
        rows = sorted(st_by_trip.get(row.get("trip_id", "").strip(), []),
                      key=lambda r: int(r.get("stop_sequence", "0") or 0))
        if not train_no or not rows:
            continue
        stop_seq = []
        for r in rows:
            sid = r.get("stop_id", "").strip()
            dep = parse_time(r.get("departure_time", ""))
            if sid in station_idx and dep is not None:
                stop_seq.append([station_idx[sid], round(dep, 2)])
        if not stop_seq:
            continue
        runs[train_no].append({
            "route": RAIL_ROUTE_CODES.get(code, code),
            "service": row.get("service_id", "").strip(),
            "outbound": row.get("direction_id", "").strip() == outbound_dir.get(code),
            "headsign": row.get("trip_headsign", "").strip(),
            "stops": stop_seq,
        })

    # Order each run's legs by departure time so concatenation yields the
    # journey in travel order.
    for legs in runs.values():
        legs.sort(key=lambda leg: leg["stops"][0][1])

    start, end = feed_window(zf)
    schedule = {"feed_start": start, "feed_end": end, "stations": stations,
                "services": services, "runs": dict(runs)}

    lines_path = OUT_DIR / "rail_lines.json"
    lines_path.write_text(json.dumps(rail_lines, indent=2))
    print(f"Wrote {lines_path}  ({len(rail_lines)} lines)")

    sched_path = OUT_DIR / "rail_schedule.json"
    sched_path.write_text(json.dumps(schedule, separators=(",", ":")))
    print(f"Wrote {sched_path}  ({len(runs)} train numbers, feed {start}–{end})")


def main():
    OUT_DIR.mkdir(exist_ok=True)

    content = fetch_gtfs(prefer_latest="--latest" in sys.argv)
    print(f"  Downloaded {len(content) / 1024:.0f} KB")

    with open_gtfs(content) as zf:
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
                with contextlib.suppress(ValueError):
                    stops[sid] = {"name": name, "lat": float(lat), "lng": float(lng)}
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
            if w == 5:
                bucket[1] += 1
            elif w == 6:
                bucket[2] += 1
            else:
                bucket[0] += 1

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
                    with contextlib.suppress(ValueError):
                        raw_shapes[sid].append((int(seq), float(lat), float(lng)))

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
                **RAIL_ROUTE_CODES,
            }
            for gtfs_key, app_key in ALIASES.items():
                if gtfs_key in route_shapes:
                    route_shapes[app_key] = route_shapes[gtfs_key]

            shapes_path = OUT_DIR / "shapes.json"
            shapes_path.write_text(json.dumps(route_shapes))
            print(f"Wrote {shapes_path}  ({len(route_shapes)} route shapes)")
        except FileNotFoundError as e:
            print(f"  shapes.txt not found: {e} — skipping")

        # ── rail_lines.json + rail_schedule.json ────────────────────────
        print("Building rail lines and schedule …")
        build_rail(zf)

    # ── Add schedule aliases (same mapping as shapes) ────────────────────
    SCHED_ALIASES = {
        "L1": "MFL", "B1": "BSL",
        **RAIL_ROUTE_CODES,
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
