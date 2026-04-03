"""Background data poller and in-memory caches.

Provider-agnostic: works with any Provider implementation.
Transit/rail data is fetched on a fixed cycle and served from caches,
so the external API sees a fixed call rate regardless of client count.
"""

from __future__ import annotations

import threading
import time

POLL_INTERVAL = 5  # seconds

# transit cache: route_id -> list of enriched vehicle dicts
transit_cache = {"routes": {}, "ts": 0.0}
transit_lock = threading.Lock()

# rail cache: list of enriched vehicle dicts
rail_cache = {"data": [], "ts": 0.0}
rail_lock = threading.Lock()


_provider = None
_trip_manager = None


def _poll_loop():
    """Background thread: refresh transit + rail data every POLL_INTERVAL seconds."""
    while True:
        # -- Transit vehicles (buses, trolleys, subway) --
        try:
            by_route = _provider.poll_transit()

            # Enrich vehicles with Trip-based fields
            try:
                _trip_manager.enrich_vehicles(by_route)
            except Exception as e:
                print(f"  [trip] enrichment error: {e}")

            # -- Tunnel ghost tracking (before caching so lingering is annotated) --
            try:
                tunnel_detector = _provider.get_tunnel_detector()
                if tunnel_detector:
                    tunnel_detector.process(by_route, _trip_manager.get_direction)
                    lingering = tunnel_detector.get_lingering()
                    for route_vehicles in by_route.values():
                        for v in route_vehicles:
                            vid = v.get('vehicle_id')
                            if vid and vid in lingering:
                                v['lingering'] = lingering[vid]
            except Exception as e:
                print(f"  [tunnel] error: {e}")

            with transit_lock:
                transit_cache["routes"] = by_route
                transit_cache["ts"] = time.time()
        except Exception as e:
            print(f"  [poller] transit poll error: {e}")

        # -- Rail vehicles --
        try:
            rail_vehicles = _provider.poll_rail()
            with rail_lock:
                rail_cache["data"] = rail_vehicles
                rail_cache["ts"] = time.time()
        except Exception as e:
            print(f"  [poller] rail poll error: {e}")

        time.sleep(POLL_INTERVAL)


def start_poller(provider, trip_manager):
    """Launch the background data poller thread.

    Parameters:
        provider: Provider instance (e.g. SeptaProvider)
        trip_manager: TripManager instance
    """
    global _provider, _trip_manager
    _provider = provider
    _trip_manager = trip_manager

    t = threading.Thread(target=_poll_loop, daemon=True, name="DataPoller")
    t.start()
    print(f"  [poller] started ({provider.__class__.__name__})")
