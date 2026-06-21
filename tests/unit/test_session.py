"""Step 2.2a unit tests for get_tenant_session bootstrap.

7 tests:
    T9:  TENANT auth.tenant_id flows to app.tenant_id.
    T10: TENANT auth.user_type flows to app.user_type.
    T11: PLATFORM with auth.tenant_id=None can query a multi-tenant
         table without raising (RLS policies use NULLIF to interpret
         the registered-empty-string GUC as NULL → default-deny holds
         without crashing on ''::uuid).
    T12: PLATFORM auth.user_type='PLATFORM' flows.
    T13: PLATFORM with non-NULL tenant_id (impersonation per FN-AB-14)
         flows tenant_id through.
    T14: Two concurrent sessions on different physical connections see
         their own tenant_id only (no cross-session leakage).
    T15: After a get_tenant_session transaction ends, a fresh raw
         connection from the pool can query a multi-tenant table
         without raising. The reused connection has app.tenant_id
         registered at empty string (Postgres 15 quirk); the NULLIF
         policy is what makes this safe.
"""
import asyncio
from typing import AsyncIterator
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from admin_backend.auth.context import AuthContext
from admin_backend.config import Settings
from admin_backend.db.engine import create_engine, create_session_factory
from admin_backend.db.session import get_tenant_session


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


@pytest.fixture
async def engine(settings: Settings):  # type: ignore[no-untyped-def]
    eng = create_engine(settings)
    yield eng
    await eng.dispose()


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(engine)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_VALID_AUTH_BASE: dict[str, object] = {
    "sub": "test-sub",
    "iss": "https://stub-issuer.local/",
    "aud": "https://api.test/",
    "exp": 9999999999,
    "email": "test@ithina.local",
}


def _tenant_auth(tenant_id: UUID) -> AuthContext:
    return AuthContext(  # type: ignore[call-arg]
        **_VALID_AUTH_BASE,
        user_id=uuid4(),
        tenant_id=tenant_id,
        user_type="TENANT",
    )


def _platform_auth(tenant_id: UUID | None = None) -> AuthContext:
    return AuthContext(  # type: ignore[call-arg]
        **_VALID_AUTH_BASE,
        user_id=uuid4(),
        tenant_id=tenant_id,
        user_type="PLATFORM",
    )


async def _consume_one(
    gen: AsyncIterator[AsyncSession],
) -> AsyncSession:
    """Advance the dependency to its yield point and return the session."""
    return await gen.__anext__()


async def _close(gen: AsyncIterator[AsyncSession]) -> None:
    """Drive the dependency past its yield so commit/cleanup runs."""
    try:
        await gen.__anext__()
    except StopAsyncIteration:
        pass


# ---------------------------------------------------------------------------
# T9-T13: session var setting
# ---------------------------------------------------------------------------


