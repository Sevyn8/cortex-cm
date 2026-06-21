# RBAC endpoints (Roles, Permissions, Permission Matrix)

Canonical endpoint documentation for the Roles & Permissions page (Frontend spec 7.5). Four GET endpoints across three URL prefixes — `/roles`, `/permissions`, `/permission-matrix`. Format follows CLAUDE.md "Per-endpoint documentation" — eight fixed sections per endpoint. Mirrors `tenant-users.md`'s structure; resource-specific additions are **app-layer audience filtering** (the audience filter substitutes for RLS on the platform-global RBAC tables) and three **deliberate D-30 exceptions** (E1's pre-grouped envelope, E3's parent-echo, E6's render-ready matrix).

| Endpoint | Description | Calling user types |
|---|---|---|
| `GET /api/v1/roles` | E1 — list roles, pre-grouped by audience | PLATFORM (sees both blocks); TENANT (`platform_roles` always empty) |
| `GET /api/v1/permissions` | E2 — flat permission catalogue | Both — catalogue is reference data, no audience filter |
| `GET /api/v1/roles/{role_id}/permissions` | E3 — permissions granted by a role, with parent-echo | Both, audience-gated by role; TENANT requesting a PLATFORM role -> 404 |
| `GET /api/v1/permission-matrix` | E6 — render-ready permission × role matrix | PLATFORM (full grid); TENANT (TENANT-audience role columns only) |

Cross-cutting:

- **Auth** — `Authorization: Bearer <jwt>` required; missing or invalid -> 401.
- **No PLATFORM-only gate.** All four endpoints accept both user types; visibility is enforced via the audience filter at the app layer for `roles` / `role_permissions` (the tables have no RLS — they are platform-global) and via reference-data semantics for `permissions` (the catalogue is open). Distinct from RLS scoping, but the same anti-information-disclosure intent.
- **Cross-audience role lookups return 404, not 403.** A TENANT JWT requesting a PLATFORM-audience role's id (E3) receives 404 (`ROLE_NOT_FOUND`). Returning 403 would disclose existence. The load-bearing test `test_rp3_tenant_jwt_platform_role_returns_404` proves the audience gate works end-to-end.
- **Three deliberate D-30 exceptions.**
  - **E1** is pre-grouped (`platform_roles` + `tenant_roles` blocks) — the pre-grouped shape doesn't compose with cross-group pagination.
  - **E3** echoes the parent role identity at the top level (`role_id`, `role_name`); a single-resource sub-resource has nowhere pagination would belong.
  - **E6** is render-ready (`roles` column array + `rows` with position-aligned `cells[]`); the matrix is one shape, returned in full.
  - **E2** follows D-30 normally (`{items, pagination}`).
- **Field semantics** — append-only per D-31. Once a field's meaning ships, it stays. New variants get new field names.
- **Hidden fields.** Three Pattern (b) audit-actor pairs on `roles` (`created_by_user_id`+`created_by_user_type`, `updated_by_user_id`+`updated_by_user_type`, `archived_by_user_id`+`archived_by_user_type`) are NOT in any response body. The `audience` enum on `roles` is intentionally absent from E1's items (implied by the container key `platform_roles` vs `tenant_roles`); it IS present on E6's role-column entries (the matrix UI labels columns by audience).
- **Error envelope** — `{code, message, details, request_id}` on all server-generated errors. `details` is `null` in v0.
- **`X-Request-Id`** — set on every response by the audit middleware; same UUID appears in the per-request log line.
- **RBAC enforcement (write-time invariants AI-RBAC-01..06).** Not enforced in v0; the catalogue is reference data populated by Ithina platform admins via migration. v0 ships read-only. Future write surfaces (FN-AB-12) land per-step.

---

## `GET /api/v1/roles`  (E1)

List roles, pre-grouped by audience.

### 1. Endpoint summary

- **Method:** `GET`
- **Path:** `/api/v1/roles`
- **Description:** Returns the role catalogue as two blocks (`platform_roles`, `tenant_roles`), each with `items` and `total`. PLATFORM JWTs see both blocks populated; TENANT JWTs see `platform_roles` always `{items: [], total: 0}`.
- **Who can call:** any authenticated user. PLATFORM sees both audiences; TENANT sees only TENANT-audience rows.

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
| `status` | string | `ACTIVE` | One of `ACTIVE`, `INACTIVE`, `ARCHIVED`. Filter applied within each block. |
| `is_system` | bool | (none) | Filter by Ithina-system flag. `true` = roles shipped by Ithina that should not be deleted (e.g., `SUPER_ADMIN`). |
| `q` | string | (none) | Trimmed; if length 0 after trim, treated as no filter. ILIKE substring match against `name`, `code`, `description`. |
| `sort` | string | `name_asc` | One of: `name_asc`, `name_desc`, `created_at_asc`, `created_at_desc`. Sort applies within each block. Unknown -> 400 with `code: "INVALID_SORT_KEY"`. |
| `offset` | int | `0` | `>= 0`. Present for consistency; v0 catalogue fits in one page. |
| `limit` | int | `50` | `>= 1`, `<= 200`. Above the cap -> 422. |

**Request body:** none.

### 3. Response 200

```json
{
  "platform_roles": {
    "items": [
      {
        "id": "94340a03-3f07-4814-91d6-3f78e3e9de99",
        "name": "Platform Admin",
        "code": "PLATFORM_ADMIN",
        "description": "Create/manage tenants and platform users",
        "status": "ACTIVE",
        "is_system": true,
        "user_count": 1,
        "created_at": "2026-04-19T15:00:00+00:00",
        "updated_at": "2026-04-19T15:00:00+00:00"
      }
    ],
    "total": 3
  },
  "tenant_roles": {
    "items": [
      {
        "id": "...",
        "name": "Associate",
        "code": "ASSOCIATE",
        "description": "Frontline floor staff",
        "status": "ACTIVE",
        "is_system": true,
        "user_count": 2,
        "created_at": "2026-04-19T15:00:00+00:00",
        "updated_at": "2026-04-19T15:00:00+00:00"
      }
    ],
    "total": 12
  }
}
```

**Item field reference:**

