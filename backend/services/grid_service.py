"""
GridService – deterministic tactical grid and recursive snail addressing.

This is the core of the tactical location system. All grid math is
code-based and deterministic – never delegated to LLM prompts.
"""

from __future__ import annotations

import math
import re
from typing import Any

from pyproj import Transformer
from shapely.geometry import Polygon, Point, box, mapping
from shapely.affinity import rotate, translate

# Snail (spiral) numbering within a 3×3 sub-grid:
#   1 2 3
#   8 9 4
#   7 6 5
#
# Mapping: snail_number → (col, row) where row 0 = bottom, row 2 = top
SNAIL_TO_OFFSET: dict[int, tuple[int, int]] = {
    1: (0, 2), 2: (1, 2), 3: (2, 2),
    4: (2, 1), 5: (2, 0), 6: (1, 0),
    7: (0, 0), 8: (0, 1), 9: (1, 1),
}

# Inverse: (col, row) → snail_number
OFFSET_TO_SNAIL: dict[tuple[int, int], int] = {v: k for k, v in SNAIL_TO_OFFSET.items()}


class GridService:
    """
    Tactical grid with recursive 3×3 "snail" subdivision.

    Works in a local Cartesian coordinate system (meters) projected
    from the grid origin, with an Azimuthal Equidistant projection.
    """

    def __init__(self, grid_def: Any):
        """
        Initialize from a GridDefinition ORM object or dict-like.

        Expected attributes:
            origin          – WKB/Shapely Point or (lon, lat) tuple
            orientation_deg – grid rotation from north (degrees)
            base_square_size_m – side length of top-level square (meters)
            columns         – number of top-level columns
            rows            – number of top-level rows
            labeling_scheme – 'alphanumeric' or 'numeric'
            recursion_base  – subdivision factor (default 3)
            max_depth       – max recursion depth (default 3)
        """
        # Extract origin coordinates
        if hasattr(grid_def, 'origin') and grid_def.origin is not None:
            from geoalchemy2.shape import to_shape
            try:
                origin_point = to_shape(grid_def.origin)
                self._origin_lon = origin_point.x
                self._origin_lat = origin_point.y
            except Exception:
                # Fallback: might be a raw shapely Point
                self._origin_lon = grid_def.origin.x
                self._origin_lat = grid_def.origin.y
        else:
            self._origin_lon = 0.0
            self._origin_lat = 0.0

        self._orientation_deg = getattr(grid_def, 'orientation_deg', 0.0) or 0.0
        self._square_size = getattr(grid_def, 'base_square_size_m', 1000.0)
        self._columns = getattr(grid_def, 'columns', 10)
        self._rows = getattr(grid_def, 'rows', 10)
        self._labeling = getattr(grid_def, 'labeling_scheme', 'alphanumeric')
        self._recursion_base = getattr(grid_def, 'recursion_base', 3)
        self._max_depth = getattr(grid_def, 'max_depth', 3)

        # Set up projections: WGS84 ↔ local Azimuthal Equidistant (meters)
        self._to_local = Transformer.from_crs(
            "EPSG:4326",
            f"+proj=aeqd +lat_0={self._origin_lat} +lon_0={self._origin_lon} +datum=WGS84 +units=m",
            always_xy=True,
        )
        self._to_geo = Transformer.from_crs(
            f"+proj=aeqd +lat_0={self._origin_lat} +lon_0={self._origin_lon} +datum=WGS84 +units=m",
            "EPSG:4326",
            always_xy=True,
        )

        # Pre-compute rotation (degrees, clockwise from north → math CCW from east)
        self._rot_rad = math.radians(self._orientation_deg)

    # ── Label parsing helpers ──────────────────────────

    def _parse_top_label(self, label: str) -> tuple[int, int] | None:
        """Parse top-level square label → (col, row) 0-indexed.

        Row numbering: label row 1 = top of grid (highest y / northernmost).
        Internally row 0 is bottom (south), so we invert:
            internal_row = (rows - 1) - (display_row - 1) = rows - display_row
        """
        label = label.strip()
        if self._labeling == "alphanumeric":
            m = re.match(r'^([A-Z])(\d+)$', label.upper())
            if not m:
                return None
            col = ord(m.group(1)) - ord('A')
            display_row = int(m.group(2))
            row = self._rows - display_row  # invert: row 1 → top (highest internal row)
        else:
            # Numeric: 1-based, row-major, top-to-bottom, left-to-right
            try:
                n = int(label)
            except ValueError:
                return None
            n -= 1
            display_row_idx = n // self._columns
            col = n % self._columns
            row = self._rows - 1 - display_row_idx

        if 0 <= col < self._columns and 0 <= row < self._rows:
            return (col, row)
        return None

    def _make_top_label(self, col: int, row: int) -> str:
        """(col, row) 0-indexed → top-level label string.

        Row 1 = top (north), increasing downward.
        internal row is 0-based from bottom, so display_row = rows - row.
        """
        if self._labeling == "alphanumeric":
            display_row = self._rows - row
            return f"{chr(ord('A') + col)}{display_row}"
        else:
            display_row_idx = self._rows - 1 - row
            return str(display_row_idx * self._columns + col + 1)

    # ── Local coordinate helpers ───────────────────────

    def _local_square_bounds(self, col: int, row: int) -> tuple[float, float, float, float]:
        """Return (x_min, y_min, x_max, y_max) in local meters for a top-level square."""
        x_min = col * self._square_size
        y_min = row * self._square_size
        return (x_min, y_min, x_min + self._square_size, y_min + self._square_size)

    def _local_to_geo_polygon(self, x_min: float, y_min: float, x_max: float, y_max: float) -> Polygon:
        """Convert a local axis-aligned box to a geographic polygon (with rotation)."""
        corners_local = [
            (x_min, y_min),
            (x_max, y_min),
            (x_max, y_max),
            (x_min, y_max),
            (x_min, y_min),
        ]

        # Apply grid rotation around origin (0,0)
        cos_r = math.cos(self._rot_rad)
        sin_r = math.sin(self._rot_rad)
        rotated = []
        for x, y in corners_local:
            rx = x * cos_r - y * sin_r
            ry = x * sin_r + y * cos_r
            rotated.append((rx, ry))

        # Transform to geographic
        geo_corners = []
        for x, y in rotated:
            lon, lat = self._to_geo.transform(x, y)
            geo_corners.append((lon, lat))

        return Polygon(geo_corners)

    def _geo_to_local(self, lat: float, lon: float) -> tuple[float, float]:
        """Geographic → local (un-rotated) coordinates."""
        x, y = self._to_local.transform(lon, lat)
        # Undo grid rotation
        cos_r = math.cos(-self._rot_rad)
        sin_r = math.sin(-self._rot_rad)
        ux = x * cos_r - y * sin_r
        uy = x * sin_r + y * cos_r
        return (ux, uy)

    # ── Snail sub-square resolution ────────────────────

    def _resolve_snail_in_box(
        self, snail_digits: list[int], x_min: float, y_min: float, size: float
    ) -> tuple[float, float, float, float]:
        """Recursively resolve a list of snail digits within a bounding box."""
        if not snail_digits:
            return (x_min, y_min, x_min + size, y_min + size)

        sub_size = size / self._recursion_base
        digit = snail_digits[0]
        col_off, row_off = SNAIL_TO_OFFSET[digit]
        new_x = x_min + col_off * sub_size
        new_y = y_min + row_off * sub_size
        return self._resolve_snail_in_box(snail_digits[1:], new_x, new_y, sub_size)

    def _parse_snail_path(self, path: str) -> tuple[str, list[int]] | None:
        """Parse 'B4-3-7-9' → ('B4', [3, 7, 9]) or ('12', [3, 7, 9])."""
        parts = path.strip().split("-")
        if not parts:
            return None
        top_label = parts[0]
        snail_digits = []
        for p in parts[1:]:
            try:
                d = int(p)
            except ValueError:
                return None
            if d < 1 or d > 9:
                return None
            snail_digits.append(d)
        return (top_label, snail_digits)

    # ── Public API ─────────────────────────────────────

    def validate_square(self, label: str) -> bool:
        """Check if a top-level square label is valid."""
        return self._parse_top_label(label) is not None

    def validate_snail(self, snail_path: str) -> bool:
        """Check that a snail path is syntactically and geometrically valid."""
        parsed = self._parse_snail_path(snail_path)
        if parsed is None:
            return False
        top_label, digits = parsed
        if self._parse_top_label(top_label) is None:
            return False
        if len(digits) > self._max_depth:
            return False
        return True

    def square_to_polygon(self, label: str) -> Polygon:
        """Top-level square label → geographic polygon."""
        cr = self._parse_top_label(label)
        if cr is None:
            raise ValueError(f"Invalid square label: {label}")
        col, row = cr
        bounds = self._local_square_bounds(col, row)
        return self._local_to_geo_polygon(*bounds)

    def snail_to_polygon(self, snail_path: str) -> Polygon:
        """Snail path (e.g. 'B4-3-7') → geographic polygon."""
        parsed = self._parse_snail_path(snail_path)
        if parsed is None:
            raise ValueError(f"Invalid snail path: {snail_path}")
        top_label, digits = parsed
        cr = self._parse_top_label(top_label)
        if cr is None:
            raise ValueError(f"Invalid top-level label: {top_label}")

        col, row = cr
        x_min = col * self._square_size
        y_min = row * self._square_size
        bounds = self._resolve_snail_in_box(digits, x_min, y_min, self._square_size)
        return self._local_to_geo_polygon(*bounds)

    def snail_to_center(self, snail_path: str) -> Point:
        """Snail path → geographic center point."""
        poly = self.snail_to_polygon(snail_path)
        return poly.centroid

    def point_to_square(self, lat: float, lon: float) -> str | None:
        """Geographic point → top-level square label, or None if outside grid."""
        ux, uy = self._geo_to_local(lat, lon)
        col = int(ux // self._square_size)
        row = int(uy // self._square_size)
        if 0 <= col < self._columns and 0 <= row < self._rows:
            return self._make_top_label(col, row)
        return None

    def is_point_inside_grid(self, lat: float, lon: float) -> bool:
        """Check if a geographic point falls within the grid boundaries."""
        ux, uy = self._geo_to_local(lat, lon)
        col = int(ux // self._square_size)
        row = int(uy // self._square_size)
        return 0 <= col < self._columns and 0 <= row < self._rows

    def point_to_snail(self, lat: float, lon: float, depth: int = 3) -> str | None:
        """Geographic point → snail address at given depth."""
        depth = min(depth, self._max_depth)
        ux, uy = self._geo_to_local(lat, lon)

        col = int(ux // self._square_size)
        row = int(uy // self._square_size)
        if not (0 <= col < self._columns and 0 <= row < self._rows):
            return None

        top_label = self._make_top_label(col, row)
        digits: list[int] = []

        # Local position within the top-level square
        lx = ux - col * self._square_size
        ly = uy - row * self._square_size
        size = self._square_size

        for _ in range(depth):
            sub_size = size / self._recursion_base
            sub_col = min(int(lx / sub_size), self._recursion_base - 1)
            sub_row = min(int(ly / sub_size), self._recursion_base - 1)
            digit = OFFSET_TO_SNAIL.get((sub_col, sub_row))
            if digit is None:
                break
            digits.append(digit)
            lx -= sub_col * sub_size
            ly -= sub_row * sub_size
            size = sub_size

        if digits:
            return top_label + "-" + "-".join(str(d) for d in digits)
        return top_label

    def all_squares(self) -> list[tuple[str, Polygon]]:
        """All top-level squares with their polygons."""
        result = []
        for row in range(self._rows):
            for col in range(self._columns):
                label = self._make_top_label(col, row)
                bounds = self._local_square_bounds(col, row)
                poly = self._local_to_geo_polygon(*bounds)
                result.append((label, poly))
        return result

    def subdivide(self, snail_path: str) -> list[tuple[str, Polygon]]:
        """Return the 9 children of a square / snail path."""
        parsed = self._parse_snail_path(snail_path)
        if parsed is None:
            raise ValueError(f"Invalid snail path: {snail_path}")
        top_label, digits = parsed
        cr = self._parse_top_label(top_label)
        if cr is None:
            raise ValueError(f"Invalid top-level label: {top_label}")

        col, row = cr
        x_min = col * self._square_size
        y_min = row * self._square_size
        parent_bounds = self._resolve_snail_in_box(digits, x_min, y_min, self._square_size)
        parent_size = parent_bounds[2] - parent_bounds[0]
        sub_size = parent_size / self._recursion_base

        children = []
        for snail_num in range(1, 10):
            col_off, row_off = SNAIL_TO_OFFSET[snail_num]
            sx = parent_bounds[0] + col_off * sub_size
            sy = parent_bounds[1] + row_off * sub_size
            poly = self._local_to_geo_polygon(sx, sy, sx + sub_size, sy + sub_size)
            child_path = snail_path + f"-{snail_num}"
            children.append((child_path, poly))
        return children

    def enumerate_cells(self, depth: int) -> list[tuple[str, float, float]]:
        """
        Fast enumeration of all cells at a given depth.
        Returns list of (snail_path, center_lat, center_lon) without building
        full polygons or GeoJSON — much faster than grid_as_geojson for large depths.
        """
        cells: list[tuple[str, float, float]] = []
        cos_r = math.cos(self._rot_rad)
        sin_r = math.sin(self._rot_rad)

        def _recurse(path: str, x_min: float, y_min: float, size: float, cur: int):
            if cur >= depth:
                cx = x_min + size / 2
                cy = y_min + size / 2
                rx = cx * cos_r - cy * sin_r
                ry = cx * sin_r + cy * cos_r
                lon, lat = self._to_geo.transform(rx, ry)
                cells.append((path, lat, lon))
            else:
                sub = size / self._recursion_base
                for sn in range(1, 10):
                    co, ro = SNAIL_TO_OFFSET[sn]
                    _recurse(f"{path}-{sn}", x_min + co * sub, y_min + ro * sub, sub, cur + 1)

        for row in range(self._rows):
            for col in range(self._columns):
                label = self._make_top_label(col, row)
                x0 = col * self._square_size
                y0 = row * self._square_size
                if depth == 0:
                    cx = x0 + self._square_size / 2
                    cy = y0 + self._square_size / 2
                    rx = cx * cos_r - cy * sin_r
                    ry = cx * sin_r + cy * cos_r
                    lon, lat = self._to_geo.transform(rx, ry)
                    cells.append((label, lat, lon))
                else:
                    _recurse(label, x0, y0, self._square_size, 0)
        return cells

    def grid_as_geojson(self, depth: int = 0) -> dict:
        """Full grid as GeoJSON FeatureCollection at given depth."""
        features = []

        if depth == 0:
            for label, poly in self.all_squares():
                center = poly.centroid
                features.append({
                    "type": "Feature",
                    "properties": {"label": label, "depth": 0, "center_lat": center.y, "center_lon": center.x},
                    "geometry": mapping(poly),
                })
        else:
            # Generate at specified depth by recursion
            def _gen(path: str, current_depth: int):
                if current_depth >= depth:
                    poly = self.snail_to_polygon(path)
                    center = poly.centroid
                    features.append({
                        "type": "Feature",
                        "properties": {"label": path, "depth": current_depth, "center_lat": center.y, "center_lon": center.x},
                        "geometry": mapping(poly),
                    })
                else:
                    for child_path, _ in self.subdivide(path):
                        _gen(child_path, current_depth + 1)

            for label, _ in self.all_squares():
                _gen(label, 0)

        return {
            "type": "FeatureCollection",
            "features": features,
        }

    def _squares_in_bounds(self, south: float, west: float, north: float, east: float) -> list[str]:
        """Return top-level square labels whose polygons overlap the given geographic bounds."""
        # Convert all four corners of the viewport to local coords
        corners = [
            self._geo_to_local(south, west),
            self._geo_to_local(south, east),
            self._geo_to_local(north, west),
            self._geo_to_local(north, east),
        ]
        # Find bounding box in local coords
        lx_min = min(c[0] for c in corners)
        lx_max = max(c[0] for c in corners)
        ly_min = min(c[1] for c in corners)
        ly_max = max(c[1] for c in corners)

        # Determine which columns/rows overlap
        col_min = max(0, int(lx_min / self._square_size))
        col_max = min(self._columns - 1, int(lx_max / self._square_size))
        row_min = max(0, int(ly_min / self._square_size))
        row_max = min(self._rows - 1, int(ly_max / self._square_size))

        labels = []
        for row in range(row_min, row_max + 1):
            for col in range(col_min, col_max + 1):
                labels.append(self._make_top_label(col, row))
        return labels

    def grid_viewport_geojson(
        self, south: float, west: float, north: float, east: float, depth: int = 1
    ) -> dict:
        """
        Return sub-squares at the given depth only for top-level squares visible
        in the viewport bounds. Efficient for zoom-adaptive loading.
        """
        depth = min(depth, self._max_depth)
        visible_squares = self._squares_in_bounds(south, west, north, east)
        features = []

        def _gen_sub(path: str, current_depth: int, target_depth: int):
            if current_depth >= target_depth:
                poly = self.snail_to_polygon(path)
                center = poly.centroid
                parts = path.split("-")
                short_label = parts[-1] if len(parts) > 1 else path
                features.append({
                    "type": "Feature",
                    "properties": {
                        "label": path,
                        "short_label": short_label,
                        "depth": current_depth,
                        "center_lat": center.y,
                        "center_lon": center.x,
                    },
                    "geometry": mapping(poly),
                })
            else:
                for child_path, _ in self.subdivide(path):
                    _gen_sub(child_path, current_depth + 1, target_depth)

        for label in visible_squares:
            _gen_sub(label, 0, depth)

        return {
            "type": "FeatureCollection",
            "features": features,
            "meta": {
                "depth": depth,
                "visible_squares": len(visible_squares),
                "total_cells": len(features),
            },
        }

