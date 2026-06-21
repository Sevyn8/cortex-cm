"""Shared fixtures for integration tests (Steps 2.3, 2.4, 3.2).

Originally extracted from tests/integration/test_middleware.py at
Step 2.4 (FastAPI / middleware fixtures). Step 3.2 added the
repo-layer fixtures (engine, session_factory, platform_auth,
tenant_auth_factory, make_tenant, platform_session,
tenant_session_factory) — these are the building blocks for every
subsequent Repo's integration tests (stores 4.5, platform_users 5.1,
etc.).

The `app_with_test_routes` fixture manually wires app.state to skip
the lifespan (which would run assert_app_role_no_bypassrls and install
logging globally). The real /v1/health and /v1/ready are now
registered by create_app() itself; the previous stub /v1/health from
Step 2.3's fixture has been dropped to avoid the route-registration
conflict.

Repo-layer test pattern (Step 3.2 onwards). The make_tenant factory
*commits* (it has to: setup runs in one get_tenant_session call as
PLATFORM, assertions run in a separate get_tenant_session call as
PLATFORM or TENANT — they are different transactions on potentially
different connections from the pool, so setup must be visible to the
assertion phase). Cleanup is via explicit DELETE in fixture teardown,
not via transaction rollback. This pattern only works post-Step-3.0:
pre-3.0, the PLATFORM session's WITH CHECK predicate would have
rejected the INSERT because tenants_self_access lacked the
PLATFORM-visibility OR-branch.
"""
import io
import logging
import uuid
from datetime import datetime
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any
from uuid import UUID

import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pythonjsonlogger import jsonlogger
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)

from admin_backend.auth.context import AuthContext
from admin_backend.auth.stub import StubAuthClient
from admin_backend.auth.testing import make_test_jwt
from admin_backend.config import Settings, get_settings
from admin_backend.db.engine import create_engine, create_session_factory
from admin_backend.db.session import get_tenant_session
from admin_backend.dependencies import (
    get_auth_context,
    get_tenant_session_dep,
)
from admin_backend.errors import ServerError
from admin_backend.main import create_app
from admin_backend.models.org_node import OrgNode, OrgNodeStatus, OrgNodeType
from admin_backend.models.store import Store, StoreStatus, TaxTreatment
from admin_backend.models.tenant_user import TenantUser, TenantUserStatus
from admin_backend.models.tenant import (
    Tenant,
    TenantRegion,
    TenantStatus,
)
from admin_backend.models.tenant_module_access import (
    ModuleAccessStatus,
    ModuleCode,
    TenantModuleAccess,
)


@pytest.fixture(scope="module")
def settings() -> Settings:
    return get_settings()


@pytest_asyncio.fixture
async def app_with_test_routes(settings: Settings) -> Any:
    """FastAPI app with middlewares + test-only routes.

    Manually wires app.state to skip the lifespan; async fixture so
    engine.dispose() runs in the same event loop as the test.
    """
    app = create_app()

    engine = create_engine(settings)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)
    app.state.auth_client = StubAuthClient(settings)

    @app.get("/v1/_test_protected", include_in_schema=False)
    async def _test_protected(
        auth: AuthContext = Depends(get_auth_context),
    ) -> dict[str, str | None]:
        return {
            "tenant_id": (
                str(auth.tenant_id) if auth.tenant_id else None
            ),
            "user_type": auth.user_type,
            "user_id": str(auth.user_id),
        }

    @app.get("/v1/_test_db_user_type", include_in_schema=False)
    async def _test_db_user_type(
        session: Any = Depends(get_tenant_session_dep),
    ) -> dict[str, str | None]:
        result = await session.execute(
            text("SELECT current_setting('app.user_type', TRUE)")
        )
        return {"db_user_type": result.scalar()}

    @app.get("/v1/_test_server_error", include_in_schema=False)
    async def _test_server_error() -> dict[str, str]:
        class _SecretLeakError(ServerError):
            pass
        raise _SecretLeakError(
            "the database is on fire and the keys leaked"
        )

    yield app

    await engine.dispose()


@pytest.fixture
def client(app_with_test_routes: FastAPI) -> TestClient:
    return TestClient(app_with_test_routes)


@pytest.fixture
def valid_tenant_jwt(settings: Settings) -> tuple[str, str]:
    """A valid TENANT JWT and the tenant_id it carries."""
    tenant_id = "11111111-1111-1111-1111-111111111111"
    user_id = "22222222-2222-2222-2222-222222222222"
    token = make_test_jwt(
        settings,
        user_id=uuid.UUID(user_id),
        user_type="TENANT",
        tenant_id=uuid.UUID(tenant_id),
    )
    return token, tenant_id


@pytest.fixture
def json_log_buffer() -> Any:
    """Buffer-backed JSON handler attached to admin_backend.requests."""
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(
        jsonlogger.JsonFormatter(  # type: ignore[attr-defined]
            "%(asctime)s %(name)s %(levelname)s %(message)s",
            rename_fields={"asctime": "timestamp", "levelname": "level"},
        )
    )

    request_logger = logging.getLogger("admin_backend.requests")
    original_handlers = request_logger.handlers[:]
    original_propagate = request_logger.propagate
    request_logger.handlers = [handler]
    request_logger.setLevel(logging.INFO)
    request_logger.propagate = False

    yield buffer

    request_logger.handlers = original_handlers
    request_logger.propagate = original_propagate


@pytest.fixture
def error_log_buffer() -> Any:
    """Buffer-backed JSON handler attached to admin_backend.errors."""
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(
        jsonlogger.JsonFormatter(  # type: ignore[attr-defined]
            "%(asctime)s %(name)s %(levelname)s %(message)s",
            rename_fields={"asctime": "timestamp", "levelname": "level"},
        )
    )

    err_logger = logging.getLogger("admin_backend.errors")
    original_handlers = err_logger.handlers[:]
    original_propagate = err_logger.propagate
    err_logger.handlers = [handler]
    err_logger.setLevel(logging.ERROR)
    err_logger.propagate = False

    yield buffer

    err_logger.handlers = original_handlers
    err_logger.propagate = original_propagate


# ============================================================================
# Repo-layer fixtures (Step 3.2 onwards)
#
# The fixtures below build up from the engine to the per-test factories.
# They are independent of `app_with_test_routes` (which wires the FastAPI
# app for middleware/health tests) — repo-layer integration tests bypass
# FastAPI entirely per the test pyramid (CLAUDE.md "Test pyramid").
# ============================================================================


# Mirror of test_session.py's _VALID_AUTH_BASE — the fields AuthContext
# requires beyond user_id/tenant_id/user_type. Kept private to this
# module: future shared test-helper extraction is a separate concern.
_VALID_AUTH_BASE: dict[str, object] = {
    "sub": "test-sub",
    "iss": "https://stub-issuer.local/",
    "aud": "https://api.test/",
    "exp": 9999999999,
    "email": "test@ithina.local",
}


