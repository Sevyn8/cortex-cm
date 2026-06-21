# Org-tree endpoints

Canonical endpoint documentation for the Organization Tree page (Frontend spec 7.3). Two GET endpoints under `/api/v1/tenants/{tenant_id}/...`. Format follows CLAUDE.md "Per-endpoint documentation" — eight fixed sections per endpoint. Mirrors `tenant-users.md`'s structure with one structural difference: the E2 response is a **deliberate D-30 exception** — the org-tree is a singleton resource for the tenant, not a paginatable collection, so the envelope is `{tenant_id, tenant_name, stats, tree}` rather than `{items, pagination}`. E3 follows D-30 normally.

| Endpoint | Description | Calling user types |
|---|---|---|
| `GET /api/v1/tenants/{tenant_id}/org-tree` | Initial tree fetch with smart-default behavior | PLATFORM (any tenant) and TENANT (own only; cross-tenant -> 404) |
| `GET /api/v1/tenants/{tenant_id}/org-nodes/{node_id}/children` | Lazy-load immediate children of a specific node | PLATFORM (any tenant) and TENANT (own only; cross-tenant -> 404) |

Cross-cutting:

- **Auth** — `Authorization: Bearer <jwt>` required; missing or invalid -> 401.
- **No PLATFORM-only gate.** Both user types accepted; visibility scoping is the DB layer's job via RLS via `org_nodes_tenant_isolation`. PLATFORM JWTs see all rows via D-29's unconditional OR-branch; TENANT JWTs see only rows matching `app.tenant_id`.
- **Cross-tenant requests return 404, not 403.** A TENANT-A user requesting TENANT-B's tenant or node receives 404 (`TENANT_NOT_FOUND` for E2 cross-tenant; `TENANT_NOT_FOUND` or `ORG_NODE_NOT_FOUND` for E3 cross-tenant depending on which check fires first). Per D-17 (RLS-as-404). Tests T12 (E2) and T18 (E3) are LOAD-BEARING for this end-to-end.
- **Smart-default (E2).** Server picks full vs depth-limited mode based on tenant size; frontend doesn't pass `depth` by default. Locked tunables: full-tree threshold ≤500 nodes, default lazy-mode depth=4, max depth=6, payload cap 1000 nodes (auto-reduces depth on overflow).
- **Lazy-load metadata.** Each `OrgNodeTreeItem` carries `has_children`, `child_count`, `loaded_children`. Frontend uses these to decide which subtrees to lazy-fetch via E3.
- **Field semantics** — append-only per D-31. Once a field's meaning ships, it stays.
- **Hidden fields.** All six Pattern (b) audit-actor columns (`created_by_*`, `updated_by_*`, `archived_by_*`) — internal lineage, not for UI.
- **Error envelope** — `{code, message, details, request_id}` on all server-generated errors. `details` is `null` in v0.
- **`X-Request-Id`** — set on every response by the audit middleware.
- **RBAC** — not enforced in v0; lands at Step 6.1.
- **Filtering.** v0 returns ACTIVE nodes only. INACTIVE / ARCHIVED filters are reserved for future. The `status` field is exposed on each item so frontend code is forward-compatible.

---

## `GET /api/v1/tenants/{tenant_id}/org-tree`

Initial org-tree fetch with smart-default behavior (E2).

### 1. Endpoint summary

- **Method:** `GET`
- **Path:** `/api/v1/tenants/{tenant_id}/org-tree`
- **Description:** Returns the tenant's org tree. Smart-default: small tenants get the full tree; larger tenants get a depth-limited tree.
- **Who can call:** any authenticated user. PLATFORM sees any tenant; TENANT sees only own tenant (cross-tenant -> 404).

### 2. Request

**Headers:**

| Header | Required | Notes |
|---|---|---|
| `Authorization` | Yes | `Bearer <jwt>` (PLATFORM or TENANT) |
| `Accept` | No | Defaults to `application/json` |

**Path parameters:**

| Param | Type | Notes |
|---|---|---|
| `tenant_id` | UUID string | Target tenant. 404 if missing or RLS-filtered. |

**Query parameters:**

| Param | Type | Default | Validation |
|---|---|---|---|
| `depth` | int | (server picks) | `>=1, <=6`. Optional. If omitted, server picks: full tree for tenants with ≤500 ACTIVE non-TENANT nodes, depth=4 otherwise. depth=1 -> HQ-level only; depth=4 -> HQ + 3 mid-levels. |

