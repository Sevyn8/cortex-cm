# Step CI-1: add a unit-tier CI gate (prompt)

## Context

cortex-cm (Customer Master, the identity/RBAC system-of-record) had NO CI gate
(`.github/workflows/` did not exist). This step stands one up as the no-secrets,
unit-tier safety net required before the upcoming Auth0Client verifier change.

## Standing discipline

- Pre-flight: run `./scripts/check_setup.sh` first. FAILs unrelated to this task
  (no local Postgres, `DATABASE_URL` unset) are noted and do not block; this task
  adds CI and does not need the full local stack.
- Read `docs/architecture.md` and `docs/architecture_RBAC.md` per the CLAUDE.md
  workflow before changes.
- Commit convention: `Step <id>: <one-line description>` (not feat()/fix()).
- Per-commit bundle: the work + any CLAUDE.md/BUILD_PLAN.md updates + this prompt
  file, committed once together.
- No Co-Authored-By trailers. No em-dashes anywhere.
- Plan mode first; build only after approval; commit LOCAL ONLY (no push, no PR,
  no branch protection from code).

## Task

Build `.github/workflows/ci.yml` with one job on `pull_request` and `push` to
`main` that:

1. checks out, installs uv, `uv sync --group dev`.
2. generates a throwaway RS256 keypair into `keys/` (CI-ephemeral, never
   committed; the path the StubAuthClient/tests expect: `jwt_private.pem` +
   `jwt_public.pem`).
3. runs `uv run mypy --strict src/admin_backend`.
4. runs the DB-free unit tests: `uv run pytest tests/unit` excluding the two
   DB-bound files.
5. carries the non-secret placeholder env Settings() needs at import time.

## Pre-flight findings (verified against the live repo)

- `ruff` is NOT in the toolchain (not a dependency, not configured, not
  installed). The code is ~67 ruff-lint errors and ~139 files unformatted away
  from clean, so a ruff step would either fail CI or force a sweeping unrelated
  reformat. DECISION D1: omit ruff from this gate; adopt it in a separate future
  PR if wanted. The gate is `mypy --strict` + unit pytest, which is what the repo
  enforces today (`scripts/check_setup.sh`, `docs/architecture.md` "mypy strict
  in CI").
- "Unit tests are DB-free" is only partly true: `tests/unit/test_engine.py` and
  `tests/unit/test_session.py` (10 tests) require a live Postgres (misfiled under
  unit). DECISION D2: exclude them by path now (`--ignore`); reclassify as a
  fast-follow. The remaining 108 unit tests pass DB-free in under a second.
- Importing `admin_backend.models` constructs `Settings()` at import time,
  requiring `database_url`, `db_schema`, `jwt_issuer`, `jwt_audience`. These are
  non-secret config; the gate sets placeholders (a dummy `DATABASE_URL` that is
  never connected to). `ENVIRONMENT=development` + `AUTH_CLIENT_MODE=STUB` satisfy
  the production-guard validators in `config.py`.
- DECISION D3: defer the integration tier (50 tests; needs a Postgres service
  container + migrations) to a fast-follow second job.

## Fast-follows (not in this PR)

- Apply branch protection: require the `ci / unit` check on `main` (via `gh`).
- Integration-tier CI job (Postgres service container + `DATABASE_URL` +
  `alembic upgrade head` + the integration suite).
- Reclassify `tests/unit/test_engine.py` and `tests/unit/test_session.py` (move to
  `tests/integration/` or mark), so the unit gate no longer relies on a path
  ignore-list.
- Optional: adopt ruff (add the dependency + `[tool.ruff]` config + a one-time
  format pass), then add a lint step.
