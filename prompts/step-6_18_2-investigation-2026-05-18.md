# Investigation: Step 6.18 : 4 structural unknowns

**Date drafted:** 2026-05-18
**Investigator:** Claude Code
**Output:** single chat reply, structured per bucket
**No source edits.** Read-only investigation.

This investigation answers 4 specific structural questions before Step 6.18 implementation begins. Each question has a clear yes/no or what-is-it answer in the codebase.

## Standing discipline

- **A8 cite-or-verify**: every finding cites file:line or query output.
- **No code changes.**
- **Surface contradictions** with operator's working assumptions.
- One comprehensive sweep, one reply.

## Bucket 1: Label source location

The investigation report on 2026-05-19 confirmed `PermissionMatrixRow` carries `module_label`, `resource_label`, `action_label`, `scope_label`. Question: where do these label values actually come from?

**1a.** Grep where each `*_label` field gets populated.
```
grep -rn "module_label\|resource_label\|action_label\|scope_label" src/admin_backend/
```

**1b.** For each label, identify the source mechanism:
- Hardcoded dict in a constants file?
- Method on the enum class (e.g., `ModuleCode.ADMIN.label`)?
- Computed from enum value (e.g., title-case)?
- DB-driven lookup?

Report exact file:line of the mapping for each label type.

**1c.** If hardcoded: report the full mapping (all values) so 6.18.2 design can verify completeness.

## Bucket 2: Error class taxonomy + shadowing risk

Step 6.18.3 plans to introduce 4 new error classes:
- `LastOverrideHolderError` (409, LAST_OVERRIDE_HOLDER)
- `RoleArchivedError` (409, ROLE_ARCHIVED)
- `AudienceScopeMismatchError` (409, AUDIENCE_SCOPE_MISMATCH)
- `InternalInvariantViolationError` (500, INTERNAL_INVARIANT_VIOLATION)

Plus reuse `EmptyPatchError` (from 6.17.3) and add a 5th: `PermissionNotFoundError` (422, INVALID_PERMISSION_ID).

**2a.** Read `src/admin_backend/errors.py` end-to-end. List every existing error class with:
- Class name
- HTTP status
- Code string (the `code = "..."` class attribute)
- Whether `__init__` takes args beyond `internal_message` + `**context`

**2b.** For each of the 5 new classes proposed: does anything with similar semantics already exist?
- `Archived` variants? (any `*ArchivedError` for any resource)
- `LastHolder` / `LastAdmin` variants?
- `InternalInvariant` or `InvariantViolation` variants?
- `AudienceMismatch` / `ScopeMismatch` / `PermissionScope` variants?
- `PermissionNotFound` or `InvalidPermission` variants?

Report any near-matches.

**2c.** Read base class hierarchy. Is there a `ClientError` base? `ServerError` base? Does the 500 case need a different base than 4xx? Confirm the class inheritance shape new errors should use.

## Bucket 3: Audience-scope filter SQL verification

LD17 says: TENANT-audience roles cannot hold GLOBAL-scope permissions. The GET detail endpoint filters `available_permissions[]` accordingly.

**3a.** Confirm the actual `core.permissions` rows split by scope:
```sql
SELECT scope, COUNT(*) FROM core.permissions GROUP BY scope ORDER BY scope;
```

Report the counts.

**3b.** Run the proposed `available_permissions` filter SQL against current DB state. For a PLATFORM-audience role (e.g., SUPER_ADMIN), the filter should return all permissions NOT currently held:

```sql
-- Replace :role_id with SUPER_ADMIN's id from the earlier investigation
WITH held AS (
    SELECT permission_id FROM core.role_permissions
    WHERE role_id = :role_id
)
SELECT id, code, scope FROM core.permissions
WHERE id NOT IN (SELECT permission_id FROM held)
ORDER BY code;
```

Report the row count and a sample of returned codes. This should NOT filter by scope (PLATFORM role can hold any scope).

**3c.** Run the same filter for a TENANT-audience role (e.g., OWNER):

```sql
-- Replace :role_id with OWNER's id
WITH held AS (
    SELECT permission_id FROM core.role_permissions
    WHERE role_id = :role_id
)
SELECT id, code, scope FROM core.permissions
WHERE id NOT IN (SELECT permission_id FROM held)
  AND scope != 'GLOBAL'
ORDER BY code;
```

Report the row count and confirm zero rows have scope='GLOBAL'. This is the actual SQL the repo will use for TENANT roles.

**3d.** Surface any unexpected query plan issues (full table scan etc.) via `EXPLAIN ANALYZE` on the TENANT query. At 35 permissions this is trivial but worth confirming the SQL works as written.

## Bucket 4: PermissionDetail vs PermissionRead deconfliction

Phase 2 design introduces a new `PermissionDetail` schema (with labels) alongside the existing `PermissionRead` (without labels).

**4a.** List every endpoint currently returning `PermissionRead`:
```
grep -rn "PermissionRead\b" src/admin_backend/
```
Identify each route + response shape consumer.

**4b.** Read `src/admin_backend/schemas/permission.py` end-to-end. Report:
- All schema classes defined
- Field overlap between `PermissionRead` and `PermissionMatrixRow` (which has labels)
- Whether `PermissionRead` could be extended with optional label fields without breaking existing consumers

**4c.** Surface the design question: should 6.18.2 introduce `PermissionDetail` as a separate schema, OR extend `PermissionRead` with optional label fields (backwards-compatible, additive)?

Report the trade-off honestly. If extending `PermissionRead` works without consumer breakage, that's simpler than introducing a parallel schema. If it breaks consumers (e.g., a test asserts a specific field set), surface where.

## Output format

```
# Investigation report: Step 6.18 structural unknowns

## Bucket 1: Label source
- 1a: grep results
- 1b: source mechanism per label type
- 1c: full mapping (if hardcoded)

## Bucket 2: Error class taxonomy
- 2a: existing errors.py inventory
- 2b: near-matches for each of 5 proposed new classes
- 2c: base class hierarchy

## Bucket 3: Audience-scope filter SQL
- 3a: scope distribution
- 3b: PLATFORM role available_permissions query result
- 3c: TENANT role available_permissions query result
- 3d: EXPLAIN ANALYZE notes

## Bucket 4: PermissionDetail vs PermissionRead
- 4a: consumers of PermissionRead
- 4b: schemas/permission.py content
- 4c: design recommendation (separate vs extend)

## Summary
- N findings; K open design questions surfaced
```

## Scope

Read-only. No code changes. Single report. Cite or verify every claim.
