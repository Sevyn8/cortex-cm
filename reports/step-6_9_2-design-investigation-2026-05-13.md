# Step 6.9.2 — design-prep investigation findings

Date: 2026-05-13
HEAD: 63dd565 ("Step 6.9.1: has_permission() core + PermissionGrant + ReasonCode")
Scope: read-only investigation feeding the Step 6.9.2 design conversation. No edits, no code changes. Pytest invocations limited to the two F-VERIFY runs.

Findings are grouped by area code (VERIFY, GATE, DEPEND, ERR, TEST, ROUTER). A consolidated "Open questions for design conversation" section closes the document.

One prompt assumption surfaced as wrong and is recorded in the relevant finding rather than worked around silently: F-GATE-2 (`_audience_filter_for` location).

---

## VERIFY — 6.9.1 shipped shape

### F-VERIFY-1: `has_permission()` signature matches design intent

**Question:** Did 6.9.1 ship `has_permission()` with the signature the design conversation locked?

**Citation:** `src/admin_backend/auth/permissions.py:44-52`

**Current code:**

```python
async def has_permission(
    session: AsyncSession,
    auth: AuthContext,
    module: ModuleCode,
    resource: PermissionResource,
    action: PermissionAction,
    scope: PermissionScope,
    target_anchor: str | None = None,
) -> tuple[bool, ReasonCode, str]:
```

**Observation:** Exact match. `target_anchor` defaults to `None`. Return is `tuple[bool, ReasonCode, str]`. 6.9.2's `require(...)` factory calls this signature directly; the `/me/can-do` endpoint also calls this signature directly. Caller never has to construct partial keyword sets — every call passes all four enum values positionally or by keyword.

**Confidence:** high.

**Open question:** None.

---

### F-VERIFY-2: `PermissionGrant` is frozen dataclass with 5 fields

**Question:** Did the dataclass ship as a frozen dataclass with exactly five fields (`module`, `resource`, `action`, `scope`, `anchor_path`)?

**Citation:** `src/admin_backend/auth/permission_grant.py:23-31`

**Current code:**

```python
@dataclass(frozen=True)
class PermissionGrant:
    """One permission held by one user at one anchor."""

    module: ModuleCode
    resource: PermissionResource
    action: PermissionAction
    scope: PermissionScope
    anchor_path: str | None
```

**Observation:** Exact match. `frozen=True` makes instances hashable, supports `set[PermissionGrant]` representation if a future caller wants to deduplicate. `anchor_path` is `str | None` (None for PLATFORM grants). The dataclass is not exported via any `__init__.py`; importers reach for `admin_backend.auth.permission_grant.PermissionGrant` directly.

**Confidence:** high.

