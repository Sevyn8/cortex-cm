"""Step 2.1 unit tests for stub auth.

21 tests in 6 groups:
    A1-A3:   happy paths (TENANT, PLATFORM, PLATFORM with impersonation tenant_id)
    B4-B6:   signature/integrity attacks (tampering, garbage signature, wrong key)
    C7-C12:  claim validation (expired, wrong aud/iss, missing/malformed claims)
    D13-D16: AuthContext validation rules (cross-field, frozen)
    E17-E19: Settings configuration validation (production-vs-stub gates)
    F20-F21: missing JWT (empty, None)
"""
from typing import Any
from uuid import UUID, uuid4

import jwt
import pytest
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import ValidationError

from admin_backend.auth.context import AuthContext
from admin_backend.auth.stub import (
    CLAIM_EMAIL,
    CLAIM_TENANT_ID,
    CLAIM_USER_ID,
    CLAIM_USER_TYPE,
    StubAuthClient,
)
from admin_backend.auth.testing import make_test_jwt, tamper_token_claim
from admin_backend.config import Settings
from admin_backend.errors import (
    AuthInvalidError,
    AuthMissingError,
    InvalidTenantIdError,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def settings() -> Settings:
    """Project Settings loaded from .env. Module-scoped to avoid re-loading."""
    return Settings()  # type: ignore[call-arg]


@pytest.fixture(scope="module")
def auth_client(settings: Settings) -> StubAuthClient:
    return StubAuthClient(settings)


# ---------------------------------------------------------------------------
# Group A: happy paths
# ---------------------------------------------------------------------------


def test_a1_tenant_jwt_roundtrip(settings: Settings, auth_client: StubAuthClient) -> None:
    """TENANT JWT roundtrips through verify and produces correct AuthContext."""
    tenant_a = uuid4()
    user_a = uuid4()
    token = make_test_jwt(
        settings,
        user_id=user_a,
        user_type="TENANT",
        tenant_id=tenant_a,
        email="alice@tenant-a.test",
    )
    ctx = auth_client.verify(token)

    assert ctx.user_type == "TENANT"
    assert ctx.tenant_id == tenant_a
    assert ctx.user_id == user_a
    assert ctx.email == "alice@tenant-a.test"
    assert ctx.iss == settings.jwt_issuer
    # aud may be str (single audience); confirm it round-trips.
    assert ctx.aud == settings.jwt_audience


def test_a2_platform_jwt_roundtrip(settings: Settings, auth_client: StubAuthClient) -> None:
    """PLATFORM JWT with no tenant_id roundtrips; tenant_id=None in AuthContext."""
    user = uuid4()
    token = make_test_jwt(
        settings,
        user_id=user,
        user_type="PLATFORM",
        email="staff@ithina.local",
    )
    ctx = auth_client.verify(token)

    assert ctx.user_type == "PLATFORM"
    assert ctx.tenant_id is None
    assert ctx.user_id == user


def test_a3_platform_with_impersonation_tenant_id_roundtrip(
    settings: Settings, auth_client: StubAuthClient
) -> None:
    """PLATFORM with non-NULL tenant_id (impersonation, deferred capability per FN-AB-14)
    is permitted by D-24 and verifies cleanly."""
    user = uuid4()
    impersonated_tenant = uuid4()
    token = make_test_jwt(
        settings,
        user_id=user,
        user_type="PLATFORM",
        tenant_id=impersonated_tenant,  # PLATFORM permits this
        email="staff@ithina.local",
    )
    ctx = auth_client.verify(token)

    assert ctx.user_type == "PLATFORM"
    assert ctx.tenant_id == impersonated_tenant


# ---------------------------------------------------------------------------
# Group B: signature / integrity attacks
# ---------------------------------------------------------------------------


def test_b4_tampered_user_type_rejected(settings: Settings, auth_client: StubAuthClient) -> None:
    """Tampering user_type in the payload (without re-signing) is rejected."""
    token = make_test_jwt(
        settings,
        user_id=uuid4(),
        user_type="TENANT",
        tenant_id=uuid4(),
    )
    tampered = tamper_token_claim(token, CLAIM_USER_TYPE, "PLATFORM")
    with pytest.raises(AuthInvalidError):
        auth_client.verify(tampered)


def test_b5_garbage_signature_rejected(settings: Settings, auth_client: StubAuthClient) -> None:
    """Replacing the signature segment with garbage is rejected."""
    token = make_test_jwt(
        settings,
        user_id=uuid4(),
        user_type="TENANT",
        tenant_id=uuid4(),
    )
    header_b64, payload_b64, _ = token.split(".")
    garbage_token = f"{header_b64}.{payload_b64}.AAAA"
    with pytest.raises(AuthInvalidError):
        auth_client.verify(garbage_token)


def test_b6_wrong_signing_key_rejected(settings: Settings, auth_client: StubAuthClient) -> None:
    """A JWT signed with a DIFFERENT private key (not the project's) is rejected.

    Generates a fresh RSA keypair inside the test (B6 is the only test that
    needs a non-project key; all other tests use keys/jwt_*.pem directly).
    """
    foreign_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend(),
    )
    foreign_pem = foreign_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()

    payload = {
        "sub": "test-sub",
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": 0,
        "exp": 9999999999,
        CLAIM_USER_ID: str(uuid4()),
        CLAIM_USER_TYPE: "TENANT",
        CLAIM_TENANT_ID: str(uuid4()),
        CLAIM_EMAIL: "alice@tenant-a.test",
    }
    foreign_signed = jwt.encode(payload, foreign_pem, algorithm="RS256")

    with pytest.raises(AuthInvalidError):
        auth_client.verify(foreign_signed)


