"""Error class hierarchy for admin-backend.

Step 2.1 introduced bare exception types. Step 2.3 refactors them into
a two-tier structured hierarchy with HTTP-response mapping:

  AdminBackendError
    ClientError    (4xx; subclass-specific public_message is fine)
      AuthMissingError       (401)
      AuthInvalidError       (401)
      InvalidTenantIdError   (401, merges into AUTH_INVALID)
    ServerError    (5xx; ALWAYS returns the generic INTERNAL_ERROR
                   response to clients; specifics go to the log only)
      AppRolePrivilegeError  (was in db/engine.py)

The refactor is backwards-compatible: existing code that does
``raise AuthMissingError("...")`` still works because the constructor
keeps the same signature (a single positional internal_message + **context
kwargs). FastAPI's exception handler in main.py reads ``http_status``,
``public_message``, and ``code`` off the class to build the JSON response.

ServerError subclasses MUST NOT override ``public_message`` or ``code``.
This is anti-information-disclosure: an attacker probing the auth or DB
layer should learn nothing about the internal failure shape from the
response body. Subclasses override only ``internal_message`` (via the
constructor) for log clarity.
"""
from typing import Any


class AdminBackendError(Exception):
    """Base for all admin-backend errors.

    Class attributes drive the HTTP response (read by the FastAPI
    exception handler):
        public_message: returned in the JSON body's "message" field.
        http_status: HTTP status code returned to the client.
        code: machine-readable error code in the JSON body's "code" field.

    Constructor:
        internal_message: log-only context; never reaches the client.
        **context: structured-logging fields attached to the exception.
    """

    public_message: str = "An error occurred"
    http_status: int = 500
    code: str = "INTERNAL_ERROR"

    def __init__(self, internal_message: str, **context: Any) -> None:
        super().__init__(internal_message)
        self.internal_message = internal_message
        self.context: dict[str, Any] = context


class ClientError(AdminBackendError):
    """4xx-class: caller's fault.

    Subclass-specific ``public_message`` is fine: the caller knows what
    they sent, so telling them what was wrong with it is not disclosure.
    """

    http_status: int = 400
    code: str = "CLIENT_ERROR"
    public_message: str = "The request is invalid"


class ServerError(AdminBackendError):
    """5xx-class: our fault.

    ALWAYS returns INTERNAL_ERROR / "An internal error occurred" to the
    client, regardless of subclass. Subclass-specific information is
    only ever surfaced in internal logs (via ``internal_message`` and
    ``context``). Subclasses MUST NOT override ``public_message`` or
    ``code``; they communicate specifics by their type name and
    constructor argument.
    """

    http_status: int = 500
    code: str = "INTERNAL_ERROR"
    public_message: str = "An internal error occurred"


class AuthMissingError(ClientError):
    """JWT not provided where one was required."""

    public_message = "Authentication required"
    http_status = 401
    code = "AUTH_MISSING"


class AuthInvalidError(ClientError):
    """JWT provided but invalid (signature, expiry, audience, issuer,
    malformed claims)."""

    public_message = "Authentication invalid"
    http_status = 401
    code = "AUTH_INVALID"


class InvalidTenantIdError(ClientError):
    """tenant_id claim is present but cannot be parsed as a UUID.

    Surfaces to the client as the same generic AUTH_INVALID as other
    JWT-shape failures. Don't leak that the tenant_id specifically was
    the broken claim.
    """

    public_message = "Authentication invalid"
    http_status = 401
    code = "AUTH_INVALID"


class TenantNotFoundError(ClientError):
    """Tenant id either does not exist or is RLS-filtered from caller.

    Per D-17, RLS-filtered rows surface as 404 not 403: returning 403
    leaks that the resource exists. The handler can't (and shouldn't)
    distinguish "no such tenant" from "you can't see this tenant" —
    both produce the same 404.
    """

    public_message = "Tenant not found"
    http_status = 404
    code = "TENANT_NOT_FOUND"


