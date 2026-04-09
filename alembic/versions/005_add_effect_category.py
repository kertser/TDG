"""Add 'effect' value to object_category_enum.

Revision ID: 005
Revises: 004_add_order_queue
Create Date: 2026-04-09
"""

from alembic import op

revision = "005_add_effect_category"
down_revision = "004_add_order_queue"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE cannot run inside a transaction
    op.execute("COMMIT")
    op.execute("ALTER TYPE object_category_enum ADD VALUE IF NOT EXISTS 'effect'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values — no-op
    pass

