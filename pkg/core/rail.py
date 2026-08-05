"""Regional Rail run tracking.

Rail is deliberately *not* routed through TripManager.  A Trip reconstructs
direction, stop order and termini from raw GPS because that is all SEPTA's
TransitView gives us, and it assumes one fixed stop sequence and one shape
per route.  Neither holds for rail: TrainView names the current and next
station outright, and a line carries runs with different termini (a
Paoli/Thorndale train may end at Thorndale, Malvern, Paoli or Wayne).

Instead each live train is joined to its scheduled run.  GTFS
``trip_short_name`` is the route code followed by the train number —
"PAO2591" — and that number is TrainView's ``trainno``, so the join is
exact.  From the run we get the stop list *for that train*, its true
origin and destination, its direction, and a scheduled time per station.

Through-running is the reason a run has legs rather than one stop list.
Train 2591 runs Norristown TC → Suburban Station on Manayunk/Norristown,
then Suburban Station → Malvern on Paoli/Thorndale.  TrainView reports
``line`` for the leg the train is on *now* and ``dest`` for the end of the
whole run, which is why a Wilmington/Newark train can legitimately show a
destination of West Trenton.  Keeping legs separate lets a train appear on
the line it is actually on while still naming where it finally ends up.

When the join fails — an extra not in the timetable, or a feed published
ahead of its effective date — ``_resolve_fallback`` degrades to the line's
own station list and reports only what it can actually establish.
"""

from __future__ import annotations

import re
import threading
import time
from datetime import datetime

from ..geo import distance
from ..helpers import BASE, date_str, load

# Distance from the origin station beyond which a train counts as having
# departed.  SEPTA's rail GPS wanders by a block or so while a train sits
# at a platform; 400 m clears that without waiting for the next station,
# which on the outer ends of a line can be several minutes away.
DEPARTED_RADIUS_M = 400

# A train is dropped from the live set after this long without an update.
# TrainView keeps terminated runs in the feed briefly, and trains lose GPS
# in cuttings and under Center City, so this is deliberately generous.
STALE_AFTER_S = 600

# How close a train must be to its final station to count as arrived.
ARRIVED_RADIUS_M = 500


# ── Station name resolution ──────────────────────────────────────────
# TrainView and GTFS spell the same station differently: "Norristown
# T.C." vs "Norristown Transit Center", "Chestnut H West" vs "Chestnut
# Hill West", "Highland Avenue" vs "Highland Av".  Normalizing both sides
# resolves 58 of the 64 distinct names TrainView actually emits; the rest
# need the alias table below.

_ABBREV = (
    (r'\bt c\b|\btc\b|\btransit cent(?:er|re)\b', 'tc'),
    (r'\bave?nue\b|\bav\b',                       'av'),
    (r'\bstreet\b|\bst\b',                        'st'),
    (r'\blane\b|\bln\b',                          'ln'),
    (r'\bjunction\b|\bjct\b',                     'jct'),
    (r'\buniversity\b|\bu\b',                     'univ'),
    (r'\bhill\b|\bh\b',                           'hill'),
    (r'\bmount\b|\bmt\b',                         'mount'),
)

# Words that appear on one side only and carry no distinguishing meaning.
_NOISE = re.compile(r'\bstation\b|\bregional rail\b')

# The operating railroad, which only sometimes appears.  It distinguishes
# North Philadelphia Amtrak (Trenton) from North Philadelphia Septa
# (Chestnut Hill West) — but TrainView calls the Chestnut Hill West stop
# "North Philadelphia Amtrak" too, so a match on the full name is tried
# first and this is dropped only as a second pass.
_OPERATOR = re.compile(r'\bamtrak\b|\bsepta\b')


def normalize_station(name: str) -> str:
    """Fold a station name to a comparable key."""
    s = (name or '').lower().strip()
    s = s.replace('.', ' ').replace(',', ' ').replace('-', ' ').replace("'", '')
    s = _NOISE.sub(' ', s)
    for pattern, repl in _ABBREV:
        s = re.sub(pattern, repl, s)
    return re.sub(r'\s+', ' ', s).strip()


