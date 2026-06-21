# Prompt — remove ROOS from module_code vocabulary (Python + tests + seed only) (v2)

## Goal

Remove `ROOS` from the Python-layer module_code vocabulary and from
seed data, leaving the DB enum `core.module_code_enum` unchanged at 6
values. The remaining 5 modules (PRICING_OS, PERISHABLES_ASSISTANT,
PROMOTIONS_ASSISTANT, GOAL_CONSOLE, ADMIN) shift up in
`lookups.display_order` from gapped (2-6) to contiguous (1-5).

ROOS is being retired as a module name. A new module name will replace
it later; that's a separate future step. This prompt is the cleanup
that removes ROOS without touching the underlying PG enum (which is
hard to modify and will be handled by the future rename migration).

**Framing:** narrow Python + tests + seed XLSX. No new Alembic
migration. No DDL changes. No cloud SQL execution. No edits to archived
docs or frozen DDL files. Operator runs cloud cleanup SQL separately
after the commit lands.

## Pre-conditions for running this work

Before invoking this work, confirm:

1. Local Postgres is at alembic head `3e05299cb533` or newer. Verify
   with `uv run alembic current`. Bail if older
2. Local DB has been seeded recently against the current XLSX:
   `uv run python -m scripts.seed_dev_data --reset`. The pre-cleanup
   baseline must be working before we modify it
3. `uv run pytest -q` shows the current expected pass count
   (263 per CLAUDE.md). Pre-cleanup test count is the baseline to
   compare against after the change

## Pre-flight reading

Read these before writing code:

- `src/admin_backend/schemas/modules_access.py` — Pydantic literal at
  line 41 references `"ROOS"`
- `src/admin_backend/models/permission.py` — line 10 references ROOS.
  **First verify what this line actually is** (a live SQLAlchemy enum
  declaration, an import, or a comment). Different intervention based
  on which one. See "Open question 1" below
- `src/admin_backend/models/tenant_module_access.py` — line 36 has a
  Python enum class with `ROOS = "ROOS"`
- `tests/integration/test_modules_access_router.py` — 8 references
- `tests/integration/test_rbac_router.py` — `_MODULE_DISPLAY_ORDER`
  dict at lines 56-59 plus surrounding context
- `tests/integration/test_tenants_router.py` — 6+ references in
  fixture builders
- `tests/integration/test_dashboard_router.py` — line 454 module
  iteration includes ROOS
- `scripts/smoke_test.py` — `insert_tenant_module_access` default arg
  `module='ROOS'` plus 3 call sites
- `data/ithina_dev_seed_data.xlsx` (binary — inspect via openpyxl in
  a quick Python check). Two sheets need edits: `lookups` and
  `tenant_module_access`
- `tests/integration/test_seed_loader.py` — EXPECTED_VISIBLE_COUNTS
  dict; may need adjustment if row counts change
- `migrations/versions/2fdc4bc9f4cb_step_6_7_module_access_lookups_seed.py`
  — read only. **DO NOT EDIT.** Migrations are immutable history. The
  display_order shift-up doesn't need a new migration; it's a seed
  change that lands via re-seed

After reading, restate the open question's answer (see below) and the
list of files to be touched BEFORE starting edits.

**Batch the Open Questions.** Resolve Open Questions 1-4 (see below)
in your initial reading pass and report ALL FOUR answers in one batched
message. Do not surface each independently — that creates unnecessary
round-trip pauses. If all four can be answered from static reading,
proceed to the cells-to-modify phase after the operator confirms.

## Open question 1 — verify `models/permission.py:10`

```
src/admin_backend/models/permission.py:10:    PERISHABLES_ASSISTANT, PROMOTIONS_ASSISTANT, ROOS, GOAL_CONSOLE)
```

This looks like the tail of a multi-line statement. Open the file and
read lines 1-20. Determine which case applies:

**Case A: SQLAlchemy PG_ENUM(...) live declaration.** Removing ROOS
would create a mismatch with the DB enum (which still has ROOS as a
value). Python imports may fail. **In this case, DO NOT remove ROOS
from this line.** Pydantic narrowing happens at `schemas/modules_access.py`
only.

