# Prompt — Step 2.3: Auth middleware + audit context middleware + structured errors + tenant-session Depends provider

> Generated 2026-05-02 (second-pass revision). Paste into a fresh Claude Code session for Step 2.3.
> First-pass revision fixed: D-08 reference removed; python-jose references removed (D-26: pyjwt[crypto]); middleware ordering flipped to audit-outermost; Depends provider added; lazy-access via app.state with point-of-use annotations; CORS + structured errors + app.request_id GUC pinned at this step.
> Second-pass revision fixed: try/except/finally in audit middleware (prior try/finally had unreachable code and missed status); ServerError subclasses return generic public response (anti-information-disclosure); `Settings()` consolidated via `@lru_cache get_settings()`; python-json-logger version pinned `>=2.0,<4.0` with PyPI-vs-import name note; T9 injection test escalated to full-stack (DB session sees correct user_type).

---

## Pre-flight

1. `./scripts/check_setup.sh` — expect 35/35.
2. `git log --oneline -10` — confirm Step 2.2b at HEAD.
3. Read `CLAUDE.md` fully. Pay particular attention to:
   - **D-03** (tenant isolation; session vars).
   - **D-17** (RLS-blocked reads return 404, not 403). Forward-binding for handlers; middleware respects this contract.
   - **D-24** (JWT identity-only claims; AuthContext shape).
   - **D-26** (`pyjwt[crypto]`, not python-jose).
   - **D-27** (NULLIF wrapper in policies).
   - **AI-MT-03** (source-binding from AuthContext).
   - The error-class hierarchy section.
   - The logging discipline rules.
4. Read `docs/architecture.md` "Request lifecycle" section.
5. Read `BUILD_PLAN.md` Step 2.3 in full.
6. Read this prompt fully.

---

## Step ID and intent

**Step 2.3** — Wire the request-handling layer that runs on every request:

1. Audit context middleware (outermost): generates `request_id`, captures `ip`/`user_agent`, emits structured INFO log per request.
2. Auth middleware: extracts JWT from `Authorization: Bearer`, verifies via `StubAuthClient`, populates `request.state.auth: AuthContext`.
3. CORS middleware: per `CORS_ALLOWED_ORIGINS` setting.
4. FastAPI `Depends` provider (`get_tenant_session_dep`) that bridges Step 2.2a's `get_tenant_session(auth, session_factory)` to FastAPI's dependency injection.
5. Structured error refactor: the four error classes from Step 2.1 gain `http_status` and `public_message` fields; FastAPI exception handler maps them to JSON responses with consistent shape.

This step makes the auth + DB foundation usable from FastAPI handlers. Step 2.4 builds the health endpoint and `main.py` lifespan that wires everything.

CLAUDE_CODE step. Cross-cutting infra. No domain endpoints yet.

---

## Required behaviour

### Middleware ordering (matters)

Starlette middleware order: `add_middleware(A)` then `add_middleware(B)` means **B runs first on requests** (outermost), A runs first on responses. We want:

- **Audit context outermost** so `request_id` is generated for every request including auth-failed ones. The 401 response from auth middleware then includes the same request_id that the log line emits. One request = one request_id.
- **Auth next** so it has access to `request.state.request_id` already set.
- **CORS innermost** of these three so its handling happens after auth/audit setup.

Code-side: add in reverse order:

```python
app.add_middleware(CORSMiddleware, ...)        # innermost (last to run on request, first to run on response)
app.add_middleware(AuthMiddleware, ...)        # middle
app.add_middleware(AuditContextMiddleware)     # outermost (first to run on request)
```

### Public paths skip auth

Auth middleware skips the JWT check for these exact paths (no globs, no prefixes):

- `/v1/health`
- `/v1/openapi.json`
- `/v1/docs`
- `/v1/redoc`
- `/metrics` (if/when added at Step 7.x)

For these, `request.state.auth` is left unset (None). The dependency `get_tenant_session_dep` raises `AuthMissingError` if called without `request.state.auth` — handlers that want to be reachable on public paths must not depend on `get_tenant_session_dep`.

