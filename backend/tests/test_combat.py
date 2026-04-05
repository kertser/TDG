"""Unit tests for combat resolution engine."""

import uuid
from unittest.mock import MagicMock

import pytest

from backend.engine.combat import (
    process_combat,
    _ammo_factor,
    _distance_m,
    BASE_FIREPOWER,
    WEAPON_RANGE,
    DAMAGE_SCALAR,
)
from backend.engine.terrain import TerrainService


def _make_unit(
    lat=48.84, lon=2.335,
    side="blue",
    unit_type="infantry_platoon",
    task=None,
    strength=1.0, ammo=1.0, suppression=0.0, morale=1.0,
    is_destroyed=False,
):
    from geoalchemy2.shape import from_shape
    from shapely.geometry import Point

    unit = MagicMock()
    unit.id = uuid.uuid4()
    unit.name = f"Test {unit_type}"
    unit.side = MagicMock()
    unit.side.value = side
    unit.unit_type = unit_type
    unit.position = from_shape(Point(lon, lat), srid=4326)
    unit.current_task = task
    unit.strength = strength
    unit.ammo = ammo
    unit.suppression = suppression
    unit.morale = morale
    unit.is_destroyed = is_destroyed
    unit.capabilities = {}
    return unit


class TestAmmoFactor:
    def test_full_ammo(self):
        assert _ammo_factor(1.0) == 1.0

    def test_half_ammo(self):
        assert _ammo_factor(0.3) == 0.7

    def test_low_ammo(self):
        assert _ammo_factor(0.1) == 0.3


class TestProcessCombat:
    def test_no_attack_task(self):
        terrain = TerrainService()
        unit = _make_unit(task=None)
        events, under_fire = process_combat([unit], terrain)
        assert events == []
        assert len(under_fire) == 0

    def test_attack_in_range(self):
        terrain = TerrainService()
        attacker_id = uuid.uuid4()
        target_id = uuid.uuid4()

        attacker = _make_unit(
            lat=48.84, lon=2.335, side="blue",
            task={"type": "attack", "target_unit_id": str(target_id)},
        )
        target = _make_unit(
            lat=48.8401, lon=2.3351, side="red",
        )
        target.id = target_id

        events, under_fire = process_combat([attacker, target], terrain)
        assert len(events) >= 1
        assert target.id in under_fire
        # Target should have taken damage
        assert target.strength < 1.0

    def test_out_of_range(self):
        terrain = TerrainService()
        target_id = uuid.uuid4()

        attacker = _make_unit(
            lat=48.84, lon=2.335, side="blue",
            unit_type="infantry_platoon",
            task={"type": "attack", "target_unit_id": str(target_id)},
        )
        target = _make_unit(
            lat=49.0, lon=3.0, side="red",  # Very far away
        )
        target.id = target_id

        events, under_fire = process_combat([attacker, target], terrain)
        assert events == []
        assert target.strength == 1.0

    def test_destroyed_attacker_skipped(self):
        terrain = TerrainService()
        attacker = _make_unit(
            task={"type": "attack", "target_unit_id": str(uuid.uuid4())},
            is_destroyed=True,
        )
        events, under_fire = process_combat([attacker], terrain)
        assert events == []

    def test_unit_destruction(self):
        terrain = TerrainService()
        target_id = uuid.uuid4()

        attacker = _make_unit(
            lat=48.84, lon=2.335, side="blue",
            unit_type="tank_company",  # High firepower
            task={"type": "attack", "target_unit_id": str(target_id)},
            strength=1.0, ammo=1.0,
        )
        target = _make_unit(
            lat=48.8401, lon=2.3351, side="red",
            strength=0.01,  # Nearly destroyed
        )
        target.id = target_id

        events, under_fire = process_combat([attacker, target], terrain)
        assert target.is_destroyed is True
        assert any(e["event_type"] == "unit_destroyed" for e in events)