| Field | Type | Nullable | Notes |
|---|---|---|---|
| `id` | UUID string | No | UUIDv7 from DB DEFAULT |
| `name` | string | No | Display name (1-100 chars) |
| `code` | string | No | Stable wire-code, `^[A-Z][A-Z0-9_]{1,49}$` |
| `description` | string | Yes | One-line role description |
| `status` | enum string | No | `ACTIVE`, `INACTIVE`, or `ARCHIVED` |
| `is_system` | bool | No | True for roles Ithina ships that should not be deleted |
| `user_count` | int | No | Active assignments referencing this role. Counted as the SUM of two correlated scalar subqueries (one per physical assignment table) where `status='ACTIVE'` — `platform_user_role_assignments` for PLATFORM-audience roles and `tenant_user_role_assignments` for TENANT-audience roles. The audience-check triggers guarantee a role's assignments live in exactly one of the two tables, so one branch always contributes 0 per role. RLS-scoped on the tenant-side branch for TENANT JWTs (count reflects only the caller's tenant). For PLATFORM JWTs, the tenant-side branch spans all tenants via the D-29 unconditional OR-branch. |
| `created_at` | ISO 8601 with offset | No | When the row was inserted |
| `updated_at` | ISO 8601 with offset | No | Most recent update on any field |

**Hidden by design:** `audience` (implied by the container key), `created_by_user_id`, `created_by_user_type`, `updated_by_user_id`, `updated_by_user_type`, `archived_at`, `archived_by_user_id`, `archived_by_user_type`.

**Block field reference:**

| Field | Type | Notes |
|---|---|---|
| `items` | array | Roles in the block, sorted per `sort` |
| `total` | int | Number of items in the block, after audience and status filters |

### 4. Response codes

| Code | When | Body |
|---|---|---|
| 200 | Success | Body as above |
| 400 | Unknown `sort` key | `{code: "INVALID_SORT_KEY", message: "Invalid sort key", details: null, request_id}` |
| 401 | Missing or invalid JWT | `{code: "AUTH_MISSING" \| "AUTH_INVALID", message, details: null, request_id}` |
| 422 | Query-param validation failure | FastAPI default validation envelope |
| 500 | Internal server error | `{code: "INTERNAL_ERROR", message: "An internal error occurred", details: null, request_id}` |

### 5. Behaviour notes

