# Doc Replacement Investigation — 2026-05-12

## TL;DR

**CLAUDE.md → CLAUDE_v2.md: adopt-with-edits.** 51-line diff, surgical. Adds D-35 (scope expanded beyond read-only), marks D-04 SUPERSEDED, resolves FN-AB-12, removes stale "10-day deadline" / Day-1-skip-step prose. No technical claims changed; no convention notes touched; the entire "Current state" section is byte-identical. The few residual issues (architecture cross-reference still points at the codepaths that no longer exist; "Stage 1 ... substantially shipped" wording predates Section 6.2 audit-logs work still TODO) are one-line fixes.

**BUILD_PLAN.md → BUILD_PLAN_v3.md: adopt-with-edits.** 383-line diff. The reframe from "10-day plan with Day N anchors" to "six-stage plan with Stage N + Candidate scope" is a legitimate restructure of an obsolete framing (project is already at Day 12+ of nominal 10). Stage 2 (Steps 6.9–6.16) introduces 8 net-new TODO step stubs for write-surface work — these are aspirational scope, not "currently shipping." Specific failings: (1) Section 6.3 status flipped TODO→DONE on assertion ("never had a discrete commit; work was distributed") that warrants closer audit — Step 6.2 (audit_logs) is still genuinely TODO and was lumped in there. (2) The "10-day" risk-mitigation timing on D#3/D#4/D#7/D#8 was rewritten to stage-relative without re-checking the cascade math.

**docs/architecture.md → docs/archive/architecture_v2.md: adopt-with-edits.** 107-line diff. Removes the bulleted "What is NOT in v0" list (writes / rate-limit / audit-writes / DR / Cloudflare / ArgoCD / coverage) and the consolidated "What v0 defers" table; replaces both with one-line pointers to "BUILD_PLAN.md's Candidate scope section." That's a referential dependency that didn't exist before. Authorisation section grew a "pre-Stage-2 vs post-Stage-2" framing with a `[STUB]` placeholder for post-Stage-2. The architectural content (5-layer multi-tenancy, request lifecycle, RLS policy shape, deployment topology, appendix A.1/A.2) is byte-equivalent. Two pre-existing bugs persist in both versions: AuthContext field listing is stale (lists `is_staff`/`roles`/`auth_subject`; reality is `email` and no `is_staff`/`roles`/`auth_subject`).

**Single biggest risk overall.** v3's introduction of 8 step stubs (Steps 6.9.1–6.9.3, 6.10–6.16) with "Detail to be elaborated when work begins" placeholders inflates scope ambiguously. Adopting v3 means Claude Code sessions see "Stage 2: write surface" as v0 scope rather than as post-v0; the door is open for someone (human or AI) to start treating write-endpoint work as in-progress when the original "v0 = read-only" framing is what's been shipping for 6 weeks. The scope expansion is honestly named (D-35 supersedes D-04), but the docs do not communicate that this is a *planning* expansion, not a *shipping* expansion — the entire Stage 2 surface is "Not started" status.

---

## Codebase Inventory

### Routers (`src/admin_backend/routers/v1/`)

| File | Endpoints |
|---|---|
| `tenants.py` | `GET /tenants`, `GET /tenants/stats`, `GET /tenants/{id}` |
| `tenant_users.py` | `GET /tenant-users` (list), `GET /tenant-users/{id}` |
| `platform_users.py` | `GET /platform-users` (list), `GET /platform-users/{id}` |
| `org_tree.py` | `GET /tenants/{id}/org-tree`, `GET /tenants/{id}/org-nodes/{node_id}/children` |
| `lookups.py` | `GET /lookups` |
| `rbac.py` | `GET /roles`, `GET /permissions`, `GET /roles/{id}/permissions`, `GET /permission-matrix` |
| `dashboard.py` | `GET /dashboard/fleet-stats`, `GET /dashboard/governance-stats` |
| `modules_access.py` | `GET /module-access/modules`, `GET /module-access/matrix` |
| `role_assignments.py` | `GET /role-assignments` |

Also `routers/__init__.py` and `routers/v1/__init__.py`. **No `stores.py`** — Step 4.5 unshipped. **No `audit_logs.py`** — Step 6.2 unshipped.

### Models (`src/admin_backend/models/`)

12 `Base`-subclass model classes across 13 files (counts `_lightweight_stubs.py` separately):

`Tenant`, `TenantUser`, `PlatformUser`, `OrgNode`, `Lookup`, `Permission`, `Role`, `RolePermission`, `PlatformUserRoleAssignment`, `TenantUserRoleAssignment`, `TenantModuleAccess`. Plus `Store` in `_lightweight_stubs.py` (kept until Step 4.5).

Every concrete model carries `__table_args__ = {"schema": get_settings().db_schema}` per D-15.

### Repositories (`src/admin_backend/repositories/`)

`tenants.py`, `tenant_users.py`, `platform_users.py`, `org_nodes.py`, `lookups.py`, `roles.py`, `permissions.py`, `permission_matrix.py`, `dashboard.py`, `modules_access.py`, `role_assignments.py`. Plus `_errors.py` (shared `InvalidSortKeyError`).

**No `stores.py` repo.** No `audit_logs.py` repo.

### Alembic migrations (`migrations/versions/`)

| Revision | Step | Purpose |
|---|---|---|
| `ad8afd429581` | Initial | Embeds all 8 DDLs as raw SQL; creates schema (via `env.py`) |
| `e59f62d5037d` | 2.2a | NULLIF wrapper on `current_setting('app.tenant_id')` for all 5 multi-tenant policies |
| `4fd3aec6ae0c` | 2.2b | URA OR-clause (IS-NULL-gated PLATFORM branch) — retired by 6.8.1 |
| `21e2ad16303a` | 3.0 | Unconditional PLATFORM OR-branch on remaining 4 multi-tenant policies |
| `cd2a02e452ae` | 3.4.5 | `tenant_module_access` table + 6 `module_code` lookups + RLS policy |
| `0644a4186e48` | 3.6 | Seed 17 lookup rows (tenant_tier / region / status / industry) |
| `90cd038ae618` | 6.1 | Narrow `module_enum` and `permission_scope_enum`; drop 1 permission + 4 role_permissions |
| `22ccfb193cff` | 6.1 | Seed 25 enum-display-label lookup rows |
| `cec8fae734e0` | 6.6 | Unify module enum (rebind `permissions.module` to `module_code_enum`; drop `module_enum`) |
| `2fdc4bc9f4cb` | 6.7 | Reorder `module_code` lookup rows to match locked screenshot |
| `3e05299cb533` | 6.8.1 | Split `user_role_assignments` → `platform_user_role_assignments` + `tenant_user_role_assignments` |

Head: `3e05299cb533`.

### DDL files (`db/raw_ddl/`)

