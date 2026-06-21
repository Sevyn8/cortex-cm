# Prompt — Step 6.9.1: `has_permission()` core (targeted SQL check)

> Generated 2026-05-12. Calibrated against codebase HEAD at commit 9462e11 
> ("modules: retire ROOS from Python vocabulary and seed data"). Pytest 
> baseline: 263 passes, 0 failures.
>
> Paste this entire block into a fresh Claude Code session to start 
> Step 6.9.1.

---

## Standing discipline (read first)

### On the code sketches in this prompt

Code blocks and SQL sketches below are STARTING POINTS, not the answer. 
The operator drafts prompts without live access to the codebase. You 
have live access. Use it.

Where you have a better implementation than what's sketched — because 
you've read the actual surrounding code, the actual table indexes, 
the actual existing patterns — IMPLEMENT THE BETTER VERSION. Surface 
the deviation in your report with a one-line reason.

Specifically:
- SQL sketches are illustrative. If a different SQL shape produces 
  the same answer with a better query plan, use the better shape.
- Python code shapes follow common patterns but may not match this 
  codebase's exact conventions. Read existing similar-purpose files 
  before writing new code (for new repository code, read existing 
  Repo files; for new schemas, read existing similar Pydantic 
  schemas).
- File locations in this prompt are suggestions. Use a different 
  location if the suggestion conflicts with existing organization. 
  Surface your choice.
- Function signatures may be over-engineered. Refine if a different 
  shape reads cleaner.
- If a claim about existing code in this prompt is verifiably wrong, 
  do what the actual code requires. The actual file at HEAD is the 
  source of truth.
- If a locked decision starts requiring workarounds when implemented, 
  STOP and surface. Locked decisions can be revisited via the design 
  conversation; silent workaround cannot happen.

Locked decisions (in the "Locked decisions" section) remain locked. 
Everything else is calibrated guidance — refine where you can 
improve it.

### Documentation writing

Updates to CLAUDE.md, BUILD_PLAN.md, architecture.md must be 
technical, sharp, and concise. Documentation has long shelf life.

Rules:
1. State facts. Skip throat-clearing. ("It is worth noting that..." 
   → just state the fact.)
2. Active voice, present tense for current state. Past tense only 
   for historical record.
3. One sentence per fact. Don't pack multiple claims into compound 
   sentences.
4. Specific over general. "Improves performance" → "1 query per 
   check instead of N+1." "Various tests" → "12 tests covering X, 
   Y, Z."
5. Cite by reference, don't repeat. "(per D-34)" not paraphrase.
6. Match the surrounding document's style. CLAUDE.md uses brief 
   bulleted Current state entries. BUILD_PLAN.md uses denser 
   step-body prose.
7. No meta-commentary. "This was a significant change because..." 
   → just describe the change.
8. Cut adjectives that don't add information. "Comprehensive test 
   coverage" → "12 tests covering...".

Bad example (avoid):
> "This step introduces the has_permission function which is a 
> critically important component of the RBAC enforcement layer. 
> The implementation carefully handles both PLATFORM and TENANT 
> users through a robust dispatch mechanism. Comprehensive testing 
> has been added to ensure correctness across various scenarios."

Good example (match this style):
> "has_permission() at src/admin_backend/auth/permissions.py. Single 
> SQL query, dispatched on auth.user_type. PLATFORM reads 
> platform_user_role_assignments + role_permissions + permissions. 
> TENANT adds org_nodes (composite key per D-34) and 
> tenant_module_access (ENABLED filter). Cascade via Postgres ltree 
> <@. 12 integration tests at tests/integration/test_has_permission.py."

Same information, half the words, more specific.

### Definition of done

Before reporting complete, verify:
1. All tests in the step's scope pass.
2. All previously-passing tests still pass (no regressions).
3. mypy strict clean on every file touched.
4. EXPLAIN ANALYZE captured for the new queries. No sequential 
   scans on indexed tables. No unexpected plan choices. Include 
   the output in your report.
5. Re-read each new file end-to-end. If anything reads forced or 
   unclear, refactor before reporting complete.
6. CLAUDE.md and BUILD_PLAN.md updates are sharp per the 
   Documentation writing rules above. Verbose meandering prose is 
   not done; it's draft.
7. Forward-notes have actual FN-AB numbers, not placeholders.
8. No TODO comments in code. Deferred items are forward-notes in 
   CLAUDE.md or they're not deferred.
9. Pre-commit checks (check_setup.sh, pytest, mypy, alembic check) 
   all pass.

If any item is not met, the work is not done. Surface the gap.

---

## Context: why this step exists and why now

Section 6.9 (RBAC enforcement layer) is the first work of Stage 2. Three 
sub-steps:

- **6.9.1 (this step):** `has_permission()` — a single SQL query that 
  answers "does this user have this specific permission tuple, at this 
  target?"
- **6.9.2 (next step, starts immediately after 6.9.1 ships):** FastAPI 
  gate dependency + `/me/permissions` + `/me/can-do` + 
  `PermissionDeniedError`.
- **6.9.3:** Retrofit existing GET endpoints with the gate primitive 
  from 6.9.2.

This step ships the engine. Nothing actually runs at request time after 
this step lands — `has_permission()` is callable but not yet called by 
any production code path. 6.9.2 wires it in.

### Design intent (locked during design conversation, do not deviate)

The design conversation resolved several questions; the key choices:

1. **Targeted SQL per gate, not PermissionSet enumeration.** The 
   permission catalogue is structured so each endpoint exercises 
   exactly one permission tuple. A single SQL query per check is 
   cheaper than building a full PermissionSet per request and 
   iterating it in Python.

2. **Cascade in Postgres, not Python.** Use `ltree::<@` operator for 
   the descendant-or-self check. No Python helper for path comparison.

