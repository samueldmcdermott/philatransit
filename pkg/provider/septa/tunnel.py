"""SEPTA tunnel ghost detection for trolley routes.

When a trolley's GPS freezes near a portal for long enough, or a vehicle
disappears near a portal, it is presumed to have entered the tunnel.
Clients fetch /api/ghosts to get the current ghost list.

This module uses NormalizedVehicle dicts (with vehicle_id field) and
accepts a direction callback instead of importing TripManager directly.
"""

from __future__ import annotations

from __future__ import annotations

import threading
import time
from typing import Callable

from .constants import (
    TUNNEL_ROUTES, PORTALS, TUNNEL_EAST, LINGER_RADIUS,
    MOUTH_40TH_BOX, MOUTH_40TH_ROUTES, LINGER_TIME_S,
    STATIONARY_THRESH, GHOST_MAX_AGE_S, EASTBOUND_KW,
)


_ghost_lock = threading.Lock()
_ghosts = {}              # vid -> ghost info dict
_portal_linger = {}       # vid -> {first_ts, route, direction, lat, lng}
_prev_positions = {}      # vid -> {lat, lng, ts, route, dest, label, late}
_ghost_cooldown = {}      # vid -> timestamp


def _safe_float(val):
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _heading_east(dest):
    if not dest:
        return False
    d = dest.lower()
    return any(k in d for k in EASTBOUND_KW)


def _check_portal(lat, lng, route, dest, direction_fn=None, vid=None):
    """Return direction if vehicle is near a portal heading into tunnel.

    Uses direction_fn(vid) -> bool|None as primary source, with
    destination-keyword detection as fallback.
    """
    heading_east = None
    if direction_fn is not None and vid is not None:
        computed = direction_fn(vid)
        if computed is not None:
            heading_east = computed
    if heading_east is None:
        heading_east = _heading_east(dest)

    # West portal check — T2-T5 use the tight mouth box
    if route in MOUTH_40TH_ROUTES:
        b = MOUTH_40TH_BOX
        if (b['minLat'] <= lat <= b['maxLat']
                and b['minLng'] <= lng <= b['maxLng']
                and heading_east):
            return 'eastbound'
    else:
        portal = PORTALS.get(route)
        if portal:
            d = abs(lat - portal[0]) + abs(lng - portal[1])
            if d < LINGER_RADIUS and heading_east:
                return 'eastbound'

    # East end (13th St) check
    d_east = abs(lat - TUNNEL_EAST[0]) + abs(lng - TUNNEL_EAST[1])
    if d_east < LINGER_RADIUS and not heading_east:
        return 'westbound'
    return None


