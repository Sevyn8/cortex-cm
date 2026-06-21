# Step 6.20.3 : RBAC structural enforcement triggers

**Status.** DONE-LOCAL (2026-05-20).
**Owner.** CLAUDE_CODE (impl) + HUMAN (manual pre-check, Cloud SQL migration).
**Blocked by.** None.

## Mental Model

Three DDL triggers close structural enforcement gaps that app-layer
checks alone cannot guarantee against direct-SQL, seed-loader, or
future-endpoint bypass paths. Investigation 2026-05-19 surfaced the
core gap: Step 6.18.3 LD17's PATCH-side check rejects API attempts to
grant a GLOBAL-scope permission to a TENANT-audience role with a clean
422 envelope, but seed-loader bulk INSERTs, hand-written `psql`
sessions, and any future endpoint that omits the LD17 check would
write the forbidden row.

Two adjacent platform-bootstrap protections bundle into the same
commit because they share the architectural seam (RBAC catalogue
integrity guarded structurally rather than only by procedure):

1. **Audience-scope coherence** on `role_permissions`:
   `(TENANT-audience role x GLOBAL-scope permission)` rows are
   rejected on INSERT or UPDATE OF role_id/permission_id.
2. **OVERRIDE.GLOBAL last-row pin** on `role_permissions`:
   the `(SUPER_ADMIN, ADMIN.ROLES.OVERRIDE.GLOBAL)` grant cannot
   be deleted. Backstops Step 6.18.3 LD6/LD8 (the two-layer
   LAST_OVERRIDE_HOLDER invariant guarded at the API).
3. **SUPER_ADMIN role pin** on `roles`: status, code, and audience
   immutable; row cannot be deleted. Name and description remain
   editable (branding flexibility per LD3).

Defense-in-depth posture: the app-layer checks keep the user-facing
error envelope clean (422 / 409); the triggers ensure no bypass path
leaks. If a trigger ever fires from an API call path, it means an
app-layer check has a bug — the trigger is the pure tripwire (LD5;
mirrors Step 6.18.3 LD8 Layer 2 tripwire posture).

## Implementation Plan

### Locked decisions (operator-confirmed)

- **LD1.** Trigger 1 (TENANT x GLOBAL ban) mirrors `enforce_tenant_role_audience`.
- **LD2.** Trigger 2 (SUPER_ADMIN x OVERRIDE.GLOBAL pin) is DELETE-only.
  UPDATE-OF-role_id/permission_id rename vectors not covered (deliberate;
  renames are operator-deliberate actions, not bypass paths).
- **LD3.** Trigger 3 (SUPER_ADMIN role pin) fires on UPDATE OF status,
  code, audience OR DELETE. Name and description remain editable.
