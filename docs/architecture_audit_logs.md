# Audit Log Subsystem

> Design captured during Step 6.16.0 (pre-step design document) on 2026-05-20. Lives across the full 6.16.x sub-step series and beyond.

## Overview

The admin-backend records an immutable log of every write action across the platform. The audit log subsystem captures who did what, when, on which resource, and with what outcome (success / denied / failed). It exists to serve five operational use cases:

1. **Security forensics.** Investigating a suspected breach or anomaly: "who accessed Marcus's record on the morning of the incident?"
2. **Internal accountability.** Resolving operational disputes: "the OWNER claims they didn't disable the Pricing module; the log shows who did."
3. **Tenant transparency.** Tenant-facing audit UI for review of team actions.
4. **Engineering debugging.** Correlating audit rows to application logs via `request_id` to reconstruct request sequences.
5. **Regulatory compliance.** Offline data dumps for SOX / PCI / retail compliance audits. Compliance is served via export pipelines, not via the live API.

The system has three structural pieces:

1. **Storage layer.** Two physically separate tables (`core.tenant_activity_audit_logs` and `core.platform_activity_audit_logs`) with different RLS posture. Each row is one audit event.

2. **Emission layer.** Write endpoints (POST / PATCH / DELETE / state-transition POSTs) emit audit rows inside the same database transaction as the user-facing write. Synchronous emission; atomicity preserved.

3. **Read layer.** GET endpoints serving the frontend audit timeline. Two-layer UI: list view (Layer 1) and detail drilldown (Layer 2). Cursor-paginated, filterable, searchable.

This document is the canonical reference for the subsystem. Sub-step docs (6.16.1 through 6.16.7) implement against it. Future work that touches the audit subsystem (Stage 3 expansion, compliance changes, scale work) updates this document.

Step 6.16.7 (2026-05-23) extended the row schema with three columns (`actor_organization_name`, `actor_roles`, `resource_subtype`) and the read response shape with six fields (the three stored columns plus a backend-composed `what` display string and the raw `resource_type` / `result_type` enums); the audit list-view redesign surface lives at the deployed wire shape from this commit forward. Frontend rendering of the redesigned wire shape is out of project scope; handled separately by the frontend team.

## Routing principle

A row goes into `core.tenant_activity_audit_logs` when the affected resource has a tenant_id. The actor's user type and the action's outcome are irrelevant to this routing decision. A tenant-scoped resource being acted upon is the sole determining factor.

A row goes into `core.platform_activity_audit_logs` when the affected resource has no tenant_id: system catalogues (roles, permissions, modules), platform users, platform-wide entities.

**One named exception:** tenant creation. The row always goes into `platform_activity_audit_logs`, even when successful. On success, the new tenant_id appears in `resource_id`; on failure, `resource_id` is NULL. The action is platform-staff-initiated and concerns the platform's catalogue of tenants; the resulting tenant_id is the action's product, not its scope.

### Routing examples

| Action | Resource has tenant_id? | Table |
|---|---|---|
| POST /tenants (create new tenant) | n/a (exception) | platform |
| PATCH /tenants/{id} | yes | tenant |
| POST /tenants/{id}/suspend | yes | tenant |
| POST /tenant-users (create user inside tenant) | yes | tenant |
| PATCH /tenant-users/{id} | yes | tenant |
| Platform staff edits a tenant user's roles | yes | tenant |
| POST /module-access/{tid}/{mod}/enable | yes | tenant |
| POST /tenants/{tid}/org-tree (add node) | yes | tenant |
| PATCH /tenants/{tid}/org-tree/{id} | yes | tenant |
| POST /platform-users (Stage 2+) | no | platform |
| Creating / editing system catalogue roles | no | platform |
| Creating / editing system catalogue permissions | no | platform |

The actor's user type does not change routing. A platform user editing a tenant user produces a `tenant_activity_audit_logs` row, not a platform row, because the affected resource is tenant-scoped.

## Read-access principle

| User type | What they see | Source |
|---|---|---|
| Tenant user | Rows from `tenant_activity_audit_logs` filtered to their own tenant_id | RLS-enforced at DB layer |
| Platform user | Full `platform_activity_audit_logs` + all `tenant_activity_audit_logs` rows across all tenants | UNION at backend; RLS OR-branch lets platform pass tenant scoping |

Tenant users never see `platform_activity_audit_logs` rows. The platform table has no RLS; access is gated at the API layer (platform-only endpoints).

The GET endpoint at Step 6.16.3 surfaces the merged stream as a single chronological timeline. Gate tuples (`ADMIN.AUDIT.VIEW.TENANT`, `ADMIN.AUDIT.VIEW.GLOBAL` or final-named equivalents) gate access; tuples land at 6.16.3 with the GET endpoint.

## Architecture

### Two-table split (D-29 mirror)

The audit log subsystem mirrors the architectural pattern Step 6.8.1 established when it split `user_role_assignments` into `tenant_user_role_assignments` and `platform_user_role_assignments`. Same rationale here:

- **Tenant table has RLS+FORCE+D-29 OR-branch.** Tenant rows belong to tenants; the standard OR-branch policy `tenant_id = current_setting('app.tenant_id') OR app.user_type='PLATFORM'` enforces "tenant sees own; platform sees all" at the DB layer regardless of API endpoint.

