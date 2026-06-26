"""Auth0 Management API client (Step CI-4a, provisioning plumbing).

The FIRST outbound HTTP from CM. Lets CM create/invite Auth0 users via the Auth0
Management API (machine-to-machine, client-credentials). This is step 4a of the
CM<->Auth0 provisioning arc: the standalone client only. Wiring it into
user-creation (4b), the invite-accept callback (4c), and the Login Action (4d) are
separate later steps; nothing here touches CM's user-write repos.

Domain-agnostic: the caller passes app_metadata ({tenant_id, user_type, cm_user_id});
this client does not know CM's domain model.

SECURITY: the M2M client secret (SecretStr, read once at the token request) and the
bearer access token NEVER enter error context, logs, or repr. Auth0ManagementError
context carries only operation / HTTP status / cause-type.

FAIL-SAFE: every outbound failure (non-2xx, network/transport/timeout, a malformed
token response, or missing configuration) is mapped to the typed
Auth0ManagementError. No bare httpx error escapes _request, so the provisioning
caller (4b, post-commit) can catch one error type and leave the user INVITED /
re-provisionable.
"""
import time
from collections.abc import Callable
from typing import Any

import httpx

from admin_backend.config import Settings
from admin_backend.errors import Auth0ManagementError

# Default Auth0 database-connection name a created user belongs to (overridable via
# auth0_db_connection). "Username-Password-Authentication" is the standard default
# connection in a fresh Auth0 tenant.
_DEFAULT_DB_CONNECTION = "Username-Password-Authentication"

# Refresh the cached token this many seconds BEFORE its stated expiry, so an
# in-flight call never carries a token that expires mid-request.
_TOKEN_REFRESH_MARGIN_SECONDS = 60.0

# Explicit timeout (httpx defaults to no timeout). Outbound from the identity SoR
# must never hang indefinitely on a slow/unreachable Auth0.
_DEFAULT_TIMEOUT = httpx.Timeout(10.0)


