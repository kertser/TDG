"""
Scenario 08: Night Operations

Meeting engagement at night (23:00). Tests night visibility modifier (×0.3),
NVG advantage for equipped units, reduced detection ranges.
"""
from scripts.tactical_tests.base import (
    BaseScenario, _make_unit, make_order, grid_center, offset_position,
)


class NightOperations(BaseScenario):

    @property
    def name(self) -> str:
        return "S08: Night Operations"

    @property
    def description(self) -> str:
        return (
            "Engagement at 23:00 (night). Tests night visibility ×0.3, "
            "NVG-equipped units have advantage, severely reduced detection ranges. "
            "Mech/recon with NVG should detect infantry without NVG first."
        )

    @property
    def ticks(self) -> int:
        return 20

    def build_scenario_data(self) -> dict:
        data = super().build_scenario_data()
        data["environment"]["time_of_day"] = "night"
        data["environment"]["start_time"] = "2024-06-15T23:00:00Z"
        return data

    def build_units(self) -> list[dict]:
        blue_start = grid_center("C", 4)
        # Position Red at E5 (the center) so Blue will reliably detect them
        # when approaching. At night with NVG (range ~1170m), detection at
        # ~500m distance should have high probability (~90%).
        red_start = grid_center("E", 5)

        return [
            # Blue has NVG-equipped mech
            _make_unit("Blue Mech", "mech_platoon", "blue",
                       *blue_start, morale=0.9),
            _make_unit("Blue Infantry", "infantry_platoon", "blue",
                       *offset_position(*blue_start, north_m=200, east_m=100),
                       morale=0.85),
            # Red has no NVG — positioned at the objective
            _make_unit("Red PLT", "infantry_platoon", "red",
                       *red_start, morale=0.8),
            _make_unit("Red Section", "infantry_section", "red",
                       *offset_position(*red_start, north_m=-150, east_m=-100),
                       morale=0.8),
        ]

    def build_orders(self, unit_ids: dict[str, str]) -> list[dict]:
        center = grid_center("E", 5)
        return [
            make_order(
                ["Blue Mech"], "attack",
                "Night assault on enemy position. Use NVG advantage.",
                target_location={"lat": center[0], "lon": center[1]},
                speed="slow",  # cautious night advance
            ),
            make_order(
                ["Blue Infantry"], "attack",
                "Follow mech platoon. Maintain close formation.",
                target_location={"lat": center[0] - 0.0005, "lon": center[1]},
                speed="slow",
            ),
            make_order(
                ["Red PLT"], "defend", "Defend positions.", side="red",
            ),
        ]

    def build_assertions(self) -> list[dict]:
        return [
            {"type": "unit_moved", "params": {"unit_name": "Blue Mech", "min_distance_m": 200},
             "description": "Blue mech should advance (slower at night)"},
            {"type": "detection_occurs", "params": {"observer_side": "blue", "min_count": 1},
             "description": "Blue with NVG should still detect Red at night"},
            {"type": "unit_survives", "params": {"unit_name": "Blue Mech"},
             "description": "NVG-equipped mech should survive night engagement"},
        ]

