"""FastAPI app entrypoint.

Step 2.3 introduces the skeleton: lifespan that constructs engine,
session_factory, and auth_client; create_app that registers
middleware + exception handler. Step 2.4 builds the health endpoint
and finalises the lifespan order with the runtime privilege check
wired as a startup gate.
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from admin_backend.audit.emit import (
    AUDITED_ROUTES,
    build_conflict_details,
    build_integrity_violation_details,
    build_internal_error_details,
    build_permission_denied_details,
    build_validation_failed_details,
    compose_conflict_result_label,
    emit_audit_event_in_new_transaction,
    request_id_for_request,
    route_template_for_request,
)
from admin_backend.auth.stub import StubAuthClient
from admin_backend.config import get_settings
from admin_backend.db.engine import (
    assert_app_role_no_bypassrls,
    create_engine,
    create_session_factory,
)
from admin_backend.errors import (
    AdminBackendError,
    SelfEditForbiddenError,
    ServerError,
    build_error_payload,
)
from admin_backend.models.audit_log import AuditResultType
from admin_backend.logging_config import configure_logging
from admin_backend.middleware.audit_context import AuditContextMiddleware
from admin_backend.middleware.auth import AuthMiddleware
from admin_backend.routers.v1 import audit as audit_router
from admin_backend.routers.v1 import dashboard as dashboard_router
from admin_backend.routers.v1 import lookups as lookups_router
from admin_backend.routers.v1 import me as me_router
from admin_backend.routers.v1 import modules_access as modules_access_router
from admin_backend.routers.v1 import org_tree as org_tree_router
from admin_backend.routers.v1 import platform_users as platform_users_router
from admin_backend.routers.v1 import rbac as rbac_router
from admin_backend.routers.v1 import role_assignments as role_assignments_router
from admin_backend.routers.v1 import stores as stores_router
from admin_backend.routers.v1 import tenant_users as tenant_users_router
from admin_backend.routers.v1 import tenants as tenants_router


SERVICE_NAME = "admin-backend"
READINESS_TIMEOUT_SECONDS = 2.0
# SERVICE_VERSION removed: now lives on Settings.service_version. Default
# is the installed wheel's metadata version (pyproject.toml); deployed
# images override via the SERVICE_VERSION env var (baked into the
# Dockerfile at build time via --build-arg, or set at deploy time on
# Cloud Run / GKE) so /api/v1/health matches the image tag.


error_logger = logging.getLogger("admin_backend.errors")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Construct DB engine, session factory, and auth client; expose
    them on app.state for middleware and dependencies to read at
    request time.

    State is assigned to app.state as each resource is constructed
    (rather than batched at the end). If a startup gate later in the
    sequence raises, the partial state is still visible for inspection
    and disposal — for example, an AppRolePrivilegeError leaves
    app.state.engine populated so the caller can dispose it cleanly.
    """
    settings = get_settings()
    app.state.settings = settings
    configure_logging(settings.log_level)

    engine = create_engine(settings)
    app.state.engine = engine

    await assert_app_role_no_bypassrls(engine)

    session_factory = create_session_factory(engine)
    app.state.session_factory = session_factory

    if settings.auth_client_mode == "STUB":
        auth_client = StubAuthClient(settings)
    else:
        raise NotImplementedError(
            "Auth0Client implementation pending Auth0 tenant "
            "configuration (expected within a few days). Until it "
            "lands, AUTH_CLIENT_MODE must be 'STUB'. Production "
            "cutover blocks on Auth0Client per D-07."
        )
    app.state.auth_client = auth_client

    yield

    await engine.dispose()


