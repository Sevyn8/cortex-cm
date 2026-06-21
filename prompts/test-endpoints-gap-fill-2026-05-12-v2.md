# Prompt — test_endpoints_max_view.sh: gap-fill for missing endpoints (v2)

## Goal

Extend `scripts/test_endpoints_max_view.sh` (the current canonical
variant of the integration test harness) to exercise endpoints
currently missing from its matrix. The matrix should cover every
endpoint present in `docs/endpoints/openapi.json` (21 paths as of
2026-05-12 against v0.1.10). Same script, same patterns, same output
style. Additive only.

**Important framing:** this is a coverage-completion pass, not a
redesign. Do not refactor existing cells. Do not change the matrix
structure. Do not introduce new helper functions unless strictly
needed. Add new cells inside the existing per-caller iteration
pattern so each new endpoint is exercised by all 4 callers (P1, P2,
T1, T2) where applicable, just like every other endpoint.

## Pre-conditions for running the harness

Before invoking the test_endpoints_max_view.sh script as part of
verification, confirm:

1. Local Postgres is running and at alembic head `3e05299cb533` or
   newer. Verify with `uv run alembic current`. Bail if older.
2. Seed data is current: `uv run python -m scripts.seed_dev_data
   --reset` has been run against the current XLSX in a fresh local
   session. (Required because Step 6.8.2.1 added permissions and
   Step 6.8.1 split URA tables; stale seed will mislead.)
3. uvicorn is running on `:8000`: `uv run uvicorn
   admin_backend.main:app --host 0.0.0.0 --port 8000`.

The harness has its own Phase 0 server-health and email-existence
checks; the seed/alembic checks above are NOT in the harness. Run
them by hand before invocation.

## Pre-flight reading

Read these before proposing the edits:

- `scripts/test_endpoints_max_view.sh` — the script being extended
- `scripts/test_endpoints.sh` — the older sibling (skim only; the
  max-view variant is the one being modified)
- `docs/endpoints/openapi.json` — the source of truth for the
  endpoint inventory
- `src/admin_backend/routers/v1/` — the router source files for the
  endpoints being newly tested. **You will need to consult these to
  verify expected error-status codes** (see "Status code verification"
  below)
- `prompts/step-5_3-org-tree-2026-05-04-v3.md` — context on org-tree shape
- `prompts/step-6_5-dashboard-stats-endpoints-2026-05-05.md` — context on dashboard cards
- `prompts/step-6_8_3-roles-augmentation-and-endpoint-2026-05-09-v2.md`
  — context on /role-assignments

After reading, restate which endpoints are missing and which section
of the script each one logically belongs in BEFORE adding cells.

## Gap re-derivation (do this first)

The list of "5 missing endpoints" further down was current as of
2026-05-12. Code drifts. Before trusting it:

1. List all paths in `docs/endpoints/openapi.json` (programmatically:
   `jq -r '.paths | keys[]' < docs/endpoints/openapi.json`)
2. Grep `scripts/test_endpoints_max_view.sh` for `${API}/` patterns
   and extract the set of unique endpoint paths the script invokes.
   Strip query strings and path-param substitutions to compare
   against (1)
3. Compute the set difference: openapi paths NOT exercised by the
   script
4. Compare your derived set against the 5 listed below

If the derived set MATCHES the 5 listed, proceed using the cell
specifications below.

If the derived set DIFFERS (different count, different endpoints,
renamed paths, etc.):
- Use the derived set as authority
- Surface to the operator: what's in the derived set that's not in
  the prompt, what's in the prompt that's not in the derived set
- For endpoints in the derived set but not specified below, propose
  cell expectations and ask for confirmation before adding

## Status code verification (load-bearing)

For every NEW cell where the expected status is 4xx (especially 400
and 422), verify the actual response by reading the relevant router
in `src/admin_backend/routers/v1/`. Specifically:

- **Sort-key errors**: code raises `InvalidSortKeyError` → router
  re-raises as `InvalidSortKeyClientError` → status 400 with code
  `INVALID_SORT_KEY`. **Verify this for each new sort-error cell.**
  If the codebase deviates (e.g., FastAPI's default 422 fires before
  the handler dispatches), USE THE VERIFIED STATUS, not the one
  guessed in this prompt
- **Depth-validation errors**: FastAPI's Pydantic validation on a
  query param's `maximum` constraint produces 422. The org-tree
  `depth=99` cell below assumes 422. Verify against the depth param
  spec in `src/admin_backend/routers/v1/tenants.py` (or wherever the
  org-tree router lives)
- **Cross-tenant 404 vs 403 vs 401**: per D-17, RLS-as-404 is the
  canonical pattern. If a probe of someone else's tenant_id returns
  anything other than 404 under TENANT JWT, surface — that's a
  contract drift, not a test fix

**Critical:** If a NEW cell fails on first run because the expected
status doesn't match reality, **do NOT adjust the expected status to
match the response.** Surface the mismatch and ask. Self-confirming
matrices are worse than missing coverage.

## The 5 missing endpoints (verify via gap re-derivation first)

