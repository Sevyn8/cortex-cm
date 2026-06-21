# Prompt — Step 6.4: Tenants list aggregate sort keys

> Generated 2026-05-05. Resolved through frontend-locked design review:
> - Tiny extension to the existing `/api/v1/tenants` endpoint — adds sort keys for the per-row aggregates `num_users_active` and `num_stores`.
> - Precondition for Step 6.5 (Dashboard stats) — the dashboard's "Top tenants by users" panel calls `/tenants?sort=num_users_active_desc&limit=5`.
> - No schema changes. No new endpoints. No new schemas. Smallest endpoint-touching step in the build so far.
>
> Paste this entire block into a fresh Claude Code session to start Step 6.4.

---

## Context: why this step exists and why now

The Frontend dashboard (Frontend spec 7.1) renders a "Top tenants by users" panel: 5 rows, ordered by user count descending (Żabka 1,240 → Infomil 482 → Buc-ee's 312 → GreenLeaf 96 → SmartStore 64). Per the locked design discussion (2026-05-05), the dashboard is composed from three calls per page render: two stats endpoints (Step 6.5) plus the existing `/api/v1/tenants` list endpoint with a sort-by-user-count query.

Today's `/api/v1/tenants` accepts six sort keys, all on direct columns of the `tenants` table:

- `created_at_asc`, `created_at_desc`
- `name_asc`, `name_desc`
- `tier_asc`, `tier_desc`

It does **not** accept sorts on the per-row aggregates `num_users_active` or `num_stores`, because those are correlated scalar subqueries (Step 3.3 L9 pattern), not direct columns. Asking `?sort=num_users_active_desc` today returns `400 INVALID_SORT_KEY`.

This step extends the sort vocabulary to cover the two existing aggregates. Four new keys: `num_users_active_asc`, `num_users_active_desc`, `num_stores_asc`, `num_stores_desc`.

**Why now.** Step 6.5 (Dashboard stats) ships immediately after this step and depends on `num_users_active_desc` working against `/tenants` so the Top Tenants panel can render. Without 6.4, the dashboard endpoints would ship but the panel call would 400. Splitting into two steps (rather than bundling) is justified because the work is genuinely independent: 6.4 touches `/tenants` only; 6.5 touches `/dashboard/*` only; sharing a step bundles unrelated review surfaces.

**Why a forward-friendly step.** Frontend will likely want the same sort keys for future analytical views — "smallest tenants by users for outreach," "tenants without stores," etc. Adding asc + desc for both aggregates costs almost nothing compared to adding desc only and revisiting later.

---

## Pre-flight

1. Run `./scripts/check_setup.sh`. Expect 35/35.
2. `git log --oneline -5` — confirm Step 6.1 (RBAC read endpoints) is in history. Step 6.2 (audit-logs) has not shipped — that's expected and unrelated to this step.
3. `uv run alembic heads` — confirm head matches the most-recently-shipped step. **No migration in this step**; head should not change.
4. Read `CLAUDE.md` fully. Focus on:
   - **D-24** (Repos never accept `tenant_id` for visibility purposes — relevant if you're tempted to widen any signature here).
   - **D-29** (PLATFORM RLS visibility via OR-clause). The correlated subqueries inherit RLS through the request's session GUCs; ordering on them is RLS-correct without extra work.
   - **D-30** (list-only response envelope). Unchanged in this step — `/tenants` already conforms.
   - **D-31** (response field semantics are append-only). Adding sort keys doesn't change the response shape; D-31 doesn't apply.
   - **Step 5.2's "Shared sort-key error classes"** (`InvalidSortKeyError` / `InvalidSortKeyClientError` in `repositories/_errors.py` and `errors.py`). These already raise on unknown sort keys; **no changes needed** — adding to the SORT_MAP just expands the accepted vocabulary.
5. Read `src/admin_backend/routers/v1/tenants.py` — specifically the `list_tenants` handler. The `sort` query parameter is the surface that needs widening. The handler doesn't change (it just passes `sort` through to the Repo); only the OpenAPI `description` for the param needs updating.
6. Read `src/admin_backend/repositories/tenants.py` — this is the only source file that meaningfully changes. Find:
   - The `TENANTS_SORT_MAP` (or equivalent) module-level dict. Its current keys are the six locked at Step 3.3.
   - The `list_with_aggregates(...)` method. It builds the two correlated subqueries for `num_users_active` and `num_stores` (the `.correlate(Tenant)` pattern from Step 3.3 L9 / Step 5.2 swap). Identify the exact variable / labeled column expressions — these are what the new sort keys will reference.
7. Read `tests/integration/test_tenants_router.py` — fixture machinery and the existing **L4 / L5** sort tests. New tests follow the same shape.
8. Read `tests/integration/conftest.py` — confirm `make_tenant`, `make_store`, `make_tenant_user` factories exist (they do; reused since Step 3.3). **No new factories needed.**
9. Read `docs/endpoints/tenants.md` — find the `GET /api/v1/tenants` section's "Query parameters" list where the existing sort keys are documented. The new keys go in this list with the same formatting.
10. Read `BUILD_PLAN.md` — find where Step 6.4 sits. Provisional: **Step 6.4** (between original Step 6.3 seeds and the new Step 6.5 dashboard stats). If slotted differently, surface and use the slotted ID.
11. Read `data/ithina_dev_seed_data.xlsx` — confirm seed shape: 7 tenants with varying `num_users_active` (Buc-ee's has 6, Żabka has more, etc.) and varying `num_stores` (Buc-ee's 3, others differ). Manual curl verification at the bottom of this prompt assumes this seeded data.
12. Read `scripts/smoke_curl.sh` — find the existing `/api/v1/tenants` assertions (likely a basic 200 + envelope check). One new assertion lands here for the new sort.
13. Read this prompt fully.

---

## Step ID and intent

**Step 6.4** — Tenants list aggregate sort keys. Extends the `sort` query parameter vocabulary on the existing `/api/v1/tenants` list endpoint to accept the two per-row aggregate columns.

**Endpoint touched (no new endpoint):**

| Method + path | Change |
|---|---|
| `GET /api/v1/tenants` | `sort` query param accepts 4 new values (`num_users_active_asc`, `num_users_active_desc`, `num_stores_asc`, `num_stores_desc`). Existing 6 values still accepted. Response shape unchanged. |

**Forward notes (NOT in scope this step):** none specific to this step. The follow-up dashboard endpoints land at Step 6.5.

**Concrete deliverables:**

1. Extend `TENANTS_SORT_MAP` in `repositories/tenants.py` with 4 new entries pointing at the two existing labeled subquery expressions.
2. Update the `sort` query parameter's `description` in `routers/v1/tenants.py` to list the new accepted values.
3. Update the relevant section of `docs/endpoints/tenants.md` to document the new sort keys.
4. 4 new integration tests in `tests/integration/test_tenants_router.py` — one per new key (asc + desc for each aggregate) — verifying the ordering works against seeded data.
5. 1 new assertion in `scripts/smoke_curl.sh` (the dashboard's panel call, exactly as the frontend will issue it).
6. **No migrations. No DDL changes. No seed Excel changes. No new schemas. No new Repos. No new error classes.**
7. CLAUDE.md update: add Step 6.4 Completed bullet.
8. BUILD_PLAN.md update: Step 6.4 entry.

CLAUDE_CODE step. Smallest endpoint-touching step in the build so far. Expect ~30 minutes.

---

## Locked sort key vocabulary

| Key | Direction | Aggregate | SQL ORDER BY clause |
|---|---|---|---|
| `num_users_active_asc` | ascending | active tenant users count | `<num_users_active_subq>.asc()` |
| `num_users_active_desc` | descending | active tenant users count | `<num_users_active_subq>.desc()` |
| `num_stores_asc` | ascending | stores count | `<num_stores_subq>.asc()` |
| `num_stores_desc` | descending | stores count | `<num_stores_subq>.desc()` |

Where `<num_users_active_subq>` and `<num_stores_subq>` are the correlated scalar subqueries already constructed inside `list_with_aggregates(...)`. **The subqueries themselves are not modified** — only referenced from the SORT_MAP.

**Stable secondary sort.** The existing pattern (`order_by = [SORT_MAP[sort], Tenant.id.asc()]`) preserves a deterministic secondary sort by `id`. New keys follow the same pattern — important when two tenants have the same `num_users_active` or `num_stores`, the `id` tiebreaker ensures pagination is stable.

**Default sort unchanged.** `created_at_desc` remains the default; this step adds vocabulary, doesn't change defaults.

---

## Locked SQL: subquery-as-ORDER-BY pattern

The two correlated subqueries already exist inside `list_with_aggregates(...)`:

```python
# Already there (from Step 3.3, with TenantUser stub swap at Step 5.2):
num_users_active_subq = (
    select(func.count(TenantUser.id))
    .where(TenantUser.tenant_id == Tenant.id)
    .where(TenantUser.status == TenantUserStatus.ACTIVE)
    .correlate(Tenant)
    .scalar_subquery()
    .label("num_users_active")
)

num_stores_subq = (
    select(func.count(Store.id))
    .where(Store.tenant_id == Tenant.id)
    .correlate(Tenant)
    .scalar_subquery()
    .label("num_stores")
)
```

The **labeled subqueries** are first-class column expressions — they can be referenced in `order_by(...)` directly:

```python
TENANTS_SORT_MAP = {
    # ...existing 6 entries unchanged...
    "num_users_active_asc": num_users_active_subq.asc(),
    "num_users_active_desc": num_users_active_subq.desc(),
    "num_stores_asc": num_stores_subq.asc(),
    "num_stores_desc": num_stores_subq.desc(),
}
```

**Investigation required.** The exact placement of the SORT_MAP relative to the subquery construction matters: if the SORT_MAP is module-level (constructed at import time) but the subqueries are built inside `list_with_aggregates(...)` (per-call), they can't be referenced from the module-level map directly. Three possible structures Claude Code may find:

1. **SORT_MAP is module-level + subqueries inside method.** SORT_MAP cannot reference per-call subqueries. Restructure: build SORT_MAP inside the method, after the subqueries are constructed. Or (cleaner): expose the column-builder lambdas in a module-level dict, resolved at call site.
2. **SORT_MAP is inside the method.** Just add the four entries.
3. **SORT_MAP is module-level with literal `Tenant.column` references.** The subqueries can't be added at module level. Build a per-call extension dict that merges with the module-level base.

Pick the structure that minimises diff against the existing code. If unclear, surface (Stop-and-ask trigger #2).

**Note on RLS correctness.** The correlated subqueries already inherit RLS via the session GUCs — `TenantUser.tenant_id == Tenant.id` is correlated to the outer row, but RLS on the inner `tenant_users` table further filters by the `app.tenant_id` GUC under TENANT JWTs. Result: ORDER BY on these subqueries is RLS-correct in both PLATFORM and TENANT contexts without extra logic. **Tests verify this** (T2 below).

---

## Files to create/modify

Claude Code investigates the existing codebase and writes the actual code. The contract above is locked; the implementation pattern follows existing precedents.

### `src/admin_backend/repositories/tenants.py` — modify

Locate `TENANTS_SORT_MAP` (or equivalent name). Add four entries per the locked vocabulary. If structural restructuring is needed (per the three structures listed above), do it minimally — preserve the shape and ordering of existing entries.

Verify the secondary stable sort still applies (`Tenant.id.asc()` appended). If the existing implementation has a different stable-sort pattern, follow it.

No other changes to the file. No new methods. No schema changes.

### `src/admin_backend/routers/v1/tenants.py` — modify

Update the `sort` query parameter's `description` text in the FastAPI `Query(...)` annotation to list the new accepted values. Mirror the formatting used by similar endpoints (`platform_users.py`, `tenant_users.py`).

No handler logic change.

### `tests/integration/test_tenants_router.py` — modify

Add 4 new tests after the existing L5 (or equivalent sort test), keeping the L-test ID convention. Suggested IDs: **L5a** through **L5d** if your existing tests run L1...L10, or **L11**...**L14** otherwise. Pick to fit the file's existing conventions.

**One LOAD-BEARING test:**

| ID | Verifies |
|---|---|
| **L5b** (or equivalent) | `GET /tenants?sort=num_users_active_desc&limit=5` returns the 5 tenants with the highest active-user counts in descending order. **Load-bearing because Step 6.5's Top Tenants panel depends on this exact query shape.** Insert tenants with known per-tenant user counts; assert the response order matches descending `num_users_active`. |

**Other tests:**

- L5a — `sort=num_users_active_asc` returns ascending order.
- L5c — `sort=num_stores_desc` returns descending stores order.
- L5d — `sort=num_stores_asc` returns ascending stores order.

Optional fifth test (informational):

- **L5e (RLS regression check)** — TENANT JWT calls `?sort=num_users_active_desc&limit=5`; result is at most 1 row (the calling tenant). Confirms ORDER BY on correlated subquery doesn't leak cross-tenant data. *Optional because existing L8-style RLS tests already cover the underlying `/tenants` RLS posture; this one specifically guards the new subquery-ORDER-BY interaction.*

Reuse `make_tenant`, `make_store`, `make_tenant_user` factories. No new conftest changes.

### `docs/endpoints/tenants.md` — modify

Find the `GET /api/v1/tenants` endpoint's "Query parameters" section. Locate the `sort` parameter row. Append the four new accepted values to the existing list (`created_at_asc`, etc.). Mirror formatting.

If the doc has a worked example showing a non-default sort, add (or extend) one example using `sort=num_users_active_desc&limit=5` — useful precedent for the dashboard team.

### `scripts/smoke_curl.sh` — modify

Add **one new assertion** matching the dashboard's exact panel call:

```bash
# Top tenants by active users (dashboard's Top Tenants panel — Step 6.5)
curl -fsS -H "Authorization: Bearer $PJWT" \
  "$BASE/api/v1/tenants?sort=num_users_active_desc&limit=5" \
  | jq -e '.items | length <= 5 and (length > 0)' >/dev/null \
  && echo "PASS: tenants num_users_active_desc sort" \
  || echo "FAIL: tenants num_users_active_desc sort"
```

Update the expected PASS count comment at the top of the file.

The other three workflow scripts (`scripts/deploy-cloud-run.sh`, `scripts/env.sh`, `scripts/jwt/generate_7d.sh`) — **no change.** Confirm in the report.

### CLAUDE.md — modify

- **Current state → Completed:** Step 6.4 bullet covering the 4 new sort keys, the +4 tests, the doc update, the `smoke_curl.sh` extension.
- **No new D-XX entries.**
- **No new FN-AB entries.**
- **Schema state line:** unchanged at 12 application tables. Smoke count unchanged at 74 (pytest smoke). `smoke_curl.sh` PASS count grows by +1.

### BUILD_PLAN.md — modify

Add Step 6.4 entry. Status: TODO → DONE in same commit. Standard scope-in / scope-out / acceptance criteria structure mirroring Steps 5.1 / 5.2 / 6.1.

The "Scope in" should explicitly call out: this step is a precondition for Step 6.5 (Dashboard stats). Step 6.5's BUILD_PLAN entry already lists Step 6.4 as having shipped before it; ensure the sequencing is correct.

### `prompts/step-6_4-tenants-aggregate-sort-keys-2026-05-05.md` — new

This prompt file. Bundled per the per-step convention.

### `docs/endpoints/openapi.json` — re-export

After all code is in and tests pass:

```bash
curl -s http://localhost:8000/api/v1/openapi.json | jq '.' > docs/endpoints/openapi.json
# Verify the sort param description in the regenerated spec lists the new values:
jq '.paths."/api/v1/tenants".get.parameters[] | select(.name == "sort") | .description' docs/endpoints/openapi.json
# Expected: contains "num_users_active_asc, num_users_active_desc, num_stores_asc, num_stores_desc"
```

### `docs/architecture.md` — no edit

This step doesn't change architecture.

---

## Testing and regression discipline

### New tests

4 new integration tests (one per new sort key); 1 load-bearing (L5b — the dashboard's exact query shape).

Optional 5th test (L5e) for RLS-on-correlated-subquery — recommend including; cheap insurance.

### Tests deliberately not added

- **OpenAPI schema validation tests.** The regenerated `openapi.json` snapshot is human-verified; no test asserts on its contents.
- **Unit tests on `TENANTS_SORT_MAP` directly.** The integration tests cover the contract (does this sort key produce ordered output); a unit test on the dict shape is redundant.
- **Sort-with-pagination tests for new keys.** Existing L7 (or equivalent) covers pagination semantics generally; the new keys aren't a different pagination contract.

### Regression risk surface

1. **Existing 22 tests in `test_tenants_router.py` must stay green.** Especially L4 / L5 (existing sort tests) and **L9** (correlated subquery scoping — load-bearing for the underlying subquery semantics this step extends). A drop in any existing test is a step-blocker.
2. **Stable secondary sort (`Tenant.id.asc()`) preserved.** If your restructuring of SORT_MAP accidentally drops this, pagination becomes nondeterministic — silent corruption that's hard to debug. Verify the order_by tuple has both elements.
3. **Subquery construction relative to SORT_MAP.** If the structural restructuring is botched (e.g., subqueries reconstructed per-call but SORT_MAP cached at module load), the SORT_MAP entries hold stale subquery references. Symptom: queries either fail with SA "unknown column" errors or return wrong results. Tests catch this immediately.
4. **OpenAPI description drift.** If the `description` text on the `sort` param isn't updated, the regenerated `openapi.json` lags behind the actual accepted values. Frontend codegen consumers wouldn't know the new keys exist. Manually verify the regenerated spec.

### Verification harness (run all seven; all must be green)

```bash
# 1. Full pytest
uv run pytest -v

# 2. Per-resource regression checkpoint (LOAD-BEARING)
uv run pytest tests/integration/test_tenants_router.py -v
# Expected: 22 prior + 4 new = 26 PASS (or +5 if L5e included = 27 PASS).
# Existing tests must not drop. L9 specifically must stay green.
uv run pytest tests/integration/test_platform_users_router.py -v
uv run pytest tests/integration/test_tenant_users_router.py -v
uv run pytest tests/integration/test_org_tree_router.py -v
uv run pytest tests/integration/test_rbac_router.py -v
# Each file must report 100% PASS at exactly its pre-step count.

# 3. mypy strict
uv run mypy --strict src/admin_backend
# Note: SA `.label("...")` then `.desc()` chains can sometimes confuse mypy.
# If new errors appear, the typing pattern from existing SORT_MAP entries
# (which already chain .asc()/.desc()) should resolve them; mirror it.

# 4. Pre-flight checker
./scripts/check_setup.sh

# 5. Alembic head unchanged (no migration)
uv run alembic heads
uv run alembic check

# 6. scripts/smoke_curl.sh — run against local dev (or post-deploy against Cloud Run)
bash scripts/smoke_curl.sh
# Expected: all PASS, count grows by +1 for the new num_users_active_desc assertion.

# 7. Manual curl verification
PJWT=$(uv run python -c "from admin_backend.auth.testing import make_test_jwt; print(make_test_jwt(user_type='PLATFORM'))")

# Top 5 by active users (the dashboard's exact call):
curl -s -H "Authorization: Bearer $PJWT" \
  "http://localhost:8000/api/v1/tenants?sort=num_users_active_desc&limit=5" \
  | jq '.items | map({name, num_users_active})'
# Expected: 5 items, descending num_users_active. With seed data the order is
# roughly Żabka > Infomil > Buc-ee's > GreenLeaf > SmartStore (or the relative
# order matching the seed Excel's user counts).

# Top 5 by stores:
curl -s -H "Authorization: Bearer $PJWT" \
  "http://localhost:8000/api/v1/tenants?sort=num_stores_desc&limit=5" \
  | jq '.items | map({name, num_stores})'
# Expected: 5 items, descending num_stores.

# Asc variants:
curl -s -H "Authorization: Bearer $PJWT" \
  "http://localhost:8000/api/v1/tenants?sort=num_users_active_asc&limit=3" \
  | jq '.items | map({name, num_users_active})'
# Expected: 3 items, ascending num_users_active.

# Backwards compat — existing sort still works:
curl -s -H "Authorization: Bearer $PJWT" \
  "http://localhost:8000/api/v1/tenants?sort=name_asc&limit=3" \
  | jq '.items | map(.name)'
# Expected: 3 items, alphabetical by name.

# Invalid sort still rejected:
curl -s -H "Authorization: Bearer $PJWT" \
  "http://localhost:8000/api/v1/tenants?sort=garbage_desc"
# Expected: 400 INVALID_SORT_KEY.
```

If any leg is not green, **report the failure rather than the step.**

---

## Scope out

- **No new endpoints.** Extending an existing endpoint's sort vocabulary, not adding a route.
- **No changes to the Repo's filtering / pagination / search.** Only the sort vocabulary widens.
- **No `num_org_nodes_*` sort keys.** Org-tree aggregation isn't on `/tenants` (it's its own dedicated endpoint at Step 5.3); not in scope here.
- **No changes to the `tier` / `search` / `limit` / `offset` parameters.** All unchanged.
- **No changes to the response shape.** `TenantsListResponse` and `TenantsListItem` unchanged.
- **No new sort keys for `tenant-users` / `platform-users` / `roles` endpoints.** Each list endpoint has its own SORT_MAP; widening other resources' sort vocabularies is not part of this step.
- **No caching.** Sort by aggregate is sub-millisecond at v0 fleet scale (7 tenants).
- **Top Stores panel for tenant-side dashboard.** That panel doesn't exist yet (it's a future Tenant Owner dashboard concern); when it ships, the relevant endpoint is `/stores` (Step 4.5 territory) with its own sort keys, not `/tenants`.

---

## Stop and ask if

1. **`TENANTS_SORT_MAP` doesn't exist with the expected structure.** If the variable is named differently (e.g., `_SORT_MAP`, `sort_clauses`, etc.), use the actual name — but if the *structural pattern* is materially different (e.g., sort is handled via if/elif chain rather than dict, or the keys aren't `<column>_<direction>`), surface — the locked vocabulary may need adjustment to fit.

2. **Subquery placement vs SORT_MAP placement requires non-trivial restructuring.** Per the three structures listed in §"Locked SQL" — if the existing code has SORT_MAP at module level and subqueries inside the method, restructuring needs care (e.g., move SORT_MAP construction inside the method, or expose subquery-builder lambdas at module level). Surface the proposed restructuring for confirmation before committing to either approach.

3. **mypy --strict reports type errors on the new SORT_MAP entries.** SA's typing for labeled subqueries → `.asc()` / `.desc()` can be flaky. The existing entries (`Tenant.created_at.desc()` etc.) presumably type cleanly; if the labeled-subquery variants don't, surface — we may need a `cast(...)` or `# type: ignore[no-untyped-call]` per the codebase's existing typing-suppression conventions.

4. **The existing L9 test (correlated subquery scoping via `.correlate(Tenant)`) fails after your changes.** This is the load-bearing regression test for the underlying subquery pattern; failure here means the restructuring broke something fundamental. Halt and report rather than proceeding to commit.

5. **Step 6.5 prompt expects a sort key not in this step's vocabulary.** Re-read the Step 6.5 prompt's references to `/tenants` sort keys. If 6.5 expects `num_users_active_desc` (it does, per scope-out), this step's vocabulary covers it. If 6.5 references something else (e.g., a `num_modules_desc` for a hypothetical future panel), surface — we'll either widen this step or defer.

6. **`docs/endpoints/tenants.md` doesn't have a "Query parameters" section in the format expected.** Some steps may have evolved the doc structure since Step 3.3. If the doc layout differs, follow whatever pattern is current; preserve consistency over imposing an old structure.

---

## Acceptance criteria

- 8 files modified per scope above (4 source + 1 test + 1 doc + 1 script + 1 prompt; plus CLAUDE.md and BUILD_PLAN.md updates).
- 4 new sort keys live: `num_users_active_asc`, `num_users_active_desc`, `num_stores_asc`, `num_stores_desc`.
- Existing 6 sort keys still accepted; backwards compat preserved.
- For seed-loaded data:
  - `GET /tenants?sort=num_users_active_desc&limit=5` returns 5 items in descending active-user-count order.
  - `GET /tenants?sort=num_stores_desc&limit=5` returns 5 items in descending stores order.
  - `GET /tenants?sort=num_users_active_asc&limit=3` returns 3 items in ascending active-user-count order.
  - `GET /tenants?sort=garbage_desc` still returns 400 INVALID_SORT_KEY.
- 4 (or 5) new integration tests; the load-bearing **L5b** test explicitly green.
- All ~22 existing `test_tenants_router.py` tests pass at their pre-step count. **A drop is a step-blocker — particularly L9 (correlated subquery scoping).**
- Per-resource regression checkpoint: every prior router file at exactly its pre-step PASS count.
- mypy strict clean.
- check_setup 35/35.
- pytest smoke (`scripts/smoke_test.py`) unchanged at 74 PASS.
- `scripts/smoke_curl.sh` updated: 1 new assertion. Expected PASS count grows by +1. `bash scripts/smoke_curl.sh` returns all PASS.
- The other three workflow scripts (`scripts/deploy-cloud-run.sh`, `scripts/env.sh`, `scripts/jwt/generate_7d.sh`) — unchanged, confirmed in the report.
- Alembic head unchanged. No migration. `alembic check` clean.
- `docs/endpoints/tenants.md` lists the new sort keys.
- OpenAPI spec quality: regenerated `openapi.json` reflects the updated `sort` parameter description.

---

## Report (BEFORE proposing commit)

Six bundles per the convention:

1. **Code:** files modified with line counts; manual curl outputs verifying the 4 new sort keys against seeded data; backwards-compat curl on an existing sort key (e.g., `name_asc`) showing it still works. **Workflow scripts:** `scripts/smoke_curl.sh` delta (+1 assertion, new expected PASS count); explicit "no change" confirmation for `scripts/deploy-cloud-run.sh`, `scripts/env.sh`, `scripts/jwt/generate_7d.sh`.
2. **CLAUDE.md updates:** Step 6.4 Completed bullet; no new D-XX or FN-AB.
3. **BUILD_PLAN.md updates:** Step 6.4 entry; cross-link confirming this is the precondition for Step 6.5.
4. **architecture.md updates:** "no change."
5. **OpenAPI spec snapshot:** `docs/endpoints/openapi.json` regenerated; verify the `sort` parameter description on `/tenants` lists all 10 accepted values (6 existing + 4 new).
6. **Prompt file:** `prompts/step-6_4-tenants-aggregate-sort-keys-2026-05-05.md` confirmed in commit set.

Plus: pytest count delta (+4 or +5); per-file regression numbers confirming each at 100% PASS with no count drop; mypy status; check_setup; alembic head unchanged.

Wait for explicit authorisation before staging or committing.

---

## After completing

Propose a git commit per CLAUDE.md "After completing a task" Pattern A:

```
git status
git add -A
git commit -m "Step 6.4: Tenants list aggregate sort keys

- Extends GET /api/v1/tenants 'sort' query parameter vocabulary with 4
  new keys covering the existing per-row aggregates:
  - num_users_active_asc / num_users_active_desc
  - num_stores_asc / num_stores_desc
- TENANTS_SORT_MAP gains 4 entries pointing at the existing correlated
  subqueries (.correlate(Tenant)). No new subqueries; no new methods.
- Existing 6 sort keys (created_at_*, name_*, tier_*) still accepted.
- 4 new integration tests in test_tenants_router.py; 1 load-bearing
  (L5b — num_users_active_desc + limit=5, the exact query shape Step
  6.5's Top Tenants panel will use).
- scripts/smoke_curl.sh: +1 assertion for num_users_active_desc.
- docs/endpoints/tenants.md: 4 new values listed under sort param.
- No migrations. No DDL changes. No seed Excel changes. No new schemas.
- Precondition for Step 6.5 (Dashboard stats endpoints) which calls
  /tenants?sort=num_users_active_desc&limit=5 from the dashboard's
  Top Tenants panel."
```

Ask user "Run? yes / no / edit message". On yes, execute via bash tool. On no, skip. On edit, prompt for new message.

---

## End of prompt
