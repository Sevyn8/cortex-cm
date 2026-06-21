"""Generator for migrations/versions/<rev>_initial_schema.py.

Reads each DDL in db/raw_ddl/, strips CREATE EXTENSION ... ; lines,
embeds the content as Python raw triple-quoted string literals in the
target migration file. Replaces the alembic-generated scaffold body.

The output migration is self-contained: at runtime it does not read
from db/raw_ddl/.

Usage:
    1. Generate the scaffold:
       uv run alembic revision -m "initial schema"
    2. Run this script to replace the scaffold body with embedded DDL
       content plus upgrade()/downgrade() functions:
       uv run python scripts/build_initial_migration.py

Re-run any time the DDLs in db/raw_ddl/ change AND the change is part
of the initial wrap. After the initial migration ships to production,
schema changes go through new ALTER-style migrations, not by
regenerating this one.
"""
import re
import os
import sys

# Derive paths from this script's location so the generator works
# regardless of CWD and is portable across machines.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
DDL_DIR = os.path.join(PROJECT_ROOT, "db/raw_ddl")
VERSIONS_DIR = os.path.join(PROJECT_ROOT, "migrations/versions")

# Dependency order. Each entry: (var_name, filename, ddl_label_for_logging).
DDLS = [
    ("SQL_SHARED_UTILITIES", "Ithina_postgres_SQL_DDL_shared_utilities_v1.sql", "shared_utilities"),
    ("SQL_LOOKUPS",          "Ithina_postgres_SQL_DDL_lookups_v1.sql",          "lookups"),
    ("SQL_PLATFORM_USERS",   "Ithina_postgres_SQL_DDL_platform_users_v1.sql",   "platform_users"),
    ("SQL_TENANTS",          "Ithina_postgres_SQL_DDL_tenants_v3.sql",          "tenants"),
    ("SQL_TENANT_USERS",     "Ithina_postgres_SQL_DDL_tenant_users_v1.sql",     "tenant_users"),
    ("SQL_ORG_NODES",        "Ithina_postgres_SQL_DDL_org_nodes_v2.sql",        "org_nodes"),
    ("SQL_STORES",           "Ithina_postgres_SQL_DDL_stores_v5.sql",           "stores"),
    ("SQL_RBAC",             "Ithina_postgres_SQL_DDL_rbac_v2.sql",             "rbac"),
]

# Tables to drop on downgrade, reverse dependency order.
# rbac creates 4 tables in order: permissions, roles, role_permissions, user_role_assignments
# user_role_assignments depends on roles + tenants + tenant_users + platform_users + org_nodes
# role_permissions depends on roles + permissions
# stores depends on tenants + org_nodes
# org_nodes depends on tenants
# tenant_users depends on tenants
# tenants depends on platform_users
# platform_users self-references (via audit FKs)
# lookups stands alone
TABLES_REVERSE_ORDER = [
    "user_role_assignments",
    "role_permissions",
    "roles",
    "permissions",
    "stores",
    "org_nodes",
    "tenant_users",
    "tenants",
    "platform_users",
    "lookups",
]

# Enumerated by grep at generation time (Step 2a in the prompt).
ENUMS_TO_DROP = [
    "action_enum",
    "actor_user_type_enum",
    "module_enum",
    "org_node_status_enum",
    "org_node_type_enum",
    "permission_scope_enum",
    "platform_user_status_enum",
    "resource_enum",
    "role_audience_enum",
    "role_status_enum",
    "store_status_enum",
    "tax_treatment_enum",
    "tenant_industry_enum",
    "tenant_region_enum",
    "tenant_status_enum",
    "tenant_tier_enum",
    "tenant_user_status_enum",
    "user_role_assignment_status_enum",
]

# Enumerated by grep at generation time.
FUNCTIONS_TO_DROP = [
    "set_updated_at_timestamp",
    "uuidv7",
]


def strip_create_extension(content: str) -> str:
    """Remove every CREATE EXTENSION ... ; statement (including IF NOT EXISTS).

    Preserves the trailing newline structure of surrounding lines.
    """
    return re.sub(
        r"^[ \t]*CREATE\s+EXTENSION[^;]*;[ \t]*\r?\n",
        "",
        content,
        flags=re.MULTILINE | re.IGNORECASE,
    )


