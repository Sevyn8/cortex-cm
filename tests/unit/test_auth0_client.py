"""Step CI-3 unit tests for Auth0Client (production JWKS verifier).

Mirrors test_stub_auth.py's rigor. The ONLY difference from the stub is key
acquisition: these tests fake the JWKS by injecting a fake jwk_client into
Auth0Client, so they run with NO network, NO secrets, and NO real Auth0 call,
and fit the DB-free ci/unit gate.

Seam: an in-test RSA keypair signs the tokens; the fake jwk_client returns the
test PUBLIC key as the signing key (happy/rejection paths), or raises
PyJWKClientError (fail-closed paths: kid not found / JWKS unreachable).

Groups:
    A1-A3:   happy paths (TENANT, PLATFORM, PLATFORM with impersonation tenant_id)
    B4-B6:   signature/integrity attacks (tampered, garbage signature, foreign key)
    C7-C12:  claim validation (expired, wrong aud/iss, missing/malformed claims)
    G13-G14: FAIL CLOSED (kid not found, JWKS unreachable) -> reject, never a pass
    F15-F16: missing JWT (empty, None)
"""
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import jwt
import pytest
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from admin_backend.auth.auth0 import Auth0Client
from admin_backend.auth.stub import (
    CLAIM_EMAIL,
    CLAIM_TENANT_ID,
    CLAIM_USER_ID,
    CLAIM_USER_TYPE,
)
from admin_backend.config import Settings
from admin_backend.errors import (
    AuthInvalidError,
    AuthMissingError,
    InvalidTenantIdError,
)

_TEST_ISSUER = "https://test-tenant.us.auth0.com/"
_TEST_AUDIENCE = "https://api.cortex.test/"
_TEST_KID = "test-key-1"


def _gen_rsa() -> tuple[str, str]:
    """A fresh RSA keypair as (private_pem, public_pem)."""
    key = rsa.generate_private_key(
        public_exponent=65537, key_size=2048, backend=default_backend()
    )
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return private_pem, public_pem


class _FakeSigningKey:
    """Stand-in for jwt.PyJWK: only the .key attribute is used by Auth0Client."""

    def __init__(self, key: str) -> None:
        self.key = key


class _FakeJWKClient:
    """Serves a fixed public key as the signing key (no network)."""

    def __init__(self, public_pem: str) -> None:
        self._public_pem = public_pem

    def get_signing_key_from_jwt(self, token: str) -> _FakeSigningKey:
        return _FakeSigningKey(self._public_pem)


class _RaisingJWKClient:
    """Simulates a JWKS that cannot supply a key: kid not found, or endpoint
    unreachable. Both surface from PyJWKClient as PyJWKClientError."""

    def __init__(self, message: str) -> None:
        self._message = message

    def get_signing_key_from_jwt(self, token: str) -> _FakeSigningKey:
        raise jwt.exceptions.PyJWKClientError(self._message)


def _settings() -> Settings:
    """Explicit test Settings (env-independent): AUTH0 mode, local environment so
    the production validators do not fire, dummy DB url (never connected)."""
    return Settings(  # type: ignore[call-arg]
        database_url="postgresql+psycopg://t:t@localhost:5432/t",
        db_schema="core",
        auth_client_mode="AUTH0",
        environment="local",
        jwt_issuer=_TEST_ISSUER,
        jwt_audience=_TEST_AUDIENCE,
    )


def _make_token(
    private_pem: str,
    *,
    user_id: UUID,
    user_type: str,
    tenant_id: UUID | None = None,
    email: str = "alice@tenant-a.test",
    sub: str = "auth0|abc123",
    exp_offset_seconds: int = 3600,
    iss: str = _TEST_ISSUER,
    aud: str = _TEST_AUDIENCE,
    kid: str = _TEST_KID,
    omit_claims: tuple[str, ...] = (),
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """Sign a token with the given private key, with a kid header (Auth0 shape)."""
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": sub,
        "iss": iss,
        "aud": aud,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=exp_offset_seconds)).timestamp()),
        CLAIM_USER_ID: str(user_id),
        CLAIM_USER_TYPE: user_type,
        CLAIM_EMAIL: email,
    }
    if tenant_id is not None:
        payload[CLAIM_TENANT_ID] = str(tenant_id)
    for claim in omit_claims:
        payload.pop(claim, None)
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, private_pem, algorithm="RS256", headers={"kid": kid})


