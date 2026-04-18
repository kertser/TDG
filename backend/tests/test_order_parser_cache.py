import asyncio

from backend.schemas.order import DetectedLanguage, MessageClassification, OrderType, ParsedOrderData
from backend.services.order_parser import OrderParser, PromptBundle
from backend.services.local_triage import TriageResult


def _sample_units():
    return [
        {
            "id": "u1",
            "name": "B-squad",
            "unit_type": "infantry_squad",
            "side": "blue",
            "is_destroyed": False,
            "comms_status": "operational",
            "strength": 0.82,
            "ammo": 0.7,
            "morale": 0.88,
            "current_task": {"type": "move"},
        },
        {
            "id": "u2",
            "name": "Mortar Section",
            "unit_type": "mortar_section",
            "side": "blue",
            "is_destroyed": False,
            "comms_status": "operational",
            "strength": 0.9,
            "ammo": 0.95,
            "morale": 0.92,
            "current_task": {"type": "fire"},
        },
    ]


def test_prompt_bundle_cache_key_is_stable_for_same_inputs():
    parser = OrderParser()
    keyword_hint = ParsedOrderData(
        classification=MessageClassification.command,
        language=DetectedLanguage.en,
        target_unit_refs=["B-squad"],
        order_type=OrderType.request_fire,
        confidence=0.8,
    )

    bundle1 = parser._build_prompt_bundle(
        original_text="B-squad, request smoke on the crossing and move under concealment.",
        units=_sample_units(),
        grid_info=None,
        game_time="2026-04-17T12:00:00Z",
        model="local",
        issuer_side="blue",
        terrain_context="Terrain near friendly units:\n  - B-squad: road (E6-3, elev 171m, slope 4.0°)",
        contacts_context="Known enemy contacts (1 active):\n  - machine-gun nest at (48.1498, 24.7098), grid E6-2 [conf=90%, src=spotrep]",
        objectives_context="Mission: Seize the eastern bridge crossing.",
        friendly_status_context="Friendly forces (2 units): avg strength=86%, ammo=82%, morale=90%",
        environment_context="Environment: weather=clear, visibility=good",
        orders_context="Recent own-side orders (1):\n  - 2026-04-17T11:57:00Z: request_fire -> Mortar Section [validated] | \"Smoke the crossing\"",
        radio_context="Recent radio/chat traffic (1 msgs):\n  - 2026-04-17T11:59:00Z [UNIT_RADIO] Mortar Section -> HQ: Same target can be re-engaged with smoke.",
        reports_context="Recent operational reports (1):\n  - 2026-04-17T11:59:30Z [spotrep] from HQ: Enemy machine-gun still covers the crossing.",
        map_objects_context="Known map objects / points of interest (1):\n  - Eastern bridge (bridge_structure, category=mobility, side=neutral) at (48.1500, 24.7100)",
        keyword_hint=keyword_hint,
    )
    bundle2 = parser._build_prompt_bundle(
        original_text="B-squad, request smoke on the crossing and move under concealment.",
        units=_sample_units(),
        grid_info=None,
        game_time="2026-04-17T12:00:00Z",
        model="local",
        issuer_side="blue",
        terrain_context="Terrain near friendly units:\n  - B-squad: road (E6-3, elev 171m, slope 4.0°)",
        contacts_context="Known enemy contacts (1 active):\n  - machine-gun nest at (48.1498, 24.7098), grid E6-2 [conf=90%, src=spotrep]",
        objectives_context="Mission: Seize the eastern bridge crossing.",
        friendly_status_context="Friendly forces (2 units): avg strength=86%, ammo=82%, morale=90%",
        environment_context="Environment: weather=clear, visibility=good",
        orders_context="Recent own-side orders (1):\n  - 2026-04-17T11:57:00Z: request_fire -> Mortar Section [validated] | \"Smoke the crossing\"",
        radio_context="Recent radio/chat traffic (1 msgs):\n  - 2026-04-17T11:59:00Z [UNIT_RADIO] Mortar Section -> HQ: Same target can be re-engaged with smoke.",
        reports_context="Recent operational reports (1):\n  - 2026-04-17T11:59:30Z [spotrep] from HQ: Enemy machine-gun still covers the crossing.",
        map_objects_context="Known map objects / points of interest (1):\n  - Eastern bridge (bridge_structure, category=mobility, side=neutral) at (48.1500, 24.7100)",
        keyword_hint=keyword_hint,
    )

    assert bundle1.cache_key == bundle2.cache_key
    assert bundle1.system == bundle2.system
    assert bundle1.user == bundle2.user
    assert bundle1.user.count("Examples:") == 1