- **Audience filter at the app layer.** `roles` has no RLS (per the DDL's "platform-global, no RLS" note). Visibility is gated in the router via `_audience_filter_for(auth)`. TENANT JWTs see only `audience='TENANT'`. PLATFORM JWTs see both. The pattern is captured in CLAUDE.md "Audience filtering for non-RLS tables" convention note.
- **`user_count` correlates per-row via `.correlate(Role)` on EACH UNION branch.** Without that, the count collapses to a platform-wide aggregate. Same trap as Step 3.3 L9 / Step 5.3 L11 / Step 6.1 R4 — third occurrence on the same pattern, this time on TWO subqueries instead of one. The load-bearing test is `test_r4_user_count_aggregate_correlates_per_role`.
- **`user_count` for TENANT JWTs is RLS-scoped on the tenant-side branch.** Post Step 6.8.1 split (D-34), assignments live in two physical tables. `tenant_user_role_assignments` carries RLS+FORCE with the unconditional D-29 OR-branch; the tenant-side subquery inherits the request's session GUCs, so the count for a TENANT JWT reflects only the calling tenant's assignments. `platform_user_role_assignments` has no RLS — the platform-side subquery contributes 0 for every TENANT-audience role by audience-check trigger guarantee.
- **No top-level pagination block.** The pre-grouped shape doesn't compose with cross-group pagination; per-block pagination wasn't a v0 requirement (the catalogue fits in one page). `offset`/`limit` apply within each block.
- **Empty result.** `{items: [], total: 0}` per block, status 200.

### 6. Example calls

```bash
# Default — both blocks, ACTIVE only, sorted name_asc.
curl -s -H "Authorization: Bearer $JWT" \
  "https://admin-dev.ithina.com/api/v1/roles"

# TENANT JWT — platform_roles always empty.
curl -s -H "Authorization: Bearer $TENANT_JWT" \
  "https://admin-dev.ithina.com/api/v1/roles"

# Filter by status, search across name/code/description.
curl -s -H "Authorization: Bearer $JWT" \
  "https://admin-dev.ithina.com/api/v1/roles?status=ACTIVE&q=admin"

# Only Ithina-system roles.
curl -s -H "Authorization: Bearer $JWT" \
  "https://admin-dev.ithina.com/api/v1/roles?is_system=true"
```

### 7. Sample integration code

```typescript
type RoleStatus = "ACTIVE" | "INACTIVE" | "ARCHIVED";

type RoleListItem = {
  id: string;
  name: string;
  code: string;
  description: string | null;
  status: RoleStatus;
  is_system: boolean;
  user_count: number;
  created_at: string;
  updated_at: string;
};

type AudienceBlock = { items: RoleListItem[]; total: number };

type RoleListResponse = {
  platform_roles: AudienceBlock;
  tenant_roles: AudienceBlock;
};

async function listRoles(jwt: string): Promise<RoleListResponse> {
  const r = await fetch("https://admin-dev.ithina.com/api/v1/roles", {
    headers: { Authorization: `Bearer ${jwt}` },
  });
  if (!r.ok) throw new Error(`${r.status}`);
  return r.json();
}

// Render with section headers; suppress empty platform_roles for TENANT users.
const data = await listRoles(jwt);
if (data.platform_roles.total > 0) {
  // <h3>Platform Roles</h3>
  data.platform_roles.items.forEach(r => /* row(r) */);
}
// <h3>Tenant Roles</h3>
data.tenant_roles.items.forEach(r => /* row(r) */);
```

### 8. Implementation reference

| File | Role |
|---|---|
| `src/admin_backend/routers/v1/rbac.py` | `list_roles` handler + `_audience_filter_for` helper |
| `src/admin_backend/repositories/roles.py` | `RolesRepo.list_grouped` + `_user_count_subquery` |
| `src/admin_backend/repositories/_errors.py` | `InvalidSortKeyError` (shared) |
| `src/admin_backend/errors.py` | `InvalidSortKeyClientError` (400, shared) |
| `src/admin_backend/schemas/role.py` | `RoleListItem`, `AudienceBlock`, `RoleListResponse` |
| `src/admin_backend/models/role.py` | `Role` ORM model + `RoleAudience`, `RoleStatus` enums |
| `tests/integration/test_rbac_router.py` | R1-R8 (R2/R4 load-bearing) |

---

## `GET /api/v1/permissions`  (E2)

Flat permission catalogue.

### 1. Endpoint summary

- **Method:** `GET`
- **Path:** `/api/v1/permissions`
- **Description:** Returns the canonical (module, resource, action, scope) tuple catalogue.
- **Who can call:** any authenticated user. Both user types see all rows — the catalogue is reference data.

### 2. Request

**Headers:** Authorization required.

**Path parameters:** none.

**Query parameters:**

| Param | Type | Default | Validation |
|---|---|---|---|
| `module` | string | (none) | One of `ADMIN`, `PRICING_OS`, `PERISHABLES_ASSISTANT`, `PROMOTIONS_ASSISTANT` (post Step 6.1 vocabulary) |
| `scope` | string | (none) | One of `GLOBAL`, `TENANT`, `STORE` (post Step 6.1 vocabulary) |
| `sort` | string | `module_asc` | One of: `module_asc` (compound module/resource/action/scope ASC), `code_asc`, `code_desc`. Unknown -> 400 `INVALID_SORT_KEY`. |
| `offset` | int | `0` | `>= 0` |
| `limit` | int | `100` | `>= 1`, `<= 200` |

**Request body:** none.

### 3. Response 200

```json
{
  "items": [
    {
      "id": "5a8aaeca-1a50-4ec3-aabc-25fa3fe12e47",
      "module": "ADMIN",
      "resource": "USERS",
      "action": "CONFIGURE",
      "scope": "TENANT",
      "code": "ADMIN.USERS.CONFIGURE.TENANT",
      "description": "Invite, suspend, and reactivate users within a tenant",
      "created_at": "2026-04-19T15:00:00+00:00",
      "updated_at": "2026-04-19T15:00:00+00:00"
    }
  ],
  "pagination": { "total": 23, "offset": 0, "limit": 100 }
}
```

**Field reference:**

| Field | Type | Nullable | Notes |
|---|---|---|---|
| `id` | UUID string | No | UUIDv7 from DB DEFAULT |
| `module` | enum string | No | One of the locked module values |
| `resource` | enum string | No | One of the locked resource values |
| `action` | enum string | No | One of the locked action values |
| `scope` | enum string | No | One of the locked scope values |
| `code` | string | No | Derived: `module.resource.action.scope`; UNIQUE; matches `^[A-Z_]+\.[A-Z_]+\.[A-Z_]+\.[A-Z_]+$` |
| `description` | string | Yes | One-line permission description |
| `created_at` / `updated_at` | ISO 8601 with offset | No | DB-managed |

**No display labels on E2** — labels live in E6's render-ready response only. E2 consumers (admin-side references) render via the codes directly.

### 4. Response codes

| Code | When | Body |
|---|---|---|
| 200 | Success | Body as above |
| 400 | Unknown `sort` key | `{code: "INVALID_SORT_KEY", ...}` |
| 401 | Missing or invalid JWT | `{code: "AUTH_MISSING" \| "AUTH_INVALID", ...}` |
| 422 | Query-param validation failure | FastAPI envelope |
| 500 | Internal server error | `{code: "INTERNAL_ERROR", ...}` |

### 5. Behaviour notes

- **Reference data, no audience filter.** Both user types see the same rows.
- **Default sort `module_asc` is a compound sort** (module/resource/action/scope ASC); rows with the same module are clustered together, matching matrix render order from E6.
- **Postgres sorts enum columns by enum ordinal**, not string-alphabetic. The DDL's enum declaration order is the sort order. (Translation tables for ordinal -> alphabetic live in the matrix UI; the catalogue itself is enum-ordinal-ordered.)
- **Total post Step 6.1 vocabulary cleanup:** `permissions` carries the post-cleanup row count (the legacy `MARKDOWNS.APPROVE.REGION` was removed by the cleanup migration). New permissions land via Ithina staff migration; the catalogue is platform-global.

### 6. Example calls

```bash
# Full catalogue (default sort).
curl -s -H "Authorization: Bearer $JWT" \
  "https://admin-dev.ithina.com/api/v1/permissions"

# Filter to one module.
curl -s -H "Authorization: Bearer $JWT" \
  "https://admin-dev.ithina.com/api/v1/permissions?module=ADMIN"

# All GLOBAL-scope permissions.
curl -s -H "Authorization: Bearer $JWT" \
  "https://admin-dev.ithina.com/api/v1/permissions?scope=GLOBAL"
```

### 7. Sample integration code

```typescript
type PermissionRead = {
  id: string;
  module: string;
  resource: string;
  action: string;
  scope: string;
  code: string;
  description: string | null;
  created_at: string;
  updated_at: string;
};
type PermissionListResponse = {
  items: PermissionRead[];
  pagination: { total: number; offset: number; limit: number };
};
```

### 8. Implementation reference

| File | Role |
|---|---|
| `src/admin_backend/routers/v1/rbac.py` | `list_permissions` handler |
| `src/admin_backend/repositories/permissions.py` | `PermissionsRepo.list` + `SORT_MAP` |
| `src/admin_backend/schemas/permission.py` | `PermissionRead`, `PermissionListResponse` |
| `src/admin_backend/models/permission.py` | `Permission` ORM model + the four enum classes |
| `tests/integration/test_rbac_router.py` | P1-P4 |

---

## `GET /api/v1/roles/{role_id}/permissions`  (E3)

Permissions granted by a role, with parent-echo envelope.

### 1. Endpoint summary

- **Method:** `GET`
- **Path:** `/api/v1/roles/{role_id}/permissions`
- **Description:** Returns the role's granted permissions plus the parent role's id and name. No pagination — a role has bounded permissions (typically 5-30).
- **Who can call:** any authenticated user. The role lookup is audience-gated: TENANT JWTs requesting a PLATFORM-audience role's id receive 404 `ROLE_NOT_FOUND`.

### 2. Request

**Headers:** Authorization required.

**Path parameters:**

| Param | Type | Notes |
|---|---|---|
| `role_id` | UUID string | Role identifier; FastAPI validates the shape. Malformed -> 422. |

**Query parameters:** none.

**Request body:** none.

### 3. Response 200

```json
{
  "role_id": "90b2b633-956b-4c0c-a849-9b926b5252e3",
  "role_name": "Owner",
  "items": [
    {
      "id": "5a8aaeca-1a50-4ec3-aabc-25fa3fe12e47",
      "module": "ADMIN",
      "resource": "USERS",
      "action": "CONFIGURE",
      "scope": "TENANT",
      "code": "ADMIN.USERS.CONFIGURE.TENANT",
      "description": "Invite, suspend, and reactivate users within a tenant",
      "created_at": "2026-04-19T15:00:00+00:00",
      "updated_at": "2026-04-19T15:00:00+00:00"
    }
  ]
}
```

**Field reference:**

| Field | Type | Nullable | Notes |
|---|---|---|---|
| `role_id` | UUID string | No | Echo of the path parameter — frontend race-condition guard |
| `role_name` | string | No | Display name; saves a cross-lookup against E1's cached list |
| `items` | array of permissions | No | Same shape as E2's items; sorted module/resource/action/scope ASC |

### 4. Response codes

| Code | When | Body |
|---|---|---|
| 200 | Success | Body as above |
| 401 | Missing or invalid JWT | `{code: "AUTH_MISSING" \| "AUTH_INVALID", ...}` |
| 404 | Role does not exist OR caller is audience-gated out of seeing it | `{code: "ROLE_NOT_FOUND", message: "Role not found", ...}` |
| 422 | Malformed `role_id` UUID | FastAPI envelope |
| 500 | Internal server error | `{code: "INTERNAL_ERROR", ...}` |

### 5. Behaviour notes

- **Audience-gated 404.** TENANT JWTs requesting a PLATFORM-audience role's id receive 404. Distinguishing "doesn't exist" from "you can't see it" would leak existence. Same anti-information-disclosure intent as RLS-as-404 per D-17. The load-bearing test is `test_rp3_tenant_jwt_platform_role_returns_404`.
- **No pagination.** Bounded result; a role grants ~5-30 permissions in practice.
- **`role_name` echo** lets the frontend render the right-pane header without an additional E1 lookup.

### 6. Example calls

```bash
# Owner role's permissions.
curl -s -H "Authorization: Bearer $JWT" \
  "https://admin-dev.ithina.com/api/v1/roles/90b2b633-956b-4c0c-a849-9b926b5252e3/permissions"

# TENANT JWT to a PLATFORM role's id -> 404.
curl -s -H "Authorization: Bearer $TENANT_JWT" \
  "https://admin-dev.ithina.com/api/v1/roles/<super_admin_id>/permissions"
# {"code": "ROLE_NOT_FOUND", ...}
```

### 7. Sample integration code

```typescript
type RolePermissionsResponse = {
  role_id: string;
  role_name: string;
  items: PermissionRead[];
};

async function listRolePermissions(jwt: string, roleId: string) {
  const r = await fetch(
    `https://admin-dev.ithina.com/api/v1/roles/${roleId}/permissions`,
    { headers: { Authorization: `Bearer ${jwt}` } },
  );
  if (r.status === 404) return null;
  if (!r.ok) throw new Error(`${r.status}`);
  return (await r.json()) as RolePermissionsResponse;
}
```

### 8. Implementation reference

| File | Role |
|---|---|
| `src/admin_backend/routers/v1/rbac.py` | `list_role_permissions` handler + `RoleNotFoundError` |
| `src/admin_backend/repositories/roles.py` | `RolesRepo.get_by_id` + `RolesRepo.list_permissions_for_role` |
| `src/admin_backend/schemas/permission.py` | `RolePermissionsResponse` |
| `src/admin_backend/models/role_permission.py` | `RolePermission` ORM model |
| `tests/integration/test_rbac_router.py` | RP1-RP3 (RP3 load-bearing) |

---

## `GET /api/v1/permission-matrix`  (E6)

Render-ready permission × role matrix.

### 1. Endpoint summary

- **Method:** `GET`
- **Path:** `/api/v1/permission-matrix`
- **Description:** Returns the full role × permission grid for the Roles & Permissions matrix tab (Frontend spec 7.5.4). Cells are boolean grant flags, position-aligned with the `roles[]` column array.
- **Who can call:** any authenticated user. PLATFORM JWTs see the full grid (15 columns at v0 baseline); TENANT JWTs see TENANT-audience role columns only (12 columns at v0 baseline); `cells[]` arrays shrink correspondingly.

### 2. Request

**Headers:** Authorization required.

**Path parameters:** none.

**Query parameters:** none.

**Request body:** none.

### 3. Response 200

```json
{
  "roles": [
    { "id": "94340a03-...", "name": "Platform Admin", "audience": "PLATFORM" },
    { "id": "f10c718b-...", "name": "Super Admin",    "audience": "PLATFORM" },
    { "id": "14fcdd54-...", "name": "Support Admin",  "audience": "PLATFORM" },
    { "id": "...",          "name": "Associate",      "audience": "TENANT" }
  ],
  "rows": [
    {
      "id": "4d71c366-...",
      "module": "PRICING_OS",
      "module_label": "Pricing OS",
      "resource": "PRICING_RULES",
      "resource_label": "Pricing Rules",
      "action": "VIEW",
      "action_label": "View",
      "scope": "TENANT",
      "scope_label": "Tenant",
      "cells": [true, true, false, false, true, true, true, true, false, true, false, false, false, false, false]
    }
  ]
}
```

**Top-level field reference:**

| Field | Type | Notes |
|---|---|---|
| `roles` | array of role-column entries | Ordered audience_asc, name_asc. PLATFORM columns first, alphabetical within. TENANT JWTs see only `audience='TENANT'` columns. |
| `rows` | array of permission rows | Ordered module/resource/action/scope ASC. Same dataset for both user types — the catalogue is reference data. |

**Role-column field reference:**

| Field | Type | Notes |
|---|---|---|
| `id` | UUID string | Role id |
| `name` | string | Display name (used as column header) |
| `audience` | enum string | `PLATFORM` or `TENANT` |

**Row field reference:**

| Field | Type | Notes |
|---|---|---|
| `id` | UUID string | Permission id |
| `module` / `module_label` | string | Enum code + display label resolved from `lookups` |
| `resource` / `resource_label` | string | ditto |
| `action` / `action_label` | string | ditto |
| `scope` / `scope_label` | string | ditto |
| `cells` | array of bool | Position-based grant array. `cells[i]` is the grant state of this permission for `roles[i]`. **`len(cells) == len(roles)` is a hard invariant.** |

**Locked invariants (M1-M8):**

| # | Invariant |
|---|---|
| M1 | `cells[i]` is the grant state for `roles[i]` (position-based join, not key lookup) |
| M2 | `len(row.cells) == len(roles)` for every row |
| M3 | `roles` ordered audience_asc, name_asc |
| M4 | `rows` ordered module/resource/action/scope ASC (enum-ordinal, not string-alphabetic) |
| M5 | TENANT JWT response: `roles` filtered to `audience='TENANT'`; `cells[]` shrinks correspondingly |
| M6 | Each row carries 4 enum codes + 4 display labels |
| M7 | Display labels come from JOIN against `lookups` (list_names: `module`, `resource`, `permission_action`, `permission_scope`) |
| M8 | No pagination, no filters — the matrix is one shape, returned in full |

### 4. Response codes

| Code | When | Body |
|---|---|---|
| 200 | Success | Body as above |
| 401 | Missing or invalid JWT | `{code: "AUTH_MISSING" \| "AUTH_INVALID", ...}` |
| 500 | Internal server error | `{code: "INTERNAL_ERROR", ...}` |

### 5. Behaviour notes

- **Position-based cell alignment.** The frontend iterates `roles[]` for headers and `rows[]` for body; for each row, `cells[i]` is rendered under `roles[i]`. No id-keyed lookup needed. The load-bearing alignment test is `test_m2_cells_aligned_with_roles_array`.
- **TENANT response shrinkage.** TENANT JWTs see only TENANT-audience role columns; `roles` and every row's `cells[]` array are correspondingly shorter. The load-bearing test is `test_m3_tenant_jwt_filters_role_columns`.
- **Display labels via four LEFT JOINs.** The repo joins `permissions` against `lookups` four times (one alias per slot). If a lookup row is missing, the label falls back to the enum code via COALESCE — defensive; missing labels should not break matrix render.
- **Enum-ordinal sort.** Postgres orders enum columns by enum ordinal (declaration order in the DDL), not string-alphabetic. Frontend can re-sort client-side if a different order is preferred for the matrix UI.
- **No pagination, no filters.** The matrix is a single shape returned in full. Catalogue size is bounded.

### 6. Example calls

```bash
# Full grid as PLATFORM.
curl -s -H "Authorization: Bearer $JWT" \
  "https://admin-dev.ithina.com/api/v1/permission-matrix"

