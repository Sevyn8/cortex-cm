# Prompt — Step 6.9.3.1: scope cascade in has_permission

> Generated 2026-05-13. Calibrated against codebase HEAD at commit
> e0946b8 ("Step 6.9.2: gate factory + PermissionDeniedError + /me/*
> endpoints"). Pytest baseline: 294 passes, 0 failures.
>
> Paste this entire block into a fresh Claude Code session to start
> Step 6.9.3.1.

---

## Standing discipline (read first)

### On the code sketches in this prompt

Code blocks and SQL sketches below are STARTING POINTS, not the answer.
The operator drafts prompts without live access to the codebase. You
have live access. Use it.

Where you have a better implementation than what's sketched, implement
the better version. Surface deviations in your report with a one-line
reason.

Locked decisions in the "Locked decisions" section remain locked.
Everything else is calibrated guidance.

### Documentation writing

Updates to CLAUDE.md, BUILD_PLAN.md must be technical, sharp, concise.
Rules: state facts, active voice present tense, one sentence per fact,
specific over general, cite by reference (e.g., "per D-34"), no
meta-commentary, no adjectives that don't add information.

Bad: "This step introduces an important improvement to the cascade
logic that should make permission resolution much more flexible."

Good: "satisfying_scopes(scope) helper translates a requested scope
into the list of scopes whose grants satisfy that check via downward
cascade. SQL filter changes from `p.scope = :scope` to
`p.scope = ANY(:satisfying_scopes)` in both has_permission code paths."

### Definition of done

Before reporting complete:
1. All tests pass (existing + new).
2. mypy strict clean on every file touched.
3. EXPLAIN ANALYZE captured for both has_permission code paths (PLATFORM
   and TENANT) showing the ANY clause doesn't degrade the query plan.
4. CLAUDE.md updates sharp per the documentation-writing rules above.
5. Pre-commit checks (check_setup.sh, pytest, mypy, alembic check) all
   pass.

---

## Context: why this step exists

Section 6.9 sub-step status:
- Step 6.9.1 (SHIPPED at 63dd565): has_permission() pure-SQL permission
  check with exact-match scope filter
- Step 6.9.2 (SHIPPED at e0946b8): require() factory +
  PermissionDeniedError + /me/* endpoints
- Step 6.9.3 (split into 6.9.3.1 and 6.9.3.2):
  - **6.9.3.1 (this step):** scope cascade in has_permission
  - 6.9.3.2 (next): endpoint retrofit + per-resource anchor deps +
    mandatory-gate-discipline test

### The gap this step closes

The frontend doc (Ithina_Admin_Frontend.md section 5.5) specifies
downward cascade for permission scopes:

> "A permission granted at level N implies that permission at every
> level below N."

The 6.9.1 implementation does NOT implement this. Both has_permission
code paths filter `p.scope` by exact equality:

```
src/admin_backend/auth/permissions.py:147 (PLATFORM)
src/admin_backend/auth/permissions.py:204 (TENANT)
   AND p.scope = CAST(:scope AS permission_scope_enum)
```

This means: SUPER_ADMIN with `ADMIN.TENANTS.VIEW.GLOBAL` is DENIED
when a TENANT-scope check is requested. By design intent, GLOBAL
should cascade down to satisfy TENANT and STORE checks.

6.9.3.1 closes this gap by changing the SQL filter to accept ANY
scope that's in the cascade-satisfaction set for the requested scope.

### Design intent (locked during design conversation 2026-05-13)

1. **Cascade direction is downward only.** GLOBAL grants satisfy
   GLOBAL/TENANT/STORE checks. TENANT grants satisfy TENANT/STORE.
   STORE grants satisfy only STORE checks.

2. **Mechanism: Python helper + `ANY(:satisfying_scopes)` in SQL.**
   The helper translates the requested scope into the list of scopes
   whose grants would satisfy it. SQL does `p.scope = ANY(...)`.

3. **get_permissions_for_user is NOT modified.** It already returns
   raw grants without filtering on scope. Cascade is the gate's
   concern, not the broader-query's concern. Frontend implements
   cascade for UI gating; /me/can-do is the server-authoritative
   cascade-aware check.

4. **The full org hierarchy is encoded in the helper.** The 
   `_SCOPE_CASCADE_ORDER` tuple has 8 entries: GLOBAL (representing 
   the implicit Platform cascade root) plus the 7 `org_node_type_enum` 
   values (TENANT through DEPARTMENT). The v0 PermissionScope enum 
   has only 3 of these (GLOBAL, TENANT, STORE); the other 5 are 
   inert in the catalogue but encoded forward-compatibly in the 
   helper. When the enum expands (e.g., REGION), no helper code 
   change is needed.

5. **Coupling to org hierarchy is hardcoded and documented.** The
   `_SCOPE_CASCADE_ORDER` tuple in the helper mirrors the org tree
   hierarchy. Three other places encode the same hierarchy (DDL
   org_node_type_enum, frontend doc cascade specification, this
   tuple). All three must stay in sync; documented as a CLAUDE.md
   maintenance convention.

### Out of scope for 6.9.3.1

- Endpoint retrofits (Depends(require(...)) on existing endpoints)
  → 6.9.3.2
- Per-resource anchor dependencies (get_store_anchor, etc.) → 6.9.3.2
- Mandatory-gate-discipline meta-test → 6.9.3.2
- target_anchor cascade — already correctly implemented via ltree `<@`
  in 6.9.1; no changes here
- get_permissions_for_user changes → out of scope (returns raw grants
  per Q3 design)
- Catalogue changes — no new permission tuples added
- Role grant changes — no role_permissions rows touched
- Migration to expand PermissionScope enum → future step

---

## Pre-flight

1. Run `./scripts/check_setup.sh`. Expect 35/35.
2. `git log --oneline -3` — confirm HEAD is `e0946b8 Step 6.9.2: ...`.
3. `git status` — note any pre-existing items in the working tree;
   surface anything unexpected.
4. `uv run alembic heads` — expect `3e05299cb533` (no migration in this
   step).
5. `uv run pytest --tb=no -q | tail -5` — expect 294 passes, 0 failures.
   **If anything other than 294 passes, stop and report.**
6. Read `src/admin_backend/auth/permissions.py` fully. Focus on:
   - `has_permission()` function (PLATFORM and TENANT internal helpers)
   - Lines 147 and 204 (the exact-match scope filters being changed)
   - `get_permissions_for_user()` (NOT modified; confirm no scope
     filter exists)
   - Existing imports (ModuleCode, PermissionResource, PermissionAction,
     PermissionScope from where; verify the actual import path)
7. Read `src/admin_backend/models/enums.py` (or wherever PermissionScope
   lives at HEAD). Verify:
   - PermissionScope is a StrEnum with values "GLOBAL", "TENANT",
     "STORE"
   - The actual enum class location (the helper imports from there)
8. Read `tests/integration/test_has_permission.py` fully. Understand:
   - Existing fixtures (_lookup_permission_id, JWT helpers, seed-data
     assumptions)
   - 13 existing tests and what scenarios they cover
   - Pattern for asserting allow/deny outcomes
9. Read `src/admin_backend/models/org_node.py` (or wherever
   `OrgNodeType` lives). Note: `org_node_type_enum` has 7 values 
   (TENANT, BUSINESS_UNIT, HQ, COUNTRY, REGION, STORE, DEPARTMENT). 
   The Platform level is implicit — it's the cascade root above any 
   tenant, not stored as an `org_nodes` row. `_SCOPE_CASCADE_ORDER` 
   therefore has 8 entries (the 7 org_node_type values + GLOBAL at 
   position 0 representing Platform).
10. Read `docs/Ithina_Admin_Frontend.md` section 5.5 (Cascade rules).
    The canonical statement of cascade design intent.
11. Read `CLAUDE.md` fully. Focus on:
    - Existing D-XX entries
    - Forward-notes section (a new FN-AB number gets assigned here)
    - The "Note on dependency factories" subsection (precedent for
      lightweight code-pattern notes)
12. Read `BUILD_PLAN.md` Section 6.9 entry at HEAD. Expect the entry 
    to list 6.9.1 DONE, 6.9.2 DONE, 6.9.3 TODO (one entry). This step 
    splits 6.9.3 into 6.9.3.1 (this step) and 6.9.3.2 (next). Verify 
    the entry shape before editing; if Step 6.9.3 is already split or 
    shaped differently than expected (e.g., 6.9.2 commit already 
    introduced sub-step structure), surface and adapt the BUILD_PLAN 
    edit accordingly.
13. Confirm Postgres `ltree` extension loaded (sanity check, used
    elsewhere in has_permission):
    ```bash
    psql "$DATABASE_URL" -c "\dx ltree"
    ```

---

## Step ID and intent

**Step 6.9.3.1** — single deliverable group:

- `satisfying_scopes(scope)` helper function in
  `src/admin_backend/auth/permissions.py`
- `_SCOPE_CASCADE_ORDER` module-level tuple encoding the full 8-level
  hierarchy
- Modified `has_permission()` SQL (both PLATFORM and TENANT paths):
  exact-match → `ANY(...)`
- 8 integration tests covering cascade behavior (T_SC1-T_SC8)
- 6 unit tests for the helper and order tuple
- CLAUDE.md update (new "Org hierarchy coupling" maintenance note;
  Current state entry for 6.9.3.1)
- BUILD_PLAN.md update (split Section 6.9.3 into 6.9.3.1 + 6.9.3.2;
  flip 6.9.3.1 to DONE)
- One new forward-note (PermissionScope enum expansion) + one update 
  to the existing _require_platform_auth forward-note from 6.9.2

### Scope in

- `satisfying_scopes(scope: PermissionScope) -> list[str]` helper at
  `src/admin_backend/auth/permissions.py`.
- `_SCOPE_CASCADE_ORDER: tuple[str, ...]` module-level constant in the
  same file, listing all 8 hierarchy levels in cascade order.
- Modified `has_permission()` PLATFORM path SQL: line 147 from
  `AND p.scope = CAST(:scope AS permission_scope_enum)` to
  `AND p.scope = ANY(:satisfying_scopes)` (refine syntax to match
  codebase conventions for ANY/array binds).
- Same change to TENANT path SQL at line 204.
- Caller-side: helper invoked at the top of `has_permission` to
  produce `satisfying_scopes` from the requested `scope`, passed as a
  SQL bind parameter.
- 8 new integration tests at `tests/integration/test_has_permission.py`
  covering cascade scenarios.
- 6 unit tests verifying `satisfying_scopes()` behavior and
  `_SCOPE_CASCADE_ORDER` integrity.
- CLAUDE.md updates: maintenance convention note + Current state entry +
  one new forward-note + one update to existing 6.9.2 forward-note.
- BUILD_PLAN.md update: split 6.9.3 → 6.9.3.1 (DONE) + 6.9.3.2 (TODO).
- Prompt file bundled into the commit.

### Scope out

- `get_permissions_for_user` — NOT modified. Confirms no regression by
  running existing /me/permissions tests.
- Endpoint retrofits → 6.9.3.2.
- target_anchor cascade — already correct.
- New catalogue rows or role grants — none.
- PermissionScope enum expansion — future step; the helper is
  forward-compatible.
- DDL changes — none.
- Architecture.md updates → wait until Section 6.9 fully completes
  (after 6.9.3.2).

### Acceptance criteria

- `satisfying_scopes()` callable at the locked location with the locked
  signature. Returns the expected list for each of the 3 v0 enum values.
- `_SCOPE_CASCADE_ORDER` contains exactly 8 entries in the documented
  order.
- `has_permission()` correctly cascades downward: a user with `GLOBAL`
  grant passes a `TENANT` check; a user with `TENANT` grant passes a
  `STORE` check; a user with only `STORE` grant FAILS a `TENANT` check.
- All 8 new cascade integration tests pass.
- All 6 helper unit tests pass:
  - 3 satisfying_scopes behavior tests (GLOBAL, TENANT, STORE)
  - 3 _SCOPE_CASCADE_ORDER integrity tests (length, enum-coverage,
    canonical-match)
- All 13 existing has_permission tests still pass (cascade is additive
  — exact-match still works when the user's grant scope matches the
  request scope exactly).
- All 294 pre-step tests still pass.
- mypy strict clean on every file touched.
- `scripts/check_setup.sh` 35/35.
- `scripts/smoke_test.py` PASS count unchanged.
- `scripts/smoke_curl.sh` and `scripts/test_endpoints.sh` PASS counts
  unchanged (no new endpoints).
- EXPLAIN ANALYZE captured for both has_permission paths with the new
  ANY clause; query plan still uses `uq_permissions_tuple` index.
- One new forward-note added (PermissionScope enum expansion) with 
  next available FN-AB number; one existing 6.9.2 forward-note updated 
  in place with the post-6.9.3.1 cascade context.
- New CLAUDE.md "Org hierarchy coupling" maintenance convention.
- BUILD_PLAN.md Section 6.9.3 split into two sub-step entries.

### Locked decisions (do not deviate)

1. **Cascade direction: downward only.** GLOBAL covers all lower
   scopes. STORE covers only STORE. No upward cascade.

2. **Mechanism: Python helper + `ANY(:satisfying_scopes)` in SQL.**
   NOT CASE WHEN in SQL. NOT a Postgres function via DDL. The Python
   helper is the single source of truth for cascade ordering.

3. **`get_permissions_for_user` NOT modified.** Returns raw grants
   (no cascade expansion in `/me/permissions` response).

4. **`_SCOPE_CASCADE_ORDER` lists all 8 levels** even though v0 enum
   has only 3. Strings, not enum members, for forward compatibility.

5. **Coupling to org hierarchy is hardcoded and documented.** Three
   places (this tuple, org_node_type_enum, frontend doc section 5.5)
   must stay in sync. CLAUDE.md captures the convention.

---

## Implementation outline

### File 1: `src/admin_backend/auth/permissions.py` — MODIFY

Add helper + constant at the top of the file (or near has_permission,
matching the codebase's organization conventions).

Shape (refine based on actual file structure):

```python
# Module-level constant: cascade order, highest scope to lowest.
#
# IMPORTANT — coupling to org hierarchy:
# This tuple mirrors the order of the org-tree hierarchy as defined
# in three other places that MUST stay in sync:
#   1. DDL `org_node_type_enum` in db/raw_ddl/shared_utilities_v1.sql
#   2. Frontend doc cascade specification: docs/Ithina_Admin_Frontend.md
#      section 5.5 ("Cascade rules")
#   3. This tuple
#
# When the org hierarchy changes (add/remove/reorder levels):
#   - Update all three sources together.
#   - The unit test `test_scope_cascade_order_matches_canonical` catches
#     local drift but not cross-source drift; manual sync required.
#
# Strings (not PermissionScope enum members) so the tuple can list
# levels that don't yet exist in the v0 enum (BUSINESS_UNIT, HQ,
# COUNTRY, REGION, DEPARTMENT). The unit test verifies every current
# enum value IS in this tuple.

_SCOPE_CASCADE_ORDER: tuple[str, ...] = (
    "GLOBAL",        # Platform — highest
    "TENANT",
    "BUSINESS_UNIT",
    "HQ",
    "COUNTRY",
    "REGION",
    "STORE",
    "DEPARTMENT",    # lowest
)


def satisfying_scopes(requested: PermissionScope) -> list[str]:
    """Return the scopes whose grants satisfy a permission check at the 
    requested scope, via downward cascade.
    
    Cascade direction is downward: a grant at a higher scope satisfies
    checks at lower scopes (per frontend doc section 5.5, "A permission
    granted at level N implies that permission at every level below N").
    
    Examples (v0 enum has 3 values; helper supports all 8 levels):
        satisfying_scopes(PermissionScope.GLOBAL) -> ["GLOBAL"]
        satisfying_scopes(PermissionScope.TENANT) -> ["GLOBAL", "TENANT"]
        satisfying_scopes(PermissionScope.STORE)  -> 
            ["GLOBAL", "TENANT", "BUSINESS_UNIT", "HQ", "COUNTRY", 
             "REGION", "STORE"]
    
    The extra levels in the STORE case (BUSINESS_UNIT, HQ, COUNTRY, 
    REGION) are inert today because the v0 catalogue has no grants at 
    those scopes — but they're returned so the helper Just Works when 
    the enum expands.
    
    Returns a list[str], not list[PermissionScope], to allow forward 
    levels that aren't enum members yet.
    """
    requested_value = requested.value
    if requested_value not in _SCOPE_CASCADE_ORDER:
        # Defensive: should never happen if enum and tuple are in sync;
        # the unit test catches enum-vs-tuple drift. If it does happen,
        # fall back to exact-match (single scope) rather than crashing.
        return [requested_value]
    idx = _SCOPE_CASCADE_ORDER.index(requested_value)
    return list(_SCOPE_CASCADE_ORDER[: idx + 1])
```

**Then modify the two SQL queries in `has_permission()`:**

PLATFORM path — current line ~147 of permissions.py reads:

```sql
AND p.scope    = CAST(:scope    AS permission_scope_enum)
```

Change to:

```sql
AND p.scope    = ANY(CAST(:satisfying_scopes AS permission_scope_enum[]))
```

(The exact SQLAlchemy text() binding for an array parameter — refine to
match the codebase's pattern. For psycopg + raw text(), passing a
Python list to a `:param` typically requires explicit array-cast in
SQL OR psycopg's array adapter. Verify against the codebase's existing
ANY/array usage. If the codebase has no precedent, the cleanest
approach is `text("... = ANY(:scopes)")` with `{"scopes": list_value}`
and let psycopg adapt. Surface if the array bind requires a different
approach.)

**Concrete fallback if no codebase precedent exists for array binds:**

```sql
AND p.scope::text = ANY(CAST(:satisfying_scopes AS text[]))
```

Bind: `{"satisfying_scopes": ["GLOBAL", "TENANT", "STORE", ...]}`

Why this works: casts both sides to text for the comparison; psycopg 
adapts a Python list of strings to `text[]` cleanly without needing 
enum-array registration. The enum type-safety on the column is 
preserved at write time (the column remains `permission_scope_enum` 
in storage); the comparison still works because enum values are 
unique strings.

If this approach also fails with a psycopg error, surface the exact 
error message and pause before proceeding — don't try alternative 
patterns silently.

TENANT path — same change at line ~204.

**At the call site, before executing the SQL:**

```python
satisfying = satisfying_scopes(scope)
result = await session.execute(
    sql,
    {
        # ... existing params ...
        "satisfying_scopes": satisfying,
        # remove or keep "scope" — surface what the binding requires
    },
)
```

If the SQL bind for `ANY(...)` requires the scope values as a list,
remove the existing `:scope` bind. If both binds are needed (some SQL
shapes), surface and explain.

### File 2: `tests/integration/test_has_permission.py` — MODIFY

Add 8 new cascade tests. Categories:

**Cascade — downward (allow cases):**

- `T_SC1` — User with `GLOBAL` grant passes a `GLOBAL` check (sanity).
- `T_SC2` — User with `GLOBAL` grant passes a `TENANT` check (new
  cascade behavior).
- `T_SC3` — PLATFORM user with `ADMIN.STORES.VIEW.GLOBAL` passes a 
  STORE-scope check. Verifies GLOBAL→STORE cascade via PLATFORM query 
  path. target_anchor not relevant here (PLATFORM path doesn't filter 
  on it).
- `T_SC4` — User with `TENANT` grant passes a `TENANT` check (sanity).
- `T_SC5` — TENANT user with `ADMIN.STORES.VIEW.TENANT` anchored at 
  tenant root passes a STORE-scope check with target_anchor = 
  '<tenant>.<store>'. Verifies BOTH scope cascade (TENANT→STORE) AND 
  anchor cascade (tenant-root covers store) work together. 
  Diagnostic value: if this test fails but T_SC4 passes, scope cascade 
  is broken. If T_SC8 (cross-tenant) passes but T_SC5 fails, anchor 
  cascade is broken.

**Cascade — denial of upward (boundary cases):**

- `T_SC6` — User with only `STORE` grant FAILS a `TENANT` check. STORE
  is below TENANT; cascade is downward only; upward access is denied.
- `T_SC7` — User with only `TENANT` grant FAILS a `GLOBAL` check. TENANT
  is below GLOBAL; upward access is denied.

**Cross-tenant safety (regression check):**

- `T_SC8` — Cross-tenant injection still denied. A TENANT-A user with
  `TENANT` grant FAILS a `STORE` check targeting a STORE under TENANT-B,
  even with cascade. (target_anchor enforcement, unchanged from 6.9.1's
  T_X1, but worth a sanity test here to confirm scope cascade hasn't
  broken anchor cascade.)

For each test:
- Mirror the existing 13 tests' pattern (fixtures, JWT helpers).
- Reuse seed data; mutate state inside the test only if necessary.
- Mark T_SC6 and T_SC8 LOAD-BEARING — they verify cascade direction
  correctness and cross-tenant safety respectively. Use the docstring
  convention from 6.9.1's tests.

**Pivot considerations:**

The test scenarios may need specific permission tuples in the catalogue
to be exercisable. Before writing tests, verify:
- A user with `ADMIN.TENANTS.VIEW.GLOBAL` exists in seed → T_SC2, T_SC3
- A user with `ADMIN.USERS.VIEW.TENANT` exists in seed → T_SC4, T_SC5
- A user with `ADMIN.STORES.VIEW.TENANT` exists in seed → T_SC5

If the right combinations don't exist in seed, either (a) pivot to
adjacent tuples that do (note in test docstring), or (b) construct test
fixtures via in-test SQL inserts with proper teardown. Surface the
approach.

### File 3: Add unit tests for the helper

Unit tests live at `tests/unit/test_permissions_helpers.py` (new file).
Create `tests/unit/` directory if it does not exist at HEAD.

Reasoning: these tests do not touch the database or fixtures. Putting 
them in `tests/integration/test_has_permission.py` forces them to 
inherit DB setup overhead. A new `tests/unit/` directory establishes 
the convention for future pure-Python unit tests.

If the codebase has an existing `tests/unit/` directory at HEAD, use 
it. Surface if no precedent exists and you create it — that's a small 
convention introduction worth noting in the report.

Tests:

```python
def test_satisfying_scopes_global() -> None:
    """GLOBAL request satisfied only by GLOBAL grants."""
    assert satisfying_scopes(PermissionScope.GLOBAL) == ["GLOBAL"]

def test_satisfying_scopes_tenant() -> None:
    """TENANT request satisfied by GLOBAL and TENANT grants."""
    assert satisfying_scopes(PermissionScope.TENANT) == [
        "GLOBAL", "TENANT"
    ]

def test_satisfying_scopes_store() -> None:
    """STORE request satisfied by all higher scopes via cascade."""
    assert satisfying_scopes(PermissionScope.STORE) == [
        "GLOBAL", "TENANT", "BUSINESS_UNIT", "HQ", "COUNTRY", 
        "REGION", "STORE",
    ]

def test_scope_cascade_order_has_eight_levels() -> None:
    """Sanity: tuple lists all 8 hierarchy levels."""
    from admin_backend.auth.permissions import _SCOPE_CASCADE_ORDER
    assert len(_SCOPE_CASCADE_ORDER) == 8

def test_scope_cascade_order_includes_all_enum_values() -> None:
    """Every current PermissionScope enum value must appear in 
    _SCOPE_CASCADE_ORDER. Catches drift if the enum expands without 
    updating the order tuple."""
    from admin_backend.auth.permissions import _SCOPE_CASCADE_ORDER
    enum_values = {s.value for s in PermissionScope}
    order_values = set(_SCOPE_CASCADE_ORDER)
    missing = enum_values - order_values
    assert not missing, f"Enum values not in cascade order: {missing}"

def test_scope_cascade_order_matches_canonical() -> None:
    """Tuple exactly matches the canonical org-hierarchy order. Catches 
    reordering drift (which the previous test does not catch)."""
    from admin_backend.auth.permissions import _SCOPE_CASCADE_ORDER
    assert _SCOPE_CASCADE_ORDER == (
        "GLOBAL",
        "TENANT",
        "BUSINESS_UNIT",
        "HQ",
        "COUNTRY",
        "REGION",
        "STORE",
        "DEPARTMENT",
    )
```

### File 4: `CLAUDE.md` — MODIFY

**Add to "Current state — Completed":**

```
- Section 6.9.3.1 — scope cascade in has_permission. Downward cascade
  per frontend doc section 5.5. Python helper satisfying_scopes() +
  _SCOPE_CASCADE_ORDER tuple at auth/permissions.py; both has_permission
  code paths use `p.scope = ANY(:satisfying_scopes)` in place of
  exact-match. get_permissions_for_user unchanged (returns raw grants
  per Q3 design). 8 integration tests + 6 unit tests; 2 LOAD-BEARING 
  (T_SC6, T_SC8). Total pytest 294 → 308.
```

**Add new "Org hierarchy coupling" maintenance convention** (in
whichever section CLAUDE.md uses for maintenance conventions; alongside
D-XX entries or as a standalone subsection — match house style):

```
### Org hierarchy coupling

The org-tree hierarchy is hardcoded in three independent places that
must stay in sync:

1. DDL `org_node_type_enum` in db/raw_ddl/shared_utilities_v1.sql
2. Frontend doc cascade specification: docs/Ithina_Admin_Frontend.md
   section 5.5 ("Cascade rules")
3. `_SCOPE_CASCADE_ORDER` tuple in src/admin_backend/auth/permissions.py

When the org hierarchy changes (add/remove/reorder levels):
- Update all three sources together.
- Unit test `test_scope_cascade_order_matches_canonical` catches local
  drift in the Python tuple but not cross-source drift (i.e., changes
  to the DDL enum or frontend doc that aren't reflected in the tuple,
  or vice versa).
- Manual sync required across the three sources.

Levels in _SCOPE_CASCADE_ORDER that aren't yet in PermissionScope enum
(BUSINESS_UNIT, HQ, COUNTRY, REGION, DEPARTMENT in v0) are inert in
queries because no catalogue rows have those scope values. They're
present in the tuple for forward compatibility when the enum expands.
```

**Forward-note actions** (one new, one update to existing):

1. **NEW forward-note** (assign next available FN-AB number):

```
### FN-AB-NN — PermissionScope enum expansion (future)

Current v0 enum has 3 values (GLOBAL, TENANT, STORE). Full org tree
supports 8 levels; expansion to include REGION is the most likely first
addition (frontend doc shows Markdowns/APPROVE/Region examples).

When expanding:
- Add the value to PermissionScope enum (Python + DB enum via Alembic
  migration for DDL change).
- Add catalogue rows for resources that should support the new scope.
- Grant the rows to appropriate roles.
- No change to satisfying_scopes() or _SCOPE_CASCADE_ORDER — they
  already encode all 8 levels.

The unit test test_scope_cascade_order_includes_all_enum_values
catches the case where the enum is expanded without updating the
order tuple (which shouldn't happen if the tuple already lists all 8
levels, but guards against future tuple shrinkage).
```

2. **UPDATE to existing forward-note** (no new FN-AB number; modify 
   in place):

```
### FN-AB-NN — _require_platform_auth retirement decision (update)

The existing forward-note added during Step 6.9.2 (see CLAUDE.md 
Forward-notes section) tracks the deferred decision to retire 
_require_platform_auth in favor of Depends(require(...)).

Update the existing entry (do NOT create a new FN-AB number) with 
the post-6.9.3.1 context: now that has_permission supports scope 
cascade, the replacement is mechanically simpler — PLATFORM users 
with VIEW.GLOBAL automatically satisfy any narrower scope check via 
cascade. The decision still belongs to 6.9.3.2 design conversation; 
this is just a context refresh on the existing entry.
```

### File 5: `BUILD_PLAN.md` — MODIFY

Section 6.9 currently lists 6.9.1, 6.9.2, 6.9.3 as three sub-steps. Split
6.9.3 into:

- **6.9.3.1 — Scope cascade in has_permission.** Status: this commit
  flips to DONE. Body summarizes the helper + SQL change.
- **6.9.3.2 — Retrofit existing endpoints with require() + per-resource
  anchor deps + mandatory-gate-discipline test.** Status: TODO. Body
  describes the retrofit scope and dependencies on 6.9.3.1 (cascade
  must be in place for endpoint design choices).

Section 6.9 overall status updated to "6.9.1 DONE; 6.9.2 DONE; 6.9.3.1
DONE; 6.9.3.2 TODO."

### File 6: `prompts/step-6_9_3_1-scope-cascade-2026-05-13.md` — NEW

This prompt file. Bundle into the commit per per-step convention.

---

## Caution-first risks

1. **The SQL bind for ANY(...) may require array-typed casting.**
   Psycopg/SQLAlchemy needs the array bind shape to match Postgres's
   expected type. The exact-match SQL today casts the scope bind to
   `permission_scope_enum`. The new ANY clause needs the array cast:
   `ANY(CAST(:satisfying_scopes AS permission_scope_enum[]))` or
   equivalent. Verify against the codebase's existing array/ANY usage
   if any exists. If no precedent exists, this is the first array bind
   in the codebase — surface the exact mechanism used.

2. **`satisfying_scopes()` returns a `list[str]`, NOT `list[PermissionScope]`.**
   The list contains string values (including levels not in v0 enum).
   The SQL bind takes strings. Psycopg's array adapter handles
   string-to-enum casting via the SQL CAST. Don't convert to enum
   members; that would fail on BUSINESS_UNIT/HQ/etc.

3. **mypy strict on the new helper.** The `tuple[str, ...]` typing and
   `list[str]` return should pass strict. If mypy complains about the
   `requested.value` access (PermissionScope as StrEnum), refine the
   typing.

4. **Defensive fallback in helper.** If the requested scope's `.value`
   isn't in `_SCOPE_CASCADE_ORDER` (shouldn't happen given the unit
   tests catch this), the helper falls back to exact-match (single
   scope). This is conservative — never grant more than exact behavior
   when in doubt. Surface in the report if this defensive branch ever
   fires in tests (it shouldn't).

5. **Existing test T_X1 (cross-tenant injection) must still pass.**
   T_SC8 is the explicit cross-tenant cascade test, but the existing
   T_X1 verifies target_anchor enforcement. Scope cascade is a SEPARATE
   concern from anchor cascade; T_X1 should be unaffected. If T_X1
   fails, surface and stop.

6. **Existing /me/permissions tests must pass unchanged.**
   get_permissions_for_user is NOT modified. The 18 me_router tests
   should all still pass with no behavioral change. If any /me/* test
   fails, surface and stop.

7. **EXPLAIN ANALYZE check.** Postgres uses an index on
   `uq_permissions_tuple` for the exact-match scope filter today. With
   ANY(...) the planner might choose differently. Verify both
   has_permission paths still use index scans (not seq scans on
   permissions). If the plan degrades, surface and propose alternative
   SQL shapes before locking the change.

8. **`_SCOPE_CASCADE_ORDER` includes levels that don't exist in the
   v0 enum.** The defensive logic in the helper handles unknown
   requested scopes gracefully. The reverse case (helper returns
   "BUSINESS_UNIT" etc. for STORE requests) is fine because no
   catalogue rows have those scope values — Postgres's ANY just doesn't
   match anything for those values. Surface if pytest catches a
   surprising behavior here.

---

## Testing and regression discipline

### New tests

~6-8 cascade integration tests + ~6 helper unit tests.

**LOAD-BEARING** (regression in either blocks the step):
- `T_SC6` — User with only STORE grant denied at TENANT check. Verifies
  cascade direction is downward only; any regression here means
  upward cascade slipped in.
- `T_SC8` — Cross-tenant cascade still denied. Verifies scope cascade
  hasn't compromised target_anchor enforcement.

### Tests deliberately not added

- **Unit tests on `has_permission()` with mocked sessions.** Same
  reasoning as 6.9.1: the function's logic is the SQL; mocking
  session doesn't exercise the query.
- **Performance/load tests.** v0 scale. EXPLAIN ANALYZE captures the
  plan; sufficient.
- **Tests covering cascade for levels not in v0 enum (BUSINESS_UNIT,
  HQ, COUNTRY, REGION, DEPARTMENT).** Those levels have no catalogue
  rows in v0; the helper handles them at the Python level but they
  can't be exercised end-to-end. The unit test on the helper's output
  for STORE (which includes all 7 higher levels in its return value)
  is sufficient verification that the helper produces correct output.
- **Tests covering the defensive fallback** (when a requested scope
  isn't in the order tuple). This shouldn't happen given the
  test_scope_cascade_order_includes_all_enum_values unit test catches
  enum-vs-tuple drift before runtime. Adding a test for it would test
  unreachable code.

### Regression risk surface

1. **All 13 existing has_permission tests.** Cascade is additive — when
   the user's grant scope matches the request scope exactly, the
   `ANY(...)` clause still matches. Any regression in the 13 existing
   tests means the SQL change is incorrect.

2. **All 18 me_router tests.** get_permissions_for_user not modified;
   /me/permissions and /me/can-do should behave identically.

3. **mypy on the new helper and call site.** New typing surface;
   strict mode may surface issues with `tuple[str, ...]` or
   `list[str]` interactions with PermissionScope.

4. **Smoke and endpoint test scripts.** No new endpoints; PASS counts
   unchanged.

5. **EXPLAIN ANALYZE for has_permission queries.** Query plan may
   change due to ANY clause. Index usage on uq_permissions_tuple
   should remain. If the planner chooses seq scan, surface.

---

## Verification harness

Run in order. All must be green before reporting.

```bash
# 0. Pre-verification reseed.
uv run python -m scripts.seed_dev_data --reset

# 0a. Confirm seed counts (sanity).
psql "$DATABASE_URL" -c "
SET search_path TO core, public;
SELECT
  (SELECT COUNT(*) FROM tenant_users) AS tu,
  (SELECT COUNT(*) FROM platform_users) AS pu,
  (SELECT COUNT(*) FROM permissions) AS perm,
  (SELECT COUNT(*) FROM role_permissions) AS rp;
"
# Expected: tu=17, pu=3, perm=30, rp=120 (post-6.9.2 baseline).

# 1. Type checking.
uv run mypy src/admin_backend/

# 2. Pytest, all tests.
uv run pytest --tb=no -q

# 2a. Per-router regression checkpoint.
# Existing per-router test counts should be unchanged.
# Surface any drop.

# 3. Smoke test.
uv run python -m scripts.smoke_test

# 3a. Smoke curl + local endpoint tests (no new endpoints; counts 
# unchanged).
# Boot the app first if not running:
#   uv run uvicorn src.admin_backend.main:app --reload
bash scripts/smoke_curl.sh
# Expected: PASS count unchanged (still 22).

bash scripts/test_endpoints.sh
# Expected: clean run; counts unchanged.

# 4. Alembic heads.
uv run alembic heads
# Expected: 3e05299cb533 (unchanged; no migration in this step).

# 5. Targeted has_permission tests (existing + new cascade).
uv run pytest tests/integration/test_has_permission.py -v

# 6. Helper unit tests.
uv run pytest tests/unit/test_satisfying_scopes.py -v
# (Path may differ; use whichever location was chosen for the helper
# unit tests.)

# 7. Import smoke.
uv run python -c "
from admin_backend.auth.permissions import (
    has_permission, 
    get_permissions_for_user,
    require, 
    satisfying_scopes,
    _SCOPE_CASCADE_ORDER,
)
print('OK')
print(f'_SCOPE_CASCADE_ORDER length: {len(_SCOPE_CASCADE_ORDER)}')
print(f'_SCOPE_CASCADE_ORDER: {_SCOPE_CASCADE_ORDER}')
"

# 8. EXPLAIN ANALYZE for both has_permission paths.
# Pick a representative PLATFORM user (e.g., Anjali) and a TENANT user
# (e.g., Marcus T at Buc-ee's). Run their has_permission queries with
# the new SQL against seeded Postgres. Capture both plans.
# Verify:
# - Index scan on uq_permissions_tuple still used
# - ANY clause doesn't trigger a seq scan on permissions
# - Execution times comparable to 6.9.1 baseline (0.170ms PLATFORM,
#   0.314ms TENANT)
```

---

## Report (BEFORE proposing commit)

1. Pre-flight outputs (items 1-13 explicit results).
2. Resolution of design choices made during implementation:
   - Exact SQL bind shape for the ANY array (CAST syntax, parameter
     name, etc.).
   - Location of unit tests (integration file or new unit file).
   - Any deviation from the sketches.
3. Diffs:
   - Modified: `src/admin_backend/auth/permissions.py` (helper + SQL
     changes).
   - Modified: `tests/integration/test_has_permission.py` (cascade
     tests).
   - New: `tests/unit/test_permissions_helpers.py` (unit tests for the 
     helper; create `tests/unit/` if not exists).
   - Modified: `CLAUDE.md` (Current state + maintenance convention + 
     1 new forward-note + 1 updated forward-note from 6.9.2).
   - Modified: `BUILD_PLAN.md` (Section 6.9.3 split).
   - New: `prompts/step-6_9_3_1-scope-cascade-2026-05-13.md`.
4. Verification harness output (all steps 0 - 8).
5. Pre/post pytest counts (294 → 308: 8 cascade integration + 6 helper 
   unit).
6. Per-test summary: which cascade tests passed, mapping to T_SC1
   through T_SC8. Mark T_SC6 and T_SC8 as LOAD-BEARING.
7. EXPLAIN ANALYZE output for both has_permission paths.
8. Any deviation from the locked design decisions (should be none).
9. Forward-notes: state the new FN-AB number assigned (PermissionScope 
   enum expansion) and the existing FN-AB number that was updated 
   in place (_require_platform_auth retirement context refresh).

Wait for explicit operator authorisation before staging or committing.

---

## Surface-and-stop scenarios

Stop and report (do not work around silently) if:

1. Pytest baseline is not 294 passes at pre-flight.
2. The exact-match SQL filter at lines 147 and 204 doesn't match what
   this prompt describes (different from the 6.9.3 investigation report
   verbatim quotes).
3. PermissionScope enum has values other than GLOBAL/TENANT/STORE at
   HEAD (the investigation verified 3 values; if different, surface).
4. The SQL bind for ANY(...) requires a pattern that's not documented
   anywhere in the codebase (e.g., the codebase has zero precedent for
   array binds).
5. EXPLAIN ANALYZE shows the new query plan degrades meaningfully (e.g.,
   seq scan on permissions, execution time > 5x baseline).
6. Any existing has_permission test fails (T_C1, T_C3, T_M1, T_X1, T_T3
   are particularly important — these are 6.9.1's LOAD-BEARING tests).
7. Any /me/permissions or /me/can-do test fails (get_permissions_for_user
   is not modified; failures here would indicate unexpected side
   effects).
8. mypy strict surfaces errors that aren't trivially fixable (e.g.,
   the helper signature creates a circular import).
9. The defensive fallback branch in `satisfying_scopes()` is exercised
   during pytest runs (would indicate enum-vs-tuple drift in the unit
   tests).
10. Seed data doesn't have a user with sufficient combination of grants
    to exercise T_SC2 + T_SC3 + T_SC5. SUPER_ADMIN holds all 30 catalogue 
    permissions per the 6.9.2 investigation, so cascade-allow tests 
    (T_SC2, T_SC3, T_SC5) should be exercisable via SUPER_ADMIN.
    
    The harder case: T_SC6 and T_SC7 (denial tests) require a user 
    with ONLY a lower-scope grant. Seed roles likely don't have these 
    clean isolation cases (most roles have a mix of scopes). Pivot 
    strategies:
    - (a) Insert temporary fixtures inside the test (user + role + 
      role_permissions row), rollback after.
    - (b) Find a real seed user whose role coincidentally has only 
      the desired scope (verify before relying).
    - (c) Reduce T_SC6/T_SC7 to documentation-only tests if isolation 
      fixtures are infeasible; rely on the helper unit tests for 
      cascade-direction correctness (less direct, but the helper IS 
      the source of truth for cascade ordering).
    
    Surface the chosen approach.

---

## After completing

Propose a git commit per CLAUDE.md "After completing a task" Pattern A:

```bash
git status
git add src/admin_backend/auth/permissions.py \
        tests/integration/test_has_permission.py \
        tests/unit/test_satisfying_scopes.py \
        CLAUDE.md BUILD_PLAN.md \
        prompts/step-6_9_3_1-scope-cascade-2026-05-13.md
git commit -m "$(cat <<'EOF'
Step 6.9.3.1: scope cascade in has_permission

- Modified: src/admin_backend/auth/permissions.py. New 
  satisfying_scopes(scope) helper translates a requested scope into 
  the list of scopes whose grants satisfy that check via downward 
  cascade per frontend doc section 5.5. New _SCOPE_CASCADE_ORDER 
  tuple encoding all 8 org-hierarchy levels. has_permission SQL 
  changed from `p.scope = :scope` (exact match) to `p.scope = 
  ANY(:satisfying_scopes)` in both PLATFORM and TENANT paths.
  get_permissions_for_user unchanged (returns raw grants per Q3 
  design; cascade is the gate's concern, not /me/permissions's).
- Modified: tests/integration/test_has_permission.py. 8 new cascade 
  tests. 2 LOAD-BEARING: T_SC6 (cascade direction is downward only; 
  STORE grant fails TENANT check) and T_SC8 (cross-tenant cascade 
  still denied; scope cascade doesn't compromise target_anchor 
  enforcement).
- New: tests/unit/test_permissions_helpers.py. 6 helper unit tests 
  covering satisfying_scopes() outputs for each PermissionScope 
  enum value, plus _SCOPE_CASCADE_ORDER integrity checks (length, 
  enum-coverage, canonical-match).
- CLAUDE.md: Current state entry for 6.9.3.1. New "Org hierarchy 
  coupling" maintenance convention documenting the three sync points 
  (DDL org_node_type_enum, frontend doc section 5.5, this tuple). 
  One new forward-note: FN-AB-NN (PermissionScope enum expansion). 
  One existing forward-note updated in place: the 6.9.2 entry on 
  _require_platform_auth retirement now reflects post-cascade context.
- BUILD_PLAN.md: Section 6.9 status flipped to "6.9.1 DONE; 6.9.2 
  DONE; 6.9.3.1 DONE; 6.9.3.2 TODO". Step 6.9.3 entry split into 
  6.9.3.1 (this commit) and 6.9.3.2 (retrofit, next).
- prompts/step-6_9_3_1-scope-cascade-2026-05-13.md bundled.
- pytest 294 → 308 (8 cascade integration + 6 helper unit). mypy 
  strict clean.
- _SCOPE_CASCADE_ORDER lists all 8 hierarchy levels (GLOBAL, TENANT, 
  BUSINESS_UNIT, HQ, COUNTRY, REGION, STORE, DEPARTMENT) even though 
  v0 enum has only 3 (GLOBAL, TENANT, STORE). Forward-compatible: 
  adding REGION to the enum requires zero changes to the helper. 
  Extra levels in the order are inert because no catalogue rows 
  reference them.
- No new endpoints. No catalogue changes. No role grant changes. 
  No DDL changes. No Alembic migration.

Unblocks Step 6.9.3.2 (endpoint retrofit + per-resource anchor deps 
+ mandatory-gate-discipline test).
EOF
)"
git status
```

Substitute actual counts (N) and final FN-AB numbers. Ask operator:
"Run? yes / no / edit message".

---

## Coordination

- **Unblocks Step 6.9.3.2.** The retrofit's multi-user-type endpoint
  decisions are simpler with cascade in place (single-tuple gates work
  where audience-dispatch was previously needed).
- **No deploy required.** 6.9.3.1 modifies behavior but no production
  endpoint uses has_permission yet — only the /me/can-do endpoint
  calls it. Frontend testing of /me/can-do with cascade scenarios is
  possible post-commit if desired.
- **No frontend coordination needed.** Cascade is the documented design
  intent (frontend doc section 5.5); this just makes the backend match.

---

## End of prompt
