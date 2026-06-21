# PgBouncer readiness audit

## 1. Current pooling setup

- Driver: psycopg3 (`psycopg[binary]>=3.2`) per `pyproject.toml:11`. SQLAlchemy `>=2.0.36` with `[asyncio]` extras at `pyproject.toml:10`. Async-only.
- One application engine. `src/admin_backend/db/engine.py:43`: `create_async_engine(settings.database_url, pool_size=10, max_overflow=5, pool_timeout=30, pool_pre_ping=True, pool_recycle=1800, connect_args={"prepare_threshold": None}, echo=False)`.
- Engine constructed once at lifespan startup (`src/admin_backend/main.py:69`); session factory at `src/admin_backend/db/engine.py:90` (`async_sessionmaker(engine, expire_on_commit=False)`).
- DSN: `Settings.database_url` (`src/admin_backend/config.py:86`) loaded via `pydantic-settings` from `.env` (`src/admin_backend/config.py:79`).
- Connect-time hook at `src/admin_backend/db/engine.py:72-76` sets `search_path` per physical connection.
- Alembic engine is separate. `migrations/env.py:78-82`: `async_engine_from_config(..., poolclass=pool.NullPool)`. Schema set inside the migration transaction at `migrations/env.py:73`.

## 2. Blockers — will break under transaction pooling

`src/admin_backend/db/engine.py:75` — `cursor.execute(f"SET search_path TO {db_schema}, public")` inside a `"connect"` event listener is session-level state on the backend that handled the connect; PgBouncer in transaction mode rebinds backends per transaction, so subsequent transactions on the same SA client connection land on backends where `search_path` reverts to default and unqualified-table queries miss : move `search_path` into the DSN (`?options=-csearch_path%3D...`) or set as the role's default, or schema-qualify every raw `text()` query.

## 3. Warnings — will degrade or leak state

`src/admin_backend/db/engine.py:53` — `pool_pre_ping=True` issues a `SELECT 1` before each checkout, which becomes its own transaction through PgBouncer (extra round trip + backend acquire/release per request) : drop pre_ping once on PgBouncer or rely on `pool_recycle` plus PgBouncer's `server_check_query`.

`src/admin_backend/db/engine.py:57` — `pool_recycle=1800` controls SA's client→PgBouncer connections, not the underlying server backends; coexists with PgBouncer's `server_idle_timeout` / `server_lifetime` and double-counts toward reconnect frequency : leave alone or reduce once PgBouncer's `server_lifetime` covers the same intent.

`src/admin_backend/db/session.py:69` — first `set_config('app.tenant_id', value, true)` per backend registers the placeholder GUC at session level; under transaction pooling each new backend starts unregistered (`current_setting` returns NULL natively rather than `''`); the existing `NULLIF(..., '')` policy wrapper (D-27) handles both cases, but any future code reading `current_setting('app.tenant_id', TRUE)` directly will see NULL on fresh-backend transactions and `''` on reused ones : keep the NULLIF wrapper on every read, or move `app.*` GUCs into PgBouncer's `track_extra_parameters` if cross-transaction persistence is ever needed.

## 4. Fine — checked, no issue

- `connect_args={"prepare_threshold": None}` at `src/admin_backend/db/engine.py:60` disables psycopg3 server-side prepared statements (the canonical PgBouncer-readiness lever).
- All three `set_config` calls in `src/admin_backend/db/session.py:69,73,77` use `is_local=true` (transaction-scoped GUCs).
- No `LISTEN` / `NOTIFY` / `pg_notify` anywhere in `src/`, `migrations/`.
- No `pg_advisory_lock` / `pg_try_advisory_lock` (session-level locks) anywhere; no `pg_advisory_xact_lock` either (none needed).
- No `CREATE TEMP TABLE` / `CREATE TEMPORARY TABLE` / `DECLARE ... WITH HOLD`.
- No server-side cursors: no `stream_results=True`, no `yield_per`, no `server_side_cursors=True`, no `execution_options(stream_results=...)`.
- No `register_type` / `register_adapter` / `register_hstore` on connections.
- No `application_name` set via `SET` (not in DSN either; can be added there if needed).
- No long-lived transactions: searched `requests.`, `httpx.`, `aiohttp.`, `time.sleep`, `await asyncio.sleep` inside session blocks; no hits in handlers/repos.
- No `BackgroundTasks` / `asyncio.create_task` / Celery / RQ.
- Alembic: separate engine with `NullPool` (`migrations/env.py:81`); migrations run their own `BEGIN`/`COMMIT`; no temp objects.

Not ready : 1 blocker.
