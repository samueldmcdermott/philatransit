"""SEPTA detour detection for trolley routes.

When the trolley tunnel is closed, T1-T5 divert to surface streets in a
zone north of Baltimore Ave (between 38th and 42nd).  A vehicle whose GPS
is in this zone is flagged as on_detour.

Hysteresis: once a vehicle enters detour state, it stays on_detour until
it leaves a slightly expanded zone, preventing flicker at the boundary.
"""

from __future__ import annotations

from .constants import DETOUR_ZONE, DETOUR_ROUTES

# Expand zone by ~100m for exit hysteresis
_EXIT_PAD = 0.001  # ~110m latitude


class SeptaDetourDetector:
    """SEPTA implementation of the DetourDetector protocol."""

    def __init__(self):
        self._in_detour: set[str] = set()  # vehicle_ids currently on detour

    def check_detour(self, vehicle_id: str, route_id: str,
                     lat: float, lng: float) -> bool:
        """Return True if this vehicle is on a known detour route."""
        if route_id not in DETOUR_ROUTES:
            self._in_detour.discard(vehicle_id)
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

        return False
