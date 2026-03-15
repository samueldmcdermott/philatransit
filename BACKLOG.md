# SEPTA Live – Backlog

## Planned Improvements

### Subway Tracking (MFL / BSL)
- [ ] SEPTA does not provide real-time GPS data for subway lines
- [ ] TransitView API returns only placeholder entries for L1 (MFL) and B1 (BSL): all have `label=None`, `VehicleID=None`, `late=998`, parked at a fake coordinate (39.952187, -75.15995)
- [ ] TransitViewAll confirms: 0 real vehicles out of 14 (L1) and 12 (B1) entries
- [ ] GTFS-RT endpoints exist (`realtime.septa.org/gtfsrt/vehicles/`) but return empty HTML pages
- **Possible approaches:**
  - [ ] Schedule-based estimation: use GTFS timetables to show where trains *should* be, similar to tunnel ghost estimation but for the entire line
  - [ ] Arrival board scraping: SEPTA may have station arrival boards with countdown data
  - [ ] Third-party data: check if Transit app or Google Maps have subway positions (they may have private feeds)
  - [ ] Monitor for API changes: SEPTA may add subway tracking in the future

### Adaptive Tunnel Timing
- [ ] Currently tunnel transit time comes from GTFS schedule averages (~11 min one-way for T2-T5, ~7 min for T1)
- [ ] **Improvement:** track vehicles that reappear at the portal after a tunnel trip and log the actual elapsed "underground time"
- [ ] Build a running average of real tunnel transit times per route
- [ ] Use this adaptive timing instead of the static schedule-based estimate
- [ ] Could store historical data in `data/tunnel_observations.json` via the server

### Statistics Section
- [ ] Needs further work or may be separated into a different app
- [ ] Current trip tracking works but UI/analysis could be improved


### TODO
*(No open items)*
