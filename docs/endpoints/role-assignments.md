# Role assignments endpoint

Canonical endpoint documentation for `/api/v1/role-assignments`. One GET endpoint that returns the post-split URA tables in a single grouped envelope. Format follows CLAUDE.md "Per-endpoint documentation" — eight fixed sections.

| Endpoint | Description | Calling user types |
|---|---|---|
| `GET /api/v1/role-assignments` | List role assignments grouped by audience | PLATFORM (sees both blocks) and TENANT (sees only own-tenant `tenant_assignments`; platform-side short-circuited at the router) |

Cross-cutting:

- **Auth** — `Authorization: Bearer <jwt>` required; missing or invalid -> 401.
- **Multi-user-type with router-level audience routing.** Both PLATFORM and TENANT JWTs are accepted. **Routing is security-load-bearing** (locked decision 12 of Step 6.8.3): a TENANT JWT MUST NOT execute the platform-side query because `platform_user_role_assignments` has no RLS — the application-layer routing here is the only barrier.
- **Response envelope (D-30 exception).** Two `{items, pagination}` blocks under a named-pair envelope: `{platform_assignments: {items, pagination}, tenant_assignments: {items, pagination}}`. Pagination is per-block — different physical tables, heterogeneous datasets.
- **Field semantics** — append-only per D-31. Once a field's meaning ships, it stays.
- **Hidden fields.** Pattern (b) audit-actor pairs (`granted_by_user_id`, `granted_by_user_type`, `revoked_by_user_id`, `revoked_by_user_type`) are absent from the response. The full assignment row is internal lineage; UI should consume the inline-mini-objects (`platform_user`, `role`, `tenant_user`, `tenant`, `org_node`).
- **Error envelope** — `{code, message, details, request_id}` on all server-generated errors. `details` is `null` in v0.
- **`X-Request-Id`** — set on every response by the audit middleware.

---

## `GET /api/v1/role-assignments`

List role assignments grouped by audience.

### 1. Endpoint summary

- **Method:** `GET`
- **Path:** `/api/v1/role-assignments`
- **Description:** Returns role assignments from both `platform_user_role_assignments` and `tenant_user_role_assignments`, grouped by audience. Each block has its own `{items, pagination}`.
- **Who can call:** any authenticated user. PLATFORM JWTs see both blocks populated; TENANT JWTs see only `tenant_assignments` (RLS-scoped to their tenant); `platform_assignments` is empty for TENANT JWTs because the platform-side query is short-circuited at the router.

### 2. Request

**Headers:**

| Header | Required | Notes |
|---|---|---|
| `Authorization` | Yes | `Bearer <jwt>` (PLATFORM or TENANT) |
| `Accept` | No | Defaults to `application/json` |

**Path parameters:** none.

**Query parameters:**

| Param | Type | Default | Notes |
|---|---|---|---|
| `role_id` | UUID string | (none) | Filter both blocks by role id. Roles have an audience (`PLATFORM` or `TENANT`) so this typically narrows results to one block. |
| `platform_user_id` | UUID string | (none) | Filter platform-side block by platform user. **Side effect:** when set, the tenant-side block is short-circuited (a platform user cannot have tenant-side assignments by definition). |
| `tenant_user_id` | UUID string | (none) | Filter tenant-side block by tenant user. **Side effect:** platform-side block is short-circuited. |
| `tenant_id` | UUID string | (none) | Application-layer narrowing for the tenant-side block. PLATFORM JWTs use this to scope a view to one tenant; TENANT JWTs already RLS-scope to their own tenant (filter is redundant but harmless). Platform-side block is unaffected (no `tenant_id` column on `platform_user_role_assignments`). |
| `org_node_id` | UUID string | (none) | Filter tenant-side block by org_node anchor. **Side effect:** platform-side block is short-circuited (no org_node column on platform-side). |
| `status` | enum string | (none) | `ACTIVE` or `INACTIVE`. Filters both blocks. |
| `sort` | string | `granted_at_desc` | One of `granted_at_asc`, `granted_at_desc`. Unknown -> 400 `INVALID_SORT_KEY`. |
| `offset` | int | `0` | `>= 0` |
| `limit` | int | `50` | `>= 1`, `<= 200`. Above the cap -> 422. |

**Request body:** none.

### 3. Response 200

