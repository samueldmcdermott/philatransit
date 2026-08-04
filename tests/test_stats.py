"""Tests for pkg.core.stats — start-time persistence, filtering, and rollover.

stats owns the only data the app cannot regenerate, so these tests pin down
both the on-disk schema and the read-time filtering that feeds the CDFs.
"""

import json
from datetime import datetime, timedelta

import pytest

from pkg.core import stats


def ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def at(hour, minute=0, second=0, *, days_ago=0):
    """A ms timestamp at a wall-clock time today (or N days ago), local time."""
    d = datetime.now() - timedelta(days=days_ago)
    return ms(d.replace(hour=hour, minute=minute, second=second, microsecond=0))


@pytest.fixture(autouse=True)
def isolated_stats(tmp_path, monkeypatch):
    """Point stats at a scratch directory and clear the in-memory cache."""
    monkeypatch.setattr(stats, "TODAY", tmp_path / "today.json")
    monkeypatch.setattr(stats, "DAILY_CDFS", tmp_path / "daily_cdfs.json")
    stats.reset_cache()
    yield tmp_path
    stats.reset_cache()


def read_today():
    stats.flush()
    return json.loads(stats.TODAY.read_text())


class TestEntryMinute:
    """_entry_minute must read all three schema generations that today.json
    has carried, since old files are never migrated."""

    def test_bare_float_legacy(self):
        assert stats._entry_minute(543.21) == 543.21

    def test_current_schema_minutes(self):
        assert stats._entry_minute({"start": 543.21}) == 543.21

    def test_legacy_ms_timestamp_is_converted(self):
        """Values above the 10000 heuristic are epoch-ms, not minutes."""
        t = at(9, 30)
        assert stats._entry_minute({"start": t}) == pytest.approx(570.0, abs=0.02)

    def test_boundary_below_heuristic_is_minutes(self):
        # 1439 = 23:59, the largest legitimate minute-of-day.
        assert stats._entry_minute({"start": 1439}) == 1439

    def test_end_only_entry(self):
        assert stats._entry_minute({"end": at(6, 0)}) == pytest.approx(360.0, abs=0.02)

    def test_unrecognized_dict_returns_none(self):
        assert stats._entry_minute({"nothing": 1}) is None


class TestValidityFilters:
    @pytest.mark.parametrize("frac,expected", [
        (0.0, True),
        (0.05, True),
        (0.1, False),    # boundary: >= GHOST_STOP_FRACTION is not a ghost
        (0.5, False),
        (1.0, False),
    ])
    def test_ghost_boundary(self, frac, expected):
        e = {"start": 100, "elapsed_seconds": 60, "fraction_stops_passed": frac}
        assert stats._is_ghost(e) is expected

    def test_in_flight_entry_is_never_a_ghost(self):
        """No elapsed_seconds means the trip hasn't retired yet."""
        e = {"start": 100, "elapsed_seconds": None, "fraction_stops_passed": 0.01}
        assert stats._is_ghost(e) is False

    @pytest.mark.parametrize("frac,expected", [
        (0.94, False),
        (0.95, True),    # boundary: >= MIN_VALID_STOP_FRACTION is valid
        (1.0, True),
    ])
    def test_valid_boundary(self, frac, expected):
        e = {"start": 100, "elapsed_seconds": 60, "fraction_stops_passed": frac}
        assert stats._is_valid_for_stats(e) is expected

    def test_in_flight_entry_counts_as_valid(self):
        e = {"start": 100, "elapsed_seconds": None}
        assert stats._is_valid_for_stats(e) is True

    def test_entry_without_fraction_counts_as_valid(self):
        """Rail trips aren't Trip-managed and never get a fraction."""
        e = {"start": 100, "elapsed_seconds": 60}
        assert stats._is_valid_for_stats(e) is True


class TestAsMins:
    def test_ghosts_always_dropped(self):
        bucket = [
            {"start": 100, "elapsed_seconds": 60, "fraction_stops_passed": 0.05},
            {"start": 200, "elapsed_seconds": 60, "fraction_stops_passed": 0.99},
        ]
        assert stats._as_mins(bucket, valid_only=False) == [200]

    def test_valid_only_also_drops_partials(self):
        bucket = [
            {"start": 100, "elapsed_seconds": 60, "fraction_stops_passed": 0.5},
            {"start": 200, "elapsed_seconds": 60, "fraction_stops_passed": 0.99},
        ]
        assert stats._as_mins(bucket, valid_only=False) == [100, 200]
        assert stats._as_mins(bucket, valid_only=True) == [200]

    def test_sorted_and_deduped(self):
        assert stats._as_mins([300.0, 100.0, 300.0, 200.0]) == [100.0, 200.0, 300.0]


class TestInsortEntry:
    def test_maintains_sort_order(self):
        entries = []
        for minute in (500, 100, 900, 300):
            stats._insort_entry(entries, {"start": minute})
        assert [e["start"] for e in entries] == [100, 300, 500, 900]

    def test_inserts_among_legacy_bare_floats(self):
        entries = [100.0, 500.0]
        stats._insort_entry(entries, {"start": 300})
        assert [stats._entry_minute(e) for e in entries] == [100.0, 300.0, 500.0]