class TenantUserNotFoundError(ClientError):
    """Tenant user id either does not exist or is RLS-filtered from caller.

    Same RLS-as-404 framing as ``TenantNotFoundError`` per D-17. Moved
    from ``routers/v1/tenant_users.py`` to the shared module at Step
    6.9.3.2 so anchor deps in ``auth/`` can raise it without backward
    layering violation (``auth/`` → ``routers/v1/``).
    """

    public_message = "Tenant user not found"
    http_status = 404
    code = "TENANT_USER_NOT_FOUND"


class OrgNodeNotFoundError(ClientError):
    """Org node id either does not exist or is RLS-filtered from caller.

    Same RLS-as-404 framing as ``TenantNotFoundError`` per D-17. Moved
    from ``routers/v1/org_tree.py`` to the shared module at Step
    6.9.3.2 so anchor deps in ``auth/`` can raise it without backward
    layering violation.
    """

    public_message = "Org node not found"
    http_status = 404
    code = "ORG_NODE_NOT_FOUND"


class StoreNotFoundError(ClientError):
    """Store id either does not exist or is RLS-filtered from caller.

    Same RLS-as-404 framing as ``TenantNotFoundError`` per D-17. Raised
    by ``get_store_anchor`` on lookup miss (the anchor dep fires
    before the gate body per F-THREADING-4, so cross-tenant probes by
    TENANT JWTs surface as 404 here rather than 403) and by the detail
    router when the Repo returns ``None``.
    """

    public_message = "Store not found"
    http_status = 404
    code = "STORE_NOT_FOUND"


class InvalidSortKeyClientError(ClientError):
    """Raised by routers when a query-param ``sort`` value isn't recognised.

    Wraps the Repo-layer ``InvalidSortKeyError`` (a ValueError) so
    unknown sort values surface as 400 ``INVALID_SORT_KEY`` instead
    of 500 ``INTERNAL_ERROR``. Shared across resources — sort-key
    validation is the same concern for every Repo. Introduced at
    Step 5.1 inside the platform_users router; promoted to shared
    location at Step 5.2 so tenant_users (and future routers) reuse
    the same class.
    """

    public_message = "Invalid sort key"
    http_status = 400
    code = "INVALID_SORT_KEY"


class PermissionDeniedError(ClientError):
    """Raised by the ``require(...)`` gate when ``has_permission()`` denies.

    Step 6.9.2 introduces this. Structured fields (``module``, ``resource``,
    ``action``, ``scope``, ``target_anchor``, ``reason_code``) attach via
    the inherited ``**context`` kwargs mechanism on ``AdminBackendError``
    and reach the error log via ``exc.context``; they do NOT populate the
    response envelope's ``details`` field per the Step 6.9.2 Q7 design.

    Lives in ``errors.py`` (shared) rather than in the me router, mirroring
    ``InvalidSortKeyClientError`` (Step 5.2). Every router that retrofits
    the gate in Step 6.9.3 will raise this same class.
    """

    public_message = "Permission denied"
    http_status = 403
    code = "PERMISSION_DENIED"


class PlatformAudienceRequiredError(ClientError):
    """Raised by the ``require(...)`` gate's Layer 1 audience check when
    a route declares ``audience="PLATFORM"`` and the JWT's ``user_type``
    is not ``PLATFORM``.

    Step 6.11.1 introduces this alongside the ``audience`` kwarg on
    ``require()``. Defense-in-depth ahead of ``has_permission`` against
    catalogue drift: a future seed that grants a ``.GLOBAL`` tuple to a
    TENANT-audience role would still be refused by Layer 1 on
    platform-only routes.
    """

    public_message = "This operation requires a platform user."
    http_status = 403
    code = "PLATFORM_AUDIENCE_REQUIRED"