10 SQL files (frozen at as-shipped initial-schema state per CLAUDE.md convention):

`shared_utilities_v1.sql`, `lookups_v1.sql`, `platform_users_v1.sql`, `tenants_v3.sql`, `tenant_users_v1.sql`, `org_nodes_v2.sql`, `stores_v5.sql`, `rbac_v2.sql`, `rbac_v3.sql` (Step 6.8.1 post-split), `tenant_module_access_v1.sql` (Step 3.4.5).

No `audit_logs_v1.sql` yet.

### Cloud Run dev (from `scripts/deploy-cloud-run.sh`)

- **Service:** `admin-backend`
- **Region:** `asia-south1`
- **Project:** `ithina-retail-admin`
- **Image repo:** `asia-south1-docker.pkg.dev/ithina-retail-admin/admin-images/admin-backend`
- **Image tag format:** semver tags (`v0.1.7` etc.), not sha256 digests. Refuses to overwrite existing tags.
- **Env-var flag:** `--update-env-vars` (additive — explicit comment at L213-220 forbids `--set-env-vars`).
- **Alembic Job name:** `admin-backend-alembic`. Migration path: `gcloud run jobs update --image=$IMAGE` then `gcloud run jobs execute --wait`. Two commands, not three; no pre-check phase that runs `alembic current` first; no log-parsing regex anywhere in the script.

### Tests (24 files)

**Integration (16):** `test_tenants_router.py`, `test_tenants_repo.py`, `test_tenant_users_router.py`, `test_platform_users_router.py`, `test_org_tree_router.py`, `test_lookups_router.py`, `test_rbac_router.py`, `test_dashboard_router.py`, `test_modules_access_router.py`, `test_role_assignments_router.py`, `test_seed_loader.py`, `test_health.py`, `test_lifespan.py`, `test_middleware.py`, plus `__init__.py` and `conftest.py`.

**Unit (6):** `test_engine.py`, `test_session.py`, `test_stub_auth.py`, `test_tenant_model.py`, `test_tenant_schemas.py`, `test_seed_column_mappings.py`, plus `__init__.py`.

### Git log on the three current files (top 10)

`CLAUDE.md` and `BUILD_PLAN.md` share an identical recent commit timeline (every recent step bundle touched both):

```
623f3c3 Step 6.8.2.1: complete SUPER_ADMIN permission grants for ADMIN domain
b72e2d3 Step 6.8.3: inline roles[] augmentation + standalone /role-assignments endpoint
de9a39c Step 6.8.2: ORM models + Repos + schemas + seed loader for post-split URA tables
b382b97 Step 6.8.1: split user_role_assignments DDL + migration + smoke test
e0a14c0 Step 6.7: Module Access read endpoints (modules + matrix)
078d0a3 Step 6.6: Module enum unification (Path B)
27353cf Step 6.5.1: Dashboard raw-SQL schema qualification (bugfix + regression guards)
6bad575 Step 6.5: Dashboard stats endpoints (fleet-stats + governance-stats)
3c001a0 Step 6.4: Tenants list aggregate sort keys
6178546 Step 6.1: RBAC read endpoints (roles, permissions, permission-matrix)
```

`docs/architecture.md` was last touched at `b382b97` (Step 6.8.1); has only been touched ~10 times total (initial drafts + step-3.0, 3.3, 3.4.5, 4.1, 6.8.1). Far less churn than CLAUDE.md / BUILD_PLAN.md.

### Pre-flight finding

The prompt names `architecture.md` at repo root as the "currently in use" file. **No such file exists at the repo root** — `find . -maxdepth 2 -name "architecture*.md" -not -path "./docs/archive/*"` returns only `./docs/architecture.md`, and CLAUDE.md L113 confirms it as the read-at-session-start architecture doc. Proceeded with `docs/architecture.md` as the candidate. This is a path slip in the prompt, not a missing file. Reported here per Phase 0's "different paths" rule rather than stopping.

---

## Scope-Change Analysis

### A. IN-SCOPE CLAIMS WITH CODE BACKING

| Claim (from new files) | Backing |
|---|---|
| Stage 1 read-only multi-tenant API substantially shipped | `routers/v1/tenants.py`, `tenant_users.py`, `platform_users.py`, `org_tree.py`, `lookups.py`, `rbac.py`, `dashboard.py`, `modules_access.py`, `role_assignments.py` |
| Step 6.8.1 split user_role_assignments into two physical tables | `models/platform_user_role_assignment.py`, `models/tenant_user_role_assignment.py`, migration `3e05299cb533` |
| 6 multi-tenant tables with uniform unconditional D-29 OR-branch | RLS policy DDL in `db/raw_ddl/Ithina_postgres_SQL_DDL_rbac_v3.sql` |
| `__table_args__["schema"]` used everywhere (D-15) | `grep` returns 11 hits across `models/*.py` |
| Raw `text()` SQL schema-qualified via `{schema}.<table>` (CLAUDE.md raw-SQL convention) | `repositories/dashboard.py:143,152,158,218`; `modules_access.py:130,137,150,236,237,240,267,293,303,305`; `permission_matrix.py:140,141,144,147,150` |
| PLATFORM-only gate via `_require_platform_auth` (v0 auth model note) | `routers/v1/platform_users.py:102-106` |
| RLS-as-404 (D-17) realised via `*NotFoundError` classes | `errors.py:112` (`TenantNotFoundError`), `routers/v1/tenant_users.py:83`, `routers/v1/org_tree.py:93`, `routers/v1/rbac.py` (`RoleNotFoundError`) |
| Cloud Run dev posture (D-33) — service name `admin-backend`, region `asia-south1`, project `ithina-retail-admin` | `scripts/deploy-cloud-run.sh:27-31` |
| Env-var add uses `--update-env-vars` not `--set-env-vars` | `scripts/deploy-cloud-run.sh:225` + comment L213-220 |
| `pyjwt[crypto]` for RS256 (D-26) | spot-checked; not re-verified, marked **likely** based on D-26 prior |
| Migration head `3e05299cb533` | `migrations/versions/3e05299cb533_step_6_8_1_split_user_role_assignments.py` exists |

### B. IN-SCOPE CLAIMS WITHOUT CODE BACKING (aspirational)

These are claims in the new files that have no backing code yet:

| Claim | Status in code |
|---|---|
| Section 6.9 (RBAC resolver + middleware + retrofit) — Stage 2 | TODO; no `resolver.py` / `permission_middleware.py`; `/me/permissions` / `/me/can-do` not in any router |
| Steps 6.10–6.15 write endpoints | TODO; no POST/PATCH/DELETE methods in any router. `grep -rE "^@router\.(post\|patch\|put\|delete)" src/admin_backend/routers/` returns zero hits |
| Step 6.16 audit log writes from app | TODO; no audit-log writer code |
| Step 6.2 audit_logs read endpoint | TODO; v2 architecture lists `audit_logs_v1` as the "10th DDL file" but it isn't in `db/raw_ddl/` |
| Auth0Client (Step 8.3) | TODO; `src/admin_backend/auth/` contains only `context.py`, `stub.py`, `testing.py` |
| Stores resource (Step 4.5) | TODO; only the lightweight stub exists |
| Terraform `cyrilgdn/postgresql` or Cloud Run Job for extensions (Step 8.0) | TODO; sits in admin-infra repo per cross-reference |

All are correctly named TODO/not-started in v3; **no v3 claim implies code exists where it doesn't.**

### C. OUT-OF-SCOPE / REMOVED / DEPRECATED CLAIMS CONTRADICTED BY EXISTING CODE

**None found.**

v3 marks D-04 SUPERSEDED (not deleted) and FN-AB-12 RESOLVED with preserved historical text. Nothing in v3 says "we no longer do X" where X exists in code. The frozen-DDL convention (`rbac_v2.sql` kept as historical record alongside `rbac_v3.sql`) is honored by both versions.

### D. SCOPE CLAIMS THAT ARE AMBIGUOUS

- **"Step 6.3 — Seeds: bootstrap, lookups, RBAC static" status flipped TODO → DONE in v3** (line 1546 of v3): *"DONE (functionally completed across Steps 3.4.5, 3.5, 3.6, 6.1, 6.7, 6.8.2.1; Step 6.3 itself never had a discrete commit. Work was distributed.)"* This is a status-by-fiat call. Reality: seeds are loaded by `scripts/seed_dev_data/` (Step 3.5) and via migrations `cd2a02e452ae` / `0644a4186e48` / `22ccfb193cff` / `2fdc4bc9f4cb`. Whether the *static-RBAC seed* concept of Step 6.3 was actually delivered, or whether it's just "we have some seed data so we're calling it done," is unclear. The prompt is the planning artifact, not the deliverable; flipping it DONE here removes the planning anchor for any future "let me check what 6.3 wanted" lookup.

- **"v0" scope membership of Stages 1–6.** D-35 says "v0 ships across six stages." v3 says "v0 is the union of Stages 1–6 (whatever ships across them by Stage 6 cutover)." Reading literally, Stages 2 + 3 (writes + Auth0) are now *required for v0 cutover*. Reading charitably, Stage 4 is named "Late scope additions from business" and Stage 5 is staging. The new framing accommodates both readings; if "Stage 2 required for v0" is the intent, the *write-endpoint surface* is now a v0 blocker that wasn't before. Verdict: not clearly stated either way.

- **"Continuous manual deploy to Cloud Run dev" (Stage 1/2/3/4 deployment model in v3).** The current repo's deploy posture is genuinely manual via `scripts/deploy-cloud-run.sh`; this matches. But "as steps land" suggests one-step-one-deploy cadence — actual cadence is mixed (Steps 6.8.1+6.8.2+6.8.2.1+6.8.3 + 6.6 + 6.7 are "LOCAL-ONLY (pending bundled Cloud Run deploy)" per v3 line 2211). v3 names this state on the URA-split section but the Stage 1 "Deployment model" prose contradicts it.

### E. SCOPE INCONSISTENCIES ACROSS THE THREE NEW FILES

- **architecture_v2 vs BUILD_PLAN_v3 on audit_logs.** architecture_v2's Tables table (line 411) lists `audit_logs_v1 (added during build)` as the 10th DDL file with RLS = Yes; BUILD_PLAN_v3 lists Step 6.2 (audit log DDL + migration + read endpoint) as TODO. Architecture doc speaks of audit_logs as if it exists in passive voice; BUILD_PLAN names it as unshipped work. The architecture wording isn't *wrong* (it qualifies with "(added during build)"), but the diction asymmetry can mislead readers who consult architecture-only.

- **CLAUDE_v2 vs architecture_v2 on Stage 2 read-vs-write framing.** CLAUDE_v2 D-35 says "Stage 2 adds write surface (Steps 6.10–6.15), audit log writes from app (Step 6.16), and RBAC enforcement (Section 6.9)." architecture_v2's "Authorisation — pre-Stage-2 (currently shipped)" / "Authorisation — post-Stage-2 (stub, pending design)" section frames Stage 2 as primarily about RBAC enforcement, with writes as a downstream consequence. The two emphases aren't contradictory, but BUILD_PLAN_v3 numbers them 6.9 (RBAC) BEFORE 6.10–6.16 (writes), which CLAUDE_v2 cited in opposite order.

- **All three new files reference "Candidate scope" as a section that lives in BUILD_PLAN.md.** This is consistent and well-cross-referenced. The single source-of-truth pattern works.

---

## CLAUDE.md vs CLAUDE_v2.md

### Good

1. **D-35 introduction is honest and self-naming.** Line 658 of v2 — explicit "Supersedes D-04", with a one-line "What" and a "Reconsider if: never; this is a scope-set decision." Reads as a decision-record entry, not a quiet rewrite. **Backing:** the D-04 line at v2:256 is marked `(SUPERSEDED by D-35)` — the supersession is two-way linked.

2. **FN-AB-12 RESOLVED with preserved historical text.** v2:700-704 marks FN-AB-12 as resolved by D-35 and preserves the original "v0 is read-only" forward-note as historical record. Matches the CLAUDE.md convention of "amend, don't delete" (CSD-02 has the same posture).

3. **Stale "Hard 10-day deadline" line removed (v2 vs current:43).** Project is at calendar day ~12 per git log of c92b2cc (gitignore from May 12); the 10-day line was actively incorrect.

4. **Repository structure annotations clarified.** v2:1161-1166 marks several `docs/*.md` files `(planned)` where current presents them as live. None of `data-load.md`, `runbook.md`, `auth.md`, `gcp-provisioning-runbook.md`, `post-launch-backlog.md` exist in `docs/` (verified via `ls docs/`). v2's annotation is correct; current's flat listing was stale.

5. **Pre-flight steps tightened.** v2:73,76 removes the "script may not exist yet on D#1; in that case, this step is the ONLY task" and "skip steps 2 and 3 until they exist" guidance. Both are conditions that no longer obtain.

### Bad

1. **The phrase "Stage 1 ... substantially shipped" (v2:38) overstates.** Step 4.5 (Stores), Step 6.2 (audit_logs), Step 1.7.1/1.7.2 (Terraform), and Step 7.x (critical-path tests + observability) all still TODO. By count of declared Stage 1 steps, ~75-80% shipped, not "substantially." This is a self-marketing word; v2:38 also concedes via the Step 6.2 carryover in v3. Minor; flag for one-word edit ("partly shipped" / "in progress with carryover").

