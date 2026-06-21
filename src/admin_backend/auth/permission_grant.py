"""PermissionGrant: one permission held by one user at one anchor.

Shipped at Step 6.9.1; consumed at Step 6.9.2's ``/me/permissions``
endpoint. ``has_permission()`` itself returns ``(bool, ReasonCode, str)``
and never constructs PermissionGrant — the dataclass is the stable
contract for the future enumeration endpoint to materialise.

Frozen dataclass: hashable, equality-comparable, immutable. The
``anchor_path`` field is ``None`` for PLATFORM-audience grants (which
apply globally) and an ltree-formatted path string for TENANT-side
grants (anchored at an ``org_nodes.path``).
"""
from dataclasses import dataclass

from admin_backend.models.permission import (
    PermissionAction,
    PermissionResource,
    PermissionScope,
)
from admin_backend.models.tenant_module_access import ModuleCode


@dataclass(frozen=True)
class PermissionGrant:
    """One permission held by one user at one anchor."""

    module: ModuleCode
    resource: PermissionResource
    action: PermissionAction
    scope: PermissionScope
    anchor_path: str | None
