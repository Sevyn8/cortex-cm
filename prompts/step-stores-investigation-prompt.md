INVESTIGATION PROMPT — Stores resource code-state audit (read-only)

You are doing a read-only investigation pass over the admin-backend
codebase at HEAD to produce a code-grounded findings document for
the Stores resource. This document feeds an open design conversation
for `/stores` endpoints (read + write surfaces). The design is open;
the docs (BUILD_PLAN.md Step 4.5, architecture_RBAC.md worked
examples, CLAUDE.md FN-AB references) are NOT authoritative for the
shape of the upcoming design. They may be useful background, but
the design conversation will decide URL shape, gate tuples, write
verbs, sort vocabulary, and error taxonomy from first principles
against what the code actually supports today.

Your job is to report what is in the codebase, not what should be
built. Do not propose designs. Do not say "this is the precedent
to follow." Do not predict what the design will need.

Do not edit any file. Do not make any code change. Do not run
migrations or seed scripts. Test runs allowed only for explicit
verification tasks listed below. Output is a single structured
findings document written to:

  reports/step-stores-design-investigation-<YYYY-MM-DD>.md

where <YYYY-MM-DD> is today's date in ISO format.

## Context

The user has observed: `core.stores` exists in the database (DDL
landed at the initial wrap), but no full Stores stack (model,
schema, repo, router, tests) appears to have shipped. The codebase
has since gone through Steps 5.x and 6.1-6.16 covering other
resources. It is possible that pieces of the Stores stack landed
incidentally during later steps without the full deliverable
closing.

The findings document must establish, file-by-file, what exists
TODAY in the codebase for stores, and what does not. This is a
codebase audit; docs are NOT the source of truth. Where docs claim
something exists, verify in code. Where docs and code disagree,
surface the disagreement and treat the code as the fact.

## Investigation tasks

Read the relevant source files. Produce a structured findings
document. Each finding follows this exact format:

  ### F-<area>-<number>: <one-line summary>

  **Citation:** `<file>:<line range>` (or "absent" with the path
  checked)

  **Current code:** (excerpt if present; "file not found" if
  absent)

  **Observation:** (factual implication; what this means for the
  current code state, NOT what the design should do)

  **Confidence:** (high / medium / low)

Do NOT include design recommendations, "precedent to follow"
phrasing, or "the design will need X" predictions inside findings.
The observations are factual statements about the codebase only.

Area codes for this investigation:
- ARTIFACT (presence/absence of code artifacts for stores)
- DDL_SHAPE (columns, enums, constraints in stores_v5.sql, treated
  as facts about the table, not as design constraints)
- COUPLING (where existing code already references the stores
  table or Store identifiers)
- CATALOGUE (permission catalogue rows for STORES in the seed)
- SHAPES (catalogue of patterns the codebase actually uses today
  for analogous read and write surfaces, reported neutrally for
  design reference, with no "follow this one" framing)
- SEED (rows in the seed Excel for stores)
- TESTS (existing test scaffolding touching stores)
- WIRING (router registration, OpenAPI surface)

## Specific questions to investigate

### ARTIFACT — Presence/absence of code artifacts for stores

- F-ARTIFACT-1: Does `src/admin_backend/models/store.py` exist?
  If yes, list every column mapped, every server_default /
  FetchedValue usage, every PG enum binding. If no, state
  "absent" and cite the empty path.

- F-ARTIFACT-2: Does `src/admin_backend/schemas/store.py` exist?
  If yes, enumerate every class defined and its fields. If no,
  state absent.

- F-ARTIFACT-3: Does `src/admin_backend/repositories/stores.py`
  exist? If yes, enumerate every method on every class with
  signature. If no, state absent and list the contents of
  `repositories/` for reference.

- F-ARTIFACT-4: Does `src/admin_backend/routers/v1/stores.py`
  exist? If yes, enumerate every route by HTTP verb and path. If
  no, state absent and list the contents of `routers/v1/` for
  reference.

- F-ARTIFACT-5: Is any stores router included in `main.py`?
  Search for any `stores` reference. Cite the lines or state
  absent.

- F-ARTIFACT-6: Enumerate every anchor dep currently defined in
  `src/admin_backend/auth/anchor_deps.py`. List by function name
  and one-line signature. State which resources have coverage
  and which do not.

- F-ARTIFACT-7: Search `src/admin_backend/errors.py` for any
  class name containing "Store". List what is found or state
  absent.

- F-ARTIFACT-8: Search `src/admin_backend/` for any Python enum
  named `StoreStatus`, `TaxTreatment`, or similar. Cite location
  if present. State absent if not.

### DDL_SHAPE — Columns, enums, constraints

- F-DDL_SHAPE-1: Read `db/raw_ddl/stores_v5.sql` (confirm path
  by listing `db/raw_ddl/`). List every column with type and
  nullability.

- F-DDL_SHAPE-2: Enumerate every CHECK constraint on
  `core.stores`. State the constraint name and the SQL
  expression.

- F-DDL_SHAPE-3: Read the enum definitions for
  `store_status_enum` and `tax_treatment_enum` from wherever
  they live (likely `shared_utilities_v1.sql` or a sibling DDL).
  List the values verbatim.

- F-DDL_SHAPE-4: Confirm the RLS posture on `core.stores`. Cite
  the `FORCE ROW LEVEL SECURITY` statement and the policy
  definition (the GUC, the predicate).

- F-DDL_SHAPE-5: List every UNIQUE constraint, every index, and
  every FK on `core.stores`. State the name and the column set
  for each.

### COUPLING — Existing references to stores in code

- F-COUPLING-1: Search the entire `src/admin_backend/` tree for
  the bare string `stores` (case-insensitive). Filter out
  comments and docstrings. Enumerate every hit by file:line
  with a one-line caption.

- F-COUPLING-2: In every Repo file (`repositories/*.py`),
  identify SQL referencing `stores`. Cite the exact SQL strings
  and whether they are schema-qualified (`core.stores`) or
  unqualified (`stores`).

- F-COUPLING-3: In every schema file (`schemas/*.py`), identify
  any field name, class name, or string literal referencing
  stores. Cite file:line.

- F-COUPLING-4: Search for any import statement importing a
  `Store` identifier (`from .models.* import .*Store`,
  `from admin_backend.models import .*Store`). List every
  import site.

### CATALOGUE — Permission rows for STORES in seed

- F-CATALOGUE-1: Open the seed Excel `permissions` sheet
  (read-only). Enumerate every row with `resource='STORES'`.
  List the full tuple (module, resource, action, scope) and the
  `_key`.

- F-CATALOGUE-2: For each STORES permission row, list every
  role that holds it via the `role_permissions` sheet.

- F-CATALOGUE-3: List every member of the Python
  `PermissionResource` enum (cite the file). State whether
  `STORES` is in the enum.

### SHAPES — Patterns the codebase actually uses today

This section is a neutral survey of analogous shapes the
codebase already implements. Report each one as a factual
catalogue entry. Do NOT label any as "the precedent." The
design conversation will decide what to reuse, what to adapt,
and what to diverge from.

- F-SHAPES-1: For every router in `routers/v1/` that has a list
  + detail GET pair, list:
    - the URL prefix (from `main.py` or the router file)
    - the list handler's file:lines
    - the detail handler's file:lines
    - the gate tuple if any (presence/absence of `Depends(require(...))`)
    - whether auth tier is checked via `_require_platform_auth`
      or via the gate's audience parameter or neither
    - the response envelope shape (bare list vs `{items, pagination}`
      vs other)
  One row per router. No commentary.

- F-SHAPES-2: For every router in `routers/v1/` that has POST,
  PATCH, or state-transition endpoints (suspend/activate/close
  /enable/disable/etc.), list:
    - URL pattern (flat `/resource` vs nested
      `/parent/{id}/resource` vs other)
    - HTTP verb and path
    - gate tuple
    - anchor dep used (if any)
    - audience parameter on the gate (if any)
    - error classes raised in the handler
    - response code on success
  One row per endpoint. No commentary.

- F-SHAPES-3: List every site in `src/admin_backend/` where sort
  key validation happens. Cite each. List the validation style
  (frozenset lookup, enum membership, repo-side raise, etc.)
  and the error class raised.

