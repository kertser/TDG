"""
Session Analyzer — extract analyzable orders from a session for phrasebook mining.

Quality filters applied:
  1. Length: 8–250 chars
  2. Only command messages (not ack/report)
  3. Keyword confidence below ceiling (already well-known patterns are excluded)
  4. Terminal order status: completed / cancelled / failed
  5. Cancelled too fast (< 2 ticks alive) → likely a typo → skip
  6. Full model + very low keyword confidence → too ambiguous → skip
"""
from __future__ import annotations

MIN_TEXT_LEN     = 8
MAX_TEXT_LEN     = 250
MIN_TICKS_ALIVE  = 2
KW_CONF_CEILING  = 0.80    # already in phrasebook → skip
TICK_INTERVAL_S  = 60      # default seconds per tick


def _compute_ticks_alive(order, tick_interval_secs: int = TICK_INTERVAL_S) -> float:
    """Approximate number of ticks an order was alive before terminal status."""
    if order.issued_at is None:
        return 99
    end_time = order.completed_at or order.validated_at or order.issued_at
    diff_s = (end_time - order.issued_at).total_seconds()
    return max(0, diff_s / tick_interval_secs)


def extract_analyzable_orders(session_id: str, orders: list) -> list[dict]:
    """
    Filter and shape orders suitable for phrasebook mining.

    Returns list of dicts with keys:
        order_id, session_id, user_id, original_text,
        order_type, keyword_confidence, model_tier, outcome,
        ticks_alive, language
    """
    result = []
    for order in orders:
        text   = (order.original_text or "").strip()
        parsed = order.parsed_order or {}

        # Filter 1: length
        if not (MIN_TEXT_LEN <= len(text) <= MAX_TEXT_LEN):
            continue
        # Filter 2: only command messages
        if parsed.get("message_type") != "command":
            continue
        # Filter 3: keyword already knows this pattern
        kw_conf = float(parsed.get("keyword_confidence", 1.0))
        if kw_conf >= KW_CONF_CEILING:
            continue
        # Filter 4: only terminal statuses
        status_val = order.status.value if hasattr(order.status, "value") else str(order.status)
        if status_val not in ("completed", "cancelled", "failed"):
            continue
        # Filter 5: cancelled too fast → likely typo
        ticks_alive = _compute_ticks_alive(order)
        if status_val == "cancelled" and ticks_alive < MIN_TICKS_ALIVE:
            continue
        # Filter 6: full model + very low keyword confidence → too ambiguous
        if parsed.get("model_tier") == "full" and kw_conf < 0.15:
            continue

        result.append({
            "order_id":           str(order.id),
            "session_id":         str(session_id),
            "user_id":            str(order.issued_by_user_id) if order.issued_by_user_id else None,
            "original_text":      text,
            "order_type":         parsed.get("order_type"),
            "keyword_confidence": kw_conf,
            "model_tier":         parsed.get("model_tier"),
            "outcome":            status_val,
            "ticks_alive":        ticks_alive,
            "language":           parsed.get("language", "ru"),
        })
    return result

