# Prompt — Step 2.1: Stub auth — keys, config, AuthContext, StubAuthClient

> Paste this entire block into a fresh Claude Code session when starting Step 2.1.
> Revised after stress test: looser production issuer check (allows custom domains and any Auth0 regional pattern), `aud` field handles both string and list, no `email-validator` dependency, `Literal` types inlined, tamper helper for integrity tests, absolute path for `env_file`.

---

## Pre-flight

Before doing any work:

1. Run `./scripts/check_setup.sh`. If any check fails, stop and report. Do not attempt to fix setup unless told.
2. Read `CLAUDE.md` fully. Pay particular attention to:
   - **D-24** (JWT carries identity claims only; permissions resolve in-app per request) — the load-bearing decision for this step.
   - **D-07** (Auth0 for authentication, with stub during build; production swap is config-only).
   - **FN-AB-14** (user_role_assignments PLATFORM-audience policy fix; deferred to Step 2.2b but informs AuthContext shape).
   - The error-class hierarchy section.
   - The environment variables section.
3. Read `docs/architecture.md` "Authentication" section.
4. Read `BUILD_PLAN.md` Step 2.1 in full.
5. Read this prompt fully and confirm scope before writing code.

---

## Step ID and intent

**Step 2.1** — Generate RS256 keypair, wire env config, define `AuthContext` and `StubAuthClient`. Mint and verify JWTs locally with Auth0-shaped claims so the Auth0 swap is config-only later.

This step lays the auth foundation that Step 2.2 (DB session bootstrap) and Step 2.3 (middleware) build on. The shape of `AuthContext` and the JWT claims set must be exactly what Auth0 will produce, so swapping `AUTH_CLIENT_MODE=STUB` for `AUTH_CLIENT_MODE=AUTH0` requires zero code changes.

This is a CLAUDE_CODE step. No DB work, no FastAPI handlers, no middleware (those are 2.2, 2.3, 2.4).

---

## Required behaviour

### JWT claims policy per D-24

The JWT carries only identity-shape claims. Custom claims (Auth0 namespaced):

- `https://ithina.com/tenant_id` — UUID string. NULL for PLATFORM users.
- `https://ithina.com/user_type` — string `"PLATFORM"` or `"TENANT"`.
- `https://ithina.com/user_id` — UUID string of the row in `platform_users` or `tenant_users`.
- `https://ithina.com/email` — string.

Plus standard JWT claims: `sub`, `iss`, `aud`, `exp`, `iat`. Optionally `nbf`.

**No `roles` claim. No `permissions` claim.** Permissions resolve in-app per request from the DB.

Note on `aud`: Auth0 may issue tokens where `aud` is either a single string OR a list of strings (when the API and userinfo endpoints both apply). PyJWT's `decode(...)` handles both transparently when `audience=` is provided. `AuthContext.aud` must accept both shapes.

### AuthContext validation rules per D-24

`AuthContext` is a frozen Pydantic v2 model. Validation rules:

- `user_type == "TENANT"` requires non-NULL `tenant_id`. Cross-field validator. Reject with clear error if violated.
- `user_type == "PLATFORM"` is **permissive** on `tenant_id`: NULL is the standard case; non-NULL is allowed (impersonation pattern, deferred capability per FN-AB-14).
- `user_type` must be exactly `"PLATFORM"` or `"TENANT"`. No other values. Use `Literal["PLATFORM", "TENANT"]` inline in the field declaration.
- `email` must look email-shaped. A basic regex check (no `email-validator` dependency). Auth0 owns the strict validation.
- `tenant_id`, `user_id` are UUIDs. Use `UUID` type from `uuid` (not Pydantic `UUID4` — too strict for v0).
- `exp` is an int (Unix timestamp seconds).
- `sub`, `iss` are non-empty strings.
- `aud` is `str | list[str]` (Auth0 may return either shape).

`AuthContext` is **frozen** (`model_config = ConfigDict(frozen=True)`). Mutation raises `ValidationError`. Defence-in-depth against later middleware accidentally flipping fields.

### Environment-consistency assertion

The `Settings` class in `src/admin_backend/config.py` must include model-level validators that refuse production with stub-shaped configuration:

1. If `ENVIRONMENT == "production"` and `AUTH_CLIENT_MODE != "AUTH0"`, raise `ValueError`.
2. If `ENVIRONMENT == "production"` and `JWT_ISSUER` looks like a stub/local issuer, raise `ValueError`.

