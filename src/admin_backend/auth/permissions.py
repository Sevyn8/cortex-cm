"""Permission-decision code: ``has_permission``, ``get_permissions_for_user``, ``require``.

Three callables live here. They share JOIN structure but answer different
questions:

- ``has_permission(session, auth, M, R, A, S, target_anchor)`` answers
  "can this user perform this one tuple at this anchor?" via a single
  ``SELECT 1 ... LIMIT 1`` query. Used by the FastAPI gate and by
  ``/me/can-do``. Step 6.9.3.1: the per-tuple ``scope`` filter accepts
  any scope in the downward-cascade satisfaction set for the requested
  scope, so a GLOBAL grant satisfies TENANT/STORE checks and a TENANT
  grant satisfies STORE checks. See ``satisfying_scopes()``.

- ``get_permissions_for_user(session, auth)`` answers "what is this
  user's full grant set?" by returning every matching row as a
  ``PermissionGrant`` dataclass. Used by ``/me/permissions``. Same JOIN
  structure as ``has_permission`` with the per-tuple ``WHERE`` clauses
  dropped and the projection widened to include
  ``module/resource/action/scope/anchor_path``. Cascade expansion is
  NOT applied here (the gate / ``/me/can-do`` is the cascade-aware
  surface; ``/me/permissions`` returns raw grants).

- ``require(M, R, A, S)`` is a FastAPI dependency factory (Step 6.9.2,
  introduces the dependency-factory pattern). Returns an async callable
  that FastAPI injects via ``Depends(require(...))``. The callable runs
  ``has_permission`` against the request's session + auth and raises
  ``PermissionDeniedError`` on denial. ``target_anchor`` is hardcoded to
  ``None`` for 6.9.2; per-resource anchor dependencies and threading
  land in Step 6.9.3.2.

Audience dispatch via ``auth.user_type``:

- PLATFORM path joins ``platform_user_role_assignments``,
  ``role_permissions``, and ``permissions``. No anchor cascade
  (PLATFORM grants apply globally) and no ``tenant_module_access``
  filter (Ithina staff administer modules including those not yet
  enabled for any tenant).

- TENANT path adds ``org_nodes`` (composite key
  ``(tenant_id, org_node_id)`` per D-34 / AI-RBAC-06) and
  ``tenant_module_access`` (``status='ENABLED'`` filter). Anchor
  cascade uses Postgres ``ltree <@`` so a grant anchored at any
  ancestor of ``target_anchor`` matches.

The audience-check triggers (Step 6.8.1, migration ``3e05299cb533``)
guarantee that ``platform_user_role_assignments`` rows reference only
``role.audience='PLATFORM'`` and that ``tenant_user_role_assignments``
rows reference only ``role.audience='TENANT'``. The dispatch trusts
these triggers; no app-layer audience filter is needed.

Schema qualification follows the raw-SQL convention (D-15 / Step 6.5.1):
``schema = get_settings().db_schema`` resolved per-call and
f-string-interpolated into table references. ``db_schema`` is
identifier-validated at Settings construction; safe to interpolate.

Per-request DB read, no caching in v0. FN-AB-24 tracks revisit at scale.
"""
from __future__ import annotations

from typing import Awaitable, Callable, Literal
from uuid import UUID

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from admin_backend.auth.context import AuthContext
from admin_backend.auth.gate_info import PermissionGateInfo
from admin_backend.auth.permission_grant import PermissionGrant
from admin_backend.auth.reason_code import ReasonCode
from admin_backend.config import get_settings
from admin_backend.dependencies import get_auth_context, get_tenant_session_dep
from admin_backend.errors import (
    PermissionDeniedError,
    PlatformAudienceRequiredError,
)
from admin_backend.models.permission import (
    PermissionAction,
    PermissionResource,
    PermissionScope,
)
from admin_backend.models.tenant_module_access import ModuleCode


