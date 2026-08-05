"""Regional Rail: line keying, station naming, run resolution, tracking."""

from __future__ import annotations

import pytest

from pkg.core.rail import (
    ARRIVED_RADIUS_M,
    RailManager,
    RailSchedule,
    _station_key,
    loose_station,
    normalize_station,
)
from pkg.provider.septa.constants import RAIL_ROUTE_CODES, rail_line_key
from pkg.provider.septa.provider import SeptaProvider


# ── Line keying ──────────────────────────────────────────────────────

@pytest.mark.parametrize("line", sorted(RAIL_ROUTE_CODES.values()))
def test_every_line_maps_to_itself(line):
    """TrainView's `line` is the route ID verbatim."""
    assert rail_line_key(line) == line


def test_chestnut_hill_west_is_not_east():
    """Regression: 'che' is a substring of 'chestnut hill west'.

    The old alias scan filed every CHW train under Chestnut Hill East, so
    the line was permanently empty and never appeared in daily_cdfs.json.
    """
    assert rail_line_key("Chestnut Hill West") == "Chestnut Hill West"
    assert rail_line_key("", "Chestnut H West", "") == "Chestnut Hill West"


def test_west_trenton_is_not_trenton():
    """Regression: 'trenton' is a substring of 'west trenton'."""
    assert rail_line_key("West Trenton") == "West Trenton"
    assert rail_line_key("", "West Trenton", "") == "West Trenton"
    assert rail_line_key("Trenton") == "Trenton"


def test_destination_never_overrides_a_known_line():
    """A through-running train is on its current line, not its destination's.

    Train 2388 runs inbound on Wilmington/Newark and continues to West
    Trenton; keying on `dest` would put it on the wrong line's map.
    """
    assert rail_line_key("Wilmington/Newark", "West Trenton", "Marcus Hook") \
        == "Wilmington/Newark"


def test_unknown_line_falls_back_to_destination():
    assert rail_line_key("", "Thorndale", "") == "Paoli/Thorndale"
    assert rail_line_key("", "", "Doylestown") == "Lansdale/Doylestown"


def test_unknown_everything_is_not_silently_reassigned():
    assert rail_line_key("", "", "") == "unknown"


# ── Station names ────────────────────────────────────────────────────

@pytest.mark.parametrize("trainview, gtfs", [
    ("Norristown T.C.",    "Norristown Transit Center"),
    ("Chester TC",         "Chester Transit Center"),
    ("Chestnut H West",    "Chestnut Hill West"),
    ("Wynnefield Avenue",  "Wynnefield Av"),
    ("Highland Avenue",    "Highland Av"),
    ("Richard Allen Lane", "Richard Allen Ln"),
    ("Temple U",           "Temple University"),
    ("Mount Airy",         "Mt Airy"),
    ("Jefferson Station",  "Jefferson Station"),
])
def test_normalizer_folds_trainview_onto_gtfs(trainview, gtfs):
    assert normalize_station(trainview) == normalize_station(gtfs)


@pytest.mark.parametrize("trainview, gtfs", [
    ("Elm St",                  "Norristown Elm Street"),
    ("Prospect Park - Moore",   "Prospect Park"),
    ("Delaware Valley College", "Delaware Valley University"),
    ("30th Street Station",     "Gray 30th St Station"),
    ("30th Street Gray",        "Gray 30th St Station"),
    ("Norristown",              "Norristown Transit Center"),
    ("Trenton",                 "Trenton Transit Center"),
])
def test_aliases_cover_names_no_folding_could_reach(trainview, gtfs):
    """Names SEPTA gives a station that GTFS calls something else."""
    assert _station_key(trainview) == _station_key(gtfs)


def test_trenton_alias_does_not_swallow_west_trenton():
    assert _station_key("West Trenton") != _station_key("Trenton Transit Center")


def test_the_two_north_philadelphias_stay_distinct():
    """"Amtrak" and "Septa" are the only thing telling these two apart.

    They are different stations on different lines, and TrainView names
    them in full, so the normalizer must not treat the operator word as
    noise the way it does "Station".
    """
    assert normalize_station("North Philadelphia Amtrak") != \
        normalize_station("North Philadelphia Septa")
    assert normalize_station("North Philadelphia Amtrak") == \
        normalize_station("North Philadelphia Amtrak Station")


