# DDL Stress Test Report

> Step 1.3 deliverable. Read-only review of all 8 DDL files in `db/raw_ddl/` before they are wrapped as Alembic migrations (Step 1.6) and applied (Step 1.4). No DDL files were modified.

## Summary

- Files reviewed: 8
- Critical issues: 1
- Major issues: 3
- Minor issues: 6
- Nits / observations: 4

**Headline:** one critical blocker (a duplicate `CREATE TYPE` that would fail Step 1.4 application). Three major findings are doc-vs-DDL drift, not DDL bugs; the DDLs are coherent, the docs need adjustment to match. The full fix list is below.

## Critical issues (block Step 1.4 until fixed)

### C1: Duplicate `CREATE TYPE actor_user_type_enum` in two DDL files

**Files:**
- `db/raw_ddl/Ithina_postgres_SQL_DDL_shared_utilities_v1.sql`, lines 88-93
- `db/raw_ddl/Ithina_postgres_SQL_DDL_tenant_users_v1.sql`, lines 56-61

**Issue.** Both files contain a plain `CREATE TYPE actor_user_type_enum AS ENUM ('PLATFORM', 'TENANT')`. Postgres `CREATE TYPE` does not support `IF NOT EXISTS` for enum types. In Step 1.4 the files apply in dependency order: `shared_utilities_v1` (file 1) creates the enum, then four files later `tenant_users_v1` tries to create it again and Postgres returns `ERROR: type "actor_user_type_enum" already exists`. The DDL apply will halt at file 5.

**Why critical.** Hard apply failure at Step 1.4. Cannot reach Step 1.5 (smoke test) or Step 1.6 (Alembic wrap) without resolving.

**Confirmation in the source.** The header comment in `shared_utilities_v1.sql` lines 84-86 acknowledges the move and explains it: "Defined here once because it is referenced before tenant_users (where it was inline-defined in v1 of that file)." The duplicate definition in `tenant_users_v1.sql` is a forgotten cleanup, not a deliberate redefinition.

**Recommended fix.** Delete lines 56-61 from `tenant_users_v1.sql` (the entire `CREATE TYPE actor_user_type_enum AS ENUM (...);` block including the two inline `--` comments). The file's existing dependency comment at line 41-43 already states "Shared utilities migration providing: ... enum actor_user_type_enum (defined in tenant_users v1)" which is itself stale and should be corrected at the same time to read "defined in shared_utilities_v1".

```sql
-- DELETE these lines from tenant_users_v1.sql (lines 56-61):
CREATE TYPE actor_user_type_enum AS ENUM (
    'PLATFORM',
        -- Actor is a row in platform_users.
    'TENANT'
        -- Actor is a row in tenant_users.
);
```

No data semantics change; the enum stays defined exactly once in `shared_utilities_v1.sql` with identical values.

## Major issues (should fix soon, not blocking)

### M1: Audit-column pattern in DDLs is mixed (Pattern (a) and Pattern (b)); CLAUDE.md and architecture.md describe it as universal Pattern (b)

**Files affected.** Doc claim spans `CLAUDE.md` (D-13, schema reference, AI invariants) and `docs/architecture.md` ("Audit columns pattern" section, ~line 401-412). Reality is split across all 8 DDLs.

**Issue.** The docs claim every business table has the six-column audit block:

```
created_by_user_id UUID NOT NULL
created_by_user_type actor_user_type_enum NOT NULL
updated_by_user_id UUID NOT NULL
updated_by_user_type actor_user_type_enum NOT NULL
```

(Pattern (b): UUID + type, no FK.) Architecture doc states this is the universal shape. D-13 says "Pattern (b) for actor columns: UUID + actor_user_type_enum, no FK".

The DDLs implement two patterns:

| Pattern | Tables | Why |
|---|---|---|
| (a) — UUID, FK to `platform_users(id)`, no `_user_type` column | `platform_users`, `tenants` | Actor is unambiguously a platform_user. `platform_users` self-references; `tenants` per its v3 changelog: "Tenants are created, updated, suspended, and terminated exclusively by Ithina staff (FN-AB-03). Single FK to platform_users.id; no actor type enum needed." |
| (b) — UUID + `actor_user_type_enum`, no FK, paired CHECK constraints | `lookups`, `tenant_users`, `org_nodes`, `stores`, `roles`, `role_permissions`, `user_role_assignments` | Actor could be PLATFORM or TENANT depending on phase or future flex. |

Plus the `permissions` table has neither pattern (see M2).

