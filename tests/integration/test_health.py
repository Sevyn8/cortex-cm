"""Step 2.4 integration tests for /api/v1/health and /api/v1/ready.

6 tests:
    H1: GET /api/v1/health returns 200 with status/service/version body.
    H2: GET /api/v1/health does not touch the DB. Verified by code
        inspection (the handler has no DB dependency); no live test
        because mocking the engine to be unreachable inside an async
        TestClient flow is more brittle than the property is worth.
    H3: GET /api/v1/ready with healthy DB -> 200 / db=ok.
    H4: GET /api/v1/ready with broken engine -> 503 / db=error. Mocks
        engine.connect() to raise.
    H5: GET /api/v1/ready with hung DB -> 503 within ~3s (the 2s timeout
        budget plus slack). Mocks engine.connect() to await indefinitely.
    H6: Both endpoints emit one INFO log line via the audit middleware
        with the right path and status.
"""
import json
import time
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# H1: /api/v1/health
# ---------------------------------------------------------------------------


def test_h1_health_returns_expected_shape(
    client: TestClient, settings
) -> None:
    """/api/v1/health returns the configured service_version.

    Default is the installed wheel's metadata version
    (`pyproject.toml`). Deployed images override via the
    ``SERVICE_VERSION`` env var so the response matches the image tag.
    The test asserts against settings rather than a hardcoded literal
    so a SERVICE_VERSION env override doesn't break the test.
    """
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "status": "ok",
        "service": "admin-backend",
        "version": settings.service_version,
    }
    assert body["version"], "service_version must be non-empty"


# ---------------------------------------------------------------------------
# H2: /api/v1/health does not touch the DB
# ---------------------------------------------------------------------------


def test_h2_health_does_not_touch_db_by_construction() -> None:
    """Property: /api/v1/health does no DB I/O.

    Verified by code inspection rather than a live test: the handler
    in src/admin_backend/main.py only constructs and returns a dict
    literal. There is no `request`, `engine`, `session_factory`, or
    `text(...)` reference inside the function body. The audit
    middleware does emit one log line via `admin_backend.requests`,
    but logging is not a DB dependency.

    Mocking the engine to fault inside an async TestClient flow would
    be more brittle than this property warrants; a code-inspection
    test is sufficient and stable.
    """
    import inspect

    from admin_backend import main

    source = inspect.getsource(main)
    health_idx = source.index("async def health(")
    next_def_idx = source.index("async def ready(", health_idx)
    health_body = source[health_idx:next_def_idx]

    forbidden = ("engine", "session_factory", "text(", "execute(")
    found = [needle for needle in forbidden if needle in health_body]
    assert not found, f"/api/v1/health body references DB-shaped names: {found}"


# ---------------------------------------------------------------------------
# H3: /api/v1/ready healthy
# ---------------------------------------------------------------------------


def test_h3_ready_healthy_db_returns_200(client: TestClient) -> None:
    resp = client.get("/api/v1/ready")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ready", "db": "ok"}


# ---------------------------------------------------------------------------
# H4: /api/v1/ready broken engine
# ---------------------------------------------------------------------------


def _broken_engine() -> MagicMock:
    """Mock engine whose `connect()` context manager raises."""

    class _BrokenCM:
        async def __aenter__(self) -> None:
            raise RuntimeError("simulated db connect failure")

        async def __aexit__(self, *args: object) -> None:
            return None

    eng = MagicMock()
    eng.connect = MagicMock(return_value=_BrokenCM())
    return eng


def test_h4_ready_broken_engine_returns_503(
    app_with_test_routes, client: TestClient
) -> None:
    """When engine.connect() raises, /api/v1/ready returns 503."""
    real_engine = app_with_test_routes.state.engine
    app_with_test_routes.state.engine = _broken_engine()
    try:
        resp = client.get("/api/v1/ready")
    finally:
        app_with_test_routes.state.engine = real_engine
    assert resp.status_code == 503
    assert resp.json() == {"status": "not_ready", "db": "error"}


# ---------------------------------------------------------------------------
# H5: /api/v1/ready hung DB
# ---------------------------------------------------------------------------


def _hanging_engine() -> MagicMock:
    """Mock engine whose `connect()` __aenter__ awaits indefinitely."""
    import asyncio

    class _HangingCM:
        async def __aenter__(self) -> None:
            await asyncio.sleep(60)  # longer than READINESS_TIMEOUT

        async def __aexit__(self, *args: object) -> None:
            return None

    eng = MagicMock()
    eng.connect = MagicMock(return_value=_HangingCM())
    return eng


def test_h5_ready_hung_db_times_out_within_3s(
    app_with_test_routes, client: TestClient
) -> None:
    """Hung engine returns 503 within the 2s timeout + slack."""
    real_engine = app_with_test_routes.state.engine
    app_with_test_routes.state.engine = _hanging_engine()
    try:
        start = time.perf_counter()
        resp = client.get("/api/v1/ready")
        elapsed = time.perf_counter() - start
    finally:
        app_with_test_routes.state.engine = real_engine
    assert resp.status_code == 503
    assert resp.json() == {"status": "not_ready", "db": "error"}
    assert elapsed < 3.0, f"readiness took {elapsed:.2f}s; expected <3s"


# ---------------------------------------------------------------------------
# H6: audit middleware emits log lines for both endpoints
# ---------------------------------------------------------------------------


def test_h6_audit_log_emits_for_both_endpoints(
    client: TestClient, json_log_buffer
) -> None:
    client.get("/api/v1/health")
    client.get("/api/v1/ready")

    raw = json_log_buffer.getvalue().strip()
    lines = [json.loads(line) for line in raw.split("\n") if line]
    paths = [(line["path"], line["status"]) for line in lines]
    assert ("/api/v1/health", 200) in paths
    assert ("/api/v1/ready", 200) in paths
