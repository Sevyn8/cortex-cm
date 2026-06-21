"""Async SQLAlchemy engine factory and runtime privilege check.

The engine is created from Settings at app startup (Step 2.4 wires
this into the FastAPI lifespan). Pool configuration is conservative
for v0; tune post-launch if metrics show contention.

Connect-time hook sets `search_path` for every new physical connection
as belt-and-suspenders against role-default drift (per D-15).
Per-request session vars (`app.tenant_id`, `app.user_type`) are set in
the dependency, not here, because they depend on AuthContext.

Server-side prepared statements are disabled via
`connect_args={"prepare_threshold": None}` per D-14: PgBouncer in
transaction-pooling mode does not allow named prepared statements
across connections, and keeping them off from day one means adding
PgBouncer post-MVP does not require ripping out connection caching.
"""
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from admin_backend.config import Settings
from admin_backend.errors import AppRolePrivilegeError

# Re-export so existing `from admin_backend.db.engine import
# AppRolePrivilegeError` callers (e.g. tests/unit/test_engine.py) keep
# working. Canonical home is admin_backend.errors per the Step 2.3
# structured-error refactor.
__all__ = [
    "AppRolePrivilegeError",
    "create_engine",
    "create_session_factory",
    "assert_app_role_no_bypassrls",
]


def create_engine(settings: Settings) -> AsyncEngine:
    """Create the async engine with pool config and connect-time hook."""
    engine = create_async_engine(
        settings.database_url,
        pool_size=10,
        max_overflow=5,
        pool_timeout=30,
        # pool_pre_ping=True issues SELECT 1 before each checkout. ~1ms
        # locally, ~5-10ms in cloud (over Cloud SQL proxy). Detects
        # stale connections after Cloud SQL restarts, firewall idle-
        # kills, network partitions. Worth it for v0; revisit if
        # metrics show this is the dominant per-request latency.
        pool_pre_ping=True,
        # pool_recycle=1800 closes physical connections every 30 min.
        # Defence against Cloud SQL's idle-connection drop and against
        # any firewall that idle-kills connections. Harmless on local.
        pool_recycle=1800,
        # prepare_threshold=None disables psycopg3 server-side prepared
        # statements (D-14: PgBouncer-readiness from day one).
        connect_args={"prepare_threshold": None},
        echo=False,
    )

    # Connect-time hook: set search_path on every new physical
    # connection. Belt-and-suspenders: role-level default search_path
    # is also set at DB setup time. Explicit per-connection is what we
    # depend on per D-15.
    #
    # f-string is safe: db_schema is field-validated as a Postgres
    # identifier at Settings construction time; cannot contain SQL
    # injection.
    @event.listens_for(engine.sync_engine, "connect")
    def set_search_path_on_connect(dbapi_conn, connection_record):  # type: ignore[no-untyped-def]
        cursor = dbapi_conn.cursor()
        cursor.execute(f"SET search_path TO {settings.db_schema}, public")
        cursor.close()

    return engine


def create_session_factory(
    engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    """Create the async sessionmaker bound to this engine.

    `expire_on_commit=False` is the conventional async setting:
    prevents implicit refreshes after commit, which would issue
    queries inside a closed transaction context.
    """
    return async_sessionmaker(
        engine,
        expire_on_commit=False,
        class_=AsyncSession,
    )


async def assert_app_role_no_bypassrls(engine: AsyncEngine) -> None:
    """Verify the connecting role has neither SUPERUSER nor BYPASSRLS.

    Either attribute bypasses RLS entirely, regardless of FORCE on
    tables. This function MUST run at app startup; if it raises, the
    app must refuse to start. Step 2.4 wires this into the FastAPI
    lifespan.

    Note: `current_user` returns the active role. v0 does not use SET
    ROLE anywhere, so current_user equals the originally-connected
    role. If a future code path uses SET ROLE, this check would need
    to use `session_user` instead.

    Raises:
        AppRolePrivilegeError: the connecting role has one or both
            attributes.
    """
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT rolsuper, rolbypassrls FROM pg_roles "
                "WHERE rolname = current_user"
            )
        )
        row = result.fetchone()
        if row is None:
            raise AppRolePrivilegeError(
                "Could not query pg_roles for current_user; "
                "this is itself suspicious."
            )
        rolsuper, rolbypassrls = row
        if rolsuper or rolbypassrls:
            raise AppRolePrivilegeError(
                f"Application role has SUPERUSER={rolsuper}, "
                f"BYPASSRLS={rolbypassrls}. RLS is silently bypassed. "
                "Refusing to start. Strip privileges with: "
                "ALTER ROLE <role> NOSUPERUSER NOBYPASSRLS;"
            )
