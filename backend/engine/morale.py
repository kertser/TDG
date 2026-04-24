"""
Morale engine – updates morale each tick.

AGENTS.MD Section 8.6:
  morale_delta -= 0.02 × suppression
  morale_delta -= 0.05 if strength < 0.5
  morale_delta -= 0.10 if strength < 0.25
  morale_delta += 0.01 if not_in_combat
  morale_delta += 0.02 if friendly_units_nearby
  morale_delta += 0.05 if enemy destroyed nearby
  morale_delta -= 0.01 if march_ticks > 10 (fatigue)
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
    tick_events: list[dict] | None = None,
) -> list[dict]:
    """
    Update morale for all units.

    Args:
        all_units: all unit ORM objects (mutated in-place)
        under_fire: set of unit IDs that took fire this tick
        tick_events: events generated this tick (for enemy-destroyed morale boost)

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

    # Collect enemy-destroyed events for morale boost
    destroyed_enemy_positions = []  # (lat, lon, destroyed_side)
    if tick_events:
        for evt in tick_events:
            if evt.get("event_type") == "unit_destroyed":
                tid = evt.get("target_unit_id")
                if tid:
                    for u in all_units:
                        if u.id == tid:
                            pos = _get_position(u)
                            side = u.side.value if hasattr(u.side, 'value') else str(u.side)
                            if pos:
                                destroyed_enemy_positions.append((pos[0], pos[1], side))
                            break

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
        task = unit.current_task
        task_type = task.get("type") if task else None
        is_auto_return = (task or {}).get("auto_return_fire", False)

        if unit.id in under_fire:
            delta -= 0.01  # additional combat stress
            # Extra assault stress: advancing into fire without prepared positions
            if task_type in ("attack", "engage") and not is_auto_return:
                delta -= 0.01
        else:
            delta += 0.01  # slow recovery when safe

        # Defender steadiness: fighting on known ground, in prepared positions
        # Small but compounds — a 10-tick defence builds +0.10 morale advantage
        if task_type == "defend":
            delta += 0.01

        # ── Unit rest/recovery (strength + morale boost when idle & safe) ──
        is_resting = (
            unit.id not in under_fire
            and (not task or task.get("type") in ("defend", None))
        )
        if is_resting:
            # Track rest ticks
            caps = unit.capabilities or {}
            rest_ticks = caps.get("rest_ticks", 0) + 1
            new_caps = dict(caps)
            new_caps["rest_ticks"] = rest_ticks
            unit.capabilities = new_caps

            # Recover strength (not full personnel — represents reorganization/treatment)
            if strength < 1.0:
                if rest_ticks >= 5:
                    unit.strength = min(1.0, strength + 0.008)  # boosted recovery
                else:
                    unit.strength = min(1.0, strength + 0.003)  # slow recovery

            # Boosted morale recovery while resting
            if rest_ticks >= 5:
                delta += 0.01  # additional morale recovery
        else:
            # Reset rest counter when doing things
            caps = unit.capabilities or {}
            if caps.get("rest_ticks", 0) > 0:
                new_caps = dict(caps)
                new_caps["rest_ticks"] = 0
                unit.capabilities = new_caps

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

        # ── Enemy destroyed nearby → morale boost ──
        if unit_pos and destroyed_enemy_positions:
            for d_lat, d_lon, d_side in destroyed_enemy_positions:
                if d_side != unit_side:  # destroyed unit is enemy
                    dist = _distance_m(unit_pos[0], unit_pos[1], d_lat, d_lon)
                    if dist <= 2000:  # within 2km
                        delta += 0.05  # significant morale boost
                        break  # one bonus per tick

        # ── Long march fatigue → morale erosion ──
        if task and task.get("type") in ("move", "advance"):
            march_ticks = task.get("march_ticks", 0) + 1
            task["march_ticks"] = march_ticks
            unit.current_task = task  # mark dirty
            if march_ticks > 10:
                delta -= 0.01  # fatigue from sustained marching
        elif task and "march_ticks" in task:
            # Reset march counter when not moving
            del task["march_ticks"]
            unit.current_task = task

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

