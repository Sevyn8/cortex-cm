# Dashboard stats endpoints

Canonical endpoint documentation for the Platform Dashboard's KPI grid (Frontend spec 7.1). Two GET endpoints under `/api/v1/dashboard/` — one for fleet-scale KPIs, one for governance posture. Format follows CLAUDE.md "Per-endpoint documentation" — eight fixed sections per endpoint. Resource-specific additions: **card-shaped response objects** (deliberate D-30 exception), **multi-user-type with RLS-driven persona projection**, **`available` + `unavailable_reason` stub pattern** for cards whose underlying tables haven't shipped.

| Endpoint | Description | Calling user types |
|---|---|---|
| `GET /api/v1/dashboard/fleet-stats` | KPI cards 1–4: customer-base scale (active tenants, platform users, stores, MRR) | PLATFORM (fleet-wide aggregates); TENANT (own-tenant aggregates via RLS) |
| `GET /api/v1/dashboard/governance-stats` | KPI cards 5–8: governance posture (pending approvals, guardrails fired, custom roles, modules deployed) | Both — same RLS-driven projection; 3 of 4 cards stubbed in v0 |

Cross-cutting:

- **Auth** — `Authorization: Bearer <jwt>` required; missing or invalid -> 401.
- **No PLATFORM-only gate.** Both endpoints accept both user types. Visibility is RLS-driven via the session GUCs set by `get_tenant_session`. Same SQL runs for both; visible row sets differ by `app.tenant_id` / `app.user_type`.
- **Card-shaped responses (deliberate D-30 exception).** The dashboard is a UI-shaped query bundle, not a paginatable collection. The `description` strings on each route call out the exception so OpenAPI consumers see it.
- **`available` + `unavailable_reason` pattern.** Every card carries an `available: bool` flag. When `false`, the card carries an `unavailable_reason: <code>` from the v0 vocabulary listed below. Type-stable sentinel values appear in `value` fields when `available: false`; consumers MUST NOT read them as meaningful.
- **Append-only contract per D-31.** When a stub card flips to real, only `available`, `value`, and `unavailable_reason` change. Field set and types stay the same. Frontend can render `available: false` cards with a "coming soon" treatment without coupling to the future-real shape.
- **Persona-grained, never role-grained.** Same endpoints serve both Platform Admin and (future) Tenant Owner dashboards. Frontend hides cards that are degenerate at tenant scope (e.g., `Active tenants 1/1`).
- **Backend-formatted, scope-aware `sub_text`.** Strings like "across all tenants" vs "in your organization" are computed in the router, dispatched on `auth.user_type`. No frontend reassembly.
- **Error envelope** — `{code, message, details, request_id}` on auth/server errors. `details` is `null` in v0.
- **`X-Request-Id`** — set on every response by the audit middleware; same UUID appears in the per-request log line.
- **No RBAC enforcement** beyond the binary user_type-based posture. Per-permission gating is post-v0.

### v0 `unavailable_reason` vocabulary

Fixed-string codes, machine-readable. New codes land as forward notes resolve.

| Code | Card | Resolves when |
|---|---|---|
| `approvals_table_not_built` | `pending_approvals` | Approvals table ships (no current build-plan step). PENDING-APPROVALS-REAL forward note. |
| `audit_logs_or_guardrails_not_wired` | `guardrails_fired_24h` | Audit logs (Step 6.2) ship AND guardrail-fire events are emitted. GUARDRAILS-FIRED-REAL forward note. |
| `custom_role_creation_not_shipped` | `custom_roles` | Create-custom-role write surface ships (no current build-plan step). Step 6.1 already shipped RBAC *read* endpoints; the gap is the *write* path. CUSTOM-ROLES-REAL forward note. |

---

## `GET /api/v1/dashboard/fleet-stats`  (E1)

Fleet-scale KPI cards (1–4).

### 1. Endpoint summary

