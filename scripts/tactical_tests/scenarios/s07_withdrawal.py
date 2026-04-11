"""
Scenario 07: Withdrawal Under Pressure

Blue platoon makes brief contact with Red, then receives disengage order.
Cover platoon provides rear protection. Tests: disengage task, cover seeking,
auto-return-fire skips disengaging units.

Realistic: a force in contact disengages and seeks cover — common tactical maneuver.
"""
from scripts.tactical_tests.base import (
    BaseScenario, _make_unit, make_order, grid_center, offset_position,
)


class WithdrawalUnderPressure(BaseScenario):

    @property
    def name(self) -> str:
        return "S07: Withdrawal Under Pressure"

    @property
    def description(self) -> str:
        return (
            "Blue platoon makes brief contact, then disengages. "
            "Tests break-contact mechanics, cover terrain seeking, "
            "auto-return-fire skip for disengaging units."
        )

    @property
    def ticks(self) -> int:
        return 25

    def build_units(self) -> list[dict]:
        # Blue starts in forest at D5, Red starts 1600m north
        # Red at fast 3.0m/s=180m/tick: enters 600m weapon range at tick ~6
        blue_pos = grid_center("D", 5)
        red_pos = offset_position(*blue_pos, north_m=1600, east_m=0)

        return [
            _make_unit("Blue Contact Plt", "infantry_platoon", "blue",
                       *blue_pos, morale=0.9, strength=1.0),
            _make_unit("Blue Cover Plt", "infantry_platoon", "blue",
                       *offset_position(*blue_pos, north_m=-900, east_m=-100),
                       morale=0.9),
            _make_unit("Red Pursuit", "infantry_platoon", "red",
                       *red_pos, morale=0.9),
        ]

    def build_orders(self, unit_ids: dict[str, str]) -> list[dict]:
        blue_pos = grid_center("D", 5)

        return [
            make_order(
                ["Blue Contact Plt"], "defend",
                "Hold position. Observe and report.",
            ),
            make_order(
                ["Red Pursuit"], "attack",
                "Attack enemy positions.",
                side="red",
                target_location={"lat": blue_pos[0], "lon": blue_pos[1]},
                speed="fast",
            ),
            # Tick 8: disengage after brief contact (Red arrives ~tick 6)
            make_order(
                ["Blue Contact Plt"], "disengage",
                "Break contact! Withdraw to cover south.",
                inject_at_tick=8,
            ),
            make_order(
                ["Blue Cover Plt"], "defend",
                "Cover the withdrawal.",
                inject_at_tick=8,
            ),
        ]

    def build_terrain_cells(self) -> list[dict]:
        blue_pos = grid_center("D", 5)
        # Forest surrounding the Blue position for cover during disengage
        return [
            {"snail_path": "D5-9", "terrain_type": "forest", "depth": 1,
             "centroid_lat": blue_pos[0], "centroid_lon": blue_pos[1]},
            # Forest cells to the south (cover for withdrawal)
            {"snail_path": "D5-7", "terrain_type": "forest", "depth": 1,
             "centroid_lat": blue_pos[0] - 0.003, "centroid_lon": blue_pos[1] - 0.003},
            {"snail_path": "D5-6", "terrain_type": "forest", "depth": 1,
             "centroid_lat": blue_pos[0] - 0.003, "centroid_lon": blue_pos[1]},
            {"snail_path": "D4-2", "terrain_type": "forest", "depth": 1,
             "centroid_lat": blue_pos[0] - 0.005, "centroid_lon": blue_pos[1]},
            {"snail_path": "C5-3", "terrain_type": "forest", "depth": 1,
             "centroid_lat": blue_pos[0], "centroid_lon": blue_pos[1] - 0.005},
        ]

    def build_assertions(self) -> list[dict]:
        return [
            {"type": "event_exists", "params": {"event_type": "combat"},
             "description": "Initial combat should occur"},
            {"type": "unit_survives", "params": {"unit_name": "Blue Cover Plt"},
             "description": "Cover platoon in rear should survive"},
            {"type": "unit_strength_below",
             "params": {"unit_name": "Red Pursuit", "threshold": 1.001},
             "description": "Red should take at least some damage from Blue defense"},
        ]
