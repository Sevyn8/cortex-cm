# Prompt — Step 1.3: Stress-test the 8 DDLs

> Paste this entire block into a fresh Claude Code session when starting Step 1.3.

---

## Pre-flight

Before doing any work:

1. Run `./scripts/check_setup.sh`. If any check fails, stop and report.
2. Read `CLAUDE.md` fully — focus on:
   - Schema reference (the table of 8 DDLs).
   - "Naming conventions" and "Datatypes" subsections.
   - D-21 (schema conventions), D-13 (audit columns pattern), D-03 (RLS).
3. Read `docs/architecture.md` "Schema and storage" and "Multi-tenancy and data isolation" sections in full.
4. Read `BUILD_PLAN.md` Step 1.3 in full.
5. Read this prompt fully and confirm scope.

---

## Step ID and intent

**Step 1.3** — Stress-test the 8 DDLs.

Read every DDL file in `db/raw_ddl/` and surface issues before they're encoded into migrations. Cheaper to fix a DDL than a migration. **You produce a report; you do NOT modify any DDL file.** The user reviews and approves fixes before Step 1.4 applies them.

This is a CLAUDE_CODE step. Read-only on the DDL files. No DB connection needed (Step 1.4 applies). The deliverable is an issue list, not code.

---

## Scope in

### Files to read (in dependency order)

1. `db/raw_ddl/Ithina_postgres_SQL_DDL_shared_utilities_v1.sql`
2. `db/raw_ddl/Ithina_postgres_SQL_DDL_lookups_v1.sql`
3. `db/raw_ddl/Ithina_postgres_SQL_DDL_platform_users_v1.sql`
4. `db/raw_ddl/Ithina_postgres_SQL_DDL_tenants_v3.sql`
5. `db/raw_ddl/Ithina_postgres_SQL_DDL_tenant_users_v1.sql`
6. `db/raw_ddl/Ithina_postgres_SQL_DDL_org_nodes_v2.sql`
7. `db/raw_ddl/Ithina_postgres_SQL_DDL_stores_v5.sql`
8. `db/raw_ddl/Ithina_postgres_SQL_DDL_rbac_v2.sql`

(audit_logs DDL is added later at Step 6.2; not part of this stress test.)

### Per-file checks

For each DDL, verify:

**Naming conventions:**
- Table name: snake_case, plural (e.g., `tenants`, `stores`).
- Column names: snake_case, singular.
- Constraint names follow conventions:
  - PK: `pk_<table>` or implicit.
  - FK: `fk_<table>_<column>_<referenced_table>`.
  - Unique: `uq_<table>_<columns>`.
  - Index: `ix_<table>_<columns>`.
  - Check: `ck_<table>_<description>`.
- Enum types: `<column>_enum` (singular).

**Required columns:**
- Surrogate PK: `id UUID NOT NULL DEFAULT gen_random_uuid()`.
- Audit columns:
  - `created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`
  - `created_by_user_id UUID NOT NULL`
  - `created_by_user_type actor_user_type_enum NOT NULL`
  - `updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`
  - `updated_by_user_id UUID NOT NULL`
  - `updated_by_user_type actor_user_type_enum NOT NULL`
- Multi-tenant tables: `tenant_id UUID NOT NULL` (with FK to tenants except for tenants table itself).

**Datatypes:**
- TEXT instead of VARCHAR(n).
- TIMESTAMPTZ for all timestamps (never TIMESTAMP without TZ).
- UUID for all IDs.
- NUMERIC with precision: `(15,2)` for money, `(14,3)` for quantities.
- JSONB instead of JSON.
- CHAR(2) with regex check for ISO country codes (where used).
- CHAR(3) with regex check for ISO currency codes (where used).

**RLS (multi-tenant tables only):**
- `ALTER TABLE ... ENABLE ROW LEVEL SECURITY;`
- `ALTER TABLE ... FORCE ROW LEVEL SECURITY;` (CRITICAL — without FORCE, owner role bypasses).
- `CREATE POLICY ...` with USING clause filtering on `tenant_id = current_setting('app.tenant_id')::uuid`.
- For the `tenants` table itself: policy filters on `id = current_setting('app.tenant_id')::uuid`.

