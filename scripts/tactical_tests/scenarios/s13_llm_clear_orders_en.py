"""
Scenario 13: LLM Pipeline — Clear English Orders

Tests the LLM order-parsing pipeline with unambiguous English military commands.
Each order should be parsed correctly by the keyword tier (high confidence).

Orders tested:
  1. Clear move order with grid reference
  2. Attack order with formation
  3. Defend order
  4. Fire order with salvos
  5. Observe/standby order
"""
from scripts.tactical_tests.base import (
    BaseScenario, _make_unit, make_raw_order, grid_center, offset_position,
)


class LLMClearOrdersEN(BaseScenario):

    @property
    def name(self) -> str:
        return "S13: LLM — Clear English Orders"

    @property
    def description(self) -> str:
        return (
            "Tests LLM pipeline with clear, unambiguous English military commands. "
            "Move, attack, defend, fire, observe — all should parse correctly."
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
            _make_unit("Alpha Platoon", "infantry_platoon", "blue", *pos),
            _make_unit("Bravo Platoon", "infantry_platoon", "blue",
                       *offset_position(*pos, east_m=200)),
            _make_unit("Mortar Section", "mortar_section", "blue",
                       *offset_position(*pos, north_m=-300)),
            _make_unit("Recon Team", "recon_team", "blue",
                       *offset_position(*pos, north_m=300)),
            _make_unit("Red Platoon", "infantry_platoon", "red",
                       *grid_center("F", 5)),
        ]

    def build_orders(self, unit_ids: dict[str, str]) -> list[dict]:
        return [
            make_raw_order(
                ["Alpha Platoon"],
                "Alpha Platoon, move to grid E5 at fast speed.",
                expected_classification="command",
                expected_order_type="move",
                expected_language="en",
            ),
            make_raw_order(
                ["Bravo Platoon"],
                "Bravo Platoon — attack enemy position at F5. Wedge formation.",
                expected_classification="command",
                expected_order_type="attack",
                expected_language="en",
            ),
            make_raw_order(
                ["Mortar Section"],
                "Mortar Section, fire three salvos on grid F5.",
                expected_classification="command",
                expected_order_type="fire",
                expected_language="en",
            ),
            make_raw_order(
                ["Recon Team"],
                "Recon Team, observe sector E5 through F6. Report all contacts.",
                expected_classification="command",
                expected_order_type="observe",
                expected_language="en",
            ),
        ]

    def build_assertions(self) -> list[dict]:
        return [
            {"type": "llm_classification_is",
             "params": {"order_index": 0, "expected": "command"},
             "description": "Move order should be classified as command"},
            {"type": "llm_order_type_is",
             "params": {"order_index": 0, "expected": "move"},
             "description": "Should detect move order type"},
            {"type": "llm_language_detected",
             "params": {"order_index": 0, "expected": "en"},
             "description": "Should detect English language"},
            {"type": "llm_classification_is",
             "params": {"order_index": 1, "expected": "command"},
             "description": "Attack order should be classified as command"},
            {"type": "llm_order_type_is",
             "params": {"order_index": 1, "expected": "attack"},
             "description": "Should detect attack order type"},
            {"type": "llm_classification_is",
             "params": {"order_index": 2, "expected": "command"},
             "description": "Fire order should be classified as command"},
            {"type": "llm_order_type_is",
             "params": {"order_index": 2, "expected": "fire"},
             "description": "Should detect fire order type"},
            {"type": "llm_classification_in",
             "params": {"order_index": 3, "expected": ["command", "status_request"]},
             "description": "Observe order should be classified as command or status_request"},
            {"type": "llm_order_type_in",
             "params": {"order_index": 3, "expected": ["observe", "report_status"]},
             "description": "Should detect observe or report_status order type"},
        ]

