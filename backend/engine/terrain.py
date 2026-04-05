"""
Terrain factor lookup.

Given a point, return terrain type and modifiers.
MVP: simple polygon regions from scenario terrain_meta JSONB.
"""

from __future__ import annotations

from shapely.geometry import Point, box


# Default terrain modifiers per type
TERRAIN_MOVEMENT_FACTOR = {
    "road": 1.0,
    "open": 0.8,
    "forest": 0.5,
    "urban": 0.4,
    "water": 0.1,
}

TERRAIN_VISIBILITY_FACTOR = {
    "road": 1.0,
    "open": 1.0,
    "forest": 0.5,
    "urban": 0.4,
    "water": 1.0,
}

TERRAIN_PROTECTION_FACTOR = {
    "road": 1.0,
    "open": 1.0,
    "forest": 1.3,
    "urban": 1.5,
    "water": 1.0,
}

TERRAIN_ATTACK_MOD = {
    "road": 1.0,
    "open": 1.0,
    "forest": 0.7,
    "urban": 0.8,
    "water": 0.3,
}


class TerrainService:
    """Terrain lookup from scenario terrain_meta regions."""

    def __init__(self, terrain_meta: dict | None = None):
        self._regions: list[tuple[str, any]] = []
        if terrain_meta and "regions" in terrain_meta:
            for r in terrain_meta["regions"]:
                terrain_type = r.get("type", "open")
                bounds = r.get("bounds")  # [west, south, east, north]
                if bounds and len(bounds) == 4:
                    poly = box(bounds[0], bounds[1], bounds[2], bounds[3])
                    self._regions.append((terrain_type, poly))

    def get_terrain_at(self, lon: float, lat: float) -> str:
        """Return terrain type string at the given point."""
        pt = Point(lon, lat)
        for terrain_type, poly in self._regions:
            if poly.contains(pt):
                return terrain_type
        return "open"  # default

    def movement_factor(self, lon: float, lat: float) -> float:
        t = self.get_terrain_at(lon, lat)
        return TERRAIN_MOVEMENT_FACTOR.get(t, 0.8)

    def visibility_factor(self, lon: float, lat: float) -> float:
        t = self.get_terrain_at(lon, lat)
        return TERRAIN_VISIBILITY_FACTOR.get(t, 1.0)

    def protection_factor(self, lon: float, lat: float) -> float:
        t = self.get_terrain_at(lon, lat)
        return TERRAIN_PROTECTION_FACTOR.get(t, 1.0)

    def attack_modifier(self, lon: float, lat: float) -> float:
        t = self.get_terrain_at(lon, lat)
        return TERRAIN_ATTACK_MOD.get(t, 1.0)

