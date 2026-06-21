# Prompt — Step 3.4.5: tenant_module_access table + FN-AB-16 cleanup

> Generated 2026-05-02, 11:55 PM. Revised 2026-05-03, 12:35 AM (stress-test fixes: 13 issues across schema-qualification in migrations, audit-actor FK requirements for tests, Lookup ORM model promotion to known scope, array_agg empty-case handling).
> Paste this entire block into a fresh Claude Code session to start Step 3.4.5.
> Resolves FN-AB-16 (the module entitlement stub from Step 3.3). Adds the 11th application table to the schema. Lands a small but architecturally important step before Step 3.5 (the seed loader) to keep `tenant_module_access` data in the database, not in a Python dict.

---

## Context: why this step exists and why now

Step 3.3 deferred `tenant_module_access` to a Python stub (`_module_entitlements_stub.py`) because the table didn't yet exist in the DDLs. FN-AB-16 tracked the eventual cleanup with an xfail-strict tripwire test that fires when the stub file is deleted.

Doing this *before* Step 3.5 (the seed loader) means the loader inherits a clean Repo — `TenantsRepo` queries the real table, the seed loader treats `tenant_module_access` as a normal sheet to load, no stub data to keep in sync with the Excel. The cost of doing it now is bounded; the cost of doing it later is having to surgically extract module data from the loader-then-stub bifurcation.

This step is back-fill that cleans up tech debt before it spreads.

---

## Pre-flight

1. Run `./scripts/check_setup.sh`. Expect 35/35.
2. `git log --oneline -5` — confirm Step 3.3 + the convention note for PG_ENUM at HEAD.
3. Read `CLAUDE.md` fully. Focus on:
   - **D-13** — audit-actor patterns; `tenant_module_access` is Pattern (a) since modules are PLATFORM-only managed (typed FK direct to `platform_users`, no `*_by_user_type` discriminator).
   - **D-15** — `__table_args__["schema"]` parameterisation.
   - **D-21** — UUIDv7 via project `uuidv7()` PL/pgSQL function.
   - **D-27** — NULLIF wrapper on RLS policies.
   - **D-29** — PLATFORM RLS visibility via OR-clause; new policy on `tenant_module_access` follows the unconditional shape (since `tenant_id` is NOT NULL on this table).
   - The "Note on PG enum columns" subsection in Code conventions (added in the previous commit) — applies to both `module_code_enum` declaration and `module_access_status_enum` here.
   - **FN-AB-16** — the resolution shape this step delivers.
