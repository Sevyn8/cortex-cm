# Step 6.16.7 : Audit row schema additions + emission retrofit + GET endpoint shape change

**Status.** DONE-LOCAL 2026-05-23.
**Prior.** Step 6.16.6 (commit `59757cf`) added the `actor_user_id` GET filter.
**Closes.** FN-AB-67 (RESOLVED).
**Adds.** FN-AB-70 (INTEGRITY_VIOLATION reserved vocabulary).
**Owner.** CLAUDE_CODE.

## Mental model

The audit subsystem (Steps 6.16.0 through 6.16.6) shipped with a 16-column row shape and an 8-field GET response. The frontend audit list-view redesign (Phase 1 + Phase 2 of 6.16.7) needs three concrete extensions:

1. **Per-row actor enrichment.** The list view's Actor column needs more than `actor_display_name` (= the actor's email). It needs the actor's organisation (tenant name for tenant actors; `"Platform-Ithina"` literal for platform actors) and the actor's role(s) at the time of the action (comma-separated active role display names, e.g., `"Owner, Promotions Assistant"`). Both are denormalised snapshots frozen at write time, consistent with the Phase 1 audit-history principle: history never changes.

2. **Resource subtype for org-tree rows.** The frontend Type column needs to distinguish a Region from a Department from a Country — all ORG_NODE rows in the current schema. Adding `resource_subtype` (carrying the `org_nodes.node_type` value) at write time lets the read side compose a precise `"Region: Texas Region"` display string instead of a generic `"Org node: Texas Region"`.

3. **Display vocabulary updates.** Action labels flip ("Updated" -> "Edited"; "Status change" -> "Set status") and CONFLICT rows compose a per-class `result_label` qualifier so the read side surfaces "Blocked - email already in use for this tenant" instead of a flat "Conflict".

The wire shape changes are additive only (D-31): existing 8 list-view fields keep their structure; 6 new fields join them. The schema migration is forward-only-but-reversible Path A: ADD COLUMN NULL -> UPDATE backfill -> SET NOT NULL on the two non-NULL columns.

## Locked-decision reference

The full 16 locked decisions live at `prompts/step-6_16_7-impl-2026-05-23.md`. Key gates:

- **LD1 / LD2 / LD3.** Three new columns on both audit tables; Path A backfill; backfill expressions per side (CASE on `actor_user_type` for tenant table; literal for platform table; `'-'` for actor_roles; NULL for resource_subtype).
- **LD4.** Frozen-snapshot semantics for all 3 new columns. Resolved at emission time; never recomputed at read time.
- **LD5 + LD6.** Resolver shape for actor_roles (JOIN appropriate role-assignments table -> `core.roles`; comma-separated `roles.name`) and actor_organization_name (JOIN `tenants` for TENANT actors; literal for PLATFORM).
- **LD7.** `resource_subtype` populated only on 2 org-tree emission sites; all other paths leave it None.
- **LD8.** UPDATE label "Updated" -> "Edited"; SET_STATUS label "Status change" -> "Set status".
- **LD9.** 9 CONFLICT qualifier phrases composed via dispatch table; `"Blocked - <qualifier>"` shape.
- **LD10 / LD11 / LD12.** 6 new response fields on `AuditActivityListItem`; backend composes `what` at read time via a 13-combination Type label mapping.
- **LD13.** Centralisation: resolvers called INSIDE emission entry points; zero changes at 5 of 6 emission-site repos. Only `repositories/org_nodes.py` changes (2 sites). Dual-mechanism retrofit: ORM model attrs + raw `text()` INSERT statement extended.

## Implementation plan (as shipped)

