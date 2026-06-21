# Prompt — Step 3.5: Dev seed loader from Excel

> Generated 2026-05-03, 02:30 AM. Revised 2026-05-03, 03:15 AM (v2: centralised column-mapping schema). Revised 2026-05-03, 04:30 AM (v3: explicit loader-file enumeration; self-FK loader-bypass guidance; widened NULL detection; AuthContext bypass; lookups dependency note; centralised skipped-sheets).
> Paste this entire block into a fresh Claude Code session to start Step 3.5.
> Loads 11 of 12 sheets from `ithina_dev_seed_data.xlsx` into Postgres so the API returns real data on curl. Honours D-21 (UUIDv7) by stripping IDs and capturing per-sheet `excel_id → db_id` mappings. The first time the dev DB has meaningful content; demonstrates Step 3.3's endpoints end-to-end against actual tenants, stores, users, and modules.

---

## Pre-flight

1. Run `./scripts/check_setup.sh`. Expect 35/35.
2. `git log --oneline -5` — confirm Step 3.4.5 (`cd2a02e452ae` migration) at HEAD.
3. Confirm `data/ithina_dev_seed_data.xlsx` exists at the agreed path. If it's not in the repo yet, this step's first deliverable is committing the file (see File 12 below).
4. Read `CLAUDE.md` fully. Focus on:
   - **D-15** — DB_SCHEMA from environment, search_path-driven name resolution. Loader uses the existing engine; schema is automatic.
   - **D-21** — UUIDv7 via `uuidv7()` PL/pgSQL function. The seed Excel has v4 UUIDs throughout; the loader strips them on insert and lets `uuidv7()` fire, capturing the assigned IDs into a per-sheet mapping for FK substitution. **This is the load-bearing design choice for the loader.**
   - **D-13** — audit-actor patterns. Pattern (a) tables (tenants, tenant_module_access) have typed FK to platform_users with no user_type column; Pattern (b) tables (org_nodes, stores, tenant_users, etc.) have `*_by_user_type` discriminator + nullable IDs. Loader honours both.
   - **D-29** — PLATFORM session can INSERT on all multi-tenant tables (the OR-clause in WITH CHECK predicate). Loader runs as PLATFORM, no special handling.
   - "Note on PG enum columns" subsection — relevant when the loader translates Excel string values into typed columns. The DB does the cast at INSERT time when the value matches the enum's literal; mistyped values fail loudly.
5. Read `BUILD_PLAN.md` Step 3.5 in full. Note: the original entry is sparse; this step's commit rewrites it with the actual scope.
6. Read `docs/architecture.md` "Schema and storage" — confirms the 11-table count and the seed-vs-DDL distinction.
7. Read `data/ithina_dev_seed_data.xlsx` (12 sheets):
   - Use `openpyxl` with `read_only=True` per the file-reading conventions.
   - Sheet inventory: README (skip), platform_users (3), tenants (7), org_nodes (49), stores (25), tenant_users (17), roles (15), permissions (32), role_permissions (164), user_role_assignments (22), audit_logs (8 — UNLOADABLE, no DDL), tenant_module_access (27).
   - Sheet ordering matches FK dependency: load top-to-bottom, skip audit_logs.
8. Read `src/admin_backend/db/engine.py` and `src/admin_backend/db/session.py` — the existing engine + `get_tenant_session` are the only DB primitives the loader uses. No new connection layer.
9. Read `src/admin_backend/auth/context.py` — for the synthetic PLATFORM AuthContext the loader constructs to drive `get_tenant_session`. If AuthContext is a Pydantic BaseModel with required fields beyond user_id/tenant_id/user_type, surface during pre-flight; the loader uses `model_construct` to bypass validation since synthetic credentials don't need to validate against any real platform_users row.
10. Read `src/admin_backend/config.py`. Confirm the settings model's environment field name and the literal value used for production. The prompt assumes `settings.environment == "prod"` per CLAUDE.md line 835; if the actual field name differs (e.g., `settings.env` or `settings.deploy_env`), update the production-refusal guard to match.
11. Read `tests/integration/conftest.py` — `make_tenant`, `make_store`, `make_tenant_user`, `make_platform_user`, `make_tenant_module_access` fixtures from Steps 3.2/3.3/3.4.5. Their commit-then-track-then-DELETE pattern is the closest precedent for the loader's per-row insert; the loader can borrow the same pattern (PLATFORM session, INSERT, capture id, no deletion since loader's job is to populate not clean up).
12. Read `db/raw_ddl/*.sql` for column definitions, CHECK constraints, and FK rules. The loader's per-sheet loaders need to honour every CHECK (status companions, audit-actor XOR, paired-or-both NULL constraints).
13. Read this prompt fully.

---

## Step ID and intent

**Step 3.5** — Dev seed loader. Single Python script under `scripts/seed_dev_data/` that reads `data/ithina_dev_seed_data.xlsx` and inserts 11 sheets' worth of data into the dev Postgres, honouring D-21's UUIDv7 invariant via per-sheet `excel_id → db_id` mapping and FK substitution.

