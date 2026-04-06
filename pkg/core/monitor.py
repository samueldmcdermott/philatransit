"""Monitoring module — tracks operational metrics across routes.

Currently tracks tunnel roundtrip times with a rolling one-hour average.
Designed to be extensible for future monitoring needs.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


# ── Configuration ─────────────────────────────────────────────
ROLLING_WINDOW_S = 3600  # 1 hour


@dataclass
class TunnelTrip:
    """One observed tunnel transit (entry to exit)."""
    route: str
    entry_time: float      # unix timestamp
    exit_time: float       # unix timestamp
    roundtrip_seconds: float

    @property
    def age(self) -> float:
        return time.time() - self.exit_time


class Monitor:
    """Central monitoring hub.

    Not a module-level singleton — instantiated by the app factory.
    """

    def __init__(self, fallback_times: dict[str, float] | None = None):
        """
        fallback_times: {route_id: one_way_seconds} from tunnel_times.json,
                        used when no recent tunnel trips exist.
        """
        self._lock = threading.Lock()
        self._tunnel_trips: list[TunnelTrip] = []
        self._fallback_times = fallback_times or {}

    def record_tunnel_trip(self, route: str, entry_time: float,
                           exit_time: float) -> None:
        """Record a completed tunnel transit."""
        duration = exit_time - entry_time
        if duration <= 0:
            return
        trip = TunnelTrip(
            route=route,
            entry_time=entry_time,
            exit_time=exit_time,
            roundtrip_seconds=round(duration, 1),
        )
        with self._lock:
            self._tunnel_trips.append(trip)
            self._prune()
        print(f"  [monitor] tunnel trip: {route} "
              f"{round(duration)}s")

    def _prune(self):
        """Remove trips older than the rolling window."""
        cutoff = time.time() - ROLLING_WINDOW_S
        self._tunnel_trips = [
            t for t in self._tunnel_trips if t.exit_time > cutoff
        ]

    def get_tunnel_avg(self, route: str | None = None) -> dict:
        """Return rolling average tunnel time for a route (or all routes).

        Returns dict with:
          - avg_seconds: rolling average (or fallback)
          - sample_count: number of trips in the window
          - using_fallback: True if no recent data available
          - per_route: {route: {avg_seconds, sample_count, fallback}} (if route is None)
        """
        with self._lock:
            self._prune()
            trips = list(self._tunnel_trips)

        if route:
            route_trips = [t for t in trips if t.route == route]
            return self._route_summary(route, route_trips)

        # All routes
        by_route: dict[str, list[TunnelTrip]] = {}
        for t in trips:
            by_route.setdefault(t.route, []).append(t)

        per_route = {}
        all_durations = []
        for rid in sorted(set(list(by_route.keys()) + list(self._fallback_times.keys()))):
            rt = by_route.get(rid, [])
            per_route[rid] = self._route_summary(rid, rt)
            all_durations.extend(t.roundtrip_seconds for t in rt)

        overall_avg = (round(sum(all_durations) / len(all_durations), 1)
                       if all_durations else None)

        return {
            'avg_seconds': overall_avg,
            'sample_count': len(trips),
            'per_route': per_route,
        }

    def _route_summary(self, route: str,
                        trips: list[TunnelTrip]) -> dict:
        if trips:
            durations = [t.roundtrip_seconds for t in trips]
            avg = round(sum(durations) / len(durations), 1)
            return {
                'avg_seconds': avg,
                'half_time_seconds': round(avg / 2, 1),
                'sample_count': len(trips),
                'using_fallback': False,
            }
        # Fallback to static tunnel_times.json value
        fallback = self._fallback_times.get(route)
        if fallback:
            return {
                'avg_seconds': fallback * 2,
                'half_time_seconds': fallback,
                'sample_count': 0,
                'using_fallback': True,
            }
        return {
            'avg_seconds': None,
            'half_time_seconds': None,
            'sample_count': 0,
            'using_fallback': True,
        }

    def get_snapshot(self) -> dict:
        """Full monitoring snapshot for the API."""
        tunnel = self.get_tunnel_avg()
        return {
            'tunnel': tunnel,
            'timestamp': time.time(),
        }
