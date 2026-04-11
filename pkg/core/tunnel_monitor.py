"""Tunnel roundtrip-time monitor.

Tracks tunnel transit times with a rolling 20-minute average.

T2–T5 share the same tunnel (40th St portal to 13th St), so their trips
are pooled into a single "T2-T5" average.  T1 uses a different portal
(36th St) and keeps its own average.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass


# ── Configuration ─────────────────────────────────────────────
ROLLING_WINDOW_S = 1200  # 20 minutes
MIN_SAMPLES      = 5     # need this many trips in the window to trust it

# Routes that share the 40th St tunnel — pooled into one group.
_SHARED_TUNNEL_ROUTES = frozenset({'T2', 'T3', 'T4', 'T5'})
_SHARED_TUNNEL_KEY = 'T2-T5'


def _tunnel_key(route: str) -> str:
    """Map a route to its tunnel-time group key."""
    return _SHARED_TUNNEL_KEY if route in _SHARED_TUNNEL_ROUTES else route


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


class TunnelMonitor:
    """Tracks rolling-average tunnel transit times.

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
        # Monitor start time: the rolling average is not trusted until a
        # full window has elapsed since startup.  Before that, and whenever
        # fewer than MIN_SAMPLES trips exist in the window, we fall back
        # to the historical average.
        self._start_time = time.time()

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

    def _shared_fallback(self) -> float | None:
        """Average the fallback one-way times for T2–T5."""
        vals = [self._fallback_times[r] for r in _SHARED_TUNNEL_ROUTES
                if r in self._fallback_times]
        return round(sum(vals) / len(vals), 1) if vals else None

    def _group_summary(self, key: str,
                       trips: list[TunnelTrip]) -> dict:
        """Build a summary dict for one tunnel-time group."""
        # The rolling average is only trusted once the monitor has been
        # running for a full window AND at least MIN_SAMPLES trips have
        # been observed in that window.  Otherwise fall back to the
        # historical average to avoid noisy early readings.
        window_elapsed = (time.time() - self._start_time) >= ROLLING_WINDOW_S
        if trips and window_elapsed and len(trips) >= MIN_SAMPLES:
            durations = [t.roundtrip_seconds for t in trips]
            avg = round(sum(durations) / len(durations), 1)
            return {
                'avg_seconds': avg,
                'half_time_seconds': round(avg / 2, 1),
                'sample_count': len(trips),
                'using_fallback': False,
            }
        # Fallback
        if key == _SHARED_TUNNEL_KEY:
            fb = self._shared_fallback()
        else:
            fb = self._fallback_times.get(key)
        if fb:
            return {
                'avg_seconds': round(fb * 2, 1),
                'half_time_seconds': fb,
                'sample_count': len(trips),
                'using_fallback': True,
            }
        return {
            'avg_seconds': None,
            'half_time_seconds': None,
            'sample_count': len(trips),
            'using_fallback': True,
        }

    def get_tunnel_avg(self, route: str | None = None) -> dict:
        """Return rolling average tunnel time by group.

        T2–T5 are pooled under the key "T2-T5".  Individual route
        queries for T2/T3/T4/T5 return the shared pool result.

        Returns dict with per_route keyed by group (e.g. "T1", "T2-T5").
        """
        with self._lock:
            self._prune()
            trips = list(self._tunnel_trips)

        # Group trips by tunnel key
        by_group: dict[str, list[TunnelTrip]] = {}
        for t in trips:
            by_group.setdefault(_tunnel_key(t.route), []).append(t)

        if route:
            key = _tunnel_key(route)
            return self._group_summary(key, by_group.get(key, []))

        # All groups — ensure T1 and T2-T5 always appear
        all_keys = set(by_group.keys())
        # Always include T1 and the shared key if we have fallbacks
        if 'T1' in self._fallback_times:
            all_keys.add('T1')
        if any(r in self._fallback_times for r in _SHARED_TUNNEL_ROUTES):
            all_keys.add(_SHARED_TUNNEL_KEY)

        per_route = {}
        all_durations = []
        for key in sorted(all_keys):
            gt = by_group.get(key, [])
            per_route[key] = self._group_summary(key, gt)
            all_durations.extend(t.roundtrip_seconds for t in gt)

        overall_avg = (round(sum(all_durations) / len(all_durations), 1)
                       if all_durations else None)

        return {
            'avg_seconds': overall_avg,
            'sample_count': len(trips),
            'per_route': per_route,
        }

    def get_snapshot(self) -> dict:
        """Full monitoring snapshot for the API."""
        tunnel = self.get_tunnel_avg()
        return {
            'tunnel': tunnel,
            'timestamp': time.time(),
        }
