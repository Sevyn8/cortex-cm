# Investigation: Write-surface coupling across tenants ↔ org_nodes ↔ stores

## Mandate

This is an **investigation, not a step.** No step ID applies. The report lives under `docs/investigations/` deliberately — outside the step-doc filename convention because the investigation informs a future fix-step rather than being a step itself.

This is an **investigation only**. Do NOT change any code, do NOT modify any documents, do NOT regenerate openapi.json, do NOT run migrations. Read the codebase, run grep/view, produce a written report at the path given in the Deliverable section. The report drives the next design discussion in Claude AI chat; no fix gets written until that discussion concludes.

If you find anything that requires touching code to verify (e.g., running a test, running a query against local Postgres), do it read-only and report what you found. Local Postgres is acceptable to read; cloud is out of scope.

**Pre-flight failure stop condition.** If any file named in the Pre-flight section is missing or at an unexpected path, stop before starting C1 and surface to the operator. Do not guess at alternative paths or proceed with partial pre-flight.

## Context

Frontend reported a bug filed during 5g.1.5:

> Add Org Node when the TENANT row is selected as parent fails server-side. The synthetic TENANT row's id is `data.tenant_id` (i.e., the tenants.id UUID), which is not an org_nodes row UUID.

Cloud SQL diagnostic queries (already run by operator, results in Claude AI chat history; trust the findings stated here):

