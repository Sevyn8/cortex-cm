# Audit Log endpoints

Canonical endpoint documentation for the audit log read surface (Step 6.16.3). Two GET endpoints under `/api/v1/audit/activities`. Format follows CLAUDE.md "Per-endpoint documentation" (eight fixed sections per endpoint). The design reference for the whole subsystem is `docs/architecture_audit_logs.md`; this file documents only the read endpoints' wire contract.

| Endpoint | Description | Calling user types |
|---|---|---|
| `GET /api/v1/audit/activities` | List audit rows, newest-first, cursor-paginated, filterable, searchable | PLATFORM (merged UNION across both tables); TENANT (own-tenant only via RLS) |
| `GET /api/v1/audit/activities/{audit_row_id}` | Single audit row, full 16-column shape | PLATFORM (any row); TENANT (own-tenant rows only; cross-tenant -> 404) |

Cross-cutting:

- **Auth.** `Authorization: Bearer <jwt>` required; missing or invalid -> 401.
- **Gate.** `ADMIN.AUDIT_LOG.VIEW.TENANT` on both endpoints. Multi-audience (`audience=None`). PLATFORM callers pass via the `GLOBAL` -> `TENANT` scope cascade (the seed grants `ADMIN.AUDIT_LOG.VIEW.GLOBAL` to the 3 platform roles); tenant roles holding `.VIEW.TENANT` (OWNER and others per the operator catalogue at Step 6.16.3) pass via direct grant. No `anchor_dep` per LD8 (list/detail reads scoped by caller audience + RLS).
- **Cursor pagination.** Opaque base64-encoded `(timestamp, id)` anchor. Newest-first only (`timestamp DESC, id DESC`). Departs from the project's offset-based `Pagination` convention used by tenants / stores / tenant-users; the audit log is the only subsystem with structurally unbounded growth. See `docs/architecture_audit_logs.md` Read contract > Pagination for the rationale.
- **Audience-driven branching at the repo, not the handler (LD1).** PLATFORM callers see a UNION ALL across `tenant_activity_audit_logs` + `platform_activity_audit_logs`, with a synthesised `scope` field distinguishing rows from each branch. TENANT callers see only `tenant_activity_audit_logs` (RLS-scoped by D-29 OR-branch policy).
- **RLS-as-404 (D-17).** Cross-tenant probes by a TENANT JWT surface as 404 `AUDIT_EVENT_NOT_FOUND`, never 403. The detail endpoint also collapses "TENANT caller probing a platform-table row" to the same 404 code per the design doc's read principle ("tenant users never see platform-scope rows").
- **Append-only contract per D-31.** Field semantics, once shipped, are frozen. New variants get new field names.
- **Error envelope.** `{code, message, details, request_id}`. `details` is `null` in v0.
- **`X-Request-Id`** on every response (audit middleware).

---

## `GET /api/v1/audit/activities`  (List)

Cursor-paginated list of audit rows visible to the caller.

### 1. Endpoint summary

- **Method:** `GET`
- **Path:** `/api/v1/audit/activities`
- **Description:** Newest-first stream of audit rows. PLATFORM sees merged UNION across both audit tables; TENANT sees only own-tenant rows from `tenant_activity_audit_logs` (RLS-scoped). Filters compose via AND; search runs `ILIKE %term%` across four columns.
- **Who can call:** any authenticated user holding `ADMIN.AUDIT_LOG.VIEW.TENANT` (directly or via scope cascade from `.GLOBAL`).

### 2. Request

**Headers:**

| Header | Required | Notes |
|---|---|---|
| `Authorization` | Yes | `Bearer <jwt>` (PLATFORM or TENANT) |
| `Accept` | No | Defaults to `application/json` |

**Path parameters:** none.

**Query parameters:**