def loose_station(name: str) -> str:
    """As normalize_station, but also ignoring the operating railroad."""
    return re.sub(r'\s+', ' ', _OPERATOR.sub(' ', normalize_station(name))).strip()


# TrainView names with no normalized counterpart in GTFS.  Each is a name
# SEPTA uses for a station that GTFS calls something else entirely, so no
# amount of folding will connect them.
_STATION_ALIASES = {
    # GTFS calls 30th Street "Gray 30th St Station"; TrainView uses three
    # different spellings, none of which contain "gray" in the same place.
    '30th st':          'gray 30th st',
    '30th st gray':     'gray 30th st',
    'gray 30th st':     'gray 30th st',
    # TrainView drops the "Transit Center" suffix on these two.
    'norristown':       'norristown tc',
    'trenton':          'trenton tc',
    # TrainView reports the airport as a single station; GTFS splits it
    # into four terminal stops, the outermost of which is the terminus.
    'airport':          'airport terminals e & f',
    # TrainView shortens one and adds a local qualifier to the other.
    'elm st':           'norristown elm st',
    'prospect park moore': 'prospect park',
    # The school became a university in 2015; TrainView still says college.
    'delaware valley college': 'delaware valley univ',
}


def _station_key(name: str) -> str:
    key = normalize_station(name)
    return _STATION_ALIASES.get(key, key)


def _loose_key(name: str) -> str:
    key = loose_station(name)
    return _STATION_ALIASES.get(key, key)


# ── Scheduled runs ───────────────────────────────────────────────────

class RailSchedule:
    """The scheduled rail runs, loaded from static/rail_schedule.json."""

    def __init__(self, data: dict | None = None, lines: dict | None = None):
        data = data or {}
        self.feed_start = data.get('feed_start', '')
        self.feed_end = data.get('feed_end', '')
        self.stations = data.get('stations', [])
        self.services = data.get('services', {})
        self.runs = data.get('runs', {})
        self.lines = lines or {}

        # Per line, name → index maps over its own stations, in both the
        # exact and operator-insensitive forms (see _find_station).
        self._line_index = {}
        self._line_loose = {}
        for route_id, info in self.lines.items():
            names = [s['name'] for s in info.get('stations', [])]
            self._line_index[route_id] = {_station_key(n): i
                                          for i, n in enumerate(names)}
            self._line_loose[route_id] = {_loose_key(n): i
                                          for i, n in enumerate(names)}

    @classmethod
    def load(cls, base=BASE):
        """Load from static/, returning an empty schedule if absent."""
        data = load(base / 'static' / 'rail_schedule.json')
        lines = load(base / 'static' / 'rail_lines.json')
        sched = cls(data, lines)
        if not sched.runs:
            print("  [rail] no rail_schedule.json — falling back to line rules")
        elif not sched.covers(date_str()):
            print(f"  [rail] schedule covers {sched.feed_start}–{sched.feed_end}, "
                  f"not today — run scripts/build_gtfs.py")
        return sched

    def covers(self, day: str) -> bool:
        """True if `day` (YYYY-MM-DD) is inside the loaded feed's window."""
        if not self.feed_start or not self.feed_end:
            return False
        return self.feed_start <= day.replace('-', '') <= self.feed_end

    def active_services(self, day: str) -> set[str]:
        """Service IDs running on `day` (YYYY-MM-DD).

        Resolved per date rather than per weekday because SEPTA overlays
        short-window services (track work, holidays) on top of the base
        ones; on those dates the same train number appears under two
        service IDs with different stops.
        """
        compact = day.replace('-', '')
        dow = datetime.strptime(day, '%Y-%m-%d').weekday()
        active = set()
        for sid, svc in self.services.items():
            if compact in svc.get('removed', ()):
                continue
            if compact in svc.get('added', ()):
                active.add(sid)
                continue
            start, end = svc.get('start', ''), svc.get('end', '')
            if start and end and not (start <= compact <= end):
                continue
            dows = svc.get('dow') or []
            if len(dows) == 7 and dows[dow]:
                active.add(sid)
        return active

    def run_for(self, train_no: str, day: str) -> list[dict] | None:
        """Return `train_no`'s legs for `day`, in travel order.

        Legs whose service isn't running are dropped.  If several
        services survive (overlapping windows the calendar can't
        separate), the one contributing the most legs wins.
        """
        legs = self.runs.get(str(train_no))
        if not legs:
            return None
        active = self.active_services(day)
        usable = [leg for leg in legs if leg.get('service') in active]
        if not usable:
            return None

        by_service = {}
        for leg in usable:
            by_service.setdefault(leg['service'], []).append(leg)
        best = max(by_service.values(), key=len)
        return sorted(best, key=lambda leg: leg['stops'][0][1])

    def journey(self, legs: list[dict]) -> list[dict]:
        """Flatten legs into one ordered station list for the whole run.

        Consecutive legs share the station where the train changes lines
        (Suburban Station for train 2591), which is listed as the arrival
        of one leg and the departure of the next.  It is emitted once,
        tagged with the line the train is on when it leaves.
        """
        out = []
        for leg in legs:
            for idx, minute in leg['stops']:
                station = self.stations[idx]
                if out and out[-1]['index'] == idx:
                    # Junction between two legs: keep the earlier arrival
                    # time, but hand the station to the outgoing line.
                    out[-1]['route'] = leg['route']
                    out[-1]['outbound'] = leg['outbound']
                    continue
                out.append({
                    'index': idx,
                    'name': station['name'],
                    'lat': station['lat'],
                    'lng': station['lng'],
                    'minute': minute,
                    'route': leg['route'],
                    'outbound': leg['outbound'],
                })
        return out

    def line_stations(self, route_id: str) -> list[dict]:
        """The full ordered station list for a line (inbound → outbound end)."""
        return self.lines.get(route_id, {}).get('stations', [])

    def line_position(self, route_id: str, name: str) -> int | None:
        """Index of `name` within a line's stations, or None."""
        if not name:
            return None
        idx = self._line_index.get(route_id, {}).get(_station_key(name))
        if idx is not None:
            return idx
        return self._line_loose.get(route_id, {}).get(_loose_key(name))