def test_loose_matching_ignores_the_operating_railroad():
    """TrainView calls the Chestnut Hill West stop "North Philadelphia
    Amtrak", which is the Trenton line's station.  Dropping the operator
    is the second-pass fallback that still resolves it."""
    assert loose_station("North Philadelphia Amtrak") == \
        loose_station("North Philadelphia Septa")


def _schedule_with_lines():
    return RailSchedule(lines={
        "Trenton": {"stations": [
            {"name": "Temple University", "lat": 39.98, "lng": -75.15},
            {"name": "North Philadelphia Amtrak", "lat": 39.99, "lng": -75.15},
            {"name": "Bridesburg", "lat": 40.00, "lng": -75.07},
        ]},
        "Chestnut Hill West": {"stations": [
            {"name": "Temple University", "lat": 39.98, "lng": -75.15},
            {"name": "North Philadelphia Septa", "lat": 40.00, "lng": -75.16},
            {"name": "Queen Ln", "lat": 40.02, "lng": -75.19},
        ]},
    })


def test_lookups_are_scoped_to_one_line():
    """A station is only ever resolved against the line it belongs to."""
    sched = _schedule_with_lines()
    assert sched.line_position("Trenton", "North Philadelphia Amtrak") == 1
    assert sched.line_position("Chestnut Hill West", "North Philadelphia Septa") == 1
    # Stations of other lines never leak in.
    assert sched.line_position("Trenton", "Queen Ln") is None
    assert sched.line_position("Chestnut Hill West", "Bridesburg") is None


def test_wrong_operator_still_resolves_within_a_line():
    """SEPTA labels the Chestnut Hill West stop with the Amtrak name.

    Scoping to one line leaves only one candidate, so the loose match is
    unambiguous even though the two names collide globally.
    """
    sched = _schedule_with_lines()
    assert sched.line_position("Chestnut Hill West", "North Philadelphia Amtrak") == 1


# ── Fixtures: train 2591, a real through-running run ─────────────────
# Norristown TC → Suburban Station on Manayunk/Norristown, then
# Suburban Station → Malvern on Paoli/Thorndale.  Trimmed to the ends of
# each leg plus the junction.

STATIONS = [
    {"id": "90801", "name": "Norristown Transit Center", "lat": 40.1155, "lng": -75.3430},
    {"id": "90802", "name": "Conshohocken",              "lat": 40.0720, "lng": -75.3020},
    {"id": "90005", "name": "Suburban Station",          "lat": 39.9540, "lng": -75.1670},
    {"id": "90501", "name": "Paoli",                     "lat": 40.0425, "lng": -75.4841},
    {"id": "90502", "name": "Malvern",                   "lat": 40.0355, "lng": -75.5150},
]

SERVICES = {
    "WEEKDAY": {"dow": [1, 1, 1, 1, 1, 0, 0],
                "start": "20260726", "end": "20260829", "added": [], "removed": []},
    "SATURDAY": {"dow": [0, 0, 0, 0, 0, 1, 0],
                 "start": "20260726", "end": "20260829", "added": [], "removed": []},
    "TRACKWORK": {"dow": [0, 0, 0, 0, 0, 0, 0],
                  "start": "", "end": "", "added": ["20260812"], "removed": []},
}

RUNS = {
    "2591": [
        {"route": "Manayunk/Norristown", "service": "WEEKDAY", "outbound": False,
         "headsign": "Center City Philadelphia",
         "stops": [[0, 1011.0], [1, 1030.0], [2, 1056.0]]},
        {"route": "Paoli/Thorndale", "service": "WEEKDAY", "outbound": True,
         "headsign": "Malvern",
         "stops": [[2, 1056.0], [3, 1114.0], [4, 1120.0]]},
    ],
    # A short-turn: ends at Paoli, not at the end of the line.
    "515": [
        {"route": "Paoli/Thorndale", "service": "WEEKDAY", "outbound": True,
         "headsign": "Paoli",
         "stops": [[2, 900.0], [3, 940.0]]},
    ],
    "9001": [
        {"route": "Paoli/Thorndale", "service": "SATURDAY", "outbound": True,
         "headsign": "Malvern",
         "stops": [[2, 600.0], [4, 640.0]]},
    ],
}


