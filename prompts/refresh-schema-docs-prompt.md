# Prompt: Generate / refresh schema reference documents

> Reusable prompt. Run from a fresh Claude Code session in the
> admin-backend repo root whenever new alembic migrations have
> landed since the last refresh.
>
> Idempotent: first run generates both files from scratch;
> subsequent runs append new migration entries to migration_log.md
> and regenerate current_schema.sql.

---

## Standing discipline (read first)

This prompt produces two schema reference documents in `docs/schema/`.
Both are docs-only; no code or test changes. pytest baseline must
stay unchanged.

The output files become source-of-truth references for the schema.
Treat every claim in them as load-bearing; surface uncertainty
rather than guessing.

Documentation writing: technical, sharp, concise. State facts,
active voice present tense, one sentence per fact. No
meta-commentary, no adjectives that don't add information.

No em-dashes anywhere in output (markdown or code comments). Use
commas, parentheses, colons, or sentence breaks per CLAUDE.md
convention.

**Voice for migration entries.** Match this concrete example:

```
## 5e22b2ca13cc : Step 6.20.3: RBAC structural enforcement triggers (2026-05-19)

Commit: 80ef73d

Added:
- Function `enforce_role_audience_scope_coherence()` (trigger
  function: rejects (TENANT-audience role x GLOBAL-scope permission)
  rows on `role_permissions`)
- Function `protect_super_admin_override_global_grant()`
  (trigger function: blocks DELETE of the SUPER_ADMIN x
  ADMIN.ROLES.OVERRIDE.GLOBAL grant row)
- Function `protect_super_admin_role()` (trigger function:
  blocks UPDATE OF status/code/audience or DELETE on the
  SUPER_ADMIN row in `roles`)
- Trigger `tg_role_permissions_audience_scope_coherence` (BEFORE
  INSERT OR UPDATE OF role_id, permission_id on `role_permissions`)
- Trigger `tg_role_permissions_protect_super_admin_override`
  (BEFORE DELETE on `role_permissions`)
- Trigger `tg_roles_protect_super_admin` (BEFORE UPDATE OR DELETE
  on `roles`)

Rationale: Closes 3 structural enforcement gaps for RBAC
invariants (audience-scope coherence + SUPER_ADMIN bootstrap
protection); defense in depth alongside app-layer checks.

---
```

Rules to mirror:
- Title line: `## <revision> : <step ID>: <short description> (<date>)`
- Use backticks for table/column/index/constraint/function/trigger names
- Strip schema-qualification prefixes (`core.` or `{schema}.`) from
  identifier names; the schema is documented in the file header
- Use plain English for actions (no "elegant", "cleanly enables",
  "robust"; state what changed and why)
- Rationale is ONE sentence, present tense
- Omit empty sections (no "Modified: (none)"; just skip the heading)

---

## Intent

Produce / update two documents:

1. **`docs/schema/migration_log.md`** : human-readable digest of
   every alembic migration. One section per revision. Captures
   Added / Modified / Removed + intent. The canonical "how did
   the schema evolve" reference.

2. **`docs/schema/current_schema.sql`** : pg_dump --schema-only
   snapshot of the live `core` schema. Fully regenerated each run;
   represents current ground truth.

Together these supersede the `Ithina_postgres_SQL_DDL_*.sql` files
as the authoritative schema reference (DDL files represent only
the initial bring-up state).

---

## Pre-flight

1. `git log --oneline -1`: confirm HEAD; note the SHA.
2. `git status`: expect clean working tree (or surface anything
   unexpected).
3. `uv run alembic heads`: note the current head revision.
4. `uv run alembic current`: note the DB's current revision.
4a. **Alembic-head / DB consistency check (HIGH-IMPORTANCE)**:
    Compare items 3 and 4. They MUST match.
    - If they differ: surface "DB is at revision X but alembic
      head is Y. Schema dump would capture inconsistent state."
    - Stop and ask operator to run `uv run alembic upgrade head`
      OR confirm the mismatch is intentional.
    - Do NOT proceed with pg_dump until matched. Capturing a
      mid-migration schema as source-of-truth is a critical bug.
5. `uv run pytest --tb=no -q | tail -3`: note the baseline
   (e.g., 689 passed). This is a docs-only commit; baseline must
   stay unchanged.
6. `ls -1 migrations/versions/*.py 2>/dev/null | wc -l`: count of
   migration files. (Note: this repo uses `migrations/versions/`,
   NOT `alembic/versions/`.)