# ---------------------------------------------------------------------------
# Group C: claim validation
# ---------------------------------------------------------------------------


def test_c7_expired_token_rejected(settings: Settings, auth_client: StubAuthClient) -> None:
    """A token whose exp is in the past is rejected."""
    token = make_test_jwt(
        settings,
        user_id=uuid4(),
        user_type="TENANT",
        tenant_id=uuid4(),
        exp_offset_seconds=-3600,  # already expired
    )
    with pytest.raises(AuthInvalidError):
        auth_client.verify(token)


def test_c8_wrong_audience_rejected(settings: Settings, auth_client: StubAuthClient) -> None:
    """A token with the wrong aud is rejected."""
    token = make_test_jwt(
        settings,
        user_id=uuid4(),
        user_type="TENANT",
        tenant_id=uuid4(),
        aud="https://wrong-audience.example/",
    )
    with pytest.raises(AuthInvalidError):
        auth_client.verify(token)


def test_c9_wrong_issuer_rejected(settings: Settings, auth_client: StubAuthClient) -> None:
    """A token with the wrong iss is rejected."""
    token = make_test_jwt(
        settings,
        user_id=uuid4(),
        user_type="TENANT",
        tenant_id=uuid4(),
        iss="https://wrong-issuer.example/",
    )
    with pytest.raises(AuthInvalidError):
        auth_client.verify(token)


def test_c10_missing_user_id_claim_rejected(
    settings: Settings, auth_client: StubAuthClient
) -> None:
    """A token without the user_id claim is rejected with a clear error."""
    token = make_test_jwt(
        settings,
        user_id=uuid4(),
        user_type="TENANT",
        tenant_id=uuid4(),
        omit_claims=(CLAIM_USER_ID,),
    )
    with pytest.raises(AuthInvalidError):
        auth_client.verify(token)


def test_c11_invalid_user_type_rejected(
    settings: Settings, auth_client: StubAuthClient
) -> None:
    """A token whose user_type is neither PLATFORM nor TENANT is rejected."""
    token = make_test_jwt(
        settings,
        user_id=uuid4(),
        user_type="TENANT",
        tenant_id=uuid4(),
        extra_claims={CLAIM_USER_TYPE: "ADMIN"},
    )
    with pytest.raises(AuthInvalidError):
        auth_client.verify(token)


def test_c12_malformed_tenant_id_rejected(
    settings: Settings, auth_client: StubAuthClient
) -> None:
    """A token whose tenant_id claim is not a valid UUID is rejected."""
    token = make_test_jwt(
        settings,
        user_id=uuid4(),
        user_type="TENANT",
        tenant_id=uuid4(),
        extra_claims={CLAIM_TENANT_ID: "not-a-uuid"},
    )
    with pytest.raises(InvalidTenantIdError):
        auth_client.verify(token)


