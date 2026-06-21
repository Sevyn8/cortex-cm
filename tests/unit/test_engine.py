"""Step 2.2a unit tests for the async engine factory and privilege check.

8 tests:
    T1: create_engine() yields an AsyncEngine that connects.
    T2: connect-time hook sets search_path to db_schema, public.
    T3: privilege check passes against live local DB
        (NOSUPERUSER NOBYPASSRLS per Step 1.5).
    T4: privilege check raises for SUPERUSER alone.
    T5: privilege check raises for BYPASSRLS alone.
    T6: privilege check raises for both attributes set.
    T7: privilege check raises when pg_roles row is missing.
    T8: db_schema field validator rejects a non-identifier value.
"""
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncEngine

from admin_backend.config import Settings
from admin_backend.db.engine import (
    AppRolePrivilegeError,
    assert_app_role_no_bypassrls,
    create_engine,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def settings() -> Settings:
    """Project Settings loaded from .env. Module-scoped to avoid re-loading."""
    return Settings()  # type: ignore[call-arg]


@pytest.fixture
async def engine(settings: Settings):  # type: ignore[no-untyped-def]
    """Per-test engine. Disposed at teardown to avoid pool leakage."""
    eng = create_engine(settings)
    yield eng
    await eng.dispose()


# ---------------------------------------------------------------------------
# T1-T2: engine and connect-hook
# ---------------------------------------------------------------------------


async def test_t1_engine_connects(engine: AsyncEngine) -> None:
    """create_engine() yields an AsyncEngine that connects to the local DB."""
    assert isinstance(engine, AsyncEngine)
    async with engine.connect() as conn:
        result = await conn.exec_driver_sql("SELECT 1")
        assert result.scalar() == 1


async def test_t2_search_path_set_at_connect(
    engine: AsyncEngine, settings: Settings
) -> None:
    """Connect-time hook sets search_path to db_schema, public."""
    async with engine.connect() as conn:
        result = await conn.exec_driver_sql("SHOW search_path")
        search_path = str(result.scalar())
    assert settings.db_schema in search_path
    assert "public" in search_path


# ---------------------------------------------------------------------------
# T3: privilege check passes against live local DB
# ---------------------------------------------------------------------------


async def test_t3_privilege_check_passes_locally(engine: AsyncEngine) -> None:
    """Local app role is NOSUPERUSER NOBYPASSRLS (Step 1.5); check passes."""
    await assert_app_role_no_bypassrls(engine)


# ---------------------------------------------------------------------------
# T4-T7: privilege check failure cases (mocked engine)
# ---------------------------------------------------------------------------


def _mock_engine_returning(row: tuple[bool, bool] | None) -> AsyncEngine:
    """Build a mock AsyncEngine whose connect().execute() returns `row`."""
    fetched = MagicMock()
    fetched.fetchone.return_value = row

    conn = MagicMock()
    conn.execute = AsyncMock(return_value=fetched)

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=None)

    eng = MagicMock(spec=AsyncEngine)
    eng.connect = MagicMock(return_value=cm)
    return eng  # type: ignore[no-any-return]


async def test_t4_privilege_check_raises_on_superuser() -> None:
    """SUPERUSER=True alone causes refusal."""
    eng = _mock_engine_returning((True, False))
    with pytest.raises(AppRolePrivilegeError) as exc_info:
        await assert_app_role_no_bypassrls(eng)
    msg = str(exc_info.value)
    assert "SUPERUSER=True" in msg
    assert "BYPASSRLS=False" in msg


async def test_t5_privilege_check_raises_on_bypassrls() -> None:
    """BYPASSRLS=True alone causes refusal."""
    eng = _mock_engine_returning((False, True))
    with pytest.raises(AppRolePrivilegeError) as exc_info:
        await assert_app_role_no_bypassrls(eng)
    msg = str(exc_info.value)
    assert "SUPERUSER=False" in msg
    assert "BYPASSRLS=True" in msg


async def test_t6_privilege_check_raises_on_both() -> None:
    """SUPERUSER=True AND BYPASSRLS=True both reported."""
    eng = _mock_engine_returning((True, True))
    with pytest.raises(AppRolePrivilegeError) as exc_info:
        await assert_app_role_no_bypassrls(eng)
    msg = str(exc_info.value)
    assert "SUPERUSER=True" in msg
    assert "BYPASSRLS=True" in msg


async def test_t7_privilege_check_raises_when_no_row() -> None:
    """pg_roles row missing for current_user is itself suspicious."""
    eng = _mock_engine_returning(None)
    with pytest.raises(AppRolePrivilegeError) as exc_info:
        await assert_app_role_no_bypassrls(eng)
    assert "Could not query pg_roles" in str(exc_info.value)


# ---------------------------------------------------------------------------
# T8: db_schema validator
# ---------------------------------------------------------------------------


_BASE_SETTINGS_KWARGS: dict[str, Any] = {
    "database_url": "postgresql+psycopg://test:test@localhost:5432/test",
    "jwt_audience": "https://api.test/",
    "jwt_issuer": "https://stub-issuer.local/",
    "environment": "local",
    "auth_client_mode": "STUB",
}


def test_t8_db_schema_validator_rejects_injection_attempt() -> None:
    """Settings rejects a db_schema with non-identifier characters."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(  # type: ignore[call-arg]
            **_BASE_SETTINGS_KWARGS,
            db_schema="core; DROP TABLE tenants;",
        )
    msg = str(exc_info.value)
    assert "DB_SCHEMA" in msg
    assert "identifier" in msg.lower()
