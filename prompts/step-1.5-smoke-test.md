# Prompt — Step 1.5: Smoke test script with cross-tenant RLS verification

> Paste this entire block into a fresh Claude Code session when starting Step 1.5.
> Revised prompt: incorporates D-15 (parameterised schema), D-13 (mixed audit-actor pattern), the FORCE RLS verification from Step 1.4, and the UUIDv7 PK convention (D-21 amended in commit 9fcf9ef, function landed in ccadd44, defaults switched in b519bc0). Earlier draft had a transaction-rollback bug, undertested assertion 12, and missed v7 DEFAULT verification entirely.

---

## Pre-flight

Before doing any work:

1. Run `./scripts/check_setup.sh`. If any check fails, stop and report. Do not attempt to fix setup unless told.
2. Read `CLAUDE.md` fully. Pay particular attention to:
   - D-13 (audit-actor pattern: Pattern (a) for `tenants` and `platform_users`, Pattern (b) for everything else).
   - D-15 (schema namespacing parameterised via `DB_SCHEMA`).
   - D-21 (UUIDv7 PK convention; `uuidv7()` defined in shared utilities, used as DEFAULT on every metadata-table PK).
   - The "Current state" section.
3. Read `docs/architecture.md` "Multi-tenancy and data isolation" section in full.
4. Read `BUILD_PLAN.md` Step 1.5 in full.
5. **Read `db/raw_ddl/Ithina_postgres_SQL_DDL_platform_users_v1.sql` in full** — this is non-optional. The smoke test inserts a bootstrap platform_user, and the bootstrap pattern depends on whether `created_by_user_id` and similar audit columns on platform_users are NULL-able or NOT NULL. Confirm which before writing the script. The two patterns are:
   - **Nullable:** bootstrap user inserts with audit columns explicitly NULL.
   - **NOT NULL:** bootstrap user self-references (`created_by_user_id = id`).
   Apply whichever pattern the DDL supports. Do not guess.
6. Read this prompt fully and confirm scope before writing code.

---

## Step ID and intent

**Step 1.5** — Smoke test script.

Write a Python script that connects directly to the local Postgres (no SQLAlchemy yet) and exercises schema invariants:
- tenant isolation via RLS+FORCE,
- cross-tenant INSERT rejection,
- FK integrity (including composite-FK same-tenant integrity),
- CHECK constraints,
- status-consistency invariants,
- the `user_role_assignments` PLATFORM-audience edge case,
- **UUIDv7 DEFAULT generation on every metadata-table PK.**

The script catches schema-level bugs before any application code is built on top.

This is a CLAUDE_CODE step. Self-contained: no FastAPI, no SQLAlchemy, no app integration. Just `psycopg` (v3) against the running local Postgres.

---

## Required behaviour: read DB_SCHEMA from env, set search_path explicitly

Per CLAUDE.md D-15, the schema name is parameterised via the `DB_SCHEMA` env var. The smoke test must:

1. Read `DB_SCHEMA` and `DATABASE_URL` from the environment at script start. **If either is unset, fail with a clear error message before doing anything else.** Do not fall back to defaults.
2. At the start of every transaction or savepoint that does anything, run `SET LOCAL search_path TO {db_schema}, public` explicitly. Use `psycopg.sql.SQL` and `psycopg.sql.Identifier` to quote the schema name safely; do not use f-string interpolation. The role default is belt-and-suspenders, not a guarantee.

This is non-negotiable per D-15 "Implications for build steps." A smoke test that relies on role defaults silently fails to detect search_path misconfigurations in dev/prod.

---

## Critical: the FORCE RLS gotcha

The 5 multi-tenant tables (`tenants`, `tenant_users`, `org_nodes`, `stores`, `user_role_assignments`) have `FORCE ROW LEVEL SECURITY` enabled. Step 1.4 verified all five with both `relrowsecurity=true` and `relforcerowsecurity=true`.

With FORCE, the table owner role does NOT bypass RLS. The policy applies even to the connection that just inserted rows.

Practical implications:

- After INSERTing rows for tenant A, you cannot SELECT them back unless you set `app.tenant_id = '<tenant-A-uuid>'` first. A naive `INSERT ... ; SELECT ...` returns 0 rows.
- INSERT itself is also subject to RLS via the policy's `WITH CHECK` clause. To insert tenant A's rows, set `app.tenant_id = '<tenant-A-uuid>'` first; otherwise the INSERT fails (or silently inserts 0 rows depending on policy phrasing).
- For the default-deny check, **do not call SET on `app.tenant_id` for that transaction.** The variable is unset at the start of every transaction in psycopg3 (no role-level default is configured for it), so a transaction that doesn't SET it gets `current_setting('app.tenant_id', TRUE) = NULL` and the policy filters out everything. **Verify** at the start of the default-deny phase with `SELECT current_setting('app.tenant_id', TRUE)` returning NULL; if it returns a value, something is wrong with the role configuration and the test will give a false positive.

This will look like FK or CHECK failures but is actually RLS filtering. If you see "0 rows returned" where rows should exist, check that `app.tenant_id` is set on the current transaction.

---

## Critical: rollback discipline

The smoke test must leave the DB in the same state it started in. Inserted rows must NOT persist across runs.

Use `with conn.transaction(force_rollback=True):` for every transaction. `force_rollback=True` causes psycopg3 to roll back even on successful exit, which is what we want — the smoke test asserts behaviour during the transaction, then rolls back. Do not use a plain `with conn.transaction():` block; that commits on success and accumulates state across runs.

