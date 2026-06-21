# Prompt — Step 6.9.2: gate factory + PermissionDeniedError + /me/* endpoints

> Generated 2026-05-13. Calibrated against codebase HEAD at commit 63dd565 
> ("Step 6.9.1: has_permission() core + PermissionGrant + ReasonCode"). 
> Pytest baseline: 276 passes, 0 failures.
>
> Paste this entire block into a fresh Claude Code session to start 
> Step 6.9.2.

---

## Standing discipline (read first)

### On the code sketches in this prompt

Code blocks and SQL sketches below are STARTING POINTS, not the answer. 
The operator drafts prompts without live access to the codebase. You 
have live access. Use it.

Where you have a better implementation than what's sketched — because 
you've read the actual surrounding code, the actual table indexes, 
the actual existing patterns — IMPLEMENT THE BETTER VERSION. Surface 
the deviation in your report with a one-line reason.

Specifically:
- SQL sketches are illustrative. If a different SQL shape produces 
  the same answer with a better query plan, use the better shape.
- Python code shapes follow common patterns but may not match this 
  codebase's exact conventions. Read existing similar-purpose files 
  before writing new code (for new repository code, read existing 
  Repo files; for new schemas, read existing similar Pydantic 
  schemas).
- File locations in this prompt are suggestions. Use a different 
  location if the suggestion conflicts with existing organization. 
  Surface your choice.
- Function signatures may be over-engineered. Refine if a different 
  shape reads cleaner.
- If a claim about existing code in this prompt is verifiably wrong, 
  do what the actual code requires. The actual file at HEAD is the 
  source of truth.
- If a locked decision starts requiring workarounds when implemented, 
  STOP and surface. Locked decisions can be revisited via the design 
  conversation; silent workaround cannot happen.

Locked decisions (in the "Locked decisions" section) remain locked. 
Everything else is calibrated guidance — refine where you can 
improve it.

### Documentation writing

Updates to CLAUDE.md, BUILD_PLAN.md, architecture.md must be 
technical, sharp, and concise. Documentation has long shelf life.

Rules:
1. State facts. Skip throat-clearing. ("It is worth noting that..." 
   → just state the fact.)
2. Active voice, present tense for current state. Past tense only 
   for historical record.
3. One sentence per fact. Don't pack multiple claims into compound 
   sentences.
4. Specific over general. "Improves performance" → "1 query per 
   check instead of N+1." "Various tests" → "12 tests covering X, 
   Y, Z."
5. Cite by reference, don't repeat. "(per D-34)" not paraphrase.
6. Match the surrounding document's style. CLAUDE.md uses brief 
   bulleted Current state entries. BUILD_PLAN.md uses denser 
   step-body prose.
7. No meta-commentary. "This was a significant change because..." 
   → just describe the change.
8. Cut adjectives that don't add information. "Comprehensive test 
   coverage" → "12 tests covering...".

Bad example (avoid):
> "This step introduces the PermissionDeniedError class which is a 
> critically important component of the RBAC enforcement layer. 
> The implementation carefully handles both PLATFORM and TENANT 
> users through a robust dependency mechanism. Comprehensive testing 
> has been added."

Good example (match this style):
> "require(MODULE, RESOURCE, ACTION, SCOPE) factory at 
> src/admin_backend/auth/permissions.py. Returns a FastAPI 
> dependency that calls has_permission() and raises 
> PermissionDeniedError on denial. /me/permissions and /me/can-do 
> endpoints at routers/v1/me.py. 18 integration tests at 
> tests/integration/test_me_router.py."

### Definition of done

Before reporting complete, verify:
1. All tests in the step's scope pass.
2. All previously-passing tests still pass (no regressions).
3. mypy strict clean on every file touched.
4. EXPLAIN ANALYZE captured for new queries (the /me/permissions 
   broader query). Include output in your report.
5. Re-read each new file end-to-end. If anything reads forced or 
   unclear, refactor before reporting complete.
6. CLAUDE.md and BUILD_PLAN.md updates are sharp per the 
   Documentation writing rules above.
7. Forward-notes have actual FN-AB numbers, not placeholders.
8. No TODO comments in code. Deferred items are forward-notes in 
   CLAUDE.md or they're not deferred.
9. Pre-commit checks (check_setup.sh, pytest, mypy, alembic check) 
   all pass.
10. docs/endpoints/openapi.json regenerated; /me/permissions and 
    /me/can-do present with summary, description, response shape.

If any item is not met, the work is not done. Surface the gap.

---

## Context: why this step exists and why now

Section 6.9 (RBAC enforcement layer) is the second work of Stage 2. 
Three sub-steps:

- **Step 6.9.1 (SHIPPED at commit 63dd565):** `has_permission()` 
  pure-SQL permission check, PermissionGrant frozen dataclass, 
  ReasonCode enum. Callable but not yet called by production.
- **6.9.2 (this step):** FastAPI gate dependency factory + 
  PermissionDeniedError + /me/permissions + /me/can-do endpoints. 
  Wires has_permission into FastAPI as a declarative gate; ships 
  /me/* endpoints for frontend UI gating. No existing endpoints 
  get retrofitted.
- **6.9.3 (next):** Retrofit existing GET endpoints with the gate. 
  Per-resource anchor dependencies. Mandatory-gate discipline test.

This step ships the gate machinery and the /me/* surface. Existing 
production endpoints still behave identically; 6.9.3 wires the 
gate in.

### Design intent (locked during design conversation 2026-05-13, do not deviate)

1. **Gate is a FastAPI Depends factory.** `require(MODULE, RESOURCE, 
   ACTION, SCOPE)` is a factory function that returns a dependency. 
   Endpoints declare `_: None = Depends(require(...))`. Novel pattern 
   in this codebase (no precedent); FastAPI well-documents it.
2. **Gate shares the request's session.** Receives session via 
   `Depends(get_tenant_session_dep)`. One session per request.
3. **`_require_platform_auth` retirement deferred to 6.9.3 design.** 
   6.9.2 does not touch it.
4. **`me_router` with `/me` prefix.** Single APIRouter at 
   `routers/v1/me.py`; two routes (`/permissions`, `/can-do`); 
   registered in `main.py`.
5. **/me/permissions response shape:** structured grants per item 
   (`{module, resource, action, scope, anchor_path}`). Always 
   returns array. Envelope: `{"permissions": [...]}`.
6. **PermissionDeniedError in `errors.py`.** Shared, system-wide. 
   Carries structured fields via `**context`. http_status=403, 
   code="PERMISSION_DENIED".
7. **Response envelope `details=null`** on permission denial (matches 
   every other error in v0). Structured fields available in 
   `exc.context` for logs only.
8. **Per-endpoint anchor dependency pattern (FN-AB-25 resolved).** 
   6.9.2 establishes the decision. The actual per-resource anchor 
   functions (`get_store_anchor`, `get_user_anchor`, etc.) land in 
   6.9.3 during retrofit. 6.9.2 ships the `require()` factory that 
   accepts a `target_anchor: str | None` parameter from wherever.
9. **"Gate denies before any Repo call fires" LOAD-BEARING test 
   shipped in 6.9.2.** Pattern: `patch.object` + `AsyncMock` + 
   `call_count == 0`.

