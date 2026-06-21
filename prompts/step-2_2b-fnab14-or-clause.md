# Prompt — Step 2.2b: FN-AB-14 OR-clause migration; smoke test 9-row truth table

> Generated 2026-05-02 (initial). Paste into a fresh Claude Code session for Step 2.2b.

---

## Pre-flight

1. `./scripts/check_setup.sh` — expect 35/35.
2. `git log --oneline -10` — confirm Step 2.2a + cleanup commits at HEAD.
3. Read `CLAUDE.md` D-27, FN-AB-14, AI-MT-03, D-03 fully.
4. Read `BUILD_PLAN.md` Step 2.2b in full.
5. Read this prompt fully.

---

## Step ID and intent

**Step 2.2b** — Land the FN-AB-14 fix: amend `user_role_assignments_tenant_isolation` policy to permit PLATFORM-audience rows (NULL `tenant_id`) when `app.user_type = 'PLATFORM'`. Rewrite the smoke test's assertions 11–12 as a 9-row truth table covering all session-var combinations. Mark FN-AB-14 RESOLVED.

This closes the architectural gap surfaced by Step 1.5 and decided in FN-AB-14. The two-variable session bootstrap that this depends on already shipped at Step 2.2a; this step adds the policy that uses both variables.

CLAUDE_CODE step. No application code changes; one Alembic migration + one test file rewrite.

---

## Required behaviour

### Policy amendment (the one and only DDL change)

Current policy on `user_role_assignments` (post-NULLIF migration e59f62d5037d):

```sql
USING (tenant_id = NULLIF(current_setting('app.tenant_id', TRUE), '')::uuid)
WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', TRUE), '')::uuid)
```

Replace with:

```sql
USING (
    tenant_id = NULLIF(current_setting('app.tenant_id', TRUE), '')::uuid
    OR (tenant_id IS NULL AND current_setting('app.user_type', TRUE) = 'PLATFORM')
)
WITH CHECK (
    tenant_id = NULLIF(current_setting('app.tenant_id', TRUE), '')::uuid
    OR (tenant_id IS NULL AND current_setting('app.user_type', TRUE) = 'PLATFORM')
)
```

NULLIF wrapper preserved per D-27. The OR-branch is gated on `app.user_type = 'PLATFORM'` so tenant users cannot see PLATFORM-audience rows.

**Permissive scope:** PLATFORM users with non-NULL `app.tenant_id` (impersonation case per D-24) match the first clause AND can see PLATFORM rows via the OR clause. v0 keeps this permissive; revisit if/when impersonation rules tighten.

**Other 4 policies (tenants, tenant_users, org_nodes, stores) are NOT touched.** They don't have nullable `tenant_id` rows; the simpler NULLIF-only form is correct for them.

### Smoke test rewrite

Replace the two failing assertions (11, 12) with a 9-row truth table covering all combinations of `app.tenant_id` ∈ {A, B, NULL} × `app.user_type` ∈ {TENANT, PLATFORM, unset}, evaluated against the three row classes (TENANT-A, TENANT-B, PLATFORM-audience).

Truth table per FN-AB-14 (in CLAUDE.md):

| `app.tenant_id` | `app.user_type` | TENANT-A row | TENANT-B row | PLATFORM row |
|---|---|---|---|---|
| A | TENANT | Visible | Invisible | Invisible |
| B | TENANT | Invisible | Visible | Invisible |
| (unset) | TENANT | Invisible | Invisible | Invisible |
| A | PLATFORM | Visible | Invisible | Visible |
| B | PLATFORM | Invisible | Visible | Visible |
| (unset) | PLATFORM | Invisible | Invisible | Visible |
| A | (unset) | Invisible | Invisible | Invisible |
| B | (unset) | Invisible | Invisible | Invisible |
| (both unset) | | Invisible | Invisible | Invisible |

9 rows × 3 row classes = 27 individual visibility assertions, but the smoke test only needs to count rows per `(tenant_id, user_type)` combination — 9 SELECT count(*) assertions total.

Implement as a parameterised loop in the smoke test, not 9 hand-written assertions.

### Meta-assertion (also new)

Add a meta-assertion that every table in `core` schema with a `tenant_id` column has RLS enabled, FORCE enabled, and at least one policy. Catches future "I added a new multi-tenant table and forgot RLS."

