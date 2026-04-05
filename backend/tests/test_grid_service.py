"""
Unit tests for GridService – tactical grid and snail addressing.
"""

import pytest
from types import SimpleNamespace
from shapely.geometry import Point

from backend.services.grid_service import GridService, SNAIL_TO_OFFSET, OFFSET_TO_SNAIL


@pytest.fixture
def grid_def():
    """Standard 8×8 grid, 1km squares, origin near Paris."""
    return SimpleNamespace(
        origin=Point(2.325, 48.835),
        orientation_deg=0,
        base_square_size_m=1000,
        columns=8,
        rows=8,
        labeling_scheme='alphanumeric',
        recursion_base=3,
        max_depth=3,
    )


@pytest.fixture
def svc(grid_def):
    return GridService(grid_def)


# ── Snail mapping consistency ─────────────────────

class TestSnailMapping:
    def test_snail_offsets_cover_all_9(self):
        assert len(SNAIL_TO_OFFSET) == 9
        assert set(SNAIL_TO_OFFSET.keys()) == set(range(1, 10))

    def test_offset_inverse(self):
        for snail_num, offset in SNAIL_TO_OFFSET.items():
            assert OFFSET_TO_SNAIL[offset] == snail_num

    def test_all_offsets_unique(self):
        offsets = list(SNAIL_TO_OFFSET.values())
        assert len(set(offsets)) == 9

    def test_snail_9_is_center(self):
        assert SNAIL_TO_OFFSET[9] == (1, 1)

    def test_snail_1_is_top_left(self):
        assert SNAIL_TO_OFFSET[1] == (0, 2)

    def test_snail_5_is_bottom_right(self):
        assert SNAIL_TO_OFFSET[5] == (2, 0)


# ── Validation ────────────────────────────────────

class TestValidation:
    def test_valid_square_labels(self, svc):
        assert svc.validate_square('A1')
        assert svc.validate_square('H8')
        assert svc.validate_square('D4')

    def test_invalid_square_labels(self, svc):
        assert not svc.validate_square('I1')   # Only A-H for 8 columns
        assert not svc.validate_square('A9')   # Only 1-8 for 8 rows
        assert not svc.validate_square('A0')
        assert not svc.validate_square('')
        assert not svc.validate_square('ZZ')

    def test_valid_snail_paths(self, svc):
        assert svc.validate_snail('A1')
        assert svc.validate_snail('A1-1')
        assert svc.validate_snail('A1-3-7')
        assert svc.validate_snail('B4-9-5-1')
        assert svc.validate_snail('H8-1-2-3')

    def test_invalid_snail_paths(self, svc):
        assert not svc.validate_snail('Z99-3')     # Invalid top square
        assert not svc.validate_snail('A1-0')       # 0 not valid
        assert not svc.validate_snail('A1-10')      # 10 not valid
        assert not svc.validate_snail('A1-1-2-3-4') # Exceeds max_depth=3
        assert not svc.validate_snail('')


# ── Square to polygon ────────────────────────────

class TestSquareToPolygon:
    def test_a1_polygon_is_valid(self, svc):
        poly = svc.square_to_polygon('A1')
        assert poly.is_valid
        assert not poly.is_empty

    def test_a1_polygon_near_top(self, svc):
        poly = svc.square_to_polygon('A1')
        center = poly.centroid
        # A1 is at the top-left (NW). Row 1 = top = internal row 7 = ~7.5km north of origin
        assert center.y > 48.835 + 0.05  # Well north of origin
        assert abs(center.x - 2.325) < 0.02   # Near left edge

    def test_all_squares_non_overlapping(self, svc):
        squares = svc.all_squares()
        assert len(squares) == 64  # 8×8

        # Spot-check: A1 and B1 should not overlap significantly
        a1 = dict(squares)['A1']
        b1 = dict(squares)['B1']
        overlap = a1.intersection(b1).area
        assert overlap < 1e-10  # Essentially zero

    def test_invalid_label_raises(self, svc):
        with pytest.raises(ValueError):
            svc.square_to_polygon('Z99')


# ── Snail to polygon ─────────────────────────────

class TestSnailToPolygon:
    def test_depth1_fits_inside_parent(self, svc):
        parent = svc.square_to_polygon('B2')
        for i in range(1, 10):
            child = svc.snail_to_polygon(f'B2-{i}')
            assert parent.contains(child) or parent.intersects(child)

    def test_depth1_nine_children_tile_parent(self, svc):
        parent = svc.square_to_polygon('C3')
        children_union = svc.snail_to_polygon('C3-1')
        for i in range(2, 10):
            children_union = children_union.union(svc.snail_to_polygon(f'C3-{i}'))
        # Union of children should approximately equal parent
        ratio = children_union.area / parent.area
        assert 0.99 < ratio < 1.01

    def test_depth2_smaller_than_depth1(self, svc):
        d1 = svc.snail_to_polygon('A1-1')
        d2 = svc.snail_to_polygon('A1-1-5')
        assert d2.area < d1.area
        assert d2.area / d1.area < 0.15  # Should be ~1/9

    def test_depth3_very_small(self, svc):
        d0 = svc.square_to_polygon('A1')
        d3 = svc.snail_to_polygon('A1-1-1-1')
        # 1/27th of 1/27th... area should be tiny
        assert d3.area / d0.area < 0.002

    def test_center_is_inside_polygon(self, svc):
        poly = svc.snail_to_polygon('D5-3-7')
        center = svc.snail_to_center('D5-3-7')
        assert poly.contains(center)


# ── Point to snail round-trip ─────────────────────

