"""
Scenario 15: LLM Pipeline — Complex Multi-Verb Orders

Tests LLM with complex, multi-verb commands that should trigger full LLM model.
These are ambiguous enough that keyword parsing alone will fail.
"""
from scripts.tactical_tests.base import (
    BaseScenario, _make_unit, make_raw_order, grid_center, offset_position,
)


class LLMComplexOrders(BaseScenario):

    @property
    def name(self) -> str:
        return "S15: LLM — Complex Multi-Verb Orders"

    @property
    def description(self) -> str:
        return (
            "Complex orders: multi-verb, mixed ack+command, standby vs fire, "
            "coordination orders. Should trigger nano/full LLM model."
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
            _make_unit("Alpha", "infantry_platoon", "blue", *pos),
            _make_unit("Bravo", "infantry_platoon", "blue",
                       *offset_position(*pos, east_m=200)),
            _make_unit("Mortars", "mortar_section", "blue",
                       *offset_position(*pos, north_m=-400)),
            _make_unit("Red Plt", "infantry_platoon", "red",
                       *grid_center("F", 5)),
        ]

    def build_orders(self, unit_ids: dict[str, str]) -> list[dict]:
        return [
            # Multi-verb: advance + eliminate → should be attack
            make_raw_order(
                ["Alpha"],
                "Alpha, advance to F8-1-9. Eliminate all enemy forces inbound. "
                "Set up a defensive perimeter after securing the area.",
                expected_classification="command",
                expected_order_type="attack",
                expected_language="en",
            ),
            # Mixed ack + command: should classify as command
            make_raw_order(
                ["Bravo"],
                "Вас понял. Атакуйте позицию противника в квадрате Е5. Быстро.",
                expected_classification="command",
                expected_order_type="attack",
                expected_language="ru",
            ),
            # Standby order: should be observe, NOT fire
            make_raw_order(
                ["Mortars"],
                "Mortars, get ready for fire support on request. Stand by.",
                expected_classification="command",
                expected_order_type="observe",
                expected_language="en",
            ),
            # Coordination order mentioning fire support — should NOT be fire
            make_raw_order(
                ["Alpha"],
                "Lead the attack on F5. Coordinate artillery support with Mortars.",
                expected_classification="command",
                expected_order_type="attack",
                expected_language="en",
                inject_at_tick=1,
            ),
        ]

    def build_assertions(self) -> list[dict]:
        return [
            {"type": "llm_classification_is",
             "params": {"order_index": 0, "expected": "command"},
             "description": "Multi-verb advance+eliminate → command"},
            {"type": "llm_order_type_is",
             "params": {"order_index": 0, "expected": "attack"},
             "description": "advance+eliminate → attack (not just move)"},
            {"type": "llm_classification_is",
             "params": {"order_index": 1, "expected": "command"},
             "description": "Mixed ack+command → command (not ack)"},
            {"type": "llm_order_type_is",
             "params": {"order_index": 1, "expected": "attack"},
             "description": "Вас понял + Атакуйте → attack"},
            {"type": "llm_order_type_is",
             "params": {"order_index": 2, "expected": "observe"},
             "description": "Stand by for fire → observe (NOT fire)"},
            {"type": "llm_classification_is",
             "params": {"order_index": 3, "expected": "command"},
             "description": "Coordination order → command"},
        ]

