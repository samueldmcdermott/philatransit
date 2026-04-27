"""SEPTA tunnel ghost detection for trolley routes.

When a trolley's GPS freezes near a portal for long enough, or a vehicle
disappears near a portal, it is presumed to have entered the tunnel.
Clients fetch /api/ghosts to get the current ghost list.

Linger detection: a vehicle is "lingering" when it reports the *exact same*
GPS position for several consecutive polls while on the route shape between
the 40th St Portal and 37th & Spruce stops.  This happens because SEPTA
repeats the last known position after the GPS signal is lost underground.

All ghost state is keyed by **label** (fleet number), not by SEPTA's
vehicle_id / trip field, because SEPTA can reassign the trip ID when a
vehicle exits the tunnel — causing the old ghost and the new live entry
to coexist under different keys.  The fleet number is stable.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

from ... import geo
from .constants import (
    TUNNEL_ROUTES, PORTALS, TUNNEL_EAST, LINGER_RADIUS,
    EASTBOUND_KW,
)

# Linger zone: between these two stops on the route shape
_LINGER_STOP_A = '40th St Portal'
_LINGER_STOP_B = '37th & Spruce'
_ON_ROUTE_THRESH_M = 20   # must be within 20m of route shape
_LINGER_TIME_S = 20       # seconds of frozen GPS before flagging

_ghost_lock = threading.Lock()
_ghosts = {}              # label -> ghost info dict
_portal_linger = {}       # label -> {first_ts, route, direction, lat, lng}
_prev_positions = {}      # label -> {lat, lng, ts, route, dest, label, late, vid}
_ghost_cooldown = {}      # label -> timestamp


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
        # Recently emerged ghosts: label -> emergence info.
        # Consumed by TripManager to fix up newly created trips.
        self._emerged: dict[str, dict] = {}
        # Labels whose ghost was retired by a FIFO-violation this poll
        # cycle.  Drained by the poller and forwarded to TripManager so
        # the underlying Trip can be marked dormant.
        self._newly_dormant: set[str] = set()

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

        # Parse trolley vehicles from normalized data, keyed by label
        trolley_by_label = {}
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
                trolley_by_label[str(label)] = {
                    'lat': lat, 'lng': lng, 'route': route_id,
                    'label': str(label),
                    'vid': vid,
                    'dest': meta.get('headsign', ''),
                    'late': int(delay),
                }

        with _ghost_lock:
            # Prune expired cooldowns
            for lbl in list(_ghost_cooldown):
                if now - _ghost_cooldown[lbl] > 300:
                    del _ghost_cooldown[lbl]

            # -- Linger-based detection --
            # A vehicle is "lingering" when SEPTA repeats the exact same GPS
            # position while on the route shape between 40th St Portal and
            # 37th & Spruce.  After _LINGER_TIME_S of frozen GPS the vehicle
            # is promoted to a ghost (presumed underground).
            #
            # `seen_moving` gates ghost creation against vehicles that have
            # been frozen since our first observation — that's the server
            # restart case for trolleys already in the tunnel (SEPTA keeps
            # rebroadcasting their last-known position).
            #
            # `last_move_ts` is the timestamp of the most recent poll where
            # the vehicle's coords actually changed.  We use it as the basis
            # for enterTs / first_ts (instead of prev['ts'], which is the
            # POLL time and is shared across every live vehicle).  This is
            # what de-clusters enterTs when several tunnel-zone vehicles
            # disappear together (e.g. a SEPTA-side API hiccup): each
            # vehicle's last_move_ts reflects its own movement history.
            for label, tv in trolley_by_label.items():
                if label in _ghosts:
                    continue

                in_zone = self._check_linger_zone(
                    tv['lat'], tv['lng'], tv['route'])
                prev = _prev_positions.get(label)
                existing = _portal_linger.get(label)
                moved = prev is not None and (prev['lat'] != tv['lat']
                                              or prev['lng'] != tv['lng'])
                seen_moving = bool(prev and prev.get('seen_moving')) or moved
                last_move_ts = (now if moved
                                else prev.get('last_move_ts', now) if prev
                                else now)

                if in_zone is not None:
                    exact_repeat = (prev
                                   and prev['lat'] == tv['lat']
                                   and prev['lng'] == tv['lng'])

                    if existing and existing['route'] == tv['route']:
                        if not exact_repeat:
                            # Position changed — reset
                            _portal_linger.pop(label, None)
                    elif exact_repeat and seen_moving:
                        # Start tracking: first frozen position witnessed
                        # AFTER we'd already seen this vehicle move.  Anchor
                        # first_ts to last_move_ts (per-vehicle) rather than
                        # prev['ts'] (shared across the fleet) so this poll's
                        # batch of newly-frozen vehicles get distinct ghost
                        # enterTs values.
                        heading_east = _infer_direction(
                            tv['dest'], direction_fn, tv['vid'])
                        _portal_linger[label] = {
                            'first_ts': last_move_ts,
                            'route': tv['route'],
                            'direction': 'eastbound' if heading_east else 'westbound',
                            'lat': tv['lat'],
                            'lng': tv['lng'],
                        }
                else:
                    _portal_linger.pop(label, None)

                _prev_positions[label] = {
                    'lat': tv['lat'], 'lng': tv['lng'], 'ts': now,
                    'route': tv['route'], 'dest': tv['dest'],
                    'label': tv['label'], 'late': tv['late'],
                    'vid': tv['vid'],
                    'seen_moving': seen_moving,
                    'last_move_ts': last_move_ts,
                }

                linger = _portal_linger.get(label)
                if not linger:
                    continue

                # Promote to ghost after _LINGER_TIME_S
                if ((now - linger['first_ts']) >= _LINGER_TIME_S
                        and label not in _ghost_cooldown):
                    _ghost_cooldown[label] = now
                    _ghosts[label] = {
                        'route': tv['route'],
                        'direction': linger['direction'],
                        'enterTs': int(linger['first_ts'] * 1000),
                        'lingerSec': round(now - linger['first_ts'], 1),
                        'label': tv['label'],
                        'dest': tv['dest'],
                        'late': tv['late'],
                        'entryLat': tv['lat'],
                        'entryLng': tv['lng'],
                    }
                    _portal_linger.pop(label, None)

            # -- Disappearance-based detection --
            # Same `seen_moving` gate as the linger path: only ghost a
            # vanished vehicle if we'd previously witnessed it moving,
            # so a server restart doesn't synthesize ghosts for vehicles
            # whose only observations were frozen at the portal.
            #
            # enterTs is anchored to last_move_ts (per-vehicle), not
            # prev['ts'] (shared poll time): when a SEPTA-side hiccup
            # drops several tunnel-zone vehicles in the same poll, this
            # keeps their ghost enterTs values distinct.
            for label, prev in list(_prev_positions.items()):
                if label in trolley_by_label or label in _ghosts or label in _ghost_cooldown:
                    continue
                if now - prev['ts'] > 90:
                    continue
                if prev['route'] not in TUNNEL_ROUTES:
                    continue
                if not prev.get('seen_moving'):
                    continue
                direction = _check_portal(
                    prev['lat'], prev['lng'], prev['route'], prev['dest'],
                    direction_fn=direction_fn, vid=prev.get('vid'),
                )
                if direction is not None:
                    enter_ts = prev.get('last_move_ts', prev['ts'])
                    _ghost_cooldown[label] = now
                    _ghosts[label] = {
                        'route': prev['route'],
                        'direction': direction,
                        'enterTs': int(enter_ts * 1000),
                        'lingerSec': 0,
                        'label': prev['label'],
                        'dest': prev['dest'],
                        'late': prev['late'],
                        'entryLat': prev['lat'],
                        'entryLng': prev['lng'],
                    }

            # -- Ghost emergence --
            # Ghosts are NEVER removed for being idle: once a vehicle enters
            # the tunnel it must reemerge at some point, so we keep the ghost
            # alive until it reappears in the live feed.
            #
            # FIFO violation: if a same-direction ghost emerges while older
            # ghosts are still pending, the older ones are dropped from the
            # active queue and their fleet labels pushed into _newly_dormant
            # — the poller forwards these to TripManager so the underlying
            # Trip is marked dormant (kept alive so a later return doesn't
            # corrupt counting stats, but invisible to the API).
            for label in list(_ghosts):
                ghost = _ghosts[label]
                tv = trolley_by_label.get(label)
                if not tv:
                    continue
                entry_moved = (abs(tv['lat'] - ghost['entryLat'])
                               + abs(tv['lng'] - ghost['entryLng']))
                if entry_moved <= LINGER_RADIUS:
                    continue
                # Drop older same-direction ghosts (FIFO violation)
                for other_label in list(_ghosts):
                    if other_label == label:
                        continue
                    other = _ghosts[other_label]
                    if other['direction'] != ghost['direction']:
                        continue
                    if other['enterTs'] < ghost['enterTs']:
                        self._newly_dormant.add(other_label)
                        del _ghosts[other_label]
                        _ghost_cooldown.pop(other_label, None)
                # Record tunnel trip timing for monitoring
                if self._monitor:
                    self._monitor.record_tunnel_trip(
                        ghost['route'], ghost['enterTs'] / 1000, now)
                # Store emergence info for TripManager
                self._emerged[label] = {
                    'route': ghost['route'],
                    'direction': ghost['direction'],
                    'entry_lat': ghost['entryLat'],
                    'entry_lng': ghost['entryLng'],
                    'entry_time': ghost['enterTs'] / 1000,
                    'exit_time': now,
                }
                del _ghosts[label]
                _portal_linger.pop(label, None)
                _ghost_cooldown.pop(label, None)

            # Clean stale prev positions
            for label in list(_prev_positions):
                if now - _prev_positions[label]['ts'] > 600:
                    del _prev_positions[label]

    def pop_emerged(self) -> dict[str, dict]:
        """Return and clear recently emerged ghost info.

        Returns {label: {route, direction, entry_lat, entry_lng, exit_time}}.
        Called by the poller to pass emergence data to TripManager.
        """
        with _ghost_lock:
            result = dict(self._emerged)
            self._emerged.clear()
            return result

    def get_ghosts(self) -> list[dict]:
        """Return current active ghosts as a list of dicts.  The 'vid'
        field is the fleet label (stable vehicle identifier)."""
        with _ghost_lock:
            return [{**g, 'vid': label} for label, g in _ghosts.items()]

    def get_all_ghost_labels(self) -> set[str]:
        """All active ghost labels — used to protect their Trip objects
        from stale-pruning in TripManager."""
        with _ghost_lock:
            return set(_ghosts.keys())

    def pop_newly_dormant(self) -> set[str]:
        """Drain the set of fleet labels orphaned by a FIFO-queue violation
        this poll cycle.  The poller forwards these to TripManager so the
        Trip can be marked dormant (kept alive but excluded from API output).
        """
        with _ghost_lock:
            result = set(self._newly_dormant)
            self._newly_dormant.clear()
            return result

    def get_lingering(self) -> dict:
        """Return labels currently lingering near a portal."""
        with _ghost_lock:
            return {label: {
                'direction': info['direction'],
                'route': info['route'],
                'first_ts': int(info['first_ts'] * 1000),
                'lat': info['lat'],
                'lng': info['lng'],
            } for label, info in _portal_linger.items()}