**Case B: Comment, docstring, or unused list.** Safe to remove ROOS.

**Case C: Something else (a `__all__`, an enum class mirror, etc.).**
Surface to the operator with the actual line context and ask what to do.

Report which case before editing this file.

## Open question 2 — Pydantic literal vs Python enum class

`models/tenant_module_access.py` likely has a Python `ModuleCode` enum
class. After removing ROOS, the class loses one member. Code that
imports `ModuleCode.ROOS` would crash at import time.

Verify by grep:
```bash
grep -rn "ModuleCode\.ROOS" --include="*.py" src/ tests/ scripts/
```

Every match needs to be either (a) removed if the surrounding code is
ROOS-specific (rare) or (b) replaced with `ModuleCode.GOAL_CONSOLE`
(per the operator's substitution decision). Surface the count of
matches and the per-file breakdown before editing.

## Open question 3 — XLSX manipulation

The seed XLSX is binary. Modifying it requires openpyxl. Two concerns:

1. **Formula preservation.** Step 6.1 commit messages note that openpyxl
   can't compute formulas, so `=TRUE()` / `=FALSE()` cells must be
   converted to literal booleans before saving, or cached values get
   wiped. Verify whether the `lookups` and `tenant_module_access`
   sheets contain formula columns. If yes, the conversion step is part
   of this work
2. **Idempotency.** A re-run of the edit should produce a byte-stable
   XLSX (assuming no further edits). Verify by editing, saving, then
   re-loading the file and confirming the edit appears as intended

## Open question 4 — verify Pydantic construct shape at schemas/modules_access.py:41

Line 41 references `"ROOS"`. The surrounding construct could be:
- `Literal["ROOS", ...]`
- A Python `Enum` class with `ROOS = "ROOS"`
- A list constant
- A Pydantic Field's `examples=[...]` argument
- Something else

Open the file and read lines 30-55. Identify the actual construct.
The removal mechanic differs:
- `Literal[...]`: drop the `"ROOS"` string literal from the args
- `Enum` class: remove the `ROOS = "ROOS"` member line
- List constant: remove the `"ROOS"` element
- `Field(examples=...)`: drop from the examples list (cosmetic only)

Report the construct type before editing.

## Cells to modify

### Python source (3 files)

**`src/admin_backend/schemas/modules_access.py`**:
- Line 41 (or wherever the Literal/Pydantic enum lives): remove
  `"ROOS"` from the literal

**`src/admin_backend/models/permission.py`**:
- Line 10: action depends on Open question 1's answer. Case A: leave
  alone. Case B: remove ROOS. Case C: surface

**`src/admin_backend/models/tenant_module_access.py`**:
- Line 36: remove `ROOS = "ROOS"` from the Python enum class
- Imports may need adjustment if `ROOS` is exported anywhere
- **Add a code comment** immediately above the `ModuleCode` enum
  declaration noting the DB-Python drift:
  ```python
  # ROOS retired from Python vocabulary on 2026-05-12. The DB enum
  # core.module_code_enum still contains ROOS as a value (PG enum
  # cleanup deferred to the future rename migration when ROOS's
  # replacement module name is decided). Any DB row with module='ROOS'
  # will crash Pydantic validation; the operator-run cloud cleanup SQL
  # mitigates this by deleting all such rows.
  class ModuleCode(str, Enum):
      ...
  ```
  Adjust the comment wording to match the file's existing comment style

### Tests (4 files)

**General guidance:** ROOS is being retired AS A TEST FIXTURE VALUE.
Comments, docstrings, and historical references about ROOS as a product
or marketing name (if any exist in the test files) should be **left
intact** — those are historical context, not test data. Substitute
only the live code references and the assertions that test against
ROOS-as-fixture.

Use search-and-replace tools at your discretion, but **read each match
in context before committing the change.** A blind `sed` would
over-substitute. Each replacement must preserve the test's semantic
intent: ROOS was used as a placeholder for "an arbitrary module";
GOAL_CONSOLE serves the same role.

**`tests/integration/test_modules_access_router.py`** (8 references):
- Line 54: literal `"ROOS"` in some list — replace with `"GOAL_CONSOLE"`
- Lines 163, 166, 181, 205, 206, 219, 388, 400: replace ROOS references
  with GOAL_CONSOLE. **Read each one in context** — the substitution
  must preserve the test's semantic intent. If a test asserts something
  that's structurally about ROOS (e.g., "ROOS is the first display-order
  module"), the assertion is now about GOAL_CONSOLE. Update the assertion
  data too

**`tests/integration/test_rbac_router.py`**:
- Lines 56-59: `_MODULE_DISPLAY_ORDER` dict. Currently:
  ```python
  _MODULE_DISPLAY_ORDER = {
      "ROOS": 1,
      "GOAL_CONSOLE": 2,
      "PRICING_OS": 3,
      "PERISHABLES_ASSISTANT": 4,
      "PROMOTIONS_ASSISTANT": 5,
      "ADMIN": 6,
  }
  ```
  After shift-up:
  ```python
  _MODULE_DISPLAY_ORDER = {
      "GOAL_CONSOLE": 1,
      "PRICING_OS": 2,
      "PERISHABLES_ASSISTANT": 3,
      "PROMOTIONS_ASSISTANT": 4,
      "ADMIN": 5,
  }
  ```
  Update the comment on line 56 to reflect the new order
- Any other ROOS reference in this file: replace with GOAL_CONSOLE

**`tests/integration/test_tenants_router.py`** (6+ references):
- Lines 589, 593, 615, 618, 634, 638, 640, 829, 841: replace ROOS with
  GOAL_CONSOLE. The tests build tenant fixtures with module enablement;
  swapping the module name preserves test intent (cross-tenant isolation,
  display order, etc.)

**`tests/integration/test_dashboard_router.py`**:
- Line 454: `for module in (ModuleCode.ROOS, ModuleCode.PRICING_OS, ModuleCode.ADMIN):`
  → `for module in (ModuleCode.GOAL_CONSOLE, ModuleCode.PRICING_OS, ModuleCode.ADMIN):`

### Scripts (1 file)

**`scripts/smoke_test.py`**:
- Line 267: change function default `module='ROOS'` → `module='GOAL_CONSOLE'`
- Lines 301, 311, 1255: replace explicit `'ROOS'` arguments with
  `'GOAL_CONSOLE'`

### Seed data (1 file, 2 sheets)

**`data/ithina_dev_seed_data.xlsx`**:
- **`lookups` sheet**: delete the row where `list_name='module_code'` and
  `code='ROOS'`. Then update `display_order` for the remaining 5 rows in
  the `module_code` set, shifting up by 1 each (2→1, 3→2, 4→3, 5→4, 6→5).
  This is the load-bearing data update for the display-order shift
- **`tenant_module_access` sheet**: delete every row where `module='ROOS'`.
  Per the data shown in operator context, this is approximately 6 rows
  across multiple tenants. Count and report
- Re-save with formula preservation handling (see Open question 3)

### Test expectations that may need adjustment

**`tests/integration/test_seed_loader.py`**:
- `EXPECTED_VISIBLE_COUNTS_PLATFORM` may include `lookups` count and
  `tenant_module_access` count. After the XLSX edit:
  - `lookups`: −1 row (the ROOS row deleted)
  - `tenant_module_access`: −N rows (where N is the actual ROOS row count)
- Update both keys to match. **Run the seed loader first to determine
  the actual N**, then update the dict

### NOT to be edited

- `migrations/versions/2fdc4bc9f4cb_step_6_7_module_access_lookups_seed.py`
  and any other migration file. **Immutable historical record.**
- `db/raw_ddl/*.sql`. **Frozen by D-21 convention.**
- `docs/archive/*`. **Archived snapshots; mutating them corrupts history.**
- `docs/endpoints/openapi.json`. Will regenerate naturally; not a target
  of this work
- `CLAUDE.md`, `BUILD_PLAN.md`. **Operator has structural revisions to
  these files in flight.** Do NOT propose edits. Do NOT add either to
  `git add`

## Operator-side follow-up (NOT in this commit)

After the commit lands locally and tests pass, the operator runs SQL
against cloud's Cloud SQL Studio:

```sql
-- Delete ROOS rows from tenant_module_access
DELETE FROM core.tenant_module_access WHERE module = 'ROOS';

-- Delete ROOS lookup row and shift remaining module_code rows up
DELETE FROM core.lookups WHERE list_name = 'module_code' AND code = 'ROOS';
UPDATE core.lookups
   SET display_order = display_order - 1
 WHERE list_name = 'module_code'
   AND display_order > 1;
```

**Do NOT execute the above SQL from inside this work.** The cloud
SQL execution is operator-only. Mention it in the commit message body
as a "follow-up step required to fully clean up cloud" pointer.

## Scope out

- **`CLAUDE.md` and `BUILD_PLAN.md`.** Do NOT edit. Operator has
  structural revisions in flight. The cleanup may be summarized in
  those revisions later by the operator
- Alembic migration. **NO new migration in this step.** The DB enum
  stays at 6 values. The future rename of ROOS to a new module name
  will ship its own migration when that name is decided
- Cloud SQL execution. Operator handles
- DDL files in `db/raw_ddl/`. Frozen per D-21
- Archived docs in `docs/archive/`. Immutable
- `docs/endpoints/openapi.json`. Regenerates naturally via subsequent
  `./scripts/test_endpoints_max_view.sh` runs; not a target here
- Routers, repositories. None reference ROOS by literal — they reference
  via the Pydantic enum or the SQLAlchemy enum
- `models/__init__.py` — verify whether `ROOS` is re-exported; if not,
  no change needed
- The frontend. **Operator notifies Amit separately** that the wire
  enum drops from 6 to 5 module codes. Not a code change in this
  repository

## Stop and ask if

1. Open question 1's answer is Case C (live declaration that's not
   simply removable). Surface the line context
