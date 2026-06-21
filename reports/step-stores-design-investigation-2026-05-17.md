# Stores resource — code-state investigation (read-only)

Date: 2026-05-17. HEAD: commit `66e79b0` (`Step 6.13: org-tree mutations + Phase 3b seed update + tests + smoke + docs + retro`). Investigation scope: reconnaissance for the upcoming `/stores` design conversation. No source edits, no migrations, no seed runs, no full pytest.

## Headline

The full Stores stack is absent in code today. There is no `models/store.py`, no `schemas/store.py`, no `repositories/stores.py`, no `routers/v1/stores.py`, no `/stores` paths in main.py's `include_router` list, no `get_store_anchor` in `auth/anchor_deps.py`, no Store-named class in `errors.py`, no Python `StoreStatus` / `TaxTreatment` enum anywhere under `src/admin_backend/`. The lightweight `Store` ORM stub at `models/_lightweight_stubs.py:39-46` (id + tenant_id only) remains the only Python entity bound to the `stores` table, consumed by exactly one import site (`repositories/tenants.py:53`) for the per-tenant `num_stores` correlated subquery (`num_stores_*` sort keys + stats counts) and by raw `text()` SQL in `repositories/dashboard.py:154-158` for the dashboard fleet-stats CTE. The `core.stores` table exists in the live DB (alembic head `a0982a86985b`, RLS+FORCE enabled, policy carries the D-27 NULLIF wrapper AND the D-29 unconditional PLATFORM OR-branch) per the v0 migrations, with 25 seeded rows across 7 tenants — all `status=ACTIVE`, none `closed_at`-populated. The catalogue carries 2 STORES permission rows (`ADMIN.STORES.VIEW.TENANT` and `ADMIN.STORES.CONFIGURE.TENANT`, granted only to SUPER_ADMIN). `PermissionResource.STORES` is in the Python enum at `models/permission.py:62`. No tests, smoke entries, endpoint doc, or OpenAPI paths reference a stores resource.

## Code-state classification

| Artifact | State | Citation |
|---|---|---|
| `src/admin_backend/models/store.py` | ABSENT | path checked: `models/` enumerated; only `lookup.py`, `org_node.py`, `permission.py`, `platform_user.py`, `platform_user_role_assignment.py`, `role.py`, `role_permission.py`, `tenant.py`, `tenant_module_access.py`, `tenant_user.py`, `tenant_user_role_assignment.py`, `_lightweight_stubs.py`, `__init__.py` |
| `src/admin_backend/models/_lightweight_stubs.py::Store` | PRESENT (stub, 2 columns) | `models/_lightweight_stubs.py:39-46` |
| `src/admin_backend/schemas/store.py` | ABSENT | path checked: `schemas/` enumerated; no store file |
| `src/admin_backend/repositories/stores.py` | ABSENT | path checked: `repositories/` enumerated; no stores file |
| `src/admin_backend/routers/v1/stores.py` | ABSENT | path checked: `routers/v1/` enumerated; no stores file |
| `main.py` include of stores router | ABSENT | `main.py:30-39` (imports) + `main.py:192-226` (include_router calls); zero `stores` references |
| `auth/anchor_deps.py::get_store_anchor` | ABSENT | `auth/anchor_deps.py:38-148`; only `get_tenant_anchor`, `get_org_node_anchor`, `get_tenant_user_anchor` defined |
| `errors.py` Store-named class | ABSENT | `errors.py` grep returns zero `Store` hits (only `STORE` in unrelated `org_node` cascade docstring at lines 400-401) |
| `StoreStatus` / `TaxTreatment` Python enum | ABSENT | repo-wide grep on `StoreStatus\|TaxTreatment\|store_status_enum\|tax_treatment_enum` in `src/admin_backend/` returns zero hits |
| `core.stores` live table | PRESENT | psql `pg_class` confirms `relrowsecurity=t, relforcerowsecurity=t` |
| `db/raw_ddl/Ithina_postgres_SQL_DDL_stores_v5.sql` | PRESENT | listed in `db/raw_ddl/` |
| Seed Excel `stores` sheet | PRESENT | 25 rows; sheet name confirmed in `data/ithina_dev_seed_data.xlsx` |
| Permission catalogue STORES rows | PRESENT (2 rows) | seed Excel `permissions` `_key=p28,p29` |
| `PermissionResource.STORES` Python enum value | PRESENT | `models/permission.py:62` |
| Test files matching `*store*` | ABSENT | `find tests -iname "*store*"` returns zero |
| `tests/integration/conftest.py::make_store` | PRESENT | `tests/integration/conftest.py:484-547` (raw-SQL INSERT, returns lightweight stub instance) |
| `docs/endpoints/stores.md` | ABSENT | `docs/endpoints/` enumerated; no stores file |
| `/stores` paths in `docs/endpoints/openapi.json` | ABSENT | JSON parsed; zero paths containing `store` |

---

## ARTIFACT — Presence/absence of code artifacts for stores

### F-ARTIFACT-1: `models/store.py` absent; only a 2-column stub exists

**Citation:** absent — path `src/admin_backend/models/store.py` does not exist; consumed-by-stub at `src/admin_backend/models/_lightweight_stubs.py:39-46`.

**Current code:**

```python
class Store(Base):
    """Lightweight stub for ``stores`` (full model at Step 4.5)."""

    __tablename__ = "stores"
    __table_args__ = {"schema": _DB_SCHEMA}

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
```

The stub declares only `id` and `tenant_id`. No `server_default`, no `FetchedValue`, no PG enum binding for `store_status_enum` or `tax_treatment_enum`. The module-level docstring (`_lightweight_stubs.py:12`) names "Step 4.5" as the cleanup point.

**Observation:** No SQLAlchemy ORM model for `stores` exists in the codebase. The stub is sufficient for the two consuming queries (a `COUNT(Store.id)` subquery and a `COUNT() select_from(Store)` aggregate) but maps none of the other 18 columns the live `stores` table carries. `Base.metadata` is incomplete relative to the live schema for this table.

**Confidence:** high

### F-ARTIFACT-2: `schemas/store.py` absent

**Citation:** absent — `src/admin_backend/schemas/` contains `dashboard.py`, `lookup.py`, `me.py`, `modules_access.py`, `org_node.py`, `permission.py`, `platform_user.py`, `role.py`, `role_assignment.py`, `tenant.py`, `tenant_user.py`, `__init__.py`. No store file.

**Current code:** file not found.

**Observation:** No Pydantic schemas exist for the stores resource. `schemas/__init__.py` re-exports `StoresCard` (the dashboard KPI card schema at `schemas/dashboard.py:185-200`), but that is a dashboard card model, not a stores-resource schema.

**Confidence:** high

### F-ARTIFACT-3: `repositories/stores.py` absent

**Citation:** absent — `src/admin_backend/repositories/` contains `_errors.py`, `dashboard.py`, `lookups.py`, `modules_access.py`, `org_nodes.py`, `permission_matrix.py`, `permissions.py`, `platform_users.py`, `role_assignments.py`, `roles.py`, `tenant_users.py`, `tenants.py`, `__init__.py`. No stores file.

**Current code:** file not found.

