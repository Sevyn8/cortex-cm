# Tenants endpoints

Canonical endpoint documentation for the tenants resource. Three GET endpoints under `/api/v1/tenants`. Format follows CLAUDE.md "Per-endpoint documentation" — eight fixed sections per endpoint. Every subsequent endpoint doc inherits this structure.

| Endpoint | Description | Calling user types |
|---|---|---|
| `GET /api/v1/tenants` | List tenants with filters and pagination | PLATFORM (sees all), TENANT (sees own only) |
| `GET /api/v1/tenants/stats` | Header-summary scalars: total tenants and total stores | PLATFORM (realistic consumer); TENANT works but returns own counts |
| `GET /api/v1/tenants/{tenant_id}` | Single tenant detail | PLATFORM (sees all), TENANT (own only; other -> 404 per RLS) |

Cross-cutting:

- **Auth** — `Authorization: Bearer <jwt>` required; missing or invalid -> 401.
- **Response envelope** — list shape is `{items, pagination}` (D-30); single-object endpoints return the object directly.
- **Field semantics** — append-only per D-31. Once a field's meaning ships, it stays. New variants get new field names.
- **Error envelope** — `{code, message, details, request_id}` on all server-generated errors. `details` is `null` in v0 (slot reserved for per-field validation info).
- **`X-Request-Id`** — set on every response by the audit middleware; same UUID appears in the per-request log line.
- **RBAC** — not enforced in v0; lands at Step 6.1 (`ADMIN.TENANTS.VIEW`).

---

## `GET /api/v1/tenants`

List tenants visible to the calling user, filtered, paginated.

### 1. Endpoint summary

- **Method:** `GET`
- **Path:** `/api/v1/tenants`
- **Description:** Returns visible tenants paginated. Visibility is governed by RLS via D-29's policy clause.
- **Who can call:** any authenticated user. PLATFORM sees all rows; TENANT sees their own row only.

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
| `tier` | string | (none) | One of `tenant_tier_enum` values: `ENTERPRISE`, `MID_MARKET`, `SMB`, `SINGLE_STORE`. Other values -> 422. |
| `search` | string | (none) | Trimmed; if length 0 after trim, treated as no filter. ILIKE match against `name`, `display_code`, `contact_email`. |
| `sort` | string | `created_at_desc` | Column-based: `created_at_asc`, `created_at_desc`, `name_asc`, `name_desc`, `tier_asc`, `tier_desc`. Aggregate-based (correlated subqueries, RLS-correct): `num_users_active_asc`, `num_users_active_desc`, `num_stores_asc`, `num_stores_desc`. Stable secondary sort by `id ASC` so identical primary-sort values page deterministically. Unknown values -> 400 `INVALID_SORT_KEY`. |
| `offset` | int | `0` | `>= 0` |
| `limit` | int | `20` | `>= 1`, `<= 100`. Above the cap -> 422. |

**Request body:** none.

### 3. Response 200

```json
{
  "items": [
    {
      "id": "972a8469-1641-4f82-8b9d-2434e465e150",
      "name": "Buc-ee's",
      "display_code": "buc-ees",
      "country": "USA",
      "region": "US",
      "industry": "CONVENIENCE_FUEL",
      "tier": "ENTERPRISE",
      "status": "ACTIVE",
      "monthly_revenue_usd": "48500.00",
      "num_stores": 47,
      "num_users_active": 312,
      "modules": [
        { "code": "ROOS", "name": "ROOS" },
        { "code": "PRICING_OS", "name": "Pricing OS" }
      ],
      "created_at": "2026-04-19T15:00:00+00:00",
      "updated_at": "2026-04-19T15:00:00+00:00"
    }
  ],
  "pagination": {
    "total": 7,
    "offset": 0,
    "limit": 20
  }
}
```

**Field reference:**

| Field | Type | Nullable | Notes |
|---|---|---|---|
| `id` | UUID string | No | Tenant identifier |
| `name` | string | No | Display name |
| `display_code` | string | Yes | URL-friendly slug |
| `country` | string | Yes | Free-form name or abbreviation |
| `region` | enum string | No | `US` or `EU`; pinned at tenant creation |
| `industry` | enum string | Yes | One of the `tenant_industry_enum` values |
| `tier` | enum string | Yes | One of the `tenant_tier_enum` values |
| `status` | enum string | No | One of the `tenant_status_enum` values |
| `monthly_revenue_usd` | decimal string | Yes | Stored `NUMERIC(15, 2)`; serialised as string to preserve precision (D-28 / Q11) |
| `num_stores` | int | No | **Live count** from `stores` filtered to this tenant — NOT the self-reported `tenants.number_of_stores` snapshot (which is on detail) |
| `num_users_active` | int | No | Live count from `tenant_users` where `status = 'ACTIVE'`, filtered to this tenant |
| `modules` | list of `{code, name}` | No | Per-tenant module entitlements; comes from a stub today (FN-AB-16) |
| `created_at` | ISO 8601 with offset | No | When the tenant row was inserted |
| `updated_at` | ISO 8601 with offset | No | Most recent update on any field |

**Pagination block:**

