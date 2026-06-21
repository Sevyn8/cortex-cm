# Prompt — Step 6.3: Seeds — bootstrap, lookups, RBAC static

> Paste this entire block into a fresh Claude Code session when starting Step 6.3.

---

## Pre-flight

Before doing any work:

1. Run `./scripts/check_setup.sh`. If any check fails, stop and report.
2. Read `CLAUDE.md` fully — focus on schema reference and "Repository structure" tree (db/seeds layout).
3. Read `BUILD_PLAN.md` Step 6.3 in full.
4. Read these DDL files for column references:
   - `db/raw_ddl/Ithina_postgres_SQL_DDL_platform_users_v1.sql` (for the bootstrap user shape)
   - `db/raw_ddl/Ithina_postgres_SQL_DDL_lookups_v1.sql` (for lookup categories and shape)
   - `db/raw_ddl/Ithina_postgres_SQL_DDL_rbac_v2.sql` (for roles, permissions, role_permissions, user_role_assignments shapes)
5. Read this prompt fully and confirm scope.

---

## Step ID and intent

**Step 6.3** — Seeds: bootstrap, lookups, RBAC static.

Foundational seed data needed before any tenant onboarding. Three SQL files plus a runner script. Files committed to git so they reproduce identically across environments.

This is a CLAUDE_CODE step. Pure SQL + a small bash runner. No app integration; runs after `alembic upgrade head` and before any tenant data load.

---

## Scope in

### File 1: `db/seeds/00_bootstrap.sql`

Creates the foundational platform user that becomes the audit actor for everything else. Self-referencing audit FKs.

```sql
-- 00_bootstrap.sql
-- Creates the Ithina System platform user. This user is the audit actor for
-- all subsequent seed inserts (lookups, roles, etc.) and for any data
-- inserted before a real platform_user exists.
--
-- Idempotent: ON CONFLICT (id) DO NOTHING.
--
-- WARNING: do NOT modify the bootstrap UUID. It's referenced from other
-- seed files and from the build plan documentation.

INSERT INTO platform_users (
    id,
    name,
    email,
    status,
    created_at,
    created_by_user_id,
    created_by_user_type,
    updated_at,
    updated_by_user_id,
    updated_by_user_type
)
VALUES (
    '00000000-0000-0000-0000-000000000001',
    'Ithina System',
    'system@ithina.com',
    'ACTIVE',
    NOW(),
    '00000000-0000-0000-0000-000000000001',
    'PLATFORM',
    NOW(),
    '00000000-0000-0000-0000-000000000001',
    'PLATFORM'
)
ON CONFLICT (id) DO NOTHING;
```

Verify column list against `platform_users_v1.sql`. If actual columns differ (e.g., extra fields like `phone`, `picture_url`), include them with sensible defaults or NULL.

### File 2: `db/seeds/01_lookups.sql`

Populates the `lookups` table with all platform-global enum values used by the API and frontend.

Categories to seed (verify exact `list_name` values against your code; these are typical):

- `tier` — Commercial tiers
- `industry` — Industry codes
- `region` — Geographic regions
- `tenant_status` — Tenant lifecycle statuses
- `store_status` — Store lifecycle statuses
- `tenant_user_status` — Tenant user lifecycle statuses
- `platform_user_status` — Platform user lifecycle statuses
- `audit_result` — Audit log result codes (SUCCESS / PENDING / DENIED)
- `audit_scope` — Audit log scope (TENANT / GLOBAL)
- `org_node_type` — Org tree node types (REGION / DISTRICT / STORE)

For each category, populate codes and display names. Sample shape:

