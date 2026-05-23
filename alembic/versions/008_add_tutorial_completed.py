"""Add tutorial_completed column to users.

Revision ID: 008_add_tutorial_completed
Revises: 007_add_game_time_to_chat
Create Date: 2026-05-23
"""
from alembic import op
import sqlalchemy as sa


revision = "008_add_tutorial_completed"
down_revision = "007_add_game_time_to_chat"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "users",
        sa.Column(
            "tutorial_completed",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),
    )


def downgrade():
    op.drop_column("users", "tutorial_completed")

