INVESTIGATION PROMPT — Step 6.9.3 design preparation (read-only)

You are doing a read-only investigation pass over the admin-backend
codebase at HEAD (commit e0946b8, "Step 6.9.2: gate factory +
PermissionDeniedError + /me/* endpoints") to produce a code-grounded
findings document. This document will feed the design conversation
for Step 6.9.3.

Do not edit any file. Do not make any code change. Do not run tests
beyond what's required for verification. Output is a single
structured findings document written to:

  /reports/step-6_9_3-design-investigation-<YYYY-MM-DD>.md

where <YYYY-MM-DD> is today's date in ISO format.

## Context

Step 6.9.3 retrofits existing GET endpoints with the require() gate
factory from 6.9.2, ships per-resource anchor dependencies, and adds
the mandatory-gate-discipline meta-test. This is the broadest step
in Section 6.9 because it touches every router file.

Section 6.9 sub-step status:
- Step 6.9.1 (SHIPPED at 63dd565): has_permission() + PermissionGrant
  + ReasonCode
- Step 6.9.2 (SHIPPED at e0946b8): require() factory +
  PermissionDeniedError + /me/permissions + /me/can-do
- Step 6.9.3 (next): retrofit existing endpoints; per-resource anchor
  dependencies; mandatory-gate-discipline test

This investigation prepares for the design conversation that will
shape 6.9.3's implementation prompt.

Open items carried forward from earlier steps:
- FN-AB-25 (DECISION LOCKED to pattern (b) per-endpoint anchor deps;
  implementation lands at 6.9.3)
- FN-AB-26 (`_require_platform_auth` retirement decision — defer
  resolved during 6.9.2 design; revisit now)
- FN-AB-27 (/me/permissions response shape simplification — revisit
  if frontend integration friction surfaces)

This investigation surfaces facts the design conversation needs. It
does NOT design.

## Investigation tasks

Read the relevant source files in src/admin_backend/, tests/, and
the permission catalogue. Produce a structured findings document.
Each finding follows this exact format:

  ### F-<area>-<number>: <one-line summary>

  **Question:** (one sentence — what this finding addresses)

  **Citation:** `<file>:<line range>` (or multiple if relevant)

  **Current code:** (excerpt; only the load-bearing portion)

  **Observation:** (specific implication for 6.9.3 design)

  **Confidence:** (high / medium / low)

  **Open question:** (if any — flag for design conversation)

Use the following area codes: VERIFY (verification of 6.9.2's actual
shipped shape against design intent), INVENTORY (enumeration of every
existing endpoint with its current auth and likely required
permission), ANCHOR (per-resource org_node.path lookup mechanics),
GATE_REPLACE (_require_platform_auth replacement analysis),
DISCIPLINE (mandatory-gate-discipline test mechanics), CATALOGUE
(permission catalogue gap analysis vs retrofit needs).

## Specific questions to investigate

### VERIFY — Confirm 6.9.2 shipped as designed

- F-VERIFY-1: Read src/admin_backend/auth/permissions.py at HEAD.
  Confirm require(module, resource, action, scope) factory exists
  with the locked signature. Confirm target_anchor=None is hardcoded
  inside the inner gate function.

- F-VERIFY-2: Read src/admin_backend/errors.py at HEAD. Confirm
  PermissionDeniedError is a ClientError subclass with
  http_status=403, code="PERMISSION_DENIED".

- F-VERIFY-3: Read src/admin_backend/routers/v1/me.py at HEAD.
  Confirm me_router with /me prefix; routes /permissions and
  /can-do present.

- F-VERIFY-4: Confirm get_permissions_for_user lives in
  auth/permissions.py (Q5 / FN-AB-25 design locked it there).

- F-VERIFY-5: Run `uv run pytest --tb=no -q | tail -3` — confirm
  total pass count is 294 (post-6.9.2 baseline).

### INVENTORY — Every existing endpoint at HEAD

This is the master retrofit checklist. For EACH endpoint defined in
src/admin_backend/routers/v1/, produce a row in a table.

- F-INVENTORY-1 through F-INVENTORY-N: One finding per router file
  surveyed, OR a single F-INVENTORY-MASTER finding with a
  comprehensive table.

Each endpoint row should capture:

  - **Path** — e.g., `GET /api/v1/tenants`
  - **Handler** — e.g., `tenants.list_tenants`
  - **Current auth** — one of: none, `_require_platform_auth`,
    `_audience_filter_for` (Repo-layer filter, not a router gate),
    RLS-only, or other (specify)
  - **Likely retrofit permission tuple** — `(module, resource,
    action, scope)`. Make a best guess based on the endpoint's
    purpose and the catalogue; surface uncertainty per endpoint as
    Open question.
  - **target_anchor source** — one of: `None` (list endpoint or no
    specific target), `path_param → repo lookup → org_node.path`
    (specify the lookup chain), `direct org_node.path` (when the
    path param IS the org_node id).
  - **Currently gated by `_require_platform_auth`?** — yes/no.
    Affects FN-AB-26 retirement scope.

Routers to enumerate (verify list is complete at HEAD):

  - routers/v1/tenants.py
  - routers/v1/tenant_users.py
  - routers/v1/platform_users.py
  - routers/v1/org_tree.py
  - routers/v1/lookups.py
  - routers/v1/rbac.py (roles + permissions + matrix sub-routers)
  - routers/v1/dashboard.py
  - routers/v1/modules_access.py
  - routers/v1/role_assignments.py
  - routers/v1/me.py (NOT retrofitted — caller's own state)
  - routers/v1/health.py if it exists (NOT retrofitted — public)

For each endpoint, also note any quirks: multi-user-type behaviour,
existing audience-filter usage, RLS dependencies, anything that
makes the retrofit non-mechanical.

### ANCHOR — Per-resource org_node.path lookup mechanics

For each resource type that will need its own anchor dependency
(`get_<resource>_anchor`), surface the lookup chain.

- F-ANCHOR-1: Stores. Path param is `store_id`. How does the
  application reach `org_node.path`? Cite the existing Repo method
  (if any) or the FK relationship in the ORM model.

- F-ANCHOR-2: Tenant users. Path param is `tenant_user_id`. Does
  the tenant_user have a `home_org_node_id` FK? If yes, the anchor
  is `tenant_user.home_org_node.path`. If no, the anchor is the
  tenant root (`org_node where node_type='TENANT' and tenant_id =
  tura.tenant_id`).

- F-ANCHOR-3: Tenants. Path param is `tenant_id`. The tenant's
  anchor is the tenant-root org_node (`org_node where
  node_type='TENANT' and tenant_id = :tenant_id`).

- F-ANCHOR-4: Org_nodes. Path param IS the `org_node_id`. Direct
  lookup via `org_node.path` for that row.

- F-ANCHOR-5: Role assignments. Path param is `assignment_id` (if
  /role-assignments/{id} exists) or none (if list-only). For
  tenant-side assignments, the anchor is `assignment.anchor_org_node.path`.

- F-ANCHOR-6: Platform users. PLATFORM-scoped permissions; no
  target_anchor needed (anchor=None on all PLATFORM grants).

- F-ANCHOR-7: Roles, permissions, modules. Catalogue resources;
  generally PLATFORM-scoped (admin-managed); target_anchor=None.

- F-ANCHOR-8: Per-resource Repo methods that already do the lookup
  for OTHER purposes. If `StoresRepo.get_by_id` already fetches
  the store with its org_node relationship, the anchor dependency
  reuses that mechanism. Cite the existing methods.

Question to surface as Open: should each anchor dependency be a
small standalone function (one file or one module per resource),
or should they be grouped together in a `routers/v1/_anchor_deps.py`
helper module?

### GATE_REPLACE — `_require_platform_auth` retirement (FN-AB-26)

- F-GATE-REPLACE-1: Locate every `_require_platform_auth(auth)`
  call site in src/admin_backend/. Verify the count (was 2 at
  Step 5.1; surface if changed).

- F-GATE-REPLACE-2: For each call site, identify the endpoint and
  its likely retrofit permission tuple. For
  `GET /api/v1/platform-users`, is the right replacement
  `ADMIN.USERS.VIEW.GLOBAL`? Verify by checking the seed catalogue
  for that tuple's presence.

- F-GATE-REPLACE-3: Does SUPER_ADMIN role have grants for every
  permission tuple identified in F-GATE-REPLACE-2? If not,
  `_require_platform_auth` replacement would lock SUPER_ADMIN out
  of those endpoints — surface as catalogue gap.

- F-GATE-REPLACE-4: `PlatformAccessRequiredError` class location.
  If `_require_platform_auth` is retired, does the error class get
  retired with it (no other callers) or stay for backward compat?

- F-GATE-REPLACE-5: Are there callers OTHER than the 2 known sites?
  e.g., any test that constructs `_require_platform_auth` for some
  reason, any docstring reference that would become outdated.

### DISCIPLINE — Mandatory-gate-discipline test mechanics

The 6.9.3 test iterates `app.routes` and asserts each route either
has the require() gate in its dependency chain OR is in a
PUBLIC_ROUTES allowlist.

- F-DISCIPLINE-1: How does FastAPI expose a route's dependency
  chain? Cite the relevant FastAPI internals (likely
  `route.dependant.dependencies` per the F-TEST-4 finding in 6.9.2
  investigation). Confirm the path from `app.routes` → dependency
  list → identifiable gate.

- F-DISCIPLINE-2: How does the test identify "this dependency is
  the require() gate"? Two candidate mechanisms:
  - (a) Inspect each dependency's `call` attribute; compare against
    a known sentinel (e.g., a marker attribute set on the inner
    gate function returned by require()).
  - (b) Walk dependency tree by name (closure inspection); fragile.
  Cite what FastAPI's introspection actually exposes.

- F-DISCIPLINE-3: PUBLIC_ROUTES allowlist content. What paths
  exist today that should remain public (no auth, no gate)?
  Survey:
  - `/health`, `/readyz`, `/openapi.json`, `/docs`, `/redoc` if
    present
  - `/me/permissions`, `/me/can-do` (auth required but no gate;
    caller's own state)
  - Anything else?

- F-DISCIPLINE-4: Where does PUBLIC_ROUTES live? A new constants
  module, inline in the test, in main.py alongside the middleware?

- F-DISCIPLINE-5: Does AuthMiddleware already maintain a
  PUBLIC_PATHS frozenset (for auth-skip paths)? If yes, is it the
  same set or a different set? Cite.

### CATALOGUE — Permission catalogue gap analysis

For each retrofit-target permission tuple identified in INVENTORY,
verify it exists in the seeded catalogue.

- F-CATALOGUE-1: Query the seeded `core.permissions` table.
  Produce the full list of `(module, resource, action, scope)`
  tuples currently in v0. Use this as the master catalogue
  reference.

  ```sql
  SET search_path TO core, public;
  SELECT module, resource, action, scope
  FROM permissions
  ORDER BY module, resource, action, scope;
  ```

- F-CATALOGUE-2: Cross-reference with INVENTORY's "likely retrofit
  permission tuple" column. For every endpoint, confirm the tuple
  is in the catalogue. If gaps surface, list them as Open
  questions.

- F-CATALOGUE-3: For every gap, propose the catalogue addition
  (which migration would add the row; which roles would grant it).
  Surface as design-conversation territory; do not propose code.

- F-CATALOGUE-4: SUPER_ADMIN role coverage. After Step 6.8.2.1,
  SUPER_ADMIN should have all ADMIN-domain permissions. Verify
  by querying:

  ```sql
  SELECT p.module, p.resource, p.action, p.scope
  FROM core.role_permissions rp
  JOIN core.permissions p ON p.id = rp.permission_id
  JOIN core.roles r ON r.id = rp.role_id
  WHERE r.code = 'SUPER_ADMIN'
  ORDER BY p.module, p.resource, p.action, p.scope;
  ```

  If a SUPER_ADMIN can't pass the retrofit gates for any endpoint,
  surface — they're the most privileged user; coverage gaps here
  surface design issues.

## Constraints

- Read-only investigation. No edits, no commits.
- Test runs allowed only for F-VERIFY-5 (pytest count) and any
  catalogue/seed SQL queries.
- Cite specific files and line ranges. Do not summarize from memory.
- If something cannot be found or does not exist, say so explicitly
  with confidence: low and explain.
- If any assumption in this prompt is wrong (e.g., a file at a
  cited location doesn't match the prompt's description), call it
  out explicitly. Do not work around incorrect assumptions silently.
- Do NOT propose design decisions for Step 6.9.3. Surface facts
  and observations only; design happens in a separate conversation.
- Stay strictly inside the area codes listed above (VERIFY,
  INVENTORY, ANCHOR, GATE_REPLACE, DISCIPLINE, CATALOGUE). Note
  out-of-scope items in a separate "Open questions for design
  conversation" section at the bottom.

## Output

A single markdown document written to:

  /reports/step-6_9_3-design-investigation-<YYYY-MM-DD>.md

with the F-<area>-<number> findings grouped by area (VERIFY,
INVENTORY, ANCHOR, GATE_REPLACE, DISCIPLINE, CATALOGUE). Plus a
final section "Open questions for design conversation" consolidating
all "Open question" entries surfaced inside findings.

The INVENTORY section is likely to be the longest. Either one
finding per router file with embedded tables, OR a single
F-INVENTORY-MASTER finding with a comprehensive table covering
every endpoint. Pick whichever reads more cleanly.

Do not include scope-creep findings (Stage 3 Auth0, performance
optimisation, audit log writes) inside the area sections; surface
them as separate bullet notes in the Open questions section.