# Type alias for the per-resource anchor dependency callable.
# Anchor deps return an ltree path string (never None — they raise 404
# on lookup miss per F-THREADING-4). ``Awaitable[str]`` instead of
# ``Awaitable[str | None]`` is the load-bearing type contract.
AnchorDep = Callable[..., Awaitable[str]]


# ---------------------------------------------------------------------------
# Scope cascade (Step 6.9.3.1)
# ---------------------------------------------------------------------------
#
# Downward cascade: a grant at a higher scope level satisfies checks at any
# lower scope level. GLOBAL satisfies GLOBAL/TENANT/STORE; TENANT satisfies
# TENANT/STORE; STORE satisfies STORE only.
#
# IMPORTANT — coupling to org hierarchy:
# This tuple mirrors the org-tree hierarchy. Two in-repo sync points must
# stay aligned (see CLAUDE.md "Org hierarchy coupling"):
#   1. DDL ``org_node_type_enum`` in ``db/raw_ddl/...`` (7 values).
#   2. ``_SCOPE_CASCADE_ORDER`` here (8 entries: ``GLOBAL`` at position 0
#      representing the implicit Platform cascade root, plus the 7
#      ``org_node_type_enum`` values).
#
# Strings (not ``PermissionScope`` enum members) so the tuple lists levels
# that don't yet exist in the v0 ``permission_scope_enum`` (today only
# GLOBAL/TENANT/STORE; the other 5 are inert until the DB enum expands).
# The unit test ``test_scope_cascade_order_includes_all_enum_values``
# catches drift if the enum expands without updating the tuple.
_SCOPE_CASCADE_ORDER: tuple[str, ...] = (
    "GLOBAL",          # Platform — implicit cascade root above any tenant
    "TENANT",
    "BUSINESS_UNIT",
    "HQ",
    "COUNTRY",
    "REGION",
    "STORE",
    "DEPARTMENT",      # lowest
)


def satisfying_scopes(requested: PermissionScope) -> list[str]:
    """Return the scopes whose grants satisfy a check at ``requested``.

    Downward cascade: a grant at level N satisfies checks at every level
    below N. The returned list contains every scope value at-or-above
    ``requested`` in ``_SCOPE_CASCADE_ORDER``.

    Returns ``list[str]`` (not ``list[PermissionScope]``) so the helper
    can list forward-compat levels that aren't yet in the v0
    ``PermissionScope`` enum (``BUSINESS_UNIT``, ``HQ``, ``COUNTRY``,
    ``REGION``, ``DEPARTMENT``). The SQL call site filters this list
    against the current enum before binding to ``permission_scope_enum[]``
    via ``_satisfying_scopes_for_sql``.

    Examples (v0):
        satisfying_scopes(PermissionScope.GLOBAL)
            -> ["GLOBAL"]
        satisfying_scopes(PermissionScope.TENANT)
            -> ["GLOBAL", "TENANT"]
        satisfying_scopes(PermissionScope.STORE)
            -> ["GLOBAL", "TENANT", "BUSINESS_UNIT", "HQ", "COUNTRY",
                "REGION", "STORE"]
    """
    requested_value = requested.value
    if requested_value not in _SCOPE_CASCADE_ORDER:
        # Defensive: never reached if the enum-vs-tuple unit test is
        # green. Falls back to exact-match (single scope) rather than
        # crashing.
        return [requested_value]
    idx = _SCOPE_CASCADE_ORDER.index(requested_value)
    return list(_SCOPE_CASCADE_ORDER[: idx + 1])


# Cached frozenset of current ``permission_scope_enum`` member values.
# Used by ``_satisfying_scopes_for_sql`` to filter the helper's full
# forward-compat list down to values the DB enum cast can accept.
_PERMISSION_SCOPE_ENUM_VALUES: frozenset[str] = frozenset(
    s.value for s in PermissionScope
)


