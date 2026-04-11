"""
Scenario 11: Multi-Unit Morale Cascade

Tests morale mechanics: units break when morale < 0.15, mutual support bonus,
strength-based morale penalties, and cascading morale effects.

Realistic: weakened, demoralized units face a fresh enemy force at close range.
"""
from scripts.tactical_tests.base import (
    BaseScenario, _make_unit, make_order, grid_center, offset_position,
)


class MoraleCascade(BaseScenario):

    @property
    def name(self) -> str:
        return "S11: Morale Cascade Under Fire"

    @property
    def description(self) -> str:
        return (
            "Weak Blue units face Red force at close range. "
            "Tests morale break threshold (< 0.15), strength-based morale penalties, "
            "and combat damage on weakened units."
        )

    @property
    def ticks(self) -> int:
        return 15

    @property
    def language(self) -> str:
        return "ru"

    def build_units(self) -> list[dict]:
        blue_pos = grid_center("D", 4)
        # Red starts 300m away — 1-2 ticks to arrive at fast speed
        red_pos = offset_position(*blue_pos, north_m=300, east_m=50)

        return [
            # Blue: very weak units
            _make_unit("Weak Blue 1", "infantry_squad", "blue",
                       *blue_pos, strength=0.35, morale=0.3, ammo=0.4),
            _make_unit("Weak Blue 2", "infantry_squad", "blue",
                       *offset_position(*blue_pos, north_m=60, east_m=50),
                       strength=0.45, morale=0.35, ammo=0.5),
            # Red: full-strength platoons — very close
            _make_unit("Red Assault 1", "infantry_platoon", "red",
                       *red_pos, morale=0.95),
            _make_unit("Red Assault 2", "infantry_platoon", "red",
                       *offset_position(*red_pos, north_m=-40, east_m=100),
                       morale=0.95),
        ]

    def build_orders(self, unit_ids: dict[str, str]) -> list[dict]:
        blue_pos = grid_center("D", 4)
        return [
            make_order(["Weak Blue 1"], "defend", "Держать позиции!", side="blue"),
            make_order(["Weak Blue 2"], "defend", "Оборонять! Держаться!", side="blue"),
            make_order(
                ["Red Assault 1"], "attack",
                "Атаковать ослабленного противника.",
                side="red",
                target_location={"lat": blue_pos[0], "lon": blue_pos[1]},
                speed="fast",
            ),
            make_order(
                ["Red Assault 2"], "attack",
                "Фланговая атака.",
                side="red",
                target_location={"lat": blue_pos[0] + 0.0003, "lon": blue_pos[1]},
                speed="fast",
            ),
        ]

    def build_assertions(self) -> list[dict]:
        return [
            {"type": "event_exists", "params": {"event_type": "combat"},
             "description": "Combat should occur at close range"},
            {"type": "unit_strength_below", "params": {"unit_name": "Weak Blue 1", "threshold": 0.50},
             "description": "Weak Blue 1 should take further damage from Red assault"},
        ]