```json
{
  "platform_assignments": {
    "items": [
      {
        "id": "01913abc-aaaa-7bcd-9c8a-92d6f8a4e201",
        "platform_user": {
          "id": "01913abc-7c80-7bcd-9c8a-92d6f8a4e201",
          "email": "anjali.mehta@ithina.com",
          "full_name": "Anjali Mehta"
        },
        "role": {
          "id": "01913abc-bbbb-7bcd-9c8a-92d6f8a4e202",
          "code": "SUPER_ADMIN",
          "name": "Super Admin",
          "audience": "PLATFORM"
        },
        "status": "ACTIVE",
        "granted_at": "2026-04-15T10:42:00+00:00",
        "revoked_at": null,
        "updated_at": "2026-04-15T10:42:00+00:00"
      }
    ],
    "pagination": {"total": 3, "offset": 0, "limit": 50}
  },
  "tenant_assignments": {
    "items": [
      {
        "id": "01913abd-aaaa-7bcd-9c8a-92d6f8a4e301",
        "tenant_user": {
          "id": "01913abd-7c80-7bcd-9c8a-92d6f8a4e301",
          "email": "marcus.chen@bucees.com",
          "full_name": "Marcus Chen"
        },
        "tenant": {
          "id": "972a8469-1641-4f82-8b9d-2434e465e150",
          "name": "Buc-ee's"
        },
        "org_node": {
          "id": "01913abd-cccc-7bcd-9c8a-92d6f8a4e303",
          "name": "Buc-ee's",
          "code": "BUC-EES",
          "node_type": "TENANT"
        },
        "role": {
          "id": "01913abd-bbbb-7bcd-9c8a-92d6f8a4e302",
          "code": "TENANT_ADMIN",
          "name": "Tenant Admin",
          "audience": "TENANT"
        },
        "status": "ACTIVE",
        "granted_at": "2026-04-15T10:42:00+00:00",
        "revoked_at": null,
        "updated_at": "2026-04-15T10:42:00+00:00"
      }
    ],
    "pagination": {"total": 19, "offset": 0, "limit": 50}
  }
}
```

**Per-block:** `{items: [...], pagination: {total, offset, limit}}` — pagination metadata is per-block since the two physical tables are independent datasets.

**Per-platform-assignment row:**

| Field | Type | Notes |
|---|---|---|
| `id` | UUID string | The `platform_user_role_assignments.id` |
| `platform_user` | object | Inline mini-object: `{id, email, full_name}` |
| `role` | object | Inline mini-object: `{id, code, name, audience}` (audience is always `PLATFORM` here, enforced by the audience-check trigger from Step 6.8.1) |
| `status` | enum string | `ACTIVE` or `INACTIVE` |
| `granted_at` | ISO 8601 with offset | Created |
| `revoked_at` | ISO 8601 with offset \| null | Set when status flipped to INACTIVE |
| `updated_at` | ISO 8601 with offset | Most recent update |

**Per-tenant-assignment row:** same as platform but adds:

| Field | Type | Notes |
|---|---|---|
| `tenant_user` | object | `{id, email, full_name}` |
| `tenant` | object | `{id, name}` |
| `org_node` | object | `{id, name, code, node_type}` — the assignment's anchor in the tenant's org tree (composite-FK guaranteed within the same tenant per Step 6.8.1 D-34 / AI-RBAC-06) |
| `role` | object | Audience is always `TENANT` here |

### 4. Response codes

| Code | When | Body |
|---|---|---|
| 200 | Success | Body as above |
| 400 | Unknown `sort` key | `{code: "INVALID_SORT_KEY", message: "Invalid sort key", details: null, request_id}` |
| 401 | Missing or invalid JWT | Standard auth-error envelope |
| 422 | Query-param validation failure (bad `status` enum value, `limit` out of range, malformed UUID, etc.) | FastAPI default validation envelope |
| 500 | Internal server error | `{code: "INTERNAL_ERROR", message: "An internal error occurred", details: null, request_id}` |

### 5. Behaviour notes

- **Multi-user-type with audience routing (security-load-bearing).** `platform_user_role_assignments` has no RLS — every session can read every row at the DB layer. The router's audience-routing logic (PLATFORM JWTs run both queries; TENANT JWTs skip the platform-side query entirely) is the only barrier. The R2 integration test asserts both the empty-block response shape AND the no-call invariant via a patch on the Repo method.
- **`tenant_user_role_assignments` is RLS+FORCE.** The tenant-side query inherits scoping from the session GUCs: PLATFORM JWTs see all tenants via D-29's unconditional OR-branch; TENANT JWTs see only their own tenant.
- **Composite-FK guarantee.** Tenant-side rows have NOT NULL `tenant_id` matching both `tenant_user_id`'s parent and `org_node_id`'s parent (composite FKs from Step 6.8.1). Cross-tenant injection is structurally impossible at the schema layer (R8 test).
- **Audience-check triggers from Step 6.8.1.** Every PLATFORM-audience assignment row lives on the platform-side table; every TENANT-audience assignment lives on the tenant-side table. The triggers (`enforce_platform_role_audience`, `enforce_tenant_role_audience`) reject mismatched INSERTs (R13 test).
- **No per-row impersonation.** The 6.8.1 split retired the FN-AB-14 anti-pattern. PLATFORM JWTs see both blocks in one query without setting `app.tenant_id` per-row (R12 LOAD-BEARING regression test).
- **Filter-shape narrowing.** Filters specific to one block's table cause the OTHER block to short-circuit (no query issued). Examples:
  - `?platform_user_id=X` — tenant block returns `{items: [], pagination.total: 0}` because a platform user cannot have tenant-side assignments.
  - `?tenant_user_id=Y` / `?org_node_id=Z` — platform block returns empty.
  - `?tenant_id=A` does NOT short-circuit platform block; it narrows the tenant block only.