- **Method:** `GET`
- **Path:** `/api/v1/dashboard/fleet-stats`
- **Description:** Returns cards 1–4 of the dashboard KPI grid: active tenants, platform users, stores under management, aggregated tenant MRR. Card-shaped response (deliberate D-30 exception). All four cards have `available: true` in v0; the only stub field is `mrr_aggregated.delta`.
- **Who can call:** any authenticated user. PLATFORM JWTs see fleet-wide aggregates; TENANT JWTs see own-tenant aggregates via RLS.

### 2. Request

**Headers:**

| Header | Required | Notes |
|---|---|---|
| `Authorization` | Yes | `Bearer <jwt>` (PLATFORM or TENANT) |
| `Accept` | No | Defaults to `application/json` |

**Path parameters:** none.
**Query parameters:** none.
**Request body:** none.

### 3. Response 200

```json
{
  "active_tenants": {
    "value": 5,
    "total": 7,
    "sub_text": "1 onboarding · 1 trial · 1 suspended",
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

**Field reference:**

| Card | Field | Type | Notes |
|---|---|---|---|
| `active_tenants` | `value` | int | `COUNT(*) FILTER (WHERE status = 'ACTIVE')`. RLS-scoped — TENANT JWT receives 0 or 1. |
| `active_tenants` | `total` | int | `COUNT(*) FILTER (WHERE status != 'TERMINATED')` — covers all four non-terminated states (ONBOARDING, TRIAL, ACTIVE, SUSPENDED). |
| `active_tenants` | `sub_text` | str | Lifecycle breakdown: each non-zero segment from {onboarding, trial, suspended} in lifecycle order, separated by ` · `. Empty string when all three are zero. |
| `active_tenants` | `delta` | `DeltaBlock` | 7d window: count of non-terminated tenants `created_at >= now() - interval '7 days'`. v0 semantic is "new entities created in the window," NOT "net active-count change" (which would require snapshots). |
| `platform_users` | `value` | int | `COUNT(*) FILTER (WHERE status = 'ACTIVE')` on `tenant_users`. RLS-scoped. |
| `platform_users` | `sub_text` | str | PLATFORM: `"across all tenants"`. TENANT: `"in your organization"`. |
| `platform_users` | `delta` | `DeltaBlock` | 30d window: ACTIVE tenant_users created in the last 30 days. |
| `stores` | `value` | int | `COUNT(*)` on `stores`. RLS-scoped. |
| `stores` | `distinct_countries` | int | `COUNT(DISTINCT country)` on visible stores. RLS-scoped (TENANT typically gets 1). |
| `stores` | `sub_text` | str | `"<N> country"` (singular when N=1) or `"<N> countries"`. Same rule for both user types. |
| `stores` | `delta` | `null` | No delta on this card. Field is reserved at type-level (always null) so the card stays in the common card family. |
| `mrr_aggregated` | `value` | str | `SUM(monthly_revenue_usd)` over visible non-terminated tenants. **Always 2 decimal places** (e.g., `"308100.00"`). Empty visible-tenant set returns `"0.00"`. |
| `mrr_aggregated` | `currency` | str | Always `"USD"` in v0. |
| `mrr_aggregated` | `sub_text` | str | Always `"recurring"`. |
| `mrr_aggregated` | `delta` | `DeltaBlock` | **Permanently stubbed in v0.** `available: false`, `value: null`, `direction: null`, `window: "monthly"`. The window is preserved as the intended cadence so the shape doesn't change when MRR-DELTA-REAL ships. |
| (every card) | `available` | bool | `true` for all four E1 cards in v0. |

**`DeltaBlock` shape:**

| Field | Type | Notes |
|---|---|---|
| `value` | `int \| null` | Magnitude of the change. `null` when the delta is stubbed. |
| `direction` | `"up" \| "down" \| "flat" \| null` | Derived from `value`: `up` if `value > 0`, `down` if `< 0`, `flat` if `== 0`. `null` when stubbed. |
| `window` | `"7d" \| "30d" \| "24h" \| "monthly" \| null` | Lookback window. Card-specific. May remain populated even when `available: false` (preserved for shape stability — see `mrr_aggregated.delta`). |
| `available` | bool | False when the underlying snapshot data isn't shipped. |

### 4. Response codes

| Code | When | Body |
|---|---|---|
| 200 | Success | Body as above |
| 401 | Missing or invalid JWT | `{code: "AUTH_MISSING" \| "AUTH_INVALID", message, details: null, request_id}` |
| 500 | Internal server error | `{code: "INTERNAL_ERROR", message: "An internal error occurred", details: null, request_id}` |

### 5. Behaviour notes

- **Single CTE per request.** All four cards' aggregates come from one Postgres round-trip via a CTE in `repositories/dashboard.py`. Each inner SELECT inherits RLS via the session GUCs; same SQL runs for both user types.
- **RLS-driven persona projection.** PLATFORM JWT sees fleet-wide aggregates because D-29's OR-branch exposes all rows; TENANT JWT sees own-tenant aggregates because the equality clause matches the session's `app.tenant_id`. Identical SQL, persona-correct results.
- **`active_tenants.sub_text` lifecycle ordering.** Always onboarding → trial → suspended. ONBOARDING is real (the lifecycle's first state) and is broken out as a distinct segment alongside TRIAL and SUSPENDED.
- **`active_tenants.total` includes ONBOARDING.** Per the `!= 'TERMINATED'` filter — all four non-terminated states count toward `total`. A platform with `5 ACTIVE / 1 TRIAL / 0 SUSPENDED / 1 ONBOARDING / 0 TERMINATED` returns `value: 5, total: 7, sub_text: "1 onboarding · 1 trial"` (no SUSPENDED segment when zero).
- **`mrr_aggregated.value` formatting.** Backend formats with `f"{x:.2f}"` regardless of underlying Decimal precision. Differs from `/api/v1/tenants` per-row `monthly_revenue_usd` (which uses `field_serializer` returning `str(v)` to preserve the per-row NUMERIC canonical string). The dashboard SUM can produce edge values like `Decimal('0E-2')`; the explicit format is the safer guarantee.
- **Empty database edge case.** All counts return 0; `mrr_aggregated.value` is `"0.00"`. No 500.

### 6. Example calls

```bash
# Fleet-stats as PLATFORM.
curl -s -H "Authorization: Bearer $JWT" \
  "https://admin-dev.ithina.com/api/v1/dashboard/fleet-stats"

