"""
Scenario 24: Defense in Depth — Three Lines

Forward security, main defense, reserve. Red attacks through.
Forward units disengage on contact, main defense holds, reserve counters.

Inspired by: Soviet defense doctrine, Kursk salient defense.
"""
from scripts.tactical_tests.base import (
    BaseScenario, _make_unit, make_order, grid_center, offset_position,
)


class DefenseInDepth(BaseScenario):

    @property
    def name(self) -> str:
        return "S24: Defense in Depth — Three Lines"

    @property
    def description(self) -> str:
        return (
            "Three defensive lines: forward security → main defense → reserve. "
            "Forward disengages on contact, main holds, reserve counters. "
            "Tests disengage timing, defense dig-in, coordinated withdrawal."
        )

    @property
    def ticks(self) -> int:
        return 35

    @property
    def category(self) -> str:
        return "tactical"

    @property
    def language(self) -> str:
        return "ru"

    def build_units(self) -> list[dict]:
        # Forward: E5, Main: D5, Reserve: C4
        fwd_pos = grid_center("E", 5)
        main_pos = grid_center("D", 5)
        reserve_pos = grid_center("C", 4)
        # Red starts at G5
        red_start = grid_center("G", 5)

        return [
            # Blue Force — three lines
            _make_unit("Blue HQ", "headquarters", "blue",
                       *offset_position(*reserve_pos, north_m=-200)),
            _make_unit("Дозор", "infantry_section", "blue",
                       *fwd_pos, morale=0.9, parent_name="Blue HQ"),
            _make_unit("Основной-1", "infantry_platoon", "blue",
                       *main_pos, morale=0.9, parent_name="Blue HQ"),
            _make_unit("Основной-2", "infantry_platoon", "blue",
                       *offset_position(*main_pos, north_m=150, east_m=100),
                       morale=0.9, parent_name="Blue HQ"),
            _make_unit("Резерв", "infantry_platoon", "blue",
                       *reserve_pos, morale=0.95, parent_name="Blue HQ"),
            _make_unit("ПТ-взвод", "at_team", "blue",
                       *offset_position(*main_pos, north_m=-100, east_m=-200),
                       parent_name="Blue HQ"),
            # Red Force — attacking
            _make_unit("Red Advance", "mech_platoon", "red",
                       *red_start, morale=0.9),
            _make_unit("Red Follow", "infantry_platoon", "red",
                       *offset_position(*red_start, north_m=-200, east_m=-100),
                       morale=0.9),
            _make_unit("Red Support", "mortar_team", "red",
                       *offset_position(*red_start, north_m=-400),
                       morale=0.85),
        ]

    def build_orders(self, unit_ids: dict[str, str]) -> list[dict]:
        main_pos = grid_center("D", 5)
        fwd_pos = grid_center("E", 5)

        return [
            # Forward security observes
            make_order(
                ["Дозор"], "defend",
                "Наблюдать и доложить. При контакте — отход.",
            ),
            # Main defense digs in
            make_order(
                ["Основной-1"], "defend",
                "Окопаться. Оборонять основную позицию.",
            ),
            make_order(
                ["Основной-2"], "defend",
                "Оборона правого фланга. Окопаться.",
            ),
            # Reserve waits
            make_order(
                ["Резерв"], "defend",
                "Резерв. Готовность к контратаке.",
            ),
            # Red attacks
            make_order(
                ["Red Advance"], "attack",
                "Attack enemy positions.",
                side="red",
                target_location={"lat": fwd_pos[0], "lon": fwd_pos[1]},
                speed="fast",
            ),
            make_order(
                ["Red Follow"], "attack",
                "Follow and support.",
                side="red",
                target_location={"lat": main_pos[0], "lon": main_pos[1]},
                speed="fast",
            ),
            make_order(
                ["Red Support"], "fire",
                "Fire on enemy positions.",
                side="red",
                target_location={"lat": main_pos[0], "lon": main_pos[1]},
                salvos=5,
                inject_at_tick=3,
            ),
            # Forward security disengages after contact (tick 8)
            make_order(
                ["Дозор"], "disengage",
                "Отход! Разрыв контакта! Отходить к основной линии!",
                inject_at_tick=8,
            ),
            # Reserve counterattacks (tick 20)
            make_order(
                ["Резерв"], "attack",
                "Контратака! Восстановить переднюю линию!",
                target_location={"lat": fwd_pos[0], "lon": fwd_pos[1]},
                speed="fast", formation="wedge",
                inject_at_tick=20,
            ),
        ]

    def build_terrain_cells(self) -> list[dict]:
        main_pos = grid_center("D", 5)
        return [
            {"snail_path": "D5-9", "terrain_type": "forest", "depth": 1,
             "centroid_lat": main_pos[0], "centroid_lon": main_pos[1]},
            {"snail_path": "E5-9", "terrain_type": "open", "depth": 1,
             "centroid_lat": grid_center("E", 5)[0], "centroid_lon": grid_center("E", 5)[1]},
        ]

    def build_map_objects(self) -> list[dict]:
        main_pos = grid_center("D", 5)
        return [
            {
                "object_type": "entrenchment",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [
                        [main_pos[1] - 0.002, main_pos[0]],
                        [main_pos[1] + 0.002, main_pos[0]],
                    ],
                },
                "label": "Main Defense Line",
            },
        ]

    def build_assertions(self) -> list[dict]:
        return [
            {"type": "event_exists", "params": {"event_type": "combat"},
             "description": "Combat should occur across defense lines"},
            {"type": "unit_survives", "params": {"unit_name": "Blue HQ"},
             "description": "HQ in rear should survive"},
            {"type": "unit_survives", "params": {"unit_name": "Резерв"},
             "description": "Reserve counterattack force should survive"},
            {"type": "unit_moved", "params": {"unit_name": "Red Advance", "min_distance_m": 500},
             "description": "Red mech should advance through forward line"},
            {"type": "unit_moved", "params": {"unit_name": "Резерв", "min_distance_m": 200},
             "description": "Reserve should counterattack"},
            {"type": "detection_occurs", "params": {"observer_side": "blue", "min_count": 1},
             "description": "Forward security should detect Red advance"},
        ]


