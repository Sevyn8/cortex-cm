"""fix: schema-qualify identifiers inside audience-check trigger functions

Revision ID: a0982a86985b
Revises: 3e05299cb533
Create Date: 2026-05-15 10:49:36.416707

Cloud-emergent bug, layer 2 of CSD-03. Commit dd496bd closed the
application-side raw SQL casts (unqualified enum types in ``text()``
queries). This migration closes the same bug one level deeper, inside
plpgsql trigger function bodies.

Two functions created by migration ``3e05299cb533`` had unqualified
references in their bodies:

  - ``core.enforce_tenant_role_audience()``
  - ``core.enforce_platform_role_audience()``

Both declared ``v_audience role_audience_enum;`` and selected from
unqualified ``roles``. plpgsql resolves these via the calling session's
search_path. On Cloud SQL, per CSD-03, the connect-time hook setting
search_path does not always mask reliably; when the trigger fires on
INSERT into ``tenant_user_role_assignments`` or
``platform_user_role_assignments``, the function body crashes with
``UndefinedObject: type "role_audience_enum" does not exist``.

Fix shape: ``CREATE OR REPLACE FUNCTION`` with explicit schema
qualification inside the function body. The schema name is captured
from ``current_schema()`` at migration time (env.py has already SET
search_path on this connection) and baked into the stored function
body verbatim. Per D-15 the schema is per-env and the migration runs
once per env; the function body's literal schema name matches that
env's ``DB_SCHEMA``.

Matches the convention applied app-side at dd496bd: don't rely on
search_path; qualify all non-public identifiers. No data touch; no
schema lock; CREATE OR REPLACE is safe.

Step 6.10.2 (platform-users writes) will exercise
``enforce_platform_role_audience()``; fixing both functions now
pre-empts that recurrence path.

Forward-only per project migration convention (mirrors Step 6.7's
``2fdc4bc9f4cb``, Step 6.6's ``cec8fae734e0``, Step 6.1's
``90cd038ae618``).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a0982a86985b'
down_revision: Union[str, Sequence[str], None] = '3e05299cb533'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Replace both audience-check trigger functions with bodies that
    explicitly schema-qualify ``role_audience_enum`` and ``roles``.

    Pattern matches cec8fae734e0's ``bind = op.get_bind()`` + ``sa.text``
    convention for executing raw SQL with results inside a migration.
    ``current_schema()`` returns the schema env.py just SET on this
    connection; same source as DB_SCHEMA.
    """
    bind = op.get_bind()
    schema = bind.execute(sa.text("SELECT current_schema()")).scalar_one()

    # Replace tenant-side audience check.
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {schema}.enforce_tenant_role_audience()
        RETURNS TRIGGER AS $$
        DECLARE
            v_audience {schema}.role_audience_enum;
        BEGIN
            SELECT audience INTO v_audience
            FROM {schema}.roles
            WHERE id = NEW.role_id;
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

    # Replace platform-side audience check.
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {schema}.enforce_platform_role_audience()
        RETURNS TRIGGER AS $$
        DECLARE
            v_audience {schema}.role_audience_enum;
        BEGIN
            SELECT audience INTO v_audience
            FROM {schema}.roles
            WHERE id = NEW.role_id;
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


def downgrade() -> None:
    raise NotImplementedError(
        "fix_schema_qualify_identifiers_inside_audience_check_trigger_functions "
        "is forward-only. Restoring the pre-fix function bodies (with "
        "unqualified identifiers) would re-introduce the production 500 "
        "(cloud-emergent bug class CSD-03 fixed at dd496bd and this "
        "revision). Restore from backup if rollback is required. "
        "Mirrors Step 6.7's 2fdc4bc9f4cb, Step 6.6's cec8fae734e0, and "
        "Step 6.1's 90cd038ae618."
    )
