# Prompt — Step 6.6: Module enum unification (Path B)

> Generated 2026-05-06. Resolved through architectural review:
> - Two PG enums describe the same product concept ("which Ithina module") at different parts of the schema: `module_enum` (4 values, `permissions.module`) and `module_code_enum` (6 values, `tenant_module_access.module`). Historical fork from Step 3.4.5 + Step 6.1's narrowing.
> - Path B (unification) chosen over Path A (additive `ALTER TYPE module_enum ADD VALUE`): retire `module_enum` entirely, re-point `permissions.module` at `module_code_enum`, consolidate the two `lookups.list_name` entries.
> - Closes MODULES-EXT forward note as RESOLVED. Future module-vocabulary changes touch one enum + one lookup list, not two.
> - Pure schema/code unification. No new endpoints, no new schemas, no user-facing functionality. **Hygiene-before-Step-6.7**, not strictly blocking — Step 6.7 (Module Access read endpoint) reads from `tenant_module_access` which already uses `module_code_enum`, so 6.7 could ship before 6.6 with no functional difference. Doing 6.6 first means 6.7 lands against a unified schema.
>
> Paste this entire block into a fresh Claude Code session to start Step 6.6.

---

## Context: why this step exists and why now

The codebase has two PG enums + two lookup list_names describing the same product concept ("which Ithina module"):

| # | Artifact | Type | Defined at | Used by |
|---|---|---|---|---|
| 1 | `module_enum` | PG enum, **4 values** post-Step-6.1 narrowing | `db/raw_ddl/Ithina_postgres_SQL_DDL_rbac_v2.sql` (orig 6) → narrowed at Step 6.1 (`90cd038ae618`) | `permissions.module` column |
| 2 | `module_code_enum` | PG enum, **6 values** | `db/raw_ddl/Ithina_postgres_SQL_DDL_tenant_module_access_v1.sql` (Step 3.4.5) | `tenant_module_access.module` column |
| 3 | `list_name='module'` | 4 rows in `lookups` | Step 6.1 seed (`22ccfb193cff`) | permission-matrix endpoint's `module_label` JOIN |
| 4 | `list_name='module_code'` | 6 rows in `lookups` | Step 3.4.5 seed (`cd2a02e452ae`) | tenants list/detail's `modules[].name` JOIN |

The fork is a Step 3.4.5 oversight, not a design intent. The two columns answer slightly different product questions ("module a permission targets" vs "module a tenant subscribes to"), but they share the same vocabulary, and the dual-enum structure means future module additions/removals require keeping two enums in sync — exactly the drift risk that produced today's confusion (`module_enum` got narrowed at Step 6.1, `module_code_enum` didn't).

**Why now.** The upcoming Module Access read endpoint (Step 6.7) backs a UI that displays all 6 modules. The modules table is the source of truth for "which modules exist." Having a unified vocabulary before the endpoint ships is cleaner than backfilling later. Independently, MODULES-EXT was logged as a forward note at Step 6.1; the unification approach (Path B) supersedes the additive approach (Path A) the forward note originally proposed.

**Forward-only migration.** ALTER COLUMN TYPE on a 23-row `permissions` table is fast (column-metadata rewrite, no full-table rewrite at this scale). USING text-cast works because every value in the narrow `module_enum` (4 values) is also in the wide `module_code_enum` (6) — text round-trip is safe.

---

## Pre-flight

1. Run `./scripts/check_setup.sh`. Expect 35/35.
2. `git log --oneline -5` — confirm Step 6.5 (Dashboard stats endpoints) is at HEAD or HEAD~1.
3. `uv run alembic heads` — confirm head is `22ccfb193cff` (Step 6.1's lookups seed). This step adds one new migration on top.
4. Read `CLAUDE.md` fully. Focus on:
   - **D-15** (DB_SCHEMA from environment).
   - **D-21** (DDL files in `db/raw_ddl/` are frozen as-shipped — do NOT edit them; the live schema is the migration chain).
   - **D-31** (response field semantics are append-only). Relevant: this step doesn't change any response shape — `permissions.module` already returns the same enum string values; only the underlying enum type changes. Verify no consumer code does `isinstance(value, PermissionModule)` checks (which would break) vs string comparisons (which won't).
   - **"Note on PG enum columns"** convention — Postgres requires the `postgresql.ENUM(name="...", create_type=False)` shape; `sqlalchemy.Enum` silently drops `create_type=False` on the postgres dialect impl. Relevant when re-pointing the `Permission.module` column.
   - **Step 6.1's enum-cleanup precedent** (`90cd038ae618`) — the rename-recreate-USING-cast dance Postgres requires when removing values. **This step is structurally similar** (re-typing a column to a different enum type) but uses a single ALTER COLUMN TYPE instead of rename-recreate, because we're moving to a *different existing enum*, not modifying one in place.
   - **"Workflow convention"** — irreversible-cleanup migrations: `downgrade()` raises `NotImplementedError`. Step 6.6 is irreversible (`module_enum` is dropped); follows the same convention.
5. Read `src/admin_backend/models/permission.py` — the `Permission` ORM model and the `PermissionModule` Python enum (the artifact to delete). **Verify the actual column name** on the `permissions` table by reading the raw DDL at `db/raw_ddl/Ithina_postgres_SQL_DDL_rbac_v2.sql`. The prompt assumes the column is named `module`. If the DDL declares it differently (e.g., `module_code`), the migration's `ALTER TABLE permissions ALTER COLUMN <col> ...` needs the actual name. Confirm before writing the migration. Independently verify in the live DB:
   ```bash
   psql "$PSQL_URL" -c "SELECT column_name, udt_name FROM information_schema.columns WHERE table_schema='core' AND table_name='permissions' AND udt_name LIKE '%module%';"
   ```
   Expected: one row, `column_name=module`, `udt_name=module_enum`.
