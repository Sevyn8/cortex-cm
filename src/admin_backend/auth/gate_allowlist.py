"""Mandatory-gate-discipline allowlist (Step 6.9.3.2).

Endpoints listed in ``GATE_EXEMPT_PATHS`` are explicitly exempt from
RBAC gating. They require authentication (via ``AuthMiddleware``) but
have no permission gate. The mandatory-gate-discipline test
(``tests/integration/test_gate_discipline.py``) asserts every
``APIRoute`` is either gated (has ``__permission_gate__`` marker on
some dependency) OR is in this set OR is in
``middleware/auth.py::PUBLIC_PATHS``.

Coupling — must stay in sync with two other sets:
  - ``PUBLIC_PATHS`` at ``middleware/auth.py`` (auth-skip layer)
  - Mandatory-gate-discipline test (consumes both sets)

Adding an endpoint without either a gate or an allowlist entry is a
deploy-time error by design — the discipline test fails the build. See
"Note on gate allowlist coupling" in CLAUDE.md.

v0 exempt set (8 paths):
  - ``/api/v1/me/permissions`` — caller-state; gating against the
    caller's own permission set is circular.
  - ``/api/v1/me/can-do`` — caller-state, same.
  - ``/api/v1/lookups`` — reference data; any authenticated user.
  - ``/api/v1/permissions`` — catalogue; any authenticated user.
  - ``/api/v1/permission-matrix`` — catalogue render-grid; any
    authenticated user.
  - ``/api/v1/roles`` — role catalogue view; any authenticated user.
  - ``/api/v1/roles/{role_id}/permissions`` — same.
  - ``/api/v1/roles/{role_id}`` — role detail (E7, Step 6.18.2). Joins
    the other role read endpoints; PATCH (Step 6.18.3) will gate on
    ADMIN.ROLES.OVERRIDE.GLOBAL but GET stays exempt per FN-AB-30
    deferral.

Forward note: revisit gating ``/permissions``,
``/permission-matrix``, ``/roles`` on
``ADMIN.ROLES.VIEW.TENANT`` when Stage 2 write surfaces (FN-AB-NN).
``/lookups`` stays exempt regardless.
"""
from __future__ import annotations


GATE_EXEMPT_PATHS: frozenset[str] = frozenset({
    "/api/v1/me/permissions",
    "/api/v1/me/can-do",
    "/api/v1/lookups",
    "/api/v1/permissions",
    "/api/v1/permission-matrix",
    "/api/v1/roles",
    "/api/v1/roles/{role_id}",
    "/api/v1/roles/{role_id}/permissions",
})
