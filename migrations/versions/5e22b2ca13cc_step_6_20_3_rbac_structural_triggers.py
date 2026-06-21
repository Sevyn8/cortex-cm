"""step_6_20_3 RBAC structural enforcement triggers

Revision ID: 5e22b2ca13cc
Revises: a0982a86985b
Create Date: 2026-05-20 15:07:05.644550

Adds three Postgres triggers closing structural enforcement gaps that
app-layer checks alone cannot guarantee against direct-SQL, seed-loader,
or future-endpoint bypass paths:

  1. tg_role_permissions_audience_scope_coherence
     BEFORE INSERT OR UPDATE OF role_id, permission_id on role_permissions.
     Rejects (TENANT-audience role x GLOBAL-scope permission) rows.
     Backstops Step 6.18.3 LD17 PATCH-side check; mirrors the
     enforce_*_role_audience precedent at rbac_v3.sql:421-439 / 581-599.

  2. tg_role_permissions_protect_super_admin_override
     BEFORE DELETE on role_permissions.
     Rejects deletion of the (SUPER_ADMIN, ADMIN.ROLES.OVERRIDE.GLOBAL)
     grant. Platform-bootstrap protection.

  3. tg_roles_protect_super_admin
     BEFORE UPDATE OR DELETE on roles.
     Rejects deletion or status/code/audience mutation of the SUPER_ADMIN
     row. Name and description remain editable (branding flexibility).

Schema qualification follows the a0982a86985b convention: read
current_schema() at migration time and f-string-interpolate {schema}.
on every cross-schema reference inside the function body. Per CSD-03,
plpgsql function bodies must not rely on search_path at trigger-fire
time on Cloud SQL.

Pre-check responsibility (LD4): operator verifies zero pre-existing
violations in seed Excel + local DB + Cloud SQL before this migration
runs. Migration body is pure DDL; no data touch.

Trigger error shape (LD7 superseded): plain RAISE EXCEPTION with default
SQLSTATE P0001 (raise_exception), mirroring the surviving
enforce_*_role_audience precedent. The earlier prompt suggestion of
USING ERRCODE = '23514' was incorrect; precedent uses neither.

Reversible: downgrade drops the 3 triggers then the 3 functions in
reverse order.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5e22b2ca13cc'
down_revision: Union[str, Sequence[str], None] = 'a0982a86985b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create 3 functions + 3 triggers, schema-qualified per a0982a86985b."""
    bind = op.get_bind()
    schema = bind.execute(sa.text("SELECT current_schema()")).scalar_one()

    # Trigger 1: TENANT-audience role x GLOBAL-scope permission ban.
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {schema}.enforce_role_audience_scope_coherence()
        RETURNS TRIGGER AS $$
        DECLARE
            v_role_audience {schema}.role_audience_enum;
            v_perm_scope {schema}.permission_scope_enum;
        BEGIN
            SELECT audience INTO v_role_audience
            FROM {schema}.roles
            WHERE id = NEW.role_id;
            SELECT scope INTO v_perm_scope
            FROM {schema}.permissions
            WHERE id = NEW.permission_id;
            IF v_role_audience = 'TENANT' AND v_perm_scope = 'GLOBAL' THEN
                RAISE EXCEPTION
                    'audience-scope-check: TENANT-audience role cannot hold GLOBAL-scope permission (role_id=%, permission_id=%)',
                    NEW.role_id, NEW.permission_id;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER tg_role_permissions_audience_scope_coherence
            BEFORE INSERT OR UPDATE OF role_id, permission_id
            ON {schema}.role_permissions
            FOR EACH ROW
            EXECUTE FUNCTION {schema}.enforce_role_audience_scope_coherence()
        """
    )

    # Trigger 2: SUPER_ADMIN x OVERRIDE.GLOBAL grant deletion pin.
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {schema}.protect_super_admin_override_global_grant()
        RETURNS TRIGGER AS $$
        DECLARE
            v_super_admin_id UUID;
            v_override_global_id UUID;
        BEGIN
            SELECT id INTO v_super_admin_id
            FROM {schema}.roles
            WHERE code = 'SUPER_ADMIN';
            SELECT id INTO v_override_global_id
            FROM {schema}.permissions
            WHERE code = 'ADMIN.ROLES.OVERRIDE.GLOBAL';
            IF OLD.role_id = v_super_admin_id AND OLD.permission_id = v_override_global_id THEN
                RAISE EXCEPTION
                    'bootstrap-protection: cannot delete SUPER_ADMIN x ADMIN.ROLES.OVERRIDE.GLOBAL grant';
            END IF;
            RETURN OLD;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER tg_role_permissions_protect_super_admin_override
            BEFORE DELETE
            ON {schema}.role_permissions
            FOR EACH ROW
            EXECUTE FUNCTION {schema}.protect_super_admin_override_global_grant()
        """
    )

    # Trigger 3: SUPER_ADMIN role status/code/audience pin + DELETE block.
    # Function dispatches on TG_OP because UPDATE and DELETE share one
    # trigger declaration. Name and description remain editable (LD3).
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {schema}.protect_super_admin_role()
        RETURNS TRIGGER AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                IF OLD.code = 'SUPER_ADMIN' THEN
                    RAISE EXCEPTION
                        'bootstrap-protection: SUPER_ADMIN role cannot be deleted';
                END IF;
                RETURN OLD;
            ELSIF TG_OP = 'UPDATE' THEN
                IF OLD.code = 'SUPER_ADMIN' AND (
                    NEW.code IS DISTINCT FROM OLD.code OR
                    NEW.status IS DISTINCT FROM OLD.status OR
                    NEW.audience IS DISTINCT FROM OLD.audience
                ) THEN
                    RAISE EXCEPTION
                        'bootstrap-protection: SUPER_ADMIN role status, code, and audience are immutable';
                END IF;
                RETURN NEW;
            END IF;
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER tg_roles_protect_super_admin
            BEFORE UPDATE OR DELETE
            ON {schema}.roles
            FOR EACH ROW
            EXECUTE FUNCTION {schema}.protect_super_admin_role()
        """
    )


def downgrade() -> None:
    """Drop 3 triggers then 3 functions, reverse order of upgrade."""
    bind = op.get_bind()
    schema = bind.execute(sa.text("SELECT current_schema()")).scalar_one()

    op.execute(f"DROP TRIGGER IF EXISTS tg_roles_protect_super_admin ON {schema}.roles")
    op.execute(
        f"DROP TRIGGER IF EXISTS tg_role_permissions_protect_super_admin_override "
        f"ON {schema}.role_permissions"
    )
    op.execute(
        f"DROP TRIGGER IF EXISTS tg_role_permissions_audience_scope_coherence "
        f"ON {schema}.role_permissions"
    )
    op.execute(f"DROP FUNCTION IF EXISTS {schema}.protect_super_admin_role()")
    op.execute(f"DROP FUNCTION IF EXISTS {schema}.protect_super_admin_override_global_grant()")
    op.execute(f"DROP FUNCTION IF EXISTS {schema}.enforce_role_audience_scope_coherence()")
