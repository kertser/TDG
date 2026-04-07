"""Tests for LOSService — viewshed computation and LOS checks."""

from __future__ import annotations

import math
import pytest

from backend.engine.terrain import TerrainService
from backend.services.los_service import LOSService


# ── Helpers / Mocks ──────────────────────────────────────


class MockGrid:
    """Mock grid service that maps coordinates to a 3x3 snail cell grid."""
    _square_size = 1000.0
    _recursion_base = 3
    _max_depth = 3

    def __init__(self, center_lat=49.0, center_lon=4.5):
        self._center_lat = center_lat
        self._center_lon = center_lon

    def point_to_snail(self, lat, lon, depth=1):
        cell_size_lat = 0.003
        cell_size_lon = 0.0045
        origin_lat = self._center_lat - 1.5 * cell_size_lat
        origin_lon = self._center_lon - 1.5 * cell_size_lon
        col = int((lon - origin_lon) / cell_size_lon)
        row = int((lat - origin_lat) / cell_size_lat)
        col = max(0, min(2, col))
        row = max(0, min(2, row))
        offset_to_snail = {
            (0, 2): 1, (1, 2): 2, (2, 2): 3,
            (2, 1): 4, (2, 0): 5, (1, 0): 6,
            (0, 0): 7, (0, 1): 8, (1, 1): 9,
        }
        snail = offset_to_snail.get((col, row), 9)
        return f"A1-{snail}"


def _make_terrain(
    terrain_cells: dict[str, str] | None = None,
    elevation_cells: dict[str, dict] | None = None,
    grid: MockGrid | None = None,
) -> TerrainService:
    return TerrainService(
        terrain_cells=terrain_cells,
        elevation_cells=elevation_cells,
        grid_service=grid,
    )


def _flat_terrain():
    """All open, flat at 100m elevation."""
    grid = MockGrid()
    tc = {f"A1-{i}": "open" for i in range(1, 10)}
    ec = {f"A1-{i}": {"elevation_m": 100.0, "slope_deg": 0.0, "aspect_deg": None} for i in range(1, 10)}
    return _make_terrain(tc, ec, grid)


def _ridge_east_terrain():
    """Flat 100m with a 200m ridge on the east side (A1-3, A1-4, A1-5)."""
    grid = MockGrid()
    tc = {f"A1-{i}": "open" for i in range(1, 10)}
    ec = {f"A1-{i}": {"elevation_m": 100.0, "slope_deg": 0.0, "aspect_deg": None} for i in range(1, 10)}
    for path in ["A1-3", "A1-4", "A1-5"]:
        ec[path] = {"elevation_m": 200.0, "slope_deg": 15.0, "aspect_deg": None}
    return _make_terrain(tc, ec, grid)


def _forest_north_terrain():
    """Flat 100m with dense forest to the north (A1-1, A1-2)."""
    grid = MockGrid()
    tc = {f"A1-{i}": "open" for i in range(1, 10)}
    ec = {f"A1-{i}": {"elevation_m": 100.0, "slope_deg": 0.0, "aspect_deg": None} for i in range(1, 10)}
    tc["A1-1"] = "forest"
    tc["A1-2"] = "forest"
    return _make_terrain(tc, ec, grid)


# ── Tests: Viewshed ─────────────────────────────────────


def test_viewshed_circle_fallback():
    """With no elevation data, viewshed should produce a circle."""
    ts = TerrainService()  # no data
    los = LOSService(ts)
    pts = los.compute_viewshed(4.5, 49.0, 1000.0, num_rays=24)
    assert len(pts) == 25  # 24 + closing point

    # All points should be equidistant from center
    dists = []
    for lon, lat in pts[:-1]:
        dlat = (lat - 49.0) * 111320
        dlon = (lon - 4.5) * 74000
        dists.append(math.sqrt(dlat * dlat + dlon * dlon))

    for d in dists:
        assert abs(d - 1000.0) < 5.0  # within 5m of expected


def test_viewshed_flat_terrain():
    """On flat terrain, viewshed should be approximately circular."""
    ts = _flat_terrain()
    los = LOSService(ts)
    pts = los.compute_viewshed(4.5, 49.0, 1000.0, num_rays=36, step_m=50.0)

    dists = []
    for lon, lat in pts[:-1]:
        dlat = (lat - 49.0) * 111320
        dlon = (lon - 4.5) * 74000
        dists.append(math.sqrt(dlat * dlat + dlon * dlon))

    # On flat open terrain, all rays should reach max range
    for d in dists:
        assert d >= 900.0, f"Expected ~1000m on flat terrain, got {d:.0f}m"


def test_viewshed_ridge_blocks():
    """A ridge should block the viewshed in that direction."""
    ts = _ridge_east_terrain()
    los = LOSService(ts)
    pts = los.compute_viewshed(4.5, 49.0, 2000.0, num_rays=36, step_m=50.0)

    dists = []
    for lon, lat in pts[:-1]:
        dlat = (lat - 49.0) * 111320
        dlon = (lon - 4.5) * 74000
        dists.append(math.sqrt(dlat * dlat + dlon * dlon))

    # Should have variation: some rays blocked, others not
    assert max(dists) - min(dists) > 500, "Expected significant viewshed variation from ridge"