class SeptaTunnelDetector:
    """SEPTA-specific tunnel ghost detector.

    Implements the TunnelDetector protocol from provider.base.
    """

    def process(self, vehicles: dict[str, list[dict]],
                direction_fn: Callable | None = None) -> None:
        """Called each poll cycle to detect tunnel entries/exits."""
        now = time.time()

        # Parse trolley vehicles from normalized data
        trolley_vehicles = {}
        for route_id in TUNNEL_ROUTES:
            for v in vehicles.get(route_id, []):
                meta = v.get('meta', {})
                delay = meta.get('delay', 0)
                if delay == 998:
                    continue
                label = v.get('label', '')
                if label in ('None', None, '', '0'):
                    continue
                vid = v.get('vehicle_id')
                if not vid:
                    continue
                lat = _safe_float(v.get('lat'))
                lng = _safe_float(v.get('lng'))
                if lat is None or lng is None:
                    continue
                trolley_vehicles[vid] = {
                    'lat': lat, 'lng': lng, 'route': route_id,
                    'label': str(label),
                    'dest': meta.get('headsign', ''),
                    'late': int(delay),
                    'trip': meta.get('api_trip_id', ''),
                }

        with _ghost_lock:
            # Prune expired cooldowns
            for vid in list(_ghost_cooldown):
                if now - _ghost_cooldown[vid] > 300:
                    del _ghost_cooldown[vid]

            # -- Linger-based detection --
            for vid, tv in trolley_vehicles.items():
                if vid in _ghosts:
                    continue

                direction = _check_portal(
                    tv['lat'], tv['lng'], tv['route'], tv['dest'],
                    direction_fn=direction_fn, vid=vid,
                )
                existing = _portal_linger.get(vid)

                if direction is not None:
                    # Vehicle detected near portal heading in
                    if existing and existing['route'] == tv['route']:
                        moved = (abs(tv['lat'] - existing['lat'])
                                 + abs(tv['lng'] - existing['lng']))
                        if moved >= STATIONARY_THRESH:
                            existing['first_ts'] = now
                            existing['lat'] = tv['lat']
                            existing['lng'] = tv['lng']
                    else:
                        seed_ts = now
                        prev = _prev_positions.get(vid)
                        if prev:
                            moved = (abs(tv['lat'] - prev['lat'])
                                     + abs(tv['lng'] - prev['lng']))
                            if moved < STATIONARY_THRESH:
                                seed_ts = prev['ts']
                        _portal_linger[vid] = {
                            'first_ts': seed_ts, 'route': tv['route'],
                            'direction': direction,
                            'lat': tv['lat'], 'lng': tv['lng'],
                        }
                elif existing:
                    # Hysteresis: GPS drifted outside the portal zone but
                    # vehicle is still close — tolerate jitter (~50m).
                    drift = (abs(tv['lat'] - existing['lat'])
                             + abs(tv['lng'] - existing['lng']))
                    if drift >= 0.001:  # ~100m — enough for GPS jitter
                        _portal_linger.pop(vid, None)

                _prev_positions[vid] = {
                    'lat': tv['lat'], 'lng': tv['lng'], 'ts': now,
                    'route': tv['route'], 'dest': tv['dest'],
                    'label': tv['label'], 'late': tv['late'],
                }

                if vid not in _portal_linger:
                    continue

                linger = _portal_linger[vid]
                if (now - linger['first_ts']) >= LINGER_TIME_S and vid not in _ghost_cooldown:
                    _ghost_cooldown[vid] = now
                    _ghosts[vid] = {
                        'route': tv['route'],
                        'direction': linger['direction'],
                        'enterTs': int(linger['first_ts'] * 1000),
                        'lingerSec': round(now - linger['first_ts'], 1),
                        'label': tv['label'],
                        'dest': tv['dest'],
                        'late': tv['late'],
                        'trip': tv.get('trip', ''),
                        'entryLat': tv['lat'],
                        'entryLng': tv['lng'],
                    }
                    _portal_linger.pop(vid, None)

            # -- Disappearance-based detection --
            for vid, prev in list(_prev_positions.items()):
                if vid in trolley_vehicles or vid in _ghosts or vid in _ghost_cooldown:
                    continue
                if now - prev['ts'] > 90:
                    continue
                if prev['route'] not in TUNNEL_ROUTES:
                    continue
                direction = _check_portal(
                    prev['lat'], prev['lng'], prev['route'], prev['dest'],
                    direction_fn=direction_fn, vid=vid,
                )
                if direction is not None:
                    _ghost_cooldown[vid] = now
                    _ghosts[vid] = {
                        'route': prev['route'],
                        'direction': direction,
                        'enterTs': int(prev['ts'] * 1000),
                        'lingerSec': 0,
                        'label': prev['label'],
                        'dest': prev['dest'],
                        'late': prev['late'],
                        'trip': '',
                        'entryLat': prev['lat'],
                        'entryLng': prev['lng'],
                    }

            # -- Ghost emergence / expiry --
            for vid in list(_ghosts):
                ghost = _ghosts[vid]
                age_s = now - ghost['enterTs'] / 1000
                if age_s > GHOST_MAX_AGE_S:
                    del _ghosts[vid]
                    _ghost_cooldown.pop(vid, None)
                    continue

                tv = trolley_vehicles.get(vid)
                if tv:
                    entry_moved = abs(tv['lat'] - ghost['entryLat']) + abs(tv['lng'] - ghost['entryLng'])
                    if entry_moved > LINGER_RADIUS:
                        del _ghosts[vid]
                        _portal_linger.pop(vid, None)
                        _ghost_cooldown.pop(vid, None)

            # Clean stale prev positions
            for vid in list(_prev_positions):
                if now - _prev_positions[vid]['ts'] > 600:
                    del _prev_positions[vid]

    def get_ghosts(self) -> list[dict]:
        """Return current ghosts as a list of dicts."""
        with _ghost_lock:
            return [{**g, 'vid': vid} for vid, g in _ghosts.items()]

    def get_lingering(self) -> dict:
        """Return vehicle IDs currently lingering near a portal."""
        with _ghost_lock:
            return {vid: {
                'direction': info['direction'],
                'route': info['route'],
                'first_ts': int(info['first_ts'] * 1000),
                'lat': info['lat'],
                'lng': info['lng'],
            } for vid, info in _portal_linger.items()}