7. Check existing schema docs:
   ```bash
   ls -la docs/schema/ 2>/dev/null
   ```
   - If `docs/schema/` doesn't exist: FIRST_RUN mode
   - If `docs/schema/migration_log.md` exists: APPEND mode
   - If `docs/schema/current_schema.sql` exists: always
     regenerated, never appended
8. Verify `pg_dump` is available AND capture its version:
   ```bash
   which pg_dump
   pg_dump --version
   ```
   Surface the version string. If missing, stop.
9. Verify `DATABASE_URL` is set: `echo "${DATABASE_URL:0:30}..."`.
   If empty, stop.
10. Capture Postgres server version (the database itself):
    ```bash
    psql "$DATABASE_URL" -c "SELECT version();" -t -A
    ```
    Surface the version string. If psql fails to connect, stop.
11. **Confirm DATABASE_URL points to LOCAL Postgres, not Cloud SQL.**
    The convention is: current_schema.sql reflects local DB at
    alembic head. Cloud SQL verification is operator-driven via
    the deployment workflow, NOT this prompt.

    Check by examining the host portion of DATABASE_URL. If the
    host looks like Cloud SQL (e.g., contains `googleusercontent`,
    a public IP, or `cloudsql` proxy), surface and stop. The
    operator may need to switch DATABASE_URL temporarily to local.

Surface results of all 11 items before proceeding.

---

## Mode detection

Decide MODE before any edits.

**FIRST_RUN**: `docs/schema/migration_log.md` does not exist.
Generate full digest from every migration in
`migrations/versions/*.py`. Create the structured revision index
block (see below).

**APPEND**: `docs/schema/migration_log.md` exists. Parse the
structured revision index block (NOT the markdown headings) to
identify already-logged revisions. Append entries for any
newer migrations.

### Structured revision index block

At the top of migration_log.md (after the Summary section),
include a machine-parseable index block:

```
<!-- LOGGED_REVISIONS_START -->
<!--
3e05299cb533
abc123def456
def456789012
...
-->
<!-- LOGGED_REVISIONS_END -->
```

This block contains every revision ID already logged in this
file, one per line, in chronological order.

**APPEND mode parses THIS block, not the markdown headings.**
Robust against heading formatting drift, manual edits to entries,
or new section types inserted between entries.

FIRST_RUN creates this block with all migrations listed.
APPEND mode appends new revisions to this list.

### APPEND mode integrity check (CRITICAL)

Before appending, compute:

```
current_alembic_revs = set of revisions in current alembic history
logged_revs           = set of revisions in LOGGED_REVISIONS block

missing_from_log      = current_alembic_revs - logged_revs
                        (migrations to append; EXPECTED)

missing_from_alembic  = logged_revs - current_alembic_revs
                        (revisions logged but no longer in alembic;
                         UNEXPECTED: migration was retired/deleted/
                         rebased)
```

**Decision logic:**

- `missing_from_log` non-empty AND `missing_from_alembic` empty:
  normal APPEND. Proceed.

- `missing_from_log` empty AND `missing_from_alembic` empty:
  No new migrations. Surface "No new migrations since last
  refresh; regenerating current_schema.sql only." Skip
  migration_log.md edits; proceed to pg_dump.

- `missing_from_alembic` non-empty (any size):
  Surface and STOP. Output the list of revisions in
  missing_from_alembic. Operator must decide:

  Option A: Treat as FIRST_RUN: delete migration_log.md and
            regenerate from scratch. Operator authorizes
            explicitly.
  Option B: Manually reconcile by editing the LOGGED_REVISIONS
            block. Operator confirms reconciled.

  Do NOT proceed automatically. Silent reconciliation could
  drop entries or duplicate them.

State the detected mode + integrity check result in the report.

---

## Step 1: Order migrations chronologically

Use alembic's Python API directly. Do NOT parse CLI output
(CLI format varies by version; programmatic API is stable).

```bash
uv run python << 'PYEOF'
from alembic.config import Config
from alembic.script import ScriptDirectory

cfg = Config('alembic.ini')
script = ScriptDirectory.from_config(cfg)

# walk_revisions() walks from head to base by default.
# We need oldest-first, so collect then reverse.
revisions = list(script.walk_revisions())
revisions.reverse()

for rev in revisions:
    # Pipe-delimited for easy parsing: revision|down_revision|file_path
    print(f"{rev.revision}|{rev.down_revision}|{rev.module.__file__}")
PYEOF
```