async def test_t9_tenant_tenant_id_flows(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """TENANT auth.tenant_id is set on app.tenant_id."""
    tid = uuid4()
    auth = _tenant_auth(tid)
    gen = get_tenant_session(auth, session_factory)
    session = await _consume_one(gen)
    try:
        result = await session.execute(
            text("SELECT current_setting('app.tenant_id', TRUE)")
        )
        assert UUID(str(result.scalar())) == tid
    finally:
        await _close(gen)


async def test_t10_tenant_user_type_flows(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """TENANT auth.user_type is set on app.user_type."""
    auth = _tenant_auth(uuid4())
    gen = get_tenant_session(auth, session_factory)
    session = await _consume_one(gen)
    try:
        result = await session.execute(
            text("SELECT current_setting('app.user_type', TRUE)")
        )
        assert result.scalar() == "TENANT"
    finally:
        await _close(gen)


async def test_t11_platform_no_impersonation_can_query_without_raising(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """PLATFORM with auth.tenant_id=None: querying a multi-tenant table
    does not raise.

    set_config('app.tenant_id', NULL, true) leaves the GUC at empty
    string (Postgres 15: placeholder GUCs cannot be unset once
    registered). The NULLIF wrapper in the RLS policy is what makes
    ``''::uuid`` not crash. Pre-Step-3.0 the query returned zero rows
    because the policy was a single-clause ``tenant_id = NULL``;
    Step 3.0 (D-29) added an unconditional PLATFORM OR-branch on the
    multi-tenant policies (NOT NULL ``tenant_id`` / ``id`` form), so
    a PLATFORM session now sees every row regardless of
    ``app.tenant_id``.

    The assertion below was originally ``== 0`` (true under the
    pre-3.0 policy with an empty DB), updated to a non-negative int
    after Step 3.5 populated the DB. The test's intent is unchanged:
    PROVE the query does not raise. The exact returned count is not
    the assertion's load-bearing concern.
    """
    auth = _platform_auth(tenant_id=None)
    gen = get_tenant_session(auth, session_factory)
    session = await _consume_one(gen)
    try:
        # Query against tenants table; would raise without NULLIF.
        result = await session.execute(text("SELECT count(*) FROM tenants"))
        count = result.scalar()
        assert isinstance(count, int)
        assert count >= 0
    finally:
        await _close(gen)


async def test_t12_platform_user_type_flows(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """PLATFORM auth.user_type is set on app.user_type."""
    auth = _platform_auth(tenant_id=None)
    gen = get_tenant_session(auth, session_factory)
    session = await _consume_one(gen)
    try:
        result = await session.execute(
            text("SELECT current_setting('app.user_type', TRUE)")
        )
        assert result.scalar() == "PLATFORM"
    finally:
        await _close(gen)


async def test_t13_platform_with_impersonation_tenant_id_flows(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """PLATFORM with impersonation tenant_id: app.tenant_id is set
    (FN-AB-14 permissive)."""
    tid = uuid4()
    auth = _platform_auth(tenant_id=tid)
    gen = get_tenant_session(auth, session_factory)
    session = await _consume_one(gen)
    try:
        result = await session.execute(
            text("SELECT current_setting('app.tenant_id', TRUE)")
        )
        assert UUID(str(result.scalar())) == tid
    finally:
        await _close(gen)


# ---------------------------------------------------------------------------
# T14: concurrent session isolation
# ---------------------------------------------------------------------------


async def test_t14_concurrent_sessions_no_cross_leakage(
    settings: Settings,
) -> None:
    """Two sessions on different physical connections see only their own
    tenant_id."""
    # pool_size=2, max_overflow=0 forces both sessions onto distinct
    # physical connections (no implicit serialisation through a single
    # checkout).
    from sqlalchemy.ext.asyncio import create_async_engine

    eng = create_async_engine(
        settings.database_url,
        pool_size=2,
        max_overflow=0,
        pool_timeout=10,
        pool_pre_ping=True,
        connect_args={"prepare_threshold": None},
    )
    # Apply the search_path hook the same way create_engine does.
    from sqlalchemy import event

    @event.listens_for(eng.sync_engine, "connect")
    def _set_path(dbapi_conn, _conn_record):  # type: ignore[no-untyped-def]
        cur = dbapi_conn.cursor()
        cur.execute(f"SET search_path TO {settings.db_schema}, public")
        cur.close()

    factory = create_session_factory(eng)
    tid_a = uuid4()
    tid_b = uuid4()
    auth_a = _tenant_auth(tid_a)
    auth_b = _tenant_auth(tid_b)

    async def observe(auth: AuthContext, expected: UUID) -> UUID:
        gen = get_tenant_session(auth, factory)
        session = await _consume_one(gen)
        try:
            # Yield control so the other coroutine can run inside its
            # own transaction concurrently.
            await asyncio.sleep(0.05)
            result = await session.execute(
                text("SELECT current_setting('app.tenant_id', TRUE)")
            )
            seen = UUID(str(result.scalar()))
            assert seen == expected
            return seen
        finally:
            await _close(gen)

    try:
        seen_a, seen_b = await asyncio.gather(
            observe(auth_a, tid_a),
            observe(auth_b, tid_b),
        )
        assert seen_a == tid_a
        assert seen_b == tid_b
        assert seen_a != seen_b
    finally:
        await eng.dispose()


# ---------------------------------------------------------------------------
# T15: no leakage to subsequent connections
# ---------------------------------------------------------------------------


async def test_t15_reused_connection_can_query_without_raising(
    engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """After a get_tenant_session transaction ends, a fresh raw
    connection from the pool can query a multi-tenant table without
    raising.

    Postgres 15 registers `app.tenant_id` at session level the first
    time set_config(name, value, true) runs. Once registered,
    `current_setting` returns '' on the same connection past the
    transaction, never NULL. Without the NULLIF wrapper in the RLS
    policy, the next query against any multi-tenant table would
    crash with `invalid input syntax for type uuid: ""`. With NULLIF
    in place (this step's migration e59f62d5037d), `''` becomes NULL
    and default-deny holds.
    """
    tid = uuid4()
    auth = _tenant_auth(tid)
    gen = get_tenant_session(auth, session_factory)
    session = await _consume_one(gen)
    # Confirm it was set inside.
    result = await session.execute(
        text("SELECT current_setting('app.tenant_id', TRUE)")
    )
    assert UUID(str(result.scalar())) == tid
    await _close(gen)

    # Fresh connection (bypass get_tenant_session). The pool may hand
    # back the same physical connection just used; if so, the GUC is
    # at '' (registered, post-COMMIT). NULLIF in the RLS policy must
    # treat that as NULL so this query does not raise.
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT count(*) FROM tenants"))
        assert result.scalar() == 0