**Indexes:**
- Index on `tenant_id` for every multi-tenant table.
- Indexes on commonly-filtered columns (status, created_at, etc.).
- Composite indexes match access patterns documented in CLAUDE.md or architecture doc.

**Constraints:**
- NOT NULL on required fields.
- CHECK constraints for status enums (or use enum types).
- CHECK constraints for non-negative values (e.g., `num_stores >= 0`).
- Unique constraints where uniqueness is invariant.
- terminated_at consistency check (set when and only when status is TERMINATED).
- closed_at on stores (mirror pattern).

### Cross-file checks

**Dependency order:**
- Each DDL only references types/tables defined in earlier files.
- `actor_user_type_enum` defined in `shared_utilities_v1.sql` (or earliest user table) and referenced by all later tables.
- `tenants` table defined before any tenant_id FK.

**Foreign key targets:**
- Every FK references a table that exists.
- Cascade vs restrict policy is sensible (e.g., CASCADE on tenant deletion to tenant_users? Or RESTRICT?).
- Composite FKs work as expected.

**Enum reuse:**
- `actor_user_type_enum` used consistently across all audit columns.
- No duplicate enum types with same values across files.
- Enum value naming: UPPER_SNAKE_CASE for codes (e.g., `ACTIVE`, `MID_MARKET`).

**RLS policy consistency:**
- Same shape across multi-tenant tables.
- Default-deny via NULL session var works (verify policy uses `tenant_id = current_setting(...)::uuid`, not `tenant_id IS NULL OR ...`).

### Edge cases to check

- **NULL handling on composite keys:** if a UNIQUE constraint includes nullable columns, are NULLS NOT DISTINCT used (Postgres 15+) or sentinel values?
- **Cascade depth:** does a tenant DELETE cascade through too many tables (FK depth > 5 hops is a smell)?
- **Index bloat:** are there indexes that duplicate each other (e.g., `(tenant_id)` and `(tenant_id, status)` — the second covers the first)?
- **Audit column self-reference:** does the bootstrap user pattern work? (`platform_users.created_by_user_id = platform_users.id` for the very first row.)
- **Status enum cardinality:** are enum types preferred over CHECK constraints for status fields? Adding a new value requires `ALTER TYPE ... ADD VALUE`; consider lookup tables instead for frequently-changing enums.
- **Default values:** are timestamps defaulted to `NOW()`? Are booleans defaulted explicitly?
- **Comment coverage:** does each table and important column have a `COMMENT ON ...` for self-documentation?

### Things that are NOT in scope

- **Do NOT modify any DDL file.** Read-only.
- **Do NOT apply DDLs to the database.** That's Step 1.4.
- **Do NOT propose adding new tables or columns.** Only flag what's missing or wrong in existing files.
- **Do NOT verify performance against production data.** That's an architecture-level concern, not a stress test.

---

## Output format

Produce a structured issue list as your primary deliverable. Format:

```markdown
# DDL Stress Test Report

## Summary

- Files reviewed: 8
- Critical issues: <count>
- Major issues: <count>
- Minor issues: <count>
- Nits / observations: <count>

## Critical issues (block Step 1.4 until fixed)

### C1: <Short title>

**File:** `db/raw_ddl/<filename>`
**Lines:** <line range or specific lines>
**Issue:** <description>
**Why critical:** <impact>
**Recommended fix:**
```sql
<concrete SQL change>
```

## Major issues (should fix soon, not blocking)

### M1: ...

## Minor issues (style, consistency, nice-to-have)

### m1: ...

## Nits / observations

- ...

## Cross-file findings

- ...

## What I verified passed

- All multi-tenant tables have ENABLE + FORCE ROW LEVEL SECURITY.
- All audit columns present per Pattern (b).
- ...
```

Severity definitions:

- **Critical:** Will produce a bug, security issue, or schema-incorrectness on day one. Examples: missing FORCE on RLS, missing audit columns, wrong column type for money. **Must be fixed before Step 1.4.**
- **Major:** Wrong but not catastrophic. Examples: missing index on commonly-filtered column, inconsistent constraint naming. **Fix recommended before Step 1.4 unless reason to defer.**
- **Minor:** Style, consistency, naming. Examples: comment missing on table, constraint named slightly off-pattern. **Fix at convenience.**
- **Nit:** Observations not requiring action. Examples: "could use a covering index here later if reads are slow".

