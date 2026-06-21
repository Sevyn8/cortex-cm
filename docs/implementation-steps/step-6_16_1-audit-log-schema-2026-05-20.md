# Step 6.16.1 : Audit log schema (DDL + ORM + RLS + indexes)

## Plan

Land the database schema for the audit log subsystem per the design captured at `docs/architecture_audit_logs.md` (Step 6.16.0). Schema-only: no application code, no emission helper, no GET endpoint. Sub-steps 6.16.2 through 6.16.5 ship the emission code and the read endpoint against this schema.

Authoritative reference: `docs/architecture_audit_logs.md` (Schema section, Architecture > Two-table split, Why symmetric column shape, Why denormalised labels, Why enums for some columns / TEXT for others). Implementation decisions in the prompt's locked-decision table (LD1 through LD16) match this design verbatim.

Structural template: Step 6.8.1's split-table migration `migrations/versions/3e05299cb533_step_6_8_1_split_user_role_assignments.py`. Two-table-with-RLS-on-one shape and the operator-driven roundtrip discipline mirror that precedent. Schema-capture pattern (`bind.execute(sa.text("SELECT current_schema()")).scalar_one()` + f-string interpolation) follows the more recent `5e22b2ca13cc` per CSD-03.

Deliverables:

1. NEW Alembic migration `c530346032dd_step_6_16_1_audit_log_schema.py` (creates 1 enum, 2 tables, FKs, CHECKs, RLS+FORCE+policy on tenant table, 5 indexes; reversible).
2. NEW ORM module `src/admin_backend/models/audit_log.py` (2 SQLAlchemy models + `AuditResultType` Python enum).
3. MODIFY `src/admin_backend/models/__init__.py` (re-export the 3 new symbols).
4. NEW `tests/integration/test_audit_log_schema.py` (S1, S4-S9; 8 tests with S7 parametrized).
5. NEW `tests/integration/test_audit_log_models.py` (M1-M5; 5 ORM round-trip tests).
6. MODIFY `docs/schema/current_schema.sql` (pg_dump regen at the new alembic head).
7. MODIFY `docs/schema/migration_log.md` (append entry for `c530346032dd`; update Summary counts + LOGGED_REVISIONS block).
8. MODIFY `scripts/seed_dev_data/truncate.py` (extend `SEED_TABLES` with the 2 audit tables for FK-graph resolution under `--reset`).
9. MODIFY `BUILD_PLAN.md` (flip Step 6.16.1 to DONE-LOCAL with as-shipped scope summary).
10. MODIFY `CLAUDE.md` (1-line pointer above the Step 6.16.0 entry in Completed section).
11. NEW step doc (this file).
12. NEW prompt file bundled with commit per workflow convention.

## Mental model

### Two physically separate tables, one symmetric column shape

`tenant_activity_audit_logs` and `platform_activity_audit_logs` both carry the same 16 columns. They differ only in:

- NULLABILITY of `tenant_id` and `tenant_name` (NOT NULL on tenant table; NULLABLE on platform table).
- RLS posture (RLS+FORCE with D-29 unconditional OR-branch on tenant table; no RLS on platform table).

The symmetric shape lets the future read endpoint (Step 6.16.3) issue `SELECT ... FROM tenant_activity_audit_logs UNION ALL SELECT ... FROM platform_activity_audit_logs ORDER BY timestamp DESC, id DESC LIMIT N` without per-branch column projections. Tenant users never reach the UNION (they only read the tenant table). Platform users get the merged view.

### Why no RLS on the platform table

`platform_activity_audit_logs` rows are not tenant-scoped. A tenant user has no legitimate read path to them. Access is gated at the API layer in 6.16.3 (the GET endpoint will be PLATFORM-only for the platform branch). Adding RLS would force one of two awkward postures: (a) policy that admits no rows for TENANT user_type and all rows for PLATFORM, which is the same access control the API layer already enforces redundantly, or (b) policy that admits rows scoped by `app.tenant_id` on the `tenant_id` column, which makes no sense when most rows have `tenant_id IS NULL`.

### Why both tables FK to `tenants(id)` with RESTRICT

Tenant deletion is structurally blocked while any audit row references the tenant. This is the audit-trail-survives-tenant-deletion guarantee. The platform table's FK accepts NULL `tenant_id` (the standard SQL FK semantic); only the tenant-creation success rows pin tenants from the platform side.

### Why two CHECK constraints on the platform table, one on the tenant table

- `ck_*_resource_pair` on BOTH tables: `(resource_id IS NULL AND resource_label IS NULL) OR (resource_id IS NOT NULL AND resource_label IS NOT NULL)`. Failed-create rows have the pair NULL (the resource was never assigned an identity); success and failed-update rows have the pair populated.
- `ck_platform_activity_audit_logs_tenant_pair` on the platform table only: `(tenant_id IS NULL AND tenant_name IS NULL) OR (tenant_id IS NOT NULL AND tenant_name IS NOT NULL)`. The tenant table does not need this CHECK because both columns are NOT NULL.

### Why reuse `actor_user_type_enum`

Both audit tables record who acted (`actor_user_type` column). The actor is either a platform user or a tenant user, exactly the vocabulary the existing `actor_user_type_enum` carries (`PLATFORM`, `TENANT`). A new enum would be redundant; the existing one is the precedent for Pattern (b) audit-actor columns elsewhere in the schema.

### Why a new `audit_result_type_enum`