If `force_rollback` is not available in your psycopg3 version (it's available from 3.1.0; check with `psycopg.__version__`), wrap the entire script in a single outer transaction and call `conn.rollback()` explicitly at the end of the script in a try/finally.

The acceptance criterion "runs cleanly twice in a row" depends on this. Get it right.

---

## Connection setup pattern

Use `psycopg` (v3) directly. Sync API is simpler for a one-off script.

The DATABASE_URL in `.env` is in SQLAlchemy form (`postgresql+psycopg://...`). psycopg3 itself accepts both forms, so the URL works directly without transformation. No need to mirror the `${DATABASE_URL/postgresql+psycopg/postgresql}` substitution that `scripts/check_setup.sh` uses for psql; that's a libpq limitation, not a psycopg one. Verify this assumption when you run the connect; if psycopg3 rejects the URL, fall back to the substitution and document the workaround.

Pattern for the script:

```python
import os
import sys
import psycopg
from psycopg import sql

DATABASE_URL = os.environ.get("DATABASE_URL")
DB_SCHEMA = os.environ.get("DB_SCHEMA")

if not DATABASE_URL or not DB_SCHEMA:
    print("ERROR: DATABASE_URL and DB_SCHEMA must both be set in env", file=sys.stderr)
    sys.exit(2)

conn = psycopg.connect(DATABASE_URL, autocommit=False)
```

For deterministic UUIDs (used for **referencing** existing rows in test data setup), use string literals like:
- `00000000-0000-0000-0000-00000000aaaa` for tenant A
- `00000000-0000-0000-0000-00000000bbbb` for tenant B
- `00000000-0000-0000-0000-00000000aaa1` for tenant A's first user
- etc.

Cast to UUID in SQL: `'00000000-...'::uuid`.

**Note on hardcoded UUIDs vs uuidv7() default:** the smoke test uses hardcoded UUIDs for most rows so assertions can reference them by known ID. This means the `uuidv7()` DEFAULT does NOT fire on those rows (an explicit `id` is provided, the default is bypassed). Assertion 14 below specifically tests the DEFAULT path by inserting one row WITHOUT an explicit `id` and verifying the generated UUID is v7-shaped.

---

## Bootstrap user pattern

The first `platform_user` (the bootstrap "system" actor) has nothing to FK to — no other platform_user exists at insert time. The pattern depends on the platform_users DDL.

**Read the DDL in pre-flight step 5 and apply whichever pattern it supports.** The two possibilities:

- **If `created_by_user_id` and similar columns are NULL-able:**
```sql
  INSERT INTO platform_users (id, email, ..., created_by_user_id, updated_by_user_id, suspended_by_user_id)
  VALUES ('00000000-0000-0000-0000-000000000001', 'system@ithina.local', ..., NULL, NULL, NULL);
```

- **If `created_by_user_id` and similar columns are NOT NULL:**
```sql
  INSERT INTO platform_users (id, email, ..., created_by_user_id, updated_by_user_id)
  VALUES ('00000000-0000-0000-0000-000000000001', 'system@ithina.local', ...,
          '00000000-0000-0000-0000-000000000001',  -- self-reference
          '00000000-0000-0000-0000-000000000001');
```

After the bootstrap user exists, subsequent platform_users have their audit columns FKed to the bootstrap user. Tenants have audit FKs to platform_users via Pattern (a) (per D-13). Tables in Pattern (b) carry `actor_user_type_enum + UUID` for audit, no FK.

If the DDL doesn't match either pattern (e.g. some columns NULL, others NOT NULL with no self-reference path), stop and ask.

---

## Scope in

- File: `scripts/smoke_test.py`.
- Reads `DATABASE_URL` and `DB_SCHEMA` from env; fails fast if either is unset.
- Sets `search_path` explicitly per transaction using parameterised identifiers.
- Inserts test data: 1 bootstrap platform_user, 2 tenants (A and B), 1 tenant_user per tenant, 1 org_node per tenant, 1 store per tenant, plus role/permission/role_permission/user_role_assignment rows for the PLATFORM-audience and TENANT-audience cases.
- Wraps every transaction in `with conn.transaction(force_rollback=True):` (or equivalent rollback-on-success pattern) so the DB stays clean.

**Required assertions (14 total):**

A. Tenant isolation (SELECT side):
   1. With `app.tenant_id = tenant_A`, SELECT on each multi-tenant table returns ONLY tenant A's rows.
   2. With `app.tenant_id = tenant_B`, the same SELECTs return ONLY tenant B's rows.
   3. With `app.tenant_id` not SET on the transaction, SELECTs on multi-tenant tables return ZERO rows. Pre-verify in this transaction that `current_setting('app.tenant_id', TRUE)` returns NULL.

B. Tenant isolation (INSERT side):
   4. With `app.tenant_id = tenant_A`, INSERT into `stores` with `tenant_id = tenant_B` is REJECTED (RLS WITH CHECK clause). Capture the exception class and message.

C. Composite-FK same-tenant integrity. Architecture.md identifies 3 composite FKs that enforce same-tenant. Each gets an assertion:
   5. INSERT into `stores` referencing an `org_node_id` whose `tenant_id` differs from the store's `tenant_id` is REJECTED (composite FK). Capture exception.
   6. INSERT into `org_nodes` with a `parent_id` whose `tenant_id` differs is REJECTED. Capture exception.
   7. INSERT into `user_role_assignments` with an `org_node_id` whose `tenant_id` differs from the assignment's `tenant_id` is REJECTED. Capture exception.

D. Status-consistency CHECK constraints (test BOTH directions):
   8a. INSERT into `tenants` with `status='TERMINATED'` but `terminated_at IS NULL` is REJECTED.
   8b. INSERT into `tenants` with `terminated_at` set but `status != 'TERMINATED'` is REJECTED.
   9. UPDATE on `platform_users.suspended_at` consistency: setting `suspended_at` without `status='SUSPENDED'` (or vice versa) is REJECTED. (Use UPDATE on the bootstrap user; restore at end of phase.)

E. Domain validation (CHECK constraints, not enum cast):
   10. INSERT a row that violates a CHECK constraint with valid type-shape data — e.g. `tenants.country` length out of range, or `stores.currency` not matching the regex. The assertion exercises a real CHECK, not just enum-cast rejection. Capture exception.

F. user_role_assignments PLATFORM-audience edge case:
   11. INSERT a PLATFORM-audience role assignment with `tenant_id = NULL` (the PLATFORM-audience pattern per the DDL). Set `app.tenant_id = tenant_A` and confirm the row is NOT visible (PLATFORM rows shouldn't leak to tenant context).
   12. With `app.tenant_id` not SET in the transaction (default-deny), confirm the PLATFORM-audience row IS visible. The expected behaviour is that the RLS policy on `user_role_assignments` includes a clause permitting NULL `tenant_id` rows when no session tenant is set (e.g. `(tenant_id = current_setting('app.tenant_id')::uuid OR tenant_id IS NULL)`). If the policy doesn't include this, assertion 12 will FAIL — that's a real architectural finding, not a test bug. Surface it in the end-of-task report.

G. Bootstrap user pattern:
   13. Bootstrap platform_user inserts cleanly (per the pattern determined in pre-flight step 5). Verify the row is readable back (platform_users is not RLS-scoped).

H. UUIDv7 DEFAULT generation:
   14. INSERT a row into `platform_users` (or another metadata table — pick the simplest non-RLS one) WITHOUT specifying `id` in the column list. The DEFAULT clause should fire. Capture the returned `id` (use `RETURNING id`). Verify:
       - The version nibble (13th hex character of the canonical UUID text) is `7`.
       - The variant nibble (19th hex character) is in `{8, 9, a, b}`.
       This is the only assertion that exercises the `uuidv7()` default we landed in Step C. Without it, the smoke test has zero coverage on the v7 work.

- Print `[PASS]` / `[FAIL]` per assertion with a short label.
- On FAIL, also print the exception class and the first 200 characters of the exception message, so debugging doesn't require re-running with debug prints.
- Total assertion count printed at the end.
- Exit 0 if all pass, 1 if any fail.

---

## Scope out

- SQLAlchemy ORM (not yet built).
- FastAPI endpoints / handlers.
- Comprehensive test coverage (this is a smoke test, not the full suite).
- Audit log table (added at Step 6.2; not part of v1 of this script).
- **Lookups table.** Lookups is platform-global (no `tenant_id`, no RLS), it has no audit-actor columns after the recent DDL cleanup, and it's a catalogue table managed via seed migration not via app inserts. Including it in a smoke test exercises nothing that other assertions don't already cover. The deliberate exclusion is recorded here so a future contributor doesn't add it back without thinking.
- Tearing down DB state via `DROP` — `force_rollback=True` is sufficient.
- Verifying the smoke test itself with a deliberate-fail injection — DDLs are read-only.

---

## Implementation hints

- Connect with `psycopg.connect(database_url, autocommit=False)` to manage transactions explicitly.
- Use `with conn.transaction(force_rollback=True):` for every phase. The phase commits-or-rolls-back at block exit; `force_rollback=True` makes it always roll back.
- For the schema-name SET, use parameterised SQL composition:

```python
  from psycopg import sql

  cur.execute(
      sql.SQL("SET LOCAL search_path TO {}, public").format(
          sql.Identifier(DB_SCHEMA)
      )
  )
```

- Suggested helper:

```python
  from contextlib import contextmanager
  from psycopg import sql

  @contextmanager
  def tenant_phase(conn, tenant_id, db_schema):
      """Set search_path and (optionally) app.tenant_id for one transaction.

      Always rolls back on exit, even on success. The smoke test asserts
      behaviour during the transaction; persistence is not the goal.
      """
      with conn.transaction(force_rollback=True):
          cur = conn.cursor()
          cur.execute(
              sql.SQL("SET LOCAL search_path TO {}, public").format(
                  sql.Identifier(db_schema)
              )
          )
          if tenant_id is not None:
              cur.execute("SET LOCAL app.tenant_id = %s", (str(tenant_id),))
          # If tenant_id is None, deliberately do NOT SET app.tenant_id.
          # The variable stays NULL for this transaction (default-deny phase).
          yield cur
```

- For deterministic UUIDs, define them as Python constants at the top of the script.
- For assertion 14, use `RETURNING id` and verify the returned UUID directly:

```python
  cur.execute("INSERT INTO platform_users (email, ...) VALUES (...) RETURNING id")
  generated_id = cur.fetchone()[0]
  uuid_text = str(generated_id)
  version_char = uuid_text[14]   # 13th 0-indexed = 14th 1-indexed
  variant_char = uuid_text[19]
  assert version_char == '7', f"Expected version 7, got {version_char} in {uuid_text}"
  assert variant_char in {'8', '9', 'a', 'b'}, f"Expected variant in 8-b, got {variant_char}"
```

- Print output format:
