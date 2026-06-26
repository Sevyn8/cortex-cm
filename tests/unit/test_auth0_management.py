"""Step CI-4a unit tests for Auth0ManagementClient.

DB-free, NO network, NO secret. The Auth0 Management API is faked with httpx's
built-in MockTransport (a handler that dispatches by URL path and counts hits), and
the token-cache clock is an injected fake list so expiry is deterministic. Mirrors
test_auth0_client.py's constructor-injection seam.

Covers: token acquisition + CACHING (fetch-once, cache-hit-no-refetch,
post-expiry-refetch), create_user body + parse, create_invitation_ticket,
get_user, find_user_by_email, and the fail-safe error mapping (4xx, 5xx, network
error, malformed token response) to the typed Auth0ManagementError.
"""
from typing import Any

import httpx
import pytest

from admin_backend.auth.auth0_management import Auth0ManagementClient
from admin_backend.config import Settings
from admin_backend.errors import Auth0ManagementError

_ISSUER = "https://test-tenant.us.auth0.com/"
_TOKEN_URL_PATH = "/oauth/token"


def _settings() -> Settings:
    """Explicit test Settings (env-independent) with placeholder M2M creds."""
    return Settings(  # type: ignore[call-arg]
        database_url="postgresql+psycopg://t:t@localhost:5432/t",
        db_schema="core",
        auth_client_mode="AUTH0",
        environment="local",
        jwt_issuer=_ISSUER,
        jwt_audience="https://api.cortex.test/",
        auth0_m2m_client_id="test-client-id",
        auth0_m2m_client_secret="test-secret",  # pydantic coerces to SecretStr
    )


class _Handler:
    """A MockTransport handler: dispatches by path, counts token-endpoint hits."""

    def __init__(self) -> None:
        self.token_hits = 0
        self.last_token_body: dict[str, Any] | None = None
        self.last_users_body: dict[str, Any] | None = None
        self.user_status = 201
        self.network_error = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.network_error:
            raise httpx.ConnectError("simulated network failure", request=request)
        path = request.url.path
        if path == _TOKEN_URL_PATH:
            self.token_hits += 1
            import json as _json

            self.last_token_body = _json.loads(request.content)
            return httpx.Response(
                200, json={"access_token": f"tok-{self.token_hits}", "expires_in": 3600}
            )
        if path.endswith("/api/v2/users") and request.method == "POST":
            import json as _json

            self.last_users_body = _json.loads(request.content)
            return httpx.Response(
                self.user_status, json={"user_id": "auth0|abc123", "email": "u@t.test"}
            )
        if path.endswith("/tickets/password-change"):
            return httpx.Response(201, json={"ticket": "https://tenant.auth0.com/lo/reset?ticket=xyz"})
        if path.endswith("/users-by-email"):
            email = request.url.params.get("email")
            if email == "missing@t.test":
                return httpx.Response(200, json=[])
            return httpx.Response(200, json=[{"user_id": "auth0|abc123", "email": email}])
        if "/api/v2/users/" in path and request.method == "GET":
            return httpx.Response(200, json={"user_id": "auth0|abc123", "email": "u@t.test"})
        return httpx.Response(404, json={"error": "unexpected path"})


class _Clock:
    """A controllable monotonic clock for deterministic cache-expiry tests."""

    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def _client(handler: _Handler, clock: _Clock | None = None) -> Auth0ManagementClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    return Auth0ManagementClient(
        _settings(), http_client=http_client, clock=clock or _Clock()
    )


# ---------------------------------------------------------------------------
# Token acquisition + caching
# ---------------------------------------------------------------------------


async def test_token_caching_fetch_once_then_cache_hit() -> None:
    """First authed call fetches a token; a second call within expiry does NOT
    re-fetch (the token endpoint is hit exactly once)."""
    handler = _Handler()
    client = _client(handler)
    await client.get_user("auth0|abc123")
    assert handler.token_hits == 1
    await client.get_user("auth0|abc123")
    assert handler.token_hits == 1  # cache hit: no second token fetch


async def test_token_refetched_after_expiry() -> None:
    """Advancing the clock past the cached token's expiry forces a re-fetch."""
    handler = _Handler()
    clock = _Clock()
    client = _client(handler, clock)
    await client.get_user("auth0|abc123")
    assert handler.token_hits == 1
    # expires_in=3600, margin=60 -> deadline ~ now+3540. Advance well past it.
    clock.t += 4000
    await client.get_user("auth0|abc123")
    assert handler.token_hits == 2