class DuplicateTenantNameError(ClientError):
    """Raised by ``TenantsRepo.create`` or ``.update`` (on rename) when
    a tenant with the supplied ``name`` already exists.

    App-layer uniqueness check: ``core.tenants.name`` has no DB-level
    UNIQUE constraint in v0 (see the matching FN-AB on tenant name
    UNIQUE). The check is SELECT-then-INSERT/UPDATE in the same
    transaction; race window non-zero under concurrent writers.
    """

    public_message = "A tenant with this name already exists."
    http_status = 409
    code = "DUPLICATE_TENANT_NAME"


class InvalidTenantNameForSlugError(ClientError):
    """Raised by ``slug_for_tenant_root`` (Step 6.20.1) when the input
    name (or display_code) slugifies to an empty string.

    The slug rule strips diacritics, collapses non-alphanumeric runs to
    hyphens, trims, and truncates at 64 chars. Inputs like ``!!!`` or
    ``   `` produce no surviving alphanumerics and would yield an empty
    code/path; the helper rejects rather than inventing a placeholder.

    Constructor accepts ``field`` ('name' or 'display_code') to identify
    which request field produced the empty slug; the value is placed in
    ``exc.context`` for log paths only per the Q7 envelope convention.
    """

    public_message = (
        "Tenant name produces an empty identifier; supply a display_code "
        "or use a name with alphanumeric characters."
    )
    http_status = 422
    code = "INVALID_TENANT_NAME_FOR_SLUG"


class InvalidStateTransitionError(ClientError):
    """Raised by the suspend/activate handlers when the current
    tenant ``status`` doesn't permit the requested transition.

    Lifecycle (Step 6.11): create -> TRIAL; TRIAL/ACTIVE -> SUSPENDED on
    suspend; TRIAL/SUSPENDED -> ACTIVE on activate. SUSPENDED -> ACTIVE
    never re-enters TRIAL. Any other source state for the requested
    target raises this error.
    """

    public_message = "Tenant cannot transition to the requested state."
    http_status = 409
    code = "INVALID_STATE_TRANSITION"


class EmptyPatchError(ClientError):
    """Raised by ``PATCH /tenants/{id}`` when the request body has no
    fields set.

    Handler-level check via ``body.model_dump(exclude_unset=True)`` —
    Pydantic's ``extra="forbid"`` on ``TenantPatchRequest`` accepts an
    empty object (all fields default to ``None``); the handler converts
    "nothing to update" into a domain-shaped 422.
    """

    public_message = "PATCH request must include at least one field."
    http_status = 422
    code = "EMPTY_PATCH"


class SelfEditForbiddenError(ClientError):
    """Raised when a TENANT-audience caller targets themselves on a
    tenant-user write endpoint (PATCH / suspend / activate).

    Step 6.10.1. PLATFORM callers cannot self-edit by construction
    (PLATFORM users live in a separate table). The guard fires inside
    the handler for the three path-bound endpoints; POST /tenant-users
    has no path user_id so the case isn't expressible there.
    """

    public_message = "You cannot perform this action on yourself."
    http_status = 403
    code = "SELF_EDIT_FORBIDDEN"


class DuplicateTenantUserEmailError(ClientError):
    """Raised by ``TenantUsersRepo.create`` / ``.update`` (on email
    change) when another tenant_user in the same tenant already holds
    the supplied email.

    Per-tenant uniqueness is enforced at the schema layer via
    ``uq_tenant_users_tenant_email``; the app-layer pre-check (same
    transaction) surfaces the conflict as a domain-shaped 409 rather
    than letting the unique-index violation surface as 500.
    """

    public_message = "A user with this email already exists in this tenant."
    http_status = 409
    code = "DUPLICATE_TENANT_USER_EMAIL"


