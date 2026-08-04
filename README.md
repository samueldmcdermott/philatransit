# Philadelphia Transit Tracker Live

Real-time transit tracker for the Philadelphia area. Track SEPTA buses, trolleys, regional rail, and subway lines on a live map with vehicle cards, tunnel ghost estimation, stop predictions, and trip statistics.

**Live at [sept.ooo](https://sept.ooo)**

![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)

## Features

- **Live vehicle tracking** for all SEPTA bus, trolley, and regional rail routes
- **Interactive map** with GTFS route shapes (Leaflet + CartoDB Dark Matter tiles)
- **Tunnel ghost tracking** — detects trolleys entering the subway-surface tunnel and interpolates their position while underground
- **Rolling tunnel transit times** — 20-minute rolling average of observed trolley tunnel trips
- **Stop predictions** — real-time arrival estimates at nearby stops via SEPTA v2 API
- **Trip statistics** — per-day start-time CDFs and histograms with schedule overlay
- **Server-side SEPTA data caching** — single background poller keeps API call rate fixed regardless of user count

## Quick Start

### Local development

```bash
pip install -r requirements.txt

# Start the server (static GTFS data is committed, so this works immediately)
python3 server.py
```

Open [http://localhost:5000](http://localhost:5000) in your browser.

The files in `static/` are checked in, so no GTFS download is needed to run the
app. Regenerate them only when SEPTA publishes a new feed:

```bash
python3 scripts/build_gtfs.py      # route shapes, stops, schedules
python3 scripts/tunnel_timing.py   # historical tunnel transit times
```

### Tests and linting

```bash
pip install -r requirements-dev.txt
ruff check .
python3 -m pytest
```

### Docker (production)

```bash
docker compose up -d
```

The app binds to `127.0.0.1:5000` — use a host-level nginx (or similar) reverse proxy for HTTPS. A sample `nginx.conf` is included for reference.

## Project Structure

```
philatransit/
├── server.py                   # Entrypoint — creates the Flask app
├── pkg/
│   ├── app.py                  # Flask app factory, CORS, frontend serving
│   ├── poller.py               # Background SEPTA poller, in-memory caches
│   ├── routes.py               # API route handlers (Flask Blueprint)
│   ├── helpers.py              # Shared paths, file I/O, date helpers
│   ├── geo.py                  # Geometry (projection, bearing, headings)
│   ├── version.py
│   ├── core/
│   │   ├── trip.py             # Trip dataclass + TripManager (lifecycle)
│   │   ├── stats.py            # Start-time persistence + midnight rollover
│   │   ├── tunnel_monitor.py   # Rolling tunnel transit-time average
│   │   ├── shapes.py           # Route shape registry
│   │   └── route.py            # Route config builder
│   └── provider/
│       ├── base.py             # Provider / detector interfaces
│       └── septa/              # SEPTA implementation + tunnel/detour detection
├── public/index.html           # HTML shell
├── src/
│   ├── css/style.css
│   └── js/
│       ├── app.js              # Init, sidebar, panels, auto-refresh
│       ├── map.js              # Leaflet map, route drawing, vehicle markers
│       ├── routes.js           # Route definitions, mode config
│       ├── stations.js         # Hardcoded station coordinates
│       ├── stats.js            # Statistics panel and chart rendering
│       └── tunnel.js           # Tunnel constants, ghost interpolation
├── static/                     # Generated GTFS data (shapes, stops, schedule, termini, tunnel_times)
├── scripts/
│   ├── build_gtfs.py           # Downloads GTFS and builds the static data files
│   └── tunnel_timing.py        # Extracts tunnel transit times from GTFS
├── tests/                      # pytest suite (geo, stats, tunnel monitor, Trip lifecycle)
├── data/                       # Runtime data (today.json, daily_cdfs.json — Docker volume, untracked)
├── Dockerfile
├── docker-compose.yml
├── nginx.conf                  # Sample nginx config (HTTPS, caching, reverse proxy)
├── pyproject.toml              # ruff + pytest configuration
└── requirements.txt
```

## Architecture

### Server-side polling and caching

A single background thread in `pkg/poller.py` polls SEPTA every 5 seconds (transit + regional rail) and stores results in memory. All client requests are served from this cache, so SEPTA sees a fixed call rate regardless of how many users are connected. The app runs under gunicorn with `--workers 1 --threads 4` so background threads and caches stay in shared memory.

### Trip as the primary object

Each observed vehicle becomes a `Trip` owned by `TripManager` ([`pkg/core/trip.py`](pkg/core/trip.py)). A Trip is keyed by our own `trip_id` (`{vehicle_id}_{epoch}`) — SEPTA's trip identifiers are unstable and aren't trusted. The Trip tracks direction along the route shape, current/next stop, stops passed, speed, and origin/destination. Trips retire naturally on return-to-origin and are pruned after 10 minutes without an update.

When a Trip is created, its start time is persisted via [`pkg/core/stats.py`](pkg/core/stats.py) — one Trip, one recorded start. Regional rail isn't Trip-managed; the poller records the first sighting of each train number per day through the same `record_start` path.

Both stats files are keyed `{route: {"YYYY-MM-DD": [...]}}`, sorted by start
minute, but the buckets differ. `today.json` holds rich per-trip entries:

```json
{"start": 544.0, "nominal_start": 540.0, "idle_seconds": 240,
 "elapsed_seconds": 1500, "stops_passed": 42,
 "fraction_stops_passed": 0.98, "tunnel_seconds": 310.5}
```

`daily_cdfs.json` holds flat minute lists, with anomalous trips already
filtered out:

```json
[480.0, 494.5, 511.2]
```

A daemon thread runs `rollover()` shortly after each midnight, draining
finished-day buckets from `today.json` into `daily_cdfs.json` and flattening
them on the way. Completed trips that covered under 95% of their effective
route are excluded from the harvest; those under 10% are treated as yard
ghosts and dropped everywhere.

`today.json` is kept in memory and flushed on a 5-second debounce rather than
rewritten on every record — a busy day is ~9k trips across ~20k record calls.
All JSON writes are atomic (temp file + `fsync` + `os.replace`), so an
interrupted write cannot truncate accumulated statistics.

### Tunnel ghost tracking

SEPTA trolley lines T1–T5 share an underground tunnel between their western portals (36th St or 40th St) and 13th St. Vehicles lose GPS and disappear from the API inside the tunnel. The tunnel detector in [`pkg/provider/septa/tunnel.py`](pkg/provider/septa/tunnel.py):

1. Watches vehicle GPS near tunnel portals for stationary linger (≥60s).
2. Detects sudden disappearance near a portal.
3. Creates a "ghost" with position interpolated along the route shape, protected from stale-pruning while it's underground.
4. Removes the ghost when the real vehicle reappears at the far end.

`pkg/core/tunnel_monitor.py` keeps a 20-minute rolling average of observed tunnel transit times (T2–T5 pooled, T1 separate). When fewer than 5 samples exist in the window, it falls back to the historical average from `static/tunnel_times.json`.

### API

The HTTP API is entirely read-only — there are no endpoints that mutate server
state.

| Endpoint | Returns |
| --- | --- |
| `GET /api/version` | Contents of `VERSION` |
| `GET /api/config` | Route/mode configuration for the frontend |
| `GET /api/vehicles?route=<id>` | Live trips for one transit route |
| `GET /api/vehicles/rail[?route=<id>]` | Live regional rail trains |
| `GET /api/stops?route=<id>` | Stops for a route |
| `GET /api/stop-predictions?stops=<ids>&routes=<ids>` | Arrival predictions per stop |
| `GET /api/alerts` | SEPTA service alerts (cached 60s) |
| `GET /api/ghosts` | Estimated tunnel positions and portal lingerers |
| `GET /api/monitoring[?route=<id>]` | Rolling tunnel transit averages |
| `GET /api/stats` | Today's raw trip entries |
| `GET /api/stats/cdfs[?include_invalid=1]` | Merged history + today, as minute lists |
| `GET /api/stats/export?format=csv[&route=<id>]` | CSV export |

### Data sources

- **SEPTA TransitView API** — real-time positions for buses, trolleys, subway
- **SEPTA TrainView API** — real-time positions for regional rail
- **SEPTA v2 API** — trip-level stop predictions and service alerts
- **SEPTA GTFS** — static schedule data, route shapes, stop coordinates

### Known limitations

- **Subway lines (MFL/BSL)**: SEPTA does not provide real-time GPS for subway vehicles. The API returns only placeholder entries.
- Tunnel position estimation is approximate — actual transit times vary with traffic and dwell.

## Deployment

The production setup uses Docker for the app and host-installed nginx for TLS termination:

```
internet → nginx (443/TLS) → 127.0.0.1:5000 → gunicorn/Flask
```

The included `nginx.conf` handles HTTPS via Let's Encrypt, gzip compression, and cache headers (1-day for static GTFS data, 1-hour for JS/CSS, no-cache for API).

## Issues

If you see a bug, please open an [Issue!](https://github.com/samueldmcdermott/philatransit/issues)

## License

MIT — see [LICENSE](LICENSE).