### Structured error refactor (lands here, deferred from Step 2.1)

Update `src/admin_backend/errors.py`:

```python
class AdminBackendError(Exception):
    """Base for all admin-backend errors."""
    public_message: str = "An error occurred"
    http_status: int = 500
    code: str = "INTERNAL_ERROR"

    def __init__(self, internal_message: str, **context):
        super().__init__(internal_message)
        self.internal_message = internal_message
        self.context = context  # for structured logging


class ClientError(AdminBackendError):
    """4xx-class: caller's fault. Subclass-specific public_message is fine
    (the caller knows what they sent; we tell them what was wrong with it).
    """
    http_status: int = 400
    code: str = "CLIENT_ERROR"


class ServerError(AdminBackendError):
    """5xx-class: our fault. ALWAYS returns the generic INTERNAL_ERROR / generic
    message to the client. Subclass-specific information goes to internal logging
    only, never to the response body. This is anti-information-disclosure: an
    attacker probing the auth or DB layer should learn nothing about the internal
    structure from error responses.

    Subclasses MUST NOT override public_message or code. They override only
    internal_message (via constructor) for log clarity.
    """
    http_status: int = 500
    code: str = "INTERNAL_ERROR"
    public_message: str = "An internal error occurred"


class AuthMissingError(ClientError):
    public_message = "Authentication required"
    http_status = 401
    code = "AUTH_MISSING"


class AuthInvalidError(ClientError):
    public_message = "Authentication invalid"
    http_status = 401
    code = "AUTH_INVALID"


class InvalidTenantIdError(ClientError):
    # Don't leak that tenant_id specifically failed; merge with generic AUTH_INVALID.
    public_message = "Authentication invalid"
    http_status = 401
    code = "AUTH_INVALID"


class AppRolePrivilegeError(ServerError):
    """Internal-only marker that the DB role has SUPERUSER or BYPASSRLS.
    Inherits ServerError's generic public_message and code: client sees
    INTERNAL_ERROR / 'An internal error occurred'. Operators see the specific
    type and remediation message in the log line.
    """
    # No overrides. Constructor passes the specific message through to internal_message.
```

Refactor is backwards-compatible: existing Step 2.1 tests pass unchanged because exception inheritance is preserved and constructor args still work.

`AppRolePrivilegeError` from Step 2.2a moves under `ServerError`. It's a startup error and shouldn't reach the request path, but the consistent shape simplifies the FastAPI exception handler.

### FastAPI exception handler

Two-path handler: server errors return generic, client errors return specific.

```python
import logging

logger = logging.getLogger("admin_backend.errors")


@app.exception_handler(AdminBackendError)
async def admin_backend_error_handler(request: Request, exc: AdminBackendError):
    request_id = getattr(request.state, "request_id", None)

    if isinstance(exc, ServerError):
        # Generic to client; specifics to internal log.
        logger.error("server error", extra={
            "request_id": request_id,
            "exception_type": type(exc).__name__,
            "internal_message": exc.internal_message,
            "context": exc.context,
        })
        return JSONResponse(
            status_code=500,
            content={
                "code": "INTERNAL_ERROR",
                "message": "An internal error occurred",
                "request_id": request_id,
            },
            headers={"X-Request-Id": request_id} if request_id else {},
        )

    # ClientError path: subclass-specific code/message is fine.
    return JSONResponse(
        status_code=exc.http_status,
        content={
            "code": exc.code,
            "message": exc.public_message,
            "request_id": request_id,
        },
        headers={"X-Request-Id": request_id} if request_id else {},
    )
```

Note: the handler also adds `X-Request-Id` to the response headers. This is the path for error responses; the audit middleware's success path adds X-Request-Id directly to the response object before returning. Both paths emit the same header; the request_id is the one generated by audit middleware.

### `app.request_id` GUC

