"""amend rls policies use nullif for empty-string guc

Revision ID: e59f62d5037d
Revises: ad8afd429581
Create Date: 2026-05-02 00:01:46.544702

Discovered during Step 2.2a build that Postgres "registers" a
placeholder GUC (e.g. `app.tenant_id`) at session level the first
time `set_config(name, value, true)` runs on a connection. After
the transaction commits, `current_setting('app.tenant_id', TRUE)`
no longer returns NULL on the same connection; it returns the
empty string `''`. Casting `''::uuid` raises
`invalid input syntax for type uuid: ""`, so the original RLS
policies (`tenant_id = current_setting('app.tenant_id', TRUE)::uuid`)
crash on every reused pooled connection past its first transaction.

The fix is to wrap `current_setting(...)` in
`NULLIF(..., '')`. The cast then sees NULL on both pristine
connections and post-commit reused connections, and `tenant_id =
NULL` evaluates to unknown (false in WHERE), so default-deny is
preserved on top of the existing FORCE ROW LEVEL SECURITY.

Touches all 5 multi-tenant tables: tenants (uses id, not
tenant_id), tenant_users, org_nodes, stores, user_role_assignments.

The FN-AB-14 fix (PLATFORM-audience permissive OR-clause on
user_role_assignments) lands separately at Step 2.2b. This
migration uses the v1 single-clause policy shape for that table
too; 2.2b will drop and recreate with the OR-clause on top of the
NULLIF base.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'e59f62d5037d'
down_revision: Union[str, Sequence[str], None] = 'ad8afd429581'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (table_name, policy_name, column_expression)
# `column_expression` is the row-side expression compared against
# the GUC. Most tables use `tenant_id`; `tenants` itself uses `id`.
_POLICIES = [
    ("tenants", "tenants_self_access", "id"),
    ("tenant_users", "tenant_users_tenant_isolation", "tenant_id"),
    ("org_nodes", "org_nodes_tenant_isolation", "tenant_id"),
    ("stores", "stores_tenant_isolation", "tenant_id"),
    (
        "user_role_assignments",
        "user_role_assignments_tenant_isolation",
        "tenant_id",
    ),
]


def upgrade() -> None:
    """Drop and recreate each policy with NULLIF wrapping the GUC read."""
    for table, policy, col in _POLICIES:
        op.execute(f"DROP POLICY {policy} ON {table}")
        op.execute(
            f"""
            CREATE POLICY {policy} ON {table}
              USING (
                {col} = NULLIF(
                    current_setting('app.tenant_id', TRUE), ''
                )::uuid
              )
              WITH CHECK (
                {col} = NULLIF(
                    current_setting('app.tenant_id', TRUE), ''
                )::uuid
              )
            """
        )


def downgrade() -> None:
    """Restore the v1 (no-NULLIF) policies."""
    for table, policy, col in _POLICIES:
        op.execute(f"DROP POLICY {policy} ON {table}")
        op.execute(
            f"""
            CREATE POLICY {policy} ON {table}
              USING (
                {col} = current_setting('app.tenant_id', TRUE)::uuid
              )
              WITH CHECK (
                {col} = current_setting('app.tenant_id', TRUE)::uuid
              )
            """
        )
