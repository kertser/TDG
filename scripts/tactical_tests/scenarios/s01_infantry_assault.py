"""
Scenario 01: Infantry Assault on Fortified Position (Combined Arms)

Blue Force: 3 infantry platoons + mortar section + HQ attack a fortified Red position.
Red Force: 1 infantry platoon in entrenchment + AT team in pillbox + observation post.

Tests:
  - Combined arms combat (infantry + mortar fire support)
  - Artillery auto-support (mortar supports attacking infantry)
  - Entrenchment/fortification protection bonuses
  - Suppression mechanics
  - Morale effects under sustained fire
  - Orders in Russian (bilingual test)

Map: Reims area. Blue starts SW, Red defends center.
"""
from scripts.tactical_tests.base import (
    BaseScenario, _make_unit, make_order, grid_center, offset_position,
)


class InfantryAssault(BaseScenario):

    @property
    def name(self) -> str:
        return "S01: Infantry Assault on Fortified Position"

    @property
    def description(self) -> str:
        return (
            "Blue reinforced company (3 plt + mortar) assaults Red platoon "
            "in prepared defensive positions with AT support. Tests combined arms, "
            "artillery coordination, fortification protection, suppression."
        )

    @property
    def ticks(self) -> int:
        return 25

    @property
    def language(self) -> str:
        return "ru"

    def build_units(self) -> list[dict]:
        # Red defends around D5 (center of grid)
        red_pos = grid_center("D", 5)
        # Blue starts from B3 area (south-west)
        blue_base = grid_center("B", 3)

        return [
            # ── Blue Force ──
            _make_unit("Blue HQ", "headquarters", "blue",
                       *offset_position(*blue_base, north_m=-200, east_m=0),
                       morale=0.95),
            _make_unit("1st Platoon", "infantry_platoon", "blue",
                       *offset_position(*blue_base, north_m=100, east_m=-150),
                       parent_name="Blue HQ"),
            _make_unit("2nd Platoon", "infantry_platoon", "blue",
                       *offset_position(*blue_base, north_m=100, east_m=150),
                       parent_name="Blue HQ"),
            _make_unit("3rd Platoon", "infantry_platoon", "blue",
                       *offset_position(*blue_base, north_m=-50, east_m=0),
                       parent_name="Blue HQ"),
            _make_unit("Blue Mortars", "mortar_section", "blue",
                       *offset_position(*blue_base, north_m=-400, east_m=0),
                       parent_name="Blue HQ"),

            # ── Red Force ──
            _make_unit("Red PLT", "infantry_platoon", "red",
                       *red_pos, morale=0.8,
                       capabilities={"has_atgm": False}),
            _make_unit("Red AT Team", "at_team", "red",
                       *offset_position(*red_pos, north_m=100, east_m=200),
                       morale=0.75),
            _make_unit("Red OP", "observation_post", "red",
                       *offset_position(*red_pos, north_m=300, east_m=-100),
                       morale=0.7),
        ]

    def build_orders(self, unit_ids: dict[str, str]) -> list[dict]:
        red_pos = grid_center("D", 5)

        return [
            # Tick 0: All platoons advance to attack Red position
            make_order(
                ["1st Platoon"], "attack",
                "Первому взводу — атаковать позицию противника в квадрате Д5. Движение быстрым темпом, "
                "построение цепью.",
                side="blue",
                target_location={"lat": red_pos[0], "lon": red_pos[1]},
                speed="fast", formation="line",
            ),
            make_order(
                ["2nd Platoon"], "attack",
                "Второму взводу — атаковать правым флангом. Построение клином.",
                side="blue",
                target_location={"lat": red_pos[0] + 0.0005, "lon": red_pos[1] + 0.001},
                speed="fast", formation="wedge",
            ),
            make_order(
                ["3rd Platoon"], "move",
                "Третьему взводу — выдвинуться в район С4 как резерв.",
                side="blue",
                target_location={
                    "lat": grid_center("C", 4)[0],
                    "lon": grid_center("C", 4)[1],
                },
                speed="slow",
            ),
            # Tick 0: Mortars fire on Red position
            make_order(
                ["Blue Mortars"], "fire",
                "Миномётному расчёту — огонь по квадрату Д5. Три залпа.",
                side="blue",
                target_location={"lat": red_pos[0], "lon": red_pos[1]},
                salvos=3,
            ),
            # Tick 5: Red platoon defends
            make_order(
                ["Red PLT"], "defend",
                "Оборонять занимаемые позиции. Огонь по готовности.",
                side="red",
                inject_at_tick=0,
            ),
            # Tick 10: Commit reserve
            make_order(
                ["3rd Platoon"], "attack",
                "Третий взвод — в атаку! Развить успех первого и второго взводов.",
                side="blue",
                target_location={"lat": red_pos[0], "lon": red_pos[1]},
                speed="fast",
                inject_at_tick=10,
            ),
        ]

    def build_map_objects(self) -> list[dict]:
        """Red has entrenchments and a pillbox."""
        red_pos = grid_center("D", 5)
        return [
            {
                "object_type": "entrenchment",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [
                        [red_pos[1] - 0.001, red_pos[0] - 0.0003],
                        [red_pos[1] + 0.001, red_pos[0] + 0.0003],
                    ],
                },
                "label": "Red Main Trench Line",
                "properties": {},
            },
            {
                "object_type": "pillbox",
                "geometry": {
                    "type": "Point",
                    "coordinates": [red_pos[1] + 0.002, red_pos[0] + 0.001],
                },
                "label": "Red Pillbox (AT position)",
                "properties": {},
            },
        ]

    def build_terrain_cells(self) -> list[dict]:
        """Some forest around the approach, open fields near Red."""
        return [
            {"snail_path": "C4-5", "terrain_type": "forest", "depth": 1,
             "centroid_lat": grid_center("C", 4)[0], "centroid_lon": grid_center("C", 4)[1]},
            {"snail_path": "D5-9", "terrain_type": "open", "depth": 1,
             "centroid_lat": grid_center("D", 5)[0], "centroid_lon": grid_center("D", 5)[1]},
        ]

    def build_assertions(self) -> list[dict]:
        return [
            {
                "type": "event_exists",
                "params": {"event_type": "combat"},
                "description": "Combat should occur between Blue attackers and Red defenders",
            },
            {
                "type": "event_exists",
                "params": {"event_type": "movement"},
                "description": "Blue units should move toward Red position",
            },
            {
                "type": "unit_survives",
                "params": {"unit_name": "Blue HQ"},
                "description": "Blue HQ should survive (rear position)",
            },
            {
                "type": "unit_strength_below",
                "params": {"unit_name": "Red PLT", "threshold": 0.9},
                "description": "Red platoon should take damage from Blue assault",
            },
            {
                "type": "unit_moved",
                "params": {"unit_name": "1st Platoon", "min_distance_m": 200},
                "description": "1st Platoon should advance toward Red",
            },
            {
                "type": "detection_occurs",
                "params": {"observer_side": "blue", "min_count": 1},
                "description": "Blue should detect Red defenders",
            },
        ]