- **Platform table has no RLS.** Platform rows aren't tenant-scoped. Access control happens at the API layer: GET endpoints under platform-only gates; no path for a tenant user to reach them.

Single-table-with-nullable-tenant_id was considered and retired per D-29 (RLS complexity). Two tables is structurally cleaner.

### UNION-based merged view

Platform users see a merged stream. The backend issues UNION ALL across both tables, ordered by `(timestamp DESC, id DESC)`, paginated by cursor. Both tables share the same 16-column shape (see Schema section); the UNION query reads cleanly as `SELECT ... FROM tenant... UNION ALL SELECT ... FROM platform... ORDER BY timestamp DESC, id DESC LIMIT N` without column projections per branch.

The synthesised `scope` value (PLATFORM / TENANT) is added at query time, not stored: rows in the platform branch project `'PLATFORM'`; rows in the tenant branch project `'TENANT'`. Frontend renders this for visual disambiguation.

Tenant users hit only the tenant table; no UNION fires for them.

### Two-layer UI

| Layer | Purpose | Source |
|---|---|---|
| Layer 1 (list view) | Chronological feed of events. One row per event. Filters and search apply here. | Top-level columns: timestamp, actor display name, tenant name, action label, resource label, scope, result label. |
| Layer 2 (drilldown) | Full detail of one event, rendered on click. | Top-level columns above PLUS the `details` JSONB payload, the `request_id`, and structured codes (`action`, `resource_type`, `result_type`) for engineering / API users. |

## Schema

### Symmetric column shape across both tables

Both tables carry the same 16 columns. They differ only in NULLABILITY semantics on `tenant_id` and `tenant_name`, and in RLS posture. This symmetric design is intentional; see the rationale at the end of this Schema section.

### Table 1: `core.tenant_activity_audit_logs`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | uuidv7 default |
| `timestamp` | TIMESTAMPTZ NOT NULL | `now()` default |
| `tenant_id` | UUID NOT NULL | FK to `core.tenants(id)` ON DELETE RESTRICT |
| `tenant_name` | TEXT NOT NULL | denormalised snapshot at write time |
| `actor_user_id` | UUID NOT NULL | no FK constraint (Pattern (b)) |
| `actor_user_type` | `core.actor_user_type_enum` NOT NULL | reused enum from existing schema |
| `actor_display_name` | TEXT NOT NULL | denormalised snapshot |
| `actor_organization_name` | TEXT NOT NULL | denormalised snapshot at write time; tenant name for tenant actors, literal `"Platform-Ithina"` for platform actors (Step 6.16.7 LD6) |
| `actor_roles` | TEXT NOT NULL | denormalised snapshot at write time; comma-separated active role display names from `roles.name` (e.g., `"Owner, Promotions Assistant"`, not the uppercase `roles.code`); rendered directly by the UI without further transformation (Step 6.16.7 LD5) |
| `resource_type` | TEXT NOT NULL | free-text vocabulary; grows as resources added |
| `resource_id` | UUID NULL | NULLABLE for failed-create rows |
| `resource_label` | TEXT NULL | NULLABLE alongside resource_id |
| `resource_subtype` | TEXT NULL | populated only for `resource_type = 'ORG_NODE'` rows with the `org_nodes.node_type` enum value frozen at write time; NULL for non-ORG_NODE rows and pre-6.16.7 historical rows (Step 6.16.7 LD7) |
| `action` | TEXT NOT NULL | free-text vocabulary; grows as actions added |
| `action_label` | TEXT NOT NULL | human-readable form |
| `result_type` | `core.audit_result_type_enum` NOT NULL | new enum; stable vocabulary |
| `result_label` | TEXT NOT NULL | human-readable form |
| `request_id` | UUID NOT NULL | correlation across rows from one HTTP request |
| `details` | JSONB NOT NULL DEFAULT '{}' | variable-shape payload per `result_type` |

Constraints:
- FK `tenant_id` to `core.tenants(id)` ON UPDATE RESTRICT ON DELETE RESTRICT
- CHECK `(resource_id IS NULL AND resource_label IS NULL) OR (resource_id IS NOT NULL AND resource_label IS NOT NULL)` enforces NULL-pair consistency

RLS: ENABLE + FORCE. Standard D-29 OR-branch policy `tenant_isolation`.

Indexes:
- `(timestamp DESC, id DESC)` : cursor pagination
- `(tenant_id, timestamp DESC, id DESC)` : RLS + tenant-scoped pagination
- `result_type WHERE result_type != 'SUCCESS'` : partial index for failure investigation

### Table 2: `core.platform_activity_audit_logs`

Same 19 columns as Table 1 (post-Step-6.16.7) with two NULLABILITY differences:

| Column | Type | Notes |
|---|---|---|
| `tenant_id` | UUID **NULLABLE** | populated only on tenant-creation success rows; FK to `core.tenants(id)` ON DELETE RESTRICT |
| `tenant_name` | TEXT **NULLABLE** | populated alongside tenant_id; NULL on all non-tenant-creation rows |

All other columns identical to Table 1 (same names, types, NOT NULL posture). The Step 6.16.7 additions (`actor_organization_name`, `actor_roles`, `resource_subtype`) appear on this table with the same shape; `actor_organization_name` is the literal `"Platform-Ithina"` for every row on this table (the actor is operating with platform authority on platform-table rows).

