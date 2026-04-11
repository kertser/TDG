"""
Scenario 19: Fire and Maneuver — Coordinated Platoon Attack

Two platoons execute fire-and-maneuver: one suppresses while the other flanks.
Mortar provides preparatory fire. Tests combat role assignment
(suppress/flank/assault), phased orders, coordinated attack.

Inspired by: standard NATO fire-and-maneuver doctrine.
"""
from scripts.tactical_tests.base import (
    BaseScenario, _make_unit, make_order, grid_center, offset_position,
)


class FireAndManeuver(BaseScenario):

    @property
    def name(self) -> str:
        return "S19: Fire and Maneuver — Coordinated Platoon Attack"

    @property
    def description(self) -> str:
        return (
            "Two platoons coordinate: suppression element fixes enemy, "
            "maneuver element flanks. Mortar preps. Tests combat role "
            "assignment and fire-maneuver coordination."
        )

    @property
    def ticks(self) -> int:
        return 30

    @property
    def category(self) -> str:
        return "tactical"

    def build_units(self) -> list[dict]:
        blue_base = grid_center("C", 4)
        red_pos = grid_center("E", 5)

        return [
            _make_unit("Blue HQ", "headquarters", "blue",
                       *offset_position(*blue_base, north_m=-300), morale=0.95),
            # Support-by-fire element
            _make_unit("Support Plt", "infantry_platoon", "blue",
                       *offset_position(*blue_base, north_m=100, east_m=-100),
                       parent_name="Blue HQ"),
            # Maneuver element — flanks from the north
            _make_unit("Maneuver Plt", "infantry_platoon", "blue",
                       *offset_position(*blue_base, north_m=300, east_m=-300),
                       parent_name="Blue HQ"),
            # Mortar for prep fire
            _make_unit("Mortar Sec", "mortar_section", "blue",
                       *offset_position(*blue_base, north_m=-500),
                       parent_name="Blue HQ"),
            # AT team overwatch
            _make_unit("AT Overwatch", "at_team", "blue",
                       *offset_position(*blue_base, north_m=-100, east_m=200),
                       parent_name="Blue HQ"),
            # Red defending
            _make_unit("Red Def Plt", "infantry_platoon", "red",
                       *red_pos, morale=0.8),
            _make_unit("Red Sec", "infantry_section", "red",
                       *offset_position(*red_pos, north_m=100, east_m=100),
                       morale=0.8),
        ]

    def build_orders(self, unit_ids: dict[str, str]) -> list[dict]:
        red_pos = grid_center("E", 5)
        flank_pos = offset_position(*red_pos, north_m=400, east_m=200)

        return [
            # Phase 1: Mortar prep fire (tick 0)
            make_order(
                ["Mortar Sec"], "fire",
                "Fire on enemy position E5. 3 salvos.",
                target_location={"lat": red_pos[0], "lon": red_pos[1]},
                salvos=3,
            ),
            # Phase 1: Support element moves to fire position
            make_order(
                ["Support Plt"], "attack",
                "Move to support-by-fire position. Suppress enemy at E5.",
                target_location={"lat": red_pos[0] - 0.003, "lon": red_pos[1]},
                speed="fast", formation="line",
            ),
            # Phase 1: Maneuver element begins flank
            make_order(
                ["Maneuver Plt"], "move",
                "Move to flanking position north of E5.",
                target_location={"lat": flank_pos[0], "lon": flank_pos[1]},
                speed="fast", formation="column",
            ),
            # Phase 2: Maneuver element attacks from flank (tick 10)
            make_order(
                ["Maneuver Plt"], "attack",
                "Assault enemy position from the flank!",
                target_location={"lat": red_pos[0], "lon": red_pos[1]},
                speed="fast", formation="line",
                inject_at_tick=10,
            ),
            # Red defends
            make_order(
                ["Red Def Plt"], "defend",
                "Hold positions.",
                side="red",
            ),
        ]

    def build_terrain_cells(self) -> list[dict]:
        red_pos = grid_center("E", 5)
        return [
            {"snail_path": "E5-9", "terrain_type": "open", "depth": 1,
             "centroid_lat": red_pos[0], "centroid_lon": red_pos[1]},
            {"snail_path": "D5-3", "terrain_type": "forest", "depth": 1,
             "centroid_lat": grid_center("D", 5)[0], "centroid_lon": grid_center("D", 5)[1]},
        ]

    def build_map_objects(self) -> list[dict]:
        red_pos = grid_center("E", 5)
        return [
            {
                "object_type": "entrenchment",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [
                        [red_pos[1] - 0.001, red_pos[0]],
                        [red_pos[1] + 0.001, red_pos[0]],
                    ],
                },
                "label": "Red Trench Line",
            },
        ]

    def build_assertions(self) -> list[dict]:
        return [
            {"type": "event_exists", "params": {"event_type": "combat"},
             "description": "Combat should occur"},
            {"type": "unit_moved", "params": {"unit_name": "Maneuver Plt", "min_distance_m": 400},
             "description": "Maneuver element should execute flanking move"},
            {"type": "unit_moved", "params": {"unit_name": "Support Plt", "min_distance_m": 200},
             "description": "Support element should advance to fire position"},
            {"type": "unit_survives", "params": {"unit_name": "Blue HQ"},
             "description": "HQ should survive in rear"},
            {"type": "unit_strength_below",
             "params": {"unit_name": "Red Def Plt", "threshold": 0.9},
             "description": "Red should take damage from coordinated attack"},
            {"type": "event_count_min", "params": {"event_type": "combat", "count": 3},
             "description": "Sustained fire from multiple elements"},
        ]