3. **Module-access filter at query time.** TENANT-side query JOINs to 
   `tenant_module_access` and filters `status = 'ENABLED'`. PLATFORM 
   side does not (Ithina staff administer modules even when disabled).

4. **Binary denial code in v0.** ReasonCode enum has two values 
   initially: `GRANT_MATCHED`, `NO_MATCHING_GRANT_OR_OUT_OF_SCOPE`. 
   Granular codes (cascade vs module vs no-match) deferred to when 
   Step 6.16 audit log writes need them.

5. **PermissionGrant dataclass shipped here, consumed in 6.9.2.** 
   The `/me/permissions` endpoint in 6.9.2 returns a list of 
   PermissionGrant. We ship the dataclass in 6.9.1 to establish the 
   stable type before the endpoint that consumes it lands.

6. **Three-layer error model via the exception (option C from design 
   conversation).** `has_permission()` returns `(bool, ReasonCode, 
   developer_detail: str)`. The user-facing message and audit-field 
   accessor land on `PermissionDeniedError` in 6.9.2; not in 6.9.1.

### Out of scope for 6.9.1

- FastAPI dependency wiring (`get_permission_set`, `get_target_anchor_path`, 
  `require()` factory) → 6.9.2.
- `PermissionDeniedError` exception class with user_message and 
  audit_fields accessors → 6.9.2.
- `/me/permissions` endpoint and its broader query → 6.9.2.
- `/me/can-do` endpoint → 6.9.2.
- Retrofitting existing endpoints with the gate → 6.9.3.
- Audit log writes triggered by denials → Step 6.16.
- Impersonation enforcement (read-only-during-impersonation check) → 
  deferred via forward-note; revisit when impersonation ships.
- Permission caching per request or per user → deferred; forward-note 
  for future scale.
- target_anchor resolution pattern (universal dep vs per-endpoint vs 
  inline) → 6.9.2 design.

---

## Pre-flight

1. Run `./scripts/check_setup.sh`. Expect 35/35.
2. `git log --oneline -5` — confirm HEAD is `9462e11 modules: retire 
   ROOS from Python vocabulary and seed data` (or later if more 
   commits have landed; surface any unexpected commits).
3. `git status` — note any pre-existing items in the working tree; do 
   not stage them. Surface anything unexpected to the operator.
4. `uv run alembic heads` — expect `3e05299cb533` (no migration in 
   this step).
5. `uv run pytest --tb=no -q | tail -5` — expect 263 passes, 0 failures. 
   **If anything other than 263 passes, stop and report.**
6. **Cloud cleanup check.** Run against local Postgres:
   ```
   psql "$DATABASE_URL" -c "
   SET search_path TO core, public;
   SELECT COUNT(*) FROM tenant_module_access WHERE module = 'ROOS';
   SELECT COUNT(*) FROM lookups WHERE list_name='module_code' AND code='ROOS';
   "
   ```
   Expect both counts = 0 (ROOS retirement at HEAD removed them locally; 
   operator runs cloud-side cleanup separately). If non-zero, surface 
   and stop — the design assumes ROOS is fully retired from data.
7. Read `CLAUDE.md` fully. Focus on the following decisions and 
   invariants. The descriptions below are reminders pointing at why 
   each is relevant to this step; trust CLAUDE.md's actual content 
   over these descriptions if they conflict.
   - **D-13** — audit-actor patterns. New code should not surface 
     audit-actor columns.
   - **D-15** — `DB_SCHEMA` from environment. Schema-qualify raw SQL 
     if any is used.
   - **D-21** — UUIDv7 default.
   - **D-24** — JWT carries identity only; no roles/permissions on 
     AuthContext.
   - **D-27** — `NULLIF(current_setting('app.tenant_id', TRUE), '')::uuid` 
     in RLS policies.
   - **D-29** — PLATFORM RLS visibility via OR-branch clause.
   - **D-31** — Append-only response shapes.
   - **D-34** — Composite FK pattern on tenant-side tables. The 
     `has_permission` TENANT query must JOIN via the composite key 
     `(tenant_id, org_node_id)` to `org_nodes(tenant_id, id)`.
   - **AI-RBAC-05** — Permission cascade rule (anchored grant covers 
     descendants).
   - **AI-RBAC-06** — Cross-tenant write injection guard.
   - **FN-AB-22** — Auth0 expansion. **Confirmed irrelevant to 
     6.9.1** during design; AuthContext shape is stable contract; 
     resolver consumes AuthContext, not Auth0 specifics. Flagged 
     here only so you don't waste cycles on it.
8. Read `BUILD_PLAN.md` Section 6.9 entry. Note Step 6.9.1's recorded 
   scope is "Resolver core + tests"; this work expands that scope 
   slightly (PermissionGrant dataclass, type-drift fix, three forward 
   notes). BUILD_PLAN's Step 6.9.1 entry gets rewritten in this commit 
   to reflect actual scope shipped.
9. `docs/architecture.md` — 6.9.1 does NOT touch this file. Skip 
   reading it during pre-flight. Architecture.md's Authorisation 
   section gets updated when Section 6.9 fully completes (after 
   6.9.3).
10. Read `reports/step-6_9_1-resolver-design-investigation-2026-05-11.md` 
    if you find it useful for grounding. The investigation surfaced 
    facts the design conversation worked from. Especially:
    - F-AUTH-1, F-AUTH-2: AuthContext shape (8 fields, identity-only)
    - F-REPO-3: post-Step-6.8.1 split is handled at call site
    - F-CATALOG-3: PLATFORM/TENANT audience implicit in table choice
    - F-TRAP-3: composite-FK JOIN pattern on tenant side
