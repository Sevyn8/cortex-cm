"""Response schemas for ``/api/v1/me/*`` (Step 6.9.2).

Three Pydantic models:

- ``PermissionGrantRead`` — wire shape for one grant. Mirrors the
  ``PermissionGrant`` dataclass at ``auth/permission_grant.py``. The
  enum fields are typed as ``str`` so JSON serialisation emits the
  canonical value strings (StrEnum subclasses are accepted by Pydantic
  via the ``str`` type without explicit coercion ceremony).

- ``MePermissionsResponse`` — envelope ``{"permissions": [...]}`` for
  ``GET /me/permissions``. Always an array (D-30 list-wrapper-with-key
  shape per the batch-by-key convention precedent at Step 3.6).

- ``MeCanDoResponse`` — flat object for ``GET /me/can-do``:
  ``{"allowed": bool, "reason_code": str}``. Single-resource shape,
  D-30 exception consistent with ``/api/v1/tenants/{tenant_id}``'s
  ``TenantDetail`` precedent.

``model_config = {"from_attributes": True}`` lets the router build a
``PermissionGrantRead`` directly from a ``PermissionGrant`` dataclass
instance via ``model_validate(...)`` without restating the fields.
``extra="forbid"`` guards shape drift; future field additions go via
explicit Pydantic schema edits, not silent acceptance of unknown
attributes.
"""
from pydantic import BaseModel, ConfigDict


class PermissionGrantRead(BaseModel):
    """One permission grant, wire shape for JSON responses."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    module: str
    resource: str
    action: str
    scope: str
    anchor_path: str | None


class MePermissionsResponse(BaseModel):
    """Envelope for ``GET /me/permissions``: always an array."""

    model_config = ConfigDict(extra="forbid")

    permissions: list[PermissionGrantRead]


class MeCanDoResponse(BaseModel):
    """Flat result for ``GET /me/can-do``: allowed boolean + reason code."""

    model_config = ConfigDict(extra="forbid")

    allowed: bool
    reason_code: str
