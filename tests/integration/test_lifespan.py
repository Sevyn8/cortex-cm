"""Step 2.4 lifespan / startup-gate tests.

5 tests:
    L1: Lifespan with valid Settings + healthy DB role completes;
        engine, session_factory, auth_client all on app.state.
    L2: ENVIRONMENT=production + AUTH_CLIENT_MODE=STUB raises
        ValidationError at Settings() construction (before lifespan
        even reaches DB code).
    L3: ENVIRONMENT=production + AUTH_CLIENT_MODE=AUTH0 + a
        production-shaped issuer (HTTPS, ends with /, no stub
        markers) is accepted by the production-issuer validator.
        Confirms the gate doesn't false-positive.
    L4: assert_app_role_no_bypassrls raising AppRolePrivilegeError
        propagates out of lifespan. Engine is still created (it
        precedes the check); auth_client is not.
    L5: AUTH_CLIENT_MODE=AUTH0 reaches the lifespan and raises
        NotImplementedError carrying the new pending-Auth0 message.

L2-L5 must NOT use get_settings() (Step 2.3 wrapped Settings in an
@lru_cache; cached values would survive across tests in the same
process and miss env-var changes). Each test constructs Settings()
directly OR clears the cache before exercising the lifespan.
"""
import os
from typing import Any
from unittest.mock import patch
from uuid import UUID

import pytest
import pytest_asyncio
from fastapi import FastAPI
from pydantic import ValidationError

from admin_backend.config import Settings, get_settings
from admin_backend.errors import AppRolePrivilegeError
from admin_backend.main import create_app, lifespan


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clear_settings_cache() -> None:
    """The @lru_cache on get_settings persists across tests in the
    same process. Clear it before any test that mutates env vars."""
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _settings_cache_isolation() -> Any:
    """Clear the get_settings cache around each lifespan test so env
    var manipulation under monkeypatch has the intended effect."""
    _clear_settings_cache()
    yield
    _clear_settings_cache()


# ---------------------------------------------------------------------------
# L1: lifespan happy path
# ---------------------------------------------------------------------------


async def test_l1_lifespan_happy_path_wires_app_state() -> None:
    """Valid Settings + healthy local DB role: lifespan completes;
    app.state has the engine, session factory, and auth client."""
    app = create_app()
    async with lifespan(app):
        assert app.state.engine is not None
        assert app.state.session_factory is not None
        assert app.state.auth_client is not None
        assert app.state.settings is not None


# ---------------------------------------------------------------------------
# L2: production + STUB rejected at Settings()
# ---------------------------------------------------------------------------


def test_l2_production_with_stub_mode_rejected_at_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ENVIRONMENT=production + AUTH_CLIENT_MODE=STUB raises
    ValidationError at Settings() construction. The lifespan never
    runs because Settings() is the first thing it calls."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("AUTH_CLIENT_MODE", "STUB")
    monkeypatch.setenv("JWT_ISSUER", "https://prod.auth0.com/")
    with pytest.raises(ValidationError) as exc_info:
        Settings()  # type: ignore[call-arg]
    assert "AUTH_CLIENT_MODE" in str(exc_info.value)


# ---------------------------------------------------------------------------
# L3: production-shaped issuer not flagged
# ---------------------------------------------------------------------------


def test_l3_production_with_real_issuer_passes_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ENVIRONMENT=production + AUTH_CLIENT_MODE=AUTH0 + a real
    production-shaped issuer (HTTPS, ends with /, no stub markers)
    constructs Settings successfully. Confirms the
    production_issuer_must_not_be_stub validator doesn't false-fire
    on legitimate issuers.
    """
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("AUTH_CLIENT_MODE", "AUTH0")
    monkeypatch.setenv("JWT_ISSUER", "https://ithina.us.auth0.com/")
    s = Settings()  # type: ignore[call-arg]
    assert s.environment == "production"
    assert s.auth_client_mode == "AUTH0"
    assert s.jwt_issuer == "https://ithina.us.auth0.com/"


# ---------------------------------------------------------------------------
# L4: privilege gate raises -> lifespan exits
# ---------------------------------------------------------------------------


async def test_l4_privilege_gate_raise_propagates() -> None:
    """assert_app_role_no_bypassrls raises -> lifespan re-raises;
    engine has been created (precedes the check) but auth_client has
    not."""

    async def _raise(_engine: Any) -> None:
        raise AppRolePrivilegeError(
            "simulated SUPERUSER on current role"
        )

    app = create_app()
    with patch(
        "admin_backend.main.assert_app_role_no_bypassrls", _raise
    ):
        with pytest.raises(AppRolePrivilegeError):
            async with lifespan(app):
                pytest.fail(
                    "lifespan body should not run when privilege "
                    "gate raises"
                )

    # Engine is created BEFORE the gate; the lifespan stored it on
    # app.state then raised. session_factory and auth_client are
    # constructed AFTER the gate, so they should NOT be set.
    assert getattr(app.state, "engine", None) is not None
    assert not hasattr(app.state, "auth_client")
    # Clean up the engine the lifespan opened before raising.
    await app.state.engine.dispose()


# ---------------------------------------------------------------------------
# L5: AUTH0 mode reaches lifespan and raises pending-Auth0 message
# ---------------------------------------------------------------------------


async def test_l5_auth0_mode_raises_with_pending_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AUTH_CLIENT_MODE=AUTH0 reaches the lifespan and raises
    NotImplementedError mentioning the pending Auth0 work."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("AUTH_CLIENT_MODE", "AUTH0")
    monkeypatch.setenv("JWT_ISSUER", "https://ithina.us.auth0.com/")

    app = create_app()
    with pytest.raises(NotImplementedError) as exc_info:
        async with lifespan(app):
            pytest.fail("lifespan body should not run for AUTH0 mode")
    msg = str(exc_info.value)
    assert "Auth0Client" in msg
    assert "pending" in msg.lower()

    # Engine was created before the auth-client branch, so dispose it.
    if hasattr(app.state, "engine"):
        await app.state.engine.dispose()