async def test_token_request_body_has_client_credentials_grant() -> None:
    handler = _Handler()
    client = _client(handler)
    await client.get_user("auth0|abc123")
    assert handler.last_token_body is not None
    assert handler.last_token_body["grant_type"] == "client_credentials"
    assert handler.last_token_body["client_id"] == "test-client-id"
    assert handler.last_token_body["client_secret"] == "test-secret"
    assert handler.last_token_body["audience"] == "https://test-tenant.us.auth0.com/api/v2/"


async def test_malformed_expires_in_does_not_cache_forever() -> None:
    """A token response with a non-numeric expires_in is fail-safe: the token is
    used for this call but re-fetched next call (never cached forever)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == _TOKEN_URL_PATH:
            handler.hits += 1  # type: ignore[attr-defined]
            return httpx.Response(200, json={"access_token": "tok", "expires_in": "not-a-number"})
        return httpx.Response(200, json={"user_id": "auth0|abc123"})

    handler.hits = 0  # type: ignore[attr-defined]
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    clock = _Clock()
    client = Auth0ManagementClient(_settings(), http_client=http_client, clock=clock)
    await client.get_user("auth0|abc123")
    assert handler.hits == 1  # type: ignore[attr-defined]
    # Clock does NOT advance; a cache-forever bug would skip the re-fetch.
    await client.get_user("auth0|abc123")
    assert handler.hits == 2  # type: ignore[attr-defined]


async def test_missing_access_token_raises_typed_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"expires_in": 3600})  # no access_token

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = Auth0ManagementClient(_settings(), http_client=http_client)
    with pytest.raises(Auth0ManagementError):
        await client.get_user("auth0|abc123")


async def test_missing_config_raises_typed_error() -> None:
    """No M2M creds configured -> typed error (fail safe), not an AttributeError."""
    settings = Settings(  # type: ignore[call-arg]
        database_url="postgresql+psycopg://t:t@localhost:5432/t",
        db_schema="core",
        jwt_issuer=_ISSUER,
        jwt_audience="https://api.cortex.test/",
    )
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(_Handler()))
    client = Auth0ManagementClient(settings, http_client=http_client)
    with pytest.raises(Auth0ManagementError):
        await client.get_user("auth0|abc123")


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------


async def test_create_user_posts_body_and_parses_sub() -> None:
    handler = _Handler()
    client = _client(handler)
    user = await client.create_user(
        "alice@t.test", app_metadata={"tenant_id": "t1", "user_type": "TENANT", "user_id": "u1"}
    )
    assert user["user_id"] == "auth0|abc123"
    assert handler.last_users_body is not None
    assert handler.last_users_body["email"] == "alice@t.test"
    assert handler.last_users_body["connection"] == "Username-Password-Authentication"
    assert handler.last_users_body["app_metadata"] == {
        "tenant_id": "t1",
        "user_type": "TENANT",
        "user_id": "u1",
    }


async def test_create_invitation_ticket_returns_url() -> None:
    handler = _Handler()
    client = _client(handler)
    ticket = await client.create_invitation_ticket("auth0|abc123")
    assert ticket == "https://tenant.auth0.com/lo/reset?ticket=xyz"


async def test_get_user_parses_response() -> None:
    handler = _Handler()
    client = _client(handler)
    user = await client.get_user("auth0|abc123")
    assert user["user_id"] == "auth0|abc123"


async def test_find_user_by_email_match_and_no_match() -> None:
    handler = _Handler()
    client = _client(handler)
    found = await client.find_user_by_email("alice@t.test")
    assert found is not None and found["user_id"] == "auth0|abc123"
    missing = await client.find_user_by_email("missing@t.test")
    assert missing is None


# ---------------------------------------------------------------------------
# Fail-safe error mapping
# ---------------------------------------------------------------------------


async def test_4xx_maps_to_typed_error() -> None:
    handler = _Handler()
    handler.user_status = 400
    client = _client(handler)
    with pytest.raises(Auth0ManagementError):
        await client.create_user("alice@t.test", app_metadata={})


async def test_5xx_maps_to_typed_error() -> None:
    handler = _Handler()
    handler.user_status = 500
    client = _client(handler)
    with pytest.raises(Auth0ManagementError):
        await client.create_user("alice@t.test", app_metadata={})


async def test_network_error_maps_to_typed_error_not_bare_httpx() -> None:
    """A transport/network error surfaces as Auth0ManagementError, never a bare
    httpx error (fail safe: the provisioning caller catches one type)."""
    handler = _Handler()
    handler.network_error = True
    client = _client(handler)
    with pytest.raises(Auth0ManagementError):
        await client.get_user("auth0|abc123")


def test_default_client_has_timeout() -> None:
    """The self-constructed client carries an explicit (non-None) timeout."""
    client = Auth0ManagementClient(_settings())
    assert client._client.timeout.read is not None  # type: ignore[union-attr]
