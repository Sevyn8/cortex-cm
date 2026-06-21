# Step 6.20.1: TenantsRepo.create provisions tenant-root org_node

**Date.** 2026-05-18.
**Owner.** Claude Code.
**Status.** DONE-LOCAL (single commit on `main`).
**Prompt.** `prompts/step-6_20_1-impl-2026-05-18.md`.

---

## Mental Model

**Bug shape.** The documented invariant "tenant roots are provisioned with the tenant" (e.g. `OrgNodeCreateRequest` description: "tenant roots are provisioned with the tenant") wasn't implemented in `TenantsRepo.create` at Step 6.11.2. The seed loader honoured it; POST didn't. Every endpoint gated on `anchor_dep=get_tenant_anchor` (GET detail, PATCH, suspend, activate, org-tree, module-access enable/disable) raised `TenantNotFoundError` 404 for POST-created tenants because the anchor query missed.

**Masking factor.** Every integration test uses `make_tenant(with_root=True)` (a fixture parameter added post-Step-6.15 for exactly this kind of asymmetry, per FN-AB-46 in CLAUDE.md). The bug never surfaced in pytest. It was caught in a production-like cloud env by a real frontend POST → GET sequence (reported 2026-05-18 with tenant id `019e3bf9-ac6a-7e8f-a4e5-316ebc9d4759`).

**Fix shape.** Extend the existing `create()` transaction with one more INSERT. Pure-function helper extracts the `(code, path)` derivation for unit testability. Empty-slug edge case becomes a domain-shaped 422. No surface changes to the request body, no migration, no cross-resource ripple.

**Why this works.** `org_nodes.path` is ltree-castable; `code` conforms to the DDL CHECK regex by construction of the slug rule; tenant-root collision is structurally impossible at insert time (`uq_org_nodes_tenant_code_lower` is tenant-scoped and the tenant has no other nodes yet); audit-actor pattern follows Step 6.13's `OrgNodesRepo.add_node` precedent verbatim.

---

## DDL facts

`core.org_nodes` columns relied on for the tenant-root insert:

- `id` UUID DEFAULT `core.uuidv7()`.
- `tenant_id` UUID NOT NULL — FK to `core.tenants(id)`.
- `parent_id` UUID nullable — composite FK to `core.org_nodes(tenant_id, id)`.
- `path` ltree NOT NULL — explicit `CAST(:path AS ltree)` in SQL.
- `node_type` `core.org_node_type_enum` NOT NULL — `'TENANT'` for the root.
- `name` TEXT NOT NULL (length 1-200 per `ck_org_nodes_name_length`).
- `code` TEXT NOT NULL — `ck_org_nodes_code_format` regex `^[A-Za-z0-9][A-Za-z0-9-]{0,62}[A-Za-z0-9]$` OR `length(code) = 1`.
- `status` `core.org_node_status_enum` NOT NULL DEFAULT `'ACTIVE'` — explicit `'ACTIVE'` in the INSERT for clarity.
- `created_by_user_id`, `created_by_user_type`, `updated_by_user_id`, `updated_by_user_type` — Pattern (b) per D-13.

CHECK constraints relied on:

- `ck_org_nodes_root_parent_consistency`: `(node_type='TENANT' AND parent_id IS NULL) OR (node_type<>'TENANT' AND parent_id IS NOT NULL)` — the load-bearing structural invariant.
- `ck_org_nodes_created_by_actor_pair` and `ck_org_nodes_updated_by_actor_pair`: both-NULL or both-NOT-NULL.
- `ck_org_nodes_code_format` (above).

Unique indexes:

- `uq_org_nodes_tenant_code_lower` UNIQUE `(tenant_id, lower(code))` — tenant-scoped; collision on tenant-root insert is structurally unreachable (no other rows in the same tenant exist yet).

---

## Design pointer

See `docs/architecture.md` Appendix A.3 for the tenant create transaction shape and slug derivation rule. The Appendix lays out the three-table atomicity (`tenants`, `org_nodes`, `tenant_module_access`) and the slug rule with examples.

---

## Plan (final at Phase 5)