The check for "stub/local" should be permissive on legitimate production issuers (Auth0-hosted with any regional pattern; Auth0 custom domains; etc.) but strict on obvious stub markers:

```python
@model_validator(mode="after")
def production_issuer_must_not_be_stub(self) -> "Settings":
    if self.environment == "production":
        issuer_lower = self.jwt_issuer.lower()
        for marker in ("stub", "local", "test", "example", "fake"):
            if marker in issuer_lower:
                raise ValueError(
                    f"ENVIRONMENT=production cannot use a stub/local/test issuer; "
                    f"got JWT_ISSUER={self.jwt_issuer}. "
                    f"Suspicious marker: '{marker}'."
                )
        if not self.jwt_issuer.startswith("https://"):
            raise ValueError(
                f"Production JWT_ISSUER must be HTTPS; got: {self.jwt_issuer}"
            )
        if not self.jwt_issuer.endswith("/"):
            raise ValueError(
                f"Production JWT_ISSUER must end with '/' (Auth0 convention); "
                f"got: {self.jwt_issuer}"
            )
    return self
```

This is a Pydantic-level gate at config-load time. The actual app-startup refusal happens in Step 2.4's `main.py`; this step ensures Settings refuses to validate.

### Operational gotchas (from Steps 1.4 and 1.6)

1. **Bash tool subshells don't inherit env vars.** `set -a && source .env && set +a` at the start of each bash block.
2. **Pydantic v2.** This project uses Pydantic v2 (per `pyproject.toml`). Use `field_validator`, `model_validator`, `ConfigDict`, `Annotated`. Not v1's `@validator` decorator. If `pyproject.toml` shows v1, stop and surface.
3. **`pydantic-settings` is a separate package** from `pydantic` in v2. Verify it's in `pyproject.toml`. If missing, add to deps.
4. **`env_file` path resolution.** `pydantic-settings` reads `.env` relative to CWD by default. Tests may run from a different CWD. Use an absolute path:

```python
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent  # admin_backend/ -> src/ -> project root
model_config = SettingsConfigDict(
    env_file=PROJECT_ROOT / ".env",
    env_file_encoding="utf-8",
    case_sensitive=False,
    extra="ignore",  # don't error on env vars unrelated to Settings
)
```

---

## Scope in

### File 1: Generate RS256 keypair

```bash
mkdir -p keys
openssl genrsa -out keys/jwt_private.pem 2048
openssl rsa -in keys/jwt_private.pem -pubout -out keys/jwt_public.pem
chmod 600 keys/jwt_private.pem
chmod 644 keys/jwt_public.pem
```

Verify `keys/` is gitignored:

```bash
grep -E "^keys/?$|^/keys/?$" .gitignore
```

If it returns nothing, add `keys/` to `.gitignore`.

### File 2: `src/admin_backend/config.py`

Pydantic v2 `BaseSettings`. Reads env vars; refuses to validate if required ones missing or production-inconsistent.

```python
from pathlib import Path
from typing import Literal
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Database
    database_url: str
    db_schema: str

    # Authentication
    auth_client_mode: Literal["STUB", "AUTH0"] = "STUB"
    jwt_issuer: str
    jwt_audience: str
    jwt_public_key_path: Path = Path("keys/jwt_public.pem")
    jwt_private_key_path: Path = Path("keys/jwt_private.pem")
    token_default_ttl_seconds: int = 3600

    # Application
    app_region: Literal["EU", "US", "LOCAL"] = "LOCAL"
    environment: Literal["local", "development", "staging", "production"] = "local"
    log_level: str = "INFO"

    # Add other fields from .env.example as needed; can land incrementally.

    @model_validator(mode="after")
    def production_must_use_auth0(self) -> "Settings":
        if self.environment == "production" and self.auth_client_mode != "AUTH0":
            raise ValueError(
                f"ENVIRONMENT=production requires AUTH_CLIENT_MODE=AUTH0; "
                f"got AUTH_CLIENT_MODE={self.auth_client_mode}. "
                "Stub auth must never be used in production."
            )
        return self

    @model_validator(mode="after")
    def production_issuer_must_not_be_stub(self) -> "Settings":
        if self.environment == "production":
            issuer_lower = self.jwt_issuer.lower()
            for marker in ("stub", "local", "test", "example", "fake"):
                if marker in issuer_lower:
                    raise ValueError(
                        f"ENVIRONMENT=production cannot use a stub/local/test issuer; "
                        f"got JWT_ISSUER={self.jwt_issuer}. "
                        f"Suspicious marker: '{marker}'."
                    )
            if not self.jwt_issuer.startswith("https://"):
                raise ValueError(
                    f"Production JWT_ISSUER must be HTTPS; got: {self.jwt_issuer}"
                )
            if not self.jwt_issuer.endswith("/"):
                raise ValueError(
                    f"Production JWT_ISSUER must end with '/' (Auth0 convention); "
                    f"got: {self.jwt_issuer}"
                )
        return self
```

