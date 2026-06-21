# Step 6.21.2 pre-investigation inventory

**Date:** 2026-05-20
**Scope:** codebase inventory only; no code changes.
**Source prompt:** `prompts/investigate-step-6_21_2-inventory-2026-05-20.md`

## Q1 — StoresRepo

File: `src/admin_backend/repositories/stores.py`

Methods (line / signature header):

| Line | Method | Visibility |
|---|---|---|
| 124 | `class StoresRepo:` | class |
| 127 | `async def list(...)` | public read |
| 199 | `async def get_by_id(...)` | public read |
| 227 | `async def _tenant_exists(...)` | private helper |
| 252 | `async def _raise_if_store_code_taken(...)` | private helper |
| 306 | `async def _check_org_node_for_store(...)` | private helper |
| 376 | `async def create(...)` | public write |
| 501 | `async def update(...)` | public write |
| 638 | `async def transition(...)` | public write |

Command: `grep -n "class StoresRepo\|async def " src/admin_backend/repositories/stores.py`

**Summary.** Public write surface is `create`, `update`, `transition`. Naming convention is `transition` (not `set_status`); same naming as `TenantsRepo.transition` and `TenantUsersRepo.transition` per the shared `TransitionResult` enum imported from `repositories.tenants`. No `edit` / `patch` aliases.

## Q2 — OrgNodesRepo write method

Method name: `edit_node`
File:line: `src/admin_backend/repositories/org_nodes.py:432`

Companion write method: `add_node` at `src/admin_backend/repositories/org_nodes.py:332`.

Full public method inventory for OrgNodesRepo:

| Line | Method |
|---|---|
| 158 | `async def count_active_by_tenant(...)` |
| 180 | `async def list_active_with_child_counts(...)` |
| 237 | `async def list_children_paginated(...)` |
| 304 | `async def node_exists(...)` |
| 332 | `async def add_node(...)` |
| 432 | `async def edit_node(...)` |
| 624 | `async def _select_for_update_node(...)` (private) |
| 662 | `async def _refetch_by_id(...)` (private) |

Command: `grep -n "    async def " src/admin_backend/repositories/org_nodes.py`

**Summary.** `edit_node` is the only public update path; covers name / code / parent_id / status edits as one unified method (per Step 6.13 lineage). No separate `set_status` or `transition` on org_nodes; status changes flow through `edit_node`. The Step 6.21.2 cascade (`StoresRepo.update` / `StoresRepo.transition` calling into the paired org_node) will invoke `edit_node`, not a new method.

## Q3 — Store test files

Files (5 under `tests/integration/`):

| File | Coverage area |
|---|---|
| `tests/integration/test_stores_repo.py` | Repo reads (Step 6.17.2) |
| `tests/integration/test_stores_repo_writes.py` | Repo writes (`create`, `update`, `transition`); set-status tests at line 716+ (T-series, `repo.transition(...)`) |
| `tests/integration/test_stores_router.py` | Router GET endpoints |
| `tests/integration/test_stores_set_status_router.py` | Router POST /stores/{id}/set-status |
| `tests/integration/test_stores_writes_router.py` | Router POST /stores + PATCH /stores/{id} |

Commands:
- `ls tests/integration/ | grep -i store`
- `grep -n "set_status\|set-status\|transition" tests/integration/test_stores_*.py`

**Summary.** Split layout: per-resource read/write split + a dedicated set-status router file. Repo-level set-status tests live alongside other repo writes in `test_stores_repo_writes.py` (T-series starting line 716, exercising `repo.transition(...)` directly). Step 6.21.2 cascade tests for STORE-type org_node updates plausibly fit either `test_stores_repo_writes.py` (paired-write atomicity) or `test_stores_writes_router.py` (end-to-end through handler); both files already follow the established convention.

## Q4 — `_check_org_node_for_store` validator

File:line: `src/admin_backend/repositories/stores.py:306`

Signature: `async def _check_org_node_for_store(self, session, *, tenant_id: UUID, org_node_id: UUID) -> None`

Call site: `src/admin_backend/repositories/stores.py:430` (inside `StoresRepo.create`, conditional on `org_node_id is not None`).

