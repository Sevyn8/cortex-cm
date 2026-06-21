# Step 6.17.4 — Stores POST set-status endpoint

**Shipped.** 2026-05-18 in a single commit on `main`. First step
authored under the A7+A8+A9 discipline.

## Mental Model

State-transition endpoint on top of Step 6.17.3's write surface.
Single `POST /api/v1/stores/{store_id}/set-status` handler takes
`target_status` (and an optional, forward-compatible `reason`)
and produces an atomic UPDATE per a 9-cell liberal matrix: all
transitions allowed except `*->OPENING` (LD1), including CLOSED ->
ACTIVE / INACTIVE. Same-state is rejected per LD5 (target excluded
from its own allowed-sources set; mirrors tenants' `allowed_sources`
convention). Three SQL classes, dispatched on direction:

- **Class 1 (into-CLOSED):** populates `closed_at` + `closed_by_*`
  pair alongside the status flip and the `updated_*` audit pair.
- **Class 2 (out-of-CLOSED):** nulls the `closed_at` + `closed_by_*`
  pair atomically with the flip — `ck_stores_closed_consistency`
  forbids non-CLOSED rows from carrying a closed_at, so live row
  loses the closure metadata. LD2 accepts the trade-off; Step 6.2
  audit_log captures the full history when shipped.
- **Class 3 (between non-CLOSED):** `closed_*` untouched (already
  NULL by DDL CHECK invariant).

Deliberate divergence from the tenants per-action-endpoint pattern
(LD6): tenants ships `/suspend` + `/activate` URLs; stores has 4
states and 9 valid transitions, so it consolidates into one
`set-status` endpoint with `target_status` in body. Hyphenated URL
+ POST verb match the project-wide convention (per openapi.json
enumeration). Same gate as PATCH `/stores/{store_id}` (LD9 —
`ADMIN.STORES.CONFIGURE.TENANT` + `anchor_dep=get_store_anchor`,
multi-audience).

## Implementation Plan

Single commit per the WORKFLOW.md default. 11 work buckets:

- B1: `schemas/store.py` — `StoreSetStatusRequest` (`extra="forbid"`,
  `target_status: StoreStatus`, `reason: str | None = None`). Forward-
  compatibility comment cites LD3.
- B2: `schemas/__init__.py` — re-export.
- B3: `repositories/stores.py` — `TRANSITION_MATRIX` constant
  (module-level, mirrors tenants `allowed_sources` shape); `transition`
  method (SELECT FOR UPDATE + matrix check + dispatched UPDATE +
  expire_all + materialising read). `TransitionResult` imported from
  `repositories.tenants` (mirrors `tenant_users` precedent — same
  3-value shape).
- B4: `routers/v1/stores.py` — `set_store_status` handler with the
  cited gate + anchor dep; raises `InvalidStateTransitionError`
  per Finding A (no `resource` kwarg; `store_id` + `target_status`
  context kwargs land in `exc.context`).
- B5: `test_stores_repo_writes.py` extended with T1-T16
  (9 allowed cells + 3 *->OPENING rejects + same-state + NOT_FOUND
  + cross-tenant RLS-as-404 + Pattern (b) audit-actor invariants).
- B6: `test_stores_set_status_router.py` NEW (RT1-RT13 + MG) — body
  asserts only `status_code` + `code` per Finding B.
- B7: smoke + endpoint scripts +2 entries each
  (rejected-first / happy-second order per Obs 3 to preserve the
  ACTIVE source state for the happy transition).
- B8: verification harness — pytest 581 -> 611, mypy strict 76/76,
  check_setup 36/36, smoke 52 -> 54, OpenAPI regen +1 path.
- B9: docs — `docs/endpoints/stores.md` set-status section (8-section
  canonical); OpenAPI regen; step doc; CLAUDE.md pointer +
  FN-AB-NN entries; BUILD_PLAN 6.17.4 + 6.17 root TODO -> DONE-LOCAL.
- B10: Phase 5 exit Report-Pause per A7.

## Retro

- **A7+A8+A9 first-use observations.** A9 pre-flight gate (per-check
  Report-Pause) surfaced 2 substantive prompt-vs-codebase
  contradictions (LD4's `resource` kwarg, LD4's body `details` claim)
  before any code shipped. A8 discipline kept the prompt accurate on
  6/7 codebase claims it made (CHECK shape, TransitionResult value
  set, DDL constraint name, gate tuple — all cited and verified); the
  one slip (LD4 envelope claim) was caught by A9 rather than emerging
  during implementation. A7 commit-pause completed the loop. Net: 41
  / 41 new tests passed first try and 0 surprise findings landed
  during code or test work, which is the strongest signal yet that
  the three-sided discipline is paying off.

- **Operator-approved LD adjustments locked into code.**
  - **Finding A (LD4 raise pattern):** stores handler raises
    `InvalidStateTransitionError(internal_msg, store_id=str(...),
    target_status=body.target_status.value)`. No `resource` kwarg.
    Mirrors tenants exactly. Locked.
  - **Finding B (Q7 envelope):** router tests RT7 / RT8 assert
    `status_code` + `code` only. No body-details assertions. Matches
    tenants test precedent (test_s3, test_a3) and the live
    `build_error_payload` shape. Locked.
  - **Finding C (public_message flavor):** adopted Option A —
    `InvalidStateTransitionError.public_message` is reused as-is.
    Stores responses literally read "Tenant cannot transition to the
    requested state." today. FN-AB-NN tracks generalising the class.

- **CLAUDE.md Step 6.15 retro correction (per Obs 1).** Inline note
  added to the Step 6.15 retro entry pointing out that
  `TransitionResult` is NOT one-per-resource project-wide; only
  `modules_access` declares its own. `tenants`, `tenant_users`, and
  now `stores` share `repositories.tenants::TransitionResult`. The
  6.15 retro text overstated the convention; this commit lands the
  one-line correction.
