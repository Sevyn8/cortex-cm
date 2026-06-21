# Step 6.18.2 : GET /api/v1/roles/{role_id} detail endpoint

**Date:** 2026-05-19
**Owner:** CLAUDE_CODE
**Status:** DONE-LOCAL
**Cloud deploy:** deferred (batched with 6.18.3)

## Mental model

The role-edit screen needs three things on first paint: the role's
metadata (name, code, description, audience, status, user_count), the
permissions currently granted to that role (with display labels for
the chip UI), and the permissions grantable to that role (so the
checkbox grid can render the unchecked cells without a second API
call).

A naive design would compose these from three existing endpoints
(`/roles/{id}` + `/roles/{id}/permissions` + `/permissions`) and let
the frontend do the set arithmetic. We don't take that path because
the "available" set is audience-dependent: TENANT-audience roles
cannot hold `scope='GLOBAL'` permissions structurally (audience-scope
coherence). Pushing that rule to the frontend means every consumer of
the catalogue has to know the rule; centralising it at the repo
keeps the wire shape uniform and the contract auditable.

Hence one endpoint, one response. The response embeds `permissions`
and `available_permissions`, both as `PermissionDetail` rows with
labels resolved server-side. The frontend reads the response and
renders. No second round trip, no set arithmetic, no enum vocabulary
to ship to the client.

## Implementation plan (executed)

1. `PermissionDetail` schema added next to `PermissionRead` (separate,
   not extension — D-31 append-only). Mirrors `PermissionMatrixRow`'s
   shape minus the `cells` field.
2. `RoleDetail` schema added next to `RoleListItem`. Imports
   `PermissionDetail`; embeds two lists of it.
3. Repo helper `_select_permissions_with_labels` lifted to
   module-level inside `repositories/roles.py`. Two modes
   (`only_held` boolean) + optional `exclude_global` for LD2.
   Schema-qualified raw `text()` per CSD-03; ORDER BY mirrors
   `list_permissions_for_role` (module/resource/action/scope/code/id
   ascending; enum-ordinal sort on the enum columns).
4. `RolesRepo.get_detail_by_id` composes 3 calls: `get_by_id` for
   the audience-gated role + user_count, then the helper twice.
   Returns `None` if the role is missing or audience-gated.
5. New handler `get_role` at `GET /roles/{role_id}`. Reuses
   `_audience_filter_for(auth)` + `RoleNotFoundError`. Declared
   after `/{role_id}/permissions` per FastAPI convention.
6. Path added to `GATE_EXEMPT_PATHS` (8th entry) per LD6.
7. Tests D1-D8 + docstring count refresh. Test file went 23 -> 32
   collected (32 passed).
8. Smoke + endpoint scripts extended with 3 new assertions each.
9. Docs (rbac.md E7 section, openapi.json regen, BUILD_PLAN.md
   6.18.2 entry + DONE-LOCAL flip, CLAUDE.md pointer, this file).

## Retro

### What went smoothly

- The pre-flight Bucket 4 finding (PermissionMatrixRow already has the
  4-label shape) made schema authoring mechanical: copy structurally,
  drop `cells`.
- Mirroring `permission_matrix.py`'s 4-LEFT-JOIN block kept the SQL
  shape consistent across two consumers.
- All 11 pre-flight checks matched expectations; no surprises during
  implementation.

### What surfaced

- **`role.audience` enum comparison gotcha (D7 caught it).** First
  cut wrote `exclude_global = str(role.audience) == "TENANT"`. The
  Role ORM hydrates to a `RoleAudience` enum which subclasses `str`,
  but `Enum.__str__` returns `"RoleAudience.TENANT"` not `"TENANT"`.
  D7 caught this in one shot (assertion: GLOBAL not in available_ids
  failed). Fix: direct equality `role.audience == "TENANT"` works
  because the StrEnum's `__eq__` defers to `str.__eq__`. Lesson worth
  capturing for future enum-driven branches: prefer the enum-value
  compare (`role.audience.value == "TENANT"`) or the equality directly
  on the enum instance, never `str()` around an Enum.

- **Smoke harness stale JWTs.** First smoke run showed 12
  PERMISSION_DENIED on unrelated endpoints (tenants, dashboard,
  modules). Cause: the seeded user UUIDs rotate per reseed, the
  pinned JWT files carry stale user_ids, and `has_permission` denies
  cleanly without matching grant rows. Fix: regenerate
  `anjali-7d.jwt` and `marcus-t-7d.jwt`. Not a Step 6.18.2 issue;
  worth capturing as a recurring foot-gun for the next reseed.
  (The 3 new D-series smoke probes passed on both runs — they don't
  depend on the caller's grants since the endpoint is GATE_EXEMPT.)

### Decisions worth noting

- **Helper extraction (Surface-and-stop #3 path).** The prompt asked
  to choose between (a) extracting a shared label-JOIN helper to
  `roles.py` + `permission_matrix.py` or (b) keeping two copies.
  Chose hybrid: a module-level helper inside `roles.py` shared
  between the held and available paths in `get_detail_by_id`, but
  NOT lifted across files. `permission_matrix.py` keeps its own
  near-identical block. Rationale: the two consumers return
  structurally different shapes (matrix rows with `cells[]` vs
  PermissionDetail-shaped dicts), and the WHERE clauses meaningfully
  differ; extracting across files would force parametrised SELECT
  composition for ~12 lines of duplication. Not worth the indirection
  at v0 scale. If a third consumer arrives, revisit.

- **Sort basis (LD8 cross-check).** LD8 says "code ascending" and
  cites `list_permissions_for_role`'s ORDER BY (module/resource/
  action/scope ASC) as the equivalent form. Used the latter for
  consistency with the existing E3 endpoint — enum-ordinal on each
  column. Equivalent at v0 because catalogue codes follow the
  dot-tuple format.

- **PermissionDetail field set.** Dropped `created_at` and
  `updated_at` from `PermissionDetail` (present on `PermissionRead`).
  The role-edit screen does not need permission lifecycle timestamps;
  the catalogue is admin-managed via migrations. Less noise on the
  wire; D-31 append-only is preserved (`PermissionRead` keeps both
  fields).

### Not deferred (resolved here)

- FN-AB-30 read-gate hardening: explicitly deferred per LD6 / Q-new-3
  operator decision. New endpoint joins existing 4 GATE_EXEMPT role
  endpoints. Documented in `auth/gate_allowlist.py` v0 exempt set.

### Forward

- Step 6.18.3 (PATCH endpoint) consumes the schema + repo shape
  introduced here. The PATCH handler will gate on
  `ADMIN.ROLES.OVERRIDE.GLOBAL`; GET stays exempt.
