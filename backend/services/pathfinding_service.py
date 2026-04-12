"""
Pathfinding service — A* search over terrain cell graph.

Finds optimal movement paths that respect terrain costs, slope penalties,
obstacle avoidance (minefields, water), and tactical considerations
(enemy avoidance, cover preference, friendly proximity).

Works on depth-1 terrain cells (~333m resolution). Grid cells are nodes;
8-connected neighbors are edges weighted by terrain movement cost.

Performance architecture:
  - Static data (neighbor map, centroids, base costs) is cached per session
    via build_static_graph() — computed ONCE, reused every tick.
  - Dynamic data (tactical costs from enemy/friendly positions) is cheap to
    rebuild each tick since it only iterates cells × nearby enemies.
  - A* uses a proper closed set for efficient search.
"""

from __future__ import annotations

import heapq
import math
import logging
import threading
import time as _time
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

# Terrain types that provide meaningful cover (protection > 1.0)
_COVER_TERRAIN = {"forest", "urban", "scrub", "orchard", "mountain"}

# Terrain protection factors (for tactical routing)
_TERRAIN_PROTECTION = {
    "road": 1.0, "open": 1.0, "forest": 1.4, "urban": 1.5,
    "water": 0.5, "fields": 1.0, "marsh": 0.8, "desert": 0.8,
    "scrub": 1.2, "bridge": 0.8, "mountain": 1.5, "orchard": 1.2,
}

# Terrain visibility factors (lower = harder to spot)
_TERRAIN_VISIBILITY = {
    "road": 1.0, "open": 1.0, "forest": 0.4, "urban": 0.5,
    "water": 1.0, "fields": 0.9, "marsh": 0.8, "desert": 1.0,
    "scrub": 0.7, "bridge": 1.0, "mountain": 0.6, "orchard": 0.6,
}

# Minimum possible edge cost factor — used to keep heuristic admissible.
# This is the lowest possible _movement_cost value for any traversable cell.
# road(1.0) × no-slope(1.0) × min-tactical(1.0) = 1.0
# But we use 0.95 as safety margin for floating-point edge cases.
_MIN_COST_FACTOR = 0.95