**Request body:** none.

### 3. Response 200

**Small tenant, full tree:**

```json
{
  "tenant_id": "01928fd1-0000-7000-aaaa-000000000001",
  "tenant_name": "Buc-ee's",
  "tenant_root_id": "01928fd1-0000-7000-bbbb-000000000001",
  "tenant_root_code": "BUC-EES",
  "tenant_root_path": "buc_ees",
  "stats": {
    "total_nodes": 8,
    "nodes_returned": 8,
    "stores": 3,
    "regions": 2,
    "depth_returned": 4,
    "truncated": false
  },
  "tree": [
    {
      "id": "01928fd1-0000-7000-bbbb-000000000010",
      "node_type": "HQ",
      "name": "Buc-ee's HQ",
      "code": "BU-HQ",
      "status": "ACTIVE",
      "created_at": "2026-04-19T15:00:00+00:00",
      "updated_at": "2026-04-19T15:00:00+00:00",
      "has_children": true,
      "child_count": 2,
      "loaded_children": "all",
      "children": [
        {
          "id": "01928fd1-0000-7000-bbbb-000000000020",
          "node_type": "REGION",
          "name": "Florida Region",
          "code": "FL",
          "status": "ACTIVE",
          "created_at": "2026-04-19T15:00:00+00:00",
          "updated_at": "2026-04-19T15:00:00+00:00",
          "has_children": true,
          "child_count": 1,
          "loaded_children": "all",
          "children": [/* ... */]
        }
      ]
    }
  ]
}
```

**Large tenant, depth-limited (default depth=4):**

```json
{
  "tenant_id": "...",
  "tenant_name": "GlobalRetailer Co.",
  "tenant_root_id": "...",
  "tenant_root_code": "GR",
  "tenant_root_path": "gr",
  "stats": {
    "total_nodes": 3247,
    "nodes_returned": 156,
    "stores": 0,
    "regions": 84,
    "depth_returned": 4,
    "truncated": false
  },
  "tree": [
    {
      "node_type": "HQ",
      "has_children": true,
      "child_count": 5,
      "loaded_children": "all",
      "children": [
        {
          "node_type": "BUSINESS_UNIT",
          "has_children": true,
          "child_count": 14,
          "loaded_children": "all",
          "children": [
            {
              "node_type": "REGION",
              "has_children": true,
              "child_count": 220,
              "loaded_children": "none",
              "children": []
            }
          ]
        }
      ]
    }
  ]
}
```

**Truncated variant** (server reduced depth due to payload cap):

```json
{
  "tenant_id": "...",
  "tenant_name": "...",
  "tenant_root_id": "...",
  "tenant_root_code": "...",
  "tenant_root_path": "...",
  "stats": {
    "total_nodes": 5840,
    "nodes_returned": 980,
    "stores": 0,
    "regions": 0,
    "depth_returned": 3,
    "truncated": true
  },
  "tree": [/* depth-3 tree */]
}
```

**Tenant with only the implicit root** (no descendants beyond the tenant root):

```json
{
  "tenant_id": "...",
  "tenant_name": "FreshMart Co-op",
  "tenant_root_id": "...",
  "tenant_root_code": "FM",
  "tenant_root_path": "fm",
  "stats": {"total_nodes": 0, "nodes_returned": 0, "stores": 0, "regions": 0, "depth_returned": 0, "truncated": false},
  "tree": []
}
```

**Top-level fields:**

| Field | Type | Nullable | Notes |
|---|---|---|---|
| `tenant_id` | UUID string | No | Echo of path-param `tenant_id` (the `tenants.id` value). |
| `tenant_name` | string | No | Current `tenants.name`. |
| `tenant_root_id` | UUID string | No | The `org_nodes.id` of the tenant-root (the implicit TENANT-typed node). Distinct from `tenant_id`; use this as `parent_id` on POST /org-tree to create a top-level node directly under the tenant. Added at Step 6.21.1; see `docs/investigations/2026-05-20-write-surface-coupling.md` for the bug it unblocks. |
| `tenant_root_code` | string | No | The tenant-root org_node's `code` (matches `org_nodes.code`). |
| `tenant_root_path` | string | No | The tenant-root org_node's ltree `path` (single label; matches `org_nodes.path::text`). All descendants' paths inherit this as their root segment. |
| `stats` | `OrgTreeStats` | No | See below. |
| `tree` | array of `OrgNodeTreeItem` | No (list, may be empty) | Top-level nodes (children of the tenant root). The tenant root itself is NOT in this list; see the `tenant_root_*` fields above. |

