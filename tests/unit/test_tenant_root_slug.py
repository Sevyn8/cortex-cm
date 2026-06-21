"""Unit tests for ``slug_for_tenant_root`` (Step 6.20.1).

Pure-Python tests of the mechanical slug rule that derives
``(code, path)`` for the tenant-root org_node row provisioned alongside
every POST `/api/v1/tenants`. No database; no fixtures. Lives under
``tests/unit/`` per the existing unit-test layout (``test_engine.py``,
``test_permissions_helpers.py``).

The helper's rule is locked at LD3 in ``prompts/step-6_20_1-impl-2026-05-18.md``:
diacritic-strip, ASCII-only, lowercase, collapse non-alphanumerics to
single ``-``, trim, truncate at 64 chars, raise on empty.
"""
import pytest

from admin_backend.errors import InvalidTenantNameForSlugError
from admin_backend.repositories.tenants import slug_for_tenant_root


def test_slug_happy_path_simple_name() -> None:
    """name='Buc-ee's', display_code=None -> ('BUC-EE-S', 'buc_ee_s').

    Apostrophe is a non-alphanumeric and collapses to ``-``; the
    surrounding hyphen survives because the rule is "collapse runs",
    not "drop hyphens".
    """
    code, path = slug_for_tenant_root("Buc-ee's", None)
    assert code == "BUC-EE-S"
    assert path == "buc_ee_s"


def test_slug_display_code_wins() -> None:
    """display_code takes precedence over name when non-None."""
    code, path = slug_for_tenant_root("Buc-ee's", "buc-ees")
    assert code == "BUC-EES"
    assert path == "buc_ees"


def test_slug_diacritic_strip() -> None:
    """name='Żabka Group' -> ('ZABKA-GROUP', 'zabka_group').

    Polish diacritic on Z survives NFKD then drops at ASCII encode.
    Space becomes ``-`` via the non-alphanumeric collapse.
    """
    code, path = slug_for_tenant_root("Żabka Group", None)
    assert code == "ZABKA-GROUP"
    assert path == "zabka_group"


def test_slug_truncate_at_64() -> None:
    """100-char alphanumeric input truncates to 64 chars on both outputs."""
    long_name = "a" * 100
    code, path = slug_for_tenant_root(long_name, None)
    assert len(code) == 64
    assert len(path) == 64
    assert code == "A" * 64
    assert path == "a" * 64


def test_slug_truncate_trims_trailing_hyphen() -> None:
    """Truncation landing on a ``-`` re-trims so the final char is
    alphanumeric (DDL ``ck_org_nodes_code_format`` requires the boundary
    chars to be alphanumeric).

    Construct input where the slug pre-truncation has a hyphen at
    position 64. 63 ``a`` chars + ``-x`` -> slug ``a*63-x`` (65 chars);
    truncate to 64 -> ``a*63-`` (trailing ``-``); strip -> ``a*63``.
    """
    name = ("a" * 63) + " x"  # space collapses to -
    code, path = slug_for_tenant_root(name, None)
    assert code == "A" * 63
    assert path == "a" * 63
    # boundary is alphanumeric, conforms to DDL CHECK
    assert code[-1].isalnum()


def test_slug_collapses_runs_of_separators() -> None:
    """``Foo!!! Bar___Baz`` collapses every non-alphanumeric run
    (including underscores in the input) to single ``-`` boundaries.
    """
    code, path = slug_for_tenant_root("Foo!!! Bar___Baz", None)
    assert code == "FOO-BAR-BAZ"
    assert path == "foo_bar_baz"


def test_slug_empty_input_raises_on_name() -> None:
    """All-non-alphanumeric name yields empty slug; raises with
    field='name' identifying the source field.
    """
    with pytest.raises(InvalidTenantNameForSlugError) as excinfo:
        slug_for_tenant_root("!!!", None)
    assert excinfo.value.context.get("field") == "name"


def test_slug_empty_display_code_raises() -> None:
    """Valid name but empty-slug display_code raises with
    field='display_code'. display_code wins, so the empty-slug case
    fires on it regardless of name's validity.
    """
    with pytest.raises(InvalidTenantNameForSlugError) as excinfo:
        slug_for_tenant_root("Valid Name", "!!!")
    assert excinfo.value.context.get("field") == "display_code"


def test_slug_whitespace_only_raises() -> None:
    """Whitespace-only name yields empty slug; raises."""
    with pytest.raises(InvalidTenantNameForSlugError):
        slug_for_tenant_root("   ", None)


def test_slug_single_char_valid() -> None:
    """name='A' produces single-char slug. DDL CHECK
    ``ck_org_nodes_code_format`` allows ``length(code) = 1`` as a
    special-case branch.
    """
    code, path = slug_for_tenant_root("A", None)
    assert code == "A"
    assert path == "a"
