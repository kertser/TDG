"""
Line-of-Sight (LOS) Service — terrain-aware viewshed computation.

Replaces simple circular detection ranges with visibility polygons that
account for elevation blocking and terrain feature occlusion.

Algorithm:
  1. Cast N rays (default 72, every 5°) from observer position outward
  2. Step along each ray at intervals matching terrain cell resolution
  3. At each step check:
     a. LOS line elevation from observer eye-height to sample point
     b. Ground elevation at sample point (from ElevationCells)
     c. Terrain visibility penalty (forest/urban absorb visibility)
  4. Ray terminates at max range or when blocked
  5. Return polygon formed by ray endpoints
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.engine.terrain import TerrainService

# Approximate meters per degree (mid-latitudes ~48°N)
METERS_PER_DEG_LAT = 111_320.0
METERS_PER_DEG_LON_AT_48 = 74_000.0

# Default observer eye height above ground (meters)
DEFAULT_EYE_HEIGHT = 2.0

# Terrain types that are "tall" and block LOS even on flat ground.
# These act as vertical obstacles: if a ray passes through them at ground level
# and the observer can't see over them, the ray is blocked.
TERRAIN_OBSTACLE_HEIGHT = {
    "forest":   12.0,   # trees ~12m tall
    "urban":    10.0,   # buildings ~10m
    "orchard":   5.0,   # fruit trees ~5m
    "scrub":     2.0,   # low bushes ~2m (only blocks at same elevation)
}

# Minimum visibility factor to continue ray (below this = fully blocked)
MIN_VISIBILITY_BUDGET = 0.05


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Fast flat-earth distance in meters (consistent with detection.py)."""
    dlat = (lat2 - lat1) * METERS_PER_DEG_LAT
    dlon = (lon2 - lon1) * METERS_PER_DEG_LON_AT_48
    return math.sqrt(dlat * dlat + dlon * dlon)


def _offset_point(lat: float, lon: float, bearing_rad: float, dist_m: float):
    """Move from (lat,lon) by dist_m meters along bearing (radians, 0=N, CW)."""
    dlat = dist_m * math.cos(bearing_rad) / METERS_PER_DEG_LAT
    dlon = dist_m * math.sin(bearing_rad) / METERS_PER_DEG_LON_AT_48
    return lat + dlat, lon + dlon


