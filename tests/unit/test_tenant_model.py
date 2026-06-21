"""Unit tests for the Tenant ORM model.

No live DB. Pure metadata / SQL-compilation assertions.
"""
from sqlalchemy import FetchedValue, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM

from admin_backend.config import get_settings
from admin_backend.models.tenant import (
    Tenant,
    TenantIndustry,
    TenantRegion,
    TenantStatus,
    TenantTier,
)


# ---- T1 ----------------------------------------------------------------
def test_tenant_imports_cleanly() -> None:
    """Module loads and exposes the Tenant class."""
    assert Tenant.__name__ == "Tenant"


# ---- T2 ----------------------------------------------------------------
def test_tablename_is_tenants() -> None:
    assert Tenant.__tablename__ == "tenants"


# ---- T3 ----------------------------------------------------------------
def test_table_args_schema_matches_settings() -> None:
    """`__table_args__["schema"]` is wired to Settings.db_schema (D-15)."""
    table_args = Tenant.__table_args__
    assert isinstance(table_args, dict)
    assert table_args["schema"] == get_settings().db_schema


# ---- T4 ----------------------------------------------------------------
def test_compiled_select_is_schema_qualified() -> None:
    """SELECT FROM Tenant compiles to <schema>.tenants, not bare tenants."""
    schema = get_settings().db_schema
    compiled = str(select(Tenant).compile(dialect=postgresql.dialect()))
    assert f"{schema}.tenants" in compiled
    # Defensive: no bare 'FROM tenants' (without the schema prefix).
    assert "FROM tenants" not in compiled


# ---- T5 ----------------------------------------------------------------
def test_enum_columns_reference_existing_pg_types() -> None:
    """The four enum columns: name + create_type + native_enum + round-trip."""
    expected = {
        "status": ("tenant_status_enum", TenantStatus, TenantStatus.ACTIVE, "ACTIVE"),
        "tier": ("tenant_tier_enum", TenantTier, TenantTier.ENTERPRISE, "ENTERPRISE"),
        "industry": (
            "tenant_industry_enum",
            TenantIndustry,
            TenantIndustry.GROCERY,
            "GROCERY",
        ),
        "region": ("tenant_region_enum", TenantRegion, TenantRegion.US, "US"),
    }
    pg_dialect = postgresql.dialect()
    for col_name, (pg_type_name, py_enum, sample_member, expected_db_value) in (
        expected.items()
    ):
        col = Tenant.__table__.c[col_name]
        col_type = col.type
        # 1) Dialect-specific class is used (not generic sqlalchemy.Enum) so
        #    create_type=False is actually honoured.
        assert isinstance(col_type, PG_ENUM), (
            f"{col_name} should be postgresql.ENUM; got {type(col_type).__name__}"
        )
        # 2) PG type name is wired to the DDL-created type.
        assert col_type.name == pg_type_name, (
            f"{col_name} pg type name mismatch: {col_type.name!r} != {pg_type_name!r}"
        )
        # 3) DDL owns CREATE TYPE; ORM must not re-emit it.
        assert col_type.create_type is False, (
            f"{col_name} must have create_type=False (DDL owns the type)"
        )
        # 4) Native pg enum, not an emulated CHECK-constrained VARCHAR.
        assert col_type.native_enum is True, (
            f"{col_name} should use native pg enum"
        )
        # 5) Round-trip: column type carries the Python enum class
        #    and converts member <-> DB string in both directions.
        assert col_type.enum_class is py_enum, (
            f"{col_name} python enum class mismatch"
        )
        bind = col_type.bind_processor(pg_dialect)
        result = col_type.result_processor(pg_dialect, None)
        assert bind is not None and result is not None, (
            f"{col_name} bind/result processors missing"
        )
        # Bind: TenantStatus.ACTIVE -> "ACTIVE" goes to the DB.
        assert bind(sample_member) == expected_db_value, (
            f"{col_name} bind: {sample_member!r} -> {bind(sample_member)!r}, "
            f"expected {expected_db_value!r}"
        )
        # Result: DB "ACTIVE" -> TenantStatus.ACTIVE comes back.
        assert result(expected_db_value) is sample_member, (
            f"{col_name} result: {expected_db_value!r} -> {result(expected_db_value)!r}, "
            f"expected {sample_member!r}"
        )


# ---- T6 ----------------------------------------------------------------
def test_db_default_columns_use_fetchedvalue() -> None:
    """Columns whose DDL carries a DEFAULT use ``FetchedValue()``: SQLAlchemy
    must know a DB-side default exists (so it omits the column from INSERT
    and reads back via RETURNING) without redeclaring the SQL.

    Per D-21, the literal DEFAULT (e.g. ``uuidv7()``) is owned by the DDL.
    ``FetchedValue()`` declares only the *existence* of a default, not the
    SQL — preserving D-21's intent and avoiding the FN-AB-13 maintenance
    trap, while letting SQLA generate correct INSERTs.

    Tightened during Step 3.2 from the original ``server_default is None``
    assertion: the prior shape would pass T6 but fail any actual ORM
    INSERT on ``created_at`` / ``updated_at`` with a NOT NULL violation.
    """
    expected_columns = ("id", "created_at", "updated_at", "status")
    for col_name in expected_columns:
        col = Tenant.__table__.c[col_name]
        assert col.default is None, (
            f"Tenant.{col_name} must not have a Python/ORM-side default; "
            "the DDL is authoritative."
        )
        assert isinstance(col.server_default, FetchedValue), (
            f"Tenant.{col_name} must declare server_default=FetchedValue() "
            "so SQLAlchemy omits it from INSERT and reads back via "
            f"RETURNING; got {col.server_default!r}."
        )
    # Also re-verify that `id` is the PK (regression guard).
    assert Tenant.__table__.c.id.primary_key is True