**Stats reference:**

| Field | Type | Notes |
|---|---|---|
| `total_nodes` | int | Full count of non-TENANT ACTIVE nodes for the tenant (entire tree, not just response). |
| `nodes_returned` | int | Count of nodes in `tree` (recursively). Equals `total_nodes` when `truncated=false`. |
| `stores` | int | Count of `node_type='STORE'` IN the response. May undercount full tree when depth-limited. |
| `regions` | int | Count of `node_type='REGION'` IN the response. |
| `depth_returned` | int | Max depth in response (depth from TENANT root). 0 for empty tree. |
| `truncated` | bool | True if server auto-reduced depth below requested due to payload cap. Frontend should display a "partial tree" notice. |

**OrgNodeTreeItem reference:**

| Field | Type | Nullable | Notes |
|---|---|---|---|
| `id` | UUID string | No | UUIDv7 from DB DEFAULT |
| `node_type` | enum string | No | One of `BUSINESS_UNIT`, `HQ`, `COUNTRY`, `REGION`, `STORE`, `DEPARTMENT`. TENANT-type nodes are excluded from all responses (the implicit root) |
| `name` | string | No | 1-200 chars |
| `code` | string | No | Tenant-unique short code (e.g., `TX`, `BU-HQ`) |
| `status` | enum string | No | Always `ACTIVE` in v0 |
| `created_at` | ISO 8601 with offset | No | When the row was inserted |
| `updated_at` | ISO 8601 with offset | No | Most recent update |
| `has_children` | bool | No | True if this node has any ACTIVE immediate children, regardless of whether they're in this response |
| `child_count` | int | No | Count of ACTIVE immediate children. Always reflects the FULL subtree (server-side count), not what's in the response |
| `loaded_children` | enum string | No | `"all"`, `"partial"`, or `"none"`. See semantics below |
| `children` | array of `OrgNodeTreeItem` | No (list, may be empty) | Recursive. Empty for leaves AND for depth-cut subtrees |

**`loaded_children` semantics:**

| State | Meaning | Frontend action |
|---|---|---|
| `"all"` | Every child is in `children`. `len(children) == child_count` | None — fully loaded |
| `"partial"` | Some children present; more available via E3 with `offset > 0` | Optional: paginate via E3 |
| `"none"` + `has_children=false` | True leaf node | None — no children to load |
| `"none"` + `has_children=true` | Depth-cut subtree — children exist but cut by depth | Call E3 to load |

### 4. Response codes

| Code | When | Body |
|---|---|---|
| 200 | Success | Body as above |
| 401 | Missing or invalid JWT | `{code: "AUTH_MISSING" \| "AUTH_INVALID", message, details: null, request_id}` |
| 404 | Tenant doesn't exist OR is RLS-filtered (cross-tenant from TENANT JWT) | `{code: "TENANT_NOT_FOUND", message: "Tenant not found", details: null, request_id}` |
| 422 | Malformed `tenant_id` UUID; `depth` out of range | FastAPI default validation envelope |
| 500 | Internal server error | `{code: "INTERNAL_ERROR", message: "An internal error occurred", details: null, request_id}` |

### 5. Behaviour notes

- **ACTIVE-only filter (v0).** INACTIVE / ARCHIVED nodes are excluded from all responses and from `child_count` aggregates.
- **Sibling order.** Alphabetical by lowercased `code`. Falls out of ltree path-ASC ordering at the SQL layer (path labels are `lower(code).replace('-', '_')`).
- **Smart-default thresholds** (server-side, locked):
  - `FULL_TREE_THRESHOLD = 500`. Tenants with ≤500 ACTIVE non-TENANT nodes get full tree.
  - `DEFAULT_DEPTH = 4`. Lazy-mode depth when no explicit `depth` is provided.
  - `MAX_DEPTH = 6`. Maximum `depth` query-param value.
  - `PAYLOAD_CAP = 1000`. If response exceeds this, server reduces depth and sets `truncated=true`. Bounded retry — at most 2 reductions.
