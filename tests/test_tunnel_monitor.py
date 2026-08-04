"""Tests for pkg.core.tunnel_monitor — rolling tunnel transit averages.

The monitor deliberately distrusts its own rolling average until it has both
a full window of uptime and enough samples; these tests pin that policy down,
since a wrong fallback silently skews every ghost position estimate.
"""

import time

import pytest

from pkg.core.tunnel_monitor import (
    MIN_SAMPLES,
    ROLLING_WINDOW_S,
    TunnelMonitor,
)


FALLBACKS = {"T1": 400.0, "T2": 300.0, "T3": 310.0, "T4": 320.0, "T5": 330.0}


def warm(monitor):
    """Backdate the monitor's start so the startup window has elapsed."""
    monitor._start_time = time.time() - ROLLING_WINDOW_S - 1
    return monitor


def add_trips(monitor, route, count, duration=600.0):
    now = time.time()
    for _ in range(count):
        monitor.record_tunnel_trip(route, now - duration, now)


@pytest.fixture
def monitor():
    return warm(TunnelMonitor(fallback_times=dict(FALLBACKS)))


class TestGrouping:
    def test_t2_through_t5_pool_into_one_key(self, monitor):
        for route in ("T2", "T3", "T4", "T5"):
            add_trips(monitor, route, 2, duration=600.0)
        result = monitor.get_tunnel_avg()
        assert result["per_route"]["T2-T5"]["sample_count"] == 8
        assert result["per_route"]["T2-T5"]["using_fallback"] is False

    def test_t1_is_tracked_separately(self, monitor):
        add_trips(monitor, "T1", MIN_SAMPLES, duration=800.0)
        add_trips(monitor, "T3", MIN_SAMPLES, duration=400.0)
        per_route = monitor.get_tunnel_avg()["per_route"]
        assert per_route["T1"]["avg_seconds"] == 800.0
        assert per_route["T2-T5"]["avg_seconds"] == 400.0

    def test_querying_a_pooled_route_returns_the_pool(self, monitor):
        add_trips(monitor, "T2", MIN_SAMPLES, duration=500.0)
        # T4 has no trips of its own but shares T2's tunnel.
        assert monitor.get_tunnel_avg("T4")["avg_seconds"] == 500.0
        assert monitor.get_tunnel_avg("T4")["using_fallback"] is False

    def test_both_groups_appear_even_with_no_trips(self, monitor):
        per_route = monitor.get_tunnel_avg()["per_route"]
        assert set(per_route) == {"T1", "T2-T5"}


class TestRollingAverage:
    def test_average_of_observed_durations(self, monitor):
        now = time.time()
        for d in (400.0, 500.0, 600.0, 700.0, 800.0):
            monitor.record_tunnel_trip("T3", now - d, now)
        summary = monitor.get_tunnel_avg("T3")
        assert summary["avg_seconds"] == 600.0
        assert summary["half_time_seconds"] == 300.0
        assert summary["sample_count"] == 5

    def test_half_time_is_half_the_roundtrip(self, monitor):
        add_trips(monitor, "T1", MIN_SAMPLES, duration=900.0)
        s = monitor.get_tunnel_avg("T1")
        assert s["half_time_seconds"] == s["avg_seconds"] / 2

    def test_zero_and_negative_durations_are_rejected(self, monitor):
        now = time.time()
        monitor.record_tunnel_trip("T3", now, now)
        monitor.record_tunnel_trip("T3", now, now - 100)
        assert monitor.get_tunnel_avg("T3")["sample_count"] == 0


class TestFallback:
    def test_used_below_min_samples(self, monitor):
        add_trips(monitor, "T1", MIN_SAMPLES - 1, duration=900.0)
        s = monitor.get_tunnel_avg("T1")
        assert s["using_fallback"] is True
        assert s["half_time_seconds"] == FALLBACKS["T1"]
        assert s["avg_seconds"] == FALLBACKS["T1"] * 2
        assert s["sample_count"] == MIN_SAMPLES - 1

    def test_used_inside_the_startup_window_despite_enough_samples(self):
        """A monitor that just started can't have seen a full window, so its
        average is not yet trustworthy even with plenty of samples."""
        m = TunnelMonitor(fallback_times=dict(FALLBACKS))  # not warmed
        add_trips(m, "T1", MIN_SAMPLES * 3, duration=900.0)
        s = m.get_tunnel_avg("T1")
        assert s["using_fallback"] is True
        assert s["half_time_seconds"] == FALLBACKS["T1"]

    def test_shared_fallback_averages_t2_through_t5(self, monitor):
        s = monitor.get_tunnel_avg("T3")
        expected = round(sum(FALLBACKS[r] for r in ("T2", "T3", "T4", "T5")) / 4, 1)
        assert s["using_fallback"] is True
        assert s["half_time_seconds"] == expected

    def test_no_fallback_available_yields_none(self):
        m = warm(TunnelMonitor(fallback_times={}))
        s = m.get_tunnel_avg("T1")
        assert s["avg_seconds"] is None
        assert s["half_time_seconds"] is None
        assert s["using_fallback"] is True

    def test_unknown_route_falls_back_to_none(self, monitor):
        s = monitor.get_tunnel_avg("G1")
        assert s["using_fallback"] is True
        assert s["avg_seconds"] is None


class TestPruning:
    def test_trips_outside_the_window_are_dropped(self, monitor):
        old = time.time() - ROLLING_WINDOW_S - 60
        for _ in range(MIN_SAMPLES):
            monitor.record_tunnel_trip("T1", old - 600, old)
        s = monitor.get_tunnel_avg("T1")
        assert s["sample_count"] == 0
        assert s["using_fallback"] is True

    def test_recent_trips_survive_pruning(self, monitor):
        old = time.time() - ROLLING_WINDOW_S - 60
        monitor.record_tunnel_trip("T1", old - 600, old)
        add_trips(monitor, "T1", MIN_SAMPLES, duration=600.0)
        assert monitor.get_tunnel_avg("T1")["sample_count"] == MIN_SAMPLES


class TestSnapshot:
    def test_shape_matches_the_api_contract(self, monitor):
        add_trips(monitor, "T1", MIN_SAMPLES, duration=600.0)
        snap = monitor.get_snapshot()
        assert set(snap) == {"tunnel", "timestamp"}
        assert set(snap["tunnel"]) == {"avg_seconds", "sample_count", "per_route"}
        assert snap["timestamp"] == pytest.approx(time.time(), abs=5)

    def test_overall_average_spans_all_groups(self, monitor):
        add_trips(monitor, "T1", 1, duration=400.0)
        add_trips(monitor, "T3", 1, duration=800.0)
        assert monitor.get_tunnel_avg()["avg_seconds"] == 600.0
