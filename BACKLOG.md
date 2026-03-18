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
- [x] stops should be a little larger / easier to click on (especially on mobile)
- [x] next-to-arrive logic should improve
  - [x] *priority:* this should be "server side" aka with the cache so that it's available instantly whenever anyone loads the page
  - [x] make sure the "reflection" behavior after a trolley reaches 13th & Market is properly implemented and is accounted for as a westbound trolley arrival at all other stations
  - [x] make sure the fore/aft timing difference is self-consistent, ie the gap in estimated arrival times is based on the time to traverse the fore/aft distance
- [x] 33rd St should be included as a stop for _every_ trolley route
- [x] the T2, T3, T4, T5 should _not_ by the 36th St portal near 36th and Ludlow but should _instead_ pass through a stop at 36th and Sansom (only the T1 passes 36th and Ludlow)
- [x] the T2, T3, T4, T5 should continue to stop at 37th and Spruce

### West terminus trolley behavior
- [ ] on the official app, some trolleys at the western termini are about to embark on eastbound trips
- [ ] figure out which trolleys that have finished a complete loop and are near the western terminus are about to start a new trip
- [ ] before these start, show them as a solid dot that shrinks and expands

### Tunnel entrance and reappearance
- [x] trains move slowly through the yard near the portal; don't mistake this slow motion for tunnel entry
- [x] the "lingering" phenomenon that determines the entry to the tunnel should only happen at the very east end of the yard
- [x] eastbound trolleys that are currently determined to be lingering should linger as a solid dot that shrinks and expands
- [x] the estimated position of a westbound trolley which is underground should _never_ pass west of the actual portal on the east side of the yard
- [x] if the estimated aft position of a westbound trolley is at the portal (which is as far as it should be allowed to go), it should linger as a dashed dot that shrinks and expands

### Diversion logic
- [ ] reexamine the alerts API to see if there's a better way to do this than by keywords
- [ ] if trolleys are _currently in_ the diversion loop:
  - [ ] show banner as red regardless of announcement
  - [ ] if no pertinent alert, say "Trolley tunnel closed (unofficial)"
  - [ ] if pertinent alert, say "Trolley tunnel closed; reopening <time>" where <time> is TBD if no reopening is noted in the alert and time is given if sufficient information

### General
- [ ] make the default opacity 55% instead of 35%
- [ ] show stops for all bus routes (currently shown on some but not all)