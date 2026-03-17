# Philadelphia Transit Tracker – Backlog

## FUTURE

- [ ] long-term study to determine underground (trolley) timing
  - [ ] for each trolley trip add tunnel entrance and reemergence times
  - [ ] collect stats across routes and times of day, day of week
- [ ] improved regional rail routes
- [ ] error bands on the statistics plots

## TODO

### Direction of travel
- [x] direction of travel should come *only* from our knowledge of the trip:
  - [x] we know the terminus from which a trip started, so the vehicle travels from the start terminus to the end terminus and then turns around
  - [x] use this information instead of the officially reported destination
  - [x] use the small white arrow on the dot to show the direction towards the end terminus (instead of the current bearing of the vehicle)
- [x] *priority:* this should be "server side" aka with the cache so that it's available instantly whenever anyone loads the page

### Stops
- [ ] stops should be a little larger / easier to click on (especially on mobile)
- [ ] next-to-arrive logic should improve
  - [ ] *priority:* this should be "server side" aka with the cache so that it's available instantly whenever anyone loads the page
  - [ ] make sure the "reflection" behavior after a trolley reaches 13th & Market is properly implemented and accounts for all westbound trolley trips
  - [ ] make sure the fore/aft timing difference is self-consistent, ie the gap in estimated arrival times is based on the time to traverse the fore/aft distance
- [ ] 33rd St currently is not listed as stop for T2, T3, T4, T5 but it should be
- [ ] conversely, T2, T3, T4, T5 routes are depicted as passing by the 36th St portal but should _not_ be
- [ ] show stops for all bus routes (currently shown on some but not all)

### Tunnel entrance and reappearance
- [ ] trains move slowly through the yard near the portal; don't mistake this slow motion for tunnel entry
- [ ] the "lingering" phenomenon that determines the entry to the tunnel should only happen at the very east end of the yard
- [ ] eastbound trolleys that are currently determined to be lingering should linger as a solid dot that shrinks and expands
- [ ] the estimated position of a westbound trolley which is underground should _never_ pass west of the actual portal on the east side of the yard
- [ ] if the estimated position of a westbound trolley is at the portal or near 40th St, it should linger as a dashed dot that shrinks and expands
- [ ] make the default opacity 55% instead of 35%

### Diversion logic
- [ ] reexamine the alerts API to see if there's a better way to do this than by keywords
- [ ] if trolleys are _currently in_ the diversion loop:
  - [ ] show banner as red regardless of announcement
  - [ ] if no pertinent alert, say "Trolley tunnel closed (unofficial)"
  - [ ] if pertinent alert, say "Trolley tunnel closed; reopening <time>" where <time> is TBD if no reopening is noted in the alert and time is given if sufficient information
