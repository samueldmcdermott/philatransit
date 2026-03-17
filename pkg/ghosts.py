"""Server-side tunnel ghost detection for trolley routes.

Runs every poll cycle.  Clients fetch /api/ghosts to get current state.
"""

import threading
import time

from .direction import is_heading_to_end

_TUNNEL_ROUTES = {'T1', 'T2', 'T3', 'T4', 'T5'}
_PORTALS = {
    'T1': (39.9553, -75.1942),
    'T2': (39.949588, -75.203171),
    'T3': (39.949588, -75.203171),
    'T4': (39.949588, -75.203171),
    'T5': (39.949588, -75.203171),
}
_TUNNEL_EAST = (39.9525, -75.1626)
_LINGER_RADIUS = 0.006
_LINGER_TIME_S = 60
_STATIONARY_THRESH = 0.0005
_GHOST_MAX_AGE_S = 25 * 60
_EASTBOUND_KW = ['13th', 'market']

_ghost_lock = threading.Lock()
_ghosts = {}              # vid -> ghost info dict
_portal_linger = {}       # vid -> {first_ts, route, direction, lat, lng}
_prev_positions = {}      # vid -> {lat, lng, ts, route, dest, label, late, trip}
_ghost_cooldown = {}      # vid -> timestamp (prevent re-ghosting)


def _safe_float(val):
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _heading_east(dest):
    if not dest:
        return False
    d = dest.lower()
    return any(k in d for k in _EASTBOUND_KW)


def _check_portal(lat, lng, route, dest, vid=None):
    """Return direction if vehicle is near a portal heading into tunnel, else None.

    Uses computed direction from shape projection (via vid) with fallback
    to destination-keyword detection.
    """
    # Determine if vehicle is heading east (toward 13th St / end terminus)
    heading_east = None
    if vid is not None:
        computed = is_heading_to_end(vid)
        if computed is not None:
            heading_east = computed
    if heading_east is None:
        heading_east = _heading_east(dest)

    portal = _PORTALS.get(route)
    if portal:
        d = abs(lat - portal[0]) + abs(lng - portal[1])
        if d < _LINGER_RADIUS and heading_east:
            return 'eastbound'
    d_east = abs(lat - _TUNNEL_EAST[0]) + abs(lng - _TUNNEL_EAST[1])
    if d_east < _LINGER_RADIUS and not heading_east:
        return 'westbound'
    return None


def _trolley_vid(v):
    """Match client-side vehicle ID logic (processTransitData)."""
    trip = v.get('trip')
    if trip and str(trip) not in ('0', 'None', ''):
        return str(trip)
    vid = v.get('VehicleID')
    if vid and str(vid) not in ('0', 'None', '') and 'schedBased' not in str(vid):
        return str(vid)
    label = v.get('label', '')
    if label and str(label) not in ('None', '0'):
        return f"{label}_{v.get('lat')}_{v.get('lng')}"
    return None