def _satisfying_scopes_for_sql(requested: PermissionScope) -> list[str]:
    """SQL-bindable variant of ``satisfying_scopes``.

    Filters the helper's full output to scopes that are valid
    ``permission_scope_enum`` members. Postgres rejects strings outside
    the enum at ``CAST(... AS permission_scope_enum[])`` time, so
    forward-compat levels (``BUSINESS_UNIT``, etc.) must not be bound to
    the array parameter until the DB enum includes them. Until that
    happens those levels are inert: no catalogue rows reference them
    either.

    Equivalent to ``[s for s in satisfying_scopes(requested) if s in
    PermissionScope._value2member_map_]``.
    """
    return [
        s for s in satisfying_scopes(requested)
        if s in _PERMISSION_SCOPE_ENUM_VALUES
    ]


async def has_permission(
    session: AsyncSession,
    auth: AuthContext,
    module: ModuleCode,
    resource: PermissionResource,
    action: PermissionAction,
    scope: PermissionScope,
    target_anchor: str | None = None,
) -> tuple[bool, ReasonCode, str]:
    """Single-tuple permission check.

    Asks: does this user have an ACTIVE assignment of an ACTIVE role
    that grants the requested ``(module, resource, action, scope)``
    tuple, at an anchor that covers ``target_anchor``?

    For TENANT callers also requires the tenant's ``tenant_module_access``
    row for ``module`` to be ``ENABLED``.

    For PLATFORM callers ``target_anchor`` is accepted but ignored;
    PLATFORM grants apply globally. Callers should pass ``None`` (the
    default) for PLATFORM users.

    Returns ``(allowed, code, developer_detail)`` where
    ``developer_detail`` is a verbose string for application logs and
    not surfaced to the user.
    """
    if auth.user_type == "PLATFORM":
        return await _has_permission_platform(
            session,
            user_id=auth.user_id,
            module=module,
            resource=resource,
            action=action,
            scope=scope,
            target_anchor=target_anchor,
        )
    return await _has_permission_tenant(
        session,
        user_id=auth.user_id,
        module=module,
        resource=resource,
        action=action,
        scope=scope,
        target_anchor=target_anchor,
    )


async def _has_permission_platform(
    session: AsyncSession,
    *,
    user_id: UUID,
    module: ModuleCode,
    resource: PermissionResource,
    action: PermissionAction,
    scope: PermissionScope,
    target_anchor: str | None,
) -> tuple[bool, ReasonCode, str]:
    schema = get_settings().db_schema
    sql = text(
        f"""
        SELECT 1
        FROM {schema}.platform_user_role_assignments AS pura
        JOIN {schema}.role_permissions AS rp
            ON rp.role_id = pura.role_id
        JOIN {schema}.permissions AS p
            ON p.id = rp.permission_id
        WHERE pura.platform_user_id = :user_id
          AND pura.status = CAST('ACTIVE' AS {schema}.user_role_assignment_status_enum)
          AND p.module   = CAST(:module   AS {schema}.module_code_enum)
          AND p.resource = CAST(:resource AS {schema}.resource_enum)
          AND p.action   = CAST(:action   AS {schema}.action_enum)
          AND p.scope = ANY(CAST(:satisfying_scopes AS {schema}.permission_scope_enum[]))
        LIMIT 1
        """
    )
    result = await session.execute(
        sql,
        {
            "user_id": user_id,
            "module": module.value,
            "resource": resource.value,
            "action": action.value,
            "satisfying_scopes": _satisfying_scopes_for_sql(scope),
        },
    )
    matched = result.first() is not None
    return _build_result(
        matched=matched,
        user_id=user_id,
        module=module,
        resource=resource,
        action=action,
        scope=scope,
        target_anchor=target_anchor,
        anchor_display="platform-side",
    )