### Out of scope for 6.9.2

- Retrofitting existing endpoints with the gate → 6.9.3.
- Per-resource anchor dependencies (`get_store_anchor`, etc.) → 6.9.3.
- Mandatory-gate-discipline meta-test → 6.9.3.
- `_require_platform_auth` retirement → 6.9.3 design.
- Audit log writes on denials → Step 6.16.
- Impersonation read-only enforcement → forward-noted in 6.9.1, stays 
  deferred.
- Granular ReasonCode expansion → deferred until Step 6.16 needs them.
- Frontend integration with /me/permissions → coordinated by operator 
  after 6.9.2 ships.

---

## Pre-flight

1. Run `./scripts/check_setup.sh`. Expect 35/35.
2. `git log --oneline -3` — confirm HEAD is `63dd565 Step 6.9.1: ...` 
   (or later if more commits have landed; surface any unexpected commits).
3. `git status` — note any pre-existing items in the working tree; do 
   not stage them. Surface anything unexpected.
4. `uv run alembic heads` — expect `3e05299cb533` (no migration in 
   this step).
5. `uv run pytest --tb=no -q | tail -5` — expect 276 passes, 0 failures. 
   **If anything other than 276 passes, stop and report.**
6. Read `src/admin_backend/auth/permissions.py` fully. This is 6.9.1's 
   shipped has_permission. The new `require(...)` factory in this 
   step lives in the same file (or a new neighbor file; pick what 
   reads cleanly).
7. Read `src/admin_backend/auth/permission_grant.py`. This is 6.9.1's 
   PermissionGrant. /me/permissions response items use this shape.
8. Read `src/admin_backend/auth/reason_code.py`. This is 6.9.1's 
   ReasonCode enum. PermissionDeniedError carries it.
9. Read `src/admin_backend/errors.py` fully. Focus on:
   - `AdminBackendError` base class (note the `**context` kwargs 
     mechanism)
   - `ClientError` subclass
   - `AuthMissingError` (precedent for raising-inside-Depends)
   - `InvalidSortKeyClientError` (precedent for shared system-wide 
     error in errors.py)
   - `build_error_payload` if defined here (otherwise it's in 
     main.py)
10. Read `src/admin_backend/main.py` lines covering the FastAPI 
    exception handler and router registration. Specifically:
    - The handler at ~line 225 that catches AdminBackendError and 
      builds the response envelope
    - The router-include block where new routers get registered
11. Read `src/admin_backend/dependencies.py`. Focus on:
    - `get_auth_context` (raises AuthMissingError; precedent for 
      raising-inside-Depends)
    - `get_tenant_session_dep` (the session-opening dependency)
    - The async generator pattern
12. Read `src/admin_backend/routers/v1/platform_users.py`. Focus on:
    - `_require_platform_auth(auth)` helper (line 102-109) — context, 
      not for modification
    - `PlatformAccessRequiredError` class (line 78-87) — precedent for 
      403-shaped client error
    - How handlers consume `Depends(get_auth_context)` and 
      `Depends(get_tenant_session_dep)`
13. Read `src/admin_backend/routers/v1/rbac.py`. Focus on:
    - `_audience_filter_for(auth)` (line 108-115) — context, not for 
      modification
    - How the router groups multiple route definitions under one 
      APIRouter
14. Read `src/admin_backend/routers/v1/tenants.py` at the route 
    declarations. This is the canonical example for new router files.
15. Read `tests/integration/test_has_permission.py` fully. The new 
    tests in this step share test infrastructure (the `_lookup_permission_id` 
    helper, the JWT fixtures, the seed-data assumptions). Match the 
    pattern.
16. Read `tests/integration/test_role_assignments_router.py` focus 
    lines 101-134 (the R2 LOAD-BEARING no-call-invariant test). 
    The "gate denies before Repo call" test in this step mirrors R2's 
    pattern exactly: `patch.object` + `AsyncMock` + `call_count == 0`.
17. Read `tests/integration/conftest.py`. Understand:
    - `app_client` fixture (or whatever the actual fixture is named 
      at HEAD; verify name before using)
    - `_platform_jwt(settings, ...)` and `_tenant_jwt(settings, ...)` 
      helpers (or actual names)
    - `make_tenant`, `make_tenant_user`, `make_platform_user` factories
    
    If fixture or helper names differ from what this prompt assumes, 
    use the actual names. Surface in your report which names were 
    used vs assumed.
