# Report — Step 6.8.1: split user_role_assignments (5 bundles)

## Bundle 1 — Code / migrations / tests

### New files (3)

| File | LoC | Purpose |
|---|--:|---|
| `db/raw_ddl/Ithina_postgres_SQL_DDL_rbac_v3.sql` | 583 | New as-shipped baseline post-split. permissions/roles/role_permissions copied verbatim from v2; new `platform_user_role_assignments` (no RLS) and `tenant_user_role_assignments` (RLS+FORCE, unconditional OR-branch, composite FKs to `tenant_users(tenant_id, id)` and `org_nodes(tenant_id, id)`). v2 DDL unchanged per frozen-DDL convention. |
| `migrations/versions/3e05299cb533_step_6_8_1_split_user_role_assignments.py` | 588 | Migration; `down_revision = 2fdc4bc9f4cb`. Reversible. Body uses `op.execute()` raw SQL throughout, unqualified table names. |
| `prompts/step-6_8_1-split-user-role-assignments-2026-05-08.md` | 861 | Prompt that drove this step. Bundled per the convention. |

### Modified files (5)

| File | LoC delta | Why |
|---|---|---|
| `scripts/smoke_test.py` | +495 / -174 | test_3 URA → tenant_user_role_assignments; test_7 extended to 2 composite-FK assertions; test_11 rewrite (4 invariants from retired truth table); test_15 6th table; test_16 +2 INSERT assertions; module docstring refresh. |
| `scripts/verify_cloud_schema.py` | +13 lines net | Module docstring: expected table count 12→13; RLS list URA → tenant_user_role_assignments; platform_user_role_assignments noted as no-RLS by design. (Line 17 stale alembic head ref left alone per your instruction.) |
| `CLAUDE.md` | +55 lines net | Schema state line (11→12); D-29 amendment (uniform unconditional shape); FN-AB-14 deepened resolution; new D-34; new Step 6.8.1 Completed bullet. |
| `BUILD_PLAN.md` | +264 lines | Section 6.8 introduction; Step 6.8.1 entry DONE; Step 6.8.2 + 6.8.3 placeholder TODO entries with blocked-by chain. |
| `docs/architecture.md` | +8 lines net | Layer-1 RLS prose (uniform unconditional shape); table inventory row (rbac_v2 → rbac_v3, two assignment tables); resource-level auth bullet. |

### Key SQL excerpts

**New `tenant_user_role_assignments` policy** (unconditional OR, matches the other 5 multi-tenant tables):

```sql
CREATE POLICY tenant_user_role_assignments_tenant_isolation
    ON tenant_user_role_assignments
    FOR ALL
    USING (
        tenant_id = NULLIF(current_setting('app.tenant_id', TRUE), '')::uuid
        OR current_setting('app.user_type', TRUE) = 'PLATFORM'
    )
    WITH CHECK (...same...);
```

**Audience-check trigger** (function body, both tables symmetric):

```sql
CREATE OR REPLACE FUNCTION enforce_platform_role_audience()
RETURNS TRIGGER AS $$
DECLARE v_audience role_audience_enum;
BEGIN
    SELECT audience INTO v_audience FROM roles WHERE id = NEW.role_id;
    IF v_audience IS DISTINCT FROM 'PLATFORM' THEN
        RAISE EXCEPTION 'audience-check: ... requires PLATFORM-audience role; role % has audience %',
            NEW.role_id, v_audience;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
```

**Data-copy DO block** (excerpt — RLS-aware iteration):

```sql
DO $$
DECLARE n_platform_old INT := 0; n_tenant_old INT := 0; ... t_id UUID; ...
BEGIN
    PERFORM set_config('app.user_type', 'PLATFORM', true);
    PERFORM set_config('app.tenant_id', '', true);
    -- count + copy PLATFORM-audience rows (admitted via IS-NULL gate)
    INSERT INTO platform_user_role_assignments ... SELECT ... WHERE platform_user_id IS NOT NULL;
    -- iterate tenants for TENANT-side rows
    FOR t_id IN SELECT id FROM tenants LOOP
        PERFORM set_config('app.tenant_id', t_id::text, true);
        INSERT INTO tenant_user_role_assignments ... SELECT ... WHERE tenant_user_id IS NOT NULL AND tenant_id = t_id;
    END LOOP;
    -- post-copy count assertion
    IF n_platform_old != n_platform_new THEN RAISE EXCEPTION ...; END IF;
    IF n_tenant_old != n_tenant_new THEN RAISE EXCEPTION ...; END IF;
END $$;
```

### Counts

| Item | Pre | Post | Delta |
|---|---|---|---|
| Application tables | 11 | 12 | +1 |
| Multi-tenant tables (RLS+FORCE) | 6 | 6 | unchanged |
| Smoke test assertions | 74 PASS | **81 PASS** | +7 |
| pytest passed | 227 | 209 | -18 (17 expected URA-stub failures + 1 skipped same as before) |
| pytest failures | 0 | **17** (all `relation "core.user_role_assignments" does not exist` — Step 6.8.2 fix surface) | +17 |
| mypy | clean (60) | clean (60) | unchanged |
| check_setup | 35/35 | 35/35 | unchanged |
| alembic head | `2fdc4bc9f4cb` | `3e05299cb533` | +1 revision |

