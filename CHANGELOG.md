# Changelog

All notable changes to this project will be documented in this file.
Format based on [Keep a Changelog](https://keepachangelog.com/).

Versions correspond to the value in [`VERSION`](VERSION), which the app
serves at `/api/version`.

## [beta.3.0] - 2026-08-04

### Fixed
- The destination-terminus direction flip was being undone on the same poll
  that set it. Both direction correctors treat "still advancing along the
  shape" as evidence of outbound travel — which is exactly how a vehicle
  completing its outbound leg looks — so they immediately flipped the trip
  back and cleared `passed_destination` with it. Trips only converged to the
  return direction several polls later, once the vehicle had physically
  reversed far enough to trigger a correction. Because retirement at the
  origin is gated on `passed_destination`, a trip that vanished before
  producing that evidence (a tunnel re-entry, a dropped GPS fix) never
  registered as having reached its destination and retired on the stale
  timeout instead, recording a `fraction_stops_passed` that understated the
  trip and skewing the start-time CDFs.

  Reaching the terminus is now treated as positional fact that outranks the
  heuristics: the correctors are skipped for that poll and their state is
  re-anchored to the post-flip position, and neither can apply a
  forward-direction correction while the vehicle is within
  `_TERMINUS_RADIUS` of the shape end, where continued forward motion says
  nothing about heading. A spurious fix at the terminus is still recoverable
  by the movement corrector once real positions resume.

### Removed
- The **Tracker** toolbar button and its client-side trip-completion detection.
  It POSTed to `/api/tracker/start` and `/api/tracker/stop`, which never
  existed on the server, so clicking it produced a "Tracker error". Trip
  recording has been server-side since `beta.1.4`.
- Public write endpoints `POST /api/stats/record`, `POST /api/stats/clear`, and
  `GET`/`POST /api/scheduled`, plus `GET /api/tracker/status`. None were
  reachable from the UI; all were unauthenticated and CORS-open. The API is now
  entirely read-only and `Access-Control-Allow-Methods` is narrowed to
  `GET, OPTIONS`.
- Dead frontend code: `railLineKey`/`RAIL_ALIASES` in `routes.js` (rail keying
  is server-side in `pkg/provider/septa/constants.py`), and unreferenced
  `clearStats`/`percentile` in `stats.js`.
- `data/trips.json` (8.6 MB runtime artifact) is no longer tracked in git.

### Changed
- `today.json` is now held in memory and written back on a 5-second debounce
  instead of being re-read and re-serialized on every record. A busy day
  produces ~9k entries / ~1.4 MB across ~20k record calls, so the old path
  cost the poller thread time that grew with the length of the day.
- JSON writes are atomic (temp file + `fsync` + `os.replace`), so a crash or
  container restart mid-write can no longer truncate a day of statistics.

### Added
- `pytest` suite covering geometry, statistics persistence and rollover,
  tunnel rolling averages, and Trip lifecycle.
- `pyproject.toml` with `ruff` and `pytest` configuration; `requirements-dev.txt`.
- `CLAUDE.md` architecture and conventions guide.

## [beta.2.2] - 2026-06-18

### Fixed
- Tunnel entry/exit edge cases.

## [beta.2.1] - 2026-05-01

### Added
- Route-misassignment correction within the T1–T5 family: a trolley reported on
  the wrong route is moved to the route its GPS actually fits.
- Cross-route duplicate resolution when SEPTA lists one fleet number under
  multiple routes in a single poll.
- "Born dormant" handling — SEPTA marks trolleys live before they leave the
  yard, so trips first seen at their origin are held until real motion is
  observed, and the wake moment becomes the recorded start time.

### Changed
- General cleanup and refactor of the tracking path.

## [beta.2.0] - 2026-04-27

### Added
- Richer per-trip diagnostics in `today.json`: `elapsed_seconds`,
  `stops_passed`, `fraction_stops_passed`, `tunnel_seconds`, `idle_seconds`.
- Anomaly filtering for CDFs — completed trips covering under 95% of their
  effective route are excluded, and sub-10% "ghost" trips are always dropped.

### Fixed
- T4 shape and origin corrections.
- Clustered tunnel-entry bug.
- Trips reported live too early are now hidden.

### Changed
- Structural changes for clearer trip counting; map updates.

## [beta.1.5] - 2026-04-24

### Changed
- Overhaul of the statistics module for bug fixes and forward maintainability.
- Documentation update.

## [beta.1.4] - 2026-04-23

### Fixed
- Errors in historical trip logging; clearer naming throughout the workflow.
- Double counting of scheduled trips in the statistics.

## [beta.1.x] - 2026-04

### Added
- Tunnel occupancy information and a rolling 20-minute tunnel transit average,
  with T2–T5 pooled and T1 tracked separately.
- Live trips filter and sort controls.

### Fixed
- Ghost cache and alert-banner permanence issues.
- Tunnel mouth location and bearing on lingering ghosts.

## [0.2.0] - 2026-03-28

### Changed
- Rewrite replacing `Vehicle` with `Trip` as the central concept, with trips
  keyed on our own identifier rather than SEPTA's unstable trip IDs.

## [0.1.0-alpha.1] - 2026-03-20

Initial alpha release of the Philadelphia Transit Tracker.

### Added
- Live trolley and bus tracking on an interactive map
- Tunnel entrance/exit ghost-position estimates for underground trolleys
- Trip statistics collection and cumulative distribution plots
- SEPTA alert banners with tunnel closure detection
- Regional rail train tracking
- Legend and route info panel