def process_tunnel_ghosts(transit_routes):
    """Called each poll cycle to detect tunnel entries/exits for trolley routes."""
    now = time.time()

    # Parse all trolley vehicles from transit cache
    vehicles = {}
    for route_id in _TUNNEL_ROUTES:
        for v in transit_routes.get(route_id, []):
            if v.get('late') == 998:
                continue
            label = v.get('label', '')
            if label in ('None', None, '', '0'):
                continue
            vid = _trolley_vid(v)
            if not vid:
                continue
            lat = _safe_float(v.get('lat'))
            lng = _safe_float(v.get('lng'))
            if lat is None or lng is None:
                continue
            vehicles[vid] = {
                'lat': lat, 'lng': lng, 'route': route_id,
                'label': str(label),
                'dest': v.get('destination') or v.get('dest') or '',
                'late': int(v.get('late', 0)),
                'trip': str(v.get('trip') or ''),
            }

    with _ghost_lock:
        # Prune expired cooldowns
        for vid in list(_ghost_cooldown):
            if now - _ghost_cooldown[vid] > 300:
                del _ghost_cooldown[vid]

        # -- Linger-based detection --
        for vid, tv in vehicles.items():
            if vid in _ghosts:
                continue

            direction = _check_portal(tv['lat'], tv['lng'], tv['route'], tv['dest'], vid=vid)
            if direction is None:
                _portal_linger.pop(vid, None)
                _prev_positions[vid] = {
                    'lat': tv['lat'], 'lng': tv['lng'], 'ts': now,
                    'route': tv['route'], 'dest': tv['dest'],
                    'label': tv['label'], 'late': tv['late'], 'trip': tv['trip'],
                }
                continue

            existing = _portal_linger.get(vid)
            if existing and existing['route'] == tv['route']:
                moved = abs(tv['lat'] - existing['lat']) + abs(tv['lng'] - existing['lng'])
                if moved >= _STATIONARY_THRESH:
                    existing['first_ts'] = now
                    existing['lat'] = tv['lat']
                    existing['lng'] = tv['lng']
            else:
                # Credit time from previous position if GPS was already frozen
                seed_ts = now
                prev = _prev_positions.get(vid)
                if prev:
                    moved = abs(tv['lat'] - prev['lat']) + abs(tv['lng'] - prev['lng'])
                    if moved < _STATIONARY_THRESH:
                        seed_ts = prev['ts']
                _portal_linger[vid] = {
                    'first_ts': seed_ts, 'route': tv['route'],
                    'direction': direction, 'lat': tv['lat'], 'lng': tv['lng'],
                }

            linger = _portal_linger[vid]
            if (now - linger['first_ts']) >= _LINGER_TIME_S and vid not in _ghost_cooldown:
                _ghost_cooldown[vid] = now
                _ghosts[vid] = {
                    'route': tv['route'],
                    'direction': direction,
                    'enterTs': int(linger['first_ts'] * 1000),
                    'lingerSec': round(now - linger['first_ts'], 1),
                    'label': tv['label'],
                    'dest': tv['dest'],
                    'late': tv['late'],
                    'trip': tv['trip'],
                    'entryLat': tv['lat'],
                    'entryLng': tv['lng'],
                }
                _portal_linger.pop(vid, None)

            _prev_positions[vid] = {
                'lat': tv['lat'], 'lng': tv['lng'], 'ts': now,
                'route': tv['route'], 'dest': tv['dest'],
                'label': tv['label'], 'late': tv['late'], 'trip': tv['trip'],
            }

        # -- Disappearance-based detection --
        for vid, prev in list(_prev_positions.items()):
            if vid in vehicles or vid in _ghosts or vid in _ghost_cooldown:
                continue
            if now - prev['ts'] > 90:
                continue
            if prev['route'] not in _TUNNEL_ROUTES:
                continue
            direction = _check_portal(prev['lat'], prev['lng'], prev['route'], prev['dest'], vid=vid)
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
                    'trip': prev['trip'],
                    'entryLat': prev['lat'],
                    'entryLng': prev['lng'],
                }

        # -- Ghost emergence / expiry --
        for vid in list(_ghosts):
            ghost = _ghosts[vid]
            age_s = now - ghost['enterTs'] / 1000
            if age_s > _GHOST_MAX_AGE_S:
                del _ghosts[vid]
                _ghost_cooldown.pop(vid, None)
                continue

            tv = vehicles.get(vid)
            if tv:
                # Real vehicle reappeared — check if it moved from frozen position
                entry_moved = abs(tv['lat'] - ghost['entryLat']) + abs(tv['lng'] - ghost['entryLng'])
                if entry_moved > _LINGER_RADIUS:
                    del _ghosts[vid]
                    _portal_linger.pop(vid, None)
                    _ghost_cooldown.pop(vid, None)

        # Clean stale prev positions
        for vid in list(_prev_positions):
            if now - _prev_positions[vid]['ts'] > 600:
                del _prev_positions[vid]


def get_ghost_list():
    """Return current ghosts as a list of dicts (for the /api/ghosts endpoint)."""
    with _ghost_lock:
        return [{**g, 'vid': vid} for vid, g in _ghosts.items()]
