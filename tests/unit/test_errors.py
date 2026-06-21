"""Step 6.11.1 unit tests for the 4 new ClientError subclasses.

Each test asserts the class-level (http_status, code, public_message)
and that ``build_error_payload`` produces the expected envelope. Per
the existing ClientError contract (errors.py), subclass-specific
``code`` and ``public_message`` surface in the response body —
distinct from ServerError subclasses which always render the generic
INTERNAL_ERROR envelope.
"""
from admin_backend.errors import (
    DuplicateTenantNameError,
    EmptyPatchError,
    InvalidStateTransitionError,
    PlatformAudienceRequiredError,
    build_error_payload,
)


def test_platform_audience_required_error_envelope() -> None:
    """PlatformAudienceRequiredError -> 403 PLATFORM_AUDIENCE_REQUIRED."""
    exc = PlatformAudienceRequiredError(
        "audience='PLATFORM' required but caller user_type='TENANT'",
        required_audience="PLATFORM",
        actual_user_type="TENANT",
    )
    assert exc.http_status == 403
    assert exc.code == "PLATFORM_AUDIENCE_REQUIRED"
    assert exc.public_message == "This operation requires a platform user."
    # Internal-only fields survive on context for log emission.
    assert exc.context["required_audience"] == "PLATFORM"
    assert exc.context["actual_user_type"] == "TENANT"

    status, body, _headers = build_error_payload(exc, request_id="rid-1")
    assert status == 403
    assert body == {
        "code": "PLATFORM_AUDIENCE_REQUIRED",
        "message": "This operation requires a platform user.",
        "details": None,
        "request_id": "rid-1",
    }


def test_duplicate_tenant_name_error_envelope() -> None:
    """DuplicateTenantNameError -> 409 DUPLICATE_TENANT_NAME."""
    exc = DuplicateTenantNameError(
        "tenant name already taken: 'Acme'", name="Acme"
    )
    assert exc.http_status == 409
    assert exc.code == "DUPLICATE_TENANT_NAME"
    assert exc.public_message == "A tenant with this name already exists."
    assert exc.context["name"] == "Acme"

    status, body, _headers = build_error_payload(exc, request_id="rid-2")
    assert status == 409
    assert body["code"] == "DUPLICATE_TENANT_NAME"
    assert body["message"] == "A tenant with this name already exists."


def test_invalid_state_transition_error_envelope() -> None:
    """InvalidStateTransitionError -> 409 INVALID_STATE_TRANSITION."""
    exc = InvalidStateTransitionError(
        "tenant in SUSPENDED cannot transition to SUSPENDED",
        current_status="SUSPENDED",
        target_status="SUSPENDED",
    )
    assert exc.http_status == 409
    assert exc.code == "INVALID_STATE_TRANSITION"
    assert exc.public_message == (
        "Tenant cannot transition to the requested state."
    )

    status, body, _headers = build_error_payload(exc, request_id=None)
    assert status == 409
    assert body["code"] == "INVALID_STATE_TRANSITION"
    assert body["request_id"] is None


def test_empty_patch_error_envelope() -> None:
    """EmptyPatchError -> 422 EMPTY_PATCH."""
    exc = EmptyPatchError("PATCH body had no set fields")
    assert exc.http_status == 422
    assert exc.code == "EMPTY_PATCH"
    assert exc.public_message == "PATCH request must include at least one field."

    status, body, _headers = build_error_payload(exc, request_id="rid-3")
    assert status == 422
    assert body == {
        "code": "EMPTY_PATCH",
        "message": "PATCH request must include at least one field.",
        "details": None,
        "request_id": "rid-3",
    }