class InvalidRoleAudienceError(ClientError):
    """Raised when ``roles[]`` contains a role_id whose audience is not
    'TENANT' (Step 6.10.1 Option X pre-check).

    The audience-check trigger ``enforce_tenant_role_audience`` rejects
    mismatched audience at INSERT time with a plpgsql exception that
    would otherwise surface as 500. The handler-side pre-check converts
    that into a domain-shaped 422 ahead of the DB write.

    Per the Q7 lock (Step 6.9.2), structured detail surfaces in
    ``exc.context`` for logs; the response envelope ``details`` field
    stays ``null``. Callers pass ``invalid_role_ids=[...]`` via the
    ``**context`` kwarg mechanism.
    """

    public_message = "One or more roles cannot be assigned to a tenant user."
    http_status = 422
    code = "INVALID_ROLE_AUDIENCE"


class InvalidRoleError(ClientError):
    """Raised when ``roles[]`` references a role_id that doesn't exist
    in the catalogue (Step 6.10.1 Option X pre-check).

    Same Q7 posture as ``InvalidRoleAudienceError``: structured detail
    (``unknown_role_ids``) lives in ``exc.context``; response envelope
    ``details`` stays ``null``.
    """

    public_message = "One or more role IDs do not exist."
    http_status = 422
    code = "INVALID_ROLE"


class InvalidOrgNodeError(ClientError):
    """Raised when ``roles[]`` references an ``org_node_id`` that is
    missing from the catalogue, archived, or belongs to a different
    tenant (Step 6.14).

    Aggregates all three failure modes into one 422 code so the
    response is deterministic regardless of which specific defect the
    request hit. The composite FK
    ``fk_tenant_user_role_assignments_org_node_same_tenant`` would
    catch the cross-tenant case at INSERT time too; the pre-check
    surfaces it as a clean 422 ahead of the write.

    Q7 posture: structured detail (``invalid_org_node_ids``) lives in
    ``exc.context``; response envelope ``details`` stays ``null``.
    """

    public_message = (
        "One or more org_node IDs do not exist or cannot be used as "
        "an anchor."
    )
    http_status = 422
    code = "INVALID_ORG_NODE"


class DuplicateRoleAssignmentInRequestError(ClientError):
    """Raised when the submitted ``roles[]`` list contains the same
    ``(role_id, org_node_id)`` tuple more than once (Step 6.14).

    Handler-side pre-check ahead of the repo so the duplicate-detection
    response envelope is uniform with the rest of the
    AdminBackendError family. Without this, the duplicate would surface
    later as a UNIQUE-index violation on
    ``uq_tenant_user_role_assignments_active`` and be misclassified as
    a 409 conflict rather than a 422 client bug.

    Q7 posture: structured detail (``duplicate_pairs`` — list of
    ``{role_id, org_node_id}`` dicts) lives in ``exc.context``;
    response envelope ``details`` stays ``null``.
    """

    public_message = (
        "Duplicate (role_id, org_node_id) entries in roles[]."
    )
    http_status = 422
    code = "DUPLICATE_ROLE_ASSIGNMENT_IN_REQUEST"


class RoleAssignmentConflictError(ClientError):
    """Raised when a concurrent transaction has inserted a duplicate
    ACTIVE ``(tenant_user_id, role_id, org_node_id)`` row between this
    transaction's SELECT FOR UPDATE and the matching INSERT (Step
    6.14).

    Mapped from ``IntegrityError`` on
    ``uq_tenant_user_role_assignments_active`` ONLY. Other constraint
    violations propagate so they surface as 500 (real bugs) rather
    than be misclassified as 409 conflicts. The constraint-name match
    is the load-bearing scoping mechanism.

    No automatic retry: concurrent operator edits to the same
    tenant_user are a real conflict the caller should be made aware of,
    not papered over.

    Q7 posture: structured detail (``conflicting_triple`` —
    ``{tenant_user_id, role_id, org_node_id}``) lives in
    ``exc.context``; response envelope ``details`` stays ``null``.
    """

    public_message = (
        "A concurrent edit produced a conflicting role assignment. "
        "Retry the request after re-reading the user's current "
        "assignments."
    )
    http_status = 409
    code = "ROLE_ASSIGNMENT_CONFLICT"