11. Read `reports/step-6_9_1-preflight-checks-2026-05-12.md`. Confirms:
    - Pytest baseline 263, clean
    - No Python ltree library installed (Postgres ltree extension does 
      all path comparison)

---

## Step ID and intent

**Step 6.9.1** — single deliverable:

- `has_permission()` function (callable; not yet called by production)
- `PermissionGrant` dataclass (definition; used by 6.9.2)
- `ReasonCode` enum (definition; binary)
- Type-drift fix on `RoleAssignmentsRepo.list_tenant_assignments` 
  return annotation
- Integration tests against seeded Postgres
- Forward-notes in CLAUDE.md (3)
- BUILD_PLAN.md Step 6.9.1 entry rewritten to reflect actual scope

### Scope in

- `has_permission(session, auth, module, resource, action, scope, 
  target_anchor) → tuple[bool, ReasonCode, str]` at the locked location.
- `PermissionGrant` frozen dataclass at the locked location.
- `ReasonCode` StrEnum with two values: `GRANT_MATCHED`, 
  `NO_MATCHING_GRANT_OR_OUT_OF_SCOPE`.
- One-line type-drift fix on `RoleAssignmentsRepo.list_tenant_assignments` 
  return annotation.
- ~13 integration tests at `tests/integration/test_has_permission.py`.
- Three forward-notes added to CLAUDE.md (impersonation enforcement, 
  caching, target_anchor resolution pattern).
- BUILD_PLAN.md Step 6.9.1 entry rewritten to reflect actual scope.
- Prompt file bundled into the commit per per-step convention.

### Scope out

- FastAPI dependency wiring → 6.9.2.
- `PermissionDeniedError` exception class → 6.9.2.
- `/me/permissions` and `/me/can-do` endpoints → 6.9.2.
- Gate retrofit onto existing endpoints → 6.9.3.
- Audit log writes on denials → Step 6.16.
- Impersonation read-only enforcement → forward-noted; deferred to 
  impersonation feature design.
- Permission caching → forward-noted; revisit at scale.
- target_anchor resolution pattern → forward-noted for 6.9.2 design.
- Granular ReasonCode expansion (cascade vs module vs no-match) → 
  deferred until Step 6.16 audit log writes need them.
- `docs/architecture.md` updates → wait until Section 6.9 fully ships 
  (after 6.9.3).
- `docs/endpoints/openapi.json` regeneration → no endpoints in this 
  step, no OpenAPI change.

### Acceptance criteria

- `has_permission()` callable at the locked location with the locked 
  signature.
- Two query paths (PLATFORM, TENANT) implemented per the SQL sketches 
  in the Implementation outline, refined per actual codebase conventions.
- All ~13 integration tests pass.
- 5 LOAD-BEARING tests green: T_C1, T_C3, T_M1, T_X1, T_T3 
  (cascade-correctness, sibling-region denial, module-suspended 
  denial, cross-tenant injection guard, inactive-assignment denial). 
  These are the security-critical correctness checks; any failure is 
  a step-blocker.
- All previously-passing tests still pass. Per-router regression 
  checkpoint at HEAD (extract actual counts from CLAUDE.md Current 
  State during pre-flight; surface the baseline and post-step numbers 
  in the report).
- Type-drift fix verified by reading the actual file before edit.
- mypy strict clean on every file touched.
- `scripts/check_setup.sh` 35/35.
- `scripts/smoke_test.py` and `scripts/smoke_curl.sh` PASS counts 
  unchanged from baseline (no new endpoints in this step).
- EXPLAIN ANALYZE captured for both query paths (PLATFORM + TENANT) 
  against seeded data. No sequential scans on indexed tables. Output 
  included in the report.
- Three forward-notes added with actual FN-AB numbers (not placeholders).
- BUILD_PLAN.md Step 6.9.1 entry rewritten and status flipped to DONE.

### Locked decisions (do not deviate)

These were resolved in the operator/Claude design conversation 
2026-05-12. Do NOT re-litigate.

1. **Targeted single SQL query per check.** Not PermissionSet 
   enumeration. Not multiple queries per check.

2. **Postgres `ltree <@` for cascade.** Not Python helper. The 
   SQL filter is `(:target_anchor IS NULL OR :target_anchor::ltree 
   <@ on_.path)` for the TENANT path.

3. **Module-access filter in SQL.** TENANT path JOINs 
   `tenant_module_access` and filters `tma.status = 'ENABLED'`. 
   PLATFORM path does NOT include this JOIN.

4. **Composite-key JOIN per D-34 on tenant side.** Join 
   `tenant_user_role_assignments` to `org_nodes` via 
   `(tenant_id, org_node_id)` → `(tenant_id, id)`. Not single-column.

5. **Audience dispatch via if/else on `auth.user_type`.** Two 
   separate SQL queries, one per branch. Not a UNION ALL.

6. **Pure ORM with raw SQL inline only if needed for the `<@` 
   operator.** Matches existing `RoleAssignmentsRepo` posture. If 
   raw SQL is used, schema-qualify per D-15 / Step 6.5.1 precedent.

7. **PermissionGrant lives in `src/admin_backend/auth/permission_grant.py`** 
   (or `schemas/permission_grant.py`; pick based on what reads 
   cleanly with existing structure). It is a `@dataclass(frozen=True)` 
   with 5 fields: `module`, `resource`, `action`, `scope`, 
   `anchor_path: str | None`.

8. **ReasonCode lives next to `has_permission`.** A small `StrEnum` 
   with two values in v0:
   - `GRANT_MATCHED`
   - `NO_MATCHING_GRANT_OR_OUT_OF_SCOPE`

