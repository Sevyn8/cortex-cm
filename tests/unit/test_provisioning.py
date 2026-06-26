"""Step CI-4b unit tests for provision_auth0_user.

The post-commit provisioning task is a standalone async function taking the Auth0
management client as an argument, so it is tested DIRECTLY with a fake client: NO
network, NO DB, NO secret. Covers success, Auth0 failure (swallowed, fail-safe),
invitation-ticket failure (swallowed), and the unconfigured (None client) no-op.
"""
from uuid import uuid4

from admin_backend.auth.provisioning import provision_auth0_user
from admin_backend.errors import Auth0ManagementError

_USER_ID = uuid4()
_TENANT_ID = uuid4()
_REQUEST_ID = uuid4()


class _FakeClient:
    """Records calls; configurable to raise on a chosen operation."""

    def __init__(self, *, fail_on: str | None = None) -> None:
        self.fail_on = fail_on
        self.create_user_calls: list[tuple[str, dict[str, object]]] = []
        self.ticket_calls: list[str] = []

    async def create_user(self, email: str, app_metadata: dict[str, object]) -> dict[str, object]:
        self.create_user_calls.append((email, app_metadata))
        if self.fail_on == "create_user":
            raise Auth0ManagementError("boom", operation="users", status=500)
        return {"user_id": "auth0|abc123", "email": email}

    async def create_invitation_ticket(self, auth0_user_id: str) -> str:
        self.ticket_calls.append(auth0_user_id)
        if self.fail_on == "ticket":
            raise Auth0ManagementError("boom", operation="tickets/password-change", status=500)
        return "https://tenant.auth0.com/lo/reset?ticket=xyz"


async def _run(client: object) -> None:
    await provision_auth0_user(
        client,  # type: ignore[arg-type]
        user_id=_USER_ID,
        tenant_id=_TENANT_ID,
        email="alice@t.test",
        user_type="TENANT",
        request_id=_REQUEST_ID,
    )


async def test_success_creates_user_then_ticket() -> None:
    client = _FakeClient()
    await _run(client)
    # create_user called once with the right email + app_metadata (strings).
    assert len(client.create_user_calls) == 1
    email, app_metadata = client.create_user_calls[0]
    assert email == "alice@t.test"
    assert app_metadata == {
        "tenant_id": str(_TENANT_ID),
        "user_type": "TENANT",
        "user_id": str(_USER_ID),
    }
    # invitation ticket issued for the returned Auth0 user_id (sub).
    assert client.ticket_calls == ["auth0|abc123"]


async def test_create_user_failure_is_swallowed_no_ticket() -> None:
    """Auth0 create_user failure is fail-safe: no raise, and the ticket step is not
    reached (the user is left INVITED / re-provisionable)."""
    client = _FakeClient(fail_on="create_user")
    await _run(client)  # must NOT raise
    assert len(client.create_user_calls) == 1
    assert client.ticket_calls == []


async def test_ticket_failure_is_swallowed() -> None:
    """Invitation-ticket failure is also fail-safe: no raise."""
    client = _FakeClient(fail_on="ticket")
    await _run(client)  # must NOT raise
    assert len(client.create_user_calls) == 1
    assert client.ticket_calls == ["auth0|abc123"]


async def test_unconfigured_client_noops() -> None:
    """A None management client (unconfigured STUB/dev) no-ops: no calls, no raise."""
    await _run(None)  # must NOT raise; nothing to assert beyond not crashing