# ---------------------------------------------------------------------------
# Group D: AuthContext validation rules
# ---------------------------------------------------------------------------


_VALID_CTX_KWARGS: dict[str, Any] = {
    "sub": "test-sub",
    "iss": "https://stub-issuer.local/",
    "aud": "https://api.test/",
    "exp": 9999999999,
    "user_id": uuid4(),
    "email": "alice@example.test",
}


def test_d13_tenant_user_without_tenant_id_rejected() -> None:
    """Constructing AuthContext directly with TENANT and tenant_id=None raises."""
    with pytest.raises(ValidationError):
        AuthContext(
            **_VALID_CTX_KWARGS,
            user_type="TENANT",
            tenant_id=None,
        )


def test_d14_platform_user_without_tenant_id_succeeds() -> None:
    """PLATFORM user_type with tenant_id=None is valid (the standard PLATFORM case)."""
    ctx = AuthContext(
        **_VALID_CTX_KWARGS,
        user_type="PLATFORM",
        tenant_id=None,
    )
    assert ctx.user_type == "PLATFORM"
    assert ctx.tenant_id is None


def test_d15_platform_with_impersonation_tenant_id_succeeds() -> None:
    """PLATFORM with non-NULL tenant_id is valid (impersonation pattern, FN-AB-14)."""
    impersonated = uuid4()
    ctx = AuthContext(
        **_VALID_CTX_KWARGS,
        user_type="PLATFORM",
        tenant_id=impersonated,
    )
    assert ctx.user_type == "PLATFORM"
    assert ctx.tenant_id == impersonated


def test_d16_authcontext_is_frozen() -> None:
    """AuthContext is frozen: assigning a field raises ValidationError."""
    ctx = AuthContext(
        **_VALID_CTX_KWARGS,
        user_type="PLATFORM",
        tenant_id=None,
    )
    with pytest.raises(ValidationError):
        ctx.user_type = "TENANT"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Group E: configuration validation
# ---------------------------------------------------------------------------


_BASE_SETTINGS_KWARGS: dict[str, Any] = {
    "database_url": "postgresql+psycopg://test:test@localhost:5432/test",
    "db_schema": "core",
    "jwt_audience": "https://api.test/",
}


def test_e17_production_with_stub_mode_rejected() -> None:
    """environment=production + auth_client_mode=STUB raises ValidationError."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(  # type: ignore[call-arg]
            **_BASE_SETTINGS_KWARGS,
            environment="production",
            auth_client_mode="STUB",
            jwt_issuer="https://example.auth0.com/",
        )
    assert "AUTH_CLIENT_MODE" in str(exc_info.value)


def test_e18_production_with_stub_marker_in_issuer_rejected() -> None:
    """environment=production + AUTH0 + stub-marker in issuer raises ValidationError."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(  # type: ignore[call-arg]
            **_BASE_SETTINGS_KWARGS,
            environment="production",
            auth_client_mode="AUTH0",
            jwt_issuer="https://stub-issuer.local/",
        )
    msg = str(exc_info.value)
    # The validator surfaces the first matching marker; "stub" is in the issuer.
    assert "stub" in msg.lower()


def test_e19_local_with_stub_marker_in_issuer_succeeds() -> None:
    """environment=local + STUB + stub-shaped issuer is valid (validators don't fire)."""
    s = Settings(  # type: ignore[call-arg]
        **_BASE_SETTINGS_KWARGS,
        environment="local",
        auth_client_mode="STUB",
        jwt_issuer="https://stub-issuer.local/",
    )
    assert s.environment == "local"
    assert s.auth_client_mode == "STUB"
    assert s.jwt_issuer == "https://stub-issuer.local/"


# ---------------------------------------------------------------------------
# Group F: missing JWT
# ---------------------------------------------------------------------------


def test_f20_empty_string_rejected(auth_client: StubAuthClient) -> None:
    with pytest.raises(AuthMissingError):
        auth_client.verify("")


def test_f21_none_rejected(auth_client: StubAuthClient) -> None:
    with pytest.raises(AuthMissingError):
        auth_client.verify(None)
