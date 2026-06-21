# Step 6.16.6 : actor_user_id filter on GET /audit/activities

**Status.** DONE-LOCAL 2026-05-23.
**Prior.** Step 6.16.5 (commit `0b4db81`) closed the 6.16 emission series.
**Closes.** FN-AB-69 (born-resolved).
**Owner.** CLAUDE_CODE.

## Mental model

Step 6.16.3 shipped `GET /api/v1/audit/activities` with 6 filter parameters; Step 6.16.5 added `resource_type` as a 7th (open-vocabulary, AND-composed, both UNION branches). Step 6.16.6 adds an 8th in the same shape: `actor_user_id`. The frontend integration of the audit subsystem post-6.16.5 surfaced three consumer surfaces that need an actor-scoped read of the timeline. They were temporarily reading from a dead `/audit-logs` URL while waiting for the filter:

- `PlatformUserDetailDrawer.Activity` (drawer Activity tab per platform user)
- `TenantUserDetailDrawer.Activity` (drawer Activity tab per tenant user)
- `RecentActivityPanel` (SuperAdmin dashboard recent-events stream)

The pre-existing acknowledgement of the gap lived in `docs/architecture_audit_logs.md:399` (Scale considerations option 6) and `:432` (Open deferred items > Actor filter parameter) without a FN-AB number. This step numbers the gap as FN-AB-69 and ships its closure in the same commit.

**Wire shape (added).**

```
GET /api/v1/audit/activities?actor_user_id=<uuid>[&<other filters>]
```

