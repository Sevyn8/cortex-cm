# Step 6.10.1 — Tenant users write endpoints

**Shipped.** 2026-05-14 in a single commit per the new WORKFLOW.md default.

- 4 endpoints on `/api/v1/tenant-users` — POST (create + bundled role assignments), PATCH (full_name / email / roles replace-set), POST .../suspend, POST .../activate.
- All multi-audience (`audience=None`) gated on `ADMIN.USERS.CONFIGURE.TENANT` (SUPER_ADMIN + PLATFORM_ADMIN + OWNER).
- Handler-side self-edit guard for TENANT callers on the 3 path-bound endpoints.
- 4 new ClientError subclasses; 2 new request schemas; 3 new TenantUsersRepo methods; 31 router tests (5 LOAD-BEARING).
- Cloud deploy deferred per Phase 5.5 operator pause.

**Result.** Pytest 385 → 416 (+31). 0 xfail. mypy strict clean (73 src files). check_setup 35/35. Per-resource regression checkpoint clean. No DDL changes; no migrations; no seed Excel changes.

---

## Implementation Plan

Single commit per WORKFLOW.md default. Sixteen files touched:
4 new, 12 modify, 1 regen (openapi.json).

Public surface:
  - POST   /api/v1/tenant-users
  - PATCH  /api/v1/tenant-users/{user_id}
  - POST   /api/v1/tenant-users/{user_id}/suspend
  - POST   /api/v1/tenant-users/{user_id}/activate

All four multi-audience (`audience=None`); gated on
ADMIN.USERS.CONFIGURE.TENANT.

Code:
  - src/admin_backend/errors.py — 4 new ClientError subclasses
    (SelfEditForbiddenError, DuplicateTenantUserEmailError,
    InvalidRoleAudienceError, InvalidRoleError). Reuse
    TenantUserNotFoundError (shared since Step 6.9.3.2).
  - src/admin_backend/schemas/tenant_user.py — TenantUserCreateRequest,
    TenantUserPatchRequest, _dedupe_role_ids helper.
  - src/admin_backend/repositories/tenant_users.py — RoleIdList type
    alias; helpers _resolve_role_audience, _raise_if_email_taken,
    _lookup_tenant_root, _insert_role_assignments; methods create,
    update, transition (reuses TransitionResult enum from tenants repo).
  - src/admin_backend/routers/v1/tenant_users.py — 4 handlers;
    _actor_type_from_auth + _raise_if_self_edit helpers; self-edit
    guard handler-side, after gate, before repo.
  - src/admin_backend/auth/anchor_deps.py — UNCHANGED; existing
    get_tenant_user_anchor (shipped 6.9.3.2) is reused.

Tests:
  - tests/integration/test_tenant_users_writes_router.py — 31 new
    tests (C1-C9, P1-P12, S1-S5, A1-A5). Load-bearing: C3, C7, P3,
    P5, S4. Local cleanup fixture deletes
    tenant_user_role_assignments first (composite FK ON DELETE
    RESTRICT) then tenant_users.

Smoke + cloud:
  - scripts/smoke_curl.sh — 5 new entries; WHAT'S CHECKED 27 → 32.
    Suspend/activate against fresh INVITED users assert 409
    (state-transition matrix); 200 happy paths covered by
    integration tests.
  - scripts/test_endpoints.sh + scripts/test_endpoints_cloud.sh —
    Phase 4c block mirroring smoke flow.
  - docs/endpoints/openapi.json — regenerated.

Docs:
  - docs/architecture_RBAC.md — Appendix A applied (3 worked
    examples + audience=None subsection + Pattern (b) audit-actor
    note). Landing point adapted: live heading is "Adding a new
    endpoint (cookbook)" not "Worked examples"; intent preserved.
  - docs/endpoints/tenant-users.md — 4 new operation sections.
  - CLAUDE.md — Current-state entry; FN-AB-38/39/40/41.
  - BUILD_PLAN.md — Step 6.10 split into 6.10.1 (DONE-LOCAL),
    6.10.2 (TODO), 6.10.3 (TODO).
  - prompts/step-6_10_1-impl-2026-05-14.md — bundled per per-step
    convention.

Shipped at commit b6b76dd (HEAD on main, pre-relocate), built on
9261bfd (Step 6.11.2). 15 files changed, +5039 / -4523 (deletion
count dominated by openapi.json reformatting noise).

Step doc relocated to docs/implementation-steps/ at f516f46.

## Retro

### What shipped

Four write endpoints landed clean. pytest 385 → 416 (+31, all
passing, no xfails). mypy strict clean (73 source files).
check_setup 35/35. smoke 32/32. Per-resource regression
checkpoint held across 14 pre-existing files. EXPLAIN ANALYZE on
new query patterns all sub-millisecond at seed scale (email
pre-check 0.064 ms, transition FOR UPDATE 0.035 ms, tenant-root
anchor 0.032 ms).

All 9 Phase 1 locked decisions (D1-D9) honored, traced
end-to-end from prompt to shipped code in the staging report
section 6.

### What worked

**The pre-flight expansion (F11 fix) paid for itself on first
run.** Three of the four substantive surface-and-stop findings
(F1, F2, F4) were caught by the "read context docs end-to-end"
step, not by code-reading the impl files. F2 specifically — the
fact that TenantUserNotFoundError had been moved to the shared
errors.py at Step 6.9.3.2 — is the kind of historical-state
knowledge that lives only in CLAUDE.md's "Current state —
Completed" entries. Validates the WORKFLOW.md amendment.

**Single-commit default per the new WORKFLOW.md held.** No
foundations-vs-surface split was needed because no load-bearing
factory was modified (the audience kwarg consumption is already
codified at 6.11.2). Commit b6b76dd lands all 16 files cleanly.