## Bundle 2 — CLAUDE.md updates

- Schema state line (line 1349): 11→12 tables; URA replaced by 2 new tables; D-29 prose updated.
- D-29 (line 570): IS-NULL-gated bullet removed; uniform unconditional shape; references the 6 multi-tenant tables including `tenant_user_role_assignments`; FN-AB-14 retirement noted.
- D-29 "Permissive impersonation property": FN-AB-14 reference removed.
- New **D-34** entry (after D-33): "Mixed-audience tables get split into per-audience physical tables" with full What / Why / Trade-off / Forward dependency on `audit_logs` / Reconsider sections.
- FN-AB-14 (line 703): heading amended to "RESOLVED at Step 2.2b; deepened at Step 6.8.1"; preface paragraph explains the deeper retirement.
- New "Step 6.8.1 Completed" bullet covering migration revision, both new tables, composite FKs as the AI-RBAC-06 structural guarantee, audience-check triggers, smoke 74→81, pytest 227→209+17 known, blocked-by chain for 6.8.2/6.8.3.

## Bundle 3 — BUILD_PLAN.md updates

- New "Section 6.8 — Split `user_role_assignments` into two physical tables" introduction paragraph.
- New "Step 6.8.1" entry (status DONE) with full Goal / Why now / Scope in / Scope out / Acceptance / Notes-on-deviations / Coordination / Effort sections.
- New "Step 6.8.2" placeholder entry (status TODO; blocked by 6.8.1) with scope sketch.
- New "Step 6.8.3" placeholder entry (status TODO; blocked by 6.8.2) with scope sketch.

## Bundle 4 — architecture.md updates

- "Layer 1 — Row-Level Security" `app.user_type` paragraph (line 281): uniform unconditional shape across 6 tables; FN-AB-14 retirement noted; D-34 reference; PLATFORM-audience storage in `platform_user_role_assignments` with no RLS.
- "Schema and storage" table inventory: 11→12 tables; rbac_v2 → rbac_v3; URA row replaced with two-table row; "Mixed" / "platform-* No; tenant-* Yes" cells.
- "Authorisation" resource-level bullet (line 363): URA → two new table names.

## Bundle 5 — Prompt file

- `prompts/step-6_8_1-split-user-role-assignments-2026-05-08.md` — confirmed in commit set.

---

## Pre-flight + verification outputs

### Pre-flight items 12-14