9. **`has_permission()` returns `tuple[bool, ReasonCode, str]`.** 
   The third element is a developer-detail string for application 
   logs. Format suggestion (Claude Code adjusts as code reads 
   cleanly):
   - On match: `f"grant matched for ({module},{resource},{action},{scope}) at {anchor or 'platform-side'}"`
   - On no match: `f"no active grant for user_id={user_id} matches ({module},{resource},{action},{scope}) covering target_anchor={target_anchor}"`

10. **Type-drift fix in the same commit.** 
    `RoleAssignmentsRepo.list_tenant_assignments` currently declares 
    `tuple[list[PlatformUserRoleAssignment], int]` as its return 
    type; should be `TenantUserRoleAssignment`. One-line edit. 
    Verify the actual mistake by reading the file before edit.

---

## Implementation outline (Claude Code adjusts as needed)

### File 1: `src/admin_backend/auth/permission_grant.py` — NEW

```python
from dataclasses import dataclass
from src.admin_backend.models.tenant_module_access import ModuleCode
from src.admin_backend.models.permission import (
    PermissionResource, PermissionAction, PermissionScope,
)

@dataclass(frozen=True)
class PermissionGrant:
    """One permission held by one user at one anchor.
    
    Shipped in Step 6.9.1; consumed by Step 6.9.2's /me/permissions 
    endpoint. Not used by has_permission() (which only returns 
    bool/code/detail).
    """
    module: ModuleCode
    resource: PermissionResource
    action: PermissionAction
    scope: PermissionScope
    anchor_path: str | None  # None for PLATFORM grants
```

If `schemas/permission_grant.py` reads better given existing 
conventions, use that location instead. Surface your choice.

### File 2: `src/admin_backend/auth/reason_code.py` — NEW

```python
from enum import StrEnum

class ReasonCode(StrEnum):
    """Permission decision reason codes.
    
    Binary in v0 (matched or not). Granular codes (cascade vs module 
    vs no-match) deferred until Step 6.16 audit log writes need them.
    """
    GRANT_MATCHED = "GRANT_MATCHED"
    NO_MATCHING_GRANT_OR_OUT_OF_SCOPE = "NO_MATCHING_GRANT_OR_OUT_OF_SCOPE"
```

Alternative location: alongside `has_permission()` if the file 
structure prefers co-location.

### File 3: `src/admin_backend/auth/permissions.py` (or similar) — NEW

The `has_permission()` function. Shape:

```python
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.admin_backend.auth.context import AuthContext
from src.admin_backend.auth.reason_code import ReasonCode
from src.admin_backend.config import get_settings
from src.admin_backend.models.tenant_module_access import ModuleCode
from src.admin_backend.models.permission import (
    PermissionResource, PermissionAction, PermissionScope,
)


async def has_permission(
    session: AsyncSession,
    auth: AuthContext,
    module: ModuleCode,
    resource: PermissionResource,
    action: PermissionAction,
    scope: PermissionScope,
    target_anchor: str | None = None,
) -> tuple[bool, ReasonCode, str]:
    """Single-tuple permission check.
    
    Issues one SQL query that asks: does this user have an ACTIVE 
    assignment of an ACTIVE role that grants the requested permission 
    tuple, at an anchor that cascades to cover target_anchor?
    
    For TENANT users, also requires the tenant's module access for 
    the permission's module to be ENABLED.
    
    For PLATFORM users, no module-access check (Ithina staff 
    administer modules including those not yet enabled). PLATFORM 
    grants apply globally; target_anchor is accepted but not used 
    on the PLATFORM path. Callers should pass None (the default) for 
    PLATFORM users.
    
    Returns:
        (allowed: bool, code: ReasonCode, developer_detail: str)
        
        allowed=True only on tuple+cascade match.
        developer_detail is a verbose string for application logs; 
        not surfaced to the user.
    
    Notes:
        - Pure SQL; no Python iteration over grants.
        - Cascade via Postgres ltree <@ operator.
        - Per-request DB read; no caching in v0 (see CLAUDE.md 
          forward-note for future caching point).
    """
    if auth.user_type == ...:  # Compare against whatever AuthContext exposes
        # — use existing enum if user_type is an enum
        # — use Literal string ("PLATFORM") if user_type is typed as Literal
        # Check the AuthContext model definition before writing this line.
        return await _has_permission_platform(
            session, auth, module, resource, action, scope, target_anchor,
        )
    else:
        return await _has_permission_tenant(
            session, auth, module, resource, action, scope, target_anchor,
        )
```

The two internal functions issue separate queries. Schema-qualify 
table names per D-15 / Step 6.5.1 convention. Use `text()` with 
named bind parameters.

**Sketch of TENANT query** (Claude Code refines):

```sql
SELECT 1
FROM {schema}.tenant_user_role_assignments AS tura
JOIN {schema}.role_permissions AS rp
    ON rp.role_id = tura.role_id
JOIN {schema}.permissions AS p
    ON p.id = rp.permission_id
JOIN {schema}.org_nodes AS on_
    ON on_.tenant_id = tura.tenant_id
   AND on_.id = tura.org_node_id
JOIN {schema}.tenant_module_access AS tma
    ON tma.tenant_id = tura.tenant_id
   AND tma.module = p.module
WHERE tura.tenant_user_id = :user_id
  AND tura.status = 'ACTIVE'
  AND p.module = :module
  AND p.resource = :resource
  AND p.action = :action
  AND p.scope = :scope
  AND tma.status = 'ENABLED'
  AND (
    :target_anchor IS NULL
    OR :target_anchor::ltree <@ on_.path
  )
LIMIT 1
```

**Sketch of PLATFORM query:**

