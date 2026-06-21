"""Unit tests for the seed loader's column-mapping drift detector.

Fast (no DB). Catches the most common drift scenarios:
  - Excel adds a column nobody declared in column_mappings.
  - A sheet name typo or a sheet missing from SHEET_MAPPINGS.
  - A sheet accidentally has all HELPER columns (no DB_COLUMN).
  - An FK_REF is declared without an fk_target (constructor-validated,
    but a regression test guards it from drift).
"""
import pytest

from scripts.seed_dev_data.column_mappings import (
    SHEET_MAPPINGS,
    ColumnRole,
    UnknownColumnError,
    validate_columns,
)


def test_known_columns_pass() -> None:
    """A row whose columns match the mapping passes validation."""
    headers = [spec.name for spec in SHEET_MAPPINGS["tenants"]]
    validate_columns("tenants", headers)  # no exception


def test_unknown_column_raises() -> None:
    """An Excel column not in the mapping causes an explicit error."""
    headers = [
        spec.name for spec in SHEET_MAPPINGS["tenants"]
    ] + ["unexpected_col"]
    with pytest.raises(UnknownColumnError) as exc:
        validate_columns("tenants", headers)
    assert "unexpected_col" in str(exc.value)
    assert "tenants" in str(exc.value)


def test_unknown_sheet_raises() -> None:
    """Asking about a sheet not in SHEET_MAPPINGS raises."""
    with pytest.raises(UnknownColumnError):
        validate_columns("not_a_real_sheet", ["any_col"])


def test_every_sheet_has_db_columns() -> None:
    """Every sheet must have at least one DB_COLUMN — catches typos
    that accidentally make every column a HELPER.
    """
    for sheet_name, mapping in SHEET_MAPPINGS.items():
        db_cols = [s for s in mapping if s.role is ColumnRole.DB_COLUMN]
        assert db_cols, f"sheet {sheet_name} has no DB_COLUMN entries"


def test_fk_refs_have_targets() -> None:
    """Every FK_REF must declare an fk_target. Validated at
    construction in ColumnSpec.__post_init__, but a regression test
    guards it.
    """
    for sheet_name, mapping in SHEET_MAPPINGS.items():
        for spec in mapping:
            if spec.role is ColumnRole.FK_REF:
                assert spec.fk_target is not None, (
                    f"{sheet_name}.{spec.name} is FK_REF but has "
                    "no fk_target"
                )
