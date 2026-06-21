# Prompt — Step 3.1: Tenant ORM model + TenantRead schema

> Generated 2026-05-02, 01:45 PM. Supersedes the earlier `prompts/step-3_1-tenant-model-schema.md` (kept in git as historical per the CLAUDE.md prompt-versioning convention).
> Paste this entire block into a fresh Claude Code session to start Step 3.1.
> First domain step. The model + schema pattern locked here propagates to every subsequent resource: stores (4.5), platform_users (5.1), tenant_users (5.2), org_nodes (5.3), RBAC (6.1), audit_logs (6.2).

---

## Important context: api-contract.md is still in TEMPLATE state

Step 2.0 (the contract sync meeting with the frontend developer) has not happened. `docs/api-contract.md` is the pre-meeting template; "Decision" rows are empty. Step 3.1 cannot wait, so this prompt locks **explicit provisional defaults** for the five Qs that affect the schema layer. If Step 2.0 later locks something different, the change is localised to `TenantRead` and propagates from there.

Provisional defaults this prompt locks (and only for 3.1):

- **Q1 response naming:** `snake_case`.
- **Q4 dates:** ISO 8601 with timezone offset (Pydantic v2 default for `datetime`).
- **Q7 nulls:** include nullable fields explicitly (no `exclude_none`).
- **Q11 NUMERIC:** monetary fields serialise as **string** to preserve precision in JS clients.
- **Q2 list-response wrapping** is NOT decided here. Step 3.1 produces only the single-object `TenantRead`; list-response shape (`{items, pagination}` vs raw array) is settled in Step 3.2/3.3 when the consumer exists.

If any of these provisional defaults conflict with something already shipped or implied elsewhere, **stop and surface** before continuing.

---

## Pre-flight

1. Run `./scripts/check_setup.sh`. Expect 35/35.
2. `git log --oneline -10` — confirm Step 2.4 at HEAD.
3. Read `CLAUDE.md` fully. Focus on:
   - "Schema reference" (10 tables across 8 DDLs).
   - "Code conventions and structure" — repository layout, naming.
   - D-13 (audit-actor patterns; tenants is Pattern (a)), D-15 (DB_SCHEMA parameterisation), D-21 (UUIDv7, snake_case, `_at` suffix, TIMESTAMPTZ), D-24 (AuthContext shape), D-27 (NULLIF on RLS).
   - "Current state" Completed list (should reflect Steps 1.3 through 2.4).
4. Read `docs/architecture.md` "Schema and storage" section.
5. Read `db/raw_ddl/Ithina_postgres_SQL_DDL_tenants_v3.sql` fully. **This is the source of truth for the model. Match every column, including the four enums.**
6. Read `BUILD_PLAN.md` Step 3.1 in full. Pre-flight grep for drift:
   ```bash
   grep -A12 "## Step 3.1" BUILD_PLAN.md
   ```
   Compare what's there vs. what this prompt says. Surface any mismatch before proceeding.
7. Read this prompt fully.

---

## Step ID and intent

**Step 3.1** — Tenant ORM model + Pydantic Read schema.

Three concrete deliverables:

1. **`Tenant` SQLAlchemy 2.x ORM model** mapping every column of `tenants_v3.sql`, including the four typed enums.
2. **`TenantRead` Pydantic v2 schema** for API responses, applying the provisional contract defaults above.
3. **`Base` DeclarativeBase** in `src/admin_backend/db/base.py` (created here; reused by every subsequent model).

This step locks the canonical model + schema pattern. Do NOT produce list-response wrappers, repositories, or routers — those are Steps 3.2 and 3.3.

CLAUDE_CODE step. No DB writes; no migrations; no router work.

---

## Source-of-truth column inventory (verify against the DDL)

The actual `tenants_v3.sql` columns, in DDL order. **The earlier draft of this prompt had several wrong column names; trust the DDL, not memory.**

