from backend.prompts.order_parser import build_optimized_local_prompt, build_user_message
from backend.prompts.tactical_doctrine import (
    get_tactical_doctrine,
    get_tactical_doctrine_excerpt,
)
from backend.schemas.order import (
    DetectedLanguage,
    MessageClassification,
    OrderType,
    ParsedOrderData,
)
from backend.services.retrieval_context import build_order_parser_context


def _sample_units():
    return [
        {
            "id": "u-eng",
            "name": "Combat engineers",
            "unit_type": "combat_engineer_section",
            "side": "blue",
            "is_destroyed": False,
            "comms_status": "operational",
            "strength": 0.95,
            "current_task": {"type": "idle"},
        },
        {
            "id": "u-mortar",
            "name": "Mortar Section",
            "unit_type": "mortar_section",
            "side": "blue",
            "is_destroyed": False,
            "comms_status": "operational",
            "strength": 0.9,
            "current_task": {"type": "fire"},
        },
        {
            "id": "u-log",
            "name": "Logistics unit",
            "unit_type": "logistics_team",
            "side": "blue",
            "is_destroyed": False,
            "comms_status": "operational",
            "strength": 1.0,
            "current_task": {"type": "resupply"},
        },
        {
            "id": "u-rifle",
            "name": "B-squad",
            "unit_type": "infantry_squad",
            "side": "blue",
            "is_destroyed": False,
            "comms_status": "operational",
            "strength": 0.82,
            "current_task": {"type": "move"},
        },
    ]


def test_doctrine_excerpt_is_targeted_and_shorter_than_full_composed_text():
    full = get_tactical_doctrine("brief", topics=["engineers", "map_objects"])
    excerpt = get_tactical_doctrine_excerpt(
        level="brief",
        topics=["engineers", "map_objects"],
        query="breach bridge roadblock open lane",
        max_passages=3,
        max_chars=800,
    )

    assert "Topic: Engineers" in excerpt
    assert "Topic: Map Objects" in excerpt
    assert len(excerpt) < len(full)


def test_breach_context_builder_prioritizes_engineer_and_map_object_data():
    parsed = ParsedOrderData(
        classification=MessageClassification.command,
        language=DetectedLanguage.en,
        target_unit_refs=["Combat engineers"],
        order_type=OrderType.breach,
        confidence=0.8,
    )

    retrieved = build_order_parser_context(
        original_text="Combat engineers, breach the roadblock at E6-3 and open a lane at the bridge.",
        parsed_hint=parsed,
        doctrine_topics=["general", "engineers", "map_objects"],
        units=_sample_units(),
        grid_info={
            "height_tops": [
                {"label": "Hill 170", "label_ru": "Высота 170", "snail_path": "E6-3", "elevation_m": 170.0}
            ]
        },
        terrain_context=(
            "Terrain in operations area:\n"
            "  - road: 22 cells\n"
            "  - forest: 10 cells\n"
            "  - marsh: 4 cells\n"
            "Terrain near friendly units:\n"
            "  - Combat engineers: road (E6-3, elev 171m, slope 4.0°)\n"
            "  - B-squad: forest (E5-9, elev 165m, slope 7.0°)"
        ),
        contacts_context=(
            "Known enemy contacts (2 active):\n"
            "  - infantry platoon at (48.1500, 24.7100), grid E6-2 [conf=80%, src=visual]\n"
            "  - tank section at (48.1800, 24.7600), grid F7-1 [conf=60%, src=report]"
        ),
        objectives_context="Mission: Seize the eastern crossing and keep the lane open.",
        friendly_status_context=(
            "Friendly forces (4 units): avg strength=92%, ammo=85%, morale=90%\n"
            "  - Combat engineers: strength=95%, ammo=90%, morale=92%, task=idle\n"
            "  - B-squad: strength=82%, ammo=70%, morale=88%, task=move"
        ),
        environment_context="Current simulation time: 2026-04-17T12:00:00Z\nEnvironment: weather=clear, visibility=good",
        orders_context=(
            "Recent own-side orders (3):\n"
            "  - 2026-04-17T11:58:00Z: breach -> Combat engineers [validated] | \"Open a lane at the eastern crossing\"\n"
            "  - 2026-04-17T11:55:00Z: move -> B-squad [executing] | \"Move to support the breach\""
        ),
        radio_context=(
            "Recent radio/chat traffic (2 msgs):\n"
            "  - 2026-04-17T11:57:00Z [UNIT_RADIO] Combat engineers -> HQ: Roadblock sighted at bridge crossing.\n"
            "  - 2026-04-17T11:58:00Z [UNIT_RADIO] B-squad -> HQ: Ready to cover the lane."
        ),
        reports_context=(
            "Recent operational reports (2):\n"
            "  - 2026-04-17T11:56:00Z [spotrep] from B-squad: Enemy observation near the bridge.\n"
            "  - 2026-04-17T11:57:00Z [sitrep] from HQ: Crossing must be opened for follow-on forces."
        ),
        map_objects_context=(
            "Known map objects / points of interest (3):\n"
            "  - Eastern bridge (bridge_structure, category=mobility, side=neutral) at (48.1500, 24.7100)\n"
            "  - Roadblock Alpha (roadblock, category=obstacle, side=red) at (48.1502, 24.7101)\n"
            "  - Supply cache (supply_cache, category=support, side=blue) at (48.1200, 24.6800)"
        ),
        is_local=True,
    )

    assert retrieved.units_for_prompt[0]["name"] == "Combat engineers"
    assert "roadblock" in retrieved.map_objects_context.lower()
    assert "bridge" in retrieved.map_objects_context.lower()
    assert "Topic: Engineers" in retrieved.doctrine_text


