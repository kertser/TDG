"""
Scenario 18: LLM Pipeline — Natural Language Variety

Tests the LLM with natural, casual, informal military communications
that don't follow strict radio protocol. Real soldiers often abbreviate,
use slang, mix languages, and use non-standard grammar.
"""
from scripts.tactical_tests.base import (
    BaseScenario, _make_unit, make_raw_order, grid_center, offset_position,
)


class LLMNaturalLanguage(BaseScenario):

    @property
    def name(self) -> str:
        return "S18: LLM — Natural Language Variety"

    @property
    def description(self) -> str:
        return (
            "Informal, casual military orders: abbreviations, slang, "
            "implied context, urgency markers. Tests robustness."
        )

    @property
    def ticks(self) -> int:
        return 5

    @property
    def category(self) -> str:
        return "llm"

    @property
    def use_llm_pipeline(self) -> bool:
        return True

    def build_units(self) -> list[dict]:
        pos = grid_center("D", 4)
        return [
            _make_unit("1st Plt", "infantry_platoon", "blue", *pos),
            _make_unit("2nd Plt", "infantry_platoon", "blue",
                       *offset_position(*pos, east_m=200)),
            _make_unit("Mortar", "mortar_section", "blue",
                       *offset_position(*pos, north_m=-300)),
            _make_unit("Tanks", "tank_platoon", "blue",
                       *offset_position(*pos, east_m=500)),
            _make_unit("Enemy", "infantry_platoon", "red",
                       *grid_center("F", 5)),
        ]

    def build_orders(self, unit_ids: dict[str, str]) -> list[dict]:
        return [
            # Urgent, short, aggressive
            make_raw_order(
                ["1st Plt"],
                "GO GO GO! Move it to E5, double time!",
                expected_classification="command",
                expected_order_type="move",
            ),
            # Russian slang/informal
            make_raw_order(
                ["2nd Plt"],
                "Второй, давай бегом к пятому квадрату, там духи засели!",
                expected_classification="command",
                expected_language="ru",
                inject_at_tick=0,
            ),
            # Implied target (enemy position)
            make_raw_order(
                ["Mortar"],
                "Миномёт, накрой их! Квадрат F5!",
                expected_classification="command",
                expected_order_type="fire",
                expected_language="ru",
                inject_at_tick=1,
            ),
            # Tank commander informal style
            make_raw_order(
                ["Tanks"],
                "Armor, roll out and smash whatever's at F5. Don't stop for anything.",
                expected_classification="command",
                expected_order_type="attack",
                inject_at_tick=1,
            ),
            # Disengage with urgency
            make_raw_order(
                ["1st Plt"],
                "Первый, отходи! Немедленно! Разрывай контакт!",
                expected_classification="command",
                expected_order_type="disengage",
                expected_language="ru",
                inject_at_tick=3,
            ),
            # Resupply request
            make_raw_order(
                ["2nd Plt"],
                "We're black on ammo. Need resupply ASAP.",
                expected_classification="command",
                expected_order_type="resupply",
                inject_at_tick=3,
            ),
        ]

    def build_assertions(self) -> list[dict]:
        return [
            {"type": "llm_not_nonsense",
             "params": {"order_index": 0},
             "description": "Urgent move order should not be unclear"},
            {"type": "llm_classification_is",
             "params": {"order_index": 0, "expected": "command"},
             "description": "GO GO GO → command"},
            {"type": "llm_not_nonsense",
             "params": {"order_index": 1},
             "description": "Russian slang order should parse"},
            {"type": "llm_classification_is",
             "params": {"order_index": 2, "expected": "command"},
             "description": "накрой их → command (fire)"},
            {"type": "llm_classification_is",
             "params": {"order_index": 3, "expected": "command"},
             "description": "Armor roll out → command"},
            {"type": "llm_classification_is",
             "params": {"order_index": 4, "expected": "command"},
             "description": "отходи! → command (disengage)"},
            {"type": "llm_not_nonsense",
             "params": {"order_index": 5},
             "description": "Resupply request should parse"},
        ]

