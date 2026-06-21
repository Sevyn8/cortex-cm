"""Integration tests for PATCH /api/v1/roles/{role_id} (Step 6.18.3).

Security-critical surface: role-edit endpoint gated by
``ADMIN.ROLES.OVERRIDE.GLOBAL`` plus ``audience="PLATFORM"``. Tests
cover the full LD17 order-of-operations (LD12 SUPER_ADMIN check, LD3
ARCHIVED rejection, LD11 permission existence, LD10 audience-scope
coherence, LD6 two-layer OVERRIDE invariant) plus diff-replace audit
preservation (LD5 / LD14).

23 of 30 W-tests are load-bearing; per the prompt:

  W1, W3, W4, W5  : happy-path coverage (name, description, perm diff)
  W7-W10          : forbidden-field rejection (extra='forbid')
  W11             : ARCHIVED state rejection
  W13, W14        : LAST_OVERRIDE_HOLDER invariant
  W15, W16        : gate enforcement (PLATFORM_ADMIN, TENANT JWT)
  W18             : Layer 2 tripwire (synthetic Layer 1/2 mismatch)
  W19             : mandatory gate-discipline marker
  W20             : audit-actor populated (updated_by_* + created_by_*)
  W21             : diff-replace preserves created_at on unchanged
  W22, W23, W24   : audience-scope coherence
  W26             : INVALID_PERMISSION_ID for unknown UUIDs
  W29, W30        : SUPER_ADMIN protection (locked) + PLATFORM_ADMIN (allowed)
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from typing import Any
from uuid import UUID

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from admin_backend.auth.context import AuthContext
from admin_backend.auth.testing import make_test_jwt
from admin_backend.config import Settings, get_settings
from admin_backend.db.session import get_tenant_session
from admin_backend.main import create_app


# ============================================================================
# Test app + JWT helpers (mirrors test_rbac_router.py pattern).
# ============================================================================


@pytest.fixture
def app_client(
    settings: Settings,
    engine: Any,  # type: ignore[no-any-unimported]
    session_factory: Any,  # type: ignore[no-any-unimported]
) -> Iterator[TestClient]:
    """TestClient against a real app + real engine/session_factory.

    Same shape as ``test_rbac_router.py::app_client``.
    """
    from admin_backend.auth.stub import StubAuthClient

    app_obj = create_app()
    app_obj.state.settings = settings
    app_obj.state.engine = engine
    app_obj.state.session_factory = session_factory
    app_obj.state.auth_client = StubAuthClient(settings)
    with TestClient(app_obj) as client:
        yield client


def _tenant_jwt(settings: Settings, tenant_id: UUID) -> str:
    return make_test_jwt(
        settings,
        user_id=uuid.uuid4(),
        user_type="TENANT",
        tenant_id=tenant_id,
    )


def _auth(jwt: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {jwt}"}


@pytest_asyncio.fixture
async def platform_admin_jwt(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> str:
    """JWT for the seeded Devon (PLATFORM_ADMIN role).

    PLATFORM_ADMIN holds ``ADMIN.TENANTS.CONFIGURE.GLOBAL`` and friends
    but NOT ``ADMIN.ROLES.OVERRIDE.GLOBAL``. Used by W15 to verify the
    gate denies a platform-side caller that lacks the OVERRIDE grant.
    """
    schema = get_settings().db_schema
    async for session in get_tenant_session(
        platform_auth, session_factory
    ):
        result = await session.execute(
            text(
                f"SELECT id FROM {schema}.platform_users "
                "WHERE email = :email"
            ),
            {"email": "devon@ithina.ai"},
        )
        row = result.first()
    if row is None:
        raise LookupError(
            "Seed user 'devon@ithina.ai' not found. "
            "Re-run: uv run python -m scripts.seed_dev_data --reset"
        )
    devon_id = uuid.UUID(str(row[0]))
    return make_test_jwt(
        settings,
        user_id=devon_id,
        user_type="PLATFORM",
    )


@pytest_asyncio.fixture
async def override_permission_id(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> UUID:
    """Resolve the ADMIN.ROLES.OVERRIDE.GLOBAL permission id (seeded
    at Step 6.18.1).

    Cached per-test via fixture scoping; no DB writes.
    """
    schema = get_settings().db_schema
    async for session in get_tenant_session(
        platform_auth, session_factory
    ):
        result = await session.execute(
            text(
                f"SELECT id FROM {schema}.permissions "
                "WHERE code = :code"
            ),
            {"code": "ADMIN.ROLES.OVERRIDE.GLOBAL"},
        )
        row = result.first()
    if row is None:
        raise LookupError(
            "Permission ADMIN.ROLES.OVERRIDE.GLOBAL not found. "
            "Step 6.18.1 seed delta missing."
        )
    return uuid.UUID(str(row[0]))


@pytest_asyncio.fixture
async def cleanup_role_perms_for_roles(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> AsyncIterator[list[UUID]]:
    """Cleanup fixture: tracks role_ids and DELETEs ALL
    ``role_permissions`` for those roles at teardown.

    PATCH on a role can INSERT role_permissions rows that no test
    fixture tracks. The standard make_role + make_permission +
    make_role_permission teardown chain would then fail FK on the
    permission DELETE (role_permissions still references the perm).
    This fixture clears the cascade by purging junctions for tracked
    roles BEFORE the permission / role teardowns run.

    Listed LAST in the test signature so pytest LIFO teardown runs
    this BEFORE make_role_permission / make_permission / make_role
    teardowns.
    """
    tracked: list[UUID] = []
    yield tracked
    if tracked:
        schema = get_settings().db_schema
        async for s in get_tenant_session(platform_auth, session_factory):
            await s.execute(
                text(
                    f"DELETE FROM {schema}.role_permissions "
                    "WHERE role_id = ANY(:ids)"
                ),
                {"ids": tracked},
            )


@pytest_asyncio.fixture
async def seeded_super_admin_role_id(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> UUID:
    """Resolve the seeded SUPER_ADMIN role id.

    Read-only; no teardown.
    """
    schema = get_settings().db_schema
    async for session in get_tenant_session(
        platform_auth, session_factory
    ):
        result = await session.execute(
            text(f"SELECT id FROM {schema}.roles WHERE code = 'SUPER_ADMIN'")
        )
        row = result.first()
    if row is None:
        raise LookupError("SUPER_ADMIN role not in seed.")
    return uuid.UUID(str(row[0]))


# ============================================================================
# Happy-path: name + description + permission diff (W1-W5)
# ============================================================================


# ---- W1: PATCH name only (LOAD-BEARING) ----------------------------------
async def test_w1_patch_name_only_returns_200(
    app_client, super_admin_jwt, make_role,
):
    role = await make_role(audience="TENANT", name="W1 Original")
    resp = app_client.patch(
        f"/api/v1/roles/{role.id}",
        json={"name": "W1 Renamed"},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "W1 Renamed"
    assert body["id"] == str(role.id)
    assert body["code"] == role.code


# ---- W2: PATCH description only ------------------------------------------
async def test_w2_patch_description_only_returns_200(
    app_client, super_admin_jwt, make_role,
):
    role = await make_role(audience="TENANT", description="orig")
    resp = app_client.patch(
        f"/api/v1/roles/{role.id}",
        json={"description": "patched description"},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["description"] == "patched description"


# ---- W3: PATCH adds a permission (LOAD-BEARING) -------------------------
async def test_w3_patch_adds_permission_returns_200(
    app_client, super_admin_jwt, make_role, make_permission,
    make_role_permission,
    session_factory, platform_auth,
    cleanup_role_perms_for_roles,
):
    """Adding a permission via permission_ids inserts a new
    role_permissions row with the audit-actor pair (Pattern (b)
    per D-13 / LD14).
    """
    role = await make_role(audience="TENANT", name="W3 Role")
    cleanup_role_perms_for_roles.append(role.id)
    p1 = await make_permission(
        module="ADMIN", resource="STORES", action="EXECUTE", scope="STORE",
    )
    p2 = await make_permission(
        module="ADMIN", resource="STORES", action="AUDIT", scope="STORE",
    )
    await make_role_permission(role_id=role.id, permission_id=p1.id)

    # PATCH to (p1, p2): keep p1, add p2.
    resp = app_client.patch(
        f"/api/v1/roles/{role.id}",
        json={"permission_ids": [str(p1.id), str(p2.id)]},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200, resp.text
    perm_ids = {p["id"] for p in resp.json()["permissions"]}
    assert {str(p1.id), str(p2.id)} == perm_ids

    # Verify audit-actor populated on the newly added row.
    schema = get_settings().db_schema
    async for s in get_tenant_session(platform_auth, session_factory):
        result = await s.execute(
            text(
                f"SELECT created_by_user_id, created_by_user_type "
                f"FROM {schema}.role_permissions "
                "WHERE role_id = :rid AND permission_id = :pid"
            ),
            {"rid": role.id, "pid": p2.id},
        )
        row = result.first()
    assert row is not None
    assert row[0] is not None
    assert row[1] is not None


# ---- W4: PATCH removes a permission (LOAD-BEARING) ----------------------
async def test_w4_patch_removes_permission_returns_200(
    app_client, super_admin_jwt, make_role, make_permission,
    make_role_permission, cleanup_role_perms_for_roles,
):
    role = await make_role(audience="TENANT", name="W4 Role")
    cleanup_role_perms_for_roles.append(role.id)
    p1 = await make_permission(
        module="ADMIN", resource="STORES", action="EXECUTE", scope="STORE",
    )
    p2 = await make_permission(
        module="ADMIN", resource="STORES", action="AUDIT", scope="STORE",
    )
    await make_role_permission(role_id=role.id, permission_id=p1.id)
    await make_role_permission(role_id=role.id, permission_id=p2.id)

    # PATCH to (p1) only: remove p2.
    resp = app_client.patch(
        f"/api/v1/roles/{role.id}",
        json={"permission_ids": [str(p1.id)]},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200, resp.text
    perm_ids = {p["id"] for p in resp.json()["permissions"]}
    assert perm_ids == {str(p1.id)}


# ---- W5: PATCH replace-set with mixed add+remove+unchanged (LOAD-BEARING)
async def test_w5_patch_mixed_diff_preserves_audit_for_unchanged(
    app_client, super_admin_jwt, make_role, make_permission,
    make_role_permission,
    session_factory, platform_auth,
    cleanup_role_perms_for_roles,
):
    """Mixed diff: keep p1 (unchanged), remove p2, add p3.

    Verifies the diff-replace contract (LD5):
      - Rows in (current ∩ new) are not touched (created_at preserved).
      - Rows in (current - new) are DELETEd.
      - Rows in (new - current) are INSERTed with audit-actor pair.
    """
    role = await make_role(audience="TENANT", name="W5 Role")
    cleanup_role_perms_for_roles.append(role.id)
    p1 = await make_permission(
        module="ADMIN", resource="STORES", action="EXECUTE", scope="STORE",
    )
    p2 = await make_permission(
        module="ADMIN", resource="STORES", action="AUDIT", scope="STORE",
    )
    p3 = await make_permission(
        module="ADMIN", resource="ORG_NODES", action="EXECUTE",
        scope="STORE",
    )
    await make_role_permission(role_id=role.id, permission_id=p1.id)
    await make_role_permission(role_id=role.id, permission_id=p2.id)

    # Capture p1's created_at BEFORE patch.
    schema = get_settings().db_schema
    async for s in get_tenant_session(platform_auth, session_factory):
        result = await s.execute(
            text(
                f"SELECT created_at FROM {schema}.role_permissions "
                "WHERE role_id = :rid AND permission_id = :pid"
            ),
            {"rid": role.id, "pid": p1.id},
        )
        p1_created_at_before = result.scalar_one()

    # PATCH to (p1, p3): keep p1, remove p2, add p3.
    resp = app_client.patch(
        f"/api/v1/roles/{role.id}",
        json={"permission_ids": [str(p1.id), str(p3.id)]},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200, resp.text
    perm_ids = {p["id"] for p in resp.json()["permissions"]}
    assert perm_ids == {str(p1.id), str(p3.id)}

    # Verify p1's created_at is unchanged (diff-replace preservation).
    async for s in get_tenant_session(platform_auth, session_factory):
        result = await s.execute(
            text(
                f"SELECT created_at FROM {schema}.role_permissions "
                "WHERE role_id = :rid AND permission_id = :pid"
            ),
            {"rid": role.id, "pid": p1.id},
        )
        p1_created_at_after = result.scalar_one()
    assert p1_created_at_before == p1_created_at_after


# ============================================================================
# Empty body + forbidden-field rejection (W6-W10)
# ============================================================================


# ---- W6: empty body -> 422 EMPTY_PATCH -----------------------------------
async def test_w6_empty_body_returns_422_empty_patch(
    app_client, super_admin_jwt, make_role,
):
    role = await make_role(audience="TENANT", name="W6 Role")
    resp = app_client.patch(
        f"/api/v1/roles/{role.id}",
        json={},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "EMPTY_PATCH"


@pytest.mark.parametrize("forbidden_field", ["audience", "code", "is_system", "status"])
async def test_w7_w10_forbidden_fields_return_422(
    app_client, super_admin_jwt, make_role, forbidden_field,
):
    """W7-W10 — Pydantic ``extra='forbid'`` rejects every forbidden
    field with 422. LD19; mirrors stores PATCH RP5-RP7 precedent.
    """
    role = await make_role(audience="TENANT", name="W7-W10 Role")
    body = {forbidden_field: "anything"}
    resp = app_client.patch(
        f"/api/v1/roles/{role.id}",
        json=body,
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 422, resp.text


# ============================================================================
# State + lookup errors (W11, W12)
# ============================================================================


# ---- W11: ARCHIVED role -> 409 ROLE_ARCHIVED (LOAD-BEARING) -------------
async def test_w11_archived_role_returns_409_role_archived(
    app_client, super_admin_jwt, make_role,
    session_factory, platform_auth,
):
    """ARCHIVED state rejection (LD3).

    make_role doesn't support status='ARCHIVED' directly (the DDL
    ck_roles_archived_consistency requires archived_at + archived_by_*
    co-set). The test creates an ACTIVE role, then directly UPDATEs it
    to ARCHIVED with the archived_* triplet populated, then PATCHes.
    """
    role = await make_role(audience="TENANT", name="W11 Role")

    # Manually flip to ARCHIVED with the archived triplet (DDL CHECK
    # ck_roles_archived_consistency requires all three).
    schema = get_settings().db_schema
    async for s in get_tenant_session(platform_auth, session_factory):
        await s.execute(
            text(
                f"UPDATE {schema}.roles SET "
                f"  status = CAST('ARCHIVED' AS {schema}.role_status_enum), "
                "  archived_at = NOW(), "
                "  archived_by_user_id = gen_random_uuid(), "
                f"  archived_by_user_type = CAST('PLATFORM' AS {schema}.actor_user_type_enum) "
                "WHERE id = :rid"
            ),
            {"rid": role.id},
        )

    resp = app_client.patch(
        f"/api/v1/roles/{role.id}",
        json={"name": "should-be-rejected"},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == "ROLE_ARCHIVED"


# ---- W12: unknown role_id -> 404 ROLE_NOT_FOUND --------------------------
def test_w12_unknown_role_returns_404(app_client, super_admin_jwt):
    fake = uuid.uuid4()
    resp = app_client.patch(
        f"/api/v1/roles/{fake}",
        json={"name": "x"},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 404
    assert resp.json()["code"] == "ROLE_NOT_FOUND"


# ============================================================================
# OVERRIDE.GLOBAL invariant (W13, W14)
# ============================================================================


# ---- W13: last-holder edit -> 409 LAST_OVERRIDE_HOLDER (LOAD-BEARING) ---
async def test_w13_last_override_holder_returns_409(
    monkeypatch, app_client, super_admin_jwt,
    make_role, make_role_permission, override_permission_id,
    cleanup_role_perms_for_roles,
):
    """LOAD-BEARING (security-critical): Layer 1 invariant blocks an
    edit that would zero out OVERRIDE.GLOBAL active holders.

    Setup: create a test role X with OVERRIDE.GLOBAL grant. Patch the
    invariant counter so that excluding X from the holder set leaves
    zero active holders (simulating "X is the last bridge to platform
    admin"). PATCH X removing OVERRIDE -> expect 409.
    """
    role = await make_role(audience="PLATFORM", name="W13 Role")
    cleanup_role_perms_for_roles.append(role.id)
    await make_role_permission(
        role_id=role.id, permission_id=override_permission_id,
    )

    async def fake_count(session, *, exclude_role_id):
        # Layer 1 passes exclude_role_id=role.id; simulate zero.
        # Layer 2 should not run (Layer 1 raises first), but if it does
        # somehow, we want a non-zero so it doesn't mask the test.
        if exclude_role_id == role.id:
            return 0
        return 1

    monkeypatch.setattr(
        "admin_backend.repositories.roles."
        "_count_override_global_active_holders",
        fake_count,
    )

    resp = app_client.patch(
        f"/api/v1/roles/{role.id}",
        json={"permission_ids": []},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == "LAST_OVERRIDE_HOLDER"


# ---- W14: invariant satisfied via other holder -> 200 (LOAD-BEARING) ---
async def test_w14_invariant_satisfied_via_other_holder_returns_200(
    app_client, super_admin_jwt,
    make_role, make_role_permission, override_permission_id,
    cleanup_role_perms_for_roles,
):
    """LOAD-BEARING: edit removes OVERRIDE.GLOBAL from a role while
    another role still has an active holder. Layer 1 returns
    non-zero; invariant satisfied; PATCH succeeds.

    Real seed state has 3 ACTIVE holders of OVERRIDE.GLOBAL through
    SUPER_ADMIN (anjali, devon-no-wait-devon-is-PLATFORM_ADMIN-not-
    SUPER_ADMIN: re-check the seed). Either way, SUPER_ADMIN's holders
    persist when our test role removes OVERRIDE.GLOBAL.
    """
    role = await make_role(audience="PLATFORM", name="W14 Role")
    cleanup_role_perms_for_roles.append(role.id)
    await make_role_permission(
        role_id=role.id, permission_id=override_permission_id,
    )

    # No mock — relies on real seed state where SUPER_ADMIN holders
    # exist independently.
    resp = app_client.patch(
        f"/api/v1/roles/{role.id}",
        json={"permission_ids": []},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["permissions"] == []


# ============================================================================
# Gate enforcement (W15, W16, W17)
# ============================================================================


# ---- W15: PLATFORM_ADMIN (no OVERRIDE.GLOBAL) -> 403 (LOAD-BEARING) -----
async def test_w15_platform_admin_returns_403_permission_denied(
    app_client, platform_admin_jwt, make_role,
):
    """LOAD-BEARING: a PLATFORM caller without the OVERRIDE.GLOBAL
    grant is refused by Layer 2 (has_permission) of the gate.

    The gate is ADMIN.ROLES.OVERRIDE.GLOBAL + audience='PLATFORM'.
    PLATFORM_ADMIN passes Layer 1 (audience match) but fails Layer 2
    (Devon's PLATFORM_ADMIN role does not hold OVERRIDE.GLOBAL).
    """
    role = await make_role(audience="TENANT", name="W15 Role")
    resp = app_client.patch(
        f"/api/v1/roles/{role.id}",
        json={"name": "x"},
        headers=_auth(platform_admin_jwt),
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "PERMISSION_DENIED"


# ---- W16: TENANT JWT -> 403 PLATFORM_AUDIENCE_REQUIRED (LOAD-BEARING) --
async def test_w16_tenant_jwt_returns_403_platform_audience_required(
    app_client, settings, make_tenant, make_role,
):
    """LOAD-BEARING: TENANT caller is refused at Layer 1 (audience
    gate) before Layer 2 runs.
    """
    tenant = await make_tenant(name="W16-T")
    role = await make_role(audience="TENANT", name="W16 Role")
    tjwt = _tenant_jwt(settings, tenant.id)
    resp = app_client.patch(
        f"/api/v1/roles/{role.id}",
        json={"name": "x"},
        headers=_auth(tjwt),
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "PLATFORM_AUDIENCE_REQUIRED"


# ---- W17: no JWT -> 401 --------------------------------------------------
def test_w17_no_jwt_returns_401(app_client):
    fake = uuid.uuid4()
    resp = app_client.patch(
        f"/api/v1/roles/{fake}",
        json={"name": "x"},
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == "AUTH_MISSING"


# ============================================================================
# Layer 2 tripwire (W18)
# ============================================================================


# ---- W18: synthetic Layer 1 / Layer 2 mismatch -> 500 (LOAD-BEARING) ---
async def test_w18_layer_2_tripwire_returns_500_on_synthetic_mismatch(
    monkeypatch, app_client, super_admin_jwt,
    make_role, make_role_permission, override_permission_id,
    cleanup_role_perms_for_roles,
):
    """LOAD-BEARING (security-critical): if Layer 1 says safe but the
    post-write state is unsafe, Layer 2 fires
    InternalInvariantViolationError -> 500 INTERNAL_ERROR + ROLLBACK.

    Synthetic mismatch via monkeypatch: Layer 1 (exclude_role_id !=
    None) returns 1 (pass); Layer 2 (exclude_role_id == None) returns
    0 (fail).
    """
    role = await make_role(audience="PLATFORM", name="W18 Role")
    cleanup_role_perms_for_roles.append(role.id)
    await make_role_permission(
        role_id=role.id, permission_id=override_permission_id,
    )

    async def fake_count(session, *, exclude_role_id):
        if exclude_role_id is not None:
            return 1  # Layer 1: pass
        return 0  # Layer 2: fail (synthetic)

    monkeypatch.setattr(
        "admin_backend.repositories.roles."
        "_count_override_global_active_holders",
        fake_count,
    )

    resp = app_client.patch(
        f"/api/v1/roles/{role.id}",
        json={"permission_ids": []},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 500
    # Anti-information-disclosure: ServerError emits generic
    # INTERNAL_ERROR (not the class name).
    assert resp.json()["code"] == "INTERNAL_ERROR"


# ============================================================================
# Mandatory-gate-discipline marker (W19)
# ============================================================================


def test_w19_patch_role_carries_permission_gate_marker(app_client):
    """LOAD-BEARING: the new PATCH endpoint registers a
    ``__permission_gate__`` marker on one of its dependencies with
    audience='PLATFORM'. Mirrors
    test_gate_discipline_platform_only_writes_declare_audience but
    asserts directly for the new route so a regression on THIS
    endpoint surfaces in this file too.
    """
    from fastapi.routing import APIRoute

    matched = None
    for route in app_client.app.routes:
        if not isinstance(route, APIRoute):
            continue
        if route.path == "/api/v1/roles/{role_id}" and "PATCH" in route.methods:
            matched = route
            break
    assert matched is not None, "PATCH /api/v1/roles/{role_id} route not registered"

    marker = None
    for dep in matched.dependant.dependencies:
        marker = getattr(dep.call, "__permission_gate__", None)
        if marker is not None:
            break
    assert marker is not None, "PATCH /roles/{role_id} has no gate marker"
    assert marker.audience == "PLATFORM"


# ============================================================================
# Audit-actor population + diff-replace audit preservation (W20, W21)
# ============================================================================


# ---- W20: audit-actor populated on UPDATE and INSERT (LOAD-BEARING) ----
async def test_w20_audit_actor_populated_on_update_and_insert(
    app_client, super_admin_jwt, make_role, make_permission,
    session_factory, platform_auth,
    cleanup_role_perms_for_roles,
):
    """LOAD-BEARING: PATCH populates updated_by_user_id +
    updated_by_user_type on the role row, and created_by_user_id +
    created_by_user_type on every new role_permissions row (Pattern
    (b) per D-13 / LD14, LD15).
    """
    role = await make_role(audience="TENANT", name="W20 Role")
    cleanup_role_perms_for_roles.append(role.id)
    p1 = await make_permission(
        module="ADMIN", resource="STORES", action="EXECUTE", scope="STORE",
    )

    resp = app_client.patch(
        f"/api/v1/roles/{role.id}",
        json={
            "name": "W20 Renamed",
            "permission_ids": [str(p1.id)],
        },
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200, resp.text

    schema = get_settings().db_schema
    async for s in get_tenant_session(platform_auth, session_factory):
        # Role row: updated_by_* populated.
        role_row = await s.execute(
            text(
                f"SELECT updated_by_user_id, updated_by_user_type "
                f"FROM {schema}.roles WHERE id = :rid"
            ),
            {"rid": role.id},
        )
        r_audit = role_row.first()
        # role_permissions row: created_by_* populated.
        rp_row = await s.execute(
            text(
                f"SELECT created_by_user_id, created_by_user_type "
                f"FROM {schema}.role_permissions "
                "WHERE role_id = :rid AND permission_id = :pid"
            ),
            {"rid": role.id, "pid": p1.id},
        )
        rp_audit = rp_row.first()

    assert r_audit is not None
    assert r_audit[0] is not None and r_audit[1] is not None
    assert rp_audit is not None
    assert rp_audit[0] is not None and rp_audit[1] is not None


# ---- W21: diff-replace preserves audit (LOAD-BEARING) -------------------
# Already covered by W5's created_at assertion; W21 here doubles as a
# stronger contract assertion (created_by_* also unchanged).
async def test_w21_diff_replace_preserves_audit_on_unchanged_rows(
    app_client, super_admin_jwt, make_role, make_permission,
    make_role_permission,
    session_factory, platform_auth,
    cleanup_role_perms_for_roles,
):
    """LOAD-BEARING: diff-replace MUST NOT touch (current ∩ new) rows.

    Verified by capturing both ``created_at`` and ``created_by_*``
    before PATCH and asserting they're byte-identical after PATCH (the
    row was never DELETEd + re-INSERTed).
    """
    role = await make_role(audience="TENANT", name="W21 Role")
    cleanup_role_perms_for_roles.append(role.id)
    p_keep = await make_permission(
        module="ADMIN", resource="STORES", action="EXECUTE", scope="STORE",
    )
    p_remove = await make_permission(
        module="ADMIN", resource="STORES", action="AUDIT", scope="STORE",
    )
    p_add = await make_permission(
        module="ADMIN", resource="ORG_NODES", action="EXECUTE",
        scope="STORE",
    )
    await make_role_permission(role_id=role.id, permission_id=p_keep.id)
    await make_role_permission(role_id=role.id, permission_id=p_remove.id)

    schema = get_settings().db_schema
    async for s in get_tenant_session(platform_auth, session_factory):
        before = (await s.execute(
            text(
                f"SELECT created_at, created_by_user_id, "
                f"created_by_user_type FROM {schema}.role_permissions "
                "WHERE role_id = :rid AND permission_id = :pid"
            ),
            {"rid": role.id, "pid": p_keep.id},
        )).first()

    resp = app_client.patch(
        f"/api/v1/roles/{role.id}",
        json={"permission_ids": [str(p_keep.id), str(p_add.id)]},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200, resp.text

    async for s in get_tenant_session(platform_auth, session_factory):
        after = (await s.execute(
            text(
                f"SELECT created_at, created_by_user_id, "
                f"created_by_user_type FROM {schema}.role_permissions "
                "WHERE role_id = :rid AND permission_id = :pid"
            ),
            {"rid": role.id, "pid": p_keep.id},
        )).first()

    assert before == after


# ============================================================================
# Audience-scope coherence (W22, W23, W24)
# ============================================================================


# ---- W22: TENANT role + GLOBAL add -> 422 (LOAD-BEARING) ----------------
async def test_w22_tenant_role_global_perm_returns_422(
    app_client, super_admin_jwt, make_role, make_permission,
):
    """LOAD-BEARING (LD10): TENANT-audience role cannot have a
    GLOBAL-scope permission added.
    """
    role = await make_role(audience="TENANT", name="W22 Role")
    p_global = await make_permission(
        module="ADMIN", resource="STORES", action="OVERRIDE", scope="GLOBAL",
    )
    resp = app_client.patch(
        f"/api/v1/roles/{role.id}",
        json={"permission_ids": [str(p_global.id)]},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "AUDIENCE_SCOPE_MISMATCH"


# ---- W23: TENANT role + TENANT/STORE perms -> 200 (LOAD-BEARING) -------
async def test_w23_tenant_role_with_non_global_returns_200(
    app_client, super_admin_jwt, make_role, make_permission,
    cleanup_role_perms_for_roles,
):
    role = await make_role(audience="TENANT", name="W23 Role")
    cleanup_role_perms_for_roles.append(role.id)
    # Use unseeded tuples (CONFIGURE.TENANT / EXECUTE.STORE on STORES
    # are seeded; ORG_NODES.AUDIT.* and APPROVE.* are unseeded).
    p_tenant = await make_permission(
        module="ADMIN", resource="ORG_NODES", action="AUDIT",
        scope="TENANT",
    )
    p_store = await make_permission(
        module="ADMIN", resource="ORG_NODES", action="APPROVE",
        scope="STORE",
    )
    resp = app_client.patch(
        f"/api/v1/roles/{role.id}",
        json={"permission_ids": [str(p_tenant.id), str(p_store.id)]},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200, resp.text


# ---- W24: PLATFORM role + GLOBAL add -> 200 (LOAD-BEARING) -------------
async def test_w24_platform_role_with_global_returns_200(
    app_client, super_admin_jwt, make_role, make_permission,
    cleanup_role_perms_for_roles,
):
    """LOAD-BEARING: PLATFORM-audience roles can hold GLOBAL-scope
    permissions (no scope filter applied per LD2).
    """
    role = await make_role(audience="PLATFORM", name="W24 Role")
    cleanup_role_perms_for_roles.append(role.id)
    p_global = await make_permission(
        module="ADMIN", resource="STORES", action="OVERRIDE", scope="GLOBAL",
    )
    resp = app_client.patch(
        f"/api/v1/roles/{role.id}",
        json={"permission_ids": [str(p_global.id)]},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200, resp.text


# ============================================================================
# permission_ids edge cases (W25-W28)
# ============================================================================


# ---- W25: empty permission_ids = legitimate "remove all" ---------------
async def test_w25_empty_permission_ids_returns_200(
    app_client, super_admin_jwt, make_role, make_permission,
    make_role_permission, cleanup_role_perms_for_roles,
):
    role = await make_role(audience="TENANT", name="W25 Role")
    cleanup_role_perms_for_roles.append(role.id)
    p1 = await make_permission(
        module="ADMIN", resource="STORES", action="EXECUTE", scope="STORE",
    )
    await make_role_permission(role_id=role.id, permission_id=p1.id)
    resp = app_client.patch(
        f"/api/v1/roles/{role.id}",
        json={"permission_ids": []},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["permissions"] == []


# ---- W26: one non-existent permission_id -> 422 (LOAD-BEARING) ---------
async def test_w26_unknown_permission_id_returns_422(
    app_client, super_admin_jwt, make_role,
):
    role = await make_role(audience="TENANT", name="W26 Role")
    fake = uuid.uuid4()
    resp = app_client.patch(
        f"/api/v1/roles/{role.id}",
        json={"permission_ids": [str(fake)]},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "INVALID_PERMISSION_ID"


# ---- W27: all-unknown permission_ids -> 422 ----------------------------
async def test_w27_all_unknown_permission_ids_returns_422(
    app_client, super_admin_jwt, make_role,
):
    role = await make_role(audience="TENANT", name="W27 Role")
    fakes = [str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())]
    resp = app_client.patch(
        f"/api/v1/roles/{role.id}",
        json={"permission_ids": fakes},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "INVALID_PERMISSION_ID"


# ---- W28: permission_ids matching current set (no diff) ----------------
async def test_w28_no_diff_permission_ids_returns_200(
    app_client, super_admin_jwt, make_role, make_permission,
    make_role_permission, cleanup_role_perms_for_roles,
):
    role = await make_role(audience="TENANT", name="W28 Role")
    cleanup_role_perms_for_roles.append(role.id)
    p1 = await make_permission(
        module="ADMIN", resource="STORES", action="EXECUTE", scope="STORE",
    )
    await make_role_permission(role_id=role.id, permission_id=p1.id)
    resp = app_client.patch(
        f"/api/v1/roles/{role.id}",
        json={"permission_ids": [str(p1.id)]},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200, resp.text
    assert {p["id"] for p in resp.json()["permissions"]} == {str(p1.id)}


# ============================================================================
# SUPER_ADMIN protection (W29, W30)
# ============================================================================


# ---- W29: SUPER_ADMIN protected -> 409 (LOAD-BEARING) ------------------
async def test_w29_super_admin_returns_409_super_admin_protected(
    app_client, super_admin_jwt, seeded_super_admin_role_id,
):
    """LOAD-BEARING (v0 lockout): PATCH on SUPER_ADMIN is refused
    regardless of body content (LD12).
    """
    resp = app_client.patch(
        f"/api/v1/roles/{seeded_super_admin_role_id}",
        json={"name": "Patched Super Admin"},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == "SUPER_ADMIN_PROTECTED"


# ---- W30: other PLATFORM roles editable -> 200 (LOAD-BEARING) ----------
async def test_w30_other_platform_role_editable_returns_200(
    app_client, super_admin_jwt, make_role,
):
    """LOAD-BEARING: SUPER_ADMIN protection (LD12) only blocks the
    SUPER_ADMIN role itself; other PLATFORM-audience roles remain
    editable.
    """
    role = await make_role(audience="PLATFORM", name="W30 Test Role")
    resp = app_client.patch(
        f"/api/v1/roles/{role.id}",
        json={"name": "W30 Patched"},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "W30 Patched"
