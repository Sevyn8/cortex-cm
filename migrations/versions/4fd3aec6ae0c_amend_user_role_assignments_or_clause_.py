"""amend user_role_assignments or-clause for platform-audience

Revision ID: 4fd3aec6ae0c
Revises: e59f62d5037d
Create Date: 2026-05-02 00:54:35.034992

Step 2.2b: closes FN-AB-14. Replaces the single-clause USING/CHECK on
user_role_assignments_tenant_isolation with a two-clause form that
permits PLATFORM-audience rows (tenant_id NULL) when
app.user_type = 'PLATFORM'. The other 4 multi-tenant policies are
not touched: their tenant_id columns are NOT NULL, so the OR-branch
would never fire there.

Permissive scope: a PLATFORM user with app.tenant_id set to a real
tenant (impersonation case per D-24) matches the first clause AND
sees PLATFORM-audience rows via the second. v0 keeps this permissive;
revisit if/when impersonation rules tighten.

NULLIF wrapper preserved per D-27.

Downgrade restores the post-NULLIF (e59f62d5037d) form, NOT the
pre-NULLIF original. Reversing only this revision leaves the NULLIF
wrapper intact.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '4fd3aec6ae0c'
down_revision: Union[str, Sequence[str], None] = 'e59f62d5037d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Amend user_role_assignments_tenant_isolation: add OR-clause for
    PLATFORM-audience rows."""
    op.execute(
        "DROP POLICY user_role_assignments_tenant_isolation "
        "ON user_role_assignments"
    )
    op.execute(
        """
        CREATE POLICY user_role_assignments_tenant_isolation
        ON user_role_assignments
          USING (
            tenant_id = NULLIF(
                current_setting('app.tenant_id', TRUE), ''
            )::uuid
            OR (
                tenant_id IS NULL
                AND current_setting('app.user_type', TRUE) = 'PLATFORM'
            )
          )
          WITH CHECK (
            tenant_id = NULLIF(
                current_setting('app.tenant_id', TRUE), ''
            )::uuid
            OR (
                tenant_id IS NULL
                AND current_setting('app.user_type', TRUE) = 'PLATFORM'
            )
          )
        """
    )


def downgrade() -> None:
    """Restore the post-NULLIF, single-clause form (e59f62d5037d)."""
    op.execute(
        "DROP POLICY user_role_assignments_tenant_isolation "
        "ON user_role_assignments"
    )
    op.execute(
        """
        CREATE POLICY user_role_assignments_tenant_isolation
        ON user_role_assignments
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
