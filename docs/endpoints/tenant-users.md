# Tenant users endpoints

Canonical endpoint documentation for the tenant_users resource (customer-side users). Two GET endpoints under `/api/v1/tenant-users`. Format follows CLAUDE.md "Per-endpoint documentation" — eight fixed sections per endpoint. Mirrors `tenants.md`'s structure exactly; resource-specific additions are **multi-user-type access** (no PLATFORM-only gate; both PLATFORM and TENANT JWTs accepted) and **RLS-as-404** for cross-tenant detail requests.

| Endpoint | Description | Calling user types |
|---|---|---|
| `GET /api/v1/tenant-users` | List tenant users with filters and pagination | PLATFORM (sees all; optional `?tenant_id=X` narrows) and TENANT (sees own tenant only via RLS) |
| `GET /api/v1/tenant-users/{user_id}` | Single tenant user detail | PLATFORM (any tenant) and TENANT (own only; cross-tenant -> 404 per RLS / D-17) |

Cross-cutting:

- **Auth** — `Authorization: Bearer <jwt>` required; missing or invalid -> 401.
- **No PLATFORM-only gate.** Both user types are accepted; visibility scoping is the DB layer's job via RLS via the `tenant_users_tenant_isolation` policy. PLATFORM JWTs see all rows via D-29's unconditional OR-branch; TENANT JWTs see only rows matching `app.tenant_id`.
- **Cross-tenant detail returns 404, not 403.** A TENANT-A user requesting TENANT-B's `user_id` receives 404 (`TENANT_USER_NOT_FOUND`). Per D-17 (RLS-as-404) — returning 403 would disclose existence. The load-bearing test `test_t9_cross_tenant_detail_returns_404` in `test_tenant_users_router.py` proves this works end-to-end.
- **Response envelope** — list shape is `{items, pagination}` (D-30); single-object endpoints return the object directly.
- **Field semantics** — append-only per D-31. Once a field's meaning ships, it stays. New variants get new field names.
- **Hidden fields.** `auth0_sub`, plus three Pattern (b) audit-actor pairs (`created_by_user_id`+`created_by_user_type`, `updated_by_user_id`+`updated_by_user_type`, `suspended_by_user_id`+`suspended_by_user_type`) — internal lineage; not for UI.
- **Error envelope** — `{code, message, details, request_id}` on all server-generated errors. `details` is `null` in v0.
- **`X-Request-Id`** — set on every response by the audit middleware; same UUID appears in the per-request log line.
- **RBAC** — not enforced in v0; lands at Step 6.1. The PLATFORM/TENANT binary at the router + RLS at the DB is the v0 coarse boundary; per-role distinctions ("Module Admin can list but not see suspension details") are out of scope here.

---

## `GET /api/v1/tenant-users`

List tenant users with filters, search, sort, and pagination.

### 1. Endpoint summary

- **Method:** `GET`
- **Path:** `/api/v1/tenant-users`
- **Description:** Returns tenant users paginated, RLS-scoped to the caller.
- **Who can call:** any authenticated user. PLATFORM sees all rows; TENANT sees only their own tenant's rows.

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
| `tenant_id` | UUID string | (none) | Application-layer narrowing. Useful for PLATFORM callers scoping a list view to a single tenant; TENANT callers see only their own tenant regardless (RLS handles it). For TENANT callers passing a non-matching value, the result is empty (RLS + filter intersect to empty). |
| `status` | string | (none) | One of `tenant_user_status_enum` values: `INVITED`, `ACTIVE`, `SUSPENDED`. Other values -> 422. |
| `search` | string | (none) | Trimmed; if length 0 after trim, treated as no filter. ILIKE substring match against `email` and `full_name`. |
| `sort` | string | `created_at_desc` | One of: `created_at_asc`, `created_at_desc`, `full_name_asc`, `full_name_desc`, `email_asc`, `email_desc`. Unknown -> 400 with `code: "INVALID_SORT_KEY"`. |
| `offset` | int | `0` | `>= 0` |
| `limit` | int | `50` | `>= 1`, `<= 200`. Above the cap -> 422. |

**Request body:** none.

### 3. Response 200

