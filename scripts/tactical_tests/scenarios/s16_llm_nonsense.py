"""
Scenario 16: LLM Pipeline — Nonsense, Unclear, and Edge Cases

Tests the LLM pipeline with garbage text, partial commands, emoji,
and ambiguous messages. These should be classified as 'unclear' or
handled gracefully without crashing.
"""
from scripts.tactical_tests.base import (
    BaseScenario, _make_unit, make_raw_order, grid_center, offset_position,
)


class LLMNonsenseOrders(BaseScenario):

    @property
    def name(self) -> str:
        return "S16: LLM — Nonsense & Edge Cases"

    @property
    def description(self) -> str:
        return (
            "Tests LLM with garbage text, partial words, emoji, "
            "profanity, unrelated topics. Should classify as 'unclear'."
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
            _make_unit("Test Unit", "infantry_platoon", "blue", *grid_center("D", 4)),
        ]

    def build_orders(self, unit_ids: dict[str, str]) -> list[dict]:
        return [
            # Pure garbage
            make_raw_order(
                ["Test Unit"],
                "asdfghjkl qwerty zxcvbnm 12345",
                expected_classification="unclear",
            ),
            # Emoji soup
            make_raw_order(
                ["Test Unit"],
                "🎉🎊🎈🎁 привет мир! 💥💥💥",
                expected_classification="unclear",
                inject_at_tick=1,
            ),
            # Completely unrelated topic
            make_raw_order(
                ["Test Unit"],
                "The weather in Paris is lovely this time of year. "
                "I recommend the croissants at the café on Rue de Rivoli.",
                expected_classification="unclear",
                inject_at_tick=2,
            ),
        ]

    def build_assertions(self) -> list[dict]:
        return [
            {"type": "llm_is_nonsense",
             "params": {"order_index": 0},
             "description": "Random keyboard mashing → unclear"},
            {"type": "llm_is_nonsense",
             "params": {"order_index": 1},
             "description": "Emoji soup → unclear"},
            {"type": "llm_is_nonsense",
             "params": {"order_index": 2},
             "description": "Tourist advice → unclear"},
        ]

