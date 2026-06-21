# Prompt — Step 6.8.2: ORM models, repositories, schemas, seed loader for the post-split URA tables

> Generated 2026-05-09. Second of three steps under section 6.8 splitting `user_role_assignments` into `platform_user_role_assignments` (no RLS, platform-global) and `tenant_user_role_assignments` (RLS+FORCE, unconditional OR-branch policy).
>
> **Step 6.8.1 has shipped locally** — schema split is complete; codebase does not currently compile against the new schema (17 known-failing pytest tests reference the dropped `user_role_assignments` table via the lightweight stub or the seed loader). This step makes the codebase functional again on the new schema.
>
> **Local-only scope.** No cloud deploy. The full bundle deploys to Cloud SQL only after 6.8.3 lands.
>
> Paste this entire block into a fresh Claude Code session to start Step 6.8.2.

---

## Caution-first posture

The operative word for steps 6.8.1, 6.8.2, and 6.8.3 is **caution**. For 6.8.2 specifically:

1. **The lightweight-stub swap is load-bearing.** `RolesRepo._user_count_subquery` is the only consumer of the URA stub today. Its rewrite from "single-table correlated subquery" to "UNION over two correlated subqueries" must preserve the `.correlate(Role)` discipline that R4 (in `test_rbac_router.py`) verifies. The same trap that bit Step 3.3 L9 / Step 5.3 L11 / Step 6.1 R4 is present here on TWO subqueries instead of one.
2. **The seed loader rewrite changes the GUC pattern.** The existing loader uses per-row `set_config('app.tenant_id', ..., true)` impersonation because the FN-AB-14 IS-NULL-gated policy required it. With the unconditional OR-branch on the new tenant table, that impersonation becomes unnecessary AND the `_set_tenant_guc` helper becomes dead code. Removing it cleanly is the right move; missing a caller and breaking a different loader is not.
3. **The 17 failing tests are the precise scope target.** They were enumerated by Step 6.8.1's report. Every one of them must end this step green. Any test going green that wasn't on the list, or any test on the list staying red, indicates the cutover is incomplete.

Concretely: **stop and surface anything that doesn't match the prompt's stated assumptions before writing code.** Section "Stop and ask if" enumerates concrete triggers; the disposition is "lean toward stopping" rather than "lean toward proceeding."

---

## Context: state at start of this step

Step 6.8.1 (commit landed locally as alembic revision `3e05299cb533`) split `user_role_assignments` into:

- **`platform_user_role_assignments`** — no RLS, platform-global. Columns: `id`, `platform_user_id`, `role_id`, `status`, `granted_at`, `granted_by_user_id`, `granted_by_user_type`, `revoked_at`, `revoked_by_user_id`, `revoked_by_user_type`, `updated_at`. BEFORE INSERT/UPDATE OF role_id trigger enforces `role.audience='PLATFORM'`.
- **`tenant_user_role_assignments`** — RLS+FORCE with the unconditional OR-branch (matching the other 5 multi-tenant tables). Columns: `id`, `tenant_user_id`, `tenant_id`, `org_node_id`, `role_id`, `status`, `granted_at`, `granted_by_user_id`, `granted_by_user_type`, `revoked_at`, `revoked_by_user_id`, `revoked_by_user_type`, `updated_at`. Composite FKs to `tenant_users(tenant_id, id)` and `org_nodes(tenant_id, id)`. BEFORE INSERT/UPDATE OF role_id trigger enforces `role.audience='TENANT'`.

The codebase still references the dropped `user_role_assignments` table via:

- `src/admin_backend/models/_lightweight_stubs.py::UserRoleAssignment` — used by `RolesRepo._user_count_subquery`.
- `scripts/seed_dev_data/loaders/user_role_assignments.py` — writes to the dropped table.

Pytest currently reports 17 failures, all tracing to `relation "core.user_role_assignments" does not exist`:

```
test_rbac_router.py::test_r1_envelope_pre_grouped_with_user_count
test_rbac_router.py::test_r2_tenant_jwt_platform_block_empty
test_rbac_router.py::test_r3_platform_jwt_sees_both_audiences
test_rbac_router.py::test_r4_user_count_aggregate_correlates_per_role
test_rbac_router.py::test_r5_status_filter_default_active
test_rbac_router.py::test_r6_search_q_ilike
test_rbac_router.py::test_r8_is_system_filter
test_rbac_router.py::test_p1_envelope_and_default_sort
test_rbac_router.py::test_rp1_returns_role_permissions_with_parent_echo
test_rbac_router.py::test_rp2_unknown_role_returns_404
test_rbac_router.py::test_rp3_tenant_jwt_platform_role_returns_404
test_rbac_router.py::test_m4_display_labels_join_from_lookups
test_rbac_router.py::test_h1_role_response_hides_audit_actors
test_seed_loader.py::test_l1_seed_runs_clean_end_to_end
test_seed_loader.py::test_l2_seed_row_counts
test_seed_loader.py::test_l2b_user_role_assignments_total_across_tenants
test_seed_loader.py::test_l3_seed_sentinel_rows
```

13 in `test_rbac_router.py` (all touch `user_count` or the `_insert_active_platform_ura` helper); 4 in `test_seed_loader.py` (all reference URA shape).

The local DB is in **post-truncate state** — only `lookups` populated. Re-seeding requires the seed loader rewrite that this step lands. After this step, `python -m scripts.seed_dev_data --reset` rebuilds the full dev seed against the new tables.

---

## Hard constraints (non-negotiable)