| File | Change |
|---|---|
| `migrations/versions/7a3c8e9d2f5b_step_6_16_7_audit_actor_enrichment.py` | NEW. Path A migration: ADD COLUMN NULL (3 columns, both tables) -> UPDATE backfill (CASE on tenant table; literal on platform table; `'-'` for actor_roles) -> SET NOT NULL on `actor_organization_name` + `actor_roles` (both tables). Downgrade drops the 3 columns. Schema-qualified per CSD-03. |
| `src/admin_backend/models/audit_log.py` | Both `TenantActivityAuditLog` and `PlatformActivityAuditLog` gain 3 new `Mapped[...]` columns (`actor_organization_name: Mapped[str]`, `actor_roles: Mapped[str]`, `resource_subtype: Mapped[str \| None]`). |
| `src/admin_backend/audit/emit.py` | 5 changes: (a) LD8 action label flips, (b) new `_label_for_resource_type` helper + `_RESOURCE_TYPE_LABELS` + `_ORG_NODE_SUBTYPE_LABELS` dicts (LD12), (c) new `_CONFLICT_QUALIFIERS` dict + `compose_conflict_result_label()` + `_qualifier_for_conflict()` (LD9), (d) new `_resolve_actor_organization_name()` + `_resolve_actor_roles()` resolvers + `_PLATFORM_ORG_NAME` constant (LD5/LD6), (e) emission entry points retrofit: `emit_audit_event` calls resolvers + passes results + new `resource_subtype` kwarg to `_build_row`; `_build_row` constructs ORM with 3 new attrs; `emit_audit_event_in_new_transaction` calls resolvers post-GUC-set under the new connection + extends raw INSERT from 14 to 17 explicit columns + extends ORG_NODE failure-path lookup to fetch `node_type` for resource_subtype back-fill (LD7+LD13). |
| `src/admin_backend/main.py` | Failure handler imports `compose_conflict_result_label`; composes CONFLICT row's `result_label` when result_type is CONFLICT; passes through `result_label` kwarg to `emit_audit_event_in_new_transaction` (LD9). |
| `src/admin_backend/repositories/org_nodes.py` | 2 emission sites in `add_node` and `edit_node` pass `resource_subtype=row.node_type.value` (LD7+LD13). Only 1 repo touched among the 6 emission-site repos. |
| `src/admin_backend/schemas/audit_log.py` | `AuditActivityListItem` 8 -> 14 fields (additive): + `actor_organization_name`, `actor_roles`, `what`, `resource_type`, `resource_subtype`, `result_type`. `AuditActivityDetail` 16 -> 19 fields (additive): + `actor_organization_name`, `actor_roles`, `resource_subtype`. Both keep `model_config = ConfigDict(extra="forbid")`. |
| `src/admin_backend/repositories/audit_logs.py` | SELECT projection extended on all 4 query paths (`_build_tenant_only_sql`, `tenant_branch_sql` and `platform_branch_sql` in `_build_union_sql`, both `get_by_id` queries via the shared projection format) to include the 3 new stored columns. `AuditActivityDetailRow` frozen dataclass extended with 3 new fields. `_row_to_dataclass` populates them. |
| `src/admin_backend/routers/v1/audit.py` | New `_compose_what(row)` helper (LD11). `_list_item_from_row` and `_detail_from_row` extended to populate the new fields. `_label_for_resource_type` imported from `admin_backend.audit.emit`. |
| `tests/unit/test_audit_emit.py` | 4 new tests (AE_N3-AE_N6: type-label dispatch for non-ORG_NODE / ORG_NODE subtypes / NULL subtype fallback / CONFLICT qualifier dispatch). AE1-AE3 + AE5 + AE11 updated for the new `_build_row` signature + LD8 labels. |
| `tests/integration/test_audit_logs_repo.py` | 3 new tests (R_N1-R_N3: SELECT projection includes new columns, `what` composition across 13 type-label combinations, NULL resource_label dash-fallback). |
| `tests/integration/test_audit_router.py` | 2 new tests (L_N1 14-field shape on list response, L_N2 ORG_NODE/REGION subtype + composed `what`). D1 detail-shape test expanded from 16 to 19 expected keys. |
| `tests/integration/test_audit_emission_tenants.py` | 1 new LOAD-BEARING test (AS_N1 — PLATFORM actor enrichment populated). New `_fetch_audit_rows_full` helper SELECTing all columns. |
| `tests/integration/test_audit_emission_org_tree.py` | 1 new LOAD-BEARING test (OS_N1 — resource_subtype="REGION" on both CREATE and UPDATE; UPDATE action_label="Edited"). `_fetch_audit_rows` extended to project the new columns. |
| `tests/integration/test_audit_emission_failures.py` | 1 new LOAD-BEARING test (AF_N1 — CONFLICT result_label composed + actor enrichment on failure path). `_fetch_audit_rows` extended to project result_label / action_label / actor_organization_name / actor_roles. |
| `tests/integration/test_audit_emission_stores.py` | `test_sf_set_status_invalid_transition_emits_conflict` assertion updated for LD8: action_label "Status change" -> "Set status". |
| `tests/integration/test_audit_log_models.py` | `_tenant_row` + `_platform_row` builders populate the 3 new required columns by default. |
| `tests/integration/test_audit_log_schema.py` | `_audit_row_args` + both raw INSERT helpers (`_insert_tenant_audit_row`, `_insert_platform_audit_row`) extended with the 3 new columns. |
| `tests/unit/test_audit_log_schemas.py` | S2 / S3 renamed and expanded for 14-field list shape + 19-field detail shape; constructor assertions updated. |
| `tests/integration/conftest.py` | `make_tenant_activity_audit_log` and `make_platform_activity_audit_log` factories gain 3 optional kwargs with defaults; INSERT SQL extended from 16 to 19 columns. |
| `tests/integration/test_audit_migration.py` | NEW. 8 tests (AT_N1-AT_N8) verifying migration invariants: revision at head, column nullability shape, INSERT round-trip with new columns, backfill CASE expression, platform-table literal, NOT NULL violations on the two non-NULL columns, resource_subtype NULLABLE. |
| `docs/architecture_audit_logs.md` | Schema section: 3 new column rows on both tables (LD1). Overview amended (column-count). New "Response shape (post Step 6.16.7)" subsection in Read contract documenting the 6 new fields. New "Display vocabulary (Step 6.16.7)" subsection with action labels (LD8), CONFLICT qualifier dispatch table (LD9), and Type label mapping (LD12). |
| `docs/schema/current_schema.sql` | Regenerated via `pg_dump --schema=core --schema-only`. New columns visible on both tables. Alembic head moves to `7a3c8e9d2f5b`. |
| `docs/schema/migration_log.md` | Entries for `34f515cbc63a` (Step 6.21.2; pre-existing) and new entry for `7a3c8e9d2f5b` (Step 6.16.7). |
| `docs/endpoints/openapi.json` | Regenerated via app dev server. 16 occurrences of the new audit field names across the spec; `AuditActivityListItem` and `AuditActivityDetail` schemas reflect the 14 / 19 fields. |
| `BUILD_PLAN.md` | Step 6.16 root block amended (post-closure follow-up note). New Step 6.16.7 sub-step entry as DONE-LOCAL. |
| `CLAUDE.md` | Step 6.16.7 capsule prepended in reverse-chronological order. FN-AB-67 entry flipped to RESOLVED. FN-AB-70 created. |
| `docs/implementation-steps/step-6_16_7-audit-row-schema-and-emission-retrofit-2026-05-23.md` | NEW (this file). |
| `prompts/step-6_16_7-impl-2026-05-23.md` | Bundled into the commit. |