2. Open question 2 reveals more `ModuleCode.ROOS` references than the
   grep-derived list. Surface the count
3. The XLSX has formulas in the `lookups` or `tenant_module_access`
   sheets. Surface and ask whether to convert to literals before saving
   (per Step 6.1 commit message guidance) or whether the cells are
   already literal-valued
4. After running tests, MORE than 3 tests fail (excluding tests
   specifically asserting display_order = ROOS@1). Stop. A wider
   failure pattern signals a missed reference somewhere
5. `test_seed_loader.py`'s EXPECTED_VISIBLE_COUNTS adjustments don't
   match the actual post-edit row counts. Surface the diff
6. The Pydantic narrowing breaks any other module's import path (e.g.,
   if `ModuleCode` is re-exported through `__init__.py` and removing
   one member causes name-resolution failures elsewhere). Surface
7. The local seed re-run (after XLSX edit) produces an alembic-version
   change or schema warning. The XLSX edit shouldn't touch alembic
   state; if it does, surface
8. Tests pass count goes UP unexpectedly (test count change is fine if
   ROOS-specific tests are removed; surface the delta so it's documented)

## Acceptance criteria

1. ROOS removed from Pydantic Literal/enum at
   `src/admin_backend/schemas/modules_access.py`
2. ROOS removed from `ModuleCode` Python enum at
   `src/admin_backend/models/tenant_module_access.py`
3. `models/permission.py:10` handled per Open question 1's answer
4. All `ModuleCode.ROOS` and `"ROOS"` test-file references replaced
   with `GOAL_CONSOLE`
5. `_MODULE_DISPLAY_ORDER` dict updated to the 5-key shifted-up form
6. `data/ithina_dev_seed_data.xlsx` edited: ROOS row removed from
   `lookups` sheet, remaining 5 `module_code` rows shifted to
   display_order 1-5, all ROOS rows removed from `tenant_module_access`
   sheet
7. `uv run python -m scripts.seed_dev_data --reset` runs cleanly
   against local Postgres
8. `uv run pytest -q` produces the expected pass count (operator's
   263 baseline minus any ROOS-specific tests removed). All passes
9. `uv run mypy src/admin_backend/` clean
10. Run `bash scripts/check_setup.sh`. Expected 35/35 per CLAUDE.md
    baseline
11. `core.module_code_enum` in local Postgres UNCHANGED (still 6
    values). Verify with `\\dT+ core.module_code_enum` or equivalent
12. NO Alembic migration created or modified
13. NO archived docs, DDL files, or `CLAUDE.md`/`BUILD_PLAN.md` edited

## Report before commit

1. List of files modified with line counts (per file)
2. Open question 1's resolution: which case (A/B/C) applied and what
   was done with `models/permission.py:10`
3. Open question 2's resolution: count of `ModuleCode.ROOS` references
   pre-edit and which file each was in
4. Open question 3's resolution: formula handling in the XLSX
5. Local test count delta: BEFORE → AFTER (e.g., 263 → 263 or 263 → 260
   if ROOS-specific tests were removed). Specifically which tests
   changed
6. Local seed loader output: row counts post-reseed for `lookups` and
   `tenant_module_access`. Compare against the EXPECTED_VISIBLE_COUNTS
   dict to confirm alignment
7. `core.module_code_enum` value list (still 6 values, verbatim)
8. `git diff --stat` showing all modified files. Confirm zero changes
   to migrations/, docs/archive/, db/raw_ddl/, CLAUDE.md, BUILD_PLAN.md
9. Failure-path test (optional but recommended): edit the XLSX, save,
   re-load, confirm the edit persists. If this fails, the XLSX save
   workflow has a bug

Wait for explicit operator authorisation before staging or committing.

## After committing

Propose the commit per CLAUDE.md "After completing a task" pattern.
This work touches Python + tests + scripts + seed; bundle as one
commit (logically a single "retire ROOS from module vocabulary"
change).

```
modules: retire ROOS from Python vocabulary and seed data

Removes ROOS from the Python module_code enum, all test fixtures,
the smoke test default, and the seed XLSX. Remaining 5 modules
shift up in lookups.display_order (2-6 → 1-5).

Python:
- src/admin_backend/schemas/modules_access.py: ROOS removed from
  Pydantic Literal
- src/admin_backend/models/tenant_module_access.py: ROOS removed
  from ModuleCode enum class
- src/admin_backend/models/permission.py: <description per Open Q1>

Tests:
- ModuleCode.ROOS references in 4 test files swapped to
  ModuleCode.GOAL_CONSOLE (semantic intent preserved — ROOS was used
  as a placeholder module, not for ROOS-specific behavior)
- _MODULE_DISPLAY_ORDER dict in test_rbac_router.py: ROOS key
  removed, others shifted up to 1-5
- test_seed_loader.py EXPECTED_VISIBLE_COUNTS adjusted by <N> for
  lookups and <M> for tenant_module_access

Scripts:
- scripts/smoke_test.py: default module argument flipped from ROOS
  to GOAL_CONSOLE; 3 call sites updated

Seed data:
- data/ithina_dev_seed_data.xlsx: ROOS row removed from `lookups`
  sheet (list_name='module_code'); remaining 5 rows shifted to
  display_order 1-5; <N> ROOS rows removed from
  `tenant_module_access` sheet

DB enum unchanged. core.module_code_enum still contains 6 values
including ROOS. The Python narrowing creates a one-way drift: any
ROOS row in the DB would crash Pydantic validation. Mitigated by
the cloud cleanup SQL (operator-run, separate from this commit):

  DELETE FROM core.tenant_module_access WHERE module = 'ROOS';
  DELETE FROM core.lookups WHERE list_name = 'module_code'
    AND code = 'ROOS';
  UPDATE core.lookups SET display_order = display_order - 1
    WHERE list_name = 'module_code' AND display_order > 1;

The DB enum ROOS value stays dormant until a future rename migration
ships when the replacement module name is decided. At that point
ROOS will be renamed via ALTER TYPE...RENAME VALUE.

Local: pytest <BEFORE> → <AFTER> all pass; mypy clean; seed loader
clean; alembic head 3e05299cb533 unchanged.

Cloud cleanup is the operator's next manual step. Frontend (Amit)
to be notified separately that module_code enum drops from 6 to 5.
```