def test_viewshed_forest_reduces():
    """Dense forest should reduce viewshed range via visibility absorption."""
    ts = _forest_north_terrain()
    los = LOSService(ts)
    pts = los.compute_viewshed(4.5, 49.0, 2000.0, num_rays=36, step_m=50.0)

    dists = []
    for lon, lat in pts[:-1]:
        dlat = (lat - 49.0) * 111320
        dlon = (lon - 4.5) * 74000
        dists.append(math.sqrt(dlat * dlat + dlon * dlon))

    # North rays (indices ~0, 1, 35) should be shorter than south (index ~18)
    north_dist = dists[0]
    south_dist = dists[18]
    assert north_dist < south_dist, "Forest should reduce north viewshed"


# ── Tests: Line-of-Sight ────────────────────────────────


def test_has_los_no_data():
    """Without elevation data, LOS always returns True (backwards compat)."""
    ts = TerrainService()
    los = LOSService(ts)
    assert los.has_los(4.5, 49.0, 4.6, 49.1) is True


def test_has_los_flat_terrain():
    """On flat terrain, LOS should always be clear."""
    ts = _flat_terrain()
    los = LOSService(ts)
    assert los.has_los(4.5, 49.0, 4.51, 49.0, step_m=50.0) is True
    assert los.has_los(4.5, 49.0, 4.49, 49.0, step_m=50.0) is True


def test_has_los_blocked_by_ridge():
    """LOS through a ridge should be blocked."""
    ts = _ridge_east_terrain()
    los = LOSService(ts)
    # East direction goes through ridge
    assert los.has_los(4.5, 49.0, 4.515, 49.0, step_m=50.0) is False
    # West direction is clear
    assert los.has_los(4.5, 49.0, 4.485, 49.0, step_m=50.0) is True


def test_has_los_blocked_by_forest():
    """LOS through dense forest should be blocked (obstacle height)."""
    ts = _forest_north_terrain()
    los = LOSService(ts)
    # Through forest to the north
    assert los.has_los(4.5, 49.0, 4.5, 49.01, step_m=50.0) is False
    # South is clear
    assert los.has_los(4.5, 49.0, 4.5, 48.99, step_m=50.0) is True


# ── Tests: GeoJSON Output ───────────────────────────────


def test_geojson_format():
    """compute_viewshed_geojson should return valid GeoJSON Feature."""
    ts = TerrainService()
    los = LOSService(ts)
    gj = los.compute_viewshed_geojson(4.5, 49.0, 1000.0, num_rays=12)

    assert gj["type"] == "Feature"
    assert gj["geometry"]["type"] == "Polygon"
    assert len(gj["geometry"]["coordinates"]) == 1
    assert len(gj["geometry"]["coordinates"][0]) == 13  # 12 + 1 closing
    assert gj["properties"]["max_range_m"] == 1000.0
    assert gj["properties"]["num_rays"] == 12


def test_same_point_los():
    """LOS to same point should be True."""
    ts = _flat_terrain()
    los = LOSService(ts)
    assert los.has_los(4.5, 49.0, 4.5, 49.0) is True


# ── Tests: Terrain-only (no elevation) blocking ─────────


def _forest_no_elevation():
    """Terrain cells with forest, but NO elevation data."""
    grid = MockGrid()
    tc = {f"A1-{i}": "open" for i in range(1, 10)}
    tc["A1-1"] = "forest"
    tc["A1-2"] = "forest"
    return _make_terrain(tc, None, grid)


def test_terrain_only_blocks_los():
    """Terrain obstacles should block LOS even without elevation data.

    Forest (12m) should block an infantry observer (2m eye height)
    even when all ground elevations are 0.
    """
    ts = _forest_no_elevation()
    los = LOSService(ts)
    # Through forest to the north — should be blocked (12m > 2m)
    assert los.has_los(4.5, 49.0, 4.5, 49.01, eye_height=2.0, step_m=50.0) is False
    # South is clear (no forest)
    assert los.has_los(4.5, 49.0, 4.5, 48.99, eye_height=2.0, step_m=50.0) is True


def test_terrain_only_viewshed_not_circle():
    """With terrain cells but no elevation, viewshed should NOT be a circle.

    Rays through forest should be blocked, producing a non-circular polygon.
    """
    ts = _forest_no_elevation()
    los = LOSService(ts)
    pts = los.compute_viewshed(4.5, 49.0, 2000.0, eye_height=2.0, num_rays=36, step_m=50.0)

    dists = []
    for lon, lat in pts[:-1]:
        dlat = (lat - 49.0) * 111320
        dlon = (lon - 4.5) * 74000
        dists.append(math.sqrt(dlat * dlat + dlon * dlon))

    # North rays should be shorter (blocked by forest)
    # South rays should reach full range
    assert max(dists) - min(dists) > 200, (
        f"Expected non-circular viewshed with forest blocking, "
        f"but min={min(dists):.0f} max={max(dists):.0f}"
    )