class InvalidParentNodeTypeError(ClientError):
    """Raised when an org-tree write proposes a parent whose ``node_type``
    does not sit higher than the child's in the canonical hierarchy
    (Step 6.13).

    Canonical sequence: TENANT(0) -> BUSINESS_UNIT(1) -> HQ(2) ->
    COUNTRY(3) -> REGION(4) -> STORE(5) -> DEPARTMENT(6). Level skipping
    is allowed (STORE under TENANT is fine); level reversal is not
    (HQ under REGION rejected).

    Q7 posture: structured detail (``child_type``, ``parent_type``,
    ``attempted_ordinal_child``, ``attempted_ordinal_parent``) lives in
    ``exc.context``; response envelope ``details`` stays ``null``.
    """

    public_message = (
        "Parent node's type is not above the child's in the org "
        "hierarchy."
    )
    http_status = 422
    code = "INVALID_PARENT_NODE_TYPE"


class TenantRootNotReparentableError(ClientError):
    """Raised when PATCH attempts to set ``parent_id`` on a TENANT-type
    org_node (Step 6.13).

    The tenant root is created by tenant provisioning and is structurally
    pinned by ``ck_org_nodes_root_parent_consistency`` (TENANT-type rows
    must have NULL parent_id). PATCH can rename a tenant root (name,
    code) but cannot move it under another node.
    """

    public_message = (
        "The tenant root cannot be reparented."
    )
    http_status = 422
    code = "TENANT_ROOT_NOT_REPARENTABLE"


class CycleDetectedError(ClientError):
    """Raised when PATCH attempts to reparent a node under itself or one
    of its descendants (Step 6.13).

    Detection uses ltree's ``@>`` operator on the target's path: the
    new parent must NOT be a descendant of the target. Self-parent is
    the degenerate case (target.path @> target.path is true) and surfaces
    here too.

    Q7 posture: structured detail (``target_id``, ``attempted_parent_id``)
    lives in ``exc.context``; response envelope ``details`` stays
    ``null``.
    """

    public_message = (
        "Reparenting would create a cycle in the org tree."
    )
    http_status = 422
    code = "CYCLE_DETECTED"


class DuplicateOrgNodeCodeError(ClientError):
    """Raised when an org-tree write produces a ``code`` value that
    collides with an existing row in the same tenant (Step 6.13).

    Tenant-wide case-insensitive uniqueness is enforced by the DDL UNIQUE
    index ``uq_org_nodes_tenant_code_lower`` on ``(tenant_id,
    lower(code))``. App-layer maps the ``IntegrityError`` scoped to that
    constraint name into a domain-shaped 409.

    Q7 posture: structured detail (``code``, ``tenant_id``) lives in
    ``exc.context``; response envelope ``details`` stays ``null``.
    """

    public_message = (
        "An org node with this code already exists in this tenant."
    )
    http_status = 409
    code = "DUPLICATE_ORG_NODE_CODE"


class ParentNodeNotFoundError(ClientError):
    """Raised when an org-tree write references a ``parent_id`` that
    does not exist in the same tenant or is RLS-filtered (Step 6.13).

    Distinct from ``OrgNodeNotFoundError`` (which targets the operand
    node on PATCH); this surfaces when the proposed parent is the missing
    row. The wire code disambiguates the two; both are 404.

    Q7 posture: structured detail (``parent_id``, ``tenant_id``) lives
    in ``exc.context``; response envelope ``details`` stays ``null``.
    """

    public_message = "Parent org node not found."
    http_status = 404
    code = "PARENT_NODE_NOT_FOUND"