What it validates: three failure paths collapse to one wire error (`OrgNodeNotForStoreError`, 409):
- The `org_node_id` does not exist or is RLS-invisible to the caller.
- The `org_node` exists but its `tenant_id` differs from the request body's `tenant_id` (PLATFORM-only observable).
- The `org_node` is already linked to another `stores` row (DDL `uq_stores_org_node_id` 1-to-1 enforcement; pre-check surfaces the typed 409 ahead of the IntegrityError).

Commands:
- `grep -rn "_check_org_node_for_store\|def _check_org_node" src/admin_backend/`

**Summary.** Single call site today — only on `create`. Not called from `update` (line 501) or `transition` (line 638). Step 6.21.2 will retire the `org_node_id` parameter on `StoresRepo.create` entirely (because the paired write provisions the org_node server-side) and may retire or repurpose this helper depending on whether any "is this org_node already linked" check survives the refactor.

## Q5 — Existing error class taxonomy for stores and org-tree

All in `src/admin_backend/errors.py`:

| Class | Line | http_status | code |
|---|---|---|---|
| `OrgNodeNotFoundError` | 140 | 404 | `ORG_NODE_NOT_FOUND` |
| `StoreNotFoundError` | 154 | 404 | `STORE_NOT_FOUND` |
| `InvalidOrgNodeError` | 354 | 422 | `INVALID_ORG_NODE` |
| `InvalidParentNodeTypeError` | 431 | 422 | `INVALID_PARENT_NODE_TYPE` |
| `TenantRootNotReparentableError` | 454 | 422 | `TENANT_ROOT_NOT_REPARENTABLE` |
| `CycleDetectedError` | 471 | 422 | `CYCLE_DETECTED` |
| `DuplicateOrgNodeCodeError` | 492 | 409 | `DUPLICATE_ORG_NODE_CODE` |
| `ParentNodeNotFoundError` | 512 | 404 | `PARENT_NODE_NOT_FOUND` |
| `DuplicateStoreCodeError` | 529 | 409 | `DUPLICATE_STORE_CODE` |
| `OrgNodeNotForStoreError` | 556 | 409 | `ORG_NODE_NOT_FOR_STORE` |
| `InvalidStateTransitionError` | 259 | 409 | `INVALID_STATE_TRANSITION` (shared across tenants / tenant_users / stores) |

Commands:
- `grep -n "^class.*Error" src/admin_backend/errors.py`
- `grep -nE "http_status|^    code " src/admin_backend/errors.py`

**Summary.** The taxonomy already covers most failure modes Step 6.21.2's atomic paired write will face: cross-tenant id collisions, duplicate codes, RLS-as-404 misses, state-transition rejections. The `OrgNodeNotForStoreError` 409 specifically encodes the three concerns the current `_check_org_node_for_store` validates; the Step 6.21.2 refactor that drops the user-supplied `org_node_id` from POST /stores can retire this wire code only if `_check_org_node_for_store` is fully retired and no other write path needs the same conditions surfaced.

## Q6 — Request/response schema files for stores and org-tree

| Schema | File | Line |
|---|---|---|
| `OrgNodeCreateRequest` | `src/admin_backend/schemas/org_node.py` | 260 |
| `OrgNodePatchRequest` | `src/admin_backend/schemas/org_node.py` | 326 |
| `StoreCreateRequest` | `src/admin_backend/schemas/store.py` | 133 |
| `StorePatchRequest` | `src/admin_backend/schemas/store.py` | 163 |
| `StoreSetStatusRequest` | `src/admin_backend/schemas/store.py` | 212 |

Command: `grep -rn "class StoreCreateRequest\|class StorePatchRequest\|class StoreSetStatusRequest\|class OrgNodeCreateRequest\|class OrgNodePatchRequest" src/admin_backend/schemas/`

**Summary.** Confirms the 6.21.1 finding: schemas live at `src/admin_backend/schemas/<resource>.py`, NOT at `src/admin_backend/schemas/v1/<resource>.py`. There is no `schemas/v1/` directory. `store.py` carries all three store request schemas; `org_node.py` carries both org-tree request schemas (plus the read schemas `OrgNodeTreeItem`, `OrgTreeResponse`, `OrgNodeChildrenResponse` already touched at 6.21.1).