def find_scaffold():
    files = [
        f for f in os.listdir(VERSIONS_DIR)
        if f.endswith("_initial_schema.py")
    ]
    if len(files) != 1:
        raise SystemExit(
            f"expected exactly 1 scaffold, found {len(files)}: {files}"
        )
    return os.path.join(VERSIONS_DIR, files[0])


def parse_scaffold_metadata(scaffold_text: str):
    """Pull revision id and Create Date from the scaffold's docstring."""
    rev_match = re.search(
        r'^revision: str = ["\']([^"\']+)["\']',
        scaffold_text,
        re.MULTILINE,
    )
    if not rev_match:
        raise SystemExit("could not parse revision from scaffold")
    rev = rev_match.group(1)

    cd_match = re.search(r"Create Date: ([^\n]+)", scaffold_text)
    create_date = cd_match.group(1).strip() if cd_match else "unknown"

    return rev, create_date


def main():
    scaffold_path = find_scaffold()
    with open(scaffold_path) as f:
        scaffold_text = f.read()

    revision_id, create_date = parse_scaffold_metadata(scaffold_text)

    # Read each DDL, strip CREATE EXTENSION lines.
    ddl_payloads = []
    for var_name, filename, label in DDLS:
        ddl_path = os.path.join(DDL_DIR, filename)
        with open(ddl_path) as f:
            raw = f.read()
        stripped = strip_create_extension(raw)
        # Defensive sanity: triple-double-quote in DDL would break embedding.
        if '"""' in stripped:
            raise SystemExit(
                f"{filename} contains \"\"\" — would break Python triple-quoted "
                "embedding. Refusing to generate."
            )
        # Defensive: check that no CREATE EXTENSION lines slipped through.
        for ln in stripped.splitlines():
            if re.match(r"^\s*CREATE\s+EXTENSION", ln, re.IGNORECASE):
                raise SystemExit(
                    f"{filename}: CREATE EXTENSION line not stripped: {ln!r}"
                )
        ddl_payloads.append((var_name, label, stripped))

    # Build the migration file.
    parts = []

    parts.append(f'''"""initial schema

Revision ID: {revision_id}
Revises:
Create Date: {create_date}

Wraps the 8 DDL files in db/raw_ddl/ as a single Alembic migration.
DDL content is embedded as Python string literals at generation time;
the migration is self-contained and does not depend on db/raw_ddl/ at
runtime. Production deployments can ship migrations/ without the DDL
source on disk.

The DDL files in db/raw_ddl/ remain the source of truth for schema
GENERATION. Once a DDL change is needed, regenerate the migration (or
write a new ALTER-style migration) rather than editing this file.

Extensions (ltree, pgcrypto) are NOT installed by this migration.
CREATE EXTENSION requires superuser privilege; the application role is
NOSUPERUSER NOBYPASSRLS by design (see CLAUDE.md "Current state").
Extensions are a database-setup precondition, installed once by a
privileged role before migrations run. The upgrade() begins with a
precondition check that surfaces a clear error if either extension is
missing.

Schema name is parameterised via DB_SCHEMA env var (D-15). Tables
resolve to the configured schema via search_path, set by env.py before
this migration runs. The migration itself contains no hardcoded schema
literal.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "{revision_id}"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ============================================================================
# DDL content
# ============================================================================
#
# Each constant below holds the content of one DDL file from db/raw_ddl/,
# embedded at generation time. CREATE EXTENSION statements have been
# stripped (extensions are a setup precondition; see header docstring).
# Other statements pass through unchanged.
''')

    for var_name, label, content in ddl_payloads:
        # Raw triple-quoted strings (r"""...""") so that SQL backslash-escapes
        # like the regex `\.` in rbac_v2.sql's ck_permissions_code_format
        # CHECK constraint pass through literally without triggering Python's
        # SyntaxWarning for unrecognised escape sequences (deprecated in 3.12,
        # error in 3.13+). The opening triple-quote is followed by a newline
        # so the embedded SQL begins with a blank line; harmless for SQL
        # execution and keeps the source layout readable.
        parts.append(f'\n{var_name} = r"""\n')
        parts.append(content)
        parts.append('"""\n')

    parts.append("\n\n# Order matters: dependencies before dependents.\n")
    parts.append("DDL_IN_ORDER = [\n")
    for var_name, _, _ in ddl_payloads:
        parts.append(f"    {var_name},\n")
    parts.append("]\n")

    parts.append("\n\n# ============================================================================\n")
    parts.append("# Tables to drop on downgrade (reverse dependency order).\n")
    parts.append("# ============================================================================\n\n")
    parts.append("TABLES_REVERSE_ORDER = [\n")
    for table in TABLES_REVERSE_ORDER:
        parts.append(f'    "{table}",\n')
    parts.append("]\n")

    parts.append("\n\n# ============================================================================\n")
    parts.append("# Enums to drop on downgrade. Enumerated from DDL CREATE TYPE statements\n")
    parts.append("# at generation time; see Step 1.6 prompt section 2a for the grep.\n")
    parts.append("# ============================================================================\n\n")
    parts.append("ENUMS_TO_DROP = [\n")
    for enum in ENUMS_TO_DROP:
        parts.append(f'    "{enum}",\n')
    parts.append("]\n")

    parts.append("\n\n# ============================================================================\n")
    parts.append("# Functions to drop on downgrade. Enumerated from DDL CREATE FUNCTION\n")
    parts.append("# statements at generation time; case-insensitive grep catches both the\n")
    parts.append("# uppercase project style and the lowercase kjmph-vendored style.\n")
    parts.append("# ============================================================================\n\n")
    parts.append("FUNCTIONS_TO_DROP = [\n")
    for func in FUNCTIONS_TO_DROP:
        parts.append(f'    "{func}",\n')
    parts.append("]\n")

    parts.append('''

# ============================================================================
# Precondition check: required extensions must be present
# ============================================================================
#
# CREATE EXTENSION requires superuser privilege; the application role
# (per Step 1.5) is NOSUPERUSER NOBYPASSRLS. Extensions must be
# installed during database setup by a privileged role, not here.
#
# This check runs first in upgrade() and aborts the migration with a
# clear error if either expected extension is missing. Failing this
# means the database wasn't set up correctly before migrations were
# applied; the fix is in the setup procedure, not in the migration.

PRECONDITION_CHECK_SQL = """
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'ltree') THEN
        RAISE EXCEPTION 'ltree extension is required but not installed. '
            'Install via: CREATE EXTENSION ltree; (requires superuser)';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pgcrypto') THEN
        RAISE EXCEPTION 'pgcrypto extension is required but not installed. '
            'Install via: CREATE EXTENSION pgcrypto; (requires superuser)';
    END IF;
END
$$;
"""


def upgrade() -> None:
    """Apply the initial schema.

    Runs the extension-precondition check first; if it raises, the
    migration aborts before any DDL is applied. Then applies each DDL
    in dependency order. search_path is set by env.py before this
    function runs, so unqualified table names resolve to the configured
    schema (DB_SCHEMA env var).
    """
    op.execute(PRECONDITION_CHECK_SQL)
    for sql in DDL_IN_ORDER:
        op.execute(sql)


def downgrade() -> None:
    """Reverse the upgrade.

    Drops tables (reverse dependency order), then enums, then functions.
    CASCADE handles any residual FK or trigger dependencies; IF EXISTS
    keeps the downgrade idempotent. Extensions are NOT dropped: they may
    be shared with other schemas in the same database, and dropping
    them is the database administrator's call, not this migration's.
    """
    for table in TABLES_REVERSE_ORDER:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE;")

    for enum in ENUMS_TO_DROP:
        op.execute(f"DROP TYPE IF EXISTS {enum} CASCADE;")

    for func in FUNCTIONS_TO_DROP:
        op.execute(f"DROP FUNCTION IF EXISTS {func}() CASCADE;")
''')

    output = "".join(parts)

    # Defensive: ensure no hardcoded schema literal slipped in. The migration
    # must be schema-agnostic. We check for the local schema name "core" as a
    # bare word (not part of a longer identifier).
    if re.search(r"\bcore\b", output):
        raise SystemExit(
            'Generated migration contains the literal "core" — that should '
            "not appear; the schema is parameterised. Investigate."
        )

    with open(scaffold_path, "w") as f:
        f.write(output)

    print(f"Wrote: {scaffold_path}")
    print(f"Lines: {output.count(chr(10))}")
    print(f"Bytes: {len(output)}")


if __name__ == "__main__":
    main()