# Fleet-stats as TENANT (own-tenant aggregates).
curl -s -H "Authorization: Bearer $TENANT_JWT" \
  "https://admin-dev.ithina.com/api/v1/dashboard/fleet-stats"

# Quick sanity-check the four cards' availability flags.
curl -s -H "Authorization: Bearer $JWT" \
  "https://admin-dev.ithina.com/api/v1/dashboard/fleet-stats" \
  | jq '{active_tenants: .active_tenants.available,
         platform_users: .platform_users.available,
         stores:         .stores.available,
         mrr_aggregated: .mrr_aggregated.available,
         mrr_delta:      .mrr_aggregated.delta.available}'
# Expected: all true except mrr_delta (false in v0).
```

### 7. Sample integration code

```typescript
type DeltaBlock = {
  value: number | null;
  direction: "up" | "down" | "flat" | null;
  window: "7d" | "30d" | "24h" | "monthly" | null;
  available: boolean;
};
type ActiveTenantsCard = {
  value: number; total: number; sub_text: string;
  delta: DeltaBlock; available: boolean;
};
type PlatformUsersCard = {
  value: number; sub_text: string;
  delta: DeltaBlock; available: boolean;
};
type StoresCard = {
  value: number; distinct_countries: number; sub_text: string;
  delta: null; available: boolean;
};
type MrrAggregatedCard = {
  value: string;            // "308100.00"
  currency: string;         // "USD"
  sub_text: string;         // "recurring"
  delta: DeltaBlock;        // available: false in v0
  available: boolean;
};
type FleetStatsResponse = {
  active_tenants: ActiveTenantsCard;
  platform_users: PlatformUsersCard;
  stores: StoresCard;
  mrr_aggregated: MrrAggregatedCard;
};

