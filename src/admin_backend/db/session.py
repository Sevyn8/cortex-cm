"""FastAPI dependency: yield a session with tenant context set per AuthContext.

The dependency is the ONLY code path that sets `app.tenant_id` and
`app.user_type` per request. Both come from AuthContext, never from
request body, headers, query params, or any other source. This is the
load-bearing isolation gate per D-03.

Source binding is structural: `auth.tenant_id` is `UUID | None` on a
frozen Pydantic model (AuthContext per D-24). mypy strict catches any
attempt to flow a raw string here. AI-MT-03's intent (raw strings
cannot reach the SET path) is met without a separate newtype wrapper.

`set_config(name, value, is_local=true)` is used for both vars rather
than SET LOCAL: SET LOCAL has no clean way to represent NULL, which
the PLATFORM-not-impersonating case needs (`app.tenant_id` NULL +
`app.user_type` PLATFORM is what FN-AB-14's policy reads).

`set_config(..., is_local=true)` only persists within an active
transaction; the `async with session.begin()` context manager
provides one. The vars are transaction-scoped: when the transaction
ends they reset, so there is no leakage between requests.

Wiring into FastAPI happens at Step 2.3 (middleware populates
request.state.auth; a Depends() provider returns the current
AuthContext and the session_factory). This module's signature takes
the dependencies as direct args so the bootstrap is unit-testable
without FastAPI machinery.
"""
from typing import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from admin_backend.auth.context import AuthContext


async def get_tenant_session(
    auth: AuthContext,
    session_factory: async_sessionmaker[AsyncSession],
    request_id: str | None = None,
) -> AsyncIterator[AsyncSession]:
    """Yield an AsyncSession with `app.tenant_id`, `app.user_type`, and
    `app.request_id` set.

    For each invocation:
        1. Open a session.
        2. Begin a transaction.
        3. set_config app.tenant_id (NULL if auth.tenant_id is None).
        4. set_config app.user_type (always non-NULL).
        5. set_config app.request_id (NULL outside a request context,
           e.g. in unit tests; non-NULL when wired through the FastAPI
           dependency at Step 2.3 for audit-trigger correlation at
           Step 6.2).
        6. Yield the session to the caller.
        7. On clean exit: commit. On exception: rollback.

    AuthContext is consumed read-only; this dependency never mutates
    it.

    The `request_id` kwarg has a default of None so existing callers
    (Step 2.2a tests, smoke test) continue to work without change.
    """
    async with session_factory() as session:
        async with session.begin():
            tenant_id_value = (
                str(auth.tenant_id) if auth.tenant_id is not None else None
            )
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tid, true)"),
                {"tid": tenant_id_value},
            )
            await session.execute(
                text("SELECT set_config('app.user_type', :ut, true)"),
                {"ut": auth.user_type},
            )
            await session.execute(
                text("SELECT set_config('app.request_id', :rid, true)"),
                {"rid": request_id},
            )

            yield session
            # Transaction commits here unless an exception escapes.
