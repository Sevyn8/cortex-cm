"""Step 6.16.1: audit log schema (DDL + RLS + indexes)

Revision ID: c530346032dd
Revises: 5e22b2ca13cc
Create Date: 2026-05-20

Lands the database schema for the audit log subsystem per
`docs/architecture_audit_logs.md` (Step 6.16.0).

Creates two physically separate tables with symmetric 16-column shape:

  * `tenant_activity_audit_logs` : multi-tenant, RLS+FORCE with the
    unconditional D-29 OR-branch policy. `tenant_id` and `tenant_name`
    are NOT NULL. Reads scoped to caller's tenant under TENANT JWT;
    PLATFORM session sees all rows.

  * `platform_activity_audit_logs` : platform-global, no RLS. Access
    gated at the API layer in 6.16.3. `tenant_id` and `tenant_name`
    are NULLABLE: populated only on tenant-creation success rows.

One new enum `audit_result_type_enum` carries the 6 stable failure
categories. The existing `actor_user_type_enum` (PLATFORM, TENANT)
is reused for the `actor_user_type` column on both audit tables.

CHECK constraints enforce NULL-pair consistency:
  * `ck_*_resource_pair` on both tables: resource_id and
    resource_label are both NULL or both NOT NULL. Failed-create
    rows have NULL pair; success and failed-update rows have the
    pair populated.
  * `ck_platform_activity_audit_logs_tenant_pair` on platform table
    only: tenant_id and tenant_name are both NULL or both NOT NULL.
    Tenant table does not need this CHECK since both columns are
    NOT NULL.

Indexes per the design doc:
  * tenant table (3): `(timestamp DESC, id DESC)`,
    `(tenant_id, timestamp DESC, id DESC)`, partial on result_type
    WHERE != 'SUCCESS'.
  * platform table (2): `(timestamp DESC, id DESC)`, partial on
    result_type WHERE != 'SUCCESS'. No `tenant_id` index (no
    query pattern filters by tenant_id on the platform side).

Schema qualification follows the a0982a86985b / 5e22b2ca13cc
convention per CSD-03: read `current_schema()` at migration time and
f-string-interpolate `{schema}.` on every identifier reference. The
application role is NOSUPERUSER NOBYPASSRLS; the migration session
inherits whatever schema env.py set on search_path, but raw-SQL
identifiers are explicitly qualified regardless.

Reversible: downgrade drops the platform table, tenant table (with
its policy + RLS state), then the new enum, in reverse-of-creation
order. No data copy required (audit tables ship empty at this step;
emission lands at 6.16.2 and onward).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c530346032dd'
down_revision: Union[str, Sequence[str], None] = '5e22b2ca13cc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create audit_result_type_enum + 2 audit tables + RLS + indexes."""
    bind = op.get_bind()
    schema = bind.execute(sa.text("SELECT current_schema()")).scalar_one()

    # =========================================================================
    # 1. CREATE TYPE audit_result_type_enum
    # =========================================================================
    op.execute(
        f"""
        CREATE TYPE {schema}.audit_result_type_enum AS ENUM (
            'SUCCESS',
            'PERMISSION_DENIED',
            'VALIDATION_FAILED',
            'CONFLICT',
            'INTEGRITY_VIOLATION',
            'INTERNAL_ERROR'
        )
        """
    )

    # =========================================================================
    # 2. CREATE TABLE tenant_activity_audit_logs
    #    Symmetric 16-column shape. tenant_id and tenant_name NOT NULL.
    # =========================================================================
    op.execute(
        f"""
        CREATE TABLE {schema}.tenant_activity_audit_logs (
            id                  UUID                              NOT NULL DEFAULT {schema}.uuidv7(),
            timestamp           TIMESTAMPTZ                       NOT NULL DEFAULT NOW(),
            tenant_id           UUID                              NOT NULL,
            tenant_name         TEXT                              NOT NULL,
            actor_user_id       UUID                              NOT NULL,
            actor_user_type     {schema}.actor_user_type_enum     NOT NULL,
            actor_display_name  TEXT                              NOT NULL,
            resource_type       TEXT                              NOT NULL,
            resource_id         UUID                              NULL,
            resource_label      TEXT                              NULL,
            action              TEXT                              NOT NULL,
            action_label        TEXT                              NOT NULL,
            result_type         {schema}.audit_result_type_enum   NOT NULL,
            result_label        TEXT                              NOT NULL,
            request_id          UUID                              NOT NULL,
            details             JSONB                             NOT NULL DEFAULT '{{}}'::jsonb,

            CONSTRAINT pk_tenant_activity_audit_logs
                PRIMARY KEY (id),

            CONSTRAINT fk_tenant_activity_audit_logs_tenant
                FOREIGN KEY (tenant_id) REFERENCES {schema}.tenants (id)
                ON UPDATE RESTRICT ON DELETE RESTRICT,

            CONSTRAINT ck_tenant_activity_audit_logs_resource_pair
                CHECK (
                    (resource_id IS NULL AND resource_label IS NULL)
                    OR
                    (resource_id IS NOT NULL AND resource_label IS NOT NULL)
                )
        )
        """
    )

    # =========================================================================
    # 3. RLS + FORCE + D-29 unconditional OR-branch policy on tenant table
    # =========================================================================
    op.execute(
        f"ALTER TABLE {schema}.tenant_activity_audit_logs "
        "ENABLE ROW LEVEL SECURITY"
    )
    op.execute(
        f"ALTER TABLE {schema}.tenant_activity_audit_logs "
        "FORCE ROW LEVEL SECURITY"
    )
    op.execute(
        f"""
        CREATE POLICY tenant_activity_audit_logs_tenant_isolation
            ON {schema}.tenant_activity_audit_logs
            FOR ALL
            USING (
                tenant_id = NULLIF(current_setting('app.tenant_id', TRUE), '')::uuid
                OR current_setting('app.user_type', TRUE) = 'PLATFORM'
            )
            WITH CHECK (
                tenant_id = NULLIF(current_setting('app.tenant_id', TRUE), '')::uuid
                OR current_setting('app.user_type', TRUE) = 'PLATFORM'
            )
        """
    )

    # =========================================================================
    # 4. Indexes on tenant_activity_audit_logs (3)
    # =========================================================================
    op.execute(
        f"CREATE INDEX ix_tenant_activity_audit_logs_timestamp_id "
        f"ON {schema}.tenant_activity_audit_logs (timestamp DESC, id DESC)"
    )
    op.execute(
        f"CREATE INDEX ix_tenant_activity_audit_logs_tenant_timestamp_id "
        f"ON {schema}.tenant_activity_audit_logs (tenant_id, timestamp DESC, id DESC)"
    )
    op.execute(
        f"CREATE INDEX ix_tenant_activity_audit_logs_failures "
        f"ON {schema}.tenant_activity_audit_logs (result_type) "
        f"WHERE result_type != 'SUCCESS'"
    )

    # =========================================================================
    # 5. CREATE TABLE platform_activity_audit_logs
    #    Symmetric 16-column shape. tenant_id and tenant_name NULLABLE.
    # =========================================================================
    op.execute(
        f"""
        CREATE TABLE {schema}.platform_activity_audit_logs (
            id                  UUID                              NOT NULL DEFAULT {schema}.uuidv7(),
            timestamp           TIMESTAMPTZ                       NOT NULL DEFAULT NOW(),
            tenant_id           UUID                              NULL,
            tenant_name         TEXT                              NULL,
            actor_user_id       UUID                              NOT NULL,
            actor_user_type     {schema}.actor_user_type_enum     NOT NULL,
            actor_display_name  TEXT                              NOT NULL,
            resource_type       TEXT                              NOT NULL,
            resource_id         UUID                              NULL,
            resource_label      TEXT                              NULL,
            action              TEXT                              NOT NULL,
            action_label        TEXT                              NOT NULL,
            result_type         {schema}.audit_result_type_enum   NOT NULL,
            result_label        TEXT                              NOT NULL,
            request_id          UUID                              NOT NULL,
            details             JSONB                             NOT NULL DEFAULT '{{}}'::jsonb,

            CONSTRAINT pk_platform_activity_audit_logs
                PRIMARY KEY (id),

            CONSTRAINT fk_platform_activity_audit_logs_tenant
                FOREIGN KEY (tenant_id) REFERENCES {schema}.tenants (id)
                ON UPDATE RESTRICT ON DELETE RESTRICT,

            CONSTRAINT ck_platform_activity_audit_logs_resource_pair
                CHECK (
                    (resource_id IS NULL AND resource_label IS NULL)
                    OR
                    (resource_id IS NOT NULL AND resource_label IS NOT NULL)
                ),

            CONSTRAINT ck_platform_activity_audit_logs_tenant_pair
                CHECK (
                    (tenant_id IS NULL AND tenant_name IS NULL)
                    OR
                    (tenant_id IS NOT NULL AND tenant_name IS NOT NULL)
                )
        )
        """
    )

    # =========================================================================
    # 6. No RLS on platform_activity_audit_logs. Access gated at API layer.
    # =========================================================================

    # =========================================================================
    # 7. Indexes on platform_activity_audit_logs (2)
    # =========================================================================
    op.execute(
        f"CREATE INDEX ix_platform_activity_audit_logs_timestamp_id "
        f"ON {schema}.platform_activity_audit_logs (timestamp DESC, id DESC)"
    )
    op.execute(
        f"CREATE INDEX ix_platform_activity_audit_logs_failures "
        f"ON {schema}.platform_activity_audit_logs (result_type) "
        f"WHERE result_type != 'SUCCESS'"
    )


def downgrade() -> None:
    """Drop both tables (cascading indexes / constraints / RLS) and the enum."""
    bind = op.get_bind()
    schema = bind.execute(sa.text("SELECT current_schema()")).scalar_one()

    # Drop platform table first (no RLS to clean up).
    op.execute(f"DROP TABLE {schema}.platform_activity_audit_logs")

    # Drop tenant table; the policy and RLS state are dropped with the table.
    op.execute(f"DROP TABLE {schema}.tenant_activity_audit_logs")

    # Drop the new enum.
    op.execute(f"DROP TYPE {schema}.audit_result_type_enum")