@pytest_asyncio.fixture
async def engine(settings: Settings) -> AsyncIterator[AsyncEngine]:
    """Function-scoped async engine for repo-layer tests.

    Function-scoped so each test owns its own pool and the engine is
    bound to the same event loop pytest-asyncio created for the test.
    Module/session scope here would need event_loop scoping changes
    that aren't worth the speedup at v0 size.
    """
    eng = create_engine(settings)
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest_asyncio.fixture
async def session_factory(
    engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    """async_sessionmaker bound to the test's engine."""
    return create_session_factory(engine)


@pytest.fixture
def platform_auth() -> AuthContext:
    """Synthetic PLATFORM AuthContext for fixture-only DB operations.

    Not JWT-minted-and-verified (that path lives in Step 2.3 middleware
    tests). The frozen Pydantic model is constructed directly with a
    fresh `user_id` per test; PLATFORM with `tenant_id=None` matches
    the standard non-impersonating shape (D-24).

    `# type: ignore[call-arg]` is the same pattern used at Step 2.2a's
    `tests/unit/test_session.py` — mypy strict + Pydantic v2 + dict
    unpacking interact awkwardly for required fields.
    """
    return AuthContext(  # type: ignore[call-arg]
        **_VALID_AUTH_BASE,
        user_id=uuid.uuid4(),
        tenant_id=None,
        user_type="PLATFORM",
    )


@pytest.fixture
def tenant_auth_factory() -> Callable[[UUID], AuthContext]:
    """Returns a callable: tenant_id -> TENANT-context AuthContext."""

    def _make(tenant_id: UUID) -> AuthContext:
        return AuthContext(  # type: ignore[call-arg]
            **_VALID_AUTH_BASE,
            user_id=uuid.uuid4(),
            tenant_id=tenant_id,
            user_type="TENANT",
        )

    return _make


@pytest_asyncio.fixture
async def make_tenant(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> AsyncIterator[Callable[..., Awaitable[Tenant]]]:
    """Async factory: insert + commit a Tenant via PLATFORM session.

    Returns the persisted ORM object with `id` populated by the DB
    DEFAULT `uuidv7()`. Tracks created IDs and DELETEs them at
    teardown. Each setup-and-assert phase uses its own
    `get_tenant_session` invocation (separate transactions on
    potentially different connections); for the assert phase to see
    the setup data, setup must commit. Cleanup is by explicit DELETE.

    The PLATFORM session's WITH CHECK admits the INSERT via the
    OR-clause landed at Step 3.0 (D-29). Pre-3.0, this would fail.

    Usage:
        tenant_a = await make_tenant(name="Alpha")
        tenant_b = await make_tenant(
            name="Bravo", status=TenantStatus.ONBOARDING)
        # Anchor-reachable tenant (Step 6.15 amendment):
        tenant_c = await make_tenant(name="Charlie", with_root=True)

    Pass ``with_root=True`` when the test exercises any endpoint
    gated with ``anchor_dep=get_tenant_anchor`` (or any anchor dep
    that resolves an org_node). Without the root, the anchor dep
    returns 404 ahead of the gate body, masking the actual test
    intent. Default ``False`` preserves the existing semantics for
    tests that do not need anchor reachability.

    When ``with_root=True``: after inserting the tenant row, the
    factory also inserts a TENANT-type root ``org_node`` anchored at
    the tenant. The org_node ``code`` is ``t-<short-hex>`` (mirror of
    the retired ``_make_tenant_with_root`` helper from
    test_module_access_writes_router.py); the format respects
    ``ck_org_nodes_code_format`` (alphanumerics + hyphens, no
    underscores). The cleanup of the org_node is delegated to the
    cascading DELETE on tenant teardown (no explicit org_node DELETE
    needed because ``fk_org_nodes_tenant`` is ON DELETE RESTRICT only
    against the tenant_id pointing back; tenant DELETE here happens
    AFTER all FKs from the tenant_id are gone — including ours, so we
    DELETE the root org_node first in teardown).

    Teardown runs even if the test body raises (pytest fixture
    finalisation is finally-equivalent), so leftover rows from a
    failed test don't accumulate.
    """
    created_ids: list[UUID] = []
    created_root_node_ids: list[UUID] = []
    schema = get_settings().db_schema

    async def _make(
        *,
        name: str = "Test Tenant",
        region: TenantRegion = TenantRegion.US,
        status: TenantStatus = TenantStatus.ACTIVE,
        with_root: bool = False,
        **overrides: Any,
    ) -> Tenant:
        tenant = Tenant(
            name=name, region=region, status=status, **overrides
        )
        async for session in get_tenant_session(
            platform_auth, session_factory
        ):
            session.add(tenant)
            await session.flush()  # populate DB defaults (id, created_at)
            await session.refresh(tenant)
            created_ids.append(tenant.id)
        # The async-for body runs once. Exiting the loop drives the
        # generator to completion: `session.begin()` exits cleanly
        # (commit) and the session is closed. `expire_on_commit=False`
        # at Step 2.2a keeps the returned Tenant's attributes loaded
        # even after detach.

        if with_root:
            # Insert a TENANT-type root org_node so anchor deps
            # (get_tenant_anchor and equivalents) resolve. Raw SQL
            # mirrors make_org_node's pattern; we don't reuse that
            # fixture here because it owns its own teardown tracker
            # and we want the root to be cleaned up alongside the
            # tenant under this fixture's teardown.
            #
            # code constraint: ck_org_nodes_code_format requires
            # ``^[A-Za-z0-9][A-Za-z0-9-]+[A-Za-z0-9]$`` — no
            # underscores. Mirror of the retired
            # _make_tenant_with_root helper convention.
            code = f"t-{tenant.id.hex[:8]}"
            path = code.replace("-", "_")
            root_node_id = uuid.uuid4()
            async for session in get_tenant_session(
                platform_auth, session_factory
            ):
                await session.execute(
                    text(
                        f"INSERT INTO {schema}.org_nodes ("
                        "  id, tenant_id, parent_id, path, node_type,"
                        "  name, code, status,"
                        "  created_by_user_id, created_by_user_type,"
                        "  updated_by_user_id, updated_by_user_type"
                        ") VALUES ("
                        "  :id, :tenant_id, NULL,"
                        "  CAST(:path AS ltree),"
                        f"  CAST('TENANT' AS {schema}.org_node_type_enum),"
                        "  :name, :code,"
                        f"  CAST('ACTIVE' AS {schema}.org_node_status_enum),"
                        "  NULL, NULL, NULL, NULL"
                        ")"
                    ),
                    {
                        "id": root_node_id,
                        "tenant_id": tenant.id,
                        "path": path,
                        "name": name,
                        "code": code,
                    },
                )
            created_root_node_ids.append(root_node_id)

        return tenant

    yield _make

    # Teardown order (each step's FK to ``tenants`` is ON DELETE
    # RESTRICT):
    #   1. audit rows  (Step 6.16.4 extension: ``tenant_activity_audit_logs``
    #      and ``platform_activity_audit_logs`` both pin tenants);
    #   2. root org_nodes;
    #   3. tenants.
    # The audit cleanup mirrors the ``cleanup_tenants_router`` fixture
    # introduced by Step 6.16.2 for ``test_tenants_writes_router.py``,
    # promoted here so any test that creates a tenant via ``make_tenant``
    # and then triggers an audit-emitting endpoint (Step 6.16.4 onward)
    # cleans up without further per-test wiring.
    if created_ids:
        async for session in get_tenant_session(
            platform_auth, session_factory
        ):
            await session.execute(
                text(
                    f"DELETE FROM {schema}.tenant_activity_audit_logs "
                    "WHERE tenant_id = ANY(:ids)"
                ),
                {"ids": created_ids},
            )
            await session.execute(
                text(
                    f"DELETE FROM {schema}.platform_activity_audit_logs "
                    "WHERE tenant_id = ANY(:ids)"
                ),
                {"ids": created_ids},
            )

    if created_root_node_ids:
        async for session in get_tenant_session(
            platform_auth, session_factory
        ):
            await session.execute(
                text(
                    f"DELETE FROM {schema}.org_nodes WHERE id = ANY(:ids)"
                ),
                {"ids": created_root_node_ids},
            )

    if created_ids:
        async for session in get_tenant_session(
            platform_auth, session_factory
        ):
            await session.execute(
                delete(Tenant).where(Tenant.id.in_(created_ids))
            )


@pytest_asyncio.fixture
async def platform_session(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> AsyncIterator[AsyncSession]:
    """A single PLATFORM-scoped AsyncSession for read-side test phases.

    Used after `make_tenant` has committed setup rows. PLATFORM
    visibility on multi-tenant tables comes from D-29's OR-branch.
    """
    async for session in get_tenant_session(
        platform_auth, session_factory
    ):
        yield session


@pytest.fixture
def tenant_session_factory(
    session_factory: async_sessionmaker[AsyncSession],
    tenant_auth_factory: Callable[[UUID], AuthContext],
) -> Callable[[UUID], AbstractAsyncContextManager[AsyncSession]]:
    """Callable returning an async context manager yielding a TENANT-scoped session.

    Factory shape (rather than a fixture parameterised on tenant_id)
    so a single test can open multiple TENANT-scoped sessions if
    needed. RLS isolates rows to the supplied `tenant_id`'s context.

    Usage:
        async with tenant_session_factory(tenant_a.id) as session:
            results = await repo.list_all(session)
    """

    @asynccontextmanager
    async def _open(tenant_id: UUID) -> AsyncIterator[AsyncSession]:
        auth = tenant_auth_factory(tenant_id)
        async for session in get_tenant_session(auth, session_factory):
            yield session

    return _open


# ============================================================================
# Step 3.3 fixtures: make_store + make_tenant_user
#
# These mirror Step 3.2's `make_tenant`: async factory, PLATFORM session
# insert, commit, DELETE-tracked teardown. `make_tenant_user` uses raw
# SQL via the live ORM stub at `admin_backend.models.tenant_user` (full
# model since Step 5.2); `make_store` was raw-SQL against the
# 2-column lightweight stub until Step 6.17.2 upgraded it to ORM-native
# inserts via the full `models.store.Store`.
#
# These factories also serve Step 3.3 directly — the tenants router's
# aggregate endpoints exercise stores and tenant_users counts, so the
# integration tests need a way to create those rows.
# ============================================================================


@pytest_asyncio.fixture
async def make_store(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> AsyncIterator[Callable[..., Awaitable[Store]]]:
    """Async factory: insert + commit a Store via PLATFORM session,
    return persisted ORM object. Tracks IDs and DELETEs at teardown.

    Step 6.17.2 upgraded from raw-SQL INSERT (2-column lightweight
    stub) to ORM-native via the full ``models.store.Store``. The
    signature preserves backwards-compatibility with existing call
    sites in ``test_tenants_router.py`` and ``test_dashboard_router.py``;
    callers passing no overrides get a US-country, ACTIVE, exclusive-tax
    store with audit-actor pairs left NULL/NULL (Pattern (b) XOR-permitted).

    The ``country`` parameter (new at 6.17.2) lets callers override the
    default ``'United States'`` directly — used by ``test_dashboard_router
    ::S6`` to spread stores across multiple countries without a raw
    UPDATE post-insert.

    Step 6.21.2 made ``stores.org_node_id`` NOT NULL. To preserve
    backward compatibility for callers that omit ``org_node_id``, the
    fixture now auto-provisions a paired STORE-type org_node (under a
    get-or-create TENANT-root for the tenant) and tracks it for
    teardown alongside the store. Callers passing an explicit
    ``org_node_id`` skip the auto-provisioning path.

    Audit-actor pairs (``created_*``, ``updated_*``, ``closed_*``) are
    set to NULL/NULL — XOR-permitted by ``ck_stores_created_by_actor_pair``
    and its siblings. Tests that need populated actor pairs can use a
    follow-on UPDATE.
    """
    schema = get_settings().db_schema
    created_ids: list[UUID] = []
    auto_provisioned_org_node_ids: list[UUID] = []
    auto_provisioned_root_ids: list[UUID] = []

    async def _ensure_tenant_root(
        session: AsyncSession, tenant_id: UUID
    ) -> tuple[UUID, str]:
        """Get-or-create the TENANT-root org_node for ``tenant_id``.

        Returns ``(id, path)``. Reuses an existing root when present
        so co-located fixtures (e.g., ``make_tenant(with_root=True)``)
        don't double-up.
        """
        existing = await session.execute(
            text(
                f"SELECT id, path::text AS path FROM {schema}.org_nodes "
                "WHERE tenant_id = :tenant_id "
                f"AND node_type = CAST('TENANT' AS {schema}.org_node_type_enum) "
                "AND parent_id IS NULL "
                "LIMIT 1"
            ),
            {"tenant_id": tenant_id},
        )
        row = existing.first()
        if row is not None:
            return uuid.UUID(str(row.id)), str(row.path)
        # Create one.
        new_id = uuid.uuid4()
        code = f"t-{tenant_id.hex[:8]}"
        path = code.replace("-", "_")
        await session.execute(
            text(
                f"INSERT INTO {schema}.org_nodes ("
                "  id, tenant_id, parent_id, path, node_type,"
                "  name, code"
                ") VALUES ("
                "  :id, :tenant_id, NULL,"
                "  CAST(:path AS ltree),"
                f"  CAST('TENANT' AS {schema}.org_node_type_enum),"
                "  :name, :code"
                ")"
            ),
            {
                "id": new_id,
                "tenant_id": tenant_id,
                "path": path,
                "name": "Fixture Root",
                "code": code,
            },
        )
        auto_provisioned_root_ids.append(new_id)
        return new_id, path

    async def _make(
        *,
        tenant_id: UUID,
        org_node_id: UUID | None = None,
        name: str | None = None,
        store_code: str | None = None,
        country: str = "United States",
        timezone: str = "America/New_York",
        currency: str = "USD",
        tax_treatment: TaxTreatment = TaxTreatment.EXCLUSIVE,
        status: StoreStatus = StoreStatus.ACTIVE,
    ) -> Store:
        # Step 6.21.2: auto-provision a paired STORE-type org_node when
        # the caller didn't supply one. Keeps existing call sites that
        # predate the NOT NULL constraint working unchanged.
        if org_node_id is None:
            async for session in get_tenant_session(
                platform_auth, session_factory
            ):
                root_id, root_path = await _ensure_tenant_root(
                    session, tenant_id
                )
                new_node_id = uuid.uuid4()
                short = uuid.uuid4().hex[:8]
                node_code = f"sn-{short}"
                node_path = f"{root_path}.{node_code.replace('-', '_')}"
                await session.execute(
                    text(
                        f"INSERT INTO {schema}.org_nodes ("
                        "  id, tenant_id, parent_id, path, node_type,"
                        "  name, code"
                        ") VALUES ("
                        "  :id, :tenant_id, :parent_id,"
                        "  CAST(:path AS ltree),"
                        f"  CAST('STORE' AS {schema}.org_node_type_enum),"
                        "  :name, :code"
                        ")"
                    ),
                    {
                        "id": new_node_id,
                        "tenant_id": tenant_id,
                        "parent_id": root_id,
                        "path": node_path,
                        "name": name if name is not None else f"Store-{short}",
                        "code": node_code,
                    },
                )
                auto_provisioned_org_node_ids.append(new_node_id)
                org_node_id = new_node_id

        store = Store(
            tenant_id=tenant_id,
            org_node_id=org_node_id,
            name=name if name is not None else f"Store-{uuid.uuid4()}",
            store_code=store_code,
            country=country,
            timezone=timezone,
            currency=currency,
            tax_treatment=tax_treatment,
            status=status,
        )
        async for session in get_tenant_session(
            platform_auth, session_factory
        ):
            session.add(store)
            await session.flush()  # populate DB defaults (id, created_at)
            await session.refresh(store)
            created_ids.append(store.id)
        return store

    yield _make

    # Teardown order matters: stores first (releases FK ref on
    # org_node_id), then the auto-provisioned STORE-type org_nodes,
    # then the auto-provisioned TENANT roots (children removed first
    # so the root has no inbound FKs).
    if created_ids:
        async for session in get_tenant_session(
            platform_auth, session_factory
        ):
            await session.execute(
                delete(Store).where(Store.id.in_(created_ids))
            )
    if auto_provisioned_org_node_ids:
        async for session in get_tenant_session(
            platform_auth, session_factory
        ):
            await session.execute(
                text(
                    f"DELETE FROM {schema}.org_nodes WHERE id = ANY(:ids)"
                ),
                {"ids": auto_provisioned_org_node_ids},
            )
    if auto_provisioned_root_ids:
        async for session in get_tenant_session(
            platform_auth, session_factory
        ):
            await session.execute(
                text(
                    f"DELETE FROM {schema}.org_nodes WHERE id = ANY(:ids)"
                ),
                {"ids": auto_provisioned_root_ids},
            )


@pytest_asyncio.fixture
async def make_tenant_user(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> AsyncIterator[Callable[..., Awaitable[TenantUser]]]:
    """Async factory: insert + commit a TenantUser via PLATFORM session,
    return persisted ORM object. Tracks IDs and DELETEs at teardown.

    ``status`` defaults to ``'ACTIVE'`` (the value relevant to the
    ``num_users_active`` subquery). Supported values for the fixture:
    ``ACTIVE`` and ``INVITED`` — together they exercise both branches
    of the ``num_users_active`` filter. ``SUSPENDED`` is intentionally
    out of scope here because it pulls in the ``suspended_*`` audit-
    actor tower (5.2 will own that).

    The DDL has several CHECK constraints that vary by status:
      - ``ck_tenant_users_auth0_sub_consistency``: INVITED -> NULL;
        ACTIVE/SUSPENDED -> NOT NULL.
      - ``ck_tenant_users_invitation_accepted_consistency``: same shape.

    The fixture sets ``auth0_sub`` and ``invitation_accepted_at`` for
    ACTIVE rows and leaves them NULL for INVITED rows so both pass.
    All audit-actor columns are NULL/NULL (XOR-permitted).
    """
    created_ids: list[UUID] = []

    async def _make(
        *,
        tenant_id: UUID,
        status: str = "ACTIVE",
        **overrides: Any,
    ) -> TenantUser:
        from sqlalchemy import text

        schema = get_settings().db_schema
        if status not in ("ACTIVE", "INVITED"):
            raise ValueError(
                f"make_tenant_user only supports status ACTIVE or "
                f"INVITED in v0; got {status!r}. Step 5.2 will widen."
            )

        new_id = uuid.uuid4()
        if status == "ACTIVE":
            auth0_sub: str | None = (
                f"auth0|fixture-{new_id}"
            )
            # Use a static-but-distinct timestamp; precise value
            # doesn't matter for any v0 test, only that it's NOT NULL.
            invitation_accepted_at: str | None = "2026-01-01 00:00:00+00"
        else:  # INVITED
            auth0_sub = None
            invitation_accepted_at = None

        async for session in get_tenant_session(
            platform_auth, session_factory
        ):
            await session.execute(
                text(
                    f"INSERT INTO {schema}.tenant_users ("
                    "  id, tenant_id, email, full_name, status,"
                    "  auth0_sub, invitation_accepted_at,"
                    "  created_by_user_id, created_by_user_type,"
                    "  updated_by_user_id, updated_by_user_type,"
                    "  suspended_by_user_id, suspended_by_user_type"
                    ") VALUES ("
                    "  :id, :tenant_id, :email, :full_name, :status,"
                    "  :auth0_sub, :iaat,"
                    "  NULL, NULL, NULL, NULL,"
                    "  NULL, NULL"
                    ")"
                ),
                {
                    "id": new_id,
                    "tenant_id": tenant_id,
                    "email": overrides.get(
                        "email", f"user-{new_id}@test.local"
                    ),
                    "full_name": overrides.get(
                        "full_name", f"User {new_id}"
                    ),
                    "status": status,
                    "auth0_sub": auth0_sub,
                    "iaat": invitation_accepted_at,
                },
            )
            created_ids.append(new_id)
        return TenantUser(
            id=new_id, tenant_id=tenant_id, status=TenantUserStatus(status)
        )

    yield _make

    if created_ids:
        async for session in get_tenant_session(
            platform_auth, session_factory
        ):
            await session.execute(
                delete(TenantUser).where(TenantUser.id.in_(created_ids))
            )


# ============================================================================
# Step 3.4.5 fixtures: make_platform_user + make_tenant_module_access
#
# `tenant_module_access` requires NOT NULL FK to platform_users on three
# audit-actor columns (enabled_by_user_id, created_by_user_id,
# updated_by_user_id) plus a nullable disabled_by_user_id. Tests need a
# real platform_users.id for these. `make_platform_user` provides one.
#
# `platform_users.created_by_user_id` is itself NULLABLE in the DDL, so
# the fixture passes NULL for the audit-actor pair and avoids any
# bootstrap chicken-and-egg problem. Default status is INVITED so the
# auth0_sub / invitation_accepted_at CHECK constraints are trivially
# satisfied (both NULL).
# ============================================================================


@pytest_asyncio.fixture
async def make_platform_user(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> AsyncIterator[Callable[..., Awaitable[Any]]]:
    """Async factory: insert + commit a platform_users row via PLATFORM session.

    Returns a small object with ``.id`` populated. Tracks IDs and
    DELETEs at teardown.

    Defaults: status='INVITED' (everything NULL — auth0_sub,
    invitation_accepted_at, suspended_*). Tests that need an ACTIVE
    user pass status='ACTIVE' plus the auth0_sub +
    invitation_accepted_at companion fields. SUSPENDED is intentionally
    out of scope (the suspended_* tower is heavier; Step 5.1's full
    PlatformUser model owns that).

    The lightweight return type is a SimpleNamespace with ``id``;
    tests typically need ``.id`` only.
    """
    from types import SimpleNamespace

    created_ids: list[UUID] = []

    schema = get_settings().db_schema

    async def _make(
        *,
        email: str | None = None,
        full_name: str = "Test Platform User",
        status: str = "INVITED",
        **overrides: Any,
    ) -> Any:
        if status not in ("INVITED", "ACTIVE"):
            raise ValueError(
                f"make_platform_user only supports status INVITED or "
                f"ACTIVE in v0; got {status!r}. Step 5.1 will widen."
            )

        new_id = uuid.uuid4()
        if email is None:
            email = f"pu-{new_id}@ithina.test"

        if status == "ACTIVE":
            auth0_sub: str | None = f"auth0|fixture-{new_id}"
            invitation_accepted_at: str | None = "2026-01-01 00:00:00+00"
        else:  # INVITED
            auth0_sub = None
            invitation_accepted_at = None

        async for session in get_tenant_session(
            platform_auth, session_factory
        ):
            await session.execute(
                text(
                    f"INSERT INTO {schema}.platform_users ("
                    "  id, email, full_name, status,"
                    "  auth0_sub, invitation_accepted_at,"
                    "  created_by_user_id, updated_by_user_id,"
                    "  suspended_by_user_id"
                    ") VALUES ("
                    "  :id, :email, :full_name, :status,"
                    "  :auth0_sub, :iaat,"
                    "  NULL, NULL,"
                    "  NULL"
                    ")"
                ),
                {
                    "id": new_id,
                    "email": email,
                    "full_name": full_name,
                    "status": status,
                    "auth0_sub": auth0_sub,
                    "iaat": invitation_accepted_at,
                },
            )
            created_ids.append(new_id)
        return SimpleNamespace(id=new_id, email=email, status=status)

    yield _make

    if created_ids:
        async for session in get_tenant_session(
            platform_auth, session_factory
        ):
            await session.execute(
                text(
                    f"DELETE FROM {schema}.platform_users WHERE id = ANY(:ids)"
                ),
                {"ids": created_ids},
            )


@pytest_asyncio.fixture
async def make_tenant_module_access(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> AsyncIterator[Callable[..., Awaitable[TenantModuleAccess]]]:
    """Async factory: insert + commit a tenant_module_access row via
    PLATFORM session. Tracks IDs and DELETEs at teardown.

    Required parameters:
        tenant_id           — FK to tenants.id
        module              — ModuleCode enum value
        enabled_by_user_id  — platform_users.id (NOT NULL FK)
        created_by_user_id  — platform_users.id (NOT NULL FK)
        updated_by_user_id  — platform_users.id (NOT NULL FK)

    Optional:
        status              — default ENABLED.
        enabled_at          — default datetime.now(UTC).
        disabled_at         — required if status=DISABLED.
        disabled_by_user_id — required if status=DISABLED (FK).

    Validates the DDL CHECK constraints client-side: status=DISABLED
    requires both disabled_at AND disabled_by_user_id; status=ENABLED
    requires both NULL.
    """
    from datetime import datetime, timezone

    schema = get_settings().db_schema
    created_ids: list[UUID] = []

    async def _make(
        *,
        tenant_id: UUID,
        module: ModuleCode,
        enabled_by_user_id: UUID,
        created_by_user_id: UUID,
        updated_by_user_id: UUID,
        status: ModuleAccessStatus = ModuleAccessStatus.ENABLED,
        enabled_at: datetime | None = None,
        disabled_at: datetime | None = None,
        disabled_by_user_id: UUID | None = None,
    ) -> TenantModuleAccess:
        if enabled_at is None:
            enabled_at = datetime.now(tz=timezone.utc)

        if status == ModuleAccessStatus.DISABLED:
            if disabled_at is None or disabled_by_user_id is None:
                raise ValueError(
                    "status=DISABLED requires disabled_at AND "
                    "disabled_by_user_id (per CHECK constraints)"
                )
        else:
            if disabled_at is not None or disabled_by_user_id is not None:
                raise ValueError(
                    "status=ENABLED requires both disabled_at and "
                    "disabled_by_user_id to be NULL"
                )

        new_id = uuid.uuid4()
        async for session in get_tenant_session(
            platform_auth, session_factory
        ):
            await session.execute(
                text(
                    f"INSERT INTO {schema}.tenant_module_access ("
                    "  id, tenant_id, module, status,"
                    "  enabled_at, enabled_by_user_id,"
                    "  disabled_at, disabled_by_user_id,"
                    "  created_by_user_id, updated_by_user_id"
                    ") VALUES ("
                    "  :id, :tenant_id, :module, :status,"
                    "  :enabled_at, :enabled_by,"
                    "  :disabled_at, :disabled_by,"
                    "  :created_by, :updated_by"
                    ")"
                ),
                {
                    "id": new_id,
                    "tenant_id": tenant_id,
                    "module": module.value,
                    "status": status.value,
                    "enabled_at": enabled_at,
                    "enabled_by": enabled_by_user_id,
                    "disabled_at": disabled_at,
                    "disabled_by": disabled_by_user_id,
                    "created_by": created_by_user_id,
                    "updated_by": updated_by_user_id,
                },
            )
            created_ids.append(new_id)
        return TenantModuleAccess(
            id=new_id,
            tenant_id=tenant_id,
            module=module,
            status=status,
            enabled_at=enabled_at,
            enabled_by_user_id=enabled_by_user_id,
            disabled_at=disabled_at,
            disabled_by_user_id=disabled_by_user_id,
            created_by_user_id=created_by_user_id,
            updated_by_user_id=updated_by_user_id,
        )

    yield _make

    if created_ids:
        async for session in get_tenant_session(
            platform_auth, session_factory
        ):
            await session.execute(
                delete(TenantModuleAccess).where(
                    TenantModuleAccess.id.in_(created_ids)
                )
            )


# ============================================================================
# Step 5.3 fixture: make_org_node
#
# Mirrors make_store's raw-SQL pattern: the live org_nodes table has more
# NOT NULL columns than the ORM model would infer for INSERT (and a
# composite FK + ltree path), so the fixture writes via raw SQL with a
# locally-built ltree path. The path is parent_path + "." + lowercased
# label of code (with hyphens -> underscores, since ltree labels can't
# contain hyphens). TENANT-type roots have path = lowered_label_of_code.
#
# Returns a (id, path) tuple so callers can pass parent_path on
# subsequent insertions for descendants.
# ============================================================================


@pytest_asyncio.fixture
async def make_org_node(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> AsyncIterator[Callable[..., Awaitable[tuple[UUID, str]]]]:
    """Async factory: insert + commit an org_nodes row via PLATFORM session.

    Returns ``(id, path)`` so descendants can be inserted with the
    correct parent_path. Tracks IDs and DELETEs at teardown — in
    REVERSE insertion order to satisfy the composite FK
    fk_org_nodes_parent_same_tenant (children before parents).

    Required parameters:
        tenant_id           — FK to tenants.id
        node_type           — one of OrgNodeType values (string ok)
        code                — short tenant-unique code
        name                — display name

    Optional:
        parent_id           — required if node_type != 'TENANT'
        parent_path         — required if node_type != 'TENANT';
                              ltree path of the parent
        status              — default 'ACTIVE'

    Audit-actor pairs are NULL/NULL (Pattern (b) XOR-permitted) for
    fixture simplicity. Path label = lowercased code with hyphens
    replaced by underscores (ltree label syntax disallows hyphens).
    """

    schema = get_settings().db_schema
    created_ids: list[UUID] = []

    async def _make(
        *,
        tenant_id: UUID,
        node_type: str,
        code: str,
        name: str,
        parent_id: UUID | None = None,
        parent_path: str | None = None,
        status: str = "ACTIVE",
    ) -> tuple[UUID, str]:
        new_id = uuid.uuid4()
        label = code.lower().replace("-", "_")
        if node_type == "TENANT":
            assert parent_id is None and parent_path is None, (
                "TENANT-type org_node: parent_id and parent_path must be None"
            )
            path = label
        else:
            assert parent_id is not None and parent_path is not None, (
                f"non-TENANT org_node ({node_type}): parent_id and "
                "parent_path are required"
            )
            path = f"{parent_path}.{label}"

        async for session in get_tenant_session(
            platform_auth, session_factory
        ):
            await session.execute(
                text(
                    f"INSERT INTO {schema}.org_nodes ("
                    "  id, tenant_id, parent_id, path, node_type,"
                    "  name, code, status,"
                    "  created_by_user_id, created_by_user_type,"
                    "  updated_by_user_id, updated_by_user_type"
                    ") VALUES ("
                    "  :id, :tenant_id, :parent_id,"
                    "  CAST(:path AS ltree),"
                    f"  CAST(:node_type AS {schema}.org_node_type_enum),"
                    "  :name, :code,"
                    f"  CAST(:status AS {schema}.org_node_status_enum),"
                    "  NULL, NULL, NULL, NULL"
                    ")"
                ),
                {
                    "id": new_id,
                    "tenant_id": tenant_id,
                    "parent_id": parent_id,
                    "path": path,
                    "node_type": node_type,
                    "name": name,
                    "code": code,
                    "status": status,
                },
            )
            created_ids.append(new_id)
        return new_id, path

    yield _make

    if created_ids:
        # Delete one-at-a-time in REVERSE insertion order. The composite
        # FK fk_org_nodes_parent_same_tenant uses ON DELETE RESTRICT,
        # so children must be gone before their parent. A single
        # DELETE...WHERE id IN(...) does not guarantee delete order.
        async for session in get_tenant_session(
            platform_auth, session_factory
        ):
            for node_id in reversed(created_ids):
                await session.execute(
                    delete(OrgNode).where(OrgNode.id == node_id)
                )


# ============================================================================
# Step 6.1 fixtures: make_role + make_permission + make_role_permission
#
# Raw-SQL-INSERT factories for the RBAC catalogue. Mirror Step 5.2's
# `make_tenant_user` shape: PLATFORM session, commit, DELETE-tracked
# teardown. Audit-actor pairs (Pattern (b)) left NULL/NULL on every row
# (XOR-permitted).
#
# Roles: code is unique platform-wide; ``ck_roles_code_format`` enforces
# ``^[A-Z][A-Z0-9_]{1,49}$`` so the factory uppercases / sanitises.
#
# Permissions: ``ck_permissions_code_format`` enforces the dotted
# four-part shape. The factory builds the code from the four enum
# values to keep callers from constructing malformed strings.
#
# Role_permissions: composite PK (role_id, permission_id). Factory
# tracks (role_id, permission_id) pairs and DELETEs by composite key
# at teardown (no surrogate id to track).
# ============================================================================


@pytest_asyncio.fixture
async def make_role(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> AsyncIterator[Callable[..., Awaitable[Any]]]:
    """Async factory: insert + commit a Role via PLATFORM session,
    return a SimpleNamespace with ``.id``, ``.audience``, ``.code``,
    and ``.name``. Tracks IDs and DELETEs at teardown.

    Defaults: status='ACTIVE', is_system=False. Audit-actor pairs
    NULL/NULL.

    The DDL ``ck_roles_archived_consistency`` requires status='ARCHIVED'
    iff archived_* columns are populated. The fixture only supports
    ACTIVE / INACTIVE; tests needing ARCHIVED roles construct them
    explicitly via raw SQL.
    """
    from types import SimpleNamespace

    schema = get_settings().db_schema
    created_ids: list[UUID] = []

    async def _make(
        *,
        audience: str,
        code: str | None = None,
        name: str | None = None,
        description: str | None = None,
        status: str = "ACTIVE",
        is_system: bool = False,
    ) -> Any:
        if audience not in ("PLATFORM", "TENANT"):
            raise ValueError(
                f"audience must be PLATFORM or TENANT; got {audience!r}"
            )
        if status not in ("ACTIVE", "INACTIVE"):
            raise ValueError(
                f"make_role only supports status ACTIVE or INACTIVE; "
                f"got {status!r}"
            )

        new_id = uuid.uuid4()
        # uuid.uuid4 hex is [0-9a-f]; we need [A-Z0-9_]{1,49} starting
        # with [A-Z]. Prefix with "R_" plus uppercase the hex.
        if code is None:
            code = f"R_{new_id.hex[:24].upper()}"
        if name is None:
            name = f"Role {new_id}"

        async for session in get_tenant_session(
            platform_auth, session_factory
        ):
            await session.execute(
                text(
                    f"INSERT INTO {schema}.roles ("
                    "  id, name, code, description, audience, status,"
                    "  is_system,"
                    "  created_by_user_id, created_by_user_type,"
                    "  updated_by_user_id, updated_by_user_type"
                    ") VALUES ("
                    "  :id, :name, :code, :description,"
                    f"  CAST(:audience AS {schema}.role_audience_enum),"
                    f"  CAST(:status AS {schema}.role_status_enum),"
                    "  :is_system,"
                    "  NULL, NULL, NULL, NULL"
                    ")"
                ),
                {
                    "id": new_id,
                    "name": name,
                    "code": code,
                    "description": description,
                    "audience": audience,
                    "status": status,
                    "is_system": is_system,
                },
            )
            created_ids.append(new_id)
        return SimpleNamespace(
            id=new_id, audience=audience, code=code, name=name,
            status=status, is_system=is_system,
        )

    yield _make

    if created_ids:
        async for session in get_tenant_session(
            platform_auth, session_factory
        ):
            await session.execute(
                text(f"DELETE FROM {schema}.roles WHERE id = ANY(:ids)"),
                {"ids": created_ids},
            )


@pytest_asyncio.fixture
async def make_permission(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> AsyncIterator[Callable[..., Awaitable[Any]]]:
    """Async factory: insert + commit a Permission via PLATFORM session.

    Builds the ``code`` from the four enum slots so it always passes
    ``ck_permissions_code_format``. Caller passes the four enum
    values; the factory does the rest.

    Returns a SimpleNamespace with ``.id``, ``.module``, ``.resource``,
    ``.action``, ``.scope``, ``.code``.
    """
    from types import SimpleNamespace

    schema = get_settings().db_schema
    created_ids: list[UUID] = []

    async def _make(
        *,
        module: str,
        resource: str,
        action: str,
        scope: str,
        description: str | None = None,
    ) -> Any:
        new_id = uuid.uuid4()
        code = f"{module}.{resource}.{action}.{scope}"

        async for session in get_tenant_session(
            platform_auth, session_factory
        ):
            await session.execute(
                text(
                    f"INSERT INTO {schema}.permissions ("
                    "  id, module, resource, action, scope,"
                    "  code, description"
                    ") VALUES ("
                    "  :id,"
                    f"  CAST(:module AS {schema}.module_code_enum),"
                    f"  CAST(:resource AS {schema}.resource_enum),"
                    f"  CAST(:action AS {schema}.action_enum),"
                    f"  CAST(:scope AS {schema}.permission_scope_enum),"
                    "  :code, :description"
                    ")"
                ),
                {
                    "id": new_id,
                    "module": module,
                    "resource": resource,
                    "action": action,
                    "scope": scope,
                    "code": code,
                    "description": description,
                },
            )
            created_ids.append(new_id)
        return SimpleNamespace(
            id=new_id, module=module, resource=resource,
            action=action, scope=scope, code=code,
        )

    yield _make

    if created_ids:
        async for session in get_tenant_session(
            platform_auth, session_factory
        ):
            await session.execute(
                text(f"DELETE FROM {schema}.permissions WHERE id = ANY(:ids)"),
                {"ids": created_ids},
            )


@pytest_asyncio.fixture
async def make_role_permission(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> AsyncIterator[Callable[..., Awaitable[None]]]:
    """Async factory: insert + commit a role_permissions junction row.

    No return value (the junction has no surrogate id). Tracks
    ``(role_id, permission_id)`` pairs and DELETEs by composite key
    at teardown.

    Caller passes ``role_id`` and ``permission_id`` from prior
    ``make_role`` / ``make_permission`` calls.
    """
    schema = get_settings().db_schema
    created_keys: list[tuple[UUID, UUID]] = []

    async def _make(
        *,
        role_id: UUID,
        permission_id: UUID,
    ) -> None:
        async for session in get_tenant_session(
            platform_auth, session_factory
        ):
            await session.execute(
                text(
                    f"INSERT INTO {schema}.role_permissions ("
                    "  role_id, permission_id"
                    ") VALUES (:role_id, :permission_id)"
                ),
                {"role_id": role_id, "permission_id": permission_id},
            )
            created_keys.append((role_id, permission_id))

    yield _make

    if created_keys:
        async for session in get_tenant_session(
            platform_auth, session_factory
        ):
            for role_id, permission_id in created_keys:
                await session.execute(
                    text(
                        f"DELETE FROM {schema}.role_permissions "
                        "WHERE role_id = :role_id "
                        "AND permission_id = :permission_id"
                    ),
                    {
                        "role_id": role_id,
                        "permission_id": permission_id,
                    },
                )


# ============================================================================
# Step 6.8.3 fixtures: make_platform_user_role_assignment +
# make_tenant_user_role_assignment.
#
# Both factories use raw SQL INSERTs (mirroring make_tenant_user /
# make_org_node / make_role_permission). The audience-check triggers
# from Step 6.8.1 enforce role-audience consistency at INSERT time:
#   - platform_user_role_assignments: role.audience must be 'PLATFORM'.
#   - tenant_user_role_assignments:   role.audience must be 'TENANT'.
# The factories TRUST the caller to pass an audience-matching role_id;
# the trigger rejection (raised SQL exception) surfaces as an obvious
# test setup failure rather than a silent miscompose.
#
# Composite-FK constraints on tenant_user_role_assignments require
# tenant_id to match BOTH the parent tenant_user's tenant AND the
# parent org_node's tenant. Caller passes consistent values; FK
# violations from mismatches raise at INSERT (test setup failure).
# ============================================================================


@pytest_asyncio.fixture
async def make_platform_user_role_assignment(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> AsyncIterator[Callable[..., Awaitable[Any]]]:
    """Async factory: insert + commit a platform_user_role_assignments row.

    Caller's responsibility to pass a PLATFORM-audience ``role_id``;
    the audience-check trigger rejects mismatches at INSERT.

    Returns a SimpleNamespace with ``.id`` populated. Tracks IDs
    and DELETEs at teardown.

    Pattern (b) audit-actor pairs default to NULL/NULL. ``status``
    defaults to ``'ACTIVE'``; pass ``'INACTIVE'`` for revoked rows
    (then the DDL CHECK requires ``revoked_at`` non-NULL plus the
    revoked_by_user_* pair to be both-NULL or both-NOT-NULL — pass
    those as kwargs).
    """
    from types import SimpleNamespace

    schema = get_settings().db_schema
    created_ids: list[UUID] = []

    async def _make(
        *,
        platform_user_id: UUID,
        role_id: UUID,
        status: str = "ACTIVE",
        revoked_at: str | None = None,
    ) -> Any:
        if status not in ("ACTIVE", "INACTIVE"):
            raise ValueError(
                f"make_platform_user_role_assignment status must be "
                f"'ACTIVE' or 'INACTIVE'; got {status!r}"
            )
        if status == "INACTIVE" and revoked_at is None:
            raise ValueError(
                "INACTIVE assignments require revoked_at (DDL CHECK)"
            )

        # The DDL revoked-consistency CHECK requires revoked_at,
        # revoked_by_user_id, and revoked_by_user_type to be co-set
        # (all NOT NULL when INACTIVE; all NULL when ACTIVE). The
        # factory synthesises a placeholder actor pair for INACTIVE
        # rows so test setup doesn't have to. Tests that want a
        # specific revoking actor can extend the factory; v0 tests
        # don't.
        revoked_by_user_id = uuid.uuid4() if status == "INACTIVE" else None
        revoked_by_user_type = "PLATFORM" if status == "INACTIVE" else None

        new_id = uuid.uuid4()
        async for session in get_tenant_session(
            platform_auth, session_factory
        ):
            await session.execute(
                text(
                    f"INSERT INTO {schema}.platform_user_role_assignments ("
                    "  id, platform_user_id, role_id, status,"
                    "  granted_by_user_id, granted_by_user_type,"
                    "  revoked_at,"
                    "  revoked_by_user_id, revoked_by_user_type"
                    ") VALUES ("
                    "  :id, :pu_id, :role_id,"
                    f"  CAST(:status AS {schema}.user_role_assignment_status_enum),"
                    "  NULL, NULL,"
                    "  CAST(:revoked_at AS TIMESTAMPTZ),"
                    "  :rb_uid,"
                    f"  CAST(:rb_utype AS {schema}.actor_user_type_enum)"
                    ")"
                ),
                {
                    "id": new_id,
                    "pu_id": platform_user_id,
                    "role_id": role_id,
                    "status": status,
                    "revoked_at": revoked_at,
                    "rb_uid": revoked_by_user_id,
                    "rb_utype": revoked_by_user_type,
                },
            )
            created_ids.append(new_id)
        return SimpleNamespace(
            id=new_id,
            platform_user_id=platform_user_id,
            role_id=role_id,
            status=status,
        )

    yield _make

    if created_ids:
        async for session in get_tenant_session(
            platform_auth, session_factory
        ):
            await session.execute(
                text(
                    f"DELETE FROM {schema}.platform_user_role_assignments "
                    "WHERE id = ANY(:ids)"
                ),
                {"ids": created_ids},
            )


@pytest_asyncio.fixture
async def make_tenant_user_role_assignment(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> AsyncIterator[Callable[..., Awaitable[Any]]]:
    """Async factory: insert + commit a tenant_user_role_assignments row.

    Caller's responsibility:
      - Pass a TENANT-audience ``role_id`` (audience-check trigger
        rejects mismatches at INSERT).
      - Ensure ``tenant_id`` matches BOTH ``tenant_user_id``'s parent
        tenant AND ``org_node_id``'s parent tenant. Composite FKs
        ``fk_tenant_user_role_assignments_tenant_user_same_tenant``
        and ``fk_tenant_user_role_assignments_org_node_same_tenant``
        reject mismatches at INSERT (Step 6.8.1 / D-34 / AI-RBAC-06).

    Returns a SimpleNamespace with ``.id`` populated.

    Status defaults to ``'ACTIVE'``; ``'INACTIVE'`` requires a
    ``revoked_at`` value per the DDL revoked-consistency CHECK.
    """
    from types import SimpleNamespace

    schema = get_settings().db_schema
    created_ids: list[UUID] = []

    async def _make(
        *,
        tenant_id: UUID,
        tenant_user_id: UUID,
        org_node_id: UUID,
        role_id: UUID,
        status: str = "ACTIVE",
        revoked_at: str | None = None,
    ) -> Any:
        if status not in ("ACTIVE", "INACTIVE"):
            raise ValueError(
                f"make_tenant_user_role_assignment status must be "
                f"'ACTIVE' or 'INACTIVE'; got {status!r}"
            )
        if status == "INACTIVE" and revoked_at is None:
            raise ValueError(
                "INACTIVE assignments require revoked_at (DDL CHECK)"
            )

        # Synthesise placeholder revoking actor for INACTIVE rows
        # (DDL revoked-consistency CHECK requires the trio to be
        # co-set). Same posture as the platform-side factory.
        revoked_by_user_id = uuid.uuid4() if status == "INACTIVE" else None
        revoked_by_user_type = "PLATFORM" if status == "INACTIVE" else None

        new_id = uuid.uuid4()
        async for session in get_tenant_session(
            platform_auth, session_factory
        ):
            await session.execute(
                text(
                    f"INSERT INTO {schema}.tenant_user_role_assignments ("
                    "  id, tenant_id, tenant_user_id, org_node_id,"
                    "  role_id, status,"
                    "  granted_by_user_id, granted_by_user_type,"
                    "  revoked_at,"
                    "  revoked_by_user_id, revoked_by_user_type"
                    ") VALUES ("
                    "  :id, :t_id, :tu_id, :on_id,"
                    "  :role_id,"
                    f"  CAST(:status AS {schema}.user_role_assignment_status_enum),"
                    "  NULL, NULL,"
                    "  CAST(:revoked_at AS TIMESTAMPTZ),"
                    "  :rb_uid,"
                    f"  CAST(:rb_utype AS {schema}.actor_user_type_enum)"
                    ")"
                ),
                {
                    "id": new_id,
                    "t_id": tenant_id,
                    "tu_id": tenant_user_id,
                    "on_id": org_node_id,
                    "role_id": role_id,
                    "status": status,
                    "revoked_at": revoked_at,
                    "rb_uid": revoked_by_user_id,
                    "rb_utype": revoked_by_user_type,
                },
            )
            created_ids.append(new_id)
        return SimpleNamespace(
            id=new_id,
            tenant_id=tenant_id,
            tenant_user_id=tenant_user_id,
            org_node_id=org_node_id,
            role_id=role_id,
            status=status,
        )

    yield _make

    if created_ids:
        async for session in get_tenant_session(
            platform_auth, session_factory
        ):
            await session.execute(
                text(
                    f"DELETE FROM {schema}.tenant_user_role_assignments "
                    "WHERE id = ANY(:ids)"
                ),
                {"ids": created_ids},
            )


# ============================================================================
# Step 6.9.3.2 fixtures: super_admin_jwt + tenant_owner_jwt_factory
#
# Background. Step 6.9.3.2's gate retrofit changes has_permission's SQL to
# filter by `pura.platform_user_id = :user_id` (or tura.tenant_user_id).
# Existing test JWTs minted with a random uuid.uuid4() never match a
# seeded role assignment → gate denies → 403 → 7 router test files
# break. These two fixtures replace the random-UUID JWT pattern with
# JWTs that map to real seeded grants:
#
#   - `super_admin_jwt` mints a PLATFORM JWT for the seeded Anjali user
#     (SUPER_ADMIN role; 30 grants covering every catalogue tuple).
#     Cascade carries Anjali through every retrofitted gate. Read-only
#     query against `platform_users` by email; no DB writes; no teardown.
#
#   - `tenant_owner_jwt_factory(tenant_id)` builds a SYNTHETIC TENANT-side
#     user + role + role_permissions + tenant_user_role_assignment in the
#     given tenant, mints a JWT for that user. Used by tests that create
#     their own synthetic tenant via make_tenant and need a TENANT JWT
#     whose user has TENANT-scope grants in that tenant. Reuses the
#     established make_* factories so teardown is automatic (each
#     underlying factory tracks its own created IDs and DELETEs at
#     test-function-scope teardown).
#
# Isolation. Both fixtures are function-scoped. super_admin_jwt does no
# DB writes (read-only Anjali UUID lookup; the JWT is a string with no
# DB footprint). tenant_owner_jwt_factory writes synthetic rows via the
# existing factories; teardown is handled by those factories' own
# DELETE tracking. No cross-test pollution.
# ============================================================================


@pytest_asyncio.fixture
async def super_admin_jwt(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> str:
    """Mint a PLATFORM JWT for the seeded Anjali user (SUPER_ADMIN).

    Anjali holds all 30 catalogue permissions; her JWT passes every
    retrofitted gate via direct grant (.VIEW.GLOBAL) or scope cascade.
    Function-scoped so every test gets a fresh JWT (cheap to remint).

    Read-only DB access: SELECT id FROM platform_users WHERE email=...
    via the PLATFORM session. No writes, no teardown.

    Raises ``LookupError`` if Anjali isn't present in the DB — surfaces
    immediately rather than later as a confusing gate-denial test
    failure. Operator should ``--reset`` seed if the fixture errors.
    """
    schema = get_settings().db_schema
    async for session in get_tenant_session(
        platform_auth, session_factory
    ):
        result = await session.execute(
            text(
                f"SELECT id FROM {schema}.platform_users WHERE email = :email"
            ),
            {"email": "anjali@ithina.ai"},
        )
        row = result.first()
    if row is None:
        raise LookupError(
            "Seed user 'anjali@ithina.ai' not found in platform_users. "
            "Re-run: uv run python -m scripts.seed_dev_data --reset"
        )
    anjali_id = uuid.UUID(str(row[0]))
    return make_test_jwt(
        settings,
        user_id=anjali_id,
        user_type="PLATFORM",
    )


@pytest_asyncio.fixture
async def tenant_owner_jwt_factory(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
    # Fixture order below reflects FK-dependency depth: fixtures listed
    # LATER are set up LAST and torn down FIRST (pytest LIFO teardown).
    # tenant_user_role_assignment depends on every other factory's
    # rows, so it must be listed LAST to clear FK constraints before
    # its dependencies' teardowns fire. Reordering breaks teardown.
    make_role: Callable[..., Awaitable[Any]],
    make_platform_user: Callable[..., Awaitable[Any]],
    make_tenant_user: Callable[..., Awaitable[Any]],
    make_org_node: Callable[..., Awaitable[tuple[UUID, str]]],
    make_role_permission: Callable[..., Awaitable[None]],
    make_tenant_module_access: Callable[..., Awaitable[Any]],
    make_tenant_user_role_assignment: Callable[..., Awaitable[Any]],
) -> Callable[..., Awaitable[str]]:
    """Factory: build a synthetic TENANT user with grants in `tenant_id`; mint JWT.

    Default grants cover the three TENANT-scope tuples the retrofitted
    gates require: ADMIN.USERS.VIEW.TENANT, ADMIN.TENANTS.VIEW.TENANT,
    ADMIN.ORG_NODES.VIEW.TENANT (extended 2026-05-13 post Phase 3 seed
    update). Caller can override via the ``with_grants`` parameter —
    pass a list of ``(module, resource, action, scope)`` tuples; the
    factory looks up each permission row by structural tuple identity
    (the uq_permissions_tuple UNIQUE) and creates role_permissions
    junction rows.

    Tests using ``make_tenant`` to build synthetic tenants then call
    this factory with the synthetic tenant's id to mint a JWT whose
    user has TENANT-scope grants WITHIN that tenant. Cascade then
    carries the JWT through retrofitted gates whose tuples exist in
    the catalogue.

    Caller contract — tenant_module_access pre-existence:
        The factory ENSURES a tenant_module_access row exists for each
        module covered by the granted tuples (default: ADMIN only). The
        contract is presence, not status:

         - If no row exists for (tenant_id, module), the factory
           inserts an ENABLED row. Gate passes.
         - If an ENABLED row already exists, the factory skips the
           insert. Gate passes via the existing row.
         - If a DISABLED row exists for a module the factory needs,
           the factory skips the insert and the gate CANNOT pass
           (has_permission JOINs tma.status='ENABLED'). Tests that
           deliberately pre-create a DISABLED row for a module the
           factory needs must restructure: use a different module for
           the DISABLED case, or pass with_grants= overriding to a
           tuple whose module is not the disabled one.

    Isolation. The factory chains through ``make_tenant_user``,
    ``make_role``, ``make_role_permission``,
    ``make_tenant_user_role_assignment``, ``make_org_node``, and
    ``make_tenant_module_access`` (when inserting) — each tracks its
    own created IDs and DELETEs them at fixture teardown. No
    cross-test pollution; no manual teardown ceremony in the factory
    itself.

    Returns a JWT string. Caller wraps in ``_auth(jwt)`` per the
    standard pattern.
    """

    async def _make(
        tenant_id: UUID,
        *,
        with_grants: list[tuple[str, str, str, str]] | None = None,
    ) -> str:
        if with_grants is None:
            # Updated 2026-05-13: Phase 3 seed update applied. Default
            # grants now cover the three TENANT-scope tuples the
            # retrofitted gates require. ADMIN.USERS.VIEW.TENANT was
            # already in the seeded OWNER role; ADMIN.TENANTS.VIEW.TENANT
            # and ADMIN.ORG_NODES.VIEW.TENANT were added to the seeded
            # OWNER role via operator Phase 3 seed update post-Step-6.9.3.2.
            # Targeted extension (not a full mirror of seeded OWNER's
            # ~20+ tuples); other tests with specific grant requirements
            # continue to use explicit with_grants= overrides. See FN-AB-34
            # for the related seed-loader validation forward note.
            with_grants = [
                ("ADMIN", "USERS", "VIEW", "TENANT"),
                ("ADMIN", "TENANTS", "VIEW", "TENANT"),
                ("ADMIN", "ORG_NODES", "VIEW", "TENANT"),
            ]

        # Create tenant_user in this tenant.
        user = await make_tenant_user(tenant_id=tenant_id, status="ACTIVE")

        # Create TENANT-audience role.
        role = await make_role(audience="TENANT")

        # Look up each catalogue permission row by structural identity
        # tuple (module, resource, action, scope) — the uq_permissions_tuple
        # UNIQUE constraint guarantees exactly one row per tuple. Lookup
        # by `code` was retired 2026-05-13: code is denormalised display
        # text and can drift from the tuple via seed-Excel typos (the
        # Phase 3 update shipped one such typo: ADMIN.TENANTS.VIEW.TENANTS
        # vs ADMIN.TENANTS.VIEW.TENANT). Application code (has_permission,
        # require) already uses tuple identity; the factory now matches.
        # See FN-AB-34 for the seed-loader validation forward note.
        schema = get_settings().db_schema
        granted_modules: set[str] = set()
        async for session in get_tenant_session(
            platform_auth, session_factory
        ):
            for module, resource, action, scope in with_grants:
                result = await session.execute(
                    text(
                        f"SELECT id FROM {schema}.permissions "
                        f"WHERE module = CAST(:module AS {schema}.module_code_enum) "
                        f"AND resource = CAST(:resource AS {schema}.resource_enum) "
                        f"AND action = CAST(:action AS {schema}.action_enum) "
                        f"AND scope = CAST(:scope AS {schema}.permission_scope_enum)"
                    ),
                    {
                        "module": module,
                        "resource": resource,
                        "action": action,
                        "scope": scope,
                    },
                )
                row = result.first()
                if row is None:
                    raise LookupError(
                        f"Permission tuple ({module!r}, {resource!r}, "
                        f"{action!r}, {scope!r}) not present in seed "
                        f"catalogue; tenant_owner_jwt_factory can't "
                        f"grant a non-existent tuple. Either pick a "
                        f"different tuple or extend the seed catalogue."
                    )
                perm_id = uuid.UUID(str(row[0]))
                await make_role_permission(
                    role_id=role.id, permission_id=perm_id
                )
                granted_modules.add(module)

        # has_permission's TENANT path JOINs tenant_module_access with
        # `tma.status='ENABLED'`. Without a TMA row for each granted
        # module, the gate denies. The factory ENSURES presence (not
        # status) of a row for each granted module — via a SELECT
        # existence check then make_tenant_module_access only when
        # absent, so the existing fixture-level teardown handles
        # cleanup of factory-created rows.
        #
        # Caller contract:
        #  - No row for (tenant_id, module) → factory inserts ENABLED
        #    row; gate passes.
        #  - ENABLED row already exists → factory skips insert; gate
        #    passes via the existing row.
        #  - DISABLED row already exists → factory skips insert; the
        #    factory's gate CANNOT pass for this tenant on this module
        #    (has_permission filters tma.status='ENABLED'). Tests that
        #    deliberately pre-create a DISABLED row for a module the
        #    factory needs must restructure (use a different module
        #    for the DISABLED case).
        synthetic_pu = await make_platform_user(status="ACTIVE")
        async for session in get_tenant_session(
            platform_auth, session_factory
        ):
            existing_modules: set[str] = set()
            for module in granted_modules:
                result = await session.execute(
                    text(
                        f"SELECT 1 FROM {schema}.tenant_module_access "
                        "WHERE tenant_id = :tenant_id "
                        f"AND module = CAST(:module AS {schema}.module_code_enum)"
                    ),
                    {"tenant_id": tenant_id, "module": module},
                )
                if result.first() is not None:
                    existing_modules.add(module)
        for module in granted_modules - existing_modules:
            await make_tenant_module_access(
                tenant_id=tenant_id,
                module=ModuleCode(module),
                enabled_by_user_id=synthetic_pu.id,
                created_by_user_id=synthetic_pu.id,
                updated_by_user_id=synthetic_pu.id,
            )

        # Anchor the assignment at the tenant-root org_node. If the
        # test already created one (e.g., via _build_bucees or
        # make_org_node(node_type='TENANT', parent_id=None)), reuse
        # it — creating a second TENANT-root would race the
        # get_tenant_anchor LIMIT 1 lookup and the gate's cascade
        # could miss when LIMIT 1 picks the test's root while the
        # assignment is anchored at the factory's. If no tenant-root
        # exists, create one.
        async for session in get_tenant_session(
            platform_auth, session_factory
        ):
            result = await session.execute(
                text(
                    f"SELECT id FROM {schema}.org_nodes "
                    "WHERE tenant_id = :tenant_id "
                    f"AND node_type = CAST('TENANT' AS {schema}.org_node_type_enum) "
                    "AND parent_id IS NULL "
                    "ORDER BY created_at ASC "
                    "LIMIT 1"
                ),
                {"tenant_id": tenant_id},
            )
            existing_root = result.first()
        if existing_root is not None:
            root_id = uuid.UUID(str(existing_root[0]))
        else:
            root_id, _root_path = await make_org_node(
                tenant_id=tenant_id,
                node_type="TENANT",
                code=f"jwt-fix-{user.id.hex[:8]}",
                name=f"JWT Fixture Root {user.id.hex[:8]}",
            )

        # Wire the assignment.
        await make_tenant_user_role_assignment(
            tenant_id=tenant_id,
            tenant_user_id=user.id,
            org_node_id=root_id,
            role_id=role.id,
            status="ACTIVE",
        )

        # Mint JWT for this synthetic user.
        return make_test_jwt(
            settings,
            user_id=user.id,
            user_type="TENANT",
            tenant_id=tenant_id,
        )

    return _make


# ============================================================================
# Step 6.16.3 fixtures: make_tenant_activity_audit_log + make_platform_activity_audit_log
#
# The audit emission tests (test_audit_emission_tenants / _failures) drive
# emission through real HTTP requests, then verify rows landed in the right
# table. The 6.16.3 read-endpoint tests need direct audit-row insertion so
# tests can verify list / detail / filter / cursor behaviour without spinning
# up the full POST /tenants flow per test.
#
# Both factories use raw SQL INSERT mirroring make_tenant_user / make_org_node;
# tracks IDs and DELETEs at teardown. They use the PLATFORM session because
# tenant_activity_audit_logs has RLS+FORCE+D-29 OR-branch; the PLATFORM
# session passes the WITH CHECK via the OR-branch. platform_activity_audit_logs
# has no RLS; PLATFORM session works there too.
#
# Caller contract: the test must pass a real tenant_id (typically from
# make_tenant). The factories do not synthesise tenants; mismatched
# tenant_id values would fail the FK ON DELETE RESTRICT.
# ============================================================================


@pytest_asyncio.fixture
async def make_tenant_activity_audit_log(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> AsyncIterator[Callable[..., Awaitable[Any]]]:
    """Async factory: insert + commit a tenant_activity_audit_logs row.

    Defaults model a typical SUCCESS row from a PATCH-shaped action.
    Tests override per-field as needed via ``**overrides``. The
    ``details`` payload defaults to ``{}`` (DDL default takes over if
    omitted; passed explicitly here for clarity).

    Audit rows are FK-RESTRICT to ``tenants(id)``; teardown DELETEs
    before any test fixture's tenant DELETE fires (pytest LIFO
    teardown: factories listed LATER in the test signature tear down
    EARLIER). Place this factory AFTER ``make_tenant`` in any test
    signature that uses both.

    Returns a ``SimpleNamespace`` with the row's ``id``, ``tenant_id``,
    and ``timestamp`` populated post-INSERT.
    """
    from types import SimpleNamespace

    created_ids: list[UUID] = []
    schema = get_settings().db_schema

    async def _make(
        *,
        tenant_id: UUID,
        tenant_name: str = "Test Tenant",
        actor_user_id: UUID | None = None,
        actor_user_type: str = "PLATFORM",
        actor_display_name: str = "Test Actor",
        # Step 6.16.7 LD13 : new audit-row columns. Defaults satisfy the
        # NOT NULL constraints; tests pass explicit values when they
        # want to drive specific behaviour.
        actor_organization_name: str = "Test Org",
        actor_roles: str = "Test Role",
        resource_type: str = "TENANT",
        resource_id: UUID | None = None,
        resource_label: str | None = "Test Resource",
        # NULLABLE; defaults to None per LD2.
        resource_subtype: str | None = None,
        action: str = "UPDATE",
        action_label: str = "Update",
        result_type: str = "SUCCESS",
        result_label: str = "Success",
        request_id: UUID | None = None,
        details: dict[str, Any] | None = None,
        timestamp: datetime | None = None,
        **overrides: Any,
    ) -> Any:
        from datetime import datetime as _dt
        import json as _json

        new_id = uuid.uuid4()
        if actor_user_id is None:
            actor_user_id = uuid.uuid4()
        if resource_id is None:
            resource_id = tenant_id
        if request_id is None:
            request_id = uuid.uuid4()
        if details is None:
            details = {}
        # CHECK constraint: resource_id and resource_label are both
        # NULL or both NOT NULL. The defaults above keep them paired.

        async for session in get_tenant_session(
            platform_auth, session_factory
        ):
            await session.execute(
                text(
                    f"INSERT INTO {schema}.tenant_activity_audit_logs ("
                    "  id, tenant_id, tenant_name,"
                    "  actor_user_id, actor_user_type, actor_display_name,"
                    "  actor_organization_name, actor_roles,"
                    "  resource_type, resource_id, resource_label,"
                    "  resource_subtype,"
                    "  action, action_label,"
                    "  result_type, result_label,"
                    "  request_id, details"
                    + (", timestamp" if timestamp is not None else "")
                    + ") VALUES ("
                    "  :id, :tenant_id, :tenant_name,"
                    "  :actor_user_id,"
                    f"  CAST(:actor_user_type AS {schema}.actor_user_type_enum),"
                    "  :actor_display_name,"
                    "  :actor_organization_name, :actor_roles,"
                    "  :resource_type, :resource_id, :resource_label,"
                    "  :resource_subtype,"
                    "  :action, :action_label,"
                    f"  CAST(:result_type AS {schema}.audit_result_type_enum),"
                    "  :result_label,"
                    "  :request_id, CAST(:details AS jsonb)"
                    + (", :timestamp" if timestamp is not None else "")
                    + ")"
                ),
                {
                    "id": new_id,
                    "tenant_id": tenant_id,
                    "tenant_name": tenant_name,
                    "actor_user_id": actor_user_id,
                    "actor_user_type": actor_user_type,
                    "actor_display_name": actor_display_name,
                    "actor_organization_name": actor_organization_name,
                    "actor_roles": actor_roles,
                    "resource_type": resource_type,
                    "resource_id": resource_id,
                    "resource_label": resource_label,
                    "resource_subtype": resource_subtype,
                    "action": action,
                    "action_label": action_label,
                    "result_type": result_type,
                    "result_label": result_label,
                    "request_id": request_id,
                    "details": _json.dumps(details),
                    **(
                        {"timestamp": timestamp}
                        if timestamp is not None
                        else {}
                    ),
                },
            )
            created_ids.append(new_id)
        return SimpleNamespace(
            id=new_id,
            tenant_id=tenant_id,
            timestamp=timestamp,
            scope="TENANT",
        )

    yield _make

    if created_ids:
        async for session in get_tenant_session(
            platform_auth, session_factory
        ):
            await session.execute(
                text(
                    f"DELETE FROM {schema}.tenant_activity_audit_logs "
                    "WHERE id = ANY(:ids)"
                ),
                {"ids": created_ids},
            )


@pytest_asyncio.fixture
async def make_platform_activity_audit_log(
    session_factory: async_sessionmaker[AsyncSession],
    platform_auth: AuthContext,
) -> AsyncIterator[Callable[..., Awaitable[Any]]]:
    """Async factory: insert + commit a platform_activity_audit_logs row.

    Defaults model a typical SUCCESS row from a platform-scope action
    (e.g., POST /tenants creation). ``tenant_id`` and ``tenant_name``
    are NULLABLE on this table; populate them only for tenant-creation
    rows per the design doc routing principle.

    The platform_activity_audit_logs table has FK to tenants(id)
    ON DELETE RESTRICT per the design doc. If a test passes a
    tenant_id that doesn't exist, INSERT fails; if it passes one that
    does exist, the test's make_tenant teardown must run AFTER this
    factory's teardown (LIFO ordering).
    """
    from types import SimpleNamespace

    created_ids: list[UUID] = []
    schema = get_settings().db_schema

    async def _make(
        *,
        tenant_id: UUID | None = None,
        tenant_name: str | None = None,
        actor_user_id: UUID | None = None,
        actor_user_type: str = "PLATFORM",
        actor_display_name: str = "Test Platform Actor",
        # Step 6.16.7 LD13 : new audit-row columns. Defaults satisfy
        # NOT NULL constraints on the platform table.
        actor_organization_name: str = "Platform-Ithina",
        actor_roles: str = "Test Role",
        resource_type: str = "TENANT",
        resource_id: UUID | None = None,
        resource_label: str | None = None,
        # NULLABLE; defaults to None per LD2.
        resource_subtype: str | None = None,
        action: str = "CREATE",
        action_label: str = "Create",
        result_type: str = "SUCCESS",
        result_label: str = "Success",
        request_id: UUID | None = None,
        details: dict[str, Any] | None = None,
        timestamp: datetime | None = None,
        **overrides: Any,
    ) -> Any:
        import json as _json

        new_id = uuid.uuid4()
        if actor_user_id is None:
            actor_user_id = uuid.uuid4()
        if request_id is None:
            request_id = uuid.uuid4()
        if details is None:
            details = {}
        # Pair the CHECK constraints: resource_id <-> resource_label;
        # tenant_id <-> tenant_name. Tests override one half must pass
        # the other (or both NULL).

        async for session in get_tenant_session(
            platform_auth, session_factory
        ):
            await session.execute(
                text(
                    f"INSERT INTO {schema}.platform_activity_audit_logs ("
                    "  id, tenant_id, tenant_name,"
                    "  actor_user_id, actor_user_type, actor_display_name,"
                    "  actor_organization_name, actor_roles,"
                    "  resource_type, resource_id, resource_label,"
                    "  resource_subtype,"
                    "  action, action_label,"
                    "  result_type, result_label,"
                    "  request_id, details"
                    + (", timestamp" if timestamp is not None else "")
                    + ") VALUES ("
                    "  :id, :tenant_id, :tenant_name,"
                    "  :actor_user_id,"
                    f"  CAST(:actor_user_type AS {schema}.actor_user_type_enum),"
                    "  :actor_display_name,"
                    "  :actor_organization_name, :actor_roles,"
                    "  :resource_type, :resource_id, :resource_label,"
                    "  :resource_subtype,"
                    "  :action, :action_label,"
                    f"  CAST(:result_type AS {schema}.audit_result_type_enum),"
                    "  :result_label,"
                    "  :request_id, CAST(:details AS jsonb)"
                    + (", :timestamp" if timestamp is not None else "")
                    + ")"
                ),
                {
                    "id": new_id,
                    "tenant_id": tenant_id,
                    "tenant_name": tenant_name,
                    "actor_user_id": actor_user_id,
                    "actor_user_type": actor_user_type,
                    "actor_display_name": actor_display_name,
                    "actor_organization_name": actor_organization_name,
                    "actor_roles": actor_roles,
                    "resource_type": resource_type,
                    "resource_id": resource_id,
                    "resource_label": resource_label,
                    "resource_subtype": resource_subtype,
                    "action": action,
                    "action_label": action_label,
                    "result_type": result_type,
                    "result_label": result_label,
                    "request_id": request_id,
                    "details": _json.dumps(details),
                    **(
                        {"timestamp": timestamp}
                        if timestamp is not None
                        else {}
                    ),
                },
            )
            created_ids.append(new_id)
        return SimpleNamespace(
            id=new_id,
            tenant_id=tenant_id,
            timestamp=timestamp,
            scope="PLATFORM",
        )

    yield _make

    if created_ids:
        async for session in get_tenant_session(
            platform_auth, session_factory
        ):
            await session.execute(
                text(
                    f"DELETE FROM {schema}.platform_activity_audit_logs "
                    "WHERE id = ANY(:ids)"
                ),
                {"ids": created_ids},
            )
