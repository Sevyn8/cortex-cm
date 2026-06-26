"""The auth-client contract.

StubAuthClient (build phase) and Auth0Client (production) are interchangeable:
both verify a JWT string and return a validated AuthContext, or raise a typed
auth error. The middleware and the main.py factory annotate against this Protocol
so the two clients are drop-in swaps (selected by AUTH_CLIENT_MODE). The clients
satisfy it structurally; no inheritance is required.
"""
from typing import Protocol

from admin_backend.auth.context import AuthContext


class AuthClient(Protocol):
    """A JWT verifier: verify(jwt_string) -> AuthContext, or raise a typed error."""

    def verify(self, jwt_string: str | None) -> AuthContext: ...
