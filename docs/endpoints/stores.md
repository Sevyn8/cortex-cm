# Stores endpoints

Canonical endpoint documentation for the stores resource. Two GET endpoints under `/api/v1/stores`. Format follows CLAUDE.md "Per-endpoint documentation" — eight fixed sections per endpoint. Mirrors `tenants.md`'s structure; multi-user-type with RLS-driven persona projection (the same pattern as `tenant-users.md`).

> **Step 6.21.2 contract change.** POST `/api/v1/stores` is now the atomic-pair entry point: the server creates the `stores` row AND the paired STORE-type `org_nodes` row in one transaction. Request body field `org_node_id` is REMOVED; new REQUIRED field `parent_org_node_id` names the parent in the org tree. PATCH cascades `name`, `store_code`, and `parent_org_node_id` to the paired org_node. set-status cascades store status to the paired org_node's status + `archived_*` triplet (CLOSED -> ARCHIVED). See `docs/architecture.md` § A.5 for the full transaction shape.

| Endpoint | Description | Calling user types |
|---|---|---|
| `GET /api/v1/stores` | List stores with filters, search, sort, and pagination | PLATFORM (sees all), TENANT (sees own only) |
| `GET /api/v1/stores/{store_id}` | Single store detail (17 fields + `tenant_name`) | PLATFORM (sees all), TENANT (own only; other -> 404 per RLS) |

Cross-cutting:

- **Auth** — `Authorization: Bearer <jwt>` required; missing or invalid -> 401.
- **Gate** — `ADMIN.STORES.VIEW.TENANT` on both endpoints. SUPER_ADMIN + PLATFORM_ADMIN pass via the `.GLOBAL`→`.TENANT` scope cascade; TENANT OWNER passes via the direct `.TENANT` grant (Step 6.17.1 seed update). Callers without a satisfying grant receive 403 `PERMISSION_DENIED`.
- **RLS** — `core.stores` is tenant-scoped via `stores_tenant_isolation` (D-29 OR-branch). PLATFORM sees fleet-wide; TENANT sees own-tenant only. Cross-tenant probes by TENANT JWTs surface as 404 `STORE_NOT_FOUND`, not 403 (RLS-as-404 per D-17).
- **Response envelope** — list shape is `{items, pagination}` (D-30); single-object endpoints return the object directly.
- **Field semantics** — append-only per D-31. Once a field's meaning ships, it stays. New variants get new field names.
- **Hidden fields** — 6 audit-actor columns (`created_by_user_id`/`_type`, `updated_by_user_id`/`_type`, `closed_by_user_id`/`_type`) are intentionally absent from response bodies. Pattern (b) per D-13; lineage is internal.
- **NUMERIC fields** — `latitude` and `longitude` (stored as `NUMERIC(9, 6)`) serialise to JSON as strings to preserve precision in JS clients (D-28 / Q11). `model_dump()` (Python mode) still returns `Decimal`.
- **`tenant_name`** — surfaced on both list items and detail via a LEFT JOIN to `core.tenants` (locked decision 2). Not a correlated subquery: `tenant_name` is a sibling-table label, not an aggregate.
- **`org_node_id`** — exposed as a bare UUID on detail (locked decision 8). No JOIN to `org_nodes.name`; the frontend fetches the name via `/api/v1/tenants/{tenant_id}/org-tree` if needed.
- **Error envelope** — `{code, message, details, request_id}` on all server-generated errors. `details` is `null` in v0.
- **`X-Request-Id`** — set on every response by the audit middleware; same UUID appears in the per-request log line.

---

## `GET /api/v1/stores`

List stores visible to the calling user, filtered, paginated.

### 1. Endpoint summary

- **Method:** `GET`
- **Path:** `/api/v1/stores`
- **Description:** Returns visible stores paginated. Visibility scoped by RLS per session GUCs.
- **Who can call:** any authenticated user holding `ADMIN.STORES.VIEW.TENANT` (direct or via the GLOBAL cascade). PLATFORM sees all rows; TENANT sees own tenant only.

### 2. Request

**Headers:**

| Header | Required | Notes |
|---|---|---|
| `Authorization` | Yes | `Bearer <jwt>` |
| `Accept` | No | Defaults to `application/json` |

**Path parameters:** none.

**Query parameters:**

| Param | Type | Default | Validation |
|---|---|---|---|
| `tenant_id` | UUID | (none) | Optional. Useful for PLATFORM callers scoping a list view; TENANT callers see only their own tenant regardless (RLS handles it). Non-UUID -> 422. |
| `status` | string | (none) | One of `store_status_enum` values: `OPENING`, `ACTIVE`, `INACTIVE`, `CLOSED`. Other values -> 422. |
| `country` | string | (none) | Exact-match filter on country (case-sensitive). |
| `search` | string | (none) | Trimmed; if length 0 after trim, treated as no filter. Case-insensitive substring (ILIKE) match across `name` and `store_code`. Address is excluded. |
| `sort` | string | `tenant_name_asc` | Field-based: `name_asc`, `name_desc`, `created_at_asc`, `created_at_desc`, `status_asc`, `country_asc`. Cross-table: `tenant_name_asc` (default), `tenant_name_desc`; both apply a stable secondary sort by `stores.name ASC` for deterministic pagination within a tenant. Unknown values -> 400 `INVALID_SORT_KEY`. |
| `offset` | int | `0` | `>= 0` |
| `limit` | int | `50` | `>= 1`, `<= 100`. Above the cap -> 422. |

**Request body:** none.

### 3. Response 200