class DuplicateStoreCodeError(ClientError):
    """Raised when a write to ``core.stores`` would produce a
    ``(tenant_id, store_code)`` value that already exists in the
    same tenant (Step 6.17.3).

    Case-insensitive uniqueness is enforced at the DDL layer by the
    partial unique index ``uq_stores_tenant_store_code_lower`` on
    ``(tenant_id, lower(store_code)) WHERE store_code IS NOT NULL``.
    The repo runs a SELECT-then-INSERT/UPDATE pre-check (case-insensitive
    to align with the index) so the conflict surfaces as a typed 409
    rather than a generic IntegrityError ``500``. Race window between
    pre-check and write is closed by the DDL index.

    Pre-check excludes self on rename (``id != :store_id``) so
    PATCH that keeps ``store_code`` unchanged is a no-op 200.

    Q7 posture: structured detail (``tenant_id``, ``store_code``) lives
    in ``exc.context``; response envelope ``details`` stays ``null``.
    """

    public_message = (
        "A store with this code already exists in this tenant."
    )
    http_status = 409
    code = "DUPLICATE_STORE_CODE"


class OrgNodeFieldNotAllowedForTypeError(ClientError):
    """Raised when PATCH /org-tree on a STORE-type target attempts to
    modify a shared field owned by the /stores endpoints (Step 6.21.2).

    The two-table-one-entity coupling between ``stores`` and the paired
    STORE-type ``org_nodes`` row (architecture.md A.4 / A.5) makes
    ``name`` and ``code`` owned by the ``/stores`` endpoints. Attempts
    to modify them via /org-tree on a STORE-type target produce 422 so
    the caller is directed to the resource-specific endpoint. Reparent
    (``parent_id``) remains allowed on STORE-type targets.

    Q7 posture: structured detail (``fields``, ``node_type``) lives in
    ``exc.context`` for log paths; response envelope ``details`` stays
    ``null``.
    """

    public_message = (
        "These fields cannot be modified via /org-tree for this node "
        "type. Use the resource-specific endpoint instead."
    )
    http_status = 422
    code = "ORG_NODE_FIELD_NOT_ALLOWED_FOR_TYPE"


class ModuleAccessNotFoundError(ClientError):
    """Raised by ``POST /api/v1/module-access/{tenant_id}/{module_code}/disable``
    when no ``tenant_module_access`` row exists for the supplied
    ``(tenant_id, module)`` pair (Step 6.15).

    Only the disable path raises this. The enable path upserts (creates
    on missing) so it can never produce this error. Cross-tenant probes
    surface as the upstream ``TenantNotFoundError`` (404) from the
    anchor dep before reaching the repo.

    Q7 posture: structured detail (``tenant_id``, ``module_code``)
    lives in ``exc.context`` for log paths; response envelope
    ``details`` stays ``null``.
    """

    public_message = (
        "Module access not found for the requested tenant and module."
    )
    http_status = 404
    code = "MODULE_ACCESS_NOT_FOUND"


class RoleArchivedError(ClientError):
    """PATCH refused on an ARCHIVED role (Step 6.18.3).

    Status transitions on roles (activate / deactivate) belong to a
    separate (not-yet-shipped) endpoint. PATCH is for content edits
    (name / description / permissions) only.

    Q7 posture: structured detail (``role_id``) lives in ``exc.context``
    for log paths; response envelope ``details`` stays ``null``.
    """

    public_message = "Cannot edit an archived role."
    http_status = 409
    code = "ROLE_ARCHIVED"


class InvalidPermissionError(ClientError):
    """Raised when ``permission_ids[]`` in a role PATCH body references
    one or more UUIDs that don't exist in ``core.permissions`` (Step
    6.18.3 LD11).

    Mirrors ``InvalidRoleError`` / ``InvalidOrgNodeError`` (Step 6.10.1
    / 6.14) naming convention. Detection runs as a SELECT count pre-
    check before the diff is computed; the missing ids surface in
    ``exc.context.missing_ids`` for log paths only per Q7.
    """

    public_message = "One or more permission IDs do not exist."
    http_status = 422
    code = "INVALID_PERMISSION_ID"


