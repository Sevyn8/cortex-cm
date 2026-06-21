# Platform users endpoints

Canonical endpoint documentation for the platform_users resource (Ithina staff users). Two GET endpoints under `/api/v1/platform-users`. Format follows CLAUDE.md "Per-endpoint documentation" — eight fixed sections per endpoint. Mirrors `tenants.md`'s structure exactly; the only resource-specific addition is the **PLATFORM-only access gate** at the router layer (per the v0 auth-model note in CLAUDE.md).

| Endpoint | Description | Calling user types |
|---|---|---|
| `GET /api/v1/platform-users` | List platform users with filters and pagination | PLATFORM only (TENANT JWTs -> 403 PERMISSION_DENIED) |
| `GET /api/v1/platform-users/{user_id}` | Single platform user detail | PLATFORM only (TENANT JWTs -> 403) |

Cross-cutting:

- **Auth** — `Authorization: Bearer <jwt>` required; missing or invalid -> 401. **PLATFORM `user_type` required** — TENANT JWTs hit the router gate and receive 403 with `code: "PERMISSION_DENIED"` before any DB call lands.
- **No RLS.** `platform_users` is platform-global reference data (no tenant boundary). The application-layer auth gate is the access boundary. The TENANT-JWT-rejection integration test (`test_a2`) is the load-bearing assertion guarding this.
- **Response envelope** — list shape is `{items, pagination}` (D-30); single-object endpoints return the object directly.
- **Field semantics** — append-only per D-31. Once a field's meaning ships, it stays. New variants get new field names.
- **Hidden fields.** `auth0_sub`, `created_by_user_id`, `updated_by_user_id`, `suspended_by_user_id` are intentionally absent from all response bodies (audit-actor and Auth0-internal IDs are internal lineage; not for UI).
- **Error envelope** — `{code, message, details, request_id}` on all server-generated errors. `details` is `null` in v0.
- **`X-Request-Id`** — set on every response by the audit middleware; same UUID appears in the per-request log line.
- **RBAC** — not enforced in v0; lands at Step 6.1. The PLATFORM/TENANT binary at the router is the v0 coarse boundary; per-role distinctions ("Module Admin can list but not see suspension details") are out of scope here.

---

## `GET /api/v1/platform-users`

List platform users (Ithina staff) with filters, search, sort, and pagination.

### 1. Endpoint summary

- **Method:** `GET`
- **Path:** `/api/v1/platform-users`
- **Description:** Returns platform users paginated. PLATFORM-only.
- **Who can call:** PLATFORM JWTs only. TENANT JWTs receive 403 PERMISSION_DENIED.

### 2. Request

**Headers:**

| Header | Required | Notes |
|---|---|---|
| `Authorization` | Yes | `Bearer <jwt>` (PLATFORM user_type) |
| `Accept` | No | Defaults to `application/json` |

**Path parameters:** none.

**Query parameters:**