```json
{
  "items": [
    {
      "id": "019e378f-126c-70c6-bfef-7a6930b67192",
      "tenant_id": "019e378f-120b-71bc-b5a4-a3bfca713156",
      "tenant_name": "Buc-ee's",
      "name": "Buc-ee's #1 — Pearland",
      "store_code": "BUC-0001",
      "country": "United States",
      "status": "ACTIVE",
      "created_at": "2026-04-19T15:00:00+00:00"
    }
  ],
  "pagination": {
    "total": 25,
    "offset": 0,
    "limit": 50
  }
}
```

**Field reference:**

| Field | Type | Nullable | Notes |
|---|---|---|---|
| `id` | UUID string | No | Store identifier |
| `tenant_id` | UUID string | No | Owning tenant's id |
| `tenant_name` | string | No | Resolved via LEFT JOIN to `core.tenants` |
| `name` | string | No | Display name |
| `store_code` | string | Yes | Tenant-internal short code (e.g., `BUC-0001`) |
| `country` | string | No | Free-form name |
| `status` | enum string | No | One of `store_status_enum` values |
| `created_at` | ISO 8601 with offset | No | When the store row was inserted |

**Pagination block:**

| Field | Type | Notes |
|---|---|---|
| `total` | int | RLS-filtered total — what the caller can see, not the platform total |
| `offset` | int | Echo of request `offset` |
| `limit` | int | Echo of request `limit` |

The list response intentionally **omits** `org_node_id`, `address`, `latitude`, `longitude`, `currency`, `tax_treatment`, `timezone`, `updated_at`, `closed_at`. All available on detail.

### 4. Response codes

| Code | When | Body |
|---|---|---|
| 200 | Happy path | `{items, pagination}` as above |
| 400 | Unknown `sort` value | `{"code": "INVALID_SORT_KEY", "message": "Invalid sort key", "details": null, "request_id": "..."}` |
| 401 | Missing / invalid JWT | `{"code": "AUTH_MISSING"\|"AUTH_INVALID", ...}` |
| 403 | Caller lacks `ADMIN.STORES.VIEW.TENANT` (direct or via cascade) | `{"code": "PERMISSION_DENIED", ...}` |
| 422 | `status`, `tenant_id`, `offset`, or `limit` fails Pydantic validation | FastAPI validation envelope |

### 5. Behaviour notes