class AudienceScopeMismatchError(ClientError):
    """TENANT-audience role attempted to add a ``scope='GLOBAL'``
    permission (Step 6.18.3 LD10).

    Per LD2 audience-scope coherence rule: TENANT-audience roles cannot
    hold GLOBAL-scope permissions structurally. The pre-check is
    lenient: only the diff ``new - current`` (additions) is inspected,
    not the full current set. A pre-existing GLOBAL-scope grant on a
    TENANT role (catalogue drift) does not block the edit; only NEW
    GLOBAL additions are rejected.

    Q7 posture: structured detail (``role_audience``,
    ``offending_permission_ids``) lives in ``exc.context`` for log
    paths; response envelope ``details`` stays ``null``.
    """

    public_message = (
        "TENANT-audience roles cannot hold GLOBAL-scope permissions."
    )
    http_status = 422
    code = "AUDIENCE_SCOPE_MISMATCH"


class LastOverrideHolderError(ClientError):
    """Raised when an edit would zero out the active holder count of
    ADMIN.ROLES.OVERRIDE.GLOBAL (Step 6.18.3 LD6 Layer 1).

    Platform-wide invariant: at least one ACTIVE user (with status='ACTIVE'
    on BOTH the assignment row and the user row per LD7) must hold the
    OVERRIDE.GLOBAL permission through some role at all times. Without
    a holder, no one can edit role grants and the platform locks itself
    out.

    The check runs as a pre-write SELECT (Layer 1) and a post-write
    tripwire (Layer 2). Layer 1 raises THIS error; Layer 2 raises
    ``InternalInvariantViolationError`` (500 INTERNAL_ERROR on the
    wire) because Layer 2 firing indicates a bug in Layer 1.

    Q7 posture: structured detail (``role_id``) lives in
    ``exc.context`` for log paths; response envelope ``details`` stays
    ``null``.
    """

    public_message = (
        "Cannot remove the last active holder of platform admin "
        "permissions."
    )
    http_status = 409
    code = "LAST_OVERRIDE_HOLDER"


class SuperAdminProtectedError(ClientError):
    """PATCH refused on the SUPER_ADMIN role (Step 6.18.3 LD12).

    v0 lockout: name, description, and permission set on SUPER_ADMIN
    are not editable via the API. Operator workflow for SUPER_ADMIN
    edits is direct SQL on ``core.roles`` / ``core.role_permissions``
    via Cloud SQL Studio (operator-only path; not exposed to
    application code). v1 promotion of SUPER_ADMIN editability is
    deferred per FN-AB.

    Check fires BEFORE the status check per LD18: an ARCHIVED
    SUPER_ADMIN would still be protected (defensive — SUPER_ADMIN is
    never expected ARCHIVED in v0).

    Q7 posture: structured detail (``role_id``, ``role_code``) lives
    in ``exc.context``; response envelope ``details`` stays ``null``.
    """

    public_message = (
        "SUPER_ADMIN role cannot be edited via the API in v0."
    )
    http_status = 409
    code = "SUPER_ADMIN_PROTECTED"


class AuditEventNotFoundError(ClientError):
    """Audit row either does not exist or is RLS-filtered from caller.

    Same RLS-as-404 framing as ``TenantNotFoundError`` per D-17. Raised
    by ``GET /api/v1/audit/activities/{audit_row_id}`` (Step 6.16.3) when
    ``AuditLogsRepo.get_by_id`` returns ``None`` from probing both
    audit tables. For a TENANT JWT this covers:

      - The id is genuinely missing.
      - The id exists in ``tenant_activity_audit_logs`` but belongs to
        another tenant (RLS hides it).
      - The id exists in ``platform_activity_audit_logs`` (RLS does
        not apply there; the repo's probe order tenant-then-platform
        means a tenant caller's platform-side hit would still surface
        as 404 because the row is invisible to the caller's audience).

    PLATFORM callers also see this when probing a non-existent UUID;
    the platform branch has no RLS but the missing row is still a 404.

    Q7 posture: structured detail (``audit_row_id``) lives in
    ``exc.context`` for log paths; response envelope ``details`` stays
    ``null``.
    """

    public_message = "Audit event not found"
    http_status = 404
    code = "AUDIT_EVENT_NOT_FOUND"