| Field | Type | Notes |
|---|---|---|
| `total` | int | RLS-filtered total — what the caller can see, not the platform total |
| `offset` | int | Echo of request `offset` |
| `limit` | int | Echo of request `limit` |

The list response intentionally **omits** `monthly_revenue_as_of_date`, `number_of_stores` (the snapshot), `number_of_stores_as_of_date`, `primary_contact_name`, `contact_email`, `suspended_at`, `terminated_at`. All available on detail.

### 4. Response codes

| Code | When | Body |
|---|---|---|
| 200 | Success | Body as above |
| 401 | Missing or invalid JWT | `{code: "AUTH_MISSING" | "AUTH_INVALID", message, details: null, request_id}` |
| 403 | Reserved for future RBAC (Step 6.1); not raised in v0 | n/a in v0 |
| 422 | Query-param validation failure (bad tier, limit out of range, etc.) | FastAPI default validation envelope |
| 500 | Internal server error | `{code: "INTERNAL_ERROR", message: "An internal error occurred", details: null, request_id}` |

**Sample 401:**

```json
{
  "code": "AUTH_MISSING",
  "message": "Authentication required",
  "details": null,
  "request_id": "fac2d99f-94e0-4806-b354-e6f3e6a22fa6"
}
```

### 5. Behaviour notes

- **RLS scope.** PLATFORM session sees all rows via D-29's OR-branch. TENANT session sees only the row matching `app.tenant_id`. RLS filters automatically; no handler-side filtering.
- **Default sort.** `created_at_desc` (newest first), with stable secondary `id ASC`. Pre-Step-6.4 the endpoint had no `sort` param and ordering was hardcoded `name ASC`; callers who don't pass `sort` now receive `created_at_desc`. Step 6.4 widened the accepted vocabulary to 10 keys (6 column-based + 4 aggregate-based — see Query parameters). The aggregate keys reference correlated scalar subqueries that inherit RLS via session GUCs, so sorting by `num_users_active_*` / `num_stores_*` is RLS-correct in both PLATFORM and TENANT contexts.
- **Pagination.** Offset/limit; the count query and the page query share the same WHERE clause so `total` matches the filtered set.
- **Search.** ILIKE substring match across three columns. Multi-word `search` (e.g., `acme corp`) matches as a single phrase, not OR-ed tokens. Diacritics not normalised in v0.
- **Empty result.** Returns `{items: [], pagination: {total: 0, offset: 0, limit: 20}}` and 200, not 404.
- **`num_stores` vs `number_of_stores`.** `num_stores` (this endpoint) is the live count from `stores`. `number_of_stores` (detail only) is the self-reported snapshot from the tenant row.
- **Modules.** From `_module_entitlements_stub.py` per FN-AB-16. Tenants not in the stub get `[]`. The xfail-strict tripwire test in `tests/unit/test_module_entitlements.py` forces deletion of the stub when the real `tenant_module_access` table ships.

### 6. Example calls

```bash
# All tenants visible to the caller (default page).
curl -s -H "Authorization: Bearer $JWT" \
  "https://admin-dev.ithina.com/api/v1/tenants"

# Filter by tier.
curl -s -H "Authorization: Bearer $JWT" \
  "https://admin-dev.ithina.com/api/v1/tenants?tier=ENTERPRISE"

# Search (ILIKE) across name / display_code / contact_email.
curl -s -H "Authorization: Bearer $JWT" \
  "https://admin-dev.ithina.com/api/v1/tenants?search=acme"

# Page 2 of 20 per page.
curl -s -H "Authorization: Bearer $JWT" \
  "https://admin-dev.ithina.com/api/v1/tenants?offset=20&limit=20"

# Top 5 tenants by active-user count (Step 6.5 dashboard's Top Tenants panel).
curl -s -H "Authorization: Bearer $JWT" \
  "https://admin-dev.ithina.com/api/v1/tenants?sort=num_users_active_desc&limit=5"

# Sort alphabetically by name.
curl -s -H "Authorization: Bearer $JWT" \
  "https://admin-dev.ithina.com/api/v1/tenants?sort=name_asc"
```

### 7. Sample integration code

```typescript
type TenantsListItem = {
  id: string;
  name: string;
  display_code: string | null;
  country: string | null;
  region: "US" | "EU";
  industry: string | null;
  tier: string | null;
  status: "ONBOARDING" | "TRIAL" | "ACTIVE" | "SUSPENDED" | "TERMINATED";
  monthly_revenue_usd: string | null;
  num_stores: number;
  num_users_active: number;
  modules: Array<{ code: string; name: string }>;
  created_at: string;
  updated_at: string;
};

type TenantsListResponse = {
  items: TenantsListItem[];
  pagination: { total: number; offset: number; limit: number };
};

async function listTenants(
  jwt: string,
  filters: { tier?: string; search?: string; offset?: number; limit?: number } = {},
): Promise<TenantsListResponse> {
  const url = new URL("https://admin-dev.ithina.com/api/v1/tenants");
  for (const [k, v] of Object.entries(filters)) {
    if (v !== undefined) url.searchParams.set(k, String(v));
  }
  const r = await fetch(url.toString(), {
    headers: { Authorization: `Bearer ${jwt}`, Accept: "application/json" },
  });
  if (!r.ok) {
    const e = await r.json();
    throw new Error(`${e.code}: ${e.message}`);
  }
  return r.json();
}
```