# TENANT-audience-only columns.
curl -s -H "Authorization: Bearer $TENANT_JWT" \
  "https://admin-dev.ithina.com/api/v1/permission-matrix"

# Row count + column count + cells width sanity-check.
curl -s -H "Authorization: Bearer $JWT" \
  "https://admin-dev.ithina.com/api/v1/permission-matrix" \
  | jq '.roles | length, .rows | length, .rows[0].cells | length'
```

### 7. Sample integration code

```ts
// Permission matrix render — direct iteration, no lookups.
const matrix = await fetch('/api/v1/permission-matrix', {
  headers: { Authorization: `Bearer ${jwt}` },
}).then(r => r.json());

// Headers: one <th> per role column.
{matrix.roles.map(role => <th key={role.id}>{role.name}</th>)}

// Body: one <tr> per permission row.
{matrix.rows.map(row => (
  <tr key={row.id}>
    <td>
      <div>{row.resource_label}</div>
      <div>
        {row.module_label}{' '}
        <Chip color={chipColorFor(row.action)}>{row.action_label}</Chip>
        {' · '}{row.scope_label}
      </div>
    </td>
    {row.cells.map((checked, i) => (
      <td key={matrix.roles[i].id}>
        <Checkbox checked={checked} disabled />
      </td>
    ))}
  </tr>
))}
```

### 8. Implementation reference

| File | Role |
|---|---|
| `src/admin_backend/routers/v1/rbac.py` | `get_permission_matrix` handler |
| `src/admin_backend/repositories/permission_matrix.py` | `PermissionMatrixRepo.get_matrix` + `_load_permissions_with_labels` |
| `src/admin_backend/schemas/permission.py` | `PermissionMatrixResponse`, `PermissionMatrixRow`, `PermissionMatrixRoleColumn` |
| `migrations/versions/22ccfb193cff_step_6_1_lookups_for_permissions.py` | 25-row lookups seed feeding the display labels |
| `tests/integration/test_rbac_router.py` | M1-M5 (M2/M3 load-bearing) |

---

## `GET /api/v1/roles/{role_id}`  (E7)

Self-contained role detail for the edit screen (Step 6.18.2).

### 1. Endpoint summary

- **Method:** `GET`
- **Path:** `/api/v1/roles/{role_id}`
- **Description:** Returns role metadata plus held permissions and grantable permissions (catalogue minus held) for the role-edit screen. Both lists carry display labels resolved server-side. TENANT-audience roles see `available_permissions` with `scope='GLOBAL'` rows excluded (audience-scope coherence per LD2).
- **Who can call:** any authenticated user. PLATFORM sees both audiences; TENANT JWT requesting a PLATFORM-audience role's id receives 404 (audience filter at the app layer; same anti-information-disclosure intent as RLS-as-404 per D-17).

### 2. Request

**Headers:**

| Header | Required | Notes |
|---|---|---|
| `Authorization` | Yes | `Bearer <jwt>` (PLATFORM or TENANT) |
| `Accept` | No | Defaults to `application/json` |

**Path parameters:**

| Param | Type | Notes |
|---|---|---|
| `role_id` | UUID | The role's id. Malformed UUID -> 422. |

**Query parameters:** none.

**Request body:** none.

### 3. Response 200

```json
{
  "id": "94340a03-3f07-4814-91d6-3f78e3e9de99",
  "name": "Platform Admin",
  "code": "PLATFORM_ADMIN",
  "description": "Create/manage tenants and platform users",
  "audience": "PLATFORM",
  "status": "ACTIVE",
  "is_system": true,
  "user_count": 1,
  "created_at": "2026-04-19T15:00:00+00:00",
  "updated_at": "2026-04-19T15:00:00+00:00",
  "permissions": [
    {
      "id": "5a8aaeca-1a50-4ec3-aabc-25fa3fe12e47",
      "module": "ADMIN",
      "module_label": "Admin",
      "resource": "USERS",
      "resource_label": "Users",
      "action": "VIEW",
      "action_label": "View",
      "scope": "GLOBAL",
      "scope_label": "Global",
      "code": "ADMIN.USERS.VIEW.GLOBAL",
      "description": "List and view all users across all tenants"
    }
  ],
  "available_permissions": [
    {
      "id": "...",
      "module": "ADMIN",
      "module_label": "Admin",
      "resource": "TENANTS",
      "resource_label": "Tenants",
      "action": "CONFIGURE",
      "action_label": "Configure",
      "scope": "GLOBAL",
      "scope_label": "Global",
      "code": "ADMIN.TENANTS.CONFIGURE.GLOBAL",
      "description": "Provision and decommission tenants"
    }
  ]
}
```

**Top-level field reference:**

| Field | Type | Nullable | Notes |
|---|---|---|---|
| `id` | UUID string | No | UUIDv7 from DB DEFAULT |
| `name` | string | No | Display name |
| `code` | string | No | Stable wire-code, `^[A-Z][A-Z0-9_]{1,49}$` |
| `description` | string | Yes | One-line role description |
| `audience` | enum string | No | `PLATFORM` or `TENANT` — drives the GLOBAL-scope filter on `available_permissions` per LD2 |
| `status` | enum string | No | `ACTIVE`, `INACTIVE`, or `ARCHIVED` |
| `is_system` | bool | No | Ithina-shipped roles that should not be deleted |
| `user_count` | int | No | Same definition as E1's field — active assignments referencing this role |
| `created_at` | ISO 8601 with offset | No | When the row was inserted |
| `updated_at` | ISO 8601 with offset | No | Most recent update on any field |
| `permissions` | array of `PermissionDetail` | No | Permissions currently granted to this role |
| `available_permissions` | array of `PermissionDetail` | No | Permissions grantable but not held. TENANT roles exclude `scope='GLOBAL'` |

**PermissionDetail field reference:**

| Field | Type | Nullable | Notes |
|---|---|---|---|
| `id` | UUID string | No | Permission id |
| `module` / `module_label` | string | No | Enum code + display name from `lookups` |
| `resource` / `resource_label` | string | No | ditto |
| `action` / `action_label` | string | No | ditto |
| `scope` / `scope_label` | string | No | ditto (`GLOBAL`, `TENANT`, or `STORE`) |
| `code` | string | No | Canonical `module.resource.action.scope` tuple |
| `description` | string | Yes | One-line description |

**Hidden by design:** all 3 Pattern (b) audit-actor pairs on `roles` (same hide-policy as E1).

### 4. Response codes

| Code | When | Body |
|---|---|---|
| 200 | Success | Body as above |
| 401 | Missing or invalid JWT | `{code: "AUTH_MISSING" \| "AUTH_INVALID", ...}` |
| 404 | Role does not exist OR caller is audience-gated out of seeing it | `{code: "ROLE_NOT_FOUND", message: "Role not found", details: null, request_id}` |
| 422 | Malformed `role_id` UUID | FastAPI envelope |
| 500 | Internal server error | `{code: "INTERNAL_ERROR", ...}` |

### 5. Behaviour notes

- **Audience-gated 404 (LD5).** TENANT JWTs requesting a PLATFORM-audience role's id receive 404 ROLE_NOT_FOUND. Distinguishing "doesn't exist" from "you can't see it" would leak existence. Same anti-information-disclosure intent as RLS-as-404 per D-17. The load-bearing test is `test_d3_tenant_jwt_platform_role_returns_404`.
- **Audience-scope coherence (LD2).** TENANT-audience roles cannot hold `scope='GLOBAL'` permissions structurally. The repo applies `WHERE scope != 'GLOBAL'` to the available_permissions query when `role.audience='TENANT'`. PLATFORM-audience roles see the full catalogue minus held (no scope filter). Load-bearing tests `test_d6_platform_role_available_includes_global` and `test_d7_tenant_role_available_excludes_global`.
- **Server-side label resolution (LD4).** Four LEFT JOINs against `core.lookups` (one per enum slot: `module_code`, `resource`, `permission_action`, `permission_scope`). COALESCE to the enum code if a lookup row is missing (defensive — every seeded row resolves; fallback path is unreachable in v0). Mirrors `permission_matrix.py`'s pattern.
- **No pagination (LD7).** The catalogue is bounded (~36 permissions at v0; ~50 at scale). Single response carries both held and available.
- **Sort order (LD8).** Both `permissions` and `available_permissions` are sorted module/resource/action/scope/code/id ascending — enum-ordinal for module/resource/action/scope, then code lexicographic (equivalent because codes follow the dot-tuple format). Matches `list_permissions_for_role`.
- **`user_count` correlates per-row.** Same SUM-of-two-correlated-subqueries pattern as E1; RLS-scoped on the tenant-side branch for TENANT JWTs. See E1's behaviour notes for the audience-check trigger guarantee that exactly one branch contributes a non-zero count.
- **`GATE_EXEMPT` (LD6).** Joins the other role read endpoints in `GATE_EXEMPT_PATHS`. PATCH (Step 6.18.3) will gate on `ADMIN.ROLES.OVERRIDE.GLOBAL`; GET stays exempt per FN-AB-30 deferral.

### 6. Example calls

```bash
# PLATFORM JWT — SUPER_ADMIN role.
curl -s -H "Authorization: Bearer $JWT" \
  "https://admin-dev.ithina.com/api/v1/roles/94340a03-3f07-4814-91d6-3f78e3e9de99"