1. `GET /api/v1/tenants/{tenant_id}/org-tree`
2. `GET /api/v1/tenants/{tenant_id}/org-nodes/{node_id}/children`
3. `GET /api/v1/dashboard/fleet-stats`
4. `GET /api/v1/dashboard/governance-stats`
5. `GET /api/v1/role-assignments`

## Cells per endpoint — MUST vs SHOULD

Each cell below is marked MUST or SHOULD.

- **MUST cells** are load-bearing. The happy path, the cross-tenant
  probe (for TENANT callers), and the no-auth 401 are MUSTs. Drop
  none. If a MUST cell can't be implemented for some reason, stop
  and surface — do not silently skip
- **SHOULD cells** are parametric variants (sort, status, limit,
  offset). They harden the surface but their absence doesn't break
  coverage. If a SHOULD cell can't be implemented (fixture missing,
  status code genuinely ambiguous after verification), drop it and
  note in the report

### Org-tree

In Phase 3 (fixture discovery), add a single GET to
`${API}/tenants/${T1_TENANT_ID}/org-tree` using P1_JWT. Extract
`OWN_HQ_NODE_ID` as `.tree[0].id` from the response. (Don't filter by
node_type — just pick the first top-level node returned. The script
needs a real node_id for the children endpoint; the type doesn't
matter for status-code coverage.) If `.tree` is empty, die with
`"fixture discovery: org-tree returned empty .tree for T1 tenant
${T1_TENANT_ID} — seed first"`.

Per caller (P1, P2, T1, T2):

| Cell | Expected | Tier |
|---|---|---|
| `${API}/tenants/${OWN_TENANT}/org-tree` | 200 | MUST |
| `${API}/tenants/${OTHER_TENANT}/org-tree` | 200 for P1/P2, 404 for T1/T2 | MUST |
| `${API}/tenants/${UNKNOWN_UUID}/org-tree` | 404 | MUST |
| `${API}/tenants/${OWN_TENANT}/org-tree?depth=2` | 200 | SHOULD |
| `${API}/tenants/${OWN_TENANT}/org-tree?depth=99` | 422 (verify via router) | SHOULD |
| `${API}/tenants/${OWN_TENANT}/org-tree` no-auth | 401 | MUST (one cell only, public) |

### Children (lazy-load endpoint)

Per caller:

| Cell | Expected | Tier |
|---|---|---|
| `${API}/tenants/${OWN_TENANT}/org-nodes/${OWN_HQ_NODE_ID}/children` | 200 | MUST |
| Same with `?limit=2&offset=0` | 200 | SHOULD |
| `${API}/tenants/${OTHER_TENANT}/org-nodes/${OWN_HQ_NODE_ID}/children` | 200 for P1/P2, 404 for T1/T2 | MUST |
| No-auth | 401 | MUST (one cell only, public) |

Note on the cross-tenant probe with `OWN_HQ_NODE_ID`: when T1 calls
T2's tenant_id with T1's own node_id, the node won't exist under T2's
tenant. Expect 404 — RLS-as-404 cumulative with "this node doesn't
exist here either."

### Dashboard fleet-stats

Per caller:

| Cell | Expected | Tier |
|---|---|---|
| `${API}/dashboard/fleet-stats` | 200 | MUST |
| No-auth | 401 | MUST (one cell only, public) |

Status-code-only assertion per harness convention. RLS persona
projection (PLATFORM sees fleet totals; TENANT sees own-tenant
projection) is real but not verified at this layer.

### Dashboard governance-stats

Per caller:

| Cell | Expected | Tier |
|---|---|---|
| `${API}/dashboard/governance-stats` | 200 | MUST |
| No-auth | 401 | MUST (one cell only, public) |

### Role-assignments

Per caller:

| Cell | Expected | Tier |
|---|---|---|
| `${API}/role-assignments` | 200 | MUST |
| `${API}/role-assignments?status=ACTIVE` | 200 | SHOULD |
| `${API}/role-assignments?tenant_id=${OWN_TENANT}` | 200 | SHOULD |
| `${API}/role-assignments?sort=granted_at_asc` | 200 | SHOULD |
| `${API}/role-assignments?sort=nope` | 400 INVALID_SORT_KEY (verify via router) | SHOULD |
| No-auth | 401 | MUST (one cell only, public) |

Additionally for TENANT callers only (T1 and T2):

| Cell | Expected | Tier |
|---|---|---|
| `${API}/role-assignments?platform_user_id=${ANY_PLATFORM_USER_ID}` | 200 | SHOULD |

The TENANT-with-platform_user_id cell exercises the short-circuit at
the router (commit 6.8.3 — security-load-bearing). The response's
`platform_assignments.items` will be empty, but the status is 200
either way. The body-shape enforcement isn't part of this harness's
status-code-only convention.

## Placement in the script

The script's Phase 4 emits cells in a per-caller iteration. New cells
go inside the same iteration: each of the 5 endpoints is exercised by
P1, P2, T1, T2 in turn. Do NOT create a "Phase 5 — post-6.5
endpoints" block at the bottom of the script. Do NOT batch all
org-tree cells together outside the per-caller flow.

