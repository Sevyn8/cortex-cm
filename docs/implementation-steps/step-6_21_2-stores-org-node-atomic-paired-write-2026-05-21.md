# Step 6.21.2 : Store ↔ org_node atomic-pair write surface

**Status.** DONE-LOCAL (2026-05-21).
**Owner.** CLAUDE_CODE (impl).
**Blocked by.** None.

## Mental Model

Two seams in v0 represent one logical entity across two physical tables. Step 6.20.1 established the first (tenant + tenant-root org_node). Step 6.21.2 establishes the second (store + STORE-type org_node) and codifies the general principle as architecture.md § A.4 / CLAUDE.md D-36.

Investigation `docs/investigations/2026-05-20-write-surface-coupling.md` identified Gap B: pre-6.21.2 POST `/api/v1/stores` accepted `org_node_id` as optional (default NULL); POST `/org-tree` could create STORE-type nodes with no paired `stores` row. The two endpoints produced disjoint populations rather than the 1:1 paired pattern the schema implied.

This step closes Gap B with three coordinated changes:

1. **POST `/api/v1/stores` becomes the atomic-pair entry point.** Required body field `parent_org_node_id` replaces the optional `org_node_id`. The server creates both the `stores` row AND the paired STORE-type `org_nodes` row in one transaction.
2. **PATCH `/api/v1/stores/{store_id}` cascades shared-field changes** (name, store_code, parent_org_node_id) atomically. POST `/stores/{store_id}/set-status` cascades store status to the paired org_node's status + `archived_*` triplet via the `STORE_STATUS_TO_ORG_NODE_STATUS` map.
3. **POST `/org-tree` rejects `node_type='STORE'`** (422); PATCH `/org-tree` on STORE-type targets rejects shared fields `name` and `code` (reparent stays allowed; parent ownership is dual-endpoint per architecture.md § A.5).

A DDL migration tightens `core.stores.org_node_id` to NOT NULL, closing the schema invariant the architecture now guarantees by construction.

## Implementation Plan

### Locked decisions (operator-confirmed pre-flight)

- **LD1.** POST `/stores` body: `org_node_id` removed; `parent_org_node_id: UUID` required.
- **LD2.** PATCH `/stores` body: optional `parent_org_node_id: UUID`.
- **LD3.** `StoresRepo.create` performs atomic paired write inside one transaction.
- **LD4.** `StoresRepo.update` cascades name/store_code/parent_org_node_id to paired org_node.
- **LD5.** `StoresRepo.transition` cascades status via `STORE_STATUS_TO_ORG_NODE_STATUS` and `OrgNodesRepo.set_status`.
- **LD6.** `STORE_STATUS_TO_ORG_NODE_STATUS` is a module-level dict in `repositories/stores.py`.
- **LD7.** POST `/org-tree` rejects `node_type='STORE'` via the existing `OrgNodeCreateRequest._reject_forbidden_node_types` validator (extended from the TENANT-only rejection).
- **LD8.** PATCH `/org-tree` on STORE-type target rejects shared fields (`name`, `code` only); `status` is not in the patch schema and is covered by `extra="forbid"`.
- **LD9.** New error class `OrgNodeFieldNotAllowedForTypeError` (422, ClientError subclass with `**context`).
- **LD10.** Dropped. V8 mirrors V1's generic Pydantic 422 (no dedicated wire code per pre-flight Check #6 finding).
- **LD11.** `_check_parent_node_for_store` replaces `_check_org_node_for_store`. Two failure paths: `ParentNodeNotFoundError` (404) and `InvalidParentNodeTypeError` (422).
- **LD12.** `OrgNodeNotForStoreError` retired entirely.
- **LD13.** Migration `34f515cbc63a` ALTER TABLE `core.stores` ALTER COLUMN `org_node_id` SET NOT NULL.
- **LD14.** CLAUDE.md D-36 codifies the two-table-one-entity pattern.
- **LD15.** architecture.md § A.4 (general principle) and § A.5 (this seam) inserted after § A.3.
- **LD16.** Test catalogue: see Test catalogue subsection below.

### Deviations from the original prompt (resolved at pre-flight)

The pre-flight surfaced seven deviations; the operator's authorisation resolved each:

- **Deviation #1.** Out-of-scope working tree items (modified Excel, untracked investigation/prompt files) carry forward; not staged in this commit.
- **Deviation #2.** LD8 narrows from `{name, code, status}` to `{name, code}`. `status` is already 422-rejected via Pydantic `extra="forbid"` on `OrgNodePatchRequest`. E15 dropped from the catalogue.
- **Deviation #3.** Module-level singleton `_org_nodes_repo = OrgNodesRepo()` in `repositories/stores.py`, matching the established pattern in `routers/v1/org_tree.py:101`.
- **Deviation #4.** New `OrgNodesRepo.set_status` method, NOT extending `edit_node`. Signature mirrors `StoresRepo.transition` (target_status + auth); handles `archived_*` triplet symmetric to stores `closed_*`.
- **Deviation #5.** `OrgNodeFieldNotAllowedForTypeError` follows the actual ClientError base class pattern (public_message / http_status / code class attributes; `**context` for log-only structured detail).
- **Deviation #6.** LD12 retirement is broader than the prompt anticipated: 3 existing tests reference `OrgNodeNotForStoreError`. C5/C6 update assertions to `ParentNodeNotFoundError`; C7 deleted (already-linked case structurally unreachable); RC10 also deleted (same reason). `_base_create_kwargs` helper renamed `org_node_id` -> `parent_org_node_id`; ~17 C/U test sites updated; `auth: AuthContext` replaces `actor_user_id`/`actor_user_type` on `StoresRepo.{create,update,transition}` signatures.
- **Deviation #7.** PW6 split into PW6a (store-vs-store collision -> 409 `DUPLICATE_STORE_CODE`) and PW6b (store-vs-orgnode cascade collision -> 409 `DUPLICATE_ORG_NODE_CODE`).

### Files touched

Source code (8 files):
- `src/admin_backend/schemas/store.py`: `StoreCreateRequest`/`StorePatchRequest` body fields updated.
- `src/admin_backend/schemas/org_node.py`: `_reject_forbidden_node_types` extended.
- `src/admin_backend/errors.py`: `OrgNodeFieldNotAllowedForTypeError` added; `OrgNodeNotForStoreError` removed.
- `src/admin_backend/repositories/stores.py`: module-level singleton + mapping; `_check_parent_node_for_store`; `create`/`update`/`transition` refactored with `auth: AuthContext` signature.
- `src/admin_backend/repositories/org_nodes.py`: new `set_status` method.
- `src/admin_backend/routers/v1/stores.py`: handlers pass `auth=auth`; helper `_actor_type_from_auth` deleted (no longer used).
- `src/admin_backend/routers/v1/org_tree.py`: `edit_org_node` adds field-allowlist check on STORE-type targets.
- Migration: `migrations/versions/34f515cbc63a_step_6_21_2_stores_org_node_id_not_null.py` NEW.

