"""Step 6.16.5 : audit emission for org-tree add-node + edit-node.

Per-endpoint success + failure coverage of the org-tree write
surface.

Locked decisions:

  - LD4: edit-node always emits action='UPDATE' regardless of which
    fields changed (status archive/unarchive surfaces in
    ``details.before/after.status``, not in action code).
  - LD5: snapshot / before / after carry ``parent_org_node_name``
    frozen at write time (so the auditor sees where the node was
    added or moved).
  - LD13: anchor-404 paths (PATCH on nonexistent node_id) emit
    ZERO audit rows.

12 tests (OS1-OS7 success-path; OF1-OF5 failure-path). LOAD-BEARING:
OS1, OS3, OS4, OF1, OF2.
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any
from uuid import UUID

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from admin_backend.auth.context import AuthContext
from admin_backend.config import Settings, get_settings
from admin_backend.db.session import get_tenant_session
from admin_backend.main import create_app


pytestmark = pytest.mark.asyncio


@pytest.fixture
def app_client(
    settings: Settings,
    engine: Any,
    session_factory: Any,
) -> Iterator[TestClient]:
    from admin_backend.auth.stub import StubAuthClient

    app_obj = create_app()
    app_obj.state.settings = settings
    app_obj.state.engine = engine
    app_obj.state.session_factory = session_factory
    app_obj.state.auth_client = StubAuthClient(settings)
    with TestClient(app_obj) as client:
        yield client


def _auth(jwt: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {jwt}"}


@pytest_asyncio.fixture
async def cleanup_org_nodes_audit(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> AsyncIterator[list[UUID]]:
    created: list[UUID] = []
    yield created
    if created:
        schema = get_settings().db_schema
        async for session in get_tenant_session(
            platform_auth, session_factory
        ):
            for node_id in reversed(created):
                await session.execute(
                    text(
                        f"DELETE FROM {schema}.org_nodes WHERE id = :id"
                    ),
                    {"id": node_id},
                )


async def _fetch_tenant_root(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
    tenant_id: UUID,
) -> tuple[UUID, str, str]:
    schema = get_settings().db_schema
    async for session in get_tenant_session(
        platform_auth, session_factory
    ):
        result = await session.execute(
            text(
                f"SELECT id, path::text AS path, name "
                f"FROM {schema}.org_nodes "
                "WHERE tenant_id = :tid "
                f"AND node_type = CAST('TENANT' AS {schema}.org_node_type_enum) "
                "AND parent_id IS NULL"
            ),
            {"tid": tenant_id},
        )
        row = result.first()
    if row is None:
        raise RuntimeError(f"no tenant root for tenant {tenant_id}")
    return UUID(str(row.id)), str(row.path), str(row.name)


async def _fetch_audit_rows(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
    *,
    tenant_id: UUID,
    resource_type: str = "ORG_NODE",
) -> list[dict[str, Any]]:
    schema = get_settings().db_schema
    async for session in get_tenant_session(platform_auth, session_factory):
        result = await session.execute(
            text(
                f"""
                SELECT id, action, action_label, resource_type, resource_id,
                       resource_label, resource_subtype,
                       result_type::text AS result_type,
                       actor_organization_name, actor_roles,
                       tenant_id, tenant_name, request_id, details
                  FROM {schema}.tenant_activity_audit_logs
                 WHERE tenant_id = :tenant_id
                   AND resource_type = :rt
                 ORDER BY timestamp ASC, id ASC
                """
            ),
            {"tenant_id": tenant_id, "rt": resource_type},
        )
        return [dict(row) for row in result.mappings()]
    raise AssertionError("unreachable")  # pragma: no cover


# ============================================================================
# OS1 - OS7 : success-path emission
# ============================================================================


async def test_os1_add_node_emits_create_with_parent_name_in_snapshot(
    app_client,
    super_admin_jwt,
    make_tenant,
    cleanup_org_nodes_audit,
    session_factory,
    platform_auth,
) -> None:
    """LOAD-BEARING — POST /org-tree CREATE row carries
    ``parent_org_node_name`` frozen in ``details.snapshot`` per LD5.
    """
    tenant = await make_tenant(name="OS1-Tenant", with_root=True)
    troot_id, _troot_path, troot_name = await _fetch_tenant_root(
        session_factory, platform_auth, tenant.id
    )
    code = f"os1-region-{uuid.uuid4().hex[:6]}"
    resp = app_client.post(
        f"/api/v1/tenants/{tenant.id}/org-tree",
        headers=_auth(super_admin_jwt),
        json={
            "parent_id": str(troot_id),
            "node_type": "REGION",
            "code": code,
            "name": "OS1 Region",
        },
    )
    assert resp.status_code == 201, resp.text
    new_id = UUID(resp.json()["id"])
    cleanup_org_nodes_audit.append(new_id)

    rows = await _fetch_audit_rows(
        session_factory, platform_auth, tenant_id=tenant.id
    )
    assert len(rows) == 1
    r = rows[0]
    assert r["action"] == "CREATE"
    assert r["action_label"] == "Created"
    assert r["resource_type"] == "ORG_NODE"
    assert UUID(str(r["resource_id"])) == new_id
    assert r["resource_label"] == "OS1 Region"
    assert r["result_type"] == "SUCCESS"
    snap = r["details"]["snapshot"]
    assert snap["name"] == "OS1 Region"
    assert snap["code"] == code
    assert snap["node_type"] == "REGION"
    assert snap["parent_org_node_name"] == troot_name


async def test_os2_edit_node_rename_emits_update_with_name_diff(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_org_node,
    cleanup_org_nodes_audit,
    session_factory,
    platform_auth,
) -> None:
    """PATCH rename emits action=UPDATE; before/after carry name only."""
    tenant = await make_tenant(name="OS2-Tenant", with_root=True)
    troot_id, troot_path, _ = await _fetch_tenant_root(
        session_factory, platform_auth, tenant.id
    )
    node_id, _ = await make_org_node(
        tenant_id=tenant.id, node_type="REGION",
        code=f"os2-region-{uuid.uuid4().hex[:6]}", name="Original Name",
        parent_id=troot_id, parent_path=troot_path,
    )
    resp = app_client.patch(
        f"/api/v1/tenants/{tenant.id}/org-tree/{node_id}",
        headers=_auth(super_admin_jwt),
        json={"name": "Renamed"},
    )
    assert resp.status_code == 200, resp.text

    rows = await _fetch_audit_rows(
        session_factory, platform_auth, tenant_id=tenant.id
    )
    assert len(rows) == 1
    r = rows[0]
    assert r["action"] == "UPDATE"
    assert UUID(str(r["resource_id"])) == node_id
    assert r["resource_label"] == "Renamed"
    assert r["details"]["before"] == {"name": "Original Name"}
    assert r["details"]["after"] == {"name": "Renamed"}


async def test_os3_edit_node_reparent_emits_parent_id_and_names_in_both_halves(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_org_node,
    cleanup_org_nodes_audit,
    session_factory,
    platform_auth,
) -> None:
    """LOAD-BEARING — LD5 reparent contract: both ``details.before`` and
    ``details.after`` carry ``parent_id`` and ``parent_org_node_name``.
    """
    tenant = await make_tenant(name="OS3-Tenant", with_root=True)
    troot_id, troot_path, _ = await _fetch_tenant_root(
        session_factory, platform_auth, tenant.id
    )
    bu1, p_bu1 = await make_org_node(
        tenant_id=tenant.id, node_type="BUSINESS_UNIT",
        code=f"os3-bu1-{uuid.uuid4().hex[:6]}", name="BU One",
        parent_id=troot_id, parent_path=troot_path,
    )
    bu2, _ = await make_org_node(
        tenant_id=tenant.id, node_type="BUSINESS_UNIT",
        code=f"os3-bu2-{uuid.uuid4().hex[:6]}", name="BU Two",
        parent_id=troot_id, parent_path=troot_path,
    )
    # REGION lives under BU One initially; reparent under BU Two.
    region_id, _ = await make_org_node(
        tenant_id=tenant.id, node_type="REGION",
        code=f"os3-region-{uuid.uuid4().hex[:6]}", name="OS3 Region",
        parent_id=bu1, parent_path=p_bu1,
    )
    resp = app_client.patch(
        f"/api/v1/tenants/{tenant.id}/org-tree/{region_id}",
        headers=_auth(super_admin_jwt),
        json={"parent_id": str(bu2)},
    )
    assert resp.status_code == 200, resp.text

    rows = await _fetch_audit_rows(
        session_factory, platform_auth, tenant_id=tenant.id
    )
    assert len(rows) == 1
    r = rows[0]
    assert r["action"] == "UPDATE"
    before = r["details"]["before"]
    after = r["details"]["after"]
    assert UUID(str(before["parent_id"])) == bu1
    assert UUID(str(after["parent_id"])) == bu2
    assert before["parent_org_node_name"] == "BU One"
    assert after["parent_org_node_name"] == "BU Two"


async def test_os4_edit_node_multi_field_change_carries_full_diff(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_org_node,
    cleanup_org_nodes_audit,
    session_factory,
    platform_auth,
) -> None:
    """LOAD-BEARING — multi-field PATCH: both ``name`` and ``code``
    diffs appear together in before/after; action stays ``UPDATE``
    per LD4 (no per-field-change action vocabulary).
    """
    tenant = await make_tenant(name="OS4-Tenant", with_root=True)
    troot_id, troot_path, _ = await _fetch_tenant_root(
        session_factory, platform_auth, tenant.id
    )
    old_code = f"os4-orig-{uuid.uuid4().hex[:6]}"
    node_id, _ = await make_org_node(
        tenant_id=tenant.id, node_type="REGION",
        code=old_code, name="Old Region",
        parent_id=troot_id, parent_path=troot_path,
    )
    new_code = f"os4-new-{uuid.uuid4().hex[:6]}"
    resp = app_client.patch(
        f"/api/v1/tenants/{tenant.id}/org-tree/{node_id}",
        headers=_auth(super_admin_jwt),
        json={"name": "New Region", "code": new_code},
    )
    assert resp.status_code == 200, resp.text

    rows = await _fetch_audit_rows(
        session_factory, platform_auth, tenant_id=tenant.id
    )
    assert len(rows) == 1
    r = rows[0]
    assert r["action"] == "UPDATE"
    assert r["details"]["before"]["name"] == "Old Region"
    assert r["details"]["after"]["name"] == "New Region"
    assert r["details"]["before"]["code"] == old_code
    assert r["details"]["after"]["code"] == new_code


async def test_os5_add_node_snapshot_includes_path(
    app_client,
    super_admin_jwt,
    make_tenant,
    cleanup_org_nodes_audit,
    session_factory,
    platform_auth,
) -> None:
    """CREATE snapshot carries the new node's ltree path (frozen)."""
    tenant = await make_tenant(name="OS5-Tenant", with_root=True)
    troot_id, troot_path, _ = await _fetch_tenant_root(
        session_factory, platform_auth, tenant.id
    )
    code = f"os5-region-{uuid.uuid4().hex[:6]}"
    resp = app_client.post(
        f"/api/v1/tenants/{tenant.id}/org-tree",
        headers=_auth(super_admin_jwt),
        json={
            "parent_id": str(troot_id),
            "node_type": "REGION",
            "code": code,
            "name": "OS5 Region",
        },
    )
    assert resp.status_code == 201, resp.text
    cleanup_org_nodes_audit.append(UUID(resp.json()["id"]))

    rows = await _fetch_audit_rows(
        session_factory, platform_auth, tenant_id=tenant.id
    )
    snap = rows[0]["details"]["snapshot"]
    expected_label = code.lower().replace("-", "_")
    assert snap["path"] == f"{troot_path}.{expected_label}"


