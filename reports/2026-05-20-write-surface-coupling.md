# Investigation — Write-surface coupling across tenants ↔ org_nodes ↔ stores

**Date.** 2026-05-20.
**Status.** Investigation only; no code or doc changes proposed.
**Driver.** Frontend bug filed during 5g.1.5 (Add Org Node fails when TENANT row is selected as parent); cloud SQL diagnostics surfaced 7 NULL-`org_node_id` stores and 8 orphan STORE-type org_nodes in one tenant (Buc-ee's).

---

## 1. Summary

The operator's hypothesis holds. The codebase has two independent gaps that share a single root cause: **the joint contract across `tenants ↔ org_nodes ↔ stores` was never expressed as a unit.** Each individual endpoint is internally correct against its own table; what is missing is the cross-endpoint invariant that links them.

- **Gap A — Add Org Node from TENANT row.** The backend has no contract violation. `POST /api/v1/tenants/{tenant_id}/org-tree` validates `parent_id` exclusively against `core.org_nodes.id`. The bug is upstream: no endpoint in the current API exposes the tenant-root `org_node.id` to the frontend. The frontend invented a synthetic TENANT row keyed by `tenants.id` (which is never equal to any `org_nodes.id`); the handler correctly returns 404 `PARENT_NODE_NOT_FOUND`.
- **Gap B — store ↔ STORE-type org_node decoupling.** `POST /api/v1/stores` accepts an optional `org_node_id` (default NULL) and never auto-creates a matching STORE-type org_node. `POST /api/v1/tenants/{tenant_id}/org-tree` (STORE-type) inserts the org_node row only and never creates a paired `stores` row. The two endpoints are wholly independent on the write side; the 1:1 pairing visible in the seed data is hand-authored Excel, not a backend invariant.

Step 6.20.1's atomic three-table insert in `TenantsRepo.create` is the only place in the codebase that owns a cross-table coupling contract. Every other write touches exactly one of the three tables. The hypothesis "the multi-endpoint workflow was never designed as a unit" is supported by both gaps.

---

## 2. Investigation findings

### C1 — GET `/api/v1/tenants/{tenant_id}/org-tree` response shape

The tenant-root `org_node.id` is **not** returned anywhere in the response envelope. There is no `tenant_root_id` field, no `stats.tenant_root_id`, no metadata block carrying it.

`src/admin_backend/routers/v1/org_tree.py:208-215` — handler returns:

```python
return OrgTreeResponse(
    tenant_id=tenant.id,
    tenant_name=tenant.name,
    stats=stats,
    tree=tree,
)
```

The tree-building helper structurally **excludes** every TENANT-type node:

`routers/v1/org_tree.py:432-438`:

```python
by_id: dict[UUID, OrgNodeTreeItem] = {}
non_tenant_rows = [
    (n, cc) for (n, cc) in rows if n.node_type != OrgNodeType.TENANT
]
tenant_root_ids = {
    n.id for (n, _) in rows if n.node_type == OrgNodeType.TENANT
}
```

`tenant_root_ids` is computed only to recognise which top-level non-TENANT nodes are children of the implicit root; it is never written into any response object. `OrgTreeResponse` (`schemas/org_node.py:182-206`) only declares `tenant_id`, `tenant_name`, `stats`, `tree`. The docstring confirms intent: *"the tenant root itself is not part of the rendered tree"* (`schemas/org_node.py:71-75, 200-204`).

**Does any current endpoint expose the tenant-root `org_node.id`?**

- `GET /api/v1/tenants/{tenant_id}` (`TenantDetail`): no `org_node` references at all.
- `GET /api/v1/tenants/{tenant_id}/org-tree` (E2): excluded (above).
- `GET /api/v1/tenants/{tenant_id}/org-nodes/{node_id}/children` (E3): requires the id as input; doesn't surface it.
- `get_tenant_anchor` (`auth/anchor_deps.py:39-71`): looks up the tenant root but returns `path::text` only, not `id`. Used internally by the gate layer; never surfaced.
- `GET /api/v1/stores/{store_id}`: returns the store's `org_node_id` (which is a STORE-type id, not the tenant root).

Net: no endpoint exposes the tenant-root's UUID. A frontend that needs it has no public way to obtain it.

### C2 — POST `/api/v1/tenants/{tenant_id}/org-tree` parent_id resolution

`parent_id` is validated exclusively against `core.org_nodes.id` via `OrgNodesRepo.add_node` → `_select_for_update_node`:

`repositories/org_nodes.py:624-660`:

```python
async def _select_for_update_node(self, session, *, tenant_id, node_id) -> _NodeRow | None:
    ...
    sql = text(
        f"""
        SELECT id, tenant_id, parent_id, path::text AS path, node_type
          FROM {schema}.org_nodes
         WHERE id = :node_id
           AND tenant_id = :tenant_id
         FOR UPDATE
        """
    )
    ...
```

Miss path (`repositories/org_nodes.py:367-372`):

```python
if parent is None:
    raise ParentNodeNotFoundError(
        f"parent_id={parent_id} not visible in tenant_id={tenant_id}",
        parent_id=str(parent_id),
        tenant_id=str(tenant_id),
    )
```

Error class definition (`errors.py:512-526`):

```python
class ParentNodeNotFoundError(ClientError):
    ...
    public_message = "Parent org node not found."
    http_status = 404
    code = "PARENT_NODE_NOT_FOUND"
```

**There is no code path where `parent_id` is interpreted as `tenants.id`** or where the two ids are exchanged. The query is single-table, fixed-key, no JOIN. The frontend's `parent_id = tenants.id` always misses because:
- `tenants.id` and the tenant-root `org_nodes.id` are two independent `uuidv7()` values (confirmed in cloud diagnostics);
- the lookup is `WHERE id = :node_id AND tenant_id = :tenant_id` so even if the values were coincidentally equal, the composite check would still resolve correctly.

The handler returns `404 PARENT_NODE_NOT_FOUND` deterministically when the frontend sends `parent_id = data.tenant_id`. This is the failure shape reported in 5g.1.5.

### C3 — POST `/api/v1/tenants` tenant-root provisioning

`repositories/tenants.py:566-715` — `TenantsRepo.create` is the only multi-table write contract in the codebase. Order of operations:

1. `_raise_if_name_taken` (line 593).
2. `slug_for_tenant_root(name, display_code)` derives `(code, path)` BEFORE the tenants INSERT (line 600-602). Empty slug raises `InvalidTenantNameForSlugError` (422) here, leaving no partial state. This was a refined LD2 in Step 6.20.1's plan.
3. INSERT into `core.tenants` (lines 604-647).
4. INSERT into `core.org_nodes` (lines 657-683): `tenant_id=:new_tenant_id`, `parent_id=NULL`, `node_type='TENANT'`, `status='ACTIVE'`, `name=:name` (same as tenant), `code=:org_node_code`, audit-actor pair = `:actor_user_id` (PLATFORM JWT user_id) for IDs + `'PLATFORM'` literal for both `*_user_type` halves.
5. Per-module loop INSERT into `core.tenant_module_access` (lines 688-710).
6. `await session.flush()` (line 715).
7. `get_by_id_with_aggregates` read-back (line 717).

All four writes are in the same request-scope session and commit atomically; any failure rolls back the entire transaction. Pre-fix orphans (10 cloud + 1 local tenants without a paired org_node) were cleaned up before Step 6.20.1 landed (step doc line 124).

If `slug_for_tenant_root` raises 422 before the tenants INSERT, no row is written anywhere. Confirmed by the design statement in the step doc lines 93-94: *"Putting slug derivation BEFORE the tenants INSERT means an empty-slug 422 leaves no partial state."*

### C4 — POST `/api/v1/stores` org_node_id handling

`schemas/store.py:147-160` — `StoreCreateRequest`:

```python
class StoreCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: UUID
    name: str = Field(min_length=1, max_length=200)
    country: str = Field(min_length=2, max_length=100)
    timezone: str = Field(min_length=1, max_length=50)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    store_code: str = Field(min_length=1, max_length=50)
    tax_treatment: TaxTreatment
    org_node_id: UUID | None = None
    address: str | None = None
    ...
```

`org_node_id` is **optional**; default `None`.

Repo flow (`repositories/stores.py:376-499`):

1. `_tenant_exists` (line 426) — RLS-bound visibility check; returns False if cross-tenant or missing. Handler maps None → 404 TENANT_NOT_FOUND.
2. `if org_node_id is not None: await self._check_org_node_for_store(...)` (lines 429-434).
3. `_raise_if_store_code_taken` (line 436).
4. INSERT into `core.stores` (lines 443-481), passing `:org_node_id` directly — including NULL when omitted.

When `org_node_id` is omitted, the new `stores` row is written with `org_node_id = NULL`. **No auto-creation of a STORE-type `org_nodes` row.** No fallback, no link-by-code matching, no implicit pairing.

When `org_node_id` is supplied, `_check_org_node_for_store` (`repositories/stores.py:306-374`) verifies via a single LEFT-JOIN query:

```sql
SELECT on_.tenant_id AS node_tenant_id,
       s.id AS linked_store_id
FROM {schema}.org_nodes AS on_
LEFT JOIN {schema}.stores AS s ON s.org_node_id = on_.id
WHERE on_.id = :node_id
LIMIT 1
```

Three failure paths → one wire error `OrgNodeNotForStoreError` (409): not visible, cross-tenant, already linked. **Note: `_check_org_node_for_store` does NOT verify `org_nodes.node_type = 'STORE'`.** A caller with a HQ-type or REGION-type `org_node_id` from the same tenant, not already linked, would pass the check. This is not the bug under investigation but is a latent looseness in the contract.

### C5 — PATCH `/api/v1/stores/{store_id}` org_node_id mutability

`org_node_id` is **immutable** via PATCH. `schemas/store.py:163-191` — `StorePatchRequest`:

```python
class StorePatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = ...
    store_code: str | None = ...
    country: str | None = ...
    timezone: str | None = ...
    currency: str | None = ...
    tax_treatment: TaxTreatment | None = None
    address: str | None = None
    latitude: Decimal | None = ...
    longitude: Decimal | None = ...
```

`extra="forbid"` rejects `org_node_id` (and `status`, `tenant_id`, `id`, audit columns) at Pydantic time — 422 before the handler runs.

Repo `update` also asserts the allowlist (`repositories/stores.py:536-551`):

```python
allowed_keys: frozenset[str] = frozenset({
    "name", "store_code", "country", "timezone", "currency",
    "tax_treatment", "address", "latitude", "longitude",
})
invalid = set(fields.keys()) - allowed_keys
if invalid:
    raise ValueError(...)
```

The docstring at `routers/v1/stores.py:329-331` confirms the lock: *"`tenant_id`, and `org_node_id` (immutable per LD3)"*. LD3 of Step 6.17.3 deferred the linkage mutability "pending a product workflow decision."

Since `org_node_id` cannot be changed via PATCH, there is no cascade question on the linked org_node. The only way to change a store's org_node link in v0 is direct SQL.

### C6 — POST `/api/v1/tenants/{tenant_id}/org-tree` STORE-type creation

`OrgNodesRepo.add_node` (`repositories/org_nodes.py:332-430`) inserts into `core.org_nodes` only. It accepts `node_type: OrgNodeType` (any value not TENANT — the Pydantic validator at `schemas/org_node.py:287-299` rejects TENANT) and writes a single row.

**Does the handler create a matching `stores` row when `node_type='STORE'`?**

No. There is no branch on `node_type` in `add_node`; no import of `StoresRepo`; no INSERT into `stores` anywhere in the org-tree write path. The endpoint is structurally a single-table write.

**Is there a documented expected workflow that locks this decision?**

Closest reference is Step 6.13's step doc (`docs/implementation-steps/step-6_13-org-tree-writes-2026-05-16.md`) and BUILD_PLAN's Step 6.13 entry. Neither addresses store ↔ STORE-type-org_node pairing. The schema (`OrgNodeCreateRequest`) only documents that `node_type='TENANT'` is forbidden. No paired-write contract is named.

Step 6.17.3's LD3 (deferred org_node_id mutability) and Step 6.17.3 retro line 78c (LD1 surface-and-stop) both touch the linkage indirectly but say nothing about creation-time pairing — they leave it as caller's responsibility.

### C7 — PATCH `/api/v1/tenants/{tenant_id}/org-tree/{node_id}`

`schemas/org_node.py:302-357` — `OrgNodePatchRequest` allows only `name`, `code`, `parent_id`. `node_type` is intentionally absent (immutable per LD3 — comment at line 313). There is **no `status` field**; STORE-type org_nodes cannot be archived via this endpoint.

Reparent path (`repositories/org_nodes.py:432-620`):

- `_select_for_update_node` on the target (line 468). Missing → `OrgNodeNotFoundError`.
- Cycle detection via ltree `<@` (`_is_descendant`, lines 703-715).
- Cascade-order check.
- Two UPDATEs in one transaction: target row + subtree re-path via `subpath(path, nlevel(...))`. Both atomic.

**Does anything cascade to a linked `stores` row?**

No. The `core.stores` table is never read or written by `edit_node`. A STORE-type org_node retains its `id`, so any `stores.org_node_id` referencing it remains valid through reparent / rename / recode. The link survives (`org_node.path` changing has no effect on `stores.org_node_id` — the link is via UUID, not via path).

Archival is not expressible via this surface; the status column is not mutable from any current endpoint.

### C8 — Seed paired-write pattern

Seed loaders entry points found via grep:

```
scripts/seed_dev_data/loaders/_base.py:84:  f"INSERT INTO {table_name} ..."
scripts/seed_dev_data/loaders/tenants.py
scripts/seed_dev_data/loaders/org_nodes.py
scripts/seed_dev_data/loaders/stores.py
... (one loader per sheet)
```

Each loader is single-table. Stores (`scripts/seed_dev_data/loaders/stores.py:14-24`):

```python
async def load(session, rows, mapper) -> None:
    if rows:
        validate_columns(SHEET_NAME, list(rows[0].keys()))
    for row in rows:
        await insert_and_register(session, SHEET_NAME, TABLE_NAME, row, mapper)
```

Column mapping for stores (`scripts/seed_dev_data/column_mappings.py:153-176`):

```python
STORES: Final[SheetMapping] = [
    helper("_org_node_key"),
    helper("_tenant_key"),
    db("id"),
    fk("tenant_id", "tenants"),
    fk("org_node_id", "org_nodes"),
    ...
]
```

The seed populates `stores.org_node_id` from the Excel `_org_node_key` reference — the pairing is **hand-authored in Excel** and resolved at load time through the `UUIDMapper`. The seed loader does **not** enforce the pairing; it just respects whatever the Excel says.

The seed and the production write surface diverge in posture, not in mechanism:
- **Seed**: pairs every `stores` row with a STORE-type `org_nodes` row because the Excel author authored them as pairs.
- **POST /stores**: writes only `core.stores`; `org_node_id` is optional and frequently NULL in smoke-test data.
- **POST /org-tree**: writes only `core.org_nodes`; no `stores` row is ever produced.

The integration test factories follow the same single-table posture. `tests/integration/conftest.py:485-543` — `make_store`:

```python
async def _make(
    *,
    tenant_id: UUID,
    org_node_id: UUID | None = None,
    ...
) -> Store:
    store = Store(tenant_id=tenant_id, org_node_id=org_node_id, ...)
```

`org_node_id` defaults to None. The fixture imposes no pairing. The companion fixture `make_org_node` (line 896) is independent — callers wire the two together by passing `org_node_id` explicitly if they want the link.

The `make_tenant(with_root=True)` variant (lines 357-401) is the only fixture that does pair multiple tables atomically — and it mirrors `TenantsRepo.create`'s atomic three-table contract for testability of anchor-dep-gated endpoints. No analogous `make_store(with_node=True)` exists.

### C9 — Other entry points

Production code paths that INSERT into `tenants` / `org_nodes` / `stores`:

| Site | Tables touched | Coupling |
|---|---|---|
| `repositories/tenants.py:566-715` `TenantsRepo.create` | tenants + org_nodes + tenant_module_access | **Atomic, enforced** (Step 6.20.1) |
| `repositories/org_nodes.py:332-430` `OrgNodesRepo.add_node` | org_nodes | None |
| `repositories/org_nodes.py:432-620` `OrgNodesRepo.edit_node` | org_nodes (target + subtree) | Self-table only |
| `repositories/stores.py:376-499` `StoresRepo.create` | stores | None |
| `repositories/stores.py:501-632` `StoresRepo.update` | stores | None |
| `repositories/stores.py:638-757` `StoresRepo.transition` | stores | None |
| `scripts/seed_dev_data/loaders/tenants.py` | tenants | Excel-paired only |
| `scripts/seed_dev_data/loaders/org_nodes.py` | org_nodes | Excel-paired only |
| `scripts/seed_dev_data/loaders/stores.py` | stores | Excel-paired only |
| `tests/integration/conftest.py::make_tenant(with_root=True)` | tenants + org_nodes | Atomic fixture mirror of TenantsRepo.create |
| `tests/integration/conftest.py::make_store` | stores | None |
| `tests/integration/conftest.py::make_org_node` | org_nodes | None |

Alembic migrations (`migrations/versions/`): no data migrations into tenants / org_nodes / stores. Migrations touch the lookups table and the Step 6.8.1 user_role_assignments split only.

`scripts/smoke_test.py` (lines 155+, 224+, 480+, 517+, 554+, 678+, 698+, 755+) is a DB-level RLS truth-table smoke; not a production code path.

**Net.** Two cross-table couplings exist anywhere in the codebase: `TenantsRepo.create` (tenants → org_nodes → modules) and the `make_tenant(with_root=True)` fixture that mirrors it. Everything else is single-table.

### C10 — Existing FN-AB / D-NN / LD-NN records

- **FN-AB-54 — Slug-truncation collision risk at tenant-root org_node insert** (`CLAUDE.md:1234`). Tracks slug-collision class; structurally unreachable at v0 because `uq_org_nodes_tenant_code_lower` is tenant-scoped and the tenant has no other nodes at insert time.
- **Step 6.20.1 record** (`CLAUDE.md:2076`). Documents the original "tenants without org_node root" bug and its fix; the fix was the POST `/tenants` write surface only. Did not extend the audit to POST `/stores` or POST `/org-tree`.
- **Step 6.13 LD-NN records** (`docs/implementation-steps/step-6_13-org-tree-writes-2026-05-16.md`). Locked decisions on cascade-order, reparent invariants, tenant-root protection. No paired-write decision.
- **Step 6.17.3 LD3** (`docs/implementation-steps/step-6_17_3-stores-writes-2026-05-18.md:22-24`). Locks `org_node_id` rejection on PATCH; "store ↔ org_node linkage mutability is deferred pending a product workflow decision; future loosening is additive per D-31."
- **Step 6.17.3 LD1 + retro** (lines 53, 78). Defends the multi-audience POST contract; does not address pairing.
- **OrgNodeCreateRequest docstring** (`schemas/org_node.py:264-267`): *"TENANT is rejected here (tenant roots are provisioned with the tenant)."* The phrase "provisioned with the tenant" is the only documented invariant in the codebase that names the pairing — and only for the tenant-root direction.
- **architecture.md Appendix A.3** (lines 749-779): documents `TenantsRepo.create`'s three-table atomicity, slug rule. Names `tenants → org_nodes` as the *load-bearing anchor*. Does not address stores.

No FN-AB / D-NN exists for the store ↔ STORE-type org_node decoupling. The prompt asks about a "2026-05-16 architectural-gap memory note." A search of CLAUDE.md, BUILD_PLAN.md, all docs/, and `~/.claude/projects/.../memory/` finds no such entry. The closest 2026-05-16 reference is Step 6.13's ship date and the FN-AB-47 catalogue-gap note bundled with it; neither documents the store↔org_node coupling concern. **The gap is unrecorded.**

### C11 — Conflation of `tenants.id` and `org_node.id` elsewhere

Grep on suspect patterns finds **zero** backend conflations. Every `WHERE id = :tenant_id` clause that surfaces in the grep is on the `core.tenants` table where `tenants.id IS the tenant_id` is correct by schema definition:

```
repositories/tenants.py:811   "WHERE id = :tenant_id RETURNING id"   (UPDATE tenants)
repositories/tenants.py:857   "WHERE id = :tenant_id FOR UPDATE"     (SELECT tenants)
repositories/tenants.py:881   "WHERE id = :tenant_id"                (UPDATE tenants)
repositories/tenants.py:895   "WHERE id = :tenant_id"                (UPDATE tenants)
repositories/stores.py:245    "SELECT 1 FROM {schema}.tenants WHERE id = :tenant_id"
repositories/tenant_users.py:522   "WHERE id = :tenant_id LIMIT 1"   (on tenants)
```

All correct. There is no spot where a `tenants.id` flows into a parameter expecting an `org_node_id`, nor vice versa. The backend is internally consistent. The known bug is purely a client-side ID-identity mismatch at a contract boundary that the backend has no opinion on (because that boundary doesn't exist in any current API).

---

## 3. Coupling map

| Pair | Endpoint(s) that touch both | Enforcement |
|---|---|---|
| `tenants` ↔ `org_nodes` | `POST /api/v1/tenants` (TenantsRepo.create, Step 6.20.1) | **Atomic** — same TX, rollback on failure |
| `tenants` ↔ `org_nodes` | `POST /api/v1/tenants/{tenant_id}/org-tree` (add_node) | Implicit — relies on tenant-root org_node already existing (returns 404 from anchor dep if not) |
| `tenants` ↔ `org_nodes` | All anchor-dep gated endpoints | Read-side dependency only; `get_tenant_anchor` raises 404 on missing root |
| `tenants` ↔ `stores` | `POST /api/v1/stores` (StoresRepo.create) | Implicit via `_tenant_exists` pre-check + FK |
| `tenants` ↔ `stores` | `GET /api/v1/stores`, `GET /api/v1/stores/{id}` | Read-side LEFT JOIN for `tenant_name` label |
| `org_nodes` ↔ `stores` | `POST /api/v1/stores` (StoresRepo.create) | **Missing** — `org_node_id` optional; no auto-create; no `node_type='STORE'` enforcement when supplied |
| `org_nodes` ↔ `stores` | `PATCH /api/v1/stores/{store_id}` | **Missing** — `org_node_id` immutable per LD3; no linkage workflow |
| `org_nodes` ↔ `stores` | `POST /api/v1/tenants/{tenant_id}/org-tree` (STORE-type) | **Missing** — no paired `stores` insert |
| `org_nodes` ↔ `stores` | `PATCH /api/v1/tenants/{tenant_id}/org-tree/{node_id}` | No cascade — linkage by id survives reparent/rename/recode |
| All three | Seed loader | Implicit via Excel pairing; loader is single-table |
| All three | Test fixtures | Single-table by default; `make_tenant(with_root=True)` is the only exception |

**Pattern:** only `TenantsRepo.create` (and its fixture mirror) owns an atomic cross-table contract. The other two pairs of writes are independent INSERTs that happen to live next to each other in the API.

---

## 4. Gap A — Add Org Node from TENANT row

### Exact failure mode

1. Frontend renders the org-tree (`GET /api/v1/tenants/{tenant_id}/org-tree`). Response carries `tenant_id` and `tree[]` but no tenant-root id. To present a clickable TENANT row, frontend synthesises one keyed on `data.tenant_id`.
2. User selects the synthetic TENANT row and submits a new node. Frontend sends `POST /api/v1/tenants/{tenant_id}/org-tree` with `parent_id = data.tenant_id`.
3. `OrgNodesRepo.add_node` calls `_select_for_update_node(tenant_id=tenant_id, node_id=parent_id)`.
4. The SQL `SELECT ... FROM core.org_nodes WHERE id = :node_id AND tenant_id = :tenant_id` returns no row because `tenants.id` is not equal to any `org_nodes.id` (independent `uuidv7()` values).
5. `_select_for_update_node` returns `None`.
6. `OrgNodesRepo.add_node` line 367-372 raises `ParentNodeNotFoundError`.
7. Response: 404 `PARENT_NODE_NOT_FOUND` with message `"parent_id=<tenants.id> not visible in tenant_id=<tenants.id>"`.

### Fix shapes (do not choose)

- **(A) Backend response-shape change.** Add a top-level `tenant_root_id: UUID` field to `OrgTreeResponse` (and optionally `tenant_root_code: str` / `tenant_root_path: str`). Single source of truth. Append-only per D-31. Touches one schema, one response builder, one OpenAPI regen. Requires frontend to read the new field and use it as the synthetic TENANT row's id.
- **(B) Backend translation in the handler.** Special-case: if `body.parent_id == tenant_id`, treat as "the tenant root" and look up the actual `org_nodes.id` server-side. Touches `routers/v1/org_tree.py::add_org_node` only. Hides the model violation rather than exposing the correct id. Loosens the principle that frontend-supplied UUIDs name backend resources directly.
- **(C) Include tenant root in `tree[]`.** Stop excluding TENANT-type nodes from the response builder. The TENANT row becomes a real entry in `tree[]` with its own `id`. Breaking change to the existing wire shape (`tree[]` would now have one extra root; clients reading position-0 as "first business unit" would regress).
- **(D) Frontend-only change.** Make the frontend issue an additional lookup to obtain the tenant root's id. There is no public endpoint to call; would need (A) or (C) as a prerequisite.

(A) is the smallest backend change and the most aligned with D-31's append-only field discipline. (B) and (C) move the joint contract burden onto the backend in different ways. (D) requires backend support anyway.

---

## 5. Gap B — store ↔ STORE-type org_node decoupling

### Where the link breaks on the write side

1. **`POST /api/v1/stores`** (`StoresRepo.create`, `repositories/stores.py:376-499`): writes only `core.stores`. When `org_node_id` is omitted (its default), the column is NULL. No fallback to create a matching STORE-type org_node. Result: a `stores` row exists with `org_node_id IS NULL` — exactly the 7-row Buc-ee's case in cloud.
2. **`POST /api/v1/tenants/{tenant_id}/org-tree`** with `node_type='STORE'` (`OrgNodesRepo.add_node`): writes only `core.org_nodes`. No `stores` row produced. Result: a STORE-type `org_nodes` row exists with no paired store — exactly the 8-row Buc-ee's case.
3. **`PATCH /api/v1/stores/{store_id}`**: `org_node_id` immutable per LD3. No way for a frontend to repair an existing NULL link.
4. **`PATCH /api/v1/tenants/{tenant_id}/org-tree/{node_id}`**: rename/recode/reparent only. No status change; no link-to-store operation.
5. **`POST /api/v1/stores`** with `org_node_id` supplied: `_check_org_node_for_store` enforces same-tenant and not-already-linked, but does **not** enforce `node_type='STORE'`. A HQ-type org_node could be linked. Latent looseness.

### Fix shapes (do not choose)

- **(A) Atomic single-endpoint.** Extend `POST /api/v1/stores` to create the paired STORE-type org_node when `org_node_id` is omitted. Requires a parent-org_node hint (e.g., implicit `parent_id` = tenant root, or new field `parent_org_node_id`). Mirrors `TenantsRepo.create`'s three-table atomicity. Highest discipline; biggest semantic decision (where does the new org_node hang from?). PATCH-side question separate.
- **(A') Atomic from the org-tree side.** Extend `POST /api/v1/tenants/{tenant_id}/org-tree` with `node_type='STORE'` to optionally create the paired store via a `store: {...}` sub-body. Symmetric to (A) but inverts the entry point. Same coupling problem reframed.
- **(B) Two-call with explicit linking.** Keep both endpoints single-table. Add a separate operation (e.g., `PATCH /api/v1/stores/{store_id}/link-org-node` or a dedicated `POST /api/v1/store-org-node-links`) for the link step. Frontend orchestrates: create store → create node → link. Three calls; the orchestration is the contract. Cleaner separation; weaker atomicity guarantee.
- **(B') PATCH org_node_id loosening.** Lift LD3 on `PATCH /api/v1/stores/{store_id}` so `org_node_id` becomes mutable (with same-tenant + not-already-linked checks). Reduces (B) from three calls to two: create store → POST org-tree node → PATCH store to link. Same coupling problem.
- **(C) Matching-by-code convention.** Add a server-side rule: when a STORE-type org_node is created with `code == some_store.store_code` (case-insensitive), auto-link. Implicit, fragile, surprising. Mentioned for completeness; weakest option.
- **(D) Tighten POST `/stores`'s org_node check.** Add `node_type='STORE'` enforcement to `_check_org_node_for_store`. Independent fix; closes the latent looseness in C4 but does not solve the missing-pair case.

(A) and (A') align with `TenantsRepo.create`'s precedent: when a logical entity ("a store at a place") spans tables, one endpoint owns the write. (B)/(B') align with the current per-endpoint decoupled posture but punt the contract to the frontend. (D) is orthogonal and could land alongside any of (A)–(C).

---

## 6. Shared root cause

The operator's hypothesis is correct. Both gaps share one root cause: **the joint contract across `tenants ↔ org_nodes ↔ stores` was never designed as a unit.**

Stage 1's read-only foundation never had to deal with cross-table writes; the seed populated everything coherently and the API only read it. Stage 2 introduced writes one table at a time, in this order: 6.10.1 tenant_users, 6.11 tenants (single-table at the time), 6.13 org_nodes, 6.15 tenant_module_access, 6.17.2-4 stores. Each step owned its own scope. The cross-table issue surfaced once at Step 6.20.1 (`TenantsRepo.create` not provisioning the tenant-root org_node), was patched at the seam where it bit, and the **pattern was not generalised**.

The Step 6.17.3 retro (line 78c) names LD1 as needing an app-layer "tenant_id verified against RLS-bound session" pre-check — the same shape of cross-table concern — and resolves it for that one method. LD3 (line 22-24) defers `org_node_id` mutability with the note "pending a product workflow decision." That deferral is exactly the missing piece: there is no documented product workflow for the cross-table store ↔ org_node relationship, so each individual endpoint correctly does only what it knows about, and the joint contract has no home.

Step 6.20.1 fixed Pair (tenants ↔ org_nodes) at one seam. Pair (stores ↔ org_nodes) is the same shape and is still unfixed. Pair (tenants ↔ stores) is the only pair where a contract was never needed — `stores.tenant_id` is enough.

---

## 7. Documentation gaps

- **No FN-AB tracks the store ↔ STORE-type-org_node coupling.** FN-AB-54 covers slug collisions; FN-AB-37 covers multi-audience PATCH; FN-AB-38/39/40/41 cover tenant_user concerns; FN-AB-42/43 cover module_access. None name the store-side coupling.
- **No D-NN locks the joint write contract.** D-13 (audit-actor patterns), D-17 (RLS-as-404), D-30 (list envelope), D-31 (append-only fields) are all in play but none address paired writes.
- **`OrgTreeResponse` schema does not document the "tenant_root_id absent" decision** as a known limitation. Doc says "TENANT-type nodes are excluded" but doesn't surface the downstream implication for write callers.
- **Step 6.13 step doc does not address STORE-type creation semantics.** The mental-model section focuses on cascade order and ltree mechanics; it never says "if you create a STORE-type node here, you separately do POST /stores."
- **Step 6.17.3 LD3 deferral is unresolved.** The "pending a product workflow decision" never had a follow-on. There is no FN-AB tracking the deferral.
- **architecture.md Appendix A.3 lists `TenantsRepo.create` as the only atomic three-table site** but does not generalise the pattern to other multi-table relationships. No Appendix A.4 / A.5 for stores ↔ org_nodes.
- **No `tenant_root_id` field in any OrgTreeResponse-shape documentation** (`docs/endpoints/org-tree.md`). The frontend has no documented way to obtain it.
- **The investigation prompt references a "2026-05-16 architectural-gap memory note (CLAUDE.md 'recent_updates' or similar)" that I could not find in CLAUDE.md, BUILD_PLAN.md, docs/, or the `.claude/.../memory/` directory.** Either it was never committed, lives somewhere outside the searched paths, or is referenced from chat context I don't have. Worth confirming whether such a note exists before the design discussion.

Incidental (out-of-scope for this investigation but surfaced and worth flagging):

- `_check_org_node_for_store` in `repositories/stores.py:306-374` does not enforce `org_nodes.node_type = 'STORE'`. A latent looseness.
- The error path for "no parent / no root" returns the same 404 code regardless of whether the row was RLS-filtered or genuinely absent (`ParentNodeNotFoundError`'s message is descriptive but the wire code is opaque). Aligns with D-17 intentionally; mentioned for completeness.

---

## 8. Open questions for the design discussion

1. Should the tenant-root `org_node.id` be exposed in `OrgTreeResponse` as a new top-level field, or should the response include the tenant-root inside `tree[]` (breaking the "excluded from response" property)?
2. Should `POST /api/v1/stores` accept `org_node_id` only as a reference to an already-created STORE-type org_node, or should it own atomic creation of the paired org_node when omitted?
3. If atomic creation, where should the new STORE-type org_node hang in the tree by default — the tenant root, or a caller-supplied parent? What is the product semantic for "a store under a specific region"?
4. Should `_check_org_node_for_store` enforce `node_type = 'STORE'`, or is it intentional that other node_types can link to stores?
5. Should `PATCH /api/v1/stores/{store_id}` allow `org_node_id` mutation, or is per-store immutable linkage the product intent?
6. Should the org-tree write surface (`POST /api/v1/tenants/{tenant_id}/org-tree` with `node_type='STORE'`) optionally create a paired store, or is the inverse direction (POST /stores owns the pair) the canonical entry point?
7. Are the 7 NULL-`org_node_id` stores and 8 orphan STORE-type org_nodes in Buc-ee's smoke-test debris to delete, or do they represent real product state that needs backfilling once the joint contract is decided?
8. Should the "atomic-multi-table-write" pattern from Step 6.20.1 (`TenantsRepo.create`) be generalised into a documented convention (a new D-NN), or treated as a one-off because the relationship between specific table pairs is product-specific?
9. Should a new FN-AB or D-NN open now to track the coupling decision before Step 6.16 (audit-log emission) ships, so audit emission designs for paired writes correctly the first time?
10. Does the prompt's "2026-05-16 architectural-gap memory note" exist somewhere outside the searched paths? If so, where, and does it predetermine any of the above?

---

## End of investigation