| Param | Type | Required | Notes |
|---|---|---|---|
| `cursor` | string (opaque) | No | Pass the `next_cursor` from a previous response to fetch the next page. Omit on the first page. Malformed cursor -> 422 `INVALID_CURSOR`. Max length 1024 chars. |
| `limit` | int | No | Page size. Default 50. Min 1, max 200. The repo fetches `limit + 1` to detect `has_more` without an extra count query. |
| `from` | ISO-8601 datetime | No | Inclusive lower bound on `timestamp`. Must include timezone offset. |
| `to` | ISO-8601 datetime | No | Inclusive upper bound on `timestamp`. Must include timezone offset. |
| `status` | enum | No | One of `SUCCESS`, `PERMISSION_DENIED`, `VALIDATION_FAILED`, `CONFLICT`, `INTEGRITY_VIOLATION`, `INTERNAL_ERROR`. Filters on `result_type`. |
| `tenant_id` | UUID | No | Narrow to a single tenant. PLATFORM only; TENANT callers' filter is silently ignored (RLS already scopes their visibility). |
| `scope` | `PLATFORM` \| `TENANT` | No | Narrow the merged stream to one source branch. PLATFORM only; TENANT callers' filter is silently ignored (they never see the PLATFORM branch). |
| `search` | string | No | Case-insensitive substring match across `actor_display_name`, `action_label`, `resource_label`, `tenant_name`. Trimmed; empty after trim treated as no filter. |

**Request body:** none.

### 3. Response 200

```json
{
  "items": [
    {
      "id": "019e468a-c8e9-7433-aa25-421dde15c2af",
      "timestamp": "2026-05-20T14:32:11.123456+00:00",
      "actor_display_name": "Anjali Sharma",
      "action_label": "Update",
      "resource_label": "Buc-ee's",
      "result_label": "Success",
      "scope": "TENANT",
      "tenant_name": "Buc-ee's"
    }
  ],
  "pagination": {
    "next_cursor": "eyJ0cyI6ICIyMDI2LTA1LTIwVDE0OjMyOjExLjEyMzQ1Nis...",
    "prev_cursor": null,
    "limit": 50,
    "has_more": true
  }
}
```

**Field reference:**

| Field | Type | Notes |
|---|---|---|
| `items[].id` | UUID | Audit row id. Pass to the detail endpoint. |
| `items[].timestamp` | ISO-8601 datetime | When the event happened. |
| `items[].actor_display_name` | string | Denormalised actor name at write time. |
| `items[].action_label` | string | Human-readable action (`"Create"`, `"Update"`, `"Suspend"`, etc.). |
| `items[].resource_label` | string \| null | Human-readable resource name. Null on rows where the resource was never assigned an identity (e.g., failed-create rows). |
| `items[].result_label` | string | Human-readable result. |
| `items[].scope` | `"PLATFORM"` \| `"TENANT"` | Synthesised at query time; identifies which source table the row came from. |
| `items[].tenant_name` | string \| null | Denormalised tenant name at write time. Null on platform-table rows without tenant context. |
| `pagination.next_cursor` | string \| null | Pass on the next request to fetch the next page. Null when `has_more` is false. |
| `pagination.prev_cursor` | string \| null | First-row anchor of the current page; reserved for future bidirectional navigation. Null on the first page. |
| `pagination.limit` | int | Echo of the request's effective limit. |
| `pagination.has_more` | bool | Whether more rows exist beyond this page. |

### 4. Response codes

| Status | Code | Notes |
|---|---|---|
| 200 | n/a | List returned, possibly empty. |
| 401 | `AUTH_MISSING` / `AUTH_INVALID` | No / bad JWT. |
| 403 | `PERMISSION_DENIED` | Caller lacks the audit grant (no `.VIEW.GLOBAL` for PLATFORM, no `.VIEW.TENANT` for TENANT). |
| 422 | `INVALID_CURSOR` | Malformed `cursor` parameter. |
| 422 | n/a (FastAPI default) | Out-of-bounds `limit`, malformed `from`/`to` datetime, malformed UUID, etc. |

Sample 422 `INVALID_CURSOR` body:

```json
{
  "code": "INVALID_CURSOR",
  "message": "The pagination cursor is malformed or expired.",
  "details": null,
  "request_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

### 5. Behaviour notes

- **Cursor encoding:** `base64(json({"ts": "<iso8601>", "id": "<uuid>"}))`. The encoded payload is the LAST row of the current page; decoding it re-anchors the next page at "rows strictly older than that anchor."
- **Audience dispatch (LD1):** PLATFORM callers' SQL UNIONs both tables with a synthesised `scope` column. TENANT callers' SQL queries the tenant table only; the `tenant_id` and `scope` query parameters are silently ignored for TENANT callers.
- **RLS scoping:** TENANT callers' visibility is governed by the D-29 OR-branch policy on `tenant_activity_audit_logs`. PLATFORM callers see all rows on the tenant table via the OR-branch and all rows on the platform table (which has no RLS).
- **Search:** `ILIKE %term%` across 4 columns joined with OR. Search composes with other filters via AND. The leading `%` precludes index usage at v0; revisit if search becomes a hot path (FN-AB-64).
- **Newest first:** sort is fixed at `(timestamp DESC, id DESC)`. No reverse order in v0.
- **`tenant_id` filter on PLATFORM callers:** narrows the tenant branch. The platform branch's `tenant_id` populates only on tenant-creation success rows; setting `tenant_id` therefore effectively scopes to one tenant's tenant-side history plus their creation event.

### 6. Example calls

```bash
# PLATFORM caller, first page (default limit 50)
curl -H "Authorization: Bearer $PJWT" \
  https://admin-backend.dev.ithina.com/api/v1/audit/activities

