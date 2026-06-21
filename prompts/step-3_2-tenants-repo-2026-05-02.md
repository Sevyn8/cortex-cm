# Prompt — Step 3.2: TenantsRepo (Tenants Repository class)

> Generated 2026-05-02, 03:30 PM. Revised 2026-05-02, 08:15 PM (post-Step-3.0 reality + carry-over fixes from the original stress test).
> Paste this entire block into a fresh Claude Code session to start Step 3.2.
> First repository class. The pattern locked here propagates to every subsequent resource: stores (4.5), platform_users (5.1), tenant_users (5.2), org_nodes (5.3), RBAC (6.1), audit_logs (6.2). Get the shape right.
> **Step 3.0 is a hard prerequisite.** This prompt's `make_tenant` factory pattern (PLATFORM session inserts a tenant + commits) only works because Step 3.0 added the OR-clause to `tenants_self_access`'s WITH CHECK. Pre-3.0, the INSERT would fail with a CHECK violation on the NOSUPERUSER NOBYPASSRLS application role. If for any reason 3.0's migration is not at HEAD when this step starts, **stop and surface** — don't try to work around it.

---

## Pre-flight

1. Run `./scripts/check_setup.sh`. Expect 35/35.
2. `git log --oneline -10` — confirm the four commits Step 3.1 → drift sweep → convention extension → Step 3.0 at HEAD (Step 3.0 is the most recent). If head is anything else, surface.
3. Read `CLAUDE.md` fully. Focus on:
   - "Repository structure" — locate `repositories/` directory (line 722).
   - "Code naming" — `TenantsRepo` (line 760), the `Repo` suffix convention.
   - "Note on Repository pattern" (line 767) — one Repo class per resource, owns SELECT queries.
   - "Test pyramid" — Integration layer is "Real Postgres, real schema, no FastAPI" → for repositories (line 868).
   - "Test DB strategy" — per-test transaction rollback, factory fixtures (lines 871-876). Note: the rollback strategy described there does NOT apply to this step; see "Test infrastructure notes" below for why.
   - **D-03** — RLS enforcement; `app.tenant_id` and `app.user_type` set per-transaction by `get_tenant_session`.
   - **D-15** — `__table_args__["schema"]` parameterisation; `Tenant` model already obeys this.
   - **D-17** — RLS-blocked rows return 404 (handler-layer concern, not repository); `get_by_id` returns `None` when not found, the *router* converts to 404.
   - **D-21** — UUIDv7 via project `uuidv7()` PL/pgSQL function (no v4 anywhere on persisted columns; ephemeral test-only IDs are a separate matter, see R2).
   - **D-24** — AuthContext is the only path for tenant context; never accept `tenant_id` as a Repo method argument for RLS purposes (anti-pattern).
   - **D-27** — RLS policies wrap `current_setting` in `NULLIF`; repository code does not need to know this, but tests verifying cross-tenant isolation depend on it working.
   - **D-28** — provisional API response defaults from Step 3.1 (relevant only insofar as 3.2's tests fixture-build `Tenant` ORM objects, not `TenantRead`).
   - **D-29** — PLATFORM RLS visibility via policy clause (added at Step 3.0). Two clause shapes: unconditional (NOT NULL tenant_id, used on tenants/tenant_users/org_nodes/stores) and IS-NULL-gated (NULLABLE tenant_id, on user_role_assignments only). Permissive-impersonation property documented. **R3/R4/R5/R6/R7/R8 expectations are anchored on this; if D-29 doesn't read as you expect, surface.**
4. Read `docs/architecture.md` "Schema and storage", "Code structure", and Layer 1 RLS sections (post-Step-3.0 wording — Layer 1 was edited at 3.0).
5. Read `db/raw_ddl/Ithina_postgres_SQL_DDL_tenants_v3.sql` again — this prompt's tests insert real tenant rows and need to match the column set, the four enums, and the CHECK constraints. Note: per the convention captured at Step 3.0, the DDLs are frozen at as-shipped state; the live `tenants_self_access` policy is the post-3.0 OR-clause form, not what the DDL shows. The migration revision `21e2ad16303a` is the source of truth for the live policy.
6. Read `BUILD_PLAN.md` Step 3.2 in full:
   ```bash
   grep -A30 "## Step 3.2" BUILD_PLAN.md
   ```
   Compare with this prompt; surface mismatch before proceeding.
7. Read `src/admin_backend/db/session.py` (the `get_tenant_session` async generator, post-Step-2.3 with the `request_id` third var). Repo tests will use this directly.
8. Read `src/admin_backend/db/engine.py` (`create_engine`, `create_session_factory`).
9. Read `tests/integration/conftest.py` (the existing shared integration fixtures from Step 2.4 extraction). Identify what's reusable; what's not gets added in this step.
10. Read `src/admin_backend/auth/context.py` to confirm the AuthContext field set. The fixture sketches in this prompt assume the field set is `(user_id, tenant_id, user_type, ...)`; if there are additional required fields (email, roles, JWT raw, etc.), the fixture construction must include them. Any deviation from the sketch is fine as long as it satisfies the model. Mirror Step 2.2a's test patterns — those are the closest precedent for constructing AuthContext outside FastAPI.
11. Read this prompt fully.

---

## Step ID and intent

**Step 3.2** — `TenantsRepo`, the first repository class for the `tenants` table.

Three concrete deliverables:

1. **`TenantsRepo` class** in `src/admin_backend/repositories/tenants.py` with three SELECT methods.
2. **Integration tests** in `tests/integration/test_tenants_repo.py` against real Postgres, exercising RLS-bound sessions through `get_tenant_session`.
3. **Shared integration-test factories** for `make_tenant` (and any session-scoping helper), added to `tests/integration/conftest.py` for downstream reuse.

This step locks the Repository pattern. Every subsequent Repo (stores, platform_users, tenant_users, org_nodes, RBAC, audit_logs) follows the shape established here.

CLAUDE_CODE step. No router work; no API surface; no write methods.

---

## Source-of-truth specification for `TenantsRepo`

### File 1: `src/admin_backend/repositories/__init__.py` — new

```python
from admin_backend.repositories.tenants import TenantsRepo

__all__ = ["TenantsRepo"]
```

### File 2: `src/admin_backend/repositories/tenants.py` — new

```python
"""TenantsRepo — read-only data access for the `tenants` table.

The Repo class owns SELECT queries on `tenants`. It does NOT set tenant
context, NOT begin transactions, NOT handle commits/rollbacks. The
session passed in already carries `app.tenant_id` and `app.user_type`
GUCs set by `get_tenant_session` (Step 2.2a); RLS filtering is therefore
automatic. The Repo is unaware of multi-tenancy mechanics.

Per D-17, "row not visible" (whether absent or RLS-filtered) surfaces
as `None` from `get_by_id`. The router layer (Step 3.3) converts `None`
to a 404 response.

Per D-24, this Repo MUST NOT accept a `tenant_id` argument on any
visibility-bearing method. Tenant context flows through the session,
never through method parameters. Adding a `tenant_id` parameter would
create a second source of tenant identity that bypasses RLS.
"""
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from admin_backend.models.tenant import Tenant, TenantStatus


class TenantsRepo:
    """Read-only repository for `tenants`. RLS-bound via session GUCs."""

    async def get_by_id(
        self,
        session: AsyncSession,
        tenant_id: UUID,
    ) -> Tenant | None:
        """Return the tenant with this id, or None if not visible.

        "Not visible" covers both genuinely-missing rows and rows
        filtered out by RLS. Per D-17 the router converts None to 404;
        the Repo does not raise.
        """
        stmt = select(Tenant).where(Tenant.id == tenant_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_all(
        self,
        session: AsyncSession,
    ) -> list[Tenant]:
        """Return all tenants visible to the current session.

        Visibility is governed by RLS policy on `tenants`:
          - PLATFORM session sees all rows.
          - TENANT session sees only the row matching `app.tenant_id`.

        Order: deterministic by `name ASC` for stable test assertions
        and for predictable list rendering. Pagination is deferred
        (Step 3.3 introduces it at the API surface; the Repo can grow
        offset/limit kwargs when needed without breaking callers).
        """
        stmt = select(Tenant).order_by(Tenant.name.asc())
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_status(
        self,
        session: AsyncSession,
        status: TenantStatus,
    ) -> list[Tenant]:
        """Return tenants with the given status, visible to this session.

        Same RLS semantics as `list_all`. `status` is a typed enum
        argument; passing a raw string is a mypy error and should not
        be supported.
        """
        stmt = (
            select(Tenant)
            .where(Tenant.status == status)
            .order_by(Tenant.name.asc())
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())
```

Decisions baked in:

- **Class with instance methods, not module-level functions.** Two reasons. (1) Symmetry with the rest of the codebase: `StubAuthClient` is a class; `AuditContextMiddleware` is a class. (2) Step 3.3 will inject the Repo into router handlers via FastAPI `Depends`; injecting an instance is cleaner than injecting a module. Stateless instance is fine — no `__init__` parameters needed.
- **Session passed per call, not held on `self`.** A Repo instance is request-agnostic; the session is request-scoped. Holding the session on `self` would create lifecycle confusion and break the "one Repo, many requests" pattern.
- **Three methods, exactly as specified in BUILD_PLAN.md.** No `count_*`, no `exists_*`, no `list_with_filter`. Add when consumers exist; YAGNI for v0.
- **`scalar_one_or_none()` not `first()`.** `scalar_one_or_none` raises `MultipleResultsFound` if the PK constraint is somehow violated (defence in depth — should never fire in practice). `first()` would silently swallow that.
- **Deterministic ordering on lists.** `name ASC` is the default for both list methods. Tests assert on order; flaky tests from undefined ordering would be a real cost.
- **Type hints are load-bearing for mypy strict.** `UUID`, `TenantStatus`, `AsyncSession`, `Tenant | None`, `list[Tenant]`. No `Any`.

### File 3: `tests/integration/conftest.py` — modify

Add fixtures for AuthContext construction, an async `make_tenant` factory that *commits* and tracks created IDs for teardown, and a tenant-scoped session opener for the assertion phase. The existing `tests/integration/conftest.py` (from Step 2.4) has `engine`, `session_factory`, and likely an `app_with_test_routes` fixture; reuse those.

**Why the factory must commit:** integration tests open a `get_tenant_session` for setup (PLATFORM context, inserts tenants) and a *separate* `get_tenant_session` for assertions (TENANT-scoped). For the assertion-phase session to see rows inserted by the setup-phase session, the setup transaction must have committed — they're separate transactions on (potentially) separate connections from the pool. A "single transaction, both phases inside it" pattern is incompatible with using the real `get_tenant_session` flow (which opens its own `session.begin()` block per call). So setup commits; teardown explicitly DELETEs.

This pattern only works post-Step-3.0. Pre-3.0, the PLATFORM session's WITH CHECK predicate would have rejected the INSERT.

What to add (sketch — adapt to actual AuthContext fields):

```python
import pytest_asyncio
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator, Awaitable, Callable
from uuid import UUID

from sqlalchemy import delete

from admin_backend.auth.context import AuthContext
from admin_backend.db.session import get_tenant_session
from admin_backend.models.tenant import (
    Tenant, TenantRegion, TenantStatus,
)


# All fixtures here are function-scoped (pytest_asyncio.fixture default).
# Do NOT widen the scope on platform_session / make_tenant /
# tenant_session_factory — function scope is required for the per-test
# isolation pattern (each test gets its own tracker, its own session
# lifecycle).


@pytest_asyncio.fixture
def platform_auth() -> AuthContext:
    """Synthetic PLATFORM AuthContext for fixture-only DB operations.

    NOT a JWT-minted-and-verified context (that path lives in Step 2.3's
    middleware tests). For Repo-only tests we construct AuthContext
    directly. If AuthContext requires fields we can't satisfy here
    (e.g., a raw JWT string), surface — we'll either use
    AuthContext.model_construct() to bypass validation, or mint via
    make_test_jwt + StubAuthClient.verify per Step 2.1's helpers.
    """
    return AuthContext(
        # Adapt to actual model fields. Sketch only:
        user_id=...,        # any UUID; v4 is fine for ephemeral test fixtures
        tenant_id=None,     # PLATFORM = no tenant context
        user_type="PLATFORM",
        # ...other required fields
    )


@pytest_asyncio.fixture
def tenant_auth_factory() -> Callable[[UUID], AuthContext]:
    """Returns a callable: tenant_id -> AuthContext with TENANT context."""
    def _make(tenant_id: UUID) -> AuthContext:
        return AuthContext(
            user_id=...,
            tenant_id=tenant_id,
            user_type="TENANT",
            # ...other required fields
        )
    return _make


@pytest_asyncio.fixture
async def make_tenant(
    session_factory,
    platform_auth,
) -> AsyncIterator[Callable[..., Awaitable[Tenant]]]:
    """Async factory: insert + commit a Tenant via PLATFORM session,
    return the persisted ORM object with `id` populated. Tracks created
    IDs and DELETEs them at teardown.

    Usage (in a test):
        tenant_a = await make_tenant(name="Alpha")
        tenant_b = await make_tenant(name="Bravo", status=TenantStatus.ONBOARDING)
        # ... assertions using tenant_a.id, tenant_b.id ...
        # teardown happens automatically when the test exits

    The PLATFORM session's WITH CHECK admits the INSERT via the OR-clause
    landed at Step 3.0 (D-29). Without 3.0, this factory would fail at
    INSERT time with a CHECK violation.
    """
    created_ids: list[UUID] = []

    async def _make(
        *,
        name: str = "Test Tenant",
        region: TenantRegion = TenantRegion.US,
        status: TenantStatus = TenantStatus.ACTIVE,
        **overrides,
    ) -> Tenant:
        tenant = Tenant(name=name, region=region, status=status, **overrides)
        async for session in get_tenant_session(platform_auth, session_factory):
            session.add(tenant)
            await session.flush()           # populates DB defaults (id, created_at, etc.)
            await session.refresh(tenant)   # ensures all attrs loaded before detach
            created_ids.append(tenant.id)
        # Loop body ran once; session is now closed and transaction committed.
        # `tenant` is detached but all attributes are loaded (expire_on_commit=False
        # set at Step 2.2a in create_session_factory).
        return tenant

    yield _make

    # Teardown: DELETE all created tenants under a fresh PLATFORM session.
    # If a test errored mid-way, this still runs because pytest fixture
    # finalization runs in a finally-equivalent.
    if created_ids:
        async for session in get_tenant_session(platform_auth, session_factory):
            await session.execute(
                delete(Tenant).where(Tenant.id.in_(created_ids))
            )


@pytest_asyncio.fixture
async def platform_session(
    session_factory,
    platform_auth,
) -> AsyncIterator:
    """Single PLATFORM-scoped AsyncSession for read-side test phases.

    Use AFTER make_tenant for tests that query under PLATFORM (R1, R2,
    R3, R6, R8). The setup inserts (via make_tenant) have already
    committed, so this session sees them.
    """
    async for session in get_tenant_session(platform_auth, session_factory):
        yield session


@pytest_asyncio.fixture
def tenant_session_factory(
    session_factory,
    tenant_auth_factory,
) -> Callable[[UUID], object]:
    """Returns a callable: tenant_id -> async context manager yielding
    a TENANT-scoped AsyncSession.

    Use for tests that query under a specific tenant's context (R4, R5,
    R7, R9). The factory pattern (rather than a fixture taking tenant_id
    as a parameter) lets a single test open multiple tenant-scoped
    sessions if needed.

    Usage:
        async with tenant_session_factory(tenant_a.id) as session:
            results = await repo.list_all(session)
    """
    @asynccontextmanager
    async def _open(tenant_id: UUID):
        auth = tenant_auth_factory(tenant_id)
        async for session in get_tenant_session(auth, session_factory):
            yield session

    return _open
```

Verification points before submitting:

- Confirm AuthContext field set against `src/admin_backend/auth/context.py`. The sketches above assume `(user_id, tenant_id, user_type, ...)`; if more fields are required, fill them in. If construction from a Pydantic model that's frozen + has validators is awkward, `AuthContext.model_construct(**fields)` bypasses validation for fixture-only use — but check whether Step 2.2a's tests use that pattern before adopting it.
- `get_tenant_session` post-Step-2.3 takes `request_id` as a third kwarg with default `None`. Tests do not need to pass it; the `async for session in get_tenant_session(auth, session_factory):` pattern above works as-is. Verify the signature in `db/session.py` to be sure.
- `expire_on_commit=False` is set at Step 2.2a in `create_session_factory`. After the fixture's session closes, the returned `Tenant` retains its attributes. Verify; if expire_on_commit is True, attribute access after detach would raise.
- Teardown safety: pytest-asyncio fixture finalization runs even on test failure. The teardown DELETE block above does not need explicit try/finally because the `yield` point is what receives the test's exception, and the code after `yield` runs as part of fixture cleanup regardless.

### File 4: `tests/integration/test_tenants_repo.py` — new

Integration tests against real Postgres. **No FastAPI machinery.** The test pyramid is explicit (CLAUDE.md line 868): integration = real Postgres, real schema, no FastAPI. Use the session fixtures directly.

Tests, by category. All tests use the `make_tenant` async factory for setup, `platform_session` for PLATFORM read phases, and `tenant_session_factory(tid)` for TENANT read phases. `repo` is a function-local `TenantsRepo()` instance.

**R1. `get_by_id` happy path (PLATFORM).**
```python
tenant = await make_tenant(name="Acme")
result = await repo.get_by_id(platform_session, tenant.id)
assert result is not None
assert result.name == "Acme"
assert result.region == TenantRegion.US
assert result.status == TenantStatus.ACTIVE
```

**R2. `get_by_id` returns None for non-existent ID (PLATFORM).**
Use `uuid.uuid4()` to generate an ephemeral test-only ID guaranteed not to match any row. **Carve-out from D-21:** D-21's UUIDv7 invariant applies to *persisted* `id` columns (tenant rows in the DB). Throwaway test fixtures asserting on absence are fine to use `uuid4()` — there's nothing to be persisted, and the assertion is "no row matches this random ID." Don't use `uuidv7()` here either; it's a DB function, not a Python helper.
```python
ephemeral_id = uuid.uuid4()
result = await repo.get_by_id(platform_session, ephemeral_id)
assert result is None
```

**R3. `list_all` returns all visible tenants (PLATFORM).**
Anchored on D-29: PLATFORM session sees all rows via the OR-clause.
```python
await make_tenant(name="Alpha")
await make_tenant(name="Bravo")
await make_tenant(name="Charlie")
results = await repo.list_all(platform_session)
assert [t.name for t in results] == ["Alpha", "Bravo", "Charlie"]  # name ASC
```
Note: if other tests have committed and not cleaned up, this assertion could surface stale data. Acceptable mitigation: assert that the three names are present (subset check) rather than exact equality. Stricter mitigation: filter by a unique-per-test name prefix. Discuss in the report which you adopted.

**R4. `list_all` excludes invisible tenants (TENANT context).**
Load-bearing isolation test. Anchored on D-29: TENANT predicate is `id = app.tenant_id OR FALSE` — only matches own row.
```python
tenant_a = await make_tenant(name="TenantA-isolation")
tenant_b = await make_tenant(name="TenantB-isolation")
async with tenant_session_factory(tenant_a.id) as session:
    results = await repo.list_all(session)
assert len(results) == 1
assert results[0].id == tenant_a.id
```

**R5. `get_by_id` returns None for cross-tenant access (TENANT context).**
**Load-bearing isolation test #2.** RLS filters tenant B's row to invisibility from tenant A's session; Repo surfaces as `None`.
```python
tenant_a = await make_tenant(name="TenantA-cross")
tenant_b = await make_tenant(name="TenantB-cross")
async with tenant_session_factory(tenant_a.id) as session:
    result = await repo.get_by_id(session, tenant_b.id)
assert result is None
```

**R6. `list_by_status` filters correctly (PLATFORM).**
```python
await make_tenant(name="Onb-1", status=TenantStatus.ONBOARDING)
await make_tenant(name="Act-1", status=TenantStatus.ACTIVE)
await make_tenant(name="Act-2", status=TenantStatus.ACTIVE)
results = await repo.list_by_status(platform_session, TenantStatus.ACTIVE)
# Use subset assertion or prefix-filter to handle other-test stale rows.
active_names = {t.name for t in results}
assert {"Act-1", "Act-2"}.issubset(active_names)
assert "Onb-1" not in active_names
```

**R7. `list_by_status` respects RLS (TENANT context).**
```python
tenant_a = await make_tenant(name="TenantA-status", status=TenantStatus.ACTIVE)
tenant_b = await make_tenant(name="TenantB-status", status=TenantStatus.ACTIVE)
async with tenant_session_factory(tenant_a.id) as session:
    results = await repo.list_by_status(session, TenantStatus.ACTIVE)
assert len(results) == 1
assert results[0].id == tenant_a.id
```

**R8. PLATFORM list_all is unfiltered.**
Validates the D-29 PLATFORM OR-clause is firing on `tenants`. If this assertion ever fails post-3.0, the OR-clause was lost in a future migration — that's a security regression worth surfacing immediately. (The smoke test catches the same property at the policy level; this catches it at the Repo level.)
```python
await make_tenant(name="Mix-Onb", status=TenantStatus.ONBOARDING)
await make_tenant(name="Mix-Act", status=TenantStatus.ACTIVE)
await make_tenant(name="Mix-Sus", status=TenantStatus.SUSPENDED)
results = await repo.list_all(platform_session)
names = {t.name for t in results}
assert {"Mix-Onb", "Mix-Act", "Mix-Sus"}.issubset(names)
```

**R9. (optional, defensive) `list_all` empty for orphan TENANT context.**
Open a TENANT-scoped session with a `tenant_id` that doesn't match any row. Confirms RLS doesn't accidentally allow non-matching tenants. Use `uuid.uuid4()` for the orphan id — same carve-out reasoning as R2.
```python
orphan_id = uuid.uuid4()
async with tenant_session_factory(orphan_id) as session:
    results = await repo.list_all(session)
assert results == []
```

Test infrastructure notes:

- **Cleanup is via explicit DELETE in the `make_tenant` fixture's teardown,** not via transaction rollback. Reason: the test pattern uses two separate `get_tenant_session` invocations per test (one PLATFORM for setup, one TENANT for assertion). Each opens its own `session.begin()` block, which auto-commits at end-of-block. A "rollback the whole test" pattern would require the two phases to share a transaction, which isn't possible while using the real `get_tenant_session` flow. The explicit-DELETE pattern is what makes integration tests test the *real* setup-then-query path.
- **Stale-data robustness in assertions.** Because tests commit, a flaky test from another run (or a parallel test in the same run) could leave stray rows. Defensive assertions use subset checks (`{"Alpha"}.issubset(names)`) or unique-per-test name prefixes rather than exact-equality on row counts. R3, R6, R8 are the most exposed to this — surface in the report which mitigation you adopted (subset, prefix, or both).
- **PG enum binding.** Inserting a `Tenant` with `region=TenantRegion.US` and `status=TenantStatus.ACTIVE` should serialise to the PG enum values via the `values_callable` set in 3.1. If insertion fails with an enum error, that's a 3.1 model bug surfacing — surface it, don't paper over.
- **Don't pre-set `id`.** Let DB DEFAULT `uuidv7()` fire (D-21). After flush + refresh, `tenant.id` is populated; capture it for use in subsequent assertions.
- **Each test creates its own data.** Don't rely on data created by another test. The `make_tenant` factory is per-test (function-scoped fixture); created IDs only persist for the duration of one test.

### File 5: `BUILD_PLAN.md` — status flip

- **Step 3.2: TODO → DONE.** Update scope-in/acceptance text if the step deviated (e.g., test count differs from "tests pass").

### File 6: `CLAUDE.md` — Current state update

- **"Completed" list:** add a Step 3.2 bullet covering `TenantsRepo` (three methods), the integration-test pattern (real Postgres, real RLS, no FastAPI), the `make_tenant` factory and `platform_session` / `tenant_session_factory` fixtures (for downstream reuse by 4.5, 5.x, 6.x), the test count (~9 new), and the Repo-pattern lock-in note.
- **"Not yet completed" list:** advance "Steps 3.2 onward" to "Step 3.3 onward".
- **No new D-XX entries expected.** The Repo pattern was anchored at "Note on Repository pattern" (CLAUDE.md line 767); 3.2 implements it rather than deciding it.
- **No new FN-AB entries expected** unless the work surfaces something genuinely new.

### File 7: `architecture.md` — conditional, per the convention extension just merged

The Repository pattern is *named* in architecture.md if the doc has a "Code structure" section that lists the layers (router → repository → model). If it does, no edit needed (3.2 implements an already-described layer). If it doesn't mention repositories at all, add a short paragraph in the appropriate section. **Read the relevant section before editing**; don't add prose that duplicates what's already there. Per the convention extension: don't hunt for an edit. If nothing changed at the system-shape level, skip this file.

### File 8: `prompts/step-3_2-tenants-repo-2026-05-02.md`

This prompt file. Bundled into the commit per the per-step convention.

---

## Testing and regression discipline

### New tests added by this step

Already specified in File 4. Summary: ~9 integration tests covering happy path under PLATFORM, happy path under TENANT, cross-tenant isolation (R4 and R5 are load-bearing), status filter under both contexts, and one defensive test for orphan TENANT context.

Design discipline reminder: each test should fail against an empty `TenantsRepo`. If a test passes against a stub (`return []` / `return None`), the test isn't asserting a real behaviour.

### Regression risk surface introduced by this step

Concrete things to watch as you work, not just at end:

1. **`tests/integration/conftest.py` modifications must not break existing tests.** The current pytest baseline is 70 passes (Steps 2.1, 2.2a, 2.2b, 2.3, 2.4, 3.1). Step 3.0 added 0 pytest tests (smoke-only). Adding `platform_auth`, `tenant_auth_factory`, `make_tenant`, `platform_session`, `tenant_session_factory` fixtures should be additive — they don't override or shadow existing fixtures. Verify by running the existing integration suite (`uv run pytest tests/integration/ -v`) before adding any new test files; whatever count it currently shows is the baseline that must hold.
2. **AuthContext construction in fixtures.** The `AuthContext` Pydantic model is frozen (per AI-MT-03). If it has required fields beyond `user_id` / `tenant_id` / `user_type`, the fixture construction will fail at fixture-setup time, surfacing as ImportError-style noise rather than test failure. Read `src/admin_backend/auth/context.py` first; mirror Step 2.2a's test-helper pattern (closest precedent for AuthContext outside FastAPI).
3. **`get_tenant_session` signature.** Post-Step-2.3 it takes `(auth, session_factory, request_id=None)`. The fixtures here do not need `request_id`; don't add it. But verify the signature in `db/session.py` to be sure.
4. **PG enum binding from Python.** Inserting `Tenant(region=TenantRegion.US, status=TenantStatus.ACTIVE)` should produce SQL that emits `'US'` and `'ACTIVE'` as the enum-cast values. The `values_callable=lambda e: [m.value for m in e]` set at Step 3.1 handles this. If insertion fails with `invalid input value for enum tenant_*_enum`, that's a 3.1 bug — surface it.
5. **`expire_on_commit=False` is required.** Verify in `src/admin_backend/db/engine.py` that `create_session_factory` sets `expire_on_commit=False` (per Step 2.2a). If True, accessing `tenant.name` after the fixture's session closes would raise `DetachedInstanceError`. The `make_tenant` fixture's `await session.refresh(tenant)` mitigates partially but won't fix expire_on_commit=True for all attribute access patterns.
6. **Cleanup correctness on test failure.** The `make_tenant` fixture's teardown DELETE runs as part of pytest fixture finalization, which executes even when the test body raises. Verify by deliberately raising in one test (during local dev only — don't ship the failure) and confirming the created tenant rows are still cleaned up. Once verified, remove the deliberate failure.
7. **RLS observability when a test fails.** If a test fails with "expected 1 row, got 0" or "expected 1 row, got 2", the cause might be (a) RLS misbehaving, (b) stale data from an earlier test, or (c) a real Repo bug. Diagnostic helper: temporarily query `current_setting('app.tenant_id', TRUE)` and `current_setting('app.user_type', TRUE)` from inside the failing session to confirm GUCs are set as expected. Don't ship the diagnostic.
8. **Smoke test must remain at 64 PASS post-step.** Step 3.2 doesn't change DB structure or policies; smoke test should be unaffected. If smoke drops below 64, integration tests have leaked uncleaned rows or Repo code has somehow hit the DB outside test isolation. Surface immediately.