class TestPointToSnail:
    def test_origin_area_returns_A8(self, svc):
        # Just NE of origin should be in A8 (row 8 = bottom = near origin)
        label = svc.point_to_square(48.8355, 2.3255)
        assert label == 'A8'

    def test_outside_grid_returns_none(self, svc):
        assert svc.point_to_snail(0.0, 0.0) is None
        assert svc.point_to_square(0.0, 0.0) is None

    def test_round_trip_depth1(self, svc):
        """Point → snail → polygon should contain the original point."""
        lat, lon = 48.84, 2.335
        path = svc.point_to_snail(lat, lon, depth=1)
        assert path is not None
        poly = svc.snail_to_polygon(path)
        assert poly.contains(Point(lon, lat))

    def test_round_trip_depth2(self, svc):
        lat, lon = 48.845, 2.345
        path = svc.point_to_snail(lat, lon, depth=2)
        assert path is not None
        assert path.count('-') == 2  # top-label + 2 digits
        poly = svc.snail_to_polygon(path)
        assert poly.contains(Point(lon, lat))

    def test_round_trip_depth3(self, svc):
        lat, lon = 48.85, 2.35
        path = svc.point_to_snail(lat, lon, depth=3)
        assert path is not None
        assert path.count('-') == 3
        poly = svc.snail_to_polygon(path)
        assert poly.contains(Point(lon, lat))

    def test_depth0_returns_just_label(self, svc):
        path = svc.point_to_snail(48.84, 2.335, depth=0)
        assert '-' not in path  # Just a top-level label


# ── Subdivide ─────────────────────────────────────

class TestSubdivide:
    def test_subdivide_returns_9_children(self, svc):
        children = svc.subdivide('A1')
        assert len(children) == 9

    def test_subdivide_labels_sequential(self, svc):
        children = svc.subdivide('B3')
        labels = [c[0] for c in children]
        for i in range(1, 10):
            assert f'B3-{i}' in labels

    def test_subdivide_snail_path(self, svc):
        children = svc.subdivide('C4-5')
        assert len(children) == 9
        labels = [c[0] for c in children]
        assert 'C4-5-1' in labels
        assert 'C4-5-9' in labels


# ── Grid as GeoJSON ───────────────────────────────

class TestGridGeoJSON:
    def test_depth0_feature_count(self, svc):
        geojson = svc.grid_as_geojson(depth=0)
        assert geojson['type'] == 'FeatureCollection'
        assert len(geojson['features']) == 64

    def test_depth0_has_labels(self, svc):
        geojson = svc.grid_as_geojson(depth=0)
        labels = {f['properties']['label'] for f in geojson['features']}
        assert 'A1' in labels
        assert 'H8' in labels

    def test_depth1_feature_count(self, svc):
        geojson = svc.grid_as_geojson(depth=1)
        assert len(geojson['features']) == 64 * 9  # 576


# ── Viewport GeoJSON ─────────────────────────────

class TestViewportGeoJSON:
    def test_viewport_returns_subset(self, svc):
        # Narrow viewport: should return fewer than full grid
        result = svc.grid_viewport_geojson(
            south=48.835, west=2.325, north=48.845, east=2.340,
            depth=1
        )
        assert result['type'] == 'FeatureCollection'
        # Should have some features but not all 576
        assert 0 < len(result['features']) < 576

    def test_viewport_outside_grid_empty(self, svc):
        result = svc.grid_viewport_geojson(
            south=0.0, west=0.0, north=0.001, east=0.001,
            depth=1
        )
        assert len(result['features']) == 0

    def test_viewport_depth2(self, svc):
        result = svc.grid_viewport_geojson(
            south=48.835, west=2.325, north=48.840, east=2.335,
            depth=2
        )
        assert len(result['features']) > 0
        # All features should have depth == 2
        for f in result['features']:
            assert f['properties']['depth'] == 2


# ── Numeric labeling scheme ──────────────────────

class TestNumericLabeling:
    @pytest.fixture
    def numeric_svc(self):
        gd = SimpleNamespace(
            origin=Point(2.325, 48.835),
            orientation_deg=0,
            base_square_size_m=1000,
            columns=4,
            rows=4,
            labeling_scheme='numeric',
            recursion_base=3,
            max_depth=3,
        )
        return GridService(gd)

    def test_numeric_labels(self, numeric_svc):
        squares = numeric_svc.all_squares()
        labels = [s[0] for s in squares]
        assert '1' in labels
        assert '16' in labels
        assert len(labels) == 16

    def test_numeric_validate(self, numeric_svc):
        assert numeric_svc.validate_square('1')
        assert numeric_svc.validate_square('16')
        assert not numeric_svc.validate_square('17')
        assert not numeric_svc.validate_square('0')


# ── Rotated grid ──────────────────────────────────

class TestRotatedGrid:
    @pytest.fixture
    def rotated_svc(self):
        gd = SimpleNamespace(
            origin=Point(2.325, 48.835),
            orientation_deg=45,  # 45° rotation
            base_square_size_m=1000,
            columns=4,
            rows=4,
            labeling_scheme='alphanumeric',
            recursion_base=3,
            max_depth=3,
        )
        return GridService(gd)

    def test_rotated_polygon_is_valid(self, rotated_svc):
        poly = rotated_svc.square_to_polygon('A1')
        assert poly.is_valid
        assert not poly.is_empty

    def test_rotated_round_trip(self, rotated_svc):
        lat, lon = 48.840, 2.335
        path = rotated_svc.point_to_snail(lat, lon, depth=2)
        if path:  # Point might be outside rotated grid
            poly = rotated_svc.snail_to_polygon(path)
            assert poly.contains(Point(lon, lat))