## Test catalogue

| Test | File | Type | LOAD-BEARING | Notes |
|---|---|---|---|---|
| AE_N3 | test_audit_emit.py | unit | no | _label_for_resource_type for non-ORG_NODE codes |
| AE_N4 | test_audit_emit.py | unit | no | _label_for_resource_type covers all 7 ORG_NODE subtypes |
| AE_N5 | test_audit_emit.py | unit | no | ORG_NODE NULL subtype fallback to "Org node" |
| AE_N6 | test_audit_emit.py | unit | **yes** | CONFLICT qualifier dispatch for all 9 codes; fallback for unknown |
| AS_N1 | test_audit_emission_tenants.py | integration | **yes** | POST /tenants success row carries actor_organization_name="Platform-Ithina", actor_roles="Super Admin", resource_subtype None |
| OS_N1 | test_audit_emission_org_tree.py | integration | **yes** | POST add-node + PATCH edit-node populate resource_subtype="REGION"; UPDATE action_label="Edited" |
| AF_N1 | test_audit_emission_failures.py | integration | **yes** | 409 INVALID_STATE_TRANSITION emits CONFLICT row with result_label="Blocked - status change not allowed" + actor enrichment populated under super_admin_jwt |
| R_N1 | test_audit_logs_repo.py | integration | **yes** | Repo SELECT projection returns actor_organization_name, actor_roles, resource_subtype |
| R_N2 | test_audit_logs_repo.py | integration | **yes** | `what` composition correct across 13 (resource_type, resource_subtype) combinations |
| R_N3 | test_audit_logs_repo.py | integration | no | NULL resource_label renders as "<Type label>: -" |
| L_N1 | test_audit_router.py | integration | **yes** | GET /audit/activities item shape: exact 14-field set, populated values |
| L_N2 | test_audit_router.py | integration | no | ORG_NODE row carries resource_subtype and composed `what` "Region: ..." |
| AT_N1 | test_audit_migration.py | integration | **yes** | Migration revision 7a3c8e9d2f5b at alembic head |
| AT_N2 | test_audit_migration.py | integration | **yes** | Column nullability shape on both tables |
| AT_N3 | test_audit_migration.py | integration | **yes** | INSERT with 3 new columns round-trips correctly |
| AT_N4 | test_audit_migration.py | integration | no | Backfill CASE expression evaluates correctly |
| AT_N5 | test_audit_migration.py | integration | no | Platform-table backfill literal matches code constant |
| AT_N6 | test_audit_migration.py | integration | **yes** | NOT NULL on actor_organization_name fires on omission |
| AT_N7 | test_audit_migration.py | integration | **yes** | NOT NULL on actor_roles fires on omission |
| AT_N8 | test_audit_migration.py | integration | no | resource_subtype omitted INSERT succeeds (NULLABLE) |

