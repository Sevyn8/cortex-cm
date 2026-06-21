"""TRUNCATE-all-at-once for the ``--reset`` flag.

Postgres rejects ``TRUNCATE foo`` when *any* other table has an FK
referencing ``foo`` — even when the referencing table happens to be
empty (PG checks the constraint's existence, not the row count).
The standard fix without CASCADE is to list all the dependent
tables in a single ``TRUNCATE`` statement; Postgres resolves the FK
dependency graph across the set internally.

Step 6.16.1 added `tenant_activity_audit_logs` and
`platform_activity_audit_logs`. Both FK to `tenants(id)`, so they
have to be co-listed for the TRUNCATE-without-CASCADE resolution
to succeed (Postgres validates the FK graph across the listed
tables as one operation, regardless of row counts). They ship
empty at 6.16.1; the TRUNCATE is a no-op for now, and remains
correct once emission starts at 6.16.2.
`lookups` is not in the list, it carries the migration-seeded
`module_code` rows that the seed loader expects to be present.

The "NO CASCADE" discipline mirrors Step 1.6 / 3.0's migration
pattern. A TRUNCATE that needs CASCADE is a sign of either wrong
ordering or wrong scope; a single multi-table TRUNCATE without
CASCADE is the project-shaped solution.

ROOS lookup cleanup (2026-05-12). The migration chain seeds ROOS at
``lookups(list_name='module_code', code='ROOS', display_order=1)``.
ROOS was retired from the Python ``ModuleCode`` vocabulary on
2026-05-12; with the narrowed ``ModuleCodeLiteral`` (5 values), a
``module_code='ROOS'`` row surfacing through ``/module-access/modules``
would crash Pydantic validation at the response boundary. The local
DELETE here mirrors the operator-run cloud cleanup SQL so local and
cloud stay aligned at 5 module_code rows (display_order 2-6). The
DB enum ``core.module_code_enum`` still carries ROOS; the future
rename migration handles that. Idempotent — DELETE matches zero
rows on subsequent runs.
"""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


# All seed-loader tables in one TRUNCATE statement. Postgres resolves
# the FK constraints across this set as a single operation.
#
# Step 6.8.1 split user_role_assignments into platform_user_role_assignments
# (no RLS) and tenant_user_role_assignments (RLS+FORCE, composite FKs).
# Both are leaf tables (no inbound FKs from other seed tables).
SEED_TABLES = [
    "tenant_activity_audit_logs",
    "platform_activity_audit_logs",
    "tenant_module_access",
    "platform_user_role_assignments",
    "tenant_user_role_assignments",
    "role_permissions",
    "permissions",
    "roles",
    "stores",
    "org_nodes",
    "tenant_users",
    "tenants",
    "platform_users",
]


async def truncate_seed_tables(session: AsyncSession) -> None:
    """TRUNCATE all seed tables in one statement. NO CASCADE. Then
    DELETE the retired ROOS lookups row (see module docstring).

    Shares the caller's transaction (the runner's
    ``get_tenant_session`` block); commit happens on clean exit.
    """
    await session.execute(
        text(
            "TRUNCATE "
            + ", ".join(SEED_TABLES)
            + " RESTART IDENTITY"
        )
    )
    await session.execute(
        text(
            "DELETE FROM lookups "
            "WHERE list_name = 'module_code' AND code = 'ROOS'"
        )
    )