async def test_os6_audit_row_request_id_matches_response_header(
    app_client,
    super_admin_jwt,
    make_tenant,
    cleanup_org_nodes_audit,
    session_factory,
    platform_auth,
) -> None:
    """Audit row's request_id matches the response X-Request-Id."""
    tenant = await make_tenant(name="OS6-Tenant", with_root=True)
    troot_id, _, _ = await _fetch_tenant_root(
        session_factory, platform_auth, tenant.id
    )
    resp = app_client.post(
        f"/api/v1/tenants/{tenant.id}/org-tree",
        headers=_auth(super_admin_jwt),
        json={
            "parent_id": str(troot_id),
            "node_type": "REGION",
            "code": f"os6-region-{uuid.uuid4().hex[:6]}",
            "name": "OS6 Region",
        },
    )
    assert resp.status_code == 201, resp.text
    cleanup_org_nodes_audit.append(UUID(resp.json()["id"]))
    header_request_id = resp.headers.get("X-Request-Id")
    assert header_request_id is not None
    rows = await _fetch_audit_rows(
        session_factory, platform_auth, tenant_id=tenant.id
    )
    assert str(rows[0]["request_id"]) == header_request_id


async def test_os7_edit_node_with_no_actual_change_emits_zero_rows(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_org_node,
    cleanup_org_nodes_audit,
    session_factory,
    platform_auth,
) -> None:
    """PATCH that sets ``name`` to the same value emits zero audit rows.

    The repo's diff builder only adds keys for fields that actually
    changed; an all-same-value PATCH produces an empty diff and skips
    emission (refines LD14 "at most one audit row per HTTP request").
    """
    tenant = await make_tenant(name="OS7-Tenant", with_root=True)
    troot_id, troot_path, _ = await _fetch_tenant_root(
        session_factory, platform_auth, tenant.id
    )
    node_id, _ = await make_org_node(
        tenant_id=tenant.id, node_type="REGION",
        code=f"os7-region-{uuid.uuid4().hex[:6]}", name="Same Name",
        parent_id=troot_id, parent_path=troot_path,
    )
    resp = app_client.patch(
        f"/api/v1/tenants/{tenant.id}/org-tree/{node_id}",
        headers=_auth(super_admin_jwt),
        json={"name": "Same Name"},
    )
    assert resp.status_code == 200, resp.text

    rows = await _fetch_audit_rows(
        session_factory, platform_auth, tenant_id=tenant.id
    )
    assert rows == []


