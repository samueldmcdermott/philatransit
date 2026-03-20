"""Read version from the VERSION file at repo root."""

from .helpers import BASE

def get_version() -> str:
    try:
        return (BASE / "VERSION").read_text().strip()
    except FileNotFoundError:
        return "unknown"