6. Read `src/admin_backend/models/tenant_module_access.py` — the `TenantModuleAccess` ORM model and the `ModuleCode` Python enum (the artifact to keep, possibly rename).
7. Read `src/admin_backend/repositories/permission_matrix.py` — has the JOIN against `lookups` for `module_label`. The JOIN's `list_name` literal needs to flip from `'module'` to `'module_code'`.
8. Read `src/admin_backend/repositories/permissions.py` — verify whether the `list` method JOINs against `lookups.list_name='module'` for any reason. If yes, same flip.
9. Read `src/admin_backend/repositories/tenants.py` — confirm the existing `jsonb_agg` subquery for `modules[]` JOINs against `lookups.list_name='module_code'`. This stays unchanged.
10. Read `tests/integration/test_rbac_router.py` — find every fixture or assertion that hardcodes:
    - `PermissionModule` Python enum references (will need to flip to `ModuleCode` or whatever the unified name is)
    - String literals like `'ADMIN'`, `'PRICING_OS'`, etc. — most won't change because the values are the same; flag any that reference module values that *don't* exist in `module_code_enum` (none should — `module_enum`'s 4 are a subset of `module_code_enum`'s 6).
    - The `make_permission` factory's `module=` arg — if it's typed `PermissionModule`, that becomes `ModuleCode`.