Refined LD2: slug derivation BEFORE the `tenants` INSERT so a 422 leaves no partial state behind.

1. **errors.py.** Add `InvalidTenantNameForSlugError` (422, `INVALID_TENANT_NAME_FOR_SLUG`). Constructor accepts `field: Literal["name", "display_code"]`; placed in `exc.context` per the Q7 envelope convention.
2. **repositories/tenants.py.**
   a. Imports: `re`, `unicodedata`, `InvalidTenantNameForSlugError`.
   b. Add module-level `slug_for_tenant_root(name, display_code) -> tuple[str, str]` pure helper per LD3 rule.
   c. Extend `create()`: insert `slug_for_tenant_root(name, display_code)` call after `_raise_if_name_taken` and before the `tenants` INSERT; insert an `INSERT INTO core.org_nodes` block between the `tenants` INSERT and the per-module loop.
3. **Tests.**
   a. NEW `tests/unit/test_tenant_root_slug.py` (10 tests; pure-function, no DB).
   b. MODIFY `tests/integration/test_tenants_repo_writes.py`:
      - Update `cleanup_tenants` fixture to DELETE `org_nodes` before `tenants`.
      - Append 5 tests including the LOAD-BEARING `test_create_inserts_tenant_root_org_node`.
   c. MODIFY `tests/integration/test_tenants_writes_router.py`:
      - Update `cleanup_tenants_router` fixture to DELETE `org_nodes` before `tenants`.
      - Append LOAD-BEARING `test_post_then_get_roundtrip`.
4. **Smoke scripts.**
   - `scripts/smoke_curl.sh`: WHAT'S CHECKED 54 → 55; add GET roundtrip assertion immediately after POST success (before PATCH).
   - `scripts/test_endpoints.sh`: add `write_flow__post_get_roundtrip` GET after `write_flow__create`.
   - `scripts/test_endpoints_cloud.sh`: mirror.
5. **Docs.**
   - `docs/architecture.md`: new Appendix A.3 (tenant create transaction shape + slug rule) before the Cross-references section.
   - `docs/endpoints/tenants.md`: POST section gains side-effect note in the Description line and a new 422 row in Response codes table.
   - `docs/endpoints/openapi.json`: regen with `ensure_ascii=False`, 4-space indent. Net-zero diff because no new endpoint or schema is declared.
6. **Planning docs.**
   - `BUILD_PLAN.md`: new `### Step 6.20.1` sub-entry under existing `### 6.20 Bug Fixes` parent (operator placed the parent heading at level 3 without `Step` prefix; sub-entry aligns with that level).
   - `CLAUDE.md`: pointer in `### Completed`; FN-AB-54 added (slug-truncation collision risk; structurally unreachable at v0).
7. **This step doc.** NEW.
8. **Verification.** check_setup, pytest (full suite, expect baseline + 16), mypy --strict src/admin_backend, smoke_curl, per-resource regression checkpoint.
9. **Phase 5 exit.** Per operator pre-authorisation ("execute bucket-by-bucket including the commit"), proceed directly to commit. Report commit hash.

---

## Retro

### What worked

- **Surface-and-stop on BUILD_PLAN parent heading.** Operator placed `### 6.20 Bug Fixes` at level 3 without the `Step` prefix specified by the prompt's check #10 (`## Step 6.20 - Bug Fixes`). I surfaced the deviation in the pre-flight report and aligned the sub-entry to the operator's level rather than introducing a level-2 parent. Reading the existing BUILD_PLAN structure (level-2 for `## Step X` headings, level-3 for `### Step X.Y` sub-entries) confirmed the operator's level-3 placement is consistent with how `Step 6.17.x` sub-entries sit under their `## Step 6.17` parent.
- **Refined LD2 was right.** Putting slug derivation BEFORE the `tenants` INSERT means an empty-slug 422 leaves no partial state — no orphan `tenants` row to clean up. The original LD2 ordering (slug call between INSERTs) would have produced exactly the orphan-tenant class of bug this step was fixing.
- **Per-resource regression checkpoint was load-bearing.** Two existing cleanup fixtures (`cleanup_tenants` in repo-writes, `cleanup_tenants_router` in router-writes) had to be updated to DELETE org_nodes before tenants. Catching this in the same commit kept the test suite green throughout; missing it would have leaked rows and produced flake on subsequent runs.