def create_app() -> FastAPI:
    """Construct the FastAPI app with middleware and exception handler.

    Middleware ordering matters. Starlette runs middlewares in the
    REVERSE order they're added on the request side. The desired stack:
      CORS outermost  — short-circuits OPTIONS preflights with 204 +
                        Access-Control-* headers; adds Allow-Origin to
                        every cross-origin response (incl. auth-rejected
                        401s, so browsers can read the failure body).
      Audit middle    — sets request_id and runs the request-completed
                        log line; wraps Auth so auth-rejected requests
                        still get audited with their request_id.
      Auth innermost  — checks JWT and sets request.state.auth.
    Add in REVERSE so the outermost is added last:
      add Auth first  -> innermost
      add Audit next  -> middle
      add CORS last   -> outermost
    """
    settings = get_settings()

    app = FastAPI(
        title="Ithina Admin Backend",
        version=settings.service_version,
        openapi_url=f"{settings.api_prefix}/openapi.json",
        docs_url=f"{settings.api_prefix}/docs",
        redoc_url=f"{settings.api_prefix}/redoc",
        lifespan=lifespan,
    )

    cors_origins = [
        o.strip()
        for o in settings.cors_allowed_origins.split(",")
        if o.strip()
    ]

    app.add_middleware(AuthMiddleware)
    app.add_middleware(AuditContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
    )

    @app.get(
        f"{settings.api_prefix}/health",
        tags=["meta"],
        summary="Liveness probe",
    )
    async def health() -> dict[str, str]:
        """Liveness probe. Returns 200 unconditionally. No DB access.

        Kubernetes uses this to decide whether the pod is alive; a
        failure here means kill-and-restart, so it must respond fast
        even when the DB is down or the app is misconfigured.
        """
        return {
            "status": "ok",
            "service": SERVICE_NAME,
            "version": settings.service_version,
        }

    @app.get(
        f"{settings.api_prefix}/ready",
        tags=["meta"],
        summary="Readiness probe",
    )
    async def ready(request: Request) -> JSONResponse:
        """Readiness probe. Returns 200 if DB is reachable, 503
        otherwise.

        Kubernetes uses this to decide whether to route traffic to
        this pod. Bounded by READINESS_TIMEOUT_SECONDS so a hung DB
        does not stall the probe past what the readiness window
        permits. Specifics of any failure are logged via the audit
        middleware's exception path; the response body is generic.
        """
        engine = request.app.state.engine

        async def _ping() -> None:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))

        try:
            await asyncio.wait_for(
                _ping(), timeout=READINESS_TIMEOUT_SECONDS
            )
            return JSONResponse(
                status_code=200,
                content={"status": "ready", "db": "ok"},
            )
        except Exception:
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "db": "error"},
            )

    app.include_router(
        tenants_router.router, prefix=settings.api_prefix
    )
    app.include_router(
        lookups_router.router, prefix=settings.api_prefix
    )
    app.include_router(
        platform_users_router.router, prefix=settings.api_prefix
    )
    app.include_router(
        tenant_users_router.router, prefix=settings.api_prefix
    )
    app.include_router(
        org_tree_router.router, prefix=settings.api_prefix
    )
    app.include_router(
        stores_router.router, prefix=settings.api_prefix
    )
    app.include_router(
        rbac_router.roles_router, prefix=settings.api_prefix
    )
    app.include_router(
        rbac_router.permissions_router, prefix=settings.api_prefix
    )
    app.include_router(
        rbac_router.matrix_router, prefix=settings.api_prefix
    )
    app.include_router(
        dashboard_router.router, prefix=settings.api_prefix
    )
    app.include_router(
        modules_access_router.router, prefix=settings.api_prefix
    )
    app.include_router(
        role_assignments_router.router, prefix=settings.api_prefix
    )
    app.include_router(
        me_router.router, prefix=settings.api_prefix
    )
    app.include_router(
        audit_router.router, prefix=settings.api_prefix
    )

    @app.exception_handler(AdminBackendError)
    async def admin_backend_error_handler(
        request: Request, exc: AdminBackendError
    ) -> JSONResponse:
        """Build the JSON error response. ServerError specifics are
        logged here (anti-information-disclosure: response stays
        generic). ClientError surfaces its subclass-specific fields.
        TODO: handlers must surface 404 (not 403) on RLS-filtered
        rows per D-17.

        Note: this handler runs only for exceptions raised inside
        route handlers. Middleware-raised exceptions are converted to
        responses inline (see middleware/auth.py); both paths use the
        shared build_error_payload helper.

        Step 6.16.2 hook: after the response envelope is built, if the
        matched route is in ``AUDITED_ROUTES`` and the request has an
        AuthContext, emit a failure-path audit row in a separate
        transaction. The row records what was attempted and which
        result_type fired; per the design doc Emission contract
        refinement (LD15), this happens AFTER the data transaction
        has rolled back, in its own new transaction. If the audit
        emission itself fails (rare; constraint violation due to bug),
        the helper logs CRITICAL and continues; the user-facing error
        envelope is the visible response.
        """
        request_id = getattr(request.state, "request_id", None)

        if isinstance(exc, ServerError):
            error_logger.error(
                "server error",
                extra={
                    "request_id": request_id,
                    "exception_type": type(exc).__name__,
                    "internal_message": exc.internal_message,
                    "context": exc.context,
                },
            )

        # Step 6.16.2 failure-path audit emission. Skip if no auth
        # (request never authenticated) or no route match.
        await _emit_failure_audit_if_audited(request, exc)

        status, body, headers = build_error_payload(exc, request_id)
        return JSONResponse(
            status_code=status, content=body, headers=headers
        )

    return app