11. Read `tests/integration/conftest.py` — same survey for the `make_permission` factory and any helpers that build permissions.
12. Read `data/ithina_dev_seed_data.xlsx` permissions sheet — verify no permission row references a module value outside `module_code_enum`'s 6 values. With Step 6.1's cleanup the legacy `_key=p4` row was already removed; the surviving 23 permissions all target ADMIN / PRICING_OS / PERISHABLES_ASSISTANT / PROMOTIONS_ASSISTANT, all of which exist in both enums. **No seed Excel changes expected.** If any row violates this, surface (Stop-and-ask trigger #2).

12a. **Enumerate every column / function / domain that references `module_enum`** before writing the migration. The migration's `DROP TYPE module_enum` will fail if anything else uses the type. Run:
   ```bash
   psql "$PSQL_URL" -c "
   SELECT n.nspname AS schema, c.relname AS table, a.attname AS column
   FROM pg_attribute a
   JOIN pg_class c ON a.attrelid = c.oid
   JOIN pg_namespace n ON c.relnamespace = n.oid
   JOIN pg_type t ON a.atttypid = t.oid
   WHERE t.typname = 'module_enum' AND a.attisdropped = false;
   "
   ```
   Expected: exactly one row, `core | permissions | module`. If more rows, surface (Stop-and-ask trigger #6) — the migration needs to handle additional consumers, or those references must be re-pointed first.

   Also run a code-side scan to find any Python literal usage of the type name:
   ```bash
   grep -rn "module_enum" src/ tests/ migrations/ --include="*.py"
   ```
   Expected post-step: zero hits (any pre-step hits identify files that need updating).
13. Read `migrations/versions/90cd038ae618*.py` — Step 6.1's enum cleanup migration. Mirror its structure (forward-only, downgrade raises NotImplementedError, schema-qualified type names, env.py search_path discipline).
14. Read `migrations/versions/cd2a02e452ae*.py` — Step 3.4.5's `tenant_module_access` migration. Confirms how `module_code_enum` was originally declared and how the lookup rows were seeded.
15. Read `migrations/versions/22ccfb193cff*.py` — Step 6.1's lookups seed migration. Confirms how `list_name='module'` rows were inserted; we'll be deleting these.
16. Read `BUILD_PLAN.md` — find Step 6.1's "Known follow-ups (RBAC)" sub-section and the **MODULES-EXT** entry. This step closes that forward note.
17. Read this prompt fully.

---

## Step ID and intent

**Step 6.6** — Module enum unification (Path B). Retires `module_enum`; re-points `permissions.module` at `module_code_enum`; consolidates the two lookups list_names.

**No endpoints touched in scope.** Pure schema and code unification. The endpoints that previously read from `module_enum`-backed columns continue to return identical string values (the post-migration values are a strict subset of the pre-migration values).

**Forward note resolution:**
- **MODULES-EXT** — closes as **RESOLVED at Step 6.6** with Path B (unification). The original additive proposal (`ALTER TYPE module_enum ADD VALUE 'ROOS'` + `'GOAL_CONSOLE'` + lookup rows) is superseded.

**Concrete deliverables:**

1. New Alembic migration `<rev>_unify_module_enums.py` covering: ALTER TABLE permissions ALTER COLUMN module TYPE module_code_enum USING module::text::module_code_enum; DROP TYPE module_enum; DELETE FROM lookups WHERE list_name = 'module'.
2. Update `src/admin_backend/models/permission.py`: delete `PermissionModule` enum class; update `Permission.module` column to use `ModuleCode` (imported from `models/tenant_module_access`).
3. Optionally rename `ModuleCode` → `Module` (or keep `ModuleCode`) — see Stop-and-ask trigger #1.
4. Update `src/admin_backend/repositories/permission_matrix.py`: (a) flip the `lookups` JOIN's `list_name` literal from `'module'` to `'module_code'`; (b) replace `ORDER BY permissions.module, permissions.code` (or equivalent) with `ORDER BY lookups.display_order, permissions.code` per the locked sort-stability decision.
5. Update `src/admin_backend/repositories/permissions.py`: (a) flip any `list_name='module'` JOIN to `'module_code'`; (b) add the `lookups.display_order` JOIN-and-ORDER-BY per the locked sort-stability decision (this Repo previously may not have JOINed lookups for module — Step 6.1's E2 endpoint returned raw enum codes for module without a label; verify in pre-flight item 8).
6. Update `tests/integration/test_rbac_router.py` and `tests/integration/conftest.py`: replace `PermissionModule` references with `ModuleCode` (or unified name); replace `list_name='module'` test assertions with `list_name='module_code'`.
7. **No new endpoints.** No new schemas. No new Repos. No new error classes.
8. CLAUDE.md update: add Step 6.6 Completed bullet; mark MODULES-EXT as RESOLVED at Step 6.6 in the Forward-notes section; update the Schema state line's enum count (20 → 19, since `module_enum` is dropped).
9. BUILD_PLAN.md update: Step 6.6 entry; cross-link from Step 6.1's MODULES-EXT entry pointing at the resolution.
10. **DDL files in `db/raw_ddl/` are NOT modified** per D-21. The live schema is the migration chain.
11. **No seed Excel changes** (verified in pre-flight item 12).

CLAUDE_CODE step. Pure schema-and-code unification. Expect ~3 hours including the sort-stability Repo updates, test content adjustments, and verification.

---

## Locked migration shape

```python
"""Unify module_enum into module_code_enum.

Path B from the architectural review (2026-05-06): retire module_enum,
re-point permissions.module at module_code_enum, consolidate lookups
list_names. Closes MODULES-EXT forward note from Step 6.1.

Forward-only — irreversible. downgrade raises NotImplementedError per
the project's irreversible-cleanup convention (matching Step 6.1's
90cd038ae618).

Safety: every value in module_enum (4 values: ADMIN, PRICING_OS,
PERISHABLES_ASSISTANT, PROMOTIONS_ASSISTANT) is also in module_code_enum
(6 values: same 4 + ROOS + GOAL_CONSOLE). USING text-cast is safe.
"""

# revision identifiers
revision: str = "<generated>"
down_revision: str = "22ccfb193cff"  # Step 6.1's lookups seed
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    # Step 1: Re-type permissions.module from module_enum to module_code_enum.
    # USING text-cast is safe because every value in the narrow enum
    # is also in the wide enum.
    op.execute("""
        ALTER TABLE permissions
            ALTER COLUMN module TYPE module_code_enum
            USING module::text::module_code_enum;
    """)

    # Step 2: Drop the now-orphaned module_enum.
    op.execute("DROP TYPE module_enum;")

    # Step 3: Delete the redundant lookups rows. list_name='module_code'
    # already covers the same display labels (with 2 extra rows for
    # ROOS and GOAL_CONSOLE). list_name='module' is now redundant.
    # Defensive: assert exactly the expected row count was deleted —
    # if a future hand-edit added more rows here, surface it loudly
    # rather than silently delete unexpected data.
    result = bind.execute(
        sa.text("DELETE FROM lookups WHERE list_name = 'module' RETURNING code;")
    )
    deleted_codes = sorted(row[0] for row in result.fetchall())
    expected = sorted(
        ["ADMIN", "PRICING_OS", "PERISHABLES_ASSISTANT", "PROMOTIONS_ASSISTANT"]
    )
    if deleted_codes != expected:
        raise RuntimeError(
            f"Step 6.6 migration: lookups DELETE removed unexpected rows. "
            f"Expected {expected}, got {deleted_codes}. "
            f"Investigate before proceeding."
        )


def downgrade() -> None:
    raise NotImplementedError(
        "Step 6.6 module enum unification is forward-only. "
        "Recreating module_enum and reverting permissions.module would "
        "lose any post-step permissions targeting ROOS or GOAL_CONSOLE. "
        "Restore from backup if rollback is required."
    )
```

**Notes on the migration:**

- **Schema qualification.** Step 3.0's `env.py` sets `search_path` inside the alembic transaction, so unqualified `permissions`, `module_enum`, `module_code_enum`, `lookups` resolve correctly. Mirror Step 6.1's posture.
- **Transaction safety.** Postgres requires DROP TYPE in its own statement (can't be combined with ALTER COLUMN in some forms). Use three separate `op.execute(...)` calls.
- **No data loss.** The 23 surviving permissions all target ADMIN / PRICING_OS / PERISHABLES_ASSISTANT / PROMOTIONS_ASSISTANT — all exist in `module_code_enum`. The `lookups` DELETE removes 4 redundant rows; `module_code` already has the same 4 + 2 extras.
- **Round-trip not applicable.** Forward-only. `alembic upgrade head` succeeds; `alembic downgrade -1` raises NotImplementedError.

---

## Locked sort stability decision

The two enums have **different ordinal orderings** for the four overlapping values (per regression risk #6). Re-pointing `permissions.module` from `module_enum` to `module_code_enum` changes the result of any `ORDER BY permissions.module` query. This is not optional and not surface-able — it's a direct consequence of the migration.

**Locked option (b): replace enum-ordinal sort with explicit `lookups.display_order` sort.** Update both Repos that ORDER BY module to JOIN against `lookups` and sort by `display_order` instead.

The seeded `lookups` rows for `list_name='module_code'` carry an explicit `display_order` column that defines the intended sort. Sorting on this column is robust across enum vocabulary changes (today's unification, future ALTER TYPE ADD VALUE additions, future re-orderings) — the source of truth becomes the seed data, not the DDL declaration order.

### Repos that need updates

**`repositories/permissions.py`** — the `list` method ORDER BY.

Current (presumed shape — verify in pre-flight):
```python
.order_by(Permission.module, Permission.code)
```

After:
```python
.outerjoin(
    Lookup,
    and_(
        Lookup.list_name == "module_code",
        Lookup.code == cast(Permission.module, String),
    ),
)
.order_by(
    func.coalesce(Lookup.display_order, 999).asc(),
    Permission.code.asc(),
)
```

The `coalesce(..., 999)` defends against missing lookup rows (the same defensive posture Step 6.1 uses with `COALESCE(lookups.display_name, <enum>::text)`). Cast to `String` is needed because `Lookup.code` is text and `Permission.module` is the new enum type; PG rejects implicit text-vs-enum equality.

**`repositories/permission_matrix.py`** — the `get_matrix` method's permissions ORDER BY.

Same pattern. The matrix's `cells[]` array depends on the permissions list order; sorting by `display_order` keeps M2's position-alignment invariant stable across the migration.

### Tests that update

`tests/integration/test_rbac_router.py` — the `_permission_sort_tuple` helper (added at Step 6.1 to compare expected orderings using enum ordinal) becomes obsolete. Replace with display-order-based comparison, or remove if assertions no longer need a custom comparator.

Specific test impact:
- **P-tests** (E2 permissions list): assertions on order may need updating to match `display_order` sort instead of enum ordinal
- **M-tests** (E6 permission-matrix): position alignment invariant (M2) is preserved by this change; assertions on specific row positions may shift, but the **invariant** that cells[i] aligns with permissions[i] holds

If `_permission_sort_tuple` is referenced from elsewhere, propagate the update.

### Rationale

This is the more robust choice over option (a) ("accept the new ordering and update fixtures"). Three reasons:

1. **Survives future enum changes.** If a future step adds another module value, ALTER TYPE ADD VALUE appends to the enum's ordinal list. Sort by enum ordinal would silently re-order; sort by `display_order` doesn't.
2. **Source of truth alignment.** `lookups.display_order` is already the canonical "how should this be displayed" column; using it for sort matches its purpose.
3. **Decouples sort from schema.** The migration's success is no longer entangled with whether the new enum's declaration order happens to produce the desired UX.

The cost is one JOIN per query (negligible at v0 scale; lookups is small reference data, small index lookup).

---

## Locked Python changes

### `src/admin_backend/models/permission.py`

**Delete:** `PermissionModule` enum class entirely. Remove its import from anywhere else in the codebase.

**Update:** `Permission.module` column declaration. Currently:

```python
module: Mapped[PermissionModule] = mapped_column(
    postgresql.ENUM(
        PermissionModule,
        name="module_enum",
        create_type=False,
        native_enum=True,
        values_callable=lambda e: [m.value for m in e],
    ),
    nullable=False,
)
```

After:

```python
from admin_backend.models.tenant_module_access import ModuleCode  # or unified name

module: Mapped[ModuleCode] = mapped_column(
    postgresql.ENUM(
        ModuleCode,
        name="module_code_enum",
        create_type=False,
        native_enum=True,
        values_callable=lambda e: [m.value for m in e],
    ),
    nullable=False,
)
```

The `name="..."` change is the load-bearing line — must match the live PG enum type name post-migration.

### `src/admin_backend/models/tenant_module_access.py`

If renaming `ModuleCode` to a more neutral name (e.g., `Module`), update this file's class definition and re-export. **If keeping the name `ModuleCode`, no changes here** — see Stop-and-ask trigger #1.

### `src/admin_backend/repositories/permission_matrix.py`

Two changes:
1. Flip the `lookups` JOIN's `list_name` filter from `'module'` to `'module_code'`.
2. Per the locked sort-stability decision, change the permissions ORDER BY from `Permission.module, Permission.code` (or whatever currently relies on enum ordinal) to `lookups.display_order ASC, Permission.code ASC`. JOIN against `lookups` is now used for both the label resolution AND the sort key.

### `src/admin_backend/repositories/permissions.py`

Per the locked sort-stability decision: the `list` method gets a `LEFT JOIN` against `lookups` (using `list_name='module_code'`) and changes ORDER BY to `coalesce(lookups.display_order, 999) ASC, permissions.code ASC`. This may be a new JOIN (Step 6.1's E2 may have returned raw enum codes without a lookups JOIN — verify in pre-flight item 8). If so, the JOIN serves a single purpose: stable sort. Module label resolution stays out of the response (Step 6.1's E2 returns module as raw enum code).

### `tests/integration/test_rbac_router.py` and `tests/integration/conftest.py`

Mechanical rename: `PermissionModule` → `ModuleCode` (or unified name). String literal values stay the same (`'ADMIN'`, `'PRICING_OS'`, etc.). Any test asserting on `list_name='module'` flips to `list_name='module_code'`.

---

## Files to create/modify

Claude Code investigates the existing codebase and writes the actual code. The contracts above are locked; the implementation pattern follows existing precedents.

### `migrations/versions/<rev>_unify_module_enums.py` — new

Generate the revision via `uv run alembic revision -m "unify module enums"` (no `--autogenerate` — the project's `env.py` keeps `target_metadata = None` while the lightweight stubs exist, so autogenerate produces an empty migration body anyway). The command creates an empty migration file with a fresh revision ID; write the locked migration shape above into the body. The `down_revision` should pin to `22ccfb193cff` (Step 6.1's lookups seed).

### `src/admin_backend/models/permission.py` — modify

Delete `PermissionModule` enum class. Update `Permission.module` column to use `ModuleCode` (or unified name) and reference `module_code_enum` as the PG type name.

### `src/admin_backend/models/tenant_module_access.py` — modify only if renaming

If choosing to rename `ModuleCode` to a unified name, update this file. Otherwise no change.

### `src/admin_backend/repositories/permission_matrix.py` — modify

Flip `list_name='module'` to `list_name='module_code'` in the lookups JOIN.

### `src/admin_backend/repositories/permissions.py` — modify if applicable

Per pre-flight item 8.

### `tests/integration/test_rbac_router.py` — modify

Replace `PermissionModule` references; flip `list_name='module'` test assertions to `list_name='module_code'`.

### `tests/integration/conftest.py` — modify

Replace `PermissionModule` references in the `make_permission` factory and any helpers.

### `scripts/smoke_curl.sh` — no change

This step doesn't add public endpoints. Smoke-curl assertions for existing endpoints (e.g., `/permissions`, `/permission-matrix`) continue to PASS — the wire-format strings are unchanged. Verify in the verification harness.

### CLAUDE.md — modify

- **Current state → Completed:** Step 6.6 bullet covering the migration, the model/Repo updates, the lookups consolidation, the resolution of MODULES-EXT.
- **Schema state line:** enum count drops from 20 to 19 (`module_enum` dropped). Application table count unchanged at 12.
- **Forward-notes section → MODULES-EXT:** mark as RESOLVED at Step 6.6 with a one-line resolution paragraph: "Path B (unification) shipped instead of Path A (additive). `permissions.module` re-pointed to `module_code_enum`; `module_enum` dropped; `lookups` list_name='module' deleted." Update the Forward-notes header / index to reflect one fewer open note (count drops by 1).
- **No new D-XX entries.** The pattern of re-pointing a column to a different existing enum is straightforward and doesn't earn its own decision.
- **No new FN-AB entries.**

### BUILD_PLAN.md — modify

Add Step 6.6 entry. Status: TODO → DONE in same commit. Standard scope-in / scope-out / acceptance criteria structure.

The "Scope in" should explicitly call out: this step closes MODULES-EXT (Step 6.1 forward note) via Path B, supersedes the additive approach.

Update Step 6.1's MODULES-EXT bullet to point at Step 6.6 as the resolution.

### `prompts/step-6_6-module-enum-unification-2026-05-06.md` — new

This prompt file. Bundled per the per-step convention.

### `docs/endpoints/openapi.json` — re-export

After all code is in and tests pass, regenerate. Verify:
- `/api/v1/permissions` response schema's `module` field still appears as a string with the same accepted values
- `/api/v1/permission-matrix` similarly unchanged in wire format
- The OpenAPI's enum vocabulary listing for `module` may grow (now includes ROOS, GOAL_CONSOLE — the wider enum's values) even though no permission rows currently target those. This is a wire-format **change** in the strict sense (schema definition broadened) but not a breaking change (clients that strictly validate against the narrow set may need to update). **Document explicitly** in the report.

### `docs/architecture.md` — no edit

This step doesn't change architecture. The "two enums for one concept" was never documented architecturally; its retirement doesn't earn a doc note.

### DDL files in `db/raw_ddl/` — DO NOT MODIFY

Per D-21. The live schema is the migration chain. The DDL files stay frozen at their as-shipped state.

---

## Testing and regression discipline

### New tests

**No new tests written from scratch.** This step is mechanical schema unification; correctness is verified by existing tests continuing to pass under the new schema.

### Tests that need content updates (beyond mechanical renames)

- **Lookups row-count assertions.** Anywhere that asserted `list_name='module'` has 4 rows or that `lookups` total is 25 (Step 6.1 baseline) needs updating: `list_name='module'` → 0; total → 21. Likely `tests/integration/test_seed_loader.py` and possibly the existing lookups-router tests.
- **Permission ordering assertions.** Any test that asserted exact permission positions (P-tests for `/permissions` list; M-tests for permission-matrix's `cells[]` alignment) may need re-validation against the new `lookups.display_order`-based sort. The **invariant** that cells[i] aligns with permissions[i] (M2) is preserved by the locked sort change; specific row positions may shift.
- **`_permission_sort_tuple` helper** at `tests/integration/test_rbac_router.py` (added at Step 6.1 to compare expected orderings using enum ordinal) is now obsolete. Replace with `display_order`-based comparison or remove.

### Tests that need updates (mechanical renames)

- `tests/integration/test_rbac_router.py` — replace `PermissionModule` references; flip `list_name='module'` to `list_name='module_code'` where asserted
- `tests/integration/conftest.py` — replace `PermissionModule` in `make_permission` factory

### Tests deliberately not added

- "Migration round-trip" tests. Step 6.6 is forward-only; no downgrade path.
- "OpenAPI snapshot includes ROOS and GOAL_CONSOLE in module vocabulary" — informational, not contractually required.

### Regression risk surface

1. **Existing `test_rbac_router.py` tests must stay green.** All 23 tests. **Particularly load-bearing:**
   - **R4** (user_count correlated subquery) — unaffected by enum change but let's verify
   - **M2** (matrix cells/roles position alignment) — the JOIN flip in permission_matrix could subtly affect ordering if `module_code` lookup rows have different `display_order` than `module` lookup rows. Verify the seed data round-trips identically.
   - All P-tests (E2 permissions list) — wire-format integrity check
2. **Tenants list/detail tests must stay green.** Step 3.4.5's L10 / D6 / L10b verify the `modules[]` array via the existing `module_code` JOIN, which this step doesn't touch.
3. **Smoke test** (`scripts/smoke_test.py`) — unchanged at 74 PASS. The seed loader runs the same INSERTs against the same schema (enum values are identical strings; only the type backing them changed).
4. **Per-resource regression checkpoint:** every prior router file at exactly its pre-step PASS count.
5. **Migration round-trip not applicable.** Forward-only by design.
6. **`module_code_enum` ordinal ordering — WILL CHANGE post-migration.** Postgres sorts enum columns by ordinal (declaration order). The two enums declare values differently:
   - `module_enum` (post-Step-6.1) ordinal order: `ADMIN=0`, `PRICING_OS=1`, `PERISHABLES_ASSISTANT=2`, `PROMOTIONS_ASSISTANT=3`
   - `module_code_enum` ordinal order: `ROOS=0`, `PRICING_OS=1`, `PERISHABLES_ASSISTANT=2`, `PROMOTIONS_ASSISTANT=3`, `GOAL_CONSOLE=4`, `ADMIN=5`

   The four overlapping values have **different ordinals between the two enums** — most notably `ADMIN` moves from ordinal 0 to ordinal 5. Any query that does `ORDER BY permissions.module` returns rows in a different sequence post-migration. The permission-matrix endpoint's `cells[]` array is position-aligned to `permissions[]`; if the permissions list re-orders, the matrix re-orders. Step 6.1's load-bearing **M2** test asserts position alignment.

   **This will happen. It is not a "may fire" risk.** Locked resolution: see "Locked sort stability decision" section below.
7. **OpenAPI broadening.** The regenerated openapi.json's `module` enum list will grow from 4 to 6 values. Frontend codegen consumers (Amit) may emit warnings if their generator strictly validates. Note in report; not a blocker.

### Verification harness (run all seven; all must be green)

```bash
# 1. Full pytest
uv run pytest -v

# 2. Per-resource regression checkpoint (LOAD-BEARING)
uv run pytest tests/integration/test_tenants_router.py -v
uv run pytest tests/integration/test_platform_users_router.py -v
uv run pytest tests/integration/test_tenant_users_router.py -v
uv run pytest tests/integration/test_org_tree_router.py -v
uv run pytest tests/integration/test_lookups_router.py -v
uv run pytest tests/integration/test_rbac_router.py -v
uv run pytest tests/integration/test_dashboard_router.py -v
# Each file must report 100% PASS at exactly its pre-step count.
# rbac 23/23 is the most likely to drift — verify especially.

# 3. mypy strict
uv run mypy --strict src/admin_backend
# Risk: if PermissionModule was imported anywhere this step missed,
# mypy will fail. Use the failure to find missed import sites.

# 4. Pre-flight checker
./scripts/check_setup.sh

# 5. Alembic migration round-trip (forward-only — only test upgrade)
uv run alembic upgrade head
uv run alembic heads
# Expected: new revision is at head; previous head 22ccfb193cff is now history.
uv run alembic check

# Manually verify no module_enum left in the live DB:
psql "$PSQL_URL" -c "SELECT typname FROM pg_type WHERE typname IN ('module_enum', 'module_code_enum');"
# Expected: only module_code_enum present.

psql "$PSQL_URL" -c "SELECT list_name, COUNT(*) FROM core.lookups GROUP BY list_name ORDER BY list_name;"
# Expected: 'module' absent; 'module_code' present with 6 rows.

# Pre-migration verification: every permission's module value is in module_code_enum's vocabulary.
# Run BEFORE alembic upgrade to catch any rogue values that would fail the USING text-cast.
psql "$PSQL_URL" -c "
SELECT module::text AS module_value, COUNT(*) AS row_count
FROM core.permissions
GROUP BY module
ORDER BY module::text;
"
# Expected: only values from {ADMIN, PRICING_OS, PERISHABLES_ASSISTANT, PROMOTIONS_ASSISTANT}.
# If any other value appears, halt — the migration's USING cast will fail.

# Pre-migration: enumerate everything that references module_enum (per pre-flight 12a).
# Should return exactly one row: core | permissions | module.
psql "$PSQL_URL" -c "
SELECT n.nspname AS schema, c.relname AS table, a.attname AS column
FROM pg_attribute a
JOIN pg_class c ON a.attrelid = c.oid
JOIN pg_namespace n ON c.relnamespace = n.oid
JOIN pg_type t ON a.atttypid = t.oid
WHERE t.typname = 'module_enum' AND a.attisdropped = false;
"

# 6. scripts/smoke_curl.sh
bash scripts/smoke_curl.sh
# Expected: all PASS, count unchanged (no new endpoints).

# 7. Manual curl verification — wire format integrity
PJWT=$(./scripts/jwt/generate_7d.sh anjali@ithina.ai)

# /permissions list still works, returns string module values
curl -s -H "Authorization: Bearer $PJWT" "http://localhost:8000/api/v1/permissions?limit=5" \
  | jq '.items | map(.module)'
# Expected: 5 string values, drawn from {ADMIN, PRICING_OS, PERISHABLES_ASSISTANT,
# PROMOTIONS_ASSISTANT}. Same shape as pre-step.

# /permission-matrix module_label still resolves
curl -s -H "Authorization: Bearer $PJWT" "http://localhost:8000/api/v1/permission-matrix" \
  | jq '.permissions[0] | {module, module_label}'
# Expected: module is a string (e.g. "ADMIN"); module_label is the human label
# (e.g. "Admin"). Verifies the JOIN flip works.

# /tenants modules[] unchanged
curl -s -H "Authorization: Bearer $PJWT" "http://localhost:8000/api/v1/tenants?limit=1" \
  | jq '.items[0].modules'
# Expected: array of {code, name} objects. Same shape as pre-step.

# /dashboard/governance-stats modules_deployed unchanged
curl -s -H "Authorization: Bearer $PJWT" "http://localhost:8000/api/v1/dashboard/governance-stats" \
  | jq '.modules_deployed'
# Expected: { value: 27, sub_text: "across 7 tenants", available: true, ... }
# Wire format identical to pre-step.
```

If any leg is not green, **report the failure rather than the step.**

---

## Scope out

- **No additive `ALTER TYPE module_enum ADD VALUE`.** Path B replaces Path A entirely.
- **No DDL file edits in `db/raw_ddl/`.** Per D-21, these are frozen as-shipped.
- **No changes to public endpoint shapes.** Wire format identical pre/post.
- **No new endpoints.** Module Access read endpoint is Step 6.7, separate.
- **No label-resolution sweep across older endpoints.** Per the locked policy (Amit confirmed): old endpoints stay bare-enum; new endpoints (6.7+) get server-side label resolution.
- **No seed Excel changes.** All 23 surviving permissions target values that exist in `module_code_enum`.
- **No rename of `module_code_enum`.** The PG enum keeps its current name.
- **No backwards rename of `Permission.module` column.** It stays named `module`.

---

## Stop and ask if

1. **Naming the unified Python enum.** Today the surviving Python enum is `ModuleCode` (in `models/tenant_module_access.py`). Two options:
   - **(a) Keep the name `ModuleCode`.** Permission's column references it; clean import. Default if unclear.
   - **(b) Rename `ModuleCode` to `Module`.** More neutral name; reflects its now-broader role (covers both subscriptions and permission-targeting). Slightly more diff (every `ModuleCode` reference in `tenant_module_access.py` and its tests changes).
   Surface; user picks. Default if unclear: (a).

2. **Permission row references a value outside `module_code_enum`'s 6 values.** Should not happen (Step 6.1's narrowing already removed the legacy `_key=p4` row), but verify the Excel and the live DB. If found, surface — we may need to delete the row or amend the migration.

3. **Locked sort-stability change broke a test in an unexpected way.** The locked decision is to update `permissions.py` Repo's list method and `permission_matrix.py` Repo to ORDER BY `lookups.display_order ASC, permissions.code ASC` rather than relying on enum ordinal (see "Locked sort stability decision"). If after that change a test still breaks for ordering reasons, surface — the test may be asserting on a different invariant we missed.

4. **`PermissionModule` is imported from a file not listed in pre-flight.** Use `mypy --strict` failure as the discovery mechanism. Surface the missed file and the proposed fix.

5. **Cloud SQL dev DB has divergent state from local for the affected tables.** Before deploy, run the same verification queries (pg_attribute scan for `module_enum` references; lookups row counts for `list_name='module'` and `list_name='module_code'`; `permissions.module` column type) against Cloud SQL dev. Expected: identical to local pre-migration. If different — extra `lookups` rows for `list_name='module'`, additional columns referencing `module_enum`, etc. — surface and pause the deploy. The migration's defensive row-count assertion (see "Locked migration shape") will catch unexpected lookups state at run time, but pre-deploy verification catches it earlier.

6. **OpenAPI codegen breakage**. If the regenerated `openapi.json` has a difference that's likely to break Amit's frontend codegen pipeline beyond just "enum list grew" (e.g., property removed, field renamed), surface before commit.

---

## Acceptance criteria

- 1 new migration file at `migrations/versions/<rev>_unify_module_enums.py`; `alembic upgrade head` succeeds.
- `module_enum` PG type does NOT exist post-migration (verified via `pg_type`).
- `module_code_enum` PG type exists with 6 values unchanged.
- `permissions.module` column has type `module_code_enum` (verified via `information_schema.columns` or `pg_attribute`).
- `lookups` table has zero rows where `list_name='module'`; 6 rows where `list_name='module_code'`.
- `PermissionModule` Python enum class is removed from the codebase. `grep -r "PermissionModule" src/ tests/` returns zero hits.
- `Permission.module` column references `ModuleCode` (or renamed unified enum).
- `permission_matrix.py`'s lookups JOIN uses `list_name='module_code'`.
- All 23 prior `test_rbac_router.py` tests pass at their pre-step count.
- Per-resource regression checkpoint: every prior router file at exactly its pre-step PASS count.
- mypy strict clean.
- check_setup 35/35.
- pytest smoke unchanged at 74 PASS.
- `scripts/smoke_curl.sh` PASS count unchanged (no new endpoints).
- The other three workflow scripts (`scripts/deploy-cloud-run.sh`, `scripts/env.sh`, `scripts/jwt/generate_7d.sh`) — unchanged.
- Alembic head advances by one revision.
- Manual curl verification: `/permissions`, `/permission-matrix`, `/tenants` modules[], `/dashboard/governance-stats` modules_deployed all return wire-format-identical responses pre/post step.
- OpenAPI spec regenerated; document the enum-list broadening explicitly in the report.
- BUILD_PLAN's MODULES-EXT entry marked RESOLVED at Step 6.6.
- CLAUDE.md schema state line updated (20 → 19 enums).

---

## Report (BEFORE proposing commit)

Six bundles per the convention:

1. **Code:** files modified with line counts; verify via grep that `PermissionModule` is fully retired; manual curl outputs verifying wire-format integrity for `/permissions`, `/permission-matrix`, `/tenants`, `/dashboard/governance-stats`. **Workflow scripts:** all four unchanged.
2. **CLAUDE.md updates:** Step 6.6 Completed bullet; MODULES-EXT marked RESOLVED with the resolution paragraph; schema state line updated (enum count 20 → 19). No new D-XX or FN-AB.
3. **BUILD_PLAN.md updates:** Step 6.6 entry; cross-link from Step 6.1's MODULES-EXT entry pointing at the resolution.
4. **architecture.md updates:** "no change."
5. **OpenAPI spec snapshot:** `docs/endpoints/openapi.json` regenerated. **Explicitly document** the enum-list broadening (`module` field's accepted values grow from 4 to 6); flag for Amit if the codegen pipeline is strict-validating.
6. **Prompt file:** `prompts/step-6_6-module-enum-unification-2026-05-06.md` confirmed in commit set.

Plus: pytest **function count** unchanged (no test additions); some assertion **content** updates beyond mechanical renames — specifically lookups row-count assertions (4 → 0 for `list_name='module'`; 25 → 21 total post-Step-6.1 seed) and ordering assertions in P-tests / M-tests if `_permission_sort_tuple` was driving them. Per-file regression numbers confirming each at 100% PASS with no count drop; mypy status; check_setup; alembic head advanced by one; live-DB verification of `pg_type` and `lookups` row counts.

Wait for explicit authorisation before staging or committing.

---

## After completing

Propose a git commit per CLAUDE.md "After completing a task" Pattern A:

```
git status
git add -A
git commit -m "Step 6.6: Module enum unification (Path B)

- Retires module_enum PG type; re-points permissions.module at
  module_code_enum; consolidates lookups list_names ('module' rows
  deleted; 'module_code' rows retained as the canonical reference).
- Closes MODULES-EXT forward note (Step 6.1) as RESOLVED. Path B
  (unification) shipped instead of Path A (additive ALTER TYPE
  ADD VALUE) — eliminates the two-enum duplication that caused
  the original drift between Step 3.4.5 and Step 6.1.
- One forward-only Alembic migration:
  - ALTER TABLE permissions ALTER COLUMN module TYPE
    module_code_enum USING module::text::module_code_enum;
  - DROP TYPE module_enum;
  - DELETE FROM lookups WHERE list_name = 'module';
- downgrade() raises NotImplementedError per the project's
  irreversible-cleanup convention (matching Step 6.1's
  90cd038ae618).
- Python: PermissionModule enum class deleted; Permission.module
  references ModuleCode from models/tenant_module_access.py.
- permission_matrix.py and permissions.py: lookups JOIN flips
  from list_name='module' to 'module_code'; ORDER BY changes
  from enum ordinal to lookups.display_order (locked decision —
  see prompt's 'Locked sort stability decision' section). The
  M2 cells/permissions position-alignment invariant is preserved;
  specific row positions may shift.
- Mechanical test/fixture renames: PermissionModule -> ModuleCode
  in test_rbac_router.py and conftest.py; list_name='module'
  assertions flip to list_name='module_code'.
- WIRE FORMAT FIELD VALUES UNCHANGED. /permissions, /permission-matrix,
  /tenants modules[], /dashboard/governance-stats modules_deployed
  all return identical per-row JSON pre/post step. ROW ORDERING
  CHANGES on /permissions and /permission-matrix because the sort
  basis flipped from enum ordinal to lookups.display_order — see
  "Locked sort stability decision" in the prompt; this is a planned
  change not a regression.
- OpenAPI BROADENED (informational, not breaking): the 'module'
  field's accepted enum values grow from 4 to 6 (ROOS and
  GOAL_CONSOLE now appear in the schema's enum vocabulary), even
  though no permission rows currently target those values. Frontend
  codegen consumers may need to update if strictly validating.
- DDL files in db/raw_ddl/ unchanged per D-21 (frozen as-shipped).
- No seed Excel changes (all 23 surviving permissions target
  module values present in both enums).
- Schema state line updated: enums 20 -> 19.
- Test count unchanged. mypy strict clean. check_setup 35/35.
  Smoke 74/74. Per-resource regression: tenants 34, platform_users 10,
  tenant_users 13, org_tree 21, rbac 23, dashboard 16 — all unchanged.
- Hygiene before Step 6.7 (Module Access read endpoint). 6.7 reads
  from tenant_module_access which already uses module_code_enum, so
  6.6 doesn't strictly block 6.7 — but landing 6.6 first means 6.7
  ships against a unified schema. Step 6.7 will follow the new
  label-handling pattern (server-side resolution for new endpoints
  per the policy Amit and the team confirmed)."
```

Ask user "Run? yes / no / edit message". On yes, execute via bash tool. On no, skip. On edit, prompt for new message.

---

## End of prompt