### Verification harness (run all five; all must be green)

```bash
# 1. Full pytest suite — new + regression
uv run pytest -v

# 2. mypy strict on the new and surrounding modules
uv run mypy --strict src/admin_backend/repositories src/admin_backend/models src/admin_backend/schemas src/admin_backend/db

# 3. Pre-flight checker
./scripts/check_setup.sh

# 4. Smoke: Repo imports and is callable
uv run python -c "from admin_backend.repositories.tenants import TenantsRepo; r = TenantsRepo(); print(type(r).__name__, dir(r))"

# 5. RLS smoke test — should still pass at 64 PASS post-3.0 baseline
python scripts/smoke_test.py
```

Expected: ~9 new + 70 existing = ~79 pytest passes; mypy clean; check_setup 35/35; Repo smoke prints `TenantsRepo` and lists three methods plus dunder; RLS smoke 64 PASS unchanged.

If any of the five is not green, **report the failure rather than the step.** Don't ship a step with one leg of the harness dropped.

---

## Scope out

- **Router and endpoints.** Step 3.3.
- **List-response wrapping (`TenantListResponse`, `Pagination`).** Step 3.3, when the consumer exists.
- **Write methods (`create`, `update`, soft-delete via `terminate`).** Post-v0 per FN-AB-12.
- **Other Repos (Stores, PlatformUsers, etc.).** Steps 4.5, 5.x, 6.x. They reuse this step's pattern.
- **API-contract OpenAPI generation.** Step 3.3.
- **Per-endpoint documentation (`docs/endpoints/tenants.md`).** Step 3.3.

