"""
Scenario 17: LLM Pipeline — Status Reports and Acknowledgments

Tests message classification for non-command messages:
  - Status requests ("доложите обстановку")
  - Acknowledgments ("так точно", "roger")
  - Status reports ("находимся в районе...")
"""
from scripts.tactical_tests.base import (
    BaseScenario, _make_unit, make_raw_order, grid_center, offset_position,
)


class LLMStatusAndAcks(BaseScenario):

    @property
    def name(self) -> str:
        return "S17: LLM — Status Reports & Acknowledgments"

    @property
    def description(self) -> str:
        return (
            "Tests non-command messages: status requests, acknowledgments, "
            "status reports in both Russian and English."
        )

    @property
    def ticks(self) -> int:
        return 3

    @property
    def category(self) -> str:
        return "llm"

    @property
    def use_llm_pipeline(self) -> bool:
        return True

    def build_units(self) -> list[dict]:
        return [
            _make_unit("Alpha", "infantry_platoon", "blue", *grid_center("D", 4)),
            _make_unit("Bravo", "infantry_platoon", "blue",
                       *offset_position(*grid_center("D", 4), east_m=200)),
        ]

    def build_orders(self, unit_ids: dict[str, str]) -> list[dict]:
        return [
            # Status request in Russian
            make_raw_order(
                ["Alpha"],
                "Альфа, доложите обстановку.",
                expected_classification="status_request",
                expected_language="ru",
            ),
            # Status request in English
            make_raw_order(
                ["Bravo"],
                "Bravo, report your status and position.",
                expected_classification="status_request",
                expected_language="en",
                inject_at_tick=0,
            ),
            # Pure acknowledgment in Russian
            make_raw_order(
                ["Alpha"],
                "Так точно, вас понял.",
                expected_classification="acknowledgment",
                expected_language="ru",
                inject_at_tick=1,
            ),
            # Pure acknowledgment in English
            make_raw_order(
                ["Bravo"],
                "Roger that, wilco.",
                expected_classification="acknowledgment",
                expected_language="en",
                inject_at_tick=1,
            ),
            # Status report (unit reporting in)
            make_raw_order(
                ["Alpha"],
                "Находимся в районе D4. Потерь нет, боеготовность полная.",
                expected_classification="status_report",
                expected_language="ru",
                inject_at_tick=2,
            ),
        ]

    def build_assertions(self) -> list[dict]:
        return [
            {"type": "llm_classification_is",
             "params": {"order_index": 0, "expected": "status_request"},
             "description": "доложите обстановку → status_request"},
            {"type": "llm_classification_is",
             "params": {"order_index": 1, "expected": "status_request"},
             "description": "report your status → status_request"},
            {"type": "llm_classification_is",
             "params": {"order_index": 2, "expected": "acknowledgment"},
             "description": "так точно → acknowledgment"},
            {"type": "llm_classification_is",
             "params": {"order_index": 3, "expected": "acknowledgment"},
             "description": "roger wilco → acknowledgment"},
            {"type": "llm_classification_is",
             "params": {"order_index": 4, "expected": "status_report"},
             "description": "Находимся в районе → status_report"},
        ]