# ---------------------------------------------------------------------------
# Per-route extractor mapping (Step 6.16.5 LD12; closes FN-AB-66)
# ---------------------------------------------------------------------------
#
# Each AUDITED_ROUTES resource_type declares one extractor. The
# failure-path handler resolves the route template, reads the
# AUDITED_ROUTES tuple's resource_type, consults ``RESOURCE_EXTRACTORS``,
# and invokes the extractor to obtain a ``FailureContext`` carrying
# (resource_id, tenant_id_for_row, lookup-hint kwargs).
# ``emit_audit_event_in_new_transaction`` then runs the resource-type-
# specific tenant_name / resource_label lookups inside the new
# transaction (where the JOIN-source tables are visible after the data
# transaction has rolled back).
#
# Replaces the 6.16.4 minimal multi-key fallthrough; documented in
# ``docs/architecture_audit_logs.md`` Emission contract section.


@dataclass(frozen=True)
class FailureContext:
    """Path-derived context for failure-path emission.

    Carries what the URL path provides; the new-transaction lookups
    inside ``emit_audit_event_in_new_transaction`` resolve
    tenant_name + resource_label using the appropriate lookup table
    keyed by ``resource_type``.

    Per-resource fields (``module_code``, ``node_id``, ``store_id``)
    let emit pick the right JOIN / SELECT shape per type without
    sniffing the URL itself.
    """

    resource_id: UUID | None = None
    tenant_id_for_row: UUID | None = None
    module_code: str | None = None
    node_id: UUID | None = None
    store_id: UUID | None = None


def _parse_uuid_or_none(value: Any) -> UUID | None:
    if value is None:
        return None
    try:
        return UUID(str(value))
    except (ValueError, AttributeError):
        return None


def _extract_tenant(request: Request) -> FailureContext:
    tid = _parse_uuid_or_none(request.path_params.get("tenant_id"))
    return FailureContext(resource_id=tid, tenant_id_for_row=tid)


def _extract_tenant_user(request: Request) -> FailureContext:
    uid = _parse_uuid_or_none(request.path_params.get("user_id"))
    # tenant_id is back-filled by emit's JOIN against tenant_users.
    return FailureContext(resource_id=uid)


def _extract_role(request: Request) -> FailureContext:
    rid = _parse_uuid_or_none(request.path_params.get("role_id"))
    # Catalogue is platform-scope; tenant_id stays None.
    return FailureContext(resource_id=rid)


def _extract_module_access(request: Request) -> FailureContext:
    tid = _parse_uuid_or_none(request.path_params.get("tenant_id"))
    raw_mc = request.path_params.get("module_code")
    return FailureContext(
        resource_id=None,  # tenant_module_access.id resolved by emit
        tenant_id_for_row=tid,
        module_code=str(raw_mc) if raw_mc is not None else None,
    )


def _extract_org_node(request: Request) -> FailureContext:
    tid = _parse_uuid_or_none(request.path_params.get("tenant_id"))
    nid = _parse_uuid_or_none(request.path_params.get("node_id"))
    return FailureContext(
        resource_id=nid,  # None on POST add-node (no path id)
        tenant_id_for_row=tid,
        node_id=nid,
    )


def _extract_store(request: Request) -> FailureContext:
    sid = _parse_uuid_or_none(request.path_params.get("store_id"))
    return FailureContext(
        resource_id=sid,  # None on POST /stores
        store_id=sid,
    )


