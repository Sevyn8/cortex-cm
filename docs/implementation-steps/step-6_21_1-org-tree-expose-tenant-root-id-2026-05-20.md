# Step 6.21.1 : Expose tenant_root_id / tenant_root_code / tenant_root_path on GET /org-tree

**Status.** DONE-LOCAL (2026-05-20).
**Owner.** CLAUDE_CODE (impl).
**Blocked by.** None.

## Mental Model

Three additive top-level fields on `OrgTreeResponse` (`tenant_root_id`,
`tenant_root_code`, `tenant_root_path`) surface the tenant-root
`org_nodes` row so the frontend can use the correct UUID as
`parent_id` when calling `POST /org-tree` to create a top-level node
directly under the synthesized TENANT row in its tree visualisation.

Investigation `docs/investigations/2026-05-20-write-surface-coupling.md`
identified two write-surface coupling gaps (Gap A and Gap B). Gap A is
this step:

- The Organization Tree page rendered an implicit TENANT row at the
  top of the visualised tree.
- The frontend wired the "Add Org Node" form so that selecting this
  TENANT row produced a POST with `parent_id = data.tenant_id` (the
  value from `OrgTreeResponse.tenant_id`).
- `data.tenant_id` is the `tenants.id` UUID, not the `org_nodes.id`
  of the tenant-root row. The two are independent UUIDs (the
  tenant-root org_node has its own UUIDv7 from the `org_nodes` DB
  DEFAULT, set by Step 6.20.1's atomic `TenantsRepo.create` or by
  backfill).
- The backend correctly rejected the request because that UUID does
  not exist in `org_nodes`. The frontend had no other server-side
  source for the correct `org_nodes.id`.

The fix is response-shape-only. The handler already loads the
tenant-root row via the existing `OrgNodesRepo.list_active_with_child_counts`
call (the result set includes the TENANT-typed row; the router-level
`_build_tree` helper filters it out of the rendered `tree[]` but
keeps its id in a `tenant_root_ids` set for the
"is-this-a-top-level-node" check). LD2 surfaces the row's id/code/path
to the response envelope; no new SQL, no new repo method, no new
round-trip.

Gap B (store ↔ STORE-type org_node coupling) is out of scope; deferred
to Step 6.21.2.

## Implementation Plan

### Locked decisions (operator-confirmed at Phase 2 close)

- **LD1.** Three new fields, all required (non-nullable) on
  `OrgTreeResponse`. Every tenant has exactly one tenant-root
  org_node post-Step-6.20.1; surface as required, not optional.
