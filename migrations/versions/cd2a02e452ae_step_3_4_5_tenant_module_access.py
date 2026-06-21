"""step_3_4_5_tenant_module_access

Revision ID: cd2a02e452ae
Revises: 21e2ad16303a
Create Date: 2026-05-03 02:18:43.053634

Step 3.4.5: adds the ``tenant_module_access`` table that resolves
FN-AB-16 (the module entitlements stub from Step 3.3). Adds two PG
enums (``module_code_enum``, ``module_access_status_enum``), the
table itself with full Pattern (a) lifecycle audit columns, the
unconditional D-29 OR-clause RLS policy (tenant_id is NOT NULL), the
read-pattern index, and the BEFORE-UPDATE trigger. Seeds six rows
into ``lookups`` for the ``module_code`` list so the API can resolve
display names for module codes via JOIN.

Schema qualification: unqualified names throughout, matching Step
3.0's precedent. ``migrations/env.py`` sets search_path inside the
alembic transaction (env.py:66), so unqualified references resolve
to the configured schema. Schema renames (D-15) stay cheap because
no migration carries a hardcoded schema literal.

Trigger function: ``set_updated_at_timestamp()`` — the actual name
of the shared utility (defined in
``Ithina_postgres_SQL_DDL_shared_utilities_v1.sql``). Used by
``tenants``, ``platform_users``, ``lookups``, etc.

Downgrade reverses creation in order (lookups seed → policy → RLS
disable → trigger → index → table → enum types). No CASCADE on any
DROP, mirroring the discipline established at Step 1.6.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "cd2a02e452ae"
down_revision: Union[str, Sequence[str], None] = "21e2ad16303a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create enums + table + index + trigger + RLS policy; seed lookups."""

    # 1. Enum types.
    op.execute(
        """
        CREATE TYPE module_code_enum AS ENUM (
            'ROOS',
            'PRICING_OS',
            'PERISHABLES_ASSISTANT',
            'PROMOTIONS_ASSISTANT',
            'GOAL_CONSOLE',
            'ADMIN'
        )
        """
    )
    op.execute(
        """
        CREATE TYPE module_access_status_enum AS ENUM (
            'ENABLED',
            'DISABLED'
        )
        """
    )

    # 2. Table.
    op.execute(
        """
        CREATE TABLE tenant_module_access (
            id                          UUID                            NOT NULL DEFAULT uuidv7(),

            tenant_id                   UUID                            NOT NULL,
            module                      module_code_enum                NOT NULL,
            status                      module_access_status_enum       NOT NULL,

            enabled_at                  TIMESTAMPTZ                     NOT NULL,
            enabled_by_user_id          UUID                            NOT NULL,
            disabled_at                 TIMESTAMPTZ                     NULL,
            disabled_by_user_id         UUID                            NULL,

            created_at                  TIMESTAMPTZ                     NOT NULL DEFAULT NOW(),
            created_by_user_id          UUID                            NOT NULL,
            updated_at                  TIMESTAMPTZ                     NOT NULL DEFAULT NOW(),
            updated_by_user_id          UUID                            NOT NULL,

            CONSTRAINT pk_tenant_module_access
                PRIMARY KEY (id),

            CONSTRAINT uq_tenant_module_access_tenant_module
                UNIQUE (tenant_id, module),

            CONSTRAINT fk_tenant_module_access_tenant
                FOREIGN KEY (tenant_id)
                REFERENCES tenants (id)
                ON DELETE RESTRICT
                ON UPDATE RESTRICT,

            CONSTRAINT fk_tenant_module_access_enabled_by
                FOREIGN KEY (enabled_by_user_id)
                REFERENCES platform_users (id)
                ON DELETE RESTRICT
                ON UPDATE RESTRICT,

            CONSTRAINT fk_tenant_module_access_disabled_by
                FOREIGN KEY (disabled_by_user_id)
                REFERENCES platform_users (id)
                ON DELETE RESTRICT
                ON UPDATE RESTRICT,

            CONSTRAINT fk_tenant_module_access_created_by
                FOREIGN KEY (created_by_user_id)
                REFERENCES platform_users (id)
                ON DELETE RESTRICT
                ON UPDATE RESTRICT,

            CONSTRAINT fk_tenant_module_access_updated_by
                FOREIGN KEY (updated_by_user_id)
                REFERENCES platform_users (id)
                ON DELETE RESTRICT
                ON UPDATE RESTRICT,

            CONSTRAINT ck_tenant_module_access_disabled_pair
                CHECK (
                    (disabled_at IS NULL AND disabled_by_user_id IS NULL)
                    OR
                    (disabled_at IS NOT NULL AND disabled_by_user_id IS NOT NULL)
                ),

            CONSTRAINT ck_tenant_module_access_status_consistency
                CHECK (
                    (status = 'ENABLED' AND disabled_at IS NULL)
                    OR
                    (status = 'DISABLED' AND disabled_at IS NOT NULL)
                )
        )
        """
    )

    # 3. Read-pattern index.
    op.execute(
        """
        CREATE INDEX ix_tenant_module_access_tenant_id
            ON tenant_module_access (tenant_id)
        """
    )

    # 4. BEFORE-UPDATE trigger using the shared utility function.
    op.execute(
        """
        CREATE TRIGGER tg_tenant_module_access_set_updated_at
            BEFORE UPDATE ON tenant_module_access
            FOR EACH ROW
            EXECUTE FUNCTION set_updated_at_timestamp()
        """
    )

    # 5. RLS + FORCE.
    op.execute(
        "ALTER TABLE tenant_module_access ENABLE ROW LEVEL SECURITY"
    )
    op.execute(
        "ALTER TABLE tenant_module_access FORCE ROW LEVEL SECURITY"
    )

    # 6. Policy — D-29 unconditional OR-clause shape.
    op.execute(
        """
        CREATE POLICY tenant_module_access_tenant_isolation
            ON tenant_module_access
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

    # 7. Seed `module_code` rows in lookups. display_order locks the
    # canonical ordering used by the API's modules array. Display names
    # match the Step 3.3 stub for response-shape stability on cutover.
    op.execute(
        """
        INSERT INTO lookups (list_name, code, display_name, display_order, is_active)
        VALUES
            ('module_code', 'ROOS',                  'ROOS',                  1, TRUE),
            ('module_code', 'PRICING_OS',            'Pricing OS',            2, TRUE),
            ('module_code', 'PERISHABLES_ASSISTANT', 'Perishables Assistant', 3, TRUE),
            ('module_code', 'PROMOTIONS_ASSISTANT',  'Promotions Assistant',  4, TRUE),
            ('module_code', 'GOAL_CONSOLE',          'Goal Console',          5, TRUE),
            ('module_code', 'ADMIN',                 'Admin',                 6, TRUE)
        """
    )


def downgrade() -> None:
    """Reverse upgrade in creation order. No CASCADE."""

    # 1. Remove lookups seed first (no dependency on the table; this is
    # cheap and keeps the order intuitive).
    op.execute(
        """
        DELETE FROM lookups
        WHERE list_name = 'module_code'
          AND code IN (
            'ROOS', 'PRICING_OS', 'PERISHABLES_ASSISTANT',
            'PROMOTIONS_ASSISTANT', 'GOAL_CONSOLE', 'ADMIN'
          )
        """
    )

    # 2. Drop policy.
    op.execute(
        "DROP POLICY tenant_module_access_tenant_isolation "
        "ON tenant_module_access"
    )

    # 3. Disable RLS (FORCE flag clears with the disable).
    op.execute(
        "ALTER TABLE tenant_module_access DISABLE ROW LEVEL SECURITY"
    )

    # 4. Drop trigger and index. (DROP TABLE would clean these up
    # implicitly, but explicit drops in reverse order are clearer.)
    op.execute(
        "DROP TRIGGER tg_tenant_module_access_set_updated_at "
        "ON tenant_module_access"
    )
    op.execute("DROP INDEX ix_tenant_module_access_tenant_id")

    # 5. Drop the table.
    op.execute("DROP TABLE tenant_module_access")

    # 6. Drop the enum types (after the columns using them are gone).
    op.execute("DROP TYPE module_access_status_enum")
    op.execute("DROP TYPE module_code_enum")
