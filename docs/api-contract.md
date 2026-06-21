# API Contract — Ithina Admin Backend v0

> **Status: TEMPLATE.** This document is a pre-meeting template. The "Decision" rows are empty until the API contract sync meeting (BUILD_PLAN.md Step 2.0) is held with the frontend developer. Once decisions are locked, this document becomes part of the standing context loaded by Claude Code at every session start.

---

## Purpose

Lock the contract between the admin backend and the Admin Console frontend before any endpoint code is written. Avoids rework on D#4 when frontend integrates with the deployed backend.

---

## Meeting metadata

| Field | Value |
|---|---|
| Meeting date | TBD |
| Attendees | (you), (frontend developer name) |
| Status | NOT YET HELD |
| Locked at | TBD |
| Reviewed for v0.1 | TBD |

---

## How to use this document

1. **Before the meeting (~20 min prep):** read through the questions below. Form a starting position for each. Note any constraints (your stack, frontend's stack, time pressure).
2. **In the meeting (~45-60 min):** walk through each question. Capture decisions in the "Decision" column. Capture rationale where it matters.
3. **After the meeting (~10 min):** convert tentative answers to final, fill in metadata above, mark status as LOCKED, share with frontend developer for confirmation.
4. **Reference forever after:** Claude Code reads this at session start; engineering team consults when adding endpoints.

---

# Decisions to lock

## Q1 — Response field naming

**Question.** Are JSON response field names snake_case or camelCase?

**Context.** Python convention is snake_case. JavaScript/TypeScript convention is camelCase. Frontend may want camelCase to match its codebase, OR may be fine with snake_case if generating types from OpenAPI spec.

**Options.**

| Option | Backend produces | Frontend sees |
|---|---|---|
| A | `{"tenant_id": "..."}` | `{"tenant_id": "..."}` (snake_case throughout) |
| B | `{"tenantId": "..."}` (Pydantic alias) | `{"tenantId": "..."}` (camelCase throughout) |
| C | Pydantic `populate_by_name=True` accepts both; serialises in one form | Either |

**Decision.** TBD

**Rationale.** TBD

---

## Q2 — Response wrapping for list endpoints

**Question.** For endpoints returning a list, is the response a raw array or wrapped in an envelope?

**Options.**

| Option | Example response |
|---|---|
| A — raw array | `[{"id": "..."}, {"id": "..."}]` |
| B — `{ data: [...] }` | `{"data": [{"id": "..."}], "total": 47}` |
| C — `{ items: [...], pagination: {...} }` | `{"items": [...], "pagination": {"total": 47, "limit": 20, "offset": 0}}` |

**Trade-offs.**
- A is simplest but provides no room for pagination metadata.
- B/C are extensible (add more sibling fields later) but require boilerplate per endpoint.

**Decision.** TBD

**Rationale.** TBD

---

## Q3 — Pagination shape

**Question.** How does the frontend request page N+1 of a list?

**Options.**

| Option | Request | Response |
|---|---|---|
| A — offset/limit | `?offset=20&limit=20` | `{ "items": [...], "total": 100, "offset": 20, "limit": 20 }` |
| B — cursor | `?cursor=abc123&limit=20` | `{ "items": [...], "next_cursor": "def456" }` |
| C — page/size | `?page=2&size=20` | `{ "items": [...], "page": 2, "size": 20, "total_pages": 5 }` |

**Trade-offs.**
- A: simple, supports random-access pages, gets slow for deep pagination on large tables.
- B: more efficient at scale, opaque cursor, no "skip to page N" support.
- C: same as A but more user-friendly for UI; equivalent under the hood.

**Decision.** TBD (recommend A for v0; tables are small).

**Rationale.** TBD

---

## Q4 — Date / time format

**Question.** How are timestamps serialised in JSON?

**Options.**

| Option | Example |
|---|---|
| A — ISO 8601 UTC string | `"2026-04-30T14:23:11Z"` |
| B — ISO 8601 with timezone offset | `"2026-04-30T14:23:11+00:00"` |
| C — Unix timestamp seconds | `1714492991` |
| D — Unix timestamp milliseconds | `1714492991000` |

**Recommendation.** A — ISO 8601 UTC. Human-readable, timezone-explicit, widely supported in JS Date parsing, default for FastAPI and Pydantic.

**Decision.** TBD

**Rationale.** TBD

---

## Q5 — Error response shape

**Question.** What does the JSON body look like for a 4xx or 5xx response?

**Recommended shape.**

```json
{
  "code": "TENANT_NOT_FOUND",
  "message": "Tenant with id abc-123 not found",
  "details": null,
  "request_id": "req-uuid"
}
```

**Fields.**

| Field | Purpose |
|---|---|
| `code` | Stable machine-readable identifier. Frontend switches on this. |
| `message` | Human-readable English. Frontend may display directly or translate via lookup. |
| `details` | Optional structured context (e.g., for validation errors: `{"field_errors": {...}}`). Null if not used. |
| `request_id` | Same as `X-Request-Id` response header. For support / debugging. |

**Validation error example (400):**

```json
{
  "code": "VALIDATION_ERROR",
  "message": "Request body has 2 validation errors",
  "details": {
    "field_errors": {
      "email": "not a valid email address",
      "tier": "must be one of: ENTERPRISE, MID_MARKET, SMB, SINGLE_STORE"
    }
  },
  "request_id": "req-uuid"
}
```

**Decision.** TBD (confirm shape; capture any frontend axios interceptor expectations).

**Rationale.** TBD

---

## Q6 — Authentication header

**Question.** What format does the frontend send credentials in?

**Recommendation.** `Authorization: Bearer <jwt>` (RFC 6750). Standard. Auth0-compatible.

**Confirmations needed.**
- Frontend pulls JWT from Auth0 client SDK (production) or local stub (build phase)?
- Refresh token handling: silent refresh via Auth0 SDK, or logout-on-expiry?
- Token storage: in-memory (recommended), localStorage (less secure but persists across tabs), httpOnly cookie (more secure, requires CSRF handling)?

**Decision.** TBD

**Rationale.** TBD

---

## Q7 — Null fields in responses

**Question.** When a value is missing or not applicable, does the field appear with null, or is it omitted?

**Options.**

| Option | Example |
|---|---|
| A — always present, null if missing | `{"name": "Acme", "phone": null}` |
| B — omitted if missing | `{"name": "Acme"}` |

**Trade-offs.**
- A: stable JSON shape; frontend always knows what fields to expect; TypeScript types simpler.
- B: smaller payloads; less "noise" for sparsely-populated entities.

**Recommendation.** A. Stability over byte savings. v0 entities are small anyway.

**Decision.** TBD

**Rationale.** TBD

---

## Q8 — OpenAPI spec consumption

**Question.** Does the frontend generate TypeScript types from OpenAPI, or hand-write them?

**Context.** The backend auto-generates an OpenAPI 3.x spec at `/v1/openapi.json` (FastAPI built-in feature). Frontend can optionally use a generator like `openapi-typescript` or `orval` to produce type definitions and even API client code.

**Implications if frontend generates types.**
- The OpenAPI spec must be high-quality (every endpoint described, every field typed, examples populated).
- Pydantic schemas should have explicit `Field(..., description="...")` for all fields.
- Endpoint changes ripple to frontend types; need to coordinate releases.

**Implications if frontend hand-writes types.**
- Frontend reads `/v1/openapi.json` for reference but doesn't import it.
- Backend OpenAPI quality is "nice to have" rather than load-bearing.

**Decision.** TBD

**Rationale.** TBD

---

## Q9 — Endpoint granularity

**Question.** Are there places where the frontend wants a fat endpoint (one call returning multiple related entities) vs separate calls (one entity per call)?

**Examples to discuss.**

| Scenario | Fat option | Thin option |
|---|---|---|
| Tenant detail page | `GET /v1/tenants/{id}` returns tenant + stores + users + recent audit | Separate calls: `GET /v1/tenants/{id}`, `GET /v1/tenants/{id}/stores`, etc. |
| User detail with role assignments | `GET /v1/tenant-users/{id}` returns user + their role assignments | Separate calls |
| Org tree | `GET /v1/org-tree` returns whole tree (nested JSON) | `GET /v1/org-nodes` flat list, frontend constructs tree |

**General preference.** Thin endpoints (one resource per endpoint) unless the frontend has specific reasons for fat endpoints (perceived performance, fewer round-trips for a critical screen).

**Frontend's preferences.** TBD (capture per scenario in the meeting).

**Decision.** TBD

**Rationale.** TBD

---

## Q10 — Filter and search query parameters

**Question.** How are filters and searches expressed in URLs?

**Common cases.**

| Need | Option A | Option B |
|---|---|---|
| Filter by status | `?status=ACTIVE` | `?filter[status]=ACTIVE` |
| Multiple values | `?status=ACTIVE,SUSPENDED` | `?status=ACTIVE&status=SUSPENDED` |
| Free-text search | `?q=acme` | `?search=acme` |
| Date range | `?from_date=2026-01-01&to_date=2026-01-31` | `?date[gte]=...&date[lte]=...` |
| Combined | `?status=ACTIVE&q=acme&offset=0&limit=20` | (same shape) |

**Recommendation.** Simple flat query params (Option A), comma-separated for multi-value, `q` for search, `from_date` / `to_date` for ranges. Avoids complex bracket syntax.

**Decision.** TBD (per filter type if frontend has preferences).

**Rationale.** TBD

---

## Q11 — NUMERIC field serialisation (decimals, money, quantities)

**Question.** How are `NUMERIC(p, s)` Postgres values (prices, monthly_revenue_usd, weights) serialised in JSON?

**Context.** Postgres `NUMERIC` is arbitrary-precision decimal. JavaScript `Number` is IEEE 754 double-precision float. Numbers larger than ~9 quadrillion or with many decimal places lose precision when serialised as JSON numbers and parsed by JS.

**Options.**

| Option | Example | Trade-off |
|---|---|---|
| A — JSON number (Pydantic default for `Decimal`) | `"monthly_revenue_usd": 1500000.00` | Simple. Risk of precision loss on the frontend for very large values. |
| B — JSON string | `"monthly_revenue_usd": "1500000.00"` | Preserves precision exactly. Frontend must parse with `BigNumber.js` / similar for arithmetic. |
| C — Mixed: string for money, number for everything else | `"monthly_revenue_usd": "1500000.00"`, `"num_stores": 12` | Pragmatic. Most NUMERIC fields don't need string precision. |

**Recommendation.** **C — Mixed.**
- Monetary fields (any USD/EUR amount, e.g., `monthly_revenue_usd`, `unit_cost`, `retail_price`): serialise as **string** to preserve precision.
- Counting fields (`num_stores`, integer counts): serialise as **JSON number**.
- Quantities (e.g., `stock_qty` with weight precision): serialise as **string** if the quantity uses fractional precision; **number** otherwise.

**Implementation in Pydantic.** Use a custom serialiser:

```python
from decimal import Decimal
from pydantic import BaseModel, field_serializer

class TenantRead(BaseModel):
    monthly_revenue_usd: Decimal | None
    num_stores: int | None

    @field_serializer("monthly_revenue_usd")
    def ser_money(self, v: Decimal | None) -> str | None:
        return str(v) if v is not None else None
```

Or define a reusable `MoneyField = Annotated[Decimal, PlainSerializer(...)]` type and use it across schemas.

**Decision.** TBD (confirm in the meeting; recommendation above).

**Rationale.** TBD

**Common cases.**

| Need | Option A | Option B |
|---|---|---|
| Filter by status | `?status=ACTIVE` | `?filter[status]=ACTIVE` |
| Multiple values | `?status=ACTIVE,SUSPENDED` | `?status=ACTIVE&status=SUSPENDED` |
| Free-text search | `?q=acme` | `?search=acme` |
| Date range | `?from_date=2026-01-01&to_date=2026-01-31` | `?date[gte]=...&date[lte]=...` |
| Combined | `?status=ACTIVE&q=acme&offset=0&limit=20` | (same shape) |

**Recommendation.** Simple flat query params (Option A), comma-separated for multi-value, `q` for search, `from_date` / `to_date` for ranges. Avoids complex bracket syntax.

**Decision.** TBD (per filter type if frontend has preferences).

**Rationale.** TBD

---

# Standard non-negotiables

These are not up for discussion in the meeting; they're locked by the architecture or v0 scope.

| Rule | Reason |
|---|---|
| All endpoints under `/v1/` URI prefix | Versioning baseline; v2 will be additive |
| All requests require `Authorization: Bearer <jwt>` except `/v1/health`, `/v1/docs`, `/v1/openapi.json`, `/metrics` | Multi-tenant isolation requires authenticated tenant context |
| All responses are `application/json; charset=utf-8` | UTF-8 throughout; no XML, no form-urlencoded responses |
| All UUIDs serialised as strings, hyphenated form: `"abc-123-def"` | UUID4 default; standard JSON serialisation |
| Server-side pagination only | No "return all rows"; default limit applies if not specified |
| `X-Request-Id` header on every response | Correlation with backend logs |

---

# Extensions to capture during meeting

Things beyond the 10 questions above that come up in conversation:

- **Bulk endpoints?** (e.g., `POST /v1/tenant-users/batch` for v1 — capture v0 stance)
- **WebSockets / SSE?** (not in v0; capture future need if frontend has plans)
- **File upload?** (not in v0; capture if frontend will need it eventually)
- **CORS origins?** (likely just `http://localhost:5173` for dev, `https://admin.ithina.com` for prod)
- **Rate limiting expectations?** (not in v0; capture if frontend assumes any)
- **Caching directives?** (`Cache-Control` headers on responses; default to `no-store` for tenant data)
- **Anything frontend's framework imposes?** (e.g., axios interceptor expects specific shape; React Query caches differently per response shape)

---

# Sample endpoint contracts (placeholder)

After the meeting, fill in a few canonical examples for Claude Code to reference when implementing endpoints.

## Sample: GET /v1/tenants

**Description.** List all tenants the caller has access to.

**Request.**
- Headers: `Authorization: Bearer <jwt>`
- Query params: `?limit=20&offset=0&status=ACTIVE` (optional)

**Response 200.**

```json
TBD — fill in based on locked decisions for Q1, Q2, Q3, Q7
```

**Response 401.**

```json
{
  "code": "AUTH_MISSING",
  "message": "Authorization header missing or invalid",
  "details": null,
  "request_id": "req-uuid"
}
```

## Sample: GET /v1/tenants/{tenant_id}

**Description.** Get a single tenant by ID.

**Request.**
- Headers: `Authorization: Bearer <jwt>`
- Path: `tenant_id` (UUID)

**Response 200.**

```json
TBD
```

**Response 404.**

```json
{
  "code": "TENANT_NOT_FOUND",
  "message": "Tenant with id <id> not found",
  "details": null,
  "request_id": "req-uuid"
}
```

---

# Cross-references

- `CLAUDE.md` for code conventions, error class hierarchy, environment variables.
- `docs/architecture.md` for system flow and authentication mechanics.
- `BUILD_PLAN.md` Step 2.0 for the meeting execution plan.

---

# End of API contract template