**Open question:** Whether `/me/permissions` returns `list[PermissionGrant]` directly (serialised via FastAPI's default-encoder hooks) or whether 6.9.2 ships a parallel Pydantic schema (e.g., `PermissionGrantRead`) for explicit response-model wiring. Belongs to the response-shape design conversation.

---

### F-VERIFY-3: `ReasonCode` is `StrEnum` with two v0 values

**Question:** Did `ReasonCode` ship with the binary vocabulary the design conversation locked?

**Citation:** `src/admin_backend/auth/reason_code.py:14-18`

**Current code:**

```python
class ReasonCode(StrEnum):
    """Permission decision reason codes."""

    GRANT_MATCHED = "GRANT_MATCHED"
    NO_MATCHING_GRANT_OR_OUT_OF_SCOPE = "NO_MATCHING_GRANT_OR_OUT_OF_SCOPE"
```

**Observation:** Exact match. `StrEnum` means each value is a `str` subclass — comparisons against bare strings work (`code is ReasonCode.GRANT_MATCHED` and `code == "GRANT_MATCHED"` both succeed). The new `PermissionDeniedError` will likely carry a `reason_code: ReasonCode` field; JSON serialisation produces the enum's string value directly.

**Confidence:** high.

**Open question:** None at 6.9.2 design time; granular codes (cascade vs module-suspended vs no-match) are explicitly deferred until Step 6.16 audit log writes per the design lock.

---

### F-VERIFY-4: all 13 `has_permission` tests pass at HEAD

**Question:** Does the post-6.9.1 has_permission test suite stay green?

**Verification command:** `uv run pytest tests/integration/test_has_permission.py --tb=no -q`

**Result:** `13 passed, 1 warning in 1.98s` (the warning is the pre-existing python-json-logger deprecation, unrelated).

**Observation:** Baseline confirmed. 6.9.2 wires `has_permission()` into FastAPI without modifying the function itself; these 13 tests are the regression baseline for unchanged-resolver behaviour.

**Confidence:** high.

**Open question:** None.

---

### F-VERIFY-5: total pytest pass count is 276

**Question:** Does the post-6.9.1 full-suite baseline still hold?

**Verification command:** `uv run pytest --tb=no -q`

**Result:** `276 passed, 1 warning in 42.12s`.

**Observation:** Matches CLAUDE.md's Current State entry for Step 6.9.1 (263 prior + 13 new = 276). 6.9.2's regression checkpoint will be against 276.

**Confidence:** high.

**Open question:** None.

---

## GATE — Existing per-endpoint gate patterns

### F-GATE-1: `_require_platform_auth(auth)` is a direct-call helper at handler-top, not a Depends-injected gate

**Question:** What is the shape of the only existing v0 router-layer auth-tier gate? Can the new resolver-driven gate replace it, or must it coexist?

**Citation:** `src/admin_backend/routers/v1/platform_users.py:102-109` (helper definition); `:216, :258` (call sites).

**Current code (definition):**

```python
def _require_platform_auth(auth: AuthContext) -> None:
    """Raise ``PlatformAccessRequiredError`` if the caller isn't PLATFORM."""
    if auth.user_type != "PLATFORM":
        raise PlatformAccessRequiredError(
            f"Endpoint requires PLATFORM user_type; got {auth.user_type}",
            user_id=str(auth.user_id),
            user_type=auth.user_type,
        )
```

**Current code (call sites — every PLATFORM-only handler invokes it as the first line of the body):**

```python
async def list_platform_users(
    ...
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> Any:
    _require_platform_auth(auth)
    ...
```

**Observation:** The existing gate is a plain synchronous function called inside handler bodies — not wired through FastAPI's `Depends()` system. The handler still declares `auth: AuthContext = Depends(get_auth_context)` and `session = Depends(get_tenant_session_dep)`; both run before the body, so by the time `_require_platform_auth(auth)` fires the session has already been opened. Coexistence story for 6.9.2: the new `require(...)` gate could plausibly fold this case in (PLATFORM-only behaviour = `require(ADMIN, USERS, VIEW, GLOBAL)` modulo permission catalogue), but the existing helper is structurally simpler (no DB read) and the design conversation may want to keep it for endpoints that gate on `user_type` only, not on a specific permission.

**Confidence:** high.

**Open question:** Does the new `require(...)` dependency need to subsume `_require_platform_auth`, or should the two coexist (with `_require_platform_auth` as the cheap user-type-only fast path)? Belongs to the design conversation.

---

### F-GATE-2: `_audience_filter_for(auth)` lives in `routers/v1/rbac.py`, NOT in `repositories/permission_matrix.py`

**Question:** Where does the audience-filter pattern from Step 6.1 actually live, and is it a router-layer or Repo-layer concern?

**Citation:** `src/admin_backend/routers/v1/rbac.py:108-115`. The investigation prompt's pointer to `repositories/permission_matrix.py` is stale.

**Current code:**

```python
def _audience_filter_for(auth: AuthContext) -> str | None:
    """TENANT JWTs see only audience='TENANT'; PLATFORM sees both.

    The filter value flows from the JWT's ``user_type`` claim only;
    no other source. Mirrors AI-MT-03's source-binding discipline,
    one layer up.
    """
    return "TENANT" if auth.user_type == "TENANT" else None
```

Used at `rbac.py:184, :248, :361` — each call site passes the result as a kwarg into a Repo method:

```python
audience_filter=_audience_filter_for(auth),
```

**Observation:** This is a **router-layer helper that produces a Repo-layer filter argument**. The Repo (`PermissionMatrixRepo.get_matrix`, `RolesRepo.list_grouped`, etc.) accepts `audience_filter: str | None` and applies it in SQL. Two layers: router computes "what audience can this caller see?" from `auth.user_type`; Repo applies it to its WHERE clause. The new 6.9.2 gate (router-layer FastAPI dependency that runs before the handler body) is a third, earlier layer — it intercepts requests entirely. `_audience_filter_for` and the new gate do not conflict; they apply at different stages with different responsibilities (`_audience_filter_for` narrows results within an already-allowed request; the gate decides whether the request is allowed at all). Worth keeping the layer separation explicit in the design.

**Confidence:** high.

**Open question:** The prompt's location pointer was incorrect — surfaced rather than worked around. Design conversation should treat the rbac.py location as the canonical reference.

---

### F-GATE-3: No other gate-shaped helpers in `src/admin_backend/routers/v1/`

**Question:** Are there other `_require_*` or similar handler-top boilerplate helpers I should know about beyond `_require_platform_auth`?

**Citation:** Repo-wide grep `grep -rn "_require_\|_audience_filter_for\|require_platform" src/admin_backend/ --include="*.py"` returns only:

- `routers/v1/platform_users.py:102-109` — `_require_platform_auth` definition.
- `routers/v1/platform_users.py:216, :258` — its two call sites.
- `routers/v1/rbac.py:108-115` — `_audience_filter_for` (different category — see F-GATE-2).
- `routers/v1/tenant_users.py:11`, `routers/v1/org_tree.py:20`, `routers/v1/platform_users.py:12` — DOCSTRING references to `_require_platform_auth` in context-setting prose. Not call sites; not separate helpers.

**Current code:** No other definitions exist.

**Observation:** `_require_platform_auth` is the only v0 router-layer auth-tier gate. Other routers (`tenants.py`, `tenant_users.py`, `org_tree.py`, `lookups.py`, `dashboard.py`, `modules_access.py`, `rbac.py`, `role_assignments.py`) accept both user types and rely on RLS or an in-Repo audience filter for visibility scoping. The "Note on the v0 auth model" convention in CLAUDE.md documents three router-layer postures (PLATFORM-only, multi-user-type with RLS, multi-user-type with app-layer audience filter); the new permission-gate is a fourth posture that overlays one of the existing three.

**Confidence:** high.

**Open question:** Does 6.9.2's `require(...)` apply globally (every endpoint must declare one) or selectively (only PLATFORM-only-tier endpoints retain their explicit gate; everyone else applies `require(...)`)? Step 6.9.3's mandatory-gate-discipline test is meant to enforce one of these. Design conversation territory.

---

### F-GATE-4: `_require_platform_auth` is a DIRECT CALL inside the handler body, not `Depends(_require_platform_auth)`

**Question:** Is the existing PLATFORM gate wired through FastAPI's `Depends()` system, or invoked imperatively at the top of each handler?

**Citation:** `src/admin_backend/routers/v1/platform_users.py:216` and `:258`. No `Depends(_require_platform_auth)` appears anywhere in `src/`.

**Current code (call shape):**

```python
async def list_platform_users(
    ...
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_tenant_session_dep),
) -> Any:
    _require_platform_auth(auth)
    ...
```

The `Depends`-resolved `auth` and `session` arguments materialise before the handler body runs (FastAPI's dependency-injection ordering); only then does the body's first line `_require_platform_auth(auth)` execute.

**Observation:** Load-bearing for 6.9.2's gate design. Two architectural choices for the `require(...)` factory:

1. **Pure FastAPI dependency** — `Depends(require(MODULE, RESOURCE, ACTION, SCOPE))` declared in the handler signature. Pros: ordering is declarative; FastAPI can run the gate dependency in parallel with `get_tenant_session_dep` (same dependency tier); cleaner to enumerate via `app.routes` (Step 6.9.3 test). Cons: the gate needs the session (to call `has_permission()`), so it depends on `get_tenant_session_dep` and runs *after* the session is opened — same ordering as today's `_require_platform_auth` body call, just expressed differently.
2. **Imperative call inside handler body** — `require_permission(session, auth, MODULE, RESOURCE, ACTION, SCOPE, target_anchor)` invoked as the first line. Pros: matches the existing `_require_platform_auth` precedent exactly; lower mental load for new contributors. Cons: harder to introspect via `app.routes`; less declarative.

Either is precedented by close analogues. The existing 6.x convention is the imperative-call style for gates that take only `auth`; the `Depends(...)` style for things that take `auth + session`. The new gate takes `auth + session + target_anchor`, which puts it in a borderline category.

**Confidence:** high.

**Open question:** Pure `Depends(require(...))` vs imperative `require_permission(...)` is the load-bearing 6.9.2 design choice. Belongs to the design conversation; this finding surfaces both options without recommending.

---

## DEPEND — FastAPI dependency wiring

### F-DEPEND-1: `get_tenant_session_dep` is a single async generator

**Question:** What is the exact shape of the session-opening dependency that the new gate (if it needs a session) will sit alongside?

**Citation:** `src/admin_backend/dependencies.py:56-72`

**Current code:**

```python
async def get_tenant_session_dep(
    auth: AuthContext = Depends(get_auth_context),
    session_factory: async_sessionmaker[AsyncSession] = Depends(
        get_session_factory
    ),
    request_id: str | None = Depends(get_request_id),
) -> AsyncIterator[AsyncSession]:
    """FastAPI-shaped wrapper around get_tenant_session.

    Bridges the dependency-injection layer to Step 2.2a's
    get_tenant_session. Passes request_id through so the dependency
    sets app.request_id for audit triggers (Step 6.2).
    """
    async for session in get_tenant_session(
        auth, session_factory, request_id=request_id
    ):
        yield session
```

**Observation:** Async generator; yields a session whose `app.tenant_id` / `app.user_type` / `app.request_id` GUCs are already set. By the time the handler body runs (or any subsequent dependency runs), RLS context is in place. If 6.9.2's gate depends on `get_tenant_session_dep`, it inherits the same session — calling `has_permission(session, auth, ...)` against it works without ceremony.

**Confidence:** high.

**Open question:** None directly. (Side question for design: should the gate dependency receive `session` via `Depends(get_tenant_session_dep)`, or should it open its own short-lived session? The latter doubles the connection cost per request; the former couples gate lifecycle to handler lifecycle. Existing convention strongly favours one session per request.)

---

### F-DEPEND-2: `get_auth_context` reads `request.state.auth` populated by middleware

**Question:** How does AuthContext reach handlers, and how would a new gate dependency reach it?

**Citation:** `src/admin_backend/dependencies.py:25-37`; `src/admin_backend/middleware/auth.py` (populates `request.state.auth`).

**Current code:**

```python
def get_auth_context(request: Request) -> AuthContext:
    """Pull AuthContext from request.state (populated by AuthMiddleware).

    Raises AuthMissingError if auth wasn't set (caller hit a public
    path or the dependency was used outside the middleware chain).
    """
    auth = getattr(request.state, "auth", None)
    if auth is None:
        raise AuthMissingError(
            "AuthContext missing from request.state; "
            "auth middleware did not run"
        )
    return auth
```

Every router handler that needs auth declares:

```python
auth: AuthContext = Depends(get_auth_context),
```

Found at 11+ handler sites across `tenants.py`, `tenant_users.py`, `platform_users.py`, `rbac.py`, `role_assignments.py`, `dashboard.py`, `modules_access.py` (per `grep -rEn "Depends\(" src/admin_backend/routers/`).

**Observation:** The new gate dependency follows the same pattern. If implemented as `Depends(require(...))`, the factory's inner function declares `auth: AuthContext = Depends(get_auth_context), session: AsyncSession = Depends(get_tenant_session_dep)` and FastAPI resolves them in order. AuthContext is constructed once per request at middleware time; it's not re-built inside each handler/dep — every `Depends(get_auth_context)` call returns the same instance.

**Confidence:** high.

**Open question:** None.

---

### F-DEPEND-3: No dependency-factory pattern exists in the codebase

**Question:** Is `def make_dep(arg) -> Callable: ...` (a function that returns a `Depends`-injectable callable) used anywhere today?

**Citation:** Repo-wide grep `grep -rEn "def .+\(.+\) -> Callable|return _dep|return _gate|return _inner" src/ tests/ scripts/ --include="*.py"` returns zero hits.

**Current code:** Not present anywhere.

**Observation:** **6.9.2's `require(module, resource, action, scope)` factory is novel territory.** Every existing FastAPI dependency in the codebase is a module-level function with a fixed signature (`get_auth_context`, `get_tenant_session_dep`, `get_session_factory`, `get_request_id`). The pattern `Depends(require(ADMIN, USERS, VIEW, GLOBAL))` — where `require(...)` is called with arguments and returns a dependency function — has no v0 precedent. FastAPI supports it (well-documented in their docs); this just hasn't been needed before.

**Confidence:** high.

**Open question:** Whether 6.9.2 establishes the factory pattern (preferred for declarative gate wiring) or opts for imperative `require_permission(session, auth, ...)` calls inside handler bodies (matches the existing `_require_platform_auth` precedent). See F-GATE-4.

---

### F-DEPEND-4: Existing ClientErrors are raised inside HANDLER bodies, not inside Depends

**Question:** What is the precedent for raising `ClientError` subclasses inside FastAPI dependencies?

**Citation:**

- `routers/v1/platform_users.py:105` — `PlatformAccessRequiredError` raised inside `_require_platform_auth`, called from handler body (not a Depends).
- `routers/v1/tenants.py`, `tenant_users.py`, `org_tree.py`, `rbac.py` — every `*NotFoundError` raised inside handler bodies (per the D-17 RLS-as-404 convention).
- `dependencies.py:33` — `AuthMissingError` IS raised inside `get_auth_context` (a Depends). This is the only existing instance of `raise <ClientError>` inside a `Depends`-wired callable.
- `middleware/auth.py` — `AuthInvalidError` raised inside middleware (Starlette `BaseHTTPMiddleware.dispatch`); converted to JSON response inline (Starlette does NOT route middleware-raised exceptions through `@app.exception_handler`).

**Observation:** **One precedent exists** (`get_auth_context` raises `AuthMissingError`); everything else raises inside handler bodies or middleware. FastAPI's exception handler at `main.py:225` catches `AdminBackendError` raised anywhere in the FastAPI-managed call chain — including dependencies — and converts via `build_error_payload`. So raising `PermissionDeniedError` from inside `Depends(require(...))` IS supported and produces a proper 403 JSON response with the standard envelope (`{code, message, details, request_id}`).

**Confidence:** high.

**Open question:** None on the mechanism; the design conversation just confirms it works.

---

### F-DEPEND-5: Dependency-of-dependency ordering is FastAPI-default (topological)

**Question:** If 6.9.2's gate depends on `get_tenant_session_dep` AND `get_auth_context`, what order does FastAPI resolve them, and what happens if one raises?

**Citation:** `dependencies.py:56-72` already exhibits a 3-deep chain: `get_tenant_session_dep` depends on `get_auth_context`, `get_session_factory`, and `get_request_id`. FastAPI's resolution is topological — each dependency is resolved exactly once per request (with caching keyed on the callable identity). If `get_auth_context` raises (e.g., `AuthMissingError`), `get_tenant_session_dep` never runs because its `auth` argument cannot be supplied.

**Current code:** No custom ordering is configured anywhere. Implicit topological order via Python's resolution of `Depends(...)` arguments.

**Observation:** For 6.9.2, a `Depends(require(...))` factory's inner function can safely depend on both `get_tenant_session_dep` and `get_auth_context`; FastAPI resolves them once and caches. The gate runs after both are constructed. If the gate raises `PermissionDeniedError`, the handler body never executes and the exception handler returns 403. If middleware-raised `AuthMissingError` fires first, the chain shorts at `get_auth_context` and the gate never runs.

**Confidence:** high.

**Open question:** None.

---

## ERR — Error class hierarchy

### F-ERR-1: `errors.py` has a two-tier hierarchy with class-attribute-driven HTTP response

**Question:** What is the shape that `PermissionDeniedError` must fit?

**Citation:** `src/admin_backend/errors.py:30-80`

**Current code (load-bearing):**

```python
class AdminBackendError(Exception):
    public_message: str = "An error occurred"
    http_status: int = 500
    code: str = "INTERNAL_ERROR"

    def __init__(self, internal_message: str, **context: Any) -> None:
        super().__init__(internal_message)
        self.internal_message = internal_message
        self.context: dict[str, Any] = context


class ClientError(AdminBackendError):
    http_status: int = 400
    code: str = "CLIENT_ERROR"
    public_message: str = "The request is invalid"


class ServerError(AdminBackendError):
    http_status: int = 500
    code: str = "INTERNAL_ERROR"
    public_message: str = "An internal error occurred"
```

**Observation:** Subclasses override `public_message`, `http_status`, `code` as class attributes; the constructor accepts `internal_message` (log-only) plus arbitrary `**context` kwargs that become `self.context: dict[str, Any]`. The `**context` mechanism is exactly the slot for the new gate's structured audit fields (`reason_code`, `module`, `resource`, `action`, `scope`, `target_anchor`, `user_id`). 6.9.2's `PermissionDeniedError(ClientError)` subclass needs only to set the three class attributes (`public_message="Permission denied"`, `http_status=403`, `code="PERMISSION_DENIED"`) and rely on the existing constructor to attach kwargs into `self.context`.

**Confidence:** high.

**Open question:** Whether 6.9.2's three-layer error model (audit fields, user_message property, developer_detail string) needs additional structure beyond the existing `internal_message + **context` shape. Specifically, `developer_detail: str` (the third element of `has_permission()`'s return) maps naturally to `internal_message`; the structured audit fields map naturally to `**context`. So the existing shape covers all three layers without extension. Confirm at design time.

---

### F-ERR-2: `PlatformAccessRequiredError` is the closest 403 precedent

**Question:** What does the established 403-shaped client-error look like, and what's its location convention?

**Citation:** `src/admin_backend/routers/v1/platform_users.py:78-87`

**Current code:**

```python
class PlatformAccessRequiredError(ClientError):
    """Raised when a non-PLATFORM JWT calls a PLATFORM-only endpoint.

    First instance of v0 router-layer auth-tier checking. Future
    PLATFORM-only endpoints inherit this pattern.
    """

    public_message = "This endpoint requires platform access"
    http_status = 403
    code = "PLATFORM_ACCESS_REQUIRED"
```

The constructor inherits from `ClientError`; callers pass `internal_message` plus structured kwargs:

```python
raise PlatformAccessRequiredError(
    f"Endpoint requires PLATFORM user_type; got {auth.user_type}",
    user_id=str(auth.user_id),
    user_type=auth.user_type,
)
```

**Observation:** Two precedents for `PermissionDeniedError` location:

1. **Inside the router file** where it's primarily raised (matches `PlatformAccessRequiredError`, `PlatformUserNotFoundError`, `TenantUserNotFoundError`, `OrgNodeNotFoundError`, `RoleNotFoundError`). Pro: error class lives next to the raiser; routers stay self-contained. Con: if multiple routers need the same error (and 6.9.2's `PermissionDeniedError` is raised from a dependency used by every router), inline definition forces an awkward import.
2. **In `errors.py`** with other shared errors (matches `AuthMissingError`, `AuthInvalidError`, `TenantNotFoundError`, `InvalidSortKeyClientError`). Pro: shared from a single canonical location; importers reach for `admin_backend.errors`. Con: `errors.py` grows.

