# Prompt — Step 2.4: Health + readiness endpoints, lifespan finalisation, startup gates

> Generated 2026-05-02 (initial). Paste into a fresh Claude Code session for Step 2.4.
> Final step in the foundation block. After this lands, Steps 3.x (ORM models + handlers) start. Auth0Client is its own future step (lands when Auth0 tenant configuration details arrive, ~3-4 days); v0 production cutover blocks on it.

---

## Pre-flight

1. `./scripts/check_setup.sh` — expect 35/35.
2. `git log --oneline -10` — confirm Step 2.3 at HEAD.
3. Read `CLAUDE.md` fully:
   - "Current state" Completed list (should reflect Steps 1.3 through 2.3).
   - Error-model section (post-2.3 two-tier ClientError/ServerError shape).
   - D-03 (session vars; app.request_id added at Step 2.3).
4. Read `docs/architecture.md` "Request lifecycle" and any deployment-shape sections.
5. Read `BUILD_PLAN.md` Step 2.4 in full. Pre-flight grep for drift:
   ```bash
   grep -A12 "## Step 2.4" BUILD_PLAN.md
   ```
   Compare what's written there vs. what this prompt says. Surface any mismatch before proceeding.
6. Read this prompt fully.

---

## Step ID and intent

**Step 2.4** — Finalise the application entrypoint and ship the health endpoints. Three concrete deliverables:

1. **`/v1/health`** (liveness probe) — process is up; returns 200 unconditionally. No DB access.
2. **`/v1/ready`** (readiness probe) — service is ready to serve traffic; runs `SELECT 1`. 200 if DB reachable, 503 otherwise. K8s readiness probe at Step 4.4 will hit this.
3. **`main.py` polish** — verify the Step 2.3 lifespan works end-to-end, tighten the Auth0-pending message, add startup-gate tests that exercise both `Settings()` validation and `assert_app_role_no_bypassrls`.

