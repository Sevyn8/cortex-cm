#!/usr/bin/env bash
# =============================================================================
# scripts/env.sh
# =============================================================================
# Source the project's .env into the current shell. Exports PSQL_URL alongside
# DATABASE_URL so direct psql invocations don't trip on the SQLAlchemy driver
# prefix.
#
# WHY THIS EXISTS:
#   uvicorn loads .env at startup. Parallel terminals (where you run
#   `psql`, `alembic`, `pytest`) do NOT inherit those vars. Running any of
#   those without sourcing .env first surfaces as either:
#     - alembic: `RuntimeError: DB_SCHEMA env var is required` (D-15)
#     - psql:    connection to /var/run/postgresql/.s.PGSQL.5432 failed
#                (DATABASE_URL not set, falls back to local socket)
#   This helper centralises the workaround in one place.
#
# USAGE:
#   source scripts/env.sh
#
# Note: must be SOURCED, not executed. Running `./scripts/env.sh` exports
# vars into a subshell that exits immediately — no effect. Calling
# `source scripts/env.sh` (or shorthand `. scripts/env.sh`) loads vars
# into the calling shell where they persist.
#
# After sourcing:
#   $DATABASE_URL  postgresql+psycopg://...   (SQLAlchemy form, used by app + alembic)
#   $PSQL_URL      postgresql://...           (libpq form, used by psql + pg_isready)
#   $DB_SCHEMA     core                       (D-15 — required by every alembic call)
#   ... plus all other vars from .env
#
# IDEMPOTENT: re-sourcing is safe. Set values are simply overwritten.
#
# Place this file at:    scripts/env.sh
# Make it executable:    chmod +x scripts/env.sh    (not strictly needed for
#                                                    sourcing but conventional)
# =============================================================================

# Resolve project root by walking up from this script's location.
# Works whether sourced from project root or from a subdirectory.
_ENV_SH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_PROJECT_ROOT="$(cd "$_ENV_SH_DIR/.." && pwd)"

if [[ ! -f "$_PROJECT_ROOT/.env" ]]; then
    echo "scripts/env.sh: ERROR — .env not found at $_PROJECT_ROOT/.env" >&2
    echo "                copy .env.example to .env and fill in values" >&2
    return 1 2>/dev/null || exit 1
fi

# Auto-export every variable defined in .env into the current shell.
# `set -a` makes all subsequent assignments exported; `set +a` turns it off.
set -a
# shellcheck source=/dev/null
source "$_PROJECT_ROOT/.env"
set +a

# Derive PSQL_URL from DATABASE_URL by stripping the SQLAlchemy driver
# suffix. SQLAlchemy uses `postgresql+psycopg://...` to specify the
# driver; psql/libpq don't understand that and silently fall back to
# the unix socket. The transform here matches the one in check_setup.sh
# (Tier 3) and the existing test_endpoints.sh harness.
if [[ -n "${DATABASE_URL:-}" ]]; then
    export PSQL_URL="${DATABASE_URL/postgresql+psycopg/postgresql}"
fi

# Cleanup the local-only variables we used for path resolution. Don't
# pollute the calling shell with these.
unset _ENV_SH_DIR _PROJECT_ROOT

# Sanity output. Comment out if you want this silent.
echo "✓ env.sh: loaded .env (DB_SCHEMA=${DB_SCHEMA:-?}, PSQL_URL=${PSQL_URL:+set})"
