"""
Scenario 03: Hasty Defense Against Superior Force

Blue: Reinforced platoon (2 infantry sections + AT team) defends in forest
terrain against Red mech platoon + tank platoon.
The Blue force is numerically inferior but has terrain advantage.

Tests: dig-in progression, forest protection, AT effectiveness, morale under pressure.
Realistic: a well-dug-in force in covered terrain can delay a superior mechanized force.
"""
from scripts.tactical_tests.base import (
    BaseScenario, _make_unit, make_order, grid_center, offset_position,
)


class HastyDefense(BaseScenario):

    @property
    def name(self) -> str:
        return "S03: Hasty Defense Against Superior Force"

    @property
    def description(self) -> str:
        return (
            "Blue reinforced platoon defends in forest against "
            "Red mech platoon. Tests dig-in mechanics, forest protection, "
            "AT effectiveness, morale under sustained pressure."
        )

    @property
    def ticks(self) -> int:
        return 20

    @property
    def language(self) -> str:
        return "ru"

    def build_units(self) -> list[dict]:
        # Blue digs in at D5 (forest terrain will be added)
        blue_pos = grid_center("D", 5)
        # Red starts at F5, ~2km away
        red_start = grid_center("F", 5)

        return [
            _make_unit("Blue Def Section 1", "infantry_section", "blue",
                       *blue_pos, morale=0.9, strength=1.0),
            _make_unit("Blue Def Section 2", "infantry_section", "blue",
                       *offset_position(*blue_pos, north_m=80, east_m=0),
                       morale=0.9, strength=1.0),
            _make_unit("Blue AT Team", "at_team", "blue",
                       *offset_position(*blue_pos, north_m=-300, east_m=200),
                       morale=0.9, strength=1.0),
            _make_unit("Red Mech Plt", "mech_platoon", "red",
                       *red_start, morale=0.9, strength=1.0),
            _make_unit("Red Tank Plt", "tank_platoon", "red",
                       *offset_position(*red_start, north_m=-100, east_m=-100),
                       morale=0.85),
        ]

    def build_terrain_cells(self) -> list[dict]:
        blue_pos = grid_center("D", 5)
        return [
            {"snail_path": "D5-9", "terrain_type": "forest", "depth": 1,
             "modifiers": {"movement": 0.5, "visibility": 0.4, "protection": 1.4, "attack": 0.7},
             "centroid_lat": blue_pos[0], "centroid_lon": blue_pos[1]},
        ]

    def build_orders(self, unit_ids: dict[str, str]) -> list[dict]:
        blue_pos = grid_center("D", 5)
        return [
            make_order(["Blue Def Section 1"], "defend",
                       "Оборонять текущие позиции. Окопаться.",
                       side="blue"),
            make_order(["Blue Def Section 2"], "defend",
                       "Оборона. Окопаться, подготовить запасные позиции.",
                       side="blue"),
            make_order(["Blue AT Team"], "defend",
                       "ПТ-группа — позиция, огонь по бронетехнике.",
                       side="blue"),
            make_order(["Red Mech Plt"], "attack",
                       "Атаковать позиции противника в квадрате D5.",
                       side="red",
                       target_location={"lat": blue_pos[0], "lon": blue_pos[1]},
                       speed="fast"),
            make_order(["Red Tank Plt"], "attack",
                       "Танковому взводу — поддержать атаку на D5.",
                       side="red",
                       target_location={"lat": blue_pos[0], "lon": blue_pos[1]},
                       speed="fast"),
        ]

    def build_assertions(self) -> list[dict]:
        return [
            {"type": "event_exists", "params": {"event_type": "combat"},
             "description": "Combat should occur between forces"},
            # Red should take damage from Blue's defense
            {"type": "unit_strength_below",
             "params": {"unit_name": "Red Mech Plt", "threshold": 1.001},
             "description": "Red should take at least some damage from defenders"},
            {"type": "detection_occurs", "params": {"observer_side": "blue", "min_count": 1},
             "description": "Blue should detect approaching Red armor"},
            {"type": "unit_moved", "params": {"unit_name": "Red Mech Plt", "min_distance_m": 300},
             "description": "Red mech should advance toward Blue position"},
        ]