Query shape:

```sql
SELECT t.tablename
FROM pg_tables t
JOIN information_schema.columns c
  ON c.table_schema = t.schemaname
  AND c.table_name = t.tablename
  AND c.column_name = 'tenant_id'
WHERE t.schemaname = current_setting('search_path')  -- or hardcode 'core'
  AND NOT EXISTS (
    SELECT 1 FROM pg_class pc
    JOIN pg_namespace pn ON pc.relnamespace = pn.oid
    WHERE pc.relname = t.tablename
      AND pn.nspname = t.schemaname
      AND pc.relrowsecurity = TRUE
      AND pc.relforcerowsecurity = TRUE
  );
```

Assert this returns zero rows. Note: don't filter by policy name (tenants uses `tenants_self_access`, others use `*_tenant_isolation` — name varies but both are valid).

---

## Scope in

1. **New migration file** `migrations/versions/<rev>_amend_user_role_assignments_or_clause.py`. Reversible. Downgrade restores the post-NULLIF (e59f62d5037d) form, not the original pre-NULLIF form.
2. **Rewrite `scripts/smoke_test.py`**:
   - Drop existing assertions 11, 12.
   - Add 9-row truth-table parameterised assertion (replaces 11, 12; net change in assertion count is +7, going from 14 to 21).
   - Add meta-assertion for RLS+FORCE+policy on every multi-tenant table.
   - Update assertion-count print at end.
3. **CLAUDE.md update**: mark FN-AB-14 RESOLVED. Body updated to point at the migration revision and note the smoke test changes.
4. **BUILD_PLAN.md update**: Step 2.2b status TODO → DONE.
5. **Prompt file**: this prompt, committed alongside.

---

## Scope out

- Changes to the other 4 multi-tenant policies. They're correct.
- Application code changes. Step 2.2a already wires `app.user_type` per request.
- Auth0 wiring, middleware, handlers (Steps 2.3, 2.4, post-launch).
- Smoke test infrastructure beyond the 11/12 → 9-row replacement and the meta-assertion.

---

## Stop and ask if

- The migration's downgrade target (post-NULLIF e59f62d5037d) is unclear — i.e., reversing the OR-clause should leave the NULLIF wrapper intact. If Alembic state interferes, surface.
- The 9-row truth table reveals a behaviour different from CLAUDE.md FN-AB-14's documented expectations. Surface; don't silently adjust the table.
- The meta-assertion query returns multi-tenant tables that aren't in the expected 5. New tables have appeared somehow; investigate before continuing.
- The smoke test as a whole now exceeds reasonable runtime (>5 seconds) with the new assertions. Profile and surface.

---

## Acceptance criteria

- New migration applies cleanly: `alembic upgrade head` produces the OR-clause policy on `user_role_assignments`; the other 4 policies are byte-identical to their post-e59f62d5037d state.
- `alembic downgrade -1` restores the post-NULLIF form (not pre-NULLIF). Round-trip clean.
- Smoke test produces 22 PASS total (was 14; -2 for old assertions 11/12, +9 for the truth table, +1 for the meta-assertion). All PASS.
- `current_setting('app.user_type', TRUE)` and `app.tenant_id` GUC behaviour matches the truth table in every row.
- `./scripts/check_setup.sh` 35/35.
- CLAUDE.md FN-AB-14 marked RESOLVED with the migration revision noted.
- BUILD_PLAN.md Step 2.2b status flipped DONE.

---

## Report (BEFORE proposing commit)

Per the per-step bundling convention, four bundles:

1. **Code/migrations/tests:** the new migration file (line count); the rewritten `scripts/smoke_test.py` (diff stat).
2. **CLAUDE.md updates:** FN-AB-14 → RESOLVED; any architecture.md cross-reference updates if needed.
3. **BUILD_PLAN.md updates:** Step 2.2b status flip; scope-in/acceptance text corrections if any drift.
4. **Prompt file:** `prompts/step-2_2b-fnab14-or-clause.md` confirmed in commit set.

Plus: smoke test full output (22 assertions, all PASS); migration round-trip verification (upgrade → downgrade → upgrade with policy text byte-compared); check_setup status.

Wait for explicit authorisation before staging or committing.

---

## End of prompt