Constraints:
- FK `tenant_id` to `core.tenants(id)` ON UPDATE RESTRICT ON DELETE RESTRICT (FK semantics allow NULL)
- CHECK `(resource_id IS NULL AND resource_label IS NULL) OR (resource_id IS NOT NULL AND resource_label IS NOT NULL)` enforces NULL-pair consistency on resource columns
- CHECK `(tenant_id IS NULL AND tenant_name IS NULL) OR (tenant_id IS NOT NULL AND tenant_name IS NOT NULL)` enforces NULL-pair consistency on tenant columns

RLS: NONE. Access gated at API layer (platform-only endpoints).

Indexes:
- `(timestamp DESC, id DESC)` : cursor pagination
- `result_type WHERE result_type != 'SUCCESS'` : partial index for failure investigation

No `tenant_id` index on this table: platform users see all rows; tenant users don't reach this table; no query pattern filters by tenant_id on the platform side.

### Why symmetric column shape

The two tables intentionally share the same 19-column shape (post Step 6.16.7; 16 columns pre-6.16.7). The alternative considered was an asymmetric design where `platform_activity_audit_logs` omits `tenant_id` and `tenant_name` entirely. The symmetric choice is taken for these reasons:

- **UNION query simplifies.** Platform users' merged-view query is `SELECT * FROM tenant... UNION ALL SELECT * FROM platform... ORDER BY timestamp DESC, id DESC LIMIT N`. No constant projection per branch; SQL reads naturally.
- **Emission code shares more.** Both tables share nearly-identical column population logic. The only difference at emission time becomes "which table to INSERT into", determined by the routing principle.
- **Tenant creation success rows carry meaningful context.** The `tenant_id` of the newly-created tenant and its `tenant_name` are populated on the platform-side row, matching what would be in a tenant-side row. The row reads more clearly.
- **Future-proof for SYSTEM actors or other actor categories.** A system-actor row (background job, automated workflow) lives uniformly across both tables without schema asymmetry as the constraint.
- **Storage overhead is negligible.** Two NULLABLE columns add roughly 1-byte NULL bitmap padding per row plus actual payload only for populated cells. At the projected 100M tenant : 1M platform row ratio, the platform-side overhead is under 1 MB. Effectively zero compared to total subsystem storage.

The trade-off accepted: rows in `platform_activity_audit_logs` have NULL `tenant_id` for non-tenant-creation actions, and readers must understand that NULL means "platform-scope, no tenant context." This is documented in the schema NULL-pair CHECK constraint and the routing principle.

### Enums

**New enum:** `core.audit_result_type_enum` with values: `SUCCESS`, `PERMISSION_DENIED`, `VALIDATION_FAILED`, `CONFLICT`, `INTEGRITY_VIOLATION`, `INTERNAL_ERROR`.

**Reused enum:** `core.actor_user_type_enum` with values: `PLATFORM`, `TENANT`. Already in schema; used for `actor_user_type` column on both audit tables.

### resource_type vocabulary (Step 6.16.5)

Open string column; values declared per-route in `AUDITED_ROUTES`. Convention: UPPERCASE_SINGULAR, table-name-derived; brevity preferred when the table-name prefix is redundant (e.g. `MODULE_ACCESS` not `TENANT_MODULE_ACCESS`).

| `resource_type` value | Source step | Underlying table |
|---|---|---|
| `TENANT` | 6.16.2 | `core.tenants` |
| `TENANT_USER` | 6.16.4 | `core.tenant_users` |
| `ROLE` | 6.16.4 | `core.roles` |
| `MODULE_ACCESS` | 6.16.5 | `core.tenant_module_access` |
| `ORG_NODE` | 6.16.5 | `core.org_nodes` |
| `STORE` | 6.16.5 | `core.stores` |

The GET endpoint's `resource_type` filter (Read contract) accepts any string; values outside this set yield zero rows. The set grows as new write surfaces ship audit emission.

### Why denormalised labels

Each row carries `actor_display_name`, `tenant_name`, `resource_label`, `action_label`, `result_label` resolved at write time. The choice is intentional and stable:

- **Read-time queries don't JOIN.** GET endpoint is one SELECT per row source. Cursor pagination stays fast.
- **Historical accuracy.** If a tenant later renames from "Buc-ee's" to "Buc-ee's Travel Centers", the historical audit row keeps "Buc-ee's". The label captures truth at the moment of the action, which is what audit reading actually wants.
- **No lookups table coupling.** Other endpoints in the project (Step 6.7+) JOIN `core.lookups` for display labels. Audit logs avoid this; each row is self-contained.
- **GDPR / retention resilience.** When an actor user is deleted (GDPR purge, employee offboarding), the audit row keeps the snapshot name. The row is still queryable; the deleted user can no longer be re-identified by JOIN to a now-empty record.

### Why enums for some columns, TEXT for others

- **Enum for `result_type`:** stable vocabulary (6 values). DB-level type discipline prevents typo'd inserts. Adding a value requires migration; this is rare and acceptable.
- **Enum for `actor_user_type`:** stable vocabulary (2 values). Reused from existing schema.
- **TEXT for `action`:** vocabulary grows as new endpoints add new actions (CREATE, UPDATE, SUSPEND, GRANT, REVOKE, etc.). Migrations for every new value would be friction. Application-side discipline ensures consistency.
- **TEXT for `resource_type`:** vocabulary grows as new tables are added (TENANT, TENANT_USER, ORG_NODE, MODULE_ACCESS, ROLE_ASSIGNMENT, STORE, ROLE, etc.). Same reasoning.