class Auth0ManagementClient:
    """Async client for the Auth0 Management API (create/invite/read users).

    Construct with the app Settings. ``http_client`` is injectable so tests run
    with no network (an httpx.AsyncClient on httpx.MockTransport); ``clock`` is
    injectable so the token-cache expiry is deterministic in tests.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        http_client: httpx.AsyncClient | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT)
        self._clock = clock

        issuer = settings.jwt_issuer
        # Explicit config wins; else derive from the issuer (Auth0 convention). The
        # issuer carries a trailing slash, so join directly.
        self._token_endpoint = settings.auth0_token_endpoint or f"{issuer}oauth/token"
        self._mgmt_audience = settings.auth0_mgmt_audience or f"{issuer}api/v2/"
        self._db_connection = settings.auth0_db_connection or _DEFAULT_DB_CONNECTION

        # Token cache: the bearer plus a monotonic deadline. _token is never logged.
        self._token: str | None = None
        self._token_expiry: float = 0.0

    async def aclose(self) -> None:
        """Close the underlying client if we created it (not when injected)."""
        if self._owns_client:
            await self._client.aclose()

    # -- internals -----------------------------------------------------------

    def _require_config(self) -> tuple[str, str]:
        """Return (client_id, client_secret) or raise if M2M config is absent.

        Fail-safe: a missing credential is an Auth0ManagementError (config), not an
        AttributeError on None. The secret is read from SecretStr only here.
        """
        client_id = self._settings.auth0_m2m_client_id
        secret = self._settings.auth0_m2m_client_secret
        if not client_id or secret is None:
            raise Auth0ManagementError(
                "Auth0 Management API is not configured (client id/secret missing)",
                operation="config",
            )
        return client_id, secret.get_secret_value()

    async def _request(
        self,
        method: str,
        url: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """The single egress chokepoint. Maps every failure to Auth0ManagementError.

        No bare httpx error ever escapes. The error context carries only the
        operation (URL path) and HTTP status / cause-type, never the secret, the
        bearer header, or the request body.
        """
        try:
            response = await self._client.request(
                method, url, json=json, params=params, headers=headers
            )
        except httpx.HTTPError as exc:
            # Network / transport / timeout. Cause-type only (e.g. "ConnectError").
            raise Auth0ManagementError(
                "Auth0 Management API request failed (transport)",
                operation=_path_of(url),
                cause=type(exc).__name__,
            ) from exc

        if response.status_code >= 400:
            raise Auth0ManagementError(
                "Auth0 Management API returned an error status",
                operation=_path_of(url),
                status=response.status_code,
            )
        return response

    @staticmethod
    def _json(response: httpx.Response, operation: str) -> dict[str, Any]:
        """Parse a JSON object body, or fail safe to Auth0ManagementError."""
        try:
            data = response.json()
        except ValueError as exc:
            raise Auth0ManagementError(
                "Auth0 Management API returned a non-JSON body",
                operation=operation,
                cause=type(exc).__name__,
            ) from exc
        if not isinstance(data, dict):
            raise Auth0ManagementError(
                "Auth0 Management API returned an unexpected (non-object) body",
                operation=operation,
            )
        return data

    async def _get_token(self) -> str:
        """Return a cached Management API bearer, fetching only when missing/expired.

        Caching is REQUIRED (the tenant has a 1000-tokens/month quota): a valid
        cached token is reused until the refresh margin before its stated expiry.

        Defensive: if the token response lacks a usable numeric ``expires_in``, the
        cache deadline is set to "now" so the token is used for THIS call but
        re-fetched on the next one (fail safe; never cache-forever, never crash).
        """
        if self._token is not None and self._clock() < self._token_expiry:
            return self._token

        client_id, client_secret = self._require_config()
        response = await self._request(
            "POST",
            self._token_endpoint,
            json={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
                "audience": self._mgmt_audience,
            },
        )
        data = self._json(response, "oauth/token")

        token = data.get("access_token")
        if not isinstance(token, str) or not token:
            raise Auth0ManagementError(
                "Auth0 token response missing access_token", operation="oauth/token"
            )

        # Defensive expires_in: only a positive number is trusted; otherwise ttl=0
        # (use this token now, re-fetch next call) rather than caching forever.
        expires_in = data.get("expires_in")
        ttl = float(expires_in) if isinstance(expires_in, (int, float)) and expires_in > 0 else 0.0
        self._token = token
        self._token_expiry = self._clock() + max(0.0, ttl - _TOKEN_REFRESH_MARGIN_SECONDS)
        return token

    async def _authed_request(
        self,
        method: str,
        url: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> httpx.Response:
        """A Management API request carrying the cached bearer."""
        token = await self._get_token()
        return await self._request(
            method, url, json=json, params=params, headers={"Authorization": f"Bearer {token}"}
        )

    # -- operations ----------------------------------------------------------

    async def create_user(self, email: str, app_metadata: dict[str, Any]) -> dict[str, Any]:
        """Create an Auth0 user; return the parsed user (carries ``user_id`` = the sub).

        app_metadata is opaque here ({tenant_id, user_type, cm_user_id} from the caller).
        The user is created in the configured database connection.
        """
        response = await self._authed_request(
            "POST",
            f"{self._mgmt_audience}users",
            json={
                "email": email,
                "connection": self._db_connection,
                "app_metadata": app_metadata,
                "verify_email": False,
            },
        )
        return self._json(response, "users")

    async def create_invitation_ticket(self, auth0_user_id: str) -> str:
        """Create a password-change (invitation) ticket; return its URL.

        The invited user opens this URL to set a password and first log in.
        """
        response = await self._authed_request(
            "POST",
            f"{self._mgmt_audience}tickets/password-change",
            json={"user_id": auth0_user_id},
        )
        data = self._json(response, "tickets/password-change")
        ticket = data.get("ticket")
        if not isinstance(ticket, str) or not ticket:
            raise Auth0ManagementError(
                "Auth0 ticket response missing ticket URL", operation="tickets/password-change"
            )
        return ticket

    async def get_user(self, auth0_user_id: str) -> dict[str, Any]:
        """Fetch a user by Auth0 user_id (sub). For reconcile / idempotency."""
        response = await self._authed_request("GET", f"{self._mgmt_audience}users/{auth0_user_id}")
        return self._json(response, "users/{id}")

    async def find_user_by_email(self, email: str) -> dict[str, Any] | None:
        """Return the first Auth0 user with this email, or None. For idempotency."""
        response = await self._authed_request(
            "GET", f"{self._mgmt_audience}users-by-email", params={"email": email}
        )
        try:
            data = response.json()
        except ValueError as exc:
            raise Auth0ManagementError(
                "Auth0 users-by-email returned a non-JSON body",
                operation="users-by-email",
                cause=type(exc).__name__,
            ) from exc
        if not isinstance(data, list) or not data:
            return None
        first = data[0]
        return first if isinstance(first, dict) else None


def _path_of(url: str) -> str:
    """The URL path for error context (never query/credentials)."""
    return httpx.URL(url).path
