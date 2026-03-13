# Philadelphia Transit Tracker Live

Real-time transit tracker in the Philadelphia area including SEPTA (Southeastern Pennsylvania Transportation Authority). Track buses, trolleys, regional rail, and subway lines on a live map with vehicle cards, tunnel estimation for trolleys, and trip statistics.

![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)

## Features

- **Live vehicle tracking** for all SEPTA bus, trolley, and regional rail routes
- **Interactive map** with GTFS route shapes (Leaflet + CartoDB Dark Matter tiles)
- **Trolley tunnel estimation** — estimates vehicle positions inside the subway-surface tunnel using GTFS schedule data, with round-trip tracking and smooth path interpolation along the actual route shape
- **Trip statistics** with schedule overlay charts
- **Background trip tracker** that logs completed trips server-side

## Quick Start

```bash
# Install dependencies
pip install flask requests

# Build static GTFS data (route shapes, stops, schedules, tunnel timing)
python3 scripts/build_gtfs.py
python3 scripts/tunnel_timing.py

# Start the server
python3 server.py
```

Open [http://localhost:5000](http://localhost:5000) in your browser.

## Project Structure

```
philatransit/
├── public/
│   └── index.html          # Main HTML shell
├── src/
│   ├── css/
│   │   └── style.css       # All styles
│   └── js/
│       ├── routes.js       # Route definitions, mode config, rail line key mapping
│       ├── tunnel.js       # Tunnel constants, ghost vehicle estimation, geometry
│       ├── stations.js     # Hardcoded station coordinates from GTFS
│       ├── map.js          # Leaflet map, route drawing, vehicle markers
│       ├── stats.js        # Statistics panel and chart rendering
│       └── app.js          # App state, init, sidebar, panels, auto-refresh
├── static/
│   ├── stops.json          # GTFS stop data (generated)
│   ├── schedule.json       # GTFS schedule data (generated)
│   ├── shapes.json         # GTFS route shapes (generated)
│   └── tunnel_times.json   # Tunnel transit times (generated)
├── scripts/
│   ├── build_gtfs.py       # Downloads GTFS and builds static data files
│   └── tunnel_timing.py    # Extracts tunnel transit times from GTFS
├── data/                   # Runtime data (trip logs, tracker state)
├── server.py               # Flask backend — API proxy, trip tracker, stats
├── requirements.txt
├── BACKLOG.md              # Planned improvements
├── AUTHORS.md
└── LICENSE                 # MIT
```

## How It Works

### Data Sources

- **SEPTA TransitView API** — real-time vehicle positions for buses, trolleys, and subway
- **SEPTA TrainView API** — real-time positions for regional rail
- **SEPTA GTFS** — static schedule data, route shapes, and stop coordinates

### Tunnel Estimation

SEPTA trolley lines T1–T5 share an underground tunnel between their western portals (36th St or 40th St) and 13th St. Vehicles disappear from the API when they enter the tunnel. SEPTA Live estimates their position by:

1. Detecting when a tracked vehicle disappears near a tunnel portal
2. Using GTFS-derived one-way transit times (7–11 minutes depending on route)
3. Interpolating the vehicle's position along the actual GTFS route shape
4. Reversing direction at 13th St for the return leg

### Known Limitations

- **Subway lines (MFL/BSL)**: SEPTA does not provide real-time GPS data for subway vehicles. The API returns only placeholder entries.
- Tunnel estimation is approximate — actual transit times vary with traffic and dwell times.

## Issues

If you see a bug, please open an [Issue!](https://github.com/samueldmcdermott/philatransit/issues)

## License

MIT — see [LICENSE](LICENSE).
