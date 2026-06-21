# Investigation: Step 6.18 (Role Edit) : current-state confirmation

**Date drafted:** 2026-05-18 (revision 2)
**Investigator:** Claude Code
**Output:** single chat reply with structured findings; no source edits

This is a **read-only investigation**. Do not edit any source file. Do not write any new file in src/, tests/, scripts/, or docs/. Output is a single chat reply enumerating findings. The operator and chat will use the findings to scope Step 6.18 (role-edit feature) accurately.

The feature being scoped: a new `PATCH /api/v1/roles/{role_id}` allowing SUPER_ADMIN to edit a role's name and replace-set its permissions, plus a new `GET /api/v1/roles/{role_id}` detail endpoint (does not exist today per openapi.json enumeration; ships as a sub-step before PATCH). Gated by a new `ADMIN.ROLES.OVERRIDE.GLOBAL` permission (does not exist in catalogue per operator). Invariant: at least one ACTIVE user must hold `ADMIN.ROLES.OVERRIDE.GLOBAL` after the edit, otherwise 409 LAST_OVERRIDE_HOLDER.

## Ground-truth facts established before this investigation (do not re-verify)

These were already confirmed at draft time via direct read of `docs/endpoints/openapi.json`:

- Roles/permissions endpoints currently registered:
  - `GET /api/v1/roles` returns `RoleListResponse` (audience-grouped: platform_roles + tenant_roles)
  - `GET /api/v1/roles/{role_id}/permissions` returns `RolePermissionsResponse`
  - `GET /api/v1/permissions` returns `PermissionListResponse`
  - `GET /api/v1/permission-matrix` returns `PermissionMatrixResponse`
  - `GET /api/v1/me/permissions` returns `MePermissionsResponse`
  - `GET /api/v1/role-assignments` returns `RoleAssignmentsResponse`
- `GET /api/v1/roles/{role_id}` (detail) does NOT exist today.
- `RoleListItem` schema includes an `is_system: boolean` field.
- `RoleAudience` enum has values `PLATFORM` and `TENANT`.

This investigation focuses on what openapi.json alone does NOT reveal: gate posture, source code structure, DDL details, catalogue state, and runtime semantics of `is_system`.

## Standing discipline

- **A8 cite-or-verify, applied to investigation findings**: every finding cites a file:line, a SQL query result, an openapi.json schema reference, or an explicit "not found, searched these paths". No claims from memory or inference. The investigation report is itself an A8-compliant document.
- **Contradiction-surfacing license**: if any of the operator's working assumptions (listed below) turn out to disagree with the codebase, surface the contradiction with the conflicting evidence. Do not silently confirm a wrong assumption.
- **No code changes.** This is investigation only. If the investigation surfaces a bug or a stale doc reference, note it as a finding for follow-up; do NOT fix it.

## Working assumptions from operator (verify each)

1. `ADMIN.ROLES.OVERRIDE.GLOBAL` does NOT exist in seed Excel or local DB today (operator stated; Cloud SQL similarly confirmed by operator).
2. SUPER_ADMIN is the only role intended to hold `ADMIN.ROLES.OVERRIDE.GLOBAL` initially (operator decision).
3. Role count in `core.roles` is approximately 15 (session memory; investigation confirms actual count).
4. Permission count in `core.permissions` is approximately 33-35 depending on which 6.17.1 catalogue delta has applied (session memory; investigation confirms actual count post-6.17 series).
5. `core.role_permissions` is a join table linking `role_id` and `permission_id` (no embedded audit columns expected; investigation confirms).
6. `core.tenant_user_role_assignments` and `core.platform_user_role_assignments` carry user-to-role assignments with a user `status` accessible via JOIN to the parent user tables `core.tenant_users` and `core.platform_users`.

Verify each as a separate finding. If any assumption is wrong, surface the conflicting evidence.

## Investigation buckets

### Bucket 1: Existing GET /roles and GET /roles/{role_id}/permissions implementation

**1a. Locate the router file**

```
grep -rn "@router.get\b" src/admin_backend/routers/v1/ | grep -i role
```

Or list every roles-related route registered in `main.py`:
```
grep -n "roles\|rbac" src/admin_backend/main.py
```

Report the router file path(s) and the lines where each handler is defined.

**1b. `GET /api/v1/roles` (list) handler details**

Read the handler. Report:
- The exact `Depends(require(...))` gate tuple, including `audience` if present.
- The session dependency: `get_tenant_session_dep` (multi-audience) or `get_platform_session_dep` (PLATFORM-only) or something else.
- The repo method called and its signature.
- Whether the handler maps from a repo row type to the response schema explicitly (helper like `_list_item_from_row`) or whether the repo returns the response shape directly.

