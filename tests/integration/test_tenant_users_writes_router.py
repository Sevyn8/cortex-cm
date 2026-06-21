"""Integration tests for the tenant-users write endpoints (Step 6.10.1).

Coverage shape:

  C1-C9:  POST /tenant-users  (create + bundled role assignments)
  P1-P12: PATCH /tenant-users/{user_id}
  S1-S5:  POST /tenant-users/{user_id}/suspend
  A1-A5:  POST /tenant-users/{user_id}/activate

Five LOAD-BEARING regression tests:
  - C7:  POST with PLATFORM-audience role in body -> 422
         INVALID_ROLE_AUDIENCE. Without the validator pre-check, the
         enforce_tenant_role_audience trigger's plpgsql RAISE would
         surface as 500.
  - P3:  PATCH self-edit by TENANT caller -> 403 SELF_EDIT_FORBIDDEN.
         Primary self-edit guard case.
  - P5:  PATCH cross-tenant -> 404 (RLS-as-404 per D-17), not 403,
         so the existence of another tenant's user_id is not disclosed.
  - S4:  Suspend INVITED -> 409 INVALID_STATE_TRANSITION. Maps the DDL
         ck_tenant_users_auth0_sub_consistency reject to a clean 409.
  - C3:  POST cross-tenant tenant_id from TENANT JWT -> 404
         TENANT_NOT_FOUND. RLS-as-404 on the tenant-root anchor lookup.

Cleanup. ``cleanup_tenant_users_router`` tracks tenant_user IDs
created via POST and DELETEs them at teardown (assignments first per
the composite FK ON DELETE RESTRICT, then the tenant_users row). The
TestClient session is request-scoped (FastAPI commits per request)
so cleanup sees committed rows immediately at teardown. Fixture-order
discipline: list this AFTER make_* row-creating fixtures, BEFORE the
session-yielding fixture per CLAUDE.md's cleanup-fixture-ordering
note.

JWT decoding helper: ``_user_id_from_jwt`` extracts the user_id claim
without signature verification. The synthetic-user factory
(``tenant_owner_jwt_factory``) doesn't return user_id directly; the
helper lets tests assert self-edit guards without extending the
shared conftest factory's contract.
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any
from uuid import UUID

import jwt as pyjwt
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


@pytest.fixture
def app_client(
    settings: Settings,
    engine: Any,
    session_factory: Any,
) -> Iterator[TestClient]:
    """TestClient with engine + session_factory wired onto app.state.

    Mirrors test_tenants_writes_router.py. Bypasses the lifespan so
    the test event loop owns the engine.
    """
    from admin_backend.auth.stub import StubAuthClient

    app_obj = create_app()
    app_obj.state.settings = settings
    app_obj.state.engine = engine
    app_obj.state.session_factory = session_factory
    app_obj.state.auth_client = StubAuthClient(settings)
    with TestClient(app_obj) as client:
        yield client


@pytest_asyncio.fixture
async def cleanup_tenant_users_router(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> AsyncIterator[list[UUID]]:
    """Tracks tenant_user IDs created via POST /api/v1/tenant-users.

    Teardown DELETEs audit rows first (FK ON DELETE RESTRICT on
    tenant_activity_audit_logs.tenant_id targets tenants(id) and
    won't block on user delete, but the row's ``resource_id``
    column points at the user; we clean both tenant_ and platform_
    tables defensively for symmetry with potential routing).
    Then assignments (composite FK ON DELETE RESTRICT on
    tenant_user_role_assignments.tenant_user_id), then tenant_users
    rows. Fixture-order discipline: list this AFTER any make_*
    fixtures so their teardown (which clears FK references) fires
    AFTER this cleanup (LIFO).

    Step 6.16.4 LD18 extension : audit-row DELETE precedes the
    assignments+users DELETE. The Pattern (b) ``resource_id`` /
    ``actor_user_id`` columns carry no FK to tenant_users so the
    tenant_users DELETE itself isn't blocked by audit rows; this
    cleanup keeps the test database tidy across runs and avoids
    cross-test bleed when later tests query audit rows by tenant_id.
    """
    schema = get_settings().db_schema
    created: list[UUID] = []
    yield created

    if created:
        async for session in get_tenant_session(
            platform_auth, session_factory
        ):
            # Audit rows: clean from both tables. The Pattern (b)
            # actor_user_id / resource_id columns are bare UUIDs (no
            # FK to user tables) so the table-level DELETE doesn't
            # cascade; we filter on resource_id (the user-side audit
            # subject) AND actor_user_id (in case the test's user
            # was also the actor, e.g., self-edit denials).
            await session.execute(
                text(
                    f"DELETE FROM {schema}.tenant_activity_audit_logs "
                    "WHERE resource_id = ANY(:ids) "
                    "OR actor_user_id = ANY(:ids)"
                ),
                {"ids": created},
            )
            await session.execute(
                text(
                    f"DELETE FROM {schema}.platform_activity_audit_logs "
                    "WHERE resource_id = ANY(:ids) "
                    "OR actor_user_id = ANY(:ids)"
                ),
                {"ids": created},
            )
            await session.execute(
                text(
                    f"DELETE FROM {schema}.tenant_user_role_assignments "
                    "WHERE tenant_user_id = ANY(:ids)"
                ),
                {"ids": created},
            )
            await session.execute(
                text(
                    f"DELETE FROM {schema}.tenant_users WHERE id = ANY(:ids)"
                ),
                {"ids": created},
            )


def _auth(jwt_str: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {jwt_str}"}


def _user_id_from_jwt(token: str) -> UUID:
    """Extract the user_id claim from a (trusted) JWT.

    Used to assert self-edit guards: the synthetic-user factory
    ``tenant_owner_jwt_factory`` mints a JWT but doesn't surface the
    user_id. Decoding without signature verification is safe here —
    the test minted it; the helper is test-scoped only.
    """
    from admin_backend.auth.stub import CLAIM_USER_ID

    payload = pyjwt.decode(token, options={"verify_signature": False})
    return UUID(str(payload[CLAIM_USER_ID]))


async def _seed_tenant_with_root(
    make_tenant: Any,
    make_org_node: Any,
    *,
    name: str,
) -> tuple[UUID, UUID, str]:
    """Build (tenant_id, root_org_node_id, root_path) for a fresh tenant.

    Used by tests that need a tenant with a tenant-root org_node
    available (every POST /tenant-users path needs this because the
    repo bundles assignments anchored at the root).
    """
    tenant = await make_tenant(name=name)
    root_id, root_path = await make_org_node(
        tenant_id=tenant.id,
        node_type="TENANT",
        code=f"r-{uuid.uuid4().hex[:8]}",
        name=f"Root {name}",
    )
    return tenant.id, root_id, root_path


async def _lookup_permission_id(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
    *,
    module: str,
    resource: str,
    action: str,
    scope: str,
) -> UUID:
    """Look up a seeded permission row by its 4-tuple identity."""
    schema = get_settings().db_schema
    async for session in get_tenant_session(
        platform_auth, session_factory
    ):
        row = await session.execute(
            text(
                f"SELECT id FROM {schema}.permissions "
                f"WHERE module = CAST(:m AS {schema}.module_code_enum) "
                f"AND resource = CAST(:r AS {schema}.resource_enum) "
                f"AND action = CAST(:a AS {schema}.action_enum) "
                f"AND scope = CAST(:s AS {schema}.permission_scope_enum)"
            ),
            {"m": module, "r": resource, "a": action, "s": scope},
        )
        rec = row.first()
    if rec is None:
        raise LookupError(
            f"permission ({module}, {resource}, {action}, {scope}) not seeded"
        )
    return UUID(str(rec.id))


def _roles_payload(
    items: list[tuple[UUID, UUID]],
) -> list[dict[str, str]]:
    """Serialise (role_id, org_node_id) tuples to the Step 6.14 wire
    shape ``[{"role_id": "...", "org_node_id": "..."}]``."""
    return [
        {"role_id": str(rid), "org_node_id": str(oid)}
        for (rid, oid) in items
    ]


def _valid_create_body(
    *,
    tenant_id: UUID,
    role_assignments: list[tuple[UUID, UUID]],
    name_suffix: str | None = None,
) -> dict[str, Any]:
    """Minimal valid POST /tenant-users body for happy-path tests.

    Step 6.14 (vs 6.10.1): the body's ``roles`` field carries the new
    ``[{role_id, org_node_id}]`` shape. Tests pass the
    ``(role_id, org_node_id)`` tuples; this helper handles
    serialisation.
    """
    suffix = name_suffix or uuid.uuid4().hex[:8]
    return {
        "tenant_id": str(tenant_id),
        "email": f"user-{suffix}@test.example.com",
        "full_name": f"Test User {suffix}",
        "roles": _roles_payload(role_assignments),
    }


# ============================================================================
# POST /tenant-users (C1-C9)
# ============================================================================


async def test_c1_super_admin_create_returns_201_invited_with_roles(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_org_node,
    make_role,
    cleanup_tenant_users_router,
) -> None:
    """SUPER_ADMIN happy path: 201, status INVITED, roles[] populated."""
    tenant_id, root_id, _root_path = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="C1-Tenant"
    )
    role = await make_role(audience="TENANT")

    body = _valid_create_body(
        tenant_id=tenant_id,
        role_assignments=[(role.id, root_id)],
        name_suffix="c1",
    )
    resp = app_client.post(
        "/api/v1/tenant-users",
        json=body,
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 201, resp.text
    j = resp.json()
    cleanup_tenant_users_router.append(UUID(j["id"]))

    assert j["status"] == "INVITED"
    assert j["tenant_id"] == str(tenant_id)
    assert j["full_name"] == body["full_name"]
    assert j["email"] == body["email"]
    role_ids = {r["role_id"] for r in j["roles"]}
    assert str(role.id) in role_ids


async def test_c2_tenant_owner_create_own_tenant_returns_201(
    app_client,
    make_tenant,
    make_org_node,
    make_role,
    tenant_owner_jwt_factory,
    cleanup_tenant_users_router,
) -> None:
    """TENANT OWNER with CONFIGURE.TENANT grant: 201 on own tenant."""
    tenant_id, root_id, _root_path = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="C2-Tenant"
    )
    role = await make_role(audience="TENANT")
    owner_jwt = await tenant_owner_jwt_factory(
        tenant_id,
        with_grants=[("ADMIN", "USERS", "CONFIGURE", "TENANT")],
    )

    body = _valid_create_body(
        tenant_id=tenant_id,
        role_assignments=[(role.id, root_id)],
        name_suffix="c2",
    )
    resp = app_client.post(
        "/api/v1/tenant-users",
        json=body,
        headers=_auth(owner_jwt),
    )
    assert resp.status_code == 201, resp.text
    j = resp.json()
    cleanup_tenant_users_router.append(UUID(j["id"]))
    assert j["tenant_id"] == str(tenant_id)


async def test_c3_tenant_owner_cross_tenant_returns_404(
    app_client,
    make_tenant,
    make_org_node,
    make_role,
    tenant_owner_jwt_factory,
) -> None:
    """LOAD-BEARING — TENANT OWNER targeting another tenant -> 404
    TENANT_NOT_FOUND (RLS-as-404 on tenant-root anchor lookup).

    Without this guard, a regression making the tenant-root lookup
    visible across tenants would let TENANT-A's OWNER create users in
    TENANT-B (disaster-class cross-tenant write).
    """
    tenant_a_id, ra_id, _ra_path = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="C3-TenantA"
    )
    tenant_b_id, rb_id, _rb_path = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="C3-TenantB"
    )
    role_b = await make_role(audience="TENANT")
    owner_a_jwt = await tenant_owner_jwt_factory(
        tenant_a_id,
        with_grants=[("ADMIN", "USERS", "CONFIGURE", "TENANT")],
    )

    body = _valid_create_body(
        tenant_id=tenant_b_id,  # Cross-tenant!
        role_assignments=[(role_b.id, rb_id)],
        name_suffix="c3",
    )
    resp = app_client.post(
        "/api/v1/tenant-users",
        json=body,
        headers=_auth(owner_a_jwt),
    )
    # Anchor dep resolves on tenant_id from body? No — POST has no
    # path tenant_id, so the gate's anchor_dep is None. The repo's
    # tenant-root lookup returns None under RLS for the cross-tenant
    # target, surfacing as TENANT_NOT_FOUND.
    assert resp.status_code == 404, resp.text
    assert resp.json()["code"] == "TENANT_NOT_FOUND"


async def test_c4_duplicate_email_same_tenant_returns_409(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_org_node,
    make_role,
    cleanup_tenant_users_router,
) -> None:
    """Two POSTs with the same email in the same tenant -> 409
    DUPLICATE_TENANT_USER_EMAIL on the second."""
    tenant_id, root_id, _root_path = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="C4-Tenant"
    )
    role = await make_role(audience="TENANT")

    body = _valid_create_body(
        tenant_id=tenant_id,
        role_assignments=[(role.id, root_id)],
        name_suffix="c4",
    )
    r1 = app_client.post(
        "/api/v1/tenant-users",
        json=body,
        headers=_auth(super_admin_jwt),
    )
    assert r1.status_code == 201, r1.text
    cleanup_tenant_users_router.append(UUID(r1.json()["id"]))

    r2 = app_client.post(
        "/api/v1/tenant-users",
        json=body,
        headers=_auth(super_admin_jwt),
    )
    assert r2.status_code == 409, r2.text
    assert r2.json()["code"] == "DUPLICATE_TENANT_USER_EMAIL"


async def test_c5_same_email_different_tenants_returns_201_each(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_org_node,
    make_role,
    cleanup_tenant_users_router,
) -> None:
    """Same email in different tenants is allowed (per-tenant unique)."""
    tenant_a_id, ra_id, _ra_path = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="C5-TenantA"
    )
    tenant_b_id, rb_id, _rb_path = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="C5-TenantB"
    )
    role_a = await make_role(audience="TENANT")
    role_b = await make_role(audience="TENANT")

    shared_email = f"shared-{uuid.uuid4().hex[:8]}@test.example.com"

    body_a = _valid_create_body(
        tenant_id=tenant_a_id,
        role_assignments=[(role_a.id, ra_id)],
    )
    body_a["email"] = shared_email
    r_a = app_client.post(
        "/api/v1/tenant-users",
        json=body_a,
        headers=_auth(super_admin_jwt),
    )
    assert r_a.status_code == 201, r_a.text
    cleanup_tenant_users_router.append(UUID(r_a.json()["id"]))

    body_b = _valid_create_body(
        tenant_id=tenant_b_id,
        role_assignments=[(role_b.id, rb_id)],
    )
    body_b["email"] = shared_email
    r_b = app_client.post(
        "/api/v1/tenant-users",
        json=body_b,
        headers=_auth(super_admin_jwt),
    )
    assert r_b.status_code == 201, r_b.text
    cleanup_tenant_users_router.append(UUID(r_b.json()["id"]))


async def test_c6_email_uppercase_normalized_to_lowercase(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_org_node,
    make_role,
    cleanup_tenant_users_router,
) -> None:
    """Email with uppercase chars normalized to lowercase by validator."""
    tenant_id, root_id, _root_path = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="C6-Tenant"
    )
    role = await make_role(audience="TENANT")
    body = _valid_create_body(
        tenant_id=tenant_id,
        role_assignments=[(role.id, root_id)],
        name_suffix="c6",
    )
    suffix = uuid.uuid4().hex[:8]
    body["email"] = f"Mixed-Case-{suffix}@Test.Example.COM"

    resp = app_client.post(
        "/api/v1/tenant-users",
        json=body,
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 201, resp.text
    j = resp.json()
    cleanup_tenant_users_router.append(UUID(j["id"]))
    assert j["email"] == body["email"].lower()


async def test_c7_platform_audience_role_in_body_returns_422(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_org_node,
    make_role,
) -> None:
    """LOAD-BEARING — A PLATFORM-audience role in roles[] -> 422
    INVALID_ROLE_AUDIENCE.

    Without the handler-side pre-check, the DB trigger
    enforce_tenant_role_audience's plpgsql RAISE EXCEPTION would
    surface as 500. The 422 is the contract: caller's payload is
    invalid; tell them what's wrong.
    """
    tenant_id, root_id, _root_path = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="C7-Tenant"
    )
    platform_role = await make_role(audience="PLATFORM")
    body = _valid_create_body(
        tenant_id=tenant_id,
        role_assignments=[(platform_role.id, root_id)],
        name_suffix="c7",
    )
    resp = app_client.post(
        "/api/v1/tenant-users",
        json=body,
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "INVALID_ROLE_AUDIENCE"


async def test_c8_nonexistent_role_id_returns_422(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_org_node,
) -> None:
    """Unknown role_id -> 422 INVALID_ROLE (pre-check before INSERT)."""
    tenant_id, root_id, _root_path = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="C8-Tenant"
    )
    unknown_role_id = uuid.uuid4()
    body = _valid_create_body(
        tenant_id=tenant_id,
        role_assignments=[(unknown_role_id, root_id)],
        name_suffix="c8",
    )
    resp = app_client.post(
        "/api/v1/tenant-users",
        json=body,
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "INVALID_ROLE"


async def test_c9_audit_pair_populated(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_org_node,
    make_role,
    session_factory,
    platform_auth,
    cleanup_tenant_users_router,
) -> None:
    """Created tenant_user has both created_by_user_id AND
    created_by_user_type populated (Pattern (b) pair invariant)."""
    schema = get_settings().db_schema
    tenant_id, root_id, _root_path = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="C9-Tenant"
    )
    role = await make_role(audience="TENANT")

    # Resolve Anjali's id (the SUPER_ADMIN JWT's actor).
    async for session in get_tenant_session(
        platform_auth, session_factory
    ):
        anjali_row = await session.execute(
            text(
                f"SELECT id FROM {schema}.platform_users "
                "WHERE email = 'anjali@ithina.ai'"
            )
        )
        anjali_id = UUID(str(anjali_row.scalar_one()))

    body = _valid_create_body(
        tenant_id=tenant_id,
        role_assignments=[(role.id, root_id)],
        name_suffix="c9",
    )
    resp = app_client.post(
        "/api/v1/tenant-users",
        json=body,
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 201, resp.text
    new_id = UUID(resp.json()["id"])
    cleanup_tenant_users_router.append(new_id)

    async for session in get_tenant_session(
        platform_auth, session_factory
    ):
        row = await session.execute(
            text(
                "SELECT created_by_user_id, created_by_user_type, "
                "updated_by_user_id, updated_by_user_type "
                f"FROM {schema}.tenant_users WHERE id = :id"
            ),
            {"id": new_id},
        )
        c_uid, c_utype, u_uid, u_utype = row.one()

    assert c_uid is not None and c_utype is not None
    assert u_uid is not None and u_utype is not None
    assert UUID(str(c_uid)) == anjali_id
    assert str(c_utype) == "PLATFORM"
    assert UUID(str(u_uid)) == anjali_id
    assert str(u_utype) == "PLATFORM"


# ============================================================================
# PATCH /tenant-users/{user_id} (P1-P12)
# ============================================================================


async def test_p1_super_admin_patch_full_name(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_org_node,
    make_role,
    cleanup_tenant_users_router,
) -> None:
    """SUPER_ADMIN patches full_name -> 200 with updated value."""
    tenant_id, root_id, _root_path = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="P1-Tenant"
    )
    role = await make_role(audience="TENANT")
    body = _valid_create_body(
        tenant_id=tenant_id,
        role_assignments=[(role.id, root_id)],
        name_suffix="p1",
    )
    create = app_client.post(
        "/api/v1/tenant-users",
        json=body,
        headers=_auth(super_admin_jwt),
    )
    assert create.status_code == 201, create.text
    user_id = UUID(create.json()["id"])
    cleanup_tenant_users_router.append(user_id)

    patch = app_client.patch(
        f"/api/v1/tenant-users/{user_id}",
        json={"full_name": "Patched Name"},
        headers=_auth(super_admin_jwt),
    )
    assert patch.status_code == 200, patch.text
    assert patch.json()["full_name"] == "Patched Name"


async def test_p2_tenant_owner_patch_own_tenant_user(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_org_node,
    make_role,
    tenant_owner_jwt_factory,
    cleanup_tenant_users_router,
) -> None:
    """TENANT OWNER patches a different user in their own tenant -> 200."""
    tenant_id, root_id, _root_path = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="P2-Tenant"
    )
    role = await make_role(audience="TENANT")
    body = _valid_create_body(
        tenant_id=tenant_id,
        role_assignments=[(role.id, root_id)],
        name_suffix="p2",
    )
    create = app_client.post(
        "/api/v1/tenant-users",
        json=body,
        headers=_auth(super_admin_jwt),
    )
    assert create.status_code == 201, create.text
    user_id = UUID(create.json()["id"])
    cleanup_tenant_users_router.append(user_id)

    owner_jwt = await tenant_owner_jwt_factory(
        tenant_id,
        with_grants=[("ADMIN", "USERS", "CONFIGURE", "TENANT")],
    )
    patch = app_client.patch(
        f"/api/v1/tenant-users/{user_id}",
        json={"full_name": "Renamed By Owner"},
        headers=_auth(owner_jwt),
    )
    assert patch.status_code == 200, patch.text
    assert patch.json()["full_name"] == "Renamed By Owner"


async def test_p3_tenant_self_edit_returns_403(
    app_client,
    make_tenant,
    make_org_node,
    tenant_owner_jwt_factory,
) -> None:
    """LOAD-BEARING — TENANT caller targeting own user_id -> 403
    SELF_EDIT_FORBIDDEN.

    Primary self-edit guard case. Without this guard, a TENANT OWNER
    could rename / disrupt their own row in a way that destabilises
    the access path.
    """
    tenant_id, root_id, _root_path = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="P3-Tenant"
    )
    owner_jwt = await tenant_owner_jwt_factory(
        tenant_id,
        with_grants=[("ADMIN", "USERS", "CONFIGURE", "TENANT")],
    )
    own_user_id = _user_id_from_jwt(owner_jwt)

    resp = app_client.patch(
        f"/api/v1/tenant-users/{own_user_id}",
        json={"full_name": "Trying To Edit Self"},
        headers=_auth(owner_jwt),
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "SELF_EDIT_FORBIDDEN"


async def test_p4_missing_id_returns_404(
    app_client,
    super_admin_jwt,
) -> None:
    """PATCH a non-existent tenant_user -> 404 TENANT_USER_NOT_FOUND.

    The anchor dep fires first (see get_tenant_user_anchor); for a
    missing user_id it raises TenantUserNotFoundError BEFORE the
    handler body runs.
    """
    missing = uuid.uuid4()
    resp = app_client.patch(
        f"/api/v1/tenant-users/{missing}",
        json={"full_name": "X"},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["code"] == "TENANT_USER_NOT_FOUND"


async def test_p5_tenant_jwt_cross_tenant_returns_404(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_org_node,
    make_role,
    tenant_owner_jwt_factory,
    cleanup_tenant_users_router,
) -> None:
    """LOAD-BEARING — TENANT-A OWNER patches TENANT-B's user -> 404
    (RLS-as-404 per D-17), not 403.

    The 404 (vs 403) is the contract: existence of users in other
    tenants is not disclosed to TENANT callers. The anchor dep's
    schema-qualified SELECT inherits RLS via session GUCs; the
    cross-tenant row is invisible and surfaces as 404.
    """
    tenant_a_id, ra_id, _ra_path = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="P5-TenantA"
    )
    tenant_b_id, rb_id, _rb_path = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="P5-TenantB"
    )
    role_b = await make_role(audience="TENANT")

    # Create a user in tenant B via SUPER_ADMIN.
    body_b = _valid_create_body(
        tenant_id=tenant_b_id,
        role_assignments=[(role_b.id, rb_id)],
        name_suffix="p5b",
    )
    create_b = app_client.post(
        "/api/v1/tenant-users",
        json=body_b,
        headers=_auth(super_admin_jwt),
    )
    assert create_b.status_code == 201, create_b.text
    user_b_id = UUID(create_b.json()["id"])
    cleanup_tenant_users_router.append(user_b_id)

    # OWNER of TENANT-A tries to patch TENANT-B's user.
    owner_a_jwt = await tenant_owner_jwt_factory(
        tenant_a_id,
        with_grants=[("ADMIN", "USERS", "CONFIGURE", "TENANT")],
    )
    resp = app_client.patch(
        f"/api/v1/tenant-users/{user_b_id}",
        json={"full_name": "Cross-Tenant Probe"},
        headers=_auth(owner_a_jwt),
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["code"] == "TENANT_USER_NOT_FOUND"


async def test_p6_role_full_replacement(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_org_node,
    make_role,
    cleanup_tenant_users_router,
) -> None:
    """PATCH replacing the whole desired set: role_a revoked + role_b
    granted.

    Step 6.14 (vs 6.10.1): when the desired set has NO overlap with
    the current set, the diff-replace path reduces to one revoke + one
    insert (same wire shape as the retired whole-set replace). This
    test guards that 'no overlap' corner of LD3; the overlap case
    that's the actual diff-replace win is R3.
    """
    tenant_id, root_id, _root_path = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="P6-Tenant"
    )
    role_a = await make_role(audience="TENANT")
    role_b = await make_role(audience="TENANT")

    body = _valid_create_body(
        tenant_id=tenant_id,
        role_assignments=[(role_a.id, root_id)],
        name_suffix="p6",
    )
    create = app_client.post(
        "/api/v1/tenant-users",
        json=body,
        headers=_auth(super_admin_jwt),
    )
    assert create.status_code == 201, create.text
    user_id = UUID(create.json()["id"])
    cleanup_tenant_users_router.append(user_id)
    initial_ids = {r["role_id"] for r in create.json()["roles"]}
    assert initial_ids == {str(role_a.id)}

    patch = app_client.patch(
        f"/api/v1/tenant-users/{user_id}",
        json={"roles": _roles_payload([(role_b.id, root_id)])},
        headers=_auth(super_admin_jwt),
    )
    assert patch.status_code == 200, patch.text
    j = patch.json()
    active_ids = {
        r["role_id"] for r in j["roles"] if r["status"] == "ACTIVE"
    }
    inactive_ids = {
        r["role_id"] for r in j["roles"] if r["status"] == "INACTIVE"
    }
    assert active_ids == {str(role_b.id)}
    assert str(role_a.id) in inactive_ids


async def test_p7_patch_on_suspended_user(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_org_node,
    make_role,
    make_tenant_user,
    make_platform_user,
    session_factory,
    platform_auth,
    cleanup_tenant_users_router,
) -> None:
    """PATCH still accepted while tenant_user is SUSPENDED."""
    schema = get_settings().db_schema
    tenant_id, root_id, _root_path = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="P7-Tenant"
    )
    # Seed a SUSPENDED user directly via SQL (CHECK constraints require
    # auth0_sub + invitation_accepted_at + suspended_* tower).
    pu = await make_platform_user(status="ACTIVE")
    new_user_id = uuid.uuid4()
    async for session in get_tenant_session(
        platform_auth, session_factory
    ):
        await session.execute(
            text(
                f"INSERT INTO {schema}.tenant_users ("
                "  id, tenant_id, email, full_name, status,"
                "  auth0_sub, invitation_accepted_at,"
                "  suspended_at, suspended_by_user_id, suspended_by_user_type,"
                "  created_by_user_id, created_by_user_type,"
                "  updated_by_user_id, updated_by_user_type"
                ") VALUES ("
                "  :id, :tenant_id, :email, :full_name,"
                f"  CAST('SUSPENDED' AS {schema}.tenant_user_status_enum),"
                "  :sub, '2026-01-01 00:00:00+00',"
                "  now(), :actor,"
                f"  CAST('PLATFORM' AS {schema}.actor_user_type_enum),"
                f"  :actor, CAST('PLATFORM' AS {schema}.actor_user_type_enum),"
                f"  :actor, CAST('PLATFORM' AS {schema}.actor_user_type_enum)"
                ")"
            ),
            {
                "id": new_user_id,
                "tenant_id": tenant_id,
                "email": f"p7-{new_user_id.hex[:8]}@test.example.com",
                "full_name": "P7 Suspended",
                "sub": f"auth0|fixture-{new_user_id}",
                "actor": pu.id,
            },
        )
    cleanup_tenant_users_router.append(new_user_id)

    resp = app_client.patch(
        f"/api/v1/tenant-users/{new_user_id}",
        json={"full_name": "Patched While Suspended"},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200, resp.text
    j = resp.json()
    assert j["full_name"] == "Patched While Suspended"
    assert j["status"] == "SUSPENDED"


async def test_p8_patch_on_invited_user(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_org_node,
    make_role,
    cleanup_tenant_users_router,
) -> None:
    """PATCH allowed on INVITED users (fresh row, not yet ACTIVE)."""
    tenant_id, root_id, _root_path = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="P8-Tenant"
    )
    role = await make_role(audience="TENANT")
    body = _valid_create_body(
        tenant_id=tenant_id,
        role_assignments=[(role.id, root_id)],
        name_suffix="p8",
    )
    create = app_client.post(
        "/api/v1/tenant-users",
        json=body,
        headers=_auth(super_admin_jwt),
    )
    user_id = UUID(create.json()["id"])
    cleanup_tenant_users_router.append(user_id)
    assert create.json()["status"] == "INVITED"

    resp = app_client.patch(
        f"/api/v1/tenant-users/{user_id}",
        json={"full_name": "Renamed While Invited"},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["full_name"] == "Renamed While Invited"


async def test_p9_empty_body_returns_422(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_org_node,
    make_role,
    cleanup_tenant_users_router,
) -> None:
    """PATCH with empty body -> 422 EMPTY_PATCH."""
    tenant_id, root_id, _root_path = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="P9-Tenant"
    )
    role = await make_role(audience="TENANT")
    body = _valid_create_body(
        tenant_id=tenant_id,
        role_assignments=[(role.id, root_id)],
        name_suffix="p9",
    )
    create = app_client.post(
        "/api/v1/tenant-users",
        json=body,
        headers=_auth(super_admin_jwt),
    )
    user_id = UUID(create.json()["id"])
    cleanup_tenant_users_router.append(user_id)

    resp = app_client.patch(
        f"/api/v1/tenant-users/{user_id}",
        json={},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "EMPTY_PATCH"


async def test_p10_email_collision_returns_409(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_org_node,
    make_role,
    cleanup_tenant_users_router,
) -> None:
    """PATCH renaming to another existing user's email in the same
    tenant -> 409."""
    tenant_id, root_id, _root_path = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="P10-Tenant"
    )
    role = await make_role(audience="TENANT")

    # User 1.
    body_1 = _valid_create_body(
        tenant_id=tenant_id,
        role_assignments=[(role.id, root_id)],
        name_suffix="p10a",
    )
    r1 = app_client.post(
        "/api/v1/tenant-users",
        json=body_1,
        headers=_auth(super_admin_jwt),
    )
    cleanup_tenant_users_router.append(UUID(r1.json()["id"]))

    # User 2 with a different email.
    body_2 = _valid_create_body(
        tenant_id=tenant_id,
        role_assignments=[(role.id, root_id)],
        name_suffix="p10b",
    )
    r2 = app_client.post(
        "/api/v1/tenant-users",
        json=body_2,
        headers=_auth(super_admin_jwt),
    )
    user_2_id = UUID(r2.json()["id"])
    cleanup_tenant_users_router.append(user_2_id)

    # Try to PATCH user 2 to use user 1's email.
    resp = app_client.patch(
        f"/api/v1/tenant-users/{user_2_id}",
        json={"email": body_1["email"]},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == "DUPLICATE_TENANT_USER_EMAIL"


async def test_p11_rename_to_own_email_returns_200(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_org_node,
    make_role,
    cleanup_tenant_users_router,
) -> None:
    """PATCH email = current email -> 200 (rename-to-self no-op)."""
    tenant_id, root_id, _root_path = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="P11-Tenant"
    )
    role = await make_role(audience="TENANT")
    body = _valid_create_body(
        tenant_id=tenant_id,
        role_assignments=[(role.id, root_id)],
        name_suffix="p11",
    )
    create = app_client.post(
        "/api/v1/tenant-users",
        json=body,
        headers=_auth(super_admin_jwt),
    )
    user_id = UUID(create.json()["id"])
    cleanup_tenant_users_router.append(user_id)

    resp = app_client.patch(
        f"/api/v1/tenant-users/{user_id}",
        json={"email": body["email"]},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["email"] == body["email"]


async def test_p12_platform_role_in_patch_roles_returns_422(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_org_node,
    make_role,
    cleanup_tenant_users_router,
) -> None:
    """PATCH with a PLATFORM-audience role in roles[] -> 422
    INVALID_ROLE_AUDIENCE."""
    tenant_id, root_id, _root_path = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="P12-Tenant"
    )
    role_t = await make_role(audience="TENANT")
    role_p = await make_role(audience="PLATFORM")
    body = _valid_create_body(
        tenant_id=tenant_id,
        role_assignments=[(role_t.id, root_id)],
        name_suffix="p12",
    )
    create = app_client.post(
        "/api/v1/tenant-users",
        json=body,
        headers=_auth(super_admin_jwt),
    )
    user_id = UUID(create.json()["id"])
    cleanup_tenant_users_router.append(user_id)

    resp = app_client.patch(
        f"/api/v1/tenant-users/{user_id}",
        json={"roles": _roles_payload([(role_p.id, root_id)])},
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "INVALID_ROLE_AUDIENCE"


# ============================================================================
# POST /tenant-users/{user_id}/suspend (S1-S5)
# ============================================================================


async def _create_active_user(
    app_client: Any,
    super_admin_jwt: str,
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
    *,
    tenant_id: UUID,
    role_assignments: list[tuple[UUID, UUID]],
    name_suffix: str,
) -> UUID:
    """Create an INVITED user via POST, then promote to ACTIVE via raw
    SQL (the Auth0 invite-accept callback is Stage 3, so no endpoint
    path).

    Used by S1/S2/S5 + A4/A5 to reach the ACTIVE state for transition
    tests.
    """
    schema = get_settings().db_schema
    body = _valid_create_body(
        tenant_id=tenant_id,
        role_assignments=role_assignments,
        name_suffix=name_suffix,
    )
    create = app_client.post(
        "/api/v1/tenant-users",
        json=body,
        headers=_auth(super_admin_jwt),
    )
    assert create.status_code == 201, create.text
    user_id = UUID(create.json()["id"])

    async for session in get_tenant_session(
        platform_auth, session_factory
    ):
        await session.execute(
            text(
                f"UPDATE {schema}.tenant_users SET "
                f"  status = CAST('ACTIVE' AS {schema}.tenant_user_status_enum),"
                "  auth0_sub = :sub,"
                "  invitation_accepted_at = now() "
                "WHERE id = :id"
            ),
            {"sub": f"auth0|s-test-{user_id}", "id": user_id},
        )
    return user_id


async def test_s1_super_admin_suspends_active_returns_200(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_org_node,
    make_role,
    session_factory,
    platform_auth,
    cleanup_tenant_users_router,
) -> None:
    """SUPER_ADMIN suspends ACTIVE user -> 200, status SUSPENDED,
    suspended_at populated."""
    tenant_id, root_id, _root_path = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="S1-Tenant"
    )
    role = await make_role(audience="TENANT")
    user_id = await _create_active_user(
        app_client, super_admin_jwt, session_factory, platform_auth,
        tenant_id=tenant_id,
        role_assignments=[(role.id, root_id)], name_suffix="s1",
    )
    cleanup_tenant_users_router.append(user_id)

    resp = app_client.post(
        f"/api/v1/tenant-users/{user_id}/suspend",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 200, resp.text
    j = resp.json()
    assert j["status"] == "SUSPENDED"
    assert j["suspended_at"] is not None


async def test_s2_tenant_owner_suspends_active(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_org_node,
    make_role,
    tenant_owner_jwt_factory,
    session_factory,
    platform_auth,
    cleanup_tenant_users_router,
) -> None:
    """TENANT OWNER suspends ACTIVE user in own tenant -> 200."""
    tenant_id, root_id, _root_path = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="S2-Tenant"
    )
    role = await make_role(audience="TENANT")
    user_id = await _create_active_user(
        app_client, super_admin_jwt, session_factory, platform_auth,
        tenant_id=tenant_id,
        role_assignments=[(role.id, root_id)], name_suffix="s2",
    )
    cleanup_tenant_users_router.append(user_id)

    owner_jwt = await tenant_owner_jwt_factory(
        tenant_id,
        with_grants=[("ADMIN", "USERS", "CONFIGURE", "TENANT")],
    )
    resp = app_client.post(
        f"/api/v1/tenant-users/{user_id}/suspend",
        headers=_auth(owner_jwt),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "SUSPENDED"


async def test_s3_tenant_self_suspend_returns_403(
    app_client,
    make_tenant,
    make_org_node,
    tenant_owner_jwt_factory,
) -> None:
    """TENANT caller suspends self -> 403 SELF_EDIT_FORBIDDEN."""
    tenant_id, root_id, _root_path = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="S3-Tenant"
    )
    owner_jwt = await tenant_owner_jwt_factory(
        tenant_id,
        with_grants=[("ADMIN", "USERS", "CONFIGURE", "TENANT")],
    )
    own_user_id = _user_id_from_jwt(owner_jwt)

    resp = app_client.post(
        f"/api/v1/tenant-users/{own_user_id}/suspend",
        headers=_auth(owner_jwt),
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "SELF_EDIT_FORBIDDEN"


async def test_s4_suspend_invited_returns_409(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_org_node,
    make_role,
    cleanup_tenant_users_router,
) -> None:
    """LOAD-BEARING — Suspending an INVITED user -> 409
    INVALID_STATE_TRANSITION.

    Maps the DDL ck_tenant_users_auth0_sub_consistency reject
    (SUSPENDED requires auth0_sub non-NULL; INVITED requires NULL) to
    a clean 409 at the app layer, so the caller never sees a 500.
    """
    tenant_id, root_id, _root_path = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="S4-Tenant"
    )
    role = await make_role(audience="TENANT")
    body = _valid_create_body(
        tenant_id=tenant_id,
        role_assignments=[(role.id, root_id)],
        name_suffix="s4",
    )
    create = app_client.post(
        "/api/v1/tenant-users",
        json=body,
        headers=_auth(super_admin_jwt),
    )
    user_id = UUID(create.json()["id"])
    cleanup_tenant_users_router.append(user_id)
    assert create.json()["status"] == "INVITED"

    resp = app_client.post(
        f"/api/v1/tenant-users/{user_id}/suspend",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == "INVALID_STATE_TRANSITION"


async def test_s5_suspend_already_suspended_returns_409(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_org_node,
    make_role,
    session_factory,
    platform_auth,
    cleanup_tenant_users_router,
) -> None:
    """Suspending an already-SUSPENDED user -> 409
    INVALID_STATE_TRANSITION."""
    tenant_id, root_id, _root_path = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="S5-Tenant"
    )
    role = await make_role(audience="TENANT")
    user_id = await _create_active_user(
        app_client, super_admin_jwt, session_factory, platform_auth,
        tenant_id=tenant_id,
        role_assignments=[(role.id, root_id)], name_suffix="s5",
    )
    cleanup_tenant_users_router.append(user_id)

    # First suspend succeeds.
    r1 = app_client.post(
        f"/api/v1/tenant-users/{user_id}/suspend",
        headers=_auth(super_admin_jwt),
    )
    assert r1.status_code == 200

    # Second suspend on SUSPENDED user -> 409.
    r2 = app_client.post(
        f"/api/v1/tenant-users/{user_id}/suspend",
        headers=_auth(super_admin_jwt),
    )
    assert r2.status_code == 409, r2.text
    assert r2.json()["code"] == "INVALID_STATE_TRANSITION"


# ============================================================================
# POST /tenant-users/{user_id}/activate (A1-A5)
# ============================================================================


async def test_a1_super_admin_activates_suspended_returns_200(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_org_node,
    make_role,
    session_factory,
    platform_auth,
    cleanup_tenant_users_router,
) -> None:
    """SUPER_ADMIN activates SUSPENDED user -> 200, status ACTIVE,
    suspended_* cleared."""
    tenant_id, root_id, _root_path = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="A1-Tenant"
    )
    role = await make_role(audience="TENANT")
    user_id = await _create_active_user(
        app_client, super_admin_jwt, session_factory, platform_auth,
        tenant_id=tenant_id,
        role_assignments=[(role.id, root_id)], name_suffix="a1",
    )
    cleanup_tenant_users_router.append(user_id)

    # Suspend first.
    r1 = app_client.post(
        f"/api/v1/tenant-users/{user_id}/suspend",
        headers=_auth(super_admin_jwt),
    )
    assert r1.status_code == 200

    # Activate.
    r2 = app_client.post(
        f"/api/v1/tenant-users/{user_id}/activate",
        headers=_auth(super_admin_jwt),
    )
    assert r2.status_code == 200, r2.text
    j = r2.json()
    assert j["status"] == "ACTIVE"
    assert j["suspended_at"] is None


async def test_a2_tenant_owner_activates_clears_suspended(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_org_node,
    make_role,
    tenant_owner_jwt_factory,
    session_factory,
    platform_auth,
    cleanup_tenant_users_router,
) -> None:
    """TENANT OWNER activates SUSPENDED user in own tenant -> 200;
    suspended_* tower cleared atomically per
    ck_tenant_users_suspended_consistency."""
    tenant_id, root_id, _root_path = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="A2-Tenant"
    )
    role = await make_role(audience="TENANT")
    user_id = await _create_active_user(
        app_client, super_admin_jwt, session_factory, platform_auth,
        tenant_id=tenant_id,
        role_assignments=[(role.id, root_id)], name_suffix="a2",
    )
    cleanup_tenant_users_router.append(user_id)

    # Suspend first via SUPER_ADMIN (PLATFORM); then activate via OWNER
    # (TENANT) to exercise the multi-audience path on activate.
    app_client.post(
        f"/api/v1/tenant-users/{user_id}/suspend",
        headers=_auth(super_admin_jwt),
    )

    owner_jwt = await tenant_owner_jwt_factory(
        tenant_id,
        with_grants=[("ADMIN", "USERS", "CONFIGURE", "TENANT")],
    )
    resp = app_client.post(
        f"/api/v1/tenant-users/{user_id}/activate",
        headers=_auth(owner_jwt),
    )
    assert resp.status_code == 200, resp.text
    j = resp.json()
    assert j["status"] == "ACTIVE"
    assert j["suspended_at"] is None

    # Verify the suspended_by_user_* pair was cleared too (per the
    # ck_tenant_users_suspended_consistency CHECK both must be NULL
    # when status != SUSPENDED).
    schema = get_settings().db_schema
    async for session in get_tenant_session(
        platform_auth, session_factory
    ):
        row = await session.execute(
            text(
                "SELECT suspended_by_user_id, suspended_by_user_type "
                f"FROM {schema}.tenant_users WHERE id = :id"
            ),
            {"id": user_id},
        )
        sb_uid, sb_utype = row.one()
    assert sb_uid is None and sb_utype is None


async def test_a3_tenant_self_activate_returns_403(
    app_client,
    make_tenant,
    make_org_node,
    tenant_owner_jwt_factory,
) -> None:
    """TENANT caller activating self -> 403 SELF_EDIT_FORBIDDEN.

    Functionally impossible (a suspended user has no session) but the
    guard fires uniformly on all 3 path-bound endpoints.
    """
    tenant_id, root_id, _root_path = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="A3-Tenant"
    )
    owner_jwt = await tenant_owner_jwt_factory(
        tenant_id,
        with_grants=[("ADMIN", "USERS", "CONFIGURE", "TENANT")],
    )
    own_user_id = _user_id_from_jwt(owner_jwt)

    resp = app_client.post(
        f"/api/v1/tenant-users/{own_user_id}/activate",
        headers=_auth(owner_jwt),
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "SELF_EDIT_FORBIDDEN"


async def test_a4_activate_invited_returns_409(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_org_node,
    make_role,
    cleanup_tenant_users_router,
) -> None:
    """Activating an INVITED user -> 409 INVALID_STATE_TRANSITION.

    INVITED -> ACTIVE is the Auth0 invite-accept callback flow (Stage 3);
    the explicit /activate endpoint refuses to take that path so the
    contract stays uniform with the suspend matrix.
    """
    tenant_id, root_id, _root_path = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="A4-Tenant"
    )
    role = await make_role(audience="TENANT")
    body = _valid_create_body(
        tenant_id=tenant_id,
        role_assignments=[(role.id, root_id)],
        name_suffix="a4",
    )
    create = app_client.post(
        "/api/v1/tenant-users",
        json=body,
        headers=_auth(super_admin_jwt),
    )
    user_id = UUID(create.json()["id"])
    cleanup_tenant_users_router.append(user_id)
    assert create.json()["status"] == "INVITED"

    resp = app_client.post(
        f"/api/v1/tenant-users/{user_id}/activate",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == "INVALID_STATE_TRANSITION"


async def test_a5_activate_already_active_returns_409(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_org_node,
    make_role,
    session_factory,
    platform_auth,
    cleanup_tenant_users_router,
) -> None:
    """Activating an already-ACTIVE user -> 409 INVALID_STATE_TRANSITION."""
    tenant_id, root_id, _root_path = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="A5-Tenant"
    )
    role = await make_role(audience="TENANT")
    user_id = await _create_active_user(
        app_client, super_admin_jwt, session_factory, platform_auth,
        tenant_id=tenant_id,
        role_assignments=[(role.id, root_id)], name_suffix="a5",
    )
    cleanup_tenant_users_router.append(user_id)

    resp = app_client.post(
        f"/api/v1/tenant-users/{user_id}/activate",
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == "INVALID_STATE_TRANSITION"


# ============================================================================
# Step 6.14 router tests: R1-R6 (diff-replace shape), V1-V7 (validation),
# P1 (LD8 self-edit guard regression with new body shape).
#
# R3 / R4 / R6 are LOAD-BEARING — they enforce the core LD3 diff-replace
# invariant (unchanged tuples retain granted_at; concurrent UNIQUE race
# returns 409 not 500).
# ============================================================================


def _granted_at_for(payload: dict[str, Any], role_id: UUID, anchor_id: UUID) -> str | None:
    """Return granted_at for the ACTIVE assignment with (role, anchor)."""
    for r in payload["roles"]:
        if (
            r["status"] == "ACTIVE"
            and r["role_id"] == str(role_id)
            and r["org_node_id"] == str(anchor_id)
        ):
            return str(r["granted_at"])
    return None


async def test_r1_post_multi_anchor_creates_two_active_rows(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_org_node,
    make_role,
    cleanup_tenant_users_router,
) -> None:
    """LOAD-BEARING (new shape end-to-end): POST with two
    {role_id, org_node_id} items at distinct anchors creates 2 ACTIVE
    rows with distinct org_node_id."""
    tenant_id, root_id, root_path = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="R1-Tenant"
    )
    anchor_a_id, _ = await make_org_node(
        tenant_id=tenant_id,
        node_type="REGION",
        code=f"r1a-{uuid.uuid4().hex[:6]}",
        name="R1 Region A",
        parent_id=root_id,
        parent_path=root_path,
    )
    anchor_b_id, _ = await make_org_node(
        tenant_id=tenant_id,
        node_type="REGION",
        code=f"r1b-{uuid.uuid4().hex[:6]}",
        name="R1 Region B",
        parent_id=root_id,
        parent_path=root_path,
    )
    role_x = await make_role(audience="TENANT")
    role_y = await make_role(audience="TENANT")

    body = _valid_create_body(
        tenant_id=tenant_id,
        role_assignments=[
            (role_x.id, anchor_a_id),
            (role_y.id, anchor_b_id),
        ],
        name_suffix="r1",
    )
    resp = app_client.post(
        "/api/v1/tenant-users",
        json=body,
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 201, resp.text
    j = resp.json()
    cleanup_tenant_users_router.append(UUID(j["id"]))

    active_pairs = {
        (r["role_id"], r["org_node_id"])
        for r in j["roles"]
        if r["status"] == "ACTIVE"
    }
    assert active_pairs == {
        (str(role_x.id), str(anchor_a_id)),
        (str(role_y.id), str(anchor_b_id)),
    }


async def test_r2_pattern_b_same_role_distinct_anchors(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_org_node,
    make_role,
    cleanup_tenant_users_router,
) -> None:
    """LOAD-BEARING: the partial-UNIQUE index licenses (user, role)
    at distinct org_node_id values (Pattern B per DDL).

    Without this, the UNIQUE index would block the second INSERT and
    surface as 409; the test confirms the partial index's WHERE clause
    (status=ACTIVE, all 3 columns) is the actual key in play."""
    tenant_id, root_id, root_path = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="R2-Tenant"
    )
    anchor_a_id, _ = await make_org_node(
        tenant_id=tenant_id,
        node_type="REGION",
        code=f"r2a-{uuid.uuid4().hex[:6]}",
        name="R2 Region A",
        parent_id=root_id,
        parent_path=root_path,
    )
    anchor_b_id, _ = await make_org_node(
        tenant_id=tenant_id,
        node_type="REGION",
        code=f"r2b-{uuid.uuid4().hex[:6]}",
        name="R2 Region B",
        parent_id=root_id,
        parent_path=root_path,
    )
    role = await make_role(audience="TENANT")

    body = _valid_create_body(
        tenant_id=tenant_id,
        role_assignments=[
            (role.id, anchor_a_id),
            (role.id, anchor_b_id),
        ],
        name_suffix="r2",
    )
    resp = app_client.post(
        "/api/v1/tenant-users",
        json=body,
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 201, resp.text
    j = resp.json()
    cleanup_tenant_users_router.append(UUID(j["id"]))

    active_pairs = {
        (r["role_id"], r["org_node_id"])
        for r in j["roles"]
        if r["status"] == "ACTIVE"
    }
    assert active_pairs == {
        (str(role.id), str(anchor_a_id)),
        (str(role.id), str(anchor_b_id)),
    }


async def test_r3_patch_diff_preserves_unchanged_granted_at(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_org_node,
    make_role,
    cleanup_tenant_users_router,
) -> None:
    """LOAD-BEARING (LD3 core invariant): PATCH with overlap; unchanged
    rows retain their original granted_at."""
    tenant_id, root_id, root_path = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="R3-Tenant"
    )
    a_id, _ = await make_org_node(
        tenant_id=tenant_id, node_type="REGION",
        code=f"r3a-{uuid.uuid4().hex[:6]}",
        name="R3 A", parent_id=root_id, parent_path=root_path,
    )
    b_id, _ = await make_org_node(
        tenant_id=tenant_id, node_type="REGION",
        code=f"r3b-{uuid.uuid4().hex[:6]}",
        name="R3 B", parent_id=root_id, parent_path=root_path,
    )
    c_id, _ = await make_org_node(
        tenant_id=tenant_id, node_type="REGION",
        code=f"r3c-{uuid.uuid4().hex[:6]}",
        name="R3 C", parent_id=root_id, parent_path=root_path,
    )
    d_id, _ = await make_org_node(
        tenant_id=tenant_id, node_type="REGION",
        code=f"r3d-{uuid.uuid4().hex[:6]}",
        name="R3 D", parent_id=root_id, parent_path=root_path,
    )
    role = await make_role(audience="TENANT")

    # Initial: 3 ACTIVE rows.
    body = _valid_create_body(
        tenant_id=tenant_id,
        role_assignments=[
            (role.id, a_id), (role.id, b_id), (role.id, c_id),
        ],
        name_suffix="r3",
    )
    create = app_client.post(
        "/api/v1/tenant-users",
        json=body,
        headers=_auth(super_admin_jwt),
    )
    assert create.status_code == 201, create.text
    user_id = UUID(create.json()["id"])
    cleanup_tenant_users_router.append(user_id)

    granted_at_a_before = _granted_at_for(create.json(), role.id, a_id)
    granted_at_b_before = _granted_at_for(create.json(), role.id, b_id)
    assert granted_at_a_before is not None
    assert granted_at_b_before is not None

    # PATCH: desired = (a, b, d). 'c' revoked; 'd' added; a and b
    # unchanged.
    patch = app_client.patch(
        f"/api/v1/tenant-users/{user_id}",
        json={
            "roles": _roles_payload(
                [
                    (role.id, a_id),
                    (role.id, b_id),
                    (role.id, d_id),
                ]
            )
        },
        headers=_auth(super_admin_jwt),
    )
    assert patch.status_code == 200, patch.text
    j = patch.json()

    active_pairs = {
        (r["role_id"], r["org_node_id"])
        for r in j["roles"]
        if r["status"] == "ACTIVE"
    }
    assert active_pairs == {
        (str(role.id), str(a_id)),
        (str(role.id), str(b_id)),
        (str(role.id), str(d_id)),
    }

    inactive_anchors = {
        r["org_node_id"]
        for r in j["roles"]
        if r["status"] == "INACTIVE"
    }
    assert str(c_id) in inactive_anchors

    granted_at_a_after = _granted_at_for(j, role.id, a_id)
    granted_at_b_after = _granted_at_for(j, role.id, b_id)
    assert granted_at_a_after == granted_at_a_before
    assert granted_at_b_after == granted_at_b_before


async def test_r4_patch_noop_no_writes(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_org_node,
    make_role,
    cleanup_tenant_users_router,
) -> None:
    """LOAD-BEARING (LD3 no-op): PATCH with desired_set == current_set
    leaves rows untouched."""
    tenant_id, root_id, _root_path = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="R4-Tenant"
    )
    role = await make_role(audience="TENANT")
    body = _valid_create_body(
        tenant_id=tenant_id,
        role_assignments=[(role.id, root_id)],
        name_suffix="r4",
    )
    create = app_client.post(
        "/api/v1/tenant-users",
        json=body,
        headers=_auth(super_admin_jwt),
    )
    assert create.status_code == 201, create.text
    user_id = UUID(create.json()["id"])
    cleanup_tenant_users_router.append(user_id)
    granted_before = _granted_at_for(create.json(), role.id, root_id)

    patch = app_client.patch(
        f"/api/v1/tenant-users/{user_id}",
        json={"roles": _roles_payload([(role.id, root_id)])},
        headers=_auth(super_admin_jwt),
    )
    assert patch.status_code == 200, patch.text
    granted_after = _granted_at_for(patch.json(), role.id, root_id)
    assert granted_after == granted_before


async def test_r5_patch_empty_list_revokes_all(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_org_node,
    make_role,
    cleanup_tenant_users_router,
) -> None:
    """PATCH ``roles=[]`` revokes every current ACTIVE assignment."""
    tenant_id, root_id, _root_path = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="R5-Tenant"
    )
    role_a = await make_role(audience="TENANT")
    role_b = await make_role(audience="TENANT")
    body = _valid_create_body(
        tenant_id=tenant_id,
        role_assignments=[
            (role_a.id, root_id), (role_b.id, root_id),
        ],
        name_suffix="r5",
    )
    create = app_client.post(
        "/api/v1/tenant-users",
        json=body,
        headers=_auth(super_admin_jwt),
    )
    assert create.status_code == 201, create.text
    user_id = UUID(create.json()["id"])
    cleanup_tenant_users_router.append(user_id)

    patch = app_client.patch(
        f"/api/v1/tenant-users/{user_id}",
        json={"roles": []},
        headers=_auth(super_admin_jwt),
    )
    assert patch.status_code == 200, patch.text
    active = [
        r for r in patch.json()["roles"] if r["status"] == "ACTIVE"
    ]
    assert active == []


# R6 (concurrent UNIQUE conflict -> 409) lives at the repo level as
# RT4: simulating two interleaved SELECT FOR UPDATE windows inside a
# single pytest event loop requires bypassing the SELECT step, which
# is naturally done by calling the repo's _apply_role_assignments_diff
# directly. The router-level wire shape (409 ROLE_ASSIGNMENT_CONFLICT
# JSON envelope) is covered by E3 in test_tenant_users_errors.py.


async def test_v1_archived_role_returns_422_invalid_role(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_org_node,
    make_role,
    session_factory,
    platform_auth,
) -> None:
    """ARCHIVED role aggregates with missing under INVALID_ROLE 422."""
    schema = get_settings().db_schema
    tenant_id, root_id, _root_path = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="V1-Tenant"
    )
    role = await make_role(audience="TENANT")
    async for session in get_tenant_session(
        platform_auth, session_factory
    ):
        pu_id_row = await session.execute(
            text(
                f"SELECT id FROM {schema}.platform_users LIMIT 1"
            )
        )
        pu_id = UUID(str(pu_id_row.scalar_one()))
        await session.execute(
            text(
                f"""
                UPDATE {schema}.roles
                   SET status = CAST('ARCHIVED' AS {schema}.role_status_enum),
                       archived_at = now(),
                       archived_by_user_id = :pu,
                       archived_by_user_type = CAST('PLATFORM'
                                              AS {schema}.actor_user_type_enum)
                 WHERE id = :id
                """
            ),
            {"id": role.id, "pu": pu_id},
        )

    body = _valid_create_body(
        tenant_id=tenant_id,
        role_assignments=[(role.id, root_id)],
        name_suffix="v1",
    )
    resp = app_client.post(
        "/api/v1/tenant-users",
        json=body,
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "INVALID_ROLE"


async def test_v2_missing_org_node_returns_422_invalid_org_node(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_org_node,
    make_role,
) -> None:
    """LOAD-BEARING: missing org_node_id -> 422 INVALID_ORG_NODE."""
    tenant_id, _root_id, _root_path = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="V2-Tenant"
    )
    role = await make_role(audience="TENANT")
    missing_anchor = uuid.uuid4()
    body = _valid_create_body(
        tenant_id=tenant_id,
        role_assignments=[(role.id, missing_anchor)],
        name_suffix="v2",
    )
    resp = app_client.post(
        "/api/v1/tenant-users",
        json=body,
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "INVALID_ORG_NODE"


async def test_v3_archived_org_node_returns_422_invalid_org_node(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_org_node,
    make_role,
    session_factory,
    platform_auth,
) -> None:
    """ARCHIVED org_node aggregates with missing under INVALID_ORG_NODE.

    Org_node is created ACTIVE then UPDATEd to ARCHIVED via raw SQL
    so the ``ck_org_nodes_archived_consistency`` CHECK (archived_*
    pair must be co-set with status='ARCHIVED') is satisfied.
    """
    schema = get_settings().db_schema
    tenant_id, root_id, root_path = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="V3-Tenant"
    )
    role = await make_role(audience="TENANT")
    anchor_id, _ = await make_org_node(
        tenant_id=tenant_id, node_type="REGION",
        code=f"v3a-{uuid.uuid4().hex[:6]}",
        name="V3 Archived", parent_id=root_id, parent_path=root_path,
    )
    async for session in get_tenant_session(
        platform_auth, session_factory
    ):
        pu_id_row = await session.execute(
            text(
                f"SELECT id FROM {schema}.platform_users LIMIT 1"
            )
        )
        pu_id = UUID(str(pu_id_row.scalar_one()))
        await session.execute(
            text(
                f"""
                UPDATE {schema}.org_nodes
                   SET status = CAST('ARCHIVED'
                                AS {schema}.org_node_status_enum),
                       archived_at = now(),
                       archived_by_user_id = :pu,
                       archived_by_user_type = CAST('PLATFORM'
                                              AS {schema}.actor_user_type_enum)
                 WHERE id = :id
                """
            ),
            {"id": anchor_id, "pu": pu_id},
        )

    body = _valid_create_body(
        tenant_id=tenant_id,
        role_assignments=[(role.id, anchor_id)],
        name_suffix="v3",
    )
    resp = app_client.post(
        "/api/v1/tenant-users",
        json=body,
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "INVALID_ORG_NODE"


async def test_v4_cross_tenant_org_node_returns_422_invalid_org_node(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_org_node,
    make_role,
    cleanup_tenant_users_router,
) -> None:
    """LOAD-BEARING (cross-tenant injection guard): an org_node_id
    from a different tenant -> 422 INVALID_ORG_NODE at the validation
    stage, ahead of the composite-FK reject at INSERT stage."""
    tenant_a_id, ra_id, _ra_path = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="V4-TenantA"
    )
    tenant_b_id, rb_id, _rb_path = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="V4-TenantB"
    )
    role = await make_role(audience="TENANT")

    body = _valid_create_body(
        tenant_id=tenant_a_id,
        role_assignments=[(role.id, rb_id)],
        name_suffix="v4",
    )
    resp = app_client.post(
        "/api/v1/tenant-users",
        json=body,
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "INVALID_ORG_NODE"

    body_ok = _valid_create_body(
        tenant_id=tenant_a_id,
        role_assignments=[(role.id, ra_id)],
        name_suffix="v4ok",
    )
    resp_ok = app_client.post(
        "/api/v1/tenant-users",
        json=body_ok,
        headers=_auth(super_admin_jwt),
    )
    assert resp_ok.status_code == 201, resp_ok.text
    cleanup_tenant_users_router.append(UUID(resp_ok.json()["id"]))


async def test_v5_within_request_duplicate_returns_422(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_org_node,
    make_role,
) -> None:
    """LOAD-BEARING (LD5): duplicate (role_id, org_node_id) in the
    submitted roles[] -> 422 DUPLICATE_ROLE_ASSIGNMENT_IN_REQUEST."""
    tenant_id, root_id, _root_path = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="V5-Tenant"
    )
    role = await make_role(audience="TENANT")
    body = _valid_create_body(
        tenant_id=tenant_id,
        role_assignments=[(role.id, root_id), (role.id, root_id)],
        name_suffix="v5",
    )
    resp = app_client.post(
        "/api/v1/tenant-users",
        json=body,
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "DUPLICATE_ROLE_ASSIGNMENT_IN_REQUEST"


async def test_v6_platform_audience_role_returns_422_invalid_audience(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_org_node,
    make_role,
) -> None:
    """Regression of 6.10.1 C7 under the new body shape: PLATFORM
    audience role -> 422 INVALID_ROLE_AUDIENCE (distinct from
    INVALID_ROLE)."""
    tenant_id, root_id, _root_path = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="V6-Tenant"
    )
    platform_role = await make_role(audience="PLATFORM")
    body = _valid_create_body(
        tenant_id=tenant_id,
        role_assignments=[(platform_role.id, root_id)],
        name_suffix="v6",
    )
    resp = app_client.post(
        "/api/v1/tenant-users",
        json=body,
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "INVALID_ROLE_AUDIENCE"


async def test_v7_bare_uuid_legacy_element_rejected(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_org_node,
    make_role,
) -> None:
    """LOAD-BEARING (LD1 contract): a bare-UUID item in roles[] is
    rejected by Pydantic ahead of any business validation.

    Old 6.10.1-shape clients sending ``roles: ['uuid-string']`` get
    422 with Pydantic's default validation envelope."""
    tenant_id, _root_id, _root_path = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="V7-Tenant"
    )
    role = await make_role(audience="TENANT")
    legacy_body = {
        "tenant_id": str(tenant_id),
        "email": "v7@test.example.com",
        "full_name": "V7 Legacy",
        "roles": [str(role.id)],
    }
    resp = app_client.post(
        "/api/v1/tenant-users",
        json=legacy_body,
        headers=_auth(super_admin_jwt),
    )
    assert resp.status_code == 422, resp.text


async def test_p1_self_edit_with_new_roles_shape_returns_403(
    app_client,
    make_tenant,
    make_org_node,
    make_role,
    tenant_owner_jwt_factory,
) -> None:
    """LOAD-BEARING (LD8 regression under new shape): TENANT caller
    sending the new RoleAssignmentItem shape against own user_id ->
    403 SELF_EDIT_FORBIDDEN."""
    tenant_id, root_id, _root_path = await _seed_tenant_with_root(
        make_tenant, make_org_node, name="P1-6_14-Tenant"
    )
    role = await make_role(audience="TENANT")
    owner_jwt = await tenant_owner_jwt_factory(
        tenant_id,
        with_grants=[("ADMIN", "USERS", "CONFIGURE", "TENANT")],
    )
    own_user_id = _user_id_from_jwt(owner_jwt)

    resp = app_client.patch(
        f"/api/v1/tenant-users/{own_user_id}",
        json={"roles": _roles_payload([(role.id, root_id)])},
        headers=_auth(owner_jwt),
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "SELF_EDIT_FORBIDDEN"