RESOURCE_EXTRACTORS: dict[
    str, Callable[[Request], FailureContext]
] = {
    "TENANT": _extract_tenant,
    "TENANT_USER": _extract_tenant_user,
    "ROLE": _extract_role,
    "MODULE_ACCESS": _extract_module_access,
    "ORG_NODE": _extract_org_node,
    "STORE": _extract_store,
}


async def _emit_failure_audit_if_audited(
    request: Request, exc: AdminBackendError
) -> None:
    """Hook the failure-path audit emission per Step 6.16.2 LD8.

    Lookup the request's matched route in ``AUDITED_ROUTES``; if not
    present, do nothing (the endpoint is not audited at this step).
    If the request has no AuthContext (auth failed or was skipped),
    do nothing (v0 deferral: unauthenticated attempts not audited).

    Maps the exception type to ``AuditResultType`` via class shape
    (ClientError 403 -> PERMISSION_DENIED, 422 -> VALIDATION_FAILED,
    409 -> CONFLICT; ServerError -> INTERNAL_ERROR).

    Pydantic's ``RequestValidationError`` is NOT caught by this
    handler. Audit emission for the 422-from-Pydantic path is deferred
    per FN-AB-63.

    Step 6.16.5 LD12: the 6.16.4 minimal path-param fallthrough is
    replaced by the ``RESOURCE_EXTRACTORS`` sibling dict (FN-AB-66
    closure). The failure handler consults the dict keyed by
    resource_type and dispatches to the per-resource extractor.
    """
    auth = getattr(request.state, "auth", None)
    if auth is None:
        return

    route_template = route_template_for_request(request)
    if route_template is None:
        return

    key = (request.method, route_template)
    audited = AUDITED_ROUTES.get(key)
    if audited is None:
        return

    # 404 NOT_FOUND : the resource doesn't exist (genuinely missing or
    # RLS-filtered per D-17). There is no resource to associate the
    # attempt with; nothing meaningful to audit. Anchor-404 paths
    # (module-access disable on missing, org-tree PATCH on missing
    # node, stores PATCH / set-status on missing store) all flow here.
    if exc.http_status == 404:
        return

    action, resource_type, route_to_platform = audited
    result_type, details = _failure_result_and_details(
        exc, route_template, auth=auth
    )
    request_id = request_id_for_request(request)

    extractor = RESOURCE_EXTRACTORS.get(resource_type)
    if extractor is None:  # pragma: no cover - defensive
        # AUDITED_ROUTES carries a resource_type with no registered
        # extractor; treat as 'no path-derived context' rather than
        # silently skipping emission.
        ctx = FailureContext()
    else:
        ctx = extractor(request)

    engine = getattr(request.app.state, "engine", None)
    if engine is None:
        # Lifespan didn't run (e.g., test client without lifespan
        # wiring); silently skip. Tests that need failure-path
        # emission set up the engine.
        return

    # Step 6.16.7 LD9 : CONFLICT rows get a per-class qualifier-composed
    # ``result_label`` ("Blocked - <qualifier>"). Other result_types
    # fall through to the default static label resolved inside emit.
    composed_result_label: str | None = None
    if result_type == AuditResultType.CONFLICT:
        composed_result_label = compose_conflict_result_label(exc.code)

    await emit_audit_event_in_new_transaction(
        engine,
        auth=auth,
        action=action,
        resource_type=resource_type,
        resource_id=ctx.resource_id,
        resource_label=None,
        result_type=result_type,
        result_label=composed_result_label,
        details=details,
        tenant_id=ctx.tenant_id_for_row,
        tenant_name=None,
        request_id=request_id,
        route_to_platform=route_to_platform,
        module_code=ctx.module_code,
        node_id=ctx.node_id,
        store_id=ctx.store_id,
    )


