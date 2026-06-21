 Investigation report: Step 6.18 (Role Edit)

  Working assumptions verification

  - Assumption 1 (ADMIN.ROLES.OVERRIDE.GLOBAL absent in Excel + local DB): CONFIRMED.
    - DB: SELECT ... WHERE code = 'ADMIN.ROLES.OVERRIDE.GLOBAL' returned (0 rows).
    - Excel: scan over the permissions sheet (35 rows) found no row whose code contains ADMIN.ROLES.OVERRIDE. Only 3
  ADMIN.ROLES.* rows: p17 (VIEW.TENANT), p18 (CONFIGURE.TENANT), p33 (CONFIGURE.GLOBAL).
  - Assumption 2 (SUPER_ADMIN sole initial holder of new perm): N/A — perm doesn't exist yet. Verify in 6.18.1 seed delta.
  - Assumption 3 (~15 roles): CONFIRMED EXACTLY. core.roles row count = 15 (3 PLATFORM + 12 TENANT). Full list in Bucket
  3a.
  - Assumption 4 (~33-35 perms): CONFIRMED. core.permissions row count = 35; Excel permissions sheet row count = 35. (Post
   Step 6.17.1 catalogue delta.)
  - Assumption 5 (role_permissions is a simple join): CONTRADICTED IN PART. It is a junction with composite PK (role_id, 
  permission_id), but it ALSO carries 3 audit columns: created_at (NOT NULL, DEFAULT now()), created_by_user_id (UUID,
  nullable), created_by_user_type (actor_user_type_enum, nullable) — Pattern (b) audit-actor on created_by only (per
  models/role_permission.py:1-17 docstring: "the junction has no update lifecycle — rows are created and deleted, never
  edited in place"). FKs ON UPDATE RESTRICT and ON DELETE RESTRICT to both parents. CHECK
  ck_role_permissions_created_by_actor_pair requires both audit fields co-set or both NULL. Has 131 rows. PATCH
  replace-set must therefore populate audit-actor columns on each INSERT (Pattern (b) convention applies —
  _actor_type_from_auth from Step 6.10.1).
  - Assumption 6 (user status JOIN works): CONFIRMED via Bucket 6c dry-run (returned 1 holder for
  ADMIN.TENANTS.OVERRIDE.GLOBAL stand-in). The JOIN column names are platform_user_id on platform_user_role_assignments
  and tenant_user_id on tenant_user_role_assignments, both FKs to the respective parent user tables' id. ACTIVE labels are
   uppercase on all three enums (user_role_assignment_status_enum.ACTIVE, platform_user_status_enum.ACTIVE,
  tenant_user_status_enum.ACTIVE). Important: the prompt's draft invariant SQL omitted the assignment-side status filter
  (pura.status='ACTIVE'/tura.status='ACTIVE'); without it, INACTIVE (revoked) assignments would inflate the count.
  Surfacing this as a load-bearing correction.

  Bucket 1: GET /roles and GET /roles/{role_id}/permissions

  1a. Router file

  - File: src/admin_backend/routers/v1/rbac.py (390 lines)
  - Handlers in this file (E1/E2/E3/E6 per its module docstring):
    - list_roles at routers/v1/rbac.py:121 (@roles_router.get(""))
    - list_role_permissions at routers/v1/rbac.py:228 (@roles_router.get("/{role_id}/permissions"))
    - list_permissions at routers/v1/rbac.py:271 (@permissions_router.get(""))
    - get_permission_matrix at routers/v1/rbac.py:339 (@matrix_router.get(""))
  - main.py registration: main.py:212 (roles_router), :215 (permissions_router), :218 (matrix_router); each registered
  separately under settings.api_prefix.

  1b. GET /api/v1/roles (list) handler details

  - Gate tuple: none — Depends(require(...)) is NOT present. Path /api/v1/roles is in GATE_EXEMPT_PATHS
  (auth/gate_allowlist.py:44). Tracked as FN-AB-30 (revisit on Stage 2).
  - Session dep: get_tenant_session_dep (multi-audience). Visibility is enforced AT THE APPLICATION LAYER via
  _audience_filter_for(auth) (rbac.py:108-115) which maps auth.user_type='TENANT' → 'TENANT', else None.
  - Repo method called: RolesRepo.list_grouped(session, *, audience_filter, status, is_system, q, sort, offset, limit) -> 
  dict[str, tuple[list[tuple[Role, int]], int]]
  - Mapping: handler builds RoleListItem via model_validate({...}) from a (Role, user_count) tuple inside _to_block
  (rbac.py:197-217); not a helper module, inline closure. Each item carries 10 fields including is_system and user_count
  (correlated subquery result).

  1c. GET /api/v1/roles/{role_id}/permissions handler details

  - Gate tuple: none — in GATE_EXEMPT_PATHS (gate_allowlist.py:45).
  - Session dep: get_tenant_session_dep (multi-audience).
  - Role existence check: NO anchor dep. The handler calls RolesRepo.get_by_id(session, role_id, audience_filter=...)
  (line 247-249) BEFORE list_permissions_for_role, and converts a None result to RoleNotFoundError (404 ROLE_NOT_FOUND).
  The audience filter is applied inside get_by_id (repositories/roles.py:206-228) — TENANT JWTs probing a PLATFORM role's
  id receive 404. This is app-layer audience gating, not anchor cascade; the role catalogue is platform-global with no
  RLS, and no org_node anchor exists for a role.
  - is_system check: NONE. The handler does NOT inspect role.is_system anywhere. (Line 247-265.)

  1d. Schema definitions

  src/admin_backend/schemas/role.py (96 lines):
  - RoleListItem (line 36, ConfigDict(from_attributes=True), no extra="forbid"):
    - id: UUID, name: str, code: str, description: str | None, status: RoleStatus, is_system: bool, user_count: int 
  (Field), created_at: datetime, updated_at: datetime
    - audience deliberately NOT included — comment at lines 19-22 / 39-41: implied by container key.
  - AudienceBlock (line 66, no ConfigDict): items: list[RoleListItem], total: int (Field).
  - RoleListResponse (line 79, no ConfigDict): platform_roles: AudienceBlock, tenant_roles: AudienceBlock.

  src/admin_backend/schemas/permission.py (156 lines):
  - PermissionRead (line 47, ConfigDict(from_attributes=True), no extra="forbid"): id, module, resource, action, scope, 
  code, description, created_at, updated_at. No *_label fields.
  - PermissionListResponse (line 63): items: list[PermissionRead], pagination: Pagination.
  - RolePermissionsResponse (line 70, no ConfigDict): role_id: UUID (Field), role_name: str (Field), items: 
  list[PermissionRead]. No user_count, no is_system. No extra="forbid".
  - PermissionMatrixRoleColumn (line 96, from_attributes=True): id, name, audience.
  - PermissionMatrixRow (line 106, no ConfigDict): id, module, module_label, resource, resource_label, action, 
  action_label, scope, scope_label, cells: list[bool].
  - PermissionMatrixResponse (line 133, no ConfigDict): roles, rows.

  Finding for design: None of these schemas use ConfigDict(extra="forbid"). This diverges from Step 6.10.1+ Stage 2
  write-side convention (TenantUserCreateRequest, etc., all extra="forbid"). If 6.18 introduces a RoleUpdateRequest
  schema, the new shape should use extra="forbid" to align with the Stage 2 convention; the existing *Response shapes
  don't need retrofitting.

  1e. is_system — behavioural verification (LOAD-BEARING)

  (i) Defined at:
  - Python model: src/admin_backend/models/role.py:117-119 — is_system: Mapped[bool] = mapped_column(Boolean, 
  nullable=False, server_default=FetchedValue()).
  - DDL (via \d+ core.roles): is_system | boolean | not null | default false.
  - Schema: src/admin_backend/schemas/role.py:50 — is_system: bool on RoleListItem (NO default — required on response).

  (ii) Populated by:
  - DDL column with DEFAULT false at the DB layer.
  - Seed Excel: literal Python booleans (operator note in CLAUDE.md line 1381 captures a one-time conversion from
  =TRUE()/=FALSE() formulas during Step 6.1 cleanup). All 15 roles in Excel have explicit booleans. 14 of 15 seeded roles
  have is_system=true; NIGHT_SHIFT_LEAD is the lone is_system=false in the live data.
  
  (iii) Consumed at (every reference enumerated):
  - src/admin_backend/routers/v1/rbac.py:143 — declared as a Query parameter on E1 (filter).
  - src/admin_backend/routers/v1/rbac.py:186 — passed as kwarg into _roles_repo.list_grouped(...).
  - src/admin_backend/routers/v1/rbac.py:209 — serialized to response body inside _to_block.
  - src/admin_backend/repositories/roles.py:126 — function parameter signature on list_grouped.
  - src/admin_backend/repositories/roles.py:144 — docstring.
  - src/admin_backend/repositories/roles.py:174-175 — WHERE-clause filter: conditions.append(Role.is_system == is_system).
  - src/admin_backend/models/role.py:117-119 — column definition.
  - tests/integration/test_rbac_router.py:181, 185 — H1/H2 contract assertions.
  - tests/integration/test_rbac_router.py:380-391 — R8 test: filter behaviour.
  - tests/integration/conftest.py:1030, 1050, 1077, 1084, 1095, 1101 — make_role factory default is_system=False.
  - docs/endpoints/dashboard.md:278,299 and docs/endpoints/rbac.md:57,77,93,113,162,176 — documentation.
  - DDL constraints: none on is_system. Verified — only ck_roles_archived_consistency, ck_roles_code_format,
  ck_roles_created_by_actor_pair, ck_roles_name_length, ck_roles_updated_by_actor_pair — none mention is_system.
  - Triggers: only tg_roles_set_updated_at (timestamp); no audience or is_system trigger on roles.

  Verdict: is_system is INFORMATIONAL ONLY. The field is a filter dimension (list endpoint) and a response field (UI
  display segmentation, dashboard custom_roles sentinel). Nothing — handler, repo, gate, DDL CHECK, trigger — currently 
  REJECTS write/edit/delete operations on rows with is_system=true. This is the load-bearing fact for the Pattern 1 vs
  Pattern 2 design decision: Pattern 1 (DB-layer enforcement) does not exist today.

  Bucket 2: Catalogue state

  2a. ADMIN.ROLES.OVERRIDE.GLOBAL absent

  - DB: SELECT code, id FROM core.permissions WHERE code = 'ADMIN.ROLES.OVERRIDE.GLOBAL' → (0 rows).
  - Excel: 0 matching rows in permissions sheet.
  - Cloud SQL state: operator-confirmed absent (out of scope for this investigation).

  2b. All ADMIN.ROLES.* permissions in catalogue

  ADMIN.ROLES.CONFIGURE.GLOBAL  019e3a09-1404-7568-850e-59ae30279537
  ADMIN.ROLES.CONFIGURE.TENANT  019e3a09-13fa-7053-8ed3-7c0e67b53bf9
  ADMIN.ROLES.VIEW.TENANT       019e3a09-13f9-782a-8396-c35d8101cb1e
  (3 rows.) Descriptions: VIEW.TENANT = "View tenant roles", CONFIGURE.TENANT = "Create/configure tenant roles",
  CONFIGURE.GLOBAL = "Manage role catalogue platform-wide".

  2c. All OVERRIDE-action permissions in catalogue

  ADMIN.TENANTS.OVERRIDE.GLOBAL        019e3a09-13fe-7509-888a-699bfcc9a117
  ADMIN.USERS.OVERRIDE.GLOBAL          019e3a09-13ff-78a1-a43a-ab32131e454d
  PRICING_OS.MARKDOWNS.OVERRIDE.STORE  019e3a09-13ee-73f9-b9ea-1466aeb805cb
  (3 rows.) The two ADMIN.*.OVERRIDE.GLOBAL rows are the natural neighbours for ADMIN.ROLES.OVERRIDE.GLOBAL.

  2d. Current OVERRIDE-action permission holders

  ADMIN.TENANTS.OVERRIDE.GLOBAL        SUPER_ADMIN
  ADMIN.USERS.OVERRIDE.GLOBAL          SUPER_ADMIN
  ADMIN.USERS.OVERRIDE.GLOBAL          SUPPORT_ADMIN
  PRICING_OS.MARKDOWNS.OVERRIDE.STORE  OWNER
  PRICING_OS.MARKDOWNS.OVERRIDE.STORE  SUPER_ADMIN
  SUPER_ADMIN holds all three; SUPPORT_ADMIN holds ADMIN.USERS.OVERRIDE.GLOBAL; OWNER holds
  PRICING_OS.MARKDOWNS.OVERRIDE.STORE. If 6.18 holds Assumption 2 (SUPER_ADMIN only), the new perm differs from the
  established ADMIN.*.OVERRIDE.GLOBAL pattern only on ADMIN.USERS.OVERRIDE.GLOBAL's second holder (SUPPORT_ADMIN).

  Bucket 3: Roles structure

  3a. Full role list (15 rows, ordered audience+code)

  PLATFORM:
    PLATFORM_ADMIN      Platform Admin       is_system=t
    SUPER_ADMIN         Super Admin          is_system=t
    SUPPORT_ADMIN       Support Admin        is_system=t

  TENANT:
    ASSOCIATE           Associate            is_system=t
    CATEGORY_MANAGER    Category Manager     is_system=t
    COMPLIANCE_OFFICER  Compliance Officer   is_system=t
    DATA_ANALYST        Data Analyst         is_system=t
    FINANCE_ADMIN       Finance Admin        is_system=t
    NIGHT_SHIFT_LEAD    Night Shift Lead     is_system=f   ← the sole non-system row
    OWNER               Owner                is_system=t
    PERISHABLES_LEAD    Perishables Lead     is_system=t
    PRICING_MANAGER     Pricing Manager      is_system=t
    PROMOTIONS_MANAGER  Promotions Manager   is_system=t
    REGIONAL_DIRECTOR   Regional Director    is_system=t
    STORE_MANAGER       Store Manager        is_system=t

  3b. core.roles DDL summary

  - Columns (16): id (uuid, NOT NULL, core.uuidv7()), name (text, NOT NULL), code (text, NOT NULL), description (text,
  nullable), audience (core.role_audience_enum, NOT NULL), status (core.role_status_enum, NOT NULL, default 'ACTIVE'),
  is_system (boolean, NOT NULL, default false), then 3 Pattern (b) audit-actor triplets (created_at, created_by_user_id,
  created_by_user_type; updated_at, updated_by_user_id, updated_by_user_type; archived_at, archived_by_user_id,
  archived_by_user_type).
  - CHECK constraints (5):
    a. ck_roles_archived_consistency — archive triplet co-set IFF status='ARCHIVED'.
    b. ck_roles_code_format — code ~ '^[A-Z][A-Z0-9_]{1,49}$' (uppercase, alnum + underscore, 2-50 chars).
    c. ck_roles_created_by_actor_pair — created_by_* pair co-set or both NULL.
    d. ck_roles_name_length — length(name) BETWEEN 1 AND 100.
    e. ck_roles_updated_by_actor_pair — updated_by_* pair co-set or both NULL.
  - No FK out of roles. Inbound FKs (3, all ON UPDATE RESTRICT ON DELETE RESTRICT): platform_user_role_assignments,
  role_permissions, tenant_user_role_assignments.
  - Indexes: pk_roles, ix_roles_audience, ix_roles_status, uq_roles_code.
  - Triggers: tg_roles_set_updated_at (BEFORE UPDATE) — auto-bumps updated_at. Note: PATCH need not set updated_at
  explicitly.
  - No RLS. relrowsecurity=f, relforcerowsecurity=f (pg_class query, line "roles | f | f"). No policies (pg_policies
  empty).

  3c. core.role_permissions DDL summary

  - Columns (5): role_id, permission_id, created_at (NOT NULL, default now()), created_by_user_id (UUID, nullable),
  created_by_user_type (actor_user_type_enum, nullable). NO id surrogate; PK is composite (role_id, permission_id).
  - No updated_* audit actor (junction has no update lifecycle per models/role_permission.py:6-10).
  - CHECK: ck_role_permissions_created_by_actor_pair — pair co-set or both NULL.
  - FKs (2): fk_role_permissions_role, fk_role_permissions_permission — both ON UPDATE RESTRICT and ON DELETE RESTRICT. ON
   DELETE RESTRICT on the permission FK means deleting a permission referenced anywhere fails (no cascade); ON DELETE
  RESTRICT on the role FK means deleting a role with any junction row fails.
  - Indexes: pk_role_permissions (composite), ix_role_permissions_permission (reverse lookup).
  - No RLS. relrowsecurity=f.
  - Implication for PATCH replace-set: INSERT new rows + DELETE old rows are the only operations (no UPDATE possible —
  composite PK + no other writable columns). Each INSERT must populate the Pattern (b) created_by_* pair from AuthContext
  (use _actor_type_from_auth helper convention).

  Bucket 4: RolesRepo

  4a. Existence

  - Yes. class RolesRepo at src/admin_backend/repositories/roles.py:117.

  4b. Public method signatures (3 methods, all READ-only)

  async def list_grouped(
      self,
      session: AsyncSession,
      *,
      audience_filter: str | None,
      status: str | None = None,
      is_system: bool | None = None,
      q: str | None = None,
      sort: str = "name_asc",
      offset: int = 0,
      limit: int = 50,
  ) -> dict[str, tuple[list[tuple[Role, int]], int]]

  async def get_by_id(
      self,
      session: AsyncSession,
      role_id: UUID,
      *,
      audience_filter: str | None,
  ) -> tuple[Role, int] | None

  async def list_permissions_for_role(
      self,
      session: AsyncSession,
      role_id: UUID,
  ) -> list[Permission]
  - SQL style: SQLAlchemy ORM (select(Role), select(Permission).join(...)). NO raw text(). Per-row user_count is built via
   _user_count_subquery() (module-level helper at line 67) which returns a column expression — sum of TWO correlated 
  scalar subqueries (PlatformUserRoleAssignment.role_id==Role.id AND status='ACTIVE' +
  TenantUserRoleAssignment.role_id==Role.id AND status='ACTIVE'), each with .correlate(Role). R4 test guards the per-row
  correlation.
  - Joins to role_permissions + permissions: ONLY inside list_permissions_for_role (line 242-253) —
  select(Permission).join(RolePermission, RolePermission.permission_id == Permission.id).where(RolePermission.role_id == 
  role_id).
  - No write methods. No update, patch, rename, create, delete, transition, archive. Step 6.18 will add the first write
  method(s) to this Repo.

  4c. RolesRepo consumers (full enumeration)

  - src/admin_backend/routers/v1/rbac.py:60 (import) and :85 (_roles_repo = RolesRepo()).
  - No other consumers in src/. No test imports RolesRepo directly (tests touch Role model and RoleNotFoundError instead).

  Regression risk for 6.18: isolated to routers/v1/rbac.py. The test file tests/integration/test_rbac_router.py
  (R/P/RP/M/H/A tests, 23+ assertions) is the regression surface for any signature change.

  Bucket 5: rbac / permission-matrix surface gate posture

  All four rbac-surface read endpoints are in GATE_EXEMPT_PATHS (auth/gate_allowlist.py:38-46):

  Path: /api/v1/roles
  Method: GET      
  Gate: NONE (allowlisted)
  Session: get_tenant_session_dep       
  ────────────────────────────────────────
  Path: /api/v1/roles/{role_id}/permissions
  Method: GET
  Gate: NONE (allowlisted)
  Session: get_tenant_session_dep
  ────────────────────────────────────────
  Path: /api/v1/permissions
  Method: GET
  Gate: NONE (allowlisted)
  Session: get_tenant_session_dep
  ────────────────────────────────────────
  Path: /api/v1/permission-matrix
  Method: GET
  Gate: NONE (allowlisted)
  Session: get_tenant_session_dep
  ────────────────────────────────────────
  Path: /api/v1/me/permissions
  Method: GET
  Gate: NONE (allowlisted, caller-state)
  Session: get_tenant_session_dep
  ────────────────────────────────────────
  Path: /api/v1/me/can-do
  Method: GET
  Gate: NONE (allowlisted, caller-state)
  Session: get_tenant_session_dep
  ────────────────────────────────────────
  Path: /api/v1/role-assignments
  Method: GET
  Gate: Depends(require(ADMIN, USERS, VIEW, TENANT)) at role_assignments.py:298-303
  Session: get_tenant_session_dep

  TENANT-tier reachability today: every TENANT OWNER can reach E1/E2/E3/E6, /me/*, AND /role-assignments (passes the gate
  via OWNER's direct ADMIN.USERS.VIEW.TENANT grant). The frontend's role-edit UI surface is reachable from TENANT context
  for READ today — adequate for the role-edit screen to render. Step 6.18's new PATCH /api/v1/roles/{role_id} and GET 
  /api/v1/roles/{role_id} will be gated by require(...); existing reads stay allowlisted unless the operator decides to
  bundle the FN-AB-30 gate-hardening.

  FN-AB-30 reference: gate_allowlist.py:30-33 "Forward note: revisit gating /permissions, /permission-matrix, /roles on
  ADMIN.ROLES.VIEW.TENANT when Stage 2 write surfaces." Step 6.18 IS Stage 2 write surface for roles — natural bundling
  point if the operator chooses.

  Bucket 6: User assignment tables and invariant query

  6a. Shapes

  core.platform_user_role_assignments: 11 cols. Pertinent for the invariant query: id, platform_user_id (FK to
  platform_users(id)), role_id, status (user_role_assignment_status_enum), plus granted/revoked Pattern (b) triplets and
  updated_at. Partial unique (platform_user_id, role_id) WHERE status='ACTIVE'. No RLS, no policies. Trigger
  tg_platform_user_role_assignments_audience_check enforces role.audience='PLATFORM'.

  core.tenant_user_role_assignments: 13 cols. Pertinent: id, tenant_user_id, tenant_id, org_node_id, role_id, status.
  Composite FK (tenant_id, tenant_user_id) → tenant_users(tenant_id, id) and (tenant_id, org_node_id) → 
  org_nodes(tenant_id, id) per D-34/AI-RBAC-06. RLS+FORCE with the D-29 unconditional OR-branch. Partial unique
  (tenant_user_id, role_id, org_node_id) WHERE status='ACTIVE'. Trigger tg_tenant_user_role_assignments_audience_check
  enforces role.audience='TENANT'.

  Assignment-level status: YES, both tables have status (user_role_assignment_status_enum) distinct from the parent user's
   status. The invariant query must filter on BOTH. (See 6c finding.)

  6b. Status enum values

  platform_user_status_enum         {INVITED, ACTIVE, SUSPENDED}
  tenant_user_status_enum           {INVITED, ACTIVE, SUSPENDED}
  user_role_assignment_status_enum  {ACTIVE, INACTIVE}
  Uppercase canonical labels — direct string comparison or PG enum cast works.
  
  6c. Invariant query dry-run

  Stand-in permission: ADMIN.TENANTS.OVERRIDE.GLOBAL (held by SUPER_ADMIN per 2d).

  Actual SQL used (added pura.status='ACTIVE' / tura.status='ACTIVE' not in the prompt's draft):

  WITH override_role_ids AS (
      SELECT rp.role_id
      FROM core.role_permissions rp
      JOIN core.permissions p ON p.id = rp.permission_id
      WHERE p.code = 'ADMIN.TENANTS.OVERRIDE.GLOBAL'
  )
  SELECT COUNT(DISTINCT user_id) AS active_holders FROM (
      SELECT pura.platform_user_id AS user_id
      FROM core.platform_user_role_assignments pura
      JOIN override_role_ids ori ON ori.role_id = pura.role_id
      JOIN core.platform_users pu ON pu.id = pura.platform_user_id
      WHERE pura.status = 'ACTIVE' AND pu.status = 'ACTIVE'
      UNION
      SELECT tura.tenant_user_id AS user_id
      FROM core.tenant_user_role_assignments tura
      JOIN override_role_ids ori ON ori.role_id = tura.role_id
      JOIN core.tenant_users tu ON tu.id = tura.tenant_user_id
      WHERE tura.status = 'ACTIVE' AND tu.status = 'ACTIVE'
  ) holders;

  Result: active_holders = 1 (Anjali via SUPER_ADMIN).

  Findings for Phase 1 / Phase 2:

  1. Prompt SQL omitted assignment-side status filter. Without pura.status='ACTIVE'/tura.status='ACTIVE', INACTIVE
  (revoked) assignments would be counted as "holders" — the invariant would over-count and a true last-holder edit could
  pass the gate falsely. Load-bearing correction. Lock the canonical invariant SQL to filter BOTH assignment status AND
  user status.
  2. Local DB state: core.tenant_user_role_assignments currently has 0 rows (table re-truncated by recent smoke / not
  reseeded). The seed loader is the cure (python -m scripts.seed_dev_data --reset). This affects ad-hoc TENANT-side
  invariant testing; doesn't change the design. No row in the TENANT table holds OVERRIDE today regardless (no
  TENANT-audience OVERRIDE-ROLES tuple exists), so the dry-run's correctness is not affected.
  3. DISTINCT semantics: COUNT(DISTINCT user_id) works because platform_user_id and tenant_user_id are both UUIDs from
  different namespaces; a UUID collision across the two physical tables is structurally impossible (UUIDv7 random
  component). Safe.
  4. No tenant-context dependency on UNION query. Under a PLATFORM session, the tenant-side branch sees all rows via D-29
  OR-branch; under a TENANT session, RLS scopes it. For the invariant check at PATCH time, the request is gated on
  ADMIN.ROLES.OVERRIDE.GLOBAL (presumed SUPER_ADMIN-only per Assumption 2), so the caller is PLATFORM and the count is
  fleet-wide — correct for the "at least one active holder PLATFORM-WIDE" semantic.

  Bucket 7: Codebase observations beyond scope

  - a) FN-AB-30 (gate_allowlist.py:30-33): Already names the precondition Step 6.18 bumps into — /roles, /permissions,
  /permission-matrix are intentionally exempt today, slated to re-gate on ADMIN.ROLES.VIEW.TENANT when Stage 2 writes
  ship. Step 6.18 IS that ship. Operator decision: bundle the read-gate hardening or punt.
  - b) is_system history hazard. CLAUDE.md (lines 1381, archive/CLAUDEv1.md:1381) captures a Step 6.1 incident: openpyxl
  wiped =TRUE()/=FALSE() formulas to None on Excel save; restored by converting to literal booleans. Any 6.18 seed-Excel 
  edit (e.g., to add the new permission row) must save the file with formula-free booleans intact. Verify post-edit by
  re-reading is_system for all 15 roles before commit.
  - c) ck_roles_code_format constraint. Code column constrained to ^[A-Z][A-Z0-9_]{1,49}$. If Step 6.18's PATCH allows
  code edit, the request schema MUST validate the same regex client-side OR rely on DB IntegrityError → handler maps to
  422 INVALID_CODE_FORMAT. The closer-to-spec choice is forbidding code edit on system roles (is_system=true) — natural
  Pattern 2 partial constraint. The operator-stated scope was "name and replace-set permissions"; the role's code is not
  in scope per the prompt.
  - d) Archived role lifecycle exists. status='ARCHIVED' triggers ck_roles_archived_consistency requiring archived_at + 
  archived_by_user_id + archived_by_user_type to be co-set. Step 6.18 PATCH should either reject edits to ARCHIVED roles
  (analogous to TERMINATED tenants) or explicitly support unarchive — needs design.
  - e) No get_role_anchor exists in auth/anchor_deps.py. The 4 existing anchor deps (get_tenant_anchor,
  get_org_node_anchor, get_store_anchor, get_tenant_user_anchor) all resolve org-tree-anchored entities. A role has no
  org_node anchor; a GLOBAL-scoped ADMIN.ROLES.OVERRIDE.GLOBAL gate requires anchor_dep=None (no anchor cascade needed at
  GLOBAL scope per _SCOPE_CASCADE_ORDER and require() semantics).
  - f) Test pattern precedent (Step 6.9.2 T_GF4): _test_repo = TenantsRepo() at module level + patch.object(_test_repo, 
  "method", AsyncMock()) + call_count == 0 is the established pattern for asserting "gate denial NEVER fires handler body
  Repo call". Reusable in 6.18 for asserting the LAST_OVERRIDE_HOLDER invariant short-circuits the PATCH write path.
  - g) Pattern (b) audit-actor population convention (Step 6.10.1 + 6.10.2 + 6.11.2 + 6.13 + 6.14): every UPDATE / INSERT
  writes BOTH *_by_user_id AND *_by_user_type from AuthContext via _actor_type_from_auth (located in
  repositories/tenant_users.py / repositories/tenants.py as a free function helper, depending on which file's review).
  6.18's PATCH writes updated_by_* on roles and created_by_* on each new role_permissions row.
  - h) roles.updated_at auto-bumped by tg_roles_set_updated_at trigger; handler need not set it.
  - i) RolePermissionsResponse parent-echo schema does NOT carry user_count or is_system. If 6.18 adds a GET 
  /api/v1/roles/{role_id} detail endpoint, the operator should decide whether the new detail schema includes them (D-31
  append-only: safe to add as new sibling fields).
  - j) Excel seed roles sheet column 8 is the is_system boolean. Header: ('_key', 'id', 'name', 'code', 'description', 
  'audience', 'status', 'is_system', 'created_at', 'created_by_user_id', 'created_by_user_type', 'updated_at', 
  'updated_by_user_id', 'updated_by_user_type', 'archived_at', 'archived_by_user_id', 'archived_by_user_type'). Future
  seed loader validation hook (FN-AB-34 / FN-AB-48 territory) might enforce booleans not formulas — out of scope for 6.18.
  - k) _user_count_subquery() cost. Already efficient: two correlated scalar subqueries against partial-unique-indexed
  columns. PATCH's invariant query (Bucket 6c canonical SQL) is structurally similar — one CTE + two UNION arms hitting
  pk_*_role_assignments-adjacent indexes. Sub-millisecond at v0 scale; no caching needed.
  - l) Step 6.14 diff-replace precedent (CLAUDE.md FN-AB-45). The role-replace-set semantics on PATCH 
  /api/v1/tenant-users/{id} (Step 6.14) shifted from whole-set to diff-replace explicitly so unchanged (role_id, 
  org_node_id) tuples preserve their original granted_at/granted_by_* audit history. For role_permissions, the audit 
  column is only created_by_* (no updated_*); diff-replace preserves created_at for unchanged tuples just like 6.14 
  preserves granted_at. Recommended: 6.18's PATCH should also be diff-replace on role_permissions, not whole-set
  wipe-and-insert.
  - m) RoleNotFoundError already defined inline (rbac.py:93). Step 6.18 reuses it (no router-local re-definition for the
  detail endpoint or PATCH). Per the cross-resource pattern from Step 6.9.3.2 (TenantUserNotFoundError,
  OrgNodeNotFoundError promoted to errors.py), if RoleNotFoundError becomes used by anchor-dep-style logic, promote it to
  errors.py. For 6.18, in-router-only is acceptable.
  - n) RolesRepo has no precedent for a write method. Step 6.18 introduces the first. The TenantsRepo write methods
  (create, update, transition) are the closest precedent — raw text() SQL with f"{schema}.*" qualification, explicit
  session.expire_all() after raw UPDATE to defeat identity-map staleness. The cross-resource convention is to NOT use ORM
  for the write paths.
  - o) Existing RoleNotFoundError 404 mask. Today the audience-filtered RoleNotFoundError fires for both genuine misses
  and audience-filtered probes. Step 6.18 PATCH gated on ADMIN.ROLES.OVERRIDE.GLOBAL (PLATFORM-only via SUPER_ADMIN) means
   audience filter is irrelevant for callers; a 404 reflects only "the id doesn't exist". This is structurally consistent
  with D-17.
  - p) tests/integration/conftest.py:make_role factory — raw SQL INSERT pattern (per CLAUDE.md note); audit-actor pair
  must be NULL/NULL or both-NOT-NULL. New tests can reuse.
  - q) Cross-tenant injection guard. tenant_user_role_assignments composite FKs (D-34) make injection structurally
  impossible — for role-edit purposes (role_permissions is platform-global, no tenant context), this is non-applicable.
  The role catalogue write surface has no tenant boundary.

  Summary

  - 6 findings confirm assumptions: 1 (catalogue absent), 3 (15 roles), 4 (35 perms), 6 (JOIN works), plus 2
  (deferred-N/A) and an extra-detail confirmation around the 3 ADMIN.ROLES.* tuples in catalogue + the 3 OVERRIDE-action
  tuples in catalogue.
  - 1 finding partially contradicts an assumption: Assumption 5 — role_permissions is a junction but carries Pattern (b)
  created_by_* audit-actor columns + has FKs ON DELETE RESTRICT. INSERT path must populate audit-actor pair from
  AuthContext.
  - 5 open questions for Phase 1 / Phase 2 design:
    a. Operator-stated scope is "name + replace-set permissions"; should code, description, audience, is_system, status
  also be editable? audience likely never (audience-check triggers); code only on non-system roles; is_system unclear
  (currently informational only).
    b. PATCH semantics on ARCHIVED roles (reject vs allow unarchive)?
    c. Bundle FN-AB-30 read-gate hardening on /roles, /permissions, /permission-matrix in 6.18 or punt?
    d. New GET /api/v1/roles/{role_id} detail schema fields — include user_count, permissions[] inline, or stay minimal
  and rely on E3 for permission drilldown?
    e. PATCH role_permissions: diff-replace (preserves created_at per Step 6.14 precedent) vs whole-set (simpler but loses
   audit history)?
  - Verdict on is_system: Informational only. No DDL constraint, no trigger, no handler reject, no repo filter rejecting
  writes on is_system=true rows. The field is a UI hint / dashboard segmentation device today. Pattern 1 (DB-side
  enforcement: DDL CHECK or trigger that rejects UPDATE/DELETE on is_system=true rows) does NOT currently exist; Pattern 2
   (app-layer handler rejection) does NOT currently exist either. Step 6.18's protection of system roles is a green-field
  decision — neither pattern is anchored by precedent.
  - Load-bearing correction to the prompt's invariant SQL: must filter pura.status='ACTIVE' AND tura.status='ACTIVE'
  (assignment-side), not just user-side pu.status='ACTIVE'/tu.status='ACTIVE'. Without it, revoked (INACTIVE) assignments
  inflate the count and a true last-holder edit could pass the gate falsely.
