"""Provider abstraction layer.

Defines the interface between external transit APIs and the internal
Trip-centric model.  A Provider encapsulates all external API calls;
internal logic (Trip, Route, shapes, geo) never imports from a provider.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Protocol, TypedDict


# ── Normalized data types ────────────────────────────────────────────


class NormalizedVehicle(TypedDict):
    """Raw vehicle sighting from a provider API — identity + position only.

    Everything else (trip lifecycle, stops, direction) is computed
    internally by the TripManager.
    """
    vehicle_id: str          # stable ID (provider assigns)
    route_id: str            # canonical route key
    lat: float               # raw GPS latitude
    lng: float               # raw GPS longitude
    label: str               # human-readable fleet ID (e.g. fleet number)
    meta: dict               # extensible provider-specific raw data
                             # e.g. {"headsign": "13th St", "delay": 3,
                             #        "api_bearing": 85, "api_trip_id": "5678"}


class RouteInfo(TypedDict, total=False):
    """Static route definition.

    Origin/destination/bearing are route properties, not vehicle properties.
    """
    id: str
    label: str
    color: str
    mode: str                # e.g. "SUBWAY", "TROLLEY", "BUS", "RAIL"
    origin: str              # start terminus name
    destination: str         # end terminus name
    origin_lat: float
    origin_lng: float
    dest_lat: float
    dest_lng: float
    origin_to_dest_bearing: float   # compass bearing from origin to destination
    gtfs: str
    api_ids: list[str]       # provider API route IDs
    alert_ids: list[str]
    meta: dict               # extensible route metadata


# ── Tunnel detector protocol ────────────────────────────────────────


class TunnelDetector(Protocol):
    """Optional provider-specific tunnel/underground detector."""

    def process(self, vehicles: dict[str, list[NormalizedVehicle]],
                direction_fn) -> None:
        """Process a poll cycle.  direction_fn(vid) -> bool|None."""
        ...

    def get_ghosts(self) -> list[dict]:
        """Return current ghost vehicles."""
        ...

    def get_lingering(self) -> dict:
        """Return vehicles lingering near portals."""
        ...


class DetourDetector(Protocol):
    """Optional provider-specific detour detector."""

    def check_detour(self, vehicle_id: str, route_id: str,
                     lat: float, lng: float) -> bool:
        """Return True if the vehicle is currently on a known detour route."""
        ...


# ── Provider ABC ────────────────────────────────────────────────────


class Provider(ABC):
    """Abstract base for transit data providers.

    Concrete implementations (e.g. SEPTA) encapsulate all external API
    calls and return normalized data for the internal Trip system.
    """

    @abstractmethod
    def poll_transit(self) -> dict[str, list[NormalizedVehicle]]:
        """Fetch transit vehicles (bus, trolley, subway).

        Returns {route_id: [NormalizedVehicle, ...]}.
        """

    @abstractmethod
    def poll_rail(self) -> list[NormalizedVehicle]:
        """Fetch rail/commuter vehicles.

        Returns a flat list of NormalizedVehicle.
        """

    @abstractmethod
    def fetch_stops(self, route_id: str) -> list[dict]:
        """Fetch stops for a route.

        Returns [{name, lat, lng, stop_id?, ...}, ...].
        """

    @abstractmethod
    def fetch_alerts(self) -> list[dict]:
        """Fetch service alerts.

        Returns provider-specific alert dicts.
        """

    @abstractmethod
    def fetch_stop_predictions(self, stop_ids: set[str],
                               route_ids: list[str]) -> dict:
        """Fetch arrival predictions for given stops and routes.

        Returns {stop_id: [prediction, ...], ...}.
        """

    @abstractmethod
    def get_tunnel_detector(self) -> TunnelDetector | None:
        """Return provider-specific tunnel detector, or None."""

    @abstractmethod
    def get_detour_detector(self) -> DetourDetector | None:
        """Return provider-specific detour detector, or None."""

    @abstractmethod
    def get_route_config(self) -> dict:
        """Return route configuration for the frontend.

        Returns {"modes": {mode: {"type": str, "routes": [RouteInfo]}},
                 "provider": str, ...}.
        """