- **LD4.** No pre-check assertion in migration. Operator manually
  verified zero (TENANT × GLOBAL) violations in seed Excel + local DB +
  Cloud SQL (pre-flight Check #3 returned 0).
- **LD5.** No app-layer catch for Trigger 1. Pure tripwire pattern.
- **LD6.** raw_ddl/Ithina_postgres_SQL_DDL_rbac_v3.sql kept in sync.
  Append trigger DDL at appropriate locations (unqualified, matching
  surrounding precedent).
- **LD7 (superseded).** ERRCODE = '23514' was specified in the prompt
  but pre-flight Check #9 showed the existing trigger precedent uses
  plain `RAISE EXCEPTION` with default SQLSTATE P0001. Mirrored
  precedent. LD7 corrected to "default SQLSTATE P0001". Operator
  authorisation 2026-05-20.
- **LD8.** Step doc per A6 (Mental Model + Implementation Plan +
  Retro). This file.

### File changes

| File | Change | Notes |
|---|---|---|
| `migrations/versions/5e22b2ca13cc_step_6_20_3_rbac_structural_triggers.py` | NEW | 3 CREATE FUNCTION + 3 CREATE TRIGGER blocks. Reversible downgrade. Schema captured via `current_schema()` per `a0982a86985b` precedent. |
| `db/raw_ddl/Ithina_postgres_SQL_DDL_rbac_v3.sql` | MODIFY | 3 trigger DDL blocks appended adjacent to the target tables. Unqualified identifiers (matches surrounding precedent at lines 421-439 / 581-599). |
| `tests/integration/test_rbac_audience_scope_triggers.py` | NEW | 17 DB-direct tests (12 LOAD-BEARING). |
| `BUILD_PLAN.md` | MODIFY | Step 6.20.3 sub-block under "6.20 Bug Fixes" header, after 6.20.2. |
| `CLAUDE.md` | MODIFY | One-line Completed pointer + FN-AB-62 (deferred AI-RBAC-01 comment amendment). |
| `docs/implementation-steps/step-6_20_3-role-audience-scope-trigger-2026-05-19.md` | NEW | This file. |

### Migration shape

```python
def upgrade() -> None:
    bind = op.get_bind()
    schema = bind.execute(sa.text("SELECT current_schema()")).scalar_one()
    # 3 op.execute(f"CREATE OR REPLACE FUNCTION {schema}.<name>() ...")
    # 3 op.execute(f"CREATE TRIGGER tg_<table>_<purpose> ... ON {schema}.<table> ...")

def downgrade() -> None:
    bind = op.get_bind()
    schema = bind.execute(sa.text("SELECT current_schema()")).scalar_one()
    # 3 op.execute("DROP TRIGGER IF EXISTS tg_... ON ...")
    # 3 op.execute("DROP FUNCTION IF EXISTS ...")
```

Function names:
- `enforce_role_audience_scope_coherence()`
- `protect_super_admin_override_global_grant()`
- `protect_super_admin_role()`

Trigger names:
- `tg_role_permissions_audience_scope_coherence`
- `tg_role_permissions_protect_super_admin_override`
- `tg_roles_protect_super_admin`

### Test catalogue

| ID | What it asserts | Load-bearing? |
|---|---|---|
| T1 | INSERT (TENANT role x GLOBAL perm) → ProgrammingError | yes |
| T2 | INSERT (TENANT role x TENANT perm) → succeeds | yes |
| T3 | INSERT (TENANT role x STORE perm) → succeeds | |
| T4 | INSERT (PLATFORM role x GLOBAL perm) → succeeds | yes |
| T5 | UPDATE role_id of existing row from PLATFORM to TENANT (perm is GLOBAL) → ProgrammingError | yes |
| T6 | UPDATE permission_id of existing row to GLOBAL under TENANT role → ProgrammingError | yes |
| T7 | UPDATE audit columns only (created_by_user_id) → no trigger fire → succeeds | |
| T8 | DELETE (SUPER_ADMIN x OVERRIDE.GLOBAL) row → ProgrammingError | yes |
| T9 | DELETE other (SUPER_ADMIN x other perm) row → succeeds | yes |
| T10 | DELETE (non-SUPER_ADMIN x OVERRIDE.GLOBAL) row → succeeds | |
| T11 | UPDATE SUPER_ADMIN role SET status → ProgrammingError | yes |
| T12 | UPDATE SUPER_ADMIN role SET code → ProgrammingError | yes |
| T13 | UPDATE SUPER_ADMIN role SET audience → ProgrammingError | yes |
| T14 | UPDATE SUPER_ADMIN role SET name → succeeds | yes |
| T15 | UPDATE SUPER_ADMIN role SET description → succeeds | |
| T16 | DELETE SUPER_ADMIN role → ProgrammingError | yes |
| T17 | UPDATE non-SUPER_ADMIN role SET status → succeeds | |

**Load-bearing count: 12.**

### Trigger error class

plpgsql `RAISE EXCEPTION` with default SQLSTATE P0001 wraps as
`sqlalchemy.exc.ProgrammingError` (verified empirically via direct
psycopg3 + SQLAlchemy 2.x against local Postgres 15). NOT
`IntegrityError`. The prompt's "IntegrityError" wording was a
convention error and is corrected to `ProgrammingError` in the new
test file.

If a future migration ever moves the existing `enforce_*_role_audience`
trigger pattern to use `USING ERRCODE = '23514'` (SQLSTATE
23514 = check_violation, integrity-constraint class), SQLAlchemy
would wrap those raises as `IntegrityError`. Out of scope here; would
be a separate cleanup commit covering all 5 RBAC triggers
consistently.

### Verification harness (post-impl)

```bash
# Migration applies cleanly
uv run alembic upgrade head

# 17 trigger tests pass
uv run pytest tests/integration/test_rbac_audience_scope_triggers.py -v

# Full suite regression: 672 -> 689
uv run pytest --tb=no -q

# mypy strict
uv run mypy --strict src/admin_backend

# check_setup
./scripts/check_setup.sh

# Live trigger inspection
psql -c "
SELECT trigger_name, event_manipulation, action_timing, event_object_table
FROM information_schema.triggers
WHERE trigger_schema = 'core'
  AND (trigger_name LIKE 'tg_role_permissions_audience%'
       OR trigger_name LIKE 'tg_role_permissions_protect_super%'
       OR trigger_name LIKE 'tg_roles_protect_super%')
ORDER BY event_object_table, trigger_name, event_manipulation;
"

# Migration round-trip
uv run alembic downgrade -1
uv run alembic upgrade head
```

## Retro

### What landed

- Migration `5e22b2ca13cc` (down_revision `a0982a86985b`).
  Applies + downgrades + re-applies cleanly. Three triggers visible
  in `information_schema.triggers` post-upgrade (5 rows because
  Trigger 1 carries both INSERT and UPDATE rows; Trigger 3 carries
  UPDATE and DELETE rows).
- Raw DDL append to `rbac_v3.sql`:
  - Trigger 3 inline after `tg_roles_set_updated_at` block (the
    `roles` table section).
  - Triggers 1+2 inline after `ix_role_permissions_permission` (the
    `role_permissions` table section).
  - Unqualified identifiers, matching the surrounding
    `enforce_platform_role_audience` / `enforce_tenant_role_audience`
    style.
- 17 tests, 17 passing (T1-T17). 12 LOAD-BEARING.
- Full suite 672 → 689 (+17). 0 fails, 0 xfails.
- mypy strict clean (76 source files; was reported as 73 in CLAUDE.md
  but had drifted; no source files added by this step — the count
  delta was pre-existing).
- check_setup 36/36.

### Deviations from the original prompt

Three corrections applied via operator authorisation before edits
began:

- **D1.** Prompt cited `alembic/versions/`; repo uses
  `migrations/versions/`. Honored repo convention.
- **D2.** Prompt's SQL hardcoded `core.` schema. Repo convention
  (per `a0982a86985b` and CSD-03) is `current_schema()` capture +
  `{schema}.` f-string interpolation in the migration; raw_ddl
  identifiers stay unqualified.
- **D3.** Prompt LD7 specified `USING ERRCODE = '23514'`. Pre-flight
  Check #9 showed the existing trigger precedent uses neither ERRCODE
  nor an integrity-class SQLSTATE — plain `RAISE EXCEPTION` with
  P0001. Mirrored precedent; LD7 superseded.

Plus one in-band correction during implementation:

- **Test exception class.** Prompt's test-catalogue language used
  "IntegrityError"; empirical check showed SQLAlchemy wraps P0001
  raises as `ProgrammingError`. Tests use `ProgrammingError`.

### Adjacent improvement deferred

The application-layer-invariants comment block in `rbac_v3.sql` (the
AI-RBAC-01 entry) still describes the (TENANT × GLOBAL) ban as
"app-layer pre-check" only. With this step's DDL trigger landing, the
invariant is now app-layer AND DDL-enforced. The comment can be
amended to reflect the two-layer shape. Operator chose to defer
(left optional unless triggered); captured as FN-AB-62 so the
improvement isn't lost.

### Cloud deploy posture

DDL change. Migration runs via the standard `--migrate` deploy path
on next Cloud SQL deploy. Operator pre-step (per LD4): verify zero
(TENANT × GLOBAL) rows in Cloud SQL before applying. Local has zero
violations (pre-flight Check #3). Cloud DB count must be verified
manually before deploy.

Batched with 6.18.2 + 6.18.3 + 6.20.2 + 6.20.3 at next Phase 6 deploy.

### What did NOT change

- App-layer code: zero changes. Step 6.18.3 LD17 PATCH check at W22
  unchanged.
- Existing audience-check triggers (`enforce_platform_role_audience`,
  `enforce_tenant_role_audience`): unchanged.
- Existing role_permissions CHECK constraints: unchanged.
- Seed Excel: unchanged (no permission row changes needed).
- Test count for any pre-existing test file: unchanged.
