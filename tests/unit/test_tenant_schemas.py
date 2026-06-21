"""Unit tests for the TenantRead Pydantic schema.

No live DB. Uses a SimpleNamespace as an ORM-shaped fake object to
exercise ``model_validate(from_attributes=True)``.
"""
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

from admin_backend.models.tenant import (
    TenantIndustry,
    TenantRegion,
    TenantStatus,
    TenantTier,
)
from admin_backend.schemas.tenant import TenantRead


# Shared sample that lights up every nullable field. Tests pick what they need.
_FIXED_UUID = UUID("018f3c4d-5e6a-7b8c-9d0e-1f2a3b4c5d6e")
_FIXED_AUDIT_UUID = UUID("018f3c4d-5e6a-7b8c-9d0e-aaaaaaaaaaaa")
_TS = datetime(2026, 5, 2, 10, 0, 0, tzinfo=timezone.utc)


def _fake_orm_obj(**overrides: object) -> SimpleNamespace:
    base = dict(
        id=_FIXED_UUID,
        name="Acme Retail",
        display_code="acme-retail",
        country="USA",
        region=TenantRegion.US,
        tier=TenantTier.ENTERPRISE,
        industry=TenantIndustry.GROCERY,
        monthly_revenue_usd=Decimal("1500000.50"),
        monthly_revenue_as_of_date=date(2026, 4, 1),
        number_of_stores=42,
        number_of_stores_as_of_date=date(2026, 4, 1),
        primary_contact_name="Jane Doe",
        contact_email="jane@acme.com",
        status=TenantStatus.ACTIVE,
        created_at=_TS,
        created_by_user_id=_FIXED_AUDIT_UUID,
        updated_at=_TS,
        updated_by_user_id=_FIXED_AUDIT_UUID,
        suspended_at=None,
        suspended_by_user_id=None,
        terminated_at=None,
        terminated_by_user_id=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# ---- S1 ----------------------------------------------------------------
def test_model_validate_from_orm_shaped_object() -> None:
    obj = _fake_orm_obj()
    read = TenantRead.model_validate(obj)
    assert read.id == _FIXED_UUID
    assert read.name == "Acme Retail"
    assert read.region is TenantRegion.US
    assert read.status is TenantStatus.ACTIVE


# ---- S2 ----------------------------------------------------------------
def test_dump_json_uses_snake_case_keys() -> None:
    """Q1 default: snake_case keys in JSON output."""
    read = TenantRead.model_validate(_fake_orm_obj())
    data = json.loads(read.model_dump_json())
    expected_keys = {
        "id", "name", "display_code", "country", "region", "tier",
        "industry", "monthly_revenue_usd", "monthly_revenue_as_of_date",
        "number_of_stores", "number_of_stores_as_of_date",
        "primary_contact_name", "contact_email", "status",
        "created_at", "updated_at", "suspended_at", "terminated_at",
    }
    assert set(data.keys()) == expected_keys
    # Defensive: no camelCase leakage.
    for k in data:
        assert k.islower() or "_" in k or k.isalpha(), f"unexpected key shape: {k!r}"


# ---- S3 ----------------------------------------------------------------
def test_decimal_serialises_as_string_in_json() -> None:
    """Q11: Decimal -> JSON string preserves precision and trailing zeros."""
    read = TenantRead.model_validate(_fake_orm_obj())
    data = json.loads(read.model_dump_json())
    # JSON: string, not number.
    assert data["monthly_revenue_usd"] == "1500000.50"
    assert isinstance(data["monthly_revenue_usd"], str)
    # Trailing zeros preserved.
    read2 = TenantRead.model_validate(
        _fake_orm_obj(monthly_revenue_usd=Decimal("100.00"))
    )
    assert json.loads(read2.model_dump_json())["monthly_revenue_usd"] == "100.00"
    # Python mode (when_used="json") leaves the Decimal alone.
    py_dump = read.model_dump()
    assert isinstance(py_dump["monthly_revenue_usd"], Decimal)
    assert py_dump["monthly_revenue_usd"] == Decimal("1500000.50")


# ---- S4 ----------------------------------------------------------------
def test_null_fields_appear_as_json_null_not_omitted() -> None:
    """Q7: nullable fields are present in the output even when None."""
    obj = _fake_orm_obj(
        display_code=None,
        country=None,
        tier=None,
        industry=None,
        monthly_revenue_usd=None,
        monthly_revenue_as_of_date=None,
        number_of_stores=None,
        number_of_stores_as_of_date=None,
        primary_contact_name=None,
        contact_email=None,
        suspended_at=None,
        terminated_at=None,
    )
    read = TenantRead.model_validate(obj)
    data = json.loads(read.model_dump_json())
    for nullable_field in (
        "display_code", "country", "tier", "industry",
        "monthly_revenue_usd", "monthly_revenue_as_of_date",
        "number_of_stores", "number_of_stores_as_of_date",
        "primary_contact_name", "contact_email",
        "suspended_at", "terminated_at",
    ):
        assert nullable_field in data, f"{nullable_field} omitted from dump"
        assert data[nullable_field] is None, (
            f"{nullable_field} should be JSON null, got {data[nullable_field]!r}"
        )


# ---- S5 ----------------------------------------------------------------
def test_datetime_fields_serialise_as_iso8601_with_offset() -> None:
    """Q4: ISO 8601 with timezone offset."""
    read = TenantRead.model_validate(
        _fake_orm_obj(suspended_at=_TS, terminated_at=_TS)
    )
    data = json.loads(read.model_dump_json())
    for ts_field in ("created_at", "updated_at", "suspended_at", "terminated_at"):
        v = data[ts_field]
        assert isinstance(v, str), f"{ts_field} should be ISO string"
        # ISO 8601 with offset: pydantic emits 'Z' or '+00:00' for UTC.
        assert v.startswith("2026-05-02T10:00:00"), (
            f"{ts_field} unexpected datetime shape: {v!r}"
        )
        assert v.endswith("Z") or "+" in v[10:] or v.endswith("+00:00"), (
            f"{ts_field} missing timezone offset: {v!r}"
        )


# ---- S6 ----------------------------------------------------------------
def test_audit_actor_ids_are_hidden_from_response() -> None:
    """*_by_user_id columns are internal lineage; never returned."""
    read = TenantRead.model_validate(_fake_orm_obj())
    data = json.loads(read.model_dump_json())
    for hidden in (
        "created_by_user_id",
        "updated_by_user_id",
        "suspended_by_user_id",
        "terminated_by_user_id",
    ):
        assert hidden not in data, (
            f"{hidden} leaked into API response (must be hidden)"
        )


# ---- S7 ----------------------------------------------------------------
def test_enums_serialise_as_string_values_not_repr() -> None:
    """Enums emit their .value strings, not 'TenantStatus.ACTIVE' or similar."""
    read = TenantRead.model_validate(_fake_orm_obj())
    data = json.loads(read.model_dump_json())
    assert data["status"] == "ACTIVE"
    assert data["region"] == "US"
    assert data["tier"] == "ENTERPRISE"
    assert data["industry"] == "GROCERY"