- **Truncation.** When the server reduces depth due to payload cap, `truncated=true` and `depth_returned` reflects the reduced value. Frontend should display a "tree partially loaded" notice.
- **TENANT-type nodes excluded** from `tree` and from all stats. The implicit tenant root is the parent of `tree[*]` but is itself never returned.
- **Performance.** Tested for trees up to ~1000 nodes serializing in <100ms. Pathological cases auto-truncate.
- **Race-condition reconciliation.** Frontends with rapid-tenant-switching should compare `response.tenant_id` against `currentSelection.id` and discard responses that no longer match.

### 6. Example calls

```bash
JWT=$(./scripts/jwt/generate.sh anjali@ithina.com)
TENANT_ID=$(psql $DATABASE_URL -tAc "SELECT id FROM tenants WHERE name = 'Buc-ee''s' LIMIT 1")

# Smart-default (server picks)
curl -s -H "Authorization: Bearer $JWT" \
  "http://localhost:8000/api/v1/tenants/$TENANT_ID/org-tree" | jq .

# Explicit depth
curl -s -H "Authorization: Bearer $JWT" \
  "http://localhost:8000/api/v1/tenants/$TENANT_ID/org-tree?depth=2" | jq .
```

### 7. Sample integration code

```typescript
type LoadedState = "all" | "partial" | "none";

interface OrgNodeTreeItem {
  id: string;
  node_type: "BUSINESS_UNIT" | "HQ" | "COUNTRY" | "REGION" | "STORE" | "DEPARTMENT";
  name: string;
  code: string;
  status: "ACTIVE" | "INACTIVE" | "ARCHIVED";
  created_at: string;
  updated_at: string;
  has_children: boolean;
  child_count: number;
  loaded_children: LoadedState;
  children: OrgNodeTreeItem[];
}

interface OrgTreeResponse {
  tenant_id: string;
  tenant_name: string;
  stats: {
    total_nodes: number;
    nodes_returned: number;
    stores: number;
    regions: number;
    depth_returned: number;
    truncated: boolean;
  };
  tree: OrgNodeTreeItem[];
}

async function fetchOrgTree(tenantId: string, jwt: string): Promise<OrgTreeResponse> {
  const r = await fetch(`/api/v1/tenants/${tenantId}/org-tree`, {
    headers: { Authorization: `Bearer ${jwt}` },
  });
  if (!r.ok) throw new Error(`Org-tree fetch failed: ${r.status}`);
  return r.json();
}

// Lazy-fetch on expand
async function expandNode(tenantId: string, nodeId: string, jwt: string) {
  const r = await fetch(
    `/api/v1/tenants/${tenantId}/org-nodes/${nodeId}/children`,
    { headers: { Authorization: `Bearer ${jwt}` } }
  );
  return r.json();
}

// Disambiguate leaf vs depth-cut
function shouldLoadChildren(node: OrgNodeTreeItem): boolean {
  return node.has_children && node.loaded_children !== "all";
}
```

### 8. Implementation reference

- Router: `src/admin_backend/routers/v1/org_tree.py` (`get_org_tree`)
- Repo: `src/admin_backend/repositories/org_nodes.py` (`count_active_by_tenant`, `list_active_with_child_counts`)
- Tree builder: `_build_tree` in the router module
- Schemas: `src/admin_backend/schemas/org_node.py` (`OrgTreeResponse`, `OrgTreeStats`, `OrgNodeTreeItem`)
- Tests: `tests/integration/test_org_tree_router.py` (T1-T14, T20, T21)

---

## `GET /api/v1/tenants/{tenant_id}/org-nodes/{node_id}/children`

Lazy-load immediate children of a specific org-node (E3).

### 1. Endpoint summary

- **Method:** `GET`
- **Path:** `/api/v1/tenants/{tenant_id}/org-nodes/{node_id}/children`
- **Description:** Returns paginated immediate ACTIVE children of `node_id` within `tenant_id`. Used by the frontend to lazy-load subtrees not in the initial E2 response.
- **Who can call:** any authenticated user. RLS-scoped to caller's visibility.

### 2. Request

**Headers:**

| Header | Required | Notes |
|---|---|---|
| `Authorization` | Yes | `Bearer <jwt>` (PLATFORM or TENANT) |

**Path parameters:**

| Param | Type | Notes |
|---|---|---|
| `tenant_id` | UUID string | Target tenant. 404 if missing or RLS-filtered. |
| `node_id` | UUID string | Parent node id. 404 if not ACTIVE within this tenant (cross-tenant or missing). |

**Query parameters:**

| Param | Type | Default | Validation |
|---|---|---|---|
| `offset` | int | `0` | `>=0` |
| `limit` | int | `100` | `>=1, <=200`. Above the cap -> 422. |

