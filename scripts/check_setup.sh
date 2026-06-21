#!/bin/bash
# ============================================================================
# check_setup.sh
#
# Pre-flight checks for Ithina Admin Backend local development environment.
# Run at the start of every Claude Code session and any time something feels
# off. Catches setup drift (DB down, env vars missing, deps out of sync)
# before code work begins.
#
# Usage:
#   ./scripts/check_setup.sh
#
# Exit codes:
#   0 — all checks passed
#   1 — one or more checks failed
#
# Output: PASS / FAIL per check, with actionable hints on failure.
# ============================================================================

set -uo pipefail

# Colour codes (skip if NO_COLOR is set or not a TTY)
if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
    GREEN=$'\033[0;32m'
    RED=$'\033[0;31m'
    YELLOW=$'\033[1;33m'
    CYAN=$'\033[0;36m'
    BOLD=$'\033[1m'
    RESET=$'\033[0m'
else
    GREEN=""; RED=""; YELLOW=""; CYAN=""; BOLD=""; RESET=""
fi

# Track failures
TOTAL_PASS=0
TOTAL_FAIL=0

pass() {
    echo "  ${GREEN}PASS${RESET}  $1"
    TOTAL_PASS=$((TOTAL_PASS + 1))
}

fail() {
    echo "  ${RED}FAIL${RESET}  $1"
    if [[ -n "${2:-}" ]]; then
        echo "        ${YELLOW}hint:${RESET} $2"
    fi
    TOTAL_FAIL=$((TOTAL_FAIL + 1))
}

skip() {
    echo "  ${YELLOW}SKIP${RESET}  $1"
    if [[ -n "${2:-}" ]]; then
        echo "        ${YELLOW}reason:${RESET} $2"
    fi
}

section() {
    echo ""
    echo "${BOLD}${CYAN}=== $1 ===${RESET}"
}

# ----------------------------------------------------------------------------
# Find project root (directory containing pyproject.toml)
# ----------------------------------------------------------------------------

PROJECT_ROOT=""
SEARCH_DIR="$(pwd)"
while [[ "$SEARCH_DIR" != "/" ]]; do
    if [[ -f "$SEARCH_DIR/pyproject.toml" ]]; then
        PROJECT_ROOT="$SEARCH_DIR"
        break
    fi
    SEARCH_DIR="$(dirname "$SEARCH_DIR")"
done

if [[ -z "$PROJECT_ROOT" ]]; then
    echo "${RED}ERROR:${RESET} Could not find project root (no pyproject.toml found in any parent directory)."
    echo "Run this script from inside the admin-backend repo."
    exit 1
fi

cd "$PROJECT_ROOT"
[ -f ".env" ] && set -a && . ".env" && set +a
echo "${BOLD}check_setup.sh${RESET} — Ithina Admin Backend pre-flight"
echo "Project root: $PROJECT_ROOT"

# ----------------------------------------------------------------------------
# Tier 1 — Environment (tools and structure)
# ----------------------------------------------------------------------------

section "Tier 1: Environment"

# Required tools on PATH
for tool in python3 uv psql docker git; do
    if command -v "$tool" >/dev/null 2>&1; then
        pass "$tool found on PATH"
    else
        fail "$tool not found" "install $tool and ensure it is on PATH"
    fi
done

# docker compose (subcommand, not standalone)
if docker compose version >/dev/null 2>&1; then
    pass "docker compose available"
else
    fail "docker compose not available" "install docker-compose-plugin"
fi

# alembic via uv
if uv run alembic --version >/dev/null 2>&1; then
    pass "alembic available via uv"
else
    fail "alembic not available via uv" "run: uv sync"
fi

# Python version (uv-managed)
if [[ -f ".python-version" ]]; then
    pinned=$(cat .python-version)
    pass ".python-version pinned to $pinned"
else
    fail ".python-version missing" "run: uv python pin 3.12"
fi

# Required directories
for dir in src/admin_backend db/raw_ddl migrations/versions scripts; do
    if [[ -d "$dir" ]]; then
        pass "directory exists: $dir"
    else
        fail "directory missing: $dir" "create it or check git status"
    fi
done

# Required top-level files
for file in pyproject.toml docker-compose.yml CLAUDE.md BUILD_PLAN.md; do
    if [[ -f "$file" ]]; then
        pass "file exists: $file"
    else
        fail "file missing: $file" "check git status"
    fi
done

# DDL files present (8 expected after lookups added)
ddl_count=$(find db/raw_ddl -maxdepth 1 -name "Ithina_postgres_SQL_DDL_*.sql" 2>/dev/null | wc -l)
if [[ "$ddl_count" -ge 8 ]]; then
    pass "DDL files present: $ddl_count found in db/raw_ddl/"
else
    fail "expected 8 DDL files in db/raw_ddl/, found $ddl_count" "verify git status; missing files may be uncommitted"
