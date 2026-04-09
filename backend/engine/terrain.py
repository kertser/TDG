"""
Terrain factor lookup — expanded 12-type taxonomy with DB-backed cell support.

Supports three modes:
  1. DB-backed cells: terrain_cells dict[snail_path → terrain_type] + grid_service
  2. Legacy regions: scenario terrain_meta JSONB with bounding-box regions
  3. Default: everything is "open"

Also provides elevation-aware methods for movement slope penalty,
detection height bonus, and combat height advantage.
"""

from __future__ import annotations

import math
from typing import Any

from shapely.geometry import Point, box


# ── Expanded terrain taxonomy (12 types) ─────────────────────
# Section 1.7.3 of AGENTS.MD

TERRAIN_MODIFIERS = {
    #                  movement  visibility  protection  attack
    "road":           {"movement": 1.0,  "visibility": 1.0,  "protection": 1.0,  "attack": 1.0},
    "open":           {"movement": 0.8,  "visibility": 1.0,  "protection": 1.0,  "attack": 1.0},
    "forest":         {"movement": 0.5,  "visibility": 0.4,  "protection": 1.4,  "attack": 0.7},
    "urban":          {"movement": 0.4,  "visibility": 0.5,  "protection": 1.5,  "attack": 0.8},
    "water":          {"movement": 0.05, "visibility": 1.0,  "protection": 0.5,  "attack": 0.2},
    "fields":         {"movement": 0.7,  "visibility": 0.9,  "protection": 1.0,  "attack": 0.9},
    "marsh":          {"movement": 0.3,  "visibility": 0.8,  "protection": 0.8,  "attack": 0.5},
    "desert":         {"movement": 0.7,  "visibility": 1.0,  "protection": 0.8,  "attack": 1.0},
    "scrub":          {"movement": 0.6,  "visibility": 0.7,  "protection": 1.2,  "attack": 0.8},
    "bridge":         {"movement": 1.0,  "visibility": 1.0,  "protection": 0.8,  "attack": 0.9},
    "mountain":       {"movement": 0.3,  "visibility": 0.6,  "protection": 1.5,  "attack": 0.6},
    "orchard":        {"movement": 0.5,  "visibility": 0.6,  "protection": 1.2,  "attack": 0.8},
}

# Backwards-compatible flat dicts
TERRAIN_MOVEMENT_FACTOR = {k: v["movement"] for k, v in TERRAIN_MODIFIERS.items()}
TERRAIN_VISIBILITY_FACTOR = {k: v["visibility"] for k, v in TERRAIN_MODIFIERS.items()}
TERRAIN_PROTECTION_FACTOR = {k: v["protection"] for k, v in TERRAIN_MODIFIERS.items()}
TERRAIN_ATTACK_MOD = {k: v["attack"] for k, v in TERRAIN_MODIFIERS.items()}

# Color scheme for frontend rendering
TERRAIN_COLORS = {
    "road":     "#666666",
    "open":     "#90EE90",
    "forest":   "#228B22",
    "urban":    "#A0A0A0",
    "water":    "#4488FF",
    "fields":   "#DAA520",
    "marsh":    "#8B8B00",
    "desert":   "#DEB887",
    "scrub":    "#9ACD32",
    "bridge":   "#888888",
    "mountain": "#8B7355",
    "orchard":  "#556B2F",
}

# All valid terrain type names
TERRAIN_TYPES = list(TERRAIN_MODIFIERS.keys())