**Pattern (b) audit-actor convention exercised on writes for
the first time.** Previously the pattern was passive (seed
loader did the writes as superuser). The discipline of populating
both `*_by_user_id` and `*_by_user_type` columns on every
INSERT/UPDATE held. The `_actor_type_from_auth` helper maps
AuthContext's Literal user_type to the actor_user_type_enum
cleanly. Test C9 explicitly asserts the pair populated; passed.

**Q7 envelope lock survived a contract test.** The original prompt
specified `details: {"invalid_role_ids": [...]}` on the two role
errors — a violation of the locked envelope shape. CC caught this
at pre-flight (F3) and surfaced; resolved to context-only logging.
Manual curl confirms `"details":null` on both error response bodies.
The locked decision held because the read-context-docs discipline
fired.

### What didn't (and would do differently)

**Adversarial readback at Phase 4 close missed F3.** The Q7
envelope lock was the most consequential prompt error in the step,
and the structure-conformance / design-sanity passes both gave it
a clean bill of health. The adversarial-readback pass (run only
after operator pushed for it) is what found 10 issues, but it
also missed F3 — the systematic check needed is "for every new
contract this prompt introduces, grep for existing locks that
govern it." Captured as WORKFLOW.md amendment A1.

**Three "already exists" findings (F1, F2, plus implicit
TransitionResult assumption).** Pattern: drafting a prompt adjacent
to recent work (6.9.x, 6.11.x) without a draft-time check against
the codebase. CC catches it at pre-flight (slow path; surfaces
mid-execution). A draft-time `grep -rn "def <symbol>\|class <symbol>"`
across the codebase for each new symbol would catch this
pre-pre-flight. Captured as WORKFLOW.md amendment A4.

**Smoke test happy-path deviation for state-transition endpoints.**
Smoke initially specified "suspend (200) + activate (200)" against
fresh seed users. Reality: fresh seed users are INVITED, and
INVITED → {SUSPENDED, ACTIVE} is structurally rejected. CC adapted
to assert 409 INVALID_STATE_TRANSITION on both, exercising the
gate + anchor + repo + transition matrix end-to-end. The 200
happy paths are covered by integration tests S1/S2/A1/A2 which
set up ACTIVE state via direct DB write. This pattern will recur
on every lifecycle-touching write endpoint. Captured as a new
CLAUDE.md convention.

**Code-volume gate at 30%/400 was loose.** Ran at 37% utilization
on the fixed prompt (129 measured / 347 cap). The 6.10.1 data
point favors tightening to 20%/250; the prompt would still pass
at 58% utilization. Captured as WORKFLOW.md amendment A3.

**Operator copy-paste friction noticed mid-step.** When response
text needed to be pasted into CC, inline prose required
selection-and-copy. A single-click copy box (via
message_compose_v1) is the right shape. Captured as WORKFLOW.md
amendment A5.

### Forward notes

Four FN-AB entries landed in CLAUDE.md at commit time:
  - FN-AB-38 — Cancel-invitation deferred to 6.10.3
  - FN-AB-39 — Auth0 invite-accept flow (Stage 3)
  - FN-AB-40 — Email-change Auth0 reconciliation (Stage 3)
  - FN-AB-41 — Anchored role bundling (Step 6.14)

### Metrics

  pytest                 385 → 416  (+31)
  mypy strict            clean (73 src files)
  check_setup            35/35
  smoke                  32/32
  per-resource floor     14 files held at baseline
  new file               test_tenant_users_writes_router.py: 31
  code-volume gate       37% utilization (129 / 347)
  files touched          16 (4 new, 12 modify, 1 regen)
  commit                 b6b76dd on main, +5039 / -4523

### Deploy posture

DONE-LOCAL per Phase 5.5 operator pause. Cloud deploy queued
behind Steps 6.11.1 + 6.11.2 + 6.10.1 batched together (no
migrations in any of the three, so deploy is image-build +
Cloud Run + smoke; no Cloud Run Job step needed).

### Post-deploy notes

Cloud deploy v0.1.11 (carrying Step 6.10.1) surfaced a CSD-03
cascade: 14 of 25 smoke entries 500'd due to unqualified enum
types in raw `text()` SQL across `auth/permissions.py`,
`auth/anchor_deps.py`, `repositories/tenants.py`, and
`repositories/tenant_users.py`. Same bug class as Step 6.5.1's
dashboard recurrence — different identifier shape (enum casts
instead of table names).

Three follow-up fix commits closed the bug class across all
layers: `dd496bd` (40 app-side cast sites), `1516484` (Alembic
migration `a0982a86985b` for two plpgsql trigger function
bodies), and `6204fbd` (55 test-layer sites exposed after
stripping the local DB role-default `search_path`).

The local role-default strip was the structural fix: local now
mirrors cloud's strict identifier resolution, so future CSD-03
bugs fail at pytest time rather than cloud-deploy time.
`scripts/check_setup.sh` now asserts the role default remains
stripped. CSD-03 convention extended in `CLAUDE.md` to cover all
three layers (app, plpgsql, tests).

A separate lesson surfaced in the v0.1.13 exhaustive cloud test:
29 failures, all confirmed as stale shell-script expectations
from Step 6.9.3.2's gate-audience retrofit (pytest was updated;
`smoke_curl.sh` + `test_endpoints_cloud.sh` weren't). Captured
as a `CLAUDE.md` gate-retrofit discipline rule.

This was the first real Phase 7 firing under the A6 amendment
(retro-as-conditional-pass triggered by cloud-emergent lessons).