The actor-column choice is sensible: use a real FK where the actor type is fixed; use Pattern (b) where it isn't. But the docs do not capture this nuance, which causes:

1. Confusion when generating SQLAlchemy models in Step 3.1 onward (different audit shapes per resource).
2. Risk that critical-path tests in Step 7.1 get written against the wrong assumption.
3. Cross-team friction if a reader of the doc believes Pattern (b) is universal.

**Why major (not critical).** No DDL applies fails. But subsequent code work depends on the docs being right; left unaddressed, this becomes 2-4 hours of rework distributed across model-building steps.

**Recommended fix.** Update CLAUDE.md D-13 and architecture.md "Audit columns pattern" to describe both patterns and the rationale for choice. The fix is doc-only; DDLs as-written are coherent. Suggested rewrite of D-13:

> **D-13 — Audit actor columns: Pattern (a) where actor type is fixed, Pattern (b) otherwise**
>
> Pattern (a): single FK to `platform_users(id)`, no `_user_type` column. Used on `platform_users` (self-referencing) and `tenants` (lifecycle is staff-only per FN-AB-03).
>
> Pattern (b): UUID + `actor_user_type_enum`, no FK, with paired CHECK constraints. Used on every other business table where the actor could be PLATFORM (Phase 1 staff invites) or TENANT (Phase 2 customer admins).
>
> **Tech debt.** Pattern (b) tables have no DB-level referential integrity on actor UUIDs. Migration to FK-bearing pattern is ~2-4 days when needed. Tracked as FN-AB-09.

### M2: `permissions` table has no actor columns at all (no `created_by_*`, `updated_by_*`)

**File:** `db/raw_ddl/Ithina_postgres_SQL_DDL_rbac_v2.sql`, lines 149-184.

**Issue.** `permissions` has only `created_at` and `updated_at`; no actor columns. Inconsistent with every other table. Likely a deliberate choice (permission rows are catalogue entries added via migration / Alembic, not human action; the "actor" is git/CI). The change-log comment on the file does not call this out.

**Why major.** Two downstream consequences:

1. The "every table has audit columns" assumption in CLAUDE.md does not hold for permissions. If model code or tests are generated assuming uniform shape, they will fail or mis-render permissions.
2. If a permission row is ever mutated in production (a code change, an out-of-band fix), there is no record of who did it. For a Phase 1 paying-customer system, this might warrant accountability columns even if 99% of writes are migration-driven.

**Recommended fix.** Two options:

- **Option A (preferred — explicit exception):** keep the DDL as-is, document the exception in CLAUDE.md (D-13 or a new "permissions exception" note) and architecture.md. Permissions rows are catalogue-only, mutated via migration, no actor needed.
- **Option B (uniform):** add the Pattern (b) audit block (created_by_user_id, created_by_user_type, updated_by_user_id, updated_by_user_type) with all four nullable. Apply via Alembic ALTER TABLE later. Adds ~6 lines to the DDL.

**Lean:** Option A. The DDL choice is defensible; document it instead of bloating the catalogue table.

### M3: CLAUDE.md says "12 tables across 8 DDL files"; actual count is 10

**File:** `CLAUDE.md` "Schema reference" section, line 465.

**Issue.** The text claims "12 tables across 8 DDL files (a 9th file, audit_logs, is added during the build at Step 6.2)". But the table that immediately follows lists tables per file as: shared_utilities (n/a) + lookups (1) + platform_users (1) + tenants (1) + tenant_users (1) + org_nodes (1) + stores (1) + rbac (4) = **10 tables**. The "12" claim contradicts CLAUDE.md's own table.

The DDLs match the table (10 tables). When audit_logs lands at Step 6.2 the count is 11, still not 12.

**Why major.** Doc-internal contradiction in a load-bearing reference document. Not a DDL bug, but the inconsistency erodes trust in the doc.

**Recommended fix.** Edit CLAUDE.md line 465 from "12 tables across 8 DDL files" to "10 tables across 8 DDL files (an 11th, audit_logs, is added during the build at Step 6.2)".

## Minor issues (style, consistency, nice-to-have)

### m1: Trigger name uses `trg_` prefix only on lookups; everywhere else `tg_`

**File:** `lookups_v1.sql` line 131: `CREATE TRIGGER trg_lookups_set_updated_at`.

All other tables use `tg_<table>_set_updated_at`: tenants (line 327), platform_users (184), tenant_users (213), org_nodes (261), stores (250), permissions (186), roles (289), user_role_assignments (549). Inconsistent.

