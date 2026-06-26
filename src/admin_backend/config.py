"""Application configuration via Pydantic v2 BaseSettings.

Reads env vars from the project's .env file. The env_file path is
resolved from THIS file's location (not CWD), so tests, scripts,
migrations, and the app all see the same .env regardless of where
they're invoked.

Two model_validators refuse production with stub-shaped configuration:

  1. production_must_use_auth0: ENVIRONMENT=production requires
     AUTH_CLIENT_MODE=AUTH0. Stub auth must never be used in prod.

  2. production_issuer_must_not_be_stub: ENVIRONMENT=production
     refuses any JWT_ISSUER that contains an obvious stub marker,
     isn't HTTPS, or doesn't end with '/' (Auth0 convention).
     Permissive on legitimate Auth0 issuers (any regional
     auth0.com pattern, custom domains, etc.) by checking only for
     stub markers rather than whitelisting Auth0 patterns.

This is a Pydantic-level gate at config-load time. The actual
app-startup refusal happens at Step 2.4 main.py; this layer ensures
Settings refuses to validate.
"""
import re
from functools import lru_cache
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_service_version() -> str:
    """Default for ``service_version`` when the env var is unset.

    Reads the installed wheel's metadata version (`pyproject.toml`'s
    ``[project].version``). Falls back to ``"0.0.0-dev"`` if the
    package isn't installed (e.g., a layout where the source tree
    is on PYTHONPATH but ``uv sync`` hasn't run).

    This default is overridden in deployed images by the
    ``SERVICE_VERSION`` env var, set either as a Dockerfile ENV
    (baked at build time via ``--build-arg SERVICE_VERSION=<tag>``)
    or as a Cloud Run / GKE deploy-time env override. The deployed
    value should always be the image tag (e.g., ``v0.1.4``) so
    ``/api/v1/health`` reports a value the operator can correlate
    back to the running container.
    """
    try:
        return _pkg_version("admin-backend")
    except PackageNotFoundError:
        return "0.0.0-dev"


# Resolve project root from this file's location (not CWD).
# config.py lives at src/admin_backend/config.py: parent.parent.parent
# walks src/admin_backend -> src -> project root.
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# db_schema is interpolated into a SET search_path SQL string at
# connect time (see db/engine.py). Validate as a Postgres unquoted
# identifier so injection cannot reach the DB even if env loading is
# ever subverted. Lowercase-only matches the project convention.
_IDENTIFIER_RE = re.compile(r"^[a-z_][a-z0-9_]*$")

# api_prefix is interpolated into FastAPI route registrations and the
# AuthMiddleware public-paths set. Validate as URL path segments so a
# misconfigured value cannot accidentally introduce wildcards or
# query-string ambiguity.
_API_PREFIX_RE = re.compile(r"^/[a-z0-9/_-]+$")


class Settings(BaseSettings):
    """Typed application configuration loaded from env vars (and .env)."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # ignore env vars unrelated to Settings
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
    # Auth0 JWKS endpoint (AUTH_CLIENT_MODE=AUTH0). Optional: when unset, Auth0Client
    # derives it from jwt_issuer (issuer + .well-known/jwks.json, the Auth0 convention).
    # Stub mode never reads it. Documented in .env.example as AUTH0_JWKS_URL.
    auth0_jwks_url: str | None = None

    # Auth0 Management API (Step CI-4a, provisioning plumbing). All optional/None so STUB
    # mode and existing tests need nothing. The M2M client secret is SecretStr so it never
    # appears in a Settings repr or log; read it with .get_secret_value() at the one use
    # site (the token request). auth0_mgmt_audience / auth0_token_endpoint are explicit but
    # the client derives them from jwt_issuer when unset (issuer + api/v2/ and oauth/token).
    # auth0_db_connection is the Auth0 database-connection name a created user belongs to;
    # the client defaults to "Username-Password-Authentication" when unset.
    auth0_m2m_client_id: str | None = None
    auth0_m2m_client_secret: SecretStr | None = None
    auth0_mgmt_audience: str | None = None
    auth0_token_endpoint: str | None = None
    auth0_db_connection: str | None = None

    # Application
    app_region: Literal["EU", "US", "LOCAL"] = "LOCAL"
    environment: Literal["local", "development", "staging", "production"] = "local"
    log_level: str = "INFO"

    # CORS: comma-separated list of allowed origins, parsed in main.py.
    # Default empty disables CORS (no origins permitted) until configured.
    cors_allowed_origins: str = ""

    # API URL prefix applied at app.include_router(prefix=...) and used
    # for the OpenAPI/docs/redoc/health/ready URLs. Forward-compat lever
    # for a future v2 cutover (Step 3.3).
    api_prefix: str = "/api/v1"

    # Service version reported in /api/v1/health and the FastAPI
    # OpenAPI ``info.version``. Default is the installed wheel's
    # metadata version (pyproject.toml). In deployed images, set the
    # ``SERVICE_VERSION`` env var to the image tag at build/deploy
    # time so the value reported by /api/v1/health matches the
    # running container's tag exactly.
    service_version: str = _default_service_version()

    @field_validator("db_schema")
    @classmethod
    def db_schema_must_be_identifier(cls, v: str) -> str:
        if not _IDENTIFIER_RE.match(v):
            raise ValueError(
                f"DB_SCHEMA must be a valid lowercase Postgres identifier "
                f"(letters, digits, underscores; starting with a letter or "
                f"underscore); got: {v!r}"
            )
        return v

    @field_validator("api_prefix")
    @classmethod
    def api_prefix_must_be_well_formed(cls, v: str) -> str:
        if not v.startswith("/"):
            raise ValueError(
                f"api_prefix must start with '/'; got: {v!r}"
            )
        if v != "/" and v.endswith("/"):
            raise ValueError(
                f"api_prefix must not end with '/'; got: {v!r}"
            )
        if not _API_PREFIX_RE.match(v):
            raise ValueError(
                f"api_prefix must match {_API_PREFIX_RE.pattern} "
                f"(lowercase letters, digits, '_', '-', '/'); got: {v!r}"
            )
        return v

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
                        f"ENVIRONMENT=production cannot use a stub/local/test "
                        f"issuer; got JWT_ISSUER={self.jwt_issuer}. "
                        f"Suspicious marker: '{marker}'."
                    )
            if not self.jwt_issuer.startswith("https://"):
                raise ValueError(
                    f"Production JWT_ISSUER must be HTTPS; got: "
                    f"{self.jwt_issuer}"
                )
            if not self.jwt_issuer.endswith("/"):
                raise ValueError(
                    f"Production JWT_ISSUER must end with '/' "
                    f"(Auth0 convention); got: {self.jwt_issuer}"
                )
        return self


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor: construct once per process.

    Used by FastAPI's lifespan and create_app so they share one Settings
    instance instead of re-parsing the env file. lru_cache (no maxsize)
    is the FastAPI-recommended pattern.
    """
    return Settings()  # type: ignore[call-arg]