```json
{
  "items": [
    {
      "id": "01913abd-7c80-7bcd-9c8a-92d6f8a4e301",
      "tenant_id": "972a8469-1641-4f82-8b9d-2434e465e150",
      "email": "marcus.chen@bucees.com",
      "full_name": "Marcus Chen",
      "status": "ACTIVE",
      "invited_at": "2026-04-15T10:00:00+00:00",
      "invitation_accepted_at": "2026-04-15T10:42:00+00:00",
      "suspended_at": null,
      "created_at": "2026-04-15T10:00:00+00:00",
      "updated_at": "2026-04-15T10:42:00+00:00",
      "roles": [
        {
          "assignment_id": "01913abd-aaaa-7bcd-9c8a-92d6f8a4e301",
          "role_id": "01913abd-bbbb-7bcd-9c8a-92d6f8a4e302",
          "role_name": "Tenant Admin",
          "role_code": "TENANT_ADMIN",
          "status": "ACTIVE",
          "granted_at": "2026-04-15T10:42:00+00:00",
          "org_node_id": "01913abd-cccc-7bcd-9c8a-92d6f8a4e303",
          "org_node_name": "Buc-ee's"
        }
      ]
    }
  ],
  "pagination": {
    "total": 17,
    "offset": 0,
    "limit": 50
  }
}
```

**Field reference:**

| Field | Type | Nullable | Notes |
|---|---|---|---|
| `id` | UUID string | No | UUIDv7 from DB DEFAULT |
| `tenant_id` | UUID string | No | The tenant this user belongs to. Frontend uses this to group users by tenant |
| `email` | string | No | Lowercase enforced at write time. Unique per tenant |
| `full_name` | string | No | 1-200 chars |
| `status` | enum string | No | `INVITED`, `ACTIVE`, or `SUSPENDED` |
| `invited_at` | ISO 8601 with offset | Yes | Set when invite email is dispatched |
| `invitation_accepted_at` | ISO 8601 with offset | Yes | Set when status transitions INVITED -> ACTIVE |
| `suspended_at` | ISO 8601 with offset | Yes | Set when status transitions to SUSPENDED |
| `created_at` | ISO 8601 with offset | No | When the row was inserted |
| `updated_at` | ISO 8601 with offset | No | Most recent update on any field |
| `roles` | array of objects | No | Inline role assignments from `tenant_user_role_assignments`. Always present; empty array (not `null`) when the user has no assignments. Both ACTIVE and INACTIVE assignments included; frontend filters as needed. Ordered by `granted_at DESC, assignment_id ASC`. Step 6.8.3 augmentation. |

**`roles[]` per-item field reference:**