fi

# ----------------------------------------------------------------------------
# Tier 2 — Services (Docker + Postgres container)
# ----------------------------------------------------------------------------

section "Tier 2: Services"

# Docker daemon up
if docker info >/dev/null 2>&1; then
    pass "Docker daemon running"
else
    fail "Docker daemon not running" "run: sudo systemctl start docker (or start Docker Desktop)"
fi

# Postgres container running
container_name="ithina-postgres"
if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$container_name"; then
    pass "Postgres container running: $container_name"

    # Health status
    health=$(docker inspect --format='{{.State.Health.Status}}' "$container_name" 2>/dev/null || echo "no-healthcheck")
    if [[ "$health" == "healthy" ]]; then
        pass "Postgres container reports healthy"
    elif [[ "$health" == "starting" ]]; then
        fail "Postgres container still starting" "wait a few seconds and re-run"
    elif [[ "$health" == "unhealthy" ]]; then
        fail "Postgres container reports unhealthy" "check: docker logs $container_name"
    else
        skip "Postgres health status: $health"
    fi
else
    fail "Postgres container not running" "run: docker compose up -d"
fi

# ----------------------------------------------------------------------------
# Tier 3 — Connectivity and env vars
# ----------------------------------------------------------------------------

section "Tier 3: Connectivity"

# DATABASE_URL set
if [[ -n "${DATABASE_URL:-}" ]]; then
    pass "DATABASE_URL is set"
else
    fail "DATABASE_URL not set" "export DATABASE_URL=postgresql+psycopg://user_admin_backend:password_admin_backend@localhost:5432/ithina_platform_db"
fi

# pg_isready against local Postgres
if command -v pg_isready >/dev/null 2>&1; then
    if pg_isready -h localhost -p 5432 -U user_admin_backend >/dev/null 2>&1; then
        pass "Postgres accepting connections on localhost:5432"
    else
        fail "Postgres not accepting connections on localhost:5432" "verify container is up and port 5432 is exposed"
    fi
else
    skip "pg_isready not available; skipping connectivity check"
fi

# psql can actually query
if command -v psql >/dev/null 2>&1 && [[ -n "${DATABASE_URL:-}" ]]; then
    # Use the DATABASE_URL but psql doesn't accept the SQLAlchemy-style prefix;
    # build a libpq-compatible URL by stripping "+psycopg".
    psql_url="${DATABASE_URL/postgresql+psycopg/postgresql}"
    if PGPASSWORD="" psql "$psql_url" -c "SELECT 1;" >/dev/null 2>&1; then
        pass "psql can SELECT 1 from the database"
    else
        fail "psql cannot connect to the database" "check DATABASE_URL credentials and that container is running"
    fi
fi

# Other useful env vars (warn but don't fail; they're needed for app startup but not for setup)
for var in JWT_ISSUER JWT_AUDIENCE APP_REGION ENVIRONMENT LOG_LEVEL; do
    if [[ -n "${!var:-}" ]]; then
        pass "$var is set"
    else
        skip "$var not set" "needed when running the FastAPI app; not blocking for setup checks"
    fi
done

# ----------------------------------------------------------------------------
# Tier 4 — DB state
# ----------------------------------------------------------------------------

section "Tier 4: DB state"

if [[ -n "${DATABASE_URL:-}" ]] && command -v uv >/dev/null 2>&1; then
    if uv run alembic current >/dev/null 2>&1; then
        current=$(uv run alembic current 2>&1 | tail -n 1)
        if [[ -z "$current" || "$current" == *"INFO"* ]]; then
            pass "alembic current runs (no migrations applied yet, or up to date)"
        else
            pass "alembic current: $current"
        fi
    else
        fail "alembic current failed" "check DATABASE_URL and migrations/env.py configuration"
    fi
else
    skip "alembic check (DATABASE_URL or uv missing)"
fi

# Application role attributes. RLS is silently bypassed if the role has
# SUPERUSER or BYPASSRLS. Production-correct posture is NOSUPERUSER
# NOBYPASSRLS. Catches regressions if the local DB is recreated without
# the privilege strip.
if [[ -n "${DATABASE_URL:-}" ]] && command -v psql >/dev/null 2>&1; then
    role_url="${DATABASE_URL/postgresql+psycopg/postgresql}"
    # Cast booleans to int to avoid format-quirk surprises ("t"/"f" vs
    # "true"/"false"); 0/1 is unambiguous across psql output modes.
    role_attrs=$(PGPASSWORD="" psql "$role_url" -tAc \
        "SELECT rolsuper::int::text || ',' || rolbypassrls::int::text FROM pg_roles WHERE rolname = 'user_admin_backend';" \
        2>/dev/null)
    if [[ "$role_attrs" == "0,0" ]]; then
        pass "app role 'user_admin_backend' is NOSUPERUSER NOBYPASSRLS"
    elif [[ -z "$role_attrs" ]]; then
        fail "could not query pg_roles for 'user_admin_backend'" "verify the role exists and DATABASE_URL is correct"
    else
        flags=""
        IFS=',' read -r is_super is_bypass <<< "$role_attrs"
        [[ "$is_super" == "1" ]] && flags="${flags}SUPERUSER "
        [[ "$is_bypass" == "1" ]] && flags="${flags}BYPASSRLS "
        fail "app role 'user_admin_backend' has elevated privileges (${flags% }), RLS will be silently bypassed" "as a superuser, run: ALTER ROLE user_admin_backend NOSUPERUSER NOBYPASSRLS;"
    fi
