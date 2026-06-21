"""Step 6.14 unit tests for the new error envelopes.

Pure error-class tests with no DB or HTTP. Confirms the
``build_error_payload`` shape and the Q7 structured-context posture
(structured detail in ``exc.context`` for logs; response envelope
``details`` field stays ``null``).

  E1 — ``InvalidOrgNodeError`` envelope: 422 + code INVALID_ORG_NODE;
       ``invalid_org_node_ids`` in ``exc.context``; envelope details
       is null.
  E2 — ``DuplicateRoleAssignmentInRequestError`` envelope: 422 + code
       + ``duplicate_pairs`` in context.
  E3 — ``RoleAssignmentConflictError`` envelope: 409 + code +
       ``conflicting_triple`` in context.
"""
from __future__ import annotations

from uuid import uuid4

from admin_backend.errors import (
    DuplicateRoleAssignmentInRequestError,
    InvalidOrgNodeError,
    RoleAssignmentConflictError,
    build_error_payload,
)


def test_e1_invalid_org_node_error_envelope() -> None:
    """422 + code INVALID_ORG_NODE; structured invalid_org_node_ids in
    context; envelope.details stays null per Q7."""
    bad_ids = [str(uuid4()), str(uuid4())]
    exc = InvalidOrgNodeError(
        f"unknown org_node ids: {bad_ids!r}",
        invalid_org_node_ids=bad_ids,
    )
    status, body, _headers = build_error_payload(exc, request_id="req-e1")

    assert status == 422
    assert body["code"] == "INVALID_ORG_NODE"
    assert body["details"] is None
    assert exc.context["invalid_org_node_ids"] == bad_ids


def test_e2_duplicate_role_assignment_in_request_error_envelope() -> None:
    """422 + code DUPLICATE_ROLE_ASSIGNMENT_IN_REQUEST; duplicate_pairs
    in context."""
    rid = uuid4()
    oid = uuid4()
    pairs = [
        {"role_id": str(rid), "org_node_id": str(oid)}
    ]
    exc = DuplicateRoleAssignmentInRequestError(
        "duplicate in roles[]",
        duplicate_pairs=pairs,
    )
    status, body, _headers = build_error_payload(exc, request_id="req-e2")

    assert status == 422
    assert body["code"] == "DUPLICATE_ROLE_ASSIGNMENT_IN_REQUEST"
    assert body["details"] is None
    assert exc.context["duplicate_pairs"] == pairs


def test_e3_role_assignment_conflict_error_envelope() -> None:
    """409 + code ROLE_ASSIGNMENT_CONFLICT; conflicting_triple in context."""
    triple = {
        "tenant_user_id": str(uuid4()),
        "role_id": str(uuid4()),
        "org_node_id": str(uuid4()),
    }
    exc = RoleAssignmentConflictError(
        "concurrent conflict",
        conflicting_triple=triple,
    )
    status, body, _headers = build_error_payload(exc, request_id="req-e3")

    assert status == 409
    assert body["code"] == "ROLE_ASSIGNMENT_CONFLICT"
    assert body["details"] is None
    assert exc.context["conflicting_triple"] == triple
