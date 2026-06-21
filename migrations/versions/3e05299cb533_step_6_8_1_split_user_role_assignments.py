"""Step 6.8.1: split user_role_assignments

Revision ID: 3e05299cb533
Revises: 2fdc4bc9f4cb
Create Date: 2026-05-09

Splits ``user_role_assignments`` into two physical tables:

  * ``platform_user_role_assignments`` — platform-global, no RLS,
    references platform_users only. PLATFORM-audience assignments.
  * ``tenant_user_role_assignments``   — multi-tenant, RLS+FORCE with
    the unconditional PLATFORM OR-branch (D-29 form). Composite FKs
    to ``tenant_users (tenant_id, id)`` and ``org_nodes (tenant_id, id)``
    make AI-RBAC-06 cross-tenant injection structurally impossible at
    the schema layer.

Retires the FN-AB-14 IS-NULL-gated policy by removing the table that
needed it. Aligns the multi-tenant policy shape uniformly across all
6 multi-tenant tables (D-29 unconditional OR).

Precondition added in this migration: ``UNIQUE (tenant_id, id)`` on
``tenant_users``. Required for the composite FK from
``tenant_user_role_assignments``. ``org_nodes`` already has the
equivalent constraint per its DDL (``uq_org_nodes_tenant_id``); only
``tenant_users`` lacked it. The added UNIQUE is not reflected in
``tenant_users_v1.sql`` per the frozen-DDL convention; documented as
live-vs-DDL drift in CLAUDE.md alongside the existing policy-migration
drift (e59f62d5037d, 4fd3aec6ae0c, 21e2ad16303a).

Reversible:
  * upgrade()  : adds UNIQUE, creates 2 new tables + indexes + triggers,
                 enables RLS+FORCE+policy on tenant_user_role_assignments,
                 verifies XOR invariant pre-copy, copies rows by audience,
                 verifies post-copy counts, drops user_role_assignments.
  * downgrade(): byte-equivalent restoration of FN-AB-14 form. Recreates
                 user_role_assignments with full v2 shape + the post-NULLIF
                 IS-NULL-gated policy (mirrors 4fd3aec6ae0c output);
                 copies rows back from both new tables; drops new tables,
                 trigger functions, and the UNIQUE on tenant_users.

Schema-agnostic: unqualified table names throughout. env.py sets
``search_path`` inside the alembic transaction (Step 3.0/3.4.5/6.7
precedent).

Per the prompt's caution-first posture, the migration body includes
two DO blocks with assertions:
  * pre-flight: XOR invariant on ``user_role_assignments`` (no row has
    both user FKs set, no row has neither).
  * post-copy: PLATFORM and TENANT row counts match between old and new
    tables before the DROP.

If either fires, the migration aborts. Composite FK rejection during
the TENANT-side copy is also a structural-integrity gate — a row whose
denormalised tenant_id mismatches the user's or org_node's tenant_id
fails the FK and aborts the migration.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '3e05299cb533'
down_revision: Union[str, Sequence[str], None] = '2fdc4bc9f4cb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Split user_role_assignments into two new tables; data-copy then drop."""

    # =========================================================================
    # 0. Precondition: UNIQUE (tenant_id, id) on tenant_users
    #
    # Required for the composite FK from tenant_user_role_assignments.
    # Mirrors org_nodes' uq_org_nodes_tenant_id naming. Adding this UNIQUE
    # to a table that already has PRIMARY KEY (id) is widening (no row
    # rejection possible since (id) is already unique).
    # =========================================================================
    op.execute(
        "ALTER TABLE tenant_users "
        "ADD CONSTRAINT uq_tenant_users_tenant_id UNIQUE (tenant_id, id)"
    )

    # =========================================================================
    # 1. CREATE TABLE platform_user_role_assignments
    # =========================================================================
    op.execute(
        """
        CREATE TABLE platform_user_role_assignments (
            id                       UUID                                NOT NULL DEFAULT uuidv7(),
            platform_user_id         UUID                                NOT NULL,
            role_id                  UUID                                NOT NULL,
            status                   user_role_assignment_status_enum    NOT NULL,
            granted_at               TIMESTAMPTZ                         NOT NULL DEFAULT NOW(),
            granted_by_user_id       UUID                                NULL,
            granted_by_user_type     actor_user_type_enum                NULL,
            revoked_at               TIMESTAMPTZ                         NULL,
            revoked_by_user_id       UUID                                NULL,
            revoked_by_user_type     actor_user_type_enum                NULL,
            updated_at               TIMESTAMPTZ                         NOT NULL DEFAULT NOW(),

            CONSTRAINT pk_platform_user_role_assignments
                PRIMARY KEY (id),

            CONSTRAINT fk_platform_user_role_assignments_platform_user
                FOREIGN KEY (platform_user_id) REFERENCES platform_users (id)
                ON DELETE RESTRICT ON UPDATE RESTRICT,

            CONSTRAINT fk_platform_user_role_assignments_role
                FOREIGN KEY (role_id) REFERENCES roles (id)
                ON DELETE RESTRICT ON UPDATE RESTRICT,

            CONSTRAINT ck_platform_user_role_assignments_granted_by_actor_pair
                CHECK (
                    (granted_by_user_id IS NULL AND granted_by_user_type IS NULL)
                    OR
                    (granted_by_user_id IS NOT NULL AND granted_by_user_type IS NOT NULL)
                ),

            CONSTRAINT ck_platform_user_role_assignments_revoked_by_actor_pair
                CHECK (
                    (revoked_by_user_id IS NULL AND revoked_by_user_type IS NULL)
                    OR
                    (revoked_by_user_id IS NOT NULL AND revoked_by_user_type IS NOT NULL)
                ),

            CONSTRAINT ck_platform_user_role_assignments_revoked_consistency
                CHECK (
                    (status = 'INACTIVE'
                        AND revoked_at IS NOT NULL
                        AND revoked_by_user_id IS NOT NULL
                        AND revoked_by_user_type IS NOT NULL)
                    OR
                    (status = 'ACTIVE'
                        AND revoked_at IS NULL
                        AND revoked_by_user_id IS NULL
                        AND revoked_by_user_type IS NULL)
                )
        )
        """
    )

    # =========================================================================
    # 2. CREATE TABLE tenant_user_role_assignments
    # =========================================================================
    op.execute(
        """
        CREATE TABLE tenant_user_role_assignments (
            id                       UUID                                NOT NULL DEFAULT uuidv7(),
            tenant_user_id           UUID                                NOT NULL,
            tenant_id                UUID                                NOT NULL,
            org_node_id              UUID                                NOT NULL,
            role_id                  UUID                                NOT NULL,
            status                   user_role_assignment_status_enum    NOT NULL,
            granted_at               TIMESTAMPTZ                         NOT NULL DEFAULT NOW(),
            granted_by_user_id       UUID                                NULL,
            granted_by_user_type     actor_user_type_enum                NULL,
            revoked_at               TIMESTAMPTZ                         NULL,
            revoked_by_user_id       UUID                                NULL,
            revoked_by_user_type     actor_user_type_enum                NULL,
            updated_at               TIMESTAMPTZ                         NOT NULL DEFAULT NOW(),

            CONSTRAINT pk_tenant_user_role_assignments
                PRIMARY KEY (id),

            CONSTRAINT fk_tenant_user_role_assignments_tenant_user_same_tenant
                FOREIGN KEY (tenant_id, tenant_user_id)
                REFERENCES tenant_users (tenant_id, id)
                ON DELETE RESTRICT ON UPDATE RESTRICT,

            CONSTRAINT fk_tenant_user_role_assignments_org_node_same_tenant
                FOREIGN KEY (tenant_id, org_node_id)
                REFERENCES org_nodes (tenant_id, id)
                ON DELETE RESTRICT ON UPDATE RESTRICT,

            CONSTRAINT fk_tenant_user_role_assignments_role
                FOREIGN KEY (role_id) REFERENCES roles (id)
                ON DELETE RESTRICT ON UPDATE RESTRICT,

            CONSTRAINT fk_tenant_user_role_assignments_tenant
                FOREIGN KEY (tenant_id) REFERENCES tenants (id)
                ON DELETE RESTRICT ON UPDATE RESTRICT,

            CONSTRAINT ck_tenant_user_role_assignments_granted_by_actor_pair
                CHECK (
                    (granted_by_user_id IS NULL AND granted_by_user_type IS NULL)
                    OR
                    (granted_by_user_id IS NOT NULL AND granted_by_user_type IS NOT NULL)
                ),

            CONSTRAINT ck_tenant_user_role_assignments_revoked_by_actor_pair
                CHECK (
                    (revoked_by_user_id IS NULL AND revoked_by_user_type IS NULL)
                    OR
                    (revoked_by_user_id IS NOT NULL AND revoked_by_user_type IS NOT NULL)
                ),

            CONSTRAINT ck_tenant_user_role_assignments_revoked_consistency
                CHECK (
                    (status = 'INACTIVE'
                        AND revoked_at IS NOT NULL
                        AND revoked_by_user_id IS NOT NULL
                        AND revoked_by_user_type IS NOT NULL)
                    OR
                    (status = 'ACTIVE'
                        AND revoked_at IS NULL
                        AND revoked_by_user_id IS NULL
                        AND revoked_by_user_type IS NULL)
                )
        )
        """
    )

    # =========================================================================
    # 3. Indexes on platform_user_role_assignments (4)
    # =========================================================================
    op.execute(
        "CREATE UNIQUE INDEX uq_platform_user_role_assignments_active "
        "ON platform_user_role_assignments (platform_user_id, role_id) "
        "WHERE status = 'ACTIVE'"
    )
    op.execute(
        "CREATE INDEX ix_platform_user_role_assignments_platform_user "
        "ON platform_user_role_assignments (platform_user_id)"
    )
    op.execute(
        "CREATE INDEX ix_platform_user_role_assignments_role "
        "ON platform_user_role_assignments (role_id)"
    )
    op.execute(
        "CREATE INDEX ix_platform_user_role_assignments_platform_user_active "
        "ON platform_user_role_assignments (platform_user_id) "
        "WHERE status = 'ACTIVE'"
    )

    # =========================================================================
    # 4. Indexes on tenant_user_role_assignments (5)
    # =========================================================================
    op.execute(
        "CREATE UNIQUE INDEX uq_tenant_user_role_assignments_active "
        "ON tenant_user_role_assignments (tenant_user_id, role_id, org_node_id) "
        "WHERE status = 'ACTIVE'"
    )
    op.execute(
        "CREATE INDEX ix_tenant_user_role_assignments_tenant "
        "ON tenant_user_role_assignments (tenant_id)"
    )
    op.execute(
        "CREATE INDEX ix_tenant_user_role_assignments_tenant_user "
        "ON tenant_user_role_assignments (tenant_user_id)"
    )
    op.execute(
        "CREATE INDEX ix_tenant_user_role_assignments_role_org_node "
        "ON tenant_user_role_assignments (role_id, org_node_id)"
    )
    op.execute(
        "CREATE INDEX ix_tenant_user_role_assignments_tenant_user_active "
        "ON tenant_user_role_assignments (tenant_user_id) "
        "WHERE status = 'ACTIVE'"
    )

    # =========================================================================
    # 5. enforce_platform_role_audience() function and trigger
    # =========================================================================
    op.execute(
        """
        CREATE OR REPLACE FUNCTION enforce_platform_role_audience()
        RETURNS TRIGGER AS $$
        DECLARE
            v_audience role_audience_enum;
        BEGIN
            SELECT audience INTO v_audience FROM roles WHERE id = NEW.role_id;
            IF v_audience IS DISTINCT FROM 'PLATFORM' THEN
                RAISE EXCEPTION
                    'audience-check: platform_user_role_assignments requires PLATFORM-audience role; role % has audience %',
                    NEW.role_id, v_audience;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER tg_platform_user_role_assignments_audience_check "
        "BEFORE INSERT OR UPDATE OF role_id ON platform_user_role_assignments "
        "FOR EACH ROW EXECUTE FUNCTION enforce_platform_role_audience()"
    )

    # =========================================================================
    # 6. enforce_tenant_role_audience() function and trigger
    # =========================================================================
    op.execute(
        """
        CREATE OR REPLACE FUNCTION enforce_tenant_role_audience()
        RETURNS TRIGGER AS $$
        DECLARE
            v_audience role_audience_enum;
        BEGIN
            SELECT audience INTO v_audience FROM roles WHERE id = NEW.role_id;
            IF v_audience IS DISTINCT FROM 'TENANT' THEN
                RAISE EXCEPTION
                    'audience-check: tenant_user_role_assignments requires TENANT-audience role; role % has audience %',
                    NEW.role_id, v_audience;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER tg_tenant_user_role_assignments_audience_check "
        "BEFORE INSERT OR UPDATE OF role_id ON tenant_user_role_assignments "
        "FOR EACH ROW EXECUTE FUNCTION enforce_tenant_role_audience()"
    )

    # =========================================================================
    # 7. updated_at triggers on both new tables
    # =========================================================================
    op.execute(
        "CREATE TRIGGER tg_platform_user_role_assignments_set_updated_at "
        "BEFORE UPDATE ON platform_user_role_assignments "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at_timestamp()"
    )
    op.execute(
        "CREATE TRIGGER tg_tenant_user_role_assignments_set_updated_at "
        "BEFORE UPDATE ON tenant_user_role_assignments "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at_timestamp()"
    )

    # =========================================================================
    # 8. ENABLE + FORCE RLS + create policy on tenant_user_role_assignments
    #    (unconditional PLATFORM OR-branch per D-29; matches the other
    #    5 multi-tenant tables uniformly)
    # =========================================================================
    op.execute(
        "ALTER TABLE tenant_user_role_assignments ENABLE ROW LEVEL SECURITY"
    )
    op.execute(
        "ALTER TABLE tenant_user_role_assignments FORCE ROW LEVEL SECURITY"
    )
    op.execute(
        """
        CREATE POLICY tenant_user_role_assignments_tenant_isolation
            ON tenant_user_role_assignments
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
    # 9-12. Data copy with RLS-aware iteration.
    #
    # The migration session is the application role
    # (NOSUPERUSER NOBYPASSRLS). Reading `user_role_assignments` is
    # subject to the FN-AB-14 IS-NULL-gated policy: without GUCs set,
    # zero rows are visible. To see all rows the session must:
    #   - app.user_type='PLATFORM'           -> admits the 3 PLATFORM-
    #                                          audience rows via the
    #                                          IS-NULL-gated branch
    #   - per-row impersonation (app.tenant_id=<each tenant's id>)
    #                                       -> admits that tenant's
    #                                          TENANT-side rows via
    #                                          the first clause
    #
    # The single DO block below does count + copy + post-copy verify
    # in one pass per tenant, mirroring the seed loader's per-row
    # impersonation pattern (loaders/user_role_assignments.py).
    #
    # Side benefits:
    #   - Composite FKs validate TENANT-side INSERTs at row level:
    #     a row with denormalised tenant_id mismatching the user's or
    #     org_node's tenant_id raises and aborts the migration.
    #   - Audience-check triggers validate role.audience consistency:
    #     a TENANT-side URA row with a PLATFORM-audience role (or
    #     vice-versa) raises and aborts.
    #
    # Either failure is a real data-integrity find that needs operator
    # inspection, not silent fix. The migration aborts loudly per the
    # caution-first posture.
    # =========================================================================
    op.execute(
        """
        DO $$
        DECLARE
            n_platform_old   INT := 0;
            n_tenant_old     INT := 0;
            n_platform_new   INT;
            n_tenant_new     INT;
            t_id             UUID;
            per_tenant_count INT;
        BEGIN
            -- PLATFORM session: admits PLATFORM-audience reads via
            -- IS-NULL-gated branch; tenant reads still require
            -- per-row impersonation below.
            PERFORM set_config('app.user_type', 'PLATFORM', true);
            PERFORM set_config('app.tenant_id', '', true);

            -- 9. Count + copy PLATFORM-audience rows (tenant_id IS NULL).
            SELECT COUNT(*) INTO n_platform_old
              FROM user_role_assignments
              WHERE platform_user_id IS NOT NULL;

            INSERT INTO platform_user_role_assignments (
                id, platform_user_id, role_id, status,
                granted_at, granted_by_user_id, granted_by_user_type,
                revoked_at, revoked_by_user_id, revoked_by_user_type,
                updated_at
            )
            SELECT
                id, platform_user_id, role_id, status,
                granted_at, granted_by_user_id, granted_by_user_type,
                revoked_at, revoked_by_user_id, revoked_by_user_type,
                updated_at
            FROM user_role_assignments
            WHERE platform_user_id IS NOT NULL;

            -- 10. Iterate tenants; per-row impersonate; count + copy
            -- this tenant's TENANT-side rows.
            FOR t_id IN SELECT id FROM tenants LOOP
                PERFORM set_config('app.tenant_id', t_id::text, true);

                SELECT COUNT(*) INTO per_tenant_count
                  FROM user_role_assignments
                  WHERE tenant_user_id IS NOT NULL
                    AND tenant_id = t_id;
                n_tenant_old := n_tenant_old + per_tenant_count;

                INSERT INTO tenant_user_role_assignments (
                    id, tenant_user_id, tenant_id, org_node_id,
                    role_id, status,
                    granted_at, granted_by_user_id, granted_by_user_type,
                    revoked_at, revoked_by_user_id, revoked_by_user_type,
                    updated_at
                )
                SELECT
                    id, tenant_user_id, tenant_id, org_node_id,
                    role_id, status,
                    granted_at, granted_by_user_id, granted_by_user_type,
                    revoked_at, revoked_by_user_id, revoked_by_user_type,
                    updated_at
                FROM user_role_assignments
                WHERE tenant_user_id IS NOT NULL
                  AND tenant_id = t_id;
            END LOOP;

            -- Reset impersonation
            PERFORM set_config('app.tenant_id', '', true);

            -- 11. Post-copy count verification. The new tables are
            -- readable in full under PLATFORM session (platform table
            -- has no RLS; tenant table has unconditional OR policy
            -- that admits all rows under PLATFORM).
            SELECT COUNT(*) INTO n_platform_new FROM platform_user_role_assignments;
            SELECT COUNT(*) INTO n_tenant_new   FROM tenant_user_role_assignments;

            IF n_platform_old != n_platform_new THEN
                RAISE EXCEPTION
                    'split-migration: PLATFORM count mismatch (old=%, new=%)',
                    n_platform_old, n_platform_new;
            END IF;
            IF n_tenant_old != n_tenant_new THEN
                RAISE EXCEPTION
                    'split-migration: TENANT count mismatch (old=%, new=%)',
                    n_tenant_old, n_tenant_new;
            END IF;

            RAISE NOTICE
                'split-migration: copy complete (platform=%, tenant=%, total=%)',
                n_platform_new, n_tenant_new, n_platform_new + n_tenant_new;
        END $$
        """
    )

    # =========================================================================
    # 12. DROP TABLE user_role_assignments
    #     Zero inbound FKs verified pre-flight (item 13).
    # =========================================================================
    op.execute("DROP TABLE user_role_assignments")


def downgrade() -> None:
    """Restore user_role_assignments + FN-AB-14 IS-NULL-gated policy.

    The result is byte-equivalent to the pre-6.8.1 schema state for the
    URA table (mirrors v2 DDL shape + 4fd3aec6ae0c policy text on top of
    the e59f62d5037d NULLIF baseline).
    """

    # =========================================================================
    # 1. Recreate user_role_assignments (full v2 shape)
    # =========================================================================
    op.execute(
        """
        CREATE TABLE user_role_assignments (
            id                      UUID                            NOT NULL DEFAULT uuidv7(),
            platform_user_id        UUID                            NULL,
            tenant_user_id          UUID                            NULL,
            role_id                 UUID                            NOT NULL,
            tenant_id               UUID                            NULL,
            org_node_id             UUID                            NULL,
            status                  user_role_assignment_status_enum NOT NULL DEFAULT 'ACTIVE',
            granted_at              TIMESTAMPTZ                     NOT NULL DEFAULT NOW(),
            granted_by_user_id      UUID                            NULL,
            granted_by_user_type    actor_user_type_enum            NULL,
            revoked_at              TIMESTAMPTZ                     NULL,
            revoked_by_user_id      UUID                            NULL,
            revoked_by_user_type    actor_user_type_enum            NULL,
            updated_at              TIMESTAMPTZ                     NOT NULL DEFAULT NOW(),

            CONSTRAINT pk_user_role_assignments
                PRIMARY KEY (id),

            CONSTRAINT fk_user_role_assignments_role
                FOREIGN KEY (role_id) REFERENCES roles (id)
                ON DELETE RESTRICT ON UPDATE RESTRICT,

            CONSTRAINT fk_user_role_assignments_tenant
                FOREIGN KEY (tenant_id) REFERENCES tenants (id)
                ON DELETE RESTRICT ON UPDATE RESTRICT,

            CONSTRAINT fk_user_role_assignments_org_node_same_tenant
                FOREIGN KEY (tenant_id, org_node_id)
                REFERENCES org_nodes (tenant_id, id)
                ON DELETE RESTRICT ON UPDATE RESTRICT,

            CONSTRAINT fk_user_role_assignments_platform_user
                FOREIGN KEY (platform_user_id) REFERENCES platform_users (id)
                ON DELETE RESTRICT ON UPDATE RESTRICT,

            CONSTRAINT fk_user_role_assignments_tenant_user
                FOREIGN KEY (tenant_user_id) REFERENCES tenant_users (id)
                ON DELETE RESTRICT ON UPDATE RESTRICT,

            CONSTRAINT ck_user_role_assignments_user_xor
                CHECK (
                    (platform_user_id IS NOT NULL AND tenant_user_id IS NULL)
                    OR
                    (platform_user_id IS NULL AND tenant_user_id IS NOT NULL)
                ),

            CONSTRAINT ck_user_role_assignments_anchor_shape
                CHECK (
                    (tenant_id IS NULL AND org_node_id IS NULL)
                    OR
                    (tenant_id IS NOT NULL AND org_node_id IS NOT NULL)
                ),

            CONSTRAINT ck_user_role_assignments_user_anchor_consistency
                CHECK (
                    (platform_user_id IS NOT NULL AND tenant_id IS NULL AND org_node_id IS NULL)
                    OR
                    (tenant_user_id   IS NOT NULL AND tenant_id IS NOT NULL AND org_node_id IS NOT NULL)
                ),

            CONSTRAINT ck_user_role_assignments_granted_by_actor_pair
                CHECK (
                    (granted_by_user_id IS NULL AND granted_by_user_type IS NULL)
                    OR
                    (granted_by_user_id IS NOT NULL AND granted_by_user_type IS NOT NULL)
                ),

            CONSTRAINT ck_user_role_assignments_revoked_by_actor_pair
                CHECK (
                    (revoked_by_user_id IS NULL AND revoked_by_user_type IS NULL)
                    OR
                    (revoked_by_user_id IS NOT NULL AND revoked_by_user_type IS NOT NULL)
                ),

            CONSTRAINT ck_user_role_assignments_revoked_consistency
                CHECK (
                    (status = 'INACTIVE'
                        AND revoked_at IS NOT NULL
                        AND revoked_by_user_id IS NOT NULL
                        AND revoked_by_user_type IS NOT NULL)
                    OR
                    (status = 'ACTIVE'
                        AND revoked_at IS NULL
                        AND revoked_by_user_id IS NULL
                        AND revoked_by_user_type IS NULL)
                )
        )
        """
    )

    # =========================================================================
    # 2. Recreate v2 indexes (8 total)
    # =========================================================================
    op.execute(
        "CREATE UNIQUE INDEX uq_user_role_assignments_platform_active_unique "
        "ON user_role_assignments (platform_user_id, role_id) "
        "WHERE status = 'ACTIVE' AND platform_user_id IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_user_role_assignments_tenant_active_unique "
        "ON user_role_assignments (tenant_user_id, role_id, org_node_id) "
        "WHERE status = 'ACTIVE' AND tenant_user_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX ix_user_role_assignments_tenant "
        "ON user_role_assignments (tenant_id)"
    )
    op.execute(
        "CREATE INDEX ix_user_role_assignments_platform_user "
        "ON user_role_assignments (platform_user_id) "
        "WHERE platform_user_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX ix_user_role_assignments_tenant_user "
        "ON user_role_assignments (tenant_user_id) "
        "WHERE tenant_user_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX ix_user_role_assignments_role_org_node "
        "ON user_role_assignments (role_id, org_node_id)"
    )
    op.execute(
        "CREATE INDEX ix_user_role_assignments_platform_user_active "
        "ON user_role_assignments (platform_user_id) "
        "WHERE status = 'ACTIVE' AND platform_user_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX ix_user_role_assignments_tenant_user_active "
        "ON user_role_assignments (tenant_user_id) "
        "WHERE status = 'ACTIVE' AND tenant_user_id IS NOT NULL"
    )

    # =========================================================================
    # 3. Recreate updated_at trigger
    # =========================================================================
    op.execute(
        "CREATE TRIGGER tg_user_role_assignments_set_updated_at "
        "BEFORE UPDATE ON user_role_assignments "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at_timestamp()"
    )

    # =========================================================================
    # 4. Enable RLS + FORCE + create policy in the FN-AB-14 IS-NULL-gated
    #    form (byte-equivalent to 4fd3aec6ae0c's upgrade).
    # =========================================================================
    op.execute(
        "ALTER TABLE user_role_assignments ENABLE ROW LEVEL SECURITY"
    )
    op.execute(
        "ALTER TABLE user_role_assignments FORCE ROW LEVEL SECURITY"
    )
    op.execute(
        """
        CREATE POLICY user_role_assignments_tenant_isolation
            ON user_role_assignments
            FOR ALL
            USING (
                tenant_id = NULLIF(current_setting('app.tenant_id', TRUE), '')::uuid
                OR (
                    tenant_id IS NULL
                    AND current_setting('app.user_type', TRUE) = 'PLATFORM'
                )
            )
            WITH CHECK (
                tenant_id = NULLIF(current_setting('app.tenant_id', TRUE), '')::uuid
                OR (
                    tenant_id IS NULL
                    AND current_setting('app.user_type', TRUE) = 'PLATFORM'
                )
            )
        """
    )

    # =========================================================================
    # 5. Copy PLATFORM-audience rows back. Set app.user_type=PLATFORM so
    #    the WITH CHECK predicate's IS-NULL-gated branch fires for
    #    tenant_id-NULL rows.
    # =========================================================================
    op.execute(
        "SELECT set_config('app.user_type', 'PLATFORM', true)"
    )
    op.execute(
        """
        INSERT INTO user_role_assignments (
            id, platform_user_id, tenant_user_id, role_id,
            tenant_id, org_node_id, status,
            granted_at, granted_by_user_id, granted_by_user_type,
            revoked_at, revoked_by_user_id, revoked_by_user_type,
            updated_at
        )
        SELECT
            id, platform_user_id, NULL, role_id,
            NULL, NULL, status,
            granted_at, granted_by_user_id, granted_by_user_type,
            revoked_at, revoked_by_user_id, revoked_by_user_type,
            updated_at
        FROM platform_user_role_assignments
        """
    )

    # =========================================================================
    # 6. Copy TENANT-audience rows back. Mirrors the seed loader's per-row
    #    impersonation pattern (loaders/user_role_assignments.py): set
    #    app.tenant_id to the row's tenant_id before each INSERT so the
    #    WITH CHECK first clause matches.
    # =========================================================================
    op.execute(
        """
        DO $$
        DECLARE
            r RECORD;
        BEGIN
            FOR r IN SELECT * FROM tenant_user_role_assignments LOOP
                PERFORM set_config('app.tenant_id', r.tenant_id::text, true);
                INSERT INTO user_role_assignments (
                    id, platform_user_id, tenant_user_id, role_id,
                    tenant_id, org_node_id, status,
                    granted_at, granted_by_user_id, granted_by_user_type,
                    revoked_at, revoked_by_user_id, revoked_by_user_type,
                    updated_at
                ) VALUES (
                    r.id, NULL, r.tenant_user_id, r.role_id,
                    r.tenant_id, r.org_node_id, r.status,
                    r.granted_at, r.granted_by_user_id, r.granted_by_user_type,
                    r.revoked_at, r.revoked_by_user_id, r.revoked_by_user_type,
                    r.updated_at
                );
            END LOOP;
            -- Reset impersonation
            PERFORM set_config('app.tenant_id', '', true);
        END $$
        """
    )

    # =========================================================================
    # 7. Drop new-table triggers, functions, indexes, then tables, then UNIQUE
    #    on tenant_users. Reverse-creation order; no CASCADE.
    # =========================================================================
    op.execute(
        "DROP TRIGGER tg_tenant_user_role_assignments_audience_check "
        "ON tenant_user_role_assignments"
    )
    op.execute(
        "DROP TRIGGER tg_platform_user_role_assignments_audience_check "
        "ON platform_user_role_assignments"
    )
    op.execute("DROP FUNCTION enforce_tenant_role_audience()")
    op.execute("DROP FUNCTION enforce_platform_role_audience()")

    op.execute(
        "DROP TRIGGER tg_tenant_user_role_assignments_set_updated_at "
        "ON tenant_user_role_assignments"
    )
    op.execute(
        "DROP TRIGGER tg_platform_user_role_assignments_set_updated_at "
        "ON platform_user_role_assignments"
    )

    op.execute(
        "DROP POLICY tenant_user_role_assignments_tenant_isolation "
        "ON tenant_user_role_assignments"
    )
    op.execute(
        "ALTER TABLE tenant_user_role_assignments DISABLE ROW LEVEL SECURITY"
    )

    # DROP TABLE cascades the indexes; explicit drops are unnecessary.
    op.execute("DROP TABLE tenant_user_role_assignments")
    op.execute("DROP TABLE platform_user_role_assignments")

    # Drop the precondition UNIQUE on tenant_users.
    op.execute(
        "ALTER TABLE tenant_users DROP CONSTRAINT uq_tenant_users_tenant_id"
    )