**Request body:** none.

### 3. Response 200

```json
{
  "node_id": "01928fd1-0000-7000-bbbb-000000000010",
  "items": [
    {
      "id": "01928fd1-0000-7000-bbbb-000000000020",
      "node_type": "REGION",
      "name": "Florida Region",
      "code": "FL",
      "status": "ACTIVE",
      "created_at": "2026-04-19T15:00:00+00:00",
      "updated_at": "2026-04-19T15:00:00+00:00",
      "has_children": true,
      "child_count": 1,
      "loaded_children": "none",
      "children": []
    },
    {
      "id": "01928fd1-0000-7000-bbbb-000000000021",
      "node_type": "REGION",
      "name": "Texas Region",
      "code": "TX",
      "status": "ACTIVE",
      "created_at": "2026-04-19T15:00:00+00:00",
      "updated_at": "2026-04-19T15:00:00+00:00",
      "has_children": true,
      "child_count": 2,
      "loaded_children": "none",
      "children": []
    }
  ],
  "pagination": {"total": 2, "offset": 0, "limit": 100}
}
```

**Field reference:** same `OrgNodeTreeItem` shape as E2. Each child's `loaded_children` is always `"none"` — E3 fetches one level only; grandchildren require another E3 call.

**Pagination block:**

| Field | Type | Notes |
|---|---|---|
| `total` | int | Total ACTIVE children of `node_id` matching the filter (RLS-aware) |
| `offset` | int | Echo of request `offset` |
| `limit` | int | Echo of request `limit` |

### 4. Response codes

| Code | When | Body |
|---|---|---|
| 200 | Success (including parent with no children: `items=[], total=0`) | Body as above |
| 401 | Missing or invalid JWT | `{code: "AUTH_MISSING" \| "AUTH_INVALID", message, details: null, request_id}` |
| 404 | Tenant doesn't exist / is RLS-filtered (cross-tenant) | `{code: "TENANT_NOT_FOUND", message: "Tenant not found", details: null, request_id}` |
| 404 | `node_id` doesn't exist ACTIVE within the tenant (or RLS-filtered) | `{code: "ORG_NODE_NOT_FOUND", message: "Org node not found", details: null, request_id}` |
| 422 | Malformed UUID; `limit` out of range | FastAPI default validation envelope |
| 500 | Internal server error | `{code: "INTERNAL_ERROR", message: "An internal error occurred", details: null, request_id}` |

### 5. Behaviour notes

- **404 disambiguation order.** Tenant is resolved first; if missing/RLS-filtered -> `TENANT_NOT_FOUND`. Else `node_exists` check; if false -> `ORG_NODE_NOT_FOUND`. Both surface as 404 to avoid information disclosure.
- **`node_exists` is a separate query** from the children fetch. Distinguishes "parent has no children" (200, `items=[], total=0`) from "parent doesn't exist" (404). Tracked as DP-3 in the Step 5.3 prompt — separate method picked for clarity.
- **Each child's `loaded_children='none'`** by design. E3 always returns shallow data; UI renders the expand-arrow based on `has_children` and calls E3 again on click.
- **Sibling order** = alphabetical by lowercased `code`, same as E2.
- **Pagination is offset/limit**, not cursor-based, to keep frontend pagination simple (rare to have >100 siblings; even rarer >200).

### 6. Example calls

```bash
JWT=$(./scripts/jwt/generate.sh anjali@ithina.com)
TENANT_ID=$(psql $DATABASE_URL -tAc "SELECT id FROM tenants WHERE name = 'Buc-ee''s' LIMIT 1")
HQ_ID=$(psql $DATABASE_URL -tAc \
  "SELECT id FROM org_nodes WHERE tenant_id='$TENANT_ID' AND node_type='HQ' LIMIT 1")

curl -s -H "Authorization: Bearer $JWT" \
  "http://localhost:8000/api/v1/tenants/$TENANT_ID/org-nodes/$HQ_ID/children" | jq .

# Pagination
curl -s -H "Authorization: Bearer $JWT" \
  "http://localhost:8000/api/v1/tenants/$TENANT_ID/org-nodes/$HQ_ID/children?offset=0&limit=10" | jq .
```

### 7. Sample integration code

