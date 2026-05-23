"""
Proposal Store — persist learning proposals to the database.

Also provides:
  - apply_proposal()  : write approved TOML to disk and hot-reload phrasebook
  - append_to_toml()  : low-level TOML append with backup
"""
from __future__ import annotations

import shutil
import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.learning_proposal import LearningProposal, ProposalStatus

logger = logging.getLogger(__name__)

AUTO_REJECT_CONF      = 0.40
AUTO_REJECT_LLM_SCORE = 0.60

PHRASEBOOK_PATH = Path("backend/data/order_phrasebook.toml")


async def save_proposals(proposals: list[dict], db: AsyncSession) -> tuple[int, int]:
    """
    Persist a list of raw proposal dicts to the database.

    Returns (saved_count, auto_rejected_count).
    """
    now = datetime.now(timezone.utc)
    saved = auto_rejected = 0

    for prop in proposals:
        status = ProposalStatus.pending

        # Auto-reject low-confidence candidates (not shown to admin)
        if prop.get("confidence", 0) < AUTO_REJECT_CONF:
            status = ProposalStatus.auto_rejected
            auto_rejected += 1
        elif (prop.get("llm_judge_score") is not None
              and prop["llm_judge_score"] < AUTO_REJECT_LLM_SCORE):
            status = ProposalStatus.auto_rejected
            auto_rejected += 1

        record = LearningProposal(
            session_ids=prop.get("session_ids", []),
            user_ids=prop.get("user_ids", []),
            proposal_type=prop.get("proposal_type", "phrasebook_case"),
            target_file=prop.get("target_file", "backend/data/order_phrasebook.toml"),
            target_section=prop.get("target_section"),
            proposed_text=prop.get("proposed_text", ""),
            rationale=prop.get("rationale", ""),
            source_order_ids=prop.get("source_order_ids", []),
            example_texts=prop.get("example_texts", []),
            confidence=float(prop.get("confidence", 0.0)),
            cross_session_count=int(prop.get("cross_session_count", 0)),
            unique_user_count=int(prop.get("unique_user_count", 0)),
            llm_judge_score=prop.get("llm_judge_score"),
            llm_judge_reasoning=prop.get("llm_judge_reasoning"),
            status=status,
            created_at=now,
        )
        db.add(record)
        saved += 1

    await db.commit()
    return saved, auto_rejected


def append_to_toml(toml_block: str) -> None:
    """Append an approved TOML block to the phrasebook file with backup."""
    if not PHRASEBOOK_PATH.exists():
        logger.error("Phrasebook file not found: %s", PHRASEBOOK_PATH)
        return
    # Backup first
    backup = PHRASEBOOK_PATH.with_suffix(".toml.bak")
    shutil.copy2(PHRASEBOOK_PATH, backup)

    with open(PHRASEBOOK_PATH, "a", encoding="utf-8") as f:
        f.write("\n# === Human-approved auto-proposal ===\n")
        f.write(toml_block)
        if not toml_block.endswith("\n"):
            f.write("\n")

    logger.info("Appended proposal to %s (backup: %s)", PHRASEBOOK_PATH, backup)


def hot_reload_phrasebook() -> None:
    """Hot-reload the phrasebook after applying proposals."""
    try:
        from backend.services.order_phrasebook import reload_phrasebook  # type: ignore
        reload_phrasebook()
        logger.info("Phrasebook hot-reloaded after proposal application.")
    except (ImportError, AttributeError):
        logger.warning("reload_phrasebook() not available — restart backend to apply changes.")