After this step: `curl http://localhost:8000/api/v1/tenants` returns 7 real tenants (Buc-ee's, Żabka, Infomil, GreenLeaf, SmartStore, FreshMart, CornerStop) with their actual store counts, active user counts, and module entitlements. The first time the API surfaces meaningful content.

Seven concrete deliverables:

1. **`data/ithina_dev_seed_data.xlsx`** committed to the repo (if not already present). Single binary file. Source of truth for dev seed data.
2. **`scripts/seed_dev_data/`** module structure with one loader per sheet, an Excel reader, a UUID mapper, a centralised column-mapping schema, and a top-level entry point with `--reset`, `--dry-run`, and `--sheets` CLI flags.
3. **Centralised column mappings** at `scripts/seed_dev_data/column_mappings.py` declaring per-sheet which Excel columns map to DB columns, which are FK references, and which are helpers. Source of truth for the Excel-to-DB column correspondence; drift-detects unknown columns by raising an explicit error rather than silently inserting garbage.
4. **Production-refusal guard** at script startup — refuses to run when `ENVIRONMENT=prod`, exits non-zero, no DB writes.
5. **Four integration tests** at `tests/integration/test_seed_loader.py`: end-to-end run, row-count verification, sentinel-row spot checks, production-refusal. Plus one unit test for the column-mapping drift detector.
6. **`scripts/seed_dev_data/README.md`** — short doc covering invocation, the UUIDv7-substitution mechanism, the column-mapping schema, the audit_logs skip, and the rollback procedure.
7. **BUILD_PLAN.md Step 3.5** rewrite with status DONE, scope-in matching what shipped, plus the existing Step 7.3.1 entry (Excel→SQL converter post-v0) gets a note that this step is its prototype.

CLAUDE_CODE step. Estimated effort: 1.5 days. No DDL changes, no migrations, no schema impact.

---

## Source-of-truth specification

### File 1: `data/ithina_dev_seed_data.xlsx` — new

The seed Excel itself. Commit as a binary blob in the repo. Path is `data/` not `scripts/seed_dev_data/data/` so it's discoverable as a project artefact, not buried in a tool's directory.

If the file is already in the repo at a different path, surface and we'll standardise.

### File 2: `scripts/seed_dev_data/__init__.py` — new

Empty marker file.

### File 3: `scripts/seed_dev_data/__main__.py` — new

CLI entry point. Allows `uv run python -m scripts.seed_dev_data` invocation.

```python
"""Dev seed loader entry point.

Usage:
    uv run python -m scripts.seed_dev_data [--reset] [--dry-run] [--sheets sheet1,sheet2]

Refuses to run if ENVIRONMENT=prod (safety guard).
"""
import argparse
import asyncio
import sys

from admin_backend.config import get_settings


def main() -> int:
    parser = argparse.ArgumentParser(description="Load dev seed data from Excel into Postgres.")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="TRUNCATE seed tables before insert (in reverse-FK order). Destructive.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Read and validate the Excel; do not write to the DB.",
    )
    parser.add_argument(
        "--sheets",
        type=str,
        default=None,
        help="Comma-separated sheet names to load (default: all loadable sheets).",
    )
    args = parser.parse_args()

    settings = get_settings()
    if settings.environment == "prod":
        print(
            "ERROR: seed loader refuses to run with ENVIRONMENT=prod. "
            "This script is for dev/local use only.",
            file=sys.stderr,
        )
        return 2

    from scripts.seed_dev_data.runner import run_seed
    return asyncio.run(run_seed(
        reset=args.reset,
        dry_run=args.dry_run,
        sheets=args.sheets.split(",") if args.sheets else None,
    ))


if __name__ == "__main__":
    sys.exit(main())
```

### File 4: `scripts/seed_dev_data/runner.py` — new

Orchestrator. Calls each sheet loader in dependency order. Manages the `excel_id → db_id` mapping across sheets.

```python
"""Seed runner: orchestrates per-sheet loaders in FK dependency order.

Skips audit_logs (no DDL — Step 6.2 territory). Loads the other 11 sheets.

Transaction boundaries: one transaction per sheet. Each loader commits
at end of its load() function. If sheet N fails mid-load, sheets 1..N-1
are committed and persisted; sheet N rolls back; sheets N+1..end never
ran. The DB ends in a partial state. The user reruns with --reset for
a clean slate. This is intentional for dev seeding — load what you can,
fail loudly, surface the failing sheet by name. Production-style
ingestion (Step 7.3.1) gets richer error handling.

Reference data (lookups table) is NOT loaded by this script. The
lookups rows for module_code are seeded by Step 3.4.5's migration,
so they exist post-migration. Future lookup categories are seeded via
their own migrations or by BUILD_PLAN's Step 6.3 lookup-seeding work.
"""
from __future__ import annotations

import logging
from pathlib import Path
from uuid import UUID

from admin_backend.auth.context import AuthContext
from admin_backend.db.engine import create_engine, create_session_factory
from admin_backend.db.session import get_tenant_session

from scripts.seed_dev_data.excel_reader import read_workbook
from scripts.seed_dev_data.uuid_mapper import UUIDMapper
from scripts.seed_dev_data.loaders import (
    platform_users,
    tenants,
    org_nodes,
    stores,
    tenant_users,
    roles,
    permissions,
    role_permissions,
    user_role_assignments,
    tenant_module_access,
)
from scripts.seed_dev_data.truncate import truncate_seed_tables


SHEETS_IN_ORDER = [
    ("platform_users", platform_users.load),
    ("tenants", tenants.load),
    ("org_nodes", org_nodes.load),
    ("stores", stores.load),
    ("tenant_users", tenant_users.load),
    ("roles", roles.load),
    ("permissions", permissions.load),
    ("role_permissions", role_permissions.load),
    ("user_role_assignments", user_role_assignments.load),
    ("tenant_module_access", tenant_module_access.load),
]
SKIPPED_SHEETS = {"audit_logs"}  # No DDL; Step 6.2 territory.

EXCEL_PATH = Path("data/ithina_dev_seed_data.xlsx")

logger = logging.getLogger(__name__)


def _platform_auth() -> AuthContext:
    """Synthetic PLATFORM AuthContext for the loader's PLATFORM session.

    The synthetic user_id (00000000-...-001) does NOT match any real
    platform_users row. If AuthContext is a Pydantic BaseModel with
    field validators that check against the DB, use model_construct()
    to bypass validation:

        return AuthContext.model_construct(
            user_id=UUID("00000000-0000-0000-0000-000000000001"),
            tenant_id=None,
            user_type="PLATFORM",
            ...other required fields...
        )

    Surface during pre-flight (item 9) what fields AuthContext needs.
    """
    return AuthContext(
        user_id=UUID("00000000-0000-0000-0000-000000000001"),
        tenant_id=None,
        user_type="PLATFORM",
        # ... other AuthContext fields per the model
    )


async def run_seed(
    *,
    reset: bool = False,
    dry_run: bool = False,
    sheets: list[str] | None = None,
) -> int:
    """Run the seed. Returns 0 on success, non-zero on failure."""
    workbook = read_workbook(EXCEL_PATH)

    selected_sheets = sheets or [name for name, _ in SHEETS_IN_ORDER]

    if dry_run:
        logger.info("DRY RUN: reading and validating, no DB writes.")
        for name, loader in SHEETS_IN_ORDER:
            if name not in selected_sheets:
                continue
            sheet_data = workbook[name]
            logger.info("  %s: %d data rows", name, len(sheet_data))
        return 0

    engine = create_engine()
    session_factory = create_session_factory(engine)
    auth = _platform_auth()
    mapper = UUIDMapper()

    if reset:
        logger.info("Reset requested: TRUNCATE-ing seed tables.")
        async for session in get_tenant_session(auth, session_factory):
            await truncate_seed_tables(session)

    for name, loader in SHEETS_IN_ORDER:
        if name not in selected_sheets:
            logger.info("Skipping %s (not in --sheets selection).", name)
            continue
        if name in SKIPPED_SHEETS:
            logger.warning("Skipping %s (no DDL yet).", name)
            continue
        sheet_data = workbook[name]
        if not sheet_data:
            logger.warning("Sheet %s is empty; nothing to load.", name)
            continue
        logger.info("Loading %s (%d rows)...", name, len(sheet_data))
        async for session in get_tenant_session(auth, session_factory):
            await loader(session, sheet_data, mapper)
        logger.info("  ✓ %s loaded.", name)

    logger.info("Seed complete.")
    return 0
```

The exact AuthContext field set must match `auth/context.py` — adapt the construction in `_platform_auth()` accordingly.

### File 5: `scripts/seed_dev_data/excel_reader.py` — new

Single responsibility: read the Excel, return a dict-of-dicts data structure for downstream loaders.

```python
"""Excel reader: turn the seed workbook into structured Python data.

For each sheet (except README and audit_logs), returns a list of dicts
keyed by column header. Helper-only columns (those starting with
underscore: _key, _tenant_key, _parent_key, _role_key, _legal_name_FYI,
etc.) are kept; the loaders consume them to resolve cross-sheet FK
references via the UUIDMapper.

Translates NULL-ish cell values to Python None. Detection is wide:
- Literal string 'NULL' (per the seed Excel's README convention)
- Case variants: 'null', 'Null', etc.
- Whitespace-padded variants: ' NULL ', '  null '
- Empty string and whitespace-only cells

Empty cells (genuinely blank) come back from openpyxl as None already;
the wide detection covers the case where a human types something that
looks like NULL but doesn't match the convention exactly.
"""
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from scripts.seed_dev_data.column_mappings import SHEET_MAPPINGS

# All loadable sheets. Derived from SHEET_MAPPINGS to keep a single
# source of truth — when Step 6.2 lands audit_logs, adding it to
# SHEET_MAPPINGS automatically extends excel_reader's coverage too.
SHEETS_TO_READ = set(SHEET_MAPPINGS.keys())


def _is_null_ish(value: Any) -> bool:
    """True if value should be treated as Python None."""
    if value is None:
        return True
    if isinstance(value, str):
        stripped = value.strip()
        if stripped == "" or stripped.upper() == "NULL":
            return True
    return False


def read_workbook(path: Path) -> dict[str, list[dict[str, Any]]]:
    """Read all loadable sheets. Returns {sheet_name: [{col: value, ...}, ...]}."""
    wb = load_workbook(path, read_only=True, data_only=True)
    result: dict[str, list[dict[str, Any]]] = {}
    for sheet_name in SHEETS_TO_READ:
        if sheet_name not in wb.sheetnames:
            continue
        ws = wb[sheet_name]
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            result[sheet_name] = []
            continue
        headers = list(rows[0])
        data_rows = []
        for row in rows[1:]:
            row_dict = {}
            for h, v in zip(headers, row):
                row_dict[h] = None if _is_null_ish(v) else v
            data_rows.append(row_dict)
        result[sheet_name] = data_rows
    return result
```

### File 6: `scripts/seed_dev_data/column_mappings.py` — new

**Source of truth for Excel-to-DB column correspondence per sheet.** Every per-sheet loader consults this module rather than encoding column rules in its own code. Three benefits:

1. **Drift detection.** Unknown Excel columns (typos, helper columns added without an `_` prefix, schema additions not yet reflected in the DDL) cause the loader to raise an explicit error rather than silently inserting garbage.
2. **Single source of truth.** Anyone reading the codebase sees, in one file, exactly what every Excel column means.
3. **DRY.** Per-sheet loaders become mechanical: iterate the mapping, dispatch by role, do the right thing.

```python
"""Per-sheet column mappings.

Each Excel column has one of three roles:

- DB_COLUMN: maps to a real column in the DDL; passed verbatim to INSERT
  (after 'NULL'-string translation, which excel_reader.py already does).
- FK_REF: an FK; substituted via UUIDMapper.lookup() before INSERT. The
  fk_target names the sheet whose mapper to consult.
- HELPER: not in the DDL; skip entirely. Includes _-prefixed natural-key
  columns (_key, _tenant_key, _parent_key, _role_key, _org_node_key) and
  yellow FYI columns (_legal_name_FYI).

If a per-sheet loader encounters an Excel column NOT in the mapping, it
MUST raise an explicit `UnknownColumnError` with the column name, sheet
name, and a hint to either add the column to the mapping or remove it
from the Excel. Drift detection.

Adding a new sheet or new column to an existing sheet: edit this file
first, then update the loader. Order matters — get the mapping right,
then the loader does the right thing automatically.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final


class ColumnRole(Enum):
    DB_COLUMN = "db_column"     # Real DB column; INSERT verbatim.
    FK_REF = "fk_ref"           # FK; resolve via UUIDMapper before INSERT.
    HELPER = "helper"           # Skip; not a DB column.


@dataclass(frozen=True)
class ColumnSpec:
    name: str
    role: ColumnRole
    fk_target: str | None = None  # For FK_REF only; names the source sheet.

    def __post_init__(self) -> None:
        if self.role is ColumnRole.FK_REF and self.fk_target is None:
            raise ValueError(f"FK_REF column '{self.name}' must have fk_target")
        if self.role is not ColumnRole.FK_REF and self.fk_target is not None:
            raise ValueError(f"Non-FK_REF column '{self.name}' must not have fk_target")


# Type alias for readability.
SheetMapping = list[ColumnSpec]

# Convenience constructors.
def db(name: str) -> ColumnSpec:
    return ColumnSpec(name=name, role=ColumnRole.DB_COLUMN)

def fk(name: str, target: str) -> ColumnSpec:
    return ColumnSpec(name=name, role=ColumnRole.FK_REF, fk_target=target)

def helper(name: str) -> ColumnSpec:
    return ColumnSpec(name=name, role=ColumnRole.HELPER)


# ─── Per-sheet mappings ──────────────────────────────────────────

# platform_users: 14 Excel cols, 13 DB cols. _role_label is helper.
# Self-referential audit: created_by_user_id and updated_by_user_id
# are FK_REF to platform_users itself, requiring the two-phase insert.
PLATFORM_USERS: Final[SheetMapping] = [
    db("id"),
    db("auth0_sub"),
    db("email"),
    db("full_name"),
    db("status"),
    db("invited_at"),
    db("invitation_accepted_at"),
    db("suspended_at"),
    fk("suspended_by_user_id", "platform_users"),
    db("created_at"),
    fk("created_by_user_id", "platform_users"),
    db("updated_at"),
    fk("updated_by_user_id", "platform_users"),
    helper("_role_label"),
]

# tenants: Pattern (a) audit-actors — typed FK direct to platform_users,
# no *_by_user_type columns.
TENANTS: Final[SheetMapping] = [
    db("id"),
    db("name"),
    db("display_code"),
    db("country"),
    db("region"),
    db("tier"),
    db("industry"),
    db("monthly_revenue_usd"),
    db("monthly_revenue_as_of_date"),
    db("number_of_stores"),
    db("number_of_stores_as_of_date"),
    db("primary_contact_name"),
    db("contact_email"),
    db("status"),
    db("created_at"),
    fk("created_by_user_id", "platform_users"),
    db("updated_at"),
    fk("updated_by_user_id", "platform_users"),
    db("suspended_at"),
    fk("suspended_by_user_id", "platform_users"),
    db("terminated_at"),
    fk("terminated_by_user_id", "platform_users"),
    helper("_legal_name_FYI"),
]

# org_nodes: Pattern (b) audit-actors. The DDL allows *_by_user_id to
# point to either platform_users or tenant_users depending on
# *_by_user_type. The seed has all PLATFORM audit-actors (Anjali); the
# loader looks up only in platform_users. If a future seed row uses
# user_type='TENANT', the load FAILS with KeyError — intentional, surfaces
# data drift loudly. To support TENANT audit-actors here, extend
# ColumnRole with FK_REF_DUAL or specialise the org_nodes loader.
ORG_NODES: Final[SheetMapping] = [
    helper("_key"),
    helper("_tenant_key"),
    helper("_parent_key"),
    db("id"),
    fk("tenant_id", "tenants"),
    fk("parent_id", "org_nodes"),  # self-reference; resolves within sheet
    db("path"),                    # ltree pre-computed, insert verbatim
    db("node_type"),
    db("name"),
    db("code"),
    db("status"),
    db("created_at"),
    fk("created_by_user_id", "platform_users"),  # PLATFORM-only assumption
    db("created_by_user_type"),
    db("updated_at"),
    fk("updated_by_user_id", "platform_users"),  # PLATFORM-only assumption
    db("updated_by_user_type"),
    db("archived_at"),
    fk("archived_by_user_id", "platform_users"),  # PLATFORM-only assumption
    db("archived_by_user_type"),
]

# stores: Pattern (b) audit-actors. tenant_id and org_node_id are FK refs.
# Same PLATFORM-only assumption as org_nodes: seed has all PLATFORM
# audit-actors; loader looks up in platform_users.
STORES: Final[SheetMapping] = [
    helper("_org_node_key"),
    helper("_tenant_key"),
    db("id"),
    fk("tenant_id", "tenants"),
    fk("org_node_id", "org_nodes"),
    db("name"),
    db("store_code"),
    db("country"),
    db("timezone"),
    db("address"),
    db("latitude"),
    db("longitude"),
    db("currency"),
    db("tax_treatment"),
    db("status"),
    db("created_at"),
    fk("created_by_user_id", "platform_users"),
    db("created_by_user_type"),
    db("updated_at"),
    fk("updated_by_user_id", "platform_users"),
    db("updated_by_user_type"),
]

# tenant_users: Pattern (b) audit-actors. The seed audit-actors are all
# PLATFORM (Anjali); loader resolves via platform_users.
TENANT_USERS: Final[SheetMapping] = [
    helper("_key"),
    helper("_tenant_key"),
    db("id"),
    fk("tenant_id", "tenants"),
    db("auth0_sub"),
    db("email"),
    db("full_name"),
    db("status"),
    db("invited_at"),
    db("invitation_accepted_at"),
    db("suspended_at"),
    fk("suspended_by_user_id", "platform_users"),
    db("suspended_by_user_type"),
    db("created_at"),
    fk("created_by_user_id", "platform_users"),
    db("created_by_user_type"),
    db("updated_at"),
    fk("updated_by_user_id", "platform_users"),
    db("updated_by_user_type"),
]

# roles: Pattern (b). is_system is bool (openpyxl returns Python bool).
ROLES: Final[SheetMapping] = [
    helper("_key"),
    db("id"),
    db("name"),
    db("code"),
    db("description"),
    db("audience"),
    db("status"),
    db("is_system"),
    db("created_at"),
    fk("created_by_user_id", "platform_users"),
    db("created_by_user_type"),
    db("updated_at"),
    fk("updated_by_user_id", "platform_users"),
    db("updated_by_user_type"),
    db("archived_at"),
    fk("archived_by_user_id", "platform_users"),
    db("archived_by_user_type"),
]

# permissions: minimal audit. No Pattern-based audit-actor columns —
# permissions are seed-level reference data, no per-row attribution.
PERMISSIONS: Final[SheetMapping] = [
    helper("_key"),
    db("id"),
    db("module"),
    db("resource"),
    db("action"),
    db("scope"),
    db("code"),
    db("description"),
    db("created_at"),
    db("updated_at"),
]

# role_permissions: pure mapping table. _permission_code is helper for
# human reading; the FK substitution uses _role_key and _permission_key.
ROLE_PERMISSIONS: Final[SheetMapping] = [
    helper("_role_key"),
    helper("_permission_key"),
    helper("_permission_code"),
    fk("role_id", "roles"),
    fk("permission_id", "permissions"),
    db("created_at"),
    fk("created_by_user_id", "platform_users"),
    db("created_by_user_type"),
]

# user_role_assignments: dual-FK XOR (platform_user_id XOR tenant_user_id).
# Loader passes both verbatim; CHECK constraint enforces XOR at INSERT.
USER_ROLE_ASSIGNMENTS: Final[SheetMapping] = [
    helper("_role_key"),
    helper("_org_node_key"),
    db("id"),
    fk("platform_user_id", "platform_users"),
    fk("tenant_user_id", "tenant_users"),
    fk("role_id", "roles"),
    fk("tenant_id", "tenants"),
    fk("org_node_id", "org_nodes"),
    db("status"),
    db("granted_at"),
    fk("granted_by_user_id", "platform_users"),
    db("granted_by_user_type"),
    db("revoked_at"),
    fk("revoked_by_user_id", "platform_users"),
    db("revoked_by_user_type"),
    db("updated_at"),
]

# tenant_module_access: Pattern (a) audit-actors (PLATFORM-only managed).
TENANT_MODULE_ACCESS: Final[SheetMapping] = [
    helper("_tenant_key"),
    helper("_tenant_name"),
    db("id"),
    fk("tenant_id", "tenants"),
    db("module"),
    db("status"),
    db("enabled_at"),
]


# Master mapping. All loadable sheets present; audit_logs is excluded
# (no DDL — Step 6.2 territory).
SHEET_MAPPINGS: Final[dict[str, SheetMapping]] = {
    "platform_users": PLATFORM_USERS,
    "tenants": TENANTS,
    "org_nodes": ORG_NODES,
    "stores": STORES,
    "tenant_users": TENANT_USERS,
    "roles": ROLES,
    "permissions": PERMISSIONS,
    "role_permissions": ROLE_PERMISSIONS,
    "user_role_assignments": USER_ROLE_ASSIGNMENTS,
    "tenant_module_access": TENANT_MODULE_ACCESS,
}


class UnknownColumnError(Exception):
    """Raised when an Excel column has no entry in SHEET_MAPPINGS for its sheet."""


def validate_columns(sheet_name: str, excel_headers: list[str]) -> None:
    """Verify every Excel column is in the mapping. Raise on drift.

    Called by per-sheet loaders before INSERT. Catches: typo'd columns,
    new helper columns missing the `_` prefix, schema additions not
    reflected in the mapping yet.
    """
    if sheet_name not in SHEET_MAPPINGS:
        raise UnknownColumnError(
            f"sheet '{sheet_name}' has no mapping in SHEET_MAPPINGS"
        )
    known = {spec.name for spec in SHEET_MAPPINGS[sheet_name]}
    unknown = [h for h in excel_headers if h not in known]
    if unknown:
        raise UnknownColumnError(
            f"unknown columns on sheet '{sheet_name}': {unknown}. "
            f"Either add them to column_mappings.py:{sheet_name.upper()} "
            f"or remove them from the Excel."
        )


def excel_columns_for_db_insert(sheet_name: str) -> list[ColumnSpec]:
    """Return only the DB_COLUMN and FK_REF specs for a sheet, in order.

    The loader iterates this list to build the row dict for INSERT.
    HELPER columns are filtered out.
    """
    return [
        spec for spec in SHEET_MAPPINGS[sheet_name]
        if spec.role is not ColumnRole.HELPER
    ]
```

This file is ~250 lines. Most of the volume is the per-sheet declarations themselves. The logic (validate_columns, excel_columns_for_db_insert) is small.

### File 7: `scripts/seed_dev_data/uuid_mapper.py` — new

Per-sheet `excel_id → db_id` mapping. Lets downstream loaders resolve cross-sheet FK references after their dependent rows have been inserted.

```python
"""UUID mapper: tracks excel-id-to-db-id correspondence per sheet.

Honours D-21: every INSERT strips the Excel's v4 UUID and lets the DB's
DEFAULT uuidv7() fire. The mapper captures the assigned UUIDv7 keyed
by the original Excel UUID, so subsequent sheets can resolve their FK
columns by looking up the originally-referenced ID in the mapper and
substituting the now-known db_id.

Sheets reference each other by Excel UUIDs in their FK columns. After
loading, those FK columns contain the dep-sheet's NEW UUIDs (the v7s
the DB assigned), not the original v4s.
"""
from __future__ import annotations

from uuid import UUID


class UnresolvedFKError(KeyError):
    """Raised when an FK references an excel_id that wasn't registered.

    Common causes:
    - Sheet load order is wrong (a sheet referenced before its dependency loaded).
    - Self-referential FK in a sheet that should bypass _base (platform_users).
    - Excel data error (FK points to a non-existent ID).
    - Self-referential FK in a sheet that uses multi-pass loading
      (org_nodes parent_id) — the multi-pass loader catches this and
      defers; only the final pass's persistent failure indicates a real
      cycle or data error.
    """


class UUIDMapper:
    """Per-sheet excel_id -> db_id mapping."""

    def __init__(self) -> None:
        self._maps: dict[str, dict[UUID, UUID]] = {}

    def register(self, sheet: str, excel_id: UUID | str, db_id: UUID) -> None:
        """Record that <excel_id> on <sheet> got assigned <db_id> by uuidv7()."""
        if sheet not in self._maps:
            self._maps[sheet] = {}
        if isinstance(excel_id, str):
            excel_id = UUID(excel_id)
        self._maps[sheet][excel_id] = db_id

    def lookup(self, sheet: str, excel_id: UUID | str | None) -> UUID | None:
        """Look up the db_id for an excel_id on a given sheet.

        Returns None if excel_id is None (NULL column in the Excel —
        excel_reader already translated 'NULL' / '' / whitespace).
        Raises UnresolvedFKError with sheet+excel_id if not registered.
        """
        if excel_id is None:
            return None
        if isinstance(excel_id, str):
            try:
                excel_id = UUID(excel_id)
            except ValueError as e:
                raise UnresolvedFKError(
                    f"FK on sheet '{sheet}' has malformed UUID: {excel_id!r}"
                ) from e
        try:
            return self._maps[sheet][excel_id]
        except KeyError as e:
            raise UnresolvedFKError(
                f"FK target not registered: sheet='{sheet}' excel_id={excel_id}"
            ) from e

    def is_mapped(self, sheet: str, excel_id: UUID | str) -> bool:
        """True if (sheet, excel_id) is in the mapper. Used by org_nodes
        multi-pass loader to test whether a parent is ready before insert."""
        if isinstance(excel_id, str):
            try:
                excel_id = UUID(excel_id)
            except ValueError:
                return False
        return sheet in self._maps and excel_id in self._maps[sheet]
```

### File 8: `scripts/seed_dev_data/loaders/__init__.py` — new

Empty.

### File 9: `scripts/seed_dev_data/loaders/_base.py` — new

The generic loader pattern. Most per-sheet loaders share the same structure: validate the sheet's columns against the mapping, iterate rows, dispatch each column by its role, INSERT, capture the returned ID into the mapper.

**Important — when NOT to use `_base`:** any sheet whose FK columns can reference rows in *the same sheet* needs special handling, because `_base.build_insert_row` calls `mapper.lookup(...)` and the referenced row may not exist in the mapper yet. Two such sheets exist:

- **`platform_users`** — `created_by_user_id`, `updated_by_user_id`, `suspended_by_user_id` all reference platform_users itself. Anjali is `created_by` herself. Two-phase loader bypasses `_base`.
- **`org_nodes`** — `parent_id` references org_nodes itself. Children must be inserted after parents. Multi-pass loader uses `_base.insert_and_register` but iterates carefully: a child whose parent isn't yet mapped is deferred to the next pass rather than calling `insert_and_register` (which would raise UnresolvedFKError).

For all other sheets — `tenants`, `stores`, `tenant_users`, `roles`, `permissions`, `role_permissions`, `user_role_assignments`, `tenant_module_access` — the standard `_base.insert_and_register` works directly.

```python
"""Shared loader pattern. Per-sheet loaders specialise this for their
table-specific INSERT shape, but the column-dispatch logic is uniform.

Self-referential sheets (platform_users, org_nodes) bypass or defer-loop
this base because mapper.lookup raises UnresolvedFKError when a row
references another row that hasn't been inserted yet.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from scripts.seed_dev_data.column_mappings import (
    ColumnRole, ColumnSpec, SHEET_MAPPINGS,
    validate_columns, excel_columns_for_db_insert,
)
from scripts.seed_dev_data.uuid_mapper import UnresolvedFKError, UUIDMapper


def build_insert_row(
    sheet_name: str,
    excel_row: dict[str, Any],
    mapper: UUIDMapper,
) -> dict[str, Any]:
    """Translate an Excel row dict into a DB insert row dict.

    - HELPER columns: dropped.
    - DB_COLUMN: passed verbatim.
    - FK_REF: looked up via mapper[fk_target]. None values pass through
      (NULL FK is legitimate for nullable columns and the dual-FK XOR
      pattern in user_role_assignments).

    The 'id' DB column is also dropped (DB DEFAULT uuidv7() fires).

    Raises UnresolvedFKError if any FK_REF points to an unmapped excel_id.
    """
    insert_row: dict[str, Any] = {}
    for spec in excel_columns_for_db_insert(sheet_name):
        if spec.name == "id":
            continue  # let DB DEFAULT uuidv7() fire
        excel_value = excel_row.get(spec.name)
        if spec.role is ColumnRole.DB_COLUMN:
            insert_row[spec.name] = excel_value
        elif spec.role is ColumnRole.FK_REF:
            assert spec.fk_target is not None
            insert_row[spec.name] = mapper.lookup(spec.fk_target, excel_value)
    return insert_row


async def insert_and_register(
    session: AsyncSession,
    sheet_name: str,
    table_name: str,
    excel_row: dict[str, Any],
    mapper: UUIDMapper,
) -> UUID:
    """Build the INSERT row, execute, register the assigned id in mapper.

    Returns the new db_id. INSERT uses RETURNING id so we get the
    DB-assigned uuidv7 back.

    Raises UnresolvedFKError if the row references unmapped FK targets;
    the caller (e.g., org_nodes' multi-pass loader) handles by deferring.
    """
    insert_row = build_insert_row(sheet_name, excel_row, mapper)
    columns = list(insert_row.keys())
    placeholders = ", ".join(f":{c}" for c in columns)
    column_list = ", ".join(columns)
    sql = f"INSERT INTO {table_name} ({column_list}) VALUES ({placeholders}) RETURNING id"
    result = await session.execute(text(sql), insert_row)
    db_id: UUID = result.scalar_one()
    excel_id = excel_row.get("id")
    if excel_id is not None:
        mapper.register(sheet_name, excel_id, db_id)
    return db_id
```

### File 10: Per-sheet loaders — `scripts/seed_dev_data/loaders/<sheet>.py`

**Files Claude Code MUST create — 10 per-sheet loaders + 2 module files = 12 files in `loaders/`:**

```
scripts/seed_dev_data/loaders/
├── __init__.py                       # File 8 (empty)
├── _base.py                          # File 9 (shared helpers)
├── platform_users.py                 # File 10a — specialised (two-phase self-reference)
├── tenants.py                        # File 10  — standard
├── org_nodes.py                      # File 10b — specialised (multi-pass parent-first)
├── stores.py                         # File 10  — standard
├── tenant_users.py                   # File 10  — standard
├── roles.py                          # File 10  — standard
├── permissions.py                    # File 10  — standard
├── role_permissions.py               # File 10  — standard
├── user_role_assignments.py          # File 10  — standard
└── tenant_module_access.py           # File 10  — standard
```

The runner.py imports each module and calls `<module>.load(session, rows, mapper)`.

**Standard loader file** — applies to 8 of the 10 sheets (`tenants`, `stores`, `tenant_users`, `roles`, `permissions`, `role_permissions`, `user_role_assignments`, `tenant_module_access`):

```python
"""Loader for <sheet_name>."""
from typing import Any
from sqlalchemy.ext.asyncio import AsyncSession
from scripts.seed_dev_data.column_mappings import validate_columns
from scripts.seed_dev_data.loaders._base import insert_and_register
from scripts.seed_dev_data.uuid_mapper import UUIDMapper

SHEET_NAME = "<sheet_name>"
TABLE_NAME = "<table_name>"


async def load(
    session: AsyncSession,
    rows: list[dict[str, Any]],
    mapper: UUIDMapper,
) -> None:
    if rows:
        validate_columns(SHEET_NAME, list(rows[0].keys()))
    for row in rows:
        await insert_and_register(session, SHEET_NAME, TABLE_NAME, row, mapper)
    await session.commit()
```

For each of the 8 standard loaders, the file is exactly this shape with `SHEET_NAME` and `TABLE_NAME` constants set. ~15 lines per file.

**Specialised loaders** — the two sheets with self-referential FK chains needing custom handling:

**`platform_users`** (File 10a): Self-referential audit. Anjali's `created_by_user_id` references herself. Two-phase insert:

```python
"""Loader for platform_users with two-phase self-reference handling."""
from typing import Any
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from scripts.seed_dev_data.column_mappings import validate_columns, excel_columns_for_db_insert
from scripts.seed_dev_data.uuid_mapper import UUIDMapper

SHEET_NAME = "platform_users"
TABLE_NAME = "platform_users"


async def load(
    session: AsyncSession,
    rows: list[dict[str, Any]],
    mapper: UUIDMapper,
) -> None:
    if rows:
        validate_columns(SHEET_NAME, list(rows[0].keys()))

    # Phase 1: insert with NULL audit-actors.
    # The created_by/updated_by/suspended_by columns are all FK_REF to
    # platform_users itself. We can't resolve them yet because the
    # referenced rows haven't been inserted. Pass NULL; UPDATE in phase 2.
    for row in rows:
        insert_row = {}
        for spec in excel_columns_for_db_insert(SHEET_NAME):
            if spec.name == "id":
                continue
            if spec.fk_target == "platform_users":
                insert_row[spec.name] = None  # phase 2 will populate
            else:
                insert_row[spec.name] = row.get(spec.name)
        columns = list(insert_row.keys())
        placeholders = ", ".join(f":{c}" for c in columns)
        column_list = ", ".join(columns)
        sql = f"INSERT INTO {TABLE_NAME} ({column_list}) VALUES ({placeholders}) RETURNING id"
        result = await session.execute(text(sql), insert_row)
        db_id = result.scalar_one()
        mapper.register(SHEET_NAME, row["id"], db_id)
    await session.commit()

    # Phase 2: UPDATE to set the now-resolvable audit-actor IDs.
    for row in rows:
        db_id = mapper.lookup(SHEET_NAME, row["id"])
        created_by = mapper.lookup("platform_users", row["created_by_user_id"])
        updated_by = mapper.lookup("platform_users", row["updated_by_user_id"])
        suspended_by = mapper.lookup("platform_users", row["suspended_by_user_id"])
        await session.execute(
            text("""
                UPDATE platform_users
                SET created_by_user_id = :created_by,
                    updated_by_user_id = :updated_by,
                    suspended_by_user_id = :suspended_by
                WHERE id = :id
            """),
            {"id": db_id, "created_by": created_by, "updated_by": updated_by,
             "suspended_by": suspended_by},
        )
    await session.commit()
```

**`org_nodes`** (File 10b): Parents must be inserted before children. The Excel row order does NOT guarantee topological ordering. Two viable approaches:

- (i) **Multi-pass.** Loop: insert every row whose `_parent_key` is NULL (TENANT roots) or already-mapped. Repeat until all rows inserted or no progress (cycle = error). Simple, handles arbitrary tree shapes.
- (ii) **Pre-sort by depth.** BFS over the parent_id chain to assign each row a depth, sort ascending, insert. Slightly more code but single pass.

(i) is simpler and the seed data is small (49 rows) — overhead is irrelevant. Recommend (i):

```python
"""Loader for org_nodes with parent-before-child ordering."""
from typing import Any
from sqlalchemy.ext.asyncio import AsyncSession
from scripts.seed_dev_data.column_mappings import validate_columns
from scripts.seed_dev_data.loaders._base import insert_and_register
from scripts.seed_dev_data.uuid_mapper import UUIDMapper

SHEET_NAME = "org_nodes"
TABLE_NAME = "org_nodes"


async def load(
    session: AsyncSession,
    rows: list[dict[str, Any]],
    mapper: UUIDMapper,
) -> None:
    if rows:
        validate_columns(SHEET_NAME, list(rows[0].keys()))

    remaining = list(rows)
    while remaining:
        progress = False
        deferred = []
        for row in remaining:
            parent_excel_id = row.get("parent_id")
            if parent_excel_id is None:
                # Root node — insert immediately.
                await insert_and_register(session, SHEET_NAME, TABLE_NAME, row, mapper)
                progress = True
                continue
            # Check mapper without raising — defer if parent not yet mapped.
            if not mapper.is_mapped(SHEET_NAME, parent_excel_id):
                deferred.append(row)
                continue
            await insert_and_register(session, SHEET_NAME, TABLE_NAME, row, mapper)
            progress = True
        if not progress:
            unresolved_keys = [r.get("_key", r.get("id")) for r in deferred]
            raise RuntimeError(
                f"org_nodes: cycle or unresolvable parents: {unresolved_keys}"
            )
        remaining = deferred
    await session.commit()
```

**`user_role_assignments`**: standard loader (File 10). The dual-FK XOR pattern works automatically — the column_mappings declares both `platform_user_id` and `tenant_user_id` as FK_REF, and `mapper.lookup(...)` returns None for None inputs (each row has exactly one populated, the other arrives as None per the seed Excel + the widened NULL detection in excel_reader). DB-level CHECK enforces the XOR property; loader passes both verbatim.

**Other sheets** (`tenants`, `stores`, `tenant_users`, `roles`, `permissions`, `role_permissions`, `tenant_module_access`): standard loader as shown above. The mappings file already encodes their FK references; the base loader resolves and inserts.

### File 11: `scripts/seed_dev_data/truncate.py` — new

```python
"""Reverse-FK-order TRUNCATE for the --reset flag.

Reverse-FK-order matters: TRUNCATE without CASCADE fails on tables
referenced by other tables. Listing in reverse-FK order means each
TRUNCATE happens after its referencing tables are already empty.
audit_logs is not in the list (no DDL).
"""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


# Reverse-dependency order. tenant_module_access references tenants
# and platform_users; platform_users is referenced by everything;
# truncate the leaf tables first.
TABLES_REVERSE_FK_ORDER = [
    "tenant_module_access",
    "user_role_assignments",
    "role_permissions",
    "permissions",
    "roles",
    "tenant_users",
    "stores",
    "org_nodes",
    "tenants",
    "platform_users",
]


async def truncate_seed_tables(session: AsyncSession) -> None:
    """TRUNCATE each table in reverse-FK order. NO CASCADE."""
    for table in TABLES_REVERSE_FK_ORDER:
        await session.execute(text(f"TRUNCATE {table} RESTART IDENTITY"))
    await session.commit()
```

The "NO CASCADE" discipline mirrors Step 1.6 / 3.0's migration pattern. A TRUNCATE that needs CASCADE is a sign of either wrong ordering or wrong scope.

### File 12: `tests/integration/test_seed_loader.py` — new

Four integration tests + one unit test for the drift detector. The seed loader is utility code; proportional coverage.

```python
"""Integration tests for the dev seed loader.

Test pattern: each test runs the loader (or its components) against the
real test DB and asserts on observable database state.
"""
import os
from pathlib import Path

import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_seed_runs_clean_end_to_end(platform_session, monkeypatch):
    """Layer 1: loader runs without exceptions against an empty DB."""
    monkeypatch.setenv("ENVIRONMENT", "local")
    from scripts.seed_dev_data.runner import run_seed
    rc = await run_seed(reset=True)
    assert rc == 0


@pytest.mark.asyncio
async def test_seed_row_counts(platform_session):
    """Layer 2: per-table row counts match the Excel sheet counts."""
    expected_counts = {
        "platform_users": 3,
        "tenants": 7,
        "org_nodes": 49,
        "stores": 25,
        "tenant_users": 17,
        "roles": 15,
        "permissions": 32,
        "role_permissions": 164,
        "user_role_assignments": 22,
        "tenant_module_access": 27,
    }
    for table, expected in expected_counts.items():
        result = await platform_session.execute(text(f"SELECT count(*) FROM {table}"))
        actual = result.scalar_one()
        assert actual == expected, f"{table}: expected {expected}, got {actual}"


@pytest.mark.asyncio
async def test_seed_sentinel_rows(platform_session):
    """Layer 3: spot-check specific rows for value correctness.

    Each assertion verifies a known-tricky aspect of the data.
    """
    # Buc-ee's: ENTERPRISE tier, monthly_revenue_usd round-trips as Decimal-string-shaped value.
    result = await platform_session.execute(text("""
        SELECT name, tier, monthly_revenue_usd::text
        FROM tenants
        WHERE name = 'Buc-ee''s'
    """))
    row = result.one()
    assert row.name == "Buc-ee's"
    assert row.tier == "ENTERPRISE"
    assert row.monthly_revenue_usd == "48500.00"

    # GreenLeaf: SUSPENDED tenant — verifies status companion-field constraint.
    result = await platform_session.execute(text("""
        SELECT status, suspended_at IS NOT NULL AS has_suspended_at
        FROM tenants WHERE name = 'GreenLeaf Markets'
    """))
    row = result.one()
    assert row.status == "SUSPENDED"
    assert row.has_suspended_at is True

    # FreshMart: monthly_revenue_usd is NULL — verifies 'NULL'-string translation.
    result = await platform_session.execute(text("""
        SELECT monthly_revenue_usd FROM tenants WHERE name = 'FreshMart Co-op'
    """))
    assert result.scalar_one() is None

    # org_node child with multi-segment ltree path.
    result = await platform_session.execute(text("""
        SELECT path::text, node_type FROM org_nodes
        WHERE name = 'Deli'
        AND path::text LIKE 'buc_ees.bu_hq.tx.tx_101%'
    """))
    rows = result.all()
    assert len(rows) >= 1
    assert all(r.node_type == "DEPARTMENT" for r in rows)

    # user_role_assignments: PLATFORM-side row has platform_user_id populated,
    # tenant_user_id/tenant_id/org_node_id NULL. Verifies dual-FK XOR worked.
    result = await platform_session.execute(text("""
        SELECT count(*) FROM user_role_assignments
        WHERE platform_user_id IS NOT NULL
          AND tenant_user_id IS NULL
          AND tenant_id IS NULL
    """))
    platform_assigns = result.scalar_one()
    assert platform_assigns >= 3  # at least the 3 platform_users have role assignments

    # tenant_module_access for Buc-ee's: at least 5 modules per the screenshots.
    result = await platform_session.execute(text("""
        SELECT count(*) FROM tenant_module_access tma
        JOIN tenants t ON t.id = tma.tenant_id
        WHERE t.name = 'Buc-ee''s' AND tma.status = 'ENABLED'
    """))
    bucees_modules = result.scalar_one()
    assert bucees_modules >= 5


def test_seed_refuses_production(monkeypatch):
    """Safety: ENVIRONMENT=prod refuses to run, exits non-zero, no DB writes."""
    monkeypatch.setenv("ENVIRONMENT", "prod")
    # Force settings re-read since get_settings uses lru_cache.
    from admin_backend.config import get_settings
    get_settings.cache_clear()

    from scripts.seed_dev_data.__main__ import main
    rc = main()
    assert rc == 2
```

Test conftest setup: these tests assume the existing `platform_session` fixture. The `test_seed_refuses_production` test is synchronous (the guard runs before any async work) and doesn't need DB access.

### File 13: `tests/unit/test_seed_column_mappings.py` — new

Unit test for the drift detector — fast-running, no DB. Catches regressions where the Excel adds a column nobody added to `column_mappings.py`.

```python
"""Unit test for column-mapping drift detection.

Each test directly exercises validate_columns to confirm:
- Known columns pass without error.
- Unknown columns raise UnknownColumnError with a useful message.
- All loadable sheets in SHEET_MAPPINGS have at least one DB_COLUMN entry
  (catches accidentally-empty sheet specs).
"""
import pytest

from scripts.seed_dev_data.column_mappings import (
    SHEET_MAPPINGS, ColumnRole, UnknownColumnError, validate_columns,
)


def test_known_columns_pass():
    """A row whose columns match the mapping passes validation."""
    headers = [spec.name for spec in SHEET_MAPPINGS["tenants"]]
    validate_columns("tenants", headers)  # no exception


def test_unknown_column_raises():
    """An Excel column not in the mapping causes an explicit error."""
    headers = [spec.name for spec in SHEET_MAPPINGS["tenants"]] + ["unexpected_col"]
    with pytest.raises(UnknownColumnError) as exc:
        validate_columns("tenants", headers)
    assert "unexpected_col" in str(exc.value)
    assert "tenants" in str(exc.value)


def test_unknown_sheet_raises():
    """Asking about a sheet not in SHEET_MAPPINGS raises."""
    with pytest.raises(UnknownColumnError):
        validate_columns("not_a_real_sheet", ["any_col"])


def test_every_sheet_has_db_columns():
    """Every sheet must have at least one DB_COLUMN — catches typos that
    accidentally make every column a HELPER."""
    for sheet_name, mapping in SHEET_MAPPINGS.items():
        db_cols = [s for s in mapping if s.role is ColumnRole.DB_COLUMN]
        assert db_cols, f"sheet {sheet_name} has no DB_COLUMN entries"


def test_fk_refs_have_targets():
    """Every FK_REF must declare an fk_target. Validated at construction
    in ColumnSpec.__post_init__, but a regression test guards it."""
    for sheet_name, mapping in SHEET_MAPPINGS.items():
        for spec in mapping:
            if spec.role is ColumnRole.FK_REF:
                assert spec.fk_target is not None, (
                    f"{sheet_name}.{spec.name} is FK_REF but has no fk_target"
                )
```

Five small unit tests, all run in milliseconds, no DB needed. Catches the most common drift scenarios.

### File 14: `scripts/seed_dev_data/README.md` — new

```markdown
# Dev seed loader

Loads `data/ithina_dev_seed_data.xlsx` into the configured Postgres
database. For dev/local environments only — refuses to run with
`ENVIRONMENT=prod`.

## Usage

```bash
# Standard run (assumes DB exists and migrations applied):
uv run python -m scripts.seed_dev_data

# Re-seed (TRUNCATE then load):
uv run python -m scripts.seed_dev_data --reset

# Validate without writing:
uv run python -m scripts.seed_dev_data --dry-run

# Load specific sheets only:
uv run python -m scripts.seed_dev_data --sheets tenants,stores
```

## How it works

The Excel uses v4 UUIDs throughout. The loader honours D-21 (UUIDv7
invariant) by stripping IDs on insert, letting `DEFAULT uuidv7()` fire,
and capturing per-sheet `excel_id → db_id` mappings via the UUIDMapper.
Subsequent sheets resolve their FK references through the mapper.

Sheet load order matches FK dependency: platform_users → tenants →
org_nodes → stores → tenant_users → roles → permissions →
role_permissions → user_role_assignments → tenant_module_access.

`audit_logs` is skipped (no DDL — Step 6.2 territory). The Excel sheet
exists for reference only.

## Rollback

```bash
uv run python -m scripts.seed_dev_data --reset
# This TRUNCATEs all seed tables in reverse-FK order, then re-seeds.
# To roll back without re-seeding, comment out the seed phase in runner.py
# or just TRUNCATE manually.
```

## Special cases

- **platform_users self-reference:** Anjali's `created_by_user_id`
  references herself. The loader inserts the row first with NULL
  audit-actors, then UPDATEs them in a second pass (the column is
  nullable, so this is straightforward).

- **org_nodes parent-child ordering:** parents must be inserted before
  children. The loader sorts non-root rows by depth (BFS over the
  parent_id chain) before inserting.

- **tenant_users CHECK constraints:** ACTIVE users require auth0_sub
  and invitation_accepted_at populated. The seed honours this; if a
  test row added later doesn't, the DB rejects it loudly.

- **user_role_assignments dual-FK:** PLATFORM rows have
  platform_user_id populated; TENANT rows have tenant_user_id +
  tenant_id + org_node_id populated. CHECK constraint enforces XOR;
  loader passes values verbatim.
```

### File 15: `BUILD_PLAN.md` — modify

Step 3.5 status: TODO → DONE. Scope-in/acceptance rewrite to match what shipped (the original was sparse; new entry names the 11 loadable sheets, the UUIDv7 substitution mechanism, the `--reset`/`--dry-run`/`--sheets` flags, the production-refusal guard, and the four tests).

Add a note that this step is the prototype for Step 7.3.1 (post-v0 customer-data converter).

### File 16: `CLAUDE.md` — modify

- **Current state → Completed:** Step 3.5 bullet covering the script structure, the UUIDv7 substitution mechanism, the four tests, the production-refusal guard, the audit_logs skip.
- **Current state → Not yet completed:** advance "Step 4.x onward" appropriately if needed (Steps 3.4 GCP env still pending).
- **No new D-XX entries.** UUIDv7 substitution honours D-21; nothing new to capture.
- **No new FN-AB entries** unless something genuinely surfaces.

### File 17: `prompts/step-3_5-dev-seed-loader-2026-05-03.md` — new

This prompt file. Bundled per the per-step convention.

### File 18: `docs/architecture.md` — likely no-edit

This step adds tooling, not application surface. Architecture.md already describes the seed-vs-DDL distinction (Schema and storage section). If this step changes nothing about the architectural narrative, skip the file. Per the convention, don't hunt for an edit.

---

## Testing and regression discipline

### New tests added by this step

Four integration tests in `tests/integration/test_seed_loader.py` (File 12) plus five unit tests in `tests/unit/test_seed_column_mappings.py` (File 13). Each tests a distinct property:

**Integration tests (Layer 1-3 + Safety):**

1. **End-to-end run** (no exceptions): catches ~80% of bugs (mid-run INSERT failures, FK-substitution errors, type-cast errors).
2. **Per-table row count** matches Excel: catches silent row drops, duplications, partial inserts.
3. **Sentinel row spot checks**: catches `'NULL'` translation, money serialization, status companion fields, ltree paths, dual-FK substitution, multi-row aggregation.
4. **Production-refusal**: catastrophic-bug guard.

**Unit tests (drift detection):**

5-9. **Column-mapping integrity:** known columns pass, unknown columns raise, unknown sheets raise, every sheet has at least one DB_COLUMN, every FK_REF has an fk_target. Fast (no DB), guards the centralised mappings module.

### Tests deliberately not added

- "UUIDv7 fires correctly" — proven at Step 1.4; reasserting via the loader adds nothing.
- "RLS isolation works on seeded data" — proven at Step 3.0; loader doesn't change RLS.
- "FK constraints fire if loader points to non-existent ID" — DB-level, already enforced; covered implicitly by the run-clean test.
- "Schema validation against the API" — that's Step 3.3's responsibility, not the loader's.

The seed loader is utility code; over-testing it is its own anti-pattern.

### Regression risk surface introduced by this step

1. **The seed Excel must be at `data/ithina_dev_seed_data.xlsx` exactly.** If it's not present, the loader crashes at `read_workbook(path)`. Pre-flight item 3 catches this.

2. **The audit_logs sheet exists in the Excel but is skipped.** A WARNING is logged, not an error. Anyone running `--dry-run` sees it skipped. If audit_logs becomes loadable in a future step (Step 6.2), update `SKIPPED_SHEETS` in runner.py.

3. **`--reset` is destructive.** The script makes the destructive nature obvious in CLI output. The production-refusal guard prevents accidental prod nuking. Local/dev still has the destructive surface — unavoidable.

4. **org_nodes topological insertion.** If the Excel's parent_id chain has a cycle (it shouldn't), the BFS sorter loops forever. Add a max-iteration guard. Mirror the discipline used at Step 1.5's smoke test.

