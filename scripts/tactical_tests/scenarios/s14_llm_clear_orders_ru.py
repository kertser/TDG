"""
Scenario 14: LLM Pipeline — Clear Russian Military Orders

Tests LLM pipeline with standard Russian military commands.
Proper military radio style: callsigns, grid references, task types.
"""
from scripts.tactical_tests.base import (
    BaseScenario, _make_unit, make_raw_order, grid_center, offset_position,
)


class LLMClearOrdersRU(BaseScenario):

    @property
    def name(self) -> str:
        return "S14: LLM — Clear Russian Orders"

    @property
    def description(self) -> str:
        return (
            "Tests LLM with unambiguous Russian military radio commands: "
            "выдвинуться, атаковать, оборонять, огонь, наблюдать."
        )

    @property
    def ticks(self) -> int:
        return 5

    @property
    def language(self) -> str:
        return "ru"

    @property
    def category(self) -> str:
        return "llm"

    @property
    def use_llm_pipeline(self) -> bool:
        return True

    def build_units(self) -> list[dict]:
        pos = grid_center("D", 4)
        return [
            _make_unit("1-й взвод", "infantry_platoon", "blue", *pos),
            _make_unit("2-й взвод", "infantry_platoon", "blue",
                       *offset_position(*pos, east_m=200)),
            _make_unit("Миномётный расчёт", "mortar_section", "blue",
                       *offset_position(*pos, north_m=-400)),
            _make_unit("Разведгруппа", "recon_team", "blue",
                       *offset_position(*pos, north_m=400)),
            _make_unit("Противник 1", "infantry_platoon", "red",
                       *grid_center("F", 5)),
        ]

    def build_orders(self, unit_ids: dict[str, str]) -> list[dict]:
        return [
            make_raw_order(
                ["1-й взвод"],
                "Первому взводу — выдвинуться в квадрат Е5 быстрым маршем.",
                side="blue",
                expected_classification="command",
                expected_order_type="move",
                expected_language="ru",
            ),
            make_raw_order(
                ["2-й взвод"],
                "Второму взводу — атаковать позиции противника в F5. Построение клином.",
                side="blue",
                expected_classification="command",
                expected_order_type="attack",
                expected_language="ru",
            ),
            make_raw_order(
                ["Миномётный расчёт"],
                "Миномётному расчёту — огонь по квадрату F5. Пять залпов.",
                side="blue",
                expected_classification="command",
                expected_order_type="fire",
                expected_language="ru",
            ),
            make_raw_order(
                ["Разведгруппа"],
                "Разведгруппе — наблюдать сектор Е5-F5. Огня не открывать.",
                side="blue",
                expected_classification="command",
                expected_order_type="observe",
                expected_language="ru",
            ),
        ]

    def build_assertions(self) -> list[dict]:
        return [
            {"type": "llm_classification_is",
             "params": {"order_index": 0, "expected": "command"},
             "description": "RU move order → command"},
            {"type": "llm_order_type_is",
             "params": {"order_index": 0, "expected": "move"},
             "description": "выдвинуться → move"},
            {"type": "llm_language_detected",
             "params": {"order_index": 0, "expected": "ru"},
             "description": "Should detect Russian"},
            {"type": "llm_classification_is",
             "params": {"order_index": 1, "expected": "command"},
             "description": "RU attack order → command"},
            {"type": "llm_order_type_is",
             "params": {"order_index": 1, "expected": "attack"},
             "description": "атаковать → attack"},
            {"type": "llm_order_type_is",
             "params": {"order_index": 2, "expected": "fire"},
             "description": "огонь → fire"},
            {"type": "llm_order_type_is",
             "params": {"order_index": 3, "expected": "observe"},
             "description": "наблюдать → observe"},
        ]

