INVESTIGATION PROMPT — Step 6.9.3.2 design preparation (read-only)

You are doing a read-only investigation pass over the admin-backend
codebase at HEAD (commit ddea23c, "Step 6.9.3.1: scope cascade in
has_permission") to produce a code-grounded findings document. This
document feeds the Step 6.9.3.2 design conversation.

Do not edit any file. Do not make any code change. Test runs allowed
only for explicit verification tasks. Output is a single structured
findings document written to:

  /reports/step-6_9_3_2-design-investigation-<YYYY-MM-DD>.md

where <YYYY-MM-DD> is today's date in ISO format.

## Context

Step 6.9.3.2 is the second half of the original Step 6.9.3 split.

Section 6.9 sub-step status:
- Step 6.9.1 (SHIPPED at 63dd565): has_permission() pure-SQL permission
  check + PermissionGrant + ReasonCode
- Step 6.9.2 (SHIPPED at e0946b8): require() factory +
  PermissionDeniedError + /me/* endpoints
- Step 6.9.3.1 (SHIPPED at ddea23c): scope cascade in has_permission
  (satisfying_scopes helper + _SCOPE_CASCADE_ORDER tuple + ANY clause
  in SQL)
- Step 6.9.3.2 (NEXT): endpoint retrofit + per-resource anchor
  dependencies + mandatory-gate-discipline test

The original 6.9.3 investigation (reports/step-6_9_3-design-investigation-
2026-05-13.md, 32 findings) is still mostly valid for INVENTORY, ANCHOR,
GATE_REPLACE, DISCIPLINE, CATALOGUE concerns. However, Step 6.9.3.1
changed has_permission semantics (scope cascade now works); some of
the previous findings should be re-examined against post-cascade
reality.

This investigation re-verifies the prior investigation against HEAD
and adds new findings specific to post-cascade design decisions.

## Investigation tasks

Read the relevant source files. Produce a structured findings document.
Each finding follows this exact format:

  ### F-<area>-<number>: <one-line summary>

  **Question:** (one sentence — what this finding addresses)

  **Citation:** `<file>:<line range>` (or multiple if relevant)

  **Current code:** (excerpt; only the load-bearing portion)

  **Observation:** (specific implication for 6.9.3.2 design)

  **Confidence:** (high / medium / low)

  **Open question:** (if any — flag for design conversation)

Area codes for this investigation:
- VERIFY (verification of 6.9.3.1 shipped state and prior investigation's
  validity at new HEAD)
- INVENTORY (re-confirmation of the 17-endpoint retrofit list)
- POST_CASCADE (impact of scope cascade on prior 6.9.3 decisions —
  what simplifies, what stays the same)
- ANCHOR (per-resource anchor dependency mechanics)
- THREADING (target_anchor threading from anchor dep into gate factory)
- GATE_REPLACE (_require_platform_auth retirement, with post-cascade
  context per FN-AB-26 update)
- DISCIPLINE (mandatory-gate-discipline test mechanics + gate marker
  attribute)
- CATALOGUE (catalogue gap analysis re-examined under cascade
  semantics)

## Specific questions to investigate

### VERIFY — Confirm 6.9.3.1 shipped state at HEAD ddea23c

- F-VERIFY-1: Read src/admin_backend/auth/permissions.py at HEAD.
  Confirm presence of:
  - `_SCOPE_CASCADE_ORDER` module-level tuple (8 entries)
  - `satisfying_scopes(requested)` public helper
  - `_PERMISSION_SCOPE_ENUM_VALUES` frozenset
  - `_satisfying_scopes_for_sql(requested)` private companion
  - Modified has_permission SQL using `ANY(CAST(:satisfying_scopes AS
    permission_scope_enum[]))` in both PLATFORM and TENANT paths

- F-VERIFY-2: Run `uv run pytest --tb=no -q | tail -3` and confirm
  total pass count is 308 (post-6.9.3.1 baseline).

- F-VERIFY-3: Re-verify F-VERIFY-1 through F-VERIFY-4 from the
  6.9.3 investigation report against HEAD (6.9.1 + 6.9.2 deliverables
  still match design intent). If any 6.9.2 component has changed
  unexpectedly, surface.

- F-VERIFY-4: Confirm FN-AB-26 (require_platform_auth retirement)
  was updated in place with post-cascade context per the 6.9.3.1
  forward-note resolution. Confirm FN-AB-28 (PermissionScope enum
  expansion) added.

### INVENTORY — Re-confirm endpoint retrofit list

- F-INVENTORY-1: Re-run the route enumeration via app.routes against
  the latest app at HEAD ddea23c. Confirm 23 APIRoute objects total
  (matching the 6.9.3 investigation's F-INVENTORY-MASTER count).
  Surface any new endpoints added between e0946b8 and ddea23c that
  weren't in the prior investigation's master table.

- F-INVENTORY-2: For each of the 17 retrofit-eligible endpoints,
  re-check whether the "likely retrofit permission tuple" guess from
  the prior investigation still holds under scope-cascade semantics.
  Some endpoints may simplify (e.g., /tenants/{id} could now use
  ADMIN.TENANTS.VIEW.GLOBAL gating because GLOBAL cascades to
  TENANT-scope checks — wait, this is exactly what cascade does NOT
  do; cascade is on grants matching checks, not on endpoints matching
  user grants).
  
  Be careful here: the cascade direction is "a user's higher-scope
  grant satisfies a lower-scope check." NOT "a higher-scope endpoint
  check accepts lower-scope user grants."
  
  Restate the cascade rule precisely in this finding to anchor the
  rest of the investigation: gate-required tuple = the LOWER bound
  of authority needed; user grants at that scope or HIGHER satisfy
  it. So /tenants/{tenant_id} should be gated at the LOWEST scope
  appropriate for tenant-scoped data (TENANT), not the highest.

### POST_CASCADE — Impact of scope cascade on prior 6.9.3 decisions

- F-POST_CASCADE-1: The original 6.9.3 investigation surfaced 4 gap
  classes for multi-user-type endpoints (F-CATALOGUE-2):
  - /tenants/* and /dashboard/*: regression risk with GLOBAL gating
  - /module-access/*: no catalogue tuple
  - reference data (/lookups, /permissions, /permission-matrix)
  - /role-assignments
  
  Re-examine each under post-cascade semantics. Does cascade resolve
  any of these gaps without catalogue additions, or are catalogue
  additions still required?

- F-POST_CASCADE-2: With cascade, an endpoint can be gated at the
  LOWEST scope its semantics require (e.g., TENANT for /tenants/*).
  PLATFORM users with GLOBAL grants automatically satisfy via cascade.
  TENANT users need an explicit TENANT-scope grant.
  
  Re-evaluate whether new TENANT-scope tuples need to be added to
  the catalogue (e.g., ADMIN.TENANTS.VIEW.TENANT, ADMIN.DASHBOARD.*).
  If yes, list which ones. If no, explain why cascade alone suffices.

- F-POST_CASCADE-3: The original investigation's F-INVENTORY-MASTER
  Open question listed 4 candidate patterns for multi-user-type
  endpoints (a-d). Under cascade, audience-dispatch (option C) may
  no longer be needed. Re-state which patterns remain viable and
  which become unnecessary.

### ANCHOR — Per-resource anchor dependency mechanics

- F-ANCHOR-1: For each retrofit-target endpoint that needs a
  target_anchor (per the prior investigation's INVENTORY table),
  identify the lookup chain from path param to org_node.path.
  Confirm or refine the prior findings (F-ANCHOR-2 through F-ANCHOR-7
  in the previous report).

- F-ANCHOR-2: Specifically verify F-ANCHOR-2 from the prior
  investigation: TenantUser has NO home_org_node_id FK; per-tenant-user
  anchor defaults to tenant root (lookup chain tenant_user_id →
  tenant_id → org_nodes where node_type='TENANT' AND parent_id IS
  NULL → path). Confirm this still holds at HEAD ddea23c.

- F-ANCHOR-3: For each anchor lookup needed, determine if any
  existing Repo method already returns the org_node.path for the
  target row type, or whether a new method is needed. Cite the
  existing methods.

- F-ANCHOR-4: How does the ltree path get exposed for Cloud SQL
  compatibility? Is `org_nodes.path::text AS anchor_path` (text cast
  for transport) the established pattern, or does the existing code
  return ltree directly?

- F-ANCHOR-5: Will anchor dependency functions return `str | None`
  (matching has_permission's target_anchor parameter type), or
  something richer? Cite the existing target_anchor parameter usage
  in has_permission.

### THREADING — target_anchor threading from anchor dep into gate

The 6.9.2 design left target_anchor hardcoded to None inside the gate
factory's inner function. 6.9.3.2 must wire target_anchor from a
per-endpoint anchor dependency into the gate's has_permission call.

- F-THREADING-1: How does FastAPI compose multiple Depends in the
  same handler signature? If an endpoint declares BOTH
  Depends(require(...)) AND Depends(get_some_anchor), can the gate's
  inner function receive the anchor's output? Investigate FastAPI's
  dependency-of-dependency mechanics for this case. Cite FastAPI
  docs or experimental verification.

- F-THREADING-2: Three candidate threading shapes from the design
  conversation:
  - (a) Gate factory accepts an anchor_dep callable parameter:
    `require(MODULE, RESOURCE, ACTION, SCOPE, anchor_dep=get_store_anchor)`
    The factory composes both deps inside the gate function.
  - (b) Endpoint declares both gate AND anchor as parallel Depends.
    Gate uses FastAPI's dependency injection to receive the anchor
    value indirectly.
  - (c) Gate factory returns a configurable wrapper; endpoint passes
    target_anchor via a post-resolution mechanism.
  
  For each, surface the FastAPI mechanics that make it work or fail.
  Identify the cleanest pattern; flag if any requires significant
  framework gymnastics.

- F-THREADING-3: How does the gate know whether an endpoint should
  pass target_anchor or not? List endpoints (e.g., list endpoints,
  /me/* endpoints) handle target_anchor=None correctly today. Does
  the design need an explicit "no anchor" gate variant, or does
  target_anchor=None work uniformly?

- F-THREADING-4: For endpoints that DO need an anchor, what happens
  when the anchor lookup fails (e.g., the path param references a
  non-existent or RLS-invisible row)? Should the anchor dep return
  None (gate denies via has_permission's NO_MATCHING_GRANT), or raise
  404 directly (matches D-17 RLS-as-404)? Cite the precedent from
  Step 5.2's pattern.

### GATE_REPLACE — _require_platform_auth retirement (FN-AB-26)

- F-GATE-REPLACE-1: At HEAD ddea23c, verify _require_platform_auth
  is still at routers/v1/platform_users.py with 2 call sites
  (no change from the 6.9.3 prior investigation).

- F-GATE-REPLACE-2: Under scope cascade, what's the equivalent
  permission tuple for the 2 call sites? The 6.9.3 prior
  investigation suggested ADMIN.USERS.VIEW.GLOBAL. Re-verify by
  examining what authority _require_platform_auth actually checks
  (user_type == PLATFORM) and what permission tuple captures that
  semantically.

- F-GATE-REPLACE-3: With cascade, can the gate use the GLOBAL
  tuple? Confirm that gating a PLATFORM-only endpoint with
  require(ADMIN, USERS, VIEW, GLOBAL):
  - PLATFORM SUPER_ADMIN passes (has ADMIN.USERS.VIEW.GLOBAL directly)
  - PLATFORM PLATFORM_ADMIN with lower-tier grants — does this
    person exist in seed? Verify whether they'd have VIEW.GLOBAL.
  - TENANT user denied (no VIEW.GLOBAL grant, can't have one due to
    audience-check triggers)
  
  Cross-check against actual seed roles and grants.

- F-GATE-REPLACE-4: PlatformAccessRequiredError class — should it
  retire with _require_platform_auth, or stay? Survey other uses.

- F-GATE-REPLACE-5: Docstring references to _require_platform_auth
  in router files (the prior investigation found 4: org_tree.py,
  tenant_users.py, platform_users.py, test_platform_users_router.py).
  Confirm count at HEAD; these docstrings would update or remove
  as part of the retirement.

### DISCIPLINE — Mandatory-gate-discipline test mechanics

- F-DISCIPLINE-1: Re-verify F-DISCIPLINE-1 from the prior
  investigation: app.routes → APIRoute → route.dependant.dependencies
  → .call introspection still works at HEAD ddea23c.

- F-DISCIPLINE-2: Confirm that the gate's inner function in the
  require() factory still has NO marker attribute at HEAD (the
  prior investigation surfaced this; 6.9.3.1 didn't touch the
  factory). 6.9.3.2 must add the marker. Decide marker shape:
  - (a) Tuple of enum values: `gate.__permission_gate__ = (module,
    resource, action, scope)`
  - (b) Typed dataclass for richer assertion surface
  - (c) Simple sentinel (binary "is a gate" only)
  
  Cite reasoning per option.

- F-DISCIPLINE-3: PUBLIC_PATHS allowlist at middleware/auth.py
  unchanged at HEAD? Confirm. Verify the gate allowlist for
  discipline test = PUBLIC_PATHS ∪ {/api/v1/me/permissions,
  /api/v1/me/can-do}.

- F-DISCIPLINE-4: For the audience-dispatch gate factory (if 6.9.3.2
  introduces one — see POST_CASCADE), how does its marker attribute
  shape compare to the single-tuple gate's? Should the discipline
  test treat both factories uniformly, or have separate logic?

- F-DISCIPLINE-5: Where does the gate allowlist live? Prior
  investigation surfaced 3 options:
  - (a) New dedicated module auth/gate_allowlist.py
  - (b) Inline in the discipline test
  - (c) Extending PUBLIC_PATHS (rejected — different concerns)
  
  Re-cite the prior reasoning; design conversation picks.

### CATALOGUE — Permission catalogue under cascade semantics

- F-CATALOGUE-1: Re-execute the catalogue enumeration query from
  the prior investigation. Confirm 30 rows at HEAD ddea23c (no
  changes since 6.9.2). List all 30 tuples for design-conversation
  reference.

- F-CATALOGUE-2: For each of the 17 retrofit-eligible endpoints,
  state:
  - The proposed gate tuple (under cascade semantics — pick the
    lowest scope appropriate for the endpoint's semantics)
  - Whether that tuple exists in the current catalogue
  - If absent, what catalogue addition is needed
  - If present, which existing roles hold it (and whether the
    operationally-expected roles can pass the gate)

- F-CATALOGUE-3: Cross-reference with role assignments at HEAD.
  Verify that SUPER_ADMIN still holds all 30 catalogue permissions.
  Identify any role that, under the proposed gates, would lose
  access to endpoints it currently has via RLS-only or
  _audience_filter_for visibility. Flag as regression risks.

- F-CATALOGUE-4: Reference-data endpoints (/lookups, /permissions,
  /permission-matrix) — these expose catalogue data, not tenant
  data. Under cascade, what gate makes sense?
  - Option (a): exempt from gating (add to discipline test
    allowlist); reference data is inherently public for any
    authenticated user
  - Option (b): gate with a low-bar permission everyone has (e.g.,
    a stub MEMBERSHIP permission)
  - Option (c): gate with ADMIN.ROLES.VIEW.TENANT (already exists;
    OWNER + SUPER_ADMIN both hold it; but regular tenant users
    don't)
  
  Surface trade-offs; design conversation picks.

- F-CATALOGUE-5: Specifically for /tenants/* and /dashboard/*
  (the multi-user-type endpoints flagged by the prior investigation):
  list the EXACT new permission tuples that need to be added if
  the design picks "add TENANT-scope counterparts and gate at
  TENANT scope under cascade." E.g.:
  - ADMIN.TENANTS.VIEW.TENANT (new)
  - ADMIN.DASHBOARD.VIEW.GLOBAL (new)
  - ADMIN.DASHBOARD.VIEW.TENANT (new)
  
  Plus the role grants needed:
  - SUPER_ADMIN gets all new GLOBAL tuples
  - OWNER gets the new TENANT-scope counterparts
  - Other roles: verify per role's intended capability
  
  Identify any role that's currently expected to use these endpoints
  but wouldn't be granted access under the proposed addition.

- F-CATALOGUE-6: /module-access/* — no catalogue tuple exists at all.
  Surface the same exact-addition specification as F-CATALOGUE-5.

- F-CATALOGUE-7: /role-assignments — no specific catalogue tuple
  exists. The prior investigation suggested
  ADMIN.ROLE_ASSIGNMENTS.* as new tuples or ADMIN.USERS.VIEW.TENANT
  as a proxy. Cross-reference with the catalogue at HEAD.

## Constraints

- Read-only investigation. No edits, no commits.
- Test runs limited to F-VERIFY-2 (pytest count) and any catalogue/seed
  SQL queries needed for findings.
- Cite specific files and line ranges. Do not summarize from memory.
- If something cannot be found or does not exist, say so explicitly
  with confidence: low.
- If any assumption in this prompt is wrong (e.g., a file at a cited
  location doesn't match), call it out explicitly. Do not work around
  silently.
- Do NOT propose design decisions for Step 6.9.3.2. Surface facts and
  observations only; design happens in a separate conversation.
- Stay strictly inside the area codes (VERIFY, INVENTORY, POST_CASCADE,
  ANCHOR, THREADING, GATE_REPLACE, DISCIPLINE, CATALOGUE). Out-of-scope
  findings go in a separate "Open questions" section at the bottom.

## Output

A single markdown document written to:

  /reports/step-6_9_3_2-design-investigation-<YYYY-MM-DD>.md

Findings grouped by area. INVENTORY may be brief (re-verification of
prior 17-endpoint list). POST_CASCADE and CATALOGUE are likely the
longest sections.

Final section "Open questions for design conversation" consolidates
all "Open question" entries surfaced inside findings.

Scope-creep findings (Stage 3 Auth0, audit log writes, performance
caching, /me/* simplification revisit, etc.) go in a separate bullet
list at the bottom; do not investigate them inside the area sections.
