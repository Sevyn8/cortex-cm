# Prompt — Step 1.6: Wrap DDLs as Alembic migrations and verify reversibility

> Paste this entire block into a fresh Claude Code session when starting Step 1.6.
> Revised after stress test: extensions handled as precondition, migration file is self-contained (no runtime filesystem dependency), enum and function lists enumerated from DDLs at generation time.

---

## Pre-flight

Before doing any work:

1. Run `./scripts/check_setup.sh`. If any check fails, stop and report. Do not attempt to fix setup unless told.
2. Read `CLAUDE.md` fully. Pay particular attention to D-15 (schema parameterised via `DB_SCHEMA`), D-13 (audit-actor pattern), D-24 (JWT identity claims; relevant downstream, not this step), the "Current state" section, and the schema reference table.
3. Read `docs/architecture.md` "Schema and storage" section.
4. Read `BUILD_PLAN.md` Step 1.6 in full.
5. Read this prompt fully and confirm scope before writing code.

---

## Step ID and intent

**Step 1.6** — Wrap the 8 DDLs as a single Alembic initial migration; verify upgrade/downgrade reversibility.

The DDLs in `db/raw_ddl/` are the source of truth for the v0 schema, but they're not directly runnable through Alembic. This step produces an Alembic migration that, applied to an empty schema, recreates the same state Step 1.4 produced. Going forward, every schema change goes through Alembic, never by editing a DDL file.

Reversibility is the load-bearing acceptance criterion. A migration that can't downgrade cleanly is an operational liability.

This is a CLAUDE_CODE step. No application code, no auth, no ORM models. Pure schema migration mechanics.

---

## Required behaviour

### Schema parameterisation per D-15

- Migration files do NOT contain hardcoded schema names. The string `'core'` (or any specific schema name) does not appear in the migration file.
- `migrations/env.py` reads `DB_SCHEMA` from env at runtime, sets `search_path` on the migration's connection, and configures Alembic accordingly.

### Extensions are a precondition, not part of the migration

`CREATE EXTENSION` requires superuser privilege. The application role (`user_admin_backend` on local; corresponding cloud roles in dev/prod) is NOSUPERUSER NOBYPASSRLS. Therefore the migration must NOT attempt to install extensions.

Extensions (`ltree`, `pgcrypto`) are installed once per database during setup, by a privileged role:
- **Local:** installed during the schema setup procedure (see CLAUDE.md "Current state" — already done).
- **Cloud (dev/prod):** GCP-helper installs them when provisioning Cloud SQL, before the first migration runs. Document this as a precondition in the GCP provisioning runbook (Step 1.7.2 deliverable).

The migration assumes extensions exist. The migration's first step is a precondition check that surfaces a clear error if they don't.

### Migration file is self-contained

The migration file embeds DDL content as Python string literals at generation time. It does NOT read from `db/raw_ddl/` at migration runtime. This means:
- The migration file can be deployed in environments without `db/raw_ddl/` (e.g., a Docker image that only ships `migrations/`).
- Production deployments don't depend on `db/raw_ddl/` being on disk.
- The DDL files stay as source of truth for *generation*; the migration is the runtime artefact.

The DDL content embedded in the migration must have `CREATE EXTENSION` statements stripped (per the precondition rule). Other statements pass through unchanged.

### Operational gotchas (from Step 1.4)

1. **Bash subshells don't inherit env vars.** Each bash tool call needs `set -a && source .env && set +a` at the start.
2. **psql doesn't accept SQLAlchemy URL prefix.** Transform with `PSQL_URL="${DATABASE_URL/postgresql+psycopg/postgresql}"` if you need to call psql directly. Alembic itself uses SQLAlchemy and accepts the URL as-is.

---

## Scope in

### File 1: `migrations/env.py`

Update the existing `migrations/env.py` to:

1. Read `DB_SCHEMA` from env. Refuse to run if missing — raise `RuntimeError("DB_SCHEMA env var is required")`.
2. In `run_migrations_online()`, after acquiring the connection, run `SET search_path TO {db_schema}, public`.
3. In `context.configure(...)`, set:
   - `version_table_schema=db_schema` so `alembic_version` lives in the application schema (travels with any future schema rename).
   - `include_schemas=True` so Alembic recognises non-public schemas during introspection.
4. Set `target_metadata = None` for now. Add a comment: `# TODO: when ORM models exist (Step 3.1+), set to Base.metadata for autogenerate support.`
5. Read `DATABASE_URL` from env (already done — verify still works).

Reasoning for `target_metadata = None`: no ORM models exist yet. Setting it to `None` disables autogenerate diff comparison. This is correct for now; revisit at Step 3.1 when models land.

### File 2: Generate the initial migration

Use `alembic revision -m "initial schema"` (without `--autogenerate`) to create an empty migration scaffold. Then populate `upgrade()` and `downgrade()` by hand.

The migration file must be self-contained. The procedure to build it:

**Step 2a: Enumerate enums and functions from DDL files**

```bash
set -a && source .env && set +a

echo "=== ENUMS (CREATE TYPE ... AS ENUM) ==="
grep -rEh "^\s*CREATE TYPE\s+\S+\s+AS ENUM" db/raw_ddl/*.sql | \
  sed -E 's/^\s*CREATE TYPE\s+([a-z_]+).*/\1/' | sort -u

echo ""
echo "=== FUNCTIONS (CREATE FUNCTION / CREATE OR REPLACE FUNCTION) ==="
grep -rEh "^\s*CREATE\s+(OR REPLACE\s+)?FUNCTION\s+[a-z_]+" db/raw_ddl/*.sql | \
  sed -E 's/^\s*CREATE\s+(OR REPLACE\s+)?FUNCTION\s+([a-z_]+).*/\2/' | sort -u

echo ""
echo "=== EXTENSIONS (informational; NOT included in migration) ==="
grep -rEh "^\s*CREATE EXTENSION" db/raw_ddl/*.sql | sort -u
```

Run this and capture the output. The enum and function lists become the input to the migration's downgrade. The extension list is informational only — confirms what the precondition check needs to look for.

**Step 2b: Read DDL contents and strip extension statements**