def _schedule():
    return RailSchedule(
        {"feed_start": "20260726", "feed_end": "20260829",
         "stations": STATIONS, "services": SERVICES, "runs": RUNS},
        lines={"Paoli/Thorndale": {"stations": STATIONS[2:]},
               "Manayunk/Norristown": {"stations": list(reversed(STATIONS[:3]))}},
    )


WEDNESDAY = "2026-08-05"
SATURDAY = "2026-08-08"


# ── Service resolution ───────────────────────────────────────────────

def test_active_services_by_weekday():
    sched = _schedule()
    assert sched.active_services(WEDNESDAY) == {"WEEKDAY"}
    assert sched.active_services(SATURDAY) == {"SATURDAY"}


def test_added_date_activates_an_out_of_window_service():
    """Overlay services run only on their explicit dates."""
    sched = _schedule()
    assert "TRACKWORK" not in sched.active_services(WEDNESDAY)
    assert "TRACKWORK" in sched.active_services("2026-08-12")


def test_run_for_ignores_legs_of_inactive_services():
    sched = _schedule()
    assert sched.run_for("9001", WEDNESDAY) is None
    assert len(sched.run_for("9001", SATURDAY)) == 1


def test_covers_reports_the_feed_window():
    sched = _schedule()
    assert sched.covers(WEDNESDAY)
    assert not sched.covers("2026-09-15")


# ── Journey assembly ─────────────────────────────────────────────────

def test_through_run_becomes_one_journey():
    sched = _schedule()
    journey = sched.journey(sched.run_for("2591", WEDNESDAY))
    names = [s["name"] for s in journey]
    assert names == ["Norristown Transit Center", "Conshohocken",
                     "Suburban Station", "Paoli", "Malvern"]


def test_junction_station_appears_once():
    """Suburban Station ends one leg and begins the next."""
    sched = _schedule()
    journey = sched.journey(sched.run_for("2591", WEDNESDAY))
    assert [s["name"] for s in journey].count("Suburban Station") == 1


def test_junction_belongs_to_the_outgoing_line():
    sched = _schedule()
    journey = sched.journey(sched.run_for("2591", WEDNESDAY))
    junction = next(s for s in journey if s["name"] == "Suburban Station")
    assert junction["route"] == "Paoli/Thorndale"
    assert junction["outbound"] is True


# ── Live tracking ────────────────────────────────────────────────────

class FakeStats:
    def __init__(self):
        self.starts, self.finishes = [], []

    def record_start(self, route, start_ms):
        self.starts.append((route, start_ms))

    def record_finish(self, route, start_ms, **kw):
        self.finishes.append((route, start_ms, kw))


def _manager():
    stats = FakeStats()
    return RailManager(_schedule(), stats=stats), stats


def _train(current, nxt, lat, lng, **meta):
    base = {"delay": 0, "headsign": "Malvern", "source": "Norristown TC",
            "line": "Manayunk/Norristown", "current_stop": current,
            "next_stop": nxt, "track": "1", "api_bearing": "90"}
    base.update(meta)
    return {"vehicle_id": "2591", "route_id": "Manayunk/Norristown",
            "label": "2591", "lat": lat, "lng": lng, "meta": base}


def test_train_reports_its_own_run_not_the_line():
    mgr, _ = _manager()
    out = mgr.update([_train("Norristown T.C.", "Conshohocken", 40.1155, -75.3430)])[0]
    assert out["origin"] == "Norristown Transit Center"
    assert out["destination"] == "Malvern"
    assert out["progress"]["stops_total"] == 4
    assert out["scheduled"] is True


def test_direction_comes_from_the_current_leg():
    """Inbound on Manayunk/Norristown, then outbound on Paoli/Thorndale."""
    mgr, _ = _manager()
    inbound = mgr.update([_train("Conshohocken", "Suburban Station", 40.0720, -75.3020)])[0]
    assert inbound["toward_destination"] is False
    assert inbound["route_id"] == "Manayunk/Norristown"

    outbound = mgr.update([_train("Paoli", "Malvern", 40.0425, -75.4841)])[0]
    assert outbound["toward_destination"] is True
    assert outbound["route_id"] == "Paoli/Thorndale"


