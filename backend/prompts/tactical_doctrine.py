"""
Tactical doctrine reference for LLM context injection.

**SINGLE SOURCE OF TRUTH: FIELD_MANUAL.md (Appendix C)**

This module reads tactical doctrine from FIELD_MANUAL.md at import time.
It extracts text between marker comments:
  - <!-- DOCTRINE:FULL:START --> / <!-- DOCTRINE:FULL:END -->   → Red AI commander
  - <!-- DOCTRINE:BRIEF:START --> / <!-- DOCTRINE:BRIEF:END --> → Order parser

NEVER put doctrine text directly in this file. Edit FIELD_MANUAL.md instead.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Locate FIELD_MANUAL.md relative to project root ──────────────────────────


def _find_field_manual() -> Path:
    """Find FIELD_MANUAL.md by walking up from this file's directory."""
    # This file is at backend/prompts/tactical_doctrine.py
    # FIELD_MANUAL.md is at the project root
    current = Path(__file__).resolve()
    for ancestor in [current.parent.parent.parent, current.parent.parent]:
        candidate = ancestor / "FIELD_MANUAL.md"
        if candidate.exists():
            return candidate
    # Fallback: check CWD
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


def _load_doctrine() -> tuple[str, str]:
    """
    Load full and brief doctrine text from FIELD_MANUAL.md.

    Returns:
        (full_doctrine, brief_doctrine) tuple of strings.
    """
    try:
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

        # Prepend a header for LLM context
        full_with_header = "## TACTICAL DOCTRINE REFERENCE\n\n" + full
        brief_with_header = "## Tactical Reference (Brief)\n\n" + brief

        logger.info(
            "Loaded tactical doctrine from %s (full=%d chars, brief=%d chars)",
            fm_path.name,
            len(full_with_header),
            len(brief_with_header),
        )
        return full_with_header, brief_with_header

    except (FileNotFoundError, ValueError) as e:
        logger.warning(
            "Failed to load doctrine from FIELD_MANUAL.md: %s. "
            "Using fallback minimal doctrine.",
            e,
        )
        return _FALLBACK_FULL, _FALLBACK_BRIEF


# ── Minimal fallback (only used if FIELD_MANUAL.md is missing/broken) ─────────

_FALLBACK_FULL = """
## TACTICAL DOCTRINE REFERENCE (FALLBACK — FIELD_MANUAL.md not loaded)

Key principles:
- Fire and maneuver: one element suppresses while another moves.
- Combined arms: infantry clears close terrain, armor in open terrain, artillery suppresses.
- Concentration of force: 3:1 superiority at the decisive point.
- Terrain: use cover and concealment, seek elevation advantage.
- Recon finds the enemy, does not fight. Artillery supports, does not act alone.
- Protect flanks. Maintain reserves. Coordinate fires with maneuver.
"""

_FALLBACK_BRIEF = """
## Tactical Reference (FALLBACK)

- Fire and maneuver: suppress + move. Combined arms: infantry/armor/artillery.
- 3:1 superiority at point of attack. Use terrain. Protect flanks.
- Recon observes, artillery supports, infantry assaults, armor exploits.
"""


# ── Load at import time ──────────────────────────────────────────────────────

TACTICAL_DOCTRINE_FULL, TACTICAL_DOCTRINE_BRIEF = _load_doctrine()


def get_tactical_doctrine(level: str = "full") -> str:
    """
    Get tactical doctrine text for injection into LLM prompts.

    The text is read from FIELD_MANUAL.md at import time (single source of truth).
    To update doctrine, edit FIELD_MANUAL.md Appendix C and restart the backend.

    Args:
        level: "full" for comprehensive (Red AI commander),
               "brief" for condensed (order parser, response generator).

    Returns:
        Tactical doctrine text string.
    """
    if level == "brief":
        return TACTICAL_DOCTRINE_BRIEF
    return TACTICAL_DOCTRINE_FULL

