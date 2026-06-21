"""Test helpers for stub-auth: JWT minting and tampering.

make_test_jwt(...): mints a JWT signed with the local RS256 private
key. Required: user_id, user_type. tenant_id required if
user_type=TENANT. Defaults: email, sub, exp (1 hour from now), iss/aud
from settings. For negative tests, omit_claims removes specific claims
from the payload, and extra_claims overrides claim values with bad
ones.

tamper_token_claim(...): modifies a single claim in an already-signed
JWT WITHOUT re-signing. The result is a token whose payload no longer
matches its signature. Used in B4-style integrity tests to confirm
that signature verification rejects payload tampering.

These are intentionally separate from src/admin_backend/auth/stub.py
because they're test-only helpers, not production code paths.
"""
import base64
import json
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import jwt  # PyJWT

from admin_backend.auth.stub import (
    CLAIM_EMAIL,
    CLAIM_TENANT_ID,
    CLAIM_USER_ID,
    CLAIM_USER_TYPE,
)
from admin_backend.config import Settings


def make_test_jwt(
    settings: Settings,
    *,
    user_id: UUID,
    user_type: str,
    tenant_id: UUID | None = None,
    email: str = "test@ithina.local",
    sub: str = "test-sub",
    exp_offset_seconds: int = 3600,
    iss: str | None = None,
    aud: str | None = None,
    omit_claims: tuple[str, ...] = (),
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """Mint a JWT signed with the local RS256 private key.

    Required:
        user_id: UUID of the row in platform_users / tenant_users.
        user_type: "PLATFORM" or "TENANT".

    Optional:
        tenant_id: required if user_type=TENANT, ignored otherwise
            unless caller supplies it for the impersonation pattern.
        email, sub: defaulted; overridable for negative tests.
        exp_offset_seconds: seconds from now for the exp claim. Pass a
            negative value to mint an already-expired token.
        iss, aud: override the settings values for negative tests.
        omit_claims: tuple of claim keys to leave OUT of the payload.
            Used to test missing-claim rejection.
        extra_claims: dict of claim keys to bad values. Applied AFTER
            the defaults, so overrides them. Used to test malformed
            claim rejection.

    Raises:
        ValueError: user_type is not PLATFORM/TENANT, or user_type=
            TENANT was supplied without tenant_id.
    """
    if user_type not in ("PLATFORM", "TENANT"):
        raise ValueError(
            f"user_type must be PLATFORM or TENANT; got: {user_type!r}"
        )
    if user_type == "TENANT" and tenant_id is None:
        raise ValueError(
            "TENANT user_type requires tenant_id"
        )

    private_key = settings.jwt_private_key_path.read_text()
    now = datetime.now(timezone.utc)

    payload: dict[str, Any] = {
        "sub": sub,
        "iss": iss if iss is not None else settings.jwt_issuer,
        "aud": aud if aud is not None else settings.jwt_audience,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=exp_offset_seconds)).timestamp()),
        CLAIM_USER_ID: str(user_id),
        CLAIM_USER_TYPE: user_type,
        CLAIM_EMAIL: email,
    }
    if tenant_id is not None:
        payload[CLAIM_TENANT_ID] = str(tenant_id)

    for k in omit_claims:
        payload.pop(k, None)

    if extra_claims:
        payload.update(extra_claims)

    return jwt.encode(payload, private_key, algorithm="RS256")


def tamper_token_claim(token: str, claim_name: str, new_value: Any) -> str:
    """Modify a single claim in a signed JWT WITHOUT re-signing it.

    Decodes the payload segment, mutates the named claim, re-encodes,
    and reattaches the original signature. The result is a structurally
    valid JWT whose payload no longer matches its signature. Used in
    integrity tests (B4-style) to confirm signature verification
    rejects payload tampering even when the rest of the token is
    well-formed.
    """
    header_b64, payload_b64, signature = token.split(".")
    # JWT base64url encoding strips padding; restore for decode.
    padded = payload_b64 + "=" * (-len(payload_b64) % 4)
    payload = json.loads(base64.urlsafe_b64decode(padded))
    payload[claim_name] = new_value
    new_payload_bytes = json.dumps(payload, separators=(",", ":")).encode()
    new_payload_b64 = (
        base64.urlsafe_b64encode(new_payload_bytes).rstrip(b"=").decode()
    )
    return f"{header_b64}.{new_payload_b64}.{signature}"