### What diverged from plan

- **Openapi.json regen was net-zero.** The prompt's plan called for regen + commit. The regen produced no functional diff because Step 6.20.1 doesn't introduce a new endpoint or schema — the new `InvalidTenantNameForSlugError` is internal to the handler's behaviour and FastAPI's auto-OpenAPI doesn't enumerate raised-exception types without explicit `responses=` declarations on the route. Regen still ran (precedent + future-proof against subtle ordering drift); nothing landed in the diff.
- **Two cleanup fixtures needed updating, not one.** The prompt listed `repositories/tenants.py` + the three test files but didn't call out that the existing `cleanup_tenants` and `cleanup_tenants_router` fixtures both DELETE `tenants` directly (without first clearing org_nodes). After my code change, the org_node FK ON DELETE RESTRICT would have failed teardown for every existing C/U/T test in `test_tenants_repo_writes.py` and every POST-using test in `test_tenants_writes_router.py`. I caught this by running the existing test suite immediately after the code change and adding the org_node DELETE to both fixtures in the same commit.

### Surface-and-stop findings

- **Pre-flight check #2 (working tree)**: `BUILD_PLAN.md` already modified by operator with the `### 6.20 Bug Fixes` parent heading. Matches the prompt's "operator-placed amendment file only" allowance. Three untracked files (one prompt + two prior-step artifacts) ignored per the same allowance.
- **Pre-flight check #10**: parent heading present at level 3 instead of level 2 with `Step` prefix. Surfaced and proceeded; sub-entry aligns with operator's level. Operator can revise heading shape in a future commit if desired; this step doesn't touch that decision.

### Code-volume gate

- `src/admin_backend/errors.py`: +20 lines.
- `src/admin_backend/repositories/tenants.py`: +57 lines (helper +47, create extension +37, imports +2 — minus 5 minor renumbering).
- `tests/unit/test_tenant_root_slug.py`: +117 lines (NEW).
- `tests/integration/test_tenants_repo_writes.py`: +178 lines (+5 tests + cleanup-fixture update).
- `tests/integration/test_tenants_writes_router.py`: +50 lines (+1 test + cleanup-fixture update).
- `scripts/smoke_curl.sh`: +30 lines.
- `scripts/test_endpoints.sh`: +5 lines.
- `scripts/test_endpoints_cloud.sh`: +5 lines.
- Docs: `architecture.md` +35, `tenants.md` +5.

Total: ~500 lines of new content across code + tests + scripts + docs. Within the "extend existing seam" code-volume profile; no architectural ripple.

### Deferred items confirmed

Per Scope out:

- Backfill production tenants: DONE (10 cloud + 1 local orphans deleted in single transactions pre-step).
- Slug-conflict resolution: tracked as FN-AB-54; structurally unreachable at v0.
- Editorial short-codes for POST-created tenants: deferred to future PATCH-on-tenant-root surface. Mechanical slug produces `BUC-EE-S` not seed's curated `BUC-EES`.
- Tightening regex on `code`: not needed; current slug rule produces DDL-compliant output.

### Post-deploy notes

To be filled at Phase 7 if any cloud-emergent lessons surface.

---

## Summary

**Code surface change.** One new pure-function helper, one new error class, one INSERT block added to an existing transaction in one Repo method.

**Wire contract change.** None. New 422 error code (`INVALID_TENANT_NAME_FOR_SLUG`) is documented but only fires for an input class (all-non-alphanumeric name) that was previously rejected as a 500 or accepted silently — neither was correct. The frontend's standard error-envelope handling accepts the new code without any client-side change.

**Risk class.** Low. Transaction is unchanged structurally (still atomic); the new INSERT writes to a column-complete row whose shape mirrors the seed loader's tenant-root rows verbatim. The load-bearing test (`test_create_inserts_tenant_root_org_node`) locks the contract at the seam.