This produces one line per migration in oldest-first order:

```
<revision>|<down_revision>|<full_path_to_.py_file>
```

For each line, the revision ID is authoritative (taken from the
file's `revision = "..."` variable, NOT the filename prefix).

If the Python script errors out (e.g., alembic.ini missing,
revision chain broken), surface the exact error and stop.

### Migration file -> revision ID linkage

Each migration's revision ID comes from the Python API output
above (the `<revision>` field). This is authoritative; it
reads the file's `revision = "..."` variable internally.

Do NOT extract revision IDs from filenames. Filename prefixes
typically match but are not authoritative (filenames can be
renamed; the `revision` variable cannot be without breaking
the chain).

---

## Step 2: Extract per-migration metadata

For each migration file identified in Step 1 (FIRST_RUN: all;
APPEND: only those in `missing_from_log`), gather:

### Revision ID (authoritative source)

From the Python API output's `<revision>` field. Already
correct; no further extraction needed.

### Step ID extraction (with precedence)

Apply these precedence rules in order; use the FIRST match:

1. **Docstring match**: the migration file's module
   `"""docstring"""` first line. Pattern: `Step \d+\.\d+(\.\d+)*:`
   (e.g., "Step 6.9.1:" or "Step 6.8.2.1:").

2. **Git commit message match**: the commit message that
   introduced the file. Pattern: same as above.
   ```bash
   git log --diff-filter=A --follow --format=%s -- <file> | tail -1
   ```

3. **Filename message portion match**: the part of the filename
   after the revision ID. Pattern: same as above.

4. **Fall back** to literal string `(no step ID)`.

If sources 1 and 2 BOTH match but disagree (different step IDs),
use source 1 (docstring) and surface in the report:
```
WARNING: <revision> has step ID conflict: docstring says "X"
but commit message says "Y". Used docstring. Manual review
suggested.
```

### Date extraction with source attribution

```bash
git log --diff-filter=A --follow --format=%aI -- <file> | tail -1
```

Format the result as `YYYY-MM-DD`.

Track the source:

- If git log returns a date: date source is `git`
- If git log returns empty: fall back to:
  ```bash
  stat -c %y <file> 2>/dev/null | cut -d' ' -f1
  ```
  Date source is `mtime` (filesystem mtime; less reliable).
- If both fail: date is `unknown`; source is `unknown`.

For entries with date source != `git`, include an HTML comment
after the date to flag the source:

```
## <revision> : <step> (2026-05-04 <!-- source: mtime -->)
```

For git-sourced dates (the normal case), no comment is needed.

### Commit SHA

Short form (7 chars):

```bash
git log --diff-filter=A --follow --format=%H -- <file> | tail -1 | cut -c1-7
```

If no commit found, use `(not in git)`.

### Intent

From the file's module docstring (the `"""..."""` block at the
top, BELOW the standard alembic header comments). Paraphrase to a
single sentence; don't quote verbatim. Remove "Revises:" and
"Create Date:" lines if they exist.

---

## Step 3: Analyze upgrade() for changes

Read each migration's `upgrade()` function. Classify each
operation. Use the **mechanical rules** below; do not improvise
classifications.

### Added

- `op.create_table(name, ...)`: new table.
  - **Boilerplate trio rule**: if the table has exactly these 3
    columns with matching types/defaults:
    - `id` (uuid, default uuidv7())
    - `created_at` (timestamptz, default now())
    - `updated_at` (timestamptz, default now())

    list the table creation and any OTHER columns; add the
    parenthetical "(with standard `id`/`created_at`/`updated_at`)".

    If ANY of the 3 is missing, OR if any has a different
    type/default, list ALL columns including these. The rule
    requires exact match; no judgement.

- `op.add_column(table, col)`: new column on existing table.
- `op.create_index(name, table, columns, ...)`: new index.
- `op.create_unique_constraint(...)`: unique constraint.
- `op.create_check_constraint(...)`: check constraint.
- `op.create_foreign_key(...)`: foreign key.

### Modified

- `op.alter_column(...)`: state FROM and TO for whatever changed
  (type, default, nullable).
- `op.rename_table(...)`: state old and new names.
- Column renames via `op.alter_column(new_column_name=...)`.
- Constraint replacement (drop + create same-named constraint
  with different definition in the same migration).