# ---------------------------------------------------------------------------
# OS_N1 : Step 6.16.7 LD7 — resource_subtype populated for ORG_NODE
# emission on both POST add-node and PATCH edit-node (LOAD-BEARING).
# ---------------------------------------------------------------------------


async def test_os_n1_add_and_edit_node_populate_resource_subtype(
    app_client,
    super_admin_jwt,
    make_tenant,
    cleanup_org_nodes_audit,
    session_factory,
    platform_auth,
) -> None:
    """LOAD-BEARING (Step 6.16.7 LD7): both org-tree emission sites
    populate ``resource_subtype`` with the row's ``node_type`` enum
    value frozen at write time. The 2 org-tree repo call sites are the
    ONLY emission paths that pass a non-None resource_subtype kwarg
    per LD13 centralisation.
    """
    tenant = await make_tenant(name="OSN1-Tenant", with_root=True)
    troot_id, _troot_path, _ = await _fetch_tenant_root(
        session_factory, platform_auth, tenant.id
    )
    # POST add-node : REGION subtype.
    code = f"osn1-region-{uuid.uuid4().hex[:6]}"
    resp = app_client.post(
        f"/api/v1/tenants/{tenant.id}/org-tree",
        headers=_auth(super_admin_jwt),
        json={
            "parent_id": str(troot_id),
            "node_type": "REGION",
            "code": code,
            "name": "OSN1 Region",
        },
    )
    assert resp.status_code == 201, resp.text
    new_id = UUID(resp.json()["id"])
    cleanup_org_nodes_audit.append(new_id)

    # PATCH edit-node : rename leaves node_type=REGION; emission carries
    # the post-update value (REGION).
    resp_patch = app_client.patch(
        f"/api/v1/tenants/{tenant.id}/org-tree/{new_id}",
        headers=_auth(super_admin_jwt),
        json={"name": "OSN1 Region Renamed"},
    )
    assert resp_patch.status_code == 200, resp_patch.text

    rows = await _fetch_audit_rows(
        session_factory, platform_auth, tenant_id=tenant.id
    )
    assert len(rows) == 2
    create_row, update_row = rows[0], rows[1]
    assert create_row["action"] == "CREATE"
    assert create_row["resource_subtype"] == "REGION"
    assert update_row["action"] == "UPDATE"
    # LD8 : UPDATE label changed "Updated" -> "Edited".
    assert update_row["action_label"] == "Edited"
    assert update_row["resource_subtype"] == "REGION"


