INVESTIGATION PROMPT — Step 6.9.2 design preparation (read-only)

You are doing a read-only investigation pass over the admin-backend
codebase at HEAD (commit 63dd565, "Step 6.9.1: has_permission() core 
+ PermissionGrant + ReasonCode") to produce a code-grounded findings 
document. This document will feed the design conversation for 
Step 6.9.2.

Do not edit any file. Do not make any code change. Do not run tests 
beyond what's required for verification. Output is a single 
structured findings document written to:

  /reports/step-6_9_2-design-investigation-<YYYY-MM-DD>.md

where <YYYY-MM-DD> is today's date in ISO format.

## Context

Step 6.9.2 wires the resolver (6.9.1's has_permission) into FastAPI 
as a gate dependency, ships /me/permissions and /me/can-do endpoints, 
and adds PermissionDeniedError. Three sub-steps in Section 6.9:

- Step 6.9.1 (SHIPPED at commit 63dd565): has_permission() pure-SQL 
  permission check, PermissionGrant frozen dataclass, ReasonCode 
  enum. Callable but not yet called by production.
- Step 6.9.2 (next): FastAPI dependency wiring + /me endpoints + 
  PermissionDeniedError exception class.
- Step 6.9.3 (after): Retrofit existing GET endpoints with the gate.

This investigation prepares for the design conversation that will 
shape 6.9.2's implementation prompt.

Design decisions already locked from 6.9.1's design conversation:
- has_permission() is the single-tuple SQL check. The gate dependency 
  calls it once per check.
- The gate is a FastAPI dependency, declared via 
  Depends(require(module, resource, action, scope)).
- /me/permissions returns the user's full permission set as JSON 
  (runs its own broader query, NOT has_permission). Frontend consumes 
  for UI gating.
- /me/can-do is a single-permission check (calls has_permission).
- PermissionDeniedError is a ClientError subclass, HTTP 403, carries 
  module/resource/action/scope/reason_code/developer_detail.
- Three-layer error model: structured fields for audit, user_message 
  property for response body, developer_detail for application logs.
- No spec from frontend on /me/* response shapes yet; design will 
  pick one in the upcoming conversation.

This investigation surfaces facts the design conversation needs. It 
does NOT design.

## Investigation tasks

Read the relevant source files in src/admin_backend/, tests/, and 
prompts/. Produce a structured findings document. Each finding 
follows this exact format:

  ### F-<area>-<number>: <one-line summary>

  **Question:** (one sentence — what this finding addresses)

  **Citation:** `<file>:<line range>` (or multiple if relevant)

  **Current code:** (excerpt; only the load-bearing portion)

  **Observation:** (specific implication for 6.9.2 design)

  **Confidence:** (high / medium / low)

  **Open question:** (if any — flag for design conversation)

Use the following area codes: VERIFY (verification of 6.9.1's actual 
shipped shape against design intent), GATE (existing per-endpoint 
gate patterns), DEPEND (FastAPI dependency wiring patterns), ERR 
(error class hierarchy), TEST (security test patterns), ROUTER 
(existing /me/* or similar patterns; /me design surface).

## Specific questions to investigate

### VERIFY — Confirm 6.9.1 shipped as designed

- F-VERIFY-1: Read src/admin_backend/auth/permissions.py at HEAD. 
  Confirm: has_permission() signature is 
  `(session, auth, module, resource, action, scope, target_anchor=None) 
  -> tuple[bool, ReasonCode, str]`. Note any deviation.
- F-VERIFY-2: Read src/admin_backend/auth/permission_grant.py at 
  HEAD. Confirm PermissionGrant is @dataclass(frozen=True) with 
  5 fields (module, resource, action, scope, anchor_path). Note 
  any deviation.
- F-VERIFY-3: Read src/admin_backend/auth/reason_code.py at HEAD. 
  Confirm ReasonCode is StrEnum with two values GRANT_MATCHED and 
  NO_MATCHING_GRANT_OR_OUT_OF_SCOPE. Note any deviation.
- F-VERIFY-4: Run `uv run pytest tests/integration/test_has_permission.py 
  --tb=no -q` and confirm all 13 tests still pass at HEAD.
- F-VERIFY-5: Run `uv run pytest --tb=no -q | tail -3` and confirm 
  total pass count is 276 (the post-6.9.1 baseline).

### GATE — Existing per-endpoint gate patterns

- F-GATE-1: Step 5.1's `_require_platform_auth(auth)` pattern. 
  Read src/admin_backend/routers/v1/platform_users.py. Cite the 
  helper definition, its raise behavior, and where in the handler 
  body it's called (top-of-function vs inside session vs other). 
  Observation: can the new resolver-driven gate REPLACE this, or 
  must it coexist?

- F-GATE-2: Step 6.1's `_audience_filter_for(auth)` pattern. Find 
  it in src/admin_backend/repositories/permission_matrix.py (or 
  wherever it lives at HEAD). Cite the function. Observation: this 
  is a Repo-layer filter, not a router-layer gate. The new gate is 
  router-layer (FastAPI dependency). They don't conflict; they 
  layer. Note the layer separation.

- F-GATE-3: Are there OTHER per-endpoint gate-shaped patterns 
  in src/admin_backend/routers/v1/ I should know about? Survey 
  every router file's imports and handler-top boilerplate. List 
  any helper that's called at the start of handlers and raises if 
  the request shouldn't proceed.

- F-GATE-4: How does Step 5.1's `_require_platform_auth` surface 
  via Depends vs direct call? Cite the exact pattern. Does FastAPI's 
  dependency system get used, or is it a direct call at handler-top? 
  This is load-bearing for 6.9.2's design — the `require()` factory 
  must follow whichever pattern is established.

### DEPEND — FastAPI dependency wiring

- F-DEPEND-1: How is `get_tenant_session_dep` defined? Cite the 
  exact decorator, signature, and yield pattern. The new 
  get_permission_set dependency (if added) follows whatever shape 
  this uses.

- F-DEPEND-2: How is `get_auth_context` injected into handlers? 
  Cite from a router that uses it. Confirm: middleware sets 
  request.state.auth; handlers receive via Depends. Same pattern 
  for the gate (Depends).

- F-DEPEND-3: Are there any dependency FACTORIES in the codebase 
  — functions that return a Depends-injectable callable when called 
  with arguments? FastAPI supports this: 
  `def make_dep(arg) -> Callable: ... ` then 
  `Depends(make_dep("x"))`. Search for this pattern. If not found, 
  this is novel territory for 6.9.2; the `require(module, resource, 
  action, scope)` factory is the first one.

- F-DEPEND-4: How does FastAPI surface exceptions raised inside 
  a dependency? Are existing client errors (e.g., 
  PlatformAccessRequiredError) raised inside dependencies or only 
  inside handlers? Cite the precedent for raising-inside-Depends 
  in this codebase. If no precedent exists, surface — 6.9.2 will 
  establish one.

- F-DEPEND-5: How does dependency-of-dependency ordering work in 
  this codebase? E.g., if get_permission_set depends on 
  get_tenant_session_dep AND get_auth_context, what's the 
  resolution order FastAPI uses, and what happens if one raises?

### ERR — Error class hierarchy

- F-ERR-1: Read src/admin_backend/errors.py at HEAD. Cite the 
  base classes (ClientError, ServerError) and their structure: 
  http_status, code, public_message, details fields. The new 
  PermissionDeniedError must fit this shape.

- F-ERR-2: PlatformAccessRequiredError is the closest precedent 
  for a 403-shaped client error. Cite its definition. Note its 
  fields, message format, and how it surfaces in the response 
  body (the JSON envelope).

- F-ERR-3: How does the FastAPI exception handler convert these 
  errors to HTTP responses? Cite the handler (likely in main.py 
  or a middleware). What's the JSON envelope shape? D-31 says 
  append-only response shapes — confirm PermissionDeniedError's 
  fields fit the envelope without breaking it.

- F-ERR-4: How are error codes (like "TENANT_USER_NOT_FOUND") 
  surfaced — string constants in the class, in an enum, in a 
  registry? The new PermissionDenied error code (like 
  "PERMISSION_DENIED") needs to follow whatever convention 
  exists. Surface the convention.

### TEST — Security test patterns

- F-TEST-1: Step 5.1's A2 test (PLATFORM-only gate) — exact 
  location, structure, fixture usage. The new gate's "deny on 
  no permission" test mirrors this.

- F-TEST-2: Step 5.2's T9 test (RLS-as-404) — exact location 
  and structure. Not directly mirrored by 6.9.2 (RLS vs 
  permission gate are different layers), but the test scaffolding 
  pattern is the same.

- F-TEST-3: Step 6.8.3's R2 test (TENANT JWT short-circuits 
  platform-side with no-call invariant via Repo patch) — exact 
  location. The "gate must run before DB session is opened" 
  property (if 6.9.2 wants it) tests similarly.

- F-TEST-4: How are FastAPI route-tree assertions made in 
  existing tests? Any test that iterates `app.routes` and 
  asserts properties? 6.9.3's mandatory-gate-discipline test 
  will need this pattern; if it doesn't exist, 6.9.2's TEST 
  area should flag the gap.

- F-TEST-5: LOAD-BEARING test convention. How are tests marked 
  as LOAD-BEARING in code (docstring? marker? comment?). Cite 
  examples. The 6.9.2 gate test for cross-tenant denial would 
  be LOAD-BEARING and should use the existing convention.

### ROUTER — /me/* design surface

- F-ROUTER-1: Are there existing /me/* endpoints in the codebase? 
  Search src/admin_backend/routers/v1/. If yes, cite their 
  structure (mount path, response shape, auth requirement). If 
  no, this is greenfield — 6.9.2 establishes the /me/* pattern.

- F-ROUTER-2: Response envelope convention. D-30 says envelope 
  is list-only for list endpoints; D-31 says append-only fields. 
  How do non-list endpoints (like single-resource GETs) shape 
  their responses? Cite an example. /me/permissions returns a 
  list of grants; /me/can-do returns a single object. Note the 
  shape each should follow.

- F-ROUTER-3: How are router files registered with the main app? 
  Cite main.py's router includes. 6.9.2's new me router needs 
  the same registration.

- F-ROUTER-4: Per-endpoint documentation pattern. The 8-section 
  endpoint doc convention (per tenants.md, platform-users.md). 
  /me/permissions and /me/can-do each need a doc file. Confirm 
  the convention is still active and what the canonical example 
  is.

## Constraints

- Read-only investigation. No edits, no commits, no significant 
  test runs (the F-VERIFY-4/5 test runs are allowed for verification 
  only).
- Cite specific files and line ranges. Do not summarize from memory.
- If something cannot be found or does not exist in the codebase, 
  say so explicitly with confidence: low and explain.
- If any assumption in this prompt is wrong (e.g., a design 
  decision contradicts what is in the codebase at HEAD), call it 
  out explicitly. Do not work around incorrect assumptions silently.
- Do NOT propose design decisions for Step 6.9.2. Surface facts 
  and observations only; design happens in a separate conversation.
- Stay strictly inside the area codes listed above (VERIFY, GATE, 
  DEPEND, ERR, TEST, ROUTER). If findings naturally surface that 
  belong to outside areas (e.g., 6.9.3 retrofit territory, 
  performance optimisation, Auth0), note them in the "Open 
  questions" section at the bottom but do not investigate them.

## Output

A single markdown document written to:

  /reports/step-6_9_2-design-investigation-<YYYY-MM-DD>.md

with the F-<area>-<number> findings grouped by area (VERIFY, GATE, 
DEPEND, ERR, TEST, ROUTER). Plus a final section "Open questions 
for design conversation" consolidating all "Open question" entries 
surfaced inside findings.

Do not include scope-creep findings (6.9.3 retrofit, Stage 3 Auth0, 
performance) inside the area sections; surface them as separate 
bullet notes in the Open questions section.