---

## Implementation hints

### Reading order matters

Read in dependency order so you can verify FK targets exist as you go. If shared_utilities defines `actor_user_type_enum`, every later file's audit columns must reference it consistently — flag any that use a different type.

### Don't trust the file name to match table content

A file named `Ithina_postgres_SQL_DDL_rbac_v2.sql` may contain 1, 2, or 4 tables. List exactly what each file produces and verify against CLAUDE.md "Schema reference" table.

### Cross-check against CLAUDE.md

CLAUDE.md "Schema reference" claims:
- 12 tables across 8 DDL files.
- 9th file (audit_logs) added during build.
- Specific file → table mappings.

Verify the actual files match those claims. If they don't, that's an issue.

### Verify the architecture doc claims

Architecture doc "Schema and storage" makes specific claims:
- Composite identity for canonical entities (tenant_id + sku_id pattern) — n/a here, that's DIS.
- For admin backend: every multi-tenant entity has tenant_id NOT NULL and RLS.
- audit columns Pattern (b): UUID + actor_user_type_enum, no FK.

Verify each claim against the actual DDL files. Flag drift.

### Don't get bogged down in style

Keep critical / major separate from minor / nit. The user wants the critical list short and actionable. The minor / nit list can be long; it's a backlog.

### Use grep / awk patterns for repeated checks

For example, to find all `CREATE TABLE` statements:
```bash
grep -nE "^CREATE TABLE" db/raw_ddl/*.sql
```

To find FORCE RLS:
```bash
grep -nE "FORCE.*ROW LEVEL SECURITY" db/raw_ddl/*.sql
```

To find tables that ENABLE but don't FORCE RLS:
```bash
# Tables with ENABLE
grep -lE "ENABLE.*ROW LEVEL SECURITY" db/raw_ddl/*.sql > /tmp/enable.txt
# Tables with FORCE
grep -lE "FORCE.*ROW LEVEL SECURITY" db/raw_ddl/*.sql > /tmp/force.txt
# Difference
diff /tmp/enable.txt /tmp/force.txt
```

Use these patterns liberally. Don't try to read 8 files line-by-line for everything.

---

## Acceptance criteria

- Issue list produced with critical / major / minor / nit severity.
- Every critical issue includes: file, lines, description, why critical, recommended SQL fix.
- "What I verified passed" section lists invariants that ARE upheld across the DDLs.
- User reviews list. For each critical/major: user marks "fix before 1.4", "defer", or "won't fix".
- For approved fixes: user authorises edits to specific DDL files (or asks Claude Code to make them in a follow-up).
- Non-critical issues recorded in CLAUDE.md "Forward notes" section as a backlog.

---

## Stop and ask if

- A DDL file is missing or empty.
- The naming conventions in CLAUDE.md don't match what the DDLs use, AND it's unclear which is authoritative.
- A schema decision in the DDL contradicts a decision in CLAUDE.md or architecture doc.
- You find issues that suggest the DDL is fundamentally wrong (not just minor) — surface immediately, don't try to compile a long list.

---

## What to report at end

The full report (using the format above), plus:

- Total time spent reviewing.
- Files where everything looked clean.
- Any patterns you noticed (e.g., "audit columns are missing on lookups; this is intentional but flag for confirmation").
- Recommendation on whether Step 1.4 (apply DDLs) should proceed:
  - "Yes, no critical issues."
  - "Yes after fixes to: <list>".
  - "No, there are <N> critical issues that need user input before proceeding."

---

## After completing

This step does NOT necessarily end with a git commit; the deliverable is a report (often pasted into chat or saved as a file). If you saved the report to a file (e.g., `docs/ddl-stress-test-report.md`), propose:

```
git status
git add -A
git commit -m "Step 1.3: DDL stress-test report

- Reviewed 8 DDL files for naming, types, RLS, indexes, constraints
- Surfaced N critical, M major, K minor issues
- See docs/ddl-stress-test-report.md for full breakdown"
```

If the report was inline in chat (no file saved), no commit is needed.

If user-approved DDL fixes follow in a separate commit, that commit's message should reference this report.

---

## End of prompt
