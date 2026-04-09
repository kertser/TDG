"""Add order_queue column to units table for phased/conditional orders.

Revision ID: 004
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '004_add_order_queue'
down_revision = '003_add_map_object_discovery'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('units', sa.Column('order_queue', JSONB, nullable=True))


def downgrade():
    op.drop_column('units', 'order_queue')