**1c. `GET /api/v1/roles/{role_id}/permissions` (per-role permissions) handler details**

Same read. Specifically report:
- The gate tuple.
- Whether the handler validates the role_id exists separately (anchor dep?) or relies on the repo returning 404 on miss.
- Whether `is_system` is checked anywhere in the handler.

**1d. Response schemas: deep read**

Read the role schemas file (likely `src/admin_backend/schemas/role.py`; confirm path). Report:
- All schema classes defined in the file with their full field lists (name, type, optional vs required, validators).
- Specifically: `RoleListItem`, `RoleListResponse`, `AudienceBlock`, `RolePermissionsResponse`, `PermissionRead`, and any others adjacent.
- Whether any of these schemas has `ConfigDict(extra="forbid")` or equivalent.

**1e. The `is_system` field: behavioural verification**

This is load-bearing for the design. Three sub-questions:

(i) Where is `is_system` defined in source?
```
grep -rn "is_system" src/admin_backend/
```

(ii) What populates `is_system`? Is it a hardcoded list, a DDL column, a derived value?
- Check the `Role` ORM model (likely `src/admin_backend/models/role.py`; confirm).
- If it's a DDL column: report the DDL constraint (`NOT NULL`, default value).
- If it's derived from a hardcoded list: report the list and its location.

(iii) Where is `is_system` consumed?
- Search for code that reads `role.is_system` or `is_system=True` or `WHERE is_system` or `is_system =`.
- Surface every consumer: tests, gate logic, validators, repo methods.
- Critical: does anything REJECT operations on `is_system=True` roles today? If yes, where? If no, the field is informational only.

Report: is_system is (informational only / enforced at handler X / enforced at DDL / enforced at repo) with evidence.

### Bucket 2: Catalogue state

**2a. Confirm `ADMIN.ROLES.OVERRIDE.GLOBAL` absent locally**

Local DB:
```
psql "${DATABASE_URL/postgresql+psycopg/postgresql}" -c "
SELECT code, resource, action, scope FROM core.permissions
WHERE code = 'ADMIN.ROLES.OVERRIDE.GLOBAL';
"
```

Excel seed:
```
python3 -c "
import openpyxl
wb = openpyxl.load_workbook('data/ithina_dev_seed_data.xlsx', read_only=True)
ws = wb['permissions']
rows = list(ws.iter_rows(values_only=True))
header = rows[0]
code_col = header.index('code')
for row in rows[1:]:
    if row[code_col] and 'ADMIN.ROLES.OVERRIDE' in str(row[code_col]):
        print(row)
"
```

Expected: zero rows in both. Surface if found.

Note: Cloud SQL state is the operator's responsibility to confirm (operator stated absent). Local + Excel coverage in this investigation is sufficient.

**2b. List all ADMIN.ROLES.* permissions currently in catalogue**

```
psql "${DATABASE_URL/postgresql+psycopg/postgresql}" -c "
SELECT code, id FROM core.permissions
WHERE code LIKE 'ADMIN.ROLES.%'
ORDER BY code;
"
```

Report all rows. This establishes what's adjacent to the new permission for the seed delta sub-step (6.18.1).

**2c. List all OVERRIDE-action permissions in catalogue**

```
psql "${DATABASE_URL/postgresql+psycopg/postgresql}" -c "
SELECT code, id FROM core.permissions
WHERE action = 'OVERRIDE'
ORDER BY code;
"
```

Report all rows. This establishes the existing OVERRIDE topology.

**2d. Currently who holds OVERRIDE-action permissions?**

```
psql "${DATABASE_URL/postgresql+psycopg/postgresql}" -c "
SELECT p.code, r.id AS role_id, r.code AS role_code
FROM core.permissions p
JOIN core.role_permissions rp ON rp.permission_id = p.id
JOIN core.roles r ON r.id = rp.role_id
WHERE p.action = 'OVERRIDE'
ORDER BY p.code, r.code;
"
```

Report all rows. Establishes existing privilege topology.

### Bucket 3: Roles structure

**3a. Confirm role count and full role list**

```
psql "${DATABASE_URL/postgresql+psycopg/postgresql}" -c "
SELECT id, code, name, audience, is_system FROM core.roles ORDER BY audience, code;
"
```

Report all rows. The `audience` column should exist (openapi.json shows `RoleAudience` enum with values `PLATFORM`, `TENANT`). The `is_system` column may or may not exist as a DDL column; if the SELECT errors on it, that tells us `is_system` is computed elsewhere : surface the error and re-run without `is_system`.

**3b. `core.roles` DDL**

```
psql "${DATABASE_URL/postgresql+psycopg/postgresql}" -c "\d+ core.roles"
```

