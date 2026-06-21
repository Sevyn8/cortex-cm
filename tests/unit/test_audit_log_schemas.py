"""Schema unit tests for audit log read endpoints (Step 6.16.3).

Wire-shape contracts: every model uses ``extra="forbid"`` per LD11; the
8-field list item and 16-field detail shapes are deliberately frozen
so a future addition surfaces as a test failure rather than silent
schema drift.

S1-S4 mirror existing schema test patterns in
``tests/unit/test_module_access_schemas.py`` and elsewhere.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from admin_backend.models.audit_log import AuditResultType
from admin_backend.models.tenant_user import ActorUserType
from admin_backend.schemas.audit_log import (
    AuditActivitiesListResponse,
    AuditActivityDetail,
    AuditActivityListItem,
    CursorPagination,
)


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def test_s1_cursor_pagination_serialises_correctly() -> None:
    """CursorPagination serialises all 4 fields, accepts None for cursors."""
    p = CursorPagination(
        next_cursor="abc123==",
        prev_cursor=None,
        limit=50,
        has_more=True,
    )
    dumped = p.model_dump()
    assert dumped == {
        "next_cursor": "abc123==",
        "prev_cursor": None,
        "limit": 50,
        "has_more": True,
    }

    # extra forbidden
    with pytest.raises(Exception):
        CursorPagination(
            next_cursor="x",
            prev_cursor=None,
            limit=10,
            has_more=False,
            unknown_field="boom",  # type: ignore[call-arg]
        )


def test_s2_audit_activity_list_item_has_exactly_14_fields() -> None:
    """Step 6.16.7 LD10: list item shape grows from 8 to 14 fields.

    Additive only; the existing 8 fields keep their shape, 6 new ones
    join them (``actor_organization_name``, ``actor_roles``, ``what``,
    ``resource_type``, ``resource_subtype``, ``result_type``).
    """
    fields = set(AuditActivityListItem.model_fields.keys())
    expected = {
        "id",
        "timestamp",
        "actor_display_name",
        "actor_organization_name",
        "actor_roles",
        "action_label",
        "what",
        "resource_label",
        "resource_type",
        "resource_subtype",
        "result_label",
        "result_type",
        "scope",
        "tenant_name",
    }
    assert fields == expected, (
        f"AuditActivityListItem field set drifted. "
        f"Got: {sorted(fields)}, expected: {sorted(expected)}"
    )

    # Construct one to verify types accept the documented values.
    item = AuditActivityListItem(
        id=uuid4(),
        timestamp=_now(),
        actor_display_name="Alice",
        actor_organization_name="Platform-Ithina",
        actor_roles="Super Admin",
        action_label="Created",
        what="Tenant: Acme Corp",
        resource_label="Acme Corp",
        resource_type="TENANT",
        resource_subtype=None,
        result_label="Success",
        result_type=AuditResultType.SUCCESS,
        scope="PLATFORM",
        tenant_name="Acme Corp",
    )
    # Nullable fields accept None.
    item_no_resource = AuditActivityListItem(
        id=uuid4(),
        timestamp=_now(),
        actor_display_name="Bob",
        actor_organization_name="Acme",
        actor_roles="Owner",
        action_label="Edited",
        what="User: -",
        resource_label=None,
        resource_type="TENANT_USER",
        resource_subtype=None,
        result_label="Permission denied",
        result_type=AuditResultType.PERMISSION_DENIED,
        scope="TENANT",
        tenant_name=None,
    )
    assert item.resource_label == "Acme Corp"
    assert item_no_resource.resource_label is None
    assert item_no_resource.tenant_name is None


def test_s3_audit_activity_detail_has_exactly_19_fields() -> None:
    """Step 6.16.7 LD10: detail shape grows from 16 to 19 fields.

    Additive only; the existing 16 fields keep their shape, 3 new
    stored-column fields join them (``actor_organization_name``,
    ``actor_roles``, ``resource_subtype``).
    """
    fields = set(AuditActivityDetail.model_fields.keys())
    expected = {
        "id",
        "timestamp",
        "tenant_id",
        "tenant_name",
        "actor_user_id",
        "actor_user_type",
        "actor_display_name",
        "actor_organization_name",
        "actor_roles",
        "resource_type",
        "resource_id",
        "resource_label",
        "resource_subtype",
        "action",
        "action_label",
        "result_type",
        "result_label",
        "request_id",
        "details",
    }
    assert fields == expected, (
        f"AuditActivityDetail field set drifted. "
        f"Got: {sorted(fields)}, expected: {sorted(expected)}"
    )

    # Construct one minimal valid row.
    detail = AuditActivityDetail(
        id=uuid4(),
        timestamp=_now(),
        tenant_id=uuid4(),
        tenant_name="Acme",
        actor_user_id=uuid4(),
        actor_user_type=ActorUserType.PLATFORM,
        actor_display_name="Alice",
        actor_organization_name="Platform-Ithina",
        actor_roles="Super Admin",
        resource_type="TENANT",
        resource_id=uuid4(),
        resource_label="Acme Corp",
        resource_subtype=None,
        action="UPDATE",
        action_label="Edited",
        result_type=AuditResultType.SUCCESS,
        result_label="Success",
        request_id=uuid4(),
        details={"before": {"status": "TRIAL"}, "after": {"status": "ACTIVE"}},
    )
    # Round-trip through model_dump.
    dumped = detail.model_dump()
    assert dumped["action"] == "UPDATE"
    # enum serialisation
    assert dumped["actor_user_type"] in (
        "PLATFORM",
        ActorUserType.PLATFORM,
    )


def test_s4_audit_activities_list_response_rejects_extra_fields() -> None:
    """AuditActivitiesListResponse rejects unknown fields (extra='forbid')."""
    pagination = CursorPagination(
        next_cursor=None,
        prev_cursor=None,
        limit=50,
        has_more=False,
    )

    # Empty list is valid.
    resp = AuditActivitiesListResponse(items=[], pagination=pagination)
    dumped = resp.model_dump()
    assert dumped["items"] == []
    assert dumped["pagination"]["limit"] == 50

    # Extra field rejected.
    with pytest.raises(Exception):
        AuditActivitiesListResponse(
            items=[],
            pagination=pagination,
            total=99,  # type: ignore[call-arg]
        )
