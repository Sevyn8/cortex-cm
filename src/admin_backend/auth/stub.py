"""StubAuthClient: build-phase JWT verifier.

Verifies tokens signed with the local RS256 private key (paired with
keys/jwt_public.pem). Rejects expired, malformed, wrong-audience,
wrong-issuer, wrong-signature, and malformed-claim tokens.

Production swap is config-only via AUTH_CLIENT_MODE=AUTH0 (handled at
Step 2.3 middleware): the same verify(jwt_string) -> AuthContext
contract works for both clients. No handler-code change required.

Custom claim namespaces per D-24:
    https://sevyn8.com/tenant_id
    https://sevyn8.com/user_type
    https://sevyn8.com/user_id
    https://sevyn8.com/email
"""
from uuid import UUID

import jwt  # PyJWT

from admin_backend.auth.context import AuthContext
from admin_backend.config import Settings
from admin_backend.errors import (
    AuthInvalidError,
    AuthMissingError,
    InvalidTenantIdError,
)


# Custom claim namespaces per D-24. Module-level constants so tests
# (and future Auth0 management code) can reference the exact strings.
NAMESPACE = "https://sevyn8.com"
CLAIM_TENANT_ID = f"{NAMESPACE}/tenant_id"
CLAIM_USER_TYPE = f"{NAMESPACE}/user_type"
CLAIM_USER_ID = f"{NAMESPACE}/user_id"
CLAIM_EMAIL = f"{NAMESPACE}/email"


class StubAuthClient:
    """JWT verification client for the build-phase stub auth.

    Verifies RS256 tokens signed with the local private key, against
    the local public key. Validates issuer, audience, expiry, and the
    custom claim shape per D-24. Returns an AuthContext if the token
    is valid; raises a typed error otherwise.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        try:
            self._public_key: str = settings.jwt_public_key_path.read_text()
        except (FileNotFoundError, PermissionError) as e:
            raise RuntimeError(
                f"Cannot load JWT public key from "
                f"{settings.jwt_public_key_path}: {e}. Was the keypair "
                "generated? See keys/ in the project setup procedure."
            ) from e

    def verify(self, jwt_string: str | None) -> AuthContext:
        """Verify a JWT and return a validated AuthContext.

        Raises:
            AuthMissingError: jwt_string is empty or None.
            AuthInvalidError: signature, expiry, audience, issuer,
                claim shape, or AuthContext validation failed.
            InvalidTenantIdError: tenant_id claim is present but is
                not a valid UUID.
        """
        if not jwt_string:
            raise AuthMissingError("JWT string is empty or None")

        try:
            payload = jwt.decode(
                jwt_string,
                self._public_key,
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

        # Extract and validate custom claims.
        tenant_id_raw = payload.get(CLAIM_TENANT_ID)
        tenant_id: UUID | None
        if tenant_id_raw is None:
            tenant_id = None
        else:
            try:
                tenant_id = UUID(tenant_id_raw)
            except (ValueError, TypeError, AttributeError) as e:
                raise InvalidTenantIdError(
                    f"tenant_id claim is not a valid UUID: "
                    f"{tenant_id_raw!r}"
                ) from e

        user_id_raw = payload.get(CLAIM_USER_ID)
        if not user_id_raw:
            raise AuthInvalidError(
                f"Missing required claim: {CLAIM_USER_ID}"
            )
        try:
            user_id = UUID(user_id_raw)
        except (ValueError, TypeError, AttributeError) as e:
            raise AuthInvalidError(
                f"user_id claim is not a valid UUID: {user_id_raw!r}"
            ) from e

        user_type = payload.get(CLAIM_USER_TYPE)
        if user_type not in ("PLATFORM", "TENANT"):
            raise AuthInvalidError(
                f"user_type claim must be 'PLATFORM' or 'TENANT'; "
                f"got: {user_type!r}"
            )

        email = payload.get(CLAIM_EMAIL)
        if not email:
            raise AuthInvalidError(
                f"Missing required claim: {CLAIM_EMAIL}"
            )

        # Construct AuthContext (its validators run here).
        try:
            return AuthContext(
                sub=payload["sub"],
                iss=payload["iss"],
                aud=payload["aud"],
                exp=payload["exp"],
                user_id=user_id,
                tenant_id=tenant_id,
                user_type=user_type,
                email=email,
            )
        except Exception as e:
            raise AuthInvalidError(
                f"Token claims failed AuthContext validation: {e}"
            ) from e
