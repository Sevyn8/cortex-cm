# Prompt — Step 6.5: Dashboard stats endpoints (Scale + Organization)

> Generated 2026-05-05. Resolved through frontend-locked design review:
> - Two new dedicated stats endpoints, separate from shipped resource APIs.
> - Stats-only payload — KPI scalars, no panel/feed rows. Frontend composes panels via existing/future list endpoints (Top Tenants → `/api/v1/tenants` with sort keys added at Step 6.4; Recent Activity → `/api/v1/audit-logs` Step 6.2).
> - Multi-user-type with RLS-driven persona projection (resolves FN-AB-21 by extension — confirms Option 2 as the platform-wide default for stats endpoints).
> - Card-shaped response objects with `available` + `unavailable_reason` stub pattern. Shape stable as values flip from stub to real over future steps.
> - Backend formats `sub_text` strings (scope-aware via `auth.user_type`).
> - Persona-grained, never role-grained — same endpoints serve Platform Admin and (future) Tenant Owner dashboards.
> - Step 6.4 (tenants list aggregate sort keys) is expected to have shipped before this step; verify in pre-flight.
>
> Paste this entire block into a fresh Claude Code session to start Step 6.5.

---

## Context: why this step exists and why now

The Frontend dashboard (Frontend spec 7.1) renders eight KPI cards plus two panels (Top Tenants, Recent Activity). Per the screenshots: `Active tenants 5/7`, `Platform users 8,438`, `Stores under mgmt 10,084`, `MRR $308.1k`, `Pending approvals 7`, `Guardrails fired (24h) 23`, `Custom roles 1`, `Modules deployed 6`.

The dashboard is a single visual surface but its data spans three categories:

1. **Resources already shipped** — tenants, tenant_users, stores, tenant_module_access (Steps 3.x / 5.x), roles/RBAC read endpoints (Step 6.1).
2. **Resources on the build plan but not yet shipped** — audit_logs (Step 6.2).
3. **Resources not on the v0 build plan at all** — guardrails, approvals, the create-custom-role write surface.

Note on (1) vs (3) for `custom_roles`: Step 6.1 shipped *read* endpoints (the `roles` table is reachable, queryable, RLS-correct), but the *write* surface to create custom roles is not on the v0 plan. With no path to create them, `COUNT(*) FILTER (WHERE is_system = false)` is structurally zero. Flipping `available: true` while the count cannot meaningfully change misrepresents platform state to the dashboard reader, so the card stays stubbed with `unavailable_reason: "custom_role_creation_not_shipped"` until the write surface ships.

A frontend-locked design review (logged 2026-05-05) settled the shape questions:

- **Endpoint posture:** new dedicated `/dashboard/*` endpoints, separate from shipped resource APIs. Do NOT extend `/api/v1/tenants/stats`. The dashboard surface keeps evolving; isolating it from resource endpoints prevents shape drift on resource APIs.
- **Slicing:** two endpoints, by concern:
  - `GET /api/v1/dashboard/fleet-stats` — customer-base scale (KPIs 1–4)
  - `GET /api/v1/dashboard/governance-stats` — governance posture (KPIs 5–8)
- **Stats only:** these endpoints serve KPI scalars, not panel rows. Frontend makes 2–3 calls per page render: stats endpoints + `/tenants` (sort key `num_users_active_desc` added at Step 6.4) + future `/audit-logs`.
- **Full shape from day one:** all 8 KPIs ship in the response. Values that depend on unshipped tables come back with `available: false` and a machine-readable `unavailable_reason`. As tables ship, `available` flips to `true` without changing endpoint shape.
- **Multi-user-type via RLS:** both endpoints accept PLATFORM and TENANT JWTs. RLS scopes the values automatically. A future Tenant Owner dashboard reuses the same endpoints and gets tenant-scoped values; frontend hides cards that are degenerate at tenant scope (e.g., "Active tenants 1/1"). This resolves FN-AB-21 by extending the multi-user-type pattern to dashboard stats — Option 2 confirmed as the platform-wide default for stats endpoints.

---

## Pre-flight

1. Run `./scripts/check_setup.sh`. Expect 35/35.
2. `git log --oneline -5` — confirm Step 6.1 (RBAC read endpoints) is in history. Step 6.2 (audit-logs) has not shipped yet — that's expected and accounted for in the stub posture for `guardrails_fired_24h`.
3. `uv run alembic heads` — confirm head matches the most-recently-shipped step.
4. Read `CLAUDE.md` fully. Focus on:
   - **D-13** (audit-actor patterns — relevant for understanding why audit-actor columns are hidden across read endpoints).
   - **D-15** (DB_SCHEMA from environment).
   - **D-29** (PLATFORM RLS visibility via OR-clause). **Load-bearing for the fleet-stats CTE:** PLATFORM JWT sees fleet-wide aggregates because the OR-clause exposes all tenants/stores/users; TENANT JWT sees own-tenant only because RLS filters before aggregation.
   - **D-30** (list-only response envelope). **Both endpoints are deliberate D-30 exceptions** — card-shaped objects, not `{items, pagination}`. Document each exception in the OpenAPI summary.
   - **D-31** (response field semantics are append-only). Critical for stub-to-real transitions: `available: false` cards must keep the same field shape when their underlying tables ship.
   - **FN-AB-21** (`/api/v1/tenants/stats` endpoint posture). **This step closes FN-AB-21** by confirming Option 2 (multi-user-type, document scope-dependent semantics) as the platform-wide default. **No code changes to the existing `/tenants/stats` endpoint** — the resolution is documentation-only.
   - **Audience filtering for non-RLS tables** convention (Step 6.1). Does NOT apply here — all dashboard data lives in RLS-protected tables; RLS handles persona projection.
