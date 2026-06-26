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
import jwt  # PyJWT

# The claim namespace, the CLAIM_* constants, and the claim-to-AuthContext helper
# now live in auth/claims.py (shared with Auth0Client, Step CI-3). They are
# re-exported here (explicit ``as`` form, so mypy --strict treats them as a public
# re-export) to keep existing ``from admin_backend.auth.stub import CLAIM_*`` sites
# working unchanged.
from admin_backend.auth.claims import (
    CLAIM_EMAIL as CLAIM_EMAIL,
    CLAIM_TENANT_ID as CLAIM_TENANT_ID,
    CLAIM_USER_ID as CLAIM_USER_ID,
    CLAIM_USER_TYPE as CLAIM_USER_TYPE,
    NAMESPACE as NAMESPACE,
    claims_to_auth_context,
)
from admin_backend.auth.context import AuthContext
from admin_backend.config import Settings
from admin_backend.errors import AuthInvalidError, AuthMissingError


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

        # Extract identity claims + construct AuthContext (shared with Auth0Client).
        return claims_to_auth_context(payload)
