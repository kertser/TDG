"""
Scenario 20: River Crossing with AVLB Bridge

Infantry halts at water terrain (no bridge), AVLB deploys bridge,
infantry crosses. Tests: water_blocked event, bridge deployment,
movement across water with bridge present.

Inspired by: Assault river crossings (Dnipro, Rhine).
"""
from scripts.tactical_tests.base import (
    BaseScenario, _make_unit, make_order, grid_center, offset_position,
)


class RiverCrossing(BaseScenario):

    @property
    def name(self) -> str:
        return "S20: River Crossing with AVLB"

    @property
    def description(self) -> str:
        return (
            "Infantry approaches river, halts (water terrain), AVLB deploys "
            "bridge, infantry crosses. Tests water_blocked, bridge mechanics."
        )

    @property
    def ticks(self) -> int:
        return 25

    @property
    def category(self) -> str:
        return "tactical"

    def build_units(self) -> list[dict]:
        # Blue starts 400m south of river, river at D5, objective 400m north of river
        river = grid_center("D", 5)
        blue_start = offset_position(*river, north_m=-400)
        far_side = offset_position(*river, north_m=400)

        return [
            _make_unit("Assault Plt", "infantry_platoon", "blue",
                       *blue_start, morale=0.9),
            _make_unit("AVLB", "avlb_vehicle", "blue",
                       *offset_position(*blue_start, north_m=-100, east_m=100)),
            _make_unit("Red Guard", "infantry_section", "red",
                       *far_side, morale=0.8),
        ]

    def build_orders(self, unit_ids: dict[str, str]) -> list[dict]:
        river = grid_center("D", 5)
        far_side = offset_position(*river, north_m=400)

        return [
            make_order(
                ["Assault Plt"], "move",
                "Advance to river crossing at D5.",
                target_location={"lat": far_side[0], "lon": far_side[1]},
                speed="fast",
            ),
            make_order(
                ["AVLB"], "move",
                "Move to river crossing point.",
                target_location={"lat": river[0], "lon": river[1]},
                speed="fast",
            ),
            make_order(
                ["Red Guard"], "defend",
                "Defend the far bank.",
                side="red",
            ),
        ]

    def build_terrain_cells(self) -> list[dict]:
        river = grid_center("D", 5)
        return [
            {"snail_path": "D4-2", "terrain_type": "open", "depth": 1,
             "centroid_lat": offset_position(*river, north_m=-400)[0],
             "centroid_lon": river[1]},
            # River
            {"snail_path": "D5-2", "terrain_type": "water", "depth": 1,
             "centroid_lat": river[0], "centroid_lon": river[1]},
            {"snail_path": "D5-5", "terrain_type": "water", "depth": 1,
             "centroid_lat": river[0], "centroid_lon": river[1] + 0.002},
            {"snail_path": "D5-8", "terrain_type": "water", "depth": 1,
             "centroid_lat": river[0], "centroid_lon": river[1] - 0.002},
            # Far side
            {"snail_path": "D6-6", "terrain_type": "open", "depth": 1,
             "centroid_lat": offset_position(*river, north_m=400)[0],
             "centroid_lon": river[1]},
        ]

    def build_assertions(self) -> list[dict]:
        return [
            {"type": "unit_moved", "params": {"unit_name": "Assault Plt", "min_distance_m": 200},
             "description": "Infantry should advance toward river"},
            {"type": "unit_moved", "params": {"unit_name": "AVLB", "min_distance_m": 100},
             "description": "AVLB should move to river crossing point"},
            {"type": "unit_survives", "params": {"unit_name": "Assault Plt"},
             "description": "Assault platoon should survive"},
        ]


