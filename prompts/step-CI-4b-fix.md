# Step CI-4b-fix: use cm_user_id app_metadata key (Auth0 reserves user_id)

## Context (a real bug found by live testing)

`provision_auth0_user` (Step CI-4b) built the Auth0 app_metadata with the key
`user_id`. Auth0 REJECTS `user_id` as a reserved/restricted app_metadata property
(confirmed live: "Payload validation error: Invalid property app_metadata.user_id"),
so provisioning would fail against live Auth0 on every tenant-user creation.

Fix: write the key as `cm_user_id` instead. Proven on the Auth0 side: the deployed
Login Action reads `md.cm_user_id` and stamps it as the `https://sevyn8.com/user_id`
CLAIM, so the token claim CM's verifier (CI-3) reads is unchanged (still `user_id`).
Only the app_metadata KEY changes, to avoid the Auth0 reserved-name collision.

## Standing discipline

- Pre-flight: `./scripts/check_setup.sh` (note unrelated FAILs, proceed; no DB).
- Commit: `Step CI-4b-fix: use cm_user_id app_metadata key (Auth0 reserves user_id)`.
- Bundle: code + test change + this prompt + a BUILD_PLAN note.
- No Co-Authored-By. No em-dashes. Branch off main; local commit only; no push/PR.

## Changes (three files)

1. `src/admin_backend/auth/provisioning.py`: in the app_metadata dict, key
   `user_id` -> `cm_user_id` (value `str(user_id)` unchanged), plus a one-line
   comment explaining the Auth0 reserved-name collision and the Login Action mapping.
2. `tests/unit/test_provisioning.py`: the success-path app_metadata assertion key
   `user_id` -> `cm_user_id` (value unchanged).
3. `src/admin_backend/auth/auth0_management.py`: the two docstring mentions of the
   example app_metadata shape (`{tenant_id, user_type, user_id}` -> `cm_user_id`),
   so the one place documenting the expected shape matches the Auth0-safe key.

## Explicitly NOT changed

- `provisioning.py` log-context `user_id` field (a structured-log field, not app_metadata).
- `provisioning.py` `created["user_id"]` (reads Auth0's own response user_id = the sub).
- `auth0_management.py` docstring "carries ``user_id`` = the sub" (the Auth0 response field).
- `tests/unit/test_auth0_management.py` (the opaque pass-through blob: it correctly
  tests that the domain-agnostic client posts whatever app_metadata it is given; the
  key value there is arbitrary, not CM's production provisioning key).
- The token claim name (still `https://sevyn8.com/user_id` via the Action), the CI-3
  verifier, the AuthContext, or how user_id flows once verified.

## Grep confirmation

The only production app_metadata write of the key is provisioning.py; the only
assertion of CM's provisioning app_metadata key is test_provisioning.py. No other
place writes or asserts an app_metadata `user_id` key. All other `user_id`
occurrences across the repo are SQL bind-param names, path params, JWT-claim keys,
role-assignment payloads, or the Auth0 response sub field.

## Acceptance

`uv run mypy --strict src/admin_backend` clean; `uv run pytest tests/unit` green
(the updated provisioning test + the existing 141).
