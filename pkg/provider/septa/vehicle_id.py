"""SEPTA-specific vehicle identification.

Extracts a stable vehicle identifier from raw SEPTA API vehicle dicts.
"""


def extract_vehicle_id(v: dict):
    """Build a stable vehicle identifier from a SEPTA vehicle dict.

    Priority: trip ID > VehicleID > label-based fallback.
    Returns None if no usable identifier can be constructed.
    """
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
