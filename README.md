# Philadelphia Transit Tracker Live

Real-time transit tracker for the Philadelphia area. Track SEPTA buses, trolleys, regional rail, and subway lines on a live map with vehicle cards, tunnel ghost estimation, stop predictions, and trip statistics.

**Live at [sept.ooo](https://sept.ooo)**

![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)

## Features

- **Live vehicle tracking** for all SEPTA bus, trolley, and regional rail routes
- **Interactive map** with GTFS route shapes (Leaflet + CartoDB Dark Matter tiles)
- **Server-side tunnel ghost tracking** — detects trolleys entering the subway-surface tunnel and estimates their position using GTFS schedule data with smooth path interpolation
- **Stop predictions** — real-time arrival estimates at nearby stops via SEPTA v2 API
- **Trip statistics** with schedule overlay charts
- **Background trip tracker** that logs completed trips server-side
- **Server-side SEPTA data caching** — single background poller keeps API call rate fixed regardless of user count

## Quick Start

### Local development

```bash
pip install flask requests

# Build static GTFS data (route shapes, stops, schedules, tunnel timing)
python3 scripts/build_gtfs.py
python3 scripts/tunnel_timing.py

# Start the server
python3 server.py
```

Open [http://localhost:5000](http://localhost:5000) in your browser.

### Docker (production)

```bash
docker compose up -d
```

The app binds to `127.0.0.1:5000` — use a host-level nginx (or similar) reverse proxy for HTTPS. A sample `nginx.conf` is included for reference.

## Project Structure

```
philatransit/
├── server.py               # Entrypoint — creates Flask app via pkg/
├── pkg/                    # Python backend package
│   ├── app.py              #   Flask app factory, CORS, frontend serving
│   ├── cache.py            #   Background SEPTA data poller, in-memory caches
│   ├── ghosts.py           #   Tunnel ghost detection and state
│   ├── tracker.py          #   Background trip completion tracker
│   ├── routes.py           #   All API route handlers (Flask Blueprint)
│   └── helpers.py          #   Shared constants, file I/O, utilities
├── public/
│   └── index.html          # Main HTML shell
├── src/
│   ├── css/
│   │   └── style.css       # All styles
│   └── js/
│       ├── app.js          # App state, init, sidebar, panels, auto-refresh
│       ├── map.js          # Leaflet map, route drawing, vehicle markers
│       ├── routes.js       # Route definitions, mode config, rail line mapping
│       ├── stations.js     # Hardcoded station coordinates from GTFS
│       ├── stats.js        # Statistics panel and chart rendering
│       └── tunnel.js       # Tunnel constants, ghost interpolation, geometry
├── static/
│   ├── shapes.json         # GTFS route shapes (generated)
│   ├── stops.json          # GTFS stop data (generated)
│   ├── schedule.json       # GTFS schedule data (generated)
│   └── tunnel_times.json   # Tunnel transit times (generated)
├── scripts/
│   ├── build_gtfs.py       # Downloads GTFS and builds static data files
│   └── tunnel_timing.py    # Extracts tunnel transit times from GTFS
├── data/                   # Runtime data (trip logs — persisted via Docker volume)
├── Dockerfile
├── docker-compose.yml
├── nginx.conf              # Sample nginx config (HTTPS, caching, reverse proxy)
├── requirements.txt
├── BACKLOG.md
├── AUTHORS.md
└── LICENSE                 # MIT
```

## Architecture

### Server-side caching

A single background thread polls the SEPTA API every 15 seconds and stores results in memory. All client requests are served from this cache, so SEPTA sees a fixed call rate regardless of how many users are connected. The app runs with gunicorn (`--workers 1 --threads 4`) to keep background threads and caches in shared memory.

### Tunnel ghost tracking

SEPTA trolley lines T1-T5 share an underground tunnel between their western portals (36th St or 40th St) and 13th St. Vehicles lose GPS and disappear from the API inside the tunnel. The server detects tunnel entries by:

1. Monitoring vehicle GPS positions near tunnel portals for stationary linger (60s threshold)
2. Detecting sudden vehicle disappearance near portals
3. Creating "ghost" entries with estimated position interpolated along the GTFS route shape
4. Removing ghosts when the real vehicle reappears at the far end

Ghost state is maintained server-side so all users see tunnel vehicles immediately, even on first page load.

### Data sources

- **SEPTA TransitView API** — real-time vehicle positions for buses, trolleys, and subway
- **SEPTA TrainView API** — real-time positions for regional rail
- **SEPTA v2 API** — trip-level stop predictions and service alerts
- **SEPTA GTFS** — static schedule data, route shapes, and stop coordinates

### Known limitations

- **Subway lines (MFL/BSL)**: SEPTA does not provide real-time GPS data for subway vehicles. The API returns only placeholder entries.
- Tunnel estimation is approximate — actual transit times vary with traffic and dwell times.

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