Tests (5 files):
- `tests/integration/test_stores_repo_writes.py`: `_base_create_kwargs` rewritten; C/U/T tests updated; C7 deleted; PW1-PW10 added (with PW6a/PW6b split).
- `tests/integration/test_stores_writes_router.py`: `_valid_create_body` updated; `_ensure_tenant_root` helper added; RC9 reframed; RC10 deleted; RC11/RC12 reshaped; W1-W5 added.
- `tests/integration/test_stores_set_status_router.py`: SS1/SS2 added (cascade verification).
- `tests/integration/test_org_tree_writes_router.py`: C1/C3 + V3-V7 reshaped to use non-STORE types; V8/E13/E14/E16 added (E15 dropped per Deviation #2).
- `tests/integration/test_org_tree_repo_writes.py`: RT7/RT8 added (set_status into/out-of ARCHIVED).
- `tests/integration/conftest.py`: `make_store` auto-provisions paired STORE-type org_node when `org_node_id` omitted; teardown order updated.

Scripts (3 files):
- `scripts/smoke_curl.sh`: POST /stores body uses `parent_org_node_id`; +1 assertion for org_node_id in response; ot_flow uses DEPARTMENT instead of STORE; +1 STORE-rejection assertion (V8 equivalent).
- `scripts/test_endpoints.sh`: mirrors smoke_curl changes.
- `scripts/test_endpoints_cloud.sh`: mirrors.

Docs (4 files + openapi.json regen):
- `docs/endpoints/stores.md`: contract-change banner + POST/PATCH/set-status sections updated.
- `docs/endpoints/org-tree.md`: POST/PATCH sections updated with STORE rejection + STORE-target field-allowlist notes.
- `docs/endpoints/openapi.json`: regenerated.
- `docs/architecture.md`: § A.4 + § A.5 inserted after § A.3.

Tracking (3 files):
- `BUILD_PLAN.md`: 6.21.2 TODO -> DONE-LOCAL.
- `CLAUDE.md`: D-36 added; Completed entry added.
- `docs/implementation-steps/step-6_21_2-stores-org-node-atomic-paired-write-2026-05-21.md`: this doc.

Plus the impl prompt itself: `prompts/step-6_21_2-impl-2026-05-20.md`.

### Test catalogue (final, post-deviations)

| Test ID | File | Coverage |
|---|---|---|
| PW1 | test_stores_repo_writes | Atomic paired write: both rows + link |
| PW2 | test_stores_repo_writes | Missing parent -> ParentNodeNotFoundError + no orphans |
| PW3 | test_stores_repo_writes | STORE-type parent -> InvalidParentNodeTypeError |
| PW4 | test_stores_repo_writes | Cross-tenant parent -> 404 (RLS-as-404) |
| PW5 | test_stores_repo_writes | Update name cascade |
| PW6a | test_stores_repo_writes | Update store_code -> stores collision (DUPLICATE_STORE_CODE) |
| PW6b | test_stores_repo_writes | Update store_code -> org_node collision (DUPLICATE_ORG_NODE_CODE) |
| PW7 | test_stores_repo_writes | Update parent_org_node_id reparents paired |
| PW8 | test_stores_repo_writes | Transition CLOSED -> paired ARCHIVED + triplet |
| PW9 | test_stores_repo_writes | Transition out-of-CLOSED -> paired ACTIVE + nulled triplet |
| PW10 | test_stores_repo_writes | STORE_STATUS_TO_ORG_NODE_STATUS dict correctness |
| W1 | test_stores_writes_router | POST returns org_node_id end-to-end |
| W2 | test_stores_writes_router | POST without parent_org_node_id -> 422 |
| W3 | test_stores_writes_router | POST with legacy org_node_id -> 422 extra_forbidden |
| W4 | test_stores_writes_router | PATCH name cascades end-to-end (verified via DB read) |
| W5 | test_stores_writes_router | PATCH parent_org_node_id reparents (verified via DB read) |
| SS1 | test_stores_set_status_router | Set-status CLOSED cascades to org_node ARCHIVED |
| SS2 | test_stores_set_status_router | Set-status out-of-CLOSED unarchives paired |
| V8 | test_org_tree_writes_router | POST /org-tree node_type='STORE' rejected |
| E13 | test_org_tree_writes_router | PATCH STORE target with `name` rejected |
| E14 | test_org_tree_writes_router | PATCH STORE target with `code` rejected |
| E16 | test_org_tree_writes_router | PATCH STORE target with parent_id only succeeds |
| RT7 | test_org_tree_repo_writes | OrgNodesRepo.set_status into ARCHIVED populates triplet |
| RT8 | test_org_tree_repo_writes | OrgNodesRepo.set_status out-of-ARCHIVED nulls triplet |

Total new tests: 23. Net test count change: +23 - 2 (C7 + RC10 deleted) = +21. Baseline 769 -> 791.

### Verification commands

```bash
# Migration
set -a; source .env; set +a
uv run alembic upgrade head
uv run alembic downgrade -1
uv run alembic upgrade head

# Targeted tests
uv run pytest \
  tests/integration/test_stores_repo_writes.py \
  tests/integration/test_stores_writes_router.py \
  tests/integration/test_stores_set_status_router.py \
  tests/integration/test_org_tree_writes_router.py \
  tests/integration/test_org_tree_repo_writes.py \
  -v --tb=short

# Full suite regression
uv run pytest --tb=no -q

# mypy strict
uv run mypy --strict src/admin_backend

# check_setup
./scripts/check_setup.sh

# Local smoke
set -a; source .env; set +a
uv run uvicorn admin_backend.main:app --host 127.0.0.1 --port 8000 > /tmp/uvicorn-smoke.log 2>&1 &
sleep 4
bash scripts/smoke_curl.sh http://localhost:8000

# OpenAPI regen
curl -sf http://localhost:8000/api/v1/openapi.json | jq -a . > docs/endpoints/openapi.json
```

## Retro

### What landed

- POST `/api/v1/stores` is now the atomic-pair entry point. `parent_org_node_id` required; server creates the paired STORE-type org_node + the stores row in one transaction. Response carries the server-allocated `org_node_id`.
- PATCH and set-status cascade shared fields atomically (name / store_code / parent_org_node_id / status -> archived_* triplet).
- POST `/org-tree` rejects `node_type='STORE'` via the model_validator (mirrors V1's TENANT rejection shape).
- PATCH `/org-tree` on STORE-type target rejects shared fields (`name`, `code`) with 422 `ORG_NODE_FIELD_NOT_ALLOWED_FOR_TYPE`. Reparent (`parent_id`-only body) remains allowed per the dual-endpoint contract.
- Migration `34f515cbc63a` ships the NOT NULL tightening. Round-trip clean.
- 23 new tests; 2 retired (C7, RC10); net +21. Full suite 791 passing.
- mypy strict clean (82 source files).
- check_setup 36/36.
- Local smoke 69/69 (was 68; +1 for org_node_id-in-response).
- architecture.md gains § A.4 (general principle) and § A.5 (this seam) after § A.3.
- CLAUDE.md gains D-36 codifying the two-table-one-entity pattern.

### Deviations applied at impl-time

- Refactored `StoresRepo.{create,update,transition}` to take `auth: AuthContext` directly (Deviation #6 extension). Replaced the prior `actor_user_id` / `actor_user_type` pair which would have required synthetic AuthContext re-packing for the `add_node` / `edit_node` / `set_status` cascade calls.
- `make_store` fixture auto-provisions a paired STORE-type org_node when `org_node_id` is omitted. Necessary post-NOT-NULL migration; 125+ existing call sites continue to work unchanged.
- C1/C3 in `test_org_tree_writes_router.py` reshaped from STORE-type creates to non-STORE creates (REGION under BU; REGION under tenant root). The pre-6.21.2 behaviour they tested (create STORE via /org-tree) is now unreachable; the tests preserve their intent (happy path, level-skipping) with valid node types.
- V3 (cascade-order equal-ord reject) reshaped from STORE-under-STORE to HQ-under-HQ. Same reason.
- V4/V5/V6/V7 (validation-failure tests) updated to use non-STORE node_types where they previously used STORE.

### Cloud deploy posture

DDL change. Migration `34f515cbc63a` ships via the standard `--migrate` deploy path. Pre-deploy SQL cleanup of Cloud SQL orphans (per the impl prompt's Appendix A) is operator workflow and MUST run before the migration:

- Delete 7 NULL-`org_node_id` rows in `core.stores` belonging to Buc-ee's.
- Delete 8 orphan STORE-type rows in `core.org_nodes` belonging to Buc-ee's (no matching `stores` row).

After cleanup, the migration's `ALTER COLUMN ... SET NOT NULL` runs without error. Frontend coordination (the new POST body shape is breaking) is a Phase 6 timing decision.

### What did NOT change

- DDL beyond the one ALTER COLUMN. The `uq_stores_org_node_id` partial unique index is preserved (the partial form becomes equivalent to full UNIQUE once `org_node_id` is NOT NULL).
- RBAC permission catalogue. `ADMIN.STORES.CONFIGURE.TENANT` continues to cover the whole atomic write per architecture.md § A.4 RBAC rule.
- Seed Excel.
- OrgNodesRepo.edit_node signature (status remains out of edit_node's scope; set_status is the dedicated method).
- Read endpoints (GET /stores, GET /org-tree). The Step 6.21.1 tenant_root_id surface is unchanged.

### Adjacent observation

The atomic-pair pattern surfaces a sub-pattern worth naming for future steps: when one of the two tables has a strict cross-table uniqueness check that the other lacks, the cascade can fire 409s from EITHER table's index. PW6a/PW6b's split makes this explicit for store_code (stores-only via `_raise_if_store_code_taken`) vs org_node.code (tenant-wide via `uq_org_nodes_tenant_code_lower`). When the next two-table seam appears, the design conversation should enumerate which uniqueness scopes apply on each side.