1. **All 17 failing tests must end this step green.** Any pytest count delta beyond +17 (i.e., a test that wasn't failing pre-step now goes green for unrelated reasons, or vice versa) needs explicit explanation in the report.
2. **The `.correlate(Role)` discipline on the user_count subquery must hold across both UNION branches.** This is the third occurrence of the L9 trap; the R4 test catches it.
3. **No new endpoints.** This step is internal-only — ORM, repos, schemas, seed loader. The new `/role-assignments` endpoint lands in 6.8.3.
4. **No changes to the schema or migration history.** This step works against the schema 6.8.1 created. Do not write a new migration.

---

## Pre-flight

1. Run `./scripts/check_setup.sh`. Expect 35/35.
2. `git log --oneline -5` — confirm Step 6.8.1's commit at HEAD or close to it.
3. `uv run alembic heads` — note the current head revision (expected `3e05299cb533` per the 6.8.1 commit). Surface immediately if different.
4. `uv run alembic current` — should match `heads`.
5. **Confirm DB is reachable but empty (post-truncate state).** Run:
   ```sql
   SELECT count(*) FROM core.lookups;  -- expect 44
   SELECT count(*) FROM core.tenants;  -- expect 0
   SELECT count(*) FROM core.platform_user_role_assignments;  -- expect 0
   SELECT count(*) FROM core.tenant_user_role_assignments;  -- expect 0
   ```
   If the DB has data already, surface — it suggests something has run between 6.8.1 and now.
6. Read `CLAUDE.md` fully. Focus on:
   - **D-13** — audit-actor patterns. New ORM models declare `actor_user_type_enum` for `granted_by_user_type` / `revoked_by_user_type` (Pattern (b)).
   - **D-15** — `DB_SCHEMA` from environment; ORM `__table_args__["schema"]` resolves from `settings.db_schema`.
   - **D-21** — UUIDv7 default via `uuidv7()`. ORM `id` columns use `server_default=FetchedValue()`.
   - **D-24** — Repos do NOT accept `tenant_id` for visibility purposes. The new `RoleAssignmentsRepo` follows this.
   - **D-29** — PLATFORM RLS visibility. The new `tenant_user_role_assignments` joined this set in 6.8.1.
   - **D-34 (new from 6.8.1)** — Mixed-audience tables get split. This step is the codebase realisation of that decision.
   - **PG enum convention** — `PG_ENUM(..., create_type=False, native_enum=True, values_callable=lambda e: [m.value for m in e])`. Both ORM models use this for `status`, `granted_by_user_type`, `revoked_by_user_type`.
   - **Workflow convention — Per-step commit bundling.** All five surfaces (code, CLAUDE.md, BUILD_PLAN.md, architecture.md if shape changed, prompt file) land in this step's commit.
7. Read `src/admin_backend/models/_lightweight_stubs.py` fully. Identify:
   - The `UserRoleAssignment` class (to be removed).
   - The `Store` class (must remain; Step 4.5 hasn't shipped).
   - Any other declarations.
8. Read `src/admin_backend/repositories/roles.py` fully. Focus on `_user_count_subquery` — the post-split rewrite is the load-bearing work.
9. Read `src/admin_backend/models/tenant_user.py` and `src/admin_backend/models/platform_user.py` (both shipped in Step 5.1 / 5.2). These are the canonical full-ORM-model precedents to mirror — file structure, enum declarations, audit-actor column shape, FetchedValue defaults.
10. Read `src/admin_backend/models/role.py`, `permission.py`, `role_permission.py` (Step 6.1). Mirror their imports, naming, and `__table_args__` structure.
11. Read `scripts/seed_dev_data/loaders/user_role_assignments.py` fully. Understand the per-row impersonation pattern and the dual-FK XOR routing logic.
12. Read `scripts/seed_dev_data/loaders/_base.py`. The `insert_and_register` helper is reused by the new routing loader. Confirm understanding before changes.
13. Read `scripts/seed_dev_data/column_mappings.py`. Locate the `USER_ROLE_ASSIGNMENTS` SheetMapping (current shape: dual-FK XOR with both `platform_user_id` and `tenant_user_id` FK_REFs). The mapping STAYS as one entry per the option-(a) decision; only the docstring/comments update.
14. Read `scripts/seed_dev_data/runner.py`. Locate the entry for `user_role_assignments` in the loader sequence — the entry stays as one symbol; routing happens inside the loader.
15. Read `scripts/seed_dev_data/truncate.py`. The truncate list currently has `user_role_assignments`; replace with the two new table names.
16. Read `tests/integration/test_rbac_router.py` fully. Focus on:
    - The `_insert_active_platform_ura` helper at the top (the helper that fixes 13 of the 17 failing tests).
    - The `_delete_uras_by_id` teardown helper.
    - R4 specifically (`test_r4_user_count_aggregate_correlates_per_role`) — load-bearing for the correlate-on-UNION discipline.
17. Read `tests/integration/test_seed_loader.py` fully. Focus on:
    - The `EXPECTED` dict (likely line ~43-45).
    - `test_l2_seed_row_counts` (uses EXPECTED).
    - `test_l2b_user_role_assignments_total_across_tenants` — the per-tenant iteration test that needs full rewrite.
    - The PLATFORM-audience count test (~line 174 per Step 6.8.1's earlier survey).
    - `test_l3_seed_sentinel_rows` — sentinel-row assertions that may reference URA.
18. Read `docs/endpoints/rbac.md`. Focus on the `user_count` field description (line 114 per earlier survey). Update the description to reflect UNION-over-two-tables computation.
19. Read this prompt fully. Confirm scope before writing code.
20. **Capture pre-step pytest baseline.** Run `uv run pytest --tb=no -q 2>&1 | tail -10` and record:
    - Total tests passed
    - Total tests failed (expected: 17)
    - The exact 17 failing test names (cross-check against the 17 listed in the Context section above)
    - Any failures NOT in the list (surface immediately — that's an unexpected regression and stop-and-ask trigger)

---

## Step ID and intent

**Step 6.8.2** — ORM, Repos, schemas, seed loader for the post-split URA tables. No new endpoints, no migrations.

**Eight code deliverables plus four bundled documentation updates:**

Code:
1. **Two new ORM models**: `PlatformUserRoleAssignment` and `TenantUserRoleAssignment`. One file per table per project convention.
2. **`_lightweight_stubs.py` cleanup**: remove `UserRoleAssignment` stub class. Keep `Store`.
3. **`models/__init__.py`**: re-export the two new ORM classes; remove `UserRoleAssignment` re-export.
4. **`RolesRepo._user_count_subquery` rewrite**: UNION over two correlated subqueries on the two new tables. Preserve `.correlate(Role)` discipline on both branches.
5. **New `RoleAssignmentsRepo`** with two list methods (one per table) — used by 6.8.3's router. List signatures defined here so 6.8.3's wire-up is mechanical.
6. **New schemas** (`PlatformAssignmentItem`, `TenantAssignmentItem`, `RoleAssignmentsResponse`) — defined here so 6.8.3 can use them without refactoring.
7. **Seed loader rewrite**: `loaders/user_role_assignments.py` becomes a routing loader (one entry per row inspects which user-side FK is populated and routes to the right new table). Per-row tenant impersonation removed (no longer needed under unconditional OR-branch). `_set_tenant_guc` helper removed if no other caller uses it.
8. **Test updates**: 
   - `test_rbac_router.py`: `_insert_active_platform_ura` helper renamed and rewritten to insert into `platform_user_role_assignments`. The 13 R/P/RP/M/H tests using this helper go green automatically.
   - `test_seed_loader.py`: `EXPECTED` dict updated; `test_l2b_*` rewritten for the simpler unconditional-OR shape; PLATFORM-audience count test updated; sentinel-row assertions verified.

Documentation (per the per-step bundling convention):
9. **CLAUDE.md updates** (see "CLAUDE.md changes this step" section below).
10. **BUILD_PLAN.md updates** (see section below).
11. **architecture.md updates** (likely "no change" — surface confirms or describes the edit).
12. **`docs/endpoints/rbac.md`** — `user_count` field description updated to reflect UNION-over-two-tables computation.
13. **Prompt file** committed alongside per the convention.

This is a CLAUDE_CODE step. No application code changes the API surface; no auth changes; no migrations.

---

## Source-of-truth specification

### File 1: `src/admin_backend/models/platform_user_role_assignment.py` — NEW

Mirror the structure of `models/tenant_user.py` and `models/role.py`. PG enums declared at module level alongside the model class.

```python
"""ORM model for platform_user_role_assignments.

Platform-global table: no RLS, no tenant_id. PLATFORM-audience role
assignments to platform_users. Mirrors the platform_users / tenant_users
Pattern 2 split (D-12, D-34).

Audience invariant ('PLATFORM' role audience only) is enforced by the
DB-level BEFORE INSERT/UPDATE OF role_id trigger
enforce_platform_role_audience() — application code does not need to
re-enforce.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from sqlalchemy import FetchedValue, ForeignKey
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM
from sqlalchemy.orm import Mapped, mapped_column

from admin_backend.config import settings
from admin_backend.models._base import Base


class UserRoleAssignmentStatus(str, Enum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class ActorUserType(str, Enum):
    PLATFORM = "PLATFORM"
    TENANT = "TENANT"


class PlatformUserRoleAssignment(Base):
    """Active or revoked role grant for a platform user.

    No RLS — platform_users are globally visible to PLATFORM sessions.
    Visibility is controlled at role-grant level (the rbac router's
    audience filter), not via row-level isolation.
    """

    __tablename__ = "platform_user_role_assignments"
    __table_args__ = {"schema": settings.db_schema}

    id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        server_default=FetchedValue(),
    )
    platform_user_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey(f"{settings.db_schema}.platform_users.id"),
        nullable=False,
    )
    role_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey(f"{settings.db_schema}.roles.id"),
        nullable=False,
    )
    status: Mapped[UserRoleAssignmentStatus] = mapped_column(
        PG_ENUM(
            UserRoleAssignmentStatus,
            name="user_role_assignment_status_enum",
            create_type=False,
            native_enum=True,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    granted_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=FetchedValue(),
    )
    granted_by_user_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True), nullable=True,
    )
    granted_by_user_type: Mapped[ActorUserType | None] = mapped_column(
        PG_ENUM(
            ActorUserType,
            name="actor_user_type_enum",
            create_type=False,
            native_enum=True,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=True,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(nullable=True)
    revoked_by_user_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True), nullable=True,
    )
    revoked_by_user_type: Mapped[ActorUserType | None] = mapped_column(
        PG_ENUM(
            ActorUserType,
            name="actor_user_type_enum",
            create_type=False,
            native_enum=True,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=FetchedValue(),
    )
```

**Note on enum re-declarations — confirmed via investigation:**

- **`ActorUserType`** exists at `src/admin_backend/models/tenant_user.py:73`. **Import from there**, do not redeclare:
  ```python
  from admin_backend.models.tenant_user import ActorUserType
  ```

- **`UserRoleAssignmentStatus`** does NOT exist as a Python enum anywhere. The lightweight stub used raw PG_ENUM column with `Mapped[str]`. **Declare it in `platform_user_role_assignment.py`** (the file naming makes it the natural home). Import from there in `tenant_user_role_assignment.py`. Re-export both `ActorUserType` (already exported via tenant_user) and `UserRoleAssignmentStatus` (new) from `models/__init__.py` if not already present.

The code shown in this prompt's File 1 declared both enums locally; **adjust to import `ActorUserType` from `tenant_user` and declare only `UserRoleAssignmentStatus`**.

### File 2: `src/admin_backend/models/tenant_user_role_assignment.py` — NEW

Same shape as File 1, with the additional columns (tenant_id, tenant_user_id, org_node_id) and the RLS-relevant comment.

```python
"""ORM model for tenant_user_role_assignments.

Multi-tenant table: RLS+FORCE with the unconditional OR-branch policy
(matching tenants, tenant_users, org_nodes, stores, tenant_module_access
per D-29). A row is visible to its own tenant under TENANT JWT, and to
any PLATFORM session.

Composite FKs to tenant_users(tenant_id, id) and org_nodes(tenant_id, id)
prevent cross-tenant injection structurally (D-34, AI-RBAC-06 closed).
The composite FKs are NOT declared at the SA layer — composite FKs to
non-PK columns require explicit ForeignKeyConstraint at __table_args__
which we omit per existing project convention (Step 3.1's Pattern;
no Repo query needs the SA-layer FK).

Audience invariant ('TENANT' role audience only) is enforced by the
DB-level BEFORE INSERT/UPDATE OF role_id trigger
enforce_tenant_role_audience() — application code does not need to
re-enforce.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import FetchedValue, ForeignKey
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM
from sqlalchemy.orm import Mapped, mapped_column

from admin_backend.config import settings
from admin_backend.models._base import Base
from admin_backend.models.platform_user_role_assignment import (
    ActorUserType,
    UserRoleAssignmentStatus,
)


class TenantUserRoleAssignment(Base):
    """Active or revoked role grant for a tenant user at an org_node anchor."""

    __tablename__ = "tenant_user_role_assignments"
    __table_args__ = {"schema": settings.db_schema}

    id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        server_default=FetchedValue(),
    )
    tenant_user_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        nullable=False,  # Composite FK at DB layer; no SA-layer FK
    )
    tenant_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey(f"{settings.db_schema}.tenants.id"),
        nullable=False,
    )
    org_node_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        nullable=False,  # Composite FK at DB layer; no SA-layer FK
    )
    role_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey(f"{settings.db_schema}.roles.id"),
        nullable=False,
    )
    status: Mapped[UserRoleAssignmentStatus] = mapped_column(
        PG_ENUM(
            UserRoleAssignmentStatus,
            name="user_role_assignment_status_enum",
            create_type=False,
            native_enum=True,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    granted_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=FetchedValue(),
    )
    granted_by_user_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True), nullable=True,
    )
    granted_by_user_type: Mapped[ActorUserType | None] = mapped_column(
        PG_ENUM(
            ActorUserType,
            name="actor_user_type_enum",
            create_type=False,
            native_enum=True,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=True,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(nullable=True)
    revoked_by_user_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True), nullable=True,
    )
    revoked_by_user_type: Mapped[ActorUserType | None] = mapped_column(
        PG_ENUM(
            ActorUserType,
            name="actor_user_type_enum",
            create_type=False,
            native_enum=True,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=FetchedValue(),
    )
```

### File 3: `src/admin_backend/models/_lightweight_stubs.py` — MODIFY

Remove the `UserRoleAssignment` stub class (lines ~47-92 per Step 6.8.1's report). Keep:
- `Store` (Step 4.5 hasn't shipped).
- Any other stubs that exist.

Update the module-level docstring to reflect that only `Store` remains (or whatever is still present).

### File 4: `src/admin_backend/models/__init__.py` — MODIFY

- Add re-exports for `PlatformUserRoleAssignment`, `TenantUserRoleAssignment`.
- Add re-exports for `UserRoleAssignmentStatus`, `ActorUserType` if not already exported from another module.
- Remove the re-export for `UserRoleAssignment` if present.

### File 5: `src/admin_backend/repositories/roles.py` — MODIFY (load-bearing rewrite)

Rewrite `_user_count_subquery` from single-table correlated subquery to UNION-over-two correlated subqueries. The R4 test (`test_r4_user_count_aggregate_correlates_per_role`) is the load-bearing regression check — it verifies per-row correlation with the parent `Role` row.

**Current shape (verified — `src/admin_backend/repositories/roles.py` lines 58-75):**

```python
def _user_count_subquery() -> Any:
    """Correlated scalar subquery: COUNT(*) of ACTIVE assignments for
    the outer ``Role`` row.
    ...
    Returns a SA ScalarSelect that callers wrap in ``.label(...)``.
    """
    return (
        select(func.count())
        .select_from(UserRoleAssignment)
        .where(UserRoleAssignment.role_id == Role.id)
        .where(UserRoleAssignment.status == "ACTIVE")
        .correlate(Role)
        .scalar_subquery()
    )
```

Two call sites, both consume via `.label("user_count")`:
- Line 151 in `list_grouped`: `user_count_col = _user_count_subquery().label("user_count")`
- Line 181 in `get_by_id`: `user_count_col = _user_count_subquery().label("user_count")`

The import on line 39: `from admin_backend.models._lightweight_stubs import UserRoleAssignment`. This import goes away when the stub is removed.

**New shape:**

The current helper returns a `ScalarSelect` that the caller wraps with `.label("user_count")`. The new helper preserves the same contract — returns something the caller can call `.label("user_count")` on. The two call sites (lines 151, 181) need NO changes.

```python
from sqlalchemy import select, func

from admin_backend.models import (
    PlatformUserRoleAssignment,
    TenantUserRoleAssignment,
    UserRoleAssignmentStatus,
)
from admin_backend.models.role import Role  # already imported per current file

def _user_count_subquery() -> Any:
    """Correlated count of ACTIVE assignments for the outer ``Role`` row,
    summed across both physical assignment tables.

    Implementation: TWO independent correlated scalar subqueries (one
    per physical table), summed at the column-expression layer. Cleaner
    than UNION-then-SUM because:
    - Each .correlate(Role) is on a single subquery (more obvious;
      easier to read; harder to get wrong).
    - No subquery wrapper around a UNION (one less layer of nesting).
    - SQLAlchemy emits two scalar subselects added at SQL level —
      Postgres optimises this efficiently.

    Both subqueries MUST .correlate(Role). The R4 test verifies
    per-row correlation; without correlate(Role), the subqueries
    execute once per query (returning a global total) instead of
    once per row.

    For TENANT JWTs: the tenant_user_role_assignments branch inherits
    the request's session GUCs and RLS scopes the count to the calling
    tenant automatically (D-29 unconditional OR-branch). The
    platform_user_role_assignments branch has no RLS — every PLATFORM
    or TENANT session sees all platform-side assignments. Audience-check
    triggers ensure: PLATFORM-audience roles only have entries on the
    platform table; TENANT-audience roles only on the tenant table.
    The other branch contributes 0 by construction.

    Returns a SQLAlchemy column expression that callers wrap in
    ``.label("user_count")`` — same contract as the previous helper.
    """
    platform_count_subq = (
        select(func.count(PlatformUserRoleAssignment.id))
        .where(PlatformUserRoleAssignment.role_id == Role.id)
        .where(PlatformUserRoleAssignment.status == "ACTIVE")
        .correlate(Role)
        .scalar_subquery()
    )
    tenant_count_subq = (
        select(func.count(TenantUserRoleAssignment.id))
        .where(TenantUserRoleAssignment.role_id == Role.id)
        .where(TenantUserRoleAssignment.status == "ACTIVE")
        .correlate(Role)
        .scalar_subquery()
    )
    # Sum the two scalars; either may return 0 in normal operation.
    # SQLAlchemy emits this as a single column expression that supports
    # .label() — same contract as the previous helper.
    return platform_count_subq + tenant_count_subq
```

**Note on the status comparison:** kept as the string literal `"ACTIVE"` (matching the current code's line 72 pattern) for minimum diff. If you prefer type-safety, replace both occurrences with `UserRoleAssignmentStatus.ACTIVE` after declaring the enum — both work; the string literal is what the current code uses.

**Critical:** `.correlate(Role)` is on EACH inner scalar subquery. Forgetting it on either branch causes that branch to compute a global total, summed with the other (correctly correlated) branch — the symptom is a `user_count` that's correctly per-row for one audience and globally inflated for the other. The R4 test catches this.

**Caller call sites need NO changes** — they continue calling `_user_count_subquery().label("user_count")` and the result remains a usable column expression.

**Remove import on line 39:** `from admin_backend.models._lightweight_stubs import UserRoleAssignment`. Replace with imports of the two new ORM models (paths confirmed to exist as a result of File 1 and File 2 in this prompt).

**Verification:** after the rewrite, run R4 specifically: `uv run pytest tests/integration/test_rbac_router.py::test_r4_user_count_aggregate_correlates_per_role -v`. Must pass.

### File 6: `src/admin_backend/repositories/role_assignments.py` — NEW

```python
"""Repository for role assignments — internal-facing.

Two list methods, one per physical table. The 6.8.3 /role-assignments
router calls both and assembles the grouped response shape with
two arrays (platform_assignments, tenant_assignments).

Per D-24, this Repo does NOT accept tenant_id for visibility purposes.
For the tenant table, RLS scopes via session GUCs. For the platform
table, no scoping — platform-global.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from admin_backend.models import (
    PlatformUserRoleAssignment,
    TenantUserRoleAssignment,
    UserRoleAssignmentStatus,
)
from admin_backend.repositories._errors import InvalidSortKeyError


PLATFORM_ASSIGNMENTS_SORT_MAP = {
    "granted_at_desc": [PlatformUserRoleAssignment.granted_at.desc(),
                        PlatformUserRoleAssignment.id.asc()],
    "granted_at_asc": [PlatformUserRoleAssignment.granted_at.asc(),
                       PlatformUserRoleAssignment.id.asc()],
}

TENANT_ASSIGNMENTS_SORT_MAP = {
    "granted_at_desc": [TenantUserRoleAssignment.granted_at.desc(),
                        TenantUserRoleAssignment.id.asc()],
    "granted_at_asc": [TenantUserRoleAssignment.granted_at.asc(),
                       TenantUserRoleAssignment.id.asc()],
}


class RoleAssignmentsRepo:
    """Read-only repository for the two post-split assignment tables."""

    async def list_platform_assignments(
        self,
        session: AsyncSession,
        *,
        role_id: UUID | None = None,
        platform_user_id: UUID | None = None,
        status: UserRoleAssignmentStatus | None = None,
        sort: str = "granted_at_desc",
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[PlatformUserRoleAssignment], int]:
        """List PLATFORM-side assignments. No RLS — visible to all sessions.

        Returns (rows, total). Total is unfiltered-by-pagination count
        (matches existing Repo conventions).
        """
        if sort not in PLATFORM_ASSIGNMENTS_SORT_MAP:
            raise InvalidSortKeyError(f"unknown sort key: {sort}")
        # ... implementation ...

    async def list_tenant_assignments(
        self,
        session: AsyncSession,
        *,
        role_id: UUID | None = None,
        tenant_user_id: UUID | None = None,
        org_node_id: UUID | None = None,
        status: UserRoleAssignmentStatus | None = None,
        sort: str = "granted_at_desc",
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[TenantUserRoleAssignment], int]:
        """List TENANT-side assignments. RLS-scoped per session GUCs."""
        if sort not in TENANT_ASSIGNMENTS_SORT_MAP:
            raise InvalidSortKeyError(f"unknown sort key: {sort}")
        # ... implementation ...
```

Default sort is `granted_at_desc` matching PlatformUsersRepo / TenantUsersRepo precedent. The actual implementation uses standard `count_query + paginated_query` pattern from existing Repos.

### File 7: `src/admin_backend/schemas/role_assignment.py` — NEW

Pydantic schemas for the 6.8.3 endpoint. Defining here so 6.8.3's wire-up is mechanical.

```python
"""Pydantic schemas for /role-assignments endpoint (6.8.3).

Two grouped arrays per the API contract: platform_assignments and
tenant_assignments. Each array's row shape reflects its physical table
(platform has no tenant/org_node fields; tenant does). No row-level
discriminator — the array name carries the audience.

Hidden fields: granted_by_user_id, granted_by_user_type,
revoked_by_user_id, revoked_by_user_type — audit-actor IDs are
internal, follow the Step 6.1 H1 hidden-fields convention.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class _AssignedRole(BaseModel):
    """Inline role mini-object on each assignment row."""
    model_config = ConfigDict(from_attributes=True, extra="forbid")
    id: UUID
    code: str
    name: str
    audience: str  # 'PLATFORM' or 'TENANT'


class _AssignedPlatformUser(BaseModel):
    """Inline platform_user mini-object on platform-side rows."""
    model_config = ConfigDict(from_attributes=True, extra="forbid")
    id: UUID
    email: str
    full_name: str


class _AssignedTenantUser(BaseModel):
    """Inline tenant_user mini-object on tenant-side rows."""
    model_config = ConfigDict(from_attributes=True, extra="forbid")
    id: UUID
    email: str
    full_name: str


class _AssignedTenant(BaseModel):
    """Inline tenant mini-object on tenant-side rows."""
    model_config = ConfigDict(from_attributes=True, extra="forbid")
    id: UUID
    name: str


class _AssignedOrgNode(BaseModel):
    """Inline org_node mini-object on tenant-side rows."""
    model_config = ConfigDict(from_attributes=True, extra="forbid")
    id: UUID
    name: str
    code: str
    node_type: str


class PlatformAssignmentItem(BaseModel):
    """Row in platform_assignments array."""
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    platform_user: _AssignedPlatformUser
    role: _AssignedRole
    status: str
    granted_at: datetime
    revoked_at: datetime | None
    updated_at: datetime


class TenantAssignmentItem(BaseModel):
    """Row in tenant_assignments array."""
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    tenant_user: _AssignedTenantUser
    tenant: _AssignedTenant
    org_node: _AssignedOrgNode
    role: _AssignedRole
    status: str
    granted_at: datetime
    revoked_at: datetime | None
    updated_at: datetime


class RoleAssignmentsResponse(BaseModel):
    """Response shape: two grouped arrays.

    For PLATFORM JWTs: both arrays may be populated.
    For TENANT JWTs: platform_assignments is [] (no PLATFORM-side rows
    visible to a tenant user); tenant_assignments contains only the
    calling tenant's rows (RLS-scoped).
    """
    model_config = ConfigDict(extra="forbid")

    platform_assignments: list[PlatformAssignmentItem] = Field(
        description=(
            "PLATFORM-audience role assignments to platform_users. "
            "Empty array for TENANT JWTs."
        ),
    )
    tenant_assignments: list[TenantAssignmentItem] = Field(
        description=(
            "TENANT-audience role assignments to tenant_users at "
            "org_node anchors. RLS-scoped to the calling tenant for "
            "TENANT JWTs; full list for PLATFORM JWTs."
        ),
    )
```

### File 8: `src/admin_backend/schemas/__init__.py` — MODIFY

Re-export `PlatformAssignmentItem`, `TenantAssignmentItem`, `RoleAssignmentsResponse`.

### File 9: `scripts/seed_dev_data/loaders/user_role_assignments.py` — REWRITE

Current shape: per-row tenant impersonation via `set_config('app.tenant_id', t_id, true)` for TENANT-side rows under FN-AB-14's IS-NULL-gated policy. Routes one INSERT per row to `user_role_assignments`.

New shape: per-row routing to one of two physical tables based on which user-side FK is populated. **No more per-row impersonation** — the unconditional OR-branch on `tenant_user_role_assignments` admits any PLATFORM-session INSERT. The `_set_tenant_guc` helper (or similar) is removed if no other loader uses it.

**Sketch:**

```python
"""Loader for user_role_assignments sheet — routes per row to either
platform_user_role_assignments or tenant_user_role_assignments based on
the audience discriminator (which user-side FK is populated).

Post-split (Step 6.8.1, 6.8.2): the dual-FK XOR is gone at the DB layer.
Each row has exactly one user-side FK; the loader inspects which one and
writes to the matching physical table.

Per-row tenant impersonation (the pre-split pattern under FN-AB-14's
IS-NULL-gated policy) is no longer needed. tenant_user_role_assignments
uses the unconditional OR-branch (D-29); a PLATFORM session writes any
TENANT-side row without setting app.tenant_id.

Audience-check triggers (enforce_platform_role_audience,
enforce_tenant_role_audience) fire per row and would abort the load if
a row's role.audience doesn't match the user-side column. The seed has
been verified consistent at multiple prior steps; this is defensive.
"""
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from scripts.seed_dev_data.column_mappings import validate_columns
from scripts.seed_dev_data.uuid_mapper import UUIDMapper
from scripts.seed_dev_data.loaders._base import build_insert_row

SHEET_NAME = "user_role_assignments"
PLATFORM_TABLE = "platform_user_role_assignments"
TENANT_TABLE = "tenant_user_role_assignments"


async def load(
    session: AsyncSession,
    rows: list[dict[str, Any]],
    mapper: UUIDMapper,
) -> None:
    if rows:
        validate_columns(SHEET_NAME, list(rows[0].keys()))

    for row in rows:
        # Determine routing: which user-side FK is populated?
        platform_user_xid = row.get("platform_user_id")
        tenant_user_xid = row.get("tenant_user_id")

        if platform_user_xid is not None and tenant_user_xid is None:
            # PLATFORM-audience row → platform_user_role_assignments
            target_table = PLATFORM_TABLE
            # Drop tenant_id, org_node_id, tenant_user_id from insert row
            # (they're not columns on the platform table).
            insert_row = build_insert_row(SHEET_NAME, row, mapper)
            for k in ("tenant_id", "org_node_id", "tenant_user_id"):
                insert_row.pop(k, None)
        elif tenant_user_xid is not None and platform_user_xid is None:
            # TENANT-audience row → tenant_user_role_assignments
            target_table = TENANT_TABLE
            # Drop platform_user_id from insert row.
            insert_row = build_insert_row(SHEET_NAME, row, mapper)
            insert_row.pop("platform_user_id", None)
        else:
            raise ValueError(
                f"user_role_assignments seed row with _key={row.get('_key', '?')}: "
                f"exactly one of platform_user_id / tenant_user_id must be populated; "
                f"got platform_user_id={platform_user_xid}, tenant_user_id={tenant_user_xid}"
            )

        columns = list(insert_row.keys())
        placeholders = ", ".join(f":{c}" for c in columns)
        column_list = ", ".join(columns)
        sql = f"INSERT INTO {target_table} ({column_list}) VALUES ({placeholders}) RETURNING id"
        result = await session.execute(text(sql), insert_row)
        db_id = result.scalar_one()
        excel_id = row.get("id")
        if excel_id is not None:
            mapper.register(SHEET_NAME, excel_id, db_id)

    await session.commit()
```

The exact `build_insert_row` calling convention should match what `_base.py` provides — verify against the actual helper. The pattern above is the gist.

### File 10: `scripts/seed_dev_data/column_mappings.py` — MODIFY (docstring/comments only)

The `USER_ROLE_ASSIGNMENTS` SheetMapping STAYS as a single mapping. Update only:
- Module docstring or section comment to reflect the post-split routing.
- Any per-column comments referencing the dual-FK XOR now describe "loader-level routing per row."

### File 11: `scripts/seed_dev_data/runner.py` — VERIFY (likely no change)

The entry for `user_role_assignments` in the loader sequence stays as one symbol. No change expected. Verify and confirm.

### File 12: `scripts/seed_dev_data/truncate.py` — MODIFY

Replace `user_role_assignments` in the truncate list with the two new table names: `platform_user_role_assignments` and `tenant_user_role_assignments`. Order: both are leaves (no inbound FKs); place wherever the original entry was, in either order.

### File 13: `scripts/seed_dev_data/loaders/_base.py` — MODIFY (docstring only)

Locate the docstring reference to "the dual-FK XOR pattern in user_role_assignments" (line ~39 per Step 6.8.1's report). Update to describe the post-split shape: "the user_role_assignments loader routes per row to one of two physical tables based on the audience discriminator."

### File 14: `scripts/seed_dev_data/README.md` — MODIFY

Update the section describing the user_role_assignments loader to reflect the post-split shape. The Excel sheet remains unchanged; the loader routes per row.

### File 15: `tests/integration/test_rbac_router.py` — MODIFY

Three touchpoints (verified against actual file content):

1. **`_insert_active_platform_ura` helper** (lines 138-172): rename to `_insert_active_platform_assignment`. The current INSERT statement explicitly inserts NULLs for `tenant_user_id`, `tenant_id`, `org_node_id` — those columns don't exist on `platform_user_role_assignments`, so they go away in the rewrite. Significant simplification.

   **Replacement sketch:**
   ```python
   async def _insert_active_platform_assignment(
       session_factory: Any,
       platform_auth: Any,
       *,
       role_id: UUID,
       platform_user_id: UUID,
   ) -> UUID:
       """Insert one ACTIVE platform-audience role assignment row.

       Post-split (Step 6.8.1, 6.8.2): platform_user_role_assignments
       has no RLS — any PLATFORM session writes any row directly. The
       audience-check trigger (enforce_platform_role_audience) verifies
       role.audience='PLATFORM' at insert time; tests must use a
       PLATFORM-audience role.

       Returns the new id. Tests must clean up via DELETE in fixture
       teardown.
       """
       new_id = uuid.uuid4()
       async for session in get_tenant_session(platform_auth, session_factory):
           await session.execute(
               text(
                   "INSERT INTO platform_user_role_assignments ("
                   "  id, platform_user_id, role_id, status,"
                   "  granted_by_user_id, granted_by_user_type"
                   ") VALUES ("
                   "  :id, :pu_id, :role_id,"
                   "  CAST('ACTIVE' AS user_role_assignment_status_enum),"
                   "  NULL, NULL"
                   ")"
               ),
               {"id": new_id, "pu_id": platform_user_id, "role_id": role_id},
           )
       return new_id
   ```

   The manual `uuid.uuid4()` (line 155) is preserved — tests that need to know the inserted ID upfront use this pattern. The DEFAULT uuidv7() fires only when `id` is omitted from the INSERT.

2. **`_delete_uras_by_id` helper** (lines 175-188): rename to `_delete_assignments_by_id`. Currently DELETEs from `user_role_assignments`. Update to DELETE from `platform_user_role_assignments` (since this helper only deletes platform-audience assignments — only one call site at line 347, partnered with the platform helper above).

   **Replacement:**
   ```python
   async def _delete_assignments_by_id(
       session_factory: Any,
       platform_auth: Any,
       ids: list[UUID],
   ) -> None:
       if not ids:
           return
       async for session in get_tenant_session(platform_auth, session_factory):
           await session.execute(
               text(
                   "DELETE FROM platform_user_role_assignments WHERE id = ANY(:ids)"
               ),
               {"ids": ids},
           )
   ```

3. **Three call sites of `_insert_active_platform_ura`** (lines 315, 321, 327) and **one call site of `_delete_uras_by_id`** (line 347): rename the function calls to the new names. The arguments don't change.

The 13 R/P/RP/M/H tests using these helpers go green automatically on these renames + the `_user_count_subquery` rewrite from File 5.

### File 16: `tests/integration/test_seed_loader.py` — MODIFY

Five touchpoints (verified against actual file content):

1. **`EXPECTED_VISIBLE_COUNTS_PLATFORM` dict** (lines 33-46): the dict counts rows visible to a PLATFORM session without impersonation. Currently has `"user_role_assignments": 3` (the PLATFORM-audience count visible under the IS-NULL gate; lines 43-45 comment explains this). Replace with:
   ```python
   "platform_user_role_assignments": 3,  # PLATFORM-audience; no RLS, all visible
   "tenant_user_role_assignments": 19,   # TENANT-side; PLATFORM session sees all via D-29 unconditional OR
   ```
   The comments on lines 43-44 about IS-NULL-gated visibility are obsolete; replace them with the new shape's logic.

2. **`EXPECTED_URA_TOTAL` constant** (referenced at line 126; the declaration is around line 48-50 per the grep): currently equals 22 (3 PLATFORM + 19 TENANT). Either:
   - Keep the constant but rename to `EXPECTED_URA_TOTAL_COMBINED` (still meaningful — total assignments across both tables); used by the rewritten l2b.
   - Or remove the constant and inline the value in l2b's assertion.
   Either is fine; pick whichever fits the test's clarity.

3. **`test_l2b_user_role_assignments_total_across_tenants`** (lines 96-130): full rewrite. The original iterates per-tenant impersonation under the IS-NULL-gated form. Post-split: PLATFORM session reads both tables in two queries, sums them, asserts against the expected total. Per-tenant impersonation is gone.

   **Replacement sketch:**
   ```python
   async def test_l2b_role_assignments_total_split_correctly(platform_session):
       """Post-split: PLATFORM session reads both physical tables directly.

       Before Step 6.8.1, user_role_assignments used the IS-NULL-gated
       D-29 form, so PLATFORM-without-impersonation only saw the 3
       PLATFORM-audience rows. This test iterated per-tenant
       impersonation to verify the IS-NULL gate's behaviour.

       Post-split: tenant_user_role_assignments uses the unconditional
       OR-branch (D-29 + D-34), so PLATFORM-without-impersonation sees
       all rows. No iteration needed.
       """
       result = await platform_session.execute(
           text("SELECT count(*) FROM platform_user_role_assignments")
       )
       platform_count = result.scalar_one()

       result = await platform_session.execute(
           text("SELECT count(*) FROM tenant_user_role_assignments")
       )
       tenant_count = result.scalar_one()

       total = platform_count + tenant_count
       assert total == EXPECTED_URA_TOTAL_COMBINED, (
           f"role_assignments split totals: platform={platform_count}, "
           f"tenant={tenant_count}, sum={total}, expected {EXPECTED_URA_TOTAL_COMBINED}"
       )
   ```

   Test name change is optional. The test's role is still validating "all role assignments are accounted for"; the rename clarifies that the IS-NULL-gate verification is gone.

4. **`test_l3_seed_sentinel_rows`** (lines 174-187 — verified URA references): the PLATFORM-audience XOR-shape check on `user_role_assignments` (lines 177-187). The XOR-shape no longer exists post-split; the table doesn't exist either. **Replace with a check on `platform_user_role_assignments`:**

   ```python
   # PLATFORM-audience role assignments now live on platform_user_role_assignments
   # (no RLS; every session sees them). Post-split: no tenant_id, no org_node_id,
   # no tenant_user_id columns to check — just count.
   result = await platform_session.execute(
       text("SELECT count(*) FROM platform_user_role_assignments")
   )
   assert result.scalar_one() >= 3, (
       "Expected at least 3 PLATFORM-audience role assignments"
   )
   ```

   The comment on lines 174-176 (about dual-FK XOR) is obsolete; replace.

5. **All file-level `user_role_assignments` references** (per the grep at lines 17, 43, 45, 48, 82, 96, 99, 108, 121, 127, 174, 179): update each per its context. Most are in test bodies or comments; mechanical replacement.

### File 17: `docs/endpoints/rbac.md` — MODIFY

Locate the `user_count` field description (line 114 per earlier survey). Today reads "Counted via correlated subquery on user_role_assignments where status=ACTIVE." Update to:

> "Counted via UNION over `platform_user_role_assignments` and `tenant_user_role_assignments` where status='ACTIVE'. RLS-scoped on the tenant-side branch for TENANT JWTs."

If line 141 (RLS-scoping note for TENANT JWTs) references the IS-NULL-gated policy, update to reflect the unconditional OR-branch shape.

---

## CLAUDE.md changes this step (per the per-step bundling convention)

Three touchpoints. Smaller surface than 6.8.1 (no schema changes; no new D-XX entries; no FN-AB resolutions).

1. **New "Completed" bullet for Step 6.8.2.** Concise summary mirroring recent Completed bullets. Include: two new ORM models, RolesRepo._user_count_subquery rewrite to UNION, new RoleAssignmentsRepo + schemas (used by 6.8.3), seed loader rewrite (routing, no per-row impersonation), `_set_tenant_guc` helper removed if unused, `_lightweight_stubs.py::UserRoleAssignment` removed (only `Store` stub remains), 17 previously-failing tests now passing, pytest 209+17→ ~226+ post-step.

2. **"Note on canonical write pattern under PLATFORM session" amendment.** CLAUDE.md currently has a paragraph documenting per-row impersonation as the canonical pattern, citing `loaders/user_role_assignments.py` as the reference implementation. Amend to:
   - Note that the per-row impersonation pattern was retired for `user_role_assignments` at Step 6.8.1 (the IS-NULL gate it worked around is gone).
   - Confirm the pattern remains canonical for any FUTURE table that needs an IS-NULL-gated policy form (none planned in v0; D-34's split principle prevents new ones from appearing).
   - The seed loader at `scripts/seed_dev_data/loaders/user_role_assignments.py` is no longer the reference implementation; if any future table needs the pattern, refer to git history.

3. **Lightweight stub state update.** The "lightweight stubs" reference in CLAUDE.md (referencing the Step 5.2 swap of TenantUser, the standing Step 4.5 Store stub, and Step 6.1's UserRoleAssignment stub) updates: UserRoleAssignment removed (Step 6.8.2); only `Store` remains.

---

## BUILD_PLAN.md changes this step

1. **Step 6.8.2 entry.** Status DONE. Standard scope-in / scope-out / acceptance / coordination structure. Include:
   - Scope: ORM models + Repo + schemas + seed loader rewrite + lightweight stub removal + test cutover.
   - Acceptance: 17 previously-failing tests now pass; pytest delta +17 (no other tests changed); seed reset+reseed succeeds; `/roles` endpoint's user_count works via UNION; cross-tenant integrity verification query returns zero rows.
   - Coordination: none (local-only; cloud deploy blocks on 6.8.3).

2. **Step 6.8.3 placeholder entry.** Update from "Blocked by Step 6.8.2" → "Ready for prompt."

---

## architecture.md changes this step

Likely no change. The system shape moved at 6.8.1 (table inventory, multi-tenancy section). 6.8.2 is internal implementation work. Verify by scanning for any architecture-level reference to `user_role_assignments` or to the URA stub; if any surface is found, update.

If no change is required, the report's architecture.md bundle is "no change."

---

## docs/endpoints/rbac.md changes this step

Per File 17 above. The `user_count` field description updates from "correlated subquery on user_role_assignments" to "UNION over platform_user_role_assignments and tenant_user_role_assignments." This is the only endpoint doc touching this work in 6.8.2; the new `/role-assignments` endpoint doc (`docs/endpoints/role-assignments.md`) lands in 6.8.3.

---

## Verification harness (run all in order; all must be green)

**Note on test ordering:** `test_seed_loader.py::test_l1_seed_runs_clean_end_to_end` runs the seed loader and is the data-setup precondition for `test_l2`, `test_l2b`, `test_l3`. Standard pytest collection order (file order) places L1 first; subsequent tests inherit the post-seed state. If any test in this file is moved out of order, the others fail with empty-table assertions. Verify the test order matches `L1 → L2 → L2b → L3 → L4`.

**Note on RBAC router tests:** they use the `_insert_active_platform_assignment` (renamed) helper to insert their own test data via raw SQL, then teardown via `_delete_assignments_by_id`. They do NOT depend on seed data and run cleanly against any DB state (empty or seeded).

```bash
# 1. Codebase compiles (mypy strict)
uv run mypy src/admin_backend/
# Expected: clean. The two new ORM models, the rewritten _user_count_subquery,
# the new RoleAssignmentsRepo, and the new schemas all type-check.

# 2. Seed reset + reseed succeeds end-to-end. ALSO sets up data for L1/L2/L2b/L3.
uv run python -m scripts.seed_dev_data --reset
# Expected: clean run. All 10 sheets load. The user_role_assignments loader
# routes 3 rows to platform_user_role_assignments, 19 to
# tenant_user_role_assignments. No per-row impersonation. No errors.

# 3. Cross-tenant integrity verification (NEW — Q2 addition)
psql "$DATABASE_URL" -c "
SET search_path TO core, public;
SELECT t.id, t.tenant_id AS row_tenant,
       tu.tenant_id AS user_tenant,
       on_.tenant_id AS org_node_tenant
FROM tenant_user_role_assignments t
LEFT JOIN tenant_users tu ON t.tenant_user_id = tu.id
LEFT JOIN org_nodes on_ ON t.org_node_id = on_.id
WHERE t.tenant_id != tu.tenant_id
   OR t.tenant_id != on_.tenant_id;"
# Expected: ZERO rows. The composite FKs guarantee this structurally;
# this query is the empirical confirmation. Same query becomes the
# pre-deploy data-shape check on Cloud SQL (deploy runbook).

# 4. /roles endpoint's user_count works
uv run uvicorn admin_backend.main:app &
sleep 2
curl -s http://localhost:8000/api/v1/roles -H "Authorization: Bearer <PLATFORM_JWT>" | jq '.platform_roles[0].user_count'
# Expected: a non-zero integer (the seed has assignments).

# 5. R4 specifically (load-bearing correlate-on-UNION test)
uv run pytest tests/integration/test_rbac_router.py::test_r4_user_count_aggregate_correlates_per_role -v
# Expected: PASS. If FAIL, the .correlate(Role) is missing on one or both
# UNION branches.

# 6. Full pytest run
# Run AFTER step 2's seed has populated data; some tests (test_l1, test_l2, test_l2b, test_l3)
# require post-seed state.
uv run pytest -v
# Expected: pre-step pytest baseline (226 PASS = 209 functioning tests + 17 previously-
# failing-but-now-fixed). All 17 named failing tests now PASS. No other test changes state.
# Specifically:
#   - 13 R/P/RP/M/H tests in test_rbac_router.py: previously red, now green (helper fix).
#   - 4 L tests in test_seed_loader.py: previously red, now green (loader rewrite + EXPECTED dict).
# If a test that wasn't in the 17-list goes red, surface as unexpected regression.
# If a test in the 17-list stays red, surface as incomplete cutover.

# 7. Smoke test
uv run python scripts/smoke_test.py
# Expected: 81 PASS (unchanged from 6.8.1 — no schema changes this step).

# 8. check_setup
./scripts/check_setup.sh
# Expected: 35/35.
```

---

## Regression risk surface introduced by this step

1. **`.correlate(Role)` discipline on TWO subqueries.** Forgetting it on either branch causes R4 to fail with the per-row-equals-global-total symptom. Same trap as L9/L11/R4 history — third occurrence on the same pattern, second occurrence on the user_count subquery specifically.

2. **The lightweight-stub swap order matters.** If the new ORM models land before the Repo rewrite, the codebase has both `UserRoleAssignment` (stub, broken) and the new models present simultaneously — `_user_count_subquery` references the stub, which references the dropped table, so the build is still broken. Land File 5 (Repo rewrite) BEFORE removing File 3 (stub class) — the dependency direction matters. Or land them in the same edit if the helper supports it.

3. **Audience-check trigger fires on seed re-run.** If the seed Excel has a row where `role.audience` doesn't match the user-side column (e.g., a tenant_user_id row pointing at a PLATFORM-audience role), the audience-check trigger raises and the seed aborts. The seed has been verified consistent at multiple prior steps, but if the trigger fires, surface the row.

4. **Composite FK on tenant_user_id during seed re-run.** Same risk surface as 6.8.1's data copy — if seed Excel has any row where `tenant_user.tenant_id != row.tenant_id`, the composite FK rejects the INSERT and the seed aborts. Surface and stop-and-ask.

5. **`_set_tenant_guc` helper removal.** Before deleting, `grep -r '_set_tenant_guc' scripts/ src/ tests/` to confirm no other caller. If any caller exists outside `loaders/user_role_assignments.py`, surface — do not silently break that caller.

6. **`build_insert_row` helper signature.** The new routing loader calls `build_insert_row` then mutates the returned dict (popping platform/tenant fields per row). Verified safe: `_base.py:build_insert_row` constructs and returns a fresh dict per call (no shared state). Worth re-confirming during implementation — read the helper before adopting the mutation pattern.

7. **Cross-tenant integrity verification query (Q2 addition).** This query MUST return zero rows post-reseed. If it returns non-zero, the seed Excel itself has cross-tenant data (separate from any code issue) and the composite FK should have caught it. If the FK didn't catch it but this query does, that's evidence of a deeper schema issue.

8. **`docs/endpoints/rbac.md` description update.** Frontend reads this doc. The user_count description change is content-only; the field shape and value are unchanged.

---

## Scope out

- **`/role-assignments` router and endpoint** — Step 6.8.3.
- **docs/endpoints/role-assignments.md** — Step 6.8.3.
- **docs/endpoints/openapi.json regeneration** — Step 6.8.3 (no new endpoint shape this step).
- **BUILD_PLAN.md Step 6.1 "Known follow-ups (RBAC)" E4/E5 URL update** — Step 6.8.3.
- **Cloud SQL migration / deploy.** Local-only this step.
- **Cloud SQL pre-deploy runbook (cross-tenant integrity, backup, etc.).** Separate deliverable; written before the eventual Cloud SQL deploy of the 6.8.x bundle.
- **Audit_logs (Step 6.2) precedent.** D-34 is established; no work on audit_logs in this step.

---

## Stop and ask if

1. **`alembic heads` reports something other than `3e05299cb533`.** Surface the actual head; we need to confirm whether 6.8.1 is the latest landed migration.
2. **Pre-flight item 5 finds the DB has data.** Surface; we need to know whether someone reseeded between 6.8.1 and this step (in which case the post-step verification needs to account for that).
3. **Pytest baseline failure count is not exactly 17, OR the failing test names don't match the list in Context.** Either an unexpected regression has been introduced, or 6.8.1 left behind something not yet captured. Surface the actual list.
4. **`_set_tenant_guc` (or equivalent) has callers outside `loaders/user_role_assignments.py`.** Surface the callers; we'll decide whether to keep the helper, refactor the callers, or split the cleanup.
5. **The audience-check trigger fires during seed re-run.** Surface the row; that's a real data integrity issue in the Excel seed.
6. **The composite FK rejects a row during seed re-run.** Same as 6.8.1's stop-and-ask: surface the rejected row's data; manual decision.
7. **Cross-tenant integrity verification query returns non-zero rows.** Surface the rows; this should be impossible per the composite FK, so any non-zero count is a deeper issue.
8. **R4 fails after the `_user_count_subquery` rewrite.** That means `.correlate(Role)` is missing on at least one UNION branch. Investigate; do not "fix" by removing R4.
9. **A test that wasn't in the 17-failing list goes RED after this step's changes.** That's an unexpected regression. Surface the test name + failure mode.
10. **A test that WAS in the 17-failing list stays RED after this step's changes.** That's incomplete cutover. Surface; investigate whether the test references something not yet rewritten.
11. **`build_insert_row` returns a fresh dict** — verified by reading `_base.py` (constructed inline from the spec list per call). The routing loader's pop-mutation pattern is safe. **No action; documenting for clarity.**
12. **`ActorUserType` location is something OTHER than `models/tenant_user.py:73`** (e.g., somebody moved it, or it doesn't exist there anymore). Verified at investigation time but worth re-checking. Surface if the import fails.
13. **The CLAUDE.md / BUILD_PLAN.md / architecture.md changes feel like they should be deferred to a later step.** They should not be. Per the per-step bundling convention, this step's commit must include them. If you find yourself wanting to defer, that's a signal to stop and surface.

---

## Acceptance criteria

- Two new ORM model files created (`platform_user_role_assignment.py`, `tenant_user_role_assignment.py`) following the file-per-table convention; correct PG_ENUM declarations; correct FetchedValue defaults.
- `_lightweight_stubs.py::UserRoleAssignment` removed; `Store` stub remains.
- `models/__init__.py` re-exports updated.
- `RolesRepo._user_count_subquery` rewritten to UNION over both new tables; `.correlate(Role)` on each branch.
- New `RoleAssignmentsRepo` with two list methods.
- New schemas (`PlatformAssignmentItem`, `TenantAssignmentItem`, `RoleAssignmentsResponse`) defined and re-exported.
- Seed loader (`loaders/user_role_assignments.py`) rewritten as routing loader; per-row tenant impersonation removed; `_set_tenant_guc` helper removed if unused.
- `column_mappings.py`, `truncate.py`, `_base.py`, `README.md` updated to reflect post-split shape.
- `test_rbac_router.py` helpers renamed and rewritten; 13 R/P/RP/M/H tests passing.
- `test_seed_loader.py` `EXPECTED` dict updated; `test_l2b_*` rewritten; PLATFORM-audience count test updated; sentinel-row assertions verified; 4 L tests passing.
- `docs/endpoints/rbac.md` `user_count` field description updated.
- `python -m scripts.seed_dev_data --reset` succeeds; the 22 URA rows re-seed correctly (3 to platform, 19 to tenant).
- Cross-tenant integrity verification query returns zero rows.
- mypy strict clean.
- `./scripts/check_setup.sh` 35/35.
- Smoke test 81 PASS (unchanged from 6.8.1).
- pytest count: pre-step + 17 = post-step PASS. No new failures, no test that wasn't in the 17-list now passes for unrelated reasons.
- alembic head unchanged at `3e05299cb533` (no migration this step).
- CLAUDE.md, BUILD_PLAN.md, architecture.md updated per their respective sections.

---

## Report (BEFORE proposing commit)

Five bundles per the workflow convention:

1. **Code/tests:**
   - File-by-file line counts (NEW vs MODIFIED).
   - The post-rewrite `_user_count_subquery` source (10-15 line excerpt) so the `.correlate(Role)` discipline is visible at review time.
   - The post-rewrite seed loader's routing logic (10-15 line excerpt).
   - Pre-step pytest baseline (count + the 17 failing test names).
   - Post-step pytest result (count; confirm exactly the 17 named tests went green; no others changed state).
   - R4-specific test result.
   - Cross-tenant integrity query result (must be zero rows).
   - Seed reset+reseed output (success/error; row counts).
   - mypy / smoke / check_setup status.

2. **CLAUDE.md updates:** which sections were touched (Completed bullet, canonical-write-pattern amendment, lightweight-stub state update); diff stat.

3. **BUILD_PLAN.md updates:** Step 6.8.2 status DONE; Step 6.8.3 marked Ready.

4. **architecture.md updates:** "no change" or list the edit.

5. **`docs/endpoints/rbac.md`:** the `user_count` field description diff.

6. **Prompt file:** `prompts/step-6_8_2-orm-repos-loader-2026-05-09.md` confirmed in commit set.

Plus, in the report (not in the commit):
- Output of pre-flight items 1-5 (check_setup, git log, alembic head/current, DB state).
- Pre-flight item 20's pytest baseline.
- Verification harness output for all 8 commands.
- Any deviations from this prompt's procedure and why.
- Any incidental findings encountered during reading (don't fix; record).

Wait for explicit authorisation before staging or committing.

---

## After completing

When operator authorises (after reviewing the report), propose a Pattern A commit per CLAUDE.md "After completing a task":

```
git status
git add -A
git commit -m "Step 6.8.2: ORM models + Repos + schemas + seed loader for post-split URA tables

- New ORM models PlatformUserRoleAssignment, TenantUserRoleAssignment (file-per-table per project convention; PG_ENUM declarations; FetchedValue defaults; composite FKs at DB layer not SA layer per existing precedent).
- _lightweight_stubs.py::UserRoleAssignment removed; only Store stub remains (Step 4.5 hasn't shipped).
- RolesRepo._user_count_subquery rewritten as UNION over two correlated subqueries; .correlate(Role) on each branch (third occurrence of the L9/L11/R4 trap; R4 verifies).
- New RoleAssignmentsRepo with two list methods (used by Step 6.8.3's /role-assignments endpoint).
- New schemas PlatformAssignmentItem, TenantAssignmentItem, RoleAssignmentsResponse (used by Step 6.8.3).
- Seed loader (loaders/user_role_assignments.py) rewritten as routing loader: per-row inspection of which user-side FK is populated, route to platform_user_role_assignments or tenant_user_role_assignments. Per-row tenant impersonation removed (no longer needed under unconditional OR-branch). <_set_tenant_guc helper removed/retained per actual outcome>.
- column_mappings.py, truncate.py, _base.py, README.md updated to reflect post-split shape.
- test_rbac_router.py: _insert_active_platform_ura → _insert_active_platform_assignment; writes to platform_user_role_assignments. 13 R/P/RP/M/H tests now pass.
- test_seed_loader.py: EXPECTED dict updated; test_l2b_* rewritten for unconditional-OR shape; 4 L tests now pass.
- docs/endpoints/rbac.md: user_count field description updated to reflect UNION-over-two-tables computation.
- pytest <X> -> <Y> PASS (+17 known failures fixed). Seed reset+reseed clean (3 platform + 19 tenant). Cross-tenant integrity query returns 0 rows.
- CLAUDE.md: Step 6.8.2 Completed bullet; canonical-write-pattern amendment (per-row impersonation pattern retired for URA); lightweight-stub state update.
- BUILD_PLAN.md: Step 6.8.2 status TODO -> DONE; Step 6.8.3 Ready.
- architecture.md: <change description or 'no change'>.
"
```

Substitute actual counts and outcomes. Ask operator: "Run? yes / no / edit message".

After commit lands: Step 6.8.3 (`/role-assignments` router + endpoint + tests + docs) is unblocked. Do not auto-chain — wait for operator direction.

---

## End of prompt
