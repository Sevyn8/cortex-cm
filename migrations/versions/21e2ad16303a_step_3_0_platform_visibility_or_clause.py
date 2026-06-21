"""step_3_0_platform_visibility_or_clause

Revision ID: 21e2ad16303a
Revises: 4fd3aec6ae0c
Create Date: 2026-05-02 19:45:37.954207

Step 3.0: extends the FN-AB-14 PLATFORM-visibility pattern to the four
remaining multi-tenant RLS policies (tenants, tenant_users, org_nodes,
stores). Without this clause:

  - READ: a PLATFORM session (app.tenant_id = NULL,
    app.user_type = 'PLATFORM') sees zero rows on these tables; the
    list-tenants endpoint planned for Step 3.3 returns an empty list.
  - WRITE: a PLATFORM session cannot INSERT into these tables either;
    the WITH CHECK predicate id/tenant_id = NULLIF(NULL, '')::uuid
    evaluates to UNKNOWN, which RLS treats as a violation. Test
    fixtures and seed scripts cannot insert tenant rows from the
    NOSUPERUSER NOBYPASSRLS application role.

Critical structural difference vs. FN-AB-14 (4fd3aec6ae0c).

  FN-AB-14's OR-branch is gated by `tenant_id IS NULL AND ...` because
  user_role_assignments.tenant_id is NULLABLE (PLATFORM-audience rows
  carry tenant_id NULL). On these four tables the column is NOT NULL,
  so the IS-NULL gate would never fire and the OR-branch would be a
  no-op. The correct shape here is unconditional:

      OR current_setting('app.user_type', TRUE) = 'PLATFORM'

Permissive impersonation property. When app.tenant_id is set AND
app.user_type = 'PLATFORM', the OR-clause's PLATFORM branch is TRUE
for every row, so the user sees all rows on these tables (not just
the impersonated tenant's). v0 keeps this permissive; if v1 needs
RLS-enforced impersonation-scoping, the policy needs a third state.
See D-29.

NULLIF wrapper preserved per D-27.

tenants is the column-name exception: its policy compares `id` (its
own PK), not `tenant_id`. The other three use `tenant_id`.

Downgrade restores the post-NULLIF (e59f62d5037d) form with no
OR-clause, NOT the pre-NULLIF original. Reversing only this revision
leaves the NULLIF wrapper intact on these four policies.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '21e2ad16303a'
down_revision: Union[str, Sequence[str], None] = '4fd3aec6ae0c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add unconditional PLATFORM-visibility OR-clause to four multi-
    tenant policies."""

    # tenants — column is `id`, not `tenant_id`.
    op.execute("DROP POLICY tenants_self_access ON tenants")
    op.execute(
        """
        CREATE POLICY tenants_self_access
        ON tenants
          FOR ALL
          USING (
            id = NULLIF(
                current_setting('app.tenant_id', TRUE), ''
            )::uuid
            OR current_setting('app.user_type', TRUE) = 'PLATFORM'
          )
          WITH CHECK (
            id = NULLIF(
                current_setting('app.tenant_id', TRUE), ''
            )::uuid
            OR current_setting('app.user_type', TRUE) = 'PLATFORM'
          )
        """
    )

    # tenant_users
    op.execute(
        "DROP POLICY tenant_users_tenant_isolation ON tenant_users"
    )
    op.execute(
        """
        CREATE POLICY tenant_users_tenant_isolation
        ON tenant_users
          FOR ALL
          USING (
            tenant_id = NULLIF(
                current_setting('app.tenant_id', TRUE), ''
            )::uuid
            OR current_setting('app.user_type', TRUE) = 'PLATFORM'
          )
          WITH CHECK (
            tenant_id = NULLIF(
                current_setting('app.tenant_id', TRUE), ''
            )::uuid
            OR current_setting('app.user_type', TRUE) = 'PLATFORM'
          )
        """
    )

    # org_nodes
    op.execute("DROP POLICY org_nodes_tenant_isolation ON org_nodes")
    op.execute(
        """
        CREATE POLICY org_nodes_tenant_isolation
        ON org_nodes
          FOR ALL
          USING (
            tenant_id = NULLIF(
                current_setting('app.tenant_id', TRUE), ''
            )::uuid
            OR current_setting('app.user_type', TRUE) = 'PLATFORM'
          )
          WITH CHECK (
            tenant_id = NULLIF(
                current_setting('app.tenant_id', TRUE), ''
            )::uuid
            OR current_setting('app.user_type', TRUE) = 'PLATFORM'
          )
        """
    )

    # stores
    op.execute("DROP POLICY stores_tenant_isolation ON stores")
    op.execute(
        """
        CREATE POLICY stores_tenant_isolation
        ON stores
          FOR ALL
          USING (
            tenant_id = NULLIF(
                current_setting('app.tenant_id', TRUE), ''
            )::uuid
            OR current_setting('app.user_type', TRUE) = 'PLATFORM'
          )
          WITH CHECK (
            tenant_id = NULLIF(
                current_setting('app.tenant_id', TRUE), ''
            )::uuid
            OR current_setting('app.user_type', TRUE) = 'PLATFORM'
          )
        """
    )


def downgrade() -> None:
    """Restore the post-NULLIF, single-clause form (e59f62d5037d state)."""

    op.execute("DROP POLICY tenants_self_access ON tenants")
    op.execute(
        """
        CREATE POLICY tenants_self_access
        ON tenants
          FOR ALL
          USING (
            id = NULLIF(
                current_setting('app.tenant_id', TRUE), ''
            )::uuid
          )
          WITH CHECK (
            id = NULLIF(
                current_setting('app.tenant_id', TRUE), ''
            )::uuid
          )
        """
    )

    op.execute(
        "DROP POLICY tenant_users_tenant_isolation ON tenant_users"
    )
    op.execute(
        """
        CREATE POLICY tenant_users_tenant_isolation
        ON tenant_users
          FOR ALL
          USING (
            tenant_id = NULLIF(
                current_setting('app.tenant_id', TRUE), ''
            )::uuid
          )
          WITH CHECK (
            tenant_id = NULLIF(
                current_setting('app.tenant_id', TRUE), ''
            )::uuid
          )
        """
    )

    op.execute("DROP POLICY org_nodes_tenant_isolation ON org_nodes")
    op.execute(
        """
        CREATE POLICY org_nodes_tenant_isolation
        ON org_nodes
          FOR ALL
          USING (
            tenant_id = NULLIF(
                current_setting('app.tenant_id', TRUE), ''
            )::uuid
          )
          WITH CHECK (
            tenant_id = NULLIF(
                current_setting('app.tenant_id', TRUE), ''
            )::uuid
          )
        """
    )

    op.execute("DROP POLICY stores_tenant_isolation ON stores")
    op.execute(
        """
        CREATE POLICY stores_tenant_isolation
        ON stores
          FOR ALL
          USING (
            tenant_id = NULLIF(
                current_setting('app.tenant_id', TRUE), ''
            )::uuid
          )
          WITH CHECK (
            tenant_id = NULLIF(
                current_setting('app.tenant_id', TRUE), ''
            )::uuid
          )
        """
    )