```typescript
async function loadChildrenIncrementally(
  tenantId: string,
  nodeId: string,
  jwt: string
): Promise<OrgNodeTreeItem[]> {
  const all: OrgNodeTreeItem[] = [];
  let offset = 0;
  const limit = 100;
  while (true) {
    const r = await fetch(
      `/api/v1/tenants/${tenantId}/org-nodes/${nodeId}/children?offset=${offset}&limit=${limit}`,
      { headers: { Authorization: `Bearer ${jwt}` } }
    );
    const body = await r.json();
    all.push(...body.items);
    if (offset + body.items.length >= body.pagination.total) break;
    offset += body.items.length;
  }
  return all;
}
```

### 8. Implementation reference

- Router: `src/admin_backend/routers/v1/org_tree.py` (`get_node_children`)
- Repo: `src/admin_backend/repositories/org_nodes.py` (`list_children_paginated`, `node_exists`)
- Schemas: `src/admin_backend/schemas/org_node.py` (`OrgNodeChildrenResponse`)
- Tests: `tests/integration/test_org_tree_router.py` (T15-T19)

---

## POST `/api/v1/tenants/{tenant_id}/org-tree` (Add Node)

### 1. Endpoint summary

| | |
|---|---|
| Method | POST |
| Path | `/api/v1/tenants/{tenant_id}/org-tree` |
| Description | Add a new org_node under an existing parent in the tenant. |
| Who can call | Ithina staff with `ADMIN.ORG_NODES.CONFIGURE.GLOBAL` (Super Admin, Platform Admin) — cascades to TENANT scope. Tenant Owner with `ADMIN.ORG_NODES.CONFIGURE.TENANT` directly. |

### 2. Request

| | |
|---|---|
| Auth | Bearer JWT (PLATFORM or TENANT). |
| Path params | `tenant_id` (UUID) — target tenant. |
| Query params | none |
| Body | `OrgNodeCreateRequest` |

Body fields:

| Field | Type | Required | Description |
|---|---|---|---|
| `parent_id` | UUID | yes | Existing org_node in the same tenant. Must sit strictly above the new node in the cascade order. |
| `node_type` | enum | yes | One of `BUSINESS_UNIT`, `HQ`, `COUNTRY`, `REGION`, `DEPARTMENT`. `TENANT` is rejected (tenant roots are created at tenant provisioning). `STORE` is rejected (Step 6.21.2; use POST `/api/v1/stores` which creates both the store and the paired STORE-type org_node atomically). |
| `code` | string | yes | 1-64 chars, alphanumerics with hyphens, must start and end alphanumeric, no underscores. Tenant-unique case-insensitive. |
| `name` | string | yes | 1-200 chars. |

Cascade-order rule. A node's parent must sit higher in the canonical hierarchy: Tenant -> Business Unit -> HQ -> Country -> Region -> Store -> Department. Level skipping is allowed (a Store can sit directly under the Tenant root in a small chain). Level reversal is not (a Region cannot be a child of a Store; a Store cannot be a child of a Store).

### 3. Response 200

`201 Created` with the full `OrgNodeRead` shape. Includes the persisted `id`, the computed `path` (ltree), and timestamps populated by the DB.

```json
{
  "id": "019e3170-b682-7d84-b43e-a93995ee544e",
  "tenant_id": "019e3170-b686-7882-98db-2b811a9b0d78",
  "parent_id": "019e3170-b685-7c11-9a30-cb7c01d5a212",
  "node_type": "STORE",
  "code": "tx-store-101",
  "name": "Texas Store 101",
  "status": "ACTIVE",
  "path": "bucees.tx_region.tx_store_101",
  "created_at": "2026-05-16T13:42:00Z",
  "updated_at": "2026-05-16T13:42:00Z"
}
```

### 4. Response codes

| Status | `code` | When |
|---|---|---|
| 201 | (none, body is the new node) | Happy. |
| 401 | `AUTH_MISSING` / `AUTH_INVALID` | Bearer absent or invalid. |
| 403 | `PERMISSION_DENIED` | Authenticated caller lacks `ADMIN.ORG_NODES.CONFIGURE.TENANT` (directly or via cascade). |
| 404 | `TENANT_NOT_FOUND` | `tenant_id` not visible (cross-tenant attempt or unknown). |
| 404 | `PARENT_NODE_NOT_FOUND` | `parent_id` does not exist in the same tenant. |
| 409 | `DUPLICATE_ORG_NODE_CODE` | `code` collides (case-insensitive) with an existing row in the tenant. |
| 422 | `INVALID_PARENT_NODE_TYPE` | Cascade-order rule violated (parent ord >= child ord). |
| 422 | (Pydantic validation) | `node_type='TENANT'` or `node_type='STORE'` (Step 6.21.2), invalid code format, missing field, etc. |

