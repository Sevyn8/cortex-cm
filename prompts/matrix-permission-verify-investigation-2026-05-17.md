# Prompt — investigate matrix permission-deny handling (investigation only)

## Goal

Investigate whether the test_endpoints matrix scripts correctly verify
permission-denial responses. Recent cloud run (v0.1.14) showed 23
failures where the system returned 403/404 (likely correct RBAC
behavior) but the matrix expected 200. The matrix may be giving false
green signals on local because local seed has all grants, masking
real RBAC behavior the cloud run surfaces.

**This is investigation-only.** Do NOT modify any matrix scripts, test
files, or seed data in this step. Produce a written investigation
report with a remediation plan for the operator to review.

**Cloud access boundary: HARD NO.** Claude Code MUST NOT interact with
cloud infrastructure in any form. Specifically forbidden:
- No `gcloud` commands of any kind (run, jobs, sql, logs, etc.)
- No `curl` against any `*.run.app` URL or any cloud-hosted endpoint
- No SQL execution against Cloud SQL (no `gcloud sql connect`,
  no psql against cloud, nothing)
- No reading from Cloud SQL Studio, Cloud Logging, or any GCP console
- No image pushes to Artifact Registry, no Cloud Run service/job updates
- No use of cloud JWTs (`scripts/jwt/tokens/cloud/`) for live cloud
  testing