5. Read `docs/architecture.md` "Multi-tenancy and data isolation" — confirms RLS does persona projection on aggregates.
6. Read `src/admin_backend/routers/v1/tenants.py` — the existing `tenants_stats` handler is the closest precedent. **Do NOT modify it.** It stays at its Step 3.3 contract for backwards compatibility.
7. Read `src/admin_backend/repositories/tenants.py` — specifically `count_for_stats`. The new fleet-stats query is structurally similar but produces more aggregates; mirror the session/return-tuple shape.
8. Read `src/admin_backend/models/tenant.py`, `models/tenant_user.py`, `models/_lightweight_stubs.py`, `models/tenant_module_access.py`. Verify enum values used in queries:
   - `tenants.status` values — likely `'ACTIVE'`, `'TRIAL'`, `'SUSPENDED'`, `'TERMINATED'`. Confirm from `TenantStatus` enum.
   - `tenant_users.status` value for "active" — likely `'ACTIVE'`.
   - `tenant_module_access.status` value for "currently enabled" — likely `'ENABLED'`. **If different, use the actual value** and adjust the SQL accordingly (Stop-and-ask trigger #4).
   - `stores.country` column — confirm exists on either the lightweight stub or the live DDL.
9. Read `src/admin_backend/routers/v1/lookups.py` and `routers/v1/tenant_users.py` — closest router precedents for the response-model wiring pattern, OpenAPI summary/description style, and `Depends(get_auth_context)` + `Depends(get_tenant_session_dep)` shape.
10. Read `src/admin_backend/errors.py` — confirm `ClientError` shape. **No new error classes needed.** Both endpoints return only 401 (auth) and 500 (internal).
11. Read `tests/integration/test_tenants_router.py` — fixture machinery (`client`, `_platform_jwt`, `_tenant_jwt`, `make_*` factories). Reuse all of this.
12. Read `tests/integration/conftest.py` — confirm these factories exist (added by Steps 3.2 / 3.3 / 3.4.5 / 5.2): `make_tenant`, `make_store`, `make_tenant_user`, `make_tenant_module_access`. **No new factories expected.** If `make_tenant_module_access` is missing, see Stop-and-ask trigger #3.
13. Read `BUILD_PLAN.md` — find the slot for this step. Provisional: **Step 6.5** (after Step 6.4 tenants sort keys; original Step 6.3 was seeds, Step 6.4 is the tenants extension). If slotted differently, surface and use the slotted ID.
14. Read `docs/endpoints/tenants.md` — closest precedent for the 8-section endpoint doc. The new `docs/endpoints/dashboard.md` covers both endpoints in one file.
15. Read `data/ithina_dev_seed_data.xlsx` — confirm seed shape from Step 3.5: 7 tenants (5 ACTIVE, 1 TRIAL, 1 SUSPENDED matches the prototype `5/7`), 17 tenant_users, 25 stores, 27 tenant_module_access rows. **Do not modify seed data.**
16. Read `src/admin_backend/dependencies.py` (or wherever `get_auth_context` and `get_tenant_session_dep` live) — confirm import paths used by the new router.
17. Read this prompt fully.

---

## Step ID and intent

**Step 6.5** — Dashboard stats endpoints. Two endpoints backing the Platform Dashboard's KPI grid (Frontend spec 7.1).

**Endpoints in scope:**

| # | Method + path | Auth | Visibility |
|---|---|---|---|
| E1 | `GET /api/v1/dashboard/fleet-stats` | multi-user-type | PLATFORM sees fleet-wide aggregates; TENANT sees own-tenant aggregates via RLS |
| E2 | `GET /api/v1/dashboard/governance-stats` | multi-user-type | Same RLS-driven projection; mostly stubs in v0 |

**Forward notes (NOT in scope this step):** PENDING-APPROVALS-REAL, GUARDRAILS-FIRED-REAL, CUSTOM-ROLES-REAL, MRR-DELTA-REAL, TENANT-OWNER-DASHBOARD, ITHINA-COMMERCIAL-HEALTH. Captured in BUILD_PLAN's Step 6.5 "Known follow-ups (Dashboard)" sub-section. (TOP-TENANTS-PANEL-EXT — sort keys on `/tenants` — is shipped separately at Step 6.4.)

**Concrete deliverables:**

1. New schemas at `schemas/dashboard.py`: card-shaped response models per the locked contracts in §"Locked endpoint contracts" below.
2. New `DashboardRepo` at `repositories/dashboard.py` with two methods (`fleet_stats`, `governance_stats`).
3. New router at `routers/v1/dashboard.py` with both endpoints under `/dashboard` prefix.
4. Single CTE for fleet-stats (one Postgres round-trip — full SQL provided below in §"Locked SQL: fleet-stats CTE").
5. Per-card stub posture for governance-stats: 3 of 4 cards `available: false` with locked `unavailable_reason` codes; `modules_deployed` is real.
6. Backend-formatted, scope-aware `sub_text` (locked formatting rules in §"Locked sub_text rules").
7. Wire-up in `routers/v1/__init__.py`.
8. Integration tests at `tests/integration/test_dashboard_router.py` — ~16 tests, 5 load-bearing (listed in §"Tests").
9. `docs/endpoints/dashboard.md` — single doc, both endpoints in 8-section format.
10. **No migrations. No DDL changes. No seed Excel changes.**
11. CLAUDE.md update: close FN-AB-21 (Option 2 confirmation, doc-only); add Step 6.5 Completed bullet.
12. BUILD_PLAN.md update: Step 6.5 entry with "Known follow-ups (Dashboard)" sub-section.

CLAUDE_CODE step. No migrations, no schema changes — simplest endpoint step in the build so far. Expect ~2–3 hours.

---

## Locked endpoint contracts

### E1 — `GET /api/v1/dashboard/fleet-stats`

**Request:** no query params, no body. JWT in Authorization header.

**Response 200 (PLATFORM JWT)** — fleet-wide aggregates:

```jsonc
{
  "active_tenants": {
    "value": 5,
    "total": 7,
    "sub_text": "2 trial · 1 suspended",
    "delta": { "value": 1, "direction": "up", "window": "7d", "available": true },
    "available": true
  },
  "platform_users": {
    "value": 8438,
    "sub_text": "across all tenants",
    "delta": { "value": 184, "direction": "up", "window": "30d", "available": true },
    "available": true
  },
  "stores": {
    "value": 10084,
    "distinct_countries": 9,
    "sub_text": "9 countries",
    "delta": null,
    "available": true
  },
  "mrr_aggregated": {
    "value": "308100.00",
    "currency": "USD",
    "sub_text": "recurring",
    "delta": { "value": null, "direction": null, "window": "monthly", "available": false },
    "available": true
  }
}
```

**Response 200 (TENANT JWT, caller in Buc-ee's)** — same shape, RLS-scoped values; only sub_text strings differ where scope-aware:

```jsonc
{
  "active_tenants": { "value": 1, "total": 1, "sub_text": "", "delta": {...flat...}, "available": true },
  "platform_users": { "value": 6, "sub_text": "in your organization", "delta": {...}, "available": true },
  "stores":         { "value": 47, "distinct_countries": 1, "sub_text": "1 country", "delta": null, "available": true },
  "mrr_aggregated": { "value": "95000000.00", "currency": "USD", "sub_text": "recurring", "delta": {...stub...}, "available": true }
}
```

**Locked invariants for E1:**

| # | Invariant |
|---|---|
| S1 | Response shape identical for both user types. RLS scopes values; shape never forks. |
| S2 | `active_tenants.value` = count of `tenants` with `status = 'ACTIVE'`. Under TENANT JWT this is `0` or `1`. |
| S3 | `active_tenants.total` = count of `tenants` with `status != 'TERMINATED'` (= ACTIVE + TRIAL + SUSPENDED). |
| S4 | `active_tenants.sub_text` is backend-formatted per §"Locked sub_text rules". |
| S5 | `active_tenants.delta.value` = count of non-terminated tenants where `created_at >= now() - interval '7 days'`. **v0 semantic: "new entities created in the window," NOT net active-count change** (which would require snapshots). Document in field reference. |
| S6 | `platform_users.value` = count of `tenant_users` with `status = 'ACTIVE'`. RLS-scoped. **Field name retained even on TENANT side for consistency** — frontend can rename if needed; backend returns the same field. |
| S7 | `platform_users.sub_text` is scope-aware per §"Locked sub_text rules". |
| S8 | `platform_users.delta.value` = count of `tenant_users` where `status = 'ACTIVE'` AND `created_at >= now() - interval '30 days'`. |
| S9 | `stores.value` = count of stores. RLS-scoped. **No delta block** (`delta: null`) — spec shows no delta on this card. |
| S10 | `stores.distinct_countries` = `COUNT(DISTINCT country)` over visible store rowset. |
| S11 | `stores.sub_text` is backend-formatted (singular/plural). |
| S12 | `mrr_aggregated.value` = `SUM(monthly_revenue_usd)` over visible non-terminated tenants. **Decimal-as-string, always 2 dp.** Mirror the precedent from `tenants.md` (Q11 NUMERIC decision). |
| S13 | `mrr_aggregated.currency` = `"USD"` for v0. |
| S14 | `mrr_aggregated.delta.available` = **always `false` in v0.** No MRR snapshot table exists. `value`, `direction` are `null`; `window` is `"monthly"` (the intended cadence when this goes real). Tracked as MRR-DELTA-REAL forward note. |
| S15 | All four cards have `available: true`. The MRR *delta* is the only `available: false` field in this endpoint. |
| S16 | `direction` derivation: `value > 0` → `"up"`; `value < 0` → `"down"`; `value == 0` → `"flat"`. Backend-computed. |
| S17 | Single CTE query (one Postgres round-trip). SQL locked in §"Locked SQL: fleet-stats CTE". |

### E2 — `GET /api/v1/dashboard/governance-stats`

**Request:** no query params, no body.

**Response 200 (PLATFORM JWT):**

```jsonc
{
  "pending_approvals": {
    "value": 0, "sub_text": "across guardrails", "delta": null,
    "available": false, "unavailable_reason": "approvals_table_not_built"
  },
  "guardrails_fired_24h": {
    "value": 0, "escalations": 0, "sub_text": "0 escalations", "delta": null,
    "available": false, "unavailable_reason": "audit_logs_or_guardrails_not_wired"
  },
  "custom_roles": {
    "value": 0, "total": 0, "sub_text": "of 0 total", "delta": null,
    "available": false, "unavailable_reason": "custom_role_creation_not_shipped"
  },
  "modules_deployed": {
    "value": 32, "sub_text": "across 7 tenants", "delta": null,
    "available": true
  }
}
```

**Response 200 (TENANT JWT, caller in Buc-ee's with 6 modules enabled)** — same shape; sub_text strings scope-aware; `modules_deployed.value` RLS-scoped to own-tenant:

```jsonc
{
  "pending_approvals":   { ..., "sub_text": "across your organization", ... },
  "guardrails_fired_24h":{ ..., "sub_text": "0 escalations", ... },
  "custom_roles":        { ..., "sub_text": "of 0 total", ... },
  "modules_deployed":    { "value": 6, "sub_text": "enabled for your organization", ..., "available": true }
}
```

**Locked invariants for E2:**

| # | Invariant |
|---|---|
| O1 | Same response shape for both user types. |
| O2 | `pending_approvals` stubbed: `available: false`, `value: 0`, `delta: null`, `unavailable_reason: "approvals_table_not_built"`. `sub_text` scope-aware. |
| O3 | `guardrails_fired_24h` stubbed: `available: false`, `value: 0`, `escalations: 0`, `delta: null`, `unavailable_reason: "audit_logs_or_guardrails_not_wired"`. `sub_text: "0 escalations"` (literal — stub shape matches what real data will look like). |
| O4 | `custom_roles` stubbed in v0: `available: false`, `value: 0`, `total: 0`, `sub_text: "of 0 total"`, `unavailable_reason: "custom_role_creation_not_shipped"`. Step 6.1 shipped RBAC *read* endpoints (the `roles` table is reachable, queryable, RLS-correct). What's missing is the *write* surface — there's no path in v0 to actually create a custom role, so the count is structurally pinned at zero. Flipping `available: true` while the count cannot meaningfully change misrepresents the platform's state to the dashboard reader. Stays stubbed until the create-custom-role write surface ships. |
| O5 | `modules_deployed.value` = count of `tenant_module_access` rows where `status = 'ENABLED'` (or actual active-status value per pre-flight item 8). RLS-scoped. **Single arithmetic operation** — no scope-conditional logic. |
| O6 | `modules_deployed.sub_text` is scope-aware via `auth.user_type` per §"Locked sub_text rules". |
| O7 | `modules_deployed.delta` = `null` (no delta on this card). |
| O8 | All cards have `delta: null` in v0. None of the governance-stats cards carry deltas in the prototype. |
| O9 | **`unavailable_reason` is fixed vocabulary.** v0 codes: `"approvals_table_not_built"`, `"audit_logs_or_guardrails_not_wired"`, `"custom_role_creation_not_shipped"`. New codes added as forward notes resolve. Document the vocabulary in `docs/endpoints/dashboard.md`. |
| O10 | When `available: false`, callers MUST NOT read `value` as meaningful — it's a type-stable sentinel. Document in field reference. |

---

## Locked SQL: fleet-stats CTE

One round-trip producing all four card values + delta windows. RLS does persona projection — same SQL runs for both user types, visible row sets differ by session GUC.

```sql
WITH
tenant_counts AS (
    SELECT
        COUNT(*) FILTER (WHERE status = 'ACTIVE')                 AS active,
        COUNT(*) FILTER (WHERE status = 'TRIAL')                  AS trial,
        COUNT(*) FILTER (WHERE status = 'SUSPENDED')              AS suspended,
        COUNT(*) FILTER (WHERE status != 'TERMINATED')            AS total,
        COUNT(*) FILTER (
            WHERE status != 'TERMINATED'
              AND created_at >= NOW() - INTERVAL '7 days'
        )                                                         AS new_7d,
        COALESCE(
            SUM(monthly_revenue_usd) FILTER (WHERE status != 'TERMINATED'),
            0
        )                                                         AS mrr_sum
    FROM tenants
),
user_counts AS (
    SELECT
        COUNT(*) FILTER (WHERE status = 'ACTIVE') AS active,
        COUNT(*) FILTER (
            WHERE status = 'ACTIVE'
              AND created_at >= NOW() - INTERVAL '30 days'
        ) AS new_30d
    FROM tenant_users
),
store_counts AS (
    SELECT
        COUNT(*)                AS total,
        COUNT(DISTINCT country) AS countries
    FROM stores
)
SELECT
    tc.active     AS tenants_active,
    tc.trial      AS tenants_trial,
    tc.suspended  AS tenants_suspended,
    tc.total      AS tenants_total,
    tc.new_7d     AS tenants_new_7d,
    tc.mrr_sum    AS mrr_sum,
    uc.active     AS users_active,
    uc.new_30d    AS users_new_30d,
    sc.total      AS stores_total,
    sc.countries  AS stores_distinct_countries
FROM tenant_counts tc, user_counts uc, store_counts sc;
```

**Notes:**

- `'TERMINATED'`, `'ACTIVE'`, `'TRIAL'`, `'SUSPENDED'` must match the actual enum values. Verify from `TenantStatus` and `TenantUserStatus` enums in pre-flight.
- `monthly_revenue_usd` is `NUMERIC(15,2)`. The `COALESCE(..., 0)` handles the empty-table case.
- `country` column source: confirm during pre-flight whether it's on `stores` or `tenants`. The dashboard prototype's "9 countries" implies stores-level, but if the column doesn't exist on stores, fall back to `tenants.country`.

## Locked SQL: governance-stats query

Only `modules_deployed` is real in v0. Single query:

```sql
SELECT
    COUNT(*)                  AS modules_enabled,
    COUNT(DISTINCT tenant_id) AS visible_tenant_count
FROM tenant_module_access
WHERE status = 'ENABLED';
```

Where `'ENABLED'` is the actual active-status enum value (verify in pre-flight).

The other three cards (`pending_approvals`, `guardrails_fired_24h`, `custom_roles`) are returned as constants by the router — no Repo work for them.

---

## Locked sub_text rules

Backend formats these strings. No frontend reassembly. Implement as small helpers in the router (or `_helpers.py` module — Claude Code's choice).

| Card | PLATFORM | TENANT |
|---|---|---|
| `active_tenants.sub_text` | `"<n> trial · <m> suspended"` when both > 0; `"<n> trial"` when only trial > 0; `"<m> suspended"` when only suspended > 0; **empty string `""`** when both zero | Same rule, but values are typically 0 (caller is the only tenant) → empty string |
| `platform_users.sub_text` | `"across all tenants"` | `"in your organization"` |
| `stores.sub_text` | `"<N> country"` when N=1; `"<N> countries"` otherwise | Same rule |
| `mrr_aggregated.sub_text` | `"recurring"` | `"recurring"` |
| `pending_approvals.sub_text` | `"across guardrails"` | `"across your organization"` |
| `guardrails_fired_24h.sub_text` | `"0 escalations"` (stub) | `"0 escalations"` (stub) — when real: `"<N> escalations"` |
| `custom_roles.sub_text` | `"of <total> total"` | `"of <total> total"` |
| `modules_deployed.sub_text` | `"across <N> tenant"` when N=1; `"across <N> tenants"` otherwise | `"enabled for your organization"` |

`auth.user_type` is the dispatch key. Values: `"PLATFORM"` or `"TENANT"`.

---

## Files to create/modify

Claude Code investigates the existing codebase and writes the actual code. The contracts above are locked; the implementation pattern follows existing precedents.

### Schemas — `src/admin_backend/schemas/dashboard.py` (new)

Pydantic v2 models, `model_config = ConfigDict(extra="forbid")` on every model. Mirror the conventions in `schemas/tenant.py` and `schemas/permission.py`.

Required types:

- `DeltaBlock` — fields: `value: int | None`, `direction: Literal["up","down","flat"] | None`, `window: Literal["7d","30d","24h","monthly"] | None`, `available: bool`.
- One card model per E1 card: `ActiveTenantsCard` (adds `total`), `PlatformUsersCard`, `StoresCard` (adds `distinct_countries`), `MrrAggregatedCard` (`value: str` not int, plus `currency: str`).
- `FleetStatsResponse` — composes the four E1 cards.
- One card model per E2 card: `PendingApprovalsCard`, `GuardrailsFired24hCard` (adds `escalations: int`), `CustomRolesCard` (adds `total: int`), `ModulesDeployedCard`. The first three carry an `unavailable_reason: str | None = None` field.
- `GovernanceStatsResponse` — composes the four E2 cards.

Common shape across cards: `value`, `sub_text`, `delta`, `available`. Some cards add 1–2 fields beyond this. Every field gets a Pydantic `Field(description=...)` matching the invariants above; descriptions surface in the OpenAPI spec.

Re-export the new symbols from `schemas/__init__.py`.

### Repository — `src/admin_backend/repositories/dashboard.py` (new)

Stateless singleton class `DashboardRepo` (mirror `TenantsRepo`, `LookupsRepo`).

Two methods:

- `async def fleet_stats(self, session: AsyncSession) -> FleetStatsRow` — runs the locked CTE via `text()` SQL, returns a frozen dataclass with all 10 aggregate fields (tenants_active/trial/suspended/total/new_7d, mrr_sum as `Decimal`, users_active/new_30d, stores_total/distinct_countries).
- `async def governance_stats(self, session: AsyncSession) -> GovernanceStatsRow` — runs the locked single query, returns a frozen dataclass with `modules_enabled` and `modules_visible_tenant_count`. Reserves space (in comments) for the 3 stub-card aggregates that will land later.

The dataclasses can live in the same file as the Repo (mirror existing precedents).

**Note on the convention departure:** the dashboard isn't a CRUD resource; it's a UI-shaped query bundle. Document this in the Repo's module docstring. The cohesion of "all dashboard queries in one place" outweighs the consistency cost of departing from the resource-Repo pattern.

**Decimal handling:** `monthly_revenue_usd` is `NUMERIC(15,2)`. With `text()` SQL, the driver may return `Decimal`, `float`, or `str`. Use a defensive `Decimal(...)` cast on the way out. Format as `f"{x:.2f}"` in the router (always 2 dp).

### Router — `src/admin_backend/routers/v1/dashboard.py` (new)

`APIRouter(prefix="/dashboard", tags=["dashboard"])`. Two endpoints:

- `GET /fleet-stats` → `response_model=FleetStatsResponse`. Calls `DashboardRepo().fleet_stats(session)`, formats sub_text strings via helpers, builds the response.
- `GET /governance-stats` → `response_model=GovernanceStatsResponse`. Calls `DashboardRepo().governance_stats(session)`, returns the 3 stubbed cards as constants and the modules_deployed card from real data.

Both endpoints depend on `Depends(get_auth_context)` and `Depends(get_tenant_session_dep)`. No `_require_platform_auth(...)` gate — both user types accepted, RLS does the work.

`description` text on each endpoint should call out the D-30 exception (card-shaped, not list-envelope) so the OpenAPI spec is self-documenting.

Sub_text helper functions: small, pure, scope-aware. Either inline at the top of the file or in a private `_helpers.py` — implementer's choice.

Wire the router in `routers/v1/__init__.py` mirroring existing `include_router` calls.

### Tests — `tests/integration/test_dashboard_router.py` (new)

~16 tests. Reuse fixture machinery from `test_tenants_router.py`. **No new conftest factories.**

Test ID convention:

- `S*` — E1 fleet-stats (8 tests)
- `O*` — E2 governance-stats (6 tests)
- `A*` — auth (1 test, both endpoints)
- `X*` — cross-cutting (1 test)

**Five LOAD-BEARING tests:**

| ID | Verifies |
|---|---|
| **S2** | TENANT JWT fleet-stats: RLS scopes counts to own tenant. Insert tenants A and B, query as tenant-A user, assert `active_tenants.value == 1`, `total == 1`, `platform_users.value` reflects only tenant A's users. |
| **S5** | fleet-stats sub_text scope-awareness. Same data, two requests (PLATFORM and TENANT), assert sub_text differs per the locked rules. |
| **S7** | MRR delta is permanently stubbed (`available: false`). Contract guard against accidentally flipping to true without the snapshot table existing. |
| **O2** | governance-stats: modules_deployed is real and RLS-scoped while the other 3 cards are stubbed. Insert `tenant_module_access` rows in two tenants, assert PLATFORM sees both, TENANT sees own only. |
| **O5** | modules_deployed sub_text scope-awareness. PLATFORM `"across N tenants"` vs TENANT `"enabled for your organization"`. |

**Other tests:**

- S1 envelope shape (PLATFORM): all 4 cards present with expected fields and `available` flags.
- S3 active_tenants sub_text formatting: insert tenants with mixed statuses, assert all four sub_text branches (both > 0, only trial, only suspended, both zero).
- S4 active_tenants delta 7d window: insert a tenant created today and one created 8 days ago, assert delta.value reflects only the recent one.
- S6 stores distinct_countries: insert stores in 3 distinct countries; assert count and singular/plural sub_text.
- S8 mrr_aggregated.value is a JSON string with 2 decimal places, not a number.
- O1 envelope shape (PLATFORM): 3 cards `available: false`, modules_deployed `available: true`.
- O3 unavailable_reason exact strings on the 3 stubbed cards.
- O4 pending_approvals sub_text scope-awareness.
- O6 modules_deployed singular/plural: PLATFORM with 1 visible tenant gets `"across 1 tenant"` (no 's').
- A1 no JWT returns 401 on both endpoints.
- X1 Pydantic `extra='forbid'` guards against drift — POST mock asserting unknown field is rejected (or comparable test depending on existing fixtures).

### Documentation — `docs/endpoints/dashboard.md` (new)

8-section format × 2 endpoints in one file. Mirror `docs/endpoints/tenants.md` for shape.

Section 5 (behaviour notes) covers:

- The `available` + `unavailable_reason` pattern.
- The full v0 `unavailable_reason` vocabulary (3 codes; new codes land as forward notes resolve).
- Scope-aware sub_text rules table.
- Why the response shape is card-shaped (deliberate D-30 exception).
- The persona-projection pattern (TENANT JWT collapses cards via RLS; frontend hides degenerate cards).
- The MRR-DELTA-REAL forward note explaining why the delta is stubbed in v0.
- A "shape stability guarantee" paragraph: when a stub flips to real, only `available`, `value`, and `unavailable_reason` change. Field set and types stay the same. Append-only per D-31.

Section 7 (TypeScript snippet) shows the render pattern for both real and stubbed cards — frontend gates render on `available` and shows a "coming soon" treatment when false.

### CLAUDE.md — modify

- **Current state → Completed:** Step 6.5 bullet covering the 2 endpoints, DashboardRepo, card-shaped schemas, stub posture, 5 load-bearing tests, doc.
- **Resolve FN-AB-21:** Mark RESOLVED at Step 6.5. Add a paragraph: Option 2 (multi-user-type, document scope-dependent semantics) confirmed as the platform-wide default for stats endpoints. The existing `/api/v1/tenants/stats` is unchanged; resolution is documentation policy only. Both new dashboard endpoints follow the same multi-user-type + RLS-driven scoping pattern.
- **No new D-XX entries.** Card-shape exception is already covered by D-30's "deliberate exception" language.
- **No new FN-AB entries.** Dashboard-specific deferrals live in BUILD_PLAN's Step 6.5 sub-section.
- **Schema state line:** unchanged at 12 application tables. Smoke count unchanged at 74.

### BUILD_PLAN.md — modify

Add Step 6.5 entry. Status: TODO → DONE in same commit. Standard scope-in / scope-out / acceptance criteria / coordination structure mirroring Steps 5.1 / 5.2 / 6.1.

**"Known follow-ups (Dashboard)" sub-section** — capture these 6 forward notes with landing triggers:

1. **PENDING-APPROVALS-REAL** — flip `pending_approvals.available` to true. Lands when `approvals` table ships. No current build-plan step.
2. **GUARDRAILS-FIRED-REAL** — flip `guardrails_fired_24h.available` to true. Lands when audit_logs ships at Step 6.2 AND guardrail-fire events are emitted into audit_logs.
3. **CUSTOM-ROLES-REAL** — flip `custom_roles.available` to true. Lands when the create-custom-role write surface ships (no current build-plan step). Step 6.1 already shipped read endpoints, so the `roles` table is reachable; the gap is that no v0 path lets users actually create custom roles, so the count is structurally zero today. When the write surface lands, replace the stub with a `COUNT(*) FILTER (WHERE is_system=false) / COUNT(*)` pair against `roles`.
4. **MRR-DELTA-REAL** — flip `mrr_aggregated.delta.available` to true. Requires a per-period MRR snapshot table (no current plan). Tracked here so the contract is explicit.
5. **TENANT-OWNER-DASHBOARD** — when a Tenant Owner dashboard ships, it reuses these endpoints unchanged. RLS handles persona projection. Frontend hides degenerate cards. No backend work.
6. **ITHINA-COMMERCIAL-HEALTH** — future third-concern endpoint (`/dashboard/billing-stats`) covering Ithina's own MRR/ARR/churn/billing. Deliberately deferred.

### `prompts/step-6_5-dashboard-stats-endpoints-2026-05-05.md` — new

This prompt file. Bundled per the per-step convention.

### `docs/endpoints/openapi.json` — re-export

After all code is in and tests pass:

```bash
curl -s http://localhost:8000/api/v1/openapi.json | jq '.' > docs/endpoints/openapi.json
cat docs/endpoints/openapi.json | jq '.paths | keys' | grep dashboard
# Expected: /api/v1/dashboard/fleet-stats, /api/v1/dashboard/governance-stats
```

### `docs/architecture.md` — likely no edit

If the doc names Repos by example, add `DashboardRepo` and note the deliberate departure from one-Repo-per-resource. Otherwise skip.

### Scripts maintenance — modify `scripts/smoke_curl.sh` only

Four build-workflow scripts exist (`scripts/deploy-cloud-run.sh`, `scripts/env.sh`, `scripts/jwt/generate_7d.sh`, `scripts/smoke_curl.sh`). For each, decide explicitly whether it changes:

- `scripts/deploy-cloud-run.sh` — **no change.** Step 6.5 doesn't alter build inputs.
- `scripts/env.sh` — **no change.** No new env vars introduced. (Image tag bump for the deploy that ships these endpoints is a deploy-time decision, not part of this step's code.)
- `scripts/jwt/generate_7d.sh` — **no change.** JWT shape is unchanged.
- `scripts/smoke_curl.sh` — **modify.** Add one assertion per new public endpoint. For Step 6.5: 2 assertions (or 4 if covering both PLATFORM and TENANT JWTs):

  - `GET /api/v1/dashboard/fleet-stats` with PLATFORM JWT → 200 + envelope contains `active_tenants`, `platform_users`, `stores`, `mrr_aggregated`.
  - `GET /api/v1/dashboard/governance-stats` with PLATFORM JWT → 200 + envelope contains `pending_approvals`, `guardrails_fired_24h`, `custom_roles`, `modules_deployed`.
  - Optional: same two endpoints with TENANT JWT, asserting RLS-projected values (e.g., `active_tenants.value <= 1`).

  Update the expected PASS count comment at the top of the file to reflect the new total. Verify by running `bash scripts/smoke_curl.sh` against the deployed image (post-deploy, in the operator-driven phase).

---

## Testing and regression discipline

### New tests

~16 integration tests; 5 load-bearing (S2, S5, S7, O2, O5 — listed above).

### Tests deliberately not added

- "Single CTE produces correct values" — each card's individual aggregate is verified by S1/S3/S4/S6/S8; the CTE-as-mechanism is a query optimization, not a contract.
- "Pagination on dashboard endpoints" — not paginated.
- "401 on each endpoint individually" — covered by A1 across both paths.

### Regression risk surface

1. **Backwards compat with `/api/v1/tenants/stats`.** This step does NOT modify the existing endpoint. Verify by re-running `tests/integration/test_tenants_router.py` (must report exact pre-step PASS count).
2. **`tenant_module_access.status` filter value.** Verify against the actual enum (likely `'ENABLED'`).
3. **MRR Decimal precision.** Defensive `Decimal(...)` cast plus `f"{x:.2f}"` formatting. S8 verifies the JSON output is a string with 2 dp.
4. **Pydantic `extra='forbid'`.** X1 catches drift.
5. **`auth.user_type` value dispatch.** Code dispatches on `== "TENANT"`; if a future user_type lands, the dispatch defaults to PLATFORM-shaped sub_text. Acceptable for v0.
6. **Empty database edge case.** `SUM(monthly_revenue_usd)` returns NULL on empty tables (handled by COALESCE). Counts return 0. Endpoint returns sensible zeros, not 500s.

### Verification harness (run all seven; all must be green)

```bash
# 1. Full pytest
uv run pytest -v

# 2. Per-resource regression checkpoint (LOAD-BEARING)
uv run pytest tests/integration/test_tenants_router.py -v
uv run pytest tests/integration/test_platform_users_router.py -v
uv run pytest tests/integration/test_tenant_users_router.py -v
uv run pytest tests/integration/test_lookups_router.py -v
# Plus any later-step routers that have shipped (org-tree, rbac, audit-logs).
# Each file must report 100% PASS at exactly its pre-step count. A drop = step-blocker.

# 3. mypy strict
uv run mypy --strict src/admin_backend

# 4. Pre-flight checker
./scripts/check_setup.sh

# 5. Alembic head unchanged
uv run alembic heads
uv run alembic check

# 6. scripts/smoke_curl.sh — run against local dev (or post-deploy against Cloud Run)
bash scripts/smoke_curl.sh
# Expected: all PASS, count grows by +2 (or +4) for the new dashboard assertions.

# 7. Manual curl verification
PJWT=$(uv run python -c "from admin_backend.auth.testing import make_test_jwt; print(make_test_jwt(user_type='PLATFORM'))")
TJWT=$(uv run python -c "from admin_backend.auth.testing import make_test_jwt; print(make_test_jwt(user_type='TENANT', tenant_id='972a8469-1641-4f82-8b9d-2434e465e150'))")  # Buc-ee's

# E1 PLATFORM
curl -s -H "Authorization: Bearer $PJWT" /api/v1/dashboard/fleet-stats | jq '.'
# Expected (with seed): active_tenants 5/7, sub_text "1 trial · 1 suspended", platform_users.value matches seed,
#                       stores.value 25, distinct_countries N, mrr_aggregated.value as decimal string,
#                       mrr_aggregated.delta.available false

# E1 TENANT (Buc-ee's)
curl -s -H "Authorization: Bearer $TJWT" /api/v1/dashboard/fleet-stats | jq '.active_tenants.value, .stores.value, .platform_users.sub_text'
# Expected: 1, 3, "in your organization"

# E2 PLATFORM
curl -s -H "Authorization: Bearer $PJWT" /api/v1/dashboard/governance-stats | jq '.'
# Expected: 3 cards available:false with locked unavailable_reason codes; modules_deployed.value 27 (or seed count),
#           sub_text "across 7 tenants"

# E2 TENANT
curl -s -H "Authorization: Bearer $TJWT" /api/v1/dashboard/governance-stats | jq '.modules_deployed'
# Expected: value 6, sub_text "enabled for your organization", available true

# Backwards compat — /tenants/stats unchanged
curl -s -H "Authorization: Bearer $PJWT" /api/v1/tenants/stats | jq '.'
# Expected: {"total_tenants": 7, "total_stores": 25}
```

If any leg is not green, **report the failure rather than the step.** The per-resource regression checkpoint is especially load-bearing — `/tenants/stats` MUST still work.

---

## Scope out

- Modifications to `/api/v1/tenants/stats` — stays at Step 3.3 contract. FN-AB-21 resolved at the policy level.
- Top Tenants panel data extension on `/tenants` — sort keys `num_users_active_desc` / `num_stores_desc` shipped at Step 6.4 (precondition for this step).
- Recent Activity panel data — Step 6.2 territory.
- Pagination, filtering, sorting on dashboard endpoints — these are aggregates.
- Caching — at v0 scale, every CTE is sub-millisecond. At fleet-scale of 100+ tenants, consider a 60s cache. Captured for later.
- Configurable delta windows — hardcoded 7d / 30d / monthly in v0.
- Card-as-resource architecture — overengineered for ≤4 dashboard personas.
- Per-role dashboards — persona-grained, not role-grained. Never role-grained.
- Tenant Owner dashboard frontend — reuses these endpoints; tracked separately.
- Ithina commercial health endpoint — deliberately deferred.
- Real values for stubbed cards — each is its own forward note.

---

## Stop and ask if

1. **Surprising state on the `roles` table.** The current expectation: Step 6.1 has shipped, the `roles` table exists with system roles seeded, and `COUNT(*) FILTER (WHERE is_system = false)` returns `0` (no v0 path to create custom roles). If the table doesn't exist, OR if it has rows where `is_system = false` (someone created custom roles outside the v0 path), surface — the stub posture for `custom_roles` was designed assuming the count is structurally zero. We may need to flip the card to real or revise the `unavailable_reason` framing.

2. **Step 6.4 (tenants list aggregate sort keys) has NOT shipped before this step starts.** Verify by reading `repositories/tenants.py` for `TENANTS_SORT_MAP` and confirming `num_users_active_desc` is present. If absent, surface — frontend's Top Tenants panel won't render against `/tenants` until 6.4 lands; we'll either (a) bundle the 6.4 work into this step (one map entry, one test) or (b) ship Step 6.5 anyway and let the panel call return `400 INVALID_SORT_KEY` until 6.4 lands.

3. **Conftest factories don't exist.** If `make_tenant_module_access` is missing from `tests/integration/conftest.py`, surface — should have been added at Step 3.4.5. Either backfill (with a docstring note) or write inline factories in the new test file.

4. **`tenant_module_access.status` enum value for "active" is not `'ENABLED'`.** Use the actual value. Surface.

5. **`tenants.status` enum has values different from `ACTIVE / TRIAL / SUSPENDED / TERMINATED`.** Verify against `models/tenant.py`'s `TenantStatus`. If different, adjust the CTE FILTERs.

6. **`stores.country` column doesn't exist.** The lightweight stub may not declare it; the live DDL might or might not have it. If absent on stores entirely, surface — we'll either pivot to `tenants.country` or defer the country count.

7. **`tenant_users.status` enum value for "active" is not `'ACTIVE'`.** Verify against `models/tenant_user.py`'s `TenantUserStatus`.

8. **Decimal serialization differs from `tenants.md` precedent.** Verify by checking how `monthly_revenue_usd` is serialized on `/tenants` list response. Mirror exactly.

9. **Pydantic `extra='forbid'` breaks something.** If existing test machinery sends extra fields, surface; either add the config to other schemas or relax this step's schemas.

10. **Cloud SQL dev** has different state than local. Surface; HUMAN-coordinated cleanup before deploy.

---

## Acceptance criteria

- 14 files created/modified per scope above (13 in the original list + `scripts/smoke_curl.sh`).
- 1 new Repo (`DashboardRepo`) with 2 methods.
- 2 endpoints live and routed under `/api/v1/dashboard/`.
- For seed-loaded data:
  - `fleet-stats` (PLATFORM): all 4 cards with real values; MRR delta `available: false`.
  - `fleet-stats` (TENANT-Buc-ee's): values RLS-scoped; sub_text reflects scope.
  - `governance-stats` (PLATFORM): 3 stubbed cards with correct unavailable_reason; `modules_deployed` real.
  - `governance-stats` (TENANT-Buc-ee's): same 3 stubs; `modules_deployed` RLS-scoped to own.
- 5 load-bearing tests (S2, S5, S7, O2, O5) explicitly green.
- All ~16 new integration tests pass.
- Per-resource regression checkpoint: every prior router file at exactly its pre-step PASS count. **A drop is a step-blocker.**
- mypy strict clean.
- check_setup 35/35.
- pytest smoke (`scripts/smoke_test.py`) unchanged at 74 PASS — no new pytest smoke checks added in this step.
- `scripts/smoke_curl.sh` updated: 2 new assertions for `fleet-stats` and `governance-stats` (or 4 if both PLATFORM and TENANT JWT covered). Expected PASS count grows accordingly. `bash scripts/smoke_curl.sh` against the deployed image returns all PASS.
- The other three workflow scripts (`scripts/deploy-cloud-run.sh`, `scripts/env.sh`, `scripts/jwt/generate_7d.sh`) — unchanged, confirmed in the report.
- Alembic head unchanged. No migration. `alembic check` clean.
- `docs/endpoints/dashboard.md` covers both endpoints in 8-section format.
- OpenAPI spec quality: both endpoints with `summary`, `description` calling out the D-30 exception, response schemas with `description` on every field, error responses 401 referenced.
- FN-AB-21 marked RESOLVED in CLAUDE.md.

---

## Report (BEFORE proposing commit)

Six bundles per the convention:

1. **Code:** files created with line counts; manual curl outputs for both endpoints (PLATFORM and TENANT JWTs) with verified values; backwards-compat curl on `/api/v1/tenants/stats`. **Workflow scripts:** `scripts/smoke_curl.sh` delta (+2 or +4 assertions, new expected PASS count); explicit "no change" confirmation for `scripts/deploy-cloud-run.sh`, `scripts/env.sh`, `scripts/jwt/generate_7d.sh`.
2. **CLAUDE.md updates:** Step 6.5 Completed bullet; FN-AB-21 marked RESOLVED with the resolution paragraph; no new D-XX or FN-AB.
3. **BUILD_PLAN.md updates:** Step 6.5 entry with "Known follow-ups (Dashboard)" sub-section.
4. **architecture.md updates:** "no change" or specific edits if Repo names appear.
5. **OpenAPI spec snapshot:** `docs/endpoints/openapi.json` regenerated; verify both new paths present.
6. **Prompt file:** `prompts/step-6_5-dashboard-stats-endpoints-2026-05-05.md` confirmed in commit set.

Plus: pytest count delta; per-file regression numbers confirming each at 100% PASS with no count drop; mypy status; check_setup; alembic head unchanged.

Wait for explicit authorisation before staging or committing.

---

## After completing

Propose a git commit per CLAUDE.md "After completing a task" Pattern A:

```
git status
git add -A
git commit -m "Step 6.5: Dashboard stats endpoints (fleet-stats + governance-stats)

- 2 endpoints under /api/v1/dashboard/:
  - GET /fleet-stats        (cards 1-4: active tenants, platform users,
                             stores, aggregated tenant MRR)
  - GET /governance-stats (cards 5-8: pending approvals, guardrails fired,
                             custom roles, modules deployed)
- Multi-user-type, RLS-driven persona projection. Resolves FN-AB-21
  by confirming Option 2 as the platform-wide default for stats endpoints.
- Card-shaped response objects (D-30 exception, OpenAPI-documented).
  available + unavailable_reason pattern handles stub cards; shape stays
  stable as values flip from stub to real.
- Single CTE for fleet-stats: one Postgres round-trip for all 4 cards.
- Backend-formatted, scope-aware sub_text strings.
- 3 of 4 governance-stats cards stubbed in v0:
  - pending_approvals: 'approvals_table_not_built'
  - guardrails_fired_24h: 'audit_logs_or_guardrails_not_wired'
  - custom_roles: 'custom_role_creation_not_shipped'
  modules_deployed is real (queries tenant_module_access, RLS-scoped).
- DashboardRepo at repositories/dashboard.py — deliberate departure from
  one-Repo-per-resource (the dashboard IS a resource at the product level).
- 16 integration tests; 5 load-bearing (S2 TENANT scope; S5 sub_text
  scope-awareness; S7 MRR delta stub guard; O2 modules-real-others-stub;
  O5 modules sub_text scope-awareness).
- docs/endpoints/dashboard.md (8-section × 2 endpoints in one file).
- No migrations. No DDL changes. No seed Excel changes.
- Builds on Step 6.4 (tenants list aggregate sort keys), which the
  frontend's Top Tenants panel uses to query /tenants.
- BUILD_PLAN 6.5 includes 'Known follow-ups (Dashboard)' sub-section
  with 6 forward notes (PENDING-APPROVALS-REAL, GUARDRAILS-FIRED-REAL,
  CUSTOM-ROLES-REAL, MRR-DELTA-REAL, TENANT-OWNER-DASHBOARD,
  ITHINA-COMMERCIAL-HEALTH)."
```

Ask user "Run? yes / no / edit message". On yes, execute via bash tool. On no, skip. On edit, prompt for new message.

---

## End of prompt