- **Item 12 (contradiction-surface):** Live policy text matched live state (DDL/migration drift documented, expected); table count = 11 (matches CLAUDE.md); FN-AB-14 status was "RESOLVED at 2.2b" (now deepened to 6.8.1 per the planned doc update); BUILD_PLAN E4/E5 URL updates deferred to 6.8.3 per scope. **No surprises.**
- **Item 13 (inbound FK):** Zero rows. URA had no inbound FKs. DROP TABLE clean.
- **Item 14 (composite UNIQUE):** **STOP-AND-ASK fired** — `tenant_users` lacked `UNIQUE (tenant_id, id)`. Resolved per your decision: Q1=A (fold into 6.8.1's migration as upgrade op 0), Q2=b (live-vs-DDL drift documented in D-34; `tenant_users_v1.sql` unchanged). Constraint name `uq_tenant_users_tenant_id` mirrors `org_nodes`'s `uq_org_nodes_tenant_id`.

### Migration runs (NOTICE messages)

`alembic upgrade head` against post-truncate DB: 0 rows total. Against post-seed DB: 22 rows split into 3 PLATFORM-audience + 19 TENANT-side. Verified empirically; the migration's two RAISE NOTICE messages emit through alembic logging at NOTICE level (alembic suppresses by default; observed via direct verification queries).

### Schema verification

```
core | platform_user_role_assignments | f | f
core | tenant_user_role_assignments   | t | t
```
12 application tables; alembic head = `3e05299cb533`.

### Round-trip verification

upgrade (22 rows → 3+19) → downgrade (22 rows restored, byte-equivalent FN-AB-14 IS-NULL-gated policy) → upgrade (22 rows → 3+19 again). Round-trip clean across both data and policy text.

### Smoke test output (changed assertions)

All 81 PASS post-truncate. Salient new/changed assertions:

- 7a/7b: composite-FK rejection on both tenant_user and org_node sides ✓
- 11a: platform_user_role_assignments has no RLS (relrowsecurity=false) ✓
- 11b: tenant_user_role_assignments has RLS+FORCE ✓
- 11c: enforce_platform_role_audience rejects TENANT-audience role ✓
- 11d: enforce_tenant_role_audience rejects PLATFORM-audience role ✓
- 15.tenant_user_role_assignments.* (9 cells, all PASS) ✓
- 16.platform_user_role_assignments / 16.tenant_user_role_assignments INSERT ✓

### pytest delta (17 expected URA-stub failures)

All in `test_rbac_router.py` and `test_seed_loader.py`:

- `test_rbac_router.py::test_r1_envelope_pre_grouped_with_user_count`
- `test_rbac_router.py::test_r2_tenant_jwt_platform_block_empty`
- `test_rbac_router.py::test_r3_platform_jwt_sees_both_audiences`
- `test_rbac_router.py::test_r4_user_count_aggregate_correlates_per_role`
- `test_rbac_router.py::test_r5_status_filter_default_active`
- `test_rbac_router.py::test_r6_search_q_ilike`
- `test_rbac_router.py::test_r8_is_system_filter`
- `test_rbac_router.py::test_p1_envelope_and_default_sort`
- `test_rbac_router.py::test_rp1_returns_role_permissions_with_parent_echo`
- `test_rbac_router.py::test_rp2_unknown_role_returns_404`
- `test_rbac_router.py::test_rp3_tenant_jwt_platform_role_returns_404`
- `test_rbac_router.py::test_m4_display_labels_join_from_lookups`
- `test_rbac_router.py::test_h1_role_response_hides_audit_actors`
- `test_seed_loader.py::test_l1_seed_runs_clean_end_to_end`
- `test_seed_loader.py::test_l2_seed_row_counts`
- `test_seed_loader.py::test_l2b_user_role_assignments_total_across_tenants`
- `test_seed_loader.py::test_l3_seed_sentinel_rows`

All trace to `relation "core.user_role_assignments" does not exist` via `_lightweight_stubs.UserRoleAssignment` (used by `RolesRepo._user_count_subquery`) and `loaders/user_role_assignments.py` (writes to dropped table). Step 6.8.2 fixes them; per the prompt these are expected for 6.8.1.

### mypy + check_setup

- mypy strict: 60 source files clean
- check_setup: 35/35

---

## Deviations from the prompt's procedure

1. **Migration body restructured for RLS-aware data copy.** The prompt's locked SQL had `INSERT ... SELECT FROM user_role_assignments WHERE ...` as plain statements. Under the migration session (application role, no GUCs set, FN-AB-14 IS-NULL-gated policy), TENANT-side rows are invisible — the SELECT would silently copy 0 TENANT-side rows. **Restructured into a single DO block** that sets `app.user_type='PLATFORM'`, copies PLATFORM-audience rows under IS-NULL-gate admission, then iterates `tenants` and per-row impersonates `app.tenant_id` to copy each tenant's TENANT-side rows. Mirrors `loaders/user_role_assignments.py`'s pattern. Verified empirically: 22 rows copied correctly. The prompt's pre-flight + post-copy DO blocks were folded into the single iteration DO block (count + copy + verify in one pass per the operator-clarity criterion).

2. **Stop-and-ask trigger #4 (composite UNIQUE missing)** fired and was surfaced before any code work. Resolved per your decision (Option A + Option b).

## Incidental findings

1. **`scripts/verify_cloud_schema.py` line 17** stale alembic head reference (`0644a4186e48 as of Step 3.6 lookups seed`) — pre-existing drift, NOT fixed in this step per your instruction. Should be addressed when convenient (probably as part of Step 8.x prod-cutover updates to verify_cloud_schema.py).

2. **`db/scripts/` untracked directory** and **`scripts/test_endpoints.sh` modified** — your in-progress work, left alone per established convention.

3. **`docs/build-step-workflow.md` modified** and **`scripts/test_endpoints_max_view.sh` untracked** — same; your in-progress work.

---

## Files staged-or-not at commit time (your call)

**In scope for Step 6.8.1 (recommend stage):**

- `db/raw_ddl/Ithina_postgres_SQL_DDL_rbac_v3.sql` (NEW)
- `migrations/versions/3e05299cb533_step_6_8_1_split_user_role_assignments.py` (NEW)
- `prompts/step-6_8_1-split-user-role-assignments-2026-05-08.md` (NEW)
- `scripts/smoke_test.py` (MODIFIED)
- `scripts/verify_cloud_schema.py` (MODIFIED)
- `CLAUDE.md` (MODIFIED)
- `BUILD_PLAN.md` (MODIFIED)
- `docs/architecture.md` (MODIFIED)

**Out of scope (your in-progress work, leave alone unless told otherwise):**

- `docs/build-step-workflow.md` (MODIFIED)
- `scripts/test_endpoints.sh` (MODIFIED)
- `db/scripts/` (UNTRACKED)
- `scripts/test_endpoints_max_view.sh` (UNTRACKED)

---

**Tasks 9-15 complete. Task 16 (this report) in progress, awaiting your authorization to stage and commit.** No `git add` or `git commit` will run until you explicitly authorize. Please review and confirm with `yes / no / edit message` per the per-step convention.