**Observation:** No Repository class exists for the stores resource. The two live queries against `core.stores` live elsewhere: `TenantsRepo` uses the ORM stub for `num_stores` correlated subqueries (`repositories/tenants.py:306-308, 372-374`, `count_for_stats` at `tenants.py:422-425`); `DashboardRepo` uses raw `text()` SQL within its fleet-stats CTE (`repositories/dashboard.py:154-159`).

**Confidence:** high

### F-ARTIFACT-4: `routers/v1/stores.py` absent

**Citation:** absent — `src/admin_backend/routers/v1/` contains `dashboard.py`, `lookups.py`, `me.py`, `modules_access.py`, `org_tree.py`, `platform_users.py`, `rbac.py`, `role_assignments.py`, `tenant_users.py`, `tenants.py`, `__init__.py`. No stores file.

**Current code:** file not found.

**Observation:** No HTTP router exists for `/stores`. No endpoint by any verb (GET, POST, PATCH, DELETE) addresses the stores resource directly.

**Confidence:** high

### F-ARTIFACT-5: Zero stores router references in `main.py`

**Citation:** absent — grep `-i "store"` against `main.py` returns zero hits. Router imports live at `main.py:30-39` (10 routers imported); `app.include_router` calls live at `main.py:192-226` (12 include calls covering the 10 routers plus rbac's 3 sub-routers).

**Current code:** no store-related lines.

**Observation:** No stores router would be reachable even if a router file existed. The application has no `/api/v1/stores` mount point.

**Confidence:** high

### F-ARTIFACT-6: Anchor dep coverage

**Citation:** `src/admin_backend/auth/anchor_deps.py:38-148`.

**Current code:** three functions defined.

| Function | Signature | Lookup |
|---|---|---|
| `get_tenant_anchor` | `(tenant_id: UUID, session) -> str` (raises `TenantNotFoundError`) | `SELECT path FROM core.org_nodes WHERE tenant_id=:id AND node_type='TENANT' AND parent_id IS NULL` |
| `get_org_node_anchor` | `(tenant_id: UUID, node_id: UUID, session) -> str` (raises `OrgNodeNotFoundError`) | `SELECT path FROM core.org_nodes WHERE tenant_id=:id AND id=:node_id` |
| `get_tenant_user_anchor` | `(user_id: UUID, session) -> str` (raises `TenantUserNotFoundError`) | JOIN tenant_users → org_nodes on `(tenant_id, node_type='TENANT', parent_id IS NULL)` |

**Observation:** Three resources (tenants, org_nodes, tenant_users) have anchor coverage. Stores has none. No anchor dep takes a `store_id` or returns the path of a `STORE`-typed org_node, nor any path via `stores.org_node_id`. The module-level docstring notes a security invariant — anchor deps RAISE on miss; returning `None` would short-circuit `has_permission`'s cascade clause to TRUE.

**Confidence:** high

### F-ARTIFACT-7: No Store-named class in `errors.py`

**Citation:** absent — `errors.py` grep on bare `Store` returns zero class-name hits. The only `STORE` token in the file is inside the org_node cascade-order docstring at lines 400-401 (`COUNTRY(3) -> REGION(4) -> STORE(5) -> DEPARTMENT(6)`).

**Current code:** no Store-named error class.

**Observation:** No `StoreNotFoundError`, no `DuplicateStoreCodeError`, no `StoreClosedError`, no transition-result wrapper. Any future stores write surface needs new error classes.

**Confidence:** high

### F-ARTIFACT-8: No Python `StoreStatus` / `TaxTreatment` enums

**Citation:** absent — repo-wide grep `StoreStatus\|TaxTreatment\|store_status_enum\|tax_treatment_enum` against `src/admin_backend/` returns zero hits.

**Current code:** no such enums.

**Observation:** The PG enums `store_status_enum` (OPENING/ACTIVE/INACTIVE/CLOSED) and `tax_treatment_enum` (EXCLUSIVE/INCLUSIVE) exist live but have no Python mirror. Any future ORM model or schema would need them declared.

**Confidence:** high

---

## DDL_SHAPE — Columns, enums, constraints

### F-DDL_SHAPE-1: Full column list for `core.stores`

**Citation:** `db/raw_ddl/Ithina_postgres_SQL_DDL_stores_v5.sql:80-212`.

**Current code:** 21 columns mapped in DDL order (and confirmed against live psql).

| Column | Type | Nullable | Default |
|---|---|---|---|
| id | UUID | NOT NULL | `uuidv7()` |
| tenant_id | UUID | NOT NULL | — |
| org_node_id | UUID | NULL | — |
| name | TEXT | NOT NULL | — |
| store_code | TEXT | NULL | — |
| country | TEXT | NOT NULL | — |
| timezone | TEXT | NOT NULL | — |
| address | TEXT | NULL | — |
| latitude | NUMERIC(9, 6) | NULL | — |
| longitude | NUMERIC(9, 6) | NULL | — |
| currency | CHAR(3) | NOT NULL | — |
| tax_treatment | tax_treatment_enum | NOT NULL | — |
| status | store_status_enum | NOT NULL | `'ACTIVE'` |
| created_at | TIMESTAMPTZ | NOT NULL | `NOW()` |
| created_by_user_id | UUID | NULL | — |
| created_by_user_type | actor_user_type_enum | NULL | — |
| updated_at | TIMESTAMPTZ | NOT NULL | `NOW()` |
| updated_by_user_id | UUID | NULL | — |
| updated_by_user_type | actor_user_type_enum | NULL | — |
| closed_at | TIMESTAMPTZ | NULL | — |
| closed_by_user_id | UUID | NULL | — |
| closed_by_user_type | actor_user_type_enum | NULL | — |

DDL header notes (lines 22-36): v5 adopted Pattern (b) audit-actor pairs (id + type, no FK); `ck_stores_closed_consistency` updated for the closed-pair; new `ck_stores_*_actor_pair` checks; "1:1 link" between stores and org_nodes via partial UNIQUE since v3; `store_code` uniqueness case-insensitive since v1.

**Observation:** 22 columns total (counting `id`). 3 Pattern (b) audit-actor pairs (created / updated / closed). `org_node_id` is nullable per DDL comment (lines 89-94): "Nullable in v4 to support store creation before tree placement; promote to NOT NULL once onboarding flow is stable." `store_code` is also nullable per DDL comment (lines 98-101) to allow "code not yet assigned" onboarding state. `address`, `latitude`, `longitude`, `closed_at`, `closed_by_user_*` are nullable. All others NOT NULL.

**Confidence:** high

### F-DDL_SHAPE-2: CHECK constraints on `core.stores`

**Citation:** live psql `pg_constraint` query against `core.stores`; mirror in `db/raw_ddl/Ithina_postgres_SQL_DDL_stores_v5.sql:166-211`.

**Current code:** 7 CHECK constraints (plus 2 FKs + 1 PK).

| Constraint | SQL expression |
|---|---|
| `ck_stores_name_length` | `length(name) BETWEEN 1 AND 200` |
| `ck_stores_country_format` | `length(country) BETWEEN 2 AND 100 AND country ~ '[A-Za-z]' AND country !~ '^[[:space:]]*$'` |
| `ck_stores_currency_format` | `currency ~ '^[A-Z]{3}$'` |
| `ck_stores_latitude_range` | `latitude IS NULL OR (latitude >= -90 AND latitude <= 90)` |
| `ck_stores_longitude_range` | `longitude IS NULL OR (longitude >= -180 AND longitude <= 180)` |
| `ck_stores_closed_consistency` | `(status='CLOSED' AND closed_at IS NOT NULL AND closed_by_user_id IS NOT NULL AND closed_by_user_type IS NOT NULL) OR (status!='CLOSED' AND closed_at IS NULL AND closed_by_user_id IS NULL AND closed_by_user_type IS NULL)` |
| `ck_stores_created_by_actor_pair` | `(created_by_user_id IS NULL AND created_by_user_type IS NULL) OR (created_by_user_id IS NOT NULL AND created_by_user_type IS NOT NULL)` |
| `ck_stores_updated_by_actor_pair` | `(updated_by_user_id IS NULL AND updated_by_user_type IS NULL) OR (updated_by_user_id IS NOT NULL AND updated_by_user_type IS NOT NULL)` |

**Observation:** Closed-consistency CHECK is the structural state-transition guard: a `CLOSED` row requires all three closed_* fields populated; a non-`CLOSED` row requires all three NULL. There is no INACTIVE-side consistency CHECK; only `CLOSED` carries an audit-pair invariant. Actor-pair CHECKs on created_by and updated_by; no actor-pair CHECK is needed on closed_by because the closed-consistency CHECK subsumes the pair invariant for that triplet.

**Confidence:** high

### F-DDL_SHAPE-3: Enum definitions

**Citation:** `store_status_enum` in `db/raw_ddl/Ithina_postgres_SQL_DDL_stores_v5.sql:63-68`. `tax_treatment_enum` and `actor_user_type_enum` in `db/raw_ddl/Ithina_postgres_SQL_DDL_shared_utilities_v1.sql:119-123` and `:137-141` respectively. Live values confirmed via psql `pg_enum`.

**Current code:**

```
store_status_enum: OPENING, ACTIVE, INACTIVE, CLOSED
tax_treatment_enum: EXCLUSIVE, INCLUSIVE
actor_user_type_enum: PLATFORM, TENANT
```

**Observation:** Three enums in use on `core.stores`. `store_status_enum` is local to the stores DDL; `tax_treatment_enum` and `actor_user_type_enum` are shared (both defined in `shared_utilities_v1.sql`; `actor_user_type_enum` is widely shared across audit-actor pairs).

**Confidence:** high

### F-DDL_SHAPE-4: RLS posture on `core.stores`

**Citation:** psql `pg_class` shows `relrowsecurity=t, relforcerowsecurity=t`. Live policy `stores_tenant_isolation` text:

```
USING:      (tenant_id = NULLIF(current_setting('app.tenant_id', TRUE), '')::uuid)
            OR (current_setting('app.user_type', TRUE) = 'PLATFORM')
WITH CHECK: same predicate
```

DDL form (lines 265-272) carried the original single-clause shape without the NULLIF wrapper or the OR-branch.

**Observation:** Live policy is the post-Step-3.0 unconditional D-29 OR-branch form WITH the D-27 NULLIF wrapper. `stores.tenant_id` is NOT NULL so the OR-branch is unconditional (no IS-NULL gate). FORCE RLS is enabled — the table owner can't bypass either. Default-deny when `app.tenant_id` is unset and `app.user_type != 'PLATFORM'`. The frozen-DDL convention applies: the DDL file shows the as-shipped initial schema; the live policy is a migration amendment (`e59f62d5037d` NULLIF wrapper + `21e2ad16303a` unconditional OR per the 6.8.1-and-earlier policy work).

**Confidence:** high

### F-DDL_SHAPE-5: UNIQUE constraints, indexes, FKs

**Citation:** live psql `pg_indexes` + `pg_constraint`; mirror in DDL `:215-240` (indexes) and `:152-164` (FKs).

**Current code:**

| Object | Type | Columns / Definition |
|---|---|---|
| `pk_stores` | PRIMARY KEY (unique index) | `(id)` |
| `uq_stores_org_node_id` | UNIQUE INDEX (partial) | `(org_node_id) WHERE org_node_id IS NOT NULL` — 1:1 store ↔ org_node |
| `uq_stores_tenant_store_code_lower` | UNIQUE INDEX (partial, case-insensitive) | `(tenant_id, lower(store_code)) WHERE store_code IS NOT NULL` |
| `ix_stores_tenant` | INDEX | `(tenant_id)` |
| `ix_stores_tenant_status` | INDEX | `(tenant_id, status)` |
| `fk_stores_tenant` | FOREIGN KEY | `(tenant_id) REFERENCES core.tenants(id) ON UPDATE RESTRICT ON DELETE RESTRICT` |
| `fk_stores_org_node_same_tenant` | FOREIGN KEY (composite) | `(tenant_id, org_node_id) REFERENCES core.org_nodes(tenant_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT` |

**Observation:** Two partial unique indexes (both predicate `WHERE col IS NOT NULL`). One global unique scope: at most one row per `org_node_id`. One tenant-scoped unique scope: case-insensitive `store_code` unique within a tenant. The composite FK on `(tenant_id, org_node_id)` to `org_nodes(tenant_id, id)` is the structural cross-tenant injection guard (matches the D-34 pattern used by `tenant_user_role_assignments`). Two btree indexes on `tenant_id` alone and on `(tenant_id, status)` — the latter supports filtered list queries. No FK on audit-actor user_id columns (Pattern (b) per D-13).

**Confidence:** high

---

## COUPLING — Existing references to stores in code

### F-COUPLING-1: All `store`/`stores` references in `src/admin_backend/` (filtered for the stores table or Store identifier specifically)

**Citation:** Repo-wide grep `-i "store"` in `src/admin_backend/`, then manually filtered to remove unrelated uses (`SINGLE_STORE` enum value, `OrgNodeType.STORE`, `STORE` permission scope, `number_of_stores`/`num_stores` on the tenants resource, `StoresCard` dashboard schema, generic English text like "stored").

**Current code (stores-table-bound references only):**

| Site | Line(s) | Caption |
|---|---|---|
| `models/_lightweight_stubs.py:42` | `__tablename__ = "stores"` | Stub binding to live table |
| `repositories/tenants.py:53` | `from admin_backend.models._lightweight_stubs import Store` | Sole stub-import site |
| `repositories/tenants.py:306-308` | `num_stores_subq = select(func.count(Store.id)).where(Store.tenant_id == Tenant.id)` | List correlated subquery |
| `repositories/tenants.py:372-374` | duplicate of above for `get_by_id_with_aggregates` | Detail correlated subquery |
| `repositories/tenants.py:422-425` | `select(func.count()).select_from(Store)` | Header stats aggregate |
| `repositories/dashboard.py:154-158` | `store_counts AS (SELECT COUNT(*) AS total, COUNT(DISTINCT country) AS countries FROM {schema}.stores)` | Schema-qualified raw SQL CTE |
| `repositories/dashboard.py:83-84` | `stores_total: int; stores_distinct_countries: int` | Dataclass row fields |
| `repositories/dashboard.py:170-171, 197-198` | SELECT projection and result mapping for `stores_total` / `stores_distinct_countries` | CTE consumers |
| `routers/v1/dashboard.py:75, 136, 200-203, 230, 242` | Dashboard handler consumes `StoresCard` schema | Card render path |
| `routers/v1/tenants.py:115-116, 174-175, 270-273` | Tenant CREATE/PATCH/stats body and response carry `number_of_stores` (a column on `core.tenants`, not the `stores` table) | Not a `stores` table reference |

Unrelated `STORE` tokens (org_node type, permission scope, tenant tier `SINGLE_STORE`, `OrgNodeType.STORE`, English prose) are not stores-table couplings and are excluded.

**Observation:** Two consumers of the `stores` live table in app code: `TenantsRepo` (via the lightweight ORM stub, 3 subquery sites) and `DashboardRepo` (via raw `text()` SQL, schema-qualified). No other modules touch the table. The stub's two columns (`id`, `tenant_id`) are exactly what those consumers reference; widening the stub or replacing it with a full ORM model would have to preserve both column names.

**Confidence:** high

### F-COUPLING-2: SQL referencing `stores` in Repo files

**Citation:** grep across `repositories/*.py`.

**Current code:**

| Repo | SQL form | Schema-qualified? |
|---|---|---|
| `repositories/tenants.py` | SQLAlchemy ORM `select(func.count(Store.id)).where(Store.tenant_id == Tenant.id)` (`:306-308`, `:372-374`) and `select(func.count()).select_from(Store)` (`:422-425`) | Yes — via `Store.__table_args__["schema"]` from the stub |
| `repositories/dashboard.py:154-158` | `FROM {schema}.stores` inside a raw `text()` CTE | Yes — `{schema}` is `get_settings().db_schema` interpolated per-call |

No unqualified `stores` references appear in app code today.

**Observation:** Both consumers follow the established CSD-03 raw-SQL convention (schema-qualified). The ORM stub path is structurally safe because the stub declares the schema. No drift risk surface for either site.

**Confidence:** high

### F-COUPLING-3: Schema files

**Citation:** grep across `schemas/*.py`.

**Current code:**

| File | Line | Reference |
|---|---|---|
| `schemas/__init__.py:12, 110` | `StoresCard` re-export | Dashboard KPI card schema |
| `schemas/dashboard.py:185-200, 268` | `class StoresCard(BaseModel)` and the `stores: StoresCard` field on the fleet-stats response | Dashboard card model |
| `schemas/tenant.py:57-58, 104, 120, 146-147, 149-150, 175-176, 258-259, 282, 307-308, 318` | `number_of_stores` / `number_of_stores_as_of_date` field declarations on TenantRead, TenantDetail, TenantCreateRequest, TenantPatchRequest, `total_stores` on `TenantsStatsResponse`, `num_stores` on tenant list items | All are `core.tenants` columns or aggregates, NOT stores-table fields |
| `schemas/org_node.py:151-153` | `stores: int` field on `OrgTreeStats` — count of returned org_nodes with `node_type='STORE'` | Org_node count, NOT a stores-table field |
| `schemas/modules_access.py:152` | `tier: Literal["ENTERPRISE", "MID_MARKET", "SMB", "SINGLE_STORE"]` | TenantTier enum value, NOT a stores reference |

**Observation:** The `StoresCard` schema is the only `stores`-table-shaped Pydantic model in the codebase; it carries `value: int`, `distinct_countries: int`, and a `sub_text: str`. All other references are either tenant-resource snapshot fields, org-node counts, or tenant tier enum values.

**Confidence:** high

### F-COUPLING-4: Store imports

**Citation:** grep on `from .*models.* import .*Store\|from admin_backend\.models import .*Store`.

**Current code:** one site.

```
src/admin_backend/repositories/tenants.py:53:
    from admin_backend.models._lightweight_stubs import Store
```

**Observation:** Exactly one import site for the `Store` ORM stub. `models/__init__.py` (29 re-exported names) does NOT include `Store`; consumers reach in through the underscore-prefixed `_lightweight_stubs` module directly. If the full ORM model lands, this import line is the only one that needs to change (or stay, switched to `from admin_backend.models import Store` once `__init__.py` re-exports it).

**Confidence:** high

---

## CATALOGUE — Permission rows for STORES in seed

### F-CATALOGUE-1: STORES rows in `permissions` sheet

**Citation:** seed Excel `data/ithina_dev_seed_data.xlsx`, `permissions` sheet, read-only inspection.

**Current code (2 rows):**

| `_key` | module | resource | action | scope | code | id |
|---|---|---|---|---|---|---|
| p28 | ADMIN | STORES | VIEW | TENANT | `ADMIN.STORES.VIEW.TENANT` | `c848f300-1ff6-4ef3-8692-973955bc111d` |
| p29 | ADMIN | STORES | CONFIGURE | TENANT | `ADMIN.STORES.CONFIGURE.TENANT` | `6f4f631b-1c91-406f-ac5e-b33e4428591d` |

Both rows landed via FN-AB-19's Step 6.8.2.1 operator-driven seed update (CLAUDE.md FN-AB-19 names p28/p29 explicitly).

**Observation:** Two STORES permission tuples in the catalogue, both ADMIN-module, both TENANT-scope, no GLOBAL-scope STORES permission, no STORE-scope STORES permission, no OVERRIDE or EXECUTE or APPROVE or AUDIT action on STORES.

**Confidence:** high

### F-CATALOGUE-2: Role holders of STORES permissions

**Citation:** seed Excel `role_permissions` sheet cross-referenced against `roles` sheet by id.

**Current code:**

- `ADMIN.STORES.VIEW.TENANT` (p28): held by Super Admin only (role_id `f10c718b-...`).
- `ADMIN.STORES.CONFIGURE.TENANT` (p29): held by Super Admin only.

**Observation:** Neither STORES permission is granted to Platform Admin, Support Admin, Owner, Store Manager, or any other role in the catalogue. STORES grants are SUPER_ADMIN-exclusive in v0 today; via the scope cascade in `has_permission` (Step 6.9.3.1), SUPER_ADMIN's `GLOBAL` grants on other tuples also satisfy `TENANT` and `STORE` checks for THOSE tuples, but no role outside SUPER_ADMIN currently passes an `ADMIN.STORES.*` gate.

**Confidence:** high

### F-CATALOGUE-3: `PermissionResource.STORES` in Python enum

**Citation:** `src/admin_backend/models/permission.py:49-63`.

**Current code:**

```python
class PermissionResource(str, Enum):
    """Mirrors ``resource_enum`` (locked vocabulary; no narrowing needed)."""

    PRICING_RULES = "PRICING_RULES"
    MARKDOWNS = "MARKDOWNS"
    EXPIRING_ITEMS = "EXPIRING_ITEMS"
    WASTE_LOG = "WASTE_LOG"
    DONATION_ROUTING = "DONATION_ROUTING"
    CAMPAIGNS = "CAMPAIGNS"
    USERS = "USERS"
    ROLES = "ROLES"
    AUDIT_LOG = "AUDIT_LOG"
    TENANTS = "TENANTS"
    STORES = "STORES"
    ORG_NODES = "ORG_NODES"
```

**Observation:** `STORES` is present in the Python enum, position 11 (index 10). Available for use in any `require(..., PermissionResource.STORES, ...)` gate factory call.

**Confidence:** high

---

## SHAPES — Patterns the codebase actually uses today

Neutral survey of analogous patterns. No "follow this one" framing.

### F-SHAPES-1: List + detail GET pairs by router

**Citation:** route inspection across `routers/v1/*.py`; prefixes set in each router's `APIRouter(prefix=...)`.

| Router | URL prefix | List handler | Detail handler | Gate on list | Gate on detail | Auth-tier mechanism | List envelope |
|---|---|---|---|---|---|---|---|
| tenants | `/tenants` | `list_tenants` `routers/v1/tenants.py:185-243` | `get_tenant` `:277-303` | `require(ADMIN, TENANTS, VIEW, GLOBAL)` (no audience) | `require(ADMIN, TENANTS, VIEW, TENANT, anchor_dep=get_tenant_anchor)` | Gate factory; list has no audience kwarg; detail uses anchor_dep | `{items, pagination}` |
| platform-users | `/platform-users` | `list_platform_users` `:196-248` | `get_platform_user` `:263-279` | `require(ADMIN, USERS, VIEW, GLOBAL)` | `require(ADMIN, USERS, VIEW, GLOBAL)` | Gate factory; no audience kwarg; GLOBAL gate effectively restricts to PLATFORM holders in v0 (per FN-AB-26 retirement) | `{items, pagination}` |
| tenant-users | `/tenant-users` | `list_tenant_users` `:196-257` | `get_tenant_user` `:273-290+` | `require(ADMIN, USERS, VIEW, TENANT)` (no audience; no anchor_dep on list) | `require(ADMIN, USERS, VIEW, TENANT, anchor_dep=get_tenant_user_anchor)` | Gate factory; multi-audience | `{items, pagination}` |
| org-tree (read) | (router has no prefix; routes prefix-themselves) | `get_org_tree` `routers/v1/org_tree.py:138-215` at `/tenants/{tenant_id}/org-tree`; `get_node_children` `:237-287` at `/tenants/{tenant_id}/org-nodes/{node_id}/children` | (same as list — tree view itself is the "detail" of a tenant) | `require(ADMIN, ORG_NODES, VIEW, TENANT, anchor_dep=get_tenant_anchor)` and `require(..., anchor_dep=get_org_node_anchor)` | (no separate handler) | Gate factory; multi-audience | E2 (`/org-tree`) returns flat singleton `{tenant_id, tenant_name, stats, tree}` (D-30 exception); E3 (`/children`) returns `{node_id, items, pagination}` (D-30 + parent-echo) |
| roles | `/roles` | `list_roles` `routers/v1/rbac.py:135-222` | `list_role_permissions` `:242-265` at `/{role_id}/permissions` (technically the role's permission catalogue, not a generic detail; a generic role detail is absent) | None — `/roles` is in `GATE_EXEMPT_PATHS` per FN-AB-30 | None — `/roles/{role_id}/permissions` likewise allowlisted | App-layer audience filter `_audience_filter_for(auth)` on the Repo | Pre-grouped `{platform_roles: {items, total}, tenant_roles: {items, total}}` (D-30 exception) |
| permissions | `/permissions` | `list_permissions` `routers/v1/rbac.py:284-333` | (none — catalogue endpoint is list-only) | None — allowlisted reference data | n/a | None | `{items, pagination}` |
| permission-matrix | `/permission-matrix` | matrix root GET `:339+` | (none) | None — allowlisted | n/a | None | Position-aligned `{roles, cells}` (D-30 exception) |
| dashboard | `/dashboard` | `fleet_stats` (GET `/fleet-stats`) `routers/v1/dashboard.py:235+`; `governance_stats` (GET `/governance-stats`) `:317+` | (none — pure stats endpoints) | `require(ADMIN, TENANTS, VIEW, TENANT)` on both | n/a | Gate factory; no audience kwarg | Card-shaped (D-30 exception) |
| modules-access | `/module-access` | `list_modules` (GET `/modules`) `routers/v1/modules_access.py:108-135`; `list_matrix` (GET `/matrix`) `:163-252` | (none) | `require(ADMIN, TENANTS, VIEW, TENANT)` on both | n/a | Gate factory; no audience kwarg | `/modules` returns flat `{items}` (no pagination — bounded card set); `/matrix` returns `{items, pagination}` |
| role-assignments | `/role-assignments` | `list_role_assignments` `routers/v1/role_assignments.py:260-365+` | (none) | `require(ADMIN, USERS, VIEW, TENANT)` | n/a | Gate factory; no audience kwarg; in-handler TENANT-JWT short-circuit on platform-side block (security-load-bearing per locked decision 12) | Pre-grouped `{platform_assignments: {items, pagination}, tenant_assignments: {items, pagination}}` (D-30 exception) |
| lookups | `/lookups` | `list_lookups` `routers/v1/lookups.py:49+` | (none) | None — allowlisted reference data | n/a | None | `{lookups: {list_name: [items], ...}}` (batch-by-key envelope) |
| me | `/me` | `get_permissions` `/permissions`, `can_do` `/can-do` | (none) | None — caller-state path, allowlisted | n/a | None | `/permissions` returns `{permissions: [...]}`; `/can-do` returns `{allowed, reason_code}` (single-object) |

**Confidence:** high

### F-SHAPES-2: POST / PATCH / state-transition endpoints

**Citation:** route inspection across `routers/v1/*.py`.

| URL pattern | Verb + path | Gate tuple | Anchor dep | Audience kwarg | Errors raised in handler | Success status |
|---|---|---|---|---|---|---|
| Flat `/tenants` | `POST /tenants` | `(ADMIN, TENANTS, CONFIGURE, GLOBAL)` | none | `"PLATFORM"` | `DuplicateTenantNameError` (via Repo) | 201 |
| Flat `/tenants/{id}` | `PATCH /tenants/{tenant_id}` | `(ADMIN, TENANTS, CONFIGURE, GLOBAL)` | none | `"PLATFORM"` | `EmptyPatchError`, `DuplicateTenantNameError` (Repo), `TenantNotFoundError` | 200 |
| Nested transition | `POST /tenants/{tenant_id}/suspend` | `(ADMIN, TENANTS, OVERRIDE, GLOBAL)` | none | `"PLATFORM"` | `TenantNotFoundError`, `InvalidStateTransitionError` | 200 |
| Nested transition | `POST /tenants/{tenant_id}/activate` | `(ADMIN, TENANTS, OVERRIDE, GLOBAL)` | none | `"PLATFORM"` | same as suspend | 200 |
| Flat `/tenant-users` | `POST /tenant-users` | `(ADMIN, USERS, CONFIGURE, TENANT)` | none (tenant_id in body) | none (multi-audience) | `TenantNotFoundError`, `DuplicateTenantUserEmailError` (Repo), `InvalidRoleError`/`InvalidRoleAudienceError`/`DuplicateRoleAssignmentInRequestError` (Repo/handler), `RoleAssignmentConflictError` (Repo) | 201 |
| Flat `/tenant-users/{id}` | `PATCH /tenant-users/{user_id}` | `(ADMIN, USERS, CONFIGURE, TENANT)` | `get_tenant_user_anchor` | none | `SelfEditForbiddenError`, `EmptyPatchError`, `InvalidRoleError`/`InvalidRoleAudienceError`/`DuplicateRoleAssignmentInRequestError`, `DuplicateTenantUserEmailError`, `TenantUserNotFoundError` | 200 |
| Nested transition | `POST /tenant-users/{user_id}/suspend` | `(ADMIN, USERS, CONFIGURE, TENANT)` | `get_tenant_user_anchor` | none | `SelfEditForbiddenError`, `TenantUserNotFoundError`, `InvalidStateTransitionError` | 200 |
| Nested transition | `POST /tenant-users/{user_id}/activate` | `(ADMIN, USERS, CONFIGURE, TENANT)` | `get_tenant_user_anchor` | none | same as suspend | 200 |
| Nested write | `POST /tenants/{tenant_id}/org-tree` (add node) | `(ADMIN, ORG_NODES, CONFIGURE, TENANT)` | `get_tenant_anchor` | none | `InvalidParentNodeTypeError`/`DuplicateOrgNodeCodeError`/`OrgNodeNotFoundError` (Repo); `TenantNotFoundError` (anchor) | 201 |
| Nested write | `PATCH /tenants/{tenant_id}/org-tree/{node_id}` | `(ADMIN, ORG_NODES, CONFIGURE, TENANT)` | `get_tenant_anchor` | none | `TenantRootNotReparentableError` (handler pre-check), `OrgNodeNotFoundError`/`InvalidParentNodeTypeError`/`DuplicateOrgNodeCodeError` (Repo) | 200 |
| Nested transition | `POST /module-access/{tenant_id}/{module_code}/enable` | `(ADMIN, TENANTS, OVERRIDE, GLOBAL)` | `get_tenant_anchor` | `"PLATFORM"` | (upserts; no 404 path — Repo always returns a row) | 200 |
| Nested transition | `POST /module-access/{tenant_id}/{module_code}/disable` | `(ADMIN, TENANTS, OVERRIDE, GLOBAL)` | `get_tenant_anchor` | `"PLATFORM"` | `ModuleAccessNotFoundError` (when row absent) | 200 |

**Observation:** Three URL patterns coexist for write surfaces: flat `/<resource>` for create + flat list (tenants, tenant-users); flat `/<resource>/{id}` for PATCH; nested `/<parent>/{id}/<op>` for state transitions (tenants suspend/activate, tenant-users suspend/activate) AND nested `/<parent>/{id}/<resource>` writes for the org-tree (where org-nodes are conceptually owned by the parent tenant) AND nested `/<parent>/{tenant_id}/{module_code}/<op>` for module-access transitions. Audience kwarg is used 6 times (all PLATFORM): create/patch/suspend/activate tenants + enable/disable module-access. No `audience="TENANT"` use today.

**Confidence:** high

### F-SHAPES-3: Sort-key validation sites

**Citation:** `repositories/_errors.py:17-24` defines the shared `InvalidSortKeyError(ValueError)`. `errors.py` defines the `InvalidSortKeyClientError` shared 400 wrapper. Repo-side raise + router-side re-raise pattern, single shape across consumers.

**Current code:** all sites raise the same `InvalidSortKeyError` from a `SORT_MAP` lookup or `frozenset` membership check, then routers re-raise as `InvalidSortKeyClientError` (400, `INVALID_SORT_KEY`).

| Site | Validation style | Where raised |
|---|---|---|
| `repositories/tenants.py:280` | `if sort not in SORT_MAP and sort not in {aggregate keys}: raise` | inside `list_with_aggregates` |
| `repositories/platform_users.py:193` | dict-lookup raise | inside `list` |
| `repositories/tenant_users.py:267` | dict-lookup raise | inside `list` |
| `repositories/roles.py:151` | dict-lookup raise | inside `list_grouped` |
| `repositories/permissions.py:100` | dict-lookup raise | inside `list` |
| `repositories/modules_access.py:211` | dict-lookup raise | inside `list_matrix` |
| `repositories/role_assignments.py:87, 144` | dict-lookup raise — both `list_platform_assignments` AND `list_tenant_assignments` raise; the two methods share `ROLE_ASSIGNMENTS_SORT_KEYS` constant | inside each list method |

Router-side catch + re-raise sites: `routers/v1/tenants.py:236`, `platform_users.py:240`, `tenant_users.py:249`, `rbac.py:192, 325`, `modules_access.py:227`, `role_assignments.py:359`.

**Observation:** Uniform pattern: per-Repo `SORT_MAP` (or in two cases a `frozenset` of valid keys + a per-call clause-builder), `InvalidSortKeyError` raise on miss, router re-raises as `InvalidSortKeyClientError`. No alternative styles (no Pydantic enum, no FastAPI Literal Query type) for sort keys today.

**Confidence:** high

### F-SHAPES-4: UNIQUE-constraint pre-check sites (SELECT-then-INSERT/UPDATE)

**Citation:** grep on `_raise_if_*_taken\|raise.*Duplicate`.

**Current code:** three Repo sites.

| Site | Method | Error class raised | HTTP status |
|---|---|---|---|
| `repositories/tenants.py:446-486` | `_raise_if_name_taken` (called by `create` at `:524`, `update` at `:669`) | `DuplicateTenantNameError` | 409 |
| `repositories/tenant_users.py:_raise_if_email_taken` (the helper around `:475-499`; sites `create` + `update`) | per-tenant email uniqueness | `DuplicateTenantUserEmailError` | 409 |
| `repositories/org_nodes.py:_raise_if_code_taken` (around `:687-693`) | per-tenant-per-parent code uniqueness with handler-translation of IntegrityError to `DuplicateOrgNodeCodeError` | `DuplicateOrgNodeCodeError` | 409 |

Tenant name has no DB-level UNIQUE (FN-AB-35 forward note). Tenant user email is enforced by a DB-level UNIQUE; the pre-check produces the typed 409 ahead of the integrity error. Org-node code uniqueness is enforced by a DB-level partial UNIQUE; the Repo catches the IntegrityError and re-raises as the typed 409.

**Observation:** Two patterns coexist for UNIQUE handling: explicit SELECT-then-INSERT pre-check (tenants name, tenant_users email), and IntegrityError catch + re-raise (org_nodes code). All three converge on the same 409 + `DUPLICATE_*` envelope shape.

**Confidence:** high

### F-SHAPES-5: State-transition matrix enforcement in Repo code

**Citation:** `TransitionResult` enum sites + `transition` methods.

**Current code:** three Repos enforce a transition matrix.

| Site | TransitionResult values | Error class on invalid | Matrix |
|---|---|---|---|
| `repositories/tenants.py:192-202`, transition at `:720-795` | `OK`, `NOT_FOUND`, `INVALID_STATE` | router re-raises `InvalidStateTransitionError` (409 `INVALID_STATE_TRANSITION`) | TRIAL/ACTIVE → SUSPENDED; TRIAL/SUSPENDED → ACTIVE (SUSPENDED → ACTIVE clears suspended_*) |
| `repositories/tenant_users.py:971-1072` (TransitionResult reused from tenants Repo per `tenant_users.py:72`) | same 3 values | same `InvalidStateTransitionError` | ACTIVE → SUSPENDED; SUSPENDED → ACTIVE. INVITED → {SUSPENDED, ACTIVE} structurally rejected by `ck_tenant_users_auth0_sub_consistency`; Repo maps to INVALID_STATE |
| `repositories/modules_access.py:601-616`, enable/disable methods at `:422-448` (and surrounding) | `OK`, `NOT_FOUND` only (no INVALID_STATE) | enable upserts so never returns NOT_FOUND; disable on missing returns NOT_FOUND → `ModuleAccessNotFoundError` (404); idempotent-200 on no-op cells | enable: any → ENABLED (DISABLED → ENABLED flip, missing → INSERT, ENABLED → no-op 200); disable: ENABLED → DISABLED (DISABLED → no-op 200, missing → 404). No 409 |

**Observation:** Two transition-result vocabularies: 3-value `OK/NOT_FOUND/INVALID_STATE` for tenants and tenant_users; 2-value `OK/NOT_FOUND` for modules-access. The cross-resource asymmetry (modules idempotent-200 vs tenants 409) is captured as FN-AB-42 in CLAUDE.md. `InvalidStateTransitionError` is reused across resources via a `target_status` context kwarg.

**Confidence:** high

### F-SHAPES-6: Anchor deps with lookup chain

**Citation:** `src/admin_backend/auth/anchor_deps.py:38-148`.

| Function | Input → SQL → output | Raises on miss |
|---|---|---|
| `get_tenant_anchor(tenant_id)` | `tenant_id` → `SELECT path FROM core.org_nodes WHERE tenant_id=:id AND node_type='TENANT' AND parent_id IS NULL LIMIT 1` → ltree path string | `TenantNotFoundError` (404) |
| `get_org_node_anchor(tenant_id, node_id)` | `(tenant_id, node_id)` composite → `SELECT path FROM core.org_nodes WHERE tenant_id=:id AND id=:node_id LIMIT 1` → ltree path string | `OrgNodeNotFoundError` (404) |
| `get_tenant_user_anchor(user_id)` | `user_id` → JOIN `tenant_users tu ⋈ org_nodes on_ ON on_.tenant_id=tu.tenant_id AND on_.node_type='TENANT' AND on_.parent_id IS NULL WHERE tu.id=:user_id LIMIT 1` → ltree path string (tenant-root path) | `TenantUserNotFoundError` (404) |

**Observation:** All three deps return the same shape (ltree path string); all three RAISE on miss (no `None` return); all three raise resource-specific 404 errors so the response envelope can carry the right `code` and structured context. Cross-tenant probes from a TENANT JWT surface as 404 via RLS-as-404 (D-17).

**Confidence:** high

---

## SEED — Stores rows in seed Excel

### F-SEED-1: Total row count and per-tenant breakdown

**Citation:** read-only inspection of `data/ithina_dev_seed_data.xlsx` `stores` sheet.

**Current code:** 25 total rows.

| Tenant id | Tenant name | Store count |
|---|---|---|
| `c241330b-01a9-471f-9e8a-774bcf36d58b` | GreenLeaf Markets | 6 |
| `b74d0fb1-32e7-4629-8fad-c1a606cb0fb3` | Infomil Retail | 6 |
| `6b65a6a4-8b81-48f6-b38a-088ca65ed389` | FreshMart Co-op | 4 |
| `972a8469-1641-4f82-8b9d-2434e465e150` | Buc-ee's | 3 |
| `17fc695a-07a0-4a6e-8822-e8f36c031199` | Żabka Group | 3 |
| `9a1de644-815e-46d1-bb8f-aa1837f8a88b` | SmartStore Demo | 2 |
| `47378190-96da-4dac-b2ff-5d2a386ecbe0` | CornerStop | 1 |

Header columns present in the sheet: `_org_node_key, _tenant_key, id, tenant_id, org_node_id, name, store_code, country, timezone, address, latitude, longitude, currency, tax_treatment, status, created_at, created_by_user_id, created_by_user_type, updated_at, updated_by_user_id, updated_by_user_type`. Headers absent: `closed_at`, `closed_by_user_id`, `closed_by_user_type`.

**Observation:** 25 rows across 7 tenants. All 7 v0 tenants have at least one store. The seed sheet has no `closed_*` columns — every row's status is necessarily non-`CLOSED` since the CHECK constraint `ck_stores_closed_consistency` requires the closed_* triplet populated for `CLOSED` rows and NULL for non-`CLOSED` rows. No CHECK violation risk from seed shape.

**Confidence:** high

### F-SEED-2: Distinct status and country values

**Citation:** seed Excel `stores` sheet `status` and `country` columns.

**Current code:**

- Distinct `status` values: `['ACTIVE']` (1 value)
- Distinct `country` values: `['Canada', 'France', 'Poland', 'UK', 'USA']` (5 values)

**Observation:** All 25 seed rows are `status='ACTIVE'`. No `OPENING`, no `INACTIVE`, no `CLOSED` rows in seed. Five country strings, all mixed-case (matches `ck_stores_country_format` regex `'[A-Za-z]'`). The country list does not overlap with the `country` lookups list (which is empty per Step 3.6's deferred decision).

**Confidence:** high

### F-SEED-3: Rows with `closed_at` populated

**Citation:** seed Excel `stores` sheet.

**Current code:** 0 rows have `closed_at` populated. The column itself is absent from the seed sheet header.

**Observation:** No stores in the seed are in `CLOSED` state. Any test or smoke flow exercising the `CLOSED` lifecycle would need to construct its own row.

**Confidence:** high

### F-SEED-4: Cross-reference with dashboard `fleet-stats.total_stores`

**Citation:** seed row count = 25 (per F-SEED-1); live DB count under PLATFORM session via psql `SELECT COUNT(*) FROM core.stores` after `set_config('app.user_type','PLATFORM',true)` = 25; dashboard's `fleet-stats` CTE counts `COUNT(*) FROM {schema}.stores` (`repositories/dashboard.py:154-159`) under the caller's RLS context.

**Observation:** Seed count and live DB count match at 25. The dashboard's `stores.value` field would surface 25 under a PLATFORM JWT against the live seeded DB.

**Confidence:** high

---

## TESTS — Existing test scaffolding touching stores

### F-TESTS-1: Files matching `*store*` in `tests/`

**Citation:** `find tests -iname "*store*"` returns zero hits.

**Observation:** No test file has "store" in its name. No dedicated stores-resource test module exists.

**Confidence:** high

### F-TESTS-2: Bare-string `stores` references in tests, outside store-named files

Since F-TESTS-1's set is empty, every store-related test reference is in this section. Filtered to remove unrelated `STORE` tokens (org_node type, SINGLE_STORE tier enum, `stored` verb, `number_of_stores`, `num_stores`, `total_stores`, `StoresCard`, `node_type.STORE`).

**Citation:** grep results.

**Current code:**

| File:line | Context |
|---|---|
| `tests/integration/conftest.py:484-547` | `make_store` async fixture: raw-SQL INSERT into `core.stores`, country fixed to `'United States'`, currency `'USD'`, tax_treatment `EXCLUSIVE`, status `ACTIVE`, audit-actor pair NULL/NULL; returns `Store(id=..., tenant_id=...)` stub instance |
| `tests/integration/test_tenants_router.py:436, 446, 448, 463, 473, 475, 557, 570, 572, 574, 761, 769-770` | `make_store(tenant_id=...)` invocations to set up tenant rows with non-trivial `num_stores` aggregates for sort/page assertions |
| `tests/integration/test_dashboard_router.py:319-358, 698, 709` | S6 test: insert 4 stores, force three distinct countries via raw `UPDATE {schema}.stores SET country=:c WHERE id=:id`, assert `body["stores"]["value"]==4` and `distinct_countries==3` |
| `tests/integration/test_dashboard_router.py:330-332` | `make_store` country override commentary (the fixture doesn't accept a country override; the test patches via raw SQL) |
| `tests/integration/test_me_router.py:544, 558` | One test uses `make_org_node(node_type='STORE', ...)` to construct a STORE-typed org_node as a target_anchor for a permission check — NOT a `stores` table row |

**Observation:** The stores table is touched by tests exclusively via the `make_store` fixture, used to populate tenants-resource aggregate columns and the dashboard's stores card. No test exercises a stores-resource handler (none exists). No test exercises a stores CRUD operation directly.

**Confidence:** high

### F-TESTS-3: `make_*` fixtures in `tests/integration/conftest.py`

**Citation:** `tests/integration/conftest.py` fixture definitions.

**Current code (12 make_* fixtures):**

- `make_tenant` (`:282`)
- **`make_store` (`:484`)** — exists
- `make_tenant_user` (`:551`)
- `make_platform_user` (`:668`)
- `make_tenant_module_access` (`:761`)
- `make_org_node` (`:892`)
- `make_role` (`:1018`)
- `make_permission` (`:1113`)
- `make_role_permission` (`:1188`)
- `make_platform_user_role_assignment` (`:1263`)
- `make_tenant_user_role_assignment` (`:1367`)
- Plus `tenant_owner_jwt_factory` (`:1552`) — not a `make_*` but a related synthetic-user factory.

**Observation:** `make_store` exists. It uses raw `text()` INSERT, fixes `country='United States'`, `timezone='America/New_York'`, `currency='USD'`, `tax_treatment='EXCLUSIVE'`, `status='ACTIVE'`, audit-actor pair both NULL. The fixture's docstring (lines 494-499) notes Step 4.5 will replace it with an ORM-native INSERT once the full Store model lands. Teardown DELETEs the tracked ids in one statement.

**Confidence:** high

### F-TESTS-4: `scripts/smoke_test.py` assertions touching stores

**Citation:** grep + section reads.

**Current code (lines that touch the stores table specifically):**

| Line(s) | Assertion / setup |
|---|---|
| `:219-249` | `insert_store(cur, store_id, tenant_id, org_node_id, name, ...)` helper used by setup blocks |
| `:300, 310` | Setup data: `insert_store(... TENANT_A_STORE ..., "Store A")`, `insert_store(... TENANT_B_STORE ..., "Store B")` |
| `:338, 375` | Truth-table assertions `("stores", 1)` — under TENANT-A's session see exactly 1 store row |
| `:441` | Cleanup loop iterates `("tenants", "tenant_users", "org_nodes", "stores", ...)` for DELETE |
| `:464-501` | `test_4_cross_tenant_insert_rejected`: under TENANT-A context, INSERT into `stores` with `tenant_id=B` is REJECTED |
| `:502-537` | `test_5_stores_composite_fk`: under TENANT-B, INSERT into `stores` with `(tenant_id=B, org_node_id=A's org_node)` REJECTED by composite FK |
| `:744-775` | `test_10_currency_check`: assertion 10, INSERT with lowercase `currency='usd'` FAILS `ck_stores_currency_format` |
| `:999, 1051` | `test_15`'s truth table includes `stores` as one of the 4 (now 6) multi-tenant tables exercised |
| `:1176, 1235-1248` | `test_16_platform_can_insert_into_multi_tenant_tables` includes `("stores", "INSERT stores")` — verifies PLATFORM session can INSERT |

**Observation:** Smoke test exercises stores rows in 4 assertions (cross-tenant insert reject, composite FK reject, currency CHECK reject, PLATFORM-session-can-INSERT) plus visibility cells in the truth tables. The "stores": 1 visibility expectation under TENANT-A vs invisible under TENANT-B is the canonical RLS cell. No smoke assertion exercises a stores handler (none exists).

**Confidence:** high

### F-TESTS-5: `scripts/smoke_curl.sh` / `scripts/test_endpoints.sh` / `scripts/test_endpoints_cloud.sh` stores references

**Citation:** grep results.

**Current code:**

- `smoke_curl.sh:106, 109` — header comments for org-tree write step (mentions `STORE` node_type, not a store table).
- `smoke_curl.sh:240, 296, 297, 345` — references to `number_of_stores` field on a tenant POST body (tenants resource, not stores).
- `smoke_curl.sh:758, 780, 793` — STORE org_node creation for the org-tree write flow (mentions `OT_STORE_CODE="ot-store-${OT_SUFFIX:0:8}"` and `STORE-under-anything-above-STORE` cascade-rule wording). NOT a `stores` table row.
- `test_endpoints.sh:639-640, 662, 911-912, 916, 946-948, 950, 964, 972, 979` — same shape: tenant POST body `number_of_stores`, and org-tree STORE node_type flow.
- `test_endpoints_cloud.sh:448, 716-717, 738, 981, 983, 985, 1005, 1010` — same shape.

**Observation:** No script references a `/api/v1/stores` URL. Every `store`/`STORE` token in the three scripts is either: tenant resource body field (`number_of_stores`), or an org-tree write flow that adds a `STORE`-typed org_node (which has no corresponding row in `core.stores` — it's an `org_nodes` row of type STORE).

**Confidence:** high

---

## WIRING — Router registration and OpenAPI surface

### F-WIRING-1: `/stores` paths in `docs/endpoints/openapi.json`

**Citation:** `docs/endpoints/openapi.json` parsed; 30 paths total; zero paths containing "store" substring (case-insensitive).

**Observation:** No `/api/v1/stores` paths in the committed OpenAPI spec. Spec was generated against the running app's surface; no stores router means no path entries.

**Confidence:** high

### F-WIRING-2: `docs/endpoints/stores.md` state

**Citation:** `docs/endpoints/` enumerated; file list: `dashboard.md, me.md, module-access.md, openapi.json, org-tree.md, platform-users.md, rbac.md, role-assignments.md, tenants.md, tenant-users.md`.

**Observation:** `docs/endpoints/stores.md` does not exist. Not a placeholder, not a draft — entirely absent.

**Confidence:** high

---

## End of investigation report

No surface-and-stop conditions triggered: docs and code agree (CLAUDE.md describes `make_store` as a stub-binding artifact, and that is what exists; CLAUDE.md flags Step 4.5 as "not yet completed" for the Stores router, and the artifacts are absent in the codebase); no dead-wired stores artifact exists (the lightweight `Store` stub has exactly one importing consumer); seed `stores` rows respect all CHECK constraints. Report complete.