- **Default sort.** `granted_at_desc` (newest first). Stable secondary sort by `id ASC`.
- **Hidden fields.** `granted_by_user_*`, `revoked_by_user_*` (Pattern (b) audit-actor pairs).

### 6. Example calls

```bash
# PLATFORM JWT — see both blocks
curl -H "Authorization: Bearer $PJWT" \
  "http://localhost:8000/api/v1/role-assignments?limit=20"

# TENANT JWT — only tenant_assignments populated
curl -H "Authorization: Bearer $TJWT" \
  "http://localhost:8000/api/v1/role-assignments"

# Filter to a single tenant (PLATFORM use case)
curl -H "Authorization: Bearer $PJWT" \
  "http://localhost:8000/api/v1/role-assignments?tenant_id=$TENANT_ID"

# Filter by role
curl -H "Authorization: Bearer $PJWT" \
  "http://localhost:8000/api/v1/role-assignments?role_id=$ROLE_ID"

# Filter by status — only INACTIVE (revoked) assignments
curl -H "Authorization: Bearer $PJWT" \
  "http://localhost:8000/api/v1/role-assignments?status=INACTIVE"

# Pagination — per block
curl -H "Authorization: Bearer $PJWT" \
  "http://localhost:8000/api/v1/role-assignments?limit=5&offset=10"
```

### 7. Sample integration code

```typescript
type AssignedRole = {
  id: string;
  code: string;
  name: string;
  audience: "PLATFORM" | "TENANT";
};

type PlatformAssignment = {
  id: string;
  platform_user: { id: string; email: string; full_name: string };
  role: AssignedRole;
  status: "ACTIVE" | "INACTIVE";
  granted_at: string;
  revoked_at: string | null;
  updated_at: string;
};

type TenantAssignment = PlatformAssignment & {
  tenant_user: { id: string; email: string; full_name: string };
  tenant: { id: string; name: string };
  org_node: {
    id: string;
    name: string;
    code: string;
    node_type: string;
  };
};

type RoleAssignmentsResponse = {
  platform_assignments: {
    items: PlatformAssignment[];
    pagination: { total: number; offset: number; limit: number };
  };
  tenant_assignments: {
    items: TenantAssignment[];
    pagination: { total: number; offset: number; limit: number };
  };
};

async function fetchRoleAssignments(
  jwt: string,
  filters: {
    role_id?: string;
    platform_user_id?: string;
    tenant_user_id?: string;
    tenant_id?: string;
    status?: "ACTIVE" | "INACTIVE";
    limit?: number;
    offset?: number;
  } = {}
): Promise<RoleAssignmentsResponse> {
  const qs = new URLSearchParams(
    Object.entries(filters).filter(([, v]) => v !== undefined) as [
      string,
      string,
    ][]
  ).toString();
  const resp = await fetch(`/api/v1/role-assignments?${qs}`, {
    headers: { Authorization: `Bearer ${jwt}` },
  });
  if (!resp.ok) throw new Error(`role-assignments fetch failed: ${resp.status}`);
  return resp.json();
}
```

### 8. Implementation reference

- Router: `src/admin_backend/routers/v1/role_assignments.py`
- Repo: `src/admin_backend/repositories/role_assignments.py` (`list_platform_assignments`, `list_tenant_assignments`)
- Schemas: `src/admin_backend/schemas/role_assignment.py` (per-block envelope wrappers + per-row item types + inline mini-objects)
- Models: `src/admin_backend/models/platform_user_role_assignment.py`, `src/admin_backend/models/tenant_user_role_assignment.py`
- DDL: `db/raw_ddl/Ithina_postgres_SQL_DDL_rbac_v3.sql` (Step 6.8.1 split per D-34)
- Tests: `tests/integration/test_role_assignments_router.py` (R1-R15; 5 LOAD-BEARING)

---

## Cross-references

- Step 6.8.1 D-34 (table split rationale; composite-FK guarantees; audience-check triggers).
- Step 6.8.2 (full ORM models + Repo + pre-emptive schemas; loader rewritten to route per audience).
- Step 6.8.3 / locked decision 12 (security-load-bearing TENANT-JWT short-circuit).
- D-17 (RLS-as-404 — *does not apply* on this endpoint; missing assignments simply don't appear in the items list).
- D-29 (PLATFORM RLS visibility OR-branch on `tenant_user_role_assignments`).
- D-30 / D-31 (response envelope and field-semantics conventions).

## What this document is NOT

This is the human-readable companion to the OpenAPI snapshot at `docs/endpoints/openapi.json`. The OpenAPI spec is the machine-readable source of truth (auto-generated by FastAPI). When they disagree, OpenAPI wins; this document gets a follow-up edit.