- `actor_user_id` is optional. AND-composed with the other filters per the 6.16.5 LD17 / 6.16.3 LD5 precedent.
- Both UNION branches (`tenant_activity_audit_logs` + `platform_activity_audit_logs`) receive the filter so PLATFORM callers find rows from either source by actor. TENANT callers receive it naturally; RLS continues to scope to own-tenant.
- Unknown UUIDs return 0 rows with `has_more=false` and no 422 (open-vocabulary mirror of resource_type).
- No `actor_user_type` companion. `platform_users.id` and `tenant_users.id` both default to `core.uuidv7()` (verified at pre-flight Check #11 against `docs/schema/current_schema.sql`) and are globally unique. `actor_user_id` alone is fully selective.

**Why no shared-helper refactor.** Pre-flight Check #4 showed the live repo has no `_apply_common_filters` helper. Filters are inlined at two sites in `src/admin_backend/repositories/audit_logs.py`:

1. `_build_tenant_only_sql(schema)` (single `text()` template for TENANT callers).
2. The `common_where` Python f-string block in `_build_union_sql(schema, scope)`, concatenated into both `tenant_branch_sql` and `platform_branch_sql`.

The 6.16.5 `resource_type` clause is inlined at the same two sites. 6.16.6 follows the same pattern (LD5 Adjusted-trivial per Surface-and-stop scenario #3's pre-authorised resolution). Behaviour identical to a single shared helper. Forward note: consider a shared-helper refactor when 3+ filters share the both-branch pattern; currently 2 (`resource_type`, `actor_user_id`).

## Implementation plan (as shipped)

| File | Change |
|---|---|
| `src/admin_backend/routers/v1/audit.py` | Added `actor_user_id: UUID | None = Query(None, description=...)` to `list_audit_activities`. Passed through to `_repo.list(...)`. |
| `src/admin_backend/repositories/audit_logs.py` | `AuditLogsRepo.list` gains `actor_user_id: UUID | None = None` kwarg. Param bound at the `params` dict. SQL clause `AND (CAST(:actor_user_id AS uuid) IS NULL OR actor_user_id = CAST(:actor_user_id AS uuid))` added inline at two sites: inside `_build_tenant_only_sql` and inside the `common_where` block. |
| `tests/integration/test_audit_router.py` | +3 new tests (AUF1 / AUF2 / AUF3), inserted between L15 and D1. AUF1 + AUF3 LOAD-BEARING. |
| `docs/endpoints/openapi.json` | Regenerated via `app.openapi()` python entry point. Diff: new parameter on the activities-list operation. |
| `docs/architecture_audit_logs.md` | Read contract > Filter parameters table: new `actor_user_id` row. Scale option 6 + Open deferred items > Actor filter parameter: rewritten from "deferred" to "shipped at Step 6.16.6". Sub-step plan table: 6.16.6 row added; closure note amended (preserves "series complete" wording, adds "Step 6.16.6 followed up post-closure with the actor filter ..."). |
| `BUILD_PLAN.md` | Step 6.16 root block amended (post-closure follow-up note). Step 6.16.6 sub-step entry added after 6.16.5. |
| `CLAUDE.md` | Step 6.16.6 capsule added at top of the 6.16 entries (reverse-chronological order in Completed). FN-AB-69 created as RESOLVED entry. |
| `docs/implementation-steps/step-6_16_6-actor-filter-on-audit-activities-2026-05-23.md` | NEW (this file). |
| `prompts/step-6_16_6-impl-2026-05-23.md` | NEW (the impl prompt, renamed from `-2026-05-21.md` to match the commit date per the prompt's date-placeholder convention). |

## Test catalogue

| ID | File | Load-bearing | Asserts |
|---|---|---|---|
| AUF1 | test_audit_router.py | yes | `?actor_user_id=B` selects only the row with `actor_user_id=B` (3 distinct actor_user_ids inserted on the same tenant; 2 of 3 rows correctly excluded). |
| AUF2 | test_audit_router.py | no | `?actor_user_id=A&status=PERMISSION_DENIED` selects only the row matching BOTH (4 rows inserted; 1 row matches; 3 excluded). |
| AUF3 | test_audit_router.py | yes | `?actor_user_id=<random>` returns 200 with `items=[]`, `has_more=false`, `next_cursor=null`. No 422. |

Existing 25 audit_router tests exercise the filter-inactive branch implicitly (`CAST(:actor_user_id AS uuid) IS NULL` evaluates true when the param is omitted).

## Surface-and-stop deviations applied

- **LD5 Adjusted-trivial** (Check #4): SQL clause inlined at two sites instead of one shared helper. Pre-authorised by Surface-and-stop scenario #3.
- **FN-AB-69 born-RESOLVED** (Check #9): FN-AB-69 created and flipped to RESOLVED in the same commit, mirroring the FN-AB-19 / FN-AB-21 precedent. The pre-existing acknowledgement of the gap lived in the design doc paragraphs without a FN-AB number; this commit numbers and resolves it together.

## Verification

```
uv run pytest --tb=no -q  # 872 passed (+3 from 869 baseline)
uv run mypy --strict src/admin_backend  # Success: no issues found in 82 source files
./scripts/check_setup.sh  # 36/36
```

## Retro

**What landed cleanly.**

- The repo's existing inline-twice pattern absorbed the new clause without restructuring. The 6.16.5 precedent made the shape obvious.
- `make_tenant_activity_audit_log` already accepted `actor_user_id` as an optional kwarg (per Check #7); AUF1/AUF2/AUF3 reused it directly without fixture extension.
- OpenAPI regen via `app.openapi()` Python entry point (not the full `scripts/test_endpoints.sh` cycle) produced a focused 19-line diff; deterministic and fast.

**What deviated.**

- The prompt anticipated a `_apply_common_filters` shared helper that does not exist in the live repo. Surface-and-stop scenario #3 pre-authorised inline-twice; minimal friction.
- FN-AB-69 was not an OPEN forward note at HEAD. Born-resolved interpretation kept the prompt's framing usable without operator round-trip mid-implementation.
- The prompt's Appendix A specified ONE design doc edit site (the Filter parameters table row). At apply time the design doc carried two adjacent "deferred" paragraphs about the same filter (Scale considerations option 6 + Open deferred items > Actor filter parameter); leaving them stale would have created an internal inconsistency (filter-shipped in the table; "deferred" two paragraphs above and below). Both rewritten in the same commit as good-citizenship cleanup. Total: 3 design doc edit sites, not 1.

**Forward.**

- A shared-helper refactor for the audit filter builder becomes attractive when a 3rd both-branch filter ships (today: `resource_type` + `actor_user_id` = 2). Until then, the inline-twice pattern is the right cost.
- `actor_user_id` BTREE index remains deferred per design doc Scale option 6. v0 scale is sub-millisecond on sequential scan; revisit when monitoring shows actor-filtered queries on the hot path.
- Frontend integration of the 3 consumer surfaces is now unblocked; separate frontend work.
- **Appendix A sweep convention (next time):** when a deferred-feature step ships and crosses into a "now-shipped" state, the impl prompt's Appendix A should explicitly sweep adjacent design doc sections for stale "deferred" wording on the same feature — not just the primary feature-description site. The forward-spec table row is rarely the only place where deferral status lives; Scale considerations and Open deferred items are common adjacent homes. Catch them together so the design doc stays coherent in one pass.
