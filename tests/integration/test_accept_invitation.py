"""Step CI-4c integration tests: invite-accept (INVITED -> ACTIVE + auth0_sub).

DB-TIER (needs Postgres + the live ck_tenant_users_auth0_sub_consistency constraint
+ RLS). Not in the ci/unit gate; validated in a DB run. The PLATFORM-guard branch
is the only DB-free piece and is unit-tested in
tests/unit/test_accept_invitation_guard.py.

Two layers:
  - Repo: accept_invitation does the atomic INVITED -> ACTIVE + auth0_sub UPDATE
    (constraint-safe), is idempotent (already-active), classifies not-found, and
    emits the ACCEPT_INVITATION audit row same-transaction.
  - Endpoint: POST /api/v1/me/accept-invitation happy / idempotent / not-found /
    platform-403, self-only via the verified token.
"""
import uuid
from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import text

from admin_backend.auth.context import AuthContext
from admin_backend.auth.testing import make_test_jwt
from admin_backend.config import Settings, get_settings
from admin_backend.repositories.tenant_users import (
    AcceptInvitationResult,
    TenantUsersRepo,
)

_repo = TenantUsersRepo()
_ENDPOINT = "/api/v1/me/accept-invitation"


def _auth_header(jwt: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {jwt}"}


def _tenant_ctx(tenant_id: uuid.UUID, user_id: uuid.UUID) -> AuthContext:
    """A TENANT AuthContext for the accepting user (repo-layer audit actor)."""
    return AuthContext(
        sub=f"auth0|{user_id}",
        iss="https://stub-issuer.local/",
        aud="https://api.test/",
        exp=9999999999,
        user_id=user_id,
        tenant_id=tenant_id,
        user_type="TENANT",
        email="invitee@tenant.test",
    )


# ---------------------------------------------------------------------------
# Repo layer
# ---------------------------------------------------------------------------


async def test_repo_accept_activates_and_records_auth0_sub(
    make_tenant: Callable[..., Any],
    make_tenant_user: Callable[..., Any],
    tenant_session_factory: Callable[..., Any],
) -> None:
    tenant = await make_tenant(name="Accept Co")
    user = await make_tenant_user(tenant_id=tenant.id, status="INVITED")
    sub = f"auth0|accepted-{user.id}"

    async with tenant_session_factory(tenant.id) as session:
        row, outcome = await _repo.accept_invitation(
            session, user_id=user.id, tenant_id=tenant.id, auth0_sub=sub
        )
    assert outcome is AcceptInvitationResult.ACTIVATED
    assert row is not None
    assert row.user.status.value == "ACTIVE"
    assert row.user.auth0_sub == sub

    # Persisted + constraint satisfied (re-read in a fresh tenant session).
    async with tenant_session_factory(tenant.id) as session:
        persisted = (
            await session.execute(
                text(
                    f"SELECT status, auth0_sub, invitation_accepted_at "
                    f"FROM {get_settings().db_schema}.tenant_users WHERE id = :id"
                ),
                {"id": user.id},
            )
        ).first()
    assert persisted is not None
    assert str(persisted.status) == "ACTIVE"
    assert persisted.auth0_sub == sub
    assert persisted.invitation_accepted_at is not None


async def test_repo_accept_is_idempotent_when_already_active(
    make_tenant: Callable[..., Any],
    make_tenant_user: Callable[..., Any],
    tenant_session_factory: Callable[..., Any],
) -> None:
    tenant = await make_tenant(name="Idem Co")
    user = await make_tenant_user(tenant_id=tenant.id, status="ACTIVE")
    async with tenant_session_factory(tenant.id) as session:
        row, outcome = await _repo.accept_invitation(
            session, user_id=user.id, tenant_id=tenant.id, auth0_sub="auth0|ignored"
        )
    assert outcome is AcceptInvitationResult.ALREADY_ACTIVE
    assert row is None


async def test_repo_accept_not_found_for_unknown_user(
    make_tenant: Callable[..., Any],
    tenant_session_factory: Callable[..., Any],
) -> None:
    tenant = await make_tenant(name="Missing Co")
    async with tenant_session_factory(tenant.id) as session:
        row, outcome = await _repo.accept_invitation(
            session, user_id=uuid.uuid4(), tenant_id=tenant.id, auth0_sub="auth0|x"
        )
    assert outcome is AcceptInvitationResult.NOT_FOUND
    assert row is None


async def test_repo_accept_emits_audit_same_transaction(
    make_tenant: Callable[..., Any],
    make_tenant_user: Callable[..., Any],
    tenant_session_factory: Callable[..., Any],
) -> None:
    tenant = await make_tenant(name="Audit Co")
    user = await make_tenant_user(tenant_id=tenant.id, status="INVITED")
    request_id = uuid.uuid4()

    async with tenant_session_factory(tenant.id) as session:
        _row, outcome = await _repo.accept_invitation(
            session,
            user_id=user.id,
            tenant_id=tenant.id,
            auth0_sub=f"auth0|{user.id}",
            auth=_tenant_ctx(tenant.id, user.id),
            request_id=request_id,
        )
    assert outcome is AcceptInvitationResult.ACTIVATED

    async with tenant_session_factory(tenant.id) as session:
        audit = (
            await session.execute(
                text(
                    f"SELECT action, resource_type FROM "
                    f"{get_settings().db_schema}.tenant_activity_audit_logs "
                    "WHERE resource_id = :rid AND action = 'ACCEPT_INVITATION'"
                ),
                {"rid": user.id},
            )
        ).first()
    assert audit is not None
    assert str(audit.action) == "ACCEPT_INVITATION"
    assert str(audit.resource_type) == "TENANT_USER"


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


async def test_endpoint_happy_then_idempotent(
    client: TestClient,
    settings: Settings,
    make_tenant: Callable[..., Any],
    make_tenant_user: Callable[..., Any],
) -> None:
    """First call activates (200, activated=true); the repeat is idempotent
    (200, activated=false). The self-only scope is enforced by the verified
    token: the JWT carries the invited user's own user_id + tenant_id."""
    tenant = await make_tenant(name="Endpoint Co")
    user = await make_tenant_user(tenant_id=tenant.id, status="INVITED")
    token = make_test_jwt(
        settings, user_id=user.id, user_type="TENANT", tenant_id=tenant.id
    )

    resp = client.post(_ENDPOINT, headers=_auth_header(token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ACTIVE"
    assert body["activated"] is True
    assert body["user_id"] == str(user.id)

    # Repeat: already ACTIVE -> idempotent success, activated=false.
    repeat = client.post(_ENDPOINT, headers=_auth_header(token))
    assert repeat.status_code == 200, repeat.text
    assert repeat.json()["activated"] is False


def test_endpoint_platform_caller_forbidden(
    client: TestClient, settings: Settings
) -> None:
    token = make_test_jwt(settings, user_id=uuid.uuid4(), user_type="PLATFORM")
    resp = client.post(_ENDPOINT, headers=_auth_header(token))
    assert resp.status_code == 403
    assert resp.json()["code"] == "PERMISSION_DENIED"


def test_endpoint_not_found_for_unmatched_user(
    client: TestClient, settings: Settings
) -> None:
    # A valid TENANT token whose user_id matches no tenant_users row.
    token = make_test_jwt(
        settings,
        user_id=uuid.uuid4(),
        user_type="TENANT",
        tenant_id=uuid.uuid4(),
    )
    resp = client.post(_ENDPOINT, headers=_auth_header(token))
    assert resp.status_code == 404
    assert resp.json()["code"] == "TENANT_USER_NOT_FOUND"