5. **AuthContext field set.** The loader's synthetic PLATFORM AuthContext must have all required fields. If `auth/context.py` requires fields beyond user_id/tenant_id/user_type, the loader's `_platform_auth()` fails at construction. Surface during pre-flight; mirror the conftest pattern from Step 3.3.

6. **`get_tenant_session` async iteration.** Each `async for session in get_tenant_session(...)` loop runs once. The loader's per-sheet pattern uses one session per sheet — verify that each iteration gives a fresh session and the previous one's transaction has committed.

7. **The Excel's `created_at` and `updated_at` timestamps are explicit values.** The loader passes them verbatim. The DB CHECK constraints (if any on these columns) will fire if a value is implausibly old or future-dated. Spot-check during dry-run.

8. **Per-sheet vs. per-row commits.** The loader commits at the end of each sheet (one transaction per sheet). If a single row fails mid-sheet, the rest of the sheet rolls back; preceding sheets are unaffected. This is the right granularity for dev seeding (load what you can, surface failures cleanly). For production-style ingestion later, per-row commits with explicit error capture would be better — but that's Step 7.3.1.

9. **mypy strict on the loader.** SQLAlchemy 2.x typing on raw `text(...)` queries needs explicit type hints. The skeletons in this prompt show the pattern (e.g., `total: int = result.scalar_one()`). Plan for ~30 minutes of mypy clean-up.

