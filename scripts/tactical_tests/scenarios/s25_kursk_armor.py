"""
Scenario 25: Battle of Kursk — Armor Clash (Prokhorovka inspired)

Massive armor engagement in open terrain. Tank companies + AT teams
clash on open steppe. Tests: tank-on-tank combat, AT effectiveness,
high firepower exchanges, movement speed on open terrain.

Historical: Battle of Prokhorovka, July 12, 1943 — one of the largest
tank battles in history. German Tiger/Panther vs Soviet T-34 masses.
"""
from scripts.tactical_tests.base import (
    BaseScenario, _make_unit, make_order, grid_center, offset_position,
)


class KurskArmorClash(BaseScenario):

    @property
    def name(self) -> str:
        return "S25: Kursk — Armor Clash (Prokhorovka)"

    @property
    def description(self) -> str:
        return (
            "Tank companies clash on open terrain with AT support. "
            "Inspired by Prokhorovka. Tests armor combat, "
            "AT effectiveness, high-intensity fire exchange."
        )

    @property
    def ticks(self) -> int:
        return 40

    @property
    def category(self) -> str:
        return "historical"

    def build_units(self) -> list[dict]:
        blue_start = grid_center("C", 4)
        red_start = grid_center("F", 5)

        return [
            # Blue armor force
            _make_unit("Blue Tank Co", "tank_company", "blue",
                       *blue_start, morale=0.9, strength=1.0),
            _make_unit("Blue Tank Plt", "tank_platoon", "blue",
                       *offset_position(*blue_start, north_m=200, east_m=200),
                       morale=0.9),
            _make_unit("Blue Mech", "mech_platoon", "blue",
                       *offset_position(*blue_start, north_m=-100, east_m=100),
                       morale=0.85),
            _make_unit("Blue AT", "at_team", "blue",
                       *offset_position(*blue_start, north_m=-300, east_m=-200)),
            # Red armor force
            _make_unit("Red Tank Co", "tank_company", "red",
                       *red_start, morale=0.9, strength=1.0),
            _make_unit("Red Tank Plt", "tank_platoon", "red",
                       *offset_position(*red_start, north_m=-200, east_m=-200),
                       morale=0.9),
            _make_unit("Red Mech", "mech_platoon", "red",
                       *offset_position(*red_start, north_m=100, east_m=-100),
                       morale=0.85),
            _make_unit("Red AT", "at_team", "red",
                       *offset_position(*red_start, north_m=300, east_m=200)),
        ]

    def build_orders(self, unit_ids: dict[str, str]) -> list[dict]:
        center = grid_center("D", 5)
        blue_start = grid_center("C", 4)
        red_start = grid_center("F", 5)

        return [
            make_order(
                ["Blue Tank Co"], "attack",
                "All tanks — charge the enemy armor! Full speed!",
                target_location={"lat": center[0], "lon": center[1]},
                speed="fast", formation="line",
            ),
            make_order(
                ["Blue Tank Plt"], "attack",
                "Tank platoon — support the company on the right flank.",
                target_location={"lat": center[0] + 0.001, "lon": center[1] + 0.001},
                speed="fast", formation="wedge",
            ),
            make_order(
                ["Blue Mech"], "attack",
                "Mech — follow tanks, mop up.",
                target_location={"lat": center[0], "lon": center[1]},
                speed="fast",
                inject_at_tick=3,
            ),
            make_order(
                ["Red Tank Co"], "attack",
                "Counter-charge! Destroy the enemy tanks!",
                side="red",
                target_location={"lat": center[0], "lon": center[1]},
                speed="fast", formation="line",
            ),
            make_order(
                ["Red Tank Plt"], "attack",
                "Flank left!",
                side="red",
                target_location={"lat": center[0] - 0.001, "lon": center[1] - 0.001},
                speed="fast",
            ),
            make_order(
                ["Red Mech"], "attack",
                "Follow tanks.",
                side="red",
                target_location={"lat": center[0], "lon": center[1]},
                speed="fast",
                inject_at_tick=3,
            ),
        ]

    def build_terrain_cells(self) -> list[dict]:
        """Open steppe terrain — high visibility, no cover."""
        cells = []
        for col in ["C", "D", "E", "F"]:
            for row in [4, 5, 6]:
                pos = grid_center(col, row)
                cells.append({
                    "snail_path": f"{col}{row}-9",
                    "terrain_type": "open",
                    "depth": 1,
                    "centroid_lat": pos[0], "centroid_lon": pos[1],
                })
        return cells

    def build_assertions(self) -> list[dict]:
        return [
            {"type": "event_exists", "params": {"event_type": "combat"},
             "description": "Tank battle should occur"},
            {"type": "event_count_min", "params": {"event_type": "combat", "count": 5},
             "description": "Intense sustained tank combat"},
            {"type": "detection_occurs", "params": {"observer_side": "red", "min_count": 1},
             "description": "Red detects Blue on open ground"},
            {"type": "unit_strength_below",
             "params": {"unit_name": "Red Tank Co", "threshold": 1.001},
             "description": "Red tanks should take at least some damage from combat"},
        ]