# TENANT JWT — OWNER role (TENANT-audience; succeeds).
curl -s -H "Authorization: Bearer $TENANT_JWT" \
  "https://admin-dev.ithina.com/api/v1/roles/<owner_id>"

# TENANT JWT — SUPER_ADMIN id (PLATFORM-audience; 404).
curl -s -H "Authorization: Bearer $TENANT_JWT" \
  "https://admin-dev.ithina.com/api/v1/roles/94340a03-3f07-4814-91d6-3f78e3e9de99"
# {"code": "ROLE_NOT_FOUND", ...}
```

### 7. Sample integration code

```typescript
type PermissionDetail = {
  id: string;
  module: string;
  module_label: string;
  resource: string;
  resource_label: string;
  action: string;
  action_label: string;
  scope: "GLOBAL" | "TENANT" | "STORE";
  scope_label: string;
  code: string;
  description: string | null;
};

type RoleDetail = {
  id: string;
  name: string;
  code: string;
  description: string | null;
  audience: "PLATFORM" | "TENANT";
  status: "ACTIVE" | "INACTIVE" | "ARCHIVED";
  is_system: boolean;
  user_count: number;
  created_at: string;
  updated_at: string;
  permissions: PermissionDetail[];
  available_permissions: PermissionDetail[];
};