---

## Stop and ask if

- The existing `tests/integration/conftest.py` does not expose a usable `engine` and `session_factory` fixture, or exposes them under different names than expected. Surface the actual layout.
- `AuthContext` requires fields the fixture construction can't satisfy (e.g., a JWT raw string that has to be minted). Surface; we'll either use `make_test_jwt`-style helpers or `AuthContext.model_construct()` to bypass validation for fixture-only cases.
- `expire_on_commit=False` is *not* set in `create_session_factory`. The `make_tenant` fixture's "return detached but loaded Tenant" pattern depends on it. If this is the case, surface — we'll either fix `create_session_factory` (small, but a Step 2.2a edit and worth flagging) or change the fixture pattern.
- The teardown DELETE in `make_tenant` fails because the PLATFORM session can't authorise the DELETE statement. This shouldn't happen post-3.0 (the OR-clause covers all FOR ALL operations), but if it does, surface — it's a gap in 3.0's policy that needs fixing before 3.2 can land cleanly.
- Inserting a `Tenant` ORM object fails for any reason (enum binding, NOT NULL constraint, CHECK constraint mismatch with the values_callable mapping). This indicates a 3.1 issue — surface, don't work around.
- The `make_tenant` factory's defaults conflict with the DDL's CHECK constraints (e.g., the `name` length constraint, or the paired `monthly_revenue_usd` / `monthly_revenue_as_of_date` constraint if defaults set one but not the other). Surface; we'll fix the factory.
- A test reveals an actual RLS gap (cross-tenant read returns rows when it shouldn't). This is a security-critical finding — stop, surface, do not commit.
- Smoke test count drops below 64 post-implementation. Means an integration test left state behind or somehow contaminated the DB. Surface immediately.

---

## Acceptance criteria

- 8 files created/modified per the bundle (4 source + 4 doc/prompt; architecture.md may be 0-edit).
- All new tests pass: ~9 integration tests for `TenantsRepo`.
- All existing 70 tests still pass — no regressions.
- mypy strict clean on `repositories`, `models`, `schemas`, `db`.
- `check_setup.sh` 35/35.
- Smoke command prints `TenantsRepo` with three methods.
- **Cross-tenant isolation tests (R4, R5) explicitly verified** — these are the load-bearing assertions of this step. Report should call them out by name.
- RLS smoke test still at 64 PASS — Step 3.2 must not break DB-level isolation.

---

## Report (BEFORE proposing commit)

Per the per-step bundling convention (now five-item, per the convention extension that just landed):

1. **Code/tests:** files created/modified with line counts; test count delta (~9 new); R4 and R5 results highlighted as the cross-tenant isolation lock; sample compiled SQL from one Repo method (e.g., `select(Tenant).where(Tenant.id == ...)` rendered) to confirm the schema-qualified SQL is still emitted from the Repo layer.
2. **CLAUDE.md updates:** Current state Completed/Not-yet-completed updates; any new conventions clarified during the work (unlikely but possible — e.g., if a new Repo-test fixture pattern emerges that's worth documenting).
3. **BUILD_PLAN.md updates:** Step 3.2 status flip; scope-in/acceptance corrections if the step deviated.
4. **architecture.md updates:** "no change" if the system shape didn't move (likely outcome); otherwise list the edit.
5. **Prompt file:** `prompts/step-3_2-tenants-repo-2026-05-02.md` confirmed in commit set.

Plus: pytest counts (~79 expected), mypy status, check_setup status.

Wait for explicit authorisation before staging or committing.

---

## End of prompt
