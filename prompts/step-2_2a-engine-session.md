# Prompt — Step 2.2a: Async DB engine, `get_tenant_session` dependency, connect hook

> Paste this entire block into a fresh Claude Code session when starting Step 2.2a.
> Step 2.2 was split into 2.2a (engine + session bootstrap) and 2.2b (FN-AB-14 fix). This is 2.2a; 2.2b lands the policy amendment afterward.
> Revised after stress test: uses `set_config()` for NULL GUCs (works correctly on custom GUCs); db_schema is identifier-validated to prevent injection; dependency is reshaped to work with FastAPI's `Depends()` patterns; test guidance for concurrent-session isolation.

---

## Pre-flight

Before doing any work:

1. Run `./scripts/check_setup.sh`. If any check fails, stop and report. Do not attempt to fix setup unless told.
2. Read `CLAUDE.md` fully. Pay particular attention to:
   - **D-15** (schema parameterised via `DB_SCHEMA`).
   - **D-24** (JWT identity-only claims; AuthContext shape).
   - **FN-AB-14** (PLATFORM-audience policy fix; deferred to 2.2b but informs the session-bootstrap shape now).
   - The "Current state" section (local DB state, role attributes).
3. Read `docs/architecture.md` "Multi-tenancy and data isolation" section in full.
4. Read `BUILD_PLAN.md` Step 2.2 in full.
5. Read this prompt fully and confirm scope before writing code.

---

## Step ID and intent

**Step 2.2a** — Build the async SQLAlchemy engine, the `get_tenant_session` FastAPI dependency, the connect-time hook that sets `search_path`, the per-request session-bootstrap that sets `app.tenant_id` and `app.user_type` from `AuthContext`, and the runtime startup gate that refuses to boot if the application role has SUPERUSER or BYPASSRLS.

