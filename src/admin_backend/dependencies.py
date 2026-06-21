"""FastAPI dependency providers.

Bridges the request-scoped state populated by middleware (AuthContext,
request_id) and the app-scoped state populated by lifespan
(session_factory) into FastAPI's `Depends()` machinery. The bridge is
why this module exists: Step 2.2a's `get_tenant_session(auth,
session_factory, request_id)` is a plain async generator that doesn't
know about request.state or app.state; this module wraps it.

Per D-17, RLS-blocked / missing-row reads from handlers must surface as
404, not 403. That's handler-layer logic; this dependency does not
enforce it. Handlers landing at Step 3.x onward implement the contract.
"""
from typing import AsyncIterator

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.requests import Request

from admin_backend.auth.context import AuthContext
from admin_backend.db.session import get_tenant_session
from admin_backend.errors import AuthMissingError


def get_auth_context(request: Request) -> AuthContext:
    """Pull AuthContext from request.state (populated by AuthMiddleware).

    Raises AuthMissingError if auth wasn't set (caller hit a public
    path or the dependency was used outside the middleware chain).
    """
    auth = getattr(request.state, "auth", None)
    if auth is None:
        raise AuthMissingError(
            "AuthContext missing from request.state; "
            "auth middleware did not run"
        )
    return auth  # type: ignore[no-any-return]


def get_session_factory(
    request: Request,
) -> async_sessionmaker[AsyncSession]:
    """Pull the session factory from app.state (set in main.py lifespan)."""
    factory: async_sessionmaker[AsyncSession] = (
        request.app.state.session_factory
    )
    return factory


def get_request_id(request: Request) -> str | None:
    """Pull request_id from request.state (set by AuditContextMiddleware)."""
    rid: str | None = getattr(request.state, "request_id", None)
    return rid


async def get_tenant_session_dep(
    auth: AuthContext = Depends(get_auth_context),
    session_factory: async_sessionmaker[AsyncSession] = Depends(
        get_session_factory
    ),
    request_id: str | None = Depends(get_request_id),
) -> AsyncIterator[AsyncSession]:
    """FastAPI-shaped wrapper around get_tenant_session.

    Bridges the dependency-injection layer to Step 2.2a's
    get_tenant_session. Passes request_id through so the dependency
    sets app.request_id for audit triggers (Step 6.2).
    """
    async for session in get_tenant_session(
        auth, session_factory, request_id=request_id
    ):
        yield session