async function getRoleDetail(jwt: string, roleId: string) {
  const r = await fetch(
    `https://admin-dev.ithina.com/api/v1/roles/${roleId}`,
    { headers: { Authorization: `Bearer ${jwt}` } },
  );
  if (r.status === 404) return null;
  if (!r.ok) throw new Error(`${r.status}`);
  return (await r.json()) as RoleDetail;
}
```

### 8. Implementation reference

| File | Role |
|---|---|
| `src/admin_backend/routers/v1/rbac.py` | `get_role` handler + reused `RoleNotFoundError` |
| `src/admin_backend/repositories/roles.py` | `RolesRepo.get_detail_by_id` + `_select_permissions_with_labels` helper |
| `src/admin_backend/schemas/role.py` | `RoleDetail` |
| `src/admin_backend/schemas/permission.py` | `PermissionDetail` |
| `src/admin_backend/auth/gate_allowlist.py` | `/api/v1/roles/{role_id}` listed in `GATE_EXEMPT_PATHS` |
| `tests/integration/test_rbac_router.py` | D1-D8 (D1, D2, D3, D5, D6, D7 load-bearing) |

---

## `PATCH /api/v1/roles/{role_id}`  (E8)

Role-edit endpoint (Step 6.18.3).

### 1. Endpoint summary

- **Method:** `PATCH`
- **Path:** `/api/v1/roles/{role_id}`
- **Description:** Partial update of a role: name, description, and/or permission set. PLATFORM-only by gate construction (`ADMIN.ROLES.OVERRIDE.GLOBAL` + `audience="PLATFORM"`). Two-layer invariant guards the platform-admin bootstrap. SUPER_ADMIN itself is locked from PATCH in v0.
- **Who can call:** PLATFORM JWT only. The gate's audience kwarg refuses TENANT JWTs at Layer 1; a PLATFORM JWT without OVERRIDE.GLOBAL grant is refused at Layer 2.

### 2. Request

**Headers:**

| Header | Required | Notes |
|---|---|---|
| `Authorization` | Yes | `Bearer <jwt>` (PLATFORM, holding ADMIN.ROLES.OVERRIDE.GLOBAL) |
| `Content-Type` | Yes | `application/json` |
| `Accept` | No | Defaults to `application/json` |

**Path parameters:**

| Param | Type | Notes |
|---|---|---|
| `role_id` | UUID | Role to edit. Malformed UUID -> 422. |

**Query parameters:** none.

**Request body (`RoleUpdateRequest`):**

| Field | Type | Notes |
|---|---|---|
| `name` | string \| null | New role name. Length 1-100 per `ck_roles_name_length`. |
| `description` | string \| null | New description. `null` clears the field. |
| `permission_ids` | list[UUID] \| null | Replace-set of permissions. Empty list `[]` removes all. Diff vs current is applied at the repo layer (LD5). Unchanged rows preserve `created_at` and `created_by_*`. |

**Forbidden fields** (rejected by Pydantic `extra="forbid"` -> 422): `audience`, `code`, `is_system`, `status`, and all audit columns. The audience is immutable on PATCH; status transitions go through a separate (not-yet-shipped) activate/deactivate API.

**Empty body** (`{}`) returns 422 `EMPTY_PATCH`.

### 3. Response 200

Same shape as `GET /api/v1/roles/{role_id}` (`RoleDetail`). Fields reflect the post-PATCH state with `updated_at` bumped.

### 4. Response codes

| Code | When | Body |
|---|---|---|
| 200 | Success | `RoleDetail` |
| 401 | Missing JWT | `AUTH_MISSING` |
| 403 | TENANT JWT (Layer 1 audience) | `PLATFORM_AUDIENCE_REQUIRED` |
| 403 | PLATFORM JWT without OVERRIDE.GLOBAL grant (Layer 2) | `PERMISSION_DENIED` |
| 404 | Role does not exist | `ROLE_NOT_FOUND` |
| 409 | Target is SUPER_ADMIN (LD12) | `SUPER_ADMIN_PROTECTED` |
| 409 | Target.status='ARCHIVED' (LD3) | `ROLE_ARCHIVED` |
| 409 | Edit would zero out OVERRIDE.GLOBAL active holders (LD6 Layer 1) | `LAST_OVERRIDE_HOLDER` |
| 422 | Empty body | `EMPTY_PATCH` |
| 422 | `permission_ids` contains UUIDs not in catalogue (LD11) | `INVALID_PERMISSION_ID` |
| 422 | TENANT-audience role + new GLOBAL-scope permission (LD10) | `AUDIENCE_SCOPE_MISMATCH` |
| 422 | Pydantic validation (forbidden field, malformed UUID, length) | FastAPI envelope |
| 500 | Layer 2 invariant tripwire fired (bug indicator) | `INTERNAL_ERROR` (generic; class name in logs only) |

### 5. Behaviour notes

- **Gate (LD4).** `Depends(require(ADMIN, ROLES, OVERRIDE, GLOBAL, audience="PLATFORM"))`. PLATFORM-only by gate-tuple construction (no TENANT role holds any `.GLOBAL` permission per LD17 audience-scope coherence) AND defense-in-depth via the audience kwarg.
- **Order of operations (LD17).** SUPER_ADMIN check fires BEFORE status check (LD18 — SUPER_ADMIN locked even if ARCHIVED). Then empty-body guard. Then if `permission_ids` is in body: permission existence pre-check -> diff compute -> audience-scope check -> Layer 1 OVERRIDE invariant (only when the edit removes OVERRIDE from the role-under-edit). Writes apply atomically; Layer 2 tripwire reads the committed-to-be state.
- **Diff-replace audit preservation (LD5 / LD14 / LD15).** Unchanged role_permissions rows are not touched (created_at + created_by_* preserved). New rows populate `created_by_user_id` + `created_by_user_type` from the caller's `AuthContext`. The role row's `updated_by_user_id` + `updated_by_user_type` bump on every PATCH. `updated_at` refreshes via the BEFORE-UPDATE trigger.
- **Two-layer OVERRIDE.GLOBAL invariant (LD6).** Layer 1 (pre-write) raises 409 if the edit would leave zero active holders of ADMIN.ROLES.OVERRIDE.GLOBAL platform-wide. Layer 2 (post-write tripwire) catches Layer 1 bugs: if the committed-to-be state has zero holders despite Layer 1's pass, it raises `InternalInvariantViolationError` -> 500. Both queries filter BOTH assignment-side AND user-side `status='ACTIVE'` (LD7).
- **Pre-check optimisation (LD9).** Layer 1 query runs ONLY when the edit removes OVERRIDE.GLOBAL from the role-under-edit. Other edits (add OVERRIDE, no-op, unrelated) skip the invariant query.
- **SUPER_ADMIN v0 lockout (LD12 / LD20).** SUPER_ADMIN role is uneditable via API in v0. Operator workflow for SUPER_ADMIN edits is direct SQL on `core.roles` / `core.role_permissions` via Cloud SQL Studio. v1 promotion deferred per FN-AB.
- **Audience-scope coherence (LD10).** TENANT-audience roles structurally cannot hold GLOBAL-scope permissions. The pre-check is lenient: only the diff `new - current` (additions) is inspected, so a pre-existing GLOBAL grant on a TENANT role (catalogue drift) does not block the edit; only NEW GLOBAL additions are rejected.
- **Transaction boundary.** The handler runs inside `get_tenant_session_dep`'s `async with session.begin()` block. Any escaped exception (including Layer 2 tripwire) rolls back the entire transaction.

### 6. Example calls

```bash
# Happy path: edit PLATFORM_ADMIN name.
curl -s -X PATCH \
  -H "Authorization: Bearer $SUPER_ADMIN_JWT" \
  -H "Content-Type: application/json" \
  -d '{"name": "Platform Admin (Renamed)"}' \
  "https://admin-dev.ithina.com/api/v1/roles/<platform_admin_id>"

