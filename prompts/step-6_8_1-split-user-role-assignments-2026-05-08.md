# Prompt — Step 6.8.1: Split `user_role_assignments` into two tables (DDL + Alembic migration + smoke test)

> Generated 2026-05-08. First of three steps under section 6.8 splitting `user_role_assignments` into `platform_user_role_assignments` (no RLS, platform-global) and `tenant_user_role_assignments` (RLS+FORCE, unconditional OR-branch policy). This step lands schema and data migration only. Application code (ORM, repos, router) follows in 6.8.2; the new endpoint lands in 6.8.3.
>
> **Local-only scope.** No cloud deploy. The bundle deploys to Cloud SQL only after 6.8.3 is green locally and all three steps land in a single push.
>
> Paste this entire block into a fresh Claude Code session to start Step 6.8.1.

---

## Caution-first posture

The operative word for steps 6.8.1, 6.8.2, and 6.8.3 is **caution**. Three reasons:

1. **The split touches the load-bearing identity layer.** RBAC isolation is the primary design constraint of this entire system per CLAUDE.md ("Cross-tenant data leak = disaster"). Any silent regression here is the worst kind of regression.
2. **The DDL frozen-state convention has not previously needed a v3 file.** This step introduces v3 of the RBAC DDL — novel within the project. Treat as precedent-setting.
3. **The downgrade fidelity matters more than usual.** If a follow-on step (6.8.2 or 6.8.3) needs to be redone, the rollback path through this migration must produce a functioning post-FN-AB-14 state. Byte-equivalent restoration of `4fd3aec6ae0c`'s policy text is non-negotiable.

Concretely, this means: **stop and surface anything that doesn't match the prompt's stated assumptions before writing code.** Section "Stop and ask if" enumerates concrete triggers; the disposition is "lean toward stopping" rather than "lean toward proceeding."

---

## Context: why this step exists

The current `user_role_assignments` table has a dual-FK XOR shape: a row is either PLATFORM-audience (`platform_user_id` NOT NULL, `tenant_id` NULL, `org_node_id` NULL) or TENANT-audience (`tenant_user_id` NOT NULL, `tenant_id` NOT NULL, `org_node_id` NOT NULL). It is the only table in the schema with that dual-FK XOR shape; every other table that needs an actor reference uses the Pattern (b) discriminator (single `*_by_user_id UUID` + `*_by_user_type actor_user_type_enum`).

The XOR shape forced the table's RLS policy into the IS-NULL-gated form (FN-AB-14, migration `4fd3aec6ae0c`). That gate was the only one of the 6 multi-tenant tables that diverged from the unconditional PLATFORM OR-branch shape used by the other 5. The gate has now surfaced two structural problems:

1. PLATFORM sessions (Super Admin, app.tenant_id empty) cannot see all role assignments in one query — only the 3 PLATFORM-audience rows; TENANT-side rows are hidden. This blocks the upcoming Step 6.8 RBAC resolver and any "list all assignments" Super Admin view.
2. AI-RBAC-06 cross-tenant injection (a row where `tenant_user.tenant_id` doesn't match the row's `tenant_id`) is enforced only at the application layer per the DDL's forward note. The user's principle is that RLS-grade protections must NOT depend on application correctness.

The user has decided: split the table into two, mirroring the existing `platform_users` / `tenant_users` Pattern 2 split (D-12). This eliminates the dual-FK XOR shape, makes cross-tenant injection structurally impossible (composite FKs to `(tenant_users.tenant_id, tenant_users.id)` and `(org_nodes.tenant_id, org_nodes.id)`), and aligns the URA shape with Pattern 2.

This step is CLAUDE_CODE. Local-only. No cloud deploy.

---

## Hard constraints (non-negotiable)

The split must conform 100% to two principles:

1. **RLS maintained across all tables.** No table loses `ENABLE ROW LEVEL SECURITY` or `FORCE ROW LEVEL SECURITY`. No role gets `BYPASSRLS`. RLS remains the bedrock of multi-tenant isolation.
2. **PLATFORM users see all data they have permission to see.** A platform user with `app.user_type = 'PLATFORM'` and `app.tenant_id` empty must be able to see every TENANT-side row across every tenant in a single query, on any multi-tenant table — including the new `tenant_user_role_assignments`.

Surface any tension between these and your proposed approach immediately.

---

## Pre-flight

1. Run `./scripts/check_setup.sh`. Expect 35/35.
2. `git log --oneline -5` — confirm Step 6.7 (`2fdc4bc9f4cb`) at HEAD or a later in-progress reference if more landed.
3. `uv run alembic heads` — note the current head revision (expected `2fdc4bc9f4cb` per CLAUDE.md "Current state"; surface immediately if different).
4. `uv run alembic current` — note current revision; should match `heads`.
5. Read `CLAUDE.md` fully. Focus on:
   - **D-13** — audit-actor patterns. The two new tables use Pattern (b) for `granted_by` / `revoked_by` (UUID + `actor_user_type_enum`).
   - **D-15** — `DB_SCHEMA` from environment. Migration body uses unqualified table names; `env.py` sets `search_path` inside the alembic transaction (per Step 3.0/3.4.5 precedent).
   - **D-21** — UUIDv7 default via `uuidv7()` PL/pgSQL function in shared utilities.
   - **D-27** — NULLIF wrapper convention on `current_setting('app.tenant_id', TRUE)`.
   - **D-29** — PLATFORM RLS visibility via the unconditional OR-branch (this step adds `tenant_user_role_assignments` to the set of 5 tables already using this form).
   - **FN-AB-14** — the IS-NULL-gated policy this step retires. Mark RESOLVED.
   - **Frozen-DDL convention** — DDLs are not edited per-migration. The v2 DDL stays as historical record; a new v3 DDL becomes the as-shipped baseline for the post-split shape.
   - **Workflow convention — Per-step commit bundling** (5 items: code/migrations, CLAUDE.md updates, BUILD_PLAN.md updates, architecture.md updates if shape changed, prompt file). All five must land in this step's commit. **No "we'll fix docs in a later step" deferrals.**
