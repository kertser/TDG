"""
Objective Control Engine

Processes territorial objectives for the session:
  - Reads objectives from scenario.objectives.objectives_list (or .objectives)
  - Checks which side has units within objective radius
  - Emits objective_captured / objective_contested events when control changes
  - Provides fast deterministic (no-LLM) victory check
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_METERS_PER_DEG_LAT = 111_320.0
_METERS_PER_DEG_LON_48 = 74_000.0

# Minimum friendly units inside the objective radius to count as controlling it
_CONTROL_UNIT_COUNT = 1

# Session-level objective state: session_id_str → {obj_id: "blue"|"red"|"contested"|None}
_obj_control_cache: dict[str, dict[str, str | None]] = {}


def _get_obj_state(session_id_str: str) -> dict[str, str | None]:
    """Return (and auto-create) mutable objective control state for a session."""
    return _obj_control_cache.setdefault(session_id_str, {})


def clear_objective_cache(session_id_str: str | None = None) -> None:
    """Clear objective control cache (call on session reset)."""
    if session_id_str:
        _obj_control_cache.pop(session_id_str, None)
    else:
        _obj_control_cache.clear()


def process_objective_control(
    all_units: list,
    scenario,
    grid_service,
    tick: int,
    session_id_str: str = "",
) -> list[dict]:
    """
    Evaluate which side controls each scenario objective this tick.
    Emits events when control changes (captured / contested / lost).

    Returns:
        list of event dicts
    """
    events: list[dict] = []
    if scenario is None:
        return events

    objectives = _extract_objectives(scenario)
    if not objectives:
        return events

    prev_state = _get_obj_state(session_id_str)

    # Pre-build unit position lists per side
    unit_positions: dict[str, list[tuple[float, float]]] = {}
    try:
        from geoalchemy2.shape import to_shape
    except ImportError:
        return events

    for u in all_units:
        if u.is_destroyed or u.position is None:
            continue
        side = u.side.value if hasattr(u.side, "value") else str(u.side)
        try:
            pt = to_shape(u.position)
            unit_positions.setdefault(side, []).append((pt.y, pt.x))
        except Exception:
            pass

    for obj in objectives:
        obj_id = str(obj.get("id") or obj.get("name") or "?")
        name = obj.get("name") or obj_id
        obj_lat = float(obj.get("lat", 0.0))
        obj_lon = float(obj.get("lon", 0.0))
        radius = float(obj.get("radius_m", 300.0))

        blue_count = _count_units_in_radius(unit_positions.get("blue", []), obj_lat, obj_lon, radius)
        red_count = _count_units_in_radius(unit_positions.get("red", []), obj_lat, obj_lon, radius)

        if blue_count >= _CONTROL_UNIT_COUNT and red_count >= _CONTROL_UNIT_COUNT:
            new_owner: str | None = "contested"
        elif blue_count >= _CONTROL_UNIT_COUNT:
            new_owner = "blue"
        elif red_count >= _CONTROL_UNIT_COUNT:
            new_owner = "red"
        else:
            new_owner = prev_state.get(obj_id)  # no units → ownership unchanged

        old_owner = prev_state.get(obj_id)
        if new_owner is not None and new_owner != old_owner:
            prev_state[obj_id] = new_owner

            if new_owner == "contested":
                events.append({
                    "event_type": "objective_contested",
                    "text_summary": f"Objective '{name}' is being contested",
                    "visibility": "all",
                    "payload": {
                        "objective_id": obj_id,
                        "objective_name": name,
                        "blue_units": blue_count,
                        "red_units": red_count,
                        "lat": obj_lat,
                        "lon": obj_lon,
                        "tick": tick,
                    },
                })
            else:
                prev_label = old_owner or "neutral"
                events.append({
                    "event_type": "objective_captured",
                    "text_summary": f"{new_owner.upper()} captured objective '{name}'",
                    "visibility": "all",
                    "payload": {
                        "objective_id": obj_id,
                        "objective_name": name,
                        "new_owner": new_owner,
                        "prev_owner": prev_label,
                        "lat": obj_lat,
                        "lon": obj_lon,
                        "tick": tick,
                    },
                })
            logger.debug(
                "Objective '%s': %s → %s (blue=%d, red=%d)",
                name, old_owner, new_owner, blue_count, red_count,
            )

    return events


def check_deterministic_victory(
    all_units: list,
    scenario,
    tick: int,
    obj_events: list[dict] | None = None,
    session_id_str: str = "",
) -> dict | None:
    """
    Fast deterministic victory check — no LLM.

    Evaluated conditions (in order):
      1. All units on one side destroyed → other side wins
      2. If scenario.objectives.objectives_to_win is set:
         side holding >= that many objectives wins

    Returns:
        {"winner": "blue"|"red", "summary": str, "detail": str}  or  None
    """
    if tick < 3:
        return None  # too early

    def _side(u) -> str:
        return u.side.value if hasattr(u.side, "value") else str(u.side)

    # Separate alive / ever-existed by side
    blue_alive = [u for u in all_units if not u.is_destroyed and _side(u) == "blue"]
    red_alive  = [u for u in all_units if not u.is_destroyed and _side(u) == "red"]
    blue_ever  = [u for u in all_units if _side(u) == "blue"]
    red_ever   = [u for u in all_units if _side(u) == "red"]

    # ── Condition 1: total annihilation ──────────────────────────────
    if blue_alive and red_ever and not red_alive:
        return {
            "winner": "blue",
            "summary": "Blue forces eliminated all Red units",
            "detail": f"All {len(red_ever)} Red units destroyed by tick {tick}",
        }
    if red_alive and blue_ever and not blue_alive:
        return {
            "winner": "red",
            "summary": "Red forces eliminated all Blue units",
            "detail": f"All {len(blue_ever)} Blue units destroyed by tick {tick}",
        }

    # ── Condition 2: objective score threshold ───────────────────────
    if scenario is None:
        return None
    objectives = _extract_objectives(scenario)
    if not objectives:
        return None

    win_threshold: int | None = None
    if scenario.objectives and isinstance(scenario.objectives, dict):
        win_threshold = scenario.objectives.get("objectives_to_win")
    if not win_threshold or not isinstance(win_threshold, int) or win_threshold <= 0:
        return None

    obj_state = _get_obj_state(session_id_str)
    blue_held = sum(1 for v in obj_state.values() if v == "blue")
    red_held  = sum(1 for v in obj_state.values() if v == "red")
    total = len(objectives)

    if blue_held >= win_threshold:
        return {
            "winner": "blue",
            "summary": f"Blue controls {blue_held}/{total} objectives (needed {win_threshold})",
            "detail": f"Objective victory at tick {tick}",
        }
    if red_held >= win_threshold:
        return {
            "winner": "red",
            "summary": f"Red controls {red_held}/{total} objectives (needed {win_threshold})",
            "detail": f"Objective victory at tick {tick}",
        }

    return None


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _extract_objectives(scenario) -> list[dict]:
    """
    Extract objective list from scenario.objectives JSONB.

    Supported formats:
      - {"objectives_list": [{id, name, lat, lon, radius_m}, ...]}
      - {"objectives": [...]}
      - bare list: [{id, name, lat, lon}, ...]
    """
    if scenario is None or not scenario.objectives:
        return []
    obj = scenario.objectives
    if isinstance(obj, list):
        return [o for o in obj if isinstance(o, dict) and "lat" in o]
    if isinstance(obj, dict):
        lst = obj.get("objectives_list") or obj.get("objectives")
        if isinstance(lst, list):
            return [o for o in lst if isinstance(o, dict) and "lat" in o]
    return []


def _count_units_in_radius(
    positions: list[tuple[float, float]],
    lat: float,
    lon: float,
    radius_m: float,
) -> int:
    count = 0
    for u_lat, u_lon in positions:
        dlat = (u_lat - lat) * _METERS_PER_DEG_LAT
        dlon = (u_lon - lon) * _METERS_PER_DEG_LON_48
        if (dlat * dlat + dlon * dlon) ** 0.5 <= radius_m:
            count += 1
    return count