For each DDL file, read its content, remove `CREATE EXTENSION ...;` lines (they're preconditions, not part of the migration). Embed the filtered content as a string constant in the migration file.

```bash
# Sketch — adapt as needed:
for ddl in db/raw_ddl/Ithina_postgres_SQL_DDL_*.sql; do
    name=$(basename "$ddl" .sql | sed 's/Ithina_postgres_SQL_DDL_/SQL_/')
    echo "--- $name ---"
    # Strip CREATE EXTENSION lines
    sed -E '/^\s*CREATE EXTENSION/d' "$ddl"
    echo ""
done
```

**Step 2c: Build the migration file**

The migration file structure:

```python
"""initial schema

Revision ID: <generated by alembic>
Revises:
Create Date: <generated>

Wraps the 8 DDL files in db/raw_ddl/ as a single migration.
DDL content embedded as string literals at generation time; migration is
self-contained and does not depend on db/raw_ddl/ at runtime.

Extensions (ltree, pgcrypto) are NOT installed by this migration. They are
preconditions installed by the database administrator before migrations run.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '<generated>'
down_revision = None
branch_labels = None
depends_on = None


# ============================================================================
# DDL content (embedded at generation time; CREATE EXTENSION stripped)
# ============================================================================

SQL_SHARED_UTILITIES = """\
<full content of shared_utilities_v1.sql with CREATE EXTENSION lines removed>
"""

SQL_LOOKUPS = """\
<full content of lookups_v1.sql>
"""

SQL_PLATFORM_USERS = """\
<full content of platform_users_v1.sql>
"""

SQL_TENANTS = """\
<full content of tenants_v3.sql>
"""

SQL_TENANT_USERS = """\
<full content of tenant_users_v1.sql>
"""

SQL_ORG_NODES = """\
<full content of org_nodes_v2.sql with CREATE EXTENSION lines removed>
"""

SQL_STORES = """\
<full content of stores_v5.sql>
"""

SQL_RBAC = """\
<full content of rbac_v2.sql>
"""

# Order matters: dependencies before dependents.
DDL_IN_ORDER = [
    SQL_SHARED_UTILITIES,
    SQL_LOOKUPS,
    SQL_PLATFORM_USERS,
    SQL_TENANTS,
    SQL_TENANT_USERS,
    SQL_ORG_NODES,
    SQL_STORES,
    SQL_RBAC,
]

# ============================================================================
# Tables to drop on downgrade (reverse dependency order)
# ============================================================================

TABLES_REVERSE_ORDER = [
    "user_role_assignments",
    "role_permissions",
    "permissions",
    "roles",
    "stores",
    "org_nodes",
    "tenant_users",
    "tenants",
    "platform_users",
    "lookups",
]

# ============================================================================
# Enums to drop on downgrade (enumerated from DDL files at generation time)
# ============================================================================

ENUMS_TO_DROP = [
    # Enumerated by the grep at Step 2a; populate from that output, not from memory.
    # Example shape:
    # "actor_user_type_enum",
    # "tenant_status_enum",
    # ...
]

# ============================================================================
# Functions to drop on downgrade (enumerated from DDL files at generation time)
# ============================================================================

FUNCTIONS_TO_DROP = [
    # Enumerated by the grep at Step 2a; populate from that output.
    # Example shape:
    # "set_updated_at_timestamp",
    # ...
]


def upgrade() -> None:
    # Precondition check: required extensions must exist.
    # Failing this check means the database wasn't set up correctly before migrations.
    op.execute("""
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
    """)

    # Apply DDLs in dependency order.
    # search_path is set by env.py before this runs, so unqualified table names
    # resolve to the configured schema.
    for sql in DDL_IN_ORDER:
        op.execute(sql)


def downgrade() -> None:
    # Reverse order: tables, then enums, then functions.
    # CASCADE handles any FK ordering issues; IF EXISTS makes the downgrade idempotent.
    # Extensions are NOT dropped — they may be shared with other schemas in the same DB.

    for table in TABLES_REVERSE_ORDER:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE;")

    for enum in ENUMS_TO_DROP:
        op.execute(f"DROP TYPE IF EXISTS {enum} CASCADE;")

    for func in FUNCTIONS_TO_DROP:
        op.execute(f"DROP FUNCTION IF EXISTS {func}() CASCADE;")
```

Substitute the actual SQL content into the `SQL_*` constants when building the file. The full migration file will be substantial (likely 2000-3000 lines because it embeds the DDL content). That's expected and correct — the migration is now self-contained.

### File 3: Verify clean upgrade against fresh schema

Reset the schema and let Alembic apply the migration from scratch.

```bash
set -a && source .env && set +a
PSQL_URL="${DATABASE_URL/postgresql+psycopg/postgresql}"

# Parse the application role from DATABASE_URL
APP_ROLE=$(echo "$DATABASE_URL" | sed -E 's|.*://([^:]+):.*|\1|')

# Drop the schema and recreate empty
psql "$PSQL_URL" -c "DROP SCHEMA IF EXISTS $DB_SCHEMA CASCADE;"
psql "$PSQL_URL" -c "CREATE SCHEMA $DB_SCHEMA;"
psql "$PSQL_URL" -c "GRANT USAGE, CREATE ON SCHEMA $DB_SCHEMA TO $APP_ROLE;"
psql "$PSQL_URL" -c "ALTER DEFAULT PRIVILEGES IN SCHEMA $DB_SCHEMA GRANT ALL ON TABLES TO $APP_ROLE;"
psql "$PSQL_URL" -c "ALTER DEFAULT PRIVILEGES IN SCHEMA $DB_SCHEMA GRANT ALL ON SEQUENCES TO $APP_ROLE;"
# Note: search_path was set role-wide during local rename; persists across schema recreate.
# Note: extensions were installed during local rename; still present in public.

# Apply the migration
uv run alembic upgrade head

# Verify: schema state matches Step 1.4
echo "=== Tables (expect 11: 10 application tables + alembic_version) ==="
psql "$PSQL_URL" -c "\dt $DB_SCHEMA.*"

echo "=== Enums (expect 18) ==="
psql "$PSQL_URL" -c "\dT $DB_SCHEMA.*"

echo "=== RLS+FORCE (expect 5 rows, all t/t) ==="
psql "$PSQL_URL" -c "
  SELECT n.nspname AS schema, c.relname AS table, c.relrowsecurity AS rls, c.relforcerowsecurity AS force
  FROM pg_class c
  JOIN pg_namespace n ON c.relnamespace = n.oid
  WHERE n.nspname = '$DB_SCHEMA' AND c.relrowsecurity = true
  ORDER BY c.relname;"

echo "=== Alembic current revision ==="
uv run alembic current
```

Expected: 10 application tables (+ `alembic_version`), 18 enums, 5/5 multi-tenant tables with RLS+FORCE, alembic_current shows the migration's revision id.

### File 4: Verify reversibility

```bash
set -a && source .env && set +a
PSQL_URL="${DATABASE_URL/postgresql+psycopg/postgresql}"

# Downgrade to base
uv run alembic downgrade base

echo "=== Tables in $DB_SCHEMA after downgrade (expect 0 application tables; alembic_version may persist or be empty) ==="
psql "$PSQL_URL" -c "
  SELECT count(*) AS table_count
  FROM pg_tables
  WHERE schemaname = '$DB_SCHEMA' AND tablename != 'alembic_version';"
# Expected: 0

echo "=== Enums in $DB_SCHEMA after downgrade (expect 0) ==="
psql "$PSQL_URL" -c "
  SELECT count(*) AS type_count
  FROM pg_type t
  JOIN pg_namespace n ON t.typnamespace = n.oid
  WHERE n.nspname = '$DB_SCHEMA' AND t.typtype = 'e';"
# Expected: 0

echo "=== Functions in $DB_SCHEMA after downgrade (expect 0) ==="
psql "$PSQL_URL" -c "
  SELECT count(*) AS func_count
  FROM pg_proc p
  JOIN pg_namespace n ON p.pronamespace = n.oid
  WHERE n.nspname = '$DB_SCHEMA';"
# Expected: 0

echo "=== Extensions in public (expect ltree, pgcrypto preserved) ==="
psql "$PSQL_URL" -c "SELECT extname FROM pg_extension WHERE extname IN ('ltree', 'pgcrypto');"
# Expected: 2 rows

# Re-upgrade
uv run alembic upgrade head

# Round-trip: downgrade and upgrade once more
uv run alembic downgrade base
uv run alembic upgrade head

# Final verification: same state as the first upgrade
psql "$PSQL_URL" -c "\dt $DB_SCHEMA.*"
psql "$PSQL_URL" -c "
  SELECT count(*) AS table_count
  FROM pg_tables
  WHERE schemaname = '$DB_SCHEMA';"
# Expected: 11 (10 + alembic_version)
```

If any verification fails, the migration is not reversible. Stop and surface.

---

## Scope out

- ORM models (Step 3.1+ territory).
- Application code (none yet).
- Tests against the schema (Step 1.5 already covers schema invariants).
- Multi-revision migrations. Only one migration file: the initial wrap.
- Cloud-side migration runs (Step 4.1).
- Extension installation. Extensions are a setup precondition, not a migration concern.

---

## Stop and ask if

- The migration upgrade fails because extensions aren't found. The precondition check is doing its job — surface the failure cleanly with the error message we wrote into the precondition. Don't add `CREATE EXTENSION` to the migration as a workaround; that's the wrong fix.
- The downgrade fails on a constraint or FK that CASCADE doesn't resolve. Surface the specific error; we'll decide whether to add explicit DROP CONSTRAINT calls.
- Setting `version_table_schema=DB_SCHEMA` causes Alembic to report the existing `alembic_version` table (in `public` from a prior run) as a conflict. Drop `public.alembic_version` if it exists before retrying — it was a leftover from the pre-namespacing setup.
- The enum or function enumeration from DDLs (Step 2a grep) returns counts that don't match Step 1.4's verification (18 enums). Investigate the discrepancy before proceeding.
- An extension other than `ltree` or `pgcrypto` is found in the DDL files. The precondition check needs to include it. Add it to the check; don't silently skip.
- Round-trip verification (downgrade → upgrade → downgrade → upgrade) produces inconsistent state between the two upgrades. The migration is not idempotent in some way; investigate.

---

## Acceptance criteria

- `migrations/env.py` reads `DATABASE_URL` and `DB_SCHEMA` from env. Refuses to run if either is missing.
- `migrations/env.py` sets `search_path` per migration run, sets `version_table_schema=DB_SCHEMA`, sets `include_schemas=True`, sets `target_metadata = None` with a TODO comment.
- `migrations/versions/<timestamp>_initial_schema.py` exists. Migration is self-contained: DDL content embedded as Python string literals at generation time. The string `'core'` (or any specific schema name) does not appear in the migration file.
- The migration's `upgrade()` includes a precondition check that fails clearly if `ltree` or `pgcrypto` extensions are missing.
- The migration's `upgrade()` does NOT contain `CREATE EXTENSION` statements.
- The migration's `downgrade()` drops 10 tables (reverse dependency order), 18 enums, all functions enumerated from DDLs, and does NOT drop extensions.
- Enum and function lists in the migration are enumerated from DDLs at generation time (verifiable by re-running the grep and matching the lists).
- Fresh-schema upgrade produces 10 application tables + `alembic_version`, 18 enums, 5/5 multi-tenant tables with RLS+FORCE — matching Step 1.4 verification.
- Downgrade to base leaves the schema with 0 application tables, 0 enums, 0 functions. Extensions in `public` are preserved.
- Round-trip (downgrade → upgrade) produces identical state to the first upgrade. No errors.
- `./scripts/check_setup.sh` passes after the work is done.
- `alembic current` returns the head revision id when the schema is at head.

---

## What to report at end

- Files created/modified: `migrations/env.py` (line counts before/after), `migrations/versions/<timestamp>_initial_schema.py` (line count, expected ~2000-3000 lines).
- Output of the enum/function/extension enumeration (Step 2a). The actual lists discovered.
- Output of `alembic upgrade head` against the fresh schema (or relevant excerpts).
- Output of post-upgrade verification queries.
- Output of `alembic downgrade base`.
- Output of post-downgrade verification queries.
- Output of round-trip verification.
- `alembic current` output before and after the migration.
- Any deviations from this prompt's procedure and why.
- Any extensions found in DDLs other than `ltree` and `pgcrypto`.

---

## After completing

Propose a git commit per CLAUDE.md "After completing a task" Pattern A:

```
git status
git add -A
git commit -m "Step 1.6: wrap DDLs as Alembic initial migration; verify reversibility

- migrations/env.py: read DB_SCHEMA from env; SET search_path per migration run; version_table_schema=DB_SCHEMA; include_schemas=True; target_metadata=None (TODO Step 3.1)
- migrations/versions/<timestamp>_initial_schema.py: self-contained migration (DDL content embedded at generation time, no runtime filesystem dependency); upgrade applies 8 DDLs in dependency order; downgrade drops 10 tables, N enums, M functions in reverse order; CREATE EXTENSION stripped (extensions are a setup precondition); precondition check at upgrade start surfaces missing extensions clearly
- Migration is schema-agnostic (no hardcoded 'core' literal)
- Verified: fresh-schema upgrade produces 10 tables + 18 enums + 5/5 RLS+FORCE matching Step 1.4 state; downgrade to base leaves schema empty (extensions preserved in public); round-trip clean
- BUILD_PLAN.md Step 1.6 status TODO -> DONE"
```

(Substitute actual N for enum count and M for function count in the message.)

Ask user "Run? yes / no / edit message".

---

## End of prompt
