"""
Scenario 27: Golan Heights — Outnumbered Defense (1973 inspired)

Small force with AT teams defends elevated terrain against superior
armor assault from below. Elevation advantage critical.

Historical: Yom Kippur War, October 1973 — Israeli 7th Armored Brigade
held the Golan Heights against 5:1 Syrian armor superiority.
"""
from scripts.tactical_tests.base import (
    BaseScenario, _make_unit, make_order, grid_center, offset_position,
)


class GolanHeightsDefense(BaseScenario):

    @property
    def name(self) -> str:
        return "S27: Golan Heights — Outnumbered Defense"

    @property
    def description(self) -> str:
        return (
            "Small Blue force with AT teams defends high ground against "
            "3:1 Red armor. Elevation advantage crucial. Tests: height "
            "combat bonus, AT vs tanks, defensive dig-in."
        )

    @property
    def ticks(self) -> int:
        return 30

    @property
    def category(self) -> str:
        return "historical"

    def build_units(self) -> list[dict]:
        heights = grid_center("D", 6)
        valley = grid_center("F", 4)

        return [
            # Blue on heights (small force)
            _make_unit("Blue Tank Plt", "tank_platoon", "blue",
                       *heights, morale=0.95),
            _make_unit("Blue AT 1", "at_team", "blue",
                       *offset_position(*heights, north_m=100, east_m=-200),
                       morale=0.95),
            _make_unit("Blue AT 2", "at_team", "blue",
                       *offset_position(*heights, north_m=-100, east_m=200),
                       morale=0.95),
            _make_unit("Blue OP", "observation_post", "blue",
                       *offset_position(*heights, north_m=300)),
            # Red in valley (3:1 superiority)
            _make_unit("Red Tank Co 1", "tank_company", "red",
                       *valley, morale=0.85),
            _make_unit("Red Tank Co 2", "tank_company", "red",
                       *offset_position(*valley, north_m=200, east_m=-200),
                       morale=0.85),
            _make_unit("Red Mech Plt", "mech_platoon", "red",
                       *offset_position(*valley, north_m=-200, east_m=100),
                       morale=0.8),
        ]

    def build_orders(self, unit_ids: dict[str, str]) -> list[dict]:
        heights = grid_center("D", 6)

        return [
            make_order(["Blue Tank Plt"], "defend",
                       "Hold the heights. Engage at maximum range.",
                       ),
            make_order(["Blue AT 1"], "defend",
                       "AT position alpha — hull-down, engage enemy armor.",
                       ),
            make_order(["Blue AT 2"], "defend",
                       "AT position bravo — crossfire with alpha.",
                       ),
            make_order(["Blue OP"], "observe",
                       "Observe and direct fire.",
                       ),
            make_order(
                ["Red Tank Co 1"], "attack",
                "Assault the heights.",
                side="red",
                target_location={"lat": heights[0], "lon": heights[1]},
                speed="fast",
            ),
            make_order(
                ["Red Tank Co 2"], "attack",
                "Flanking assault on the heights.",
                side="red",
                target_location={
                    "lat": heights[0] - 0.001,
                    "lon": heights[1] + 0.001,
                },
                speed="fast",
            ),
            make_order(
                ["Red Mech Plt"], "attack",
                "Follow tanks up the slope.",
                side="red",
                target_location={"lat": heights[0], "lon": heights[1]},
                speed="fast",
                inject_at_tick=5,
            ),
        ]

    def build_terrain_cells(self) -> list[dict]:
        return [
            {"snail_path": "D6-9", "terrain_type": "open", "depth": 1,
             "centroid_lat": grid_center("D", 6)[0], "centroid_lon": grid_center("D", 6)[1]},
            {"snail_path": "E5-9", "terrain_type": "open", "depth": 1,
             "centroid_lat": grid_center("E", 5)[0], "centroid_lon": grid_center("E", 5)[1]},
            {"snail_path": "F4-9", "terrain_type": "desert", "depth": 1,
             "centroid_lat": grid_center("F", 4)[0], "centroid_lon": grid_center("F", 4)[1]},
        ]

    def build_elevation_cells(self) -> list[dict]:
        return [
            {"snail_path": "D6-9", "depth": 1, "elevation_m": 200,
             "slope_deg": 10, "centroid_lat": grid_center("D", 6)[0],
             "centroid_lon": grid_center("D", 6)[1]},
            {"snail_path": "E5-9", "depth": 1, "elevation_m": 100,
             "slope_deg": 20, "centroid_lat": grid_center("E", 5)[0],
             "centroid_lon": grid_center("E", 5)[1]},
            {"snail_path": "F4-9", "depth": 1, "elevation_m": 30,
             "slope_deg": 5, "centroid_lat": grid_center("F", 4)[0],
             "centroid_lon": grid_center("F", 4)[1]},
        ]

    def build_assertions(self) -> list[dict]:
        return [
            {"type": "event_exists", "params": {"event_type": "combat"},
             "description": "Armored engagement should occur"},
            {"type": "unit_survives", "params": {"unit_name": "Blue OP"},
             "description": "OP on heights should survive (observer only)"},
            {"type": "detection_occurs", "params": {"observer_side": "blue", "min_count": 1},
             "description": "Blue detects attacking armor from heights"},
            {"type": "unit_strength_below",
             "params": {"unit_name": "Red Tank Co 1", "threshold": 1.001},
             "description": "Red armor should take at least some damage"},
            {"type": "event_count_min", "params": {"event_type": "combat", "count": 3},
             "description": "Defensive fire from heights"},
        ]