class TestRecording:
    def test_record_start_writes_expected_schema(self):
        stats.record_start("T1", at(9, 30))
        data = read_today()
        day = list(data["T1"])[0]
        assert data["T1"][day] == [{
            "start": pytest.approx(570.0, abs=0.02),
            "elapsed_seconds": None,
            "stops_passed": None,
        }]

    def test_record_start_ignores_blank_route(self):
        stats.record_start("", at(9, 30))
        assert stats.today_snapshot() == {}

    def test_bucket_stays_sorted_across_out_of_order_records(self):
        for h in (14, 8, 11, 6):
            stats.record_start("T2", at(h, 0))
        day = list(stats.today_snapshot()["T2"])[0]
        mins = [e["start"] for e in stats.today_snapshot()["T2"][day]]
        assert mins == sorted(mins)

    def test_record_starts_batches(self):
        t = at(10, 0)
        stats.record_starts([("T1", t), ("T3", t), ("T1", t + 60_000)])
        data = read_today()
        assert len(data["T1"][list(data["T1"])[0]]) == 2
        assert len(data["T3"][list(data["T3"])[0]]) == 1

    def test_record_starts_empty_is_a_noop(self):
        stats.record_starts([])
        assert stats.today_snapshot() == {}

    def test_full_lifecycle_start_travel_finish(self):
        """One Trip produces exactly one entry, progressively filled in."""
        nominal = at(9, 0)
        travel = at(9, 4)
        stats.record_start("T1", nominal)
        stats.record_travel_start("T1", nominal, travel, idle_seconds=240)
        stats.record_finish(
            "T1", travel,
            elapsed_seconds=1500, stops_passed=42,
            fraction_stops_passed=0.98, tunnel_seconds=310.5,
        )

        data = read_today()
        day = list(data["T1"])[0]
        assert len(data["T1"][day]) == 1, "one Trip must yield one entry"
        e = data["T1"][day][0]
        assert e["start"] == pytest.approx(544.0, abs=0.02)      # 09:04
        assert e["nominal_start"] == pytest.approx(540.0, abs=0.02)  # 09:00
        assert e["idle_seconds"] == 240
        assert e["elapsed_seconds"] == 1500
        assert e["stops_passed"] == 42
        assert e["fraction_stops_passed"] == 0.98
        assert e["tunnel_seconds"] == 310.5

    def test_travel_start_reorders_the_bucket(self):
        """Promoting a start past a later trip must keep the bucket sorted."""
        early, late = at(9, 0), at(9, 30)
        stats.record_start("T1", early)
        stats.record_start("T1", late)
        stats.record_travel_start("T1", early, at(10, 0), idle_seconds=3600)
        day = list(stats.today_snapshot()["T1"])[0]
        mins = [e["start"] for e in stats.today_snapshot()["T1"][day]]
        assert mins == sorted(mins)

    def test_travel_start_is_a_noop_when_unchanged(self):
        t = at(9, 0)
        stats.record_start("T1", t)
        stats.record_travel_start("T1", t, t, idle_seconds=0)
        e = stats.today_snapshot()["T1"][list(stats.today_snapshot()["T1"])[0]][0]
        assert "nominal_start" not in e

    def test_finish_without_matching_start_is_dropped_silently(self):
        stats.record_finish("T9", at(9, 0), elapsed_seconds=100)
        assert stats.today_snapshot() == {}

    def test_detour_flag_only_set_when_true(self):
        t = at(9, 0)
        stats.record_start("T1", t)
        stats.record_finish("T1", t, elapsed_seconds=10, was_on_detour=False)
        e = stats.today_snapshot()["T1"][list(stats.today_snapshot()["T1"])[0]][0]
        assert "was_on_detour" not in e


class TestCaching:
    def test_writes_are_debounced_until_flush(self):
        stats.record_start("T1", at(9, 0))
        assert not stats.TODAY.exists(), "record must not hit disk directly"
        stats.flush()
        assert stats.TODAY.exists()

    def test_flush_is_idempotent(self):
        stats.record_start("T1", at(9, 0))
        stats.flush()
        first = stats.TODAY.read_text()
        stats.flush()
        assert stats.TODAY.read_text() == first

    def test_existing_file_is_loaded_on_first_access(self):
        stats.TODAY.write_text(json.dumps({"T4": {"2026-01-01": [{"start": 600}]}}))
        stats.reset_cache()
        assert stats.today_snapshot()["T4"]["2026-01-01"][0]["start"] == 600

    def test_snapshot_reflects_unflushed_writes(self):
        """/api/stats reads the snapshot, so it must never lag the cache."""
        stats.record_start("T1", at(9, 0))
        assert "T1" in stats.today_snapshot()