D-21 doesn't specify trigger naming. Convention in the codebase is `tg_`. Recommend renaming `trg_lookups_set_updated_at` → `tg_lookups_set_updated_at`.

### m2: `set_updated_at_timestamp()` function defined twice

**Files:**
- `shared_utilities_v1.sql` lines 53-59
- `tenants_v3.sql` lines 87-93

Both use `CREATE OR REPLACE FUNCTION` with identical bodies, so the second redefinition is benign (it overwrites with itself). But it's leftover from when there was no shared_utilities file. The header comment in `tenants_v3.sql` line 84 even acknowledges: "If a shared utilities file is introduced later, move this there." That move was done; the original was not removed.

Recommend deleting lines 80-93 from `tenants_v3.sql` (the function definition and its comment block).

### m3: `CREATE EXTENSION IF NOT EXISTS ltree` in two files

**Files:**
- `shared_utilities_v1.sql` line 38
- `org_nodes_v2.sql` line 59

Both use `IF NOT EXISTS`, so apply is idempotent. But the duplicate is redundant. shared_utilities runs first; ltree is available before org_nodes runs. Recommend deleting line 59 from `org_nodes_v2.sql` (and the surrounding comment block lines 56-60).

### m4: Stale "defined in tenant_users v1" comments in stores and org_nodes

**Files:**
- `stores_v5.sql` line 20: "enum actor_user_type_enum (defined in tenant_users v1)"
- `org_nodes_v2.sql` line 43: "enum actor_user_type_enum (defined in tenant_users v1)"

The enum is now defined in `shared_utilities_v1.sql`. Update both comments to "defined in shared_utilities_v1.sql". Same fix mentioned in C1 should be applied to `tenant_users_v1.sql`'s line 41-43 dependency comment (which says shared_utilities provides the enum even though the file currently redefines it).

### m5: Stale migration-order comment in shared_utilities_v1.sql lacks lookups

**File:** `shared_utilities_v1.sql` lines 17-25.

```
-- Migration order:
--   1. shared_utilities  (this file)
--   2. platform_users
--   3. tenants
--   4. tenant_users
--   5. org_nodes
--   6. stores
--   7. rbac
--   8. (future) audit_logs
```