```sql
SELECT 1
FROM {schema}.platform_user_role_assignments AS pura
JOIN {schema}.role_permissions AS rp
    ON rp.role_id = pura.role_id
JOIN {schema}.permissions AS p
    ON p.id = rp.permission_id
WHERE pura.platform_user_id = :user_id
  AND pura.status = 'ACTIVE'
  AND p.module = :module
  AND p.resource = :resource
  AND p.action = :action
  AND p.scope = :scope
LIMIT 1
```

Note: PLATFORM grants have no anchor and apply to everything. The 
`target_anchor` argument is accepted but not used on the PLATFORM 
path. Callers should pass None (the default) for PLATFORM users.

### File 4: `src/admin_backend/repositories/role_assignments.py` — MODIFY

One-line edit: change `list_tenant_assignments` return type from
```python
) -> tuple[list[PlatformUserRoleAssignment], int]:
```
to
```python
) -> tuple[list[TenantUserRoleAssignment], int]:
```

Verify the line by reading the file first. Surface if the actual 
state differs from the investigation report's claim.

### File 5: `tests/integration/test_has_permission.py` — NEW

Integration tests against seeded Postgres. Test count: ~12-15 tests.

**Before writing tests**, verify the seed data supports each scenario 
below. For scenarios that can't be exercised against seed data 
unchanged (e.g., T_M1 needs a tenant with PRICING_OS module SUSPENDED 
but seed enables all modules for all tenants), the test must:

1. Mutate state inside the test (e.g., `UPDATE tenant_module_access 
   SET status = 'SUSPENDED' WHERE ...`).
2. Use a transaction-rollback fixture, OR explicitly revert the 
   mutation in test teardown.
3. Be isolated from other tests (no test should depend on or be 
   affected by another test's mutations).

Surface in the report which tests required mutations and how isolation 
was ensured.

If a scenario genuinely cannot be exercised even with mutations (e.g., 
seed has no cascade-relevant grant at any region anchor), surface 
before inventing test fixtures from scratch.

Categories (one test per scenario unless noted; **LOAD-BEARING** 
markers identify the security-critical tests where any regression 
blocks the step):

**PLATFORM path:**
- `T_P1` — SUPER_ADMIN with `ADMIN.USERS.VIEW.GLOBAL` → allowed
- `T_P2` — SUPER_ADMIN with no role for the tuple requested → denied
- `T_P3` — PLATFORM user with `ADMIN.TENANTS.VIEW.GLOBAL` → allowed regardless of target_anchor (PLATFORM ignores anchor)

**TENANT path — tuple matching:**
- `T_T1` — Tenant user with matching grant → allowed (target_anchor=None)
- `T_T2` — Tenant user with no matching tuple → denied
- `T_T3` — **LOAD-BEARING.** Tenant user with inactive assignment → denied. Guards against accidental status filter regression; inactive grants must never authorize action.

**TENANT path — cascade:**
- `T_C1` — **LOAD-BEARING.** Grant anchored at tenant root, target = a store under it → allowed. Canonical cascade-correctness check; if this fails the cascade semantics are broken.
- `T_C2` — Grant anchored at region X, target = store under region X → allowed
- `T_C3` — **LOAD-BEARING.** Grant anchored at region X, target = store under region Y → denied (sibling region). Guards against the str.startswith bug class (segment-boundary respect via Postgres ltree <@).
- `T_C4` — Grant anchored at region X, target_anchor = None → allowed (tuple match sufficient)

**TENANT path — module access:**
- `T_M1` — **LOAD-BEARING.** Tenant user has grant for PRICING_OS but tenant's PRICING_OS access is SUSPENDED → denied. Module-access filter must work; without it, suspended-module access is silently granted.
- `T_M2` — Tenant user has grant for ADMIN, tenant's ADMIN access is ENABLED → allowed

**Cross-tenant safety:**
- `T_X1` — **LOAD-BEARING.** Tenant A user with grant in their tenant cannot pass check for Tenant B's anchor → denied (target_anchor not under user's tenant). Cross-tenant injection guard per AI-RBAC-06.

Use the existing conftest fixtures (`platform_session`, 
`tenant_session_factory`, `make_tenant`, etc.). Re-use the seed data 
where possible; supplement with `make_*` factories where needed.

If a test needs setup that doesn't exist in current conftest, surface 
before adding new fixtures.

### File 6: `CLAUDE.md` — MODIFY

**Add three forward-notes** in the "Forward-notes (parked items)" 
section:

```
### FN-AB-NN — Impersonation enforcement (read-only-during-impersonation)

When impersonation is implemented as a v0 feature, the gate must 
enforce "PLATFORM user impersonating a tenant cannot perform write 
actions during impersonation regardless of grants." Two candidate 
mechanisms: (a) resolver-level — auth carries an impersonation flag 
that denies writes; (b) gate-level — separate dependency intercepts 
write actions during impersonation and denies before has_permission 
runs. Decision deferred to impersonation feature design.
```

```
### FN-AB-NN — has_permission caching

In v0, has_permission runs one SQL query per gate check. For a v0 
fleet (a few thousand users, low traffic), 1-3ms per request is 
acceptable. At larger scale, the query may become hot enough to 
warrant caching. Caching strategies to consider: per-request 
memoization of has_permission results within one request (mitigates 
endpoints that check the same permission tuple multiple times); 
per-user short-TTL cache of has_permission results keyed on the 
full input tuple (Redis or in-memory) at the cost of permission-change 
latency. Revisit when monitoring shows has_permission as the 
bottleneck.
```

```
### FN-AB-NN — target_anchor resolution pattern

Step 6.9.2 needs a FastAPI dependency that produces target_anchor 
from the request (path params → org_node lookup → ltree path). 
Three patterns to choose between: (a) universal dependency that 
knows all endpoint shapes (god-dependency risk); (b) per-endpoint 
anchor dependency (more code, less coupling); (c) inline 
computation in handlers (defeats declarative gate pattern). Decision 
deferred to 6.9.2 design conversation against shipped 6.9.1 code.
```

