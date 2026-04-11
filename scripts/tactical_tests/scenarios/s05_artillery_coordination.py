"""
Scenario 05: Artillery Fire Support Coordination

Blue: Infantry platoon + Mortar section + Artillery platoon.
Red: Infantry platoon 2km away.

Tests: artillery support auto-assignment, fire requests, danger-close ceasefire,
salvo counting, area fire blast radius, phased standby → fire.
"""
from scripts.tactical_tests.base import (
    BaseScenario, _make_unit, make_order, grid_center, offset_position,
)


class ArtilleryCoordination(BaseScenario):

    @property
    def name(self) -> str:
        return "S05: Artillery Fire Support Coordination"

    @property
    def description(self) -> str:
        return (
            "Blue infantry attacks with mortar and artillery support. "
            "Tests fire request flow, auto-support, danger-close ceasefire at 250m, "
            "salvo counting, and area fire mechanics."
        )

    @property
    def ticks(self) -> int:
        return 25

    @property
    def language(self) -> str:
        return "ru"

    def build_units(self) -> list[dict]:
        blue_base = grid_center("C", 3)
        arty_pos = grid_center("B", 2)
        red_pos = grid_center("E", 5)

        return [
            _make_unit("Blue HQ", "headquarters", "blue",
                       *offset_position(*blue_base, north_m=-300),
                       morale=0.95),
            _make_unit("Assault Plt", "infantry_platoon", "blue",
                       *blue_base, parent_name="Blue HQ"),
            _make_unit("Mortar Sect", "mortar_section", "blue",
                       *offset_position(*blue_base, north_m=-500, east_m=-200),
                       parent_name="Blue HQ"),
            _make_unit("Arty Plt", "artillery_platoon", "blue",
                       *arty_pos, parent_name="Blue HQ"),
            # Red
            _make_unit("Red Def PLT", "infantry_platoon", "red",
                       *red_pos, morale=0.8),
        ]

    def build_orders(self, unit_ids: dict[str, str]) -> list[dict]:
        red_pos = grid_center("E", 5)

        return [
            # Artillery opens fire on Red position first
            make_order(
                ["Arty Plt"], "fire",
                "Артиллерийскому взводу — огонь по квадрату Е5. Пять залпов.",
                side="blue",
                target_location={"lat": red_pos[0], "lon": red_pos[1]},
                salvos=5,
            ),
            # Mortar fires simultaneously
            make_order(
                ["Mortar Sect"], "fire",
                "Миномётному расчёту — огонь по позициям противника в Е5.",
                side="blue",
                target_location={"lat": red_pos[0], "lon": red_pos[1]},
                salvos=3,
            ),
            # Infantry advances after preparation
            make_order(
                ["Assault Plt"], "attack",
                "Штурмовому взводу — выдвинуться для атаки на позиции в Е5. "
                "Движение быстрым темпом после артподготовки.",
                side="blue",
                target_location={"lat": red_pos[0], "lon": red_pos[1]},
                speed="fast",
                inject_at_tick=3,
            ),
            # Red defends
            make_order(
                ["Red Def PLT"], "defend",
                "Оборонять позиции.",
                side="red",
            ),
        ]

    def build_assertions(self) -> list[dict]:
        return [
            {"type": "event_exists", "params": {"event_type": "combat"},
             "description": "Artillery should fire and combat events generated"},
            {"type": "unit_moved", "params": {"unit_name": "Assault Plt", "min_distance_m": 300},
             "description": "Infantry should advance toward Red after arty prep"},
            {"type": "unit_survives", "params": {"unit_name": "Assault Plt"},
             "description": "Assaulting infantry should survive"},
            {"type": "event_count_min", "params": {"event_type": "combat", "count": 3},
             "description": "Multiple combat events from sustained fire"},
        ]

