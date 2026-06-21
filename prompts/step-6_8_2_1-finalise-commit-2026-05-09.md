# Step 6.8.2.1 — finalise commit

Operative word: caution. All implementation work is done by the operator manually; Claude Code's job is verification, doc updates, and commit hygiene.

## What the operator already did

Manually edited `data/ithina_dev_seed_data.xlsx`:
- `permissions` sheet: appended 7 rows (p28 through p34) for the following codes:
  - ADMIN.STORES.VIEW.TENANT
  - ADMIN.STORES.CONFIGURE.TENANT
  - ADMIN.ORG_NODES.VIEW.TENANT
  - ADMIN.ORG_NODES.CONFIGURE.TENANT
  - ADMIN.USERS.VIEW.GLOBAL
  - ADMIN.ROLES.CONFIGURE.GLOBAL
  - ADMIN.USERS.CONFIGURE.GLOBAL
- `role_permissions` sheet: appended 7 rows linking SUPER_ADMIN (`r_super_admin`, role_id `f10c718b-1eb0-438a-a75d-d5af3c365296`) to each new permission.

Manually edited `tests/integration/test_seed_loader.py`:
- `EXPECTED_VISIBLE_COUNTS_PLATFORM["permissions"]`: 23 → 30
- `EXPECTED_VISIBLE_COUNTS_PLATFORM["role_permissions"]`: 113 → 120

Manually edited `tests/integration/test_rbac_router.py`:
- `test_p4_tenant_jwt_sees_full_catalogue` (around line 490): the unseeded permission tuple changed from `("ADMIN", "STORES", "CONFIGURE", "TENANT")` to `("ADMIN", "STORES", "EXECUTE", "STORE")`. Reason: `STORES.CONFIGURE.TENANT` is now seeded; collision with unique constraint.
- `test_rp1_returns_role_permissions_with_parent_echo` (around line 531): unseeded tuples for perm1 and perm2 changed from `("ADMIN", "ORG_NODES", "VIEW", "TENANT")` and `("ADMIN", "ORG_NODES", "CONFIGURE", "TENANT")` to `("ADMIN", "ORG_NODES", "EXECUTE", "STORE")` and `("ADMIN", "ORG_NODES", "AUDIT", "STORE")`. Reason: same as above. Comment in the test was also updated to reflect the new tuples.

Verified locally:
- `uv run python -m scripts.seed_dev_data --reset` → loaded 30 permissions, 120 role_permissions, 22 user_role_assignments. No errors.
- `uv run pytest tests/integration/test_seed_loader.py -v` → 5 passed.
- `uv run pytest --tb=no -q | tail -5` → 263 passed (matches post-6.8.3 baseline).
- DBeaver confirms 30 permissions, 120 role_permissions in DB.

## Pre-flight

1. `git status` and confirm only these 4 files are modified (plus the pre-existing in-progress items the operator already knows about):
   - `data/ithina_dev_seed_data.xlsx`
   - `tests/integration/test_seed_loader.py`
   - `tests/integration/test_rbac_router.py`
   - (the operator may have already-modified docs/build-step-workflow.md as a pre-existing in-progress item; do NOT stage it)

   Pre-existing in-progress items (do NOT stage):
   - `docs/build-step-workflow.md`
   - `db/scripts/` (untracked)
   - `reports/step-6_8_1-report.md` (untracked)
   - `reports/step-6_8_2-readonly-investigation.md` (untracked)
   - `scripts/test_endpoints_max_view.sh` (untracked)

   If anything else appears modified or untracked, surface and stop.

2. `git log --oneline -5` and confirm HEAD is `b72e2d3 Step 6.8.3: ...`. (The 6.8.3 commit landed earlier today.)

3. `uv run pytest --tb=no -q | tail -5` → expect 263 passed.

## Tasks

1. **Update `CLAUDE.md`** — add a "Step 6.8.2.1 — Completed" entry under the appropriate section. Keep it concise; mirror the style of recent completed-step bullets. Capture:
   - 7 ADMIN-domain permissions added to seed Excel and granted to SUPER_ADMIN.
   - List the 7 codes.
   - EXPECTED_VISIBLE_COUNTS_PLATFORM bumps: permissions 23 → 30, role_permissions 113 → 120.
   - Two test_rbac_router.py test fixtures repaired (P4 and RP1) — picked unseeded slots that don't collide with the new catalogue.
   - pytest unchanged at 263 (no new tests; same count as post-6.8.3).
   - Hard precondition for Step 6.8 (RBAC enforcement layer) — without these grants, SUPER_ADMIN becomes structurally underprivileged the moment the resolver gates ADMIN-domain writes.