class TestAtomicWrite:
    def test_interrupted_write_leaves_previous_file_intact(self, monkeypatch):
        """The whole point of the temp-file + os.replace dance."""
        stats.record_start("T1", at(9, 0))
        stats.flush()
        good = stats.TODAY.read_text()

        stats.record_start("T2", at(10, 0))
        real_replace = stats.dump

        def exploding_dump(path, obj):
            raise OSError("disk full")

        monkeypatch.setattr(stats, "dump", exploding_dump)
        with pytest.raises(OSError):
            stats.flush()
        monkeypatch.setattr(stats, "dump", real_replace)

        assert stats.TODAY.read_text() == good
        assert json.loads(stats.TODAY.read_text())  # still parses

    def test_no_temp_file_left_behind(self, isolated_stats):
        stats.record_start("T1", at(9, 0))
        stats.flush()
        assert list(isolated_stats.glob("*.tmp")) == []


class TestRollover:
    def test_drains_yesterday_and_keeps_today(self):
        stats.record_start("T1", at(8, 0, days_ago=1))
        stats.record_start("T1", at(9, 0))
        stats.flush()

        stats.rollover()

        today_key = stats.date_str()
        remaining = stats.today_snapshot()
        assert list(remaining["T1"]) == [today_key], "only today should remain"

        cdfs = json.loads(stats.DAILY_CDFS.read_text())
        yesterday_key = stats.date_str(at(8, 0, days_ago=1))
        assert cdfs["T1"][yesterday_key] == [pytest.approx(480.0, abs=0.02)]

    def test_harvest_excludes_anomalous_trips(self):
        """daily_cdfs.json must only ever contain clean completed trips."""
        t_good = at(8, 0, days_ago=1)
        t_partial = at(9, 0, days_ago=1)
        t_ghost = at(10, 0, days_ago=1)
        stats.record_start("T1", t_good)
        stats.record_start("T1", t_partial)
        stats.record_start("T1", t_ghost)
        stats.record_finish("T1", t_good, elapsed_seconds=100, fraction_stops_passed=0.99)
        stats.record_finish("T1", t_partial, elapsed_seconds=100, fraction_stops_passed=0.5)
        stats.record_finish("T1", t_ghost, elapsed_seconds=100, fraction_stops_passed=0.02)

        stats.rollover()

        cdfs = json.loads(stats.DAILY_CDFS.read_text())
        day = stats.date_str(t_good)
        assert cdfs["T1"][day] == [pytest.approx(480.0, abs=0.02)]

    def test_empty_route_is_removed(self):
        stats.record_start("T1", at(8, 0, days_ago=1))
        stats.rollover()
        assert "T1" not in stats.today_snapshot()

    def test_does_not_overwrite_an_existing_harvest(self):
        day = stats.date_str(at(8, 0, days_ago=1))
        stats.DAILY_CDFS.write_text(json.dumps({"T1": {day: [1.0, 2.0]}}))
        stats.record_start("T1", at(8, 0, days_ago=1))
        stats.rollover()
        cdfs = json.loads(stats.DAILY_CDFS.read_text())
        assert cdfs["T1"][day] == [1.0, 2.0]

    def test_persists_immediately(self):
        """rollover must not leave the drained state sitting in memory."""
        stats.record_start("T1", at(8, 0, days_ago=1))
        stats.record_start("T1", at(9, 0))
        stats.flush()
        stats.rollover()
        on_disk = json.loads(stats.TODAY.read_text())
        assert list(on_disk["T1"]) == [stats.date_str()]

    def test_is_a_noop_on_empty_data(self):
        stats.rollover()  # must not raise
        assert stats.today_snapshot() == {}

    def test_normalizes_legacy_rich_entries_in_cdfs(self):
        """Old daily_cdfs.json rows held dicts; rollover flattens them."""
        stats.DAILY_CDFS.write_text(json.dumps(
            {"T1": {"2026-01-01": [{"start": 480.0}, {"start": 500.0}]}}))
        stats.rollover()
        cdfs = json.loads(stats.DAILY_CDFS.read_text())
        assert cdfs["T1"]["2026-01-01"] == [480.0, 500.0]


class TestTodayMinutes:
    def test_shapes_data_for_the_cdf_endpoint(self):
        t = at(9, 0)
        stats.record_start("T1", t)
        out = stats.today_minutes()
        assert out["T1"][stats.date_str(t)] == [pytest.approx(540.0, abs=0.02)]

    def test_valid_only_toggle(self):
        t_ok, t_bad = at(9, 0), at(10, 0)
        stats.record_start("T1", t_ok)
        stats.record_start("T1", t_bad)
        stats.record_finish("T1", t_ok, elapsed_seconds=10, fraction_stops_passed=0.99)
        stats.record_finish("T1", t_bad, elapsed_seconds=10, fraction_stops_passed=0.5)
        day = stats.date_str(t_ok)

        assert len(stats.today_minutes(valid_only=True)["T1"][day]) == 1
        assert len(stats.today_minutes(valid_only=False)["T1"][day]) == 2

    def test_accepts_explicit_data(self):
        out = stats.today_minutes({"T1": {"2026-01-01": [480.0, 500.0]}})
        assert out == {"T1": {"2026-01-01": [480.0, 500.0]}}