For audit-trigger work at Step 6.2, audit triggers need to know the request_id of the change. Easiest pattern: set `app.request_id` as a third session var in `get_tenant_session` so triggers can read it via `current_setting('app.request_id', TRUE)`.

Decision: set it now. One extra `set_config` call per request, negligible cost, removes a 6.2-time retrofit.

This means `get_tenant_session` (in `src/admin_backend/db/session.py`) gains a third var assignment. The Depends provider in this step pulls `request_id` from `request.state.request_id` and passes it through.

### CORS

Use FastAPI's built-in `CORSMiddleware`. Configure from `settings.cors_allowed_origins` (parse the comma-separated env var into a list). Allow credentials, allow standard methods, allow `Authorization` header.

### 404-vs-403 contract per D-17 (forward-binding for handlers)

This step doesn't ship handlers, but the contract is: when handlers do tenant-scoped reads, RLS-filtered (invisible) rows result in 404 (not 403, not 401). Document in the prompt's scope-out and put a `# TODO: handlers must surface 404, see D-17` comment in the FastAPI exception handler if there's a natural place.

### Operational gotchas

1. `set -a && source .env && set +a` per bash call.
2. `request.state` is a `starlette.datastructures.State` object. Attributes set on it persist for the request's lifetime.
3. **`BaseHTTPMiddleware` buffers the entire response body before sending.** Fine for v0 (read-only API returning JSON). NOT fine for streaming responses or large file downloads — those would need pure ASGI middleware (no `BaseHTTPMiddleware`). Forward-note for future contributors: if a streaming endpoint is added, that endpoint's response will be buffered by these middlewares, defeating the streaming. The escape hatch is to rewrite as pure ASGI middleware. Don't do that work now; just know the constraint.
4. **The new `request_id` parameter on `get_tenant_session` (default None) MUST not break Step 2.2a tests.** Existing tests call `get_tenant_session(auth, factory)` positionally; the kwarg default keeps them green. Run `pytest tests/unit/test_session.py -v` after the signature change to confirm 15/15 still pass before declaring done.

---

## Scope in

### File 1: Update `src/admin_backend/errors.py`

Refactor per "Structured error refactor" section above. Verify Step 2.1's 21 tests still pass.

### File 2: `src/admin_backend/middleware/__init__.py`

Empty package marker.

### File 3: `src/admin_backend/middleware/audit_context.py`

```python
import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from admin_backend.errors import AdminBackendError

logger = logging.getLogger("admin_backend.requests")


class AuditContextMiddleware(BaseHTTPMiddleware):
    """Generates request_id, captures request metadata, emits one INFO log line per request.

    Outermost middleware: runs first on the request, last on the response. The log
    line emits in finally so it fires on success AND on exception paths. Status code
    captured from the response object on success, or from exc.http_status if an
    AdminBackendError propagates.
    """

    async def dispatch(self, request: Request, call_next):
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id

        # IP: prefer X-Forwarded-For first IP if present.
        xff = request.headers.get("x-forwarded-for")
        request.state.ip = xff.split(",")[0].strip() if xff else (
            request.client.host if request.client else None
        )
        request.state.user_agent = request.headers.get("user-agent")

        start = time.perf_counter()

        # Status defaults to 500 (assume worst). Updated on success or known exception.
        response_status = 500
        exception_logged: Exception | None = None

        try:
            response = await call_next(request)
            response_status = response.status_code
            response.headers["X-Request-Id"] = request_id
            return response
        except AdminBackendError as exc:
            response_status = exc.http_status
            exception_logged = exc
            raise
        except Exception as exc:
            # Unhandled non-AdminBackendError. Status stays 500.
            exception_logged = exc
            raise
        finally:
            latency_ms = round((time.perf_counter() - start) * 1000, 2)
            auth = getattr(request.state, "auth", None)
            tenant_id = str(auth.tenant_id) if auth and auth.tenant_id else None
            user_id = str(auth.user_id) if auth else None
            user_type = auth.user_type if auth else None

            logger.info("request completed", extra={
                "request_id": request_id,
                "tenant_id": tenant_id,
                "user_id": user_id,
                "user_type": user_type,
                "method": request.method,
                "path": request.url.path,
                "status": response_status,
                "latency_ms": latency_ms,
                "ip": request.state.ip,
                "user_agent": request.state.user_agent,
                "exception": type(exception_logged).__name__ if exception_logged else None,
            })
```