| Column | DB type | Nullable | Notes |
|---|---|---|---|
| `id` | UUID | NOT NULL | PK; `DEFAULT uuidv7()` (DB-side; no Python-side default) |
| `name` | TEXT | NOT NULL | LENGTH 1-200 (CHECK) |
| `display_code` | TEXT | NULL | URL-friendly slug; case-insensitive UNIQUE via expression index |
| `country` | TEXT | NULL | Free-form name or abbreviation |
| `region` | `tenant_region_enum` | NOT NULL | `'US' \| 'EU'` |
| `tier` | `tenant_tier_enum` | NULL | `'ENTERPRISE' \| 'MID_MARKET' \| 'SMB' \| 'SINGLE_STORE'` |
| `industry` | `tenant_industry_enum` | NULL | `'CONVENIENCE_FUEL' \| 'CONVENIENCE' \| 'GROCERY' \| 'HYPERMART' \| 'SPECIALITY_GROCERY' \| 'ORGANIC_GROCERY'` |
| `monthly_revenue_usd` | NUMERIC(15,2) | NULL | Self-reported; paired with `monthly_revenue_as_of_date` via CHECK |
| `monthly_revenue_as_of_date` | DATE | NULL | NULL iff `monthly_revenue_usd` is NULL |
| `number_of_stores` | INTEGER | NULL | Self-reported; paired with `number_of_stores_as_of_date` via CHECK |
| `number_of_stores_as_of_date` | DATE | NULL | NULL iff `number_of_stores` is NULL |
| `primary_contact_name` | TEXT | NULL | LENGTH 1-200 when present |
| `contact_email` | TEXT | NULL | Lowercase enforced; basic shape regex |
| `status` | `tenant_status_enum` | NOT NULL | `'ONBOARDING' \| 'TRIAL' \| 'ACTIVE' \| 'SUSPENDED' \| 'TERMINATED'`; DEFAULT `'ONBOARDING'` |
| `created_at` | TIMESTAMPTZ | NOT NULL | DEFAULT NOW() |
| `created_by_user_id` | UUID | NULL | FK to `platform_users` (Pattern (a) per D-13) |
| `updated_at` | TIMESTAMPTZ | NOT NULL | DEFAULT NOW(); BEFORE-UPDATE trigger refreshes it |
| `updated_by_user_id` | UUID | NULL | FK to `platform_users` |
| `suspended_at` | TIMESTAMPTZ | NULL | Set when status → SUSPENDED |
| `suspended_by_user_id` | UUID | NULL | FK to `platform_users`; paired with `suspended_at` via CHECK |
| `terminated_at` | TIMESTAMPTZ | NULL | Set when status → TERMINATED |
| `terminated_by_user_id` | UUID | NULL | FK to `platform_users`; paired with `terminated_at` via CHECK |

**Pattern (a) note (D-13):** `tenants` is the canonical Pattern-(a) table. Audit actor columns are typed FKs to `platform_users` only; there is **no** `*_by_user_type` enum column on this table. Any code that mentions `created_by_user_type` for `tenants` is wrong.

**UUIDv7, not v4 (D-21).** The `id` PK uses UUIDv7 via the project's `uuidv7()` PL/pgSQL function, defined in `db/raw_ddl/Ithina_postgres_SQL_DDL_shared_utilities_v1.sql` and already shipped (applied at Step 1.4, wrapped into Alembic migration `ad8afd429581` at Step 1.6). It is **not** a Postgres-native function in our PG 15 deployment — FN-AB-13 tracks the eventual swap to Postgres 18's native `uuidv7()` once Cloud SQL ships it. The application side has no v7-specific dependency: SQLAlchemy and Pydantic just see Python's standard `uuid.UUID` type. **Do NOT use `uuid.uuid4`, `gen_random_uuid()`, or any v4 generator anywhere in this step's code or tests** — D-21 is explicit that mixing v4 and v7 in the same table defeats insert-locality on the canonical layer.

---

## Scope in

### File 1: `src/admin_backend/db/base.py` — new

DeclarativeBase reused by every ORM model from now on.

```python
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Project-wide SQLAlchemy DeclarativeBase. Every ORM model inherits this."""
```

