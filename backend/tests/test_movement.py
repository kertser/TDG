"""Unit tests for movement calculations."""

import uuid
from unittest.mock import MagicMock

import pytest

from backend.engine.movement import (
    process_movement,
    _morale_factor,
    _distance_m,
    _move_toward,
)
from backend.engine.terrain import TerrainService


def _make_unit(
    lat=48.84, lon=2.335,
    task=None,
    speed=4.0, suppression=0.0, morale=1.0,
    is_destroyed=False,
):
    """Create a mock unit for testing."""
    from geoalchemy2.shape import from_shape
    from shapely.geometry import Point

    unit = MagicMock()
    unit.id = uuid.uuid4()
    unit.name = "Test Unit"
    unit.position = from_shape(Point(lon, lat), srid=4326)
    unit.heading_deg = 0.0
    unit.current_task = task
    unit.move_speed_mps = speed
    unit.suppression = suppression
    unit.morale = morale
    unit.is_destroyed = is_destroyed
    return unit


class TestMoraleFactor:
    def test_high_morale(self):
        assert _morale_factor(0.8) == 1.0

    def test_medium_morale(self):
        assert _morale_factor(0.35) == 0.7

    def test_low_morale(self):
        assert _morale_factor(0.1) == 0.4

    def test_boundary_above(self):
        assert _morale_factor(0.51) == 1.0

    def test_boundary_at_half(self):
        assert _morale_factor(0.5) == 0.7

    def test_boundary_at_quarter(self):
        assert _morale_factor(0.25) == 0.7


class TestDistance:
    def test_zero_distance(self):
        assert _distance_m(48.0, 2.0, 48.0, 2.0) == 0.0

    def test_known_distance(self):
        # Roughly 111km per degree latitude
        d = _distance_m(48.0, 2.0, 49.0, 2.0)
        assert abs(d - 111320) < 100

    def test_short_distance(self):
        d = _distance_m(48.0, 2.0, 48.001, 2.0)
        assert 100 < d < 120


class TestMoveToward:
    def test_move_north(self):
        new_lat, new_lon, heading = _move_toward(48.0, 2.0, 49.0, 2.0, 1000)
        assert new_lat > 48.0
        assert abs(new_lon - 2.0) < 0.0001
        assert abs(heading - 0) < 1  # North

    def test_move_east(self):
        new_lat, new_lon, heading = _move_toward(48.0, 2.0, 48.0, 3.0, 1000)
        assert abs(new_lat - 48.0) < 0.001
        assert new_lon > 2.0
        assert abs(heading - 90) < 5  # East

    def test_arrive_at_target(self):
        new_lat, new_lon, _ = _move_toward(48.0, 2.0, 48.0001, 2.0, 999999)
        assert abs(new_lat - 48.0001) < 0.00001
        assert abs(new_lon - 2.0) < 0.00001


class TestProcessMovement:
    def test_no_units(self):
        terrain = TerrainService()
        events = process_movement([], 60, terrain)
        assert events == []

    def test_no_task(self):
        terrain = TerrainService()
        unit = _make_unit(task=None)
        events = process_movement([unit], 60, terrain)
        assert events == []

    def test_move_task(self):
        terrain = TerrainService()
        task = {
            "type": "move",
            "target_location": {"lat": 48.85, "lon": 2.34},
        }
        unit = _make_unit(task=task, speed=4.0, morale=1.0)
        events = process_movement([unit], 60, terrain)
        assert len(events) == 1
        assert events[0]["event_type"] == "movement"

    def test_destroyed_unit_skipped(self):
        terrain = TerrainService()
        task = {"type": "move", "target_location": {"lat": 48.85, "lon": 2.34}}
        unit = _make_unit(task=task, is_destroyed=True)
        events = process_movement([unit], 60, terrain)
        assert events == []

    def test_suppression_slows_movement(self):
        terrain = TerrainService()
        task = {"type": "move", "target_location": {"lat": 49.0, "lon": 2.0}}

        unit_normal = _make_unit(task=dict(task), speed=4.0, suppression=0.0)
        unit_suppressed = _make_unit(task=dict(task), speed=4.0, suppression=1.0)

        events_normal = process_movement([unit_normal], 60, terrain)
        events_suppressed = process_movement([unit_suppressed], 60, terrain)

        # Suppressed unit moves less distance
        speed_normal = events_normal[0]["payload"]["speed_mps"]
        speed_suppressed = events_suppressed[0]["payload"]["speed_mps"]
        assert speed_suppressed < speed_normal