`PermissionDeniedError` is going to be raised by a centrally-defined gate dependency that every router uses → shared → strongly suggests **errors.py** (matching `InvalidSortKeyClientError`'s "shared error promoted to errors.py" precedent at Step 5.2).

**Confidence:** high.

**Open question:** None on the mechanism; design conversation confirms the location choice.

---

### F-ERR-3: Exception-handler envelope is fixed `{code, message, details, request_id}` per D-31

**Question:** How does the FastAPI handler convert errors to HTTP responses, and does `PermissionDeniedError` fit the envelope without breaking D-31's append-only contract?

**Citation:** `src/admin_backend/main.py:225-256` (handler); `src/admin_backend/errors.py:154-188` (`build_error_payload`).

**Current code (`build_error_payload`):**

```python
def build_error_payload(
    exc: AdminBackendError, request_id: str | None
) -> tuple[int, dict[str, Any], dict[str, str]]:
    headers: dict[str, str] = (
        {"X-Request-Id": request_id} if request_id else {}
    )

    if isinstance(exc, ServerError):
        body: dict[str, Any] = {
            "code": "INTERNAL_ERROR",
            "message": "An internal error occurred",
            "details": None,
            "request_id": request_id,
        }
        return 500, body, headers

    body = {
        "code": exc.code,
        "message": exc.public_message,
        "details": None,
        "request_id": request_id,
    }
    return exc.http_status, body, headers
```

**Observation:** Envelope shape is fixed across all errors today: `{code, message, details, request_id}`. The `details` field is always `None` in v0 — slot reserved for future per-field validation info. `PermissionDeniedError` raised inside the gate produces `{code: "PERMISSION_DENIED", message: "Permission denied" (or similar), details: null, request_id: ...}` automatically; the structured kwargs (`reason_code`, `module`, etc.) attach to `exc.context` but DO NOT reach the response body — they go to the error log only. This matches the three-layer design intent: structured fields for audit (via `context`), user_message via `public_message`, developer_detail via `internal_message`.

If the design conversation wants `details` populated with structured permission-denial data (e.g., `{module, resource, action, scope, reason_code}`), that would be a deliberate envelope extension. D-31 says append-only — adding fields to the `details` payload doesn't break anything (callers already see `details=null` today), but the convention says new variants come via new fields, not by reinterpreting existing ones. Since `details=null` everywhere today, populating it for `PermissionDeniedError` only is a behaviour change; should be explicitly decided.

**Confidence:** high.

**Open question:** Should `PermissionDeniedError` populate the response's `details` field with the structured tuple (`{module, resource, action, scope, reason_code}`), or keep `details=null` and surface only `{code, message, request_id}` to the client? Design conversation territory; affects frontend (does it want to display "Permission required: PRICING_OS.MARKDOWNS.VIEW.STORE" or just a generic "Permission denied" toast?).

---

### F-ERR-4: Error codes are CLASS ATTRIBUTES (strings), not enum values or a registry

**Question:** How are error codes (`"AUTH_MISSING"`, `"TENANT_USER_NOT_FOUND"`, etc.) surfaced?

**Citation:** Every error class in `errors.py` and inline in routers sets `code: str = "..."` as a class attribute. No central enum, no registry, no `Error.codes` namespace.

**Current code (examples):**

```python
# errors.py
class AuthMissingError(ClientError):
    code = "AUTH_MISSING"

class TenantNotFoundError(ClientError):
    code = "TENANT_NOT_FOUND"

# platform_users.py
class PlatformAccessRequiredError(ClientError):
    code = "PLATFORM_ACCESS_REQUIRED"
```

`build_error_payload` reads `exc.code` directly off the instance (which resolves to the class attribute).

**Observation:** The new `PermissionDeniedError` follows the same convention: set `code: str = "PERMISSION_DENIED"` as a class attribute. Convention is UPPER_SNAKE_CASE for the code value. No enum, no registry. Future codes added by future steps will continue this pattern.

**Confidence:** high.

**Open question:** None.

---

## TEST — Security test patterns

### F-TEST-1: Step 5.1 A2 test scaffolding

**Question:** What is the structure of the canonical PLATFORM-only gate test? The new gate's "deny on missing permission" test will mirror this.

**Citation:** `tests/integration/test_platform_users_router.py:340-359`

**Current code:**

```python
def test_a2_tenant_jwt_returns_403_platform_access_required(
    app_client, settings
):
    """Load-bearing v0 auth-gate assertion.

    A TENANT JWT must NOT be able to read the platform_users directory.
    ``platform_users`` has no RLS — without this assertion, a regression
    that drops the ``_require_platform_auth`` call would expose Ithina
    staff identities to tenant users undetected.

    The tenant_id is synthetic (no FK from the JWT to tenants); the
    middleware accepts any well-formed UUID as tenant_id, then the
    router gate fires before any DB call lands.
    """
    synthetic_tenant_id = uuid.uuid4()
    resp = app_client.get(
        "/api/v1/platform-users",
        headers=_auth(_tenant_jwt(settings, synthetic_tenant_id)),
    )
    assert resp.status_code == 403
```

**Observation:** Uses `app_client` (FastAPI TestClient via the `app_with_test_routes` fixture from conftest), `settings`, and helpers `_auth(...)` + `_tenant_jwt(settings, tenant_id)` defined at the top of the test file. The 6.9.2 gate's analogous test ("TENANT user with no relevant grant gets 403 from `/api/v1/some-endpoint`") follows the same scaffolding: build JWT with no role assignments, hit the endpoint, assert 403 + `PERMISSION_DENIED` code.

**Confidence:** high.

**Open question:** None.

---

### F-TEST-2: Step 5.2 T9 RLS-as-404 test scaffolding

**Question:** What does the canonical end-to-end RLS isolation test look like? Not directly mirrored by 6.9.2 but uses the same scaffolding.

**Citation:** `tests/integration/test_tenant_users_router.py:421-454`

**Current code (load-bearing structure):**

```python
async def test_t9_cross_tenant_detail_returns_404(
    app_client, settings, make_tenant, make_tenant_user
):
    """LOAD-BEARING: cross-tenant access by TENANT users surfaces as 404
    (RLS-as-404 per D-17), not 403.
    ...
    """
    tenant_a = await make_tenant(name="T9-TenantA")
    tenant_b = await make_tenant(name="T9-TenantB")
    user_b = await make_tenant_user(
        tenant_id=tenant_b.id, email="t9b@t9.test", status="ACTIVE"
    )

    resp = app_client.get(
        f"/api/v1/tenant-users/{user_b.id}",
        headers=_auth(_tenant_jwt(settings, tenant_a.id)),
    )
    assert resp.status_code == 404
    body = resp.json()
    assert body["code"] == "TENANT_USER_NOT_FOUND"
    assert body["message"] == "Tenant user not found"
```

**Observation:** Async test, uses conftest factories to build cross-tenant fixtures, hits endpoint with TENANT-A JWT requesting TENANT-B's row. Mirrors what 6.9.2 will need for "PLATFORM user with grant fails on cross-tenant target_anchor" or similar. Same fixture suite (`make_tenant`, `make_tenant_user`, `_tenant_jwt`, `app_client`).

**Confidence:** high.

**Open question:** None.

---

### F-TEST-3: Step 6.8.3 R2 no-call-invariant test (Repo patching)

**Question:** How does the codebase express the "must NOT execute X before Y is verified" property for security-critical code paths?

**Citation:** `tests/integration/test_role_assignments_router.py:101-134`

**Current code:**

```python
async def test_r2_tenant_jwt_does_not_see_platform_assignments(
    app_client, settings, make_tenant
):
    """LOAD-BEARING: TENANT JWT response has empty platform_assignments
    block AND the platform-side Repo method was NOT invoked.

    platform_user_role_assignments has NO RLS (per Step 6.8.1 D-34).
    The router's app-layer routing is the only barrier preventing
    a TENANT JWT from seeing every platform-side assignment in the
    DB. We assert BOTH the response shape AND the no-call invariant
    (via patch on the Repo method).
    """
    tenant = await make_tenant(name="R2-Tenant")
    from admin_backend.routers.v1 import role_assignments as router_module

    with patch.object(
        router_module._repo,
        "list_platform_assignments",
        new=AsyncMock(),
    ) as mock_platform_list:
        resp = app_client.get(
            "/api/v1/role-assignments?limit=200",
            headers=_auth(_tenant_jwt(settings, tenant.id)),
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["platform_assignments"]["items"] == []
    assert body["platform_assignments"]["pagination"]["total"] == 0
    assert mock_platform_list.call_count == 0
```

**Observation:** `unittest.mock.patch.object` + `AsyncMock()` is the established pattern for asserting "method X was NEVER called for case Y". 6.9.2 may want an analogous test: "if `require(...)` denies, the handler body's Repo calls NEVER fire" — verifies the gate runs BEFORE any DB work the handler would do. The pattern is straightforward to apply: patch the relevant Repo method, assert `call_count == 0` after a denied request.

**Confidence:** high.

**Open question:** Is the "gate runs before DB session is opened" property worth a dedicated test? `Depends(get_tenant_session_dep)` opens the session whether or not the gate denies — opening + closing an empty session per denied request is cheap but not free. If design decides the gate's `Depends(...)` ordering should be "before session", that needs its own test similar to R2.

---

### F-TEST-4: No route-tree assertion tests exist in the codebase

**Question:** Is there a test that iterates `app.routes` and asserts properties (e.g., "every route has a Depends-injected gate")?

**Citation:** Repo-wide grep `grep -rn "app.routes\|app.router.routes\|for route in" tests/` returns zero hits.

**Current code:** Not present.

**Observation:** **6.9.3's mandatory-gate-discipline test will be novel territory.** No current test iterates the FastAPI route tree to assert structural properties. The test would look approximately like:

```python
def test_every_route_has_a_gate_or_is_explicitly_public(app):
    PUBLIC_PATHS = frozenset({"/api/v1/health", "/api/v1/ready", ...})
    for route in app.routes:
        if route.path in PUBLIC_PATHS:
            continue
        deps = route.dependant.dependencies  # FastAPI internal
        gate_present = any(d.call is require or ...)
        assert gate_present, f"{route.path} has no gate"
```

This is **6.9.3 design territory**, not 6.9.2 — the gate factory needs to ship before the discipline test can reference it.

**Confidence:** high.

**Open question:** Surfaced as a 6.9.3 design item per the prompt's framing.

---

### F-TEST-5: LOAD-BEARING is a convention written into docstrings and section banners, not a pytest marker

**Question:** How are tests marked as LOAD-BEARING in code?

**Citation:** Multiple examples in `tests/integration/` (per `grep -rn "LOAD-BEARING\|LOAD BEARING\|load.bearing" tests/`):

- `test_tenant_users_router.py:14, :416, :420, :424` — section banner + docstring annotation
- `test_role_assignments_router.py:3, :97-98, :101, :104` — module docstring + section banner + docstring
- `test_has_permission.py:11, :367, :434, :583, :748, :879` — module docstring + each test's first-line docstring tag
- `test_platform_users_router.py:13, :330, :343-348` — module docstring + section banner + docstring (lowercase "load-bearing")
- `test_tenants_repo.py:7` — module docstring at the top: "R4 and R5 are the load-bearing cross-tenant isolation tests"

**Current code (example pattern):**

```python
# =============================================================================
# R2 (LOAD-BEARING SECURITY): TENANT JWT short-circuits platform-side query.
# =============================================================================


async def test_r2_tenant_jwt_does_not_see_platform_assignments(...):
    """LOAD-BEARING: TENANT JWT response has empty platform_assignments
    block AND the platform-side Repo method was NOT invoked.
    ...
    """
```

**Observation:** No pytest marker, no decorator, no naming convention beyond the docstring/banner annotation. The convention is informational (helps readers prioritise) rather than mechanical (no tooling consumes it). 6.9.2's cross-tenant-denial test would follow the same shape — first-line docstring marker + section banner comment.

**Confidence:** high.

**Open question:** Would 6.9.2 benefit from elevating LOAD-BEARING to a pytest marker (e.g., `@pytest.mark.load_bearing`) so CI could enforce "load-bearing tests must not be deselected"? Not a 6.9.2 requirement; surfaced as a backlog item for a separate prompt.

---

## ROUTER — `/me/*` design surface

### F-ROUTER-1: No `/me/*` endpoints exist anywhere

**Question:** Does the codebase already have a `/me/*` shape that 6.9.2 inherits, or is this greenfield?

**Citation:** Repo-wide grep `grep -rn "/me\b\|/me/" src/admin_backend/routers/v1/` returns zero hits. `grep -rn "/me\b" docs/endpoints/` returns zero hits.

**Current code:** Not present.

**Observation:** **Greenfield.** 6.9.2 establishes the `/me/*` mount path, the per-endpoint response shapes, and the auth requirement (presumably "any authenticated user"). No `me_router.py` exists; 6.9.2 creates `src/admin_backend/routers/v1/me.py` (or similar) and registers it in `main.py`'s router-includes block.

**Confidence:** high.

**Open question:** Naming — `/me/permissions` and `/me/can-do` are the conventional names; design conversation just confirms.

---

### F-ROUTER-2: Singleton response shape is the resource fields at top level; D-30 list-only envelope doesn't apply

**Question:** What shape do non-list endpoints (like `/me/can-do`) follow?

**Citation:** `src/admin_backend/routers/v1/tenants.py:193` (`GET /api/v1/tenants/{tenant_id}` returns `TenantDetail`); `src/admin_backend/schemas/tenant.py:136` (`TenantDetail` class); `docs/endpoints/tenants.md:14`.

**Current code (tenants.md convention):**

> Response envelope — list shape is `{items, pagination}` (D-30); single-object endpoints return the object directly.

`TenantDetail` is a Pydantic `BaseModel` returned as-is; no envelope wrapper. The same convention applies to `TenantUserRead`, `PlatformUserRead`, `OrgNodeTreeItem` (singleton tree response per Step 5.3's D-30 exception note), and the dashboard's card-shaped responses (e.g., `FleetStatsResponse`).

**Observation:** `/me/permissions` returns a list of grants → either `{items: list[PermissionGrant]}` (matches the D-30 list envelope without pagination — closest analogue is `/api/v1/lookups`'s batch-by-key `{lookups: {...}}` shape from Step 3.6, which Note "batch-by-key response envelope" in CLAUDE.md documents as the precedent), OR a bare `list[PermissionGrant]` at the JSON top level (currently no precedent for bare top-level array).

`/me/can-do` returns a single decision object — natural shape is the object directly: `{allowed: bool, reason_code: str, ...}` at the top level, no envelope. Matches `TenantDetail`, `TenantUserRead`, etc.

**Confidence:** high.

**Open question:** `/me/permissions` envelope — `{items: [...]}` vs `{permissions: [...]}` vs bare `list[PermissionGrant]`. The "batch-by-key" note in CLAUDE.md (added at Step 3.6) recommends `{<resource_name>: [...]}` at the top level "to leave room for cross-cutting metadata". `{permissions: [...]}` is the matching pattern. Design conversation territory.

---

### F-ROUTER-3: Router-include pattern in `main.py`

**Question:** How does 6.9.2 register its new router?

**Citation:** `src/admin_backend/main.py:30-38` (imports); `:191-223` (includes).

**Current code:**

```python
from admin_backend.routers.v1 import dashboard as dashboard_router
from admin_backend.routers.v1 import lookups as lookups_router
# ... (one import per router module)

# In create_app():
app.include_router(
    tenants_router.router, prefix=settings.api_prefix
)
app.include_router(
    lookups_router.router, prefix=settings.api_prefix
)
# ... etc, one include_router call per registered router.
```

`settings.api_prefix` is `/api/v1`. Each router module exports either a single `router` (most) or multiple named routers (rbac.py exports `roles_router`, `permissions_router`, `matrix_router`; included separately).

**Observation:** `me_router` follows the single-`router` pattern. Add `from admin_backend.routers.v1 import me as me_router` at the import block; add `app.include_router(me_router.router, prefix=settings.api_prefix)` at the includes block. The router's internal route declarations use prefixes like `/me/permissions` and `/me/can-do`, so the final URLs are `/api/v1/me/permissions` and `/api/v1/me/can-do`.

**Confidence:** high.

**Open question:** Whether to declare a single router with prefix `/me` or two flat routes. The single-prefix-`/me` approach is cleaner and matches how `rbac.py` and `dashboard.py` group related routes; design conversation confirms.

---

### F-ROUTER-4: Per-endpoint doc convention is the 8-section format; canonical example is `tenants.md`

**Question:** What documentation does each new endpoint require?

**Citation:** `docs/endpoints/tenants.md` (canonical example, referenced by every other endpoint doc); `docs/endpoints/platform-users.md`, `tenant-users.md`, `org-tree.md`, `rbac.md`, `lookups.md` (wait — `lookups.md` isn't in the listing; only `dashboard.md`, `module-access.md`, `org-tree.md`, `platform-users.md`, `rbac.md`, `role-assignments.md`, `tenants.md`, `tenant-users.md`).

Actual files in `docs/endpoints/` (per `ls`): `dashboard.md`, `module-access.md`, `openapi.json`, `org-tree.md`, `platform-users.md`, `rbac.md`, `role-assignments.md`, `tenants.md`, `tenant-users.md`.

**Current code:** Each endpoint doc carries 8 fixed sections per CLAUDE.md "Per-endpoint documentation":

1. Endpoint summary
2. Request
3. Response 200
4. Response codes
5. Behaviour notes
6. Example calls
7. Sample integration code
8. Implementation reference

`tenants.md` is the canonical 3-endpoint example; all subsequent docs copy-paste-edit its structure.

**Observation:** 6.9.2 ships `docs/endpoints/me.md` (or `me-permissions.md` + `me-can-do.md` if separated) following the same 8-section convention. Reference is still `tenants.md`. `lookups.md` is conspicuously absent from `docs/endpoints/` — that's a pre-existing gap (Step 3.6 shipped `/api/v1/lookups` without an endpoint doc), not anything 6.9.2 needs to address.

**Confidence:** high.

**Open question:** None.

---

## Open questions for design conversation

Consolidated from each finding plus any naturally-surfaced observations. The first group sits inside the area scope; the trailing bullets flag scope-creep observations the prompt asks to NOT investigate.

### Design-conversation in-scope (6.9.2):

1. **F-VERIFY-2 / F-ROUTER-2** — `/me/permissions` response shape: serialise `list[PermissionGrant]` directly via a parallel Pydantic schema (`PermissionGrantRead`)? Envelope as `{permissions: [...]}` per the batch-by-key precedent, `{items: [...]}` per the D-30 list precedent, or bare top-level array? Affects both the route handler's return-type annotation and `docs/endpoints/me.md`.

2. **F-GATE-1 / F-GATE-4** — Whether the new `require(...)` gate **replaces** `_require_platform_auth` (folding the PLATFORM-tier case into a `require(...)` call) or **coexists** with it (keeping `_require_platform_auth` as the cheap user-type-only fast path). Step 6.9.3 retrofit territory; flag at 6.9.2 design so the gate's API doesn't preclude either option.

3. **F-GATE-4 / F-DEPEND-3** — Gate architecture: pure `Depends(require(MODULE, RESOURCE, ACTION, SCOPE))` factory pattern (declarative, introspectable via `app.routes`, no v0 precedent) vs imperative `require_permission(session, auth, MODULE, RESOURCE, ACTION, SCOPE, target_anchor)` call inside the handler body (matches `_require_platform_auth` precedent exactly, simpler mental model). Load-bearing design choice; the v0 codebase has no `Depends(...)` factory anywhere, so the factory path is novel territory.

4. **F-ERR-2** — `PermissionDeniedError` location: inline in the new `me.py` router file (matches `PlatformAccessRequiredError`, `PlatformUserNotFoundError` precedent) vs in shared `errors.py` (matches `InvalidSortKeyClientError`'s "shared error promoted to errors.py" Step 5.2 precedent). Since the error is raised by a gate dependency every router uses, shared `errors.py` looks like the better fit.

5. **F-ERR-3** — Whether `PermissionDeniedError` populates the response's `details` field with structured permission-denial data (`{module, resource, action, scope, reason_code}`) or keeps `details=null` and surfaces only `{code, message, request_id}` to the client. Affects frontend display (specific permission message vs generic toast). D-31 says append-only — adding fields to `details` is non-breaking for callers seeing `null` today, but is a real behaviour change for the envelope shape across all errors.

6. **F-TEST-3** — Worth a dedicated "gate denies before any Repo call fires" test? `Depends(get_tenant_session_dep)` opens the session whether or not the gate denies (per F-DEPEND-1 / F-DEPEND-5). If design wants the gate's ordering to be "before session" (cheaper denial path), that needs to be expressed in the gate's `Depends(...)` order and verified by a test similar to Step 6.8.3's R2.

7. **F-ROUTER-3** — Single `me_router` with prefix `/me` (cleanest) vs two flat routes on a generic `meta_router`. Single-prefix is the obvious pick given `rbac.py`'s precedent; confirm at design time.

8. **F-VERIFY-3 / F-ERR-1** — Whether granular `ReasonCode` values surface to the response (today's binary `GRANT_MATCHED` / `NO_MATCHING_GRANT_OR_OUT_OF_SCOPE` map to either `allowed=true` or `allowed=false` plus a single denial code). The design lock already says granular codes are deferred to Step 6.16 audit log writes; just confirming `/me/can-do` is fine with the binary today.

### Out-of-scope (scope-creep flags surfaced for later prompts, NOT investigated here):

- **Step 6.9.3 mandatory-gate-discipline test surface (F-TEST-4).** No `app.routes` iteration test exists today; 6.9.3 establishes the pattern. Surfaces a question whether the `require()` factory needs an introspectable marker (e.g., a sentinel attribute on the inner function) so the discipline test can identify "this route has a gate" without false positives.

- **LOAD-BEARING pytest marker (F-TEST-5).** Today purely a docstring/banner convention. Worth promoting to `@pytest.mark.load_bearing` so CI can enforce "load-bearing tests must not be deselected"? Backlog item, not 6.9.2.

- **`docs/endpoints/lookups.md` gap.** Pre-existing; Step 3.6 shipped `/api/v1/lookups` without an endpoint doc. Not 6.9.2's territory.

- **AuthMiddleware `PUBLIC_PATHS` and `/me/*`.** `/me/*` will always require auth (it returns the caller's permissions), so the `PUBLIC_PATHS` frozenset in `middleware/auth.py:38` doesn't change. Trivially confirmable; no design conversation needed.

- **FN-AB-22 (Auth0 scope expansion).** Flagged in 6.9.1's pre-flight as irrelevant to the resolver core; same applies to 6.9.2's gate. AuthContext shape is stable; gate consumes AuthContext, not Auth0 specifics. Surfaced here so it doesn't get re-litigated.
