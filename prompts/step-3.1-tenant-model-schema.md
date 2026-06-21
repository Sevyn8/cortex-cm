# Prompt — Step 3.1: Tenant ORM model + Pydantic Read schema

> Paste this entire block into a fresh Claude Code session when starting Step 3.1.

---

## Pre-flight

Before doing any work:

1. Run `./scripts/check_setup.sh`. If any check fails, stop and report.
2. Read `CLAUDE.md` fully — focus on schema reference, code naming, and "Code conventions and structure".
3. Read `docs/architecture.md` "Schema and storage" section.
4. Read `docs/api-contract.md` Q1, Q2, Q4, Q7, Q11 (response naming, wrapping, dates, nulls, NUMERIC serialisation). **These decisions drive every subsequent schema in the project.**
5. Read `db/raw_ddl/Ithina_postgres_SQL_DDL_tenants_v3.sql` fully. This is the source of truth.
6. Read `BUILD_PLAN.md` Step 3.1 in full.
7. Read this prompt fully and confirm scope.

---

## Step ID and intent

**Step 3.1** — Tenant ORM model + Pydantic Read schema.

Create the SQLAlchemy ORM model for the `tenants` table and the Pydantic `TenantRead` schema for the API response. **This step locks the pattern that all subsequent resources will follow** (stores, platform_users, tenant_users, org_nodes, RBAC, audit_logs).

Decisions made here propagate. Get the response field naming, NUMERIC serialisation, null handling, and date format right; they apply to every later resource.

This is a CLAUDE_CODE step. Schema layer; no router yet.

---

## Scope in

### File 1: `src/admin_backend/models/__init__.py`

Empty or re-export `Tenant` for convenient imports.

### File 2: `src/admin_backend/models/tenant.py`

SQLAlchemy 2.x ORM model using `Mapped` + `mapped_column` style.

Map every column from `Ithina_postgres_SQL_DDL_tenants_v3.sql`:

```python
class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String, nullable=False)
    legal_name: Mapped[str | None] = mapped_column(String, nullable=True)
    country: Mapped[str | None] = mapped_column(String, nullable=True)
    tier: Mapped[str | None] = mapped_column(String, nullable=True)
    industry: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    primary_contact_name: Mapped[str | None] = mapped_column(String, nullable=True)
    primary_contact_email: Mapped[str | None] = mapped_column(String, nullable=True)
    monthly_revenue_usd: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)
    num_stores: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # ... plus audit columns: created_by_user_id, created_by_user_type, etc.
    # ... plus terminated_at if it exists in the DDL.
```

Verify column list against the DDL. **Do not modify the DDL.** If the DDL has columns the model is missing, add them. If the DDL has constraints the model needs to know about (e.g., status_enum), reflect them as Python `Literal` types in the type hints where appropriate, but the actual constraint is enforced by Postgres.

Use `Base` from `src/admin_backend/db/base.py`. If `db/base.py` doesn't exist yet (depends on Step 2.2 ordering), create it:

```python
# src/admin_backend/db/base.py
from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    pass
```

### File 3: `src/admin_backend/schemas/__init__.py`

Empty or re-export `TenantRead`, `TenantListResponse`, `Pagination`.

### File 4: `src/admin_backend/schemas/common.py`

Shared schemas used by all resources.

```python
class Pagination(BaseModel):
    """Pagination metadata for list responses."""
    limit: int
    offset: int
    total: int
    has_more: bool
```

If `docs/api-contract.md` Q2 locked a different wrapping shape (e.g., `data` vs `items`), use that. Recommendation: `items` + `pagination` per the example doc.

### File 5: `src/admin_backend/schemas/tenant.py`

Pydantic schemas for tenant.

Apply locked API contract decisions:

- **Q1 (naming):** snake_case field names by default. If contract locks camelCase, use `Field(..., alias=...)` and `model_config = ConfigDict(populate_by_name=True, by_alias=True)`.
- **Q4 (dates):** ISO 8601 UTC. Pydantic v2 default for `datetime` serialisation produces ISO 8601 with timezone offset. If contract locks Z-suffix UTC explicitly, use a custom serialiser.
- **Q7 (nulls):** include nullable fields explicitly (do not omit). Pydantic default behaviour. Confirm via `model_dump(exclude_none=False)`.
- **Q11 (NUMERIC):** monetary fields (e.g., `monthly_revenue_usd`) serialise as **string** to preserve precision.

```python
from decimal import Decimal
from pydantic import BaseModel, ConfigDict, Field, field_serializer

class TenantRead(BaseModel):
    """Tenant entity as returned by the API."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(..., description="Tenant identifier")
    name: str = Field(..., description="Display name")
    legal_name: str | None = Field(None, description="Legal/registered name")
    country: str | None = Field(None, description="Country (free-form text in v0)")
    tier: str | None = Field(None, description="Commercial tier code")
    industry: str | None = Field(None, description="Industry code")
    status: str = Field(..., description="Lifecycle status")
    primary_contact_name: str | None
    primary_contact_email: str | None
    monthly_revenue_usd: Decimal | None = Field(
        None, description="Monthly revenue in USD; serialised as string for precision"
    )
    num_stores: int | None
    created_at: datetime
    updated_at: datetime

    @field_serializer("monthly_revenue_usd")
    def serialise_money(self, v: Decimal | None) -> str | None:
        return str(v) if v is not None else None


class TenantListResponse(BaseModel):
    items: list[TenantRead]
    pagination: Pagination
```

Notes:

