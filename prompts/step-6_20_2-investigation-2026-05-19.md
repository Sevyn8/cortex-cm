# Investigation : Step 6.20.2 ltree input validation gap surface

**Date drafted:** 2026-05-19
**Investigator:** Claude Code
**Output:** single chat reply, structured per bucket
**No source edits.** Read-only investigation.

This investigation surfaces all endpoints + repo methods that accept caller-supplied ltree input without Pydantic-layer validation, before scoping the fix for Step 6.20.2. The bug surfaced via Cloud Run logs (v0.1.17, revision admin-backend-00018-46f): GET /api/v1/me/can-do returned 500 when target_anchor was a UUID-with-hyphens instead of an ltree path. Postgres SyntaxError bubbled to the generic 500 envelope.

## Standing discipline

- **A8 cite-or-verify**: every finding cites file:line or query output.
- **No code changes.**
- **Surface contradictions** with operator's working assumptions.
- One comprehensive sweep, one reply.

## Working assumptions to verify

1. `GET /api/v1/me/can-do` is the bug-reported endpoint; accepts `target_anchor` as bare `str | None` Query param.
2. `has_permission()` casts target_anchor via `CAST(:target_anchor AS ltree)`; that's where SyntaxError originates.
3. Other endpoints may pass user-supplied input to ltree-typed SQL paths.
4. Codebase precedent for input pattern validation exists at `schemas/org_node.py:271` (OrgNodeCreateRequest.code).

Verify each as a finding.

## Investigation buckets

### Bucket 1 : the bug-reported endpoint

**1a. `/me/can-do` handler signature.**

```
grep -n "can.do\|target_anchor" src/admin_backend/routers/v1/me.py
```

Report:
- Exact handler signature, Query param declaration, type annotation.
- Whether any validation (pattern, max_length, custom validator) is currently applied.
- The call site to `has_permission()` and the args passed.

**1b. `has_permission` ltree cast location.**

```
grep -n "ltree\|target_anchor\|CAST.*ltree\|::ltree" src/admin_backend/auth/permissions.py
```

Report:
- Every site in permissions.py where target_anchor reaches SQL.
- The exact SQL fragment that casts to ltree.
- Whether any pre-cast validation exists.

### Bucket 2 : all other endpoints accepting ltree-shaped input

**2a. Search for ltree-typed Query params across all routers.**

```
grep -rn "target_anchor\|anchor_path\|ltree\|target_path" src/admin_backend/routers/v1/
```

Report every match. For each handler that accepts ltree input, capture:
- Endpoint path + HTTP method.
- Query/body param name + type annotation.
- Whether validation is applied.

**2b. Search for `::ltree` casts and CAST(... AS ltree) across the codebase.**

```
grep -rn "::ltree\|CAST.*AS ltree\|AS LTREE\|ltree_type" src/admin_backend/
```

Report every match. Each is a potential vulnerability if user input reaches it.

**2c. Search for repo methods that take ltree-named params.**

```
grep -rn "def.*anchor\|def.*path:.*str\|def.*ltree" src/admin_backend/repositories/
```

Surface any repo method whose param name suggests an ltree path. Trace back to handlers.

### Bucket 3 : codebase pattern-validator precedents

**3a. Existing pattern= validators on Query/Field declarations.**

```
grep -rn "pattern=r\"" src/admin_backend/schemas/ src/admin_backend/routers/
```

Report all existing pattern validators. The fix should mirror existing style.

**3b. The cited precedent at schemas/org_node.py:271.**

```
sed -n '265,285p' src/admin_backend/schemas/org_node.py
```

Report the exact pattern, max_length, error message style. The fix mirrors this.

**3c. Ltree label grammar.**

Postgres ltree labels are documented as alphanumeric + underscore, dot-separated. Verify the precedent's pattern (`OrgNodeCreateRequest.code`) handles single labels only or full dot-separated paths.

### Bucket 4 : existing tests around target_anchor

**4a. Tests that exercise /me/can-do with target_anchor.**

```
grep -rn "can_do\|can-do\|target_anchor" tests/
```

Report:
- Test count by file.
- Any negative test that covers malformed target_anchor (none expected; this is the bug).
- Test fixtures that supply ltree-shaped values.

**4b. Tests adjacent to OrgNode code-pattern validation.**

```
grep -rn "INVALID_CODE\|pattern" tests/integration/test_org_tree*.py
```

Report whether the existing org_node pattern validation has test coverage. The new ltree pattern tests should mirror.

### Bucket 5 : codebase observations beyond scope

FYI items:
- Any other input that bubbles to Postgres without Pydantic validation (SQL syntax errors → 500).
- FN-AB entries adjacent to input validation hygiene.
- Recent FN-ABs about generic 500 envelope leaking SQL errors.

## Output format

```
# Investigation report : Step 6.20.2 ltree input validation gap

## Working assumptions verification
- Assumption 1 (/me/can-do bare str): confirmed / contradicted
- Assumption 2 (CAST happens in has_permission): confirmed / contradicted
- Assumption 3 (other endpoints affected): confirmed / contradicted with list
- Assumption 4 (precedent at org_node.py:271): confirmed / contradicted

## Bucket 1 : /me/can-do
- 1a: handler shape
- 1b: ltree cast site(s)

## Bucket 2 : other ltree-accepting endpoints
- 2a: Query param search
- 2b: SQL cast search
- 2c: repo method search

## Bucket 3 : pattern-validator precedents
- 3a: existing validators
- 3b: org_node.py:271 detail
- 3c: ltree grammar verification

## Bucket 4 : existing tests
- 4a: /me/can-do test coverage
- 4b: pattern validation test precedents

## Bucket 5 : codebase observations beyond scope

## Summary
- N endpoints affected
- K SQL cast sites that need upstream validation
- M open design questions for the fix scoping conversation
```

## Don't

- Don't write or modify any source file.
- Don't propose code changes; surface them as findings if they emerge.
- Don't run multi-stage investigation; one comprehensive sweep, one reply.
