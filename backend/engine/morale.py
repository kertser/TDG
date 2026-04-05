"""
Morale engine – updates morale each tick.

AGENTS.MD Section 8.6:
  morale_delta -= 0.02 × suppression
  morale_delta -= 0.05 if strength < 0.5
  morale_delta -= 0.10 if strength < 0.25
  morale_delta += 0.01 if not_in_combat
  morale_delta += 0.02 if friendly_units_nearby
  if morale < 0.15: unit breaks
"""

from __future__ import annotations

import math
import uuid

from geoalchemy2.shape import to_shape

METERS_PER_DEG_LAT = 111_320.0
METERS_PER_DEG_LON_AT_48 = 74_000.0
SUPPORT_RADIUS_M = 500.0  # friendly units within this range provide mutual support


def _distance_m(lat1, lon1, lat2, lon2):
    dlat = (lat2 - lat1) * METERS_PER_DEG_LAT
    dlon = (lon2 - lon1) * METERS_PER_DEG_LON_AT_48
    return math.sqrt(dlat * dlat + dlon * dlon)


def _get_position(unit):
    if unit.position is None:
        return None
    try:
        pt = to_shape(unit.position)
        return pt.y, pt.x
    except Exception:
        return None


def process_morale(
    all_units: list,
    under_fire: set[uuid.UUID],
) -> list[dict]:
    """
    Update morale for all units.

    Args:
        all_units: all unit ORM objects (mutated in-place)
        under_fire: set of unit IDs that took fire this tick

    Returns:
        list of morale-related event dicts
    """
    events = []

    # Pre-compute positions for proximity checks
    positions: dict[uuid.UUID, tuple[float, float]] = {}
    sides: dict[uuid.UUID, str] = {}
    for u in all_units:
        if u.is_destroyed:
            continue
        pos = _get_position(u)
        if pos:
            positions[u.id] = pos
            sides[u.id] = u.side.value if hasattr(u.side, 'value') else str(u.side)

    for unit in all_units:
        if unit.is_destroyed:
            continue

        morale = unit.morale if unit.morale is not None else 1.0
        strength = unit.strength if unit.strength is not None else 1.0
        suppression = unit.suppression if unit.suppression is not None else 0.0

        delta = 0.0

        # Suppression erodes morale
        delta -= 0.02 * suppression

        # Casualties lower morale
        if strength < 0.25:
            delta -= 0.10
        elif strength < 0.5:
            delta -= 0.05

        # In combat penalty / safe recovery
        if unit.id in under_fire:
            delta -= 0.01  # additional combat stress
        else:
            delta += 0.01  # slow recovery when safe

        # Mutual support: friendly units nearby
        unit_side = unit.side.value if hasattr(unit.side, 'value') else str(unit.side)
        unit_pos = positions.get(unit.id)
        if unit_pos:
            for other_id, other_pos in positions.items():
                if other_id == unit.id:
                    continue
                if sides.get(other_id) != unit_side:
                    continue
                if _distance_m(unit_pos[0], unit_pos[1], other_pos[0], other_pos[1]) <= SUPPORT_RADIUS_M:
                    delta += 0.02
                    break  # only one bonus

        # Apply delta
        new_morale = max(0.0, min(1.0, morale + delta))
        unit.morale = new_morale

        # Check for morale break
        if new_morale < 0.15 and morale >= 0.15:
            # Unit breaks — stop executing orders, will retreat
            unit.current_task = None
            events.append({
                "event_type": "morale_break",
                "actor_unit_id": unit.id,
                "text_summary": f"{unit.name} morale broken! Unit routing.",
                "payload": {"morale": round(new_morale, 3), "strength": round(strength, 3)},
            })

    return events