- `from_attributes=True` lets Pydantic build from SQLAlchemy ORM objects directly.
- Use `Field(..., description=...)` for fields that need OpenAPI descriptions. Dropping the description for trivial fields is fine.
- If contract Q1 locks camelCase, add aliases: `Field(..., alias="legalName")` etc.

### File 6: `src/admin_backend/schemas/types.py`

Reusable type aliases for repeated patterns. Optional for this step, but recommended:

```python
from typing import Annotated
from decimal import Decimal
from pydantic import PlainSerializer

MoneyAmount = Annotated[
    Decimal,
    PlainSerializer(lambda v: str(v) if v is not None else None, return_type=str | None),
]
```

Then in tenant.py: `monthly_revenue_usd: MoneyAmount | None`. Keeps schemas DRY for monetary fields across resources.

### File 7: Tests

`tests/unit/test_tenant_schemas.py`. Cover:

- `Tenant` ORM imports and `select(Tenant)` produces valid SQL (smoke).
- `TenantRead.model_validate(orm_obj)` works given a fake ORM-like object.
- `TenantRead(...).model_dump()` produces expected JSON shape.
- `monthly_revenue_usd=Decimal("1500000.50")` serialises as string `"1500000.50"`.
- Null fields appear as `null` in JSON, not omitted.
- Datetime fields serialise as ISO 8601 strings.
- Field naming matches contract decision (snake_case or camelCase as locked).

Use a small helper to construct ORM-like objects without needing a real DB session:

```python
from types import SimpleNamespace
fake_tenant = SimpleNamespace(
    id=uuid.uuid4(),
    name="Acme",
    monthly_revenue_usd=Decimal("1500000.50"),
    ...
)
TenantRead.model_validate(fake_tenant)
```

---

## Scope out

- Repository class (`TenantsRepo`) — Step 3.2.
- Router and endpoints — Step 3.3.
- Other resources (stores, users, etc.) — they follow the pattern this step establishes.
- Write schemas (`TenantCreate`, `TenantUpdate`) — post-v0.

---

## Implementation hints

### Verify against the DDL

Cross-check the column list. The DDL is the source of truth. Common fields to look for and verify:

- `id`, `name`, `legal_name`, `country`, `tier`, `industry`, `status`
- `primary_contact_name`, `primary_contact_email`
- `monthly_revenue_usd`, `num_stores`
- Audit columns: `created_at`, `created_by_user_id`, `created_by_user_type`, `updated_at`, `updated_by_user_id`, `updated_by_user_type`
- Lifecycle: `terminated_at` (nullable)
- Possibly: notes, metadata JSONB, etc.

If the DDL has a column the prompt's scope didn't anticipate (e.g., a `tags` array, a `metadata_jsonb` JSONB), include it in the model but flag it. The Pydantic schema may or may not expose it — for v0 internal-use columns, hide from `TenantRead`.

### NUMERIC handling

`monthly_revenue_usd` is `NUMERIC(15, 2)`. SQLAlchemy maps to Python `Decimal`. Pydantic default for `Decimal` is JSON number, which loses precision in JS for very large values. Per `docs/api-contract.md` Q11 recommendation, serialise as string.

### Audit columns in the API response

Decision: do NOT expose audit columns (`created_by_user_id`, `updated_by_user_id`, etc.) in `TenantRead`. They're internal lineage; not part of the API contract. Only `created_at` and `updated_at` are exposed.

If you change this decision, document the reasoning in CLAUDE.md.

### Status as Literal vs str

Could type `status` as `Literal["ACTIVE", "SUSPENDED", "TRIAL", "TERMINATED"]` for stricter type-checking. Pydantic v2 handles this well. But: future status additions require code changes. For v0, recommend `str` with a description noting valid values, OR a Python `Enum` mirrored from the DDL.

If you go with Enum, keep it in `src/admin_backend/schemas/enums.py` shared across resources.

---

## Acceptance criteria

- All files created or modified per scope above.
- `Tenant` model imports without errors.
- `select(Tenant)` produces valid SQL when run against the local DB.
- `TenantRead.model_validate(orm_obj)` produces correct JSON.
- All schema-naming, NUMERIC serialisation, date format, and null behaviour matches `docs/api-contract.md` decisions.
- All unit tests pass.
- mypy strict clean: `uv run mypy --strict src/admin_backend/models src/admin_backend/schemas`.

---

## Stop and ask if

- A column in the DDL doesn't have an obvious Python type mapping.
- `docs/api-contract.md` has a TBD on a question that affects this step's output (e.g., Q1 not yet locked). In that case: implement with the recommended default and flag it; or stop and ask user to lock the decision first.
- Audit columns: the user wants them exposed in the API response. Default decision is hide; flag if uncertain.
- The DDL has columns not in the prompt's anticipated list — list them and confirm whether to include in TenantRead.

---

## What to report at end

- Files created/modified.
- Confirmation that the schema response shape matches `docs/api-contract.md` locked decisions (cite specific Q numbers).
- Any DDL columns flagged for inclusion/exclusion.
- Test counts.
- Sample `model_dump_json(indent=2)` output of a populated `TenantRead` instance, for visual confirmation.

---

## After completing

Propose a git commit per CLAUDE.md "After completing a task" Pattern A:

```
git status
git add -A
git commit -m "Step 3.1: Tenant ORM model + Pydantic Read schema

- Tenant SQLAlchemy 2.x ORM model maps tenants_v3.sql
- TenantRead Pydantic schema for API responses
- TenantListResponse with Pagination shared schema
- MoneyAmount type alias for NUMERIC string serialisation
- Pattern locked for all subsequent resources
- N unit tests covering serialisation, null handling, money precision"
```

Ask user "Run? yes / no / edit message".

---

## End of prompt
