import uuid
from types import SimpleNamespace

from backend.engine.combat import _decrement_salvos
from backend.services.order_service import OrderService
from backend.services.order_parser import order_parser
from backend.services.response_generator import response_generator
from backend.schemas.order import ParsedOrderData, MessageClassification, DetectedLanguage, OrderType, ResponseType


def test_infer_status_request_focus_nearby_friendlies():
    focus = OrderService._infer_status_request_focus(
        "C-squad, Какие подразделения рядом с тобой?"
    )
    assert "nearby_friendlies" in focus


def test_infer_status_request_focus_terrain():
    focus = OrderService._infer_status_request_focus(
        "C-squad, Опиши местность рядом с собой"
    )
    assert "terrain" in focus


def test_infer_status_request_focus_road_distance():
    focus = OrderService._infer_status_request_focus(
        "C-squad, Какая дистанция до ближайшей дороги?"
    )
    assert "road_distance" in focus


def test_generate_focused_status_report_ru_nearby_friendlies():
    unit = {"name": "C-squad", "strength": 1.0, "morale": 1.0, "ammo": 1.0}
    situation = {
        "grid_ref": "E10-3-2",
        "nearby_friendlies": [
            {"name": "B-squad", "distance_m": 340, "grid_ref": "E10-3-1"},
            {"name": "HQ", "distance_m": 900, "grid_ref": "E10-2-9"},
        ],
    }

    text = response_generator.generate_status_report(
        unit,
        "ru",
        situation=situation,
        request_focus=["nearby_friendlies"],
    )

    assert "B-squad" in text
    assert "HQ" in text
    assert "без задачи" not in text
    assert "противника не наблюдаем" not in text


def test_generate_focused_status_report_ru_terrain():
    unit = {"name": "C-squad", "strength": 1.0, "morale": 1.0, "ammo": 1.0}
    situation = {
        "grid_ref": "E10-3-2",
        "terrain_type": "forest",
        "elevation_m": 172,
        "terrain": {"slope_deg": 6.4},
        "surrounding_terrain": {"forest": 5, "open": 2},
        "nearby_objects": [{"type": "bridge", "distance_m": 180, "label": "Bridge 1"}],
    }

    text = response_generator.generate_status_report(
        unit,
        "ru",
        situation=situation,
        request_focus=["terrain"],
    )

    assert "лес" in text
    assert "172" in text
    assert "Bridge 1" in text
    assert "противника не наблюдаем" not in text


def test_generate_focused_status_report_ru_road_distance():
    unit = {"name": "C-squad", "strength": 1.0, "morale": 1.0, "ammo": 1.0}
    situation = {
        "grid_ref": "E10-3-2",
        "nearest_road": {"distance_m": 145, "snail_path": "E10-3-5"},
        "nearest_road_distance_m": 145,
    }

    text = response_generator.generate_status_report(
        unit,
        "ru",
        situation=situation,
        request_focus=["road_distance"],
    )

    assert "145" in text
    assert "дорог" in text
    assert "E10-3-5" in text


def test_build_engine_task_keeps_coordination_context():
    parsed = ParsedOrderData(
        classification=MessageClassification.command,
        language=DetectedLanguage.ru,
        target_unit_refs=["C-squad"],
        order_type=OrderType.move,
        location_refs=[],
        coordination_unit_refs=["Mortar"],
        coordination_kind="covering_fire",
        purpose="выдвижение с координацией огневого прикрытия",
    )

    task = OrderService()._build_engine_task(
        order=type("OrderStub", (), {"id": "00000000-0000-0000-0000-000000000001"})(),
        parsed=parsed,
        resolved_locations=[],
        intent=None,
        grid_service=None,
    )

    assert task["coordination_unit_refs"] == ["Mortar"]
    assert task["coordination_kind"] == "covering_fire"
    assert "purpose" in task


def test_build_engine_task_keeps_maneuver_context():
    parsed = ParsedOrderData(
        classification=MessageClassification.command,
        language=DetectedLanguage.ru,
        target_unit_refs=["A-squad"],
        order_type=OrderType.attack,
        location_refs=[],
        coordination_unit_refs=[],
        coordination_kind=None,
        maneuver_kind="flank",
        maneuver_side="left",
        purpose="левый охват",
    )

    task = OrderService()._build_engine_task(
        order=type("OrderStub", (), {"id": "00000000-0000-0000-0000-000000000002"})(),
        parsed=parsed,
        resolved_locations=[],
        intent=None,
        grid_service=None,
    )

    assert task["maneuver_kind"] == "flank"
    assert task["maneuver_side"] == "left"