Report:
- All columns with type, nullable, default.
- All CHECK constraints.
- All foreign keys.
- All indexes.
- Any trigger definitions.

Watch specifically for: whether `is_system` is a real DDL column, audit-actor columns (`created_by_*`, `updated_by_*`), CHECK constraints on `name` and `code` format.

**3c. `core.role_permissions` DDL**

```
psql "${DATABASE_URL/postgresql+psycopg/postgresql}" -c "\d+ core.role_permissions"
```

Report the join structure: composite PK on `(role_id, permission_id)` or surrogate id? Any other columns (`created_at`, `granted_by_user_id`)? Any FK cascade behavior on role or permission deletion?

This matters because PATCH replace-set means INSERT/DELETE in this table; we need to know whether each row carries columns that we need to populate.

### Bucket 4: RolesRepo

**4a. Does a `RolesRepo` class exist?**

```
grep -rn "class RolesRepo\b" src/admin_backend/repositories/
```

If yes, report the file path and confirm.

**4b. If RolesRepo exists, list all public method signatures**

Read the file. Report:
- All public method names, full signatures (kwargs, types, return type).
- The SQL style: ORM, raw `text()`, joined?
- Whether it joins to `role_permissions` and `permissions` when reading roles (the per-role permissions endpoint must do this).
- Whether there's already an `update`, `patch`, or `rename` method that this step would extend.

**4c. RolesRepo consumers across the codebase**

```
grep -rn "RolesRepo\b\|from admin_backend.repositories.roles\b\|from admin_backend.repositories import roles\b" src/ tests/
```

Report consumers. This identifies regression risk: any handler/test depending on the repo's current shape will need to be re-verified after we extend it.

### Bucket 5: rbac / permission-matrix surface gate posture

The investigation already confirmed via openapi.json that `GET /api/v1/permissions`, `/permission-matrix`, `/me/permissions`, `/role-assignments` exist. Now confirm their gate tuples.

For each of these endpoints, read the handler and report:
- The exact `Depends(require(...))` gate tuple including `audience`.
- Whether it's multi-audience or PLATFORM-only.

```
grep -rn "@router.get" src/admin_backend/routers/v1/ | grep -E "permissions|permission-matrix|role-assignments|me"
```

Critical: which of these does a TENANT-tier user (OWNER) currently access today? The answer tells us whether the frontend's role-edit UI is reachable from a TENANT context at all, which affects how 6.18 is positioned (PLATFORM-only feature that lives at a PLATFORM-only URL prefix vs PLATFORM-only PATCH that sits next to multi-audience GETs).

### Bucket 6: User assignment tables and invariant query dry-run

**6a. `core.tenant_user_role_assignments` and `core.platform_user_role_assignments` shape**

```
psql "${DATABASE_URL/postgresql+psycopg/postgresql}" -c "\d+ core.tenant_user_role_assignments"
psql "${DATABASE_URL/postgresql+psycopg/postgresql}" -c "\d+ core.platform_user_role_assignments"
```