The `try/except/finally` structure is load-bearing. The `try/finally` shape (without explicit excepts) has two bugs: (1) the `return response` inside `try` makes any post-finally code unreachable, so X-Request-Id never gets set; (2) the log line can't tell if an exception occurred or what status will eventually be sent. The version above:

- Captures status from `response.status_code` on success.
- Captures status from `exc.http_status` when an AdminBackendError propagates (matches what the FastAPI exception handler will return).
- Defaults to 500 for unhandled exceptions.
- Sets X-Request-Id on the response *before* returning (success path only; error responses get X-Request-Id from the exception handler, see File 8).
- Logs every request including those that raised.

### File 4: `src/admin_backend/middleware/auth.py`

```python
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from admin_backend.auth.context import AuthContext
from admin_backend.auth.stub import StubAuthClient
from admin_backend.errors import AdminBackendError, AuthInvalidError, AuthMissingError


PUBLIC_PATHS = frozenset({
    "/v1/health",
    "/v1/openapi.json",
    "/v1/docs",
    "/v1/redoc",
    "/metrics",
})


class AuthMiddleware(BaseHTTPMiddleware):
    """JWT verification middleware.

    Reads auth_client from request.app.state at request time (Pattern 1: lazy
    access). The auth_client is constructed in main.py's lifespan and assigned
    to app.state.auth_client; lifespan runs after middleware __init__, so
    constructor injection is not viable.
    """

    async def dispatch(self, request: Request, call_next):
        if request.url.path in PUBLIC_PATHS:
            return await call_next(request)

        auth_header = request.headers.get("authorization", "")
        if not auth_header.startswith("Bearer "):
            raise AuthMissingError("Authorization header missing or malformed")

        jwt_string = auth_header[len("Bearer "):].strip()
        if not jwt_string:
            raise AuthMissingError("Bearer token is empty")

        # Point-of-use type annotation; app.state is typed as Any otherwise.
        auth_client: StubAuthClient = request.app.state.auth_client
        auth_context = auth_client.verify(jwt_string)
        request.state.auth = auth_context

        return await call_next(request)
```

Auth middleware **raises** `AuthMissingError` and `AuthInvalidError` (the latter via `auth_client.verify`). The exception handler in `main.py` (registered as part of this step) catches and returns the 401 JSON response. This avoids per-middleware response-formatting code.

### File 5: `src/admin_backend/dependencies.py`

```python
"""FastAPI dependency providers."""
from typing import AsyncIterator
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from admin_backend.auth.context import AuthContext
from admin_backend.db.session import get_tenant_session
from admin_backend.errors import AuthMissingError


def get_auth_context(request: Request) -> AuthContext:
    """Pull AuthContext from request.state (populated by AuthMiddleware).

    Raises AuthMissingError if auth wasn't set (caller hit a public path
    or the dependency was used outside the middleware chain).
    """
    auth = getattr(request.state, "auth", None)
    if auth is None:
        raise AuthMissingError("AuthContext missing from request.state; auth middleware did not run")
    return auth


def get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    """Pull the session factory from app.state (set in main.py lifespan)."""
    return request.app.state.session_factory


def get_request_id(request: Request) -> str | None:
    """Pull request_id from request.state (populated by AuditContextMiddleware)."""
    return getattr(request.state, "request_id", None)


async def get_tenant_session_dep(
    auth: AuthContext = Depends(get_auth_context),
    session_factory: async_sessionmaker = Depends(get_session_factory),
    request_id: str | None = Depends(get_request_id),
) -> AsyncIterator[AsyncSession]:
    """FastAPI-shaped wrapper around get_tenant_session.

    Bridges the dependency-injection layer to Step 2.2a's get_tenant_session.
    Passes request_id through so the dependency can set app.request_id GUC
    for audit triggers (Step 6.2).
    """
    async for session in get_tenant_session(auth, session_factory, request_id=request_id):
        yield session
```

