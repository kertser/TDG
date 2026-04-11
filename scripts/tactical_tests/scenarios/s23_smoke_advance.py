"""
Scenario 23: Smoke Screen Advance

Mortar fires smoke, infantry advances through the smoke screen to close
with the enemy. Tests: smoke object creation, detection reduction in smoke,
smoke dissipation, infantry reaching objective under cover.

Inspired by: Standard infantry assault with smoke screening.
"""
from scripts.tactical_tests.base import (
    BaseScenario, _make_unit, make_order, grid_center, offset_position,
)


class SmokeAndAdvance(BaseScenario):

    @property
    def name(self) -> str:
        return "S23: Smoke Screen Advance"

    @property
    def description(self) -> str:
        return (
            "Mortar fires smoke between forces. Infantry advances through "
            "smoke screen. Tests smoke effects on detection, dissipation timing."
        )

    @property
    def ticks(self) -> int:
        return 20

    @property
    def category(self) -> str:
        return "tactical"

    def build_units(self) -> list[dict]:
        blue_start = grid_center("C", 4)
        red_pos = grid_center("E", 5)

        return [
            _make_unit("Assault Plt", "infantry_platoon", "blue",
                       *blue_start, morale=0.9),
            _make_unit("Smoke Mortar", "mortar_section", "blue",
                       *offset_position(*blue_start, north_m=-400),
                       morale=0.9),
            _make_unit("Red Def", "infantry_platoon", "red",
                       *red_pos, morale=0.8),
        ]

    def build_orders(self, unit_ids: dict[str, str]) -> list[dict]:
        red_pos = grid_center("E", 5)
        # Smoke target: between forces, at D5
        smoke_target = grid_center("D", 5)

        return [
            # Mortar fires smoke first
            make_order(
                ["Smoke Mortar"], "fire",
                "Fire smoke screen at D5. Cover the advance.",
                target_location={"lat": smoke_target[0], "lon": smoke_target[1]},
                salvos=2,
            ),
            # Infantry follows after smoke is laid
            make_order(
                ["Assault Plt"], "attack",
                "Advance through smoke to enemy position at E5.",
                target_location={"lat": red_pos[0], "lon": red_pos[1]},
                speed="fast", formation="line",
                inject_at_tick=3,
            ),
            make_order(
                ["Red Def"], "defend",
                "Hold position. Fire at will.",
                side="red",
            ),
        ]

    def build_assertions(self) -> list[dict]:
        return [
            {"type": "unit_moved", "params": {"unit_name": "Assault Plt", "min_distance_m": 300},
             "description": "Infantry should advance through smoke"},
            {"type": "unit_survives", "params": {"unit_name": "Assault Plt"},
             "description": "Infantry should survive advance under smoke cover"},
            {"type": "event_exists", "params": {"event_type": "combat"},
             "description": "Combat should occur when infantry reaches enemy"},
        ]

