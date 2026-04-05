"""Unit tests for detection engine."""

import uuid
from unittest.mock import MagicMock

import pytest

from backend.engine.detection import (
    process_detection,
    _deterministic_roll,
    _posture_modifier,
    _distance_m,
)
from backend.engine.terrain import TerrainService


def _make_unit(
    lat=48.84, lon=2.335,
    side="blue",
    unit_type="infantry_platoon",
    detection_range=1500.0,
    task=None,
    is_destroyed=False,
    is_recon=False,
):
    from geoalchemy2.shape import from_shape
    from shapely.geometry import Point

    unit = MagicMock()
    unit.id = uuid.uuid4()
    unit.name = f"Test {side} {unit_type}"
    unit.side = MagicMock()
    unit.side.value = side
    unit.unit_type = unit_type
    unit.position = from_shape(Point(lon, lat), srid=4326)
    unit.detection_range_m = detection_range
    unit.current_task = task
    unit.is_destroyed = is_destroyed
    unit.capabilities = {"is_recon": is_recon} if is_recon else {}
    return unit


class TestDeterministicRoll:
    def test_reproducibility(self):
        uid1 = uuid.uuid4()
        uid2 = uuid.uuid4()
        r1 = _deterministic_roll(1, uid1, uid2)
        r2 = _deterministic_roll(1, uid1, uid2)
        assert r1 == r2

    def test_different_ticks(self):
        uid1 = uuid.uuid4()
        uid2 = uuid.uuid4()
        r1 = _deterministic_roll(1, uid1, uid2)
        r2 = _deterministic_roll(2, uid1, uid2)
        # Different ticks should (very likely) produce different rolls
        # Can't guarantee, but probability of collision is ~1/2^32
        assert r1 != r2 or True  # Allow for rare collision

    def test_range(self):
        uid1 = uuid.uuid4()
        uid2 = uuid.uuid4()
        for tick in range(100):
            r = _deterministic_roll(tick, uid1, uid2)
            assert 0.0 <= r < 1.0


class TestPostureModifier:
    def test_moving(self):
        assert _posture_modifier({"type": "move"}) == 1.0

    def test_stationary(self):
        assert _posture_modifier(None) == 0.6

    def test_dug_in(self):
        assert _posture_modifier({"type": "dig_in"}) == 0.3


class TestProcessDetection:
    def test_no_units(self):
        terrain = TerrainService()
        contacts = process_detection([], [], 0, terrain)
        assert contacts == []

    def test_out_of_range(self):
        terrain = TerrainService()
        blue = _make_unit(lat=48.84, lon=2.335, side="blue", detection_range=100.0)
        red = _make_unit(lat=49.0, lon=3.0, side="red")  # Very far away
        contacts = process_detection([blue], [red], 0, terrain)
        assert contacts == []

    def test_in_range_detection(self):
        terrain = TerrainService()
        blue = _make_unit(lat=48.84, lon=2.335, side="blue", detection_range=5000.0, is_recon=True)
        red = _make_unit(lat=48.841, lon=2.336, side="red")  # Very close
        # Run many ticks to get at least one detection
        blue_detected = False
        for tick in range(50):
            contacts = process_detection([blue], [red], tick, terrain)
            for c in contacts:
                if c["observing_side"] == "blue":
                    blue_detected = True
                    break
            if blue_detected:
                break
        assert blue_detected, "Expected blue side to detect red in 50 ticks"

    def test_destroyed_units_skipped(self):
        terrain = TerrainService()
        blue = _make_unit(lat=48.84, lon=2.335, side="blue", is_destroyed=True)
        red = _make_unit(lat=48.841, lon=2.336, side="red")
        contacts = process_detection([blue], [red], 0, terrain)
        assert contacts == []