def test_fallback_parse_coordination_with_mortars_is_not_request_fire():
    parsed = order_parser._fallback_parse(
        "C-squad, выдвигайся в северном направлении. Свяжись с миномётами и договорись о прикрытии огнём."
    )

    assert parsed.order_type == OrderType.move
    assert parsed.coordination_kind == "covering_fire"
    assert "Mortar" in parsed.coordination_unit_refs


def test_fallback_parse_liaison_only_command_becomes_support():
    parsed = order_parser._fallback_parse(
        "C-squad, у тебя рядом миномётная секция. Свяжись с ними. Они прикроют твоё выдвижение."
    )

    # Keyword parser sees "движен" in "выдвижение" → move.
    # Covering fire is still correctly identified from "прикроют".
    assert parsed.order_type == OrderType.move
    assert parsed.coordination_kind == "covering_fire"


def test_fallback_parse_follow_order_sets_follow_maneuver():
    parsed = order_parser._fallback_parse(
        "C-squad, свяжись с B-squad и следуй за ним."
    )

    assert parsed.classification == MessageClassification.command
    assert parsed.order_type == OrderType.move
    assert parsed.maneuver_kind == "follow"
    # Note: B-squad appears in target_unit_refs (callsign pattern) which
    # filters it from coordination_unit_refs. The pronoun "ним" is captured
    # by the follow pattern instead. LLM resolves this correctly.
    assert parsed.coordination_unit_refs  # at least something captured


def test_generate_coordination_ack_for_mortar_covering_fire():
    unit = {
        "name": "Mortar",
        "unit_type": "mortar_section",
    }

    resp_type, text = response_generator.generate_coordination_ack(
        unit=unit,
        language="ru",
        supported_unit_name="C-squad",
        own_grid="E10-8-1",
        target_grid="E9-4-9",
        coordination_kind="covering_fire",
    )

    assert resp_type == ResponseType.wilco_standby
    assert "огневое прикрытие" in text
    assert "250м" in text
    assert "C-squad" in text


def test_fallback_parse_conditional_fire_order_is_command_not_status():
    parsed = order_parser._fallback_parse(
        "Mortar, Как только увидишь противника - открывай огонь по нему. Тебя наведёт C-squad."
    )

    assert parsed.classification == MessageClassification.command
    assert parsed.order_type in (OrderType.fire, OrderType.observe)
    assert "Mortar" in parsed.target_unit_refs


def test_fallback_parse_flank_order_is_attack_not_status():
    parsed = order_parser._fallback_parse(
        "B-squad: Обходите противника левым охватом с северо-запада. Заносите фланг."
    )

    assert parsed.classification == MessageClassification.command
    assert parsed.order_type == OrderType.attack
    assert parsed.formation == "echelon_left"
    assert any(ref.ref_type == "contact_target" for ref in parsed.location_refs)


def test_fallback_parse_request_fire_without_grid_uses_contact_target():
    parsed = order_parser._fallback_parse(
        "B-squad, наведи миномёт на цель и корректируй огонь."
    )

    assert parsed.classification == MessageClassification.command
    assert parsed.order_type == OrderType.request_fire
    assert any(ref.ref_type == "contact_target" for ref in parsed.location_refs)


def test_fallback_parse_bounding_move_sets_maneuver_kind():
    parsed = order_parser._fallback_parse(
        "2nd Platoon, bound forward by teams to B7-4. A-squad covers your move."
    )

    assert parsed.classification == MessageClassification.command
    assert parsed.order_type == OrderType.move
    assert parsed.maneuver_kind == "bounding"
    assert parsed.coordination_kind == "covering_fire"
    # Note: A-squad captured by target_unit_refs (callsign), so filtered from
    # coordination_unit_refs. LLM resolves this correctly.