- F-SHAPES-4: List every site in `src/admin_backend/` where a
  UNIQUE-constraint pre-check is implemented in app code
  (SELECT-then-INSERT/UPDATE pattern). Cite each. List the
  error class raised on collision and the HTTP status it maps
  to.

- F-SHAPES-5: List every site in `src/admin_backend/` where a
  state-transition matrix is enforced in repo code (allowed
  from-state to to-state pairs). Cite each. List the
  TransitionResult enum or equivalent type, and the error class
  raised on invalid transitions.

- F-SHAPES-6: List every anchor dep in `auth/anchor_deps.py`
  with its lookup chain (input → SQL → output path string). Note
  the error class each raises on miss.

### SEED — Stores rows in seed Excel

- F-SEED-1: Open the seed Excel `stores` sheet. Report total
  row count, then count per `tenant_id`.

- F-SEED-2: Enumerate distinct values present in the seed's
  `status` column and the seed's `country` column.

- F-SEED-3: Count rows where `closed_at` is populated. List
  which tenants own them.

- F-SEED-4: Cross-reference: the dashboard `fleet-stats`
  endpoint reports a `total_stores` count. Confirm the seed
  count matches.

### TESTS — Existing test scaffolding touching stores

- F-TESTS-1: Search `tests/` for any file with `store` in the
  filename (case-insensitive). Enumerate. State each file's
  test function count.

- F-TESTS-2: Search `tests/` for the bare string `stores`
  across all test files. List file:line of every hit not in
  F-TESTS-1's files.

- F-TESTS-3: List every `make_*` fixture in
  `tests/integration/conftest.py`. State whether a `make_store`
  fixture exists.

- F-TESTS-4: In `scripts/smoke_test.py`, list every assertion
  touching stores by line.

- F-TESTS-5: In `scripts/smoke_curl.sh`,
  `scripts/test_endpoints.sh`, and
  `scripts/test_endpoints_cloud.sh`, list any line referencing
  stores.

### WIRING — Router registration and OpenAPI surface

- F-WIRING-1: Confirm whether `docs/endpoints/openapi.json` has
  any `/stores` paths. If yes, list them. (Read the committed
  file; do not regenerate.)

- F-WIRING-2: Confirm whether `docs/endpoints/stores.md` exists
  and summarise its state (placeholder text vs populated content
  vs absent).

## Reporting format

Write the findings document to
`reports/step-stores-design-investigation-<YYYY-MM-DD>.md`.

Top-of-document summary, in this order:

1. **Headline** (one paragraph): definitive statement of what
   exists in the codebase for Stores today vs what is absent.
   E.g., "Model absent. Schema absent. Repo absent. Router
   absent. Anchor dep absent. Errors absent. N permission
   tuples for STORES in catalogue. M stores in seed across K
   tenants." Or whatever the truth is.

2. **Code-state classification** of every artifact checked in
   ARTIFACT: PRESENT (with file:line) or ABSENT (with path
   checked).

3. **The 8 area sections** in order: ARTIFACT, DDL_SHAPE,
   COUPLING, CATALOGUE, SHAPES, SEED, TESTS, WIRING.

No "open questions for design," no "recommendations," no
"precedents to follow." The findings document is reconnaissance.

## Surface-and-stop conditions

Report the finding, stop the investigation, await operator
direction:

- The codebase contradicts docs on a load-bearing claim (e.g.,
  docs say `get_store_anchor` exists, code shows it absent; or
  vice versa). Report the contradiction with citations from
  both sides and stop.

- A code artifact for stores exists but is dead-wired (router
  with routes that are never included in main.py, repo never
  imported, schema never referenced). Report and stop.

- The seed Excel has stores rows that would violate a CHECK
  constraint visible in stores_v5.sql. Report and stop.

Otherwise: complete the full investigation and write the report.

## Caveats

- Do not edit any source file. Do not regenerate any committed
  artifact.
- Do not run the seed loader. Do not run migrations.
- Do not run the full test suite. pytest runs are allowed only
  for verifying a single identified test file is green or red
  at HEAD if a finding requires it.
- Do not propose designs. Each finding is a factual code
  observation. The design conversation that follows this report
  is open and not bound by prior step decisions; do not
  pre-empt it.
- Match the operator's existing style: factual, citation-first,
  no rhetorical filler.