### 8. Implementation reference

| File | Role |
|---|---|
| `src/admin_backend/routers/v1/tenants.py` | `list_tenants` handler |
| `src/admin_backend/repositories/tenants.py` | `TenantsRepo.list_with_aggregates` |
| `src/admin_backend/schemas/tenant.py` | `TenantsListResponse`, `TenantsListItem`, `Pagination`, `Module` |
| `src/admin_backend/models/tenant.py` | `Tenant` ORM model |
| `src/admin_backend/repositories/_module_entitlements_stub.py` | Module data (FN-AB-16 stub) |
| `tests/integration/test_tenants_router.py` | L1-L10 tests |

---

## `GET /api/v1/tenants/stats`

Header summary scalars: visible tenant count and visible store count.

### 1. Endpoint summary

- **Method:** `GET`
- **Path:** `/api/v1/tenants/stats`
- **Description:** Two RLS-filtered scalars for header rendering.
- **Who can call:** any authenticated user. PLATFORM is the realistic consumer; TENANT works but returns its own scoped counts.

### 2. Request

**Headers:** Authorization required (as above).

**Path parameters:** none.

**Query parameters:** none.

**Request body:** none.

### 3. Response 200

```json
{
  "total_tenants": 7,
  "total_stores": 10084
}
```

**Field reference:**

| Field | Type | Notes |
|---|---|---|
| `total_tenants` | int | `count(*) FROM tenants` under the caller's RLS |
| `total_stores` | int | `count(*) FROM stores` under the caller's RLS |

**Response headers:** `Cache-Control: private, max-age=60` — the only endpoint setting a cache header in v0.

### 4. Response codes

| Code | When | Body |
|---|---|---|
| 200 | Success | Body as above |
| 401 | Missing or invalid JWT | Standard auth-error envelope |
| 500 | Internal server error | Standard `INTERNAL_ERROR` envelope |

### 5. Behaviour notes

- For PLATFORM callers, both scalars reflect platform totals (D-29 OR-branch on tenants and stores).
- For TENANT callers, both scalars reflect the caller's RLS scope — typically `total_tenants = 1` (their own) and `total_stores =` their own store count.
- Cache header is `private` (per-user; not shareable across users) with a 1-minute TTL — long enough for repeated header refreshes during a UI session, short enough that newly-onboarded tenants surface within a minute.

### 6. Example calls

```bash
curl -s -H "Authorization: Bearer $JWT" \
  "https://admin-dev.ithina.com/api/v1/tenants/stats"
```

### 7. Sample integration code

```typescript
type TenantsStats = { total_tenants: number; total_stores: number };

async function getTenantsStats(jwt: string): Promise<TenantsStats> {
  const r = await fetch(
    "https://admin-dev.ithina.com/api/v1/tenants/stats",
    { headers: { Authorization: `Bearer ${jwt}` } },
  );
  if (!r.ok) {
    const e = await r.json();
    throw new Error(`${e.code}: ${e.message}`);
  }
  return r.json();
}
```

### 8. Implementation reference

| File | Role |
|---|---|
| `src/admin_backend/routers/v1/tenants.py` | `tenants_stats` handler |
| `src/admin_backend/repositories/tenants.py` | `TenantsRepo.count_for_stats` |
| `src/admin_backend/schemas/tenant.py` | `TenantsStatsResponse` |
| `tests/integration/test_tenants_router.py` | S1-S3 tests |

---

## `GET /api/v1/tenants/{tenant_id}`

Single tenant detail by ID.

### 1. Endpoint summary

- **Method:** `GET`
- **Path:** `/api/v1/tenants/{tenant_id}`
- **Description:** Full detail shape for a single tenant; includes the same live aggregates the list endpoint exposes plus the snapshot fields the list endpoint omits.
- **Who can call:** PLATFORM gets any visible tenant; TENANT gets only its own row (others -> 404 per RLS / D-17).

### 2. Request

**Headers:** Authorization required (as above).

**Path parameters:**

| Param | Type | Notes |
|---|---|---|
| `tenant_id` | UUID string | Tenant identifier; FastAPI validates the shape. Malformed -> 422. |

**Query parameters:** none.

**Request body:** none.

### 3. Response 200

```json
{
  "id": "972a8469-1641-4f82-8b9d-2434e465e150",
  "name": "Żabka Group",
  "display_code": "zabka-group",
  "country": "Poland",
  "region": "EU",
  "tier": "ENTERPRISE",
  "industry": "CONVENIENCE",
  "monthly_revenue_usd": "142000.00",
  "monthly_revenue_as_of_date": "2026-04-01",
  "number_of_stores": 9842,
  "number_of_stores_as_of_date": "2026-04-01",
  "primary_contact_name": "Tomasz Nowak",
  "contact_email": "tomasz.nowak@zabka.pl",
  "status": "ACTIVE",
  "created_at": "2026-04-19T15:00:00+00:00",
  "updated_at": "2026-04-19T15:00:00+00:00",
  "suspended_at": null,
  "terminated_at": null,
  "num_stores": 9842,
  "num_users_active": 1240,
  "modules": [
    { "code": "ROOS", "name": "ROOS" }
  ]
}
```

