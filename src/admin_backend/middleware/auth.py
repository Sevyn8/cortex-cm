"""Auth middleware: extracts JWT, verifies, populates request.state.auth.

Reads `auth_client` lazily from `request.app.state` at dispatch time
because the middleware is constructed before lifespan runs (lifespan
populates `app.state.auth_client`). Constructor injection is not viable
for that reason; lazy access is the FastAPI-canonical pattern.

Public paths (health, openapi, docs, redoc, metrics) skip the JWT check
entirely; `request.state.auth` is left unset for those. Handlers that
must be reachable on a public path therefore must not depend on
`get_tenant_session_dep`, which raises `AuthMissingError` when auth is
absent (per AI-MT-03 source-binding: no auth → no DB session).

Auth failures (AdminBackendError subclasses) are caught here and
converted into JSON responses via `build_error_payload`. We do NOT
re-raise from the middleware: Starlette's BaseHTTPMiddleware does not
route exceptions raised in `dispatch` through FastAPI's
`@app.exception_handler` chain (the exception handler runs INSIDE the
middleware stack, so anything raised in user middleware propagates UP
past it to ServerErrorMiddleware, which returns a generic 500). The
shared `build_error_payload` helper keeps the response shape identical
to the FastAPI exception handler in main.py.
"""
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from admin_backend.auth.base import AuthClient
from admin_backend.errors import (
    AdminBackendError,
    AuthMissingError,
    build_error_payload,
)


PUBLIC_PATHS = frozenset({
    "/api/v1/health",
    "/api/v1/ready",
    "/api/v1/openapi.json",
    "/api/v1/docs",
    "/api/v1/redoc",
    "/metrics",
})


class AuthMiddleware(BaseHTTPMiddleware):
    """JWT verification middleware."""

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        if request.url.path in PUBLIC_PATHS:
            return await call_next(request)  # type: ignore[no-any-return]

        try:
            auth_header = request.headers.get("authorization", "")
            if not auth_header.startswith("Bearer "):
                raise AuthMissingError(
                    "Authorization header missing or malformed"
                )

            jwt_string = auth_header[len("Bearer "):].strip()
            if not jwt_string:
                raise AuthMissingError("Bearer token is empty")

            # Point-of-use type annotation; app.state is dynamically typed.
            auth_client: AuthClient = request.app.state.auth_client
            auth_context = auth_client.verify(jwt_string)
            request.state.auth = auth_context
        except AdminBackendError as exc:
            request_id = getattr(request.state, "request_id", None)
            status, body, headers = build_error_payload(exc, request_id)
            return JSONResponse(
                status_code=status, content=body, headers=headers
            )

        return await call_next(request)  # type: ignore[no-any-return]
