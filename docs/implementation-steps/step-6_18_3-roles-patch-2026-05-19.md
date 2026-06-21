# Step 6.18.3 : PATCH /api/v1/roles/{role_id} role-edit endpoint

**Date:** 2026-05-19
**Owner:** CLAUDE_CODE
**Status:** DONE-LOCAL
**Cloud deploy:** deferred (batched with 6.18.2 at next Phase 6 deploy)

## Mental model

This is the v0 role-edit write surface. Three concerns dominate the
design:

1. **Platform-admin bootstrap safety.** The OVERRIDE.GLOBAL permission
   is the privilege bit that lets a user edit roles. If an edit
   removes OVERRIDE.GLOBAL from the last role with an active holder,
   no one can issue further role edits and the platform locks itself
   out. The two-layer invariant (Layer 1 pre-check + Layer 2 tripwire)
   makes this structurally impossible at v0 scale. At larger scale,
   concurrent edits could race past Layer 1, so Layer 2 catches the
   bug-or-race that slips through.

2. **SUPER_ADMIN as the bootstrap anchor.** SUPER_ADMIN is the seeded
   role that holds OVERRIDE.GLOBAL initially. A misconfigured edit
   to SUPER_ADMIN itself could create downstream surprises no gate
   catches (a careless rename, an accidental permission removal that
   later interacts with cascade). v0 takes the conservative posture:
   SUPER_ADMIN is uneditable via API; operator workflow is direct SQL.
   v1 lifts this with audit + review (FN-AB-57).

3. **Audit trail preservation under diff-replace.** Role permissions
   are PATCH'd as a replace-set, but the repo applies the diff: only
   the actual additions and removals touch DB rows. Unchanged rows
   preserve their `created_at` and `created_by_*` audit columns. This
   matches the Step 6.14 precedent for tenant_user_role_assignments
   and is the same operator-trust contract: "who originally granted
   this permission" is preserved across re-saves.

The pre-check ordering (LD17) is the operational shape: cheap checks
first (SUPER_ADMIN, status), then catalogue validation (permission
existence), then audience-scope coherence, then the OVERRIDE invariant.
Each layer short-circuits with a typed error; the more expensive
checks only run when the simpler ones pass.

## Implementation plan (executed)

Per the file-by-file change list:

1. `RoleUpdateRequest` schema with `extra='forbid'`.
2. Re-export from `schemas/__init__.py`.
3. 6 new error classes in `errors.py`.
4. `OVERRIDE_GLOBAL_CODE` constant + `_count_override_global_active_holders`
   + `_resolve_override_global_permission_id` + `RolesRepo.update`.
5. `patch_role` handler + local `_actor_type_from_auth` (third copy).
6. New W-series test file with `cleanup_role_perms_for_roles` fixture.
7. New RW-series repo test file with same cleanup fixture.
8-10. Smoke + endpoint scripts: +5 probes each.
11-12. `docs/endpoints/rbac.md` E8 section + OpenAPI regen.
13-14. BUILD_PLAN.md status flips + CLAUDE.md pointer + 4 FN-ABs.
15. This step doc.

## Retro

### What went well

- Pre-flight Check #10's dry-run of the Layer 1 invariant SQL caught
  the structure issue early: confirmed the query returns 0 holders
  when SUPER_ADMIN is excluded, which is the exact state Layer 1
  tests for. Saved debugging time at integration-test phase.
- The 6.14 diff-replace precedent + the existing stores PATCH pattern
  made the repo skeleton mechanical. Most of the implementation
  energy went into the two-layer invariant logic.
- The `super_admin_jwt` fixture (from Step 6.11.2 / 6.18.2) already
  exists and gives the W-tests a real SUPER_ADMIN JWT with OVERRIDE
  grants. No new infra needed for the happy path.

### What surfaced

- **Test fixture FK-cascade issue.** The PATCH handler INSERTs
  `role_permissions` rows that no test fixture tracks. The standard
  `make_role + make_permission + make_role_permission` teardown chain
  fails on the permission DELETE because `role_permissions` still
  references the perm. Resolution: added a per-test cleanup fixture
  `cleanup_role_perms_for_roles` that purges all junction rows for
  tracked roles BEFORE the standard teardowns fire. Listed LAST in
  the test signature so pytest LIFO teardown runs it first. Discipline:
  every PATCH test that touches `permission_ids` registers its
  role.id. Documented inline in the fixture docstring.

