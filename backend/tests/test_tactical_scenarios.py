import uuid
from types import SimpleNamespace

from geoalchemy2.shape import from_shape
from shapely.geometry import Point

from backend.engine.combat import process_combat, process_artillery_support
from backend.engine.engineering import process_engineering
from backend.models.map_object import MapObject, ObjectCategory, ObjectSide
from backend.schemas.order import (
    DetectedLanguage,
    MessageClassification,
    OrderType,
    ParsedOrderData,
    ResolvedLocation,
)
from backend.services.location_resolver import LocationResolver
from backend.services.order_parser import order_parser
from backend.services.order_service import OrderService


class _StubTerrain:
    def attack_modifier(self, lon: float, lat: float) -> float:
        return 1.0

    def protection_factor(self, lon: float, lat: float) -> float:
        return 1.0


def _pt(lat: float, lon: float):
    return from_shape(Point(lon, lat), srid=4326)


def test_scenario_recon_observe_bridge_resolves_real_map_object():
    parsed = order_parser._fallback_parse(
        "Recon section, observe the crossing and report any movement."
    )

    resolver = LocationResolver(
        map_objects=[
            {
                "object_type": "bridge_structure",
                "name": "Eastern Crossing",
                "lat": 48.145,
                "lon": 24.715,
            }
        ]
    )
    resolved = resolver.resolve_all(parsed.location_refs)

    assert parsed.classification == MessageClassification.command
    assert parsed.order_type == OrderType.observe
    assert any(loc.ref_type == "map_object" for loc in parsed.location_refs)
    assert any(loc.lat == 48.145 and loc.lon == 24.715 for loc in resolved)


def test_scenario_engineer_breach_completes_on_real_obstacle():
    session_id = uuid.uuid4()
    engineer = SimpleNamespace(
        id=uuid.uuid4(),
        session_id=session_id,
        name="Engineer section",
        unit_type="combat_engineer_section",
        side="blue",
        is_destroyed=False,
        suppression=0.0,
        position=_pt(48.15, 24.71),
        current_task={"type": "breach", "target_object_id": None},
    )
    obstacle = MapObject(
        id=uuid.uuid4(),
        session_id=session_id,
        side=ObjectSide.neutral,
        object_type="roadblock",
        object_category=ObjectCategory.obstacle,
        geometry=_pt(48.1502, 24.7101),
        label="Roadblock Alpha",
        properties={},
        is_active=True,
        health=1.0,
    )
    engineer.current_task["target_object_id"] = str(obstacle.id)

    new_objects = []
    events_1 = process_engineering([engineer], [obstacle], session_id, new_objects)
    events_2 = process_engineering([engineer], [obstacle], session_id, new_objects)

    assert any(evt["payload"]["action"] == "breach_progress" for evt in events_1)
    assert any(evt["payload"]["action"] == "breach_complete" for evt in events_2)
    assert obstacle.is_active is False
    assert engineer.current_task is None


def test_scenario_smoke_order_sets_smoke_fire_task():
    parsed = order_parser._fallback_parse(
        "Mortar, put smoke on the bridge crossing at E6-2."
    )

    task = OrderService()._build_engine_task(
        order=type("OrderStub", (), {"id": "00000000-0000-0000-0000-000000000031"})(),
        parsed=ParsedOrderData(
            classification=parsed.classification,
            language=DetectedLanguage.en,
            target_unit_refs=parsed.target_unit_refs,
            order_type=parsed.order_type,
            location_refs=parsed.location_refs,
            map_object_type=parsed.map_object_type,
        ),
        resolved_locations=[
            ResolvedLocation(
                source_text="bridge crossing",
                ref_type="map_object",
                normalized_ref="bridge_structure",
                lat=48.160,
                lon=24.730,
            )
        ],
        intent=None,
        grid_service=None,
    )

    assert parsed.order_type == OrderType.fire
    assert parsed.map_object_type == "smoke"
    assert task["fire_effect_type"] == "smoke"
    assert task["salvos_remaining"] == 1
    assert task["target_location"] == {"lat": 48.160, "lon": 24.730}


def test_scenario_smoke_request_assigns_single_mortar_mission():
    requester = SimpleNamespace(
        id=uuid.uuid4(),
        name="C-squad",
        side="blue",
        parent_unit_id=None,
        is_destroyed=False,
        current_task=None,
    )
    mortar = SimpleNamespace(
        id=uuid.uuid4(),
        name="Mortar",
        side="blue",
        unit_type="mortar_section",
        parent_unit_id=None,
        is_destroyed=False,
        ammo=1.0,
        position=_pt(48.10, 24.60),
        current_task=None,
    )

    events = process_artillery_support(
        [requester, mortar],
        terrain=_StubTerrain(),
        fire_requests=[
            {
                "unit_id": str(requester.id),
                "target_location": {"lat": 48.14, "lon": 24.66},
                "target_unit_id": None,
                "coordination_unit_refs": ["Mortar"],
                "fire_effect_type": "smoke",
                "smoke_duration_ticks": 4,
            }
        ],
    )

    assert mortar.current_task is not None
    assert mortar.current_task["type"] == "fire"
    assert mortar.current_task["fire_effect_type"] == "smoke"
    assert mortar.current_task["sustained_support"] is False
    assert mortar.current_task["salvos_remaining"] == 1
    assert any(evt["payload"]["support_type"] == "smoke_request" for evt in events)


def test_scenario_smoke_fire_creates_transient_map_object():
    session_id = uuid.uuid4()
    mortar = SimpleNamespace(
        id=uuid.uuid4(),
        session_id=session_id,
        name="Mortar",
        side="blue",
        unit_type="mortar_section",
        is_destroyed=False,
        strength=1.0,
        ammo=1.0,
        suppression=0.0,
        capabilities={},
        position=_pt(48.100, 24.600),
        current_task={
            "type": "fire",
            "target_location": {"lat": 48.120, "lon": 24.620},
            "fire_effect_type": "smoke",
            "smoke_duration_ticks": 4,
            "salvos_remaining": 1,
        },
    )

    new_objects = []
    events, under_fire = process_combat(
        [mortar],
        terrain=_StubTerrain(),
        map_objects=[],
        contacts=[],
        new_map_objects_out=new_objects,
    )

    assert under_fire == set()
    assert any(evt["event_type"] == "smoke_deployed" for evt in events)
    assert len(new_objects) == 1
    assert new_objects[0].object_type == "smoke"
    assert new_objects[0].properties["ticks_remaining"] == 4
    assert mortar.current_task is None