# Next page via cursor
curl -H "Authorization: Bearer $PJWT" \
  "https://admin-backend.dev.ithina.com/api/v1/audit/activities?cursor=<next_cursor>&limit=50"

# Filter to permission-denied events in the last hour for tenant X
curl -H "Authorization: Bearer $PJWT" \
  "https://admin-backend.dev.ithina.com/api/v1/audit/activities?tenant_id=<uuid>&status=PERMISSION_DENIED&from=2026-05-20T13:00:00%2B00:00"

# Search by actor name
curl -H "Authorization: Bearer $PJWT" \
  "https://admin-backend.dev.ithina.com/api/v1/audit/activities?search=marcus"

# TENANT caller (RLS scopes to own tenant)
curl -H "Authorization: Bearer $TJWT" \
  https://admin-backend.dev.ithina.com/api/v1/audit/activities
```

### 7. Sample integration code

```typescript
async function fetchAuditPage(
  baseUrl: string,
  jwt: string,
  cursor?: string,
  limit = 50
): Promise<AuditActivitiesListResponse> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (cursor) params.set("cursor", cursor);

  const r = await fetch(`${baseUrl}/api/v1/audit/activities?${params}`, {
    headers: { Authorization: `Bearer ${jwt}` },
  });
  if (!r.ok) throw new Error(`audit list failed: ${r.status}`);
  return r.json();
}