@pytest.fixture(scope="module")
def keypair() -> tuple[str, str]:
    return _gen_rsa()


@pytest.fixture(scope="module")
def settings() -> Settings:
    return _settings()


@pytest.fixture()
def client(keypair: tuple[str, str], settings: Settings) -> Auth0Client:
    """Auth0Client wired to a fake JWKS serving the test public key (no network)."""
    _, public_pem = keypair
    return Auth0Client(settings, jwk_client=_FakeJWKClient(public_pem))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Group A: happy paths
# ---------------------------------------------------------------------------


def test_a1_tenant_jwt_roundtrip(keypair: tuple[str, str], client: Auth0Client) -> None:
    private_pem, _ = keypair
    tenant_a, user_a = uuid4(), uuid4()
    token = _make_token(
        private_pem, user_id=user_a, user_type="TENANT", tenant_id=tenant_a,
        email="alice@tenant-a.test",
    )
    ctx = client.verify(token)
    assert ctx.user_type == "TENANT"
    assert ctx.tenant_id == tenant_a
    assert ctx.user_id == user_a
    assert ctx.email == "alice@tenant-a.test"
    assert ctx.iss == _TEST_ISSUER
    assert ctx.aud == _TEST_AUDIENCE


def test_a2_platform_jwt_roundtrip(keypair: tuple[str, str], client: Auth0Client) -> None:
    private_pem, _ = keypair
    user = uuid4()
    token = _make_token(private_pem, user_id=user, user_type="PLATFORM", email="staff@sevyn8.test")
    ctx = client.verify(token)
    assert ctx.user_type == "PLATFORM"
    assert ctx.tenant_id is None
    assert ctx.user_id == user


def test_a3_platform_with_impersonation_tenant_id_roundtrip(
    keypair: tuple[str, str], client: Auth0Client
) -> None:
    private_pem, _ = keypair
    user, impersonated = uuid4(), uuid4()
    token = _make_token(
        private_pem, user_id=user, user_type="PLATFORM", tenant_id=impersonated,
        email="staff@sevyn8.test",
    )
    ctx = client.verify(token)
    assert ctx.user_type == "PLATFORM"
    assert ctx.tenant_id == impersonated


# ---------------------------------------------------------------------------
# Group B: signature / integrity attacks
# ---------------------------------------------------------------------------


def test_b4_tampered_user_type_rejected(keypair: tuple[str, str], client: Auth0Client) -> None:
    private_pem, _ = keypair
    token = _make_token(private_pem, user_id=uuid4(), user_type="TENANT", tenant_id=uuid4())
    header_b64, payload_b64, sig = token.split(".")
    # Flip a payload byte without re-signing: signature no longer matches.
    tampered_payload = payload_b64[:-2] + ("AA" if payload_b64[-2:] != "AA" else "BB")
    tampered = f"{header_b64}.{tampered_payload}.{sig}"
    with pytest.raises(AuthInvalidError):
        client.verify(tampered)


def test_b5_garbage_signature_rejected(keypair: tuple[str, str], client: Auth0Client) -> None:
    private_pem, _ = keypair
    token = _make_token(private_pem, user_id=uuid4(), user_type="TENANT", tenant_id=uuid4())
    header_b64, payload_b64, _ = token.split(".")
    with pytest.raises(AuthInvalidError):
        client.verify(f"{header_b64}.{payload_b64}.AAAA")


def test_b6_foreign_signing_key_rejected(keypair: tuple[str, str], settings: Settings) -> None:
    """A token signed with a DIFFERENT private key is rejected: the JWKS serves the
    legitimate public key, so the foreign signature fails verification."""
    _, public_pem = keypair
    foreign_private, _ = _gen_rsa()
    token = _make_token(foreign_private, user_id=uuid4(), user_type="TENANT", tenant_id=uuid4())
    client = Auth0Client(settings, jwk_client=_FakeJWKClient(public_pem))  # type: ignore[arg-type]
    with pytest.raises(AuthInvalidError):
        client.verify(token)


