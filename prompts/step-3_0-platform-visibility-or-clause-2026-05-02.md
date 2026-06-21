# Prompt — Step 3.0: PLATFORM-visibility OR-clause on remaining 4 multi-tenant policies

> Generated 2026-05-02, 06:30 PM. Paste this entire block into a fresh Claude Code session to start Step 3.0.
> Step 3.0 is a back-fill: the OR-clause that FN-AB-14 added to `user_role_assignments` at Step 2.2b should also exist on the other 4 multi-tenant policies (`tenants`, `tenant_users`, `org_nodes`, `stores`).
>
> **Two failure modes this step fixes** (both surfaced during the Step 3.2 stress test):
>
> 1. **READ:** A PLATFORM session (`app.tenant_id = NULL`, `app.user_type = 'PLATFORM'`) sees zero rows on these 4 tables. The list-tenants endpoint planned for Step 3.3 returns an empty list to the admin user it's built for. R3/R6/R8 in the Step 3.2 prompt are blocked.
> 2. **WRITE:** A PLATFORM session also cannot INSERT into these 4 tables. The `WITH CHECK` predicate `id = NULLIF(NULL, '')::uuid` evaluates to UNKNOWN, which RLS treats as a CHECK violation. This means: test fixtures (`make_tenant` in Step 3.2's conftest), seed scripts (Step 6.3), and any future write endpoint cannot insert tenant rows from a non-BYPASSRLS PLATFORM session. The application role is `NOSUPERUSER NOBYPASSRLS` per Step 1.5 hardening, with a startup gate refusing to boot otherwise — so there is no escape hatch. Without Step 3.0, the only way to insert tenant rows is direct DB access by a privileged role, which is exactly what the project rejected.
>
> The READ failure is what motivated raising the question; the WRITE failure is the stronger reason to land the fix. Either alone justifies the step.
>
> First step landing under the new five-item per-step convention (architecture.md as conditional fifth bundle item). Likely a yes-edit on architecture.md given this changes how 4 of the 5 multi-tenant policies behave.

---

## Pre-flight

1. Run `./scripts/check_setup.sh`. Expect 35/35.
2. `git log --oneline -10` — confirm Step 3.1 + drift sweep + convention extension at HEAD.
3. Read `CLAUDE.md` fully. Focus on:
   - **D-03** — RLS shape; `app.tenant_id` and `app.user_type` set per-transaction.
   - **D-27** — NULLIF wrapper requirement on `current_setting`. All clauses in this step preserve it.
   - **FN-AB-14** (CLAUDE.md ~line 585, "RESOLVED at Step 2.2b") — read the resolution carefully. Migration `4fd3aec6ae0c` is the template; this step extends the same approach to 4 more policies, with one critical structural difference (see below).
   - **Schema state** (~line 916): "5/5 multi-tenant tables ... NULLIF wrapper as of `e59f62d5037d` (Step 2.2a). `user_role_assignments_tenant_isolation` carries the FN-AB-14 OR-clause as of `4fd3aec6ae0c` (Step 2.2b)." Step 3.0 will produce a fourth migration in this chain.
   - **Smoke test state** (~line 918): `scripts/smoke_test.py` at 24 PASS. After this step, the smoke test must grow new assertions — see "Scope in" below.
4. Read `docs/architecture.md` "Schema and storage" section (post-drift-sweep wording).
5. Read the 4 affected DDLs to confirm current policy shape and column names:
   - `db/raw_ddl/Ithina_postgres_SQL_DDL_tenants_v3.sql` — policy `tenants_self_access`. **Note:** column is `id`, not `tenant_id`. This is the one structural exception.
   - `db/raw_ddl/Ithina_postgres_SQL_DDL_tenant_users_v1.sql` — policy on `tenant_users`. Column: `tenant_id` (NOT NULL).
   - `db/raw_ddl/Ithina_postgres_SQL_DDL_org_nodes_v2.sql` — policy on `org_nodes`. Column: `tenant_id` (NOT NULL).
   - `db/raw_ddl/Ithina_postgres_SQL_DDL_stores_v5.sql` — policy on `stores`. Column: `tenant_id` (NOT NULL).
6. Read the existing FN-AB-14 migration: `migrations/versions/4fd3aec6ae0c_*.py`. Mirror its file shape (downgrade reverts to the pre-OR-clause form, etc.). The SQL inside is *not* a copy-paste target — see "Critical structural difference" below.
7. Read `BUILD_PLAN.md` Step 3.0:
   ```bash
   grep -A30 "## Step 3.0" BUILD_PLAN.md || echo "Step 3.0 not yet in BUILD_PLAN — to be added in this step's commit."
   ```
   If the step doesn't exist in BUILD_PLAN.md yet, it's added in this step's commit (slotting between 2.4 and 3.1, or after 3.1 — pick whichever ordering reads right; see the open question in "Stop and ask if").
8. Read `scripts/smoke_test.py` — note the existing 9-row truth table on `user_role_assignments`. Step 3.0 grows analogous truth-table assertions on the other 4 tables.
9. Read this prompt fully.

---

## Step ID and intent

**Step 3.0** — Extend the FN-AB-14-style PLATFORM-visibility OR-clause to the 4 multi-tenant RLS policies that don't currently carry it: `tenants`, `tenant_users`, `org_nodes`, `stores`.

Three concrete deliverables:

1. **Single Alembic migration** that drops and recreates these 4 policies with the new shape.
2. **Update to the 4 raw DDL files** to reflect the new policy form (DDL files are the source of truth per CLAUDE.md; if the policy changes, the DDL changes).
3. **Smoke-test additions** that assert PLATFORM visibility on each of the 4 tables.

CLAUDE_CODE step. No application-code changes; the OR-clause is enforced entirely by the database. Application code already sets `app.user_type` correctly per-transaction (Step 2.2a).

---

## Critical structural difference vs. FN-AB-14

This is the one place where copy-pasting from FN-AB-14 would silently produce a wrong result. **Read this section twice before writing the migration.**

### FN-AB-14 OR-clause (on `user_role_assignments`, where `tenant_id` is NULLABLE)

```sql
tenant_id = NULLIF(current_setting('app.tenant_id', TRUE), '')::uuid
OR (tenant_id IS NULL AND current_setting('app.user_type', TRUE) = 'PLATFORM')
```

The `tenant_id IS NULL AND ...` gate scopes the OR-branch to PLATFORM-audience rows specifically. PLATFORM users see PLATFORM-audience rows + (if `app.tenant_id` set) their impersonated tenant's rows.

### Step 3.0 OR-clause (on `tenants`, `tenant_users`, `org_nodes`, `stores`, where `tenant_id` is NOT NULL)

```sql
<id_column> = NULLIF(current_setting('app.tenant_id', TRUE), '')::uuid
OR current_setting('app.user_type', TRUE) = 'PLATFORM'
```

**Without** the `IS NULL AND` gate. Why: on these 4 tables, `tenant_id` (or `id` on `tenants`) is NOT NULL, so a clause requiring `tenant_id IS NULL` would never fire — defeating the OR's purpose entirely. The intent here is "PLATFORM users see all rows," and that requires the OR-branch to fire on every row, not on the (empty) subset where tenant_id is NULL.

If a copy-paste of FN-AB-14's clause lands here, the migration will run successfully, the smoke test (depending on how it's written) might even pass, but PLATFORM-users-list-all-tenants will silently return zero rows — exactly the bug Step 3.0 is fixing. **The migration is a no-op if the OR-branch has the IS NULL gate.**

