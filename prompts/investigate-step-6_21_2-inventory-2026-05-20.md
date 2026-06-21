# Investigation: Step 6.21.2 codebase inventory

**Date drafted:** 2026-05-20
**Mandate:** investigation only. Do NOT change any code, do NOT modify any documents. Run grep/view, produce a written inventory report. The report informs the Step 6.21.2 implementation prompt drafting in Chat. No fix gets written here.

## Context

Step 6.21.2 will:

- Refactor `StoresRepo` to perform an atomic paired write (`stores` + STORE-type `org_nodes`) on create.
- Cascade `name`, `store_code`, and status changes from `stores` to the paired org_node on update / set-status.
- Restrict `OrgNodeCreateRequest` (POST /org-tree) to reject `node_type='STORE'`.
- Restrict `OrgNodePatchRequest` (PATCH /org-tree) on STORE-type targets to allow only `parent_id` (reject shared fields `name`, `code`, `status`).
- Add a DDL migration `ALTER TABLE core.stores ALTER COLUMN org_node_id SET NOT NULL`.

The Chat-side design conversation locked all this. The drafting of the impl prompt now needs accurate codebase references so the prompt's locked-decision Owner lines, change list, and pre-flight checks cite real artifacts, not guessed ones.

## Investigation questions

For each, produce a short answer with file:line citations and the smallest grep/view command that produced the answer. No commentary beyond a 1-2 sentence summary per question.

### Q1 — StoresRepo class location and method inventory

What file holds the `StoresRepo` class? What methods does it expose? Specifically: is there a `create`, an `update` (or `edit` / `patch`), and a `transition` (or `set_status`)? What's each method's signature (just the line)?

Suggested command:

```bash
grep -rn "class StoresRepo\|async def " src/admin_backend/repositories/ | grep -E "stores|StoresRepo" | head -30
```

### Q2 — OrgNodesRepo write-side method names

For Step 6.21.2, we'll need to UPDATE an org_node from inside `StoresRepo.update` and `StoresRepo.transition` (cascade). What's the existing public method on `OrgNodesRepo` for editing an org_node (name / code / parent_id / status)? Is it called `edit_node`, `update`, `patch`, something else?

Suggested command:

```bash
grep -n "async def edit_node\|async def update\|async def patch\|async def set_status\|async def transition" src/admin_backend/repositories/org_nodes.py
```

### Q3 — Existing store test files and their layout

What test files exist for stores under `tests/integration/`? Specifically: is there a single `test_stores_router.py` or are tests split (router / repo / specific endpoint)? What's the existing convention for stores set-status tests?

Suggested commands:

```bash
ls tests/integration/ | grep -i store
grep -n "set_status\|set-status\|transition" tests/integration/test_stores_*.py | head -20
```

### Q4 — `_check_org_node_for_store` validator: location and current shape

There's an existing helper called `_check_org_node_for_store` (referenced in CC's earlier investigation report). Where is it? What does it currently validate? Is it called from POST /stores, PATCH /stores, or both?

Suggested commands:

```bash
grep -rn "_check_org_node_for_store\|def _check_org_node" src/admin_backend/
```

### Q5 — Existing error class taxonomy for stores and org-tree

What error classes already exist for stores and org-tree concerns? Specifically: `OrgNodeNotForStoreError`, `StoreCodeTakenError`, `ParentNodeNotFoundError`, anything similar. What HTTP status and wire code does each map to?

Suggested commands:

```bash
grep -rn "class.*Error" src/admin_backend/errors/ | grep -iE "store|org_node|node|parent" | head -20
grep -n "OrgNodeNotForStore\|StoreCodeTaken\|ParentNodeNotFound" src/admin_backend/errors/codes.py src/admin_backend/errors/exceptions.py
```

### Q6 — Request/response schema files for stores and org-tree

Where do `StoreCreateRequest`, `StorePatchRequest`, `StoreSetStatusRequest`, `OrgNodeCreateRequest`, `OrgNodePatchRequest` live? (Confirms the schema-file convention — we found in 6.21.1 that schemas live at `schemas/<resource>.py`, not `schemas/v1/<resource>.py`.)

Suggested command:

```bash
grep -rn "class StoreCreateRequest\|class StorePatchRequest\|class StoreSetStatusRequest\|class OrgNodeCreateRequest\|class OrgNodePatchRequest" src/admin_backend/schemas/
```

### Q7 — Router handler locations

Where are the POST /stores, PATCH /stores, POST /stores/{id}/set-status, POST /org-tree, PATCH /org-tree/{node_id} handlers? Confirm the file path and line numbers.

Suggested command:

```bash
grep -rn "@router.post\|@router.patch" src/admin_backend/routers/v1/stores.py src/admin_backend/routers/v1/org_tree.py
```

### Q8 — Alembic migration directory and most-recent migration

Where do Alembic migrations live? What's the most-recent migration file (so the new DDL migration follows the same naming + structure convention)? Does the project use sync or async Alembic?

Suggested commands:

```bash
ls -t alembic/versions/ | head -5
head -30 alembic/versions/<most-recent-file>
```

## Out of scope

- Reading any test logic in detail. Names + locations are sufficient.
- Any code changes.
- Any commentary on what the implementation should look like.
- Any cloud-side queries.

## Deliverable

Save the report to:

`reports/step-6_21_2-inventory-2026-05-20.md`

The `reports/` directory exists and is used for investigation / inventory / diagnostic outputs that inform later prompt drafting; it sits outside `docs/` because these reports are working notes, not user-facing documentation.

Structure:

```
# Step 6.21.2 pre-investigation inventory

**Date:** 2026-05-20
**Scope:** codebase inventory only; no code changes.
**Source prompt:** prompts/investigate-step-6_21_2-inventory-2026-05-20.md

## Q1 — StoresRepo

File: <path>
Methods: <list with line numbers and signatures>
Summary: <1-2 sentences>

## Q2 — OrgNodesRepo write method

Method name: <name>
File:line: <citation>
Summary: <1-2 sentences>

[... Q3-Q8 same shape]
```

When the report file is written, surface the path in your final message and stop. Do not start drafting any code or any other prompt. Do not stage or commit; the operator will decide whether to commit the report later.

