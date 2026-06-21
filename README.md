# Ithina Admin Backend

Python FastAPI service backing the Ithina platform's Admin Console. Serves read-only endpoints for tenants, stores, users, organisation hierarchy, RBAC, and audit logs. v0 of a multi-version build; writes and additional functionality come in later versions.

This service is the sole writer to its own tables in the master DB. Other Ithina services (DIS, Pricing OS, etc.) operate independently and own their own tables.

---

## Repository layout

```
admin-backend/
├── pyproject.toml             # Python deps via uv
├── docker-compose.yml         # Local Postgres
├── alembic.ini                # Migration tool config
├── migrations/                # Alembic migrations
│   └── versions/
├── db/
│   ├── raw_ddl/               # Source DDL files (read-only; do not edit)
│   └── seeds/                 # Bootstrap, lookups, RBAC, customer data SQL
├── src/admin_backend/         # Application code
├── tests/                     # Unit, integration, e2e tests
├── scripts/                   # check_setup, smoke_test, seed scripts, onboarding
├── k8s/                       # Kubernetes manifests (dev, prod)
├── docs/                      # Architecture, API contract, runbooks
│   └── archive/               # Superseded versions
├── CLAUDE.md                  # Standing context for Claude Code
├── BUILD_PLAN.md              # Step-by-step build plan
└── README.md                  # This file
```

---

## Quick start (local dev)

Prerequisites: Python 3.12+, Docker, Docker Compose, uv (https://docs.astral.sh/uv/), psql.

```bash
# Clone
git clone <repo-url>
cd admin-backend

# Install deps
uv sync

# Start Postgres
docker compose up -d

# Copy env template and fill in values
cp .env.example .env
# Edit .env

# Run pre-flight checks
./scripts/check_setup.sh

# Apply migrations
uv run alembic upgrade head

# Seed lookup data, RBAC, and bootstrap user
./scripts/apply_seeds.sh

# Run the app
uv run uvicorn admin_backend.main:app --reload
```

The app starts on http://localhost:8000.

Health check:

```bash
curl http://localhost:8000/v1/health
# {"status":"ok"}
```

API docs (auto-generated from OpenAPI spec):

```
http://localhost:8000/v1/docs       # Swagger UI
http://localhost:8000/v1/redoc      # ReDoc
http://localhost:8000/v1/openapi.json   # raw OpenAPI spec
```

---

## Authentication during build

v0 uses a local stub for JWT auth (RS256 with a local key pair). Auth0 is the production target; swap is config-only via `AUTH_CLIENT_MODE` env var.

Mint a test JWT:

```bash
uv run python -m admin_backend.auth.testing \
  --user-id <uuid> \
  --tenant-id <uuid> \
  --user-type TENANT
```

Use it:

```bash
curl -H "Authorization: Bearer <jwt>" http://localhost:8000/v1/tenants
```

---

## Common commands

```bash
# Run tests
uv run pytest

# Run with coverage
uv run pytest --cov=admin_backend

# Type check
uv run mypy --strict src/admin_backend

# Format
uv run ruff format src/ tests/

# Lint
uv run ruff check src/ tests/

# Pre-flight (run at start of every Claude Code session)
./scripts/check_setup.sh

# Smoke test against current schema
uv run python scripts/smoke_test.py

# Reset local DB
docker compose down -v && docker compose up -d
uv run alembic upgrade head
./scripts/apply_seeds.sh
```

---

## Where to look for what

| Question | Document |
|---|---|
| Why was X decided this way? | `CLAUDE.md` (decision entries with reasoning) |
| What's the system architecture? | `docs/architecture.md` |
| What's the API contract with the frontend? | `docs/api-contract.md` |
| What's the build plan? | `BUILD_PLAN.md` |
| How do I deploy / rollback / debug prod? | `docs/runbook.md` |
| How do I onboard a new tenant or user? | `docs/data-load.md` |
| How does auth work locally vs prod? | `docs/auth.md` |
| How do I provision GCP? | `docs/gcp-provisioning-runbook.md` |
| What's the git workflow? | `docs/git-cheatsheet.md` |
| What's the historical version of <doc>? | `docs/archive/` |

---

## Working with Claude Code

This project uses Claude Code (Anthropic's CLI agent) for the build phase. CLAUDE.md is the standing context loaded at session start.

If you're picking up the project mid-build:

1. Read CLAUDE.md fully.
2. Read docs/architecture.md fully.
3. Read docs/api-contract.md fully.
4. Read BUILD_PLAN.md to see where the build stands.
5. Run `./scripts/check_setup.sh` to verify your local setup.

---

## Schema

10 tables across 8 DDL files (lookups, platform_users, tenants, tenant_users, org_nodes, stores, RBAC; audit_logs added during the build at Step 6.2).

All tables live in a Postgres schema whose name is supplied per environment via the `DB_SCHEMA` env var (`core` on local). DDLs are unqualified; tables resolve to the configured schema via `search_path`. See CLAUDE.md D-15 for the parameterised-schema decision.

Multi-tenancy enforced via PostgreSQL Row-Level Security (RLS) with FORCE on every multi-tenant table. Application sets `app.tenant_id` per transaction; RLS filters rows automatically.

See `docs/architecture.md` "Schema and storage" and "Multi-tenancy and data isolation" sections for details.
---

## Stack

- Python 3.12, FastAPI, async SQLAlchemy 2.x, psycopg3.
- PostgreSQL 15 (Cloud SQL in production).
- Alembic for migrations, uv for dependency management.
- mypy strict in CI, ruff for lint and format.
- Auth0 (production) / RS256 stub (build phase).
- GKE Autopilot (compute), Cloud SQL (database), Secret Manager (secrets), managed Prometheus + Cloud Logging (observability).

See `docs/architecture.md` "Stack" section for the complete table.

---

## License

Internal Ithina project. Not for external distribution.

---

## Contact

For questions:

- Build / architecture: engineering team.
- GCP / deployment: GCP-helper / DevOps.
- Customer onboarding: Ithina staff.