This step lays the DB-access foundation for all subsequent handler work. The session-bootstrap discipline (what gets set, where it comes from, how it's protected from injection) is the load-bearing mechanism for tenant isolation per CLAUDE.md D-03.

This is a CLAUDE_CODE step. No FastAPI app yet, no middleware, no endpoints. Just the engine, the dependency, the hook, the privilege check, and unit tests.

---

## Required behaviour

### Session var discipline

Per the architecture, every DB session used by request handlers must have these set on its connection:

1. `search_path` set to `{db_schema}, public` per session — D-15 discipline. Set at connect-time via SQLAlchemy event hook; belt-and-suspenders against role-default drift.
2. `app.tenant_id` set from `AuthContext.tenant_id`. If `tenant_id` is None (PLATFORM user not impersonating), set the GUC to NULL via `set_config('app.tenant_id', NULL, true)`. Do NOT use empty string; do NOT use `SET LOCAL ... = ''`; do NOT use `RESET` (its behaviour on registered custom GUCs is version-dependent).
3. `app.user_type` set from `AuthContext.user_type`. Always non-NULL: either `'PLATFORM'` or `'TENANT'`.

These three setters happen at the start of every transaction, sourced ONLY from `AuthContext`. NEVER from request body, query params, headers, or any other source. Defence-in-depth gate against privilege escalation.

### `set_config()` is the right primitive for nullable custom GUCs

Postgres has two ways to set runtime parameters in a transaction:

- `SET LOCAL <name> = <value>` — works for non-NULL values; cannot represent NULL cleanly.
- `SELECT set_config('<name>', <value>, true)` — function form; third arg `true` means "transaction-local" (equivalent to SET LOCAL); accepts NULL as the value cleanly.

For this step, use `set_config()` for both `app.tenant_id` and `app.user_type` so the code is uniform and NULL-handling is correct on the tenant_id branch.

### `set_config()` requires an active transaction (when `is_local=true`)

Like SET LOCAL, `set_config(..., true)` only persists within a transaction. The dependency must explicitly `async with session.begin():` before issuing the calls.

### Engine connect-time vs per-request

Two different hook points; appropriate uses:

- **Connect (one-time per physical connection):** SQLAlchemy `event.listens_for(engine.sync_engine, "connect")`. Set `search_path` here.
- **Per request (in the dependency):** AuthContext-derived session vars. Inline in `get_tenant_session`, not via events.

### Runtime privilege check

At application startup, query `pg_roles` for the connecting role and raise `AppRolePrivilegeError` if SUPERUSER or BYPASSRLS. This step exposes the function; Step 2.4 wires it into the FastAPI lifespan.

### DB_SCHEMA must be identifier-validated to prevent injection

The connect-time hook composes a SQL string `SET search_path TO {db_schema}, public`. Even though `db_schema` comes from env var (not user input), defence-in-depth requires validating it as a Postgres identifier:

```python
import re
_IDENTIFIER_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
```

Validate at Settings construction time (a `field_validator` on `db_schema`) so any future code path that flows non-identifier characters in is caught early.

### Operational gotchas (from earlier steps)

1. **Bash subshells don't inherit env vars.** `set -a && source .env && set +a` per call.
2. **Pydantic v2.** Already enforced.
3. **psycopg URL prefix.** Engine uses SQLAlchemy URL (`postgresql+psycopg://...`); psycopg3 accepts it directly.

---

## Scope in

### File 1: Update `src/admin_backend/config.py`

Add a `field_validator` to `db_schema`:

```python
import re
from pydantic import field_validator

_IDENTIFIER_RE = re.compile(r"^[a-z_][a-z0-9_]*$")

class Settings(BaseSettings):
    # ... existing fields ...

    @field_validator("db_schema")
    @classmethod
    def db_schema_must_be_identifier(cls, v: str) -> str:
        if not _IDENTIFIER_RE.match(v):
            raise ValueError(
                f"DB_SCHEMA must be a valid lowercase identifier "
                f"(letters, digits, underscores; starting with letter or underscore); "
                f"got: {v!r}"
            )
        return v
```

This adds a 1-test test case to the existing Step 2.1 test suite (E20: invalid DB_SCHEMA rejected). Add to test_stub_auth.py or a new test file.

### File 2: `src/admin_backend/db/__init__.py`

Empty package marker.

### File 3: `src/admin_backend/db/engine.py`

```python
"""Async SQLAlchemy engine factory and runtime privilege check.

The engine is created from Settings at app startup (Step 2.4 wires this
into the FastAPI lifespan). Pool configuration is conservative for v0;
tune post-launch if metrics show contention.

Connect-time hook sets `search_path` for every new physical connection
as belt-and-suspenders against role-default drift (per D-15).
Per-request session vars (`app.tenant_id`, `app.user_type`) are set in
the dependency, not here, because they depend on AuthContext.
"""
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from admin_backend.config import Settings
from admin_backend.errors import AdminBackendError


class AppRolePrivilegeError(AdminBackendError):
    """Application role has SUPERUSER or BYPASSRLS; refuses to start."""


def create_engine(settings: Settings) -> AsyncEngine:
    """Create the async engine with pool config and connect-time hook."""
    engine = create_async_engine(
        settings.database_url,
        pool_size=10,
        max_overflow=5,
        pool_timeout=30,
        # pool_pre_ping=True issues SELECT 1 before each checkout. ~1ms locally,
        # ~5-10ms in cloud (over Cloud SQL proxy). Robustness vs cost trade-off:
        # detects stale connections after Cloud SQL restarts, firewall idle-kills,
        # network partitions. Worth it for v0; revisit if metrics show this is
        # the dominant per-request latency.
        pool_pre_ping=True,
        # pool_recycle=1800 closes physical connections every 30 min. Defense
        # against Cloud SQL's idle-connection drop and against any firewall
        # that idle-kills connections. Harmless on local.
        pool_recycle=1800,
        echo=False,
    )

    # Connect-time hook: set search_path on every new physical connection.
    # Belt-and-suspenders: role-level default search_path is also set
    # at DB setup time. Explicit per-connection is what we depend on per D-15.
    #
    # f-string is safe: db_schema is field_validated as a Postgres identifier
    # at Settings construction time. Cannot contain SQL injection.
    @event.listens_for(engine.sync_engine, "connect")
    def set_search_path_on_connect(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute(f"SET search_path TO {settings.db_schema}, public")
        cursor.close()

    return engine


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create the async sessionmaker bound to this engine.

    `expire_on_commit=False` is the conventional async setting: prevents
    implicit refreshes after commit, which would issue queries inside a
    closed transaction context.
    """
    return async_sessionmaker(
        engine,
        expire_on_commit=False,
        class_=AsyncSession,
    )


async def assert_app_role_no_bypassrls(engine: AsyncEngine) -> None:
    """Verify the connecting role has neither SUPERUSER nor BYPASSRLS.

    Either attribute bypasses RLS entirely, regardless of FORCE on tables.
    This function MUST run at app startup; if it raises, the app must
    refuse to start. Step 2.4 wires this into the FastAPI lifespan.

    Note: `current_user` returns the active role. v0 does not use SET ROLE
    anywhere, so current_user == the originally-connected role. If a future
    code path uses SET ROLE, this check would need rethinking.

    Raises:
        AppRolePrivilegeError: the connecting role has one or both attributes.
    """
    async with engine.connect() as conn:
        result = await conn.execute(text("""
            SELECT rolsuper, rolbypassrls
            FROM pg_roles
            WHERE rolname = current_user
        """))
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
```

### File 4: `src/admin_backend/db/session.py`

```python
"""FastAPI dependency: yield a session with tenant context set per AuthContext.

The dependency is the ONLY code path that sets `app.tenant_id` and
`app.user_type` per request. Both come from AuthContext, never from
request body/headers/params. This is the load-bearing isolation gate.

Wiring into FastAPI happens at Step 2.3 (middleware populates
request.state.auth; a Depends() provider returns the current AuthContext
and the session_factory).
"""
from typing import AsyncIterator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from admin_backend.auth.context import AuthContext


async def get_tenant_session(
    auth: AuthContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Yield an AsyncSession with app.tenant_id and app.user_type set.

    Invoked per request via FastAPI's Depends() machinery (wired at Step 2.3).
    For each request:
        1. Open a session.
        2. Begin a transaction.
        3. set_config app.tenant_id and app.user_type from AuthContext.
        4. Yield the session to the handler.
        5. On clean exit: commit. On exception: rollback.

    set_config(name, value, is_local=true) requires an active transaction;
    the begin() context manager handles this. The vars are transaction-scoped:
    when the transaction ends, they reset (no leakage between requests).

    AuthContext is consumed read-only; this dependency never mutates it.
    """
    async with session_factory() as session:
        async with session.begin():
            # Set app.tenant_id (or NULL for PLATFORM not-impersonating).
            # set_config() is the right primitive for nullable custom GUCs.
            tenant_id_value = str(auth.tenant_id) if auth.tenant_id is not None else None
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tid, true)"),
                {"tid": tenant_id_value},
            )

            # Set app.user_type. Always non-NULL.
            await session.execute(
                text("SELECT set_config('app.user_type', :ut, true)"),
                {"ut": auth.user_type},
            )

            yield session
            # Transaction commits here unless an exception escapes.
```

**Note on FastAPI integration (Step 2.3 territory):** The function signature `(auth, session_factory)` is intentional for unit-testability — callable directly without FastAPI machinery. At Step 2.3, the wiring will look like:

```python
# In a separate module at Step 2.3:
async def get_tenant_session_dep(
    auth: AuthContext = Depends(get_current_auth),
    session_factory: async_sessionmaker = Depends(get_session_factory),
) -> AsyncIterator[AsyncSession]:
    async for session in get_tenant_session(auth, session_factory):
        yield session
```

This wraps `get_tenant_session` in a FastAPI-shaped function while keeping the core logic testable. Don't build this wrapper in Step 2.2a.

### File 5: Tests

#### `tests/unit/test_engine.py`

T1. `create_engine(settings)` returns an `AsyncEngine`. Engine connects successfully to the local DB.

T2. After establishing a connection, querying `current_setting('search_path')` returns `core, public` (the connect-time hook fired).

T3. `await assert_app_role_no_bypassrls(engine)` against the local DB returns without raising (current local state: NOSUPERUSER NOBYPASSRLS per Step 1.5).

T4. Mock `engine.connect()` to return `(rolsuper=True, rolbypassrls=False)`. Verify `assert_app_role_no_bypassrls` raises `AppRolePrivilegeError` with both attribute values in the message.

T5. Same as T4 but `(False, True)`. Verify raises.

T6. Same as T4 but `(True, True)`. Verify raises.

T7. Mock pg_roles to return no rows. Verify raises with the "could not query pg_roles" message.

T8. Test the `db_schema` validator: construct Settings with `db_schema="core; DROP TABLE tenants;"`. Verify raises ValidationError with a clear message about identifier validity.

#### `tests/unit/test_session.py`

T9. Construct a TENANT AuthContext with tenant_id=A. Use `get_tenant_session(auth, session_factory)` as an async iterator. Inside the yielded session, query `current_setting('app.tenant_id', TRUE)::uuid` — verify it equals A.

T10. Same setup; query `current_setting('app.user_type', TRUE)` — verify returns `'TENANT'`.

T11. Construct a PLATFORM AuthContext with tenant_id=None. Inside the session, query `current_setting('app.tenant_id', TRUE)` — verify returns NULL (not empty string). Specifically: `SELECT current_setting('app.tenant_id', TRUE) IS NULL` should return TRUE.

T12. Same setup; verify `app.user_type` is `'PLATFORM'`.

T13. Construct a PLATFORM AuthContext with tenant_id=A (impersonation). Verify `app.tenant_id` is A. Per FN-AB-14 permissive rule.

T14. **Concurrent session isolation.** Create an engine with `pool_size=2, max_overflow=0` (forces real concurrency on different physical connections). Open two `get_tenant_session` async iterators concurrently with different AuthContexts (TENANT-A, TENANT-B). Use `asyncio.gather` to truly run them concurrently. From within each session, query `current_setting('app.tenant_id', TRUE)`. Verify session 1 sees A, session 2 sees B (no cross-session leakage).

T15. **No leakage to subsequent connections.** Open a `get_tenant_session` for TENANT-A, complete the transaction. Then open a fresh raw connection (bypass `get_tenant_session`) on the same engine. Query `current_setting('app.tenant_id', TRUE)`. Verify it's NULL (the GUC is transaction-scoped; it doesn't persist on the connection after the transaction ends).