If the investigation needs cloud-side data (e.g., cloud's
`role_permissions` rows, Devon's role in cloud), STOP and ASK the
operator to run the specific query in Cloud SQL Studio and paste
results back. Provide the exact SQL the operator should run.

The operator is the only entity that touches cloud in this session.
Claude Code's job is to read local code/data, run local queries,
form hypotheses, and tell the operator which cloud-side facts are
needed to validate those hypotheses. The operator then runs those
queries and supplies the data back to Claude Code for analysis.

## Pre-flight reading

Read these before forming conclusions:

- `scripts/test_endpoints_max_view.sh` — local matrix
- `scripts/test_endpoints_cloud.sh` — cloud matrix (Phase 4 should be
  byte-identical to local per commit `18d4e60` convention; verify)
- `src/admin_backend/auth/gate.py` (or wherever the permission gate
  factory lives — Step 6.9.2 introduced this)
- `src/admin_backend/auth/permissions.py` — `has_permission()` core
- The router files for endpoints involved in the 23 failures:
  - `src/admin_backend/routers/v1/platform_users.py`
  - `src/admin_backend/routers/v1/tenants.py`
  - `src/admin_backend/routers/v1/org_tree.py` (the `ot_flow__tenant_no_grant_deny` 404 vs 403 question)
- Step retro docs in `docs/implementation-steps/` for 6.9.x and
  6.11.x — these likely document the expected 403/404 contracts
- `CLAUDE.md` — specifically the D-17 RLS-as-404 convention

## Part A — Verify local-vs-cloud divergence

Run the matrix locally and capture the result:

```bash
cd ~/ithina-retail/admin-backend
# Ensure uvicorn is running on :8000
./scripts/test_endpoints_max_view.sh
```

Report:
1. Total cells, passed, failed (compare against cloud's 340/317/23)
2. If local fails any of the 23 cells cloud failed: which ones, and
   are the cloud failures a strict superset of local failures?
3. If local passes 340/340: the 23 failures are pure cloud-vs-local
   divergence — meaning either (a) cloud's seed/grants are stale or
   (b) the matrix expectations are wrong for the cloud reality

## Part B — Diagnose each of the 23 cloud failures

Failures fall into 3 groups (from the v0.1.14 smoke).

For each cloud-side fact you need (role assignments, role_permissions
rows, etc.): **prepare the exact SQL** that would answer the question
and present it to the operator. Operator runs it in Cloud SQL Studio
and pastes results back. Do not execute against cloud yourself.

**Group 1 (8 cells) — `devon_P__plat_users__*`**

Devon is the P2 (secondary PLATFORM) caller. All /platform-users
endpoints return 403 to him. Expected 200/404/400.

Investigation steps:
- Read the /platform-users router(s). Identify the permission gate(s)
  applied (e.g., `Depends(require_permission(module=ADMIN,
  resource=USERS, action=VIEW, scope=GLOBAL))` or similar)
- From LOCAL DB: query Devon's local role + role_permissions to confirm
  whether the local equivalent has the required permission. (Local
  query you CAN run directly — local Postgres is fair game.)
- From LOCAL DB: query Anjali's local role + role_permissions to
  confirm she has the permission Devon lacks (explaining why she
  passes the same cells)
- Cloud-side query needed: Devon's role assignment and grants. Present
  the SQL to operator; do not run it yourself

**Group 2 (14 cells) — `marcus-t_T__tenants__*` and `a-kowalski_T__tenants__*`**

TENANT callers hitting /tenants list/search/sort/stats — all 403.
Matrix expects 200.

Investigation steps:
- Read the /tenants GET router(s). Identify the gate for TENANT
  audience access
- From LOCAL DB: query marcus-t and a-kowalski equivalents' local role
  and role_permissions. Do they have the permission locally?
- Cloud-side queries needed: their cloud role assignments and grants.
  Present SQL to operator

**Group 3 (1 cell) — `ot_flow__tenant_no_grant_deny` (got 404, expected 403)**

This one's different. The TEST INTENT was "tenant without the grant
should be denied". The system returns 404 instead of 403. Per D-17
(RLS-as-404 convention in CLAUDE.md), permission denials on resources
the user shouldn't even know exist correctly return 404.

Investigation steps (local-only, no cloud needed):
- Read CLAUDE.md D-17 to confirm the RLS-as-404 pattern
- Read the org-tree write router's permission gate
- Read the matrix cell's intent (comments, surrounding cells)
- Determine: is 404 correct per D-17, or is 403 expected per
  some other contract?

## Operator-supplied context (2026-05-17)

**Cloud SQL and local Postgres are nearly in sync at the data level**
as of this investigation. The operator confirms that role_permissions,
user_role_assignments, and lookups rows are aligned between local and
cloud, modulo at most a small recent delta.

This means: **Plan X (cloud seed sync) is unlikely to be the
answer.** The 23 failures are more likely to be one of:

- Matrix expected statuses are wrong (Plan Y) — the system is
  correctly returning 403/404, the matrix just expected 200
- Local-vs-cloud divergence at a layer that isn't seed data (RLS
  evaluation, schema-qualification, search_path, GUC handling)
- Real bugs in specific endpoints

Lead with these hypotheses. If your investigation reveals significant
seed-data drift contradicting the operator's claim, surface that
immediately — it would be useful operator information.

## Part C — Identify the underlying problem class

After investigating, the question is: which of these are operating?

1. **Matrix expected statuses are wrong (PRIMARY HYPOTHESIS per
   operator context).** The matrix was written assuming all callers
   can hit all endpoints. As permission gates were added in Step
   6.9.3.2 (the endpoint retrofit) and 6.11+/6.14 write endpoints,
   the matrix's expected statuses weren't updated for the new
   contracts. The matrix tests the WIRE response, not the
   EXPECTED-PER-RBAC response.

2. **Local-vs-cloud environment divergence below the seed-data
   level.** Things like search_path handling, RLS policy
   evaluation, schema-qualification of identifiers in DB functions,
   GUC propagation. Same data, different runtime behavior. The
   CSD-03 fix earlier today was an instance of this class.

3. **Real bugs.** Some endpoints might have over-restrictive gates.
   The matrix is the canary; investigation confirms or rules out.

4. **Seed-state drift between local and cloud (UNLIKELY per
   operator context, but verify).** If found, surface as a
   contradiction to the operator's stated state.

## Part D — Remediation plan to present to operator

Based on Part C's findings, propose ONE or MORE of the following:

**Plan X — Cloud seed sync.**

If cloud's role_permissions / *_user_role_assignments rows are
genuinely stale vs local, propose:
- A SQL script (operator-run in Cloud SQL Studio) that brings cloud's
  grants in sync with the current XLSX seed
- Specific INSERT/UPDATE statements per missing row, with expected
  row counts so the operator can sanity-check
- Whether this is one-time (just for tonight) or needs to become
  part of the deploy playbook

Claude Code prepares the SQL and supporting analysis. Operator
executes against cloud.

**Plan Y — Matrix expectation update.**

If the matrix was wrong about expected statuses, propose:
- For each affected cell, what the correct expected status is
- The discipline change: "every cell's expected status must be
  derived from the router's gate decorator, not assumed from
  caller-type defaults"
- Whether this needs to be a one-shot fix or a structural change
  to the matrix's cell-spec format

**Plan Z — Permission-aware matrix annotations.**

A structural improvement: extend the matrix's cell spec to include
the permission gate the cell exercises (e.g., `module=ADMIN
resource=USERS action=VIEW scope=GLOBAL`). The matrix would then
assert:
- Caller has the grant → expect 200
- Caller does NOT have the grant → expect 403 or 404 (per D-17)

This makes the test self-verifying against permission state changes.
More work than Plan Y, but durable.

**Plan W — RBAC parity check between local and cloud.**

A pre-deploy verification step that diffs local's permission catalogue
against cloud's. Surfaces drift before it bites in production.

Note: any implementation that requires cloud access (running a script
that queries Cloud SQL) is operator-executed. Claude Code can design
the SQL queries and the comparison logic, but the operator runs them
against cloud.

## Stop and ask if

1. The local matrix run produces failures that overlap with the cloud
   23. Surface the overlap — it changes the diagnosis materially
2. Reading routers reveals an inconsistent gate pattern (e.g., some
   endpoints use `Depends(require_permission(...))`, others use
   inline `if not has_permission(...): raise PermissionDeniedError`).
   Surface the inconsistency
3. The D-17 RLS-as-404 pattern doesn't clearly apply to /platform-users
   (Group 1). PLATFORM resources don't have RLS in the same shape as
   TENANT resources. Surface and propose how to reconcile
4. Cloud's `role_permissions` count differs from local's by more than
   a small delta (e.g., local has 120, cloud has 80). That's serious
   drift — surface immediately
5. Devon's role assignment shows him with a role that SHOULD have the
   permission per local, but the role_permissions row is missing in
   cloud. Surface — that's the smoking gun for seed drift

## Acceptance criteria

1. Local matrix run completed and result documented
2. Each of the 23 cloud failures investigated; for each:
   - Root cause (seed drift, matrix expectation drift, real bug, or
     unclear)
   - Whether local exhibits the same failure
   - Evidence: SQL query results, router source line numbers, or
     CLAUDE.md convention citations
3. Failures grouped by root cause
4. Remediation plan presented covering the failure groups with
   concrete next-step actions
5. NO matrix scripts, test files, or seed data modified
6. NO migrations created
7. NO commits made
8. Report written as one batched message (no incremental
   surface-and-stop unless one of the Stop-and-ask conditions fires)

## Report to operator (final deliverable)

Single message with these sections:

1. **Local matrix result**: Total/Passed/Failed counts; whether any
   of the 23 cloud failures also fail locally
2. **Per-failure diagnosis** (table or grouped list):
   - Cell name
   - Cloud status / expected status
   - Local status (does it fail locally too?)
   - Root cause category (seed drift / matrix wrong / real bug)
   - Supporting evidence (one line)
3. **Diagnosis summary**: which of the 4 problem classes from Part C
   are operating, and which cells belong to each
4. **Remediation plan**: Plans X/Y/Z/W (one or more), with concrete
   actions the operator can authorize. For each plan:
   - What it fixes (which failure groups)
   - Effort estimate (rough hours)
   - Risks / side effects
5. **Recommended sequence**: if multiple plans are needed, in what
   order should they run

Wait for operator authorization before doing any remediation work.
This investigation should NOT result in any commits.
