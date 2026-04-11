"""
Base class for all tactical test scenarios.

Each scenario defines:
  - Units, their positions and capabilities
  - Orders to issue (pre-parsed, no LLM required)
  - Map objects (obstacles, structures)
  - Terrain cells for specific terrain setup
  - Assertions to evaluate after running

Scenario categories:
  - engine:     Tests deterministic engine mechanics (pre-parsed orders)
  - llm:        Tests the LLM order-parsing pipeline (raw text orders)
  - tactical:   Tests complex tactical situations (pre-parsed orders)
  - historical: Tests historical battle-inspired scenarios (pre-parsed)
  - statistical: Tests probabilistic outcomes over multiple runs

All scenarios use the Reims area grid:
  Origin: (49.025, 4.440), 8x8 squares, 1000m each, alphanumeric labeling
"""
from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any


# ── Standard grid for all scenarios ──
GRID_ORIGIN_LAT = 49.025
GRID_ORIGIN_LON = 4.440
GRID_COLUMNS = 8
GRID_ROWS = 8
GRID_SQUARE_SIZE_M = 1000
GRID_LABELING = "alphanumeric"

# ── Approximate coordinate helpers ──
# 1 degree latitude ≈ 111,320 m
# 1 degree longitude ≈ 74,000 m at 49°N
METERS_PER_DEG_LAT = 111_320.0
METERS_PER_DEG_LON = 74_000.0


def offset_position(lat: float, lon: float, north_m: float = 0, east_m: float = 0) -> tuple[float, float]:
    """Offset a position by meters north/east. Returns (lat, lon)."""
    return (
        lat + north_m / METERS_PER_DEG_LAT,
        lon + east_m / METERS_PER_DEG_LON,
    )


def grid_center(col_letter: str, row_number: int) -> tuple[float, float]:
    """Get approximate center of a grid square. A1 = SW corner.
    Returns (lat, lon)."""
    col_idx = ord(col_letter.upper()) - ord('A')
    row_idx = row_number - 1
    lat = GRID_ORIGIN_LAT + (row_idx + 0.5) * GRID_SQUARE_SIZE_M / METERS_PER_DEG_LAT
    lon = GRID_ORIGIN_LON + (col_idx + 0.5) * GRID_SQUARE_SIZE_M / METERS_PER_DEG_LON
    return lat, lon