| Param | Type | Default | Validation |
|---|---|---|---|
| `status` | string | (none) | One of `platform_user_status_enum` values: `INVITED`, `ACTIVE`, `SUSPENDED`. Other values -> 422. |
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
      "id": "01913abc-7c80-7bcd-9c8a-92d6f8a4e201",
      "email": "anjali.mehta@ithina.com",
      "full_name": "Anjali Mehta",
      "status": "ACTIVE",
      "invited_at": "2026-04-15T10:00:00+00:00",
      "invitation_accepted_at": "2026-04-15T10:42:00+00:00",
      "suspended_at": null,
      "created_at": "2026-04-15T10:00:00+00:00",
      "updated_at": "2026-04-15T10:42:00+00:00",
      "roles": [
        {
          "assignment_id": "01913abc-aaaa-7bcd-9c8a-92d6f8a4e201",
          "role_id": "01913abc-bbbb-7bcd-9c8a-92d6f8a4e202",
          "role_name": "Super Admin",
          "role_code": "SUPER_ADMIN",
          "status": "ACTIVE",
          "granted_at": "2026-04-15T10:42:00+00:00",
          "org_node_id": null,
          "org_node_name": null
        }
      ]
    },
    {
      "id": "01913abc-7c80-7bcd-9c8a-92d6f8a4e202",
      "email": "devon.park@ithina.com",
      "full_name": "Devon Park",
      "status": "ACTIVE",
      "invited_at": "2026-04-16T08:00:00+00:00",
      "invitation_accepted_at": "2026-04-16T08:30:00+00:00",
      "suspended_at": null,
      "created_at": "2026-04-16T08:00:00+00:00",
      "updated_at": "2026-04-16T08:30:00+00:00",
      "roles": []
    }
  ],
  "pagination": {
    "total": 3,
    "offset": 0,
    "limit": 50
  }
}
```

**Field reference:**

| Field | Type | Nullable | Notes |
|---|---|---|---|
| `id` | UUID string | No | UUIDv7 from DB DEFAULT |
| `email` | string | No | Lowercase enforced at write time |
| `full_name` | string | No | 1-200 chars |
| `status` | enum string | No | `INVITED`, `ACTIVE`, or `SUSPENDED` |
| `invited_at` | ISO 8601 with offset | Yes | Set when invite email is dispatched |
| `invitation_accepted_at` | ISO 8601 with offset | Yes | Set when status transitions INVITED -> ACTIVE |
| `suspended_at` | ISO 8601 with offset | Yes | Set when status transitions to SUSPENDED |
| `created_at` | ISO 8601 with offset | No | When the row was inserted |
| `updated_at` | ISO 8601 with offset | No | Most recent update on any field |
| `roles` | array of objects | No | Inline role assignments from `platform_user_role_assignments`. Always present; empty array (not `null`) when the user has no assignments. Both ACTIVE and INACTIVE assignments included. Ordered by `granted_at DESC, assignment_id ASC`. **For platform users, every item's `org_node_id` and `org_node_name` are `null`** — the platform-side assignment table has no org-node anchoring; the keys are still present so the wire shape stays uniform with `/tenant-users`. Step 6.8.3 augmentation. |

**`roles[]` per-item field reference:**

| Field | Type | Nullable | Notes |
|---|---|---|---|
| `assignment_id` | UUID string | No | The `id` of the `platform_user_role_assignments` row |
| `role_id` | UUID string | No | FK into `roles` (PLATFORM-audience by audience-check trigger) |
| `role_name` | string | No | Display name resolved from joined `roles.name` |
| `role_code` | string | No | Stable code from `roles.code` (e.g., `SUPER_ADMIN`) |
| `status` | enum string | No | `ACTIVE` or `INACTIVE` |
| `granted_at` | ISO 8601 with offset | No | When the assignment was created |
| `org_node_id` | UUID string \| null | Always null | No org-node anchor on platform-side; key present for shape uniformity |
| `org_node_name` | string \| null | Always null | Same as above |

**Hidden by design:** `auth0_sub`, `created_by_user_id`, `updated_by_user_id`, `suspended_by_user_id`. Within `roles[]`: `granted_by_user_id`, `granted_by_user_type`, `revoked_at`, `revoked_by_user_id`, `revoked_by_user_type`, `updated_at`.

**Pagination block:**

| Field | Type | Notes |
|---|---|---|
| `total` | int | Total matching the filters (ignores `offset` and `limit`) |
| `offset` | int | Echo of request `offset` |
| `limit` | int | Echo of request `limit` |

### 4. Response codes

| Code | When | Body |
|---|---|---|
| 200 | Success | Body as above |
| 400 | Unknown `sort` key | `{code: "INVALID_SORT_KEY", message: "Invalid sort key", details: null, request_id}` |
| 401 | Missing or invalid JWT | `{code: "AUTH_MISSING" \| "AUTH_INVALID", message, details: null, request_id}` |
| 403 | TENANT JWT (PLATFORM-only endpoint) | `{code: "PERMISSION_DENIED", message: "Permission denied", details: null, request_id}` |
| 422 | Query-param validation failure (bad `status` enum value, `limit` out of range, etc.) | FastAPI default validation envelope |
| 500 | Internal server error | `{code: "INTERNAL_ERROR", message: "An internal error occurred", details: null, request_id}` |

**Sample 403:**

```json
{
  "code": "PERMISSION_DENIED",
  "message": "Permission denied",
  "details": null,
  "request_id": "fac2d99f-94e0-4806-b354-e6f3e6a22fa6"
}
```

### 5. Behaviour notes

- **PLATFORM-only.** The router-layer `Depends(require(ADMIN, USERS, VIEW, GLOBAL))` gate fires before the Repo is touched. TENANT JWTs receive 403 PERMISSION_DENIED. This is the v0 binary user_type boundary; finer RBAC (e.g., "Module Admin can list but not see suspension details") lands at Step 6.1.
- **No RLS.** `platform_users` has no row-level security. The boundary is enforced at the router layer; without that gate any authenticated TENANT JWT could read all staff identities. The `test_a2_tenant_jwt_returns_403_platform_access_required` integration test guards this.
- **Default sort.** `created_at_desc` (newest staff first). Stable secondary sort by `id ASC` so identical primary-sort values page deterministically.
- **Search.** ILIKE substring match across `email` and `full_name`. Case-insensitive. Multi-word searches match as a single phrase (no token splitting). Diacritics not normalised.
- **Empty result.** `{items: [], pagination: {total: 0, offset: 0, limit: 50}}` and 200, not 404.
- **Hidden fields stay hidden.** `auth0_sub` is the Auth0 `sub` claim — internal mapping to the IdP, no UI use. Audit-actor IDs (`created_by_user_id`, etc.) are internal lineage; the frontend renders lifecycle state from the timestamp fields alone.

### 6. Example calls

```bash
# All platform users (default page, default sort = created_at_desc).
curl -s -H "Authorization: Bearer $JWT" \
  "https://admin-dev.ithina.com/api/v1/platform-users"

