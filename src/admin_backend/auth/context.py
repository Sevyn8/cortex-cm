"""AuthContext: the verified identity context derived from a valid JWT.

Per D-24 (CLAUDE.md), the JWT carries identity claims only. No roles,
no permissions. Permission resolution happens in-app per request from
the DB tables (roles, permissions, role_permissions,
user_role_assignments).

AuthContext is frozen: mutation raises ValidationError as
defence-in-depth against later middleware accidentally flipping fields
between auth resolution and the request reaching a handler.

Validation rules:

  - user_type=TENANT requires non-NULL tenant_id (cross-field
    validator). A TENANT user with no tenant context is a malformed
    AuthContext.
  - user_type=PLATFORM is permissive on tenant_id: NULL is the standard
    case; non-NULL is the impersonation pattern (deferred capability,
    per FN-AB-14). Both shapes are accepted at v0.
  - email must look email-shaped via a basic regex (no email-validator
    dependency; Auth0 owns the strict validation).
  - tenant_id, user_id are UUIDs (uuid.UUID, not Pydantic UUID4 which
    is over-strict for v0).
  - sub, iss are non-empty strings.
  - aud is str | list[str] because Auth0 may issue tokens with either
    shape (single string when only the API audience applies; list when
    the userinfo endpoint also applies).
"""
import re
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


# Basic email-shape regex; full validation is Auth0's job. Catches
# obvious garbage (no '@', no domain, leading/trailing whitespace) but
# does not enforce RFC 5322 conformance.
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
    aud: str | list[str]
    exp: int

    # Custom claims (Auth0 namespaced; per D-24)
    user_id: UUID
    tenant_id: UUID | None
    user_type: Literal["PLATFORM", "TENANT"]
    email: str

    @field_validator("email")
    @classmethod
    def email_must_look_email_shaped(cls, v: str) -> str:
        if not _EMAIL_RE.match(v):
            raise ValueError(f"email does not look email-shaped: {v!r}")
        return v

    @field_validator("sub", "iss")
    @classmethod
    def must_be_non_empty(cls, v: str) -> str:
        if not v:
            raise ValueError("must be non-empty")
        return v

    @model_validator(mode="after")
    def tenant_user_must_have_tenant_id(self) -> "AuthContext":
        # PLATFORM is permissive: NULL is the standard case; non-NULL
        # is the impersonation pattern (deferred capability per
        # FN-AB-14). Both shapes pass.
        if self.user_type == "TENANT" and self.tenant_id is None:
            raise ValueError(
                "TENANT user_type requires non-NULL tenant_id; got "
                "user_type=TENANT, tenant_id=None. This is a malformed "
                "AuthContext."
            )
        return self