| Field | Type | Nullable | Notes |
|---|---|---|---|
| `assignment_id` | UUID string | No | The `id` of the `tenant_user_role_assignments` row |
| `role_id` | UUID string | No | FK into `roles` |
| `role_name` | string | No | Display name resolved from the joined `roles.name` |
| `role_code` | string | No | Stable code from `roles.code` (e.g., `TENANT_ADMIN`) |
| `status` | enum string | No | `ACTIVE` or `INACTIVE` |
| `granted_at` | ISO 8601 with offset | No | When the assignment was created |
| `org_node_id` | UUID string | No | Anchor org_node id (composite-FK guaranteed within the user's tenant) |
| `org_node_name` | string | No | Display name resolved from the joined `org_nodes.name` |

**Hidden by design:** `auth0_sub`, `created_by_user_id`, `created_by_user_type`, `updated_by_user_id`, `updated_by_user_type`, `suspended_by_user_id`, `suspended_by_user_type`. Within `roles[]`: `granted_by_user_id`, `granted_by_user_type`, `revoked_at`, `revoked_by_user_id`, `revoked_by_user_type`, `updated_at`. (Frontend can fetch full per-assignment lifecycle details from `/role-assignments` if needed; D-31 leaves room to add to `roles[]` later if a use case emerges.)

**Pagination block:**

| Field | Type | Notes |
|---|---|---|
| `total` | int | RLS-filtered total — what the caller can see, not the platform total |
| `offset` | int | Echo of request `offset` |
| `limit` | int | Echo of request `limit` |

### 4. Response codes

| Code | When | Body |
|---|---|---|
| 200 | Success | Body as above |
| 400 | Unknown `sort` key | `{code: "INVALID_SORT_KEY", message: "Invalid sort key", details: null, request_id}` |
| 401 | Missing or invalid JWT | `{code: "AUTH_MISSING" \| "AUTH_INVALID", message, details: null, request_id}` |
| 422 | Query-param validation failure (bad `status` enum value, malformed `tenant_id` UUID, `limit` out of range, etc.) | FastAPI default validation envelope |
| 500 | Internal server error | `{code: "INTERNAL_ERROR", message: "An internal error occurred", details: null, request_id}` |

### 5. Behaviour notes

- **RLS scope.** PLATFORM session sees all rows via D-29's unconditional OR-branch on `tenant_users_tenant_isolation`. TENANT session sees only rows matching `app.tenant_id`. RLS filters automatically; no handler-side filtering.
- **Default sort.** `created_at_desc` (newest first). Stable secondary sort by `id ASC` so identical primary-sort values page deterministically.
- **Search.** ILIKE substring match across `email` and `full_name`. Case-insensitive. Multi-word searches match as a single phrase.
- **Empty result.** `{items: [], pagination: {total: 0, offset: 0, limit: 50}}` and 200, not 404.
- **`tenant_id` filter on a TENANT JWT.** Functionally redundant (RLS already scopes to the caller's tenant); using it just makes the intent explicit. A non-matching value intersects to empty rather than disclosing other-tenant rows.
- **Hidden fields stay hidden.** `auth0_sub` is internal mapping to Auth0; the six audit-actor columns are internal lineage. Frontend renders lifecycle state from the timestamp fields alone.

### 6. Example calls

```bash
# All tenant users visible to the caller (default page, default sort).
curl -s -H "Authorization: Bearer $JWT" \
  "https://admin-dev.ithina.com/api/v1/tenant-users"

# PLATFORM scoping to a single tenant.
curl -s -H "Authorization: Bearer $PLATFORM_JWT" \
  "https://admin-dev.ithina.com/api/v1/tenant-users?tenant_id=972a8469-1641-4f82-8b9d-2434e465e150"

# Filter by status + search.
curl -s -H "Authorization: Bearer $JWT" \
  "https://admin-dev.ithina.com/api/v1/tenant-users?status=ACTIVE&search=marcus"

# Sort alphabetically by email.
curl -s -H "Authorization: Bearer $JWT" \
  "https://admin-dev.ithina.com/api/v1/tenant-users?sort=email_asc"

# Page 2 of 10.
curl -s -H "Authorization: Bearer $JWT" \
  "https://admin-dev.ithina.com/api/v1/tenant-users?offset=10&limit=10"
```

### 7. Sample integration code

```typescript
type TenantUserStatus = "INVITED" | "ACTIVE" | "SUSPENDED";

type TenantUserListItem = {
  id: string;
  tenant_id: string;
  email: string;
  full_name: string;
  status: TenantUserStatus;
  invited_at: string | null;
  invitation_accepted_at: string | null;
  suspended_at: string | null;
  created_at: string;
  updated_at: string;
};

type TenantUsersListResponse = {
  items: TenantUserListItem[];
  pagination: { total: number; offset: number; limit: number };
};

async function listTenantUsers(
  jwt: string,
  filters: {
    tenant_id?: string;
    status?: TenantUserStatus;
    search?: string;
    sort?: string;
    offset?: number;
    limit?: number;
  } = {},
): Promise<TenantUsersListResponse> {
  const url = new URL("https://admin-dev.ithina.com/api/v1/tenant-users");
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
| `src/admin_backend/routers/v1/tenant_users.py` | `list_tenant_users` handler |
| `src/admin_backend/repositories/tenant_users.py` | `TenantUsersRepo.list` + `SORT_MAP` |
| `src/admin_backend/repositories/_errors.py` | `InvalidSortKeyError` (shared with platform_users) |
| `src/admin_backend/errors.py` | `InvalidSortKeyClientError` (shared, 400) |
| `src/admin_backend/schemas/tenant_user.py` | `TenantUserListItem`, `TenantUserListResponse` |
| `src/admin_backend/models/tenant_user.py` | `TenantUser` ORM model + `TenantUserStatus` + `ActorUserType` enums |
| `tests/integration/test_tenant_users_router.py` | L1-L8 tests + T10 (cross-tenant filter -> empty) |

---

## `GET /api/v1/tenant-users/{user_id}`

Single tenant user by ID.

### 1. Endpoint summary

- **Method:** `GET`
- **Path:** `/api/v1/tenant-users/{user_id}`
- **Description:** Full detail shape for a single tenant user.
- **Who can call:** PLATFORM gets any tenant_user; TENANT gets only its own tenant's users (others -> 404 per RLS / D-17).

### 2. Request

**Headers:** Authorization required (PLATFORM or TENANT).

**Path parameters:**

| Param | Type | Notes |
|---|---|---|
| `user_id` | UUID string | Tenant user identifier; FastAPI validates the shape. Malformed -> 422. |

**Query parameters:** none.

**Request body:** none.

### 3. Response 200

```json
{
  "id": "01913abd-7c80-7bcd-9c8a-92d6f8a4e301",
  "tenant_id": "972a8469-1641-4f82-8b9d-2434e465e150",
  "email": "marcus.chen@bucees.com",
  "full_name": "Marcus Chen",
  "status": "ACTIVE",
  "invited_at": "2026-04-15T10:00:00+00:00",
  "invitation_accepted_at": "2026-04-15T10:42:00+00:00",
  "suspended_at": null,
  "created_at": "2026-04-15T10:00:00+00:00",
  "updated_at": "2026-04-15T10:42:00+00:00",
  "roles": [
    {
      "assignment_id": "01913abd-aaaa-7bcd-9c8a-92d6f8a4e301",
      "role_id": "01913abd-bbbb-7bcd-9c8a-92d6f8a4e302",
      "role_name": "Tenant Admin",
      "role_code": "TENANT_ADMIN",
      "status": "ACTIVE",
      "granted_at": "2026-04-15T10:42:00+00:00",
      "org_node_id": "01913abd-cccc-7bcd-9c8a-92d6f8a4e303",
      "org_node_name": "Buc-ee's"
    }
  ]
}
```

11 fields, fully flat. Same field set as the list-item shape; the dataset is small enough at v0 that a slimmer list projection isn't worthwhile. Step 6.8.3 added the `roles[]` field; both list and detail share one Pydantic class via `TenantUserListItem = TenantUserRead`.

**Field reference:** identical to the list-item shape. See `GET /api/v1/tenant-users` field reference above (including the `roles[]` per-item table).

### 4. Response codes

| Code | When | Body |
|---|---|---|
| 200 | Success | Body as above |
| 401 | Missing or invalid JWT | Standard auth-error envelope |
| 404 | `user_id` not found OR RLS-filtered (cross-tenant request from a TENANT JWT) | `{code: "TENANT_USER_NOT_FOUND", message: "Tenant user not found", details: null, request_id}` |
| 422 | `user_id` not a valid UUID | FastAPI default validation envelope |
| 500 | Internal server error | Standard `INTERNAL_ERROR` envelope |

**Sample 404 (canonical envelope):**

```json
{
  "code": "TENANT_USER_NOT_FOUND",
  "message": "Tenant user not found",
  "details": null,
  "request_id": "4de64a37-7905-469b-806a-b300876b5d4c"
}
```

### 5. Behaviour notes

- **RLS-as-404 (D-17).** A TENANT-A user requesting another tenant's `user_id` gets 404, not 403. RLS filters the row out before the handler sees it; the handler can't (and shouldn't) distinguish "no such user" from "you can't see this user". Returning 403 would leak existence. Test `test_t9_cross_tenant_detail_returns_404` is the load-bearing assertion that this works end-to-end.
- **Concurrent updates.** Reflects the row at query time. No version token in v0.
- **Fresh load.** No cache headers; the user directory may change due to status transitions and stale data on a suspension would be surprising.

### 6. Example calls

```bash
curl -s -H "Authorization: Bearer $JWT" \
  "https://admin-dev.ithina.com/api/v1/tenant-users/01913abd-7c80-7bcd-9c8a-92d6f8a4e301"
```

### 7. Sample integration code

```typescript
type TenantUserDetail = TenantUserListItem;

async function getTenantUser(
  jwt: string,
  userId: string,
): Promise<TenantUserDetail> {
  const r = await fetch(
    `https://admin-dev.ithina.com/api/v1/tenant-users/${userId}`,
    { headers: { Authorization: `Bearer ${jwt}` } },
  );
  if (r.status === 404) {
    throw new Error(`Tenant user ${userId} not found`);
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
| `src/admin_backend/routers/v1/tenant_users.py` | `get_tenant_user` handler + `TenantUserNotFoundError` |
| `src/admin_backend/repositories/tenant_users.py` | `TenantUsersRepo.get_by_id` |
| `src/admin_backend/schemas/tenant_user.py` | `TenantUserRead` |
| `src/admin_backend/models/tenant_user.py` | `TenantUser` ORM model |
| `tests/integration/test_tenant_users_router.py` | D1-D2 tests + **T9 cross-tenant detail -> 404** (LOAD-BEARING) |

---

## `POST /api/v1/tenant-users`

Step 6.10.1. Create a tenant user in INVITED state with bundled role assignments.

### Request

- **Auth.** Bearer JWT (PLATFORM or TENANT). Multi-audience: both PLATFORM (Ithina staff) and TENANT (tenant OWNER) callers pass the gate, subject to `ADMIN.USERS.CONFIGURE.TENANT` (held by SUPER_ADMIN + PLATFORM_ADMIN + OWNER per the seed catalogue).
- **Path params.** None.
- **Query params.** None.
- **Body** (`application/json`):

```json
{
  "tenant_id": "<uuid>",
  "email": "user@example.com",
  "full_name": "User Name",
  "roles": [
    {"role_id": "<tenant-audience-role-uuid>", "org_node_id": "<anchor-uuid>"},
    {"role_id": "<role-uuid>", "org_node_id": "<other-anchor-uuid>"}
  ]
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `tenant_id` | UUID | yes | Target tenant. RLS-scoped on cross-tenant probe (TENANT JWT targeting another tenant returns 404). |
| `email` | EmailStr | yes | Lowercased server-side (`ck_tenant_users_email_lowercase`). Per-tenant unique (`uq_tenant_users_tenant_email`). |
| `full_name` | string (1-200) | yes | `ck_tenant_users_full_name_length`. |
| `roles` | list[RoleAssignmentItem] | yes; non-empty (`min_length=1`); within-request dupes rejected as 422 | Each item carries `role_id` (TENANT-audience role) and `org_node_id` (the anchor org_node in the org tree). Step 6.14: tenant-root anchoring is just one option; the frontend resolves the tenant-root via `GET /tenants/{id}/org-tree` or chooses any non-archived descendant org_node in the same tenant. |

### Response 200/201

`201 Created` with the full `TenantUserRead` shape; status forced to `INVITED`, `auth0_sub`=null, `invitation_accepted_at`=null. `roles[]` carries one entry per assignment created.

### Response codes

| Code | Status | Reason |
|---|---|---|
| `401` | AUTH_MISSING | No JWT. |
| `403` | PERMISSION_DENIED | Caller lacks `ADMIN.USERS.CONFIGURE.TENANT` (after cascade). |
| `404` | TENANT_NOT_FOUND | Target tenant invisible (RLS-as-404 from a TENANT JWT) or missing tenant-root org_node. |
| `409` | DUPLICATE_TENANT_USER_EMAIL | Email collides with another user in the same tenant. |
| `409` | ROLE_ASSIGNMENT_CONFLICT | Concurrent edit produced a duplicate ACTIVE row (Step 6.14 LD7). |
| `422` | INVALID_ROLE | A role_id in `roles[]` is missing or ARCHIVED. |
| `422` | INVALID_ROLE_AUDIENCE | A role is non-TENANT audience (would fail trigger reject). |
| `422` | INVALID_ORG_NODE | An `org_node_id` in `roles[]` is missing, ARCHIVED, or cross-tenant (Step 6.14). |
| `422` | DUPLICATE_ROLE_ASSIGNMENT_IN_REQUEST | Same `(role_id, org_node_id)` appears more than once in `roles[]` (Step 6.14 LD5). |

### Behaviour notes

- Server forces `status='INVITED'`. The Auth0 invite-accept callback (INVITED -> ACTIVE) is Stage 3.
- Bundled role assignments anchor at any non-archived org_node in the same tenant (Step 6.14; pre-6.14 anchored only at the tenant root). Anchor-cascade is the gate's concern.
- Pattern (b) audit-actor pair populated on `tenant_users` AND each `tenant_user_role_assignments` row (D-13).
- Validation order (Step 6.14 LD4): `INVALID_ROLE` -> `INVALID_ROLE_AUDIENCE` -> tenant visibility -> `INVALID_ORG_NODE` -> `DUPLICATE_TENANT_USER_EMAIL`. A request hitting multiple failure modes returns the FIRST in this order.

### Example calls

```bash
# PLATFORM SUPER_ADMIN creating a user in Buc-ee's with role bundled
# at the tenant root org_node:
curl -X POST "$API/api/v1/tenant-users" \
  -H "Authorization: Bearer $PJWT" \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "<bucees-uuid>",
    "email": "newuser@bucees.com",
    "full_name": "New User",
    "roles": [
      {"role_id": "<owner-role-uuid>", "org_node_id": "<bucees-root-uuid>"}
    ]
  }'

# Same role, two anchors (Pattern B: same role active at distinct
# org_nodes):
curl -X POST "$API/api/v1/tenant-users" \
  -H "Authorization: Bearer $PJWT" \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "<bucees-uuid>",
    "email": "regional-mgr@bucees.com",
    "full_name": "Regional Manager",
    "roles": [
      {"role_id": "<owner-role-uuid>", "org_node_id": "<florida-region-uuid>"},
      {"role_id": "<owner-role-uuid>", "org_node_id": "<texas-region-uuid>"}
    ]
  }'
```

### Implementation reference

| File | Role |
|---|---|
| `src/admin_backend/routers/v1/tenant_users.py` | `create_tenant_user` handler |
| `src/admin_backend/repositories/tenant_users.py` | `TenantUsersRepo.create` |
| `src/admin_backend/schemas/tenant_user.py` | `TenantUserCreateRequest` |
| `src/admin_backend/errors.py` | `InvalidRoleError`, `InvalidRoleAudienceError`, `DuplicateTenantUserEmailError`, `InvalidOrgNodeError`, `DuplicateRoleAssignmentInRequestError`, `RoleAssignmentConflictError` |
| `tests/integration/test_tenant_users_writes_router.py` | C1-C9 + Step 6.14 R1-R5, V1-V7, P1 (**C3, C7, R1, R2, V2, V4, V5, V7 LOAD-BEARING**) |
| `tests/integration/test_tenant_users_repo_writes.py` | Step 6.14 RT1-RT6 (**RT1, RT4 LOAD-BEARING**) |

---

## `PATCH /api/v1/tenant-users/{user_id}`

Step 6.10.1. Partial update of a tenant user. Multi-audience with self-edit guard for TENANT callers.

### Request

- **Auth.** Bearer JWT (PLATFORM or TENANT). Multi-audience same as POST.
- **Path params.** `user_id` (UUID).
- **Query params.** None.
- **Body** (`application/json`, all fields optional, `extra="forbid"`):

```json
{
  "full_name": "Renamed",
  "email": "new@example.com",
  "roles": [
    {"role_id": "<role-uuid>", "org_node_id": "<anchor-uuid>"}
  ]
}
```

| Field | Type | Notes |
|---|---|---|
| `full_name` | string (1-200) | Optional. |
| `email` | EmailStr | Lowercased server-side. Rename collision -> 409. |
| `roles` | list[RoleAssignmentItem] OR null OR [] | Field omitted (`null`): no change. Empty list (`[]`): revoke ALL current ACTIVE assignments. Non-empty list: **diff-replace** (Step 6.14 LD3) against the current ACTIVE set; unchanged `(role_id, org_node_id)` tuples retain their original `granted_at` and `granted_by_*`. Within-request duplicates rejected as 422. |

Server-managed and lifecycle-managed fields (`tenant_id`, `status`, `auth0_sub`, `invited_at`, `invitation_accepted_at`, `suspended_*`, audit columns) rejected at the schema layer.

### Response 200

The full updated `TenantUserRead`. `roles[]` carries both pre-existing `INACTIVE` rows (from prior revocations) and the post-update ACTIVE set. Unchanged ACTIVE rows retain their original `granted_at`.

### Response codes

| Code | Status | Reason |
|---|---|---|
| `401` | AUTH_MISSING | No JWT. |
| `403` | PERMISSION_DENIED | Caller lacks `ADMIN.USERS.CONFIGURE.TENANT`. |
| `403` | SELF_EDIT_FORBIDDEN | TENANT caller's `user_id` matches the path `user_id`. |
| `404` | TENANT_USER_NOT_FOUND | Row missing or RLS-filtered (RLS-as-404 per D-17). |
| `409` | DUPLICATE_TENANT_USER_EMAIL | Email rename collides in same tenant. |
| `409` | ROLE_ASSIGNMENT_CONFLICT | Concurrent edit produced a duplicate ACTIVE row (Step 6.14 LD7). |
| `422` | EMPTY_PATCH | Body has no set fields. |
| `422` | INVALID_ROLE / INVALID_ROLE_AUDIENCE | Same as POST. |
| `422` | INVALID_ORG_NODE | Same as POST. |
| `422` | DUPLICATE_ROLE_ASSIGNMENT_IN_REQUEST | Within-request duplicate `(role_id, org_node_id)` (Step 6.14 LD5). |

### Behaviour notes

- Self-edit guard fires for TENANT callers only (PLATFORM users live in a different table; self-edit is structurally impossible).
- Allowed in any state (INVITED, ACTIVE, SUSPENDED). Status transitions go through `/suspend` and `/activate`.

**Diff-replace semantics (Step 6.14).** PATCH on `roles` is diff-replace, NOT whole-set replace. The submitted list is the desired complete set of ACTIVE role assignments after the operation:

- Tuples `(role_id, org_node_id)` present in both the current and desired sets are preserved verbatim: their `granted_at`, `granted_by_user_id`, `granted_by_user_type`, and `updated_at` are NOT changed.
- Tuples only in current go INACTIVE with `revoked_at` + `revoked_by_*` populated per Pattern (b).
- Tuples only in desired INSERT as new ACTIVE rows.
- `roles: []` (empty list) revokes every current ACTIVE assignment.
- `roles` field omitted (or `null`): no change to assignments.

This differs from the 6.10.1 whole-set-replace behavior which has been retired. The wire shape change is breaking: pre-6.14 callers sending `roles: ["uuid"]` now hit a 422 from Pydantic.

### Example calls

```bash
# Rename a user:
curl -X PATCH "$API/api/v1/tenant-users/$USER_ID" \
  -H "Authorization: Bearer $PJWT" \
  -H "Content-Type: application/json" \
  -d '{"full_name": "Renamed User"}'

# Diff-replace roles: keep (role_a, anchor_a), revoke (role_b, anchor_b),
# add (role_c, anchor_c). Pre/post the PATCH, role_a's row keeps its
# original granted_at; only role_b flips INACTIVE and role_c INSERTs.
curl -X PATCH "$API/api/v1/tenant-users/$USER_ID" \
  -H "Authorization: Bearer $PJWT" \
  -H "Content-Type: application/json" \
  -d '{"roles": [
    {"role_id": "<role-a-uuid>", "org_node_id": "<anchor-a-uuid>"},
    {"role_id": "<role-c-uuid>", "org_node_id": "<anchor-c-uuid>"}
  ]}'