21 fields, fully flat. No `live_counts` nesting, no `lifecycle` nesting, no `*_by_user_id` exposed (Step 3.1's hide policy stands).

**Field reference:** all `TenantsListItem` fields (see list endpoint above) PLUS:

| Field | Type | Nullable | Notes |
|---|---|---|---|
| `monthly_revenue_as_of_date` | ISO date | Yes | When the `monthly_revenue_usd` snapshot was reported |
| `number_of_stores` | int | Yes | Self-reported snapshot of the tenant's store count (NOT the live `num_stores` count above) |
| `number_of_stores_as_of_date` | ISO date | Yes | When the `number_of_stores` snapshot was reported |
| `primary_contact_name` | string | Yes | Free-form |
| `contact_email` | string | Yes | Lowercase enforced at write time |
| `suspended_at` | ISO 8601 with offset | Yes | Set when status -> SUSPENDED |
| `terminated_at` | ISO 8601 with offset | Yes | Set when status -> TERMINATED |

### 4. Response codes

| Code | When | Body |
|---|---|---|
| 200 | Success | Body as above |
| 401 | Missing or invalid JWT | Standard auth-error envelope |
| 404 | Tenant not found OR RLS-filtered | `{code: "TENANT_NOT_FOUND", message: "Tenant not found", details: null, request_id}` |
| 422 | `tenant_id` not a valid UUID | FastAPI default validation envelope |
| 500 | Internal server error | Standard `INTERNAL_ERROR` envelope |

**Sample 404 (canonical envelope):**

```json
{
  "code": "TENANT_NOT_FOUND",
  "message": "Tenant not found",
  "details": null,
  "request_id": "4de64a37-7905-469b-806a-b300876b5d4c"
}
```

### 5. Behaviour notes

- **RLS-as-404 (D-17).** A TENANT-type user requesting another tenant's id gets 404, not 403. RLS filters the row out before the handler sees it; the handler can't (and shouldn't) distinguish "no such tenant" from "you can't see this tenant". Returning 403 would leak existence.
- **Concurrent updates.** Reflects the row at query time. No version token in v0; if write endpoints land in v1 with optimistic concurrency, `updated_at` is the natural anchor.
- **Aggregates match the list endpoint.** `num_stores` and `num_users_active` use the same subquery semantics — live counts, RLS-filtered, correlated to this tenant.

### 6. Example calls

```bash
curl -s -H "Authorization: Bearer $JWT" \
  "https://admin-dev.ithina.com/api/v1/tenants/972a8469-1641-4f82-8b9d-2434e465e150"
```

### 7. Sample integration code

```typescript
type TenantDetail = TenantsListItem & {
  monthly_revenue_as_of_date: string | null;
  number_of_stores: number | null;
  number_of_stores_as_of_date: string | null;
  primary_contact_name: string | null;
  contact_email: string | null;
  suspended_at: string | null;
  terminated_at: string | null;
};

async function getTenant(jwt: string, tenantId: string): Promise<TenantDetail> {
  const r = await fetch(
    `https://admin-dev.ithina.com/api/v1/tenants/${tenantId}`,
    { headers: { Authorization: `Bearer ${jwt}` } },
  );
  if (r.status === 404) {
    throw new Error(`Tenant ${tenantId} not found`);
  }
  if (!r.ok) {
    const e = await r.json();
    throw new Error(`${e.code}: ${e.message}`);
  }
  return r.json();
}
```

### 8. Implementation reference

| File | Role |
|---|---|
| `src/admin_backend/routers/v1/tenants.py` | `get_tenant` handler |
| `src/admin_backend/repositories/tenants.py` | `TenantsRepo.get_by_id_with_aggregates` |
| `src/admin_backend/schemas/tenant.py` | `TenantDetail` |
| `src/admin_backend/errors.py` | `TenantNotFoundError` (404 / `TENANT_NOT_FOUND`) |
| `tests/integration/test_tenants_router.py` | D1-D6 tests |

---

## `POST /api/v1/tenants`

### 1. Endpoint summary

- Method: `POST`
- Path: `/api/v1/tenants`
- Description: Provision a new tenant. Server-forces `status=TRIAL`. The `ADMIN` module is force-merged into the requested modules and one `tenant_module_access` row per module is inserted in the same transaction as the `tenants` row. **Also (Step 6.20.1) inserts the tenant-root `org_nodes` row in the same transaction**: `node_type='TENANT'`, `parent_id IS NULL`, `code` and `path` mechanically derived from `display_code` (if provided) or `name`. That org_node is the load-bearing anchor for every tenant-scoped endpoint gated with `get_tenant_anchor` (GET detail, PATCH, suspend, activate, org-tree, module-access). See `docs/architecture.md` Appendix A.3 for the slug rule.
- Who can call: **Platform users only.** Two-layer gate: `audience="PLATFORM"` rejects TENANT JWTs ahead of `has_permission`; Layer 2 requires `ADMIN.TENANTS.CONFIGURE.GLOBAL` (held by `SUPER_ADMIN` and `PLATFORM_ADMIN`).

### 2. Request

- Auth: `Authorization: Bearer <PLATFORM JWT>`.
- Body (`application/json`):

| Field | Type | Required | Notes |
|---|---|---|---|
| `name` | string (1-200) | yes | App-layer UNIQUE — duplicates surface as 409 |
| `region` | enum (`US` \| `EU`) | yes | Immutable post-create |
| `tier` | enum (`ENTERPRISE` \| `MID_MARKET` \| `SMB` \| `SINGLE_STORE`) | yes | |
| `industry` | enum | yes | One of: `CONVENIENCE_FUEL`, `CONVENIENCE`, `GROCERY`, `HYPERMART`, `SPECIALITY_GROCERY`, `ORGANIC_GROCERY` |
| `country` | string (2-100) | yes | |
| `primary_contact_name` | string (1-200) | yes | |
| `contact_email` | email | yes | Lowercased server-side |
| `number_of_stores` | integer (>=1) | yes | Self-reported snapshot |
| `number_of_stores_as_of_date` | date | yes | DDL CHECK mandates both-or-neither |
| `display_code` | string (<=64) | no | Lowercase URL-friendly slug |
| `monthly_revenue_usd` | decimal (>=0) | no | If present, `monthly_revenue_as_of_date` is required (and vice versa) |
| `monthly_revenue_as_of_date` | date | no | |
| `modules_enabled` | array of `module_code` | no | Defaults `[]`; ADMIN auto-included; duplicates collapsed |

`status` is not accepted — `extra="forbid"` rejects.

### 3. Response 201

Returns the full `TenantDetail` shape (same fields as `GET /api/v1/tenants/{id}`), with `status="TRIAL"` and `modules` populated.

```json
{
  "id": "019e2747-5608-7235-a747-1490f9e3a971",
  "name": "Acme Retail",
  "display_code": null,
  "country": "United States",
  "region": "US",
  "tier": "ENTERPRISE",
  "industry": "GROCERY",
  "monthly_revenue_usd": null,
  "monthly_revenue_as_of_date": null,
  "number_of_stores": 5,
  "number_of_stores_as_of_date": "2026-01-01",
  "primary_contact_name": "Alice Operator",
  "contact_email": "alice@acme.example",
  "status": "TRIAL",
  "created_at": "2026-05-14T16:17:27.038932Z",
  "updated_at": "2026-05-14T16:17:27.038932Z",
  "suspended_at": null,
  "terminated_at": null,
  "num_stores": 0,
  "num_users_active": 0,
  "modules": [{"code": "ADMIN", "name": "Admin"}]
}
```

### 4. Response codes

| Status | Code | When |
|---|---|---|
| 201 | — | Tenant created. Response body is `TenantDetail`. |
| 401 | `AUTH_MISSING` / `AUTH_INVALID` | JWT missing or invalid. |
| 403 | `PLATFORM_AUDIENCE_REQUIRED` | Caller is a TENANT user (Layer 1 refusal). |
| 403 | `PERMISSION_DENIED` | PLATFORM caller lacks `ADMIN.TENANTS.CONFIGURE.GLOBAL`. |
| 409 | `DUPLICATE_TENANT_NAME` | Another tenant already has this name. |
| 422 | `INVALID_TENANT_NAME_FOR_SLUG` | (Step 6.20.1) `name` (or `display_code`) produced an empty slug after diacritic-strip and alphanumeric-collapse (e.g. `"!!!"`). Supply a `display_code` or use a name with alphanumeric characters. |
| 422 | (validation) | Field missing, malformed, or `status`/`region`/`id` in body. |

### 5. Behaviour notes

- `status` is server-forced to `TRIAL`. The DDL default `ONBOARDING` is never reached through this path.
- `modules_enabled` validation: ADMIN auto-appended if absent; duplicates collapsed preserving order.
- `contact_email` is lowercased before INSERT (satisfies `ck_tenants_contact_email_lowercase`).
- The `monthly_revenue_usd` / `monthly_revenue_as_of_date` pair is enforced both-or-neither at the schema layer.
- Audit columns (`created_by_user_id`, `updated_by_user_id`) populated from the JWT's `user_id` (Pattern (a) FK to `platform_users` per D-13).
- Name-uniqueness is enforced app-layer via SELECT-then-INSERT in the same transaction. No DB UNIQUE constraint in v0 (tracked as FN-AB).
- **(Step 6.20.1) Side-effect: tenant-root org_node insert.** The same transaction also inserts one row in `core.org_nodes` with `node_type='TENANT'`, `parent_id IS NULL`, `status='ACTIVE'`. The row's `code` and `path` are derived mechanically from `display_code` (if provided) or `name`; an empty slug raises 422 `INVALID_TENANT_NAME_FOR_SLUG` BEFORE the `tenants` INSERT so a 422 leaves no partial state. See `docs/architecture.md` Appendix A.3 for the slug rule.

### 6. Example calls

```bash
# Happy path.
curl -X POST \
  -H "Authorization: Bearer ${PLATFORM_JWT}" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Acme Retail",
    "region": "US",
    "tier": "ENTERPRISE",
    "industry": "GROCERY",
    "country": "United States",
    "primary_contact_name": "Alice",
    "contact_email": "alice@acme.example",
    "number_of_stores": 5,
    "number_of_stores_as_of_date": "2026-01-01",
    "modules_enabled": ["PRICING_OS"]
  }' \
  https://example.com/api/v1/tenants