## Q7 — Router handler locations

Stores router (`src/admin_backend/routers/v1/stores.py`):

| Line | Decorator | Handler |
|---|---|---|
| 126 | `@router.get` (list) | `list_stores` |
| 207 | `@router.get("/{store_id}")` | `get_store` |
| 236 | `@router.post(...)` | `create_store` (def at 241) |
| 308 | `@router.patch("/{store_id}", ...)` | `patch_store` (def at 309) |
| 365 | `@router.post("/{store_id}/set-status", ...)` | `set_store_status` (def at 366) |

Org-tree router (`src/admin_backend/routers/v1/org_tree.py`):

| Line | Decorator | Handler |
|---|---|---|
| (E2) | `@router.get(...)` | `get_org_tree` (def at 139) |
| (E3) | `@router.get(.../children, ...)` | `get_node_children` (def at 261) |
| 317 | `@router.post(...)` | `add_org_node` (def at 335) |
| 360 | `@router.patch(...)` | `edit_org_node` (def at 375) |

Commands:
- `grep -nE "@router\.(post|patch)" src/admin_backend/routers/v1/stores.py src/admin_backend/routers/v1/org_tree.py`
- `grep -n "^async def " src/admin_backend/routers/v1/stores.py src/admin_backend/routers/v1/org_tree.py`

**Summary.** Five Step-6.21.2-relevant write handlers: `create_store` (POST /stores), `patch_store` (PATCH /stores/{id}), `set_store_status` (POST /stores/{id}/set-status), `add_org_node` (POST /org-tree), `edit_org_node` (PATCH /org-tree/{node_id}). The two org-tree handlers are where the new `node_type='STORE'` restrictions land; the three store handlers are where the cascade-into-org_node logic lands.

## Q8 — Alembic migrations

Directory: `migrations/versions/` (NOT `alembic/versions/`).

Most-recent migration files (top of `ls -t` output):

| File | Step | Notes |
|---|---|---|
| `c530346032dd_step_6_16_1_audit_log_schema.py` | 6.16.1 | Current head (verified: `alembic heads` returns `c530346032dd (head)`) |
| `5e22b2ca13cc_step_6_20_3_rbac_structural_triggers.py` | 6.20.3 | Previous |
| `a0982a86985b_fix_schema_qualify_identifiers_inside_.py` | fix | Schema-qualify identifiers in trigger function bodies (CSD-03) |
| `3e05299cb533_step_6_8_1_split_user_role_assignments.py` | 6.8.1 | |
| `2fdc4bc9f4cb_step_6_7_module_access_lookups_seed.py` | 6.7 | |

Migration file header convention (from `c530346032dd_step_6_16_1_audit_log_schema.py`):

```python
"""Step 6.16.1: audit log schema (DDL + RLS + indexes)

Revision ID: c530346032dd
Revises: 5e22b2ca13cc
Create Date: 2026-05-20

[docstring continues...]
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'c530346032dd'
down_revision: Union[str, Sequence[str], None] = '5e22b2ca13cc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None: ...
def downgrade() -> None: ...
```

Alembic mode: env.py uses `async_engine_from_config` (`migrations/env.py:13`); migrations run inside an async event loop (`migrations/env.py:7` imports `asyncio`). Individual migration upgrade/downgrade functions use the synchronous `op.*` API (e.g., `op.create_table`, `op.execute`) — standard alembic shape; async-vs-sync split is the env.py layer's concern, not the migration body's.

Commands:
- `ls -t migrations/versions/*.py | head -5`
- `head -3 migrations/env.py` + `grep -n "async\|run_migrations" migrations/env.py`
- `uv run alembic heads`

**Summary.** New DDL migration for Step 6.21.2 (`ALTER TABLE core.stores ALTER COLUMN org_node_id SET NOT NULL`) follows the `<rev>_step_X_Y_Z_<short_desc>.py` filename pattern with `down_revision = 'c530346032dd'` (current head). Naming pattern: `<rev>_step_6_21_2_stores_org_node_id_not_null.py` or similar. Schema-qualify identifiers per CSD-03 — for an `ALTER TABLE`, capture `current_schema()` and interpolate via f-string, mirroring `a0982a86985b`'s posture; the simpler `op.alter_column(table_name='stores', schema=schema, ...)` alembic API may suffice for this specific case.