# Revoke all ACTIVE assignments:
curl -X PATCH "$API/api/v1/tenant-users/$USER_ID" \
  -H "Authorization: Bearer $PJWT" \
  -H "Content-Type: application/json" \
  -d '{"roles": []}'
```

### Implementation reference

| File | Role |
|---|---|
| `src/admin_backend/routers/v1/tenant_users.py` | `patch_tenant_user` handler + `_raise_if_self_edit` + `_flatten_role_assignments` |
| `src/admin_backend/repositories/tenant_users.py` | `TenantUsersRepo.update`, `_apply_role_assignments_diff` |
| `src/admin_backend/schemas/tenant_user.py` | `TenantUserPatchRequest`, `RoleAssignmentItem` |
| `src/admin_backend/errors.py` | `SelfEditForbiddenError`, `EmptyPatchError`, plus Step 6.14 errors (see POST). |
| `tests/integration/test_tenant_users_writes_router.py` | P1-P12 + Step 6.14 R3-R5, P1 (**P3, P5, R3, R4 LOAD-BEARING**) |
| `tests/integration/test_tenant_users_repo_writes.py` | Step 6.14 RT1, RT3-RT4 (**RT1, RT4 LOAD-BEARING**) |

---

## `POST /api/v1/tenant-users/{user_id}/suspend`

Step 6.10.1. Transition ACTIVE -> SUSPENDED. Multi-audience with self-edit guard.

### Request

- **Auth.** Bearer JWT (PLATFORM or TENANT). Same gate as PATCH.
- **Path params.** `user_id` (UUID).
- **Body.** None.

### Response 200

The full updated `TenantUserRead`. `status` flips to `SUSPENDED`; `suspended_at` populated; `suspended_by_user_id` + `suspended_by_user_type` (Pattern (b) pair) populated.

### Response codes

| Code | Status | Reason |
|---|---|---|
| `403` | PERMISSION_DENIED / SELF_EDIT_FORBIDDEN | Standard gate / self-edit. |
| `404` | TENANT_USER_NOT_FOUND | Row missing or RLS-filtered. |
| `409` | INVALID_STATE_TRANSITION | Current status is INVITED or SUSPENDED. |

### Behaviour notes

- **Allowed source: ACTIVE only.**
  - INVITED -> SUSPENDED is structurally rejected by `ck_tenant_users_auth0_sub_consistency` (SUSPENDED requires `auth0_sub` non-NULL; INVITED requires NULL). App layer maps the rejection to a clean 409 so the client never sees a 500.
  - SUSPENDED -> SUSPENDED is a 409 by the transition matrix.
- Atomic with status flip: `suspended_at`, `suspended_by_user_id`, `suspended_by_user_type` all set (Pattern (b) pair invariant per `ck_tenant_users_suspended_consistency`).
- Self-suspend forbidden for TENANT callers (uniform guard).

### Example calls

```bash
curl -X POST "$API/api/v1/tenant-users/$USER_ID/suspend" \
  -H "Authorization: Bearer $PJWT"