### `tenants` is the column-name exception

The `tenants` table's policy compares `id` (its own primary key), not `tenant_id`. So the `tenants` clause is:

```sql
id = NULLIF(current_setting('app.tenant_id', TRUE), '')::uuid
OR current_setting('app.user_type', TRUE) = 'PLATFORM'
```

`tenant_users`, `org_nodes`, `stores` use `tenant_id` (their FK to the parent tenant) and follow the standard form.

---

## Scope in

### File 1: New Alembic migration `migrations/versions/<rev>_step_3_0_platform_visibility_or_clause.py` — new

Generate via:
```bash
uv run alembic revision -m "step_3_0_platform_visibility_or_clause"
```

Set `down_revision` to the current head (`4fd3aec6ae0c`). The migration body drops and recreates 4 policies inside one `def upgrade()` block, all with the new shape. `def downgrade()` reverts to the pre-OR-clause form (the post-NULLIF shape from `e59f62d5037d`).

Pseudocode shape (Claude Code: read `4fd3aec6ae0c`'s actual file for the exact stylistic conventions, then mirror):

```python
"""step_3_0_platform_visibility_or_clause

Revision ID: <gen>
Revises: 4fd3aec6ae0c
Create Date: 2026-05-02 ...

Extends the FN-AB-14 PLATFORM-visibility pattern to the remaining 4
multi-tenant RLS policies: tenants, tenant_users, org_nodes, stores.

Without this clause, PLATFORM sessions (app.tenant_id = NULL,
app.user_type = 'PLATFORM') see zero rows on these tables, because the
tenant_id-equality clause evaluates id/tenant_id = NULL → unknown.
PLATFORM-users-list-all-tenants is the canonical example of a query that
should work but doesn't until this migration lands.

Note: this OR-clause is structurally different from FN-AB-14's. The 4
target tables have NOT NULL tenant_id (or in the tenants case, NOT NULL
id), so the IS-NULL gate from FN-AB-14 would never fire here. The
correct shape is unconditional:

    OR current_setting('app.user_type', TRUE) = 'PLATFORM'
"""
from alembic import op

revision = "<gen>"
down_revision = "4fd3aec6ae0c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # tenants — note: column is `id`, not `tenant_id`.
    op.execute("DROP POLICY IF EXISTS tenants_self_access ON tenants")
    op.execute("""
        CREATE POLICY tenants_self_access ON tenants
          FOR ALL
          USING (
            id = NULLIF(current_setting('app.tenant_id', TRUE), '')::uuid
            OR current_setting('app.user_type', TRUE) = 'PLATFORM'
          )
          WITH CHECK (
            id = NULLIF(current_setting('app.tenant_id', TRUE), '')::uuid
            OR current_setting('app.user_type', TRUE) = 'PLATFORM'
          )
    """)

    # tenant_users
    op.execute("DROP POLICY IF EXISTS <existing_policy_name> ON tenant_users")
    op.execute("""
        CREATE POLICY <existing_policy_name> ON tenant_users
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

    # org_nodes — same shape as tenant_users.
    # stores — same shape as tenant_users.


def downgrade() -> None:
    # Reverts to the post-NULLIF shape (the form `e59f62d5037d` left).
    # tenants, tenant_users, org_nodes, stores: all four policies dropped
    # and recreated with the single-clause USING/WITH CHECK form.
    ...
```

Verification points:
- Read each existing policy's *exact name* from the DDL files; don't guess. The migration must DROP the policy by its real name. (`tenants` is `tenants_self_access`; the others have their own names — read the DDLs.)
- The four policies are recreated with the same name they had — don't rename.
- `WITH CHECK` mirrors `USING` (matches FN-AB-14's pattern). For v0 read-only this is belt-and-suspenders, but it makes the policies write-correct for when write endpoints land post-v0.
- `FOR ALL` matches the pre-existing form.

### Files 2-5: Update the 4 raw DDL files

The DDL files in `db/raw_ddl/` are the source of truth. When the live policy changes, the DDL must change. (Per CLAUDE.md "DDL files are source of truth; do not edit. Schema changes go through Alembic." That rule is about *schema-level* changes — adding columns, changing types — and the project's pattern has been: DDL is the canonical record; migrations encode the deltas; DDL is updated in lockstep when migrations land. Verify by checking whether the FN-AB-14 work updated the `user_role_assignments` DDL; if it did, mirror that practice. If it didn't, surface as an open question — there may be a different convention.)

Edits per file:
- `Ithina_postgres_SQL_DDL_tenants_v3.sql`: replace the `tenants_self_access` policy block with the new OR-clause form.
- `Ithina_postgres_SQL_DDL_tenant_users_v1.sql`: same for the tenant_users policy.
- `Ithina_postgres_SQL_DDL_org_nodes_v2.sql`: same for the org_nodes policy.
- `Ithina_postgres_SQL_DDL_stores_v5.sql`: same for the stores policy.

Don't bump the `_v3`/`_v5` suffixes — those track structural revisions of the schema, not policy edits. Leave the filenames alone.

### File 6: `scripts/smoke_test.py` — add truth-table assertions for the 4 tables

The existing smoke test has a 9-row truth table on `user_role_assignments` (Step 2.2b). The new assertions extend that pattern.

For each of the 4 tables, the truth table is 9 rows × 2 row classes (TENANT-A row, TENANT-B row). PLATFORM-audience rows don't exist on these tables (their tenant_id is NOT NULL).

Add 18 assertions per table × 4 tables = 72 new assertions, OR (cleaner) one parameterised assertion that walks the full 4×9×2 matrix programmatically and reports per-row pass/fail. The existing smoke test's style should guide which approach fits — read its current shape and mirror.

The expected matrix per table (e.g., for `tenants`):

| `app.tenant_id` | `app.user_type` | TENANT-A row | TENANT-B row | count |
|---|---|---|---|---|
| A | TENANT | Visible | Invisible | 1 |
| B | TENANT | Invisible | Visible | 1 |
| (unset) | TENANT | Invisible | Invisible | 0 |
| A | PLATFORM | Visible | Visible | 2 |
| B | PLATFORM | Visible | Visible | 2 |
| (unset) | PLATFORM | Visible | Visible | 2 |
| A | (unset) | Visible | Invisible | 1 |
| B | (unset) | Invisible | Visible | 1 |
| (both unset) |  | Invisible | Invisible | 0 |

Note rows 4-6: PLATFORM users see both tenants regardless of `app.tenant_id`. This is the new behaviour — under the old policy, rows 4 and 5 saw 1 (only the matched tenant) and row 6 saw 0.

Total smoke-test count after this step: 24 (existing) + 72 (or fewer, if parameterised) = ~96 read-side assertions, give or take depending on how the parameterisation lands. Don't fixate on the count; fixate on "every cell of every truth table is asserted, every table is exercised."

**Plus: one INSERT-side assertion per table** (4 new assertions total). The WITH CHECK predicate matters: a PLATFORM session must be able to INSERT into each of the 4 tables. Assertion shape per table: open a session with `app.user_type = 'PLATFORM'` and `app.tenant_id = NULL`, attempt an INSERT (use a row that satisfies all NOT NULL / FK / CHECK constraints; rollback after), expect success. Without this, a future regression that re-narrows the WITH CHECK predicate would silently break test fixtures and seed scripts.

Meta-assertion 12 (every `tenant_id`-bearing table has RLS + FORCE + ≥1 policy) stays as-is; this step doesn't add tables.

### File 7: `BUILD_PLAN.md` — modify

- **Add Step 3.0** as a new section. Place it between Step 2.4 and Step 3.1 (numerically ordered) or after 3.1 (chronologically ordered). Recommend numerical ordering (between 2.4 and 3.1) so the BUILD_PLAN reads coherently — a future reader scanning the file shouldn't have to mentally reorder steps. The chronological reality (this is being added after 3.1 shipped) is captured in git history; BUILD_PLAN should reflect logical ordering.
- **Step 3.0 status:** TODO → DONE in this same edit (the step lands and closes in one commit).
- Scope-in / scope-out / acceptance reflects what shipped.

### File 8: `CLAUDE.md` — update

- **Current state → Completed:** add a Step 3.0 bullet covering the migration revision id, the 4 policies updated, the DDL updates, the smoke-test growth, and a one-line summary of the change ("PLATFORM users now see all rows on tenants/tenant_users/org_nodes/stores; tenant isolation for TENANT users unchanged").
- **Schema state line:** update the description of the 5 multi-tenant tables. Currently says NULLIF wrapper on all 5 (Step 2.2a) + OR-clause on `user_role_assignments` (Step 2.2b). After 3.0: NULLIF wrapper + PLATFORM OR-clause on **all 5**, with a structural-difference note (the IS-NULL gate is on `user_role_assignments` only because of its nullable tenant_id; the other 4 use the unconditional form).
- **Smoke test state line:** update from "24 PASS" to the new count.
- **D-03 reference area:** add a one-line note that PLATFORM visibility on tenant-owned tables is policy-enforced (no BYPASSRLS, no separate role) — anchors the design choice for future readers.
- **Optionally a new D-XX entry** for "PLATFORM RLS visibility pattern: policy-clause keyed on `app.user_type`, not BYPASSRLS." This is the kind of decision that warrants an entry — it's load-bearing and it's the pattern future tables will inherit. Lean toward adding it. Suggested: D-29 "PLATFORM RLS visibility via policy clause, not BYPASSRLS role." The entry should also explicitly capture the **permissive-impersonation property**: when `app.tenant_id` is set AND `app.user_type = 'PLATFORM'`, the OR-clause's PLATFORM branch is TRUE for every row, so the user sees all rows on these tables — not just the impersonated tenant's. This is consistent with FN-AB-14's resolution note ("Permissive: a PLATFORM user with `app.tenant_id` set sees both that tenant's rows AND PLATFORM-audience rows"). For v0 this is intentional: RLS is the visibility floor; if true impersonation-scoping is needed (e.g., a Support Admin's UI showing only the impersonated tenant's data while debugging a ticket), it must be enforced at the application layer — typically as a `WHERE tenant_id = <impersonated_id>` filter in the handler, on top of RLS. Step 6.1 (RBAC) is where this handler-layer scoping lands. The D-29 `Reconsider if` clause should name this: "if v1 needs RLS-enforced impersonation-scoping (e.g., a Support Admin must not be able to *accidentally* query across tenants while impersonating), the policy needs a third state — possibly a fourth `app.*` GUC like `app.impersonation_active` — and this entry should be revisited."

### File 9: `docs/architecture.md` — likely yes-edit per the new convention

The "Schema and storage" section describes RLS. Specifically, this step changes how 4 of the 5 multi-tenant policies behave. The doc must reflect this; otherwise architecture.md and reality drift again (the same drift category that the recent sweep cleaned up).

Specific edits expected:
- Update any text that describes RLS as "tenant_id = app.tenant_id" → describe the policy-clause pattern with the OR-branch for PLATFORM.
- If there's prose like "PLATFORM users see no rows by default and must impersonate" — that was the old reality, no longer true. Replace with the v0 reality: "PLATFORM users see all rows on multi-tenant tables; per-role RBAC (Step 6.1) constrains what they can do with that visibility."
- Add a short note that this is policy-enforced, not BYPASSRLS-enforced (consistency anchor for future tables).

If the relevant section turns out to be silent on this layer of RLS detail — i.e., it doesn't describe the policies at all, just says "RLS enforces tenant isolation" — then this file is a no-edit. Read first, then decide.

### File 10: `prompts/step-3_0-platform-visibility-or-clause-2026-05-02.md`

This prompt file. Bundled into the commit per the per-step convention.

---

## Testing and regression discipline

### New tests added by this step

The smoke test grows ~72 new assertions across the 4 tables (or fewer if parameterised). These are the load-bearing tests for this step. Design discipline reminder: each cell of each truth table must fail against the *current* (pre-migration) policy shape and pass against the new shape. If an assertion passes both ways, it's not testing what we think.

No new pytest unit/integration tests in this step. The Repo-level tests for cross-tenant isolation (R4/R5) land in Step 3.2; the changes here just unblock R3/R6/R8 (PLATFORM-visible) tests.

### Regression risk surface introduced by this step

1. **The old behaviour ("PLATFORM sees nothing on tenants without impersonation") may have been load-bearing somewhere.** It shouldn't be — Step 3.2 hasn't shipped yet, no router endpoints exist that depend on this. But verify: no existing test in `tests/` asserts that a PLATFORM session sees zero tenants. Run `grep -rn "user_type.*PLATFORM\|PLATFORM.*user_type" tests/` and check that nothing relies on the old behaviour.
2. **Migration ordering.** The new migration's `down_revision` must be `4fd3aec6ae0c` (the FN-AB-14 head). If a migration has landed since (none expected, but verify), `down_revision` shifts accordingly. Run `uv run alembic current` and `uv run alembic heads` before generating the migration.
3. **Migration round-trip.** Run `uv run alembic upgrade head` then `uv run alembic downgrade -1` then `uv run alembic upgrade head` again. All three must succeed without error. `downgrade` should revert to the post-NULLIF form (single-clause USING/WITH CHECK), not back to pre-NULLIF; we don't want to reintroduce the D-27 issue on the way down.
4. **Smoke test must run cleanly against both pre-migration and post-migration states.** Pre-migration: the old assertions pass (24); the new READ assertions for rows 4-6 (PLATFORM seeing all) FAIL, and the 4 new INSERT assertions FAIL (this is good — it confirms the assertions are real). Post-migration: all assertions pass (~100). Easy way to verify: stash the migration changes, run smoke test (expect new assertions to fail in expected places), unstash, run again (all pass).
5. **DDL/migration consistency.** The DDL files describe the *current* policy. After the migration lands, the DDL files must match. If a future step regenerates the initial migration from DDL (per the Step 1.6 generator pattern), the regenerated migration must include the OR-clause. This is automatic if the DDL is updated; flagging in case the DDL update gets missed.
6. **Pytest baseline.** Run `uv run pytest -v` after the migration applies. Expected: 70 passed (no change from current). The OR-clause is read-only-time visibility logic; nothing in the existing pytest suite touches it directly. If a test fails, that's a real regression to investigate.
7. **Downstream dependencies on the WRITE path.** Step 3.2's `make_tenant` factory (in `tests/integration/conftest.py`) and Step 6.3's seed scripts both need PLATFORM sessions to INSERT into multi-tenant tables. Both are blocked until this migration lands. After 3.0 ships, Step 3.2's stress-test concerns (cleanup pattern, factory commit semantics) become solvable; before 3.0 they're literally unsolvable without BYPASSRLS, which the project rejected. Sequencing matters: 3.0 → 3.2 → 3.3.

### Verification harness (run all five; all must be green)

```bash
# 1. Existing pytest suite — no regressions expected
uv run pytest -v

# 2. mypy strict (no source changes, but verify nothing drifted)
uv run mypy --strict src/admin_backend

# 3. Pre-flight checker
./scripts/check_setup.sh

# 4. Migration round-trip
uv run alembic upgrade head      # apply 3.0
uv run alembic downgrade -1      # revert to 4fd3aec6ae0c
uv run alembic upgrade head      # re-apply 3.0

# 5. Smoke test on the post-migration database state
python scripts/smoke_test.py
```

Expected: pytest 70 passed; mypy clean; check_setup 35/35; alembic round-trip succeeds; smoke test ~96 PASS (or whatever the parameterised count produces).

If any leg is not green, **report the failure rather than the step.** Do not commit.

---

## Scope out

- **Application code changes.** None expected. Tenant-context flow already sets `app.user_type` correctly (Step 2.2a).
- **`audit_logs` table policies.** Step 6.2 lands `audit_logs`; its policy gets designed at that step.
- **RBAC enforcement** ("can a Support Admin actually configure pricing rules?"). That's Step 6.1, application-layer.
- **Per-role RLS distinctions** ("Platform Admin shouldn't see waste log rows"). v0 keeps RLS coarse (binary TENANT/PLATFORM); per-role distinctions live in RBAC. If post-v0 a tighter RLS policy is needed, a new decision entry covers that.
- **RLS-enforced impersonation scoping.** When a PLATFORM user has `app.tenant_id` set (impersonating a specific tenant), this step's OR-clause makes them see *all* rows on these tables, not just the impersonated tenant's. Permissive by design, consistent with FN-AB-14's stance. If the admin UI needs to show only the impersonated tenant's data during impersonation, that's a *handler-layer* concern (a `WHERE tenant_id = <impersonated_id>` filter on top of RLS), addressed at Step 6.1. Captured in D-29's `Reconsider if`. Do not attempt to encode impersonation-scoping in RLS in this step.
- **Auth0 cutover, deploy work, etc.** Unrelated.

---

## Stop and ask if

- The existing FN-AB-14 migration `4fd3aec6ae0c` doesn't update the raw DDL file (i.e., `Ithina_postgres_SQL_DDL_rbac_v2.sql` shows the pre-OR-clause form). That would mean the project's convention is "DDL files are source-of-truth for *initial* schema; migrations encode all subsequent policy/structure changes; DDL files are not edited per migration." In that case, skip Files 2-5 — don't edit the DDLs. Surface and we'll confirm.
- Step 3.0's BUILD_PLAN.md insertion point is unclear. The choice is: (a) numerical, between 2.4 and 3.1 — reads cleanest but reorders; (b) chronological, after 3.1 — reflects history but creates a numbering gap. Recommend (a). Surface if unsure.
- The smoke test's existing structure makes it hard to extend cleanly (e.g., it's a long list of explicit asserts rather than a parameterised matrix). Surface; we'll either refactor it as part of this step (small) or accept some duplication.
- The migration's `down_revision` is anything other than `4fd3aec6ae0c`. That means another migration landed in between; surface what it is and we'll figure out the right ordering.
- Running the migration against the local DB produces unexpected output (e.g., a policy DROP fails because the policy name was different from what the DDL shows). Surface; don't paper over.
- A pytest test that the prompt didn't anticipate fails after the migration. This would mean the old behaviour was load-bearing somewhere — we want to know about it before committing.

---

## Acceptance criteria

- 1 new migration file; 4 DDL files updated (or 0 if convention says DDLs aren't edited per-migration — confirm via FN-AB-14 precedent).
- ~72 new smoke-test assertions across the 4 tables, all passing post-migration.
- Pytest 70 passed (no change).
- mypy clean.
- check_setup 35/35.
- Migration round-trip works (upgrade → downgrade → upgrade).
- BUILD_PLAN.md has a Step 3.0 entry, status DONE.
- CLAUDE.md "Current state" reflects the new schema state and smoke-test count; D-29 (or similar) added if the design-decision entry is judged worth capturing.
- architecture.md updated if the system shape moved (likely yes for this step); confirmed no-edit otherwise.

---

## Report (BEFORE proposing commit)

Five bundles per the new convention:

1. **Code/migrations:** migration file with the 4 policy DDLs; the 4 raw DDL edits (or "not edited per FN-AB-14 precedent"); the smoke-test additions with new assertion count.
2. **CLAUDE.md updates:** Current state Completed bullet for 3.0; Schema state line update; Smoke test state update; D-29 (or chosen alternative) added or skipped (with reasoning).
3. **BUILD_PLAN.md updates:** Step 3.0 added at the chosen position; status DONE.
4. **architecture.md updates:** specific edits made, or "no change because the section doesn't describe policy-clause detail."
5. **Prompt file:** `prompts/step-3_0-platform-visibility-or-clause-2026-05-02.md` confirmed in commit set.

Plus: pytest pass count; mypy status; check_setup status; alembic round-trip output; smoke-test pre/post counts.

Wait for explicit authorisation before staging or committing.

---

## End of prompt
