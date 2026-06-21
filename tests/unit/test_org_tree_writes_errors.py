"""Unit tests for Step 6.13 error envelope semantics.

Verifies http_status + code mapping + Q7 context-vs-details posture.
"""
from uuid import uuid4

from admin_backend.errors import (
    CycleDetectedError,
    DuplicateOrgNodeCodeError,
    InvalidParentNodeTypeError,
    build_error_payload,
)


def test_err1_invalid_parent_node_type_envelope() -> None:
    """422 + INVALID_PARENT_NODE_TYPE + structured context in exc.context."""
    exc = InvalidParentNodeTypeError(
        "parent_type=REGION must sit above child_type=HQ",
        child_type="HQ",
        parent_type="REGION",
        attempted_ordinal_child=2,
        attempted_ordinal_parent=4,
    )
    assert exc.http_status == 422
    assert exc.code == "INVALID_PARENT_NODE_TYPE"
    assert exc.context["child_type"] == "HQ"
    assert exc.context["parent_type"] == "REGION"
    assert exc.context["attempted_ordinal_child"] == 2
    assert exc.context["attempted_ordinal_parent"] == 4

    status, body, _ = build_error_payload(exc, request_id="req-1")
    assert status == 422
    assert body["code"] == "INVALID_PARENT_NODE_TYPE"
    assert body["details"] is None  # Q7: structured info NOT in envelope
    assert body["request_id"] == "req-1"


def test_err2_cycle_detected_envelope() -> None:
    """422 + CYCLE_DETECTED + structured context."""
    target = uuid4()
    parent = uuid4()
    exc = CycleDetectedError(
        f"node {target} cannot be reparented under descendant {parent}",
        target_id=str(target),
        attempted_parent_id=str(parent),
    )
    assert exc.http_status == 422
    assert exc.code == "CYCLE_DETECTED"
    assert exc.context["target_id"] == str(target)
    assert exc.context["attempted_parent_id"] == str(parent)

    status, body, _ = build_error_payload(exc, request_id=None)
    assert status == 422
    assert body["code"] == "CYCLE_DETECTED"
    assert body["details"] is None


def test_err3_duplicate_org_node_code_envelope() -> None:
    """409 + DUPLICATE_ORG_NODE_CODE + structured context."""
    tenant_id = uuid4()
    exc = DuplicateOrgNodeCodeError(
        "code='store-1' already exists in tenant",
        code="store-1",
        tenant_id=str(tenant_id),
    )
    assert exc.http_status == 409
    assert exc.code == "DUPLICATE_ORG_NODE_CODE"
    assert exc.context["code"] == "store-1"
    assert exc.context["tenant_id"] == str(tenant_id)

    status, body, _ = build_error_payload(exc, request_id="req-3")
    assert status == 409
    assert body["code"] == "DUPLICATE_ORG_NODE_CODE"
    assert body["details"] is None