Tenant-root provisioning note. The tenant root org_node is created automatically when a tenant is provisioned. POST cannot create a `TENANT`-type node; the request is rejected at Pydantic with 422.

Step 6.21.2 store-rejection note. `STORE`-type org_nodes are now created exclusively via POST `/api/v1/stores` (the server creates the paired `stores` row and the STORE-type `org_node` atomically; see `docs/architecture.md` § A.5). POST `/org-tree` with `node_type='STORE'` returns 422.

### 5. Behaviour notes

- RLS scopes visibility per D-29; cross-tenant calls surface as 404 (D-17).
- The new row's `path` is `parent.path || lower(code).replace('-', '_')`. ltree label syntax disallows hyphens; the substitution keeps the in-DB label valid while preserving the human-readable `code`.
- `status` is server-forced to `ACTIVE`. INACTIVE / ARCHIVED are out of scope for v0.
- The repo selects the parent `FOR UPDATE` before INSERTing so two concurrent adds against the same parent serialize cleanly. The DDL UNIQUE index on `(tenant_id, lower(code))` is the final arbiter.

### 6. Example calls

```sh
curl -X POST "https://admin.ithina.com/api/v1/tenants/${TENANT}/org-tree" \
  -H "Authorization: Bearer ${JWT}" \
  -H "Content-Type: application/json" \
  -d '{
    "parent_id": "019e3170-b685-7c11-9a30-cb7c01d5a212",
    "node_type": "STORE",
    "code": "tx-store-101",
    "name": "Texas Store 101"
  }'
```

### 7. Sample integration code

```ts
type AddNodeBody = {
  parent_id: string;
  node_type: "BUSINESS_UNIT" | "HQ" | "COUNTRY" | "REGION" | "STORE" | "DEPARTMENT";
  code: string;
  name: string;
};

async function addOrgNode(tenantId: string, body: AddNodeBody) {
  const resp = await fetch(`/api/v1/tenants/${tenantId}/org-tree`, {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${jwt}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
  if (!resp.ok) throw new Error(`${resp.status}: ${await resp.text()}`);
  return resp.json();
}
```

### 8. Implementation reference

- Router: `src/admin_backend/routers/v1/org_tree.py` (`add_org_node`)
- Repo: `src/admin_backend/repositories/org_nodes.py` (`add_node`, `_check_cascade_order`)
- Schemas: `src/admin_backend/schemas/org_node.py` (`OrgNodeCreateRequest`, `OrgNodeRead`)
- Errors: `src/admin_backend/errors.py` (`InvalidParentNodeTypeError`, `DuplicateOrgNodeCodeError`, `ParentNodeNotFoundError`)
- Tests: `tests/integration/test_org_tree_writes_router.py` (C1-C3, V1-V7, P1-P4, PA1)

---

## PATCH `/api/v1/tenants/{tenant_id}/org-tree/{node_id}` (Edit Node)

### 1. Endpoint summary

| | |
|---|---|
| Method | PATCH |
| Path | `/api/v1/tenants/{tenant_id}/org-tree/{node_id}` |
| Description | Rename, recode, and/or reparent an existing org_node. `node_type` is immutable. |
| Who can call | Same as POST. |

### 2. Request

Path params: `tenant_id` (UUID), `node_id` (UUID).
Body: `OrgNodePatchRequest`. At least one of `name`, `code`, `parent_id` must be set.

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | optional | 1-200 chars. |
| `code` | string | optional | 1-64 chars; same format and uniqueness rules as POST. |
| `parent_id` | UUID | optional | Reparent the node. The node and every descendant under it have their ltree paths rewritten atomically. |

### 3. Response 200

`200 OK` with the updated `OrgNodeRead` shape (same fields as POST).

### 4. Response codes

