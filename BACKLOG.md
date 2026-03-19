# Philadelphia Transit Tracker – Backlog

## FUTURE

- [ ] long-term study to determine underground (trolley) timing
  - [ ] for each trolley trip add tunnel entrance and reemergence times
  - [ ] collect stats across routes and times of day, day of week
- [ ] improved regional rail routes
- [ ] error bands on the statistics plots

## TODO

### Diversion logic
- [ ] if the trolleys are on diversion, change the esimate of the turnaround time since they aren't going as far
- [ ] add stops at the points where the trolleys turn (they can stop at any intersection, but we don't need them, since they're just approximate)

### General
- [ ] make the default opacity 55% instead of 35% for "ghosts"
- [ ] if a bus route has no official stops, put one at every fourth intersection (approximately)