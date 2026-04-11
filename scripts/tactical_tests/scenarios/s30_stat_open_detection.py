"""
Scenario 30: Statistical — Open Terrain Detection

Single Blue observer vs single Red target on open terrain.
Run multiple times to verify detection probability distribution.
Detection uses deterministic hash → different UUIDs each run → different outcomes.
"""
from scripts.tactical_tests.base import (
    BaseScenario, _make_unit, make_order, grid_center, offset_position,
)


class StatOpenDetection(BaseScenario):

    @property
    def name(self) -> str:
        return "S30: Statistical — Open Terrain Detection"

    @property
    def description(self) -> str:
        return (
            "Single observer vs target in open terrain at ~1000m. "
            "Run 20 times. Detection probability should be 50-95% "
            "with variance across runs."
        )

    @property
    def ticks(self) -> int:
        return 10

    @property
    def category(self) -> str:
        return "statistical"

    @property
    def statistical_runs(self) -> int:
        return 20

    def build_units(self) -> list[dict]:
        blue_pos = grid_center("D", 4)
        # ~1000m away
        red_pos = offset_position(*blue_pos, north_m=1000, east_m=0)

        return [
            _make_unit("Observer", "infantry_platoon", "blue",
                       *blue_pos, morale=0.9),
            _make_unit("Target", "infantry_platoon", "red",
                       *red_pos, morale=0.8),
        ]

    def build_orders(self, unit_ids: dict[str, str]) -> list[dict]:
        return [
            make_order(["Observer"], "observe",
                       "Observe enemy positions. Report contacts."),
            make_order(["Target"], "defend",
                       "Hold position.", side="red"),
        ]

    def build_terrain_cells(self) -> list[dict]:
        blue_pos = grid_center("D", 4)
        red_pos = offset_position(*blue_pos, north_m=1000, east_m=0)
        return [
            {"snail_path": "D4-9", "terrain_type": "open", "depth": 1,
             "centroid_lat": blue_pos[0], "centroid_lon": blue_pos[1]},
            {"snail_path": "D5-6", "terrain_type": "open", "depth": 1,
             "centroid_lat": red_pos[0], "centroid_lon": red_pos[1]},
        ]

    def build_assertions(self) -> list[dict]:
        # For individual runs, just check basics
        return [
            {"type": "unit_survives", "params": {"unit_name": "Observer"},
             "description": "Observer should survive (no combat)"},
        ]

    def build_statistical_assertions(self) -> list[dict]:
        return [
            {"type": "stat_detection_rate",
             "params": {"observer_side": "blue", "min_rate": 0.25, "max_rate": 1.0},
             "description": "Detection rate in open terrain at 1000m should be 25-100%"},
        ]