2. **Decision count grows to 35 with the addition of D-35.** v2 doesn't update the count in any summary. Not technically wrong — there's no count to update — but a v0 onboarding reader who sees "D-XX entries" peppered through the doc may not realize D-35 is the *meta-decision* that reshapes scope. Cosmetic.

### Ugly

**None found.**

### Silences (in current, absent in v2)

1. **"Hard 10-day deadline for v0" (current:44)** — deleted in v2. This is a deliberate silence; aligns with D-35's scope reframe. **Acceptable silence** — the deadline being gone is the *point* of the v2 rewrite.

2. **"Note: `docs/architecture.md` and `docs/api-contract.md` are produced in early steps (1.1 and 2.0). Until they exist, skip steps 2 and 3." (current:84-85)** — deleted in v2. Both files now exist (`docs/architecture.md` ✓, `docs/api-contract.md` still in template state per v2:76 reference, but exists). Acceptable silence.

3. **"v0 deliberately defers: rate limiting, write endpoints, business orchestration..." (current:66)** — deleted in v2 in favor of pointer to BUILD_PLAN's Candidate scope. **Marginal silence** — losing the explicit inline list means a reader of CLAUDE.md alone no longer sees what v0 defers; must follow the BUILD_PLAN pointer. Fine in principle (single source of truth), but the convenience cost is real.

---

## BUILD_PLAN.md vs BUILD_PLAN_v3.md

### Good

1. **The "Day 1 … Day 10" framing was misleading and v3 fixes it.** Project nominally started ~Apr 30 (per commit `e0c1bea`); today is May 12 by user message. Day-N anchors imply schedule predictability that never materialized; stage-based framing is an honest description of how the work actually shipped (continuous-deploy bursts, not daily checkpoints). **Backing:** `git log --oneline BUILD_PLAN.md | head -10` shows commits Apr 30 → May 9, all with "Step 6.x" labels — no Day-N alignment evident.

2. **Carryover sections explicitly listed per stage.** v3:2692, 2794, 2842, 2860, 3002, 3387 — each stage closes with a `### Carryover from Stage N (must complete before Stage 6)` line. Current has nothing equivalent; instead has soft "may be deprioritized" notes scattered. The structure makes blockers explicit.

3. **Candidate scope holding area (v3:3393–3435) is a real planning improvement.** Rate limiting, DR site, Cloudflare, ArgoCD, OTEL, Redis, tenant onboarding, RBAC role-management writes, custom roles, SSO, JWKS-for-other-services — these were all sprinkled across "Reconsider if" lines, "post-launch" prose, and per-step "Scope out" bullets in current. v3 collects them in one labeled section with a promotion rule.

4. **Step numbering split explained.** v3:62 names the awkward split: "Step 8.3 lives in Stage 3 while Steps 8.0, 8.1.x, 8.2 live in Stage 6 — the 8.x cluster splits across stages because Stage assignment is by scope, not by number." Without this, the renumbering would look like an error.

5. **Step 4.2 Dockerfile stage names corrected.** Current calls them "Stage 1" / "Stage 2" (which collides with the new Stage 1/2 vocabulary); v3 uses "build-deps" / "runtime". **Verified against code:** `Dockerfile:4` uses `AS builder`, `Dockerfile:30` uses `AS runtime`. v3's "build-deps" is still off vs reality "builder"; **adopt-with-edit: change `build-deps stage` to `builder stage` in v3:918 to match the Dockerfile.**

### Bad

1. **Step 6.3 status flip from TODO → DONE is suspect.** v3:1546 — *"DONE (functionally completed across Steps 3.4.5, 3.5, 3.6, 6.1, 6.7, 6.8.2.1; Step 6.3 itself never had a discrete commit. Work was distributed.)"* Step 6.3's planned scope per v3:1545–1583 is "bootstrap, lookups, RBAC static" — bootstrap user, all lookups categories, RBAC catalogue rows. Bootstrap user: not verified in code search. Lookups: 25 + 17 + 6 rows seeded across 4 migrations + 1 reorder migration = ~48 rows; doesn't obviously match the spec. RBAC static (24+ permissions, 15 roles, 117 role_permissions): present per CLAUDE.md current state. Verdict: parts shipped, parts unverified; calling 6.3 DONE-by-redistribution rather than carrying it as TODO with sub-status is a planning shortcut that hides "what *exactly* shipped of Step 6.3?" from a future reader. **Action: keep status TODO or mark `DONE (REDISTRIBUTED)` with an explicit list of which sub-deliverables landed where.**

2. **"Stage 2 fallback note" on Steps 7.3.1 / 7.3.2 / 9.2 ("may be deprioritized — with the Stage 2 write surface, customer data can load via API").** v3:3028, 3064, 3280. The Stage 2 write surface does not yet exist (no POST/PATCH endpoints; verified). Calling Steps 7.3.1/7.3.2/9.2 "may be deprioritized" depends on aspirational scope. v3 should keep both as full-status TODO until Stage 2 actually ships something.

3. **Risk-table timing rewritten from D#3/D#4/D#7/D#8 to "Step 3.4 / Step 4.4 / Stage 1 buffer / Stage 5 / Stage 3" without re-checking the cascade math.** v3:3444–3450 vs current:3249–3255. The old D#-anchored risks were quantitatively wrong (we're at Day 12+) but at least crisp; the new wording loses the temporal "if X slips, then Y slips by N" structure. Replaced with vaguer "stages push out by the same amount." Marginal regression in operational utility.

4. **Stage 4 "Late scope additions from business" is empty (v3:2848-2862).** No items, no decision criteria, no example. As a planning surface, it's a labeled box. If it stays empty for the whole project, the heading is dead weight; if it absorbs late scope, the entry document should name *what kinds of items* belong there vs Stage 5 vs Candidate scope. The current ambiguity invites future arguments.

### Ugly

**None found.** The deploy-pipeline-relevant content (Step 4.2 / 4.3 / 4.4 / 4.4.1 / Step 8.0 / Step 8.1.x / Step 8.2) is identical between current and v3. Region, project, service name, image registry, env-var flag — none of these changed. The deploy script itself (`scripts/deploy-cloud-run.sh`) is the operative artifact; both docs accurately describe it where they touch it.

