"""Add ON DELETE SET NULL to unit foreign keys.

Revision ID: 006_unit_fk_set_null
Revises: 005_add_effect_category
"""

from alembic import op

revision = "006_unit_fk_set_null"
down_revision = "005_add_effect_category"
branch_labels = None
depends_on = None


def upgrade():
    # events.actor_unit_id
    op.drop_constraint("events_actor_unit_id_fkey", "events", type_="foreignkey")
    op.create_foreign_key("events_actor_unit_id_fkey", "events", "units",
                          ["actor_unit_id"], ["id"], ondelete="SET NULL")

    # events.target_unit_id
    op.drop_constraint("events_target_unit_id_fkey", "events", type_="foreignkey")
    op.create_foreign_key("events_target_unit_id_fkey", "events", "units",
                          ["target_unit_id"], ["id"], ondelete="SET NULL")

    # contacts.observing_unit_id
    op.drop_constraint("contacts_observing_unit_id_fkey", "contacts", type_="foreignkey")
    op.create_foreign_key("contacts_observing_unit_id_fkey", "contacts", "units",
                          ["observing_unit_id"], ["id"], ondelete="SET NULL")

    # reports.from_unit_id
    op.drop_constraint("reports_from_unit_id_fkey", "reports", type_="foreignkey")
    op.create_foreign_key("reports_from_unit_id_fkey", "reports", "units",
                          ["from_unit_id"], ["id"], ondelete="SET NULL")

    # units.parent_unit_id (self-referential)
    op.drop_constraint("units_parent_unit_id_fkey", "units", type_="foreignkey")
    op.create_foreign_key("units_parent_unit_id_fkey", "units", "units",
                          ["parent_unit_id"], ["id"], ondelete="SET NULL")


def downgrade():
    for table, constraint, cols in [
        ("events", "events_actor_unit_id_fkey", ["actor_unit_id"]),
        ("events", "events_target_unit_id_fkey", ["target_unit_id"]),
        ("contacts", "contacts_observing_unit_id_fkey", ["observing_unit_id"]),
        ("reports", "reports_from_unit_id_fkey", ["from_unit_id"]),
        ("units", "units_parent_unit_id_fkey", ["parent_unit_id"]),
    ]:
        op.drop_constraint(constraint, table, type_="foreignkey")
        op.create_foreign_key(constraint, table, "units", cols, ["id"])

