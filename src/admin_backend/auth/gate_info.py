"""Permission gate marker dataclass (Step 6.9.3.2).

Every gate function returned by ``require(...)`` carries a
``PermissionGateInfo`` instance via its ``__permission_gate__``
attribute. The mandatory-gate-discipline meta-test reads this
attribute to assert every retrofittable APIRoute is either gated or
in the explicit allowlist.

Strict introspection target: code that walks ``app.routes`` should
check ``hasattr(dep.call, "__permission_gate__")`` on each
``Dependant.call`` to identify gate dependencies. The marker is the
canonical signal.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Literal

from admin_backend.models.permission import (
    PermissionAction,
    PermissionResource,
    PermissionScope,
)
from admin_backend.models.tenant_module_access import ModuleCode


@dataclass(frozen=True)
class PermissionGateInfo:
    """Marker attached by ``require(...)`` to every gate function.

    Fields capture the gate's required tuple plus an optional reference
    to the per-resource anchor dependency and the optional audience
    constraint (Step 6.11.1). The discipline meta-test only reads
    ``__permission_gate__`` for existence; positive verification of the
    tuple (per-route assertions) lives in
    ``tests/integration/test_gate_retrofit.py::T_RET_6``.
    """

    module: ModuleCode
    resource: PermissionResource
    action: PermissionAction
    scope: PermissionScope
    anchor_dep: Callable[..., Awaitable[str]] | None
    audience: Literal["PLATFORM", "TENANT"] | None = None