### File 6: Update `src/admin_backend/db/session.py`

Add `request_id` parameter:

```python
async def get_tenant_session(
    auth: AuthContext,
    session_factory: async_sessionmaker[AsyncSession],
    request_id: str | None = None,
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        async with session.begin():
            # Existing: app.tenant_id and app.user_type
            tenant_id_value = str(auth.tenant_id) if auth.tenant_id is not None else None
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tid, true)"),
                {"tid": tenant_id_value},
            )
            await session.execute(
                text("SELECT set_config('app.user_type', :ut, true)"),
                {"ut": auth.user_type},
            )

            # New: app.request_id (NULL when not in a request context, e.g. tests)
            await session.execute(
                text("SELECT set_config('app.request_id', :rid, true)"),
                {"rid": request_id},
            )

            yield session
```

Existing Step 2.2a tests should pass; the new parameter has a default of None.

### File 7: `src/admin_backend/logging_config.py`

```python
import logging
import sys

from pythonjsonlogger import jsonlogger


def configure_logging(level: str = "INFO") -> None:
    """Configure stdout JSON logging."""
    handler = logging.StreamHandler(sys.stdout)
    formatter = jsonlogger.JsonFormatter(
        "%(asctime)s %(name)s %(levelname)s %(message)s",
        rename_fields={"asctime": "timestamp", "levelname": "level"},
    )
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
```

**PyPI name vs import name:** PyPI package is `python-json-logger` (with hyphens). Import as `from pythonjsonlogger import jsonlogger` (no hyphens, no underscores in the import path). Common gotcha.

**Version pinning:** add `python-json-logger>=2.0,<4.0` to `pyproject.toml`. v2.x is stable and tested; v3.x changed the import path slightly and isn't worth chasing for this step. The `rename_fields` kwarg requires v2.0+.

### File 8: `src/admin_backend/main.py` (skeleton; expanded at Step 2.4)

First, add a cached settings accessor. In `src/admin_backend/config.py`, add at the bottom:

```python
from functools import lru_cache

@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor. Construct once per process."""
    return Settings()
```

This is FastAPI's recommended pattern. Reading env vars is cheap, but we want a single source of truth across lifespan, create_app, and any other accessor.

Then `main.py`:

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from admin_backend.auth.stub import StubAuthClient
from admin_backend.config import get_settings
from admin_backend.db.engine import (
    assert_app_role_no_bypassrls,
    create_engine,
    create_session_factory,
)
from admin_backend.errors import AdminBackendError, ServerError
from admin_backend.logging_config import configure_logging
from admin_backend.middleware.audit_context import AuditContextMiddleware
from admin_backend.middleware.auth import AuthMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)

    engine = create_engine(settings)
    await assert_app_role_no_bypassrls(engine)
    session_factory = create_session_factory(engine)

    if settings.auth_client_mode == "STUB":
        auth_client = StubAuthClient(settings)
    else:
        # Step 2.4 / post-launch: Auth0Client wiring
        raise NotImplementedError("Only STUB auth client is wired in v0")

    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.auth_client = auth_client

    yield

    await engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()  # same cached instance as lifespan; no env re-parse

    app = FastAPI(
        title="Ithina Admin Backend",
        version="0.1.0",
        openapi_url="/v1/openapi.json",
        docs_url="/v1/docs",
        redoc_url="/v1/redoc",
        lifespan=lifespan,
    )

    # Middleware ordering: add in REVERSE of execution order.
    # CORS innermost (last to add → runs last on incoming, first on outgoing).
    # Auth middle.
    # Audit context outermost (last to add of the three → runs first on incoming).
    cors_origins = [o.strip() for o in settings.cors_allowed_origins.split(",") if o.strip()]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )
    app.add_middleware(AuthMiddleware)
    app.add_middleware(AuditContextMiddleware)

    # Exception handler (defined here so it has access to app)
    import logging
    logger = logging.getLogger("admin_backend.errors")

    @app.exception_handler(AdminBackendError)
    async def admin_backend_error_handler(request: Request, exc: AdminBackendError):
        request_id = getattr(request.state, "request_id", None)

        if isinstance(exc, ServerError):
            logger.error("server error", extra={
                "request_id": request_id,
                "exception_type": type(exc).__name__,
                "internal_message": exc.internal_message,
                "context": exc.context,
            })
            return JSONResponse(
                status_code=500,
                content={
                    "code": "INTERNAL_ERROR",
                    "message": "An internal error occurred",
                    "request_id": request_id,
                },
                headers={"X-Request-Id": request_id} if request_id else {},
            )

        return JSONResponse(
            status_code=exc.http_status,
            content={
                "code": exc.code,
                "message": exc.public_message,
                "request_id": request_id,
            },
            headers={"X-Request-Id": request_id} if request_id else {},
        )

    return app


