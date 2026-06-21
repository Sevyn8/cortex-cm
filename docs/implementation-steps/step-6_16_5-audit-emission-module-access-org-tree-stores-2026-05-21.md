# Step 6.16.5 : Audit emission for module-access + org-tree + stores + GET resource_type filter (2026-05-21)

> Status: DONE-LOCAL. Cloud deploy batched per Phase 5.5; closes the 6.16 series.

## Mental model

Step 6.16.5 is the closing sub-step of the audit subsystem. It wires synchronous emission into the remaining 7 v0 write endpoints (module-access enable/disable, org-tree add-node/edit-node, stores create/update/set-status), extends the GET `/audit/activities` endpoint with a `resource_type` filter, and promotes the failure-path resource-identification logic from a multi-key path-param fallthrough into a per-route extractor mapping (FN-AB-66 closure).

Three structural shifts ship in this step:

1. **No-op idempotent emission policy (LD2 / FN-AB-42 closure).** Module-access enable/disable's no-op cells (enable-on-ENABLED, disable-on-DISABLED) now emit ZERO audit rows. The 6.16.4 invariant "one row per HTTP request" refines to "at most one row per HTTP request" — emission is conditional on a state change or a failure outcome. Tenants suspend/activate continues to return 409 on no-op (asymmetric and intentional; the audit posture mirrors the API posture per FN-AB-42's resolution choice b).

2. **Per-target action vocabulary for stores set-status (LD3).** Each `target_status` maps to one action code on the SUCCESS path: OPEN_SOFT, ACTIVATE, CLOSE, DEACTIVATE. The failure path uses single fallback `SET_STATUS` because the failure handler can't re-parse the body to determine `target_status` (FastAPI consumes it before the exception flows up). OPEN_SOFT is reserved but currently unreachable (no TRANSITION_MATRIX cell allows `*->OPENING`); FN-AB-68 tracks the reservation.

3. **Per-route extractor mapping (LD12 / FN-AB-66 closure).** The 6.16.4 multi-key path-param fallthrough (`tenant_id` -> `user_id` -> `role_id`) and resource_type-dispatched resource_label lookup get refactored into a sibling-dict `RESOURCE_EXTRACTORS` keyed by resource_type (shape b per operator decision). Six extractors: TENANT, TENANT_USER, ROLE (6.16.4 set) + MODULE_ACCESS, ORG_NODE, STORE (6.16.5 set). Each returns a `FailureContext` carrying `(resource_id, tenant_id_for_row, module_code, node_id, store_id)`; the failure handler forwards these plus auxiliary kwargs to `emit_audit_event_in_new_transaction`, which dispatches its label-lookup logic on resource_type.

`actor_display_name` continues to use `auth.email` per the 6.16.2/6.16.4 posture (FN-AB-67 stays open). LD9's module-access label resolves from `core.lookups` (3rd lookup pattern after `core.tenants` for TENANT/TENANT_USER and `core.roles` for ROLE).

## Implementation plan (as shipped)

### Bucket 1 : `src/admin_backend/audit/emit.py`

- AUDITED_ROUTES gains 7 entries (all `route_to_platform=False`):
  - module-access enable / disable -> `ENABLE` / `DISABLE`, `MODULE_ACCESS`
  - org-tree POST / PATCH -> `CREATE` / `UPDATE`, `ORG_NODE`
  - stores POST / PATCH / set-status -> `CREATE` / `UPDATE` / `SET_STATUS`, `STORE`