def distance_between(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance between two geographic points in meters."""
    import math
    dlat = (lat2 - lat1) * METERS_PER_DEG_LAT
    dlon = (lon2 - lon1) * METERS_PER_DEG_LON
    return math.sqrt(dlat * dlat + dlon * dlon)


# ── Standard unit templates ──
# SIDC codes for Blue (identity=3) and Red (identity=6)

def _make_unit(
    name: str,
    unit_type: str,
    side: str,
    lat: float,
    lon: float,
    strength: float = 1.0,
    ammo: float = 1.0,
    morale: float = 0.9,
    move_speed_mps: float | None = None,
    detection_range_m: float | None = None,
    capabilities: dict | None = None,
    parent_name: str | None = None,
) -> dict:
    """Create a unit definition dict."""
    # Default speeds and detection ranges by unit type
    _defaults = {
        "infantry_platoon": {"speed": 3.0, "det": 1500},
        "infantry_company": {"speed": 2.5, "det": 1500},
        "infantry_squad": {"speed": 3.0, "det": 1500},
        "infantry_section": {"speed": 3.0, "det": 1500},
        "infantry_team": {"speed": 3.5, "det": 1500},
        "mech_platoon": {"speed": 10.0, "det": 1800},
        "mech_company": {"speed": 8.0, "det": 1800},
        "tank_platoon": {"speed": 12.0, "det": 2000},
        "tank_company": {"speed": 10.0, "det": 2000},
        "artillery_battery": {"speed": 5.0, "det": 1200},
        "artillery_platoon": {"speed": 5.0, "det": 1200},
        "mortar_section": {"speed": 2.5, "det": 1000},
        "mortar_team": {"speed": 3.0, "det": 1000},
        "at_team": {"speed": 3.0, "det": 2000},
        "recon_team": {"speed": 4.0, "det": 3000},
        "recon_section": {"speed": 4.0, "det": 3000},
        "sniper_team": {"speed": 2.5, "det": 2500},
        "observation_post": {"speed": 1.5, "det": 4000},
        "headquarters": {"speed": 5.0, "det": 2000},
        "command_post": {"speed": 3.0, "det": 1500},
        "engineer_platoon": {"speed": 2.5, "det": 1200},
        "combat_engineer_team": {"speed": 2.5, "det": 1200},
        "obstacle_breacher_team": {"speed": 2.5, "det": 1200},
        "avlb_vehicle": {"speed": 6.0, "det": 1000},
        "logistics_unit": {"speed": 6.0, "det": 800},
    }
    d = _defaults.get(unit_type, {"speed": 3.0, "det": 1500})

    # SIDCs by side
    ident = "3" if side == "blue" else "6"
    _sidc_map = {
        "infantry_platoon": f"100{ident}1000141211000000",
        "infantry_company": f"100{ident}1000151211000000",
        "infantry_squad": f"100{ident}1000121211000000",
        "infantry_section": f"100{ident}1000131211000000",
        "infantry_team": f"100{ident}1000111211000000",
        "mech_platoon": f"100{ident}1000141211020000",
        "mech_company": f"100{ident}1000151211020000",
        "tank_platoon": f"100{ident}1000141205000000",
        "tank_company": f"100{ident}1000151205000000",
        "artillery_battery": f"100{ident}1000151303000000",
        "artillery_platoon": f"100{ident}1000141303000000",
        "mortar_section": f"100{ident}1000131308000000",
        "mortar_team": f"100{ident}1000111308000000",
        "at_team": f"100{ident}1000111204000000",
        "recon_team": f"100{ident}1000111213000000",
        "recon_section": f"100{ident}1000131213000000",
        "sniper_team": f"100{ident}1000111215000000",
        "observation_post": f"100{ident}1000111212000000",
        "headquarters": f"100{ident}1002151100000000",
        "command_post": f"100{ident}1002141100000000",
        "engineer_platoon": f"100{ident}1000141407000000",
        "combat_engineer_team": f"100{ident}1000111407000900",
        "obstacle_breacher_team": f"100{ident}1000111414000000",
        "avlb_vehicle": f"100{ident}1000111407000600",
        "logistics_unit": f"100{ident}1000141634000000",
    }

    # Default capabilities by unit type
    _default_caps = {
        "recon_team": {"is_recon": True, "has_nvg": True},
        "recon_section": {"is_recon": True, "has_nvg": True},
        "sniper_team": {"is_recon": True, "has_nvg": True},
        "observation_post": {"is_recon": True, "has_nvg": True},
        "mortar_section": {"has_mortar": True, "mortar_range_m": 4000},
        "mortar_team": {"has_mortar": True, "mortar_range_m": 3000},
        "at_team": {"has_atgm": True, "atgm_range_m": 3000},
        "artillery_battery": {"indirect_fire": True},
        "artillery_platoon": {"indirect_fire": True},
        "tank_platoon": {"has_nvg": True},
        "tank_company": {"has_nvg": True},
        "mech_platoon": {"has_nvg": True},
        "mech_company": {"has_nvg": True},
    }

    caps = dict(_default_caps.get(unit_type, {}))
    if capabilities:
        caps.update(capabilities)

    return {
        "name": name,
        "unit_type": unit_type,
        "side": side,
        "sidc": _sidc_map.get(unit_type, f"100{ident}1000141211000000"),
        "lat": lat,
        "lon": lon,
        "strength": strength,
        "ammo": ammo,
        "morale": morale,
        "move_speed_mps": move_speed_mps or d["speed"],
        "detection_range_m": detection_range_m or d["det"],
        "capabilities": caps,
        "parent_name": parent_name,
    }


def make_order(
    target_unit_names: list[str],
    order_type: str,
    original_text: str,
    side: str = "blue",
    target_location: dict | None = None,
    target_unit_id: str | None = None,
    speed: str | None = None,
    formation: str | None = None,
    inject_at_tick: int = 0,
    salvos: int | None = None,
    phases: list[dict] | None = None,
) -> dict:
    """Create an order definition dict with pre-parsed data."""
    parsed_order = {"order_type": order_type}
    if target_location:
        parsed_order["target_location"] = target_location
    if target_unit_id:
        parsed_order["target_unit_id"] = target_unit_id
    if speed:
        parsed_order["speed"] = speed
    if formation:
        parsed_order["formation"] = formation
    if salvos is not None:
        parsed_order["salvos"] = salvos

    parsed_intent = {
        "action": order_type,
    }
    if target_location:
        parsed_intent["target_location"] = target_location
        parsed_intent["destination"] = target_location
    if speed:
        parsed_intent["speed"] = speed
    if formation:
        parsed_intent["suggested_formation"] = formation

    result = {
        "target_unit_names": target_unit_names,
        "order_type": order_type,
        "original_text": original_text,
        "issued_by_side": side,
        "parsed_order": parsed_order,
        "parsed_intent": parsed_intent,
        "inject_at_tick": inject_at_tick,
    }
    if phases:
        result["parsed_order"]["phases"] = phases

    return result


def make_raw_order(
    target_unit_names: list[str],
    original_text: str,
    side: str = "blue",
    inject_at_tick: int = 0,
    expected_classification: str | None = None,
    expected_order_type: str | None = None,
    expected_language: str | None = None,
    expected_locations: list[str] | None = None,
    expected_model_tier: str | None = None,
) -> dict:
    """Create a raw text order for LLM pipeline testing.
    
    Unlike make_order(), this does NOT include parsed_order or parsed_intent.
    The order text will be routed through the full LLM parsing pipeline
    (keyword → nano → full model).
    
    Args:
        target_unit_names: Unit names to match the order to.
        original_text: Raw natural language text (EN or RU).
        side: Issuing side.
        inject_at_tick: When to inject the order.
        expected_classification: Expected MessageClassification value.
        expected_order_type: Expected OrderType value (for commands).
        expected_language: Expected language ('en' or 'ru').
        expected_locations: Expected resolved location strings.
        expected_model_tier: Expected routing tier ('keyword', 'nano', 'full').
    """
    return {
        "target_unit_names": target_unit_names,
        "original_text": original_text,
        "issued_by_side": side,
        "inject_at_tick": inject_at_tick,
        "use_llm_pipeline": True,
        # Expected outcomes for LLM assertions
        "expected_classification": expected_classification,
        "expected_order_type": expected_order_type,
        "expected_language": expected_language,
        "expected_locations": expected_locations or [],
        "expected_model_tier": expected_model_tier,
    }


class BaseScenario(ABC):
    """Abstract base class for all tactical test scenarios."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable scenario title."""

    @property
    @abstractmethod
    def description(self) -> str:
        """What this scenario tests."""

    @property
    @abstractmethod
    def ticks(self) -> int:
        """Number of ticks to run."""

    @property
    def language(self) -> str:
        """Primary language for orders ('en' or 'ru')."""
        return "en"

    @property
    def category(self) -> str:
        """Scenario category: 'engine', 'llm', 'tactical', 'historical', 'statistical'."""
        return "engine"

    @property
    def use_llm_pipeline(self) -> bool:
        """If True, orders are routed through the real LLM parsing pipeline."""
        return False

    @property
    def statistical_runs(self) -> int:
        """Number of times to run for statistical scenarios. 1 = single run."""
        return 1

    def build_scenario_data(self) -> dict:
        """Return Scenario model fields."""
        return {
            "title": self.name,
            "description": self.description,
            "map_center": {"lat": 49.058, "lon": 4.495},
            "map_zoom": 13,
            "terrain_meta": {"regions": []},
            "objectives": {},
            "environment": {
                "weather": "clear",
                "visibility_km": 5.0,
                "time_of_day": "morning",
                "temperature_c": 15,
                "language": self.language,
            },
            "grid_settings": {
                "origin_lat": GRID_ORIGIN_LAT,
                "origin_lon": GRID_ORIGIN_LON,
                "orientation_deg": 0,
                "base_square_size_m": GRID_SQUARE_SIZE_M,
                "columns": GRID_COLUMNS,
                "rows": GRID_ROWS,
                "labeling_scheme": GRID_LABELING,
            },
        }

    @abstractmethod
    def build_units(self) -> list[dict]:
        """Return list of unit definition dicts (use _make_unit helper)."""

    @abstractmethod
    def build_orders(self, unit_ids: dict[str, str]) -> list[dict]:
        """Return list of order dicts (use make_order helper).
        unit_ids maps unit name → UUID string."""

    def build_map_objects(self) -> list[dict]:
        """Return list of MapObject creation dicts. Override if needed."""
        return []

    def build_terrain_cells(self) -> list[dict]:
        """Return list of TerrainCell creation dicts. Override if needed."""
        return []

    def build_elevation_cells(self) -> list[dict]:
        """Return list of ElevationCell creation dicts. Override if needed."""
        return []

    @abstractmethod
    def build_assertions(self) -> list[dict]:
        """Return list of assertion definitions.
        Each dict: {"type": str, "params": dict, "description": str}"""

    def build_llm_assertions(self) -> list[dict]:
        """Return LLM-specific assertions. Override for LLM scenarios."""
        return []

    def build_statistical_assertions(self) -> list[dict]:
        """Return statistical assertions for multi-run scenarios.
        Each dict: {"type": str, "params": dict, "description": str}"""
        return []