# TENANT JWT — 403 PLATFORM_AUDIENCE_REQUIRED.
curl -X POST \
  -H "Authorization: Bearer ${TENANT_JWT}" \
  -H "Content-Type: application/json" \
  -d '{"name": "x", "region": "US", ...}' \
  https://example.com/api/v1/tenants
```

### 7. Sample integration code

```typescript
import { z } from "zod";

const TenantCreate = z.object({
  name: z.string().min(1).max(200),
  region: z.enum(["US", "EU"]),
  tier: z.enum(["ENTERPRISE", "MID_MARKET", "SMB", "SINGLE_STORE"]),
  industry: z.enum([
    "CONVENIENCE_FUEL", "CONVENIENCE", "GROCERY", "HYPERMART",
    "SPECIALITY_GROCERY", "ORGANIC_GROCERY",
  ]),
  country: z.string().min(2).max(100),
  primary_contact_name: z.string().min(1).max(200),
  contact_email: z.string().email(),
  number_of_stores: z.number().int().positive(),
  number_of_stores_as_of_date: z.string(),  // YYYY-MM-DD
  display_code: z.string().max(64).optional(),
  monthly_revenue_usd: z.string().optional(),
  monthly_revenue_as_of_date: z.string().optional(),
  modules_enabled: z.array(z.string()).optional(),
});

