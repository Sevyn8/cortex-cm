"""Post-commit Auth0 user provisioning (Step CI-4b).

When CM creates a tenant_user (INVITED), this task runs AFTER the DB row commits
(scheduled via FastAPI BackgroundTasks, which fire after the response and after the
session dependency's commit-in-teardown). It uses the Step 4a Auth0ManagementClient
to create the Auth0 user (with app_metadata) and issue an invitation ticket.

FAIL-SAFE: the task NEVER raises. The response already went out; the committed CM
row is a valid, re-provisionable INVITED user. On Auth0ManagementError the task logs
and returns. It does NOT roll back, does NOT write auth0_sub (recording auth0_sub and
INVITED->ACTIVE is the invite-accept callback, Step 4c; the DB constraint forbids
auth0_sub while INVITED), and does NOT raise to the client.

The task captures DETACHED PRIMITIVES (UUID/str), never the request session (gone
post-commit) or a session-bound ORM object, so nothing it reads can touch the closed
session. No secret is ever logged.
"""
import logging
from uuid import UUID

from admin_backend.auth.auth0_management import Auth0ManagementClient
from admin_backend.errors import Auth0ManagementError

_log = logging.getLogger("admin_backend.auth.provisioning")


async def provision_auth0_user(
    client: Auth0ManagementClient | None,
    *,
    user_id: UUID,
    tenant_id: UUID,
    email: str,
    user_type: str,
    request_id: UUID | None,
) -> None:
    """Create the Auth0 user + invitation ticket for a freshly-created INVITED user.

    Args are detached primitives captured at schedule time. ``client`` is None when
    the Auth0 Management API is unconfigured (STUB/dev): the task logs and skips.
    """
    log_context = {
        "operation": "provision",
        "user_id": str(user_id),
        "tenant_id": str(tenant_id),
        "request_id": str(request_id) if request_id is not None else None,
    }

    if client is None:
        # Unconfigured (STUB/dev): no Auth0 to call. The user stays INVITED.
        _log.info("auth0 provisioning skipped (management client unconfigured)", extra=log_context)
        return

    # app_metadata is JSON, so UUIDs are serialized to strings. user_type is "TENANT"
    # (this is the tenant-user creation path).
    app_metadata = {
        "tenant_id": str(tenant_id),
        "user_type": user_type,
        "user_id": str(user_id),
    }

    try:
        created = await client.create_user(email, app_metadata)
        await client.create_invitation_ticket(created["user_id"])
    except Auth0ManagementError as exc:
        # Fail-safe: swallow. The committed row stays a valid re-provisionable INVITED
        # user; a later reconcile/retry can complete provisioning. No auth0_sub write,
        # no rollback, no raise. The error context carries no secret.
        _log.warning(
            "auth0 provisioning failed; user left INVITED (re-provisionable)",
            extra={**log_context, "auth0_operation": exc.context.get("operation")},
        )
        return

    _log.info("auth0 user provisioned and invitation issued", extra=log_context)
