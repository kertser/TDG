"""
Scenario 10: Weather Effects — Storm Assault

Attack in storm conditions. Tests weather visibility/movement modifiers.
Storm: -60% visibility, -40% movement.
"""
from scripts.tactical_tests.base import (
    BaseScenario, _make_unit, make_order, grid_center, offset_position,
)


class WeatherStormAssault(BaseScenario):

    @property
    def name(self) -> str:
        return "S10: Storm Assault (Weather Effects)"

    @property
    def description(self) -> str:
        return (
            "Blue attacks Red position during a storm. "
            "Tests weather modifiers: -60% visibility, -40% movement speed. "
            "Detection ranges severely reduced, movement much slower."
        )

    @property
    def ticks(self) -> int:
        return 25

    def build_scenario_data(self) -> dict:
        data = super().build_scenario_data()
        data["environment"]["weather"] = "storm"
        data["environment"]["precipitation"] = "heavy_rain"
        data["environment"]["visibility_km"] = 1.0
        return data

    def build_units(self) -> list[dict]:
        blue_start = grid_center("C", 4)
        red_pos = grid_center("E", 5)

        return [
            _make_unit("Storm Assault 1", "infantry_platoon", "blue",
                       *blue_start, morale=0.85),
            _make_unit("Storm Assault 2", "infantry_platoon", "blue",
                       *offset_position(*blue_start, north_m=200, east_m=100),
                       morale=0.85),
            _make_unit("Red Storm Def", "infantry_platoon", "red",
                       *red_pos, morale=0.8),
        ]

    def build_orders(self, unit_ids: dict[str, str]) -> list[dict]:
        red_pos = grid_center("E", 5)
        return [
            make_order(
                ["Storm Assault 1"], "attack",
                "Attack through the storm to E5. Use weather as cover.",
                target_location={"lat": red_pos[0], "lon": red_pos[1]},
                speed="fast",
            ),
            make_order(
                ["Storm Assault 2"], "attack",
                "Support attack on E5.",
                target_location={"lat": red_pos[0] + 0.0005, "lon": red_pos[1]},
                speed="fast",
            ),
            make_order(
                ["Red Storm Def"], "defend", "Hold position.",
                side="red",
            ),
        ]

    def build_assertions(self) -> list[dict]:
        return [
            {"type": "unit_moved", "params": {"unit_name": "Storm Assault 1", "min_distance_m": 100},
             "description": "Units should move even in storm (slower)"},
            {"type": "unit_survives", "params": {"unit_name": "Storm Assault 1"},
             "description": "At least one assault platoon should survive"},
        ]

