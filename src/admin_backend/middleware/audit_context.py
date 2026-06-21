"""Audit context middleware: wraps Auth.

Generates one request_id per request, captures IP and user_agent, and
emits one structured INFO log line per request via the
`admin_backend.requests` logger. The log line fires on success AND on
exception paths; status comes from the Response on success or from the
`AdminBackendError.http_status` on the typed exception path; defaults
to 500 on unhandled exceptions.

Wraps the Auth middleware (registered second-to-last via
`app.add_middleware`) so the request_id exists for auth-failed requests
too. The 401 response from the auth path carries the same request_id
that the log line emits.

CORS is outermost (registered last) so OPTIONS preflights can short-
circuit with 204 + Access-Control-* headers without going through audit
or auth, and so cross-origin auth-rejection 401s still get
Allow-Origin headers attached on the way out. This means OPTIONS
preflights are NOT audit-logged — that's a deliberate trade-off; CORS
preflights aren't user actions.

The try/except/finally shape is load-bearing. A try/finally without
explicit excepts has two bugs: (1) `return response` makes any
post-finally code unreachable, so X-Request-Id never gets attached, and
(2) the log line cannot tell whether the request raised or what status
will eventually be sent.
"""
import logging
import time
import uuid
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from admin_backend.errors import AdminBackendError

logger = logging.getLogger("admin_backend.requests")


class AuditContextMiddleware(BaseHTTPMiddleware):
    """Generates request_id, captures request metadata, emits one INFO
    log line per request."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id

        # IP: prefer X-Forwarded-For first IP if present.
        xff = request.headers.get("x-forwarded-for")
        request.state.ip = (
            xff.split(",")[0].strip()
            if xff
            else (request.client.host if request.client else None)
        )
        request.state.user_agent = request.headers.get("user-agent")

        start = time.perf_counter()

        # Status defaults to 500 (assume worst). Updated on success or
        # on the AdminBackendError-typed exception path.
        response_status = 500
        exception_logged: BaseException | None = None

        try:
            response: Response = await call_next(request)
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
            tenant_id = (
                str(auth.tenant_id)
                if auth is not None and auth.tenant_id is not None
                else None
            )
            user_id = str(auth.user_id) if auth is not None else None
            user_type = auth.user_type if auth is not None else None

            logger.info(
                "request completed",
                extra={
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
                    "exception": (
                        type(exception_logged).__name__
                        if exception_logged is not None
                        else None
                    ),
                },
            )