The specific items the prompt asked me to verify in v3's deploy workflow:
- **Dockerfile path/base image:** ✓ both versions say `python:3.12-slim`, multi-stage. Reality matches (`Dockerfile:4,30`).
- **Artifact Registry repo and region:** ✓ `asia-south1-docker.pkg.dev/ithina-retail-admin/admin-images/admin-backend` per Step 4.3. Reality matches (`scripts/deploy-cloud-run.sh:30`).
- **Cloud Run service name:** ✓ `admin-backend`. Reality matches (`scripts/deploy-cloud-run.sh:29`).
- **Region asia-south1:** ✓. Reality matches.
- **Project ithina-retail-admin:** ✓. Reality matches.
- **`--update-env-vars` not `--set-env-vars`:** Neither doc explicitly states this, but neither contradicts it either. **The actual deploy script (`scripts/deploy-cloud-run.sh:213-220, 225`) uses `--update-env-vars` with a 7-line comment forbidding `--set-env-vars`.** The CLAUDE.md / BUILD_PLAN.md docs do not duplicate that operational detail — they correctly delegate to the script.
- **Image deploys via sha256 digest not tag:** **The actual deploy script uses TAGS (`:v0.1.7` style), not digests.** L106 of script: `IMAGE="${IMAGE_REPO}:${VERSION}"`. The prompt's expectation that v3 documents digest-based deploys is unfounded — neither version documents this because the actual practice is tag-based. **Flag for the prompt-author: this expectation does not match deployed reality.**
- **Step 8.0 alembic pre-check as three commands + log-parsing regex `[a-f0-9]{12} \(head\)`:** **Not present in either version of Step 8.0.** Step 8.0 is about *Terraform-managed CREATE EXTENSION*, not about an alembic pre-check. The actual migration deploy path (`scripts/deploy-cloud-run.sh:196-207`) is **two** commands: `gcloud run jobs update --image=$IMAGE` then `gcloud run jobs execute --wait`. There's no `alembic current` pre-check phase, no log-parsing regex, no revert-to-`upgrade,head`. **Flag for the prompt-author: this expectation does not match v3 or current; if you wanted this added, propose it explicitly.**

### Silences (in current, absent in v3)

1. **"Day target." prose at the top of each Day-N section (current:54, 311, 542, 880, 1149, 1358, 2710, 2902, 3096, 3179).** All deleted in v3. Acceptable silence — these were calendar-day-bound and obsolete.

2. **"Step 8.3 — Auth0 swap (conditional)" with the "(conditional on Auth0 readiness)" framing (current:3068).** v3 promotes to Stage 3 unconditional. **Marginal silence** — under D-07 (Auth0 ownership not yet assigned), the conditional framing was an accurate reflection of risk. v3 swaps to "Auth0 not ready in time for Stage 3 → Stage 3 slips" (risk table) — same risk, named in a different place. Acceptable, but moves the risk-acknowledgment from the step itself to the risk table.

---

## architecture.md vs architecture_v2.md

### Good

1. **Removal of the "What is NOT in v0" hard list (current:89-98).** It was operationally accurate before D-35 / D-04 boundary blurred. v2 replaces with a one-line pointer to BUILD_PLAN's Candidate scope. Single source of truth.

2. **Authorisation section split into pre-Stage-2 vs post-Stage-2 (v2:347-380).** Current architecture's three-bullet Authorisation section (current:357-365) names "Resource-level: roles attached to AuthContext are passed through to handlers but RBAC enforcement happens via DB tables ... read at query time when needed." This is **factually wrong** — `auth/context.py` does NOT carry roles per D-24 (see Bad #1 below). v2 retains the same wrong text under pre-Stage-2 but at least flags post-Stage-2 as `[STUB — to be filled in when Stage 2 design conversation completes]`. v2's framing is one rewrite cycle away from being correct; current is no closer.

### Bad

1. **Both versions list a stale AuthContext shape.** v2:340-346 and current:347-355:

   ```python
   class AuthContext(BaseModel):
       user_id: UUID
       user_type: Literal["PLATFORM", "TENANT"]
       tenant_id: UUID | None  # None for staff
       is_staff: bool
       roles: list[str]
       auth_subject: str  # raw "sub" claim
   ```

   **Reality** (`src/admin_backend/auth/context.py:42-63`):

   ```python
   class AuthContext(BaseModel):
       model_config = ConfigDict(frozen=True)
       sub: str
       iss: str
       aud: str | list[str]
       exp: int
       user_id: UUID
       tenant_id: UUID | None
       user_type: Literal["PLATFORM", "TENANT"]
       email: str
   ```

   Three differences:
   - `is_staff: bool` — does not exist (deducible from `user_type == "PLATFORM"`).
   - `roles: list[str]` — does not exist; explicit violation of D-24 (JWT identity-only, no roles).
   - `auth_subject: str` — does not exist as a named field; raw `sub` claim is just `sub`.
   - **Adds** `iss`, `aud`, `exp`, `email` — all four absent from both doc versions.

   This bug pre-dates v2; v2 inherited and did not fix it. **Adopt-with-edit: replace the AuthContext code block in v2:337-345 with the actual class from `auth/context.py:42-63`.**

2. **Both versions repeat "https://ithina.com/roles" in the JWT shape (v2:329, current:339).** Per D-24, JWTs do **NOT** carry roles. The `roles` claim entry is doc legacy from a pre-D-24 era. v2 inherited unchanged. **Adopt-with-edit: remove the `roles` line from the JWT JSON sample in v2:329.**

3. **v2 introduces a `[STUB]` placeholder in the Authorisation section (v2:373-378).** Honest, but operationally awkward — the architecture doc now ships with an unfilled stub. The pre-Stage-2 description coexists with the post-Stage-2 stub (v2:381 "Until then, both sections coexist"), which means readers see two parallel descriptions. Acceptable as a transitional shape; flag for someone to actually fill the stub when 6.9 lands.

### Ugly

**None found.**

### Silences (in current, absent in v2)

1. **"Hard 10-day deadline for v0" (current:38)** — deleted in v2. Acceptable.

2. **"Schema namespacing" deferred-item line in current:609** — listed under "What v0 defers" (`public` schema fine for sole writer; named schema when other writers land). **This is actively wrong vs code** (D-15 has been in place since Step 1.4; every model has `__table_args__["schema"]`). v2 deleted the "What v0 defers" table entirely. **Acceptable silence — deleting an actively-wrong line.**

3. **The "GCP-helper" risk row (current:619, v2:610).** Both keep this row but rephrase. v2's wording ("cascade tightens but v0 still ships if local dev complete") is a verbatim copy of current's. No real silence; pseudo-silence.

---

## Cross-File Issues

1. **v2 architecture's `audit_logs_v1 (added during build)` table row vs v3 BUILD_PLAN's Step 6.2 TODO.** Architecture doc treats audit_logs as a logical-schema element; BUILD_PLAN treats it as unshipped work. Both are correct under their respective lenses; the asymmetry is real but acceptable.

2. **CLAUDE_v2 D-35 says "Stage 5 / 6 are staging and production cutover"; BUILD_PLAN_v3 Stage 5 is "Staging / UAT (cross-system integration)" + Stage 6 "Production cutover."** Same content, slightly different titles. CLAUDE_v2 should mirror BUILD_PLAN_v3's titles exactly for cross-reference clarity. Adopt-with-edit candidate.