```

### Implementation reference

| File | Role |
|---|---|
| `src/admin_backend/routers/v1/tenant_users.py` | `suspend_tenant_user` handler |
| `src/admin_backend/repositories/tenant_users.py` | `TenantUsersRepo.transition` |
| `tests/integration/test_tenant_users_writes_router.py` | S1-S5 (**S4 LOAD-BEARING**) |

---

## `POST /api/v1/tenant-users/{user_id}/activate`

Step 6.10.1. Transition SUSPENDED -> ACTIVE. Multi-audience with self-edit guard.

### Request

- **Auth.** Bearer JWT (PLATFORM or TENANT). Same gate as PATCH.
- **Path params.** `user_id` (UUID).
- **Body.** None.

### Response 200

The full updated `TenantUserRead`. `status` flips to `ACTIVE`; `suspended_at`, `suspended_by_user_id`, `suspended_by_user_type` cleared atomically.

### Response codes

| Code | Status | Reason |
|---|---|---|
| `403` | PERMISSION_DENIED / SELF_EDIT_FORBIDDEN | Standard. |
| `404` | TENANT_USER_NOT_FOUND | Row missing or RLS-filtered. |
| `409` | INVALID_STATE_TRANSITION | Current status is INVITED or ACTIVE. |

### Behaviour notes

- **Allowed source: SUSPENDED only.**
  - INVITED -> ACTIVE is the Auth0 invite-accept callback flow (Stage 3); the explicit `/activate` endpoint refuses that path so the v0 contract stays uniform.
  - ACTIVE -> ACTIVE is a 409 by the matrix.
- SUSPENDED -> ACTIVE clears all three `suspended_*` columns atomically per `ck_tenant_users_suspended_consistency` (a SUSPENDED row requires the three NOT NULL; any non-SUSPENDED row requires all three NULL).

### Example calls

```bash
curl -X POST "$API/api/v1/tenant-users/$USER_ID/activate" \
  -H "Authorization: Bearer $PJWT"