def test_request_fire_context_builder_prioritizes_contacts_and_reports():
    parsed = ParsedOrderData(
        classification=MessageClassification.command,
        language=DetectedLanguage.en,
        target_unit_refs=["B-squad"],
        coordination_unit_refs=["Mortar Section"],
        order_type=OrderType.request_fire,
        confidence=0.8,
    )

    retrieved = build_order_parser_context(
        original_text="B-squad, request mortar smoke on the crossing and move under concealment.",
        parsed_hint=parsed,
        doctrine_topics=["general", "fires", "recon"],
        units=_sample_units(),
        grid_info=None,
        terrain_context="Terrain in operations area:\n  - road: 22 cells\n  - forest: 10 cells",
        contacts_context=(
            "Known enemy contacts (3 active):\n"
            "  - infantry platoon at (48.1500, 24.7100), grid E6-2 [conf=80%, src=visual]\n"
            "  - machine-gun nest at (48.1498, 24.7098), grid E6-2 [conf=90%, src=spotrep]\n"
            "  - truck column at (48.1800, 24.7600), grid F7-1 [conf=40%, src=report]"
        ),
        objectives_context="Mission: Seize the eastern crossing.",
        friendly_status_context="Friendly forces (4 units): avg strength=92%, ammo=85%, morale=90%",
        environment_context="Current simulation time: 2026-04-17T12:00:00Z",
        orders_context="Recent own-side orders (1):\n  - 2026-04-17T11:55:00Z: move -> B-squad [executing] | \"Move to the crossing\"",
        radio_context="Recent radio/chat traffic (1 msgs):\n  - 2026-04-17T11:57:00Z [UNIT_RADIO] Mortar Section -> HQ: Ready for smoke mission on request.",
        reports_context=(
            "Recent operational reports (2):\n"
            "  - 2026-04-17T11:56:00Z [spotrep] from B-squad: Enemy machine-gun covering the crossing.\n"
            "  - 2026-04-17T11:57:00Z [spotrep] from HQ: Smoke recommended before movement."
        ),
        map_objects_context="Known map objects / points of interest (1):\n  - Eastern crossing (bridge_structure, category=mobility, side=neutral) at (48.1500, 24.7100)",
        is_local=False,
    )

    assert "machine-gun" in retrieved.contacts_context.lower()
    assert "smoke" in retrieved.reports_context.lower()
    assert "Mortar Section" in [u["name"] for u in retrieved.units_for_prompt]


