"""
Scenario 04: Reconnaissance with Concealment

Blue: Recon team + Sniper team observe Red platoon from concealment.
Tests: concealment mechanics, detection probability suppression, stationary vs moving.
"""
from scripts.tactical_tests.base import (
    BaseScenario, _make_unit, make_order, grid_center, offset_position,
)


class ReconConcealment(BaseScenario):

    @property
    def name(self) -> str:
        return "S04: Reconnaissance with Concealment"

    @property
    def description(self) -> str:
        return (
            "Blue recon and sniper teams observe Red from concealment in forest. "
            "Tests concealment detection suppression (max 300m range), "
            "stationary recon remaining hidden, detection probability."
        )

    @property
    def ticks(self) -> int:
        return 20

    def build_units(self) -> list[dict]:
        # Blue recon in forest ~400m from Red (should be concealed)
        red_pos = grid_center("E", 5)
        recon_pos = offset_position(*red_pos, north_m=-300, east_m=-200)
        sniper_pos = offset_position(*red_pos, north_m=200, east_m=-350)

        return [
            _make_unit("Blue Recon", "recon_team", "blue", *recon_pos, morale=0.95),
            _make_unit("Blue Sniper", "sniper_team", "blue", *sniper_pos, morale=0.95),
            _make_unit("Red PLT", "infantry_platoon", "red", *red_pos, morale=0.8),
            _make_unit("Red Patrol", "infantry_squad", "red",
                       *offset_position(*red_pos, north_m=-100, east_m=100),
                       morale=0.8, move_speed_mps=3.0),
        ]

    def build_orders(self, unit_ids: dict[str, str]) -> list[dict]:
        return [
            # Recon observes (stays still = concealed)
            make_order(
                ["Blue Recon"], "observe",
                "Observe enemy positions. Do not engage. Maintain concealment.",
            ),
            make_order(
                ["Blue Sniper"], "observe",
                "Sniper team — observe and report. Hold fire.",
            ),
            # Red patrol moves (will be easier to detect)
            make_order(
                ["Red Patrol"], "move",
                "Patrol the area south of the main position.",
                side="red",
                target_location={
                    "lat": grid_center("E", 4)[0],
                    "lon": grid_center("E", 4)[1],
                },
                speed="slow",
            ),
        ]

    def build_terrain_cells(self) -> list[dict]:
        red_pos = grid_center("E", 5)
        recon_pos = offset_position(*red_pos, north_m=-300, east_m=-200)
        sniper_pos = offset_position(*red_pos, north_m=200, east_m=-350)
        return [
            {"snail_path": "D5-6", "terrain_type": "forest", "depth": 1,
             "centroid_lat": recon_pos[0], "centroid_lon": recon_pos[1]},
            {"snail_path": "D5-1", "terrain_type": "forest", "depth": 1,
             "centroid_lat": sniper_pos[0], "centroid_lon": sniper_pos[1]},
            {"snail_path": "E5-9", "terrain_type": "open", "depth": 1,
             "centroid_lat": red_pos[0], "centroid_lon": red_pos[1]},
        ]

    def build_assertions(self) -> list[dict]:
        return [
            {"type": "detection_occurs", "params": {"observer_side": "blue", "min_count": 1},
             "description": "Blue recon should detect Red units"},
            {"type": "unit_survives", "params": {"unit_name": "Blue Recon"},
             "description": "Concealed recon should survive (hidden)"},
            {"type": "unit_survives", "params": {"unit_name": "Blue Sniper"},
             "description": "Concealed sniper should survive (hidden)"},
            {"type": "no_event", "params": {"event_type": "unit_destroyed"},
             "description": "No units should be destroyed in observation mission"},
        ]