async function createTenant(payload: z.infer<typeof TenantCreate>, jwt: string) {
  const resp = await fetch("/api/v1/tenants", {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${jwt}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(TenantCreate.parse(payload)),
  });
  if (resp.status === 409) throw new Error("Duplicate tenant name");
  if (!resp.ok) throw new Error(`Create failed: ${resp.status}`);
  return await resp.json();
}
```

### 8. Implementation reference

| File | Role |
|---|---|
| `src/admin_backend/routers/v1/tenants.py` | `create_tenant` handler |
| `src/admin_backend/repositories/tenants.py` | `TenantsRepo.create` + `_raise_if_name_taken` |
| `src/admin_backend/schemas/tenant.py` | `TenantCreateRequest` |
| `src/admin_backend/errors.py` | `DuplicateTenantNameError`, `PlatformAudienceRequiredError`, `PermissionDeniedError` |
| `tests/integration/test_tenants_writes_router.py` | C1-C9 tests |
| `tests/integration/test_tenants_repo_writes.py` | R-C1..R-C4 repo tests |

---

## `PATCH /api/v1/tenants/{tenant_id}`

### 1. Endpoint summary

- Method: `PATCH`
- Path: `/api/v1/tenants/{tenant_id}`
- Description: Partial update of a tenant's mutable fields. Status transitions go through `/suspend` and `/activate` — not this endpoint.
- Who can call: **Platform users only** (locked decision; multi-audience PATCH deferred post-6.16 per FN-AB). Same gate as `POST /api/v1/tenants`: `audience="PLATFORM"` + `ADMIN.TENANTS.CONFIGURE.GLOBAL`.

### 2. Request

- Auth: `Authorization: Bearer <PLATFORM JWT>`.
- Path: `tenant_id` — UUID.
- Body: subset of the create body's mutable fields. All optional. `region`, `status`, `id`, and audit columns are rejected by `extra="forbid"`.

Allowed fields: `name`, `display_code`, `country`, `tier`, `industry`, `primary_contact_name`, `contact_email`, `monthly_revenue_usd`, `monthly_revenue_as_of_date`, `number_of_stores`, `number_of_stores_as_of_date`.

### 3. Response 200

Full `TenantDetail` shape (post-update), with `updated_at` refreshed by the DB trigger and `updated_by_user_id` set from the JWT.

### 4. Response codes

| Status | Code | When |
|---|---|---|
| 200 | — | Updated. Response body is the post-update `TenantDetail`. |
| 401 | `AUTH_MISSING` / `AUTH_INVALID` | JWT missing or invalid. |
| 403 | `PLATFORM_AUDIENCE_REQUIRED` | Caller is a TENANT user. |
| 403 | `PERMISSION_DENIED` | PLATFORM caller lacks `ADMIN.TENANTS.CONFIGURE.GLOBAL`. |
| 404 | `TENANT_NOT_FOUND` | Row absent or RLS-filtered (per D-17). |
| 409 | `DUPLICATE_TENANT_NAME` | Rename target collides with another tenant. |
| 422 | `EMPTY_PATCH` | Body has no fields set. |
| 422 | (validation) | Disallowed field, malformed enum, etc. |

### 5. Behaviour notes

- Allowed in any non-TERMINATED state (including `SUSPENDED`).
- Rename-to-self (PATCH `name` to its current value) is a no-op success (the uniqueness check excludes `tenant_id`).
- `contact_email` is lowercased before UPDATE.
- The DDL trigger `tg_tenants_set_updated_at` refreshes `updated_at` automatically; the handler doesn't set it explicitly.
- Multi-audience PATCH (TENANT OWNER editing own tenant's operational fields) is deferred — blocked at the schema layer by Pattern (a) FKs on audit columns (per D-13). Tracked as FN-AB.

### 6. Example calls

```bash
# Update contact info.
curl -X PATCH \
  -H "Authorization: Bearer ${PLATFORM_JWT}" \
  -H "Content-Type: application/json" \
  -d '{"primary_contact_name": "Bob Operator"}' \
  https://example.com/api/v1/tenants/019e2747-5608-7235-a747-1490f9e3a971

