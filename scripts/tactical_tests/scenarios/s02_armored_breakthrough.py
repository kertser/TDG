"""
Scenario 02: Armored Breakthrough with Flanking Maneuver

Blue: Tank company + Mech platoon on road, Recon team flanks through forest.
Red: AT team + Infantry platoon defend road junction.

Tests: terrain speed differentials, flanking, vehicle vs infantry, weapon ranges.
"""
from scripts.tactical_tests.base import (
    BaseScenario, _make_unit, make_order, grid_center, offset_position,
)


class ArmoredBreakthrough(BaseScenario):

    @property
    def name(self) -> str:
        return "S02: Armored Breakthrough with Flanking"

    @property
    def description(self) -> str:
        return (
            "Blue armor advances along road while recon flanks through forest. "
            "Red AT team and infantry defend road junction. Tests terrain speed, "
            "flanking detection, and combined vehicle-infantry combat."
        )

    @property
    def ticks(self) -> int:
        return 20

    def build_units(self) -> list[dict]:
        road_start = grid_center("B", 4)
        flank_start = grid_center("A", 5)
        red_pos = grid_center("E", 5)

        return [
            _make_unit("Blue Tank Co", "tank_company", "blue",
                       *road_start, morale=0.95),
            _make_unit("Blue Mech Plt", "mech_platoon", "blue",
                       *offset_position(*road_start, north_m=-200, east_m=100),
                       morale=0.9),
            _make_unit("Blue Recon", "recon_team", "blue",
                       *flank_start, morale=0.95),
            # Red
            _make_unit("Red AT Team", "at_team", "red",
                       *offset_position(*red_pos, north_m=50, east_m=-100),
                       morale=0.8),
            _make_unit("Red Infantry", "infantry_platoon", "red",
                       *red_pos, morale=0.8),
        ]

    def build_orders(self, unit_ids: dict[str, str]) -> list[dict]:
        red_pos = grid_center("E", 5)
        flank_target = offset_position(*red_pos, north_m=200, east_m=300)

        return [
            make_order(
                ["Blue Tank Co"], "attack",
                "Tank company — advance along the main road to grid E5. Engage any enemy.",
                target_location={"lat": red_pos[0], "lon": red_pos[1]},
                speed="fast",
            ),
            make_order(
                ["Blue Mech Plt"], "attack",
                "Mech platoon — follow tanks, support the assault.",
                target_location={"lat": red_pos[0], "lon": red_pos[1]},
                speed="fast",
            ),
            make_order(
                ["Blue Recon"], "move",
                "Recon team — flank north through the forest, reach position north of E5.",
                target_location={"lat": flank_target[0], "lon": flank_target[1]},
                speed="slow",
            ),
            make_order(
                ["Red Infantry"], "defend",
                "Defend the road junction. Do not withdraw.",
                side="red",
            ),
        ]

    def build_terrain_cells(self) -> list[dict]:
        return [
            {"snail_path": "B4-2", "terrain_type": "road", "depth": 1,
             "centroid_lat": grid_center("B", 4)[0], "centroid_lon": grid_center("B", 4)[1]},
            {"snail_path": "C4-2", "terrain_type": "road", "depth": 1,
             "centroid_lat": grid_center("C", 4)[0], "centroid_lon": grid_center("C", 4)[1]},
            {"snail_path": "D5-2", "terrain_type": "road", "depth": 1,
             "centroid_lat": grid_center("D", 5)[0], "centroid_lon": grid_center("D", 5)[1]},
            {"snail_path": "A5-9", "terrain_type": "forest", "depth": 1,
             "centroid_lat": grid_center("A", 5)[0], "centroid_lon": grid_center("A", 5)[1]},
            {"snail_path": "B5-3", "terrain_type": "forest", "depth": 1,
             "centroid_lat": grid_center("B", 5)[0], "centroid_lon": grid_center("B", 5)[1]},
        ]

    def build_assertions(self) -> list[dict]:
        return [
            {"type": "unit_moved", "params": {"unit_name": "Blue Tank Co", "min_distance_m": 500},
             "description": "Tanks should advance significantly along road"},
            {"type": "unit_moved", "params": {"unit_name": "Blue Recon", "min_distance_m": 200},
             "description": "Recon should move through forest flank"},
            {"type": "event_exists", "params": {"event_type": "combat"},
             "description": "Combat should occur at Red position"},
            {"type": "detection_occurs", "params": {"observer_side": "blue", "min_count": 1},
             "description": "Blue should detect Red defenders"},
            {"type": "unit_survives", "params": {"unit_name": "Blue Tank Co"},
             "description": "Tank company should survive (heavy armor)"},
        ]