- `_ACTION_LABELS` gains 6 entries: `ENABLE` -> "Enabled", `DISABLE` -> "Disabled", `OPEN_SOFT` -> "Soft-opened", `CLOSE` -> "Closed", `DEACTIVATE` -> "Deactivated", `SET_STATUS` -> "Status change". `ACTIVATE` reused from 6.16.4.
- `emit_audit_event_in_new_transaction` signature extended with 3 optional kwargs: `module_code: str | None`, `node_id: UUID | None`, `store_id: UUID | None`. The resource_type lookup dispatch grows with 3 new branches:
  - **MODULE_ACCESS** : single SELECT joining `tenant_module_access` (by tenant_id + module_code -> tma.id), `tenants` (-> tenant_name), `lookups` (list_name='module_code', code=module_code -> display_name). Parameter typing required two distinct binds for `module_code` (`CAST(... AS module_code_enum)` and `CAST(... AS text)`) because psycopg infers parameter type from the first occurrence; surfaced during test verification.
  - **ORG_NODE** : two SELECTs — `org_nodes.name` by `(id, tenant_id)` for resource_label (when node_id is known); `tenants.name` for tenant_name.
  - **STORE** : one JOIN — `stores -> tenants` returning store_name + tenant_id + tenant_name (when store_id is known). Triggers the post-lookup table-routing re-evaluation (STORE row routes to tenant table once JOIN populates tenant_id).
- `ck_*_resource_pair` guard: when `resource_id` stays None (e.g., MODULE_ACCESS auth-deny with no pre-existing tma row), `resource_label` is also left None so the CHECK is satisfied. The lookup branch sets `resource_label` only when `resource_id is not None` post-resolution.
- Builders unchanged: `build_success_details_for_create` and `build_success_details_for_update` already accept arbitrary dict snapshots. Repo callers compose the `org_node_created_atomically` (LD6), `parent_org_node_name` (LD5), `org_node_name` (LD9 store side), and other resource-specific keys directly in their snapshot dicts; `_json_safe` coerces UUIDs / datetimes through unchanged.

### Bucket 2 : `src/admin_backend/main.py`