(Assign FN-AB numbers based on next available slot.)

**Add to "Current state — Completed":**

```
- has_permission() core at Step 6.9.1. Pure-SQL single-tuple 
  permission check with audience dispatch (PLATFORM vs TENANT), 
  Postgres ltree <@ cascade, tenant_module_access JOIN for module 
  filter (TENANT only). PermissionGrant dataclass shipped for 
  6.9.2 consumption. ReasonCode binary enum (granular codes deferred 
  to Step 6.16). Type-drift fix on RoleAssignmentsRepo.list_tenant_-
  assignments return annotation. ~13 new integration tests. 276 
  total pytest passes (263 prior + ~13 new). mypy strict clean. 
  Not yet wired into FastAPI; 6.9.2 ships the gate dependency that 
  calls this function.
```

(Adjust pytest count to actual.)

### File 7: `BUILD_PLAN.md` — MODIFY

**Step 6.9.1 entry rewrite.** Replace existing entry:

```
- **Step 6.9.1 — Resolver core + tests.** [old entry]
```

with:

```
- **Step 6.9.1 — has_permission() core + PermissionGrant + ReasonCode.** 
  
  Pure-SQL single-tuple permission check at `src/admin_backend/auth/permissions.py`.
  Function signature: `has_permission(session, auth, module, resource, 
  action, scope, target_anchor) → tuple[bool, ReasonCode, str]`. 
  Dispatches on `auth.user_type`. PLATFORM reads 
  `platform_user_role_assignments` JOIN through to permissions. TENANT 
  reads `tenant_user_role_assignments` JOIN role_permissions JOIN 
  permissions JOIN org_nodes (composite key per D-34) JOIN 
  tenant_module_access (filter status='ENABLED'). Cascade via Postgres 
  ltree `<@` operator. PermissionGrant `@dataclass(frozen=True)` shipped 
  for Step 6.9.2's `/me/permissions` endpoint to consume. ReasonCode 
  enum binary in v0 (GRANT_MATCHED / NO_MATCHING_GRANT_OR_OUT_OF_SCOPE); 
  granular codes deferred to Step 6.16 audit log needs. Type-drift fix 
  on `RoleAssignmentsRepo.list_tenant_assignments` return annotation. 
  Per-request DB read, no 
  caching (forward-noted for future scale). ~13 integration tests at 
  `tests/integration/test_has_permission.py`. Three forward-notes in 
  CLAUDE.md: impersonation enforcement, has_permission caching, 
  target_anchor resolution pattern (for 6.9.2).
  
  Status: DONE.
```

(Adjust counts as needed.)

### File 8: `prompts/step-6_9_1-has-permission-core-2026-05-12.md`

This prompt file. Bundle into the commit per per-step convention.

(File numbering condensed: no architecture.md change in this step.)

---

## Caution-first risks the prompt explicitly guards against

1. **Cross-tenant injection via wrong-shape JOIN.** Per D-34, the 
   tenant-side JOIN to `org_nodes` MUST use composite key 
   `(tenant_id, org_node_id) → (tenant_id, id)`, not single-column 
   `org_node_id`. Single-column technically works for reads (FKs 
   guard inserts) but violates the house convention and would mask 
   bugs at higher levels. Verified by test T_X1.

2. **Module-access filter applied to PLATFORM users.** PLATFORM 
   users administer modules; their grants must NOT be filtered by 
   `tenant_module_access`. The two query implementations differ 
   precisely here — PLATFORM query has no JOIN to `tenant_module_access`. 
   Verified by tests T_P1, T_P2, T_P3.

3. **ltree extension not loaded in some session contexts.** The 
   `<@` operator requires `ltree` extension. Cloud SQL has it 
   (verified Step 4.1); local should have it (verified by your 
   pre-flight DB check). The query uses raw text for the `<@` 
   operator since SQLAlchemy 2.x doesn't have a native operator for 
   it. Surface if a test fails with "operator does not exist".

4. **target_anchor as ltree-castable string.** The SQL casts 
   `:target_anchor::ltree`. Caller must pass a valid ltree path 
   (dot-separated, label format). For Step 6.9.1 testing, paths 
   come from `org_nodes.path` which is already ltree-formatted, so 
   this is structurally safe. 6.9.2's `get_target_anchor_path` 
   dependency must produce ltree-formatted strings.

5. **`auth.user_type` mismatch with table contents.** Audience-check 
   triggers (Step 6.8.1) prevent a PLATFORM user from having rows in 
   `tenant_user_role_assignments` and vice versa. The dispatch logic 
   trusts these triggers. Test T_T3 covers the case where a row 
   exists but is inactive; cross-audience-row-in-wrong-table is 
   structurally prevented.

6. **`tma.status = 'ENABLED'` blocks legitimate access if 
   tenant_module_access has unexpected status values.** The current 
   schema has `tenant_module_access_status_enum` with values like 
   ENABLED, SUSPENDED, NOT_PROVISIONED. Any non-ENABLED value 
   correctly denies access. Verify the enum values match expectation 
   before locking the SQL — surface if you find unexpected values.

7. **Type-drift fix correctness.** The investigation says 
   `list_tenant_assignments` declares `tuple[list[PlatformUserRole-
   Assignment], int]`. Read the actual file before edit. The fix 
   is a one-line type annotation change. Surface if the actual 
   state differs from the investigation's claim.

8. **ReasonCode in the type signature commits to a public contract.** 
   The enum lives in `auth/reason_code.py`. 6.9.2 will import it. 
   Future steps may extend it. Keep the v0 values stable; don't 
   rename them.

---

## Testing and regression discipline

### New tests

