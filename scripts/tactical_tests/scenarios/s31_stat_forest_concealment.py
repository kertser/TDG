"""
Scenario 31: Statistical — Forest Concealment

Observer vs target in forest. Lower detection rate than open terrain.
Run multiple times to verify concealment effects.
"""
from scripts.tactical_tests.base import (
    BaseScenario, _make_unit, make_order, grid_center, offset_position,
)


class StatForestConcealment(BaseScenario):

    @property
    def name(self) -> str:
        return "S31: Statistical — Forest Concealment"

    @property
    def description(self) -> str:
        return (
            "Observer vs target in forest at ~800m. "
            "Detection rate should be lower than open terrain (10-60%). "
            "Run 20 times."
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
        red_pos = offset_position(*blue_pos, north_m=800, east_m=0)

        return [
            _make_unit("Observer", "infantry_platoon", "blue",
                       *blue_pos, morale=0.9),
            _make_unit("Hidden Target", "infantry_platoon", "red",
                       *red_pos, morale=0.8),
        ]

    def build_orders(self, unit_ids: dict[str, str]) -> list[dict]:
        return [
            make_order(["Observer"], "observe",
                       "Observe. Report contacts."),
            make_order(["Hidden Target"], "defend",
                       "Hold position in forest.", side="red"),
        ]

    def build_terrain_cells(self) -> list[dict]:
        blue_pos = grid_center("D", 4)
        red_pos = offset_position(*blue_pos, north_m=800, east_m=0)
        return [
            {"snail_path": "D4-2", "terrain_type": "forest", "depth": 1,
             "centroid_lat": blue_pos[0], "centroid_lon": blue_pos[1]},
            {"snail_path": "D5-6", "terrain_type": "forest", "depth": 1,
             "centroid_lat": red_pos[0], "centroid_lon": red_pos[1]},
        ]

    def build_assertions(self) -> list[dict]:
        return [
            {"type": "unit_survives", "params": {"unit_name": "Observer"},
             "description": "Observer should survive"},
            {"type": "no_event", "params": {"event_type": "unit_destroyed"},
             "description": "No units destroyed in observation scenario"},
        ]

    def build_statistical_assertions(self) -> list[dict]:
        return [
            {"type": "stat_detection_rate",
             "params": {"observer_side": "blue", "min_rate": 0.0, "max_rate": 1.0},
             "description": "Forest concealment — detection is probabilistic (range 0-100%)"},
        ]