def test_via_names_the_onward_line_only_while_off_it():
    mgr, _ = _manager()
    early = mgr.update([_train("Norristown T.C.", "Conshohocken", 40.1155, -75.3430)])[0]
    assert early["meta"]["via"] == "Paoli/Thorndale"

    late = mgr.update([_train("Paoli", "Malvern", 40.0425, -75.4841)])[0]
    assert late["meta"]["via"] == ""


def test_progress_is_relative_to_the_run():
    mgr, _ = _manager()
    out = mgr.update([_train("Suburban Station", "Paoli", 39.9540, -75.1670)])[0]
    assert out["progress"]["stops_passed"] == 2
    assert out["progress"]["stops_total"] == 4
    assert out["progress"]["next_stop"] == "Paoli"


def test_standing_at_a_station_looks_ahead_to_the_next_one():
    """TrainView repeats the station as both current and next while a
    train is stopped at it.  Reporting it as the next stop would say the
    train is heading where it already is."""
    mgr, _ = _manager()
    out = mgr.update([_train("Suburban Station", "Suburban Station", 39.9540, -75.1670)])[0]
    assert out["progress"]["current_stop"] == "Suburban Station"
    assert out["progress"]["next_stop"] == "Paoli"


def test_express_skip_is_preserved():
    """An express's next stop is not always the adjacent station."""
    mgr, _ = _manager()
    out = mgr.update([_train("Conshohocken", "Paoli", 40.0720, -75.3020)])[0]
    assert out["progress"]["current_stop"] == "Conshohocken"
    assert out["progress"]["next_stop"] == "Paoli"
    assert out["progress"]["stops_passed"] == 1


def test_backward_pair_is_reconciled_against_position():
    """A stale field can name a station the train has already left."""
    mgr, _ = _manager()
    # Reported at Malvern (the end) but next is Conshohocken (near the
    # start); the coordinates put it at Conshohocken.
    out = mgr.update([_train("Malvern", "Conshohocken", 40.0720, -75.3020)])[0]
    assert out["progress"]["next_stop"] == "Conshohocken"
    assert out["progress"]["stops_passed"] == 0


def test_unresolvable_names_fall_back_to_position():
    mgr, _ = _manager()
    out = mgr.update([_train("Nowhere", "Nowhere Else", 40.0355, -75.5150)])[0]
    assert out["progress"]["current_stop"] == "Malvern"


def test_short_turn_counts_against_its_own_terminus():
    """A Paoli train has 1 stop to make, not the full line's 3."""
    mgr, _ = _manager()
    v = {"vehicle_id": "515", "route_id": "Paoli/Thorndale", "label": "515",
         "lat": 39.9540, "lng": -75.1670,
         "meta": {"delay": 0, "headsign": "Paoli", "current_stop": "Suburban Station",
                  "next_stop": "Paoli", "line": "Paoli/Thorndale"}}
    out = mgr.update([v])[0]
    assert out["destination"] == "Paoli"
    assert out["progress"]["stops_total"] == 1


# ── Stats ────────────────────────────────────────────────────────────

def test_no_start_recorded_while_sitting_at_the_origin():
    """First sighting is not a departure — the old code recorded it as one."""
    mgr, stats = _manager()
    mgr.update([_train("Norristown T.C.", "Conshohocken", 40.1155, -75.3430)])
    assert stats.starts == []


def test_start_recorded_once_the_train_leaves_the_origin():
    mgr, stats = _manager()
    mgr.update([_train("Norristown T.C.", "Conshohocken", 40.1155, -75.3430)])
    mgr.update([_train("Conshohocken", "Suburban Station", 40.0720, -75.3020)])
    assert len(stats.starts) == 1
    assert stats.starts[0][0] == "Manayunk/Norristown"

    # Still only one after further polls.
    mgr.update([_train("Suburban Station", "Paoli", 39.9540, -75.1670)])
    assert len(stats.starts) == 1