# ---------------------------------------------------------------------------
# Group C: claim validation
# ---------------------------------------------------------------------------


def test_c7_expired_token_rejected(keypair: tuple[str, str], client: Auth0Client) -> None:
    private_pem, _ = keypair
    token = _make_token(
        private_pem, user_id=uuid4(), user_type="TENANT", tenant_id=uuid4(),
        exp_offset_seconds=-3600,
    )
    with pytest.raises(AuthInvalidError):
        client.verify(token)


def test_c8_wrong_audience_rejected(keypair: tuple[str, str], client: Auth0Client) -> None:
    private_pem, _ = keypair
    token = _make_token(
        private_pem, user_id=uuid4(), user_type="TENANT", tenant_id=uuid4(),
        aud="https://wrong-audience.example/",
    )
    with pytest.raises(AuthInvalidError):
        client.verify(token)


def test_c9_wrong_issuer_rejected(keypair: tuple[str, str], client: Auth0Client) -> None:
    private_pem, _ = keypair
    token = _make_token(
        private_pem, user_id=uuid4(), user_type="TENANT", tenant_id=uuid4(),
        iss="https://wrong-issuer.example/",
    )
    with pytest.raises(AuthInvalidError):
        client.verify(token)


def test_c10_missing_user_id_claim_rejected(
    keypair: tuple[str, str], client: Auth0Client
) -> None:
    private_pem, _ = keypair
    token = _make_token(
        private_pem, user_id=uuid4(), user_type="TENANT", tenant_id=uuid4(),
        omit_claims=(CLAIM_USER_ID,),
    )
    with pytest.raises(AuthInvalidError):
        client.verify(token)


def test_c11_invalid_user_type_rejected(
    keypair: tuple[str, str], client: Auth0Client
) -> None:
    private_pem, _ = keypair
    token = _make_token(
        private_pem, user_id=uuid4(), user_type="TENANT", tenant_id=uuid4(),
        extra_claims={CLAIM_USER_TYPE: "ADMIN"},
    )
    with pytest.raises(AuthInvalidError):
        client.verify(token)


def test_c12_malformed_tenant_id_rejected(
    keypair: tuple[str, str], client: Auth0Client
) -> None:
    private_pem, _ = keypair
    token = _make_token(
        private_pem, user_id=uuid4(), user_type="TENANT", tenant_id=uuid4(),
        extra_claims={CLAIM_TENANT_ID: "not-a-uuid"},
    )
    with pytest.raises(InvalidTenantIdError):
        client.verify(token)


# ---------------------------------------------------------------------------
# Group G: FAIL CLOSED (key acquisition). Any doubt -> reject, never a pass.
# ---------------------------------------------------------------------------


def test_g13_kid_not_found_rejected(keypair: tuple[str, str], settings: Settings) -> None:
    """JWKS has no key matching the token's kid -> reject (never returns a context)."""
    private_pem, _ = keypair
    token = _make_token(private_pem, user_id=uuid4(), user_type="TENANT", tenant_id=uuid4())
    client = Auth0Client(  # type: ignore[arg-type]
        settings, jwk_client=_RaisingJWKClient("Unable to find a signing key that matches kid")
    )
    with pytest.raises(AuthInvalidError):
        client.verify(token)


def test_g14_jwks_unreachable_rejected(keypair: tuple[str, str], settings: Settings) -> None:
    """JWKS endpoint unreachable -> reject (fail closed), never a pass."""
    private_pem, _ = keypair
    token = _make_token(private_pem, user_id=uuid4(), user_type="TENANT", tenant_id=uuid4())
    client = Auth0Client(  # type: ignore[arg-type]
        settings, jwk_client=_RaisingJWKClient("Fail to fetch data from the url, network unreachable")
    )
    with pytest.raises(AuthInvalidError):
        client.verify(token)


# ---------------------------------------------------------------------------
# Group F: missing JWT
# ---------------------------------------------------------------------------


def test_f15_empty_string_rejected(client: Auth0Client) -> None:
    with pytest.raises(AuthMissingError):
        client.verify("")


def test_f16_none_rejected(client: Auth0Client) -> None:
    with pytest.raises(AuthMissingError):
        client.verify(None)
