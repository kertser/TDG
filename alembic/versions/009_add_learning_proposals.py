"""Add learning_proposals table.

Revision ID: 009_add_learning_proposals
Revises: 008_add_tutorial_completed
Create Date: 2026-05-23
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "009_add_learning_proposals"
down_revision = "008_add_tutorial_completed"
branch_labels = None
depends_on = None


def upgrade():
    # Create enums (idempotent via DO block)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE proposaltype AS ENUM ('phrasebook_case', 'phrasebook_lexicon');
        EXCEPTION WHEN duplicate_object THEN null;
        END $$
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE proposalstatus AS ENUM ('pending', 'approved', 'rejected', 'applied', 'auto_rejected');
        EXCEPTION WHEN duplicate_object THEN null;
        END $$
    """)

    # Create table only if it doesn't exist
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT 1 FROM information_schema.tables WHERE table_name='learning_proposals'"
    ))
    if result.fetchone() is not None:
        return  # Table already exists, skip

    op.create_table(
        "learning_proposals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("session_ids", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("user_ids", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("proposal_type", sa.Enum("phrasebook_case", "phrasebook_lexicon", name="proposaltype"), nullable=False),
        sa.Column("target_file", sa.String(200), nullable=False),
        sa.Column("target_section", sa.String(200), nullable=True),
        sa.Column("proposed_text", sa.Text, nullable=False),
        sa.Column("rationale", sa.Text, nullable=False),
        sa.Column("source_order_ids", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("example_texts", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column("cross_session_count", sa.Integer, nullable=False),
        sa.Column("unique_user_count", sa.Integer, nullable=False),
        sa.Column("llm_judge_score", sa.Float, nullable=True),
        sa.Column("llm_judge_reasoning", sa.Text, nullable=True),
        sa.Column("status", sa.Enum("pending", "approved", "rejected", "applied", "auto_rejected", name="proposalstatus"), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_learning_proposals_status", "learning_proposals", ["status"])


def downgrade():
    op.drop_table("learning_proposals")
    op.execute("DROP TYPE IF EXISTS proposaltype")
    op.execute("DROP TYPE IF EXISTS proposalstatus")

