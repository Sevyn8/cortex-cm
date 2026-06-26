"""Auth0Client: production JWT verifier.

Verifies real Auth0-issued RS256 tokens against the Auth0 JWKS endpoint. It is the
production counterpart to StubAuthClient and satisfies the same AuthClient contract:
same verify(jwt_string) -> AuthContext signature, same typed errors, the SAME
identity-claim extraction (via the shared claims_to_auth_context helper, D-24,
identity-only). The ONLY difference from the stub is key acquisition: the stub reads
a local PEM; this fetches the signing key from JWKS by the token's kid.

FAIL CLOSED (security invariant): every key-acquisition or verification failure
(kid not found, JWKS endpoint unreachable, any PyJWKClientError, any jwt exception,
any doubt) raises a typed error and REJECTS. No code path returns an AuthContext on
failure. The verifier never fails open.

Production swap is config-only via AUTH_CLIENT_MODE=AUTH0 (main.py selects the
client); no handler-code change is required.
"""
import jwt  # PyJWT
from jwt import PyJWKClient

from admin_backend.auth.claims import claims_to_auth_context
from admin_backend.auth.context import AuthContext
from admin_backend.config import Settings
from admin_backend.errors import AuthInvalidError, AuthMissingError


def _derive_jwks_url(issuer: str) -> str:
    """The Auth0-convention JWKS URL for an issuer: issuer + .well-known/jwks.json.

    The issuer carries a trailing slash (Auth0 convention, enforced for production
    in Settings), so strip it before joining to avoid a double slash.
    """
    return f"{issuer.rstrip('/')}/.well-known/jwks.json"


class Auth0Client:
    """Production JWT verification client (Auth0 RS256 + JWKS).

    Verifies the signature against the Auth0 JWKS signing key (selected by the
    token's kid), plus issuer, audience, and expiry, then extracts the identity
    claims per D-24. Returns an AuthContext if valid; raises a typed error otherwise.
    """

    def __init__(self, settings: Settings, jwk_client: PyJWKClient | None = None) -> None:
        self._settings = settings
        # Explicit override (AUTH0_JWKS_URL) if set; else the Auth0-convention default
        # derived from the issuer. jwk_client is injectable so tests can supply a fake
        # (no network); production constructs a real PyJWKClient, which caches signing
        # keys by kid and fetches on cache-miss.
        jwks_url = settings.auth0_jwks_url or _derive_jwks_url(settings.jwt_issuer)
        self._jwk_client: PyJWKClient = (
            jwk_client if jwk_client is not None else PyJWKClient(jwks_url)
        )

    def verify(self, jwt_string: str | None) -> AuthContext:
        """Verify a JWT and return a validated AuthContext.

        Raises:
            AuthMissingError: jwt_string is empty or None.
            AuthInvalidError: signing-key acquisition failed (kid not found, JWKS
                unreachable, any PyJWKClientError), or signature/expiry/audience/
                issuer/claim-shape/AuthContext validation failed.
            InvalidTenantIdError: tenant_id claim is present but not a valid UUID.
        """
        if not jwt_string:
            raise AuthMissingError("JWT string is empty or None")

        # 1. Acquire the signing key from JWKS by the token's kid. FAIL CLOSED: any
        # PyJWKClientError (kid not found, JWKS endpoint unreachable, malformed JWKS)
        # rejects; we never proceed without a verified signing key.
        try:
            signing_key = self._jwk_client.get_signing_key_from_jwt(jwt_string)
        except jwt.exceptions.PyJWKClientError as e:
            raise AuthInvalidError(
                f"Unable to obtain a JWKS signing key (rejecting): {e}"
            ) from e

        # 2. Verify signature, issuer, audience, expiry. Same exception-to-typed-error
        # mapping as StubAuthClient. Any jwt exception rejects (fail closed).
        try:
            payload = jwt.decode(
                jwt_string,
                signing_key.key,
                algorithms=["RS256"],
                audience=self._settings.jwt_audience,
                issuer=self._settings.jwt_issuer,
            )
        except jwt.ExpiredSignatureError as e:
            raise AuthInvalidError(f"Token expired: {e}") from e
        except jwt.InvalidAudienceError as e:
            raise AuthInvalidError(f"Token audience mismatch: {e}") from e
        except jwt.InvalidIssuerError as e:
            raise AuthInvalidError(f"Token issuer mismatch: {e}") from e
        except jwt.InvalidSignatureError as e:
            raise AuthInvalidError(f"Token signature invalid: {e}") from e
        except jwt.DecodeError as e:
            raise AuthInvalidError(f"Token malformed: {e}") from e
        except jwt.InvalidTokenError as e:
            # Backstop: any other PyJWT validation error rejects (fail closed),
            # rather than falling through to a pass.
            raise AuthInvalidError(f"Token invalid: {e}") from e

        # 3. Extract identity claims + construct AuthContext (shared with the stub).
        return claims_to_auth_context(payload)