### Removed

- `op.drop_table(...)`.
- `op.drop_column(...)`.
- `op.drop_index(...)`.
- `op.drop_constraint(...)`.
- Anything dropped without re-creation in the same migration.

### `op.execute()` raw SQL recognition

For each `op.execute("...")` call, scan the SQL string for these
patterns. **Multi-statement strings are SPLIT BY SEMICOLON and
each statement classified individually** (a single op.execute()
may produce multiple Added entries, e.g., a CREATE FUNCTION
followed by a CREATE TRIGGER in the same string).

**Auto-classifiable** (parse the SQL keyword and classify):

| SQL pattern                                   | Classification     | Entry under |
|-----------------------------------------------|--------------------|-------------|
| `CREATE POLICY`                               | RLS policy added   | Added       |
| `DROP POLICY`                                 | RLS policy removed | Removed     |
| `CREATE TRIGGER`                              | Trigger added      | Added       |
| `DROP TRIGGER`                                | Trigger removed    | Removed     |
| `CREATE FUNCTION` / `CREATE OR REPLACE FUNCTION` | Function added  | Added       |
| `DROP FUNCTION`                               | Function removed   | Removed     |
| `ALTER TYPE ... ADD VALUE`                    | Enum value added   | Added       |
| `ALTER TYPE ... RENAME VALUE`                 | Enum value renamed | Modified    |
| `CREATE INDEX`                                | Index added        | Added       |
| `DROP INDEX`                                  | Index removed      | Removed     |
| `CREATE SCHEMA`                               | Schema added       | Added       |
| `CREATE EXTENSION`                            | Extension added    | Added       |
| `ALTER TABLE ... ENABLE ROW LEVEL SECURITY`   | RLS enabled        | Added       |
| `ALTER TABLE ... FORCE ROW LEVEL SECURITY`    | (same, combine with above into one entry) | Added |

Format the entry in plain English. Examples:
- `op.execute("CREATE POLICY tenants_platform_read ON tenants ...")`
  yields "RLS policy `tenants_platform_read` added on `tenants`"
- `op.execute("CREATE OR REPLACE FUNCTION enforce_role_audience()...")`
  yields "Function `enforce_role_audience()` added (trigger function)"
- A single `op.execute()` string containing both a CREATE FUNCTION
  and a CREATE TRIGGER produces TWO Added entries (one per
  statement). Split on top-level semicolons; ignore semicolons
  inside `$$ ... $$` function bodies.

**Schema qualification handling.** Recent migrations use
`{schema}.` f-string interpolation (per CSD-03). When summarizing,
strip the `{schema}.` or `core.` prefix from displayed identifier
names; the schema is documented in the file header. Example: a
migration body containing `CREATE TRIGGER tg_x ON {schema}.roles`
becomes "Trigger `tg_x` added on `roles`".

**Not auto-classifiable**: surface for operator review:

- Conditional SQL (`DO $$ ... END $$` blocks) that do data
  validation or other procedural work
- Comments-only SQL (`COMMENT ON ...`)
- Data manipulation (INSERT/UPDATE/DELETE on existing rows)
- Any SQL pattern outside the table above

For not-classifiable cases, add an entry under a separate
"Other operations:" subsection:

```
Other operations:
- (op.execute, line X): <brief description>; verbatim SQL:
  `<first 100 chars of SQL>...`
```

Surface in the report list of migrations that have "Other
operations" entries; operator may want to refine these manually.

### `downgrade()` is NOT analyzed

Reading `upgrade()` alone produces the digest. `downgrade()` is
ignored unless `upgrade()` doesn't describe the change clearly
(rare). If a migration's `upgrade()` is empty but `downgrade()`
isn't, surface as an anomaly.

---

## Step 4: Write migration_log.md

### Per-entry format (mechanical)

Each entry has this EXACT structure:

```markdown
## <revision_id> : <step ID>: <short description> (<YYYY-MM-DD>)

Commit: <7-char SHA>

Added:
- <item 1>
- <item 2>

Modified:
- <item 1>

Removed:
- <item 1>

Other operations:
- <item 1>

Rationale: <one-line summary of intent, derived from docstring +
           commit message>

---
```

Rules (mechanical, no judgement):
- Omit any of the four operation sub-sections if empty (no
  "Added: (none)"; just skip the heading).