4. Read `docs/architecture.md` "Schema and storage" section.
5. Read `db/raw_ddl/Ithina_postgres_SQL_DDL_lookups_v1.sql` — note the table shape. This step seeds `module_code` rows into `lookups`. The list_name is `module_code` (snake_case per `ck_lookups_list_name_format`); codes are UPPER_SNAKE_CASE per `ck_lookups_code_format`.
6. Read `db/raw_ddl/Ithina_postgres_SQL_DDL_tenants_v3.sql` for reference on the Pattern (a) audit-actor shape (typed FK direct to platform_users; no `*_by_user_type` columns). The new table mirrors this pattern.
7. Read `db/raw_ddl/Ithina_postgres_SQL_DDL_tenant_users_v1.sql` for reference on status/companion-field CHECK constraint patterns. The new table's `status='DISABLED'` requires `disabled_at` populated, mirroring this style.
8. Run `uv run alembic heads`. Confirm output is exactly `21e2ad16303a (head)`. If anything else, the migration chain has shifted since this prompt was drafted; surface and pause.
9. Read the existing FN-AB-14 migration `migrations/versions/4fd3aec6ae0c_*.py` and Step 3.0's migration `migrations/versions/21e2ad16303a_*.py` for migration style. Note specifically: how schema-qualified table names are written in `op.execute()` blocks (see "Schema qualification in migration body" in regression risks).
10. Read `src/admin_backend/repositories/_module_entitlements_stub.py` and `tests/unit/test_module_entitlements.py` — both files get deleted in this step's commit.
11. Read `src/admin_backend/repositories/tenants.py` — the `list_with_aggregates` and `get_by_id_with_aggregates` methods currently call `get_modules_for_tenant(...)` from the stub. Both methods change in this step to query `tenant_module_access` directly via subquery.
12. Read `tests/integration/conftest.py` (Step 3.2/3.3's existing fixtures) and `tests/integration/test_tenants_router.py` — specifically how Step 3.3's test setup populates `tenants.created_by_user_id` and `tenants.updated_by_user_id` (both NOT NULL FK to platform_users on the tenants table). Step 3.4.5 inherits whatever pattern is already in place; if no platform_user is being created in tests today (i.e., the existing tests are somehow getting away with NULL or skirting the constraint via the `make_tenant` fixture's defaults), this step adds a `make_platform_user` fixture as part of its work. Surface the answer during pre-flight before writing fixture code.
13. Read `BUILD_PLAN.md` Step 3.4.5 (which doesn't exist yet — this step adds it as a new section between Step 3.4 and Step 3.5).
14. Read this prompt fully.

---

## Step ID and intent

**Step 3.4.5** — Schema-level resolution of FN-AB-16. Adds `tenant_module_access` table with full lifecycle audit columns. Replaces the Step 3.3 stub with a real query path. Seeds the `lookups` table with `module_code` reference data so the API can return display names alongside codes.

Eight concrete deliverables:

1. **DDL file** (raw, not edited per the established convention) defining `tenant_module_access` plus the two new PG enums.
2. **Alembic migration** applying the table, the enums, the RLS policy, the indexes, and the lookups seed. Schema-qualified throughout.
3. **ORM model** for `TenantModuleAccess`.
4. **ORM model** for `Lookup` — needed for the JOIN in TenantsRepo's new subquery to resolve display names. The `lookups` table is from Step 1.4 DDL load but no application code has needed an ORM model yet; this step adds it. Small (~30 lines mirroring the lookups DDL columns).
5. **Repo update** — `TenantsRepo.list_with_aggregates` and `get_by_id_with_aggregates` switch from stub call to subquery against `tenant_module_access` (joined with `lookups` for display names).
6. **Stub cleanup** — delete `_module_entitlements_stub.py`, delete the xfail tripwire test, update integration tests that exercised the stub.
7. **Test infrastructure additions** — new `make_tenant_module_access` fixture in conftest. Plus, depending on what Pre-flight item 12 surfaces, possibly a new `make_platform_user` fixture (the new table's audit-actor columns are NOT NULL FK to platform_users; tests need a real platform_user ID, no NULL escape hatch).
8. **Smoke test additions** — extend the truth-table assertions to cover the new table's RLS policy.

CLAUDE_CODE step. Same complexity envelope as Step 3.0 (migration + RLS + smoke + cleanup) — half a day or so.

---

## Source-of-truth specification

### File 1: `db/raw_ddl/Ithina_postgres_SQL_DDL_tenant_module_access_v1.sql` — new

The DDL is the platform's source of truth for the table's *initial* shape. Per the "DDLs frozen at as-shipped initial-schema state" convention captured at Step 3.0, this file is created once and not edited per future migration. The Alembic migration in File 2 carries the actual SQL applied to the DB.

```sql
-- ============================================================================
-- Ithina platform master DB — tenant_module_access
--
-- Tracks which modules each tenant is entitled to use, with full lifecycle
-- audit (enabled_by, disabled_by, dates) for billing and compliance.
--
-- Modules are platform-fixed: Ithina engineering adds new modules via DDL
-- migration. Tenants do NOT extend the module set. The module_code_enum
-- enumerates the set; the lookups table provides display names for UI.
--
-- Pattern (a) audit-actors per D-13: typed FKs direct to platform_users,
-- no *_by_user_type discriminator (modules are managed by Ithina staff
-- only; no TENANT user_type ever appears in audit-actor columns).
--
-- RLS follows D-29: tenant-id-equality plus PLATFORM OR-branch (the
-- unconditional form since tenant_id is NOT NULL on this table).
-- ============================================================================


-- ----------------------------------------------------------------------------
-- Enums
-- ----------------------------------------------------------------------------

CREATE TYPE module_code_enum AS ENUM (
    'ROOS',
    'PRICING_OS',
    'PERISHABLES_ASSISTANT',
    'PROMOTIONS_ASSISTANT',
    'GOAL_CONSOLE',
    'ADMIN'
);

CREATE TYPE module_access_status_enum AS ENUM (
    'ENABLED',
    'DISABLED'
);


-- ----------------------------------------------------------------------------
-- Table: tenant_module_access
-- ----------------------------------------------------------------------------

CREATE TABLE tenant_module_access (
    id UUID NOT NULL DEFAULT uuidv7(),

    tenant_id   UUID NOT NULL,
    module      module_code_enum NOT NULL,
    status      module_access_status_enum NOT NULL,

    -- ----- lifecycle ------------------------------------------------------
    enabled_at              TIMESTAMPTZ NOT NULL,
        -- Required: billing reads this to compute prorated charges.
    enabled_by_user_id      UUID NOT NULL,
    disabled_at             TIMESTAMPTZ NULL,
    disabled_by_user_id     UUID NULL,

    -- ----- audit ----------------------------------------------------------
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by_user_id  UUID NOT NULL,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by_user_id  UUID NOT NULL,

    -- ----- constraints ----------------------------------------------------
    CONSTRAINT pk_tenant_module_access
        PRIMARY KEY (id),

    CONSTRAINT uq_tenant_module_access_tenant_module
        UNIQUE (tenant_id, module),
        -- One row per tenant per module. Re-enabling after disable
        -- updates the existing row rather than inserting a new one.

    CONSTRAINT fk_tenant_module_access_tenant
        FOREIGN KEY (tenant_id) REFERENCES tenants(id),

    CONSTRAINT fk_tenant_module_access_enabled_by
        FOREIGN KEY (enabled_by_user_id) REFERENCES platform_users(id),
    CONSTRAINT fk_tenant_module_access_disabled_by
        FOREIGN KEY (disabled_by_user_id) REFERENCES platform_users(id),
    CONSTRAINT fk_tenant_module_access_created_by
        FOREIGN KEY (created_by_user_id) REFERENCES platform_users(id),
    CONSTRAINT fk_tenant_module_access_updated_by
        FOREIGN KEY (updated_by_user_id) REFERENCES platform_users(id),

    CONSTRAINT ck_tenant_module_access_disabled_pair
        CHECK (
            (disabled_at IS NULL AND disabled_by_user_id IS NULL)
            OR (disabled_at IS NOT NULL AND disabled_by_user_id IS NOT NULL)
        ),
        -- XOR pairing: disabled_at and disabled_by_user_id both NULL
        -- (module currently enabled) or both NOT NULL (module disabled).

    CONSTRAINT ck_tenant_module_access_status_consistency
        CHECK (
            (status = 'ENABLED' AND disabled_at IS NULL)
            OR (status = 'DISABLED' AND disabled_at IS NOT NULL)
        )
        -- ENABLED requires no disabled_at; DISABLED requires disabled_at set.
);


-- ----------------------------------------------------------------------------
-- Indexes
-- ----------------------------------------------------------------------------

-- Primary read pattern: the API joins tenants → tenant_module_access on
-- tenant_id to populate the modules array for each tenant card.
CREATE INDEX ix_tenant_module_access_tenant_id
    ON tenant_module_access (tenant_id);

-- Trigger for updated_at — mirrors the pattern used elsewhere.
CREATE TRIGGER trg_tenant_module_access_set_updated_at
    BEFORE UPDATE ON tenant_module_access
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at_now();


-- ----------------------------------------------------------------------------
-- Row Level Security
-- ----------------------------------------------------------------------------

-- Standard pattern per D-03 + D-27 + D-29: tenant-id-equality plus PLATFORM
-- OR-branch (unconditional form — tenant_id is NOT NULL here).

ALTER TABLE tenant_module_access ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_module_access FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_module_access_tenant_isolation
    ON tenant_module_access
    FOR ALL
    USING (
        tenant_id = NULLIF(current_setting('app.tenant_id', TRUE), '')::uuid
        OR current_setting('app.user_type', TRUE) = 'PLATFORM'
    )
    WITH CHECK (
        tenant_id = NULLIF(current_setting('app.tenant_id', TRUE), '')::uuid
        OR current_setting('app.user_type', TRUE) = 'PLATFORM'
    );
```

Note: the `set_updated_at_now()` function and the trigger pattern should already exist (used by `lookups` and others). Verify it's in `shared_utilities_v1.sql` before assuming; if not, the migration adds the trigger inline using NOW().

### File 2: Alembic migration `migrations/versions/<rev>_step_3_4_5_tenant_module_access.py` — new

Generate via:
```bash
uv run alembic revision -m "step_3_4_5_tenant_module_access"
```

`down_revision = "21e2ad16303a"` (Step 3.0's revision; Step 3.3 added no migration, only code).

**Critical: schema qualification.** Alembic's `op.execute()` blocks do not auto-set search_path. Every table reference, enum reference, and function reference in the migration body MUST be schema-qualified with `core.` (the application schema per D-15). Without this, objects could land in `public` instead of `core`, or trigger creation could fail with "function does not exist" errors. The Step 3.0 migration is the closest precedent for the right pattern; mirror it.

**No CASCADE on DROP statements.** Mirror the discipline established at Step 1.6 (never CASCADE). Use explicit drops in reverse-creation order. The order matters: enums can only be dropped after the columns using them are gone; the table drop implicitly removes its triggers/indexes/policies, but explicit drops in reverse order are cleaner and safer.

The migration upgrade body (skeleton):

```python
"""step_3_4_5_tenant_module_access

Revision ID: <auto-generated>
Revises: 21e2ad16303a
Create Date: <auto-generated>

"""
from alembic import op
import sqlalchemy as sa


revision = "<auto-generated>"
down_revision = "21e2ad16303a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Create the two PG enum types.
    op.execute("""
        CREATE TYPE core.module_code_enum AS ENUM (
            'ROOS',
            'PRICING_OS',
            'PERISHABLES_ASSISTANT',
            'PROMOTIONS_ASSISTANT',
            'GOAL_CONSOLE',
            'ADMIN'
        )
    """)
    op.execute("""
        CREATE TYPE core.module_access_status_enum AS ENUM (
            'ENABLED',
            'DISABLED'
        )
    """)

    # 2. Create the table.
    op.execute("""
        CREATE TABLE core.tenant_module_access (
            id UUID NOT NULL DEFAULT core.uuidv7(),
            tenant_id UUID NOT NULL,
            module core.module_code_enum NOT NULL,
            status core.module_access_status_enum NOT NULL,
            enabled_at TIMESTAMPTZ NOT NULL,
            enabled_by_user_id UUID NOT NULL,
            disabled_at TIMESTAMPTZ NULL,
            disabled_by_user_id UUID NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            created_by_user_id UUID NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_by_user_id UUID NOT NULL,

            CONSTRAINT pk_tenant_module_access PRIMARY KEY (id),
            CONSTRAINT uq_tenant_module_access_tenant_module
                UNIQUE (tenant_id, module),

            CONSTRAINT fk_tenant_module_access_tenant
                FOREIGN KEY (tenant_id) REFERENCES core.tenants(id),
            CONSTRAINT fk_tenant_module_access_enabled_by
                FOREIGN KEY (enabled_by_user_id) REFERENCES core.platform_users(id),
            CONSTRAINT fk_tenant_module_access_disabled_by
                FOREIGN KEY (disabled_by_user_id) REFERENCES core.platform_users(id),
            CONSTRAINT fk_tenant_module_access_created_by
                FOREIGN KEY (created_by_user_id) REFERENCES core.platform_users(id),
            CONSTRAINT fk_tenant_module_access_updated_by
                FOREIGN KEY (updated_by_user_id) REFERENCES core.platform_users(id),

            CONSTRAINT ck_tenant_module_access_disabled_pair
                CHECK (
                    (disabled_at IS NULL AND disabled_by_user_id IS NULL)
                    OR (disabled_at IS NOT NULL AND disabled_by_user_id IS NOT NULL)
                ),
            CONSTRAINT ck_tenant_module_access_status_consistency
                CHECK (
                    (status = 'ENABLED' AND disabled_at IS NULL)
                    OR (status = 'DISABLED' AND disabled_at IS NOT NULL)
                )
        )
    """)

    # 3. Index for the primary read pattern.
    op.execute("""
        CREATE INDEX ix_tenant_module_access_tenant_id
            ON core.tenant_module_access (tenant_id)
    """)

    # 4. Trigger for updated_at.
    # Verify core.set_updated_at_now() exists in the DB before this runs.
    # If it doesn't exist, the trigger creation fails and the migration
    # aborts — see Stop-and-ask in this prompt for the contingency.
    op.execute("""
        CREATE TRIGGER trg_tenant_module_access_set_updated_at
            BEFORE UPDATE ON core.tenant_module_access
            FOR EACH ROW
            EXECUTE FUNCTION core.set_updated_at_now()
    """)

    # 5. Enable RLS + FORCE.
    op.execute("ALTER TABLE core.tenant_module_access ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE core.tenant_module_access FORCE ROW LEVEL SECURITY")

    # 6. RLS policy — D-29 unconditional OR-clause shape.
    op.execute("""
        CREATE POLICY tenant_module_access_tenant_isolation
            ON core.tenant_module_access
            FOR ALL
            USING (
                tenant_id = NULLIF(current_setting('app.tenant_id', TRUE), '')::uuid
                OR current_setting('app.user_type', TRUE) = 'PLATFORM'
            )
            WITH CHECK (
                tenant_id = NULLIF(current_setting('app.tenant_id', TRUE), '')::uuid
                OR current_setting('app.user_type', TRUE) = 'PLATFORM'
            )
    """)

    # 7. Seed module_code rows into core.lookups.
    # Display names match the Step 3.3 stub for response-shape stability
    # on cutover. display_order locks visual ordering at the API surface.
    op.execute("""
        INSERT INTO core.lookups (list_name, code, display_name, display_order, is_active)
        VALUES
            ('module_code', 'ROOS',                  'ROOS',                  1, TRUE),
            ('module_code', 'PRICING_OS',            'Pricing OS',            2, TRUE),
            ('module_code', 'PERISHABLES_ASSISTANT', 'Perishables Assistant', 3, TRUE),
            ('module_code', 'PROMOTIONS_ASSISTANT',  'Promotions Assistant',  4, TRUE),
            ('module_code', 'GOAL_CONSOLE',          'Goal Console',          5, TRUE),
            ('module_code', 'ADMIN',                 'Admin',                 6, TRUE)
    """)


def downgrade() -> None:
    # Reverse-creation order; no CASCADE.
    # 1. Remove lookups seed.
    op.execute("""
        DELETE FROM core.lookups
        WHERE list_name = 'module_code'
          AND code IN ('ROOS','PRICING_OS','PERISHABLES_ASSISTANT',
                       'PROMOTIONS_ASSISTANT','GOAL_CONSOLE','ADMIN')
    """)
    # 2. Drop policy.
    op.execute("DROP POLICY tenant_module_access_tenant_isolation ON core.tenant_module_access")
    # 3. Disable RLS (FORCE is automatic when RLS disables).
    op.execute("ALTER TABLE core.tenant_module_access DISABLE ROW LEVEL SECURITY")
    # 4. Drop trigger and index.
    op.execute("DROP TRIGGER trg_tenant_module_access_set_updated_at ON core.tenant_module_access")
    op.execute("DROP INDEX ix_tenant_module_access_tenant_id")
    # 5. Drop the table (also implicitly removes the column types' usages).
    op.execute("DROP TABLE core.tenant_module_access")
    # 6. Drop the enum types.
    op.execute("DROP TYPE core.module_access_status_enum")
    op.execute("DROP TYPE core.module_code_enum")
```

The skeleton above is structurally complete; Claude Code adapts paths/names if any drift from the prompt is needed. The schema-qualified pattern (`core.tenant_module_access`, `core.module_code_enum`, `core.set_updated_at_now()`) is the load-bearing convention.

### File 3: `src/admin_backend/models/tenant_module_access.py` — new

Full ORM model from the start (not a stub like the Step 3.3 lightweight stubs, since this table will be queried directly by `TenantsRepo` immediately).

```python
"""TenantModuleAccess ORM model.

Tracks which modules each tenant is entitled to use, with full lifecycle
audit columns. Pattern (a) audit-actors per D-13: typed FKs direct to
platform_users, no *_by_user_type discriminator (modules are managed by
Ithina staff only).
"""
from datetime import datetime
from enum import Enum
from uuid import UUID

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, Text
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM
from sqlalchemy.orm import Mapped, mapped_column

from admin_backend.config import get_settings
from admin_backend.db.base import Base


_DB_SCHEMA = get_settings().db_schema


class ModuleCode(str, Enum):
    """Platform-fixed module codes. Mirrors module_code_enum in DDL."""
    ROOS = "ROOS"
    PRICING_OS = "PRICING_OS"
    PERISHABLES_ASSISTANT = "PERISHABLES_ASSISTANT"
    PROMOTIONS_ASSISTANT = "PROMOTIONS_ASSISTANT"
    GOAL_CONSOLE = "GOAL_CONSOLE"
    ADMIN = "ADMIN"


class ModuleAccessStatus(str, Enum):
    """Module access status. Mirrors module_access_status_enum in DDL."""
    ENABLED = "ENABLED"
    DISABLED = "DISABLED"


class TenantModuleAccess(Base):
    __tablename__ = "tenant_module_access"
    __table_args__ = {"schema": _DB_SCHEMA}

    id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column()  # FK declared at DB level
    module: Mapped[ModuleCode] = mapped_column(
        PG_ENUM(
            ModuleCode,
            name="module_code_enum",
            create_type=False,
            native_enum=True,
            values_callable=lambda e: [m.value for m in e],
        )
    )
    status: Mapped[ModuleAccessStatus] = mapped_column(
        PG_ENUM(
            ModuleAccessStatus,
            name="module_access_status_enum",
            create_type=False,
            native_enum=True,
            values_callable=lambda e: [m.value for m in e],
        )
    )
    enabled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    enabled_by_user_id: Mapped[UUID] = mapped_column()
    disabled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    disabled_by_user_id: Mapped[UUID | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[UUID] = mapped_column()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_by_user_id: Mapped[UUID] = mapped_column()
```

Notes:
- `module` and `status` use `PG_ENUM(..., create_type=False, native_enum=True)` per the convention reminder. `Text` would fail the same way Step 3.3's TenantUser stub did.
- `values_callable` mirrors what Step 3.1's amendment uses on the Tenant model — defends against any future enum where `name != value`.
- No SQLAlchemy `ForeignKey(...)` declarations on the audit-actor columns (same reasoning as Step 3.1's Tenant model — avoids forward-reference complexity; FK constraints exist at the DB level).
- No `server_default=FetchedValue()` on `id` because the model is built from scratch — but actually verify: per Step 3.1's amendment, columns with DB-side defaults need `FetchedValue()` so SQLAlchemy omits them from INSERT. So `id`, `created_at`, `updated_at` need `FetchedValue()`. Mirror the pattern from `models/tenant.py` post-3.1-amendment.

### File 4: `src/admin_backend/models/lookup.py` — new

The `lookups` table is from Step 1.4 DDL but no application code has needed an ORM model yet. Step 3.4.5 needs one for the JOIN in `TenantsRepo`'s subquery.

```python
"""Lookup ORM model.

Maps the `lookups` table created at Step 1.4. Each row is a (list_name, code,
display_name) reference entry — the platform's source of truth for enum-style
display data the API exposes to frontends. List names are snake_case
(matching ck_lookups_list_name_format); codes are UPPER_SNAKE_CASE.
"""
from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import FetchedValue

from admin_backend.config import get_settings
from admin_backend.db.base import Base


_DB_SCHEMA = get_settings().db_schema


class Lookup(Base):
    __tablename__ = "lookups"
    __table_args__ = {"schema": _DB_SCHEMA}

    id: Mapped[UUID] = mapped_column(
        primary_key=True, server_default=FetchedValue()
    )
    list_name: Mapped[str] = mapped_column(Text)
    code: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    display_order: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=FetchedValue()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=FetchedValue()
    )
```

Notes:
- `FetchedValue()` on `id`, `created_at`, `updated_at` — DB-side defaults exist; SQLAlchemy must be told to defer per Step 3.1's amendment lesson.
- No PG enum columns on this table; all-Text plus standard types. The PG_ENUM convention reminder applies elsewhere; here it's not relevant.
- This model is exported via `models/__init__.py` (full model, not a stub like the lightweight Store/TenantUser stubs from Step 3.3).

### File 5: `src/admin_backend/models/__init__.py` — modify

Re-export both new models. Match the existing pattern (which currently imports Tenant from Step 3.1):

```python
from admin_backend.models.tenant import Tenant
from admin_backend.models.tenant_module_access import (
    TenantModuleAccess, ModuleCode, ModuleAccessStatus,
)
from admin_backend.models.lookup import Lookup

__all__ = [
    "Tenant",
    "TenantModuleAccess", "ModuleCode", "ModuleAccessStatus",
    "Lookup",
]
```

The lightweight stubs (Store, TenantUser) from Step 3.3 are intentionally NOT exported through `__init__` — they're stubs scoped to internal subquery use. This convention stays. New full models go through `__init__`; stubs do not.

### File 6: `src/admin_backend/repositories/tenants.py` — modify

Replace the calls to `get_modules_for_tenant(...)` with subqueries against `tenant_module_access` joined to `lookups`.

The query shape: for each tenant, fetch the array of `(code, name)` tuples where `code = tenant_module_access.module` and `name = lookups.display_name` for the matching `lookups WHERE list_name = 'module_code' AND code = tenant_module_access.module`. Filter to `status = 'ENABLED'` so DISABLED modules don't surface in the API.

**Critical: handle the empty-modules case.** `array_agg(...)` over zero rows returns SQL NULL, not an empty array. A tenant with no enabled modules would come back with `modules: null`, which breaks the response schema (`list[Module]` doesn't accept None). Wrap with `COALESCE(..., '[]'::jsonb)` to guarantee an empty JSON array.

**Sort ordering.** Modules per tenant are returned in `lookups.display_order` ascending — the platform's canonical ordering. The frontend renders cards by iterating the array; ordering must be stable.

The subquery (illustrative; adapt to project's import patterns):

```python
from sqlalchemy import and_, func, select
from sqlalchemy.dialects.postgresql import JSONB

from admin_backend.models import Tenant, TenantModuleAccess, Lookup, ModuleAccessStatus

# Build the per-tenant modules subquery.
# Returns a JSONB array of {code, name} per tenant, ordered by display_order.
# Empty arrays come back as `[]`, never NULL.
modules_subq = (
    select(
        func.coalesce(
            func.jsonb_agg(
                func.jsonb_build_object(
                    "code", TenantModuleAccess.module,
                    "name", Lookup.display_name,
                ),
                # ORDER BY clause for jsonb_agg — keeps array stable.
                order_by=Lookup.display_order.asc(),
            ),
            sa.text("'[]'::jsonb"),
        )
    )
    .select_from(TenantModuleAccess)
    .join(
        Lookup,
        and_(
            Lookup.list_name == "module_code",
            Lookup.code == TenantModuleAccess.module,
        ),
    )
    .where(
        TenantModuleAccess.tenant_id == Tenant.id,
        TenantModuleAccess.status == ModuleAccessStatus.ENABLED,
    )
    .correlate(Tenant)
    .scalar_subquery()
)
```

Notes:
- `jsonb_agg(... ORDER BY ...)` is preferred over `array_agg` because (a) it returns JSONB which SQLAlchemy with the Postgres dialect natively deserializes to Python `list[dict]`, and (b) the `ORDER BY` is more explicit than relying on tuple-unpacking order.
- The COALESCE wrap is the load-bearing fix for the empty-modules case.
- `.correlate(Tenant)` scopes the subquery to the outer row's tenant — same pattern as Step 3.3's num_stores/num_users_active subqueries.

The subquery slots into the existing list_with_aggregates and get_by_id_with_aggregates methods alongside the existing num_stores and num_users_active subqueries. The handler's response-mapping code already expects `modules` to be `list[Module]`; with the COALESCE, the value is always a list, never None.

The Step 3.3 stub call (`get_modules_for_tenant(tenant.id)`) is removed from both Repo methods. The handler shape stays the same — only the source changes.

### File 7: Delete `src/admin_backend/repositories/_module_entitlements_stub.py`

Per FN-AB-16's resolution shape. The file is deleted in this commit.

### File 8: Delete `tests/unit/test_module_entitlements.py`

The xfail-strict tripwire. When the stub file is deleted, this test xpasses (under strict=True, that's a test failure forcing cleanup). We're forcing the cleanup *in this same commit* by deleting the test alongside the stub.

If the test is left in place after the stub deletion, the next pytest run fails. Cleanup discipline: both files go in the same commit.

### File 9: `tests/integration/conftest.py` — modify

Two new fixtures, mirroring the patterns of `make_tenant`, `make_store`, `make_tenant_user` from Step 3.3.

**Audit-actor FK satisfaction.** The new `tenant_module_access` table has four NOT NULL FK columns to `platform_users`: `enabled_by_user_id`, `created_by_user_id`, `updated_by_user_id`, plus `disabled_by_user_id` (nullable). Tests that insert tenant_module_access rows need a real platform_users.id to use as the audit-actor.

Pre-flight item 12 surfaces what Step 3.3 already does for this — depending on the answer, two paths:

- **Path A:** Step 3.3's tests already create platform_users via some pattern. Reuse it. Just add `make_tenant_module_access` fixture.
- **Path B:** Step 3.3's tests skirt the audit-actor FK somehow (NULL pairs, or audit-actors aren't enforced today). Step 3.4.5 adds a `make_platform_user` fixture to bridge the gap.

Path B is more likely (Step 3.2/3.3's `make_tenant` fixture probably uses NULL or some bootstrap value). Sketch for Path B:

```python
@pytest_asyncio.fixture
async def make_platform_user(
    session_factory,
    platform_auth,
) -> AsyncIterator[Callable[..., Awaitable[PlatformUser]]]:
    """Async factory: insert + commit a PlatformUser via PLATFORM session.
    Tracks IDs and DELETEs at teardown.

    The bootstrap row (Anjali, with self-referential created_by) lands as
    a special case: created_by_user_id = id (self). This requires either
    deferred FK constraints (the platform_users DDL may have these) or
    a two-phase insert (NULL created_by, then UPDATE). Tests should not
    need the bootstrap pattern — they create non-bootstrap users with
    created_by_user_id pointing to an existing platform_users.id.

    For tests that need the bootstrap row to exist, surface; we'll add
    a session-scoped fixture that ensures one is present.
    """
    created_ids: list[UUID] = []

    async def _make(
        *,
        email: str = "test@ithina.test",
        full_name: str = "Test User",
        auth0_sub: str = "auth0|test",
        status: str = "ACTIVE",
        created_by_user_id: UUID | None = None,
        **overrides,
    ) -> PlatformUser:
        # If created_by_user_id is None, default to self-referential
        # (platform_users may permit this for the bootstrap row, or this
        # path may need adjustment based on what the DDL actually allows).
        # Surface during pre-flight if defaults conflict with constraints.
        ...

    yield _make
    # Teardown: DELETE by tracked IDs.
```

Then the tenant_module_access fixture:

```python
@pytest_asyncio.fixture
async def make_tenant_module_access(
    session_factory,
    platform_auth,
) -> AsyncIterator[Callable[..., Awaitable[TenantModuleAccess]]]:
    """Async factory: insert + commit a TenantModuleAccess row via PLATFORM
    session. Tracks IDs and DELETEs at teardown.

    Defaults: status=ENABLED, enabled_at=now, all audit-actor IDs supplied
    by caller. Tests setting status=DISABLED MUST supply disabled_at and
    disabled_by_user_id (CHECK constraint enforces XOR pairing and status
    consistency).
    """
    created_ids: list[UUID] = []

    async def _make(
        *,
        tenant_id: UUID,
        module: ModuleCode,
        enabled_by_user_id: UUID,        # required: NOT NULL FK
        created_by_user_id: UUID,        # required: NOT NULL FK
        updated_by_user_id: UUID,        # required: NOT NULL FK
        status: ModuleAccessStatus = ModuleAccessStatus.ENABLED,
        enabled_at: datetime | None = None,
        disabled_at: datetime | None = None,
        disabled_by_user_id: UUID | None = None,
        **overrides,
    ) -> TenantModuleAccess:
        if enabled_at is None:
            enabled_at = datetime.now(tz=timezone.utc)
        # Validation: status=DISABLED requires the disabled_* pair
        if status == ModuleAccessStatus.DISABLED:
            if disabled_at is None or disabled_by_user_id is None:
                raise ValueError(
                    "status=DISABLED requires disabled_at and "
                    "disabled_by_user_id (per CHECK constraint)"
                )
        # ... insert + commit + track id pattern

    yield _make
    # Teardown: DELETE by tracked IDs.
```

Tests that use `make_tenant_module_access` typically:

1. Create a platform_user via `make_platform_user`, capture its `id`.
2. Create a tenant via `make_tenant`, capture its `id`.
3. Create one or more tenant_module_access rows via `make_tenant_module_access`, supplying the platform_user's id as the audit-actor.

Setup is more verbose than for simpler tables, but unavoidable — the audit-actor FK is real.

### File 10: `tests/integration/test_tenants_router.py` — modify

Update L10 and D6 (the modules tests). Both currently assert against the Step 3.3 stub's hardcoded data; they get rewritten to assert against real seeded `tenant_module_access` rows.

**L10 was:** "each returned item has the module list expected from the stub."

**L10 becomes** (concrete shape, matching the new fixture's required parameters):

```python
async def test_l10_modules_from_table_with_display_name_resolution(
    client, make_tenant, make_platform_user, make_tenant_module_access,
    platform_jwt,
):
    # Setup: platform_user for audit actors, two tenants
    actor = await make_platform_user(email="actor@ithina.test")
    tenant_a = await make_tenant(name="L10-Alpha")
    tenant_b = await make_tenant(name="L10-Bravo")

    # Tenant A: ROOS enabled, PRICING_OS enabled, ADMIN disabled
    await make_tenant_module_access(
        tenant_id=tenant_a.id,
        module=ModuleCode.ROOS,
        status=ModuleAccessStatus.ENABLED,
        enabled_by_user_id=actor.id,
        created_by_user_id=actor.id,
        updated_by_user_id=actor.id,
    )
    await make_tenant_module_access(
        tenant_id=tenant_a.id,
        module=ModuleCode.PRICING_OS,
        status=ModuleAccessStatus.ENABLED,
        enabled_by_user_id=actor.id,
        created_by_user_id=actor.id,
        updated_by_user_id=actor.id,
    )
    # DISABLED row — must supply disabled_at and disabled_by_user_id
    # per the status_consistency and disabled_pair CHECK constraints.
    await make_tenant_module_access(
        tenant_id=tenant_a.id,
        module=ModuleCode.ADMIN,
        status=ModuleAccessStatus.DISABLED,
        enabled_by_user_id=actor.id,
        created_by_user_id=actor.id,
        updated_by_user_id=actor.id,
        disabled_at=datetime.now(tz=timezone.utc),
        disabled_by_user_id=actor.id,
    )
    # Tenant B: only ROOS enabled
    await make_tenant_module_access(
        tenant_id=tenant_b.id,
        module=ModuleCode.ROOS,
        status=ModuleAccessStatus.ENABLED,
        enabled_by_user_id=actor.id,
        created_by_user_id=actor.id,
        updated_by_user_id=actor.id,
    )

    # Act
    response = await client.get(
        "/api/v1/tenants?search=L10-",
        headers={"Authorization": f"Bearer {platform_jwt}"},
    )

    # Assert
    assert response.status_code == 200
    items = response.json()["items"]
    items_by_name = {item["name"]: item for item in items}

    # Tenant A: ROOS + PRICING_OS, DISABLED ADMIN filtered out
    a_modules = items_by_name["L10-Alpha"]["modules"]
    assert len(a_modules) == 2
    a_codes = {m["code"] for m in a_modules}
    assert a_codes == {"ROOS", "PRICING_OS"}
    # Display name resolution from lookups
    assert {m["name"] for m in a_modules} == {"ROOS", "Pricing OS"}
    # Stable ordering by display_order: ROOS (1) before PRICING_OS (2)
    assert a_modules[0]["code"] == "ROOS"
    assert a_modules[1]["code"] == "PRICING_OS"

    # Tenant B: just ROOS
    b_modules = items_by_name["L10-Bravo"]["modules"]
    assert b_modules == [{"code": "ROOS", "name": "ROOS"}]
```

This test exercises:
- The JOIN to `lookups` for display name resolution
- The DISABLED-status filter (ADMIN doesn't appear)
- The stable ordering by `display_order`
- The cross-tenant isolation (tenant_a's modules don't appear on tenant_b's row)
- The empty-modules COALESCE path is NOT exercised here — add a separate test if you want explicit coverage of "tenant with zero enabled modules returns `modules: []`".

**D6** is the analogous detail-endpoint test:

```python
async def test_d6_detail_modules_from_table(
    client, make_tenant, make_platform_user, make_tenant_module_access,
    platform_jwt,
):
    actor = await make_platform_user(email="actor-d6@ithina.test")
    tenant = await make_tenant(name="D6-Tenant")
    await make_tenant_module_access(
        tenant_id=tenant.id, module=ModuleCode.ROOS,
        enabled_by_user_id=actor.id,
        created_by_user_id=actor.id,
        updated_by_user_id=actor.id,
    )

    response = await client.get(
        f"/api/v1/tenants/{tenant.id}",
        headers={"Authorization": f"Bearer {platform_jwt}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["modules"] == [{"code": "ROOS", "name": "ROOS"}]
```

**Plus one new test worth adding** — the empty-modules case to verify COALESCE works:

```python
async def test_l10b_tenant_with_no_modules_returns_empty_array(
    client, make_tenant, platform_jwt,
):
    """Tenant with zero rows in tenant_module_access. The COALESCE
    in the subquery wraps NULL → '[]'::jsonb; response carries
    an empty list, not None."""
    tenant = await make_tenant(name="L10b-NoModules")
    response = await client.get(
        f"/api/v1/tenants/{tenant.id}",
        headers={"Authorization": f"Bearer {platform_jwt}"},
    )
    assert response.status_code == 200
    assert response.json()["modules"] == []
```

This catches the easy regression where someone "simplifies" the COALESCE out of the subquery later.

### File 11: `scripts/smoke_test.py` — modify

The existing meta-assertion 12 (every tenant_id-bearing table has RLS + FORCE + ≥1 policy) automatically picks up the new table. The truth-table assertions need extending:

- 9 truth-table cells for `tenant_module_access` (3 GUC combinations × 3 row classes: TENANT-A row, TENANT-B row, no PLATFORM-audience row since this table has NOT NULL tenant_id)
- 1 INSERT assertion for PLATFORM session (mirrors test_16 from Step 3.0)

Total smoke goes from 64 → 73 PASS (or thereabouts; Claude Code computes exact count).

### File 12: `BUILD_PLAN.md` — modify

Add Step 3.4.5 between Step 3.4 (GCP env) and Step 3.5 (seed loader). Status DONE in same commit. Scope-in/acceptance reflects what shipped.

Note: the original BUILD_PLAN didn't anticipate Step 3.4.5 (it's a back-fill surfaced during Step 3.5's planning). The numbering 3.4.5 captures this — "between 3.4 and 3.5 chronologically; 3.4.5 logically."

### File 13: `CLAUDE.md` — modify

- **Current state → Completed:** add Step 3.4.5 bullet covering: new table + 2 enums, OR-clause RLS policy, lookups seed for `module_code`, ORM model, Repo update (subquery pattern), stub cleanup (FN-AB-16 RESOLVED), conftest fixture, integration test updates, smoke test growth.
- **Schema state line:** ticks 10 → 11 application tables. Smoke test count updates to whatever the new total is.
- **FN-AB-16 entry:** mark RESOLVED with a one-line summary of how it landed (mirroring how FN-AB-14 was marked resolved at Step 2.2b).
- **Not yet completed:** advance "Steps 4.x onward" appropriately if needed (Step 3.5 still pending).

### File 14: `docs/architecture.md` — likely yes-edit

The "Schema and storage" section says "10 application tables" (after the drift sweep we did). After this step, it's 11 tables. Update the count and add a one-liner naming `tenant_module_access` in the table-by-DDL mapping.

If architecture.md mentions the module-entitlement stub anywhere (it shouldn't, but check), remove the reference.

### File 15: `prompts/step-3_4_5-tenant-module-access-2026-05-02.md`

This prompt file. Bundled in the commit per the convention.

---

## Testing and regression discipline

### New tests added by this step

- **9 smoke-test truth-table cells** + 1 INSERT assertion for the new table.
- **Updated L10 and D6** in test_tenants_router.py (now using real seeded data instead of stub assertions).
- **Possibly 2-3 new integration tests** for the new Repo path: cross-tenant module isolation (TENANT-A's modules don't appear on TENANT-B's row), DISABLED-module filtering (disabled rows don't surface in API), display-name resolution (the join to lookups works correctly).

### Tests removed by this step

- **`tests/unit/test_module_entitlements.py`** (the xfail tripwire) — deleted alongside the stub file.

### Regression risk surface introduced by this step

1. **The migration's `down_revision` must be `21e2ad16303a`.** Step 3.3 didn't add a migration; the chain is `ad8afd429581 → e59f62d5037d → 4fd3aec6ae0c → 21e2ad16303a → <new>`. Verify with `uv run alembic heads` before generating (Pre-flight item 8).

2. **Schema qualification in migration body.** Alembic `op.execute()` blocks do NOT auto-set search_path. Every reference in the body must be schema-qualified: `core.tenant_module_access`, `core.module_code_enum`, `core.tenants`, `core.platform_users`, `core.uuidv7()`, `core.set_updated_at_now()`, `core.lookups`. Without qualification, objects could land in `public` schema or trigger creation could fail with "function does not exist." This is the most likely-to-bite issue in the migration; Step 3.0's migration is the closest precedent. Mirror it.

3. **No CASCADE on DROP statements in the downgrade path.** Mirror the discipline established at Step 1.6. Use explicit drops in reverse-creation order (lookups seed → policy → RLS disable → trigger → index → table → enum types). PostgreSQL handles `DROP TABLE` cleanly and auto-removes dependent objects, but explicit ordered drops are clearer and safer.

4. **PG enum values must match across DDL, migration, and Python `ModuleCode` Enum.** A typo (e.g., DDL says `'PERISHABLES_ASSIST'` and Python says `'PERISHABLES_ASSISTANT'`) wouldn't surface until first INSERT, with a confusing type-cast error. Cross-reference all three after writing.

5. **Lookups seed values must match `ModuleCode` enum values exactly.** If lookup `code` has `'PERISHABLES_ASSIST'` and enum has `'PERISHABLES_ASSISTANT'`, the JOIN in TenantsRepo returns NULL display_name. Tests should catch via L10's display name assertions; flagging because it's the easiest place to typo.

6. **`set_updated_at_now()` function — verify it exists in `core` schema.** Used by `lookups` already, probably defined in `shared_utilities_v1.sql` and applied to `core`. If not, the trigger creation fails. See Stop-and-ask for contingency.

7. **The Lookup ORM model addition affects nothing pre-existing.** No code currently imports from `models.lookup` — it's a new file. Verify by `grep -rn "from admin_backend.models import.*Lookup\|from admin_backend.models.lookup" src tests` before adding; expect zero hits.

8. **Cleanup pairing — stub file and tripwire test must delete together.** If stub deletes but tripwire remains, next pytest run fails (xfail under strict=True becomes xpass = failure). If tripwire deletes but stub remains, the tripwire is gone but dead code stays. Both deletions in the same commit; tested by running pytest after both deletions.

9. **The list endpoint's existing 21 integration tests must still pass.** The shape of the response doesn't change — modules are still `[{code, name}]` per item. Only the *source* changes (table query instead of dict lookup). Tests asserting on shape/structure don't change; tests asserting on data directly (L10, D6) are rewritten in this step.

10. **Smoke test must run cleanly against both pre-migration and post-migration states.** Pre-migration: the new truth-table cells fail (table doesn't exist). Post-migration: all pass. Round-trip verification via stash → run → unstash → run.

11. **Migration round-trip.** Run upgrade → downgrade → upgrade. All three must succeed. Downgrade completely removes the new table, the two enums, the RLS policy, the trigger, the index, and the lookups rows for `module_code`. If downgrade leaves residue (e.g., a lookups row not deleted because the WHERE clause typoed), the next upgrade will fail with a UNIQUE violation on the lookups seed.

12. **Audit-actor FK satisfaction in tests.** The new `tenant_module_access` table has four NOT NULL FK columns to `platform_users` (three required, one nullable). Tests must create real platform_users before they can create tenant_module_access rows. Step 3.3's existing tests probably get away with this in some way (Pre-flight item 12 surfaces how); if they don't, this step adds `make_platform_user` to conftest, which in turn requires a working pattern for the bootstrap row's self-referential audit. Surface during pre-flight so the fixture work isn't underestimated.

13. **The COALESCE in the modules subquery is load-bearing.** Without it, tenants with zero enabled modules return `modules: null` instead of `modules: []`, breaking the response schema validation. Test L10b specifically guards this; don't remove the COALESCE in any future "simplification."

### Verification harness (run all five; all must be green)

```bash
# 1. Full pytest suite — new + regression
uv run pytest -v

# 2. mypy strict
uv run mypy --strict src/admin_backend

# 3. Pre-flight checker
./scripts/check_setup.sh

# 4. Migration round-trip
uv run alembic upgrade head      # apply 3.4.5
uv run alembic downgrade -1      # revert
uv run alembic upgrade head      # re-apply

# 5. Smoke test (post-migration state)
python scripts/smoke_test.py
```

Expected: pytest 100+ passes (depends on test additions; flag if regressions); mypy clean; check_setup 35/35; alembic round-trip clean; smoke ~73 PASS.

If any leg is not green, **report rather than commit**.

---

## Scope out

- **Step 3.5 (seed loader)** — runs after this. Will load `tenant_module_access` rows from the Excel sheet as part of normal seed flow.
- **Audit-trigger work** that writes `audit_logs` rows when `tenant_module_access` rows change. Step 6.2 territory.
- **API endpoints** for managing module access (e.g., `POST /api/v1/tenants/{id}/modules` to enable a module). Post-v0; the table supports the use case but the endpoints aren't built.
- **Lookup endpoint** (`GET /api/v1/lookups/module_code`) for the frontend's dropdowns. Separate concern; not in this step.
- **Module display ordering decisions beyond `lookups.display_order`.** If the frontend wants module-tier-based ordering or some grouping, that's separate. v0 sorts by display_order only.

---

## Stop and ask if

- Pre-flight item 12 reveals that Step 3.3's existing tests have NO working pattern for `tenants.created_by_user_id` audit-actor population (e.g., the column is somehow currently NULL in test rows despite being NOT NULL FK). Surface; we'll either dig into how it currently passes, or treat the pattern's absence as an existing FN-AB-XX worth tracking before this step proceeds.
- The `set_updated_at_now()` function doesn't exist anywhere in the DDL set or in `core` schema. Surface; we'll either add it to `shared_utilities_v1.sql` (small new function) or inline the trigger logic with `BEGIN NEW.updated_at = NOW(); RETURN NEW; END`.
- The migration's downgrade fails to drop one of the enums because Postgres requires the column type to be dropped first. The order in the skeleton (table dropped before enum types) handles this; if a different order is attempted and fails, fix the order rather than working around with CASCADE.
- The lookups seed conflicts with existing data (a `module_code` row already exists from a prior partial migration or manual INSERT). Default expectation: lookups table is empty for `list_name='module_code'` so straight INSERT works. If it isn't, surface and we'll decide whether to UPSERT or roll back the partial state first.
- The integration tests fail in unexpected ways post-migration (e.g., display_name comes back NULL when it should be populated). This usually means either the JOIN is wrong or the seed didn't apply. Surface with the actual SQL plan.
- Any test currently asserts on the `_module_entitlements_stub` import path or the `get_modules_for_tenant` function. Surface; update to the new path before deleting the stub. Likely candidates: integration tests at L10/D6; unit tests if any exist.
- The platform_users DDL doesn't permit a self-referential `created_by_user_id` for the bootstrap row pattern (i.e., creating a row where created_by_user_id = id). Surface; we'll either use deferred FK constraints (if available), a two-phase INSERT (NULL then UPDATE), or rework `make_platform_user` to require an existing actor.
- The Lookup ORM model needs additional columns or methods beyond the simple shape sketched above (e.g., a class method like `Lookup.get_by_code(session, list_name, code)` is wanted). The sketch is minimal; expand if a clean call site needs it.

---

## Acceptance criteria

- 15-16 files created/modified/deleted (range accommodates whether `architecture.md` and `models/__init__.py` need touches; both are conditional but likely yes).
- New table + 2 enums + RLS policy + trigger + 6 lookups rows applied via single migration. Migration is fully schema-qualified.
- Migration round-trip clean (upgrade → downgrade → upgrade). No CASCADE on any DROP statement.
- All existing pytest passes (currently ~100 + 1 xfail; xfail count drops to 0 since the tripwire test is deleted).
- Smoke test grows by ~9 truth-table cells + 1 INSERT assertion.
- mypy strict clean.
- check_setup 35/35.
- FN-AB-16 marked RESOLVED in CLAUDE.md with concrete cleanup notes.
- L10 and D6 integration tests rewritten to assert against real `tenant_module_access` data; cross-tenant isolation property verified; new L10b test added covering the empty-modules COALESCE path.
- BUILD_PLAN.md Step 3.4.5 entry added with status DONE.
- architecture.md table count ticks 10 → 11 (assuming an edit is warranted by the convention).

---

## Report (BEFORE proposing commit)

Five bundles per the convention:

1. **Code/migrations:** files created/modified/deleted with line counts; the migration revision; sample SQL output from the OR-clause policy verifying it has the right shape; sample query plan from the modules-via-subquery JOIN to confirm the index is used.
2. **CLAUDE.md updates:** Step 3.4.5 Completed bullet, schema state line update, FN-AB-16 marked RESOLVED, smoke count.
3. **BUILD_PLAN.md updates:** Step 3.4.5 entry added; scope-in/acceptance match what shipped.
4. **architecture.md updates:** table count tick + new table reference; or "no other changes."
5. **Prompt file:** confirmed in commit set.

Plus: pytest count delta (old vs new, with breakdown); mypy status; check_setup status; smoke test pre/post counts; alembic round-trip output.

Wait for explicit authorisation before staging or committing.

---

## End of prompt