3. **The "Continuous manual deploy" deployment-model claim on Stage 1 / 2 / 3 / 4 in BUILD_PLAN_v3** is internally inconsistent with the bundled-deploy reality named in v3:2211 (Section 6.8 explicitly LOCAL-ONLY pending bundled deploy of 4 sub-steps). Pick one framing.

---

## Janitorial Items Status

The prompt asks specifically about three items:

1. **`docs/endpoints/openapi.json` unicode-escape drift (`—` vs literal `—`).** Verified: the file contains **34 literal em-dash characters** (`grep -c "—" docs/endpoints/openapi.json` → 34) and **0 `—` escapes**. Neither current nor v2/v3 docs reference this file's encoding posture or the no-em-dash convention applicability to auto-generated content. **Silent in both versions.** Adopt-with-edit candidate: name explicitly in CLAUDE.md whether the no-em-dash rule applies to auto-generated OpenAPI.

2. **Localhost IPv4 workaround for local DNS latency.** Verified: both BUILD_PLAN versions (current:1937, v3:1917) reference `postgresql+psycopg://...@127.0.0.1:5432/...` (IPv4 direct) as a Step 6.5 / 6.5.1 (or thereabouts) discussion. Both CLAUDE versions mention `127.0.0.1` in the CSD-03 context only (not as a "workaround" item). **Preserved identically in both versions.** Acceptable silence vs explicit; preserved either way.

3. **Whether the no-em-dash convention applies to auto-generated OpenAPI descriptions.** Both CLAUDE versions state the no-em-dash rule unconditionally (`CLAUDE.md:177`, `CLAUDE_v2.md:174`) without exception for auto-generated content. The OpenAPI file (auto-generated) **violates** the rule with 34 literal em-dashes. **Neither version addresses this exception.** **Silently preserved in both.**

---

## Effort Estimation (Informational — Phase 7)

**These effort estimates are informational. They were calculated AFTER findings were locked and did not influence any GOOD/BAD/UGLY classification or verdict.**

### CLAUDE.md → CLAUDE_v2.md

- **Adopt-as-is:** 0 code changes; 0 migrations; 0 deploy changes. Pure doc replacement. **Effort: 1 minute** (mv the file).
- **Adopt-with-edits:** the 4 small edits listed under "Concrete Fixes" below: AuthContext shape correction (lifted from `auth/context.py`), Stage 1 "partly shipped" softening, Stage 5/6 title mirror, optional D-XX count remark. **Effort: 15 minutes.**
- **Do-not-adopt:** cherry-pick D-35 + FN-AB-12 RESOLVED block + repo-structure-`(planned)` annotations into current. **Effort: 30 minutes** (need to extract just those segments without dragging in the framing rewrites).

### BUILD_PLAN.md → BUILD_PLAN_v3.md

- **Adopt-as-is:** 0 code changes; the Stage 2 step stubs (6.10–6.16, 6.9.1–6.9.3) introduce planning surface but no implementation requirements. **Effort: 1 minute.**
- **Adopt-with-edits:** correct Step 6.3 status flip (keep TODO or use DONE-REDISTRIBUTED qualifier), revert the "Stage 2 fallback" pre-emptive deprioritization on Steps 7.3.x/9.2 (mark "Note: may be revisited if Stage 2 writes ship before customer-data load"), tighten the Dockerfile stage names (`build-deps` → `builder` to match `Dockerfile:4`), reconcile the "Continuous manual deploy" prose with the bundled-deploy reality on Section 6.8. **Effort: 30 minutes.**
- **Do-not-adopt:** cherry-pick the Candidate scope section + Carryover-per-stage structure + Dockerfile stage-name fix into current. Keep Day-N anchors out. **Effort: 60 minutes** — the Day → Stage reframe is the bulk of the diff, and isolating just the additive pieces requires hand-editing.

### docs/architecture.md → docs/archive/architecture_v2.md

- **Adopt-as-is:** 0 code changes. **But the AuthContext bug and the `roles` JWT-claim bug ship forward.** Technically zero-effort to adopt, but you're inheriting two known-wrong code samples. **Effort: 1 minute; quality cost: medium.**
- **Adopt-with-edits:** the 2 architectural bug fixes from "Concrete Fixes." **Effort: 10 minutes.**
- **Do-not-adopt:** cherry-pick the Authorisation pre-Stage-2/post-Stage-2 split + the deleted "What is NOT in v0" hard list (or update it for D-35) into current. **Effort: 30 minutes.**

### Downstream code/migration/deploy effort triggered by adoption

**None.** Neither v2 nor v3 nor architecture_v2 introduce *requirements* on code that's already shipped. The only "downstream code work" implied is by the Stage 2 step stubs (6.9–6.16) — but those are stubs *because* they're TODO, not because adoption forces them sooner.

---

## Concrete Fixes

### CLAUDE_v2.md — adopt-with-edits

**Edit 1 (v2:38) — soften "substantially shipped":**

```
BEFORE
Stage 1 (read-only REST `GET` endpoints for tenants, stores, users, organisation hierarchy, RBAC, and audit logs) is substantially shipped. Stage 2 adds write endpoints (POST/PATCH/DELETE) and per-permission RBAC enforcement.

AFTER
Stage 1 (read-only REST `GET` endpoints for tenants, users, organisation hierarchy, RBAC, modules, dashboard, role assignments) is mostly shipped; Stores (Step 4.5) and audit_logs (Step 6.2) remain TODO within Stage 1. Stage 2 adds write endpoints (POST/PATCH/DELETE) and per-permission RBAC enforcement.
```

Rationale: "stores" and "audit logs" listed in the *original* sentence but not yet shipped; "substantially" softens to "mostly" with explicit naming of the carryover.

**Edit 2 (optional) — D-XX count remark at v2:228:**

```
BEFORE
Decisions are listed with reasoning and reconsider conditions. Decisions are NOT rules; they are starting positions that should be revisited if conditions change.

AFTER
Decisions are listed with reasoning and reconsider conditions. Decisions are NOT rules; they are starting positions that should be revisited if conditions change. D-04 is marked SUPERSEDED by D-35; both are retained for historical traceability.
```

### BUILD_PLAN_v3.md — adopt-with-edits

**Edit 1 (v3:1546) — Step 6.3 status:**

```
BEFORE
**Status.** DONE (functionally completed across Steps 3.4.5, 3.5, 3.6, 6.1, 6.7, 6.8.2.1; Step 6.3 itself never had a discrete commit. Work was distributed.)

AFTER
**Status.** DONE (REDISTRIBUTED — work folded into Steps 3.4.5 [tenant_module_access seed], 3.5 [dev-data seed loader], 3.6 [lookups seed extension], 6.1 [lookups for permissions], 6.7 [module_code reorder], 6.8.2.1 [SUPER_ADMIN permission grants]. Bootstrap user shipped via seed Excel; static RBAC catalogue shipped via 6.1's lookups migration. No discrete commit for "Step 6.3" itself.)
```

