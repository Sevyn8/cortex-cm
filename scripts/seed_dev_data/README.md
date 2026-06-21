# Dev seed loader

Loads `data/ithina_dev_seed_data.xlsx` into the configured Postgres
database. For dev/local environments only — refuses to run with
`ENVIRONMENT=production`.

## Usage

```bash
# Standard run (assumes DB exists and migrations applied):
uv run python -m scripts.seed_dev_data

# Re-seed (TRUNCATE then load):
uv run python -m scripts.seed_dev_data --reset

# Validate without writing:
uv run python -m scripts.seed_dev_data --dry-run

# Load specific sheets only:
uv run python -m scripts.seed_dev_data --sheets tenants,stores
```

## How it works

The Excel uses v4 UUIDs throughout. The loader honours **D-21**
(UUIDv7 invariant) by stripping IDs on insert, letting `DEFAULT
uuidv7()` fire, and capturing per-sheet `excel_id → db_id` mappings
via the `UUIDMapper`. Subsequent sheets resolve their FK references
through the mapper, so cross-sheet foreign keys end up pointing at
the new v7 ids the DB assigned.

Sheet load order matches FK dependency:

```
platform_users → tenants → org_nodes → stores → tenant_users →
roles → permissions → role_permissions →
user_role_assignments → tenant_module_access
```

`audit_logs` is skipped (no DDL — Step 6.2 territory). The Excel
sheet exists for reference only.

## Special-cased loaders

Most sheets use `_base.insert_and_register` directly (a small
mechanical loader file per sheet). Five sheets need bespoke handling:

- **`platform_users`** — self-referential audit (Anjali is `created_by`
  herself). Two-phase loader: Phase 1 inserts each row with NULL
  audit-actors; Phase 2 walks the rows again and `UPDATE`s the now-
  resolvable audit-actor FKs. The audit-actor columns are nullable in
  the DDL, which makes the two-phase shape clean.

- **`org_nodes`** — `parent_id` is a self-FK. The Excel row order does
  not guarantee topological ordering. Multi-pass loader: insert any
  row whose parent is NULL or already-mapped; defer the rest; repeat
  until done or no progress (cycle).

- **`role_permissions`** — junction table with composite PK
  `(role_id, permission_id)`; no `id` column. Bypasses
  `_base.insert_and_register` (which adds `RETURNING id`) and emits a
  plain INSERT; nothing to register in the mapper since no other sheet
  references it by id.

- **`user_role_assignments`** — sheet shape unchanged (one row per
  assignment; one of `platform_user_id` / `tenant_user_id`
  populated). Post Step 6.8.1 split (D-34), the loader inspects each
  row and routes it to one of two physical tables —
  `platform_user_role_assignments` (no RLS, no `tenant_id` /
  `org_node_id` columns) or `tenant_user_role_assignments`
  (RLS+FORCE, composite FKs to `tenant_users(tenant_id, id)` and
  `org_nodes(tenant_id, id)`). Per-row tenant impersonation is no
  longer needed: the unconditional D-29 OR-branch on
  `tenant_user_role_assignments` admits PLATFORM-session writes for
  any tenant. Audience-check triggers
  (`enforce_platform_role_audience`,
  `enforce_tenant_role_audience`) reject mismatched role audience at
  insert time.

- **`tenant_module_access`** — the seed Excel doesn't carry the three
  NOT NULL audit-actor FK columns the DDL requires. The loader
  synthesises them at load time by looking up Anjali (the seed's
  universal "system actor") by email in the live `platform_users`
  table and using her id for `enabled_by_user_id`,
  `created_by_user_id`, and `updated_by_user_id`. See CLAUDE.md
  "Note on seed Excel shape" for the captured convention.

## Reference data (`lookups`)

The `lookups` table is **not loaded by this script**. Lookup rows
are seeded by their owning Alembic migrations (Step 3.4.5 seeded the
`module_code` rows). Future lookup categories follow the same
pattern.

## Safety rails

- **Production refusal.** The CLI exits non-zero if
  `ENVIRONMENT=production`, before any DB connection is opened.
- **Drift detection.** `column_mappings.py` is the source of truth
  for every Excel column. A column in the Excel that's not in the
  mapping raises `UnknownColumnError`; data drift can't slip through
  silently.
- **No CASCADE.** `--reset` uses a single multi-table `TRUNCATE`
  statement, never `CASCADE`. (Postgres rejects `TRUNCATE foo` when
  *any* other table has an FK to `foo`, even when that referencing
  table is empty; listing all tables in one statement is the
  project-shaped solution.)

## Rollback

```bash
uv run python -m scripts.seed_dev_data --reset
```

This `TRUNCATE`s all 10 seed tables in one statement and then re-
seeds. To clear without re-seeding, run `--reset --sheets ''` (no
sheets selected) or `TRUNCATE` manually.

## Step 7.3.1 (post-v0)

This loader is the prototype for Step 7.3.1 — the customer-data
ingest tool that takes a partially-filled
`Ithina_data_entry_template.xlsx` and ingests one tenant's worth of
data. The customer-data tool will need richer error handling
(per-row errors, partial-load recovery, idempotent UPSERT). The
dev seed loader is "load what you can, fail loudly"; the customer
tool gets the more careful surface.