def test_local_prompt_bundle_keeps_examples_single_and_compact():
    parser = OrderParser()
    keyword_hint = ParsedOrderData(
        classification=MessageClassification.command,
        language=DetectedLanguage.en,
        target_unit_refs=["Combat engineers"],
        order_type=OrderType.breach,
        confidence=0.8,
    )

    bundle = parser._build_prompt_bundle(
        original_text="Combat engineers, breach the roadblock at E6-3 and open a lane at the bridge.",
        units=_sample_units(),
        grid_info={"columns": 10, "rows": 10, "labeling_scheme": "alphanumeric"},
        game_time="2026-04-17T12:00:00Z",
        model="local",
        issuer_side="blue",
        terrain_context="Terrain near friendly units:\n  - Combat engineers: road (E6-3, elev 171m, slope 4.0°)",
        contacts_context="Known enemy contacts (1 active):\n  - machine-gun nest at (48.1498, 24.7098), grid E6-2 [conf=90%, src=spotrep]",
        objectives_context="Mission: Seize the eastern bridge crossing and keep the lane open.",
        friendly_status_context="Friendly forces (2 units): avg strength=86%, ammo=82%, morale=90%",
        environment_context="Environment: weather=clear, visibility=good",
        orders_context="Recent own-side orders (1):\n  - 2026-04-17T11:58:00Z: breach -> Combat engineers [validated] | \"Open a lane at E6-3\"",
        radio_context="Recent radio/chat traffic (1 msgs):\n  - 2026-04-17T11:59:30Z [UNIT_RADIO] Combat engineers -> HQ: Roadblock in sight at E6-3.",
        reports_context="Recent operational reports (1):\n  - 2026-04-17T11:59:45Z [sitrep] from HQ: Crossing must be opened for follow-on forces.",
        map_objects_context="Known map objects / points of interest (2):\n  - Eastern bridge (bridge_structure, category=mobility, side=neutral) at (48.1500, 24.7100)\n  - Roadblock Alpha (roadblock, category=obstacle, side=red) at (48.1502, 24.7101)",
        keyword_hint=keyword_hint,
    )

    assert bundle.user.count("Examples:") == 1
    assert bundle.user.count('MESSAGE: "') == 2
    assert len(bundle.user) < 2200


def test_prompt_result_cache_returns_detached_copy():
    parser = OrderParser()
    parsed = ParsedOrderData(
        classification=MessageClassification.command,
        language=DetectedLanguage.en,
        target_unit_refs=["B-squad"],
        order_type=OrderType.move,
        confidence=0.84,
    )

    parser._store_cached_prompt_result("abc", parsed)
    cached = parser._get_cached_prompt_result("abc")

    assert cached is not None
    assert cached == parsed
    assert cached is not parsed

    cached.target_unit_refs.append("Mutated")
    cached_again = parser._get_cached_prompt_result("abc")
    assert cached_again is not None
    assert cached_again.target_unit_refs == ["B-squad"]


def test_parsed_order_data_coerces_null_list_fields_to_empty_lists():
    parsed = ParsedOrderData.model_validate(
        {
            "classification": "command",
            "language": "ru",
            "target_unit_refs": None,
            "order_type": "move",
            "location_refs": None,
            "coordination_unit_refs": None,
            "status_request_focus": None,
            "ambiguities": None,
            "confidence": 0.91,
        }
    )

    assert parsed.target_unit_refs == []
    assert parsed.location_refs == []
    assert parsed.coordination_unit_refs == []
    assert parsed.status_request_focus == []
    assert parsed.ambiguities == []


