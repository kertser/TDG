"""Add game_time column to chat_messages.

Revision ID: 007
Revises: 006
Create Date: 2026-04-10
"""
from alembic import op
import sqlalchemy as sa


revision = "007_add_game_time_to_chat"
down_revision = "006_unit_fk_set_null"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "chat_messages",
        sa.Column("game_time", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade():
    op.drop_column("chat_messages", "game_time")


