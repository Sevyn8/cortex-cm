"""Seed runner: orchestrates per-sheet loaders in FK dependency order.

Skips ``audit_logs`` (no DDL — Step 6.2 territory). Loads the other
10 sheets.

Transaction boundaries: one transaction per sheet. Each loader
commits at end of its ``load()`` function. If sheet N fails mid-load,
sheets 1..N-1 are committed and persisted; sheet N rolls back; sheets
N+1..end never ran. The DB ends in a partial state. The user reruns
with ``--reset`` for a clean slate. This is intentional for dev
seeding — load what you can, fail loudly, surface the failing sheet
by name. Production-style ingestion (Step 7.3.1) gets richer error
handling.

Reference data (``lookups`` table) is NOT loaded by this script. The
``lookups`` rows for ``module_code`` are seeded by Step 3.4.5's
migration, so they exist post-migration. Future lookup categories
are seeded via their own migrations.
"""
from __future__ import annotations

import logging
from pathlib import Path
from uuid import UUID

from admin_backend.auth.context import AuthContext
from admin_backend.config import get_settings
from admin_backend.db.engine import create_engine, create_session_factory
from admin_backend.db.session import get_tenant_session

from scripts.seed_dev_data.excel_reader import read_workbook
from scripts.seed_dev_data.loaders import (
    org_nodes,
    permissions,
    platform_users,
    role_permissions,
    roles,
    stores,
    tenant_module_access,
    tenant_users,
    tenants,
    user_role_assignments,
)
from scripts.seed_dev_data.truncate import truncate_seed_tables
from scripts.seed_dev_data.uuid_mapper import UUIDMapper


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
SKIPPED_SHEETS: set[str] = {"audit_logs"}  # No DDL; Step 6.2 territory.

EXCEL_PATH = Path("data/ithina_dev_seed_data.xlsx")

logger = logging.getLogger(__name__)


# Sentinel synthetic user_id for the loader's PLATFORM session. Does
# NOT match any real platform_users row; AuthContext validators don't
# touch the DB so the sentinel passes Pydantic validation cleanly.
_SEED_USER_ID = UUID("00000000-0000-0000-0000-000000000001")


def _platform_auth() -> AuthContext:
    """Synthetic PLATFORM AuthContext for the loader's PLATFORM session.

    Mirrors the ``_VALID_AUTH_BASE`` shape used by the integration-test
    conftest (Step 3.2 onwards): synthetic but Pydantic-valid values for
    the JWT claims (sub, iss, aud, exp, email) plus a sentinel
    ``user_id``. AuthContext's validators check field shape only — they
    don't query the DB — so this passes validation without referencing
    any real ``platform_users`` row.
    """
    return AuthContext(
        sub="seed-loader",
        iss="https://stub-issuer.local/",
        aud="https://api.test/",
        exp=9999999999,
        user_id=_SEED_USER_ID,
        tenant_id=None,
        user_type="PLATFORM",
        email="seed-loader@ithina.local",
    )


async def run_seed(
    *,
    reset: bool = False,
    dry_run: bool = False,
    sheets: list[str] | None = None,
) -> int:
    """Run the seed. Returns 0 on success, non-zero on failure."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    workbook = read_workbook(EXCEL_PATH)
    selected_sheets = (
        sheets if sheets else [name for name, _ in SHEETS_IN_ORDER]
    )

    if dry_run:
        logger.info("DRY RUN: reading and validating, no DB writes.")
        for name, _loader in SHEETS_IN_ORDER:
            if name not in selected_sheets:
                continue
            sheet_data = workbook.get(name, [])
            logger.info("  %s: %d data rows", name, len(sheet_data))
        for skipped in SKIPPED_SHEETS:
            logger.info("  %s: SKIPPED (no DDL)", skipped)
        return 0

    settings = get_settings()
    engine = create_engine(settings)
    try:
        session_factory = create_session_factory(engine)
        auth = _platform_auth()
        mapper = UUIDMapper()

        if reset:
            logger.info(
                "Reset requested: TRUNCATE-ing seed tables."
            )
            async for session in get_tenant_session(
                auth, session_factory
            ):
                await truncate_seed_tables(session)

        for name, loader in SHEETS_IN_ORDER:
            if name not in selected_sheets:
                logger.info(
                    "Skipping %s (not in --sheets selection).", name
                )
                continue
            if name in SKIPPED_SHEETS:
                logger.warning("Skipping %s (no DDL yet).", name)
                continue
            sheet_data = workbook.get(name, [])
            if not sheet_data:
                logger.warning(
                    "Sheet %s is empty; nothing to load.", name
                )
                continue
            logger.info(
                "Loading %s (%d rows)...", name, len(sheet_data)
            )
            async for session in get_tenant_session(
                auth, session_factory
            ):
                await loader(session, sheet_data, mapper)
            logger.info("  ok %s loaded.", name)
    finally:
        await engine.dispose()

    logger.info("Seed complete.")
    return 0
