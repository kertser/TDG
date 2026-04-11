"""
Scenario 29: Stalingrad — Urban Attrition (Mamayev Kurgan inspired)

Close-range urban combat between weakened forces. Both sides with
moderate-low morale, lots of suppression, building-by-building fighting.

Historical: Battle of Stalingrad, Sept-Nov 1942 — brutal urban fighting,
especially around Mamayev Kurgan hill and the factory district.
"""
from scripts.tactical_tests.base import (
    BaseScenario, _make_unit, make_order, grid_center, offset_position,
)


class StalingradUrban(BaseScenario):

    @property
    def name(self) -> str:
        return "S29: Stalingrad — Urban Attrition"

    @property
    def description(self) -> str:
        return (
            "Dense urban combat at close range. Both sides depleted, "
            "low morale. Building-by-building fighting. High suppression, "
            "limited movement. Inspired by Mamayev Kurgan."
        )

    @property
    def ticks(self) -> int:
        return 30

    @property
    def category(self) -> str:
        return "historical"

    @property
    def language(self) -> str:
        return "ru"

    def build_units(self) -> list[dict]:
        # Close quarters — forces start only 400m apart
        blue_pos = grid_center("D", 5)
        red_pos = offset_position(*blue_pos, north_m=400, east_m=100)

        return [
            # Soviet defenders (depleted but determined)
            _make_unit("Рота Павлова", "infantry_platoon", "blue",
                       *blue_pos, morale=0.7, strength=0.7),
            _make_unit("Штурмгруппа", "infantry_section", "blue",
                       *offset_position(*blue_pos, north_m=100, east_m=-50),
                       morale=0.75, strength=0.8),
            _make_unit("Снайпер", "sniper_team", "blue",
                       *offset_position(*blue_pos, north_m=50, east_m=150),
                       morale=0.85),
            # German attackers (also tired)
            _make_unit("Stoßtrupp 1", "infantry_platoon", "red",
                       *red_pos, morale=0.75, strength=0.8),
            _make_unit("Stoßtrupp 2", "infantry_section", "red",
                       *offset_position(*red_pos, north_m=-50, east_m=150),
                       morale=0.7, strength=0.75),
            _make_unit("MG-Trupp", "infantry_squad", "red",
                       *offset_position(*red_pos, north_m=100, east_m=-100),
                       morale=0.7, strength=0.85),
        ]

    def build_orders(self, unit_ids: dict[str, str]) -> list[dict]:
        blue_pos = grid_center("D", 5)

        return [
            make_order(["Рота Павлова"], "defend",
                       "Стоять насмерть! Ни шагу назад!",
                       ),
            make_order(["Штурмгруппа"], "defend",
                       "Оборонять дом. Огонь по готовности.",
                       ),
            make_order(["Снайпер"], "observe",
                       "Снайпер — работать по целям.",
                       ),
            make_order(
                ["Stoßtrupp 1"], "attack",
                "Storm the building!",
                side="red",
                target_location={"lat": blue_pos[0], "lon": blue_pos[1]},
                speed="fast",
            ),
            make_order(
                ["Stoßtrupp 2"], "attack",
                "Flank left through the rubble.",
                side="red",
                target_location={"lat": blue_pos[0] - 0.0003, "lon": blue_pos[1] - 0.0005},
                speed="slow",
            ),
            make_order(
                ["MG-Trupp"], "attack",
                "Provide covering fire.",
                side="red",
                target_location={"lat": blue_pos[0] + 0.0002, "lon": blue_pos[1]},
                speed="slow",
                inject_at_tick=2,
            ),
        ]

    def build_terrain_cells(self) -> list[dict]:
        blue_pos = grid_center("D", 5)
        red_pos = offset_position(*blue_pos, north_m=400, east_m=100)
        return [
            {"snail_path": "D5-9", "terrain_type": "urban", "depth": 1,
             "centroid_lat": blue_pos[0], "centroid_lon": blue_pos[1]},
            {"snail_path": "D5-2", "terrain_type": "urban", "depth": 1,
             "centroid_lat": red_pos[0], "centroid_lon": red_pos[1]},
        ]

    def build_assertions(self) -> list[dict]:
        return [
            {"type": "event_exists", "params": {"event_type": "combat"},
             "description": "Close-range urban combat should occur"},
            {"type": "event_count_min", "params": {"event_type": "combat", "count": 5},
             "description": "Sustained brutal fighting in rubble"},
            {"type": "unit_strength_below",
             "params": {"unit_name": "Stoßtrupp 1", "threshold": 1.001},
             "description": "Attacking Germans should take at least some casualties"},
            {"type": "event_count_min", "params": {"event_type": "combat", "count": 5},
             "description": "Prolonged close-quarters urban battle"},
        ]


