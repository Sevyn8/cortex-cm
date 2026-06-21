# Step 6.20.2 : /me/can-do ltree input validation fix

**Date:** 2026-05-19
**Owner:** CLAUDE_CODE
**Status:** DONE-LOCAL (single commit on `main`)
**Closes:** FN-AB-61
**Cloud incident reference:** v0.1.17, revision `admin-backend-00018-46f`

## Mental Model

A single-line gap in input validation produced a 500 `INTERNAL_ERROR`
on `GET /api/v1/me/can-do` whenever the caller passed a non-ltree
value for the optional `target_anchor` Query param. The endpoint
forwarded the value verbatim into `has_permission()`, where the
TENANT branch ran `CAST(:target_anchor AS ltree) <@ on_.path` against
Postgres. ltree's label syntax is `[A-Za-z0-9_]+` per dot-separated
segment; any non-conforming input (the cloud-reported case was a
UUID with hyphens) raised `psycopg.errors.SyntaxError`, which the
handler did not catch and which bubbled to the generic 500 envelope.

The fix is the cheapest possible: a Pydantic `pattern=` + `max_length=`
validator on the Query declaration. FastAPI runs Query validation
during dependency resolution, BEFORE the gate dependency or the
handler body. A malformed input now produces FastAPI's default 422
detail envelope, identifying `target_anchor` as the failing field.
The SQL CAST is never reached.

Two adjacent observations from the investigation shaped scope:

- The bug fires only under TENANT JWT. `_has_permission_platform`
  accepts `target_anchor` but never references it in SQL (the
  PLATFORM cascade is global, not anchored). The Pydantic check is
  JWT-type-agnostic, but the cloud-reported failure shape exercises
  the TENANT path; tests assert that path explicitly.
- `/me/can-do` is the SOLE caller-supplied-ltree surface in v0. The
  other six ltree CAST sites in source all consume `_path_label`-
  derived or DB-read strings, both structurally safe. A shared
  `LtreePath` Pydantic type would be over-investment for a single
  call site; inline `pattern=` matches the four existing Field
  pattern validators in style (LD2).

A bundled docstring correction at `schemas/org_node.py:275` lands
in the same commit: the existing claim "No underscores (ltree label
restriction)" had the direction backwards. Underscores are valid in
ltree labels; the org_node code convention is the inverse alphabet
(alphanumerics + hyphens, no underscores). `_path_label` is the
bridge that converts the code form to the ltree-label form by
replacing hyphens with underscores. The pattern itself is correct
for the convention; only the parenthetical was misleading.

## Implementation Plan

Single commit on `main`. Ten file changes. No DDL, no migration, no
catalogue update, no env-var or IAM change.

### Source changes

1. **`src/admin_backend/routers/v1/me.py`.** Extend the
   `target_anchor` `Annotated[str | None, Query(...)]` declaration with
   `pattern=r"^[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)*$"` (LD1: multi-label
   ltree grammar) + `max_length=1024` (LD3: conservative cap above
   realistic org-tree depth; deepest seeded path is 31 chars) +
   expanded `description=` explaining the format and the 422 contract.

2. **`src/admin_backend/schemas/org_node.py`.** Replace the docstring
   line "No underscores (ltree label restriction)" inside
   `OrgNodeCreateRequest.code` with a corrected explanation: the
   org_node code convention forbids underscores, while ltree labels
   USE underscores, and `_path_label` bridges the two by converting
   hyphens to underscores. Pattern itself unchanged.

### Test changes

3. **`tests/integration/test_me_router.py`.** Append one new sync test
   function `test_mc8_malformed_target_anchor_returns_422` mirroring
   the shape of `test_v7_invalid_code_format_pydantic_422`
   (`tests/integration/test_org_tree_writes_router.py:485-521`). Six
   assertion blocks per LD6:

   - MC8a — UUID with hyphens (`019df261-b87c-7d3e-ab9e-dcf26259cec6`;
     the cloud-reported failure shape). LOAD-BEARING.
   - MC8b — leading dot (`.tenant_root.region_us`). LOAD-BEARING.
   - MC8c — trailing dot (`tenant_root.region_us.`). LOAD-BEARING.
   - MC8d — consecutive dots (`tenant_root..region_us`). LOAD-BEARING.
   - MC8e — whitespace mid-path (`tenant_root region_us`). LOAD-BEARING.
   - MC8f — empty string. Correctness-only.

   Each block asserts (a) status 422 and (b) `target_anchor` identified
   in the failing `loc` of FastAPI's default detail envelope. Uses a
   TENANT JWT (random UUID `tenant_id`) per LD4 — the cloud bug shape
   is the TENANT path even though the Pydantic check itself is
   JWT-type-agnostic.

### Smoke and endpoint test scripts

4-6. Three scripts gain one assertion each:

- **`scripts/smoke_curl.sh`**: counter `(63 endpoints` -> `(64 endpoints`
  at line 47; new `18a` entry in the WHAT'S CHECKED comment list;
  new `req "me_can_do_ltree_validation_422"` after the existing
  `me_can_do_platform` line. Uses `$PJWT` (PJWT is always available;
  Pydantic 422 is JWT-type-agnostic; saves a TJWT-conditional block).
- **`scripts/test_endpoints.sh`**: new Phase 4i block before Phase 5
  Summary, single outside-matrix entry using `$P1_JWT_VALUE`.
- **`scripts/test_endpoints_cloud.sh`**: mirrors the Phase 4i block.

### Documentation

7. **`docs/endpoints/openapi.json`**: regen. Expect: only the
   `/api/v1/me/can-do` operation's `target_anchor` parameter changes
   (gains `pattern` and `maxLength` properties). No new paths; no new
   schemas.
8. **`BUILD_PLAN.md`**: new `### Step 6.20.2 — /me/can-do ltree input
   validation` block under `## 6.20 Bug Fixes`, after the Step 6.20.1
   block. Mirrors 6.20.1's shape.
9. **`CLAUDE.md`**: (a) new 1-line pointer in the Completed steps
   bullet list, after the Step 6.20.1 entry; (b) `RESOLVED 2026-05-19
   (Step 6.20.2)` marker appended to the existing FN-AB-61 entry at
   line 1403.
10. **This file**: new step doc per A6 convention.

### Verification

- Targeted: `uv run pytest tests/integration/test_me_router.py -v`.
- Full suite: `uv run pytest --tb=no -q`. Expect 671 -> 672.
- mypy strict: `uv run mypy --strict src/admin_backend`.
- check_setup: `./scripts/check_setup.sh`.
- Local smoke: launch uvicorn, run `scripts/smoke_curl.sh
  http://localhost:8000`. Expect 64 PASS.
- OpenAPI regen: curl `/api/v1/openapi.json` from local server,
  pipe through `json.dumps` for stable formatting, write to
  `docs/endpoints/openapi.json`. Diff should show only the
  `target_anchor` parameter changes.

### Verification harness — execution context

The fix runs against the existing local Postgres + seed data; no
fresh seed required. The new test does not depend on seeded grants
because Pydantic 422 fires before the gate dependency runs (the
random-UUID TENANT JWT in the test would otherwise produce 403 from
the gate body, but never reaches it). No cloud deploy in this commit;
cloud deploy batches with 6.18.2 + 6.18.3 + 6.20.2 at the next Phase
6 deploy.

## Retro

(filled at commit time per A7 convention)