Total: 15 tests. All must pass.

### File 6: Update `pyproject.toml` if needed

Verify presence of:
- `psycopg[binary]>=3.1` (or psycopg3 equivalent)
- `sqlalchemy[asyncio]>=2.0`

If anything is missing for the async engine, surface and add.

---

## Scope out

- FastAPI middleware that populates AuthContext on the request (Step 2.3).
- The actual `Depends(get_tenant_session)` wiring in handlers (Step 2.3 / 2.4).
- Health check endpoint and main.py (Step 2.4).
- The lifespan hookup for `assert_app_role_no_bypassrls` (Step 2.4).
- The FN-AB-14 policy fix (Step 2.2b).
- The smoke test 9-row truth table update (Step 2.2b).
- Auth0 client (post-launch).
- Audit-log integration (Step 6.x).
- Read replicas, multi-region routing (post-launch).

---

## Stop and ask if

- The connect-time hook for `search_path` causes test failures because of asyncio event-loop interactions. SQLAlchemy 2.x async + sync events have known sharp edges. If the hook fires at a problematic point, surface; we may need to use `engine.sync_engine` differently or move to a connect arg.
- `set_config()` with a NULL value behaves unexpectedly on the local Postgres version. Specifically: after `set_config('app.tenant_id', NULL, true)`, verify that `current_setting('app.tenant_id', TRUE) IS NULL` returns TRUE. If the GUC ends up as empty string instead, surface.
- `current_user` returns something other than the role in DATABASE_URL. v0 does not use SET ROLE; if it does, the privilege check needs to use `session_user` instead. Surface.
- The pool size or pre_ping defaults conflict with what's already documented in CLAUDE.md or architecture.md. Use the documented values.
- DB_SCHEMA validator rejects values that should be legitimate (e.g., contains uppercase). Postgres identifiers are case-sensitive when quoted; v0 uses lowercase by convention but if a future need calls for uppercase, the regex needs updating.
- `pool_size=2, max_overflow=0` for the concurrency test conflicts with Postgres's `max_connections` setting. Local Postgres allows ~100 connections; should be fine. If something else, surface.