| Status | `code` | When |
|---|---|---|
| 200 | (none) | Happy. |
| 401 | `AUTH_MISSING` / `AUTH_INVALID` | Bearer absent or invalid. |
| 403 | `PERMISSION_DENIED` | Lack of grant. |
| 404 | `TENANT_NOT_FOUND` | Tenant not visible. |
| 404 | `ORG_NODE_NOT_FOUND` | `node_id` not visible. |
| 404 | `PARENT_NODE_NOT_FOUND` | `parent_id` (in body) not visible. |
| 409 | `DUPLICATE_ORG_NODE_CODE` | New `code` collides. |
| 422 | `CYCLE_DETECTED` | `parent_id` is self or a descendant. |
| 422 | `INVALID_PARENT_NODE_TYPE` | Reparent violates cascade order. |
| 422 | `TENANT_ROOT_NOT_REPARENTABLE` | Target is the tenant root and `parent_id` was set. |
| 422 | `ORG_NODE_FIELD_NOT_ALLOWED_FOR_TYPE` | Step 6.21.2 — PATCH targets a STORE-type org_node with `name` or `code` in the body. Those fields are owned by the `/stores` endpoints (architecture.md § A.5). Reparent (`parent_id` only) is still allowed on STORE-type targets. |
| 422 | (Pydantic) | Empty body, unknown field, attempt to set `node_type`. |

### 5. Behaviour notes

- Edit Node atomicity. Rename, code change, and reparent can all happen in a single PATCH save. The backend applies all changes atomically within one transaction.
- Subtree re-pathing. When a node is moved (parent change), the node and every descendant under it have their org-tree paths rewritten in one SQL statement using ltree's `subpath` and `||` operators.
- Role assignments. Role assignments anchored at the moved node or its descendants are unaffected; the structural reference is by stable `id`, not `path`. The org-tree write surface and the RBAC surface are decoupled.
- Tenant-root protection. The tenant root org_node can be renamed (and its code changed) but not reparented. Attempting to set `parent_id` on a `TENANT`-type node returns 422 `TENANT_ROOT_NOT_REPARENTABLE`. The DDL `ck_org_nodes_root_parent_consistency` is the structural backstop.
- **STORE-type target field-allowlist (Step 6.21.2).** PATCH on a STORE-type target rejects `name` and `code` with 422 `ORG_NODE_FIELD_NOT_ALLOWED_FOR_TYPE`. The reverse direction (architecture.md § A.5 "Field ownership") makes those fields exclusive to PATCH `/api/v1/stores/{store_id}`. Reparent (body containing only `parent_id`) IS allowed; the parent ownership is dual-endpoint per § A.5.

### 6. Example calls

Rename:

```sh
curl -X PATCH "https://admin.ithina.com/api/v1/tenants/${TENANT}/org-tree/${NODE}" \
  -H "Authorization: Bearer ${JWT}" \
  -H "Content-Type: application/json" \
  -d '{"name": "Updated Region Name"}'
```

Reparent (move subtree):

```sh
curl -X PATCH "https://admin.ithina.com/api/v1/tenants/${TENANT}/org-tree/${NODE}" \
  -H "Authorization: Bearer ${JWT}" \
  -H "Content-Type: application/json" \
  -d '{"parent_id": "019e3170-..."}'
```

### 7. Sample integration code

```ts
type EditNodeBody = {
  name?: string;
  code?: string;
  parent_id?: string;
};

async function editOrgNode(tenantId: string, nodeId: string, body: EditNodeBody) {
  if (!body.name && !body.code && !body.parent_id) {
    throw new Error("Provide at least one of: name, code, parent_id.");
  }
  const resp = await fetch(`/api/v1/tenants/${tenantId}/org-tree/${nodeId}`, {
    method: "PATCH",
    headers: {
      "Authorization": `Bearer ${jwt}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
  if (!resp.ok) throw new Error(`${resp.status}: ${await resp.text()}`);
  return resp.json();
}
```

### 8. Implementation reference

- Router: `src/admin_backend/routers/v1/org_tree.py` (`edit_org_node`)
- Repo: `src/admin_backend/repositories/org_nodes.py` (`edit_node`, `_select_for_update_node`, subtree-repath SQL)
- Schemas: `src/admin_backend/schemas/org_node.py` (`OrgNodePatchRequest`)
- Errors: `src/admin_backend/errors.py` (`CycleDetectedError`, `TenantRootNotReparentableError`, plus shared `*NotFoundError` family)
- Tests: `tests/integration/test_org_tree_writes_router.py` (E1-E12, PA2)

---

## Changelog

| Version | Date | Change |
|---|---|---|
| v1.0 | 2026-05-04 | Initial. E2 + E3 ship in Step 5.3. |
| v1.1 | 2026-05-16 | Step 6.13: POST + PATCH write endpoints (Add Node, Edit Node). |
