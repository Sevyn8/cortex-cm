# Step 6.18.1 : ADMIN.ROLES.OVERRIDE.GLOBAL catalogue seed delta

## Mental Model

Two-row catalogue delta. Adds the new permission tuple that will gate
the future 6.18.3 PATCH endpoint, and grants it to SUPER_ADMIN. Mirrors
Step 6.17.1 pattern: Excel edit for local, inline SQL for Cloud SQL,
test count updates.

## Implementation Plan

1. Excel edit: permissions sheet +p40 (ADMIN.ROLES.OVERRIDE.GLOBAL);
   role_permissions sheet +r_super_admin x p40 grant.
2. Local seed: `uv run python -m scripts.seed_dev_data --reset`.
3. Cloud SQL: operator-run inline UPSERT in Cloud SQL Studio.
4. Test count updates in EXPECTED_VISIBLE_COUNTS_PLATFORM.
5. BUILD_PLAN + CLAUDE.md edits.

## Retro

Manual operator-driven step (no CC implementation in the catalogue
sub-step; CC handles only verification + text-file edits + commit).

- Local applied via Excel + seed loader. Cloud SQL applied via inline
  UPSERT.
- Cross-env audit-actor drift surfaced as FN-AB-55 (bootstrap user
  referenced locally; Anjali used in cloud).
- Cloud SQL Studio required schema-qualifying `core.uuidv7()` (function
  exists but not on default search_path). Mirrors known Cloud SQL
  search_path issue from earlier dashboard fix (FN-AB-19 / D-29 era).
- Excel ghost-row gotcha during edit: openpyxl tracked one extra blank
  row after the initial p40 entry causing first seed load to fail with
  NotNullViolation on a fully-blank row; resolved by clearing and
  re-entering the new row.
- Verification clean: both local and cloud at permissions=36,
  role_permissions=132, with SUPER_ADMIN holding the new permission.
- This step is a hard precondition for 6.18.3 PATCH endpoint's gate
  resolution; without it, the gate `(ADMIN, ROLES, OVERRIDE, GLOBAL)`
  has no catalogue row to resolve through.