- All four operation sub-sections, when present, follow the same
  format: heading line, then bulleted list.
- Use backticks for table/column/index/constraint/function/trigger
  names.
- The trailing `---` separator is mandatory.

### FIRST_RUN: full file structure

Write `docs/schema/migration_log.md` with EXACTLY this structure:

```markdown
# Migration log

Authoritative digest of alembic migrations applied to the
admin-backend `core` schema. Each entry summarizes a single
revision's effect on the schema; together they describe how the
schema evolved from initial bring-up (Step 1.3 / 1.5 / 1.6) to
the current head.

For the current live schema state, see `current_schema.sql` in
this directory.

For the rationale behind any specific migration, see the
corresponding commit and the migration file itself in
`migrations/versions/`.

## Summary

Total migrations: <N>  
Latest revision: <revision_id>  
Last refresh: <YYYY-MM-DD>  
Generated by: `prompts/refresh-schema-docs-prompt.md`

<!-- LOGGED_REVISIONS_START -->
<!--
<oldest_revision>
<next_revision>
...
<latest_revision>
-->
<!-- LOGGED_REVISIONS_END -->

---

<entries in chronological order, oldest first, separated by `---`>
```

Format constraints (preserve exactly):
- The `## Summary` section has 4 lines of values, each ending
  with 2 trailing spaces (markdown line breaks)
- The `<!-- LOGGED_REVISIONS_START -->` and `END` markers are
  exact strings (parser depends on these)
- Revision IDs in the index block: one per line, between the
  `<!--` and `-->` HTML comment markers, chronological order
- A single `---` separator after the index block, before entries

### APPEND mode: targeted updates

DO NOT regenerate the whole file. Make exactly these changes:

1. **Update the Summary block values** (in-place replacement):
   - `Total migrations: <N>`: new total count
   - `Latest revision: <revision_id>`: new latest revision
   - `Last refresh: <YYYY-MM-DD>`: today's date

   Use exact-string replacement, preserving the surrounding format
   (4 lines, 2 trailing spaces each, exact heading text). If any
   of these 3 lines doesn't match the expected pattern, surface
   and stop; the file's structural integrity has drifted.

2. **Append new revision IDs to the LOGGED_REVISIONS block**:
   - Find the `<!-- LOGGED_REVISIONS_START -->` ...
     `<!-- LOGGED_REVISIONS_END -->` block
   - Inside the inner `<!-- ... -->` HTML comment, append the new
     revision IDs (from `missing_from_log` set) in chronological
     order, one per line, AFTER existing entries
   - Preserve all existing revisions verbatim

3. **Append new entries at the END of the file** (after the last
   existing entry):
   - In chronological order (oldest of the new ones first)
   - Same per-entry format as FIRST_RUN
   - Each entry ends with the `---` separator

Do NOT touch any existing entries. Preserve them verbatim,
including any manual edits the operator may have made.

---

## Step 5: Generate current_schema.sql

