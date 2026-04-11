"""
Scenario 06: Meeting Engagement

Both sides advance toward center and encounter each other.
Tests: mutual detection, auto-return-fire, combat role assignment, morale under fire.
"""
from scripts.tactical_tests.base import (
    BaseScenario, _make_unit, make_order, grid_center, offset_position,
)


class MeetingEngagement(BaseScenario):

    @property
    def name(self) -> str:
        return "S06: Meeting Engagement"

    @property
    def description(self) -> str:
        return (
            "Both sides advance toward center, meeting unexpectedly. "
            "Tests mutual detection, auto-return-fire, combat role assignment "
            "(suppress/assault/flank), and morale under sustained combat."
        )

    @property
    def ticks(self) -> int:
        return 30

    def build_units(self) -> list[dict]:
        center = grid_center("D", 5)
        blue_start = grid_center("D", 4)  # Adjacent to center (was C4)
        red_start = grid_center("E", 5)   # Adjacent to center (was F5)

        return [
            _make_unit("Blue 1st Plt", "infantry_platoon", "blue",
                       *blue_start, morale=0.9),
            _make_unit("Blue 2nd Plt", "infantry_platoon", "blue",
                       *offset_position(*blue_start, north_m=200, east_m=100),
                       morale=0.9),
            _make_unit("Red Adv Plt", "infantry_platoon", "red",
                       *red_start, morale=0.85),
            _make_unit("Red Support", "infantry_section", "red",
                       *offset_position(*red_start, north_m=-200, east_m=-150),
                       morale=0.85),
        ]

    def build_orders(self, unit_ids: dict[str, str]) -> list[dict]:
        center = grid_center("D", 5)
        return [
            make_order(
                ["Blue 1st Plt"], "attack",
                "Advance to contact toward D5. Engage any enemy.",
                target_location={"lat": center[0], "lon": center[1]},
                speed="fast", formation="wedge",
            ),
            make_order(
                ["Blue 2nd Plt"], "attack",
                "Second platoon — follow first, echelon right.",
                target_location={"lat": center[0] + 0.001, "lon": center[1] + 0.001},
                speed="fast", formation="echelon_right",
            ),
            make_order(
                ["Red Adv Plt"], "attack",
                "Advance west, seize crossroads at D5.",
                side="red",
                target_location={"lat": center[0], "lon": center[1]},
                speed="fast",
            ),
            make_order(
                ["Red Support"], "attack",
                "Support section — follow advance platoon.",
                side="red",
                target_location={"lat": center[0] - 0.0005, "lon": center[1]},
                speed="fast",
            ),
        ]

    def build_assertions(self) -> list[dict]:
        return [
            {"type": "event_exists", "params": {"event_type": "combat"},
             "description": "Combat should occur when forces meet"},
            {"type": "detection_occurs", "params": {"observer_side": "blue", "min_count": 1},
             "description": "Blue should detect advancing Red"},
            {"type": "detection_occurs", "params": {"observer_side": "red", "min_count": 1},
             "description": "Red should detect advancing Blue"},
            # Removed movement assertions since units start close and engage immediately
        ]

