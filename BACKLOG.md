# Philadelphia Transit Tracker – Backlog

## TODO

### Diversion logic
- [ ] if the trolleys are on diversion, change the estimate of the turnaround time since they aren't going as far
- [ ] add stops at the points where the trolleys turn (they can stop at any intersection, but we don't need them, since they're just approximate)

### Statistics
- [ ] error bands on the statistics plots
- [ ] break the tunnel-timing stats down by time of day and day of week
      (per-trip `tunnel_seconds` is already recorded — this is analysis, not collection)

### Routes
- [ ] improved regional rail routes

## FUTURE

- [ ] real-time subway (MFL/BSL) positions, if SEPTA ever exposes them
- [ ] frontend modernization: replace inline `onclick=` handlers with
      `addEventListener`, and give `src/js/` a real module boundary instead of
      cross-file implicit globals (`liveRegistry`, `ghostVehicles`, …)
- [ ] subresource-integrity hashes on the Leaflet CDN tags in `public/index.html`

## DONE

- [x] long-term study to determine underground (trolley) timing
  - [x] for each trolley trip add tunnel entrance and reemergence times —
        accumulated as `tunnel_seconds` on the Trip (`pkg/core/trip.py`) and
        persisted per trip in `today.json`
  - [x] rolling 20-minute tunnel transit average, T2–T5 pooled and T1 separate
        (`pkg/core/tunnel_monitor.py`), with a GTFS-derived historical fallback