# ── Live trains ──────────────────────────────────────────────────────

class RailRun:
    """One live train, tracked across polls."""

    __slots__ = ('train_no', 'route_id', 'journey', 'scheduled', 'first_seen',
                 'last_seen', 'start_ms', 'departed', 'finished', 'origin',
                 'destination', 'stops_total', 'recorded_route')

    def __init__(self, train_no, now=None):
        self.train_no = train_no
        self.route_id = ''
        self.journey = []          # full station list for the run, or []
        self.scheduled = False     # True when matched to a GTFS run
        self.first_seen = now if now is not None else time.time()
        self.last_seen = self.first_seen
        self.start_ms = None       # departure from origin, once observed
        self.departed = False
        self.finished = False
        self.origin = ''
        self.destination = ''
        self.stops_total = None
        self.recorded_route = ''   # route the start was filed under


class RailManager:
    """Tracks live trains and enriches them with their scheduled run.

    Mirrors TripManager's role for rail: the poller hands it each poll's
    vehicles and it returns enriched dicts shaped like transit trips, so
    the frontend renders both with the same code.
    """

    def __init__(self, schedule: RailSchedule, stats=None):
        self._schedule = schedule
        self._runs: dict[str, RailRun] = {}
        self._lock = threading.Lock()
        # Injected so tests can observe recording without touching disk.
        if stats is None:
            from . import stats as stats_module
            stats = stats_module
        self._stats = stats

    # -- polling ------------------------------------------------------

    def update(self, vehicles: list[dict]) -> list[dict]:
        """Enrich a poll's rail vehicles and record start/finish stats."""
        now = time.time()
        day = date_str()
        enriched = []
        with self._lock:
            seen = set()
            for v in vehicles:
                train_no = v.get('vehicle_id', '')
                if not train_no:
                    continue
                seen.add(train_no)
                run = self._runs.get(train_no)
                if run is None:
                    run = RailRun(train_no, now)
                    self._runs[train_no] = run
                run.last_seen = now
                enriched.append(self._enrich(run, v, day, now))
            self._prune(seen, now)
        return enriched

    def _prune(self, seen, now):
        """Drop trains that have gone quiet.  Caller holds the lock."""
        for train_no, run in list(self._runs.items()):
            if train_no not in seen and (now - run.last_seen) > STALE_AFTER_S:
                del self._runs[train_no]

    # -- enrichment ---------------------------------------------------

    def _enrich(self, run: RailRun, v: dict, day: str, now: float) -> dict:
        meta = v.get('meta') or {}
        lat, lng = v.get('lat'), v.get('lng')

        if not run.journey:
            self._attach_run(run, v, day)

        if run.journey:
            state = self._resolve_scheduled(run, meta, lat, lng)
        else:
            state = self._resolve_fallback(run, v, meta)

        self._track_progress(run, state, lat, lng, now)

        delay = meta.get('delay', 0)
        return {
            'vehicle_id': run.train_no,
            'trip_id': f'{run.train_no}_{int(run.first_seen)}',
            'route_id': state['route_id'],
            'label': v.get('label', run.train_no),
            'lat': lat,
            'lng': lng,
            'position': {
                'lat': lat,
                'lng': lng,
                'heading': _to_float(meta.get('api_bearing')),
            },
            'progress': {
                'current_stop': state['current_stop'],
                'next_stop': state['next_stop'],
                'stops_passed': state['stops_passed'],
                'stops_total': state['stops_total'],
                'delay_minutes': delay,
            },
            'origin': run.origin,
            'destination': run.destination,
            'toward_destination': state['outbound'],
            # Unix seconds, matching the transit trip payload.
            'start_time': run.start_ms / 1000 if run.start_ms else None,
            'scheduled': run.scheduled,
            'meta': dict(meta, via=state['via'], scheduled_start=state['scheduled_start']),
        }

    def _attach_run(self, run: RailRun, v: dict, day: str):
        """Bind the train to its scheduled run, once."""
        legs = self._schedule.run_for(run.train_no, day)
        if not legs:
            run.route_id = v.get('route_id', '')
            return
        run.journey = self._schedule.journey(legs)
        run.scheduled = True
        run.origin = run.journey[0]['name']
        run.destination = run.journey[-1]['name']
        run.stops_total = len(run.journey) - 1

    def _resolve_scheduled(self, run: RailRun, meta, lat, lng) -> dict:
        """Locate a train within its own run's station list."""
        journey = run.journey
        keys = [_station_key(s['name']) for s in journey]
        loose = [_loose_key(s['name']) for s in journey]

        pos = _find_station(keys, loose, meta.get('current_stop', ''))
        nxt = _find_station(keys, loose, meta.get('next_stop', ''))

        if pos is not None and nxt is not None:
            if nxt == pos:
                # TrainView reports both as the same station while the
                # train is standing at it, about to depart.
                nxt = pos + 1
            elif nxt < pos:
                # One of the two is stale.  Believe whichever the train is
                # actually near, and derive the other from it.
                if _nearest_index(journey, lat, lng, [pos, nxt]) == nxt:
                    pos = max(0, nxt - 1)
                else:
                    nxt = pos + 1
            # nxt > pos is left alone: an express legitimately skips the
            # stations in between, so next is not always pos + 1.
        elif pos is None and nxt is not None:
            pos = max(0, nxt - 1)
        elif pos is not None and nxt is None:
            nxt = pos + 1
        else:
            pos = _nearest_index(journey, lat, lng)
            nxt = None if pos is None else pos + 1

        pos = 0 if pos is None else max(0, min(pos, len(journey) - 1))
        current = journey[pos]
        next_station = journey[nxt] if nxt is not None and 0 <= nxt < len(journey) else None

        # The line the train is on now is the leg that owns its current
        # station — not the run's final destination's line.
        route_id = current['route']
        final_route = journey[-1]['route']
        via = final_route if final_route != route_id else ''

        return {
            'route_id': route_id,
            'outbound': bool(current['outbound']),
            'current_stop': current['name'],
            'next_stop': next_station['name'] if next_station else None,
            'stops_passed': pos,
            'stops_total': run.stops_total,
            'scheduled_start': journey[0]['minute'],
            'via': via,
        }

    def _resolve_fallback(self, run: RailRun, v, meta) -> dict:
        """Best effort for a train with no scheduled run.

        Everything here comes from TrainView alone, so stop counts are
        left unset rather than measured against the full line — a
        short-turn would otherwise report progress it never makes.
        """
        route_id = v.get('route_id', '')
        current = meta.get('current_stop') or ''
        nxt = meta.get('next_stop') or ''
        run.destination = run.destination or (meta.get('headsign') or '')
        run.origin = run.origin or (meta.get('source') or '')

        outbound = None
        ci = self._schedule.line_position(route_id, current)
        ni = self._schedule.line_position(route_id, nxt)
        if ci is not None and ni is not None and ci != ni:
            # Line stations run inbound-end → outbound-end, so an
            # increasing index is an outbound train.
            outbound = ni > ci

        return {
            'route_id': route_id,
            'outbound': outbound,
            'current_stop': current or None,
            'next_stop': nxt or None,
            'stops_passed': None,
            'stops_total': None,
            'scheduled_start': None,
            'via': '',
        }

    # -- stats --------------------------------------------------------

    def _track_progress(self, run: RailRun, state, lat, lng, now):
        """Record the run's departure and arrival exactly once each.

        A start is the moment the train is first seen to have left its
        origin.  The previous implementation recorded first *sighting*,
        which stamped every train in a poll with the same minute — after
        a restart the whole fleet landed on one timestamp — and never
        recorded a finish at all, so no rail trip ever had a duration.
        """
        if run.finished or not run.journey:
            return

        if not run.departed:
            origin = run.journey[0]
            away = (state['stops_passed'] or 0) > 0
            if not away and lat and lng:
                away = distance(lat, lng, origin['lat'], origin['lng']) > DEPARTED_RADIUS_M
            if away:
                run.departed = True
                run.start_ms = int(now * 1000)
                run.recorded_route = state['route_id']
                self._stats.record_start(run.recorded_route, run.start_ms)
            return

        final = run.journey[-1]
        at_end = (state['stops_passed'] or 0) >= len(run.journey) - 1
        if not at_end and lat and lng:
            at_end = distance(lat, lng, final['lat'], final['lng']) < ARRIVED_RADIUS_M
        if at_end:
            run.finished = True
            passed = run.stops_total or 0
            self._stats.record_finish(
                run.recorded_route, run.start_ms,
                elapsed_seconds=round(now - run.start_ms / 1000, 1),
                stops_passed=passed,
                fraction_stops_passed=1.0 if passed else None,
            )

    # -- introspection ------------------------------------------------

    def snapshot(self) -> dict:
        with self._lock:
            return {
                'tracked': len(self._runs),
                'scheduled': sum(1 for r in self._runs.values() if r.scheduled),
                'departed': sum(1 for r in self._runs.values() if r.departed),
            }


# ── helpers ──────────────────────────────────────────────────────────

def _to_float(val):
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _find_station(keys, loose, name):
    """Index of `name` among a journey's stations, or None.

    The exact name is tried first so that a run traversing both North
    Philadelphias keeps them apart; the operator-insensitive form is a
    fallback for the stops SEPTA labels with the wrong railroad.
    """
    if not name:
        return None
    key = _station_key(name)
    if key in keys:
        return keys.index(key)
    lk = _loose_key(name)
    return loose.index(lk) if lk in loose else None


def _nearest_index(journey, lat, lng, candidates=None):
    """Index of the journey station closest to a coordinate."""
    if lat is None or lng is None:
        return None
    pool = candidates if candidates is not None else range(len(journey))
    best, best_d = None, None
    for i in pool:
        if not 0 <= i < len(journey):
            continue
        d = distance(lat, lng, journey[i]['lat'], journey[i]['lng'])
        if best_d is None or d < best_d:
            best, best_d = i, d
    return best