Always regenerate (don't append):

```bash
mkdir -p docs/schema

# Generate dump
pg_dump \
  --schema-only \
  --no-owner \
  --no-acl \
  --schema=core \
  "${DATABASE_URL}" > /tmp/schema_dump_raw.sql

# Prepend provenance header to the dump
cat > docs/schema/current_schema.sql << HEADER_EOF
-- ============================================================
-- core schema dump
-- ============================================================
-- Generated: $(date -u +%Y-%m-%dT%H:%M:%SZ)
-- 
-- pg_dump version:   <captured in pre-flight item 8>
-- Postgres version:  <captured in pre-flight item 10>
-- DATABASE_URL host: <redacted host portion, NOT credentials>
-- 
-- Alembic head:      <revision_id from pre-flight item 3>
-- Alembic current:   <revision_id from pre-flight item 4>
-- (head == current verified in pre-flight 4a)
--
-- This file represents the LOCAL Postgres schema at alembic head.
-- Cloud SQL is verified separately via operator deployment workflow.
-- 
-- Regenerated on each run of prompts/refresh-schema-docs-prompt.md;
-- git diff between runs shows schema deltas.
-- ============================================================

HEADER_EOF

# Append the actual dump
cat /tmp/schema_dump_raw.sql >> docs/schema/current_schema.sql

# Cleanup
rm /tmp/schema_dump_raw.sql

# Sanity-check
wc -l docs/schema/current_schema.sql
head -25 docs/schema/current_schema.sql
```

Substitute the placeholder values in the heredoc with the actual
versions captured in pre-flight. Redact `DATABASE_URL` to just
the host portion (no credentials).

If `pg_dump` errors out, surface and stop.
If the output file is empty or implausibly small (< 50 lines
beyond the provenance header), surface and stop.

### Size note

`current_schema.sql` is intentionally large (often thousands of
lines for substantial schemas). It's the canonical snapshot;
regenerated fresh each run, so git diffs naturally show schema
deltas between commits. No size optimization or truncation.

Optional (operator's call, NOT done by this prompt): add a
`.gitattributes` entry to mark the file as generated for GitHub
linguist:

```
docs/schema/current_schema.sql linguist-generated=true
```

This excludes it from language statistics; doesn't affect diff
behavior. Mention this option in the report only if the file is
genuinely large (> 5000 lines).

---

## Step 6: Verification

Run each check and surface pass/fail explicitly. ALL must pass
before proposing commit. If any fails, stop and report.

```
[V1] docs/schema/migration_log.md exists and is non-empty
     Command: ls -la docs/schema/migration_log.md
     Pass criteria: file exists, size > 0
     Status: PASS / FAIL

[V2] docs/schema/current_schema.sql exists and has provenance header
     Command: head -5 docs/schema/current_schema.sql
     Pass criteria: starts with "-- ===" provenance header lines
     Status: PASS / FAIL

[V3] migration_log.md heading count matches expected
     Command: grep -c "^## " docs/schema/migration_log.md
     Expected: (number of migrations) + 1 for "## Summary"
     Got: <X>
     Status: PASS / FAIL

[V4] Last migration entry's revision matches alembic head
     Command:
       expected: uv run alembic heads | head -1
       got: tail -50 docs/schema/migration_log.md | grep -oE "^## [a-f0-9]+" | tail -1
     Pass criteria: revisions match
     Status: PASS / FAIL

[V5] LOGGED_REVISIONS block count matches alembic history count
     Command:
       expected: <count from alembic Python API in Step 1>
       got: count of non-empty lines between LOGGED_REVISIONS_START/END markers
     Pass criteria: counts match
     Status: PASS / FAIL

[V6] current_schema.sql contains expected schema content
     Command: grep -c "^CREATE TABLE core\." docs/schema/current_schema.sql
     Pass criteria: count >= 1 (at least one core.<table> exists)
     Status: PASS / FAIL

[V7] pytest unchanged at baseline
     Command: uv run pytest --tb=no -q | tail -3
     Pass criteria: matches baseline from pre-flight item 5
     Status: PASS / FAIL

[V8] mypy strict clean
     Command: uv run mypy --strict src/admin_backend/ 2>&1 | tail -3
     Pass criteria: "Success: no issues found"
     Status: PASS / FAIL

[V9] git diff scope (only docs/schema/* modified)
     Command: git diff --stat
     Pass criteria: only files under docs/schema/ appear
     (plus prompts/refresh-schema-docs-prompt.md if FIRST_RUN and the
     prompt isn't already committed)
     Status: PASS / FAIL
```

Surface each item's PASS/FAIL with the actual values. ALL nine
must PASS before proceeding to commit.

---

## Step 7: Report and propose commit

Report:
1. Detected mode (FIRST_RUN or APPEND).
2. Pre-flight outputs (all 11 items).
3. Mode-detection integrity check result.
4. Number of migrations digested in this run:
   - FIRST_RUN: all
   - APPEND: only-new (size of `missing_from_log`)
5. List of revisions appended (APPEND mode only).
6. Verification harness output (V1-V9 with PASS/FAIL each).
7. Step ID conflicts (if any surfaced in Step 2).
8. "Other operations" entries (if any surfaced in Step 3).
9. Any anomalies (mtime-fallback dates, unmatched revision IDs,
   multi-statement op.execute() splits, etc).
10. Latest revision ID at the bottom of migration_log.md.

Then propose the commit.

### FIRST_RUN commit

Stages both schema files AND the prompt itself (so future readers
can find the reusable prompt that generated the docs):

```bash
git status
git add docs/schema/migration_log.md \
        docs/schema/current_schema.sql \
        prompts/refresh-schema-docs-prompt.md

git commit -m "docs/schema: bootstrap migration log + current schema dump

Adds two new schema reference documents:
- docs/schema/migration_log.md: human-readable digest of every
  alembic migration from base to current head, one section per
  revision with Added/Modified/Removed + intent
- docs/schema/current_schema.sql: pg_dump --schema-only of the
  core schema with provenance header; canonical snapshot of
  live state

Together these supersede the Ithina_postgres_SQL_DDL_*.sql files
as the authoritative schema reference. DDL files retain historical
value but represent only initial bring-up pre-migrations.

Bundles prompts/refresh-schema-docs-prompt.md: reusable Claude Code
prompt for regenerating these docs (FIRST_RUN bootstraps; APPEND
mode adds new migrations on subsequent runs).

Convention: schema-changing commits going forward use
prompts/refresh-schema-docs-prompt.md to regenerate.

Verification: pytest <N>/<N>, mypy clean, alembic head matches
DB state. Verified V1-V9 all PASS."
```

### APPEND commit

Stages only the schema files (prompt itself was bundled at
FIRST_RUN; only re-stage if the prompt was modified):

```bash
git status
git add docs/schema/migration_log.md docs/schema/current_schema.sql

# Substitute N (count of new migrations) and latest_revision_id:
git commit -m "docs/schema: log <N> new migrations through <latest_revision_id>

Appends <N> migration entries to docs/schema/migration_log.md and
regenerates docs/schema/current_schema.sql against current head.

Latest revision: <revision_id>
New revisions logged: <revision_id_1>, <revision_id_2>, ...

Verification: pytest <N>/<N>, mypy clean, alembic head matches
DB state. Verified V1-V9 all PASS."
```

### Edge case: only schema regeneration (no new migrations)

If APPEND mode detected zero new migrations (only
current_schema.sql changed):

```bash
git add docs/schema/current_schema.sql

git commit -m "docs/schema: regenerate current_schema.sql snapshot

No new migrations since last refresh. current_schema.sql
regenerated to reflect any non-migration schema changes
(typically: data-only or manual fixes that aren't expressed as
alembic migrations).

Latest revision: <revision_id> (unchanged from previous refresh).

Verification: pytest <N>/<N>, mypy clean. Verified V1-V9 all PASS
(V3 and V5 unchanged from previous; V4 still matches)."
```

Wait for explicit operator authorisation before staging or
committing.

---

## Surface-and-stop scenarios

Stop and report if any of these occur:

0. Any file referenced in this prompt's commands is missing or has
   unexpectedly different content than pre-flight checks revealed
   (e.g., alembic.ini missing, migrations/versions/ directory empty
   when expecting populated, docs/schema/migration_log.md exists but
   LOGGED_REVISIONS block is malformed). Stop. Report; do not proceed.

1. Pre-flight item 5 baseline (pytest) shows test failures.
2. Pre-flight item 4a: alembic current != alembic heads (DB and
   alembic out of sync; schema dump would capture inconsistent
   state).
3. Pre-flight item 11: DATABASE_URL looks like Cloud SQL, not
   local Postgres.
4. `pg_dump` errors out or produces an implausibly small file
   (< 50 lines beyond provenance header).
5. `DATABASE_URL` is not set or unreachable.
6. Step 1: alembic Python API errors out (alembic.ini missing,
   revision chain broken, etc.).
7. APPEND mode: `missing_from_alembic` is non-empty (revisions
   logged but no longer in alembic; migration retired/deleted/
   rebased).
8. APPEND mode: Summary block format has drifted from expected
   (can't do in-place value replacement safely).
9. Step ID conflict between docstring and commit message for any
   migration (use docstring, surface warning).
10. Any migration has "Other operations" entries that may need
    operator refinement.
11. Any verification check V1-V9 fails.
12. git diff scope includes files outside docs/schema/ (other
    than `prompts/refresh-schema-docs-prompt.md` on FIRST_RUN).

Treat surface-and-stop as the default disposition. Source-of-truth
files don't tolerate silent guesses.

---

## After completing: operator workflow

1. Review the report; verify each V1-V9 result matches expectation.
2. Authorise the commit.
3. `git push origin main` (when ready; not auto-done by this
   prompt).
4. Update Claude UI Project knowledge:
   - Upload regenerated `docs/schema/migration_log.md`
   - Upload regenerated `docs/schema/current_schema.sql`
   - On FIRST_RUN: also remove the older
     `Ithina_postgres_SQL_DDL_*.sql` files from project knowledge
     (they're superseded; remain in repo for historical reference)

---

## End of prompt
