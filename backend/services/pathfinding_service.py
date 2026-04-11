"""
Pathfinding service — A* search over terrain cell graph.

Finds optimal movement paths that respect terrain costs, slope penalties,
and obstacle avoidance (minefields, water, etc).

Works on depth-1 terrain cells (~333m resolution). Grid cells are nodes;
8-connected neighbors are edges weighted by terrain movement cost.
"""

from __future__ import annotations

import heapq
import math
import logging
from typing import Any

from backend.engine.terrain import TERRAIN_MOVEMENT_FACTOR

logger = logging.getLogger(__name__)

# Approximate meters per degree at mid-latitudes
_M_PER_DEG_LAT = 111_320.0
_M_PER_DEG_LON_48 = 74_000.0

# Terrain types that are effectively impassable for pathfinding
_IMPASSABLE_TERRAIN = {"water"}
_IMPASSABLE_COST = 50.0  # Very high cost (effectively avoided unless no other option)
_MINEFIELD_COST = 100.0  # Even higher — never route through minefields


def _geo_dist(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Fast approximate distance in meters (flat-earth)."""
    dy = (lat2 - lat1) * _M_PER_DEG_LAT
    dx = (lon2 - lon1) * _M_PER_DEG_LON_48
    return math.sqrt(dy * dy + dx * dx)


class PathfindingService:
    """
    A* pathfinder over the terrain cell graph.

    Constructor args:
        terrain_cells:   dict[snail_path → terrain_type]
        elevation_cells: dict[snail_path → {elevation_m, slope_deg, ...}]
        cell_centroids:  dict[snail_path → (lat, lon)]
        grid_service:    GridService instance
        map_objects:     list of MapObject ORM instances (for obstacle avoidance)
        side:            'blue' or 'red' (for discovered-minefield filtering)
    """

    def __init__(
        self,
        terrain_cells: dict[str, str],
        elevation_cells: dict[str, dict] | None,
        cell_centroids: dict[str, tuple[float, float]],
        grid_service: Any,
        map_objects: list | None = None,
        side: str = "blue",
    ):
        self._cells = terrain_cells
        self._elevation = elevation_cells or {}
        self._centroids = cell_centroids
        self._grid = grid_service
        self._side = side

        # Build neighbor map and minefield set
        self._neighbors: dict[str, list[str]] = {}
        self._minefield_cells: set[str] = set()
        self._build_neighbor_map()
        if map_objects:
            self._build_minefield_set(map_objects)

    def _build_neighbor_map(self):
        """
        Build 8-connected neighbor graph from cell centroids.

        Two cells are neighbors if their centroids are within ~1.5× cell spacing.
        This catches direct (4-way) and diagonal (8-way) neighbors.
        """
        if not self._centroids:
            return

        # Estimate cell spacing from first two distinct-column cells
        sorted_paths = sorted(self._centroids.keys())
        if len(sorted_paths) < 2:
            return

        # Sample typical cell spacing: find min distance between any two adjacent cells
        # Use the first cell and find its nearest neighbor
        sample_path = sorted_paths[0]
        sample_lat, sample_lon = self._centroids[sample_path]
        min_dist = float("inf")
        for other_path in sorted_paths[1:50]:  # check first 50 for speed
            ola, olo = self._centroids[other_path]
            d = _geo_dist(sample_lat, sample_lon, ola, olo)
            if 10 < d < min_dist:
                min_dist = d

        if min_dist == float("inf"):
            min_dist = 350  # fallback ~333m cells

        # Neighbor threshold: ~1.5× cell spacing (catches diagonals = sqrt(2) × spacing)
        threshold = min_dist * 1.6

        # Build spatial hash for O(n) neighbor finding
        # Hash cells into buckets by (lat_bucket, lon_bucket)
        bucket_size_lat = threshold / _M_PER_DEG_LAT * 1.2
        bucket_size_lon = threshold / _M_PER_DEG_LON_48 * 1.2
        buckets: dict[tuple[int, int], list[str]] = {}

        for path, (lat, lon) in self._centroids.items():
            bx = int(lat / bucket_size_lat)
            by = int(lon / bucket_size_lon)
            key = (bx, by)
            if key not in buckets:
                buckets[key] = []
            buckets[key].append(path)

        # For each cell, check its bucket and neighboring buckets
        for path, (lat, lon) in self._centroids.items():
            bx = int(lat / bucket_size_lat)
            by = int(lon / bucket_size_lon)
            neighbors = []
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    bucket_key = (bx + dx, by + dy)
                    for other in buckets.get(bucket_key, []):
                        if other == path:
                            continue
                        ola, olo = self._centroids[other]
                        dist = _geo_dist(lat, lon, ola, olo)
                        if dist < threshold:
                            neighbors.append(other)
            # Also check same bucket
            for other in buckets.get((bx, by), []):
                if other == path:
                    continue
                ola, olo = self._centroids[other]
                dist = _geo_dist(lat, lon, ola, olo)
                if dist < threshold and other not in neighbors:
                    neighbors.append(other)

            self._neighbors[path] = neighbors

    def _build_minefield_set(self, map_objects: list):
        """Find which cells overlap with discovered minefields."""
        from shapely.geometry import Point as ShapelyPoint
        from geoalchemy2.shape import to_shape

        for obj in map_objects:
            if not obj.is_active:
                continue
            if obj.object_type != "minefield":
                continue
            # Check discovery — only avoid minefields we know about
            if self._side == "blue" and not getattr(obj, "discovered_by_blue", True):
                continue
            if self._side == "red" and not getattr(obj, "discovered_by_red", True):
                continue

            try:
                mine_shape = to_shape(obj.geometry)
            except Exception:
                continue

            # Buffer slightly for safety margin
            try:
                buffered = mine_shape.buffer(0.0005)  # ~50m buffer
            except Exception:
                buffered = mine_shape

            # Check which cells fall inside
            for path, (lat, lon) in self._centroids.items():
                if buffered.contains(ShapelyPoint(lon, lat)):
                    self._minefield_cells.add(path)

    def _movement_cost(self, to_path: str) -> float:
        """
        Movement cost to enter a cell. Lower = easier to traverse.

        Combines terrain factor and slope penalty.
        Returns value >= 1.0 (inverted from movement factor: fast terrain = low cost).
        """
        # Minefield: extremely high cost
        if to_path in self._minefield_cells:
            return _MINEFIELD_COST

        # Terrain type cost
        terrain_type = self._cells.get(to_path, "open")
        if terrain_type in _IMPASSABLE_TERRAIN:
            return _IMPASSABLE_COST

        terrain_factor = TERRAIN_MOVEMENT_FACTOR.get(terrain_type, 0.8)
        # Invert: road (1.0) → cost 1.0; forest (0.5) → cost 2.0; marsh (0.3) → cost 3.3
        if terrain_factor < 0.01:
            return _IMPASSABLE_COST
        terrain_cost = 1.0 / terrain_factor

        # Slope penalty
        slope_factor = 1.0
        elev_data = self._elevation.get(to_path)
        if elev_data:
            slope_deg = elev_data.get("slope_deg", 0)
            if slope_deg > 0:
                slope_factor = 1.0 / max(0.2, 1.0 - slope_deg / 45.0)

        return terrain_cost * slope_factor

    def find_path(
        self,
        from_lat: float,
        from_lon: float,
        to_lat: float,
        to_lon: float,
        max_iterations: int = 5000,
    ) -> list[tuple[float, float]] | None:
        """
        Find optimal path using A* search.

        Returns list of (lat, lon) waypoints from start to end,
        or None if no path found.
        """
        if not self._centroids or not self._neighbors:
            return None

        # Snap start/end to nearest cells
        start_cell = self._nearest_cell(from_lat, from_lon)
        end_cell = self._nearest_cell(to_lat, to_lon)

        if start_cell is None or end_cell is None:
            return None

        if start_cell == end_cell:
            return [(from_lat, from_lon), (to_lat, to_lon)]

        end_lat, end_lon = self._centroids[end_cell]

        # A* search
        # Priority queue: (f_score, counter, cell_path)
        counter = 0
        open_set: list[tuple[float, int, str]] = []
        heapq.heappush(open_set, (0.0, counter, start_cell))

        came_from: dict[str, str] = {}
        g_score: dict[str, float] = {start_cell: 0.0}

        while open_set:
            max_iterations -= 1
            if max_iterations <= 0:
                logger.warning("Pathfinding: max iterations reached")
                break

            _, _, current = heapq.heappop(open_set)

            if current == end_cell:
                # Reconstruct path
                path_cells = []
                c = current
                while c in came_from:
                    path_cells.append(c)
                    c = came_from[c]
                path_cells.append(start_cell)
                path_cells.reverse()

                # Convert to lat/lon waypoints
                waypoints = [(from_lat, from_lon)]  # exact start
                for cell in path_cells[1:-1]:  # skip first/last (use exact coords)
                    lat, lon = self._centroids[cell]
                    waypoints.append((lat, lon))
                waypoints.append((to_lat, to_lon))  # exact end

                # Simplify path to remove unnecessary waypoints
                return self._simplify_path(waypoints)

            for neighbor in self._neighbors.get(current, []):
                # Actual distance between cell centroids
                cur_lat, cur_lon = self._centroids[current]
                nb_lat, nb_lon = self._centroids[neighbor]
                step_dist = _geo_dist(cur_lat, cur_lon, nb_lat, nb_lon)

                # Cost = distance × terrain movement cost
                move_cost = self._movement_cost(neighbor)
                tentative_g = g_score[current] + step_dist * move_cost

                if tentative_g < g_score.get(neighbor, float("inf")):
                    came_from[neighbor] = current
                    g_score[neighbor] = tentative_g
                    # Heuristic: straight-line distance to goal
                    h = _geo_dist(nb_lat, nb_lon, end_lat, end_lon)
                    f = tentative_g + h
                    counter += 1
                    heapq.heappush(open_set, (f, counter, neighbor))

        # No path found
        logger.debug("Pathfinding: no path found from %s to %s", start_cell, end_cell)
        return None

    def _nearest_cell(self, lat: float, lon: float) -> str | None:
        """Find the nearest cell to a geographic point."""
        # Try grid service first (fast)
        if self._grid:
            # Try multiple depths starting from the cell depth in our data
            for d in (1, 2, 0):
                snail = self._grid.point_to_snail(lat, lon, depth=d)
                if snail and snail in self._centroids:
                    return snail

        # Brute force fallback: find nearest centroid
        best = None
        best_dist = float("inf")
        for path, (clat, clon) in self._centroids.items():
            d = _geo_dist(lat, lon, clat, clon)
            if d < best_dist:
                best_dist = d
                best = path
        return best

    def _simplify_path(
        self,
        waypoints: list[tuple[float, float]],
        tolerance_m: float = 80.0,
    ) -> list[tuple[float, float]]:
        """
        Simplify path using Douglas-Peucker algorithm to remove redundant
        intermediate points while preserving the overall curve shape.
        """
        if len(waypoints) <= 3:
            return waypoints

        try:
            from shapely.geometry import LineString
            # Convert to a format Shapely understands (lon, lat for geometric ops)
            coords = [(lon, lat) for lat, lon in waypoints]
            line = LineString(coords)
            # Tolerance in degrees (~80m at mid-latitudes)
            tol_deg = tolerance_m / _M_PER_DEG_LAT
            simplified = line.simplify(tol_deg, preserve_topology=True)
            # Convert back to (lat, lon)
            result = [(lat, lon) for lon, lat in simplified.coords]
            # Ensure start and end points are preserved exactly
            if len(result) >= 2:
                result[0] = waypoints[0]
                result[-1] = waypoints[-1]
            return result
        except Exception:
            return waypoints

