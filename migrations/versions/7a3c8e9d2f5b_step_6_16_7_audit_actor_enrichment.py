"""Step 6.16.7: audit actor enrichment columns

Revision ID: 7a3c8e9d2f5b
Revises: 34f515cbc63a
Create Date: 2026-05-23 12:00:00.000000

Adds three columns to both audit tables (``core.tenant_activity_audit_logs``
and ``core.platform_activity_audit_logs``) per Step 6.16.7 LD1:

  - ``actor_organization_name TEXT NOT NULL`` (denormalised snapshot:
    tenant name for tenant actors, literal ``'Platform-Ithina'`` for
    platform actors).
  - ``actor_roles TEXT NOT NULL`` (denormalised snapshot: comma-separated
    active role display names from ``roles.name``; rendered directly by
    the UI).
  - ``resource_subtype TEXT NULL`` (populated only on ORG_NODE rows with
    the ``org_nodes.node_type`` enum value frozen at write time; NULL for
    non-ORG_NODE rows and pre-6.16.7 historical rows).

Path A backfill per LD2 + LD3:
  1. ALTER TABLE ADD COLUMN ... NULL on all 3 columns, both tables.
  2. UPDATE backfill:
       - tenant table: actor_organization_name = CASE WHEN
         actor_user_type='PLATFORM' THEN 'Platform-Ithina' ELSE
         tenant_name END.
       - platform table: actor_organization_name = 'Platform-Ithina'
         literal for all rows.
       - both tables: actor_roles = '-' for all historical rows.
         (Joining live role assignments now would violate the
         frozen-snapshot principle locked at Phase 1 Q2 of 6.16.7:
         "audit is history of an event - history never changes". The
         actor's roles at the moment of the pre-6.16.7 action are no
         longer reliably knowable.)
       - resource_subtype stays NULL on all pre-6.16.7 rows.
  3. ALTER COLUMN SET NOT NULL on actor_organization_name and
     actor_roles (both tables). resource_subtype stays NULLABLE.

Schema-qualified per CSD-03; ``current_schema()`` capture mirrors
``a0982a86985b`` / ``34f515cbc63a`` posture.

Downgrade behaviour: drop the 3 new columns from both tables. The
pre-existing 16-column shape is restored.

Runtime expectation: sub-second on local seeded data. UPDATE backfills
are unindexed full-table scans, but the audit tables are empty or near-
empty at this point in the v0 chain (no real production traffic yet);
EXPLAIN ANALYZE verified at impl.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7a3c8e9d2f5b'
down_revision: Union[str, Sequence[str], None] = '34f515cbc63a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add 3 new columns to both audit tables and backfill historical rows."""
    bind = op.get_bind()
    schema = bind.execute(sa.text("SELECT current_schema()")).scalar_one()

    # Set PLATFORM GUC so the UPDATE backfills below see all rows under
    # FORCE RLS on tenant_activity_audit_logs (D-29 OR-branch). Without
    # this, the backfill UPDATE matches zero rows; SET NOT NULL then
    # fails on any pre-existing audit rows.
    bind.execute(sa.text("SELECT set_config('app.user_type', 'PLATFORM', false)"))
    bind.execute(sa.text("SELECT set_config('app.tenant_id', '', false)"))

    # ----- 1. ADD COLUMN (NULLABLE) on both tables. -----
    for table in (
        "tenant_activity_audit_logs",
        "platform_activity_audit_logs",
    ):
        op.execute(
            sa.text(
                f"ALTER TABLE {schema}.{table} "
                "ADD COLUMN actor_organization_name TEXT NULL"
            )
        )
        op.execute(
            sa.text(
                f"ALTER TABLE {schema}.{table} "
                "ADD COLUMN actor_roles TEXT NULL"
            )
        )
        op.execute(
            sa.text(
                f"ALTER TABLE {schema}.{table} "
                "ADD COLUMN resource_subtype TEXT NULL"
            )
        )

    # ----- 2. Backfill historical rows. -----
    # Tenant table: actor_organization_name from CASE on actor_user_type.
    op.execute(
        sa.text(
            f"""
            UPDATE {schema}.tenant_activity_audit_logs
            SET actor_organization_name = CASE
                WHEN actor_user_type = CAST('PLATFORM' AS {schema}.actor_user_type_enum)
                    THEN 'Platform-Ithina'
                ELSE tenant_name
            END
            WHERE actor_organization_name IS NULL
            """
        )
    )

    # Platform table: literal 'Platform-Ithina' for all rows. The
    # historical platform-table rows are platform-scope events; the
    # actor is always operating with platform authority for those
    # events. (Tenant-creation success rows DO populate tenant_name on
    # this table per the design doc routing principle, but the actor
    # was still operating as a PLATFORM user; their organisation in
    # the audit-row sense is Ithina, not the created tenant.)
    op.execute(
        sa.text(
            f"""
            UPDATE {schema}.platform_activity_audit_logs
            SET actor_organization_name = 'Platform-Ithina'
            WHERE actor_organization_name IS NULL
            """
        )
    )

    # Both tables: actor_roles = '-' for all historical rows.
    for table in (
        "tenant_activity_audit_logs",
        "platform_activity_audit_logs",
    ):
        op.execute(
            sa.text(
                f"""
                UPDATE {schema}.{table}
                SET actor_roles = '-'
                WHERE actor_roles IS NULL
                """
            )
        )

    # resource_subtype stays NULL on all pre-6.16.7 rows per LD3.

    # ----- 3. SET NOT NULL on actor_organization_name + actor_roles. -----
    # resource_subtype stays NULLABLE permanently (most resource_types
    # don't carry a subtype).
    for table in (
        "tenant_activity_audit_logs",
        "platform_activity_audit_logs",
    ):
        op.execute(
            sa.text(
                f"ALTER TABLE {schema}.{table} "
                "ALTER COLUMN actor_organization_name SET NOT NULL"
            )
        )
        op.execute(
            sa.text(
                f"ALTER TABLE {schema}.{table} "
                "ALTER COLUMN actor_roles SET NOT NULL"
            )
        )


def downgrade() -> None:
    """Drop the 3 new columns from both audit tables."""
    bind = op.get_bind()
    schema = bind.execute(sa.text("SELECT current_schema()")).scalar_one()

    for table in (
        "tenant_activity_audit_logs",
        "platform_activity_audit_logs",
    ):
        op.execute(
            sa.text(
                f"ALTER TABLE {schema}.{table} "
                "DROP COLUMN IF EXISTS resource_subtype"
            )
        )
        op.execute(
            sa.text(
                f"ALTER TABLE {schema}.{table} "
                "DROP COLUMN IF EXISTS actor_roles"
            )
        )
        op.execute(
            sa.text(
                f"ALTER TABLE {schema}.{table} "
                "DROP COLUMN IF EXISTS actor_organization_name"
            )
        )