async def _has_permission_tenant(
    session: AsyncSession,
    *,
    user_id: UUID,
    module: ModuleCode,
    resource: PermissionResource,
    action: PermissionAction,
    scope: PermissionScope,
    target_anchor: str | None,
) -> tuple[bool, ReasonCode, str]:
    schema = get_settings().db_schema
    sql = text(
        f"""
        SELECT 1
        FROM {schema}.tenant_user_role_assignments AS tura
        JOIN {schema}.role_permissions AS rp
            ON rp.role_id = tura.role_id
        JOIN {schema}.permissions AS p
            ON p.id = rp.permission_id
        JOIN {schema}.org_nodes AS on_
            ON on_.tenant_id = tura.tenant_id
           AND on_.id = tura.org_node_id
        JOIN {schema}.tenant_module_access AS tma
            ON tma.tenant_id = tura.tenant_id
           AND tma.module = p.module
        WHERE tura.tenant_user_id = :user_id
          AND tura.status = CAST('ACTIVE' AS {schema}.user_role_assignment_status_enum)
          AND p.module   = CAST(:module   AS {schema}.module_code_enum)
          AND p.resource = CAST(:resource AS {schema}.resource_enum)
          AND p.action   = CAST(:action   AS {schema}.action_enum)
          AND p.scope = ANY(CAST(:satisfying_scopes AS {schema}.permission_scope_enum[]))
          AND tma.status = CAST('ENABLED' AS {schema}.module_access_status_enum)
          AND (
            CAST(:target_anchor AS text) IS NULL
            OR CAST(:target_anchor AS ltree) <@ on_.path
          )
        LIMIT 1
        """
    )
    result = await session.execute(
        sql,
        {
            "user_id": user_id,
            "module": module.value,
            "resource": resource.value,
            "action": action.value,
            "satisfying_scopes": _satisfying_scopes_for_sql(scope),
            "target_anchor": target_anchor,
        },
    )
    matched = result.first() is not None
    return _build_result(
        matched=matched,
        user_id=user_id,
        module=module,
        resource=resource,
        action=action,
        scope=scope,
        target_anchor=target_anchor,
        anchor_display=target_anchor or "tenant-side (no anchor)",
    )


def _build_result(
    *,
    matched: bool,
    user_id: UUID,
    module: ModuleCode,
    resource: PermissionResource,
    action: PermissionAction,
    scope: PermissionScope,
    target_anchor: str | None,
    anchor_display: str,
) -> tuple[bool, ReasonCode, str]:
    tuple_repr = f"({module.value},{resource.value},{action.value},{scope.value})"
    if matched:
        return (
            True,
            ReasonCode.GRANT_MATCHED,
            f"grant matched for {tuple_repr} at {anchor_display}",
        )
    return (
        False,
        ReasonCode.NO_MATCHING_GRANT_OR_OUT_OF_SCOPE,
        (
            f"no active grant for user_id={user_id} matches {tuple_repr} "
            f"covering target_anchor={target_anchor!r}"
        ),
    )


async def get_permissions_for_user(
    session: AsyncSession,
    auth: AuthContext,
) -> list[PermissionGrant]:
    """Return the caller's full permission grant set.

    PLATFORM users: rows from ``platform_user_role_assignments`` joined
    to ``role_permissions`` and ``permissions``. ``anchor_path`` is
    always ``None`` for PLATFORM grants.

    TENANT users: rows from ``tenant_user_role_assignments`` joined to
    ``role_permissions``, ``permissions``, ``org_nodes`` (composite key
    per D-34), and ``tenant_module_access`` (``status='ENABLED'``
    filter). ``anchor_path`` carries ``on_.path`` cast to text so the
    caller can reason about cascade anchors.

    Used by ``/me/permissions`` (Step 6.9.2). Not on the gate hot path
    (the gate uses ``has_permission`` targeted query). Same JOIN
    structure as ``has_permission`` per audience, with the per-tuple
    ``WHERE`` clauses dropped and the projection widened.
    """
    if auth.user_type == "PLATFORM":
        return await _get_permissions_platform(session, auth.user_id)
    return await _get_permissions_tenant(session, auth.user_id)