---

## Acceptance criteria

- `src/admin_backend/config.py`: `db_schema` field has a validator that rejects non-identifier values.
- `src/admin_backend/db/engine.py` defines `create_engine(settings)`, `create_session_factory(engine)`, `assert_app_role_no_bypassrls(engine)`, `AppRolePrivilegeError`.
- Engine factory uses sensible pool config; connect-time event hook sets `search_path` from `settings.db_schema`.
- `src/admin_backend/db/session.py` defines `get_tenant_session(auth, session_factory)` as an async generator.
- `set_config()` (not SET LOCAL, not RESET) is used for both session vars.
- `assert_app_role_no_bypassrls` raises `AppRolePrivilegeError` with a clear remediation message.
- All 15 tests pass under `uv run pytest tests/unit/test_engine.py tests/unit/test_session.py -v`.
- The Step 2.1 tests still pass (no regression from `db_schema` validator addition; if E17/E18/E19 used a `db_schema` value other than `core`, they may need updating).
- `uv run mypy --strict src/admin_backend` passes.
- `./scripts/check_setup.sh` passes (35/35 expected).
- Tests use the real local DB (no DB mocking for session tests); only privilege-check failure cases use mocking.

---

## What to report at end (BEFORE proposing any commit)

**Report first; commit only after explicit authorisation.** Do NOT run `git add` or `git commit` until I respond to your report.