6. Read `db/raw_ddl/Ithina_postgres_SQL_DDL_rbac_v2.sql` fully. This is the source for what's being split. Note exact CHECK constraint patterns (`ck_user_role_assignments_revoked_consistency` is the canonical revoked-status-consistency shape; mirror it).
7. Read `migrations/versions/4fd3aec6ae0c_*.py` (FN-AB-14 IS-NULL-gated form). The downgrade in this step's migration must restore this shape **byte-equivalent** to what `4fd3aec6ae0c` produces.
8. Read `migrations/versions/21e2ad16303a_*.py` (Step 3.0 unconditional OR — the policy shape the new tenant table adopts) and `migrations/versions/cd2a02e452ae_*.py` (Step 3.4.5 — closest precedent for "new table + RLS + trigger + audit pattern").
9. Read `migrations/versions/e59f62d5037d_*.py` (NULLIF wrapper) so the downgrade restores the post-NULLIF form (don't reintroduce D-27).
10. Read `scripts/smoke_test.py` fully. Note specifically:
    - The 5/6-table truth-table block (originally `test_15_*` at Step 3.0; extended to 6 tables at Step 3.4.5). URA is one of those 6.
    - The PLATFORM-INSERT block (`test_16_*`). URA has an INSERT assertion in this block.
    - Items related to URA's PLATFORM-audience visibility (originally Step 1.5 items 11 and 12).
    - Composite-FK same-tenant integrity test (originally Step 1.5 item 7) — the assertion that an URA row referencing an org_node with a mismatched tenant_id is REJECTED.
    - Run `uv run python scripts/smoke_test.py` and capture the **current PASS count** before any work. Surface in the report.
11. Read `scripts/verify_cloud_schema.py` and locate any reference to `user_role_assignments` (search for the table name).
12. **Contradiction-surface check.** Before writing code, audit these four sources for drift relevant to this step. **Stop and surface any mismatch; do not silently work around.**

    (a) **DDL vs live state on policy text.** v2 DDL describes the original CREATE POLICY shape WITHOUT NULLIF and WITHOUT the PLATFORM OR-branch. The live policy on `user_role_assignments` has both, applied via migrations `e59f62d5037d` (NULLIF) and `4fd3aec6ae0c` (FN-AB-14 IS-NULL-gated form). When this step's downgrade restores the FN-AB-14 form, it must mirror the **live migration's output**, not the v2 DDL's original policy. **The DDL is the source of truth for table STRUCTURE; the migration chain is the source of truth for POLICY TEXT and any post-DDL amendments.**

    (b) **CLAUDE.md "Current state" table count.** CLAUDE.md states 11 application tables. Verify by counting in the live DB:

    ```sql
    SELECT count(*) FROM information_schema.tables
    WHERE table_schema = current_setting('search_path')::text  -- or the configured schema
      AND table_type = 'BASE TABLE'
      AND table_name NOT IN ('alembic_version');
    ```

    If the live count differs from CLAUDE.md, the doc has drifted — surface, do not silently proceed.

    (c) **FN-AB-14 status in CLAUDE.md.** Currently marked "RESOLVED at Step 2.2b" (the IS-NULL gate fix). This step closes it more thoroughly (the gate is gone entirely after the split). The CLAUDE.md update must amend the resolution note to reference this step's migration revision and method (split, not gate amendment).

    (d) **BUILD_PLAN.md Step 6.1 "Known follow-ups (RBAC)" sub-section.** References E4/E5 as future endpoints at `/api/v1/user-role-assignments`. That URL becomes `/api/v1/role-assignments` per the post-split design. Do not edit this in 6.8.1; the URL change lands with 6.8.3. But note its existence so it isn't a surprise later.

13. **Inbound FK verification.** Before writing the migration, confirm zero inbound FKs to `user_role_assignments`:

    ```sql
    SELECT conname, conrelid::regclass AS from_table
    FROM pg_constraint
    WHERE confrelid = 'user_role_assignments'::regclass
      AND contype = 'f';
    ```

    Expected: zero rows. If anything references `user_role_assignments`, `DROP TABLE` in the upgrade will fail unless the references are handled. **Surface immediately; do not auto-handle.**

14. **Composite UNIQUE pre-checks for the new composite FKs.** The new `tenant_user_role_assignments` declares two composite FKs:
    - `FOREIGN KEY (tenant_id, tenant_user_id) REFERENCES tenant_users (tenant_id, id)`
    - `FOREIGN KEY (tenant_id, org_node_id) REFERENCES org_nodes (tenant_id, id)`

    Each requires the referenced table to have a UNIQUE or PRIMARY KEY on the `(tenant_id, id)` pair. Verify both before writing the migration:

    ```sql
    -- For tenant_users: must have UNIQUE or PK on (tenant_id, id)
    SELECT conname, contype, pg_get_constraintdef(oid)
    FROM pg_constraint
    WHERE conrelid = 'tenant_users'::regclass
      AND contype IN ('u', 'p');

    -- For org_nodes: must have UNIQUE or PK on (tenant_id, id)
    SELECT conname, contype, pg_get_constraintdef(oid)
    FROM pg_constraint
    WHERE conrelid = 'org_nodes'::regclass
      AND contype IN ('u', 'p');
    ```

    For `org_nodes` per its DDL, `UNIQUE (tenant_id, id)` exists (referenced by the parent_id self-FK). For `tenant_users` per its DDL, only `PRIMARY KEY (id)` exists by default — verify whether the composite UNIQUE is present. **If `tenant_users` lacks a `UNIQUE (tenant_id, id)`, the migration must add it before declaring the composite FK.** Surface and propose the fix; do not silently add.

15. Read this prompt fully and confirm scope before writing code.

---

## Step ID and intent

**Step 6.8.1** — Schema split. Two new tables, one migration, smoke-test refresh, plus per-step bundled CLAUDE.md / BUILD_PLAN.md / architecture.md updates. No application code changes.

**Three concrete deliverables (code) plus four bundled documentation updates:**

Code:
1. **New DDL file** `db/raw_ddl/Ithina_postgres_SQL_DDL_rbac_v3.sql` — captures the as-shipped post-split shape. The two new tables plus the 3 unchanged RBAC tables (`permissions`, `roles`, `role_permissions`) copied verbatim from v2 so the file is self-contained. Per the frozen-DDL convention, v2 stays as historical record.

2. **Alembic migration** `<gen>_step_6_8_1_split_user_role_assignments.py` — `down_revision` is whatever `alembic heads` reports (expected `2fdc4bc9f4cb`). Reversible. Schema-agnostic body (unqualified names, env.py-set search_path). Operations on upgrade specified below; full restoration of FN-AB-14 form on downgrade.

3. **Smoke test refresh** — replace URA's row in the 6-table truth-table block with `tenant_user_role_assignments`; replace URA's PLATFORM-audience visibility tests with PLATFORM/TENANT visibility tests on the new tables; add a no-RLS structural assertion for `platform_user_role_assignments`; update PLATFORM-INSERT test_16; add audience-check trigger assertions; add composite-FK cross-tenant-user injection rejection assertion.

Documentation (per the per-step bundling convention):
4. **CLAUDE.md updates** (see "CLAUDE.md changes this step" section below).
5. **BUILD_PLAN.md updates** (see "BUILD_PLAN.md changes this step" section below).
6. **architecture.md updates** (see "architecture.md changes this step" section below).
7. **Prompt file** committed alongside per the convention.

This is a CLAUDE_CODE step. No application code, no auth, no ORM models. Pure schema migration mechanics with their bundled documentation.

---

## Source-of-truth specification

### File 1: `db/raw_ddl/Ithina_postgres_SQL_DDL_rbac_v3.sql` — NEW

New baseline DDL. Contains five table definitions in this order:

1. `permissions` — copy verbatim from v2 (no modification)
2. `roles` — copy verbatim from v2
3. `role_permissions` — copy verbatim from v2
4. `platform_user_role_assignments` — new (specified below)
5. `tenant_user_role_assignments` — new (specified below)

The shared enum types (`role_audience_enum`, `module_code_enum`, `resource_enum`, `action_enum`, `permission_scope_enum`, `role_status_enum`, `user_role_assignment_status_enum`) are copied from v2 unchanged. Per the frozen-DDL convention, the v2 file remains as historical record; do not edit it.

#### `platform_user_role_assignments` — schema

```sql
CREATE TABLE platform_user_role_assignments (
    id                       UUID                                NOT NULL DEFAULT uuidv7(),

    platform_user_id         UUID                                NOT NULL,
    role_id                  UUID                                NOT NULL,

    status                   user_role_assignment_status_enum    NOT NULL,

    granted_at               TIMESTAMPTZ                         NOT NULL DEFAULT NOW(),
    granted_by_user_id       UUID                                NULL,
    granted_by_user_type     actor_user_type_enum                NULL,
    revoked_at               TIMESTAMPTZ                         NULL,
    revoked_by_user_id       UUID                                NULL,
    revoked_by_user_type     actor_user_type_enum                NULL,

    updated_at               TIMESTAMPTZ                         NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_platform_user_role_assignments
        PRIMARY KEY (id),

    CONSTRAINT fk_platform_user_role_assignments_platform_user
        FOREIGN KEY (platform_user_id) REFERENCES platform_users (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,

    CONSTRAINT fk_platform_user_role_assignments_role
        FOREIGN KEY (role_id) REFERENCES roles (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,

    CONSTRAINT ck_platform_user_role_assignments_granted_by_actor_pair
        CHECK (
            (granted_by_user_id IS NULL AND granted_by_user_type IS NULL)
            OR
            (granted_by_user_id IS NOT NULL AND granted_by_user_type IS NOT NULL)
        ),

    CONSTRAINT ck_platform_user_role_assignments_revoked_by_actor_pair
        CHECK (
            (revoked_by_user_id IS NULL AND revoked_by_user_type IS NULL)
            OR
            (revoked_by_user_id IS NOT NULL AND revoked_by_user_type IS NOT NULL)
        ),

    -- Mirrors v2's revoked_consistency CHECK pattern exactly.
    -- user_role_assignment_status_enum has 2 values: 'ACTIVE', 'INACTIVE'.
    CONSTRAINT ck_platform_user_role_assignments_revoked_consistency
        CHECK (
            (status = 'INACTIVE'
                AND revoked_at IS NOT NULL
                AND revoked_by_user_id IS NOT NULL
                AND revoked_by_user_type IS NOT NULL)
            OR
            (status = 'ACTIVE'
                AND revoked_at IS NULL
                AND revoked_by_user_id IS NULL
                AND revoked_by_user_type IS NULL)
        )
);

-- Partial UNIQUE: a platform user cannot hold the same role twice (active).
CREATE UNIQUE INDEX uq_platform_user_role_assignments_active
    ON platform_user_role_assignments (platform_user_id, role_id)
    WHERE status = 'ACTIVE';

-- "What roles does this platform user have?"
CREATE INDEX ix_platform_user_role_assignments_platform_user
    ON platform_user_role_assignments (platform_user_id);

-- "Who has this role assigned?" (reverse-lookup)
CREATE INDEX ix_platform_user_role_assignments_role
    ON platform_user_role_assignments (role_id);

-- Active platform assignments for permission resolution at login.
CREATE INDEX ix_platform_user_role_assignments_platform_user_active
    ON platform_user_role_assignments (platform_user_id)
    WHERE status = 'ACTIVE';

-- updated_at trigger.
CREATE TRIGGER tg_platform_user_role_assignments_set_updated_at
    BEFORE UPDATE ON platform_user_role_assignments
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at_timestamp();

-- Audience-check trigger function and trigger.
-- CHECK constraints can't query other tables, so role.audience consistency
-- requires a row-level trigger. This is the structural-impossibility guard
-- that prevents inserting a TENANT-audience role into the platform table.
CREATE OR REPLACE FUNCTION enforce_platform_role_audience()
RETURNS TRIGGER AS $$
DECLARE
    v_audience role_audience_enum;
BEGIN
    SELECT audience INTO v_audience FROM roles WHERE id = NEW.role_id;
    IF v_audience IS DISTINCT FROM 'PLATFORM' THEN
        RAISE EXCEPTION
            'audience-check: platform_user_role_assignments requires PLATFORM-audience role; role % has audience %',
            NEW.role_id, v_audience;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER tg_platform_user_role_assignments_audience_check
    BEFORE INSERT OR UPDATE OF role_id ON platform_user_role_assignments
    FOR EACH ROW
    EXECUTE FUNCTION enforce_platform_role_audience();

-- No RLS. Platform-global table, same posture as platform_users.
-- Visibility is controlled at the role-grant level, not at the row level.
```

#### `tenant_user_role_assignments` — schema

```sql
CREATE TABLE tenant_user_role_assignments (
    id                       UUID                                NOT NULL DEFAULT uuidv7(),

    tenant_user_id           UUID                                NOT NULL,
    tenant_id                UUID                                NOT NULL,
    org_node_id              UUID                                NOT NULL,
    role_id                  UUID                                NOT NULL,

    status                   user_role_assignment_status_enum    NOT NULL,

    granted_at               TIMESTAMPTZ                         NOT NULL DEFAULT NOW(),
    granted_by_user_id       UUID                                NULL,
    granted_by_user_type     actor_user_type_enum                NULL,
    revoked_at               TIMESTAMPTZ                         NULL,
    revoked_by_user_id       UUID                                NULL,
    revoked_by_user_type     actor_user_type_enum                NULL,

    updated_at               TIMESTAMPTZ                         NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_tenant_user_role_assignments
        PRIMARY KEY (id),

    -- Composite FK to tenant_users — enforces tenant_id matches the user's tenant.
    -- This is the structural-impossibility guarantee for cross-tenant injection
    -- on the user side. AI-RBAC-06 closed at the schema level.
    CONSTRAINT fk_tenant_user_role_assignments_tenant_user_same_tenant
        FOREIGN KEY (tenant_id, tenant_user_id)
        REFERENCES tenant_users (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,

    -- Composite FK to org_nodes — enforces tenant_id matches the org_node's tenant.
    -- Structural-impossibility guarantee for cross-tenant injection on the
    -- org_node side.
    CONSTRAINT fk_tenant_user_role_assignments_org_node_same_tenant
        FOREIGN KEY (tenant_id, org_node_id)
        REFERENCES org_nodes (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,

    CONSTRAINT fk_tenant_user_role_assignments_role
        FOREIGN KEY (role_id) REFERENCES roles (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,

    CONSTRAINT fk_tenant_user_role_assignments_tenant
        FOREIGN KEY (tenant_id) REFERENCES tenants (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,

    CONSTRAINT ck_tenant_user_role_assignments_granted_by_actor_pair
        CHECK (
            (granted_by_user_id IS NULL AND granted_by_user_type IS NULL)
            OR
            (granted_by_user_id IS NOT NULL AND granted_by_user_type IS NOT NULL)
        ),

    CONSTRAINT ck_tenant_user_role_assignments_revoked_by_actor_pair
        CHECK (
            (revoked_by_user_id IS NULL AND revoked_by_user_type IS NULL)
            OR
            (revoked_by_user_id IS NOT NULL AND revoked_by_user_type IS NOT NULL)
        ),

    CONSTRAINT ck_tenant_user_role_assignments_revoked_consistency
        CHECK (
            (status = 'INACTIVE'
                AND revoked_at IS NOT NULL
                AND revoked_by_user_id IS NOT NULL
                AND revoked_by_user_type IS NOT NULL)
            OR
            (status = 'ACTIVE'
                AND revoked_at IS NULL
                AND revoked_by_user_id IS NULL
                AND revoked_by_user_type IS NULL)
        )
);

-- Partial UNIQUE: a tenant user cannot hold the same role at the same
-- org_node twice (active). Mirrors v2's uq_user_role_assignments_tenant_active_unique.
CREATE UNIQUE INDEX uq_tenant_user_role_assignments_active
    ON tenant_user_role_assignments (tenant_user_id, role_id, org_node_id)
    WHERE status = 'ACTIVE';

-- Tenant-scoped queries (RLS path).
CREATE INDEX ix_tenant_user_role_assignments_tenant
    ON tenant_user_role_assignments (tenant_id);

-- "What roles does this tenant user have?"
CREATE INDEX ix_tenant_user_role_assignments_tenant_user
    ON tenant_user_role_assignments (tenant_user_id);

-- "Who has this role assigned at this org_node?"
CREATE INDEX ix_tenant_user_role_assignments_role_org_node
    ON tenant_user_role_assignments (role_id, org_node_id);

-- Active tenant assignments for permission resolution at login.
CREATE INDEX ix_tenant_user_role_assignments_tenant_user_active
    ON tenant_user_role_assignments (tenant_user_id)
    WHERE status = 'ACTIVE';

-- updated_at trigger.
CREATE TRIGGER tg_tenant_user_role_assignments_set_updated_at
    BEFORE UPDATE ON tenant_user_role_assignments
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at_timestamp();

-- Audience-check trigger.
CREATE OR REPLACE FUNCTION enforce_tenant_role_audience()
RETURNS TRIGGER AS $$
DECLARE
    v_audience role_audience_enum;
BEGIN
    SELECT audience INTO v_audience FROM roles WHERE id = NEW.role_id;
    IF v_audience IS DISTINCT FROM 'TENANT' THEN
        RAISE EXCEPTION
            'audience-check: tenant_user_role_assignments requires TENANT-audience role; role % has audience %',
            NEW.role_id, v_audience;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER tg_tenant_user_role_assignments_audience_check
    BEFORE INSERT OR UPDATE OF role_id ON tenant_user_role_assignments
    FOR EACH ROW
    EXECUTE FUNCTION enforce_tenant_role_audience();

-- RLS — unconditional OR-branch (matches the other 5 multi-tenant tables per D-29).
ALTER TABLE tenant_user_role_assignments ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_user_role_assignments FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_user_role_assignments_tenant_isolation
    ON tenant_user_role_assignments
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

---

### File 2: Alembic migration — NEW

Filename: `migrations/versions/<auto-generated>_step_6_8_1_split_user_role_assignments.py`. Use `uv run alembic revision -m "Step 6.8.1: split user_role_assignments"` to generate the boilerplate; expand the body manually (do not use `--autogenerate`).

**`down_revision`** = current head from `alembic heads` (expected `2fdc4bc9f4cb`).

**Body convention (per Step 3.0/3.4.5 precedent):**
- `op.execute()` with raw SQL strings. Do not use `op.create_table()`, `op.create_index()`, or other Alembic ops.
- **Unqualified table names throughout.** No hardcoded `core.` literal anywhere. The migration is schema-agnostic; `env.py` sets `search_path` inside the alembic transaction.
- No CASCADE on DROP statements in downgrade. Use explicit ordered drops (mirror Step 3.4.5's discipline).

#### `upgrade()` operations in order:

1. CREATE `platform_user_role_assignments` table (full DDL from File 1).
2. CREATE `tenant_user_role_assignments` table (full DDL from File 1, including composite FKs).
3. CREATE all indexes for `platform_user_role_assignments` (4 indexes per File 1).
4. CREATE all indexes for `tenant_user_role_assignments` (5 indexes per File 1).
5. CREATE FUNCTION `enforce_platform_role_audience()` and the BEFORE INSERT OR UPDATE OF role_id trigger.
6. CREATE FUNCTION `enforce_tenant_role_audience()` and the BEFORE INSERT OR UPDATE OF role_id trigger.
7. CREATE TRIGGER `tg_*_set_updated_at` on each new table (uses `set_updated_at_timestamp()` from shared utilities; the actual function name — not `set_updated_at_now()`).
8. ENABLE + FORCE ROW LEVEL SECURITY on `tenant_user_role_assignments`; CREATE POLICY with the unconditional OR-branch.
9. **Pre-flight count assertion (DO block):**

```sql
DO $$
DECLARE
    n_platform INT;
    n_tenant INT;
    n_total INT;
BEGIN
    SELECT COUNT(*) FILTER (WHERE platform_user_id IS NOT NULL),
           COUNT(*) FILTER (WHERE tenant_user_id IS NOT NULL),
           COUNT(*)
      INTO n_platform, n_tenant, n_total
      FROM user_role_assignments;
    IF n_platform + n_tenant != n_total THEN
        RAISE EXCEPTION
            'split-migration: URA XOR invariant violated pre-migration (platform=%, tenant=%, total=%)',
            n_platform, n_tenant, n_total;
    END IF;
    RAISE NOTICE 'split-migration: pre-flight OK (platform=%, tenant=%, total=%)',
                 n_platform, n_tenant, n_total;
END $$;
```

10. **Copy PLATFORM-audience rows.** Note: the new table has no `created_at` column distinct from `granted_at` — the existing URA also has no separate `created_at`, so no synthesis is needed. Map columns 1:1.

```sql
INSERT INTO platform_user_role_assignments (
    id, platform_user_id, role_id, status,
    granted_at, granted_by_user_id, granted_by_user_type,
    revoked_at, revoked_by_user_id, revoked_by_user_type,
    updated_at
)
SELECT
    id, platform_user_id, role_id, status,
    granted_at, granted_by_user_id, granted_by_user_type,
    revoked_at, revoked_by_user_id, revoked_by_user_type,
    updated_at
FROM user_role_assignments
WHERE platform_user_id IS NOT NULL;
```

11. **Copy TENANT-audience rows.** Composite FKs validate each row at INSERT time; the migration is the structural integrity gate. If any row fails (denormalised tenant_id mismatches user/org_node), the migration aborts and the user surfaces the offending row(s).

```sql
INSERT INTO tenant_user_role_assignments (
    id, tenant_user_id, tenant_id, org_node_id, role_id, status,
    granted_at, granted_by_user_id, granted_by_user_type,
    revoked_at, revoked_by_user_id, revoked_by_user_type,
    updated_at
)
SELECT
    id, tenant_user_id, tenant_id, org_node_id, role_id, status,
    granted_at, granted_by_user_id, granted_by_user_type,
    revoked_at, revoked_by_user_id, revoked_by_user_type,
    updated_at
FROM user_role_assignments
WHERE tenant_user_id IS NOT NULL;
```

**Note on audience-check triggers during data copy:** the audience-check triggers (steps 5-6 above) fire during the copy. Existing URA rows are already audience-consistent (the seed loader and any prior writes respected the XOR), so this should pass cleanly. If any row has a role whose audience doesn't match the user-side column, the trigger raises and the migration aborts — that's a real data-integrity find that needs manual investigation (see Stop and ask).

12. **Post-copy count assertion (DO block):**

```sql
DO $$
DECLARE
    n_old_platform INT;
    n_old_tenant INT;
    n_new_platform INT;
    n_new_tenant INT;
BEGIN
    SELECT COUNT(*) FILTER (WHERE platform_user_id IS NOT NULL),
           COUNT(*) FILTER (WHERE tenant_user_id IS NOT NULL)
      INTO n_old_platform, n_old_tenant
      FROM user_role_assignments;
    SELECT COUNT(*) INTO n_new_platform FROM platform_user_role_assignments;
    SELECT COUNT(*) INTO n_new_tenant FROM tenant_user_role_assignments;
    IF n_old_platform != n_new_platform THEN
        RAISE EXCEPTION 'split-migration: PLATFORM count mismatch (old=%, new=%)',
                        n_old_platform, n_new_platform;
    END IF;
    IF n_old_tenant != n_new_tenant THEN
        RAISE EXCEPTION 'split-migration: TENANT count mismatch (old=%, new=%)',
                        n_old_tenant, n_new_tenant;
    END IF;
    RAISE NOTICE 'split-migration: post-copy counts OK (platform=%, tenant=%)',
                 n_new_platform, n_new_tenant;
END $$;
```

13. **DROP TABLE `user_role_assignments`.** Cascades cleanly (zero inbound FKs verified empirically). The `user_role_assignment_status_enum` type is reused by both new tables and stays.

#### `downgrade()` operations:

The downgrade restores the FN-AB-14 form **byte-equivalent** to what `4fd3aec6ae0c`'s upgrade produces. Read `4fd3aec6ae0c`'s upgrade SQL carefully and mirror it. Specifically the policy text:

```sql
CREATE POLICY user_role_assignments_tenant_isolation
    ON user_role_assignments
    FOR ALL
    USING (
        tenant_id = NULLIF(current_setting('app.tenant_id', TRUE), '')::uuid
        OR (
            tenant_id IS NULL
            AND current_setting('app.user_type', TRUE) = 'PLATFORM'
        )
    )
    WITH CHECK (
        tenant_id = NULLIF(current_setting('app.tenant_id', TRUE), '')::uuid
        OR (
            tenant_id IS NULL
            AND current_setting('app.user_type', TRUE) = 'PLATFORM'
        )
    );
```

Operation sequence (reverse-creation order; no CASCADE):

1. CREATE TABLE `user_role_assignments` with the full schema from `rbac_v2.sql` (all columns, all CHECK constraints, all FK constraints — verbatim from v2).
2. CREATE all indexes from v2 (8 indexes total — see v2 DDL).
3. CREATE TRIGGER `tg_user_role_assignments_set_updated_at`.
4. ENABLE + FORCE ROW LEVEL SECURITY; CREATE POLICY with the IS-NULL-gated form (verbatim from `4fd3aec6ae0c`).
5. INSERT PLATFORM-audience rows back from `platform_user_role_assignments` (NULL for `tenant_user_id`, `tenant_id`, `org_node_id`).
6. INSERT TENANT-audience rows back from `tenant_user_role_assignments` (NULL for `platform_user_id`).
7. DROP TRIGGER `tg_*_audience_check` on both new tables.
8. DROP FUNCTION `enforce_platform_role_audience()`.
9. DROP FUNCTION `enforce_tenant_role_audience()`.
10. DROP TRIGGER `tg_*_set_updated_at` on both new tables.
11. DROP POLICY on `tenant_user_role_assignments`.
12. ALTER TABLE `tenant_user_role_assignments` DISABLE ROW LEVEL SECURITY.
13. DROP all indexes on both new tables (or rely on DROP TABLE cascading the indexes — explicit drops match Step 3.4.5's discipline).
14. DROP TABLE `tenant_user_role_assignments`.
15. DROP TABLE `platform_user_role_assignments`.

---

### File 3: `scripts/smoke_test.py` — MODIFY

The smoke test currently asserts on URA-specific behaviours that need redirecting to the new tables.

#### Items to update (read the file first to confirm current line numbers and exact wording):

1. **6-table truth-table block** (`test_15_*` per Step 3.0 → extended at Step 3.4.5). URA's row in this block must be replaced with `tenant_user_role_assignments`. Same 9-row truth-table assertions; the new table uses the unconditional OR-branch identically to the other 5.

2. **PLATFORM-INSERT block** (`test_16_*`). URA's INSERT assertion must be replaced with one for `tenant_user_role_assignments` (PLATFORM impersonating tenant A inserts a row with tenant_id=A; expect success).

3. **PLATFORM-audience visibility tests** (originally Step 1.5 items 11/12). The original assertions checked PLATFORM-audience URA rows visible/invisible based on session GUC. Post-split:
   - Replace with: `platform_user_role_assignments` is platform-global with no RLS — every session sees every row. Single assertion.
   - The `tenant_user_role_assignments` PLATFORM visibility is already covered by item 1 above (the truth-table block).

4. **Composite-FK same-tenant integrity test** (originally Step 1.5 item 7). The assertion that a URA row with a mismatched org_node tenant_id is REJECTED moves to `tenant_user_role_assignments`. The new table's composite FKs to BOTH `tenant_users(tenant_id, id)` AND `org_nodes(tenant_id, id)` mean **two** assertions are now possible:
   - INSERT row with `tenant_user.tenant_id != row.tenant_id` is REJECTED (new — was previously app-layer only).
   - INSERT row with `org_node.tenant_id != row.tenant_id` is REJECTED (existed before, moved to new table).

5. **Audience-check trigger assertions** (NEW). Two new structural integrity tests:
   - INSERT into `platform_user_role_assignments` referencing a TENANT-audience role is REJECTED by trigger.
   - INSERT into `tenant_user_role_assignments` referencing a PLATFORM-audience role is REJECTED by trigger.

6. **No-RLS structural assertion for `platform_user_role_assignments`** (NEW). Single check: `pg_class.relrowsecurity = false` for this table. Confirms platform-global posture.

#### Smoke test count expectation:

The current PASS count (record this in Pre-flight item 10) should change by approximately +2 (the 2 audience-check tests are new; the cross-tenant tenant_user FK rejection is also new; the no-RLS check is new; balanced against the visibility test condensation from 2 to 1 for `platform_user_role_assignments`). Exact delta depends on current item structure; report the actual number.

---

### File 4: `scripts/verify_cloud_schema.py` — MODIFY (likely)

Locate any reference to `user_role_assignments` (likely a comment around line 14 per Claude Code's earlier survey, but verify). Update to reference the two new tables.

If the script's `EXPECTED_TABLES` or similar structural assertions list table names, update accordingly.

---

## CLAUDE.md changes this step (per the per-step bundling convention)

Six touchpoints. The full content of each edit is up to Claude Code's discretion, guided by what changed; the bullet list below captures the sections that must be updated:

1. **Schema state line** under "Current state" section. Today reads "11 application tables, ... 6/6 multi-tenant tables (tenants, tenant_users, org_nodes, stores, user_role_assignments, tenant_module_access) with RLS + FORCE + isolation policy." Update to:
   - Application table count: 11 → 12 (URA replaced by 2 new tables).
   - Multi-tenant table list: replace `user_role_assignments` with `tenant_user_role_assignments`. Note that `platform_user_role_assignments` is NOT in this list (no RLS).
   - Reference to the new migration revision and confirm `tenant_user_role_assignments` uses the unconditional OR-branch (not the IS-NULL-gated form).

2. **Smoke test state.** Update the pre-step PASS count to the post-step PASS count.

3. **D-29 — PLATFORM RLS visibility.** Today documents two policy shapes: unconditional OR (5 tables) and IS-NULL-gated OR (1 table — URA). Amend to remove the IS-NULL-gated bullet entirely; the only remaining shape is unconditional OR across all 6 multi-tenant tables. Note that the IS-NULL-gated form was retired by Step 6.8.1 via the table split (not by amending the policy).

4. **FN-AB-14.** Today marked "RESOLVED at Step 2.2b" with reference to migration `4fd3aec6ae0c`. Amend the resolution note to:
   - Note the deeper resolution at Step 6.8.1: the IS-NULL gate is gone entirely (the table that had it no longer exists).
   - Reference this step's migration revision.
   - The Step 2.2b resolution stays in the historical record (it was the right fix at the time given the schema constraints).

5. **New "Completed" bullet for Step 6.8.1.** Concise summary mirroring the format of recent Completed bullets (Step 3.4.5, Step 6.7). Include: the two new tables, the composite FKs as the structural-impossibility guarantee for AI-RBAC-06, the audience-check triggers, the migration revision, smoke test count delta.

6. **New decision entry — D-XX (next available number) — Mixed-audience tables get split.** Document the principle that drove this step:

   - **What.** Tables that mix PLATFORM-audience rows (no tenant_id) with TENANT-audience rows (with tenant_id) must be split into two physical tables (one platform-global with no RLS, one multi-tenant with the standard RLS shape) rather than unified with a nullable tenant_id and an IS-NULL-gated policy.
   - **Why.** Cross-tenant injection is structurally impossible by table shape rather than runtime-prevented by policy or trigger. RLS becomes uniform across all multi-tenant tables. The dual-FK XOR shape goes away. Aligns with Pattern 2 (D-12) which already established this for `platform_users` / `tenant_users`.
   - **Reconsider if.** A future requirement makes a unified table genuinely cheaper despite the cost of the IS-NULL gate or equivalent guard. This would have to overcome the precedent set by user_role_assignments (Step 6.8.1) and audit_logs (Step 6.2).
   - **Forward dependency.** `audit_logs` at Step 6.2 has the same nullable-tenant_id shape (tenant_id NULL for GLOBAL events). It must split into `platform_audit_logs` and `tenant_audit_logs` per this principle. Update Step 6.2's prompt at the time it's drafted.

---

## BUILD_PLAN.md changes this step

1. **Section 6.8 introduction.** A short paragraph explaining the section: three sub-steps (6.8.1, 6.8.2, 6.8.3) splitting `user_role_assignments` into two tables to retire FN-AB-14's IS-NULL gate and make AI-RBAC-06 cross-tenant injection structurally impossible. Cross-reference D-XX (the new decision entry from CLAUDE.md).

2. **Step 6.8.1 entry.** Status DONE. Standard scope-in / scope-out / acceptance / coordination structure mirroring Step 3.4.5 / Step 6.7's entries. Include:
   - Scope: DDL v3 file, migration with data copy, smoke test refresh, this step's documentation bundle.
   - Acceptance: migration round-trip clean; data counts match; new RLS policy form; all smoke assertions PASS; pytest baseline preserved (with known failures localised to URA-stub references that 6.8.2 fixes).
   - Coordination: none (local-only; cloud deploy blocks on 6.8.3).

3. **Step 6.8.2 placeholder entry.** Status TODO. Scope sketch (ORM models, repositories, schemas, seed loader update, RolesRepo._user_count_subquery rewrite). Mark "Blocked by Step 6.8.1."

4. **Step 6.8.3 placeholder entry.** Status TODO. Scope sketch (router, endpoint, integration tests, docs/endpoints/role-assignments.md, OpenAPI regen). Mark "Blocked by Step 6.8.2."

---

## architecture.md changes this step

1. **Schema and storage section — table inventory.** The table count line moves from 11 to 12 (or whatever count is current minus 1 plus 2). Replace the `user_role_assignments` row with two rows for the new tables. The "Tenant-scoped?" / "RLS?" columns: `platform_user_role_assignments` = No / No; `tenant_user_role_assignments` = Yes / Yes.

2. **Multi-tenancy and data isolation section.** Any prose that describes the IS-NULL-gated policy form on `user_role_assignments` must be updated. The unconditional-OR shape now applies across all 6 multi-tenant tables uniformly.

3. **AI-RBAC-06 cross-tenant injection paragraph (if present).** Update to note that on the new `tenant_user_role_assignments` table, cross-tenant injection is structurally prevented by composite FKs to `(tenant_users.tenant_id, tenant_users.id)` and `(org_nodes.tenant_id, org_nodes.id)`. The forward note about a future DB trigger for AI-RBAC-06 is retired for this table (replaced by structural FK enforcement).

If any of the above sections doesn't exist or doesn't reference URA, the corresponding update is "no change."

---

## Verification harness (run all six; all must be green)

```bash
# 1. Migration applies cleanly on a fresh local DB
uv run alembic upgrade head
# Should complete without errors. NOTICE messages from pre-flight + post-copy
# count assertions confirm the data copy worked.

# 2. Round-trip clean
uv run alembic downgrade -1
# Restores user_role_assignments table with FN-AB-14 IS-NULL-gated policy.

uv run alembic upgrade head
# Re-applies the split. Counts identical to first run.

# 3. Schema verification — new tables exist, old gone
psql "$DATABASE_URL" -c "\dt $DB_SCHEMA.*role_assignments*"
# Expected: platform_user_role_assignments, tenant_user_role_assignments.
# user_role_assignments should NOT appear.

# 4. RLS state
psql "$DATABASE_URL" -c "
SELECT n.nspname, c.relname,
       c.relrowsecurity AS rls,
       c.relforcerowsecurity AS force
FROM pg_class c JOIN pg_namespace n ON c.relnamespace = n.oid
WHERE c.relname IN ('platform_user_role_assignments', 'tenant_user_role_assignments')
ORDER BY c.relname;"
# Expected: platform_user_role_assignments rls=f force=f;
#           tenant_user_role_assignments rls=t force=t.

# 5. Smoke test — full run, all PASS
uv run python scripts/smoke_test.py
# Expected: pre-step count + delta from File 3 changes. All PASS.

# 6. pytest baseline regression
uv run pytest -v
# Expected: pre-step count, with N tests now FAILING because they reference
# user_role_assignments via the lightweight stub or _user_count_subquery.
# These failures are EXPECTED for Step 6.8.1 — they get fixed in Step 6.8.2.
# Surface the count of expected failures + the test names. They should all
# trace to the dead URA reference, not to genuine regressions.
```

If the pytest count includes failures for reasons OTHER than the dead URA reference, surface immediately — that's an unexpected regression.

---

## Regression risk surface introduced by this step

1. **Codebase will not be runnable post-migration.** The `_lightweight_stubs.UserRoleAssignment` class and `RolesRepo._user_count_subquery` reference the dropped table. This is intentional for Step 6.8.1; Step 6.8.2 fixes it. Document in the report.

2. **Migration round-trip with seed data.** The downgrade INSERTs rows back from the new tables into the recreated old table. Verify the round-trip preserves row counts AND row identity (same UUIDs). The migration's NOTICE messages cover counts; manual spot-check on UUIDs after a downgrade-upgrade-downgrade cycle is worth doing during testing.

3. **Trigger function namespace.** `enforce_platform_role_audience()` and `enforce_tenant_role_audience()` are new function names. Verify no name collision with anything in `core` schema (unlikely but possible). Run `\df core.enforce_*` before the migration runs.

4. **Composite FK rejection mid-copy.** If the dev seed has any URA row where `tenant_users.tenant_id` doesn't match the URA's denormalised `tenant_id`, the copy will fail and the migration aborts. The dev seed has been verified consistent at multiple prior steps, but a row-level integrity check is worth surfacing before the copy fires (the pre-flight count assertion catches XOR violations but not this).

5. **DDL/migration consistency.** The v3 DDL describes the *post-migration* shape. After the migration lands, the v3 file matches the live schema. Frozen-DDL convention applies going forward (no edits to v3 per future migrations).

6. **Smoke test changes.** The smoke test must run cleanly against both pre-migration and post-migration states.
   - **Pre-migration** (i.e., before this step's migration): the new assertions (audience-check, composite-FK on tenant_user, no-RLS on platform table) FAIL because the new tables don't exist. The replaced URA assertions still pass.
   - **Post-migration**: all new assertions pass; URA-specific assertions are gone; truth-table covers the new tenant table.
   - Verify by stashing the migration changes, running the updated smoke test (some will fail because the schema isn't there yet), unstashing, re-running (all pass).

7. **Pytest baseline.** Pre-step pytest count is captured in Pre-flight item 10's adjacent step. Post-migration, expect failures localized to URA-stub-referencing tests. These are NOT regressions in the sense of behavioural drift; they're known-failures that 6.8.2 resolves. Surface the exact failing test names so 6.8.2's prompt can target them precisely.

8. **CLAUDE.md / BUILD_PLAN.md / architecture.md drift if updates are skipped.** Per the per-step bundling convention, this step's commit must include all four documentation surfaces. Skipping any creates documentation drift that downstream steps inherit. Surface immediately if any of the documented updates feels misaligned with the actual change shape.

---

## Scope out

- **ORM models** (`PlatformUserRoleAssignment`, `TenantUserRoleAssignment`) — Step 6.8.2 territory.
- **Repositories** (new `RoleAssignmentsRepo`, updates to `RolesRepo._user_count_subquery`) — Step 6.8.2.
- **Pydantic schemas** — Step 6.8.2.
- **Seed loader updates** — Step 6.8.2.
- **`/role-assignments` router and endpoint** — Step 6.8.3.
- **docs/endpoints/role-assignments.md** — Step 6.8.3.
- **docs/endpoints/openapi.json regeneration** — Step 6.8.3.
- **docs/endpoints/rbac.md `user_count` description update** — Step 6.8.3 (when the implementation actually changes).
- **BUILD_PLAN.md Step 6.1 "Known follow-ups (RBAC)" E4/E5 URL update** — Step 6.8.3 (URL changes when the endpoint lands).
- **Cloud SQL migration.** Local-only this step. Cloud deploy happens after 6.8.3.
- **Audit_logs (Step 6.2) precedent.** This step establishes the split pattern via the new D-XX entry; no work on audit_logs in this step.

---

## Stop and ask if

1. **`alembic heads` reports something other than `2fdc4bc9f4cb`.** Surface the actual head; we need to confirm whether an unexpected migration has landed since the last documented Step 6.7 state. The `down_revision` for this step's migration is whatever the live head is.
2. **Pre-flight contradiction-surface check item 12 finds drift.** Any of (a) DDL vs live state, (b) CLAUDE.md table count vs live count, (c) FN-AB-14 status, (d) BUILD_PLAN E4/E5 URL — surface, do not silently work around.
3. **Pre-flight item 13 finds inbound FKs to `user_role_assignments`.** Surface the FK list. The migration cannot DROP TABLE without handling these.
4. **Pre-flight item 14 finds `tenant_users` lacks `UNIQUE (tenant_id, id)`.** Surface; the migration must add the UNIQUE before declaring the composite FK. Propose the fix; do not silently add.
5. **Pre-flight count assertion fails.** The XOR invariant has been violated by some unexpected row shape (a row with both `platform_user_id` and `tenant_user_id` set, or both NULL). Surface the row(s) so we can diagnose. Don't auto-fix.
6. **Post-copy count assertion fails.** The composite FK rejected one or more rows during the TENANT-side copy. Surface the rejected row's data: which row, which column mismatch, which tenant. We'll decide whether to fix the seed data, write a separate cleanup migration, or amend this migration's data-copy logic.
7. **Audience-check trigger fires during data copy.** A URA row references a role whose audience doesn't match the user-side column (e.g., a tenant-side row with a PLATFORM-audience role). This is a real data-integrity find. Surface the row; manual decision needed.
8. **A downgrade-then-upgrade cycle produces different state from the first upgrade.** The migration is non-deterministic in some way. Surface the diff (counts, schema, policy text) and we'll diagnose.
9. **The smoke test's pre-step count differs from what CLAUDE.md says** (~74 PASS at Step 3.4.5, plus subsequent additions from steps 5.x and 6.x). Surface the actual current count and where it diverges.
10. **The pytest count after this migration shows failures in tests that DON'T reference URA.** That's an unexpected regression beyond the known stub-reference failures. Surface the test names.
11. **`role_audience_enum` doesn't resolve in the trigger function declaration.** Check that the enum is in the search_path-resolvable schema. If not, the trigger function will fail to compile.
12. **A function named `enforce_platform_role_audience` or `enforce_tenant_role_audience` already exists in the schema.** Surface; we'll either rename ours or investigate why it exists.
13. **The CLAUDE.md / BUILD_PLAN.md / architecture.md changes feel like they should be deferred to a later step.** They should not be. Per the per-step bundling convention, this step's commit must include them. If you find yourself wanting to defer, that's a signal to stop and surface the conflict.

---

## Acceptance criteria

- New file `db/raw_ddl/Ithina_postgres_SQL_DDL_rbac_v3.sql` created with both new tables plus the 3 unchanged RBAC tables copied verbatim from v2.
- v2 DDL file unchanged (frozen-DDL convention).
- New Alembic migration created with `down_revision` matching live `alembic heads`.
- Migration body uses `op.execute()` with raw SQL (not `op.create_table()`).
- Migration body has zero hardcoded schema literals (no `core.` anywhere).
- `alembic upgrade head` runs cleanly. Pre-flight + post-copy NOTICE messages confirm data integrity.
- Schema post-upgrade: 12 application tables (was 11; +2 new, -1 old, net +1); RLS+FORCE on `tenant_user_role_assignments`; no RLS on `platform_user_role_assignments`. (Note: the 12-table count assumes `audit_logs` has not yet shipped.)
- Round-trip clean: `alembic upgrade head` → `alembic downgrade -1` → `alembic upgrade head`. Final state byte-equivalent to first upgrade (same tables, same policy text, same trigger definitions, same row counts).
- `tenant_user_role_assignments` policy text matches the unconditional OR-branch shape (mirror Step 3.0's pattern); `4fd3aec6ae0c`-equivalent IS-NULL-gated form is restored on downgrade.
- Smoke test passes. Post-migration count = pre-step count + delta from File 3 changes (~+2 to +4; report exact number).
- mypy strict clean across `src/admin_backend` (no application code touched in this step but mypy must still pass cleanly — the lightweight stub for `UserRoleAssignment` will start showing failures because its referenced table is gone; that's expected and addressed in 6.8.2).
- `./scripts/check_setup.sh` 35/35.
- CLAUDE.md updated per the "CLAUDE.md changes this step" section.
- BUILD_PLAN.md updated per the "BUILD_PLAN.md changes this step" section.
- architecture.md updated per the "architecture.md changes this step" section (or "no change" recorded if a section turns out to be unaffected).
- pytest count documented (pre-step → post-step), with the failing tests enumerated and confirmed to all be URA-stub-related (sets up 6.8.2's scope precisely).

---

## Report (BEFORE proposing commit)

Five bundles per the workflow convention:

1. **Code/migrations/tests:**
   - `db/raw_ddl/Ithina_postgres_SQL_DDL_rbac_v3.sql` line count.
   - Migration file revision id and line count; key SQL excerpts (the two new CREATE POLICY statements, the two trigger function bodies, the count-assertion DO blocks).
   - `scripts/smoke_test.py` diff stat (lines added/removed); pre-step PASS count; post-step PASS count.
   - `scripts/verify_cloud_schema.py` diff stat.
2. **CLAUDE.md updates:** which sections were touched (schema state line, smoke test count, D-29 amendment, FN-AB-14 amendment, new D-XX entry, new Completed bullet); diff stat.
3. **BUILD_PLAN.md updates:** Section 6.8 intro paragraph; Step 6.8.1 entry status DONE; Step 6.8.2 and 6.8.3 placeholder entries TODO.
4. **architecture.md updates:** which sections were touched; "no change" if a planned section turned out to not need editing.
5. **Prompt file:** `prompts/step-6_8_1-split-user-role-assignments-2026-05-08.md` confirmed in commit set.

Plus, in the report (not in the commit):
- Output of `alembic heads` before and after.
- Output of pre-flight items 12-14 (contradiction-surface check, inbound FK check, composite UNIQUE check) — explicit confirmation of clean or surfacing of any drift.
- NOTICE messages from the migration's count-assertion DO blocks.
- Output of the schema verification queries (table list, RLS state).
- Round-trip verification output.
- Smoke test output (the changed assertions specifically).
- pytest count delta + list of expected-failing tests (these define 6.8.2's targeted-fix surface).
- mypy status.
- check_setup status.
- Any deviations from this prompt's procedure and why.
- Any incidental findings encountered during reading (don't fix; record).

Wait for explicit authorisation before staging or committing.

---

## After completing

When operator authorises (after reviewing the report), propose a Pattern A commit per CLAUDE.md "After completing a task":

```
git status
git add -A
git commit -m "Step 6.8.1: split user_role_assignments DDL + migration + smoke test

- New db/raw_ddl/Ithina_postgres_SQL_DDL_rbac_v3.sql: post-split baseline (platform_user_role_assignments no-RLS; tenant_user_role_assignments RLS+FORCE with unconditional OR-branch policy per D-29). v2 DDL stays as historical record per frozen-DDL convention.
- New Alembic migration <revision> (down_revision <prior_head>): split URA into two tables; composite FKs on tenant_user_role_assignments to (tenant_users.tenant_id, tenant_users.id) and (org_nodes.tenant_id, org_nodes.id) for structural cross-tenant injection prevention; audience-check triggers on both new tables; data copy with pre-flight + post-copy count assertions; reversible (downgrade restores FN-AB-14 IS-NULL-gated form byte-equivalent).
- scripts/smoke_test.py: replace URA-specific assertions; truth-table block now covers tenant_user_role_assignments; new audience-check trigger assertions; new composite-FK cross-tenant-user assertion; no-RLS structural assertion for platform_user_role_assignments. Pre-step <X> PASS -> post-step <Y> PASS.
- scripts/verify_cloud_schema.py: <description of changes>.
- CLAUDE.md: schema state line (11 -> 12 tables; URA replaced by 2); smoke test count update; D-29 amended (IS-NULL-gated form retired); FN-AB-14 deepened RESOLVED note; new D-XX (mixed-audience tables get split); new Completed bullet.
- BUILD_PLAN.md: Section 6.8 intro; Step 6.8.1 status TODO -> DONE; Step 6.8.2 and 6.8.3 placeholder entries TODO.
- architecture.md: schema and storage table inventory; multi-tenancy section policy form prose; AI-RBAC-06 paragraph if present.
"
```

Substitute actual revision IDs and PASS counts. Ask operator: "Run? yes / no / edit message".

After commit lands: Step 6.8.2 (ORM models, repos, schemas, seed loader) is unblocked. Do not auto-chain — wait for operator direction.

---

## End of prompt