The exact field set should reflect `.env.example`. Add fields incrementally; this step doesn't need them all wired in (e.g., CORS, GCP — those land at Step 2.4).

### File 3: `src/admin_backend/auth/context.py`

```python
import re
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


# Basic email-shape regex; full validation is Auth0's job.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AuthContext(BaseModel):
    """Verified identity context derived from a valid JWT.

    Per D-24: identity claims only. No roles, no permissions.
    Permissions resolve in-app per request from the DB.

    Frozen: mutation raises ValidationError as defence-in-depth.
    """
    model_config = ConfigDict(frozen=True)

    # Standard JWT claims
    sub: str
    iss: str
    aud: str | list[str]   # Auth0 may return string or list
    exp: int

    # Custom claims (Auth0 namespaced)
    user_id: UUID
    tenant_id: UUID | None
    user_type: Literal["PLATFORM", "TENANT"]
    email: str

    @field_validator("email")
    @classmethod
    def email_must_look_email_shaped(cls, v: str) -> str:
        if not _EMAIL_RE.match(v):
            raise ValueError(f"email does not look email-shaped: {v}")
        return v

    @field_validator("sub", "iss")
    @classmethod
    def must_be_non_empty(cls, v: str) -> str:
        if not v:
            raise ValueError("must be non-empty")
        return v

    @model_validator(mode="after")
    def tenant_user_must_have_tenant_id(self) -> "AuthContext":
        if self.user_type == "TENANT" and self.tenant_id is None:
            raise ValueError(
                "TENANT user_type requires non-NULL tenant_id; "
                "got user_type=TENANT, tenant_id=None. "
                "This is a malformed AuthContext."
            )
        return self
```

PLATFORM with non-NULL tenant_id is **allowed** for v0 (impersonation pattern, deferred). The validator only enforces the TENANT case.

### File 4: `src/admin_backend/auth/stub.py`

The stub auth client. Verifies JWTs signed with the local RS256 private key.

```python
from uuid import UUID

import jwt  # PyJWT

from admin_backend.auth.context import AuthContext
from admin_backend.config import Settings
from admin_backend.errors import (
    AuthInvalidError,
    AuthMissingError,
    InvalidTenantIdError,
)


# Custom claim namespaces per D-24
NAMESPACE = "https://ithina.com"
CLAIM_TENANT_ID = f"{NAMESPACE}/tenant_id"
CLAIM_USER_TYPE = f"{NAMESPACE}/user_type"
CLAIM_USER_ID = f"{NAMESPACE}/user_id"
CLAIM_EMAIL = f"{NAMESPACE}/email"


class StubAuthClient:
    """JWT verification client for build-phase stub auth.

    Verifies tokens signed with the local RS256 private key.
    Rejects expired, malformed, wrong-audience, wrong-issuer tokens.
    Extracts custom claims into AuthContext per D-24.
    """

    def __init__(self, settings: Settings):
        self._settings = settings
        try:
            self._public_key = settings.jwt_public_key_path.read_text()
        except (FileNotFoundError, PermissionError) as e:
            raise RuntimeError(
                f"Cannot load JWT public key from {settings.jwt_public_key_path}: {e}. "
                "Was the keypair generated? See keys/ directory."
            ) from e

    def verify(self, jwt_string: str) -> AuthContext:
        """Verify a JWT and return a validated AuthContext.

        Raises:
            AuthMissingError: jwt_string is empty/None.
            AuthInvalidError: signature, expiry, audience, issuer, or claim shape invalid.
            InvalidTenantIdError: tenant_id claim is not a valid UUID.
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

        # Extract and validate custom claims
        tenant_id_raw = payload.get(CLAIM_TENANT_ID)
        try:
            tenant_id = UUID(tenant_id_raw) if tenant_id_raw else None
        except (ValueError, TypeError) as e:
            raise InvalidTenantIdError(
                f"tenant_id claim is not a valid UUID: {tenant_id_raw}"
            ) from e

        user_id_raw = payload.get(CLAIM_USER_ID)
        if not user_id_raw:
            raise AuthInvalidError(f"Missing required claim: {CLAIM_USER_ID}")
        try:
            user_id = UUID(user_id_raw)
        except (ValueError, TypeError) as e:
            raise AuthInvalidError(
                f"user_id claim is not a valid UUID: {user_id_raw}"
            ) from e

        user_type = payload.get(CLAIM_USER_TYPE)
        if user_type not in ("PLATFORM", "TENANT"):
            raise AuthInvalidError(
                f"user_type claim must be PLATFORM or TENANT; got: {user_type}"
            )

        email = payload.get(CLAIM_EMAIL)
        if not email:
            raise AuthInvalidError(f"Missing required claim: {CLAIM_EMAIL}")

        # Construct AuthContext (validators run here)
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
            raise AuthInvalidError(f"Token claims failed AuthContext validation: {e}") from e
```