// Sequential pagination loop
let cursor: string | undefined;
do {
  const page = await fetchAuditPage(BASE, jwt, cursor);
  for (const row of page.items) render(row);
  cursor = page.pagination.has_more ? page.pagination.next_cursor ?? undefined : undefined;
} while (cursor);
```

### 8. Implementation reference

- Handler: `src/admin_backend/routers/v1/audit.py::list_audit_activities`
- Repo: `src/admin_backend/repositories/audit_logs.py::AuditLogsRepo.list`
- Schemas: `src/admin_backend/schemas/audit_log.py` (`AuditActivitiesListResponse`, `AuditActivityListItem`, `CursorPagination`)
- Error: `src/admin_backend/errors.py::InvalidCursorError`
- Tests: `tests/integration/test_audit_router.py` (L1-L15), `tests/integration/test_audit_logs_repo.py` (R1-R8)

---

## `GET /api/v1/audit/activities/{audit_row_id}`  (Detail)

Single audit row, full 16-column shape including the `details` JSONB payload.

### 1. Endpoint summary

- **Method:** `GET`
- **Path:** `/api/v1/audit/activities/{audit_row_id}`
- **Description:** Probes both audit tables (tenant first, then platform). Returns the full row including the `details` JSONB payload. 404 `AUDIT_EVENT_NOT_FOUND` on miss (genuinely missing OR RLS-filtered OR cross-audience per D-17).
- **Who can call:** any authenticated user holding `ADMIN.AUDIT_LOG.VIEW.TENANT`. PLATFORM callers can fetch any row; TENANT callers see only own-tenant rows (cross-tenant probes return 404).

### 2. Request

**Headers:**

| Header | Required | Notes |
|---|---|---|
| `Authorization` | Yes | `Bearer <jwt>` |

**Path parameters:**

| Param | Type | Notes |
|---|---|---|
| `audit_row_id` | UUID | The row id; obtain from the list endpoint's `items[].id`. |

**Query parameters:** none. **Request body:** none.

### 3. Response 200

```json
{
  "id": "019e468a-c8e9-7433-aa25-421dde15c2af",
  "timestamp": "2026-05-20T14:32:11.123456+00:00",
  "tenant_id": "019e468a-c8e9-7433-aa25-421dde15c2b0",
  "tenant_name": "Buc-ee's",
  "actor_user_id": "019e468a-c8e9-7433-aa25-421dde15c2c0",
  "actor_user_type": "PLATFORM",
  "actor_display_name": "Anjali Sharma",
  "resource_type": "TENANT",
  "resource_id": "019e468a-c8e9-7433-aa25-421dde15c2b0",
  "resource_label": "Buc-ee's",
  "action": "UPDATE",
  "action_label": "Update",
  "result_type": "SUCCESS",
  "result_label": "Success",
  "request_id": "550e8400-e29b-41d4-a716-446655440000",
  "details": {
    "before": {"contact_email": "old@example.com"},
    "after":  {"contact_email": "new@example.com"}
  }
}
```

**Field reference:** all 16 stored columns. Pairs with the list-endpoint summary fields above plus:

| Field | Type | Notes |
|---|---|---|
| `tenant_id` | UUID \| null | Null on platform-table rows without tenant context. |
| `actor_user_id` | UUID | Raw FK to `platform_users.id` or `tenant_users.id` per `actor_user_type`. No SA-level FK declared (Pattern (b)). |
| `actor_user_type` | `"PLATFORM"` \| `"TENANT"` | Discriminator for `actor_user_id`. |
| `resource_type` | string | Free-text vocabulary (`"TENANT"`, `"TENANT_USER"`, `"STORE"`, etc.). |
| `resource_id` | UUID \| null | Null on failed-create rows where no identity was assigned. |
| `action` | string | Free-text vocabulary (`"CREATE"`, `"UPDATE"`, `"SUSPEND"`, etc.). |
| `result_type` | enum | One of the 6 `audit_result_type_enum` values. |
| `request_id` | UUID | Correlates with the response `X-Request-Id` header of the original write request. |
| `details` | object | Variable-shape JSONB payload per `result_type`; see the design doc Emission contract > Failure-row payload shapes for the per-result_type vocabulary. |

### 4. Response codes

| Status | Code | Notes |
|---|---|---|
| 200 | n/a | Row returned. |
| 401 | `AUTH_MISSING` / `AUTH_INVALID` | No / bad JWT. |
| 403 | `PERMISSION_DENIED` | Caller lacks the audit grant. |
| 404 | `AUDIT_EVENT_NOT_FOUND` | Row missing OR RLS-filtered OR cross-audience for the caller. |
| 422 | n/a (FastAPI default) | Malformed UUID in path. |

Sample 404:

```json
{
  "code": "AUDIT_EVENT_NOT_FOUND",
  "message": "Audit event not found",
  "details": null,
  "request_id": "..."
}
```

### 5. Behaviour notes

- **Probe order:** tenant table first, then platform table. RLS does the right thing on the tenant probe; the platform probe runs unconditionally (no RLS on `platform_activity_audit_logs`).
- **Cross-tenant probe by TENANT JWT:** RLS hides the row -> tenant probe returns nothing -> platform probe returns the row only if it lives there; the router-level read-principle check then collapses it to 404. Net behaviour: TENANT callers ALWAYS get 404 on rows from other tenants OR from the platform table.
- **Frontend correlation:** the `request_id` field lets the frontend group rows sharing the same originating HTTP request (e.g., a multi-row save action) and link to engineering logs via the same id.

### 6. Example calls

```bash
curl -H "Authorization: Bearer $JWT" \
  https://admin-backend.dev.ithina.com/api/v1/audit/activities/<audit_row_id>
```

### 7. Sample integration code

```typescript
async function fetchAuditDetail(
  baseUrl: string,
  jwt: string,
  id: string
): Promise<AuditActivityDetail | null> {
  const r = await fetch(`${baseUrl}/api/v1/audit/activities/${id}`, {
    headers: { Authorization: `Bearer ${jwt}` },
  });
  if (r.status === 404) return null;
  if (!r.ok) throw new Error(`audit detail failed: ${r.status}`);
  return r.json();
}
```

### 8. Implementation reference

- Handler: `src/admin_backend/routers/v1/audit.py::get_audit_activity`
- Repo: `src/admin_backend/repositories/audit_logs.py::AuditLogsRepo.get_by_id`
- Schema: `src/admin_backend/schemas/audit_log.py::AuditActivityDetail`
- Error: `src/admin_backend/errors.py::AuditEventNotFoundError`
- Tests: `tests/integration/test_audit_router.py` (D1-D7)