// Render pattern: gate on .available.
function renderCard(card: { available: boolean; value: any; sub_text: string }) {
  if (!card.available) return <ComingSoon />;
  return <Kpi value={card.value} sub={card.sub_text} />;
}
```

### 8. Implementation reference

| File | Role |
|---|---|
| `src/admin_backend/routers/v1/dashboard.py` | `get_fleet_stats` handler + sub_text helpers |
| `src/admin_backend/repositories/dashboard.py` | `DashboardRepo.fleet_stats` + the CTE |
| `src/admin_backend/schemas/dashboard.py` | `FleetStatsResponse`, `ActiveTenantsCard`, `PlatformUsersCard`, `StoresCard`, `MrrAggregatedCard`, `DeltaBlock` |
| `tests/integration/test_dashboard_router.py` | S1–S8 (S2/S5/S7 load-bearing) |

---

## `GET /api/v1/dashboard/governance-stats`  (E2)

Governance-posture KPI cards (5–8).

### 1. Endpoint summary

- **Method:** `GET`
- **Path:** `/api/v1/dashboard/governance-stats`
- **Description:** Returns cards 5–8 of the dashboard KPI grid: pending approvals, guardrails fired (24h), custom roles, modules deployed. Card-shaped response (deliberate D-30 exception). **Three of four cards are stubbed in v0** (`available: false` with locked `unavailable_reason`); `modules_deployed` is real and RLS-scoped.
- **Who can call:** any authenticated user. RLS-driven projection same as fleet-stats.

### 2. Request

**Headers:** Authorization required (PLATFORM or TENANT JWT).
**Path parameters:** none.
**Query parameters:** none.
**Request body:** none.

### 3. Response 200

```json
{
  "pending_approvals": {
    "value": 0,
    "sub_text": "across guardrails",
    "delta": null,
    "available": false,
    "unavailable_reason": "approvals_table_not_built"
  },
  "guardrails_fired_24h": {
    "value": 0,
    "escalations": 0,
    "sub_text": "0 escalations",
    "delta": null,
    "available": false,
    "unavailable_reason": "audit_logs_or_guardrails_not_wired"
  },
  "custom_roles": {
    "value": 0,
    "total": 0,
    "sub_text": "of 0 total",
    "delta": null,
    "available": false,
    "unavailable_reason": "custom_role_creation_not_shipped"
  },
  "modules_deployed": {
    "value": 27,
    "sub_text": "across 7 tenants",
    "delta": null,
    "available": true
  }
}
```

**Field reference:**

| Card | Field | Type | Notes |
|---|---|---|---|
| `pending_approvals` | `value` | int | Type-stable sentinel `0` while `available: false`. Will count rows in the future approvals table. |
| `pending_approvals` | `sub_text` | str | PLATFORM: `"across guardrails"`. TENANT: `"across your organization"`. |
| `guardrails_fired_24h` | `value` | int | Sentinel `0` while stubbed. Will count guardrail-fire events in the last 24h. |
| `guardrails_fired_24h` | `escalations` | int | Sentinel `0`. Of the fires, how many escalated. |
| `guardrails_fired_24h` | `sub_text` | str | Format: `"<N> escalations"`. Stub literal `"0 escalations"` in v0; same format applies once real. |
| `custom_roles` | `value` | int | Sentinel `0`. Future: `COUNT(*) FILTER (WHERE is_system = false)` on `roles`. |
| `custom_roles` | `total` | int | Sentinel `0`. Future: `COUNT(*)` on `roles`. |
| `custom_roles` | `sub_text` | str | Format: `"of <total> total"`. Same for both user types. |
| `modules_deployed` | `value` | int | `COUNT(*) FILTER (WHERE status = 'ENABLED')` on `tenant_module_access`. RLS-scoped. **Real in v0.** |
| `modules_deployed` | `sub_text` | str | PLATFORM: `"across <N> tenant(s)"` (singular/plural). TENANT: `"enabled for your organization"`. |
| (every card) | `delta` | `null` | All cards have `delta: null` in v0. |
| (every card) | `available` | bool | First three: `false`. `modules_deployed`: `true`. |
| (first three) | `unavailable_reason` | str | Fixed-vocabulary code per the table at the top of this doc. |

### 4. Response codes

| Code | When | Body |
|---|---|---|
| 200 | Success | Body as above |
| 401 | Missing or invalid JWT | `{code: "AUTH_MISSING" \| "AUTH_INVALID", ...}` |
| 500 | Internal server error | `{code: "INTERNAL_ERROR", ...}` |

### 5. Behaviour notes

- **Stub-to-real shape stability.** When a stub flips to real, only `available`, `value`, and `unavailable_reason` change. Field set and types stay identical. Frontend rendering MUST gate on `available`, not on whether `value > 0`.
- **`modules_deployed` is the only real card in v0.** Its query is a single SELECT against `tenant_module_access` filtered to `status = 'ENABLED'`. RLS-scoped: PLATFORM counts across all tenants; TENANT counts within own tenant.
- **`custom_roles` rationale.** Step 6.1 shipped RBAC *read* endpoints — the `roles` table is reachable, queryable, RLS-correct. The *write* surface to create custom roles is not on the v0 plan, so `COUNT(*) FILTER (WHERE is_system = false)` is structurally pinned at zero. Flipping `available: true` while the count cannot meaningfully change misrepresents platform state. Stays stubbed until the create-custom-role write surface ships.
- **Empty visible-set edge case.** `modules_deployed.value` is `0` and `sub_text` is `"across 0 tenants"` (PLATFORM) or `"enabled for your organization"` (TENANT). No 500.

### 6. Example calls

```bash
# Governance-stats as PLATFORM.
curl -s -H "Authorization: Bearer $JWT" \
  "https://admin-dev.ithina.com/api/v1/dashboard/governance-stats"