# Empty body — 422 EMPTY_PATCH.
curl -X PATCH -H "Authorization: Bearer ${PLATFORM_JWT}" \
  -d '{}' https://example.com/api/v1/tenants/${id}
```

### 7. Sample integration code

```typescript
async function patchTenant(
  tenantId: string,
  fields: Partial<TenantUpdate>,
  jwt: string,
) {
  if (Object.keys(fields).length === 0) {
    throw new Error("PATCH must include at least one field");
  }
  const resp = await fetch(`/api/v1/tenants/${tenantId}`, {
    method: "PATCH",
    headers: {
      "Authorization": `Bearer ${jwt}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(fields),
  });
  if (resp.status === 404) return null;
  if (resp.status === 409) throw new Error("Duplicate tenant name");
  if (!resp.ok) throw new Error(`Patch failed: ${resp.status}`);
  return await resp.json();
}
```

### 8. Implementation reference

| File | Role |
|---|---|
| `src/admin_backend/routers/v1/tenants.py` | `patch_tenant` handler |
| `src/admin_backend/repositories/tenants.py` | `TenantsRepo.update` |
| `src/admin_backend/schemas/tenant.py` | `TenantPatchRequest` |
| `src/admin_backend/errors.py` | `EmptyPatchError`, `DuplicateTenantNameError`, `TenantNotFoundError` |
| `tests/integration/test_tenants_writes_router.py` | P1-P10 tests |
| `tests/integration/test_tenants_repo_writes.py` | R-U1..R-U4 repo tests |

---

## `POST /api/v1/tenants/{tenant_id}/suspend`

### 1. Endpoint summary

- Method: `POST`
- Path: `/api/v1/tenants/{tenant_id}/suspend`
- Description: Transition a tenant from `TRIAL` or `ACTIVE` to `SUSPENDED`. Populates `suspended_at` and `suspended_by_user_id` atomically with the `status` flip.
- Who can call: **Platform users with override authority.** Two-layer gate: `audience="PLATFORM"` + `ADMIN.TENANTS.OVERRIDE.GLOBAL` (held by `SUPER_ADMIN` only per Phase 3 seed).

### 2. Request

- Auth: `Authorization: Bearer <PLATFORM JWT>`.
- Path: `tenant_id` — UUID.
- Body: none.

### 3. Response 200

Full `TenantDetail` shape with `status="SUSPENDED"` and `suspended_at` / `suspended_by_user_id` populated.

### 4. Response codes

| Status | Code | When |
|---|---|---|
| 200 | — | Transition succeeded. Response body is the post-suspend `TenantDetail`. |
| 401 | `AUTH_MISSING` / `AUTH_INVALID` | JWT missing or invalid. |
| 403 | `PLATFORM_AUDIENCE_REQUIRED` | Caller is a TENANT user. |
| 403 | `PERMISSION_DENIED` | PLATFORM caller lacks `ADMIN.TENANTS.OVERRIDE.GLOBAL` (e.g., `PLATFORM_ADMIN` who only holds CONFIGURE.GLOBAL). |
| 404 | `TENANT_NOT_FOUND` | Row absent or RLS-filtered. |
| 409 | `INVALID_STATE_TRANSITION` | Current status is not `TRIAL` or `ACTIVE` (e.g., already `SUSPENDED`). |

### 5. Behaviour notes

- Allowed source states: `TRIAL` or `ACTIVE`. Any other source returns 409.
- `SELECT ... FOR UPDATE` inside the request transaction locks the row so concurrent suspend/activate calls don't race.
- `suspended_at` and `suspended_by_user_id` MUST be co-set when `status='SUSPENDED'` (DDL `ck_tenants_suspended_consistency`); the handler enforces atomically.

### 6. Example calls

```bash
curl -X POST \
  -H "Authorization: Bearer ${PLATFORM_JWT}" \
  https://example.com/api/v1/tenants/019e2747-5608-7235-a747-1490f9e3a971/suspend
```

### 7. Sample integration code

```typescript
async function suspendTenant(tenantId: string, jwt: string) {
  const resp = await fetch(`/api/v1/tenants/${tenantId}/suspend`, {
    method: "POST",
    headers: { "Authorization": `Bearer ${jwt}` },
  });
  if (resp.status === 409) throw new Error("Cannot suspend from current state");
  if (resp.status === 404) return null;
  if (!resp.ok) throw new Error(`Suspend failed: ${resp.status}`);
  return await resp.json();
}
```

### 8. Implementation reference

| File | Role |
|---|---|
| `src/admin_backend/routers/v1/tenants.py` | `suspend_tenant` handler |
| `src/admin_backend/repositories/tenants.py` | `TenantsRepo.transition` (target=`SUSPENDED`) |
| `src/admin_backend/errors.py` | `InvalidStateTransitionError`, `TenantNotFoundError` |
| `tests/integration/test_tenants_writes_router.py` | S1-S6 tests (S6 LOAD-BEARING: PLATFORM_ADMIN refusal) |
| `tests/integration/test_tenants_repo_writes.py` | R-T1, R-T2, R-T3 repo tests |

---

## `POST /api/v1/tenants/{tenant_id}/activate`

### 1. Endpoint summary

- Method: `POST`
- Path: `/api/v1/tenants/{tenant_id}/activate`
- Description: Transition a tenant from `TRIAL` or `SUSPENDED` to `ACTIVE`. When activating from `SUSPENDED`, clears `suspended_at` and `suspended_by_user_id` atomically. A SUSPENDED tenant never lands back in TRIAL.
- Who can call: **Platform users with override authority.** Same gate as `/suspend`: `audience="PLATFORM"` + `ADMIN.TENANTS.OVERRIDE.GLOBAL`.

### 2. Request

- Auth: `Authorization: Bearer <PLATFORM JWT>`.
- Path: `tenant_id` — UUID.
- Body: none.

### 3. Response 200

Full `TenantDetail` shape with `status="ACTIVE"`, `suspended_at=null`, and `suspended_by_user_id` cleared.

### 4. Response codes

| Status | Code | When |
|---|---|---|
| 200 | — | Transition succeeded. Response body is the post-activate `TenantDetail`. |
| 401 | `AUTH_MISSING` / `AUTH_INVALID` | JWT missing or invalid. |
| 403 | `PLATFORM_AUDIENCE_REQUIRED` | Caller is a TENANT user. |
| 403 | `PERMISSION_DENIED` | PLATFORM caller lacks `ADMIN.TENANTS.OVERRIDE.GLOBAL`. |
| 404 | `TENANT_NOT_FOUND` | Row absent or RLS-filtered. |
| 409 | `INVALID_STATE_TRANSITION` | Current status is not `TRIAL` or `SUSPENDED` (e.g., already `ACTIVE`). |

### 5. Behaviour notes

- Allowed source states: `TRIAL` or `SUSPENDED`.
- `SUSPENDED -> ACTIVE` clears `suspended_at` and `suspended_by_user_id` atomically with the status flip (`ck_tenants_suspended_consistency`).
- `TRIAL -> ACTIVE` is forward-only progression; no fields cleared.
- Operationally idempotent against re-activation: a second `/activate` on an already-ACTIVE tenant returns 409 rather than a silent no-op (callers should check current status before invoking, or treat 409 as "already there").

### 6. Example calls

```bash
curl -X POST \
  -H "Authorization: Bearer ${PLATFORM_JWT}" \
  https://example.com/api/v1/tenants/019e2747-5608-7235-a747-1490f9e3a971/activate
```

### 7. Sample integration code

```typescript
async function activateTenant(tenantId: string, jwt: string) {
  const resp = await fetch(`/api/v1/tenants/${tenantId}/activate`, {
    method: "POST",
    headers: { "Authorization": `Bearer ${jwt}` },
  });
  if (resp.status === 409) throw new Error("Cannot activate from current state");
  if (resp.status === 404) return null;
  if (!resp.ok) throw new Error(`Activate failed: ${resp.status}`);
  return await resp.json();
}
```

### 8. Implementation reference

| File | Role |
|---|---|
| `src/admin_backend/routers/v1/tenants.py` | `activate_tenant` handler |
| `src/admin_backend/repositories/tenants.py` | `TenantsRepo.transition` (target=`ACTIVE`) |
| `src/admin_backend/errors.py` | `InvalidStateTransitionError`, `TenantNotFoundError` |
| `tests/integration/test_tenants_writes_router.py` | A1-A5 tests |
| `tests/integration/test_tenants_repo_writes.py` | R-T4, R-T5, R-T6, R-T8 repo tests |

---

## Cross-references

- `docs/architecture.md` — multi-tenancy enforcement (RLS), Layer 1 policy clauses (D-29), request flow.
- `db/raw_ddl/Ithina_postgres_SQL_DDL_tenants_v3.sql` — source of truth for the underlying schema.
- `CLAUDE.md` — D-17 (RLS-blocked -> 404), D-24 (AuthContext -> tenant context), D-28 (response shape defaults), D-29 (PLATFORM RLS visibility), D-30 (response envelope), D-31 (field-meaning lock), FN-AB-16 (module stub cleanup).

## What this document is NOT

- **Not the OpenAPI spec.** The OpenAPI spec is auto-generated by FastAPI at `/api/v1/openapi.json` and is the machine-readable source of truth (TypeScript codegen, Postman imports, etc.). This file is the human-readable companion: behaviour notes, edge cases, and integration intent that don't fit cleanly into OpenAPI.
- **Not exhaustive of every internal detail.** Implementation reference points to source files for engineers who want depth.