```sql
-- 01_lookups.sql
-- Platform-global lookup data: enum values used by frontend and API.
-- Idempotent: ON CONFLICT (list_name, code) DO NOTHING.

-- ============================================================================
-- tier
-- ============================================================================
INSERT INTO lookups (list_name, code, display_name, display_order, is_active,
                     created_at, created_by_user_id, created_by_user_type,
                     updated_at, updated_by_user_id, updated_by_user_type)
VALUES
    ('tier', 'ENTERPRISE', 'Enterprise', 1, true, NOW(),
     '00000000-0000-0000-0000-000000000001', 'PLATFORM', NOW(),
     '00000000-0000-0000-0000-000000000001', 'PLATFORM'),
    ('tier', 'MID_MARKET', 'Mid-Market', 2, true, NOW(),
     '00000000-0000-0000-0000-000000000001', 'PLATFORM', NOW(),
     '00000000-0000-0000-0000-000000000001', 'PLATFORM'),
    ('tier', 'SMB', 'Small Business', 3, true, NOW(),
     '00000000-0000-0000-0000-000000000001', 'PLATFORM', NOW(),
     '00000000-0000-0000-0000-000000000001', 'PLATFORM'),
    ('tier', 'SINGLE_STORE', 'Single Store', 4, true, NOW(),
     '00000000-0000-0000-0000-000000000001', 'PLATFORM', NOW(),
     '00000000-0000-0000-0000-000000000001', 'PLATFORM')
ON CONFLICT (list_name, code) DO NOTHING;

-- ============================================================================
-- industry
-- ============================================================================
-- Common industry codes
INSERT INTO lookups (list_name, code, display_name, display_order, is_active,
                     created_at, created_by_user_id, created_by_user_type,
                     updated_at, updated_by_user_id, updated_by_user_type)
VALUES
    ('industry', 'GROCERY', 'Grocery', 1, true, NOW(), ..., NOW(), ...),
    ('industry', 'CONVENIENCE', 'Convenience Store', 2, true, NOW(), ..., NOW(), ...),
    ('industry', 'PHARMACY', 'Pharmacy', 3, true, NOW(), ..., NOW(), ...),
    ('industry', 'FUEL', 'Fuel / C-Store', 4, true, NOW(), ..., NOW(), ...),
    ('industry', 'GENERAL_RETAIL', 'General Retail', 5, true, NOW(), ..., NOW(), ...)
ON CONFLICT (list_name, code) DO NOTHING;

-- (continue for all 10 categories)
```

Use a CTE or the `'00000000-0000-0000-0000-000000000001'` UUID directly. The actor is always the bootstrap user.

To reduce verbosity, consider using a temp variable approach in psql or just write it out — clarity over brevity for seeds.

### File 3: `db/seeds/02_rbac_static.sql`

Platform-defined roles, permissions, role-permission mappings, and minimal demo user-role assignments.

Roles (5):

- `SUPER_ADMIN` — Ithina staff, full access.
- `SUPPORT_ADMIN` — Ithina staff, support-level read access across tenants.
- `OWNER` — Tenant-side owner, full access within their tenant.
- `MANAGER` — Tenant-side manager, broad access within their tenant.
- `STORE_MANAGER` — Tenant-side, scoped to specific store(s).

Permissions (~12-15): use Module + Resource + Action + Scope shape. Verify the actual schema against `rbac_v2.sql`. Sample:

```
("ADMIN_BACKEND", "tenants", "READ", "GLOBAL")
("ADMIN_BACKEND", "tenants", "READ", "OWN_TENANT")
("ADMIN_BACKEND", "stores", "READ", "OWN_TENANT")
("ADMIN_BACKEND", "stores", "READ", "ASSIGNED_STORES")
("ADMIN_BACKEND", "users", "READ", "OWN_TENANT")
("ADMIN_BACKEND", "audit_logs", "READ", "OWN_TENANT")
("ADMIN_BACKEND", "audit_logs", "READ", "GLOBAL")
("ADMIN_BACKEND", "rbac", "READ", "OWN_TENANT")
... and so on
```

Role-permission mappings:

- `SUPER_ADMIN` → all GLOBAL permissions.
- `SUPPORT_ADMIN` → READ permissions GLOBAL on tenants, audit_logs.
- `OWNER` → all OWN_TENANT permissions.
- `MANAGER` → READ permissions OWN_TENANT.
- `STORE_MANAGER` → READ permissions ASSIGNED_STORES.

User-role assignments: minimal for v0 (1-3 demo rows). Examples:

```
(bootstrap_user_id, SUPER_ADMIN_role_id, NULL_org_node, ACTIVE)
```

Frontend renders the assignment screens; full enforcement is post-v0. Don't over-seed here.

Use deterministic UUIDs for roles and permissions so seeds are reproducible:

```
SUPER_ADMIN role: 'aaaaaaaa-0000-0000-0000-000000000001'
SUPPORT_ADMIN role: 'aaaaaaaa-0000-0000-0000-000000000002'
... and so on
```

Document the deterministic UUID convention in the file header.

Idempotent: `ON CONFLICT (id) DO NOTHING`.

### File 4: `scripts/apply_seeds.sh`

Runner that applies all seed files in order against the configured DATABASE_URL.

```bash
#!/bin/bash
# apply_seeds.sh
# Apply all seed SQL files in dependency order against DATABASE_URL.
# Idempotent: each seed file uses ON CONFLICT DO NOTHING.
#
# Usage:
#   ./scripts/apply_seeds.sh
#
# Requires:
#   - DATABASE_URL env var set
#   - psql on PATH
#   - alembic upgrade head already run (schema must exist)

set -euo pipefail

if [[ -z "${DATABASE_URL:-}" ]]; then
    echo "ERROR: DATABASE_URL is not set"
    exit 1
fi

# psql doesn't accept SQLAlchemy-style URL prefix; strip "+psycopg".
PSQL_URL="${DATABASE_URL/postgresql+psycopg/postgresql}"

SEED_DIR="$(cd "$(dirname "$0")/.." && pwd)/db/seeds"

echo "Applying seeds from $SEED_DIR..."

for seed_file in "$SEED_DIR"/00_bootstrap.sql \
                 "$SEED_DIR"/01_lookups.sql \
                 "$SEED_DIR"/02_rbac_static.sql; do
    if [[ ! -f "$seed_file" ]]; then
        echo "  WARN: $seed_file not found; skipping"
        continue
    fi
    echo "  Applying $(basename "$seed_file")..."
    psql "$PSQL_URL" -v ON_ERROR_STOP=1 -f "$seed_file"
done

echo ""
echo "Seeds applied successfully."
echo ""
echo "Verify with:"
echo "  psql \"\$DATABASE_URL\" -c 'SELECT list_name, COUNT(*) FROM lookups GROUP BY list_name ORDER BY list_name;'"
echo "  psql \"\$DATABASE_URL\" -c 'SELECT name FROM platform_users;'"
echo "  psql \"\$DATABASE_URL\" -c 'SELECT code FROM roles;'"
```

`chmod +x` it.

### File 5: `db/seeds/README.md`

Brief explanation:

```markdown
# db/seeds — Foundational seed data

Seeds applied after `alembic upgrade head`. Apply with:

    ./scripts/apply_seeds.sh

## Files

- `00_bootstrap.sql` — Ithina System platform user (audit actor).
- `01_lookups.sql` — All lookup categories: tier, industry, region, statuses, audit_result, etc.
- `02_rbac_static.sql` — Platform roles, permissions, role-permission mappings, minimal demo assignments.

Idempotent: re-running with the same files is safe.

## Customer/tenant data

NOT in this directory. Customer data is loaded via `scripts/onboarding/` per Step 7.3.x.

## Adding a new lookup category or role

1. Edit the relevant seed file.
2. Re-run `./scripts/apply_seeds.sh`.
3. Existing rows untouched (ON CONFLICT DO NOTHING).
4. Commit the change.
```

### File 6: Tests

Light verification, not full test coverage. Add to `tests/integration/test_seeds.py`:

- Run seeds against test DB.
- Verify bootstrap user exists with expected UUID.
- Verify each lookup category has expected codes.
- Verify each role exists.
- Verify role-permissions are mapped (at least non-zero count).
- Re-run seeds; verify no duplicates (idempotency).

Use a session-scoped fixture or inline psql call from the test. Match existing test patterns.

---

## Scope out

- Customer/tenant seed data (Step 7.3.x).
- Audit log seeds (Step 6.2 created the table; sample data added if needed for screen demo).
- Onboarding scripts (Step 7.3.1).
- Auth0 user creation.

---

## Implementation hints

### Verify against actual DDL

Column names and types may differ from this prompt's assumptions. The DDL is the source of truth. If `lookups_v1.sql` uses `category` instead of `list_name`, adapt accordingly.

### Lookups data — keep practical

Don't enumerate every conceivable code. Focus on what the frontend renders today and what the build plan references. Easy to add more later.

### Role names

CLAUDE.md mentions the 5 roles but doesn't lock the exact `code` values. Use UPPER_SNAKE_CASE per convention (`SUPER_ADMIN`, not `Super Admin`).

### Permissions — keep flat

Resist building deep permission hierarchies for v0. Each permission is one row. Cascade logic happens at query time (org tree ltree, not in the permissions table itself).

### Demo user-role assignments — minimal

Don't seed 50 assignments to make the screens look populated. Seed 2-3, mark them as demo. Real assignments come with real customer data.

### NUMERIC and dates in seeds

Always use `NOW()` for `created_at` / `updated_at` rather than hardcoding timestamps. Cleaner.

For UUIDs, use the deterministic patterns documented in file headers.

### ON CONFLICT clauses

Match the conflict target to the table's UNIQUE constraint:

- `platform_users` → `ON CONFLICT (id) DO NOTHING`
- `lookups` → `ON CONFLICT (list_name, code) DO NOTHING`
- `roles` → `ON CONFLICT (code) DO NOTHING` (if `code` is unique)
- `permissions` → adapt
- `role_permissions` → `ON CONFLICT (role_id, permission_id) DO NOTHING`

---

## Acceptance criteria

- All 6 files created (3 SQL + 1 script + README + tests).
- `./scripts/apply_seeds.sh` runs cleanly after `alembic upgrade head`.
- Re-running the script is idempotent (no errors, no duplicates).
- Endpoints return seeded data:
  - `GET /v1/lookups/tier` (or whatever the lookups endpoint is) returns the 4 tier codes.
  - `GET /v1/roles` returns the 5 platform roles.
- Tests pass.
- mypy / lint clean (for the test file; SQL files don't need lint).

---

## Stop and ask if

- Lookup categories or codes are unclear; check with user before guessing.
- Role names don't match what frontend expects (capture in `docs/api-contract.md` if you need to lock).
- The `lookups` schema column names differ from the prompt's assumptions.
- The `rbac_v2.sql` schema doesn't have separate `permissions` and `role_permissions` tables (some implementations consolidate).

---

## What to report at end

- Files created (line counts).
- Seed run output (which files applied, any warnings).
- Counts of seeded rows per category (e.g., "tier: 4 rows, industry: 5 rows").
- Verification of idempotency (re-ran twice, no duplicates).
- Any decisions made on your own (e.g., specific industry codes chosen).

---

## After completing

Propose a git commit per CLAUDE.md "After completing a task" Pattern A:

```
git status
git add -A
git commit -m "Step 6.3: Seeds — bootstrap, lookups, RBAC static

- 00_bootstrap.sql: Ithina System platform user (audit actor)
- 01_lookups.sql: tier, industry, region, statuses, audit_result, etc.
- 02_rbac_static.sql: 5 roles, ~15 permissions, role-permission mappings
- scripts/apply_seeds.sh: idempotent runner
- db/seeds/README.md
- Integration tests verify seed data and idempotency"
```

Ask user "Run? yes / no / edit message".

---

## End of prompt