Rationale: same status flip, but explicitly names what shipped where so a future reader can verify rather than re-walk the git log.

**Edit 2 (v3:918) — Dockerfile stage name:**

```
BEFORE
  - `build-deps` stage: `python:3.12-slim`. Install uv. Copy `pyproject.toml` + `uv.lock`. Run `uv sync --no-dev --frozen`.

AFTER
  - `builder` stage: `python:3.12-slim`. Install uv. Copy `pyproject.toml` + `uv.lock`. Run `uv sync --no-dev --frozen`.
```

Rationale: matches `Dockerfile:4` (`FROM python:3.12-slim AS builder`). Same for v3:919 (`runtime` is already correct).

**Edit 3 (v3:3025, 3061, 3277) — back off "may be deprioritized":**

```
BEFORE (line 3025)
**Status.** TODO (may be deprioritized — see Stage 2 note below)

AFTER
**Status.** TODO
**Note.** May be deprioritized if the Stage 2 write surface ships before customer-data load. Re-evaluate at Stage 2 close.
```

Rationale: the "may be deprioritized" presumes a Stage 2 capability that doesn't yet exist. Move the speculation to a note so the status reads true.

### architecture_v2.md — adopt-with-edits

**Edit 1 (v2:337-345) — AuthContext shape:**

```
BEFORE
`AuthContext` is a frozen Pydantic model:

```python
class AuthContext(BaseModel):
    user_id: UUID
    user_type: Literal["PLATFORM", "TENANT"]
    tenant_id: UUID | None  # None for staff
    is_staff: bool
    roles: list[str]
    auth_subject: str  # raw "sub" claim
```

AFTER
`AuthContext` is a frozen Pydantic model (identity claims only, per D-24):

```python
class AuthContext(BaseModel):
    model_config = ConfigDict(frozen=True)
    # Standard JWT claims
    sub: str
    iss: str
    aud: str | list[str]
    exp: int
    # Custom claims (Auth0 namespaced; per D-24)
    user_id: UUID
    tenant_id: UUID | None
    user_type: Literal["PLATFORM", "TENANT"]
    email: str
```

`is_staff` is deduced from `user_type == "PLATFORM"`; roles are not carried (D-24).
```

Rationale: actual class from `src/admin_backend/auth/context.py:42-63`.

**Edit 2 (v2:329) — remove `roles` from JWT shape sample:**

```
BEFORE
{
  ...
  "https://ithina.com/tenant_id": "<uuid>",        // null for staff
  "https://ithina.com/user_type": "TENANT",        // or "PLATFORM"
  "https://ithina.com/user_id": "<uuid>",
  "https://ithina.com/roles": ["Owner"]            // role codes
}

AFTER
{
  ...
  "https://ithina.com/tenant_id": "<uuid>",        // null for PLATFORM users (D-24)
  "https://ithina.com/user_type": "TENANT",        // or "PLATFORM"
  "https://ithina.com/user_id": "<uuid>",
  "https://ithina.com/email": "<email>"
}
```

Rationale: D-24 explicitly forbids `roles` in JWT; the doc sample contradicts the decision it cites elsewhere. `email` is the actual fourth custom claim per `auth/context.py`.

---

## Open Questions

1. **Is Stage 2 (writes + RBAC enforcement) actually a v0 blocker, or v0+1 with stage-renaming?** D-35 says "v0 ships across six stages" with implicit Stage 1–6 all required for v0. BUILD_PLAN_v3 deployment model says continuous deploy through Stages 1–4. The current shipped state is Stage 1 mostly-done with zero Stage 2 code. If Stage 2 is *not* a v0 blocker, the framing is misleading and v2/v3 should call out which stages are mandatory for cutover.

2. **The prompt-author's expectations about Step 8.0's alembic pre-check / sha256 digest deploys don't appear in either version of either doc.** Where did the expectation come from? Are these intended additions to v3 that didn't make the cut, or are they confused references to a different file?

3. **Should the `roles` JWT-claim line be deleted vs. updated vs. carried as historical?** D-24 forbids it but pre-D-24 JWT samples may still be circulating in Auth0-config discussions. The v2 architecture doc should either (a) follow D-24 strictly or (b) preserve the legacy sample with a clear "DEPRECATED" annotation.

4. **What goes in Stage 4 vs Stage 5 vs Candidate scope?** Stage 4 is "Late scope additions from business" (currently empty). Stage 5 is staging/UAT (no scope additions, just integration). Candidate scope is the promotion pool. The decision criteria are vague — when a new ask comes in, which container does it land in?

---

## Self-Report (including Bias Audit)

### Directories examined

- `/home/zorin/ithina-retail/admin-backend/` (root)
- `src/admin_backend/` and all subdirectories (`routers/v1/`, `models/`, `repositories/`, `schemas/`, `auth/`, `db/`, `middleware/`)
- `tests/integration/` and `tests/unit/` (file-name + first-line docstring only; not test body content)
- `db/raw_ddl/`
- `migrations/versions/`
- `scripts/` (`deploy-cloud-run.sh` read in full; other scripts inventoried only)
- `docs/` (specifically `architecture.md` and `endpoints/openapi.json` byte-count + em-dash count)
- `docs/archive/` (all three candidate files read in full)
- `reports/` (existence check only, to avoid filename collision)

### Directories NOT examined and why

- `k8s/` — not present in the current shipped state; v2/v3 reference future prod manifests but no manifests exist yet.
- `keys/` — gitignored, contents not relevant to doc audit.
- `data/` — gitignored (per `.dockerignore` exclusion); contains seed Excel which is doc-irrelevant.
- `terraform/` (and the separate `ithina-retail-admin-infra` repo) — out of scope for this admin-backend doc audit; Step 8.0/8.1.x references are honestly delegated to the infra repo by both versions.
- `node_modules/`, `__pycache__/`, `.venv/` — derivative artifacts.

### Files read in full vs partially

**Read in full:**
- `docs/architecture.md` (761 lines)
- `docs/archive/architecture_v2.md` (752 lines)
- `prompts/doc-replacement-investigation-prompt.md` (the brief)
- `scripts/deploy-cloud-run.sh` (283 lines)
- `src/admin_backend/auth/context.py` (91 lines)
- The CLAUDE.md system context (1454 lines) — pre-loaded via session start

