# CLAUDE.md — Ithina Admin Backend

> Standing context for Claude Code. Read this fully at the start of every session before doing any work on this project.

> **Infra repo cross-reference.** The `ithina-retail-admin-infra` repo manages this project's GCP infra (Terraform). When making infra changes, edit Terraform there first, apply, then have other config follow. Read `terraform/README.md` in that repo before making infra changes from here.

---

## How to engage with this document

This document captures background, decisions with reasoning, conventions, current state, and working rules. **Treat decisions as current best answers with conditions, not as rules to follow.**

Every decision below has:
- **What** was decided.
- **Why** (the reasoning).
- **Reconsider if** (the conditions that would invalidate it).

If during your work you see a condition that hits "Reconsider if", or you find that applying a decision requires workarounds, special cases, or contradictions with other decisions — **stop and flag it before proceeding**. Do not silently rationalise. Successful outcome is the goal, not compliance with this document.

The same rule applies to the prompts you receive for each task. Prompts are written in advance and may have errors, missing context, or unanticipated cases. **Review the prompt itself before executing.** If something is wrong, ambiguous, or doesn't fit — push back. Don't execute a flawed prompt and find out three hours later that the assumption was wrong.

The same rule applies to the repository structure, code conventions, error model, fixture list, and environment variables described later in this document. They are starting points based on common patterns, not contracts. If during the build you find a better fit, surface it.

**The default disposition is critical engagement, not compliance.**

Periodically (every several tasks, or when something feels off), pause and ask yourself: "Has anything we've built started to make an existing decision feel forced? Anything that should be reconsidered?" Surface this proactively. Do not wait for the user to ask.

---

## Background

### What is being built

A Python FastAPI backend service for the Ithina platform's Admin Console.

**This is v0 — the initial release / MVP for beta users.** v0 ships across six stages: read-only foundation (Stage 1), RBAC enforcement and write surface (Stage 2), Auth0 integration (Stage 3), late scope from business (Stage 4), staging/UAT (Stage 5), production cutover (Stage 6). See BUILD_PLAN.md for the complete stage map.

Stage 1 (read-only REST `GET` endpoints for tenants, stores, users, organisation hierarchy, RBAC, and audit logs) is substantially shipped; Stores (Step 4.5) and audit_logs (Step 6.2) remain TODO within Stage 1. Stage 2 adds write endpoints (POST/PATCH/DELETE) and per-permission RBAC enforcement. The database is currently populated externally (manual SQL by Ithina staff); Stage 2's Step 6.16 promotes audit log population to app-side.

### Why it matters

- Real product, going to a select set of paying B2B customers in Phase 1.
- Cross-tenant data leak = "disaster". Treated as primary design constraint.

### Where it fits

Ithina = Retail Intelligence Platform. The platform has multiple modules; the Admin module's backend is what we're building. Other services (DIS, Pricing OS, etc.) are owned by other teams.

The platform-wide master database holds tenants, stores, users, RBAC, audit logs, and other shared entities. **Admin backend is the sole writer to its own tables** (tenants, stores, users, RBAC). The master DB may also contain tables owned and written by other services. Admin backend never writes to those tables; it may read from them if needed.

Other services in the platform are typically read-only consumers of admin backend's tables.

---

## Mental model

The admin backend is a REST API service that gives the frontend safe, authenticated, multi-tenant-isolated access to the platform's master database. Stage 1 exposed read endpoints; Stage 2 adds writes and per-permission enforcement.

Most endpoints are CRUD-shaped (one table, one operation). Some endpoints involve derived shapes (org tree descendants via ltree, permission resolution across joined tables) — these are still READ but require non-trivial query logic. Write endpoints (Stage 2) involve multi-table operations, validation rules, and side effects (audit, notifications, cache invalidation).

**Standard REST API concerns apply.** v0 includes: authentication (JWT-verified), input validation (Pydantic), structured error responses, observability (logs + metrics), URI versioning, OpenAPI specification.

**Auth and multi-tenancy are not "standard concerns" but the load-bearing mechanics of this service.** Every request is filtered by tenant context, every connection is RLS-bound. Get auth right and data isolation is guaranteed; get it wrong and there is a leak.

**What v0 defers:** items deferred from v0 are listed in BUILD_PLAN.md's "Candidate scope (eligible for v0 promotion)" section. That list is the canonical source.

---

## How Claude Code works in this project

### Before starting any task

0. **Pre-flight: run `./scripts/check_setup.sh`.** If any check fails, stop. Report the failure to the user. Do not attempt to fix setup issues unless explicitly told. (Early in Stage 1 the script may not have existed yet; that's no longer the case post-Step 1.2.)
1. Read this CLAUDE.md fully.
2. Read `docs/architecture.md` fully (system narrative, request lifecycle, deployment shape) and `docs/architecture_RBAC.md` fully (RBAC subsystem reference: system model, gate factory, anchor dependencies, endpoint cookbook).
3. Read `docs/api-contract.md` if it has been updated past the template state (per D-28). When still in template, the operating contract lives in CLAUDE.md's D-28, D-30, D-31 and conventions.
4. Read BUILD_PLAN.md fully.
5. Read the specific prompt for the task you're assigned.
6. Review the prompt itself: is it clear, complete, internally consistent? Does it conflict with anything in the docs above? Does it assume something that doesn't hold?
7. If anything is unclear, ambiguous, or wrong: ask the user. Wait for confirmation. Do not guess.
8. Before writing code, restate your understanding of the scope and the acceptance criteria in your own words. If the user agrees, proceed.

### Executing a task

- Implement the task as described, having first vetted the prompt for issues.
- Write tests as you go. Tests are not optional for any task that produces code.
- Run the tests. If they fail, investigate and fix. Iterate until green.
- Escalate to the user only when the failure reveals a design issue, ambiguity, or unanticipated condition. Do not escalate routine bugs you can fix.
- In permission-required mode: pause for confirmation at meaningful decision points (architecture-affecting changes, scope expansion, "I'm about to do X — confirm?"). Do not pause for trivial actions.

### After completing a task

1. Run the acceptance criteria from the prompt. All must pass.
2. Report briefly:
   - What changed (files modified, lines roughly).
   - Test counts (pass / fail / skipped).
   - Any design decisions made on your own (and why).
   - What was NOT implemented (confirming scope boundaries were respected).
   - Anything you noticed that doesn't fit existing decisions or conventions.
3. Update CLAUDE.md if any decision, state, or convention changed.
4. Update BUILD_PLAN.md (status field for the step) and any sequence/scope changes.
5. **Propose a git commit.** Show the user:
   ```
   git status
   git add -A
   git commit -m "Step <id>: <one-line description>"
   ```
   Use commit message convention: `Step <id>: <one-line description>` with optional bullet points underneath for multi-aspect steps. Example:
   ```
   Step 1.5: smoke test script with cross-tenant RLS verification
   
   - 11 assertions covering tenant isolation, FK integrity, CHECK constraints
   - Self-contained: creates and rolls back own test data
   - Handles FORCE RLS by setting app.tenant_id before SELECTs
   ```
   Ask user "Run? yes / no / edit message". On yes, execute the commands via bash tool. On no, skip. On edit, prompt for new message.
6. Stop. Wait for user direction. Do not auto-chain to the next task.

### Workflow convention — Per-step commit bundling

Every step's main commit bundles four things in a single commit. No more parking lot of CLAUDE.md/BUILD_PLAN.md/prompt updates that get applied later.

1. **Code and configs the step produced.** Source files, tests, migrations, dep changes, anything the step's deliverable list requires.
2. **CLAUDE.md updates flowing from the step.** New decisions made (D-XX entries), FN-AB items resolved or added, "Current state" updates, references that need correcting because reality has moved.
3. **BUILD_PLAN.md updates.** Status flip TODO → DONE for the completed step. Scope-in or acceptance-criteria corrections if the step deviated from the original plan (this is normal; document it rather than hiding the deviation).
4. **The prompt file that drove the step.** Committed once, alongside the work. Filename pattern `prompts/step-X_Y-name.md`.
5. `architecture.md` updates, IF the step changed the system shape. If the step changed RBAC-specific behavior, `architecture_RBAC.md` also updates.
   Most steps don't. Steps that do: schema additions/removals
   (count of tables, new ltree usage, new audit storage, etc.),
   deployment-topology changes, new external dependencies, contract
   changes between layers. If the step only implemented a corner of
   an already-specified shape, this item is "no change" don't hunt
   for an edit.

**DDL files are frozen at the as-shipped initial-schema state.** The 8 DDLs in `db/raw_ddl/` are source-of-truth for the *initial* schema only. All subsequent schema and policy changes are encoded in Alembic migrations; DDL files are not edited per-migration. Precedented twice in the v0 chain: `e59f62d5037d` (NULLIF wrapper) and `4fd3aec6ae0c` (FN-AB-14 OR-clause) both shipped without touching their respective DDL files; Step 3.0's `21e2ad16303a` follows the same convention. The regenerator script `scripts/build_initial_migration.py` is intended for post-v0 chain consolidation only; running it against the current DDLs would silently omit the three live policy migrations (and any future ones). Tracked as FN-AB-15.

The prompt file is one-shot. Once the step lands, the prompt is treated as historical and is not edited. If the step needs to be re-run with a different approach, the new prompt is a new file (`prompts/step-X_Y-name-v2.md`); the original stays in git unchanged. Change-notes inside prompt files are forbidden — the git commit message is the change-log; duplicating that inside the file creates drift.

The "report before commit" gate enforces this. At report time, Claude Code enumerates all four bundles explicitly:

- Files changed (with line counts)
- CLAUDE.md updates this step requires
- BUILD_PLAN.md updates this step requires
- Prompt file confirmed in commit set
- Test results, mypy, check_setup status

The user authorises the full bundle once; the commit lands once. No follow-up "I forgot to add X" rounds.

Co-author trailer: per project convention, no `Co-Authored-By` lines on AI-touched commits in this repo. Consistency forward; older commits are as-they-are.

### When you find an issue

- Stop immediately.
- State the issue clearly: what conflicts with what, what's ambiguous, what doesn't fit.
- Show the relevant snippets (decision, plan section, code).
- Propose options if you can. Mark which one you lean toward and why.
- Wait for user confirmation before proceeding.

### What you must never do

- Silently work around a decision because it "doesn't quite fit".
- Implement something a prompt explicitly excludes.
- Skip writing or running tests because they're "obvious".
- Push to main / merge / deploy without user confirmation.
- Touch infrastructure (GCP, Cloud SQL, Kubernetes) without explicit user direction.
- Regenerate or rewrite the DDL files. They are the source of truth for schema.

---

## Standing working rules

These apply unconditionally to every interaction.

- **No em-dashes** anywhere in output documents (markdown, docx, code comments). Use commas, parentheses, colons, or sentence breaks. Convention applies to handwritten content. Auto-generated OpenAPI descriptions from handler docstrings are out of scope for this rule.
- **One question at a time** when asking the user, numbered `Q1/X` where X is total identified.
- **Mark certainty explicitly:** `verified`, `likely`, `guess`. Especially for tool/library/API claims.
- **Lead with the answer.** No meta-commentary, no "let me think", no restating the question.
- **Match length to question.** Factual question = sentence. Design question = paragraph. Decision = structured bullets. Default to short.
- **Concise and sharp by default.** The user can always ask for elaboration. Don't pre-elaborate.
- **Push back on over-engineering.** Solutions complex relative to the problem must be questioned.
- **Stress-test your own output before delivering.** Don't commit unverified patterns.
- **Take user pushback seriously.** When the user says something looks wrong, reconsider, don't dismiss.
- **Distinguish exploratory mode from locked decisions.** When the user says "I'm exploring" or "thinking about", ideas are context, not decisions.
- **Proactively flag when a decision starts requiring workarounds, special cases, or reasoning that contradicts other decisions.** Do not wait to be asked.

### Logging discipline

Logging has real cost (CPU, Cloud Logging billing, signal-to-noise ratio). Cheap to be disciplined from day one.

- **One INFO log per request.** Standard fields: timestamp, level, request_id, tenant_id (where applicable), user_id (where applicable), method, path, status, latency_ms. The audit-context middleware handles this.
- **One ERROR log per failure.** Full context (exception, request ID, what failed). No swallowed exceptions.
- **No DEBUG logs in committed code unless gated by feature flag.** Investigative DEBUG logs can be added during development; remove or downgrade before commit.
- **No logging request or response bodies.** Summary statistics only (e.g., row count, not row contents).
- **No logs in tight loops or hot paths.** A 10,000-row response is one log line, not 10,000.
- **Log levels: INFO in prod, DEBUG only when actively investigating locally.**

---

## Stack

### What

| Layer | Choice |
|---|---|
| Language | Python 3.12 |
| Framework | FastAPI (async) |
| DB engine | PostgreSQL 15 |
| DB driver | psycopg3 (async) |
| ORM | SQLAlchemy 2.x async |
| Migrations | Alembic |
| Auth | Auth0 (prod) / RS256 stub (build phase) |
| Package manager | uv |
| Type checking | mypy strict |
| Cloud | Google Cloud Platform |
| Compute | GKE Autopilot |
| Edge | Cloudflare (post-MVP/v0) |

### Why

Cross-team consistency, modern async support, industry-standard patterns. Stack choices match the broader Ithina platform (especially Postgres 15 and async Python).

### Reconsider if

A specific tool reveals a real blocker. Stack drift causes cross-team friction; reasons to change must be substantive (not "I prefer X").

---

## Decisions

Decisions are listed with reasoning and reconsider conditions. Decisions are NOT rules; they are starting positions that should be revisited if conditions change.

### D-01 — Two caller classes: Ithina staff and tenant users

**What.** The service serves both Ithina internal staff (cross-tenant operators) and tenant-side users (customer-organisation employees). Two auth contexts, two route postures.

**Why.** Product spec covers both. Different access patterns and security requirements.

**Reconsider if.** Product direction shifts to staff-only or tenant-only. Or if a third caller class emerges (e.g., partner integrations).

### D-02 — Pattern 2 user split: physically separate `platform_users` and `tenant_users` tables

**What.** Not a single `users` table with audience flag.

**Why.** Cross-tenant leak risk = disaster. Physical separation makes leakage structurally impossible, not policy-based. Industry-standard for regulated B2B.

**Reconsider if.** Customer expectation shifts to "single human, multi-tenant" UX (rare in retail). Or if maintenance overhead of two tables exceeds the safety value (unlikely).

### D-03 — Multi-tenancy via shared schema + `tenant_id` + Postgres RLS with FORCE

**What.** Every tenant-owned table has `tenant_id` + RLS policy + FORCE ROW LEVEL SECURITY. Default-deny when no tenant context is set. Per-transaction context is set via three `set_config(name, value, true)` calls inside `get_tenant_session`: `app.tenant_id` (UUID-or-NULL), `app.user_type` (`'PLATFORM'`/`'TENANT'`), and `app.request_id` (UUID, NULL outside a request context). The first two govern visibility (see D-27 for the NULLIF requirement on the policy side; FN-AB-14 for the OR-clause on `user_role_assignments`); `app.request_id` is read by audit triggers landing at Step 6.2 to correlate row-level changes with HTTP requests.

**Why.** RLS is the last-resort filter. Even if every layer above fails, RLS gives zero rows instead of wrong rows.

**Reconsider if.** A specific table genuinely cannot work under RLS. None identified yet.

### D-04 — Read-only API for v0 (SUPERSEDED by D-35)

**What.** No POST, PATCH, PUT, or DELETE endpoints in v0. Database populated externally (manual SQL, external scripts). Future versions will add writes.

**Why.** v0 ships in 10 days. Read-only halves the surface area, eliminates write-side invariants, simplifies testing. Frontend still gets full screen functionality.

**Reconsider if.** A write requirement emerges that the frontend must trigger directly before v1. Or if external population proves operationally too painful.

### D-05 — Per-region deployment with hard residency boundary

**What.** EU and US regions, independent stacks. Tenants pinned to region at onboarding. Per-region hostnames (admin-eu.ithina.com, admin-us.ithina.com).

**Why.** Data residency for EU customers. No cross-region routing in backend.

**Reconsider if.** Customer demand requires a third region or a strict residency posture (FN-AB-01 covers).

### D-06 — Admin backend is sole writer to its own tables in master DB

**What.** Admin backend owns and writes tables for tenants, stores, users, RBAC, audit logs. Master DB is shared platform-wide and may contain tables owned by other services. Admin backend does not write to those.

**Why.** One-writer-per-table rule eliminates cross-team migration coordination on tables admin backend owns.

**Reconsider if.** A new admin-backend feature requires writing to a table owned by another service (in which case, coordinate API/event boundaries instead of direct writes).

### D-07 — Auth0 for authentication, with stub during build

**What.** Build phase uses StubAuthClient with Auth0-shaped JWT claims (RS256). Production swap to real Auth0 = JWKS URL + iss/aud values; no handler code change.

**Why.** Auth0 ownership not yet assigned. Stub unblocks build. Production-shaped from day one means low-risk swap.

**Reconsider if.** Auth0 ownership lands with a different decision (different IdP, in-house, etc.).

### D-08 — Middleware (auth + request context) and dependency (`get_tenant_session`)

**What.** ASGI middleware for auth and request context. FastAPI dependency for tenant-scoped session.

**Why.** Industry-standard FastAPI pattern. Audit log context needs middleware anyway. Handler signatures stay clean.

**Reconsider if.** Performance profiling shows the middleware path is a bottleneck (unlikely at MVP scale).

### D-09 — Roles are platform-global; tenants cannot create custom roles in v0

**What.** All roles platform-defined by Ithina staff in v0.

**Why.** Simplifies the model. Custom-role UI is for Super Admin only.

**Reconsider if.** Customer feedback demands tenant-specific roles. Schema accommodates this without change (audience enum + nullable tenant_id).

### D-10 — Permission shape: Module + Resource + Action + Scope

**What.** Permissions stored as composite rows. UNIQUE on (module, resource, action, scope). Roles reference permissions via `role_permissions` junction.

**Why.** Matches the permission matrix UI. Catalogue-first discipline.

**Reconsider if.** Permission shape needs to extend (time-bound, ABAC conditions).

### D-11 — User-role assignment carries org_node anchor; permissions cascade via ltree

**What.** Assignments anchor at a position in the org tree. Permissions apply to that node and all descendants via ltree `<@` operator.

**Why.** Audit-friendly. Permission check is one indexed ltree query.

**Reconsider if.** Permission resolution needs alternative semantics.

### D-12 — Tenant onboarding is staff-driven (Phase 1)

**What.** Ithina staff creates tenant + first tenant admin manually.

**Why.** Phase 1 customers are select paying. Sales/onboarding handles them manually. No public signup form, no abuse surface.

**Reconsider if.** Self-serve becomes a product requirement.

### D-13 — Audit-actor columns: Pattern (b) is the default; Pattern (a) where the actor type was never polymorphic

**Background.** Audit-actor columns (`*_by_user_id`, recording who created, updated, suspended, archived, etc. each row) were originally designed when there was a single `users` table. When that table was split into `platform_users` and `tenant_users` (D-02), the audit-actor columns now had a polymorphism problem: a row could be acted on by either a platform user or a tenant user, and a single FK column can only point at one table. Adding polymorphic FKs (separate `*_by_platform_user_id` and `*_by_tenant_user_id` with XOR CHECK) was rejected as more complexity than the integrity gain warranted at v0 scale.

**What.**

- **Pattern (b) — UUID + actor_user_type_enum, no FK. This is the default.** Every table with audit-actor columns uses this pattern unless there's a specific reason not to. Paired columns: `*_by_user_id UUID` and `*_by_user_type actor_user_type_enum`. No FK to either user table. App-layer validation must ensure the UUID exists in the correct table given the type.

- **Pattern (a) — typed FK to platform_users. Used only where the actor was, by definition, never polymorphic.** Single `*_by_user_id UUID REFERENCES platform_users(id)`, no `*_user_type` column. ON DELETE RESTRICT, ON UPDATE RESTRICT. Used only on tables where the actor can structurally only be a platform user, so there was never a polymorphism problem to solve.

- **Some tables have no audit-actor columns at all.** Catalogue tables whose rows are inserted exclusively via seed migrations, not by app code, not by manual SQL, have no per-row actor to record. The seed migration's git history is the audit trail.

**Mapping.**

| Table | Pattern | Why |
|---|---|---|
| `lookups` | none | Catalogue table, populated via seed migration only (per AI-LK-01). No app-driven write path. |
| `permissions` | none | Catalogue table, populated via seed migration only. No app-driven write path. |
| `platform_users` | (a) self-FK | Actor is, by definition, a platform user (only platform users create/update/suspend other platform users). |
| `tenants` | (a) FK to platform_users | Actor is, by definition, a platform user (only Ithina staff create tenants). |
| `tenant_users` | (b) | Actor can be platform user (Ithina staff during onboarding) or tenant user (post-self-serve). True polymorphism. |
| `org_nodes` | (b) | Actor can be platform user or tenant admin. |
| `stores` | (b) | Same. |
| `roles` | (b) | Default. (Per D-09, only platform users currently write these; Pattern (b) is the default and was applied uniformly.) |
| `role_permissions` | (b) | Default. (Same reasoning as `roles`.) |
| `user_role_assignments` | (b) | Actor can be platform user or tenant admin. True polymorphism. |

For `user_role_assignments`: note that `granted_by_user_id` and `revoked_by_user_id` are audit-actor columns (Pattern b). The `platform_user_id` and `tenant_user_id` columns on the same table are *subject* columns (the user being granted the role), governed by an XOR CHECK with FKs to both user tables. Different concerns; do not conflate.

**Why this default.** Pattern (b) keeps the schema simple where the polymorphism is real and uniform where it isn't. The alternative (polymorphic FKs everywhere) was rejected as more complexity than the integrity gain warranted at v0 scale.

**Implications for ORM (Step 3.1 onward).**

- Pattern (a) tables: SQLAlchemy `relationship()` to `PlatformUser`. Standard FK-backed relationship.
- Pattern (b) tables: no `relationship()`. The `*_by_user_id` is a raw UUID column; `*_by_user_type` is a typed enum column. Resolution to the actual user row happens in application code (read whichever table the type indicates), or is deferred entirely if the use case is just "show audit timestamp and ID."
- Tables with no audit-actor columns: no actor handling at all.

**Tech debt acknowledged.** Pattern (b) tables have no DB-level referential integrity on actor columns. App-layer validation (and discipline in seed/migration scripts) is the only check. Tracked in FN-AB-09.

**Reconsider if.**

- Drift becomes visible: orphan rows surface in CI tests, audit display issues appear, compliance audits flag the missing integrity.
- The polymorphism problem changes shape (e.g., a third actor type is introduced).
- The cost of polymorphic FKs becomes worth paying for the integrity gain (e.g., regulatory or compliance pressure).


### D-14 — App-level connection pool only; no PgBouncer for MVP

**What.** SQLAlchemy/asyncpg pool inside FastAPI process. `pool_size=10`. `prepare_threshold=None` set from day one for PgBouncer-readiness later.

**Why.** MVP scale doesn't need PgBouncer. Adding later is ~10-15 hours IF prepared statements stay disabled from day one (which they do).

**Reconsider if.** Connection saturation shows in metrics, or pod count grows past Cloud SQL connection limits.

### D-15 — Schema name is parameterised via `DB_SCHEMA` env var; not committed to a specific name in code or DDLs

**What.** Admin-backend tables live in a Postgres schema whose name is supplied at runtime via the `DB_SCHEMA` environment variable. The application reads it from config; the schema is referenced through search_path (set on the Postgres role and on every connection by the engine), not hardcoded anywhere. DDL files use unqualified table names; tables resolve to the configured schema via search_path.

The schema name is independent per environment. Local can be `core`. Dev can be `admin_backend`. Prod can be something else. The application code, DDLs, and migration files are identical across all environments; only the env-var value differs.

**Why.** Three reasons. First, naming is a decision that often gets stuck early in a project; parameterising it lets technical work proceed without freezing names. Second, multiple services may eventually share the master DB; naming the schema per-environment lets each environment's owner pick a name that doesn't clash with siblings. Third, renaming a schema later (`ALTER SCHEMA ... RENAME`) is a 30-second operation if no name is hardcoded in code or migrations.

**Implications for build steps.**

- **Step 1.4 (apply DDLs):** Apply unqualified DDLs against a connection whose search_path resolves to the configured schema. Tables land in the right schema automatically.
- **Step 1.5 (smoke test):** The script must `SET search_path TO <schema>, public` at session start, reading the schema name from the `DB_SCHEMA` env var. Don't rely on the role-level default alone.
- **Step 1.6 (Alembic wrap):** Migration files must NOT contain hardcoded schema names. `migrations/env.py` reads `DB_SCHEMA` from env and either sets `target_metadata.schema = None` and uses search_path, OR sets `target_metadata.schema = schema` reading from env. Migration files generated must be portable across schema names.
- **Step 2.1 (config):** `Settings` class includes `db_schema: str` (required, no default).
- **Step 2.2 (DB engine):** Connect-time hook sets `search_path TO {settings.db_schema}, public` on every new connection. Belt-and-suspenders against role-default drift.
- **Step 3.1+ (ORM models):** Every model uses `__table_args__ = {"schema": settings.db_schema}`, never a literal string.
- **Step 4.1 / 4.4 (cloud deploy):** GCP-helper picks dev and prod schema names; configures them via k8s ConfigMap. Same DDLs, same code, same migrations.

**Reconsider if.** The parameterised setup proves more friction than the cross-environment flexibility it provides. Specifically: search_path bugs become a recurring debugging cost, or a future tool/library doesn't honour search_path correctly and forces hardcoding.

### D-16 — Audit log table required, populated externally in v0

**What.** Schema designed and migrated. App reads from it. Population via manual SQL / external scripts / future services. App does not write audit log entries in v0.

**Why.** v0 is read-only; audit entries originate elsewhere. Future writes will populate audit_logs as a side effect.

**Reconsider if.** Frontend or operational requirement emerges that demands real-time audit writes.

### D-17 — RLS-blocked reads return 404, not 403

**What.** When RLS filters a row out, surface as `NotFoundError` → 404.

**Why.** Returning 403 leaks the existence of the resource. 404 says "no such resource as far as you're concerned."

**Reconsider if.** Industry standard or compliance requires explicit 403 for known-but-forbidden resources.

### D-18 — Tenant mismatch (JWT vs path) returns 400 with quarantine code

**What.** If JWT's tenant_id and path tenant_id disagree, do not return either. Log loudly, return generic 400 with `code=TENANT_CONTEXT_MISMATCH`.

**Why.** Potential attack signal. Treating it as anything other than quarantine masks attempts at tenant impersonation.

### D-19 — Critical-path tests for v0; comprehensive coverage post-launch

**What.** v0 covers cross-tenant isolation, tenant-mismatch quarantine, suspended user, org tree descendants, permission cascade, audit log filters. Other tests post-launch.

**Why.** 10-day timeline. Critical-path tests catch the disasters; comprehensive tests catch regressions and need time.

**Reconsider if.** Critical paths reveal patterns of bugs that suggest broader test coverage is urgent.

### D-20 — Structured JSON logs to stdout; basic Prometheus metrics

**What.** `python-json-logger` for logs. `prometheus-fastapi-instrumentator` for metrics. OpenTelemetry deferred.

**Why.** Floor for paying B2B customers. GCP-native (Cloud Logging picks up stdout JSON automatically).

**Reconsider if.** Cross-service debugging becomes frequent (then add OpenTelemetry).

### D-21 — Schema conventions

**What.**
- snake_case identifiers; plural tables; singular columns.
- PK is `id` (UUID **v7**, default `uuidv7()` defined in
  `Ithina_postgres_SQL_DDL_shared_utilities_v1.sql`). UUIDv4 via
  `gen_random_uuid()` is **not** used. The insert-locality and
  WAL-pattern benefits of v7 are load-bearing for canonical-layer
  table design downstream; mixing v4 and v7 in the same table
  defeats both.
- FK columns: `<referenced>_id`.
- Timestamps: `_at` suffix.
- Booleans: `is_<state>` or `has_<thing>`.
- Constraint prefixes: `pk_`, `fk_`, `uq_`, `ix_`, `ck_`.
- Enums: `<column>_enum` singular.
- TEXT over varchar(n). TIMESTAMPTZ for all timestamps. JSONB over JSON.

**Why.** Standard SQL conventions. Already applied to the 7 DDL files.

**Reconsider if.** Cross-team alignment requires a different convention (unlikely; this matches DIS). Postgres 18 lands on Cloud SQL with native `uuidv7()`: replace the PL/pgSQL implementation in shared utilities with a wrapper around the native function, or redirect the DEFAULT directly. App code and DDL DEFAULTs do not change.

### D-22 — Single GCP project for dev; separate for prod

**What.** `ithina-admin-dev` and `ithina-admin-prod`. Same region for now (us-central1 or first customer's region). Cross-project IAM isolation.

**Why.** Clean blast radius. Standard for production-bound services.

### D-23 — Terraform from day one for infra; skip ArgoCD / DR / Cloudflare for MVP (revised 2026-05-03)

**What.** GCP infrastructure provisioned via Terraform from day one. The Terraform code lives in the separate `ithina-retail-admin-infra` repo (not in this repo): root `terraform/` directory there with `bootstrap/`, `modules/`, and `envs/dev/` (prod env added at Step 8.1.1). ArgoCD, DR, and Cloudflare remain deferred to post-launch.

**Why revised.** Operator preference for IaC over imperative gcloud scripts on this stack. Trade-off accepted: slower first apply and some Terraform-on-GCP debugging in exchange for state-tracked, drift-detectable, reproducible infra. Original D-23 wording deferred Terraform entirely; the revised stance pulls it forward to day one because the value of having the dev environment captured-as-code from the start outweighs the time cost of the first apply.

**Reconsider if.** Operator decides imperative scripts are preferable after first apply.

### D-24 — JWT carries identity claims only; permissions resolve in-app per request

**What.** JWTs (from Auth0 in production, from stub in build phase) carry only identity-shape claims. The custom claims that appear in the JWT are exactly:

- `https://ithina.com/tenant_id` — UUID, the user's home tenant. NULL for platform users.
- `https://ithina.com/user_type` — enum `PLATFORM` or `TENANT`.
- `https://ithina.com/user_id` — UUID, the row id in `platform_users` (when `user_type=PLATFORM`) or `tenant_users` (when `user_type=TENANT`).
- `https://ithina.com/email` — string, the user's email.

Plus standard JWT claims (`sub`, `iss`, `aud`, `exp`, `iat`, `nbf`).

No role names, no permission codes, no org-node anchors, no feature flags. All authorisation data resolves in-app from the DB per request.

**Why.**

1. **Permission staleness is a security incident.** A JWT lives for its TTL (typically 1 hour). If a permission is revoked, a JWT-stored permission stays valid until refresh. Identity is far more stable than permissions; identity is safe to embed, permissions are not.

2. **JWT size has hard limits.** Common HTTP infrastructure (load balancers, CDNs, browsers) breaks around 4-8 KB headers. Resolved permissions for a complex user can blow past this.

3. **Source-of-truth discipline.** Permissions live in `permissions`, `roles`, `role_permissions`, `user_role_assignments`. These tables are where audit, policy review, and revocation happen. JWTs duplicating that data would create two sources of truth.

4. **Shape match with industry pattern.** Multi-tenant B2B SaaS with own user DB (Stripe, Shopify, GitLab, Linear pattern) keeps Auth0 to identity, resolves authz app-side. Differs from internal-only services or read-only API gateways which sometimes embed permissions for throughput.

**Implications for build steps.**

- **Step 2.1 (stub auth):** `AuthContext` has fields `user_id, tenant_id, user_type, email, sub, iss, aud, exp`. No `roles` field. No `permissions` field. The `make_test_jwt(...)` helper accepts only these claims. Verification rejects tokens whose claim shapes don't match. Pydantic model is frozen; mutation raises.

- **Step 2.2 (DB engine session bootstrap):** Connect-time hook sets `app.tenant_id` and `app.user_type` from `AuthContext`. Both come from the JWT only; no other source (request body, query params, headers). NULL `tenant_id` for PLATFORM users is set as `RESET app.tenant_id` (true NULL, not empty string), accompanied by `app.user_type = 'PLATFORM'`.

- **Step 3.x onward (handlers):** Permission checks query DB tables, never JWT claims. Permission resolution is cached for the request lifetime via FastAPI dependency caching, not longer. Caching across requests is forbidden until Auth0 webhooks (or equivalent) signal revocation.

- **Auth0 management API (when wiring real users):** When creating a user via `POST /api/v2/users`, populate `app_metadata` with exactly the four custom claims above. No `roles`, no `permissions`. Update `app_metadata` only when the user's identity changes (rare); never to push permission updates.

**AuthContext validation rules:**

- `TENANT` user_type requires non-NULL `tenant_id`. Pydantic validator enforces.
- `PLATFORM` user_type for v0 is permissive on `tenant_id`: NULL is the standard case; non-NULL is allowed and treated as "this platform user is operating in tenant context" (impersonation pattern, deferred capability). Validator allows both.

**Reconsider if.**

- A specific high-traffic endpoint demonstrates that per-request DB permission resolution is a measured bottleneck. Then revisit per-endpoint cache strategies before adding to JWT.
- A regulatory or compliance requirement mandates JWT-embedded permissions (rare; usually the opposite — auditors want a single source of truth).
- Cross-service authorisation needs (DIS or Pricing OS reading tenants/users) force a pattern where permissions are signed and shareable. Different problem; revisit then.

### D-25 — `uv.lock` is tracked in git; treat as deployable artefact

**What.** `uv.lock` is committed to the repo. It pins exact resolved versions of every transitive dependency.

**Why.** `admin-backend` is a deployed application, not a library. Standard pattern for deployed Python applications under modern uv-managed projects (per Astral's guidance) is to commit the lock file. Reproducibility benefits: identical dependency versions across local, CI, and cloud builds; ability to audit when a vulnerable transitive dep was active via git history; protection against silent dep drift between Claude Code sessions.

The lock file was originally gitignored as scaffolding leftover; this entry captures the corrected policy from the Step 2.1 commit forward.

**Reconsider if.** The repo becomes a library (rather than a deployed service); some other tooling shifts the consensus on lock-file tracking.

### D-26 — JWT library is `pyjwt[crypto]`

**What.** The dependency line is `pyjwt[crypto]>=2.8`. The `[crypto]` extra pulls in `cryptography` transitively, which is what supplies the RS256 backend. PyJWT alone does not.

**Why.** PyJWT's base install does not include the cryptography backend. `import jwt` succeeds without it; `jwt.encode(..., algorithm="RS256")` then raises `NotImplementedError: Algorithm 'RS256' could not be found. Do you have cryptography installed?` at first sign/verify. The failure surfaces only at runtime, not at import time, so a quiet downgrade of the dependency line (during a routine dep cleanup or a library swap) silently breaks RS256.

If the JWT library is ever swapped (python-jose, authlib, jwcrypto, native PyCA, etc.) for the Auth0 production wiring or any other reason, the chosen library's RS256 dependency must be verified explicitly. The unit tests that exercise sign/verify will catch the breakage, but a quiet drop of the dependency extra will not show up at lint/import time.

**Reconsider if.** Common JWT libraries start shipping a cryptography backend in their base install (currently rare).

### D-27 — RLS policies wrap `current_setting(...)` in `NULLIF(..., '')` for `app.tenant_id`

**What.** Every multi-tenant RLS policy that compares `tenant_id` against `app.tenant_id` reads the GUC as `NULLIF(current_setting('app.tenant_id', TRUE), '')::uuid`, never as `current_setting('app.tenant_id', TRUE)::uuid` directly. All future RLS policies on tenant-owned tables must follow this pattern.

**Why.** Postgres 15 registers a placeholder GUC (one with a `.` in the name, like `app.tenant_id`) at session level the first time `set_config(name, value, is_local=true)` runs on a connection. After the transaction commits, `current_setting('app.tenant_id', TRUE)` no longer returns NULL on that physical connection; it returns the empty string `''`. The cast `''::uuid` raises `invalid input syntax for type uuid: ""`, which means the original policies (`tenant_id = current_setting(...)::uuid`) crash on every reused pooled connection past its first use.

The `NULLIF(..., '')` wrapper makes both the pristine-NULL case and the registered-empty-string case resolve to NULL. `tenant_id = NULL` is unknown (false in WHERE), so default-deny is preserved on top of FORCE ROW LEVEL SECURITY without runtime errors. Discovered and fixed during Step 2.2a; landed via migration `e59f62d5037d` on all 5 multi-tenant tables (tenants, tenant_users, org_nodes, stores, user_role_assignments).

The Step 1.5 smoke test did not catch this because it uses `force_rollback=True`; ROLLBACK reverts the GUC, leaving subsequent connections pristine. Real handlers COMMIT, so every pooled connection past its first use was in the broken state.

**Reconsider if.** Postgres lifts the placeholder-GUC empty-string-after-set semantic (unlikely, long-standing behaviour). Or if a future Postgres version introduces a clean way to "deregister" a placeholder GUC mid-session that we adopt instead of NULLIF.

### D-28 — Provisional API response shape defaults pending Step 2.0

**What.** `docs/api-contract.md` is in TEMPLATE state pending the Step 2.0 sync with the frontend developer. To unblock Step 3.1 (the first ORM model + read schema, which locks the response-shape pattern reused by every subsequent resource), the following defaults are locked **provisionally** and propagate from `TenantRead` forward to `StoreRead`, `PlatformUserRead`, `TenantUserRead`, `OrgNodeRead`, RBAC reads, and `AuditLogRead`:

- **Q1 — response field naming:** `snake_case`. No camelCase aliasing on Pydantic models. Pydantic's default field-name-as-JSON-key behaviour applies.
- **Q4 — datetimes:** ISO 8601 with timezone offset (Pydantic v2's default `datetime` serialisation when the value is timezone-aware). UTC `Z` or `+00:00` both acceptable.
- **Q7 — null handling:** nullable fields are emitted explicitly as JSON `null`. No `model_dump(exclude_none=True)` at any layer.
- **Q11 — NUMERIC fields:** monetary / precise-decimal fields serialise to JSON as **strings** to preserve precision in JS clients (which lose precision past 2^53 - 1). Use `field_serializer(when_used="json")` so `model_dump()` (Python mode) still returns `Decimal`.
- **Q2 — list-response wrapping:** **NOT** decided here; deferred to Step 3.2/3.3 when the consumer (`TenantsRepo` + router) lands.

**Why.** Without these provisional locks, Step 3.1 stalls — the response shape question is too load-bearing to defer past the first model. Each default chosen tracks the most-conservative interpretation of how a typical web frontend consumes a B2B admin API: snake_case matches the DB column names (no transform layer to drift), explicit nulls let frontend distinguish "not set" from "field omitted", NUMERIC-as-string is the only safe option for currency in JavaScript, and ISO-8601-with-offset is what Pydantic v2 emits without coercion.

**How to apply.** Every subsequent `*Read` schema mirrors `TenantRead`'s shape: `ConfigDict(from_attributes=True)`, no aliasing, audit-actor IDs hidden, `field_serializer(when_used="json")` on any `Decimal`/`NUMERIC` field. If Step 2.0 lands a different decision on any of Q1/Q4/Q7/Q11, the change is localised to schema files (no router or repo touch needed) and tracked here as a D-28 amendment.

**Reconsider if.** Step 2.0 happens and the frontend developer locks any of these differently, or asks for camelCase aliasing, or wants currency as a number with explicit precision metadata, or wants `exclude_none`. Step 2.0's outcome supersedes this entry entirely; rewrite or remove D-28 then.

### D-29 — PLATFORM RLS visibility via policy clause, not BYPASSRLS role

**What.** PLATFORM users see all rows on multi-tenant tables via a permissive OR-branch on every multi-tenant RLS policy, keyed on `current_setting('app.user_type', TRUE) = 'PLATFORM'`. No separate Postgres role with `BYPASSRLS` exists; the application role is `NOSUPERUSER NOBYPASSRLS` and stays that way (per Step 1.5 hardening, with a startup gate refusing to boot otherwise).

**Single shape across all multi-tenant tables.** All 6 multi-tenant policies use the unconditional OR-branch:

```
OR current_setting('app.user_type', TRUE) = 'PLATFORM'
```

Tables: tenants, tenant_users, org_nodes, stores (Step 3.0, migration `21e2ad16303a`); tenant_module_access (Step 3.4.5, migration `cd2a02e452ae`); tenant_user_role_assignments (Step 6.8.1, migration `3e05299cb533`).

The IS-NULL-gated form that originally lived on `user_role_assignments` (FN-AB-14, migration `4fd3aec6ae0c`) was retired by Step 6.8.1: the table that needed it was split into `platform_user_role_assignments` (no RLS — platform-global; visibility at app layer) and `tenant_user_role_assignments` (NOT NULL `tenant_id`, unconditional OR-branch). Pre-Step-6.8.1, the IS-NULL gate was the only structural divergence among the 6 multi-tenant policies; post-split, the policy shape is uniform.

The NULLIF wrapper on `current_setting('app.tenant_id', TRUE)` per D-27 is preserved on every policy.

**Why.** Three reasons:

1. **One application role.** Adding a BYPASSRLS role would mean two separate connection pools (one per role), divergent connection strings per request type, and a runtime-policy switch on every connection. Policy-clause visibility keeps the pool single, the role single, and the configuration uniform.

2. **One audit anchor.** With BYPASSRLS, RLS-bypassed queries leave no trace of *why* the bypass happened beyond the role. With a policy clause, the same `app.tenant_id` / `app.user_type` GUCs that govern visibility are also captured by `app.request_id` (Step 2.3) into row-level audit triggers (Step 6.2). Every PLATFORM-visible row read or written is correlated back to a request_id and a user_type.

3. **Same RLS surface for testing.** The smoke test (truth-table assertions in `scripts/smoke_test.py`) exercises every (`app.tenant_id`, `app.user_type`) combination on the same physical pool of connections that production handlers use. A BYPASSRLS approach would require either skipping RLS in tests or maintaining a parallel test suite for the bypass role.

**Permissive impersonation property.** When `app.tenant_id` is set AND `app.user_type = 'PLATFORM'`, the OR-branch is TRUE for every row, so the user sees all rows on these tables — not just the impersonated tenant's. For v0 this is intentional: RLS is the visibility floor; if true impersonation-scoping is needed (e.g., a Support Admin's UI showing only the impersonated tenant's data while debugging a ticket), it must be enforced at the application layer — typically as a `WHERE tenant_id = <impersonated_id>` filter in the handler, on top of RLS. Step 6.1 (RBAC) is where this handler-layer scoping lands.

**How to apply.** Every multi-tenant table added post-v0 follows the same pattern. Pick the unconditional or IS-NULL-gated shape based on the column's nullability. The DDL describes the *initial* schema (per the frozen-DDL convention); subsequent policy edits go through Alembic migrations.

**Reconsider if.** v1 needs RLS-enforced impersonation-scoping (a PLATFORM user with `app.tenant_id` set should NOT see other tenants' rows accidentally while impersonating). The policy then needs a third state — possibly a fourth `app.*` GUC like `app.impersonation_active` set by `get_tenant_session` only when AuthContext indicates impersonation. The OR-branch becomes `OR (app.user_type = 'PLATFORM' AND NOT app.impersonation_active)`. Revisit then; until then, the permissive property is by design.

### D-30 — Response envelope is list-only

**What.** Endpoints returning a collection wrap as `{items, pagination}`. Endpoints returning a single object return the object directly with no wrapper. No top-level `data` key, no `result` key — single-object responses *are* the object.

**Why.** Wrapping single objects adds a layer the frontend has to peel for no benefit. The list wrapper exists because pagination metadata has nowhere else to live; a single-object response has no metadata to carry.

**How to apply.** Pydantic `*ListResponse` schemas hold `items: list[<Item>]` and `pagination: Pagination`. Single-object schemas (e.g., `TenantDetail`) are returned as-is. Cross-cutting metadata that doesn't fit (rate-limit body indicators, partial-result flags) gets pushed to response headers, not synthetic top-level keys.

**Reconsider if.** Cross-cutting metadata becomes a recurring need across ALL endpoint types (not just one). Until then, the simpler shape wins; per-endpoint exceptions go in headers.

### D-31 — Response field semantics are append-only

**What.** Once a field ships with defined meaning, that meaning is frozen for the lifetime of the API version. New variants are added as new fields with distinct names, never as semantic reinterpretations of existing fields.

**Why.** Renaming-while-keeping-the-name is invisible to frontend code that's already shipped: a UI that read `monthly_revenue_usd` as "self-reported revenue" continues to read it as such, even if the backend silently swapped it to "live revenue." There is no compile-time signal of the change. Adding a new field (`monthly_revenue_live_usd`) is visible: the frontend opts in by reading it.

**How to apply.** A migration to a new semantic must add a new field, populate both for one release, then deprecate the old. The error-envelope `details` field's role is to surface validation specifics in the future without altering `code` or `message` semantics — same principle.

**Reconsider if.** Never; this is a compatibility invariant. The escape hatch when a field truly needs different semantics is bumping the API version (v2), not redefining v1 fields.

### D-32 — Cloud Run for the frontend (both envs) (2026-05-03)

**What.** The Next.js frontend deploys to Cloud Run in both dev and prod.

**Why.** Stateless, bursty, scales to zero. No need to share a network with Cloud SQL; the frontend talks to the backend over HTTPS, not directly to the DB.

**Reconsider if.** Frontend grows long-running server-side jobs that don't fit Cloud Run's request-time model.

### D-33 — Backend on Cloud Run in dev; backend on GKE in prod (2026-05-03)

**What.** The backend runs on Cloud Run in the dev environment and on GKE Autopilot in prod. Cloud Run for dev: one v2 service for the long-running API, one v2 Job for Alembic migrations. Both use the same Docker image. Direct VPC egress reaches Cloud SQL on its private IP. No sidecar; Cloud Run's runtime SA holds `roles/cloudsql.client` and `secretAccessor`. Frontend stays on Cloud Run in both environments per D-32.

**Why.** v0 timeline + first-time GCP. Running dev on the full Autopilot + Workload Identity + sidecar + Ingress stack costs operational time and money (~$75/mo Autopilot cluster fee) for parity benefits we don't actually need yet. The image artifact is identical across both shapes — we ship the same container to prod that we tested in dev. The k8s-specific patterns (sidecar, WI, BackendConfig, Ingress) are well-trodden; first contact in prod is acceptable risk.

**Trade-off accepted.** Dev no longer mirrors prod's runtime shape. The sidecar pattern, Workload Identity binding, GCE Ingress, and readiness probes are first exercised in prod. If a prod-only failure mode appears, dev will not have caught it. Mitigation: prod cutover (Step 8.2) treats this as new ground; smoke and cross-tenant tests run there explicitly, not as a routine re-run of dev.

**Reconsider if.** A prod incident traces back to a runtime-shape difference that dev would have caught. Then move dev to GKE for parity, accepting the operational cost. Tracked in `docs/post-launch-backlog.md` once that file is created at Step 10.2.

### D-34 — Mixed-audience tables get split into per-audience physical tables (2026-05-09)

**What.** Tables that mix PLATFORM-audience rows (no `tenant_id`) with TENANT-audience rows (`tenant_id` NOT NULL) are split into two physical tables rather than unified with a nullable `tenant_id` and an IS-NULL-gated RLS policy:

- `platform_<entity>` — platform-global, no RLS, references `platform_users` only.
- `tenant_<entity>` — multi-tenant with the standard D-29 unconditional OR-branch RLS policy, NOT NULL `tenant_id`.

Established at Step 6.8.1 by splitting `user_role_assignments` into `platform_user_role_assignments` and `tenant_user_role_assignments` (migration `3e05299cb533`).

Step 6.8.1 also added `UNIQUE (tenant_id, id)` to `tenant_users` as a precondition for the new composite FK from `tenant_user_role_assignments(tenant_id, tenant_user_id) → tenant_users(tenant_id, id)`. The constraint is named `uq_tenant_users_tenant_id` (mirrors `org_nodes`' `uq_org_nodes_tenant_id`). **The UNIQUE is NOT reflected in `tenant_users_v1.sql`** per the frozen-DDL convention; it's a live-vs-DDL divergence captured by the migration. Same precedent as the existing post-DDL drift on policy migrations (`e59f62d5037d` NULLIF wrapper, `4fd3aec6ae0c` IS-NULL gate, `21e2ad16303a` unconditional OR).

**Why.** Three reasons:

1. **Cross-tenant injection becomes structurally impossible, not runtime-prevented.** Composite FKs on `tenant_user_role_assignments(tenant_id, tenant_user_id)` and `(tenant_id, org_node_id)` reject any row whose denormalised `tenant_id` mismatches the user's or org_node's. AI-RBAC-06 closes at the schema layer; the future-DB-trigger forward note is retired.
2. **RLS shape becomes uniform.** All 6 multi-tenant policies use the same unconditional OR-branch (D-29). The IS-NULL-gated divergence on the original URA goes away.
3. **Aligns with Pattern 2 (D-12).** `platform_users` / `tenant_users` already established this split for users; URA generalises the principle to junction tables that span both audiences.

**Trade-off accepted.** Two physical tables instead of one. Application-layer code (Step 6.8.2 onward) branches on `auth.user_type` — known input from the verified JWT, not row content. Two queries, one fires per request based on the user's audience. Cleaner than the unified table's policy-clause complexity.

**Forward dependency.** `audit_logs` at Step 6.2 has the same nullable-`tenant_id` shape (NULL for GLOBAL events). It must split into `platform_audit_logs` (no RLS) and `tenant_audit_logs` (RLS+FORCE, unconditional OR-branch) per this principle. Step 6.2's prompt updates accordingly when drafted.

**Reconsider if.** A future requirement makes a unified table genuinely cheaper despite the cost of the IS-NULL gate or equivalent guard. This would have to overcome the precedent set here.

### D-35 — v0 scope expanded beyond read-only

**Supersedes.** D-04

**What.** v0 ships across six stages. Stage 1 is read-only foundation (D-04 was correct for that scope). Stage 2 adds write surface (Steps 6.10–6.15), audit log writes from app (Step 6.16), and RBAC enforcement (Section 6.9). Stage 3 swaps Auth0. Stages 5–6 are staging/UAT and production cutover. See BUILD_PLAN.md for the complete stage map.

v0 is defined as the product shipped to the first real beta user. All six stages must complete before v0 cutover.

**Reconsider if.** Never; this is a scope-set decision.

### D-36 — Two-table-one-entity coupling: atomic API at one endpoint (Step 6.21.2)

**What.** When two tables in the master DB together represent one logical entity (1:1 schema-enforced FK + UNIQUE, no meaningful existence without both rows, field-uniqueness checks require both rows present), all writes that touch either table flow through one API endpoint that performs the writes atomically inside a single request transaction. RBAC gates the caller's semantic intent (one permission tuple covers the whole atomic write). Shared fields have one owner endpoint; the other endpoint rejects modification with a clear 422. Lifecycle projections (where the two tables hold different enum value sets) live as named, tested mapping constants in the repo layer.

**Why.** The pattern surfaced twice in v0: tenant + tenant-root org_node at Step 6.20.1, and store + STORE-type org_node at Step 6.21.2. Codifying the rule prevents future endpoints from re-introducing the divergent-shape failure mode (a write surface that leaves orphan rows on the other side of a 1:1 link). Frontend coordination becomes simpler because one API call corresponds to one atomic state change.

**How to apply.** Current instances: tenant ↔ tenant-root org_node (Step 6.20.1, architecture.md § A.3); store ↔ STORE-type org_node (Step 6.21.2, architecture.md § A.5). Adding a third instance requires verifying all three conditions hold (1:1 schema-enforced relationship; both rows required by domain; cross-table field-uniqueness invariant). Merely sharing an FK does NOT make this pattern applicable — `tenant_users -> tenants` is a parent/child relationship, NOT one-entity-two-tables.

**Reconsider if.** A future seam matches conditions 1 and 2 but NOT 3, and the cost of forcing it through the atomic pattern outweighs the benefits. Most likely emergence: where the second table's `code` / unique value isn't actually needed across both tables, the seam can be modelled as a separate-entity write instead.

---

## Forward-notes (parked items)

These are visible so you do not try to redesign around them. **Do not act on any of these without explicit user direction.**

### FN-AB-01 — Data residency posture

Strict ("data + processing + access stays in region") vs storage-only. Decide before EU paying customers go live.

### FN-AB-02 — Auth0 integration ownership

Who owns Auth0 across Ithina, claim shape, tenant resolution pattern (claim-based vs DB-lookup), connection strategy. Decide before MVP launch.

### FN-AB-05 — Audit log storage location

Master DB vs separate DB vs hybrid (recent in Postgres + archive in BigQuery). Decide when audit log volume becomes a concern.

### FN-AB-06 — Tenant-specific roles

Schema accommodates without change. Defer until customer demand.

### FN-AB-09 — Pattern (b) tech debt

Pattern (b) audit-actor columns (UUID + actor_user_type_enum, no FK) carry no DB-level referential integrity. App-layer validation is the only safety net. Watch for orphan rows in CI tests. Migration to a polymorphic-FK pattern (separate `*_by_platform_user_id` and `*_by_tenant_user_id` columns with XOR CHECK) is feasible at ~2-4 days if the integrity gain becomes worth paying for.

### FN-AB-10 — Schema namespacing in production master DB (RESOLVED)

Resolved during pre-1.4 work. Schema namespacing is now parameterised per environment via the `DB_SCHEMA` env var, applied uniformly on local, dev, and prod from day one. No specific schema name is committed in code or DDLs; each environment picks its own. See D-15 for the current decision and reasoning.

### FN-AB-11 — DEPARTMENT linkage in org tree

DEPARTMENT entity exists in org_nodes; no downstream linkage to SKUs/sales. Defer until a query needs it.

### FN-AB-12 — Write endpoints (RESOLVED by D-35; promoted to Stage 2)

**Resolved:** D-35 promoted write endpoints to v0 Stage 2 (Steps 6.10–6.15) and audit log writes to Step 6.16. Original forward-note text preserved below for historical record.

v0 is read-only. Write endpoints (POST/PATCH/DELETE) are post-v0. Schema and infrastructure designed to support writes when added.

### FN-AB-13 — Postgres 18 native uuidv7() migration

When Cloud SQL Postgres 18 reaches GA and our cluster is upgraded, the PL/pgSQL `uuidv7()` function defined in `Ithina_postgres_SQL_DDL_shared_utilities_v1.sql` should be replaced with a wrapper around the native `uuidv7()` function (or the DEFAULT redirected directly). No data migration required; existing v7 rows remain valid. Estimated effort: 30 minutes plus one Alembic migration. Deferred until Postgres 18 GA on Cloud SQL.

### FN-AB-14 — user_role_assignments PLATFORM-audience policy (RESOLVED at Step 2.2b; deepened at Step 6.8.1)

**Step 6.8.1 deepens the resolution.** Step 2.2b's `4fd3aec6ae0c` migration was the right fix at the time given the unified-URA schema constraint: it added the IS-NULL-gated PLATFORM OR-branch so PLATFORM-audience rows (`tenant_id IS NULL`) became insertable from the application role. But the IS-NULL gate had two structural side effects that surfaced operationally:

1. PLATFORM sessions could not see all role assignments in one query (only the 3 PLATFORM-audience rows; TENANT-side rows hidden under unimpersonated PLATFORM).
2. AI-RBAC-06 cross-tenant injection prevention had to live at the application layer per the DDL's forward note.

Step 6.8.1 (migration `3e05299cb533`) **retires the IS-NULL gate entirely** by splitting `user_role_assignments` into `platform_user_role_assignments` (no RLS) and `tenant_user_role_assignments` (NOT NULL `tenant_id`, unconditional OR-branch matching the other 5 multi-tenant policies). See D-34 for the full rationale.

The historical record below documents Step 2.2b's resolution as it stood prior to Step 6.8.1.

---

The RLS policy on `user_role_assignments` was originally single-clause: `tenant_id = current_setting('app.tenant_id', TRUE)::uuid`. PLATFORM-audience rows (tenant_id NULL) could neither be matched (`NULL = anything` is NULL) nor inserted by any non-BYPASSRLS role. Step 1.5 smoke test surfaced this; Step 2.2a NULLIF-wrapped the expression per D-27; Step 2.2b lands the OR-clause that closes the gap.

**Resolution: option B (two-variable pattern, permissive for v0).** Migration `4fd3aec6ae0c` drops and recreates only `user_role_assignments_tenant_isolation` with the two-clause form:

```sql
CREATE POLICY user_role_assignments_tenant_isolation ON user_role_assignments
  USING (
    tenant_id = NULLIF(current_setting('app.tenant_id', TRUE), '')::uuid
    OR (tenant_id IS NULL AND current_setting('app.user_type', TRUE) = 'PLATFORM')
  )
  WITH CHECK (
    tenant_id = NULLIF(current_setting('app.tenant_id', TRUE), '')::uuid
    OR (tenant_id IS NULL AND current_setting('app.user_type', TRUE) = 'PLATFORM')
  );
```

The other 4 multi-tenant policies (tenants, tenant_users, org_nodes, stores) were not touched at Step 2.2b: their `tenant_id` (or `id` for `tenants`) columns are NOT NULL, so the FN-AB-14 IS-NULL-gated OR-branch would never fire there. **Step 3.0 (migration `21e2ad16303a`, 2026-05-02) extended the PLATFORM-visibility pattern to those 4 policies with a structurally different shape: an unconditional `OR app.user_type = 'PLATFORM'` (no IS-NULL gate). See D-29 for the full pattern.**

Permissive: a PLATFORM user with `app.tenant_id` set sees both that tenant's rows AND PLATFORM-audience rows (impersonation pattern). v0 keeps this permissive; revisit if/when impersonation needs distinct rules.

`app.user_type` is set per-transaction by `get_tenant_session` (Step 2.2a). The session bootstrap sets both vars from `AuthContext` only (AI-MT-03); the policy can rely on `current_setting('app.user_type', TRUE)` returning `'PLATFORM'` or `'TENANT'` inside any tenant-bound session.

**Empirical truth table (Step 2.2b smoke test, 9 rows × 3 row classes):**

| `app.tenant_id` | `app.user_type` | TENANT-A row | TENANT-B row | PLATFORM row | count |
|---|---|---|---|---|---|
| A | TENANT | Visible | Invisible | Invisible | 1 |
| B | TENANT | Invisible | Visible | Invisible | 1 |
| (unset) | TENANT | Invisible | Invisible | Invisible | 0 |
| A | PLATFORM | Visible | Invisible | Visible | 2 |
| B | PLATFORM | Invisible | Visible | Visible | 2 |
| (unset) | PLATFORM | Invisible | Invisible | Visible | 1 |
| A | (unset) | Visible | Invisible | Invisible | 1 |
| B | (unset) | Invisible | Visible | Invisible | 1 |
| (both unset) |  | Invisible | Invisible | Invisible | 0 |

Rows 7-8 (tenant_id set, user_type unset) are structurally unreachable through `get_tenant_session` per AI-MT-03; the policy gives them tenant_id alone is sufficient for tenant-row visibility, consistent with Option B's permissive intent. Default-deny holds when tenant_id is unset and user_type is not PLATFORM, or when both are unset.

**Smoke-test additions at Step 2.2b** (`scripts/smoke_test.py`):
- Old assertions 11/12 (which documented the gap as failing assertions) replaced by 9 truth-table assertions (`11.A/TENANT` ... `11.unset/unset`).
- New meta-assertion 12: every table in `core` with a `tenant_id` column has RLS enabled, FORCE enabled, and at least one policy. Catches future "added a multi-tenant table and forgot RLS".

**Canonical write pattern under PLATFORM session (added at Step 3.5; retired at Step 6.8.2 for the v0 chain).** Historical context: under the FN-AB-14 IS-NULL-gated policy form of `user_role_assignments`, a PLATFORM session could READ all rows (the OR-branch fired for `tenant_id IS NULL` rows; the equality clause handled the rest via impersonation), but the WITH CHECK predicate forced per-row tenant impersonation (`set_config('app.tenant_id', <row.tenant_id>, true)` before each INSERT or UPDATE) for TENANT-side writes. The seed loader at `scripts/seed_dev_data/loaders/user_role_assignments.py` was the canonical reference implementation.

**Status post Step 6.8.1 / 6.8.2 (D-34).** The IS-NULL-gated policy form is gone — `user_role_assignments` was split into `platform_user_role_assignments` (no RLS) and `tenant_user_role_assignments` (RLS+FORCE with the unconditional D-29 OR-branch). PLATFORM sessions write any row to either table without impersonation; the seed loader's per-row impersonation pattern was retired at Step 6.8.2. No table in v0 needs this pattern any longer.

**Forward applicability.** D-34's split principle prevents new mixed-audience tables with IS-NULL-gated policies from appearing in v0. If a future post-v0 table genuinely needs an IS-NULL-gated policy form (anti-pattern; D-34 says split instead), revisit this pattern with reference to git history of `loaders/user_role_assignments.py` pre-Step-6.8.2. The seed loader is no longer the reference implementation.

### FN-AB-15 — Regenerator script lacks staleness guard

`scripts/build_initial_migration.py` reads the 8 raw DDLs in `db/raw_ddl/` and emits the initial Alembic migration (`ad8afd429581`). It has no awareness of subsequent migrations and would silently produce a stale initial state if rerun: the resulting "initial" migration would omit `e59f62d5037d` (NULLIF wrapper, Step 2.2a), `4fd3aec6ae0c` (FN-AB-14 OR-clause, Step 2.2b), and `21e2ad16303a` (Step 3.0 OR-clauses on the other 4 policies), plus any future policy/structure migrations. The DDL files are intentionally frozen at the as-shipped initial-schema state per the workflow convention, so the regenerator's output drifts further from live state with every migration that lands.

Resolution options:
- (i) amend the script to refuse to run when post-initial migrations exist (read `alembic history` and bail if more than one revision is reachable from base);
- (ii) add a banner warning in the script header and require an explicit `--i-know-this-is-stale` flag;
- (iii) update DDLs to live state at squash time and accept the manual coordination cost.

Defer to the post-v0 chain-consolidation step that will squash the v0 migration history into a clean baseline. No action in v0; tracking here so the foot-gun is documented before someone reaches for the regenerator out of habit.

### FN-AB-16 — Module entitlements served from a stub (RESOLVED at Step 3.4.5)

Step 3.3 shipped per-tenant module entitlements from a hardcoded Python dict in `src/admin_backend/repositories/_module_entitlements_stub.py` because the underlying `tenant_module_access` table didn't yet exist. An xfail-strict tripwire at `tests/unit/test_module_entitlements.py` asserted the stub file did NOT exist; under `strict=True` an XPASSed test would have failed the run, forcing cleanup pairing.

**Resolved at Step 3.4.5 (migration `cd2a02e452ae`, 2026-05-03).** The DDL for `tenant_module_access` landed (Pattern (a) audit-actors per D-13; D-29 unconditional PLATFORM OR-clause RLS since `tenant_id` is NOT NULL); the migration also seeded six rows in `lookups` for the `module_code` list. The Repo's `list_with_aggregates` and `get_by_id_with_aggregates` methods replaced the stub call with a correlated `jsonb_agg` subquery that JOINs `tenant_module_access` to `lookups` for display-name resolution, ordered by `lookups.display_order`, COALESCE-wrapped to `'[]'::jsonb` for the empty-modules case. The stub file and the tripwire test were deleted in the same commit.

### FN-AB-17 — Cloud SQL extension creation is manual until Step 8.0 lands

Step 4.1 dev bring-up surfaced that `ltree` and `pgcrypto` extensions must be created on Cloud SQL out-of-band (CSD-02). For dev, the operator runs the two `CREATE EXTENSION IF NOT EXISTS` statements once via Cloud SQL Studio as the `postgres` cloudsqlsuperuser. This is acceptable for dev but **must NOT carry into prod**.

**Hard precondition.** Step 8.0 automates extension creation in admin-infra Terraform. Step 8.0 must complete before Step 8.1.1 (production GCP provisioning) starts. Any prod-shaped Terraform apply that requires a manual Cloud SQL Studio step is not acceptable for prod posture.

**Resolution criterion.** FN-AB-17 closes when Step 8.0 ships and a fresh Terraform apply (against a clean GCP project) produces a Cloud SQL instance with `ltree` and `pgcrypto` already created in the application database with no operator action. CSD-02 is then amended (not deleted) to record the historical "manual until Step 8.0" period.

**Why this is FN-AB-17 and not just a TODO.** This entry exists because the failure mode is silent during dev (manual step easy to remember when there's only one env) and loud during prod cutover (someone forgets the manual step on a Sunday-night deploy and a fresh prod DB blocks the migration chain). Forward-notes that name a hard precondition catch this class of regression at planning time, not at 2 AM.

### FN-AB-18 — v0.1.3-seed image is a temporary discipline deviation; reverse at Step 4.4.1 (RESOLVED at Step 4.4.1)

Step 4.3.5 dev seeding required `scripts/seed_dev_data/`, the Excel
fixture `data/ithina_dev_seed_data.xlsx`, and `openpyxl==3.1.5`
(a `dependency-groups.dev` member, pinned to the uv.lock-resolved
version) inside the Cloud Run Job's image. All three are kept out of
v0.1.2 by deliberate Step 4.2 / 4.3 discipline, via three complementary
mechanisms:

- `data/` is excluded by `.dockerignore`.
- `scripts/seed_dev_data/` is kept out by Dockerfile's selective `COPY`
  pattern (only `smoke_test.py` and `verify_cloud_schema.py` are
  individually copied in; the directory is in build context but never
  reaches the runtime layer).
- `openpyxl` is absent because the runtime venv is built with
  `uv sync --frozen --no-dev`.

The discipline holds — dev tooling and fake-company fixture data have
no business in a runtime image — even though the enforcement is split
across three places.

Step 4.3.5 introduced a one-off Dockerfile.seed that extends v0.1.2:
adds `scripts/seed_dev_data/`, `data/ithina_dev_seed_data.xlsx`, and
installs `openpyxl==3.1.5` into the inherited `/opt/venv`. Because
v0.1.2's venv ships no pip (uv sync doesn't install pip by default),
Dockerfile.seed copies the uv 0.5.4 binary from
`ghcr.io/astral-sh/uv:0.5.4` into `/usr/local/bin/uv` and uses
`uv pip install` against the existing venv. As a side effect, the
v0.1.3-seed image carries the uv binary (~28 MB uncompressed) — one
more reason the image is unsuitable for non-seeding use; reinforces
the "temporary deviation" framing.

**Why acceptable for one run.** The image is tagged `v0.1.3-seed`,
not promoted to `:latest`, and is referenced only by the
`admin-backend-seed-dev-data` Cloud Run Job — never by the live
`admin-backend` service (which stays on `v0.1.2` throughout). The
synthetic-PLATFORM AuthContext code path (loader's `model_construct`
bypass of validation) is gated by the production-refusal guard
(refuses when `ENVIRONMENT=production`); the data fixture is
fake-company seed data, no real PII.

**Why this still has to be reversed.** Defense-in-depth says don't
ship dev tooling in any deployable image, even a Job image. The
Dockerfile.seed, the build script, and the v0.1.3-seed tag in
Artifact Registry all need to come out once seeding is verified.
Otherwise the next person needing a one-off Job image inherits a
"just extend the seed image" pattern that drifts further from
discipline.

**Hard precondition.** Step 4.4.1 (Dockerfile.seed teardown) is a
hard precondition of Step 4.5 (Stores resource — next image build).
Step 4.5 will not start until Step 4.4.1 ships. FN-AB-18 closes
when Step 4.4.1 ships.

**Resolution criterion.** Repo no longer contains `Dockerfile.seed`
or `scripts/build_seed_image.sh`. Artifact Registry no longer has
the `v0.1.3-seed` tag (the underlying digest may remain GC-eligible;
tag removal is the load-bearing part — prevents accidental
redeployment). The `admin-backend-seed-dev-data` Cloud Run Job is
either deleted (default) or kept paused as a re-seeding artifact
(operator's call at Step 4.4.1 time).

**The deeper thing this defers.** A proper prod-vs-dev image split
(separate Dockerfile targets, separate registry paths, separate
.dockerignore variants) is the long-term answer. That conversation
happens before the first prod deploy (Step 8.x), not now. This
FN-AB names the gap so it doesn't get forgotten in the run-up to
prod.

**Resolved at Step 4.4.1 (2026-05-04).** The temporary
`Dockerfile.seed` and `scripts/build_seed_image.sh` removed from
repo. Artifact Registry tag `v0.1.3-seed` deleted (digest
GC-eligible). The `admin-backend-seed-dev-data` Cloud Run Job
retained as a paused artifact for potential near-term re-seeding —
tracked as FN-AB-20 for eventual deletion. Image discipline restored
to v0.1.2-era posture. Step 4.5 unblocked.

### FN-AB-19 — SUPER_ADMIN supplementary ADMIN-domain permissions (RESOLVED at Step 6.8.2.1)

The Step 3.5 seed Excel shipped with 23 permission rows (post-Step-6.1 cleanup). Of those, only 1 ADMIN-domain permission targeted SUPER_ADMIN's actual operational scope: ADMIN.USERS.VIEW.TENANT. The seven other ADMIN-domain operations a Super Admin needs to perform (cross-tenant store/org-node visibility and configuration, global-scope user/role configuration) had no rows in the catalogue. SUPER_ADMIN was therefore structurally underprivileged: any future RBAC enforcement layer (Step 6.8) gating ADMIN-domain writes would have rejected the Super Admin's own writes because the permission tuples weren't in the catalogue to grant.

Captured here (numbered FN-AB-19 to fill the previously-skipped slot in the sequence) so the resolution is discoverable. Logically tracked through Step 6.8.3's design conversation; the four ADMIN-domain permissions enumerated in that conversation expanded to seven during operator review (the cross-tenant store/org-node VIEW pair was added because the seed had no per-resource VIEW rows for those tables either, only the resource-aggregate USERS.VIEW.TENANT).

**Resolved at Step 6.8.2.1 (2026-05-09).** Operator manually appended seven rows to the seed Excel's `permissions` sheet (codes ADMIN.STORES.VIEW.TENANT, ADMIN.STORES.CONFIGURE.TENANT, ADMIN.ORG_NODES.VIEW.TENANT, ADMIN.ORG_NODES.CONFIGURE.TENANT, ADMIN.USERS.VIEW.GLOBAL, ADMIN.ROLES.CONFIGURE.GLOBAL, ADMIN.USERS.CONFIGURE.GLOBAL — `_key` p28 through p34) and seven matching rows to `role_permissions` linking each new permission to SUPER_ADMIN (`role_id` `f10c718b-1eb0-438a-a75d-d5af3c365296`). Total catalogue: 23 → 30 permissions, 113 → 120 role_permissions. SUPER_ADMIN now holds every ADMIN-domain permission it operationally needs; the resolver gate at Step 6.8 will not reject SUPER_ADMIN's own writes.

**Hard precondition for Step 6.8 (RBAC enforcement layer).** Without these grants the resolver becomes a structural-deadlock once it gates ADMIN-domain writes — the most-privileged role wouldn't have permissions to do its job. Tracked here so the precondition is named, not implicit.

### FN-AB-20 — admin-backend-seed-dev-data Cloud Run Job retained, pending deletion

Step 4.4.1 left the Job in place at operator request to preserve
flexibility for near-term re-seeding. Job consumes zero compute
(Cloud Run Jobs run only on explicit invocation). Its frozen
reference to v0.1.3-seed digest is harmless until Artifact Registry
GCs the digest, at which point execution would fail with "image
not found" — clear failure mode.

**Resolution.** Delete via:

    gcloud run jobs delete admin-backend-seed-dev-data \
      --region=asia-south1 --project=ithina-retail-admin --quiet

No code or doc state to clean up. Resolves when Job no longer in
project's Cloud Run Jobs list.

### FN-AB-21 — /api/v1/tenants/stats endpoint posture (RESOLVED at Step 6.5)

The `/api/v1/tenants/stats` endpoint (Step 3.3 tenants router) was
multi-user-type from inception. RLS-scoped aggregate behavior:

- **PLATFORM JWT** → `{total_tenants: 7, total_stores: 25}` (all tenants
  via D-29 OR-clause).
- **TENANT JWT** → `{total_tenants: 1, total_stores: <own count>}`
  (RLS-scoped to caller's tenant — always 1 tenant, store count varies).

This is RLS-correct, but the URL name implies platform-wide aggregation,
which is misleading for TENANT users.

**Resolved at Step 6.5 (2026-05-06): Option 2 — multi-user-type.** The
endpoint stays at its Step 3.3 contract; no code changes. The
resolution is documentation policy: scope-dependent semantics are
acceptable, and the URL's apparent "platform-wide" implication is
documented as caller-scope-relative ("your scope's stats" for TENANT
users). Step 6.5's two new dashboard endpoints
(`/api/v1/dashboard/fleet-stats` and `/api/v1/dashboard/governance-stats`)
follow the same multi-user-type + RLS-driven scoping pattern, locking
this in as the **platform-wide default for stats endpoints**.

The original three options were:

1. Lock to PLATFORM-only — rejected; would force a TENANT-scope
   counterpart endpoint.
2. **Keep multi-user-type — chosen.** RLS-uniform with the rest of the
   router; one URL, persona-correct values via session GUCs.
3. Split into two endpoints — rejected as overengineering; the
   per-tenant case is structurally a 1-row response.

**Why this works.** The dashboard's KPI grid mirrors the same shape
across PLATFORM and TENANT personas — same cards, same field types,
same field set. RLS does the persona projection at the DB layer; the
`sub_text` strings are scope-aware via `auth.user_type` at the
application layer. A future Tenant Owner dashboard reuses these
endpoints unchanged; frontend hides degenerate cards
(e.g., `Active tenants 1/1`).

The original `/tenants/stats` payload (`{total_tenants, total_stores}`)
remains untouched — it's a lighter-weight 2-scalar shape used by the
tenants router header summary, distinct from the dashboard's KPI
grid. Both coexist.

---
### FN-AB-22 — Auth0 scope expansion: admin-backend as platform auth gate

**Note.** Stage 3 entry is currently scoped to a vanilla Auth0 swap (Step 8.3), replacing the stub-auth dependency. However, a broader scope direction is under consideration: admin-backend becomes the sole owner and gate of authentication for the whole platform (not just the admin surface). Under this framing, other platform services would validate Auth0 tokens via admin-backend rather than directly, making admin-backend the central trust anchor.

**Why flagged, not decided.** The downstream implications have not been worked out — token-validation latency, JWKS caching strategy, multi-service trust shape, whether `AuthContext` grows or stays minimal, how the PLATFORM-vs-TENANT split interacts with cross-service token validation, and whether D-24's "identity-only JWT" posture
survives unchanged. Settling these belongs at Stage 3 kickoff, not now.

**Affects.** D-07 (Auth0 ownership), D-24 (JWT identity-only), D-26 (RS256 via pyjwt[crypto]), Stage 3 scope,and the architecture.md Authorisation section rewrite landed alongside Section 6.9 close (replaced the post-Stage-2 stub with the RBAC enforcement subsection + pointer to architecture_RBAC.md).

**Resolution.** Expected at Stage 3 kickoff. Will be resolved by a D-XX entry that either confirms the expanded scope or explicitly declines it.

### FN-AB-23 — Impersonation read-only enforcement (during PLATFORM-impersonating-TENANT sessions)

When impersonation ships as a v0 feature, the gate must enforce "a PLATFORM user impersonating a tenant cannot perform write actions during the impersonation regardless of their PLATFORM grants." Today the AuthContext validator's permissive PLATFORM branch (D-24 / Step 2.1) accepts `user_type='PLATFORM'` with non-NULL `tenant_id` as the impersonation shape, but `has_permission()` (Step 6.9.1) treats this purely as "PLATFORM user" and ignores `target_anchor`. Two candidate mechanisms when the feature lands: (a) resolver-level — AuthContext carries an explicit `is_impersonating` flag (or equivalent) that `has_permission()` reads to deny write actions; (b) gate-level — a separate FastAPI dependency intercepts write actions during impersonation and denies before `has_permission()` runs. Decision deferred to impersonation feature design.

### FN-AB-24 — `has_permission()` caching

`has_permission()` runs one SQL query per gate check in v0. At v0 scale (low thousands of users, low traffic) measured plans are sub-millisecond per check (Step 6.9.1 EXPLAIN ANALYZE: PLATFORM 0.169 ms, TENANT 0.196 ms on seeded data); per-request DB load is acceptable. At larger scale, caching points to consider: (a) per-request memoisation when one endpoint checks the same permission tuple multiple times; (b) per-user short-TTL cache keyed on the full `(user_id, module, resource, action, scope, target_anchor)` tuple at the cost of permission-change latency. Revisit when monitoring shows `has_permission` as a measured hot path.

### FN-AB-25 — `target_anchor` resolution pattern (DECISION LOCKED at Step 6.9.2; implementation at Step 6.9.3)

Step 6.9.2's FastAPI gate dependency needs to produce a `target_anchor` ltree string from each request (typically: extract path params → look up the relevant `org_node` → read `path`). Three patterns to choose between: (a) a single universal dependency that knows every endpoint's anchor shape (god-dependency risk); (b) per-endpoint anchor dependencies (more code, less coupling); (c) inline computation inside each handler (defeats the declarative gate pattern).

**Decision locked at Step 6.9.2 (2026-05-13): pattern (b) per-endpoint anchor dependencies.** Each retrofitted endpoint declares its own `Depends(get_<resource>_anchor)` alongside `Depends(require(...))`. The `require(...)` factory in 6.9.2 ships with `target_anchor=None` hardcoded internally; the threading mechanism (composing the gate's `has_permission` call with the anchor dependency's result) ships in Step 6.9.3 as part of the retrofit. Per-resource anchor functions (`get_store_anchor`, `get_tenant_anchor`, `get_org_node_anchor`) all land in 6.9.3.

### FN-AB-26 — `_require_platform_auth` retirement decision (RESOLVED at Step 6.9.3.2)

**Resolved:** Step 6.9.3.2 picked option (a). `_require_platform_auth` was removed from `routers/v1/platform_users.py`; its two call sites (`list_platform_users`, `get_platform_user`) now use `Depends(require(ADMIN, USERS, VIEW, GLOBAL))`. `PlatformAccessRequiredError` retired alongside (was dead code after the swap). The wire-contract change is the error code on TENANT-JWT denial: `PLATFORM_ACCESS_REQUIRED` (412) → `PERMISSION_DENIED` (still 403). All paired tests + workflow scripts updated.

Historical context preserved below.

The `_require_platform_auth` helper at `src/admin_backend/routers/v1/platform_users.py:102-109` (the binary `auth.user_type != "PLATFORM"` check) predates Step 6.9.2's `require(...)` gate factory. Retirement options for Step 6.9.3.2 retrofit: (a) replace every `_require_platform_auth(auth)` call with `Depends(require(MODULE, RESOURCE, ACTION, SCOPE))` against the appropriate PLATFORM-scope permission; (b) keep `_require_platform_auth` as the cheap user-type-only fast path for endpoints that gate purely on `user_type=PLATFORM` and don't need a specific permission tuple. The original helper's rationale — `platform_users` table has no RLS, so app-layer auth must enforce — is in CLAUDE.md's "Note on the v0 auth model". Decision deferred to Step 6.9.3.2 design conversation.

Post-Step-6.9.3.1 context update: with scope cascade in `has_permission`, a PLATFORM user holding `ADMIN.USERS.VIEW.GLOBAL` automatically satisfies any narrower-scope check on the same tuple. Replacement option (a) is mechanically simpler than it was at 6.9.2 — `Depends(require(ADMIN, USERS, VIEW, GLOBAL))` is a single tuple and cascade handles any frontend-side variant. The audience-check at the DB layer plus the gate's PLATFORM path together still produce the same 403-on-TENANT-JWT shape; only the error `code` changes (`PLATFORM_ACCESS_REQUIRED` → `PERMISSION_DENIED`).

### FN-AB-27 — `/me/permissions` response shape simplification

Current shape (Step 6.9.2) returns structured `PermissionGrant` items: `{module, resource, action, scope, anchor_path}`. Frontend integration during Step 6.9.3.2 retrofit may surface that the structured shape requires significant client-side reconstruction logic. If so, consider simplifying to pre-joined string codes (`{"permissions": ["ADMIN.USERS.VIEW.TENANT@bucees", ...]}`) and dropping the nested-object shape. The simplification is a breaking change to the wire shape; revisit during Step 6.9.3.2 retrofit only if concrete frontend integration friction surfaces. D-31 (append-only response shapes) means a non-breaking path would add a sibling `permission_codes: list[str]` field rather than replacing the existing array.

### FN-AB-28 — `PermissionScope` enum expansion (future)

Current v0 `permission_scope_enum` has 3 values (`GLOBAL`, `TENANT`, `STORE`). The full org-tree hierarchy supports 8 levels (per `_SCOPE_CASCADE_ORDER` in `src/admin_backend/auth/permissions.py`); the missing 5 (`BUSINESS_UNIT`, `HQ`, `COUNTRY`, `REGION`, `DEPARTMENT`) are forward-compat placeholders.

Expansion requires:
1. Alembic migration adding the new value(s) to `permission_scope_enum` (Postgres `ALTER TYPE ... ADD VALUE`).
2. Matching update to `PermissionScope` (Python `Enum`) in `src/admin_backend/models/permission.py`.
3. Catalogue rows in `data/ithina_dev_seed_data.xlsx` for resources that should support the new scope; matching role grants.

No change needed to `satisfying_scopes()` or `_SCOPE_CASCADE_ORDER` — both already encode all 8 levels. The unit test `test_scope_cascade_order_includes_all_enum_values` will pass automatically; the SQL-level filter `_satisfying_scopes_for_sql` picks up the new enum value via `_PERMISSION_SCOPE_ENUM_VALUES = frozenset(s.value for s in PermissionScope)`.

REGION is the likely first addition — the frontend admin surface references region-level scoping in the Markdowns/Approve flows.

### FN-AB-29 — Dashboard + module-access dedicated permission tuples (Phase 3b seed update)

Step 6.9.3.2 gated `/dashboard/fleet-stats`, `/dashboard/governance-stats`, `/module-access/modules`, and `/module-access/matrix` on `ADMIN.TENANTS.VIEW.TENANT`. SUPER_ADMIN passes via the GLOBAL→TENANT cascade. Post-Phase-3 (commit `6c92661`), OWNER holds the proxy tuple `ADMIN.TENANTS.VIEW.TENANT` directly via the operator-applied seed update; the 10 xfail tests that documented the pre-Phase-3 gap (`test_s2`, `s5`, `s6`, `o2`, `o4`, `o5`, `o6` in `test_dashboard_router.py`; `test_m2`, `m5`, `x1` in `test_modules_access_router.py`) were migrated to `tenant_owner_jwt_factory` and unxfailed in the 6.9.3.2 cleanup commit.

**Open design question** (forward):

**Single shared tuple or dedicated tuples per UI page?** The current posture reuses `ADMIN.TENANTS.VIEW.TENANT` for both `/dashboard/*` and `/module-access/*`. Operationally fine (one grant = whole tenant-side admin surface) but loses the future option of segmenting dashboard visibility from module-access visibility separately. Dedicated tuples (`ADMIN.DASHBOARD.VIEW.TENANT`, `ADMIN.MODULE_ACCESS.VIEW.TENANT`) would require new `resource_enum` values plus catalogue rows + grants + endpoint gate updates.

Resolution criterion: explicit operator decision when a use case surfaces that requires segmented visibility (e.g., a tenant role meant to see modules but not the dashboard KPI grid, or vice versa). Until then, the shared-tuple posture is the locked v0 default.

### FN-AB-30 — Reference-data + roles-catalogue endpoint gating

The following 4 endpoints are currently in `GATE_EXEMPT_PATHS` (`auth/gate_allowlist.py`): `/lookups`, `/permissions`, `/permission-matrix`, `/roles`, `/roles/{role_id}/permissions`. Rationale at Step 6.9.3.2: every authenticated caller (PLATFORM + TENANT) needs these to render the admin UI's dropdowns and roles-catalogue, and no per-permission segmentation has been requested. The allowlist is the documented mechanism for not-yet-gated endpoints (the meta-test `test_gate_discipline` would flag any new route that doesn't either gate or land here).

Forward question: are these reference-data endpoints actually safe to keep open to all TENANT JWTs, or do they leak Ithina-internal taxonomy (lookup categories, role names) that should require an ADMIN-domain grant? Two postures:

(a) Keep allowlisted. Reference data IS public-shaped for all authenticated callers; no leak concern.
(b) Gate behind a low-friction tuple like `ADMIN.SYSTEM.VIEW.GLOBAL`. Adds catalogue rows + ubiquitous grants; one more gate hop per dropdown render.

Resolution criterion: explicit operator decision at Stage 2 close or Phase 3 prep. Until then, posture (a) holds via the allowlist; any new reference-data endpoint follows the same pattern.

### FN-AB-31 — `/role-assignments` dedicated permission tuple

`/api/v1/role-assignments` (Step 6.8.3) was gated at Step 6.9.3.2 on `ADMIN.USERS.VIEW.TENANT` — the closest existing tuple given the endpoint's "view who has what role" semantics, per the Phase 1i locked design decision. PLATFORM callers pass via the GLOBAL→TENANT cascade (SUPER_ADMIN holds `.GLOBAL`; PLATFORM_ADMIN / SUPPORT_ADMIN hold `.GLOBAL` via FN-AB-19 supplementary grants). TENANT callers need the direct `.TENANT` grant; OWNER has it in the seed catalogue (originally from Step 3.5). Existing TENANT-side tests use `tenant_owner_jwt_factory(tenant_id)` with the factory's default `with_grants=[("ADMIN", "USERS", "VIEW", "TENANT")]` for synthetic-grant test coverage.

Forward question: should the endpoint have its own tuple like `ADMIN.ROLE_ASSIGNMENTS.VIEW.GLOBAL` and `.TENANT`? Adds a level of specificity (separating "list users" from "list role assignments" in the catalogue). Alternative: keep the current shared `ADMIN.USERS.VIEW.*` tuple and live with the implication that "viewing users" and "viewing role assignments" share a permission gate.

Resolution criterion: explicit operator decision at Stage 2 close or Phase 3 prep. The current shared-tuple posture is the locked v0 default; revisit if a Phase 3 use case surfaces a need to grant role-assignment visibility separately from user visibility.

### FN-AB-32 — PLATFORM_ADMIN / SUPPORT_ADMIN ADMIN.USERS.VIEW.GLOBAL coverage

Step 6.9.3.2 surfaced (via gate-discipline analysis) that the seed grants PLATFORM_ADMIN and SUPPORT_ADMIN `ADMIN.USERS.VIEW.GLOBAL` exactly to cover the existing platform-side "list users" UI. The current grants overlap heavily with SUPER_ADMIN; the more nuanced "PLATFORM_ADMIN reads, doesn't configure" intent vs SUPPORT_ADMIN's read-mostly-impersonate posture isn't reflected in distinct permission tuples. The catalogue currently doesn't carry an `ADMIN.USERS.VIEW.PLATFORM` or `ADMIN.PLATFORM_USERS.VIEW.GLOBAL` differentiation that would let the seed grant these roles separately from SUPER_ADMIN's full surface.

Resolution criterion: when role-design clarity is needed for the platform-admin-side UI (a Stage-2 ask if PLATFORM_ADMIN's UI differs from SUPER_ADMIN's), revisit catalogue + grants. Not urgent for v0 cutover.

### FN-AB-33 — `PlatformAccessRequiredError` final removal

Step 6.9.3.2 retired `_require_platform_auth` and replaced its 2 call sites with `Depends(require(...))`. `PlatformAccessRequiredError` (defined in `errors.py`) is no longer imported or raised anywhere. It remains in the file at HEAD as documented dead code (commented "kept as dead code post Step 6.9.3.2 retirement; remove at next cleanup pass") for one-release deprecation grace.

Resolution criterion: next cleanup pass (any commit touching `errors.py` for other reasons after Step 7.x lands, or explicit operator cleanup ask). Remove the class definition; verify no string references remain in `scripts/`, `docs/`, or tests; commit alongside other cleanup. Wire contract: code already changed to `PERMISSION_DENIED`; class removal is internal-only.

### FN-AB-34 — Seed loader column semantics inconsistency + validation hook

The seed loader at `scripts/seed_dev_data/` treats Excel columns inconsistently: `id` is IGNORED (UUIDs regenerated server-side via DEFAULT `uuidv7()` per D-21) while `code` (on `permissions`) is HONORED VERBATIM. The inconsistency isn't documented, and there is no load-time check that `code` matches its tuple. Step 6.9.3.2 cleanup surfaced the gap: the Phase 3 seed update originally shipped a typo where `permissions.code = 'ADMIN.TENANTS.VIEW.TENANTS'` (plural) for a row whose tuple is `(ADMIN, TENANTS, VIEW, TENANT)` (singular scope). Runtime code (`has_permission`, `require`, post-cleanup `tenant_owner_jwt_factory`) is robust because it uses tuple identity; the typo was purely cosmetic in DB rows and OpenAPI label rendering — but a code-based lookup elsewhere would fail silently. Operator corrected the Excel typo to `ADMIN.TENANTS.VIEW.TENANT` (singular) in the same commit as the cleanup; local DB and Cloud SQL retain the original typo in the `code` column until the next seed reload picks up the corrected Excel.

Action items:
- Document seed loader column semantics (per-sheet): which columns are authoritative at insert, which are regenerated, which are advisory.
- Add a seed-loader validation step. For `permissions`: assert `excel.code == f"{module}.{resource}.{action}.{scope}"` per row and fail the seed load on mismatch. Same shape for any future table where `code` is a denormalised display column derived from other fields.
- Decide whether to keep `code` in Excel (with strict validation) or derive server-side and drop the column from Excel entirely. The latter eliminates the drift class.

Resolution criterion: load-time validation lands as part of the seed-loader cleanup pass (no fixed step yet; revisit when next touching `scripts/seed_dev_data/`). The Excel-side typo is already corrected in HEAD; once the validation hook ships, the next seed reload picks up the corrected code with no Excel edits required.

### FN-AB-35 — Tenant name UNIQUE constraint (added Step 6.11.2)

`core.tenants.name` has no UNIQUE constraint at the schema layer. Step 6.11 enforces uniqueness via the app-layer pattern: a `SELECT 1 FROM tenants WHERE name = :name LIMIT 1` pre-check inside `TenantsRepo.create` and `TenantsRepo.update` (the latter excludes the row's own id so rename-to-self is a no-op success). Both runs in the same request transaction as the subsequent INSERT/UPDATE. Race window non-zero under concurrent writers — two parallel `POST /api/v1/tenants` calls with the same `name` can both pass the pre-check and both INSERT.

Resolution: add `CONSTRAINT uq_tenants_name UNIQUE (name)` (or a case-insensitive `LOWER(name)` expression index — same shape as `uq_tenants_display_code_lower`) via an additive Alembic migration. Roughly 30 minutes of work; no data backfill required since the seed and Step 6.11.2 smoke / write-tests are name-unique by construction.

When the UNIQUE constraint lands the pre-check stays in place (gives a domain-shaped 409 response with `code=DUPLICATE_TENANT_NAME` rather than relying on the integrity-error → 500 path). The Repo's pre-check query then benefits from the unique index — see FN-AB-36.

### FN-AB-36 — Tenant name uniqueness pre-check query plan (added Step 6.11.2)

The pre-check `SELECT 1 FROM tenants WHERE name = :name LIMIT 1` does a Seq Scan today — no index on `tenants.name` exists. At seed scale (7 rows) EXPLAIN ANALYZE shows ~0.06 ms execution time (Step 6.11.2 verification). Acceptable indefinitely while the tenants table stays small.

Resolution: once FN-AB-35 lands the UNIQUE constraint, the same query uses the unique index and the plan shifts to Index Scan. No code change required.

Revisit if: tenants table grows beyond ~1k rows AND name pre-check shows up as a measured hot path in profiling. Otherwise inert.

### FN-AB-37 — Multi-audience PATCH on tenants (TENANT OWNER edits) (added Step 6.11.2)

`PATCH /api/v1/tenants/{tenant_id}` is platform-only in Step 6.11 (`audience="PLATFORM"`). Product intent of letting a TENANT OWNER edit their own tenant's operational fields (contact_email, primary_contact_name, monthly_revenue_usd, number_of_stores, etc.) is blocked at the schema layer:

`core.tenants.{created,updated,suspended,terminated}_by_user_id` are typed FKs to `platform_users(id)` (Pattern (a) per D-13). A TENANT OWNER's `auth.user_id` is a `tenant_users.id`, not a `platform_users.id`. The UPDATE would fail the FK on `updated_by_user_id`.

Resolution requires migrating those 4 columns to Pattern (b) (UUID + `actor_user_type_enum`, no FK). The catalogue tuple `ADMIN.TENANTS.CONFIGURE.TENANT` is already in the seed (Phase 3 update); the OWNER role grant of that tuple was added at the same time. Once the migration lands, multi-audience PATCH needs:

1. Schema migration: drop the four `platform_users` FKs, add `*_by_user_type` enum columns to the existing audit-actor columns; backfill existing rows with `PLATFORM` since every current writer was a platform user.
2. `TenantPatchRequest` field allowlist split: TENANT callers get a narrower set (e.g., contact_email, primary_contact_name, monthly_revenue pair, number_of_stores pair) — operational fields only. PLATFORM callers retain the full superset.
3. Handler `audience` kwarg drop on PATCH; replace with per-field allowlist check after Pydantic-layer routing on `auth.user_type`.
4. Tests + smoke entries for TENANT-side patch happy + cross-tenant 404.

Defer to a post-6.16 step. Step 6.16 (audit-log emission to `core.audit_logs`) ships the same Pattern (b) migration concern for other write tables (its audit triggers can write the actor_user_type column structurally rather than relying on the trigger's caller having platform-only FK shape) and is the natural bundle point. Estimated ~1 day when triggered.

### FN-AB-38 — Cancel-invitation for tenant_users (deferred from 6.10.1)

The Step 6.10.1 write surface deliberately omits a cancel-invitation endpoint. An INVITED user (`auth0_sub IS NULL`, `invitation_accepted_at IS NULL`) currently has no clean path off the row: the suspend transition is structurally rejected by `ck_tenant_users_auth0_sub_consistency`; PATCH can rename / re-role but not retire; hard delete is not in v0 (no DELETE endpoints).

Two implementation options, both rejected at 6.10.1 design time:

(a) **Column-based cancellation.** Additive Alembic migration adding `cancelled_at TIMESTAMPTZ`, `cancelled_by_user_id UUID`, `cancelled_by_user_type actor_user_type_enum`; a partial-unique-index rewrite on email + auth0_sub so a cancelled invitation can be re-issued; a new endpoint `POST /api/v1/tenant-users/{id}/cancel-invitation`. Most surgical fix, but requires schema work the v0 product hasn't asked for yet.

(b) **Email-mangling workaround.** Update the cancelled row's email to a unique sentinel (`<original>+cancelled-<uuid>@...`) and rely on PATCH to free the original email for re-invite. Avoids schema change but pollutes the row's email history irreversibly.

Resolution criterion: either a v0 deferred-cleanup pass bundles the column-based DDL migration with similar soft-delete additions on other tables, OR product/UX surfaces a hard requirement to cancel invitations not accepted within N days. Tracked as BUILD_PLAN.md Step 6.10.3.

### FN-AB-39 — Auth0 invite-accept flow (INVITED -> ACTIVE)

Step 6.10.1 leaves INVITED -> ACTIVE as the Auth0 invite-accept callback path (out of v0 scope; Stage 3 territory per BUILD_PLAN.md). The explicit `/activate` endpoint refuses to take that transition (returns 409 `INVALID_STATE_TRANSITION`) so the v0 contract stays uniform with the suspend matrix.

When Stage 3 lands, the callback path must populate `auth0_sub` AND `invitation_accepted_at` atomically with the status flip (per `ck_tenant_users_auth0_sub_consistency` + `ck_tenant_users_invitation_accepted_consistency` — both columns are CHECK-bound to status). Likely shape: a dedicated `/auth0/callbacks/invite-accepted` endpoint that calls a new Repo method `accept_invitation(user_id, auth0_sub)` — distinct from `transition()`, since the column writes are different.

Resolution at Stage 3 Auth0 integration.

### FN-AB-40 — Email-change Auth0 reconciliation

Step 6.10.1's PATCH allows email change on a tenant_user. Under stub auth (D-07), this is a pure DB-side write: the JWT carries identity claims only (per D-24); email changes don't affect the JWT.

Under real Auth0 (Stage 3), the user's email is also stored in Auth0's user-management database. A PATCH that changes only the backend's `tenant_users.email` would leave the Auth0 record stale; the next login would surface the Auth0 email, the backend would re-read by the unchanged `auth0_sub`, and the two would drift.

Resolution at Stage 3 Auth0 integration. Likely shape: PATCH /tenant-users with `email` change becomes a 2-step transaction — Auth0 Management API call first (idempotent email-change PATCH on the matching `auth0_sub`), then backend DB write. Failure modes (Auth0 down, mismatched ownership, etc.) need design.

### FN-AB-41 — Anchored role bundling at create (RESOLVED at Step 6.14)

Step 6.10.1 anchored ALL bundled roles at the tenant root org_node (locked decision 4). A user granted a TENANT-audience role had that role active across the entire tenant tree via the anchor cascade. Discriminated-union shape was speculated.

**Resolved at Step 6.14 (2026-05-16).** `roles` body shape on POST + PATCH `/tenant-users` flips from `list[UUID]` to `list[{role_id, org_node_id}]` (pure object shape; the speculated discriminated union with bare-UUID fallback was rejected in favor of uniform pair shape — simpler and safer at the wire). Tenant-root-only anchoring retired; any non-archived org_node in the same tenant is acceptable. The repo's whole-set-replace path is retired in favor of diff-replace (LD3): unchanged `(role_id, org_node_id)` tuples in (current ∩ desired) preserve their original `granted_at` and `granted_by_*`. Composite-FK invariant per D-34 still applies; structurally enforced.

Three new ClientError subclasses added at this step (`InvalidOrgNodeError` 422, `DuplicateRoleAssignmentInRequestError` 422, `RoleAssignmentConflictError` 409); resolution-fix detail in `docs/implementation-steps/step-6_14-role-assignment-writes-2026-05-16.md`.

### FN-AB-45 — Cross-step behavioral shift: 6.10.1 whole-set vs 6.14 diff-replace

Pre-Step-6.14 PATCH `/tenant-users` was whole-set replace: every existing ACTIVE assignment went INACTIVE on PATCH and the desired set INSERTed as new ACTIVE rows. Post-Step-6.14 PATCH is diff-replace: only `(current − desired)` flips INACTIVE; only `(desired − current)` INSERTs.

**Why this matters.** When Step 6.16 audit-log emission lands, the row counts for equivalent logical edits will differ across the 6.14 cutover. A "rename user, keep same roles" PATCH that produced N revoke + N insert rows pre-6.14 produces zero role-assignment rows post-6.14. This is the right product behavior — `granted_at` retains its "when this grant first happened" semantics — but downstream analysis pre/post the cutover should be aware.

**How to apply.** Audit-log analysis spanning the 6.14 cutover should bucket on commit date and not draw inferences from raw row-count deltas alone. Step 6.16's audit-log emission design should call out the cutover explicitly.

**Resolution criterion.** Documentation only; no code change needed. Closes when Step 6.16 ships its audit-log doc with the cutover note.

### FN-AB-46 — conftest.py make_tenant mixed-style (ORM main, raw text() with_root branch)

`tests/integration/conftest.py::make_tenant` was promoted to support `with_root: bool = False` at commit `485d123` (Step 6.15 retro). The main fixture path uses ORM (`session.add()` + `flush` + `refresh`); the `with_root=True` branch uses raw `text()` SQL for the `org_node` insert, mirroring `make_org_node`'s established raw-SQL pattern.

The asymmetry is deliberate: introducing a new ORM mapping path for the OrgNode insert was out of scope for the workflow-only commit at `485d123`. Promoting the OrgNode insert to ORM uniformly across the fixture set is the cleaner long-term shape.

Future trigger to revisit: any next step needing another auxiliary root-style insert (e.g., a fixture variant for nested org-node depth, or a sibling fixture for similar parent-anchored entity creation). At that point, promote the OrgNode insert path to ORM uniformly across `make_tenant`, `make_org_node`, and any future fixture sharing the pattern.

Resolution criterion: arrival of the second use case OR a dedicated test-fixture-cleanup commit. Not urgent.

### FN-AB-42 — Cross-resource transition-matrix asymmetry (modules idempotent-200 vs tenants 409)

Step 6.15 ships `POST /api/v1/module-access/{tenant_id}/{module_code}/enable` and `.../disable` with idempotent-200 semantics on no-op cells: enable on already-`ENABLED` returns 200 with no row mutation; disable on already-`DISABLED` returns 200 with no row mutation; disable on missing returns 404 `MODULE_ACCESS_NOT_FOUND`. By contrast, Step 6.11.2's tenant `/suspend` and `/activate` endpoints — same gate tuple `ADMIN.TENANTS.OVERRIDE.GLOBAL`, same audience pattern — return 409 `INVALID_STATE_TRANSITION` on no-op cells.

The two surfaces deliberately diverge. Modules toggle freely (commercial/operational concern; the audit-trail differentiation between "first enable" and "re-enable after disable" is captured via `enabled_at` overwrite per LD5). Tenants do not: a tenant lifecycle state is a contract-bearing fact, and silently accepting `SUSPENDED -> SUSPENDED` would mask a stuck integration or duplicate suspend signal. The 409 is informational.

The asymmetry concrete-bites when Step 6.16 audit-log emission lands: the same OVERRIDE action category produces a structured audit entry on tenants suspend/activate (whether successful, 409, or 404) but only on modules enable/disable for the path that actually mutated a row (no-op cells produce no audit entry). The billing-mutation cost-of-evidence trade-off ("is the no-op auditable?") differs between resources because the underlying business intent does.

Resolution criterion: revisit at **Step 6.16** when audit-log emission surfaces the billing-mutation audit-trail differences concretely. Two paths are open: (a) unify on idempotent-200 across both resources and lean on the audit trail to record no-op intent; (b) keep the asymmetry as canonical and document each resource's posture per write step. Decision deferred to 6.16 design.

**RESOLVED 2026-05-21 (Step 6.16.5).** Path (b) chosen with the audit posture: the asymmetry is canonical (modules toggle freely; tenants are contract-bearing). Step 6.16.5 LD2 explicitly emits ZERO audit rows on module-access no-op idempotent paths (enable-on-ENABLED, disable-on-DISABLED). This refines 6.16.4's "one row per HTTP request" invariant to "at most one row per HTTP request" — emission is conditional on the request causing a state change or a failure outcome. Tenants suspend/activate continues to 409 INVALID_STATE_TRANSITION on no-op cells (mid-state assertion, not silent acceptance). Documented in `docs/architecture_audit_logs.md` Emission contract section and via the MS3/MS5 LOAD-BEARING integration tests.

### FN-AB-43 — Module-access schema evolution under billing/payments

The `tenant_module_access` shape is operational-only at v0: per-tenant per-module ENABLED/DISABLED with audit-actor columns. It captures who flipped what when, but not the financial side: which modules generate revenue, which are in trial vs paid status, when the billing period started or ended for a given module, who approved a discount.

A billing/payments integration step (Stage 3 or post-v0) will likely need to extend the shape with: a billing-status column distinct from the access status (e.g., `trial`, `paid`, `grace`, `terminated`); a `billing_started_at`/`billing_ended_at` pair tied to module access changes; a price-plan FK referencing a pricing catalogue not yet in v0; perhaps a sibling `tenant_module_billing_events` table for granular billing-audit.

Resolution criterion: a Stage 3 or post-v0 billing-integration design step explicitly opens this question. v0 deliberately punts; the upsert + LD5 overwrite semantics at Step 6.15 leave room for a billing-status column to be added without changing existing endpoint contracts (D-31 append-only field semantics).

### FN-AB-44 — ROOS in module_code_enum but not in ModuleCode Python enum

`core.module_code_enum` ships 6 values; `ModuleCode` (Python, retired ROOS 2026-05-12) ships 5. Any pre-existing `tenant_module_access` row with `module='ROOS'` is unmanageable via the new 6.15 write endpoints (POST /enable/disable returns 422 from FastAPI path-param binding), and is invisible to the 6.7 read endpoints. Acceptable for v0 (seed has no ROOS rows). Resolution: when the next module_code lifecycle change lands (add or remove), bundle an Alembic migration that retires ROOS from `module_code_enum` via the standard PG enum-remove pattern (rename old enum, create new enum, ALTER TABLE USING cast, drop old enum). No urgency; pre-existing drift since 2026-05-12.

### FN-AB-47 — ORG_NODES grants catalogue gap + cloud deferral (Step 6.13)

Step 6.13 surfaced two catalogue gaps at pre-flight: OWNER had `ADMIN.ORG_NODES.VIEW.TENANT` but no `CONFIGURE.TENANT`; PLATFORM_ADMIN had no `ADMIN.ORG_NODES.*` grants of any kind, so the locked LD2 multi-audience design (GLOBAL->TENANT cascade for SUPER_ADMIN + PLATFORM_ADMIN, direct .TENANT for OWNER) had nothing to resolve through for PLATFORM_ADMIN.

**Local resolution (2026-05-16).** Operator applied a Phase 3b seed update (Excel edit + `python -m scripts.seed_dev_data --reset`) before implementation began:

- +2 permission rows: `ADMIN.ORG_NODES.CONFIGURE.GLOBAL`, `ADMIN.ORG_NODES.VIEW.GLOBAL`.
- +5 role_permissions rows: SUPER_ADMIN -> both new GLOBAL tuples; PLATFORM_ADMIN -> both new GLOBAL tuples; OWNER -> `ADMIN.ORG_NODES.CONFIGURE.TENANT`.

Local catalogue moved 31 -> 33 permissions and 122 -> 127 role_permissions. `EXPECTED_VISIBLE_COUNTS_PLATFORM` in `tests/integration/test_seed_loader.py` updated accordingly. Two new integration tests (PA1, PA2 in `tests/integration/test_org_tree_writes_router.py`) lock the PLATFORM_ADMIN cascade resolution end-to-end via the seeded Devon user.

**Cloud deferral.** Cloud Postgres is still at 31 permissions / 122 role_permissions as of this commit. The Step 6.13 cloud deploy (next Phase 6 cycle) must apply the same catalogue update before cloud test_endpoints.sh Phase 4e entries (`ot_flow__*` OWNER + PLATFORM_ADMIN paths) will pass. SUPER_ADMIN happy paths and TENANT-no-grant denies are reliable in the interim.

**Resolution criterion.** Closes when cloud DB carries +2 permissions and +5 role_permissions matching the local catalogue, AND `scripts/test_endpoints_cloud.sh` Phase 4e passes against cloud. Paralleling Step 6.8.2.1's FN-AB-19 (same operator-driven seed-update pattern with one-deployment-cycle gap).

### FN-AB-48 — Seed loader does not strip whitespace on Excel cells

The Excel-driven seed loader passes cell values through unchanged. A trailing space in an enum-typed cell (e.g., `'VIEW '` instead of `'VIEW'`) causes `psycopg.errors.InvalidTextRepresentation` at INSERT time. Surfaced at Step 6.17.1 when adding new permission rows manually.

**How to apply.** Until the loader is hardened, manually verify enum-typed cells for trailing/leading whitespace before reseed. The loader could add `.strip()` to all cell reads as a defensive fix.

**Resolution criterion.** Either: (a) loader gains `strip()` on all string cell reads (one-line PR), or (b) the convention is to verify whitespace post-edit and the FN-AB stays open as a procedural warning. Either is acceptable; pick at the next loader touch-point. Deferred from 6.17.1 to 6.17.2 per the impl prompt.

### FN-AB-49 — `core.lookups` is dual-sourced (migration + SQL delta, not Excel)

The `core.lookups` table is seeded from two sources today: 17 rows from the Step 3.6 Alembic migration (tenant_tier, tenant_region, tenant_status, tenant_industry) and 6 rows from Step 6.17.1's inline SQL delta (store_status, tax_treatment). Excel does NOT have a `lookups` sheet. `seed_dev_data --reset` does not truncate `core.lookups`, so the SQL-managed rows survive reseeds.

**How to apply.** Future lookup additions should follow the same pattern: inline SQL UPSERT on both local and Cloud SQL, idempotent, no Excel sheet. If a Stage 3 or later step demands lookup-table-as-Excel parity, that's a dedicated migration step, not a side effect of another step.

**Resolution criterion.** Documentation only; no code change needed. Closes if/when a dedicated `lookups`-to-Excel migration step ships and Excel becomes authoritative. Deferred from 6.17.1 to 6.17.2 per the impl prompt.

### FN-AB-50 — `core.stores.store_code` + `tax_treatment` NOT NULL migration pending

Step 6.17.3 enforces `store_code` and `tax_treatment` as required at the Pydantic schema layer for POST `/stores`. The DDL columns remain nullable in v0 (`core.stores.store_code TEXT` and `core.stores.tax_treatment tax_treatment_enum`, both without NOT NULL). A future migration tightens both to NOT NULL once cloud testing confirms no orphan rows (the seed populates both for all 25 rows; new POST writes enforce at the API layer).

**Trigger.** Either (a) any production deploy reveals no NULL rows in `core.stores` for either column after a reasonable observation period, OR (b) operator decides to enforce structurally before broader rollout.

**Mechanics.** Alembic migration with two `ALTER COLUMN ... SET NOT NULL` statements. Migration is reversible (drop NOT NULL). No data fix expected.

**Resolution criterion.** NOT NULL constraint on both columns; schema-layer Pydantic constraints become defensive-only.

### FN-AB-51 — `core.stores.status` DDL default is `ACTIVE`; product intent is `OPENING`

Step 6.17.3 LD8 specified "Status server-forced to OPENING via DDL default". The DDL in v0 is `DEFAULT 'ACTIVE'::core.store_status_enum`. The Pydantic schema rejects `status` in the POST body and the repo omits the column from INSERT (honouring LD8's intent — "via DDL default"); v0 new stores read back as `ACTIVE`. The product-intended initial state per the lifecycle enum ordering (`OPENING -> ACTIVE -> INACTIVE -> CLOSED`) is `OPENING`, deferred to a future migration that flips the DDL default.

**Trigger.** Product owner confirms the lifecycle entry state; bundle with a related migration or treat as a single-line Alembic change.

**Mechanics.** Single Alembic migration: `ALTER TABLE core.stores ALTER COLUMN status SET DEFAULT 'OPENING'::core.store_status_enum`. No app-code change required (the schema and repo already rely on the DDL default).

**Resolution criterion.** DDL default flipped; the Step 6.17.3 test `test_c9_create_status_defaults_via_ddl_default` auto-re-aligns to `OPENING`; CLAUDE.md and the router/schema docstrings shed the v0-default-is-ACTIVE notes.

### FN-AB-52 — `InvalidStateTransitionError.public_message` is tenant-flavored; generalise for cross-resource clarity

Step 6.17.4 ships set-status reusing `InvalidStateTransitionError` as-is. The class's `public_message` is `"Tenant cannot transition to the requested state."` (errors.py:247) — accurate for tenants suspend/activate but reused by tenant_users (Step 6.10.1) and stores (Step 6.17.4) without generalisation. Stores' /set-status rejection responses literally read "Tenant cannot transition..." today; matches the tenant_users precedent that has been live since 6.10.1.

**Trigger.** Either (a) a frontend / customer flags the wrong-flavor copy as a UX bug, or (b) a future writer step adds a fourth state-transition consumer and the precedent feels increasingly wrong.

**Mechanics.** One-line change in `errors.py` (e.g., `public_message = "The resource cannot transition to the requested state."` or similar resource-agnostic wording). Update the matching assertion in `tests/unit/test_errors.py:67-69`. Three consumers (tenants, tenant_users, stores) all benefit; no router-side changes.

**Resolution criterion.** `InvalidStateTransitionError.public_message` is resource-agnostic; unit test asserts the new wording; no API contract drift on response `code` or `http_status` (both already 409 / `INVALID_STATE_TRANSITION`).

### FN-AB-53 — `audit_log` write integration for stores set-status

Step 6.17.4 ships `POST /stores/{store_id}/set-status` with a `reason` field accepted at the schema layer but silently dropped at the repo layer per LD3. When Step 6.2's `audit_log` ships, the `set_store_status` handler gains an `audit_log_repo.write(...)` call after the repo `.transition(...)` succeeds. No API change required — the reason field is already on the wire.

**Trigger.** Step 6.2 audit_log ship (per operator: ~1 day from Step 6.17.4).

**Mechanics.** Single `audit_log` row per transition capturing `(actor_user_id, actor_user_type, resource_type='store', resource_id=store_id, action='set_status', from_status, to_status, reason)`. The closure-history-on-reopen case (CLOSED -> ACTIVE / INACTIVE) becomes preserved via this audit log; the live `closed_*` triplet on `core.stores` still nulls per `ck_stores_closed_consistency` (LD2), but historical closure metadata is captured in the audit trail.

**Resolution criterion.** audit_log writes from `set_store_status` are tested; closure-on-reopen audit history queryable via the audit log read endpoint shipped at Step 6.2.

### FN-AB-54 — Slug-truncation collision risk at tenant-root org_node insert (added Step 6.20.1)

The mechanical slug rule in `slug_for_tenant_root` (Step 6.20.1) truncates at 64 chars after diacritic-strip + alphanumeric-collapse. Two tenants whose names slugify to identical 64-char prefixes would both produce the same `code` for their tenant-root org_node. Structurally unreachable at v0: `uq_org_nodes_tenant_code_lower` is tenant-scoped, so the collision only matters if a single tenant ever had two TENANT-type roots (impossible: DDL CHECK `ck_org_nodes_root_parent_consistency` + app convention). The cross-tenant case is masked because each tenant has its own scope.

FN captured for revisit if a future feature ever introduces shared codes across tenants, or if a different resource adopts the same slug helper without the tenant-scoping guarantee.

**Resolution criterion.** Revisit when (a) a future endpoint introduces cross-tenant code visibility (e.g. a global directory of tenant short-codes), or (b) the slug helper is promoted to `utils/` for reuse by a resource without tenant-scoping. Either trigger requires explicit collision-resolution (suffix-append or rejection on collision).

### FN-AB-55 : Cross-env audit-actor drift on catalogue seed inserts

Step 6.18.1's local seed used Excel `created_by_user_id` referencing
bootstrap@ithina.ai (a user that exists in local seed). Cloud SQL has
no bootstrap@ithina.ai user; operator's inline UPSERT used
anjali@ithina.ai's id instead. The `core.role_permissions.created_by_user_id`
for the new SUPER_ADMIN -> ADMIN.ROLES.OVERRIDE.GLOBAL row differs
between local and cloud.

Not load-bearing: both rows satisfy ck_role_permissions_created_by_actor_pair
(non-null + matching user_type). Audit history is divergent across envs
for this single row.

Resolution criterion: either (a) seed Excel converges on a user that
exists in all envs (anjali@ithina.ai is the obvious candidate), or
(b) the catalogue-delta workflow standardises a "system actor"
convention for operator-driven inserts. Revisit at next
catalogue-delta sub-step or when audit_log integration (Step 6.2)
makes this observable.

### FN-AB-56 : NIGHT_SHIFT_LEAD is_system=false intent

Investigation for Step 6.18 surfaced NIGHT_SHIFT_LEAD as the sole role
with is_system=false in the seed. Intent unclear: test data, custom-
role demo placeholder, or operator data-entry error.

Resolution criterion: confirm intent at next seed Excel touch-point.
If intentional, document why. If error, flip to true.

### FN-AB-57 : SUPER_ADMIN role API editability deferred to v1

Step 6.18.3 ships PATCH /api/v1/roles/{role_id} with a hard-coded v0
lockout: the SUPER_ADMIN role itself cannot be edited via the API (409
SUPER_ADMIN_PROTECTED on any PATCH targeting that role). Operator
workflow for SUPER_ADMIN edits in v0 is direct SQL on `core.roles`
and `core.role_permissions` via Cloud SQL Studio.

The lockout is defensive: SUPER_ADMIN is the platform-admin bootstrap
role; a misconfigured edit could lock platform admins out of all
write surfaces. The two-layer OVERRIDE.GLOBAL invariant (LD6) guards
the assignment side (no edit can zero out active holders) but does
not guard the role's permission set itself — a careless rename or
permission removal on SUPER_ADMIN could create downstream issues no
gate catches.

v1 promotion plan: lift the lockout in favor of Pattern 1 (full
audit trail + admin-review queue + two-person approval for
SUPER_ADMIN edits). Requires:
- Audit log integration (Step 6.2).
- A review-queue surface (new endpoint or admin UI).
- Optional: a 24-hour delayed-apply window for SUPER_ADMIN edits.

Resolution criterion: explicit operator decision plus audit log
prerequisite (Step 6.2). Until then, SUPER_ADMIN edits are direct-SQL
only.

### FN-AB-58 : `_actor_type_from_auth` duplicated across router files

Step 6.18.3 added a third local copy of `_actor_type_from_auth` to
`routers/v1/rbac.py`. The helper now lives in:

- `routers/v1/tenant_users.py:312`
- `routers/v1/stores.py:84`
- `routers/v1/rbac.py:128`

All three are byte-identical functions mapping AuthContext.user_type
(Literal) to ActorUserType (typed enum). The duplication exists
because the routers are deliberately decoupled from each other in
the v0 layout. Each new router that writes Pattern (b) audit-actor
columns adds another copy.

Resolution: promote the helper to a shared module (likely
`admin_backend/auth/actor_type.py` or `admin_backend/auth/utils.py`).
The promotion is mechanical (move the function, update the three
import sites) but expands step scope; defer to a dedicated cleanup
step or fold into the next write-router step.

Resolution criterion: when the FOURTH local copy would be added
(next new write router), or as a dedicated `auth/` reorg.

### FN-AB-59-CRITICAL : Race-condition mitigation for PATCH /api/v1/roles/{role_id}

**CRITICAL marker** (new convention; this entry establishes it).
High-blast-radius, low-probability, must-not-defer-indefinitely:
concurrent platform-admin role edits could silently corrupt
role-permission state without detection at v0 scale. The Layer 2
tripwire catches the security-critical OVERRIDE.GLOBAL zero-out case
but does NOT catch last-writer-wins on non-invariant fields.

Step 6.18.3 ships PATCH role-edit without per-row locking
(SELECT FOR UPDATE) or SERIALIZABLE isolation. Two PLATFORM admins
PATCHing the same role concurrently can interleave such that:

- Both pass Layer 1 OVERRIDE.GLOBAL invariant (each sees the other's
  pre-write state).
- One commits a state-A write; the other commits a state-B write
  immediately after.
- Net result depends on transaction ordering at Postgres; one set of
  changes wins, the other is lost without notification.

The Layer 2 tripwire catches the case where the two-write
combination zeroes out OVERRIDE.GLOBAL active holders (one or both
transactions abort with 500); other interleavings are silently
last-writer-wins.

v0 acceptance: at v0 scale (a handful of platform admins doing rare
edits), concurrent PATCH on the same role is operationally unlikely.
The Layer 2 tripwire provides correctness on the security-critical
invariant; non-invariant fields (name, description, non-OVERRIDE
permissions) are last-writer-wins.

Resolution candidates (post-v0):
- Add `SELECT ... FOR UPDATE` on the role row at the start of the
  repo update method.
- Switch the transaction to SERIALIZABLE isolation level (heavier;
  may need retry logic for serialization failures).
- Add optimistic concurrency control via an `etag` / `version` field
  on the role row (D-31 append-only field).

Resolution criterion: a real concurrent-edit incident, OR
post-v0 operational-hardening pass.

### FN-AB-60-CRITICAL : Runtime permission catalogue API + enum decoupling

**CRITICAL marker.** High-blast-radius, low-probability, must-not-
defer-indefinitely: the enum-locked permission vocabulary blocks
runtime extension of the catalogue. Custom modules, partner
integrations, and any v1 scope that requires permissions not in the
hardcoded enum require a code change + redeploy. The current shape
is fine for v0's locked vocabulary; the moment v1 introduces a
runtime-extension use case, this entry becomes load-bearing.

The `permissions` catalogue is currently both a DB table AND a set
of Python enums (`PermissionResource`, `PermissionAction`,
`PermissionScope`) hardcoded in `models/permission.py`. Step 6.18.3's
PATCH endpoint validates `permission_ids` against the DB catalogue
(LD11 INVALID_PERMISSION_ID check) but the enums themselves cannot
be extended at runtime — adding a new resource or action requires
a code change + redeploy.

For v1 scope expansion (e.g., custom modules, partner integrations),
the enum vocabulary needs to be runtime-driven:

- New permissions added to `core.permissions` via admin API or
  migration.
- Python code references permissions by id or code string, not by
  enum member.
- Pydantic schemas validate permission codes against the catalogue
  at runtime, not against a hardcoded enum.

Resolution requires:
- Catalogue-extension API (POST /api/v1/permissions or similar).
- Refactor of all permission-checking code to use code strings.
- Migration path for existing enum-typed columns
  (`permissions.module`, `permissions.resource`, etc.) to text
  columns with FK to a catalogue.

Resolution criterion: explicit v1 scope decision. v0 ships with the
enum-locked vocabulary; the catalogue is admin-managed via
migrations.

### FN-AB-61 : GET /me/can-do returns 500 on malformed target_anchor; should be 422

`routers/v1/me.py:106-115` accepts `target_anchor` as a bare
`str | None` query param and passes it verbatim to `has_permission`,
which casts via `CAST(:target_anchor AS ltree)` at
`auth/permissions.py:318`. A malformed value (any character outside
`[A-Za-z0-9_]` per label, e.g. a hyphen or space at any position)
raises `psycopg.errors.SyntaxError: ltree syntax error at character N`.
The handler does not catch it; it bubbles to the generic 500
INTERNAL_ERROR envelope. Surfaced via Cloud Run logs on a
`GET /me/can-do?target_anchor=...` call in cloud.

Right behavior: validate at the FastAPI layer so a malformed
`target_anchor` returns 422 before the DB sees it. The ltree label
syntax is well-defined; a single regex covers it (one or more
dot-separated alphanumeric+underscore labels):
`^[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)*$`.

**Resolution options:**

(a) Pydantic-level: add `pattern=r"^[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)*$"`
    on the `target_anchor` `Query(...)` declaration. FastAPI returns
    its default 422 envelope on mismatch. One-line change; matches the
    `OrgNodeCreateRequest.code` pattern-validator precedent
    (`schemas/org_node.py:271, 334`).

(b) Handler-level: wrap the `has_permission` call in a `try/except
    psycopg.errors.SyntaxError` (or SQLAlchemy `DataError`) and raise
    a new `InvalidLtreePathError` ClientError (422,
    `INVALID_LTREE_PATH`). Heavier than (a) but lets the message name
    the offending input.

(c) Future-thinking: only the `/me/can-do` endpoint takes a raw
    `target_anchor` from the caller. Every other production gate
    sources `target_anchor` from an `anchor_dep` (DB-read; cannot be
    bad). If a second user-supplied-ltree endpoint lands, promote
    validation to a shared `LtreePath` Pydantic type used at both call
    sites.

**Resolution criterion.** `GET /me/can-do?target_anchor=<bad>` returns
422 (not 500). Pick (a) at minimum; revisit (c) when a second
user-supplied-ltree endpoint surfaces.

**Severity.** Not CRITICAL — caller-induced, no state corruption, no
information disclosure (the 500 envelope is the generic
INTERNAL_ERROR per the error model). Wire-contract cosmetic.

**RESOLVED 2026-05-19 (Step 6.20.2).** Option (a) shipped: inline
`pattern=` + `max_length=` on the `target_anchor` Query declaration
at `routers/v1/me.py:106-118`. Pattern is
`r"^[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)*$"` (multi-label ltree grammar);
`max_length=1024` (conservative cap above realistic org-tree depth).
Mirror of the existing pattern validator at `schemas/org_node.py:271`.
Bundled docstring correction at `schemas/org_node.py:275` (the
"No underscores (ltree label restriction)" claim was backwards;
underscores are valid in ltree labels — hyphens are not — and the
org_node code convention is the inverse, with `_path_label` as the
bridge). Option (c) shared `LtreePath` Pydantic type promotion
deferred; single call site today. Revisit trigger documented in
this entry when a second user-supplied-ltree endpoint surfaces.

### FN-AB-62 : AI-RBAC-01 comment in rbac_v3.sql can reference the new DDL backstop

Step 6.20.3 added a DDL trigger
(`tg_role_permissions_audience_scope_coherence`) that structurally
enforces "TENANT-audience role cannot hold GLOBAL-scope permission" at
INSERT or UPDATE OF role_id/permission_id on `role_permissions`. The
existing comment block at `db/raw_ddl/Ithina_postgres_SQL_DDL_rbac_v3.sql`
(application-layer invariants section, AI-RBAC-01 entry) still
describes the invariant as "App-layer pre-check" only. The shipped
trigger means the invariant is now app-layer (clean 422 envelope per
Step 6.18.3 LD17) AND DDL-enforced (backstop for direct-SQL /
seed-loader / future-endpoint paths). The comment can be amended to
note both layers, mirroring the two-layer description style used for
the OVERRIDE.GLOBAL invariant.

Resolution criterion: next commit that already touches `rbac_v3.sql`
for unrelated reasons OR a dedicated raw_ddl comment-cleanup pass.
Pure documentation; no code change. Captured here so the adjacent
improvement isn't lost.

### FN-AB-63 : Pydantic RequestValidationError bypasses project error envelope; audit emission for direct-Pydantic 422 deferred

Surfaced at Step 6.16.2 pre-flight. The codebase's global exception
handler at `src/admin_backend/main.py:233` is registered for
`AdminBackendError` (the project's ClientError/ServerError base);
Pydantic's `RequestValidationError` (raised when a FastAPI route's
request body or query params fail Pydantic validation) is NOT a
subclass of `AdminBackendError` and therefore bypasses the handler.
FastAPI's default handler produces a 422 response with shape
`{"detail": [...]}`, which differs from the project's envelope
`{"code", "message", "details", "request_id"}`.

Consequence for audit emission: Step 6.16.2 wired failure-path audit
emission into the AdminBackendError handler. Pydantic-direct 422s
(e.g., POST /tenants with a missing required field, or PATCH with a
type mismatch) do not reach the handler and therefore do not produce
audit rows. The codebase's own 422 paths (e.g., `EmptyPatchError`,
`InvalidTenantNameForSlugError`, which ARE AdminBackendError
subclasses) DO emit audit rows as expected.

Why deferred at Step 6.16.2:

- Adding `@app.exception_handler(RequestValidationError)` to convert
  Pydantic's default shape to the project envelope is a wire-contract
  change that spans every endpoint, not just the audited tenant
  endpoints. Frontend consumers may parse `detail` today; flipping to
  the project envelope is a coordinated change.
- The scope decision belongs to a dedicated step (envelope unification
  + audit emission for the merged path lands as side effect), not to
  Step 6.16.2's narrow scope of "wire emission for 4 tenant endpoints".

What this leaves uncovered today:

- POST /tenants and PATCH /tenants/{id} with a request body that
  fails Pydantic validation (wrong type, missing required field,
  email format, etc.): no audit row.
- 422s that flow through the codebase's own ClientError subclasses:
  unaffected; audit rows fire normally.

Resolution path:

1. Operator-led scope-decision step: confirm whether the project
   envelope should subsume Pydantic-direct 422s. Frontend consumer
   sign-off required.
2. Add `@app.exception_handler(RequestValidationError)` that converts
   to the project envelope. Update or add tests confirming the wire
   shape.
3. The new handler calls the same `AUDITED_ROUTES` lookup +
   `emit_audit_event_in_new_transaction` path that the existing
   handler does, so audit emission for Pydantic-direct 422 lands as
   a side effect of the envelope migration.

Trigger to revisit:

- Frontend reports inconsistent error envelopes between codebase 422s
  and Pydantic 422s (UX-bug ticket).
- Compliance / audit review surfaces gap that 422-from-Pydantic
  requests aren't logged in audit.
- Step 6.16.5 (final sub-step) cleanup pass picks this up if neither
  of the above has fired by then.

Severity: not CRITICAL. Caller-induced, no state mutation lost
(the request never reaches the data write), no information
disclosure beyond what Pydantic's default handler already exposes.
The audit-trail completeness gap is the only meaningful concern.

### FN-AB-64 : Audit log search uniformity + ADMIN.AUDIT_LOG.VIEW.TENANT over-granted to tenant roles

Two related observations surfaced at Step 6.16.3 about the audit log
read endpoints' read-access shape.

**(a) Uniform 4-column search rationale.**

The list endpoint at `GET /api/v1/audit/activities` runs
`ILIKE %term%` on four columns (`actor_display_name`, `action_label`,
`resource_label`, `tenant_name`) joined with OR. The same SQL fires
regardless of caller audience. For tenant callers the `tenant_name`
column matches every row they see (RLS scopes to own-tenant; only
one `tenant_name` value present), so that branch of the OR is a
benign no-op rather than a wasted filter. The uniformity is
intentional per Q6 design lock: divergent SQL per audience would
require maintaining two near-identical query trees with the same
correctness invariants. Cost is negligible at v0 scale; revisit if
search becomes a hot path and the leading `%` becomes the actual
bottleneck (then add `pg_trgm` per the design doc Scale
considerations).

**(b) `ADMIN.AUDIT_LOG.VIEW.TENANT` over-granted to tenant roles.**

The Q5 design intent at 6.16.0 was that `ADMIN.AUDIT_LOG.VIEW.TENANT`
goes to high-trust tenant roles (OWNER, COMPLIANCE_OFFICER, perhaps
FINANCE_ADMIN). The seed Excel as of Step 6.16.3 grants the tuple to
8 of 11 tenant roles: OWNER, PRICING_MANAGER, STORE_MANAGER,
CATEGORY_MANAGER, FINANCE_ADMIN, COMPLIANCE_OFFICER,
PROMOTIONS_MANAGER, DATA_ANALYST. Four tenant roles deliberately do
NOT have the grant: ASSOCIATE, NIGHT_SHIFT_LEAD, PERISHABLES_LEAD,
REGIONAL_DIRECTOR. The over-granting was an operator decision at
6.16.3 to enable broad test coverage during v0 (more callers can
exercise the endpoint); the cleanup to align with Q5 design intent
is deferred to v0 staging cleanup.

Adjacent observation: SUPPORT_ADMIN (a platform role) previously
held `.VIEW.TENANT` but not `.VIEW.GLOBAL`. The Step 6.16.3
catalogue UPSERT revoked `.VIEW.TENANT` from SUPPORT_ADMIN and
granted `.VIEW.GLOBAL`. The repo's dispatch on `auth.user_type`
treats SUPPORT_ADMIN as PLATFORM and shows them the merged
platform-wide view (UNION of both audit tables). For a role
intended to impersonate / debug tenant issues, this is broader
than necessary; whether SUPPORT_ADMIN should see other tenants'
audit logs is a Stage 3 impersonation-design question
(FN-AB-23 territory).

**Severity.** Not CRITICAL. (a) is design-confirmed; (b) is over-
grant rather than under-grant, so no caller is wrongly denied.
Both are noted for the v0 staging cleanup pass that will revisit
the tenant-role grant matrix.

**Resolution criterion.** v0 staging cleanup commit (probably
adjacent to / bundled with the Stage 5 staging Cloud SQL deploy)
revisits the tenant-role grant matrix and aligns with the Q5
design intent. Adjacent SUPPORT_ADMIN over-broad visibility folds
into the impersonation design at FN-AB-23.

### FN-AB-65 : Post-6.16.0 endpoint-count drift; closed across 6.16.4 + 6.16.5

The 6.16.0 design framed 12 v0 write endpoints across 4 resource
families (tenants 4 + tenant-users 4 + module-access 2 + org-tree 2).
Between 6.16.0 (2026-05-20) and 6.16.4 (2026-05-21), two steps shipped
additional write endpoints with explicit "audit deferred to Step 6.16"
annotations:

- **Step 6.17.3 + 6.17.4** : stores writes (POST + PATCH + set-status,
  3 endpoints). FN-AB-53 names the explicit deferral for set-status;
  the other two stores writes ship without a separate FN.
- **Step 6.18.3** : roles PATCH (1 endpoint). Architecture posture noted
  the audit-log integration was pending Step 6.2 / 6.16 ship.

Live v0 write surface is now **16 endpoints across 6 resource families**.

**Closure plan.**

- Step 6.16.4 (this commit) closes the roles PATCH deferral (1
  endpoint). `docs/architecture_audit_logs.md` Overview and Sub-step
  plan amended; BUILD_PLAN.md Step 6.16 root entry and Step 6.16.4 /
  6.16.5 sub-step blocks amended in lockstep.
- Step 6.16.5 closes the stores deferrals (3 endpoints) alongside
  the originally-scoped module-access (2) + org-tree (2).

**Resolution criterion.** FN-AB-65 closes when Step 6.16.5 ships.

**RESOLVED 2026-05-21 (Step 6.16.5).** Module-access (2) + org-tree (2) + stores (3) emissions shipped; the live v0 write surface (16 endpoints across 6 resource families) now emits synchronous audit rows on success and failure paths uniformly. BUILD_PLAN.md Step 6.16 root flipped to DONE-LOCAL; `docs/architecture_audit_logs.md` Sub-step plan table closure note records "6.16 series complete."

### FN-AB-66 : AUDITED_ROUTES per-route extractor mapping (deferred)

Step 6.16.4's failure-path handler extension uses a minimal multi-key
fallthrough loop for path-param extraction (`tenant_id` -> `user_id`
-> `role_id`) and a `resource_type`-dispatched lookup table for
resource_label resolution. The pattern works cleanly for the 5
endpoints landing in 6.16.4. The 6.16.5 endpoints (module-access,
org-tree, stores) all bind a similar small set of path-param names;
no new shape is expected.

If 6.16.5 surfaces a third path-param name OR a third resource-label
lookup table (e.g., module-access wants to resolve a module_code
display label from `core.lookups`), promote the pattern to a per-route
extractor declaration in `AUDITED_ROUTES` (e.g., extend the tuple to
include `extractor: ResourceExtractor`) or a sibling dict. The current
fallthrough scales to ~5 paths before the dispatch becomes cluttered.

**Resolution criterion.** Step 6.16.5 design Phase 1; revisit when a
third extractor shape would land. Until then, the minimal fallthrough
holds.

**RESOLVED 2026-05-21 (Step 6.16.5).** Per-route extractor mapping
shipped via sibling dict `RESOURCE_EXTRACTORS: dict[resource_type,
ExtractorFunc]` at `src/admin_backend/main.py` (shape b per the
operator-locked LD12 decision). Six extractors keyed by resource_type:
TENANT, TENANT_USER, ROLE (the 6.16.4 set), and MODULE_ACCESS,
ORG_NODE, STORE (the new 6.16.5 set). Each extractor returns a
`FailureContext` carrying `(resource_id, tenant_id_for_row,
module_code, node_id, store_id)`; the failure handler resolves the
route template, reads `AUDITED_ROUTES[key].resource_type`, consults
`RESOURCE_EXTRACTORS[resource_type]`, invokes it, and forwards the
`FailureContext` plus auxiliary lookup hints to
`emit_audit_event_in_new_transaction`. The 6.16.4
`_failure_result_and_details(... auth=...)` extension (caller_audience
fallback) preserved. Label-resolution dispatch inside emit extends to
3 new resource_types; lookup tables are `core.tenant_module_access`
+ `core.tenants` + `core.lookups` (MODULE_ACCESS); `core.org_nodes`
+ `core.tenants` (ORG_NODE); `core.stores` JOIN `core.tenants` (STORE).
Documented in `docs/architecture_audit_logs.md` Emission contract
section.

### FN-AB-67 : Audit row actor enrichment (full_name + role snapshot) (RESOLVED at Step 6.16.7)

**RESOLVED 2026-05-23 (Step 6.16.7).** Actor enrichment shipped via two new NOT NULL columns on both audit tables: `actor_roles` (comma-separated active role display names from `roles.name`, e.g., "Owner, Promotions Assistant") and `actor_organization_name` (tenant name for TENANT actors, literal `"Platform-Ithina"` for PLATFORM). Both frozen at write time per the Phase 1 lock "audit is history of an event — history never changes" (LD4). The display-name (not `roles.code`) choice is load-bearing for the UI redesign: the column is rendered directly without further transformation. `full_name` was a candidate the operator chose not to pursue per Phase 1 lock; `actor_display_name` (= `auth.email`) remains the actor identity. Path A backfill (LD3) handled historical rows: tenant table via CASE on `actor_user_type`; platform table via literal; `actor_roles = '-'` for all pre-6.16.7 rows because the actor's roles at the historical action moment are no longer reliably knowable. The role + organisation snapshot honors the "audit is history of an event - history never changes" principle (Phase 1 Q3 lock). The forward-note text below is preserved verbatim for historical record; the resolution overturns the Phase 1 Q7 of 6.16.4 lock as the forward-note anticipated.

---

The audit row's actor representation today carries three columns:
`actor_user_id` (UUID), `actor_user_type` (PLATFORM/TENANT),
`actor_display_name` (= `auth.email` at write time per Step 6.16.2
posture; Step 6.16.4 explicitly keeps this per pre-flight Option B).
The list-view Actor column shows email; the detail view adds user_id
+ user_type.

Product intent surfaced during 6.16.4 Phase 1 / pre-flight: the audit
reader would benefit from also seeing the actor's `full_name` AND
the actor's `role(s) at the time of the action`, frozen at write
time. Neither is in the schema today.

**Trigger.** Either (a) operator feedback from the cloud-deployed
audit UI that email-only is insufficient for the auditor's mental
load; (b) audit subsystem frontend Layer 1 mockup fleshed out and
full_name + role become required wire fields; (c) security /
compliance review requires actor-role-at-time-of-action.

**Non-triggers (explicit).** "Would be nicer" is not enough. Display
preference alone is not enough.

**What changes when triggered.**

- DDL migration: add `actor_full_name TEXT NULL` and `actor_roles
  JSONB NULL` to BOTH `core.tenant_activity_audit_logs` AND
  `core.platform_activity_audit_logs`. Pre-migration rows carry
  NULL; new writes after the migration populate both. Decision
  point: NOT NULL via a separate post-backfill ALTER, or NULL-allowed
  indefinitely.
- `audit/emit.py::_build_row` eager-resolves both at write time:
  full_name via dispatch on `actor_user_type` (PLATFORM ->
  `platform_users.full_name`; TENANT -> `tenant_users.full_name`);
  roles via JOIN over the appropriate `*_user_role_assignments` ->
  `roles` -> `org_nodes` filtered by `status='ACTIVE'`.
- `AuditActivityListItem` schema grows `actor_full_name: str | None`
  and `actor_roles: list[dict] | None`.
- Existing AE-series + AS-series + AF-series tests need value
  updates (currently assert email-shaped `actor_display_name`).

**Snapshot principle (consistent with Phase 1 Q2).** Both fields
frozen at the moment of emission. Subsequent renames or role changes
do NOT rewrite historical rows. Overturns Phase 1 Q7 of 6.16.4
explicitly (which locked name-only-no-role); the reversal is
intentional and acknowledged at FN closure time.

**Cost.** Half a day to one day of work. Ripples backward into
6.16.2 emission code (every emit point needs the new lookups).
Existing audit rows in the database carry NULL in the new columns;
audit UI handles NULL gracefully.

**Resolution criterion.** Explicit operator decision that the
actor-enrichment value (richer list-view, easier forensic
correlation, role-snapshot for compliance) outweighs the cost (DDL
migration, 6.16.2 retrofit, additional per-emit latency, audit-row
size growth).

### FN-AB-68 : OPEN_SOFT action code reserved for unreachable transition

Step 6.16.5 LD3 ships 4 per-target action codes for stores
set-status: ``OPEN_SOFT`` (target=OPENING), ``ACTIVATE``
(target=ACTIVE), ``CLOSE`` (target=CLOSED), ``DEACTIVATE``
(target=INACTIVE). The first is reserved but not currently
producible: the live ``TRANSITION_MATRIX`` (``repositories/stores.py``,
Step 6.17.4 LD1) has no cell that allows ``*->OPENING``. OPENING is
the entry-only status, populated at POST /stores via the DDL
default (per 6.17.4 LD1 + 6.17.3 LD8 + FN-AB-51).

The label stays in the action vocabulary (and in `_label_for_action`)
for D-31 append-only stability and for future matrix relaxation
(e.g., if a future "reopen-as-OPENING" use case lands and the matrix
gains a ``*->OPENING`` cell). The AE11 unit test
(`tests/unit/test_audit_emit.py`) covers the label dispatch via the
builder; integration coverage gated on matrix relaxation.

**Resolution criterion.** Either (a) the TRANSITION_MATRIX is
relaxed to allow `*->OPENING` (the label then has integration
coverage automatically), or (b) explicit operator decision to retire
``OPEN_SOFT`` from the vocabulary (would require a follow-on cleanup
removing the label entry plus the dispatch branch). v0 ships
posture (a) by default — minimum-friction; the label is dormant
not dead.

### FN-AB-69 : Actor filter on GET /audit/activities (RESOLVED at Step 6.16.6)

Frontend integration of the audit subsystem post-6.16.5 surfaced three
consumer surfaces that needed an actor-scoped read of
`/api/v1/audit/activities`: `PlatformUserDetailDrawer.Activity` and
`TenantUserDetailDrawer.Activity` (drawer Activity tabs per-user) and
`RecentActivityPanel` on the SuperAdmin dashboard. The shipped 6.16.3
read endpoint exposed no structured way to filter by actor; the drawers
were temporarily reading from the dead `/audit-logs` URL while waiting
for the filter. The pre-existing acknowledgement of this gap lived in
`docs/architecture_audit_logs.md:399` (Scale considerations option 6 —
"actor filter parameter + actor_user_id BTREE index") and `:432` (Open
deferred items — "Actor filter parameter") without a FN-AB number;
this entry numbers the gap and records its closure in the same commit
(born-resolved; mirrors FN-AB-19 / FN-AB-21 precedent).

**Resolved at Step 6.16.6 (2026-05-23).** `GET /api/v1/audit/activities`
gained an optional `actor_user_id: UUID | None = None` query
parameter. AND-composed with existing filters per the 6.16.5 LD17 /
6.16.3 LD5 precedent. SQL clause
`AND (CAST(:actor_user_id AS uuid) IS NULL OR actor_user_id = CAST(:actor_user_id AS uuid))`
added inline at the two filter sites in
`src/admin_backend/repositories/audit_logs.py` (the TENANT-only
builder at `_build_tenant_only_sql` and the shared `common_where`
block in `_build_union_sql`; behaviour identical to a single shared
helper). No actor_user_type companion parameter:
`platform_users.id` and `tenant_users.id` use the same `uuidv7()`
DDL default (verified at pre-flight Check #11) and are globally
unique, so `actor_user_id` alone is fully selective. Open-vocabulary
posture: unknown UUIDs return 0 rows naturally, no 422. RLS-scoping
for TENANT callers preserved (the filter narrows; RLS still enforces
own-tenant visibility). 3 new tests (AUF1 happy path, AUF2
AND-composition with status, AUF3 unknown UUID -> empty); 2
LOAD-BEARING (AUF1, AUF3). pytest 869 -> 872 (+3). actor_user_id
BTREE index remains deferred per design doc Scale option 6 (v0
scale sub-millisecond on sequential scan).

### FN-AB-70 : INTEGRITY_VIOLATION reserved vocabulary

`AuditResultType.INTEGRITY_VIOLATION` exists as a fully-shipped result_type with enum value, "Integrity violation" label, and `build_integrity_violation_details` builder function — but zero production callers anywhere in the codebase at HEAD post-Step-6.16.7.

The dispatch in `main.py::_failure_result_and_details` routes by `http_status` (403 -> PERMISSION_DENIED, 409 -> CONFLICT, 422 -> VALIDATION_FAILED, ServerError -> INTERNAL_ERROR); no path reaches INTEGRITY_VIOLATION. No success-path emission passes this result_type either.

Phase 1 of Step 6.16.7 locked retention of the dormant vocabulary (option b: minimum-friction; if a future emission path needs to distinguish DB-layer integrity violations from app-layer CONFLICTs, the slot is ready).

**Resolution criterion.** Either (a) a production caller surfaces (an emission path determines that a DB integrity constraint violation merits a distinct audit result_type), or (b) explicit operator decision to retire INTEGRITY_VIOLATION from the vocabulary (would require a migration to remove the enum value).

## Application-layer invariants

These are NOT enforced at the DB layer. They MUST be enforced in service code.

For v0 (read-only), the relevant ones:

### AI-MT-01 — Centralised tenant binding

Every DB connection acquired by app code goes through `get_tenant_session()`. No other path. Linter rule should forbid direct `engine.connect()`.

### AI-MT-02 — Tenant_id only from trusted source

`tenant_id` reaches the backend only from verified JWT (tenant users) or verified path parameter (staff cross-tenant ops). Never from request body, query string, or custom headers.

### AI-MT-03 — `app.tenant_id` is sourced exclusively from `AuthContext.tenant_id`

The Step 2.2a `get_tenant_session` dependency is the only code path that calls `set_config('app.tenant_id', ..., true)`. Its input is `auth.tenant_id`, a `UUID | None` field on the frozen Pydantic `AuthContext` model (per D-24). mypy strict statically rejects any attempt to flow a raw string into this dependency.

Step 2.2a implementation supersedes the originally-planned `VerifiedTenantId` newtype with structural source-binding: `app.tenant_id` is set only from `AuthContext.tenant_id` (validated `UUID | None` on a frozen Pydantic model). mypy enforces statically. The newtype is redundant ceremony — there is no runtime path that accepts a raw string and produces a `VerifiedTenantId`, so the wrapper would only re-state what the AuthContext field type already guarantees.

The same discipline holds for `app.user_type`: sourced only from `AuthContext.user_type`, a `Literal["PLATFORM", "TENANT"]`. mypy rejects any other source.

### AI-MT-04 — Cross-check tenant context

If JWT tenant_id and path tenant_id disagree, quarantine the request (see D-18).

### AI-RBAC-04 — Permission resolution filters by all-active

Permission resolution requires user, assignment, role, org_node ALL to be active.

### AI-RBAC-05 — Permission cascade via ltree path for TENANT-audience

Permissions inherit downward through org tree. PLATFORM-audience permissions apply globally regardless of org_node.

---

## Cloud-specific differences

Behaviours that differ between local Docker Postgres and Cloud SQL, and the conventions that bridge them. New entries land here when subsequent build steps surface real divergences.

### CSD-01 — Schema creation is Alembic's responsibility, not Terraform's

**What.** The application schema (`core` on local; per-env configurable per D-15) is created by `migrations/env.py` via `CREATE SCHEMA IF NOT EXISTS "{db_schema}"` immediately after opening the migration connection and before `do_run_migrations` runs. Terraform provisions the Cloud SQL instance, the database (`ithina_platform_db`), and the application role (`user_admin_backend`); it does NOT create the schema inside the database.

**Why.** Discovered at Step 4.1 first cloud bring-up (image v0.1.1 alembic execute, 2026-05-04): Alembic's internal `_ensure_version_table()` runs before any migration's `upgrade()` and tries to `CREATE TABLE {schema}.alembic_version`; on a fresh cloud DB where the schema doesn't exist, this fails with `psycopg.errors.InvalidSchemaName: schema "core" does not exist`, and no migrations run. Local doesn't hit this because Step 1.4 set up the schema by hand on the Docker Postgres container; cloud has no equivalent setup step. The CREATE-SCHEMA-in-env.py pattern is idempotent (no-op when present, e.g. local) and self-heals every fresh DB.

**How to apply.** New cloud DBs (a new dev Cloud SQL instance, staging, prod) inherit the fix: first `alembic upgrade head` creates the schema if missing, then proceeds normally. Operator and Terraform have no schema-creation responsibility. If `migrations/env.py` is ever rewritten or a different migration tool replaces Alembic, the schema-pre-create must move with the migration runner — it's load-bearing for fresh-DB bring-up.

### CSD-02 — Cloud SQL extensions (`ltree`, `pgcrypto`) are infra-owned, created out-of-band before Alembic runs

**What.** The `ltree` and `pgcrypto` Postgres extensions are required by the initial-schema migration (`ad8afd429581` runs a precondition check that raises if either is missing). Locally they were installed by hand at Step 1.4 in `public`. On Cloud SQL they must be created by a `cloudsqlsuperuser` role (the auto-provisioned `postgres` BUILT_IN user), NOT by the application role `user_admin_backend` which is `NOSUPERUSER NOBYPASSRLS` by deliberate Step 1.5 hardening and structurally cannot `CREATE EXTENSION`.

**Current dev procedure (manual, one-time per Cloud SQL instance).** Before the first `alembic upgrade head` on any Cloud SQL instance: open Cloud SQL Studio (GCP Console → SQL → instance → Studio), connect as `postgres`, and run:

```sql
CREATE EXTENSION IF NOT EXISTS ltree;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
```

Idempotent (no-op when present). Cloud SQL whitelists both extensions on Postgres 15.

**Why this is NOT in env.py the way CSD-01 is.** CSD-01 (CREATE SCHEMA) is privilege-compatible with the application role; CSD-02 (CREATE EXTENSION) is not. Granting the application role `cloudsqlsuperuser` just so Alembic can run extensions would defeat Step 1.5 hardening permanently for what is structurally a one-time-per-database setup task. Extensions are infrastructure-owned; the application role stays minimum-privilege.

**Forward path.** This is a known gap, not a permanent posture. **Step 8.0** in BUILD_PLAN.md replaces the manual Cloud SQL Studio step with Terraform-managed extension creation in admin-infra, and is a **hard precondition** of Step 8.1.1 (production GCP provisioning). Tracked as **FN-AB-17**. Production must NOT depend on a manual Cloud SQL Studio step.

**How to apply.** Until Step 8.0 ships: any new Cloud SQL instance (a fresh dev re-bring-up, staging) follows the manual two-statement Studio procedure above. After Step 8.0 ships: the manual procedure goes away in favour of Terraform; this CSD-02 entry will be amended (not deleted — the historical "discovered at Step 4.1, manual until Step 8.0" record stays).

### CSD-03 — Direct SQL verification against private-IP-only Cloud SQL: paths and limits

**What.** Three operator-side paths exist for ad-hoc SQL queries
against Cloud SQL `admin-master-dev` (private-IP-only by Step 4.1
design). Their viability differs significantly from local Docker
Postgres, where direct `psql` always works.

**Path 1 — `gcloud sql connect` from operator's laptop.** Doesn't
work. The local Cloud SQL Auth Proxy that gcloud spawns can't reach
the private-IP-only instance from outside the VPC, even with
just-in-time IP whitelisting. Symptom: `psql: error: connection to
server at "127.0.0.1", port <N> failed: server closed the connection
unexpectedly` — the proxy itself terminates before authentication.
Worked locally in Step 1.5 because that was Docker Postgres on
loopback; doesn't translate to private-IP managed Cloud SQL.

**Path 2 — Cloud SQL Studio (browser, GCP Console).** Partially
works, with two distinct failure modes depending on which user
authenticates:

- **postgres user (cloudsqlsuperuser).** Editor accepts paste/edit.
  But the schema explorer doesn't show `core` (information_schema
  filters by `has_schema_privilege()`, and postgres has no privilege
  on `core`), and any `SELECT FROM core.*` returns "permission denied
  for schema core". Schema is owned by `user_admin_backend` per
  CSD-01; postgres can't grant itself USAGE because it doesn't own
  the schema, and `cloudsqlsuperuser` does not bypass schema
  ownership the way a true Postgres superuser would.
- **user_admin_backend (application role).** Schema explorer shows
  `core` and all tables (USAGE present, owns the schema). But Studio
  disables paste and edit in the query editor for non-superuser
  non-IAM accounts. The editor renders read-only.

The two failure modes are mutually exclusive — one can edit but not
see; the other can see but not edit. No single Studio session
combines both capabilities for direct SQL queries against
application data in `core`.

**Path 3 — End-to-end deployed-service curl with a minted PLATFORM
JWT.** Works reliably and is the recommended path for this
environment. Mints JWT locally via `./scripts/jwt/generate.sh
<email>`; curls authenticated endpoints against the deployed Cloud
Run service. The service connects to Cloud SQL via VPC private IP
(by design) and surfaces all read paths through HTTP. This is also
the strongest signal — exercises HTTPS → JWT verify → AuthContext →
middleware → RLS-bound Repo → JSON, end-to-end. Endpoints
particularly useful for verification: `/api/v1/tenants/stats`
(aggregate counts), `/api/v1/platform-users` (PLATFORM-only list),
`/api/v1/lookups` (migration-seeded data), `/api/v1/tenant-users`
with PLATFORM JWT (exercises D-29 OR-clause across all tenants).

**Verdict.** Use Path 3 for ad-hoc verification. Use Path 2 (postgres
user) for `CREATE EXTENSION` and other instance-admin operations
that don't need core schema access (per CSD-02). Path 1 is not viable
unless/until private-IP-only is relaxed (which it shouldn't be — Step
4.1 design).

**Forward path.** None — this is a documented constraint, not a gap
to fix. Operators need to know which path to reach for; this entry
captures the trade-offs. Step 8.x prod-time considerations may
revisit if Cloud SQL Studio's UI policy changes or if a different
ad-hoc query mechanism becomes attractive.

**Discovered at Step 4.3.5 (2026-05-04).** The Step 4.3.5 verification
plan originally called for Path 1 (gcloud sql connect) and Path 2
(Studio); both failed in the ways above; Path 3 was substituted and
proved stronger. This CSD captures the lessons for future operators.

---

## Schema reference

10 tables across 8 DDL files (a 9th file, audit_logs, is added during the build at Step 6.2). **Do not modify the DDL files; they are source of truth for schema.** Use Alembic migrations for any schema change after the initial wrap. All tables live in the schema named by the `DB_SCHEMA` env var (`core` on local; per-environment configurable per D-15). DDLs themselves are unqualified; tables resolve to the configured schema via `search_path`.

| Order | File | Tables | Tenant-scoped? | RLS? |
|---|---|---|---|---|
| 1 | shared_utilities_v1.sql | (extensions, functions, shared enums) | n/a | n/a |
| 2 | lookups_v1.sql | lookups | No (platform-global) | No |
| 3 | platform_users_v1.sql | platform_users | No (platform-global) | No |
| 4 | tenants_v3.sql | tenants | Self (id IS the tenant_id) | Yes |
| 5 | tenant_users_v1.sql | tenant_users | Yes | Yes |
| 6 | org_nodes_v2.sql | org_nodes | Yes | Yes |
| 7 | stores_v5.sql | stores | Yes | Yes |
| 8 | rbac_v2.sql | permissions, roles, role_permissions, user_role_assignments | Mixed | Yes on assignments |
| 9 | (added during build) audit_logs_v1.sql | audit_logs | Yes (nullable for GLOBAL scope) | Yes |

---

## Code conventions and structure

> **These are starting points based on common patterns, not contracts.** Use them as sensible defaults. If during the build you find a different layout, naming, or pattern that fits the actual code better, surface it before continuing. Common reasons to revise: a module turns out to be heavier than expected, a layer feels artificial, a folder ends up empty or has only one file. Flag these and propose a revision rather than working around the structure.

### Repository structure (starting point)

```
admin-backend/
├── pyproject.toml
├── docker-compose.yml
├── alembic.ini
├── migrations/
│   └── versions/
├── db/
│   ├── raw_ddl/                  # 8 source DDL files (read-only). 9th added at Step 6.2.
│   └── seeds/
│       ├── 00_bootstrap.sql      # Bootstrap platform_user (system actor)
│       ├── 01_lookups.sql        # All lookup category data
│       ├── 02_rbac_static.sql    # Roles, permissions, role_permissions
│       ├── 03_customer_data_dev.sql   # Generated from dev Excel
│       └── README.md
├── src/admin_backend/
│   ├── __init__.py
│   ├── main.py                   # FastAPI app entrypoint
│   ├── config.py                 # Pydantic Settings, env vars
│   ├── db/
│   │   ├── engines.py            # SQLAlchemy engine creation
│   │   └── session.py            # get_tenant_session dependency
│   ├── auth/
│   │   ├── context.py            # AuthContext type
│   │   ├── stub.py               # StubAuthClient
│   │   ├── auth0.py              # Placeholder for production
│   │   └── testing.py            # make_test_jwt helper
│   ├── middleware/
│   │   ├── auth.py
│   │   └── audit_context.py
│   ├── models/                   # SQLAlchemy ORM models
│   ├── repositories/             # Data access; tenant_id-aware. One Repo per resource.
│   ├── routers/v1/               # FastAPI routers
│   ├── schemas/                  # Pydantic request/response models
│   └── errors.py                 # Exception hierarchy + HTTP mapping
├── tests/
│   ├── conftest.py               # Pytest fixtures
│   ├── unit/
│   ├── integration/
│   └── e2e/
├── scripts/
│   ├── check_setup.sh            # Pre-flight checks (run at start of every task)
│   ├── apply_seeds.sh            # Run all seed SQL files in order
│   ├── excel_to_seed_sql.py      # Convert Excel customer data → SQL inserts
│   └── smoke_test.py             # Cross-tenant isolation + FK + CHECK assertions
├── k8s/
│   ├── dev/
│   └── prod/
├── docs/         
│   ├── architecture.md           # System narrative; loaded by Claude Code at session start
│   ├── architecture_RBAC.md      # RBAC subsystem reference; loaded by Claude Code for Stage 2+ endpoint work
│   ├── api-contract.md           # Currently template state per D-28; locked at Step 2.0
│   ├── data-load.md              # (planned) How to manually populate tables
│   ├── runbook.md                # (planned) Deploy / rollback / common errors
│   ├── auth.md                   # (planned) JWT minting, stub vs Auth0
│   ├── gcp-provisioning-runbook.md  # (planned)
│   └── post-launch-backlog.md    # (planned; intended at Step 10.2)
├── .env.example
├── CLAUDE.md
├── BUILD_PLAN.md
└── README.md
```

### Code naming (starting point)

| Element | Convention |
|---|---|
| Module files | snake_case |
| SQLAlchemy models | PascalCase, singular: `Tenant`, `Store` |
| Pydantic schemas | PascalCase, suffix by purpose: `TenantRead` |
| Repository classes (data access layer) | PascalCase, suffix `Repo`: `TenantsRepo` |
| Functions | snake_case, verb-led: `get_tenant_by_id` |
| Constants | UPPER_SNAKE_CASE |
| Test files | `test_<module>.py` |
| Test functions | `test_<behaviour>` |
| Custom exceptions | PascalCase, suffix `Error`: `TenantNotFoundError` |

**Note on Repository pattern.** Each "Repo" class owns SELECT queries for one table. Located in `src/admin_backend/repositories/`. The "Repo" suffix is short for "Repository" (a standard data-access pattern that separates DB queries from HTTP handlers). One Repo class per resource (tenants, stores, platform_users, etc.). Roughly 9-11 Repo classes for v0.

**Note on mypy strict scope.** `check_setup.sh` runs `mypy --strict src/admin_backend` only (73 source files; gates clean). Broader scopes (`tests`, `scripts`) carry ~625 pre-existing errors as of commit `d7456ad`; tracked as latent debt for a dedicated cleanup commit. Step-level verification harnesses use the narrower scope to avoid surfacing the same noise per-step.

**Note on PG enum columns.** When declaring a SQLAlchemy ORM model column whose live database type is a named PG enum (e.g., `tenant_user_status_enum`, `tenant_tier_enum`), the column MUST be declared as `PG_ENUM(..., create_type=False, native_enum=True)`, never as `Text` or `String`. Postgres does not implicitly cast varchar to a named enum type, so a string-column declaration produces query errors like `operator does not exist: tenant_user_status_enum = character varying` on any WHERE / comparison. The `create_type=False` flag is also load-bearing: the DDL has already created the enum type, and a second declaration would conflict. This applies to full ORM models (Step 3.1's Tenant model is the canonical example) and to lightweight stubs (Step 3.3's `_lightweight_stubs.py`). Surfaced as a Step 3.3 amendment when the TenantUser stub initially used `Text`; documented here so Step 4.5 (Store), Step 5.2 (TenantUser full model), Step 5.3 (OrgNode), Step 6.1 (RBAC), and Step 6.2 (audit_logs) don't rediscover the trap.

**Note on batch-by-key response envelope.** Step 3.6's `GET /api/v1/lookups` returns `{lookups: {list_name: [items]}}` — a top-level wrapper around the map, NOT a bare top-level map. D-30 (list-only envelope) doesn't directly apply since this isn't a list response, but the wrapping is intentional: it leaves room for cross-cutting metadata (e.g., `cached_at`, `version`, `partial: true`) at the top level later without breaking the contract. **Future batch-by-key endpoints follow this same envelope pattern; do not return a bare map at top level.**

**Note on D-30 exception for singleton-resource responses.** Step 5.3's E2 endpoint `GET /api/v1/tenants/{tenant_id}/org-tree` is a deliberate D-30 exception. The org-tree is a singleton resource for the tenant, not a paginatable collection; the response shape is `{tenant_id, tenant_name, stats, tree}`, NOT the standard `{items, pagination}`. E3 (`/org-nodes/{node_id}/children`) follows D-30 normally — it's a paginated collection. The exception is captured here as a one-line convention rather than a new D-XX entry because D-30's "list-only envelope" framing already accommodates per-endpoint exceptions where the resource isn't a collection. **Future singleton/structured tenant-scoped resources should follow E2's pattern: a flat envelope with the resource fields at the top level, plus any cross-cutting metadata (`stats`, etc.) as named siblings.**

**Note on seed Excel shape.** The dev seed Excel (`data/ithina_dev_seed_data.xlsx`) is a **seeding mechanism, not a source of truth**. The DDL is the source of truth for the schema; the seed loader is a tool that transforms a convenient authoring format into DB rows. Three implications follow.

(1) **System-concern columns may be synthesised at load time, not carried in the Excel.** Step 3.5's `tenant_module_access` loader synthesises the three NOT NULL audit-actor FKs (`enabled_by_user_id`, `created_by_user_id`, `updated_by_user_id`) by looking up Anjali by email at load time, because audit-actor identity is a platform-management concern that adds no information per row and would conflate two concerns in the Excel. (2) **When schema requires a value the Excel doesn't have, prefer synthesising in the loader rather than editing the Excel.** Editing the Excel adds maintenance burden and forces the data author to think about system-side concerns; synthesising in the loader keeps each layer's concerns clean. (3) **Step 7.3.1's customer-data tool reverses this principle.** That tool's input is a partially-filled `Ithina_data_entry_template.xlsx` from a real tenant; the customer's data IS the source of truth. Synthesis is wrong there; the customer's row goes in verbatim, with stronger validation, per-row error reporting, and idempotent UPSERT. The two tools share shape but their authority models invert. Don't conflate them.

**Note on the v0 auth model.** The router-layer auth check is **binary user_type-based** (PLATFORM vs TENANT) plus RLS at the DB layer; it is NOT role-based. Three postures cover all v0 endpoints:

- **PLATFORM-only endpoints** (e.g., `/api/v1/platform-users` from Step 5.1) gate via an explicit `_require_platform_auth(auth)` call at the top of each handler. The gate raises `PlatformAccessRequiredError` (403, code `PLATFORM_ACCESS_REQUIRED`) for any non-PLATFORM caller. Used for resources where no tenant boundary applies (no RLS) and only Ithina staff have a legitimate read.
- **Multi-user-type endpoints with RLS** (e.g., `/api/v1/tenants` from Step 3.3, `/api/v1/lookups` from Step 3.6, `/api/v1/tenant-users` from Step 5.2, `/api/v1/tenants/{id}/org-tree` from Step 5.3) accept both PLATFORM and TENANT JWTs without an explicit gate. Visibility is scoped by RLS via the session GUCs set by `get_tenant_session` — PLATFORM sees all rows via D-29's OR-branch; TENANT sees only rows matching `app.tenant_id`. Step 5.2's `/api/v1/tenant-users` is the canonical instance: an optional `?tenant_id=X` query param provides application-layer narrowing for PLATFORM callers who want to scope a list view to a single tenant; for TENANT callers the filter is functionally redundant (RLS already scopes) but harmless — a non-matching value just intersects to empty rather than disclosing other-tenant rows.
- **Multi-user-type endpoints with app-layer audience filter** (Step 6.1's `/api/v1/roles`, `/api/v1/roles/{id}/permissions`, `/api/v1/permission-matrix`) accept both PLATFORM and TENANT JWTs but the underlying tables are platform-global (no RLS — `roles`, `role_permissions`). Visibility is enforced at the application layer via the `audience` column. The router computes `audience_filter` from `AuthContext.user_type` (TENANT JWTs -> `'TENANT'`; PLATFORM JWTs -> `None`) and threads it through the Repo as an optional argument. Distinct from RLS scoping (DB layer) but follows the same anti-information-disclosure intent: cross-audience misses surface as 404 (RLS-as-404 parallel per D-17), not 403. The convention pattern is `_audience_filter_for(auth)` helper at the router + `audience_filter: str | None` arg on the Repo's relevant methods. `/api/v1/permissions` is exempt — the catalogue is reference data and both user types see all rows. **Future non-RLS tables that need user-type-based visibility (none expected in v0) follow this same pattern.**

No router-layer permission check (e.g., "does this user have ADMIN.USERS.VIEW") in Stage 1. RBAC seed data exists (Step 3.5) and the catalogue is readable from Step 6.1, but per-permission enforcement on writes/handlers lands at Stage 2 (Section 6.9). Future PLATFORM-only endpoints inherit Step 5.1's gate pattern (`_require_platform_auth(auth)` at handler-top); future RLS-scoped endpoints follow Step 5.2's RLS-only pattern; future non-RLS audience-segmented endpoints follow Step 6.1's `_audience_filter_for(auth)` pattern.

**Cross-tenant access by TENANT users on multi-user-type endpoints surfaces as 404, not 403** (RLS-as-404 per D-17). A TENANT-A user requesting TENANT-B's `user_id` receives 404 because RLS filters the row out before the handler sees it; returning 403 would disclose existence. The two load-bearing tests are `test_a2_tenant_jwt_returns_403_platform_access_required` in `tests/integration/test_platform_users_router.py` (PLATFORM-only gate) and `test_t9_cross_tenant_detail_returns_404` in `tests/integration/test_tenant_users_router.py` (RLS-as-404 end-to-end). Without the first, a regression dropping the gate would expose Ithina staff identities to tenant users undetected; without the second, a regression in RLS or session-handling could silently expose tenant data across boundaries.

**Shared sort-key validation.** Both `PlatformUsersRepo` and `TenantUsersRepo` raise the same `InvalidSortKeyError` (a ValueError) from `repositories/_errors.py` on unknown sort keys; both routers catch and re-raise as the shared `InvalidSortKeyClientError` from `errors.py` (400, code `INVALID_SORT_KEY`). Future Repos with a sort param reuse these classes — sort-key validation is the same concern across resources, so the classes don't get duplicated per-Repo. Promoted to shared modules at Step 5.2 when the second consumer arrived.

**Note on raw `text()` SQL — schema-qualify ALL non-public identifiers.** Every raw `text()` SQL string — app code, test code, and Alembic migrations — MUST schema-qualify every identifier that lives in a non-public schema. This applies to:

- Tables (`{schema}.tenants`, not `tenants`)
- Enum types in CAST (`CAST(:x AS {schema}.foo_enum)`)
- Enum array types (`CAST(:x AS {schema}.foo_enum[])`)
- Functions, procedures (`{schema}.my_func(...)`)
- Sequences (`{schema}.my_seq`)
- Composite/row types (`{schema}.my_type`)
- Domains (`{schema}.my_domain`)
- Identifiers inside plpgsql function bodies, including trigger functions — the function body's references resolve via the calling session's `search_path` at trigger-fire time

Use `get_settings().db_schema` interpolation in app/test code; `SELECT current_schema()` captured at migration-apply time in Alembic migrations.

- **Rule.** Tables, types, functions, sequences in raw SQL: `f"FROM {schema}.tenants"`, `f"CAST(:x AS {schema}.tenant_status_enum)"`, never the unqualified form. The SQL must work regardless of session `search_path`.
- **Reason.** The engine's connect-time hook sets `SET search_path TO {db_schema}, public` on every new physical connection (`db/engine.py:72-76`). It works locally pre-strip because the role-default `search_path` also included the schema, masking any failure of the hook. It does not always mask reliably on Cloud SQL (pool-reuse semantics + `RESET ALL` on connection return). ORM queries don't have this problem because every model's `__table_args__["schema"]` injects the schema at SQL render time; raw `text()` queries inherit no such injection.
- **Precedent.** `repositories/permission_matrix.py:101-128` (Step 6.1) — `schema = get_settings().db_schema` resolved per-call, interpolated as `f"FROM {schema}.permissions"` etc. F-string is injection-safe because `db_schema` is field-validated as a Postgres identifier at Settings construction (`config.py:_IDENTIFIER_RE`).
- **Anti-patterns.** `repositories/dashboard.py` Step 6.5 → fixed at Step 6.5.1 (unqualified table names). `auth/permissions.py` + 3 other files at Step 6.10.1 deploy → fixed at commit `dd496bd` (unqualified enum types in `CAST` and `ANY(CAST(... AS …[]))`). Trigger function bodies created by migration `3e05299cb533` → fixed by migration `a0982a86985b` (commit `1516484`). Test fixtures and test bodies → fixed at commit `6204fbd`.

**Local DB protection.** The local DB role `user_admin_backend` MUST have its `rolconfig` stripped (no role-default `search_path`). This makes local behave identically to cloud, so unqualified identifiers fail at pytest time instead of cloud-deploy time. `scripts/check_setup.sh` asserts this — if a future operator runs `ALTER ROLE user_admin_backend SET search_path = core, public`, check_setup fails and the mask is detected before it costs another cloud cascade. Restoration: `ALTER ROLE user_admin_backend RESET search_path;`.

**Detection coverage gap.** pytest + the role strip catches the vast majority of CSD-03 bugs at pytest time, but NOT 100%. Code paths not exercised by tests, dynamic SQL constructed at runtime, and new plpgsql function bodies introduced in migrations may still slip past. A static-analysis detection layer (greps `src/`, `tests/`, `migrations/versions/` for unqualified identifiers in `text()` literals, wired into `check_setup.sh`) would close the remainder. Deferred pending a fourth recurrence per the WORKFLOW.md detection-trigger rule.

**Recurrence record (three layers, three commits in the 6.10.1 → 6.11.2 deploy arc):**

| Step / commit | Layer | Identifier class | Sites |
|---|---|---|---|
| 6.5.1 (pre-arc) | app code (dashboard) | unqualified tables (`tenants`, `tenant_module_access`) | 2 raw-SQL queries in `repositories/dashboard.py` |
| `dd496bd` | app code | unqualified enum types in `CAST` and `ANY(CAST(... AS …[]))` (`user_role_assignment_status_enum`, `permission_scope_enum`, `actor_user_type_enum`, +7 more) | 40 sites across 4 files (`auth/permissions.py`, `auth/anchor_deps.py`, `repositories/tenants.py`, `repositories/tenant_users.py`) |
| `1516484` | plpgsql | trigger function bodies (`enforce_tenant_role_audience`, `enforce_platform_role_audience`) — unqualified `role_audience_enum` + `roles` table inside the body, resolved via search_path at trigger-fire time | 2 function bodies via Alembic migration `a0982a86985b` |
| `6204fbd` | test code | test-layer raw SQL in conftest fixtures + 8 test files; exposed by stripping the local DB role-default `search_path` that had been masking the bug | 55 sites across 9 files |

If a fourth recurrence happens with another identifier class (the rule didn't catch it), the rule is not the problem — add a CI grep / pre-commit hook.

Future raw-SQL Repos: f-string-interpolate `{schema}` per-call. Tests guard the contract by clobbering search_path before calling the Repo (see `test_dashboard_router.py::test_x2_*` and `test_rbac_router.py::test_m6_*`).

**Note on gate retrofit — update ALL test surfaces in the same commit.** When a step changes gate audience, scope, or permission tuple on existing endpoints (e.g., Step 6.9.3.2's audience-retrofit on `/tenants` endpoints), the change MUST update three test surfaces in the same commit:

1. pytest integration tests (assertion logic)
2. `scripts/smoke_curl.sh` (expected status codes)
3. `scripts/test_endpoints.sh` + `scripts/test_endpoints_cloud.sh` (expected status codes for ALL caller kinds, not just the primary kind being changed)

Step 6.9.3.2 updated pytest but not the shell test scripts; commit log noted "comment lines updated; no assertion logic changes; curl strings already match." The shell scripts had stale `200` expectations for TENANT callers on `/tenants` endpoints, which surfaced as 29 failures in the exhaustive cloud test after the CSD-03 cascade was cleared. Cost ~2 hours of debugging to confirm the 29 failures were stale expectations, not new bugs.

When in doubt: if the API behavior change is intentional, the test expectations are the documentation of intent. Stale expectations are a documentation bug masquerading as a code bug.

**Note on cloud verification — smoke is necessary, not sufficient.** After every cloud deploy, run BOTH:

```
./scripts/smoke_curl.sh <cloud_url>           # ~25–32 critical-path checks
./scripts/test_endpoints_cloud.sh <cloud_url> # ~325 broad-coverage checks
```

Smoke passing (e.g., 32/32) is the gate to call the deploy "functional." Exhaustive passing is the gate to call the deploy "complete." Different bars.

v0.1.13 deploy: smoke 32/32 passed; exhaustive surfaced 29 failures, all confirmed as stale test expectations (per the gate-retrofit discipline above), not real bugs. Smoke wouldn't have caught the test-script staleness; exhaustive did. Both serve distinct purposes.

**Note on label resolution (Step 6.7 forward).** Endpoints from Step 6.7 onward return enum-coded fields with sibling `<field>_label` strings, resolved server-side via LEFT JOIN against the relevant `lookups.list_name` with `COALESCE(display_name, code::text)` fallback. Always present (never null where the source field is non-null). Older endpoints (`/tenants`, `/tenant-users`, `/platform-users`, `/roles`, `/org-tree`, `/dashboard/*`) retain bare enum codes; frontend handles client-side resolution against `/lookups`. **This asymmetry is intentional** — retrofitting older endpoints is out of scope unless explicitly prompted; new endpoints follow the new rule. Precedent: `routers/v1/modules_access.py` (`module_label`, `tier_label`, `status_label`); `repositories/modules_access.py` for the JOIN shape. Step 6.1's permission-matrix predates the codified rule but uses the same pattern.

**Note on dependency factories (Step 6.9.2 forward).** The `require(module, resource, action, scope)` factory at `src/admin_backend/auth/permissions.py` establishes the dependency-factory pattern in this codebase. A factory is a regular Python function that returns a Depends-injectable callable: `def require(...) -> Callable[..., Awaitable[None]]: async def gate(...) -> None: ...; return gate`. Endpoints declare `Depends(require(MODULE, RESOURCE, ACTION, SCOPE))`. FastAPI documents this shape; no precedent existed in v0 before Step 6.9.2. The returned callable participates in FastAPI's dependency-resolution graph normally — it can declare its own `Depends(...)` parameters (`auth`, `session`) and FastAPI resolves them once per request (cached) before the gate body runs. Raising `PermissionDeniedError` (or any `AdminBackendError` subclass) from inside the gate body propagates through `@app.exception_handler(AdminBackendError)` and produces the standard JSON error envelope. Future parameterised dependencies follow the same factory shape — Step 6.9.3.2's per-resource anchor dependencies (`get_store_anchor`, `get_tenant_anchor`, etc.) are the next consumer.

**Note on org-hierarchy coupling (Step 6.9.3.1 forward).** The org-tree hierarchy is hardcoded in two in-repo sources that must stay in sync:

1. DDL `org_node_type_enum` in `db/raw_ddl/Ithina_postgres_SQL_DDL_org_nodes_v2.sql` (7 values: TENANT, BUSINESS_UNIT, HQ, COUNTRY, REGION, STORE, DEPARTMENT).
2. `_SCOPE_CASCADE_ORDER` tuple in `src/admin_backend/auth/permissions.py` (8 entries: GLOBAL at position 0 representing the implicit Platform cascade root above any tenant, plus the 7 `org_node_type_enum` values in order).

The cascade rule itself (a permission granted at level N implies the same permission at every level below N) is the design intent of Step 6.9.3.1; the canonical statement lives in the design conversation captured in `prompts/step-6_9_3_1-scope-cascade-2026-05-13.md`.

When the org hierarchy changes (add / remove / reorder levels):
- Update both in-repo sources together.
- Unit test `test_scope_cascade_order_matches_canonical` catches local drift in the Python tuple; `test_scope_cascade_order_includes_all_enum_values` catches the case where `PermissionScope` expands without updating the tuple. Neither catches DDL drift (changes to `org_node_type_enum` without matching tuple updates).
- The `permission_scope_enum` DDL is a SEPARATE enum from `org_node_type_enum`; expanding `permission_scope_enum` to include a new level (e.g., REGION) is a Step 6.9.3.1+-era migration tracked as FN-AB-28. Until that migration, levels in `_SCOPE_CASCADE_ORDER` that aren't in `PermissionScope` (`BUSINESS_UNIT`, `HQ`, `COUNTRY`, `REGION`, `DEPARTMENT`) are inert — no catalogue rows reference them, and `_satisfying_scopes_for_sql` filters them out before binding.

**Note on gate allowlist coupling (Step 6.9.3.2 forward).** Two structures together define "every authenticated route is either gated or knowingly exempt":

1. `Depends(require(M, R, A, S, *, anchor_dep=None))` at each gated route's signature — see `routers/v1/tenants.py:121-128` for the canonical no-anchor shape, `routers/v1/tenants.py:213-222` for the anchored shape.
2. `GATE_EXEMPT_PATHS: frozenset[str]` in `src/admin_backend/auth/gate_allowlist.py` — explicit hand-maintained list of paths exempt from the gate, distinct from `PUBLIC_PATHS` (which are exempt from auth altogether: health, ready, docs, openapi.json, metrics).

`tests/integration/test_gate_discipline.py` enumerates every FastAPI `APIRoute` and asserts the disjoint trichotomy: route is either (a) gated (carries a `Depends(require(...))` with the marker attribute `__permission_gate__`), (b) in `GATE_EXEMPT_PATHS`, or (c) in `PUBLIC_PATHS`. The meta-test fails the run if a new route doesn't satisfy one of the three.

When adding a new route:
- If the route is data-bearing and authenticated, add `Depends(require(...))`. Pick `anchor_dep` if the route binds to a specific resource (`/tenants/{tenant_id}`, `/org-nodes/{node_id}/children`, etc.).
- If the route is reference data (lookups, role catalogue) or caller-state (`/me/*`), add the path to `GATE_EXEMPT_PATHS` with a comment explaining why. The 5 reference-data exemptions at HEAD are tracked as FN-AB-30 for posture revisit.
- Never add `# type: ignore` or skip the meta-test to make a new route pass. The meta-test is the only place that enforces this discipline.

The marker attribute `gate.__permission_gate__ = PermissionGateInfo(...)` makes gate introspection cheap (one attribute read per route in the meta-test); the alternative (parsing the inner function's closure for cell values) is fragile and Python-version-dependent. Future router authors should NOT replace the inner-function-marker pattern with closure introspection.

**Note on investigation-vs-implementation discipline (Section 6.9 forward).** Investigation reports describe codebase state at a point in time; implementation steps verify each claim against actual code before acting. Stale findings surface via Surface-and-stop triggers in the prompt, not silent workarounds. Section 6.9 caught 6 such cases:

1. Step 6.9.1 F-REPO-4: investigation cited a type-drift on `RoleAssignmentsRepo.list_tenant_assignments`; the annotation was already correct since Step 6.8.2 (commit `de9a39cd`). Implementation dropped the no-op from scope; operator confirmed at pre-flight.
2. Step 6.9.1 Caution #6: prompt cited `module_access_status_enum` values `SUSPENDED` and `NOT_PROVISIONED`; live enum has only `ENABLED` and `DISABLED`. Test fixture used `DISABLED`; mismatch documented in the current-state entry.
3. Step 6.9.2 F-GATE-2: investigation cited wrong file location for `_audience_filter_for`. Implementation read the actual router file before threading the helper.
4. Step 6.9.3.1 Caution #2: prompt claimed Postgres `CAST(... AS permission_scope_enum[])` accepts out-of-enum strings; empirically rejects them at CAST time. The `_satisfying_scopes_for_sql` filter handles the constraint.
5. Step 6.9.3.1 frontend-doc reference: prompt cited `docs/Ithina_Admin_Frontend.md` section 5.5 as the cascade-spec source; that file isn't in this repo at HEAD. Canonical statement moved into the design-conversation prompt file bundled with the commit.
6. Step 6.9.3.2 commit review: final report and FN-AB-31 both contained `.GLOBAL` for `/role-assignments` when the locked design decision and shipped code both used `.TENANT`. Operator review caught the propagation before merge.

Convention: implementation prompts include a Surface-and-stop section listing triggers where Claude Code pauses before silent workarounds. Operator reviews surfaced findings; either confirms the prompt's claim or accepts the empirical correction.

**Note on smoke and endpoint test scripts.** Three scripts in `scripts/` update in lockstep when new endpoints land:

- `smoke_curl.sh` — quick local smoke (counted assertion shape; WHAT'S CHECKED header tracks the count).
- `test_endpoints.sh` — full per-endpoint local matrix (4 callers × matrix entries).
- `test_endpoints_cloud.sh` — same matrix against the deployed Cloud Run service.

Adding a new endpoint without updating these means smoke PASS counts no longer reflect actual surface coverage. The mandatory-gate-discipline test (`tests/integration/test_gate_discipline.py`) catches missing gates structurally; the smoke scripts catch missing per-endpoint behavioural coverage in CI and post-deploy. Convention: any Stage 2+ commit adding a new endpoint includes matching `smoke_curl.sh` + `test_endpoints.sh` updates in the same commit. `test_endpoints_cloud.sh` updates by inspection and is verified post-deploy.

**Note on documentation-vs-locked-decisions sanity check.** Implementation reports (the final report from Claude Code per step) verify documented decisions against the actual shipped code, not against the prompt's claims. Three specific places drift can land undetected:

- FN-AB entries: cite real file paths, real tuple values, real code references. Paraphrasing from memory drifts from the lock.
- Final report summary tables: cross-check each row against the actual handler/router/migration file before claiming "matches Phase X lock".
- CLAUDE.md current-state entries: copy locked tuple/value strings verbatim from the design conversation, not retyped.

Caught example: Step 6.9.3.2's final report and the original FN-AB-31 wording both contained `.GLOBAL` for `/role-assignments` when the locked design decision and shipped code both used `.TENANT`. The discrepancy propagated through report + CLAUDE.md before operator review. Convention: prompts include a "Verify against locked decisions" item in the report template; operator review treats locked-decision deviations as Surface-and-stop, not silent corrections.

**Note on cross-repo references.** The frontend product spec `Ithina_Admin_Frontend.md` lives in the frontend repo, not in this admin-backend repo. Backend prompts and design conversations cite it cross-repo as `"frontend repo, Ithina_Admin_Frontend.md, section X.Y"` rather than copying content. Sections frequently cited: 5.5 (cascade rules), 7.2.11 (tenant administration), 7.3 (organization tree). The frontend doc remains authoritative for product intent; backend cites by section reference. Revisit if cross-repo references become friction during Stage 2 / Stage 3 write-endpoint design.

**Note on cleanup-fixture ordering (write-endpoint integration tests).** When an integration test creates DB rows via one fixture path and cleans up via a separate cleanup fixture, the **argument order in the test signature is load-bearing** because pytest tears down fixtures in reverse of setup, and setup is argument-order-determined among same-depth deps.

Three concrete fixture-order patterns surfaced at Step 6.11.1 and 6.11.2:

1. **Repo-write tests (`test_tenants_repo_writes.py`)**: signature `repo, make_platform_user, cleanup_tenants, platform_session`. `cleanup_tenants` DELETEs tenants created via `repo.create(...)` inside `platform_session`'s still-open transaction. Setup order: `make_platform_user` → `cleanup_tenants` → `platform_session`. Teardown reverse: `platform_session` first (COMMITs the test's transaction so rows become visible) → `cleanup_tenants` (sees committed rows; DELETEs them) → `make_platform_user` (no FK refs left to platform_users; DELETE succeeds). Reordering breaks teardown silently — leaked rows surface as later-test name-collision 409s.

2. **Router-write tests (`test_tenants_writes_router.py`)**: signature `..., make_platform_user, cleanup_tenants_router, ...`. The TestClient's session is request-scoped (FastAPI commits per request), so each `POST /api/v1/tenants` row is committed before the next test setup phase. `cleanup_tenants_router` only needs to be set up AFTER `make_platform_user` so its teardown runs FIRST (DELETEs tenants → make_platform_user can then DELETE platform_users with FK refs gone).

3. **Multi-row test chain (`tenant_owner_jwt_factory` consumers)**: when a test uses both `make_tenant` directly AND `tenant_owner_jwt_factory` (which transitively pulls `make_tenant_user`, `make_org_node`, etc.), `make_tenant` must be listed BEFORE `tenant_owner_jwt_factory` in the test signature. The factory's sub-fixtures (children of the tenant) tear down FIRST; `make_tenant` (parent) tears down LAST — FK refs gone before tenants DELETE. AUD-1 in Step 6.11.2's router tests surfaced this ordering when the original arg order put `tenant_owner_jwt_factory` first.

When adding a new cleanup-style fixture: document the required argument order in the fixture's docstring with reference to (a) which open-transaction fixture must commit before cleanup runs and (b) which FK-referencing fixtures must DELETE before cleanup's target table. The fixture-order discipline is the only structural safeguard against test-pollution; pytest gives no warning when ordering is wrong.

**Note on `make_tenant` and anchor reachability.** Tests exercising endpoints gated with `anchor_dep=get_tenant_anchor` (or any anchor dep that resolves an org_node) must construct the tenant with `make_tenant(..., with_root=True)`. The default `with_root=False` creates only the tenant row; without a TENANT-type root org_node, the anchor dep returns 404 before the gate body fires, masking test intent. The pattern surfaced twice (6.9.3.2 cleanup audit observation; Step 6.15 surface-and-stop finding #4) before being promoted to a fixture parameter at the workflow-amendments commit; do not regress to manual `make_tenant + make_org_node(node_type='TENANT')` pairing in test files. The retired helper lived at `tests/integration/test_module_access_writes_router.py::_make_tenant_with_root` if a future contributor needs the historical shape.

### Per-endpoint documentation

Every endpoint that lands in the build gets a markdown documentation file at `docs/endpoints/<resource>.md`. Format follows the canonical example at `docs/endpoints/_example_tenants.md` with 8 fixed sections per endpoint:

1. Endpoint summary (method, path, description, who can call)
2. Request (auth, path params, query params, body)
3. Response 200 (full shape with sample, field-by-field reference)
4. Response codes (error table with sample bodies)
5. Behaviour notes (RLS scope, sort, pagination, edge cases)
6. Example calls (curl)
7. Sample integration code (TypeScript snippet)
8. Implementation reference (file pointers)

Claude Code produces this file as part of each endpoint-building step. The OpenAPI spec at `/v1/openapi.json` is the machine-readable source of truth (auto-generated by FastAPI); the markdown doc is the human-readable companion explaining behaviour, edge cases, and integration intent that don't fit cleanly into OpenAPI.


### Error model (current shape after Step 2.3 refactor)

Two-tier hierarchy with HTTP-response mapping:

```
AdminBackendError                       (base)
├── ClientError                         (4xx; specifics OK in response)
│   ├── AuthMissingError                → 401, code AUTH_MISSING
│   ├── AuthInvalidError                → 401, code AUTH_INVALID
│   └── InvalidTenantIdError            → 401, code AUTH_INVALID
└── ServerError                         (5xx; ALWAYS generic to client)
    └── AppRolePrivilegeError           → 500, code INTERNAL_ERROR
```

ClientError subclasses set their own `public_message`, `http_status`, and `code`: the caller knows what they sent, so telling them what was wrong with it is not disclosure.

ServerError subclasses MUST NOT override `public_message` or `code`. The response shape is always `{"code": "INTERNAL_ERROR", "message": "An internal error occurred", "request_id": "..."}` regardless of subclass. Subclass-specific information goes to the internal log only via the constructor's `internal_message` and `**context` kwargs. Anti-information-disclosure: an attacker probing the auth or DB layer should learn nothing about the internal failure shape from the response body.

The future categories left in the original sketch (ValidationError, NotFoundError, ConflictError, CrossTenantAccessError) will land under ClientError as Steps 3.x onward need them. Per D-17, RLS-blocked / missing-row reads must surface as 404; that's handler-layer logic, not middleware.

Error response shape:

```json
{
    "code": "AUTH_MISSING",
    "message": "Authentication required",
    "request_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

Headers carry `X-Request-Id` on every response (set by AuditContextMiddleware on success and by the FastAPI exception handler / auth middleware on error). Same UUID appears in the per-request log line via the `admin_backend.requests` JSON logger.

Two paths build error responses, both via the shared `errors.build_error_payload()` helper so they stay in sync:

1. **FastAPI `@app.exception_handler(AdminBackendError)`** — for exceptions raised inside route handlers.
2. **Auth middleware in-line catch** — for exceptions raised inside `BaseHTTPMiddleware.dispatch`. Starlette does NOT route middleware-raised exceptions through the FastAPI exception handler (the handler runs INSIDE the middleware stack, so anything raised in user middleware propagates UP past it to ServerErrorMiddleware, which would otherwise return a generic 500).

---

## Environment variables

Required at startup (app refuses to boot if missing):

| Variable | Format | Purpose |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://user:pass@host:5432/db` | Master DB connection |
| `DB_SCHEMA` | snake_case identifier, e.g. `core` | Postgres schema where admin-backend tables live; per-environment configurable (see D-15) |
| `JWT_ISSUER` | URL | Expected `iss` claim |
| `JWT_AUDIENCE` | string | Expected `aud` claim |
| `APP_REGION` | `US` or `EU` | Pinned at deployment |
| `ENVIRONMENT` | `local`, `development`, `staging`, `production` | Drives behaviour gates |
| `LOG_LEVEL` | DEBUG / INFO / WARNING / ERROR | Log filter |

Required when stub auth is active:

| Variable | Format | Purpose |
|---|---|---|
| `JWT_PUBLIC_KEY_PATH` | path | Public key for JWT verification |
| `JWT_PRIVATE_KEY_PATH` | path | Private key for test JWT minting |

Required when Auth0 is active:

| Variable | Format | Purpose |
|---|---|---|
| `AUTH0_DOMAIN` | hostname | e.g., `ithina.auth0.com` |
| `AUTH0_JWKS_URL` | URL | Cache locally |

Optional:

| Variable | Default | Purpose |
|---|---|---|
| `DB_POOL_SIZE` | 10 | Per-pod SQLAlchemy pool size |
| `DB_MAX_OVERFLOW` | 5 | Per-pod overflow allowance |
| `SERVICE_VERSION` | wheel metadata (`pyproject.toml` `[project].version`) | Reported in `/api/v1/health` and the FastAPI OpenAPI `info.version`. Deployed images set this to the image tag at build time via `docker build --build-arg SERVICE_VERSION=<tag>` or at deploy time via Cloud Run / GKE env override. Default falls back to `0.0.0-dev` if the package metadata is unreadable. |

---

## Testing discipline

### Test pyramid (starting point)

| Layer | Scope | When to write |
|---|---|---|
| Unit | Pure logic, no DB, no network | For pure-logic functions |
| Integration | Real Postgres, real schema, no FastAPI | For repositories |
| Module | FastAPI + Postgres + stub auth | For each endpoint |

### Test DB strategy

- Shared local Postgres test instance running in Docker.
- Per-test transaction rollback. Each test runs inside a transaction that is rolled back at teardown.
- Schema set up once per test session.
- Every test creates its own data via factory fixtures.

### Pytest fixtures (starting point)

| Fixture | Purpose |
|---|---|
| `db_session` | SQLAlchemy session bound to a rolling-back transaction |
| `make_tenant` | Factory that creates a tenant in the test transaction |
| `make_platform_user` | Factory for platform_user |
| `make_tenant_user` | Factory for tenant_user, given a tenant |
| `auth_context_platform` | StubAuthClient context for staff |
| `auth_context_tenant` | StubAuthClient context for a tenant user |
| `client` | TestClient (httpx) bound to the FastAPI app |

### Critical-path tests (must exist for v0)

- Cross-tenant read returns zero rows under RLS, on every endpoint that filters by tenant.
- Tenant mismatch (JWT vs path) returns 400.
- Suspended user JWT returns 401.
- Permission cascade: assignment at Region grants access to descendant Stores' data.
- Org tree descendants query returns correct subtree.
- Audit log filter (search, result, date range) returns expected rows.

### Test discipline rules

- Every code change is followed by writing or updating tests.
- Tests must run and pass before claiming a step is complete.
- If tests fail, investigate and fix. Iterate. Do not escalate routine bugs to the user.
- Escalate only when the failure reveals a design issue, ambiguity, or unanticipated condition.

### Smoke-test posture for state-transition endpoints

When a write endpoint exercises a state-transition matrix
(suspend / activate / similar) and the seed users / rows are in
a state that the matrix rejects, smoke tests SHOULD assert the
rejection path (e.g., 409 INVALID_STATE_TRANSITION), not the
happy path. Happy-path verification belongs in integration tests
where fixture-mutated state can be set up via direct DB write
outside the API surface.

Why: smoke tests verify the wire surface fires correctly (gate
+ anchor + repo + transition matrix end-to-end). Setting up
non-seed state inside smoke leaks implementation detail into
operational scripts and creates maintenance debt every time the
seed changes.

Example (Step 6.10.1, b6b76dd): fresh tenant_users are seeded
INVITED. INVITED → SUSPENDED is rejected by
ck_tenant_users_auth0_sub_consistency. Smoke asserts 409 on
suspend; integration test S1 sets up an ACTIVE user via DB
write and asserts 200.

Applies to: every future lifecycle-touching write endpoint
(6.10.2 platform users; 6.12+ store/role lifecycle if added).

---

## Current state

### Completed

- **Local environment.** Python 3.12.13 (uv-managed), uv 0.11.7, Docker 29.x, Docker Compose v5.1.3, psql 14.22, git 2.34.1, Claude Code 2.1.123.
- **Repo scaffolded.** Src layout at `/home/zorin/ithina-retail/admin-backend`; `pyproject.toml` with core + dev deps; `uv sync` complete; `uv.lock` tracked from Step 2.1 forward per D-25.
- **Local Postgres 15** running in Docker. Container `ithina-postgres`, db `ithina_platform_db`, user `user_admin_backend` (NOSUPERUSER NOBYPASSRLS per Step 1.5 hardening). Schema `core` with role-level `search_path = core, public`. Extensions `ltree` and `pgcrypto` installed in `public`.
- **8 DDL files** in `db/raw_ddl/`. Step 1.3 stress-tested; Step 1.4 applied. Two authorised DDL edits landed during Step 1.3: C1 fix removing the duplicate `actor_user_type_enum` definition, and the `lookups` DDL audit-actor cleanup.
- **Schema state.** 12 application tables, 19 enums, 6/6 multi-tenant tables (tenants, tenant_users, org_nodes, stores, tenant_module_access, tenant_user_role_assignments) with RLS + FORCE + isolation policy, plus 1 platform-global no-RLS new table (`platform_user_role_assignments`). NULLIF wrapper on `current_setting('app.tenant_id', TRUE)` across all 6 policies (Step 2.2a `e59f62d5037d` for the original 5, Step 3.4.5 `cd2a02e452ae` for `tenant_module_access`, Step 6.8.1 `3e05299cb533` for `tenant_user_role_assignments`); see D-27. PLATFORM-visibility OR-branch on all 6 policies (per D-29) — **uniform unconditional form** post-Step-6.8.1 (the IS-NULL-gated form on `user_role_assignments` was retired by the table split per D-34; `user_role_assignments` no longer exists). All NOT NULL `tenant_id` (or NOT NULL `id` for `tenants`); migrations `21e2ad16303a` Step 3.0 (4 tables), `cd2a02e452ae` Step 3.4.5 (`tenant_module_access`), `3e05299cb533` Step 6.8.1 (`tenant_user_role_assignments`). **Step 6.1 (`90cd038ae618`) narrowed `module_enum` (4 values: ADMIN, PRICING_OS, PERISHABLES_ASSISTANT, PROMOTIONS_ASSISTANT) and `permission_scope_enum` (3 values: GLOBAL, TENANT, STORE) — forward-only structural cleanup; downgrade raises NotImplementedError per the project's irreversible-cleanup convention. `resource_enum` and `action_enum` already matched the locked vocabulary; no migration needed for those. The DDL files in `db/raw_ddl/` remain frozen at the as-shipped state per the workflow convention.** **Step 6.6 (`cec8fae734e0`) retired `module_enum` entirely (Path B unification — re-pointed `permissions.module` at the wider `module_code_enum` shared with `tenant_module_access.module`; closes MODULES-EXT). Enum count thus moves from 20 to 19.** Step 6.1 (`22ccfb193cff`) seeded 25 lookup rows for the four enum-display-label categories (`module`, `resource`, `permission_action`, `permission_scope`); Step 6.6 deleted the 4 `list_name='module'` rows post-unification (the `module_code` list_name covers all six modules with display_order 1-6). **Step 6.7 (`2fdc4bc9f4cb`) re-ordered the `module_code` rows to match the locked screenshot sequence — ROOS=1, GOAL_CONSOLE=2, PRICING_OS=3, PERISHABLES_ASSISTANT=4, PROMOTIONS_ASSISTANT=5, ADMIN=6 — and idempotently re-seeded `tenant_tier` (4 rows) and `tenant_status` (5 rows) lookup rows (no-op INSERTs since Step 3.6 already seeded them).**
- **Alembic wrapped** at Step 1.6. Initial migration `ad8afd429581` embeds all 8 DDLs as raw triple-quoted strings at generation time; `CREATE EXTENSION` stripped (extensions are a setup precondition); generator at `scripts/build_initial_migration.py` for reproducibility. NULLIF amendment is migration `e59f62d5037d` on top.
- **Smoke test** (`scripts/smoke_test.py`) at 74 PASS as of Step 3.4.5. The Step 2.2b 9-row truth table on `user_role_assignments` is unchanged; `test_15_multi_tenant_or_clause_truth_tables` (Step 3.0) covered 4 tables × 9 GUC cells = 36 visibility assertions, extended at Step 3.4.5 to a 5th table (`tenant_module_access`) for 45 cells; `test_16_platform_can_insert_into_multi_tenant_tables` (Step 3.0) covered 4 PLATFORM-INSERT assertions, extended at Step 3.4.5 to a 5th (tenant_module_access). Pre-3.0 (downgrade past 3.0) state correctly shows 16 failures on the original four tables — verified via round-trip.
- **Auth foundation** at Step 2.1. RS256 keypair under `keys/` (gitignored); `pyjwt[crypto]` per D-26 supplies the cryptography backend that PyJWT alone does not; `Settings` (Pydantic v2 `BaseSettings`) with two production-consistency model_validators and a `db_schema` identifier validator; frozen `AuthContext` with identity-only claims per D-24; `StubAuthClient.verify`; `make_test_jwt` and `tamper_token_claim` helpers; 4 typed error classes; 21 integrity tests.
- **DB engine + session bootstrap** at Step 2.2a. Async engine with `prepare_threshold=None` per D-14; connect-time `search_path` hook per D-15; `get_tenant_session(auth, session_factory)` async generator that uses `set_config(name, value, true)` for both `app.tenant_id` and `app.user_type` from `AuthContext` only (source-binding per AI-MT-03); `assert_app_role_no_bypassrls` runtime gate; 15 unit tests including reused-pooled-connection RLS behaviour after COMMIT. Step 2.3 added `app.request_id` as a third session var (NULL outside a request context).
- **Middleware + structured errors + tenant-session Depends provider** at Step 2.3. `AuditContextMiddleware` (outermost) generates `request_id`, captures IP / user_agent, emits one structured INFO log line per request via `admin_backend.requests`. `AuthMiddleware` (middle) extracts JWT from `Authorization: Bearer`, verifies via `StubAuthClient`, populates `request.state.auth: AuthContext`; converts `AdminBackendError` to JSON response inline (Starlette doesn't route middleware-raised exceptions through `@app.exception_handler`). `CORSMiddleware` (innermost) configured from `cors_allowed_origins` setting. Public paths (`/v1/health`, `/v1/ready`, `/v1/openapi.json`, `/v1/docs`, `/v1/redoc`, `/metrics`) skip auth. `errors.py` refactored to `ClientError`/`ServerError` two-tier hierarchy with `http_status` / `public_message` / `code` class attributes; ServerError always returns generic `INTERNAL_ERROR` (anti-information-disclosure). `dependencies.get_tenant_session_dep` bridges the request layer to `get_tenant_session`. `logging_config.configure_logging` sets up stdout JSON logging via `python-json-logger`. `main.py` skeleton: `lifespan` constructs engine + session_factory + auth_client; `create_app` registers middleware and exception handler. 10 integration tests (request-id flow, JSON log shape, anti-injection, anti-information-disclosure on ServerError).
- **Health and readiness endpoints + lifespan finalisation** at Step 2.4. `/v1/health` (liveness, public, no DB) returns `{status, service, version}` and is what Kubernetes will hit to decide kill-and-restart. `/v1/ready` (readiness, public) opens a short-lived `engine.connect()` and runs `SELECT 1`, bounded by a 2-second `asyncio.wait_for` so a hung DB cannot stall the probe; returns 503 / `{"status": "not_ready", "db": "error"}` on any failure. Both endpoints are tagged `meta` and appear in OpenAPI. Lifespan refactored to assign each resource onto `app.state` as it is constructed (settings → engine → session_factory → auth_client) so a startup gate raise leaves partial state inspectable. NotImplementedError message for `AUTH_CLIENT_MODE=AUTH0` updated to reflect that Auth0Client is its own pending step (lands when Auth0 tenant configuration arrives). Shared integration-test fixtures extracted to `tests/integration/conftest.py`. 11 new tests (6 health, 5 lifespan startup-gate); foundation block closed at 57 tests total.
- **PLATFORM-visibility OR-clause back-fill on remaining 4 multi-tenant policies** at Step 3.0 (numerically prior to 3.1; chronologically landed after 3.1 + drift sweep + convention extension — see git log for the actual order). Migration `21e2ad16303a` drops and recreates `tenants_self_access`, `tenant_users_tenant_isolation`, `org_nodes_tenant_isolation`, and `stores_tenant_isolation` with the unconditional PLATFORM OR-branch (`OR current_setting('app.user_type', TRUE) = 'PLATFORM'`, no IS-NULL gate — these tables' tenant_id/id is NOT NULL, so the FN-AB-14 IS-NULL form would never fire). The 5th multi-tenant policy (`user_role_assignments_tenant_isolation`) keeps its FN-AB-14 IS-NULL-gated form because that column IS nullable. Both shapes captured as **D-29** (PLATFORM RLS visibility via policy clause, not BYPASSRLS). Two failure modes the back-fill fixes: (1) PLATFORM sessions now see all rows on these 4 tables (previously zero); (2) PLATFORM sessions can now INSERT into these 4 tables (previously the WITH CHECK predicate evaluated to UNKNOWN). The second is what unblocks Step 3.2's `make_tenant` factory and Step 6.3's seed scripts — the application role is `NOSUPERUSER NOBYPASSRLS` per Step 1.5 and there is no privileged-role escape hatch. Smoke test grew from 24 to 64 PASS via `test_15` (4×9 truth tables = 36 visibility cells) + `test_16` (4 PLATFORM-INSERT assertions). Round-trip verified: pre-3.0 (downgrade) state shows exactly 16 expected failures (12 PLATFORM-visibility + 4 INSERT), post-3.0 shows 64/64. New FN-AB-15 added for the regenerator-staleness foot-gun: `scripts/build_initial_migration.py` would silently emit a stale baseline if rerun against the frozen DDLs, since 3 policy migrations now live in the chain.
- **Tenant ORM model + TenantRead schema** at Step 3.1. First domain step. `src/admin_backend/db/base.py` ships the project-wide SQLAlchemy `DeclarativeBase`. `src/admin_backend/models/tenant.py` maps all 22 columns of `tenants_v3.sql` and defines four `str`-Enum classes (`TenantStatus`, `TenantTier`, `TenantIndustry`, `TenantRegion`). Enum columns use the dialect-specific `postgresql.ENUM` (not generic `sqlalchemy.Enum`, which silently drops `create_type=False` on the postgres dialect impl) with `create_type=False, native_enum=True, values_callable=lambda e: [m.value for m in e]`. `__table_args__["schema"]` resolves from `get_settings().db_schema` per D-15. Audit FKs to `platform_users` are not declared at the SA layer (PlatformUser model lands at Step 5.1; DB enforces the FK regardless). `src/admin_backend/schemas/tenant.py` ships `TenantRead` with `ConfigDict(from_attributes=True)`, audit-actor IDs hidden from response, and `field_serializer(when_used="json")` converting `monthly_revenue_usd` (NUMERIC) to a JSON string. Provisional API response defaults captured as **D-28** (snake_case keys, ISO-8601 with offset, nulls explicit, NUMERIC-as-string; list-response wrapping deferred to Step 3.2/3.3). 13 new unit tests (T1-T6 model, S1-S7 schema); 70 total pytest passes (57 prior + 13 new), no regressions. The 14 `scripts/smoke_test.py` collection errors visible under bare `uv run pytest` are pre-existing pytest-config drift from before Step 3.1 (no `testpaths`/`--ignore=scripts/` configured); functions are named `test_*` so pytest collects them but they take positional args and ERROR rather than failing — out of scope for 3.1. **Amended during Step 3.2:** `id`, `status`, `created_at`, `updated_at` now carry `server_default=FetchedValue()`. Step 3.1 originally set no server-side default to honour D-21's "DDL is single source of truth for `uuidv7()`" rule. That rule is correct for the literal SQL, but SQLAlchemy needs to *know* a DB-side default exists so it omits the column from INSERT (otherwise SA sends explicit NULLs that defeat the DDL DEFAULT and trigger NOT NULL violations on the timestamp columns). `FetchedValue()` declares the existence of a default without redeclaring the SQL — preserves D-21's intent and avoids the FN-AB-13 maintenance trap. Test T6 tightened to assert `isinstance(col.server_default, FetchedValue)` for those four columns. No D-XX entry: implementation correctness within D-21's intent.
- **Tenants router + 3 GET endpoints + canonical endpoint doc** at Step 3.3. First domain endpoints. `src/admin_backend/routers/v1/tenants.py` ships `list_tenants` (`GET /api/v1/tenants`), `tenants_stats` (`GET /api/v1/tenants/stats`), `get_tenant` (`GET /api/v1/tenants/{tenant_id}`). Route order matters: `/stats` declared before `/{tenant_id}` so the static path matches first (FastAPI is first-match-wins). All three handlers depend on `get_tenant_session_dep` so RLS comes for free; per D-17 the detail endpoint surfaces missing/RLS-filtered rows as 404 via new `TenantNotFoundError` (`code: TENANT_NOT_FOUND`). New schemas (`Module`, `Pagination`, `TenantsListItem`, `TenantsListResponse`, `TenantsStatsResponse`, `TenantDetail`) confirm D-28 defaults; new D-30 (response envelope is list-only — `{items, pagination}` for collections, single objects returned directly) and D-31 (response field semantics are append-only). New Repo methods (`list_with_aggregates`, `get_by_id_with_aggregates`, `count_for_stats`) carry the per-row aggregates `num_stores` / `num_users_active` via correlated scalar subqueries — `.correlate(Tenant)` is what scopes the count to each tenant rather than collapsing to platform-wide. Module entitlements come from `_module_entitlements_stub.py` (FN-AB-16) with an xfail-strict tripwire at `tests/unit/test_module_entitlements.py` that forces stub deletion at the same commit as `tenant_module_access` table landing. Lightweight ORM stubs `Store` and `TenantUser` in `models/_lightweight_stubs.py` declare the minimal columns (id, tenant_id, plus tenant_user_status_enum on TenantUser) for the subqueries; the docstring carries an explicit warning never to point Alembic autogenerate at `Base.metadata` while the stubs exist (it would propose ALTER TABLE DROPs for every column the stubs don't declare). API URL prefix is now `settings.api_prefix = "/api/v1"`; `/v1/health`, `/v1/ready`, `/v1/openapi.json`, `/v1/docs`, `/v1/redoc` moved to `/api/v1/...` with the corresponding `tests/integration/test_health.py` + `test_middleware.py` URL updates and the `AuthMiddleware.PUBLIC_PATHS` frozenset bump. Error envelope now carries an explicit `details: None` field on every `AdminBackendError` response (validation-info slot reserved for future use; existing tests don't enforce exact key sets so they keep passing). Two new conftest fixtures (`make_store`, `make_tenant_user`) layer on Step 3.2's `make_tenant` shape — they use raw SQL INSERTs because the lightweight stubs don't declare every NOT NULL column; the audit-actor and status-consistency CHECK constraints on the live tables are honoured (NULL/NULL for audit-actor pairs; auth0_sub + invitation_accepted_at populated when status='ACTIVE'). 21 new integration tests at `tests/integration/test_tenants_router.py` — L1-L10 list, S1-S3 stats, D1-D6 detail, A1-A2 auth — including **L9 (per-row aggregates scope correctly via .correlate(Tenant); load-bearing for the subquery semantics)** and **D4 (TENANT-A asking for TENANT-B's id returns 404; load-bearing security regression test)**. Plus the xfail tripwire (1 XFAIL). 100 total pytest passes + 1 XFAIL (79 prior + 21 new); mypy strict clean on 28 source files; smoke test still 64 PASS; OpenAPI generates with all three operations and the new schemas at `/api/v1/openapi.json`. `docs/endpoints/tenants.md` rewritten as the canonical 8-section endpoint doc (3 endpoints × 8 sections each); future endpoint docs copy-paste-edit this structure.
- **TenantsRepo + repo-test fixture suite** at Step 3.2. First Repository class. `src/admin_backend/repositories/tenants.py` ships `TenantsRepo` with three async read methods (`get_by_id`, `list_all`, `list_by_status`); each takes an `AsyncSession`, no `tenant_id` argument (D-24 — visibility flows from session GUCs only). Per D-17, missing/RLS-filtered rows surface as `None` (router converts to 404 at 3.3). 9 integration tests at `tests/integration/test_tenants_repo.py` (R1-R9): happy-path PLATFORM, missing-id, list_all PLATFORM (D-29 OR-branch validation), **R4 list_all TENANT (load-bearing cross-tenant isolation)**, **R5 get_by_id cross-tenant returns None (load-bearing)**, list_by_status under both contexts, PLATFORM-unfiltered-across-statuses, orphan TENANT context. Pattern locked here propagates to stores 4.5, platform_users 5.1, tenant_users 5.2, org_nodes 5.3, RBAC 6.1, audit_logs 6.2. Shared fixtures added to `tests/integration/conftest.py` for downstream reuse: `engine`, `session_factory`, `platform_auth`, `tenant_auth_factory`, `make_tenant` (commits + DELETE-tracked teardown — only viable post-3.0), `platform_session`, `tenant_session_factory`. The `make_tenant` fixture documents the "two separate `get_tenant_session` invocations per test, setup commits, teardown DELETEs" pattern that integration tests use; transaction-rollback isolation (CLAUDE.md "Test DB strategy") doesn't apply to this layer because setup and assertion phases are deliberately on different transactions to exercise the real RLS-bound flow. 79 total pytest passes (70 prior + 9 new); RLS smoke test still 64 PASS.
- **`tenant_module_access` table + FN-AB-16 cleanup** at Step 3.4.5 (back-fill that surfaced during Step 3.5 planning; landed chronologically after Step 3.3, logically between 3.4 and 3.5). New DDL `Ithina_postgres_SQL_DDL_tenant_module_access_v1.sql` (frozen per the convention); migration `cd2a02e452ae` adds the table, two PG enums (`module_code_enum`, `module_access_status_enum`), the unconditional D-29 OR-clause RLS policy (tenant_id NOT NULL, so the unconditional shape applies), the read-pattern index, the BEFORE-UPDATE trigger using the existing `set_updated_at_timestamp()` shared utility, and seeds six rows into `lookups` for the `module_code` list (ROOS, PRICING_OS, PERISHABLES_ASSISTANT, PROMOTIONS_ASSISTANT, GOAL_CONSOLE, ADMIN — display names match the Step 3.3 stub for cutover stability). Schema-qualification: unqualified names throughout, matching Step 3.0's precedent (env.py sets search_path inside the alembic transaction). New ORM models: `TenantModuleAccess` (PG_ENUM per the convention; `FetchedValue()` defaults on id/created_at/updated_at) and `Lookup` (the `lookups` table's first ORM consumer; previously seed-only). `TenantsRepo` replaces the stub call in `list_with_aggregates` and `get_by_id_with_aggregates` with a correlated `jsonb_agg` subquery that JOINs `tenant_module_access` to `lookups` for display-name resolution, ordered by `lookups.display_order` via `aggregate_order_by`, COALESCE-wrapped to `'[]'::jsonb` for tenants with no enabled modules. The JOIN casts `tenant_module_access.module` to text to bridge the enum-vs-text comparison Postgres rejects (same gotcha as the convention reminder; documented inline). The Step 3.3 stub file `_module_entitlements_stub.py` and its xfail-strict tripwire test were both deleted in this commit (cleanup pairing). New conftest fixtures: `make_platform_user` (status='INVITED' default — the simplest CHECK-satisfying shape; status='ACTIVE' supported with auth0_sub + invitation_accepted_at populated) and `make_tenant_module_access` (validates the DDL CHECK constraints client-side: status=DISABLED requires both disabled_at AND disabled_by_user_id). L10 + D6 in `test_tenants_router.py` rewritten to assert against real seeded `tenant_module_access` rows (display name resolution via JOIN, DISABLED-status filter, display_order ordering, cross-tenant isolation); new L10b explicitly guards the empty-modules COALESCE path. Smoke test grew from 64 to 74 PASS (test_15 extends to the 5th table; test_16 gets a 5th INSERT assertion). FN-AB-16 marked RESOLVED in the Forward-notes section. Migration round-trip clean (upgrade → downgrade → upgrade); pytest 101 passes (100 + 1 net change: tripwire test removed, +1 L10b, +1 cross-test reshape); mypy strict on 29 source files; check_setup 35/35.
- **Dev seed loader** at Step 3.5. `scripts/seed_dev_data/` package reads `data/ithina_dev_seed_data.xlsx` and inserts 11 of 12 sheets' worth of data into Postgres so the API returns real content on curl (`audit_logs` skipped — Step 6.2 territory). 7 PLATFORM users (Anjali, Devon, Kira) → 7 tenants (Buc-ee's, Żabka, Infomil, GreenLeaf, SmartStore, FreshMart, CornerStop) → 49 org_nodes → 25 stores → 17 tenant_users → 15 roles → 24 permissions → 117 role_permissions → 22 user_role_assignments (3 PLATFORM-audience + 19 TENANT-side) → 27 tenant_module_access rows. CLI: `--reset` (TRUNCATE before load), `--dry-run` (validate without writing), `--sheets` (load specific sheets only). Production-refusal guard refuses to run when `settings.environment == "production"`. UUIDv7 honoured per D-21: every INSERT strips the Excel's v4 UUIDs and lets the DB's `DEFAULT uuidv7()` fire; per-sheet `excel_id → db_id` mappings via `UUIDMapper` resolve cross-sheet FK references. `column_mappings.py` is the source of truth for Excel-to-DB column correspondence; drift detection raises `UnknownColumnError` rather than silently inserting garbage. Reader uses asymmetric helpers: `_is_null_ish` (strict — for per-cell translation; preserves error sentinels in real rows so they surface as loud INSERT failures) vs `_is_phantom_cell` (broad — for row-skip; treats Excel error sentinels like `#VALUE!` as null-ish so phantom rows past visible data are silently dropped). Five specialised loaders: `platform_users` (two-phase self-reference), `org_nodes` (multi-pass parent-first), `role_permissions` (junction table — no `id`, no RETURNING), `user_role_assignments` (per-row tenant impersonation via `set_config('app.tenant_id', ..., true)` for TENANT-side rows under the FN-AB-14 IS-NULL-gated policy), and `tenant_module_access` (synthesises the three NOT NULL audit-actor FKs at load time by looking up Anjali by email — see "Note on seed Excel shape" for the captured convention). 5 unit tests for column-mapping drift detection + 5 integration tests (L1 end-to-end, L2 PLATFORM-visible row counts, L2b URA total across tenants verifying IS-NULL-gated visibility, L3 sentinel rows + audit-actor synthesis assertion, L4 production-refusal). Stale `test_t11` updated in the same commit: pre-Step-3.0 the test asserted PLATFORM-without-impersonation sees 0 rows on `tenants`, true under the original single-clause policy; D-29's unconditional OR-branch made that obsolete, but the assertion only failed visibly once the seed loader actually populated the table — fixed to `count >= 0` with an updated docstring. Manual curl: `/api/v1/tenants/stats` returns `{"total_tenants": 7, "total_stores": 25}`; Buc-ee's detail shows 3 stores, 6 active users, 6 modules. 111 pytest passes (101 prior + 10 new); mypy strict clean on 48 source files (added `scripts/__init__.py` so `scripts.seed_dev_data` resolves consistently); check_setup 35/35; smoke test still 74 PASS post-truncate. New `Note on seed Excel shape` convention captured in CLAUDE.md "Code conventions and structure": Excel is a seeding mechanism, not source of truth; system-concern columns may be synthesised at load; Step 7.3.1's customer-data tool inverts this principle. Drift fix to the `ENVIRONMENT` env-var documentation row (line 924 was `local, dev, staging, prod`; the actual `Settings` Literal is `local | development | staging | production`).
- **Lookups batch endpoint + seed extension** at Step 3.6. Single endpoint `GET /api/v1/lookups?lists=...` returns `{lookups: {list_name: [items], ...}}` so the frontend loads all dropdown values for a page in one request rather than one request per dropdown. Migration `0644a4186e48` (down_revision `cd2a02e452ae`) seeds 17 rows for the 4 PG-enum-backed categories (`tenant_tier` 4, `tenant_region` 2, `tenant_status` 5, `tenant_industry` 6); `module_code` already seeded by Step 3.4.5; total `lookups` count goes 6 → 23. **Country deferred** — the dev seed Excel's `tenants.country` carries mixed-case literals (`Canada`, `France`, `Poland`) that violate `lookups.ck_lookups_code_format` (UPPER_SNAKE_CASE only); aligning either side requires its own design decision (ISO 3166 codes, UPPER-cased literals + frontend normalisation, or a country-aware re-seed). The endpoint stays country-tolerant: `?lists=country` returns `{"country": []}` (predictable empty shape per the prompt's contract); future country lookup data populates without endpoint changes. New `LookupsRepo` mirrors `TenantsRepo`'s stateless-singleton shape (`_repo = LookupsRepo()` at module level; methods take `session: AsyncSession` as first arg). New `schemas/lookup.py` with `LookupItem` + `LookupsBatchResponse`; the top-level `{lookups: ...}` envelope leaves room for cross-cutting metadata (`cached_at`, `version`) without breaking the contract. Auth: standard JWT (any user_type); reuses `get_tenant_session_dep` for parity with the tenants router (the unused `app.tenant_id`/`app.user_type` GUCs on a no-RLS table are harmless). Comma-separated `lists=a,b,c` style locked; reversible to repeated `?lists=a&lists=b` via a one-line parser change if Amit's frontend HTTP library forces the other shape. 4 integration tests in `tests/integration/test_lookups_router.py` (L1 all-categories happy path, L2 unknown-list-empty + country-empty cases, L3 no-auth-401, L4 empty/whitespace `lists` returns `{}` with 200). 115 pytest passes (was 111; +4); mypy strict clean on 51 source files; check_setup 35/35; smoke test still 74 PASS post-truncate; round-trip migration clean (downgrade leaves only the 6 `module_code` rows from Step 3.4.5). `docs/openapi.json` regenerated with the new endpoint — `summary`, `description`, query-param `description` + `example`, and per-field `description` on every `LookupItem` property all populated for Amit's frontend codegen.
- **Platform Users resource** at Step 5.1. First PLATFORM-only endpoint and the v0 binary user_type auth-tier gate. Two GET endpoints: `GET /api/v1/platform-users` (list with status filter, ILIKE search across email + full_name, sort, offset/limit pagination) and `GET /api/v1/platform-users/{user_id}` (detail). New `PlatformUser` ORM model + `PlatformUserStatus` enum (INVITED/ACTIVE/SUSPENDED) — 14 columns from `platform_users_v1.sql`; mirrors `Tenant`'s shape (FetchedValue defaults, dialect-specific `postgresql.ENUM` per the convention, audit-actor self-FKs left as raw UUIDs because a self-referential `relationship()` adds ceremony with no v0 read benefit). New `PlatformUserRead` + `PlatformUserListItem` (alias — list/detail share one shape; small dataset, no slimmer projection needed) + `PlatformUserListResponse`. `auth0_sub`, `created_by_user_id`, `updated_by_user_id`, `suspended_by_user_id` hidden by deliberate design — same hide-policy as Tenant. New `PlatformUsersRepo` with `list(...)` and `get_by_id(...)`; sort key validation raises `InvalidSortKeyError` (a ValueError subclass) which the router catches and re-raises as `InvalidSortKeyClientError` (400, `INVALID_SORT_KEY`) so unknown sort surfaces as 400 instead of 500. **PLATFORM-only gate**: `_require_platform_auth(auth)` at handler-top raises `PlatformAccessRequiredError` (403, `PLATFORM_ACCESS_REQUIRED`) for any non-PLATFORM caller — the first concrete instance of v0 router-layer auth-tier checking, codified as the new "Note on the v0 auth model" convention in CLAUDE.md (alongside PG enum and batch-by-key envelope notes). 10 integration tests in `tests/integration/test_platform_users_router.py`: L1 envelope + hidden-fields contract, L2 status filter, L3 search, L4 sort, L5 invalid sort -> 400, L6 pagination, D1 detail + hidden, D2 unknown-id 404, A1 no-JWT 401, **A2 TENANT-JWT 403 PLATFORM_ACCESS_REQUIRED (load-bearing — without it a regression dropping the gate would expose Ithina staff identities to tenant users undetected)**. Reuses the existing `make_platform_user` factory (added at Step 3.4.5); no conftest changes. 125 pytest passes (was 115; +10); mypy strict clean on 52 source files; check_setup 35/35; smoke test unchanged at 74 PASS (no RLS surface added); alembic head unchanged at `0644a4186e48` (no new migration). `docs/endpoints/platform-users.md` follows tenants.md's 8-section structure; `docs/endpoints/openapi.json` regenerated with both new endpoints (`summary`, `description`, query-param `description` populated; `PlatformUserRead` schema with per-field types and nullability for Amit's frontend codegen).
- **Tenant Users resource + lightweight stub swap** at Step 5.2. Two GET endpoints: `GET /api/v1/tenant-users` (list with `tenant_id` filter for PLATFORM scoping, status filter, ILIKE search across email + full_name, sort, offset/limit pagination) and `GET /api/v1/tenant-users/{user_id}` (detail). Both PLATFORM and TENANT JWTs accepted (multi-user-type — first canonical instance of the pattern named in the v0 auth model note); RLS scopes visibility automatically per `tenant_users_tenant_isolation` (Step 3.0's unconditional D-29 OR-branch since `tenant_id` is NOT NULL on this table). New `TenantUser` ORM model with **all 17 columns** of `tenant_users_v1.sql`, plus `TenantUserStatus` enum (INVITED/ACTIVE/SUSPENDED) and `ActorUserType` enum (PLATFORM/TENANT) for the Pattern (b) audit-actor discriminator columns. Three Pattern (b) audit-actor pairs declared (created/updated/suspended × `*_user_id` + `*_user_type`); no SA `ForeignKey` on the `*_user_id` half (D-13 Pattern b's whole point — actor could be in either user table). New `TenantUserRead` (= `TenantUserListItem` alias) + `TenantUserListResponse`; **seven hidden fields** by deliberate design — `auth0_sub` plus all six `*_by_user_id` / `*_by_user_type` columns. New `TenantUsersRepo` with `list(...)` (optional `tenant_id` filter; status; search; sort; offset; limit) and `get_by_id(...)`. Per D-24 the Repo never accepts `tenant_id` for visibility purposes — RLS handles it via session GUCs; the optional `tenant_id` arg on `list(...)` is application-layer narrowing for explicit PLATFORM scoping. New `TenantUserNotFoundError` (404, `TENANT_USER_NOT_FOUND`); per D-17 it fires for both genuinely missing rows AND RLS-filtered rows (cross-tenant probes from a TENANT JWT). **Lightweight TenantUser stub swap** (small refactor that landed cleanly): `models/_lightweight_stubs.py` had carried a 3-column TenantUser stub since Step 3.3 for `TenantsRepo`'s `num_users_active` correlated subquery; the full ORM model replaces it. `repositories/tenants.py` now imports `TenantUser` + `TenantUserStatus` from `models.tenant_user`; the `status == "ACTIVE"` string literal was tightened to `status == TenantUserStatus.ACTIVE`. The Store stub stays (Step 4.5 territory). Step 3.3's L9 test (per-row aggregates scope correctly via `.correlate(Tenant)`) is the load-bearing regression check — pre-swap baseline 22/22 in `test_tenants_router.py`, post-swap identical 22/22. **Shared sort-key error classes** (one-step refactor, surfaced because Step 5.2 is the second consumer): `InvalidSortKeyError` (ValueError) promoted from `repositories/platform_users.py` to a new `repositories/_errors.py`; `InvalidSortKeyClientError` (ClientError) promoted from `routers/v1/platform_users.py` to `errors.py` alongside the other shared error classes. Step 5.1's imports updated; Step 5.1 tests (10/10) still pass. Future Repos with a sort param reuse these — captured as a forward-convention bullet in the v0 auth model note. 13 integration tests in `tests/integration/test_tenant_users_router.py`: L1 envelope + 7 hidden fields, L2 PLATFORM `tenant_id` filter, L3 status filter, L4 search, L5 sort, L6 invalid sort -> 400, L7 pagination, L8 TENANT-A list scoped to A only (RLS), D1 detail + hidden, D2 unknown id -> 404, **T9 cross-tenant detail returns 404 with TENANT_USER_NOT_FOUND (LOAD-BEARING — proves RLS-as-404 works end-to-end through middleware -> session -> Repo -> router; without it a regression in RLS or session-handling could silently expose tenant data across boundaries)**, T10 cross-tenant `?tenant_id=B` from TENANT-A returns empty (RLS + filter intersect cleanly to empty), A1 no-JWT 401. Reuses existing `make_tenant` + `make_tenant_user` factories; no conftest changes (the factory's NULL/NULL audit-actor INSERT shape works against the live CHECK constraints exactly as before the stub swap). 138 pytest passes (was 125; +13); mypy strict clean on 41 source files; check_setup 35/35; smoke test still 74/74 PASS post-truncate; alembic head unchanged at `0644a4186e48` (no migration). `docs/endpoints/tenant-users.md` follows tenants.md's 8-section structure; `docs/endpoints/openapi.json` regenerated with both new endpoints. Manual curl verification: PLATFORM list returns 17 across all tenants; TENANT-A list returns 6 (Buc-ee's only); cross-tenant detail returns 404 TENANT_USER_NOT_FOUND; PLATFORM detail of any tenant works; TENANT-A `?tenant_id=B` returns empty; invalid sort returns 400 INVALID_SORT_KEY.
- **Backend Dockerfile + Artifact Registry push** at Steps 4.2 + 4.3. Multi-stage `Dockerfile` (62 lines): stage 1 (`python:3.12-slim` + uv 0.5.4 pinned) installs runtime deps from `uv.lock` via cache-mounted `uv sync --frozen --no-dev --no-install-project`, then copies `src/` + `README.md` (the latter required by hatchling because `pyproject.toml` declares `readme = "README.md"`; without it the second `uv sync` fails the project wheel build) and runs `uv sync --frozen --no-dev` to install the project itself. Stage 2 (also `python:3.12-slim`) brings the venv across at `/opt/venv`, creates a non-root system user `app:app` (uid/gid 1000), copies `src/` + `migrations/` + `alembic.ini`, exposes 8000, and runs uvicorn against `admin_backend.main:app`. `--log-config` flag deliberately dropped from CMD pending Step 7.2.1's `logging.json`. `.dockerignore` extended (+9 lines on the existing 140) with two functional changes: `!README.md` re-include (the bang-prefix re-include must come AFTER the broader `*.md` exclusion or it's a no-op) so the project wheel build can find it, and a new `data/` exclusion so the dev seed Excel (`data/ithina_dev_seed_data.xlsx`, ~200KB) doesn't bloat the build context. Local image 224 MB (under the 350 MB ceiling); registry-compressed 75.9 MB. Smoke test: container started with `--network=host` against the local Postgres container, `curl /api/v1/health` returns `{"status":"ok","service":"admin-backend","version":"0.1.0"}` 200 after a 5s cold start; lifespan completes cleanly (DB role check via `assert_app_role_no_bypassrls` succeeds; structured JSON log line for the request emitted via `admin_backend.requests`). Image tagged `:v0.1.0` and `:latest` and pushed to `asia-south1-docker.pkg.dev/ithina-retail-admin/admin-images/admin-backend`; both tags share registry manifest digest `sha256:838e24fd6bf73984995aa4f5bc1902368bcb0ecfbad3be16296e9aedac625047`. **The image artifact is identical for Cloud Run dev (Step 4.4 next) and GKE prod (Step 8.2 future) per D-33** — same container, two runtime shapes; the prompt's stale "GKE deployment in Step 4.4 / GKE one-shot Job in Step 4.1" prose was acknowledged-and-flowed-past, and the deliverable matches D-33's revised topology. No CLAUDE.md decision changes; no architecture.md changes (Appendix A.2 already documents the sidecar-not-in-image topology). No source files changed; pytest still 138 passes; mypy strict still clean; check_setup 35/35.
- **Org-tree read surface (lazy-load with smart defaults)** at Step 5.3. Two new endpoints back the Organization Tree page (Frontend spec 7.3): **E2** `GET /api/v1/tenants/{tenant_id}/org-tree` (initial fetch with smart-default — full tree if ≤500 ACTIVE non-TENANT nodes, depth-limited at depth=4 otherwise, auto-reduces depth and sets `truncated=true` if the depth-limited tree still exceeds the 1000-node payload cap; bounded retry max 2 reductions) and **E3** `GET /api/v1/tenants/{tenant_id}/org-nodes/{node_id}/children` (paginated immediate-children fetch for lazy expansion of depth-cut subtrees, default limit=100, max 200). Multi-user-type per the v0 auth model: both PLATFORM and TENANT JWTs accepted; visibility scoping is RLS's job (D-29 OR-clause on `org_nodes_tenant_isolation`); cross-tenant requests surface as 404 (RLS-as-404 per D-17), tested by **T12 (E2) and T18 (E3) load-bearing regression tests**. New `OrgNode` ORM model maps all 18 columns of `org_nodes_v2.sql`, with `OrgNodeType` and `OrgNodeStatus` PG enums, the shared `ActorUserType` enum reused from `models/tenant_user.py` (third consumer), and `path` declared as `Mapped[str]` with `FetchedValue()` (ltree column treated as opaque text on the read path; SA passes column references through unchanged so PG resolves `nlevel(path)` etc. against the native ltree type). New `OrgNodesRepo` (stateless singleton, four methods): `count_active_by_tenant` drives the smart-default decision; `list_active_with_child_counts(tenant_id, max_depth=None)` returns path-ASC ordered `(OrgNode, child_count)` tuples with the per-row child count attached via CTE + LEFT JOIN — Approach A per DP-1 (split count + LEFT JOIN, two queries per E2 call inheriting RLS independently); `list_children_paginated` for E3; `node_exists` separate (DP-3) so E3 disambiguates "parent has no children" (200, empty items) from "parent doesn't exist or RLS-filtered" (404, `ORG_NODE_NOT_FOUND`). The `nlevel(path) <= max_depth + 1` filter absorbs the implicit TENANT root level so callers think in "depth from root" (depth=1 → HQ-level, depth=4 → HQ + 3 mid-levels). Recursive `OrgNodeTreeItem` Pydantic schema with `model_rebuild()` at module bottom; `loaded_children: Literal["all", "partial", "none"]` carries the lazy-load state machine; `child_count` always reflects the FULL subtree's immediate children (not just what's in the response) so the frontend can decide expansion. **D-30 exception captured as a one-line convention note** (alongside the existing PG enum, batch-by-key, and seed-Excel-shape notes): E2's response is `{tenant_id, tenant_name, stats, tree}` — singleton-resource shape, NOT the standard `{items, pagination}`; E3 follows D-30 normally. No new D-XX entry; the exception is a per-endpoint posture under D-30's existing framing. The `_build_tree` helper is pure-functional, three passes (DP-5 lean — clarity over terseness): build items → link children to parents (identifying roots as nodes whose parent is the TENANT root, NULL, or absent from the loaded set) → finalize `loaded_children` based on whether all `child_count` immediate children are present in the loaded set. New `OrgNodeNotFoundError` defined inline in the org-tree router (matching the Step 5.2 `TenantUserNotFoundError` pattern of localizing per-resource 404 classes with their router). Server-side tunables locked: `FULL_TREE_THRESHOLD=500`, `DEFAULT_DEPTH=4`, `MAX_DEPTH=6`, `PAYLOAD_CAP=1000`, `MAX_REDUCTIONS=2`. New `make_org_node` conftest fixture mirrors `make_store`'s raw-SQL pattern (more NOT NULL columns than the ORM stub-style would handle); audit-actor pairs NULL/NULL (XOR-permitted); ltree path built locally as `parent_path + "." + lower(code).replace("-", "_")` — ltree label syntax disallows hyphens, so codes like `BU-HQ` produce label `bu_hq`; teardown deletes one-at-a-time in REVERSE insertion order to satisfy the composite FK `fk_org_nodes_parent_same_tenant ON DELETE RESTRICT`. **21 integration tests** at `tests/integration/test_org_tree_router.py`: T1 small Buc-ee's-shape (8-node) full-envelope + invariants I1-I11, T2 empty tenant, T3 TENANT-root-only, T4 sibling order, T5 recursive serialization, T6 INACTIVE/ARCHIVED excluded, T7 smart-default full mode, T8 smart-default lazy mode (monkeypatched threshold), T9 payload-cap auto-reduce (monkeypatched cap), T10 explicit `?depth=N` respected, T11 TENANT JWT own tenant, **T12 E2 cross-tenant 404 LOAD-BEARING**, T13 unknown tenant 404, T14 no-JWT 401, T15 E3 happy path, T16 E3 pagination, T17 E3 unknown node 404, **T18 E3 cross-tenant 404 LOAD-BEARING**, T19 E3 node-with-no-children, T20 mixed-depth subtree (true leaf vs depth-cut, distinguished by `has_children`), T21 invalid UUID 422. 159 pytest passes (was 138; +21); mypy strict clean on 45 source files; check_setup 35/35; smoke test still 74 PASS; alembic head unchanged at `0644a4186e48` (no migration). `docs/endpoints/org-tree.md` follows the 8-section format with both endpoints; `docs/endpoints/openapi.json` regenerated with both new routes and the recursive `OrgNodeTreeItem.children` `$ref` schema. **Originally-planned `num_nodes` augmentation on `/api/v1/tenants` dropped** from this step (parked post-v0 per the design conversation; D-31 means future addition is non-breaking). Originally-planned `/api/v1/org-nodes` flat list / detail / descendants raw-SQL endpoint also dropped — lazy via E3 covers all UI use cases.
- **RBAC read endpoints (Roles + Permissions + Permission Matrix)** at Step 6.1. Four GET endpoints across three URL prefixes back the Roles & Permissions page (Frontend spec 7.5): **E1** `GET /api/v1/roles` (pre-grouped by audience — `{platform_roles, tenant_roles}` blocks each `{items, total}`; deliberate D-30 exception); **E2** `GET /api/v1/permissions` (flat catalogue, `{items, pagination}`); **E3** `GET /api/v1/roles/{role_id}/permissions` (parent-echo envelope with `role_id` + `role_name` at top level, deliberate D-30 exception); **E6** `GET /api/v1/permission-matrix` (render-ready grid with `cells[]` position-aligned to `roles[]`, deliberate D-30 exception). All four are multi-user-type per the v0 auth model. **Two migrations** land in this step: **`90cd038ae618`** (DDL enum cleanup, forward-only) narrows `module_enum` to 4 values (drops ROOS, GOAL_CONSOLE) and `permission_scope_enum` to 3 values (drops REGION) via the rename-recreate-USING-cast dance Postgres requires (no ALTER TYPE DROP VALUE); deletes 1 legacy permission row + 4 referencing role_permissions rows; downgrade raises NotImplementedError per the project's irreversible-cleanup convention. **`22ccfb193cff`** (lookups seed) adds 25 rows for the four enum-display-label categories: 4 `module` + 12 `resource` + 6 `permission_action` + 3 `permission_scope`; idempotent via ON CONFLICT DO NOTHING; round-trip clean. **3 ORM models**: `Role` (with `RoleAudience` and `RoleStatus` enums; reuses `ActorUserType` from `tenant_user.py` per the convention), `Permission` (with `PermissionModule`, `PermissionResource`, `PermissionAction`, `PermissionScope` enums — ResourceEnum and ActionEnum already match the locked vocabulary; ModuleEnum and ScopeEnum updated by the cleanup migration), `RolePermission` (composite PK; Pattern (b) audit-actor on created_by only). `UserRoleAssignment` deferred per FN: E4/E5; a lightweight stub at `models/_lightweight_stubs.py` lands alongside the existing `Store` stub (carries `id`, `role_id`, `tenant_id`, `status`, `platform_user_id`, `tenant_user_id` only — the columns the user_count subquery needs). Same lifecycle as Step 3.3's TenantUser stub that Step 5.2 swapped out: removed when E4/E5 land. **3 Repos**: `RolesRepo` (`list_grouped` returns `{'PLATFORM': (rows, total), 'TENANT': (rows, total)}` — handler renders the pre-grouped envelope; `get_by_id` with optional `audience_filter` for E3's audience-gated 404; `list_permissions_for_role` for E3's items), `PermissionsRepo` (single `list` method; no audience filter — catalogue is reference data), `PermissionMatrixRepo` (`get_matrix` returns `(roles, permission_rows, grants)`; the router's pure-functional assembler turns these into the position-aligned `cells[]`-per-row response; display labels resolved via four LEFT JOINs against `lookups` with COALESCE to enum code as defensive fallback). **`user_count` correlated subquery** uses `.correlate(Role)` (load-bearing — same trap as Step 3.3 L9 / Step 5.3 L11); for TENANT JWTs the subquery inherits the request's session GUCs and RLS on `user_role_assignments` (FN-AB-14 IS-NULL-gated form per D-29) automatically scopes the count to the calling tenant. **App-layer audience filter pattern**: `_audience_filter_for(auth)` helper in the router maps `AuthContext.user_type` to `'TENANT'` or `None`; threaded into Repo methods as an optional argument. Distinct from RLS but with the same anti-information-disclosure intent. **Codified as the third posture in the v0 auth model note** (alongside PLATFORM-only and multi-user-type-with-RLS): "Multi-user-type endpoints with app-layer audience filter" — applies to non-RLS tables that need user-type-segmented visibility. Future non-RLS audience-segmented endpoints follow this pattern. **1 new error class**: `RoleNotFoundError` (404, `ROLE_NOT_FOUND`) defined inline in the rbac router (matching the Step 5.2 `TenantUserNotFoundError` / Step 5.3 `OrgNodeNotFoundError` pattern). `InvalidSortKeyClientError` reused from Step 5.2. **3 conftest factories** (raw-SQL-INSERT pattern mirroring Step 5.2's `make_tenant_user`): `make_role`, `make_permission` (builds the `code` from the four enum slots so it always passes `ck_permissions_code_format`), `make_role_permission` (no surrogate id; tracks composite (role_id, permission_id) keys for teardown). **23 integration tests** at `tests/integration/test_rbac_router.py`: 8 R-tests (E1) + 4 P-tests (E2) + 3 RP-tests (E3) + 5 M-tests (E6) + 1 A-test (no-JWT 401 across all 4 endpoints) + 2 H-tests (hidden-fields contracts). **Five LOAD-BEARING**: **R2** TENANT JWT empty platform_roles block (audience filter on E1); **R4** user_count correlated subquery scopes per-row via `.correlate(Role)`; **RP3** TENANT JWT requesting a PLATFORM role's id returns 404 ROLE_NOT_FOUND; **M2** E6 cells/roles position alignment invariant; **M3** E6 TENANT JWT filters role columns. **Excel seed cleanup** (data/ithina_dev_seed_data.xlsx): legacy permission row `_key=p4` (`PRICING_OS.MARKDOWNS.APPROVE.REGION`) removed from permissions sheet; 4 role_permissions rows referencing it removed. As a one-time side-effect, the `=TRUE()`/`=FALSE()` Excel formulas in the `is_system` column on the roles sheet were converted to literal Python booleans (openpyxl can't compute formulas, so a plain save would otherwise wipe the cached values — surfaced when the seed loader started returning `is_system=None` after my first cleanup attempt; restored from a pre-step backup and re-cleaned with formula conversion). One stray `=SUBTOTAL` formula on role_permissions also cleared. EXPECTED counts in `tests/integration/test_seed_loader.py` updated: permissions 24 -> 23, role_permissions 117 -> 113. Test sort assertions updated to use enum-ordinal comparison via `_permission_sort_tuple` helper rather than string-alphabetic — Postgres sorts enum columns by enum ordinal (declaration order in the DDL), not string-alphabetic; the test contract now matches the live behaviour. 182 pytest passes (was 159; +23); mypy strict clean on 54 source files; check_setup 35/35; smoke test still 74 PASS (no new RLS surface — the catalogue tables are platform-global). Alembic head moves to `22ccfb193cff` (was `0644a4186e48`). Per-resource regression checkpoint clean: tenants 22/22, platform_users 10/10, tenant_users 13/13, org_tree 21/21, lookups 4/4. `docs/endpoints/rbac.md` covers all 4 endpoints in the 8-section format; `docs/endpoints/openapi.json` regenerated.
- **Tenants list aggregate sort keys** at Step 6.4. Extends `GET /api/v1/tenants` with a new `sort` query parameter accepting **10 keys**: 6 column-based (`created_at_asc/desc`, `name_asc/desc`, `tier_asc/desc`) + 4 aggregate-based (`num_users_active_asc/desc`, `num_stores_asc/desc`). Default `created_at_desc` (mirrors PlatformUsersRepo / TenantUsersRepo precedent); stable secondary sort by `Tenant.id ASC` so identical primary-sort values page deterministically. **Scope expansion:** the original prompt described this as "extending an existing 6-key sort vocabulary" with 4 new aggregates, but the endpoint had no `sort` parameter at all and the Repo hardcoded `Tenant.name.asc()` — sort infrastructure planned for Step 3.3 never landed. Step 6.4 lands the foundational sort surface (param + SORT_MAP + 6 column keys) AND the 4 aggregate keys in one bundle. **Behaviour change:** callers who don't pass `sort` get `created_at_desc` instead of the previous hardcoded `name ASC`; documented in the doc, docstring, and commit message. New module-level `_BASE_TENANTS_SORT_MAP` (column keys), `_AGGREGATE_TENANTS_SORT_KEYS` (aggregate keys), and public `TENANTS_SORT_KEYS: frozenset[str]` (validation) in `repositories/tenants.py`. Aggregate sort clauses built per-call inside `list_with_aggregates` since their underlying scalar subqueries are constructed there; same pattern referenced twice (once labeled in SELECT, once `.asc()/.desc()` in ORDER BY) — PG executes the correlated subquery twice per row, negligible at v0 fleet scale (7 tenants). Sort is RLS-correct in both PLATFORM and TENANT contexts: both subquery executions inherit session GUCs, so a TENANT JWT's aggregate-keyed sort scopes the inner count to its own tenant. Reuses Step 5.2's shared `InvalidSortKeyError` (Repo) / `InvalidSortKeyClientError` (router, 400) — no new error classes. Existing L5 test (pagination + search) pinned to `sort=name_asc` so its alphabetical-page assertion holds independent of the default-sort change. **12 new integration tests** in `test_tenants_router.py`: L4a-L4f for column keys, L4g for invalid-sort 400, L5a-L5d for aggregate keys, L5e for RLS-on-aggregate-sort. **L5b is load-bearing** — Step 6.5's Top Tenants dashboard panel calls `?sort=num_users_active_desc&limit=5` exactly; without this sort key the panel would receive 400 INVALID_SORT_KEY. `scripts/smoke_curl.sh` extended with 1 new assertion (the dashboard's exact call shape); "WHAT'S CHECKED" header count 15 → 16. `docs/endpoints/tenants.md` query-params table gains the `sort` row; default-sort behaviour note rewritten; 2 example calls added. 194 pytest passes (was 182; +12); mypy strict clean on 54 source files; check_setup 35/35; smoke test still 74 PASS post-truncate; alembic head unchanged at `22ccfb193cff` (no migration). Per-resource regression checkpoint: tenants 22 → 34 (the +12 deliverable), platform_users 10, tenant_users 13, org_tree 21, lookups 4, rbac 23 — all other files unchanged. **Precondition for Step 6.5** (dashboard stats endpoints).
- **Dashboard stats endpoints (fleet-stats + governance-stats)** at Step 6.5. Two new endpoints back the Platform Dashboard's KPI grid (Frontend spec 7.1): **E1** `GET /api/v1/dashboard/fleet-stats` (KPI cards 1-4 — active tenants, platform users, stores, aggregated MRR) and **E2** `GET /api/v1/dashboard/governance-stats` (KPI cards 5-8 — pending approvals, guardrails fired, custom roles, modules deployed). Both card-shaped responses (deliberate D-30 exception — the dashboard is a UI-shaped query bundle, not a paginatable collection); both multi-user-type with RLS-driven persona projection. PLATFORM JWT sees fleet-wide aggregates via D-29 OR-clauses; TENANT JWT sees own-tenant aggregates via the equality clause; same SQL, persona-correct values. **Single CTE per E1 request** producing all 4 cards' aggregates in one Postgres round-trip (`repositories/dashboard.py`). E2 uses one small SELECT for the only real card; the other 3 are returned as constants. **Card-shaped schemas** at `schemas/dashboard.py`: 1 shared `DeltaBlock` + 4 fleet cards + 4 governance cards (3 with `unavailable_reason` field) + 2 response models. `ConfigDict(extra="forbid")` on every model — guards against shape drift; the X1 test asserts the exact field set per card. **`available` + `unavailable_reason` stub pattern**: 3 of 4 governance cards `available: false` in v0 with locked vocabulary codes (`approvals_table_not_built`, `audit_logs_or_guardrails_not_wired`, `custom_role_creation_not_shipped`); fleet-stats has all 4 cards real but `mrr_aggregated.delta.available: false` (no MRR snapshot table — tracked as MRR-DELTA-REAL forward note). Append-only contract per D-31: when a stub flips to real, only `available`, `value`, and `unavailable_reason` change; field set and types stay identical. **Backend-formatted, scope-aware sub_text** strings; helpers are pure functions in the router dispatched on `auth.user_type`. Examples: `platform_users.sub_text` is `"across all tenants"` (PLATFORM) vs `"in your organization"` (TENANT); `modules_deployed.sub_text` is `"across N tenant(s)"` (PLATFORM, singular/plural) vs `"enabled for your organization"` (TENANT). **Scope expansion at design-review time (Stop-and-ask trigger #5 fired):** the original prompt assumed `TenantStatus` had four values; the actual enum has five (ONBOARDING, TRIAL, ACTIVE, SUSPENDED, TERMINATED). The `active_tenants.sub_text` vocabulary was extended to break out ONBOARDING as a distinct lifecycle segment in lifecycle order (onboarding → trial → suspended), separated by ` · `; empty string when all three are zero. The CTE adds an explicit `onboarding` filter; `total` continues to use `status != 'TERMINATED'` (already correctly captures all four non-terminated states). **MRR formatting departure from `tenants.py`** (Q2 confirmed at design review): explicit `f"{x:.2f}"` formatting in the router for `mrr_aggregated.value`, vs `tenants.py`'s per-row `field_serializer` returning `str(v)`. Different contracts (per-row NUMERIC canonical-string preservation vs aggregate `SUM` Decimal precision normalization), different posture; documented in the schema docstring. **`DashboardRepo` deliberately departs from one-Repo-per-resource** — the dashboard isn't a CRUD resource; it's a UI-shaped query bundle. Two methods (`fleet_stats`, `governance_stats`) returning frozen dataclasses; defensive `Decimal(...)` cast on `mrr_sum` to handle driver variance on `SUM(NUMERIC)` outputs. **16 integration tests** at `tests/integration/test_dashboard_router.py`: 8 S-tests (E1) + 6 O-tests (E2) + 1 A-test (no-JWT 401) + 1 X-test (Pydantic extra-forbid drift guard). **Five LOAD-BEARING**: **S2** TENANT JWT RLS-scopes counts to own tenant (without it a TENANT user could read fleet-wide values); **S5** sub_text scope-awareness same-data different-requests; **S7** MRR delta permanently stubbed in v0 (regression guard against accidentally flipping `available` to true without the snapshot table); **O2** `modules_deployed` real and RLS-scoped while the other 3 cards stay stubbed; **O5** `modules_deployed.sub_text` scope-awareness across user types. Plus a fixture-ordering note: tests requesting both `make_platform_user` and `make_tenant` need `make_platform_user` listed FIRST so pytest tears down tenants before the platform_user (the SUSPENDED tenant fixtures reference the platform_user via `suspended_by_user_id` FK ON DELETE RESTRICT). **`scripts/smoke_curl.sh` extended** with 2 new assertions (`dashboard_fleet_stats`, `dashboard_governance_stats`); WHAT'S CHECKED count 16 → 18. **FN-AB-21 RESOLVED** in this step: Option 2 (multi-user-type, document scope-dependent semantics) confirmed as the platform-wide default for stats endpoints. The existing `/api/v1/tenants/stats` is unchanged; resolution is documentation policy. 210 pytest passes (was 194; +16); mypy strict clean on 57 source files; check_setup 35/35; smoke test still 74/74 post-truncate; alembic head unchanged at `22ccfb193cff` (no migration). Per-resource regression checkpoint: tenants 34, platform_users 10, tenant_users 13, org_tree 21, lookups 4, rbac 23 — all unchanged. `docs/endpoints/dashboard.md` covers both endpoints in 8-section format with the v0 unavailable_reason vocabulary documented; `docs/endpoints/openapi.json` regenerated.
- **Dashboard raw-SQL schema qualification** at Step 6.5.1. Cloud SQL deploy of v0.1.7 surfaced both `/api/v1/dashboard/*` endpoints returning 500 with `relation "tenants" does not exist` and `relation "tenant_module_access" does not exist`. Root cause: `repositories/dashboard.py` used module-level `text()` constants with unqualified table names, depending on `search_path` to include `core`. The engine's connect-time hook (`db/engine.py:72-76`) sets it on every new physical connection but does not always mask reliably on Cloud SQL (connection cycling, pool recycle, async event-listener ordering — exact cause not diagnosed because the fix removes the dependency). **Fix**: schema-qualify both `text()` queries via `get_settings().db_schema` per-call interpolation, mirroring `repositories/permission_matrix.py:101-128`'s pattern exactly. Module-level SQL constants dropped; SQL builds inside the methods. **Two new regression tests** lock the contract: `test_x2_raw_sql_works_with_clobbered_search_path` (test_dashboard_router.py) and `test_m6_raw_sql_works_with_clobbered_search_path` (test_rbac_router.py) — both clobber `search_path` to `public` and assert the Repo methods still succeed. The matrix Repo was already correct; M6 is a forward-guard against future regressions there. **New CLAUDE.md convention note** ("Note on raw `text()` SQL — schema qualification is mandatory") with rule + reason + precedent + anti-pattern. 212 pytest passes (was 210; +2). mypy strict clean on 57 source files. check_setup 35/35. No migration. No DDL changes. No endpoint contract changes. Per-resource regression checkpoint: dashboard 16 → 17, rbac 23 → 24, all others unchanged. Re-deploy required to ship the fix to Cloud Run.
- **Module enum unification (Path B)** at Step 6.6. Closes the **MODULES-EXT** forward note from Step 6.1's "Known follow-ups (RBAC)" sub-section. Two PG enums + two `lookups` list_names had been describing the same product concept ("which Ithina module") since a Step 3.4.5 oversight: `module_enum` (4 values post Step 6.1 narrowing) backed `permissions.module`; `module_code_enum` (6 values) backed `tenant_module_access.module`. The fork was a historical accident that produced drift at Step 6.1 (when only one of the two got narrowed). Path A (additive `ALTER TYPE module_enum ADD VALUE` to re-add ROOS / GOAL_CONSOLE) was the originally-proposed fix; Path B (unification) supersedes it because the additive approach restores symmetry without fixing the underlying duplication. **One forward-only Alembic migration** (`cec8fae734e0`): `ALTER TABLE permissions ALTER COLUMN module TYPE module_code_enum USING module::text::module_code_enum`; `DROP TYPE module_enum`; `DELETE FROM lookups WHERE list_name = 'module'` (with defensive row-count assertion — the migration raises if the deleted set isn't exactly the 4 codes Step 6.1's seed inserted, catching any unexpected hand-edits since). `downgrade()` raises NotImplementedError per the project's irreversible-cleanup convention (mirrors Step 6.1's `90cd038ae618`). Safety: every value in the narrow `module_enum` is also in `module_code_enum`, so the USING text-cast cannot fail; `permissions` table is small (23 rows post Step 6.1), column rewrite is sub-second; `ALTER COLUMN TYPE` automatically rebuilds the dependent `uq_permissions_tuple` UNIQUE index. **Python**: deleted the `PermissionModule` enum class entirely (was in `models/permission.py`); `Permission.module` column now references `ModuleCode` imported from `models/tenant_module_access.py` (the surviving Python enum, kept under its original name per the design-review confirmation — renaming to `Module` would shadow the existing `schemas.Module` Pydantic class). Updated `models/__init__.py` re-exports; updated `schemas/permission.py`'s `PermissionRead.module` and `PermissionMatrixRow.module` field types from `PermissionModule` to `ModuleCode`. **Repos**: `permission_matrix.py`'s `_load_permissions_with_labels` flipped the lookups JOIN's `list_name` from `'module'` to `'module_code'`; `permissions.py`'s `list` method gained a `LEFT JOIN` against `lookups` on `(list_name='module_code', code = Permission.module::text)`. **Sort-stability change**: both Repos' `module_asc` ORDER BY changed from `Permission.module` enum-ordinal to `coalesce(lookups.display_order, 999) ASC`. Reason: the new `module_code_enum`'s ordinal differs from the old `module_enum`'s for the same four overlapping values (e.g., ADMIN moved from ordinal 0 to 5), so any post-migration `ORDER BY permissions.module` would re-sequence rows. Sorting by `lookups.display_order` decouples the contract from enum ordinal — robust across future ALTER TYPE ADD VALUE additions, future re-orderings; the seed data is the source of truth for "intended display order." **Tests**: mechanical rename of `PermissionModule` → `ModuleCode` in test_rbac_router.py imports; `_permission_sort_tuple` helper rewritten — module sort key now uses a hardcoded `_MODULE_DISPLAY_ORDER` map mirroring the Step 3.4.5 seed's display_order values (resource/action/scope still use enum ordinal — those weren't touched by this step). conftest's `make_permission` factory's `CAST(:module AS module_enum)` → `CAST(:module AS module_code_enum)`. **Wire format**: per-row JSON identical pre/post step (the same string values flow through the same field names); row ORDERING on `/api/v1/permissions` and `/api/v1/permission-matrix` changes because the sort basis flipped — planned change, not regression. **OpenAPI broadening** (informational, not breaking): the `module` field's accepted enum values grew from 4 to 6 (ROOS and GOAL_CONSOLE now appear in the schema's enum vocabulary, even though no permission rows currently target those values). Frontend codegen consumers may need to update if strictly validating; no permission row references those values today, so no live data sees them. **DDL files in `db/raw_ddl/` unchanged** per D-21 (the migration chain is the live schema; DDLs stay frozen). **No seed Excel changes** — all 23 surviving permissions target values present in both enums. **No new endpoints, no new schemas, no new error classes, no smoke_curl changes.** 212 pytest passes (unchanged from Step 6.5.1); mypy strict clean on 57 source files; check_setup 35/35; smoke test 74/74 post-truncate; per-resource regression: tenants 34, platform_users 10, tenant_users 13, org_tree 21, lookups 4, rbac 24, dashboard 17 — all unchanged. Alembic head moves `22ccfb193cff` → `cec8fae734e0`. Re-deploy required to ship the fix to Cloud SQL — and the deploy MUST run `--migrate` because Step 6.6 introduces a new migration (the first migration to ship since Step 6.1).
- **Module Access read endpoints** at Step 6.7. First instance of the new label-handling convention (sibling `<field>_label`, server-side resolution via `lookups`). Two GET endpoints under `/api/v1/module-access/`: **E1** `GET /modules` (6-card aggregates — `module_code`, `module_label`, `enabled_count` over the visible ACTIVE+TRIAL subset, `total_active_trial_tenants` denominator); **E2** `GET /matrix` (paginated tenant × module grid, sort/filter/q-search). Both multi-user-type per the v0 auth model — RLS does the persona projection: PLATFORM sees fleet-wide via D-29's OR-clause on `tenants` and `tenant_module_access`; TENANT sees own-tenant only (1-row matrix; `total_active_trial_tenants` collapses to 0/1). New `ModulesAccessRepo` at `repositories/modules_access.py` with raw `text()` SQL, schema-qualified per the raw-SQL convention (no module-level constants — same posture as `permission_matrix.py`). E1 uses a single 3-CTE query (visible_tenants → enabled_per_module → total_count → final SELECT joined to `lookups` for the row set); E2 uses three stages (page query with label JOINs → cells grid via tenants × modules CROSS JOIN LEFT JOIN tenant_module_access → total count) and assembles in Python. **Cell synthesis**: every visible non-TERMINATED tenant gets exactly 6 cells regardless of how many `tenant_module_access` rows exist; absent rows AND `status='DISABLED'` rows both render as `DISABLED` on the wire (frontend doesn't distinguish). **Module ordering** anchored on `lookups.display_order` (decoupled from PG enum ordinal per Step 6.6's sort-stability decision); position-aligned across `/modules.items[i]` and `/matrix.items[*].cells[i]` so frontend reconciles by index. **Sort vocabulary** for `/matrix`: 6 column-based keys (`name_*`, `created_at_*`, `tier_*`); aggregate keys deliberately absent (matrix doesn't expose those per row). Default `tier_asc` with secondary stable sort by name then id. Reuses Step 5.2's `InvalidSortKeyError` / `InvalidSortKeyClientError` — no new error classes. **Aggregate denominator** uses `status IN (ACTIVE, TRIAL)`, distinct from `/dashboard/fleet-stats.active_tenants.total` which uses `status != 'TERMINATED'` — different product questions. **Matrix row set** is `status != 'TERMINATED'` (ACTIVE + TRIAL + SUSPENDED + ONBOARDING) so platform admins retain governance visibility into non-currently-transacting tenants; the `status` filter Literal vocabulary excludes `TERMINATED` for the same reason (would always match zero rows). **One additive Alembic migration** `2fdc4bc9f4cb` (down_revision `cec8fae734e0`): UPDATEs the 6 `lookups` rows under `list_name='module_code'` to match the locked screenshot ordering (ROOS=1, GOAL_CONSOLE=2, PRICING_OS=3, PERISHABLES_ASSISTANT=4, PROMOTIONS_ASSISTANT=5, ADMIN=6 — pre-step had GOAL_CONSOLE at 5); idempotent INSERT for `tenant_tier` (4 rows) and `tenant_status` (5 rows) — no-op since Step 3.6 already seeded them with matching content. `downgrade()` raises NotImplementedError per the project's irreversible-cleanup convention (mirrors Step 6.6's `cec8fae734e0` and Step 6.1's `90cd038ae618`). **Test-side amendment**: `_MODULE_DISPLAY_ORDER` dict in `tests/integration/test_rbac_router.py` updated to mirror the new live order (the 4 modules in seed data — PRICING_OS, PERISHABLES, PROMOTIONS, ADMIN — preserve their relative ordering, so the rbac sort assertions don't break, but the dict's own comment commits to staying in sync). **15 new integration tests** in `tests/integration/test_modules_access_router.py`: M1-M5 (E1) + X1-X9 (E2) + A1 (auth). **Five LOAD-BEARING**: **M2** TENANT JWT `/modules` aggregate counts collapse to own-tenant only (without it a TENANT user could read fleet-wide module counts); **M3** module ordering position-alignment across `/modules` and `/matrix.cells[]` (frontend reconciles by index); **M4** server-side label resolution end-to-end against the seeded `tenant_tier` / `tenant_status` / `module_code` lookups (`tier_label="Enterprise"`, `status_label="Active"`, `module_label="Pricing OS"`); **X1** TENANT JWT `/matrix` returns exactly 1 row (RLS scoping); **X2** synthesized DISABLED cells respect RLS — for a tenant with N ENABLED rows, `cells[]` always has 6 entries, with absent modules rendering as DISABLED via the CROSS JOIN. **`scripts/smoke_curl.sh` extended** with 2 new assertions (`module_access_modules`, `module_access_matrix`); WHAT'S CHECKED count 18 → 20. **Other 3 workflow scripts unchanged.** 227 pytest passes (was 212; +15); mypy strict clean on 60 source files (was 57; +3 new modules — schemas, Repo, router); check_setup 35/35; smoke test 74/74 post-truncate; per-resource regression: tenants 34, platform_users 10, tenant_users 13, org_tree 21, lookups 4, rbac 24 (post-`_MODULE_DISPLAY_ORDER` update), dashboard 17 — all exact pre-step counts. Alembic head moves `cec8fae734e0` → `2fdc4bc9f4cb`. New "Note on label resolution" convention codified in CLAUDE.md alongside the existing convention notes. `docs/endpoints/module-access.md` covers both endpoints in 8-section format; `docs/endpoints/openapi.json` regenerated with both new paths. **Re-deploy MUST run `--migrate`** — bundles Step 6.6 + 6.7; Step 6.6's `cec8fae734e0` has not yet run on Cloud SQL, and Step 6.7's seed migration depends on it. Without `--migrate`, Cloud SQL would still have `module_enum` and `/permissions` + `/permission-matrix` would 500.
- **Split `user_role_assignments` into platform / tenant tables** at Step 6.8.1 (DDL + migration + smoke). First of three sub-steps under section 6.8 retiring the FN-AB-14 IS-NULL gate via the table split (per D-34). Two new tables: `platform_user_role_assignments` (no RLS, platform-global, references platform_users only) and `tenant_user_role_assignments` (RLS+FORCE with the unconditional D-29 OR-branch matching the other 5 multi-tenant tables; composite FKs to `tenant_users (tenant_id, id)` and `org_nodes (tenant_id, id)` make AI-RBAC-06 cross-tenant injection structurally impossible at the schema layer rather than enforced at the application layer). Both new tables carry an audience-check row-level trigger (`enforce_platform_role_audience`, `enforce_tenant_role_audience`) that rejects mismatched role audience on INSERT or UPDATE OF role_id — CHECK constraints can't query other tables; trigger required. New DDL `db/raw_ddl/Ithina_postgres_SQL_DDL_rbac_v3.sql` is the post-split as-shipped baseline; v2 DDL stays as historical record per the frozen-DDL convention. **One forward-only-but-reversible Alembic migration** `3e05299cb533` (down_revision `2fdc4bc9f4cb`): adds `UNIQUE (tenant_id, id)` on `tenant_users` as a precondition for the composite FK (constraint name `uq_tenant_users_tenant_id` mirrors `org_nodes`' `uq_org_nodes_tenant_id`; this UNIQUE is NOT reflected in `tenant_users_v1.sql` per the frozen-DDL convention — live-vs-DDL drift documented in D-34, same precedent as the policy migrations `e59f62d5037d` / `4fd3aec6ae0c` / `21e2ad16303a`); creates the 2 new tables with full constraints/indexes/triggers; enables RLS+FORCE+policy on `tenant_user_role_assignments`; copies data via per-row tenant impersonation in a single DO block (mirrors the seed loader's `loaders/user_role_assignments.py` pattern — required because the migration session is the application role and the OLD URA's IS-NULL-gated policy doesn't admit TENANT-side reads without per-tenant `app.tenant_id` impersonation; copies 3 PLATFORM-audience rows + 19 TENANT-side rows with PLATFORM-and-impersonate); post-copy count assertion via DO block; drops `user_role_assignments`. **Reversible**: downgrade restores the v2 URA shape with the FN-AB-14 IS-NULL-gated policy text byte-equivalent to `4fd3aec6ae0c`'s upgrade; copies rows back via the same per-row impersonation pattern; drops new tables, trigger functions, and the precondition UNIQUE. Round-trip verified clean (3+19 → 3+19). **Smoke test refresh** at `scripts/smoke_test.py`: `test_7` extended to two assertions covering BOTH composite FKs on `tenant_user_role_assignments` (org_node side AND tenant_user side — the new structural-impossibility guarantee for AI-RBAC-06); `test_11` rewritten from the FN-AB-14 9-row truth table (no longer applicable; the table is gone) into 4 structural-invariant tests: 11a `platform_user_role_assignments` has no RLS (relrowsecurity=false), 11b `tenant_user_role_assignments` has RLS+FORCE, 11c `enforce_platform_role_audience` rejects TENANT-audience role, 11d `enforce_tenant_role_audience` rejects PLATFORM-audience role; `test_15` 6-table truth table extended to include `tenant_user_role_assignments` (54 cells = 6×9); `test_16` PLATFORM-INSERT extended with assertions for both new tables; `test_3` updated to reference `tenant_user_role_assignments` instead of the dropped URA. **Smoke test count: 74 → 81 PASS** post-truncate (+7 net). **`scripts/verify_cloud_schema.py`** module docstring updated: expected table count 12 → 13 (12 application + alembic_version); RLS list updated (tenant_user_role_assignments replaces user_role_assignments). **CLAUDE.md updates this step** include: schema state line (11→12 application tables; URA replaced by 2 new tables); D-29 amended to remove the IS-NULL-gated form (uniform unconditional shape post-split); FN-AB-14 deepened resolution note pointing at Step 6.8.1; new D-34 (mixed-audience tables get split per-audience). **BUILD_PLAN.md updates this step** include: new Section 6.8 introduction; Step 6.8.1 entry status DONE; Step 6.8.2 + 6.8.3 placeholder entries TODO (blocked-by chain documented). **architecture.md updates this step** TBD per the changed shape. **pytest delta**: 227 → 209 passed + 17 expected URA-stub failures (`relation "core.user_role_assignments" does not exist` in test_rbac_router and test_seed_loader). All 17 are localised to URA-stub references; precise list captured in this step's report. Step 6.8.2 fixes them by replacing the lightweight stub + `RolesRepo._user_count_subquery` with a UNION over the two new tables; Step 6.8.2 also updates the seed loader to route per row. mypy strict clean (60 source files); check_setup 35/35; alembic head moves `2fdc4bc9f4cb` → `3e05299cb533`. **No application code changes this step** (ORM/Repo/router/seed-loader land at 6.8.2; new endpoint at 6.8.3). **Local-only**: cloud deploy blocks on 6.8.3.
- **SUPER_ADMIN supplementary ADMIN-domain permissions** at Step 6.8.2.1. Operator-driven housekeeping commit (no implementation work for Claude Code; verification + doc updates + commit hygiene only). Seven ADMIN-domain permissions appended to `data/ithina_dev_seed_data.xlsx`'s `permissions` sheet (rows `_key=p28` through `p34`) and granted to SUPER_ADMIN via seven matching `role_permissions` rows: **ADMIN.STORES.VIEW.TENANT**, **ADMIN.STORES.CONFIGURE.TENANT**, **ADMIN.ORG_NODES.VIEW.TENANT**, **ADMIN.ORG_NODES.CONFIGURE.TENANT**, **ADMIN.USERS.VIEW.GLOBAL**, **ADMIN.ROLES.CONFIGURE.GLOBAL**, **ADMIN.USERS.CONFIGURE.GLOBAL**. **Hard precondition for Step 6.8 (RBAC enforcement layer)** — without these grants SUPER_ADMIN becomes structurally underprivileged the moment the resolver gates ADMIN-domain writes. **Resolves FN-AB-19** (now captured in CLAUDE.md's Forward-notes section; created and marked RESOLVED in this same commit per the FN-AB-21 precedent — never had a TODO period). `tests/integration/test_seed_loader.py` `EXPECTED_VISIBLE_COUNTS_PLATFORM` updated: `permissions` 23 → 30, `role_permissions` 113 → 120. **Two test fixture repairs** in `test_rbac_router.py` for unique-constraint collisions with the new catalogue rows: P4 (`test_p4_tenant_jwt_sees_full_catalogue`) repointed from previously-unseeded `(ADMIN, STORES, CONFIGURE, TENANT)` to `(ADMIN, STORES, EXECUTE, STORE)`; RP1 (`test_rp1_returns_role_permissions_with_parent_echo`) repointed from `(ADMIN, ORG_NODES, VIEW, TENANT)` and `(ADMIN, ORG_NODES, CONFIGURE, TENANT)` to `(ADMIN, ORG_NODES, EXECUTE, STORE)` and `(ADMIN, ORG_NODES, AUDIT, STORE)`. Both repairs preserve the test's intent (asserting the catalogue rows the test creates do NOT appear in unintended places) by picking permission tuples that are valid enum combinations but NOT in the seed. **No code changes** to `src/`. **No new tests.** Pytest unchanged at **263 passed** (same as post-6.8.3); smoke test 248 passed (unchanged); mypy clean on 65 source files; check_setup 35/35; alembic head unchanged at `3e05299cb533` (no migration). Re-deploy to Cloud SQL: bundled with 6.8.1 + 6.8.2 + 6.8.3 deploy via `--migrate`; the seed re-load against cloud uses the post-6.8.2.1 Excel.

- **Inline `roles[]` augmentation + standalone `/role-assignments` endpoint** at Step 6.8.3. Bundled commit covering both Step 6.1's A1/A2 forward note (inline roles[] on user endpoints) and the standalone `/role-assignments` endpoint that consumes 6.8.2's prepared `RoleAssignmentsRepo`. **Half 1 (A1/A2):** `GET /api/v1/tenant-users` (list + detail) and `GET /api/v1/platform-users` (list + detail) responses gain a `roles: list[UserRoleAssignmentItem]` field carrying 8 fields per item: `assignment_id`, `role_id`, `role_name`, `role_code`, `status`, `granted_at`, `org_node_id`, `org_node_name`. Append-only per D-31; URL unchanged; no new endpoint URL on the user side. **Half 2 (E4):** new `GET /api/v1/role-assignments` returns a grouped envelope `{platform_assignments: {items, pagination}, tenant_assignments: {items, pagination}}` — D-30 exception with per-block envelopes. Filters: `role_id`, `platform_user_id`, `tenant_user_id`, `tenant_id`, `org_node_id`, `status`. Sort vocabulary `granted_at_asc` / `granted_at_desc`. **Query posture for Half 1: jsonb_agg correlated subquery, mirroring `repositories/tenants.py:list_with_aggregates` exactly.** New `_roles_subq()` helpers in `repositories/tenant_users.py` and `repositories/platform_users.py` produce the per-row JSONB array via `jsonb_build_object` + `aggregate_order_by(..., granted_at DESC, id ASC)` + COALESCE-to-`'[]'::jsonb`. The tenant-side join uses **composite-FK keys** `(tenant_id, tenant_user_id) → tenant_users (tenant_id, id)` and `(tenant_id, org_node_id) → org_nodes (tenant_id, id)` per Step 6.8.1's D-34 / AI-RBAC-06 invariant. Platform-side has no org_node anchor; the jsonb_build_object literally sets `org_node_id` and `org_node_name` to NULL so the wire shape stays uniform. **BREAKING return-type change** on `TenantUsersRepo.list / get_by_id` and `PlatformUsersRepo.list / get_by_id` — return rows now wrap as `TenantUserListRow` / `PlatformUserListRow` dataclasses (parallel to `TenantListRow`); routers updated with hand-written `_list_item_from_row` / `_detail_from_row` mappers, mirroring `routers/v1/tenants.py:_list_item_from_row`'s `Module` mapper exactly. **Schema home for `UserRoleAssignmentItem`: `schemas/tenant_user.py`** (re-exported from `schemas/platform_user.py` for symmetry). Distinct from the richer nested shapes in `schemas/role_assignment.py` (used by Half 2 for the `/role-assignments` endpoint). **`schemas/role_assignment.py` rewritten** (path-(a) trigger #8 resolution) to use per-block envelope wrappers `PlatformAssignmentsBlock` / `TenantAssignmentsBlock` (each `{items, pagination}`) instead of flat `list[Item]`; per-row item types (`PlatformAssignmentItem`, `TenantAssignmentItem`, the `_Assigned*` mini-objects) preserved from 6.8.2. **Audience routing on `/role-assignments` is a CALL-SITE DECISION, not a column filter** (locked decision 12, security-load-bearing): TENANT JWTs MUST NOT execute the platform-side query because `platform_user_role_assignments` has no RLS — the app-layer routing here is the only barrier. The R2 integration test asserts both the empty-block response shape AND the no-call invariant via patching `RoleAssignmentsRepo.list_platform_assignments` and asserting `call_count == 0`. **Filter-shape narrowing:** `platform_user_id` set → tenant block short-circuited (a platform user has no tenant assignments by definition); `tenant_user_id` / `org_node_id` set → platform block short-circuited; `tenant_id` set → narrows tenant block only (platform-side has no `tenant_id` column). **`RoleAssignmentsRepo.list_tenant_assignments` extended** with a new optional `tenant_id` filter for the PLATFORM-callers narrowing case; `ROLE_ASSIGNMENTS_SORT_KEYS: frozenset` added at module level for sort validation; reuses Step 5.2's `InvalidSortKeyError` / `InvalidSortKeyClientError`. **Conftest factories** added: `make_platform_user_role_assignment` and `make_tenant_user_role_assignment` (raw-SQL-INSERT pattern mirroring `make_tenant_user`); both auto-synthesise the placeholder revoking actor pair when `status='INACTIVE'` to satisfy the DDL `revoked_consistency` CHECK (which requires `revoked_at`, `revoked_by_user_id`, `revoked_by_user_type` to be co-set). **Local helper `_insert_active_platform_assignment` in `test_rbac_router.py` retired**; R4 swapped to use the conftest factory (cleaner; no manual cleanup loop). **Smoke test extended** (`scripts/test_endpoints.sh`): 4 new `req` calls inside `run_matrix_for_caller` testing `/role-assignments` per caller — list, list?limit=5, status=ACTIVE filter, invalid_sort 400. (4 entries × 4 callers = 16 new smoke checks; deviates from prompt's "+1 PASS" projection but matches the precedent for multi-user-type endpoints inside the per-caller matrix.) **Tests:** 18 new Half 1 tests in `test_tenant_users_router.py` (10 tu_* functions + U7 parametrized over 4 endpoint kinds = 14 collected items) and `test_platform_users_router.py` (7 pu_* functions). **Five LOAD-BEARING:** **U5_tu_list** (cross-tenant RLS isolation under TENANT JWT), **U5_tu_detail** (RLS-as-404 regression), **U6_tu_list** + **U6_pu_list** (pagination not broken by jsonb_agg), **U7** (negative-key assertion across all 4 endpoints — Pattern (b) audit-actor must NOT leak into roles[]). Plus 15 Half 2 tests in NEW `test_role_assignments_router.py` (R1-R15), **five LOAD-BEARING**: **R2** (TENANT JWT short-circuits platform-side; both response shape AND no-call invariant via Repo patch), **R3** (RLS scoping), **R7** (new `tenant_id` filter), **R8** (composite-FK injection rejection at DB), **R12** (PLATFORM no-impersonation regression). Updated 4 broken exact-set assertions in existing tests to add `"roles"` to the asserted set. **Pytest:** 227 → 263 (+36 collected; Half 1: 21 collected — 10 functions + U7 over 4 cases + adjustments to existing 13 → 27; Half 2: 15; pu side: 7). 263 passed. **Smoke test +16** entries inside `run_matrix_for_caller`. **mypy strict** clean on 65 source files (was 60; +5 — schemas + new router + dataclass row types). **alembic head unchanged** at `3e05299cb533` (no migration). **Pre-flight discrepancies surfaced and resolved:** the prompt's pre-flight item 9 stated `perms=27, rp=117` as expected; reality (per `test_seed_loader.py` and CLAUDE.md Step 6.1) is `perms=23, rp=113`. Operator confirmed reality is ground truth. The prompt's expected `pu=7` was also off — actual seed produces 3 platform_users (per seed Excel + `test_seed_loader.py:40`); response same as for perms/rp. Schema home for the rich `/role-assignments` shape per trigger #8 path (a) — the pre-emptive `RoleAssignmentsResponse` from 6.8.2 had flat `list[Item]` per block; rewritten to nested `{items, pagination}` per block. **`docs/endpoints/role-assignments.md`** added (8-section format, mirrors `tenant-users.md`). **`docs/endpoints/openapi.json` regenerated** via running uvicorn locally and curling `/api/v1/openapi.json` (`scripts/test_endpoints.sh` is the proper regen path; manual curl used during the build). New `/api/v1/role-assignments` path appears; `UserRoleAssignmentItem` schema with the 8 fields appears; `TenantUserRead.roles` and `PlatformUserRead.roles` reference it as `array of UserRoleAssignmentItem`. **CLAUDE.md** entry (this one); **BUILD_PLAN.md** Step 6.8.3 entry rewritten to reflect bundled scope; A1/A2/E4 marked RESOLVED; E5 retained as forward note; URL drift `/user-role-assignments` → `/role-assignments` reconciled across BUILD_PLAN. **Cloud deploy unblocked** — bundled deploy of 6.8.1 + 6.8.2 + 6.8.3 ships via `--migrate` (depends on 6.8.1's `3e05299cb533` migration which has not yet hit Cloud SQL).

- **ORM models, Repos, schemas, seed loader for the post-split URA tables** at Step 6.8.2. Second of three sub-steps under section 6.8; resolves the 17 pytest failures Step 6.8.1 left behind. **Two new full ORM models** at `models/platform_user_role_assignment.py` and `models/tenant_user_role_assignment.py` (file-per-table per project convention) — mirror `models/tenant_user.py` / `models/role.py` shape: PG_ENUM declarations with `create_type=False, native_enum=True, values_callable=...` per the convention; `FetchedValue()` defaults on `id` / `status` / `granted_at` / `updated_at`; Pattern (b) audit-actor pairs (no SA `ForeignKey` on `*_user_id`, dialect-specific `actor_user_type_enum` on `*_user_type`); composite FKs to `tenant_users(tenant_id, id)` and `org_nodes(tenant_id, id)` are NOT declared at the SA layer (existing project convention — composite FKs to non-PK columns require explicit `ForeignKeyConstraint`; no Repo query needs the SA-layer FK to navigate). New `UserRoleAssignmentStatus` Python enum (ACTIVE/INACTIVE) declared in `platform_user_role_assignment.py`; imported from there by `tenant_user_role_assignment.py`. `ActorUserType` reused from `models/tenant_user.py:73` per the existing convention. Models re-exported via `models/__init__.py`. **`models/_lightweight_stubs.py::UserRoleAssignment` removed**; only `Store` stub remains (Step 4.5 territory). **`RolesRepo._user_count_subquery` rewritten** as the SUM of TWO independent correlated scalar subqueries (one per physical table), with `.correlate(Role)` on EACH branch — third occurrence of the L9/L11/R4 trap, this time on TWO subqueries instead of one; the R4 test (`test_r4_user_count_aggregate_correlates_per_role`) is the load-bearing regression check. Cleaner than UNION-then-SUM because each `.correlate(Role)` is on a single subquery (more obvious; easier to read; harder to get wrong) and there's no extra subquery wrapper around a UNION. Returns a SQLAlchemy column expression that callers wrap in `.label("user_count")` — same contract as the previous helper; the two call sites (lines 151, 181) need NO changes. The audience-check triggers guarantee a role's assignments live in exactly one of the two tables, so one branch is always 0 per role row. **New `RoleAssignmentsRepo`** at `repositories/role_assignments.py` with two list methods (`list_platform_assignments`, `list_tenant_assignments`) — used by Step 6.8.3's `/role-assignments` router; defined here so 6.8.3's wire-up is mechanical. Default sort `granted_at_desc` mirrors PlatformUsersRepo precedent; reuses the shared `InvalidSortKeyError` from `repositories/_errors.py`. **New schemas** at `schemas/role_assignment.py` (`PlatformAssignmentItem`, `TenantAssignmentItem`, `RoleAssignmentsResponse`) — defined here so 6.8.3 can use them without refactoring; hidden fields per the H1 convention (D-13 Pattern (b)); `extra="forbid"` on every model as the drift guard. **Seed loader rewritten** at `scripts/seed_dev_data/loaders/user_role_assignments.py` as a routing loader: per-row inspection of which user-side FK is populated, route to `platform_user_role_assignments` or `tenant_user_role_assignments`, pop the columns that don't exist on the target table from the insert dict before INSERT. Per-row tenant impersonation removed (no longer needed under the unconditional D-29 OR-branch on `tenant_user_role_assignments`); `_set_tenant_guc` helper deleted (no other callers — verified via repo-wide grep). `column_mappings.py` `USER_ROLE_ASSIGNMENTS` SheetMapping unchanged in shape; comment rewritten to describe loader-level routing. `truncate.py` `SEED_TABLES` list replaces `user_role_assignments` with the two new tables (both leaves; no inbound FKs). `_base.py` and `README.md` docstrings updated to reflect the post-split shape. The Excel sheet is unchanged (still 22 logical rows: 3 PLATFORM-audience + 19 TENANT-side). **Test cutover**: `test_rbac_router.py` `_insert_active_platform_ura` -> `_insert_active_platform_assignment` (writes to `platform_user_role_assignments`; significant simplification — no more NULL placeholders for the dropped tenant_id/tenant_user_id/org_node_id columns); `_delete_uras_by_id` -> `_delete_assignments_by_id`. The 13 R/P/RP/M/H tests using these helpers go green automatically on the renames + the `_user_count_subquery` rewrite. `test_seed_loader.py` `EXPECTED_VISIBLE_COUNTS_PLATFORM` dict's `user_role_assignments: 3` replaced with `platform_user_role_assignments: 3` and `tenant_user_role_assignments: 19` (PLATFORM session sees both tables in full post-split). `EXPECTED_URA_TOTAL = 22` renamed to `EXPECTED_ASSIGNMENTS_TOTAL = 22`. `test_l2b_user_role_assignments_total_across_tenants` renamed to `test_l2b_role_assignments_total_split_correctly` and rewritten — no per-tenant impersonation; PLATFORM session reads both physical tables and asserts the sum. The PLATFORM-audience sentinel-row check in `test_l3_seed_sentinel_rows` updated to count `platform_user_role_assignments` rows directly (no XOR check; physical-table separation IS the audience guarantee). 4 L tests now pass. **Documentation bundling**: `docs/endpoints/rbac.md` `user_count` field description and the "Behaviour notes" RLS-scoping bullet rewritten to describe SUM-of-two-correlated-subqueries (PLATFORM and TENANT branches; audience-check trigger guarantee that one branch contributes 0; RLS-scoping on the tenant-side branch only). 226 pytest passes (was 209, +17 — exactly the 17 known-failing tests now green; no others changed state); mypy strict clean on 87 files (was 60; +27 — adds the test/scripts trees that were already mypy-clean and the 4 new source modules); check_setup 35/35; smoke test 81/81 PASS post-truncate (unchanged from 6.8.1 — no schema changes this step); alembic head unchanged at `3e05299cb533` (no migration). Per-resource regression checkpoint clean: tenants 34, platform_users 10, tenant_users 13, org_tree 21, lookups 4, dashboard 17, modules-access 15. Cross-tenant integrity verification query (`SELECT ... WHERE t.tenant_id != tu.tenant_id OR t.tenant_id != on_.tenant_id`) returns ZERO rows post-reseed — composite FK guarantees confirmed empirically. **Local-only**; cloud deploy still blocks on Step 6.8.3.
- **`has_permission()` core** at Step 6.9.1. Pure-SQL single-tuple permission check at `src/admin_backend/auth/permissions.py`. Signature: `has_permission(session, auth, module, resource, action, scope, target_anchor=None) → tuple[bool, ReasonCode, str]`. Dispatches on `auth.user_type`. PLATFORM joins `platform_user_role_assignments` ⋈ `role_permissions` ⋈ `permissions` filtered by `platform_user_id` and `status='ACTIVE'`; no anchor cascade, no `tenant_module_access` JOIN. TENANT adds `org_nodes` (composite key `(tenant_id, org_node_id)` per D-34) and `tenant_module_access` (`status='ENABLED'` filter), with anchor cascade via Postgres `ltree <@` operator. Raw `text()` with schema-qualification per the raw-SQL convention; LIMIT 1 on both paths. `PermissionGrant` frozen dataclass at `src/admin_backend/auth/permission_grant.py` shipped for Step 6.9.2's `/me/permissions` endpoint; not consumed by `has_permission()` itself. `ReasonCode` `StrEnum` at `src/admin_backend/auth/reason_code.py` with two values in v0 (`GRANT_MATCHED`, `NO_MATCHING_GRANT_OR_OUT_OF_SCOPE`); granular codes deferred to Step 6.16's audit log writes. 13 integration tests at `tests/integration/test_has_permission.py`; 5 LOAD-BEARING (T_C1 cascade-correctness, T_C3 sibling-region segment-boundary respect, T_M1 module-DISABLED denial, T_T3 inactive-assignment denial, T_X1 cross-tenant injection guard end-to-end). Tests look up seeded permission rows via a local `_lookup_permission_id` helper rather than calling `make_permission` (the seed already populates the 30 canonical tuples; a parallel `make_permission` would violate `uq_permissions_code`). T_M1 uses `module_access_status_enum='DISABLED'`; the live enum has only `ENABLED`/`DISABLED`, not the prompt's `SUSPENDED`/`NOT_PROVISIONED`. EXPLAIN ANALYZE (seeded data, local): PLATFORM 0.169 ms hitting `uq_platform_user_role_assignments_active` + `pk_permissions`; TENANT 0.196 ms hitting `uq_permissions_tuple` + `uq_tenant_user_role_assignments_active` + `ix_org_nodes_tenant_type`. The two Seq Scans in plans (`role_permissions` 118 rows, `tenant_module_access` 13 visible rows) are on small tables where PG correctly picks Seq+Hash. **Type-drift fix from prompt's scope dropped**: investigation report F-REPO-4 was stale at write-time; `RoleAssignmentsRepo.list_tenant_assignments` already declared `tuple[list[TenantUserRoleAssignment], int]` correctly since Step 6.8.2 (commit `de9a39cd`). No code change in 6.9.1 for this item. Operator-confirmed at pre-flight (Q1/(a), 2026-05-13). 276 pytest passes (263 + 13); mypy strict clean on 68 source files; smoke test 81/81 post-truncate; check_setup 35/35; alembic head unchanged at `3e05299cb533` (no migration). No new endpoints; OpenAPI unchanged. Three new forward-notes: FN-AB-23 (impersonation read-only enforcement), FN-AB-24 (`has_permission` caching), FN-AB-25 (target_anchor resolution pattern for 6.9.2). Not yet wired into FastAPI; Step 6.9.2 ships the gate dependency that calls this function.

- **Gate factory + PermissionDeniedError + /me/* endpoints** at Step 6.9.2. `require(module, resource, action, scope)` factory at `src/admin_backend/auth/permissions.py` returns a FastAPI dependency that calls `has_permission()` and raises `PermissionDeniedError` on denial; novel dependency-factory pattern in v0 (no precedent before this step, see "Note on dependency factories"). `target_anchor` hardcoded to `None` inside the gate for 6.9.2; per-resource anchor dependencies and threading land in Step 6.9.3 retrofit. `PermissionDeniedError` at `src/admin_backend/errors.py` (shared, system-wide, mirrors `InvalidSortKeyClientError`'s Step 5.2 promotion precedent): `ClientError` subclass, `http_status=403`, `code='PERMISSION_DENIED'`. Structured fields (`module`, `resource`, `action`, `scope`, `target_anchor`, `reason_code`) attach via `**context` and reach error logs only; response envelope's `details` stays `null` per Q7 design. `get_permissions_for_user()` companion query at `auth/permissions.py` — same JOIN structure as `has_permission` per audience, per-tuple `WHERE` clauses dropped, projection widened to `(module, resource, action, scope, anchor_path)`. Separate-methods strategy chosen over shared-helper extraction (the two functions are each short and copy-paste of the JOIN block with focused edits; extracting a helper made both methods harder to read). `me_router` at `src/admin_backend/routers/v1/me.py` with `/me` prefix; two routes — `GET /me/permissions` returns `{"permissions": [PermissionGrantRead, ...]}` (always array; empty if no grants; D-30 batch-by-key envelope precedent at Step 3.6) and `GET /me/can-do?module=...&resource=...&action=...&scope=...&target_anchor=...` returns `{"allowed": bool, "reason_code": str}` (D-30 single-resource exception per `TenantDetail` precedent). Both endpoints multi-user-type with RLS-driven persona projection; no `require(...)` gate applies (caller-state endpoints). New schemas at `src/admin_backend/schemas/me.py` (`PermissionGrantRead`, `MePermissionsResponse`, `MeCanDoResponse`); `extra="forbid"` on every model; enum fields typed as `str` so StrEnum subclasses serialise to canonical value strings without coercion ceremony. 18 integration tests at `tests/integration/test_me_router.py` (6 MP, 7 MC, 4 GF, 1 XT); **4 LOAD-BEARING** (T_GF1 factory produces FastAPI-compatible dependency, T_GF2 denial returns 403 with envelope contract, T_GF3 allow runs handler body, T_GF4 denial NEVER fires handler body Repo call via `patch.object` + `AsyncMock` + `call_count == 0` mirror of Step 6.8.3's R2). T_GF4's test-only `/api/v1/_test_gated_global` endpoint mounted via a per-test `app_client_with_gate` fixture; its handler calls a module-level `_test_repo = TenantsRepo()` instance that the test patches — Repo singleton bound at module level so the patch reaches the same instance the handler uses. EXPLAIN ANALYZE (`/me/permissions` against seed): PLATFORM 0.170 ms (30 grants for Anjali/SUPER_ADMIN), TENANT 0.314 ms (19 grants for Marcus T/Buc-ee's OWNER); hits `pk_permissions` and `ix_tenant_module_access_tenant_id`. 294 pytest passes (276 + 18); mypy strict clean on 70 source files (was 68; +2 — `schemas/me.py` + `routers/v1/me.py`). `scripts/smoke_curl.sh` 20 → 22 checks; `scripts/test_endpoints.sh` matrix +2 entries × 4 callers = 8 new calls (248 → 256 total); `scripts/test_endpoints_cloud.sh` mirrors with +2 matrix entries. check_setup 35/35. smoke_test 81/81 post-truncate. alembic head unchanged at `3e05299cb533` (no migration). `docs/endpoints/me.md` (8-section format covering both endpoints); `docs/endpoints/openapi.json` regenerated with both `/me/*` paths and the three new schemas. Two new forward-notes: FN-AB-26 (`_require_platform_auth` retirement decision deferred to 6.9.3), FN-AB-27 (`/me/permissions` response shape simplification revisit at 6.9.3 retrofit). FN-AB-25 (target_anchor resolution pattern) DECISION LOCKED to pattern (b) per-endpoint anchor dependencies; per-resource anchor functions ship at 6.9.3. Not wired into any existing production endpoint; 6.9.3 retrofits.

- **Scope cascade in `has_permission`** at Step 6.9.3.1. Downward cascade per the locked design (a grant at level N satisfies checks at every level below N). New `satisfying_scopes(requested: PermissionScope) -> list[str]` helper at `src/admin_backend/auth/permissions.py` translates a requested scope into the cascade-satisfaction set; new `_SCOPE_CASCADE_ORDER` tuple encodes all 8 hierarchy levels (`GLOBAL` plus the 7 `org_node_type_enum` values: TENANT, BUSINESS_UNIT, HQ, COUNTRY, REGION, STORE, DEPARTMENT). Both `has_permission` SQL paths replace `AND p.scope = CAST(:scope AS permission_scope_enum)` with `AND p.scope = ANY(CAST(:satisfying_scopes AS permission_scope_enum[]))`; first `ANY()` array-bind in the codebase. Private `_satisfying_scopes_for_sql` companion intersects the helper's full list with `_PERMISSION_SCOPE_ENUM_VALUES = frozenset(s.value for s in PermissionScope)` before binding — Postgres rejects strings outside the enum at `CAST(... AS permission_scope_enum[])` time, so forward-compat levels (`BUSINESS_UNIT`, `HQ`, `COUNTRY`, `REGION`, `DEPARTMENT`) stay inert until the DB enum expands. `get_permissions_for_user` NOT modified — returns raw grants per the design lock; cascade is the gate's concern, not `/me/permissions`'s. 8 cascade integration tests (T_SC1-T_SC8) at `tests/integration/test_has_permission.py`; **2 LOAD-BEARING** (T_SC6 STORE grant fails TENANT check — cascade direction is downward only; T_SC8 cross-tenant cascade still denied — scope cascade doesn't compromise `target_anchor` enforcement). 6 helper unit tests at NEW `tests/unit/test_permissions_helpers.py` (3 `satisfying_scopes()` per-enum-value + 3 `_SCOPE_CASCADE_ORDER` integrity checks: length, enum-coverage, canonical-match). 308 pytest passes (294 + 8 cascade + 6 unit); mypy strict clean on 70 source files; smoke_test 81/81 post-truncate; smoke_curl 22/22 (unchanged); test_endpoints 256/256 (unchanged). EXPLAIN ANALYZE: PLATFORM 0.139 ms / TENANT 0.146 ms; both still hit `pk_permissions` index — the `ANY` predicate is a different filter shape, not an index-degrading rewrite. alembic head unchanged at `3e05299cb533` (no migration). No endpoint or catalogue changes. New "Note on org-hierarchy coupling" maintenance convention documents the two in-repo sync points (DDL `org_node_type_enum` + `_SCOPE_CASCADE_ORDER` tuple). FN-AB-28 added (PermissionScope enum expansion); FN-AB-26 updated in place with post-cascade context. Prompt's caution #2 was empirically wrong (Postgres rejects out-of-enum strings even in array CAST); the `_satisfying_scopes_for_sql` filter handles the constraint. Prompt referenced `docs/Ithina_Admin_Frontend.md` section 5.5 as the cascade-spec source; that file isn't in this repo at HEAD; cascade rule canonical statement now lives in the design-conversation prompt file bundled into this commit.

- **Endpoint retrofit to `require(...)` gate + per-resource anchor deps + mandatory-gate-discipline test** at Step 6.9.3.2. Section 6.9 closes here: every authenticated production route is now either gated by `Depends(require(M, R, A, S, *, anchor_dep=None))` or appears in `GATE_EXEMPT_PATHS`; the gate-discipline meta-test (`tests/integration/test_gate_discipline.py`) enumerates every `APIRoute` and asserts the disjoint trichotomy holds. **14 endpoints retrofitted** across 7 routers (`tenants`, `platform_users`, `tenant_users`, `org_tree`, `dashboard`, `modules_access`, `role_assignments`); **5 endpoints in allowlist** (`/lookups`, `/permissions`, `/permission-matrix`, `/roles`, `/roles/{role_id}/permissions`); **2 endpoints exempt as caller-state** (`/me/permissions`, `/me/can-do`). **3 new modules** at `src/admin_backend/auth/`: `gate_info.py` (`PermissionGateInfo` frozen dataclass marker — written onto the inner gate function as `__permission_gate__` so the discipline test can introspect every route's gate tuple), `anchor_deps.py` (3 functions — `get_tenant_anchor`, `get_org_node_anchor`, `get_tenant_user_anchor` — each raises the appropriate `*NotFoundError` on lookup miss per F-THREADING-4; never returns `None` — returning `None` would short-circuit `has_permission`'s cascade clause to TRUE, creating a security regression), `gate_allowlist.py` (`GATE_EXEMPT_PATHS: frozenset[str]` with the 7 paths listed above; explicit hand-maintained list). `require(...)` factory at `auth/permissions.py` extended with a keyword-only `anchor_dep` parameter; two inner-function shapes (no-anchor and with-anchor) keep FastAPI's static-signature requirement satisfied while preserving cascade semantics in `has_permission`. Both shapes carry `gate.__permission_gate__ = PermissionGateInfo(...)`. **`_require_platform_auth` retired** from `platform_users.py`; its 2 call sites use `Depends(require(ADMIN, USERS, VIEW, GLOBAL))`; `PlatformAccessRequiredError` left as documented dead code (FN-AB-33 — final removal at next cleanup pass). **2 errors promoted** from router-local to shared `errors.py` (`TenantUserNotFoundError`, `OrgNodeNotFoundError`) so anchor deps can raise them. **org_tree.py loop-variable rename**: `for _ in range(MAX_REDUCTIONS):` collided with the new `_: None = Depends(require(...))` gate param; renamed to `for _reduction in range(...)`. **Wire-contract changes**: TENANT-JWT denial at `/platform-users` returns `code=PERMISSION_DENIED` (was `PLATFORM_ACCESS_REQUIRED`); still 403; all other endpoint behaviour identical for SUPER_ADMIN (cascade carries every gate). **Tests**: `tenant_owner_jwt_factory(tenant_id, with_grants=...)` factory in `conftest.py` builds synthetic user + role + grants + assignment + tenant_module_access (required for the TENANT-path has_permission JOIN) + org_node; function-scoped with DELETE-tracked teardown in FK-aware parameter order (`make_tenant_user_role_assignment` listed LAST so LIFO teardown clears the FK before underlying rows). `super_admin_jwt` fixture (queries Anjali by email, mints JWT) — function-scoped, read-only. Inline `strict=False` rationale comment in the conftest factory docstring per the operator's third ask. `test_gate_discipline.py` is a single load-bearing meta-test. `test_gate_retrofit.py` is 10 new tests (T_RET_1-8 with 5a/5b/8a/8b split); **3 LOAD-BEARING** (T_RET_3 anchor-miss security regression, T_RET_5b retirement equivalence, T_RET_6 marker positive verification). **15 existing tests** marked xfail across 5 router test files (7 dashboard, 3 modules_access, 1 org_tree, 1 tenants D3, 1 retrofit-T_RET_2 + 2 dashboard already-xfailed during 6.9.3) — all `strict=False` so seed update auto-flips them to pass. **6 tests renamed** to assert post-retrofit gate-denial semantics (TENANT JWTs now hit 403 PERMISSION_DENIED at endpoints requiring ADMIN.TENANTS.VIEW.GLOBAL): test_a2_tenant_jwt_returns_403_platform_access_required → test_a2_tenant_jwt_returns_403_permission_denied (platform_users), test_l5e_sort_aggregate_under_tenant_jwt_rls_correct → test_l5e_tenant_jwt_denied_at_tenants_list_with_sort, test_l8_list_under_tenant_returns_only_own_row → test_l8_tenant_jwt_denied_at_tenants_list, test_s2_stats_under_tenant_returns_own_counts → test_s2_tenant_jwt_denied_at_stats, test_d3_detail_tenant_own_id_returns_200 → test_d3_detail_tenant_owner_own_id_returns_200 (also xfailed), test_t18 assertion code updated from TENANT_NOT_FOUND to ORG_NODE_NOT_FOUND (anchor dep fires first on cross-tenant org_node probe — different code, same 404 shape). **`scripts/test_endpoints.sh`, `test_endpoints_cloud.sh`, `test_endpoints_max_view.sh`** comment lines updated PLATFORM_ACCESS_REQUIRED → PERMISSION_DENIED (no assertion logic changes; the curl strings already match). **319 collected, 306 passed, 13 xfailed** (was 308 passed; net +9 new from gate-discipline + retrofit tests, -15 → xfailed). mypy strict clean on 73 source files (was 70; +3 new modules); check_setup 35/35; smoke_test 81/81 post-truncate (no schema changes); alembic head unchanged at `3e05299cb533` (no migration). Per-resource regression checkpoint: tenants 33 pass + 1 xfail, platform_users 17, tenant_users 27, org_tree 20 pass + 1 xfail, dashboard 10 pass + 7 xfail, modules-access 12 pass + 3 xfail, role_assignments 15, rbac 24, lookups 4, me_router 18, has_permission 13 + 8 cascade. **No DDL changes; no migration; no seed Excel changes** (the OWNER catalogue gap is documented as FN-AB-29 and Phase 3b seed update will flip the xfails). New forward notes FN-AB-29 through FN-AB-33 added; FN-AB-26 marked RESOLVED. New "Note on gate allowlist coupling" maintenance convention codifies the GATE_EXEMPT_PATHS sync discipline (any new route must either gate or land in the allowlist; the meta-test enforces). `docs/endpoints/openapi.json` regenerated.
- **Step 6.9.3.2 cleanup — Phase 3 seed update applied; test infrastructure reconciled** at 2026-05-13. Follow-on commit to `80911fa`. Operator applied Phase 3 seed updates post-commit: `permissions` 30→31 (one new tuple `(ADMIN, TENANTS, VIEW, TENANT)`; code column carries a TENANTS-plural display typo per FN-AB-34), `role_permissions` 120→122 (2 new OWNER grants). Pre-cleanup state was 305 passed + 1 failed (`test_l2_seed_row_counts` stale snapshot) + 13 xfailed. **Audit-discipline lesson — 11 of the 13 xfails were misclassified at 6.9.3.2 write-time as "needs seed update" when they actually needed factory migration**: only D3 and T_RET_2 used `tenant_owner_jwt_factory`; the other 11 used `_tenant_jwt` (random-UUID JWT). Random-UUID JWTs have no `tenant_user_role_assignment` row, so `has_permission` denies 403 regardless of seed state. The xfail reason text was uniform-looking but the underlying causes were two structurally different problems; reading the reason without auditing the JWT mechanism missed it. Removing markers alone left those 11 failing; the cleanup migrates 11 test bodies to the factory. **Lesson for future xfail batches**: when marking a group of tests as xfail with a shared reason, also audit each test's JWT/fixture mechanism. Tests that share a symptom (403) can have different root causes (missing-grant vs missing-DB-row vs missing-anchor). **Factory changes at `tests/integration/conftest.py`**: (a) permission lookup switched from code-string `WHERE code = ...` to structural tuple `WHERE module = ... AND resource = ... AND action = ... AND scope = ...` per the `uq_permissions_tuple` UNIQUE; runtime gate identity is the tuple, the factory now matches; closes the vulnerability to display-string drift via seed-Excel typos. (b) Default grants extended from 1 tuple to 3: `(ADMIN, USERS, VIEW, TENANT)` (existing) plus `(ADMIN, TENANTS, VIEW, TENANT)` and `(ADMIN, ORG_NODES, VIEW, TENANT)` (added post Phase 3). Targeted extension, not a mirror of OWNER's 21 seeded tuples. (c) `tenant_module_access` insert switched to SELECT-then-conditional-`make_tenant_module_access` rather than unconditional INSERT — the previous unconditional INSERT raised `uq_tenant_module_access_tenant_module` violations when a test pre-created an ADMIN TMA row. Existing fixture-level DELETE teardown is preserved via this path. **Factory caller contract** documented in conftest docstring: ensures TMA presence not status — pre-existing ENABLED row → factory no-ops, gate passes; pre-existing DISABLED row → factory no-ops, gate denies (test must use a different module for the DISABLED case). (d) Tenant-root org_node reused if any exists for the tenant; the factory was creating a 2nd TENANT-type root and `get_tenant_anchor`'s `LIMIT 1` then non-deterministically picked the test's root while the assignment was anchored at the factory's, breaking the cascade. **11 test bodies migrated** `_tenant_jwt(settings, tid)` → `await tenant_owner_jwt_factory(tid)`: 7 in `test_dashboard_router.py` (S2, S5, S6, O2, O4, O5, O6), 3 in `test_modules_access_router.py` (M2, M5, X1), 1 in `test_org_tree_router.py` (T11). **S2 assertion bumped** `platform_users.value == 2` → `== 3` (the factory adds 1 ACTIVE tenant_user to tenant_a). **M5 module swap** DISABLED ADMIN → DISABLED GOAL_CONSOLE (test intent "DISABLED rows excluded from count" preserved; module choice was incidental; required because the factory needs ENABLED ADMIN per its caller contract). **13 xfail markers removed** (the 11 migrated + D3 + T_RET_2). Vestigial pre-decorator comments referencing Phase 3b deferred state also cleared. **`test_l2_seed_row_counts`** expected counts updated to match Phase 3 state (permissions 31, role_permissions 122); all other counts in the snapshot unchanged. **FN-AB-34 added** (seed-loader validation forward note — assert `excel.code == f"{module}.{resource}.{action}.{scope}"` for each permission row at load time; catches display-string typos at load time even though runtime code is robust via tuple-lookup). The Phase 3 update originally shipped one such typo (code `ADMIN.TENANTS.VIEW.TENANTS` plural vs scope `TENANT` singular); operator corrected the Excel `code` cell to `ADMIN.TENANTS.VIEW.TENANT` (singular) and bundled it into this commit. Local DB and Cloud SQL retain the original typo in the `code` column until the next seed reload picks up the corrected Excel; runtime is unaffected (tuple identity is authoritative). **319 passed, 0 failed, 0 xfailed** (was 305 + 1 failed + 13 xfailed; +14 net pass moves). mypy strict clean on 73 source files (unchanged); check_setup 35/35; smoke_test 81/81 post-truncate (no schema changes); alembic head unchanged at `3e05299cb533` (no migration); no DDL changes; **seed Excel updated** (`data/ithina_dev_seed_data.xlsx` bundled — Phase 3 permissions and role_permissions sheets reflecting +1 permission tuple and +2 OWNER grants, with corrected `code` value). Section 6.9 fully closed.
- **Step 6.14: role-assignment writes (per-anchor `roles[]` + diff-replace).** DONE-LOCAL 2026-05-16. Existing POST + PATCH `/tenant-users` body's `roles` flips from `list[UUID]` to `list[{role_id, org_node_id}]`; diff-replace retires whole-set replace; 3 new error codes; resolves FN-AB-41. Detail: `docs/implementation-steps/step-6_14-role-assignment-writes-2026-05-16.md`.
- **Step 6.13: org-tree write endpoints (Add Node + Edit Node)** at 2026-05-16. Two endpoints (POST add, PATCH edit) on the existing org-tree router; multi-audience, gated on `ADMIN.ORG_NODES.CONFIGURE.TENANT`. Reparent rewrites moved node + descendants' ltree paths atomically. Role assignments stable across moves (D-11). Local catalogue update (FN-AB-47) closed the OWNER + PLATFORM_ADMIN gap; cloud deferred to next Phase 6 deploy. 42 new tests, smoke 47/47, pytest 463→505. Detail: `docs/implementation-steps/step-6_13-org-tree-writes-2026-05-16.md`.
- **Step 6.17.2: Stores GET endpoints + full Store ORM (retires lightweight stub).** DONE-LOCAL 2026-05-18. Two GET endpoints on `/api/v1/stores`, multi-user-type, gated on `ADMIN.STORES.VIEW.TENANT` with `get_store_anchor` on detail. Detail: `docs/implementation-steps/step-6_17_2-stores-get-2026-05-18.md`.
- **Step 6.17.3: Stores POST + PATCH endpoints (writes, multi-audience).** DONE-LOCAL 2026-05-18. Two new endpoints on `/api/v1/stores`, multi-audience (LD1, diverges from tenants POST), gated on `ADMIN.STORES.CONFIGURE.TENANT`. POST uses `_tenant_exists` pre-check to convert cross-tenant `tenant_id` bodies to 404 `TENANT_NOT_FOUND`; PATCH binds `anchor_dep=get_store_anchor`. `store_code` uniqueness is case-insensitive (aligned with DDL partial unique index `uq_stores_tenant_store_code_lower` — LD5 prompt-vs-codebase contradiction resolved). `status` omitted from INSERT so the DDL default fires (LD8; current DDL default is `ACTIVE`, product-intent is `OPENING`, deferred to a future migration). `org_node_id` immutable on PATCH (LD3). 41 new tests, smoke 49→52, pytest 540→581. Detail: `docs/implementation-steps/step-6_17_3-stores-writes-2026-05-18.md`.
- **Step 6.17.4: Stores POST set-status endpoint (state-transition, multi-audience).** DONE-LOCAL 2026-05-18. First step authored under A7+A8+A9 discipline. Single new endpoint `POST /api/v1/stores/{store_id}/set-status`, multi-audience, gated on `ADMIN.STORES.CONFIGURE.TENANT` with `anchor_dep=get_store_anchor` (same gate as PATCH per LD9). Deliberate divergence from tenants per-action-endpoint pattern (4 states + 9 transitions → consolidated set-status URL per LD6; POST + hyphenated name per project convention). 9-cell liberal `TRANSITION_MATRIX` rejects `*->OPENING` and same-state per LD1/LD5. Three SQL classes dispatch on direction: Class 1 (into-CLOSED) populates `closed_*` triplet; Class 2 (out-of-CLOSED) nulls it atomically with the status flip per `ck_stores_closed_consistency`; Class 3 (between non-CLOSED) leaves `closed_*` untouched. Reuses `TransitionResult` from `repositories.tenants` (mirrors tenant_users pattern). Reuses `InvalidStateTransitionError` as-is — class's `public_message` is tenant-flavored ("Tenant cannot transition...") and reused for stores per operator decision Option A (matches tenant_users precedent; FN-AB-52 tracks future generalisation). `reason` field accepted at schema layer, silently dropped at repo layer until Step 6.2 audit_log ships (FN-AB-53 tracks integration). 30 new tests (16 repo T + 14 router RT/MG), smoke 52→54, pytest 581→611. Detail: `docs/implementation-steps/step-6_17_4-stores-set-status-2026-05-18.md`. Closes Step 6.17 series.
- **Step 6.18.1: ADMIN.ROLES.OVERRIDE.GLOBAL catalogue seed delta.** DONE-LOCAL + CLOUD APPLIED 2026-05-19. Operator-driven Excel edit + local seed + Cloud SQL inline UPSERT; +1 permission row (ADMIN.ROLES.OVERRIDE.GLOBAL, sole holder SUPER_ADMIN), +1 role_permissions grant, test count updates (permissions 35 -> 36, role_permissions 131 -> 132). Unblocks the future Step 6.18.3 PATCH /api/v1/roles/{role_id} gate resolution. Two new FN-AB entries captured: FN-AB-55 (cross-env audit-actor drift on catalogue inserts) and FN-AB-56 (NIGHT_SHIFT_LEAD is_system=false intent confirmation). See `docs/implementation-steps/step-6_18_1-roles-catalogue-2026-05-19.md` for plan, mental model, and retro.
- **Step 6.18.2: GET /api/v1/roles/{role_id} detail endpoint.** DONE-LOCAL 2026-05-19. Self-contained role detail (E7) for the role-edit screen: returns `RoleDetail` carrying role metadata + held permissions + grantable permissions (catalogue minus held; TENANT-audience roles exclude `scope='GLOBAL'` per LD2 audience-scope coherence). New `PermissionDetail` schema carries 4 display labels resolved server-side via 4 LEFT JOINs on `core.lookups` (mirrors `permission_matrix.py`'s pattern per LD4). New `RolesRepo.get_detail_by_id` + module-level `_select_permissions_with_labels` helper (used twice — once for held, once for available). Multi-user-type, `GATE_EXEMPT` (joins the existing 4 role read endpoints; PATCH at 6.18.3 will gate on `ADMIN.ROLES.OVERRIDE.GLOBAL`). Cross-audience 404 (TENANT JWT probing PLATFORM role's id -> 404 `ROLE_NOT_FOUND`) per LD5 / D-17. 8 new D-series tests (6 LOAD-BEARING: D1 envelope contract, D2 same-audience read, D3 cross-audience 404, D5 server-side label resolution, D6 PLATFORM available_permissions CAN include GLOBAL, D7 TENANT available_permissions EXCLUDES GLOBAL). Smoke 55 -> 58. Pytest 627 -> 635. Detail: `docs/implementation-steps/step-6_18_2-roles-get-detail-2026-05-19.md`.
- **Step 6.18.3: PATCH /api/v1/roles/{role_id} role-edit endpoint.** DONE-LOCAL 2026-05-19. Closes the Step 6.18 sub-series. Role-edit write surface (E8) gated by `ADMIN.ROLES.OVERRIDE.GLOBAL` + `audience="PLATFORM"` (PLATFORM-only by gate-tuple construction; defense-in-depth via the audience kwarg makes the gate-discipline meta-test load-bearing). Editable scope: name, description, permission_ids (replace-set with diff-replace at the repo per LD5 — unchanged role_permissions rows preserve `created_at` and `created_by_*` audit trail). Forbidden fields (`audience`, `code`, `is_system`, `status`, audit columns) rejected by Pydantic `extra='forbid'` per LD19. **Two-layer OVERRIDE.GLOBAL invariant (LD6)**: Layer 1 pre-check returns 409 `LAST_OVERRIDE_HOLDER` if the edit would zero out active holders of `ADMIN.ROLES.OVERRIDE.GLOBAL` platform-wide (filters BOTH assignment-side AND user-side `status='ACTIVE'` per LD7); Layer 2 post-write tripwire raises `InternalInvariantViolationError` (500 INTERNAL_ERROR + ROLLBACK) if Layer 1's logic is buggy. **SUPER_ADMIN v0 lockout** per LD12 / LD20: PATCH on SUPER_ADMIN returns 409 `SUPER_ADMIN_PROTECTED`; v1 promotion deferred per FN-AB-57. **Audience-scope coherence** (LD10): TENANT-audience roles cannot add `scope='GLOBAL'` permissions (422 `AUDIENCE_SCOPE_MISMATCH`); only the diff's additions are inspected (lenient). **Permission existence pre-check** (LD11): 422 `INVALID_PERMISSION_ID` for unknown UUIDs in body. 6 new error classes (5 ClientError + 1 ServerError); module-level `_count_override_global_active_holders` + `_resolve_override_global_permission_id` invariant helpers; `RolesRepo.update` follows LD17 strict ordering. Third local copy of `_actor_type_from_auth` in `routers/v1/rbac.py` (FN-AB-58 tracks future promotion to shared module). 30 new W-series router tests + 6 RW-series repo tests (23 W-tests LOAD-BEARING). New `cleanup_role_perms_for_roles` test fixture per file resolves the FK-cascade issue when PATCH handler INSERTs role_permissions rows not tracked by `make_role_permission`. Smoke 58 -> 63. Pytest 635 -> 671. Four new FN-AB entries documented: FN-AB-57 SUPER_ADMIN editability deferral; FN-AB-58 `_actor_type_from_auth` duplication; FN-AB-59-CRITICAL race-condition mitigation; FN-AB-60-CRITICAL runtime permission catalogue API + enum decoupling (CRITICAL marker is new convention; flags entries that are low-probability but high-blast-radius and must not defer indefinitely). Detail: `docs/implementation-steps/step-6_18_3-roles-patch-2026-05-19.md`.
- **Step 6.20.1: TenantsRepo.create provisions tenant-root org_node.** DONE-LOCAL 2026-05-18. Bug-fix for the latent gap since Step 6.11.2: `POST /api/v1/tenants` did not insert the tenant-root `org_nodes` row, leaving newly-created tenants unreachable through every endpoint gated on `get_tenant_anchor`. `create()` now inserts the org_node in the same transaction as `tenants` + `tenant_module_access`. New pure-function `slug_for_tenant_root` helper derives `(code, path)` mechanically from `display_code` or `name`; empty slug rejected as 422 `INVALID_TENANT_NAME_FOR_SLUG`. Cleanup pre-fix: 10 cloud + 1 local orphan tenants deleted (single transactions). Detail: `docs/implementation-steps/step-6_20_1-tenants-create-org-node-root-2026-05-18.md`.
- **Step 6.20.2: /me/can-do ltree input validation fix.** DONE-LOCAL 2026-05-19. Bug-fix closing FN-AB-61: `GET /api/v1/me/can-do` returned 500 `INTERNAL_ERROR` when the caller-supplied `target_anchor` Query param contained any non-ltree character (e.g., a UUID with hyphens — the cloud-reported failure shape at v0.1.17 / admin-backend-00018-46f). Pre-fix the value reached `CAST(:target_anchor AS ltree)` in `_has_permission_tenant` and `psycopg.errors.SyntaxError` bubbled to the generic 500 envelope. Post-fix the `target_anchor` Query declaration carries `pattern=r"^[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)*$"` (multi-label ltree grammar per LD1) + `max_length=1024` (LD3), so FastAPI returns 422 BEFORE the gate runs. Mirrors the existing pattern validator at `schemas/org_node.py:271`. Also corrects a backwards docstring at `schemas/org_node.py:275` ("No underscores (ltree label restriction)" was inverted; the org_node code convention forbids underscores and uses hyphens, while ltree labels use underscores — `_path_label` is the bridge). +1 router test (`test_mc8_malformed_target_anchor_returns_422`, 6 assertion blocks: hyphen / leading-dot / trailing-dot / consecutive-dots / whitespace / empty-string; MC8a-MC8e LOAD-BEARING). Smoke 63 -> 64. Pytest 671 -> 672. FN-AB-61 marked RESOLVED. No DDL changes; no migration. Detail: `docs/implementation-steps/step-6_20_2-can-do-ltree-validation-2026-05-19.md`.
- **Step 6.16.7: audit row schema additions + emission retrofit + GET endpoint shape change.** DONE-LOCAL 2026-05-23. Single-commit step. Extends the audit subsystem with the audit list-view redesign wire shape. **DDL (both audit tables, migration `7a3c8e9d2f5b`):** 3 new columns — `actor_organization_name TEXT NOT NULL` (LD1+LD6: tenant name for TENANT actors, literal `'Platform-Ithina'` for PLATFORM), `actor_roles TEXT NOT NULL` (LD5: comma-separated active role display names from `roles.name`, e.g., "Owner, Promotions Assistant"; UI-renderable directly), `resource_subtype TEXT NULL` (LD7: ORG_NODE rows only, `org_nodes.node_type` frozen at write time). Path A backfill per LD2+LD3: pre-6.16.7 rows on tenant table backfilled via CASE on `actor_user_type` (PLATFORM -> `Platform-Ithina`; TENANT -> `tenant_name`); platform table backfilled with literal `Platform-Ithina`; `actor_roles = '-'` for all historical rows; `resource_subtype` stays NULL. NOT NULL applied post-backfill on the two non-NULL columns. Migration upgrade + downgrade + round-trip clean; EXPLAIN ANALYZE confirms sub-millisecond backfill on local seeded data per LD16. **Emission retrofit (LD13 centralisation):** all 16 v0 emission sites populate the 3 new columns via 2 new resolvers (`_resolve_actor_organization_name`, `_resolve_actor_roles`) called INSIDE both emission entry points (`emit_audit_event` success path; `emit_audit_event_in_new_transaction` failure path); zero changes at 5 of 6 emission-site repos. Only `repositories/org_nodes.py` changes at the repo layer to pass `resource_subtype=row.node_type.value` (2 sites). Dual-mechanism INSERT retrofit: ORM model column-mapped attributes added; raw `text()` INSERT statement extended from 14 to 17 explicit columns. ORG_NODE failure-path lookup extended to fetch `node_type` and back-fill `resource_subtype` when node_id is known. **Vocabulary changes (LD8):** `_ACTION_LABELS[UPDATE]` flipped "Updated" -> "Edited"; `_ACTION_LABELS[SET_STATUS]` flipped "Status change" -> "Set status". **CONFLICT result_label dispatch (LD9):** new `_CONFLICT_QUALIFIERS` dict + `compose_conflict_result_label()` helper covering 9 ClientError subclasses (DUPLICATE_TENANT_NAME, INVALID_STATE_TRANSITION, DUPLICATE_TENANT_USER_EMAIL, ROLE_ASSIGNMENT_CONFLICT, DUPLICATE_ORG_NODE_CODE, DUPLICATE_STORE_CODE, ROLE_ARCHIVED, LAST_OVERRIDE_HOLDER, SUPER_ADMIN_PROTECTED). Failure handler at `main.py` composes `"Blocked - <qualifier>"` when result_type is CONFLICT; falls through to static "Conflict" for unmapped codes. **GET endpoint response (LD10+LD11+LD12):** `AuditActivityListItem` grows 8 -> 14 fields (additive only); `AuditActivityDetail` grows 16 -> 19 fields. Backend composes `what` field at read time via new `_label_for_resource_type` helper mapping `(resource_type, resource_subtype)` to Type label across 13 combinations (5 non-ORG_NODE + 7 ORG_NODE subtypes + 1 historical fallback "Org node"). NULL `resource_label` renders as `"<Type label>: -"`. `_compose_what` lives in `routers/v1/audit.py`. Repo `_build_tenant_only_sql` / `_build_union_sql` / `get_by_id` SELECT projections extended to include the 3 new columns; `AuditActivityDetailRow` dataclass extended. **Tests:** 11 unit tests (4 new AE_N3-AE_N6 plus AE1-AE3 + AE5 + AE11 updated for new signature/labels), 3 repo tests for `what` composition (R_N1-R_N3; parameterised loop covers 13 type-label combinations), 2 router tests for list-response shape (L_N1 14-field set, L_N2 ORG_NODE subtype). 3 LOAD-BEARING emission tests (1 per file): AS_N1 (tenants — PLATFORM actor enrichment; `actor_organization_name="Platform-Ithina"`, `actor_roles="Super Admin"`, resource_subtype=None), OS_N1 (org-tree — resource_subtype="REGION" on both CREATE and UPDATE; UPDATE action_label="Edited"), AF_N1 (failures — CONFLICT result_label="Blocked - status change not allowed" + actor enrichment on failure path). New `tests/integration/test_audit_migration.py` file (8 tests AT_N1-AT_N8: revision at head, column nullability shape, INSERT round-trip, backfill CASE expression, platform literal, NOT NULL violations on both columns, resource_subtype NULLABLE). Conftest factories extended with 3 optional kwargs and 19-column INSERTs. Existing schema-test helpers + ORM model row builders updated for the new required columns. Stores set-status failure test action_label assertion updated "Status change" -> "Set status"; D1 detail test expanded to 19-key check; S2/S3 schema unit tests renamed and expanded to 14/19 fields. **pytest 872 -> 892 (+20).** mypy strict clean on 82 source files (unchanged). check_setup 36/36 (unchanged). alembic head moves `34f515cbc63a` -> `7a3c8e9d2f5b`. **Three design doc edits** at `docs/architecture_audit_logs.md`: Schema section gains 3 new column rows on both tables and updates the 16->19 framing; new "Response shape (post Step 6.16.7)" subsection in Read contract documenting the 6 new fields; new "Display vocabulary (Step 6.16.7)" subsection with action labels, CONFLICT qualifier map, and Type label mapping. `docs/schema/current_schema.sql` + `docs/schema/migration_log.md` regenerated. OpenAPI regenerated (+~190 lines). **FN-AB-67 RESOLVED** (actor enrichment shipped via role + organisation; full_name was a candidate the operator chose not to pursue per Phase 1 lock). **FN-AB-70 NEW** (INTEGRITY_VIOLATION reserved vocabulary; no production callers; revisit when an emission path needs to distinguish DB-layer integrity violations from app-layer CONFLICTs). **Frontend rendering is out of project scope** (separate frontend team consumes the deployed wire shape on their own schedule). Detail: `docs/implementation-steps/step-6_16_7-audit-row-schema-and-emission-retrofit-2026-05-23.md`.
- **Step 6.16.6: actor_user_id filter on GET /audit/activities.** DONE-LOCAL 2026-05-23. Single optional query parameter `actor_user_id: UUID | None = None` added to `GET /api/v1/audit/activities` adjacent to the existing `resource_type` parameter. AND-composed with existing filters per the 6.16.5 LD17 / 6.16.3 LD5 precedent; open-vocabulary posture (unknown UUIDs return 0 rows naturally, no 422). No actor_user_type companion: `platform_users.id` and `tenant_users.id` both use the `uuidv7()` DDL default (verified at pre-flight Check #11) and are globally unique, so `actor_user_id` alone is fully selective. **LD5 Adjusted-trivial** vs the prompt's single-shared-helper framing: no `_apply_common_filters` helper exists in the live repo; `src/admin_backend/repositories/audit_logs.py` has a TENANT-only builder (`_build_tenant_only_sql`) plus a UNION builder (`_build_union_sql`) with a Python f-string `common_where` block shared between two branch templates. The 6.16.5 `resource_type` clause is inlined at the same two sites; 6.16.6 follows the same pattern. SQL clause added: `AND (CAST(:actor_user_id AS uuid) IS NULL OR actor_user_id = CAST(:actor_user_id AS uuid))`. Behaviour identical to a single shared helper. `AuditLogsRepo.list` gains `actor_user_id: UUID | None = None` kwarg; param bound at the params dict. 3 new tests in `tests/integration/test_audit_router.py`: **AUF1** (LOAD-BEARING: filter happy path; 3 distinct actor_user_ids on the same tenant, filter selects one row), **AUF2** (AND-composition with status filter; only rows matching both pass), **AUF3** (LOAD-BEARING: unknown UUID returns 200 + empty `items` + `has_more=false`; no 422). Existing 25 audit_router tests exercise the `IS NULL` branch implicitly. pytest 869 -> 872 (+3 exact). mypy strict clean on 82 source files (unchanged). check_setup 36/36 (unchanged). alembic head unchanged (no migration). No DDL changes; no permission catalogue change; no Excel change; no smoke / test_endpoint script changes. One design doc edit at `docs/architecture_audit_logs.md`: new `actor_user_id` row in the Read contract > Filter parameters table; "Currently deferred" wording in Scale considerations option 6 + Open deferred items > Actor filter parameter rewritten to reflect shipped state; sub-step plan table closure note amended ("series complete" preserved; "Step 6.16.6 followed up post-closure with the actor filter required for frontend drawer integration"). BUILD_PLAN.md Step 6.16 root block amended (post-closure follow-up note); 6.16.6 sub-step entry added. OpenAPI regenerated with the new parameter. **FN-AB-69 RESOLVED** (born-resolved; the pre-existing acknowledgement of the gap lived in `docs/architecture_audit_logs.md:399` (Scale option 6) and `:432` (Open deferred items) without a FN-AB number — mirrors FN-AB-19 / FN-AB-21 precedent). Surfacing context: frontend Claude's report of 3 dead `/audit-logs` consumer surfaces (PlatformUserDetailDrawer.Activity, TenantUserDetailDrawer.Activity, RecentActivityPanel) now have the wire shape to migrate to `/audit/activities?actor_user_id=<id>`. Detail: `docs/implementation-steps/step-6_16_6-actor-filter-on-audit-activities-2026-05-23.md`.
- **Step 6.16.5: Audit emission for module-access (2) + org-tree (2) + stores (3) + GET resource_type filter.** DONE-LOCAL 2026-05-21. Closes the 6.16 series. Wires synchronous audit emission into the remaining 7 v0 write endpoints; AUDITED_ROUTES extended with 7 entries (all route to `tenant_activity_audit_logs` on the success path). Stores set-status dispatches on `target_status` to pick one of 4 per-target action codes (OPEN_SOFT / ACTIVATE / CLOSE / DEACTIVATE) per LD3; failure-path uses single fallback `SET_STATUS`. Module-access enable/disable no-op idempotent paths emit ZERO audit rows per LD2 (closes FN-AB-42; refines 6.16.4 LD14 to "at most one row per HTTP request"). Atomic-pair stores POST emits 1 row with `org_node_created_atomically: True` in snapshot per LD6 (always True in v0; FN-AB-68 reserves the False branch). FN-AB-66 closure: per-route extractor mapping shipped via sibling dict `RESOURCE_EXTRACTORS` (shape b) at `src/admin_backend/main.py`; six extractors (TENANT / TENANT_USER / ROLE / MODULE_ACCESS / ORG_NODE / STORE) replace the 6.16.4 minimal multi-key fallthrough; 6.16.4 `_failure_result_and_details(... auth=...)` caller_audience fallback preserved. LD17: `GET /api/v1/audit/activities` gains optional `resource_type` query param (open string vocabulary; AND-composed; applied to both UNION branches; unknown values return 0 rows naturally). 4 design doc edits at `docs/architecture_audit_logs.md` (per-route extractor paragraph, resource_type vocabulary table, resource_type filter mention in Read contract, sub-step plan closure + "series complete" note). OpenAPI regenerated with `resource_type` param. pytest 824 -> 869 (+45). mypy strict clean (82 source files); check_setup 36/36; alembic head unchanged; no DDL / no permission-catalogue change. FN-AB-65 and FN-AB-66 RESOLVED; FN-AB-42 RESOLVED; FN-AB-68 new (OPEN_SOFT label reserved for unreachable `target=OPENING` transition). Detail: `docs/implementation-steps/step-6_16_5-audit-emission-module-access-org-tree-stores-2026-05-21.md`.
- **Step 6.16.4: Audit emission for tenant-users + roles PATCH endpoints.** DONE-LOCAL 2026-05-21. Wires synchronous audit emission into 5 v0 write endpoints: 4 on `/tenant-users` (POST, PATCH, suspend, activate) and PATCH `/roles/{role_id}` (closes the 6.18.3 audit deferral). AUDITED_ROUTES extended with 5 entries; roles PATCH routes to platform_activity_audit_logs (LD1 / LD7: tenant_id NULL, route_to_platform=True). Failure-path handler at `main.py:233` extended to fall through path-param extraction across `tenant_id` / `user_id` / `role_id` and dispatch the resource_label lookup on `resource_type` (TENANT_USER -> `core.tenant_users` with a JOIN to `core.tenants` for tenant_id + tenant_name back-fill; ROLE -> `core.roles`). `caller_audience` falls back to `auth.user_type` when the raise site (gate / handler-side guard) didn't set it. Two new optional sub-keys on standard `details` shapes codify a generalisable pattern in `docs/architecture_audit_logs.md`: `denial_reason` on PERMISSION_DENIED (handler-side guard naming — LD11; `SelfEditForbiddenError` -> `"SELF_EDIT_FORBIDDEN"`); `invariant` on INTERNAL_ERROR (Layer 2 tripwire naming — LD12; `InternalInvariantViolationError` carries `invariant=...` via `**context`). `actor_display_name` continues to use `auth.email` per pre-flight Deviation 1 Option B (no DB-lookup helper extension; the JWT email IS the snapshot per Phase 1 Q2). `TenantUsersRepo.create / update / transition` and `RolesRepo.update` gain optional `auth: AuthContext | None` + `request_id: UUID | None` kwargs per LD15; ValueError on one-supplied-without-other; both omitted skips emission cleanly for repo-level unit tests. Eager name resolution at write time per LD9: tenant-users emits role+org_node names (`_resolve_role_labels` helper, 2 ANY-array SELECTs); roles emits permission codes (union-set SELECT, partition into before/after). `make_tenant` conftest fixture extended with audit-row DELETE scoped by `tenant_id` from BOTH audit tables before tenant DELETE — promotes 6.16.2's `cleanup_tenants_router` pattern to the shared fixture so future audit-emitting endpoint tests inherit it. `cleanup_tenant_users_router` (in `test_tenant_users_writes_router.py` per Deviation 3, NOT in conftest.py) extended to DELETE audit rows referencing tracked user_ids ahead of the assignments + users DELETE. 33 new tests (3 AE unit + 10 AS + 12 AF + 8 RS/RF; 16 LOAD-BEARING). pytest 791 -> 824. mypy strict clean on 82 source files. check_setup 36/36. No DDL changes; no migration; no smoke / test_endpoint script changes; no permission catalogue change. New FN-AB-65 (post-6.16.0 endpoint-count drift closed across 6.16.4 + 6.16.5), FN-AB-66 (AUDITED_ROUTES per-route extractor mapping deferred to 6.16.5 design), FN-AB-67 (audit row actor enrichment with full_name + role snapshot, deferred pending operator decision). Detail: `docs/implementation-steps/step-6_16_4-audit-emission-tenant-users-and-roles-2026-05-21.md`.
- **Step 6.16.3: Audit log read endpoints (list + detail).** DONE-LOCAL 2026-05-20. Two new GET endpoints on `/api/v1/audit/activities` (list + detail) backing the frontend audit timeline. Multi-audience gate `ADMIN.AUDIT_LOG.VIEW.TENANT` with audience-driven repo dispatch (LD1): PLATFORM callers see merged UNION ALL across both audit tables (synthesised `scope` column distinguishes branches); TENANT callers see only `tenant_activity_audit_logs` (RLS-scoped via D-29 OR-branch). Cursor pagination via opaque base64-encoded `{"ts": <iso8601>, "id": <uuid>}` (LD13); newest-first only; default limit 50, max 200. Departs from the project's offset-based `Pagination` convention; design doc Read contract > Pagination updated with the rationale (unbounded growth of audit log). Detail probes both tables (tenant first, platform fallback); 404 `AUDIT_EVENT_NOT_FOUND` on miss. Router-level check on the detail collapses "TENANT caller probing a PLATFORM-scope row" to the same 404 code per D-17 read principle. 2 new ClientError classes (`AuditEventNotFoundError` 404, `InvalidCursorError` 422). New `AuditLogsRepo` + 4 new schemas (`CursorPagination`, `AuditActivityListItem`, `AuditActivitiesListResponse`, `AuditActivityDetail`); `CursorPagination` lives in `schemas/audit_log.py` (LD4; `schemas/_common.py` does not exist; co-locating with `Pagination` in `schemas/tenant.py` rejected to avoid expanding that module's surface). Operator pre-prompt applied catalogue UPSERT: +1 permission `ADMIN.AUDIT_LOG.VIEW.GLOBAL`; revoked `.VIEW.TENANT` from SUPER_ADMIN / PLATFORM_ADMIN / SUPPORT_ADMIN; granted `.VIEW.GLOBAL` to same 3 platform roles. Tenant-side `.VIEW.TENANT` grants stay on 8 tenant roles per operator decision; 4 tenant roles (ASSOCIATE, NIGHT_SHIFT_LEAD, PERISHABLES_LEAD, REGIONAL_DIRECTOR) deliberately have no grant. Live DB: permissions 36 -> 37; role_permissions 132 -> 131. Pre-flight Check #4 verified live state matches. 2 new conftest factories (`make_tenant_activity_audit_log`, `make_platform_activity_audit_log`) use raw SQL INSERT under PLATFORM session. 37 new tests across 3 new files: 25 router (`test_audit_router.py` L1-L15 + D1-D7 + P1-P3; 8 LOAD-BEARING: L1 merged stream, L2 RLS scoping, L3 limit + pagination, L4 cursor round-trip, L13 malformed-cursor 422, D1 full-row detail, D4 cross-tenant 404, D5 cross-audience 404, P2 PLATFORM-without-grant 403, P3 TENANT-without-grant 403), 8 repo (`test_audit_logs_repo.py` R1-R8; 2 LOAD-BEARING: R2 cursor decode error, R3 TENANT dispatch queries tenant table only), 4 schema unit (`test_audit_log_schemas.py` S1-S4). The PLATFORM-without-audit-grant case uses a fixture-injected custom role (the seed grants `.VIEW.GLOBAL` to all 3 platform roles, so a real seeded PLATFORM user can never hit the 403 path). pytest 729 -> 766 (+37). mypy strict clean on 82 source files (was 79; +3 new modules). check_setup 36/36. smoke_curl 64 -> 67 (+3 audit probes); `test_endpoints.sh` + `test_endpoints_cloud.sh` extended with +4 entries per caller (16 new calls across the 4-caller matrix). OpenAPI regenerated: +2 paths, +4 schemas. `test_gate_discipline.py` pytest count unchanged (functions enumerate routes dynamically; +2 audit routes picked up automatically as gated). alembic head unchanged (no migration). No DDL changes. New FN-AB-64 captures uniform 4-column search rationale + over-granted `.VIEW.TENANT` tenant-role observation (deferred to v0 staging cleanup; SUPPORT_ADMIN consequently sees merged platform-wide via `user_type='PLATFORM'` dispatch). Detail: `docs/implementation-steps/step-6_16_3-audit-read-endpoints-2026-05-20.md`.
- **Step 6.16.2: Audit emission for tenants endpoints.** DONE-LOCAL 2026-05-20. New `src/admin_backend/audit/` package with two emission entry points: `emit_audit_event(session, ...)` for the success path (same transaction as the data write per the design doc Rule 1) and `emit_audit_event_in_new_transaction(engine, ...)` for the failure path (separate new transaction per Rule 2, after the data transaction has rolled back). Wired into `TenantsRepo.create` / `update` / `transition` for the 4 tenant write endpoints (POST, PATCH, suspend, activate). Failure-path emission hooks into the global exception handler at `main.py:233` (NOT `errors.py` per pre-flight Check #4) with the module-level `AUDITED_ROUTES` dict mapping (method, route template) -> (action, resource_type, route_to_platform). POST /tenants routes both success and failure rows to `platform_activity_audit_logs` per the design-doc-named exception (LD3); everything else routes by `tenant_id` presence. 4 deliberate non-audited paths: (1) 404-on-anchor (no resource to log), (2) Pydantic-direct 422 deferred per FN-AB-63 (wire-contract change spans all endpoints), (3) unauthenticated requests (auth=None; v0 deferral), (4) requests that didn't match a route. Failure-path emission sets `app.user_type='PLATFORM'` on the new connection so the D-29 OR-branch admits the INSERT; the actor's true identity is recorded INSIDE the audit row's `actor_user_type` column. Failure-path `tenant_name` + `resource_label` lookup falls back to `<unknown>` if the tenant was deleted concurrently (defensive). 4th local copy of `_actor_type_from_auth` in `audit/emit.py` per LD6; FN-AB-58 stays open. Repo signature evolution: `auth: AuthContext | None = None` and `request_id: UUID | None = None` added as optional kwargs (when both provided, emission fires; when both omitted, repo-level unit tests skip emission cleanly; mixing one without the other raises ValueError for developer-bug protection). Bundled design doc refinement at `docs/architecture_audit_logs.md` Emission contract section: original "synchronous, same transaction" wording (mechanically impossible on the failure path) replaced with explicit two-rule structure (Rule 1 success = same txn; Rule 2 failure = separate new txn). 27 new tests across 3 new files: 6 unit (`test_audit_emit.py`, AE1-AE6; 3 LOAD-BEARING: AE1, AE2, AE3 routing correctness), 10 success-path integration (`test_audit_emission_tenants.py`, AS1-AS10; 4 LOAD-BEARING: AS1, AS3, AS5, AS6), 11 failure-path integration (`test_audit_emission_failures.py`, AF1-AF11 with AF4 the codebase-422 case; 5 LOAD-BEARING: AF1, AF3, AF4, AF6, AF10). Test renumber-and-drop per operator: original AF4 (Pydantic-direct 422 from invalid POST body) deferred per FN-AB-63; renumbered AF5-AF12 to AF4-AF11 so IDs run sequentially. `tests/integration/test_tenants_writes_router.py`'s `cleanup_tenants_router` fixture extended to DELETE audit rows referencing the test tenant before the tenant DELETE (FK ON DELETE RESTRICT). pytest 702 -> 729 (+27). mypy strict clean on 79 source files (was 77; +2 for `audit/__init__.py` + `audit/emit.py`). check_setup 36/36. No new endpoints; no DDL changes; no migration; no smoke / test_endpoint script changes; no permission catalogue change. New FN-AB-63 added: Pydantic RequestValidationError bypasses project error envelope; audit emission for direct-Pydantic 422 deferred pending separate scope-decision step. Detail: `docs/implementation-steps/step-6_16_2-audit-emission-tenants-2026-05-20.md`.
- **Step 6.16.1: Audit log schema (DDL + ORM + RLS + indexes).** DONE-LOCAL 2026-05-20. Migration `c530346032dd` creates `core.audit_result_type_enum` (6 values: SUCCESS, PERMISSION_DENIED, VALIDATION_FAILED, CONFLICT, INTEGRITY_VIOLATION, INTERNAL_ERROR), two physical tables `core.tenant_activity_audit_logs` (16 columns, NOT NULL `tenant_id`/`tenant_name`, RLS+FORCE with the D-29 unconditional OR-branch policy, 3 indexes) and `core.platform_activity_audit_logs` (same 16-column shape with `tenant_id`/`tenant_name` NULLABLE, no RLS, 2 indexes). Both tables FK to `core.tenants(id)` ON UPDATE/DELETE RESTRICT. Symmetric column shape mirrors the Step 6.8.1 split-per-audience pattern (D-29); the audit tables ship empty at this step, emission lands at 6.16.2 onward. ORM models in `src/admin_backend/models/audit_log.py` reuse `ActorUserType` per the existing enum-binding convention. Schema-capture pattern mirrors `5e22b2ca13cc` per CSD-03. `scripts/seed_dev_data/truncate.py` `SEED_TABLES` extended with the 2 audit tables so `--reset` resolves the FK graph in one statement. 13 new integration tests across 2 files: 8 in `test_audit_log_schema.py` (S1, S4-S9 with S7 parametrized; 5 LOAD-BEARING — S1 schema present, S6 tenant resource_pair CHECK, S7 platform both CHECKs, S8 FK RESTRICT, S9 RLS+OR-branch isolation) + 5 in `test_audit_log_models.py` (M1-M5; M4 LOAD-BEARING — all 6 `AuditResultType` values round-trip). Pytest 689 -> 702. mypy strict clean on 77 source files (was 76). alembic head moves `5e22b2ca13cc` -> `c530346032dd`. `docs/schema/current_schema.sql` and `docs/schema/migration_log.md` regenerated. Detail: `docs/implementation-steps/step-6_16_1-audit-log-schema-2026-05-20.md`.
- **Step 6.16.0: Audit log subsystem design document.** DONE-LOCAL 2026-05-20. Landed `docs/architecture_audit_logs.md` capturing the full subsystem design (routing principle, read-access principle, symmetric two-table schema, sync emission, read contract, scale considerations, deferred items) plus BUILD_PLAN.md sub-step expansion (6.16.0 through 6.16.5). No code or schema changes. Detail: `docs/architecture_audit_logs.md`.
- **Step 6.21.2: Store ↔ org_node atomic-pair write surface.** Status: DONE-LOCAL at `<this-commit>`. POST /api/v1/stores becomes the atomic-pair entry point (creates both stores row and paired STORE-type org_node in one transaction); PATCH /stores cascades shared-field changes (name, store_code, parent_org_node_id); POST /stores/{id}/set-status cascades status; POST /org-tree rejects node_type='STORE'; PATCH /org-tree on STORE-type rejects shared fields (name, code). DDL migration `34f515cbc63a` tightens core.stores.org_node_id to NOT NULL. Establishes architecture.md § A.4 "two-table-one-entity coupling" as the codified pattern (D-36) with § A.5 documenting this seam. Closes Gap B from the 2026-05-20 write-surface coupling investigation. Detail: `docs/implementation-steps/step-6_21_2-stores-org-node-atomic-paired-write-2026-05-21.md`.
- **Step 6.21.1: expose tenant_root_id / tenant_root_code / tenant_root_path on GET /api/v1/tenants/{tenant_id}/org-tree.** Status: DONE-LOCAL at `<this-commit>`. Three additive fields to OrgTreeResponse surface the tenant-root org_node's id/code/path; unblocks frontend's Add Org Node from the synthesized TENANT row (Gap A from the 2026-05-20 write-surface coupling investigation). Detail: `docs/implementation-steps/step-6_21_1-org-tree-expose-tenant-root-id-2026-05-20.md`.
- **Step 6.20.3: RBAC structural enforcement triggers.** DONE-LOCAL 2026-05-20. Three Postgres triggers added via Alembic migration `5e22b2ca13cc` close structural enforcement gaps surfaced by the 2026-05-19 investigation: (1) `tg_role_permissions_audience_scope_coherence` rejects (TENANT-audience role x GLOBAL-scope permission) on INSERT or UPDATE OF role_id/permission_id and backstops Step 6.18.3 LD17's app-layer pre-check (Layer 1 still returns 422 `AUDIENCE_SCOPE_MISMATCH` for API callers; trigger is the DDL backstop for direct-SQL, seed-loader, or future-endpoint bypass paths — mirrors the LD6/LD8 two-layer OVERRIDE.GLOBAL invariant pattern); (2) `tg_role_permissions_protect_super_admin_override` pins the (SUPER_ADMIN x ADMIN.ROLES.OVERRIDE.GLOBAL) grant from DELETE (platform-bootstrap protection); (3) `tg_roles_protect_super_admin` pins SUPER_ADMIN's status/code/audience and blocks DELETE while leaving name/description editable (branding flexibility per LD3). Migration mirrors the `a0982a86985b` shape: schema captured via `bind.execute(sa.text("SELECT current_schema()")).scalar_one()` and `{schema}.` f-string interpolation throughout the function bodies (CSD-03 posture; per D-15 the schema is per-env). RAW DDL block appended unqualified to `db/raw_ddl/Ithina_postgres_SQL_DDL_rbac_v3.sql` adjacent to the relevant tables (Trigger 3 after `tg_roles_set_updated_at`; Triggers 1+2 after the `role_permissions` index). Trigger error shape: plain `RAISE EXCEPTION` with default SQLSTATE P0001 (mirrors the surviving `enforce_*_role_audience` precedent at `rbac_v3.sql:421-439` / `:581-599`; the prompt's LD7 `USING ERRCODE = '23514'` claim was incorrect and superseded). SQLAlchemy wraps these P0001 raises as `sqlalchemy.exc.ProgrammingError` (verified empirically), NOT `IntegrityError`; the prompt's "IntegrityError" wording was a convention error and the new tests use `ProgrammingError`. 17 new DB-direct tests at `tests/integration/test_rbac_audience_scope_triggers.py` (T1-T17; 12 LOAD-BEARING: T1, T2, T4, T5, T6, T8, T9, T11, T12, T13, T14, T16). Pre-existing TENANT × GLOBAL violations in seed Excel / local DB / Cloud SQL: zero (operator verified at pre-flight Check #3); no data migration required. Migration round-trip clean (`upgrade head` → `downgrade -1` → `upgrade head`); 5 trigger rows visible in `information_schema.triggers` post-upgrade (one per `event_manipulation` since UPDATE OR DELETE and INSERT OR UPDATE produce multiple rows). Pytest 672 → 689 (+17). mypy strict clean on 76 source files. check_setup 36/36. New FN-AB-62 captures the deferred AI-RBAC-01 comment amendment (left optional per operator). No app-layer code changes; Step 6.18.3 LD17 PATCH-side check at W22 unchanged. Detail: `docs/implementation-steps/step-6_20_3-role-audience-scope-trigger-2026-05-19.md`.
- **Step 6.15: tenant-module-access write endpoints (enable / disable on existing tenants)** (single commit per the WORKFLOW.md default; 2026-05-16). Two PLATFORM-only POST endpoints toggle `core.tenant_module_access.status` between `ENABLED` and `DISABLED`, with one upsert seam on the enable path. URL shape `POST /api/v1/module-access/{tenant_id}/{module_code}/enable` and `.../disable` — writes follow the reads' prefix (Step 6.7 reads at `/module-access/`), distinct from tenant suspend/activate which nests under the parent. **Same gate tuple as tenant suspend/activate** (`ADMIN.TENANTS.OVERRIDE.GLOBAL`, SUPER_ADMIN only) with `audience="PLATFORM"` and `anchor_dep=get_tenant_anchor`. **Idempotent-200 on no-op cells** (LD4): enable on already-`ENABLED` is 200 with no row mutation; disable on already-`DISABLED` is 200 with no row mutation; disable on missing is 404 `MODULE_ACCESS_NOT_FOUND`. Deliberate divergence from tenant suspend/activate's 409 `INVALID_STATE_TRANSITION` on the same no-op cells — captured as FN-AB-42 (cross-resource asymmetry to revisit at Step 6.16 audit-log emission). **LD5 overwrite semantics**: `enabled_at` + `enabled_by_user_id` overwritten on every `DISABLED -> ENABLED` flip (treated as "current ENABLED stint began at" markers); preserved on the disable flip as historical record. The DDL's `ck_tenant_module_access_disabled_pair` + `ck_tenant_module_access_status_consistency` constraints atomically pair status with the `disabled_*` columns. **LD8 race control**: `SELECT FOR UPDATE` on `(tenant_id, module)` inside the request transaction; on the missing-row branch, `IntegrityError` from a concurrent enable-on-missing triggers one retry that takes the UPDATE branch on the committed row (the unique index `uq_tenant_module_access_tenant_module` is the arbiter). **`ModulesAccessRepo` extended** with `enable`, `disable`, and 5 private helpers (`_select_for_update`, `_insert_enabled`, `_apply_enable_transition`, `_apply_disable_transition`, `_refetch`). Local `TransitionResult` enum (`OK` / `NOT_FOUND`) mirrors `TenantsRepo`'s pattern (separate enum per resource — the prompt locks this so cross-resource transition semantics stay decoupled). **Correction (2026-05-18, per Step 6.17.4 retro Obs 1):** "separate enum per resource" is not the project-wide convention. Only `modules_access` declares its own `TransitionResult` (2 values: `OK`, `NOT_FOUND` — no `INVALID_STATE` because the matrix is idempotent). `tenant_users` (Step 6.10.1) and `stores` (Step 6.17.4) both IMPORT `TransitionResult` from `repositories.tenants` (3 values: `OK`, `NOT_FOUND`, `INVALID_STATE`). The shared-import pattern is the right shape for any resource whose matrix has the full 3-value set; the local-enum pattern is reserved for resources with a narrower set (like modules_access's 2-value subset). Raw `text()` SQL with `f"{schema}.tenant_module_access"` schema-qualification per the convention. `session.expire_all()` after every UPDATE so a subsequent ORM read returns fresh data. **New `ModuleAccessRead` schema** (`from_attributes=True`, `extra="forbid"`); 8 fields with audit-actor IDs hidden per H1; re-exported via `schemas/__init__.py`. **New `ModuleAccessNotFoundError`** (404, `MODULE_ACCESS_NOT_FOUND`); structured `tenant_id` + `module_code` live in `exc.context` for log paths only per Q7. Only the disable path raises it — enable upserts, so it can never produce this code. **Path-param binds to `ModuleCode`** (LD7): FastAPI validates the enum at path-param time; invalid values surface as 422 before the handler runs. The `ModuleCode` Python enum carries 5 values (PRICING_OS, PERISHABLES_ASSISTANT, PROMOTIONS_ASSISTANT, GOAL_CONSOLE, ADMIN) — narrower than the live PG enum's 6 (ROOS retired Python-side 2026-05-12). **`tests/integration/test_gate_discipline.py::test_gate_discipline_platform_only_writes_declare_audience` extended** from 4 to 6 (method, path) tuples to cover both new routes. **21 new tests**: 14 router (`test_module_access_writes_router.py` C1-C6 transition matrix cells, P1-P4 permission boundary, V1 path validation, AUD-1 layer ordering, R1-R2 regression flows); 4 repo (`test_module_access_repo_writes.py` RT1 FOR UPDATE, RT2 LD8 retry, RT3 LD5 overwrite, RT4 no-op leaves `updated_at` unchanged); 2 schema unit (`test_module_access_schemas.py` S1 enum serialisation, S2 audit-actor reject); 1 error envelope unit (`test_module_access_errors.py` E1 404 + context). **Six LOAD-BEARING**: C1 (enable upsert INSERT branch), C4 (disable on missing -> 404), P1/P2 (TENANT JWT -> 403 PLATFORM_AUDIENCE_REQUIRED), P3 (PLATFORM_ADMIN -> 403 PERMISSION_DENIED — catches OVERRIDE-vs-CONFIGURE catalogue regression; mirrors 6.11.2's S6), P4 (unknown tenant_id -> 404 TENANT_NOT_FOUND from anchor dep BEFORE the gate body), AUD-1 (Layer 1 audience fires before Layer 2 has_permission). **Router test helper `_make_tenant_with_root`** pairs `make_tenant` with `make_org_node(node_type='TENANT')` because the gate's anchor_dep needs a tenant-root org_node; the bare `make_tenant` factory creates only the row in `tenants`, and `get_tenant_anchor` raises 404 without the paired root. P1/P2/AUD-1 use the seeded-tenant-and-root pair plus `_tenant_jwt(tenant.id)` so the anchor dep succeeds and Layer 1 audience refusal is the assertion target. **Smoke scripts +6 entries** each. `smoke_curl.sh` WHAT'S CHECKED 32 → 38: enable upsert / enable no-op / disable flip / disable no-op / disable on missing (404) / TENANT audience-deny. Tenant selection probes `/tenants/{id}/org-tree` for anchor reachability before picking a tenant with 2+ unused modules, skipping smoke-created tenants from prior runs (those lack org_node roots). TENANT audience-deny uses the TJWT's own `tenant_id` (extracted from the JWT payload) so the anchor dep resolves and Layer 1 fires. `test_endpoints.sh` + `test_endpoints_cloud.sh` mirror with a new Phase 4d block reusing the `write_req` shim. **EXPLAIN ANALYZE** (Step 6.15 verification, seeded data): SELECT FOR UPDATE on `(tenant_id, module)` is sub-millisecond (Index Scan on `uq_tenant_module_access_tenant_module`); UPDATE / INSERT each one-row roundtrip. Order-of-magnitude < 1 ms per repo call at v0 scale. **Documentation**: `docs/architecture_RBAC.md` gains one worked example slotted between tenant suspend/activate and POST `/tenant-users` (Appendix A); `docs/endpoints/module-access.md` extended with the 2 new operations in canonical 8-section format; `docs/endpoints/openapi.json` regenerated — both new paths visible with their request / response / error schemas. **2 new FN-AB**: FN-AB-42 cross-resource transition-matrix asymmetry (modules idempotent-200 vs tenants 409; revisit at Step 6.16 when audit-log emission surfaces the audit-trail differences concretely); FN-AB-43 module-access schema evolution under billing/payments (Stage 3 or post-v0 billing-integration design). **One BUILD_PLAN Step 6.7 wording correction** bundled: replaced "Disabling instantly revokes all related role permissions" in the Scope out section with a note that the cascade is structural via the `has_permission()` JOIN on `tma.status='ENABLED'` (no imperative revocation pass; re-enable restores access automatically per D-24). **Pytest**: 416 → 437 (+21 new tests). 0 xfail. **Per-resource regression checkpoint** clean: every pre-existing router test file at baseline count. **mypy strict** clean on 73 source files. **check_setup** 36/36. **No DDL changes; no migrations; no seed Excel changes.** Cloud deploy via standard `./scripts/deploy-cloud-run.sh` (no `--migrate` needed); deferred per Phase 5.5 batching.
- **Step 6.11: tenants write endpoints** at 6.11.1 (foundations) + 6.11.2 (endpoints + tests + smoke + docs). Section 6.11 ships the first write surface in v0 across two commits.

  **6.11.1 (commit `f280f8a`).** Internal foundations only — no public routing changes. 4 new ClientError subclasses (`PlatformAudienceRequiredError` 403, `DuplicateTenantNameError` 409, `InvalidStateTransitionError` 409, `EmptyPatchError` 422). `require()` factory gains `audience: Literal["PLATFORM", "TENANT"] | None` keyword-only kwarg; early `_check_audience` body call BEFORE `has_permission` raises `PlatformAudienceRequiredError` on mismatch. `PermissionGateInfo` marker extended with the `audience` field (default `None`) so the mandatory-gate-discipline meta-test can introspect Layer-1 declarations. `TenantCreateRequest` + `TenantPatchRequest` schemas with `extra="forbid"`: create force-merges ADMIN into `modules_enabled`, dedupes, enforces revenue-pair both-or-neither, lowercases `contact_email`; `number_of_stores_as_of_date` is REQUIRED on create (deviation from prompt sketch — `number_of_stores` is required+>=1, DDL `ck_tenants_number_of_stores_as_of_consistency` mandates the date). `TenantsRepo` gains `TransitionResult` enum + `_raise_if_name_taken`, `create`, `update`, `transition` methods. Raw SQL with schema qualification per the convention. `session.expire_all()` after raw UPDATE in `update` and `transition` so the post-write `get_by_id_with_aggregates` returns fresh data instead of stale identity-map cache. `pyproject.toml` flips `pydantic>=2.9` → `pydantic[email]>=2.9` (EmailStr backend). 4 error envelope unit + 13 schema unit + 16 repo integration tests; pytest 319 → 352; mypy strict clean (73 src files); check_setup 35/35.

  **6.11.2 (this commit).** Public surface: 4 new endpoints on `routers/v1/tenants.py`. **POST `/api/v1/tenants`** — provision tenant; `audience="PLATFORM"` + `ADMIN.TENANTS.CONFIGURE.GLOBAL` (SUPER_ADMIN + PLATFORM_ADMIN); server-forces `status=TRIAL`; bundled `tenant_module_access` INSERTs in same transaction; 409 `DUPLICATE_TENANT_NAME` on collision. **PATCH `/api/v1/tenants/{tenant_id}`** — same gate as POST; `extra="forbid"` rejects `status`/`region`/`id`; empty body → 422 `EMPTY_PATCH`; rename pre-check excludes self by id; allowed on SUSPENDED. **POST `/api/v1/tenants/{tenant_id}/suspend`** and **POST `/api/v1/tenants/{tenant_id}/activate`** — `audience="PLATFORM"` + `ADMIN.TENANTS.OVERRIDE.GLOBAL` (SUPER_ADMIN only); valid transitions per the matrix; SUSPENDED → ACTIVE atomically clears `suspended_at` + `suspended_by_user_id` (DDL `ck_tenants_suspended_consistency`). All 4 endpoints declare `audience="PLATFORM"` and inherit the new gate-discipline assertion. **`tests/integration/test_gate_discipline.py` extended** with `test_gate_discipline_platform_only_writes_declare_audience` — enumerates the 4 (method, path) tuples and asserts `__permission_gate__.audience == "PLATFORM"` on each. **`tests/integration/test_gate_retrofit.py::T_RET_6` updated** from path-keyed to (method, path)-keyed lookup so the same parameterised path can have multiple gated methods (GET + PATCH on `/tenants/{tenant_id}`). **31 new router tests** in `tests/integration/test_tenants_writes_router.py` (C1-C9, P1-P10, S1-S6, A1-A5, AUD-1, AUD-2). Plus 1 new gate-discipline assertion. **Four LOAD-BEARING regression tests**: C8 (TENANT JWT POST → 403 `PLATFORM_AUDIENCE_REQUIRED`), P5 (TENANT JWT PATCH same), S6 (PLATFORM_ADMIN on /suspend → 403 `PERMISSION_DENIED` — catches OVERRIDE vs CONFIGURE catalogue regression), AUD-2 (Layer 1 fires before Layer 2 — verifies the ordering invariant). **EXPLAIN ANALYZE** (Step 6.11.2 verification, seeded data): name pre-check 0.062 ms (Seq Scan; expected on 7-row table; FN-AB-36 tracks index dependency); transition FOR UPDATE 0.075 ms (Seq Scan rather than Index Scan on pk_tenants because PG correctly picks seq on tiny tables — plan shifts to Index Scan as table grows). **Smoke scripts +5 entries** each: `smoke_curl.sh` POST/PATCH/suspend/activate happy + TENANT audience-deny (WHAT'S CHECKED 22 → 27); `test_endpoints.sh` and `test_endpoints_cloud.sh` mirror with a Phase 4b outside-matrix write flow + inline `write_req` shim (the existing `req` helper accepts a JWT file path and no body). Names are UUID-suffixed for re-run safety; each run leaks one tenant in ACTIVE state. **Manual curl verification**: POST → 201 with full TenantDetail; PATCH → 200 with updated_at refreshed; suspend → 200 with `status=SUSPENDED` and `suspended_at` populated; activate → 200 with `suspended_at=null`; TENANT POST → 403 `PLATFORM_AUDIENCE_REQUIRED`. **Documentation**: `docs/architecture_RBAC.md` gains two insertions per Appendix A — Insertion 1 (audience-kwarg subsection inside `## Gate: require() factory`) with the **D3-corrected order-of-checks block** (FastAPI Depends resolution happens BEFORE the gate body; anchor_dep miss → 404 ahead of either Layer 1 or Layer 2; audience check fires inside the gate body before has_permission); Insertion 2 (3 worked examples — POST/create, PATCH, suspend+activate) inside `## Adding a new endpoint (cookbook)` after the existing POST `/tenants/{id}/stores` example. `docs/endpoints/tenants.md` extended with 4 new operations in the canonical 8-section format. `docs/endpoints/openapi.json` regenerated; all 4 new paths appear with their request/response schemas. **3 new FN-AB**: FN-AB-35 tenant name UNIQUE constraint (app-layer pattern for v0; resolution = additive Alembic migration with `uq_tenants_name UNIQUE (name)` ~30 min); FN-AB-36 tenant name uniqueness pre-check query plan (Seq Scan at v0 scale, Index Scan after FN-AB-35); FN-AB-37 multi-audience PATCH on tenants (deferred post-6.16 — Pattern (a) FKs on audit columns block TENANT OWNER UPDATE; bundles with the audit-log step's Pattern (b) migration). **No new FN-AB** for tenant suspended/terminated CHECK constraints — the prompt's claim that those constraints are absent is incorrect; `ck_tenants_suspended_consistency` and `ck_tenants_terminated_consistency` are present in the DDL since v3. **Pytest**: 352 → 385 (+33 = 31 router + 1 gate-discipline + 1 T_RET_6 update). 0 xfail. **Per-resource regression checkpoint** clean: all 12 pre-existing router test files unchanged at baseline; `test_gate_discipline.py` moved 1 → 2 (intentional carry-forward of operator note #2). **mypy strict** clean on 73 source files (no count change — 4 endpoints in an existing router module). **check_setup** 35/35. **No DDL changes; no migrations; no seed Excel changes.** Cloud deploy via standard 12-step workflow next.
- **Step 6.10.1: tenant-users write endpoints** (single commit per the new WORKFLOW.md default). Four new endpoints on `routers/v1/tenant_users.py` — **POST `/api/v1/tenant-users`** (create + bundled role assignments), **PATCH `/{user_id}`** (full_name / email / roles replace-set), **POST `/{user_id}/suspend`**, **POST `/{user_id}/activate`**. All four declare `audience=None` (multi-audience) gated on `ADMIN.USERS.CONFIGURE.TENANT` (held by SUPER_ADMIN + PLATFORM_ADMIN + OWNER per the seed catalogue). PLATFORM callers pass via GLOBAL→TENANT scope cascade; TENANT OWNER passes via direct grant. **Self-edit guard** (TENANT-audience callers cannot target their own `user_id`) fires handler-side AFTER `has_permission` resolves but BEFORE the repo call on the 3 path-bound endpoints (`_raise_if_self_edit`); PLATFORM callers can never self-edit by construction (they live in `platform_users`). POST has no path user_id so the case isn't expressible. **Server-forces `status='INVITED'`** on create — INVITED → ACTIVE is the Auth0 invite-accept callback flow (Stage 3); the explicit `/activate` endpoint refuses to take that path. **Bundled role assignments** anchored at the tenant root org_node (locked decision 4) — any TENANT-audience role acceptable in `roles[]`; **Option X handler-side audience pre-check** at `TenantUsersRepo._resolve_role_audience` converts the DB trigger `enforce_tenant_role_audience`'s plpgsql RAISE into clean 422 INVALID_ROLE / INVALID_ROLE_AUDIENCE. **Reused decisions**: `TenantUserNotFoundError` (already in `errors.py` since Step 6.9.3.2); `get_tenant_user_anchor` (already in `anchor_deps.py` returning the ltree path string per the gate's anchor-cascade contract — F1 surface-and-stop resolution per pre-flight); `TransitionResult` enum reused from `tenants` repo (F4 resolution); `InvalidStateTransitionError` reused from Step 6.11.1. **4 new ClientError subclasses**: `SelfEditForbiddenError` (403, `SELF_EDIT_FORBIDDEN`), `DuplicateTenantUserEmailError` (409), `InvalidRoleAudienceError` + `InvalidRoleError` (422). Per the Q7 lock (Step 6.9.2), structured detail (`invalid_role_ids`, `unknown_role_ids`) lives in `exc.context` for logs only; response envelope `details` field stays `null` (F3 resolution). **2 new request schemas** in `schemas/tenant_user.py`: `TenantUserCreateRequest` (`extra="forbid"`, `roles: list[UUID]` with `min_length=1`, email lowercased + deduped) and `TenantUserPatchRequest` (all optional, replace-set on roles). Module-level type alias `RoleIdList = list[UUID]` in `repositories/tenant_users.py` to escape the class-scope shadowing where `list` resolves to the bound `.list()` method (mypy quirk; `from __future__ import annotations` alone doesn't fix it). **3 new TenantUsersRepo methods**: `create`, `update`, `transition`. `update`'s role-replace-set: revokes existing ACTIVE assignments with the `revoked_*` pair (Pattern (b)), then INSERTs new ACTIVE rows; both appear in `roles[]` until next read filters by status. **Pattern (b) audit-actor pair population**: every INSERT / UPDATE writes BOTH `*_by_user_id` AND `*_by_user_type` columns (helper `_actor_type_from_auth` maps `AuthContext.user_type` Literal to `ActorUserType` enum). The `ck_*_actor_pair` CHECK constraints enforce both-NULL or both-NOT-NULL. **31 new router tests** in `tests/integration/test_tenant_users_writes_router.py` (C1-C9, P1-P12, S1-S5, A1-A5). **Five LOAD-BEARING**: C3 (TENANT OWNER cross-tenant `tenant_id` → 404 TENANT_NOT_FOUND, RLS-as-404 on tenant-root anchor lookup — prevents cross-tenant write disaster), C7 (PLATFORM-audience role in body → 422 INVALID_ROLE_AUDIENCE, ahead of trigger reject), P3 (TENANT self-edit → 403 SELF_EDIT_FORBIDDEN, primary self-edit guard case), P5 (TENANT-A OWNER patching TENANT-B user → 404, RLS-as-404 not 403), S4 (INVITED → SUSPENDED → 409 INVALID_STATE_TRANSITION, maps DDL `ck_tenant_users_auth0_sub_consistency` reject to clean 409). Local helper `_user_id_from_jwt` decodes the JWT payload without signature verification to extract user_id for self-edit tests; cleaner than extending `tenant_owner_jwt_factory`'s shared contract. **Cleanup-fixture**: `cleanup_tenant_users_router` tracks tenant_user IDs and DELETEs `tenant_user_role_assignments` FIRST (composite FK ON DELETE RESTRICT), then `tenant_users` rows. Fixture-order discipline: listed AFTER `make_*` factories so LIFO teardown clears FK refs in the right order. **Smoke scripts +5 entries** each: `smoke_curl.sh` WHAT'S CHECKED 27 → 32; `test_endpoints.sh` + `test_endpoints_cloud.sh` mirror via a new Phase 4c block reusing the `write_req` shim. **Deviation from prompt's smoke spec**: prompt asked for "POST .../suspend (PLATFORM) happy" + "POST .../activate (PLATFORM) happy" but a freshly-created tenant_user is INVITED, and INVITED → {SUSPENDED, ACTIVE} is structurally rejected (`ck_tenant_users_auth0_sub_consistency` + Auth0 invite-accept). Smoke can't promote a user to ACTIVE without DB access. Smoke verifies 409 INVALID_STATE_TRANSITION on both transitions against the INVITED user — still exercises the gate + anchor + repo + transition-matrix logic end to end; the 200 happy paths are covered by integration tests S1/S2/A1/A2. **`docs/architecture_RBAC.md`** gains: (1) `audience=None` subsection inside `### Two-layer gate` (`audience` parameter section); (2) 3 worked examples in `## Adding a new endpoint (cookbook)` — POST /tenant-users (multi-audience create), PATCH (multi-audience update with self-edit guard), suspend+activate (multi-audience transitions); (3) Pattern (b) audit-actor population convention note. **`docs/endpoints/tenant-users.md`** extended with 4 new operation sections in the canonical 8-section format. **`docs/endpoints/openapi.json` regenerated** — 4 paths visible for tenant-users (list shares with POST; detail shares with PATCH; suspend + activate distinct). **4 new FN-AB**: FN-AB-38 cancel-invitation deferred to Step 6.10.3 (column-based migration vs email-mangling — both rejected at 6.10.1 design); FN-AB-39 Auth0 invite-accept flow (INVITED → ACTIVE, Stage 3 territory; explicit `/activate` refuses); FN-AB-40 email-change Auth0 reconciliation (PATCH email under real Auth0 needs 2-step Auth0 + DB write; Stage 3); FN-AB-41 anchored role bundling at create (current: all roles at tenant root; anchored variants land at Step 6.14). **Pytest**: 385 → 416 (+31 new router tests). 0 xfail. **Per-resource regression checkpoint** clean: all 14 pre-existing files unchanged at baseline counts. **mypy strict** clean on 73 source files (no count change — additions land in existing modules). **check_setup** 35/35. **No DDL changes; no migrations; no seed Excel changes.** Cloud deploy deferred per Phase 5.5 operator pause; batched verification with Step 6.12 et seq.
- **`./scripts/check_setup.sh`** at 35/35. Tier 4 NOSUPERUSER NOBYPASSRLS check added at Step 1.5; mypy-strict and pytest-collection checks active since Step 2.1.
- **Workflow convention codified** (commit `75fda0e`). Per-step commit bundling: code + CLAUDE.md updates + BUILD_PLAN.md updates + prompt file in a single commit. No `Co-Authored-By` trailer on AI-touched commits.

### Not yet completed

- **Auth0Client.** Lands when Auth0 tenant configuration arrives (~3-4 days). Production cutover blocks on this per D-07.
- **Step 1.7.1, 1.7.2.** GCP provisioning script and runbook (deferred — listed in Stage 1 but not yet started).
- **Step 4.5.** Stores router + repo: superseded by Step 6.17.2 (Stores GET endpoints shipped 2026-05-18; full Store ORM landed and lightweight stub retired). Write endpoints (POST/PATCH/change_status) land in Steps 6.17.3 / 6.17.4.
- **Step 6.2.** Audit logs.
- **Steps 7.x, 8.x.** customer data load, Auth0 swap, prod cutover (GKE).

### Open questions / blockers

- **GCP-helper provisioning timeline.** Required before Step 1.7.x can produce a runnable script.
- **Auth0 ownership and timeline.** Stub keeps the build phase unblocked; production swap is config-only per D-07. Defer until ownership lands.

---

## Glossary

| Term | Meaning |
|---|---|
| Ithina | The platform / company |
| ROOS | Commercial name for the Retail Intelligence Platform |
| DIS | Data Ingestion Service (sibling project) |
| Master DB | Platform-wide Postgres database; admin backend writes its own tables |
| Tenant | A customer organisation |
| Tenant user | A human at a customer organisation |
| Platform user | An Ithina staff member |
| Org node | A node in a tenant's organisation hierarchy |
| AuthContext | Resolved auth state attached to every request |
| RLS | Postgres Row-Level Security |
| ltree | Postgres extension for hierarchical paths |

---

## How to ask the user good questions

When you need user input:

- One question at a time, numbered Q1/X.
- State your assumption if you have one. "I am assuming X. Is that right?"
- Offer 2-4 options when there is a real choice.
- Mark which one you lean toward and why.
- Don't ask questions you can answer yourself by reading this CLAUDE.md or BUILD_PLAN.md.

Bad: "How should I handle errors?"
Good: "Q1/1 — For `GET /v1/tenants/{id}`, RLS blocks the row → SQLAlchemy returns None. Per D-17, return 404 not 403. Confirm?"

---

## Document maintenance

- Update CLAUDE.md when a decision changes (D-NN), a new convention is set, or a forward-note (FN-AB) opens or resolves. `### Completed` entries (under `## Current state`) are 1-2 sentence pointers to the step doc, NOT step changelogs. The step doc is the canonical record for retro detail per A6; CLAUDE.md is the index of standing context.
- Update BUILD_PLAN.md when a step's status changes or sequencing shifts.
- PROMPTS.md is updated only if a prompt itself was wrong; usually we just write the next one.
- Doc updates happen at the end of a task, before stopping. Not during.
- When superseding a document substantively (e.g., architecture.md gets rewritten after a major decision change), move the old version to docs/archive/ with a version suffix (architecture_v1.md). Update docs/archive/README.md with the entry. Do this BEFORE replacing the current file.

---

## End of CLAUDE.md