class LOSService:
    """
    Computes viewshed polygons and point-to-point LOS checks
    using terrain elevation and feature data.
    """

    def __init__(self, terrain: TerrainService):
        self._terrain = terrain

    def compute_viewshed(
        self,
        observer_lon: float,
        observer_lat: float,
        max_range_m: float = 2000.0,
        eye_height: float = DEFAULT_EYE_HEIGHT,
        num_rays: int = 72,
        step_m: float | None = None,
    ) -> list[tuple[float, float]]:
        """
        Compute a viewshed polygon from the observer's position.

        Returns a list of (lon, lat) tuples forming the polygon boundary.
        The polygon represents all areas visible from the observer position.

        If no elevation data AND no terrain cells exist, returns a simple circle.
        If terrain cells exist (even without elevation), terrain obstacles still
        block visibility (forest=12m, urban=10m, etc.) on flat ground.
        """
        # Check if we have any terrain data that can affect LOS
        has_elevation = (
            self._terrain._elevation is not None
            and len(self._terrain._elevation) > 0
        )
        has_terrain_cells = (
            self._terrain._cells is not None
            and len(self._terrain._cells) > 0
        )

        # Auto-detect step size from terrain cell resolution
        if step_m is None:
            if (has_elevation or has_terrain_cells) and self._terrain._grid:
                # Use ~1/3 of cell side for reasonable resolution
                cell_size = self._terrain._grid._square_size
                depth = self._terrain._cell_depth
                rec_base = self._terrain._grid._recursion_base
                cell_side = cell_size / (rec_base ** depth) if depth > 0 else cell_size
                step_m = max(cell_side / 2.0, 25.0)  # at least 25m steps
                step_m = min(step_m, 200.0)  # at most 200m steps
            else:
                step_m = 100.0  # default for no-data case

        # Ensure step_m is a concrete float from here on
        resolved_step: float = step_m

        # No terrain data at all → return simple circle
        if not has_elevation and not has_terrain_cells:
            return self._make_circle(observer_lon, observer_lat, max_range_m, num_rays)

        observer_elev = self._terrain.get_elevation_at(observer_lon, observer_lat)
        observer_eye = observer_elev + eye_height

        # Cast rays in all directions
        polygon_points: list[tuple[float, float]] = []
        angle_step = 2.0 * math.pi / num_rays

        for i in range(num_rays):
            bearing = i * angle_step  # radians, 0=N, CW

            endpoint = self._cast_ray(
                observer_lat, observer_lon,
                observer_eye, observer_elev,
                bearing, max_range_m, resolved_step,
            )
            polygon_points.append(endpoint)

        # Close the polygon
        if polygon_points and polygon_points[0] != polygon_points[-1]:
            polygon_points.append(polygon_points[0])

        return polygon_points

    def _cast_ray(
        self,
        obs_lat: float,
        obs_lon: float,
        obs_eye_elev: float,
        obs_ground_elev: float,
        bearing_rad: float,
        max_range_m: float,
        step_m: float,
    ) -> tuple[float, float]:
        """
        Cast a single ray from observer position along bearing.
        Returns (lon, lat) of the farthest visible point along the ray.

        Uses the "maximum elevation angle" algorithm:
        Track the max elevation angle seen so far along the ray.
        - If a new sample point's elevation angle EXCEEDS the max, the point
          is VISIBLE (we can see it over/past everything closer). Update max.
        - If the angle is BELOW the max, the point is HIDDEN behind closer
          terrain — a ridge or obstacle is blocking the view.

        Terrain features (forest, urban) absorb visibility progressively —
        both when visible (looking at the canopy) and when hidden behind the
        near edge (the obstacle mass is still physically blocking). When the
        visibility budget is exhausted, the ray terminates.

        The ray does NOT terminate early after hidden steps (valleys).
        If terrain rises later (e.g. a ridge beyond a valley), it IS visible
        if its elevation angle exceeds the running max.
        """
        max_elev_angle = -math.pi / 2  # start looking straight down
        visibility_budget = 1.0  # starts at 100%, terrain absorbs it
        farthest_visible_lon, farthest_visible_lat = obs_lon, obs_lat
        last_step_visible = False

        num_steps = int(max_range_m / step_m)
        if num_steps < 1:
            num_steps = 1

        for s in range(1, num_steps + 1):
            dist = s * step_m
            if dist > max_range_m:
                dist = max_range_m

            sample_lat, sample_lon = _offset_point(obs_lat, obs_lon, bearing_rad, dist)

            # Get ground elevation at sample point
            ground_elev = self._terrain.get_elevation_at(sample_lon, sample_lat)

            # Compute elevation angle from observer to this point's ground level
            elev_diff = ground_elev - obs_eye_elev
            elev_angle = math.atan2(elev_diff, dist)

            # Check if terrain feature adds obstacle height
            terrain_type = self._terrain.get_terrain_at(sample_lon, sample_lat)
            obstacle_h = TERRAIN_OBSTACLE_HEIGHT.get(terrain_type, 0.0)

            if obstacle_h > 0:
                # The obstacle top is at ground_elev + obstacle_h
                obstacle_top = ground_elev + obstacle_h
                obstacle_angle = math.atan2(obstacle_top - obs_eye_elev, dist)
            else:
                obstacle_angle = elev_angle

            # The effective angle is the max of ground and obstacle top
            effective_angle = max(elev_angle, obstacle_angle)

            if effective_angle > max_elev_angle:
                # This point is VISIBLE — its terrain/obstacles rise above
                # the shadow line cast by everything closer
                max_elev_angle = effective_angle
                farthest_visible_lon = sample_lon
                farthest_visible_lat = sample_lat
                last_step_visible = True
            else:
                last_step_visible = False

            # Apply terrain visibility absorption for obstacle terrain,
            # regardless of whether the point is "visible" in the max-angle
            # sense. The physical mass of forest/urban blocks visibility
            # even when the max-angle algorithm considers the point hidden
            # behind the near edge — the obstacle is still there.
            if obstacle_h > 0:
                vis_factor = self._terrain.visibility_factor(sample_lon, sample_lat)
                if vis_factor < 1.0:
                    absorption = (1.0 - vis_factor) * (step_m / 100.0)
                    visibility_budget -= absorption
                    if visibility_budget < MIN_VISIBILITY_BUDGET:
                        # Fully obscured by accumulated terrain features
                        return (farthest_visible_lon, farthest_visible_lat)

            if dist >= max_range_m:
                break

        # Ray reached max range — return based on final visibility state.
        if last_step_visible and visibility_budget >= MIN_VISIBILITY_BUDGET:
            # Last sample was visible and budget remains → extend to max range
            end_lat, end_lon = _offset_point(obs_lat, obs_lon, bearing_rad, max_range_m)
            return (end_lon, end_lat)
        return (farthest_visible_lon, farthest_visible_lat)

    def has_los(
        self,
        from_lon: float,
        from_lat: float,
        to_lon: float,
        to_lat: float,
        eye_height: float = DEFAULT_EYE_HEIGHT,
        step_m: float | None = None,
    ) -> bool:
        """
        Check if there is line-of-sight from one point to another.

        Returns True if LOS exists, False if blocked by terrain.
        Used by the detection engine for individual unit-to-unit checks.

        Checks both geometric line-of-sight (terrain/obstacle height blocking)
        and accumulated visibility absorption (dense forest/urban gradually
        consume a visibility budget, consistent with the viewshed algorithm).
        """
        # No elevation data → check if terrain cells can still block LOS
        has_elevation = (
            self._terrain._elevation is not None
            and len(self._terrain._elevation) > 0
        )
        has_terrain_cells = (
            self._terrain._cells is not None
            and len(self._terrain._cells) > 0
        )
        if not has_elevation and not has_terrain_cells:
            return True  # no data at all → assume LOS exists

        dist = _distance_m(from_lat, from_lon, to_lat, to_lon)
        if dist < 1.0:
            return True  # same point

        # Auto-detect step size
        if step_m is None:
            if self._terrain._grid:
                cell_size = self._terrain._grid._square_size
                depth = self._terrain._cell_depth
                rec_base = self._terrain._grid._recursion_base
                cell_side = cell_size / (rec_base ** depth) if depth > 0 else cell_size
                step_m = max(cell_side, 50.0)   # full cell stride for LOS checks
                step_m = min(step_m, 300.0)
            else:
                step_m = 150.0

        resolved_step: float = step_m

        # Observer and target elevations
        from_elev = self._terrain.get_elevation_at(from_lon, from_lat)
        to_elev = self._terrain.get_elevation_at(to_lon, to_lat)
        from_eye = from_elev + eye_height
        to_eye = to_elev + eye_height  # target also has some height

        num_steps = max(2, int(dist / resolved_step))

        # Visibility absorption budget — consistent with viewshed _cast_ray.
        # Dense terrain (forest, urban) gradually absorbs visibility even when
        # the geometric LOS line clears the obstacle tops (e.g. elevated observer).
        visibility_budget = 1.0

        # Check intermediate points along the line
        for s in range(1, num_steps):
            t = s / num_steps  # interpolation factor 0..1

            sample_lat = from_lat + t * (to_lat - from_lat)
            sample_lon = from_lon + t * (to_lon - from_lon)

            # LOS line elevation at this distance
            # Linear interpolation between observer eye and target eye
            los_elev = from_eye + t * (to_eye - from_eye)

            # Ground elevation at sample
            ground_elev = self._terrain.get_elevation_at(sample_lon, sample_lat)

            # Add terrain obstacle height
            terrain_type = self._terrain.get_terrain_at(sample_lon, sample_lat)
            obstacle_h = TERRAIN_OBSTACLE_HEIGHT.get(terrain_type, 0.0)
            effective_ground = ground_elev + obstacle_h

            # If the effective ground + obstacle is above the LOS line, blocked
            if effective_ground > los_elev:
                return False

            # Visibility absorption: even when geometric LOS clears the obstacles,
            # dense terrain gradually absorbs visibility (can't see clearly through
            # hundreds of meters of forest canopy from an elevated position).
            # Only apply absorption at intermediate points (not near the target
            # itself — the target IS at eye_height and should be visible if the
            # line reaches them).
            if t < 0.9:  # skip absorption near the target
                vis_factor = self._terrain.visibility_factor(sample_lon, sample_lat)
                if vis_factor < 1.0:
                    absorption = (1.0 - vis_factor) * (resolved_step / 100.0)
                    visibility_budget -= absorption
                    if visibility_budget < MIN_VISIBILITY_BUDGET:
                        return False  # accumulated vegetation absorption blocks LOS

        return True

    def _make_circle(
        self,
        center_lon: float,
        center_lat: float,
        radius_m: float,
        num_points: int,
    ) -> list[tuple[float, float]]:
        """Generate a simple circle polygon as fallback when no elevation data."""
        points: list[tuple[float, float]] = []
        angle_step = 2.0 * math.pi / num_points
        for i in range(num_points):
            bearing = i * angle_step
            lat, lon = _offset_point(center_lat, center_lon, bearing, radius_m)
            points.append((lon, lat))
        # Close
        points.append(points[0])
        return points

    def compute_viewshed_geojson(
        self,
        observer_lon: float,
        observer_lat: float,
        max_range_m: float = 2000.0,
        eye_height: float = DEFAULT_EYE_HEIGHT,
        num_rays: int = 72,
        step_m: float | None = None,
    ) -> dict:
        """
        Compute viewshed and return as GeoJSON Feature.
        """
        polygon_points = self.compute_viewshed(
            observer_lon, observer_lat, max_range_m,
            eye_height, num_rays, step_m,
        )

        # GeoJSON Polygon coordinates: [exterior_ring]
        # Each point is [lon, lat]
        coordinates = [[list(p) for p in polygon_points]]

        return {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": coordinates,
            },
            "properties": {
                "observer_lon": observer_lon,
                "observer_lat": observer_lat,
                "max_range_m": max_range_m,
                "num_rays": num_rays,
            },
        }





