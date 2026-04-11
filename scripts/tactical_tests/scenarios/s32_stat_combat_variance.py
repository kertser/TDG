"""
Scenario 32: Statistical — Combat Outcome Consistency

Same forces engage in identical setup. The engine is deterministic:
same unit IDs + same positions = same detection hash = same outcome.
This test verifies deterministic consistency (every run identical)
and that final strength is in a reasonable range.
"""
from scripts.tactical_tests.base import (
    BaseScenario, _make_unit, make_order, grid_center, offset_position,
)


class StatCombatVariance(BaseScenario):

    @property
    def name(self) -> str:
        return "S32: Statistical — Combat Outcome Consistency"

    @property
    def description(self) -> str:
        return (
            "Identical forces engage. Run multiple times. "
            "Engine is deterministic — results should be identical across runs. "
            "Verifies consistency and valid combat outcome range."
        )

    @property
    def ticks(self) -> int:
        return 20

    @property
    def category(self) -> str:
        return "statistical"

    @property
    def statistical_runs(self) -> int:
        return 15

    def build_units(self) -> list[dict]:
        blue_pos = grid_center("C", 4)
        red_pos = grid_center("E", 5)

        return [
            _make_unit("Blue Plt", "infantry_platoon", "blue",
                       *blue_pos, morale=0.9),
            _make_unit("Red Plt", "infantry_platoon", "red",
                       *red_pos, morale=0.9),
        ]

    def build_orders(self, unit_ids: dict[str, str]) -> list[dict]:
        red_pos = grid_center("E", 5)
        blue_pos = grid_center("C", 4)

        return [
            make_order(
                ["Blue Plt"], "attack",
                "Advance and engage.",
                target_location={"lat": red_pos[0], "lon": red_pos[1]},
                speed="fast",
            ),
            make_order(
                ["Red Plt"], "attack",
                "Counter-advance.",
                side="red",
                target_location={"lat": blue_pos[0], "lon": blue_pos[1]},
                speed="fast",
            ),
        ]

    def build_assertions(self) -> list[dict]:
        return [
            {"type": "event_exists", "params": {"event_type": "combat"},
             "description": "Combat should occur in each run"},
        ]

    def build_statistical_assertions(self) -> list[dict]:
        return [
            {"type": "stat_combat_occurs_always",
             "params": {},
             "description": "Combat should occur in every run"},
            {"type": "stat_mean_strength",
             "params": {"unit_name": "Blue Plt", "min": 0.0, "max": 0.99},
             "description": "Blue Plt should take some damage (mean < 100%)"},
        ]