```

### Implementation reference

| File | Role |
|---|---|
| `src/admin_backend/routers/v1/tenant_users.py` | `activate_tenant_user` handler |
| `src/admin_backend/repositories/tenant_users.py` | `TenantUsersRepo.transition` |
| `tests/integration/test_tenant_users_writes_router.py` | A1-A5 |

---

## Cross-references

- `docs/architecture.md` — multi-tenancy enforcement (RLS), Layer 1 policy clauses (D-29), request flow.
- `docs/architecture_RBAC.md` — Step 6.10.1 worked examples (POST / PATCH / suspend / activate); audience=None subsection; Pattern (b) audit-actor convention.
- `db/raw_ddl/Ithina_postgres_SQL_DDL_tenant_users_v1.sql` — source of truth for the underlying schema.
- `CLAUDE.md` — D-02 (Pattern 2 user split), D-03 (RLS via session GUCs), D-13 Pattern (b) (audit-actors with no FK), D-17 (RLS-blocked -> 404), D-24 (AuthContext is the only path for tenant context), D-29 (PLATFORM RLS visibility), D-30 (response envelope), D-31 (field-meaning lock), "v0 auth model" convention note (binary user_type at router + RLS at DB; multi-user-type pattern with cross-tenant-404).

## What this document is NOT

- **Not the OpenAPI spec.** The OpenAPI spec is auto-generated by FastAPI at `/api/v1/openapi.json` and is the machine-readable source of truth. This file is the human-readable companion: behaviour notes, edge cases, and integration intent that don't fit cleanly into OpenAPI.
- **Not exhaustive of every internal detail.** Implementation reference points to source files for engineers who want depth.
