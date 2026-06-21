# Step 6.16.3 : Audit log read endpoints (list + detail)

## Plan

Ship two GET endpoints serving the audit log timeline UI (Frontend spec, sidebar entry "Audit Log" under "GOVERNANCE"). The endpoints are the read counterpart to the emission landed at 6.16.2; together they close the schema -> emission -> read loop for the tenants-endpoint slice. Sub-steps 6.16.4 and 6.16.5 extend emission to the remaining write surfaces.

Authoritative reference: `docs/architecture_audit_logs.md` (Read contract, Schema, Routing principle). Implementation against the 19 locked decisions (LD1-LD19) in the impl prompt.

Deliverables shipped:

1. NEW `src/admin_backend/schemas/audit_log.py`: 4 schemas (`CursorPagination`, `AuditActivityListItem`, `AuditActivitiesListResponse`, `AuditActivityDetail`).
2. NEW `src/admin_backend/repositories/audit_logs.py` (~330 lines): `AuditLogsRepo` with `list` + `get_by_id`; `_encode_cursor` / `_decode_cursor`; per-branch SQL builders; `AuditActivityDetailRow` dataclass; `ListResult` dataclass.
3. NEW `src/admin_backend/routers/v1/audit.py`: 2 handlers (`list_audit_activities`, `get_audit_activity`); 2 mappers (`_list_item_from_row`, `_detail_from_row`); cursor shape guard regex constant.
4. MODIFY `src/admin_backend/main.py`: register the new router.
5. MODIFY `src/admin_backend/errors.py`: 2 new ClientError subclasses (`AuditEventNotFoundError` 404, `InvalidCursorError` 422).
6. MODIFY `src/admin_backend/schemas/__init__.py`: re-export the 4 new schemas.
7. MODIFY `tests/integration/conftest.py`: 2 new factories (`make_tenant_activity_audit_log`, `make_platform_activity_audit_log`).
8. NEW `tests/integration/test_audit_router.py` (25 tests: L1-L15, D1-D7, P1-P3).
9. NEW `tests/integration/test_audit_logs_repo.py` (8 tests: R1-R8).
10. NEW `tests/unit/test_audit_log_schemas.py` (4 tests: S1-S4).
11. MODIFY `tests/integration/test_seed_loader.py`: `EXPECTED_VISIBLE_COUNTS_PLATFORM` updated to match the operator's pre-prompt catalogue update (permissions 36 -> 37, role_permissions 132 -> 131).
12. MODIFY `scripts/smoke_curl.sh`: +3 audit smoke probes (WHAT'S CHECKED 64 -> 67).
13. MODIFY `scripts/test_endpoints.sh` + `scripts/test_endpoints_cloud.sh`: 4 audit entries inside `run_matrix_for_caller` (16 new per-caller calls across 4 callers).
14. MODIFY `docs/architecture_audit_logs.md`: inline note in Read contract > Pagination explaining the cursor vs offset deviation; Step 6.16.3 sub-step status flipped DONE-LOCAL.
15. REGEN `docs/endpoints/openapi.json`: +2 paths, +4 schemas.
16. NEW `docs/endpoints/audit.md`: 8-section format for both endpoints.
17. MODIFY `BUILD_PLAN.md`: Step 6.16.3 status flip TODO -> DONE-LOCAL.
18. MODIFY `CLAUDE.md`: 1-line pointer above the Step 6.16.2 entry; new FN-AB-64 entry per LD18.
19. NEW step doc (this file).
20. NEW prompt file bundled with the commit.

## Mental Model

### Two endpoints, one repo, audience-dispatched

`AuditLogsRepo.list` dispatches on `auth.user_type` per LD1. TENANT callers query `tenant_activity_audit_logs` only; RLS does the tenant_id scoping via the D-29 OR-branch policy. PLATFORM callers query a UNION ALL across both tables, with the synthesised `scope` column distinguishing the source. The router handler is audience-agnostic; branching lives in the repo.

`AuditLogsRepo.get_by_id` probes both tables sequentially: tenant first (RLS-scoped), then platform (no RLS). Returns `None` on miss; the router converts to 404 `AUDIT_EVENT_NOT_FOUND`. The detail handler adds one extra check: TENANT callers fetching a PLATFORM-scope row collapse to 404 too, matching the design doc's read principle ("tenant users never see platform-scope rows").

### Cursor pagination is local to audit

The project's other list endpoints (tenants, stores, tenant-users) use offset-based `Pagination` from `schemas/tenant.py`. The audit log subsystem deviates because it's the only table with structurally unbounded growth, and offset degrades visibly past ~100k rows. `CursorPagination` lives in `schemas/audit_log.py` at v0 per LD4 (the prompt's Surface-and-stop confirmed `schemas/_common.py` does not exist; co-locating beside `Pagination` in `schemas/tenant.py` was rejected to avoid expanding that module's surface). If a second cursor-paginated endpoint ships, the envelope promotes.

### Cursor encoding: opaque base64 of JSON

`base64.urlsafe_b64encode(json.dumps({"ts": <iso8601>, "id": <uuid>}))`: opaque to the client, cheap to decode, validates per failure mode. The decoder collapses every malformed shape (bad base64, bad JSON, missing keys, bad ts, bad UUID) under one `InvalidCursorError` (422). The encoded payload is the LAST row of the current page; decoding it re-anchors the next page at "rows strictly older than that anchor" via the tuple compare `(timestamp, id) < (cursor_ts, cursor_id)`.

`limit + 1` rows fetched; if `limit + 1` came back, the extra row is dropped and `has_more=True`.

### Two failure modes for cross-tenant probes; same code

A TENANT JWT probing an audit row from another tenant can hit two paths:

1. The row lives in `tenant_activity_audit_logs` but belongs to another tenant. RLS filters it from the tenant probe; the platform probe also misses (the row isn't in the platform table). Returns 404 `AUDIT_EVENT_NOT_FOUND`.
2. The row lives in `platform_activity_audit_logs`. RLS does not apply there; the platform probe DOES find the row. The router-level check on `scope == 'PLATFORM' AND auth.user_type == 'TENANT'` then collapses to 404.

Same code in both cases, preserving D-17's anti-information-disclosure posture. The D5 test exercises path 2; D4 exercises path 1.

### `tenant_id` and `scope` filters: PLATFORM-only

Per LD14 / LD15, both filters are silently ignored for TENANT callers (RLS already scopes their visibility). The repo's dispatch on `user_type` reaches the TENANT branch SQL which doesn't accept either filter; PLATFORM SQL accepts both. No router-side branching needed.

### Audience-driven UNION shape

For PLATFORM callers, the SQL composes both branches inside a CTE then applies the cursor predicate + ORDER BY + LIMIT at the outer level so the merged stream's ordering is consistent. When `scope=PLATFORM` or `scope=TENANT` is set, the SQL elides the other branch entirely; PG doesn't read rows from the table that won't contribute.

### Catalogue precondition + operator-driven UPSERT

Per the impl prompt, the catalogue UPSERT is operator's responsibility: +1 permission `ADMIN.AUDIT_LOG.VIEW.GLOBAL`; revoke `.VIEW.TENANT` from SUPER_ADMIN / PLATFORM_ADMIN / SUPPORT_ADMIN; grant `.VIEW.GLOBAL` to the same 3 platform roles. Tenant-side `.VIEW.TENANT` grants on 8 tenant roles (OWNER, PRICING_MANAGER, STORE_MANAGER, CATEGORY_MANAGER, FINANCE_ADMIN, COMPLIANCE_OFFICER, PROMOTIONS_MANAGER, DATA_ANALYST) stay as-is per operator decision. Four tenant roles deliberately have NO grant: ASSOCIATE, NIGHT_SHIFT_LEAD, PERISHABLES_LEAD, REGIONAL_DIRECTOR.

Pre-flight Check #4 verified the live DB: `permissions=37`, `role_permissions=131`, all expected grants present. The seed Excel was updated by the operator to match.

`test_seed_loader.py` `EXPECTED_VISIBLE_COUNTS_PLATFORM` updated from `36, 132` to `37, 131` to reflect the live state post-reseed.

### Test fixture pattern

Audit-row factories at `tests/integration/conftest.py` use the raw-SQL INSERT pattern established by `make_tenant_user` / `make_org_node`. Both insert under the PLATFORM session (the D-29 OR-branch admits the WITH CHECK on `tenant_activity_audit_logs`; `platform_activity_audit_logs` has no RLS). DELETE in teardown. Fixture order: list factories AFTER `make_tenant` in test signatures so audit-row DELETEs run BEFORE tenant DELETEs (FK ON DELETE RESTRICT).

The L/D/R-series tests inject rows directly via the factories rather than driving emission through real POST/PATCH/etc. requests; cleaner and faster than running the whole emission flow per test.

### Mandatory-gate-discipline coverage

`test_gate_discipline.py` already enumerates every `APIRoute` dynamically; the two new audit routes are picked up automatically as gated routes (carry the `__permission_gate__` marker via `require(...)`). The pytest count stays at 2 (the file has 2 dynamic-enumeration functions, not parametrized cases); the enumerated route count grows by +2 internally. No file modification needed for the meta-test; the discipline holds by construction.

## Retro

### Iteration 1: dataclass enum typing for mypy

Initial `AuditActivityDetailRow` dataclass typed `actor_user_type` and `result_type` as `str` because the SQL uses `::text` casts. mypy then complained at the router boundary when feeding into Pydantic schemas (which expect typed enums). Fix: dataclass fields typed as `ActorUserType` and `AuditResultType`; `_row_to_dataclass` coerces string -> enum at the boundary. One source of truth: the dataclass carries enums; the SQL produces strings; the converter bridges. Cleaner than per-call coercion at every consumer.

### Iteration 2: URL-encoding for `+00:00` in datetime query params

L10 (`?from=<iso>&to=<iso>`) initially failed with 422 because the `+00:00` timezone offset URL-decodes to a space, which then fails FastAPI's datetime parsing. Fix: `urllib.parse.quote` on the ISO strings in the test body. The production frontend's HTTP client will handle the encoding automatically (every modern fetch/axios library does); the test had to encode manually because the curl-shaped string interpolation doesn't.

### Iteration 3: Cursor shape guard regex

Added a router-level Pydantic `pattern=` constraint on the `cursor` Query parameter (`^[A-Za-z0-9_=-]+$`, max_length 1024) as a cheap early reject before the repo decoder runs. The repo's `InvalidCursorError` is still the authoritative failure path for semantic-shape issues (bad JSON, missing keys, bad ts/UUID); the regex catches pathologically-malformed strings before decoding starts. Belt-and-suspenders; minimal cost.

**Envelope-shape divergence.** The regex matches the URL-safe base64 alphabet (`[A-Za-z0-9_-]` plus optional `=` padding). A cursor that decodes to gibberish but happens to be URL-safe-base64-shaped still passes the regex and hits the repo decoder, which raises `InvalidCursorError` per its 5 failure-category dispatch; the wire response is the project envelope `{code: "INVALID_CURSOR", message, details, request_id}`. A cursor with `+`, `/`, whitespace, or punctuation outside the URL-safe alphabet gets rejected at the FastAPI layer with FastAPI's default 422 envelope (the `code` field will be the Pydantic validation default rather than `INVALID_CURSOR`). The envelope-shape divergence on shape-malformed cursors is acceptable for v0; this is the same class of issue as FN-AB-63 (Pydantic `RequestValidationError` bypasses the project envelope), and resolves together when that scope-decision step lands. The L13 test asserts the project-envelope path (the dominant failure mode in practice, since real clients always emit URL-safe-base64 from any standard library).

### Iteration 4: TENANT cross-audience 404 check at the router

The repo's `get_by_id` probes tenant table first, then platform. RLS handles the tenant-side cross-tenant case. But a TENANT caller probing a row that lives in `platform_activity_audit_logs` would find it (no RLS on the platform table). The repo doesn't know the caller's audience; the router does. Added an explicit check at the router: `if row.scope == 'PLATFORM' and auth.user_type == 'TENANT': raise AuditEventNotFoundError`. Two failure modes (cross-tenant on tenant table; cross-audience on platform table) collapse to the same 404 code per D-17.

The alternative (passing `user_type` into `get_by_id` and short-circuiting in the repo) was rejected because it would duplicate the router's check logic into the repo; the repo's job is to project DB rows, not to enforce read-access rules. The router is the right home for the read-principle check.

**Wire-level invariant.** Two different "TENANT caller probing a row they shouldn't see" cases collapse to one wire code. (1) Row exists in `tenant_activity_audit_logs` for another tenant: RLS hides it; the tenant probe returns nothing; the platform probe also misses (row isn't in the platform table); the repo returns `None`; the router raises `AuditEventNotFoundError`. (2) Row exists in `platform_activity_audit_logs`: RLS doesn't apply; the platform probe finds it; the repo returns the row; the router's cross-audience check fires and raises the same error. From the caller's wire perspective, both look identical (404 `AUDIT_EVENT_NOT_FOUND`), preserving D-17's anti-information-disclosure posture: a TENANT caller cannot distinguish "row exists but I'm not allowed to see it" from "row doesn't exist." The D4 test exercises path 1; D5 exercises path 2.

**Future-shape note for impersonation.** When Stage 3 impersonation lands (FN-AB-23 territory), a PLATFORM caller impersonating a tenant may need to see only the impersonated tenant's audit log. The simplest extension to that shape is the same router-level check pattern: `if impersonating and row.tenant_id != impersonated_tenant_id: raise AuditEventNotFoundError`. The router-not-repo placement makes this extensible without touching the repo's SQL: the repo continues to project rows the caller's audience admits (governed by RLS + the D-29 OR-branch); the router applies the impersonation-scope mask on top. The pattern generalises uniformly to the list endpoint (`AuditLogsRepo.list` accepts a `tenant_id` filter today for PLATFORM callers; impersonation would pin that filter to the impersonated tenant rather than leaving it free).

### Iteration 5: Cleanup-fixture for audit rows in test_audit_emission_tenants

The Step 6.16.2 `cleanup_tenants_for_audit` fixture already DELETEs audit rows before tenant DELETEs (FK ON DELETE RESTRICT). The 6.16.3 router tests using the factory fixtures get cleanup for free via the factories' own teardown. No new cleanup ceremony needed.

### Per-resource regression checkpoint

| File | Before | After |
|---|---|---|
| `test_tenants_writes_router.py` | 33 | 33 |
| `test_tenant_users_writes_router.py` | 44 | 44 |
| `test_stores_writes_router.py` | 24 | 24 |
| `test_org_tree_writes_router.py` | 29 | 29 |
| `test_module_access_writes_router.py` | 14 | 14 |
| `test_audit_log_schema.py` | 8 | 8 |
| `test_audit_log_models.py` | 5 | 5 |
| `test_audit_emit.py` | 6 | 6 |
| `test_audit_emission_tenants.py` | 10 | 10 |
| `test_audit_emission_failures.py` | 11 | 11 |
| `test_rbac_router.py` | 32 | 32 |
| `test_me_router.py` | 19 | 19 |
| `test_gate_discipline.py` | 2 | 2 |
| `test_seed_loader.py` | 5 | 5 |

`test_gate_discipline.py` pytest count stays at 2 (functions); the enumerated route count grew by +2 internally, covered by the existing dynamic test.

Test catalogue this step: 37 new tests (25 + 8 + 4). LOAD-BEARING: 12 (8 router + 2 repo + 0 schema; +2 for D4/D5 = explicit cross-tenant/cross-audience 404 assertions).

### Pytest delta

729 -> 766 (+37). mypy strict clean on 82 source files (was 79; +3 new modules). check_setup 36/36. smoke_curl 64 -> 67 (+3). OpenAPI: +2 paths, +4 schemas.

### Locked-decision honour record

All 19 LDs honoured. LD13 cursor encoding: opaque base64 of JSON `{ts, id}`. LD14 / LD15 tenant_id and scope filters silently ignored for TENANT callers. LD16 mandatory-gate-discipline: covered by the existing dynamic test (no file modification needed; pytest count unchanged). LD17 design doc inline note: added to Read contract > Pagination section. LD18 FN-AB-64: filed.

### Forward notes opened

- FN-AB-64 per LD18: uniform 4-column search rationale + observation that `ADMIN.AUDIT_LOG.VIEW.TENANT` is granted broadly during v0 (8 of 11 tenant roles per operator decision; cleanup deferred to v0 staging). SUPPORT_ADMIN holds `.VIEW.GLOBAL` post-UPSERT (was `.VIEW.TENANT` pre-step), so they see the merged platform-wide view via the repo's `user_type='PLATFORM'` dispatch.

### Cloud deploy gating

The catalogue UPSERT is bundled with the Phase 6 deploy cycle for 6.16.0 + 6.16.1 + 6.16.2 + 6.16.3. Cloud SQL has pre-existing drift (133 rows vs local 131 per the impl prompt note); captured for v0 staging cleanup, not blocking 6.16.3.

### What this step does NOT close

- Frontend integration: deferred to within 24 hours of cloud deploy per the impl prompt's Coordination section.
- CSV export: deferred per design doc Open Questions.
- Indexes (trigram / JSONB GIN / actor BTREE), partitioning, async emission: deferred per Scale Considerations.
- Pydantic-direct 422 audit emission: deferred per FN-AB-63.
- Tenant-role catalogue cleanup (the over-granted `.VIEW.TENANT` on 8 tenant roles): deferred per FN-AB-64 to v0 staging.
