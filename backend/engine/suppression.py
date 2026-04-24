"""
Suppression recovery engine.

AGENTS.MD Section 8.5:
  each tick where unit is NOT under fire:
    unit.suppression = max(0, unit.suppression - recovery_rate)

  recovery_rate depends on posture:
    - attacking / advancing: 0.02  (harder to recover while moving under fire)
    - all others (stationary/defending/idle): 0.05
"""

from __future__ import annotations

import uuid

# Standard recovery for stationary / defending / idle units
SUPPRESSION_RECOVERY_RATE = 0.05

# Slower recovery for units actively advancing while suppressed
# Attacking soldiers cannot easily take cover or regroup while moving
SUPPRESSION_RECOVERY_RATE_ATTACKING = 0.02

# Task types that count as "attacking advance" for suppression recovery purposes
ATTACK_TASK_TYPES = {"attack", "engage", "advance", "airstrike"}

# Unit types exempt from the attack penalty (they fire in place, not advancing)
# Import lazily to avoid circular dependency — checked by string comparison
_ARTILLERY_UNIT_TYPES = {
    "artillery_battery",
    "artillery_platoon",
    "mortar_section",
    "mortar_team",
}


def process_suppression_recovery(
    all_units: list,
    under_fire: set[uuid.UUID],
) -> None:
    """
    Recover suppression for units not currently under fire.

    Attacking units recover at half the rate of stationary/defending units:
    a unit advancing under fire cannot easily take cover or reorganise.

    Mutates units in-place. No events generated.
    """
    for unit in all_units:
        if unit.is_destroyed:
            continue
        if unit.id in under_fire:
            continue  # Still under fire, no recovery

        suppression = unit.suppression if unit.suppression is not None else 0.0
        if suppression > 0:
            # Choose recovery rate based on posture
            task = unit.current_task or {}
            task_type = task.get("type", "")
            is_artillery = unit.unit_type in _ARTILLERY_UNIT_TYPES
            is_auto_return = task.get("auto_return_fire", False)

            if (
                not is_artillery
                and not is_auto_return
                and task_type in ATTACK_TASK_TYPES
            ):
                rate = SUPPRESSION_RECOVERY_RATE_ATTACKING
            else:
                rate = SUPPRESSION_RECOVERY_RATE

            unit.suppression = max(0.0, suppression - rate)
