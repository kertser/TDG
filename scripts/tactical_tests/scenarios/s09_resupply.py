"""
Scenario 09: Resupply Under Fire

Blue platoon with low ammo near supply cache. Logistics unit provides resupply.
Red infantry attacks from a distance. Tests supply cache proximity resupply and
logistics unit resupply mechanics.

Realistic: a platoon resupplies from a forward cache before re-engaging.
"""
from scripts.tactical_tests.base import (
    BaseScenario, _make_unit, make_order, grid_center, offset_position,
)


class ResupplyUnderFire(BaseScenario):

    @property
    def name(self) -> str:
        return "S09: Resupply Under Fire"

    @property
    def description(self) -> str:
        return (
            "Blue platoon low on ammo near supply cache. Logistics unit nearby. "
            "Red attacks from distance. Tests supply cache resupply, "
            "logistics unit proximity ammo recovery."
        )

    @property
    def ticks(self) -> int:
        return 20

    @property
    def language(self) -> str:
        return "ru"

    def build_units(self) -> list[dict]:
        blue_pos = grid_center("D", 4)
        # Supply cache very close (40m) — within 50m resupply radius
        supply_pos = offset_position(*blue_pos, north_m=-30, east_m=20)
        # Red far away: G5 — ~3km, infantry foot fast 3 m/s = 180m/tick → 16 ticks to arrive
        red_pos = grid_center("G", 5)

        return [
            _make_unit("Blue Low Ammo", "infantry_platoon", "blue",
                       *blue_pos, ammo=0.15, morale=0.75, strength=0.9),
            _make_unit("Blue Logistics", "logistics_unit", "blue",
                       *supply_pos, morale=0.85),
            _make_unit("Red Attacker", "infantry_platoon", "red",
                       *red_pos, morale=0.9),
        ]

    def build_orders(self, unit_ids: dict[str, str]) -> list[dict]:
        blue_pos = grid_center("D", 4)

        return [
            # Blue defends near supply cache, getting resupplied
            make_order(
                ["Blue Low Ammo"], "defend",
                "Оборонять позиции. Пополняем боекомплект.",
                side="blue",
            ),
            # Red attacks — will take time to arrive
            make_order(
                ["Red Attacker"], "attack",
                "Атаковать позиции противника в D4.",
                side="red",
                target_location={"lat": blue_pos[0], "lon": blue_pos[1]},
                speed="fast",
            ),
        ]

    def build_map_objects(self) -> list[dict]:
        blue_pos = grid_center("D", 4)
        supply_pos = offset_position(*blue_pos, north_m=-30, east_m=20)
        return [
            {
                "object_type": "supply_cache",
                "geometry": {"type": "Point", "coordinates": [supply_pos[1], supply_pos[0]]},
                "label": "Forward Supply Cache",
                "properties": {},
            },
        ]

    def build_assertions(self) -> list[dict]:
        return [
            {"type": "unit_survives", "params": {"unit_name": "Blue Low Ammo"},
             "description": "Blue platoon should survive (resupplied before combat)"},
            {"type": "unit_moved", "params": {"unit_name": "Red Attacker", "min_distance_m": 500},
             "description": "Red should advance toward Blue"},
            {"type": "unit_survives", "params": {"unit_name": "Blue Logistics"},
             "description": "Logistics unit should survive (behind defensive line)"},
        ]