- **RLS scope.** PLATFORM session sees all rows via D-29 OR-branch. TENANT session sees only the rows matching `app.tenant_id` (set from the JWT). The `?tenant_id=` query param is functionally redundant for TENANT callers (RLS already scopes); a non-matching value just intersects to empty rather than disclosing other-tenant rows.
- **Sort.** Default `tenant_name_asc` groups stores by tenant, then sorts alphabetically within a tenant. `tenant_name_asc` / `tenant_name_desc` carry a stable secondary sort by `stores.name ASC` so two stores in the same tenant page deterministically. All other keys end with a final tie-breaker on `stores.id ASC`.
- **Pagination.** `total` reflects the filter set under RLS (it's not the platform total). `offset` and `limit` apply after filtering and sorting.
- **Search.** Trimmed; ILIKE `%search%` across `name` and `store_code`. Address is deliberately excluded (locked decision 4).

### 6. Example calls

```bash
# All stores, PLATFORM view
curl -H "Authorization: Bearer $PLATFORM_JWT" \
  "https://admin-backend.example.com/api/v1/stores"

# Scope to one tenant, sort by store name, ACTIVE only
curl -H "Authorization: Bearer $PLATFORM_JWT" \
  "https://admin-backend.example.com/api/v1/stores?tenant_id=019e378f-120b-71bc-b5a4-a3bfca713156&status=ACTIVE&sort=name_asc"

# Search by code prefix
curl -H "Authorization: Bearer $PLATFORM_JWT" \
  "https://admin-backend.example.com/api/v1/stores?search=BUC-"

# TENANT view: returns own-tenant stores regardless of tenant_id arg
curl -H "Authorization: Bearer $TENANT_JWT" \
  "https://admin-backend.example.com/api/v1/stores"
```

### 7. Sample integration code

```typescript
type StoreListItem = {
  id: string;
  tenant_id: string;
  tenant_name: string;
  name: string;
  store_code: string | null;
  country: string;
  status: "OPENING" | "ACTIVE" | "INACTIVE" | "CLOSED";
  created_at: string;
};

type StoreListResponse = {
  items: StoreListItem[];
  pagination: { total: number; offset: number; limit: number };
};

async function listStores(
  jwt: string,
  opts: {
    tenantId?: string;
    status?: string;
    search?: string;
    sort?: string;
    offset?: number;
    limit?: number;
  } = {},
): Promise<StoreListResponse> {
  const params = new URLSearchParams();
  if (opts.tenantId) params.set("tenant_id", opts.tenantId);
  if (opts.status) params.set("status", opts.status);
  if (opts.search) params.set("search", opts.search);
  if (opts.sort) params.set("sort", opts.sort);
  if (opts.offset != null) params.set("offset", String(opts.offset));
  if (opts.limit != null) params.set("limit", String(opts.limit));

  const resp = await fetch(`/api/v1/stores?${params}`, {
    headers: { Authorization: `Bearer ${jwt}` },
  });
  if (!resp.ok) throw new Error(`stores list failed: ${resp.status}`);
  return resp.json();
}
```

### 8. Implementation reference

- Router: `src/admin_backend/routers/v1/stores.py::list_stores`
- Repo: `src/admin_backend/repositories/stores.py::StoresRepo.list`
- ORM model: `src/admin_backend/models/store.py::Store`
- Response schema: `src/admin_backend/schemas/store.py::StoreListResponse`
- Sort vocabulary: `src/admin_backend/repositories/stores.py::SORT_MAP`
- Tests: `tests/integration/test_stores_repo.py` (R1-R10), `tests/integration/test_stores_router.py` (L1-L10)

---

## `GET /api/v1/stores/{store_id}`

Single store detail (17 fields including `tenant_name`).

### 1. Endpoint summary

- **Method:** `GET`
- **Path:** `/api/v1/stores/{store_id}`
- **Description:** Returns full store detail. RLS-scoped per session.
- **Who can call:** any authenticated user holding `ADMIN.STORES.VIEW.TENANT` (direct or via cascade). Same gate as the list endpoint, plus a per-store anchor dep that runs ahead of the gate body.

### 2. Request

**Headers:**

| Header | Required | Notes |
|---|---|---|
| `Authorization` | Yes | `Bearer <jwt>` |

**Path parameters:**

| Param | Type | Validation |
|---|---|---|
| `store_id` | UUID | Non-UUID -> 422. |

**Query / body:** none.

### 3. Response 200

```json
{
  "id": "019e378f-126c-70c6-bfef-7a6930b67192",
  "tenant_id": "019e378f-120b-71bc-b5a4-a3bfca713156",
  "tenant_name": "Buc-ee's",
  "org_node_id": "019e378f-13a4-7b91-b58c-2b7c19ee5a8c",
  "name": "Buc-ee's #1 — Pearland",
  "store_code": "BUC-0001",
  "country": "United States",
  "timezone": "America/Chicago",
  "address": "200 Highway 6 W, Alvin, TX",
  "latitude": "29.378900",
  "longitude": "-95.272300",
  "currency": "USD",
  "tax_treatment": "EXCLUSIVE",
  "status": "ACTIVE",
  "created_at": "2026-04-19T15:00:00+00:00",
  "updated_at": "2026-04-19T15:00:00+00:00",
  "closed_at": null
}
```

**Field reference:**

| Field | Type | Nullable | Notes |
|---|---|---|---|
| `id` | UUID string | No | Store identifier |
| `tenant_id` | UUID string | No | Owning tenant's id |
| `tenant_name` | string | No | Resolved via LEFT JOIN to `core.tenants` |
| `org_node_id` | UUID string | Yes | Anchor in the org tree (locked decision 8 — bare UUID, name resolved via `/org-tree`) |
| `name` | string | No | Display name |
| `store_code` | string | Yes | Tenant-internal short code |
| `country` | string | No | Free-form name |
| `timezone` | string | No | IANA name (e.g., `America/Chicago`) |
| `address` | string | Yes | Free-form |
| `latitude` | decimal string | Yes | `NUMERIC(9, 6)`; serialised as string to preserve precision |
| `longitude` | decimal string | Yes | `NUMERIC(9, 6)`; serialised as string |
| `currency` | string (3 chars) | No | ISO 4217 (uppercase) |
| `tax_treatment` | enum string | No | `EXCLUSIVE` or `INCLUSIVE` |
| `status` | enum string | No | One of `store_status_enum` values |
| `created_at` | ISO 8601 with offset | No | Insert timestamp |
| `updated_at` | ISO 8601 with offset | No | Most recent update |
| `closed_at` | ISO 8601 with offset | Yes | Set when `status = CLOSED` |

### 4. Response codes

| Code | When | Body |
|---|---|---|
| 200 | Happy path | Full detail shape above |
| 401 | Missing / invalid JWT | `{"code": "AUTH_MISSING"\|"AUTH_INVALID", ...}` |
| 403 | Caller lacks `ADMIN.STORES.VIEW.TENANT` | `{"code": "PERMISSION_DENIED", ...}` |
| 404 | Store id missing OR RLS-filtered (cross-tenant probe by TENANT JWT) | `{"code": "STORE_NOT_FOUND", ...}` |
| 422 | `store_id` not a UUID | FastAPI validation envelope |

### 5. Behaviour notes

- **RLS-as-404 path.** Per D-17 and F-THREADING-4, cross-tenant probes do not 403. The anchor dependency `get_store_anchor` is resolved BEFORE the gate body; if the store is RLS-invisible to the caller, the anchor dep raises `StoreNotFoundError` (404) rather than returning a path that would have let the gate proceed. The existence of another tenant's `store_id` is therefore not disclosed.
- **Anchor.** The gate's cascade root is the store's tenant root (the tenant-root `org_node` for `stores.tenant_id`). Per-store anchoring per locked decision 8: `org_node_id` is exposed as a bare UUID; org-node-level scoping is deferred to a future step.
- **Audit-actor columns hidden.** Pattern (b) per D-13. Tests assert the exact response key set.

### 6. Example calls

```bash
# Detail, PLATFORM view
curl -H "Authorization: Bearer $PLATFORM_JWT" \
  "https://admin-backend.example.com/api/v1/stores/019e378f-126c-70c6-bfef-7a6930b67192"

# Cross-tenant probe by a TENANT JWT for another tenant's store -> 404
curl -H "Authorization: Bearer $TENANT_A_JWT" \
  "https://admin-backend.example.com/api/v1/stores/{tenant_b_store_id}"
# 404 STORE_NOT_FOUND
```

### 7. Sample integration code

```typescript
type StoreDetail = {
  id: string;
  tenant_id: string;
  tenant_name: string;
  org_node_id: string | null;
  name: string;
  store_code: string | null;
  country: string;
  timezone: string;
  address: string | null;
  latitude: string | null;  // serialised as decimal string
  longitude: string | null;
  currency: string;
  tax_treatment: "EXCLUSIVE" | "INCLUSIVE";
  status: "OPENING" | "ACTIVE" | "INACTIVE" | "CLOSED";
  created_at: string;
  updated_at: string;
  closed_at: string | null;
};

async function getStore(jwt: string, storeId: string): Promise<StoreDetail> {
  const resp = await fetch(`/api/v1/stores/${storeId}`, {
    headers: { Authorization: `Bearer ${jwt}` },
  });
  if (resp.status === 404) throw new Error("Store not found");
  if (!resp.ok) throw new Error(`stores detail failed: ${resp.status}`);
  return resp.json();
}
```

### 8. Implementation reference

- Router: `src/admin_backend/routers/v1/stores.py::get_store`
- Repo: `src/admin_backend/repositories/stores.py::StoresRepo.get_by_id`
- Anchor dep: `src/admin_backend/auth/anchor_deps.py::get_store_anchor`
- Error class: `src/admin_backend/errors.py::StoreNotFoundError`
- Response schema: `src/admin_backend/schemas/store.py::StoreDetail`

---

## `POST /api/v1/stores`

Provision a new store. Multi-audience (Step 6.17.3).

### 1. Endpoint summary

- **Method:** `POST`
- **Path:** `/api/v1/stores`
- **Description:** Create a store under a tenant. PLATFORM callers create for any tenant via GLOBAL cascade; TENANT OWNER creates for own tenant via the direct `.TENANT` grant. `tenant_id` in the body is verified against the caller's RLS-bound session — cross-tenant ids by TENANT callers surface as 404 `TENANT_NOT_FOUND`.
- **Who can call:** any authenticated user holding `ADMIN.STORES.CONFIGURE.TENANT` (direct or via GLOBAL cascade). Seeded grant on OWNER, SUPER_ADMIN, PLATFORM_ADMIN per the Step 6.17.1 seed.

### 2. Request

**Headers:**

| Header | Required | Notes |
|---|---|---|
| `Authorization` | Yes | `Bearer <jwt>` |
| `Content-Type` | Yes | `application/json` |

**Body (extra fields rejected — `extra="forbid"`):**

| Field | Type | Required | Notes |
|---|---|---|---|
| `tenant_id` | UUID string | Yes | Must be visible to the caller's session (RLS-as-404 otherwise). |
| `name` | string | Yes | 1 — 200 chars (`ck_stores_name_length`). |
| `country` | string | Yes | 2 — 100 chars (`ck_stores_country_format`). |
| `timezone` | string | Yes | 1 — 50 chars; IANA name. |
| `currency` | string | Yes | `^[A-Z]{3}$` (`ck_stores_currency_format`). |
| `store_code` | string | Yes | 1 — 50 chars. Required at the schema layer (LD2 — FN-AB tracks the future NOT NULL migration). |
| `tax_treatment` | enum | Yes | `EXCLUSIVE` or `INCLUSIVE`. |
| `parent_org_node_id` | UUID string | **Yes** (Step 6.21.2) | Parent in the org tree under which the server creates the paired STORE-type org_node. Must be a non-STORE node in the same tenant. Use `tenant_root_id` from GET `/org-tree` to anchor directly under the tenant root. Replaces the pre-6.21.2 `org_node_id` field. |
| `address` | string | No | Free-form. |
| `latitude` | decimal string | No | -90 — 90. Accepts string or number; stored as `NUMERIC(9, 6)`. |
| `longitude` | decimal string | No | -180 — 180. Same shape as latitude. |

**Server-managed (rejected on the wire):**

`id`, `status` (DDL default fires — see Behaviour notes), `created_at`, `updated_at`, `closed_at`, all `*_by_user_id` / `*_by_user_type` audit-actor columns.

### 3. Response 201

Returns the full `StoreDetail` shape (same 17 fields as the GET detail endpoint above). The freshly-INSERTed row is materialised via a JOIN to `core.tenants` for the `tenant_name` label.

```json
{
  "id": "019e3a00-0000-7000-8000-000000000001",
  "tenant_id": "019e378f-120b-71bc-b5a4-a3bfca713156",
  "tenant_name": "Buc-ee's",
  "org_node_id": "019e3a00-0000-7000-9000-000000000099",
  "name": "Buc-ee's #47 — Houston",
  "store_code": "BUC-0047",
  "country": "United States",
  "timezone": "America/Chicago",
  "address": null,
  "latitude": null,
  "longitude": null,
  "currency": "USD",
  "tax_treatment": "EXCLUSIVE",
  "status": "ACTIVE",
  "created_at": "2026-05-18T10:00:00+00:00",
  "updated_at": "2026-05-18T10:00:00+00:00",
  "closed_at": null
}
```

### 4. Response codes

| Code | When | Body |
|---|---|---|
| 201 | Happy path | Full `StoreDetail` |
| 401 | Missing / invalid JWT | `{"code": "AUTH_MISSING"\|"AUTH_INVALID", ...}` |
| 403 | Caller lacks `ADMIN.STORES.CONFIGURE.TENANT` (direct or cascade) | `{"code": "PERMISSION_DENIED", ...}` |
| 404 | `tenant_id` in body missing or RLS-invisible to caller | `{"code": "TENANT_NOT_FOUND", ...}` |
| 404 | `parent_org_node_id` missing, in a different tenant, or RLS-invisible (Step 6.21.2) | `{"code": "PARENT_NODE_NOT_FOUND", ...}` |
| 409 | `store_code` collides with another store in the same tenant (case-insensitive) | `{"code": "DUPLICATE_STORE_CODE", ...}` |
| 409 | `store_code` collides (case-insensitive) with another org_node code in the tenant — the cascade `add_node` hits the broader `uq_org_nodes_tenant_code_lower` index (Step 6.21.2) | `{"code": "DUPLICATE_ORG_NODE_CODE", ...}` |
| 422 | `parent_org_node_id` is STORE-type (Step 6.21.2) | `{"code": "INVALID_PARENT_NODE_TYPE", ...}` |
| 422 | Body validation failure: missing required field, unexpected field (`status`, `id`, the deprecated `org_node_id`, etc.), out-of-range numeric, malformed currency, etc. | FastAPI validation envelope |

### 5. Behaviour notes

- **Multi-audience (LD1).** Diverges from the tenants POST audience kwarg. Both PLATFORM (GLOBAL cascade) and TENANT OWNER (`.TENANT` direct grant) reach this handler.
- **`tenant_id` verified via RLS-as-404.** Pre-check probes `core.tenants` under the caller's session before the INSERT — a TENANT JWT submitting another tenant's id finds the row invisible and surfaces as 404 instead of letting the RLS WITH CHECK predicate produce a 500.
- **`status` server-forced via DDL default (LD8).** The schema rejects `status` in body; the INSERT omits the column. The current DDL default is `ACTIVE`; the product intent (per the lifecycle enum ordering) is `OPENING`. Deferred to a future migration; no app-code change required when the default flips.
- **`store_code` uniqueness is case-insensitive per tenant.** Pre-check uses `lower()`; aligns with the DDL partial unique index `uq_stores_tenant_store_code_lower`. The DB index closes the race window between pre-check and INSERT.
- **Atomic paired write (Step 6.21.2).** The handler creates the paired STORE-type `org_nodes` row + the `stores` row in one transaction. ``parent_org_node_id`` is pre-checked under the caller's session (404 ``PARENT_NODE_NOT_FOUND`` on missing/cross-tenant; 422 ``INVALID_PARENT_NODE_TYPE`` when the parent is itself STORE-type). The response ``org_node_id`` field carries the server-allocated UUID of the new paired row. See `docs/architecture.md` § A.5.
- **Audit-actor pair (Pattern (b) per D-13).** Both `created_by_*` and `updated_by_*` pairs populate from `auth.user_id` + the audience-bridged `ActorUserType`.

### 6. Example calls

```bash
# PLATFORM creates for any tenant.
curl -X POST \
  -H "Authorization: Bearer $PLATFORM_JWT" \
  -H "Content-Type: application/json" \
  -d '{
        "tenant_id": "019e378f-120b-71bc-b5a4-a3bfca713156",
        "parent_org_node_id": "019e378f-1234-7000-aaaa-000000000001",
        "name": "Buc-eea #47",
        "country": "United States",
        "timezone": "America/Chicago",
        "currency": "USD",
        "store_code": "BUC-0047",
        "tax_treatment": "EXCLUSIVE"
      }' \
  "https://admin-backend.example.com/api/v1/stores"

# TENANT OWNER creates for own tenant.
curl -X POST \
  -H "Authorization: Bearer $OWNER_JWT" \
  -H "Content-Type: application/json" \
  -d '{...}' \
  "https://admin-backend.example.com/api/v1/stores"

# TENANT OWNER with tenant_id from another tenant -> 404 TENANT_NOT_FOUND.
```

### 7. Sample integration code

```typescript
type StoreCreateRequest = {
  tenant_id: string;
  name: string;
  country: string;
  timezone: string;
  currency: string;
  store_code: string;
  tax_treatment: "EXCLUSIVE" | "INCLUSIVE";
  org_node_id?: string | null;
  address?: string | null;
  latitude?: string | number | null;
  longitude?: string | number | null;
};

async function createStore(
  jwt: string,
  body: StoreCreateRequest,
): Promise<StoreDetail> {
  const resp = await fetch("/api/v1/stores", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${jwt}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
  if (resp.status === 409) {
    const err = await resp.json();
    throw new Error(`stores create conflict: ${err.code}`);
  }
  if (!resp.ok) throw new Error(`stores create failed: ${resp.status}`);
  return resp.json();
}
```

### 8. Implementation reference

- Router: `src/admin_backend/routers/v1/stores.py::create_store`
- Repo: `src/admin_backend/repositories/stores.py::StoresRepo.create`
- Errors: `src/admin_backend/errors.py::DuplicateStoreCodeError`, `OrgNodeNotForStoreError`, `TenantNotFoundError`
- Request schema: `src/admin_backend/schemas/store.py::StoreCreateRequest`
- Response schema: `src/admin_backend/schemas/store.py::StoreDetail`

---

## `PATCH /api/v1/stores/{store_id}`

Partial update of a store (Step 6.17.3). Multi-audience.

### 1. Endpoint summary

- **Method:** `PATCH`
- **Path:** `/api/v1/stores/{store_id}`
- **Description:** Update one or more mutable fields on a store. Status, tenant_id, and org_node_id are immutable on this path.
- **Who can call:** same as POST — any authenticated user holding `ADMIN.STORES.CONFIGURE.TENANT` (direct or via GLOBAL cascade), plus the per-store anchor dependency that runs ahead of the gate body.

### 2. Request

**Headers:**

| Header | Required | Notes |
|---|---|---|
| `Authorization` | Yes | `Bearer <jwt>` |
| `Content-Type` | Yes | `application/json` |

**Path parameters:**

| Param | Type | Validation |
|---|---|---|
| `store_id` | UUID | Non-UUID -> 422. |

**Body (all fields optional, but the body must contain at least one):**

| Field | Type | Notes |
|---|---|---|
| `name` | string | 1 — 200 chars. |
| `store_code` | string | 1 — 50 chars. Same case-insensitive uniqueness as POST; pre-check excludes self for rename-to-same. |
| `country` | string | 2 — 100 chars. |
| `timezone` | string | 1 — 50 chars. |
| `currency` | string | `^[A-Z]{3}$`. |
| `tax_treatment` | enum | `EXCLUSIVE` or `INCLUSIVE`. |
| `address` | string | Free-form. |
| `latitude` | decimal string | -90 — 90. |
| `longitude` | decimal string | -180 — 180. |
| `parent_org_node_id` | UUID string | **Step 6.21.2.** If set, reparents the paired STORE-type org_node under this parent. Must be a non-STORE node in the same tenant. The store's own `org_node_id` (its slot) is unchanged; only the slot's `parent_id` moves. |

**Rejected at schema layer via `extra="forbid"`:**

`status` (Step 6.17.4 territory — `/change_status`), `tenant_id`, `org_node_id` (the store's link to its paired org_node is immutable; reparenting uses `parent_org_node_id`), `id`, audit-actor columns, `closed_*`.

**Step 6.21.2 cascade.** When `name`, `store_code`, or `parent_org_node_id` is in the body, the change propagates to the paired STORE-type `org_nodes` row atomically inside one transaction. `org_node.name` mirrors `store.name`; `org_node.code` mirrors `store.store_code`; `org_node.parent_id` mirrors `parent_org_node_id`. Audit-actor pair on both rows carries the same actor + timestamp.

### 3. Response 200

Full `StoreDetail` shape (same 17 fields as GET detail).

### 4. Response codes

| Code | When | Body |
|---|---|---|
| 200 | Happy path; rename to same value is a no-op success | Full `StoreDetail` |
| 401 | Missing / invalid JWT | `{"code": "AUTH_MISSING"\|"AUTH_INVALID", ...}` |
| 403 | Caller lacks `ADMIN.STORES.CONFIGURE.TENANT` | `{"code": "PERMISSION_DENIED", ...}` |
| 404 | Store id missing OR RLS-filtered | `{"code": "STORE_NOT_FOUND", ...}` (anchor dep fires first on cross-tenant probes) |
| 404 | `parent_org_node_id` set to a missing or cross-tenant value (Step 6.21.2) | `{"code": "PARENT_NODE_NOT_FOUND", ...}` |
| 409 | Rename `store_code` to a value held by another store same tenant (case-insensitive) | `{"code": "DUPLICATE_STORE_CODE", ...}` |
| 409 | Rename `store_code` to a value held by a non-store org_node code in the tenant (cascade collision) | `{"code": "DUPLICATE_ORG_NODE_CODE", ...}` |
| 422 | Empty body | `{"code": "EMPTY_PATCH", ...}` |
| 422 | `parent_org_node_id` set to a STORE-type node (Step 6.21.2) | `{"code": "INVALID_PARENT_NODE_TYPE", ...}` |
| 422 | Unexpected field (`status`, `org_node_id`, `tenant_id`, etc.) or invalid value | FastAPI validation envelope |

### 5. Behaviour notes

- **Empty body returns 422 `EMPTY_PATCH`** (LD4). Handler calls `body.model_dump(exclude_unset=True)`; an empty dict raises before the repo runs.
- **Non-empty same-as-current is a 200 no-op.** `updated_at` is refreshed by the BEFORE-UPDATE trigger `tg_stores_set_updated_at` (which calls `set_updated_at_timestamp` -> `NOW()`). Inside a single transaction, `NOW()` is TX-start; the timestamp does not advance for same-TX read-then-write but advances normally across transactions (the real-world case).
- **`store_code` rename-to-self.** The duplicate pre-check excludes the current row's id, so PATCH that keeps `store_code` unchanged is a clean 200.
- **`org_node_id` is immutable on PATCH** (LD3). Defer until a product workflow for store ↔ org_node linkage lands. Future loosening is additive (backward-compatible per D-31).
- **Anchor dep precedence.** `get_store_anchor` fires before the gate body. Cross-tenant probes by TENANT callers surface as 404 `STORE_NOT_FOUND` from the anchor dep path, not 403.

### 6. Example calls

```bash
# PLATFORM rename
curl -X PATCH \
  -H "Authorization: Bearer $PLATFORM_JWT" \
  -H "Content-Type: application/json" \
  -d '{"name": "Buc-eea #47 (Houston Westpark)"}' \
  "https://admin-backend.example.com/api/v1/stores/019e3a00-..."

# TENANT OWNER updating own-tenant store's coordinates
curl -X PATCH \
  -H "Authorization: Bearer $OWNER_JWT" \
  -H "Content-Type: application/json" \
  -d '{"latitude": "29.5", "longitude": "-95.2"}' \
  "https://admin-backend.example.com/api/v1/stores/019e3a00-..."

# Status flip attempted via PATCH -> 422 (use /change_status when shipped)
```

### 7. Sample integration code

```typescript
type StorePatchRequest = Partial<{
  name: string;
  store_code: string;
  country: string;
  timezone: string;
  currency: string;
  tax_treatment: "EXCLUSIVE" | "INCLUSIVE";
  address: string | null;
  latitude: string | number | null;
  longitude: string | number | null;
}>;

async function patchStore(
  jwt: string,
  storeId: string,
  body: StorePatchRequest,
): Promise<StoreDetail> {
  if (Object.keys(body).length === 0) {
    throw new Error("PATCH body must include at least one field");
  }
  const resp = await fetch(`/api/v1/stores/${storeId}`, {
    method: "PATCH",
    headers: {
      Authorization: `Bearer ${jwt}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
  if (resp.status === 404) throw new Error("Store not found");
  if (!resp.ok) throw new Error(`stores patch failed: ${resp.status}`);
  return resp.json();
}
```

### 8. Implementation reference

- Router: `src/admin_backend/routers/v1/stores.py::patch_store`
- Repo: `src/admin_backend/repositories/stores.py::StoresRepo.update`
- Anchor dep: `src/admin_backend/auth/anchor_deps.py::get_store_anchor`
- Errors: `src/admin_backend/errors.py::DuplicateStoreCodeError`, `StoreNotFoundError`, `EmptyPatchError`
- Request schema: `src/admin_backend/schemas/store.py::StorePatchRequest`
- Response schema: `src/admin_backend/schemas/store.py::StoreDetail`
- Tests: `tests/integration/test_stores_repo.py` (R11-R13), `tests/integration/test_stores_router.py` (D1-D4, MG1)

---

## `POST /api/v1/stores/{store_id}/set-status`

State-transition endpoint (Step 6.17.4). Multi-audience.

### 1. Endpoint summary

- **Method:** `POST`
- **Path:** `/api/v1/stores/{store_id}/set-status`
- **Description:** Transition a store's lifecycle status. 9-cell liberal matrix (all transitions allowed except `*->OPENING`). Atomic: status flip + audit pair (and `closed_*` triplet when relevant) land in one UPDATE.
- **Who can call:** any authenticated user holding `ADMIN.STORES.CONFIGURE.TENANT` (direct or via GLOBAL cascade) — same gate as PATCH `/stores/{store_id}`. Seeded grant on OWNER, SUPER_ADMIN, PLATFORM_ADMIN.

### 2. Request

**Headers:**

| Header | Required | Notes |
|---|---|---|
| `Authorization` | Yes | `Bearer <jwt>` |
| `Content-Type` | Yes | `application/json` |

**Path parameters:**

| Param | Type | Validation |
|---|---|---|
| `store_id` | UUID | Non-UUID -> 422. |

**Body (extra fields rejected — `extra="forbid"`):**

| Field | Type | Required | Notes |
|---|---|---|---|
| `target_status` | enum (`StoreStatus`) | Yes | One of `OPENING`, `ACTIVE`, `INACTIVE`, `CLOSED`. Invalid values -> 422. |
| `reason` | string | No | Forward-compatible with Step 6.2 audit_log integration. Consumed by Pydantic; silently dropped at the repo layer until audit_log ships. No observable side effect in v0; when audit_log lands, the handler gains an `audit_log_repo.write(...reason=...)` call with no wire change. |

### 3. Transition matrix

9-cell liberal matrix per locked decision 1 (Step 6.17.4). Same-state is REJECTED (target state is not in its own allowed-sources set; mirrors tenants `allowed_sources` convention).

```
                  target_status →
              OPENING    ACTIVE    INACTIVE    CLOSED
current ↓
OPENING         -         OK         OK         OK
ACTIVE         REJ         -         OK         OK
INACTIVE       REJ         OK         -         OK
CLOSED         REJ         OK         OK         -
```

`OK` = 200 + full `StoreDetail`. `REJ` = 409 `INVALID_STATE_TRANSITION`. Same-state cells (the four diagonal entries) also REJ.

### 4. Response 200

Returns the full `StoreDetail` shape (same 17 fields as the GET detail endpoint). Three classes of write happen behind the wire:

- **Class 1 (into-CLOSED):** `status` + `closed_at` + `closed_by_user_id` + `closed_by_user_type` + the `updated_*` audit pair all set in one UPDATE.
- **Class 2 (out-of-CLOSED):** `status` flips; `closed_at` + `closed_by_*` triplet **nulled atomically** with the flip. Historical closure metadata is lost on the row per `ck_stores_closed_consistency` (LD2). Step 6.2 audit_log preserves the history when shipped.
- **Class 3 (between non-CLOSED):** `status` flips; `closed_*` columns untouched (they are already NULL by the DDL CHECK invariant).

**Step 6.21.2 cascade.** Every transition also projects to the paired STORE-type org_node's status via the `STORE_STATUS_TO_ORG_NODE_STATUS` map (OPENING/ACTIVE -> ACTIVE; INACTIVE -> INACTIVE; CLOSED -> ARCHIVED). The org_node's `archived_*` triplet is symmetric to the stores `closed_*` triplet: populated on into-ARCHIVED, nulled on out-of-ARCHIVED, untouched on between non-ARCHIVED. All writes share the same actor and transaction. See `docs/architecture.md` § A.5 "Status mapping" and "Closed-state triplets".

```json
{
  "id": "019e378f-126c-70c6-bfef-7a6930b67192",
  "tenant_id": "019e378f-120b-71bc-b5a4-a3bfca713156",
  "tenant_name": "Buc-ee's",
  "org_node_id": null,
  "name": "Buc-eea #47",
  "store_code": "BUC-0047",
  "country": "United States",
  "timezone": "America/Chicago",
  "address": null,
  "latitude": null,
  "longitude": null,
  "currency": "USD",
  "tax_treatment": "EXCLUSIVE",
  "status": "CLOSED",
  "created_at": "2026-04-19T15:00:00+00:00",
  "updated_at": "2026-05-18T10:00:00+00:00",
  "closed_at": "2026-05-18T10:00:00+00:00"
}
```

### 5. Response codes

| Code | When | Body |
|---|---|---|
| 200 | Allowed transition; full `StoreDetail` returned | (shape above) |
| 401 | Missing / invalid JWT | `{"code": "AUTH_MISSING"\|"AUTH_INVALID", ...}` |
| 403 | Caller lacks `ADMIN.STORES.CONFIGURE.TENANT` | `{"code": "PERMISSION_DENIED", ...}` |
| 404 | Store id missing OR RLS-filtered (cross-tenant probe by TENANT JWT) | `{"code": "STORE_NOT_FOUND", ...}` |
| 409 | Transition rejected by matrix (`*->OPENING` or same-state) | `{"code": "INVALID_STATE_TRANSITION", "message": "...", "details": null, ...}` |
| 422 | Body validation failure: extra field, invalid `target_status` enum value, missing `target_status` | FastAPI validation envelope |

### 6. Behaviour notes

- **Multi-audience (LD9 — same gate as PATCH).** Both PLATFORM (GLOBAL cascade) and TENANT OWNER (direct `.TENANT` grant) reach this handler.
- **Same-state rejected (LD5).** `ACTIVE -> ACTIVE` (and any other diagonal cell) returns 409, not 200. Matches the tenants `transition` convention (target state excluded from its own allowed-sources set).
- **Closure history on reopen (LD2).** A CLOSED -> ACTIVE / INACTIVE transition NULLS the `closed_*` triplet on the live row to satisfy `ck_stores_closed_consistency`. Historical closure metadata (who closed, when) is lost on the live row in v0. Step 6.2 audit_log will preserve the full transition history when shipped.
- **Response envelope per Q7 lock.** On 409 the response body's `details` field stays `null`. Structured context (`store_id`, `target_status`) reaches `exc.context` for log paths only; the wire does not surface a separate `current_status` / `target_status` body field. (Future evolution per D-31: a future `details` payload could be added without breaking existing consumers; the current contract reserves the field as null.)
- **Public message inherited from `InvalidStateTransitionError`.** The class's `public_message` is tenant-flavored ("Tenant cannot transition to the requested state.") for historical reasons; reused as-is by tenant_users and stores. A future FN-AB tracks generalising this copy.
- **`reason` is forward-compatible.** The schema accepts the field; the repo signature does not. Consumers can include `reason` in requests today without behaviour change. When Step 6.2 audit_log ships, the value will route to `audit_log` with no API change.
- **Anchor dep precedence.** `get_store_anchor` fires before the gate body. Cross-tenant probes by TENANT callers surface as 404 `STORE_NOT_FOUND` from the anchor dep path, not 403.

### 7. Example calls

```bash
# PLATFORM transitioning into CLOSED with a reason.
curl -X POST \
  -H "Authorization: Bearer $PLATFORM_JWT" \
  -H "Content-Type: application/json" \
  -d '{"target_status":"CLOSED","reason":"Lease ended; site demolished."}' \
  "https://admin-backend.example.com/api/v1/stores/019e378f-126c-70c6-bfef-7a6930b67192/set-status"

# OWNER reopening: CLOSED -> ACTIVE. Live closed_* nulled; full
# audit history preserved via Step 6.2's audit_log (forward).
curl -X POST \
  -H "Authorization: Bearer $OWNER_JWT" \
  -H "Content-Type: application/json" \
  -d '{"target_status":"ACTIVE","reason":"Reopened at new location."}' \
  "https://admin-backend.example.com/api/v1/stores/.../set-status"

# Rejected: ACTIVE -> OPENING (no reopen-to-OPENING per LD1).
# Returns 409 INVALID_STATE_TRANSITION.
```

### 8. Sample integration code

```typescript
type StoreSetStatusRequest = {
  target_status: "OPENING" | "ACTIVE" | "INACTIVE" | "CLOSED";
  reason?: string;
};

async function setStoreStatus(
  jwt: string,
  storeId: string,
  body: StoreSetStatusRequest,
): Promise<StoreDetail> {
  const resp = await fetch(`/api/v1/stores/${storeId}/set-status`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${jwt}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
  if (resp.status === 409) {
    const err = await resp.json();
    throw new Error(`Invalid state transition: ${err.message}`);
  }
  if (resp.status === 404) throw new Error("Store not found");
  if (!resp.ok) throw new Error(`set-status failed: ${resp.status}`);
  return resp.json();
}
```

### Implementation reference

- Router: `src/admin_backend/routers/v1/stores.py::set_store_status`
- Repo: `src/admin_backend/repositories/stores.py::StoresRepo.transition` + `TRANSITION_MATRIX`
- Anchor dep: `src/admin_backend/auth/anchor_deps.py::get_store_anchor`
- Errors: `src/admin_backend/errors.py::InvalidStateTransitionError`, `StoreNotFoundError`
- `TransitionResult` enum: `src/admin_backend/repositories/tenants.py::TransitionResult` (imported by stores; same 3-value shape as tenants / tenant_users)
- Request schema: `src/admin_backend/schemas/store.py::StoreSetStatusRequest`
- Response schema: `src/admin_backend/schemas/store.py::StoreDetail`
- Tests: `tests/integration/test_stores_repo_writes.py` (T1-T16), `tests/integration/test_stores_set_status_router.py` (RT1-RT13, MG)