def test_finish_recorded_on_arrival_with_a_duration():
    mgr, stats = _manager()
    mgr.update([_train("Norristown T.C.", "Conshohocken", 40.1155, -75.3430)])
    mgr.update([_train("Conshohocken", "Suburban Station", 40.0720, -75.3020)])
    mgr.update([_train("Malvern", "", 40.0355, -75.5150)])

    assert len(stats.finishes) == 1
    route, start_ms, kw = stats.finishes[0]
    assert route == "Manayunk/Norristown"
    assert start_ms is not None
    assert kw["stops_passed"] == 4
    assert kw["fraction_stops_passed"] == 1.0
    assert kw["elapsed_seconds"] >= 0

    # Arrival is recorded once, not on every subsequent poll.
    mgr.update([_train("Malvern", "", 40.0355, -75.5150)])
    assert len(stats.finishes) == 1


def test_arrival_radius_is_the_documented_constant():
    assert ARRIVED_RADIUS_M == 500


# ── Fallback tier ────────────────────────────────────────────────────

def _unscheduled(current, nxt):
    return {"vehicle_id": "8888", "route_id": "Paoli/Thorndale", "label": "8888",
            "lat": 40.0425, "lng": -75.4841,
            "meta": {"delay": 3, "headsign": "Thorndale", "source": "Malvern",
                     "line": "Paoli/Thorndale", "current_stop": current,
                     "next_stop": nxt}}


def test_unscheduled_train_still_reports_what_is_known():
    """A train absent from the timetable, or a feed published early."""
    mgr, _ = _manager()
    out = mgr.update([_unscheduled("Suburban Station", "Paoli")])[0]
    assert out["scheduled"] is False
    assert out["route_id"] == "Paoli/Thorndale"
    assert out["destination"] == "Thorndale"
    assert out["progress"]["next_stop"] == "Paoli"


def test_unscheduled_train_does_not_invent_a_stop_count():
    """Counting against the whole line would overstate a short-turn's journey."""
    mgr, stats = _manager()
    out = mgr.update([_unscheduled("Suburban Station", "Paoli")])[0]
    assert out["progress"]["stops_total"] is None
    assert out["progress"]["stops_passed"] is None
    # Nothing is recorded for a run we cannot measure.
    assert stats.starts == []


def test_unscheduled_direction_from_the_line_sequence():
    mgr, _ = _manager()
    out = mgr.update([_unscheduled("Suburban Station", "Paoli")])[0]
    assert out["toward_destination"] is True

    back = mgr.update([_unscheduled("Paoli", "Suburban Station")])[0]
    assert back["toward_destination"] is False


# ── Provider normalization ───────────────────────────────────────────

def _raw(**kw):
    base = {"trainno": "2591", "line": "Paoli/Thorndale", "dest": "Malvern",
            "lat": "40.0425", "lon": "-75.4841", "late": 0,
            "currentstop": "Paoli", "nextstop": "Malvern",
            "TRACK": "2", "consist": "451,452", "service": "LOCAL",
            "SOURCE": "Norristown TC", "heading": "270"}
    base.update(kw)
    return base


def test_normalize_rail_keeps_the_station_fields():
    v = SeptaProvider()._normalize_rail(_raw())
    assert v["meta"]["current_stop"] == "Paoli"
    assert v["meta"]["next_stop"] == "Malvern"
    assert v["meta"]["track"] == "2"
    assert v["meta"]["consist"] == "451,452"


def test_normalize_rail_strips_a_trailing_period_from_the_train_number():
    """SEPTA emits e.g. "3537." — which otherwise matches no GTFS run."""
    assert SeptaProvider()._normalize_rail(_raw(trainno="3537."))["vehicle_id"] == "3537"


def test_normalize_rail_drops_schedule_based_entries():
    assert SeptaProvider()._normalize_rail(_raw(late=998)) is None


@pytest.mark.parametrize("lat, lon", [("0", "-75.48"), ("40.04", "0"), ("0", "0")])
def test_normalize_rail_drops_missing_gps(lat, lon):
    """A zero coordinate means "no fix", and used to render at null island."""
    assert SeptaProvider()._normalize_rail(_raw(lat=lat, lon=lon)) is None
