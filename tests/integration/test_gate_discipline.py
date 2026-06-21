"""Mandatory-gate-discipline meta-test (Step 6.9.3.2).

Structural assertion: every ``APIRoute`` registered on the FastAPI app
is either gated (has the ``__permission_gate__`` marker on at least one
of its dependencies' ``.call``) OR is in the explicit allowlist
(``GATE_EXEMPT_PATHS`` or ``PUBLIC_PATHS``).

This is the deploy-time guarantee that prevents new endpoints from
shipping ungated by accident. A new APIRoute that lacks BOTH a gate
AND an allowlist entry fails this test, blocking the build. Removing a
gate without adding the path to the allowlist also fails.

Step 6.11.2 extends the meta-test with a second assertion: every
platform-only write route (the 4 tenants write endpoints) must declare
``audience="PLATFORM"`` on its gate. The marker's ``audience`` field
(added at 6.11.1) is the inspection target. Without this assertion, a
future regression that dropped ``audience="PLATFORM"`` from one of the
four routes would let the route reach Layer 2 (has_permission); for
SUPER_ADMIN that still denies correctly via CONFIGURE/OVERRIDE.GLOBAL,
but TENANT users would get the wrong error code (PERMISSION_DENIED
instead of PLATFORM_AUDIENCE_REQUIRED) and the defense-in-depth layering
silently degrades.

Positive per-route gate tuple verification lives in
``tests/integration/test_gate_retrofit.py::T_RET_6``.

LOAD-BEARING: any new ungated, unlisted endpoint fails this test; any
platform-only-route audience drop fails the audience assertion.
"""
from __future__ import annotations

from fastapi.routing import APIRoute

from admin_backend.auth.gate_allowlist import GATE_EXEMPT_PATHS
from admin_backend.main import create_app
from admin_backend.middleware.auth import PUBLIC_PATHS


# Step 6.11.2 + 6.15: platform-only write routes must declare
# audience="PLATFORM". Method-and-path tuples; matches how FastAPI
# represents routes.
#
# Step 6.15 extends the set with the two module-access write
# endpoints (enable / disable). Same OVERRIDE.GLOBAL gate + audience
# discipline as tenants suspend/activate.
_PLATFORM_ONLY_WRITE_ROUTES: frozenset[tuple[str, str]] = frozenset({
    ("POST", "/api/v1/tenants"),
    ("PATCH", "/api/v1/tenants/{tenant_id}"),
    ("POST", "/api/v1/tenants/{tenant_id}/suspend"),
    ("POST", "/api/v1/tenants/{tenant_id}/activate"),
    ("POST", "/api/v1/module-access/{tenant_id}/{module_code}/enable"),
    ("POST", "/api/v1/module-access/{tenant_id}/{module_code}/disable"),
    # Step 6.18.3: role-edit. Gated by ADMIN.ROLES.OVERRIDE.GLOBAL +
    # audience="PLATFORM". PLATFORM-only by gate-tuple construction
    # (LD17 audience-scope coherence: no TENANT role holds .GLOBAL).
    ("PATCH", "/api/v1/roles/{role_id}"),
})


def test_gate_discipline_every_route_is_gated_or_allowlisted() -> None:
    """LOAD-BEARING — every APIRoute is gated OR in an explicit allowlist.

    Mechanism:
      1. Walk ``app.routes``; filter to ``APIRoute`` instances (skips
         FastAPI's own ``/docs``, ``/redoc``, ``/openapi.json`` which
         are Starlette ``Route`` not ``APIRoute``).
      2. If the path is in ``PUBLIC_PATHS`` (auth-skip layer) or
         ``GATE_EXEMPT_PATHS`` (gate-skip layer), allow.
      3. Otherwise scan ``route.dependant.dependencies`` for any
         ``.call`` carrying ``__permission_gate__``. If found, allow.
      4. If none of the above, fail the test with the unlisted path.
    """
    app = create_app()
    allowed_paths = GATE_EXEMPT_PATHS | PUBLIC_PATHS
    ungated_routes: list[str] = []

    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue

        if route.path in allowed_paths:
            continue

        has_gate = any(
            hasattr(dep.call, "__permission_gate__")
            for dep in route.dependant.dependencies
        )
        if not has_gate:
            ungated_routes.append(route.path)

    assert not ungated_routes, (
        f"Routes neither gated nor allowlisted: {ungated_routes}. "
        f"Either add Depends(require(...)) to the handler, or add the "
        f"path to GATE_EXEMPT_PATHS in auth/gate_allowlist.py."
    )


def test_gate_discipline_platform_only_writes_declare_audience() -> None:
    """LOAD-BEARING — platform-only write routes declare audience='PLATFORM'.

    Step 6.11.2 carry-forward: every entry in ``_PLATFORM_ONLY_WRITE_ROUTES``
    must have a ``__permission_gate__`` marker whose ``audience`` field
    is exactly ``"PLATFORM"``. Future write endpoints that ship with
    audience="PLATFORM" should extend ``_PLATFORM_ONLY_WRITE_ROUTES``
    in the same commit; a missing route here is not enforced by this
    test (use the broader gate-discipline test plus a follow-on
    explicit assertion).
    """
    app = create_app()
    failures: list[str] = []
    found: set[tuple[str, str]] = set()

    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue

        for method in route.methods:
            key = (method, route.path)
            if key not in _PLATFORM_ONLY_WRITE_ROUTES:
                continue
            found.add(key)

            gate_marker = None
            for dep in route.dependant.dependencies:
                marker = getattr(dep.call, "__permission_gate__", None)
                if marker is not None:
                    gate_marker = marker
                    break

            if gate_marker is None:
                failures.append(
                    f"{method} {route.path}: no __permission_gate__ marker"
                )
                continue
            if gate_marker.audience != "PLATFORM":
                failures.append(
                    f"{method} {route.path}: gate audience="
                    f"{gate_marker.audience!r}, expected 'PLATFORM'"
                )

    missing = _PLATFORM_ONLY_WRITE_ROUTES - found
    assert not missing, (
        f"Routes listed in _PLATFORM_ONLY_WRITE_ROUTES are not "
        f"registered on the app: {sorted(missing)}. Either fix the "
        f"path/method tuple or remove from the list."
    )
    assert not failures, (
        f"Audience-discipline violations on platform-only writes:\n"
        + "\n".join(f"  - {f}" for f in failures)
    )
