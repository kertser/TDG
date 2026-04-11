"""
Scenario 12: Combined Arms — Full Spectrum

Large scenario with all unit types exercising all engine subsystems.
HQ, infantry, tanks, recon, mortar, AT, engineers, logistics, sniper, OP.

Forces start ~2km apart. Recon leads, mortars prep, assault follows.
"""
from scripts.tactical_tests.base import (
    BaseScenario, _make_unit, make_order, grid_center, offset_position,
)


class CombinedArmsFullSpectrum(BaseScenario):

    @property
    def name(self) -> str:
        return "S12: Combined Arms — Full Spectrum"

    @property
    def description(self) -> str:
        return (
            "Large scenario with HQ, infantry, tanks, recon, mortar, AT, engineers, "
            "logistics, sniper, observation post. Tests all subsystems."
        )

    @property
    def ticks(self) -> int:
        return 35

    def build_units(self) -> list[dict]:
        # Blue base at D4, Red base at F5 (~2km apart)
        blue_base = grid_center("D", 4)
        red_base = grid_center("F", 5)

        return [
            # ── Blue Force ──
            _make_unit("Blue HQ", "headquarters", "blue",
                       *offset_position(*blue_base, north_m=-300),
                       morale=0.95),
            _make_unit("Alpha Plt", "infantry_platoon", "blue",
                       *offset_position(*blue_base, north_m=100, east_m=-150),
                       parent_name="Blue HQ"),
            _make_unit("Bravo Plt", "infantry_platoon", "blue",
                       *offset_position(*blue_base, north_m=100, east_m=150),
                       parent_name="Blue HQ"),
            _make_unit("Charlie Plt", "mech_platoon", "blue",
                       *offset_position(*blue_base, north_m=-50, east_m=0),
                       parent_name="Blue HQ"),
            _make_unit("Tank Section", "tank_platoon", "blue",
                       *offset_position(*blue_base, north_m=0, east_m=300),
                       parent_name="Blue HQ"),
            _make_unit("Mortar Team", "mortar_section", "blue",
                       *offset_position(*blue_base, north_m=-400, east_m=-100),
                       parent_name="Blue HQ"),
            _make_unit("Recon Alpha", "recon_team", "blue",
                       *offset_position(*blue_base, north_m=400, east_m=0),
                       parent_name="Blue HQ"),
            _make_unit("Sniper Pair", "sniper_team", "blue",
                       *offset_position(*blue_base, north_m=300, east_m=-200),
                       parent_name="Blue HQ"),
            _make_unit("AT Section", "at_team", "blue",
                       *offset_position(*blue_base, north_m=50, east_m=-300),
                       parent_name="Blue HQ"),
            _make_unit("Engr Team", "combat_engineer_team", "blue",
                       *offset_position(*blue_base, north_m=-150, east_m=80),
                       parent_name="Blue HQ"),
            _make_unit("Supply Unit", "logistics_unit", "blue",
                       *offset_position(*blue_base, north_m=-500, east_m=0),
                       parent_name="Blue HQ"),
            _make_unit("Blue OP", "observation_post", "blue",
                       *offset_position(*blue_base, north_m=500, east_m=150),
                       parent_name="Blue HQ"),

            # ── Red Force ──
            _make_unit("Red HQ", "headquarters", "red",
                       *offset_position(*red_base, north_m=200),
                       morale=0.85),
            _make_unit("Red 1st Plt", "infantry_platoon", "red",
                       *red_base, parent_name="Red HQ"),
            _make_unit("Red 2nd Plt", "infantry_platoon", "red",
                       *offset_position(*red_base, north_m=-150, east_m=150),
                       parent_name="Red HQ"),
            _make_unit("Red Mech", "mech_platoon", "red",
                       *offset_position(*red_base, north_m=50, east_m=-200),
                       parent_name="Red HQ"),
            _make_unit("Red AT", "at_team", "red",
                       *offset_position(*red_base, north_m=-80, east_m=80),
                       parent_name="Red HQ"),
            _make_unit("Red Mortar", "mortar_team", "red",
                       *offset_position(*red_base, north_m=300, east_m=0),
                       parent_name="Red HQ"),
            _make_unit("Red Recon", "recon_team", "red",
                       *offset_position(*red_base, north_m=-300, east_m=-150),
                       parent_name="Red HQ"),
        ]

    def build_orders(self, unit_ids: dict[str, str]) -> list[dict]:
        red_base = grid_center("F", 5)
        # Objective between the two forces: E5
        objective = grid_center("E", 5)

        return [
            # ── Phase 1: Recon forward ──
            make_order(
                ["Recon Alpha"], "move",
                "Recon team — advance to E5, report enemy positions.",
                target_location={"lat": objective[0], "lon": objective[1]},
                speed="slow",
            ),
            make_order(
                ["Blue OP"], "observe",
                "Observation post — scan sector E5-F5 for enemy activity.",
            ),

            # ── Phase 2: Fire preparation (tick 3) ──
            make_order(
                ["Mortar Team"], "fire",
                "Mortars — fire on suspected enemy positions at F5.",
                target_location={"lat": red_base[0], "lon": red_base[1]},
                salvos=3,
                inject_at_tick=3,
            ),

            # ── Phase 3: Main assault (tick 5) ──
            make_order(
                ["Alpha Plt"], "attack",
                "Alpha — assault objective E5. Formation: line. Speed: fast.",
                target_location={"lat": objective[0], "lon": objective[1]},
                speed="fast", formation="line",
                inject_at_tick=5,
            ),
            make_order(
                ["Bravo Plt"], "attack",
                "Bravo — supporting attack, right flank.",
                target_location={
                    "lat": objective[0] + 0.0005,
                    "lon": objective[1] + 0.0005,
                },
                speed="fast", formation="wedge",
                inject_at_tick=5,
            ),
            make_order(
                ["Charlie Plt"], "attack",
                "Charlie mech — follow Alpha, exploit any breach.",
                target_location={"lat": objective[0], "lon": objective[1]},
                speed="fast",
                inject_at_tick=7,
            ),
            make_order(
                ["Tank Section"], "attack",
                "Tanks — advance and support the assault with direct fire.",
                target_location={"lat": objective[0], "lon": objective[1]},
                speed="fast",
                inject_at_tick=5,
            ),

            # ── Supporting elements ──
            make_order(
                ["AT Section"], "move",
                "AT section — overwatch position at D5.",
                target_location={
                    "lat": grid_center("D", 5)[0],
                    "lon": grid_center("D", 5)[1],
                },
                speed="slow",
                inject_at_tick=3,
            ),

            # ── Red defense ──
            make_order(
                ["Red 1st Plt"], "defend", "Defend positions.", side="red",
            ),
            make_order(
                ["Red 2nd Plt"], "defend", "Defend right flank.", side="red",
            ),
            make_order(
                ["Red Mortar"], "fire",
                "Fire on approaching enemy.",
                side="red",
                target_location={
                    "lat": grid_center("D", 4)[0],
                    "lon": grid_center("D", 4)[1],
                },
                salvos=5,
                inject_at_tick=3,
            ),
        ]

    def build_terrain_cells(self) -> list[dict]:
        objective = grid_center("E", 5)
        return [
            {"snail_path": "D4-2", "terrain_type": "road", "depth": 1,
             "centroid_lat": grid_center("D", 4)[0], "centroid_lon": grid_center("D", 4)[1]},
            {"snail_path": "E5-9", "terrain_type": "open", "depth": 1,
             "centroid_lat": objective[0], "centroid_lon": objective[1]},
            {"snail_path": "F5-9", "terrain_type": "urban", "depth": 1,
             "centroid_lat": grid_center("F", 5)[0], "centroid_lon": grid_center("F", 5)[1]},
            {"snail_path": "D5-1", "terrain_type": "forest", "depth": 1,
             "centroid_lat": grid_center("D", 5)[0], "centroid_lon": grid_center("D", 5)[1]},
        ]

    def build_assertions(self) -> list[dict]:
        return [
            {"type": "event_exists", "params": {"event_type": "movement"},
             "description": "Units should move"},
            {"type": "event_exists", "params": {"event_type": "combat"},
             "description": "Combat should occur"},
            {"type": "detection_occurs", "params": {"observer_side": "blue", "min_count": 1},
             "description": "Blue recon/OP should detect Red"},
            {"type": "unit_survives", "params": {"unit_name": "Blue HQ"},
             "description": "Blue HQ should survive (rear)"},
            {"type": "unit_survives", "params": {"unit_name": "Supply Unit"},
             "description": "Supply unit should survive (rear)"},
            {"type": "unit_moved", "params": {"unit_name": "Recon Alpha", "min_distance_m": 300},
             "description": "Recon should advance forward"},
            {"type": "unit_moved", "params": {"unit_name": "Alpha Plt", "min_distance_m": 200},
             "description": "Assault infantry should advance"},
            {"type": "event_count_min", "params": {"event_type": "combat", "count": 2},
             "description": "Multiple combat exchanges expected"},
            {"type": "unit_strength_below", "params": {"unit_name": "Red 1st Plt", "threshold": 0.98},
             "description": "Red defenders should take some damage"},
        ]
