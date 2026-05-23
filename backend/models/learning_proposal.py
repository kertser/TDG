"""
Learning Proposal SQLAlchemy model.

A proposal represents a candidate phrasebook entry mined from completed
session orders and reviewed / applied by a human admin.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Column, Integer, Float, String, Text, DateTime,
    ARRAY,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy import Enum as SAEnum

from backend.database import Base


class ProposalType(str, enum.Enum):
    phrasebook_case    = "phrasebook_case"      # new [[case]] entry in TOML
    phrasebook_lexicon = "phrasebook_lexicon"   # new word in [lexicon.X] table


class ProposalStatus(str, enum.Enum):
    pending       = "pending"
    approved      = "approved"
    rejected      = "rejected"
    applied       = "applied"
    auto_rejected = "auto_rejected"   # low confidence — not shown to admin


class LearningProposal(Base):
    __tablename__ = "learning_proposals"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_ids        = Column(JSONB, nullable=False, default=list)   # list[str UUID]
    user_ids           = Column(JSONB, nullable=False, default=list)   # list[str UUID]
    proposal_type      = Column(SAEnum(ProposalType), nullable=False)
    target_file        = Column(String(200), nullable=False)
    target_section     = Column(String(200), nullable=True)
    proposed_text      = Column(Text, nullable=False)
    rationale          = Column(Text, nullable=False)
    source_order_ids   = Column(JSONB, nullable=False, default=list)   # list[str UUID]
    example_texts      = Column(JSONB, nullable=False, default=list)   # list[str]
    confidence         = Column(Float, nullable=False)
    cross_session_count = Column(Integer, nullable=False)
    unique_user_count  = Column(Integer, nullable=False)
    llm_judge_score    = Column(Float, nullable=True)
    llm_judge_reasoning = Column(Text, nullable=True)
    status             = Column(SAEnum(ProposalStatus), default=ProposalStatus.pending, nullable=False)
    created_at         = Column(DateTime(timezone=True), nullable=False)
    applied_at         = Column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<LearningProposal {self.proposal_type} {self.status}>"