Step 2.3 already shipped main.py with lifespan + middleware + exception handler in working form. This step finalises it: real health endpoints (replacing the test-only stubs in Step 2.3's fixtures), startup-gate tests, and a few small polish items.

CLAUDE_CODE step. Closes the foundation block.

---

## Required behaviour

### `/v1/health` — liveness

Path: `/v1/health`. Method: GET. Auth: public (already in `PUBLIC_PATHS` from Step 2.3).

Response shape (200):
```json
{
  "status": "ok",
  "service": "admin-backend",
  "version": "0.1.0"
}
```

No DB access, no auth check, no middleware-fail-fast logic. Must respond fast even when DB is down or the app is misconfigured. The kubelet uses this to decide "kill and restart this pod"; if it's slow or returns 503 due to DB issues, k8s will kill healthy pods.

Hardcode `version` from `pyproject.toml` if convenient (or import `__version__` from a package-level constant). Pulling from settings is fine if `Settings` exposes it; otherwise constant string is fine for v0.

`include_in_schema=True` so OpenAPI documents it. Tag: `meta`.

### `/v1/ready` — readiness

Path: `/v1/ready`. Method: GET. Auth: public.

**Add `/v1/ready` to `PUBLIC_PATHS` in `src/admin_backend/middleware/auth.py`.** Currently the set is `{"/v1/health", "/v1/openapi.json", "/v1/docs", "/v1/redoc", "/metrics"}` — add `/v1/ready`.

Behaviour:
- Use `request.app.state.engine` (already set at Step 2.3 lifespan).
- Open a short-lived connection: `async with engine.connect() as conn: await conn.execute(text("SELECT 1"))`.
- On success: 200 with `{"status": "ready", "db": "ok"}`.
- On any exception: catch broad `Exception`, log internally, return 503 with `{"status": "not_ready", "db": "error"}`.
- The 503 is the signal to the load balancer; the actual error type is logged via the audit middleware's exception path (Step 2.3 already handles this).

Don't go through `get_tenant_session_dep`; readiness shouldn't need an AuthContext. Don't open a transaction; `engine.connect()` for a single SELECT is sufficient.

`include_in_schema=True`. Tag: `meta`.

### Startup gates (verify, don't add new ones)

The lifespan from Step 2.3 already exercises:
- `Settings()` construction (raises `ValidationError` if `ENVIRONMENT=production` + `AUTH_CLIENT_MODE=STUB`, or production with stub-marker issuer).
- `assert_app_role_no_bypassrls(engine)` (raises `AppRolePrivilegeError` if SUPERUSER or BYPASSRLS).

This step adds tests that exercise both paths and verify the lifespan refuses to start.

### main.py polish

1. **Improve the Auth0 NotImplementedError message.** Currently the lifespan has:
   ```python
   if settings.auth_client_mode == "STUB":
       auth_client = StubAuthClient(settings)
   else:
       raise NotImplementedError("Only STUB auth client is wired in v0")
   ```
   Replace the message with:
   ```python
   raise NotImplementedError(
       "Auth0Client implementation pending Auth0 tenant configuration "
       "(expected within a few days). Until it lands, AUTH_CLIENT_MODE must be "
       "'STUB'. Production cutover blocks on Auth0Client per D-07."
   )
   ```

2. **Verify imports.** Step 2.3's main.py may have imports that aren't actually used. Run mypy strict and if any "unused import" warnings surface, clean them.

3. **Confirm middleware order, exception handler, and lifespan structure unchanged.** Don't refactor.

4. **No router registration yet.** Health endpoints register directly on the FastAPI app inside `create_app`. Routers come at Step 3.x.

### Operational gotchas

1. `set -a && source .env && set +a` per bash call.
2. `pyproject.toml`'s `asyncio_mode = "auto"` makes Step 2.3's `pytest_asyncio.fixture` pattern work without per-fixture decorators. Step 2.4 tests follow the same pattern.
3. Health/readiness endpoints must NOT log at INFO level beyond what the audit middleware already emits. The audit middleware will log every request including these; don't double-log inside the handler.
4. Readiness check should have a short timeout. SQLAlchemy `engine.connect()` honours `pool_timeout` (set to 30s at Step 2.2a). For readiness, that's too long — k8s will mark the pod unready faster than that. Worth wrapping the readiness check in `asyncio.wait_for(..., timeout=2.0)` so a hung DB doesn't tie up the readiness probe.

---

## Scope in

### File 1: `src/admin_backend/main.py` — additions

Add the two endpoints inside `create_app()`, before the exception handler registration:

```python
import asyncio
from sqlalchemy import text


@app.get(
    "/v1/health",
    tags=["meta"],
    summary="Liveness probe",
)
async def health() -> dict:
    """Liveness probe. Returns 200 unconditionally. No DB access.

    Used by Kubernetes to determine whether the pod is alive. A failure
    here means the pod gets killed and restarted.
    """
    return {
        "status": "ok",
        "service": "admin-backend",
        "version": "0.1.0",
    }


@app.get(
    "/v1/ready",
    tags=["meta"],
    summary="Readiness probe",
)
async def ready(request: Request) -> JSONResponse:
    """Readiness probe. Returns 200 if DB is reachable, 503 otherwise.

    Used by Kubernetes to determine whether the pod is ready to serve
    traffic. A 503 here means the load balancer skips this pod.

    Bounded by a 2-second timeout so a hung DB does not stall the probe.
    """
    engine = request.app.state.engine
    try:
        async def _ping() -> None:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        await asyncio.wait_for(_ping(), timeout=2.0)
        return JSONResponse(
            status_code=200,
            content={"status": "ready", "db": "ok"},
        )
    except Exception:
        # Any failure (DB down, timeout, etc.) is unready. Specifics are
        # logged by audit middleware via the exception path; the response
        # body is intentionally generic.
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "db": "error"},
        )
```

Update the lifespan's NotImplementedError message per "main.py polish" point 1 above.

### File 2: `src/admin_backend/middleware/auth.py` — update

Add `/v1/ready` to `PUBLIC_PATHS`:

```python
PUBLIC_PATHS = frozenset({
    "/v1/health",
    "/v1/ready",
    "/v1/openapi.json",
    "/v1/docs",
    "/v1/redoc",
    "/metrics",
})
```

### File 3: `tests/integration/test_health.py` — new

Tests for the two endpoints. Use the `app_with_test_routes` and `client` fixtures from Step 2.3's `test_middleware.py`. Add new fixtures only if needed. Suggested layout: keep the conftest at `tests/integration/conftest.py` with shared fixtures, OR replicate inline if the fixtures live in `test_middleware.py`. **Decision: extract shared fixtures to `tests/integration/conftest.py` if they aren't already there**, and have both test files import from that.

H1. GET `/v1/health` (no auth) → 200, body matches expected shape (status, service, version).

H2. GET `/v1/health` returns immediately even when DB is down. Mock or stop the engine; verify endpoint still returns 200. Acceptable simplification: skip the explicit DB-down test; the *absence* of a DB query in the handler is the property, verifiable by code inspection. If skipped, document why in a comment in the test file.

H3. GET `/v1/ready` with healthy DB → 200, body `{"status": "ready", "db": "ok"}`.

H4. GET `/v1/ready` with broken engine → 503. Mock `engine.connect()` to raise. Body `{"status": "not_ready", "db": "error"}`.

H5. GET `/v1/ready` with hung DB → 503 within ~2s. Mock `engine.connect()` to await for 5+ seconds; verify the endpoint returns 503 (not 200) within 3 seconds. (Loose timing bound to avoid flake.)

H6. Both endpoints emit one INFO log line via audit middleware. Verify via `json_log_buffer` fixture that path matches and status is correct.

### File 4: `tests/integration/test_lifespan.py` — new

Startup-gate tests. These exercise the lifespan directly without `TestClient` — use FastAPI's lifespan-enter / lifespan-exit pattern manually.

L1. Lifespan startup with valid Settings + healthy DB role → completes without raising. Verify `app.state.auth_client`, `app.state.engine`, `app.state.session_factory` are all set.

L2. Lifespan startup with `ENVIRONMENT=production` + `AUTH_CLIENT_MODE=STUB` → `ValidationError` raised at `Settings()` construction (before engine/lifespan even runs). This validates the production-mode gate from Step 2.1.

L3. Lifespan startup with `ENVIRONMENT=production` + production-marker issuer → succeeds. (Confirms the production gate doesn't false-positive.)

L4. Lifespan startup where `assert_app_role_no_bypassrls` raises (mock `pg_roles` to return SUPERUSER) → `AppRolePrivilegeError`, lifespan does not complete. Verify `app.state.engine` is set (because engine is created before the check) but the lifespan exits via the raise.

L5. Lifespan startup with `AUTH_CLIENT_MODE=AUTH0` → `NotImplementedError` with the new pending-Auth0 message.

For L2, L4, L5: these don't need a real DB connection (the failure happens before the DB is queried, except L4 which mocks the query). Use environment variable manipulation via `monkeypatch` or test-scoped Settings construction.

**Important: bypass `get_settings()` in lifespan tests.** Step 2.3 wrapped `Settings()` in an `@lru_cache`-d `get_settings()` accessor for production efficiency. That cache survives across tests in the same process; if L2 sets `ENVIRONMENT=production` then `get_settings()` would return the *previously cached* Settings (from a prior test) without picking up the new env var. Lifespan tests must construct `Settings()` directly (not via `get_settings()`), OR call `get_settings.cache_clear()` before each test. The latter is cleaner; add to the test fixture.

For L1, L3: these need a real engine. Use the same Settings/engine pattern as the existing fixtures.

Total: 6 health tests + 5 lifespan tests = 11 new tests.

### File 5: `BUILD_PLAN.md` — status flip + scope-in correction

- Step 2.4 status TODO → DONE.
- Scope-in: rewrite to match shipped reality (two endpoints, lifespan polish, 11 tests, no router work).

### File 6: `CLAUDE.md` — Current state update

- Completed list: add Step 2.4 bullet covering the two health endpoints, the readiness 2-second timeout, the lifespan polish, the 11 new tests.
- Not yet completed list: remove Step 2.4; tighten any 2.4-related text in Step 2.5 / 3.x descriptions.
- No new decisions (D-XX) expected for this step.
- No new FN-AB items expected.

### File 7: `prompts/step-2_4-health-lifespan-2026-05-02.md` — this prompt

Committed alongside per the per-step bundling convention.

---

## Scope out

- Auth0Client (separate future step, lands when Auth0 tenant config arrives).
- ORM models, schemas, repositories (Step 3.x).
- Domain endpoints (Step 3.x onward).
- `/metrics` endpoint (Step 7.2.1, post-launch monitoring).
- Router registration (Step 3.x; routes register on the app directly for now).
- K8s manifests (Step 4.4); this step ships the endpoints those manifests will probe but doesn't write the manifests.
- Production cutover gates (post-Auth0).

---

## Stop and ask if

- The readiness 2-second timeout fights with `engine.connect()`'s pool acquisition under load. If the pool is saturated, `engine.connect()` blocks waiting for a free connection — a long pool wait could push readiness over the 2s budget. For v0 with single-instance deploy and pool_size=10, this shouldn't happen, but if you find evidence of it, surface.
- L4's mock of `pg_roles` to return SUPERUSER produces an unexpected behaviour because `current_user` resolves differently in the mock vs. real engine. Surface; we'll evaluate alternative test approaches.
- The `app_with_test_routes` fixture from Step 2.3 has a stub `/v1/health` route in it. That fixture will conflict with the real `/v1/health` registered in `create_app()`. Either drop the fixture's stub route or reuse the real route via `create_app()`. **Decision: drop the fixture's `/v1/health` stub since `create_app()` now registers it.** Update Step 2.3's fixture accordingly; verify the existing 10 integration tests still pass.
- Existing `caplog` or `json_log_buffer` fixture interactions break under the new tests. Surface and we'll triage.
- `pytest_asyncio` async fixture pattern doesn't compose cleanly with FastAPI's lifespan-context-manager. Surface; lifespan tests are notoriously fiddly.

---

## Acceptance criteria

- 7 files created/modified.
- All 11 new tests pass.
- All 46 existing tests (Steps 2.1, 2.2a, 2.3) still pass — no regressions.
- mypy strict clean.
- check_setup 35/35.
- `uv run uvicorn admin_backend.main:app` starts cleanly. `curl http://localhost:8000/v1/health` returns 200 with the expected JSON; `curl http://localhost:8000/v1/ready` returns 200 with `{"status": "ready", "db": "ok"}`.
- OpenAPI doc at `/v1/openapi.json` lists both endpoints under the "meta" tag.

---

## Report (BEFORE proposing commit)

Per the per-step bundling convention, four bundles:

1. **Code/tests:** all files with line counts; sample curl responses for both endpoints.
2. **CLAUDE.md updates:** Current state Completed/Not yet completed updates.
3. **BUILD_PLAN.md updates:** Step 2.4 status flip + scope-in rewrite.
4. **Prompt file:** `prompts/step-2_4-health-lifespan-2026-05-02.md` confirmed in commit set.

Plus: test results (11 new + 46 existing = 57 total expected), mypy status, check_setup status, sample log line for /v1/health and /v1/ready (verifying audit middleware emits one line per request as expected).

Wait for explicit authorisation before staging or committing.

---

## End of prompt
