"""Shared identity-claim mapping (D-24).

The custom-claim namespace and the claim-to-AuthContext logic live here in ONE
place, used by BOTH verifiers (StubAuthClient and Auth0Client). The two clients
differ only in key acquisition (stub reads a local PEM; Auth0 fetches the signing
key from JWKS by kid); once they have a verified payload, the identity-claim
extraction and AuthContext construction are identical, so they share this helper.

Per D-24 the token is identity-only: no roles, no permissions. Permissions resolve
in-app per request from the DB.
"""
from typing import Any
from uuid import UUID

from admin_backend.auth.context import AuthContext
from admin_backend.errors import AuthInvalidError, InvalidTenantIdError

# Custom claim namespaces per D-24. Module-level constants so tests (and future
# Auth0 management code) can reference the exact strings. The namespace is our own
# domain (sevyn8.com), not the client domain (corrected in Step CI-2).
NAMESPACE = "https://sevyn8.com"
CLAIM_TENANT_ID = f"{NAMESPACE}/tenant_id"
CLAIM_USER_TYPE = f"{NAMESPACE}/user_type"
CLAIM_USER_ID = f"{NAMESPACE}/user_id"
CLAIM_EMAIL = f"{NAMESPACE}/email"


def claims_to_auth_context(payload: dict[str, Any]) -> AuthContext:
    """Map a VERIFIED JWT payload to a validated AuthContext (D-24, identity-only).

    The caller is responsible for having already verified the token's signature,
    issuer, audience, and expiry; this function only extracts and validates the
    identity claims and constructs the (frozen) AuthContext.

    Raises:
        AuthInvalidError: a required claim is missing or malformed, or the claims
            fail AuthContext validation.
        InvalidTenantIdError: the tenant_id claim is present but is not a valid UUID.
    """
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
                f"tenant_id claim is not a valid UUID: {tenant_id_raw!r}"
            ) from e

    user_id_raw = payload.get(CLAIM_USER_ID)
    if not user_id_raw:
        raise AuthInvalidError(f"Missing required claim: {CLAIM_USER_ID}")
    try:
        user_id = UUID(user_id_raw)
    except (ValueError, TypeError, AttributeError) as e:
        raise AuthInvalidError(
            f"user_id claim is not a valid UUID: {user_id_raw!r}"
        ) from e

    user_type = payload.get(CLAIM_USER_TYPE)
    if user_type not in ("PLATFORM", "TENANT"):
        raise AuthInvalidError(
            f"user_type claim must be 'PLATFORM' or 'TENANT'; got: {user_type!r}"
        )

    email = payload.get(CLAIM_EMAIL)
    if not email:
        raise AuthInvalidError(f"Missing required claim: {CLAIM_EMAIL}")

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
