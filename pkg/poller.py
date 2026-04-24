"""Background data poller and in-memory caches.

Provider-agnostic: works with any Provider implementation.
Transit/rail data is fetched on a fixed cycle and served from caches,
so the external API sees a fixed call rate regardless of client count.
"""

from __future__ import annotations

import threading
import time

from .helpers import date_str

POLL_INTERVAL = 5  # seconds

# transit cache: route_id -> list of enriched vehicle dicts
transit_cache = {"routes": {}, "ts": 0.0}
transit_lock = threading.Lock()

# rail cache: list of enriched vehicle dicts
rail_cache = {"data": [], "ts": 0.0}
rail_lock = threading.Lock()


_provider = None
_trip_manager = None

# Rail first-sighting state: rail isn't Trip-managed, so we record a
# rail start the first time each train number appears on a given day.
_rail_seen: set[str] = set()
_rail_date: str | None = None


def _record_rail_starts(trains):
    from .core.stats import record_starts  # avoid import cycle at module load
    global _rail_date
    today = date_str()
    if today != _rail_date:
        _rail_seen.clear()
        _rail_date = today
    now_ms = int(time.time() * 1000)
    new = []
    for t in trains:
        vid = t.get('vehicle_id', '')
        route = t.get('route_id', '')
        if not vid or not route or vid in _rail_seen:
            continue
        _rail_seen.add(vid)
        new.append((route, now_ms))
    record_starts(new)


def _poll_loop():
    """Background thread: refresh transit + rail data every POLL_INTERVAL seconds."""
    while True:
        # -- Transit vehicles (buses, trolleys, subway) --
        try:
            by_route = _provider.poll_transit()

            # -- Tunnel ghost detection (runs before enrichment so
            #    ghost labels protect trips from stale-pruning) --
            tunnel_emerged = {}
            try:
                tunnel_detector = _provider.get_tunnel_detector()
                if tunnel_detector:
                    tunnel_detector.process(by_route, _trip_manager.get_direction)
                    tunnel_emerged = tunnel_detector.pop_emerged()
                    # Tell TripManager which vehicles are underground
                    ghost_labels = {g['vid'] for g in tunnel_detector.get_ghosts()}
                    _trip_manager.set_ghost_labels(ghost_labels)
                    lingering = tunnel_detector.get_lingering()
                    for route_vehicles in by_route.values():
                        for v in route_vehicles:
                            vid = v.get('vehicle_id')
                            if vid and vid in lingering:
                                v['lingering'] = lingering[vid]
            except Exception as e:
                print(f"  [tunnel] error: {e}")

            # Enrich vehicles with Trip-based fields
            try:
                _trip_manager.enrich_vehicles(by_route)
            except Exception as e:
                print(f"  [trip] enrichment error: {e}")

            # Apply tunnel emergence (flip direction on trips that
            # just exited — the trip persists because all transit
            # routes use the fleet label as vehicle_id).
            if tunnel_emerged:
                try:
                    _trip_manager.apply_tunnel_emergence(tunnel_emerged)
                except Exception as e:
                    print(f"  [tunnel] emergence error: {e}")

            with transit_lock:
                transit_cache["routes"] = by_route
                transit_cache["ts"] = time.time()
        except Exception as e:
            print(f"  [poller] transit poll error: {e}")

        # -- Rail vehicles --
        try:
            rail_vehicles = _provider.poll_rail()
            _record_rail_starts(rail_vehicles)
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