def _failure_result_and_details(
    exc: AdminBackendError,
    route_template: str,
    *,
    auth: Any = None,
) -> tuple[AuditResultType, dict[str, Any]]:
    """Map an AdminBackendError subclass to (result_type, details).

    The mapping uses class shape signals (http_status + code) rather
    than isinstance checks against every subclass; adding a new
    subclass at 6.16.4 / 6.16.5 doesn't require updating this
    function.

    Step 6.16.4: ``auth`` is the request's resolved ``AuthContext``;
    used to fall back ``caller_audience`` to ``auth.user_type`` when
    the raise site didn't set it explicitly (gate raises and
    handler-side guards typically don't).
    """
    code = exc.code
    http_status = exc.http_status
    context = exc.context

    if isinstance(exc, ServerError):
        # Step 6.16.4 LD12: a Layer 2 invariant tripwire raise (e.g.
        # ``InternalInvariantViolationError`` from
        # ``RolesRepo.update``) passes ``invariant=...`` via
        # ``**context`` on the ServerError constructor; surface it
        # into the INTERNAL_ERROR details for forensic clarity.
        invariant = context.get("invariant")
        details = build_internal_error_details(
            error_class=type(exc).__name__,
            invariant=str(invariant) if invariant is not None else None,
        )
        return AuditResultType.INTERNAL_ERROR, details

    # ClientError subclasses.
    if http_status == 403:
        # Permission-denied family. ``code`` distinguishes:
        # PLATFORM_AUDIENCE_REQUIRED (Layer 1 audience refusal),
        # PERMISSION_DENIED (Layer 2 has_permission denial),
        # SELF_EDIT_FORBIDDEN (handler-side guard at Step 6.10.1).
        required = (
            context.get("required_permission")
            or _required_permission_from_code(code)
        )
        # Step 6.16.4: caller_audience falls back to auth.user_type
        # when the raise site (gate / handler-side guard) didn't set
        # it explicitly. Gate raises carry module / resource / action
        # / scope but not caller-shape info; the JWT identity is the
        # authoritative source for audience.
        caller_audience = context.get("caller_audience")
        if not caller_audience and auth is not None:
            caller_audience = getattr(auth, "user_type", "") or ""
        caller_audience = caller_audience or ""
        caller_roles = context.get("caller_roles") or []
        # Step 6.16.4 LD11: handler-side guard denials carry an
        # optional ``denial_reason`` sub-key. SelfEditForbiddenError
        # has no structured context constructor (see pre-flight
        # Observation #3); dispatch by class type.
        denial_reason: str | None = None
        if isinstance(exc, SelfEditForbiddenError):
            denial_reason = "SELF_EDIT_FORBIDDEN"
        details = build_permission_denied_details(
            required_permission=str(required),
            caller_audience=str(caller_audience),
            caller_roles=list(caller_roles),
            denial_reason=denial_reason,
        )
        return AuditResultType.PERMISSION_DENIED, details

    if http_status == 409:
        # Conflict family: DUPLICATE_TENANT_NAME, INVALID_STATE_TRANSITION.
        constraint = code
        field = context.get("field")
        value = context.get("name") or context.get("value")
        details = build_conflict_details(
            constraint=constraint,
            field=str(field) if field is not None else None,
            value=str(value) if value is not None else None,
        )
        return AuditResultType.CONFLICT, details

    if http_status == 422:
        # Validation family raised by the codebase's own ClientErrors
        # (e.g., EMPTY_PATCH, INVALID_TENANT_NAME_FOR_SLUG).
        # FastAPI/Pydantic's RequestValidationError is not handled here
        # (FN-AB-63).
        details = build_validation_failed_details(
            validation_errors=[
                {"field": context.get("field"), "error_message": exc.public_message}
            ]
        )
        return AuditResultType.VALIDATION_FAILED, details

    # Fallback: surface as INTERNAL_ERROR with class name.
    return (
        AuditResultType.INTERNAL_ERROR,
        build_internal_error_details(error_class=type(exc).__name__),
    )


def _required_permission_from_code(code: str) -> str:
    """Best-effort code -> permission tuple string for the audit row.

    The audit row's `required_permission` field is informational; the
    forensic-correctness lookup happens via the gate's own logs. This
    helper produces a useful default when the exception didn't carry
    a structured `required_permission` in its context.
    """
    if code == "PLATFORM_AUDIENCE_REQUIRED":
        return "<audience=PLATFORM>"
    if code == "PERMISSION_DENIED":
        return "<see gate logs>"
    return f"<{code}>"


app = create_app()