Single file, one class. **Do not** set `metadata.schema` globally — schema is per-table via `__table_args__` per D-15.

### File 2: `src/admin_backend/models/__init__.py` — new

Re-export `Tenant` for ergonomics:

```python
from admin_backend.models.tenant import Tenant

__all__ = ["Tenant"]
```

### File 3: `src/admin_backend/models/tenant.py` — new

Map every column from `tenants_v3.sql` using `Mapped[...] + mapped_column(...)` (SQLAlchemy 2.x style).

Specific requirements:

- **`__tablename__ = "tenants"`**.
- **`__table_args__ = {"schema": <db_schema-from-settings>}`** per D-15. Resolve `db_schema` once at module-import time via `get_settings().db_schema`. Do NOT hardcode `"core"`. Do NOT use a literal.
- **`id` column: no Python or ORM-side default.** Per the UUIDv7 note above, the DB carries `DEFAULT uuidv7()` and the application never generates the UUID locally. No `default=uuid.uuid4`. No `server_default=text("uuidv7()")` either — the DDL already carries the DEFAULT, and declaring it a second time at the ORM layer creates a maintenance trap the day FN-AB-13 swaps the PL/pgSQL function for the Postgres 18 native.
- **Four enum columns** (`status`, `tier`, `industry`, `region`): use SQLAlchemy `Enum` with `name=<existing-pg-enum-name>`, `create_type=False` (DDL already created the type), `native_enum=True`. Define matching Python `enum.Enum` classes (str-Enum subclasses) — `TenantStatus`, `TenantTier`, `TenantIndustry`, `TenantRegion`. Place them in this same module for v0; promote to a shared module only if a future resource needs to reuse one.
- **TIMESTAMPTZ columns**: `DateTime(timezone=True)`.
- **NUMERIC**: `Numeric(15, 2)`.
- **DATE**: `Date`.
- **TEXT**: `Text`.
- **UUID**: SQLAlchemy 2.x's generic `Uuid` is acceptable; `from sqlalchemy.dialects.postgresql import UUID as PG_UUID` is also acceptable. Prefer the generic for portability unless a reason emerges.
- **Audit FKs (Pattern (a)):** the `*_by_user_id` columns are typed FKs at the DB level (`fk_tenants_*_by_user`). **Do NOT add a SQLAlchemy `ForeignKey(...)` declaration on these columns in 3.1.** A `PlatformUser` model doesn't exist until Step 5.1; declaring the FK now creates a forward-reference problem. Just type the column as `Mapped[UUID | None]` with no `ForeignKey`. Step 5.1 (or a later step) can add the SQLAlchemy `relationship(PlatformUser)` if the application needs it — for v0 read-only the relationship isn't required.
- **No CHECK constraints in the model.** They live in the DDL; the DB enforces them. Documenting them in column docstrings is optional.

### File 4: `src/admin_backend/schemas/__init__.py` — new

```python
from admin_backend.schemas.tenant import TenantRead

__all__ = ["TenantRead"]
```

### File 5: `src/admin_backend/schemas/tenant.py` — new

Pydantic v2 `TenantRead` for API responses. Skeleton:

```python
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_serializer

from admin_backend.models.tenant import (
    TenantIndustry, TenantRegion, TenantStatus, TenantTier,
)


class TenantRead(BaseModel):
    """Tenant entity as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    display_code: str | None
    country: str | None
    region: TenantRegion
    tier: TenantTier | None
    industry: TenantIndustry | None
    monthly_revenue_usd: Decimal | None
    monthly_revenue_as_of_date: date | None
    number_of_stores: int | None
    number_of_stores_as_of_date: date | None
    primary_contact_name: str | None
    contact_email: str | None
    status: TenantStatus
    created_at: datetime
    updated_at: datetime
    suspended_at: datetime | None
    terminated_at: datetime | None

    @field_serializer("monthly_revenue_usd", when_used="json")
    def _serialise_money(self, v: Decimal | None) -> str | None:
        return str(v) if v is not None else None
```

Decisions reflected:

- **Audit-actor IDs hidden.** `created_by_user_id`, `updated_by_user_id`, `suspended_by_user_id`, `terminated_by_user_id` are internal lineage; not in the API response. Frontend can render lifecycle state from `suspended_at` / `terminated_at` alone.
- **Lifecycle timestamps exposed.** `suspended_at` and `terminated_at` are exposed; the frontend uses them to render badges and timelines.
- **Enum types reused from the model.** Pydantic accepts Python `str`-Enums and serialises by value (string) — no explicit converter needed.
- **NUMERIC → string (Q11 default).** The serialiser converts `Decimal` to its decimal string representation, preserving precision. `when_used="json"` keeps `model_dump()` (Python dict) returning `Decimal` while `model_dump_json()` and `model_dump(mode="json")` emit the string.
- **No `TenantListResponse`** in this step. Defer to 3.2.

### File 6: `tests/unit/test_tenant_model.py` — new

Model-level smoke tests. No live DB connection required.

- **T1.** Import `Tenant` without errors.
- **T2.** `Tenant.__tablename__ == "tenants"`.
- **T3.** `Tenant.__table_args__["schema"]` matches `get_settings().db_schema` (D-15 wired correctly).
- **T4.** `select(Tenant).compile(dialect=postgresql.dialect())` produces SQL containing the schema-qualified `<schema>.tenants` reference.
- **T5.** The four enum columns reference the correct PG enum type names (`tenant_status_enum`, `tenant_tier_enum`, `tenant_industry_enum`, `tenant_region_enum`) and were declared with `create_type=False`. Inspect via `Tenant.__table__.c.<col>.type`.
- **T6.** `Tenant.__table__.c.id.default is None` and `Tenant.__table__.c.id.server_default is None` — no Python or ORM-level default; DB DEFAULT fires.

### File 7: `tests/unit/test_tenant_schemas.py` — new

Pydantic schema tests. Use `types.SimpleNamespace` (or a small dataclass) to fake an ORM-shaped object — no live DB needed.

- **S1.** `TenantRead.model_validate(fake_orm_obj)` succeeds with every required field populated.
- **S2.** `TenantRead(...).model_dump_json()` produces `snake_case` keys (Q1 default).
- **S3.** `monthly_revenue_usd=Decimal("1500000.50")` round-trips as JSON string `"1500000.50"` via `model_dump_json()`. Trailing zeros preserved. Verify via `model_dump()` (Python dict, mode="python") still returns a `Decimal` — `when_used="json"` keeps native Python use untouched.
- **S4.** Null fields appear as JSON `null`, not omitted.
- **S5.** `created_at` / `updated_at` / `suspended_at` / `terminated_at` serialise as ISO 8601 strings with timezone offset (e.g. `"2026-05-02T10:00:00+00:00"`).
- **S6.** Audit-actor IDs are NOT present in the dumped output even when set on the source object — confirms the hide policy.
- **S7.** Status / tier / industry / region serialise as their string values (e.g. `"ACTIVE"`, not `"TenantStatus.ACTIVE"`).

### File 8: `BUILD_PLAN.md` — status flips

- **Step 1.5: TODO → DONE.** Yesterday's drift carry-forward; the smoke test deliverable shipped at Step 2.2b but the status flag was never flipped.
- **Step 3.1: TODO → DONE.** Update the scope-in/acceptance text if the step deviated from what's currently written.

### File 9: `CLAUDE.md` — Current state + provisional-defaults entry

- **"Completed" list:** add a Step 3.1 bullet covering the `Base`, the `Tenant` model with the four typed enums, the `TenantRead` schema with NUMERIC-as-string, the audit-actor hide policy, and the test counts.
- **"Not yet completed" list:** drop 3.1 from "Steps 3.x onward".
- **New entry capturing the provisional contract defaults** (Q1 snake_case, Q4 ISO-8601-offset, Q7 nulls included, Q11 money-as-string, Q2 deferred). Two placement options — pick whichever fits cleaner:
  - (a) inline under "Current state" as a one-paragraph note tagged "Provisional, pending Step 2.0 lock with frontend developer".
  - (b) a new D-28 entry: "Provisional API response shape defaults pending Step 2.0", with a `Reconsider if` clause that points to Step 2.0's outcome.
  Lean toward (b) if any of these defaults already feels load-bearing; otherwise (a). Surface your choice and reasoning in the report.