else
    skip "app role attribute check (DATABASE_URL or psql missing)"
fi

# CSD-03 protection: local DB role must NOT have a default search_path.
# If rolconfig is set (e.g., ALTER ROLE ... SET search_path = core, public),
# local DB masks unqualified identifiers in raw SQL that would fail in
# cloud — restoring the cloud-emergent bug class CSD-03 closed across
# commits dd496bd / 1516484 / 6204fbd. See CLAUDE.md's "Note on raw
# text() SQL — schema-qualify ALL non-public identifiers" for the full
# convention.
if [[ -n "${DATABASE_URL:-}" ]] && command -v psql >/dev/null 2>&1; then
    role_url="${DATABASE_URL/postgresql+psycopg/postgresql}"
    # rolconfig is text[] or NULL; coalesce to empty string for a clean
    # post-strip signal. Any non-empty value indicates a role-default
    # search_path (or other setting) is configured — which masks
    # CSD-03 bugs locally.
    rolconfig=$(PGPASSWORD="" psql "$role_url" -tAc \
        "SELECT COALESCE(array_to_string(rolconfig, ','), '') FROM pg_roles WHERE rolname = 'user_admin_backend';" \
        2>/dev/null)
    if [[ -z "$rolconfig" ]]; then
        pass "app role 'user_admin_backend' has no rolconfig (CSD-03 protection)"
    else
        fail "app role 'user_admin_backend' has rolconfig set (masks CSD-03 bugs locally): ${rolconfig}" "as a superuser, run: ALTER ROLE user_admin_backend RESET search_path;"
    fi
else
    skip "app role rolconfig check (DATABASE_URL or psql missing)"
fi

# ----------------------------------------------------------------------------
# Tier 5 — Code state (deps, types, imports)
# ----------------------------------------------------------------------------

section "Tier 5: Code state"

# Lock file in sync with pyproject.toml
if uv lock --check >/dev/null 2>&1; then
    pass "uv.lock is in sync with pyproject.toml"
else
    fail "uv.lock out of sync with pyproject.toml" "run: uv sync"
fi

# .venv exists
if [[ -d ".venv" ]]; then
    pass ".venv exists"
else
    fail ".venv missing" "run: uv sync"
fi

# admin_backend package importable
if uv run python -c "import admin_backend" 2>/dev/null; then
    pass "admin_backend package importable"
else
    fail "admin_backend package not importable" "run: uv sync ; check src/admin_backend/__init__.py"
fi

# mypy can parse src (only if there's any code beyond __init__.py)
src_files=$(find src/admin_backend -name "*.py" 2>/dev/null | wc -l)
if [[ "$src_files" -le 1 ]]; then
    skip "mypy check (no code yet beyond __init__.py)"
else
    if uv run mypy --strict src/admin_backend >/dev/null 2>&1; then
        pass "mypy --strict passes on src/admin_backend"
    else
        fail "mypy --strict has errors" "run: uv run mypy --strict src/admin_backend"
    fi
fi

# pytest can collect tests (only if tests/ has any test files)
test_files=$(find tests -name "test_*.py" 2>/dev/null | wc -l)
if [[ "$test_files" -eq 0 ]]; then
    skip "pytest collection (no tests yet)"
else
    if uv run pytest --collect-only -q >/dev/null 2>&1; then
        pass "pytest can collect tests"
    else
        fail "pytest collection failed" "run: uv run pytest --collect-only ; fix import errors"
    fi
fi

# ----------------------------------------------------------------------------
# Summary
# ----------------------------------------------------------------------------

section "Summary"

total=$((TOTAL_PASS + TOTAL_FAIL))
echo "  ${GREEN}Passed:${RESET} $TOTAL_PASS / $total"
if [[ "$TOTAL_FAIL" -gt 0 ]]; then
    echo "  ${RED}Failed:${RESET} $TOTAL_FAIL"
    echo ""
    echo "${RED}${BOLD}Setup is not ready. Fix the FAIL items above before proceeding.${RESET}"
    exit 1
else
    echo ""
    echo "${GREEN}${BOLD}All checks passed. Setup is ready.${RESET}"
    exit 0
fi