# Verify the unavailable_reason vocabulary.
curl -s -H "Authorization: Bearer $JWT" \
  "https://admin-dev.ithina.com/api/v1/dashboard/governance-stats" \
  | jq '{
      pa: .pending_approvals.unavailable_reason,
      gf: .guardrails_fired_24h.unavailable_reason,
      cr: .custom_roles.unavailable_reason,
      md: .modules_deployed.available
    }'
# Expected:
# pa: "approvals_table_not_built"
# gf: "audit_logs_or_guardrails_not_wired"
# cr: "custom_role_creation_not_shipped"
# md: true
```

### 7. Sample integration code

```typescript
type UnavailableReason =
  | "approvals_table_not_built"
  | "audit_logs_or_guardrails_not_wired"
  | "custom_role_creation_not_shipped";

type StubbableCard<T> = T & {
  available: boolean;
  unavailable_reason?: UnavailableReason;
};

type GovernanceStatsResponse = {
  pending_approvals: StubbableCard<{
    value: number; sub_text: string; delta: null;
  }>;
  guardrails_fired_24h: StubbableCard<{
    value: number; escalations: number; sub_text: string; delta: null;
  }>;
  custom_roles: StubbableCard<{
    value: number; total: number; sub_text: string; delta: null;
  }>;
  modules_deployed: {
    value: number; sub_text: string; delta: null; available: boolean;
  };
};

// Render: gate on .available; show "coming soon" treatment when false.
function renderGovernanceCard(card: { available: boolean }, view: () => JSX.Element) {
  return card.available ? view() : <ComingSoon />;
}
```

### 8. Implementation reference

| File | Role |
|---|---|
| `src/admin_backend/routers/v1/dashboard.py` | `get_governance_stats` handler + sub_text helpers |
| `src/admin_backend/repositories/dashboard.py` | `DashboardRepo.governance_stats` (modules_deployed only) |
| `src/admin_backend/schemas/dashboard.py` | `GovernanceStatsResponse`, `PendingApprovalsCard`, `GuardrailsFired24hCard`, `CustomRolesCard`, `ModulesDeployedCard` |
| `tests/integration/test_dashboard_router.py` | O1–O6 (O2/O5 load-bearing) |
