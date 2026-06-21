# Prompt — Step 3.6: Lookups batch endpoint + seed data extension

> Generated 2026-05-03. Revised 2026-05-03 (v2: hardened naming via explicit Pre-flight verification of existing code; country-codes Stop-and-ask; tightened downgrade semantics; OpenAPI quality bar; response-envelope note).
> Paste this entire block into a fresh Claude Code session to start Step 3.6.
> Single endpoint that returns all dropdown values for the tenants UI in one call. Plus migration that seeds 22 rows into `lookups` for the 5 missing categories. Unblocks Amit's frontend integration of the tenants list page (filters: tier, region, status, industry, country) plus the existing module_code lookup.

---

## Pre-flight

1. Run `./scripts/check_setup.sh`. Expect 35/35.
2. `git log --oneline -5` — confirm Step 3.5 at HEAD (most recent commit). Step 3.4.5's migration `cd2a02e452ae` should be the latest in the alembic chain.
3. `uv run alembic heads` — confirm output is `cd2a02e452ae (head)`.
4. Read `CLAUDE.md` fully. Focus on:
   - **D-15** — DB_SCHEMA from environment.
   - **D-21** — UUIDv7 (lookups uses it for `id`; the migration's INSERT statements omit `id` and let DEFAULT fire).
   - **D-29** — PLATFORM RLS visibility (lookups has NO RLS; it's platform reference data, accessible to all sessions).
   - **D-30** — list-only response envelope (`{items, ...}` for lists; bare object for single resources).
   - **D-31** — response field semantics are append-only.
   - "Note on PG enum columns" subsection.
   - "Note on seed Excel shape" subsection (the convention you captured at Step 3.5).
5. Read `db/raw_ddl/Ithina_postgres_SQL_DDL_lookups_v1.sql` — column shape, constraints, list_name format (`^[a-z][a-z0-9_]*$`), code format (UPPER_SNAKE_CASE).
6. Read the Step 3.4.5 migration `migrations/versions/cd2a02e452ae_*.py` — it added the `module_code` rows to lookups. Mirror its INSERT pattern for the 5 new categories.
7. Read `src/admin_backend/models/lookup.py`. **Confirm the exact column attribute names**: the prompt's repo, schema, and tests assume `Lookup.list_name`, `Lookup.code`, `Lookup.display_name`, `Lookup.display_order`, `Lookup.is_active`. If any drift (e.g., `Lookup.name` instead of `Lookup.display_name`), surface and we'll adjust the prompt's code. **Do not silently substitute** — the integration tests reference these names by string in JSON assertions too.
8. Read `src/admin_backend/repositories/tenants.py`. The new `LookupsRepo` MUST mirror exactly: (a) the constructor signature (probably `__init__(self, session: AsyncSession)`), (b) how methods access `self._session` or equivalent, (c) the import paths for SQLAlchemy primitives. Whatever pattern `TenantsRepo` uses, `LookupsRepo` uses identically. The skeleton in File 2 below uses placeholders; replace before writing.
9. Read `src/admin_backend/routers/v1/tenants.py`. The new lookups router MUST mirror exactly: (a) the import line for the auth dependency (the prompt's File 4 placeholder `from admin_backend.auth.deps import require_auth` is almost certainly wrong; copy the actual import), (b) the import line for the session dependency (placeholder `from admin_backend.db.deps import get_session` is also a guess; copy actual), (c) the `Depends(...)` invocations, (d) the response_model declarations, (e) the URL prefix style. **For the session dependency: lookups uses the same dependency as tenants (likely `get_tenant_session`)**. Lookups doesn't need RLS GUCs (no RLS on lookups), but using a *different* session-getter creates inconsistency. One pattern across all routers.
10. Read `tests/integration/test_tenants_router.py`. The new lookups tests MUST mirror exactly: (a) the `client` fixture name (might be `client`, `async_client`, `httpx_client`), (b) the JWT fixture name (might be `platform_jwt`, `platform_token`, or generated inline via `make_test_jwt(...)`), (c) the headers pattern, (d) the response.json() unwrapping convention. Test fixtures are unforgiving — wrong name = collection failure, not a test error.
11. Read `src/admin_backend/schemas/` directory listing. Find an existing schema file (probably `tenants.py`) and confirm the file naming convention, the Pydantic version (v1 vs v2 — v2 uses `model_config`, v1 uses `Config` class), and import patterns. Mirror exactly.
12. Read `BUILD_PLAN.md` Steps 3.5 and any "next" markers; the new Step 3.6 entry slots between Step 3.5 and the next-numbered step.
13. Read this prompt fully.

---

## Step ID and intent

**Step 3.6** — Lookups batch endpoint. Single deliverable: `GET /api/v1/lookups?lists=tier,region,status,industry,country,module_code` returns a map of `{list_name: [item, item, ...]}` so the frontend loads all dropdown content in one request.

Five concrete deliverables:

1. **Alembic migration** seeding 22 rows into `lookups` for 5 new categories (`tenant_tier`, `tenant_region`, `tenant_status`, `tenant_industry`, `country`).
2. **Repository method** `LookupsRepo.get_lists_batch(list_names: list[str])` returning a dict keyed by list_name.
3. **Router** `GET /api/v1/lookups` with single query param `lists` (comma-separated).
4. **Schemas** for the response envelope.
5. **Integration tests** covering the four expected behaviours (success, list_name validation, missing list_name in DB, no lists requested).

CLAUDE_CODE step. No DDL changes (lookups table already exists). No schema impact. No new ORM models. Mostly leveraging Step 3.4.5's Lookup ORM and the Step 3.3 router pattern.

---

## Source-of-truth specification

### File 1: Alembic migration `migrations/versions/<rev>_step_3_6_lookups_seed.py` — new

**Stop-and-ask before writing:** the `country` lookup codes seeded below match the dev seed Excel's column values verbatim (`USA`, `UK`, `Canada`, `France`, `Poland`) — NOT ISO 3166 alpha-3 codes. Reasoning: frontend filter values must equal the column values they filter, and the seed Excel uses these literals. Switching to ISO codes (`USA` → still `USA`, `UK` → `GBR`, `Canada` → `CAN`, etc.) would require re-seeding tenants and stores in lock step.

For v0 with one seed source, the literal-match approach is safe. Surface to the user before applying this migration if anything about this trade-off feels wrong; otherwise proceed as-written.

Generate via:
```bash
uv run alembic revision -m "step_3_6_lookups_seed"
```

`down_revision = "cd2a02e452ae"` (Step 3.4.5's revision; Step 3.5 didn't add a migration).

The migration seeds 22 rows. **Schema-qualified (`core.lookups`) per the precedent in Step 3.4.5.** No DDL changes.

```python
"""step_3_6_lookups_seed

Seeds lookup categories for the tenants UI dropdowns:
- tenant_tier (4 rows)
- tenant_region (2 rows)
- tenant_status (5 rows)
- tenant_industry (6 rows)
- country (5 rows — the 5 countries in the dev seed)

module_code is already seeded by cd2a02e452ae (Step 3.4.5); not
re-seeded here.

Display names match the convention used at Step 3.4.5 — title case
for natural words, ALL CAPS only when the original is an acronym
(SMB, EU). display_order is sequential within each list_name; tested
visually for sensible ordering.

The Excel seed (Step 3.5) uses 'USA', 'UK', 'Canada', 'France',
'Poland' verbatim in the country column. The country lookup codes
match these literals so frontend dropdowns and backend filter values
align.
"""
from alembic import op


revision = "<auto-generated>"
down_revision = "cd2a02e452ae"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO core.lookups (list_name, code, display_name, display_order, is_active)
        VALUES
            -- tenant_tier
            ('tenant_tier', 'ENTERPRISE',   'Enterprise',    1, TRUE),
            ('tenant_tier', 'MID_MARKET',   'Mid-Market',    2, TRUE),
            ('tenant_tier', 'SMB',          'SMB',           3, TRUE),
            ('tenant_tier', 'SINGLE_STORE', 'Single Store',  4, TRUE),

            -- tenant_region
            ('tenant_region', 'US', 'United States',  1, TRUE),
            ('tenant_region', 'EU', 'European Union', 2, TRUE),

            -- tenant_status
            ('tenant_status', 'ONBOARDING', 'Onboarding', 1, TRUE),
            ('tenant_status', 'TRIAL',      'Trial',      2, TRUE),
            ('tenant_status', 'ACTIVE',     'Active',     3, TRUE),
            ('tenant_status', 'SUSPENDED',  'Suspended',  4, TRUE),
            ('tenant_status', 'TERMINATED', 'Terminated', 5, TRUE),

            -- tenant_industry
            ('tenant_industry', 'CONVENIENCE_FUEL',   'Convenience & Fuel',     1, TRUE),
            ('tenant_industry', 'CONVENIENCE',        'Convenience',            2, TRUE),
            ('tenant_industry', 'GROCERY',            'Grocery',                3, TRUE),
            ('tenant_industry', 'HYPERMART',          'Hypermart',              4, TRUE),
            ('tenant_industry', 'SPECIALITY_GROCERY', 'Speciality Grocery',     5, TRUE),
            ('tenant_industry', 'ORGANIC_GROCERY',    'Organic Grocery',        6, TRUE),

            -- country (matching the dev seed's 5 countries verbatim)
            ('country', 'USA',    'United States',  1, TRUE),
            ('country', 'UK',     'United Kingdom', 2, TRUE),
            ('country', 'Canada', 'Canada',         3, TRUE),
            ('country', 'France', 'France',         4, TRUE),
            ('country', 'Poland', 'Poland',         5, TRUE)
    """)


def downgrade() -> None:
    # Delete only the rows this migration inserted, by explicit
    # (list_name, code) pairs. Looser DELETE WHERE list_name IN (...)
    # would also delete rows added later in the same list_names by
    # other migrations or manual edits. Explicit pairs preserve
    # downgrade safety even if someone adds new lookup values
    # before downgrading.
    op.execute("""
        DELETE FROM core.lookups
        WHERE (list_name, code) IN (
            ('tenant_tier', 'ENTERPRISE'),
            ('tenant_tier', 'MID_MARKET'),
            ('tenant_tier', 'SMB'),
            ('tenant_tier', 'SINGLE_STORE'),
            ('tenant_region', 'US'),
            ('tenant_region', 'EU'),
            ('tenant_status', 'ONBOARDING'),
            ('tenant_status', 'TRIAL'),
            ('tenant_status', 'ACTIVE'),
            ('tenant_status', 'SUSPENDED'),
            ('tenant_status', 'TERMINATED'),
            ('tenant_industry', 'CONVENIENCE_FUEL'),
            ('tenant_industry', 'CONVENIENCE'),
            ('tenant_industry', 'GROCERY'),
            ('tenant_industry', 'HYPERMART'),
            ('tenant_industry', 'SPECIALITY_GROCERY'),
            ('tenant_industry', 'ORGANIC_GROCERY'),
            ('country', 'USA'),
            ('country', 'UK'),
            ('country', 'Canada'),
            ('country', 'France'),
            ('country', 'Poland')
        )
    """)
```

Note: the `country` list_name uses the literal country strings (`USA`, `UK`, `Canada`, `France`, `Poland`) as codes because that's what the seed Excel populates in the `country` column. If frontend filters need to match backend rows, the codes must equal the column values. NOT ISO 3166 alpha-3 codes — those would require a re-seed of tenants/stores too.

### File 2: `src/admin_backend/repositories/lookups.py` — new

**The skeleton below uses placeholder import and constructor patterns. Before writing, complete Pre-flight item 8 (read `repositories/tenants.py`) and replace the placeholders with the actual conventions used by `TenantsRepo`.**

```python
"""Repository for lookups: read-only access to the lookups table.

Returns dropdown data for frontend forms. PLATFORM-only access since
lookups is platform reference data (no RLS; same access as roles,
permissions).
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from admin_backend.models import Lookup


class LookupsRepo:
    """Read-only repo for lookup categories."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_lists_batch(
        self, list_names: list[str]
    ) -> dict[str, list[Lookup]]:
        """Return a map of list_name -> list of Lookup rows.

        Each list is filtered to is_active=True and sorted by
        display_order ascending. List names not present in the
        database simply return an empty list (caller decides
        whether that's an error or just "no data yet").
        """
        if not list_names:
            return {}

        stmt = (
            select(Lookup)
            .where(
                Lookup.list_name.in_(list_names),
                Lookup.is_active.is_(True),
            )
            .order_by(Lookup.list_name, Lookup.display_order)
        )
        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())

        # Group by list_name; ensure every requested list_name has
        # an entry (empty list if no rows). Keeps response shape
        # predictable for the frontend.
        grouped: dict[str, list[Lookup]] = {name: [] for name in list_names}
        for row in rows:
            grouped[row.list_name].append(row)
        return grouped
```

### File 3: `src/admin_backend/schemas/lookups.py` — new

```python
"""Pydantic schemas for the lookups endpoint."""
from __future__ import annotations

from pydantic import BaseModel, Field


class LookupItem(BaseModel):
    """Single row from the lookups table."""
    code: str
    display_name: str
    display_order: int


class LookupsBatchResponse(BaseModel):
    """Response shape for GET /api/v1/lookups.

    A map keyed by list_name. Each value is a list of items sorted
    by display_order ascending. List_names requested but absent from
    the DB return an empty list (predictable shape — frontend can
    iterate without nullchecks).
    """
    lookups: dict[str, list[LookupItem]] = Field(
        default_factory=dict,
        description="Map of list_name -> list of {code, display_name, display_order}",
    )
```

### File 4: `src/admin_backend/routers/v1/lookups.py` — new

**The skeleton below uses placeholder import paths (`auth.deps.require_auth`, `db.deps.get_session`). Before writing, complete Pre-flight item 9 (read `routers/v1/tenants.py`) and replace ALL imports + dependency names with the actual conventions used by the existing tenants router. Do not assume any name in this skeleton is correct.**

```python
"""Router for GET /api/v1/lookups.

Single batch endpoint: query param 'lists' is comma-separated list
of list_names. Returns a map keyed by list_name. Each map value is
a sorted list of {code, display_name, display_order}.

Auth: standard JWT-required (any user_type accepted; lookups are
platform reference data, no tenant scoping).

Why batch: Amit's frontend tenant-list page renders 6 dropdowns
(tier, region, status, industry, country, module_code). Batch
endpoint = 1 request instead of 6. Better latency, less code in
the page.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from admin_backend.auth.context import AuthContext
from admin_backend.auth.deps import require_auth
from admin_backend.db.deps import get_session
from admin_backend.repositories.lookups import LookupsRepo
from admin_backend.schemas.lookups import LookupItem, LookupsBatchResponse


router = APIRouter(prefix="/lookups", tags=["lookups"])


@router.get(
    "",
    response_model=LookupsBatchResponse,
    summary="Batch lookup values for dropdowns",
)
async def get_lookups_batch(
    lists: str = Query(
        ...,
        description=(
            "Comma-separated list_names (e.g., "
            "'tenant_tier,tenant_region,tenant_status,tenant_industry,country,module_code')"
        ),
        examples=[
            "tenant_tier,tenant_region,tenant_status,tenant_industry,country,module_code"
        ],
    ),
    auth: AuthContext = Depends(require_auth),
    session: AsyncSession = Depends(get_session),
) -> LookupsBatchResponse:
    """Return lookup items for each requested list_name.

    Response shape:
        {
          "lookups": {
            "tenant_tier": [
              {"code": "ENTERPRISE", "display_name": "Enterprise", "display_order": 1},
              ...
            ],
            "tenant_region": [...],
            ...
          }
        }

    list_names not present in the lookups table return as empty
    arrays (predictable shape, no NPEs in the frontend).
    """
    list_names = [n.strip() for n in lists.split(",") if n.strip()]
    repo = LookupsRepo(session)
    grouped = await repo.get_lists_batch(list_names)

    return LookupsBatchResponse(
        lookups={
            name: [
                LookupItem(
                    code=row.code,
                    display_name=row.display_name,
                    display_order=row.display_order,
                )
                for row in rows
            ]
            for name, rows in grouped.items()
        }
    )
```

The exact import paths for `auth.deps`, `db.deps`, and the auth dependency name (`require_auth` vs `get_auth_context`) might differ — check the existing `routers/v1/tenants.py` imports and mirror them exactly.

### File 5: `src/admin_backend/routers/v1/__init__.py` — modify

Wire the new router into the v1 prefix. Mirror however `tenants` router is included.

```python
# After existing tenants_router include
from admin_backend.routers.v1 import lookups
v1_router.include_router(lookups.router)
```

### File 6: `tests/integration/test_lookups_router.py` — new

**The skeleton below uses placeholder fixture names (`client`, `platform_jwt`). Before writing, complete Pre-flight item 10 (read `tests/integration/test_tenants_router.py`) and replace fixture names with whatever the existing tests use. The four assertion patterns (success, unknown list, no auth, empty lists) stay; the fixture machinery may need renaming.**

Four integration tests covering the four expected behaviours.

```python
"""Integration tests for GET /api/v1/lookups."""
import pytest


@pytest.mark.asyncio
async def test_get_lookups_returns_all_requested_lists(client, platform_jwt):
    """All 6 categories return their seeded rows in display_order."""
    response = await client.get(
        "/api/v1/lookups",
        params={
            "lists": "tenant_tier,tenant_region,tenant_status,tenant_industry,country,module_code"
        },
        headers={"Authorization": f"Bearer {platform_jwt}"},
    )
    assert response.status_code == 200
    body = response.json()
    lookups = body["lookups"]

    # All 6 lists present
    assert set(lookups.keys()) == {
        "tenant_tier", "tenant_region", "tenant_status",
        "tenant_industry", "country", "module_code",
    }

    # Tier has 4 rows, sorted
    tiers = lookups["tenant_tier"]
    assert len(tiers) == 4
    assert [t["code"] for t in tiers] == [
        "ENTERPRISE", "MID_MARKET", "SMB", "SINGLE_STORE",
    ]
    assert tiers[0]["display_name"] == "Enterprise"

    # Region has 2 rows
    assert len(lookups["tenant_region"]) == 2

    # Module_code from Step 3.4.5 (still works)
    assert len(lookups["module_code"]) == 6


@pytest.mark.asyncio
async def test_get_lookups_returns_empty_array_for_unknown_list(client, platform_jwt):
    """Requesting a list_name not in the DB returns an empty array
    for that key. Predictable shape; frontend doesn't need null checks."""
    response = await client.get(
        "/api/v1/lookups",
        params={"lists": "tenant_tier,not_a_real_list"},
        headers={"Authorization": f"Bearer {platform_jwt}"},
    )
    assert response.status_code == 200
    lookups = response.json()["lookups"]
    assert "tenant_tier" in lookups
    assert "not_a_real_list" in lookups
    assert lookups["not_a_real_list"] == []


@pytest.mark.asyncio
async def test_get_lookups_requires_auth(client):
    """No JWT = 401 (or whatever the project's no-auth status is)."""
    response = await client.get(
        "/api/v1/lookups",
        params={"lists": "tenant_tier"},
    )
    assert response.status_code in {401, 403}


@pytest.mark.asyncio
async def test_get_lookups_handles_empty_lists_param_gracefully(client, platform_jwt):
    """Whitespace/empty list_names are filtered; lists='' or
    lists='   ' returns empty lookups dict (200, not 422)."""
    response = await client.get(
        "/api/v1/lookups",
        params={"lists": "  ,  ,  "},
        headers={"Authorization": f"Bearer {platform_jwt}"},
    )
    assert response.status_code == 200
    assert response.json()["lookups"] == {}
```

The fourth test exists because `?lists=` (empty) might be the natural way a JS framework serialises "no lists chosen yet" — better to handle gracefully than to surface a 422.

### File 7: `BUILD_PLAN.md` — modify

Insert Step 3.6 between Step 3.5 and Day 4 / Step 4.x. Status DONE in same edit.

```markdown
### Step 3.6 — Lookups batch endpoint + seed data extension

**Status:** DONE

GET /api/v1/lookups?lists=... returns a map of {list_name: [items]}
for frontend dropdowns. Single batch endpoint (1 request to load all
6 dropdowns on the tenants list page rather than 6 sequential).

**Migration** (after cd2a02e452ae): seeds 22 rows into core.lookups
for tenant_tier, tenant_region, tenant_status, tenant_industry, and
country. module_code already seeded at Step 3.4.5.

**Why now:** Amit's first-integration tenants page needs filter
dropdowns; without lookups, frontend would have to hardcode enum
values (couples frontend to backend schema, breaks D-31's
append-only contract).

**Endpoint shape:** ?lists=tier,region,... → {lookups: {tier:[...],
region:[...]}}. Unknown list_names return empty arrays (predictable
shape).

**Auth:** standard JWT, any user_type. Lookups are platform reference
data, no tenant scoping.
```

### File 8: `CLAUDE.md` — modify

- **Current state → Completed:** Step 3.6 bullet covering the migration, the repo, the router, the schema, the four tests, and "frontend integration unblocked for tenants list page filters."
- **Schema state line:** lookups row count grows from 6 to 28; no other schema changes.
- **Append a one-line response-envelope convention note** in the Code conventions section:
  > Step 3.6's response shape is `{lookups: {list_name: [items]}}` — an envelope-wrapped map, not a bare top-level map. D-30 (list-only envelope) doesn't directly apply since this isn't a list response, but the wrapping is intentional: it leaves room for metadata (e.g., `cached_at`, `version`) at top level later without breaking the contract. **Future batch-by-key endpoints follow this same envelope pattern; do not return a bare map at top level.**
- **No new D-XX entries** unless the response-envelope note grows large enough to warrant one (it doesn't — keep as a convention note alongside the existing PG_ENUM and seed-Excel notes).
- **No new FN-AB entries.**

### File 9: `prompts/step-3_6-lookups-batch-endpoint-2026-05-03.md` — new

This prompt file. Bundled per the per-step convention.

### File 10: `docs/openapi.json` — re-export

After all code is in and tests pass, regenerate the OpenAPI spec snapshot:

```bash
# Server must be running for this
curl -s http://localhost:8000/api/v1/openapi.json | jq '.' > docs/openapi.json
```

This is the deliverable to Amit. The new `/api/v1/lookups` endpoint with its full schema appears in the spec, his Claude Code consumes it, generates the matching frontend client.

If the server isn't running, start it:
```bash
uv run uvicorn admin_backend.main:app --reload &
```

---

## Testing and regression discipline

### New tests added by this step

Four integration tests in `tests/integration/test_lookups_router.py` (specified above):

1. All 6 lists return their seeded rows in display_order
2. Unknown list_name returns empty array
3. No JWT = 401/403
4. Empty/whitespace lists param returns empty dict

### Tests deliberately not added

- "All 22 seed rows present in DB after migration." Migration tested by alembic round-trip (upgrade + downgrade + upgrade); no separate test needed.
- "RLS doesn't apply to lookups." lookups has no RLS by design; testing it would just reaffirm what's already true.
- Performance/scale tests. ~30 rows total in lookups; performance is not a concern.

### Regression risk surface introduced by this step

1. **Migration's `down_revision` must be `cd2a02e452ae`.** Step 3.5 added no migration. Verify with `uv run alembic heads` (Pre-flight item 3).

2. **Schema qualification in migration body.** Use `core.lookups` per the precedent at Steps 3.0/3.4.5. Step 3.5 didn't add a migration so there's no recent precedent — mirror Step 3.4.5's INSERT pattern.

3. **Display-order values.** Sequential within each list_name. The frontend will sort by these. If a future migration adds new values mid-list, use display_order=10, 20, 30 spacing to allow inserts; the initial seed's tight spacing (1, 2, 3, 4) is fine for v0 since the lists are small and stable.

4. **`country` codes match the dev seed verbatim.** USA/UK/Canada/France/Poland (NOT ISO codes). The dev seed Excel has these literals in the country column; the lookup codes must equal those literals so frontend filter values match backend rows. If we ever switch to ISO codes, both sides change in lock step.

5. **The router's `lists` query param is comma-separated, not repeated.** `?lists=a,b,c` (one occurrence), not `?lists=a&lists=b&lists=c` (three occurrences). The latter is also a defensible REST style; choosing one consistently matters more than which. Frontend's HTTP library default is the deciding factor — if Amit's library auto-serialises arrays as repeated params, this might need to swap to that style. Surface if in doubt.

6. **Migration round-trip.** Upgrade → downgrade → upgrade must all succeed. Downgrade must remove exactly the 22 inserted rows; module_code rows from Step 3.4.5 must NOT be touched.

### Verification harness (run all five; all must be green)

```bash
# 1. Full pytest suite — new + regression
uv run pytest -v

# 2. mypy strict
uv run mypy --strict src/admin_backend

# 3. Pre-flight checker
./scripts/check_setup.sh

# 4. Migration round-trip
uv run alembic upgrade head
uv run alembic downgrade -1
uv run alembic upgrade head

# 5. Manual curl verification (server must be running)
JWT=$(uv run python -c "
from admin_backend.config import get_settings
from admin_backend.auth.testing import make_test_jwt
from uuid import UUID
print(make_test_jwt(get_settings(), user_type='PLATFORM', user_id=UUID('00000000-0000-0000-0000-000000000001')))
")

curl -s -H "Authorization: Bearer $JWT" \
  "http://localhost:8000/api/v1/lookups?lists=tenant_tier,tenant_region,tenant_status,tenant_industry,country,module_code" \
  | jq
```

Expected pytest count: ~115 passes (111 prior + 4 new). Smoke test unchanged (74 PASS — lookups has no RLS, doesn't affect the truth table).

If any leg is not green, **report rather than commit**.

---

## Scope out

- **Per-list endpoint** (`GET /api/v1/lookups/{list_name}`). Not in this step. Batch is the only shape; if a future need for per-list emerges, add as additive surface.
- **Cache headers.** Lookups change infrequently; HTTP caching would be useful. Defer post-v0.
- **lookups CRUD** (POST/PATCH/DELETE for adding lookup values via API). Step 6.x territory. Lookups extensions go via migrations until then.
- **Module-code lookup re-seed.** Already seeded at Step 3.4.5; this step does not touch module_code rows.

---

## Stop and ask if

- Migration head is not `cd2a02e452ae`. Surface the actual head and we'll figure out whether to rebase the prompt.
- The auth dependency name in the project differs from what the prompt assumes (`require_auth`). Surface the actual name; mirror.
- The `Lookup` ORM model isn't where Step 3.4.5 said it would be (`src/admin_backend/models/lookup.py`). Surface; we'll sort.
- An existing `lookups` row already has list_name in {tenant_tier, tenant_region, tenant_status, tenant_industry, country}. The migration would fail with a UNIQUE violation. Surface and we'll decide whether to UPSERT or fail-fast.
- The frontend's HTTP library (per Amit's stack) needs `?lists=a&lists=b` (repeated) instead of `?lists=a,b` (comma-separated). Surface and confirm before locking in the parser. **Note this is reversible** — the choice is a one-line parser change in the router (`lists.split(",")` vs `Query(default=[])`), not a one-way door. If unable to confirm before committing, ship comma-separated and document the choice in the report so it's easy to swap if Amit's frontend needs the other shape.

---

## Acceptance criteria

- 10 files created/modified (range slightly wider if `__init__.py` re-exports change).
- Migration applied; 22 new rows in `lookups` (`SELECT count(*) FROM core.lookups WHERE list_name IN (...)` returns 22).
- Migration round-trip clean.
- All 4 new integration tests pass.
- All 111 existing pytest passes still pass — no regressions. Expected new pytest count: at least 115.
- mypy strict clean.
- check_setup 35/35.
- Smoke test unchanged at 74 PASS.
- BUILD_PLAN.md Step 3.6 entry added with status DONE.
- `docs/openapi.json` regenerated and committed.
- **OpenAPI spec quality:** the new `/api/v1/lookups` endpoint in `docs/openapi.json` shows: a clear `summary`, a `description` explaining batch-fetch rationale, the `lists` query param marked `required: true` with `description` and `example` populated, and the response schema with `description` on each of `code`, `display_name`, `display_order`. Verify by `cat docs/openapi.json | jq '.paths."/api/v1/lookups"'` and reading the spec — Amit's Claude Code uses this spec to generate the frontend client; rich descriptions = better generated code.
- Manual curl returns valid response with all 6 lookup lists populated.

---

## Report (BEFORE proposing commit)

Five bundles per the convention:

1. **Code/migrations:** files created with line counts; the migration revision; the manual curl output showing all 6 lookup lists.
2. **CLAUDE.md updates:** Step 3.6 Completed bullet; the one-line note about response-shape choice (map vs list); no new D-XX or FN-AB.
3. **BUILD_PLAN.md updates:** Step 3.6 entry added; status DONE.
4. **architecture.md updates:** "no change" (likely outcome — endpoint addition doesn't move the schema/storage/request-flow narrative).
5. **OpenAPI spec snapshot:** `docs/openapi.json` regenerated with the new endpoint visible; verify by `cat docs/openapi.json | jq '.paths | keys'` showing `/api/v1/lookups` in the list.
6. **Prompt file:** `prompts/step-3_6-lookups-batch-endpoint-2026-05-03.md` confirmed in commit set.

Plus: pytest count delta (was 111, now ~115); mypy status; check_setup; alembic round-trip output.

Wait for explicit authorisation before staging or committing.

---

## End of prompt
