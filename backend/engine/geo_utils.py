"""
Geographic utility functions — single source of truth for lat/lon ↔ meters conversions.

All engine modules should import from here instead of using local approximation constants.
"""

from __future__ import annotations

import math

METERS_PER_DEG_LAT = 111_320.0


def meters_per_deg_lon(lat_deg: float) -> float:
    """Longitude-degree length in meters at a given latitude."""
    return METERS_PER_DEG_LAT * math.cos(math.radians(lat_deg))


def planar_offset_m(p_from, p_to) -> tuple[float, float, float]:
    """
    Returns (dx_east_m, dy_north_m, dist_m) using local tangent plane at p_from latitude.

    p_from / p_to must be Shapely Point objects (or anything with .x (lon) and .y (lat)).
    """
    dy_m = (p_to.y - p_from.y) * METERS_PER_DEG_LAT
    dx_m = (p_to.x - p_from.x) * meters_per_deg_lon(p_from.y)
    return dx_m, dy_m, math.hypot(dx_m, dy_m)


def bearing_deg(p_from, p_to) -> float:
    """
    Compass bearing from p_from to p_to in degrees (0=N, 90=E, 180=S, 270=W).

    p_from / p_to must be Shapely Point objects.
    """
    dx, dy, _ = planar_offset_m(p_from, p_to)
    return math.degrees(math.atan2(dx, dy)) % 360


def distance_m_latlon(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Flat-earth distance in metres between two (lat, lon) pairs."""
    dy = (lat2 - lat1) * METERS_PER_DEG_LAT
    dx = (lon2 - lon1) * meters_per_deg_lon((lat1 + lat2) / 2.0)
    return math.hypot(dx, dy)