- No new FN-AB items expected.

### File 10: `prompts/step-3_1-tenant-model-schema-2026-05-02.md`

This prompt file. Committed alongside the work per the per-step bundling convention.

---

## Testing and regression discipline

Standing rule for every step prompt: each step bundles **new tests proving the new behaviour** and a **full regression run proving nothing existing broke**. Both must be green before reporting the step done. This section is not a duplicate of Acceptance criteria; it names the *risk surface* this step introduces and the *exact commands* that prove all four legs (new tests, regression, types, environment) are green.

### New tests added by this step

Already specified in Files 6 and 7. Summary:

- **Model tests (Files 6, 6 cases):** `__tablename__`, `__table_args__["schema"]` wiring per D-15, schema-qualified SQL generation, the four PG enums with `create_type=False`, and the no-default invariant on `id` (which is what enforces UUIDv7-not-v4 at the ORM layer).
- **Schema tests (File 7, 7 cases):** ORM-mode ingestion, snake_case keys (Q1), `Decimal` → JSON string (Q11), null-not-omitted (Q7), ISO 8601 timestamps (Q4), audit-actor hide policy, and enum string-value serialisation.

Design each test so it would fail against an empty/unimplemented module *before* you write the implementation. If a new test passes against a stub, the test isn't actually testing anything.

### Regression risk surface introduced by this step

Concrete things to watch as you work, not just at the end:

1. **Alembic autogenerate.** `migrations/env.py` currently has `target_metadata = None` (Step 1.6). Do **not** change this to `Base.metadata` in 3.1. The other 9 models don't exist yet; autogenerate would propose dropping every table. The metadata wiring lands when all models are mapped, not piecemeal.
2. **`get_settings()` called at model-module import time.** Existing 57 tests don't import `models.tenant`, so they don't hit this path. The new tests do — a failed `Settings()` construction surfaces at test-collection time as an `ImportError`, not as a test failure, which is harder to debug. Run the new test files *in isolation first* (`uv run pytest tests/unit/test_tenant_model.py -v`) to catch this cleanly before the full-suite run.
3. **Pydantic `field_serializer(when_used="json")`.** Confirm the installed pydantic version honours the `when_used` kwarg. If `model_dump_json()` emits `monthly_revenue_usd` as a JSON number rather than a string, the pin is too old or the kwarg has moved. Surface; do not silently fall back to a less precise pattern.
4. **Search-path interaction.** Step 2.2a's connect-time hook sets `search_path = {db_schema}, public`. The new model uses `__table_args__["schema"]`, which produces fully-qualified SQL (`<schema>.tenants`). Both mechanisms are fine independently and fine together — the schema-qualified form bypasses search_path entirely. Flag if `select(Tenant)` compiles to bare `tenants` without the schema prefix; that would mean `__table_args__` didn't take effect.
5. **Existing engine/session tests.** None of them touch `models.tenant`, but several construct an engine and run live SQL. Confirm they still pass once `db/base.py` exists in the import graph — the import shouldn't side-effect anything, but the verification is cheap.

### Verification harness (run all four; all must be green)

```bash
# 1. Full pytest suite — new + regression in one run
uv run pytest -v

# 2. mypy strict on the new and surrounding modules
uv run mypy --strict src/admin_backend/models src/admin_backend/schemas src/admin_backend/db

# 3. Pre-flight checker (also catches dep drift, missing tools, etc.)
./scripts/check_setup.sh

# 4. Smoke: model imports and compiles to schema-qualified SQL
uv run python -c "from admin_backend.models.tenant import Tenant; from sqlalchemy import select; from sqlalchemy.dialects import postgresql; print(select(Tenant).compile(dialect=postgresql.dialect()))"
```

