"""Integration tests for AuditLogsRepo (Step 6.16.3).

Real Postgres, real schema, real RLS. No FastAPI machinery. Sessions
come from ``get_tenant_session`` via ``platform_session`` and
``tenant_session_factory``.

R2 + R3 are load-bearing:
  - R2: cursor decode raises typed error (anti-500 contract).
  - R3: TENANT user_type queries the tenant table only (catches an
    accidental UNION exposure to tenant callers, which would be an
    information-disclosure regression).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from typing import Any

from admin_backend.errors import InvalidCursorError
from admin_backend.models.audit_log import AuditResultType
from admin_backend.models.tenant_user import ActorUserType
from admin_backend.repositories.audit_logs import (
    AuditLogsRepo,
    _decode_cursor,
    _encode_cursor,
)


@pytest.fixture
def repo() -> AuditLogsRepo:
    return AuditLogsRepo()


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


# ---- R1: encode/decode round-trip ----------------------------------------


def test_r1_cursor_encode_decode_roundtrip() -> None:
    """_encode_cursor and _decode_cursor are inverses."""
    ts = _now().replace(microsecond=0)
    row_id = uuid.uuid4()

    encoded = _encode_cursor(ts, row_id)
    decoded_ts, decoded_id = _decode_cursor(encoded)

    # Datetime round-trips through isoformat with microsecond
    # precision preserved.
    assert decoded_ts == ts
    assert decoded_id == row_id


# ---- R2: decode raises InvalidCursorError on malformed input ------------


def test_r2_decode_malformed_cursor_raises_invalid_cursor_error() -> None:
    """LOAD-BEARING. Cursor decoder maps every failure mode to the same
    typed exception so the router can convert to a clean 422 envelope.
    """
    # Not valid base64 (contains non-alphabet chars handled below; here
    # use an actually-malformed payload).
    with pytest.raises(InvalidCursorError):
        _decode_cursor("not!valid#base64$$")

    # Valid base64 but not JSON.
    import base64

    not_json = base64.urlsafe_b64encode(b"this is not json").decode("ascii")
    with pytest.raises(InvalidCursorError):
        _decode_cursor(not_json)

    # JSON but missing required keys.
    bad_shape = base64.urlsafe_b64encode(b'{"foo": "bar"}').decode("ascii")
    with pytest.raises(InvalidCursorError):
        _decode_cursor(bad_shape)

    # JSON with right keys but bad ts.
    bad_ts = base64.urlsafe_b64encode(
        b'{"ts": "not-a-date", "id": "not-a-uuid"}'
    ).decode("ascii")
    with pytest.raises(InvalidCursorError):
        _decode_cursor(bad_ts)

    # JSON with right keys, valid ts, bad uuid.
    bad_uuid = base64.urlsafe_b64encode(
        b'{"ts": "2026-01-01T00:00:00+00:00", "id": "xxx"}'
    ).decode("ascii")
    with pytest.raises(InvalidCursorError):
        _decode_cursor(bad_uuid)


# ---- R3: list() under TENANT user_type queries tenant table only -----


async def test_r3_list_under_tenant_user_type_queries_tenant_table_only(
    repo,
    make_tenant,
    make_tenant_activity_audit_log,
    make_platform_activity_audit_log,
    tenant_session_factory,
) -> None:
    """LOAD-BEARING. TENANT callers must NEVER reach platform-table rows
    through the list endpoint. The repo's dispatch on ``user_type`` is
    the only barrier (platform_activity_audit_logs has no RLS).

    Setup: insert one row in each table for the same tenant. Query
    under TENANT user_type. Assert only the tenant-table row comes
    back.
    """
    tenant = await make_tenant(name="R3-AuditDispatch")
    tenant_row = await make_tenant_activity_audit_log(
        tenant_id=tenant.id, tenant_name="R3-AuditDispatch"
    )
    # The platform row carries this tenant_id (tenant-creation-style).
    plat_row = await make_platform_activity_audit_log(
        tenant_id=tenant.id, tenant_name="R3-AuditDispatch"
    )

    async with tenant_session_factory(tenant.id) as session:
        result = await repo.list(session, user_type="TENANT", limit=50)

    ids = {r.id for r in result.items}
    assert tenant_row.id in ids
    assert plat_row.id not in ids
    # All returned rows must have scope='TENANT' under tenant dispatch.
    assert all(r.scope == "TENANT" for r in result.items)


# ---- R4: list() under PLATFORM user_type UNIONs both tables -----


async def test_r4_list_under_platform_user_type_unions_both_tables(
    repo,
    make_tenant,
    make_tenant_activity_audit_log,
    make_platform_activity_audit_log,
    platform_session,
) -> None:
    """PLATFORM callers see rows from BOTH tables via UNION ALL."""
    tenant = await make_tenant(name="R4-UnionTest")
    t_row = await make_tenant_activity_audit_log(
        tenant_id=tenant.id, tenant_name="R4-UnionTest"
    )
    p_row = await make_platform_activity_audit_log(
        tenant_id=tenant.id, tenant_name="R4-UnionTest"
    )

    result = await repo.list(
        platform_session,
        user_type="PLATFORM",
        limit=200,
        tenant_id=tenant.id,
    )
    ids = {r.id for r in result.items}
    assert t_row.id in ids
    assert p_row.id in ids

    scopes = {r.scope for r in result.items if r.id in {t_row.id, p_row.id}}
    assert scopes == {"PLATFORM", "TENANT"}


# ---- R5: list() with limit + has_more ------------------------------------


async def test_r5_list_respects_limit_and_sets_has_more(
    repo,
    make_tenant,
    make_tenant_activity_audit_log,
    platform_session,
) -> None:
    """Limit clamps page size; has_more reflects whether more rows exist
    beyond the page."""
    tenant = await make_tenant(name="R5-LimitTest")
    # Create 5 rows, all within the same tenant.
    created = []
    base = _now()
    for n in range(5):
        ts = base - timedelta(seconds=n)
        row = await make_tenant_activity_audit_log(
            tenant_id=tenant.id,
            tenant_name="R5-LimitTest",
            timestamp=ts,
        )
        created.append(row)

    # limit=3 -> 3 items + has_more=True (5 created).
    result = await repo.list(
        platform_session,
        user_type="PLATFORM",
        limit=3,
        tenant_id=tenant.id,
    )
    # Filter to just our created rows so we don't mix with other seed
    # rows under PLATFORM scope.
    our_ids = {r.id for r in created}
    matched = [r for r in result.items if r.id in our_ids]
    # We may have got <3 if other audit rows for this tenant exist
    # from another test, but our tenant is fresh; assert at most 3
    # plus has_more.
    assert len(result.items) == 3
    # has_more is set when our 5 + any others exceeded 3 -> True.
    assert result.has_more is True


# ---- R6: list() with from/to filters dates correctly ---------------------


async def test_r6_list_applies_from_to_timestamp_filter(
    repo,
    make_tenant,
    make_tenant_activity_audit_log,
    platform_session,
) -> None:
    """from/to bracket the page; rows outside the window are excluded."""
    tenant = await make_tenant(name="R6-DateFilter")
    base = datetime(2026, 5, 15, 12, 0, 0, tzinfo=timezone.utc)
    rows = []
    for n in range(5):
        ts = base + timedelta(hours=n)  # 12:00, 13:00, ..., 16:00
        rows.append(
            await make_tenant_activity_audit_log(
                tenant_id=tenant.id,
                tenant_name="R6-DateFilter",
                timestamp=ts,
            )
        )

    # Window: 13:00 - 15:00 inclusive -> should match rows[1], [2], [3].
    from_ts = base + timedelta(hours=1)
    to_ts = base + timedelta(hours=3)
    result = await repo.list(
        platform_session,
        user_type="PLATFORM",
        limit=200,
        from_ts=from_ts,
        to_ts=to_ts,
        tenant_id=tenant.id,
    )

    our_ids = {r.id for r in rows}
    matched = [r.id for r in result.items if r.id in our_ids]
    assert set(matched) == {rows[1].id, rows[2].id, rows[3].id}


# ---- R7: get_by_id returns from tenant table when row exists there -------


async def test_r7_get_by_id_returns_tenant_table_row(
    repo,
    make_tenant,
    make_tenant_activity_audit_log,
    platform_session,
) -> None:
    """get_by_id finds rows in tenant_activity_audit_logs."""
    tenant = await make_tenant(name="R7-DetailTenant")
    row = await make_tenant_activity_audit_log(
        tenant_id=tenant.id, tenant_name="R7-DetailTenant"
    )

    fetched = await repo.get_by_id(platform_session, audit_row_id=row.id)
    assert fetched is not None
    assert fetched.id == row.id
    assert fetched.scope == "TENANT"


# ---- R8: get_by_id falls back to platform table --------------------------


async def test_r8_get_by_id_falls_back_to_platform_table(
    repo,
    make_tenant,
    make_platform_activity_audit_log,
    platform_session,
) -> None:
    """get_by_id falls back to platform table when the row lives there."""
    tenant = await make_tenant(name="R8-DetailPlatform")
    row = await make_platform_activity_audit_log(
        tenant_id=tenant.id, tenant_name="R8-DetailPlatform"
    )

    fetched = await repo.get_by_id(platform_session, audit_row_id=row.id)
    assert fetched is not None
    assert fetched.id == row.id
    assert fetched.scope == "PLATFORM"

    # Probing a non-existent UUID returns None.
    missing = await repo.get_by_id(platform_session, audit_row_id=uuid.uuid4())
    assert missing is None


# ---------------------------------------------------------------------------
# R_N1 / R_N2 / R_N3 : Step 6.16.7 LD10 + LD11 — new columns SELECT-able
# and ``what`` composed correctly across resource_type x subtype combos.
# ---------------------------------------------------------------------------


async def test_r_n1_repo_select_returns_new_audit_columns(
    repo,
    make_tenant,
    make_tenant_activity_audit_log,
    platform_session,
) -> None:
    """LOAD-BEARING (Step 6.16.7 LD10): the repo's SELECT projection
    includes ``actor_organization_name``, ``actor_roles``, and
    ``resource_subtype``. Each surfaces on ``AuditActivityDetailRow``.
    """
    tenant = await make_tenant(name="RN1-Cols")
    row = await make_tenant_activity_audit_log(
        tenant_id=tenant.id,
        tenant_name="RN1-Cols",
        actor_organization_name="RN1-Cols",
        actor_roles="Owner, Promotions Assistant",
        resource_type="ORG_NODE",
        resource_subtype="REGION",
        resource_label="Texas Region",
    )

    fetched = await repo.get_by_id(platform_session, audit_row_id=row.id)
    assert fetched is not None
    assert fetched.actor_organization_name == "RN1-Cols"
    assert fetched.actor_roles == "Owner, Promotions Assistant"
    assert fetched.resource_subtype == "REGION"


async def test_r_n2_what_composition_via_router_mapper() -> None:
    """LOAD-BEARING (Step 6.16.7 LD11): ``what`` composes per the LD12
    type-label mapping across each resource_type / subtype combination.
    """
    from admin_backend.repositories.audit_logs import AuditActivityDetailRow
    from admin_backend.routers.v1.audit import _list_item_from_row

    def _row(**overrides: Any) -> AuditActivityDetailRow:
        base: dict[str, Any] = {
            "id": uuid.uuid4(),
            "timestamp": _now(),
            "tenant_id": uuid.uuid4(),
            "tenant_name": "T",
            "actor_user_id": uuid.uuid4(),
            "actor_user_type": ActorUserType.PLATFORM,
            "actor_display_name": "alice@x.com",
            "actor_organization_name": "Platform-Ithina",
            "actor_roles": "Super Admin",
            "resource_type": "TENANT",
            "resource_id": uuid.uuid4(),
            "resource_label": "Acme",
            "resource_subtype": None,
            "action": "UPDATE",
            "action_label": "Edited",
            "result_type": AuditResultType.SUCCESS,
            "result_label": "Success",
            "request_id": uuid.uuid4(),
            "details": {},
            "scope": "PLATFORM",
        }
        base.update(overrides)
        return AuditActivityDetailRow(**base)

    # Walks the full LD12 table.
    cases: list[tuple[str, str | None, str, str]] = [
        ("TENANT", None, "Acme", "Tenant: Acme"),
        ("TENANT_USER", None, "ada@x.com", "User: ada@x.com"),
        ("ROLE", None, "Owner", "Role: Owner"),
        ("MODULE_ACCESS", None, "Pricing OS", "Module: Pricing OS"),
        ("STORE", None, "Downtown Buc-ee's", "Store: Downtown Buc-ee's"),
        # ORG_NODE subtypes.
        ("ORG_NODE", "TENANT", "Buc-ee's", "Tenant root: Buc-ee's"),
        ("ORG_NODE", "BUSINESS_UNIT", "Retail Ops", "Business unit: Retail Ops"),
        ("ORG_NODE", "HQ", "Phoenix HQ", "HQ: Phoenix HQ"),
        ("ORG_NODE", "COUNTRY", "United States", "Country: United States"),
        ("ORG_NODE", "REGION", "Texas Region", "Region: Texas Region"),
        ("ORG_NODE", "STORE", "Downtown Buc-ee's", "Store: Downtown Buc-ee's"),
        ("ORG_NODE", "DEPARTMENT", "Bakery", "Department: Bakery"),
        # Pre-6.16.7 historical fallback (NULL subtype on ORG_NODE).
        ("ORG_NODE", None, "Old Node", "Org node: Old Node"),
    ]
    for rt, sub, label, expected_what in cases:
        item = _list_item_from_row(
            _row(resource_type=rt, resource_subtype=sub, resource_label=label)
        )
        assert item.what == expected_what, (
            f"({rt}, {sub}) composed wrong: got {item.what!r}, "
            f"want {expected_what!r}"
        )


async def test_r_n3_what_composition_null_resource_label_renders_dash() -> None:
    """LD11: NULL ``resource_label`` (failure-path rows without a
    resource identity) renders as ``"<Type label>: -"``.
    """
    from admin_backend.repositories.audit_logs import AuditActivityDetailRow
    from admin_backend.routers.v1.audit import _list_item_from_row

    row = AuditActivityDetailRow(
        id=uuid.uuid4(),
        timestamp=_now(),
        tenant_id=None,
        tenant_name=None,
        actor_user_id=uuid.uuid4(),
        actor_user_type=ActorUserType.PLATFORM,
        actor_display_name="alice@x.com",
        actor_organization_name="Platform-Ithina",
        actor_roles="Super Admin",
        resource_type="STORE",
        resource_id=None,
        resource_label=None,
        resource_subtype=None,
        action="CREATE",
        action_label="Created",
        result_type=AuditResultType.VALIDATION_FAILED,
        result_label="Validation failed",
        request_id=uuid.uuid4(),
        details={},
        scope="PLATFORM",
    )
    item = _list_item_from_row(row)
    assert item.what == "Store: -"