- **Orphan rows from earlier crashed test runs.** During iteration,
  several test failures left orphan rows in `core.permissions` and
  `core.role_permissions`. The orphans then caused subsequent test
  runs to hit duplicate-code violations on `make_permission`. Manually
  cleaned twice during the build. The cleanup fixture above should
  prevent the recurrence going forward, but a wholesale
  truncate-and-reseed (`scripts/seed_dev_data --reset`) is the
  bigger hammer for fully-clean state.

- **SQLAlchemy `::uuid` cast syntax conflict.** First cut of the
  invariant SQL used Postgres's `::uuid` cast syntax (`:exclude_role_id::uuid IS NULL`).
  SQLAlchemy's bind parser sees `:bind::type` and produces a syntax
  error because `:` is the bind prefix and `::` is the cast operator;
  they collide adjacent. Fix: use `CAST(:bind AS UUID)` explicitly,
  matching the rest of the codebase. Captured this footgun mentally
  for future raw `text()` SQL.

- **`session.expire_all()` + lazy load = MissingGreenlet.** After the
  raw UPDATE/INSERT/DELETE bypass SA's ORM, calling `session.expire_all()`
  invalidates all cached attributes on tracked instances. Subsequent
  attribute access on a tracked instance triggers a lazy-load SELECT.
  In an async session, that lazy-load fires outside the greenlet
  context and raises `MissingGreenlet`. Fix: capture the role.id
  value BEFORE `expire_all()` and use the captured value (or the
  function parameter `role_id`) for post-expire references. Function
  parameter UUID is identity-equal to `role.id` here.

### Decisions worth noting

- **`audience="PLATFORM"` kwarg INCLUDED on the gate (Surface-and-stop
  resolution).** LD4 literal said "No audience= kwarg on the gate"
  but the codebase precedent (tenants.py + modules_access.py) for
  PLATFORM-only writes consistently uses the kwarg, and the
  gate-discipline meta-test asserts the marker for the new endpoint
  via `_PLATFORM_ONLY_WRITE_ROUTES`. Operator confirmed
  INCLUDE; LD4 wording superseded.

- **`_actor_type_from_auth` local third copy (Surface-and-stop
  resolution).** Three router files now carry byte-identical copies
  of the helper. Operator confirmed local copy + FN-AB-58 for future
  shared-module promotion. The local copy keeps `routers/v1/*`
  modules decoupled at the cost of mechanical duplication.

- **Layer 2 tripwire ALSO catches the `OVERRIDE.GLOBAL permission
  row missing entirely` case.** When `_resolve_override_global_permission_id`
  raises `InternalInvariantViolationError` if the permission row
  doesn't exist in the catalogue, the invariant cannot be evaluated.
  Reusing the same error class for "permission missing" and "post-write
  count is zero" keeps the wire shape uniform: both are 500
  INTERNAL_ERROR with class details in the log only.

- **Sort order of `permissions` / `available_permissions` in the
  PATCH response.** PATCH reuses `get_detail_by_id` for the response,
  which inherits Step 6.18.2's sort: module/resource/action/scope/code/id
  ascending. Same contract as GET; nothing to test separately.

### Forward items

- FN-AB-57 SUPER_ADMIN API editability deferred to v1 (audit log
  prerequisite).
- FN-AB-58 `_actor_type_from_auth` promotion deferred to next
  write-router step or dedicated auth/ reorg.
- FN-AB-59-CRITICAL race-condition mitigation deferred to post-v0
  operational-hardening pass. CRITICAL marker (new convention) flags
  it as low-probability but high-blast-radius.
- FN-AB-60-CRITICAL runtime permission catalogue API + enum
  decoupling deferred to v1 scope decision. CRITICAL marker for the
  same reason.
- Step 6.2 audit_log integration: PATCH operations will gain audit
  entries when that step ships.