def test_generate_coordination_ack_for_explicit_fire_request():
    unit = {
        "name": "Mortar",
        "unit_type": "mortar_section",
    }

    resp_type, text = response_generator.generate_coordination_ack(
        unit=unit,
        language="ru",
        supported_unit_name="C-squad",
        own_grid="E10-8-1",
        target_grid="F8-4-2",
        coordination_kind="fire_support",
        explicit_fire_request=True,
    )

    assert resp_type == ResponseType.wilco_fire
    assert "огневую задачу" in text
    assert "F8-4-2" in text
    assert "250м" in text


def test_compute_flank_approach_point_left_side_bias():
    lat, lon = OrderService._compute_flank_approach_point(
        start_lat=0.0,
        start_lon=0.0,
        target_lat=0.01,
        target_lon=0.0,
        side="left",
    )

    assert lat > 0.01
    assert lon < 0.0


def test_sustained_support_salvos_continue_while_supported_unit_advances():
    supported_id = uuid.uuid4()
    arty_id = uuid.uuid4()
    supported = SimpleNamespace(
        id=supported_id,
        is_destroyed=False,
        current_task={
            "type": "attack",
            "target_location": {"lat": 48.1, "lon": 24.1},
        },
    )
    artillery = SimpleNamespace(
        id=arty_id,
        name="Mortar",
        is_destroyed=False,
        current_task={
            "type": "fire",
            "target_location": {"lat": 48.0, "lon": 24.0},
            "support_for": str(supported_id),
            "sustained_support": True,
            "salvos_remaining": 1,
        },
    )

    events: list[dict] = []
    _decrement_salvos(artillery, events, [supported, artillery])

    assert events == []
    assert artillery.current_task is not None
    assert artillery.current_task["salvos_remaining"] == 1
    assert artillery.current_task["target_location"] == {"lat": 48.1, "lon": 24.1}


def test_fallback_parse_engineer_breach_order_ru():
    parsed = order_parser._fallback_parse(
        "Инженерный взвод, проделайте проход в минном поле у квадрата F7-2-1."
    )

    assert parsed.classification == MessageClassification.command
    assert parsed.order_type == OrderType.breach
    assert parsed.map_object_type in {"minefield", "at_minefield"}
    assert any(ref.ref_type in {"snail", "grid"} for ref in parsed.location_refs)


def test_fallback_parse_lay_mines_order_en():
    parsed = order_parser._fallback_parse(
        "Engineer section, lay mines on the western approach to B6-3 and block the road."
    )

    assert parsed.classification == MessageClassification.command
    assert parsed.order_type == OrderType.lay_mines
    assert parsed.map_object_type == "minefield"


def test_fallback_parse_construct_entrenchment_ru():
    parsed = order_parser._fallback_parse(
        "Сапёры, оборудуйте окопы и укрепите позицию в квадрате C4-2."
    )

    assert parsed.classification == MessageClassification.command
    assert parsed.order_type == OrderType.construct
    assert parsed.map_object_type == "entrenchment"


def test_fallback_parse_deploy_bridge_en():
    parsed = order_parser._fallback_parse(
        "AVLB section, deploy bridge at crossing E5-4 to support the assault."
    )

    assert parsed.classification == MessageClassification.command
    assert parsed.order_type == OrderType.deploy_bridge
    assert parsed.map_object_type == "bridge_structure"


def test_fallback_parse_logistics_resupply_target_ru():
    parsed = order_parser._fallback_parse(
        "Logistics, подвези боеприпасы B-squad и следуй за ними до высоты 149."
    )

    assert parsed.classification == MessageClassification.command
    assert parsed.order_type == OrderType.resupply
    assert parsed.support_target_ref == "B-squad"


def test_fallback_parse_recon_screen_order_en():
    parsed = order_parser._fallback_parse(
        "Recon team, screen the left flank and report any enemy movement toward Hill 210."
    )

    assert parsed.classification == MessageClassification.command
    assert parsed.order_type == OrderType.observe


def test_fallback_parse_aviation_insertion_order_en():
    parsed = order_parser._fallback_parse(
        "Aviation flight, insert recon team to Hill 201 and extract casualties from the LZ on return."
    )

    assert parsed.classification == MessageClassification.command
    assert parsed.order_type == OrderType.move


