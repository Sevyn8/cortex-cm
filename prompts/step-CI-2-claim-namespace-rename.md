# Step CI-2: rename JWT claim namespace ithina.com to sevyn8.com (prompt)

## Context

`https://ithina.com` is a CLIENT domain; `https://sevyn8.com` is ours. The JWT
custom-claim namespace was using the client domain. This is a SURGICAL rename of
the claim namespace ONLY, because the Auth0 Login Action and DIS's verifier must
agree on this exact claim prefix. It is NOT the general de-Ithina work (a separate
deferred task per FORK_ANCESTOR.md, 163 files, infra-bearing).

## Standing discipline

- Pre-flight: run `./scripts/check_setup.sh`; FAILs unrelated to this task (no
  local Postgres, `DATABASE_URL` unset) are noted and do not block (no DB needed).
- Read `docs/architecture.md` and `docs/architecture_RBAC.md` per the CLAUDE.md
  workflow before changes.
- Commit convention: `Step <id>: <one-line description>`. CI-2 continues the
  out-of-band series.
- Per-commit bundle: the work + this prompt file + a short BUILD_PLAN.md "Step
  CI-2" note + the D-24 update, committed once together.
- No Co-Authored-By trailers. No em-dashes anywhere.
- Plan mode first; build only after approval; commit LOCAL ONLY (no push, no PR).

## In scope (these and only these)

1. `src/admin_backend/auth/stub.py`: `NAMESPACE` literal -> `https://sevyn8.com`
   (the four `CLAIM_*` derive via f-string and update automatically, not
   hand-edited); the docstring claim list -> `https://sevyn8.com/*`.
2. `tests/integration/test_rbac_writes_repo.py` (3 sites): replace the hardcoded
   `payload["https://ithina.com/user_id"]` literal with the imported constant
   `CLAIM_USER_ID` from `admin_backend.auth.stub`, so it cannot drift again.
3. `docs/architecture.md`: the claim-namespace lines only (diagram lines 181-182,
   the prose reference at line 292, and the JWT sample claim lines 326-328) ->
   `sevyn8.com`; DELETE the `https://ithina.com/roles` sample line (contradicts
   D-24, identity-only token). LEAVE the `iss`/`aud` lines (321-322) untouched.
4. `CLAUDE.md` D-24: the four claim descriptions -> `https://sevyn8.com/*`, plus a
   one-line note that the namespace was corrected from the client domain
   `ithina.com` to `sevyn8.com`.

## Out of scope (do NOT touch)

architecture.md `iss`/`aud` (`https://ithina.auth0.com/`, `https://api.ithina.com`);
GCP project names; the database name `ithina_platform_db`; the `ithina-postgres`
container; deployment hostnames (`admin-*.ithina.com`); the `.ithina.com` cookie
domain; fixture emails (`@ithina.ai`/`@ithina.local`); the CI workflow
`JWT_AUDIENCE` placeholder (`https://api.ithina.com`). All deferred to the separate
de-Ithina task per FORK_ANCESTOR.md / the Auth0Client step.

## Pre-flight finding (surfaced and approved into scope)

`docs/architecture.md:292` also referenced `https://ithina.com/tenant_id` (not in
the original line list). It is the same surgical claim-namespace class in the same
file; leaving it would make the doc self-inconsistent. Approved for inclusion in
the rename. It is NOT the deferred de-Ithina scope.

## Verify

- The four `CLAIM_*` resolve to `https://sevyn8.com/*`.
- No remaining `https://ithina.com` in the in-scope files; the out-of-scope
  `iss`/`aud` lines are unchanged.
- `uv run mypy --strict src/admin_backend` and
  `uv run pytest tests/unit --ignore=tests/unit/test_engine.py --ignore=tests/unit/test_session.py`
  pass (`test_stub_auth.py` exercises the constants through verify -> AuthContext,
  proving the new namespace round-trips). The integration test edits are
  constant-based and validate fully in a DB-enabled run.