- All 13 tenants in cloud have exactly one tenant-root org_node (`node_type='TENANT'`, `parent_id IS NULL`). No backfill gap.
- `tenants.id` is never equal to its tenant-root `org_nodes.id`. Two independent uuidv7() values. The frontend sending `parent_id = data.tenant_id` cannot match any tenant-root.
- The composite FK `fk_stores_org_node_same_tenant` holds: no cross-tenant linkage exists.
- Across all linked stores in cloud, `stores.org_node_id` always points at a STORE-type `org_nodes` row. Architectural intent matches reality on the linked side.
- BUT: for one inspected tenant (Buc-ee's), 7 `stores` rows have `org_node_id IS NULL` (created via POST /stores in smoke tests), AND 8 STORE-type `org_nodes` rows have no matching `stores` row (created via POST /org-tree in smoke tests). Two disjoint populations. The seed established a paired 1:1 pattern; the write surface broke it.

Operator's hypothesis (to test, not assume true):

> The link between the three tables — tenants, org_nodes, stores — is not done correctly on POST/PATCH or was missed during design, especially in the multi-endpoint business workflow.

There are two visible gaps:

- **Gap A.** Add Org Node from TENANT row fails because frontend sends `parent_id = tenants.id` but backend looks up `org_nodes.id`. ID identity mismatch at a contract boundary.
- **Gap B.** POST /stores creates a `stores` row with `org_node_id = NULL`; POST /org-tree (STORE-type) creates an `org_nodes` row with no `stores` link. The two write paths produce disjoint populations rather than the seed's paired 1:1 pattern.

Hypothesis to evaluate: both gaps share a root cause — the multi-endpoint workflow across tenants / org_nodes / stores was never designed as a unit. Each endpoint is internally correct; the joint contract is what's missing.

## Pre-flight (REQUIRED before any other work)

1. Read `CLAUDE.md` fully.
2. Read `BUILD_PLAN.md` sections for these steps in order:
   - Step 6.13 (Add Org Node endpoint — POST /org-tree)
   - Step 6.17.2 (Stores GET)
   - Step 6.17.3 (Stores POST + PATCH)
   - Step 6.17.4 (Stores set-status)
   - Step 6.20.1 (TenantsRepo.create provisions tenant-root)
3. Read each step's full implementation doc under `docs/implementation-steps/`:
   - `step-6_13-*.md`
   - `step-6_17_2-*.md`
   - `step-6_17_3-*.md`
   - `step-6_17_4-*.md`
   - `step-6_20_1-*.md`
4. Read `docs/architecture.md` and `docs/architecture_RBAC.md` sections on tenants, org_nodes, stores (the table model and the anchor/path mechanics).
5. Read `docs/schema/current_schema.sql` for the three tables and their constraints, especially:
   - `core.tenants` (PK, no FK to org_nodes)
   - `core.org_nodes` (`uq_org_nodes_tenant_id`, `uq_org_nodes_tenant_code_lower`, `ck_org_nodes_root_parent_consistency`)
   - `core.stores` (`uq_stores_org_node_id` partial unique, `fk_stores_org_node_same_tenant` composite FK)

If a step doc is missing or thin, note it in the report under "Documentation gaps" but proceed.

## Investigation questions

Each question gets a section in the report. Answer with file:line citations and short verbatim code excerpts where they sharpen the point. Do not paraphrase if the code itself is short enough to quote. If a question can't be answered from code, say so explicitly.

### C1 — GET /api/v1/tenants/{tenant_id}/org-tree response shape

What does the handler actually return for the tenant-root org_node? The OpenAPI doc says the tenant-root is "excluded from the response" and `tree[]` contains its children. Confirm against the handler code (`routers/v1/org_tree.py` or equivalent) and the response builder. Specifically:

- Is the tenant-root org_node's `id` returned anywhere in the response envelope (e.g., a separate `tenant_root_id` field, or embedded in `stats`, or in metadata)?
- If not, can the frontend obtain it from any current endpoint at all? List which endpoint(s).
- Quote the response-construction code.

### C2 — POST /api/v1/tenants/{tenant_id}/org-tree parent_id resolution

In the handler (`routers/v1/org_tree.py` POST) and its repo call (`repositories/org_nodes.py` or wherever the create lives):

- How is `parent_id` validated and resolved? SQL/ORM query shape.
- What happens when `parent_id` does not match any `org_nodes.id` in the same tenant? Which exception class, which HTTP status, which error code in the envelope?
- Is there any path where `parent_id` is interpreted as `tenants.id` rather than `org_nodes.id`, or any translation between the two? Look for anything that joins `tenants` and `org_nodes` on `id = id` or anything similar.
- Quote the resolution code.

### C3 — POST /api/v1/tenants tenant-root provisioning

Confirm Step 6.20.1's claim that `TenantsRepo.create` provisions the tenant-root org_node atomically with the tenants insert:

- Quote the relevant section of `repositories/tenants.py`.
- Confirm the order of inserts (tenants → org_nodes → modules per the step doc's LD2 refinement).
- Confirm `created_by_user_*` is populated correctly on the org_node row.
- Confirm what happens if the slug helper raises before the tenants insert (Step 6.20.1 says "no partial state" — verify).

### C4 — POST /api/v1/stores org_node_id handling

In the stores POST handler and `StoresRepo.create`:

- What field controls the `org_node_id` value on the new row?
- Is `org_node_id` accepted in the request body? Is it optional? Is it validated against the same tenant?
- When omitted, what gets written — NULL, or is a STORE-type org_node auto-created and linked?
- Quote the create code and the request schema.

### C5 — PATCH /api/v1/stores/{store_id} org_node_id mutability

Same questions as C4 but for PATCH:

- Is `org_node_id` mutable via PATCH? If yes, what validation applies?
- If a store's `org_node_id` is changed, is the prior org_node affected (archived, deleted, left dangling)?
- Quote the relevant code.

### C6 — POST /api/v1/tenants/{tenant_id}/org-tree STORE-type creation

When a frontend creates a STORE-type org_node via POST /org-tree:

- Does the handler check whether a matching `stores` row should also be created? Does it create one?
- If not, what is the documented expected workflow? Find the step doc passage that locks this decision (or note its absence).
- Quote the relevant code.

### C7 — PATCH /api/v1/tenants/{tenant_id}/org-tree/{node_id}

Same coupling question on the org-tree PATCH side:

- Can a STORE-type org_node be reparented? If yes, does anything cascade to the linked store row?
- Can a STORE-type org_node be archived? If yes, what happens to the linked store?
- Quote the relevant code.

### C8 — Seed script paired-write pattern

Find the seed entry points by grep before guessing at filenames:

```
grep -rn "INSERT INTO core.tenants\|INSERT INTO core.org_nodes\|INSERT INTO core.stores" scripts/ db/ migrations/
grep -rln "seed" scripts/
```

Then, for whichever seed script(s) surface:

- How does the seed create a store + matching STORE-type org_node? Is it one transaction with two inserts?
- Does the seed use the production `StoresRepo` and `OrgNodesRepo`, or does it bypass them with raw SQL?
- If the seed uses a paired pattern that the write surface doesn't, document the exact divergence with quotes from both sides.
- Also check the test fixtures `make_tenant`, `make_store`, `make_org_node` in `conftest.py` — same questions.

### C9 — Tenants / org_nodes / stores: other entry points

Beyond POST/PATCH/seed, what other code paths INSERT into any of the three tables?

- Any Alembic migrations that insert data (data migrations)?
- Any CLI script under `scripts/`?
- Any test fixture not already covered in C8?

For each, note whether it follows the paired pattern or not.

### C10 — Existing FN-AB / D-NN / LD-NN records

Grep CLAUDE.md, BUILD_PLAN.md, and every file under `docs/implementation-steps/` for forward-notes (FN-AB), decisions (D-NN), and locked decisions (LD-NN) that reference any of:

- "tenant root", "tenant_root", "tenant-root"
- "org_node_id" + "stores"
- "atomic" + ("store" OR "org_node")
- "decoupled" + ("store" OR "org_node")
- "paired-write" or "paired write"

For each hit, summarise the note in one sentence and cite the file:line. The 2026-05-16 architectural-gap memory note (CLAUDE.md "recent_updates" or similar) should be one of these hits — if you don't find it, search harder.

### C11 — Any conflation of tenants.id and org_node_id elsewhere

Grep the codebase for any spot where a `tenants.id` is used to look up something in `org_nodes`, or vice versa, OR where the two are passed to the same parameter without translation. Suspect patterns:

- `org_nodes.id = tenant_id`
- `parent_id = tenant_id`
- `anchor_id = tenant_id`
- Anywhere `tenants.id` flows into a function expecting an `org_node_id`.

If any exist (other than the known bug), document them. The known bug is on the frontend side; we want to know if the backend has its own latent version.

## Surface and stop

If any of the following surfaces, stop the investigation and report to the operator before proceeding. Do not work around or push through.

1. **Pre-flight file missing.** Any file in the Pre-flight section is missing or at a different path. (Covered in Mandate; restated here for completeness.)

2. **Hypothesis disproved early.** If by the end of C5 you have evidence that the operator's hypothesis is materially wrong (e.g., the write surface DOES handle the joint contract correctly and the gaps observed in cloud have a different cause), stop and surface — completing C6–C11 may be wasted effort against a stale frame.

3. **Schema reality contradicts the stated facts.** If `docs/schema/current_schema.sql` shows a constraint or column that contradicts the Context section's stated cloud-truth findings (e.g., a UNIQUE constraint that should prevent the two-disjoint-populations situation), stop and surface — the cloud findings may be a snapshot of a transient state we don't fully understand.

4. **Investigation depth exceeds session budget.** If by C7 the code reading is taking longer than ~90 minutes of effort, stop, write up what you have so far, and surface — better to ship a partial report with clear "not yet investigated" markers than to over-run and produce an unfocused single deliverable.

## Deliverable

Write the report to:

`docs/investigations/2026-05-20-write-surface-coupling.md`

Structure:

1. **Summary.** 5-10 sentences. State the root cause finding, whether the hypothesis held, and the headline of each gap.

2. **C1–C11 findings.** One section per question above, with file:line citations and code excerpts.

3. **Coupling map.** A short table or bullet list: for every pair of (table_A, table_B) in {tenants, org_nodes, stores}, list every write-surface endpoint that touches both, and whether the coupling is enforced (FK + transactional insert), implicit (relied on by code without enforcement), or missing.

4. **Gap A — Add Org Node from TENANT row.** Exact failure mode in code (which line raises, which error code returns). List every place the fix could go (handler-level translation, response-shape change, frontend-only change). Do not pick yet.

5. **Gap B — Store ↔ STORE-type org_node decoupling.** Every place the link is broken on the write side. List every fix shape (atomic single-endpoint, two-call with explicit linking, matching-by-code convention). Do not pick yet.

6. **Shared root cause.** Was the operator's hypothesis correct? Restate the actual root cause in your own words.

7. **Documentation gaps.** Step docs missing or thin; CLAUDE.md / BUILD_PLAN.md entries that should exist but don't.

8. **Open questions for the design discussion.** Things you'd want the operator to answer before a fix can be designed. One sentence each.

When the report is written, stop. Do not start fixing anything. Surface the report path in your final message.

## Out of scope

- Any fix to the bug.
- Any change to OpenAPI, schemas, repos, routers, tests, docs (other than creating the investigation report file).
- Cloud SQL queries (operator handles those).
- Auth0 / Stage 3 territory.
- Performance, observability, or unrelated tech-debt items surfaced incidentally — note them in section 7 but don't expand.