The trade-off is intentional: type discipline for stable values, flexibility for growing ones. AWS CloudTrail, Stripe, and most production audit systems use TEXT for `action`/`eventName` and accept the discipline-via-tests cost.

## Emission contract

### Synchronous emission, two transaction rules

Emission is synchronous in v0 (the request waits for the audit INSERT before returning). The transaction shape differs between success and failure paths:

**Rule 1: Success path emits in the same transaction as the data write.**

The data write and the audit row share one transaction. Both commit together. If either fails (constraint violation on the audit row, FK rejection on the data write), the whole transaction rolls back. This preserves atomicity per the original design intent: a row in the audit log implies the data change happened; absence of an audit row implies no data change happened.

Latency cost: one extra INSERT per request, roughly 1-5ms at v0 scale.

Implementation: repo methods that write data call `emit_audit_event(session, ...)` as their final step before returning. The caller (request-scope session in `get_tenant_session`) commits the whole bundle on clean handler return.

**Rule 2: Failure path emits in a separate new transaction.**

When the data write fails (CHECK constraint, FK violation, app-layer raise of a ClientError or ServerError), the transaction enters error state and is rolled back. An audit INSERT inside that transaction CANNOT commit; it would be discarded along with the data write. The audit row for the failed attempt is therefore written in a new, separate transaction opened AFTER the failed one has cleared.

This is still synchronous (the request waits for the audit emission before the error envelope returns). Atomic per-row (one INSERT statement). Just a different transaction.

