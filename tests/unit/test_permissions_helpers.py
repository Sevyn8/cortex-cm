"""Unit tests for ``satisfying_scopes`` and ``_SCOPE_CASCADE_ORDER`` (Step 6.9.3.1).

Pure-Python tests. No database, no fixtures. Live in ``tests/unit/``
alongside other DB-free unit tests (``test_engine.py`` etc.).

Two concerns:

1. ``satisfying_scopes()`` returns the correct cascade list for each v0
   ``PermissionScope`` enum value (GLOBAL/TENANT/STORE).

2. ``_SCOPE_CASCADE_ORDER`` integrity: length, enum-coverage, canonical
   match. The enum-coverage test catches the drift case where the DB
   ``permission_scope_enum`` expands without updating the tuple; the
   canonical-match test catches reordering drift the coverage test
   misses.
"""
from admin_backend.auth.permissions import (
    _SCOPE_CASCADE_ORDER,
    satisfying_scopes,
)
from admin_backend.models.permission import PermissionScope


# ---------------------------------------------------------------------------
# satisfying_scopes() — per-enum-value behaviour
# ---------------------------------------------------------------------------


def test_satisfying_scopes_global() -> None:
    """GLOBAL request: only GLOBAL grants satisfy."""
    assert satisfying_scopes(PermissionScope.GLOBAL) == ["GLOBAL"]


def test_satisfying_scopes_tenant() -> None:
    """TENANT request: GLOBAL and TENANT grants satisfy."""
    assert satisfying_scopes(PermissionScope.TENANT) == ["GLOBAL", "TENANT"]


def test_satisfying_scopes_store() -> None:
    """STORE request: all higher scopes via cascade.

    Includes forward-compat levels (BUSINESS_UNIT, HQ, COUNTRY, REGION)
    that aren't in the v0 ``PermissionScope`` enum yet. The SQL call
    site filters these out before binding via
    ``_satisfying_scopes_for_sql``.
    """
    assert satisfying_scopes(PermissionScope.STORE) == [
        "GLOBAL",
        "TENANT",
        "BUSINESS_UNIT",
        "HQ",
        "COUNTRY",
        "REGION",
        "STORE",
    ]


# ---------------------------------------------------------------------------
# _SCOPE_CASCADE_ORDER — integrity
# ---------------------------------------------------------------------------


def test_scope_cascade_order_has_eight_levels() -> None:
    """Tuple lists all 8 hierarchy levels (1 implicit Platform + 7
    ``org_node_type_enum`` values)."""
    assert len(_SCOPE_CASCADE_ORDER) == 8


def test_scope_cascade_order_includes_all_enum_values() -> None:
    """Every current ``PermissionScope`` enum value must appear in
    ``_SCOPE_CASCADE_ORDER``.

    Catches drift if the DB enum is expanded (e.g., a future REGION
    addition to ``permission_scope_enum``) without updating the cascade
    tuple. Without this test, an expanded enum would silently fall
    through to the helper's defensive single-scope branch.
    """
    enum_values = {s.value for s in PermissionScope}
    order_values = set(_SCOPE_CASCADE_ORDER)
    missing = enum_values - order_values
    assert not missing, f"Enum values not in cascade order: {missing}"


def test_scope_cascade_order_matches_canonical() -> None:
    """Tuple exactly matches the canonical org-hierarchy order.

    The previous test catches enum-vs-tuple coverage drift but not
    reordering (e.g., swapping TENANT and BUSINESS_UNIT positions would
    still pass coverage). This test pins the exact order.
    """
    assert _SCOPE_CASCADE_ORDER == (
        "GLOBAL",
        "TENANT",
        "BUSINESS_UNIT",
        "HQ",
        "COUNTRY",
        "REGION",
        "STORE",
        "DEPARTMENT",
    )