### File 5: `src/admin_backend/auth/testing.py`

Helper for minting test JWTs and tampering them for integrity tests.

```python
import base64
import json
from datetime import datetime, timedelta, timezone
from uuid import UUID

import jwt

from admin_backend.config import Settings
from admin_backend.auth.stub import (
    CLAIM_EMAIL,
    CLAIM_TENANT_ID,
    CLAIM_USER_ID,
    CLAIM_USER_TYPE,
)


def make_test_jwt(
    settings: Settings,
    *,
    user_id: UUID,
    user_type: str,                 # "PLATFORM" or "TENANT"
    tenant_id: UUID | None = None,
    email: str = "test@ithina.local",
    sub: str = "test-sub",
    exp_offset_seconds: int = 3600,
    iss: str | None = None,
    aud: str | None = None,
    omit_claims: tuple[str, ...] = (),  # for negative tests: claims to leave out
    extra_claims: dict | None = None,   # for negative tests: claims with bad values
) -> str:
    """Mint a test JWT signed with the local RS256 private key.

    Required: user_id, user_type. tenant_id required if user_type=TENANT.
    Defaults: email, sub, exp (1 hour), iss/aud (from settings).

    For negative tests:
        - omit_claims: tuple of claim names to leave out of the payload.
        - extra_claims: dict of claim names to bad values (overrides defaults).
    """
    if user_type not in ("PLATFORM", "TENANT"):
        raise ValueError(f"user_type must be PLATFORM or TENANT; got: {user_type}")
    if user_type == "TENANT" and tenant_id is None:
        raise ValueError("TENANT user_type requires tenant_id")

    private_key = settings.jwt_private_key_path.read_text()
    now = datetime.now(timezone.utc)

    payload = {
        "sub": sub,
        "iss": iss or settings.jwt_issuer,
        "aud": aud or settings.jwt_audience,
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


def tamper_token_claim(token: str, claim_name: str, new_value) -> str:
    """Modify a single claim in a signed JWT WITHOUT re-signing.

    Result: token whose payload doesn't match its signature. Used in B4-style
    tests to confirm signature verification rejects tampered tokens.
    """
    header_b64, payload_b64, signature = token.split(".")
    # JWT base64url encoding without padding; restore padding for decode.
    padded = payload_b64 + "=" * (-len(payload_b64) % 4)
    payload = json.loads(base64.urlsafe_b64decode(padded))
    payload[claim_name] = new_value
    new_payload_bytes = json.dumps(payload, separators=(",", ":")).encode()
    new_payload_b64 = base64.urlsafe_b64encode(new_payload_bytes).rstrip(b"=").decode()
    return f"{header_b64}.{new_payload_b64}.{signature}"
```

The `tamper_token_claim` helper is essential for B4. Without it, the integrity test must inline the tamper procedure, which is fiddly enough to cause subtle bugs.

### File 6: `src/admin_backend/errors.py`

Error class hierarchy. This step's contribution:

```python
class AdminBackendError(Exception):
    """Base for all admin-backend errors."""


class AuthMissingError(AdminBackendError):
    """JWT not provided where one was required."""


class AuthInvalidError(AdminBackendError):
    """JWT provided but invalid (signature, expiry, claims, etc.)."""


class InvalidTenantIdError(AdminBackendError):
    """tenant_id claim cannot be parsed as UUID."""
```

The full error hierarchy lands incrementally as later steps need new types. CLAUDE.md's error-class hierarchy section is the source of truth for shape; add to that file (or its successor) if you introduce new categories.

### File 7: Tests — `tests/unit/test_stub_auth.py`

Required test cases (numbered for verification):

