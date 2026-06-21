# /me/* endpoints

Caller's-own-state surface. Two GET endpoints under `/api/v1/me`. Step 6.9.2.

| Endpoint | Description | Calling user types |
|---|---|---|
| `GET /api/v1/me/permissions` | Full permission grant set for the caller | any authenticated (PLATFORM or TENANT) |
| `GET /api/v1/me/can-do` | Server-authoritative single-permission check | any authenticated (PLATFORM or TENANT) |

Cross-cutting:

- **Auth** — `Authorization: Bearer <jwt>` required; missing or invalid -> 401. No `require(...)` gate applies (these endpoints describe the caller's own state).
- **Response envelope** — `/me/permissions` returns `{"permissions": [...]}` (always an array; empty if no grants). `/me/can-do` returns a flat object (D-30 single-resource shape).
- **Field semantics** — append-only per D-31.
- **Error envelope** — `{code, message, details, request_id}` on all server-generated errors. `details` is `null` in v0.
- **`X-Request-Id`** — set on every response by the audit middleware.
- **Server enforcement** — these endpoints are UX hints. The security boundary is the `require(...)` gate on each protected endpoint (Step 6.9.3 retrofit). Frontend uses `/me/*` to decide which UI elements to render; the server still authorises every action.

When to use which:

- `/me/permissions` — call once at login or session refresh; cache the result client-side and use it to gate UI elements (menu items, button states, disabled flags). Per-request fetching is correct but slower; the cached snapshot is a UX optimisation.
- `/me/can-do` — call before a high-stakes action where cascade-aware verification matters (`Can I delete this specific store?`). Server runs `has_permission` against the requested `(module, resource, action, scope, target_anchor)` and returns the same answer the gate will return when the action is attempted. Avoids the user clicking through a UI element only to receive 403 on submit.

---

## `GET /api/v1/me/permissions`

Full set of active permission grants for the caller.

### 1. Endpoint summary

- **Method:** `GET`
- **Path:** `/api/v1/me/permissions`
- **Description:** Returns every active permission grant on the caller's role assignments, scoped by audience and (for TENANT users) by `tenant_module_access.status = 'ENABLED'`.
- **Who can call:** any authenticated user. PLATFORM sees grants from `platform_user_role_assignments`; TENANT sees grants from `tenant_user_role_assignments`, filtered by enabled modules.

### 2. Request

**Headers:**

| Header | Required | Notes |
|---|---|---|
| `Authorization` | Yes | `Bearer <jwt>` |
| `Accept` | No | Defaults to `application/json` |

**Path parameters:** none.

**Query parameters:** none.

**Request body:** none.

### 3. Response 200

```json
{
  "permissions": [
    {
      "module": "ADMIN",
      "resource": "USERS",
      "action": "VIEW",
      "scope": "GLOBAL",
      "anchor_path": null
    },
    {
      "module": "PRICING_OS",
      "resource": "PRICING_RULES",
      "action": "VIEW",
      "scope": "TENANT",
      "anchor_path": "bucees"
    }
  ]
}
```

**Field reference:**

| Field | Type | Nullable | Notes |
|---|---|---|---|
| `module` | enum string | No | One of `module_code_enum` values (`ADMIN`, `PRICING_OS`, `PERISHABLES_ASSISTANT`, `PROMOTIONS_ASSISTANT`, `GOAL_CONSOLE`) |
| `resource` | enum string | No | One of `resource_enum` values (`USERS`, `ROLES`, `PRICING_RULES`, `MARKDOWNS`, ...) |
| `action` | enum string | No | One of `action_enum` values (`VIEW`, `CONFIGURE`, `EXECUTE`, `APPROVE`, `OVERRIDE`, `AUDIT`) |
| `scope` | enum string | No | One of `permission_scope_enum` values (`GLOBAL`, `TENANT`, `STORE`) |
| `anchor_path` | string | Yes | ltree path of the org_node anchor for TENANT grants; `null` for PLATFORM grants (which apply globally) |

`permissions` is always an array. An empty caller (no role assignments, or all module access disabled) returns `{"permissions": []}` not 404.

The same `(module, resource, action, scope)` tuple can appear more than once with different `anchor_path` values when the caller holds the same permission at multiple anchors via different role assignments.

### 4. Response codes

| Code | When | Body |
|---|---|---|
| 200 | Success | as above |
| 401 | Missing / invalid JWT | `{"code": "AUTH_MISSING", ...}` or `{"code": "AUTH_INVALID", ...}` |

### 5. Behaviour notes

- PLATFORM callers: `anchor_path` is always `null`. The query joins `platform_user_role_assignments` ⋈ `role_permissions` ⋈ `permissions`.
- TENANT callers: `anchor_path` is the org_node ltree path of the grant. The query joins through `org_nodes` (composite key per D-34) and `tenant_module_access` (`status='ENABLED'` filter). Grants for modules whose access is `DISABLED` are excluded.
- RLS scopes the underlying reads. A TENANT caller with a synthetic `tenant_id` (no real seed row) returns an empty array.
- Per-request DB read; no caching in v0 (FN-AB-24). Measured EXPLAIN ANALYZE on seeded data: PLATFORM 0.170 ms, TENANT 0.314 ms.

### 6. Example calls

```bash
# Full permission set for the caller
curl -H "Authorization: Bearer $JWT" \
  https://admin-backend/api/v1/me/permissions
```

### 7. Sample integration code (TypeScript)

```ts
type PermissionGrant = {
  module: string;
  resource: string;
  action: string;
  scope: string;
  anchor_path: string | null;
};

type MePermissionsResponse = {
  permissions: PermissionGrant[];
};

async function loadMyPermissions(token: string): Promise<PermissionGrant[]> {
  const r = await fetch("/api/v1/me/permissions", {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!r.ok) throw new Error(`me/permissions failed: ${r.status}`);
  const data = (await r.json()) as MePermissionsResponse;
  return data.permissions;
}
```

### 8. Implementation reference

- Handler: `src/admin_backend/routers/v1/me.py:get_me_permissions`
- Schema: `src/admin_backend/schemas/me.py:MePermissionsResponse`, `PermissionGrantRead`
- Resolver: `src/admin_backend/auth/permissions.py:get_permissions_for_user`
- Dataclass: `src/admin_backend/auth/permission_grant.py:PermissionGrant`

---

## `GET /api/v1/me/can-do`

Server-authoritative single-permission check.

### 1. Endpoint summary

- **Method:** `GET`
- **Path:** `/api/v1/me/can-do`
- **Description:** Runs `has_permission()` against the caller and the requested `(module, resource, action, scope, target_anchor)` tuple; returns the boolean decision plus a reason code.
- **Who can call:** any authenticated user.

### 2. Request

**Headers:**

| Header | Required | Notes |
|---|---|---|
| `Authorization` | Yes | `Bearer <jwt>` |
| `Accept` | No | Defaults to `application/json` |

**Path parameters:** none.

**Query parameters:**

| Param | Type | Required | Validation |
|---|---|---|---|
| `module` | enum string | Yes | One of `module_code_enum` values. Other values -> 422. |
| `resource` | enum string | Yes | One of `resource_enum` values. Other values -> 422. |
| `action` | enum string | Yes | One of `action_enum` values. Other values -> 422. |
| `scope` | enum string | Yes | One of `permission_scope_enum` values. Other values -> 422. |
| `target_anchor` | string | No | ltree path of the target the action would apply to. Required for cascade-aware checks on TENANT grants; ignored on the PLATFORM path. Invalid ltree syntax raises a server-side DB error (avoid sending arbitrary strings; pass an `org_nodes.path` value). |

**Request body:** none.

### 3. Response 200

```json
{
  "allowed": true,
  "reason_code": "GRANT_MATCHED"
}
```

**Field reference:**

| Field | Type | Notes |
|---|---|---|
| `allowed` | boolean | `true` iff the caller's grants cover the requested tuple at the requested anchor |
| `reason_code` | enum string | `GRANT_MATCHED` on allow; `NO_MATCHING_GRANT_OR_OUT_OF_SCOPE` on deny. Binary in v0; granular codes (cascade vs module-suspended vs no-match) deferred to Step 6.16. |

### 4. Response codes

| Code | When | Body |
|---|---|---|
| 200 | Decision returned (allowed or denied) | as above |
| 401 | Missing / invalid JWT | `{"code": "AUTH_MISSING", ...}` |
| 422 | Missing required query param or invalid enum value | FastAPI validation envelope |

A denied result is **not** a 403. The endpoint returns 200 with `allowed: false`; the 403 only surfaces when an actual gated endpoint is hit and the gate rejects.

### 5. Behaviour notes

- Same `has_permission()` SQL the `require(...)` gate uses; same answer for the same tuple.
- `target_anchor` cascade: PG ltree `<@` operator. Grants anchored at any ancestor of `target_anchor` match.
- TENANT path filters by `tenant_module_access.status = 'ENABLED'`. A grant whose module is disabled returns `allowed: false`.
- Cross-tenant: a TENANT-A user querying with a TENANT-B `target_anchor` receives `allowed: false`. The composite-FK JOIN and ltree path disjointness produce the denial; no leak of TENANT-B's existence.

### 6. Example calls

```bash
# PLATFORM-scope global permission
curl -H "Authorization: Bearer $JWT" \
  "https://admin-backend/api/v1/me/can-do?module=ADMIN&resource=USERS&action=VIEW&scope=GLOBAL"

# Cascade-aware: can I view markdowns at a specific store?
curl -H "Authorization: Bearer $JWT" \
  "https://admin-backend/api/v1/me/can-do?module=PRICING_OS&resource=MARKDOWNS&action=VIEW&scope=STORE&target_anchor=bucees.tx.austin_store_42"
```

### 7. Sample integration code (TypeScript)

```ts
type MeCanDoResponse = {
  allowed: boolean;
  reason_code: "GRANT_MATCHED" | "NO_MATCHING_GRANT_OR_OUT_OF_SCOPE";
};

async function canIDo(
  token: string,
  module: string,
  resource: string,
  action: string,
  scope: string,
  targetAnchor?: string,
): Promise<MeCanDoResponse> {
  const qs = new URLSearchParams({ module, resource, action, scope });
  if (targetAnchor) qs.set("target_anchor", targetAnchor);
  const r = await fetch(`/api/v1/me/can-do?${qs.toString()}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!r.ok) throw new Error(`me/can-do failed: ${r.status}`);
  return (await r.json()) as MeCanDoResponse;
}
```

### 8. Implementation reference

- Handler: `src/admin_backend/routers/v1/me.py:get_me_can_do`
- Schema: `src/admin_backend/schemas/me.py:MeCanDoResponse`
- Resolver: `src/admin_backend/auth/permissions.py:has_permission`
- Reason codes: `src/admin_backend/auth/reason_code.py:ReasonCode`
