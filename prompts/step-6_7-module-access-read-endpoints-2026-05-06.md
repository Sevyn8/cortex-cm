# Prompt — Step 6.7: Module Access read endpoints (Modules + Matrix)

> Generated 2026-05-06. Resolved through frontend-locked design review:
> - Two new dedicated read endpoints under `/api/v1/module-access/` covering the Module Access UI (Frontend spec — superadmin governance console screen with 6 module cards + tenant × module matrix).
> - Multi-user-type with RLS-driven persona projection (continues Step 6.5 pattern).
> - Read-only first; toggle/write surface deferred to a future step.
> - Module taglines stay frontend-hardcoded; no `lookups.description` column added.
> - **First instance of the new label-handling convention**: server-side resolution of enum codes via lookups JOIN with COALESCE fallback. Sibling-field shape (`<field>` + `<field>_label`). Codified in CLAUDE.md as the rule for all new endpoints from this step forward; older endpoints (`/tenants`, `/tenant-users`, `/platform-users`, `/roles`, `/org-tree`, `/dashboard/*`) stay bare-enum (Amit's frontend handles client-side resolution for those).
> - Built on Step 6.6's unified `module_code_enum` (single PG enum, single `lookups.list_name='module_code'` reference list — six values: ROOS, PRICING_OS, PERISHABLES_ASSISTANT, PROMOTIONS_ASSISTANT, GOAL_CONSOLE, ADMIN).
>
> Paste this entire block into a fresh Claude Code session to start Step 6.7.

---

## Context: why this step exists and why now

The Module Access page in the SUPERADMIN CONSOLE (Frontend spec — sidebar entry "Module Access" under "ACCESS CONTROL" group) shows two structurally different things:

1. **Top section — 6 module cards.** Each card shows: module code, display name, frontend-hardcoded tagline, and an aggregate count *"Enabled in X / Y tenants"* where:
   - **Y** = count of tenants with `status IN ('ACTIVE', 'TRIAL')`
   - **X** = subset of Y where the tenant has an `ENABLED` row in `tenant_module_access` for that module

2. **Bottom section — tenant × module matrix.** Per row: tenant identity (name, tier badge, status indicator), and per cell: that tenant's enablement status for each module (ENABLED or DISABLED). Matrix shows all non-terminated tenants (ACTIVE + TRIAL + SUSPENDED + ONBOARDING) so Anjali has governance visibility into non-currently-transacting tenants.

Frontend renders these as one page but composes them via two backend calls. Per the locked design (frontend split per concern):

- `GET /api/v1/module-access/modules` — module catalogue with per-module aggregates
- `GET /api/v1/module-access/matrix` — tenant × module matrix

**Why now.** Step 6.6 just unified `module_enum` and `module_code_enum` into one PG type. With unification done, the Module Access read surface is the cleanest first consumer of the unified vocabulary. The page is also a real frontend deliverable — Amit's team needs the read endpoints to integrate against.

**Why this step is also locking a new convention.** Step 6.1's permission-matrix already uses the JOIN-against-lookups pattern for label resolution (`module_label`, `resource_label`, etc.). The other read endpoints (Steps 3.3 / 5.1 / 5.2 / 5.3 / 6.5) return bare enum codes; frontend resolves client-side. After conversation with Amit, the policy is locked: **older endpoints stay as-is; new endpoints from Step 6.7 forward use server-side label resolution with sibling-field shape (`<field>` + `<field>_label`).** Step 6.7 is the first new endpoint under this rule.

---

## Pre-flight

1. Run `./scripts/check_setup.sh`. Expect 35/35.
2. `git log --oneline -5` — confirm Step 6.6 (Module enum unification) is at HEAD or HEAD~1. Step 6.7 builds on 6.6's unified enum.
3. `uv run alembic heads` — confirm head is `cec8fae734e0` (Step 6.6's unification migration). This step adds **one new migration** (lookups seed for tenant_tier + tenant_status, plus optionally module_code display_order updates per pre-flight 17a). Head advances by one revision.
4. Read `CLAUDE.md` fully. Focus on:
   - **D-13** (audit-actor patterns — `tenant_module_access` has 3 NOT NULL audit-actor columns, but they're hidden in the read response; relevant for understanding why).
   - **D-15** (DB_SCHEMA from environment).
   - **D-17** (RLS-as-404 pattern). Not directly relevant — these endpoints are aggregate / list, not row-by-id, so 404 doesn't apply. RLS produces empty/collapsed result sets, not 404s.
   - **D-29** (PLATFORM RLS visibility via OR-clause). **Load-bearing**: PLATFORM JWT sees fleet-wide aggregates because the OR-clause on `tenant_module_access` and `tenants` exposes all rows; TENANT JWT sees own-tenant only because RLS filters before aggregation/JOIN.
   - **D-30** (list-only response envelope). `/modules` uses standard `{items}` (no pagination — fixed cardinality of 6). `/matrix` uses standard `{items, pagination}`. **No D-30 exception in this step.**
   - **D-31** (response field semantics are append-only). Relevant: the new label-handling pattern adds fields (`*_label`), doesn't change existing ones.
   - **"v0 auth model" note** — multi-user-type with RLS continues to apply here. FN-AB-21 was resolved at Step 6.5 establishing this as the platform-wide default for read endpoints.
   - **"Note on PG enum columns"** convention — the `tenant_module_access.module` and `tenants.tier`, `tenants.status` columns are PG enums; ORM declarations need `postgresql.ENUM(name="...", create_type=False)`.
   - **Step 6.6's sort-stability decision** — `permissions.module` was flipped from enum-ordinal sort to `lookups.display_order` sort. **Step 6.7 follows the same pattern**: module ordering on both endpoints uses `ORDER BY lookups.display_order ASC, module_code ASC`.
5. Read `docs/architecture.md` "Multi-tenancy and data isolation" — confirms RLS does persona projection on aggregates (relevant for `/modules`'s aggregate counts under TENANT JWT collapsing to 0/1 or 1/1).
6. Read `src/admin_backend/models/tenant_module_access.py` — the `TenantModuleAccess` ORM model + `ModuleCode` enum (Step 3.4.5, post-Step-6.6 unification). The `status` column is `TenantModuleAccessStatus` enum (`ENABLED` / `DISABLED`); only `ENABLED` rows count toward enabled-count aggregates.
7. Read `src/admin_backend/models/tenant.py` — the `Tenant` ORM model + `TenantStatus`, `TenantTier`, `TenantIndustry` enums. Verify the `status` enum vocabulary (`ONBOARDING`, `TRIAL`, `ACTIVE`, `SUSPENDED`, `TERMINATED`).
8. Read `src/admin_backend/repositories/permission_matrix.py` — closest precedent for the `/matrix` endpoint's CROSS JOIN pattern. The permission-matrix Repo builds a synthesized grid (cells position-aligned to permissions); `/module-access/matrix` does the same but for (tenant × module) instead of (role × permission).
9. Read `src/admin_backend/repositories/tenants.py` — closest precedent for `/matrix`'s sort/filter/pagination posture. The `list_with_aggregates` method has the canonical SORT_MAP, search ILIKE, tier/status filters, and offset/limit pagination shape. Step 6.4 added 4 aggregate sort keys; the column-key ones (`name_*`, `created_at_*`, `tier_*`) are the vocabulary `/matrix` reuses.
10. Read `src/admin_backend/routers/v1/rbac.py` — closest precedent for label-resolution in handler responses. The permission-matrix endpoint resolves 4 labels (module / resource / action / scope) via lookups JOINs.
11. Read `src/admin_backend/routers/v1/dashboard.py` — closest precedent for **multi-user-type endpoint structure** (auth wiring, `Depends(get_auth_context)`, `Depends(get_tenant_session_dep)`). **Note**: the dashboard's scope-aware sub_text helpers (`auth.user_type` dispatch for "across all tenants" vs "in your organization") do NOT apply to Module Access — there are no scope-aware string fields in the response shape. Module Access response strings are static; RLS does all the projection at the data layer.
12. Read `tests/integration/test_rbac_router.py` — fixture machinery and the **M-tests** (E6 permission-matrix). The M2 position-alignment invariant test is the closest analogue to Step 6.7's load-bearing test (d) — module ordering consistent across `/modules` and `/matrix` cells.
13. Read `tests/integration/test_dashboard_router.py` — the load-bearing tests (S2, S5, S7, O2, O5) for multi-user-type RLS scoping. Step 6.7's tests follow the same shape.
14. Read `tests/integration/conftest.py` — confirm these factories exist: `make_tenant`, `make_tenant_module_access`. **No new factories expected.**
15. Read `BUILD_PLAN.md` — find the slot for this step. Provisional: **Step 6.7** (after Step 6.6 module enum unification). If slotted differently, surface and use the slotted ID.
16. Read `docs/endpoints/rbac.md` — closest precedent for the 8-section endpoint doc with 2+ endpoints sharing one file. Step 6.7's `docs/endpoints/module-access.md` follows the same shape.
17. Read `data/ithina_dev_seed_data.xlsx` — confirm seed shape: 7 tenants (4 ACTIVE, 1 TRIAL, 1 SUSPENDED, 1 TERMINATED — verify the exact distribution), 27 `tenant_module_access` rows. Manual curl verification at the bottom of this prompt depends on these counts.

17a. **Verify the existing `module_code` lookup rows' `display_order` values match the screenshot ordering.** The locked sequence is `ROOS → Goal Console → Pricing OS → Perishables Assistant → Promotions Assistant → Admin`. Step 3.4.5's seed listed the rows as `ROOS, PRICING_OS, PERISHABLES_ASSISTANT, PROMOTIONS_ASSISTANT, GOAL_CONSOLE, ADMIN` — note that GOAL_CONSOLE is fifth in the seed-list but second in the screenshot. Run:
   ```bash
   psql "$PSQL_URL" -c "SELECT code, display_order FROM core.lookups WHERE list_name = 'module_code' ORDER BY display_order;"
   ```
   - Expected (matching screenshot): ROOS=1, GOAL_CONSOLE=2, PRICING_OS=3, PERISHABLES_ASSISTANT=4, PROMOTIONS_ASSISTANT=5, ADMIN=6.
   - If the live state has GOAL_CONSOLE at display_order=5: **the lookups seed migration in this step also UPDATEs the `module_code` rows' display_order values to match the screenshot.** Add an `UPDATE core.lookups SET display_order = ... WHERE list_name = 'module_code' AND code = ...` block to the migration. The change is forward-only (downgrade raises NotImplementedError as usual).
   - If display_order already matches: no UPDATE needed; the migration only adds the new tenant_tier / tenant_status rows.

17b. **Verify UNIQUE constraint on `tenant_module_access(tenant_id, module)`.** The /matrix's LEFT JOIN against `tenant_module_access` assumes at most one row per (tenant, module) pair. If the constraint is missing, the LEFT JOIN could produce duplicate cells. Run:
   ```bash
   psql "$PSQL_URL" -c "
   SELECT conname, pg_get_constraintdef(oid)
   FROM pg_constraint
   WHERE conrelid = 'core.tenant_module_access'::regclass
     AND contype = 'u';
   "
   ```
   Expected: a UNIQUE constraint on `(tenant_id, module)`. If absent, surface (Stop-and-ask trigger #9) — the SQL needs adjustment (e.g., `DISTINCT ON` or aggregation in the JOIN).

17c. Verify `tenant_module_access.status` enum value for "active" is `'ENABLED'`. The locked SQL hardcodes `tma.status = 'ENABLED'`. Read `models/tenant_module_access.py`'s `TenantModuleAccessStatus` enum (or equivalent name). If different, adjust SQL.
18. Read `scripts/smoke_curl.sh` — find the existing assertions structure. Two new assertions land for the new endpoints.
19. Read this prompt fully.

---

## Step ID and intent

**Step 6.7** — Module Access read endpoints. Two new GET endpoints backing the Module Access governance console page (Frontend spec).

**Endpoints in scope:**

| # | Method + path | Auth | Visibility |
|---|---|---|---|
| E1 | `GET /api/v1/module-access/modules` | multi-user-type | PLATFORM sees fleet-wide aggregates (`enabled_count` over visible tenants); TENANT sees own-tenant aggregates collapsed to 0/1 or 1/1 via RLS |
| E2 | `GET /api/v1/module-access/matrix` | multi-user-type | PLATFORM sees N tenant rows (N = visible non-terminated tenants); TENANT sees exactly 1 row (own tenant) via RLS |

**Forward notes (NOT in scope this step):**

- **MODULE-ACCESS-WRITE** — POST/PATCH endpoint to enable/disable a module for a tenant. Includes the cascade: "Disabling instantly revokes all related role permissions" (per the screen's blurb). Lands when the toggle UI is integrated. ~Future Step 6.8 or beyond.
- **TIER-INDUSTRY-FILTER-EXTENSION** — `/matrix` filtering by industry, multi-tier filter. v0 ships with single-value tier and status filters only.
- **MODULE-CARD-TIME-SERIES** — per-card trend ("Enabled in X / Y tenants — was X' last month"). Future.
- **PER-ROLE PERMISSION CASCADE PREVIEW** — when toggling a module to DISABLED, frontend wants to show "this will revoke N permissions across M roles." Backend would surface the cascade preview via a separate endpoint. Future.

**Concrete deliverables:**

1. New `ModulesAccessRepo` at `repositories/modules_access.py` with two methods (`list_modules_with_aggregates`, `list_matrix`).
2. New router at `routers/v1/modules_access.py` with both endpoints under `/module-access` prefix.
3. New schemas at `schemas/modules_access.py`: response models for both endpoints + the cell shape + the row shape.
4. Single SQL query per endpoint:
   - `/modules`: one query with conditional aggregation over `tenant_module_access` JOIN `tenants` + `lookups`.
   - `/matrix`: one query producing the synthesized grid via `tenants × modules LEFT JOIN tenant_module_access`, then sort/paginate at the tenant level.
5. **Server-side label resolution per the new convention** — sibling-field shape `<field>` + `<field>_label`. Three fields per matrix row carry labels: `tier_label`, `status_label`, plus `module_label` per cell (and per `/modules` card).
6. Wire-up in `routers/v1/__init__.py`.
7. Integration tests at `tests/integration/test_modules_access_router.py` — ~14 tests, **5 load-bearing**: (a) `/matrix` TENANT 1-row, (b) `/modules` aggregate collapse under TENANT, (c) synthesized DISABLED cells respect RLS, (d) module ordering position-alignment, (e) label resolution with COALESCE fallback.
8. `docs/endpoints/module-access.md` — 8-section format covering both endpoints in one file.
9. **One small additive migration**: seed `lookups` rows for `list_name='tenant_tier'` and `list_name='tenant_status'`. Mirror Step 6.1's `22ccfb193cff` lookups seed shape: idempotent via `ON CONFLICT (list_name, code) DO NOTHING`, forward-only, downgrade raises NotImplementedError per the project convention. Roughly 9 rows total (4 tier values × ENTERPRISE / MID_MARKET / SMB / SINGLE_STORE + 5 status values × ONBOARDING / TRIAL / ACTIVE / SUSPENDED / TERMINATED). The migration's `display_order` values follow lifecycle / commercial-tier ordering (verify in pre-flight what makes sense). **No DDL changes. No seed Excel changes.** This migration unblocks the new label-handling convention's first use — without it, `tier_label` and `status_label` degrade to raw enum codes.
10. CLAUDE.md update: add Step 6.7 Completed bullet; **codify the new label-handling convention as a one-line note** alongside the existing convention notes (PG enum, batch-by-key envelope, v0 auth model, seed Excel shape).
11. BUILD_PLAN.md update: Step 6.7 entry; reference Step 6.6 as a hygiene-precedent dependency.
12. `scripts/smoke_curl.sh`: 2 new assertions (one per new endpoint, PLATFORM JWT). Other 3 workflow scripts unchanged.

CLAUDE_CODE step. Read endpoints with multi-user-type RLS pattern + new label-resolution convention + small lookups-seed migration. Expect ~4 hours.

---

## Locked endpoint contracts

### E1 — `GET /api/v1/module-access/modules`

**Request:** no query params. Both PLATFORM and TENANT JWTs accepted.

**Response 200 (PLATFORM JWT)** — fleet-wide aggregates:

```jsonc
{
  "items": [
    {
      "module_code": "ROOS",
      "module_label": "ROOS",
      "enabled_count": 4,
      "total_active_trial_tenants": 5
    },
    {
      "module_code": "GOAL_CONSOLE",
      "module_label": "Goal Console",
      "enabled_count": 2,
      "total_active_trial_tenants": 5
    },
    {
      "module_code": "PRICING_OS",
      "module_label": "Pricing OS",
      "enabled_count": 4,
      "total_active_trial_tenants": 5
    },
    {
      "module_code": "PERISHABLES_ASSISTANT",
      "module_label": "Perishables Assistant",
      "enabled_count": 5,
      "total_active_trial_tenants": 5
    },
    {
      "module_code": "PROMOTIONS_ASSISTANT",
      "module_label": "Promotions Assistant",
      "enabled_count": 3,
      "total_active_trial_tenants": 5
    },
    {
      "module_code": "ADMIN",
      "module_label": "Admin",
      "enabled_count": 5,
      "total_active_trial_tenants": 5
    }
  ]
}
```

**Response 200 (TENANT JWT, caller in Buc-ee's with all 6 modules enabled)**:

```jsonc
{
  "items": [
    { "module_code": "ROOS", "module_label": "ROOS", "enabled_count": 1, "total_active_trial_tenants": 1 },
    { "module_code": "GOAL_CONSOLE", "module_label": "Goal Console", "enabled_count": 1, "total_active_trial_tenants": 1 },
    { "module_code": "PRICING_OS", "module_label": "Pricing OS", "enabled_count": 1, "total_active_trial_tenants": 1 },
    { "module_code": "PERISHABLES_ASSISTANT", "module_label": "Perishables Assistant", "enabled_count": 1, "total_active_trial_tenants": 1 },
    { "module_code": "PROMOTIONS_ASSISTANT", "module_label": "Promotions Assistant", "enabled_count": 1, "total_active_trial_tenants": 1 },
    { "module_code": "ADMIN", "module_label": "Admin", "enabled_count": 1, "total_active_trial_tenants": 1 }
  ]
}
```

**Locked invariants for E1:**

| # | Invariant |
|---|---|
| M1 | Response shape identical for both user types (sample N varies by RLS scope; shape doesn't fork). |
| M2 | `items[]` always has 6 entries — one per module in `module_code_enum`. Items ordered by `lookups.display_order ASC, module_code ASC`. The ordering is deterministic and stable across calls. |
| M3 | `total_active_trial_tenants` = `COUNT(*) FROM tenants WHERE status IN ('ACTIVE', 'TRIAL')` over visible (RLS-filtered) tenants. Same value on every card in the response (it's a row-set property, not a per-module value). |
| M4 | `enabled_count` = `COUNT(DISTINCT tenant_id) FROM tenant_module_access tma JOIN tenants t ON tma.tenant_id = t.id WHERE tma.module = <this_card.module_code> AND tma.status = 'ENABLED' AND t.status IN ('ACTIVE', 'TRIAL')`. RLS-scoped through both joined tables. |
| M5 | `module_code` and `module_label` resolved via JOIN against `lookups` where `list_name='module_code'`. `module_label` uses `COALESCE(lookups.display_name, module_code::text)` so if a lookup row is missing the label degrades to the raw enum code rather than failing. |
| M6 | No pagination, no query params, no sort param. Fixed-shape reference data. |
| M7 | **Independent of `/matrix` filters.** Even if the same caller queries both endpoints with `/matrix?tier=ENTERPRISE`, `/modules` returns aggregates over the full ACTIVE+TRIAL tenant set (subject to RLS), not the filtered subset. The two endpoints carry separate scopes; the cards are page-level KPIs, the matrix is a filtered view below them. |

### E2 — `GET /api/v1/module-access/matrix`

**Request:**

| Param | Type | Default | Vocabulary |
|---|---|---|---|
| `limit` | int | 25 | 1-200 |
| `offset` | int | 0 | ≥ 0 |
| `sort` | str | `tier_asc` (with secondary `name_asc, id_asc`) | `name_asc`, `name_desc`, `created_at_asc`, `created_at_desc`, `tier_asc`, `tier_desc` |
| `tier` | str | (none) | `ENTERPRISE`, `MID_MARKET`, `SMB`, `SINGLE_STORE` (one value, not a list) |
| `status` | str | (none) | `ACTIVE`, `TRIAL`, `SUSPENDED`, `ONBOARDING` (one value; `TERMINATED` is implicitly excluded — see below) |
| `q` | str | (none) | Free-text search across `tenants.name` (ILIKE, case-insensitive) |

Both PLATFORM and TENANT JWTs accepted.

**Response 200 (PLATFORM JWT)** — full grid:

```jsonc
{
  "items": [
    {
      "tenant_id": "972a8469-1641-4f82-8b9d-2434e465e150",
      "name": "Buc-ee's",
      "tier": "ENTERPRISE",
      "tier_label": "Enterprise",
      "status": "ACTIVE",
      "status_label": "Active",
      "cells": [
        { "module_code": "ROOS", "status": "ENABLED" },
        { "module_code": "GOAL_CONSOLE", "status": "ENABLED" },
        { "module_code": "PRICING_OS", "status": "ENABLED" },
        { "module_code": "PERISHABLES_ASSISTANT", "status": "ENABLED" },
        { "module_code": "PROMOTIONS_ASSISTANT", "status": "ENABLED" },
        { "module_code": "ADMIN", "status": "ENABLED" }
      ]
    },
    {
      "tenant_id": "...",
      "name": "Żabka Group",
      "tier": "ENTERPRISE",
      "tier_label": "Enterprise",
      "status": "ACTIVE",
      "status_label": "Active",
      "cells": [
        { "module_code": "ROOS", "status": "ENABLED" },
        { "module_code": "GOAL_CONSOLE", "status": "DISABLED" },
        { "module_code": "PRICING_OS", "status": "ENABLED" },
        { "module_code": "PERISHABLES_ASSISTANT", "status": "ENABLED" },
        { "module_code": "PROMOTIONS_ASSISTANT", "status": "ENABLED" },
        { "module_code": "ADMIN", "status": "ENABLED" }
      ]
    }
    // ... up to limit; total in pagination
  ],
  "pagination": {
    "limit": 25,
    "offset": 0,
    "total": 6
  }
}
```

**Response 200 (TENANT JWT, caller in Buc-ee's)**:

```jsonc
{
  "items": [
    {
      "tenant_id": "972a8469-1641-4f82-8b9d-2434e465e150",
      "name": "Buc-ee's",
      "tier": "ENTERPRISE",
      "tier_label": "Enterprise",
      "status": "ACTIVE",
      "status_label": "Active",
      "cells": [ /* 6 cells, all in lookups.display_order */ ]
    }
  ],
  "pagination": { "limit": 25, "offset": 0, "total": 1 }
}
```

**Locked invariants for E2:**

| # | Invariant |
|---|---|
| X1 | Row set: `tenants WHERE status != 'TERMINATED'`. ACTIVE + TRIAL + SUSPENDED + ONBOARDING all appear. TERMINATED tenants are excluded. RLS-filtered. |
| X2 | Each row's `cells[]` has exactly 6 entries — one per module in `module_code_enum`. **Position-aligned across rows AND with E1's `items[]` ordering**: `cells[0].module_code` is the same for every row in the response and matches `E1.items[0].module_code`. Frontend reconciles cell-to-module by index. |
| X3 | Cell synthesis: backend produces the full N × 6 grid via `tenants` (RLS-filtered) CROSS JOIN `module_code` lookups LEFT JOIN `tenant_module_access`. Cells where the LEFT JOIN matches a row with `status='ENABLED'` render as `"ENABLED"`; absent rows OR rows with `status='DISABLED'` both render as `"DISABLED"`. Frontend doesn't distinguish absent vs explicitly-disabled. |
| X4 | `tier`, `tier_label`, `status`, `status_label` all populated. Labels resolved via JOIN against `lookups` where `list_name IN ('tenant_tier', 'tenant_status')` (or whatever the actual list_names are — verify in pre-flight item 17). COALESCE fallback to raw enum code. |
| X5 | `total` in pagination = total visible non-terminated tenants matching active filters (q / tier / status), NOT `limit`-capped. |
| X6 | Sort vocabulary mirrors `/tenants`'s vocabulary (Step 6.4): `name_asc/desc`, `created_at_asc/desc`, `tier_asc/desc`. Default `tier_asc` with secondary stable sort `name_asc, id_asc`. **The 4 aggregate sort keys from `/tenants` (`num_users_active_*`, `num_stores_*`) are NOT in scope for `/matrix`** — the matrix doesn't expose those aggregates per row, so sorting by them is meaningless here. |
| X7 | Filter on `tier` and `status` are exact-match (not multi-value). `q` is ILIKE substring search on `tenants.name` only. |
| X8 | Invalid sort key → 400 INVALID_SORT_KEY (reuses `InvalidSortKeyClientError` from Step 5.2). Invalid `tier` or `status` value → FastAPI's standard 422 unprocessable entity (Pydantic enum validation). Invalid `limit` (>200 or <1) → 422. |
| X9 | RLS posture: `tenants` and `tenant_module_access` both have RLS policies (D-29 OR-clause). PLATFORM sees all visible tenants; TENANT sees own only. The CROSS JOIN's left side is RLS-filtered; the LEFT JOIN to `tenant_module_access` is also RLS-filtered. Both filters compose correctly: under TENANT JWT, the matrix has 1 row with 6 cells reflecting that tenant's actual `tenant_module_access` rows. |
| X10 | Pydantic `extra='forbid'` on all response models guards against drift. |

---

## Locked SQL: /modules query

One query producing all 6 cards. Lookups JOIN provides ordering + label resolution; conditional aggregation provides per-module enabled counts and the shared denominator.

```sql
WITH visible_tenants AS (
    SELECT id, status
    FROM tenants
    WHERE status IN ('ACTIVE', 'TRIAL')
    -- RLS filters this naturally; this is the visible subset for aggregates
),
enabled_per_module AS (
    SELECT
        tma.module,
        COUNT(DISTINCT tma.tenant_id) AS enabled_count
    FROM tenant_module_access tma
    JOIN visible_tenants vt ON tma.tenant_id = vt.id
    WHERE tma.status = 'ENABLED'
    GROUP BY tma.module
),
total_count AS (
    SELECT COUNT(*) AS total_active_trial_tenants FROM visible_tenants
)
SELECT
    lk.code AS module_code,
    COALESCE(lk.display_name, lk.code) AS module_label,
    COALESCE(epm.enabled_count, 0) AS enabled_count,
    tc.total_active_trial_tenants
FROM lookups lk
LEFT JOIN enabled_per_module epm ON epm.module::text = lk.code
CROSS JOIN total_count tc
WHERE lk.list_name = 'module_code'
ORDER BY lk.display_order ASC, lk.code ASC;
```

**Notes:**

- `epm.module::text = lk.code` — required because `tenant_module_access.module` is `module_code_enum` (post-Step-6.6), and `lookups.code` is text; PG rejects implicit enum-to-text equality.
- `COALESCE(lk.display_name, lk.code)` — defensive fallback if a lookup row is missing for some reason. Same posture as Step 6.1's permission-matrix.
- `COALESCE(epm.enabled_count, 0)` — modules with zero ENABLED rows still appear in the response with `enabled_count=0` (LEFT JOIN preserves them).
- `CROSS JOIN total_count tc` — single-row CTE multiplied across the 6 module rows; no Cartesian explosion.
- RLS does the work via session GUCs. Both `tenants` and `tenant_module_access` have D-29 OR-clause policies.

---

## Locked SQL: /matrix query

Synthesizes the full N × 6 grid via tenant × module CROSS JOIN, LEFT JOIN against `tenant_module_access` for actual enablement status. Pagination applies at the tenant level (must paginate before the CROSS JOIN expands rows).

Two-stage approach:

**Stage 1 (tenant page):** Get the visible tenant page with sort/filter/pagination applied.

```sql
SELECT
    t.id,
    t.name,
    t.tier,
    t.status,
    t_tier_lk.display_name AS tier_label,
    t_status_lk.display_name AS status_label,
    t.created_at
FROM tenants t
LEFT JOIN lookups t_tier_lk ON t_tier_lk.list_name = 'tenant_tier'
    AND t_tier_lk.code = t.tier::text
LEFT JOIN lookups t_status_lk ON t_status_lk.list_name = 'tenant_status'
    AND t_status_lk.code = t.status::text
WHERE t.status != 'TERMINATED'
    -- Optional filters applied here (tier, status, q ILIKE on name)
ORDER BY <sort_clause>, t.id ASC
LIMIT :limit OFFSET :offset;
```

**Stage 2 (cells per page):** For each tenant in the page, build the 6 cells. One query per page (not per tenant) using a CTE pattern:

```sql
WITH page_tenants AS (<stage 1 query>),
modules_ordered AS (
    SELECT code AS module_code, display_order
    FROM lookups
    WHERE list_name = 'module_code'
    ORDER BY display_order ASC, code ASC
)
SELECT
    pt.id AS tenant_id,
    mo.module_code,
    CASE
        WHEN tma.status = 'ENABLED' THEN 'ENABLED'
        ELSE 'DISABLED'
    END AS cell_status
FROM page_tenants pt
CROSS JOIN modules_ordered mo
LEFT JOIN tenant_module_access tma
    ON tma.tenant_id = pt.id
    AND tma.module::text = mo.module_code  -- enum-to-text cast required, same trap as Step 3.4.5
ORDER BY pt.<sort_columns>, pt.id ASC, mo.display_order ASC;
```

**Stage 3 (total count):** Separate query for pagination total. Same WHERE filters as stage 1; no JOINs needed since we just count.

```sql
SELECT COUNT(*)
FROM tenants
WHERE status != 'TERMINATED'
    -- Same optional filters as stage 1: tier, status, q ILIKE on name
;
```

RLS still applies via session GUCs (the COUNT runs in the same RLS-bound session). Under PLATFORM JWT this counts all visible non-terminated tenants matching the filters; under TENANT JWT this returns 0 or 1.

The Repo's `list_matrix` method runs all three stages and returns:
1. The list of `TenantRow` objects (from stage 1).
2. A dict `{tenant_id: list[Cell]}` (from stage 2, grouped by tenant_id in Python).
3. The total count for pagination (a separate count query).

The router assembles the response by iterating stage 1 rows in order and looking up cells from the dict.

**Alternative implementation (single query):** A more clever single-query approach could use `jsonb_agg` to produce the cells array per row directly. **Decision: prefer the two-stage approach** for clarity. Single-query with `jsonb_agg` works but is harder to reason about, and the perf delta is negligible at v0 fleet scale. Mirror Step 6.1's permission-matrix Repo's pattern (which assembles in Python from separate queries).

**Notes:**

- `t.tier::text = t_tier_lk.code` — same enum-to-text cast as the `/modules` JOIN.
- The matrix sort vocabulary doesn't include aggregate keys; only column-based keys.
- Total count uses a separate query (`SELECT COUNT(*) FROM tenants WHERE status != 'TERMINATED' AND <filters>` without joins) — same pattern as `/tenants`'s pagination.
- RLS is applied via session GUCs, not SQL clauses. Both `tenants` and `tenant_module_access` are RLS-protected.

---

## Locked label-handling convention (codified)

**Rule:** Every enum-coded field in a response from a Step-6.7-or-later endpoint includes a sibling `<field>_label` field carrying the human-readable display name. Resolution: LEFT JOIN against `lookups` where `list_name = '<the lookup list for this enum>'` AND `code = <field>::text`. Use `COALESCE(lookups.display_name, <field>::text)` so missing lookup rows degrade gracefully to the raw enum code.

**Field naming:** `<field>_label`. Always sibling, never nested object. Always present (never null — COALESCE guarantees a non-null value).

**Backwards compatibility:** This rule applies to NEW endpoints from Step 6.7 forward. Existing endpoints (`/tenants`, `/tenant-users`, `/platform-users`, `/roles`, `/org-tree`, `/dashboard/*`) stay bare-enum; frontend handles their resolution client-side via `/lookups`. Do NOT retrofit older endpoints in this step.

**CLAUDE.md codification:** Add a one-line convention note alongside existing conventions (PG enum, batch-by-key, v0 auth model, seed Excel shape, sort-stability). Suggested wording:

> **Note on label resolution.** Endpoints from Step 6.7 onward return enum-coded fields with sibling `<field>_label` strings, resolved server-side via LEFT JOIN against the relevant `lookups.list_name` with COALESCE(display_name, code::text) fallback. Older endpoints (Steps 3.x / 5.x / 6.5) retain bare enum codes; frontend handles client-side resolution against `/lookups`. This asymmetry is intentional — retrofitting older endpoints is out of scope unless explicitly prompted; new endpoints follow the new rule.

---

## Files to create/modify

Claude Code investigates the existing codebase and writes the actual code. The contracts above are locked; the implementation pattern follows existing precedents.

### `migrations/versions/<rev>_seed_tenant_tier_and_status_lookups.py` — new

Generate via `uv run alembic revision -m "seed tenant_tier and tenant_status lookups"` (no `--autogenerate`; project's `env.py` keeps `target_metadata = None`). Mirror Step 6.1's `22ccfb193cff` migration shape: idempotent INSERTs via `ON CONFLICT (list_name, code) DO NOTHING`, forward-only, downgrade raises `NotImplementedError`.

Migration body (sketch):

```python
"""Seed lookups rows for tenant_tier and tenant_status.

Step 6.7's locked label-handling convention requires server-side
resolution of enum codes via lookups. The four enum lists Step 6.1
seeded ('module', 'resource', 'permission_action', 'permission_scope')
plus Step 3.4.5's 'module_code' do not cover tenant_tier or
tenant_status; this migration adds them.

Also (if pre-flight verifies the existing module_code display_order
doesn't match the screenshot ordering): UPDATE the module_code rows'
display_order so /modules and /matrix produce the locked sequence
(ROOS, GOAL_CONSOLE, PRICING_OS, PERISHABLES_ASSISTANT,
PROMOTIONS_ASSISTANT, ADMIN).

Forward-only — downgrade raises NotImplementedError per project
convention.
"""

revision: str = "<generated>"
down_revision: str = "cec8fae734e0"  # Step 6.6 unification

def upgrade() -> None:
    op.execute("""
        INSERT INTO lookups (list_name, code, display_name, display_order, is_active) VALUES
            ('tenant_tier', 'ENTERPRISE', 'Enterprise', 1, TRUE),
            ('tenant_tier', 'MID_MARKET', 'Mid-Market', 2, TRUE),
            ('tenant_tier', 'SMB', 'SMB', 3, TRUE),
            ('tenant_tier', 'SINGLE_STORE', 'Single-Store', 4, TRUE),
            ('tenant_status', 'ONBOARDING', 'Onboarding', 1, TRUE),
            ('tenant_status', 'TRIAL', 'Trial', 2, TRUE),
            ('tenant_status', 'ACTIVE', 'Active', 3, TRUE),
            ('tenant_status', 'SUSPENDED', 'Suspended', 4, TRUE),
            ('tenant_status', 'TERMINATED', 'Terminated', 5, TRUE)
        ON CONFLICT (list_name, code) DO NOTHING;
    """)

    # Conditional: only emit the UPDATE if pre-flight 17a verified
    # display_order doesn't match. If display_order already matches
    # the screenshot ordering, omit this block.
    # op.execute("""
    #     UPDATE lookups SET display_order = CASE code
    #         WHEN 'ROOS' THEN 1
    #         WHEN 'GOAL_CONSOLE' THEN 2
    #         WHEN 'PRICING_OS' THEN 3
    #         WHEN 'PERISHABLES_ASSISTANT' THEN 4
    #         WHEN 'PROMOTIONS_ASSISTANT' THEN 5
    #         WHEN 'ADMIN' THEN 6
    #     END
    #     WHERE list_name = 'module_code';
    # """)


def downgrade() -> None:
    raise NotImplementedError(
        "Step 6.7 lookups seed is forward-only. Manual DELETE if "
        "rollback is required."
    )
```

Verify the actual `tenants.tier` and `tenants.status` enum values match the codes above before writing the migration. The vocabulary above is the prompt's assumption; pre-flight Stop-and-ask #1 / #2 catches any discrepancy.

### `src/admin_backend/schemas/modules_access.py` — new

Pydantic v2 models, `model_config = ConfigDict(extra="forbid")` on every model. Mirror conventions from `schemas/dashboard.py` and `schemas/permission.py`.

Required types:

- `ModuleCard` — fields per E1 row (module_code, module_label, enabled_count, total_active_trial_tenants). `module_code: Literal["ROOS", "GOAL_CONSOLE", "PRICING_OS", "PERISHABLES_ASSISTANT", "PROMOTIONS_ASSISTANT", "ADMIN"]` so the OpenAPI snapshot carries the enum vocabulary for frontend codegen. `module_label`, `enabled_count`, `total_active_trial_tenants` are plain `str` / `int`.
- `ModulesResponse` — `{items: list[ModuleCard]}`.
- `MatrixCell` — `{module_code, status}`. Both `Literal` types: same module_code Literal as above, and `status: Literal["ENABLED", "DISABLED"]`.
- `MatrixRow` — `{tenant_id: UUID, name: str, tier, tier_label, status, status_label, cells: list[MatrixCell]}`. `tier: Literal["ENTERPRISE", "MID_MARKET", "SMB", "SINGLE_STORE"]`, `status: Literal["ONBOARDING", "TRIAL", "ACTIVE", "SUSPENDED"]` (TERMINATED excluded — never appears in matrix rows). `*_label` are plain `str`. **Verify the actual enum values in pre-flight (Stop-and-ask #1 / #2) before locking these Literal vocabularies; if the enum has different values, adjust.**
- `MatrixResponse` — `{items: list[MatrixRow], pagination: Pagination}` (reuse existing Pagination from `schemas/__init__.py` or wherever it lives).

Re-export the new symbols from `schemas/__init__.py`.

### `src/admin_backend/repositories/modules_access.py` — new

Stateless singleton class `ModulesAccessRepo` (mirror `DashboardRepo`, `PermissionMatrixRepo`).

Two methods:

- `async def list_modules_with_aggregates(self, session: AsyncSession) -> list[ModuleCardRow]` — runs the locked `/modules` SQL, returns 6 frozen-dataclass rows.
- `async def list_matrix(self, session: AsyncSession, *, sort: str, tier: TenantTier | None, status: TenantStatus | None, q: str | None, limit: int, offset: int) -> tuple[list[MatrixTenantRow], dict[UUID, list[MatrixCellRow]], int]` — runs the locked `/matrix` 2-stage SQL, returns (tenant rows, cells grouped by tenant_id, total).

Sort key validation reuses `InvalidSortKeyError` from `repositories/_errors.py` (Step 5.2). The router catches and re-raises as `InvalidSortKeyClientError`.

`MATRIX_SORT_MAP` module-level dict mirroring Step 6.4's `_BASE_TENANTS_SORT_MAP`. 6 keys total: `name_asc`, `name_desc`, `created_at_asc`, `created_at_desc`, `tier_asc`, `tier_desc`. NO aggregate keys (those are `/tenants`-only).

The dataclass returns from `list_matrix` are framework-agnostic (no Pydantic in the Repo); the router maps them to schema models.

### `src/admin_backend/routers/v1/modules_access.py` — new

`APIRouter(prefix="/module-access", tags=["module-access"])`. Two endpoints:

- `GET /modules` → `response_model=ModulesResponse`. Calls `ModulesAccessRepo().list_modules_with_aggregates(session)`. Maps Repo dataclasses to `ModuleCard` schema. No sub_text formatting helpers needed (no string formatting beyond label resolution which happens in SQL).
- `GET /matrix` → `response_model=MatrixResponse`. Validates sort/tier/status query params. Calls `ModulesAccessRepo().list_matrix(session, ...)`. Assembles response by iterating tenant rows in order and attaching cells from the grouped dict. Builds `Pagination` from `(limit, offset, total)`.

Both endpoints depend on `Depends(get_auth_context)` and `Depends(get_tenant_session_dep)`. No `_require_platform_auth(...)` gate — both user types accepted, RLS does the work.

`description` text on each endpoint should call out the multi-user-type RLS behavior.

Wire the router in `routers/v1/__init__.py` mirroring existing `include_router` calls.

### `tests/integration/test_modules_access_router.py` — new

~14 tests. Reuse fixture machinery from `test_dashboard_router.py` and `test_rbac_router.py`. **No new conftest factories.**

Test ID convention:

- `M*` — `/modules` E1 (5 tests)
- `X*` — `/matrix` E2 (8 tests)
- `A*` — auth (1 test, both endpoints)

**Five LOAD-BEARING tests:**

| ID | Verifies |
|---|---|
| **X1** | TENANT JWT `/matrix` returns exactly 1 row (own tenant only). RLS scoping. |
| **M2** | TENANT JWT `/modules` aggregate counts collapse: every card has `total_active_trial_tenants=1` and `enabled_count` is 0 or 1. RLS-on-aggregate. |
| **X2** | Synthesized DISABLED cells respect RLS — for a tenant with 3 ENABLED rows in `tenant_module_access` and 6 modules total, `cells[]` has 6 entries (3 ENABLED, 3 DISABLED), and PLATFORM JWT sees all visible tenants' synthesized cells correctly. CROSS JOIN safety. |
| **M3** | Module ordering position-alignment: `M1.items[i].module_code == X3.items[any_row].cells[i].module_code` for all i. Cross-endpoint invariant — frontend's reconciliation depends on this holding. |
| **M4** | Labels populate correctly under normal seed conditions: assert `tier_label="Enterprise"` for an Enterprise tenant, `status_label="Active"` for ACTIVE status, `module_label="Pricing OS"` for PRICING_OS module on the matrix's first cell. Verifies the label JOIN works end-to-end against the seeded `tenant_tier`, `tenant_status`, and `module_code` lookup rows that this step's migration introduces. (COALESCE-fallback behavior is tested separately at the unit test layer if needed; integration test asserts the happy path.) |

**Other tests:**

- M1 `/modules` envelope (PLATFORM): all 6 cards with expected fields and ordering.
- M5 `/modules` `enabled_count` reflects only `status='ENABLED'` rows (DISABLED rows in tenant_module_access don't count).
- X3 `/matrix` envelope (PLATFORM): N rows, each with 6 cells.
- X4 `/matrix` sort=`name_asc` orders alphabetically.
- X5 `/matrix` sort=invalid → 400 INVALID_SORT_KEY.
- X6 `/matrix` filter `tier=ENTERPRISE` returns only enterprise tenants.
- X7 `/matrix` filter `status=ACTIVE` returns only active tenants.
- X8 `/matrix` `q=Buc` returns Buc-ee's via name search.
- X9 `/matrix` pagination: `limit=2&offset=2` returns 2 rows, `total` matches full visible count.
- A1 no JWT → 401 on both endpoints.

### `docs/endpoints/module-access.md` — new

8-section format × 2 endpoints in one file. Mirror `docs/endpoints/rbac.md` (which covers 4 endpoints in one file).

Section 5 (behaviour notes) covers:
- Multi-user-type RLS pattern (TENANT collapses to own-tenant view).
- Server-side label resolution convention (the new rule, codified at this step).
- Cell synthesis (absent rows AND `status='DISABLED'` rows both render as `"DISABLED"`).
- Position-alignment invariant on `cells[]` ordering.
- Aggregate semantics: ACTIVE + TRIAL denominator (different from dashboard's `!= 'TERMINATED'`).
- Why TERMINATED is excluded from the matrix row set.

Section 7 (TypeScript snippet) shows the render pattern for both endpoints.

### `scripts/smoke_curl.sh` — modify

Add 2 new assertions:

```bash
# Module Access — modules catalogue
curl -fsS -H "Authorization: Bearer $PJWT" "$BASE/api/v1/module-access/modules" \
  | jq -e '.items | length == 6' >/dev/null \
  && echo "PASS: module-access modules" || echo "FAIL: module-access modules"

# Module Access — matrix
curl -fsS -H "Authorization: Bearer $PJWT" "$BASE/api/v1/module-access/matrix?limit=10" \
  | jq -e '.items | length > 0 and (.[0].cells | length == 6)' >/dev/null \
  && echo "PASS: module-access matrix" || echo "FAIL: module-access matrix"
```

Update the WHAT'S CHECKED count comment at the top: 18 → 20.

The other three workflow scripts (`scripts/deploy-cloud-run.sh`, `scripts/env.sh`, `scripts/jwt/generate_7d.sh`) — **no change.** Confirm in the report.

### CLAUDE.md — modify

- **Current state → Completed:** Step 6.7 Completed bullet covering both endpoints, the `ModulesAccessRepo`, the schemas, the 5 load-bearing tests, the new label-handling convention codification, the `smoke_curl.sh` extension.
- **New convention note:** Add the "Note on label resolution" one-liner per the locked text in §"Locked label-handling convention" above. Place it in the "Code conventions and structure" section alongside the existing convention notes.
- **No new D-XX entries.** The label-handling rule is a convention, not a decision (sibling-field shape was the obvious one given D-31 append-only + Step 6.1 precedent).
- **No new FN-AB entries.**
- **Schema state line:** unchanged at 11 application tables, 19 enums (Step 6.6's count after `module_enum` retirement). Smoke count unchanged at 74.

### BUILD_PLAN.md — modify

Add Step 6.7 entry. Status: TODO → DONE in same commit. Standard scope-in / scope-out / acceptance criteria / coordination structure mirroring Steps 6.5 / 6.6.

The "Coordination" section should explicitly call out:

- **Deploy state:** Steps 6.5, 6.5.1, 6.6 are at `origin/main` but the deployed Cloud Run image only includes 6.5 and 6.5.1. Step 6.6's migration `cec8fae734e0` has NOT yet run on Cloud SQL.
- **Next deploy must use `--migrate`.** This deploy bundles Steps 6.6 + 6.7. Without `--migrate`, the deployed code (post-6.6) expects `module_code_enum` while Cloud SQL still has `module_enum`, and `/permissions` + `/permission-matrix` will 500 immediately (cascading effect on Step 6.5's governance-stats card too).
- Post-deploy verification: confirm `module_enum` no longer exists in Cloud SQL AND `/api/v1/module-access/modules` and `/matrix` return 200.

### `prompts/step-6_7-module-access-read-endpoints-2026-05-06.md` — new

This prompt file. Bundled per the per-step convention.

### `docs/endpoints/openapi.json` — re-export

After all code is in and tests pass:

```bash
curl -s http://localhost:8000/api/v1/openapi.json | jq '.' > docs/endpoints/openapi.json
cat docs/endpoints/openapi.json | jq '.paths | keys' | grep module-access
# Expected: /api/v1/module-access/modules, /api/v1/module-access/matrix
```

### `docs/architecture.md` — likely no edit

This step doesn't change architecture. The label-resolution convention is captured in CLAUDE.md, not architecture.md.

---

## Testing and regression discipline

### New tests

~14 integration tests; **5 load-bearing** (M2, M3, M4, X1, X2 per the convention IDs above).

### Tests deliberately not added

- "OpenAPI schema validation tests." Snapshot is human-verified; no test asserts contents.
- "Performance under fleet scale (100+ tenants)." v0 fleet is 7; perf test is premature.
- "Cell synthesis when zero `tenant_module_access` rows exist for a tenant." Implicit in X2; doesn't need its own test.

### Regression risk surface

1. **Existing 17 tests in `test_dashboard_router.py` must stay green.** Especially `O2` (modules_deployed real-and-RLS-scoped) since `/dashboard/governance-stats` reads from the same `tenant_module_access` table. A drop in O2 means our RLS pattern broke somewhere.
2. **Existing 24 `test_rbac_router.py` tests must stay green.** Step 6.6's M2 position-alignment test in permission-matrix is the closest precedent — Step 6.7 borrows the same pattern. If the pattern got something subtly wrong in 6.6 it'd surface here too.
3. **Per-resource regression checkpoint.** Every prior router file at exactly its pre-step PASS count.
4. **`tenants.tier` and `tenants.status` enum vocabulary** must match what the JOINs assume. Verify in pre-flight item 17. If the actual enum values differ (e.g., `MID_MARKET` is actually `MID-MARKET` in DDL), the JOIN's text-cast comparison breaks.
5. **`lookups` rows for `tenant_tier` and `tenant_status` are seeded by this step's migration.** Pre-step they don't exist (Step 3.4.5 seeded `module_code`; Step 6.1 seeded `module`, `resource`, `permission_action`, `permission_scope`). The migration in Step 6.7's locked deliverables adds them. If the migration somehow runs incompletely (transaction failure, partial rollback), the label JOINs return NULL and COALESCE falls back to raw enum codes — `tier_label` becomes `"ENTERPRISE"` not `"Enterprise"`, defeating the convention. The locked test M4 (labels populate correctly under normal seed) is the canary.
6. **Pagination total under filters.** The `total` count must apply the same filters as the items query; an off-by-filter bug would make `total` lie about how many results match. Non-trivial to test exhaustively but X9 covers the basic case.
7. **Pydantic `extra='forbid'`** drift guard: if any test machinery sends extra fields in mocks, the schema rejects. X1 and other test bodies need to assert against the exact field set.
8. **Sort applied within RLS-filtered scope.** Under TENANT JWT, sorting is moot (1 row), but the SQL still emits the ORDER BY clause. Verify it doesn't break under TENANT.
9. **`q` ILIKE search on tenants.name** — case-insensitive substring. Verify Postgres-side `ILIKE` semantics match expectations (no special character escaping needed for typical names).
10. **Empty-result corner cases.** `/matrix?status=TERMINATED` returns 0 rows (TERMINATED tenants are filtered out at the row-set level — the filter literally has no matching tenants). `/matrix?q=zzznonexistent` returns empty `items[]`, `total=0`. These don't need explicit tests but the SQL must handle them without errors.

### Verification harness (run all seven; all must be green)

```bash
# 1. Full pytest
uv run pytest -v

# 2. Per-resource regression checkpoint (LOAD-BEARING)
uv run pytest tests/integration/test_tenants_router.py -v
uv run pytest tests/integration/test_platform_users_router.py -v
uv run pytest tests/integration/test_tenant_users_router.py -v
uv run pytest tests/integration/test_org_tree_router.py -v
uv run pytest tests/integration/test_lookups_router.py -v
uv run pytest tests/integration/test_rbac_router.py -v
uv run pytest tests/integration/test_dashboard_router.py -v
# Each file must report 100% PASS at exactly its pre-step count.
# dashboard's O2 (modules_deployed RLS) is especially load-bearing as a
# canary for tenant_module_access RLS regression.

# 3. mypy strict
uv run mypy --strict src/admin_backend
# 57 source files (was 57 at end of Step 6.6 — Step 6.7 adds router, schemas, repo,
# but mypy may not count empty __init__ updates separately).

# 4. Pre-flight checker
./scripts/check_setup.sh

# 5. Alembic head advances by one (Step 6.7's lookups-seed migration)
uv run alembic heads
# Expected: new revision (Step 6.7's lookups seed) on top of cec8fae734e0
# (Step 6.6's unification).
uv run alembic check

# Verify the new lookups rows exist post-migration:
psql "$PSQL_URL" -c "
SELECT list_name, code, display_name, display_order
FROM core.lookups
WHERE list_name IN ('tenant_tier', 'tenant_status')
ORDER BY list_name, display_order;
"
# Expected: 4 tenant_tier rows + 5 tenant_status rows.

# 6. scripts/smoke_curl.sh — run against local dev (or post-deploy against Cloud Run)
bash scripts/smoke_curl.sh
# Expected: all PASS, count grows by +2 (was 18, now 20).

# 7. Manual curl verification
PJWT=$(./scripts/jwt/generate_7d.sh anjali@ithina.ai)
TJWT=$(./scripts/jwt/generate_7d.sh marcus.t@bucees.com)  # Buc-ee's tenant user

# /modules PLATFORM
curl -s -H "Authorization: Bearer $PJWT" "http://localhost:8000/api/v1/module-access/modules" | jq '.'
# Expected: 6 cards, ordered ROOS, Goal Console, Pricing OS, Perishables Assistant,
# Promotions Assistant, Admin. Each with module_label populated. enabled_count
# reflects ACTIVE + TRIAL tenants only. total_active_trial_tenants is consistent
# across all 6 cards.

# /modules TENANT (Buc-ee's)
curl -s -H "Authorization: Bearer $TJWT" "http://localhost:8000/api/v1/module-access/modules" | jq '.items[0]'
# Expected: enabled_count = 0 or 1, total_active_trial_tenants = 1 (just Buc-ee's).
# Same shape, RLS scoped.

# /matrix PLATFORM (default sort)
curl -s -H "Authorization: Bearer $PJWT" "http://localhost:8000/api/v1/module-access/matrix" | jq '.items | map({name, tier, status, cells: .cells | length})'
# Expected: 6 rows (7 tenants minus 1 TERMINATED if seed has one), each with cells: 6.
# Sorted by tier (Enterprise tenants first).

# /matrix TENANT (Buc-ee's)
curl -s -H "Authorization: Bearer $TJWT" "http://localhost:8000/api/v1/module-access/matrix" | jq '.items'
# Expected: 1 row (Buc-ee's), cells reflect that tenant's actual tenant_module_access state.

# /matrix filter tier=ENTERPRISE
curl -s -H "Authorization: Bearer $PJWT" "http://localhost:8000/api/v1/module-access/matrix?tier=ENTERPRISE" | jq '.items | map(.name)'
# Expected: Only Enterprise tenants (Buc-ee's, Żabka, Infomil per seed).

# /matrix invalid sort
curl -s -H "Authorization: Bearer $PJWT" "http://localhost:8000/api/v1/module-access/matrix?sort=garbage_desc"
# Expected: 400 INVALID_SORT_KEY.

# /matrix free-text search
curl -s -H "Authorization: Bearer $PJWT" "http://localhost:8000/api/v1/module-access/matrix?q=buc" | jq '.items | map(.name)'
# Expected: ["Buc-ee's"] (case-insensitive ILIKE match).

# Position alignment: same module_code at the same index across endpoints
curl -s -H "Authorization: Bearer $PJWT" "http://localhost:8000/api/v1/module-access/modules" | jq '[.items[].module_code]'
curl -s -H "Authorization: Bearer $PJWT" "http://localhost:8000/api/v1/module-access/matrix" | jq '[.items[0].cells[].module_code]'
# Expected: identical arrays.
```

If any leg is not green, **report the failure rather than the step.**

---

## Scope out

- **Write surface (toggle endpoint).** POST/PATCH to enable/disable a module for a tenant, with the cascade ("Disabling instantly revokes all related role permissions"). Captured as MODULE-ACCESS-WRITE forward note. Future step.
- **Multi-tier or multi-status filtering.** v0 ships single-value filters only.
- **Filtering by module status.** "Show me tenants with Promotions Assistant disabled" — frontend-side concern at v0 fleet scale.
- **Time-series for module cards.** "Enabled in 4/7, was 3/7 last month" — future feature.
- **Industry filter on /matrix.** Captured as TIER-INDUSTRY-FILTER-EXTENSION forward note.
- **Per-role permission cascade preview.** When toggling DISABLED, frontend wants to show "this will revoke N permissions across M roles." Backend would need a separate endpoint. Future.
- **Retroactive label resolution on older endpoints.** New convention applies forward only.
- **Tenant-side dedicated dashboard.** Tenants see module-access via the same endpoints, RLS-scoped. Frontend hides degenerate views. No separate tenant-shaped endpoint.

---

## Stop and ask if

1. **`tenants.tier` enum values don't match `{ENTERPRISE, MID_MARKET, SMB, SINGLE_STORE}`.** Verify against `models/tenant.py`'s `TenantTier` enum. If different (e.g., `SMB` is actually `SMALL_BUSINESS`), update the filter param's vocabulary and any hardcoded tier strings in tests.

2. **`tenants.status` enum values don't match `{ONBOARDING, TRIAL, ACTIVE, SUSPENDED, TERMINATED}`.** Verify against `TenantStatus` enum. If `ONBOARDING` doesn't exist (Step 6.5's S4 amendment surfaced it as the fifth value), check whether the schema has been updated.

3. **`lookups` rows for `tenant_tier` and `tenant_status` exist with unexpected values.** Per the locked deliverable, this step ships a small additive migration that seeds the lookup rows for these two enum lists. Pre-flight verifies the live state with:
   ```bash
   psql "$PSQL_URL" -c "SELECT list_name, COUNT(*) FROM core.lookups WHERE list_name IN ('tenant_tier', 'tenant_status') GROUP BY list_name;"
   ```
   - Expected: zero rows (neither list_name has been seeded). The migration adds them.
   - If rows exist already (someone seeded them out-of-band): surface — the migration's `ON CONFLICT DO NOTHING` will skip them but you'd want to confirm the existing rows match the locked vocabulary before relying on them.

4. **`lookups.list_name` for tenant_status is something other than `'tenant_status'`.** Verify by looking at any existing seeded rows or by reading whatever's there. If the convention is `'tenant_status_enum'` or `'status'`, use the actual name.

5. **`tenant_module_access.module` is not the column name (it might be `module_code`).** Verify in pre-flight item 6. If different, adjust SQL queries.

6. **Pagination filter interaction.** If the SQL has a subtle bug where the `total` count doesn't match the items returned (e.g., one applies tier filter, the other doesn't), the page navigates incorrectly. X9 should catch this; if it doesn't, surface.

7. **Cloud SQL dev** schema state diverges from local — particularly around lookups rows. Pre-deploy verification queries should run against Cloud SQL too.

8. **`make_tenant_module_access` factory** doesn't take the `module` arg as `ModuleCode` enum (it might still take string after the Step 6.6 unification). Verify; if it needs updating, that's a small conftest edit. Default: assume it works post-6.6.

9. **UNIQUE constraint on `tenant_module_access(tenant_id, module)` is missing.** The /matrix LEFT JOIN assumes at most one row per (tenant, module) pair. Per pre-flight item 17b, run the pg_constraint query. If the constraint is absent, the LEFT JOIN can produce duplicate cells, breaking position alignment. Surface — fix is either (a) add a UNIQUE constraint via migration (not in this step's locked scope), or (b) adjust SQL to use `DISTINCT ON (pt.id, mo.module_code)` to deduplicate.

---

## Acceptance criteria

- 10 files created/modified per scope above (plus prompt + CLAUDE.md + BUILD_PLAN.md = 13).
- 1 new Repo (`ModulesAccessRepo`) with 2 methods.
- 2 endpoints live and routed under `/api/v1/module-access/`.
- For seed-loaded data:
  - `/modules` (PLATFORM): 6 cards in locked ordering, each with module_label populated, enabled_count and total_active_trial_tenants reflecting ACTIVE+TRIAL only.
  - `/modules` (TENANT-Buc-ee's): same shape, all values RLS-scoped to that tenant.
  - `/matrix` (PLATFORM): all non-terminated tenants, each with 6 cells in locked ordering, tier_label and status_label populated.
  - `/matrix` (TENANT-Buc-ee's): exactly 1 row.
- 5 load-bearing tests (M2, M3, M4, X1, X2) explicitly green.
- All ~14 new integration tests pass.
- Per-resource regression checkpoint: every prior router file at exactly its pre-step PASS count. **A drop is a step-blocker — particularly dashboard's O2 (modules_deployed RLS) which is the canary for tenant_module_access RLS regression.**
- mypy strict clean.
- check_setup 35/35.
- pytest smoke (`scripts/smoke_test.py`) unchanged at 74 PASS.
- `scripts/smoke_curl.sh` updated: 2 new assertions. Expected PASS count grows by +2 (was 18, now 20).
- The other three workflow scripts unchanged.
- Alembic head advances by one — Step 6.7's lookups-seed migration on top of `cec8fae734e0` (Step 6.6's unification).
- `docs/endpoints/module-access.md` covers both endpoints in 8-section format.
- OpenAPI spec quality: both endpoints with summary, description, all enum fields with vocabulary, all response fields with descriptions.
- New label-handling convention codified in CLAUDE.md.

---

## Report (BEFORE proposing commit)

Six bundles per the convention:

1. **Code:** files created/modified with line counts; manual curl outputs verifying both endpoints under PLATFORM and TENANT JWTs; backwards-compat check on `/dashboard/governance-stats` (modules_deployed) and `/permissions` (post-6.6 unification preserves wire format). **Workflow scripts:** `scripts/smoke_curl.sh` delta (+2 assertions); explicit "no change" confirmation for the other three.
2. **CLAUDE.md updates:** Step 6.7 Completed bullet; new "Note on label resolution" convention note; no new D-XX or FN-AB.
3. **BUILD_PLAN.md updates:** Step 6.7 entry with explicit Coordination paragraph flagging the `--migrate` requirement on next deploy (Step 6.6 migration must run).
4. **architecture.md updates:** "no change."
5. **OpenAPI spec snapshot:** `docs/endpoints/openapi.json` regenerated; verify both new paths present with schema descriptions.
6. **Prompt file:** `prompts/step-6_7-module-access-read-endpoints-2026-05-06.md` confirmed in commit set.

Plus: pytest count delta (+~14); per-file regression numbers confirming each at 100% PASS with no count drop (especially dashboard); mypy status; check_setup; alembic head advanced by one (Step 6.7's lookups-seed migration); position-alignment manual verification.

Wait for explicit authorisation before staging or committing.

---

## After completing

Propose a git commit per CLAUDE.md "After completing a task" Pattern A:

```
git status
git add -A
git commit -m "Step 6.7: Module Access read endpoints (modules + matrix)

- 2 endpoints under /api/v1/module-access/:
  - GET /modules (6 module cards with per-module aggregates)
  - GET /matrix  (tenant × module grid, paginated/sorted/filtered)
- Multi-user-type, RLS-driven persona projection (continues Step 6.5
  pattern). PLATFORM sees fleet-wide; TENANT sees own-tenant only;
  same response shape, RLS does the projection.
- Aggregates use ACTIVE + TRIAL denominator. Different from dashboard's
  != TERMINATED — different product question, different count;
  documented inline.
- /matrix row set: all non-terminated tenants (ACTIVE + TRIAL +
  SUSPENDED + ONBOARDING). TERMINATED excluded. Each row carries
  tier/status with sibling _label fields.
- Cell synthesis: backend produces N × 6 grid via tenants × modules
  CROSS JOIN with LEFT JOIN to tenant_module_access. Absent rows AND
  rows with status=DISABLED both render as DISABLED in the response.
- Module ordering: ORDER BY lookups.display_order ASC, code ASC
  (mirrors Step 6.6 sort-stability decision). Position-aligned across
  /modules.items[i] and /matrix.items[*].cells[i] — frontend
  reconciles by index.
- NEW LABEL-HANDLING CONVENTION: server-side resolution via lookups
  JOIN with COALESCE fallback. Sibling-field shape (<field> +
  <field>_label). Codified in CLAUDE.md as the rule for new endpoints
  from this step forward; older endpoints stay bare-enum (Amit's
  frontend handles those via /lookups).
- ModulesAccessRepo at repositories/modules_access.py with two
  methods. Two-stage SQL for /matrix (tenant page + cells per page,
  joined in Python) — mirrors Step 6.1 permission-matrix pattern.
- ~14 integration tests; 5 load-bearing (M2 aggregate collapse, M3
  position alignment, M4 label resolution fallback, X1 TENANT 1-row,
  X2 cell synthesis under RLS).
- Reuses Step 5.2 InvalidSortKeyClientError; no new error classes.
- scripts/smoke_curl.sh: +2 assertions for the new endpoints; PASS
  count 18 → 20.
- docs/endpoints/module-access.md (8-section × 2 endpoints).
- One small additive Alembic migration: seeds tenant_tier and
  tenant_status lookup rows + (if needed) UPDATEs module_code rows'
  display_order to match the screenshot ordering. Idempotent
  (ON CONFLICT DO NOTHING). Forward-only; downgrade raises
  NotImplementedError per project convention.
- No DDL changes. No seed Excel changes.
- Builds on Step 6.6 (module enum unification): /modules and /matrix
  use module_code_enum directly via the unified vocabulary.
- DEPLOY MUST PASS --migrate. Next deploy bundles Step 6.6 + 6.7;
  Step 6.6's migration cec8fae734e0 must run on Cloud SQL or the live
  service code expects module_code_enum while Cloud SQL still has
  module_enum (cascading 500s on /permissions, /permission-matrix,
  and /dashboard/governance-stats)."
```

Ask user "Run? yes / no / edit message". On yes, execute via bash tool. On no, skip. On edit, prompt for new message.

---

## End of prompt