**Group A: happy paths**

A1. Mint a TENANT JWT for a tenant A user. Verify roundtrips through `StubAuthClient.verify()`. Returns AuthContext with `tenant_id=A`, `user_type="TENANT"`.

A2. Mint a PLATFORM JWT (no tenant_id). Verify roundtrips. Returns AuthContext with `tenant_id=None`, `user_type="PLATFORM"`.

A3. Mint a PLATFORM JWT with non-NULL tenant_id (impersonation case). Verify roundtrips. Returns AuthContext with `tenant_id=<uuid>`, `user_type="PLATFORM"`. Allowed per D-24.

**Group B: signature/integrity attacks**

B4. Mint a TENANT JWT, tamper the `user_type` claim to `"PLATFORM"` using `tamper_token_claim`. Verify rejected with `AuthInvalidError`.

B5. Mint a JWT, replace the signature segment with garbage (e.g., `"AAAA"`). Verify rejected.

B6. Mint a JWT signed with a DIFFERENT key (a fresh RSA keypair, generated in the test, not the project's `keys/`). Verify rejected.

**Group C: claim validation**

C7. Mint a JWT with `exp_offset_seconds=-3600` (expired). Verify rejected with `AuthInvalidError`.

C8. Mint a JWT with `aud="https://wrong-audience/"` (override). Verify rejected.

C9. Mint a JWT with `iss="https://wrong-issuer/"` (override). Verify rejected.

C10. Mint a JWT with `omit_claims=(CLAIM_USER_ID,)`. Verify rejected with clear error.

C11. Mint a JWT with `extra_claims={CLAIM_USER_TYPE: "ADMIN"}`. Verify rejected.

C12. Mint a JWT with `extra_claims={CLAIM_TENANT_ID: "not-a-uuid"}`. Verify rejected with `InvalidTenantIdError`.

**Group D: AuthContext validation rules**

D13. Construct AuthContext directly with `user_type="TENANT"`, `tenant_id=None`. Verify raises `ValidationError`.

D14. Construct AuthContext directly with `user_type="PLATFORM"`, `tenant_id=None`. Verify succeeds.

D15. Construct AuthContext directly with `user_type="PLATFORM"`, `tenant_id=<uuid>`. Verify succeeds (impersonation allowed).

D16. Try to mutate a constructed AuthContext (`auth.user_type = "PLATFORM"`). Verify raises `ValidationError` (frozen).

**Group E: configuration validation**

E17. Construct `Settings` with `environment="production"` and `auth_client_mode="STUB"` (other fields valid). Verify raises `ValidationError` mentioning AUTH_CLIENT_MODE.

E18. Construct `Settings` with `environment="production"`, `auth_client_mode="AUTH0"`, `jwt_issuer="https://stub-issuer.local/"`. Verify raises `ValidationError` mentioning the suspicious marker.

E19. Construct `Settings` with `environment="local"`, `auth_client_mode="STUB"`, `jwt_issuer="https://stub-issuer.local/"`. Verify succeeds (local env permits stub-shaped issuer).

**Group F: missing JWT**

F20. Call `StubAuthClient.verify("")`. Verify raises `AuthMissingError`.

F21. Call `StubAuthClient.verify(None)`. Verify raises `AuthMissingError`.

Total: 21 test cases. All must pass.

Notes on test setup:

- Tests can read the project's `keys/jwt_*.pem` directly. No separate test keypair needed (except in B6, where a fresh keypair is generated inside the test to confirm rejection).
- For E17/E18/E19, construct `Settings` with explicit kwargs to override `.env`. Pydantic accepts kwargs at construction time; this is the cleanest way to test invalid configurations without monkeypatching `.env`.
- For test fixtures, a `pytest.fixture(scope="module")` for `settings` and `auth_client` is reasonable. Avoid loading `.env` per test.

---

## Scope out

- Auth0 client (production, post-launch).
- Middleware (Step 2.3).
- Dependency `get_tenant_session` (Step 2.2).
- Endpoints (Step 2.4 onward).
- DB-side session bootstrap, `app.tenant_id`, `app.user_type` (Step 2.2).
- Application-startup gate that refuses to boot in production with stub (Step 2.4).
- Audit-log integration (Step 6.x).
- JWKS endpoint, real Auth0 key rotation (post-launch).
- Refresh tokens, token revocation lists (post-launch).
- Structured error fields (`http_status`, `public_message`, structured logging context) — added at Step 2.3 when middleware needs to map exceptions to HTTP responses. The four error classes here are deliberately minimal; the Step 2.3 refactor will be backwards-compatible.

`AuthContext` is consumed by Step 2.2's session bootstrap; do NOT build that bootstrap in this step. Just produce `AuthContext`.

---

## Stop and ask if

- The Pydantic version in `pyproject.toml` is v1, not v2. The prompt assumes v2; v1 has different validator decorators and `Config` class.
- A claim namespace different from `https://ithina.com/` is needed. The prompt assumes that namespace.
- The `pydantic-settings` package isn't installed. It's a separate dependency from `pydantic` in v2. Verify with `uv pip list | grep pydantic`.
- PyJWT is not installed. Add to `pyproject.toml` and `uv sync`. Alternatives (python-jose, authlib) are fine but require adapting the code; prompt assumes PyJWT.
- The keypair generation fails (e.g., openssl not installed). Surface; we'll find an alternative.
- The `keys/` directory is not gitignored. The prompt requires adding it to `.gitignore` if missing.
- Production-issuer check causes false positives for legitimate Auth0 deployments. Surface; we'll relax the marker list.
- Any AuthContext validation rule contradicts D-24 or FN-AB-14. Surface; we'll align.

---

## Acceptance criteria

- `keys/jwt_private.pem` and `keys/jwt_public.pem` exist. `keys/` is gitignored.
- `src/admin_backend/config.py` has a `Settings` class with the two production-consistency validators.
- `src/admin_backend/auth/context.py` has `AuthContext` (frozen, with TENANT-requires-tenant_id validator, email regex check, non-empty sub/iss).
- `src/admin_backend/auth/stub.py` has `StubAuthClient.verify()` handling Group B and Group C error cases.
- `src/admin_backend/auth/testing.py` has `make_test_jwt(...)` and `tamper_token_claim(...)`.
- `src/admin_backend/errors.py` has the four error classes.
- All 21 unit tests pass under `uv run pytest tests/unit/test_stub_auth.py -v`.
- `uv run mypy --strict src/admin_backend` passes (no type errors).
- `./scripts/check_setup.sh` continues to pass. With code beyond `__init__.py`, mypy and pytest-collection checks should now PASS instead of SKIP.
- No new dependencies on `email-validator`. The prompt's regex check replaces `EmailStr`.

---

## What to report at end (BEFORE proposing any commit)

**Report first; commit only after explicit authorisation.** Do NOT run `git add` or `git commit` until I respond to your report.

Provide:

- Files created/modified, with line counts.
- The 21 test results (all PASS expected).
- Sample mint+verify roundtrip output for one TENANT and one PLATFORM token (decoded payload, redacted UUIDs if needed).
- Output of `mypy --strict src/admin_backend`.
- Output of `./scripts/check_setup.sh` (final state).
- Any deviations from this prompt's procedure and why.
- Anything you noticed that doesn't match `CLAUDE.md` (D-24, D-07, FN-AB-14, etc.).

After I authorise the report, propose a Pattern A commit. Show the exact `git status`, `git add`, `git commit` commands. Wait for "yes / no / edit message" before running.

---

## After completing

Once I authorise (after reviewing the report), propose:

```
git status
git add -A
git commit -m "Step 2.1: stub auth — keys, config, AuthContext, StubAuthClient

- keys/jwt_*.pem: RS256 keypair for build-phase JWT signing/verification
- config.py: Settings with two production-consistency model_validators (production must use AUTH0; production issuer must not contain stub/local/test markers and must be https://...auth0-shape)
- auth/context.py: AuthContext (frozen Pydantic v2 model, identity claims only per D-24); cross-field validator enforces TENANT requires tenant_id, PLATFORM permissive on tenant_id (impersonation pattern, FN-AB-14 deferred capability); aud field handles str | list[str]
- auth/stub.py: StubAuthClient verifies RS256 JWTs; rejects expired, malformed, wrong-audience, wrong-issuer, wrong-signature, malformed-claim tokens
- auth/testing.py: make_test_jwt() helper for tests; tamper_token_claim() helper for integrity tests
- errors.py: AdminBackendError, AuthMissingError, AuthInvalidError, InvalidTenantIdError
- 21 unit tests (Groups A-F): happy paths, signature/integrity attacks, claim validation, AuthContext validation rules, configuration validation, missing JWT
- mypy strict clean
- BUILD_PLAN.md Step 2.1 status TODO -> DONE"
```

Ask user "Run? yes / no / edit message".

---

## End of prompt
