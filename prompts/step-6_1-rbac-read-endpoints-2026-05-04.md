# Prompt — Step 6.1: RBAC read endpoints (Roles + Permissions + Permission Matrix)

> Generated 2026-05-04. Revised through frontend-locked design review:
> - Scope narrowed from 5 endpoints to 4 (assignments-side dropped to forward notes).
> - Response shapes locked: E1 pre-grouped by audience; E3 with parent echo; E6 render-ready grid.
> - 25-row lookups seed migration for permission display labels.
> - DDL cleanup narrowed to `module_enum` and `permission_scope_enum` only (resource_enum and action_enum already match locked vocabulary).
> Paste this entire block into a fresh Claude Code session to start Step 6.1.

---

## Context: why this step exists and why now

The Frontend Roles & Permissions page (Frontend spec 7.5) operates in two views:

- **Role catalog tab.** Two-column: left is grouped role list (`PLATFORM ROLES` / `TENANT ROLES`) with user_count beneath each role name; right is the selected role's detail with its permissions block.
- **Permission matrix tab.** Wide grid: rows are permissions, columns are roles, cells are checkboxes indicating "this role grants this permission."

Three endpoints back the catalog tab; one backs the matrix tab. All four are read-only and v0-locked.

The original BUILD_PLAN entry called for 5 endpoints across 4 resource families, including a list of `user_role_assignments`. A frontend-locked design review (logged 2026-05-04) cut the assignments-side endpoints because:

1. The Users page renders role chips by reading inline `roles[]` on `/tenant-users` and `/platform-users` — that augmentation is its own follow-up step (FN: A1, A2).
2. No v0 UI consumer exercises a flat assignments list (FN: E4).
3. The audit-log drawer's single-fetch use case is dormant in v0 (FN: E5).

These deferrals are captured as forward notes in the Step 6.1 BUILD_PLAN entry's "Known follow-ups (RBAC)" sub-section. They land when their landing triggers fire.

This step also closes a vocabulary drift surfaced during prompt drafting: the seed Excel uses values from a draft vocabulary that doesn't match the locked product vocabulary on `module_enum` and `permission_scope_enum`. A small DDL cleanup migration narrows those two enums to the locked vocabulary; `resource_enum` and `action_enum` already match the locked list and need no migration.

---

## Pre-flight

1. Run `./scripts/check_setup.sh`. Expect 35/35.
2. `git log --oneline -5` — confirm Step 5.3 (org-tree) at HEAD. Most recent migration in the alembic chain should still be `0644a4186e48` from Step 3.6; Step 5.3 added no migration.
3. `uv run alembic heads` — confirm output is exactly `0644a4186e48 (head)`.
4. Read `CLAUDE.md` fully. Focus on:
   - **D-13** — audit-actor patterns. RBAC tables use Pattern (b): `*_by_user_id UUID + *_by_user_type actor_user_type_enum`. **Hide audit-actor columns from response shapes**, same hide-policy as Steps 3.3/5.1/5.2.
   - **D-15** — DB_SCHEMA from environment.
   - **D-21** — UUIDv7 default.
   - **D-29** — PLATFORM RLS visibility. RBAC tables in this step (`roles`, `permissions`, `role_permissions`) are platform-global with NO RLS. Visibility is controlled at the application layer via the `audience` column.
   - **D-30** — list-only response envelope. **E1 is a deliberate D-30 exception** (pre-grouped into `platform_roles` / `tenant_roles` blocks). **E3 echoes parent identity** at the top of the response (`role_id`, `role_name`, no pagination). **E6 returns a render-ready matrix** with no `items` wrapper. Document each exception in the OpenAPI summary; do not attempt to force the list-envelope.
   - **D-31** — response field semantics are append-only.
   - "Note on the v0 auth model" subsection — Step 6.1 introduces **app-layer audience filtering** for non-RLS tables. TENANT JWTs see only `audience='TENANT'` roles on E1, E3, and the `roles` block of E6. PLATFORM JWTs see both. This pattern is captured as a new convention note in CLAUDE.md (alongside the existing PG enum and batch-by-key envelope notes).
   - "Note on PG enum columns" subsection — RBAC's existing PG enums need narrowing on two of four. The `actor_user_type_enum` is already declared by tenant_users ORM (Step 5.2); **reuse, don't redeclare**.
   - "Shared sort-key error classes" — `InvalidSortKeyError` / `InvalidSortKeyClientError` already in `repositories/_errors.py` and `errors.py` from Step 5.2. **Reuse, don't duplicate.**
5. Read `docs/architecture.md` "Schema and storage" section — confirms RBAC is the 8th DDL file with 4 tables; this step models 3 of them (UserRoleAssignment is deferred per FN: E4/E5).
6. Read `db/raw_ddl/Ithina_postgres_SQL_DDL_rbac_v2.sql` fully. **Locked vocabulary (final):**

   ```
   module_enum:           ADMIN, PRICING_OS, PERISHABLES_ASSISTANT, PROMOTIONS_ASSISTANT
                          (DDL has 6 values; cleanup migration drops ROOS, GOAL_CONSOLE)

   resource_enum:         PRICING_RULES, MARKDOWNS, WASTE_LOG, USERS, AUDIT_LOG,
                          EXPIRING_ITEMS, CAMPAIGNS, DONATION_ROUTING, ROLES,
                          TENANTS, STORES, ORG_NODES
                          (DDL already matches; NO migration needed)

   action_enum:           VIEW, CONFIGURE, AUDIT, APPROVE, OVERRIDE, EXECUTE
                          (DDL already matches; NO migration needed)

   permission_scope_enum: GLOBAL, TENANT, STORE
                          (DDL has 4 values; cleanup migration drops REGION)

   role_audience_enum:    PLATFORM, TENANT       (already correct; no migration)
   role_status_enum:      ACTIVE, INACTIVE, ARCHIVED   (already correct; no migration)
   ```