Provide:

- Files created/modified, with line counts.
- The 15 test results (and confirmation Step 2.1 tests still pass).
- Sample output from a TENANT session and a PLATFORM session: `current_setting('app.tenant_id', TRUE)` and `current_setting('app.user_type', TRUE)` for each.
- Output of `assert_app_role_no_bypassrls` against the live local DB.
- Output of `mypy --strict src/admin_backend`.
- `./scripts/check_setup.sh` final state.
- Any deviations from this prompt's procedure and why.
- Anything you noticed that doesn't match `CLAUDE.md` (D-03, D-15, D-24, FN-AB-14).

After I authorise the report, propose a Pattern A commit.

---

## After completing

When I authorise (after reviewing the report), propose:

```
git status
git add -A
git commit -m "Step 2.2a: async DB engine, get_tenant_session dependency, runtime privilege check

- src/admin_backend/db/engine.py: create_engine() with conservative pool config (pool_size=10, max_overflow=5, pool_pre_ping=True, pool_recycle=1800); connect-time event hook sets search_path from settings.db_schema (D-15 belt-and-suspenders); create_session_factory() async sessionmaker with expire_on_commit=False; assert_app_role_no_bypassrls() runtime check raises AppRolePrivilegeError if SUPERUSER or BYPASSRLS detected (defence against Step 1.5 finding)
- src/admin_backend/db/session.py: get_tenant_session(auth, session_factory) async generator dependency; uses set_config(name, value, true) for both session vars (correctly handles NULL for PLATFORM-not-impersonating tenant_id case); SET sources are AuthContext only — never headers/params/body (load-bearing isolation gate per D-03)
- src/admin_backend/config.py: db_schema field_validator rejects non-identifier values (defence against potential future SQL-injection paths into the connect hook's f-string)
- 15 tests: engine init + connect hook (T1-T2), privilege check (T3-T7), db_schema validator (T8), session var setting (T9-T13), concurrent session isolation (T14), no-leakage (T15)
- mypy strict clean
- BUILD_PLAN.md Step 2.2a status TODO -> DONE"
```

Ask user "Run? yes / no / edit message".

---

## End of prompt