app = create_app()
```

**Note on the auth_client lifecycle.** Middleware `__init__` runs at app construction, before lifespan. The auth_client is constructed *in* lifespan and assigned to `app.state.auth_client`. So AuthMiddleware can't take it as a constructor arg — it doesn't exist yet.

Pattern: lazy access via `request.app.state.auth_client` inside `dispatch()`. This is FastAPI's canonical pattern for resources constructed in lifespan (engines, HTTP clients, ML models, auth clients). Documented in FastAPI's lifespan docs.

Add a point-of-use type annotation so mypy can verify usage even though `app.state` is dynamically typed:

```python
class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path in PUBLIC_PATHS:
            return await call_next(request)

        auth_header = request.headers.get("authorization", "")
        if not auth_header.startswith("Bearer "):
            raise AuthMissingError("Authorization header missing or malformed")

        jwt_string = auth_header[len("Bearer "):].strip()
        if not jwt_string:
            raise AuthMissingError("Bearer token is empty")

        # Point-of-use annotation: app.state is typed as Any; this restores
        # type-checking for the rest of the function.
        auth_client: StubAuthClient = request.app.state.auth_client
        auth_context = auth_client.verify(jwt_string)
        request.state.auth = auth_context

        return await call_next(request)
```

No constructor; reads from app state at request time. Failure mode if lifespan didn't set auth_client: AttributeError on first request. The 10 integration tests in this step exercise this path; misconfiguration won't reach production.

If misconfiguration becomes a recurring problem at v0+ scale, the natural escalation is to wrap app.state in a Pydantic model (`app.state.app_data = AppState(auth_client=..., session_factory=..., ...)`) for fail-fast startup validation. Not needed for v0; keep this prompt's pattern.

### File 9: Tests — `tests/integration/test_middleware.py`

Use FastAPI's `TestClient`. Include a temporary protected route via a test fixture (not in `main.py`).

**JSON log capture pattern.** `caplog` (pytest's built-in) captures `LogRecord` objects, not formatted JSON output. To verify the actual JSON shape that ships to stdout, attach a buffer-backed handler with the same JsonFormatter:

```python
import io
import json
import logging

import pytest
from pythonjsonlogger import jsonlogger


@pytest.fixture
def json_log_buffer():
    """Attach a buffer-backed JSON handler to admin_backend.requests logger.
    Returns the buffer; tests parse JSON from it.
    """
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(jsonlogger.JsonFormatter(
        "%(asctime)s %(name)s %(levelname)s %(message)s",
        rename_fields={"asctime": "timestamp", "levelname": "level"},
    ))

    logger = logging.getLogger("admin_backend.requests")
    original_handlers = logger.handlers[:]
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False  # don't double-log to root

    yield buffer

    logger.handlers = original_handlers