class InvalidCursorError(ClientError):
    """Raised by ``AuditLogsRepo._decode_cursor`` (Step 6.16.3) when the
    opaque ``cursor`` query parameter on the list endpoint cannot be
    decoded.

    Failure categories collapsed under one code:
      - Malformed base64 / padding error.
      - Decoded payload is not valid JSON.
      - JSON payload lacks the required ``ts`` / ``id`` keys.
      - ``ts`` value cannot be parsed as ISO-8601 datetime.
      - ``id`` value cannot be parsed as UUID.

    Q7 posture: structured detail (``reason``, a server-side category
    string) lives in ``exc.context`` for log paths; response envelope
    ``details`` stays ``null``. ``reason`` is diagnostic, not a stable
    machine-parseable contract.
    """

    public_message = "The pagination cursor is malformed or expired."
    http_status = 422
    code = "INVALID_CURSOR"


class InternalInvariantViolationError(ServerError):
    """Layer 2 OVERRIDE.GLOBAL tripwire (Step 6.18.3 LD6).

    Layer 1 (pre-check) said the edit was safe; Layer 2 (post-write,
    pre-commit) found the platform-wide invariant violated. Indicates
    a bug in Layer 1's logic OR a write defect (e.g., a concurrent
    edit raced past Layer 1's snapshot).

    Class name is for log clarity only. Per the ServerError
    anti-information-disclosure rule, the wire emits the generic
    INTERNAL_ERROR shape; the class type and ``internal_message`` +
    ``context`` (``role_id``, ``layer_1_count``, ``layer_2_count``)
    surface only to internal logs.

    Triggers ROLLBACK on the request transaction (the session-dep
    rolls back on any exception escape; no explicit rollback needed in
    the repo).
    """


class AppRolePrivilegeError(ServerError):
    """Application role has SUPERUSER or BYPASSRLS; refuses to start.

    A startup-time error; it should never reach the request path under
    normal operation. Inheriting ServerError keeps the response shape
    consistent if it ever does (e.g., a privileged role is rotated
    in mid-run): client sees INTERNAL_ERROR / generic message; operators
    see the specific type and remediation in the log line.
    """


class Auth0ManagementError(ServerError):
    """An Auth0 Management API call failed (Step CI-4a).

    Raised by Auth0ManagementClient for any outbound failure: a non-2xx
    Auth0 response, a network/transport/timeout error, a token-acquisition
    failure, or missing M2M configuration. ServerError parent: an upstream
    identity-provider failure is our/infra's concern, not the caller's, and
    the wire emits the generic INTERNAL_ERROR shape.

    The provisioning caller (Step 4b, post-commit) catches this and leaves
    the user INVITED / re-provisionable rather than failing the request.

    SECURITY: the constructor ``context`` carries only non-sensitive
    diagnostics (operation, HTTP status, cause-type). The M2M client secret
    and the bearer token MUST NEVER be passed into ``context`` (or logged):
    they are not request data and would leak into log sinks.
    """


def build_error_payload(
    exc: AdminBackendError, request_id: str | None
) -> tuple[int, dict[str, Any], dict[str, str]]:
    """Convert an AdminBackendError into the (status, body, headers)
    triple used by the HTTP response.

    Two-path: ServerError subclasses always yield the generic
    INTERNAL_ERROR shape (anti-information-disclosure); ClientError
    subclasses surface their subclass-specific code/message.

    Used by both the FastAPI exception handler (for errors raised in
    route handlers) and the auth middleware (for errors raised in
    middleware, which Starlette's BaseHTTPMiddleware does not route
    through the FastAPI exception handler).
    """
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