10. **The lookups table seed (Step 3.4.5's migration adds the 6 module_code rows) must be present before tenant_module_access loads.** Otherwise the API's modules JOIN returns NULL display_names. Step 3.4.5's migration is at HEAD per pre-flight; the loader runs after migrations, so the lookups rows exist. Verify in the test_seed_runs_clean test.

### Verification harness (run all five; all must be green)

```bash
# 1. Full pytest suite — new + regression
uv run pytest -v

# 2. mypy strict
uv run mypy --strict src/admin_backend scripts/seed_dev_data

# 3. Pre-flight checker
./scripts/check_setup.sh

# 4. Dry-run the loader against the Excel
uv run python -m scripts.seed_dev_data --dry-run

# 5. Real seed run + curl verification
uv run python -m scripts.seed_dev_data --reset
JWT=$(uv run python -c "from admin_backend.auth.testing import make_test_jwt; print(make_test_jwt(user_type='PLATFORM'))")
curl -s -H "Authorization: Bearer $JWT" http://localhost:8000/api/v1/tenants/stats | jq
curl -s -H "Authorization: Bearer $JWT" http://localhost:8000/api/v1/tenants?limit=10 | jq '.items | length'
curl -s -H "Authorization: Bearer $JWT" "http://localhost:8000/api/v1/tenants?search=Bucees" | jq '.items[0].modules | length'
```

Expected:
- pytest 110+ passes (101 prior + 4 new integration tests + 5 new unit tests)
- mypy clean
- check_setup 35/35
- Dry-run reports 11 sheets, ~370 rows, audit_logs skipped
- Real seed: stats returns `{total_tenants: 7, total_stores: 25}`; list returns 7 items; Buc-ee's has 5 modules.

If any leg is not green, **report rather than commit**.

---

## Scope out

- **`audit_logs` table and seed loading.** Step 6.2 territory — needs its own DDL first.
- **Customer-data converter** (the post-v0 tool that takes a partially-filled `Ithina_data_entry_template.xlsx` and ingests one tenant's worth of data). Step 7.3.1 — this step's loader is the prototype.
- **Per-row error reporting and recovery.** Dev seed is "load what you can, fail loudly." Step 7.3.1's customer-data tool gets richer error reporting.
- **Idempotency** beyond `--reset`. Re-running without `--reset` against a populated DB will fail on UNIQUE constraints. That's the right behaviour for a dev tool; producton tools want UPSERT, but that's not v0.
- **Lookup-table seeding.** lookups already has rows for `module_code` (from Step 3.4.5's migration). If new lookup categories are needed (industry codes, region codes, status codes for UI dropdowns), they go in their own migration, not the dev seed loader.
- **GCP / cloud environments.** Loader works against any Postgres the existing engine connects to. Cloud SQL is the same code path; no special handling.

---

## Stop and ask if

- The Excel file isn't at `data/ithina_dev_seed_data.xlsx`. Surface where it actually is and we'll standardise the path or copy it in.
- Pre-flight reading the Excel reveals an Excel column not declared in `column_mappings.py:SHEET_MAPPINGS`. The drift detector will fire at first run; surface the discrepancy and decide whether to add it to the mapping (real DB column missed) or rename it to `_*` prefix (helper column).
- Pre-flight reading the DDLs reveals a DB column NOT NULL that isn't in any Excel column for that sheet. The loader will fail at INSERT with a NOT NULL violation; better to surface during pre-flight.
- AuthContext requires fields the loader's synthetic context can't satisfy without minting a JWT. Surface; we'll either use `make_test_jwt` from auth/testing.py or `model_construct` to bypass validation.
- The `get_tenant_session` async-iteration pattern doesn't behave as the runner expects (e.g., the session doesn't auto-commit at end of `async for` block). Surface; we'll either restructure the loaders or use a different session pattern.
- A sheet's data violates a DDL CHECK that the loader can't avoid (e.g., a tenant_user with `status='ACTIVE'` but `auth0_sub IS NULL` slipped into the Excel). Surface; we'll either fix the seed data or relax the loader's behaviour to allow override.
- The org_nodes path values aren't in the format ltree expects (e.g., dots at the wrong place, or characters outside `[A-Za-z_0-9]`). Surface; we'll either fix the data or transform during load.
- The `'NULL'` string convention isn't honoured uniformly across the Excel (e.g., some cells use `'null'` lowercase or empty string). Surface concrete examples; we'll widen the translation logic.
- A sheet's load fails partway through with a confusing error (e.g., type cast failed but no row info). Add per-row diagnostics in the loader (which row was being processed, what the values were).
- The user_role_assignments dual-FK XOR property is violated by any seed row (both platform_user_id AND tenant_user_id populated, or both NULL). Surface; this would mean the Excel has a data error.
- `tenant_module_access` references a tenant that wasn't loaded (FK lookup fails in the mapper). Surface; this would be a data error — but the load order should prevent this.
- The four tests pass but a manual curl returns unexpected data (e.g., `num_users_active=0` for a tenant that should have users). Surface; the issue is more subtle than the test suite catches.

---

## Acceptance criteria

- 17-18 files created/modified (range slightly wider if architecture.md gets touched).
- Loader runs end-to-end via `uv run python -m scripts.seed_dev_data --reset` against a fresh dev DB.
- All 4 new integration tests + 5 new unit tests pass.
- All 101 existing tests still pass — no regressions.
- mypy strict clean across `src/admin_backend` AND `scripts/seed_dev_data`.
- `check_setup.sh` 35/35.
- Smoke test at 74 PASS — unchanged (loader doesn't change DB structure).
- Manual curl verification: `/api/v1/tenants/stats` → `{total_tenants: 7, total_stores: 25}`; `/api/v1/tenants?limit=10` returns 7 items; Buc-ee's row in detail has `num_stores ≥ 1`, `num_users_active ≥ 3`, `modules` length ≥ 5 (depends on what's in the Excel).
- Production-refusal guard verified by `ENVIRONMENT=prod uv run python -m scripts.seed_dev_data` returning exit code 2 with no DB writes.
- BUILD_PLAN.md Step 3.5 entry rewritten and flipped to DONE.
- README at `scripts/seed_dev_data/README.md` covers usage, the UUIDv7 substitution mechanism, and rollback.

---

## Report (BEFORE proposing commit)

Five bundles per the convention:

1. **Code/tests:** files created with line counts; the script structure tree (one loader per sheet); the production-refusal guard wired and verified; the four-test pass count; the manual curl outputs from the verification harness (stats response, list count, Buc-ee's modules).
2. **CLAUDE.md updates:** Step 3.5 Completed bullet; no new D-XX or FN-AB.
3. **BUILD_PLAN.md updates:** Step 3.5 status DONE; scope rewritten.
4. **architecture.md updates:** "no change" (likely outcome — loader is tooling).
5. **Prompt file:** `prompts/step-3_5-dev-seed-loader-2026-05-03.md` confirmed in commit set.

Plus: pytest count delta; mypy status; check_setup; smoke; manual curl results.

Wait for explicit authorisation before staging or committing.

---

## End of prompt