## Q9 — Org-tree write-side router tests

File: `tests/integration/test_org_tree_writes_router.py` (separate from `test_org_tree_router.py`, which holds the read-side E2/E3 tests covered at Step 5.3 / Step 6.21.1).

Coverage map (from the file's module docstring at `tests/integration/test_org_tree_writes_router.py:1-28`):

```
Add Node (POST /api/v1/tenants/{tenant_id}/org-tree):
  C1-C3   happy paths (SUPER_ADMIN, OWNER, level-skip)
  V1-V7   validation failures
  P1-P4   permission boundary + caller variants
  PA1     PLATFORM_ADMIN happy via GLOBAL->TENANT cascade (FN-AB-47)

Edit Node (PATCH /api/v1/tenants/{tenant_id}/org-tree/{node_id}):
  E1-E12  rename, recode, reparent, combined, cycle, tenant-root,
          empty-body, role-assignment stability, duplicate, missing
  PA2     PLATFORM_ADMIN write happy via GLOBAL->TENANT cascade
```

Representative POST /org-tree (`add_org_node`) tests:

| Line | Test | Series purpose |
|---|---|---|
| 165 | `test_c1_super_admin_adds_store_under_region` | C-series: happy paths |
| 289 | `test_v1_node_type_tenant_rejected` | V-series: validation failures |
| 314 | `test_v2_hq_under_region_rejected_as_reversal` | V-series (cascade-order) |
| 352 | `test_v3_store_under_store_rejected_as_same_ordinal` | V-series (equal-ordinal) |
| 528 | `test_p1_owner_tenant_caller_adds_happy` | P-series: permission boundary |

Representative PATCH /org-tree/{node_id} (`edit_org_node`) tests:

| Line | Test | Series purpose |
|---|---|---|
| 680 | `test_e1_rename_only_path_unchanged` | E-series: rename |
| 710 | `test_e2_code_change_path_segment_rewritten` | E-series: recode |
| 742 | `test_e3_reparent_leaf_path_updated` | E-series: reparent |
| 971 | `test_e7_tenant_root_reparent_rejected` | E-series (TENANT_ROOT_NOT_REPARENTABLE) |
| 1025 | `test_e9_empty_patch_body_422` | E-series (EMPTY_PATCH) |
| 1184 | `test_pa2_platform_admin_patches_happy_via_global_cascade` | PA-series: PLATFORM_ADMIN cascade |

Commands:
- `ls tests/integration/ | grep -i org_tree`
- `grep -nE "^async def test_" tests/integration/test_org_tree_writes_router.py`
- `head -50 tests/integration/test_org_tree_writes_router.py`

**Summary.** Both POST `add_org_node` and PATCH `edit_org_node` router-integration tests live in `tests/integration/test_org_tree_writes_router.py`. Naming convention is **NOT** `test_add_org_node_*` / `test_edit_org_node_*`; instead it is `test_<series_letter><n>_<descriptive_name>` where:
- POST handler tests use prefixes **C** (happy), **V** (validation), **P** (permission), **PA** (PLATFORM_ADMIN cascade).
- PATCH handler tests use prefixes **E** (edit), **PA** (PLATFORM_ADMIN cascade).

For Step 6.21.2's new tests:
- **(a) POST /org-tree rejecting `node_type='STORE'` with 422** lands as a new **V-series** test in this file. Adjacent precedent: `test_v1_node_type_tenant_rejected` (line 289) already rejects TENANT-type at POST; the new STORE-type reject is the obvious **V8** slot. Existing series goes V1-V7; pick V8 or higher.
- **(b) PATCH /org-tree on STORE-type target rejecting shared fields (`name`, `code`, `status`) while allowing `parent_id`** lands as new **E-series** tests in this file. Existing series goes E1-E12; pick E13 or higher. Likely 3-4 sub-tests (one per rejected shared field + one positive: parent_id-only on STORE target still works).
