"""
Scenario 28: 73 Easting — Armor Attack in Sandstorm

Recon-led armor attack in storm conditions (sandstorm). Low visibility.
US armor with thermal sights (NVG capability) vs Iraqi positions.

Historical: Battle of 73 Easting, Feb 26, 1991 — 2nd ACR's Eagle Troop
destroyed Iraqi Republican Guard positions in a sandstorm, using thermal
sights to see through the storm while Iraqis couldn't see them.
"""
from scripts.tactical_tests.base import (
    BaseScenario, _make_unit, make_order, grid_center, offset_position,
)


class SeventyThreeEasting(BaseScenario):

    @property
    def name(self) -> str:
        return "S28: 73 Easting — Armor in Sandstorm"

    @property
    def description(self) -> str:
        return (
            "US armor with thermal sights attacks through sandstorm. "
            "Tests storm weather effects (-60% vis, -40% move), NVG advantage, "
            "recon-led attack. Inspired by Battle of 73 Easting."
        )

    @property
    def ticks(self) -> int:
        return 25

    @property
    def category(self) -> str:
        return "historical"

    def build_scenario_data(self) -> dict:
        data = super().build_scenario_data()
        data["environment"]["weather"] = "storm"
        data["environment"]["precipitation"] = "sandstorm"
        data["environment"]["visibility_km"] = 0.5
        return data

    def build_units(self) -> list[dict]:
        blue_start = grid_center("C", 4)
        red_pos = grid_center("F", 5)

        return [
            # Blue: recon + armor with thermal sights
            _make_unit("Eagle Recon", "recon_team", "blue",
                       *offset_position(*blue_start, north_m=300),
                       morale=0.95, capabilities={"has_nvg": True, "is_recon": True}),
            _make_unit("Eagle Tanks", "tank_company", "blue",
                       *blue_start, morale=0.95,
                       capabilities={"has_nvg": True}),
            _make_unit("Eagle Mech", "mech_platoon", "blue",
                       *offset_position(*blue_start, north_m=-200),
                       morale=0.9, capabilities={"has_nvg": True}),
            # Red: dug-in positions, no thermals
            _make_unit("Red Tank Plt 1", "tank_platoon", "red",
                       *red_pos, morale=0.7),
            _make_unit("Red Tank Plt 2", "tank_platoon", "red",
                       *offset_position(*red_pos, north_m=200, east_m=200),
                       morale=0.7),
            _make_unit("Red Infantry", "infantry_platoon", "red",
                       *offset_position(*red_pos, north_m=-150),
                       morale=0.65),
        ]

    def build_orders(self, unit_ids: dict[str, str]) -> list[dict]:
        red_pos = grid_center("F", 5)
        center = grid_center("E", 5)

        return [
            make_order(
                ["Eagle Recon"], "move",
                "Scout ahead through the storm. Report contacts.",
                target_location={"lat": center[0], "lon": center[1]},
                speed="slow",
            ),
            make_order(
                ["Eagle Tanks"], "attack",
                "Tanks — advance through sandstorm. Engage with thermals.",
                target_location={"lat": red_pos[0], "lon": red_pos[1]},
                speed="fast",
                inject_at_tick=3,
            ),
            make_order(
                ["Eagle Mech"], "attack",
                "Follow tanks, mop up.",
                target_location={"lat": red_pos[0], "lon": red_pos[1]},
                speed="fast",
                inject_at_tick=5,
            ),
            # Red holds position (can't see through storm)
            make_order(
                ["Red Tank Plt 1"], "defend",
                "Hold positions.",
                side="red",
            ),
            make_order(
                ["Red Infantry"], "defend",
                "Defend.",
                side="red",
            ),
        ]

    def build_terrain_cells(self) -> list[dict]:
        """Desert terrain — open, flat."""
        cells = []
        for col in ["C", "D", "E", "F"]:
            for row in [4, 5]:
                pos = grid_center(col, row)
                cells.append({
                    "snail_path": f"{col}{row}-9",
                    "terrain_type": "desert",
                    "depth": 1,
                    "centroid_lat": pos[0], "centroid_lon": pos[1],
                })
        return cells

    def build_assertions(self) -> list[dict]:
        return [
            {"type": "event_exists", "params": {"event_type": "contact_new"},
             "description": "Blue should detect Red (even in storm, combat confirms detection)"},
            {"type": "event_exists", "params": {"event_type": "combat"},
             "description": "Combat should occur"},
            {"type": "unit_moved", "params": {"unit_name": "Eagle Tanks", "min_distance_m": 300},
             "description": "Tanks should advance (slower in storm)"},
            {"type": "unit_survives", "params": {"unit_name": "Eagle Tanks"},
             "description": "Blue armor should survive (technology advantage)"},
            {"type": "unit_strength_below",
             "params": {"unit_name": "Red Tank Plt 1", "threshold": 0.9},
             "description": "Red tanks caught blind in storm should take damage"},
        ]