# Replace permission set (diff-replace at repo).
curl -s -X PATCH \
  -H "Authorization: Bearer $SUPER_ADMIN_JWT" \
  -H "Content-Type: application/json" \
  -d '{"permission_ids": ["<perm1>", "<perm2>", "<perm3>"]}' \
  "https://admin-dev.ithina.com/api/v1/roles/<role_id>"

# Refused: SUPER_ADMIN locked in v0.
curl -s -X PATCH \
  -H "Authorization: Bearer $SUPER_ADMIN_JWT" \
  -H "Content-Type: application/json" \
  -d '{"name": "Try to rename SUPER_ADMIN"}' \
  "https://admin-dev.ithina.com/api/v1/roles/<super_admin_id>"
# {"code":"SUPER_ADMIN_PROTECTED", ...}

# Refused: TENANT JWT.
curl -s -X PATCH \
  -H "Authorization: Bearer $TENANT_JWT" \
  -H "Content-Type: application/json" \
  -d '{"name": "x"}' \
  "https://admin-dev.ithina.com/api/v1/roles/<any_role_id>"
# {"code":"PLATFORM_AUDIENCE_REQUIRED", ...}
```

### 7. Sample integration code

```typescript
type RoleUpdateRequest = {
  name?: string;
  description?: string | null;
  permission_ids?: string[];
};

