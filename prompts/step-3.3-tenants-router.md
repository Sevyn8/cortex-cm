# Prompt — Step 3.3: Tenants router + endpoints + cross-tenant test + endpoint doc

> Paste this entire block into a fresh Claude Code session when starting Step 3.3.

---

## Pre-flight

Before doing any work:

1. Run `./scripts/check_setup.sh`. If any check fails, stop and report.
2. Read `CLAUDE.md` fully — focus on "Per-endpoint documentation" section, error model, and code conventions.
3. Read `docs/architecture.md` "Request lifecycle" and "Multi-tenancy and data isolation".
4. Read `docs/api-contract.md` — every locked Q. Endpoint behaviour must match.
5. Read `BUILD_PLAN.md` Step 3.3 in full.
6. Read this prompt fully and confirm scope.

---

## Step ID and intent

**Step 3.3** — Tenants router + endpoints + cross-tenant test + endpoint doc.

Implement the first two real domain endpoints: `GET /v1/tenants` and `GET /v1/tenants/{tenant_id}`. **This step locks the canonical endpoint pattern** that all subsequent endpoint steps (4.5, 5.x, 6.x) will follow.

Three deliverables:

1. Working endpoints with tests (including cross-tenant isolation verified end-to-end).
2. OpenAPI spec produced cleanly via FastAPI auto-generation.
3. `docs/endpoints/tenants.md` — the per-endpoint markdown documentation that becomes the canonical example.

This is a CLAUDE_CODE step. Full vertical slice: model + schema + repo + router + endpoint + tests + doc.

---

## Scope in

### Prerequisites

By this point in the build, these should exist (Steps 3.1, 3.2):

- `src/admin_backend/models/tenant.py` (Tenant ORM model).
- `src/admin_backend/schemas/tenant.py` (TenantRead, TenantListResponse).
- `src/admin_backend/repositories/tenants.py` (TenantsRepo with read methods).

If any of these are missing or incomplete, stop and flag.

### File 1: `src/admin_backend/routers/__init__.py`

Empty.

### File 2: `src/admin_backend/routers/v1/__init__.py`

Empty.

### File 3: `src/admin_backend/routers/v1/tenants.py`

FastAPI router for tenants resource.

```python
from fastapi import APIRouter, Depends, HTTPException, Query, status
from typing import Annotated
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/v1/tenants", tags=["tenants"])

@router.get(
    "",
    response_model=TenantListResponse,
    summary="List tenants",
    description="""
    List tenants visible to the calling user.
    PLATFORM users see all tenants; TENANT users see only their own.
    """,
    responses={
        401: {"model": ErrorResponse, "description": "Authentication failed"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def list_tenants(
    status_filter: Annotated[str | None, Query(alias="status", description="Filter by status (comma-separated for multi-value)")] = None,
    tier: Annotated[str | None, Query(description="Filter by commercial tier")] = None,
    industry: Annotated[str | None, Query(description="Filter by industry code")] = None,
    q: Annotated[str | None, Query(description="Free-text search across name, legal_name, primary_contact_email")] = None,
    created_from: Annotated[date | None, Query(description="Filter created_at >= this date")] = None,
    created_to: Annotated[date | None, Query(description="Filter created_at <= this date")] = None,
    limit: Annotated[int, Query(ge=1, le=200, description="Max rows returned")] = 50,
    offset: Annotated[int, Query(ge=0, description="Skip this many rows")] = 0,
    sort: Annotated[str, Query(description="Sort key")] = "created_at_desc",
    session: AsyncSession = Depends(get_tenant_session),
) -> TenantListResponse:
    repo = TenantsRepo(session)
    items, total = await repo.list(
        status_filter=status_filter,
        tier=tier,
        industry=industry,
        q=q,
        created_from=created_from,
        created_to=created_to,
        limit=limit,
        offset=offset,
        sort=sort,
    )
    return TenantListResponse(
        items=[TenantRead.model_validate(t) for t in items],
        pagination=Pagination(
            limit=limit,
            offset=offset,
            total=total,
            has_more=(offset + limit) < total,
        ),
    )

@router.get(
    "/{tenant_id}",
    response_model=TenantRead,
    summary="Get tenant by ID",
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def get_tenant(
    tenant_id: UUID,
    session: AsyncSession = Depends(get_tenant_session),
) -> TenantRead:
    repo = TenantsRepo(session)
    tenant = await repo.get_by_id(tenant_id)
    if tenant is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "TENANT_NOT_FOUND", "message": f"Tenant {tenant_id} not found"},
        )
    return TenantRead.model_validate(tenant)
```

Notes:

- Query parameters: `status` is reserved in some contexts; use `status_filter` internally with `alias="status"` for the URL.
- Sort parameter validation: accept only known sort keys; reject others with 400. Implement in repo.
- The handler is thin. Filter logic, sorting, pagination — all inside `TenantsRepo`.
- 404 detail format uses the dict shape that the global exception handler converts to ErrorResponse.

### File 4: Update `src/admin_backend/main.py`

Wire the tenants router into the app:

```python
from admin_backend.routers.v1 import tenants
app.include_router(tenants.router)
```

Also add a global exception handler that converts our typed errors and HTTPException to the contract `ErrorResponse` shape:

```python
@app.exception_handler(AdminBackendError)
async def admin_backend_error_handler(request: Request, exc: AdminBackendError):
    return JSONResponse(
        status_code=exc.http_status,
        content=ErrorResponse(
            code=exc.code,
            message=exc.message,
            details=exc.details,
            request_id=getattr(request.state, "request_id", None),
        ).model_dump(),
    )

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    detail = exc.detail
    if isinstance(detail, dict) and "code" in detail:
        return JSONResponse(
            status_code=exc.status_code,
            content=ErrorResponse(
                code=detail["code"],
                message=detail.get("message", ""),
                details=detail.get("details"),
                request_id=getattr(request.state, "request_id", None),
            ).model_dump(),
        )
    # Fallback for raw HTTPException
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(
            code="HTTP_ERROR",
            message=str(exc.detail),
            details=None,
            request_id=getattr(request.state, "request_id", None),
        ).model_dump(),
    )
```

### File 5: `tests/integration/test_tenants_endpoints.py`

Integration tests using FastAPI TestClient + a real test database.

Setup fixture: insert two tenants (A and B) plus stores/users belonging to each. Use the bootstrap pattern (deterministic UUIDs).

Test cases:

1. **List as PLATFORM user** — JWT with `user_type=PLATFORM`. Expect both tenants in response.
2. **List as TENANT A user** — JWT with `tenant_id=A`. Expect only tenant A in response.
3. **List as TENANT B user** — JWT with `tenant_id=B`. Expect only tenant B.
4. **Get tenant A as TENANT A user** — 200 with tenant A's data.
5. **Get tenant A as TENANT B user** — 404 (RLS-as-404 pattern).
6. **Get nonexistent tenant** — 404.
7. **Filter by status** — `?status=ACTIVE` returns only active tenants.
8. **Filter by multiple statuses** — `?status=ACTIVE,SUSPENDED` returns both.
9. **Search by `q`** — `?q=acme` matches tenant with "Acme" in name.
10. **Pagination** — `?limit=1&offset=0` returns 1 item, `has_more=true`. `?limit=1&offset=1` returns next.
11. **Invalid sort key** — returns 400 with `code=VALIDATION_ERROR`.
12. **No JWT** — 401 with `code=AUTH_MISSING`.
13. **Invalid tenant_id format** — `GET /v1/tenants/not-a-uuid` returns 400.

Use FastAPI dependency overrides to inject test DB session if needed. Use `make_test_jwt(...)` from Step 2.1 to mint test tokens.

### File 6: `docs/endpoints/tenants.md`

Per-endpoint markdown documentation. **This file is the canonical example for all subsequent endpoint docs.** Follow the 8-section pattern from `CLAUDE.md` "Per-endpoint documentation":

1. Endpoint summary (table at top: method, path, description, who can call)
2. Request — auth, path params, query params, body
3. Response 200 — full shape with realistic sample, field-by-field reference table
4. Response codes — error table with sample bodies for each
5. Behaviour notes — RLS scope, sort, pagination, edge cases (RLS-as-404 pattern, search semantics, performance)
6. Example calls (curl)
7. Sample integration code (TypeScript)
8. Implementation reference (file pointers)

Use realistic sample data:

- UUIDs in proper UUID4 format.
- `monthly_revenue_usd` as a string in JSON (per Q11 NUMERIC decision).
- ISO 8601 UTC timestamps.

The TypeScript snippet should include type definitions for `Tenant`, `TenantListResponse`, `Pagination`, plus `listTenants(jwt, filters)` and `getTenant(jwt, tenantId)` async functions with proper error handling (especially the 404-from-RLS pattern).

If you've previously seen `docs/endpoints/_example_tenants.md` or a draft of this doc, use it as a reference but ensure the final version reflects the actual endpoints implemented (params, error codes, behaviour notes).

---

## Scope out

- Other resources (stores, users, etc.).
- Write endpoints (POST / PATCH / DELETE).
- Cross-tenant impersonation routes (e.g., `/v1/admin/tenants/{tenant_id}/...` for staff). Not in v0.

---

## Implementation hints

### Sort handling

Acceptable sort keys: `created_at_asc`, `created_at_desc`, `name_asc`, `name_desc`, `tier_asc`, `tier_desc`. Implement in `TenantsRepo.list()`:

```python
SORT_MAP = {
    "created_at_asc": Tenant.created_at.asc(),
    "created_at_desc": Tenant.created_at.desc(),
    "name_asc": Tenant.name.asc(),
    ...
}
if sort not in SORT_MAP:
    raise ValidationError(f"unknown sort key: {sort}")
order_by = [SORT_MAP[sort], Tenant.id.asc()]  # stable secondary sort
```

### Free-text search

For `q` parameter, use ILIKE across name/legal_name/primary_contact_email:

```python
if q:
    pattern = f"%{q}%"
    stmt = stmt.where(
        Tenant.name.ilike(pattern)
        | Tenant.legal_name.ilike(pattern)
        | Tenant.primary_contact_email.ilike(pattern)
    )
```

ILIKE is case-insensitive, which matches the contract.

### Total count

For pagination, run two queries: one with limit/offset, one for total count without limit. Or: use a single query with `func.count().over()` window function. Recommend two queries — clearer, indistinguishable performance at v0 scale.

### RLS-as-404 in tests

For test 5 (Get tenant A as TENANT B user → 404), this test explicitly verifies the contract claim. The repo's `get_by_id` returns None because RLS filters tenant A's row out for tenant B's session. Handler converts None to 404. Test asserts on the response: `status_code == 404`, `body["code"] == "TENANT_NOT_FOUND"`.

### OpenAPI quality

After this step, hit `http://localhost:8000/v1/openapi.json` and confirm:

- Both endpoints appear under "tenants" tag.
- Query parameters have descriptions and types.
- Response 200 has the TenantListResponse / TenantRead schema.
- Error responses (401, 404, 500) reference ErrorResponse.
- The `/v1/docs` Swagger UI is browsable and explorable.

If any of these are weak, improve handler decorators (descriptions, response_model, etc.) until the spec is complete.

### Stable test data setup

The tests need deterministic data. Use a session-scoped fixture that:

1. Connects to a test DB (separate from local dev DB if `TEST_DATABASE_URL` is set).
2. Runs migrations.
3. Inserts known test data with deterministic UUIDs.
4. Yields to tests.
5. Rolls back / drops at end.

Or simpler: each test starts with a clean DB via savepoint rollback. Pick the pattern that fits the existing test fixture conventions (check Step 1.5 smoke test for precedent).

---

## Acceptance criteria

- All files created or modified per scope above.
- `uv run uvicorn admin_backend.main:app --reload` starts cleanly.
- `curl http://localhost:8000/v1/tenants` returns 401 (no JWT).
- `curl -H "Authorization: Bearer $JWT" http://localhost:8000/v1/tenants` (with valid stub JWT) returns 200 with TenantListResponse.
- `curl -H "Authorization: Bearer $JWT" http://localhost:8000/v1/tenants/<uuid>` returns either 200 or 404.
- All 13 integration tests pass.
- Cross-tenant isolation tests (5, 3, 2) verify RLS works end-to-end.
- OpenAPI spec at `/v1/openapi.json` shows tenants endpoints with correct schemas matching `docs/api-contract.md`.
- `docs/endpoints/tenants.md` produced as the canonical example for subsequent endpoint docs (8 fixed sections per endpoint, see CLAUDE.md "Per-endpoint documentation").
- mypy strict clean.

---

## Stop and ask if

- The repo class signature (e.g., `list(...)`) doesn't match what was built at Step 3.2. Prompt 3.2 may have lighter scope; this prompt may need to add a `list` method with full filter support.
- A query parameter the prompt asks for (e.g., `industry`) isn't a column in the DDL. Verify against `tenants_v3.sql`.
- `docs/api-contract.md` has TBD on a question that materially affects this step (e.g., Q1 naming not locked). Implement with the recommended default and flag.

---

## What to report at end

- Files created/modified (line counts).
- Test counts (all 13 cases pass / fail / skip).
- Sample response output from `GET /v1/tenants` (with redacted UUIDs if needed) for visual verification.
- Sample OpenAPI spec excerpt for tenants endpoints.
- Confirmation that `docs/endpoints/tenants.md` matches the implementation (no drift).

---

## After completing

Propose a git commit per CLAUDE.md "After completing a task" Pattern A:

```
git status
git add -A
git commit -m "Step 3.3: tenants router + endpoints + cross-tenant test + endpoint doc

- GET /v1/tenants and GET /v1/tenants/{id}
- Filter, search, sort, pagination support
- Global exception handler for typed errors and HTTPException
- 13 integration tests including cross-tenant isolation
- OpenAPI spec auto-generated and verified
- docs/endpoints/tenants.md as canonical example"
```

Ask user "Run? yes / no / edit message".

---

## End of prompt
