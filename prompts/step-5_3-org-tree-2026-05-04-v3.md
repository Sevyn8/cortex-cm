# Prompt — Step 5.3: Org-tree read surface (lazy-load with smart defaults)

> Generated 2026-05-04 (v3). Replaces v1 (full-tree-always design) and v2 (smart-default with retry-loop and helper-spec issues). v3 fixes: bounded retry loop, explicit `node_exists` method spec, clarified `loaded_children` semantics, mixed-depth test added, navigation TOC, READ FULLY markers on critical sections.
> Paste this entire block into a fresh Claude Code session to start Step 5.3.
> Two endpoints back the Organization Tree page (Frontend spec 7.3): (E2) `GET /api/v1/tenants/{tenant_id}/org-tree` returning a smart-defaulted tree (full for small tenants, depth-limited for large), and (E3) `GET /api/v1/tenants/{tenant_id}/org-nodes/{node_id}/children` for lazy-loading children of a specific node when the user expands it. The previously-planned `num_nodes` augmentation on `/api/v1/tenants` is dropped from this step (parked post-v0 per design conversation 2026-05-04).

---

## Navigation (long prompt — ~1,600 lines)

| Section | Status |
|---|---|
| Context (why this step, why now, why design changed) | Read normally |
| **Locked decisions table** | **READ FULLY** |
| Pre-flight (18 items) | **READ FULLY** |
| Step ID and intent | Read normally |
| Source-of-truth specification (response shapes + invariants) | **READ FULLY** |
| File 1: `OrgNode` ORM model | Read normally |
| File 2: models/__init__ | Skim |
| **File 3: Pydantic schemas** | **READ FULLY** |
| File 4: schemas/__init__ | Skim |
| **File 5: `OrgNodesRepo` + SQL strategy** | **READ FULLY** |
| **File 6: Routers + `_build_tree` helper** | **READ FULLY** |
| File 7: routers/__init__ wiring | Skim |
| File 8: conftest factory | Read normally |
| File 9: 20 integration tests | Read normally |
| Files 10-15: docs, BUILD_PLAN, CLAUDE.md, openapi snapshot | Read normally |
| Testing and regression discipline | Read normally |
| Verification harness (5 commands all green) | **READ FULLY** |
| Stop and ask if (12 triggers) | **READ FULLY** |
| Acceptance criteria | **READ FULLY** |
| Report (BEFORE proposing commit) | Read normally |

**Critical decision points** for Claude Code (latitude given; surface the chosen path in the report):
- **DP-1** (File 5): SQL strategy — combined CTE-with-CTE vs split count + LEFT JOIN vs full-fetch + Python trim.
- **DP-2** (File 5): Whether `count_active_by_tenant` is its own method or inlined.
- **DP-3** (File 5): How `node_exists` is implemented — separate query vs combined into `list_children_paginated`.
- **DP-4** (File 6): Auto-reduction retry strategy in E2 — single retry vs bounded loop (cap at 2).
- **DP-5** (File 6): `_build_tree` pass count — three-pass for clarity vs single-pass for terseness.

For each, the prompt has a leaning recommendation. Claude Code should pick + briefly justify; or pick the leaning recommendation if no strong reason to deviate.

---

## Context: why this step exists, why now, and why the design changed

The admin frontend's Organization Tree page renders a two-column layout: tenant list on the left (pagination already shipped at Step 3.3), full org tree on the right for whichever tenant is selected.

The page's read surface needs two API calls per tenant selection: the tenant card fetch (already shipped) and the tree fetch (this step). However, the original v1 plan proposed "always return full tree." That design has a scaling problem the v2 design fixes:

- A flat-shaped tenant (50 stores under HQ): tree = ~50 nodes, full-tree response is ~15 KB. Fast.
- A regional tenant (Buc-ee's-shape): tree = ~30 nodes. Fast.
- A multinational (Walmart-shape, 3000+ nodes across HQ → BU → Country → Region → Store → Department): full-tree response is ~1 MB raw, ~250 KB compressed. Slow on mobile, painful to render in React without virtualization, and Pydantic recursive serialization adds 200-500ms.

The v2 design lets the server pick: **full tree for small tenants (≤500 nodes); depth-limited tree for large ones, with frontend lazy-fetching children on expand**. Frontend's flow is the same in both modes; the response shape carries `has_children`, `child_count`, and `loaded_children` flags telling the frontend which subtrees to lazy-fetch.

Critical design decisions locked during the 2026-05-04 design conversation:

| Decision | Value | Reasoning |
|---|---|---|
| Full-tree threshold | ≤500 nodes | Compressed payload <50 KB; renders in <500ms on average hardware |
| Default depth (lazy mode) | 4 | Covers 4-layer hierarchies fully (HQ + 2 mid + leaf); 5-6 layer hierarchies show top 4 levels |
| Max depth via `?depth=` | 6 | Realistic max; deeper is pathological |
| Hard payload cap | 1,000 nodes | Server auto-reduces depth if exceeded; truncated flag set |
| E3 default `limit` | 100 | ~30 KB compressed; covers typical region |
| E3 max `limit` | 200 | Bounded server cost |
| `num_nodes` on `/tenants` | DROPPED | Per design conversation; parked post-v0. D-31 means future addition won't break |
| Smart-default on E2 | Yes | Frontend doesn't pass any param by default; server picks full vs depth-limited based on tenant size |

**Latitude given to Claude Code** for backend implementation decisions:
- SQL query strategy (single CTE-with-window vs separate count + LEFT JOIN vs other patterns).
- Whether the threshold count + tree query combine into one round trip or stay as two.
- `_build_tree` helper's exact signature (takes raw rows + child-count map separately vs takes pre-joined records).
- Depth auto-reduction strategy when the requested depth would exceed payload cap.
- Edge cases with inconsistent-depth subtrees (some branches deeper than others).

Each of these has performance/complexity tradeoffs. Surface the chosen approach in the report (Phase D). For each, briefly explain the alternative considered and why the chosen path wins on perf-vs-complexity.

---

## Pre-flight

1. Run `./scripts/check_setup.sh`. Expect 35/35.
2. `git log --oneline -5` — confirm Step 5.2 (`tenant-users` resource) at HEAD. The most recent migration in the alembic chain should still be `0644a4186e48` (Step 3.6); Step 5.2 added no migration.
3. `uv run alembic heads` — confirm output is exactly `0644a4186e48 (head)`. This step adds no migration.
4. **Verify ltree extension and operators are available:**

   ```bash
   uv run python -c "
   import asyncio
   from admin_backend.config import get_settings
   from admin_backend.db.engine import create_engine
   from sqlalchemy import text

   async def main():
       e = create_engine(get_settings())
       async with e.connect() as c:
           await c.execute(text(\"SELECT set_config('app.user_type', 'PLATFORM', false)\"))
           # Verify ltree is installed and nlevel works
           r = await c.execute(text(\"SELECT nlevel('a.b.c'::ltree)\"))
           print('nlevel(a.b.c):', r.scalar())  # expect 3
       await e.dispose()
   asyncio.run(main())
   "
   ```

   If this fails with "function nlevel does not exist" or similar, surface CSD-02 (extensions). For dev/local: `CREATE EXTENSION IF NOT EXISTS ltree;` should already have run at Step 1.4. For Cloud SQL dev: handled per CSD-02. Don't proceed if nlevel is unavailable.

5. Read `CLAUDE.md` fully. Focus on:
   - **D-13** — audit-actor patterns. `org_nodes` is Pattern (b): paired columns `*_by_user_id` + `*_by_user_type` for created/updated/archived. **Hide all six audit-actor columns from the response shape**, same hide-policy as Step 3.3 used for `tenants`.
   - **D-15** — DB_SCHEMA from environment, search_path-driven name resolution.
   - **D-17** — RLS-blocked / missing reads surface as 404 at the handler boundary. The 404 path for E2 and E3 is: tenant doesn't exist OR exists-but-RLS-filtered OR (E3 only) node_id doesn't exist within that tenant.
   - **D-21** — UUIDv7 via `uuidv7()` PL/pgSQL function. No relevance to read endpoints.
   - **D-29** — PLATFORM RLS visibility via OR-clause. `org_nodes` carries the unconditional shape (tenant_id NOT NULL); already in place since Step 3.0. Both PLATFORM and TENANT JWTs work with the existing policy.
   - **D-30** — list-only response envelope. **E2 is a deliberate D-30 exception** (singleton resource per tenant, not a paginatable collection); response shape is `{tenant_id, tenant_name, stats, tree}`. **E3 follows D-30** (paginated children list); response shape is `{node_id, items, pagination}`. The E2 exception is captured in CLAUDE.md File 14 as a one-line note alongside the existing PG_ENUM and batch-by-key envelope notes; do not propose a new D-XX.
   - **D-31** — response field semantics are append-only.
   - **"Note on the v0 auth model"** — both endpoints follow the **multi-user-type** pattern (no PLATFORM-only gate; RLS does isolation). Mirror the `tenant-users` router's auth shape, NOT the `platform-users` router's.
   - **"Note on PG enum columns"** — `node_type` is a PG enum (`org_node_type_enum`) and `status` is `org_node_status_enum`; the ORM model uses `postgresql.ENUM(name="...", create_type=False)` per convention.
   - **"Per-endpoint documentation"** — `docs/endpoints/org-tree.md` follows the 8-section pattern from `docs/endpoints/tenants.md`.
6. Read `docs/architecture.md` "Schema and storage" section — confirms `org_nodes` is the 6th tenant-scoped table with RLS.
7. Read `db/raw_ddl/Ithina_postgres_SQL_DDL_org_nodes_v2.sql` fully — column shape, the two PG enums (`org_node_type_enum`, `org_node_status_enum`), the composite FK guarantee that `parent_id` references a node within the same tenant, the ltree GIST index. **Confirm exact label characters allowed in path** (alphanumeric + underscore for ltree; the loader translates hyphens in `code` to underscores when building path). **Verify index covering `(tenant_id, parent_id)` exists** by inspecting the DDL. Run this confirmation query against the local DB:

   ```bash
   psql $DATABASE_URL -c "
   SELECT indexname, indexdef
   FROM pg_indexes
   WHERE tablename = 'org_nodes'
   ORDER BY indexname;
   "
   ```

   E3's `list_children_paginated` query uses `WHERE tenant_id = ? AND parent_id = ?`. With both columns indexed (whether jointly or in a multi-column index that has them as leading columns), the query is efficient. If neither covers (tenant_id, parent_id), the query falls back to a per-tenant scan filtered by parent_id. For Walmart-shape tenants (~5K nodes), this is ~5K row scan per E3 call.

   **If the index doesn't exist, surface and report. We have three options:**
   - (a) Accept the scan cost for v0; revisit if profiling shows a problem.
   - (b) Add an index migration (against the no-migration-this-step default; would expand scope).
   - (c) Restructure the query to use a different filter shape that an existing index covers.

   Lean: (a) for v0 since absolute scan size is bounded by tenant data volume.
8. Read `db/raw_ddl/Ithina_postgres_SQL_DDL_tenants_v3.sql` — confirms `tenants.id` and `org_nodes.tenant_id` semantics. `org_nodes` rows of type `TENANT` carry `tenant_id = tenants.id` (the tenant references itself).
9. Read `src/admin_backend/models/_lightweight_stubs.py` — confirm whether an org_nodes stub exists. If yes, it's replaced by the full ORM model in this step. If no, the new model is greenfield.
10. Read `src/admin_backend/models/tenant_module_access.py` (Step 3.4.5 ORM) and `src/admin_backend/models/tenant_user.py` (Step 5.2 ORM). The new `OrgNode` model mirrors their shape exactly: `__table_args__ = {"schema": settings.db_schema}`, `FetchedValue()` defaults on `id`, `created_at`, `updated_at`, `path`; `postgresql.ENUM(..., create_type=False)` for `node_type` and `status`; raw UUID columns for audit-actor fields (no relationship() — D-13 Pattern b discriminator means polymorphic FK, no clean SA relationship).
11. Read `src/admin_backend/repositories/tenants.py` and `src/admin_backend/repositories/tenant_users.py`. The new `OrgNodesRepo` follows their shape directly: stateless singleton, `__init__(self, session: AsyncSession)`, async methods only. **Repo gets THREE methods this step:** `count_active_by_tenant`, `list_active_by_tenant_with_child_counts` (full tree), `list_active_to_depth_with_child_counts` (depth-limited tree), `list_children_paginated`. (Possibly four; `count_active_by_tenant` may be inlined into the tree methods — Claude Code's call.)
12. Read `src/admin_backend/routers/v1/tenant_users.py` — the new org-tree router mirrors this for: (a) the auth dependency import line, (b) the session-getter dependency name (`get_tenant_session_dep`), (c) the multi-user-type pattern, (d) error-class import shape, (e) `response_model` declarations, (f) `summary` + `description` decorator hygiene. **Do not assume names; copy from the file.**
13. Read `src/admin_backend/schemas/tenant_user.py` — the new `OrgNode*` schemas mirror this file's shape (Pydantic v2 `model_config = ConfigDict(from_attributes=True)`, no aliasing, audit-actor columns hidden).
14. Read `tests/integration/test_tenant_users_router.py` — particularly **T9** (`test_t9_cross_tenant_detail_returns_404`, the canonical RLS-as-404 test) and the `client` + `_platform_jwt` + `_tenant_jwt` fixture machinery.
15. Read `tests/integration/conftest.py` — `make_tenant`, `make_store`, `make_tenant_user`, `make_platform_user` factories. A new `make_org_node` factory is added in this step. Mirror `make_store`'s raw-SQL-INSERT pattern (the lightweight Store stub didn't declare every NOT NULL column; org_nodes has the same property). The factory must build the ltree path correctly: TENANT-root has path = lowercased(code); non-TENANT nodes have path = parent_path + "." + lowercased(code with hyphens→underscores).
16. Read `BUILD_PLAN.md` Step 5.3 in full. Status TODO. The original entry's scope-in/acceptance is rewritten by this step's commit (the four endpoints become two; the descendants raw-SQL example is removed; the cut endpoints documented as "out of scope; no UI consumer; lazy-fetch via E3 covers the use cases").
17. Read `docs/endpoints/tenant-users.md` — the closest precedent for endpoint documentation. Frontend-facing 8-section structure; new `docs/endpoints/org-tree.md` mirrors this.
18. Read `src/admin_backend/errors.py` — verify the existence and signatures of:
    - `TenantNotFoundError` (used by E2 + E3 when tenant doesn't exist or is RLS-filtered).
    - A `NotFoundError` base or generic class that supports a `code` parameter (used by E3 for `code='ORG_NODE_NOT_FOUND'`). Or whatever the existing pattern is for resource-specific 404s — mirror Step 5.2's pattern for the analogous tenant-user-not-found case.
    
    **Stop and ask if** the existing pattern requires creating a new class (`OrgNodeNotFoundError`) vs reusing `NotFoundError(code=...)` vs another approach.
19. Read this prompt fully.

---

## Step ID and intent

**Step 5.3** — Org-tree read surface, lazy-load with smart defaults. Two new endpoints, both backing the Organization Tree page (Frontend spec 7.3).

Eight concrete deliverables:

1. **`OrgNode` ORM model** (full, not lightweight).
2. **`OrgNode*` Pydantic schemas** — `OrgNodeTreeItem` (recursive, with `children` + lazy-load metadata), `OrgTreeStats`, `OrgTreeResponse`, `OrgNodeChildrenResponse`.
3. **`OrgNodesRepo`** — three async methods: `count_active_by_tenant`, `list_active_by_tenant_with_child_counts` (or split — Claude Code's call), `list_children_paginated`.
4. **Pure-functional `_build_tree` helper** — takes flat path-ordered rows + child-count info, returns nested tree structure. Reusable from both E2 endpoint paths (full and depth-limited).
5. **Routers**: `routers/v1/org_tree.py` for E2, possibly E3 colocated or in `routers/v1/org_nodes.py`. Multi-user-type. RLS-as-404 per D-17.
6. **Integration tests** — new `tests/integration/test_org_tree_router.py` with ~14-16 tests covering smart-default behavior, depth params, payload cap, lazy-load metadata, sibling order, cross-tenant 404, E3 pagination, edge cases.
7. **`docs/endpoints/org-tree.md`** — 8-section format; covers both E2 and E3.
8. **OpenAPI snapshot regenerated** so frontend codegen picks up both endpoints.

CLAUDE_CODE step. No DDL changes (org_nodes table already exists since Day 1; RLS policy already includes the D-29 OR-clause since Step 3.0). No migration. Same complexity envelope as Step 5.2 plus the lazy-load logic.

---

## Source-of-truth specification

### E2 response shape — locked

`GET /api/v1/tenants/{tenant_id}/org-tree?depth={N}` (depth optional)

**Small tenant (≤500 nodes), no depth param:**

```jsonc
{
  "tenant_id": "0192e1a0-0000-7000-aaaa-000000000000",
  "tenant_name": "Buc-ee's",
  "stats": {
    "total_nodes": 8,
    "nodes_returned": 8,
    "stores": 3,
    "regions": 2,
    "depth_returned": 4,
    "truncated": false
  },
  "tree": [
    {
      "id": "...",
      "node_type": "HQ",
      "name": "Buc-ee's HQ",
      "code": "BU-HQ",
      "status": "ACTIVE",
      "created_at": "2026-04-19T15:00:00Z",
      "updated_at": "2026-04-19T15:00:00Z",
      "has_children": true,
      "child_count": 2,
      "loaded_children": "all",
      "children": [
        {
          "id": "...",
          "node_type": "REGION",
          "name": "Florida Region",
          "code": "FL",
          "status": "ACTIVE",
          "has_children": true,
          "child_count": 1,
          "loaded_children": "all",
          "children": [ /* ... */ ]
        },
        // sibling region "TX" ordered after "FL" (alphabetical by lowercased code = path-ASC)
      ]
    }
  ]
}
```

**Large tenant (>500 nodes), default depth = 4:**

```jsonc
{
  "tenant_id": "...",
  "tenant_name": "GlobalRetailer Co.",
  "stats": {
    "total_nodes": 3247,
    "nodes_returned": 156,
    "stores": 0,
    "regions": 84,
    "depth_returned": 4,
    "truncated": false
  },
  "tree": [
    {
      "id": "...",
      "node_type": "HQ",
      "name": "GR Headquarters",
      "has_children": true,
      "child_count": 5,
      "loaded_children": "all",
      "children": [
        {
          "node_type": "BUSINESS_UNIT",
          "has_children": true,
          "child_count": 6,
          "loaded_children": "all",
          "children": [
            {
              "node_type": "COUNTRY",
              "has_children": true,
              "child_count": 14,
              "loaded_children": "all",
              "children": [
                {
                  "node_type": "REGION",
                  "has_children": true,
                  "child_count": 220,        // 220 stores under this region
                  "loaded_children": "none", // ⚠️ NOT loaded (depth limit reached)
                  "children": []             // empty until E3 fetches them
                }
                // ... more regions
              ]
            }
          ]
        }
      ]
    }
  ]
}
```

**Empty-tenant variant** (tenant exists, has zero org_nodes — architecturally invalid but DDL-permissive):

```jsonc
{
  "tenant_id": "...",
  "tenant_name": "FreshMart Co-op",
  "stats": {
    "total_nodes": 0, "nodes_returned": 0,
    "stores": 0, "regions": 0,
    "depth_returned": 0, "truncated": false
  },
  "tree": []
}
```

**Truncated variant** (large tenant where depth=4 still exceeds 1000 nodes; server auto-reduces to depth=3):

```jsonc
{
  "tenant_id": "...",
  "tenant_name": "...",
  "stats": {
    "total_nodes": 5840,
    "nodes_returned": 980,
    "stores": 0,
    "regions": 0,
    "depth_returned": 3,           // server reduced from 4
    "truncated": true              // ⚠️ frontend should display a notice
  },
  "tree": [ /* depth-3 tree */ ]
}
```

### E3 response shape — locked

`GET /api/v1/tenants/{tenant_id}/org-nodes/{node_id}/children?offset={N}&limit={N}` (default limit=100, max 200)

```jsonc
{
  "node_id": "abc...",                   // echo of path-param parent
  "items": [
    {
      "id": "...",
      "node_type": "STORE",
      "name": "GR Store #4521",
      "code": "GR-CA-LA-4521",
      "status": "ACTIVE",
      "created_at": "...",
      "updated_at": "...",
      "has_children": true,             // departments below
      "child_count": 5,
      "loaded_children": "none",        // not loaded; further E3 call needed
      "children": []
    }
    // ... up to 100 items
  ],
  "pagination": {
    "total": 220,
    "offset": 0,
    "limit": 100
  }
}
```

### Locked invariants

| # | Invariant | Tested in |
|---|---|---|
| I1 | TENANT-type nodes never appear in E2's `tree` or E3's `items`. | T1, T2 |
| I2 | Every returned node has `status = 'ACTIVE'`. v0 only. | T1 |
| I3 | `children: []` for "no children loaded" AND for "truly no children" — distinguished by `has_children` flag. | T1, T6 |
| I4 | `stats.total_nodes` = full count of non-TENANT ACTIVE nodes for the tenant (regardless of depth limit). | T1, T7 |
| I5 | `stats.nodes_returned` = count of nodes actually present in `tree` (recursively). | T7, T8 |
| I6 | `stats.stores` and `stats.regions` count only nodes IN THE RESPONSE (not full tree). Frontend uses these for the right-pane header badges; for "true totals" the frontend uses `total_nodes` and counts node_types client-side once full data is loaded. | T1, T7 |
| I7 | Sibling order = alphabetical by lowercased code = path-ASC ordering from ltree. | T4 |
| I8 | `tenant_id` in response equals path-param `tenant_id` exactly. | T1 |
| I9 | `tenant_name` is the current `tenants.name` value. | T1 |
| I10 | `loaded_children` ∈ {"all", "partial", "none"}. "all" if all children present in response; "partial" if some present but more exist (E3 case); "none" if has_children but children array is empty. | T8, T13 |
| I11 | `has_children = (child_count > 0)`. `child_count` always reflects the FULL subtree's immediate children, not what's in this response. | T8 |
| I12 | E3 returns 404 for a `node_id` that doesn't exist in this tenant (whether it doesn't exist at all OR belongs to another tenant — RLS makes both look the same). | T15 |
| I13 | `truncated = true` only when server reduced depth below requested. False for organic small tenants and when requested depth wasn't reduced. | T9 |

---

### File 1: `src/admin_backend/models/org_node.py` — new

Mirror `src/admin_backend/models/tenant_user.py` structure (Pre-flight item 10).

```python
"""ORM model for org_nodes.

Represents one node in the tenant's organisational hierarchy. Backs
the Organization Tree page (Frontend spec 7.3) and serves as the
permission-scope anchor (Step 6.1, RBAC enforcement).

Pattern (b) audit-actors per D-13: created/updated/archived each have
a (UUID, actor_user_type_enum) pair, no FK. App layer validates the
UUID exists in the table indicated by user_type.

Read-only model in v0 (FN-AB-12). Tree mutations land in Step 5.4.

DDL: db/raw_ddl/Ithina_postgres_SQL_DDL_org_nodes_v2.sql
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import FetchedValue
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from admin_backend.config import settings
from admin_backend.models._base import Base


class OrgNode(Base):
    __tablename__ = "org_nodes"
    __table_args__ = {"schema": settings.db_schema}

    id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        server_default=FetchedValue(),
    )
    tenant_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        nullable=False,
    )
    parent_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        nullable=True,  # NULL only for TENANT-type nodes
    )
    path: Mapped[str] = mapped_column(
        # ltree column. Treat as opaque string in Python; ordering
        # happens via path ASC at the SQL layer (lexicographic on
        # ltree gives DFS ordering with sibling-alphabetical for free).
        # Mark with `server_default=FetchedValue()` since DDL has a
        # trigger that rebuilds path from parent.path + this.code.
        nullable=False,
        server_default=FetchedValue(),
    )
    node_type: Mapped[str] = mapped_column(
        postgresql.ENUM(
            "TENANT", "BUSINESS_UNIT", "HQ", "COUNTRY",
            "REGION", "STORE", "DEPARTMENT",
            name="org_node_type_enum",
            create_type=False,
        ),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(nullable=False)
    code: Mapped[str] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(
        postgresql.ENUM(
            "ACTIVE", "INACTIVE", "ARCHIVED",
            name="org_node_status_enum",
            create_type=False,
        ),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=FetchedValue(),
    )
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True), nullable=True,
    )
    created_by_user_type: Mapped[str | None] = mapped_column(
        postgresql.ENUM(
            "PLATFORM", "TENANT",
            name="actor_user_type_enum",
            create_type=False,
        ),
        nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=FetchedValue(),
    )
    updated_by_user_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True), nullable=True,
    )
    updated_by_user_type: Mapped[str | None] = mapped_column(
        postgresql.ENUM(
            "PLATFORM", "TENANT", name="actor_user_type_enum",
            create_type=False,
        ),
        nullable=True,
    )
    archived_at: Mapped[datetime | None] = mapped_column(nullable=True)
    archived_by_user_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True), nullable=True,
    )
    archived_by_user_type: Mapped[str | None] = mapped_column(
        postgresql.ENUM(
            "PLATFORM", "TENANT", name="actor_user_type_enum",
            create_type=False,
        ),
        nullable=True,
    )
```

**If a lightweight `OrgNode` stub already exists** in `models/_lightweight_stubs.py` (likely doesn't): delete it; verify imports; verify Step 3.3's tests still pass.

---

### File 2: `src/admin_backend/models/__init__.py` — modify

Re-export `OrgNode`. Mirror Step 5.2's TenantUser addition.

---

### File 3: `src/admin_backend/schemas/org_node.py` — new

Pydantic v2 schemas. Recursive type via `model_rebuild()` at module bottom.

```python
"""Pydantic schemas for org-tree endpoints (E2, E3).

Note on recursive type: OrgNodeTreeItem.children is self-referential.
Pattern (a) — string forward ref + model_rebuild() at module bottom.

Note on `loaded_children` semantics:
  - "all"     — every child of this node is in the response.
  - "partial" — some children are present, but more exist (E3 case).
  - "none"    — node has children (has_children=true) but none returned
                in this response. Frontend should call E3 to load them
                if user expands.

Note on `child_count` vs items in `children`:
  - `child_count` is always the FULL subtree's immediate children.
  - len(children) may be 0 even if child_count > 0 (when loaded_children="none").
  - len(children) == child_count when loaded_children="all".
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from admin_backend.schemas._common import Pagination  # Existing pagination schema


class OrgNodeTreeItem(BaseModel):
    """One node in the org-tree. Recursive via children."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    node_type: str = Field(
        description=(
            "One of: BUSINESS_UNIT, HQ, COUNTRY, REGION, STORE, DEPARTMENT. "
            "TENANT-type nodes are excluded from all responses."
        ),
    )
    name: str = Field(description="Display name (e.g., 'Texas Region').")
    code: str = Field(description="Short code (e.g., 'TX', 'BU-HQ'). Tenant-unique.")
    status: str = Field(
        description=(
            "Lifecycle status. Always 'ACTIVE' in v0. "
            "INACTIVE/ARCHIVED filters reserved for future."
        ),
    )
    created_at: datetime
    updated_at: datetime

    has_children: bool = Field(
        description="True if this node has any ACTIVE children (regardless of whether they're in this response).",
    )
    child_count: int = Field(
        description=(
            "Count of ACTIVE immediate children of this node. "
            "Frontend uses this for badge UI and for deciding whether "
            "to show an expand-arrow. NOT the recursive descendant count."
        ),
    )
    loaded_children: Literal["all", "partial", "none"] = Field(
        description=(
            "Loading state of children array. "
            "'all' = every child is in `children`. "
            "'partial' = some children present, more available via E3 with offset. "
            "'none' = either (a) leaf node with no children at all, OR "
            "(b) has_children=true but children=[] because depth limit cut them. "
            "Frontend disambiguates via has_children: "
            "  has_children=false + loaded_children='none' → true leaf, no E3 call. "
            "  has_children=true  + loaded_children='none' → call E3 to load. "
            "  has_children=true  + loaded_children='partial' → call E3 with offset>0."
        ),
    )
    children: list[OrgNodeTreeItem] = Field(
        default_factory=list,
        description=(
            "Child nodes. Empty list for leaves AND for not-yet-loaded subtrees. "
            "Distinguish via has_children + loaded_children flag. "
            "Sorted alphabetical by lowercased code."
        ),
    )

    # Audit-actor columns (created_by_*, etc.) NOT exposed.


class OrgTreeStats(BaseModel):
    """Counts for the right-pane header and frontend decisions."""
    total_nodes: int = Field(
        description=(
            "Full count of non-TENANT ACTIVE nodes for the tenant (the entire "
            "tree, not just what's in this response). Frontend uses this to "
            "decide whether to display a 'large tenant' indicator."
        ),
    )
    nodes_returned: int = Field(
        description="Count of nodes actually present in `tree` (recursively).",
    )
    stores: int = Field(
        description=(
            "Count of nodes in the RESPONSE with node_type='STORE'. "
            "May undercount the full tree if response is depth-limited."
        ),
    )
    regions: int = Field(
        description="Count of nodes in the RESPONSE with node_type='REGION'.",
    )
    depth_returned: int = Field(
        description=(
            "Maximum nlevel(path) of any node in the response. "
            "0 if tree is empty."
        ),
    )
    truncated: bool = Field(
        description=(
            "True if server auto-reduced depth below requested due to payload cap. "
            "Frontend should display a 'partial tree' notice."
        ),
    )


class OrgTreeResponse(BaseModel):
    """Response for E2: GET /api/v1/tenants/{tenant_id}/org-tree."""
    tenant_id: UUID = Field(description="Echo of path-param tenant_id.")
    tenant_name: str = Field(
        description="Current tenants.name. Saves frontend a cross-lookup.",
    )
    stats: OrgTreeStats
    tree: list[OrgNodeTreeItem] = Field(
        description=(
            "Top-level org nodes (children of the synthetic TENANT root, which "
            "is itself excluded). Empty list for tenants with no nodes."
        ),
    )


class OrgNodeChildrenResponse(BaseModel):
    """Response for E3: GET /api/v1/tenants/{tenant_id}/org-nodes/{node_id}/children."""
    node_id: UUID = Field(
        description="Echo of path-param parent node_id.",
    )
    items: list[OrgNodeTreeItem] = Field(
        description=(
            "Immediate children of the parent node. Sorted alphabetical by code. "
            "Each child's `loaded_children` is 'none' (lazy by default — caller "
            "must invoke E3 again for grandchildren if needed)."
        ),
    )
    pagination: Pagination = Field(
        description="Standard {total, offset, limit} envelope.",
    )


# Resolve the forward reference inside OrgNodeTreeItem.children.
OrgNodeTreeItem.model_rebuild()
```

---

### File 4: `src/admin_backend/schemas/__init__.py` — modify

Re-export the four new schemas. Mirror existing pattern.

---

### File 5: `src/admin_backend/repositories/org_nodes.py` — new

**Decision point for Claude Code:** how to structure the repo methods. Below is one plausible structure; consider alternatives and pick on perf/complexity tradeoff.

**Approach A (split methods):** Three separate methods, two queries per E2 call (one count, one fetch).

**Approach B (combined):** One method per endpoint that does count + fetch in one CTE. More complex SQL, one round trip.

**Approach C (no separate count):** Always fetch; let `len(rows)` plus path-info derive total. Simpler but means depth-limited mode can't reach `total_nodes` correctly without an extra query anyway, so probably not viable.

Lean: **Approach A** for simplicity unless the extra round-trip cost shows up in benchmarks. Default to Approach A; flag if you pick differently.

```python
"""Repository for org_nodes: read access for the Organization Tree page.

Three methods:
- count_active_by_tenant: COUNT(*) for threshold decision (full vs depth-limited mode in E2).
- list_active_with_child_counts: full or depth-limited tree fetch with per-node child counts.
- list_children_paginated: paginated immediate children (E3 backing query).

All three are called only after RLS-bound session is set up. RLS handles
cross-tenant isolation. PLATFORM JWTs see all rows via D-29 OR-clause.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from admin_backend.models import OrgNode


class OrgNodesRepo:
    """Read-only repo for the Organization Tree page."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def count_active_by_tenant(self, tenant_id: UUID) -> int:
        """Count of non-TENANT ACTIVE nodes in tenant's tree.

        Used by E2 to decide between full-tree mode (≤500) and
        depth-limited mode (>500).
        """
        stmt = (
            select(func.count(OrgNode.id))
            .where(
                OrgNode.tenant_id == tenant_id,
                OrgNode.status == "ACTIVE",
                OrgNode.node_type != "TENANT",
            )
        )
        result = await self._session.execute(stmt)
        return result.scalar_one()

    async def list_active_with_child_counts(
        self,
        tenant_id: UUID,
        max_depth: int | None = None,  # None = full tree, else SQL filter on nlevel(path)
    ) -> list[tuple[OrgNode, int]]:
        """Return ACTIVE nodes (path-ASC) with per-node immediate-child count.

        If max_depth is set, only returns nodes with nlevel(path) <= max_depth.
        Includes the TENANT root if present (caller filters it out). Returns
        list of (OrgNode, child_count) tuples.

        SQL strategy: single query with a CTE for child counts. The CTE
        groups all the tenant's ACTIVE rows by parent_id once; the outer
        query LEFT JOINs to attach per-node counts.

        Performance note: at this point we already trust that count <= some
        upper bound (caller ran count_active_by_tenant first or this is a
        full-tree call known small). If the caller passes max_depth too
        large for the tree, that's still bounded — caller can post-process
        and decide to retry with smaller depth.
        """
        # Claude Code: the SQL below is a sketch. Verify against the real
        # SQLAlchemy 2.x async patterns in the repo and adjust idiomatic
        # construction. The text() approach keeps the CTE readable; pure
        # ORM expression for the same query is also fine.

        depth_clause = "AND nlevel(n.path) <= :max_depth" if max_depth is not None else ""
        sql = text(f"""
            WITH child_counts AS (
              SELECT parent_id, COUNT(*) AS n
              FROM {OrgNode.__table__}
              WHERE tenant_id = :tenant_id
                AND status = 'ACTIVE'
                AND parent_id IS NOT NULL
              GROUP BY parent_id
            )
            SELECT n.*, COALESCE(cc.n, 0) AS child_count
            FROM {OrgNode.__table__} n
            LEFT JOIN child_counts cc ON cc.parent_id = n.id
            WHERE n.tenant_id = :tenant_id
              AND n.status = 'ACTIVE'
              {depth_clause}
            ORDER BY n.path ASC
        """)
        params: dict = {"tenant_id": tenant_id}
        if max_depth is not None:
            params["max_depth"] = max_depth
        result = await self._session.execute(sql, params)
        # Convert to (OrgNode, child_count) tuples.
        return [(self._row_to_orgnode(row), row.child_count) for row in result]

    async def list_children_paginated(
        self,
        tenant_id: UUID,
        parent_id: UUID,
        offset: int,
        limit: int,
    ) -> tuple[list[tuple[OrgNode, int]], int]:
        """Return paginated immediate children of parent_id, with each child's own child_count.

        Returns ((rows, total)) where rows is list of (OrgNode, child_count)
        tuples for the paginated slice, and total is the unpaginated count.
        
        Note: this method does NOT verify parent_id exists. Caller must
        check via `node_exists` before relying on a 0-result as "no children"
        vs "parent doesn't exist." See DP-3 for alternative shapes.
        """
        # Implementation: similar CTE pattern. Filter by parent_id; LIMIT/OFFSET.
        # One query for slice + COUNT(*); two-query pattern is fine.
        ...

    async def node_exists(self, tenant_id: UUID, node_id: UUID) -> bool:
        """Verify a node exists within the tenant (and is not RLS-filtered).
        
        Used by E3 router to disambiguate "parent has no children" (200 + empty)
        from "parent doesn't exist" (404). The method runs against the
        RLS-bound session, so cross-tenant requests return False (the row is
        invisible regardless of whether it physically exists).
        
        DP-3 alternatives:
        - (a) Separate method as shown — simple, one extra ~10ms query per E3 call.
        - (b) Inline into list_children_paginated; return None for not-exists.
              Saves a query but couples concerns.
        - (c) Always return success; let frontend treat empty-children + no-parent
              the same. Loses the 404-distinction.
        Lean: (a). Simplicity over micro-optimization for v0.
        """
        stmt = (
            select(OrgNode.id)
            .where(
                OrgNode.tenant_id == tenant_id,
                OrgNode.id == node_id,
                OrgNode.status == "ACTIVE",
            )
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None
```

**Stop-and-ask** if `OrgNode.__table__` doesn't render in `text()` with the current SQLAlchemy version (might need explicit schema-qualified literal). Surface; we'll switch to `text()` with the explicit table name.

**Stop-and-ask** if a query plan inspection (`EXPLAIN ANALYZE`) shows the LEFT JOIN with the child-counts CTE doesn't use the GIST index on path, or shows a full-table scan instead of the per-tenant filter. The path index plus the b-tree index on tenant_id should suffice; surface unexpected plans.

---

### File 6: `src/admin_backend/routers/v1/org_tree.py` — new

Multi-user-type endpoint. **Smart-default behavior on E2 lives here**; the repo is dumb (returns whatever the caller asks for); the router decides the mode.

```python
"""Routers for E2 (org-tree) and E3 (org-node children).

E2: GET /api/v1/tenants/{tenant_id}/org-tree?depth={N}
E3: GET /api/v1/tenants/{tenant_id}/org-nodes/{node_id}/children?offset={N}&limit={N}

Both multi-user-type. RLS-as-404 per D-17.

Smart-default behavior on E2:
1. Fetch tenant (404 if missing/RLS-filtered).
2. Count active non-TENANT nodes for this tenant.
3. If count <= FULL_TREE_THRESHOLD (500): full-tree mode.
4. Else: depth-limited mode (default depth=4, capped at 6).
5. Fetch nodes-with-child-counts. Build tree.
6. If response > PAYLOAD_CAP (1000 nodes), retry with depth-1.
   Set truncated=true. Stop after at most 1-2 retries.
"""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from admin_backend.auth.context import AuthContext
from admin_backend.dependencies import get_auth_context, get_tenant_session_dep
from admin_backend.errors import TenantNotFoundError, NotFoundError
from admin_backend.models import OrgNode
from admin_backend.repositories.org_nodes import OrgNodesRepo
from admin_backend.repositories.tenants import TenantsRepo
from admin_backend.schemas.org_node import (
    OrgNodeChildrenResponse,
    OrgNodeTreeItem,
    OrgTreeResponse,
    OrgTreeStats,
)
from admin_backend.schemas._common import Pagination

router = APIRouter(tags=["org-tree"])

# Server-side tunables. Captured per the design conversation; locked but
# may need adjustment based on real-world feedback.
FULL_TREE_THRESHOLD = 500
DEFAULT_DEPTH = 4
MAX_DEPTH = 6
PAYLOAD_CAP = 1000


@router.get(
    "/tenants/{tenant_id}/org-tree",
    response_model=OrgTreeResponse,
    summary="Get organisation tree for a tenant",
    description=(
        "Returns the tenant's org tree. Smart-default behavior: small tenants "
        "(≤500 ACTIVE non-TENANT nodes) get the full tree; larger tenants get "
        "a depth-limited tree (default depth=4) with deeper nodes available "
        "via the /org-nodes/{node_id}/children endpoint. Each returned node "
        "carries `has_children`, `child_count`, and `loaded_children` so the "
        "frontend knows which subtrees to lazy-fetch. If the depth-limited "
        "tree still exceeds 1000 nodes, the server auto-reduces depth and "
        "sets `truncated=true`."
    ),
)
async def get_org_tree(
    tenant_id: UUID,
    depth: int | None = Query(
        None,
        ge=1, le=MAX_DEPTH,
        description=(
            "Optional. Max depth (nlevel) of nodes returned. "
            "1 = HQ only; 4 = HQ + Country + Region + Store. "
            "If omitted, server picks based on tenant size."
        ),
    ),
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> OrgTreeResponse:
    # 1. Resolve tenant. 404 if missing or RLS-filtered.
    tenants_repo = TenantsRepo(session)  # adjust to actual constructor
    tenant = await tenants_repo.get_by_id(tenant_id)
    if tenant is None:
        raise TenantNotFoundError()

    # 2. Count nodes. Decides mode.
    org_repo = OrgNodesRepo(session)
    total = await org_repo.count_active_by_tenant(tenant_id)

    # 3. Decide mode.
    if depth is not None:
        # Explicit depth requested — respect it (still capped by MAX_DEPTH).
        max_depth = depth
    elif total <= FULL_TREE_THRESHOLD:
        # Small tenant — return full tree.
        max_depth = None
    else:
        # Large tenant — apply default depth.
        max_depth = DEFAULT_DEPTH

    # 4. Fetch with possible depth filter.
    rows = await org_repo.list_active_with_child_counts(
        tenant_id, max_depth=max_depth
    )

    # 5. Auto-reduce depth if response exceeds PAYLOAD_CAP. (Only when
    #    in depth-limited mode; full-tree mode will still error if a
    #    single tenant exceeds PAYLOAD_CAP — surface as a known-issue.
    #    Realistically a tenant with 1000+ nodes shouldn't have <500
    #    so we won't hit this in the full path.)
    #
    # DP-4: Bounded retry. At most 2 reductions (e.g., 4→3→2). After
    # that, return the last result with truncated=true regardless of
    # over-cap. Bounded loop avoids three round-trips on huge trees
    # AND avoids unbounded SQL queries. Prefer this over the unbounded
    # `while len(rows) > PAYLOAD_CAP` pattern which can issue 4+
    # queries on pathological cases.
    truncated = False
    if max_depth is not None:
        for _ in range(2):  # bounded retry (max 2 reductions)
            if len(rows) <= PAYLOAD_CAP or max_depth <= 1:
                break
            max_depth -= 1
            rows = await org_repo.list_active_with_child_counts(
                tenant_id, max_depth=max_depth
            )
            truncated = True
        # If still over cap after bounded retries, accept and proceed.
        # Stats will show truncated=true and frontend can display notice.

    # 6. Build tree.
    tree, stats = _build_tree(rows, total_full=total, truncated=truncated)

    return OrgTreeResponse(
        tenant_id=tenant.id,
        tenant_name=tenant.name,
        stats=stats,
        tree=tree,
    )


@router.get(
    "/tenants/{tenant_id}/org-nodes/{node_id}/children",
    response_model=OrgNodeChildrenResponse,
    summary="Get immediate children of an org-node (lazy-load)",
    description=(
        "Returns the immediate ACTIVE children of node_id within tenant_id. "
        "Used by the frontend to lazy-load subtrees that were not included "
        "in the initial /org-tree response (loaded_children='none' nodes). "
        "Paginated by offset/limit. Each child carries its own has_children "
        "and child_count for further lazy expansion."
    ),
)
async def get_node_children(
    tenant_id: UUID,
    node_id: UUID,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> OrgNodeChildrenResponse:
    # Resolve tenant — same 404 path as E2.
    tenants_repo = TenantsRepo(session)
    tenant = await tenants_repo.get_by_id(tenant_id)
    if tenant is None:
        raise TenantNotFoundError()

    org_repo = OrgNodesRepo(session)
    rows, total = await org_repo.list_children_paginated(
        tenant_id, node_id, offset=offset, limit=limit,
    )

    # If parent node doesn't exist within this tenant, total will be 0
    # AND we need to disambiguate: parent doesn't exist (404) vs parent
    # has no children (200 with empty items). Verify parent exists
    # explicitly to distinguish.
    parent_exists = await org_repo.node_exists(tenant_id, node_id)
    if not parent_exists:
        raise NotFoundError(code="ORG_NODE_NOT_FOUND", message="Org node not found")

    items = [_row_to_tree_item(node, child_count, "none") for node, child_count in rows]
    # E3 returns "loaded_children=none" because we don't fetch grandchildren.
    return OrgNodeChildrenResponse(
        node_id=node_id,
        items=items,
        pagination=Pagination(total=total, offset=offset, limit=limit),
    )


# --- Pure-functional helpers ---

def _build_tree(
    rows: list[tuple[OrgNode, int]],
    total_full: int,
    truncated: bool,
) -> tuple[list[OrgNodeTreeItem], OrgTreeStats]:
    """Build nested tree + stats from path-ordered (OrgNode, child_count) tuples.

    Single pass:
    1. Build OrgNodeTreeItem for every non-TENANT node, indexed by id.
    2. Determine each node's loaded_children based on whether all of its
       child_count children are present in the loaded set.
    3. Link children into parents; identify roots as nodes whose parent
       is either NULL, or is a TENANT-type node, or is not in the loaded
       set (defensive — happens when depth filter excludes parent).
    4. Compute stats from the loaded non-TENANT nodes.

    The input is path-ordered, so children get appended to parents in
    alphabetical-by-code order naturally.
    """
    by_id: dict[UUID, OrgNodeTreeItem] = {}

    # First pass: build items for all non-TENANT nodes.
    non_tenant_rows = [(n, cc) for (n, cc) in rows if n.node_type != "TENANT"]
    loaded_ids = {n.id for (n, _) in non_tenant_rows}

    for node, child_count in non_tenant_rows:
        # Determine loaded_children at this point: count of children
        # actually in the loaded set vs the child_count. We don't know
        # this yet (it requires the second pass). Defer; second pass
        # finalizes loaded_children.
        item = OrgNodeTreeItem(
            id=node.id,
            node_type=node.node_type,
            name=node.name,
            code=node.code,
            status=node.status,
            created_at=node.created_at,
            updated_at=node.updated_at,
            has_children=(child_count > 0),
            child_count=child_count,
            loaded_children="none",  # placeholder; corrected in second pass
            children=[],
        )
        by_id[node.id] = item

    # Second pass: link children into parents.
    tenant_root_ids = {n.id for (n, _) in rows if n.node_type == "TENANT"}
    roots: list[OrgNodeTreeItem] = []
    for node, child_count in non_tenant_rows:
        item = by_id[node.id]
        if (
            node.parent_id is None
            or node.parent_id in tenant_root_ids
            or node.parent_id not in by_id
        ):
            roots.append(item)
        else:
            by_id[node.parent_id].children.append(item)

    # Third pass: finalize loaded_children based on whether all children
    # are in the loaded set.
    for node, child_count in non_tenant_rows:
        item = by_id[node.id]
        if child_count == 0:
            item.loaded_children = "none"  # leaf — no children to load anyway
            # Note: distinguishing "leaf" from "unloaded" is via child_count;
            # has_children=false signals leaf.
        else:
            loaded = len(item.children)
            if loaded == child_count:
                item.loaded_children = "all"
            elif loaded == 0:
                item.loaded_children = "none"
            else:
                item.loaded_children = "partial"

    # Stats.
    stores = sum(1 for (n, _) in non_tenant_rows if n.node_type == "STORE")
    regions = sum(1 for (n, _) in non_tenant_rows if n.node_type == "REGION")
    depth_returned = max(
        (_path_depth(n.path) for (n, _) in non_tenant_rows), default=0
    )
    stats = OrgTreeStats(
        total_nodes=total_full,
        nodes_returned=len(non_tenant_rows),
        stores=stores,
        regions=regions,
        depth_returned=depth_returned,
        truncated=truncated,
    )
    return roots, stats


def _path_depth(path: str) -> int:
    """Compute nlevel of an ltree path string."""
    return path.count(".") + 1 if path else 0


def _row_to_tree_item(
    node: OrgNode,
    child_count: int,
    loaded_children: Literal["all", "partial", "none"],
) -> OrgNodeTreeItem:
    """Convert one (node, child_count) row into a tree item."""
    return OrgNodeTreeItem(
        id=node.id,
        node_type=node.node_type,
        name=node.name,
        code=node.code,
        status=node.status,
        created_at=node.created_at,
        updated_at=node.updated_at,
        has_children=(child_count > 0),
        child_count=child_count,
        loaded_children=loaded_children if child_count > 0 else "none",
        children=[],
    )
```

**Decision points** for Claude Code in this file:

1. **Auto-reduction loop in E2 — should it be 1 retry or up to MAX_DEPTH retries?** Above sketch retries up to depth=1. Alternative: single retry, then accept whatever even if over cap. Lean: bounded loop (no more than 2 retries), since after that the tenant is genuinely huge and the truncation flag should suffice; better to return something than recurse forever.

2. **`_build_tree` — three-pass vs two-pass.** The sketch uses three passes for clarity. A single-pass implementation is possible but harder to reason about. Lean: keep three passes; they're each O(n) and the constant factor doesn't dominate. (DP-5)

3. **`node_exists` repo method** — separate vs combined with `list_children_paginated`. Lean: separate method for clarity (one extra ~10ms query is fine). (DP-3)

---

### File 7: `src/admin_backend/routers/v1/__init__.py` — modify

Wire the router. Mirror however `tenant_users` is wired.

---

### File 8: `tests/integration/conftest.py` — modify

Add `make_org_node` factory mirroring `make_store`'s raw-SQL pattern. Audit-actor pairs nullable per D-13. Path built from parent_path + lowercased(code with hyphens→underscores).

```python
@pytest.fixture
def make_org_node(platform_session):
    """Factory creating an org_node row with correctly-built ltree path.

    Caller orders insertions: TENANT root first, then descendants.
    Returns (id, path) tuple; caller passes parent_path on subsequent calls.
    """
    created_ids: list[UUID] = []

    async def _make(
        *,
        tenant_id: UUID,
        node_type: str,
        code: str,
        name: str,
        parent_id: UUID | None = None,
        parent_path: str | None = None,
        status: str = "ACTIVE",
    ) -> tuple[UUID, str]:
        if node_type == "TENANT":
            path = code.lower().replace("-", "_")
        else:
            assert parent_path is not None, "non-TENANT nodes need parent_path"
            child_label = code.lower().replace("-", "_")
            path = f"{parent_path}.{child_label}"

        async with platform_session() as session:
            result = await session.execute(text("""
                INSERT INTO org_nodes (
                    tenant_id, parent_id, path, node_type, name, code, status
                ) VALUES (
                    :tenant_id, :parent_id, :path::ltree,
                    :node_type::org_node_type_enum,
                    :name, :code,
                    :status::org_node_status_enum
                )
                RETURNING id, path::text
            """), {
                "tenant_id": tenant_id, "parent_id": parent_id,
                "path": path, "node_type": node_type,
                "name": name, "code": code, "status": status,
            })
            row = result.one()
            await session.commit()
            created_ids.append(row[0])
            return row[0], row[1]

    yield _make

    # Teardown — reverse insertion order; FK requires children before parents.
    async with platform_session() as session:
        if created_ids:
            await session.execute(
                text("DELETE FROM org_nodes WHERE id = ANY(:ids)"),
                {"ids": list(reversed(created_ids))},
            )
            await session.commit()
```

**Stop-and-ask** if `platform_session` fixture's name or shape differs (Pre-flight item 15).

---

### File 9: `tests/integration/test_org_tree_router.py` — new

~14-16 tests. Reuse the `client` + `_platform_jwt` + `_tenant_jwt` machinery from `test_tenant_users_router.py`.

```python
"""Integration tests for E2 (/org-tree) and E3 (/org-nodes/.../children).

Each test corresponds to a contract invariant (I1-I13) or a smart-default
behavior, plus auth and 404 paths.
"""
import pytest
from uuid import uuid4


# --- E2 tests ---

@pytest.mark.asyncio
async def test_t1_e2_small_tenant_full_tree_envelope(...):
    """T1 (E2): small tenant gets full tree with loaded_children='all' everywhere.
    Verifies I1, I2, I3 (children:[] for leaves), I4-I9, I10 (all/none split).

    Setup: Buc-ee's-shape — 8 nodes (HQ + 2 regions + 3 stores + 2 departments).
    Expected: stats.total_nodes=8, stats.nodes_returned=8, tree[0]=HQ with 2
    children (FL before TX); leaves carry loaded_children='none' (they're
    leaves so no children to load) but has_children=false.
    """
    ...


@pytest.mark.asyncio
async def test_t2_e2_empty_tenant_returns_empty_tree(...):
    """T2 (E2): tenant with zero org_nodes returns 200, tree=[], stats all 0.
    Architectural-invalid case (every tenant should have a TENANT root) but
    DDL-permissive; backend handles gracefully.
    """


@pytest.mark.asyncio
async def test_t3_e2_only_tenant_root_returns_empty_tree(...):
    """T3 (E2): tenant with only TENANT-type root returns tree=[].
    TENANT root excluded per I1.
    """


@pytest.mark.asyncio
async def test_t4_e2_sibling_order_alphabetical(...):
    """T4 (E2): siblings in alphabetical-by-lowercased-code order (I7).
    Insert TX, CA, FL in that order. Expected: CA, FL, TX in response.
    """


@pytest.mark.asyncio
async def test_t5_e2_recursive_depth_3_or_more(...):
    """T5 (E2): tree handles depth-3+ correctly (HQ → Region → Store → Dept).
    Pydantic recursive serialization works.
    """


@pytest.mark.asyncio
async def test_t6_e2_inactive_nodes_excluded(...):
    """T6 (E2): INACTIVE / ARCHIVED nodes excluded.
    Insert HQ + Store_A (ACTIVE) + Store_B (INACTIVE).
    Expected: tree has HQ with 1 child only.
    """


@pytest.mark.asyncio
async def test_t7_e2_smart_default_full_mode_under_threshold(...):
    """T7 (E2): tenant with <500 nodes gets full tree even without depth param.
    Build a 50-node tenant; call /org-tree (no depth). Verify
    loaded_children='all' on internal nodes, nodes_returned=50, truncated=false.
    """


@pytest.mark.asyncio
async def test_t8_e2_smart_default_lazy_mode_over_threshold(...):
    """T8 (E2): tenant with >500 nodes gets depth-limited tree by default.
    Build a 600-node tenant (HQ + 5 regions + 100 stores + 500 departments).
    Call /org-tree (no depth). Verify default depth=4 applied;
    deeper nodes (departments) marked loaded_children='none'.
    """


@pytest.mark.asyncio
async def test_t9_e2_payload_cap_triggers_auto_reduce(...):
    """T9 (E2): when default depth=4 still exceeds 1000 nodes, server reduces.
    Build a tree where depth=4 alone has >1000 ACTIVE nodes (e.g., 5000 stores
    in one region). Call /org-tree. Verify truncated=true, depth_returned<4.
    """


@pytest.mark.asyncio
async def test_t10_e2_explicit_depth_param_respected(...):
    """T10 (E2): ?depth=2 returns just HQ + first level.
    Verify depth_returned=2; child_count populated for all returned nodes;
    next-level nodes carry has_children=true and loaded_children='none'.
    """


@pytest.mark.asyncio
async def test_t11_e2_tenant_jwt_own_tenant(...):
    """T11 (E2): TENANT user requests own tenant — 200, tree returned."""


@pytest.mark.asyncio
async def test_t12_e2_tenant_jwt_cross_tenant_returns_404(...):
    """T12 (E2): TENANT-A asking for TENANT-B's tree → 404 TENANT_NOT_FOUND.
    LOAD-BEARING regression test for D-17 (RLS-as-404).
    """


@pytest.mark.asyncio
async def test_t13_e2_unknown_tenant_returns_404(...):
    """T13 (E2): PLATFORM JWT with random uuid → 404."""


@pytest.mark.asyncio
async def test_t14_e2_no_jwt_returns_401(...):
    """T14 (E2): no Authorization header → 401."""


# --- E3 tests ---

@pytest.mark.asyncio
async def test_t15_e3_happy_path(...):
    """T15 (E3): GET /tenants/{tid}/org-nodes/{nid}/children with valid IDs.
    Verify items, pagination, each child's has_children and child_count populated.
    """


@pytest.mark.asyncio
async def test_t16_e3_pagination(...):
    """T16 (E3): node with 250 children, request limit=100 offset=100.
    Verify items has 100 entries; total=250; offset=100; limit=100.
    """


@pytest.mark.asyncio
async def test_t17_e3_unknown_node_id_returns_404(...):
    """T17 (E3): PLATFORM JWT requesting a node_id that doesn't exist
    in the path-param tenant — 404 ORG_NODE_NOT_FOUND.
    
    Setup: Buc-ee's tenant exists; pass random uuid4() as node_id.
    JWT: PLATFORM (Anjali) so the request reaches the handler.
    Expected: 404, code='ORG_NODE_NOT_FOUND'.
    
    Distinct from T18 (cross-tenant); same response shape but the
    cause is genuinely-not-exists rather than RLS-filtered.
    """


@pytest.mark.asyncio
async def test_t18_e3_cross_tenant_node_returns_404(...):
    """T18 (E3): TENANT JWT (Marcus / Buc-ee's) requesting a node_id
    that belongs to another tenant (Żabka) — 404 ORG_NODE_NOT_FOUND.
    
    Setup: insert a node under Żabka via factory; capture its node_id.
    Switch to Marcus JWT; call E3 with Buc-ee's tenant_id (path) and
    Żabka's node_id (path). RLS filters Żabka's node out of Marcus's
    view; node_exists returns False; 404 fires.
    
    LOAD-BEARING regression test for D-17 (RLS-as-404). Verifies the
    cross-tenant 404 path matches the genuinely-not-exists path so an
    attacker can't probe for node existence in another tenant.
    
    Expected: same envelope as T17. 404, code='ORG_NODE_NOT_FOUND'.
    """


@pytest.mark.asyncio
async def test_t19_e3_no_children_returns_empty_items(...):
    """T19 (E3): node has no children — 200 with items=[], pagination.total=0.
    Distinct from T17 (parent doesn't exist → 404).
    """


@pytest.mark.asyncio
async def test_t20_e2_mixed_depth_subtrees_loaded_children_correct(...):
    """T20 (E2): one branch goes depth=3 (flat), another goes depth=6 (deep).
    
    In lazy mode (default depth=4), the depth-3 branch should have
    loaded_children='all' on its leaves (no children below; child_count=0;
    has_children=false). The depth-6 branch's depth-4 nodes should have
    loaded_children='none' AND has_children=true (children exist but
    were excluded by depth=4 cutoff).
    
    Setup: 600+ node tenant (forces lazy mode at default depth=4).
    Branch A: HQ → Region → Store → Department (depth 4 max; some end at depth 3).
    Branch B: HQ → BU → Country → Region → Store → Department (depth 6).
    
    Expected: Branch A leaves at depth 3 → has_children=false, loaded_children='none'.
    Branch B nodes at depth 4 → has_children=true, loaded_children='none'.
    Frontend disambiguates the two via has_children.
    
    LOAD-BEARING for the loaded_children semantic clarification: if a
    future regression makes 'none' mean only-unloaded (or only-leaf),
    one of the two cases breaks.
    """


@pytest.mark.asyncio
async def test_t21_e2_invalid_uuid_path_returns_400_or_422(...):
    """T21 (E2): malformed UUID in path-param.
    
    GET /api/v1/tenants/not-a-uuid/org-tree
    
    FastAPI converts invalid path-param UUIDs to 422 by default. If the
    project has an exception handler that converts to 400 (Step 3.3 may
    have done this), assert 400. Otherwise 422. Check the actual
    behaviour from existing tenants tests' invalid-uuid case (Step 3.3's
    test 13 if present) and match.
    
    Expected: 4xx (400 or 422), structured error envelope.
    """
```

**Total tests: 21.** (T1-T14 for E2; T15-T21 covering E3 + mixed-depth + invalid UUID.) Plus 0-2 unit tests for `_build_tree` (recommended: at least one for the loaded_children-state-machine logic).

---

### File 10: `docs/endpoints/org-tree.md` — new

8-section format. Cover both E2 and E3. Important sections:

- **Section 1 (Overview):** Two-endpoint family. E2 for initial load; E3 for lazy expansion. Smart-default explained.
- **Section 2 (Auth):** Multi-user-type. RLS-as-404.
- **Section 3 (Request shape):** path params, query params for both endpoints.
- **Section 4 (Response shape):** Full examples for E2 small / E2 large / E2 truncated / E3 / 404 / 401. Recursive `OrgNodeTreeItem` clearly shown with `has_children`, `child_count`, `loaded_children`.
- **Section 5 (Frontend integration patterns):** TypeScript snippets for the two-call flow + lazy-expansion logic. Race-condition reconciliation note (`response.tenant_id !== currentSelection` → discard).
- **Section 6 (Behaviour notes):**
  - ACTIVE-only filter (v0).
  - Sibling sort = alphabetical by lowercased code.
  - Smart-default thresholds (locked: 500 nodes, depth=4, cap 1000).
  - Truncation behavior.
  - Performance: trees up to ~1000 nodes serialize in <100ms; pathological cases truncated.
- **Section 7 (Errors):** Standard error envelope examples.
- **Section 8 (Changelog):** First version.

---

### File 11: `BUILD_PLAN.md` — modify

Step 5.3 entry rewritten. Status TODO → DONE.

```markdown
## Step 5.3 — Org-tree read surface (lazy-load with smart defaults)

**Status.** DONE
**Owner.** CLAUDE_CODE

**Note on scope narrowing.** Original entry called for four endpoints
(org-nodes flat list, detail, descendants, full org-tree from JWT).
Frontend contract review and design conversation 2026-05-04 narrowed
scope to two endpoints: E2 (org-tree with smart defaults) and E3
(node children for lazy expansion). The originally-planned `num_nodes`
augmentation on `/api/v1/tenants` was also dropped from this step
(parked post-v0; D-31 means it can be added later without breaking).

**Goal.** Read surface for the Organization Tree page with scaling for
large tenants (3000+ nodes per tenant supported).

**Scope in (as shipped).**
- `OrgNode` ORM model (full).
- Schemas: `OrgNodeTreeItem` (with has_children, child_count, loaded_children),
  `OrgTreeStats`, `OrgTreeResponse`, `OrgNodeChildrenResponse`.
- `OrgNodesRepo`: count, list-with-child-counts (full or depth-limited),
  list-children-paginated, node-exists.
- E2: `GET /api/v1/tenants/{tenant_id}/org-tree` with smart-default
  (full tree if ≤500 nodes; depth=4 otherwise; auto-reduce on payload cap).
- E3: `GET /api/v1/tenants/{tenant_id}/org-nodes/{node_id}/children`
  paginated; for lazy-loading children of a specific node.
- Pure-functional `_build_tree` helper.
- 21 integration tests covering invariants I1-I13 + smart-default behavior +
  auth + 404 paths + mixed-depth subtree loaded_children + invalid UUID.
- `docs/endpoints/org-tree.md` (8-section).
- OpenAPI snapshot regenerated.

**Scope out.**
- `num_nodes` augmentation on `/api/v1/tenants` (parked; D-31 covers).
- `GET /api/v1/org-nodes` flat list / `GET /api/v1/org-nodes/{id}` detail
  drawer / `GET /api/v1/org-nodes/{id}/descendants` (lazy via E3 covers
  all UI use cases).
- INACTIVE / ARCHIVED filters.
- Tree mutations (Step 5.4).

**Acceptance criteria (met).**
- Existing 138 pytest passes plus ~19 new tests, all green.
- mypy strict clean.
- check_setup 35/35.
- Smoke test unchanged.
- Alembic head unchanged at `0644a4186e48`.
- T8 cross-tenant 404 (E2) and T18 cross-tenant 404 (E3) explicitly green.
- T9 payload cap auto-reduce explicitly green.
- T10 explicit depth param respected.
- `docs/endpoints/org-tree.md` follows 8-section format.

**Coordination.**
- Frontend integrates against deployed dev within 24 hours.

**Known follow-ups.**
- **Step 5.3.1**: drawer endpoint when mockup is locked.
- **Step 5.4**: tree mutations + sort_order + status cascade.
```

---

### File 12: `CLAUDE.md` — modify

- **Current state → Completed:** Step 5.3 bullet (E2 + E3, smart-default, lazy-load metadata, ~19 tests, docs).
- **D-30 exception note**: append one-liner alongside the existing PG_ENUM and batch-by-key envelope notes:
  > Step 5.3's E2 response is a deliberate D-30 exception. The org-tree is a singleton resource for the tenant; response shape is `{tenant_id, tenant_name, stats, tree}`, not the standard `{items, pagination}`. E3 follows D-30 normally (`{node_id, items, pagination}`). Future singleton/structured resources should follow E2's pattern.
- **No new D-XX entries.** D-30's "list-only envelope" already accommodates per-endpoint exceptions.
- **No new FN-AB entries** unless something genuinely surfaces.

---

### File 13: `docs/architecture.md` — likely no-edit

This step adds endpoints and an ORM model but doesn't move the system shape. Skip the file. If a "Code structure" section names specific Repos by example, add `OrgNodesRepo`.

---

### File 14: `prompts/step-5_3-org-tree-2026-05-04.md` — new (this prompt v2)

Bundle this v2 prompt. The earlier v1 prompt at the same filename is replaced; commit replaces v1 with v2.

---

### File 15: `docs/endpoints/openapi.json` — re-export

After all code is in:
```bash
curl -s http://localhost:8000/api/v1/openapi.json | jq '.' > docs/endpoints/openapi.json
```

Verify both new endpoints appear with rich descriptions, recursive schema for `OrgNodeTreeItem`, and standard 401/404 error responses.

---

## Testing and regression discipline

### New tests added by this step
- 21 integration tests at `tests/integration/test_org_tree_router.py`.
- 0-2 unit tests for `_build_tree` (recommended).

### Tests deliberately not added
- "ltree column type works correctly." Covered by smoke test (Step 1.5).
- "RLS isolation on org_nodes." Covered exhaustively by smoke test test_15.
- "OrgNodesRepo handles huge trees." 1000-node test exists (T9); larger pathological cases out of scope.
- "Cycle detection in tree assembly." DDL composite FK + path materialization guarantee no cycles.

### Regression risk surface introduced

1. **Smart-default decision logic.** Server-side fork between full-tree and depth-limited paths. If the count query and the tree query disagree (TOCTOU between them), the response could be wrong. Mitigation: both run against the same RLS-bound session; they observe the same snapshot. Not a real risk in practice.

2. **`_build_tree` loaded_children state machine.** Three states (all/partial/none) with subtle transitions. T8 covers the lazy case directly; the sketch's three-pass logic is straightforward.

3. **`nlevel(path) <= max_depth` query plan.** Without a functional index on `nlevel(path)`, this filter is per-row. Acceptable at our scale (max 10K nodes per tenant, indexed on tenant_id); flag if EXPLAIN ANALYZE shows a full scan past the path index.

4. **CTE child-counts vs response children — semantic consistency.** child_count reflects the FULL subtree's immediate children (server-side count); len(children) reflects what the depth filter included. Mismatch is the signal frontend uses for lazy-load. Tests T7, T8 verify this directly.

5. **E3's `node_exists` vs the children query.** Two separate queries; small TOCTOU window if a node is deleted between. Acceptable for v0 read-only; Step 5.4 (writes) revisits.

6. **`OrgNodeTreeItem.children` recursive type performance.** Pydantic v2 handles recursion correctly but adds overhead for deeply-nested trees. Trees are bounded by PAYLOAD_CAP (1000 nodes); within bounds, performance acceptable.

7. **Existing `actor_user_type_enum` reuse.** TenantUser model declared this enum; reuse the same import. Pre-flight item 10.

### Verification harness (run all five; all must be green)

```bash
# 1. Full pytest suite
uv run pytest -v

# 2. mypy strict
uv run mypy --strict src/admin_backend

# 3. Pre-flight checker
./scripts/check_setup.sh

# 4. Migration round-trip (no migration; verify head)
uv run alembic heads
# Expected: 0644a4186e48 (head)

# 5. Manual curl verification
JWT=$(cat scripts/jwt/tokens/anjali.jwt)
TENANT_ID=$(psql $DATABASE_URL -tAc "SELECT id FROM tenants WHERE name = 'Buc-ee''s' LIMIT 1")

# E2 — small tenant, full tree
curl -s -H "Authorization: Bearer $JWT" \
  "http://localhost:8000/api/v1/tenants/$TENANT_ID/org-tree" | jq .
# Expected: stats.total_nodes=8, stats.nodes_returned=8, truncated=false,
# tree[0].name="Buc-ee's HQ", FL before TX in HQ.children.

# E2 — explicit depth
curl -s -H "Authorization: Bearer $JWT" \
  "http://localhost:8000/api/v1/tenants/$TENANT_ID/org-tree?depth=2" | jq '.stats'
# Expected: depth_returned=2; nodes_returned smaller than full.

# E3 — children of HQ
HQ_ID=$(psql $DATABASE_URL -tAc \
  "SELECT id FROM org_nodes WHERE tenant_id='$TENANT_ID' AND node_type='HQ' LIMIT 1")
curl -s -H "Authorization: Bearer $JWT" \
  "http://localhost:8000/api/v1/tenants/$TENANT_ID/org-nodes/$HQ_ID/children" | jq .
# Expected: items has 2 regions (FL, TX); pagination total=2.

# E3 — unknown node id (within own tenant)
curl -s -H "Authorization: Bearer $JWT" \
  "http://localhost:8000/api/v1/tenants/$TENANT_ID/org-nodes/00000000-0000-0000-0000-000000000000/children"
# Expected: 404, code=ORG_NODE_NOT_FOUND.
```

If any leg is not green, **report the failure rather than the step.** Do not commit.

---

## Stop and ask if

- Migration head is not `0644a4186e48`. Surface the actual head.
- A lightweight `OrgNode` stub exists in `_lightweight_stubs.py` (Pre-flight item 9). Replace and verify Step 3.3 tests still pass.
- The `path` ltree column requires a non-trivial SQLAlchemy type beyond `Mapped[str]`. Surface and we'll pick.
- `actor_user_type_enum` declared shared in a Python class somewhere. Reuse rather than re-declare.
- `TenantNotFoundError` class signature in `errors.py` doesn't match what's needed. Surface; reuse.
- `nlevel()` ltree function not available (Pre-flight item 4). CSD-02 surfaces.
- The CTE-with-window query produces an EXPLAIN ANALYZE plan that does a full table scan instead of using the (tenant_id, status) index. Surface; we'll add an explicit index hint or restructure the query.
- The `_build_tree` helper's three-pass approach hits a complexity wall (e.g., the `loaded_children` state machine has more cases than enumerated). Surface; we'll either simplify or document.
- A test surfaces a regression in the existing `test_tenants_router.py` Step 3.3 tests (e.g., L9's correlation pattern). Should not happen since this step doesn't touch TenantsRepo's aggregates, but verify.
- An invariant (I1-I13) is structurally hard to verify without database state introspection (e.g., I11 needs the actual unloaded-children count). Surface; we'll relax the test or add a helper.
- The error class hierarchy doesn't have a `NotFoundError(code=...)` shape. Instead it might have per-resource `*NotFoundError` subclasses. Surface and we'll either create `OrgNodeNotFoundError` (matching the pattern) or use a generic `NotFoundError` with code parameter.
- FastAPI's invalid-UUID handler returns raw 422 (Pydantic) without going through the project's error envelope. Surface; we'll either accept 422 (T21 asserts both) or add a custom path-param validator.
- The `(tenant_id, parent_id)` index doesn't exist (Pre-flight item 7). Pick from (a) accept scan / (b) add migration / (c) restructure query. Surface the chosen path.

---

## Acceptance criteria

- 15 files created/modified per scope above.
- E2 returns expected shape for small tenant (Buc-ee's: stats.total_nodes=8); for synthetic large tenant test fixtures (>500 nodes); for synthetic tenant with payload-cap trigger (auto-reduces depth).
- E3 returns paginated children correctly; 404 for unknown/cross-tenant node_ids.
- T7 (smart-default full-mode), T8 (smart-default lazy-mode), T9 (payload-cap auto-reduce), T12 (E2 cross-tenant 404), T18 (E3 cross-tenant 404) explicitly green.
- All existing pytest passes still pass (no regressions).
- mypy strict clean.
- check_setup 35/35.
- Smoke test unchanged.
- Alembic head unchanged at `0644a4186e48`.
- `docs/endpoints/org-tree.md` follows 8-section format with both endpoints.
- **OpenAPI spec quality:** new endpoints show `summary`, `description`, full schemas (recursive `OrgNodeTreeItem` with `$ref`), error responses 400/401/404 referenced. Frontend codegen consumes the spec; quality matters.

---

## Report (BEFORE proposing commit)

Six bundles per the convention:

1. **Code:** files created with line counts; the SQL strategy chosen for `list_active_with_child_counts` (CTE-with-CTE vs alternative); the `_build_tree` pass count and rationale; the manual curl outputs for the three E2 modes (full / lazy / truncated) and E3 (happy / 404 unknown).
2. **CLAUDE.md updates:** Step 5.3 Completed bullet; the D-30-exception note for E2; no new D-XX or FN-AB.
3. **BUILD_PLAN.md updates:** Step 5.3 entry rewritten with v2 scope.
4. **architecture.md updates:** "no change" likely.
5. **OpenAPI spec snapshot:** `docs/endpoints/openapi.json` regenerated.
6. **Prompt file:** v2 prompt confirmed.

Plus: pytest count delta (was 138, now ~159); mypy status; check_setup; alembic head unchanged; the five DP decision points called out (DP-1 SQL strategy, DP-2 count method, DP-3 node_exists, DP-4 retry strategy, DP-5 _build_tree pass count) with brief justification.

Wait for explicit authorisation before staging or committing.

---

## After completing

```
git status
git add -A
git commit -m "Step 5.3: org-tree read surface with lazy-load smart-defaults

- E2: GET /api/v1/tenants/{id}/org-tree (smart-default: full ≤500 nodes; depth=4 otherwise; auto-reduce on cap)
- E3: GET /api/v1/tenants/{id}/org-nodes/{node_id}/children (paginated lazy expansion)
- OrgNode ORM + recursive OrgNodeTreeItem schema with has_children/child_count/loaded_children
- OrgNodesRepo: count, list-with-child-counts (full and depth-limited), list-children-paginated, node-exists
- _build_tree pure-functional helper for tree assembly with state-machine for loaded_children
- 21 integration tests covering invariants I1-I13 + smart-default + auth + 404 paths + mixed-depth + invalid UUID
- T9 (payload cap auto-reduce), T12 + T18 (cross-tenant 404), T8 (lazy-mode) load-bearing
- num_nodes augmentation on /tenants DROPPED (parked post-v0)
- D-30 exception note for singleton tree responses (CLAUDE.md)
- BUILD_PLAN 5.3 rewritten: scope narrowed to lazy-load 2-endpoint design
- Step 5.3.1 (drawer), Step 5.4 (writes) deferred"
```

Ask "Run? yes / no / edit message". On yes, execute via bash tool.

---

## End of prompt