7. Read `src/admin_backend/models/tenant_user.py` (Step 5.2's ORM) — the new `Role`, `Permission`, `RolePermission` models mirror its shape exactly: `__table_args__`, `FetchedValue()` defaults, `postgresql.ENUM(name="...", create_type=False)`, raw UUID for audit-actor columns.
8. Read `src/admin_backend/models/_lightweight_stubs.py` — confirm no RBAC stub exists (none expected). For the user_count correlated subquery on E1, the `user_role_assignments` table is referenced — see Stop-and-ask trigger #9 below for the lightweight-stub vs raw-SQL decision.
9. Read `src/admin_backend/repositories/tenant_users.py` and `src/admin_backend/repositories/platform_users.py` — the new RBAC repos mirror their shape: stateless singleton, `__init__(self, session: AsyncSession)`, async methods only, **no `tenant_id` parameter** (D-24 — visibility flows from session GUCs; for non-RLS tables, the audience filter flows from auth context via the router's helper).
10. Read `src/admin_backend/routers/v1/tenant_users.py` and `src/admin_backend/routers/v1/lookups.py`. The new RBAC router consolidates 4 endpoints across 3 URL prefixes (`/roles`, `/permissions`, `/permission-matrix`) into one file. Mirror Step 5.2's auth-context conventions and Step 3.6's clean lookups-Repo invocation pattern.
11. Read `src/admin_backend/schemas/tenant_user.py` and `src/admin_backend/schemas/lookup.py` — the new RBAC schemas use Pydantic v2 `model_config = ConfigDict(from_attributes=True)`, no aliasing.
12. Read `src/admin_backend/errors.py` — confirm `ClientError` base class shape and existing `*NotFoundError` classes. New error class: `RoleNotFoundError` (404). `InvalidSortKeyClientError` is reused.
13. Read `tests/integration/test_tenant_users_router.py` — fixture machinery (`client`, `_platform_jwt`, `_tenant_jwt`, `make_*` factories) and load-bearing test patterns.
14. Read `tests/integration/conftest.py` — confirm whether RBAC factories already exist. **Step 3.5's seed loader inserts roles/permissions/role_permissions/user_role_assignments**, but conftest factories may not. New factories needed for this step: `make_role`, `make_permission`, `make_role_permission`. (`make_user_role_assignment` is deferred to whichever step lands E4.)
15. Read `BUILD_PLAN.md` Step 6.1 in full. The original entry is sparse and lists 5 endpoints; this step's commit rewrites scope-in/acceptance to reflect the 4-endpoint reality plus the "Known follow-ups (RBAC)" sub-section.
16. Read `docs/endpoints/tenant-users.md` and `docs/endpoints/platform-users.md` — closest precedents for the 8-section format. The new `docs/endpoints/rbac.md` covers all 4 endpoints in one file.
17. Read `data/ithina_dev_seed_data.xlsx` sheets `roles`, `permissions`, `role_permissions`, `user_role_assignments` AND `lookups`. **Stop-and-ask trigger #1** below covers the seed permission cleanup decision.
18. Read `scripts/seed_dev_data/column_mappings.py` and the existing lookups loader if present. Verify the lookups loader accepts new rows without changes (the column mapping should be row-count-agnostic).
19. Read this prompt fully.

---

## Step ID and intent

**Step 6.1** — RBAC read endpoints. Four endpoints backing the Roles & Permissions page (Frontend spec 7.5).

**Endpoints in scope:**

| # | Method + path | Auth | Visibility |
|---|---|---|---|
| E1 | `GET /api/v1/roles` | multi-user-type | PLATFORM sees 3 PLATFORM + 12 TENANT roles; TENANT sees only 12 TENANT roles |
| E2 | `GET /api/v1/permissions` | multi-user-type | Both see full catalogue |
| E3 | `GET /api/v1/roles/{role_id}/permissions` | multi-user-type | TENANT user requesting a PLATFORM role's permissions → 404 ROLE_NOT_FOUND |
| E6 | `GET /api/v1/permission-matrix` | multi-user-type | TENANT sees TENANT-audience role columns only |

**Forward notes (NOT in scope this step):** A1 (inline `roles[]` on `/tenant-users`), A2 (inline `roles[]` on `/platform-users`), E4 (`/user-role-assignments` list), E5 (`/user-role-assignments/{id}` single-fetch), MODULES-EXT (ROOS/GOAL_CONSOLE module enum extension), RESOURCES-EXT (MODULE_ACCESS/GUARDRAILS/APPROVALS resource enum extension). Captured in BUILD_PLAN's Step 6.1 "Known follow-ups (RBAC)" sub-section with their landing triggers.

**Twelve concrete deliverables:**

1. **DDL cleanup migration** narrowing 2 PG enums (`module_enum`, `permission_scope_enum`) to the locked vocabulary and deleting legacy seed permission rows. **`resource_enum` and `action_enum` already match the locked vocabulary in the DDL — no migration needed for those.**
2. **Lookups seed migration** adding 25 rows for the 4 enum-display-label categories.
3. **3 ORM models**: `Role`, `Permission`, `RolePermission`. (`UserRoleAssignment` deferred per FN: E4/E5.)
4. **Schemas**: `RoleListItem`, `RoleListResponse` (pre-grouped `platform_roles` + `tenant_roles`), `PermissionRead`, `PermissionListResponse`, `RolePermissionsResponse` (E3's parent-echo), `PermissionMatrixResponse` (E6's render-ready grid).
5. **`RolesRepo`** with `list_grouped(...)`, `get_by_id(...)`, `list_permissions_for_role(...)`.
6. **`PermissionsRepo`** with `list(...)`.
7. **`PermissionMatrixRepo`** with `get_matrix(audience_filter)`.
8. **Router** at `routers/v1/rbac.py` consolidating 4 endpoints under 3 APIRouter objects (`/roles`, `/permissions`, `/permission-matrix`).
9. **3 conftest factories**: `make_role`, `make_permission`, `make_role_permission`.
10. **Integration tests**: 23 tests across 4 endpoints — envelope/hidden contracts, audience filtering, cross-tenant 404 paths, matrix shape, label join.
11. **Excel seed update**: append 25 rows to `data/ithina_dev_seed_data.xlsx` `lookups` sheet AND remove legacy permission rows from `permissions` sheet (binary file commit).
12. **`docs/endpoints/rbac.md`** — single doc covering all 4 endpoints in 8-section format per endpoint.

CLAUDE_CODE step. Two migrations land in this step. Same complexity envelope as Step 5.2 plus migration work — expect ~3-4 hours.

---

## Source-of-truth specification

### Locked vocabulary (the only values valid for this step)

```
module_enum:           ADMIN, PRICING_OS, PERISHABLES_ASSISTANT, PROMOTIONS_ASSISTANT      [4]
resource_enum:         PRICING_RULES, MARKDOWNS, WASTE_LOG, USERS, AUDIT_LOG,
                       EXPIRING_ITEMS, CAMPAIGNS, DONATION_ROUTING, ROLES,
                       TENANTS, STORES, ORG_NODES                                          [12]
action_enum:           VIEW, CONFIGURE, AUDIT, APPROVE, OVERRIDE, EXECUTE                  [6]
permission_scope_enum: GLOBAL, TENANT, STORE                                               [3]
```

### Locked endpoint contracts

#### E1 — `GET /api/v1/roles`

**Request**
```http
GET /api/v1/roles?status=&q=&is_system=&sort=name_asc&limit=50&offset=0
Authorization: Bearer <jwt>
```

Query params (all optional):
- `status` — `ACTIVE` (default) | `INACTIVE` | `ARCHIVED`
- `q` — ILIKE search across `name`, `code`, `description`
- `is_system` — `true` | `false`
- `sort` — `name_asc` (default within each group) | `name_desc` | `created_at_desc` | `created_at_asc`
- `limit`, `offset` — present for consistency, never paginates in v0

**Response 200 (PLATFORM JWT)**
```jsonc
{
  "platform_roles": {
    "items": [
      {
        "id": "94340a03-3f07-4814-91d6-3f78e3e9de99",
        "name": "Platform Admin",
        "code": "PLATFORM_ADMIN",
        "description": "Create/manage tenants and platform users",
        "status": "ACTIVE",
        "is_system": true,
        "user_count": 1,
        "created_at": "2026-04-19T15:00:00Z",
        "updated_at": "2026-04-19T15:00:00Z"
      }
      // ... 2 more, ordered name_asc
    ],
    "total": 3
  },
  "tenant_roles": {
    "items": [
      {
        "id": "...",
        "name": "Associate",
        "code": "ASSOCIATE",
        "description": "...",
        "status": "ACTIVE",
        "is_system": true,
        "user_count": 2,
        "created_at": "2026-04-19T15:00:00Z",
        "updated_at": "2026-04-19T15:00:00Z"
      }
      // ... 11 more, ordered name_asc
    ],
    "total": 12
  }
}
```

**Response 200 (TENANT JWT)** — `platform_roles` block always returns `{items: [], total: 0}`. Frontend suppresses empty section header.

**Notes:**
- `audience` field DROPPED from items (implied by container key).
- `user_count` correlated subquery on `user_role_assignments` filtered to `status='ACTIVE'`. **`.correlate(Role)` load-bearing** (Step 3.3 L9 / Step 5.3 L11 lesson). For TENANT JWTs, RLS on `user_role_assignments` automatically scopes the count to the calling tenant (D-29 IS-NULL-gated OR-clause).
- Audit-actor columns hidden (D-13 hide-policy).
- No top-level pagination block — pre-grouped shape doesn't compose with cross-group pagination.

#### E2 — `GET /api/v1/permissions`

**Request**
```http
GET /api/v1/permissions?module=&scope=&sort=module_asc&limit=100&offset=0
Authorization: Bearer <jwt>
```

Query params (all optional):
- `module` — one of locked module values
- `scope` — one of locked scope values
- `sort` — `module_asc` (default) | `code_asc` | `code_desc`
- `limit`, `offset` — present for consistency, never paginates in v0

**Response 200 (same shape for both user types)**
```jsonc
{
  "items": [
    {
      "id": "5a8aaeca-1a50-4ec3-aabc-25fa3fe12e47",
      "module": "ADMIN",
      "resource": "USERS",
      "action": "CONFIGURE",
      "scope": "TENANT",
      "code": "ADMIN.USERS.CONFIGURE.TENANT",
      "description": "Invite, suspend, and reactivate users within a tenant",
      "created_at": "2026-04-19T15:00:00Z",
      "updated_at": "2026-04-19T15:00:00Z"
    }
    // ... more, sorted module/resource/action/scope ascending
  ],
  "pagination": { "limit": 100, "offset": 0, "total": <N>, "has_more": false }
}
```

**Notes:**
- Catalogue is reference data — both user types see all rows.
- Default sort `module_asc, resource_asc, action_asc, scope_asc` clusters related rows together (matches matrix render order from E6).
- No `*_label` fields on E2 — labels are in E6's response only.
- Total post-cleanup is bounded by what survives the DDL cleanup migration's DELETE on legacy `module='ROOS'/'GOAL_CONSOLE'` and `scope='REGION'` rows. Step 6.3 (RBAC seed migration) reseeds the canonical catalogue. For Step 6.1's verification, expect total > 0 and < 100; do not pin a specific count.

#### E3 — `GET /api/v1/roles/{role_id}/permissions`

**Request**
```http
GET /api/v1/roles/90b2b633-956b-4c0c-a849-9b926b5252e3/permissions
Authorization: Bearer <jwt>
```

No query params.

**Response 200**
```jsonc
{
  "role_id": "90b2b633-956b-4c0c-a849-9b926b5252e3",
  "role_name": "Owner",
  "items": [
    {
      "id": "5a8aaeca-1a50-4ec3-aabc-25fa3fe12e47",
      "module": "ADMIN",
      "resource": "USERS",
      "action": "CONFIGURE",
      "scope": "TENANT",
      "code": "ADMIN.USERS.CONFIGURE.TENANT",
      "description": "Invite, suspend, and reactivate users within a tenant",
      "created_at": "2026-04-19T15:00:00Z",
      "updated_at": "2026-04-19T15:00:00Z"
    }
    // ... more, sorted module/resource/action/scope ascending
  ]
}
```

**Notes:**
- Top-level `role_id` echo — frontend race-condition guard.
- Top-level `role_name` — saves the frontend a cross-lookup against E1's cached list when rendering the right-pane header.
- No pagination — a role has bounded permissions (~5-30); pagination would be ceremony.
- TENANT JWT requesting a PLATFORM role's id → **404 ROLE_NOT_FOUND** (audience filter applied at lookup).

#### E6 — `GET /api/v1/permission-matrix`

**Request**
```http
GET /api/v1/permission-matrix
Authorization: Bearer <jwt>
```

No query params.

**Response 200 (PLATFORM JWT)**
```jsonc
{
  "roles": [
    { "id": "94340a03-...", "name": "Platform Admin", "audience": "PLATFORM" },
    { "id": "f10c718b-...", "name": "Super Admin",    "audience": "PLATFORM" },
    { "id": "14fcdd54-...", "name": "Support Admin",  "audience": "PLATFORM" },
    { "id": "...",          "name": "Associate",      "audience": "TENANT" }
    // ... 11 more, ordered audience_asc, name_asc — column order
  ],
  "rows": [
    {
      "id": "4d71c366-...",
      "module": "PRICING_OS",
      "module_label": "Pricing OS",
      "resource": "PRICING_RULES",
      "resource_label": "Pricing Rules",
      "action": "VIEW",
      "action_label": "View",
      "scope": "TENANT",
      "scope_label": "Tenant",
      "cells": [true, true, false, false, true, true, true, true, false, true, false, false, false, false, false]
    },
    {
      "id": "ce9e1a11-...",
      "module": "PRICING_OS",
      "module_label": "Pricing OS",
      "resource": "PRICING_RULES",
      "resource_label": "Pricing Rules",
      "action": "CONFIGURE",
      "action_label": "Configure",
      "scope": "TENANT",
      "scope_label": "Tenant",
      "cells": [true, true, false, false, true, true, false, false, false, false, false, false, false, false, false]
    }
    // ... more, ordered module/resource/action/scope ascending
  ]
}
```

**Response 200 (TENANT JWT)** — `roles` array contains only TENANT-audience roles (12 columns). Each `row.cells` array is correspondingly length 12.

**Notes — locked invariants for E6:**

| # | Invariant |
|---|---|
| M1 | `cells` is a `boolean[]` array. `cells[i]` is the grant state for `roles[i]`. Position-based join. |
| M2 | `len(row.cells) == len(roles)` for every row. Backend guarantees alignment. |
| M3 | `roles` ordered `audience_asc, name_asc` (PLATFORM columns first, alphabetical within). |
| M4 | `rows` ordered `module_asc, resource_asc, action_asc, scope_asc` (matches E2 default and matrix UI). |
| M5 | TENANT JWT response: `roles` filtered to `audience='TENANT'` only. `cells[i]` count drops from 15 to 12. |
| M6 | Each row carries 4 enum codes + 4 display labels: `module/module_label`, `resource/resource_label`, `action/action_label`, `scope/scope_label`. |
| M7 | Display labels source: JOIN `permissions` against `lookups` 4 times by `(list_name, code)`. List names: `module`, `resource`, `permission_action`, `permission_scope`. |
| M8 | No pagination, no filters. The matrix is one shape, returned in full. |

---

### File 1: DDL cleanup migration — `migrations/versions/<rev>_step_6_1_rbac_enum_cleanup.py`

Two enum types need narrowing: `module_enum` (drop `ROOS`, `GOAL_CONSOLE`) and `permission_scope_enum` (drop `REGION`). `resource_enum` and `action_enum` already match the locked vocabulary in the DDL.

**Strategy:** rename old enums to `*_legacy`, create new enums with locked literals, ALTER TABLE COLUMN to use the new types via USING cast through text, drop the legacy enums. PG doesn't support ALTER TYPE DROP VALUE so this rename-recreate dance is the safe path.

**Stop-and-ask trigger #1** below covers the seed Excel handling for the legacy permission rows.

```python
"""step_6_1_rbac_enum_cleanup

Narrow 2 RBAC enums to the locked product vocabulary:
  - module_enum:           drops ROOS, GOAL_CONSOLE (4 values remain)
  - permission_scope_enum: drops REGION (3 values remain)

resource_enum and action_enum already match the locked vocabulary
in the DDL; not touched here.

Forward-only: legacy permission rows (with module='ROOS' or 'GOAL_CONSOLE'
or scope='REGION') are deleted. ROOS / GOAL_CONSOLE may be added back
as additive ALTER TYPE migrations later if/when those modules ship
(see FN-RBAC-MODULES-EXT in BUILD_PLAN's Known follow-ups).
"""
revision = "<auto>"
down_revision = "0644a4186e48"

def upgrade():
    # 1. Drop legacy seed rows BEFORE altering enum types.
    #    role_permissions FK RESTRICT requires deletion of junction rows first.
    op.execute("""
        DELETE FROM role_permissions
        WHERE permission_id IN (
            SELECT id FROM permissions
            WHERE module::text IN ('ROOS', 'GOAL_CONSOLE')
               OR scope::text  = 'REGION'
        );
    """)
    op.execute("""
        DELETE FROM permissions
        WHERE module::text IN ('ROOS', 'GOAL_CONSOLE')
           OR scope::text  = 'REGION';
    """)

    # 2. Rename old enums.
    op.execute("ALTER TYPE module_enum RENAME TO module_enum_legacy;")
    op.execute("ALTER TYPE permission_scope_enum RENAME TO permission_scope_enum_legacy;")

    # 3. Create new enums with locked vocabulary.
    op.execute("""
        CREATE TYPE module_enum AS ENUM (
            'ADMIN', 'PRICING_OS', 'PERISHABLES_ASSISTANT', 'PROMOTIONS_ASSISTANT'
        );
    """)
    op.execute("""
        CREATE TYPE permission_scope_enum AS ENUM (
            'GLOBAL', 'TENANT', 'STORE'
        );
    """)

    # 4. ALTER COLUMN TYPEs via USING cast through text.
    op.execute("""
        ALTER TABLE permissions
            ALTER COLUMN module TYPE module_enum
                USING module::text::module_enum,
            ALTER COLUMN scope  TYPE permission_scope_enum
                USING scope::text::permission_scope_enum;
    """)

    # 5. Drop legacy enums.
    op.execute("DROP TYPE module_enum_legacy;")
    op.execute("DROP TYPE permission_scope_enum_legacy;")


def downgrade():
    # Forward-only: deleted permission rows cannot be reconstructed.
    # Document in CLAUDE.md as a one-line entry under the schema-state line.
    raise NotImplementedError(
        "Forward-only migration: legacy permission rows deleted in upgrade()"
    )
```

---

### File 2: Lookups seed migration — `migrations/versions/<rev>_step_6_1_lookups_for_permissions.py`

Seeds 25 rows into `core.lookups` for permission display labels. Idempotent via `ON CONFLICT (list_name, code) DO NOTHING`.

```python
"""step_6_1_lookups_for_permissions

Seed display labels for the 4 permission-tuple slots:
  module             4 rows
  resource          12 rows
  permission_action  6 rows
  permission_scope   3 rows
                    ----
                    25 rows

These back the *_label fields on E6's permission-matrix endpoint.
"""
revision = "<auto>"
down_revision = "<rev of File 1>"

LOOKUP_ROWS = [
    # module (4)
    ('module', 'ADMIN',                'Admin',                 1),
    ('module', 'PRICING_OS',            'Pricing OS',            2),
    ('module', 'PERISHABLES_ASSISTANT', 'Perishables Assistant', 3),
    ('module', 'PROMOTIONS_ASSISTANT',  'Promotions Assistant',  4),
    # resource (12)
    ('resource', 'PRICING_RULES',     'Pricing Rules',     1),
    ('resource', 'MARKDOWNS',         'Markdowns',         2),
    ('resource', 'WASTE_LOG',         'Waste Log',         3),
    ('resource', 'USERS',             'Users',             4),
    ('resource', 'AUDIT_LOG',         'Audit Log',         5),
    ('resource', 'EXPIRING_ITEMS',    'Expiring Items',    6),
    ('resource', 'CAMPAIGNS',         'Campaigns',         7),
    ('resource', 'DONATION_ROUTING',  'Donation Routing',  8),
    ('resource', 'ROLES',             'Roles',             9),
    ('resource', 'TENANTS',           'Tenants',          10),
    ('resource', 'STORES',            'Stores',           11),
    ('resource', 'ORG_NODES',         'Org Nodes',        12),
    # permission_action (6)
    ('permission_action', 'VIEW',      'View',      1),
    ('permission_action', 'CONFIGURE', 'Configure', 2),
    ('permission_action', 'AUDIT',     'Audit',     3),
    ('permission_action', 'APPROVE',   'Approve',   4),
    ('permission_action', 'OVERRIDE',  'Override',  5),
    ('permission_action', 'EXECUTE',   'Execute',   6),
    # permission_scope (3)
    ('permission_scope', 'GLOBAL', 'Global', 1),
    ('permission_scope', 'TENANT', 'Tenant', 2),
    ('permission_scope', 'STORE',  'Store',  3),
]


def upgrade():
    rows_sql = ", ".join(
        f"('{ln}', '{c}', '{dn}', {do}, TRUE)"
        for ln, c, dn, do in LOOKUP_ROWS
    )
    op.execute(f"""
        INSERT INTO lookups (list_name, code, display_name, display_order, is_active)
        VALUES {rows_sql}
        ON CONFLICT (list_name, code) DO NOTHING;
    """)


def downgrade():
    op.execute("""
        DELETE FROM lookups
        WHERE list_name IN ('module', 'resource', 'permission_action', 'permission_scope');
    """)
```

Unqualified table names per the schema-search_path-resolution convention from Step 3.6.

---

### File 3: `src/admin_backend/models/role.py` — new

Mirror `models/tenant_user.py` shape. Pattern (b) audit-actors. Reuses `actor_user_type_enum` declaration.

```python
"""ORM model for roles.

Platform-global table — no RLS. Visibility controlled at app layer
via the audience column: TENANT JWTs see only audience='TENANT'.
"""
from __future__ import annotations
from datetime import datetime
from uuid import UUID
from sqlalchemy import FetchedValue
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column
from admin_backend.config import settings
from admin_backend.models._base import Base


class Role(Base):
    __tablename__ = "roles"
    __table_args__ = {"schema": settings.db_schema}

    id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        server_default=FetchedValue(),
    )
    name: Mapped[str] = mapped_column(nullable=False)
    code: Mapped[str] = mapped_column(nullable=False)
    description: Mapped[str | None] = mapped_column(nullable=True)
    audience: Mapped[str] = mapped_column(
        postgresql.ENUM("PLATFORM", "TENANT",
                        name="role_audience_enum", create_type=False),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        postgresql.ENUM("ACTIVE", "INACTIVE", "ARCHIVED",
                        name="role_status_enum", create_type=False),
        nullable=False,
    )
    is_system: Mapped[bool] = mapped_column(nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=FetchedValue(),
    )
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True), nullable=True,
    )
    created_by_user_type: Mapped[str | None] = mapped_column(
        postgresql.ENUM("PLATFORM", "TENANT",
                        name="actor_user_type_enum", create_type=False),
        nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=FetchedValue(),
    )
    updated_by_user_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True), nullable=True,
    )
    updated_by_user_type: Mapped[str | None] = mapped_column(
        postgresql.ENUM("PLATFORM", "TENANT",
                        name="actor_user_type_enum", create_type=False),
        nullable=True,
    )
    archived_at: Mapped[datetime | None] = mapped_column(nullable=True)
    archived_by_user_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True), nullable=True,
    )
    archived_by_user_type: Mapped[str | None] = mapped_column(
        postgresql.ENUM("PLATFORM", "TENANT",
                        name="actor_user_type_enum", create_type=False),
        nullable=True,
    )
```

---

### File 4: `src/admin_backend/models/permission.py` — new

```python
"""ORM model for permissions catalogue.

Platform-global, no RLS. Both user types see all rows. Minimal audit:
created_at + updated_at only, no audit-actor pairs.
"""
from __future__ import annotations
from datetime import datetime
from uuid import UUID
from sqlalchemy import FetchedValue
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column
from admin_backend.config import settings
from admin_backend.models._base import Base


class Permission(Base):
    __tablename__ = "permissions"
    __table_args__ = {"schema": settings.db_schema}

    id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        server_default=FetchedValue(),
    )
    module: Mapped[str] = mapped_column(
        postgresql.ENUM(
            "ADMIN", "PRICING_OS", "PERISHABLES_ASSISTANT", "PROMOTIONS_ASSISTANT",
            name="module_enum", create_type=False,
        ),
        nullable=False,
    )
    resource: Mapped[str] = mapped_column(
        postgresql.ENUM(
            "PRICING_RULES", "MARKDOWNS", "WASTE_LOG",
            "USERS", "AUDIT_LOG", "EXPIRING_ITEMS",
            "CAMPAIGNS", "DONATION_ROUTING", "ROLES",
            "TENANTS", "STORES", "ORG_NODES",
            name="resource_enum", create_type=False,
        ),
        nullable=False,
    )
    action: Mapped[str] = mapped_column(
        postgresql.ENUM(
            "VIEW", "CONFIGURE", "AUDIT", "APPROVE", "OVERRIDE", "EXECUTE",
            name="action_enum", create_type=False,
        ),
        nullable=False,
    )
    scope: Mapped[str] = mapped_column(
        postgresql.ENUM(
            "GLOBAL", "TENANT", "STORE",
            name="permission_scope_enum", create_type=False,
        ),
        nullable=False,
    )
    code: Mapped[str] = mapped_column(nullable=False)
    description: Mapped[str | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=FetchedValue(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=FetchedValue(),
    )
```

---

### File 5: `src/admin_backend/models/role_permission.py` — new

```python
"""ORM model for role_permissions junction.

Composite PK. Pattern (b) audit-actor on created_by only.
"""
from __future__ import annotations
from datetime import datetime
from uuid import UUID
from sqlalchemy import FetchedValue, ForeignKey
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column
from admin_backend.config import settings
from admin_backend.models._base import Base


class RolePermission(Base):
    __tablename__ = "role_permissions"
    __table_args__ = {"schema": settings.db_schema}

    role_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey(f"{settings.db_schema}.roles.id"),
        primary_key=True,
    )
    permission_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey(f"{settings.db_schema}.permissions.id"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=FetchedValue(),
    )
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True), nullable=True,
    )
    created_by_user_type: Mapped[str | None] = mapped_column(
        postgresql.ENUM("PLATFORM", "TENANT",
                        name="actor_user_type_enum", create_type=False),
        nullable=True,
    )
```

---

### File 6: `src/admin_backend/models/__init__.py` — modify

```python
from admin_backend.models.role import Role  # noqa: F401
from admin_backend.models.permission import Permission  # noqa: F401
from admin_backend.models.role_permission import RolePermission  # noqa: F401
```

---

### File 7: `src/admin_backend/schemas/role.py` — new

```python
"""Pydantic schemas for roles."""
from __future__ import annotations
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class RoleListItem(BaseModel):
    """Item shape for E1's pre-grouped response.

    `audience` field is NOT included — it's implied by the container key
    (`platform_roles` vs `tenant_roles`).
    """
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    code: str
    description: str | None
    status: str
    is_system: bool
    user_count: int = Field(
        description=(
            "Active assignments referencing this role. Counted via correlated "
            "subquery on user_role_assignments where status='ACTIVE'. "
            "RLS-scoped for TENANT JWTs (count reflects only their tenant's "
            "assignments)."
        ),
    )
    created_at: datetime
    updated_at: datetime


class AudienceBlock(BaseModel):
    items: list[RoleListItem]
    total: int


class RoleListResponse(BaseModel):
    """E1 response: pre-grouped by audience.

    TENANT JWT response: platform_roles always {items: [], total: 0}.
    """
    platform_roles: AudienceBlock
    tenant_roles: AudienceBlock
```

---

### File 8: `src/admin_backend/schemas/permission.py` — new

```python
"""Pydantic schemas for permissions and the permission matrix."""
from __future__ import annotations
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field
from admin_backend.schemas.common import Pagination  # or wherever it lives


class PermissionRead(BaseModel):
    """Shape used by E2 and E3 items (no display labels)."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    module: str
    resource: str
    action: str
    scope: str
    code: str
    description: str | None
    created_at: datetime
    updated_at: datetime


class PermissionListResponse(BaseModel):
    """E2 response."""
    items: list[PermissionRead]
    pagination: Pagination


class RolePermissionsResponse(BaseModel):
    """E3 response: role identity + permissions list. No pagination
    (a role has bounded permissions ~5-30)."""
    role_id: UUID
    role_name: str
    items: list[PermissionRead]


class PermissionMatrixRoleColumn(BaseModel):
    """One column header in E6's grid."""
    id: UUID
    name: str
    audience: str


class PermissionMatrixRow(BaseModel):
    """One permission row in E6's grid.

    Each row carries 4 enum codes + 4 display labels + a boolean array
    of grant states aligned with the roles[] column array (cells[i]
    aligns with roles[i]).
    """
    id: UUID
    module: str
    module_label: str
    resource: str
    resource_label: str
    action: str
    action_label: str
    scope: str
    scope_label: str
    cells: list[bool] = Field(
        description=(
            "Position-based grant array. cells[i] is the grant state of "
            "this permission for roles[i]. len(cells) == len(roles)."
        ),
    )


class PermissionMatrixResponse(BaseModel):
    """E6 response: render-ready grid.

    PLATFORM JWT: roles has 15 columns. TENANT JWT: roles has 12
    columns (audience='TENANT' only); cells arrays correspondingly
    shorter.
    """
    roles: list[PermissionMatrixRoleColumn]
    rows: list[PermissionMatrixRow]
```

---

### File 9: `src/admin_backend/schemas/__init__.py` — modify

Re-export the new schemas. Mirror existing pattern.

---

### File 10: `src/admin_backend/repositories/roles.py` — new

```python
"""Repository for roles + role->permissions JOIN.

Three methods backing E1 and E3. No `tenant_id` parameter — visibility
flows from auth-context-derived `audience` filter (app-layer, not RLS)
for the platform-global tables, and from session GUCs (RLS) for the
user_role_assignments subquery used in user_count.

The user_count subquery references user_role_assignments. The ORM
model for that table is deferred (FN: E4/E5); see Stop-and-ask
trigger #9 for the lightweight-stub vs raw-SQL decision.
"""
from __future__ import annotations
from uuid import UUID
from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession
from admin_backend.models import Role, Permission, RolePermission
from admin_backend.repositories._errors import InvalidSortKeyError


ROLES_SORT_MAP = {
    "name_asc": [Role.name.asc(), Role.id.asc()],
    "name_desc": [Role.name.desc(), Role.id.asc()],
    "created_at_asc": [Role.created_at.asc(), Role.id.asc()],
    "created_at_desc": [Role.created_at.desc(), Role.id.asc()],
}


class RolesRepo:
    async def list_grouped(
        self,
        session: AsyncSession,
        *,
        audience_filter: str | None,  # 'TENANT' for TENANT JWTs, None for PLATFORM
        status: str | None = None,
        is_system: bool | None = None,
        q: str | None = None,
        sort: str = "name_asc",
        offset: int = 0,
        limit: int = 50,
    ) -> dict[str, tuple[list[tuple[Role, int]], int]]:
        """Return {'PLATFORM': (rows, total), 'TENANT': (rows, total)}.

        For TENANT-JWT callers (audience_filter='TENANT'), the PLATFORM
        bucket returns ([], 0). The handler renders the pre-grouped
        response shape from this output.

        user_count is a correlated subquery against user_role_assignments
        with .correlate(Role) — load-bearing for per-row scoping.
        """
        if sort not in ROLES_SORT_MAP:
            raise InvalidSortKeyError(f"unknown sort key: {sort}")
        # ... implementation ...

    async def get_by_id(
        self,
        session: AsyncSession,
        role_id: UUID,
        *,
        audience_filter: str | None,
    ) -> tuple[Role, int] | None:
        """Single-role lookup with optional audience gate. Returns None
        if the role doesn't exist OR if audience_filter excludes it.
        Used by E3."""
        # ... implementation ...

    async def list_permissions_for_role(
        self,
        session: AsyncSession,
        role_id: UUID,
    ) -> list[Permission]:
        """JOIN role_permissions with permissions. Sorted module/
        resource/action/scope ascending. Used by E3.

        Caller is responsible for confirming the role exists and is
        audience-visible BEFORE calling this method.
        """
        stmt = (
            select(Permission)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .where(RolePermission.role_id == role_id)
            .order_by(
                Permission.module.asc(),
                Permission.resource.asc(),
                Permission.action.asc(),
                Permission.scope.asc(),
                Permission.id.asc(),
            )
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())
```

---

### File 11: `src/admin_backend/repositories/permissions.py` — new

```python
"""Repository for permissions catalogue (E2)."""
from __future__ import annotations
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from admin_backend.models import Permission
from admin_backend.repositories._errors import InvalidSortKeyError


PERMISSIONS_SORT_MAP = {
    "module_asc": [
        Permission.module.asc(),
        Permission.resource.asc(),
        Permission.action.asc(),
        Permission.scope.asc(),
        Permission.id.asc(),
    ],
    "code_asc": [Permission.code.asc(), Permission.id.asc()],
    "code_desc": [Permission.code.desc(), Permission.id.asc()],
}


class PermissionsRepo:
    async def list(
        self,
        session: AsyncSession,
        *,
        module: str | None = None,
        scope: str | None = None,
        sort: str = "module_asc",
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[list[Permission], int]:
        if sort not in PERMISSIONS_SORT_MAP:
            raise InvalidSortKeyError(f"unknown sort key: {sort}")
        stmt = select(Permission)
        if module is not None:
            stmt = stmt.where(Permission.module == module)
        if scope is not None:
            stmt = stmt.where(Permission.scope == scope)
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await session.execute(count_stmt)).scalar_one()
        for clause in PERMISSIONS_SORT_MAP[sort]:
            stmt = stmt.order_by(clause)
        result = await session.execute(stmt.offset(offset).limit(limit))
        return list(result.scalars().all()), total
```

---

### File 12: `src/admin_backend/repositories/permission_matrix.py` — new

```python
"""Repository for the permission matrix (E6).

Single method: get_matrix(audience_filter). Builds the render-ready
grid by:
  1. Loading roles (audience-filtered).
  2. Loading permissions with display labels (LEFT JOIN against lookups
     4 times, one per enum slot).
  3. Loading the role_permissions junction filtered to the loaded roles.
  4. (In the router) assembling boolean cells[] arrays per row,
     position-aligned with the roles[] column array.
"""
from __future__ import annotations
from uuid import UUID
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from admin_backend.models import Role, Permission, RolePermission


class PermissionMatrixRepo:
    async def get_matrix(
        self,
        session: AsyncSession,
        *,
        audience_filter: str | None,
    ) -> tuple[list[Role], list[dict], list[tuple[UUID, UUID]]]:
        """Returns (roles, permission_rows, grants).

        - roles: ordered audience_asc, name_asc.
        - permission_rows: list of dicts each with permission fields +
          4 *_label fields (joined from lookups). Ordered module/
          resource/action/scope ascending.
        - grants: list of (role_id, permission_id) tuples for every
          junction row referencing a loaded role.

        The router's _build_matrix_response helper turns these into
        the final response shape with cells[] arrays (position-aligned
        with the roles[] column array).
        """
        # 1. Load roles (audience-filtered + active-only).
        role_stmt = select(Role).where(Role.status == "ACTIVE")
        if audience_filter is not None:
            role_stmt = role_stmt.where(Role.audience == audience_filter)
        role_stmt = role_stmt.order_by(
            Role.audience.asc(), Role.name.asc(), Role.id.asc(),
        )
        roles = list((await session.execute(role_stmt)).scalars().all())
        role_ids = [r.id for r in roles]

        # 2. Load permissions with display labels via 4 lookups joins.
        permission_rows = await self._load_permissions_with_labels(session)

        # 3. Load grants for the loaded roles.
        if not role_ids:
            grants: list[tuple[UUID, UUID]] = []
        else:
            grant_stmt = select(
                RolePermission.role_id, RolePermission.permission_id,
            ).where(RolePermission.role_id.in_(role_ids))
            grants = [
                (r[0], r[1])
                for r in (await session.execute(grant_stmt)).all()
            ]

        return roles, permission_rows, grants

    async def _load_permissions_with_labels(
        self, session: AsyncSession,
    ) -> list[dict]:
        """LEFT JOIN permissions against lookups 4 times. Returns a list
        of dicts, ordered module/resource/action/scope ascending.

        Implementation: use sqlalchemy `aliased(Lookup)` 4 times, one
        alias per slot. LEFT JOIN ON list_name = '<slot>' AND code =
        permission.<slot>. Project display_name as <slot>_label. Fall
        back to the code itself if no lookup row matches (defensive —
        a missing label should not break matrix render).
        """
        # ... raw SQL or aliased subqueries; implementer's choice.
        # See M7 invariant for join semantics.
        ...
```

---

### File 13: `src/admin_backend/routers/v1/rbac.py` — new

```python
"""RBAC read endpoints — 4 endpoints across 3 prefixes.

E1 GET /roles                       — pre-grouped by audience
E2 GET /permissions                 — flat catalogue
E3 GET /roles/{role_id}/permissions — sub-resource with parent echo
E6 GET /permission-matrix           — render-ready grid

Audience filtering: TENANT JWTs see only audience='TENANT' rows on E1,
E3, and E6's roles array. App-layer filter (no RLS on these tables).
PLATFORM JWTs see both audiences.
"""
from __future__ import annotations
from uuid import UUID
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from admin_backend.auth.context import AuthContext
from admin_backend.dependencies import get_auth_context, get_tenant_session_dep
from admin_backend.errors import ClientError, InvalidSortKeyClientError
from admin_backend.repositories._errors import InvalidSortKeyError
from admin_backend.repositories.roles import RolesRepo
from admin_backend.repositories.permissions import PermissionsRepo
from admin_backend.repositories.permission_matrix import PermissionMatrixRepo
from admin_backend.schemas.common import Pagination
from admin_backend.schemas.role import (
    RoleListItem, RoleListResponse, AudienceBlock,
)
from admin_backend.schemas.permission import (
    PermissionRead, PermissionListResponse,
    RolePermissionsResponse,
    PermissionMatrixResponse, PermissionMatrixRoleColumn, PermissionMatrixRow,
)


class RoleNotFoundError(ClientError):
    code = "ROLE_NOT_FOUND"
    http_status = 404
    public_message = "Role not found"


def _audience_filter_for(auth: AuthContext) -> str | None:
    """TENANT JWTs see only audience='TENANT'; PLATFORM sees both."""
    return "TENANT" if auth.user_type == "TENANT" else None


roles_router = APIRouter(prefix="/roles", tags=["rbac"])
permissions_router = APIRouter(prefix="/permissions", tags=["rbac"])
matrix_router = APIRouter(prefix="/permission-matrix", tags=["rbac"])


# ---------- E1 ----------

@roles_router.get("", response_model=RoleListResponse, summary="List roles")
async def list_roles(
    status: str | None = Query(None),
    is_system: bool | None = Query(None),
    q: str | None = Query(None),
    sort: str = Query("name_asc"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> RoleListResponse:
    repo = RolesRepo()
    try:
        grouped = await repo.list_grouped(
            session,
            audience_filter=_audience_filter_for(auth),
            status=status,
            is_system=is_system,
            q=q,
            sort=sort,
            offset=offset,
            limit=limit,
        )
    except InvalidSortKeyError as e:
        raise InvalidSortKeyClientError(str(e))

    def _to_block(rows_total: tuple[list[tuple[Role, int]], int]) -> AudienceBlock:
        rows, total = rows_total
        items = [
            RoleListItem.model_validate({**role.__dict__, "user_count": uc})
            for role, uc in rows
        ]
        return AudienceBlock(items=items, total=total)

    return RoleListResponse(
        platform_roles=_to_block(grouped["PLATFORM"]),
        tenant_roles=_to_block(grouped["TENANT"]),
    )


# ---------- E3 ----------

@roles_router.get(
    "/{role_id}/permissions",
    response_model=RolePermissionsResponse,
    summary="List permissions granted by a role",
)
async def list_role_permissions(
    role_id: UUID,
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> RolePermissionsResponse:
    repo = RolesRepo()
    role_or_none = await repo.get_by_id(
        session, role_id, audience_filter=_audience_filter_for(auth),
    )
    if role_or_none is None:
        raise RoleNotFoundError()
    role, _ = role_or_none

    permissions = await repo.list_permissions_for_role(session, role_id)
    return RolePermissionsResponse(
        role_id=role.id,
        role_name=role.name,
        items=[PermissionRead.model_validate(p) for p in permissions],
    )


# ---------- E2 ----------

@permissions_router.get(
    "", response_model=PermissionListResponse,
    summary="List permission catalogue",
)
async def list_permissions(
    module: str | None = Query(None),
    scope: str | None = Query(None),
    sort: str = Query("module_asc"),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> PermissionListResponse:
    repo = PermissionsRepo()
    try:
        rows, total = await repo.list(
            session, module=module, scope=scope,
            sort=sort, offset=offset, limit=limit,
        )
    except InvalidSortKeyError as e:
        raise InvalidSortKeyClientError(str(e))
    return PermissionListResponse(
        items=[PermissionRead.model_validate(p) for p in rows],
        pagination=Pagination(
            limit=limit, offset=offset, total=total,
            has_more=(offset + limit) < total,
        ),
    )


# ---------- E6 ----------

@matrix_router.get(
    "", response_model=PermissionMatrixResponse,
    summary="Render-ready permission × role matrix",
    description=(
        "Returns the full role × permission grid for the Roles & "
        "Permissions matrix tab (Frontend spec 7.5.4). Cells are "
        "boolean grant flags, position-aligned with the roles[] "
        "column array. TENANT JWT response filters roles to "
        "audience='TENANT' only; cells[] arrays are correspondingly "
        "12 elements wide instead of 15."
    ),
)
async def get_permission_matrix(
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> PermissionMatrixResponse:
    repo = PermissionMatrixRepo()
    roles, permission_rows, grants = await repo.get_matrix(
        session, audience_filter=_audience_filter_for(auth),
    )

    granted_set: set[tuple[UUID, UUID]] = set(grants)
    role_id_order = [r.id for r in roles]

    return PermissionMatrixResponse(
        roles=[
            PermissionMatrixRoleColumn(
                id=r.id, name=r.name, audience=r.audience,
            )
            for r in roles
        ],
        rows=[
            PermissionMatrixRow(
                id=row["id"],
                module=row["module"],
                module_label=row["module_label"],
                resource=row["resource"],
                resource_label=row["resource_label"],
                action=row["action"],
                action_label=row["action_label"],
                scope=row["scope"],
                scope_label=row["scope_label"],
                cells=[
                    (rid, row["id"]) in granted_set
                    for rid in role_id_order
                ],
            )
            for row in permission_rows
        ],
    )
```

---

### File 14: Wire the routers

`src/admin_backend/routers/v1/__init__.py` — modify.

```python
from admin_backend.routers.v1 import rbac
v1_router.include_router(rbac.roles_router)
v1_router.include_router(rbac.permissions_router)
v1_router.include_router(rbac.matrix_router)
```

---

### File 15: `tests/integration/conftest.py` — modify

Add 3 raw-SQL-INSERT factories: `make_role`, `make_permission`, `make_role_permission`. Mirror Step 5.2's `make_tenant_user` pattern. Audit-actor pairs left NULL (Pattern (b) allows it).

(Skeletons preserved from earlier prompt revisions — same shape as `make_tenant_user`.)

---

### File 16: `tests/integration/test_rbac_router.py` — new

23 tests across 4 endpoints. Reuse fixture machinery from `test_tenant_users_router.py`.

```python
"""Integration tests for RBAC read endpoints.

Test ID convention:
  R*  = E1 list (8 tests)
  P*  = E2 list (4 tests)
  RP* = E3 sub-resource (3 tests)
  M*  = E6 matrix (5 tests)
  A*  = auth (1 test)
  H*  = hidden-fields (2 tests)

Five LOAD-BEARING tests:
  R2  TENANT JWT returns empty platform_roles block (audience filter on E1)
  R4  user_count correlated subquery scopes per-row via .correlate(Role)
  RP3 TENANT JWT requesting PLATFORM role's permissions → 404
  M2  E6 cells/roles position alignment invariant (M1, M2)
  M3  E6 TENANT JWT filters role columns (M5)
"""

# E1
async def test_r1_envelope_pre_grouped_with_user_count(...): ...
async def test_r2_tenant_jwt_platform_block_empty(...): ...     # LOAD-BEARING
async def test_r3_platform_jwt_sees_both_audiences(...): ...
async def test_r4_user_count_aggregate_correlates_per_role(...): ...  # LOAD-BEARING
async def test_r5_status_filter_default_active(...): ...
async def test_r6_search_q_ilike(...): ...
async def test_r7_invalid_sort_returns_400(...): ...
async def test_r8_is_system_filter(...): ...

# E2
async def test_p1_envelope_and_default_sort(...): ...
async def test_p2_module_filter(...): ...
async def test_p3_scope_filter(...): ...
async def test_p4_tenant_jwt_sees_full_catalogue(...): ...

# E3
async def test_rp1_returns_role_permissions_with_parent_echo(...): ...
async def test_rp2_unknown_role_returns_404(...): ...
async def test_rp3_tenant_jwt_platform_role_returns_404(...): ...  # LOAD-BEARING

# E6
async def test_m1_matrix_envelope_and_dimensions(...): ...
async def test_m2_cells_aligned_with_roles_array(...): ...  # LOAD-BEARING
async def test_m3_tenant_jwt_filters_role_columns(...): ...  # LOAD-BEARING
async def test_m4_display_labels_join_from_lookups(...): ...
async def test_m5_row_order_module_resource_action_scope(...): ...

# Auth
async def test_a1_no_jwt_401(...): ...

# Hidden fields
async def test_h1_role_response_hides_audit_actors(...): ...
async def test_h2_permission_response_no_audit_actor_fields(...): ...
```

---

### File 17: `data/ithina_dev_seed_data.xlsx` — modify

Two edits in one binary commit:

1. **Append 25 rows to `lookups` sheet.** Same column shape as existing rows. Values per File 2's `LOOKUP_ROWS` constant.

2. **Delete legacy permission rows from `permissions` sheet.** Any row whose `module` is `ROOS` or `GOAL_CONSOLE`, or whose `scope` is `REGION`. The DDL cleanup migration (File 1) deletes these from the DB; the Excel must mirror so `--reset` runs don't re-introduce them.

After editing, run `python -m scripts.seed_dev_data --dry-run` to confirm the loader accepts the new shape.

---

### File 18: `docs/endpoints/rbac.md` — new

8-section format × 4 endpoints in one file. Mirror `docs/endpoints/tenant-users.md` for shape.

For E6, the section 7 TypeScript snippet should show the render pattern:

```ts
// Permission matrix render — direct iteration, no lookups.
const matrix = await fetch('/api/v1/permission-matrix').then(r => r.json());

// Headers: one <th> per role column.
{matrix.roles.map(role => <th>{role.name}</th>)}

// Body: one <tr> per permission row.
{matrix.rows.map(row => (
  <tr>
    <td>
      <div>{row.resource_label}</div>
      <div>
        {row.module_label}{' '}
        <Chip color={chipColorFor(row.action)}>{row.action_label}</Chip>
        {' · '}{row.scope_label}
      </div>
    </td>
    {row.cells.map((checked, i) => (
      <td key={matrix.roles[i].id}>
        <Checkbox checked={checked} disabled />
      </td>
    ))}
  </tr>
))}
```

Behaviour-notes paragraph capturing the locked decisions.

---

### File 19: `BUILD_PLAN.md` — modify

Step 6.1 entry rewritten. Status: TODO → DONE in same commit.

```markdown
## Step 6.1 — RBAC read endpoints (Roles + Permissions + Permission Matrix)

**Status.** DONE
**Owner.** CLAUDE_CODE

**Note on scope narrowing.** Original entry called for 5 endpoints
including a list of user_role_assignments. A frontend-locked design
review (2026-05-04) narrowed scope to 4 endpoints — the assignments-
side is captured as forward notes (see "Known follow-ups (RBAC)"
sub-section below).

**Goal.** Roles & Permissions page (Frontend spec 7.5) becomes
data-driven. Both the Role catalog tab and the Permission matrix tab
render from these endpoints.

**Scope in (as shipped).**
- 2 migrations: DDL enum cleanup (narrows module_enum to 4 values
  and permission_scope_enum to 3 values; deletes legacy seed rows;
  forward-only) + lookups seed (25 rows for the 4 enum display
  label categories: 4 module + 12 resource + 6 action + 3 scope).
- 3 ORM models: Role, Permission, RolePermission. UserRoleAssignment
  deferred per FN: E4/E5.
- Schemas covering E1's pre-grouped response, E2's flat list, E3's
  parent-echo, E6's render-ready matrix.
- 3 Repos: RolesRepo, PermissionsRepo, PermissionMatrixRepo.
- 1 router (rbac.py) with 4 endpoints across 3 APIRouter prefixes
  (/roles, /permissions, /permission-matrix).
- App-layer audience filter pattern: TENANT JWTs see only
  audience='TENANT' rows on E1 (platform_roles block returns empty),
  E3 (404 for cross-audience id), and E6 (roles array filtered).
  Distinct from RLS — codified as a new convention note in CLAUDE.md.
- 1 new error class: RoleNotFoundError (404). InvalidSortKeyClientError
  reused from Step 5.2.
- 3 conftest factories (raw-SQL-INSERT pattern).
- 23 integration tests; 5 load-bearing (R2 audience block; R4
  .correlate scoping; RP3 audience-gated 404; M2 matrix alignment;
  M3 tenant filter on matrix).
- Excel seed update: 25 rows appended to lookups sheet; legacy
  permission rows (module='ROOS'/'GOAL_CONSOLE' or scope='REGION')
  removed from permissions sheet to match DDL cleanup.
- docs/endpoints/rbac.md (8-section × 4 endpoints in one file).

**Scope out.**
- Permission resolution endpoint (post-v0).
- Write endpoints (FN-AB-12).
- AI-RBAC-01 through AI-RBAC-06 enforcement (write-time).
- RBAC-driven authorisation in handlers (post-v0).
- Custom-role creation flow (FN-AB-06).

**Acceptance criteria (met).**
- 4 endpoints live; ~155-160 pytest passes (138 prior + 23 new);
  mypy strict clean; check_setup 35/35; smoke test unchanged at
  74 PASS (no new RLS surface).
- Both migrations applied; lookups round-trip clean (DDL cleanup
  is forward-only and documented as such).
- Migration applied to Cloud SQL dev DB post-merge (HUMAN-coordinated).
- All 5 load-bearing tests explicitly green.
- docs/endpoints/rbac.md follows tenant-users.md's 8-section
  structure; OpenAPI spec at docs/endpoints/openapi.json shows all
  4 endpoints with rich descriptions.

**Known follow-ups (RBAC).**

The following were considered for Step 6.1 and deliberately deferred.
Each carries its own landing trigger; do not implement until the
trigger fires.

- **A1 / A2: User-resource augmentation.** Inline `roles[]` array on
  GET /api/v1/tenant-users and GET /api/v1/platform-users responses.
  Each user's active assignments embedded with role_name, role_code,
  org_node_id, org_node_name, status, granted_at, assignment_id. Plus
  query params `?role_id=X` and `?org_node_id=X` on both endpoints.
  Append-only per D-31. Lands as a dedicated amendment to Step 5.1 /
  5.2 before the Users page integration goes live in dev. Frontend
  dev locks the `roles[]` shape against Users page render needs.

- **E4: GET /api/v1/user-role-assignments (list).** With filters
  ?role_id, ?platform_user_id, ?tenant_user_id, ?tenant_id,
  ?org_node_id, ?status. RLS-isolated per D-29's IS-NULL-gated
  OR-clause. Lands when first of: org-tree node-delete impact UI
  (post-v0 write surface) needs ?org_node_id=X; role catalog
  "click user_count to see which users" drill-down exceeds A1's
  ?role_id=X coverage; audit log assignment-by-anchor query gets
  specced.

- **E5: GET /api/v1/user-role-assignments/{id} (single-fetch).**
  RLS-as-404 for cross-tenant probes per D-17. Lands when first of:
  Step 6.2 audit log drawer needs a live-state panel (vs snapshot-
  only); user detail drawer adds "click assignment chip → expand"
  lifecycle panel.

  The UserRoleAssignment ORM model and UserRoleAssignmentsRepo land
  with the first of E4/E5, NOT in Step 6.1.

- **MODULES-EXT: Module enum extension for ROOS and GOAL_CONSOLE.**
  Both are real product surfaces but their place in the RBAC module
  hierarchy is not yet decided. Lands when first of: ROOS or Goal
  Console gets a dedicated frontend page in the admin console;
  product team confirms whether they're top-level modules or
  sub-features of an existing module. Migration is additive
  (`ALTER TYPE module_enum ADD VALUE`) plus 1-2 lookup rows; no
  enforcement impact since v0 is read-only.

- **RESOURCES-EXT: Resource enum extension for MODULE_ACCESS,
  GUARDRAILS, APPROVALS.** Frontend spec 7.6/7.7 describes pages
  for these but their RBAC gating is post-v0. v0 ships without
  permissions targeting these resources; the matrix has narrower
  rows, matching v0's narrower enforcement reality. Lands when
  Module Access / Guardrails / Approvals page gating ships. Each
  is one ALTER TYPE ADD VALUE migration plus one lookup row.

**Coordination.**
- Frontend integrates dev within 24 hours.
- Cloud SQL dev migration run post-merge (HUMAN).
```

---

### File 20: `CLAUDE.md` — modify

- **Current state → Completed:** Step 6.1 bullet covering the 3 ORM models, schemas, 3 repos, 1 router with 4 endpoints, the audience-filter pattern, the load-bearing tests, the doc, the two migrations.
- **Schema state line:** unchanged at 11 application tables. Smoke count unchanged at 74. Note added: "DDL enum cleanup migration is forward-only — legacy permission rows deleted, downgrade raises NotImplementedError per project convention for irreversible structural cleanup."
- **Append a one-line audience-filter convention note** in the Code conventions section, after the v0 auth model subsection:
  > **Audience filtering for non-RLS tables (Step 6.1).** When a platform-global table has rows that must be visibility-segmented by user_type AT THE APPLICATION LAYER (e.g., `roles` where TENANT JWTs see only audience='TENANT'), apply the filter in the router via a small helper (`_audience_filter_for(auth)`) that returns the filter value or None, then thread it through the Repo as an optional argument. Distinct from RLS-driven scoping (DB layer) but follows the same intent. Future non-RLS tables that need user-type-based visibility (none expected in v0) follow this pattern.
- **No new D-XX entries.**
- **No new FN-AB entries.** RBAC-specific deferrals live in BUILD_PLAN's Step 6.1 "Known follow-ups (RBAC)" sub-section, not promoted to global forward notes.

---

### File 21: `docs/architecture.md` — likely no-edit

If the doc names Repos by example, add `RolesRepo`, `PermissionsRepo`, `PermissionMatrixRepo`. Otherwise skip the file.

---

### File 22: `prompts/step-6_1-rbac-read-endpoints-2026-05-04.md` — new

This prompt file. Bundled per the per-step convention.

---

### File 23: `docs/endpoints/openapi.json` — re-export

After all code is in and tests pass:

```bash
curl -s http://localhost:8000/api/v1/openapi.json | jq '.' > docs/endpoints/openapi.json
cat docs/endpoints/openapi.json | jq '.paths | keys' \
  | grep -E 'roles|permissions|permission-matrix'
# Expected: /api/v1/roles, /api/v1/roles/{role_id}/permissions,
# /api/v1/permissions, /api/v1/permission-matrix.
```

---

## Testing and regression discipline

### New tests added by this step

23 integration tests. **Five load-bearing**:
- **R2** TENANT JWT empty platform_roles block (audience filter on E1).
- **R4** user_count correlated subquery scoping (third occurrence of the L9 lesson).
- **RP3** TENANT JWT requesting PLATFORM role → 404 (audience gate on E3).
- **M2** cells/roles position alignment (E6 invariants M1/M2).
- **M3** TENANT JWT filters matrix columns (E6 invariant M5).

### Tests deliberately not added

- "RLS isolation on user_role_assignments." Already proven at smoke test (Step 2.2b) and end-to-end via Step 5.2's T9. The user_count subquery's RLS scoping is implied by R4 + smoke test.
- "AI-RBAC-01 through AI-RBAC-06 invariants." Write-time concerns; this step is read-only.
- "Cloud SQL migration succeeds." Production-ops concern; covered by the standard migration runbook.

### Regression risk surface

1. **`.correlate(Role)` on user_count subquery.** Same trap as L9/L11. R4 catches.
2. **Audience filter on E1, E3, E6.** Three separate enforcement points. R2/RP3/M3 each cover one.
3. **DDL enum cleanup migration data loss.** Permission rows with `module='ROOS'/'GOAL_CONSOLE'` or `scope='REGION'` are deleted. Mirror in seed Excel; Step 6.3 reseeds with canonical catalogue.
4. **Display label JOIN to lookups.** If a permission's enum value has no matching `lookups` row, the label will be NULL. Defensive: fall back to the code itself in the JOIN. Tested by M4.
5. **Cells/roles alignment on E6.** If repo loads roles in a different order than the position assumed by cells[], the matrix renders garbage. M2 enforces alignment as the primary invariant.
6. **`actor_user_type_enum` reuse.** Step 5.2 already declared. The 3 new RBAC ORM models reference it; redeclaring would cause `create_type=True` errors.
7. **Lookup migration idempotency.** ON CONFLICT (list_name, code) DO NOTHING required. Verifies migration can re-run safely.
8. **Excel seed shape.** Row count + column shape must remain compatible with the existing loader's column mapping. Verify with `--dry-run`.

### Verification harness (run all six; all must be green)

```bash
# 1. Full pytest suite — total count must be (prior_count + 23 new RBAC tests).
#    Capture and compare:
PRIOR=$(grep -c "^def test_\|^async def test_" tests/integration/test_*.py 2>/dev/null | head -1 || echo "138")
uv run pytest -v

# 2. Per-resource regression checkpoint (LOAD-BEARING).
#    Re-run every previously-shipped router's integration tests in isolation.
#    Each must report 100% PASS with the exact same count as pre-step.
#    A regression in any of these is a step-blocker — do not commit.
uv run pytest tests/integration/test_tenants_router.py -v
uv run pytest tests/integration/test_platform_users_router.py -v
uv run pytest tests/integration/test_tenant_users_router.py -v
uv run pytest tests/integration/test_org_tree_router.py -v
uv run pytest tests/integration/test_lookups_router.py -v

# Expected count per file (anchor against current state — adjust if Step 5.3
# or other recent steps have changed these):
#   test_tenants_router.py        — 22 tests (Step 3.3 baseline)
#   test_platform_users_router.py — 10 tests (Step 5.1)
#   test_tenant_users_router.py   — 13 tests (Step 5.2)
#   test_org_tree_router.py       — TBD (Step 5.3 — verify pre-step count)
#   test_lookups_router.py        — 4 tests (Step 3.6)
# If any file's PASS count drops by even 1, surface immediately and STOP.
# A regression here means an existing endpoint's contract or behaviour
# was changed by this step's work — must be diagnosed before commit.

# 3. mypy strict
uv run mypy --strict src/admin_backend

# 4. Pre-flight checker
./scripts/check_setup.sh

# 5. Both migrations apply cleanly
uv run alembic upgrade head
uv run alembic check  # autogenerate-clean

# 6. Manual curl verification
PJWT=$(uv run python -c "from admin_backend.auth.testing import make_test_jwt; print(make_test_jwt(user_type='PLATFORM'))")
TJWT=$(uv run python -c "from admin_backend.auth.testing import make_test_jwt; print(make_test_jwt(user_type='TENANT', tenant_id='972a8469-1641-4f82-8b9d-2434e465e150'))")

# E1: PLATFORM gets pre-grouped 3+12; TENANT gets 0+12
curl -s -H "Authorization: Bearer $PJWT" /api/v1/roles | jq '.platform_roles.total, .tenant_roles.total'  # → 3, 12
curl -s -H "Authorization: Bearer $TJWT" /api/v1/roles | jq '.platform_roles.total, .tenant_roles.total'  # → 0, 12

# E2: total > 0 and < 100 for both user types
curl -s -H "Authorization: Bearer $PJWT" /api/v1/permissions | jq '.pagination.total'

# E3: PLATFORM JWT to PLATFORM role works; TENANT JWT to PLATFORM role 404
SUPER_ID=$(psql $DATABASE_URL -tAc "SELECT id FROM roles WHERE code='SUPER_ADMIN'")
curl -s -H "Authorization: Bearer $PJWT" /api/v1/roles/$SUPER_ID/permissions | jq '.role_name'  # → "Super Admin"
curl -s -H "Authorization: Bearer $TJWT" /api/v1/roles/$SUPER_ID/permissions | jq '.code'  # → "ROLE_NOT_FOUND"

# E6: PLATFORM 15 columns; TENANT 12 columns; cells aligned
curl -s -H "Authorization: Bearer $PJWT" /api/v1/permission-matrix \
  | jq '.roles | length, .rows | length, .rows[0].cells | length'  # → 15, <N>, 15
curl -s -H "Authorization: Bearer $TJWT" /api/v1/permission-matrix \
  | jq '.roles | length, .rows | length, .rows[0].cells | length'  # → 12, <N>, 12

# E6: display labels populated from lookups
curl -s -H "Authorization: Bearer $PJWT" /api/v1/permission-matrix \
  | jq '.rows[0] | {module, module_label, action, action_label}'
# → {"module": "ADMIN", "module_label": "Admin", "action": "AUDIT", "action_label": "Audit"}
# (or whatever the first row's enum values resolve to)
```

If any leg is not green, **report the failure rather than the step.** The per-resource regression checkpoint (#2) is especially load-bearing: a failure there means an existing endpoint's behaviour was inadvertently changed by this step's work. Diagnose the regression and fix or revert before proceeding.

---

## Scope out

- **Permission resolution endpoint** (`/me/permissions/check`). Post-v0.
- **Write endpoints** (FN-AB-12).
- **A1 / A2** (inline roles[] on user resources). Forward note.
- **E4** (assignments list). Forward note.
- **E5** (assignment single-fetch). Forward note.
- **MODULES-EXT** (ROOS / GOAL_CONSOLE module enum extension). Forward note.
- **RESOURCES-EXT** (MODULE_ACCESS / GUARDRAILS / APPROVALS resource enum extension). Forward note.
- **AI-RBAC-01 through AI-RBAC-06.** Write-time invariants.
- **Custom-role creation flow.** FN-AB-06.
- **Single-permission detail endpoint** (`/permissions/{id}`). No UI consumer.
- **Audience filter on E2 (permissions catalogue).** Catalogue is reference data.
- **Pagination on E3 and E6.** Bounded responses.

---

## Stop and ask if

1. **Seed Excel's `permissions` sheet** (Pre-flight item 17 surfaces) cannot be cleanly cleaned because the loader requires non-empty data or the column shape is rigid. Surface and we'll decide whether to (a) clean the rows in this step's Excel commit, (b) leave Excel as-is and rely on `--reset` failing fast on legacy rows, or (c) defer Excel cleanup to Step 6.3.
2. **Migration head is not `0644a4186e48`**. Surface and we'll rebase.
3. **The `actor_user_type_enum` reuse pattern in Step 5.2's ORM** doesn't match what Files 3-5 assume. Surface; mirror the existing convention exactly.
4. **Lookups Excel sheet validation fails** after appending 25 rows (`python -m scripts.seed_dev_data --dry-run`). Surface the error.
5. **The `_load_permissions_with_labels` LEFT JOIN approach in File 12** doesn't fit cleanly with the existing Repo conventions. Surface; alternative is raw SQL `text()` plus row-to-dict conversion.
6. **OpenAPI generates with non-standard schema names** for the new pre-grouped E1 response. Verify the spec is consumable by Amit's frontend codegen; surface if anything looks wrong.
7. **The frontend dev surfaces additional sort keys** for E1 (e.g., `user_count_desc` to find most-assigned roles) before the step ships. Surface; we'll extend SORT_MAP additively.
8. **Cloud SQL dev migration sequencing.** Local migration applies cleanly but Cloud SQL has different state (e.g., the wrong DDL was applied earlier). Surface; HUMAN-coordinated cleanup.
9. **The user_role_assignments ORM model is needed transitively** by the user_count correlated subquery and there's no clean way to reference the table without a model. Surface; either (a) declare a lightweight stub similar to Step 3.3's pattern, or (b) use raw `text()` SQL for the subquery only.

---

## Acceptance criteria

- 23 files created/modified per scope above.
- 3 new ORM models (Role, Permission, RolePermission). `Base.metadata` includes the 3 tables; `uv run alembic check` produces no autogenerate proposals after both migrations applied.
- 4 endpoints live and routed under `/api/v1/`.
- For seed-loaded data (after both migrations + Excel cleanup):
  - `GET /api/v1/roles` (PLATFORM): `{platform_roles.total: 3, tenant_roles.total: 12}`.
  - `GET /api/v1/roles` (TENANT-Buc-ee's): `{platform_roles.total: 0, tenant_roles.total: 12}`.
  - `GET /api/v1/permissions` (either): non-zero, bounded by surviving seed rows post-cleanup.
  - `GET /api/v1/roles/{owner_id}/permissions`: bounded items, ordered by module/resource/action/scope; `role_id` and `role_name` echoed.
  - `GET /api/v1/roles/{super_admin_id}/permissions` from TENANT JWT: 404 ROLE_NOT_FOUND.
  - `GET /api/v1/permission-matrix` (PLATFORM): roles=15, rows=non-zero, cells[0].length=15.
  - `GET /api/v1/permission-matrix` (TENANT): roles=12, rows=non-zero, cells[0].length=12.
  - All 4 display label fields present and non-NULL on every E6 row.
- 5 load-bearing tests (R2, R4, RP3, M2, M3) explicitly green.
- All 23 new integration tests pass.
- All existing pytest still pass. Expected new pytest count: ~155-160.
- **Per-resource regression checkpoint passes:** `test_tenants_router.py`, `test_platform_users_router.py`, `test_tenant_users_router.py`, `test_org_tree_router.py`, and `test_lookups_router.py` each report 100% PASS at exactly their pre-step count (no test deletions, no test failures). A drop in any file's PASS count is a step-blocker.
- mypy strict clean.
- check_setup 35/35.
- Smoke test unchanged at 74 PASS.
- Both migrations applied cleanly to local DB. Cloud SQL dev migration scheduled post-merge (HUMAN-coordinated; not blocking step closure).
- `docs/endpoints/rbac.md` covers all 4 endpoints in 8-section format per endpoint.
- **OpenAPI spec quality:** all 4 endpoints with `summary`, `description`, response schemas with `description` on every field, error responses 400/401/404 referenced.
- Audit-actor columns hidden from response shapes (verified by H1/H2 tests).

---

## Report (BEFORE proposing commit)

Six bundles per the convention:

1. **Code:** files created with line counts; the 3 ORM models + PG enum references summary; manual curl outputs for all 4 endpoints with verified counts.
2. **CLAUDE.md updates:** Step 6.1 Completed bullet; the audience-filter convention note; no new D-XX or FN-AB.
3. **BUILD_PLAN.md updates:** Step 6.1 entry rewritten with "Known follow-ups (RBAC)" sub-section.
4. **architecture.md updates:** "no change" or specific edits if Repo names appear.
5. **OpenAPI spec snapshot:** `docs/endpoints/openapi.json` regenerated; verify all 4 paths present.
6. **Prompt file:** `prompts/step-6_1-rbac-read-endpoints-2026-05-04.md` confirmed in commit set.

Plus: pytest count delta (was 138, now ~155-160); **per-file regression numbers for `test_tenants_router.py`, `test_platform_users_router.py`, `test_tenant_users_router.py`, `test_org_tree_router.py`, `test_lookups_router.py` — confirm each at 100% PASS with no count drop**; mypy status; check_setup; both migrations applied; Excel update verified by `--dry-run`; Cloud SQL dev migration scheduled.

Wait for explicit authorisation before staging or committing.

---

## After completing

Propose a git commit per CLAUDE.md "After completing a task" Pattern A:

```
git status
git add -A
git commit -m "Step 6.1: RBAC read endpoints (roles, permissions, permission-matrix)

- 2 migrations: DDL enum cleanup narrowing module_enum (drop ROOS,
  GOAL_CONSOLE) and permission_scope_enum (drop REGION) — forward-
  only; resource_enum and action_enum already match locked vocabulary
- 25-row lookups seed for permission display labels (4 module +
  12 resource + 6 action + 3 scope)
- 3 ORM models (Role, Permission, RolePermission); UserRoleAssignment
  deferred per FN: E4/E5
- 4 endpoints: /roles (pre-grouped by audience), /permissions,
  /roles/{id}/permissions (parent-echo), /permission-matrix (render-
  ready grid with cells[]/roles[] position alignment)
- Audience-filter pattern: TENANT JWTs see audience='TENANT' on E1, E3,
  E6 (app-layer, distinct from RLS); codified as new convention note
- New error class: RoleNotFoundError; reuse InvalidSortKeyClientError
- 3 conftest factories (raw-SQL-INSERT)
- 23 integration tests; 5 load-bearing (R2 audience block; R4
  .correlate scoping; RP3 audience-gated 404; M2 matrix alignment;
  M3 tenant filter)
- Excel seed update: 25 rows added to lookups; legacy permission rows
  (ROOS/GOAL_CONSOLE module or REGION scope) removed from permissions
- docs/endpoints/rbac.md (8-section × 4 endpoints in one file)
- BUILD_PLAN 6.1 rewritten: scope narrowed; Known follow-ups (RBAC)
  captures A1/A2/E4/E5/MODULES-EXT/RESOURCES-EXT deferrals with
  landing triggers"
```

Ask user "Run? yes / no / edit message". On yes, execute via bash tool. On no, skip. On edit, prompt for new message.

---

## End of prompt
