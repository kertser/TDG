"""
Stale contact decay and expiration.

AGENTS.MD Section 8.8:
  if ticks_since_seen > STALE_THRESHOLD (10): contact.is_stale = True
  if ticks_since_seen > EXPIRE_THRESHOLD (30): delete contact
"""

from __future__ import annotations

STALE_THRESHOLD = 10
EXPIRE_THRESHOLD = 30


def process_contacts(
    contacts: list,
    current_tick: int,
) -> tuple[list, list[dict]]:
    """
    Decay stale contacts and expire old ones.

    Args:
        contacts: list of Contact ORM objects
        current_tick: current simulation tick

    Returns:
        (contacts_to_delete, event_dicts)
    """
    events = []
    to_delete = []

    for contact in contacts:
        last_seen = contact.last_seen_tick or 0
        ticks_since = current_tick - last_seen

        if ticks_since > EXPIRE_THRESHOLD:
            to_delete.append(contact)
            events.append({
                "event_type": "contact_lost",
                "text_summary": f"Lost contact with {contact.estimated_type or 'unknown'} unit",
                "payload": {
                    "contact_id": str(contact.id),
                    "ticks_since_seen": ticks_since,
                },
            })
        elif ticks_since > STALE_THRESHOLD and not contact.is_stale:
            contact.is_stale = True

    return to_delete, events