Report columns, FKs, indexes. Specifically need to identify:
- The `user_id` column name (or whatever it's called) in each table.
- Whether the assignment itself has a status column (some systems have "assignment is suspended" independent of user status).
- The FK to the parent user table (so we know how to JOIN to get user status).

**6b. User status enum**

The enum names are unknown at draft time. Discover via:
```
psql "${DATABASE_URL/postgresql+psycopg/postgresql}" -c "
SELECT t.typname, e.enumlabel
FROM pg_type t
JOIN pg_enum e ON e.enumtypid = t.oid
WHERE t.typname LIKE '%user_status%'
ORDER BY t.typname, e.enumsortorder;
"
```

Report all status enums and their labels. The invariant query uses ACTIVE; confirm the actual enum label for "active user" (might be `ACTIVE`, `active`, something else).

**6c. Invariant query dry-run**

Run the proposed invariant query against an existing OVERRIDE-action permission as a stand-in for `ADMIN.ROLES.OVERRIDE.GLOBAL` (which doesn't exist yet). First pick an existing OVERRIDE permission from Bucket 2c (one that has actual holders per Bucket 2d).

Then run, substituting the actual column names found in Bucket 6a and the actual ACTIVE enum label from Bucket 6b:

```
psql "${DATABASE_URL/postgresql+psycopg/postgresql}" -c "
WITH override_role_ids AS (
    SELECT rp.role_id
    FROM core.role_permissions rp
    JOIN core.permissions p ON p.id = rp.permission_id
    WHERE p.code = '<existing_override_code_from_bucket_2c>'
)
SELECT COUNT(DISTINCT user_id) AS active_holders FROM (
    SELECT pura.<user_id_column> AS user_id
    FROM core.platform_user_role_assignments pura
    JOIN override_role_ids ori ON ori.role_id = pura.role_id
    JOIN core.platform_users pu ON pu.id = pura.<user_id_column>
    WHERE pu.status = '<active_label>'
    UNION
    SELECT tura.<user_id_column> AS user_id
    FROM core.tenant_user_role_assignments tura
    JOIN override_role_ids ori ON ori.role_id = tura.role_id
    JOIN core.tenant_users tu ON tu.id = tura.<user_id_column>
    WHERE tu.status = '<active_label>'
) holders;
"
```

Report:
- Which existing OVERRIDE permission was used as the stand-in.
- The actual SQL run (with substitutions filled in).
- The count returned.
- Any SQL errors verbatim (column not found, enum mismatch, etc.). If errors, propose adjustments based on what was actually found.

This is the load-bearing pre-design check. If the invariant query has a bug, we want to know now, not in 6.18.3's PATCH implementation.

### Bucket 7: Codebase observations beyond scope

FYI items noticed while running the above. Anything that may matter for Phase 1 / Phase 2 design but isn't in the explicit bucket scope:

- Stale references in CLAUDE.md or BUILD_PLAN.md about role editing or roles in general.
- Inconsistencies in how other resources handle similar operations.
- Existing tests adjacent to role read or assignment that may need updates when PATCH ships.
- Any FN-AB forward note adjacent to role editing.
- Anything else.

## Output format

Single chat reply with these sections, each populated with evidence:

```
# Investigation report: Step 6.18 (Role Edit)

## Working assumptions verification
- Assumption 1 (ADMIN.ROLES.OVERRIDE.GLOBAL absent): confirmed / contradicted (evidence)
- Assumption 2 (SUPER_ADMIN holds new perm): N/A (perm doesn't exist yet; verify in 6.18.1 seed)
- Assumption 3 (~15 roles): actual count = N (evidence)
- Assumption 4 (~33-35 perms): actual count = N (evidence)
- Assumption 5 (role_permissions is simple join): confirmed / contradicted (evidence)
- Assumption 6 (user status JOIN works): confirmed via 6c dry-run / contradicted

## Bucket 1: GET /roles and GET /roles/{role_id}/permissions
- 1a: router file at <path>; handlers at <lines>
- 1b: GET /roles gate tuple + session + repo method
- 1c: GET /roles/{role_id}/permissions gate tuple + session + repo method
- 1d: schema definitions for RoleListItem, RoleListResponse, AudienceBlock, RolePermissionsResponse, PermissionRead, ...
- 1e: is_system findings:
    (i) defined at: <file:line>
    (ii) populated by: DDL column / hardcoded list / derived
    (iii) consumed at: <list of consumers> OR "informational only, no enforcement found"

## Bucket 2: Catalogue state
- 2a: ADMIN.ROLES.OVERRIDE.GLOBAL absent confirmation
- 2b: all ADMIN.ROLES.* permissions
- 2c: all OVERRIDE-action permissions
- 2d: current OVERRIDE holders

## Bucket 3: Roles structure
- 3a: role list with audience + is_system columns
- 3b: roles DDL
- 3c: role_permissions DDL

## Bucket 4: RolesRepo
- 4a: existence
- 4b: methods with signatures
- 4c: consumers

## Bucket 5: rbac / permission-matrix gate posture
- per-endpoint gate tuples
- which are PLATFORM-only vs multi-audience

## Bucket 6: User assignment tables and invariant query
- 6a: assignment table shapes
- 6b: status enum values
- 6c: dry-run invariant query result + any SQL issues + actual SQL used

## Bucket 7: Codebase observations beyond scope
- FYI items

## Summary
- N findings confirm assumptions
- M findings contradict assumptions (each with evidence)
- K open questions for Phase 1 / Phase 2 design
- Verdict on is_system: (informational only / enforced at X / enforced at Y)
```

## Scope cap

If a bucket query fails for reasons unrelated to the investigation (env vars unset, DB unreachable, fixture missing), surface the failure and skip that bucket; do not block the rest of the investigation. The chat-side design conversation will handle gaps explicitly.

If the investigation reveals a security gap in the existing code (e.g., a role-related endpoint missing a gate, or a gate misconfigured), surface it as a finding but do not fix it. Step 6.18 may need to bundle the fix or it may warrant its own step.

## Don't

- Don't write or modify any source file.
- Don't propose code changes in the report (other than "this would need to change" surface notes).
- Don't extrapolate from the findings to a design. That's Chat's job in Phase 1 / Phase 2.
- Don't run the investigation in stages; one comprehensive sweep, one comprehensive report.
- Don't fabricate findings if a query fails; report the failure and move on.