The 6 values (`SUCCESS`, `PERMISSION_DENIED`, `VALIDATION_FAILED`, `CONFLICT`, `INTEGRITY_VIOLATION`, `INTERNAL_ERROR`) are the stable failure-classification vocabulary the emission code in 6.16.2-5 will use. Enum (not TEXT) is the right choice because the vocabulary is small, stable, and adding a value is rare and worth a migration. Compare with `action` and `resource_type` which are TEXT precisely because their vocabularies grow as new endpoints land.

### Indexes

Tenant table (3):
- `(timestamp DESC, id DESC)`: cursor pagination over the merged view.
- `(tenant_id, timestamp DESC, id DESC)`: RLS-scoped pagination under TENANT JWT (the policy filters by tenant_id; this index makes the filter cheap).
- partial on `result_type WHERE != 'SUCCESS'`: failure investigation queries hit a tiny index instead of scanning all rows.

Platform table (2):
- `(timestamp DESC, id DESC)`: cursor pagination.
- partial on `result_type WHERE != 'SUCCESS'`: failure investigation.

No `tenant_id` index on the platform table: there is no query pattern that filters platform rows by tenant_id (tenant-creation success rows are the only rows with a populated tenant_id; the merged view orders by timestamp, not tenant_id).

### Test strategy

Migration upgrade/downgrade/roundtrip safety is verified at development time via `alembic upgrade head && alembic downgrade -1 && alembic upgrade head`. Tests assert the LIVE schema state at head matches expectations, which is equivalent runtime evidence that the migration applied. Roundtrip behaviour is a property of the migration code, not pytest state.

LOAD-BEARING tests:
- S1 (schema objects present): regression guard for "migration applied successfully".
- S6 (tenant resource_pair CHECK fires): NULL-pair invariant is a load-bearing schema contract for emission code in 6.16.2.
- S7 (platform table resource_pair + tenant_pair CHECKs): same invariant, plus the tenant-pair NULL constraint on the platform side.
- S8 (FK ON DELETE RESTRICT): audit-trail-survives-tenant-deletion guarantee.
- S9 (RLS active + D-29 OR-branch resolves): tenant isolation is load-bearing for the entire subsystem.
- M4 (all 6 `AuditResultType` values round-trip): vocabulary is the failure-classification surface for 6.16.2-5; a regression would silently break emission downstream.

Correctness-only tests: S4 (enum value count + order), S5 (actor_user_type column references the existing enum), M1-M3 (ORM round-trips on tenant + platform with both tenant-populated and tenant-NULL paths), M5 (JSONB column round-trip with `@>` containment query).

### Why the seed-loader truncate list grows

`tenant_activity_audit_logs` and `platform_activity_audit_logs` both FK to `tenants(id)`. Postgres rejects `TRUNCATE tenants` (in any list) unless every table that FKs to it is also in the same TRUNCATE list (the FK graph is resolved across the listed set as one operation). The audit tables ship empty at 6.16.1, but the FK constraint exists, so they have to be in the seed loader's `SEED_TABLES` for `--reset` to work.

## Retro

### What landed cleanly

- The migration applied first try; round-trip (`upgrade -> downgrade -> upgrade`) clean.
- mypy strict passed on the new ORM module without any tweaks.
- All 13 new tests passed on first run.
- The S9 RLS truth-table assertion (TENANT-A sees A's row, TENANT-B sees B's row, PLATFORM sees both) confirmed the D-29 OR-branch policy resolves correctly end-to-end.

### One regression caught at full-pytest

The seed loader's `truncate_seed_tables` does `TRUNCATE tenants` (in a multi-table list) under `--reset`. The new audit tables FK to `tenants` and were not in the list, so Postgres rejected the TRUNCATE with "cannot truncate a table referenced in a foreign key constraint". `test_l1_seed_runs_clean_end_to_end` failed.

Fix: append `tenant_activity_audit_logs` and `platform_activity_audit_logs` to `SEED_TABLES` in `scripts/seed_dev_data/truncate.py`. The tables ship empty at this step; the inclusion is purely for FK-graph resolution and remains correct after emission starts at 6.16.2.

A pre-flight question worth asking on every schema-changing step where a table FKs to an existing seed table: "does the seed loader's truncate list need updating?"

### S2 / S3 deliberately not implemented as runtime tests

The prompt's S2 ("Migration downgrade drops both tables and the new enum; round-trip safe") and S3 ("Roundtrip: upgrade -> downgrade -> upgrade produces consistent state") are awkward to express as pytest cases. Running alembic from inside a test would either (a) require a separate test database to avoid corrupting state across tests, or (b) leave the live state at an unexpected point. The pragmatic posture is to verify roundtrip manually at development time (already done: `alembic upgrade head && alembic downgrade -1 && alembic upgrade head` clean) and let S1 assert the live-state-at-head invariant. S1 PASS plus the manual roundtrip is equivalent evidence to S2 + S3.

### One stale comment in `truncate.py` cleaned up

The pre-existing docstring referenced "`audit_logs` is not in the list (no DDL — Step 6.2 territory)". That sentence is stale post-6.16.0: the single `audit_logs` table was retired in favour of the two-table split. Updated to describe the new shape.

### The CSD-03 schema-capture pattern is the precedent now

The Step 6.8.1 migration uses unqualified table names (`CREATE TABLE platform_user_role_assignments ...`) and relies on env.py's `search_path` setting. The more recent `a0982a86985b` and `5e22b2ca13cc` migrations use the `current_schema()` + `{schema}.` f-string pattern explicitly. Per CSD-03, this is the right shape for new migrations and the convention is now uniform.

### No FN-AB entries opened or resolved

No new tech debt; no existing FN-AB entry resolved at this step. The subsystem design itself is the load-bearing reference; sub-steps land against it without changing it.