18. Read `CLAUDE.md` fully. Focus on:
    - **D-13** — audit-actor patterns
    - **D-17** — RLS-as-404 (the /me/* endpoints don't use this, but 
      know the precedent for "no leak via 403")
    - **D-30** — list envelope shape (/me/permissions departs from 
      this — confirmed by design)
    - **D-31** — append-only response shapes
    - **Note on the v0 auth model** — context for `_require_platform_auth`
    - **Forward-notes section** — two new forward-notes added 
      during this step; FN-AB numbers assigned at implementation 
      time based on the next available slot in CLAUDE.md (do not 
      reserve specific numbers in advance)
19. Read `BUILD_PLAN.md` Section 6.9 entry. Step 6.9.2 entry rewritten 
    in this commit.
20. Read `reports/step-6_9_2-design-investigation-2026-05-13.md`. The 
    24 findings ground the design conversation. Key facts at HEAD:
    - F-VERIFY-1/2/3: 6.9.1 shipped as designed
    - F-DEPEND-3: no `Depends` factory pattern in codebase yet — 6.9.2 
      establishes it
    - F-DEPEND-4: raising ClientError inside Depends supported (precedent: 
      `get_auth_context` raises AuthMissingError)
    - F-ERR-3: error envelope `details=null` everywhere today
    - F-ROUTER-1: no /me/* endpoints exist; greenfield
21. Confirm Postgres `ltree` extension loaded (same check as 6.9.1):
    ```bash
    psql "$DATABASE_URL" -c "\dx ltree"
    ```
    Surface if missing.

---

## Step ID and intent

**Step 6.9.2** — single deliverable group:

- `require(module, resource, action, scope)` factory function
- `PermissionDeniedError` exception class
- `/me/permissions` endpoint (returns user's full permission set)
- `/me/can-do` endpoint (single-permission check)
- The broader query in `/me/permissions` (or a small new module 
  function) that returns `list[PermissionGrant]` for a user
- `me_router` registered in main.py
- `docs/endpoints/me.md` 8-section endpoint doc
- Integration tests for the new surface
- LOAD-BEARING test: gate denies before any Repo call fires
- CLAUDE.md and BUILD_PLAN.md updates
- Two new forward-notes in CLAUDE.md

### Scope in

- `require(module, resource, action, scope)` at 
  `src/admin_backend/auth/permissions.py` (or new neighbor file). 
  Factory that returns a FastAPI dependency.
- `PermissionDeniedError` at `src/admin_backend/errors.py`. Subclass 
  of ClientError. http_status=403, code="PERMISSION_DENIED".
- `me_router.py` at `src/admin_backend/routers/v1/me.py` with:
  - `GET /api/v1/me/permissions` → returns `{"permissions": [...]}`
  - `GET /api/v1/me/can-do?module=X&resource=Y&action=Z&scope=W&target_anchor=...` 
    → returns `{"allowed": bool, "reason_code": str}`
- Module-level function (e.g., `get_permissions_for_user`) at 
  `src/admin_backend/auth/permissions.py` that runs the broader 
  query against role assignments and returns `list[PermissionGrant]`. 
  Lives alongside `has_permission` and `require`; permissions.py is 
  the canonical home for permission-decision code.
- `docs/endpoints/me.md` covering both endpoints (8-section format).
- 18 integration tests at `tests/integration/test_me_router.py` 
  (6 MP, 7 MC, 4 GF, 1 XT).
- One LOAD-BEARING test for "gate denies before any Repo call fires" 
  (mirrors Step 6.8.3 R2's pattern).
- main.py: register `me_router`.
- **Smoke and endpoint test scripts updated for the 2 new endpoints**:
  - `scripts/smoke_curl.sh` — add 2 assertions (one per new endpoint)
  - `scripts/test_endpoints.sh` — add 2 cases (one per new endpoint)
  - `scripts/test_endpoints_cloud.sh` — add 2 cases (one per new endpoint)
- CLAUDE.md: Current state entry for 6.9.2. Two new forward-notes.
- BUILD_PLAN.md: Step 6.9.2 entry rewritten; status TODO → DONE.
- Prompt file bundled into the commit.
- `docs/endpoints/openapi.json` regenerated.

### Scope out

- Retrofitting existing endpoints with `Depends(require(...))` → 6.9.3.
- Per-resource anchor dependencies (`get_store_anchor`, etc.) → 6.9.3.
- Mandatory-gate-discipline meta-test → 6.9.3.
- `_require_platform_auth` retirement → 6.9.3 design.
- Audit log writes on denials → Step 6.16.
- Impersonation enforcement → deferred via existing forward-note.
- Granular ReasonCode expansion → deferred until Step 6.16.
- Architecture.md updates → wait until Section 6.9 completes (after 6.9.3).

### Acceptance criteria

- `require(...)` factory callable at the locked location; returns a 
  callable that FastAPI accepts as a Depends.
- `PermissionDeniedError` raisable; the existing FastAPI exception 
  handler at main.py:225 produces a proper 403 response with envelope 
  `{code: "PERMISSION_DENIED", message, details: null, request_id}`.
- `GET /api/v1/me/permissions` returns the caller's full permission 
  set with structured grants. Empty case returns `{"permissions": []}`.
- `GET /api/v1/me/can-do` returns the caller's single-permission 
  check result for the queried tuple + target_anchor.
- All 18 integration tests pass.
- LOAD-BEARING test: "gate denies before any Repo call fires" green. 
  This is a step-blocker.
- All previously-passing tests still pass. Per-router regression 
  checkpoint clean.
- mypy strict clean on every file touched.
- `scripts/check_setup.sh` 35/35.
- `scripts/smoke_test.py` PASS count unchanged from baseline.
- `scripts/smoke_curl.sh` PASS count grows by +2 (the new /me/* 
  endpoints).
- `scripts/test_endpoints.sh` includes 2 new cases for /me/* 
  endpoints; runs clean.
- `scripts/test_endpoints_cloud.sh` includes 2 new cases for /me/* 
  endpoints; cloud-side run is operator-driven and not part of this 
  step's verification, but the script changes ship in this commit.
- EXPLAIN ANALYZE captured for the /me/permissions broader query.
- Two forward-notes added with actual FN-AB numbers:
  - `_require_platform_auth` retirement decision (deferred to 6.9.3)
  - /me/permissions response shape simplification (revisit at 6.9.3 
    retrofit)
- BUILD_PLAN.md Step 6.9.2 entry rewritten and status flipped to DONE.
- `docs/endpoints/openapi.json` regenerated; new paths visible with 
  full metadata.
- `docs/endpoints/me.md` shipped (8-section format).

### Locked decisions (do not deviate)

These were resolved in the operator/Claude design conversation 
2026-05-13. Do NOT re-litigate.

1. **Gate architecture: pure `Depends(require(...))` factory.** 
   Not imperative call. Novel pattern in this codebase; FastAPI 
   well-documents the factory shape.

2. **Gate's place in dependency chain: shares request's session.** 
   The gate dependency declares `Depends(get_tenant_session_dep)` 
   and uses the same session as the handler.

3. **`_require_platform_auth` not touched.** Retirement deferred to 
   6.9.3 design conversation.

4. **`me_router` with `/me` prefix at `routers/v1/me.py`.** Single 
   APIRouter, two routes inside.

5. **`/me/permissions` response shape:** 
   ```
   {"permissions": [
     {"module": "ADMIN", "resource": "USERS", "action": "VIEW", 
      "scope": "TENANT", "anchor_path": "bucees"},
     ...
   ]}
   ```
   Always returns array. Empty case: `{"permissions": []}`. Pydantic 
   response model uses `PermissionGrant` field shape; serialization 
   produces the structured items.

6. **PermissionDeniedError in `errors.py`.** Subclass of ClientError. 
   `code = "PERMISSION_DENIED"`, `http_status = 403`. Structured 
   fields (module, resource, action, scope, target_anchor, 
   reason_code) attached via `**context` constructor kwargs.

7. **Response envelope `details=null`.** Permission-denial errors 
   do NOT populate the details field. Structured fields available in 
   `exc.context` for application logs.

8. **Per-endpoint anchor dependency pattern (FN-AB-25 resolved).** 
   6.9.2 ships the `require(...)` factory with `target_anchor=None` 
   hardcoded INTERNALLY — no retrofitted endpoints exist yet, so 
   target_anchor threading is not exercised. The per-resource anchor 
   dependencies (`get_store_anchor`, etc.) AND the mechanism that 
   threads target_anchor INTO the factory's internal has_permission 
   call both ship in 6.9.3. 6.9.3 design conversation revisits 
   threading mechanism (see Caution-first risk #2).

9. **LOAD-BEARING test: gate denies before Repo call.** Pattern: 
   `patch.object(repo_module._repo, "<method>", new=AsyncMock())` 
   + assert response is 403 + assert `call_count == 0`.

---

## Implementation outline

Sketches in this section follow the standing discipline. Refine per 
actual codebase conventions; surface deviations.

### File 1: `src/admin_backend/auth/permissions.py` — MODIFY

Add the `require(...)` factory alongside the existing has_permission().

Shape (refine):

```python
from typing import Callable, Awaitable

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

# Existing imports for AuthContext, ReasonCode, etc.


def require(
    module: ModuleCode,
    resource: PermissionResource,
    action: PermissionAction,
    scope: PermissionScope,
) -> Callable[..., Awaitable[None]]:
    """Factory: returns a FastAPI dependency that gates on this 
    permission tuple.
    
    Usage:
        @router.get("/some-endpoint")
        async def some_handler(
            _: None = Depends(require(MODULE, RESOURCE, ACTION, SCOPE)),
            target_anchor: str | None = Depends(get_some_anchor),  # 6.9.3
            ...
        ):
            ...
    
    The returned dependency:
    - Pulls AuthContext via Depends(get_auth_context)
    - Pulls session via Depends(get_tenant_session_dep)
    - Pulls target_anchor — handled by 6.9.3's per-endpoint anchor 
      dependencies; for 6.9.2, the gate accepts target_anchor=None 
      (used by /me/can-do which passes it explicitly)
    - Calls has_permission(...)
    - Raises PermissionDeniedError on denial; returns None on allow
    """
    async def gate(
        auth: AuthContext = Depends(get_auth_context),
        session: AsyncSession = Depends(get_tenant_session_dep),
        # target_anchor flows in via 6.9.3's per-endpoint anchor dep;
        # in 6.9.2, retrofitted endpoints don't exist yet, so this 
        # parameter is None by default. The /me/can-do endpoint 
        # calls has_permission directly with its own target_anchor 
        # from query params.
    ) -> None:
        allowed, reason_code, detail = await has_permission(
            session, auth, module, resource, action, scope, 
            target_anchor=None,  # 6.9.3 fills this in via anchor dep
        )
        if not allowed:
            raise PermissionDeniedError(
                detail,  # internal_message
                module=str(module),
                resource=str(resource),
                action=str(action),
                scope=str(scope),
                target_anchor=None,
                reason_code=str(reason_code),
            )
    return gate
```

**mypy strict typing for the factory.** The `Callable[..., Awaitable[None]]` 
return annotation may or may not pass mypy strict depending on the 
project's mypy config. If it doesn't, try in order:
1. `Callable[..., Coroutine[Any, Any, None]]` (more explicit)
2. Define a custom `Protocol` matching FastAPI's dependency callable shape
3. Surface if neither works — do NOT paper over with `# type: ignore`.

**Important refinement question for Claude Code**: how does target_anchor 
flow from the per-endpoint anchor dependency INTO the gate's `has_permission` 
call? FastAPI dependency resolution makes this tricky — the gate is a 
separate Depends from any anchor dep the endpoint declares. Two ways:

(a) Endpoints that need cascade pass target_anchor manually by calling 
has_permission inside the handler (defeats the declarative pattern for 
those endpoints — only acceptable for /me/can-do)

(b) The `require(...)` factory accepts an optional anchor-fetching 
function as a parameter: `require(MODULE, RESOURCE, ACTION, SCOPE, 
anchor_dep=get_store_anchor)`. The factory composes both deps into 
the inner gate function.

Pattern (b) is cleaner long-term but adds complexity to the factory. 
For 6.9.2, since no endpoint is retrofitted yet, the gate factory 
ships with `target_anchor=None` hardcoded. Surface this design call 
in your report — 6.9.3 design will revisit how target_anchor 
threading works for retrofitted endpoints.

### File 2: `src/admin_backend/errors.py` — MODIFY

Add `PermissionDeniedError` class:

```python
class PermissionDeniedError(ClientError):
    """Raised when the gate denies a request.
    
    Structured fields (module, resource, action, scope, target_anchor, 
    reason_code) carried via **context for application logs. The 
    response envelope's `details` field is null per D-31 / Q7 
    decision.
    """
    code = "PERMISSION_DENIED"
    http_status = 403
    public_message = "Permission denied"
```

Constructor kwargs (module, resource, etc.) flow via the existing 
`**context: Any` mechanism on AdminBackendError. No additional 
constructor needed.

### File 3: `src/admin_backend/routers/v1/me.py` — NEW

The me_router with two endpoints.

Shape (refine):

```python
"""Router for /me/* endpoints.

The caller's own permission context. Used by the frontend for UI 
gating decisions.

Auth: any authenticated user (PLATFORM or TENANT). The /me/* 
endpoints describe the caller's own state, so no permission gate 
applies. PUBLIC_PATHS allowlist (used by middleware) does NOT 
include /me/* — auth is still required.
"""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

# Imports for AuthContext, get_auth_context, get_tenant_session_dep,
# has_permission, PermissionGrant, ModuleCode, PermissionResource, etc.


router = APIRouter(prefix="/me", tags=["me"])


class PermissionGrantRead(BaseModel):
    """Pydantic response model for one grant.
    
    Mirrors PermissionGrant dataclass shape for JSON serialization. 
    String types for enums (StrEnum auto-serializes; explicit BaseModel 
    field ensures OpenAPI schema is correct).
    """
    module: str
    resource: str
    action: str
    scope: str
    anchor_path: str | None
    
    model_config = {"from_attributes": True}


class MePermissionsResponse(BaseModel):
    permissions: list[PermissionGrantRead]


class MeCanDoResponse(BaseModel):
    allowed: bool
    reason_code: str


@router.get(
    "/permissions",
    response_model=MePermissionsResponse,
    summary="Get caller's permission set",
    description=(
        "Returns the caller's full set of permission grants. Used by "
        "the frontend at login (or on refresh) to decide which UI "
        "elements to render. Always returns an array; empty if the "
        "caller has no grants. Server-side enforcement is still the "
        "security boundary; this endpoint is a UX hint."
    ),
)
async def get_me_permissions(
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> MePermissionsResponse:
    grants = await get_permissions_for_user(session, auth)
    return MePermissionsResponse(
        permissions=[PermissionGrantRead.model_validate(g) for g in grants]
    )


@router.get(
    "/can-do",
    response_model=MeCanDoResponse,
    summary="Check a single permission",
    description=(
        "Server-authoritative single-permission check. Pass the "
        "permission tuple via query parameters; optionally pass "
        "target_anchor for cascade-aware checks. Returns "
        "{allowed: bool, reason_code: str}. Frontend uses this for "
        "pre-flight checks before high-stakes actions."
    ),
)
async def get_me_can_do(
    module: Annotated[ModuleCode, Query(description="Permission module")],
    resource: Annotated[PermissionResource, Query(description="Permission resource")],
    action: Annotated[PermissionAction, Query(description="Permission action")],
    scope: Annotated[PermissionScope, Query(description="Permission scope")],
    target_anchor: Annotated[str | None, Query(description="Optional ltree path of the target")] = None,
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> MeCanDoResponse:
    allowed, reason_code, _ = await has_permission(
        session, auth, module, resource, action, scope, target_anchor
    )
    return MeCanDoResponse(
        allowed=allowed,
        reason_code=str(reason_code),
    )
```

### File 4: The broader query — get_permissions_for_user

Lives in `src/admin_backend/auth/permissions.py` alongside 
`has_permission` and `require`. Rationale: `permissions.py` is the 
canonical home for permission-decision code in v0. Three functions 
in one file is fine; future RBAC additions also land here unless 
they're a different category (e.g., audit log writes go elsewhere).

Shape (refine):

```python
async def get_permissions_for_user(
    session: AsyncSession,
    auth: AuthContext,
) -> list[PermissionGrant]:
    """Return the caller's full permission set.
    
    For PLATFORM users: all grants from platform_user_role_assignments.
    For TENANT users: all grants from tenant_user_role_assignments, 
    filtered by tenant_module_access status=ENABLED.
    
    Used by /me/permissions. Not on the gate hot path (gate uses 
    has_permission() targeted query).
    """
    if auth.user_type == ...:  # match the codebase's user_type comparison
        # PLATFORM query
        rows = await session.execute(text("""
            SELECT p.module, p.resource, p.action, p.scope, NULL AS anchor_path
            FROM {schema}.platform_user_role_assignments pura
            JOIN {schema}.role_permissions rp ON rp.role_id = pura.role_id
            JOIN {schema}.permissions p ON p.id = rp.permission_id
            WHERE pura.platform_user_id = :user_id
              AND pura.status = 'ACTIVE'
        """), {"user_id": auth.user_id})
    else:
        # TENANT query — mirror has_permission's TENANT path SQL 
        # JOIN structure exactly. Differences:
        #   - No per-tuple filter in WHERE (returns all grants)
        #   - SELECT includes p.module, p.resource, p.action, p.scope, 
        #     on_.path AS anchor_path
        #   - Same JOINs: tenant_user_role_assignments → role_permissions
        #     → permissions → org_nodes (composite key per D-34) →
        #     tenant_module_access (status='ENABLED')
        #   - WHERE tura.tenant_user_id = :user_id AND tura.status = 'ACTIVE'
        #     AND tma.status = 'ENABLED'
        # Read src/admin_backend/auth/permissions.py at HEAD and 
        # derive the structure from the shipped TENANT query, not 
        # this sketch.
        ...
    
    return [
        PermissionGrant(
            module=row.module,
            resource=row.resource,
            action=row.action,
            scope=row.scope,
            anchor_path=row.anchor_path,
        )
        for row in rows
    ]
```

**Note on SQL duplication.** This broader query shares ~80% of its 
structure with has_permission's per-tuple SQL. Two reasonable shapes: 
(a) keep them as separate methods with a sync comment, (b) extract a 
shared private helper that builds the common portion. Pick based on 
what reads cleanly. Surface your choice.

### File 5: `src/admin_backend/main.py` — MODIFY

Add the me_router include:

```python
from admin_backend.routers.v1 import me as me_router

# In the includes block:
app.include_router(me_router.router, prefix=settings.api_prefix)
```

Verify the existing include block's style; match it.

### File 6: `docs/endpoints/me.md` — NEW

8-section format per CLAUDE.md "Per-endpoint documentation" 
convention. Single file covering both /me/permissions and 
/me/can-do. Mirror `tenants.md`'s structure.

Sections per endpoint:
1. Endpoint summary
2. Request
3. Response 200
4. Response codes
5. Behaviour notes
6. Example calls
7. Sample integration code
8. Implementation reference

Behaviour notes section should document:
- "/me/permissions returns the full set; always an array."
- "Frontend should call at login/refresh; treat the response as a 
  cached UI gating snapshot."
- "Server enforces permissions; this endpoint is a UX hint."
- "/me/can-do supports cascade-aware checks via target_anchor."
- "When to use which: /me/permissions for initial-page-load UI 
  gating decisions (cache client-side). /me/can-do for 
  server-authoritative pre-flight checks before destructive 
  actions where cascade-aware verification matters (e.g., 'can I 
  delete this store?')."

### File 7: `tests/integration/test_me_router.py` — NEW

Integration tests against seeded Postgres. Test count: 18 tests, 
broken down as:
- 6 /me/permissions (T_MP1-T_MP6)
- 7 /me/can-do (T_MC1-T_MC7)
- 4 gate factory (T_GF1-T_GF4)
- 1 cross-tenant safety (T_XT1)

Post-step expected pytest baseline: 276 (pre) + 18 (new) = 294. 
Adjust counts if you genuinely need more or fewer; surface deviations 
of more than +/- 2 with reasoning.

**Before writing tests**, verify the seed data supports each scenario. 
Same discipline as Step 6.9.1: if a scenario can't be exercised 
against seed data unchanged (e.g., a user with zero permissions), the 
test must mutate state inside the test with rollback, OR use existing 
JWT/auth fixtures to construct the scenario without seeding.

Categories:

**/me/permissions:**
- `T_MP1` — PLATFORM user gets non-empty permission set
- `T_MP2` — TENANT user gets non-empty permission set scoped to their 
  tenant's enabled modules
- `T_MP3` — User with no role assignments gets empty array
- `T_MP4` — TENANT user with grant in suspended module does NOT see 
  that module's grants in response
- `T_MP5` — Response shape matches the PermissionGrant structure 
  (module/resource/action/scope/anchor_path)
- `T_MP6` — No auth → 401

**/me/can-do:**
- `T_MC1` — User with the permission gets `{allowed: true, ...}`
- `T_MC2` — User without the permission gets `{allowed: false, ...}`
- `T_MC3` — Cascade-aware: TENANT user with region grant, target_anchor 
  under that region → allowed
- `T_MC4` — Cascade-aware: TENANT user with region grant, target_anchor 
  outside that region → denied
- `T_MC5` — Missing required query param → 422
- `T_MC6` — Invalid module enum value → 422
- `T_MC7` — No auth → 401

**Gate factory tests:**
- `T_GF1` — `require(...)` returns a callable that FastAPI accepts 
  as Depends. Use a test-only endpoint declared inside the test 
  module.
- `T_GF2` — Gate denies → 403 with code="PERMISSION_DENIED", 
  details=null
- `T_GF3` — Gate allows → handler body runs normally
- `T_GF4` — **LOAD-BEARING**. Gate denies → handler body's Repo call 
  is NEVER fired. Use `patch.object(some_repo, "some_method", 
  new=AsyncMock())` + assert `call_count == 0`. Mirrors Step 6.8.3 R2.
  
  **Important:** the test endpoint must actually perform a Repo 
  call in its body for the assertion to be meaningful. The plain 
  `return {"reached_handler": True}` shape doesn't touch a Repo; 
  `mock.call_count == 0` would be vacuously true. For T_GF4 
  specifically, either (a) extend the test endpoint to call a Repo 
  method (e.g., `await tenants_repo.list(session, ...)`) and patch 
  that, or (b) reuse an existing real router endpoint and gate it 
  for the test. Surface your approach.

**Cross-tenant safety:**
- `T_XT1` — Tenant A user calling /me/can-do with target_anchor under 
  Tenant B → denied (target_anchor not under user's tenant)

Test fixtures: reuse `app_client`, `_platform_jwt`, `_tenant_jwt`, 
`make_tenant`, `make_tenant_user`, etc. from existing conftest.

For T_GF1-T_GF4, create a test-only router with a single gated 
endpoint inside the test module:

```python
test_router = APIRouter(prefix="/test-gate", tags=["test"])

@test_router.get("/protected")
async def _test_protected(
    _: None = Depends(require(
        ModuleCode.ADMIN, PermissionResource.USERS,
        PermissionAction.VIEW, PermissionScope.GLOBAL,
    )),
):
    return {"reached_handler": True}
```

**Why GLOBAL scope here:** the require() factory in 6.9.2 hardcodes 
`target_anchor=None` internally. GLOBAL-scoped permissions are 
semantically correct with target_anchor=None (they apply globally, 
no specific target). TENANT or STORE scope would require target_anchor 
threading, which is 6.9.3 territory; using them here would force 
testing of unimplemented mechanism.

Mount this on the test app in a fixture. Use it as the target for 
T_GF1-T_GF4.

### File 8: `CLAUDE.md` — MODIFY

**Add to "Current state — Completed":**

```
- Section 6.9.2 — gate factory + PermissionDeniedError + /me/* 
  endpoints. require(MODULE, RESOURCE, ACTION, SCOPE) factory at 
  src/admin_backend/auth/permissions.py (FastAPI Depends-injectable; 
  novel pattern in v0, see "Note on dependency factories" below). 
  PermissionDeniedError at errors.py (shared, system-wide; 403; 
  code=PERMISSION_DENIED; details=null per Q7 design). me_router at 
  routers/v1/me.py with /me/permissions (returns {permissions: 
  [PermissionGrant, ...]}; full set, always array) and /me/can-do 
  (single-permission check). docs/endpoints/me.md shipped (8-section 
  format). N integration tests at tests/integration/test_me_router.py, 
  4 LOAD-BEARING (T_GF1-T_GF4 covering factory and gate-denies-before-
  Repo-call). N+13 = <X> total pytest passes. mypy strict clean.
```

**Add a new "Note on dependency factories" subsection** under 
existing v0 auth-model notes:

```
### Note on dependency factories (Step 6.9.2)

The require(MODULE, RESOURCE, ACTION, SCOPE) factory establishes a 
new pattern in this codebase. Functions that return Depends-injectable 
callables — `def make_dep(arg) -> Callable: ...` — are FastAPI's 
documented way to parameterize dependencies. Step 6.9.2 introduces 
this pattern; future steps reuse it for similar parameterized 
dependencies (e.g., the per-resource anchor dependencies in Step 
6.9.3).
```

**Add two forward-notes** in the Forward-notes section:

```
### FN-AB-NN — _require_platform_auth retirement decision

The existing _require_platform_auth helper in 
routers/v1/platform_users.py (the binary user_type=PLATFORM check) 
predates the require(...) gate factory. Decision deferred to Step 
6.9.3 design conversation: replace it with Depends(require(ADMIN, 
USERS, VIEW, GLOBAL)) or keep as cheap user-type-only fast path. 
The rationale for the original helper (platform_users table has no 
RLS; app-layer auth must enforce) is in Step 5.1's "v0 auth model" 
prompt section.
```

```
### FN-AB-NN — /me/permissions response shape simplification

Current shape returns structured PermissionGrant items 
({module, resource, action, scope, anchor_path}). If frontend 
integration during 6.9.3 retrofit shows the structured shape 
requires significant client-side logic, consider simplifying to 
pre-joined string codes ({"permissions": ["ADMIN.USERS.VIEW.TENANT", 
...]}) and dropping anchor_path. Revisit during 6.9.3 retrofit 
when real frontend integration surfaces concrete needs.
```

(Assign FN-AB numbers based on next available slot.)

### File 9: `BUILD_PLAN.md` — MODIFY

Step 6.9.2 entry rewrite (status TODO → DONE). Match the structure 
of the 6.9.1 entry that landed at commit 63dd565.

### File 10: `prompts/step-6_9_2-gate-and-me-endpoints-2026-05-13.md`

This prompt file. Bundle into the commit per per-step convention.

### File 11: `docs/endpoints/openapi.json` — REGENERATE

After all code is in and tests pass.

**Check if an OpenAPI sync script exists.** Run:
```bash
ls scripts/ | grep -i openapi
```

If `scripts/sync_openapi.sh` (or similar) exists, use it — it handles 
the boot/curl/save sequence. If no script exists, use the manual 
pattern below and surface that no script was found:

```bash
# Manual fallback — boot the app, hit /openapi.json, save the regenerated spec
curl -s http://localhost:8000/api/v1/openapi.json | jq '.' > docs/endpoints/openapi.json

# Verify the new paths
jq '.paths."/api/v1/me/permissions"' docs/endpoints/openapi.json
jq '.paths."/api/v1/me/can-do"' docs/endpoints/openapi.json
```

Both should have summary, description, response schema, parameter 
descriptions.

### File 12: Smoke and endpoint test scripts — MODIFY

**Convention:** when new endpoints land, three scripts in `scripts/` 
must be updated. Failure to update them creates drift between the 
shipped endpoints and the smoke/endpoint test harness, which is 
caught only later when someone runs them and discovers missing cases.

Update all three:

**`scripts/smoke_curl.sh`** — adds 2 new assertions (one per new 
endpoint). Read the existing structure (a sequence of `curl` calls 
each asserting expected status + content). Pattern (refine to match 
actual style):

```bash
# /me/permissions — PLATFORM JWT, expect 200 + non-empty array
assert_curl_get "$BASE/api/v1/me/permissions" 200 \
  --header "Authorization: Bearer $PLATFORM_JWT" \
  --jq-check '.permissions | length > 0'

# /me/can-do — PLATFORM JWT, expect 200 + {allowed: true/false}
assert_curl_get "$BASE/api/v1/me/can-do?module=ADMIN&resource=USERS&action=VIEW&scope=GLOBAL" 200 \
  --header "Authorization: Bearer $PLATFORM_JWT" \
  --jq-check '.allowed != null and .reason_code != null'
```

After this step ships, total smoke_curl.sh PASS count = current + 2.

**`scripts/test_endpoints.sh`** — adds 2 cases (one per new endpoint). 
This script runs against the local backend during dev. Read the 
existing structure; the cases for `/me/permissions` and `/me/can-do` 
follow the same pattern as existing per-endpoint cases.

**`scripts/test_endpoints_cloud.sh`** — adds 2 cases for the cloud 
backend. Same shape as test_endpoints.sh but targets the deployed 
service URL. The script changes ship in this commit; the actual 
cloud run is operator-driven (when the next deploy happens).

**If you cannot find one or more of these scripts**, surface 
immediately. The scripts exist at HEAD per the project's established 
workflow.

**For each script: add the case(s), verify the script still parses 
cleanly (e.g., `bash -n scripts/smoke_curl.sh`), and (for local 
scripts) verify the run is clean against a local backend.**

---

## Caution-first risks the prompt explicitly guards against

1. **Raising PermissionDeniedError inside Depends.** Verified by 
   F-DEPEND-4: precedent exists (`get_auth_context` raises 
   `AuthMissingError`). The FastAPI exception handler at 
   `main.py:225` catches it. Test T_GF2 verifies the 403 response 
   shape end-to-end.

2. **target_anchor threading from per-endpoint anchor dep into the 
   gate.** 6.9.2 ships the gate factory hardcoded to 
   `target_anchor=None`. The actual threading happens in 6.9.3 via 
   the anchor-dep design. If you find yourself wanting to thread 
   target_anchor through the factory now, STOP and surface — that's 
   6.9.3 territory.

3. **SQL duplication between has_permission and get_permissions_for_user.** 
   Real but bounded. Pick separate methods or shared helper based on 
   what reads cleanly. Surface your choice.

4. **Pydantic v2 enum serialization.** PermissionGrant uses 
   StrEnum-based fields (ModuleCode, PermissionResource, etc.). 
   Verify `PermissionGrantRead.model_validate(PermissionGrant_instance)` 
   produces clean string values for the response, not enum reprs. 
   If serialization mis-fires, surface — may need explicit 
   `field_validator` or `field_serializer` on the response model.

5. **module_access_status_enum** has only ENABLED/DISABLED in live 
   schema (per 6.9.1's pre-flight finding). The /me/permissions 
   TENANT query filters `tma.status = 'ENABLED'`. Any non-ENABLED 
   value correctly excludes grants.

6. **ModuleCode Python enum has 5 values post-ROOS retirement.** 
   The Python enum at HEAD (post-commit 9462e11) does NOT include 
   ROOS, though the DB enum still does. For T_MC6 (invalid module 
   enum → 422), use a clearly nonsense value like `"NOT_A_MODULE"`, 
   not `"ROOS"` — the latter would fail at FastAPI's Python-enum 
   validation level but for the wrong reason.

7. **Test fixtures for empty-permissions case.** T_MP3 needs a user 
   with zero permissions. Seed data may not have one; surface if 
   the seed-based approach doesn't work and a test-time fixture is 
   needed.

8. **Test-only gated endpoint mounting for T_GF1-T_GF4.** Mount the 
   test router on the FastAPI test app via the existing conftest 
   pattern. Surface if the conftest doesn't support adding extra 
   routers to the test app.

---

## Testing and regression discipline

### New tests

18 integration tests at `tests/integration/test_me_router.py`. 
4 are LOAD-BEARING:

- `T_GF1` — `require(...)` factory produces a working Depends 
  callable
- `T_GF2` — Gate denies → 403 with `code="PERMISSION_DENIED"`, 
  `details=null`
- `T_GF3` — Gate allows → handler body runs
- `T_GF4` — Gate denies → handler-body Repo call is NEVER fired 
  (mirrors Step 6.8.3 R2)

The remaining tests are standard correctness checks. All must pass.

### Tests deliberately not added

- **Unit tests on `require(...)` factory in isolation.** The factory's 
  output is a FastAPI dependency; testing it without FastAPI's runtime 
  is meaningless. Integration tests via the test-mounted router are 
  the only useful verification.
- **Performance/load tests on /me/permissions.** v0 scale. EXPLAIN 
  ANALYZE captures the broader query's plan; that's sufficient.
- **PermissionDeniedError unit tests.** It's a ClientError subclass 
  with class-attribute overrides. Testing the inheritance machinery 
  tests Python, not Ithina code.
- **PermissionGrantRead model_validate exhaustive coverage.** Pydantic 
  v2 validation is framework code; if model_config is correct, it 
  works.
- **OpenAPI schema validation tests.** Human-verified via the curl 
  inspection in the verification harness; no programmatic snapshot.

### Regression risk surface

1. **Existing 276 tests must stay green.** Particularly auth-related 
   tests (test_auth_middleware.py, test_platform_users_router.py 
   A2 test). 6.9.2 adds new code but doesn't modify auth middleware, 
   the exception handler, or existing routers. Any drop is a 
   step-blocker.

2. **Mypy strict on touched files.** New types (PermissionGrantRead, 
   MePermissionsResponse, MeCanDoResponse, PermissionDeniedError) 
   must pass strict checks. The require(...) factory's typing 
   (returning a Callable that returns a coroutine) is non-trivial; 
   may need `Callable[..., Awaitable[None]]` or similar.

3. **Smoke and endpoint test script drift.** Three scripts in 
   `scripts/` must be updated whenever new endpoints ship: 
   `smoke_curl.sh`, `test_endpoints.sh`, `test_endpoints_cloud.sh`. 
   Forgetting any one creates drift between shipped endpoints and 
   the test harness. The smoke_curl.sh PASS count check at +2 is 
   the canary; the other two scripts ship the updates but their 
   runs are out of step verification (local for test_endpoints.sh, 
   cloud-driven for test_endpoints_cloud.sh).

4. **OpenAPI spec regeneration.** Forgetting to regenerate produces 
   a docs-vs-reality drift. The verification harness explicitly 
   includes the curl-based regen step.

5. **mypy on the require(...) return type.** FastAPI's Depends 
   accepts any callable; mypy strict may complain about the 
   factory's return type if not annotated precisely. Surface if 
   mypy reports issues that aren't trivially fixable.

---

## Verification harness

Run in order. All must be green before reporting.

```bash
# 0. Pre-verification reseed.
uv run python -m scripts.seed_dev_data --reset

# 0a. Confirm seed counts (sanity check; baseline at HEAD).
psql "$DATABASE_URL" -c "
SET search_path TO core, public;
SELECT
  (SELECT COUNT(*) FROM tenant_users) AS tu,
  (SELECT COUNT(*) FROM platform_users) AS pu,
  (SELECT COUNT(*) FROM tenant_user_role_assignments) AS tura,
  (SELECT COUNT(*) FROM platform_user_role_assignments) AS pura,
  (SELECT COUNT(*) FROM tenant_module_access WHERE status='ENABLED') AS tma_enabled,
  (SELECT COUNT(*) FROM tenant_module_access WHERE module = 'ROOS') AS roos_count;
"
# Expected (approximate; confirm against CLAUDE.md Current State at 
# HEAD): tu=17, pu=3, tura=19, pura=3, tma_enabled≈23, roos_count=0.

# 1. Type checking.
uv run mypy src/admin_backend/

# 2. Pytest, all tests.
uv run pytest --tb=no -q

# 2a. Per-router regression checkpoint. Extract baseline counts from 
# CLAUDE.md Current State during pre-flight; the post-step counts 
# must match. Surface any drop immediately.

# 3. Smoke test.
uv run python -m scripts.smoke_test

# 3a. Smoke curl + local endpoint tests (require a running local backend).
# Boot the app first if not already running:
#   uv run uvicorn src.admin_backend.main:app --reload
# Then in another shell:
bash scripts/smoke_curl.sh
# Expected: PASS count = current_baseline + 2 (the 2 new /me/* assertions)

bash scripts/test_endpoints.sh
# Expected: clean run; the 2 new /me/* cases included

# scripts/test_endpoints_cloud.sh runs cloud-side; not part of this 
# step's verification. Confirm the file was edited (the 2 new cases 
# present) but don't execute it locally.

# 4. Alembic round-trip (no migration in this step).
uv run alembic heads
uv run alembic check

# 5. Targeted /me/* tests.
uv run pytest tests/integration/test_me_router.py -v

# 6. Confirm new files are importable cleanly.
uv run python -c "
from src.admin_backend.auth.permissions import require, has_permission
from src.admin_backend.errors import PermissionDeniedError
from src.admin_backend.routers.v1 import me as me_router
print('OK')
"

# 7. Boot the app and regenerate OpenAPI spec.
# In one terminal:
#   uv run uvicorn src.admin_backend.main:app --reload
# In another:
curl -s http://localhost:8000/api/v1/openapi.json | jq '.' > docs/endpoints/openapi.json
jq '.paths."/api/v1/me/permissions"' docs/endpoints/openapi.json
jq '.paths."/api/v1/me/can-do"' docs/endpoints/openapi.json

# 8. EXPLAIN ANALYZE the /me/permissions query.
# Pick a representative user from seed (e.g., Devon, Anjali); run 
# the query against seeded Postgres. Include the output in your report.
```

---

## Report (BEFORE proposing commit)

1. Pre-flight outputs (items 1-21 explicit results).
2. Resolution of design choices made during implementation:
   - File location for require() factory (in permissions.py or 
     separate file)
   - File location for get_permissions_for_user
   - SQL duplication strategy (separate methods or shared helper)
   - Any other location/structure choice
3. Diffs:
   - Modified: `src/admin_backend/auth/permissions.py` (require() 
     factory + get_permissions_for_user added)
   - Modified: `src/admin_backend/errors.py` (PermissionDeniedError 
     added)
   - New: `src/admin_backend/routers/v1/me.py`
   - Modified: `src/admin_backend/main.py` (me_router include)
   - New: `tests/integration/test_me_router.py`
   - New: `docs/endpoints/me.md`
   - Modified: `docs/endpoints/openapi.json` (regenerated)
   - Modified: `CLAUDE.md` (2 forward-notes added; Current state 
     entry appended)
   - Modified: `BUILD_PLAN.md` (Step 6.9.2 entry rewritten)
   - New: `prompts/step-6_9_2-gate-and-me-endpoints-2026-05-13.md`
   - Modified: `scripts/smoke_curl.sh` (+2 assertions)
   - Modified: `scripts/test_endpoints.sh` (+2 cases)
   - Modified: `scripts/test_endpoints_cloud.sh` (+2 cases)
4. Verification harness output for all sections (0, 0a, 1-8).
5. Pre/post pytest counts. State explicit numbers.
6. Per-test summary: which /me/* tests passed, mapping to T_MP1-T_MP6, 
   T_MC1-T_MC7, T_GF1-T_GF4, T_XT1.
7. EXPLAIN ANALYZE output for the /me/permissions broader query.
8. Any deviation from the locked design decisions (should be none; 
   surface immediately if any).
9. Forward-notes: list the 2 forward-notes added with the actual 
   FN-AB numbers used.

Wait for explicit operator authorisation before staging or committing.

---

## Surface-and-stop scenarios

Stop and report (do not work around silently) if:

1. Pytest baseline is not 276 passes at pre-flight.
2. ltree extension not available in local Postgres.
3. PermissionGrant or ReasonCode have changed from what 6.9.1 shipped.
4. The FastAPI exception handler at main.py:225 doesn't match 
   investigation F-ERR-3's description.
5. `Depends(get_auth_context)` doesn't raise AuthMissingError on 
   missing auth (the precedent for raising-inside-Depends).
6. Mounting a test-only router on the test app fails or the conftest 
   doesn't support it.
7. Pydantic v2 enum serialization on the response model produces 
   non-string values.
8. mypy strict surfaces errors on the require() factory's return type 
   that aren't trivially fixable.
9. The seed data doesn't include a user with zero permissions (needed 
   for T_MP3).
10. During implementation, if `has_permission` and 
    `get_permissions_for_user` diverge in non-trivial structural 
    ways (different JOIN orders, different filter columns, different 
    audience dispatch from what 6.9.1 ships) — surface. The design 
    expects them to share the same JOIN structure with only the 
    WHERE clauses and projection differing.

11. Any of the three scripts (`scripts/smoke_curl.sh`, 
    `scripts/test_endpoints.sh`, `scripts/test_endpoints_cloud.sh`) 
    is missing at HEAD. The prompt assumes all three exist per the 
    project's established workflow; surface if any is absent.

If any item above triggers, stop and ask the operator.

---

## After completing

Propose a git commit per CLAUDE.md "After completing a task" Pattern A:

```bash
git status
git add -A
git commit -m "Step 6.9.2: gate factory + PermissionDeniedError + /me/* endpoints

- New: src/admin_backend/auth/permissions.py (require() factory + 
  get_permissions_for_user added). require(MODULE, RESOURCE, ACTION, 
  SCOPE) returns a FastAPI dependency that calls has_permission and 
  raises PermissionDeniedError on denial. Novel pattern in v0 (no 
  dependency-factory precedent before this step); FastAPI well-
  documented.
- New: src/admin_backend/errors.py — PermissionDeniedError class. 
  Subclass of ClientError; http_status=403; code='PERMISSION_DENIED'. 
  Structured fields (module, resource, action, scope, target_anchor, 
  reason_code) carried via **context kwargs. Response envelope 
  details=null per Q7 design.
- New: src/admin_backend/routers/v1/me.py. Single APIRouter with 
  prefix='/me'. Two routes: GET /me/permissions returns 
  {permissions: [PermissionGrant, ...]} (always array, empty if no 
  grants); GET /me/can-do?module=X&resource=Y&action=Z&scope=W&
  target_anchor=... returns {allowed: bool, reason_code: str}.
- New: docs/endpoints/me.md (8-section format covering both 
  endpoints).
- New: tests/integration/test_me_router.py. <N> integration tests. 
  4 LOAD-BEARING: T_GF1-T_GF4 (factory mechanics, denial response 
  shape, gate-denies-before-Repo-call invariant).
- Modified: src/admin_backend/main.py — me_router registered.
- Modified: docs/endpoints/openapi.json — regenerated; /me/* paths 
  present with full metadata.
- CLAUDE.md: Current state entry for 6.9.2. New 'Note on dependency 
  factories' subsection. Two new forward-notes (FN-AB-<N>: 
  _require_platform_auth retirement deferred to 6.9.3 design; 
  FN-AB-<N>: /me/permissions response shape simplification revisit 
  at 6.9.3 retrofit).
- BUILD_PLAN.md: Section 6.9 status '6.9.1 DONE; 6.9.2 DONE; 6.9.3 
  TODO'. Step 6.9.2 entry rewritten.
- prompts/step-6_9_2-gate-and-me-endpoints-2026-05-13.md bundled.
- scripts/smoke_curl.sh: +2 assertions for /me/permissions and 
  /me/can-do.
- scripts/test_endpoints.sh: +2 cases for new endpoints.
- scripts/test_endpoints_cloud.sh: +2 cases for new endpoints 
  (operator runs cloud-side at next deploy).
- target_anchor in require() factory hardcoded to None for 6.9.2; 
  per-endpoint anchor dep threading lands in 6.9.3 retrofit.
- No new endpoints touched by existing routers; 6.9.3 retrofits.
- No architecture.md change (Section 6.9 update lands when 6.9.3 
  ships).
- No migrations. No DDL changes. No seed Excel changes.
- pytest <BASELINE> → <BASELINE + N>. mypy strict clean. 
  check_setup 35/35. smoke_test unchanged. smoke_curl.sh +2.

Unblocks Step 6.9.3 (retrofit existing endpoints with require() and 
per-resource anchor deps; mandatory-gate-discipline meta-test)."
```

Substitute actual counts (`<N>`, `<BASELINE>`) and final file location 
choices. Ask operator: "Run? yes / no / edit message".

---

## Coordination

- **Unblocks Step 6.9.3.** The retrofit needs the require() factory 
  (this step) plus per-resource anchor dependencies (6.9.3 ships 
  those). Step 6.9.3 starts after 6.9.2 commits.
- **No deploy required.** 6.9.2 ships the gate factory but no existing 
  production endpoint uses it yet. Next deploy bundles 6.9.1 + 6.9.2 
  + 6.9.3 together when Section 6.9 fully completes.
- **Frontend coordination after commit lands.** Once 6.9.2 ships, 
  operator coordinates with Amit (frontend) to:
  - Confirm /me/permissions and /me/can-do response shapes match 
    expectations
  - Integrate /me/permissions into the frontend's session-init flow
  - Surface any simplification needs (caught by FN-AB-NN forward-note 
    for 6.9.3 retrofit revisit)

---

## End of prompt