# Filter by status.
curl -s -H "Authorization: Bearer $JWT" \
  "https://admin-dev.ithina.com/api/v1/platform-users?status=ACTIVE"

# Search across email + full_name.
curl -s -H "Authorization: Bearer $JWT" \
  "https://admin-dev.ithina.com/api/v1/platform-users?search=anjali"

# Sort alphabetically by email.
curl -s -H "Authorization: Bearer $JWT" \
  "https://admin-dev.ithina.com/api/v1/platform-users?sort=email_asc"

# Page 2 of 10.
curl -s -H "Authorization: Bearer $JWT" \
  "https://admin-dev.ithina.com/api/v1/platform-users?offset=10&limit=10"
```

### 7. Sample integration code

```typescript
type PlatformUserStatus = "INVITED" | "ACTIVE" | "SUSPENDED";

type PlatformUserListItem = {
  id: string;
  email: string;
  full_name: string;
  status: PlatformUserStatus;
  invited_at: string | null;
  invitation_accepted_at: string | null;
  suspended_at: string | null;
  created_at: string;
  updated_at: string;
};

type PlatformUsersListResponse = {
  items: PlatformUserListItem[];
  pagination: { total: number; offset: number; limit: number };
};

async function listPlatformUsers(
  jwt: string,
  filters: {
    status?: PlatformUserStatus;
    search?: string;
    sort?: string;
    offset?: number;
    limit?: number;
  } = {},
): Promise<PlatformUsersListResponse> {
  const url = new URL("https://admin-dev.ithina.com/api/v1/platform-users");
  for (const [k, v] of Object.entries(filters)) {
    if (v !== undefined) url.searchParams.set(k, String(v));
  }
  const r = await fetch(url.toString(), {
    headers: { Authorization: `Bearer ${jwt}`, Accept: "application/json" },
  });
  if (r.status === 403) {
    throw new Error("Platform access required");
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
| `src/admin_backend/routers/v1/platform_users.py` | `list_platform_users` handler + `_require_platform_auth` gate |
| `src/admin_backend/repositories/platform_users.py` | `PlatformUsersRepo.list` + `SORT_MAP` + `InvalidSortKeyError` |
| `src/admin_backend/schemas/platform_user.py` | `PlatformUserListItem`, `PlatformUserListResponse` |
| `src/admin_backend/models/platform_user.py` | `PlatformUser` ORM model + `PlatformUserStatus` enum |
| `tests/integration/test_platform_users_router.py` | L1-L6 tests + A2 (TENANT-JWT 403) |

---

## `GET /api/v1/platform-users/{user_id}`

Single platform user by ID.

### 1. Endpoint summary

- **Method:** `GET`
- **Path:** `/api/v1/platform-users/{user_id}`
- **Description:** Full detail shape for a single platform user.
- **Who can call:** PLATFORM JWTs only. TENANT JWTs -> 403.

### 2. Request

**Headers:** Authorization required (PLATFORM user_type).

**Path parameters:**

| Param | Type | Notes |
|---|---|---|
| `user_id` | UUID string | Platform user identifier; FastAPI validates the shape. Malformed -> 422. |

**Query parameters:** none.

**Request body:** none.

### 3. Response 200

```json
{
  "id": "01913abc-7c80-7bcd-9c8a-92d6f8a4e201",
  "email": "anjali.mehta@ithina.com",
  "full_name": "Anjali Mehta",
  "status": "ACTIVE",
  "invited_at": "2026-04-15T10:00:00+00:00",
  "invitation_accepted_at": "2026-04-15T10:42:00+00:00",
  "suspended_at": null,
  "created_at": "2026-04-15T10:00:00+00:00",
  "updated_at": "2026-04-15T10:42:00+00:00",
  "roles": [
    {
      "assignment_id": "01913abc-aaaa-7bcd-9c8a-92d6f8a4e201",
      "role_id": "01913abc-bbbb-7bcd-9c8a-92d6f8a4e202",
      "role_name": "Super Admin",
      "role_code": "SUPER_ADMIN",
      "status": "ACTIVE",
      "granted_at": "2026-04-15T10:42:00+00:00",
      "org_node_id": null,
      "org_node_name": null
    }
  ]
}
```

10 fields, fully flat. Same field set as the list-item shape (Step 6.8.3 added `roles[]` to both via `PlatformUserListItem = PlatformUserRead`).

**Field reference:** identical to the list-item shape. See `GET /api/v1/platform-users` field reference above (including the `roles[]` per-item table; `org_node_*` always null on platform side).

### 4. Response codes

| Code | When | Body |
|---|---|---|
| 200 | Success | Body as above |
| 401 | Missing or invalid JWT | Standard auth-error envelope |
| 403 | TENANT JWT | Standard `PERMISSION_DENIED` envelope |
| 404 | `user_id` not found | `{code: "PLATFORM_USER_NOT_FOUND", message: "Platform user not found", details: null, request_id}` |
| 422 | `user_id` not a valid UUID | FastAPI default validation envelope |
| 500 | Internal server error | Standard `INTERNAL_ERROR` envelope |

**Sample 404:**

```json
{
  "code": "PLATFORM_USER_NOT_FOUND",
  "message": "Platform user not found",
  "details": null,
  "request_id": "4de64a37-7905-469b-806a-b300876b5d4c"
}
```

### 5. Behaviour notes

- **404 vs 403.** Unlike tenants (where RLS-filtered rows surface as 404 per D-17 to avoid existence leaks), `platform_users` has no RLS so 404 means genuinely not found. The 403 path here is reserved for the user-type gate, not for visibility scoping.
- **Concurrent updates.** Reflects the row at query time. No version token in v0.
- **Fresh load.** No cache headers; the staff directory changes infrequently but stale data on a suspension change would be surprising. If load profile changes, reconsider.

### 6. Example calls

```bash
curl -s -H "Authorization: Bearer $JWT" \
  "https://admin-dev.ithina.com/api/v1/platform-users/01913abc-7c80-7bcd-9c8a-92d6f8a4e201"
```

### 7. Sample integration code

```typescript
type PlatformUserDetail = PlatformUserListItem;

async function getPlatformUser(
  jwt: string,
  userId: string,
): Promise<PlatformUserDetail> {
  const r = await fetch(
    `https://admin-dev.ithina.com/api/v1/platform-users/${userId}`,
    { headers: { Authorization: `Bearer ${jwt}` } },
  );
  if (r.status === 404) {
    throw new Error(`Platform user ${userId} not found`);
  }
  if (r.status === 403) {
    throw new Error("Platform access required");
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
| `src/admin_backend/routers/v1/platform_users.py` | `get_platform_user` handler + `PlatformUserNotFoundError` + `PlatformAccessRequiredError` |
| `src/admin_backend/repositories/platform_users.py` | `PlatformUsersRepo.get_by_id` |
| `src/admin_backend/schemas/platform_user.py` | `PlatformUserRead` |
| `src/admin_backend/models/platform_user.py` | `PlatformUser` ORM model |
| `tests/integration/test_platform_users_router.py` | D1-D2 tests + A1 (no JWT) |

---

## Cross-references

- `docs/architecture.md` — request flow, auth middleware.
- `db/raw_ddl/Ithina_postgres_SQL_DDL_platform_users_v1.sql` — source of truth for the underlying schema.
- `CLAUDE.md` — D-02 (Pattern 2 user split), D-13 Pattern (a) (audit-actor self-FK), D-17 (RLS-blocked -> 404; not directly applicable here since `platform_users` has no RLS), D-24 (AuthContext is the only path for tenant context), D-30 (response envelope), D-31 (field-meaning lock), "v0 auth model" convention note (binary user_type at router + RLS at DB).

## What this document is NOT

- **Not the OpenAPI spec.** The OpenAPI spec is auto-generated by FastAPI at `/api/v1/openapi.json` and is the machine-readable source of truth. This file is the human-readable companion: behaviour notes, edge cases, and integration intent that don't fit cleanly into OpenAPI.
- **Not exhaustive of every internal detail.** Implementation reference points to source files for engineers who want depth.
