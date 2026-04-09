"""
Defensive posture engine — handles dig-in progression over time.

Units with a "defend" task gradually improve their defensive position.
Each 3 ticks of continuous defense grants one dig-in level (max 5).
Higher dig-in levels provide increasing protection bonuses.

Entrenchment map objects and urban terrain provide additional base protection.
"""

from __future__ import annotations


# Ticks required per dig-in level
TICKS_PER_LEVEL = 3
# Maximum dig-in level (= 15 ticks to reach)
MAX_DIG_IN_LEVEL = 5


def process_defense(all_units: list, map_objects: list | None = None) -> list[dict]:
    """
    Update dig-in progression for all units in defensive posture.
    Units with task type "defend" accumulate dig_in_ticks and gain levels.

    Args:
        all_units: all Unit ORM objects (mutated in-place)
        map_objects: list of MapObject ORM objects (for entrenchment bonuses)

    Returns:
        list of dig-in event dicts
    """
    events = []

    for unit in all_units:
        if unit.is_destroyed:
            continue

        task = unit.current_task
        if not task:
            continue

        task_type = task.get("type", "")
        if task_type != "defend":
            # If unit stops defending, reset dig-in progress
            if "dig_in_ticks" in task:
                del task["dig_in_ticks"]
                task.pop("dig_in_level", None)
                unit.current_task = task
            continue

        # Increment dig-in ticks
        dig_ticks = task.get("dig_in_ticks", 0) + 1
        old_level = task.get("dig_in_level", 0)
        new_level = min(MAX_DIG_IN_LEVEL, dig_ticks // TICKS_PER_LEVEL)

        task["dig_in_ticks"] = dig_ticks
        task["dig_in_level"] = new_level
        unit.current_task = task  # mark dirty

        # Emit event on level-up
        if new_level > old_level:
            events.append({
                "event_type": "dig_in_progress",
                "actor_unit_id": unit.id,
                "text_summary": f"{unit.name} dug in (level {new_level}/{MAX_DIG_IN_LEVEL})",
                "payload": {
                    "dig_in_level": new_level,
                    "dig_in_ticks": dig_ticks,
                },
            })

    return events