Net: +20 new tests collected (4 unit + 16 integration). 11 LOAD-BEARING.

## Cloud Note

Cloud deploy bundles with 6.16.6 at next deploy cycle per Phase 5.5 batching. The migration (`7a3c8e9d2f5b`) applies at deploy time; the backfill UPDATE is sub-millisecond on local seeded data per EXPLAIN ANALYZE. Cloud SQL is verified post-deploy via Studio + `verify_cloud_schema.py`.

## Retro

### What surfaced

1. **Five existing tests broke on `_build_row` signature change.** AE1-AE3 + AE5 + AE11 needed updates because `_build_row`'s required keyword arguments grew. Caught immediately at first focused-test run; fixed mechanically. No design-level concern.

2. **6 unexpected pre-existing test files broke on NOT NULL constraints.** `test_audit_log_models.py M1-M5` and `test_audit_log_schema.py S8/S9` use raw SQL or ORM with literal column lists; they had no defaults for the 3 new required columns and hit `NotNullViolation`. Each was a 2-3 line fix (default values added to row builders). All fixes mechanical; no design pull.

3. **Two field-count tests need annual maintenance.** `test_s2_audit_activity_list_item_has_exactly_8_fields` and `test_s3_audit_activity_detail_has_exactly_16_fields` are explicit shape contracts. Renamed to 14/19 and the assertion sets updated. The shape-contract test is doing the job it was designed to do: surfacing additive shape changes for explicit review.

4. **LD13 centralisation pays off concretely.** Of the 6 emission-site repos, only `org_nodes.py` changed (and only to pass a new kwarg for `resource_subtype`). The other 5 (`tenants.py`, `tenant_users.py`, `modules_access.py`, `stores.py`, `roles.py`) were untouched. The resolvers run inside `emit_audit_event` / `emit_audit_event_in_new_transaction`, so the per-call-site cost of adding the 2 new actor columns is zero. Confirmed by `git diff --cached --stat` showing those 5 files don't appear in the commit-set.

### What's deferred

- **FN-AB-58** (`_actor_type_from_auth` promotion) stays open. The 4 local copies remain unchanged in this commit.
- **FN-AB-63** (Pydantic 422 envelope) stays open. The audit subsystem's failure-path emission still bypasses Pydantic-direct 422.
- **FN-AB-70** (INTEGRITY_VIOLATION reserved vocabulary) created NEW. The dormant slot stays in the enum + label + builder; revisit when a use case surfaces.

### What got better

- **Two-resolver shape is the simplest possible.** Each resolver is a single SQL query keyed by the actor's UUID; no joins to dispatch tables. The PLATFORM / TENANT branch dispatches on `auth.user_type` alone. Total emission-time cost: 2 extra SELECTs (sub-millisecond each at v0 scale).
- **Dual-mechanism INSERT is now explicit.** Both `emit_audit_event` (ORM) and `emit_audit_event_in_new_transaction` (raw SQL) needed the same column-list extension. The raw-SQL retrofit caught a class of latent failure-path bug that would have NOT NULL-violated post-migration. Tests in `test_audit_migration.py` (AT_N6, AT_N7) lock the contract.
- **CONFLICT qualifier dispatch is open-vocabulary friendly.** Adding a 10th ClientError 409 just means adding a row to `_CONFLICT_QUALIFIERS`; unmapped codes fall through to "Conflict" cleanly.

## References

- Prompt: `prompts/step-6_16_7-impl-2026-05-23.md`
- Investigation: `reports/step-6_16_7-audit-list-view-investigation-2026-05-23.md`
- Design doc: `docs/architecture_audit_logs.md`
- Migration: `migrations/versions/7a3c8e9d2f5b_step_6_16_7_audit_actor_enrichment.py`