async function patchRole(
  jwt: string,
  roleId: string,
  patch: RoleUpdateRequest,
): Promise<RoleDetail> {
  const r = await fetch(
    `https://admin-dev.ithina.com/api/v1/roles/${roleId}`,
    {
      method: "PATCH",
      headers: {
        Authorization: `Bearer ${jwt}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(patch),
    },
  );
  if (!r.ok) {
    const err = await r.json();
    throw new Error(`${err.code}: ${err.message}`);
  }
  return (await r.json()) as RoleDetail;
}
```

### 8. Implementation reference

| File | Role |
|---|---|
| `src/admin_backend/routers/v1/rbac.py` | `patch_role` handler + local `_actor_type_from_auth` helper |
| `src/admin_backend/repositories/roles.py` | `RolesRepo.update` + `_count_override_global_active_holders` + `_resolve_override_global_permission_id` |
| `src/admin_backend/schemas/role.py` | `RoleUpdateRequest` |
| `src/admin_backend/errors.py` | `RoleArchivedError`, `InvalidPermissionError`, `AudienceScopeMismatchError`, `LastOverrideHolderError`, `SuperAdminProtectedError`, `InternalInvariantViolationError` |
| `tests/integration/test_rbac_writes_router.py` | W1-W30 (W1, W3, W4, W5, W7-W11, W13-W16, W18-W26, W29, W30 load-bearing) |
| `tests/integration/test_rbac_writes_repo.py` | RW1-RW6 (invariant edge cases + diff preservation + rollback) |
| `tests/integration/test_gate_discipline.py` | New entry in `_PLATFORM_ONLY_WRITE_ROUTES` |