~13 integration tests at `tests/integration/test_has_permission.py`. 
5 are LOAD-BEARING (T_C1, T_C3, T_M1, T_X1, T_T3 — see the test 
catalogue above for what each guards against).

The remaining ~8 tests are standard correctness checks. All must pass; 
none are optional.

### Tests deliberately not added

- **Unit tests on `has_permission()` with mocked sessions.** The 
  function's logic is the SQL query; mocking the session would test 
  the dispatch (which is trivially `if/else`) without exercising the 
  query. Integration tests against seeded Postgres are the only useful 
  verification.
- **Performance/load tests.** v0 scale (~thousands of users, low 
  traffic). EXPLAIN ANALYZE captures the query plan; that's enough 
  performance verification at this stage. Forward-noted in CLAUDE.md 
  for revisit when scale demands.
- **Tests on `PermissionGrant` dataclass.** It's a frozen dataclass 
  with five fields. The dataclass machinery is standard library; 
  testing it directly tests Python, not Ithina code. The dataclass 
  is exercised when 6.9.2's `/me/permissions` consumes it.
- **Tests on `ReasonCode` enum.** Same reasoning. Two enum values; 
  no logic to test.
- **Tests on the type-drift fix itself.** mypy strict pass after the 
  fix is the verification. A dedicated test would be testing the 
  type system.
- **Cross-audience-row-in-wrong-table tests.** Audience-check triggers 
  (Step 6.8.1) structurally prevent a PLATFORM user from having rows 
  in `tenant_user_role_assignments` and vice versa. The triggers are 
  tested in Step 6.8.1's test suite; re-testing here would test the 
  database, not `has_permission()`.

### Regression risk surface

This step adds code that no production path calls. The regression 
risk is narrowly bounded but worth listing:

1. **`RoleAssignmentsRepo.list_tenant_assignments` callers.** The 
   type-drift fix changes a return annotation. If any caller was 
   relying on the wrong type narrowing, mypy may surface the issue. 
   Callers as of HEAD: `routers/v1/role_assignments.py`. Verify the 
   router still passes its tests post-fix. Surface if mypy errors 
   appear at any call site that wasn't expected.

2. **Import-graph from the new files.** The new modules import from 
   `auth/context.py`, `config.py`, `models/permission.py`, 
   `models/tenant_module_access.py`. If any of those have moved or 
   been renamed since the investigation report, the new modules 
   fail to import. The `python -c "from ... import ..."` step in 
   verification catches this.

3. **Pytest collection.** Adding `tests/integration/test_has_permission.py` 
   should not change collection behavior elsewhere. If pytest's 
   total count drops anywhere other than the new file, that's a 
   regression.

4. **Existing `tests/integration/test_role_assignments_router.py` 
   tests.** The router consumes `list_tenant_assignments`; the 
   type-drift fix changes its annotation. Existing tests still pass 
   if their assertions don't depend on the (incorrect) prior 
   annotation.

5. **mypy on the routers/repositories layer.** mypy strict may newly 
   surface narrowing issues at call sites if the fixed annotation 
   changes inference. Surface if errors appear.

---

## Verification harness

Run in order. All must be green before reporting.

```bash
# 0. Pre-verification reseed (in case pytest cleaned up data).
uv run python -m scripts.seed_dev_data --reset

# 0a. Confirm seed counts.
psql "$DATABASE_URL" -c "
SET search_path TO core, public;
SELECT
  (SELECT COUNT(*) FROM tenant_users) AS tu,
  (SELECT COUNT(*) FROM platform_users) AS pu,
  (SELECT COUNT(*) FROM tenant_user_role_assignments) AS tura,
  (SELECT COUNT(*) FROM platform_user_role_assignments) AS pura,
  (SELECT COUNT(*) FROM tenant_module_access WHERE status='ENABLED') AS tma_enabled,
  (SELECT COUNT(*) FROM tenant_module_access WHERE module = 'ROOS') AS roos_count;
"
# Expected (approximate; confirm against CLAUDE.md Current State at 
# HEAD). roos_count MUST be 0 — that's a hard gate per ROOS retirement.
# All other counts are sanity checks; if they differ from CLAUDE.md, 
# trust CLAUDE.md and surface the prompt's stale numbers.
# tu ≈ 17, pu ≈ 3-7, tura ≈ 19, pura ≈ 3, roos_count = 0.

# 1. Type checking.
uv run mypy src/admin_backend/

# 2. Pytest, all tests.
uv run pytest --tb=no -q

# 2a. Per-router regression checkpoint. Each router file must report 
# the same PASS count as before this step (no new endpoints added). 
# Extract baseline counts from CLAUDE.md Current State during pre-flight; 
# the post-step counts must match. Surface any drop immediately.
#
# Typical per-router test counts (extract actual at HEAD from CLAUDE.md):
#   test_tenants_router.py
#   test_platform_users_router.py
#   test_tenant_users_router.py
#   test_org_tree_router.py
#   test_lookups_router.py
#   test_rbac_router.py
#   test_dashboard_router.py
#   test_modules_access_router.py
#   test_role_assignments_router.py
#
# Run per-file as needed if the aggregate count delta is unexpected:
#   uv run pytest tests/integration/test_tenants_router.py --tb=no -q
#   ...etc

# 3. Smoke test still passes.
uv run python -m scripts.smoke_test

# 4. Alembic round-trip (no migration in this step).
uv run alembic heads
uv run alembic check

# 5. Targeted test for has_permission specifically.
uv run pytest tests/integration/test_has_permission.py -v

# 6. Confirm new files are importable cleanly.
uv run python -c "
from src.admin_backend.auth.permission_grant import PermissionGrant
from src.admin_backend.auth.reason_code import ReasonCode
from src.admin_backend.auth.permissions import has_permission
print('OK')
"
```