- **LD2.** Handler-side extraction. No `OrgNodesRepo.get_tree`
  refactor (the method does not exist; pre-flight Check #3 confirmed).
  The handler scans the existing `list_active_with_child_counts`
  result set once for the row with `node_type=OrgNodeType.TENANT`.
- **LD3.** Handler populates the 3 new fields directly from the
  extracted row. No new SQL. No new query.
- **LD4.** OpenAPI description text update on `OrgTreeResponse` class
  docstring and on the `tree` field-level description.
- **LD5.** Test scope: 3 router tests (PLATFORM, TENANT OWNER,
  empty-descendants). No repo tests (the repo contract is unchanged
  under LD2).
- **LD6.** No new error classes.

Two locked decisions reframed at pre-flight:

- **LD2 originally**: "refactor `OrgNodesRepo.get_tree`'s return
  shape." Pre-flight Check #3 found no `get_tree` method; LD2
  reframed to handler-side extraction by operator authorisation
  (2026-05-20). Operator updated the prompt accordingly before
  implementation began.
- **LD5 originally**: included two repo tests E1 / E2. Reframed at
  the same authorisation pass because under the new LD2 the repo
  contract is unchanged. Repo tests dropped.

### Surface-and-stop findings during implementation

None.

### Files touched

- `src/admin_backend/schemas/org_node.py`: +3 required fields on
  `OrgTreeResponse` (`tenant_root_id: UUID`, `tenant_root_code: str`,
  `tenant_root_path: str`); class docstring updated; field-level
  description on `tree` updated to reflect the new top-level fields.
- `src/admin_backend/routers/v1/org_tree.py`: handler extracts the
  TENANT-typed row from the existing `list_active_with_child_counts`
  result; raises `InternalInvariantViolationError` if no TENANT row
  is found (structurally impossible post-Step-6.20.1; the
  field-assignment otherwise would crash uninstrumented). Three new
  kwargs on `OrgTreeResponse(...)`. Added one import for
  `InternalInvariantViolationError`.
- `tests/integration/test_org_tree_router.py`: +3 router tests
  (`test_t22_e2_tenant_root_fields_platform`,
  `test_t23_e2_tenant_root_fields_tenant_owner`,
  `test_t24_e2_tenant_root_fields_empty_tree`); coverage map at file
  head extended (T22 / T23 / T24); T1's exact-set keys assertion
  bumped from 4 keys to 7 keys.
- `scripts/smoke_curl.sh`: +1 assertion against the existing GET
  /org-tree probe; WHAT'S CHECKED count 67 → 68; assertion-name
  `org_tree__tenant_root_fields`. Loose shape: non-empty
  UUID-pattern plus non-empty `code` and `path`.
- `scripts/test_endpoints.sh`: +1 mirroring assertion attached to
  the existing TU_TREE_RESP fetch.
- `scripts/test_endpoints_cloud.sh`: +1 mirroring assertion attached
  to the existing T1 setup fetch (Buc-ee's). Cloud-strict on code
  (`BUC-EES`) and path (`buc_ees`) per operator-verified Cloud SQL
  state.
- `docs/endpoints/org-tree.md`: GET section Response 200 sample
  envelopes updated (full-tree, depth-limited, truncated,
  empty-tenant); new "Top-level fields" reference table.
- `docs/endpoints/openapi.json`: regenerated via `jq -a .`
  (ASCII-escape matches the file's existing convention; previously
  attempted `python -m json.tool indent=4` was wrong because the
  committed file uses 2-space indent + `\u`-escaped non-ASCII).
- `BUILD_PLAN.md`: Step 6.21 family block added; 6.21.1 DONE-LOCAL;
  6.21.2 TODO placeholder.
- `CLAUDE.md`: one-line pointer entry under `## Current state` / `### Completed`.
- `docs/implementation-steps/step-6_21_1-org-tree-expose-tenant-root-id-2026-05-20.md`:
  this step doc.
- `prompts/step-6_21_1-impl-2026-05-20.md`: the impl prompt (committed
  for audit trail).

### Verification commands

```bash
# Targeted tests
uv run pytest tests/integration/test_org_tree_router.py -v

# Full suite regression
uv run pytest --tb=no -q

# mypy strict
uv run mypy --strict src/admin_backend

# check_setup
./scripts/check_setup.sh

# Local smoke (uvicorn + smoke_curl)
set -a; source .env; set +a
uv run uvicorn admin_backend.main:app --host 127.0.0.1 --port 8000 > /tmp/uvicorn-smoke.log 2>&1 &
sleep 4
bash scripts/smoke_curl.sh http://localhost:8000

# OpenAPI regen
curl -sf http://localhost:8000/api/v1/openapi.json | jq -a . > docs/endpoints/openapi.json.tmp
mv docs/endpoints/openapi.json.tmp docs/endpoints/openapi.json
```

## Retro

### What landed

- Three additive top-level fields on `OrgTreeResponse`. Append-only
  per D-31. No breaking change.
- Handler-side extraction (LD2). Zero repo changes. Zero new SQL.
  Single extra defensive check (`InternalInvariantViolationError`
  raise when the TENANT-typed row is absent, which is structurally
  impossible post-Step-6.20.1 but mypy-friendly and louder than a
  bare `AttributeError`).
- 3 new router tests (T22 PLATFORM, T23 TENANT OWNER, T24
  empty-descendants). T1's exact-set keys assertion updated to
  include the 3 new keys.
- Smoke + endpoint scripts gain 1 assertion each; cloud-strict on
  Buc-ee's. WHAT'S CHECKED count 67 → 68.
- OpenAPI regen produces 3 new field blocks (`tenant_root_id`,
  `tenant_root_code`, `tenant_root_path`) + updated descriptions on
  `OrgTreeResponse` class and `tree` field. Plus minor pre-existing
  drift catching up (audit description text was already non-em-dash
  in source but the previous openapi.json was stale; latitude /
  longitude maximum / minimum reformat from `90.0` to `90` is a
  Pydantic / FastAPI version cosmetic).
- Full suite 766 → 769 (+3). 0 fails, 0 xfails.
- mypy strict clean (82 source files; unchanged from pre-step).
- check_setup 36/36.
- Local smoke 67 → 68 (+1). All 68 pass.
- alembic head unchanged (no migration).

### Deviations from the original prompt

The prompt's first revision (operator updated before pre-flight)
already incorporates the reframings agreed at the design-conversation
close. The implementation surfaced two further small refinements
during execution:

- **OpenAPI regen command.** The prompt's verification harness step 6
  used `python -c 'json.dumps(..., indent=4, ensure_ascii=False)'`.
  This produces a 6700-line diff because the committed `openapi.json`
  uses 2-space indent with `\u`-escaped non-ASCII (the latter from
  ASCII-escape). The canonical pattern (per
  `scripts/test_endpoints.sh:260`) is `jq -a .` which preserves both
  conventions. Used `jq -a .` for the actual regen. Resulting diff is
  34 insertions / 15 deletions and focused on the intended fields.
- **Defensive raise on missing TENANT row.** The bare expression
  `next(... default=None)` followed by attribute access is mypy-strict-
  unfriendly (`OrgNode | None` cannot be `.attribute`-accessed). Two
  resolution patterns considered: (a) `# type: ignore[union-attr]`
  on three lines (ugly and silent); (b) explicit
  `if tenant_root_node is None: raise InternalInvariantViolationError(...)`.
  Picked (b) because the codebase has a `InternalInvariantViolationError`
  (Step 6.18.3 LD8 tripwire pattern, `errors.py:766`) that maps to the
  generic `INTERNAL_ERROR` wire envelope and tells operators the class
  type in logs. Mirrors the LD8 posture without inventing a new error
  class. Captured in LD3's handler implementation; not a deviation, a
  refinement.

### Cloud deploy posture

Pure code addition. No DDL change. No migration. No env-var. No
secret. No seed change.

Cloud deploy via the standard `./scripts/deploy-cloud-run.sh` pattern
(no `--migrate` needed). Operator decides at Phase 6 whether to
deploy 6.21.1 alone (unblocks frontend's Add Org Node fix immediately)
or to batch with 6.21.2 (when 6.21.2 lands).

### What did NOT change

- DDL: zero changes.
- Migration chain: zero changes (alembic head unchanged at
  `c530346032dd`).
- Permission catalogue: zero changes (seed Excel unchanged).
- Other endpoints: zero changes (POST /org-tree, PATCH /org-tree,
  E3 children, etc.).
- Existing `_build_tree` helper: unchanged signature, unchanged
  behaviour.
- `OrgNodesRepo`: unchanged.
- Test count for any pre-existing test file: unchanged.

### Adjacent observation

Pre-flight Check #1 surfaced that the working tree had four
non-prompt items: a modified seed Excel and three untracked
investigation/inquiry prompt files. Operator confirmed at pre-flight
authorisation that these are out of scope for this commit. Step
6.21.1 leaves them alone; they will be addressed in a separate
operator-initiated pass.
