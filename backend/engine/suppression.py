"""
Suppression recovery engine.

AGENTS.MD Section 8.5:
  each tick where unit is NOT under fire:
    unit.suppression = max(0, unit.suppression - 0.05)
"""

from __future__ import annotations

import uuid

SUPPRESSION_RECOVERY_RATE = 0.05


def process_suppression_recovery(
    all_units: list,
    under_fire: set[uuid.UUID],
) -> None:
    """
    Recover suppression for units not currently under fire.
    Mutates units in-place. No events generated.
    """
    for unit in all_units:
        if unit.is_destroyed:
            continue
        if unit.id in under_fire:
            continue  # Still under fire, no recovery

        suppression = unit.suppression if unit.suppression is not None else 0.0
        if suppression > 0:
            unit.suppression = max(0.0, suppression - SUPPRESSION_RECOVERY_RATE)