async def _get_permissions_platform(
    session: AsyncSession, user_id: UUID
) -> list[PermissionGrant]:
    schema = get_settings().db_schema
    sql = text(
        f"""
        SELECT
            p.module   AS module,
            p.resource AS resource,
            p.action   AS action,
            p.scope    AS scope
        FROM {schema}.platform_user_role_assignments AS pura
        JOIN {schema}.role_permissions AS rp
            ON rp.role_id = pura.role_id
        JOIN {schema}.permissions AS p
            ON p.id = rp.permission_id
        WHERE pura.platform_user_id = :user_id
          AND pura.status = CAST('ACTIVE' AS {schema}.user_role_assignment_status_enum)
        """
    )
    result = await session.execute(sql, {"user_id": user_id})
    return [
        PermissionGrant(
            module=ModuleCode(row.module),
            resource=PermissionResource(row.resource),
            action=PermissionAction(row.action),
            scope=PermissionScope(row.scope),
            anchor_path=None,
        )
        for row in result.mappings().all()
    ]


async def _get_permissions_tenant(
    session: AsyncSession, user_id: UUID
) -> list[PermissionGrant]:
    schema = get_settings().db_schema
    sql = text(
        f"""
        SELECT
            p.module     AS module,
            p.resource   AS resource,
            p.action     AS action,
            p.scope      AS scope,
            on_.path::text AS anchor_path
        FROM {schema}.tenant_user_role_assignments AS tura
        JOIN {schema}.role_permissions AS rp
            ON rp.role_id = tura.role_id
        JOIN {schema}.permissions AS p
            ON p.id = rp.permission_id
        JOIN {schema}.org_nodes AS on_
            ON on_.tenant_id = tura.tenant_id
           AND on_.id = tura.org_node_id
        JOIN {schema}.tenant_module_access AS tma
            ON tma.tenant_id = tura.tenant_id
           AND tma.module = p.module
        WHERE tura.tenant_user_id = :user_id
          AND tura.status = CAST('ACTIVE' AS {schema}.user_role_assignment_status_enum)
          AND tma.status = CAST('ENABLED' AS {schema}.module_access_status_enum)
        """
    )
    result = await session.execute(sql, {"user_id": user_id})
    return [
        PermissionGrant(
            module=ModuleCode(row.module),
            resource=PermissionResource(row.resource),
            action=PermissionAction(row.action),
            scope=PermissionScope(row.scope),
            anchor_path=row.anchor_path,
        )
        for row in result.mappings().all()
    ]