If the script structure makes natural insertion awkward (e.g., it has
strict per-resource sub-sections and there's no obvious place for
`dashboard/*` cells), surface and ask — do not silently invent a new
section.

The no-auth cells (1 per endpoint, public path) go in whatever
section the script already uses for no-auth probes. If there's an
existing "Public + no-auth" section at the start of Phase 4 (per the
existing convention), add the 5 new no-auth cells there.

## Scope out

- **`CLAUDE.md` and `BUILD_PLAN.md` updates.** This step deviates from
  the standing commit-bundling convention. The operator has structural
  revisions to those files in flight. Do NOT propose edits to either
  file. Do NOT add either file to the commit's `git add` list. The
  operator handles them separately
- Deep response-body assertions. Status-codes-only is the harness
  convention; do not bolt on body shape validation
- New helper functions unless strictly needed. Reuse `req`
- Reordering or refactoring existing cells
- Updating `scripts/smoke_curl.sh` — separate concern (its purpose is
  to stay small)
- Cloud variant — separate prompt that follows this one (after this
  lands)
- Updating documentation in `docs/build-step-workflow.md` unless the
  doc explicitly enumerates cell counts (verify first; if it does,
  one-line bump only)

## Stop and ask if

1. The gap re-derivation (above) produces a different set from the
   5 endpoints listed. Use the derived set; tell the operator what
   changed
2. Any expected status code in a NEW cell fails on first run.
   **Do not adjust the expected status.** Surface and ask
3. Router source verification of an expected 4xx status reveals a
   different status (400 vs 422 vs 403). Use the verified status and
   note in the report
4. The org-tree fixture discovery returns empty `.tree` for T1's
   tenant — surface "seed first" message and stop
5. The matrix structure makes natural placement of the new cells
   awkward and there's no obvious section to put them in
6. A MUST cell can't be implemented (fixture genuinely missing,
   path can't be constructed). Surface — never silently skip a MUST
7. ANY of the no-auth cells produces 422 instead of 401 (would
   indicate FastAPI's validation firing before the auth dependency,
   which means the route signature has a structural issue — not a
   test problem)
8. The `req` helper's existing signature can't accommodate a cell
   pattern without modification

## Acceptance criteria

1. The matrix exercises every endpoint from
   `docs/endpoints/openapi.json` (one or more cells per endpoint;
   every MUST cell present)
2. Local `./scripts/test_endpoints_max_view.sh` run returns exit 0
   against local Postgres + uvicorn at the pre-conditions stated above
3. New cells follow the same `req` pattern as existing cells; no new
   helpers, no structural changes
4. No reordering of existing cells; only additions
5. Phase 3 extended to discover `OWN_HQ_NODE_ID` if and only if the
   children endpoint cells are being added
6. `docs/endpoints/openapi.json` is unmodified by the run (Phase 1
   re-fetches and pretty-prints; content should be byte-stable since
   the server is on v0.1.10 already)
7. Every expected 4xx status in a new cell was verified against
   router source — list which cells, which file, which line range
   in the report

## Report before commit

1. List of cells added per endpoint (label + path + expected status
   + caller scope: P1/P2/T1/T2 + MUST/SHOULD tier)
2. Status-code verification log: for each NEW 4xx cell, which file
   and what was found
3. Gap re-derivation result: did the derived set match the 5 listed?
   If not, what changed?
4. Phase 3 discovery additions: what new fixture IDs were resolved
   (specifically `OWN_HQ_NODE_ID` — value or note that it was
   skipped because the cells weren't added)
5. Total counter delta: before vs after on a successful local run
   (e.g., 247 → 290 calls)
6. `git diff --stat` showing only
   `scripts/test_endpoints_max_view.sh` modified, zero unintended
   changes elsewhere
7. Any SHOULD cells dropped, with reasons

Wait for explicit operator authorisation before committing.

## After committing

Propose the commit per CLAUDE.md "After completing a task" pattern:

```
scripts: gap-fill test_endpoints_max_view matrix for post-6.5 endpoints

Adds matrix coverage for endpoints that landed in steps 5.3, 6.5,
and 6.8.3:

- /tenants/{tenant_id}/org-tree
- /tenants/{tenant_id}/org-nodes/{node_id}/children
- /dashboard/fleet-stats
- /dashboard/governance-stats
- /role-assignments

Matrix structure unchanged; cells additive only. Phase 3 discovery
extended to resolve OWN_HQ_NODE_ID for the children endpoint. Local
run: <BEFORE> -> <AFTER> calls, all expected statuses matched.

Status code verification: <list cells where 4xx expectations were
checked against router source — e.g., role-assignments sort error
verified as 400 in src/admin_backend/routers/v1/role_assignments.py>.

Closes a coverage gap discovered when validating the cloud deploy of
v0.1.10 (split URA migration + /role-assignments endpoint).
```

## Next

After this lands, the cloud-targeted variant follows in a separate
prompt: `test_endpoints_cloud.sh`. The two are sequential, not
parallel — the cloud variant clones this matrix verbatim and depends
on it being current. Do NOT bundle the cloud variant into this step.
