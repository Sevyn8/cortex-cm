"""Unit tests for the Step 6.15 error class ``ModuleAccessNotFoundError``.

E1 — verifies the class attributes that drive the HTTP response and the
Q7 envelope posture (structured context lives on the exception, not in
the response body's ``details`` field).
"""
from __future__ import annotations

import uuid

from admin_backend.errors import (
    ClientError,
    ModuleAccessNotFoundError,
    build_error_payload,
)


def test_e1_module_access_not_found_envelope_and_context() -> None:
    """``http_status=404``, ``code=MODULE_ACCESS_NOT_FOUND``; details=null;
    structured ``tenant_id`` + ``module_code`` reach ``exc.context``."""
    tenant_id = uuid.uuid4()
    module_code = "PRICING_OS"
    exc = ModuleAccessNotFoundError(
        "no row for tenant_id=... module=...",
        tenant_id=str(tenant_id),
        module_code=module_code,
    )

    assert isinstance(exc, ClientError)
    assert exc.http_status == 404
    assert exc.code == "MODULE_ACCESS_NOT_FOUND"
    assert exc.context["tenant_id"] == str(tenant_id)
    assert exc.context["module_code"] == module_code

    status, body, headers = build_error_payload(exc, request_id="req-1")
    assert status == 404
    assert body["code"] == "MODULE_ACCESS_NOT_FOUND"
    assert body["details"] is None
    assert body["request_id"] == "req-1"
    # The structured tenant_id / module_code MUST NOT appear in the
    # response body (Q7: log-only). The build_error_payload helper
    # does not surface ``exc.context`` to the wire.
    assert "tenant_id" not in body
    assert "module_code" not in body
