# CLAUDE.md

Guidance for AI assistants working in this repository.

## What this is

A live SEPTA transit tracker running at [sept.ooo](https://sept.ooo). Flask
backend, vanilla-JS frontend, no build step. It polls SEPTA's public APIs,
reconstructs trips from raw vehicle sightings, and serves a map, live trip
cards, service alerts, and start-time statistics.

**This is a deployed site with real users and irreplaceable accumulated data.**
`data/daily_cdfs.json` holds months of trip history that cannot be regenerated.
Treat anything under `data/` as production state.

## Running it

```bash
pip install -r requirements-dev.txt   # runtime deps + pytest + ruff
python3 server.py                     # → http://localhost:5000

ruff check .                          # lint
python3 -m pytest                     # tests
```

Rebuilding static GTFS data (only needed when SEPTA publishes a new feed):

```bash
python3 scripts/build_gtfs.py      # → static/{stops,schedule,shapes,rail_lines,rail_schedule}.json
python3 scripts/tunnel_timing.py   # → static/tunnel_times.json
```

`build_gtfs.py` picks the feed in effect *today*, not GitHub's newest release
(SEPTA publishes ahead of the effective date, and rail train numbers must
match the trains actually running). `--latest` forces the newest.

Production is Docker + host nginx: `docker compose up -d`.

## Architecture

```
SEPTA APIs → SeptaProvider → poller thread → TripManager → in-memory caches → /api/* → browser
```

- **`pkg/provider/`** — the only code that talks to external APIs. `base.py`
  defines the `Provider` ABC and the normalized types; `septa/` implements it.
  Internal modules (`core/`, `geo.py`) must never import from a provider.
- **`pkg/poller.py`** — one background thread polls SEPTA every 5s and fills
  module-level caches. All client requests read those caches, so SEPTA sees a
  fixed call rate no matter how many users are connected.
- **`pkg/core/trip.py`** — `Trip` and `TripManager`. The heart of the system.
- **`pkg/core/stats.py`** — start-time persistence and the nightly rollover.
- **`pkg/routes.py`** — Flask blueprint. **The API is entirely read-only.**
- **`src/js/`** — plain scripts loaded in order by `public/index.html`. No
  modules, no bundler.

### Trip is the primary object

The provider gives us only `vehicle_id`, `route_id`, and raw GPS. Everything
else is computed: direction, current/next stop, stops passed, speed, origin,
destination. A `Trip` is keyed by our own `{vehicle_id}_{epoch}` — **SEPTA's
trip IDs are unstable and are never trusted**, and provider-internal
identifiers must not leak past the provider boundary.

Direction is not decided by any single check. A terminus crossing, stop
transitions, and net movement over the last four polls all vote, and later
polls correct earlier mistakes. It is tuned against real SEPTA behavior over
many months. **Do not "fix" direction, tunnel, or detour logic based on
reasoning about the code alone** — a change that looks obviously right will
regress edge cases that the constants at the top of `trip.py` and
`septa/tunnel.py` exist to handle.

### Regional Rail is a separate pipeline

Rail does **not** go through `TripManager`, and shouldn't be made to. `Trip`
infers direction and stop order from raw GPS and assumes one fixed stop
sequence per route; rail needs neither. TrainView names the current and next
station directly, and a line carries runs with different termini — a
Paoli/Thorndale train may end at Thorndale, Malvern, Paoli or Wayne.

`pkg/core/rail.py` joins each live train to its scheduled GTFS run. GTFS
`trip_short_name` is the route code plus the train number (`PAO2591`), and
that number is TrainView's `trainno`. Rules when touching it:

- **`line` is where the train is now; `dest` is where the run ends.** They
  disagree for through-running trains, and that is not an error — a
  Wilmington/Newark train really can be bound for West Trenton. Never key a
  train's line off `dest`.
- A run has one leg per line it traverses. Legs are kept separate so a train
  appears on the line it is currently on; `via` names the onward line.
- Stop counts are relative to the train's **own run**, never the full line.
  A short-turn that counted against the line would never reach its terminus.
- Station names differ between TrainView and GTFS ("Norristown T.C." vs
  "Norristown Transit Center"). Resolution is normalize → alias table →
  operator-insensitive retry, always scoped to one line's stations.
- The fallback path for trains missing from the timetable is load-bearing,
  not theoretical: it runs whenever SEPTA publishes a feed ahead of its
  effective date. It must not invent stop counts.
- Rail route IDs are TrainView's exact `line` strings. `rail_line_key` is an
  equality check — it used to be a substring scan, which filed Chestnut Hill
  West under Chestnut Hill East and West Trenton under Trenton for months.

### Statistics persistence

`today.json` is held in memory (`pkg/core/stats.py`) and flushed to disk on a
5-second debounce by the `StatsWriter` thread. A busy day is ~9k entries /
~1.4 MB across ~20k record calls, so re-serializing per call was quadratic in
the length of the day. Writes go through `helpers.dump`, which is atomic (temp
file + `fsync` + `os.replace`) so a crash cannot truncate the file.

Rules when touching this module:
- The in-memory dict is the source of truth while the process runs. Read it via
  `today_snapshot()`, never `load(TODAY)`.
- Mutate under `_file_lock` and call `_mark_dirty()`; don't write directly.
- The on-disk schema must stay backward-compatible. `_entry_minute` still reads
  bare floats and legacy epoch-ms starts from older files, which are never
  migrated.
- One Trip produces exactly one entry: `record_start` on creation,
  `record_travel_start` if it was idle, `record_finish` on retirement.

### The single-worker constraint

The Dockerfile runs gunicorn with `--workers 1 --threads 4`. This is load-bearing,
not a default. The poller thread, the stats writer, the midnight rollover
scheduler, and every in-memory cache live in one process's memory. A second
worker would poll SEPTA twice, keep a divergent `TripManager`, and race on
`today.json`. Scale with `--threads`, never `--workers`.

## Conventions

- Python: 4-space indent, `from __future__ import annotations`, module-level
  constants in `SCREAMING_CASE` at the top of the file with a comment
  explaining the value's origin. Private helpers take a `_` prefix.
- JS: `'use strict'`, 2-space indent, plain functions on the global scope.
- Comments explain *why*, especially where a constant encodes an observed
  SEPTA quirk. Match the surrounding density.
- `pkg/geo.py` is pure and stateless. Keep it that way.
- Registries and managers are instantiated by the app factory, not module-level
  singletons.

## Things that will bite you

- **Subway (MFL/BSL) has no real-time GPS.** SEPTA returns placeholder entries.
  The empty map is correct, not a bug.
- **A rail train changing `route_id` mid-run is correct.** It crossed Center
  City onto its second leg, so it leaves one line's `/api/vehicles/rail`
  listing and joins another's.
- **SEPTA marks trolleys live before they leave the yard.** Hence the
  "born dormant" path — trips first seen at their origin are hidden until real
  movement is observed, and the wake moment becomes the recorded start time.
- **SEPTA lists the same fleet number under multiple routes**, and often on the
  wrong T-route entirely. `_resolve_cross_route_duplicates` and
  `_correct_route_misassignment` handle both by GPS-vs-shape distance.
- **Trolleys lose GPS in the subway tunnel.** `septa/tunnel.py` synthesizes
  "ghost" positions; those trips are exempt from stale-pruning.
- `static/*.json` is generated but committed, so the app runs without a GTFS
  download. `shapes.json` is 3.6 MB — don't reformat it.
- Timezone is `America/New_York` everywhere; stats are minutes-since-local-midnight.

## Before committing

`ruff check .` and `python3 -m pytest` both clean. Bump `VERSION` and add a
`CHANGELOG.md` entry for user-visible changes.