Expected outcome: ~13 new + 57 existing = ~70 pytest passes; mypy clean; check_setup 35/35; the smoke command prints a SELECT qualified by the configured `db_schema` (not bare `tenants`).

If any of the four is not green, **report the failure rather than the step**. Don't ship a step with one leg of the harness dropped or skipped.

---

## Scope out

- **`TenantsRepo` / repository class.** Step 3.2.
- **Router and endpoints.** Step 3.3.
- **List-response wrapping (`TenantListResponse`, `Pagination`).** Step 3.2 / 3.3, when the consumer exists.
- **Other resources (stores, users, etc.).** Steps 4.5, 5.x, 6.x. They reuse this step's pattern.
- **Write schemas (`TenantCreate`, `TenantUpdate`).** Post-v0 per FN-AB-12.
- **Relationships to `PlatformUser`.** Step 5.1 lands the `PlatformUser` model; relationships can be added when both ends exist.
- **Permission / RBAC fields on `TenantRead`.** Not part of the tenant entity.

---

## Stop and ask if

- A column in `tenants_v3.sql` doesn't have an obvious Python type mapping. The table above should cover everything — surface anything ambiguous.
- The `Base` location (`db/base.py`) conflicts with anything Step 2.2a/2.3 wired (it shouldn't — `db/engine.py` and `db/session.py` don't define a Base — but verify before creating).
- `__table_args__ = {"schema": get_settings().db_schema}` evaluating at module-import time creates a problem with how tests construct Settings (the schema gets pinned to the value at first import). For v0 single-process tests this should be fine; if the test suite needs to vary `db_schema` per-test, surface and we'll discuss whether a `metadata`-level schema or per-process re-import is the right move.
- The four DB enums don't map cleanly to SQLAlchemy `Enum` with `create_type=False` (e.g., a Postgres-side enum value is missing from your Python enum, or vice versa). Surface and we'll reconcile.
- Pydantic v2's `field_serializer(when_used="json")` doesn't behave as expected for `Decimal` — e.g., `model_dump_json()` still emits a JSON number rather than a string. Surface; we'll triage. (Pydantic v2.x has had subtle changes around `when_used`; if your installed version doesn't honour it, use `mode="json"` branching inside the serialiser instead.)
- The "provisional defaults" placement (CLAUDE.md inline note vs new D-28) — if either feels forced, surface before committing.

---

## Acceptance criteria

- 10 files created/modified per the bundle above.
- All new tests pass: target ~6 model + ~7 schema = ~13 tests.
- All existing 57 tests (Steps 2.1, 2.2a, 2.2b, 2.3, 2.4) still pass — no regressions.
- mypy strict clean: `uv run mypy --strict src/admin_backend/models src/admin_backend/schemas src/admin_backend/db`.
- `check_setup.sh` 35/35.
- `python -c "from admin_backend.models.tenant import Tenant; from sqlalchemy import select; from sqlalchemy.dialects import postgresql; print(select(Tenant).compile(dialect=postgresql.dialect()))"` produces a SELECT compiling to the correct schema-qualified table reference.
- Sample `TenantRead(...).model_dump_json(indent=2)` output included in the report (visual confirmation of the response shape).

---

## Report (BEFORE proposing commit)

Per the per-step bundling convention, four bundles, enumerated explicitly:

1. **Code/tests:** all files created/modified with line counts; the sample `TenantRead.model_dump_json(indent=2)` output for visual confirmation; the compiled `select(Tenant)` SQL string.
2. **CLAUDE.md updates:** Current state Completed/Not-yet-completed updates; the provisional-defaults entry (which placement chosen and why); any conventions clarified during the work.
3. **BUILD_PLAN.md updates:** Step 1.5 status flip (carry-forward from yesterday's drift); Step 3.1 status flip + scope-in correction if the step deviated.
4. **Prompt file:** `prompts/step-3_1-tenant-model-schema-2026-05-02.md` confirmed in commit set.

Plus: test results (~13 new + 57 existing = ~70 total expected), mypy status, check_setup status.

Wait for explicit authorisation before staging or committing.

---

## End of prompt