```

Then in tests:

```python
def test_log_line_shape(client, json_log_buffer, valid_jwt):
    client.get("/v1/_test_protected", headers={"Authorization": f"Bearer {valid_jwt}"})
    log_lines = json_log_buffer.getvalue().strip().split("\n")
    assert len(log_lines) >= 1
    last_log = json.loads(log_lines[-1])
    assert last_log["request_id"]
    assert last_log["status"] == 200
    # ... etc
```

Test fixture pattern for the app (use `pytest_asyncio.fixture` for async cleanup; the project's `pyproject.toml` already has `asyncio_mode = "auto"` from Step 2.2a so no decorators needed):

```python
import pytest_asyncio
from fastapi import FastAPI, Request, Depends
from fastapi.testclient import TestClient
from sqlalchemy import text

from admin_backend.config import get_settings
from admin_backend.auth.stub import StubAuthClient
from admin_backend.auth.testing import make_test_jwt
from admin_backend.dependencies import get_auth_context, get_tenant_session_dep
from admin_backend.db.engine import create_engine, create_session_factory
from admin_backend.main import create_app


@pytest_asyncio.fixture
async def app_with_test_routes():
    """Build a FastAPI app with the middlewares + test-only routes.
    Manually wires app.state to skip the full lifespan (which connects to DB).
    Async fixture so engine.dispose() runs in the same event loop as the test.
    """
    settings = get_settings()
    app = create_app()

    # Manually wire app.state for tests; bypass lifespan startup for fixture speed.
    engine = create_engine(settings)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)
    app.state.auth_client = StubAuthClient(settings)

    # Test-only routes (include_in_schema=False keeps them out of OpenAPI).
    @app.get("/v1/_test_protected", include_in_schema=False)
    async def _test_protected(auth = Depends(get_auth_context)):
        return {
            "tenant_id": str(auth.tenant_id) if auth.tenant_id else None,
            "user_type": auth.user_type,
            "user_id": str(auth.user_id),
        }

    @app.get("/v1/_test_db_user_type", include_in_schema=False)
    async def _test_db_user_type(session = Depends(get_tenant_session_dep)):
        # End-to-end: middleware → request.state → dependency → DB session.
        # Returns whatever app.user_type is actually set to on the session.
        result = await session.execute(text("SELECT current_setting('app.user_type', TRUE)"))
        return {"db_user_type": result.scalar()}

    @app.get("/v1/health", include_in_schema=False)
    async def _test_health():
        return {"status": "ok"}

    yield app

    # Async cleanup; engine.dispose() awaits in the same event loop as the test.
    await engine.dispose()


@pytest.fixture
def client(app_with_test_routes):
    return TestClient(app_with_test_routes)
```

Note: `TestClient` (sync) sits on top of an async app. The `pytest_asyncio.fixture` async-yields the app from an async context; the sync `client` fixture wraps it. This combination works under pytest-asyncio's `auto` mode (set in pyproject.toml at Step 2.2a). If pytest-asyncio is not yet a dep, add `pytest-asyncio>=0.23` to `pyproject.toml`'s `[dependency-groups.dev]`.

Test cases:

T1. GET `/v1/health` without Authorization → 200, X-Request-Id present.

T2. GET `/v1/_test_protected` with no Authorization → 401, body has `code: "AUTH_MISSING"`, `request_id` set, `X-Request-Id` header set.

T3. GET `/v1/_test_protected` with `Authorization: Bearer ` (empty token) → 401, code `AUTH_MISSING`.

T4. GET `/v1/_test_protected` with `Authorization: Bearer invalid.token.string` → 401, code `AUTH_INVALID`.

T5. GET `/v1/_test_protected` with valid TENANT JWT → 200, response body contains the tenant_id from the JWT.

T6. Response headers include `X-Request-Id` (UUID format) on every response, success or failure.

T7. Per-request log line emitted with all expected fields. Use `json_log_buffer` fixture; parse last JSON line; assert all expected keys present.

T8. Same request_id appears in BOTH the `X-Request-Id` response header AND the log line's `request_id` field. (Verifies the audit-context-outermost ordering.)

T9. **Full-stack injection attempt.** GET `/v1/_test_db_user_type` with valid TENANT JWT AND a header `X-User-Type: PLATFORM`. Verify the response shows `db_user_type: "TENANT"` — the DB session sees TENANT because AuthContext.user_type came from the JWT, not the header. End-to-end verification of AI-MT-03 source-binding.

T10. Exception handler with ServerError: trigger an unhandled `ServerError` inside a temp route; verify response is 500 with `code: "INTERNAL_ERROR"` and `message: "An internal error occurred"` (NOT the subclass-specific message — that goes to the log only). Verify the log captures `exception_type` matching the subclass name. Anti-information-disclosure verification.

Total: 10 integration tests.

---

## Scope out

- Health endpoint implementation (Step 2.4).
- main.py final shape (Step 2.4 finalises).
- Auth0 client (post-launch).
- Audit log table writes (Step 6.2). The middleware logs to stdout; audit_logs table writes are a separate Step 6.2 concern.
- OpenTelemetry / tracing (deferred per architecture).
- Domain endpoints (Step 3.x onward).
- Rate limiting (post-launch).
- 404-vs-403 enforcement on tenant-mismatch (D-17). That's handler logic; this step's scope is middleware. Handlers landing at Step 3.x must surface 404 on RLS-filtered/missing rows per D-17. Add a `# Per D-17` comment in `dependencies.py` near `get_auth_context` if a natural place exists.

