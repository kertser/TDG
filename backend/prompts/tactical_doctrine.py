"""
Tactical doctrine reference for LLM context injection.

Single source of truth: `FIELD_MANUAL.md`.

This loader supports:
- full doctrine slices for deep reasoning
- concise brief doctrine for order parsing
- topic-scoped doctrine snippets so prompts receive only relevant context
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)


def _find_field_manual() -> Path:
    """Find FIELD_MANUAL.md by walking up from this file's directory."""
    current = Path(__file__).resolve()
    for ancestor in [current.parent.parent.parent, current.parent.parent]:
        candidate = ancestor / "FIELD_MANUAL.md"
        if candidate.exists():
            return candidate
    cwd_candidate = Path.cwd() / "FIELD_MANUAL.md"
    if cwd_candidate.exists():
        return cwd_candidate
    raise FileNotFoundError(
        "FIELD_MANUAL.md not found. Searched from: " + str(current.parent)
    )


def _extract_section(text: str, start_marker: str, end_marker: str) -> str:
    """Extract text between two HTML comment markers."""
    start_idx = text.find(start_marker)
    if start_idx == -1:
        raise ValueError(f"Marker not found in FIELD_MANUAL.md: {start_marker}")
    start_idx += len(start_marker)

    end_idx = text.find(end_marker, start_idx)
    if end_idx == -1:
        raise ValueError(f"Marker not found in FIELD_MANUAL.md: {end_marker}")

    return text[start_idx:end_idx].strip()


def _extract_topic_sections(text: str) -> dict[str, str]:
    """Extract all topic-scoped doctrine snippets from FIELD_MANUAL.md."""
    topic_pattern = re.compile(
        r"<!-- DOCTRINE:TOPIC:([A-Z0-9_]+):START -->(.*?)<!-- DOCTRINE:TOPIC:\1:END -->",
        re.DOTALL,
    )
    topics: dict[str, str] = {}
    for match in topic_pattern.finditer(text):
        key = match.group(1).lower()
        topics[key] = match.group(2).strip()
    return topics


def _normalize_topic(topic: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", (topic or "").strip().lower())


def _compose_topic_doctrine(
    header: str,
    base_text: str,
    topic_map: dict[str, str],
    topics: list[str] | None,
) -> str:
    """Compose doctrine text with only the requested topics plus general context."""
    if not topics:
        return f"{header}\n\n{base_text}"

    ordered_topics: list[str] = []
    for topic in ["general", *topics]:
        normalized = _normalize_topic(topic)
        if normalized and normalized not in ordered_topics:
            ordered_topics.append(normalized)

    blocks = []
    for topic in ordered_topics:
        snippet = topic_map.get(topic)
        if snippet:
            blocks.append(f"### Topic: {topic.replace('_', ' ').title()}\n{snippet}")

    if not blocks:
        return f"{header}\n\n{base_text}"

    return f"{header}\n\n" + "\n\n".join(blocks)


def _load_doctrine() -> tuple[str, str, dict[str, str]]:
    """Load full, brief, and topic-scoped doctrine from FIELD_MANUAL.md."""
    fm_path = _find_field_manual()
    raw = fm_path.read_text(encoding="utf-8")

    full = _extract_section(
        raw,
        "<!-- DOCTRINE:FULL:START -->",
        "<!-- DOCTRINE:FULL:END -->",
    )
    brief = _extract_section(
        raw,
        "<!-- DOCTRINE:BRIEF:START -->",
        "<!-- DOCTRINE:BRIEF:END -->",
    )
    topics = _extract_topic_sections(raw)

    logger.info(
        "Loaded tactical doctrine from %s (full=%d chars, brief=%d chars, topics=%d)",
        fm_path.name,
        len(full),
        len(brief),
        len(topics),
    )
    return full, brief, topics


_FALLBACK_FULL = """
Key principles:
- Fire and maneuver: one element suppresses while another moves.
- Combined arms: infantry clears close terrain, armor in open terrain, artillery suppresses.
- Recon finds the enemy, does not fight decisively. Protect flanks. Coordinate fires.
""".strip()

_FALLBACK_BRIEF = """
- Fire and maneuver.
- Recon forward, artillery in support, engineers enable mobility, logistics sustain.
""".strip()

_FALLBACK_TOPICS = {
    "general": "- Use combined arms, maintain security, and coordinate maneuver with fires.",
    "offense": "- Offensive action combines suppression, maneuver, and flank security.",
    "defense": "- Defensive action preserves observation, cover, and planned disengagement routes.",
    "fires": "- Fire support suppresses and shifts with maneuver; cease when friendlies close.",
    "recon": "- Recon screens, observes, and reports; avoid decisive engagement.",
    "engineers": "- Engineers breach, emplace obstacles, construct positions, and deploy bridges.",
    "logistics": "- Logistics follows supported units and sustains them in protected positions.",
    "aviation": "- Use air mobility for insertion/extraction and air reconnaissance for screening.",
    "map_objects": "- Bridges, minefields, wire, smoke, bunkers, and roadblocks change routes and tactics.",
    "split_merge": "- Split to create a detached element; merge to recombine combat power when close.",
}


try:
    _FULL_RAW, _BRIEF_RAW, _TOPIC_MAP = _load_doctrine()
except (FileNotFoundError, ValueError) as exc:
    logger.warning(
        "Failed to load doctrine from FIELD_MANUAL.md: %s. Using fallback doctrine.",
        exc,
    )
    _FULL_RAW, _BRIEF_RAW, _TOPIC_MAP = _FALLBACK_FULL, _FALLBACK_BRIEF, _FALLBACK_TOPICS


def get_tactical_doctrine(level: str = "full", topics: list[str] | None = None) -> str:
    """
    Get tactical doctrine text for prompt injection.

    Args:
        level: "full" or "brief".
        topics: optional topic keys such as ["fires", "recon", "engineers"].

    Returns:
        Doctrine text string.
    """
    if level == "brief":
        return _compose_topic_doctrine(
            "## Tactical Reference (Brief)",
            _BRIEF_RAW,
            _TOPIC_MAP,
            topics,
        )
    return _compose_topic_doctrine(
        "## Tactical Doctrine Reference",
        _FULL_RAW,
        _TOPIC_MAP,
        topics,
    )


def available_doctrine_topics() -> list[str]:
    """Return the available topic keys from FIELD_MANUAL.md."""
    return sorted(_TOPIC_MAP.keys())
