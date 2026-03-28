"""Route configuration utilities.

Builds the internal route_config dict (route_id → RouteInfo) that the
TripManager uses to look up origin, destination, and bearing for each
route.  The data comes from the provider's get_route_config() combined
with terminus data from the shape registry.
"""

from __future__ import annotations

from .. import geo


def build_route_config(provider_config: dict, termini: dict) -> dict:
    """Merge provider route definitions with terminus data.

    Parameters:
        provider_config: from Provider.get_route_config()
            {"modes": {mode: {"type": str, "routes": [route_dict]}}, ...}
        termini: from RouteShapeRegistry.termini
            {route_id: (start_name, start_lat, start_lng, end_name, end_lat, end_lng)}

    Returns:
        {route_id: {"origin": str, "destination": str,
                     "origin_to_dest_bearing": float, ...}}
    """
    route_config = {}

    for mode_name, mode_info in provider_config.get("modes", {}).items():
        for route in mode_info.get("routes", []):
            rid = route.get("id", "")
            if not rid:
                continue

            term = termini.get(rid)
            if term:
                origin = term[0]
                destination = term[3]
                bearing = geo.bearing(term[1], term[2], term[4], term[5])
            else:
                origin = ""
                destination = ""
                bearing = 0.0

            route_config[rid] = {
                "id": rid,
                "label": route.get("label", rid),
                "color": route.get("color", "#78818c"),
                "mode": mode_name,
                "origin": origin,
                "destination": destination,
                "origin_to_dest_bearing": round(bearing, 1),
                "gtfs": route.get("gtfs", rid),
                "api_ids": route.get("api_ids", [rid]),
                "alert_ids": route.get("alert_ids", []),
                "meta": route.get("meta", {}),
            }

            if term:
                route_config[rid]["origin_lat"] = term[1]
                route_config[rid]["origin_lng"] = term[2]
                route_config[rid]["dest_lat"] = term[4]
                route_config[rid]["dest_lng"] = term[5]

    return route_config
