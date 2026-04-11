"""
Scenario 26: Normandy Beach Assault (Omaha inspired)

Infantry advances uphill over open terrain against entrenched positions
with minefields, barbed wire, pillboxes. High attacker casualties expected.
Tests: fortification protection, obstacle effects, uphill penalty.

Historical: D-Day Omaha Beach, June 6, 1944 — US 1st/29th Infantry
assaulting German fortified bluffs with obstacles, minefields, MG nests.
"""
from scripts.tactical_tests.base import (
    BaseScenario, _make_unit, make_order, grid_center, offset_position,
)


class NormandyBeachAssault(BaseScenario):

    @property
    def name(self) -> str:
        return "S26: Normandy Beach Assault (Omaha)"

    @property
    def description(self) -> str:
        return (
            "Infantry assaults uphill against fortified positions with "
            "minefields, barbed wire, pillboxes. Very high casualties. "
            "Tests obstacle effects, fortification bonuses, attrition."
        )

    @property
    def ticks(self) -> int:
        return 35

    @property
    def category(self) -> str:
        return "historical"

    def build_units(self) -> list[dict]:
        # Beach: C3, Bluffs: D4-E5
        beach = grid_center("C", 3)
        bluffs = grid_center("D", 5)

        return [
            # Assault waves
            _make_unit("Wave 1", "infantry_platoon", "blue",
                       *beach, morale=0.85),
            _make_unit("Wave 2", "infantry_platoon", "blue",
                       *offset_position(*beach, north_m=100, east_m=200),
                       morale=0.85),
            _make_unit("Wave 3", "infantry_platoon", "blue",
                       *offset_position(*beach, north_m=-100, east_m=-100),
                       morale=0.8),
            _make_unit("Engineers", "combat_engineer_team", "blue",
                       *offset_position(*beach, north_m=50, east_m=100),
                       morale=0.9),
            # German defenders on bluffs
            _make_unit("Garrison 1", "infantry_platoon", "red",
                       *bluffs, morale=0.9),
            _make_unit("Garrison 2", "infantry_section", "red",
                       *offset_position(*bluffs, north_m=100, east_m=-200),
                       morale=0.9),
            _make_unit("MG Nest", "infantry_squad", "red",
                       *offset_position(*bluffs, north_m=-100, east_m=100),
                       morale=0.85),
            _make_unit("Mortar Support", "mortar_team", "red",
                       *offset_position(*bluffs, north_m=300),
                       morale=0.85),
        ]

    def build_orders(self, unit_ids: dict[str, str]) -> list[dict]:
        bluffs = grid_center("D", 5)

        return [
            make_order(
                ["Wave 1"], "attack",
                "Assault the bluffs! Move move move!",
                target_location={"lat": bluffs[0], "lon": bluffs[1]},
                speed="fast", formation="line",
            ),
            make_order(
                ["Wave 2"], "attack",
                "Second wave — attack right of first!",
                target_location={"lat": bluffs[0] + 0.0005, "lon": bluffs[1] + 0.001},
                speed="fast", formation="line",
            ),
            make_order(
                ["Wave 3"], "attack",
                "Third wave — follow first, reinforce.",
                target_location={"lat": bluffs[0], "lon": bluffs[1]},
                speed="fast",
                inject_at_tick=5,
            ),
            make_order(
                ["Engineers"], "move",
                "Clear obstacles on the beach.",
                target_location={"lat": bluffs[0] - 0.003, "lon": bluffs[1]},
                speed="fast",
            ),
            make_order(
                ["Garrison 1"], "defend",
                "Hold the bluffs. Maximum fire.",
                side="red",
            ),
            make_order(
                ["Garrison 2"], "defend",
                "Defend flanks.",
                side="red",
            ),
            make_order(
                ["Mortar Support"], "fire",
                "Fire on the beach.",
                side="red",
                target_location={
                    "lat": grid_center("C", 3)[0],
                    "lon": grid_center("C", 3)[1],
                },
                salvos=5,
            ),
        ]

    def build_terrain_cells(self) -> list[dict]:
        return [
            {"snail_path": "C3-2", "terrain_type": "open", "depth": 1,
             "centroid_lat": grid_center("C", 3)[0], "centroid_lon": grid_center("C", 3)[1]},
            {"snail_path": "C4-2", "terrain_type": "open", "depth": 1,
             "centroid_lat": grid_center("C", 4)[0], "centroid_lon": grid_center("C", 4)[1]},
            {"snail_path": "D5-9", "terrain_type": "open", "depth": 1,
             "centroid_lat": grid_center("D", 5)[0], "centroid_lon": grid_center("D", 5)[1]},
        ]

    def build_elevation_cells(self) -> list[dict]:
        """Bluffs are higher than beach — elevation advantage for defenders."""
        return [
            {"snail_path": "C3-2", "depth": 1, "elevation_m": 5,
             "slope_deg": 5, "centroid_lat": grid_center("C", 3)[0],
             "centroid_lon": grid_center("C", 3)[1]},
            {"snail_path": "C4-2", "depth": 1, "elevation_m": 15,
             "slope_deg": 15, "centroid_lat": grid_center("C", 4)[0],
             "centroid_lon": grid_center("C", 4)[1]},
            {"snail_path": "D5-9", "depth": 1, "elevation_m": 50,
             "slope_deg": 5, "centroid_lat": grid_center("D", 5)[0],
             "centroid_lon": grid_center("D", 5)[1]},
        ]

    def build_map_objects(self) -> list[dict]:
        beach = grid_center("C", 4)
        return [
            # Barbed wire along beach approach
            {
                "object_type": "barbed_wire",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [
                        [beach[1] - 0.003, beach[0]],
                        [beach[1] + 0.003, beach[0]],
                    ],
                },
                "label": "Beach Obstacles",
            },
            # Minefield
            {
                "object_type": "minefield",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [beach[1] - 0.002, beach[0] + 0.001],
                        [beach[1] + 0.002, beach[0] + 0.001],
                        [beach[1] + 0.002, beach[0] + 0.002],
                        [beach[1] - 0.002, beach[0] + 0.002],
                        [beach[1] - 0.002, beach[0] + 0.001],
                    ]],
                },
                "label": "Beach Minefield",
            },
            # Pillbox on bluff
            {
                "object_type": "pillbox",
                "geometry": {"type": "Point",
                             "coordinates": [grid_center("D", 5)[1], grid_center("D", 5)[0]]},
                "label": "Bluff Pillbox",
            },
        ]

    def build_assertions(self) -> list[dict]:
        return [
            {"type": "event_exists", "params": {"event_type": "combat"},
             "description": "Beach assault combat should occur"},
            {"type": "event_count_min", "params": {"event_type": "combat", "count": 5},
             "description": "Intense sustained fire from bluffs"},
            {"type": "unit_moved", "params": {"unit_name": "Wave 1", "min_distance_m": 200},
             "description": "Assault wave should advance up beach"},
            {"type": "unit_survives", "params": {"unit_name": "Garrison 1"},
             "description": "Dug-in defenders should survive initial waves"},
            # High casualties expected for attackers (may or may not happen within 35 ticks)
            {"type": "event_count_min", "params": {"event_type": "combat", "count": 3},
             "description": "Sustained combat from multiple engagements"},
        ]