def require(
    module: ModuleCode,
    resource: PermissionResource,
    action: PermissionAction,
    scope: PermissionScope,
    *,
    anchor_dep: AnchorDep | None = None,
    audience: Literal["PLATFORM", "TENANT"] | None = None,
) -> Callable[..., Awaitable[None]]:
    """Factory: return a FastAPI dependency that gates on this permission tuple.

    Usage::

        # List / aggregate endpoint (no anchor):
        @router.get("/some-list")
        async def some_handler(
            _: None = Depends(require(
                ModuleCode.ADMIN, PermissionResource.USERS,
                PermissionAction.VIEW, PermissionScope.TENANT,
            )),
            session: AsyncSession = Depends(get_tenant_session_dep),
        ) -> ...: ...

        # Single-resource endpoint with cascade-aware anchor:
        @router.get("/some-row/{row_id}")
        async def some_handler(
            row_id: UUID,
            _: None = Depends(require(
                ModuleCode.ADMIN, PermissionResource.USERS,
                PermissionAction.VIEW, PermissionScope.TENANT,
                anchor_dep=get_some_anchor,
            )),
            session: AsyncSession = Depends(get_tenant_session_dep),
        ) -> ...: ...

        # Platform-only write endpoint (Step 6.11):
        @router.post("/tenants")
        async def create_tenant(
            body: TenantCreateRequest,
            _: None = Depends(require(
                ModuleCode.ADMIN, PermissionResource.TENANTS,
                PermissionAction.CONFIGURE, PermissionScope.GLOBAL,
                audience="PLATFORM",
            )),
            ...
        ) -> ...: ...

    The returned dependency pulls ``auth`` via ``get_auth_context`` and
    ``session`` via ``get_tenant_session_dep`` (sharing the request's
    session). If ``anchor_dep`` is provided, the inner gate also pulls
    ``target_anchor`` via ``Depends(anchor_dep)`` — FastAPI resolves
    the anchor dep BEFORE running the gate body.

    Order of checks inside the gate body (Step 6.11.1):

      1. audience (Layer 1, if set) -> 403 PLATFORM_AUDIENCE_REQUIRED
      2. has_permission (Layer 2)   -> 403 PERMISSION_DENIED

    Note that FastAPI resolves ``auth``, ``session``, and ``anchor_dep``
    (when set) as Depends parameters BEFORE the gate body runs. A
    missing-anchor lookup raises 404 from the anchor_dep itself, ahead
    of either Layer 1 or Layer 2.

    The ``audience`` kwarg (Step 6.11.1) is defense-in-depth against
    catalogue drift. A future seed change that leaks a ``.GLOBAL`` grant
    to a TENANT-audience role would still be refused at Layer 1 on
    platform-only routes. ``audience=None`` (default) preserves every
    existing pre-6.11 call site unchanged. Convention codification
    deferred to Step 6.12's second-example confirmation.

    Two inner-function shapes (picked at factory-call time by
    ``anchor_dep`` presence) are required because FastAPI introspects
    a static signature; a single inner function with a conditionally-
    present ``Depends`` parameter doesn't compose.

    Every returned gate carries a ``PermissionGateInfo`` instance as
    ``gate.__permission_gate__`` for the mandatory-gate-discipline
    test to introspect (Step 6.9.3.2).
    """
    info = PermissionGateInfo(
        module=module,
        resource=resource,
        action=action,
        scope=scope,
        anchor_dep=anchor_dep,
        audience=audience,
    )

    def _check_audience(auth: AuthContext) -> None:
        if audience is not None and auth.user_type != audience:
            raise PlatformAudienceRequiredError(
                (
                    f"audience={audience!r} required but caller "
                    f"user_type={auth.user_type!r}"
                ),
                required_audience=audience,
                actual_user_type=auth.user_type,
            )

    if anchor_dep is None:
        async def gate(
            auth: AuthContext = Depends(get_auth_context),
            session: AsyncSession = Depends(get_tenant_session_dep),
        ) -> None:
            _check_audience(auth)
            allowed, reason_code, detail = await has_permission(
                session,
                auth,
                module,
                resource,
                action,
                scope,
                target_anchor=None,
            )
            if not allowed:
                raise PermissionDeniedError(
                    detail,
                    module=module.value,
                    resource=resource.value,
                    action=action.value,
                    scope=scope.value,
                    target_anchor=None,
                    reason_code=reason_code.value,
                )

        gate.__permission_gate__ = info  # type: ignore[attr-defined]
        return gate

    async def anchored_gate(
        auth: AuthContext = Depends(get_auth_context),
        session: AsyncSession = Depends(get_tenant_session_dep),
        target_anchor: str = Depends(anchor_dep),
    ) -> None:
        _check_audience(auth)
        allowed, reason_code, detail = await has_permission(
            session,
            auth,
            module,
            resource,
            action,
            scope,
            target_anchor=target_anchor,
        )
        if not allowed:
            raise PermissionDeniedError(
                detail,
                module=module.value,
                resource=resource.value,
                action=action.value,
                scope=scope.value,
                target_anchor=target_anchor,
                reason_code=reason_code.value,
            )

    anchored_gate.__permission_gate__ = info  # type: ignore[attr-defined]
    return anchored_gate