2. **Update `BUILD_PLAN.md`** — find the appropriate place for Step 6.8.2.1 (likely under Section 6.8 alongside 6.8.1, 6.8.2, 6.8.3). Add a Step 6.8.2.1 entry with:
   - Status: DONE.
   - Scope as above (concise).
   - The 7 codes listed.
   - Note that this resolves the supplementary-permissions concern that was tracked in FN-AB-19 (added during Step 6.8.3); update FN-AB-19 to status RESOLVED at Step 6.8.2.1.

3. **architecture.md** — almost certainly no change. Verify by `grep -n "permission\|role_permission" docs/architecture.md`. Report "no change" or describe.

## Verification

```bash
# 1. Tests still green
uv run pytest --tb=no -q | tail -5
# Expected: 263 passed.

# 2. Smoke
./scripts/test_endpoints.sh
# Expected: same count as post-6.8.3 (was 248 passed in that step; expect identical).

# 3. mypy
uv run mypy src/admin_backend/
# Expected: clean.

# 4. check_setup
./scripts/check_setup.sh
# Expected: 35/35.
```

## Stop and ask if

1. Pre-flight pytest count ≠ 263. Surface immediately.
2. Pre-flight DB row counts diverge from `permissions=30, role_permissions=120` after a fresh reseed.
3. CLAUDE.md / BUILD_PLAN.md don't have an obvious place for Step 6.8.2.1 — surface and propose a location.
4. FN-AB-19 doesn't exist in BUILD_PLAN.md (it was added during 6.8.3; verify it's there before marking RESOLVED).
5. Smoke or mypy regress from post-6.8.3 baseline.

## Report (BEFORE staging)

1. `git status` output (confirm only the 4 expected files modified).
2. `git diff --stat` for the 4 files plus the 2 doc files (CLAUDE.md, BUILD_PLAN.md).
3. CLAUDE.md and BUILD_PLAN.md additions (full text of new bullets).
4. architecture.md status: "no change" or describe.
5. Verification harness output (4 sections).
6. Pytest count: 263 (unchanged from post-6.8.3).

Wait for explicit operator authorisation before staging or committing.

## Commit message template

```
Step 6.8.2.1: complete SUPER_ADMIN permission grants for ADMIN domain

Add 7 ADMIN-domain permissions to data/ithina_dev_seed_data.xlsx
permissions sheet and grant each to SUPER_ADMIN via role_permissions:

- ADMIN.STORES.VIEW.TENANT
- ADMIN.STORES.CONFIGURE.TENANT
- ADMIN.ORG_NODES.VIEW.TENANT
- ADMIN.ORG_NODES.CONFIGURE.TENANT
- ADMIN.USERS.VIEW.GLOBAL
- ADMIN.ROLES.CONFIGURE.GLOBAL
- ADMIN.USERS.CONFIGURE.GLOBAL

Hard precondition for Step 6.8 (RBAC enforcement layer): without these
grants SUPER_ADMIN becomes structurally underprivileged the moment the
resolver gates ADMIN-domain writes. Resolves FN-AB-19.

EXPECTED_VISIBLE_COUNTS_PLATFORM updated:
- permissions: 23 -> 30
- role_permissions: 113 -> 120

Two test fixtures in test_rbac_router.py repaired (the P4 and RP1
tests previously assumed STORES.CONFIGURE.TENANT and ORG_NODES.{VIEW,
CONFIGURE}.TENANT were unseeded; both are now in the catalogue).
Repointed at unseeded slots:
- P4: ADMIN.STORES.EXECUTE.STORE
- RP1: ADMIN.ORG_NODES.EXECUTE.STORE and ADMIN.ORG_NODES.AUDIT.STORE

pytest: 263 -> 263 (no test count change; updated EXPECTED dict and
two fixture tuples).
Smoke: unchanged from post-6.8.3 baseline.
alembic: unchanged (no migration).
mypy: clean.

CLAUDE.md / BUILD_PLAN.md: 6.8.2.1 entry added; FN-AB-19 marked RESOLVED.
architecture.md: <no change | describe edit>.
```

Use explicit `git add`:

```bash
git add \
  data/ithina_dev_seed_data.xlsx \
  tests/integration/test_seed_loader.py \
  tests/integration/test_rbac_router.py \
  CLAUDE.md \
  BUILD_PLAN.md
```

Add `architecture.md` only if it actually changed. Do NOT use `git add -A` — preserve the pre-existing in-progress items.

After commit lands, STOP and report the commit hash. Do not push to origin/main; the operator will do that after the housekeeping commit.
