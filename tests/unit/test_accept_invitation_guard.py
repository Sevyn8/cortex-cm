"""Step CI-4c unit test: the PLATFORM guard on POST /me/accept-invitation.

This is the one branch of the endpoint that is genuinely DB-free: the
``user_type != "TENANT"`` guard runs BEFORE any DB access. We prove that by
passing a tripwire session whose every attribute access raises, then asserting
the handler raises PermissionDeniedError WITHOUT touching the session. So this
fits the ci/unit gate (no DB, no network). The activated / idempotent /
not-found / not-invited behavior exercises the real constraint + RLS and lives
in the integration tier (tests/integration).
"""
from uuid import uuid4

import pytest

from admin_backend.auth.context import AuthContext
from admin_backend.errors import PermissionDeniedError
from admin_backend.routers.v1.me import accept_invitation


class _TripwireSession:
    """Any attribute access fails the test: proves the session is never touched."""

    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"session was touched (attr {name!r}); guard must run first")


class _TripwireRequest:
    """Same tripwire for the request: the guard returns before reading it."""

    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"request was touched (attr {name!r}); guard must run first")


def _platform_auth() -> AuthContext:
    return AuthContext(
        sub="auth0|platformadmin",
        iss="https://stub-issuer.local/",
        aud="https://api.test/",
        exp=9999999999,
        user_id=uuid4(),
        tenant_id=None,
        user_type="PLATFORM",
        email="staff@sevyn8.test",
    )


async def test_platform_caller_rejected_before_any_db_access() -> None:
    """A PLATFORM token is refused (403) and the session is never touched."""
    with pytest.raises(PermissionDeniedError):
        await accept_invitation(
            _TripwireRequest(),  # type: ignore[arg-type]
            auth=_platform_auth(),
            session=_TripwireSession(),  # type: ignore[arg-type]
        )
    # Reaching here without an AssertionError from the tripwires proves neither the
    # session nor the request was touched: the guard short-circuits first (DB-free).
