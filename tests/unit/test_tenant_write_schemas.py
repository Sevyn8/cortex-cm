"""Step 6.11.1 unit tests for TenantCreateRequest + TenantPatchRequest.

12 tests in two groups: 8 cover the create-request validation rules
(minimal valid, ADMIN force-add, dedupe, status rejection, missing
required fields, invalid module_code, number_of_stores=0 rejection,
revenue-pair consistency, email lowercase); 4 cover the patch-request
shape (single-field valid, status rejection, region rejection,
empty-body builds).
"""
from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from admin_backend.models.tenant_module_access import ModuleCode
from admin_backend.schemas.tenant import (
    TenantCreateRequest,
    TenantPatchRequest,
)


_MIN_VALID_CREATE: dict[str, object] = {
    "name": "Acme Retail",
    "region": "US",
    "tier": "ENTERPRISE",
    "industry": "GROCERY",
    "country": "United States",
    "primary_contact_name": "Alice Operator",
    "contact_email": "alice@acme.example",
    "number_of_stores": 10,
    "number_of_stores_as_of_date": date(2026, 1, 1),
}


# ---------------------------------------------------------------------------
# TenantCreateRequest
# ---------------------------------------------------------------------------


def test_create_minimal_valid_request_force_includes_admin() -> None:
    """Bare minimum fields build; default modules_enabled gets ADMIN."""
    body = TenantCreateRequest(**_MIN_VALID_CREATE)
    assert body.name == "Acme Retail"
    assert body.modules_enabled == [ModuleCode.ADMIN]


def test_create_modules_enabled_force_includes_admin_when_absent() -> None:
    """Non-empty modules_enabled without ADMIN gets ADMIN appended."""
    body = TenantCreateRequest(
        **_MIN_VALID_CREATE,
        modules_enabled=["PRICING_OS", "PERISHABLES_ASSISTANT"],
    )
    assert ModuleCode.ADMIN in body.modules_enabled
    assert body.modules_enabled[-1] == ModuleCode.ADMIN  # appended last


def test_create_modules_enabled_dedupes_preserving_order() -> None:
    """Duplicates collapse; ADMIN duplicates also collapse."""
    body = TenantCreateRequest(
        **_MIN_VALID_CREATE,
        modules_enabled=["PRICING_OS", "PRICING_OS", "ADMIN", "ADMIN"],
    )
    assert body.modules_enabled == [
        ModuleCode.PRICING_OS,
        ModuleCode.ADMIN,
    ]


def test_create_rejects_status_field() -> None:
    """``status`` is server-forced to TRIAL; extra=forbid rejects."""
    with pytest.raises(ValidationError) as exc_info:
        TenantCreateRequest(**_MIN_VALID_CREATE, status="ACTIVE")
    # Pydantic v2's extra='forbid' surfaces as type "extra_forbidden".
    errors = exc_info.value.errors()
    assert any(
        e["type"] == "extra_forbidden" and e["loc"] == ("status",)
        for e in errors
    )


def test_create_rejects_unknown_id_field() -> None:
    """``id`` is server-generated; extra=forbid rejects."""
    with pytest.raises(ValidationError):
        TenantCreateRequest(
            **_MIN_VALID_CREATE,
            id="00000000-0000-0000-0000-000000000000",
        )


def test_create_rejects_invalid_module_code() -> None:
    """Module code outside the enum is 422."""
    with pytest.raises(ValidationError):
        TenantCreateRequest(
            **_MIN_VALID_CREATE,
            modules_enabled=["NOT_A_MODULE"],
        )


def test_create_rejects_number_of_stores_zero() -> None:
    """number_of_stores must be >= 1."""
    body = dict(_MIN_VALID_CREATE)
    body["number_of_stores"] = 0
    with pytest.raises(ValidationError):
        TenantCreateRequest(**body)


def test_create_requires_revenue_pair_consistency() -> None:
    """monthly_revenue_usd without as_of_date is rejected."""
    with pytest.raises(ValidationError):
        TenantCreateRequest(
            **_MIN_VALID_CREATE,
            monthly_revenue_usd=Decimal("1000.00"),
        )
    # ... and as_of_date without usd is rejected too.
    with pytest.raises(ValidationError):
        TenantCreateRequest(
            **_MIN_VALID_CREATE,
            monthly_revenue_as_of_date=date(2026, 1, 1),
        )


def test_create_lowercases_email() -> None:
    """contact_email is lowercased to satisfy the DDL CHECK."""
    body = dict(_MIN_VALID_CREATE)
    body["contact_email"] = "Mixed.CASE@Example.COM"
    parsed = TenantCreateRequest(**body)
    assert parsed.contact_email == "mixed.case@example.com"


# ---------------------------------------------------------------------------
# TenantPatchRequest
# ---------------------------------------------------------------------------


def test_patch_single_field_valid_dump_excludes_unset() -> None:
    """One-field patch surfaces exactly one key in exclude_unset dump."""
    body = TenantPatchRequest(primary_contact_name="New Contact")
    sent = body.model_dump(exclude_unset=True)
    assert sent == {"primary_contact_name": "New Contact"}


def test_patch_rejects_status_field() -> None:
    """status changes go through /suspend and /activate only."""
    with pytest.raises(ValidationError):
        TenantPatchRequest(status="SUSPENDED")


def test_patch_rejects_region_field() -> None:
    """region is immutable post-create (D-05 / locked decision)."""
    with pytest.raises(ValidationError):
        TenantPatchRequest(region="EU")


def test_patch_empty_body_builds_for_handler_check() -> None:
    """Empty body builds; the handler converts to EmptyPatchError (422)."""
    body = TenantPatchRequest()
    sent = body.model_dump(exclude_unset=True)
    assert sent == {}
