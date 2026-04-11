"""
Scenario 22: Urban Clearing Operations

Mech + infantry clear urban area with entrenchments and pillboxes.
High protection, low visibility. Slow, attritional combat.

Inspired by: Battle of Fallujah, Mosul, urban warfare doctrine.
"""
from scripts.tactical_tests.base import (
    BaseScenario, _make_unit, make_order, grid_center, offset_position,
)


class UrbanClearing(BaseScenario):

    @property
    def name(self) -> str:
        return "S22: Urban Clearing Operations"

    @property
    def description(self) -> str:
        return (
            "Blue mech + infantry clear urban terrain with entrenchments. "
            "Tests urban combat modifiers (protection×1.5, visibility×0.5), "
            "close-quarters fighting, high suppression."
        )

    @property
    def ticks(self) -> int:
        return 30

    @property
    def category(self) -> str:
        return "tactical"

    @property
    def language(self) -> str:
        return "ru"

    def build_units(self) -> list[dict]:
        assault_base = grid_center("C", 5)
        urban_center = grid_center("E", 5)

        return [
            _make_unit("Штурм-1", "infantry_platoon", "blue",
                       *assault_base, morale=0.9),
            _make_unit("Штурм-2", "infantry_platoon", "blue",
                       *offset_position(*assault_base, north_m=200, east_m=100),
                       morale=0.9),
            _make_unit("Мехвзвод", "mech_platoon", "blue",
                       *offset_position(*assault_base, north_m=-100, east_m=200),
                       morale=0.9),
            _make_unit("Миномёт", "mortar_section", "blue",
                       *offset_position(*assault_base, north_m=-400),
                       morale=0.9),
            # Red defending urban area
            _make_unit("Гарнизон-1", "infantry_platoon", "red",
                       *urban_center, morale=0.85),
            _make_unit("Гарнизон-2", "infantry_section", "red",
                       *offset_position(*urban_center, north_m=100, east_m=-80),
                       morale=0.85),
            _make_unit("ПТ-расчёт", "at_team", "red",
                       *offset_position(*urban_center, north_m=-50, east_m=150),
                       morale=0.8),
        ]

    def build_orders(self, unit_ids: dict[str, str]) -> list[dict]:
        urban_center = grid_center("E", 5)

        return [
            make_order(
                ["Миномёт"], "fire",
                "Подавить противника в городе. Квадрат Е5.",
                target_location={"lat": urban_center[0], "lon": urban_center[1]},
                salvos=3,
            ),
            make_order(
                ["Штурм-1"], "attack",
                "Штурм городского квартала. Построение цепью.",
                target_location={"lat": urban_center[0], "lon": urban_center[1]},
                speed="slow", formation="line",
                inject_at_tick=3,
            ),
            make_order(
                ["Штурм-2"], "attack",
                "Атака с фланга. Клин.",
                target_location={"lat": urban_center[0] + 0.0005, "lon": urban_center[1] + 0.0005},
                speed="slow", formation="wedge",
                inject_at_tick=3,
            ),
            make_order(
                ["Мехвзвод"], "attack",
                "Мехвзвод — поддержать штурм огнём.",
                target_location={"lat": urban_center[0], "lon": urban_center[1]},
                speed="slow",
                inject_at_tick=5,
            ),
            make_order(
                ["Гарнизон-1"], "defend",
                "Оборонять город до последнего.",
                side="red",
            ),
            make_order(
                ["Гарнизон-2"], "defend",
                "Держать фланг.",
                side="red",
            ),
        ]

    def build_terrain_cells(self) -> list[dict]:
        uc = grid_center("E", 5)
        return [
            {"snail_path": "E5-9", "terrain_type": "urban", "depth": 1,
             "centroid_lat": uc[0], "centroid_lon": uc[1]},
            {"snail_path": "E5-2", "terrain_type": "urban", "depth": 1,
             "centroid_lat": uc[0] + 0.002, "centroid_lon": uc[1]},
            {"snail_path": "E5-8", "terrain_type": "urban", "depth": 1,
             "centroid_lat": uc[0], "centroid_lon": uc[1] - 0.003},
            {"snail_path": "E5-4", "terrain_type": "urban", "depth": 1,
             "centroid_lat": uc[0], "centroid_lon": uc[1] + 0.003},
        ]

    def build_map_objects(self) -> list[dict]:
        uc = grid_center("E", 5)
        return [
            {
                "object_type": "entrenchment",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [
                        [uc[1] - 0.002, uc[0]],
                        [uc[1] + 0.002, uc[0]],
                    ],
                },
                "label": "Urban Defense Line",
            },
            {
                "object_type": "pillbox",
                "geometry": {"type": "Point", "coordinates": [uc[1] + 0.002, uc[0] + 0.001]},
                "label": "AT Strongpoint",
            },
        ]

    def build_assertions(self) -> list[dict]:
        return [
            {"type": "event_exists", "params": {"event_type": "combat"},
             "description": "Urban combat should occur"},
            {"type": "unit_moved", "params": {"unit_name": "Штурм-1", "min_distance_m": 200},
             "description": "Assault force should advance into city"},
            {"type": "event_count_min", "params": {"event_type": "combat", "count": 3},
             "description": "Sustained urban fighting"},
            {"type": "unit_strength_below",
             "params": {"unit_name": "Гарнизон-1", "threshold": 0.95},
             "description": "Urban defenders should take damage"},
            {"type": "unit_survives", "params": {"unit_name": "Мехвзвод"},
             "description": "Mech platoon should survive (armor protection in urban)"},
        ]