Implementation: the global exception handler at `src/admin_backend/main.py` calls `emit_audit_event_in_new_transaction(engine, ...)` after the response envelope is built. The new connection is taken from the engine pool, runs the INSERT in its own autocommit block, and returns to the pool. The actor's true identity (PLATFORM / TENANT) is recorded INSIDE the audit row's `actor_user_type` column; the connection uses `app.user_type='PLATFORM'` at the GUC layer purely to satisfy the D-29 OR-branch on INSERT (the data session's GUCs are gone by the time the new connection opens).

**Why this clarification matters.**

The original wording "synchronous, same transaction" was correct for the success path but mechanically impossible on the failure path (the data transaction has already rolled back). The two-rule structure is not a deviation from the design intent; it is the canonical statement of what synchronous emission means in success vs failure flows.

**What this section does NOT specify (intentionally).**

Which endpoints are audited (lives in `AUDITED_ROUTES`, an implementation detail in `src/admin_backend/audit/emit.py`). Retry policy on failure-path emission failure (CRITICAL log only at v0; see Emission failure handling below). The exact mapping from error class to `result_type` (a code concern; the result_type taxonomy table further down enumerates the values).

### What gets logged

Logged in audit tables:
- Successful writes (POST / PATCH / DELETE / state-transition POSTs) across all resources.
- Failed writes including: PERMISSION_DENIED (403), VALIDATION_FAILED (422), CONFLICT (409), INTEGRITY_VIOLATION, INTERNAL_ERROR (500).

NOT logged in audit tables:
- Successful or denied READ endpoints. Reads are not state-changing events.
- Read denials route to application logs (Cloud Logging) instead, where Step 2.3's structured logging infrastructure handles them.

### Row granularity

One row per HTTP request, regardless of how many fields or how many related entities the request affected. A PATCH that renames a tenant user and changes their role set produces ONE audit row in `tenant_activity_audit_logs`. The `details` JSONB payload captures the multiple field changes and role-assignment diffs.

Trade-off accepted: queries that want field-level granularity (e.g., "show me every email change") must JSONB-extract. At v0 scale this is acceptable; at production scale, see Scale Considerations.

### Failure-row payload shapes

Each `result_type` has a defined `details` payload shape:

| `result_type` | `details` payload |
|---|---|
| SUCCESS | `{before: {...}, after: {...}}` (for UPDATE-shaped actions); `{snapshot: {...}}` (for CREATE-shaped actions) |
| PERMISSION_DENIED | `{required_permission, caller_audience, caller_roles}` |
| VALIDATION_FAILED | `{validation_errors: [{field, error_message}, ...]}` (no submitted values) |
| CONFLICT | `{constraint, field, value}` |
| INTEGRITY_VIOLATION | `{constraint}` |
| INTERNAL_ERROR | `{error_class, sanitised_message}` |

Submitted request bodies are NEVER stored in audit rows. Full request payloads, if needed for forensics, are routed to Cloud Logging where retention and access controls are managed separately.

#### Optional sub-keys (Step 6.16.4 convention)

Standard `result_type` shapes admit OPTIONAL sub-keys that name a specific guard or reason when multiple categories share one `result_type`. The standard sub-keys (those in the table above) remain present; the optional sub-keys augment, not replace. Reading code MUST handle absence gracefully.

Two worked examples ship at Step 6.16.4:

1. **PERMISSION_DENIED with `denial_reason`.** A 403 raised by `_raise_if_self_edit` (Step 6.10.1's handler-side guard) is mechanically a permission denial but stems from a different cause than the standard "caller lacked the gate". The audit row carries `denial_reason: "SELF_EDIT_FORBIDDEN"` alongside `required_permission`, `caller_audience`, `caller_roles`. The auditor reads the specific reason without inferring it from actor_user_id-resource_id equality.

2. **INTERNAL_ERROR with `invariant`.** A `RolesRepo.update` Layer 2 tripwire (Step 6.18.3 LD6) raises `InternalInvariantViolationError` when the post-write platform invariant check fails despite Layer 1's pre-check saying it was safe. The audit row carries `invariant: "OVERRIDE_GLOBAL_HOLDER_PRESERVATION"` (or another named guard string) alongside `error_class`, `sanitised_message`. The on-call engineer reads the specific guard that fired without grepping the error class name.

The pattern generalises. Future emission work that needs to distinguish sub-cases within a `result_type` adds an optional sub-key following this convention; the result_type table above stays unchanged. CRITICAL: the optional sub-key is supplementary signal for forensics; the row's `result_type` is still the routing key for filtering and reporting.

`resource_id` and `resource_label` are NULL on failed-create rows where the target was never assigned an identity. They are populated on failed-update rows where the target exists but the action was rejected.

### Emission failure handling

Success path (Rule 1). If the audit INSERT itself fails (rare; constraint violation due to a bug), the entire transaction rolls back. The user-facing data write also rolls back. Audit emission is load-bearing for user requests by design.

Failure path (Rule 2). If the audit INSERT itself fails on the separate new transaction (rare), the helper logs CRITICAL and returns without raising. The user-facing error envelope is the visible response; audit emission failure on the failure path is a back-end-only concern (the data change already failed, so there is nothing to roll back).

Mitigation: emission code is heavily tested. The label-resolution path (which could fail if an actor user record was deleted between auth and emission) uses defensive fallbacks (e.g., the failure-path emission falls back to "<unknown>" if a tenant name lookup misses because the tenant was deleted concurrently).

### Per-route extractor mapping (FN-AB-66 closure, Step 6.16.5)

Step 6.16.2 + 6.16.4 carried failure-path resource identification via a minimal multi-key path-param fallthrough (`tenant_id` then `user_id` then `role_id`) plus a `resource_type`-dispatched lookup for the resource label. The fallthrough scaled to five routes before it started to obscure intent.

Step 6.16.5 promotes the pattern to a per-route extractor mapping. Each audited resource_type declares one extractor function that reads its identifying path params from the Request and returns a `FailureContext` carrying `(resource_id, tenant_id_for_row, module_code, node_id, store_id)`. The failure handler resolves the route template, reads the AUDITED_ROUTES tuple's `resource_type`, consults `RESOURCE_EXTRACTORS[resource_type]`, and invokes the extractor.

Label resolution (resource_label, tenant_name) stays inside `emit_audit_event_in_new_transaction` where the new transaction can JOIN against the appropriate tables (tenants, tenant_users, roles, tenant_module_access, org_nodes, stores, lookups). The extractor's job is path-derived context only; the lookup's job is name resolution against still-present rows.

Six extractors ship in v0: `TENANT`, `TENANT_USER`, `ROLE` (the 6.16.4 set), and `MODULE_ACCESS`, `ORG_NODE`, `STORE` (the 6.16.5 set). Adding a new audited resource_type requires (a) one `AUDITED_ROUTES` entry per affected route and (b) one extractor function. The lookup dispatch inside emit grows by one branch for the new lookup table.

Two failure-path constraints to honour when adding a new resource_type:

- `ck_*_resource_pair`: `resource_id` and `resource_label` must be both-NULL or both-NOT-NULL. If the resource doesn't yet exist at failure time (e.g., POST-shape failures where the row was never created, or upsert-seam denials), the row carries both as NULL. The label resolution branch checks `resource_id is not None` before populating `resource_label`.
- POST routes that carry resource identity in the body (POST /tenants, POST /tenant-users, POST /stores) lose that identity when the failure handler runs (FastAPI consumes the body before the exception flows up). These routes' failure rows route to `platform_activity_audit_logs` with `tenant_id=NULL` per the routing principle — `route_to_platform=True` on POST /tenants; the others fall through the `tenant_id is None` branch in `emit_audit_event_in_new_transaction`.

## Read contract

### Filter parameters on the GET endpoint

The GET endpoint at Step 6.16.3 accepts:

| Parameter | Type | Notes |
|---|---|---|
| `from` | ISO-8601 timestamp | inclusive lower bound on `timestamp` |
| `to` | ISO-8601 timestamp | inclusive upper bound on `timestamp` |
| `status` | enum (single value) | matches `result_type` |
| `tenant_id` | UUID | filters by tenant; platform users only |
| `scope` | PLATFORM \| TENANT | filters merged view to one source table |
| `resource_type` | string (open vocabulary) | filters by `resource_type` column; AND-composed with the other filters; applied to both UNION branches for PLATFORM callers. Unknown values produce 0 rows naturally (no 422). Shipped at Step 6.16.5. |
| `actor_user_id` | UUID | filters by `actor_user_id` column; AND-composed; applied to both UNION branches for PLATFORM callers. TENANT callers receive the filter naturally; RLS scoping ensures they only see audit rows from their own tenant regardless of the actor_user_id supplied. Unknown UUIDs produce 0 rows naturally (no 422). UUIDs are globally unique across PLATFORM and TENANT user tables (both use `uuidv7()` DDL default), so no `actor_user_type` companion is needed. Shipped at Step 6.16.6. |
| (cursor) | opaque | base64-encoded `(timestamp, id)` tuple |
| (limit) | integer | rows per page, capped at frontend-configurable max |

Tenant users only see TENANT-scope rows from their own tenant; their endpoint accepts the same filters, but RLS + API gate enforces the constraints regardless of what they pass.

### Search behaviour

Search box matches against four columns via case-insensitive substring (`ILIKE %term%`):
- `actor_display_name`
- `action_label`
- `resource_label`
- `tenant_name`

Search composes with other filters via AND. Search results are time-bounded by `from`/`to` if those are also set, which mitigates performance impact at scale.

### Pagination

Cursor pagination using `(timestamp DESC, id DESC)` as the sort key. Sequential navigation only (Next / Previous); no jump-to-page. Reasons:

1. Performance stays constant as table grows (offset pagination degrades; cursor doesn't).
2. Audit timelines are feed-shaped UIs; sequential navigation matches user expectation.
3. New events arriving mid-paging don't shift the cursor (offset pagination drifts).

The audit log subsystem departs from the project's standard offset-based `Pagination` schema (used by tenants, stores, tenant-users, and the other list endpoints) for the reasons above. The deviation is intentional and scoped: audit log is the only table in the system with structurally unbounded growth, and offset pagination degrades visibly past ~100k rows because the database reads `offset + limit` rows to return `limit` rows. Other resources are bounded by business scope (a tenant has tens of stores, hundreds of users) and offset pagination remains the right pattern there. The `CursorPagination` response envelope is local to the audit endpoints at v0; if a second cursor-paginated endpoint ships in the future, the envelope promotes to `schemas/_common.py` or co-locates beside `Pagination` in `schemas/tenant.py`.

### Response shape (post Step 6.16.7)

`GET /audit/activities` returns items of type `AuditActivityListItem` (14 fields post-Step-6.16.7; 8 fields pre-6.16.7). The Step 6.16.7 redesign added 6 additive fields:

| Field | Type | Notes |
|---|---|---|
| `actor_organization_name` | str | actor's tenant name or `"Platform-Ithina"` literal; frozen snapshot per LD6. |
| `actor_roles` | str | comma-separated active role display names (`roles.name` values, e.g., `"Owner, Promotions Assistant"`) at the moment of the audited action; frozen snapshot per LD5. Rendered directly by the UI as the Actor role column value. |
| `what` | str | composed `"<Type label>: <resource_label>"` display string for the resource the action affected; backend composes at read time from `resource_type` + `resource_subtype` + `resource_label` per LD11. Type label mapping documented in the Display vocabulary subsection below. |
| `resource_type` | str | raw resource type enum value (TENANT, TENANT_USER, ROLE, MODULE_ACCESS, STORE, ORG_NODE). Surfaced for frontend filtering and styling. |
| `resource_subtype` | str \| None | for ORG_NODE rows, carries the `node_type` value (REGION, HQ, DEPARTMENT, etc.); NULL for non-ORG_NODE rows and pre-6.16.7 historical rows. |
| `result_type` | AuditResultType | raw result type enum (6 values: SUCCESS, PERMISSION_DENIED, VALIDATION_FAILED, CONFLICT, INTEGRITY_VIOLATION, INTERNAL_ERROR). Surfaced for frontend visual styling. |

The 8 existing fields (`id`, `timestamp`, `actor_display_name`, `action_label`, `resource_label`, `result_label`, `scope`, `tenant_name`) keep unchanged structure. The wire-shape change is additive per D-31; the detail endpoint (`AuditActivityDetail`) gains 3 of the same fields (the 3 stored columns: `actor_organization_name`, `actor_roles`, `resource_subtype`) and grows from 16 to 19 fields.

### Display vocabulary (Step 6.16.7)

**Action labels.** The `action_label` field renders one of the following user-facing strings depending on `action`:

| `action` | `action_label` |
|---|---|
| CREATE | "Created" |
| UPDATE | "Edited" |
| ENABLE | "Enabled" |
| DISABLE | "Disabled" |
| SUSPEND | "Suspended" |
| ACTIVATE | "Activated" |
| DEACTIVATE | "Deactivated" |
| CLOSE | "Closed" |
| OPEN_SOFT | "Soft-opened" (dormant; reserved for future matrix relaxation per FN-AB-68) |
| SET_STATUS | "Set status" (failure-path fallback for store set-status when target cannot be resolved) |

Step 6.16.7 LD8 changed two labels: UPDATE label flipped from "Updated" to "Edited"; SET_STATUS label flipped from "Status change" to "Set status".

**Result labels for CONFLICT rows.** For `result_type = CONFLICT`, the `result_label` composes `"Blocked - <qualifier>"` based on the specific ClientError subclass that raised. The 9 CONFLICT-mapped error classes (per Step 6.16.7 LD9):

| Error class code | Qualifier phrase |
|---|---|
| DUPLICATE_TENANT_NAME | "tenant name already exists" |
| INVALID_STATE_TRANSITION | "status change not allowed" |
| DUPLICATE_TENANT_USER_EMAIL | "email already in use for this tenant" |
| ROLE_ASSIGNMENT_CONFLICT | "role assignment conflict, please retry" |
| DUPLICATE_ORG_NODE_CODE | "code already in use for this tenant" |
| DUPLICATE_STORE_CODE | "store code already in use for this tenant" |
| ROLE_ARCHIVED | "role is archived" |
| LAST_OVERRIDE_HOLDER | "would remove the last platform admin" |
| SUPER_ADMIN_PROTECTED | "SUPER_ADMIN role is protected" |

When a CONFLICT row's `code` is not in the dispatch table, `result_label` falls back to the static "Conflict" label.

**Type labels (for `what` field composition).** The `what` field uses the following mapping from `(resource_type, resource_subtype)` to user-facing Type label per LD12:

| `resource_type` | `resource_subtype` | Type label | Example `what` |
|---|---|---|---|
| TENANT | (any) | "Tenant" | "Tenant: Buc-ee's" |
| TENANT_USER | (any) | "User" | "User: marcus@bucees.com" |
| ROLE | (any) | "Role" | "Role: Promotions Assistant" |
| MODULE_ACCESS | (any) | "Module" | "Module: Goal Console" |
| STORE | (any) | "Store" | "Store: Downtown Buc-ee's" |
| ORG_NODE | TENANT | "Tenant root" | "Tenant root: Buc-ee's" |
| ORG_NODE | BUSINESS_UNIT | "Business unit" | "Business unit: Retail Operations" |
| ORG_NODE | HQ | "HQ" | "HQ: Phoenix HQ" |
| ORG_NODE | COUNTRY | "Country" | "Country: United States" |
| ORG_NODE | REGION | "Region" | "Region: Texas Region" |
| ORG_NODE | STORE | "Store" | "Store: Downtown Buc-ee's" |
| ORG_NODE | DEPARTMENT | "Department" | "Department: Bakery Operations" |
| ORG_NODE | NULL | "Org node" (historical fallback) | "Org node: <name>" |

NULL `resource_label` (failed-create rows) renders as `"<Type label>: -"`.

### Frontend display

Layer 1 list view per the mockup pattern: one row per audit event. Columns: timestamp, actor (name + role), tenant, action, resource, scope, result. Filter pills for status (All / Success / Denied / etc.); dropdown for tenant; date range picker for from/to; search box. The Step 6.16.7 redesign surfaces actor enrichment (`actor_organization_name`, `actor_roles`), composed `what` display string, and raw enum codes for filtering/styling.

Layer 2 detail panel on click: same row info plus `details` JSONB rendered as structured fields, request_id displayed for engineering correlation, and a way to navigate to other events with the same request_id (grouping by save-action). Frontend rendering of the redesigned wire shape is out of project scope; handled by the frontend team against the deployed wire shape on their own schedule.

## Scale considerations

The v0 design intentionally trades future operational complexity for shipping speed. Synchronous emission, light indexing, no archival, all reasonable at v0 scale (hundreds-to-thousands of writes per day per tenant) but not at production scale (tens-of-thousands per day per tenant).

### Triggers for revisiting

Any one of these signals from production telemetry warrants a design revisit:

1. **Audit INSERT median time > 50ms.** Indicates index-maintenance cost or transaction-time growth becoming visible to users.
2. **Either audit table > 10M rows.** Indicates query and partition strategy needs.
3. **GET endpoint p95 latency > 500ms.** Indicates read-path performance is hurting the UI.
4. **Storage cost on Cloud SQL becoming a budget item.** Indicates archival to colder storage is needed.

### Forward options

Each is independent work; not all needed at the same time.

**1. Partition by month or quarter.**

Postgres native declarative partitioning. New rows go to the current month's partition (small, hot, fast inserts). Old partitions become read-only and queryable but archivable. Most queries hit recent partitions; planner skips old ones. Implementation is a one-time schema change; transparent to application code.

Triggered when either table grows past ~10M rows or insert latency creeps up.

**2. Asynchronous emission via outbox pattern.**

User write transaction writes user data AND a row into an `audit_outbox` table. Background worker reads outbox, writes the real audit row, deletes the outbox row. Audit completeness preserved (outbox is durable); user-facing latency drops to baseline.

Triggered when synchronous emission becomes a visible-to-users latency cost.

**3. Trigram GIN indexes on search columns.**

`pg_trgm` extension enables ILIKE substring queries to use trigram-based GIN indexes. Currently deferred (F4 decision); add when search queries are slow and frequent.

Triggered when search-related p95 latency exceeds operational comfort.

**4. JSONB GIN index on `details`.**

When use cases arise that query inside the JSONB payload (e.g., "show me every PERMISSION_DENIED where required_permission was X"), a GIN index on `details` makes those queries fast. Currently deferred (F5 decision).

Triggered when JSONB-extract queries become a regular pattern.

**5. Hot / warm / cold tiering.**

- **Hot (Postgres, ~3-6 months):** the GET endpoint queries this. Indexes optimal for live use.
- **Warm (Postgres partitions, ~6 months to ~2 years):** queryable but rare-access. Partition pruning makes them invisible to typical queries.
- **Cold (object storage, ~2+ years):** archived to Cloud Storage / S3 / equivalent in Parquet / Avro. Queryable only via dedicated tools (BigQuery, Athena) or compliance dump pipelines.

Implementation is a multi-step migration involving: partitioning the existing tables (forward option 1), pg_partman or similar for routine partition management, a job that exports old partitions to cold storage, modification of the GET endpoint to query only hot+warm.

Triggered when storage cost or retention requirements demand it.

**6. Actor filter parameter + actor_user_id BTREE index.**

The `?actor_user_id=` filter parameter shipped at Step 6.16.6 (closes FN-AB-69). The BTREE index remains deferred: v0 scale is sub-millisecond on sequential scan; add the index when row counts or query latency justify.

Triggered when monitoring shows actor-filtered queries on the hot path.

### Retention policy

Not yet defined. Industry conventions vary:

- SOX-regulated financial systems: 7 years.
- HIPAA: 6 years.
- Some retail compliance regimes: 3-5 years.
- Consumer products (non-regulated): 90 days to 1 year.

For Ithina v0, retention is effectively "indefinite" (no archival, no purge). When telemetry justifies tiering, retention policy gets defined alongside.

## Open questions and deferred decisions

### Sensitive platform-on-tenant action hiding

There may be cases where platform-staff actions affecting a tenant are sensitive (forensic investigation, compliance hold, internal flagging) and should NOT appear in the tenant's audit log. Three structural options identified (visibility flag column; route-by-policy; dual-write with flag); all deferred pending real product requirements.

Current v0 posture: all tenant-affecting actions appear in the tenant's audit log regardless of actor. Tenant OWNERs see platform-staff actions on their data.

Trigger to revisit: a product / security / compliance requirement surfaces a real "hide from tenant" use case.

### CSV export

The audit log GET endpoint's CSV export functionality (mockup shows "Export CSV" button) deferred. Recommended shape when added: `GET /audit/activities/export` with same filters as GET; streaming response; 10K row cap; truncate with `X-Result-Truncated` header. Estimated half-day implementation.

Trigger: tenant or platform user need for bulk export beyond paginated reads.

### Actor filter parameter

Shipped at Step 6.16.6: GET `/api/v1/audit/activities` accepts an optional `?actor_user_id=<uuid>` query parameter, AND-composed with the other filters, applied to both UNION branches for PLATFORM callers (RLS scopes TENANT callers naturally). Closes FN-AB-69. The BTREE index on `actor_user_id` remains deferred per scale option 6 above (v0 scale sub-millisecond on sequential scan).

### Permission tuples

The GET endpoint's gate tuples (`ADMIN.AUDIT.VIEW.TENANT`, `ADMIN.AUDIT.VIEW.GLOBAL` or final-named equivalents) are NOT in the permission catalogue yet. They land at Step 6.16.3 as part of the GET endpoint's commit. Same operator-pattern as ORG_NODES catalogue addition at Step 6.13: Excel + local DB + Cloud SQL UPSERT in lockstep.

## Sub-step plan

The v0 live write surface is **16 endpoints across 6 resource families**: tenants (4), tenant-users (4), module-access (2), org-tree (2), stores (3, shipped at Step 6.17.3/6.17.4 with explicit audit deferral), and roles PATCH (1, shipped at Step 6.18.3 with explicit audit deferral). Step 6.16.0's original framing cited 12 endpoints across 4 resource families; the stores + roles endpoints landed post-6.16.0 and are folded back into the emission plan by amending the 6.16.4 and 6.16.5 scopes below.

| Sub-step | Scope | Status |
|---|---|---|
| 6.16.0 | This design document (pre-step) | DONE-LOCAL (this commit) |
| 6.16.1 | Schema design: 2 tables, 1 new enum, RLS, indexes, ORM models, tests. No app code. | DONE-LOCAL |
| 6.16.2 | Audit emission for tenants endpoints (POST, PATCH, suspend, activate) | DONE-LOCAL |
| 6.16.3 | GET endpoint for the frontend audit timeline + permission tuples | DONE-LOCAL |
| 6.16.4 | Audit emission for tenant-users (4 endpoints) + roles PATCH (1 endpoint) | DONE-LOCAL |
| 6.16.5 | Audit emission for module-access (2) + org-tree (2) + stores (3) + GET resource_type filter + FN-AB-66 extractor closure | DONE-LOCAL |
| 6.16.6 | GET `/audit/activities` `actor_user_id` filter (closes FN-AB-69) | DONE-LOCAL |

**6.16 series complete.** All 16 v0 write endpoints across 6 resource families (tenants 4 + tenant-users 4 + module-access 2 + org-tree 2 + stores 3 + roles 1) emit synchronous audit rows on success and failure paths. The audit subsystem reads via `GET /api/v1/audit/activities` (list + detail) per 6.16.3, with optional `resource_type` filter per 6.16.5 and optional `actor_user_id` filter per 6.16.6. The per-route extractor mapping (FN-AB-66 closure) is the maintenance surface for adding a new audited resource_type. Step 6.16.6 followed up post-closure with the actor filter required for frontend drawer integration (PlatformUserDetailDrawer.Activity, TenantUserDetailDrawer.Activity, RecentActivityPanel).

Each sub-step's step doc (under `docs/implementation-steps/`) references back to this design document instead of redocumenting principles. This document is updated at each sub-step's commit if implementation surfaces a design correction or addition.

## Related documents

- `docs/architecture_RBAC.md` : RBAC subsystem; D-29 OR-branch RLS pattern reused here.
- `docs/implementation-steps/step-6_8_1-split-user-role-assignments-2026-05-08.md` : architectural template for the two-table split with mixed RLS posture.
- `CLAUDE.md` : running record of project decisions, conventions, and FN-AB items.
- `BUILD_PLAN.md` : step-level plan including the Step 6.16 sub-step split.