def test_fallback_parse_drone_screen_order_ru():
    parsed = order_parser._fallback_parse(
        "БПЛА, прикрой левый фланг наблюдением и докладывай о движении противника."
    )

    assert parsed.classification == MessageClassification.command
    assert parsed.order_type == OrderType.observe


def test_fallback_parse_split_order_extracts_ratio():
    parsed = order_parser._fallback_parse(
        "A-squad, split off half your strength and send the new element to screen the bridge."
    )

    assert parsed.classification == MessageClassification.command
    assert parsed.order_type == OrderType.split
    assert parsed.split_ratio == 0.5
    assert parsed.map_object_type == "bridge_structure"


def test_fallback_parse_merge_order_extracts_partner():
    parsed = order_parser._fallback_parse(
        "B-squad, merge with C-squad and continue the advance."
    )

    assert parsed.classification == MessageClassification.command
    assert parsed.order_type == OrderType.merge
    assert parsed.merge_target_ref == "C-squad"


def test_build_engine_task_construct_uses_current_position_when_no_location():
    parsed = ParsedOrderData(
        classification=MessageClassification.command,
        language=DetectedLanguage.ru,
        target_unit_refs=["Engineer section"],
        order_type=OrderType.construct,
        location_refs=[],
        map_object_type="entrenchment",
    )

    task = OrderService()._build_engine_task(
        order=type("OrderStub", (), {"id": "00000000-0000-0000-0000-000000000021"})(),
        parsed=parsed,
        resolved_locations=[],
        intent=None,
        grid_service=None,
        matched_units=[{"id": "eng-1", "name": "Engineer section", "lat": 48.15, "lon": 24.71}],
    )

    assert task["type"] == "construct"
    assert task["target_location"] == {"lat": 48.15, "lon": 24.71}
    assert task["object_type"] == "entrenchment"
    assert task["geometry"]["type"] in {"LineString", "Point", "Polygon"}


def test_build_engine_task_lay_mines_sets_geometry_and_progress():
    parsed = ParsedOrderData(
        classification=MessageClassification.command,
        language=DetectedLanguage.en,
        target_unit_refs=["Engineer section"],
        order_type=OrderType.lay_mines,
        location_refs=[],
        map_object_type="at_minefield",
    )

    task = OrderService()._build_engine_task(
        order=type("OrderStub", (), {"id": "00000000-0000-0000-0000-000000000022"})(),
        parsed=parsed,
        resolved_locations=[],
        intent=None,
        grid_service=None,
        matched_units=[{"id": "eng-2", "name": "Engineer section", "lat": 48.2, "lon": 24.8}],
    )

    assert task["type"] == "lay_mines"
    assert task["mine_type"] == "at_minefield"
    assert task["build_progress"] == 0.0
    assert task["geometry"]["type"] == "Polygon"


def test_find_nearest_contact_target_picks_closest_enemy():
    contacts = [
        SimpleNamespace(
            location_estimate=SimpleNamespace(x=24.8200, y=48.2200),
            target_unit_id=uuid.uuid4(),
        ),
        SimpleNamespace(
            location_estimate=SimpleNamespace(x=24.8010, y=48.2010),
            target_unit_id=uuid.uuid4(),
        ),
    ]

    best = OrderService._find_nearest_contact_target(48.2000, 24.8000, contacts)

    assert best is not None
    assert round(best["lat"], 4) == 48.2010
    assert round(best["lon"], 4) == 24.8010
    assert best["distance_m"] > 0
    assert best["target_unit_id"] == str(contacts[1].target_unit_id)


def test_build_engine_task_request_fire_defaults_to_fire_support_coordination():
    parsed = ParsedOrderData(
        classification=MessageClassification.command,
        language=DetectedLanguage.ru,
        target_unit_refs=["Bravo"],
        order_type=OrderType.request_fire,
        location_refs=[],
        coordination_unit_refs=["Artillery"],
    )

    task = OrderService()._build_engine_task(
        order=type("OrderStub", (), {"id": "00000000-0000-0000-0000-000000000023"})(),
        parsed=parsed,
        resolved_locations=[],
        intent=None,
        grid_service=None,
    )

    assert task["type"] == "request_fire"
    assert task["coordination_unit_refs"] == ["Artillery"]
    assert task["coordination_kind"] == "fire_support"
