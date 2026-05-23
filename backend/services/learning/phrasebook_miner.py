"""
Phrasebook Miner — statistical mining of phrasebook proposals from session order data.

Includes:
- Text normalization
- Cross-session / cross-user aggregation
- LLM agreement check
- TOML proposal generation
- Optional LLM judge for quality validation

Anti-poisoning safeguards:
  - Minimum 5 distinct sessions
  - Minimum 3 distinct users
  - LLM agreement ≥ 85%
  - Minimum 3 example texts
"""
from __future__ import annotations

import re
from collections import defaultdict, Counter
from typing import Optional
import logging

logger = logging.getLogger(__name__)

MIN_CROSS_SESSIONS  = 5
MIN_UNIQUE_USERS    = 3
LLM_AGREEMENT_RATE  = 0.85
MIN_EXAMPLES        = 3
AUTO_REJECT_CONF    = 0.40

# Regex patterns for normalisation — strip specifics, leave command structure
_COORD_RE     = re.compile(r'\b\d{1,2}\.\d+\b|\b[A-ZА-Я]\d{1,2}(-\d)?\b', re.IGNORECASE)
_UNIT_NAME_RE = re.compile(
    r'\b(1st|2nd|3rd|\d+th|первый|второй|взвод|отдел|squad|platoon|section)\s+\w+',
    re.IGNORECASE,
)
_NUM_RE       = re.compile(r'\b\d+\b')
_SNAIL_RE     = re.compile(r'\b[A-ZА-Я]\d+-\d(-\d)*\b', re.IGNORECASE)


def _normalize(text: str) -> str:
    """Strip coordinates, unit names, and numbers — leave command skeleton."""
    t = text.lower().strip()
    t = _SNAIL_RE.sub("[LOC]", t)
    t = _COORD_RE.sub("[LOC]", t)
    t = _UNIT_NAME_RE.sub("[UNIT]", t)
    t = _NUM_RE.sub("[N]", t)
    return re.sub(r'\s+', ' ', t)


def mine_proposals(per_session_orders: list[list[dict]]) -> list[dict]:
    """
    Mine phrasebook candidates from cross-session order data.

    per_session_orders: list of lists — one inner list per session,
    each element is a dict from session_analyzer.extract_analyzable_orders().

    Returns list of raw proposal dicts (before LLM judge).
    """
    # Group by (language, normalized_text)
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for session_orders in per_session_orders:
        for o in session_orders:
            key = (o.get("language", "ru"), _normalize(o["original_text"]))
            groups[key].append(o)

    proposals = []
    for (lang, norm_text), entries in groups.items():
        session_ids = list({e["session_id"] for e in entries})
        user_ids    = list({e["user_id"] for e in entries if e.get("user_id")})

        # Anti-poisoning: minimum cross-session / cross-user counts
        if len(session_ids) < MIN_CROSS_SESSIONS:
            continue
        if len(user_ids) < MIN_UNIQUE_USERS:
            continue

        # LLM agreement: most common order_type must dominate
        type_counts = Counter(e["order_type"] for e in entries if e.get("order_type"))
        if not type_counts:
            continue
        most_type, most_count = type_counts.most_common(1)[0]
        agreement = most_count / len(entries)
        if agreement < LLM_AGREEMENT_RATE:
            continue   # LLM disagrees → ambiguous or noisy

        # Pick best examples (prefer completed, then longer texts)
        best_entries = sorted(
            [e for e in entries if e.get("order_type") == most_type],
            key=lambda e: (e["outcome"] == "completed", len(e["original_text"])),
            reverse=True,
        )[:5]
        if len(best_entries) < MIN_EXAMPLES:
            continue

        confidence = min(agreement, len(session_ids) / 10.0)

        proposed_toml = (
            f"[[case]]\n"
            f"input = {repr(best_entries[0]['original_text'])}\n"
            f"order_type = \"{most_type}\"\n"
            f"language = \"{lang}\"\n"
            f"# Auto-proposed ({len(session_ids)} sessions, {len(user_ids)} users). "
            f"Review before applying.\n"
        )

        proposals.append({
            "proposal_type":       "phrasebook_case",
            "target_file":         "backend/data/order_phrasebook.toml",
            "proposed_text":       proposed_toml,
            "rationale": (
                f"Pattern seen in {len(session_ids)} sessions from {len(user_ids)} users. "
                f"LLM classified as '{most_type}' {agreement:.0%} of the time. "
                f"Keyword conf avg: "
                f"{sum(e['keyword_confidence'] for e in entries)/len(entries):.2f} (below ceiling)."
            ),
            "source_order_ids":    [e["order_id"] for e in entries],
            "example_texts":       [e["original_text"] for e in best_entries],
            "confidence":          confidence,
            "cross_session_count": len(session_ids),
            "unique_user_count":   len(user_ids),
            "session_ids":         session_ids,
            "user_ids":            user_ids,
            "language":            lang,
        })

    return proposals


async def llm_judge(proposal: dict, llm_client) -> dict:
    """
    Use GPT-4o-mini to validate whether a proposed phrasebook candidate is
    a legitimate general military command phrase.

    Returns proposal dict enriched with llm_judge_score and llm_judge_reasoning.
    """
    import json
    try:
        examples = proposal.get("example_texts", [])
        order_type_line = proposal.get("proposed_text", "").split("order_type = ")
        order_type_str = order_type_line[1].split("\n")[0].strip('"') if len(order_type_line) > 1 else "?"

        prompt = (
            f"Military command pattern validation.\n\n"
            f"Pattern extracted from training exercise logs:\n"
            f'Main example: "{examples[0] if examples else ""}"\n'
            f"Other examples: {examples[1:3]}\n"
            f"Proposed classification: {order_type_str}\n\n"
            f"Is this a legitimate general military command phrase that should be recognized by a parser?\n\n"
            f"REJECT if:\n"
            f"- Typo, nonsense, or incomplete phrase\n"
            f"- Acknowledgment or status report, not a command\n"
            f"- Single player's idiosyncratic expression (not general military language)\n"
            f"- Highly ambiguous with no clear tactical meaning\n\n"
            f'Respond ONLY as JSON: {{"is_valid": true/false, "score": 0.0-1.0, "reasoning": "1-2 sentences"}}'
        )

        resp = await llm_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            max_tokens=120,
            temperature=0.1,
        )
        data = json.loads(resp.choices[0].message.content)
        return {
            **proposal,
            "llm_judge_score":     float(data.get("score", 0.0)),
            "llm_judge_reasoning": str(data.get("reasoning", "")),
        }
    except Exception as e:
        logger.warning("LLM judge failed for proposal: %s", e)
        return {**proposal, "llm_judge_score": None, "llm_judge_reasoning": f"Judge failed: {e}"}

