"""SEPTA detour detection for trolley routes.

When the trolley tunnel is closed, T1-T5 divert to surface streets in a
zone north of Baltimore Ave (between 38th and 42nd).  A vehicle whose GPS
is in this zone is flagged as on_detour.

Hysteresis: once a vehicle enters detour state, it stays on_detour until
it leaves a slightly expanded zone, preventing flicker at the boundary.
"""

from __future__ import annotations

from ... import geo
from .constants import DETOUR_ZONE, DETOUR_ROUTES

# Expand zone by ~100m for exit hysteresis
_EXIT_PAD = 0.001  # ~110m latitude

# T1 detour: Filbert & 38th is the virtual outbound terminus.  When a vehicle
# reaches this point and moves away, the trip should flip direction.
_T1_TURN = (39.9574, -75.1981)
_T1_NEAR = 120  # meters
_T1_FAR = 180   # meters


class SeptaDetourDetector:
    """SEPTA implementation of the DetourDetector protocol."""

    def __init__(self):
        self._in_detour: set[str] = set()       # vehicle_ids currently on detour
        self._near_t1_turn: set[str] = set()    # vids currently within T1 turnaround radius

    def check_detour(self, vehicle_id: str, route_id: str,
                     lat: float, lng: float) -> bool:
        """Return True if this vehicle is on a known detour route."""
        if route_id not in DETOUR_ROUTES:
            self._in_detour.discard(vehicle_id)
            self._near_t1_turn.discard(vehicle_id)
            return False

        in_zone = (DETOUR_ZONE['minLat'] <= lat <= DETOUR_ZONE['maxLat']
                   and DETOUR_ZONE['minLng'] <= lng <= DETOUR_ZONE['maxLng'])

        if in_zone:
            self._in_detour.add(vehicle_id)
            return True

        # Hysteresis: if already in detour, use expanded zone for exit
        if vehicle_id in self._in_detour:
            in_expanded = (
                (DETOUR_ZONE['minLat'] - _EXIT_PAD) <= lat <= (DETOUR_ZONE['maxLat'] + _EXIT_PAD)
                and (DETOUR_ZONE['minLng'] - _EXIT_PAD) <= lng <= (DETOUR_ZONE['maxLng'] + _EXIT_PAD)
            )
            if in_expanded:
                return True
            self._in_detour.discard(vehicle_id)

        self._near_t1_turn.discard(vehicle_id)
        return False

    def detect_turnaround(self, vehicle_id: str, route_id: str,
                          lat: float, lng: float,
                          toward_destination: bool) -> bool:
        """T1 detours turn around at Filbert & 38th.  Returns True the moment
        an outbound vehicle leaves the turnaround radius after entering it."""
        if route_id != 'T1':
            return False

        if not toward_destination:
            # Heading back; clear flag so it doesn't re-trigger.
            self._near_t1_turn.discard(vehicle_id)
            return False

        d = geo.distance(lat, lng, *_T1_TURN)
        if d < _T1_NEAR:
            self._near_t1_turn.add(vehicle_id)
            return False
        if d > _T1_FAR and vehicle_id in self._near_t1_turn:
            self._near_t1_turn.discard(vehicle_id)
            return True
        return False
