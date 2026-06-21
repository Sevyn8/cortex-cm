"""Unit tests for Step 6.13 org-tree write schemas.

Pure Pydantic-level checks. No DB, no auth.
"""
from uuid import uuid4

import pytest
from pydantic import ValidationError

from admin_backend.schemas.org_node import (
    OrgNodeCreateRequest,
    OrgNodePatchRequest,
)


def test_s1_create_rejects_tenant_node_type() -> None:
    """node_type='TENANT' rejected at Pydantic time (422 before handler)."""
    with pytest.raises(ValidationError) as exc_info:
        OrgNodeCreateRequest(
            parent_id=uuid4(),
            node_type="TENANT",  # type: ignore[arg-type]
            code="bu-1",
            name="Business Unit 1",
        )
    assert "TENANT" in str(exc_info.value)


def test_s2_create_rejects_extra_fields() -> None:
    """extra='forbid' rejects unknown fields."""
    with pytest.raises(ValidationError):
        OrgNodeCreateRequest.model_validate(
            {
                "parent_id": str(uuid4()),
                "node_type": "STORE",
                "code": "s1",
                "name": "Store 1",
                "status": "ACTIVE",  # not in schema
            }
        )


def test_s3_patch_all_none_rejects() -> None:
    """PATCH {} -> ValidationError -> 422 (handler envelope)."""
    with pytest.raises(ValidationError) as exc_info:
        OrgNodePatchRequest()
    assert "at least one" in str(exc_info.value).lower()


def test_s4_patch_has_no_node_type_field() -> None:
    """LD3: node_type is immutable; PATCH shape has no slot for it."""
    fields = OrgNodePatchRequest.model_fields
    assert "node_type" not in fields
    # Mutating fields that ARE permitted.
    assert {"parent_id", "code", "name"} == set(fields.keys())