---

## Stop and ask if

- The middleware ordering produces an unexpected behaviour (e.g., audit context middleware doesn't see the response status set by auth middleware's exception path). FastAPI/Starlette's exception handler may run inside or outside the middleware chain depending on version; verify and surface if the ordering doesn't deliver "single request_id per request, log line emitted always."
- python-json-logger version doesn't accept `rename_fields` kwarg. Newer versions support it; older versions need a custom formatter subclass. Surface if a workaround is needed.
- The lazy `auth_client` access via `request.app.state.auth_client` causes test-fixture issues (e.g., the fixture builds an app without a lifespan and so app.state.auth_client isn't set). The fixture pattern in File 9 sets it explicitly to handle this.
- Any AuthContext flow path (middleware → dependencies → get_tenant_session) doesn't carry through correctly. The Depends provider is the bridge; test it explicitly.
- The `app.request_id` GUC addition to `get_tenant_session` causes Step 2.2a tests to fail. The new parameter has a default of None; existing tests should pass unchanged. If they don't, surface.

---

## Acceptance criteria

- 9 files created/modified per scope.
- All 10 integration tests pass.
- Step 2.1 stub auth tests (21) still pass — error class refactor is backwards-compatible.
- Step 2.2a engine + session tests (15) still pass — get_tenant_session new parameter has default None.
- mypy strict clean across all modules.
- check_setup 35/35.
- python-json-logger added to pyproject.toml if not present.
- A live `uvicorn admin_backend.main:app` starts cleanly (manual verification: ensure no runtime errors at startup).
- `request.state.auth.tenant_id` and `request.state.auth.user_type` correctly flow into the DB session via Depends provider; verifiable by querying `current_setting('app.tenant_id')` from a handler that takes `Depends(get_tenant_session_dep)`.

---

## Report (BEFORE proposing commit)

Per the per-step bundling convention, four bundles:

1. **Code changes:** all files with line counts.
2. **CLAUDE.md updates this step requires:** error-class hierarchy section update reflecting the refactor; "Current state" Completed list updated; possibly a new D-XX or update to D-03 documenting `app.request_id` as a third session var.
3. **BUILD_PLAN.md updates:** Step 2.3 status TODO → DONE; scope-in/acceptance corrections if drift exists.
4. **Prompt file:** `prompts/step-2_3-middleware.md` confirmed in commit set.

Plus: test results (10 new integration + 21 stub auth + 15 engine/session = 46 expected); mypy status; check_setup status; one sample log line output for verification of JSON shape.

Wait for explicit authorisation before staging or committing.

---

## End of prompt