class TerrainService:
    """
    Terrain lookup with support for DB-backed cells, legacy regions, or default.

    Construction modes:
      TerrainService(terrain_meta={...})                      # legacy
      TerrainService(terrain_cells={...}, grid_service=gs)    # DB-backed
      TerrainService()                                        # default (all open)
    """

    def __init__(
        self,
        terrain_meta: dict | None = None,
        terrain_cells: dict[str, str] | None = None,
        elevation_cells: dict[str, dict] | None = None,
        grid_service=None,
    ):
        # DB-backed cells mode
        self._cells = terrain_cells  # {snail_path: terrain_type}
        self._elevation = elevation_cells  # {snail_path: {elevation_m, slope_deg, aspect_deg}}
        self._grid = grid_service
        self._cell_depth = 1  # default analysis depth

        # Detect cell depth from data
        if self._cells:
            sample = next(iter(self._cells.keys()), "")
            self._cell_depth = sample.count("-") if sample else 0

        # Legacy regions mode (fallback)
        self._regions: list[tuple[str, Any]] = []
        if terrain_meta and "regions" in terrain_meta:
            for r in terrain_meta["regions"]:
                terrain_type = r.get("type", "open")
                bounds = r.get("bounds")  # [west, south, east, north]
                if bounds and len(bounds) == 4:
                    poly = box(bounds[0], bounds[1], bounds[2], bounds[3])
                    self._regions.append((terrain_type, poly))

        # ── Fast spatial index: bypass point_to_snail for lookups ──
        # Build a direct (lon,lat) → snail_path lookup using the grid's
        # local coordinate system. This avoids repeated pyproj transforms.
        self._fast_lookup: dict | None = None
        self._fast_elev_lookup: dict | None = None
        self._local_params: dict | None = None
        if self._cells and self._grid:
            self._build_fast_index()

    def _build_fast_index(self):
        """Build a fast integer-grid index for O(1) terrain/elevation lookups.

        Instead of calling point_to_snail (which does pyproj transform + snail
        arithmetic every time), we precompute the grid's local coordinate params
        and do fast floor-division at query time.

        Gracefully falls back to slow path if grid doesn't have pyproj attrs
        (e.g. mock grids in tests).
        """
        gs = self._grid
        # Check that the grid has the necessary internal attributes
        if not hasattr(gs, '_origin_lat') or not hasattr(gs, '_to_local'):
            # Mock or incomplete grid — skip fast index, fall back to slow path
            return

        # Precompute projection transform parameters (from grid_service)
        self._local_params = {
            "origin_lat": gs._origin_lat,
            "origin_lon": gs._origin_lon,
            "square_size": gs._square_size,
            "columns": gs._columns,
            "rows": gs._rows,
            "recursion_base": gs._recursion_base,
            "rot_rad": gs._rot_rad,
            # Precompute trig for rotation
            "cos_neg_rot": math.cos(-gs._rot_rad),
            "sin_neg_rot": math.sin(-gs._rot_rad),
        }
        # We keep a reference to the pyproj transformers for the initial
        # geo→local transform but avoid re-creating them
        self._to_local = gs._to_local

        # Pre-build the snail offset map for the configured recursion base
        # (always 3 in practice)
        from backend.services.grid_service import OFFSET_TO_SNAIL
        self._offset_to_snail = OFFSET_TO_SNAIL

        # Store labeling scheme info for _make_top_label equivalent
        self._labeling = gs._labeling

    def _fast_point_to_snail(self, lon: float, lat: float) -> str | None:
        """Ultra-fast point→snail using pre-cached local projection params."""
        if self._local_params is None:
            return self._grid.point_to_snail(lat, lon, depth=self._cell_depth) if self._grid else None

        p = self._local_params
        # Project geo → local using the same pyproj transformer
        x, y = self._to_local.transform(lon, lat)

        # Undo grid rotation
        ux = x * p["cos_neg_rot"] - y * p["sin_neg_rot"]
        uy = x * p["sin_neg_rot"] + y * p["cos_neg_rot"]

        sq = p["square_size"]
        col = int(ux // sq)
        row = int(uy // sq)
        if not (0 <= col < p["columns"] and 0 <= row < p["rows"]):
            return None

        # Build top label
        if self._labeling == "alphanumeric":
            display_row = p["rows"] - row
            top_label = f"{chr(ord('A') + col)}{display_row}"
        else:
            display_row_idx = p["rows"] - 1 - row
            top_label = str(display_row_idx * p["columns"] + col + 1)

        # Recursive snail subdivision
        lx = ux - col * sq
        ly = uy - row * sq
        size = sq
        rb = p["recursion_base"]
        digits: list[str] = []

        for _ in range(self._cell_depth):
            sub_size = size / rb
            sub_col = min(int(lx / sub_size), rb - 1)
            sub_row = min(int(ly / sub_size), rb - 1)
            digit = self._offset_to_snail.get((sub_col, sub_row))
            if digit is None:
                break
            digits.append(str(digit))
            lx -= sub_col * sub_size
            ly -= sub_row * sub_size
            size = sub_size

        if digits:
            return top_label + "-" + "-".join(digits)
        return top_label

    def get_terrain_at(self, lon: float, lat: float) -> str:
        """Return terrain type string at the given point."""
        # Mode 1: DB-backed cells with fast index
        if self._cells and self._grid:
            snail = self._fast_point_to_snail(lon, lat)
            if snail and snail in self._cells:
                return self._cells[snail]
            # Try parent paths (lower depth)
            if snail:
                parts = snail.split("-")
                for i in range(len(parts) - 1, 0, -1):
                    parent = "-".join(parts[:i])
                    if parent in self._cells:
                        return self._cells[parent]

        # Mode 2: Legacy bounding-box regions
        if self._regions:
            pt = Point(lon, lat)
            for terrain_type, poly in self._regions:
                if poly.contains(pt):
                    return terrain_type

        return "open"  # default

    def get_modifiers_at(self, lon: float, lat: float) -> dict:
        """Return full modifier dict {movement, visibility, protection, attack} at point."""
        t = self.get_terrain_at(lon, lat)
        return TERRAIN_MODIFIERS.get(t, TERRAIN_MODIFIERS["open"]).copy()

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

    # ── Elevation-aware methods ──────────────────────────

    def get_elevation_at(self, lon: float, lat: float) -> float:
        """Return elevation in meters at point. Returns 0.0 if no data."""
        if not self._elevation or not self._grid:
            return 0.0
        snail = self._fast_point_to_snail(lon, lat)
        if snail and snail in self._elevation:
            return self._elevation[snail].get("elevation_m", 0.0)
        return 0.0

    def get_slope_at(self, lon: float, lat: float) -> float:
        """Return slope in degrees at point. Returns 0.0 if no data."""
        if not self._elevation or not self._grid:
            return 0.0
        snail = self._fast_point_to_snail(lon, lat)
        if snail and snail in self._elevation:
            return self._elevation[snail].get("slope_deg", 0.0)
        return 0.0

    def slope_movement_factor(self, lon: float, lat: float) -> float:
        """
        Slope penalty for movement: max(0.2, 1.0 - slope_deg / 45).
        Steep slopes dramatically slow movement.
        """
        slope = self.get_slope_at(lon, lat)
        if slope <= 0:
            return 1.0
        return max(0.2, 1.0 - slope / 45.0)

    def elevation_advantage(
        self,
        from_lon: float, from_lat: float,
        to_lon: float, to_lat: float,
    ) -> float:
        """
        Compute elevation difference advantage (meters).
        Positive = 'from' is higher than 'to' (firing downhill).
        """
        from_elev = self.get_elevation_at(from_lon, from_lat)
        to_elev = self.get_elevation_at(to_lon, to_lat)
        return from_elev - to_elev

    def detection_height_bonus(
        self,
        observer_lon: float, observer_lat: float,
        target_lon: float, target_lat: float,
    ) -> float:
        """
        Detection range multiplier from height advantage.
        +10% per 50m height advantage (Section 1.7.4).
        Returns multiplier >= 1.0 (no penalty for being lower, only bonus for higher).
        """
        adv = self.elevation_advantage(observer_lon, observer_lat, target_lon, target_lat)
        if adv <= 0:
            return 1.0
        return 1.0 + (adv / 500.0)  # +10% per 50m → adv/500

    def combat_height_modifier(
        self,
        attacker_lon: float, attacker_lat: float,
        target_lon: float, target_lat: float,
    ) -> float:
        """
        Fire effectiveness modifier from height advantage.
        +15% per 50m height advantage, capped at 0.7–1.5 (Section 1.7.4).
        """
        adv = self.elevation_advantage(attacker_lon, attacker_lat, target_lon, target_lat)
        mod = 1.0 + 0.15 * (adv / 50.0)
        return max(0.7, min(1.5, mod))


# ── Session-level terrain data cache ─────────────────────────
# Avoids re-loading terrain/elevation from DB on every viewshed request.
# Invalidated when terrain analysis runs (via clear_terrain_cache).
# Long TTL: terrain data rarely changes during a game session.

import threading
_terrain_cache_lock = threading.Lock()
_terrain_cache: dict[str, dict] = {}  # session_id_str → {terrain_cells, elevation_cells, ts}

TERRAIN_CACHE_TTL = 86400  # 24 hours — effectively session-lifetime; explicitly cleared on re-analysis


def get_cached_terrain_data(session_id_str: str) -> dict | None:
    """Get cached terrain/elevation dicts for a session, or None if expired/missing."""
    import time
    with _terrain_cache_lock:
        entry = _terrain_cache.get(session_id_str)
        if entry and (time.time() - entry["ts"]) < TERRAIN_CACHE_TTL:
            return entry
    return None


def set_cached_terrain_data(
    session_id_str: str,
    terrain_cells: dict[str, str] | None,
    elevation_cells: dict[str, dict] | None,
):
    """Cache terrain/elevation dicts for a session."""
    import time
    with _terrain_cache_lock:
        _terrain_cache[session_id_str] = {
            "terrain_cells": terrain_cells,
            "elevation_cells": elevation_cells,
            "ts": time.time(),
        }


def clear_terrain_cache(session_id_str: str | None = None):
    """Clear terrain cache for a session (or all sessions)."""
    with _terrain_cache_lock:
        if session_id_str:
            _terrain_cache.pop(session_id_str, None)
        else:
            _terrain_cache.clear()


