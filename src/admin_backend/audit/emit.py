"""Audit emission helpers.

Two entry points per Step 6.16.2 LD2:

1. ``emit_audit_event(session, ...)``: same-transaction emission for
   the success path. The caller (repo method that owns the data write
   transaction) invokes this as the last step before returning. If the
   audit INSERT fails, the exception bubbles out of the caller's
   transaction and the data write rolls back atomically.

2. ``emit_audit_event_in_new_transaction(engine, ...)``: separate-
   transaction emission for the failure path. The data transaction has
   already rolled back by the time the global exception handler fires;
   the audit row goes in a new connection's autocommit context. If the
   audit INSERT itself fails (rare; bug), log CRITICAL and continue.
   The user-facing error envelope is not blocked by audit emission
   failure on the failure path.

Both entry points share the same column-level mechanics: they
construct the appropriate ORM model instance, populate the 16 columns
per the design doc spec, and persist. The routing decision (which
table) is governed by the routing principle in
``docs/architecture_audit_logs.md`` Architecture section, refined by
the ``route_to_platform`` flag for the design-doc-named exception
(POST /tenants success rows route to ``platform_activity_audit_logs``
even though tenant_id is set).

Per FN-AB-58, ``_actor_type_from_auth`` is a 4th local copy of the
helper that maps ``AuthContext.user_type`` Literal to the
``ActorUserType`` enum value. The other 3 live in routers/v1/rbac.py,
routers/v1/tenant_users.py, routers/v1/stores.py. 6.16.2 does not
promote to a shared module; FN-AB-58 stays open.

Per the design doc Emission contract section, the JSONB ``details``
payload shape varies by ``result_type``. The builders below construct
the dict per the design-doc Failure-row payload shapes table.
Submitted request bodies are NEVER stored.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from admin_backend.auth.context import AuthContext
from admin_backend.config import get_settings
from admin_backend.models.audit_log import (
    AuditResultType,
    PlatformActivityAuditLog,
    TenantActivityAuditLog,
)
from admin_backend.models.tenant_user import ActorUserType


_logger = logging.getLogger("admin_backend.audit")


# ---------------------------------------------------------------------------
# AUDITED_ROUTES : path-template + method to (action, resource_type, route_to_platform)
# ---------------------------------------------------------------------------


# Mapping from (HTTP method, route template) to the audit triple
# (action code, resource type, route_to_platform_flag). The exception
# handler consults this dict to decide whether to emit a failure-path
# audit row and what action / resource_type to record. Sub-step 6.16.4
# and 6.16.5 extend this dict; 6.16.2 lands only the 4 tenant routes.
AUDITED_ROUTES: dict[tuple[str, str], tuple[str, str, bool]] = {
    ("POST", "/api/v1/tenants"): ("CREATE", "TENANT", True),
    ("PATCH", "/api/v1/tenants/{tenant_id}"): ("UPDATE", "TENANT", False),
    ("POST", "/api/v1/tenants/{tenant_id}/suspend"): (
        "SUSPEND",
        "TENANT",
        False,
    ),
    ("POST", "/api/v1/tenants/{tenant_id}/activate"): (
        "ACTIVATE",
        "TENANT",
        False,
    ),
    # Step 6.16.4 : tenant-users + roles emission. Per LD1, route_to_platform=False
    # for tenant-users (tenant_id is read from path or row), True for roles
    # (catalogue is platform-scope; no tenant_id column on roles).
    ("POST", "/api/v1/tenant-users"): ("CREATE", "TENANT_USER", False),
    ("PATCH", "/api/v1/tenant-users/{user_id}"): (
        "UPDATE",
        "TENANT_USER",
        False,
    ),
    ("POST", "/api/v1/tenant-users/{user_id}/suspend"): (
        "SUSPEND",
        "TENANT_USER",
        False,
    ),
    ("POST", "/api/v1/tenant-users/{user_id}/activate"): (
        "ACTIVATE",
        "TENANT_USER",
        False,
    ),
    ("PATCH", "/api/v1/roles/{role_id}"): ("UPDATE", "ROLE", True),
    # Step 6.16.5 : module-access + org-tree + stores emission. Per
    # LD1, route_to_platform=False on all 7 (tenant_id always known
    # via path or row lookup). Stores set-status entry's "SET_STATUS"
    # is the FAILURE-PATH action code; the SUCCESS path emits one of
    # 4 per-target action codes (OPEN_SOFT / ACTIVATE / CLOSE /
    # DEACTIVATE) via direct emit_audit_event from
    # StoresRepo.transition (success path bypasses AUDITED_ROUTES for
    # the action choice).
    ("POST", "/api/v1/module-access/{tenant_id}/{module_code}/enable"): (
        "ENABLE",
        "MODULE_ACCESS",
        False,
    ),
    ("POST", "/api/v1/module-access/{tenant_id}/{module_code}/disable"): (
        "DISABLE",
        "MODULE_ACCESS",
        False,
    ),
    ("POST", "/api/v1/tenants/{tenant_id}/org-tree"): (
        "CREATE",
        "ORG_NODE",
        False,
    ),
    ("PATCH", "/api/v1/tenants/{tenant_id}/org-tree/{node_id}"): (
        "UPDATE",
        "ORG_NODE",
        False,
    ),
    ("POST", "/api/v1/stores"): ("CREATE", "STORE", False),
    ("PATCH", "/api/v1/stores/{store_id}"): ("UPDATE", "STORE", False),
    ("POST", "/api/v1/stores/{store_id}/set-status"): (
        "SET_STATUS",
        "STORE",
        False,
    ),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_ACTION_LABELS: dict[str, str] = {
    "CREATE": "Created",
    # Step 6.16.7 LD8 : UPDATE label changes "Updated" -> "Edited"
    # for the audit list-view redesign. The action code stays UPDATE
    # in the DB column for D-31 append-only stability; only the
    # rendered label changes.
    "UPDATE": "Edited",
    "SUSPEND": "Suspended",
    "ACTIVATE": "Activated",
    # Step 6.16.5 : module-access enable/disable + stores per-target
    # transition labels per LD3. ACTIVATE reused (already present).
    # OPEN_SOFT is reserved per FN-AB-68: target=OPENING is not
    # reachable via the live TRANSITION_MATRIX (entry-only via POST
    # /stores per 6.17.4 LD1) but the label stays in the vocabulary
    # for D-31 append-only stability and forward matrix relaxation.
    # SET_STATUS is the failure-path action code; success path emits
    # one of the 4 per-target codes.
    "ENABLE": "Enabled",
    "DISABLE": "Disabled",
    "OPEN_SOFT": "Soft-opened",
    "CLOSE": "Closed",
    "DEACTIVATE": "Deactivated",
    # Step 6.16.7 LD8 : SET_STATUS label changes "Status change" ->
    # "Set status" for the audit list-view redesign.
    "SET_STATUS": "Set status",
}


_RESULT_LABELS: dict[AuditResultType, str] = {
    AuditResultType.SUCCESS: "Success",
    AuditResultType.PERMISSION_DENIED: "Permission denied",
    AuditResultType.VALIDATION_FAILED: "Validation failed",
    AuditResultType.CONFLICT: "Conflict",
    AuditResultType.INTEGRITY_VIOLATION: "Integrity violation",
    AuditResultType.INTERNAL_ERROR: "Internal error",
}


def _actor_type_from_auth(user_type: str) -> ActorUserType:
    """Map ``AuthContext.user_type`` Literal to ``ActorUserType``.

    Per LD6, this is the 4th local copy of the helper in the codebase.
    The other 3 copies live in:
      - src/admin_backend/routers/v1/rbac.py
      - src/admin_backend/routers/v1/tenant_users.py
      - src/admin_backend/routers/v1/stores.py
    FN-AB-58 tracks consolidation; not done in this step.
    """
    if user_type == "PLATFORM":
        return ActorUserType.PLATFORM
    if user_type == "TENANT":
        return ActorUserType.TENANT
    raise ValueError(f"unknown user_type: {user_type!r}")


def _label_for_action(action: str) -> str:
    """Return the human-readable label for an action code.

    Falls back to the action code itself if unknown (defensive; the
    set is closed at v0 per LD4, but future actions land here as new
    entries to the dict).
    """
    return _ACTION_LABELS.get(action, action)


def _label_for_result(result_type: AuditResultType) -> str:
    return _RESULT_LABELS.get(result_type, result_type.value)


# ---------------------------------------------------------------------------
# Step 6.16.7 LD12 : Type label dispatch for the ``what`` field
# ---------------------------------------------------------------------------


# Display labels for non-ORG_NODE resource_types. Keyed by the raw
# resource_type string stored on the audit row.
_RESOURCE_TYPE_LABELS: dict[str, str] = {
    "TENANT": "Tenant",
    "TENANT_USER": "User",
    "ROLE": "Role",
    "MODULE_ACCESS": "Module",
    "STORE": "Store",
}

# Display labels for ORG_NODE rows broken down by ``resource_subtype``
# (= ``org_nodes.node_type`` enum value frozen at write time).
_ORG_NODE_SUBTYPE_LABELS: dict[str, str] = {
    "TENANT": "Tenant root",
    "BUSINESS_UNIT": "Business unit",
    "HQ": "HQ",
    "COUNTRY": "Country",
    "REGION": "Region",
    "STORE": "Store",
    "DEPARTMENT": "Department",
}


def _label_for_resource_type(
    resource_type: str, resource_subtype: str | None
) -> str:
    """Return the user-facing Type label per LD12.

    For non-ORG_NODE resource_types, dispatches on ``resource_type``
    against ``_RESOURCE_TYPE_LABELS``. For ORG_NODE rows, dispatches on
    ``resource_subtype`` against ``_ORG_NODE_SUBTYPE_LABELS``. Pre-
    6.16.7 historical ORG_NODE rows (NULL ``resource_subtype``) render
    as ``"Org node"`` per LD11 historical fallback.

    Falls back to the raw ``resource_type`` value for any unmapped
    code (defensive; the v0 vocabulary is closed but future additions
    land here as new entries to the dicts).
    """
    if resource_type == "ORG_NODE":
        if resource_subtype is None:
            return "Org node"
        return _ORG_NODE_SUBTYPE_LABELS.get(resource_subtype, "Org node")
    return _RESOURCE_TYPE_LABELS.get(resource_type, resource_type)


# ---------------------------------------------------------------------------
# Step 6.16.7 LD9 : CONFLICT qualifier dispatch for result_label
# ---------------------------------------------------------------------------


# Maps the ``code`` constant of each CONFLICT-class ClientError to a
# qualifier phrase that composes into ``"Blocked – <qualifier>"``. The
# 9 codes match the 409 ClientError subclasses enumerated at Step
# 6.16.7 pre-flight Check #7. When a CONFLICT row's code is not in
# this dict (e.g., a future ClientError that maps to 409 without an
# entry here), the static ``_RESULT_LABELS["CONFLICT"] = "Conflict"``
# remains the fallback.
_CONFLICT_QUALIFIERS: dict[str, str] = {
    "DUPLICATE_TENANT_NAME": "tenant name already exists",
    "INVALID_STATE_TRANSITION": "status change not allowed",
    "DUPLICATE_TENANT_USER_EMAIL": "email already in use for this tenant",
    "ROLE_ASSIGNMENT_CONFLICT": "role assignment conflict, please retry",
    "DUPLICATE_ORG_NODE_CODE": "code already in use for this tenant",
    "DUPLICATE_STORE_CODE": "store code already in use for this tenant",
    "ROLE_ARCHIVED": "role is archived",
    "LAST_OVERRIDE_HOLDER": "would remove the last platform admin",
    "SUPER_ADMIN_PROTECTED": "SUPER_ADMIN role is protected",
}


def _qualifier_for_conflict(code: str | None) -> str | None:
    """Return the qualifier phrase for a CONFLICT row's ``code``.

    None when the code is not in the dispatch table; callers should
    fall back to the static ``"Conflict"`` label.
    """
    if code is None:
        return None
    return _CONFLICT_QUALIFIERS.get(code)


def compose_conflict_result_label(code: str | None) -> str:
    """Compose ``"Blocked - <qualifier>"`` for a CONFLICT row when the
    dispatch table has a phrase; otherwise return the static fallback.

    Used by the failure handler when constructing the audit row's
    ``result_label`` for 409 ClientError raises.
    """
    qualifier = _qualifier_for_conflict(code)
    if qualifier is None:
        return _label_for_result(AuditResultType.CONFLICT)
    return f"Blocked - {qualifier}"


# ---------------------------------------------------------------------------
# Step 6.16.7 LD5 + LD6 : actor enrichment resolvers
# ---------------------------------------------------------------------------


_PLATFORM_ORG_NAME = "Platform-Ithina"


async def _resolve_actor_organization_name(
    conn: Any, auth: AuthContext
) -> str:
    """Return the actor's organisation name for the audit row per LD6.

    PLATFORM actors return the literal ``"Platform-Ithina"``. TENANT
    actors return the tenant's ``name`` from ``core.tenants``. Falls
    back to ``"-"`` if the tenant row is not found (defensive; should
    not happen under D-29 + JWT validity but the resolver must always
    return a value because the column is NOT NULL).

    ``conn`` is either an ``AsyncSession`` (success path; same session
    as the data write, with caller's GUCs set) or an ``AsyncConnection``
    (failure path; new connection with ``app.user_type='PLATFORM'``).
    Both expose ``execute(text(...), {...})`` and ``scalar_one_or_none``
    via the result mapping.
    """
    if auth.user_type == "PLATFORM":
        return _PLATFORM_ORG_NAME
    if auth.tenant_id is None:
        # Defensive: TENANT user_type with no tenant_id would have been
        # rejected by AuthContext's Pydantic validator. Belt-and-braces
        # for an unreachable path.
        return "-"
    schema = get_settings().db_schema
    result = await conn.execute(
        text(
            f"SELECT name FROM {schema}.tenants WHERE id = :tenant_id"
        ),
        {"tenant_id": auth.tenant_id},
    )
    name = result.scalar_one_or_none()
    if name is None:
        return "-"
    return str(name)


async def _resolve_actor_roles(conn: Any, auth: AuthContext) -> str:
    """Return the actor's active role names as a comma-separated string
    per LD5.

    PLATFORM actors: JOIN ``platform_user_role_assignments`` (no RLS)
    -> ``roles`` filtered by ``platform_user_id = auth.user_id`` and
    ``status = 'ACTIVE'``. Returns the human-readable ``roles.name``
    values (not the uppercase ``roles.code``).

    TENANT actors: JOIN ``tenant_user_role_assignments`` -> ``roles``
    filtered by ``tenant_user_id = auth.user_id`` and ``status =
    'ACTIVE'``. Same display-name aggregation.

    When the actor has no active role assignments, returns ``"-"``.

    Sorted by ``roles.name`` ASC for deterministic display. Joined
    with ``", "`` separator (comma + single space). Pattern mirrors
    the existing ``_resolve_role_labels`` helper in
    ``repositories/tenant_users.py`` (Step 6.16.4) which uses ANY-array
    SELECTs against ``roles`` keyed by UUID set; difference is the
    filter clause (this resolver filters by actor user id, not by a
    pre-computed UUID set).

    The resolver must work in both transaction contexts (success path:
    caller's session; failure path: new connection under
    ``app.user_type='PLATFORM'``). RLS on
    ``tenant_user_role_assignments`` permits reads in both cases.
    """
    schema = get_settings().db_schema
    if auth.user_type == "PLATFORM":
        sql = text(
            f"""
            SELECT r.name AS role_name
              FROM {schema}.platform_user_role_assignments pura
              JOIN {schema}.roles r ON r.id = pura.role_id
             WHERE pura.platform_user_id = :user_id
               AND pura.status = CAST('ACTIVE' AS {schema}.user_role_assignment_status_enum)
             ORDER BY r.name ASC
            """
        )
    else:
        sql = text(
            f"""
            SELECT r.name AS role_name
              FROM {schema}.tenant_user_role_assignments tura
              JOIN {schema}.roles r ON r.id = tura.role_id
             WHERE tura.tenant_user_id = :user_id
               AND tura.status = CAST('ACTIVE' AS {schema}.user_role_assignment_status_enum)
             ORDER BY r.name ASC
            """
        )
    result = await conn.execute(sql, {"user_id": auth.user_id})
    rows = result.fetchall()
    if not rows:
        return "-"
    names = [str(r[0]) for r in rows if r[0] is not None]
    if not names:
        return "-"
    return ", ".join(names)


# ---------------------------------------------------------------------------
# Details builders : pure functions producing JSONB-compatible dicts
# ---------------------------------------------------------------------------


def build_success_details_for_create(
    snapshot: dict[str, Any],
    *,
    roles: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Success-row payload for CREATE actions per design doc.

    Shape: ``{"snapshot": {...the created entity...}}``.

    Step 6.16.4 LD8: when ``roles`` is provided (tenant-users CREATE),
    the snapshot includes ``roles`` as a list of frozen-label items
    each carrying ``{role_id, role_name, org_node_id, org_node_name}``
    per LD9.
    """
    payload = _json_safe(snapshot)
    if roles is not None:
        payload["roles"] = [_json_safe(item) for item in roles]
    return {"snapshot": payload}


def build_success_details_for_update(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    before_roles: list[dict[str, Any]] | None = None,
    after_roles: list[dict[str, Any]] | None = None,
    before_permissions: list[dict[str, Any]] | None = None,
    after_permissions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Success-row payload for UPDATE-shaped actions per design doc.

    Shape: ``{"before": {...}, "after": {...}}``. ``before`` and
    ``after`` are projected to only the fields that changed (the
    caller filters to the diff set).

    Step 6.16.4 LD8: when the request includes a role-list or
    permission-list diff, BOTH sides carry the full list (not the
    diff) per Phase 1 Q1. Each role item carries the 4 frozen-label
    fields per LD9; each permission item carries ``{permission_id,
    permission_code}`` per LD9.
    """
    before_payload = _json_safe(before)
    after_payload = _json_safe(after)
    if before_roles is not None:
        before_payload["roles"] = [_json_safe(item) for item in before_roles]
    if after_roles is not None:
        after_payload["roles"] = [_json_safe(item) for item in after_roles]
    if before_permissions is not None:
        before_payload["permissions"] = [
            _json_safe(item) for item in before_permissions
        ]
    if after_permissions is not None:
        after_payload["permissions"] = [
            _json_safe(item) for item in after_permissions
        ]
    return {"before": before_payload, "after": after_payload}


def build_success_details_for_transition(
    before_status: str,
    after_status: str,
) -> dict[str, Any]:
    """Success-row payload for state-transition actions.

    Uses the UPDATE shape with only the ``status`` field captured.
    """
    return {
        "before": {"status": before_status},
        "after": {"status": after_status},
    }


def build_permission_denied_details(
    required_permission: str,
    caller_audience: str,
    caller_roles: list[str] | None = None,
    *,
    denial_reason: str | None = None,
) -> dict[str, Any]:
    """PERMISSION_DENIED details per design doc.

    ``required_permission`` is the dotted tuple form
    (e.g. ``"ADMIN.TENANTS.CONFIGURE.GLOBAL"``). ``caller_audience``
    is ``"PLATFORM"`` or ``"TENANT"``. ``caller_roles`` is an optional
    list of role codes / names; defaults to empty when not available.

    Step 6.16.4 LD11: when a handler-side guard (e.g.
    ``_raise_if_self_edit``) produces the 403, ``denial_reason``
    names the specific guard (e.g. ``"SELF_EDIT_FORBIDDEN"``).
    Standard sub-keys remain present; the optional one augments.
    See ``docs/architecture_audit_logs.md`` Failure-row payload
    shapes section for the optional-sub-key convention.
    """
    payload: dict[str, Any] = {
        "required_permission": required_permission,
        "caller_audience": caller_audience,
        "caller_roles": caller_roles or [],
    }
    if denial_reason is not None:
        payload["denial_reason"] = denial_reason
    return payload


def build_validation_failed_details(
    validation_errors: list[dict[str, Any]],
) -> dict[str, Any]:
    """VALIDATION_FAILED details per design doc.

    Submitted values are NEVER included; only field paths + messages.
    """
    return {
        "validation_errors": [
            {
                "field": e.get("field"),
                "error_message": e.get("error_message"),
            }
            for e in validation_errors
        ]
    }


def build_conflict_details(
    constraint: str,
    field: str | None = None,
    value: str | None = None,
) -> dict[str, Any]:
    """CONFLICT details per design doc.

    For state-transition CONFLICT (e.g., SUSPEND on already-SUSPENDED),
    ``field`` and ``value`` may be None; ``constraint`` carries the
    relevant signal (e.g., ``"INVALID_STATE_TRANSITION"``).
    """
    payload: dict[str, Any] = {"constraint": constraint}
    if field is not None:
        payload["field"] = field
    if value is not None:
        payload["value"] = value
    return payload


def build_integrity_violation_details(constraint: str) -> dict[str, Any]:
    """INTEGRITY_VIOLATION details per design doc."""
    return {"constraint": constraint}


def build_internal_error_details(
    error_class: str,
    sanitised_message: str | None = None,
    *,
    invariant: str | None = None,
) -> dict[str, Any]:
    """INTERNAL_ERROR details per design doc.

    Sanitised_message must NOT include user-supplied content; it is
    the generic envelope's ``public_message`` or a fixed string.

    Step 6.16.4 LD12: when the 500 is a Layer 2 invariant tripwire
    (e.g. ``InternalInvariantViolationError`` from
    ``RolesRepo.update``), ``invariant`` names the specific guard
    (e.g. ``"OVERRIDE_GLOBAL_HOLDER_PRESERVATION"``). Standard
    sub-keys remain; the optional one augments. See
    ``docs/architecture_audit_logs.md`` Failure-row payload shapes
    section for the optional-sub-key convention.
    """
    payload: dict[str, Any] = {
        "error_class": error_class,
        "sanitised_message": sanitised_message or "An internal error occurred",
    }
    if invariant is not None:
        payload["invariant"] = invariant
    return payload


def _json_safe(payload: dict[str, Any]) -> dict[str, Any]:
    """Convert UUIDs, datetimes, Decimals etc. to JSON-serialisable values.

    Best-effort coercion: any object with an ``.isoformat()`` method
    becomes a string; UUID becomes ``str(uuid)``; sequences pass
    through their items recursively. Anything else passes through as
    long as the JSONB serialiser accepts it; SQLAlchemy raises if not.
    """
    out: dict[str, Any] = {}
    for key, value in payload.items():
        out[key] = _coerce_value(value)
    return out


def _coerce_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, UUID):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, dict):
        return _json_safe(value)
    if isinstance(value, (list, tuple)):
        return [_coerce_value(v) for v in value]
    # Decimal and other types: stringify defensively.
    return str(value)


# ---------------------------------------------------------------------------
# Success-path emission : same-transaction ORM INSERT
# ---------------------------------------------------------------------------


async def emit_audit_event(
    session: AsyncSession,
    *,
    auth: AuthContext,
    action: str,
    action_label: str | None = None,
    resource_type: str,
    resource_id: UUID | None,
    resource_label: str | None,
    result_type: AuditResultType,
    result_label: str | None = None,
    details: dict[str, Any],
    tenant_id: UUID | None,
    tenant_name: str | None,
    request_id: UUID,
    route_to_platform: bool,
    resource_subtype: str | None = None,
) -> None:
    """Emit one audit row in the caller's transaction (success path).

    Routing per LD3:
      - If ``route_to_platform`` is True, INSERT into
        ``platform_activity_audit_logs`` regardless of ``tenant_id``.
        (The design doc named exception: POST /tenants success rows go
        to the platform table even though tenant_id is populated.)
      - Otherwise, INSERT into ``tenant_activity_audit_logs`` when
        ``tenant_id`` is non-None; into ``platform_activity_audit_logs``
        when it is None.

    Step 6.16.7 LD13 : actor enrichment + resource_subtype.

    The two new actor columns (``actor_organization_name`` and
    ``actor_roles``) are resolved centrally here via the LD5 / LD6
    helpers; callers do NOT pass them. The resolvers run on the
    caller's ``session`` so they share the data write's transaction
    (atomicity preserved). RLS on
    ``tenant_user_role_assignments`` admits the read under the
    caller's GUCs (TENANT actor sees own tenant's assignments;
    PLATFORM actor's branch is no-RLS).

    ``resource_subtype`` is caller-supplied: only the 2 org-tree
    repo call sites pass a non-None value (the ``org_nodes.node_type``
    value already in scope from the org_node operation). All other
    emission paths leave it None.

    The call sequence is ``session.add(row)`` + ``session.flush()``.
    The caller controls the transaction; the data write and audit row
    commit together (atomicity preserved) when the caller's session
    commits. If the INSERT fails (e.g., constraint violation),
    SQLAlchemy raises; the caller's transaction rolls back.
    """
    actor_organization_name = await _resolve_actor_organization_name(
        session, auth
    )
    actor_roles = await _resolve_actor_roles(session, auth)
    row = _build_row(
        auth=auth,
        action=action,
        action_label=action_label,
        resource_type=resource_type,
        resource_id=resource_id,
        resource_label=resource_label,
        resource_subtype=resource_subtype,
        result_type=result_type,
        result_label=result_label,
        details=details,
        tenant_id=tenant_id,
        tenant_name=tenant_name,
        request_id=request_id,
        route_to_platform=route_to_platform,
        actor_organization_name=actor_organization_name,
        actor_roles=actor_roles,
    )
    session.add(row)
    await session.flush()


def _build_row(
    *,
    auth: AuthContext,
    action: str,
    action_label: str | None,
    resource_type: str,
    resource_id: UUID | None,
    resource_label: str | None,
    resource_subtype: str | None,
    result_type: AuditResultType,
    result_label: str | None,
    details: dict[str, Any],
    tenant_id: UUID | None,
    tenant_name: str | None,
    request_id: UUID,
    route_to_platform: bool,
    actor_organization_name: str,
    actor_roles: str,
) -> TenantActivityAuditLog | PlatformActivityAuditLog:
    """Construct the appropriate ORM row per routing principle."""
    actor_user_type = _actor_type_from_auth(auth.user_type)
    common: dict[str, Any] = {
        "actor_user_id": auth.user_id,
        "actor_user_type": actor_user_type,
        "actor_display_name": auth.email,
        "actor_organization_name": actor_organization_name,
        "actor_roles": actor_roles,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "resource_label": resource_label,
        "resource_subtype": resource_subtype,
        "action": action,
        "action_label": action_label or _label_for_action(action),
        "result_type": result_type,
        "result_label": result_label or _label_for_result(result_type),
        "request_id": request_id,
        "details": _json_safe(details),
    }

    if route_to_platform or tenant_id is None:
        return PlatformActivityAuditLog(
            tenant_id=tenant_id,
            tenant_name=tenant_name,
            **common,
        )
    # Normal routing : tenant_id set, not the named exception.
    return TenantActivityAuditLog(
        tenant_id=tenant_id,
        tenant_name=tenant_name or "",
        **common,
    )


# ---------------------------------------------------------------------------
# Failure-path emission : separate-transaction raw SQL INSERT
# ---------------------------------------------------------------------------


async def emit_audit_event_in_new_transaction(
    engine: AsyncEngine,
    *,
    auth: AuthContext,
    action: str,
    action_label: str | None = None,
    resource_type: str,
    resource_id: UUID | None,
    resource_label: str | None,
    result_type: AuditResultType,
    result_label: str | None = None,
    details: dict[str, Any],
    tenant_id: UUID | None,
    tenant_name: str | None,
    request_id: UUID,
    route_to_platform: bool,
    module_code: str | None = None,
    node_id: UUID | None = None,
    store_id: UUID | None = None,
    resource_subtype: str | None = None,
) -> None:
    """Emit one audit row in a new transaction (failure path).

    Opens a fresh connection from the engine pool, runs a single
    INSERT, commits. The data write has already rolled back by the
    time this is called; the audit row would have been discarded if
    it had been in the same transaction.

    Behaviour on inner failure:
      - If the INSERT itself raises (rare; constraint violation due to
        bug), log CRITICAL and return without raising. The user-facing
        error envelope is the visible response; audit emission failure
        on the failure path is a back-end-only concern.
    """
    schema = get_settings().db_schema
    # Initial table choice based on caller-supplied tenant_id and
    # route_to_platform. The final choice may flip after the post-
    # lookup re-evaluation below (Step 6.16.4 LD7: TENANT_USER routes
    # gain tenant_id from a tenant_users JOIN even when the caller
    # didn't have it).
    table = (
        "platform_activity_audit_logs"
        if route_to_platform or tenant_id is None
        else "tenant_activity_audit_logs"
    )

    # JSONB requires a JSON-encoded string at the param boundary when
    # using CAST(:p AS jsonb). dict.__str__ is NOT valid JSON (single
    # quotes); use json.dumps.
    import json

    params = {
        "tenant_id": tenant_id,
        "tenant_name": tenant_name,
        "actor_user_id": auth.user_id,
        "actor_user_type": _actor_type_from_auth(auth.user_type).value,
        "actor_display_name": auth.email,
        # Step 6.16.7 LD13 : actor enrichment columns + resource_subtype.
        # ``actor_organization_name`` and ``actor_roles`` are resolved
        # inside the ``async with engine.begin()`` block below, AFTER
        # the ``app.user_type='PLATFORM'`` GUC is set on the new
        # connection. Initialised to None here; assigned post-resolve.
        "actor_organization_name": None,
        "actor_roles": None,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "resource_label": resource_label,
        "resource_subtype": resource_subtype,
        "action": action,
        "action_label": action_label or _label_for_action(action),
        "result_type": result_type.value,
        "result_label": result_label or _label_for_result(result_type),
        "request_id": request_id,
        "details": json.dumps(_json_safe(details)),
    }

    try:
        async with engine.begin() as conn:
            # The new connection from the pool has no
            # ``app.tenant_id`` / ``app.user_type`` GUCs set, so the
            # tenant_activity_audit_logs RLS policy's USING / WITH CHECK
            # predicates would evaluate to NULL (default-deny) and the
            # INSERT would be rejected. Audit rows are platform-managed
            # metadata regardless of the actor's audience; setting
            # ``app.user_type='PLATFORM'`` here fires the D-29
            # unconditional OR-branch and admits the INSERT into either
            # table. The actor's true identity is captured INSIDE the
            # audit row (``actor_user_type`` column), so the GUC choice
            # is purely about INSERT-side RLS, not about who-did-what
            # accuracy.
            await conn.execute(
                text("SELECT set_config('app.user_type', 'PLATFORM', true)")
            )
            # Step 6.16.7 LD5 / LD6 : actor enrichment resolvers under
            # the new connection (post-GUC-set so RLS admits the read on
            # tenant_user_role_assignments). The resolvers return
            # safe-defaults ("-" / "Platform-Ithina") when lookups
            # cannot be completed; the NOT NULL columns always get a
            # value.
            params["actor_organization_name"] = (
                await _resolve_actor_organization_name(conn, auth)
            )
            params["actor_roles"] = await _resolve_actor_roles(conn, auth)
            # The failure-path emission does not have a tenant_name
            # or resource_label handy (the data session is closed; the
            # exception handler had the path params from the URL but
            # no row data). When emitting to the tenant table we MUST
            # populate tenant_name (NOT NULL on the tenant table); we
            # also populate resource_label from the same lookup so
            # ``ck_*_resource_pair`` is satisfied (resource_id and
            # resource_label must be both-NULL or both-NOT-NULL). On
            # the platform table both columns are nullable; lookup
            # still happens when tenant_id is set so the audit row
            # carries the snapshot consistently.
            #
            # Step 6.16.4 LD3 / Deviation 2 (Option A): dispatch on
            # ``resource_type`` to pick the right lookup table. TENANT
            # reads ``tenants.name``; TENANT_USER reads
            # ``tenant_users.full_name`` (and also reads
            # ``tenant_users.tenant_id`` + ``tenants.name`` so the
            # failure row routes to the tenant table correctly with
            # tenant_name populated); ROLE reads ``roles.name`` and
            # leaves tenant_id NULL (catalogue is platform-scope).
            #
            # If the underlying row no longer exists (race), fall
            # back to ``<unknown>`` rather than failing emission.
            resource_type_str = str(params["resource_type"])
            if (
                resource_type_str == "TENANT"
                and params["tenant_id"] is not None
            ):
                lookup = await conn.execute(
                    text(
                        f"SELECT name FROM {schema}.tenants "
                        "WHERE id = :tenant_id"
                    ),
                    {"tenant_id": params["tenant_id"]},
                )
                resolved = lookup.scalar_one_or_none()
                name_snapshot = (
                    str(resolved) if resolved is not None else "<unknown>"
                )
                if params["tenant_name"] is None:
                    params["tenant_name"] = name_snapshot
                if (
                    params["resource_id"] is not None
                    and params["resource_label"] is None
                ):
                    params["resource_label"] = name_snapshot
            elif (
                resource_type_str == "TENANT_USER"
                and params["resource_id"] is not None
            ):
                lookup = await conn.execute(
                    text(
                        f"SELECT tu.full_name, tu.tenant_id, t.name "
                        f"FROM {schema}.tenant_users tu "
                        f"JOIN {schema}.tenants t ON t.id = tu.tenant_id "
                        "WHERE tu.id = :resource_id"
                    ),
                    {"resource_id": params["resource_id"]},
                )
                row = lookup.first()
                if row is not None:
                    full_name, tu_tenant_id, t_name = row
                    if params["resource_label"] is None:
                        params["resource_label"] = (
                            str(full_name)
                            if full_name is not None
                            else "<unknown>"
                        )
                    if params["tenant_id"] is None:
                        params["tenant_id"] = tu_tenant_id
                    if params["tenant_name"] is None:
                        params["tenant_name"] = (
                            str(t_name) if t_name is not None else "<unknown>"
                        )
                else:
                    # Row deleted concurrently; fall back to placeholders.
                    if params["resource_label"] is None:
                        params["resource_label"] = "<unknown>"
                    if (
                        params["tenant_id"] is None
                        and params["tenant_name"] is None
                    ):
                        # Cannot infer tenant_id; row will route to
                        # platform table per the existing routing.
                        pass
            elif (
                resource_type_str == "ROLE"
                and params["resource_id"] is not None
            ):
                lookup = await conn.execute(
                    text(
                        f"SELECT name FROM {schema}.roles "
                        "WHERE id = :resource_id"
                    ),
                    {"resource_id": params["resource_id"]},
                )
                resolved = lookup.scalar_one_or_none()
                if params["resource_label"] is None:
                    params["resource_label"] = (
                        str(resolved) if resolved is not None else "<unknown>"
                    )
            elif (
                resource_type_str == "MODULE_ACCESS"
                and module_code is not None
                and params["tenant_id"] is not None
            ):
                # Step 6.16.5 LD9: module-access label resolves from
                # ``core.lookups`` (list_name='module_code'). The row's
                # ``resource_id`` is the ``tenant_module_access.id``,
                # looked up by (tenant_id, module_code); on
                # auth/permission-denied paths the row may not exist
                # yet (enable upserts), so resource_id stays NULL on
                # those failure rows. ``tenant_name`` joins from
                # ``tenants``.
                lookup = await conn.execute(
                    text(
                        f"""
                        SELECT
                            (SELECT id FROM {schema}.tenant_module_access
                              WHERE tenant_id = :tenant_id
                                AND module = CAST(:module_code_enum AS {schema}.module_code_enum))
                                AS tma_id,
                            (SELECT name FROM {schema}.tenants
                              WHERE id = :tenant_id)
                                AS t_name,
                            (SELECT COALESCE(display_name, code)
                               FROM {schema}.lookups
                              WHERE list_name = 'module_code'
                                AND code = CAST(:module_code_text AS text))
                                AS module_label
                        """
                    ),
                    {
                        "tenant_id": params["tenant_id"],
                        "module_code_enum": module_code,
                        "module_code_text": module_code,
                    },
                )
                row = lookup.first()
                if row is not None:
                    if params["resource_id"] is None and row.tma_id is not None:
                        params["resource_id"] = row.tma_id
                    if params["tenant_name"] is None:
                        params["tenant_name"] = (
                            str(row.t_name)
                            if row.t_name is not None
                            else "<unknown>"
                        )
                    # ``ck_*_resource_pair``: resource_id and
                    # resource_label must be both-NULL or both-NOT-NULL.
                    # Only resolve resource_label when resource_id is
                    # populated (the tma row exists); otherwise the
                    # row carries no resource identity and the label
                    # has to stay NULL too.
                    if (
                        params["resource_id"] is not None
                        and params["resource_label"] is None
                    ):
                        params["resource_label"] = (
                            str(row.module_label)
                            if row.module_label is not None
                            else module_code
                        )
            elif resource_type_str == "ORG_NODE":
                # Step 6.16.5 LD9: org-tree label = org_nodes.name (when
                # node_id is known via path on PATCH). POST add-node
                # has no path node_id; on its failure rows resource_id
                # stays NULL and resource_label stays NULL (the
                # resource_pair CHECK admits both-NULL on the platform
                # branch; the tenant branch's CHECK is symmetric and
                # admits both-NULL too — both columns are nullable).
                #
                # Step 6.16.7 LD7 : also fetch ``node_type`` and back-
                # fill ``resource_subtype`` for failure rows when the
                # row is reachable. POST add-node failures (no path
                # node_id) leave resource_subtype NULL.
                if (
                    node_id is not None
                    and params["tenant_id"] is not None
                ):
                    lookup = await conn.execute(
                        text(
                            f"SELECT name, node_type::text AS node_type "
                            f"FROM {schema}.org_nodes "
                            "WHERE id = :node_id "
                            "AND tenant_id = :tenant_id"
                        ),
                        {
                            "node_id": node_id,
                            "tenant_id": params["tenant_id"],
                        },
                    )
                    on_row = lookup.first()
                    if on_row is not None:
                        if params["resource_label"] is None:
                            params["resource_label"] = (
                                str(on_row.name)
                                if on_row.name is not None
                                else "<unknown>"
                            )
                        if params["resource_subtype"] is None:
                            params["resource_subtype"] = (
                                str(on_row.node_type)
                                if on_row.node_type is not None
                                else None
                            )
                    else:
                        if params["resource_label"] is None:
                            params["resource_label"] = "<unknown>"
                if params["tenant_id"] is not None:
                    lookup_t = await conn.execute(
                        text(
                            f"SELECT name FROM {schema}.tenants "
                            "WHERE id = :tenant_id"
                        ),
                        {"tenant_id": params["tenant_id"]},
                    )
                    t_name = lookup_t.scalar_one_or_none()
                    if params["tenant_name"] is None:
                        params["tenant_name"] = (
                            str(t_name)
                            if t_name is not None
                            else "<unknown>"
                        )
            elif resource_type_str == "STORE":
                # Step 6.16.5 LD9: stores label = stores.name (when
                # store_id is known via path on PATCH or set-status).
                # POST /stores has no path store_id; the body was
                # consumed by FastAPI before the failure handler ran
                # so neither tenant_id nor store identity is recoverable
                # on POST /stores failure rows. Per LD10 those rows
                # route to the platform table with tenant_id=NULL and
                # resource_id=NULL.
                if store_id is not None:
                    lookup = await conn.execute(
                        text(
                            f"""
                            SELECT s.name AS store_name,
                                   s.tenant_id AS tenant_id,
                                   t.name AS tenant_name
                              FROM {schema}.stores s
                              JOIN {schema}.tenants t ON t.id = s.tenant_id
                             WHERE s.id = :store_id
                            """
                        ),
                        {"store_id": store_id},
                    )
                    row = lookup.first()
                    if row is not None:
                        if params["resource_label"] is None:
                            params["resource_label"] = (
                                str(row.store_name)
                                if row.store_name is not None
                                else "<unknown>"
                            )
                        if params["tenant_id"] is None:
                            params["tenant_id"] = row.tenant_id
                        if params["tenant_name"] is None:
                            params["tenant_name"] = (
                                str(row.tenant_name)
                                if row.tenant_name is not None
                                else "<unknown>"
                            )
                    else:
                        if params["resource_label"] is None:
                            params["resource_label"] = "<unknown>"
            elif params["tenant_id"] is not None:
                # Fallback: tenant_id was passed in (e.g. from the
                # path) but resource_type isn't one we know how to
                # look up. Snapshot tenant_name from tenants so the
                # tenant-table NOT NULL constraint is satisfied.
                lookup = await conn.execute(
                    text(
                        f"SELECT name FROM {schema}.tenants "
                        "WHERE id = :tenant_id"
                    ),
                    {"tenant_id": params["tenant_id"]},
                )
                resolved = lookup.scalar_one_or_none()
                if params["tenant_name"] is None:
                    params["tenant_name"] = (
                        str(resolved) if resolved is not None else "<unknown>"
                    )

            # Re-evaluate routing AFTER lookups: a TENANT_USER or STORE
            # row may have just populated tenant_id from the JOIN,
            # which means the row should route to the tenant table
            # (LD7 of 6.16.4 / LD10 of 6.16.5). The original ``table``
            # chosen above was based on the caller's tenant_id which
            # may have been None.
            if (
                resource_type_str in {"TENANT_USER", "STORE"}
                and params["tenant_id"] is not None
                and not route_to_platform
            ):
                # Rebuild SQL with the tenant table; the platform-
                # table SQL above is still valid for both tables since
                # column shapes are symmetric. Switch table name in
                # the prepared SQL.
                table = "tenant_activity_audit_logs"
            # Step 6.16.7 LD13 : INSERT statement extended from 14
            # explicit columns to 17 (adds actor_organization_name,
            # actor_roles, resource_subtype). Missing this retrofit
            # would cause NOT NULL violations on failure-path emissions
            # post-migration.
            sql_resolved = text(
                f"""
                INSERT INTO {schema}.{table} (
                    tenant_id, tenant_name,
                    actor_user_id, actor_user_type, actor_display_name,
                    actor_organization_name, actor_roles,
                    resource_type, resource_id, resource_label,
                    resource_subtype,
                    action, action_label,
                    result_type, result_label,
                    request_id, details
                ) VALUES (
                    :tenant_id, :tenant_name,
                    :actor_user_id,
                    CAST(:actor_user_type AS {schema}.actor_user_type_enum),
                    :actor_display_name,
                    :actor_organization_name, :actor_roles,
                    :resource_type, :resource_id, :resource_label,
                    :resource_subtype,
                    :action, :action_label,
                    CAST(:result_type AS {schema}.audit_result_type_enum),
                    :result_label,
                    :request_id,
                    CAST(:details AS jsonb)
                )
                """
            )
            await conn.execute(sql_resolved, params)
    except Exception as exc:
        _logger.critical(
            "audit emission failed on failure path",
            extra={
                "request_id": str(request_id),
                "action": action,
                "result_type": result_type.value,
                "exception_type": type(exc).__name__,
                "exception_message": str(exc),
            },
        )
        # Swallow : audit-emission failure on the failure path must
        # not block the user-facing error envelope.


# ---------------------------------------------------------------------------
# Convenience helpers for the exception handler (Step 6.16.2 + later)
# ---------------------------------------------------------------------------


def route_template_for_request(request: Any) -> str | None:
    """Extract the matched FastAPI route template from a request.

    Returns the route's path template (e.g.
    ``"/api/v1/tenants/{tenant_id}/suspend"``) or None if no route
    matched (the request errored before route resolution; e.g., 404
    Not Found from a non-existent URL).
    """
    route = request.scope.get("route")
    if route is None:
        return None
    path: Any = getattr(route, "path", None)
    if isinstance(path, str):
        return path
    return None


def request_id_for_request(request: Any) -> UUID:
    """Read request_id from request.state, generating a fresh UUID if
    absent (defensive; in practice the audit-context middleware sets
    this before any exception handler sees the request)."""
    raw = getattr(request.state, "request_id", None)
    if raw is None:
        return uuid4()
    if isinstance(raw, UUID):
        return raw
    return UUID(str(raw))


def now_utc() -> datetime:
    """Audit-row timestamps generally come from the DB default (now()).
    This helper exists for tests or paths that build a row in Python
    before the INSERT and want a consistent UTC stamp."""
    return datetime.now(timezone.utc)