def test_call_llm_stops_retrying_nano_after_timeout(monkeypatch):
    parser = OrderParser()
    keyword_hint = ParsedOrderData(
        classification=MessageClassification.command,
        language=DetectedLanguage.ru,
        target_unit_refs=["Bravo"],
        order_type=OrderType.request_fire,
        confidence=0.86,
    )

    monkeypatch.setattr(
        parser,
        "_build_prompt_bundle",
        lambda **kwargs: PromptBundle(
            system="system",
            user="user",
            is_local=False,
            retrieved=None,
            cache_key="timeout-test",
        ),
    )

    class _DummyCompletions:
        def __init__(self):
            self.calls = 0

        async def create(self, **kwargs):
            self.calls += 1
            raise Exception("Request timed out.")

    completions = _DummyCompletions()
    dummy_client = type(
        "DummyClient",
        (),
        {"chat": type("DummyChat", (), {"completions": completions})()},
    )()

    result = asyncio.run(
        parser._call_llm(
            original_text="Bravo, оставайтесь на месте и наведите артиллерию.",
            units=_sample_units(),
            grid_info=None,
            game_time="2026-04-17T12:00:00Z",
            client=dummy_client,
            model="gpt-5-nano",
            issuer_side="blue",
            keyword_hint=keyword_hint,
        )
    )

    assert result is None
    assert completions.calls == 1


def test_parse_fast_paths_strong_keyword_move_without_triage_or_cloud(monkeypatch):
    parser = OrderParser()

    move_parse = ParsedOrderData(
        classification=MessageClassification.command,
        language=DetectedLanguage.ru,
        order_type=OrderType.move,
        location_refs=[
            {
                "source_text": "F6-4-7",
                "ref_type": "snail",
                "normalized": "F6-4-7",
            }
        ],
        speed="fast",
        confidence=0.85,
    )

    monkeypatch.setattr(parser, "_fallback_parse", lambda text: move_parse)
    monkeypatch.setattr("backend.services.order_parser.settings.OPENAI_API_KEY", "test-key")
    monkeypatch.setattr("backend.services.order_parser.settings.LLM_PARSING_MODE", "llm_first")

    async def _unexpected_triage(_text):
        raise AssertionError("Local triage should not run for a strong simple move command")

    async def _unexpected_llm(*args, **kwargs):
        raise AssertionError("Cloud LLM should not run for a strong simple move command")

    monkeypatch.setattr("backend.services.order_parser.local_triage.classify", _unexpected_triage)
    monkeypatch.setattr(parser, "_call_llm", _unexpected_llm)

    result = asyncio.run(
        parser.parse(
            original_text="Быстро двигайтесь в квадрат F6-4-7",
            units=_sample_units(),
            grid_info=None,
            game_time="2026-04-17T12:00:00Z",
            issuer_side="blue",
        )
    )

    assert result.order_type == OrderType.move
    assert result.speed.value == "fast"
    assert result.location_refs[0].normalized == "F6-4-7"


def test_confident_command_skips_local_triage_even_when_llm_still_needed(monkeypatch):
    parser = OrderParser()

    attack_parse = ParsedOrderData(
        classification=MessageClassification.command,
        language=DetectedLanguage.ru,
        order_type=OrderType.attack,
        confidence=0.82,
    )

    monkeypatch.setattr(parser, "_fallback_parse", lambda text: attack_parse)
    monkeypatch.setattr("backend.services.order_parser.settings.OPENAI_API_KEY", "test-key")
    monkeypatch.setattr("backend.services.order_parser.settings.LLM_PARSING_MODE", "llm_first")
    monkeypatch.setattr("backend.services.order_parser.settings.OPENAI_MODEL_NANO", "gpt-5-nano")
    monkeypatch.setattr("backend.services.order_parser.settings.OPENAI_MODEL", "gpt-5.4-mini")

    triage_calls = {"count": 0}
    llm_calls = {"count": 0}

    async def _triage(_text):
        triage_calls["count"] += 1
        return TriageResult(
            classification=MessageClassification.command,
            language=DetectedLanguage.ru,
            confidence=0.7,
        )

    async def _llm(*args, **kwargs):
        llm_calls["count"] += 1
        return attack_parse

    monkeypatch.setattr("backend.services.order_parser.local_triage.classify", _triage)
    monkeypatch.setattr(parser, "_call_llm", _llm)

    result = asyncio.run(
        parser.parse(
            original_text="Атакуйте противника",
            units=_sample_units(),
            grid_info=None,
            game_time="2026-04-17T12:00:00Z",
            issuer_side="blue",
        )
    )

    assert result.order_type == OrderType.attack
    assert triage_calls["count"] == 0
    assert llm_calls["count"] == 1
