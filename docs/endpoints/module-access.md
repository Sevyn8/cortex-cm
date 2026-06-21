# Module Access endpoints

Canonical endpoint documentation for the Module Access governance console (Frontend spec — sidebar entry "Module Access" under "ACCESS CONTROL" group). Two GET endpoints under `/api/v1/module-access/`. Format follows CLAUDE.md "Per-endpoint documentation" — eight fixed sections per endpoint. Resource-specific additions: **first instance of the new label-handling convention** (sibling `<field>_label`, server-side resolution via `lookups`), **multi-user-type with RLS-driven persona projection** (continues Step 6.5 pattern), and **synthesized cell grid** for the matrix endpoint.

| Endpoint | Description | Calling user types |
|---|---|---|
| `GET /api/v1/module-access/modules` | E1 — 6 module cards with per-module aggregates | PLATFORM (fleet-wide); TENANT (own-tenant only via RLS) |
| `GET /api/v1/module-access/matrix` | E2 — paginated tenant × module enablement grid | PLATFORM (full grid); TENANT (1-row, own tenant only) |

Cross-cutting:

- **Auth** — `Authorization: Bearer <jwt>` required; missing or invalid -> 401.
- **No PLATFORM-only gate.** Both endpoints accept both user types; visibility is RLS-driven via the session GUCs set by `get_tenant_session`. PLATFORM sees fleet-wide via D-29's OR-clause; TENANT sees own-tenant only via the equality clause.
- **Server-side label resolution (locked at this step).** Every enum-coded field carries a sibling `<field>_label` resolved via LEFT JOIN against `lookups` with `COALESCE(display_name, code)` fallback. Fields covered: `module_label` (E1, E2 cells), `tier_label` and `status_label` (E2 rows). Always present; never null where the source field is non-null. Applies to **new endpoints from Step 6.7 forward**; older endpoints stay bare-enum.
- **Cell synthesis (E2).** The matrix grid is synthesized backend-side: every visible non-TERMINATED tenant gets exactly 6 cells, regardless of how many `tenant_module_access` rows actually exist. Cells render as `ENABLED` only when an ENABLED `tenant_module_access` row matches; absent rows AND rows with `status='DISABLED'` both render as `DISABLED` on the wire.
- **Module ordering — position-aligned and stable.** `lookups.display_order ASC, code ASC` (per Step 6.6's sort-stability decision). `/modules.items[i].module_code` matches `/matrix.items[*].cells[i].module_code` for every i; frontend reconciles by index. Stable across enum vocabulary changes — adding a new module just adds a new lookup row without re-sequencing existing modules.
- **Aggregate denominator.** `/modules.total_active_trial_tenants` is `COUNT(*) FROM tenants WHERE status IN ('ACTIVE', 'TRIAL')` over visible (RLS-filtered) tenants. **Different from `/dashboard/fleet-stats.active_tenants.total`** which uses `status != 'TERMINATED'` — different product question, different count. Documented inline.
- **Matrix row set.** All visible non-TERMINATED tenants (ACTIVE + TRIAL + SUSPENDED + ONBOARDING). TERMINATED tenants are filtered out at the row-set level; they never appear in `/matrix`. Anjali (or any platform user) needs governance visibility into non-currently-transacting tenants like SUSPENDED — TERMINATED is post-lifecycle and not relevant to live governance.
- **Append-only contract per D-31.** Field semantics, once shipped, are frozen. New variants get new field names.
- **Error envelope** — `{code, message, details, request_id}`. `details` is `null` in v0.
- **`X-Request-Id`** on every response (audit middleware).
- **No RBAC enforcement.** Per-permission gating is post-v0. Module Access is read-only in v0; the toggle write endpoint is a forward note (MODULE-ACCESS-WRITE).

---

## `GET /api/v1/module-access/modules`  (E1)

6 module cards with per-module aggregates.

### 1. Endpoint summary

- **Method:** `GET`
- **Path:** `/api/v1/module-access/modules`
- **Description:** Returns all 6 modules with per-module aggregate counts. Backed by `lookups` for the row set and `tenant_module_access` JOIN `tenants` for the enabled-count aggregate. Card ordering anchored on `lookups.display_order` (decoupled from PG enum ordinal per Step 6.6's sort-stability decision).
- **Who can call:** any authenticated user. PLATFORM sees fleet-wide aggregates; TENANT sees own-tenant aggregates (collapsed to 0 or 1 via RLS).

### 2. Request

**Headers:**

| Header | Required | Notes |
|---|---|---|
| `Authorization` | Yes | `Bearer <jwt>` (PLATFORM or TENANT) |
| `Accept` | No | Defaults to `application/json` |

**Path parameters:** none.
**Query parameters:** none.
**Request body:** none.

### 3. Response 200

```json
{
  "items": [
    {
      "module_code": "ROOS",
      "module_label": "ROOS",
      "enabled_count": 4,
      "total_active_trial_tenants": 5
    },
    {
      "module_code": "GOAL_CONSOLE",
      "module_label": "Goal Console",
      "enabled_count": 2,
      "total_active_trial_tenants": 5
    },
    {
      "module_code": "PRICING_OS",
      "module_label": "Pricing OS",
      "enabled_count": 4,
      "total_active_trial_tenants": 5
    },
    {
      "module_code": "PERISHABLES_ASSISTANT",
      "module_label": "Perishables Assistant",
      "enabled_count": 5,
      "total_active_trial_tenants": 5
    },
    {
      "module_code": "PROMOTIONS_ASSISTANT",
      "module_label": "Promotions Assistant",
      "enabled_count": 3,
      "total_active_trial_tenants": 5
    },
    {
      "module_code": "ADMIN",
      "module_label": "Admin",
      "enabled_count": 5,
      "total_active_trial_tenants": 5
    }
  ]
}
```

**Field reference:**

| Field | Type | Notes |
|---|---|---|
| `items` | array | Always 6 entries. Ordered by `lookups.display_order ASC, code ASC`. |
| `items[].module_code` | enum string | One of `ROOS`, `GOAL_CONSOLE`, `PRICING_OS`, `PERISHABLES_ASSISTANT`, `PROMOTIONS_ASSISTANT`, `ADMIN`. Wire-stable. |
| `items[].module_label` | string | Display name from `lookups` (`list_name='module_code'`); COALESCE-fallback to raw enum code if a lookup row is missing. Always present. |
| `items[].enabled_count` | int | Visible tenants with this module ENABLED, where the tenant's status is ACTIVE or TRIAL. RLS-scoped: TENANT JWTs see 0 or 1. |
| `items[].total_active_trial_tenants` | int | Denominator: visible tenants with status IN (ACTIVE, TRIAL). Identical on every card in a single response (row-set property). |

### 4. Response codes

| Status | Code | When |
|---|---|---|
| 200 | — | Success |
| 401 | `AUTH_MISSING` / `AUTH_INVALID` | Missing or invalid JWT |
| 500 | `INTERNAL_ERROR` | Server-side failure (generic per anti-information-disclosure) |

Sample error body:

```json
{
  "code": "AUTH_MISSING",
  "message": "Authentication required",
  "details": null,
  "request_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

### 5. Behaviour notes

- **RLS-driven persona projection.** Both `tenants` and `tenant_module_access` carry RLS policies (D-29 OR-clause). PLATFORM sees all rows via the unconditional OR-branch; TENANT sees own-tenant rows via the equality clause. Same SQL, persona-correct values.
- **Aggregate denominator differs from dashboard.** `/dashboard/fleet-stats.active_tenants.total` uses `status != 'TERMINATED'` (4 lifecycle states); this endpoint uses `status IN ('ACTIVE', 'TRIAL')` (2 states). Different product questions: governance ("which tenants are commercially live?") vs platform health ("which tenants exist at all?").
- **Card ordering is contract.** Anchored on `lookups.display_order` from this step's seed migration; ROOS, GOAL_CONSOLE, PRICING_OS, PERISHABLES_ASSISTANT, PROMOTIONS_ASSISTANT, ADMIN. Frontend depends on the ordering being position-aligned with `/matrix.cells[]`.
- **Independent of `/matrix` filters.** Even if the same caller queries both endpoints with `/matrix?tier=ENTERPRISE`, `/modules` returns aggregates over the full visible ACTIVE+TRIAL tenant set — `/modules` is page-level KPI cards; `/matrix` is the filtered view below them.
- **No pagination, no query params, no sort.** Fixed cardinality of 6.

### 6. Example calls

```bash
# PLATFORM — fleet-wide aggregates
curl -H "Authorization: Bearer $PJWT" \
  http://localhost:8000/api/v1/module-access/modules

# TENANT — own-tenant only (counts collapse to 0/1)
curl -H "Authorization: Bearer $TJWT" \
  http://localhost:8000/api/v1/module-access/modules
```

### 7. Sample integration code

```ts
type ModuleCard = {
  module_code: 'ROOS' | 'GOAL_CONSOLE' | 'PRICING_OS' |
               'PERISHABLES_ASSISTANT' | 'PROMOTIONS_ASSISTANT' | 'ADMIN';
  module_label: string;
  enabled_count: number;
  total_active_trial_tenants: number;
};

type ModulesResponse = { items: ModuleCard[] };

async function loadModuleCards(): Promise<ModulesResponse> {
  const res = await fetch('/api/v1/module-access/modules', {
    headers: { Authorization: `Bearer ${jwt}` },
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}
```

### 8. Implementation reference

- Router: `src/admin_backend/routers/v1/modules_access.py:list_modules`
- Repo:   `src/admin_backend/repositories/modules_access.py:ModulesAccessRepo.list_modules_with_aggregates`
- Schema: `src/admin_backend/schemas/modules_access.py:ModuleCard, ModulesResponse`
- Migration: `migrations/versions/2fdc4bc9f4cb_step_6_7_module_access_lookups_seed.py`
- Tests:  `tests/integration/test_modules_access_router.py:test_m1..test_m5`

---

## `GET /api/v1/module-access/matrix`  (E2)

Tenant × module enablement grid.

### 1. Endpoint summary

- **Method:** `GET`
- **Path:** `/api/v1/module-access/matrix`
- **Description:** Returns the tenant × module grid for the Module Access governance console. Row set: all visible non-TERMINATED tenants. Each row carries 6 `cells[]` — one per module — synthesized backend-side via `tenants × modules CROSS JOIN LEFT JOIN tenant_module_access`. Position-aligned with `/modules.items[]`.
- **Who can call:** any authenticated user. PLATFORM sees fleet rows; TENANT sees exactly own-tenant.

### 2. Request

**Headers:**

| Header | Required | Notes |
|---|---|---|
| `Authorization` | Yes | `Bearer <jwt>` (PLATFORM or TENANT) |
| `Accept` | No | Defaults to `application/json` |

**Path parameters:** none.

**Query parameters:**

| Param | Type | Default | Validation |
|---|---|---|---|
| `sort` | string | `tier_asc` | One of: `name_asc`, `name_desc`, `created_at_asc`, `created_at_desc`, `tier_asc`, `tier_desc`. Stable secondary sort by name then id. Unknown -> 400 `INVALID_SORT_KEY`. |
| `tier` | string | (none) | One of: `ENTERPRISE`, `MID_MARKET`, `SMB`, `SINGLE_STORE`. Exact-match. Invalid value -> 422 (Pydantic). |
| `status` | string | (none) | One of: `ONBOARDING`, `TRIAL`, `ACTIVE`, `SUSPENDED`. Exact-match. `TERMINATED` is structurally absent from the matrix row set — not a valid filter value. |
| `q` | string | (none) | Trimmed; if length 0 after trim, treated as no filter. ILIKE substring match against `tenants.name` only. |
| `limit` | int | `25` | `>= 1`, `<= 200`. Above the cap -> 422. |
| `offset` | int | `0` | `>= 0`. |

**Request body:** none.

### 3. Response 200

```json
{
  "items": [
    {
      "tenant_id": "972a8469-1641-4f82-8b9d-2434e465e150",
      "name": "Buc-ee's",
      "tier": "ENTERPRISE",
      "tier_label": "Enterprise",
      "status": "ACTIVE",
      "status_label": "Active",
      "cells": [
        { "module_code": "ROOS",                  "status": "ENABLED" },
        { "module_code": "GOAL_CONSOLE",          "status": "ENABLED" },
        { "module_code": "PRICING_OS",            "status": "ENABLED" },
        { "module_code": "PERISHABLES_ASSISTANT", "status": "ENABLED" },
        { "module_code": "PROMOTIONS_ASSISTANT",  "status": "ENABLED" },
        { "module_code": "ADMIN",                 "status": "ENABLED" }
      ]
    },
    {
      "tenant_id": "...",
      "name": "Żabka Group",
      "tier": "ENTERPRISE",
      "tier_label": "Enterprise",
      "status": "ACTIVE",
      "status_label": "Active",
      "cells": [
        { "module_code": "ROOS",                  "status": "ENABLED" },
        { "module_code": "GOAL_CONSOLE",          "status": "DISABLED" },
        { "module_code": "PRICING_OS",            "status": "ENABLED" },
        { "module_code": "PERISHABLES_ASSISTANT", "status": "ENABLED" },
        { "module_code": "PROMOTIONS_ASSISTANT",  "status": "ENABLED" },
        { "module_code": "ADMIN",                 "status": "ENABLED" }
      ]
    }
  ],
  "pagination": {
    "limit": 25,
    "offset": 0,
    "total": 6
  }
}
```

**Field reference:**

| Field | Type | Notes |
|---|---|---|
| `items[]` | array | Tenant rows matching filter set, sliced to the requested page. |
| `items[].tenant_id` | UUID | |
| `items[].name` | string | |
| `items[].tier` | enum string \| null | One of `ENTERPRISE`, `MID_MARKET`, `SMB`, `SINGLE_STORE`, or null if the tenant has no tier set yet. |
| `items[].tier_label` | string \| null | Resolved from `lookups` (`list_name='tenant_tier'`). Null only when `tier` is null. |
| `items[].status` | enum string | One of `ONBOARDING`, `TRIAL`, `ACTIVE`, `SUSPENDED`. `TERMINATED` is structurally absent. |
| `items[].status_label` | string | Resolved from `lookups` (`list_name='tenant_status'`). Always present. |
| `items[].cells[]` | array | Always 6 entries. Position-aligned with `/modules.items[]`. |
| `items[].cells[].module_code` | enum string | Same vocabulary as `/modules`. |
| `items[].cells[].status` | enum string | One of `ENABLED`, `DISABLED`. Synthesized backend-side: absent rows AND `status='DISABLED'` rows both render as `DISABLED`. |
| `pagination.limit` | int | Echoed from the request. |
| `pagination.offset` | int | Echoed from the request. |
| `pagination.total` | int | Total visible rows matching the filter set (NOT capped to `limit`). |

### 4. Response codes

| Status | Code | When |
|---|---|---|
| 200 | — | Success |
| 400 | `INVALID_SORT_KEY` | Unknown `sort` value |
| 401 | `AUTH_MISSING` / `AUTH_INVALID` | Missing or invalid JWT |
| 422 | (Pydantic validation) | Invalid `tier` / `status` / `limit` value |
| 500 | `INTERNAL_ERROR` | Server-side failure |

Sample 400 body:

```json
{
  "code": "INVALID_SORT_KEY",
  "message": "Invalid sort key",
  "details": null,
  "request_id": "..."
}
```

### 5. Behaviour notes

- **RLS-driven row set.** TENANT JWTs see exactly 1 row (own tenant). PLATFORM JWTs see all visible non-TERMINATED tenants. The CROSS JOIN's left side is RLS-filtered; the LEFT JOIN to `tenant_module_access` is also RLS-filtered. Both filters compose cleanly.
- **Cell synthesis.** Each row's `cells[]` is exactly 6 entries — one per module in `module_code_enum`. Cells render as `ENABLED` only when an ENABLED `tenant_module_access` row matches; absent rows AND `status='DISABLED'` rows both render as `DISABLED`. Frontend doesn't distinguish absent vs explicitly-disabled.
- **Position-aligned ordering.** `cells[i].module_code` is the same for every row in this response AND matches `/modules.items[i].module_code`. Anchored on `lookups.display_order`. Frontend reconciles cell-to-module by index.
- **Sort applies at the tenant level.** `tier_asc` (default) sorts by tenant tier ASC; secondary stable sort by name then id keeps identical-tier values paginated deterministically.
- **`q` is name-only, ILIKE.** Case-insensitive substring match against `tenants.name`. No special-character escaping needed for typical names.
- **`total` reflects the full filter set.** Not capped to `limit`. Pagination math: `total / limit` pages.
- **Aggregate sort keys deliberately absent.** `/tenants` exposes `num_users_active_*`, `num_stores_*`; this endpoint doesn't expose those aggregates per row, so sorting by them would be meaningless.
- **TERMINATED is filtered at the row-set level.** Even an explicit `?status=TERMINATED` returns 0 rows (and is rejected at the Pydantic Literal layer as 422; not in the accepted vocabulary).

### 6. Example calls

```bash
# PLATFORM — full grid, default sort (tier_asc)
curl -H "Authorization: Bearer $PJWT" \
  "http://localhost:8000/api/v1/module-access/matrix?limit=25"

# Filter to enterprise tenants
curl -H "Authorization: Bearer $PJWT" \
  "http://localhost:8000/api/v1/module-access/matrix?tier=ENTERPRISE"

# Free-text search (case-insensitive)
curl -H "Authorization: Bearer $PJWT" \
  "http://localhost:8000/api/v1/module-access/matrix?q=buc"

# TENANT — 1-row response (own tenant)
curl -H "Authorization: Bearer $TJWT" \
  http://localhost:8000/api/v1/module-access/matrix

# Invalid sort -> 400
curl -H "Authorization: Bearer $PJWT" \
  "http://localhost:8000/api/v1/module-access/matrix?sort=garbage_desc"
```

### 7. Sample integration code

```ts
type ModuleCode =
  | 'ROOS' | 'GOAL_CONSOLE' | 'PRICING_OS'
  | 'PERISHABLES_ASSISTANT' | 'PROMOTIONS_ASSISTANT' | 'ADMIN';

type MatrixCell = {
  module_code: ModuleCode;
  status: 'ENABLED' | 'DISABLED';
};

type MatrixRow = {
  tenant_id: string;
  name: string;
  tier: 'ENTERPRISE' | 'MID_MARKET' | 'SMB' | 'SINGLE_STORE' | null;
  tier_label: string | null;
  status: 'ONBOARDING' | 'TRIAL' | 'ACTIVE' | 'SUSPENDED';
  status_label: string;
  cells: MatrixCell[];   // always 6, position-aligned with /modules
};

type Pagination = { total: number; offset: number; limit: number };

type MatrixResponse = { items: MatrixRow[]; pagination: Pagination };

async function loadMatrix(opts: {
  sort?: string; tier?: string; status?: string; q?: string;
  limit?: number; offset?: number;
} = {}): Promise<MatrixResponse> {
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(opts)) {
    if (v !== undefined && v !== '') params.set(k, String(v));
  }
  const res = await fetch(
    `/api/v1/module-access/matrix?${params}`,
    { headers: { Authorization: `Bearer ${jwt}` } }
  );
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}
```

### 8. Implementation reference

- Router: `src/admin_backend/routers/v1/modules_access.py:list_matrix`
- Repo:   `src/admin_backend/repositories/modules_access.py:ModulesAccessRepo.list_matrix`
- Schema: `src/admin_backend/schemas/modules_access.py:MatrixRow, MatrixCell, MatrixResponse`
- Migration: `migrations/versions/2fdc4bc9f4cb_step_6_7_module_access_lookups_seed.py`
- Tests:  `tests/integration/test_modules_access_router.py:test_x1..test_x9`

---

## `POST /api/v1/module-access/{tenant_id}/{module_code}/enable` (Step 6.15)

### 1. Endpoint summary

| Field | Value |
|---|---|
| Method | `POST` |
| Path | `/api/v1/module-access/{tenant_id}/{module_code}/enable` |
| Description | Enable `module_code` for `tenant_id`. Upserts: creates a `tenant_module_access` row when none exists; flips a `DISABLED` row to `ENABLED` otherwise; no-op on already-`ENABLED`. |
| Who can call | PLATFORM JWT only, with `ADMIN.TENANTS.OVERRIDE.GLOBAL` (SUPER_ADMIN per Phase 3 seed). Same privilege boundary as tenant suspend/activate. |

### 2. Request

- **Auth:** PLATFORM JWT (Layer 1 audience refusal for TENANT JWTs: 403 `PLATFORM_AUDIENCE_REQUIRED`).
- **Path params:**
  - `tenant_id` (UUID, required).
  - `module_code` (enum: one of `PRICING_OS`, `PERISHABLES_ASSISTANT`, `PROMOTIONS_ASSISTANT`, `GOAL_CONSOLE`, `ADMIN`). FastAPI validates the enum at path-param time; invalid values return 422 before the handler runs.
- **Query params:** none.
- **Body:** none (the verb-suffix POST is a controller endpoint, not a resource-create endpoint).

### 3. Response 200

Returns the post-transition `ModuleAccessRead`:

```json
{
  "id": "019e2cf0-d2d5-7c20-955f-3a3b9a1d6e51",
  "tenant_id": "019e2cda-69ff-74e4-9b4f-cfd193f855b1",
  "module": "GOAL_CONSOLE",
  "status": "ENABLED",
  "enabled_at": "2026-05-16T03:14:25.382000+00:00",
  "disabled_at": null,
  "created_at": "2026-05-16T03:14:25.382000+00:00",
  "updated_at": "2026-05-16T03:14:25.382000+00:00"
}
```

| Field | Type | Description |
|---|---|---|
| `id` | UUID | Row primary key. |
| `tenant_id` | UUID | Tenant that owns the access row. |
| `module` | enum | Module code mirroring `module_code_enum`. |
| `status` | enum | `ENABLED` (after enable) or `DISABLED` (post a subsequent disable). |
| `enabled_at` | ISO-8601 datetime | Start of the current ENABLED stint. Overwritten on every `DISABLED -> ENABLED` flip per LD5. |
| `disabled_at` | ISO-8601 datetime or null | NULL whenever `status='ENABLED'` (DDL `ck_tenant_module_access_status_consistency`). |
| `created_at` | ISO-8601 datetime | Row creation timestamp. |
| `updated_at` | ISO-8601 datetime | Auto-updated by the BEFORE-UPDATE trigger. Idempotent no-op cases leave it unchanged. |

Audit-actor IDs (`enabled_by_user_id`, `disabled_by_user_id`, `created_by_user_id`, `updated_by_user_id`) are hidden per the H1 convention.

### 4. Response codes

| Status | Code | When |
|---|---|---|
| 200 | n/a | Row created, updated, or returned unchanged (idempotent no-op on `ENABLED -> ENABLED`). |
| 403 | `PLATFORM_AUDIENCE_REQUIRED` | TENANT JWT (Layer 1 refusal). |
| 403 | `PERMISSION_DENIED` | PLATFORM JWT without `ADMIN.TENANTS.OVERRIDE.GLOBAL` (e.g., PLATFORM_ADMIN). |
| 404 | `TENANT_NOT_FOUND` | `tenant_id` does not resolve to a visible tenant-root org_node. |
| 422 | Pydantic validation envelope | Path param `module_code` is not one of the canonical enum values. |

```json
{
  "code": "PLATFORM_AUDIENCE_REQUIRED",
  "message": "This operation requires a platform user.",
  "details": null,
  "request_id": "<uuid>"
}
```

### 5. Behaviour notes

- **Upsert seam.** Missing row -> INSERT a new row with `status=ENABLED`. The `(tenant_id, module)` UNIQUE constraint arbitrates upsert race; concurrent enable-on-missing is handled by an `IntegrityError` retry that takes the UPDATE branch (LD8).
- **Idempotent no-op.** `enable` on an already-ENABLED row returns 200 with the row unchanged; `updated_at` does NOT advance. The BEFORE-UPDATE trigger is not fired because no UPDATE statement is issued.
- **LD5 overwrite semantics.** `DISABLED -> ENABLED` overwrites `enabled_at` (marking the start of the new ENABLED stint) and `enabled_by_user_id` (the acting platform user), and clears `disabled_at` + `disabled_by_user_id` atomically per `ck_tenant_module_access_disabled_pair` + `ck_tenant_module_access_status_consistency`.
- **Access cascade.** The TENANT-path `has_permission()` JOINs `tenant_module_access` filtered to `status='ENABLED'`. Toggling a module flips access on the next request without touching any role assignment row.
- **RLS posture.** The session is PLATFORM-only, so D-29's OR-branch admits the row write regardless of `app.tenant_id`. The anchor dep `get_tenant_anchor` runs first under the same session and surfaces an unreachable tenant as 404 (RLS-as-404 per D-17).

### 6. Example calls

```bash
# Happy path (SUPER_ADMIN, new row).
curl -s -X POST \
    -H "Authorization: Bearer $PLATFORM_JWT" \
    "https://admin-backend.local/api/v1/module-access/$TENANT_ID/GOAL_CONSOLE/enable"

# Layer 1 refusal (TENANT JWT).
curl -s -X POST \
    -H "Authorization: Bearer $TENANT_JWT" \
    "https://admin-backend.local/api/v1/module-access/$TENANT_ID/GOAL_CONSOLE/enable"
# -> 403 PLATFORM_AUDIENCE_REQUIRED
```

### 7. Sample integration code (TypeScript)

```typescript
type ModuleCode =
  | 'PRICING_OS' | 'PERISHABLES_ASSISTANT' | 'PROMOTIONS_ASSISTANT'
  | 'GOAL_CONSOLE' | 'ADMIN';

type ModuleAccessRead = {
  id: string;
  tenant_id: string;
  module: ModuleCode;
  status: 'ENABLED' | 'DISABLED';
  enabled_at: string;
  disabled_at: string | null;
  created_at: string;
  updated_at: string;
};

async function enableModule(
  tenantId: string,
  moduleCode: ModuleCode,
): Promise<ModuleAccessRead> {
  const res = await fetch(
    `/api/v1/module-access/${tenantId}/${moduleCode}/enable`,
    { method: 'POST', headers: { Authorization: `Bearer ${jwt}` } },
  );
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}
```

### 8. Implementation reference

- Router: `src/admin_backend/routers/v1/modules_access.py:enable_module_for_tenant`
- Repo:   `src/admin_backend/repositories/modules_access.py:ModulesAccessRepo.enable`
- Schema: `src/admin_backend/schemas/modules_access.py:ModuleAccessRead`
- Error:  `src/admin_backend/errors.py:ModuleAccessNotFoundError`
- Tests:  `tests/integration/test_module_access_writes_router.py:test_c1..test_c3, test_r1, test_r2`;
          `tests/integration/test_module_access_repo_writes.py:test_rt1..test_rt4`

---

## `POST /api/v1/module-access/{tenant_id}/{module_code}/disable` (Step 6.15)

### 1. Endpoint summary

| Field | Value |
|---|---|
| Method | `POST` |
| Path | `/api/v1/module-access/{tenant_id}/{module_code}/disable` |
| Description | Disable `module_code` for `tenant_id`. 404 when no row exists for the supplied pair (only the disable path produces this code; enable upserts). |
| Who can call | PLATFORM JWT only, with `ADMIN.TENANTS.OVERRIDE.GLOBAL`. |

### 2. Request

- **Auth:** PLATFORM JWT (Layer 1 refusal for TENANT: 403 `PLATFORM_AUDIENCE_REQUIRED`).
- **Path params:** `tenant_id` (UUID), `module_code` (enum; see enable).
- **Query params:** none.
- **Body:** none.

### 3. Response 200

Returns the post-transition `ModuleAccessRead` (same schema as enable). After a DISABLE flip, `status='DISABLED'`, `disabled_at` is non-null, and `enabled_at` is preserved as the historical record of when the just-ended ENABLED stint began.

### 4. Response codes

| Status | Code | When |
|---|---|---|
| 200 | n/a | Row updated, or returned unchanged (idempotent no-op on `DISABLED -> DISABLED`). |
| 403 | `PLATFORM_AUDIENCE_REQUIRED` | TENANT JWT. |
| 403 | `PERMISSION_DENIED` | PLATFORM JWT without `ADMIN.TENANTS.OVERRIDE.GLOBAL`. |
| 404 | `TENANT_NOT_FOUND` | `tenant_id` does not resolve to a visible tenant-root org_node. |
| 404 | `MODULE_ACCESS_NOT_FOUND` | No `tenant_module_access` row exists for `(tenant_id, module_code)`. Only the disable path produces this; enable upserts. |
| 422 | Pydantic validation envelope | `module_code` not in the canonical enum. |

```json
{
  "code": "MODULE_ACCESS_NOT_FOUND",
  "message": "Module access not found for the requested tenant and module.",
  "details": null,
  "request_id": "<uuid>"
}
```

### 5. Behaviour notes

- **No 409.** Unlike tenant suspend/activate, this endpoint pair returns 200 on every legal no-op (no `INVALID_STATE_TRANSITION`). The audit-trail and operational profile of module flips do not warrant the strict-transition shape.
- **`enabled_at` preserved on disable.** The just-ended ENABLED stint's start timestamp carries forward as the historical record per LD5; only `disabled_at` and `disabled_by_user_id` are written.
- **Cascade is structural.** A `DISABLED` row fails the `has_permission()` JOIN. Permission checks against the module return false on the next request without any role-assignment writes; re-enable restores access automatically.

### 6. Example calls

```bash
# Happy path (SUPER_ADMIN, flips ENABLED -> DISABLED).
curl -s -X POST \
    -H "Authorization: Bearer $PLATFORM_JWT" \
    "https://admin-backend.local/api/v1/module-access/$TENANT_ID/PRICING_OS/disable"

# No row for the pair -> 404 MODULE_ACCESS_NOT_FOUND.
curl -s -X POST \
    -H "Authorization: Bearer $PLATFORM_JWT" \
    "https://admin-backend.local/api/v1/module-access/$TENANT_ID/GOAL_CONSOLE/disable"
```

### 7. Sample integration code (TypeScript)

```typescript
async function disableModule(
  tenantId: string,
  moduleCode: ModuleCode,
): Promise<ModuleAccessRead> {
  const res = await fetch(
    `/api/v1/module-access/${tenantId}/${moduleCode}/disable`,
    { method: 'POST', headers: { Authorization: `Bearer ${jwt}` } },
  );
  if (res.status === 404) {
    // Row never existed for this (tenant, module). Caller can decide
    // whether to surface as "already disabled" or as a domain error.
    throw new Error('MODULE_ACCESS_NOT_FOUND');
  }
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}
```

### 8. Implementation reference

- Router: `src/admin_backend/routers/v1/modules_access.py:disable_module_for_tenant`
- Repo:   `src/admin_backend/repositories/modules_access.py:ModulesAccessRepo.disable`
- Schema: `src/admin_backend/schemas/modules_access.py:ModuleAccessRead`
- Error:  `src/admin_backend/errors.py:ModuleAccessNotFoundError`
- Tests:  `tests/integration/test_module_access_writes_router.py:test_c4..test_c6, test_r2`
