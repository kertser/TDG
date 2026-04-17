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


_DOCTRINE_STOP_WORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "your", "have",
    "will", "they", "them", "their", "then", "than", "when", "what", "where",
    "which", "при", "для", "как", "что", "это", "эти", "или", "через", "после",
    "перед", "если", "если", "будет", "нужно", "надо", "под", "над", "also",
    "only", "over", "into", "onto", "move", "order", "unit", "units", "radio",
}


def _tokenize_query(text: str) -> list[str]:
    tokens = re.findall(r"[A-Za-zА-Яа-яЁё0-9_/-]+", (text or "").lower())
    return [
        t for t in tokens
        if len(t) >= 2 and t not in _DOCTRINE_STOP_WORDS
    ]


def _split_doctrine_passages(text: str) -> list[str]:
    passages: list[str] = []
    current: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            if current:
                passages.append("\n".join(current).strip())
                current = []
            continue
        if line.startswith("### Topic:"):
            if current:
                passages.append("\n".join(current).strip())
            current = [line]
            continue
        current.append(line)
    if current:
        passages.append("\n".join(current).strip())
    return [p for p in passages if p]


def get_tactical_doctrine_excerpt(
    *,
    level: str = "brief",
    topics: list[str] | None = None,
    query: str = "",
    max_passages: int = 4,
    max_chars: int = 1600,
) -> str:
    """
    Retrieve a compact doctrine excerpt relevant to the current message.

    This is cheaper than injecting the full composed doctrine block and helps
    both local and cloud models focus on only the applicable doctrinal rules.
    """
    doctrine = get_tactical_doctrine(level=level, topics=topics)
    passages = _split_doctrine_passages(doctrine)
    if not passages:
        return doctrine[:max_chars]

    query_tokens = set(_tokenize_query(query))
    topic_tokens = {
        tok
        for topic in (topics or [])
        for tok in _tokenize_query(topic.replace("_", " "))
    }

    scored: list[tuple[float, int, str]] = []
    for idx, passage in enumerate(passages):
        lowered = passage.lower()
        score = 0.0
        overlap = sum(1 for tok in query_tokens if tok in lowered)
        topic_overlap = sum(1 for tok in topic_tokens if tok in lowered)
        score += overlap * 3.0
        score += topic_overlap * 2.0
        if "### topic: general" in lowered:
            score += 1.0
        if idx == 0:
            score += 0.5
        scored.append((score, idx, passage))

    top = sorted(scored, key=lambda item: (-item[0], item[1]))[:max_passages]
    top_sorted = sorted(top, key=lambda item: item[1])

    selected: list[str] = []
    total_chars = 0
    for _, _, passage in top_sorted:
        if total_chars >= max_chars:
            break
        remaining = max_chars - total_chars
        clipped = passage if len(passage) <= remaining else passage[: max(0, remaining - 3)].rstrip() + "..."
        if clipped:
            selected.append(clipped)
            total_chars += len(clipped) + 2

    if not selected:
        return doctrine[:max_chars]
    return "\n\n".join(selected)
