# Philadelphia Transit Tracker – Backlog

## FUTURE

- [ ] long-term study to determine underground (trolley) timing
  - [ ] for each trolley trip add tunnel entrance and reemergence times
  - [ ] collect stats across routes and times of day, day of week
- [ ] improved regional rail routes
- [ ] error bands on the statistics plots

## TODO

### Diversion logic
- [ ] if the trolleys are on diversion, change the estimate of the turnaround time since they aren't going as far
- [ ] add stops at the points where the trolleys turn (they can stop at any intersection, but we don't need them, since they're just approximate)

### Done (this session)
- [x] ghost default opacity 55% (was 35%)
- [x] server-side polling 5s (was 15s)
- [x] client refresh dropdown: 5/7/10/15/20s, default 7s (was 10/20/30s, default 20s)
- [x] auto-generate stops for bus routes with no official stops (~every 4th intersection)