"""Step 6.14 unit tests for the tenant-users request schemas.

Pure Pydantic-layer tests with no DB or HTTP. Covers the
``RoleAssignmentItem`` shape and the ``roles`` field on the two
request models.

  S1 — ``RoleAssignmentItem`` rejects extra fields (``extra="forbid"``).
  S2 — ``RoleAssignmentItem`` requires both ``role_id`` AND
       ``org_node_id``.
  S3 — POST ``roles=[]`` rejected (``min_length=1``); PATCH
       ``roles=[]`` accepted (empty-list-revoke-all semantics).
  S4 — Bare-UUID legacy element rejected (LD1 contract).
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from admin_backend.schemas.tenant_user import (
    RoleAssignmentItem,
    TenantUserCreateRequest,
    TenantUserPatchRequest,
)


def test_s1_role_assignment_item_rejects_extra_fields() -> None:
    """``RoleAssignmentItem`` carries exactly two fields; any extra is
    a 422-class violation. Guards against future shape drift where a
    new field gets silently accepted on the wire."""
    with pytest.raises(ValidationError):
        RoleAssignmentItem(
            role_id=uuid4(),
            org_node_id=uuid4(),
            tier="primary",  # type: ignore[call-arg]
        )


def test_s2_role_assignment_item_requires_both_fields() -> None:
    """Missing either ``role_id`` or ``org_node_id`` -> 422.

    Pydantic v2 raises ValidationError with one error per missing
    field; either case fails validation. The two-field invariant is
    what makes the diff-replace tuple deterministic.
    """
    # Missing org_node_id.
    with pytest.raises(ValidationError):
        RoleAssignmentItem(role_id=uuid4())  # type: ignore[call-arg]

    # Missing role_id.
    with pytest.raises(ValidationError):
        RoleAssignmentItem(org_node_id=uuid4())  # type: ignore[call-arg]


def test_s3_post_rejects_empty_roles_patch_accepts_empty_roles() -> None:
    """POST: roles=[] -> ValidationError (Field(min_length=1)).
    PATCH: roles=[] -> accepted (empty-list-revoke-all semantics).
    PATCH: roles=None -> accepted (means 'no change').
    """
    # POST with empty roles[] rejects.
    with pytest.raises(ValidationError):
        TenantUserCreateRequest(
            tenant_id=uuid4(),
            email="rt@test.example.com",
            full_name="RT",
            roles=[],
        )

    # PATCH with empty roles[] accepts.
    patch_empty = TenantUserPatchRequest(roles=[])
    assert patch_empty.roles == []

    # PATCH with no roles field at all (None default) accepts.
    patch_none = TenantUserPatchRequest(full_name="Renamed")
    assert patch_none.roles is None


def test_s4_bare_uuid_legacy_element_rejected() -> None:
    """LD1 contract: legacy 6.10.1 ``roles: list[UUID]`` shape is
    rejected ahead of any business validation. Old clients must
    migrate."""
    legacy_payload = {
        "tenant_id": str(uuid4()),
        "email": "legacy@test.example.com",
        "full_name": "Legacy",
        "roles": [str(uuid4())],  # bare UUID string, not a dict
    }
    with pytest.raises(ValidationError):
        TenantUserCreateRequest(**legacy_payload)  # type: ignore[arg-type]