This list pre-dates the addition of lookups. The comment in `lookups_v1.sql` lines 25-34 has the corrected 9-step order (with lookups at #2). Update the shared_utilities comment to match.

### m6: `uq_org_nodes_tenant_id` constraint name is misleading

**File:** `org_nodes_v2.sql` line 174: `CONSTRAINT uq_org_nodes_tenant_id UNIQUE (tenant_id, id)`.

The name implies UNIQUE on `tenant_id` alone (which would be wrong, as one tenant has many org nodes). The actual constraint is UNIQUE(tenant_id, id), required as the target of the composite FK from `org_nodes.parent_id` and from `stores.org_node_id` and `user_role_assignments.org_node_id`.

Per D-21 convention `uq_<table>_<columns>`, prefer a name that reflects both columns: `uq_org_nodes_tenant_id_id` or `uq_org_nodes_compound_key`.

Renaming requires updating the constraint plus any references. Defer to a follow-up fix; not blocking.

## Nits / observations

- **n1: No `COMMENT ON TABLE` / `COMMENT ON COLUMN` directives anywhere.** Self-documentation lives in inline column comments (good) and file headers (good), but Postgres-introspection tools like `\d+` won't surface them. Adding `COMMENT ON ...` is cheap; defer until a reader needs it.

- **n2: `lookups` audit columns are `NOT NULL` with no DEFAULT.** This relies on the bootstrap seed creating a platform_user before any lookup row inserts. Per CLAUDE.md the seed order is `00_bootstrap.sql` → `01_lookups.sql`, so the chain holds. Just noting the dependency is implicit.

- **n3: `permissions.code` is denormalised** from (module, resource, action, scope) and maintained by the app layer per the comment at line 161-164. Two rows could in principle have inconsistent `code` and tuple values if app code drifts, though `uq_permissions_code` and `uq_permissions_tuple` together prevent the most common drift mode (each must be unique). A trigger or generated column would be tighter; not worth doing now.

- **n4: `user_role_assignments` uses `granted_at + granted_by_*` and `revoked_at + revoked_by_*` instead of `created_*` and `updated_*` plus a separate revocation block.** This is a domain-specific naming choice (assignment lifecycle uses "grant" and "revoke" terms). It's coherent and the `ck_user_role_assignments_revoked_consistency` enforces the lifecycle. Just flagging the naming asymmetry vs other tables.

## Cross-file findings

- **Migration order matches dependency order** (shared_utilities → lookups → platform_users → tenants → tenant_users → org_nodes → stores → rbac). Each file references only types and tables defined in earlier files (modulo the C1 duplicate). Verified.

- **All multi-tenant tables (5) have ENABLE + FORCE ROW LEVEL SECURITY and a tenant-isolation policy.** Tables: tenants, tenant_users, org_nodes, stores, user_role_assignments. Policies all use `current_setting('app.tenant_id', TRUE)::uuid`, with `TRUE` second arg returning NULL when the session var is unset. Default-deny by NULL is verified across all 5.

- **Composite FKs enforce same-tenant integrity** at the DB level:
  - `org_nodes.fk_org_nodes_parent_same_tenant`: `(tenant_id, parent_id)` → `org_nodes(tenant_id, id)`.
  - `stores.fk_stores_org_node_same_tenant`: `(tenant_id, org_node_id)` → `org_nodes(tenant_id, id)`.
  - `user_role_assignments.fk_user_role_assignments_org_node_same_tenant`: `(tenant_id, org_node_id)` → `org_nodes(tenant_id, id)`.

  All three rely on `org_nodes.uq_org_nodes_tenant_id` (composite UNIQUE on tenant_id + id). Verified target exists. (See m6 about the misleading name on that constraint.)

- **All FK actions are `ON DELETE RESTRICT, ON UPDATE RESTRICT`.** No cascades. Consistent with the audit-heavy posture and AI-PU-02 / AI-TU-02 (hard delete is a narrow exception). Verified.

- **`tenants` table uses `id` as its tenant boundary** (no `tenant_id` column). RLS policy uses `id = current_setting('app.tenant_id', TRUE)::uuid`. Matches the schema-reference table's "Self (id IS the tenant_id)" notation.

- **All status-bearing tables have a status-consistency CHECK** that enforces all-or-nothing population of the lifecycle audit fields:
  - `tenants`: suspended_at + suspended_by_user_id; terminated_at + terminated_by_user_id.
  - `platform_users`: suspended_at + suspended_by_user_id.
  - `tenant_users`: suspended_at + suspended_by_user_id + suspended_by_user_type.
  - `org_nodes`: archived_at + archived_by_user_id + archived_by_user_type.
  - `stores`: closed_at + closed_by_user_id + closed_by_user_type.
  - `roles`: archived_at + archived_by_user_id + archived_by_user_type.
  - `user_role_assignments`: revoked_at + revoked_by_user_id + revoked_by_user_type.

  All seven verified.

- **Pattern (b) actor pair CHECK constraints** are present on every Pattern (b) table for every Pattern (b) actor column pair (the lifecycle pair is enforced via the status-consistency CHECK above, so a separate pair-CHECK is not needed and not present). Verified consistent.

- **No duplicate-by-content enums.** Aside from the C1 duplicate, each enum type is defined in exactly one file.

- **No `TIMESTAMP without TZ` columns.** All timestamps are `TIMESTAMPTZ`. Verified by inspection.

- **No `VARCHAR(n)` columns.** All variable-length strings are `TEXT` (with CHECK length constraints where length matters). One exception: `stores.currency CHAR(3) NOT NULL` for ISO 4217 currency codes (deliberate, with regex CHECK).

- **`gen_random_uuid()` used as PK default everywhere** without `pgcrypto`. Postgres 13+ provides this built-in. Stack target is 15. Verified.

## What I verified passed

- All 5 multi-tenant tables (`tenants`, `tenant_users`, `org_nodes`, `stores`, `user_role_assignments`) have `ENABLE ROW LEVEL SECURITY` and `FORCE ROW LEVEL SECURITY`.
- All 5 RLS policies match the same shape: `FOR ALL USING (... = current_setting('app.tenant_id', TRUE)::uuid) WITH CHECK (...)`. Default-deny via NULL session var is uniform.
- Every Pattern (b) actor-column pair has a paired CHECK constraint or is enforced by a status-consistency CHECK.
- Every multi-tenant table has an index on `tenant_id` (or, in the case of `tenants` itself, `id` is the PK index).
- Every UPDATE-bearing table (all except `role_permissions`) has a `tg_<table>_set_updated_at` BEFORE UPDATE trigger calling the shared `set_updated_at_timestamp()` function. (The lookups trigger is `trg_` prefixed; see m1.)
- All PKs are `UUID NOT NULL DEFAULT gen_random_uuid()`. Verified on every table.
- Constraint naming follows D-21 prefix conventions (`pk_`, `fk_`, `uq_`, `ix_`, `ck_`). One naming-clarity issue noted at m6.
- All audit timestamp columns are `TIMESTAMPTZ NOT NULL DEFAULT NOW()`.
- Email-format and email-lowercase CHECK constraints are present on all three user/contact tables (`platform_users.email`, `tenant_users.email`, `tenants.contact_email`).
- Per-tenant uniqueness on (tenant_id, email) for `tenant_users`; global uniqueness on `platform_users.email`.
- Composite FKs enforce same-tenant integrity for org-tree parents, store-to-org-node links, and assignment org-node anchors (3 places, all using the same pattern).
- XOR check on `user_role_assignments` (platform_user_id XOR tenant_user_id) plus anchor-shape and user-anchor-consistency checks.
- `ltree` extension is loaded; `ix_org_nodes_path_gist` GIST index is in place for descendant/ancestor walks.
- All status enums use UPPER_SNAKE_CASE values.
- Migration order is internally consistent: every file references only types and tables defined in earlier files (modulo C1).

## Recommendation on Step 1.4

**Yes after one fix.**

- **C1 must be fixed** (delete duplicate `CREATE TYPE actor_user_type_enum` from `tenant_users_v1.sql` lines 56-61). Without this, Step 1.4 fails at file 5.
- **M1, M2, M3 are doc-vs-DDL drift**, not DDL bugs. Step 1.4 (apply DDLs) and Step 1.5 (smoke test) can proceed without them being resolved. They should be resolved before Step 3.1 (Tenant ORM model) so model code doesn't bake in the wrong audit-column assumption.
- **Minors and nits** can be batched into a follow-up cleanup commit, or deferred. None block Step 1.4.

The DDLs are otherwise coherent. The 5-layer multi-tenancy invariant (RLS + FORCE + tenant-isolation policy with default-deny NULL) holds across all 5 tenant-scoped tables. Composite FKs enforce same-tenant integrity at the schema level. Pattern (b) actor-pair constraints and status-consistency constraints are uniformly applied where the design calls for them.

## Files where everything looked clean

- `lookups_v1.sql` — clean, modulo the `trg_` trigger prefix (m1).
- `platform_users_v1.sql` — clean, audit pattern is Pattern (a) and works with the bootstrap seed.
- `tenants_v3.sql` — clean, modulo the redundant `set_updated_at_timestamp()` definition (m2).
- `org_nodes_v2.sql` — clean, modulo redundant `CREATE EXTENSION ltree` (m3) and stale enum-source comment (m4).
- `stores_v5.sql` — clean, modulo stale enum-source comment (m4).
- `rbac_v2.sql` — clean as a unit; the `permissions`-no-actor question (M2) is the only thing to decide.
- `shared_utilities_v1.sql` — clean, modulo stale migration-order comment (m5). The duplicate-enum issue (C1) is in tenant_users, not here.
- `tenant_users_v1.sql` — has C1 duplicate enum. Otherwise clean.

## Patterns I noticed

- **Two audit-column patterns coexist by design.** Pattern (a) where the actor type is fixed and a single FK to `platform_users(id)` works; Pattern (b) where the actor could be PLATFORM or TENANT. The DDL choice is sensible. The docs need to catch up (M1).
- **Strict RESTRICT on every FK.** No DELETE CASCADE anywhere. Audit fidelity over operational convenience. Aligns with AI-PU-02 / AI-TU-02 narrow-exception delete policy.
- **Status-consistency CHECKs are the dominant invariant pattern.** Every soft-delete / lifecycle column block (suspended, terminated, archived, closed, revoked) has a CHECK that ties the timestamp + actor pair to the status enum value. Strong correctness; high test-friendliness.
- **Composite FKs and matching composite UNIQUEs are used to enforce same-tenant integrity** for parents, org-node links, and assignment anchors. This is one of the more robust tenant-isolation patterns and shows up consistently.
- **Migration order is a hand-maintained linear chain captured in comments.** Three files have a "Migration order" comment block; one is current (lookups_v1), one is stale (shared_utilities_v1, missing lookups), and the rest don't have one. A single source of truth (e.g., a dedicated comment block in shared_utilities or a top-level README in `db/raw_ddl/`) would cut the drift. Defer.

## End of report
