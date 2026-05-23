"""
Intent Cascade Engine (#8)

When a HQ unit receives a move/attack/defend order, this module propagates
implied sub-tasks to its immediate subordinates, emulating the Russian (and NATO)
three-level C2 delegation pattern:

  HQ receives order  →  HQ current_task set (by _process_orders)
  Intent cascade      →  Subordinates get implied tasks on same tick

Rules:
  - HQ move    → subordinates get "move" to same destination, slow speed
  - HQ attack  → subordinates get "attack" with same target, echelon formation
  - HQ defend  → subordinates get "defend" at their current position (no target change)
  - HQ withdraw → subordinates get "disengage" / fast retreat
  - HQ halt    → subordinates get "halt" (already cleared, but ensure they stop)

  Subordinates with existing explicit orders (issued_this_tick=True flag or explicit
  order in the current orders batch) are NOT overridden — explicit player intent wins.

Caveats:
  - Cascade is 1 level deep (HQ → direct reports, not grandchildren).
  - Cascade only triggers when HQ task is NEW this tick (has order_id different from
    the previous tick's order_id, checked via hq.current_task.order_id vs
    hq.current_task.cascade_tick).
  - Does NOT call LLM — fully deterministic.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# How many ticks back we look to decide "this is a new order"
_CASCADE_TICK_KEY = "_cascade_issued_tick"

# HQ unit types eligible to cascade intent
HQ_UNIT_TYPES = {
    "headquarters", "command_post",
    "infantry_battalion", "infantry_company",
    "tank_company", "mech_company",
}

# Mapping: HQ task type → subordinate implied task type
_TASK_MAP = {
    "move": "move",
    "advance": "move",
    "attack": "attack",
    "engage": "attack",
    "defend": "defend",
    "withdraw": "disengage",
    "disengage": "disengage",
    "halt": "halt",
}

# Formation suggestions per inherited task (overrideable by HQ task.formation)
_DEFAULT_FORMATION = {
    "move": "column",
    "advance": "wedge",
    "attack": "line",
    "defend": "line",
    "disengage": "column",
    "halt": None,
}


def process_intent_cascade(
    all_units: list,
    tick: int,
    explicitly_ordered_unit_ids: set[str],
) -> list[dict]:
    """
    Propagate HQ orders to subordinates.

    Args:
        all_units: all Unit ORM objects for the session (already loaded this tick).
        tick: current tick number.
        explicitly_ordered_unit_ids: set of unit ID strings that received explicit
            player orders this tick (cascade will not override these).

    Returns:
        list of event dicts describing each cascade assignment.
    """
    events: list[dict] = []

    # Index units by parent_unit_id for quick child lookup
    parent_to_children: dict[str, list] = {}
    for u in all_units:
        if u.is_destroyed:
            continue
        if u.parent_unit_id:
            pid = str(u.parent_unit_id)
            parent_to_children.setdefault(pid, []).append(u)

    for hq in all_units:
        if hq.is_destroyed:
            continue
        if hq.unit_type not in HQ_UNIT_TYPES:
            continue
        task = hq.current_task
        if not task:
            continue

        task_type = task.get("type", "")
        subordinate_task_type = _TASK_MAP.get(task_type)
        if subordinate_task_type is None:
            continue  # not a cascadable order

        # Only cascade on the tick the order was issued (check order_id sentinel)
        order_id = task.get("order_id")
        last_cascade_order = task.get(_CASCADE_TICK_KEY)
        if order_id and last_cascade_order == order_id:
            continue  # already cascaded this order

        children = parent_to_children.get(str(hq.id), [])
        if not children:
            continue

        cascaded_count = 0
        for child in children:
            if str(child.id) in explicitly_ordered_unit_ids:
                continue  # player-issued orders win
            if child.is_destroyed:
                continue

            # Build child task
            child_task = _build_child_task(task, subordinate_task_type, hq, child)
            child.current_task = child_task
            cascaded_count += 1
            events.append({
                "event_type": "order_issued",
                "actor_unit_id": child.id,
                "text_summary": (
                    f"{child.name} received cascaded {subordinate_task_type} order from {hq.name}"
                ),
                "payload": {
                    "cascade": True,
                    "from_hq": str(hq.id),
                    "hq_name": hq.name,
                    "task": child_task,
                },
                "visibility": (
                    hq.side.value if hasattr(hq.side, "value") else str(hq.side)
                ),
            })

        if cascaded_count > 0:
            # Mark order as already cascaded so we don't re-cascade on next tick
            new_task = dict(task)
            new_task[_CASCADE_TICK_KEY] = order_id or tick
            hq.current_task = new_task
            logger.debug(
                "Intent cascade: HQ %s → %d subordinates (task=%s)",
                hq.name, cascaded_count, task_type,
            )

    return events


def _build_child_task(
    hq_task: dict,
    child_task_type: str,
    hq,
    child,
) -> dict:
    """Construct an implied subordinate task from the HQ task."""
    task: dict = {"type": child_task_type, "cascaded": True, "from_hq": str(hq.id)}

    if child_task_type == "halt":
        return task

    if child_task_type == "disengage":
        task["disengaging"] = True
        return task

    if child_task_type == "defend":
        # Defend in place — preserve child's current position, just set task type
        return task

    # move or attack: inherit HQ target
    target_loc = hq_task.get("target_location")
    if target_loc:
        task["target_location"] = dict(target_loc)

    target_uid = hq_task.get("target_unit_id")
    if target_uid:
        task["target_unit_id"] = target_uid

    task["speed"] = hq_task.get("speed", "slow")
    task["formation"] = hq_task.get("formation") or _DEFAULT_FORMATION.get(child_task_type, "wedge")

    return task

