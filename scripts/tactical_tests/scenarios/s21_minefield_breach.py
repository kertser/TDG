"""
Scenario 21: Minefield Breach

Infantry discovers minefield, halts. Engineers breach, infantry continues.
Tests: minefield_avoidance event, engineering mechanics, obstacle effects.

Inspired by: Alamein minefield breach, Gulf War obstacle clearing.
"""
from scripts.tactical_tests.base import (
    BaseScenario, _make_unit, make_order, grid_center, offset_position,
)


class MinefieldBreach(BaseScenario):

    @property
    def name(self) -> str:
        return "S21: Minefield Breach"

    @property
    def description(self) -> str:
        return (
            "Infantry halts at discovered minefield. Engineers breach. "
            "Tests minefield_avoidance, engineering interactions, obstacle effects."
        )

    @property
    def ticks(self) -> int:
        return 25

    @property
    def category(self) -> str:
        return "tactical"

    def build_units(self) -> list[dict]:
        blue_start = grid_center("C", 4)
        eng_pos = offset_position(*blue_start, north_m=-500, east_m=100)

        return [
            _make_unit("Lead Plt", "infantry_platoon", "blue",
                       *blue_start, morale=0.9),
            _make_unit("Breach Team", "engineer_platoon", "blue",
                       *eng_pos, morale=0.95),
            _make_unit("Follow Plt", "infantry_platoon", "blue",
                       *offset_position(*blue_start, north_m=-300),
                       morale=0.9),
            # Red behind the minefield
            _make_unit("Red Def", "infantry_platoon", "red",
                       *grid_center("E", 5), morale=0.8),
        ]

    def build_orders(self, unit_ids: dict[str, str]) -> list[dict]:
        target = grid_center("E", 5)
        mine_area = grid_center("D", 5)

        return [
            make_order(
                ["Lead Plt"], "attack",
                "Advance to E5. Engage enemy.",
                target_location={"lat": target[0], "lon": target[1]},
                speed="fast",
            ),
            make_order(
                ["Breach Team"], "move",
                "Move to minefield area D5 and breach.",
                target_location={"lat": mine_area[0], "lon": mine_area[1]},
                speed="fast",
            ),
            make_order(
                ["Follow Plt"], "move",
                "Follow lead platoon.",
                target_location={"lat": target[0], "lon": target[1]},
                speed="slow",
                inject_at_tick=5,
            ),
            make_order(
                ["Red Def"], "defend",
                "Defend behind minefield.",
                side="red",
            ),
        ]

    def build_map_objects(self) -> list[dict]:
        """Create a minefield between Blue and Red."""
        mine_center = grid_center("D", 5)
        return [
            {
                "object_type": "minefield",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [mine_center[1] - 0.003, mine_center[0] - 0.001],
                        [mine_center[1] + 0.003, mine_center[0] - 0.001],
                        [mine_center[1] + 0.003, mine_center[0] + 0.001],
                        [mine_center[1] - 0.003, mine_center[0] + 0.001],
                        [mine_center[1] - 0.003, mine_center[0] - 0.001],
                    ]],
                },
                "label": "Enemy Minefield",
                "properties": {},
            },
        ]

    def build_assertions(self) -> list[dict]:
        return [
            {"type": "unit_moved", "params": {"unit_name": "Lead Plt", "min_distance_m": 200},
             "description": "Lead platoon should advance toward minefield"},
            {"type": "unit_survives", "params": {"unit_name": "Breach Team"},
             "description": "Engineers should survive"},
            {"type": "unit_survives", "params": {"unit_name": "Lead Plt"},
             "description": "Lead platoon should survive"},
            {"type": "unit_survives", "params": {"unit_name": "Follow Plt"},
             "description": "Follow platoon should survive"},
        ]



