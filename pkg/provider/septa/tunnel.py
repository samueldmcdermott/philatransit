"""SEPTA tunnel ghost detection for trolley routes.

When a trolley's GPS freezes near a portal for long enough, or a vehicle
disappears near a portal, it is presumed to have entered the tunnel.
Clients fetch /api/ghosts to get the current ghost list.

Linger detection: a vehicle is "lingering" when it reports the *exact same*
GPS position for several consecutive polls while on the route shape between
the 40th St Portal and 37th & Spruce stops.  This happens because SEPTA
repeats the last known position after the GPS signal is lost underground.

This module uses NormalizedVehicle dicts (with vehicle_id field) and
accepts a direction callback instead of importing TripManager directly.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

from ... import geo
from .constants import (
    TUNNEL_ROUTES, PORTALS, TUNNEL_EAST, LINGER_RADIUS,
    GHOST_MAX_AGE_S, EASTBOUND_KW,
)

# Linger zone: between these two stops on the route shape
_LINGER_STOP_A = '40th St Portal'
_LINGER_STOP_B = '37th & Spruce'
_ON_ROUTE_THRESH_M = 20   # must be within 20m of route shape
_LINGER_TIME_S = 20       # seconds of frozen GPS before flagging

_ghost_lock = threading.Lock()
_ghosts = {}              # vid -> ghost info dict
_portal_linger = {}       # vid -> {first_ts, route, direction, lat, lng, repeats}
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


def _infer_direction(dest, direction_fn=None, vid=None):
    """Return True if heading east (into tunnel from west portal)."""
    if direction_fn is not None and vid is not None:
        computed = direction_fn(vid)
        if computed is not None:
            return computed
    return _heading_east(dest)


def _check_portal(lat, lng, route, dest, direction_fn=None, vid=None):
    """Return direction if vehicle is near a portal heading into tunnel.

    Used for disappearance-based detection and ghost emergence checks.
    """
    heading_east = _infer_direction(dest, direction_fn, vid)

    # West portals
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

    def __init__(self):
        self._shapes = None
        self._monitor = None
        # Per-route linger zone: (min_da, max_da) between the two stops.
        # Computed lazily from shape data.
        self._linger_zones: dict[str, tuple[float, float] | None] = {}

    def set_shapes(self, shape_registry):
        self._shapes = shape_registry

    def set_monitor(self, monitor):
        self._monitor = monitor

    def _get_linger_zone(self, route_id):
        """Return (min_da, max_da) for the linger zone on this route, or None."""
        if route_id in self._linger_zones:
            return self._linger_zones[route_id]
        zone = None
        if self._shapes:
            shape = self._shapes.get(route_id)
            if shape and shape.stops:
                da_a = da_b = None
                for name, da in shape.stops:
                    if name == _LINGER_STOP_A:
                        da_a = da
                    elif name == _LINGER_STOP_B:
                        da_b = da
                if da_a is not None and da_b is not None:
                    zone = (min(da_a, da_b), max(da_a, da_b))
        self._linger_zones[route_id] = zone
        return zone

    def _check_linger_zone(self, lat, lng, route_id):
        """Check if vehicle is on-route (within 10m) in the linger zone.

        Returns (dist_along, direction) or None.
        """
        if not self._shapes:
            return None
        shape = self._shapes.get(route_id)
        if not shape:
            return None
        zone = self._get_linger_zone(route_id)
        if not zone:
            return None

        da, perp = geo.project_with_perp(shape.pts, shape.cum_dist, lat, lng)
        if perp > _ON_ROUTE_THRESH_M:
            return None
        if da < zone[0] or da > zone[1]:
            return None

        return da

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
            # A vehicle is "lingering" when SEPTA repeats the exact same GPS
            # position while on the route shape between 40th St Portal and
            # 37th & Spruce.  After _LINGER_TIME_S of frozen GPS the vehicle
            # is promoted to a ghost (presumed underground).
            for vid, tv in trolley_vehicles.items():
                if vid in _ghosts:
                    continue

                in_zone = self._check_linger_zone(
                    tv['lat'], tv['lng'], tv['route'])
                prev = _prev_positions.get(vid)
                existing = _portal_linger.get(vid)

                if in_zone is not None:
                    exact_repeat = (prev
                                   and prev['lat'] == tv['lat']
                                   and prev['lng'] == tv['lng'])

                    if existing and existing['route'] == tv['route']:
                        if not exact_repeat:
                            # Position changed — reset
                            _portal_linger.pop(vid, None)
                    elif exact_repeat:
                        # Start tracking: first frozen position
                        heading_east = _infer_direction(
                            tv['dest'], direction_fn, vid)
                        _portal_linger[vid] = {
                            'first_ts': prev['ts'] if prev else now,
                            'route': tv['route'],
                            'direction': 'eastbound' if heading_east else 'westbound',
                            'lat': tv['lat'],
                            'lng': tv['lng'],
                        }
                else:
                    _portal_linger.pop(vid, None)

                _prev_positions[vid] = {
                    'lat': tv['lat'], 'lng': tv['lng'], 'ts': now,
                    'route': tv['route'], 'dest': tv['dest'],
                    'label': tv['label'], 'late': tv['late'],
                }

                linger = _portal_linger.get(vid)
                if not linger:
                    continue

                # Promote to ghost after _LINGER_TIME_S
                if ((now - linger['first_ts']) >= _LINGER_TIME_S
                        and vid not in _ghost_cooldown):
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

            # Build label→vid lookup so we can detect re-emergence even
            # when the SEPTA trip ID (used as vehicle_id) changes.
            label_to_live = {}
            for tv_vid, tv_info in trolley_vehicles.items():
                lbl = tv_info.get('label')
                if lbl:
                    label_to_live[lbl] = (tv_vid, tv_info)

            # -- Ghost emergence / expiry --
            for vid in list(_ghosts):
                ghost = _ghosts[vid]
                age_s = now - ghost['enterTs'] / 1000
                if age_s > GHOST_MAX_AGE_S:
                    del _ghosts[vid]
                    _ghost_cooldown.pop(vid, None)
                    continue

                # Check by vehicle_id first, then by label (fleet number).
                # The trip-based vehicle_id can change when SEPTA reassigns
                # the vehicle, so label matching is essential.
                tv = trolley_vehicles.get(vid)
                if not tv:
                    match = label_to_live.get(ghost.get('label'))
                    if match:
                        tv = match[1]
                if tv:
                    entry_moved = (abs(tv['lat'] - ghost['entryLat'])
                                   + abs(tv['lng'] - ghost['entryLng']))
                    if entry_moved > LINGER_RADIUS:
                        # Record tunnel trip timing for monitoring
                        if self._monitor:
                            entry_ts = ghost['enterTs'] / 1000
                            self._monitor.record_tunnel_trip(
                                ghost['route'], entry_ts, now)
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