---

## Report (BEFORE proposing commit)

1. Pre-flight outputs (items 1-11 explicit results).
2. Resolution of design choices made during implementation:
   - PermissionGrant location (auth/ vs schemas/)
   - ReasonCode location (own file vs alongside has_permission)
   - Naming of the has_permission file (permissions.py / 
     permission_check.py / etc.)
3. Diffs:
   - New: `src/admin_backend/auth/permission_grant.py`
   - New: `src/admin_backend/auth/reason_code.py`
   - New: `src/admin_backend/auth/permissions.py` (or chosen location)
   - Modified: `src/admin_backend/repositories/role_assignments.py` 
     (type-drift one-line fix)
   - New: `tests/integration/test_has_permission.py`
   - Modified: `CLAUDE.md` (3 forward-notes added; Current state 
     bullet appended)
   - Modified: `BUILD_PLAN.md` (Step 6.9.1 entry rewritten)
   - New: `prompts/step-6_9_1-has-permission-core-2026-05-12.md`
4. Verification harness output for all sections (0, 0a, 1-6).
5. Pre/post pytest counts. State explicit numbers.
6. Per-test summary: which integration tests passed, mapping to 
   T_P1-T_P3, T_T1-T_T3, T_C1-T_C4, T_M1-T_M2, T_X1.
7. Any deviation from the locked design decisions (should be none; 
   surface immediately if any).
8. Forward-notes: list the 3 forward-notes added with the actual 
   FN-AB numbers used.

Wait for explicit operator authorisation before staging or committing.

---

## Surface-and-stop scenarios

Stop and report (do not work around silently) if:

1. Pytest baseline is not 263 passes at pre-flight.
2. ROOS rows present in `tenant_module_access` or `lookups` locally.
3. `list_tenant_assignments` doesn't actually have the type-drift 
   the investigation reported.
4. Audience-check triggers behave differently than expected (e.g., a 
   PLATFORM user with a row in `tenant_user_role_assignments` is 
   findable in the seed).
5. ltree extension is not available in local Postgres.
6. The `tenant_module_access_status_enum` has unexpected values 
   beyond ENABLED / SUSPENDED / NOT_PROVISIONED.
7. Any of the existing 263 tests start failing during this work.
8. mypy strict surfaces errors that aren't trivially fixable.
9. The seed data doesn't include the test scenarios you need (e.g., 
   no tenant user with cascade-relevant grants). Surface before 
   inventing test fixtures.

If any item above triggers, stop and ask the operator.

---

## After completing

Propose a git commit per CLAUDE.md "After completing a task" Pattern A:

```bash
git status
git add -A
git commit -m "Step 6.9.1: has_permission() core + PermissionGrant + ReasonCode

- New: src/admin_backend/auth/permissions.py (or chosen location).
  has_permission(session, auth, module, resource, action, scope,
  target_anchor=None) → tuple[bool, ReasonCode, str].
  Dispatches on auth.user_type.
  PLATFORM: SELECT 1 from platform_user_role_assignments JOIN
  role_permissions JOIN permissions, filtered by user_id and
  status=ACTIVE.
  TENANT: SELECT 1 from tenant_user_role_assignments JOIN
  role_permissions JOIN permissions JOIN org_nodes (composite key
  per D-34) JOIN tenant_module_access (status=ENABLED filter).
  Cascade via Postgres ltree <@ operator. LIMIT 1 on both paths.
- New: src/admin_backend/auth/permission_grant.py. Frozen dataclass
  for /me/permissions in Step 6.9.2 to consume.
- New: src/admin_backend/auth/reason_code.py. StrEnum with two
  v0 values; granular codes deferred to Step 6.16.
- Modified: src/admin_backend/repositories/role_assignments.py.
  One-line type-drift fix on list_tenant_assignments return
  annotation.
- New: tests/integration/test_has_permission.py. <N> integration
  tests covering PLATFORM, TENANT, cascade, module-access, and
  cross-tenant safety. 5 LOAD-BEARING (T_C1, T_C3, T_M1, T_X1, T_T3).
- CLAUDE.md: Current state — Completed entry for 6.9.1.
  Three new FN-AB forward-notes: impersonation enforcement,
  has_permission caching, target_anchor resolution pattern.
- BUILD_PLAN.md: Step 6.9.1 entry rewritten; status TODO → DONE.
- prompts/step-6_9_1-has-permission-core-2026-05-12.md bundled.
- No new endpoints; openapi.json unchanged.
- No architecture.md change (Section 6.9 update lands when 6.9.3 ships).
- No migrations. No DDL changes. No seed Excel changes.
- pytest <BASELINE> → <BASELINE + N>. mypy strict clean.
  check_setup 35/35. smoke_test unchanged.

Unblocks Step 6.9.2 (FastAPI gate dependency + /me endpoints)."
```

Substitute actual counts (`<N>`, `<BASELINE>`) and final file location 
choices. Ask operator: "Run? yes / no / edit message". On yes, execute 
via bash tool. On no, skip. On edit, prompt for new message.

---

## Coordination

- **Unblocks Step 6.9.2.** The FastAPI gate dependency 
  (`require(module, resource, action, scope)` factory) and the 
  `/me/permissions` + `/me/can-do` endpoints depend on `has_permission()` 
  shipping first. Step 6.9.2 starts immediately after 6.9.1 commits 
  per the operator's locked sequence.
- **No deploy required.** 6.9.1 ships callable code that no production 
  path uses yet. Next deploy bundles 6.9.1 + 6.9.2 + 6.9.3 together 
  when Section 6.9 fully completes.
- **No frontend coordination needed.** Frontend integration with 
  `/me/permissions` lands at 6.9.2.

---

## End of prompt
