"""Add discovered_by_blue and discovered_by_red columns to map_objects table.

Obstacles default to hidden (False), structures default to revealed (True).
Discovery is per-side: once a unit from that side has LOS, it flips to True.

Revision ID: 003_add_map_object_discovery
Revises: 002_add_password_hash
Create Date: 2026-04-08
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = '003_add_map_object_discovery'
down_revision = '002_add_password_hash'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('map_objects', sa.Column('discovered_by_blue', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('map_objects', sa.Column('discovered_by_red', sa.Boolean(), nullable=False, server_default='false'))

    # Set existing structures to revealed (True) for both sides
    op.execute("""
        UPDATE map_objects
        SET discovered_by_blue = true, discovered_by_red = true
        WHERE object_category = 'structure'
    """)


def downgrade() -> None:
    op.drop_column('map_objects', 'discovered_by_red')
    op.drop_column('map_objects', 'discovered_by_blue')