def _geo_dist(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Fast approximate distance in meters (flat-earth)."""
    dy = (lat2 - lat1) * _M_PER_DEG_LAT
    dx = (lon2 - lon1) * _M_PER_DEG_LON_48
    return math.sqrt(dy * dy + dx * dx)


# ── Session-level static graph cache ───────────────────────────
# Neighbor map and centroids don't change between ticks — cache them.

_graph_cache_lock = threading.Lock()
_graph_cache: dict[str, dict] = {}  # session_id → {centroids, neighbors, base_costs, cell_spacing, ts}
_GRAPH_CACHE_TTL = 86400  # 24h (effectively session lifetime)


def get_cached_graph(session_id: str) -> dict | None:
    """Get cached static graph for a session."""
    with _graph_cache_lock:
        entry = _graph_cache.get(session_id)
        if entry and (_time.time() - entry["ts"]) < _GRAPH_CACHE_TTL:
            return entry
    return None


def set_cached_graph(session_id: str, data: dict):
    """Cache static graph data for a session."""
    with _graph_cache_lock:
        data["ts"] = _time.time()
        _graph_cache[session_id] = data


def clear_graph_cache(session_id: str | None = None):
    """Clear cached graph (called when terrain is re-analyzed)."""
    with _graph_cache_lock:
        if session_id:
            _graph_cache.pop(session_id, None)
        else:
            _graph_cache.clear()


def serialize_static_graph(graph: dict) -> dict:
    """Serialize static graph for JSON storage (GridDefinition.settings_json).

    Converts centroid tuples to lists and neighbor tuples to nested lists.
    Result is ~200-400KB for 900 cells — fits comfortably in JSONB.
    """
    centroids_ser = {k: [v[0], v[1]] for k, v in graph.get("centroids", {}).items()}
    neighbors_ser = {
        k: [[nb, round(dist, 1)] for nb, dist in v]
        for k, v in graph.get("neighbors", {}).items()
    }
    return {
        "centroids": centroids_ser,
        "neighbors": neighbors_ser,
        "base_costs": {k: round(v, 4) for k, v in graph.get("base_costs", {}).items()},
        "cell_spacing": graph.get("cell_spacing", 350.0),
    }


def deserialize_static_graph(data: dict) -> dict:
    """Deserialize static graph from JSON (GridDefinition.settings_json).

    Converts centroid lists back to tuples and neighbor lists to tuples.
    """
    centroids = {k: (v[0], v[1]) for k, v in data.get("centroids", {}).items()}
    neighbors = {
        k: [(nb, dist) for nb, dist in v]
        for k, v in data.get("neighbors", {}).items()
    }
    return {
        "centroids": centroids,
        "neighbors": neighbors,
        "base_costs": data.get("base_costs", {}),
        "cell_spacing": data.get("cell_spacing", 350.0),
    }


def load_or_build_static_graph(
    session_id_str: str,
    terrain_cells: dict[str, str],
    elevation_cells: dict[str, dict] | None,
    cell_centroids: dict[str, tuple[float, float]] | None,
    grid_service: Any,
    grid_def_settings_json: dict | None = None,
) -> tuple[dict, dict[str, tuple[float, float]]]:
    """
    Load static graph from cache → DB → build from scratch.

    Returns (static_graph, cell_centroids).
    Populates in-memory caches for future calls.
    """
    # 1. Check in-memory cache
    cached = get_cached_graph(session_id_str)
    if cached and cached.get("centroids"):
        return cached, cached["centroids"]

    # 2. Check DB-persisted graph (GridDefinition.settings_json.pathfinding_graph)
    if grid_def_settings_json and "pathfinding_graph" in grid_def_settings_json:
        try:
            graph = deserialize_static_graph(grid_def_settings_json["pathfinding_graph"])
            if graph.get("centroids") and graph.get("neighbors"):
                set_cached_graph(session_id_str, graph)
                return graph, graph["centroids"]
        except Exception as e:
            logger.warning("Failed to deserialize persisted graph: %s", e)

    # 3. Build centroids if not provided
    if not cell_centroids:
        cell_centroids = {}
        for path in terrain_cells:
            try:
                center = grid_service.snail_to_center(path)
                if center:
                    cell_centroids[path] = (center.y, center.x)
            except Exception:
                pass

    if not cell_centroids:
        return {"centroids": {}, "neighbors": {}, "base_costs": {}, "cell_spacing": 350.0}, {}

    # 4. Build graph from scratch
    graph = build_static_graph(terrain_cells, elevation_cells, cell_centroids, grid_service)
    if graph.get("centroids"):
        set_cached_graph(session_id_str, graph)
    return graph, cell_centroids


def build_static_graph(
    terrain_cells: dict[str, str],
    elevation_cells: dict[str, dict] | None,
    cell_centroids: dict[str, tuple[float, float]],
    grid_service: Any,
) -> dict:
    """
    Build the static (topology + base costs) portion of the pathfinding graph.

    This is expensive (~50-200ms for 900 cells) but only needs to be done ONCE
    per session. Results are cached and reused every tick.

    Returns dict with keys: centroids, neighbors, base_costs, cell_spacing
    """
    neighbors: dict[str, list[tuple[str, float]]] = {}  # path → [(neighbor, distance)]
    base_costs: dict[str, float] = {}  # path → base movement cost (terrain + slope, no tactical)

    if not cell_centroids:
        return {"centroids": {}, "neighbors": {}, "base_costs": {}, "cell_spacing": 350.0}

    # ── Estimate cell spacing ──
    sorted_paths = sorted(cell_centroids.keys())
    cell_spacing = 350.0  # fallback
    if len(sorted_paths) >= 2:
        sample_lat, sample_lon = cell_centroids[sorted_paths[0]]
        min_dist = float("inf")
        for other_path in sorted_paths[1:50]:
            ola, olo = cell_centroids[other_path]
            d = _geo_dist(sample_lat, sample_lon, ola, olo)
            if 10 < d < min_dist:
                min_dist = d
        if min_dist < float("inf"):
            cell_spacing = min_dist

    # ── Build neighbor map with spatial hash ──
    threshold = cell_spacing * 1.6  # catches diagonals
    bucket_size_lat = threshold / _M_PER_DEG_LAT * 1.2
    bucket_size_lon = threshold / _M_PER_DEG_LON_48 * 1.2
    buckets: dict[tuple[int, int], list[str]] = {}

    for path, (lat, lon) in cell_centroids.items():
        bx = int(lat / bucket_size_lat)
        by = int(lon / bucket_size_lon)
        key = (bx, by)
        if key not in buckets:
            buckets[key] = []
        buckets[key].append(path)

    for path, (lat, lon) in cell_centroids.items():
        bx = int(lat / bucket_size_lat)
        by = int(lon / bucket_size_lon)
        path_neighbors = []
        seen = set()
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                bucket_key = (bx + dx, by + dy)
                for other in buckets.get(bucket_key, []):
                    if other == path or other in seen:
                        continue
                    ola, olo = cell_centroids[other]
                    dist = _geo_dist(lat, lon, ola, olo)
                    if dist < threshold:
                        path_neighbors.append((other, dist))
                        seen.add(other)
        neighbors[path] = path_neighbors

    # ── Pre-compute base costs (terrain + slope, no tactical) ──
    elevation = elevation_cells or {}
    for path in cell_centroids:
        terrain_type = terrain_cells.get(path, "open")
        if terrain_type in _IMPASSABLE_TERRAIN:
            base_costs[path] = _IMPASSABLE_COST
            continue

        terrain_factor = TERRAIN_MOVEMENT_FACTOR.get(terrain_type, 0.8)
        if terrain_factor < 0.01:
            base_costs[path] = _IMPASSABLE_COST
            continue
        terrain_cost = 1.0 / terrain_factor

        slope_factor = 1.0
        elev_data = elevation.get(path)
        if elev_data:
            slope_deg = elev_data.get("slope_deg", 0)
            if slope_deg > 0:
                slope_factor = 1.0 / max(0.2, 1.0 - slope_deg / 45.0)

        base_costs[path] = terrain_cost * slope_factor

    return {
        "centroids": cell_centroids,
        "neighbors": neighbors,
        "base_costs": base_costs,
        "cell_spacing": cell_spacing,
    }


class PathfindingService:
    """
    A* pathfinder over the terrain cell graph with tactical awareness.

    Uses pre-built static graph (neighbors, base costs) and adds dynamic
    tactical costs (enemy avoidance, cover preference, friendly proximity)
    which change each tick.

    Constructor args:
        static_graph:      Pre-built graph from build_static_graph() (cached)
        terrain_cells:     dict[snail_path → terrain_type]
        grid_service:      GridService instance (for _nearest_cell)
        side:              'blue' or 'red' (for minefield filtering)
        map_objects:       list of MapObject ORM instances (for minefield avoidance)
        enemy_positions:   list of (lat, lon, detection_range_m) for known enemies
        friendly_positions: list of (lat, lon) for friendly units
        speed_mode:        'fast' or 'slow' — affects tactical routing
    """

    def __init__(
        self,
        terrain_cells: dict[str, str],
        elevation_cells: dict[str, dict] | None = None,
        cell_centroids: dict[str, tuple[float, float]] | None = None,
        grid_service: Any = None,
        map_objects: list | None = None,
        side: str = "blue",
        enemy_positions: list[tuple[float, float, float]] | None = None,
        friendly_positions: list[tuple[float, float]] | None = None,
        speed_mode: str = "fast",
        *,
        static_graph: dict | None = None,
    ):
        self._cells = terrain_cells
        self._grid = grid_service
        self._side = side
        self._speed_mode = speed_mode

        # Use pre-built static graph if provided (fast path)
        if static_graph:
            self._centroids = static_graph["centroids"]
            self._neighbors = static_graph["neighbors"]
            self._base_costs = static_graph["base_costs"]
        else:
            # Legacy path: build everything from scratch (slower)
            self._centroids = cell_centroids or {}
            graph = build_static_graph(
                terrain_cells, elevation_cells or {},
                self._centroids, grid_service,
            )
            self._neighbors = graph["neighbors"]
            self._base_costs = graph["base_costs"]

        # Build minefield overlay (relatively cheap — only checks active mines)
        self._minefield_cells: set[str] = set()
        if map_objects:
            self._build_minefield_set(map_objects)

        # Build tactical cost overlay (cheap — spatial-hash lookup)
        self._tactical_cost: dict[str, float] = {}
        if enemy_positions or friendly_positions:
            self._build_tactical_costs(enemy_positions or [], friendly_positions or [])

    def _build_minefield_set(self, map_objects: list):
        """Find which cells overlap with discovered minefields."""
        from shapely.geometry import Point as ShapelyPoint
        from geoalchemy2.shape import to_shape

        mine_shapes = []
        for obj in map_objects:
            if not obj.is_active:
                continue
            if obj.object_type != "minefield":
                continue
            if self._side == "blue" and not getattr(obj, "discovered_by_blue", True):
                continue
            if self._side == "red" and not getattr(obj, "discovered_by_red", True):
                continue
            try:
                mine_shape = to_shape(obj.geometry)
                try:
                    buffered = mine_shape.buffer(0.0005)
                except Exception:
                    buffered = mine_shape
                mine_shapes.append(buffered)
            except Exception:
                continue

        if not mine_shapes:
            return

        # Use STRtree for efficient spatial lookup if many minefields
        if len(mine_shapes) > 3:
            try:
                from shapely import strtree
                tree = strtree.STRtree(mine_shapes)
                for path, (lat, lon) in self._centroids.items():
                    pt = ShapelyPoint(lon, lat)
                    results = tree.query(pt)
                    for idx in results:
                        if mine_shapes[idx].contains(pt):
                            self._minefield_cells.add(path)
                            break
                return
            except Exception:
                pass

        # Simple fallback
        for path, (lat, lon) in self._centroids.items():
            pt = ShapelyPoint(lon, lat)
            for ms in mine_shapes:
                if ms.contains(pt):
                    self._minefield_cells.add(path)
                    break

    def _build_tactical_costs(
        self,
        enemies: list[tuple[float, float, float]],
        friendlies: list[tuple[float, float]],
    ):
        """
        Pre-compute tactical cost modifier for each cell.

        Tactical costs are ADDITIVE penalties on top of base cost (never below 1.0)
        to keep the A* heuristic admissible. This prevents U-turns from
        the heuristic overestimating.
        """
        if not self._centroids:
            return

        is_slow = self._speed_mode == "slow"

        # Build spatial buckets for enemies (~1km buckets)
        enemy_bucket_size = 0.01
        enemy_buckets: dict[tuple[int, int], list[tuple[float, float, float]]] = {}
        for elat, elon, erange in enemies:
            bx = int(elat / enemy_bucket_size)
            by = int(elon / enemy_bucket_size)
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    k = (bx + dx, by + dy)
                    if k not in enemy_buckets:
                        enemy_buckets[k] = []
                    enemy_buckets[k].append((elat, elon, erange))

        for path, (clat, clon) in self._centroids.items():
            tactical_mod = 1.0
            terrain_type = self._cells.get(path, "open")

            # ── Enemy avoidance (additive penalty, never reduces cost) ──
            ebx = int(clat / enemy_bucket_size)
            eby = int(clon / enemy_bucket_size)
            nearby_enemies = enemy_buckets.get((ebx, eby), [])

            for elat, elon, erange in nearby_enemies:
                dist = _geo_dist(clat, clon, elat, elon)
                if dist < erange:
                    proximity = 1.0 - (dist / erange)
                    terrain_vis = _TERRAIN_VISIBILITY.get(terrain_type, 1.0)
                    if is_slow:
                        penalty = 2.5 * proximity * terrain_vis
                    else:
                        penalty = 1.0 * proximity * terrain_vis
                    tactical_mod += penalty
                elif dist < erange * 1.3 and is_slow:
                    tactical_mod += 0.15

            # ── Cover preference (slow mode) — moderate bonus, never below 1.0 ──
            if is_slow and terrain_type in _COVER_TERRAIN:
                protection = _TERRAIN_PROTECTION.get(terrain_type, 1.0)
                # Cover reduces tactical mod but never below 1.0
                cover_discount = min(0.3, (protection - 1.0) * 0.5)
                tactical_mod = max(1.0, tactical_mod - cover_discount)

            if abs(tactical_mod - 1.0) > 0.01:
                self._tactical_cost[path] = tactical_mod

    def _movement_cost(self, to_path: str) -> float:
        """
        Total movement cost to enter a cell.

        = base_cost (terrain + slope) × tactical_modifier (enemy avoidance etc.)
        Always >= 1.0 for traversable cells (keeps heuristic admissible).
        """
        if to_path in self._minefield_cells:
            return _MINEFIELD_COST

        base = self._base_costs.get(to_path, 1.25)  # default ~open terrain
        tactical = self._tactical_cost.get(to_path, 1.0)
        return base * tactical

    def find_path(
        self,
        from_lat: float,
        from_lon: float,
        to_lat: float,
        to_lon: float,
        max_iterations: int = 50000,
    ) -> list[tuple[float, float]] | None:
        """
        Find optimal path using A* search with proper closed set.

        Returns list of (lat, lon) waypoints from start to end,
        or None if no path found.
        """
        if not self._centroids or not self._neighbors:
            return None

        t0 = _time.monotonic()

        # Snap start/end to nearest cells
        start_cell = self._nearest_cell(from_lat, from_lon)
        end_cell = self._nearest_cell(to_lat, to_lon)

        if start_cell is None or end_cell is None:
            logger.debug("Pathfinding: start=%s end=%s — one is None", start_cell, end_cell)
            return None

        if start_cell == end_cell:
            return [(from_lat, from_lon), (to_lat, to_lon)]

        # Check if end cell has neighbors (not isolated)
        end_neighbors = self._neighbors.get(end_cell, [])
        if not end_neighbors:
            logger.warning(
                "Pathfinding: end cell %s has no neighbors — likely outside grid",
                end_cell,
            )
            return None

        end_lat, end_lon = self._centroids[end_cell]

        # A* search with closed set
        counter = 0
        iterations_used = 0
        open_set: list[tuple[float, int, str]] = []
        heapq.heappush(open_set, (0.0, counter, start_cell))

        came_from: dict[str, str] = {}
        g_score: dict[str, float] = {start_cell: 0.0}
        closed_set: set[str] = set()

        while open_set:
            if max_iterations <= 0:
                elapsed = _time.monotonic() - t0
                logger.warning(
                    "Pathfinding: max iterations (%d used) from %s to %s in %.1fms — "
                    "closed_set=%d, open_set=%d",
                    iterations_used, start_cell, end_cell,
                    elapsed * 1000, len(closed_set), len(open_set),
                )
                break
            max_iterations -= 1
            iterations_used += 1

            _, _, current = heapq.heappop(open_set)

            # Skip if already fully processed (closed set)
            if current in closed_set:
                continue
            closed_set.add(current)

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
                waypoints = [(from_lat, from_lon)]
                for cell in path_cells[1:-1]:
                    lat, lon = self._centroids[cell]
                    waypoints.append((lat, lon))
                waypoints.append((to_lat, to_lon))

                elapsed = _time.monotonic() - t0
                logger.debug(
                    "Pathfinding: found path %s→%s, %d cells, %d iterations, %.1fms",
                    start_cell, end_cell, len(path_cells),
                    iterations_used, elapsed * 1000,
                )

                return self._simplify_path(waypoints)

            for neighbor, step_dist in self._neighbors.get(current, []):
                if neighbor in closed_set:
                    continue

                move_cost = self._movement_cost(neighbor)
                tentative_g = g_score[current] + step_dist * move_cost

                if tentative_g < g_score.get(neighbor, float("inf")):
                    came_from[neighbor] = current
                    g_score[neighbor] = tentative_g
                    nb_lat, nb_lon = self._centroids[neighbor]
                    # Admissible heuristic: straight-line distance × minimum cost
                    h = _geo_dist(nb_lat, nb_lon, end_lat, end_lon) * _MIN_COST_FACTOR
                    f = tentative_g + h
                    counter += 1
                    heapq.heappush(open_set, (f, counter, neighbor))

        elapsed = _time.monotonic() - t0
        logger.warning(
            "Pathfinding: no path %s→%s, %d iterations, closed=%d, %.1fms",
            start_cell, end_cell, iterations_used, len(closed_set),
            elapsed * 1000,
        )
        return None

    def _nearest_cell(self, lat: float, lon: float) -> str | None:
        """Find the nearest cell to a geographic point."""
        if self._grid:
            for d in (1, 2, 0):
                try:
                    snail = self._grid.point_to_snail(lat, lon, depth=d)
                    if snail and snail in self._centroids:
                        return snail
                except Exception:
                    continue

        # Brute force fallback
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
        tolerance_m: float = 50.0,
    ) -> list[tuple[float, float]]:
        """
        Aggressive multi-pass path cleanup pipeline.

        Guarantees: no backward arrows, no U-shaped detours, no loops.
        Pipeline order matters — each step builds on the previous.
        """
        if len(waypoints) <= 3:
            return waypoints

        # Step 1: Remove initial backward/sideways waypoints FIRST
        cleaned = self._remove_initial_backward(waypoints)

        # Step 2: Douglas-Peucker to remove noise
        cleaned = self._douglas_peucker(cleaned, tolerance_m)

        # Step 3: Remove loops (path coming back near a previous position)
        cleaned = self._remove_loops(cleaned, proximity_m=700.0)

        # Step 4: Remove U-turns and detours
        cleaned = self._remove_uturn_waypoints(cleaned, angle_threshold_deg=90.0)

        # Step 5: Enforce monotonic progress toward destination
        cleaned = self._enforce_monotonic_progress(cleaned)

        # Step 6: Final light DP pass
        if len(cleaned) > 4:
            cleaned = self._douglas_peucker(cleaned, tolerance_m * 0.6)

        # Step 7: Final first-waypoint direction sanitization
        # After ALL other passes, ensure the first intermediate waypoint
        # points generally toward the destination (fixes backward arrows).
        cleaned = self._sanitize_first_waypoint_direction(cleaned)

        # Ensure start/end points are preserved exactly
        if len(cleaned) >= 2:
            cleaned[0] = waypoints[0]
            cleaned[-1] = waypoints[-1]

        return cleaned

    @staticmethod
    def _douglas_peucker(
        waypoints: list[tuple[float, float]],
        tolerance_m: float,
    ) -> list[tuple[float, float]]:
        """Douglas-Peucker simplification via Shapely."""
        if len(waypoints) <= 3:
            return list(waypoints)
        try:
            from shapely.geometry import LineString
            coords = [(lon, lat) for lat, lon in waypoints]
            line = LineString(coords)
            tol_deg = tolerance_m / _M_PER_DEG_LAT
            simplified = line.simplify(tol_deg, preserve_topology=True)
            result = [(lat, lon) for lon, lat in simplified.coords]
            if len(result) >= 2:
                result[0] = waypoints[0]
                result[-1] = waypoints[-1]
            return result
        except Exception:
            return list(waypoints)

    @staticmethod
    def _remove_loops(
        waypoints: list[tuple[float, float]],
        proximity_m: float = 700.0,
        min_loop_len: int = 2,
    ) -> list[tuple[float, float]]:
        """
        Detect and shortcut path loops.

        If point i and point j (j > i+min_loop_len) are within proximity_m,
        remove all intermediate points — the path went out and came back.
        """
        if len(waypoints) <= min_loop_len + 2:
            return waypoints

        result = list(waypoints)
        changed = True
        max_passes = 5

        while changed and max_passes > 0:
            changed = False
            max_passes -= 1
            i = 0
            while i < len(result) - min_loop_len - 1:
                lat_i, lon_i = result[i]
                j = i + min_loop_len + 1
                while j < len(result):
                    lat_j, lon_j = result[j]
                    dist = _geo_dist(lat_i, lon_i, lat_j, lon_j)
                    if dist < proximity_m:
                        result = result[:i + 1] + result[j:]
                        changed = True
                        break
                    j += 1
                if changed:
                    break
                i += 1

        return result

    @staticmethod
    def _remove_initial_backward(
        waypoints: list[tuple[float, float]],
    ) -> list[tuple[float, float]]:
        """
        Remove initial waypoints that don't make forward progress toward
        the destination. Very aggressive — removes anything > ~70° off the
        destination direction.
        """
        if len(waypoints) <= 3:
            return waypoints

        start_lat, start_lon = waypoints[0]
        end_lat, end_lon = waypoints[-1]

        dx_overall = (end_lon - start_lon) * _M_PER_DEG_LON_48
        dy_overall = (end_lat - start_lat) * _M_PER_DEG_LAT
        len_overall = math.sqrt(dx_overall * dx_overall + dy_overall * dy_overall)

        if len_overall < 50:
            return waypoints

        trim_count = 0
        for i in range(1, min(len(waypoints) - 1, 6)):
            wp_lat, wp_lon = waypoints[i]
            dx_wp = (wp_lon - start_lon) * _M_PER_DEG_LON_48
            dy_wp = (wp_lat - start_lat) * _M_PER_DEG_LAT
            len_wp = math.sqrt(dx_wp * dx_wp + dy_wp * dy_wp)

            if len_wp < 30:
                trim_count = i
                continue

            cos_angle = (dx_wp * dx_overall + dy_wp * dy_overall) / (len_wp * len_overall)
            if cos_angle < 0.35:  # > ~70° from destination direction
                trim_count = i
            else:
                break

        if trim_count > 0:
            return [waypoints[0]] + waypoints[trim_count + 1:]

        return waypoints

    @staticmethod
    def _sanitize_first_waypoint_direction(
        waypoints: list[tuple[float, float]],
    ) -> list[tuple[float, float]]:
        """
        Final check: if the first intermediate waypoint (index 1) points
        backward or strongly sideways relative to the overall direction,
        remove it. This is the last safety net to prevent backward arrows.

        Runs AFTER all other simplification passes.
        """
        if len(waypoints) <= 3:
            return waypoints

        start_lat, start_lon = waypoints[0]
        end_lat, end_lon = waypoints[-1]

        dx_overall = (end_lon - start_lon) * _M_PER_DEG_LON_48
        dy_overall = (end_lat - start_lat) * _M_PER_DEG_LAT
        len_overall = math.sqrt(dx_overall * dx_overall + dy_overall * dy_overall)
        if len_overall < 50:
            return waypoints

        # Check first waypoint direction
        wp1_lat, wp1_lon = waypoints[1]
        dx1 = (wp1_lon - start_lon) * _M_PER_DEG_LON_48
        dy1 = (wp1_lat - start_lat) * _M_PER_DEG_LAT
        len1 = math.sqrt(dx1 * dx1 + dy1 * dy1)
        if len1 < 10:
            # Very close — remove it
            return [waypoints[0]] + waypoints[2:]

        cos1 = (dx1 * dx_overall + dy1 * dy_overall) / (len1 * len_overall)
        if cos1 < 0.25:  # > ~75° off direction — remove
            return [waypoints[0]] + waypoints[2:]

        return waypoints

    @staticmethod
    def _remove_uturn_waypoints(
        waypoints: list[tuple[float, float]],
        angle_threshold_deg: float = 90.0,
    ) -> list[tuple[float, float]]:
        """
        Remove waypoints causing direction reversals or unnecessary detours.

        Three strategies:
        1. Angle check: A→B→C turn angle > threshold → remove B.
        2. Backward progress: B farther from destination than both A and C → remove B.
        3. Detour ratio: path A→B→C is > 1.5× the straight-line A→C → remove B.

        Multi-pass until stable.
        """
        if len(waypoints) <= 3:
            return waypoints

        dest_lat, dest_lon = waypoints[-1]

        result = list(waypoints)
        changed = True
        max_passes = 6

        while changed and max_passes > 0 and len(result) > 3:
            changed = False
            max_passes -= 1
            new_result = [result[0]]
            i = 1
            while i < len(result) - 1:
                ax, ay = result[i - 1]
                bx, by = result[i]
                cx, cy = result[i + 1]

                remove = False

                d1y = (bx - ax) * _M_PER_DEG_LAT
                d1x = (by - ay) * _M_PER_DEG_LON_48
                d2y = (cx - bx) * _M_PER_DEG_LAT
                d2x = (cy - by) * _M_PER_DEG_LON_48

                len1 = math.sqrt(d1x * d1x + d1y * d1y)
                len2 = math.sqrt(d2x * d2x + d2y * d2y)

                if len1 < 1 or len2 < 1:
                    remove = True
                else:
                    cos_angle = (d1x * d2x + d1y * d2y) / (len1 * len2)
                    cos_angle = max(-1.0, min(1.0, cos_angle))
                    angle_deg = math.degrees(math.acos(cos_angle))
                    # Strategy 1: Sharp angle
                    if angle_deg > angle_threshold_deg:
                        remove = True

                # Strategy 2: Backward progress
                if not remove:
                    dist_a = _geo_dist(ax, ay, dest_lat, dest_lon)
                    dist_b = _geo_dist(bx, by, dest_lat, dest_lon)
                    dist_c = _geo_dist(cx, cy, dest_lat, dest_lon)
                    if dist_b > dist_a * 1.01 and dist_b > dist_c * 1.01:
                        shortcut_dist = _geo_dist(ax, ay, cx, cy)
                        if shortcut_dist < (len1 + len2) * 0.95:
                            remove = True

                # Strategy 3: Detour ratio — the path through B is much
                # longer than the straight shortcut A→C, meaning B is an
                # unnecessary sideways detour (U-shape).
                if not remove and len1 > 100 and len2 > 100:
                    shortcut = _geo_dist(ax, ay, cx, cy)
                    detour = len1 + len2
                    if shortcut > 10 and detour > shortcut * 1.5:
                        remove = True

                if remove:
                    changed = True
                    i += 1
                    continue

                new_result.append(result[i])
                i += 1
            new_result.append(result[-1])
            result = new_result

        return result

    @staticmethod
    def _enforce_monotonic_progress(
        waypoints: list[tuple[float, float]],
        regression_tolerance: float = 1.04,
    ) -> list[tuple[float, float]]:
        """
        Remove waypoints that move AWAY from the destination compared to
        the previous waypoint.

        Tight 4% tolerance: allows very minor tactical diversions but
        removes any clear regression (backward/sideways detour).
        """
        if len(waypoints) <= 3:
            return waypoints

        dest_lat, dest_lon = waypoints[-1]

        result = [waypoints[0]]
        prev_dist = _geo_dist(waypoints[0][0], waypoints[0][1], dest_lat, dest_lon)

        for i in range(1, len(waypoints) - 1):
            wp_lat, wp_lon = waypoints[i]
            d = _geo_dist(wp_lat, wp_lon, dest_lat, dest_lon)
            if d <= prev_dist * regression_tolerance:
                result.append(waypoints[i])
                prev_dist = d

        result.append(waypoints[-1])
        return result