def test_state_packet_and_continuity_hints_capture_shorthand_context_for_local_models():
    parsed = ParsedOrderData(
        classification=MessageClassification.command,
        language=DetectedLanguage.en,
        target_unit_refs=["B-squad"],
        order_type=OrderType.request_fire,
        confidence=0.78,
    )

    retrieved = build_order_parser_context(
        original_text="B-squad, continue to the bridge and hit the same target under smoke.",
        parsed_hint=parsed,
        doctrine_topics=["general", "fires", "offense"],
        units=_sample_units(),
        grid_info=None,
        terrain_context=(
            "Terrain near friendly units:\n"
            "  - B-squad: road (E6-3, elev 171m, slope 4.0°)\n"
            "  - Combat engineers: forest (E5-9, elev 165m, slope 7.0°)"
        ),
        contacts_context=(
            "Known enemy contacts (2 active):\n"
            "  - machine-gun nest at (48.1498, 24.7098), grid E6-2 [conf=90%, src=spotrep]\n"
            "  - infantry platoon at (48.1500, 24.7100), grid E6-2 [conf=80%, src=visual]"
        ),
        objectives_context="Mission: Seize the eastern bridge crossing.",
        friendly_status_context=(
            "Friendly forces (4 units): avg strength=92%, ammo=85%, morale=90%\n"
            "  - B-squad: strength=82%, ammo=70%, morale=88%, task=move\n"
            "  - Mortar Section: strength=90%, ammo=95%, morale=92%, task=fire"
        ),
        environment_context="Environment: weather=clear, visibility=good",
        orders_context=(
            "Recent own-side orders (2):\n"
            "  - 2026-04-17T11:55:00Z: move -> B-squad [executing] | \"Move to the eastern bridge\"\n"
            "  - 2026-04-17T11:57:00Z: request_fire -> Mortar Section [validated] | \"Smoke the crossing\""
        ),
        radio_context=(
            "Recent radio/chat traffic (2 msgs):\n"
            "  - 2026-04-17T11:58:00Z [UNIT_RADIO] B-squad -> HQ: Approaching the bridge crossing.\n"
            "  - 2026-04-17T11:59:00Z [UNIT_RADIO] Mortar Section -> HQ: Same target can be re-engaged with smoke."
        ),
        reports_context=(
            "Recent operational reports (1):\n"
            "  - 2026-04-17T11:59:30Z [spotrep] from HQ: Enemy machine-gun still covers the crossing."
        ),
        map_objects_context=(
            "Known map objects / points of interest (2):\n"
            "  - Eastern bridge (bridge_structure, category=mobility, side=neutral) at (48.1500, 24.7100)\n"
            "  - Smoke screen Bravo (smoke, category=effect, side=blue) at (48.1499, 24.7099)"
        ),
        is_local=True,
    )

    assert "Parser state packet:" in retrieved.state_packet
    assert "order_hint=request_fire" in retrieved.state_packet
    assert "machine-gun nest @E6-2" in retrieved.state_packet
    assert "Eastern bridge[bridge_structure]" in retrieved.state_packet
    assert "continue_from=" in retrieved.continuity_hints
    assert "same_target=" in retrieved.continuity_hints
    assert "No relevant" not in retrieved.state_packet


def test_local_prompt_prefers_state_packet_over_verbose_section_dump():
    system, user_context = build_optimized_local_prompt(
        units=_sample_units(),
        order_type_hint="breach",
        language_hint="en",
        doctrine_excerpt="### Topic: Engineers\n- Open a lane through obstacles.",
        state_packet=(
            "Parser state packet:\n"
            "task: class=command; lang=en; order_hint=breach; targets=Combat engineers; object=roadblock\n"
            "objects: Roadblock Alpha[roadblock]@E6-3; Eastern bridge[bridge_structure]@E6-3\n"
            "Recent facts: order=breach -> Combat engineers [validated]"
        ),
        continuity_hints="Continuity hints:\n  - continue_from=breach -> Combat engineers [validated]",
        contacts_summary="No relevant enemy contact context retrieved.",
        objectives_summary="Mission: Open the crossing.",
        terrain_summary="Terrain: road, forest",
        history_summary="Recent orders: breach",
        map_objects_summary="Known objects: bridge",
        environment_summary="Weather: clear",
        friendly_status_summary="Friendlies: ready",
    )

    assert "support_target_ref" in system
    assert "Parser state packet:" in user_context
    assert "continue_from=breach" in user_context
    assert "No relevant enemy contact context retrieved." not in user_context
    assert user_context.count("Examples:") == 1
    assert user_context.count('MESSAGE: "') == 1
    assert len(user_context) < 900


def test_build_user_message_uses_dynamic_few_shot_selection():
    msg = build_user_message(
        "B-squad, merge with C-squad and continue as one element.",
        order_type_hint="merge",
        language_hint="en",
        max_examples=3,
    )

    assert msg.count('MESSAGE: "') == 4
    assert "merge with C-squad and continue as one element" in msg
    assert "Here are examples of correct parsing" not in msg


def test_build_user_message_can_skip_examples_when_context_already_contains_them():
    msg = build_user_message(
        "Combat engineers, breach the roadblock at E6-3.",
        order_type_hint="breach",
        language_hint="en",
        context_block='Examples:\nMESSAGE: "demo"\nPARSED: {"classification":"command","language":"en"}',
        include_examples=False,
    )

    assert msg.count("Examples:") == 1
    assert msg.count('MESSAGE: "') == 2