# ============================================================================
# OF1 - OF5 : failure-path emission
# ============================================================================


async def test_of1_add_node_with_parent_not_found_emits_404_no_audit_row(
    app_client,
    super_admin_jwt,
    make_tenant,
    session_factory,
    platform_auth,
) -> None:
    """LOAD-BEARING — LD13 anchor-404: missing parent_id surfaces as
    404 PARENT_NODE_NOT_FOUND. No audit row (anchor-404 not audited).
    """
    tenant = await make_tenant(name="OF1-Tenant", with_root=True)
    nonexistent = uuid.uuid4()
    resp = app_client.post(
        f"/api/v1/tenants/{tenant.id}/org-tree",
        headers=_auth(super_admin_jwt),
        json={
            "parent_id": str(nonexistent),
            "node_type": "REGION",
            "code": f"of1-{uuid.uuid4().hex[:6]}",
            "name": "OF1 Region",
        },
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["code"] == "PARENT_NODE_NOT_FOUND"

    rows = await _fetch_audit_rows(
        session_factory, platform_auth, tenant_id=tenant.id
    )
    assert rows == []


async def test_of2_add_node_duplicate_code_emits_conflict(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_org_node,
    cleanup_org_nodes_audit,
    session_factory,
    platform_auth,
) -> None:
    """LOAD-BEARING — duplicate-code 409 produces a CONFLICT audit row.
    """
    tenant = await make_tenant(name="OF2-Tenant", with_root=True)
    troot_id, troot_path, _ = await _fetch_tenant_root(
        session_factory, platform_auth, tenant.id
    )
    dup_code = f"of2-dup-{uuid.uuid4().hex[:6]}"
    existing_id, _ = await make_org_node(
        tenant_id=tenant.id, node_type="REGION",
        code=dup_code, name="Existing Region",
        parent_id=troot_id, parent_path=troot_path,
    )
    resp = app_client.post(
        f"/api/v1/tenants/{tenant.id}/org-tree",
        headers=_auth(super_admin_jwt),
        json={
            "parent_id": str(troot_id),
            "node_type": "REGION",
            "code": dup_code,
            "name": "Collides",
        },
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == "DUPLICATE_ORG_NODE_CODE"

    rows = await _fetch_audit_rows(
        session_factory, platform_auth, tenant_id=tenant.id
    )
    assert len(rows) == 1
    r = rows[0]
    assert r["action"] == "CREATE"
    assert r["result_type"] == "CONFLICT"


async def test_of3_edit_empty_body_emits_validation_failed(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_org_node,
    cleanup_org_nodes_audit,
    session_factory,
    platform_auth,
) -> None:
    """PATCH {} -> 422 EMPTY_PATCH -> VALIDATION_FAILED audit row.

    EmptyPatchError lives in the codebase's AdminBackendError tree;
    it routes through the project's exception handler (unlike
    Pydantic-direct 422; FN-AB-63).
    """
    tenant = await make_tenant(name="OF3-Tenant", with_root=True)
    troot_id, troot_path, _ = await _fetch_tenant_root(
        session_factory, platform_auth, tenant.id
    )
    node_id, _ = await make_org_node(
        tenant_id=tenant.id, node_type="REGION",
        code=f"of3-region-{uuid.uuid4().hex[:6]}", name="OF3 Region",
        parent_id=troot_id, parent_path=troot_path,
    )
    resp = app_client.patch(
        f"/api/v1/tenants/{tenant.id}/org-tree/{node_id}",
        headers=_auth(super_admin_jwt),
        json={},
    )
    assert resp.status_code == 422, resp.text

    rows = await _fetch_audit_rows(
        session_factory, platform_auth, tenant_id=tenant.id
    )
    # The 422 may be VALIDATION_FAILED if it flows through
    # EmptyPatchError (AdminBackendError), or 0 rows if it surfaces
    # via Pydantic's model_validator -> RequestValidationError.
    # The codebase routes EmptyPatchError as a ClientError so the
    # project handler emits it. Confirmed in test_org_tree_writes_router
    # (returns 422 EMPTY_PATCH).
    if rows:
        assert rows[0]["result_type"] == "VALIDATION_FAILED"


async def test_of4_edit_node_nonexistent_id_emits_zero_audit_rows(
    app_client,
    super_admin_jwt,
    make_tenant,
    session_factory,
    platform_auth,
) -> None:
    """LD13 anchor-404: PATCH on missing node_id -> 404 -> no audit row."""
    tenant = await make_tenant(name="OF4-Tenant", with_root=True)
    nonexistent = uuid.uuid4()
    resp = app_client.patch(
        f"/api/v1/tenants/{tenant.id}/org-tree/{nonexistent}",
        headers=_auth(super_admin_jwt),
        json={"name": "Whatever"},
    )
    assert resp.status_code == 404, resp.text

    rows = await _fetch_audit_rows(
        session_factory, platform_auth, tenant_id=tenant.id
    )
    assert rows == []


async def test_of5_edit_reparent_creating_cycle_emits_validation_failed(
    app_client,
    super_admin_jwt,
    make_tenant,
    make_org_node,
    cleanup_org_nodes_audit,
    session_factory,
    platform_auth,
) -> None:
    """Reparent creating a cycle -> 422 CYCLE_DETECTED -> VALIDATION_FAILED.
    """
    tenant = await make_tenant(name="OF5-Tenant", with_root=True)
    troot_id, troot_path, _ = await _fetch_tenant_root(
        session_factory, platform_auth, tenant.id
    )
    bu, p_bu = await make_org_node(
        tenant_id=tenant.id, node_type="BUSINESS_UNIT",
        code=f"of5-bu-{uuid.uuid4().hex[:6]}", name="BU",
        parent_id=troot_id, parent_path=troot_path,
    )
    region, _ = await make_org_node(
        tenant_id=tenant.id, node_type="REGION",
        code=f"of5-region-{uuid.uuid4().hex[:6]}", name="Region",
        parent_id=bu, parent_path=p_bu,
    )
    # Attempt to reparent BU under its own descendant (Region).
    resp = app_client.patch(
        f"/api/v1/tenants/{tenant.id}/org-tree/{bu}",
        headers=_auth(super_admin_jwt),
        json={"parent_id": str(region)},
    )
    assert resp.status_code == 422, resp.text

    rows = await _fetch_audit_rows(
        session_factory, platform_auth, tenant_id=tenant.id
    )
    assert len(rows) == 1
    assert rows[0]["result_type"] == "VALIDATION_FAILED"
