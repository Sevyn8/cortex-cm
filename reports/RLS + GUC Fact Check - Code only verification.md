  RLS+GUC Writeup Fact-Check — Code-only verification
  
  HEAD: 1f2d8a1 Tooling and docs upkeep: build-step workflow, DB scripts, smoke variant, step reports

  ---
  Q0 — Live table inventory
  
  Status: — (informational baseline)
  Confidence: HIGH
  Coverage: all db/raw_ddl/*.sql and migrations/versions/*.py; ^CREATE TABLE, tenant_id\s+UUID, REFERENCES (...), ENABLE ROW LEVEL 
  SECURITY, FORCE ROW LEVEL SECURITY, CREATE POLICY.

  Bucket A — Direct multi-tenant (NOT NULL tenant key) — 6 tables

  Table: tenants
  Tenant key column: id (the tenant key itself)
  Source: db/raw_ddl/.../tenants_v3.sql
  ────────────────────────────────────────
  Table: tenant_users
  Tenant key column: tenant_id NOT NULL
  Source: tenant_users_v1.sql:67
  ────────────────────────────────────────
  Table: org_nodes
  Tenant key column: tenant_id NOT NULL
  Source: org_nodes_v2.sql:101
  ────────────────────────────────────────
  Table: stores
  Tenant key column: tenant_id NOT NULL
  Source: stores_v5.sql:86
  ────────────────────────────────────────
  Table: tenant_module_access
  Tenant key column: tenant_id NOT NULL
  Source: tenant_module_access_v1.sql:58 (Step 3.4.5; not in the document's "5")
  ────────────────────────────────────────
  Table: tenant_user_role_assignments
  Tenant key column: tenant_id NOT NULL
  Source: rbac_v3.sql:479 (Step 6.8.1 split; not in the document's "5")

  Bucket B — Mixed-audience junction with NULLABLE tenant_id — empty post-Step-6.8.1

  user_role_assignments (the historical mixed-audience table with tenant_id NULL) appears in db/raw_ddl/.../rbac_v2.sql:394 but is 
  dropped by migration 3e05299cb533 (Step 6.8.1) and replaced with platform_user_role_assignments (no tenant_id, Bucket D) +
  tenant_user_role_assignments (Bucket A). The rbac_v2.sql file is a frozen historical artefact per the "DDL is frozen at as-shipped"
  convention.

  Bucket C — Transitively scoped (no tenant_id, FK only) — empty

  Searched all FK statements (grep "REFERENCES 
  (tenants|tenant_users|org_nodes|stores|tenant_module_access|tenant_user_role_assignments)"). Every table that references a
  multi-tenant table has its own tenant_id column visible from the column grep. No transitively-scoped tables found.

  Bucket D — Tenant-global (no tenant key by design) — 6 tables

  ┌────────────────────────────────┬──────────────────────────────────────────────────────────┐
  │             Table              │                        Rationale                         │
  ├────────────────────────────────┼──────────────────────────────────────────────────────────┤
  │ lookups                        │ Reference data (display labels, codes); platform-global  │
  ├────────────────────────────────┼──────────────────────────────────────────────────────────┤
  │ platform_users                 │ Pattern 2 split (D-02): Ithina staff; no tenant scope    │
  ├────────────────────────────────┼──────────────────────────────────────────────────────────┤
  │ permissions                    │ RBAC catalogue (platform-global)                         │
  ├────────────────────────────────┼──────────────────────────────────────────────────────────┤
  │ roles                          │ RBAC catalogue (platform-global)                         │
  ├────────────────────────────────┼──────────────────────────────────────────────────────────┤
  │ role_permissions               │ RBAC junction (platform-global)                          │
  ├────────────────────────────────┼──────────────────────────────────────────────────────────┤
  │ platform_user_role_assignments │ Step 6.8.1 split (D-34): platform-side has no tenant key │
  └────────────────────────────────┴──────────────────────────────────────────────────────────┘

  Cross-check vs document's "5": The document names tenants, tenant_users, org_nodes, stores, user_role_assignments. Two material 
  drifts:
  1. user_role_assignments no longer exists (split per D-34 at Step 6.8.1).
  2. tenant_module_access (Step 3.4.5) and tenant_user_role_assignments (Step 6.8.1) are post-document additions to Bucket A.

  Live multi-tenant inventory is 6 tables, not 5.

  ---
  Q1 — Three GUCs are set per request

  Status: CONFIRMED
  Confidence: HIGH
  Coverage: grep -rnE "set_config|SET LOCAL" across src/, scripts/, tests/.

  Evidence: src/admin_backend/db/session.py:63-79:
  async with session_factory() as session:
      async with session.begin(): 
          ...
          await session.execute(text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id_value})
          await session.execute(text("SELECT set_config('app.user_type', :ut, true)"), {"ut": auth.user_type})
          await session.execute(text("SELECT set_config('app.request_id', :rid, true)"), {"rid": request_id})

  Exactly three set_config calls, all , true) (transaction-local). No other set_config or SET LOCAL in src/. (Smoke test
  scripts/smoke_test.py:132,143 uses , true); operator scripts scripts/jwt/generate*.sh, scripts/test_endpoints*.sh use , false) — but
  those are not the production path.)

  ---
  Q2 — AuthContext is a frozen Pydantic model with the claimed shape

  Status: PARTIAL
  Confidence: HIGH
  Coverage: src/admin_backend/auth/context.py (read in full).

  Evidence: src/admin_backend/auth/context.py:42-63:
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
      
  tenant_id: UUID | None, user_type: Literal["PLATFORM", "TENANT"], frozen via ConfigDict(frozen=True) — confirmed.

  PARTIAL because:
  - aud is str | list[str] (union), not just str. Docstring at lines 25-27 explains: "Auth0 may issue tokens with either shape."
  - The document mentions iat, nbf claims (per CLAUDE.md D-24); these are not on the model. Only sub, iss, aud, exp of the standard
  claims are carried.

  ---
  Q3 — Boot-time privilege check exists and is wired into startup

  Status: CONFIRMED
  Confidence: HIGH
  Coverage: src/admin_backend/db/engine.py, src/admin_backend/main.py.

  Evidence:
  - src/admin_backend/db/engine.py:97-134 defines assert_app_role_no_bypassrls(engine) which queries SELECT rolsuper, rolbypassrls FROM
   pg_roles WHERE rolname = current_user (lines 116-119) and raises AppRolePrivilegeError if either is true (lines 128-134).
  - src/admin_backend/main.py:72: await assert_app_role_no_bypassrls(engine) inside the lifespan async context manager (line 53).

  ---
  Q4 — engine.connect() audit

  Status: CONFIRMED (no handler-layer bypass)
  Confidence: HIGH
  Coverage: grep -rnE "engine\.connect|engine\.begin" across src/, scripts/, tests/, migrations/.

  Evidence — only 2 hits in src/:
  - src/admin_backend/db/engine.py:114 — inside assert_app_role_no_bypassrls (legitimate boot-time check).
  - src/admin_backend/main.py:174 — inside /api/v1/ready readiness probe (SELECT 1, no tenant data, public path).

  No automated lint/CI gate; the convention is enforced by code-review and the privilege-strip on the application role (a bypass
  attempt would still go through RLS).

  ---
  Q5 — RLS policy shape matches the quoted form
  
  Status: PARTIAL (the IS-NULL-gated variant on user_role_assignments no longer applies)
  Confidence: HIGH
  Coverage: all migrations creating policies (e59f62d5037d, 4fd3aec6ae0c, 21e2ad16303a, cd2a02e452ae, 3e05299cb533).

  Evidence — NOT-NULL form (migrations/versions/21e2ad16303a_*.py:88-106, tenant_users):
  CREATE POLICY tenant_users_tenant_isolation ON tenant_users
    FOR ALL
    USING (
      tenant_id = NULLIF(current_setting('app.tenant_id', TRUE), '')::uuid
      OR current_setting('app.user_type', TRUE) = 'PLATFORM'
    )
    WITH CHECK (
      tenant_id = NULLIF(current_setting('app.tenant_id', TRUE), '')::uuid
      OR current_setting('app.user_type', TRUE) = 'PLATFORM'
    )
    
  Evidence — tenant_user_role_assignments (3e05299cb533:344-355): identical unconditional OR-branch shape. NULLIF wrapper present. WITH
   CHECK present.

  PARTIAL because: the document's claim of an "IS-NULL-gated variant on user_role_assignments" was true historically (migration
  4fd3aec6ae0c) but user_role_assignments was dropped at Step 6.8.1. The IS-NULL-gated form no longer exists in the live schema; all 6
  multi-tenant tables now use the uniform unconditional OR-branch shape.

  ---
  Q6 — Every multi-tenant table has RLS, FORCE, and at least one policy
  
  Status: CONFIRMED (using Q0's actual 6-table list)
  Confidence: HIGH
  Coverage: grep -nE "ENABLE ROW LEVEL SECURITY|FORCE ROW LEVEL SECURITY|CREATE POLICY" across DDLs and migrations.

  Evidence — DDL files have all three for each Bucket-A table:

  ┌──────────────────────────────┬─────────────────────────────────┬───────┬──────────────────────────────────────────────────────┐
  │            Table             │             ENABLE              │ FORCE │                        POLICY                        │
  ├──────────────────────────────┼─────────────────────────────────┼───────┼──────────────────────────────────────────────────────┤
  │ tenants                      │ tenants_v3.sql:344              │ :345  │ tenants_self_access (:347)                           │
  ├──────────────────────────────┼─────────────────────────────────┼───────┼──────────────────────────────────────────────────────┤
  │ tenant_users                 │ tenant_users_v1.sql:221         │ :222  │ tenant_users_tenant_isolation (:224)                 │
  ├──────────────────────────────┼─────────────────────────────────┼───────┼──────────────────────────────────────────────────────┤
  │ org_nodes                    │ org_nodes_v2.sql:276            │ :277  │ org_nodes_tenant_isolation (:279)                    │
  ├──────────────────────────────┼─────────────────────────────────┼───────┼──────────────────────────────────────────────────────┤
  │ stores                       │ stores_v5.sql:265               │ :266  │ stores_tenant_isolation (:268)                       │
  ├──────────────────────────────┼─────────────────────────────────┼───────┼──────────────────────────────────────────────────────┤
  │ tenant_module_access         │ tenant_module_access_v1.sql:162 │ :163  │ tenant_module_access_tenant_isolation (:165)         │
  ├──────────────────────────────┼─────────────────────────────────┼───────┼──────────────────────────────────────────────────────┤
  │ tenant_user_role_assignments │ rbac_v3.sql:607                 │ :608  │ tenant_user_role_assignments_tenant_isolation (:610) │
  └──────────────────────────────┴─────────────────────────────────┴───────┴──────────────────────────────────────────────────────┘

  The two post-document additions (tenant_module_access, tenant_user_role_assignments) both have full RLS+FORCE+policy.

  ---
  Q7 — Smoke test count and shape

  Status: PARTIAL
  Confidence: HIGH (count via static grep -c, not execution)
  Coverage: scripts/smoke_test.py.

  Evidence:
  - Static count of R.add( calls: 80 assertions (not 74 as the document claims).
  - Truth-table loop at test_15_multi_tenant_or_clause_truth_tables (line 997) iterates over 6 tables × 9 cells = 54 cells (per the
  docstring at line 1052: "tables = (tenants, tenant_users, org_nodes, stores, tenant_module_access, tenant_user_role_assignments)").
  - INSERT-side assertions at test_16 (line 1144): targets 7 tables (the 6 multi-tenant + platform_user_role_assignments).
  - Meta-assertion at test_12 (line 916) — confirmed in Q17a.

  PARTIAL because: the document's "74 PASS" is stale (CLAUDE.md Step 6.8.1 says 81; current code grep shows 80 R.add calls). The shape
  (truth table, INSERT-side, meta) matches the document's structural claim.

  ---
  Q8 — test_t15 reused-connection test

  Status: PARTIAL
  Confidence: HIGH
  Coverage: tests/unit/test_session.py.

  Evidence: tests/unit/test_session.py:282-316 — test_t15_reused_connection_can_query_without_raising. Opens get_tenant_session for
  tenant, completes, then opens fresh engine.connect() and runs SELECT count(*) FROM tenants.

  PARTIAL because: the document claims the assertion is "current_setting('app.tenant_id', TRUE) returns NULL". Actual assertion at line
   316 is assert result.scalar() == 0 — checks that no rows are visible (default-deny via NULLIF treating '' as NULL). The test
  verifies the same property but at a different layer (RLS outcome, not raw GUC value). Intent matches; assertion phrasing differs.

  ---
  Q9 — test_t9_cross_tenant_detail_returns_404
  
  Status: CONFIRMED
  Confidence: HIGH
  Coverage: tests/integration/test_tenant_users_router.py.

  Evidence: tests/integration/test_tenant_users_router.py:421-454. TENANT-A JWT requests TENANT-B's user_id; assertion at line 451-453:
  assert resp.status_code == 404
  body = resp.json()
  assert body["code"] == "TENANT_USER_NOT_FOUND"
  
  ---
  Q10 — tenant_id source-binding from JWT only

  Status: PARTIAL
  Confidence: HIGH
  Coverage: src/admin_backend/middleware/auth.py, src/admin_backend/auth/stub.py, src/admin_backend/dependencies.py, all routers under
  src/admin_backend/routers/v1/.

  Evidence (positive):
  - src/admin_backend/auth/stub.py:131-141: only construction site of AuthContext; pulls all fields from JWT payload only.
  - src/admin_backend/middleware/auth.py:67-69: auth_client.verify(jwt_string) → request.state.auth = auth_context. No
  header/body/query input.
  - src/admin_backend/db/session.py:65-66: tenant_id_value = str(auth.tenant_id) ... — sourced only from the AuthContext.
  - tenant_id appears as a path parameter in routers/v1/tenants.py:195, routers/v1/org_tree.py:130, 222, and as a query parameter in
  routers/v1/tenant_users.py:186, routers/v1/role_assignments.py:266 — but none of these flow into set_config('app.tenant_id', ...).
  That GUC is set only from auth.tenant_id.
  
  PARTIAL because: the document's claim that "tenant mismatch (JWT vs path) returns 400 with code TENANT_CONTEXT_MISMATCH" is REFUTED.
  Search for TENANT_CONTEXT_MISMATCH across src/ returned zero hits. The CLAUDE.md D-18 entry describes this code path but it is not
  implemented. The actual implementation relies on RLS-as-404 (D-17) — a TENANT-A JWT requesting TENANT-B's id surfaces as 404, not
  400-with-quarantine. Ergonomically the same outcome (no data leak), but not the architecturally distinct quarantine path the document
   describes.

  ---
  Q11 — is_local=TRUE on every set_config call
  
  Status: CONFIRMED for production code path; PARTIAL across the repo
  Confidence: HIGH
  Coverage: grep -rnE "set_config\('app\." across src/, scripts/, tests/.

  Evidence — production: all 3 set_config calls in src/admin_backend/db/session.py:69,73,77 use , true).

  PARTIAL note: in scripts/jwt/generate*.sh and scripts/test_endpoints*.sh the pattern set_config('app.user_type', 'PLATFORM', false)
  appears 4 times — but those are operator/CLI scripts that explicitly want the GUC to persist for the script's session, not production
   code. The smoke test (scripts/smoke_test.py:132, 143) uses , true) correctly.

  ---
  Q12 — No BYPASSRLS role exists in the codebase

  Status: CONFIRMED
  Confidence: HIGH
  Coverage: grep -rni "bypassrls" . across the entire repo (excluding .git, .venv, __pycache__, .pytest_cache); 117 hits total.
  Inspected each.

  Evidence: No CREATE ROLE ... BYPASSRLS or ALTER ROLE ... BYPASSRLS anywhere. The 3 ALTER ROLE mentions are all ... NOSUPERUSER 
  NOBYPASSRLS (the strip operation, not granting). Hit categories:
  - Boot-time gate (src/admin_backend/db/engine.py:97-134): refuses to start if rolbypassrls=true.
  - check_setup.sh diagnostic (line 270).
  - Tests verifying the gate works (tests/unit/test_engine.py:7-138).
  - CLAUDE.md decision-record entries (D-29 explicitly explains "policy-clause not BYPASSRLS").
  - BUILD_PLAN.md historical references.
  - Prompt files (historical artefacts).

  Single application engine (only one create_async_engine in src/admin_backend/db/engine.py:43); no second connection pool with
  different role.

  ---
  Q13 — Read replica references

  Status: CONFIRMED (no replica provisioned in this repo)
  Confidence: HIGH
  Coverage: grep -rni "replica" . across all source files (excluding .git, .venv, __pycache__, .pytest_cache); 8 hits inspected.

  Evidence: Only design-document mentions:
  - docs/architecture.md:179, 490, 499, 524, 536 — design references (DR site, GKE pod replicas, sibling-app read replica). All
  forward-looking.
  - prompts/step-2_2a-engine-session.md:370 — historical "Read replicas, multi-region routing (post-launch)."
  - prompts/step-8_2-gke-prod-deploy-DRAFT.md:111 — replicas: 2 (Kubernetes pod replicas, not DB replicas).

  No code, migration, infra config, or env var creates a replica DB connection. The only create_async_engine call in src/ builds one
  engine.

  ---
  Q14 — Application role privilege state

  Status: UNVERIFIABLE FROM SOURCE (creation) / CONFIRMED (runtime gate enforces)
  Confidence: HIGH (negative finding for creation)
  Coverage: grep -rniE "CREATE ROLE|ALTER ROLE" across db/raw_ddl/, migrations/versions/, scripts/, Dockerfile, docker-compose.yml.

  Evidence:
  - No CREATE ROLE or ALTER ROLE statement that creates the user_admin_backend role with explicit NOSUPERUSER NOBYPASSRLS exists in the
   repo.
  - Local dev: docker-compose.yml:7 creates Postgres with POSTGRES_USER: user_admin_backend (Postgres' default behaviour creates this
  as SUPERUSER). The strip happens out-of-band; CLAUDE.md "Step 1.5 hardening" describes a manual ALTER ROLE operator step.
  - Cloud: role creation not visible in this repo.
  - The runtime gate at src/admin_backend/db/engine.py:97-134 enforces correct privilege state at startup — if the role has SUPERUSER
  or BYPASSRLS, the app refuses to start (AppRolePrivilegeError). scripts/check_setup.sh:255-273 does the same diagnostic.
  
  Verifying that the deployed role is actually NOSUPERUSER NOBYPASSRLS requires a live DB query against the running database — it's not
   derivable from source.

  ---
  Q15 — FORCE on every multi-tenant table

  Status: CONFIRMED
  Confidence: HIGH
  Coverage: Q6's table-by-table grep across DDLs and migrations.

  Evidence: Each of the 6 Bucket-A tables has both ENABLE ROW LEVEL SECURITY AND FORCE ROW LEVEL SECURITY adjacent in the DDL (see Q6
  table). No asymmetric ENABLE-without-FORCE case.

  ---
  Q16 — Connection pool configuration

  Status: CONFIRMED (with detailed values)
  Confidence: HIGH
  Coverage: src/admin_backend/db/engine.py, pyproject.toml, tests/.

  Evidence: src/admin_backend/db/engine.py:43-62:
  engine = create_async_engine(
      settings.database_url,
      pool_size=10,
      max_overflow=5,
      pool_timeout=30,
      pool_pre_ping=True,
      pool_recycle=1800,
      connect_args={"prepare_threshold": None},
      echo=False,
  )   
  
  - pool_size=10, max_overflow=5, pool_timeout=30, pool_pre_ping=True, pool_recycle=1800. All explicit; no defaults relied upon.
  - SQLAlchemy version: >=2.0.36 (pyproject.toml).
  - No eager pre-warming — connections are acquired lazily on first checkout (SA default; no startup pool.connect() loop). Confirmed by
   reading the lifespan in main.py lines 53-90.
  - Test override: tests/unit/test_session.py:228-229 uses pool_size=2, max_overflow=0 for concurrency tests (forces both sessions onto
   distinct/queued connections).
  - No dev-vs-cloud config split visible in this repo; pool config is hardcoded in engine.py.

  ---
  Q17 — RLS coverage of every tenant_id-bearing table
  
  (a) Meta-assertion query is correctly shaped

  Status: PARTIAL
  Confidence: HIGH
  Coverage: scripts/smoke_test.py:916-970.

  Evidence: test_12_meta_multi_tenant_tables_have_rls queries:
  SELECT t.tablename
    FROM pg_tables t
    JOIN information_schema.columns c
      ON c.table_schema = t.schemaname
     AND c.table_name = t.tablename
     AND c.column_name = 'tenant_id'
   WHERE t.schemaname = %s
     AND (
       NOT EXISTS (... pc.relrowsecurity = TRUE AND pc.relforcerowsecurity = TRUE ...)
       OR NOT EXISTS (... pg_policies pp WHERE ... pp.tablename = t.tablename)
     )
  - Uses db_schema parameter ✓ (not hardcoded public).
  - Joins relrowsecurity = TRUE AND relforcerowsecurity = TRUE (both checked) ✓.
  - Checks pg_policies for at-least-one ✓.
  - Expects zero offending rows ✓.
  
  PARTIAL because: the query filters by c.column_name = 'tenant_id'. The tenants table has no tenant_id column (its id IS the tenant
  key); it is therefore not scanned by the meta-assertion. The docstring at lines 921-924 acknowledges this exception. tenants RLS is
  verified separately by the truth-table loop in test_15.

  (b) Static audit of source files

  Status: CONFIRMED
  Confidence: HIGH
  Coverage: Q6's per-table evidence.

  Per Q0's Bucket-A list of 6 tables: each has ENABLE ROW LEVEL SECURITY, FORCE ROW LEVEL SECURITY, and at least one CREATE POLICY in
  the source-of-truth files (DDL or migration). No table missing any of the three.

  ---
  Q18 — Comprehensive connection-acquisition audit
  
  Status: CONFIRMED (no production-code Layer-2 bypass)
  Confidence: HIGH
  Coverage: grep -rnE "engine\.connect|engine\.begin|async_sessionmaker|sessionmaker|asyncpg\.connect|psycopg\.connect|psycopg2\.connec
  t|session_factory\(\)" across src/, scripts/, tests/, migrations/.

  Classification:

  Path: engine.connect()
  Location: src/admin_backend/db/engine.py:114             
  Class: Bootstrap (privilege check)
  ────────────────────────────────────────
  Path: engine.connect()
  Location: src/admin_backend/main.py:174
  Class: Bootstrap (readiness probe; SELECT 1; public path)
  ────────────────────────────────────────
  Path: session_factory()
  Location: src/admin_backend/db/session.py:63
  Class: Via dependency (canonical path)
  ────────────────────────────────────────
  Path: engine.connect()
  Location: tests/unit/test_engine.py:56,65, tests/unit/test_session.py:314, tests/integration/test_health.py:11,13,119
  Class: Test-only
  ────────────────────────────────────────
  Path: psycopg.connect()
  Location: scripts/verify_cloud_schema.py:67, scripts/smoke_test.py:414, 1435
  Class: Operator/test scripts

  Seed loaders (scripts/seed_dev_data/runner.py:134, 157) use get_tenant_session(...) — canonical path. loaders/*.py accept session as
  parameter; no direct connection acquisition.

  Lookups endpoint (src/admin_backend/routers/v1/lookups.py:71) uses Depends(get_tenant_session_dep) — canonical path.

  No bypass in production code. Every handler-layer query reaches the DB through get_tenant_session (or get_tenant_session_dep).

  ---
  Q19 — Policy uniformity across all multi-tenant tables
  
  Status: CONFIRMED (post-Step-6.8.1)
  Confidence: HIGH
  Coverage: all migrations creating policies; walked the chain to find latest CREATE POLICY per table.

  Live policy table:

  Table: tenants
  Policy name: tenants_self_access
  USING / WITH CHECK: id = NULLIF(...)::uuid OR app.user_type='PLATFORM'
  NULLIF?: ✓
  Both clauses?: ✓
  ────────────────────────────────────────
  Table: tenant_users
  Policy name: tenant_users_tenant_isolation
  USING / WITH CHECK: tenant_id = NULLIF(...)::uuid OR app.user_type='PLATFORM'
  NULLIF?: ✓
  Both clauses?: ✓
  ────────────────────────────────────────
  Table: org_nodes
  Policy name: org_nodes_tenant_isolation
  USING / WITH CHECK: same shape
  NULLIF?: ✓
  Both clauses?: ✓
  ────────────────────────────────────────
  Table: stores
  Policy name: stores_tenant_isolation
  USING / WITH CHECK: same shape
  NULLIF?: ✓
  Both clauses?: ✓
  ────────────────────────────────────────
  Table: tenant_module_access
  Policy name: tenant_module_access_tenant_isolation
  USING / WITH CHECK: same shape
  NULLIF?: ✓
  Both clauses?: ✓
  ────────────────────────────────────────
  Table: tenant_user_role_assignments
  Policy name: tenant_user_role_assignments_tenant_isolation
  USING / WITH CHECK: same shape
  NULLIF?: ✓
  Both clauses?: ✓

  All 6 use the unconditional OR-branch shape (no IS-NULL gate post-Step-6.8.1). Naming convention *_tenant_isolation with the
  documented tenants_self_access exception for the self-keyed tenants table. Zero drift.

  ---
  Q20 — Middleware registration order

  Status: REFUTED (document inverts inner/outer)
  Confidence: HIGH
  Coverage: src/admin_backend/main.py:128-136.

  Evidence: src/admin_backend/main.py:96-110 and lines 128-130:
  # Add in REVERSE so the outermost is added last:
  #   add Auth first  -> innermost
  #   add Audit next  -> middle
  #   add CORS last   -> outermost
  app.add_middleware(AuthMiddleware)         # innermost
  app.add_middleware(AuditContextMiddleware) # middle
  app.add_middleware(CORSMiddleware, ...)    # outermost

  Runtime order, outer→inner: CORS → AuditContext → Auth.

  The document's claim was: "AuditContext (outermost) → Auth (middle) → CORS (innermost)." This is REFUTED. The actual order at runtime
   has CORS outermost, not innermost; Auth innermost, not middle.

  The code's reasoning (line 98-101): "CORS outermost short-circuits OPTIONS preflights with 204 + Access-Control-* headers; adds
  Allow-Origin to every cross-origin response (incl. auth-rejected 401s, so browsers can read the failure body)." This is the
  architecturally correct ordering.

  ---
  Q21 — Public-path skip list
  
  Status: PARTIAL (paths use /api/v1 prefix, not /v1)
  Confidence: HIGH
  Coverage: src/admin_backend/middleware/auth.py:38-45.

  Evidence:
  PUBLIC_PATHS = frozenset({
      "/api/v1/health",
      "/api/v1/ready",
      "/api/v1/openapi.json",
      "/api/v1/docs",
      "/api/v1/redoc",
      "/metrics",
  })  

  - 6 paths total (document's claim listed 5 — missing /api/v1/ready).
  - All meta paths; no tenant-data path on the list.
  - Exact-match (request.url.path in PUBLIC_PATHS at line 52). Not prefix-match — no /api or /v1 over-match risk.
  - Document claims paths use /v1/... prefix; actual code uses /api/v1/... prefix. Cosmetic drift only — the gate is correctly
  path-exact-matched.

  ---
  Q22 — Deployment-role binding

  Status: UNVERIFIABLE FROM SOURCE (cloud); PARTIAL (local)
  Confidence: HIGH (negative finding for cloud)
  Coverage: .env.example, pyproject.toml, Dockerfile, docker-compose.yml, scripts/deploy-cloud-run.sh, scripts/check_setup.sh,
  src/admin_backend/config.py. No *.tf, no cloudbuild.yaml, no infra/ directory in this repo.

  Evidence:
  - Local dev: .env.example:19 —
  DATABASE_URL=postgresql+psycopg://user_admin_backend:password_admin_backend@localhost:5432/ithina_platform_db. docker-compose.yml:7
  creates Postgres with POSTGRES_USER: user_admin_backend (default Postgres behaviour: this user is SUPERUSER until explicitly stripped
   via the documented manual ALTER ROLE). Runtime gate refuses to start if not stripped.
  - Cloud: scripts/deploy-cloud-run.sh:213-214 explicitly says --update-env-vars="SERVICE_VERSION=${VERSION}" only — DATABASE_URL "and
  the other 14 env vars" are set out-of-band on Cloud Run. Their values are not in this repo.
  - No Terraform / cloudbuild.yaml / Cloud SQL IAM auth code in this repo.

  Cannot verify from source which role the production Cloud Run service uses. The boot-time gate (Q3) means production fails fast if
  the role is privileged, so a leaky configuration would manifest as a startup failure, not a silent leak.

  ---
  Q23 — Transitive-RLS audit (Bucket C from Q0)

  Status: CONFIRMED (Bucket C is empty)
  Confidence: HIGH
  Coverage: Q0's FK reference grep across all DDL files.

  Evidence: No table has an FK to a multi-tenant table without also having its own tenant_id column. Every FK reference to tenants,
  tenant_users, org_nodes, stores, tenant_module_access, or tenant_user_role_assignments originates from a table that itself has
  tenant_id (or the table is in Bucket D — junction tables like role_permissions whose tenant linkage is intentionally absent because
  they hold reference-data).

  No transitively-scoped tables found; all tenant-scoped data has its own tenant_id column and its own RLS policy.

  ---
  Summary table

  ┌─────┬───────────┬────────────┬─────────────────────────────────────────────────────────────────────────────────────────┐
  │  Q  │  Status   │ Confidence │                                          Notes                                          │
  ├─────┼───────────┼────────────┼─────────────────────────────────────────────────────────────────────────────────────────┤
  │ Q0  │ —         │ HIGH       │ 6 Bucket-A tables (not 5); Bucket B and C both empty post-Step-6.8.1; Bucket D 6 tables │
  ├─────┼───────────┼────────────┼─────────────────────────────────────────────────────────────────────────────────────────┤
  │ Q1  │ CONFIRMED │ HIGH       │ Three set_config calls, all , true)                                                     │
  ├─────┼───────────┼────────────┼─────────────────────────────────────────────────────────────────────────────────────────┤
  Confidence: HIGH
  Notes: Three set_config calls, all , true)
  ────────────────────────────────────────
  Q: Q2
  Status: PARTIAL
  Confidence: HIGH
  Notes: aud: str | list[str] (not just str); no iat/nbf claims on the model
  ────────────────────────────────────────
  Q: Q3
  Status: CONFIRMED
  Confidence: HIGH
  Notes: Wired into lifespan at main.py:72
  ────────────────────────────────────────
  Q: Q4
  Status: CONFIRMED
  Confidence: HIGH
  Notes: Only 2 hits in src/, both legitimate (privilege check + readiness)
  ────────────────────────────────────────
  Q: Q5
  Status: PARTIAL
  Confidence: HIGH
  Notes: NOT-NULL form correct; IS-NULL-gated form referenced no longer applies (URA dropped)
  ────────────────────────────────────────
  Q: Q6
  Status: CONFIRMED
  Confidence: HIGH
  Notes: All 6 multi-tenant tables have ENABLE+FORCE+POLICY
  ────────────────────────────────────────
  Q: Q7
  Status: PARTIAL
  Confidence: HIGH
  Notes: 80 R.add calls (doc claims 74); shape correct
  ────────────────────────────────────────
  Q: Q8
  Status: PARTIAL
  Confidence: HIGH
  Notes: Test exists; assertion phrased differently (query-result vs raw GUC)
  ────────────────────────────────────────
  Q: Q9
  Status: CONFIRMED
  Confidence: HIGH
  Notes: At expected path, expected assertion
  ────────────────────────────────────────
  Q: Q10
  Status: PARTIAL
  Confidence: HIGH
  Notes: Source-binding correct; TENANT_CONTEXT_MISMATCH not implemented
  ────────────────────────────────────────
  Q: Q11
  Status: CONFIRMED
  Confidence: HIGH
  Notes: Production path uses , true) only
  ────────────────────────────────────────
  Q: Q12
  Status: CONFIRMED
  Confidence: HIGH
  Notes: No BYPASSRLS role anywhere; single engine
  ────────────────────────────────────────
  Q: Q13
  Status: CONFIRMED
  Confidence: HIGH
  Notes: Replicas mentioned only in design docs
  ────────────────────────────────────────
  Q: Q14
  Status: UNVERIFIABLE
  Confidence: HIGH (for negative finding)
  Notes: No CREATE/ALTER ROLE in repo; runtime gate enforces
  ────────────────────────────────────────
  Q: Q15
  Status: CONFIRMED
  Confidence: HIGH
  Notes: All 6 tables have FORCE alongside ENABLE
  ────────────────────────────────────────
  Q: Q16
  Status: CONFIRMED
  Confidence: HIGH
  Notes: pool_size=10, max_overflow=5, pre_ping/recycle as documented
  ────────────────────────────────────────
  Q: Q17
  Status: PARTIAL
  Confidence: HIGH
  Notes: Meta-query is correctly shaped but excludes tenants (no tenant_id column)
  ────────────────────────────────────────
  Q: Q18
  Status: CONFIRMED
  Confidence: HIGH
  Notes: No production-code Layer-2 bypass
  ────────────────────────────────────────
  Q: Q19
  Status: CONFIRMED
  Confidence: HIGH
  Notes: All 6 policies uniform (post-Step-6.8.1)
  ────────────────────────────────────────
  Q: Q20
  Status: REFUTED
  Confidence: HIGH
  Notes: Document inverts middleware order — actual is CORS→Audit→Auth, not Audit→Auth→CORS
  ────────────────────────────────────────
  Q: Q21
  Status: PARTIAL
  Confidence: HIGH
  Notes: Paths use /api/v1 prefix not /v1; 6 paths total (doc lists 5); skip list correct in substance
  ────────────────────────────────────────
  Q: Q22
  Status: UNVERIFIABLE
  Confidence: HIGH (negative finding)
  Notes: Cloud DATABASE_URL not in repo
  ────────────────────────────────────────
  Q: Q23
  Status: CONFIRMED
  Confidence: HIGH
  Notes: Bucket C empty; no transitively-scoped tables

  ---
  Material discrepancies

  These change the architectural picture and are highest priority for the writeup author.

  1. Q20 (REFUTED): middleware order is inverted in the document. The document claims AuditContext (outermost) → Auth (middle) → CORS 
  (innermost). Actual runtime order is CORS (outermost) → AuditContext (middle) → Auth (innermost) per main.py:128-136. The code's
  reasoning for CORS-outermost (so Access-Control-Allow-Origin lands on auth-rejected 401s) is architecturally correct; the document's
  claim is the inverse.
  2. Q10 (PARTIAL → REFUTED on the specific code-name claim): TENANT_CONTEXT_MISMATCH does not exist in the codebase. The document and
  CLAUDE.md D-18 both describe a 400 quarantine path with code TENANT_CONTEXT_MISMATCH for JWT-vs-path tenant mismatch. Search across
  src/ returned zero hits. The actual implementation relies on RLS-as-404 (D-17). Substantively the data isolation holds (RLS handles
  it), but the architectural mechanism described in the document is not the one in the code — there is no quarantine, no 400, no
  TENANT_CONTEXT_MISMATCH log signal.
  3. Q0 / Q5 (PARTIAL → architectural drift): the document's "5 multi-tenant tables" inventory is two tables short. Live multi-tenant
  inventory is 6: tenants, tenant_users, org_nodes, stores, tenant_module_access, tenant_user_role_assignments. Per Q5,
  user_role_assignments (the doc's 5th) was dropped at Step 6.8.1; tenant_module_access (Step 3.4.5) and tenant_user_role_assignments
  (Step 6.8.1) are post-document additions. The IS-NULL-gated policy variant the document quotes no longer applies anywhere — all 6
  multi-tenant tables now use the uniform unconditional OR-branch shape post-Step-6.8.1.

  ---
  UNVERIFIABLE-from-source claims
  
  Claim: Q14 — Application role is actually NOSUPERUSER NOBYPASSRLS on each deployed environment
  What's needed: Live DB query: SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = 'user_admin_backend' against each
    environment. The runtime gate at startup makes a misconfigured environment fail fast, but verifying at any specific point in time
    requires accessing that environment.
  ────────────────────────────────────────
  Claim: Q22 — Cloud production DATABASE_URL points at the audited application role
  What's needed: Cloud Run env-var inspection (or Terraform / GCP infra repo, which is not in this repo per the CLAUDE.md infra-repo
    cross-reference).
  ────────────────────────────────────────
  Claim: Q22 — GCP IAM-mapped Postgres role privileges (if Cloud SQL IAM auth is used)
  What's needed: Cloud SQL admin console / IAM policy inspection; not in this repo.

  ---
  Cosmetic / naming-level drifts
  
  - Q7: smoke test count 74 is stale; current code has 80 R.add calls (CLAUDE.md Step 6.8.1 entry says 81; precise count depends on
  which assertions evaluate vs short-circuit at runtime — but the static count is 80, not 74).
  - Q21: public-path examples use /v1/... in the document; actual code uses /api/v1/.... The skip list also includes /api/v1/ready
  which the document didn't enumerate.
  - Q2: the document references iat and nbf standard JWT claims; AuthContext in auth/context.py:54-57 carries sub, iss, aud, exp only.
  - Q5: document quotes the canonical policy form correctly; the file ranges where policies live drift across migrations (e59f62d5037d,
   4fd3aec6ae0c, 21e2ad16303a, cd2a02e452ae, 3e05299cb533). 4fd3aec6ae0c's IS-NULL-gated form was dropped along with
  user_role_assignments at 3e05299cb533.