- New `FailureContext` frozen dataclass: 5 fields (`resource_id`, `tenant_id_for_row`, `module_code`, `node_id`, `store_id`), all default-None. Carries path-derived context to emit; auxiliary fields (`module_code`, `node_id`, `store_id`) enable emit's lookup dispatch.
- New module-level `RESOURCE_EXTRACTORS: dict[str, Callable[[Request], FailureContext]]` keyed by resource_type. Six extractors (six small functions; each ~5 lines):
  - `_extract_tenant` — path `tenant_id` as both resource_id and tenant_id_for_row
  - `_extract_tenant_user` — path `user_id` as resource_id (emit's JOIN back-fills tenant_id)
  - `_extract_role` — path `role_id` as resource_id (catalogue is platform-scope; tenant_id stays None)
  - `_extract_module_access` — path `tenant_id` + `module_code`; resource_id stays None (emit resolves tma.id by lookup)
  - `_extract_org_node` — path `tenant_id` + `node_id`; resource_id = node_id (None on POST add-node)
  - `_extract_store` — path `store_id`; emit's JOIN back-fills tenant_id
- `_parse_uuid_or_none` helper for UUID-or-None path-param parsing.
- `_emit_failure_audit_if_audited` rewritten: resolves AUDITED_ROUTES entry, looks up extractor by resource_type, invokes it, forwards `FailureContext` fields to emit. 6.16.4's `_failure_result_and_details(... auth=...)` extension preserved as-is. The 404 anchor-skip branch unchanged.
- Existing AF/RF series tests pass unchanged (load-bearing regression check — the rewrite must preserve back-compat for the 3 pre-existing resource_types).

### Bucket 3 : `src/admin_backend/repositories/modules_access.py`

- New imports: `AuthContext`, `AuditResultType`, `emit_audit_event`, `build_success_details_for_update`.
- `enable / disable` each gain optional kwargs `auth: AuthContext | None = None, request_id: UUID | None = None`. Mixing one without the other raises ValueError; both omitted skips emission cleanly.
- `enable` tracks `before_status` across the upsert-or-update branches: `None` for first-time INSERT, `"DISABLED"` for re-enable, `"ENABLED"` for the no-op idempotent path (which RETURNS early without emitting per LD2).
- `disable` emits the DISABLE row when state was ENABLED; no emission on the DISABLED-already idempotent no-op.
- New private helper `_lookup_tenant_and_module_labels`: one SELECT with two correlated subqueries (tenants.name, lookups.display_name) returning `(tenant_name, module_label)`.

### Bucket 4 : `src/admin_backend/repositories/org_nodes.py`

- New imports: `AuditResultType`, `build_success_details_for_create`, `build_success_details_for_update`, `emit_audit_event`.
- `_NodeRow` dataclass extends with `name: str` and `code: str` so `edit_node` can build before/after diffs without a second SELECT. `_select_for_update_node`'s SELECT extended accordingly.
- `add_node` gains optional `request_id: UUID | None = None`. `auth` stays mandatory (load-bearing for the audit-actor pair on INSERT). When `request_id` is provided, emits one CREATE row with snapshot carrying `id, name, code, node_type, path, parent_id, parent_org_node_name (frozen), status`.
- `edit_node` gains optional `request_id: UUID | None = None`. Builds before/after diffs only on actually-changed fields (`name`, `code`, `parent_id`). When `parent_id` changed, both halves carry `parent_org_node_name` (old and new resolved by `_lookup_parent_name`). Skips emission cleanly if the diff is empty (e.g., a PATCH that sets `name` to its current value — Phase 1 Q9 / LD4 contract).
- Two new private helpers: `_lookup_tenant_name` (for the audit row's tenant_name), `_lookup_parent_name` (returns None when parent_id is None).
- Action stays `UPDATE` regardless of which fields changed (LD4).

### Bucket 5 : `src/admin_backend/repositories/stores.py`

- New imports: `build_success_details_for_create`, `build_success_details_for_update`, `emit_audit_event`, `AuditResultType`.
- New module-level constant `_TRANSITION_ACTION_BY_TARGET: dict[StoreStatus, str]` mapping target_status to per-target action code (LD3).
- `create / update / transition` each gain optional `request_id: UUID | None = None`. `auth` stays mandatory.
- `create` emits one CREATE row when `request_id` provided. Snapshot carries store identity fields + `org_node_id` + `org_node_name` (from the just-created paired org_node) + `org_node_created_atomically: True` per LD6.
- `update` SELECT for current row extended to capture all 9 mutable fields plus tenant_id + org_node_id. Builds before/after diff filtered to actually-changed fields. When `parent_org_node_id` is in `fields`, the diff includes the new parent_org_node_id + name (the old parent_id is not captured pre-cascade for v0 simplicity; the audit row reads `before.parent_org_node_id=null` + the new parent's name as `after`; documented inline).
- `transition` emits with the per-target action code per LD3. Standard transition-shape payload `{before: {status}, after: {status}}`.
- New private helper `_lookup_org_node_name(session, tenant_id, node_id)`.

### Bucket 6 : Routers (`modules_access.py`, `org_tree.py`, `stores.py`)

- Add `Request` import to each; insert `request: Request` parameter before the `Depends(require(...))` line on every modified endpoint (6 endpoint signatures + 1 audit endpoint).
- Pass `auth=auth, request_id=request.state.request_id` (modules-access; auth stays mandatory) or `request_id=request.state.request_id` (org-tree + stores; auth is already passed) into the repo calls.

### Bucket 7 : `src/admin_backend/routers/v1/audit.py` + `repositories/audit_logs.py` (LD17)

- Audit router gains `resource_type: str | None = Query(None, max_length=64, ...)` query param at the list endpoint; threaded into `AuditLogsRepo.list`.
- `AuditLogsRepo.list` signature gains `resource_type: str | None = None`. The shared params dict carries the value. Both SQL builders extend their WHERE clause with `AND (CAST(:resource_type AS text) IS NULL OR resource_type = CAST(:resource_type AS text))`. The merged-view PLATFORM branch's `common_where` fragment grows by the same clause.

### Bucket 8 : `tests/unit/test_audit_emit.py` (+2)

- AE10: CREATE snapshot survives the new optional sub-keys (`org_node_created_atomically: True`, `parent_org_node_name`) through `_json_safe`; UUID + datetime coercion preserved. Verifies the caller-builds-dict pattern works for the 6.16.5 new keys without builder signature changes.
- AE11 (LOAD-BEARING): `_label_for_action` returns the LD3 labels for OPEN_SOFT, ACTIVATE, CLOSE, DEACTIVATE, plus ENABLE, DISABLE, SET_STATUS.

### Bucket 9 : `tests/integration/test_audit_emission_module_access.py` (NEW, +12)

MS1-MS7 (success) + MF1-MF5 (failure). LOAD-BEARING:
- MS1: enable on missing -> first-time INSERT with `before.status=null`.
- MS2: enable on DISABLED -> `before.status="DISABLED"`.
- MS3: enable on ENABLED -> ZERO audit rows (closes FN-AB-42).
- MS5: disable on DISABLED -> ZERO audit rows (symmetric).
- MS6: resource_label resolves to `"Goal Console"` (display_name from lookups), not `"GOAL_CONSOLE"` (the enum code).
- MF1: TENANT JWT enable -> 403 PLATFORM_AUDIENCE_REQUIRED + failure row. `resource_id` and `resource_label` both NULL (no tma row exists yet; CHECK constraint).
- MF3: disable on missing -> 404 + ZERO audit rows (anchor-404 not audited).

### Bucket 10 : `tests/integration/test_audit_emission_org_tree.py` (NEW, +12)

OS1-OS7 (success) + OF1-OF5 (failure). LOAD-BEARING:
- OS1: add-node CREATE row carries `parent_org_node_name` frozen in snapshot.
- OS3: edit-node reparent carries `parent_id + parent_org_node_name` in both before/after halves.
- OS4: multi-field PATCH (rename + recode) emits ONE row with action=UPDATE per LD4 (not per-field action codes).
- OF1: parent-not-found -> 404 + ZERO audit rows.
- OF2: duplicate-code -> 409 CONFLICT row.

### Bucket 11 : `tests/integration/test_audit_emission_stores.py` (NEW, +14; SS5 dropped + SS1/SS2 consolidated)

Adjustments per operator authorisation:
- SS1/SS2 merged into a single SS-atomic test (consolidation: 6.21.2 atomic-pair is the only POST /stores path; `org_node_created_atomically=True` always).
- SS5 (target=OPENING) dropped (no TRANSITION_MATRIX cell allows `*->OPENING`; FN-AB-68 reserves the label).

LOAD-BEARING:
- SS-atomic: every POST /stores carries `org_node_created_atomically: True` in snapshot.
- SS-close: target=CLOSED -> action=CLOSE / label="Closed".
- SS-activate-from-closed: out-of-CLOSED -> action=ACTIVATE / label="Activated".
- SS-single-row: atomic-pair POST emits exactly 1 STORE row, 0 ORG_NODE rows (LD14 invariant).
- SF-dupcode: 409 DUPLICATE_STORE_CODE on POST — failure row routes to `platform_activity_audit_logs` with `tenant_id=NULL` per LD10 (body consumed before failure handler runs). Test asserts via the request_id lookup on the platform table.
- SF-invalid-transition: ACTIVE -> OPENING -> 409 INVALID_STATE_TRANSITION; conflict row carries action=`SET_STATUS` (failure-path fallback per LD3).
- SF-anchor-404: PATCH / set-status on missing store_id -> 404 + ZERO audit rows.

New fixture `cleanup_orphan_platform_audit_stores` tracks request_ids of POST /stores failures and DELETEs them at teardown (the standard make_tenant teardown can't reach them — `tenant_id=NULL` per LD10). Mirrors test_audit_emission_failures.py's pattern for POST /tenants.

### Bucket 12 : `tests/integration/test_audit_router_resource_type_filter.py` (NEW, +5)

GET endpoint resource_type filter coverage. LOAD-BEARING:
- RTF1: `?resource_type=TENANT_USER` returns only TENANT_USER rows.
- RTF3: unknown value returns 0 rows, NOT 422 (open string vocabulary).
- RTF4: AND-composes with `?status=PERMISSION_DENIED`.

### Bucket 13 : Doc updates

`docs/architecture_audit_logs.md`:
- New "Per-route extractor mapping (FN-AB-66 closure, Step 6.16.5)" subsection in Emission contract section (after the Emission failure handling subsection).
- New "resource_type vocabulary (Step 6.16.5)" subsection in Schema (with table of 6 values mapped to underlying tables).
- New `resource_type` row in the Filter parameters table (Read contract).
- Sub-step plan table 6.16.5 row flipped to DONE-LOCAL; "6.16 series complete" closure note added below the table.

`BUILD_PLAN.md`:
- Step 6.16 root status flipped from IN PROGRESS to DONE-LOCAL with closure summary.
- Step 6.16.5 sub-step block rewritten from TODO to DONE-LOCAL with full as-shipped detail (LD-by-LD).

`CLAUDE.md`:
- New Step 6.16.5 entry in `### Completed` (one-line pointer per A6 lean convention).
- FN-AB-42 marked RESOLVED with closure note (path b chosen; no-op-not-audited).
- FN-AB-65 marked RESOLVED (series complete).
- FN-AB-66 marked RESOLVED (extractor mapping shipped).
- New FN-AB-68 entry: OPEN_SOFT action code reserved for unreachable transition.

`docs/endpoints/openapi.json` regenerated: `GET /audit/activities` carries the new `resource_type` query parameter; spec verified post-regen.

## Retro

### What went well

1. **Builder reuse without signature changes.** The contradiction-surfacing license picked up that the existing `build_success_details_for_create` / `for_update` builders take arbitrary dicts — adding explicit kwargs for `org_node_created_atomically` / `parent_org_node_name` would have duplicated the path. Repo callers compose the snapshots directly; `_json_safe` handles coercion. Net result: zero builder signature changes; the entire LD11+LD5+LD6 surface area lives in the repo callers' snapshot dicts.

2. **Sibling-dict shape for the extractor mapping.** The operator-locked LD12 shape (b) — `RESOURCE_EXTRACTORS: dict[resource_type, ExtractorFunc]` — let the failure-path handler stay readable. Each extractor is a small focused function; adding a new resource_type adds one entry to AUDITED_ROUTES + one extractor + one lookup branch in emit. No god-dependency surface.

3. **Test catalogue adjustments surfaced cleanly at pre-flight.** SS5 (target=OPENING unreachable) and SS1/SS2 (atomic-pair always True) were caught at pre-flight Check #8 / Check #13 verification, surfaced as operator decisions before any implementation began, and incorporated into the locked plan. AE11 unit test covers the OPEN_SOFT label dispatch even though no integration cell can exercise it; documented in FN-AB-68.

### What needed an in-flight adjustment

1. **MODULE_ACCESS lookup SQL parameter typing (during test verification).** Initial SQL bound `:module_code` twice (once as enum, once as text); psycopg infers parameter type from first occurrence and reuses it. Postgres rejected `text = module_code_enum`. Fix: use two distinct parameter names (`module_code_enum` and `module_code_text`) bound to the same value, with explicit CASTs. Surfaced via the MF1 / MF2 / MF5 test failures.

2. **`ck_*_resource_pair` constraint vs LD9 label resolution (during MF1/MF2/MF5 test verification).** Initial implementation populated `resource_label` from `core.lookups` unconditionally even when `resource_id` remained None (no tma row exists yet on auth-deny paths). The DB CHECK constraint rejects half-populated pairs. Fix: only populate `resource_label` when `resource_id` is also populated post-lookup. MF1/MF2 tests amended to assert both NULL; MF5 amended to pre-create the tma row so the meaningful "label resolves even on permission denial" assertion stays (with both resource_id and resource_label populated).

3. **POST /stores failure-row routing visibility (during SF-dupcode test verification).** The 409 row from a duplicate-code POST routes to `platform_activity_audit_logs` with `tenant_id=NULL` per LD10 (the request body is consumed before the failure handler runs; the path has no `tenant_id`). The test originally queried only the tenant table and asserted 1 CONFLICT row. Amended to look up the CONFLICT row on the platform table by request_id. New `cleanup_orphan_platform_audit_stores` fixture added so leaked orphan rows don't accumulate across runs.

### Locked decision deviations

- **LD6 always-True in v0** (Adjusted-trivial; pre-flight surfaced). 6.21.2 made atomic-pair the only POST /stores path; the `org_node_created_atomically: false` branch is unreachable today. Flag retained in snapshot schema for D-31 stability; SS1/SS2 collapsed into a single test asserting `True` always; future endpoint variants allowing existing-org_node link must continue setting the flag (False in that case) so the auditor disambiguates eras.

- **LD7 org_node_id immutable on store PATCH; parent_org_node_id is the diff field** (Adjusted-trivial; surfaced during stores update integration). The prompt's LD7 framing — "Store PATCH `org_node_id` change recorded mechanically; both old and new org_node ids + names snapshotted in `details.before` / `details.after`" — predates the 6.21.2 atomic-pair design. Under 6.21.2, `stores.org_node_id` is the FK to the paired STORE-type `org_nodes` row and is structurally immutable on PATCH (the comment at `repositories/stores.py` explicitly states "stores.org_node_id is NEVER modified by this method"). What PATCH actually accepts is `parent_org_node_id`, which cascades to the paired org_node's `parent_id` (reparent + subtree path rewrite). The audit row's frozen-name capture works identically — both halves carry the parent's resolved name — but the underlying diff field is `parent_org_node_id`, not `org_node_id`. Phase 1 Q6's "Marcus moves Downtown Store" scenario is semantically correct: the user-visible behaviour (move a store under a different parent) is unchanged; the audit row records `before.parent_org_node_id` + `before.parent_org_node_name` and `after.parent_org_node_id` + `after.parent_org_node_name`. The v0 implementation captures the new parent's name post-cascade (the pre-cascade parent_id of the paired org_node wasn't projected by the initial SELECT for v0 simplicity; documented inline in `StoresRepo.update`). A future stores-audit retrofit can extend the pre-update SELECT to project the paired org_node's old parent_id for a cleaner before-half if that detail becomes load-bearing.

- **LD15 auth mandatory + request_id optional** (Adjusted-trivial; surfaced during integration). The prompt's LD15 framed both kwargs as optional, mirroring 6.16.4's TenantsRepo pattern. But on org-tree (`add_node`, `edit_node`) and stores (`create`, `update`, `transition`), `auth` is load-bearing for the audit-actor pair INSERT — not just for emission. Making it optional would break dozens of existing callers. Resolution: `auth` stays mandatory on these methods; `request_id` is the optional emission trigger. Same emission contract (both-or-neither for emission; request_id=None skips emission cleanly). Module-access `enable`/`disable` follow the original prompt-shape (both optional) because their pre-existing signatures took `actor_user_id: UUID` separately; `auth` is added as a brand-new optional kwarg there.

### Forward implications

- The per-route extractor mapping is the maintenance surface for adding a new audited resource_type. Convention: (a) one AUDITED_ROUTES entry per affected route + (b) one extractor function + (c) one lookup branch inside `emit_audit_event_in_new_transaction`. Documented in `docs/architecture_audit_logs.md`.
- FN-AB-68 documents the OPEN_SOFT action code reservation. Resolution paths: (a) TRANSITION_MATRIX relaxation produces a `*->OPENING` cell (label gains integration coverage); (b) explicit operator decision to retire OPEN_SOFT (would require removing the label entry + the dispatch branch).
- The audit subsystem ships v0-complete. Frontend integration of the timeline UI is the next consumer; FN-AB-67 (actor enrichment with full_name + role snapshot) remains the most-likely next forward request once the timeline lands.