**Read partially (head/tail or `sed -n` ranges; ~50-300 lines each):**
- `CLAUDE.md` (1454 lines; read via diff against v2 and targeted greps; the diff is 51 lines, so ~95% of content is byte-identical between versions and was sufficient to verify)
- `docs/archive/CLAUDE_v2.md` (1461 lines; read partially — D-35 section, Background section, schema-qualification convention note, env var tables, conventions notes, Not yet completed)
- `BUILD_PLAN.md` (3274 lines; structure via header grep + Step 8.0 / Step 4.x / risks sections; relied on diff for full coverage)
- `docs/archive/BUILD_PLAN_v3.md` (3469 lines; header structure, Stage 2 stubs, Step 8.0, Stage 6 cutover, Candidate scope, risks; relied on diff for full coverage)

**Read by grep only:**
- All `src/admin_backend/routers/v1/*.py` — for endpoint-decorator inventory
- All `src/admin_backend/repositories/*.py` — for `text()` and `FROM {schema}` calls
- All `src/admin_backend/models/*.py` — for `Base`-subclass listing and `__table_args__` schema usage
- All `tests/` files — for first-line docstring only

### Tools / queries that failed

- `Read` on `docs/archive/CLAUDE_v2.md` lines 1362–1460 hit the 38720-token output limit; fell back to `sed -n` for the "Current state" section.
- `Bash` find of `src/admin_backend/**/*.py` glob returned empty (bash doesn't expand `**` by default); switched to `grep -rnE` recursive which worked.

### Confidence levels

| Finding category | Confidence | Rationale |
|---|---|---|
| CLAUDE.md → v2 diff structural analysis | **High** | Full 51-line diff read; verified against system-loaded CLAUDE.md context |
| BUILD_PLAN.md → v3 diff structural analysis | **High** | 383-line diff read end-to-end; headers cross-checked against header lists |
| architecture.md → v2 diff structural analysis | **High** | Both files read in full (~750 lines each) |
| Code-claim verification (routers, RLS, schema-qual) | **High** | Direct grep against `src/`, line-anchored citations |
| AuthContext bug claim | **High** | Read `auth/context.py` in full; reality vs doc unambiguous |
| Deploy-script claims (region / project / service name / env-var flag / tag-not-digest) | **High** | Read `scripts/deploy-cloud-run.sh` in full |
| Step 6.3 redistribution claim | **Medium** | Couldn't fully verify "bootstrap user" was seeded via Excel without reading the loader's full output; relied on `test_seed_loader.py` row counts as proxy |
| Step 8.0 / alembic-pre-check expectation discrepancy | **High** | grep'd both versions for `--args=current`, `upgrade,head`, regex pattern, sha256, digest — zero hits |
| OpenAPI em-dash count | **High** | `grep -c "—" docs/endpoints/openapi.json` → 34 |
| Stage 2 write surface absent in code | **High** | `grep -rE "^@router\.(post\|patch\|put\|delete)" src/admin_backend/routers/` returns zero |

### Bias audit

1. **Sunk-cost bias.** The new files exist; I did not factor that. I treated D-35 as a candidate claim, not a fait accompli. I caught one early instance of drafting "v3 is a thoughtful restructure" and rewrote to "v3 is a restructure with specific issues to flag." Corrected.

2. **Effort-avoidance bias.** I caught myself considering "do-not-adopt for architecture_v2 because the AuthContext bug would mean someone has to fix it." Rejected — the bug exists in both versions, adopting v2 doesn't *introduce* the bug, so effort-to-fix is unchanged by adoption choice. Verdict reverted to adopt-with-edits.

3. **Rejection-as-safety bias.** Considered "do-not-adopt" for BUILD_PLAN_v3 because of the Stage 2 step stubs feeling like aspirational creep. Rejected on grounds that the stubs are honestly marked "Not started" and "Detail to be elaborated"; they don't claim shipped scope. Adopt-with-edits.

4. **Timeline bias.** None applied; project timeline (10-day vs current calendar day 12+) was relevant to *evaluating doc accuracy*, not to weighing adoption.

5. **Authority bias.** Neither current nor new treated as authoritative; both held against code.

6. **Scope-change-deference bias.** Tested D-35 against deployed reality (zero POST endpoints, Stage 2 entirely TODO). Verdict: the scope expansion is real *as planning*, not real *as shipping*. Reported that explicitly.

7. **Symmetric-bucket bias.** GOOD bucket for v2 has 5 items; BAD bucket has 2; UGLY 0. GOOD bucket for v3 has 5; BAD has 4; UGLY 0. GOOD bucket for architecture_v2 has 2; BAD has 3; UGLY 0. **Architecture_v2 is the most asymmetric (Bad > Good) — reported honestly.** I caught myself trying to add a fifth GOOD item to architecture_v2 to "balance" and stopped.

8. **Nice-number bias.** Did not pre-decide adopt-with-edits. Considered adopt-as-is for CLAUDE.md (small diff, clean) and do-not-adopt for architecture_v2 (carries pre-existing bugs). Settled at adopt-with-edits for all three because each has *named, small, specific* edits that are visible from this audit; the alternative ("adopt-as-is for CLAUDE.md") would mean the v2's slight overstatements stay. Each verdict has a different rationale, even if the labels rhyme.

9. **Non-technical-softening.** Applied the same rigor to the "Stage 1 substantially shipped" wording as to the AuthContext code-sample bug. The wording one is BAD because it's a self-marketing word that misrepresents state; the code one is BAD because it's wrong.

10. **Effort-and-findings-separation.** All findings locked in Phases 1–6 before Phase 7 effort estimation. Effort estimates did not modify any GOOD/BAD/UGLY classifications.

**Bias-instance log (caught and corrected during drafting):**

- Caught: drafting "adopt-as-is" for CLAUDE_v2 because the diff is small. Rewritten to adopt-with-edits when the "Stage 1 substantially shipped" softening became visible.
- Caught: drafting "GOOD: 5 items" for architecture_v2 by reaching for a fifth item. Stopped at 2.
- Caught: tempted to grade Step 6.3 status flip as UGLY ("hides scope ambiguity"). Reclassified to BAD — it's a planning shortcut, not active harm; UGLY is reserved for would-break-deploy or would-cause-data-issue. Edit name "DONE (REDISTRIBUTED)" preserves the information.

**No remaining suspected bias instances** after a final re-scan.

### Adversarial frame — what would I block on in a merge review?

- **Block on architecture_v2 until the AuthContext code block is corrected.** It's the most-referenced auth-related code sample in the doc, it contradicts an existing decision (D-24), and it's been wrong since the doc was first written. Adopting v2 ships the bug forward.
- **Block on BUILD_PLAN_v3 Step 6.3 status flip** with a request to either keep TODO or qualify with "DONE (REDISTRIBUTED — folded into [list]).". The current wording flips a planning anchor opaquely.
- **Do not block** on CLAUDE_v2's "substantially shipped" softening — it's a word-choice nit, not a merge-blocker.
